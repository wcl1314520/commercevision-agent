"""MySQL repository and unit of work for ProductBrief analysis."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import TracebackType

from commercevision_application.product_brief_ports import (
    ProductBriefAnalysisRecord,
    ProductBriefConfirmation,
    ProductBriefSourceAsset,
    StoredProductBriefProviderSummary,
    StoredProductBriefVersion,
    StoredProductBriefVersionPage,
    StoredProductBriefVersionSummary,
    StoredProviderArtifact,
    StoredProviderAttempt,
    StoredProviderCall,
)
from commercevision_contracts.config import (
    PROVIDER_ARTIFACT_READINESS_TARGET_LIMIT,
    WORKER_READINESS_MYSQL_QUERY_TIMEOUT_SECONDS,
)
from commercevision_contracts.product_briefs import (
    ProviderArtifactKind,
    ProviderArtifactPhysicalTarget,
    ProviderArtifactReference,
    ProviderArtifactState,
    VisionProviderStatus,
)
from commercevision_domain import (
    ConcurrencyError,
    ProductBrief,
    ProductBriefCategory,
    ProductBriefEvidence,
    ProductBriefEvidenceKind,
    ProductBriefField,
    ProductBriefFieldConflict,
    ProductBriefFieldSource,
    ProductBriefRetentionExpiredError,
    ProductBriefState,
    ProductBriefVersion,
    ProductBriefVersionSource,
    RetentionClass,
    StorageLocationClass,
)
from sqlalchemy import and_, func, literal_column, or_, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from .assets import AssetRepository
from .catalog import ProductRepository
from .database import enter_unit_of_work, exit_unit_of_work
from .integrity import (
    classify_database_error,
    execute_with_integrity_classification,
    flush_with_integrity_classification,
)
from .operations import OperationRepository
from .product_brief_models import (
    ProductBriefAnalysisRequestModel,
    ProductBriefConfirmationModel,
    ProductBriefEvidenceModel,
    ProductBriefFieldModel,
    ProductBriefModel,
    ProductBriefProviderArtifactModel,
    ProductBriefProviderAttemptModel,
    ProductBriefProviderCallModel,
    ProductBriefSourceAssetModel,
    ProductBriefVersionModel,
)
from .repositories import (
    ApprovalRepository,
    AuditRepository,
    IdempotencyRepository,
    OutboxRepository,
    WorkflowRepository,
)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _brief_from_model(model: ProductBriefModel) -> ProductBrief:
    return ProductBrief(
        id=model.id,
        workspace_id=model.workspace_id,
        workflow_id=model.workflow_id,
        product_id=model.product_id,
        created_by=model.created_by,
        state=ProductBriefState(model.state),
        current_version_id=model.current_version_id,
        confirmed_version_id=model.confirmed_version_id,
        version=model.version,
        retention_class=RetentionClass(model.retention_class),
        retention_deadline=model.retention_deadline,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _provider_call_from_model(model: ProductBriefProviderCallModel) -> StoredProviderCall:
    retention_class = RetentionClass(model.retention_class)
    request_artifact = ProviderArtifactReference(
        storage_backend=model.request_artifact_storage_backend,
        location=model.request_artifact_location,
        bucket=model.request_artifact_bucket,
        key=model.request_artifact_key,
        provider_version_id=model.request_artifact_provider_version_id,
        etag=model.request_artifact_etag,
        sha256=model.request_artifact_sha256,
        byte_size=model.request_artifact_byte_size,
        retention_class=retention_class,
        retention_deadline=model.retention_deadline,
    )
    response_artifact = (
        None
        if model.response_artifact_storage_backend is None
        else ProviderArtifactReference(
            storage_backend=model.response_artifact_storage_backend,
            location=model.response_artifact_location,
            bucket=model.response_artifact_bucket,
            key=model.response_artifact_key,
            provider_version_id=model.response_artifact_provider_version_id,
            etag=model.response_artifact_etag,
            sha256=model.response_artifact_sha256,
            byte_size=model.response_artifact_byte_size,
            retention_class=retention_class,
            retention_deadline=model.retention_deadline,
        )
    )
    return StoredProviderCall(
        id=model.id,
        workspace_id=model.workspace_id,
        product_brief_id=model.product_brief_id,
        operation_id=model.operation_id,
        operation_attempt=model.operation_attempt,
        call_index=model.call_index,
        status=VisionProviderStatus(model.status),
        provider=model.provider,
        endpoint_region=model.endpoint_region,
        endpoint_host=model.endpoint_host,
        requested_model=model.requested_model,
        submitted_model_snapshot=model.submitted_model_snapshot,
        resolved_model=model.resolved_model,
        prompt_version=model.prompt_version,
        config_snapshot_sha256=model.config_snapshot_sha256,
        request_id=model.request_id,
        input_tokens=model.input_tokens,
        output_tokens=model.output_tokens,
        total_tokens=model.total_tokens,
        latency_ms=model.latency_ms,
        request_artifact=request_artifact,
        response_artifact=response_artifact,
        error_code=model.error_code,
        error_category=model.error_category,
        error_retryable=model.error_retryable,
        retention_class=retention_class,
        retention_deadline=model.retention_deadline,
        created_at=model.created_at,
        request_artifact_id=model.request_artifact_id,
        response_artifact_id=model.response_artifact_id,
    )


def _provider_attempt_from_model(
    model: ProductBriefProviderAttemptModel,
) -> StoredProviderAttempt:
    return StoredProviderAttempt(
        id=model.id,
        workspace_id=model.workspace_id,
        product_brief_id=model.product_brief_id,
        operation_id=model.operation_id,
        operation_attempt=model.operation_attempt,
        call_index=model.call_index,
        submission_key_sha256=model.submission_key_sha256,
        input_sha256=model.input_sha256,
        provider=model.provider,
        endpoint_region=model.endpoint_region,
        endpoint_host=model.endpoint_host,
        requested_model=model.requested_model,
        submitted_model_snapshot=model.submitted_model_snapshot,
        prompt_version=model.prompt_version,
        config_snapshot_sha256=model.config_snapshot_sha256,
        retention_class=RetentionClass(model.retention_class),
        retention_deadline=model.retention_deadline,
        created_at=model.created_at,
    )


def _provider_artifact_from_model(
    model: ProductBriefProviderArtifactModel,
) -> StoredProviderArtifact:
    return StoredProviderArtifact(
        id=model.id,
        workspace_id=model.workspace_id,
        product_brief_id=model.product_brief_id,
        operation_id=model.operation_id,
        operation_attempt=model.operation_attempt,
        call_index=model.call_index,
        kind=ProviderArtifactKind(model.kind),
        state=ProviderArtifactState(model.state),
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
        provider_version_id=model.provider_version_id,
        etag=model.etag,
        unknown_reason=model.unknown_reason,
        version=model.version,
        stored_at=model.stored_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


class SqlAlchemyProviderArtifactTargetReadinessQuery:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def list_reconciliation_targets(
        self,
        *,
        limit: int,
    ) -> tuple[ProviderArtifactPhysicalTarget, ...]:
        if not 1 <= limit <= PROVIDER_ARTIFACT_READINESS_TARGET_LIMIT:
            raise ValueError(
                "provider artifact target readiness limit must be between "
                f"1 and {PROVIDER_ARTIFACT_READINESS_TARGET_LIMIT}"
            )
        statement = (
            select(
                ProductBriefProviderArtifactModel.storage_backend,
                ProductBriefProviderArtifactModel.location,
                ProductBriefProviderArtifactModel.bucket,
            )
            .distinct()
            .where(
                ProductBriefProviderArtifactModel.state.in_(
                    (
                        ProviderArtifactState.INTENDED.value,
                        ProviderArtifactState.UNKNOWN.value,
                    )
                )
            )
            .prefix_with(
                "/*+ MAX_EXECUTION_TIME("
                f"{round(WORKER_READINESS_MYSQL_QUERY_TIMEOUT_SECONDS * 1000)}"
                ") */",
                dialect="mysql",
            )
            .order_by(
                ProductBriefProviderArtifactModel.storage_backend,
                ProductBriefProviderArtifactModel.location,
                ProductBriefProviderArtifactModel.bucket,
            )
            .limit(limit + 1)
        )
        with self._session_factory() as session:
            rows = session.execute(statement).mappings().all()
        if len(rows) > limit:
            raise RuntimeError(
                "provider artifact reconciliation targets exceed the readiness bound"
            )
        return tuple(
            ProviderArtifactPhysicalTarget(
                storage_backend=row["storage_backend"],
                location=row["location"],
                bucket=row["bucket"],
            )
            for row in rows
        )


class ProductBriefRepository:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._loaded_versions: dict[str, int] = {}

    def add(self, product_brief: ProductBrief, *, operation_id: str) -> None:
        self._session.add(
            ProductBriefModel(
                id=product_brief.id,
                workspace_id=product_brief.workspace_id,
                workflow_id=product_brief.workflow_id,
                product_id=product_brief.product_id,
                operation_id=operation_id,
                created_by=product_brief.created_by,
                state=product_brief.state.value,
                current_version_id=product_brief.current_version_id,
                confirmed_version_id=product_brief.confirmed_version_id,
                version=product_brief.version,
                retention_class=product_brief.retention_class.value,
                retention_deadline=product_brief.retention_deadline,
                created_at=product_brief.created_at,
                updated_at=product_brief.updated_at,
            )
        )
        self._loaded_versions[product_brief.id] = product_brief.version

    def get(
        self,
        *,
        workspace_id: str,
        product_brief_id: str,
        for_update: bool = False,
    ) -> ProductBrief | None:
        statement = select(ProductBriefModel).where(
            ProductBriefModel.workspace_id == workspace_id,
            ProductBriefModel.id == product_brief_id,
        )
        return self._get_one(statement, for_update=for_update)

    def get_by_workflow_product(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        product_id: str,
        for_update: bool = False,
    ) -> ProductBrief | None:
        statement = select(ProductBriefModel).where(
            ProductBriefModel.workspace_id == workspace_id,
            ProductBriefModel.workflow_id == workflow_id,
            ProductBriefModel.product_id == product_id,
        )
        return self._get_one(statement, for_update=for_update)

    def get_by_operation(
        self,
        *,
        workspace_id: str,
        operation_id: str,
        for_update: bool = False,
    ) -> ProductBrief | None:
        statement = select(ProductBriefModel).where(
            ProductBriefModel.workspace_id == workspace_id,
            ProductBriefModel.operation_id == operation_id,
        )
        return self._get_one(statement, for_update=for_update)

    def operation_id(
        self,
        *,
        workspace_id: str,
        product_brief_id: str,
    ) -> str | None:
        return self._session.scalar(
            select(ProductBriefModel.operation_id).where(
                ProductBriefModel.workspace_id == workspace_id,
                ProductBriefModel.id == product_brief_id,
            )
        )

    def analysis_trace_id(
        self,
        *,
        workspace_id: str,
        product_brief_id: str,
    ) -> str | None:
        return self._session.scalar(
            select(ProductBriefAnalysisRequestModel.trace_id)
            .select_from(ProductBriefAnalysisRequestModel)
            .join(
                ProductBriefModel,
                and_(
                    ProductBriefModel.workspace_id == ProductBriefAnalysisRequestModel.workspace_id,
                    ProductBriefModel.id == ProductBriefAnalysisRequestModel.product_brief_id,
                    ProductBriefModel.operation_id == ProductBriefAnalysisRequestModel.operation_id,
                ),
            )
            .where(
                ProductBriefModel.workspace_id == workspace_id,
                ProductBriefModel.id == product_brief_id,
            )
        )

    def _get_one(self, statement, *, for_update: bool) -> ProductBrief | None:
        if for_update:
            statement = statement.with_for_update()
        model = self._session.scalar(statement)
        if model is None:
            return None
        self._loaded_versions[model.id] = model.version
        return _brief_from_model(model)

    def save(
        self,
        product_brief: ProductBrief,
        *,
        operation_id: str | None = None,
    ) -> None:
        original_version = self._loaded_versions.get(product_brief.id)
        if original_version is None:
            raise ConcurrencyError(
                f"ProductBrief {product_brief.id} was not loaded by this transaction"
            )
        flush_with_integrity_classification(self._session)
        values: dict[str, object] = {
            "state": product_brief.state.value,
            "current_version_id": product_brief.current_version_id,
            "confirmed_version_id": product_brief.confirmed_version_id,
            "version": product_brief.version,
            "updated_at": product_brief.updated_at,
        }
        if operation_id is not None:
            values["operation_id"] = operation_id
        result = execute_with_integrity_classification(
            self._session,
            update(ProductBriefModel)
            .where(
                ProductBriefModel.workspace_id == product_brief.workspace_id,
                ProductBriefModel.id == product_brief.id,
                ProductBriefModel.version == original_version,
            )
            .values(**values),
        )
        if result.rowcount != 1:
            raise ConcurrencyError(f"ProductBrief {product_brief.id} was concurrently modified")
        self._loaded_versions[product_brief.id] = product_brief.version

    def add_analysis(self, analysis: ProductBriefAnalysisRecord) -> None:
        # SQLAlchemy has no ORM relationships across these deep-module mappers;
        # flush each immutable parent boundary before staging its child rows.
        flush_with_integrity_classification(self._session)
        self._session.add(
            ProductBriefAnalysisRequestModel(
                id=analysis.id,
                workspace_id=analysis.workspace_id,
                product_brief_id=analysis.product_brief_id,
                operation_id=analysis.operation_id,
                category=analysis.category.value,
                expected_workflow_version=analysis.expected_workflow_version,
                product_catalog_version=analysis.product_catalog_version,
                provider=analysis.provider,
                endpoint_region=analysis.endpoint_region,
                endpoint_host=analysis.endpoint_host,
                requested_model=analysis.requested_model,
                submitted_model_snapshot=analysis.submitted_model_snapshot,
                provider_configuration_snapshot_sha256=(
                    analysis.provider_configuration_snapshot_sha256
                ),
                prompt_version=analysis.prompt_version,
                review_policy_version=analysis.review_policy_version,
                review_confidence_threshold=analysis.review_confidence_threshold,
                review_mandatory_paths_json=list(analysis.review_mandatory_paths),
                review_sensitive_claim_paths_json=list(analysis.review_sensitive_claim_paths),
                review_policy_snapshot_sha256=(analysis.review_policy_snapshot_sha256),
                transfer_policy_version=analysis.transfer_policy_version,
                transfer_policy_snapshot_sha256=(analysis.transfer_policy_snapshot_sha256),
                created_by=analysis.created_by,
                trace_id=analysis.trace_id,
                retention_class=analysis.retention_class.value,
                retention_deadline=analysis.retention_deadline,
                created_at=analysis.created_at,
            )
        )
        flush_with_integrity_classification(self._session)
        self._session.add_all(
            [
                ProductBriefSourceAssetModel(
                    workspace_id=analysis.workspace_id,
                    analysis_request_id=analysis.id,
                    asset_id=source.asset_id,
                    asset_version_id=source.asset_version_id,
                    asset_object_id=source.asset_object_id,
                    ordinal=source.ordinal,
                    created_at=analysis.created_at,
                )
                for source in analysis.sources
            ]
        )

    def get_analysis_by_operation(
        self,
        *,
        workspace_id: str,
        operation_id: str,
    ) -> ProductBriefAnalysisRecord | None:
        model = self._session.scalar(
            select(ProductBriefAnalysisRequestModel).where(
                ProductBriefAnalysisRequestModel.workspace_id == workspace_id,
                ProductBriefAnalysisRequestModel.operation_id == operation_id,
            )
        )
        if model is None:
            return None
        source_models = self._session.scalars(
            select(ProductBriefSourceAssetModel)
            .where(
                ProductBriefSourceAssetModel.workspace_id == workspace_id,
                ProductBriefSourceAssetModel.analysis_request_id == model.id,
            )
            .order_by(ProductBriefSourceAssetModel.ordinal)
        )
        return ProductBriefAnalysisRecord(
            id=model.id,
            workspace_id=model.workspace_id,
            product_brief_id=model.product_brief_id,
            operation_id=model.operation_id,
            category=ProductBriefCategory(model.category),
            expected_workflow_version=model.expected_workflow_version,
            product_catalog_version=model.product_catalog_version,
            provider=model.provider,
            endpoint_region=model.endpoint_region,
            endpoint_host=model.endpoint_host,
            requested_model=model.requested_model,
            submitted_model_snapshot=model.submitted_model_snapshot,
            provider_configuration_snapshot_sha256=(model.provider_configuration_snapshot_sha256),
            prompt_version=model.prompt_version,
            review_policy_version=model.review_policy_version,
            review_confidence_threshold=model.review_confidence_threshold,
            review_mandatory_paths=tuple(model.review_mandatory_paths_json),
            review_sensitive_claim_paths=tuple(model.review_sensitive_claim_paths_json),
            review_policy_snapshot_sha256=model.review_policy_snapshot_sha256,
            transfer_policy_version=model.transfer_policy_version,
            transfer_policy_snapshot_sha256=model.transfer_policy_snapshot_sha256,
            created_by=model.created_by,
            trace_id=model.trace_id,
            retention_class=RetentionClass(model.retention_class),
            retention_deadline=model.retention_deadline,
            created_at=model.created_at,
            sources=tuple(
                ProductBriefSourceAsset(
                    asset_id=source.asset_id,
                    asset_version_id=source.asset_version_id,
                    asset_object_id=source.asset_object_id,
                    ordinal=source.ordinal,
                )
                for source in source_models
            ),
        )

    def add_provider_attempt(self, attempt: StoredProviderAttempt) -> None:
        self._session.add(
            ProductBriefProviderAttemptModel(
                id=attempt.id,
                workspace_id=attempt.workspace_id,
                product_brief_id=attempt.product_brief_id,
                operation_id=attempt.operation_id,
                operation_attempt=attempt.operation_attempt,
                call_index=attempt.call_index,
                submission_key_sha256=attempt.submission_key_sha256,
                input_sha256=attempt.input_sha256,
                provider=attempt.provider,
                endpoint_region=attempt.endpoint_region,
                endpoint_host=attempt.endpoint_host,
                requested_model=attempt.requested_model,
                submitted_model_snapshot=attempt.submitted_model_snapshot,
                prompt_version=attempt.prompt_version,
                config_snapshot_sha256=attempt.config_snapshot_sha256,
                retention_class=attempt.retention_class.value,
                retention_deadline=attempt.retention_deadline,
                created_at=attempt.created_at,
            )
        )

    def get_provider_attempt(
        self,
        *,
        workspace_id: str,
        operation_id: str,
        operation_attempt: int,
        call_index: int,
    ) -> StoredProviderAttempt | None:
        model = self._session.scalar(
            select(ProductBriefProviderAttemptModel).where(
                ProductBriefProviderAttemptModel.workspace_id == workspace_id,
                ProductBriefProviderAttemptModel.operation_id == operation_id,
                ProductBriefProviderAttemptModel.operation_attempt == operation_attempt,
                ProductBriefProviderAttemptModel.call_index == call_index,
            )
        )
        return _provider_attempt_from_model(model) if model is not None else None

    def list_provider_attempts(
        self,
        *,
        workspace_id: str,
        operation_id: str,
        operation_attempt: int,
    ) -> tuple[StoredProviderAttempt, ...]:
        models = self._session.scalars(
            select(ProductBriefProviderAttemptModel)
            .where(
                ProductBriefProviderAttemptModel.workspace_id == workspace_id,
                ProductBriefProviderAttemptModel.operation_id == operation_id,
                ProductBriefProviderAttemptModel.operation_attempt == operation_attempt,
            )
            .order_by(ProductBriefProviderAttemptModel.call_index)
        )
        return tuple(_provider_attempt_from_model(model) for model in models)

    def add_provider_artifact(self, artifact: StoredProviderArtifact) -> None:
        self._session.add(
            ProductBriefProviderArtifactModel(
                id=artifact.id,
                workspace_id=artifact.workspace_id,
                product_brief_id=artifact.product_brief_id,
                operation_id=artifact.operation_id,
                operation_attempt=artifact.operation_attempt,
                call_index=artifact.call_index,
                kind=artifact.kind.value,
                state=artifact.state.value,
                key_schema_version=artifact.key_schema_version,
                storage_backend=artifact.storage_backend,
                location=artifact.location.value,
                bucket=artifact.bucket,
                object_key=artifact.key,
                target_sha256=artifact.target_sha256,
                content_type=artifact.content_type,
                expected_sha256=artifact.expected_sha256,
                expected_byte_size=artifact.expected_byte_size,
                retention_class=artifact.retention_class.value,
                retention_deadline=artifact.retention_deadline,
                write_fence=artifact.write_fence,
                provider_version_id=artifact.provider_version_id,
                etag=artifact.etag,
                unknown_reason=artifact.unknown_reason,
                version=artifact.version,
                stored_at=artifact.stored_at,
                created_at=artifact.created_at,
                updated_at=artifact.updated_at,
            )
        )

    def get_provider_artifact(
        self,
        *,
        workspace_id: str,
        operation_id: str,
        operation_attempt: int,
        call_index: int,
        kind: ProviderArtifactKind,
        for_update: bool = False,
    ) -> StoredProviderArtifact | None:
        statement = select(ProductBriefProviderArtifactModel).where(
            ProductBriefProviderArtifactModel.workspace_id == workspace_id,
            ProductBriefProviderArtifactModel.operation_id == operation_id,
            ProductBriefProviderArtifactModel.operation_attempt == operation_attempt,
            ProductBriefProviderArtifactModel.call_index == call_index,
            ProductBriefProviderArtifactModel.kind == kind.value,
        )
        if for_update:
            statement = statement.with_for_update()
        model = self._session.scalar(statement)
        return _provider_artifact_from_model(model) if model is not None else None

    def get_provider_artifact_by_id(
        self,
        *,
        workspace_id: str,
        artifact_id: str,
        for_update: bool = False,
    ) -> StoredProviderArtifact | None:
        statement = select(ProductBriefProviderArtifactModel).where(
            ProductBriefProviderArtifactModel.workspace_id == workspace_id,
            ProductBriefProviderArtifactModel.id == artifact_id,
        )
        if for_update:
            statement = statement.with_for_update()
        model = self._session.scalar(statement)
        return _provider_artifact_from_model(model) if model is not None else None

    def save_provider_artifact(
        self,
        artifact: StoredProviderArtifact,
        *,
        workspace_id: str,
        expected_version: int,
    ) -> None:
        result = execute_with_integrity_classification(
            self._session,
            update(ProductBriefProviderArtifactModel)
            .where(
                ProductBriefProviderArtifactModel.workspace_id == workspace_id,
                ProductBriefProviderArtifactModel.id == artifact.id,
                ProductBriefProviderArtifactModel.version == expected_version,
            )
            .values(
                state=artifact.state.value,
                provider_version_id=artifact.provider_version_id,
                etag=artifact.etag,
                unknown_reason=artifact.unknown_reason,
                version=artifact.version,
                stored_at=artifact.stored_at,
                updated_at=artifact.updated_at,
            ),
        )
        if result.rowcount != 1:
            raise ConcurrencyError(f"provider artifact {artifact.id} changed concurrently")

    def list_provider_artifacts(
        self,
        *,
        workspace_id: str,
        operation_id: str,
        operation_attempt: int,
    ) -> tuple[StoredProviderArtifact, ...]:
        models = self._session.scalars(
            select(ProductBriefProviderArtifactModel)
            .where(
                ProductBriefProviderArtifactModel.workspace_id == workspace_id,
                ProductBriefProviderArtifactModel.operation_id == operation_id,
                ProductBriefProviderArtifactModel.operation_attempt == operation_attempt,
            )
            .order_by(
                ProductBriefProviderArtifactModel.call_index,
                ProductBriefProviderArtifactModel.kind,
            )
        )
        return tuple(_provider_artifact_from_model(model) for model in models)

    def list_provider_artifacts_for_reconciliation(
        self,
        *,
        stale_before: datetime,
        limit: int,
        after_updated_at: datetime | None = None,
        after_id: str | None = None,
    ) -> tuple[StoredProviderArtifact, ...]:
        statement = select(ProductBriefProviderArtifactModel).where(
            ProductBriefProviderArtifactModel.state.in_(
                (
                    ProviderArtifactState.INTENDED.value,
                    ProviderArtifactState.UNKNOWN.value,
                )
            ),
            ProductBriefProviderArtifactModel.updated_at <= stale_before,
        )
        if after_updated_at is not None:
            statement = statement.where(
                or_(
                    ProductBriefProviderArtifactModel.updated_at > after_updated_at,
                    and_(
                        ProductBriefProviderArtifactModel.updated_at == after_updated_at,
                        ProductBriefProviderArtifactModel.id > (after_id or ""),
                    ),
                )
            )
        models = self._session.scalars(
            statement.order_by(
                ProductBriefProviderArtifactModel.updated_at,
                ProductBriefProviderArtifactModel.id,
            ).limit(limit)
        )
        return tuple(_provider_artifact_from_model(model) for model in models)

    def add_provider_calls(self, calls: tuple[StoredProviderCall, ...]) -> None:
        self._session.add_all(
            [
                ProductBriefProviderCallModel(
                    id=call.id,
                    workspace_id=call.workspace_id,
                    product_brief_id=call.product_brief_id,
                    operation_id=call.operation_id,
                    operation_attempt=call.operation_attempt,
                    call_index=call.call_index,
                    status=call.status.value,
                    provider=call.provider,
                    endpoint_region=call.endpoint_region,
                    endpoint_host=call.endpoint_host,
                    requested_model=call.requested_model,
                    submitted_model_snapshot=call.submitted_model_snapshot,
                    resolved_model=call.resolved_model,
                    prompt_version=call.prompt_version,
                    config_snapshot_sha256=call.config_snapshot_sha256,
                    request_id=call.request_id,
                    input_tokens=call.input_tokens,
                    output_tokens=call.output_tokens,
                    total_tokens=call.total_tokens,
                    latency_ms=call.latency_ms,
                    request_artifact_id=call.request_artifact_id,
                    request_artifact_storage_backend=(call.request_artifact.storage_backend),
                    request_artifact_location=call.request_artifact.location.value,
                    request_artifact_bucket=call.request_artifact.bucket,
                    request_artifact_key=call.request_artifact.key,
                    request_artifact_provider_version_id=(
                        call.request_artifact.provider_version_id
                    ),
                    request_artifact_etag=call.request_artifact.etag,
                    request_artifact_sha256=call.request_artifact.sha256,
                    request_artifact_byte_size=call.request_artifact.byte_size,
                    response_artifact_id=call.response_artifact_id,
                    response_artifact_storage_backend=(
                        call.response_artifact.storage_backend
                        if call.response_artifact is not None
                        else None
                    ),
                    response_artifact_location=(
                        call.response_artifact.location.value
                        if call.response_artifact is not None
                        else None
                    ),
                    response_artifact_bucket=(
                        call.response_artifact.bucket
                        if call.response_artifact is not None
                        else None
                    ),
                    response_artifact_key=(
                        call.response_artifact.key if call.response_artifact is not None else None
                    ),
                    response_artifact_provider_version_id=(
                        call.response_artifact.provider_version_id
                        if call.response_artifact is not None
                        else None
                    ),
                    response_artifact_etag=(
                        call.response_artifact.etag if call.response_artifact is not None else None
                    ),
                    response_artifact_sha256=(
                        call.response_artifact.sha256
                        if call.response_artifact is not None
                        else None
                    ),
                    response_artifact_byte_size=(
                        call.response_artifact.byte_size
                        if call.response_artifact is not None
                        else None
                    ),
                    error_code=call.error_code,
                    error_category=call.error_category,
                    error_retryable=call.error_retryable,
                    retention_class=call.retention_class.value,
                    retention_deadline=call.retention_deadline,
                    created_at=call.created_at,
                )
                for call in calls
            ]
        )

    def get_provider_call(
        self,
        *,
        workspace_id: str,
        provider_call_id: str,
    ) -> StoredProviderCall | None:
        model = self._session.scalar(
            select(ProductBriefProviderCallModel).where(
                ProductBriefProviderCallModel.workspace_id == workspace_id,
                ProductBriefProviderCallModel.id == provider_call_id,
            )
        )
        return _provider_call_from_model(model) if model is not None else None

    def list_provider_calls(
        self,
        *,
        workspace_id: str,
        operation_id: str,
        operation_attempt: int,
    ) -> tuple[StoredProviderCall, ...]:
        models = self._session.scalars(
            select(ProductBriefProviderCallModel)
            .where(
                ProductBriefProviderCallModel.workspace_id == workspace_id,
                ProductBriefProviderCallModel.operation_id == operation_id,
                ProductBriefProviderCallModel.operation_attempt == operation_attempt,
            )
            .order_by(ProductBriefProviderCallModel.call_index)
        )
        return tuple(_provider_call_from_model(model) for model in models)

    def add_version(self, stored: StoredProductBriefVersion) -> None:
        version = stored.version
        flush_with_integrity_classification(self._session)
        self._session.add(
            ProductBriefVersionModel(
                id=version.id,
                workspace_id=version.workspace_id,
                product_brief_id=version.product_brief_id,
                version_number=version.version_number,
                supersedes_version_id=version.supersedes_version_id,
                category=version.category.value,
                common_schema_version=version.common_schema_version,
                category_schema_version=version.category_schema_version,
                payload_sha256=version.payload_sha256,
                changed_paths_json=list(version.changed_field_paths),
                confirmation_required=version.confirmation_required,
                unresolved_field_count=version.unresolved_field_count,
                review_policy_version=version.review_policy_version,
                source=version.source.value,
                prompt_version=version.prompt_version,
                provider_call_id=version.provider_call_id,
                actor_id=version.actor_id,
                revision_reason=version.revision_reason,
                retention_class=version.retention_class.value,
                retention_deadline=version.retention_deadline,
                created_at=version.created_at,
            )
        )
        flush_with_integrity_classification(self._session)
        field_models: list[ProductBriefFieldModel] = []
        evidence_models: list[ProductBriefEvidenceModel] = []
        for field in version.fields:
            field_models.append(
                ProductBriefFieldModel(
                    id=field.id,
                    workspace_id=version.workspace_id,
                    product_brief_id=version.product_brief_id,
                    product_brief_version_id=version.id,
                    path=field.path,
                    value_json=field.value,
                    confidence=field.confidence,
                    source=field.source.value,
                    conflict=field.conflict.value,
                    review_required=field.review_required,
                    sensitive=field.sensitive,
                    review_reasons_json=list(stored.review_reasons_by_path.get(field.path, ())),
                    created_at=version.created_at,
                )
            )
            evidence_models.extend(
                ProductBriefEvidenceModel(
                    id=evidence.id,
                    workspace_id=version.workspace_id,
                    product_brief_id=version.product_brief_id,
                    product_brief_version_id=version.id,
                    field_id=field.id,
                    source_asset_version_id=evidence.source_asset_version_id,
                    kind=evidence.kind.value,
                    reference=evidence.reference,
                    region_json=(list(evidence.region) if evidence.region is not None else None),
                    excerpt_sha256=evidence.excerpt_sha256,
                    created_at=version.created_at,
                )
                for evidence in field.evidence
            )
        self._session.add_all(field_models)
        flush_with_integrity_classification(self._session)
        self._session.add_all(evidence_models)

    def get_version(
        self,
        *,
        workspace_id: str,
        product_brief_version_id: str,
    ) -> StoredProductBriefVersion | None:
        model = self._session.scalar(
            select(ProductBriefVersionModel).where(
                ProductBriefVersionModel.workspace_id == workspace_id,
                ProductBriefVersionModel.id == product_brief_version_id,
            )
        )
        if model is None:
            return None
        return self._stored_version(model)

    def get_model_version_by_operation(
        self,
        *,
        workspace_id: str,
        operation_id: str,
    ) -> StoredProductBriefVersion | None:
        models = list(
            self._session.scalars(
                select(ProductBriefVersionModel)
                .join(
                    ProductBriefProviderCallModel,
                    (
                        ProductBriefProviderCallModel.workspace_id
                        == ProductBriefVersionModel.workspace_id
                    )
                    & (
                        ProductBriefProviderCallModel.id
                        == ProductBriefVersionModel.provider_call_id
                    )
                    & (
                        ProductBriefProviderCallModel.product_brief_id
                        == ProductBriefVersionModel.product_brief_id
                    ),
                )
                .where(
                    ProductBriefVersionModel.workspace_id == workspace_id,
                    ProductBriefVersionModel.source == ProductBriefVersionSource.MODEL.value,
                    ProductBriefProviderCallModel.operation_id == operation_id,
                )
                .limit(2)
            )
        )
        if len(models) > 1:
            raise ConcurrencyError(
                f"operation {operation_id} produced multiple ProductBrief model versions"
            )
        return self._stored_version(models[0]) if models else None

    def list_versions(
        self,
        *,
        workspace_id: str,
        product_brief_id: str,
        limit: int,
        cursor: int | None,
    ) -> StoredProductBriefVersionPage:
        statement = select(ProductBriefVersionModel).where(
            ProductBriefVersionModel.workspace_id == workspace_id,
            ProductBriefVersionModel.product_brief_id == product_brief_id,
        )
        if cursor is not None:
            statement = statement.where(ProductBriefVersionModel.version_number < cursor)
        models = list(
            self._session.scalars(
                statement.order_by(ProductBriefVersionModel.version_number.desc()).limit(limit + 1)
            )
        )
        page_models = models[:limit]
        summaries = tuple(
            StoredProductBriefVersionSummary(
                id=model.id,
                product_brief_id=model.product_brief_id,
                version_number=model.version_number,
                supersedes_version_id=model.supersedes_version_id,
                category=ProductBriefCategory(model.category),
                common_schema_version=model.common_schema_version,
                category_schema_version=model.category_schema_version,
                payload_sha256=model.payload_sha256,
                changed_field_paths=tuple(model.changed_paths_json),
                confirmation_required=model.confirmation_required,
                unresolved_field_count=model.unresolved_field_count,
                review_policy_version=model.review_policy_version,
                source=ProductBriefVersionSource(model.source),
                prompt_version=model.prompt_version,
                provider_call_id=model.provider_call_id,
                actor_id=model.actor_id,
                revision_reason=model.revision_reason,
                retention_class=RetentionClass(model.retention_class),
                retention_deadline=model.retention_deadline,
                created_at=model.created_at,
            )
            for model in page_models
        )
        provider_call_ids = tuple(
            model.provider_call_id for model in page_models if model.provider_call_id is not None
        )
        provider_summaries: dict[str, StoredProductBriefProviderSummary] = {}
        if provider_call_ids:
            rows = self._session.execute(
                select(
                    ProductBriefProviderCallModel.id,
                    ProductBriefProviderCallModel.product_brief_id,
                    ProductBriefProviderCallModel.provider,
                    ProductBriefProviderCallModel.requested_model,
                    ProductBriefProviderCallModel.resolved_model,
                    ProductBriefProviderCallModel.latency_ms,
                ).where(
                    ProductBriefProviderCallModel.workspace_id == workspace_id,
                    ProductBriefProviderCallModel.product_brief_id == product_brief_id,
                    ProductBriefProviderCallModel.id.in_(provider_call_ids),
                )
            ).mappings()
            provider_summaries = {
                row["id"]: StoredProductBriefProviderSummary(
                    id=row["id"],
                    product_brief_id=row["product_brief_id"],
                    provider=row["provider"],
                    requested_model=row["requested_model"],
                    resolved_model=row["resolved_model"],
                    latency_ms=row["latency_ms"],
                )
                for row in rows
            }
        return StoredProductBriefVersionPage(
            items=summaries,
            provider_summaries_by_call_id=provider_summaries,
            next_cursor=(
                page_models[-1].version_number if len(models) > limit and page_models else None
            ),
        )

    def _stored_versions(
        self,
        models: list[ProductBriefVersionModel],
    ) -> tuple[StoredProductBriefVersion, ...]:
        if not models:
            return ()
        version_ids = tuple(model.id for model in models)
        field_models = list(
            self._session.scalars(
                select(ProductBriefFieldModel)
                .where(
                    ProductBriefFieldModel.workspace_id == models[0].workspace_id,
                    ProductBriefFieldModel.product_brief_version_id.in_(version_ids),
                )
                .order_by(
                    ProductBriefFieldModel.product_brief_version_id,
                    ProductBriefFieldModel.path,
                )
            )
        )
        evidence_models = list(
            self._session.scalars(
                select(ProductBriefEvidenceModel)
                .where(
                    ProductBriefEvidenceModel.workspace_id == models[0].workspace_id,
                    ProductBriefEvidenceModel.product_brief_version_id.in_(version_ids),
                )
                .order_by(
                    ProductBriefEvidenceModel.product_brief_version_id,
                    ProductBriefEvidenceModel.field_id,
                    ProductBriefEvidenceModel.id,
                )
            )
        )
        fields_by_version: dict[str, list[ProductBriefFieldModel]] = {}
        for field in field_models:
            fields_by_version.setdefault(field.product_brief_version_id, []).append(field)
        evidence_by_version: dict[str, list[ProductBriefEvidenceModel]] = {}
        for evidence in evidence_models:
            evidence_by_version.setdefault(evidence.product_brief_version_id, []).append(evidence)
        return tuple(
            self._stored_version_from_models(
                model,
                field_models=fields_by_version.get(model.id, []),
                evidence_models=evidence_by_version.get(model.id, []),
            )
            for model in models
        )

    def next_version_number(
        self,
        *,
        workspace_id: str,
        product_brief_id: str,
    ) -> int:
        current = self._session.scalar(
            select(func.max(ProductBriefVersionModel.version_number)).where(
                ProductBriefVersionModel.workspace_id == workspace_id,
                ProductBriefVersionModel.product_brief_id == product_brief_id,
            )
        )
        return int(current or 0) + 1

    def _stored_version(
        self,
        model: ProductBriefVersionModel,
    ) -> StoredProductBriefVersion:
        return self._stored_versions([model])[0]

    @staticmethod
    def _stored_version_from_models(
        model: ProductBriefVersionModel,
        *,
        field_models: list[ProductBriefFieldModel],
        evidence_models: list[ProductBriefEvidenceModel],
    ) -> StoredProductBriefVersion:
        evidence_by_field: dict[str, list[ProductBriefEvidence]] = {}
        for evidence in evidence_models:
            region = (
                tuple(float(value) for value in evidence.region_json)
                if evidence.region_json is not None
                else None
            )
            evidence_by_field.setdefault(evidence.field_id, []).append(
                ProductBriefEvidence(
                    id=evidence.id,
                    source_asset_version_id=evidence.source_asset_version_id,
                    kind=ProductBriefEvidenceKind(evidence.kind),
                    reference=evidence.reference,
                    region=region,
                    excerpt_sha256=evidence.excerpt_sha256,
                )
            )
        fields = tuple(
            ProductBriefField(
                id=field.id,
                path=field.path,
                value=field.value_json,
                confidence=field.confidence,
                source=ProductBriefFieldSource(field.source),
                conflict=ProductBriefFieldConflict(field.conflict),
                review_required=field.review_required,
                sensitive=field.sensitive,
                evidence=tuple(evidence_by_field.get(field.id, ())),
            )
            for field in field_models
        )
        version = ProductBriefVersion(
            id=model.id,
            workspace_id=model.workspace_id,
            product_brief_id=model.product_brief_id,
            version_number=model.version_number,
            supersedes_version_id=model.supersedes_version_id,
            category=ProductBriefCategory(model.category),
            common_schema_version=model.common_schema_version,
            category_schema_version=model.category_schema_version,
            fields=fields,
            changed_field_paths=tuple(model.changed_paths_json),
            payload_sha256=model.payload_sha256,
            confirmation_required=model.confirmation_required,
            unresolved_field_count=model.unresolved_field_count,
            review_policy_version=model.review_policy_version,
            source=ProductBriefVersionSource(model.source),
            prompt_version=model.prompt_version,
            provider_call_id=model.provider_call_id,
            actor_id=model.actor_id,
            revision_reason=model.revision_reason,
            retention_class=RetentionClass(model.retention_class),
            retention_deadline=model.retention_deadline,
            created_at=model.created_at,
        )
        return StoredProductBriefVersion(
            version=version,
            review_reasons_by_path={
                field.path: tuple(field.review_reasons_json)
                for field in field_models
                if field.review_reasons_json
            },
        )

    def add_confirmation(self, confirmation: ProductBriefConfirmation) -> None:
        flush_with_integrity_classification(self._session)
        self._session.add(
            ProductBriefConfirmationModel(
                id=confirmation.id,
                workspace_id=confirmation.workspace_id,
                product_brief_id=confirmation.product_brief_id,
                product_brief_version_id=confirmation.product_brief_version_id,
                product_brief_version_number=confirmation.product_brief_version_number,
                workflow_id=confirmation.workflow_id,
                operation_id=confirmation.operation_id,
                approval_id=confirmation.approval_id,
                approval_type="PRODUCT_BRIEF",
                approval_decision="APPROVE",
                confirmed_by=confirmation.confirmed_by,
                reason_code=confirmation.reason_code,
                comment_ref=confirmation.comment_ref,
                expected_product_brief_version=(confirmation.expected_product_brief_version),
                expected_workflow_version=confirmation.expected_workflow_version,
                created_at=confirmation.created_at,
            )
        )

    def get_confirmation(
        self,
        *,
        workspace_id: str,
        product_brief_id: str,
        product_brief_version_id: str,
    ) -> ProductBriefConfirmation | None:
        model = self._session.scalar(
            select(ProductBriefConfirmationModel).where(
                ProductBriefConfirmationModel.workspace_id == workspace_id,
                ProductBriefConfirmationModel.product_brief_id == product_brief_id,
                ProductBriefConfirmationModel.product_brief_version_id == product_brief_version_id,
            )
        )
        if model is None:
            return None
        return ProductBriefConfirmation(
            id=model.id,
            workspace_id=model.workspace_id,
            product_brief_id=model.product_brief_id,
            product_brief_version_id=model.product_brief_version_id,
            product_brief_version_number=model.product_brief_version_number,
            workflow_id=model.workflow_id,
            operation_id=model.operation_id,
            approval_id=model.approval_id,
            confirmed_by=model.confirmed_by,
            reason_code=model.reason_code,
            comment_ref=model.comment_ref,
            expected_product_brief_version=model.expected_product_brief_version,
            expected_workflow_version=model.expected_workflow_version,
            created_at=model.created_at,
        )


class SqlAlchemyProductBriefUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self._depth_token: object | None = None
        self._committed = False

    def __enter__(self) -> SqlAlchemyProductBriefUnitOfWork:
        self._session = self._session_factory()
        self._depth_token = enter_unit_of_work()
        product_brief_repository = ProductBriefRepository(self._session)
        self.product_briefs = product_brief_repository
        self.product_brief_analyses = product_brief_repository
        self.product_brief_artifacts = product_brief_repository
        self.product_brief_confirmations = product_brief_repository
        self.product_brief_lineage = product_brief_repository
        self.products = ProductRepository(self._session)
        self.assets = AssetRepository(self._session)
        self.workflows = WorkflowRepository(self._session)
        self.approvals = ApprovalRepository(self._session)
        self.operations = OperationRepository(self._session)
        self.idempotency = IdempotencyRepository(self._session)
        self.outbox = OutboxRepository(self._session)
        self.audit = AuditRepository(self._session)
        return self

    def database_now(self) -> datetime:
        if self._session is None:
            raise RuntimeError("ProductBrief unit of work is not active")
        value = self._session.scalar(select(literal_column("UTC_TIMESTAMP(6)")))
        if not isinstance(value, datetime):
            raise RuntimeError("database did not return a timestamp")
        return _aware(value)

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("ProductBrief unit of work is not active")
        try:
            self._session.commit()
        except DBAPIError as exc:
            self._session.rollback()
            classified = classify_database_error(exc)
            if classified is None:
                raise
            raise classified from exc
        self._committed = True

    def commit_before_retention_deadline(
        self,
        *,
        workspace_id: str,
        product_brief_id: str,
        retention_deadline: datetime,
        clock,
    ) -> None:
        if self._session is None:
            raise RuntimeError("ProductBrief unit of work is not active")
        if retention_deadline.tzinfo is None or retention_deadline.utcoffset() != timedelta(0):
            raise ValueError("ProductBrief retention deadline must be aware UTC")
        flush_with_integrity_classification(self._session)
        persisted_value = execute_with_integrity_classification(
            self._session,
            select(ProductBriefModel.retention_deadline)
            .where(
                ProductBriefModel.workspace_id == workspace_id,
                ProductBriefModel.id == product_brief_id,
            )
            .with_for_update(),
        ).scalar_one_or_none()
        if persisted_value is None:
            self._session.rollback()
            raise ConcurrencyError(
                f"Task ProductBrief {product_brief_id} lost its retention boundary"
            )
        observed_at = clock()
        if observed_at.tzinfo is None or observed_at.utcoffset() != timedelta(0):
            self._session.rollback()
            raise ValueError("ProductBrief commit clock must return aware UTC")
        database_value = self._session.scalar(select(literal_column("UTC_TIMESTAMP(6)")))
        if not isinstance(database_value, datetime):
            self._session.rollback()
            raise RuntimeError("database did not return a commit timestamp")
        persisted_deadline = _aware(persisted_value)
        database_now = _aware(database_value)
        if persisted_deadline != retention_deadline:
            self._session.rollback()
            raise ConcurrencyError(f"ProductBrief {product_brief_id} retention deadline changed")
        if observed_at >= retention_deadline or database_now >= retention_deadline:
            self._session.rollback()
            raise ProductBriefRetentionExpiredError(
                "ProductBrief retention expired at the database commit boundary"
            )
        self.commit()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if self._session is not None and (exc_type is not None or not self._committed):
                self._session.rollback()
        finally:
            if self._session is not None:
                self._session.close()
            if self._depth_token is not None:
                exit_unit_of_work(self._depth_token)
