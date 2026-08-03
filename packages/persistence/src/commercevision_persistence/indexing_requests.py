"""Atomic MySQL request boundary for one current IMAGE indexing operation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid5

from commercevision_application import OperationExecutionRequest
from commercevision_contracts.events import (
    AssetIndexDeleteRequestedPayload,
    AssetIndexRequestedPayload,
    EventType,
)
from commercevision_domain import (
    AssetKind,
    CollectionSpec,
    CollectionState,
    DurableOperation,
    EmbeddingRecord,
    OperationKind,
    VectorKind,
    compute_embedding_input_hash,
    evaluate_current_usability,
)
from commercevision_domain.messaging import EventEnvelope, OutboxEvent
from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .assets import AssetRepository
from .indexing_identity import compute_image_index_operation_hash
from .indexing_models import CollectionRegistryModel, EmbeddingRecordModel
from .integrity import database_constraint_name
from .models import AssetVersionModel, DurableOperationModel
from .operation_mappers import operation_from_model
from .operations import OperationRepository
from .repositories import OutboxRepository

_COLLECTION_NAMESPACE = UUID("8427214d-c2d0-5739-9105-6a3c20f4ef5d")
_EXPECTED_REQUEST_RACE_CONSTRAINTS = frozenset(
    {
        "uq_durable_operation_logical",
        "uq_embedding_records_asset_spec",
        "uq_embedding_records_operation",
    }
)


def is_expected_image_index_request_race(exc: IntegrityError) -> bool:
    """Only duplicate identities created by a concurrent identical request are winners."""
    return database_constraint_name(exc) in _EXPECTED_REQUEST_RACE_CONSTRAINTS


@dataclass(frozen=True, slots=True)
class ImageIndexRequestResult:
    operation: OperationExecutionRequest
    created: bool


class ImageIndexNotApplicable(ValueError):
    """The current Asset is not an authorized IMAGE indexing candidate."""


class MySqlImageIndexRequestService:
    """Resolve collection + record + operation + command event in one transaction."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        collection_spec: CollectionSpec,
        provider: str,
        model_id: str,
        model_configuration_version: str,
        preprocessing_version: str,
        max_attempts: int = 5,
        max_reconciliation_attempts: int = 8,
    ) -> None:
        if collection_spec.vector_kind is not VectorKind.IMAGE:
            raise ValueError("Ticket 09 request service only supports IMAGE vectors")
        for value, field in (
            (provider, "provider"),
            (model_id, "model_id"),
            (model_configuration_version, "model_configuration_version"),
            (preprocessing_version, "preprocessing_version"),
        ):
            if not value or value != value.strip():
                raise ValueError(f"{field} must be non-empty and trimmed")
        if max_attempts < 1 or max_reconciliation_attempts < 1:
            raise ValueError("operation attempt budgets must be positive")
        self._session_factory = session_factory
        self._collection_spec = collection_spec
        self._provider = provider
        self._model_id = model_id
        self._model_configuration_version = model_configuration_version
        self._preprocessing_version = preprocessing_version
        self._max_attempts = max_attempts
        self._max_reconciliation_attempts = max_reconciliation_attempts

    def request_current_image(
        self,
        *,
        workspace_id: str,
        asset_id: str,
    ) -> ImageIndexRequestResult:
        record_id: str | None = None
        try:
            with self._session_factory.begin() as session:
                snapshot = AssetRepository(session).get_current_usability_snapshot(
                    workspace_id=workspace_id,
                    asset_id=asset_id,
                )
                if snapshot is None or snapshot.asset.current_version_id is None:
                    raise ImageIndexNotApplicable("current IMAGE asset does not exist")
                if snapshot.asset.kind is not AssetKind.IMAGE:
                    raise ImageIndexNotApplicable("current Asset is not an IMAGE")
                decision = evaluate_current_usability(
                    asset=snapshot.asset,
                    rights_record=snapshot.rights_record,
                    asset_version_id=snapshot.asset.current_version_id,
                    purpose="RETRIEVAL",
                    provider=self._provider,
                    requires_derivative=False,
                    decision_time=snapshot.database_now,
                )
                if (
                    not decision.authorized
                    or snapshot.rights_record is None
                    or decision.rights_record_id is None
                    or decision.rights_record_version is None
                ):
                    raise ImageIndexNotApplicable(
                        f"asset is not eligible for IMAGE indexing: {decision.reason_code.value}"
                    )
                version = session.scalar(
                    select(AssetVersionModel).where(
                        AssetVersionModel.workspace_id == workspace_id,
                        AssetVersionModel.id == decision.asset_version_id,
                        AssetVersionModel.asset_id == asset_id,
                    )
                )
                if version is None:
                    raise ValueError("current IMAGE version facts are missing")
                collection = self._resolve_collection(session, snapshot.database_now)
                input_hash = compute_embedding_input_hash(
                    content_sha256=version.sha256,
                    provider=self._provider,
                    preprocessing_version=self._preprocessing_version,
                    model_configuration_version=self._model_configuration_version,
                    vector_kind=VectorKind.IMAGE,
                )
                embedding_spec_hash = self._embedding_spec_hash()
                record = EmbeddingRecord.create(
                    workspace_id=workspace_id,
                    asset_id=asset_id,
                    asset_version_id=version.id,
                    asset_version_number=version.version_number,
                    rights_record_id=decision.rights_record_id,
                    rights_record_version=decision.rights_record_version,
                    collection_id=collection.id,
                    embedding_spec_hash=embedding_spec_hash,
                    input_hash=input_hash,
                    vector_kind=VectorKind.IMAGE,
                    now=snapshot.database_now,
                )
                record_id = record.id
                existing = self._load_existing(session, workspace_id, record.id)
                if existing is not None:
                    existing_record = session.scalar(
                        select(EmbeddingRecordModel)
                        .where(
                            EmbeddingRecordModel.workspace_id == workspace_id,
                            EmbeddingRecordModel.id == record.id,
                        )
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                    if existing_record is None:
                        raise ValueError("existing IMAGE record disappeared")
                    existing = self._load_existing(session, workspace_id, record.id)
                    if existing is None:
                        raise ValueError("existing IMAGE operation disappeared")
                    rights_changed = (
                        existing_record.rights_record_id != decision.rights_record_id
                        or existing_record.rights_record_version != decision.rights_record_version
                    )
                    lifecycle_reactivation = existing_record.state in {
                        "STALE",
                        "DELETE_PENDING",
                        "DELETED",
                    }
                    if existing_record.state != "PERMANENT_FAILED" and (
                        rights_changed or lifecycle_reactivation
                    ):
                        if existing_record.write_generation > 0 and existing_record.state in {
                            "INDEXED",
                            "PROCESSING",
                            "RETRYABLE_FAILED",
                        }:
                            self._enqueue_superseded_delete(
                                session=session,
                                embedding=existing_record,
                                now=snapshot.database_now,
                            )
                        operation_epoch = existing.target_version + 1
                        operation = self._new_operation(
                            workspace_id=workspace_id,
                            record_id=record.id,
                            operation_epoch=operation_epoch,
                            embedding_input_hash=input_hash,
                            rights_record_id=decision.rights_record_id,
                            rights_record_version=decision.rights_record_version,
                            now=snapshot.database_now,
                        )
                        OperationRepository(session).add(operation)
                        session.flush()
                        existing_record.operation_id = operation.id
                        existing_record.rights_record_id = decision.rights_record_id
                        existing_record.rights_record_version = decision.rights_record_version
                        existing_record.state = "PENDING"
                        existing_record.provider_request_id = None
                        existing_record.actual_model = None
                        existing_record.indexed_at = None
                        existing_record.stale_at = None
                        existing_record.stale_reason = None
                        existing_record.version += 1
                        existing_record.updated_at = snapshot.database_now
                        self._enqueue_request(
                            session=session,
                            embedding=existing_record,
                            operation=operation,
                            now=snapshot.database_now,
                        )
                        session.flush()
                        return ImageIndexRequestResult(
                            operation=OperationExecutionRequest.from_operation(operation),
                            created=True,
                        )
                    return ImageIndexRequestResult(
                        operation=OperationExecutionRequest.from_operation(existing),
                        created=False,
                    )
                operation = self._new_operation(
                    workspace_id=workspace_id,
                    record_id=record.id,
                    operation_epoch=1,
                    embedding_input_hash=input_hash,
                    rights_record_id=decision.rights_record_id,
                    rights_record_version=decision.rights_record_version,
                    now=snapshot.database_now,
                )
                OperationRepository(session).add(operation)
                session.flush()
                embedding_model = EmbeddingRecordModel(
                    id=record.id,
                    workspace_id=record.workspace_id,
                    asset_id=record.asset_id,
                    asset_version_id=record.asset_version_id,
                    asset_version_number=record.asset_version_number,
                    rights_record_id=record.rights_record_id,
                    rights_record_version=record.rights_record_version,
                    collection_id=record.collection_id,
                    operation_id=operation.id,
                    vector_kind=record.vector_kind.value,
                    provider=self._provider,
                    model_family=self._collection_spec.model_family,
                    model_id=self._model_id,
                    pinned_revision=self._collection_spec.pinned_revision,
                    model_configuration_version=self._model_configuration_version,
                    preprocessing_version=self._preprocessing_version,
                    dimension=self._collection_spec.dimension,
                    input_hash=record.input_hash,
                    embedding_spec_hash=record.embedding_spec_hash,
                    milvus_primary_key=record.milvus_primary_key,
                    state=record.state.value,
                    write_generation=record.write_generation,
                    provider_request_id=None,
                    actual_model=None,
                    indexed_at=None,
                    stale_at=None,
                    stale_reason=None,
                    version=record.version,
                    created_at=record.created_at,
                    updated_at=record.updated_at,
                )
                session.add(embedding_model)
                self._enqueue_request(
                    session=session,
                    embedding=embedding_model,
                    operation=operation,
                    now=snapshot.database_now,
                )
                session.flush()
                return ImageIndexRequestResult(
                    operation=OperationExecutionRequest.from_operation(operation),
                    created=True,
                )
        except IntegrityError as exc:
            if not is_expected_image_index_request_race(exc):
                raise
            if record_id is None:
                raise
            with self._session_factory() as session:
                winner = self._load_existing(session, workspace_id, record_id)
                if winner is None:
                    raise
                return ImageIndexRequestResult(
                    operation=OperationExecutionRequest.from_operation(winner),
                    created=False,
                )

    def _new_operation(
        self,
        *,
        workspace_id: str,
        record_id: str,
        operation_epoch: int,
        embedding_input_hash: str,
        rights_record_id: str,
        rights_record_version: int,
        now: datetime,
    ) -> DurableOperation:
        return DurableOperation.create(
            workspace_id=workspace_id,
            kind=OperationKind.ASSET_INDEXING,
            target_type="embedding_record",
            target_id=record_id,
            target_version=operation_epoch,
            input_hash=compute_image_index_operation_hash(
                embedding_input_hash=embedding_input_hash,
                rights_record_id=rights_record_id,
                rights_record_version=rights_record_version,
                operation_epoch=operation_epoch,
            ),
            input_ref=f"mysql://embedding-records/{record_id}",
            max_attempts=self._max_attempts,
            max_reconciliation_attempts=self._max_reconciliation_attempts,
            execution_max_elapsed=timedelta(hours=24),
            now=now,
        )

    def _enqueue_request(
        self,
        *,
        session: Session,
        embedding: EmbeddingRecordModel,
        operation: DurableOperation,
        now: datetime,
    ) -> None:
        payload = AssetIndexRequestedPayload(
            operation_id=operation.id,
            operation_epoch=operation.target_version,
            operation_input_hash=operation.input_hash,
            embedding_record_id=embedding.id,
            workspace_id=embedding.workspace_id,
            asset_id=embedding.asset_id,
            asset_version_id=embedding.asset_version_id,
            asset_version_number=embedding.asset_version_number,
            rights_record_id=embedding.rights_record_id,
            rights_record_version=embedding.rights_record_version,
            collection_id=embedding.collection_id,
            vector_kind="IMAGE",
            provider=self._provider,
            embedding_input_hash=embedding.input_hash,
            embedding_spec_sha256=embedding.embedding_spec_hash,
        )
        OutboxRepository(session).add(
            OutboxEvent(
                envelope=EventEnvelope.create(
                    event_type=EventType.ASSET_INDEX_REQUESTED.value,
                    aggregate_type="embedding_record",
                    aggregate_id=embedding.id,
                    aggregate_version=embedding.version,
                    trace_id=operation.id,
                    payload=payload.model_dump(mode="json"),
                    now=now,
                ),
                available_at=now,
                workspace_id=embedding.workspace_id,
            )
        )

    @staticmethod
    def _enqueue_superseded_delete(
        *,
        session: Session,
        embedding: EmbeddingRecordModel,
        now: datetime,
    ) -> None:
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
            reason="SUPERSEDED",
        )
        OutboxRepository(session).add(
            OutboxEvent(
                envelope=EventEnvelope.create(
                    event_type=EventType.ASSET_INDEX_DELETE_REQUESTED.value,
                    aggregate_type="embedding_record",
                    aggregate_id=embedding.id,
                    aggregate_version=embedding.version,
                    trace_id=embedding.operation_id,
                    payload=payload.model_dump(mode="json"),
                    now=now,
                ),
                available_at=now,
                workspace_id=embedding.workspace_id,
            )
        )

    def _resolve_collection(
        self,
        session: Session,
        now: datetime,
    ) -> CollectionRegistryModel:
        collection_id = str(uuid5(_COLLECTION_NAMESPACE, self._collection_spec.spec_hash))
        statement = mysql_insert(CollectionRegistryModel).values(
            id=collection_id,
            logical_key=self._collection_spec.logical_key,
            spec_hash=self._collection_spec.spec_hash,
            physical_name=self._collection_spec.physical_name,
            model_family=self._collection_spec.model_family,
            model_id=self._model_id,
            pinned_revision=self._collection_spec.pinned_revision,
            dimension=self._collection_spec.dimension,
            vector_kind=self._collection_spec.vector_kind.value,
            schema_version=self._collection_spec.schema_version,
            index_spec_version=self._collection_spec.index_spec_version,
            dynamic_fields_enabled=False,
            state=CollectionState.PLANNED.value,
            is_read_enabled=False,
            is_write_enabled=False,
            validation_summary_json={},
            version=1,
            created_at=now,
            updated_at=now,
        )
        session.execute(statement.on_duplicate_key_update(id=CollectionRegistryModel.id))
        collection = session.get(CollectionRegistryModel, collection_id)
        if collection is None:
            raise RuntimeError("collection registry winner could not be reloaded")
        if (
            collection.logical_key != self._collection_spec.logical_key
            or collection.spec_hash != self._collection_spec.spec_hash
            or collection.physical_name != self._collection_spec.physical_name
            or collection.model_id != self._model_id
            or collection.dynamic_fields_enabled
        ):
            raise ValueError("collection registry identity conflicts with configured IMAGE spec")
        return collection

    def _embedding_spec_hash(self) -> str:
        canonical = "\0".join(
            (
                self._provider,
                self._model_id,
                self._model_configuration_version,
                self._preprocessing_version,
                self._collection_spec.spec_hash,
            )
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _load_existing(
        session: Session,
        workspace_id: str,
        record_id: str,
    ) -> DurableOperation | None:
        embedding = session.scalar(
            select(EmbeddingRecordModel).where(
                EmbeddingRecordModel.workspace_id == workspace_id,
                EmbeddingRecordModel.id == record_id,
            )
        )
        if embedding is None:
            return None
        operation_model = session.scalar(
            select(DurableOperationModel).where(
                DurableOperationModel.workspace_id == workspace_id,
                DurableOperationModel.id == embedding.operation_id,
            )
        )
        if operation_model is None:
            raise ValueError("existing embedding record has no durable operation")
        if (
            operation_model.target_id != embedding.id
            or operation_model.input_hash
            != compute_image_index_operation_hash(
                embedding_input_hash=embedding.input_hash,
                rights_record_id=embedding.rights_record_id,
                rights_record_version=embedding.rights_record_version,
                operation_epoch=operation_model.target_version,
            )
            or operation_model.input_ref != f"mysql://embedding-records/{embedding.id}"
        ):
            raise ValueError("existing IMAGE operation identity conflicts with embedding facts")
        return operation_from_model(operation_model)
