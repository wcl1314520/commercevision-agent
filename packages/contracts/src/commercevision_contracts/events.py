"""Versioned event contracts shared by producers, routing, and workers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal
from unicodedata import category

from commercevision_domain import (
    UUID_PATTERN,
    ApprovalDecision,
    ApprovalType,
    AssetDeletionReason,
    OperationKind,
    RetentionClass,
    WorkflowStatus,
    canonicalize_uuid,
)
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    RootModel,
    field_validator,
    model_validator,
)

from .workspace_identity import WorkspaceId


class EventQueue(StrEnum):
    WORKFLOW = "workflow"
    ASSET = "asset"
    GENERATION = "generation"
    INDEX = "index"
    MAINTENANCE = "maintenance"


class EventHandling(StrEnum):
    COMMAND = "command"
    OBSERVATION = "observation"


class EventType(StrEnum):
    WORKFLOW_RUN_REQUESTED = "workflow.run.requested"
    WORKFLOW_RESUME_REQUESTED = "workflow.resume.requested"
    WORKFLOW_NODE_STARTED = "workflow.node.started"
    WORKFLOW_NODE_COMPLETED = "workflow.node.completed"
    WORKFLOW_HUMAN_INPUT_REQUIRED = "workflow.human_input.required"
    WORKFLOW_HUMAN_INPUT_RECEIVED = "workflow.human_input.received"
    WORKFLOW_FAILED = "workflow.failed"
    WORKFLOW_CANCELLED = "workflow.cancelled"

    ASSET_UPLOAD_FINALIZED = "asset.upload.finalized"
    ASSET_VALIDATION_REQUESTED = "asset.validation.requested"
    ASSET_VALIDATION_COMPLETED = "asset.validation.completed"
    ASSET_VALIDATION_FAILED = "asset.validation.failed"
    ASSET_RIGHTS_CHANGED = "asset.rights.changed"
    ASSET_RIGHTS_EXPIRED = "asset.rights.expired"
    PRODUCT_BRIEF_REQUESTED = "product-brief.requested"
    PRODUCT_BRIEF_AWAITING_CONFIRMATION = "product-brief.awaiting-confirmation"
    PRODUCT_BRIEF_CONFIRMED = "product-brief.confirmed"
    BRAND_PROFILE_PUBLISHED = "brand-profile.published"
    ASSET_INDEX_REQUESTED = "asset.index.requested"
    ASSET_INDEX_COMPLETED = "asset.index.completed"
    ASSET_INDEX_DELETE_REQUESTED = "asset.index.delete-requested"
    COLLECTION_REBUILD_REQUESTED = "collection-rebuild.requested"
    COLLECTION_REBUILD_PROGRESSED = "collection-rebuild.progressed"
    COLLECTION_REBUILD_COMPLETED = "collection-rebuild.completed"
    ASSET_DELETE_REQUESTED = "asset.delete.requested"
    ASSET_DELETE_COMPLETED = "asset.delete.completed"
    RECONCILIATION_REQUESTED = "reconciliation.requested"
    OPERATION_RECOVERY_REQUESTED = "operation.recovery.requested"
    DEAD_LETTER_REPLAY_RECORDED = "dead-letter.replay.recorded"
    GENERATION_CANDIDATE_REQUESTED = "generation.candidate.requested"
    GENERATION_CANDIDATE_READY = "generation.candidate.ready"


class CompatibleEventPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")


class StrictEventPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class WorkflowRunRequestedPayload(CompatibleEventPayload):
    workflow_id: str = Field(min_length=1, max_length=36)
    action: Literal["start", "retry", "recover"]
    reason: str | None = Field(default=None, min_length=1, max_length=160)
    product_brief_version_id: str | None = Field(default=None, min_length=1, max_length=36)
    product_brief_version_number: int | None = Field(default=None, ge=1)


class WorkflowResumeRequestedPayload(CompatibleEventPayload):
    workflow_id: str = Field(min_length=1, max_length=36)
    approval_id: str = Field(min_length=1, max_length=36)
    approval_type: ApprovalType
    decision: ApprovalDecision
    expected_workflow_version: int = Field(ge=1)
    resulting_workflow_version: int = Field(ge=1)
    subject_id: str = Field(min_length=1, max_length=128)
    subject_version: int = Field(ge=1)


class WorkflowNodeStartedPayload(CompatibleEventPayload):
    node: str = Field(min_length=1, max_length=128)
    step_id: str = Field(min_length=1, max_length=36)
    step_key: str = Field(min_length=1, max_length=160)


class WorkflowNodeCompletedPayload(CompatibleEventPayload):
    node: str = Field(min_length=1, max_length=128)
    completed_step_id: str = Field(min_length=1, max_length=36)
    status: WorkflowStatus


class WorkflowHumanInputRequiredPayload(CompatibleEventPayload):
    step_id: str = Field(min_length=1, max_length=36)
    step_key: str = Field(min_length=1, max_length=160)


class WorkflowHumanInputReceivedPayload(CompatibleEventPayload):
    step_id: str = Field(min_length=1, max_length=36)
    decision: ApprovalDecision | None = None


class WorkflowFailedPayload(CompatibleEventPayload):
    workflow_id: str = Field(min_length=1, max_length=36)
    step_id: str = Field(min_length=1, max_length=36)
    error_class: str = Field(min_length=1, max_length=160)


class WorkflowCancelledPayload(CompatibleEventPayload):
    workflow_id: str = Field(min_length=1, max_length=36)


class OperationRecoveryReason(StrEnum):
    READY_RETRY = "READY_RETRY"
    EXPIRED_CLAIM = "EXPIRED_CLAIM"
    UNKNOWN_EXTERNAL_OUTCOME = "UNKNOWN_EXTERNAL_OUTCOME"
    RECONCILIATION_PENDING = "RECONCILIATION_PENDING"
    TERMINAL_FAILURE = "TERMINAL_FAILURE"


class OperationRecoveryRequestedPayload(CompatibleEventPayload):
    operation_id: str = Field(min_length=1, max_length=36)
    workspace_id: WorkspaceId
    operation_kind: OperationKind
    recovery_reason: OperationRecoveryReason
    recovery_generation: int = Field(default=0, ge=0)


class DeadLetterReplayRecordedPayload(CompatibleEventPayload):
    source_dead_letter_id: str = Field(min_length=1, max_length=36)
    replay_id: str = Field(min_length=1, max_length=36)
    workspace_id: WorkspaceId
    replay_attempt: int = Field(ge=1)


class GenerationCandidateRequestedPayload(StrictEventPayload):
    workspace_id: WorkspaceId
    workflow_id: str = Field(pattern=UUID_PATTERN)
    generation_batch_id: str = Field(pattern=UUID_PATTERN)
    candidate_slot_id: str = Field(pattern=UUID_PATTERN)
    operation_id: str = Field(pattern=UUID_PATTERN)
    operation_kind: Literal[OperationKind.IMAGE_GENERATION]


class GenerationCandidateReadyPayload(StrictEventPayload):
    workspace_id: WorkspaceId
    workflow_id: str = Field(pattern=UUID_PATTERN)
    generation_batch_id: str = Field(pattern=UUID_PATTERN)
    candidate_slot_id: str = Field(pattern=UUID_PATTERN)
    candidate_image_id: str = Field(pattern=UUID_PATTERN)
    asset_version_id: str = Field(pattern=UUID_PATTERN)
    operation_id: str = Field(pattern=UUID_PATTERN)
    usage_record_id: str = Field(pattern=UUID_PATTERN)


class CollectionRebuildCommand(StrEnum):
    CONTINUE = "CONTINUE"
    VALIDATE = "VALIDATE"
    RETIRE = "RETIRE"


class CollectionRebuildRequestedPayload(StrictEventPayload):
    workspace_id: WorkspaceId
    rebuild_id: str = Field(pattern=UUID_PATTERN)
    operation_id: str = Field(pattern=UUID_PATTERN)
    generation: int = Field(ge=1, strict=True)
    command: CollectionRebuildCommand


class CollectionRebuildProgressedPayload(StrictEventPayload):
    workspace_id: WorkspaceId
    rebuild_id: str = Field(pattern=UUID_PATTERN)
    operation_id: str = Field(pattern=UUID_PATTERN)
    generation: int = Field(ge=1, strict=True)
    state: str = Field(min_length=1, max_length=32)
    processed_count: int = Field(ge=0, strict=True)


class CollectionRebuildCompletedPayload(StrictEventPayload):
    workspace_id: WorkspaceId
    rebuild_id: str = Field(pattern=UUID_PATTERN)
    operation_id: str = Field(pattern=UUID_PATTERN)
    candidate_collection_id: str = Field(pattern=UUID_PATTERN)
    retired_collection_id: str = Field(pattern=UUID_PATTERN)
    vector_kind: str = Field(pattern=r"^(IMAGE|PRODUCT_FUSED)$")
    activated_at: datetime


class AssetUploadFinalizedPayload(CompatibleEventPayload):
    workspace_id: WorkspaceId
    upload_session_id: str = Field(min_length=1, max_length=36)
    asset_id: str = Field(min_length=1, max_length=36)
    asset_version_id: str = Field(min_length=1, max_length=36)
    object_fact_id: str = Field(min_length=1, max_length=36)
    validation_operation_id: str = Field(min_length=1, max_length=36)


class AssetValidationRequestedPayload(CompatibleEventPayload):
    operation_id: str = Field(min_length=1, max_length=36)
    workspace_id: WorkspaceId
    asset_id: str = Field(min_length=1, max_length=36)
    asset_version_id: str = Field(min_length=1, max_length=36)
    object_fact_id: str = Field(min_length=1, max_length=36)
    integrity_policy_version: str = Field(min_length=1, max_length=64)
    validation_policy_version: str | None = Field(default=None, min_length=1, max_length=64)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AssetValidationCompletedPayload(StrictEventPayload):
    workspace_id: WorkspaceId
    asset_id: str = Field(min_length=1, max_length=36)
    asset_version_id: str = Field(min_length=1, max_length=36)
    operation_id: str = Field(min_length=1, max_length=36)
    attempt_number: int = Field(ge=1)
    outcome: Literal["PENDING_RIGHTS", "PENDING_REVIEW"]
    reason_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Z][A-Z0-9_]{0,63}$",
    )


class AssetValidationFailedPayload(StrictEventPayload):
    workspace_id: WorkspaceId
    asset_id: str = Field(min_length=1, max_length=36)
    asset_version_id: str = Field(min_length=1, max_length=36)
    operation_id: str = Field(min_length=1, max_length=36)
    attempt_number: int = Field(
        ge=0,
        description=(
            "Execution attempt that produced the terminal failure; zero means the "
            "operation expired before its first execution claim."
        ),
    )
    outcome: Literal["BLOCKED", "FAILED"]
    reason_code: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Z][A-Z0-9_]{0,63}$",
    )


class AssetRightsChange(StrEnum):
    REGISTERED = "REGISTERED"
    REPLACED = "REPLACED"
    ACTIVATED = "ACTIVATED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"
    ADMINISTRATOR_BLOCKED = "ADMINISTRATOR_BLOCKED"


class AssetRightsConvergence(StrEnum):
    REINDEX = "REINDEX"
    REMOVE_EXTERNAL_DERIVATIVES = "REMOVE_EXTERNAL_DERIVATIVES"


class AssetRightsChangedPayload(StrictEventPayload):
    workspace_id: WorkspaceId
    asset_id: str = Field(min_length=1, max_length=36)
    asset_version_id: str = Field(min_length=1, max_length=36)
    rights_record_id: str | None = Field(default=None, min_length=1, max_length=36)
    rights_record_version: int | None = Field(default=None, ge=1)
    change: Literal[
        "REGISTERED",
        "REPLACED",
        "ACTIVATED",
        "REVOKED",
        "EXPIRED",
        "ADMINISTRATOR_BLOCKED",
    ]
    resulting_asset_state: Literal[
        "PENDING_RIGHTS",
        "AVAILABLE",
        "BLOCKED",
        "RIGHTS_EXPIRED",
    ]
    required_convergence: Literal["REINDEX", "REMOVE_EXTERNAL_DERIVATIVES"]

    @model_validator(mode="after")
    def validate_rights_identity(self) -> AssetRightsChangedPayload:
        if (self.rights_record_id is None) != (self.rights_record_version is None):
            raise ValueError("Rights Record id and version must be present together")
        if (
            self.change != AssetRightsChange.ADMINISTRATOR_BLOCKED.value
            and self.rights_record_id is None
        ):
            raise ValueError("Rights Record identity is required for a rights transition")
        expected_convergence = (
            AssetRightsConvergence.REINDEX.value
            if self.resulting_asset_state == "AVAILABLE"
            else AssetRightsConvergence.REMOVE_EXTERNAL_DERIVATIVES.value
        )
        if self.required_convergence != expected_convergence:
            raise ValueError("Rights convergence contradicts the resulting Asset state")
        required_state = {
            AssetRightsChange.ACTIVATED.value: "AVAILABLE",
            AssetRightsChange.REVOKED.value: "BLOCKED",
            AssetRightsChange.EXPIRED.value: "RIGHTS_EXPIRED",
            AssetRightsChange.ADMINISTRATOR_BLOCKED.value: "BLOCKED",
        }.get(self.change)
        if required_state is not None and self.resulting_asset_state != required_state:
            raise ValueError("Rights change contradicts the resulting Asset state")
        return self


class AssetRightsExpiredPayload(AssetRightsChangedPayload):
    rights_record_id: str = Field(min_length=1, max_length=36)
    rights_record_version: int = Field(ge=1)
    change: Literal["EXPIRED"]
    resulting_asset_state: Literal["RIGHTS_EXPIRED"]
    required_convergence: Literal["REMOVE_EXTERNAL_DERIVATIVES"]


class UploadCleanupReason(StrEnum):
    UPLOAD_EXPIRED = "UPLOAD_EXPIRED"
    UPLOAD_ABORTED = "UPLOAD_ABORTED"
    UPLOAD_PROMOTED = "UPLOAD_PROMOTED"


class AssetDeleteRequestedPayload(CompatibleEventPayload):
    operation_id: str = Field(min_length=1, max_length=36)
    workspace_id: WorkspaceId
    target_type: Literal["UPLOAD_SESSION", "ASSET"]
    target_id: str = Field(min_length=1, max_length=36)
    target_version: int = Field(ge=1)
    reason: UploadCleanupReason | AssetDeletionReason
    asset_version_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    deletion_generation: int | None = Field(default=None, ge=1, strict=True)

    @model_validator(mode="after")
    def validate_target_identity(self) -> AssetDeleteRequestedPayload:
        if self.target_type == "UPLOAD_SESSION":
            if not isinstance(self.reason, UploadCleanupReason):
                raise ValueError("Upload Session cleanup requires an upload cleanup reason")
            if self.asset_version_id is not None or self.deletion_generation is not None:
                raise ValueError("Upload Session cleanup must not carry Asset deletion identity")
            return self
        if not isinstance(self.reason, AssetDeletionReason):
            raise ValueError("Asset cleanup requires an Asset deletion reason")
        if (
            self.asset_version_id is None
            or self.deletion_generation is None
            or self.deletion_generation != self.target_version
        ):
            raise ValueError("Asset cleanup requires one exact deletion generation and version")
        if (
            canonicalize_uuid(self.target_id) != self.target_id
            or canonicalize_uuid(self.asset_version_id) != self.asset_version_id
        ):
            raise ValueError("Asset cleanup identities must be canonical lowercase UUIDs")
        return self


class AssetDeleteCompletedPayload(CompatibleEventPayload):
    """Exact Asset tombstone convergence observed by downstream modules."""

    workspace_id: WorkspaceId
    asset_id: str = Field(pattern=UUID_PATTERN)
    asset_version_id: str = Field(pattern=UUID_PATTERN)
    retention_class: RetentionClass
    deletion_generation: int = Field(ge=1, strict=True)

    @field_validator("asset_id", "asset_version_id")
    @classmethod
    def validate_canonical_ids(cls, value: str) -> str:
        if canonicalize_uuid(value) != value:
            raise ValueError("Asset deletion lineage identifiers must be canonical lowercase UUIDs")
        return value


class ProductBriefRequestedPayload(StrictEventPayload):
    workspace_id: WorkspaceId
    product_brief_id: str = Field(min_length=1, max_length=36)
    product_brief_version: int = Field(ge=1)
    workflow_id: str = Field(min_length=1, max_length=36)
    product_id: str = Field(min_length=1, max_length=36)
    operation_id: str = Field(min_length=1, max_length=36)


class ProductBriefAwaitingConfirmationPayload(StrictEventPayload):
    workspace_id: WorkspaceId
    product_brief_id: str = Field(min_length=1, max_length=36)
    product_brief_version: int = Field(ge=1)
    product_brief_version_id: str = Field(min_length=1, max_length=36)
    product_brief_version_number: int = Field(ge=1)
    workflow_id: str = Field(min_length=1, max_length=36)
    operation_id: str = Field(min_length=1, max_length=36)
    unresolved_field_count: int = Field(ge=0, le=64)
    review_policy_version: str = Field(min_length=1, max_length=64)


class ProductBriefConfirmedPayload(StrictEventPayload):
    workspace_id: WorkspaceId
    product_brief_id: str = Field(min_length=1, max_length=36)
    product_brief_version: int = Field(ge=1)
    product_brief_version_id: str = Field(min_length=1, max_length=36)
    product_brief_version_number: int = Field(ge=1)
    workflow_id: str = Field(min_length=1, max_length=36)
    operation_id: str = Field(min_length=1, max_length=36)
    confirmation_id: str | None = Field(default=None, min_length=1, max_length=36)
    confirmation_source: Literal["POLICY", "HUMAN"]

    @model_validator(mode="after")
    def validate_confirmation_identity(self) -> ProductBriefConfirmedPayload:
        if (self.confirmation_source == "HUMAN") != (self.confirmation_id is not None):
            raise ValueError("human ProductBrief confirmation requires an append-only identity")
        return self


class BrandProfilePublishedPayload(StrictEventPayload):
    workspace_id: WorkspaceId
    profile_id: str = Field(pattern=UUID_PATTERN)
    profile_version_id: str = Field(pattern=UUID_PATTERN)
    profile_version_number: int = Field(ge=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    member_count: int = Field(ge=0, le=64)
    published_by: str = Field(min_length=1, max_length=128)

    @field_validator("profile_id", "profile_version_id")
    @classmethod
    def validate_canonical_ids(cls, value: str) -> str:
        if canonicalize_uuid(value) != value:
            raise ValueError("Brand Profile event identifiers must be canonical lowercase UUIDs")
        return value

    @field_validator("published_by")
    @classmethod
    def validate_published_by(cls, value: str) -> str:
        if value != value.strip() or any(category(character) == "Cc" for character in value):
            raise ValueError("published_by must be trimmed and contain no control characters")
        return value


class AssetIndexRequestedPayload(StrictEventPayload):
    operation_id: str = Field(pattern=UUID_PATTERN)
    operation_epoch: int = Field(ge=1)
    operation_input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    embedding_record_id: str = Field(pattern=UUID_PATTERN)
    workspace_id: WorkspaceId
    asset_id: str = Field(pattern=UUID_PATTERN)
    asset_version_id: str = Field(pattern=UUID_PATTERN)
    asset_version_number: int = Field(ge=1)
    rights_record_id: str = Field(pattern=UUID_PATTERN)
    rights_record_version: int = Field(ge=1)
    collection_id: str = Field(pattern=UUID_PATTERN)
    vector_kind: Literal["IMAGE", "PRODUCT_FUSED"]
    provider: str = Field(min_length=1, max_length=64)
    embedding_input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    embedding_spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    product_brief_version_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    controlled_text_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    @field_validator(
        "operation_id",
        "embedding_record_id",
        "asset_id",
        "asset_version_id",
        "rights_record_id",
        "collection_id",
    )
    @classmethod
    def validate_index_ids(cls, value: str) -> str:
        if canonicalize_uuid(value) != value:
            raise ValueError("index event identifiers must be canonical lowercase UUIDs")
        return value

    @model_validator(mode="after")
    def validate_controlled_product_identity(self) -> AssetIndexRequestedPayload:
        has_controlled_identity = (
            self.product_brief_version_id is not None and self.controlled_text_sha256 is not None
        )
        if self.vector_kind == "PRODUCT_FUSED" and not has_controlled_identity:
            raise ValueError("PRODUCT_FUSED index event requires ProductBrief controlled identity")
        if self.vector_kind == "IMAGE" and (
            self.product_brief_version_id is not None or self.controlled_text_sha256 is not None
        ):
            raise ValueError("IMAGE index event cannot carry ProductBrief controlled identity")
        return self


class AssetIndexTerminalIdentity(StrictEventPayload):
    operation_id: str = Field(pattern=UUID_PATTERN)
    embedding_record_id: str = Field(pattern=UUID_PATTERN)
    workspace_id: WorkspaceId
    asset_id: str = Field(pattern=UUID_PATTERN)
    asset_version_id: str = Field(pattern=UUID_PATTERN)
    collection_id: str = Field(pattern=UUID_PATTERN)
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    embedding_spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    write_generation: int = Field(ge=1)

    @field_validator(
        "operation_id",
        "embedding_record_id",
        "asset_id",
        "asset_version_id",
        "collection_id",
    )
    @classmethod
    def validate_terminal_index_ids(cls, value: str) -> str:
        if canonicalize_uuid(value) != value:
            raise ValueError("index event identifiers must be canonical lowercase UUIDs")
        return value


class AssetIndexCompletedPayload(AssetIndexTerminalIdentity):
    outcome: Literal["INDEXED", "STALE"]


class AssetIndexDeleteRequestedPayload(AssetIndexTerminalIdentity):
    reason: Literal[
        "RIGHTS_INVALID",
        "ASSET_BLOCKED",
        "ASSET_DELETED",
        "SUPERSEDED",
        "RECONCILIATION",
    ]


class PendingPhase2Payload(RootModel[dict[str, JsonValue]]):
    """JSON payload boundary for Phase 2 events whose owning ticket defines fields later."""


@dataclass(frozen=True, slots=True)
class EventContract:
    event_type: EventType
    schema_version: int
    queue: EventQueue
    payload_model: type[BaseModel]
    handling: EventHandling

    def validate_payload(self, payload: object) -> BaseModel:
        return self.payload_model.model_validate(payload)


WORKFLOW_RUN_REQUESTED_V1 = EventContract(
    EventType.WORKFLOW_RUN_REQUESTED,
    1,
    EventQueue.WORKFLOW,
    WorkflowRunRequestedPayload,
    EventHandling.COMMAND,
)
WORKFLOW_RESUME_REQUESTED_V1 = EventContract(
    EventType.WORKFLOW_RESUME_REQUESTED,
    1,
    EventQueue.WORKFLOW,
    WorkflowResumeRequestedPayload,
    EventHandling.COMMAND,
)
WORKFLOW_NODE_STARTED_V1 = EventContract(
    EventType.WORKFLOW_NODE_STARTED,
    1,
    EventQueue.WORKFLOW,
    WorkflowNodeStartedPayload,
    EventHandling.OBSERVATION,
)
WORKFLOW_NODE_COMPLETED_V1 = EventContract(
    EventType.WORKFLOW_NODE_COMPLETED,
    1,
    EventQueue.WORKFLOW,
    WorkflowNodeCompletedPayload,
    EventHandling.OBSERVATION,
)
WORKFLOW_HUMAN_INPUT_REQUIRED_V1 = EventContract(
    EventType.WORKFLOW_HUMAN_INPUT_REQUIRED,
    1,
    EventQueue.WORKFLOW,
    WorkflowHumanInputRequiredPayload,
    EventHandling.OBSERVATION,
)
WORKFLOW_HUMAN_INPUT_RECEIVED_V1 = EventContract(
    EventType.WORKFLOW_HUMAN_INPUT_RECEIVED,
    1,
    EventQueue.WORKFLOW,
    WorkflowHumanInputReceivedPayload,
    EventHandling.OBSERVATION,
)
WORKFLOW_FAILED_V1 = EventContract(
    EventType.WORKFLOW_FAILED,
    1,
    EventQueue.WORKFLOW,
    WorkflowFailedPayload,
    EventHandling.OBSERVATION,
)
WORKFLOW_CANCELLED_V1 = EventContract(
    EventType.WORKFLOW_CANCELLED,
    1,
    EventQueue.WORKFLOW,
    WorkflowCancelledPayload,
    EventHandling.OBSERVATION,
)

PHASE1_EVENT_CONTRACTS = (
    WORKFLOW_RUN_REQUESTED_V1,
    WORKFLOW_RESUME_REQUESTED_V1,
    WORKFLOW_NODE_STARTED_V1,
    WORKFLOW_NODE_COMPLETED_V1,
    WORKFLOW_HUMAN_INPUT_REQUIRED_V1,
    WORKFLOW_HUMAN_INPUT_RECEIVED_V1,
    WORKFLOW_FAILED_V1,
    WORKFLOW_CANCELLED_V1,
)

OPERATION_RECOVERY_REQUESTED_V1 = EventContract(
    EventType.OPERATION_RECOVERY_REQUESTED,
    1,
    EventQueue.MAINTENANCE,
    OperationRecoveryRequestedPayload,
    EventHandling.COMMAND,
)
DEAD_LETTER_REPLAY_RECORDED_V1 = EventContract(
    EventType.DEAD_LETTER_REPLAY_RECORDED,
    1,
    EventQueue.MAINTENANCE,
    DeadLetterReplayRecordedPayload,
    EventHandling.OBSERVATION,
)
ASSET_UPLOAD_FINALIZED_V1 = EventContract(
    EventType.ASSET_UPLOAD_FINALIZED,
    1,
    EventQueue.ASSET,
    AssetUploadFinalizedPayload,
    EventHandling.OBSERVATION,
)
ASSET_VALIDATION_REQUESTED_V1 = EventContract(
    EventType.ASSET_VALIDATION_REQUESTED,
    1,
    EventQueue.ASSET,
    AssetValidationRequestedPayload,
    EventHandling.COMMAND,
)
ASSET_VALIDATION_COMPLETED_V1 = EventContract(
    EventType.ASSET_VALIDATION_COMPLETED,
    1,
    EventQueue.ASSET,
    AssetValidationCompletedPayload,
    EventHandling.OBSERVATION,
)
ASSET_VALIDATION_FAILED_V1 = EventContract(
    EventType.ASSET_VALIDATION_FAILED,
    1,
    EventQueue.ASSET,
    AssetValidationFailedPayload,
    EventHandling.OBSERVATION,
)
ASSET_RIGHTS_CHANGED_V1 = EventContract(
    EventType.ASSET_RIGHTS_CHANGED,
    1,
    EventQueue.ASSET,
    AssetRightsChangedPayload,
    EventHandling.OBSERVATION,
)
ASSET_RIGHTS_EXPIRED_V1 = EventContract(
    EventType.ASSET_RIGHTS_EXPIRED,
    1,
    EventQueue.ASSET,
    AssetRightsExpiredPayload,
    EventHandling.OBSERVATION,
)
ASSET_DELETE_REQUESTED_V1 = EventContract(
    EventType.ASSET_DELETE_REQUESTED,
    1,
    EventQueue.MAINTENANCE,
    AssetDeleteRequestedPayload,
    EventHandling.COMMAND,
)
ASSET_DELETE_COMPLETED_V1 = EventContract(
    EventType.ASSET_DELETE_COMPLETED,
    1,
    EventQueue.MAINTENANCE,
    AssetDeleteCompletedPayload,
    EventHandling.OBSERVATION,
)
PRODUCT_BRIEF_REQUESTED_V1 = EventContract(
    EventType.PRODUCT_BRIEF_REQUESTED,
    1,
    EventQueue.ASSET,
    ProductBriefRequestedPayload,
    EventHandling.COMMAND,
)
PRODUCT_BRIEF_AWAITING_CONFIRMATION_V1 = EventContract(
    EventType.PRODUCT_BRIEF_AWAITING_CONFIRMATION,
    1,
    EventQueue.ASSET,
    ProductBriefAwaitingConfirmationPayload,
    EventHandling.OBSERVATION,
)
PRODUCT_BRIEF_CONFIRMED_V1 = EventContract(
    EventType.PRODUCT_BRIEF_CONFIRMED,
    1,
    EventQueue.ASSET,
    ProductBriefConfirmedPayload,
    EventHandling.OBSERVATION,
)
BRAND_PROFILE_PUBLISHED_V1 = EventContract(
    EventType.BRAND_PROFILE_PUBLISHED,
    1,
    EventQueue.ASSET,
    BrandProfilePublishedPayload,
    EventHandling.OBSERVATION,
)
ASSET_INDEX_REQUESTED_V1 = EventContract(
    EventType.ASSET_INDEX_REQUESTED,
    1,
    EventQueue.INDEX,
    AssetIndexRequestedPayload,
    EventHandling.COMMAND,
)
ASSET_INDEX_COMPLETED_V1 = EventContract(
    EventType.ASSET_INDEX_COMPLETED,
    1,
    EventQueue.INDEX,
    AssetIndexCompletedPayload,
    EventHandling.OBSERVATION,
)
ASSET_INDEX_DELETE_REQUESTED_V1 = EventContract(
    EventType.ASSET_INDEX_DELETE_REQUESTED,
    1,
    EventQueue.INDEX,
    AssetIndexDeleteRequestedPayload,
    EventHandling.COMMAND,
)
COLLECTION_REBUILD_REQUESTED_V1 = EventContract(
    EventType.COLLECTION_REBUILD_REQUESTED,
    1,
    EventQueue.INDEX,
    CollectionRebuildRequestedPayload,
    EventHandling.COMMAND,
)
COLLECTION_REBUILD_PROGRESSED_V1 = EventContract(
    EventType.COLLECTION_REBUILD_PROGRESSED,
    1,
    EventQueue.INDEX,
    CollectionRebuildProgressedPayload,
    EventHandling.OBSERVATION,
)
COLLECTION_REBUILD_COMPLETED_V1 = EventContract(
    EventType.COLLECTION_REBUILD_COMPLETED,
    1,
    EventQueue.INDEX,
    CollectionRebuildCompletedPayload,
    EventHandling.OBSERVATION,
)
GENERATION_CANDIDATE_REQUESTED_V1 = EventContract(
    EventType.GENERATION_CANDIDATE_REQUESTED,
    1,
    EventQueue.GENERATION,
    GenerationCandidateRequestedPayload,
    EventHandling.COMMAND,
)
GENERATION_CANDIDATE_READY_V1 = EventContract(
    EventType.GENERATION_CANDIDATE_READY,
    1,
    EventQueue.WORKFLOW,
    GenerationCandidateReadyPayload,
    EventHandling.OBSERVATION,
)


def _phase2_contract(
    event_type: EventType,
    queue: EventQueue,
    handling: EventHandling,
) -> EventContract:
    return EventContract(event_type, 1, queue, PendingPhase2Payload, handling)


PHASE2_EVENT_CONTRACTS = (
    OPERATION_RECOVERY_REQUESTED_V1,
    DEAD_LETTER_REPLAY_RECORDED_V1,
    ASSET_UPLOAD_FINALIZED_V1,
    ASSET_VALIDATION_REQUESTED_V1,
    ASSET_VALIDATION_COMPLETED_V1,
    ASSET_VALIDATION_FAILED_V1,
    ASSET_RIGHTS_CHANGED_V1,
    ASSET_RIGHTS_EXPIRED_V1,
    PRODUCT_BRIEF_REQUESTED_V1,
    PRODUCT_BRIEF_AWAITING_CONFIRMATION_V1,
    PRODUCT_BRIEF_CONFIRMED_V1,
    BRAND_PROFILE_PUBLISHED_V1,
    ASSET_INDEX_REQUESTED_V1,
    ASSET_INDEX_COMPLETED_V1,
    ASSET_INDEX_DELETE_REQUESTED_V1,
    COLLECTION_REBUILD_REQUESTED_V1,
    COLLECTION_REBUILD_PROGRESSED_V1,
    COLLECTION_REBUILD_COMPLETED_V1,
    GENERATION_CANDIDATE_REQUESTED_V1,
    GENERATION_CANDIDATE_READY_V1,
    ASSET_DELETE_REQUESTED_V1,
    ASSET_DELETE_COMPLETED_V1,
    _phase2_contract(
        EventType.RECONCILIATION_REQUESTED,
        EventQueue.MAINTENANCE,
        EventHandling.COMMAND,
    ),
)

EVENT_CONTRACTS = (*PHASE1_EVENT_CONTRACTS, *PHASE2_EVENT_CONTRACTS)
_EVENT_CONTRACTS_BY_KEY = {
    (contract.event_type.value, contract.schema_version): contract for contract in EVENT_CONTRACTS
}


def event_contract_for(event_type: EventType | str, schema_version: int) -> EventContract:
    return _EVENT_CONTRACTS_BY_KEY[(str(event_type), schema_version)]


def event_contracts_for_type(event_type: EventType | str) -> tuple[EventContract, ...]:
    event_type_value = str(event_type)
    return tuple(
        contract for contract in EVENT_CONTRACTS if contract.event_type.value == event_type_value
    )
