"""Transactional administration of candidate Collection rebuilds."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from commercevision_contracts import (
    CollectionRebuildRequestV1,
    CollectionRebuildResponseV1,
    CollectionRebuildValidationV1,
)
from commercevision_contracts.events import (
    COLLECTION_REBUILD_COMPLETED_V1,
    COLLECTION_REBUILD_REQUESTED_V1,
    CollectionRebuildCommand,
    CollectionRebuildCompletedPayload,
    CollectionRebuildRequestedPayload,
)
from commercevision_domain import (
    CollectionRebuildState,
    CollectionState,
    ConcurrencyError,
    InvalidTransitionError,
    NotFoundError,
    collection_instance_name,
    new_uuid7,
)
from commercevision_domain.messaging import EventEnvelope, OutboxEvent
from commercevision_domain.workflow.errors import IdempotencyConflictError
from sqlalchemy import func, literal_column, select, update
from sqlalchemy.orm import Session, sessionmaker

from .collection_rebuild_replay import REBUILD_REPLAY_EVENT_TYPES
from .indexing_models import (
    CollectionRebuildModel,
    CollectionRebuildPlacementModel,
    CollectionRebuildProgressModel,
    CollectionRegistryModel,
    EmbeddingRecordModel,
    RetrievalPolicyPointerModel,
)
from .models import OutboxEventModel
from .repositories import IdempotencyRepository, OutboxRepository

_IDEMPOTENCY_LIFETIME = timedelta(days=30)
_RETRIEVAL_POLICY_VERSION = "retrieval-policy-v1"


class MySqlCollectionRebuildControl:
    """Own request, validation scheduling, activation, and status projection."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        retirement_delay: timedelta,
    ) -> None:
        if retirement_delay <= timedelta(0):
            raise ValueError("collection retirement delay must be positive")
        self._session_factory = session_factory
        self._retirement_delay = retirement_delay

    def request(
        self,
        *,
        workspace_id: str,
        idempotency_key: str,
        trace_id: str,
        request: CollectionRebuildRequestV1,
    ) -> CollectionRebuildResponseV1:
        request_hash = hashlib.sha256(
            json.dumps(
                request.model_dump(mode="json", exclude={"collection_spec"}),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        scope = f"collection-rebuild-request:{workspace_id}"
        key_hash = IdempotencyRepository.hash_key(idempotency_key)
        with self._session_factory.begin() as session:
            now = self._database_now(session)
            idempotency = IdempotencyRepository(session)
            claim = idempotency.claim(
                scope=scope,
                key_hash=key_hash,
                request_hash=request_hash,
                expires_at=now + _IDEMPOTENCY_LIFETIME,
            )
            if claim.request_hash != request_hash:
                raise IdempotencyConflictError(
                    "idempotency key was already used with a different rebuild request"
                )
            if claim.status == "COMPLETED":
                return self._project(session, workspace_id, claim.resource_id)

            pointer = session.scalar(
                select(RetrievalPolicyPointerModel)
                .where(RetrievalPolicyPointerModel.vector_kind == request.vector_kind.value)
                .with_for_update()
            )
            if pointer is None:
                raise InvalidTransitionError("no active retrieval policy pointer exists")
            source = session.scalar(
                select(CollectionRegistryModel)
                .where(CollectionRegistryModel.id == pointer.collection_id)
                .with_for_update()
            )
            if source is None:
                raise InvalidTransitionError("retrieval policy points to a missing collection")
            if (
                pointer.version != request.expected_policy_pointer_version
                or source.version != request.expected_active_collection_version
            ):
                raise ConcurrencyError("active collection or retrieval policy version changed")
            if (
                source.state != CollectionState.ACTIVE.value
                or not source.is_read_enabled
                or not source.is_write_enabled
            ):
                raise InvalidTransitionError("retrieval policy collection is not active")
            self._assert_compatible_upgrade(source, request)
            active_rebuild = session.scalar(
                select(CollectionRebuildModel.id).where(
                    CollectionRebuildModel.vector_kind == request.vector_kind.value,
                    CollectionRebuildModel.state.not_in(
                        {
                            CollectionRebuildState.FAILED.value,
                            CollectionRebuildState.RETIRED.value,
                        }
                    ),
                )
            )
            if active_rebuild is not None:
                raise InvalidTransitionError("another rebuild for this vector kind is still active")

            rebuild_id = new_uuid7()
            operation_id = rebuild_id
            generation = (
                int(
                    session.scalar(
                        select(
                            func.coalesce(func.max(CollectionRegistryModel.instance_generation), 0)
                        )
                        .where(CollectionRegistryModel.vector_kind == request.vector_kind.value)
                        .with_for_update()
                    )
                    or 0
                )
                + 1
            )
            candidate_id = new_uuid7()
            spec = request.collection_spec
            candidate = CollectionRegistryModel(
                id=candidate_id,
                logical_key=spec.logical_key,
                spec_hash=spec.spec_hash,
                physical_name=collection_instance_name(spec, rebuild_id=rebuild_id),
                model_family=spec.model_family,
                model_id=request.model_id,
                pinned_revision=spec.pinned_revision,
                dimension=spec.dimension,
                vector_kind=spec.vector_kind.value,
                schema_version=spec.schema_version,
                index_spec_version=spec.index_spec_version,
                dynamic_fields_enabled=False,
                instance_generation=generation,
                rebuild_id=rebuild_id,
                state=CollectionState.PLANNED.value,
                is_read_enabled=False,
                is_write_enabled=False,
                validation_summary_json={},
                version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(candidate)
            session.flush()
            rebuild = CollectionRebuildModel(
                id=rebuild_id,
                workspace_id=workspace_id,
                operation_id=operation_id,
                source_collection_id=source.id,
                candidate_collection_id=candidate_id,
                vector_kind=request.vector_kind.value,
                state=CollectionRebuildState.REQUESTED.value,
                generation=1,
                source_collection_version=source.version,
                policy_pointer_version=pointer.version,
                snapshot_watermark=now,
                backfill_cursor=None,
                replay_watermark=None,
                replay_cursor_occurred_at=None,
                replay_cursor_event_id=None,
                rights_cursor=None,
                processed_count=0,
                validation_summary_json={},
                validation_watermark=None,
                failure_code=None,
                retire_after=None,
                version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(rebuild)
            self._append_progress(session, rebuild, "REBUILD_REQUESTED", now)
            self._enqueue_command(
                session, rebuild, CollectionRebuildCommand.CONTINUE, trace_id, now
            )
            session.flush()
            response = self._project_model(session, rebuild)
            idempotency.complete(
                scope=scope,
                key_hash=key_hash,
                request_hash=request_hash,
                resource_type="collection_rebuild",
                resource_id=rebuild_id,
                response_data=response.model_dump(mode="json"),
            )
            return response

    def get(self, *, workspace_id: str, rebuild_id: str) -> CollectionRebuildResponseV1:
        with self._session_factory() as session:
            return self._project(session, workspace_id, rebuild_id)

    def request_validation(
        self,
        *,
        workspace_id: str,
        rebuild_id: str,
        expected_version: int,
        trace_id: str,
    ) -> CollectionRebuildResponseV1:
        with self._session_factory.begin() as session:
            rebuild = self._lock(session, workspace_id, rebuild_id)
            if rebuild.version != expected_version:
                raise ConcurrencyError("collection rebuild version changed")
            if rebuild.state not in {
                CollectionRebuildState.AWAITING_VALIDATION.value,
                CollectionRebuildState.VALIDATING.value,
            }:
                raise InvalidTransitionError("collection rebuild is not awaiting validation")
            now = self._database_now(session)
            self._enqueue_command(
                session, rebuild, CollectionRebuildCommand.VALIDATE, trace_id, now
            )
            return self._project_model(session, rebuild)

    def activate(
        self,
        *,
        workspace_id: str,
        rebuild_id: str,
        expected_version: int,
        trace_id: str,
    ) -> CollectionRebuildResponseV1:
        with self._session_factory.begin() as session:
            rebuild = self._lock(session, workspace_id, rebuild_id)
            if rebuild.version != expected_version:
                raise ConcurrencyError("collection rebuild version changed")
            if rebuild.state != CollectionRebuildState.READY.value:
                raise InvalidTransitionError("only a validated rebuild can be activated")
            validation = CollectionRebuildValidationV1.model_validate(
                rebuild.validation_summary_json
            )
            if not validation.accepted or rebuild.validation_watermark is None:
                raise InvalidTransitionError("collection rebuild validation was not accepted")
            pointer = session.scalar(
                select(RetrievalPolicyPointerModel)
                .where(RetrievalPolicyPointerModel.vector_kind == rebuild.vector_kind)
                .with_for_update()
            )
            source = session.scalar(
                select(CollectionRegistryModel)
                .where(CollectionRegistryModel.id == rebuild.source_collection_id)
                .with_for_update()
            )
            candidate = session.scalar(
                select(CollectionRegistryModel)
                .where(CollectionRegistryModel.id == rebuild.candidate_collection_id)
                .with_for_update()
            )
            if pointer is None or source is None or candidate is None:
                raise InvalidTransitionError("rebuild routing facts are incomplete")
            if (
                pointer.collection_id != source.id
                or pointer.version != rebuild.policy_pointer_version
                or source.version != rebuild.source_collection_version
                or source.state != CollectionState.ACTIVE.value
                or candidate.state != CollectionState.READY.value
            ):
                raise ConcurrencyError("active collection changed after rebuild validation")
            late_event = session.scalar(
                select(OutboxEventModel.id)
                .where(
                    OutboxEventModel.workspace_id == workspace_id,
                    # DATETIME(6) alone cannot order transactions that share a
                    # microsecond. Replaying the boundary is idempotent and safer
                    # than missing an authorization-changing fact.
                    OutboxEventModel.occurred_at >= rebuild.validation_watermark,
                    OutboxEventModel.event_type.in_(REBUILD_REPLAY_EVENT_TYPES),
                )
                .limit(1)
            )
            if late_event is not None:
                now = self._database_now(session)
                rebuild.state = CollectionRebuildState.REPLAYING.value
                rebuild.replay_watermark = now
                rebuild.version += 1
                rebuild.updated_at = now
                self._append_progress(session, rebuild, "LATE_FACTS_REPLAY_REQUIRED", now)
                self._enqueue_command(
                    session, rebuild, CollectionRebuildCommand.CONTINUE, trace_id, now
                )
                return self._project_model(session, rebuild)

            now = self._database_now(session)
            rebuild.state = CollectionRebuildState.ACTIVATING.value
            source.state = CollectionState.RETIRING.value
            source.is_write_enabled = False
            source.version += 1
            source.updated_at = now
            candidate.state = CollectionState.ACTIVE.value
            candidate.is_read_enabled = True
            candidate.is_write_enabled = True
            candidate.version += 1
            candidate.updated_at = now
            pointer.collection_id = candidate.id
            pointer.retrieval_policy_version = _RETRIEVAL_POLICY_VERSION
            pointer.version += 1
            pointer.updated_at = now
            session.execute(
                update(EmbeddingRecordModel)
                .where(
                    EmbeddingRecordModel.collection_id == source.id,
                    EmbeddingRecordModel.id.in_(
                        select(CollectionRebuildPlacementModel.embedding_record_id).where(
                            CollectionRebuildPlacementModel.rebuild_id == rebuild.id
                        )
                    ),
                )
                .values(
                    collection_id=candidate.id,
                    version=EmbeddingRecordModel.version + 1,
                    updated_at=now,
                )
            )
            rebuild.state = CollectionRebuildState.RETIRING.value
            rebuild.source_collection_version = source.version
            rebuild.policy_pointer_version = pointer.version
            rebuild.retire_after = now + self._retirement_delay
            rebuild.version += 1
            rebuild.updated_at = now
            self._append_progress(session, rebuild, "COLLECTION_ACTIVATED", now)
            self._enqueue_command(
                session,
                rebuild,
                CollectionRebuildCommand.RETIRE,
                trace_id,
                rebuild.retire_after,
            )
            self._enqueue_completed(session, rebuild, candidate.id, source.id, trace_id, now)
            return self._project_model(session, rebuild)

    @staticmethod
    def _assert_compatible_upgrade(
        source: CollectionRegistryModel, request: CollectionRebuildRequestV1
    ) -> None:
        spec = request.collection_spec
        if (
            source.vector_kind != spec.vector_kind.value
            or source.model_family != spec.model_family
            or source.model_id != request.model_id
            or source.pinned_revision != spec.pinned_revision
            or source.dimension != spec.dimension
        ):
            raise InvalidTransitionError(
                "rebuild cannot change embedding identity; re-index through a new embedding spec"
            )

    @staticmethod
    def _database_now(session: Session) -> datetime:
        now = session.scalar(select(literal_column("UTC_TIMESTAMP(6)")))
        if now is None:
            raise RuntimeError("database time is unavailable")
        return now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)

    @staticmethod
    def _lock(session: Session, workspace_id: str, rebuild_id: str) -> CollectionRebuildModel:
        rebuild = session.scalar(
            select(CollectionRebuildModel)
            .where(
                CollectionRebuildModel.workspace_id == workspace_id,
                CollectionRebuildModel.id == rebuild_id,
            )
            .with_for_update()
        )
        if rebuild is None:
            raise NotFoundError("collection rebuild was not found")
        return rebuild

    @classmethod
    def _project(
        cls, session: Session, workspace_id: str, rebuild_id: str
    ) -> CollectionRebuildResponseV1:
        rebuild = session.scalar(
            select(CollectionRebuildModel).where(
                CollectionRebuildModel.workspace_id == workspace_id,
                CollectionRebuildModel.id == rebuild_id,
            )
        )
        if rebuild is None:
            raise NotFoundError("collection rebuild was not found")
        return cls._project_model(session, rebuild)

    @staticmethod
    def _project_model(
        session: Session, rebuild: CollectionRebuildModel
    ) -> CollectionRebuildResponseV1:
        progress = tuple(
            session.scalars(
                select(CollectionRebuildProgressModel)
                .where(CollectionRebuildProgressModel.rebuild_id == rebuild.id)
                .order_by(CollectionRebuildProgressModel.sequence)
                .limit(200)
            )
        )
        replay_cursor = (
            rebuild.replay_cursor_event_id
            if rebuild.replay_cursor_occurred_at is not None
            else None
        )
        return CollectionRebuildResponseV1.model_validate(
            {
                "id": rebuild.id,
                "operation_id": rebuild.operation_id,
                "vector_kind": rebuild.vector_kind,
                "state": CollectionRebuildState(rebuild.state),
                "version": rebuild.version,
                "snapshot_watermark": rebuild.snapshot_watermark,
                "replay_watermark": rebuild.replay_watermark,
                "backfill_cursor": rebuild.backfill_cursor,
                "replay_cursor": replay_cursor,
                "processed_count": rebuild.processed_count,
                "validation": rebuild.validation_summary_json or None,
                "failure_code": rebuild.failure_code,
                "retire_after": rebuild.retire_after,
                "created_at": rebuild.created_at,
                "updated_at": rebuild.updated_at,
                "progress": [
                    {
                        "sequence": item.sequence,
                        "state": CollectionRebuildState(item.state),
                        "processed_count": item.processed_count,
                        "message_code": item.message_code,
                        "observed_at": item.observed_at,
                    }
                    for item in progress
                ],
            }
        )

    @staticmethod
    def _append_progress(
        session: Session,
        rebuild: CollectionRebuildModel,
        message_code: str,
        now: datetime,
    ) -> None:
        sequence = (
            int(
                session.scalar(
                    select(
                        func.coalesce(func.max(CollectionRebuildProgressModel.sequence), 0)
                    ).where(CollectionRebuildProgressModel.rebuild_id == rebuild.id)
                )
                or 0
            )
            + 1
        )
        session.add(
            CollectionRebuildProgressModel(
                id=new_uuid7(),
                rebuild_id=rebuild.id,
                sequence=sequence,
                state=rebuild.state,
                processed_count=rebuild.processed_count,
                message_code=message_code,
                observed_at=now,
            )
        )

    @staticmethod
    def _enqueue_command(
        session: Session,
        rebuild: CollectionRebuildModel,
        command: CollectionRebuildCommand,
        trace_id: str,
        available_at: datetime,
    ) -> None:
        payload = CollectionRebuildRequestedPayload(
            workspace_id=rebuild.workspace_id,
            rebuild_id=rebuild.id,
            operation_id=rebuild.operation_id,
            generation=rebuild.generation,
            command=command,
        )
        envelope = EventEnvelope.create(
            event_type=COLLECTION_REBUILD_REQUESTED_V1.event_type.value,
            schema_version=COLLECTION_REBUILD_REQUESTED_V1.schema_version,
            aggregate_type="collection_rebuild",
            aggregate_id=rebuild.id,
            aggregate_version=rebuild.version,
            trace_id=trace_id,
            payload=payload.model_dump(mode="json"),
            now=available_at,
        )
        OutboxRepository(session).add(
            OutboxEvent(
                envelope=envelope,
                available_at=available_at,
                workspace_id=rebuild.workspace_id,
            )
        )

    @staticmethod
    def _enqueue_completed(
        session: Session,
        rebuild: CollectionRebuildModel,
        candidate_id: str,
        retired_id: str,
        trace_id: str,
        now: datetime,
    ) -> None:
        payload = CollectionRebuildCompletedPayload(
            workspace_id=rebuild.workspace_id,
            rebuild_id=rebuild.id,
            operation_id=rebuild.operation_id,
            candidate_collection_id=candidate_id,
            retired_collection_id=retired_id,
            vector_kind=rebuild.vector_kind,
            activated_at=now,
        )
        OutboxRepository(session).add(
            OutboxEvent(
                envelope=EventEnvelope.create(
                    event_type=COLLECTION_REBUILD_COMPLETED_V1.event_type.value,
                    schema_version=COLLECTION_REBUILD_COMPLETED_V1.schema_version,
                    aggregate_type="collection_rebuild",
                    aggregate_id=rebuild.id,
                    aggregate_version=rebuild.version,
                    trace_id=trace_id,
                    payload=payload.model_dump(mode="json"),
                    now=now,
                ),
                available_at=now,
                workspace_id=rebuild.workspace_id,
            )
        )
