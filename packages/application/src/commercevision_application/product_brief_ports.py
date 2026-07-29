"""Typed persistence seams for ProductBrief analysis and confirmation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from commercevision_contracts.object_storage import (
    ObjectReference,
    ObjectStat,
    ObjectVersionPage,
)
from commercevision_contracts.product_briefs import (
    PreparedProviderArtifact,
    ProviderArtifactKind,
    ProviderArtifactPhysicalTarget,
    ProviderArtifactReference,
    ProviderArtifactState,
    VisionProviderStatus,
)
from commercevision_domain import (
    ProductBrief,
    ProductBriefCategory,
    ProductBriefVersion,
    ProductBriefVersionSource,
    RetentionClass,
    StorageLocationClass,
)

from .asset_ports import AssetRepositoryPort
from .catalog_ports import ProductRepositoryPort
from .operation_ports import OperationRepositoryPort
from .ports import (
    ApprovalRepositoryPort,
    AuditRepositoryPort,
    IdempotencyRepositoryPort,
    OutboxRepositoryPort,
    WorkflowRepositoryPort,
)


@dataclass(frozen=True, slots=True)
class ProductBriefSourceAsset:
    asset_id: str
    asset_version_id: str
    asset_object_id: str
    ordinal: int


@dataclass(frozen=True, slots=True)
class ProductBriefAnalysisRecord:
    id: str
    workspace_id: str
    product_brief_id: str
    operation_id: str
    category: ProductBriefCategory
    expected_workflow_version: int
    product_catalog_version: int
    provider: str
    endpoint_region: str
    endpoint_host: str
    requested_model: str
    submitted_model_snapshot: str
    provider_configuration_snapshot_sha256: str
    prompt_version: str
    review_policy_version: str
    review_confidence_threshold: Decimal
    review_mandatory_paths: tuple[str, ...]
    review_sensitive_claim_paths: tuple[str, ...]
    review_policy_snapshot_sha256: str
    transfer_policy_version: str
    transfer_policy_snapshot_sha256: str
    created_by: str
    trace_id: str
    retention_class: RetentionClass
    retention_deadline: datetime | None
    created_at: datetime
    sources: tuple[ProductBriefSourceAsset, ...]


@dataclass(frozen=True, slots=True)
class StoredProviderAttempt:
    id: str
    workspace_id: str
    product_brief_id: str
    operation_id: str
    operation_attempt: int
    call_index: int
    submission_key_sha256: str
    input_sha256: str
    provider: str
    endpoint_region: str
    endpoint_host: str
    requested_model: str
    submitted_model_snapshot: str
    prompt_version: str
    config_snapshot_sha256: str
    retention_class: RetentionClass
    retention_deadline: datetime | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class StoredProviderArtifact:
    id: str
    workspace_id: str
    product_brief_id: str
    operation_id: str
    operation_attempt: int
    call_index: int
    kind: ProviderArtifactKind
    state: ProviderArtifactState
    key_schema_version: str
    storage_backend: str
    location: StorageLocationClass
    bucket: str
    key: str
    target_sha256: str
    content_type: str
    expected_sha256: str
    expected_byte_size: int
    retention_class: RetentionClass
    retention_deadline: datetime | None
    write_fence: str
    provider_version_id: str | None
    etag: str | None
    unknown_reason: str | None
    version: int
    stored_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class StoredProviderCall:
    id: str
    workspace_id: str
    product_brief_id: str
    operation_id: str
    operation_attempt: int
    call_index: int
    status: VisionProviderStatus
    provider: str
    endpoint_region: str
    endpoint_host: str
    requested_model: str
    submitted_model_snapshot: str
    resolved_model: str | None
    prompt_version: str
    config_snapshot_sha256: str
    request_id: str | None
    input_tokens: int
    output_tokens: int
    total_tokens: int
    latency_ms: int
    request_artifact: ProviderArtifactReference
    response_artifact: ProviderArtifactReference | None
    error_code: str | None
    error_category: str | None
    error_retryable: bool | None
    retention_class: RetentionClass
    retention_deadline: datetime | None
    created_at: datetime
    request_artifact_id: str | None = None
    response_artifact_id: str | None = None


@dataclass(frozen=True, slots=True)
class StoredProductBriefVersion:
    version: ProductBriefVersion
    review_reasons_by_path: dict[str, tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class StoredProductBriefVersionSummary:
    id: str
    product_brief_id: str
    version_number: int
    supersedes_version_id: str | None
    category: ProductBriefCategory
    common_schema_version: str
    category_schema_version: str
    payload_sha256: str
    changed_field_paths: tuple[str, ...]
    confirmation_required: bool
    unresolved_field_count: int
    review_policy_version: str
    source: ProductBriefVersionSource
    prompt_version: str | None
    provider_call_id: str | None
    actor_id: str
    revision_reason: str | None
    retention_class: RetentionClass
    retention_deadline: datetime | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class StoredProductBriefProviderSummary:
    id: str
    product_brief_id: str
    provider: str
    requested_model: str
    resolved_model: str | None
    latency_ms: int


@dataclass(frozen=True, slots=True)
class StoredProductBriefVersionPage:
    items: tuple[StoredProductBriefVersionSummary, ...]
    provider_summaries_by_call_id: Mapping[str, StoredProductBriefProviderSummary]
    next_cursor: int | None


@dataclass(frozen=True, slots=True)
class ProductBriefConfirmation:
    id: str
    workspace_id: str
    product_brief_id: str
    product_brief_version_id: str
    product_brief_version_number: int
    workflow_id: str
    operation_id: str
    approval_id: str
    confirmed_by: str
    reason_code: str | None
    comment_ref: str | None
    expected_product_brief_version: int
    expected_workflow_version: int
    created_at: datetime


class ProductBriefRepositoryPort(Protocol):
    def add(self, product_brief: ProductBrief, *, operation_id: str) -> None: ...

    def get(
        self,
        *,
        workspace_id: str,
        product_brief_id: str,
        for_update: bool = False,
    ) -> ProductBrief | None: ...

    def get_by_workflow_product(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        product_id: str,
        for_update: bool = False,
    ) -> ProductBrief | None: ...

    def get_by_operation(
        self,
        *,
        workspace_id: str,
        operation_id: str,
        for_update: bool = False,
    ) -> ProductBrief | None: ...

    def operation_id(
        self,
        *,
        workspace_id: str,
        product_brief_id: str,
    ) -> str | None: ...

    def save(
        self,
        product_brief: ProductBrief,
        *,
        operation_id: str | None = None,
    ) -> None: ...

    def add_version(self, version: StoredProductBriefVersion) -> None: ...

    def get_version(
        self,
        *,
        workspace_id: str,
        product_brief_version_id: str,
    ) -> StoredProductBriefVersion | None: ...

    def get_model_version_by_operation(
        self,
        *,
        workspace_id: str,
        operation_id: str,
    ) -> StoredProductBriefVersion | None: ...

    def list_versions(
        self,
        *,
        workspace_id: str,
        product_brief_id: str,
        limit: int,
        cursor: int | None,
    ) -> StoredProductBriefVersionPage: ...

    def next_version_number(
        self,
        *,
        workspace_id: str,
        product_brief_id: str,
    ) -> int: ...


class ProductBriefAnalysisRepositoryPort(Protocol):
    def add_analysis(self, analysis: ProductBriefAnalysisRecord) -> None: ...

    def get_analysis_by_operation(
        self,
        *,
        workspace_id: str,
        operation_id: str,
    ) -> ProductBriefAnalysisRecord | None: ...

    def add_provider_attempt(self, attempt: StoredProviderAttempt) -> None: ...

    def get_provider_attempt(
        self,
        *,
        workspace_id: str,
        operation_id: str,
        operation_attempt: int,
        call_index: int,
    ) -> StoredProviderAttempt | None: ...

    def list_provider_attempts(
        self,
        *,
        workspace_id: str,
        operation_id: str,
        operation_attempt: int,
    ) -> tuple[StoredProviderAttempt, ...]: ...

    def add_provider_calls(self, calls: tuple[StoredProviderCall, ...]) -> None: ...

    def get_provider_call(
        self,
        *,
        workspace_id: str,
        provider_call_id: str,
    ) -> StoredProviderCall | None: ...

    def list_provider_calls(
        self,
        *,
        workspace_id: str,
        operation_id: str,
        operation_attempt: int,
    ) -> tuple[StoredProviderCall, ...]: ...


class ProductBriefArtifactRepositoryPort(Protocol):
    def add_provider_artifact(self, artifact: StoredProviderArtifact) -> None: ...

    def get_provider_artifact(
        self,
        *,
        workspace_id: str,
        operation_id: str,
        operation_attempt: int,
        call_index: int,
        kind: ProviderArtifactKind,
        for_update: bool = False,
    ) -> StoredProviderArtifact | None: ...

    def get_provider_artifact_by_id(
        self,
        *,
        workspace_id: str,
        artifact_id: str,
        for_update: bool = False,
    ) -> StoredProviderArtifact | None: ...

    def save_provider_artifact(
        self,
        artifact: StoredProviderArtifact,
        *,
        workspace_id: str,
        expected_version: int,
    ) -> None: ...

    def list_provider_artifacts(
        self,
        *,
        workspace_id: str,
        operation_id: str,
        operation_attempt: int,
    ) -> tuple[StoredProviderArtifact, ...]: ...

    def list_provider_artifacts_for_reconciliation(
        self,
        *,
        stale_before: datetime,
        limit: int,
        after_updated_at: datetime | None = None,
        after_id: str | None = None,
    ) -> tuple[StoredProviderArtifact, ...]: ...


class ProviderArtifactTargetReadinessQuery(Protocol):
    def list_reconciliation_targets(
        self,
        *,
        limit: int,
    ) -> tuple[ProviderArtifactPhysicalTarget, ...]: ...


class ProviderArtifactPhysicalTargetReader(Protocol):
    def list_versions(
        self,
        target: PreparedProviderArtifact,
        *,
        page_size: int,
        continuation_token: str | None,
    ) -> ObjectVersionPage: ...

    def stat(
        self,
        target: PreparedProviderArtifact,
        reference: ObjectReference,
    ) -> ObjectStat: ...


class ProductBriefConfirmationRepositoryPort(Protocol):
    def add_confirmation(self, confirmation: ProductBriefConfirmation) -> None: ...

    def get_confirmation(
        self,
        *,
        workspace_id: str,
        product_brief_id: str,
        product_brief_version_id: str,
    ) -> ProductBriefConfirmation | None: ...


class ProductBriefUnitOfWorkPort(Protocol):
    product_briefs: ProductBriefRepositoryPort
    product_brief_analyses: ProductBriefAnalysisRepositoryPort
    product_brief_artifacts: ProductBriefArtifactRepositoryPort
    product_brief_confirmations: ProductBriefConfirmationRepositoryPort
    products: ProductRepositoryPort
    assets: AssetRepositoryPort
    workflows: WorkflowRepositoryPort
    approvals: ApprovalRepositoryPort
    operations: OperationRepositoryPort
    idempotency: IdempotencyRepositoryPort
    outbox: OutboxRepositoryPort
    audit: AuditRepositoryPort

    def __enter__(self) -> ProductBriefUnitOfWorkPort: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None: ...

    def database_now(self) -> datetime: ...

    def commit(self) -> None: ...

    def commit_before_retention_deadline(
        self,
        *,
        workspace_id: str,
        product_brief_id: str,
        retention_deadline: datetime,
        clock: Callable[[], datetime],
    ) -> None: ...


ProductBriefUnitOfWorkFactory = Callable[[], ProductBriefUnitOfWorkPort]
