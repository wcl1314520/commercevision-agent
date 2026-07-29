"""ProductBrief HTTP and provider-neutral VisionAnalyzer contracts."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Protocol

from commercevision_domain import (
    ProductBriefCategory,
    ProductBriefEvidenceKind,
    ProductBriefFieldConflict,
    ProductBriefFieldSource,
    ProductBriefState,
    ProductBriefVersionSource,
    RetentionClass,
    StorageBackend,
    StorageLocationClass,
    StorageUnavailableError,
    assert_product_brief_schema,
    product_brief_field_value_kinds,
    validate_product_brief_evidence_reference,
    validate_product_brief_field_value,
)
from commercevision_domain import (
    ProductBriefFieldValueKind as ProductBriefFieldValueKind,
)
from commercevision_domain import (
    product_brief_field_paths as product_brief_field_paths,
)
from commercevision_domain import (
    product_brief_field_value_kind as product_brief_field_value_kind,
)
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SecretStr,
    field_validator,
    model_validator,
)

from .object_storage import ObjectStat


class ProductBriefContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


PROVIDER_ARTIFACT_KEY_SCHEMA_VERSION = "provider-artifact-v1"


class ProviderArtifactKind(StrEnum):
    REQUEST = "REQUEST"
    RESPONSE = "RESPONSE"


class ProviderArtifactState(StrEnum):
    INTENDED = "INTENDED"
    STORED = "STORED"
    UNKNOWN = "UNKNOWN"


class ProviderArtifactPersistenceError(RuntimeError):
    """Provider-neutral failure at the raw artifact persistence boundary."""


class ProviderArtifactIntegrityError(ProviderArtifactPersistenceError):
    """The artifact target or stored bytes violated their frozen contract."""


class ProviderArtifactUnavailableError(ProviderArtifactPersistenceError):
    """Artifact persistence was temporarily unavailable."""


class ProviderArtifactWriteSafeToRetryError(
    ProviderArtifactPersistenceError,
    StorageUnavailableError,
):
    """The artifact write was not attempted and can be retried safely."""


class ProviderArtifactWriteOutcomeUnknownError(
    ProviderArtifactPersistenceError,
    StorageUnavailableError,
):
    """The artifact write may have committed and requires reconciliation."""


class VisionProviderStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    MALFORMED = "MALFORMED"
    THROTTLED = "THROTTLED"
    TIMEOUT = "TIMEOUT"
    UNAVAILABLE = "UNAVAILABLE"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


class VisionAnalyzerIdentity(ProductBriefContract):
    provider: str = Field(min_length=1, max_length=64)
    endpoint_region: str = Field(min_length=1, max_length=64)
    endpoint_host: str = Field(min_length=1, max_length=255)
    requested_model: str = Field(min_length=1, max_length=128)
    submitted_model_snapshot: str = Field(min_length=1, max_length=128)
    prompt_version: str = Field(min_length=1, max_length=64)
    configuration_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class VisionImageInput(ProductBriefContract):
    asset_version_id: str = Field(min_length=1, max_length=36)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    url: SecretStr = Field(repr=False)
    required_headers: dict[str, SecretStr] = Field(default_factory=dict, repr=False)
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def require_utc_expiry(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("Vision image expiry must be timezone-aware UTC")
        return value


class VisionAnalysisRequest(ProductBriefContract):
    operation_id: str = Field(min_length=1, max_length=36)
    operation_attempt: int = Field(ge=1, le=1000)
    product_brief_id: str = Field(min_length=1, max_length=36)
    category: ProductBriefCategory
    product_facts: dict[str, JsonValue]
    images: tuple[VisionImageInput, ...] = Field(min_length=1, max_length=8)
    common_schema_version: str = Field(min_length=1, max_length=64)
    category_schema_version: str = Field(min_length=1, max_length=64)
    prompt_version: str = Field(min_length=1, max_length=64)
    policy_version: str = Field(min_length=1, max_length=64)
    retention_class: RetentionClass
    retention_deadline: datetime | None

    @model_validator(mode="after")
    def validate_request(self) -> VisionAnalysisRequest:
        ids = [image.asset_version_id for image in self.images]
        if len(set(ids)) != len(ids):
            raise ValueError("Vision image Asset Versions must be unique")
        if self.retention_class == RetentionClass.TASK and self.retention_deadline is None:
            raise ValueError("Task Vision requests require a retention deadline")
        if (
            self.retention_class == RetentionClass.FOUNDATION
            and self.retention_deadline is not None
        ):
            raise ValueError("Foundation Vision requests cannot have a task deadline")
        if self.retention_deadline is not None and (
            self.retention_deadline.tzinfo is None
            or self.retention_deadline.utcoffset() != timedelta(0)
        ):
            raise ValueError("Vision retention deadline must be timezone-aware UTC")
        return self


class ProviderArtifactWrite(ProductBriefContract):
    operation_id: str = Field(min_length=1, max_length=36)
    operation_attempt: int = Field(ge=1, le=1000)
    call_index: int = Field(ge=0, le=8)
    kind: ProviderArtifactKind
    content_type: str = Field(min_length=1, max_length=128)
    payload: bytes = Field(max_length=2 * 1024 * 1024, repr=False)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    retention_class: RetentionClass
    retention_deadline: datetime | None

    @model_validator(mode="after")
    def validate_artifact(self) -> ProviderArtifactWrite:
        if hashlib.sha256(self.payload).hexdigest() != self.sha256:
            raise ValueError("provider artifact SHA-256 does not match its payload")
        if self.retention_class == RetentionClass.TASK and self.retention_deadline is None:
            raise ValueError("Task provider artifacts require a retention deadline")
        if (
            self.retention_class == RetentionClass.FOUNDATION
            and self.retention_deadline is not None
        ):
            raise ValueError("Foundation provider artifacts cannot have a task deadline")
        return self


class ProviderArtifactReference(ProductBriefContract):
    storage_backend: str = Field(min_length=1, max_length=16)
    location: StorageLocationClass
    bucket: str = Field(min_length=1, max_length=255)
    key: str = Field(min_length=1, max_length=1024)
    provider_version_id: str = Field(min_length=1, max_length=256)
    etag: str = Field(min_length=1, max_length=512)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_size: int = Field(ge=0, le=2 * 1024 * 1024)
    retention_class: RetentionClass
    retention_deadline: datetime | None

    @property
    def opaque_ref(self) -> str:
        return f"object://{self.location.value}/{self.key}?version={self.provider_version_id}"


class ProviderArtifactPhysicalTarget(ProductBriefContract):
    storage_backend: StorageBackend
    location: StorageLocationClass
    bucket: str = Field(min_length=1, max_length=255)


class PreparedProviderArtifact(ProductBriefContract):
    ledger_id: str = Field(min_length=1, max_length=36)
    key_schema_version: str = Field(min_length=1, max_length=32)
    storage_backend: str = Field(min_length=1, max_length=16)
    location: StorageLocationClass
    bucket: str = Field(min_length=1, max_length=255)
    key: str = Field(min_length=1, max_length=1024)
    target_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_type: str = Field(min_length=1, max_length=128)
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_byte_size: int = Field(ge=0, le=2 * 1024 * 1024)
    retention_class: RetentionClass
    retention_deadline: datetime | None
    write_fence: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_retention(self) -> PreparedProviderArtifact:
        if self.retention_class == RetentionClass.TASK and self.retention_deadline is None:
            raise ValueError("Task provider artifact targets require a retention deadline")
        if (
            self.retention_class == RetentionClass.FOUNDATION
            and self.retention_deadline is not None
        ):
            raise ValueError("Foundation provider artifact targets cannot have a task deadline")
        return self


class ProviderArtifactSink(Protocol):
    def write(self, artifact: ProviderArtifactWrite) -> ProviderArtifactReference: ...


class ProviderArtifactStore(Protocol):
    def prepare(
        self,
        artifact: ProviderArtifactWrite,
        *,
        ledger_id: str,
        write_fence: str,
    ) -> PreparedProviderArtifact: ...

    def write_prepared(
        self,
        artifact: ProviderArtifactWrite,
        target: PreparedProviderArtifact,
    ) -> ProviderArtifactReference: ...

    def stat_matches(
        self,
        target: PreparedProviderArtifact,
        stat: ObjectStat,
    ) -> bool: ...


class ProductBriefEvidenceOutput(ProductBriefContract):
    source_asset_version_id: str = Field(min_length=1, max_length=36)
    kind: ProductBriefEvidenceKind
    reference: str = Field(min_length=1, max_length=512)
    region: tuple[float, float, float, float] | None = None
    excerpt_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_reference(self) -> ProductBriefEvidenceOutput:
        validate_product_brief_evidence_reference(self.kind, self.reference)
        return self

    @field_validator("region")
    @classmethod
    def validate_region(
        cls,
        value: tuple[float, float, float, float] | None,
    ) -> tuple[float, float, float, float] | None:
        if value is None:
            return None
        if any(not 0 <= coordinate <= 1 for coordinate in value):
            raise ValueError("evidence region coordinates must be normalized")
        if value[0] >= value[2] or value[1] >= value[3]:
            raise ValueError("evidence region bounds are invalid")
        return value


ProductBriefText = Annotated[str, Field(max_length=2048)]
ProductBriefNonEmptyText = Annotated[str, Field(min_length=1, max_length=2048)]


class ProductBriefIdentityValueV1(ProductBriefContract):
    kind: Literal["IDENTITY"]
    display_name: ProductBriefText
    model_number: ProductBriefText | None = None
    variant: ProductBriefText | None = None

    @model_validator(mode="after")
    def require_one_identity_fact(self) -> ProductBriefIdentityValueV1:
        if not any((self.display_name, self.model_number, self.variant)):
            raise ValueError("ProductBrief field value identity must contain one fact")
        return self


class ProductBriefCategoryValueV1(ProductBriefContract):
    kind: Literal["CATEGORY"]
    code: ProductBriefNonEmptyText
    label: ProductBriefNonEmptyText


class ProductBriefTextValueV1(ProductBriefContract):
    kind: Literal["TEXT"]
    text: ProductBriefText


class ProductBriefTextListValueV1(ProductBriefContract):
    kind: Literal["TEXT_LIST"]
    items: tuple[ProductBriefNonEmptyText, ...] = Field(max_length=32)


class ProductBriefStatementListValueV1(ProductBriefContract):
    kind: Literal["STATEMENT_LIST"]
    statements: tuple[ProductBriefNonEmptyText, ...] = Field(max_length=32)


class ProductBriefFlagListValueV1(ProductBriefContract):
    kind: Literal["FLAG_LIST"]
    flags: tuple[ProductBriefNonEmptyText, ...] = Field(max_length=32)


class ProductBriefDimensionValueItemV1(ProductBriefContract):
    name: ProductBriefNonEmptyText
    value: ProductBriefNonEmptyText
    unit: ProductBriefText | None = None
    raw_text: ProductBriefText | None = None


class ProductBriefDimensionListValueV1(ProductBriefContract):
    kind: Literal["DIMENSION_LIST"]
    dimensions: tuple[ProductBriefDimensionValueItemV1, ...] = Field(max_length=16)


ProductBriefFieldValueV1 = Annotated[
    ProductBriefIdentityValueV1
    | ProductBriefCategoryValueV1
    | ProductBriefTextValueV1
    | ProductBriefTextListValueV1
    | ProductBriefStatementListValueV1
    | ProductBriefFlagListValueV1
    | ProductBriefDimensionListValueV1,
    Field(discriminator="kind"),
]

_FIELD_VALUE_KIND_SCHEMA_EXTENSION = {
    "x-commercevision-field-value-kinds": product_brief_field_value_kinds()
}


class ProductBriefFieldOutput(ProductBriefContract):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        json_schema_extra=_FIELD_VALUE_KIND_SCHEMA_EXTENSION,
    )

    path: str = Field(min_length=1, max_length=160)
    value: ProductBriefFieldValueV1
    confidence: Decimal = Field(ge=0, le=1, max_digits=5, decimal_places=4)
    conflict: ProductBriefFieldConflict
    review_required: bool
    sensitive: bool
    evidence: tuple[ProductBriefEvidenceOutput, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_value_shape(self) -> ProductBriefFieldOutput:
        validate_product_brief_field_value(
            self.path,
            self.value.model_dump(mode="json"),
        )
        return self


class VisionStructuredOutput(ProductBriefContract):
    common_schema_version: str = Field(min_length=1, max_length=64)
    category_schema_version: str = Field(min_length=1, max_length=64)
    category: ProductBriefCategory
    fields: tuple[ProductBriefFieldOutput, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_schema(self) -> VisionStructuredOutput:
        assert_product_brief_schema(
            category=self.category,
            common_schema_version=self.common_schema_version,
            category_schema_version=self.category_schema_version,
            paths=tuple(field.path for field in self.fields),
        )
        return self


class VisionProviderUsage(ProductBriefContract):
    input_tokens: int = Field(default=0, ge=0, le=2_147_483_647)
    output_tokens: int = Field(default=0, ge=0, le=2_147_483_647)
    total_tokens: int = Field(default=0, ge=0, le=2_147_483_647)

    @model_validator(mode="after")
    def validate_total(self) -> VisionProviderUsage:
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("Vision provider token usage total is inconsistent")
        return self


class VisionProviderError(ProductBriefContract):
    code: str = Field(min_length=1, max_length=128)
    category: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=256)
    retryable: bool
    retry_after_seconds: int | None = Field(default=None, ge=0, le=86400)


class VisionProviderCall(ProductBriefContract):
    call_index: int = Field(ge=0, le=8)
    status: VisionProviderStatus
    provider: str = Field(min_length=1, max_length=64)
    endpoint_region: str = Field(min_length=1, max_length=64)
    endpoint_host: str = Field(min_length=1, max_length=255)
    requested_model: str = Field(min_length=1, max_length=128)
    submitted_model_snapshot: str = Field(min_length=1, max_length=128)
    resolved_model: str | None = Field(default=None, min_length=1, max_length=128)
    prompt_version: str = Field(min_length=1, max_length=64)
    config_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_id: str | None = Field(default=None, min_length=1, max_length=256)
    usage: VisionProviderUsage
    latency_ms: int = Field(ge=0, le=2_147_483_647)
    request_artifact: ProviderArtifactReference
    response_artifact: ProviderArtifactReference | None
    error: VisionProviderError | None

    @model_validator(mode="after")
    def validate_call(self) -> VisionProviderCall:
        if self.status == VisionProviderStatus.SUCCEEDED and self.response_artifact is None:
            raise ValueError("successful Vision provider calls require a response artifact")
        if self.status == VisionProviderStatus.UNKNOWN and (
            self.error is None or self.error.retryable
        ):
            raise ValueError("UNKNOWN Vision provider calls must be non-retryable")
        return self


class VisionProviderOutcome(ProductBriefContract):
    status: VisionProviderStatus
    provider: str = Field(min_length=1, max_length=64)
    endpoint_region: str = Field(min_length=1, max_length=64)
    endpoint_host: str = Field(min_length=1, max_length=255)
    requested_model: str = Field(min_length=1, max_length=128)
    submitted_model_snapshot: str = Field(min_length=1, max_length=128)
    resolved_model: str | None = Field(default=None, min_length=1, max_length=128)
    prompt_version: str = Field(min_length=1, max_length=64)
    config_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_id: str | None = Field(default=None, min_length=1, max_length=256)
    usage: VisionProviderUsage
    latency_ms: int = Field(ge=0)
    request_artifact: ProviderArtifactReference
    response_artifact: ProviderArtifactReference | None
    output: VisionStructuredOutput | None
    error: VisionProviderError | None
    calls: tuple[VisionProviderCall, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_outcome(self) -> VisionProviderOutcome:
        if self.status == VisionProviderStatus.SUCCEEDED:
            if self.output is None or self.error is not None or self.response_artifact is None:
                raise ValueError(
                    "successful Vision outcome requires output, a response artifact, and no error"
                )
        elif self.output is not None or self.error is None:
            raise ValueError("failed Vision outcome requires an error and no output")
        if self.status == VisionProviderStatus.UNKNOWN and self.error.retryable:
            raise ValueError("UNKNOWN Vision provider outcomes must be non-retryable")
        return self


class VisionCallLifecycle(Protocol):
    def store_artifact(
        self,
        artifact: ProviderArtifactWrite,
    ) -> ProviderArtifactReference | None: ...

    def before_submission(self, call_index: int) -> None: ...

    def persist_completed_call(self, call: VisionProviderCall) -> None: ...


class VisionAnalyzer(Protocol):
    @property
    def configured_identity(self) -> VisionAnalyzerIdentity: ...

    def analyze(
        self,
        request: VisionAnalysisRequest,
        *,
        lifecycle: VisionCallLifecycle | None = None,
    ) -> VisionProviderOutcome: ...


class ProductBriefAnalysisRequestV1(ProductBriefContract):
    workflow_id: str = Field(min_length=1, max_length=36)
    product_id: str = Field(min_length=1, max_length=36)
    asset_version_ids: tuple[str, ...] = Field(min_length=1, max_length=8)
    expected_workflow_version: int = Field(ge=1)

    @field_validator("asset_version_ids")
    @classmethod
    def unique_asset_versions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("ProductBrief source Asset Versions must be unique")
        return value


class ProductBriefEvidenceResponseV1(ProductBriefContract):
    id: str
    source_asset_version_id: str
    kind: ProductBriefEvidenceKind
    reference: str
    region: tuple[float, float, float, float] | None
    excerpt_sha256: str | None


class ProductBriefFieldResponseV1(ProductBriefContract):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        json_schema_extra=_FIELD_VALUE_KIND_SCHEMA_EXTENSION,
    )

    id: str
    path: str
    value: ProductBriefFieldValueV1
    confidence: Decimal
    source: ProductBriefFieldSource
    conflict: ProductBriefFieldConflict
    review_required: bool
    sensitive: bool
    review_reasons: tuple[str, ...]
    evidence: tuple[ProductBriefEvidenceResponseV1, ...]

    @model_validator(mode="after")
    def validate_value_shape(self) -> ProductBriefFieldResponseV1:
        validate_product_brief_field_value(
            self.path,
            self.value.model_dump(mode="json"),
        )
        return self


class ProductBriefProviderCallResponseV1(ProductBriefContract):
    provider: str = Field(min_length=1, max_length=64)
    requested_model: str = Field(min_length=1, max_length=128)
    resolved_model: str | None = Field(min_length=1, max_length=128)
    latency_ms: int = Field(ge=0, le=2_147_483_647)


class ProductBriefVersionSummaryResponseV1(ProductBriefContract):
    id: str = Field(min_length=1, max_length=36)
    product_brief_id: str = Field(min_length=1, max_length=36)
    version_number: int = Field(ge=1, le=2_147_483_647)
    supersedes_version_id: str | None = Field(min_length=1, max_length=36)
    effective_state: ProductBriefState
    category: ProductBriefCategory
    common_schema_version: str = Field(min_length=1, max_length=64)
    category_schema_version: str = Field(min_length=1, max_length=64)
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    changed_field_paths: tuple[
        Annotated[str, Field(min_length=1, max_length=160)],
        ...,
    ] = Field(min_length=1, max_length=64)
    confirmation_required: bool
    unresolved_field_count: int = Field(ge=0, le=64)
    review_policy_version: str = Field(min_length=1, max_length=64)
    source: ProductBriefVersionSource
    prompt_version: str | None = Field(min_length=1, max_length=64)
    provider_call: ProductBriefProviderCallResponseV1 | None
    actor_id: str = Field(min_length=1, max_length=128)
    revision_reason: str | None = Field(min_length=1, max_length=512)
    retention_class: RetentionClass
    retention_deadline: datetime | None
    created_at: datetime


class ProductBriefVersionResponseV1(ProductBriefVersionSummaryResponseV1):
    fields: tuple[ProductBriefFieldResponseV1, ...] = Field(
        min_length=1,
        max_length=64,
    )


class ProductBriefResponseV1(ProductBriefContract):
    id: str
    workspace_id: str
    workflow_id: str
    product_id: str
    operation_id: str
    state: ProductBriefState
    current_version_id: str | None
    confirmed_version_id: str | None
    version: int
    retention_class: RetentionClass
    retention_deadline: datetime | None
    created_at: datetime
    updated_at: datetime
    current_version: ProductBriefVersionResponseV1 | None
    confirmed_version: ProductBriefVersionResponseV1 | None


class ProductBriefAnalysisAcceptedV1(ProductBriefContract):
    product_brief: ProductBriefResponseV1
    operation_id: str
    operation_state: str


class ProductBriefVersionListResponseV1(ProductBriefContract):
    items: tuple[ProductBriefVersionSummaryResponseV1, ...] = Field(max_length=100)
    next_cursor: int | None = Field(ge=1, le=2_147_483_647)


class ProductBriefEvidenceRevisionV1(ProductBriefContract):
    source_asset_version_id: str = Field(min_length=1, max_length=36)
    kind: ProductBriefEvidenceKind
    reference: str = Field(min_length=1, max_length=512)
    region: tuple[float, float, float, float] | None = None
    excerpt_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_reference(self) -> ProductBriefEvidenceRevisionV1:
        validate_product_brief_evidence_reference(self.kind, self.reference)
        return self


class ProductBriefFieldRevisionV1(ProductBriefContract):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        json_schema_extra=_FIELD_VALUE_KIND_SCHEMA_EXTENSION,
    )

    path: str = Field(min_length=1, max_length=160)
    value: ProductBriefFieldValueV1
    confidence: Decimal = Field(default=Decimal("1"), ge=0, le=1)
    conflict: ProductBriefFieldConflict = ProductBriefFieldConflict.RESOLVED
    review_required: bool = False
    sensitive: bool
    evidence: tuple[ProductBriefEvidenceRevisionV1, ...] = Field(
        min_length=1,
        max_length=32,
    )

    @model_validator(mode="after")
    def validate_value_shape(self) -> ProductBriefFieldRevisionV1:
        validate_product_brief_field_value(
            self.path,
            self.value.model_dump(mode="json"),
        )
        return self


class ProductBriefRevisionRequestV1(ProductBriefContract):
    expected_product_brief_version: int = Field(ge=1)
    base_version_id: str = Field(min_length=1, max_length=36)
    reason: str = Field(min_length=3, max_length=512)
    fields: tuple[ProductBriefFieldRevisionV1, ...] = Field(min_length=1, max_length=64)

    @field_validator("fields")
    @classmethod
    def unique_paths(
        cls,
        value: tuple[ProductBriefFieldRevisionV1, ...],
    ) -> tuple[ProductBriefFieldRevisionV1, ...]:
        paths = [field.path for field in value]
        if len(set(paths)) != len(paths):
            raise ValueError("ProductBrief revision paths must be unique")
        return value


class ProductBriefConfirmationRequestV1(ProductBriefContract):
    expected_product_brief_version: int = Field(ge=1)
    product_brief_version_id: str = Field(min_length=1, max_length=36)
    expected_workflow_version: int = Field(ge=1)
    reason_code: str | None = Field(default=None, min_length=1, max_length=128)
    comment_ref: str | None = Field(default=None, min_length=1, max_length=512)


class ProductBriefConfirmationResponseV1(ProductBriefContract):
    product_brief: ProductBriefResponseV1
    workflow_id: str
    workflow_status: str
    workflow_version: int
    confirmation_id: str
