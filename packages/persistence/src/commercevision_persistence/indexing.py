"""MySQL authority adapter for IMAGE indexing."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Literal

from commercevision_application import (
    ImageIndexingTarget,
    IndexCommitDecision,
    OperationExecutionRequest,
)
from commercevision_application.asset_ports import CurrentUsabilitySnapshot
from commercevision_contracts import (
    EmbeddingImageInputV1,
    EmbeddingProviderResultV1,
    MilvusVectorIdentityV1,
)
from commercevision_contracts.events import (
    AssetIndexCompletedPayload,
    AssetIndexDeleteRequestedPayload,
    AssetIndexRequestedPayload,
    EventType,
)
from commercevision_contracts.object_storage import (
    ObjectReference,
    ObjectStorage,
    TemporaryReadRequest,
)
from commercevision_domain import (
    CollectionSpec,
    CollectionState,
    EmbeddingState,
    OperationState,
    StorageLocationClass,
    VectorKind,
    compute_embedding_input_hash,
    evaluate_current_usability,
    generation_milvus_primary_key,
)
from commercevision_domain.messaging import EventEnvelope, OutboxEvent
from pydantic import SecretStr
from sqlalchemy import literal_column, select
from sqlalchemy.orm import Session, sessionmaker

from .assets import AssetRepository
from .indexing_identity import compute_image_index_operation_hash
from .indexing_models import CollectionRegistryModel, EmbeddingRecordModel
from .models import (
    AssetObjectModel,
    AssetVersionModel,
    DurableOperationModel,
    OutboxEventModel,
    ProductModel,
)
from .repositories import OutboxRepository

INDEX_PURPOSE = "RETRIEVAL"


class MySqlIndexingAuthority:
    """Short transactions around both eligibility checks and index facts."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def load_for_provisioning(
        self,
        request: OperationExecutionRequest,
    ) -> ImageIndexingTarget:
        with self._session_factory() as session:
            target, embedding = self._load(session, request)
            self._assert_eligible(session, target, embedding)
            return target

    def activate_collection(self, target: ImageIndexingTarget) -> None:
        with self._session_factory.begin() as session:
            collection = session.scalar(
                select(CollectionRegistryModel)
                .where(CollectionRegistryModel.id == target.collection_id)
                .with_for_update()
            )
            if collection is None:
                raise ValueError("embedding collection registry disappeared")
            spec = target.collection_spec
            if (
                collection.logical_key != spec.logical_key
                or collection.spec_hash != spec.spec_hash
                or collection.physical_name != spec.physical_name
                or collection.model_family != spec.model_family
                or collection.model_id != target.model_id
                or collection.pinned_revision != spec.pinned_revision
                or collection.dimension != spec.dimension
                or collection.vector_kind != spec.vector_kind.value
                or collection.schema_version != spec.schema_version
                or collection.index_spec_version != spec.index_spec_version
                or collection.dynamic_fields_enabled
            ):
                raise ValueError("collection registry identity conflicts with verified Milvus spec")
            if collection.state == CollectionState.ACTIVE.value:
                if not collection.is_read_enabled or not collection.is_write_enabled:
                    raise ValueError("ACTIVE collection registry routing is disabled")
                return
            if collection.state != CollectionState.PLANNED.value:
                raise ValueError("collection registry cannot be activated from its current state")
            database_now = session.scalar(select(literal_column("UTC_TIMESTAMP(6)")))
            if database_now is None:
                raise RuntimeError("database time is unavailable")
            if database_now.tzinfo is None:
                database_now = database_now.replace(tzinfo=UTC)
            collection.state = CollectionState.ACTIVE.value
            collection.is_read_enabled = True
            collection.is_write_enabled = True
            collection.validation_summary_json = {
                "verified": True,
                "verifier": "milvus-adapter-v1",
            }
            collection.version += 1
            collection.updated_at = database_now
            session.flush()

    def claim_for_submission(
        self,
        request: OperationExecutionRequest,
    ) -> ImageIndexingTarget:
        with self._session_factory.begin() as session:
            target, embedding = self._load_locked(session, request)
            if (
                embedding.state == EmbeddingState.PROCESSING.value
                and embedding.provider_request_id is None
                and embedding.actual_model is None
            ):
                return target
            replaying_permanent_failure = (
                embedding.state == EmbeddingState.PERMANENT_FAILED.value
                and self._is_active_audited_replay(session, request)
            )
            if not replaying_permanent_failure and embedding.state not in {
                EmbeddingState.PENDING.value,
                EmbeddingState.RETRYABLE_FAILED.value,
                EmbeddingState.STALE.value,
                EmbeddingState.DELETE_PENDING.value,
            }:
                raise ValueError("embedding record state cannot be claimed for provider submission")
            embedding.write_generation += 1
            embedding.state = EmbeddingState.PROCESSING.value
            embedding.provider_request_id = None
            embedding.actual_model = None
            embedding.version += 1
            embedding.updated_at = target.indexed_at
            session.flush()
            return self._target_from_models(
                target=target,
                embedding=embedding,
            )

    @staticmethod
    def _is_active_audited_replay(
        session: Session,
        request: OperationExecutionRequest,
    ) -> bool:
        if request.replay_source_dead_letter_id is None or request.replay_attempt < 1:
            return False
        operation = session.scalar(
            select(DurableOperationModel).where(
                DurableOperationModel.workspace_id == request.workspace_id,
                DurableOperationModel.id == request.operation_id,
            )
        )
        return bool(
            operation is not None
            and operation.state == OperationState.RUNNING.value
            and operation.replay_source_dead_letter_id == request.replay_source_dead_letter_id
            and operation.replay_attempt == request.replay_attempt
        )

    def load_for_reconciliation(
        self,
        request: OperationExecutionRequest,
    ) -> ImageIndexingTarget:
        with self._session_factory() as session:
            target, embedding = self._load(session, request)
            return self._target_from_models(target=target, embedding=embedding)

    def load_committed_outcome(
        self,
        request: OperationExecutionRequest,
    ) -> IndexCommitDecision | None:
        with self._session_factory() as session:
            operation = session.scalar(
                select(DurableOperationModel).where(
                    DurableOperationModel.workspace_id == request.workspace_id,
                    DurableOperationModel.id == request.operation_id,
                )
            )
            if (
                operation is None
                or operation.kind != request.kind.value
                or operation.target_type != "embedding_record"
                or operation.target_id != request.target_id
                or operation.target_version != request.target_version
                or operation.input_hash != request.input_hash
                or operation.input_ref != f"mysql://embedding-records/{request.target_id}"
            ):
                raise ValueError("embedding operation identity is stale")
            completed = session.scalar(
                select(OutboxEventModel)
                .where(
                    OutboxEventModel.workspace_id == request.workspace_id,
                    OutboxEventModel.aggregate_type == "embedding_record",
                    OutboxEventModel.aggregate_id == request.target_id,
                    OutboxEventModel.event_type == EventType.ASSET_INDEX_COMPLETED.value,
                    OutboxEventModel.trace_id == request.operation_id,
                )
                .order_by(OutboxEventModel.occurred_at.desc(), OutboxEventModel.id.desc())
                .limit(1)
            )
            if completed is None:
                return None
            payload: AssetIndexCompletedPayload | None = None
            invalid_payload = False
            try:
                payload = AssetIndexCompletedPayload.model_validate(completed.payload_json)
            except ValueError:
                invalid_payload = True
            if invalid_payload or payload is None:
                raise ValueError("committed IMAGE index outcome is invalid")
            if (
                payload.operation_id != request.operation_id
                or payload.embedding_record_id != request.target_id
                or payload.workspace_id != request.workspace_id
            ):
                raise ValueError("committed IMAGE index outcome identity is invalid")
            return IndexCommitDecision(
                indexed=payload.outcome == "INDEXED",
                stale_reason=None if payload.outcome == "INDEXED" else "COMMITTED_STALE",
            )

    def record_provider_result(
        self,
        target: ImageIndexingTarget,
        result: EmbeddingProviderResultV1,
    ) -> ImageIndexingTarget:
        with self._session_factory.begin() as session:
            embedding = self._lock_exact_embedding(session, target)
            if result.provider != embedding.provider:
                raise ValueError("embedding provider result does not match configured provider")
            if embedding.state != EmbeddingState.PROCESSING.value:
                raise ValueError("provider facts require a PROCESSING embedding")
            if (
                embedding.provider_request_id is not None
                and embedding.provider_request_id != result.provider_request_id
            ):
                raise ValueError("provider request identity changed for the same generation")
            if embedding.actual_model is not None and embedding.actual_model != result.actual_model:
                raise ValueError("actual model changed for the same generation")
            embedding.provider_request_id = result.provider_request_id
            embedding.actual_model = result.actual_model
            embedding.version += 1
            embedding.updated_at = target.indexed_at
            session.flush()
            return self._target_from_models(target=target, embedding=embedding)

    def record_failure(
        self,
        target: ImageIndexingTarget,
        *,
        retryable: bool,
    ) -> None:
        with self._session_factory.begin() as session:
            embedding = self._lock_exact_embedding(session, target)
            if embedding.state != EmbeddingState.PROCESSING.value:
                raise ValueError("only the current PROCESSING generation can fail")
            database_now = session.scalar(select(literal_column("UTC_TIMESTAMP(6)")))
            if database_now is None:
                raise RuntimeError("database time is unavailable")
            if database_now.tzinfo is None:
                database_now = database_now.replace(tzinfo=UTC)
            embedding.state = (
                EmbeddingState.RETRYABLE_FAILED.value
                if retryable
                else EmbeddingState.PERMANENT_FAILED.value
            )
            embedding.version += 1
            embedding.updated_at = database_now
            session.flush()

    def mark_terminal_failure(self, request: OperationExecutionRequest) -> bool:
        with self._session_factory.begin() as session:
            embedding = session.scalar(
                select(EmbeddingRecordModel)
                .where(
                    EmbeddingRecordModel.workspace_id == request.workspace_id,
                    EmbeddingRecordModel.id == request.target_id,
                    EmbeddingRecordModel.operation_id == request.operation_id,
                )
                .with_for_update()
            )
            if embedding is None:
                return False
            if not self._operation_identity_matches(session, request, embedding):
                return False
            if embedding.state == EmbeddingState.PERMANENT_FAILED.value:
                return True
            if embedding.state not in {
                EmbeddingState.PENDING.value,
                EmbeddingState.PROCESSING.value,
                EmbeddingState.RETRYABLE_FAILED.value,
            }:
                return False
            database_now = session.scalar(select(literal_column("UTC_TIMESTAMP(6)")))
            if database_now is None:
                raise RuntimeError("database time is unavailable")
            if database_now.tzinfo is None:
                database_now = database_now.replace(tzinfo=UTC)
            embedding.state = EmbeddingState.PERMANENT_FAILED.value
            embedding.version += 1
            embedding.updated_at = database_now
            session.flush()
            return True

    def validate_request_event(self, payload: AssetIndexRequestedPayload) -> bool:
        """Validate embedding and AssetVersion identities independently of the operation."""
        with self._session_factory() as session:
            embedding = session.scalar(
                select(EmbeddingRecordModel).where(
                    EmbeddingRecordModel.workspace_id == payload.workspace_id,
                    EmbeddingRecordModel.id == payload.embedding_record_id,
                    EmbeddingRecordModel.operation_id == payload.operation_id,
                    EmbeddingRecordModel.asset_id == payload.asset_id,
                    EmbeddingRecordModel.asset_version_id == payload.asset_version_id,
                    EmbeddingRecordModel.asset_version_number == payload.asset_version_number,
                    EmbeddingRecordModel.rights_record_id == payload.rights_record_id,
                    EmbeddingRecordModel.rights_record_version == payload.rights_record_version,
                    EmbeddingRecordModel.collection_id == payload.collection_id,
                    EmbeddingRecordModel.vector_kind == payload.vector_kind,
                    EmbeddingRecordModel.provider == payload.provider,
                    EmbeddingRecordModel.input_hash == payload.embedding_input_hash,
                    EmbeddingRecordModel.embedding_spec_hash == payload.embedding_spec_sha256,
                )
            )
            if embedding is None:
                embedding = session.scalar(
                    select(EmbeddingRecordModel).where(
                        EmbeddingRecordModel.workspace_id == payload.workspace_id,
                        EmbeddingRecordModel.id == payload.embedding_record_id,
                        EmbeddingRecordModel.asset_id == payload.asset_id,
                        EmbeddingRecordModel.asset_version_id == payload.asset_version_id,
                        EmbeddingRecordModel.asset_version_number == payload.asset_version_number,
                        EmbeddingRecordModel.collection_id == payload.collection_id,
                        EmbeddingRecordModel.vector_kind == payload.vector_kind,
                        EmbeddingRecordModel.provider == payload.provider,
                        EmbeddingRecordModel.input_hash == payload.embedding_input_hash,
                        EmbeddingRecordModel.embedding_spec_hash == payload.embedding_spec_sha256,
                    )
                )
                completed = session.scalar(
                    select(OutboxEventModel)
                    .where(
                        OutboxEventModel.workspace_id == payload.workspace_id,
                        OutboxEventModel.aggregate_type == "embedding_record",
                        OutboxEventModel.aggregate_id == payload.embedding_record_id,
                        OutboxEventModel.event_type == EventType.ASSET_INDEX_COMPLETED.value,
                        OutboxEventModel.trace_id == payload.operation_id,
                    )
                    .order_by(
                        OutboxEventModel.occurred_at.desc(),
                        OutboxEventModel.id.desc(),
                    )
                    .limit(1)
                )
                if embedding is None or completed is None:
                    return False
                completion: AssetIndexCompletedPayload | None = None
                invalid_completion = False
                try:
                    completion = AssetIndexCompletedPayload.model_validate(completed.payload_json)
                except ValueError:
                    invalid_completion = True
                if (
                    invalid_completion
                    or completion is None
                    or (
                        completion.operation_id != payload.operation_id
                        or completion.embedding_record_id != payload.embedding_record_id
                        or completion.workspace_id != payload.workspace_id
                        or completion.asset_id != payload.asset_id
                        or completion.asset_version_id != payload.asset_version_id
                        or completion.collection_id != payload.collection_id
                        or completion.input_hash != payload.embedding_input_hash
                        or completion.embedding_spec_sha256 != payload.embedding_spec_sha256
                    )
                ):
                    return False
            asset_version = session.scalar(
                select(AssetVersionModel).where(
                    AssetVersionModel.workspace_id == payload.workspace_id,
                    AssetVersionModel.id == payload.asset_version_id,
                    AssetVersionModel.asset_id == payload.asset_id,
                    AssetVersionModel.version_number == payload.asset_version_number,
                )
            )
            return asset_version is not None

    def commit_after_upsert(
        self,
        target: ImageIndexingTarget,
    ) -> IndexCommitDecision:
        with self._session_factory.begin() as session:
            embedding = self._lock_embedding_after_upsert(session, target)
            if embedding.operation_id != target.operation_id:
                database_now = session.scalar(select(literal_column("UTC_TIMESTAMP(6)")))
                if database_now is None:
                    raise RuntimeError("database clock unavailable")
                if database_now.tzinfo is None:
                    database_now = database_now.replace(tzinfo=UTC)
                self._enqueue_superseded_target_delete(
                    session=session,
                    target=target,
                    current_embedding=embedding,
                    now=database_now,
                )
                self._enqueue_completed(
                    session=session,
                    target=target,
                    embedding=embedding,
                    outcome="STALE",
                    now=database_now,
                )
                return IndexCommitDecision(indexed=False, stale_reason="SUPERSEDED")
            self._assert_provider_provenance(embedding=embedding, target=target)
            if embedding.state == EmbeddingState.INDEXED.value:
                return IndexCommitDecision(indexed=True)
            if embedding.state in {
                EmbeddingState.DELETE_PENDING.value,
                EmbeddingState.DELETED.value,
            }:
                return IndexCommitDecision(
                    indexed=False,
                    stale_reason=embedding.stale_reason,
                )
            replaying_permanent_failure = (
                embedding.state == EmbeddingState.PERMANENT_FAILED.value
                and self._is_active_audited_reconciliation(session, target)
            )
            if (
                embedding.state != EmbeddingState.PROCESSING.value
                and not replaying_permanent_failure
            ):
                raise ValueError("only the current PROCESSING generation can complete")
            snapshot = AssetRepository(session).get_current_usability_snapshot(
                workspace_id=target.workspace_id,
                asset_id=target.asset_id,
            )
            collection = self._lock_collection(session, target.collection_id)
            reason = self._eligibility_reason(
                snapshot=snapshot,
                asset_version_id=target.asset_version_id,
                rights_record_id=target.rights_record_id,
                rights_record_version=target.rights_record_version,
                provider=target.provider,
            )
            if collection.state != CollectionState.ACTIVE.value or not collection.is_write_enabled:
                reason = "COLLECTION_NOT_WRITE_ACTIVE"
            embedding.version += 1
            if reason is None:
                embedding.state = EmbeddingState.INDEXED.value
                embedding.indexed_at = snapshot.database_now
                embedding.stale_at = None
                embedding.stale_reason = None
                embedding.updated_at = snapshot.database_now
                self._enqueue_completed(
                    session=session,
                    target=target,
                    embedding=embedding,
                    outcome="INDEXED",
                    now=snapshot.database_now,
                )
                return IndexCommitDecision(indexed=True)
            now = snapshot.database_now if snapshot is not None else target.indexed_at
            embedding.state = EmbeddingState.DELETE_PENDING.value
            embedding.stale_at = now
            embedding.stale_reason = reason
            embedding.updated_at = now
            self._enqueue_completed(
                session=session,
                target=target,
                embedding=embedding,
                outcome="STALE",
                now=now,
            )
            self._enqueue_stale_delete(
                session=session,
                target=target,
                embedding=embedding,
                now=now,
            )
            return IndexCommitDecision(indexed=False, stale_reason=reason)

    @staticmethod
    def _is_active_audited_reconciliation(
        session: Session,
        target: ImageIndexingTarget,
    ) -> bool:
        if target.replay_source_dead_letter_id is None or target.replay_attempt < 1:
            return False
        operation = session.scalar(
            select(DurableOperationModel).where(
                DurableOperationModel.workspace_id == target.workspace_id,
                DurableOperationModel.id == target.operation_id,
            )
        )
        return bool(
            operation is not None
            and operation.state == OperationState.RECONCILING.value
            and operation.replay_source_dead_letter_id == target.replay_source_dead_letter_id
            and operation.replay_attempt == target.replay_attempt
        )

    @staticmethod
    def _assert_provider_provenance(
        *,
        embedding: EmbeddingRecordModel,
        target: ImageIndexingTarget,
    ) -> None:
        if (
            target.provider_request_id is None
            or target.actual_model is None
            or embedding.provider_request_id != target.provider_request_id
            or embedding.actual_model != target.actual_model
        ):
            raise ValueError("provider provenance is incomplete at index commit")

    @staticmethod
    def _enqueue_completed(
        *,
        session: Session,
        target: ImageIndexingTarget,
        embedding: EmbeddingRecordModel,
        outcome: Literal["INDEXED", "STALE"],
        now: datetime,
    ) -> None:
        payload = AssetIndexCompletedPayload(
            operation_id=target.operation_id,
            embedding_record_id=target.embedding_record_id,
            workspace_id=target.workspace_id,
            asset_id=target.asset_id,
            asset_version_id=target.asset_version_id,
            collection_id=target.collection_id,
            input_hash=target.input_hash,
            embedding_spec_sha256=target.embedding_spec_sha256,
            write_generation=target.write_generation,
            outcome=outcome,
        )
        OutboxRepository(session).add(
            OutboxEvent(
                envelope=EventEnvelope.create(
                    event_type=EventType.ASSET_INDEX_COMPLETED.value,
                    aggregate_type="embedding_record",
                    aggregate_id=target.embedding_record_id,
                    aggregate_version=embedding.version,
                    trace_id=target.operation_id,
                    payload=payload.model_dump(mode="json"),
                    now=now,
                ),
                available_at=now,
                workspace_id=target.workspace_id,
            )
        )

    def load_delete_target(
        self,
        payload: AssetIndexDeleteRequestedPayload,
    ) -> MilvusVectorIdentityV1:
        with self._session_factory() as session:
            embedding = session.get(EmbeddingRecordModel, payload.embedding_record_id)
            if embedding is None:
                raise ValueError("embedding delete target does not exist")
            self._assert_delete_identity(embedding=embedding, payload=payload)
            if embedding.write_generation < payload.write_generation:
                raise ValueError("embedding delete generation is from the future")
            if (
                embedding.write_generation == payload.write_generation
                and embedding.operation_id == payload.operation_id
                and embedding.state
                not in {
                    EmbeddingState.DELETE_PENDING.value,
                    EmbeddingState.DELETED.value,
                }
            ):
                raise ValueError("embedding delete target is not delete-pending")
            collection = session.get(CollectionRegistryModel, embedding.collection_id)
            if collection is None:
                raise ValueError("embedding delete collection does not exist")
            return MilvusVectorIdentityV1(
                collection_name=collection.physical_name,
                embedding_record_id=embedding.id,
                milvus_primary_key=generation_milvus_primary_key(
                    embedding_record_id=embedding.id,
                    write_generation=payload.write_generation,
                ),
                input_hash=embedding.input_hash,
                embedding_spec_sha256=embedding.embedding_spec_hash,
                write_generation=payload.write_generation,
            )

    def complete_delete(self, payload: AssetIndexDeleteRequestedPayload) -> bool:
        with self._session_factory.begin() as session:
            embedding = session.scalar(
                select(EmbeddingRecordModel)
                .where(
                    EmbeddingRecordModel.workspace_id == payload.workspace_id,
                    EmbeddingRecordModel.id == payload.embedding_record_id,
                )
                .with_for_update()
            )
            if embedding is None:
                raise ValueError("embedding delete target does not exist")
            self._assert_delete_identity(embedding=embedding, payload=payload)
            if (
                embedding.write_generation != payload.write_generation
                or embedding.operation_id != payload.operation_id
            ):
                return False
            if embedding.state == EmbeddingState.DELETED.value:
                return False
            if embedding.state != EmbeddingState.DELETE_PENDING.value:
                raise ValueError("embedding delete completion lost its generation fence")
            database_now = session.scalar(select(literal_column("UTC_TIMESTAMP(6)")))
            if database_now is None:
                raise RuntimeError("database time is unavailable")
            if database_now.tzinfo is None:
                database_now = database_now.replace(tzinfo=UTC)
            embedding.state = EmbeddingState.DELETED.value
            embedding.version += 1
            embedding.updated_at = database_now
            session.flush()
            return True

    def mark_current_asset_stale(
        self,
        *,
        workspace_id: str,
        asset_id: str,
        asset_version_id: str,
        reason: Literal["RIGHTS_INVALID", "ASSET_BLOCKED"],
    ) -> int:
        with self._session_factory.begin() as session:
            snapshot = AssetRepository(session).get_current_usability_snapshot(
                workspace_id=workspace_id,
                asset_id=asset_id,
            )
            if snapshot is None or snapshot.asset.current_version_id != asset_version_id:
                return 0
            embeddings = tuple(
                session.scalars(
                    select(EmbeddingRecordModel)
                    .where(
                        EmbeddingRecordModel.workspace_id == workspace_id,
                        EmbeddingRecordModel.asset_id == asset_id,
                        EmbeddingRecordModel.asset_version_id == asset_version_id,
                        EmbeddingRecordModel.vector_kind == VectorKind.IMAGE.value,
                        EmbeddingRecordModel.state == EmbeddingState.INDEXED.value,
                    )
                    .order_by(EmbeddingRecordModel.id)
                    .with_for_update()
                )
            )
            for embedding in embeddings:
                embedding.state = EmbeddingState.DELETE_PENDING.value
                embedding.stale_at = snapshot.database_now
                embedding.stale_reason = reason
                embedding.updated_at = snapshot.database_now
                embedding.version += 1
                payload = AssetIndexDeleteRequestedPayload(
                    operation_id=embedding.operation_id,
                    embedding_record_id=embedding.id,
                    workspace_id=embedding.workspace_id,
                    asset_id=embedding.asset_id,
                    asset_version_id=embedding.asset_version_id,
                    collection_id=embedding.collection_id,
                    input_hash=embedding.input_hash,
                    embedding_spec_sha256=embedding.embedding_spec_hash,
                    write_generation=embedding.write_generation,
                    reason=reason,
                )
                self._enqueue_delete_payload(
                    session=session,
                    payload=payload,
                    aggregate_version=embedding.version,
                    now=snapshot.database_now,
                )
            session.flush()
            return len(embeddings)

    @staticmethod
    def _assert_delete_identity(
        *,
        embedding: EmbeddingRecordModel,
        payload: AssetIndexDeleteRequestedPayload,
    ) -> None:
        if (
            embedding.workspace_id != payload.workspace_id
            or embedding.asset_id != payload.asset_id
            or embedding.asset_version_id != payload.asset_version_id
            or embedding.collection_id != payload.collection_id
            or embedding.input_hash != payload.input_hash
            or embedding.embedding_spec_hash != payload.embedding_spec_sha256
        ):
            raise ValueError("embedding delete identity/generation fence failed")

    def _load_locked(
        self,
        session: Session,
        request: OperationExecutionRequest,
    ) -> tuple[ImageIndexingTarget, EmbeddingRecordModel]:
        target, unlocked_embedding = self._load(session, request)
        self._assert_eligible(session, target, unlocked_embedding)
        collection = self._lock_collection(session, unlocked_embedding.collection_id)
        if collection.state != CollectionState.ACTIVE.value or not collection.is_write_enabled:
            raise ValueError("IMAGE collection is not ACTIVE and write-enabled")
        embedding = self._lock_exact_embedding(session, target)
        return self._target_from_models(target=target, embedding=embedding), embedding

    def _load(
        self,
        session: Session,
        request: OperationExecutionRequest,
    ) -> tuple[ImageIndexingTarget, EmbeddingRecordModel]:
        statement = select(EmbeddingRecordModel).where(
            EmbeddingRecordModel.workspace_id == request.workspace_id,
            EmbeddingRecordModel.id == request.target_id,
            EmbeddingRecordModel.operation_id == request.operation_id,
        )
        embedding = session.scalar(statement)
        if embedding is None:
            raise ValueError("embedding record is not bound to the durable operation")
        if (
            not self._operation_identity_matches(session, request, embedding)
            or embedding.vector_kind != VectorKind.IMAGE.value
        ):
            raise ValueError("embedding operation identity is stale")
        collection = session.get(CollectionRegistryModel, embedding.collection_id)
        asset_version = session.scalar(
            select(AssetVersionModel).where(
                AssetVersionModel.workspace_id == embedding.workspace_id,
                AssetVersionModel.id == embedding.asset_version_id,
                AssetVersionModel.asset_id == embedding.asset_id,
            )
        )
        if collection is None or asset_version is None:
            raise ValueError("embedding registry facts are incomplete")
        product_brand = ""
        asset_snapshot = AssetRepository(session).get_current_usability_snapshot(
            workspace_id=embedding.workspace_id,
            asset_id=embedding.asset_id,
        )
        if asset_snapshot is None:
            raise ValueError("embedding Asset authority facts are incomplete")
        self._assert_registry_facts(
            embedding=embedding,
            collection=collection,
            content_sha256=asset_version.sha256,
        )
        if asset_snapshot.asset.product_id is not None:
            product = session.scalar(
                select(ProductModel).where(
                    ProductModel.workspace_id == embedding.workspace_id,
                    ProductModel.id == asset_snapshot.asset.product_id,
                )
            )
            if product is not None:
                product_brand = product.brand
        database_now = asset_snapshot.database_now
        target = ImageIndexingTarget(
            operation_id=embedding.operation_id,
            embedding_record_id=embedding.id,
            workspace_id=embedding.workspace_id,
            asset_id=embedding.asset_id,
            asset_version_id=embedding.asset_version_id,
            asset_version_number=embedding.asset_version_number,
            rights_record_id=embedding.rights_record_id,
            rights_record_version=embedding.rights_record_version,
            collection_id=embedding.collection_id,
            collection_spec=CollectionSpec.create(
                model_family=collection.model_family,
                pinned_revision=collection.pinned_revision,
                dimension=collection.dimension,
                vector_kind=VectorKind(collection.vector_kind),
                schema_version=collection.schema_version,
                index_spec_version=collection.index_spec_version,
            ),
            provider=embedding.provider,
            model_id=embedding.model_id,
            model_configuration_version=embedding.model_configuration_version,
            preprocessing_version=embedding.preprocessing_version,
            input_hash=embedding.input_hash,
            embedding_spec_sha256=embedding.embedding_spec_hash,
            write_generation=embedding.write_generation,
            category=asset_version.category,
            brand=product_brand,
            asset_role=asset_version.role,
            content_sha256=asset_version.sha256,
            provider_request_id=embedding.provider_request_id,
            actual_model=embedding.actual_model,
            indexed_at=database_now,
            retention_class=asset_snapshot.asset.retention_class,
            replay_source_dead_letter_id=request.replay_source_dead_letter_id,
            replay_attempt=request.replay_attempt,
        )
        return target, embedding

    @staticmethod
    def _operation_identity_matches(
        session: Session,
        request: OperationExecutionRequest,
        embedding: EmbeddingRecordModel,
    ) -> bool:
        operation = session.scalar(
            select(DurableOperationModel).where(
                DurableOperationModel.workspace_id == request.workspace_id,
                DurableOperationModel.id == request.operation_id,
            )
        )
        expected_hash = compute_image_index_operation_hash(
            embedding_input_hash=embedding.input_hash,
            rights_record_id=embedding.rights_record_id,
            rights_record_version=embedding.rights_record_version,
            operation_epoch=request.target_version,
        )
        return bool(
            operation is not None
            and operation.target_type == "embedding_record"
            and operation.target_id == embedding.id
            and operation.target_version == request.target_version
            and operation.input_hash == request.input_hash
            and request.input_hash == expected_hash
            and operation.input_ref == f"mysql://embedding-records/{embedding.id}"
        )

    def _assert_eligible(
        self,
        session: Session,
        target: ImageIndexingTarget,
        embedding: EmbeddingRecordModel,
    ) -> None:
        snapshot = AssetRepository(session).get_current_usability_snapshot(
            workspace_id=target.workspace_id,
            asset_id=target.asset_id,
        )
        reason = self._eligibility_reason(
            snapshot=snapshot,
            asset_version_id=target.asset_version_id,
            rights_record_id=target.rights_record_id,
            rights_record_version=embedding.rights_record_version,
            provider=target.provider,
        )
        if reason is not None:
            raise ValueError(f"asset is not eligible for IMAGE indexing: {reason}")

    @staticmethod
    def _eligibility_reason(
        *,
        snapshot: CurrentUsabilitySnapshot | None,
        asset_version_id: str,
        rights_record_id: str,
        rights_record_version: int,
        provider: str,
    ) -> str | None:
        if snapshot is None:
            return "ASSET_NOT_FOUND"
        decision = evaluate_current_usability(
            asset=snapshot.asset,
            rights_record=snapshot.rights_record,
            asset_version_id=asset_version_id,
            purpose=INDEX_PURPOSE,
            provider=provider,
            requires_derivative=False,
            decision_time=snapshot.database_now,
        )
        if not decision.authorized:
            return decision.reason_code.value
        if (
            decision.rights_record_id != rights_record_id
            or decision.rights_record_version != rights_record_version
        ):
            return "RIGHTS_IDENTITY_CHANGED"
        return None

    @staticmethod
    def _assert_registry_facts(
        *,
        embedding: EmbeddingRecordModel,
        collection: CollectionRegistryModel,
        content_sha256: str,
    ) -> None:
        spec = CollectionSpec.create(
            model_family=collection.model_family,
            pinned_revision=collection.pinned_revision,
            dimension=collection.dimension,
            vector_kind=VectorKind(collection.vector_kind),
            schema_version=collection.schema_version,
            index_spec_version=collection.index_spec_version,
        )
        if (
            collection.dynamic_fields_enabled
            or collection.logical_key != spec.logical_key
            or collection.spec_hash != spec.spec_hash
            or collection.physical_name != spec.physical_name
            or embedding.model_family != collection.model_family
            or embedding.model_id != collection.model_id
            or embedding.pinned_revision != collection.pinned_revision
            or embedding.dimension != collection.dimension
            or embedding.vector_kind != collection.vector_kind
        ):
            raise ValueError("embedding and collection registry facts are inconsistent")
        expected_hash = compute_embedding_input_hash(
            content_sha256=content_sha256,
            provider=embedding.provider,
            preprocessing_version=embedding.preprocessing_version,
            model_configuration_version=embedding.model_configuration_version,
            vector_kind=VectorKind(embedding.vector_kind),
        )
        if embedding.input_hash != expected_hash:
            raise ValueError("embedding input hash does not match authoritative IMAGE facts")

    @staticmethod
    def _lock_collection(session: Session, collection_id: str) -> CollectionRegistryModel:
        collection = session.scalar(
            select(CollectionRegistryModel)
            .where(CollectionRegistryModel.id == collection_id)
            .with_for_update(read=True)
        )
        if collection is None:
            raise ValueError("embedding collection registry disappeared")
        return collection

    @staticmethod
    def _lock_exact_embedding(
        session: Session,
        target: ImageIndexingTarget,
    ) -> EmbeddingRecordModel:
        embedding = session.scalar(
            select(EmbeddingRecordModel)
            .where(
                EmbeddingRecordModel.workspace_id == target.workspace_id,
                EmbeddingRecordModel.id == target.embedding_record_id,
                EmbeddingRecordModel.operation_id == target.operation_id,
                EmbeddingRecordModel.collection_id == target.collection_id,
                EmbeddingRecordModel.input_hash == target.input_hash,
                EmbeddingRecordModel.embedding_spec_hash == target.embedding_spec_sha256,
                EmbeddingRecordModel.write_generation == target.write_generation,
            )
            .with_for_update()
        )
        if embedding is None:
            raise ValueError("embedding operation/collection/generation CAS failed")
        return embedding

    @staticmethod
    def _lock_embedding_after_upsert(
        session: Session,
        target: ImageIndexingTarget,
    ) -> EmbeddingRecordModel:
        embedding = session.scalar(
            select(EmbeddingRecordModel)
            .where(
                EmbeddingRecordModel.workspace_id == target.workspace_id,
                EmbeddingRecordModel.id == target.embedding_record_id,
            )
            .with_for_update()
        )
        if embedding is None:
            raise ValueError("embedding operation/collection/generation CAS failed")
        is_exact = (
            embedding.operation_id == target.operation_id
            and embedding.collection_id == target.collection_id
            and embedding.input_hash == target.input_hash
            and embedding.embedding_spec_hash == target.embedding_spec_sha256
            and embedding.write_generation == target.write_generation
        )
        if is_exact:
            return embedding
        is_superseded = (
            target.provider_request_id is not None
            and target.actual_model is not None
            and target.write_generation > 0
            and embedding.operation_id != target.operation_id
            and embedding.write_generation >= target.write_generation
            and embedding.workspace_id == target.workspace_id
            and embedding.id == target.embedding_record_id
            and embedding.asset_id == target.asset_id
            and embedding.asset_version_id == target.asset_version_id
            and embedding.asset_version_number == target.asset_version_number
            and embedding.collection_id == target.collection_id
            and embedding.vector_kind == VectorKind.IMAGE.value
            and embedding.provider == target.provider
            and embedding.input_hash == target.input_hash
            and embedding.embedding_spec_hash == target.embedding_spec_sha256
        )
        if not is_superseded:
            raise ValueError("embedding operation/collection/generation CAS failed")
        return embedding

    @staticmethod
    def _enqueue_superseded_target_delete(
        *,
        session: Session,
        target: ImageIndexingTarget,
        current_embedding: EmbeddingRecordModel,
        now: datetime,
    ) -> None:
        payload = AssetIndexDeleteRequestedPayload(
            operation_id=target.operation_id,
            embedding_record_id=target.embedding_record_id,
            workspace_id=target.workspace_id,
            asset_id=target.asset_id,
            asset_version_id=target.asset_version_id,
            collection_id=target.collection_id,
            input_hash=target.input_hash,
            embedding_spec_sha256=target.embedding_spec_sha256,
            write_generation=target.write_generation,
            reason="SUPERSEDED",
        )
        MySqlIndexingAuthority._enqueue_delete_payload(
            session=session,
            payload=payload,
            aggregate_version=current_embedding.version,
            now=now,
        )

    @staticmethod
    def _enqueue_stale_delete(
        *,
        session: Session,
        target: ImageIndexingTarget,
        embedding: EmbeddingRecordModel,
        now: datetime,
    ) -> None:
        payload = AssetIndexDeleteRequestedPayload(
            operation_id=target.operation_id,
            embedding_record_id=target.embedding_record_id,
            workspace_id=target.workspace_id,
            asset_id=target.asset_id,
            asset_version_id=target.asset_version_id,
            collection_id=target.collection_id,
            input_hash=target.input_hash,
            embedding_spec_sha256=target.embedding_spec_sha256,
            write_generation=target.write_generation,
            reason="RIGHTS_INVALID",
        )
        MySqlIndexingAuthority._enqueue_delete_payload(
            session=session,
            payload=payload,
            aggregate_version=embedding.version,
            now=now,
        )

    @staticmethod
    def _enqueue_delete_payload(
        *,
        session: Session,
        payload: AssetIndexDeleteRequestedPayload,
        aggregate_version: int,
        now: datetime,
    ) -> None:
        envelope = EventEnvelope.create(
            event_type=EventType.ASSET_INDEX_DELETE_REQUESTED.value,
            aggregate_type="embedding_record",
            aggregate_id=payload.embedding_record_id,
            aggregate_version=aggregate_version,
            trace_id=payload.operation_id,
            payload=payload.model_dump(mode="json"),
            now=now,
        )
        OutboxRepository(session).add(
            OutboxEvent(
                envelope=envelope,
                available_at=now,
                workspace_id=payload.workspace_id,
            )
        )

    @staticmethod
    def _target_from_models(
        *,
        target: ImageIndexingTarget,
        embedding: EmbeddingRecordModel,
    ) -> ImageIndexingTarget:
        return replace(
            target,
            write_generation=embedding.write_generation,
            provider_request_id=embedding.provider_request_id,
            actual_model=embedding.actual_model,
        )


class MySqlExactImageReference:
    """Resolve one exact controlled AssetObject before issuing a bounded read."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        storage: ObjectStorage,
        lifetime: timedelta,
    ) -> None:
        if lifetime <= timedelta(0):
            raise ValueError("temporary IMAGE reference lifetime must be positive")
        self._session_factory = session_factory
        self._storage = storage
        self._lifetime = lifetime

    def temporary_input(self, target: ImageIndexingTarget) -> EmbeddingImageInputV1:
        with self._session_factory() as session:
            object_fact = session.scalar(
                select(AssetObjectModel).where(
                    AssetObjectModel.workspace_id == target.workspace_id,
                    AssetObjectModel.asset_version_id == target.asset_version_id,
                    AssetObjectModel.role == "CONTROLLED_ORIGINAL",
                    AssetObjectModel.state == "CONTROLLED",
                )
            )
            if (
                object_fact is None
                or object_fact.sha256 != target.content_sha256
                or object_fact.provider_version_id.strip().lower() == "null"
            ):
                raise ValueError("exact controlled IMAGE object is unavailable")
            reference = ObjectReference(
                location=StorageLocationClass(object_fact.location),
                key=object_fact.key,
                version_id=object_fact.provider_version_id,
            )
            etag = object_fact.etag
        temporary = self._storage.temporary_read(
            TemporaryReadRequest(
                reference=reference,
                expected_etag=etag,
                expected_sha256=target.content_sha256,
                expires_at=datetime.now(UTC) + self._lifetime,
            )
        )
        if temporary.method != "GET":
            raise ValueError("exact IMAGE temporary read must use GET")
        return EmbeddingImageInputV1(
            asset_version_id=target.asset_version_id,
            content_sha256=target.content_sha256,
            byte_size=object_fact.byte_size,
            url=SecretStr(temporary.url),
            required_headers={
                name: SecretStr(value) for name, value in temporary.required_headers.items()
            },
            expires_at=temporary.expires_at,
        )
