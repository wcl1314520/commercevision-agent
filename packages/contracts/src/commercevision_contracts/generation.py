"""Strict public contracts for approved-plan generation batches."""

from __future__ import annotations

from datetime import datetime

from commercevision_domain.operations import OperationKind, OperationState
from pydantic import BaseModel, ConfigDict, Field

from .workspace_identity import WorkspaceId

UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
TOKEN_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"


class GenerationContractV1(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApprovedPlanGenerationRequestV1(GenerationContractV1):
    workflow_id: str = Field(pattern=UUID_PATTERN)
    expected_workflow_version: int = Field(ge=1, le=2_147_483_647, strict=True)
    creative_plan_id: str = Field(pattern=UUID_PATTERN)
    creative_plan_version_id: str = Field(pattern=UUID_PATTERN)
    creative_plan_version: int = Field(ge=1, le=2_147_483_647, strict=True)
    approval_id: str = Field(pattern=UUID_PATTERN)
    direction_key: str = Field(pattern=TOKEN_PATTERN)
    tool_intent_key: str = Field(pattern=TOKEN_PATTERN)
    route_decision_sha256: str = Field(pattern=SHA256_PATTERN)


class GenerationOperationResponseV1(GenerationContractV1):
    id: str = Field(pattern=UUID_PATTERN)
    kind: OperationKind
    state: OperationState
    attempt_count: int = Field(ge=0, strict=True)
    max_attempts: int = Field(ge=1, strict=True)
    execution_deadline_at: datetime
    created_at: datetime
    updated_at: datetime
    version: int = Field(ge=1, strict=True)


class GenerationCandidateSlotResponseV1(GenerationContractV1):
    id: str = Field(pattern=UUID_PATTERN)
    candidate_index: int = Field(ge=0, le=15, strict=True)
    logical_identity_sha256: str = Field(pattern=SHA256_PATTERN)
    operation: GenerationOperationResponseV1


class GenerationBatchResponseV1(GenerationContractV1):
    id: str = Field(pattern=UUID_PATTERN)
    batch_sha256: str = Field(pattern=SHA256_PATTERN)
    workspace_id: WorkspaceId
    workflow_id: str = Field(pattern=UUID_PATTERN)
    workflow_version: int = Field(ge=1, le=2_147_483_647, strict=True)
    creative_plan_version_id: str = Field(pattern=UUID_PATTERN)
    plan_approval_id: str = Field(pattern=UUID_PATTERN)
    direction_key: str = Field(pattern=TOKEN_PATTERN)
    tool_intent_key: str = Field(pattern=TOKEN_PATTERN)
    tool_intent_sha256: str = Field(pattern=SHA256_PATTERN)
    prompt_sha256: str = Field(pattern=SHA256_PATTERN)
    context_sha256: str = Field(pattern=SHA256_PATTERN)
    route_decision_sha256: str = Field(pattern=SHA256_PATTERN)
    route_request_sha256: str = Field(pattern=SHA256_PATTERN)
    operation_kind: OperationKind
    authorized_asset_version_ids: list[str] = Field(max_length=16)
    candidate_count: int = Field(ge=1, le=16, strict=True)
    route_policy_version: str = Field(pattern=TOKEN_PATTERN)
    tool_policy_version: str = Field(pattern=TOKEN_PATTERN)
    rights_policy_version: str = Field(pattern=TOKEN_PATTERN)
    safety_policy_version: str = Field(pattern=TOKEN_PATTERN)
    workflow_deadline: datetime
    source_rights_deadline: datetime | None
    retention_deadline: datetime
    created_by: str = Field(pattern=TOKEN_PATTERN)
    created_at: datetime
    slots: list[GenerationCandidateSlotResponseV1] = Field(min_length=1, max_length=16)
