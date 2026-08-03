"""MySQL-authoritative convergence for generation-fenced Asset deletion."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from commercevision_application.asset_deletion import (
    AssetDeletionConvergenceResult,
    asset_deletion_input_hash,
)
from commercevision_application.operations import OperationExecutionRequest
from commercevision_contracts import MilvusVectorIdentityV1
from commercevision_contracts.events import AssetDeleteCompletedPayload, EventType
from commercevision_contracts.object_storage import (
    ConditionalDeleteRequest,
    ObjectReference,
    ObjectStorage,
)
from commercevision_contracts.product_briefs import PreparedProviderArtifact
from commercevision_domain import (
    Asset,
    AssetObjectState,
    AssetState,
    RetentionClass,
    StorageLocationClass,
    StoragePreconditionError,
    new_uuid7,
)
from commercevision_domain.messaging import EventEnvelope, OutboxEvent
from sqlalchemy import func, literal_column, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from .asset_provider_artifact_deletion import (
    ProviderArtifactDeletionConverger,
    ProviderArtifactDeletionStore,
    ProviderArtifactDeletionTarget,
)
from .asset_task_payload_cleanup import (
    AssetTaskPayloadScope,
    converge_task_payloads,
    task_payloads_are_converged,
)
from .assets import _asset_from_model
from .indexing_models import (
    CollectionRegistryModel,
    EmbeddingRecordModel,
    ProductSearchDocumentModel,
)
from .models import AssetModel, AssetObjectModel, AssetVersionModel
from .product_brief_models import (
    ProductBriefModel,
    ProductBriefProviderArtifactModel,
)
from .repositories import OutboxRepository
from .retention_models import (
    AssetDeletionProgressModel,
    AssetDeletionTombstoneModel,
    ProviderArtifactDeletionProgressModel,
)


@dataclass(frozen=True, slots=True)
class _ObjectTarget:
    id: str
    backend: str
    bucket: str
    reference: ObjectReference
    etag: str


@dataclass(frozen=True, slots=True)
class _VectorTarget:
    id: str
    identity: MilvusVectorIdentityV1


@dataclass(frozen=True, slots=True)
class _DeletionWork:
    tombstone_id: str
    workspace_id: str
    asset_id: str
    asset_version_id: str
    deletion_generation: int
    retention_class: RetentionClass
    workflow_id: str | None
    objects: tuple[_ObjectTarget, ...]
    vectors: tuple[_VectorTarget, ...]
    provider_artifacts: tuple[ProviderArtifactDeletionTarget, ...]
    search_document_count: int
    quarantine_count: int
    already_completed: bool = False


class MySqlAssetDeletionCoordinator:
    """Keep MySQL unusable while exact external identities converge."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        storage: ObjectStorage,
        vectors: object,
        provider_artifacts: ProviderArtifactDeletionStore,
        version_page_size: int,
        max_version_pages: int,
        max_versions: int,
        stable_empty_passes: int,
    ) -> None:
        self._session_factory = session_factory
        self._storage = storage
        self._vectors = vectors
        self._provider_deletions = ProviderArtifactDeletionConverger(
            store=provider_artifacts,
            version_page_size=version_page_size,
            max_version_pages=max_version_pages,
            max_versions=max_versions,
            stable_empty_passes=stable_empty_passes,
        )

    def converge(
        self,
        request: OperationExecutionRequest,
    ) -> AssetDeletionConvergenceResult:
        try:
            return self._converge(request)
        except DBAPIError as exc:
            raise ConnectionError("Asset deletion MySQL dependency is unavailable") from exc

    def _converge(
        self,
        request: OperationExecutionRequest,
    ) -> AssetDeletionConvergenceResult:
        work = self._prepare(request)
        try:
            self._delete_objects(work)
        except Exception:
            self._record_retryable_failure(
                work,
                component="OBJECTS",
                observed_count=len(work.objects),
                error_code="OBJECT_DELETE_RETRYABLE",
            )
            raise
        try:
            self._delete_vectors(work)
        except Exception:
            self._record_retryable_failure(
                work,
                component="VECTORS",
                observed_count=len(work.vectors),
                error_code="VECTOR_DELETE_RETRYABLE",
            )
            raise
        try:
            self._delete_provider_artifacts(work)
        except Exception:
            self._record_retryable_failure(
                work,
                component="PROVIDER_ARTIFACTS",
                observed_count=len(work.provider_artifacts),
                error_code="PROVIDER_ARTIFACT_DELETE_RETRYABLE",
            )
            raise
        try:
            self._complete(request, work)
        except Exception:
            self._record_retryable_failure(
                work,
                component="OPERATIONS",
                observed_count=1,
                error_code="MYSQL_COMPLETION_RETRYABLE",
            )
            raise
        return AssetDeletionConvergenceResult(
            output_ref=(f"mysql://assets/{work.asset_id}/deletions/{work.deletion_generation}")
        )

    def _prepare(self, request: OperationExecutionRequest) -> _DeletionWork:
        with self._session_factory.begin() as session:
            row = session.execute(
                select(
                    AssetModel,
                    AssetDeletionTombstoneModel,
                    literal_column("UTC_TIMESTAMP(6)").label("database_now"),
                )
                .join(
                    AssetDeletionTombstoneModel,
                    AssetDeletionTombstoneModel.operation_id == AssetModel.deletion_operation_id,
                )
                .where(
                    AssetModel.workspace_id == request.workspace_id,
                    AssetModel.id == request.target_id,
                    AssetDeletionTombstoneModel.workspace_id == request.workspace_id,
                    AssetDeletionTombstoneModel.operation_id == request.operation_id,
                )
                .with_for_update()
            ).one_or_none()
            if row is None:
                raise ValueError("Asset deletion tombstone is unavailable")
            asset_model, tombstone, database_now = row
            asset = _asset_from_model(asset_model)
            self._assert_identity(request, asset=asset, tombstone=tombstone)
            if database_now.tzinfo is None:
                database_now = database_now.replace(tzinfo=UTC)
            if asset.status == AssetState.DELETED:
                return _DeletionWork(
                    tombstone_id=tombstone.id,
                    workspace_id=request.workspace_id,
                    asset_id=request.target_id,
                    asset_version_id=tombstone.target_asset_version_id,
                    deletion_generation=tombstone.deletion_generation,
                    retention_class=asset.retention_class,
                    workflow_id=asset.workflow_id,
                    objects=(),
                    vectors=(),
                    provider_artifacts=(),
                    search_document_count=0,
                    quarantine_count=0,
                    already_completed=True,
                )

            version_ids = select(AssetVersionModel.id).where(
                AssetVersionModel.workspace_id == request.workspace_id,
                AssetVersionModel.asset_id == request.target_id,
            )
            object_models = tuple(
                session.scalars(
                    select(AssetObjectModel)
                    .where(
                        AssetObjectModel.workspace_id == request.workspace_id,
                        AssetObjectModel.asset_version_id.in_(version_ids),
                        AssetObjectModel.state != AssetObjectState.DELETED.value,
                    )
                    .order_by(AssetObjectModel.created_at, AssetObjectModel.id)
                    .with_for_update()
                )
            )
            for model in object_models:
                if model.state != AssetObjectState.DELETE_PENDING.value:
                    model.state = AssetObjectState.DELETE_PENDING.value
                    model.version += 1
                    model.updated_at = database_now

            vector_rows = tuple(
                session.execute(
                    select(EmbeddingRecordModel, CollectionRegistryModel.physical_name)
                    .join(
                        CollectionRegistryModel,
                        CollectionRegistryModel.id == EmbeddingRecordModel.collection_id,
                    )
                    .where(
                        EmbeddingRecordModel.workspace_id == request.workspace_id,
                        EmbeddingRecordModel.asset_id == request.target_id,
                        EmbeddingRecordModel.state != "DELETED",
                    )
                    .order_by(EmbeddingRecordModel.created_at, EmbeddingRecordModel.id)
                    .with_for_update()
                )
            )
            for model, _ in vector_rows:
                if model.state != "DELETE_PENDING":
                    model.state = "DELETE_PENDING"
                    model.stale_at = model.stale_at or database_now
                    model.stale_reason = "ASSET_DELETED"
                    model.version += 1
                    model.updated_at = database_now

            search_document_count = session.scalar(
                select(func.count())
                .select_from(ProductSearchDocumentModel)
                .where(
                    ProductSearchDocumentModel.workspace_id == request.workspace_id,
                    ProductSearchDocumentModel.asset_id == request.target_id,
                    ProductSearchDocumentModel.state != "DELETED",
                )
            )
            session.execute(
                update(ProductSearchDocumentModel)
                .where(
                    ProductSearchDocumentModel.workspace_id == request.workspace_id,
                    ProductSearchDocumentModel.asset_id == request.target_id,
                    ProductSearchDocumentModel.state != "DELETED",
                )
                .values(
                    state="DELETE_PENDING",
                    version=ProductSearchDocumentModel.version + 1,
                    updated_at=database_now,
                )
            )
            provider_models = self._provider_models(
                session,
                workspace_id=request.workspace_id,
                workflow_id=asset.workflow_id,
                retention_class=asset.retention_class,
            )
            self._append_progress(
                session,
                tombstone_id=tombstone.id,
                workspace_id=request.workspace_id,
                component="OPERATIONS",
                state="PENDING",
                observed_count=(len(object_models) + len(vector_rows) + len(provider_models)),
                converged_count=0,
                now=database_now,
            )
            return _DeletionWork(
                tombstone_id=tombstone.id,
                workspace_id=request.workspace_id,
                asset_id=request.target_id,
                asset_version_id=tombstone.target_asset_version_id,
                deletion_generation=tombstone.deletion_generation,
                retention_class=asset.retention_class,
                workflow_id=asset.workflow_id,
                objects=tuple(self._object_target(model) for model in object_models),
                vectors=tuple(
                    self._vector_target(model, physical_name)
                    for model, physical_name in vector_rows
                ),
                provider_artifacts=tuple(self._provider_target(model) for model in provider_models),
                search_document_count=int(search_document_count or 0),
                quarantine_count=sum(
                    1 for model in object_models if model.location == "QUARANTINE"
                ),
            )

    def _complete(
        self,
        request: OperationExecutionRequest,
        work: _DeletionWork,
    ) -> None:
        if work.already_completed:
            return
        with self._session_factory.begin() as session:
            row = session.execute(
                select(
                    AssetModel,
                    AssetDeletionTombstoneModel,
                    literal_column("UTC_TIMESTAMP(6)").label("database_now"),
                )
                .join(
                    AssetDeletionTombstoneModel,
                    AssetDeletionTombstoneModel.operation_id == AssetModel.deletion_operation_id,
                )
                .where(
                    AssetModel.workspace_id == request.workspace_id,
                    AssetModel.id == request.target_id,
                    AssetDeletionTombstoneModel.id == work.tombstone_id,
                )
                .with_for_update()
            ).one_or_none()
            if row is None:
                raise ValueError("Asset deletion identity disappeared before completion")
            asset_model, tombstone, database_now = row
            asset = _asset_from_model(asset_model)
            self._assert_identity(request, asset=asset, tombstone=tombstone)
            if database_now.tzinfo is None:
                database_now = database_now.replace(tzinfo=UTC)

            object_ids = [target.id for target in work.objects]
            if object_ids:
                session.execute(
                    update(AssetObjectModel)
                    .where(
                        AssetObjectModel.workspace_id == work.workspace_id,
                        AssetObjectModel.id.in_(object_ids),
                        AssetObjectModel.state == AssetObjectState.DELETE_PENDING.value,
                    )
                    .values(
                        state=AssetObjectState.DELETED.value,
                        version=AssetObjectModel.version + 1,
                        updated_at=database_now,
                    )
                )
            vector_ids = [target.id for target in work.vectors]
            if vector_ids:
                session.execute(
                    update(EmbeddingRecordModel)
                    .where(
                        EmbeddingRecordModel.workspace_id == work.workspace_id,
                        EmbeddingRecordModel.id.in_(vector_ids),
                        EmbeddingRecordModel.state == "DELETE_PENDING",
                    )
                    .values(
                        state="DELETED",
                        version=EmbeddingRecordModel.version + 1,
                        updated_at=database_now,
                    )
                )
            session.execute(
                update(ProductSearchDocumentModel)
                .where(
                    ProductSearchDocumentModel.workspace_id == work.workspace_id,
                    ProductSearchDocumentModel.asset_id == work.asset_id,
                    ProductSearchDocumentModel.state == "DELETE_PENDING",
                )
                .values(
                    state="DELETED",
                    title="",
                    labels="",
                    ocr_summary="",
                    product_brief_summary="",
                    approved_notes="",
                    version=ProductSearchDocumentModel.version + 1,
                    updated_at=database_now,
                )
            )
            payload_scope = AssetTaskPayloadScope(
                workspace_id=work.workspace_id,
                asset_id=work.asset_id,
                retention_class=work.retention_class,
                workflow_id=work.workflow_id,
            )
            payload_counts = converge_task_payloads(session, scope=payload_scope)

            remaining_objects = session.scalar(
                select(func.count())
                .select_from(AssetObjectModel)
                .join(
                    AssetVersionModel,
                    AssetVersionModel.id == AssetObjectModel.asset_version_id,
                )
                .where(
                    AssetVersionModel.workspace_id == work.workspace_id,
                    AssetVersionModel.asset_id == work.asset_id,
                    AssetObjectModel.state != AssetObjectState.DELETED.value,
                )
            )
            remaining_vectors = session.scalar(
                select(func.count())
                .select_from(EmbeddingRecordModel)
                .where(
                    EmbeddingRecordModel.workspace_id == work.workspace_id,
                    EmbeddingRecordModel.asset_id == work.asset_id,
                    EmbeddingRecordModel.state != "DELETED",
                )
            )
            remaining_search_documents = session.scalar(
                select(func.count())
                .select_from(ProductSearchDocumentModel)
                .where(
                    ProductSearchDocumentModel.workspace_id == work.workspace_id,
                    ProductSearchDocumentModel.asset_id == work.asset_id,
                    ProductSearchDocumentModel.state != "DELETED",
                )
            )
            task_payloads_converged = task_payloads_are_converged(
                session,
                scope=payload_scope,
            )
            unconverged_provider_artifacts = self._unconverged_provider_artifacts(
                session,
                work=work,
            )
            if any(
                (
                    remaining_objects,
                    remaining_vectors,
                    remaining_search_documents,
                    not task_payloads_converged,
                    unconverged_provider_artifacts,
                )
            ):
                raise TimeoutError("Asset deletion discovered late cleanup facts")

            asset.complete_deletion(
                deletion_generation=work.deletion_generation,
                target_asset_version_id=work.asset_version_id,
                now=database_now,
            )
            asset_model.status = asset.status.value
            asset_model.deletion_completed_at = asset.deletion_completed_at
            asset_model.version = asset.version
            asset_model.updated_at = asset.updated_at
            for component, count in (
                ("OBJECTS", len(work.objects)),
                ("VECTORS", len(work.vectors)),
                ("SEARCH_DOCUMENTS", work.search_document_count),
                ("PROVIDER_ARTIFACTS", len(work.provider_artifacts)),
                ("TEMPORARY_REFERENCES", payload_counts.temporary_references),
                ("CACHES", 0),
                ("PRODUCT_BRIEFS", payload_counts.product_brief_payloads),
                ("RETRIEVAL_RUNS", payload_counts.retrieval_runs),
                ("CHECKPOINTS", payload_counts.checkpoints),
                ("QUARANTINE", work.quarantine_count),
                ("OPERATIONS", 1),
            ):
                self._append_progress(
                    session,
                    tombstone_id=work.tombstone_id,
                    workspace_id=work.workspace_id,
                    component=component,
                    state="CONVERGED",
                    observed_count=count,
                    converged_count=count,
                    now=database_now,
                )
            payload = AssetDeleteCompletedPayload(
                workspace_id=work.workspace_id,
                asset_id=work.asset_id,
                asset_version_id=work.asset_version_id,
                retention_class=work.retention_class,
                deletion_generation=work.deletion_generation,
            )
            OutboxRepository(session).add(
                OutboxEvent(
                    envelope=EventEnvelope.create(
                        event_type=EventType.ASSET_DELETE_COMPLETED.value,
                        aggregate_type="Asset",
                        aggregate_id=work.asset_id,
                        aggregate_version=asset.version,
                        trace_id=f"asset-deletion:{request.operation_id}",
                        payload=payload.model_dump(mode="json"),
                        now=database_now,
                    ),
                    available_at=database_now,
                    workspace_id=work.workspace_id,
                )
            )

    def _delete_objects(self, work: _DeletionWork) -> None:
        configured_bucket = getattr(self._storage, "configured_bucket", None)
        for target in work.objects:
            if target.backend != self._storage.backend.value:
                raise StoragePreconditionError("Asset object storage backend is not registered")
            if (
                not callable(configured_bucket)
                or configured_bucket(target.reference.location) != target.bucket
            ):
                raise StoragePreconditionError("Asset object storage bucket is not registered")
            self._storage.delete_if_match(
                ConditionalDeleteRequest(
                    reference=target.reference,
                    expected_etag=target.etag,
                )
            )

    def _delete_vectors(self, work: _DeletionWork) -> None:
        delete_if_generation = getattr(self._vectors, "delete_if_generation", None)
        if not callable(delete_if_generation):
            raise ConnectionError("Milvus deletion adapter is unavailable")
        for target in work.vectors:
            delete_if_generation(target.identity)

    def _delete_provider_artifacts(self, work: _DeletionWork) -> None:
        for artifact in work.provider_artifacts:
            self._provider_deletions.converge(artifact)
            with self._session_factory.begin() as session:
                session.add(
                    ProviderArtifactDeletionProgressModel(
                        id=new_uuid7(),
                        workspace_id=work.workspace_id,
                        provider_artifact_id=artifact.id,
                        tombstone_id=work.tombstone_id,
                        state="CONVERGED",
                        provider_version_id=artifact.provider_version_id,
                        error_code=None,
                        created_at=self._database_now(session),
                    )
                )

    @staticmethod
    def _assert_identity(
        request: OperationExecutionRequest,
        *,
        asset: Asset,
        tombstone: AssetDeletionTombstoneModel,
    ) -> None:
        if (
            asset.deletion_operation_id != request.operation_id
            or asset.deletion_generation != request.target_version
            or tombstone.deletion_generation != request.target_version
            or tombstone.target_asset_version_id != asset.current_version_id
            or tombstone.asset_id != request.target_id
            or asset.status
            not in {
                AssetState.DELETING,
                AssetState.RIGHTS_EXPIRED,
                AssetState.DELETED,
            }
            or asset.deletion_reason is None
            or asset_deletion_input_hash(
                asset,
                reason=asset.deletion_reason,
                deletion_generation=request.target_version,
            )
            != request.input_hash
        ):
            raise ValueError("Asset deletion generation or version fence failed")

    @staticmethod
    def _object_target(model: AssetObjectModel) -> _ObjectTarget:
        return _ObjectTarget(
            id=model.id,
            backend=model.backend,
            bucket=model.bucket,
            reference=ObjectReference(
                location=StorageLocationClass(model.location),
                key=model.key,
                version_id=model.provider_version_id,
            ),
            etag=model.etag,
        )

    @staticmethod
    def _vector_target(
        model: EmbeddingRecordModel,
        physical_name: str,
    ) -> _VectorTarget:
        return _VectorTarget(
            id=model.id,
            identity=MilvusVectorIdentityV1(
                collection_name=physical_name,
                embedding_record_id=model.id,
                milvus_primary_key=model.milvus_primary_key,
                input_hash=model.input_hash,
                embedding_spec_sha256=model.embedding_spec_hash,
                write_generation=model.write_generation,
            ),
        )

    @staticmethod
    def _provider_target(
        model: ProductBriefProviderArtifactModel,
    ) -> ProviderArtifactDeletionTarget:
        return ProviderArtifactDeletionTarget(
            id=model.id,
            state=model.state,
            target=PreparedProviderArtifact(
                ledger_id=model.id,
                key_schema_version=model.key_schema_version,
                storage_backend=model.storage_backend,
                location=StorageLocationClass(model.location),
                bucket=model.bucket,
                key=model.object_key,
                target_sha256=model.target_sha256,
                content_type=model.content_type,
                expected_sha256=model.expected_sha256,
                expected_byte_size=model.expected_byte_size,
                retention_class=RetentionClass(model.retention_class),
                retention_deadline=model.retention_deadline,
                write_fence=model.write_fence,
            ),
            provider_version_id=model.provider_version_id,
            etag=model.etag,
        )

    @staticmethod
    def _provider_models(
        session: Session,
        *,
        workspace_id: str,
        workflow_id: str | None,
        retention_class: RetentionClass,
    ) -> tuple[ProductBriefProviderArtifactModel, ...]:
        if retention_class != RetentionClass.TASK or workflow_id is None:
            return ()
        return tuple(
            session.scalars(
                select(ProductBriefProviderArtifactModel)
                .join(
                    ProductBriefModel,
                    ProductBriefModel.id == ProductBriefProviderArtifactModel.product_brief_id,
                )
                .where(
                    ProductBriefProviderArtifactModel.workspace_id == workspace_id,
                    ProductBriefProviderArtifactModel.retention_class == "TASK",
                    ProductBriefModel.workflow_id == workflow_id,
                )
                .order_by(
                    ProductBriefProviderArtifactModel.created_at,
                    ProductBriefProviderArtifactModel.id,
                )
            )
        )

    @staticmethod
    def _unconverged_provider_artifacts(session: Session, *, work: _DeletionWork) -> int:
        if work.retention_class != RetentionClass.TASK or work.workflow_id is None:
            return 0
        total = session.scalar(
            select(func.count())
            .select_from(ProductBriefProviderArtifactModel)
            .join(
                ProductBriefModel,
                ProductBriefModel.id == ProductBriefProviderArtifactModel.product_brief_id,
            )
            .where(
                ProductBriefProviderArtifactModel.workspace_id == work.workspace_id,
                ProductBriefProviderArtifactModel.retention_class == RetentionClass.TASK.value,
                ProductBriefModel.workflow_id == work.workflow_id,
            )
        )
        converged = session.scalar(
            select(
                func.count(
                    func.distinct(ProviderArtifactDeletionProgressModel.provider_artifact_id)
                )
            )
            .select_from(ProviderArtifactDeletionProgressModel)
            .join(
                ProductBriefProviderArtifactModel,
                ProductBriefProviderArtifactModel.id
                == ProviderArtifactDeletionProgressModel.provider_artifact_id,
            )
            .join(
                ProductBriefModel,
                ProductBriefModel.id == ProductBriefProviderArtifactModel.product_brief_id,
            )
            .where(
                ProviderArtifactDeletionProgressModel.workspace_id == work.workspace_id,
                ProviderArtifactDeletionProgressModel.tombstone_id == work.tombstone_id,
                ProviderArtifactDeletionProgressModel.state == "CONVERGED",
                ProductBriefModel.workflow_id == work.workflow_id,
            )
        )
        return max(0, int(total or 0) - int(converged or 0))

    @staticmethod
    def _append_progress(
        session: Session,
        *,
        tombstone_id: str,
        workspace_id: str,
        component: str,
        state: str,
        observed_count: int,
        converged_count: int,
        now: datetime,
        error_code: str | None = None,
    ) -> None:
        session.add(
            AssetDeletionProgressModel(
                id=new_uuid7(),
                workspace_id=workspace_id,
                tombstone_id=tombstone_id,
                component=component,
                state=state,
                cursor_value=None,
                observed_count=observed_count,
                converged_count=converged_count,
                error_code=error_code,
                created_at=now,
            )
        )

    def _record_retryable_failure(
        self,
        work: _DeletionWork,
        *,
        component: str,
        observed_count: int,
        error_code: str,
    ) -> None:
        if work.already_completed:
            return
        try:
            with self._session_factory.begin() as session:
                self._append_progress(
                    session,
                    tombstone_id=work.tombstone_id,
                    workspace_id=work.workspace_id,
                    component=component,
                    state="RETRYABLE_FAILED",
                    observed_count=observed_count,
                    converged_count=0,
                    now=self._database_now(session),
                    error_code=error_code,
                )
        except Exception:
            # Progress evidence is secondary to preserving the original retryable failure.
            return

    @staticmethod
    def _database_now(session: Session) -> datetime:
        value = session.scalar(select(literal_column("UTC_TIMESTAMP(6)")))
        if value is None:
            raise ConnectionError("MySQL database time is unavailable")
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value
