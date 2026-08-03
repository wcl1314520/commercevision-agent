"""Atomic MySQL request boundary for current vector indexing operations."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
    build_controlled_product_text,
    compute_embedding_input_hash,
    compute_product_fused_input_hash,
    evaluate_current_usability,
)
from commercevision_domain.messaging import EventEnvelope, OutboxEvent
from sqlalchemy import literal_column, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .assets import AssetRepository
from .indexing_identity import compute_index_operation_hash
from .indexing_models import (
    CollectionRegistryModel,
    EmbeddingRecordModel,
    ProductSearchDocumentModel,
)
from .integrity import database_constraint_name
from .models import AssetVersionModel, DurableOperationModel, ProductModel
from .operation_mappers import operation_from_model
from .operations import OperationRepository
from .product_brief_models import (
    ProductBriefEvidenceModel,
    ProductBriefFieldModel,
    ProductBriefModel,
    ProductBriefVersionModel,
)
from .repositories import OutboxRepository

_COLLECTION_NAMESPACE = UUID("8427214d-c2d0-5739-9105-6a3c20f4ef5d")
_SEARCH_DOCUMENT_NAMESPACE = UUID("b72d6d82-84ef-5fd8-996b-3f493177fa82")
_EXPECTED_REQUEST_RACE_CONSTRAINTS = frozenset(
    {
        "uq_durable_operation_logical",
        "uq_embedding_records_asset_spec",
        "uq_embedding_records_operation",
        "uq_product_search_documents_asset_input",
        "uq_product_search_documents_workspace_id",
        "uq_product_search_documents_embedding_record",
    }
)


def is_expected_index_request_race(exc: IntegrityError) -> bool:
    """Only duplicate identities created by a concurrent identical request are winners."""
    return database_constraint_name(exc) in _EXPECTED_REQUEST_RACE_CONSTRAINTS


is_expected_image_index_request_race = is_expected_index_request_race


@dataclass(frozen=True, slots=True)
class ImageIndexRequestResult:
    operation: OperationExecutionRequest
    created: bool


@dataclass(frozen=True, slots=True)
class ProductFusedIndexRequestResult:
    operation: OperationExecutionRequest
    embedding_record_id: str
    search_document_id: str
    asset_version_id: str
    created: bool


class ImageIndexNotApplicable(ValueError):
    """The current Asset is not an authorized IMAGE indexing candidate."""


class ProductFusedIndexNotApplicable(ValueError):
    """The confirmed ProductBrief has no authorized PRODUCT_FUSED candidate."""


class MySqlIndexRequestService:
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
        if collection_spec.vector_kind not in {VectorKind.IMAGE, VectorKind.PRODUCT_FUSED}:
            raise ValueError("request service requires an indexable vector kind")
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
        if self._collection_spec.vector_kind is not VectorKind.IMAGE:
            raise ValueError("current image requests require an IMAGE collection")
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
                            vector_kind=VectorKind.IMAGE,
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
                    vector_kind=VectorKind.IMAGE,
                    operation_epoch=1,
                    embedding_input_hash=input_hash,
                    rights_record_id=decision.rights_record_id,
                    rights_record_version=decision.rights_record_version,
                    now=snapshot.database_now,
                )
                OperationRepository(session).add(operation)
                session.flush()
                embedding_model = self._embedding_model(record=record, operation=operation)
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
            if not is_expected_index_request_race(exc):
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

    def request_confirmed_brief(
        self,
        *,
        workspace_id: str,
        product_brief_id: str,
        product_brief_version_id: str,
    ) -> tuple[ProductFusedIndexRequestResult, ...]:
        if self._collection_spec.vector_kind is not VectorKind.PRODUCT_FUSED:
            raise ValueError("confirmed ProductBrief requests require a PRODUCT_FUSED collection")
        try:
            return self._request_confirmed_brief_once(
                workspace_id=workspace_id,
                product_brief_id=product_brief_id,
                product_brief_version_id=product_brief_version_id,
            )
        except IntegrityError as exc:
            if not is_expected_index_request_race(exc):
                raise
            return self._request_confirmed_brief_once(
                workspace_id=workspace_id,
                product_brief_id=product_brief_id,
                product_brief_version_id=product_brief_version_id,
            )

    def request_current_product_fused_for_asset(
        self,
        *,
        workspace_id: str,
        asset_id: str,
    ) -> tuple[ProductFusedIndexRequestResult, ...]:
        """Re-evaluate current confirmed briefs that depend on an Asset."""
        if self._collection_spec.vector_kind is not VectorKind.PRODUCT_FUSED:
            raise ValueError("current fused requests require a PRODUCT_FUSED collection")
        with self._session_factory() as session:
            candidates = tuple(
                session.execute(
                    select(
                        ProductBriefModel.id,
                        ProductBriefModel.confirmed_version_id,
                    )
                    .join(
                        ProductBriefEvidenceModel,
                        (ProductBriefEvidenceModel.workspace_id == ProductBriefModel.workspace_id)
                        & (ProductBriefEvidenceModel.product_brief_id == ProductBriefModel.id)
                        & (
                            ProductBriefEvidenceModel.product_brief_version_id
                            == ProductBriefModel.confirmed_version_id
                        ),
                    )
                    .join(
                        AssetVersionModel,
                        (AssetVersionModel.workspace_id == ProductBriefEvidenceModel.workspace_id)
                        & (
                            AssetVersionModel.id
                            == ProductBriefEvidenceModel.source_asset_version_id
                        ),
                    )
                    .where(
                        ProductBriefModel.workspace_id == workspace_id,
                        ProductBriefModel.state == "CONFIRMED",
                        ProductBriefModel.confirmed_version_id.is_not(None),
                        ProductBriefModel.current_version_id
                        == ProductBriefModel.confirmed_version_id,
                        AssetVersionModel.asset_id == asset_id,
                    )
                    .distinct()
                    .order_by(ProductBriefModel.id)
                )
            )
        results: list[ProductFusedIndexRequestResult] = []
        for product_brief_id, product_brief_version_id in candidates:
            if product_brief_version_id is None:
                continue
            try:
                results.extend(
                    self.request_confirmed_brief(
                        workspace_id=workspace_id,
                        product_brief_id=product_brief_id,
                        product_brief_version_id=product_brief_version_id,
                    )
                )
            except ProductFusedIndexNotApplicable:
                continue
        return tuple(results)

    def _request_confirmed_brief_once(
        self,
        *,
        workspace_id: str,
        product_brief_id: str,
        product_brief_version_id: str,
    ) -> tuple[ProductFusedIndexRequestResult, ...]:
        with self._session_factory.begin() as session:
            brief = session.scalar(
                select(ProductBriefModel)
                .where(
                    ProductBriefModel.workspace_id == workspace_id,
                    ProductBriefModel.id == product_brief_id,
                )
                .with_for_update()
            )
            if (
                brief is None
                or brief.state != "CONFIRMED"
                or brief.confirmed_version_id != product_brief_version_id
                or brief.current_version_id != product_brief_version_id
            ):
                raise ProductFusedIndexNotApplicable(
                    "ProductBrief is not confirmed at the requested current version"
                )
            version = session.scalar(
                select(ProductBriefVersionModel).where(
                    ProductBriefVersionModel.workspace_id == workspace_id,
                    ProductBriefVersionModel.id == product_brief_version_id,
                    ProductBriefVersionModel.product_brief_id == product_brief_id,
                )
            )
            product = session.scalar(
                select(ProductModel).where(
                    ProductModel.workspace_id == workspace_id,
                    ProductModel.id == brief.product_id,
                )
            )
            if version is None or product is None:
                raise ValueError("confirmed ProductBrief facts are incomplete")
            database_now = session.scalar(select(literal_column("UTC_TIMESTAMP(6)")))
            if database_now is None:
                raise RuntimeError("MySQL authority time is unavailable")
            if database_now.tzinfo is None:
                database_now = database_now.replace(tzinfo=UTC)
            if version.retention_class == "TASK" and (
                version.retention_deadline is None or version.retention_deadline <= database_now
            ):
                raise ProductFusedIndexNotApplicable("confirmed ProductBrief retention expired")

            field_models = list(
                session.scalars(
                    select(ProductBriefFieldModel)
                    .where(
                        ProductBriefFieldModel.workspace_id == workspace_id,
                        ProductBriefFieldModel.product_brief_id == product_brief_id,
                        ProductBriefFieldModel.product_brief_version_id == product_brief_version_id,
                    )
                    .order_by(ProductBriefFieldModel.path)
                )
            )
            fields = {field.path: field.value_json for field in field_models}
            approved_labels = self._approved_terms(field_models, "common.approved_labels")
            approved_notes = self._approved_terms(field_models, "common.approved_notes")
            controlled = build_controlled_product_text(
                confirmed_product_brief_version_id=product_brief_version_id,
                confirmed_fields=fields,
                approved_labels=approved_labels,
                approved_notes=approved_notes,
            )
            source_version_ids = tuple(
                session.scalars(
                    select(ProductBriefEvidenceModel.source_asset_version_id)
                    .where(
                        ProductBriefEvidenceModel.workspace_id == workspace_id,
                        ProductBriefEvidenceModel.product_brief_id == product_brief_id,
                        ProductBriefEvidenceModel.product_brief_version_id
                        == product_brief_version_id,
                    )
                    .distinct()
                    .order_by(ProductBriefEvidenceModel.source_asset_version_id)
                )
            )
            collection = self._resolve_collection(session, database_now)
            embedding_spec_hash = self._embedding_spec_hash()
            results: list[ProductFusedIndexRequestResult] = []
            for source_version_id in source_version_ids:
                source_version = session.scalar(
                    select(AssetVersionModel).where(
                        AssetVersionModel.workspace_id == workspace_id,
                        AssetVersionModel.id == source_version_id,
                    )
                )
                if source_version is None:
                    raise ValueError("ProductBrief source Asset Version is missing")
                snapshot = AssetRepository(session).get_current_usability_snapshot(
                    workspace_id=workspace_id,
                    asset_id=source_version.asset_id,
                )
                if (
                    snapshot is None
                    or snapshot.asset.kind is not AssetKind.IMAGE
                    or snapshot.asset.current_version_id != source_version.id
                ):
                    continue
                decision = evaluate_current_usability(
                    asset=snapshot.asset,
                    rights_record=snapshot.rights_record,
                    asset_version_id=source_version.id,
                    purpose="RETRIEVAL",
                    provider=self._provider,
                    requires_derivative=False,
                    decision_time=database_now,
                )
                if (
                    not decision.authorized
                    or decision.rights_record_id is None
                    or decision.rights_record_version is None
                ):
                    continue
                input_hash = compute_product_fused_input_hash(
                    product_brief_id=product_brief_id,
                    content_sha256=source_version.sha256,
                    controlled_text_sha256=controlled.content_sha256,
                    provider=self._provider,
                    preprocessing_version=self._preprocessing_version,
                    model_configuration_version=self._model_configuration_version,
                    vector_kind=VectorKind.PRODUCT_FUSED,
                )
                record = EmbeddingRecord.create(
                    workspace_id=workspace_id,
                    asset_id=source_version.asset_id,
                    asset_version_id=source_version.id,
                    asset_version_number=source_version.version_number,
                    rights_record_id=decision.rights_record_id,
                    rights_record_version=decision.rights_record_version,
                    collection_id=collection.id,
                    embedding_spec_hash=embedding_spec_hash,
                    input_hash=input_hash,
                    vector_kind=VectorKind.PRODUCT_FUSED,
                    product_brief_version_id=product_brief_version_id,
                    controlled_text_sha256=controlled.content_sha256,
                    now=database_now,
                )
                document_id = str(uuid5(_SEARCH_DOCUMENT_NAMESPACE, record.id))
                existing_operation = self._load_existing(session, workspace_id, record.id)
                existing_document_id = session.scalar(
                    select(ProductSearchDocumentModel.id).where(
                        ProductSearchDocumentModel.workspace_id == workspace_id,
                        ProductSearchDocumentModel.id == document_id,
                    )
                )
                if existing_operation is not None or existing_document_id is not None:
                    if existing_operation is None or existing_document_id is None:
                        raise ValueError("PRODUCT_FUSED request facts are incomplete")
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
                        raise ValueError("existing PRODUCT_FUSED record disappeared")
                    existing_operation = self._load_existing(
                        session,
                        workspace_id,
                        record.id,
                    )
                    if existing_operation is None:
                        raise ValueError("existing PRODUCT_FUSED operation disappeared")
                    existing_document = session.scalar(
                        select(ProductSearchDocumentModel)
                        .where(
                            ProductSearchDocumentModel.workspace_id == workspace_id,
                            ProductSearchDocumentModel.id == existing_document_id,
                        )
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                    if existing_document is None:
                        raise ValueError("existing PRODUCT_FUSED document disappeared")
                    self._validate_existing_fused(
                        document=existing_document,
                        record=existing_record,
                        product_id=product.id,
                        product_brief_id=product_brief_id,
                        preprocessing_version=self._preprocessing_version,
                    )
                    self._validate_requested_fused_identity(
                        existing=existing_record,
                        requested=record,
                    )
                    provenance_changed = (
                        existing_record.product_brief_version_id != product_brief_version_id
                    )
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
                                now=database_now,
                            )
                        operation = self._new_operation(
                            workspace_id=workspace_id,
                            record_id=record.id,
                            vector_kind=VectorKind.PRODUCT_FUSED,
                            operation_epoch=existing_operation.target_version + 1,
                            embedding_input_hash=input_hash,
                            rights_record_id=decision.rights_record_id,
                            rights_record_version=decision.rights_record_version,
                            now=database_now,
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
                        existing_record.product_brief_version_id = product_brief_version_id
                        existing_record.version += 1
                        existing_record.updated_at = database_now
                        existing_document.rights_record_id = decision.rights_record_id
                        existing_document.rights_record_version = decision.rights_record_version
                        existing_document.retention_class = version.retention_class
                        existing_document.retention_deadline = version.retention_deadline
                        existing_document.product_brief_version_id = product_brief_version_id
                        existing_document.title = controlled.title
                        existing_document.labels = "\n".join(controlled.labels)
                        existing_document.ocr_summary = controlled.ocr_summary
                        existing_document.product_brief_summary = controlled.product_brief_summary
                        existing_document.approved_notes = "\n".join(controlled.notes)
                        existing_document.state = "PENDING"
                        existing_document.version += 1
                        existing_document.updated_at = database_now
                        self._enqueue_request(
                            session=session,
                            embedding=existing_record,
                            operation=operation,
                            now=database_now,
                        )
                        session.flush()
                        results.append(
                            ProductFusedIndexRequestResult(
                                operation=OperationExecutionRequest.from_operation(operation),
                                embedding_record_id=record.id,
                                search_document_id=document_id,
                                asset_version_id=source_version.id,
                                created=True,
                            )
                        )
                        continue
                    if provenance_changed:
                        existing_record.product_brief_version_id = product_brief_version_id
                        existing_record.version += 1
                        existing_record.updated_at = database_now
                        existing_document.product_brief_version_id = product_brief_version_id
                        existing_document.title = controlled.title
                        existing_document.labels = "\n".join(controlled.labels)
                        existing_document.ocr_summary = controlled.ocr_summary
                        existing_document.product_brief_summary = controlled.product_brief_summary
                        existing_document.approved_notes = "\n".join(controlled.notes)
                        existing_document.retention_class = version.retention_class
                        existing_document.retention_deadline = version.retention_deadline
                        existing_document.version += 1
                        existing_document.updated_at = database_now
                        session.flush()
                    results.append(
                        ProductFusedIndexRequestResult(
                            operation=OperationExecutionRequest.from_operation(existing_operation),
                            embedding_record_id=record.id,
                            search_document_id=document_id,
                            asset_version_id=source_version.id,
                            created=False,
                        )
                    )
                    continue

                self._stale_superseded_fused(
                    session=session,
                    record=record,
                    product_brief_id=product_brief_id,
                    now=database_now,
                )
                operation = self._new_operation(
                    workspace_id=workspace_id,
                    record_id=record.id,
                    vector_kind=VectorKind.PRODUCT_FUSED,
                    operation_epoch=1,
                    embedding_input_hash=input_hash,
                    rights_record_id=decision.rights_record_id,
                    rights_record_version=decision.rights_record_version,
                    now=database_now,
                )
                OperationRepository(session).add(operation)
                session.flush()
                embedding_model = self._embedding_model(record=record, operation=operation)
                session.add(embedding_model)
                session.flush()
                session.add(
                    ProductSearchDocumentModel(
                        id=document_id,
                        workspace_id=workspace_id,
                        product_id=product.id,
                        product_brief_id=product_brief_id,
                        product_brief_version_id=product_brief_version_id,
                        asset_id=record.asset_id,
                        asset_version_id=record.asset_version_id,
                        rights_record_id=record.rights_record_id,
                        rights_record_version=record.rights_record_version,
                        embedding_record_id=record.id,
                        input_hash=record.input_hash,
                        controlled_text_sha256=controlled.content_sha256,
                        preprocessing_version=self._preprocessing_version,
                        title=controlled.title,
                        labels="\n".join(controlled.labels),
                        ocr_summary=controlled.ocr_summary,
                        product_brief_summary=controlled.product_brief_summary,
                        approved_notes="\n".join(controlled.notes),
                        retention_class=version.retention_class,
                        retention_deadline=version.retention_deadline,
                        state="PENDING",
                        version=1,
                        created_at=database_now,
                        updated_at=database_now,
                    )
                )
                self._enqueue_request(
                    session=session,
                    embedding=embedding_model,
                    operation=operation,
                    now=database_now,
                )
                session.flush()
                results.append(
                    ProductFusedIndexRequestResult(
                        operation=OperationExecutionRequest.from_operation(operation),
                        embedding_record_id=record.id,
                        search_document_id=document_id,
                        asset_version_id=source_version.id,
                        created=True,
                    )
                )
            return tuple(results)

    @staticmethod
    def _approved_terms(
        fields: list[ProductBriefFieldModel],
        path: str,
    ) -> tuple[str, ...]:
        field = next((candidate for candidate in fields if candidate.path == path), None)
        if field is None or field.source != "HUMAN":
            return ()
        value = field.value_json
        if not isinstance(value, dict) or value.get("kind") != "TEXT_LIST":
            raise ValueError(f"{path} must be an approved text list")
        items = value.get("items")
        if not isinstance(items, list) or any(not isinstance(item, str) for item in items):
            raise ValueError(f"{path} must contain approved text items")
        return tuple(items)

    def _embedding_model(
        self,
        *,
        record: EmbeddingRecord,
        operation: DurableOperation,
    ) -> EmbeddingRecordModel:
        return EmbeddingRecordModel(
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
            product_brief_version_id=record.product_brief_version_id,
            controlled_text_sha256=record.controlled_text_sha256,
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

    @staticmethod
    def _validate_existing_fused(
        *,
        document: ProductSearchDocumentModel,
        record: EmbeddingRecord | EmbeddingRecordModel,
        product_id: str,
        product_brief_id: str,
        preprocessing_version: str,
    ) -> None:
        if (
            document.product_id != product_id
            or document.product_brief_id != product_brief_id
            or document.product_brief_version_id != record.product_brief_version_id
            or document.asset_id != record.asset_id
            or document.asset_version_id != record.asset_version_id
            or document.rights_record_id != record.rights_record_id
            or document.rights_record_version != record.rights_record_version
            or document.embedding_record_id != record.id
            or document.input_hash != record.input_hash
            or document.controlled_text_sha256 != record.controlled_text_sha256
            or document.preprocessing_version != preprocessing_version
        ):
            raise ValueError("existing PRODUCT_FUSED document identity conflicts with request")

    @staticmethod
    def _validate_requested_fused_identity(
        *,
        existing: EmbeddingRecordModel,
        requested: EmbeddingRecord,
    ) -> None:
        if (
            existing.asset_id != requested.asset_id
            or existing.asset_version_id != requested.asset_version_id
            or existing.asset_version_number != requested.asset_version_number
            or existing.collection_id != requested.collection_id
            or existing.vector_kind != requested.vector_kind.value
            or existing.embedding_spec_hash != requested.embedding_spec_hash
            or existing.input_hash != requested.input_hash
            or existing.controlled_text_sha256 != requested.controlled_text_sha256
        ):
            raise ValueError("existing PRODUCT_FUSED record identity conflicts with request")

    @staticmethod
    def _stale_superseded_fused(
        *,
        session: Session,
        record: EmbeddingRecord,
        product_brief_id: str,
        now: datetime,
    ) -> None:
        superseded = tuple(
            session.execute(
                select(EmbeddingRecordModel, ProductSearchDocumentModel)
                .join(
                    ProductSearchDocumentModel,
                    ProductSearchDocumentModel.embedding_record_id == EmbeddingRecordModel.id,
                )
                .where(
                    EmbeddingRecordModel.workspace_id == record.workspace_id,
                    EmbeddingRecordModel.asset_version_id == record.asset_version_id,
                    EmbeddingRecordModel.vector_kind == VectorKind.PRODUCT_FUSED.value,
                    EmbeddingRecordModel.embedding_spec_hash == record.embedding_spec_hash,
                    EmbeddingRecordModel.id != record.id,
                    ProductSearchDocumentModel.product_brief_id == product_brief_id,
                    EmbeddingRecordModel.state.not_in(
                        {
                            "STALE",
                            "DELETE_PENDING",
                            "DELETED",
                        }
                    ),
                )
                .order_by(EmbeddingRecordModel.id)
                .with_for_update()
            )
        )
        for embedding, document in superseded:
            needs_delete = embedding.write_generation > 0 and embedding.state in {
                "PROCESSING",
                "INDEXED",
                "RETRYABLE_FAILED",
            }
            if needs_delete:
                MySqlIndexRequestService._enqueue_superseded_delete(
                    session=session,
                    embedding=embedding,
                    now=now,
                )
                embedding.state = "DELETE_PENDING"
                document.state = "DELETE_PENDING"
            else:
                embedding.state = "STALE"
                document.state = "STALE"
            embedding.stale_at = now
            embedding.stale_reason = "SUPERSEDED_PRODUCT_BRIEF"
            embedding.version += 1
            embedding.updated_at = now
            document.version += 1
            document.updated_at = now

    def _new_operation(
        self,
        *,
        workspace_id: str,
        record_id: str,
        vector_kind: VectorKind,
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
            input_hash=compute_index_operation_hash(
                vector_kind=vector_kind.value,
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
            vector_kind=embedding.vector_kind,
            provider=self._provider,
            embedding_input_hash=embedding.input_hash,
            embedding_spec_sha256=embedding.embedding_spec_hash,
            product_brief_version_id=embedding.product_brief_version_id,
            controlled_text_sha256=embedding.controlled_text_sha256,
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
            raise ValueError("collection registry identity conflicts with configured vector spec")
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
            != compute_index_operation_hash(
                vector_kind=embedding.vector_kind,
                embedding_input_hash=embedding.input_hash,
                rights_record_id=embedding.rights_record_id,
                rights_record_version=embedding.rights_record_version,
                operation_epoch=operation_model.target_version,
            )
            or operation_model.input_ref != f"mysql://embedding-records/{embedding.id}"
        ):
            raise ValueError("existing index operation identity conflicts with embedding facts")
        return operation_from_model(operation_model)


# Compatibility names keep Ticket 09 callers stable while both vector kinds share one deep module.
MySqlImageIndexRequestService = MySqlIndexRequestService
MySqlProductFusedIndexRequestService = MySqlIndexRequestService
