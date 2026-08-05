"""Strict HTTP-neutral response contracts for Creative Plans."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from commercevision_domain import CreativePlanSource, ImageRole
from pydantic import BaseModel, ConfigDict, Field

from .workspace_identity import WorkspaceId

UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
TOKEN_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"


class CreativePlanContractV1(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreativePlanToolIntentV1(CreativePlanContractV1):
    intent_key: str = Field(pattern=TOKEN_PATTERN)
    tool_name: str = Field(pattern=TOKEN_PATTERN)
    schema_version: str = Field(pattern=TOKEN_PATTERN)
    purpose: str = Field(min_length=1, max_length=512)
    arguments: dict[str, Any]
    estimated_cost_units: int = Field(ge=1, le=1_000_000, strict=True)


class CreativePlanCitationSelectionV1(CreativePlanContractV1):
    citation_id: str = Field(pattern=TOKEN_PATTERN)
    reason: str = Field(min_length=1, max_length=512)


class CreativePlanDirectionV1(CreativePlanContractV1):
    key: str = Field(pattern=TOKEN_PATTERN)
    image_role: ImageRole
    scene: str = Field(min_length=1, max_length=1024)
    composition: str = Field(min_length=1, max_length=1024)
    camera: str = Field(min_length=1, max_length=1024)
    lighting: str = Field(min_length=1, max_length=1024)
    color_direction: str = Field(min_length=1, max_length=1024)
    product_constraints: list[str] = Field(min_length=1, max_length=32)
    required_elements: list[str] = Field(min_length=1, max_length=32)
    prohibited_elements: list[str] = Field(max_length=32)
    citation_selections: list[CreativePlanCitationSelectionV1] = Field(max_length=32)
    candidate_count: int = Field(ge=1, le=16, strict=True)
    quality_targets: list[str] = Field(min_length=1, max_length=32)
    repair_scope: list[str] = Field(max_length=32)
    tool_intents: list[CreativePlanToolIntentV1] = Field(max_length=16)


class CreativePlanPayloadV1(CreativePlanContractV1):
    schema_version: str = Field(pattern=TOKEN_PATTERN)
    directions: list[CreativePlanDirectionV1] = Field(min_length=1, max_length=12)


class CreativePlanProvenanceV1(CreativePlanContractV1):
    product_brief_id: str = Field(pattern=UUID_PATTERN)
    product_brief_version: int = Field(ge=1, strict=True)
    product_brief_sha256: str = Field(pattern=SHA256_PATTERN)
    brand_profile_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    brand_profile_version: int | None = Field(default=None, ge=1, strict=True)
    brand_profile_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    retrieval_run_id: str = Field(pattern=UUID_PATTERN)
    retrieval_citation_ids: list[str] = Field(max_length=32)
    context_policy_version: str = Field(pattern=TOKEN_PATTERN)
    context_sha256: str = Field(pattern=SHA256_PATTERN)
    prompt_id: str = Field(pattern=TOKEN_PATTERN)
    prompt_revision: str = Field(pattern=TOKEN_PATTERN)
    prompt_sha256: str = Field(pattern=SHA256_PATTERN)


class CreativePlanVersionResponseV1(CreativePlanContractV1):
    id: str = Field(pattern=UUID_PATTERN)
    workspace_id: WorkspaceId
    workflow_id: str = Field(pattern=UUID_PATTERN)
    creative_plan_id: str = Field(pattern=UUID_PATTERN)
    version_number: int = Field(ge=1, strict=True)
    supersedes_version_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    source: CreativePlanSource
    payload: CreativePlanPayloadV1
    provenance: CreativePlanProvenanceV1
    payload_sha256: str = Field(pattern=SHA256_PATTERN)
    actor_id: str = Field(pattern=TOKEN_PATTERN)
    revision_reason: str | None = Field(default=None, min_length=1, max_length=512)
    created_at: datetime


class CreativePlanHeadResponseV1(CreativePlanContractV1):
    workspace_id: WorkspaceId
    workflow_id: str = Field(pattern=UUID_PATTERN)
    creative_plan_id: str = Field(pattern=UUID_PATTERN)
    current_version_id: str = Field(pattern=UUID_PATTERN)
    current_version_number: int = Field(ge=1, strict=True)
    version: int = Field(ge=1, strict=True)
    retain_until: datetime
    created_at: datetime
    updated_at: datetime


class CreativePlanCurrentResponseV1(CreativePlanContractV1):
    head: CreativePlanHeadResponseV1
    version: CreativePlanVersionResponseV1


class CreativePlanVersionListResponseV1(CreativePlanContractV1):
    items: list[CreativePlanVersionResponseV1] = Field(max_length=100)
    next_cursor: str | None = Field(default=None, min_length=1, max_length=256)


class CreativePlanCreateRequestV1(CreativePlanContractV1):
    workflow_id: str = Field(pattern=UUID_PATTERN)
    creative_plan_id: str = Field(pattern=UUID_PATTERN)
    payload: CreativePlanPayloadV1
    provenance: CreativePlanProvenanceV1
    expected_workflow_version: int = Field(ge=1, strict=True)
    expected_head_version: Literal[0]


class CreativePlanRevisionRequestV1(CreativePlanContractV1):
    workflow_id: str = Field(pattern=UUID_PATTERN)
    payload: CreativePlanPayloadV1
    revision_reason: str = Field(min_length=1, max_length=512)
    expected_workflow_version: int = Field(ge=1, strict=True)
    expected_head_version: int = Field(ge=1, le=1_000_000, strict=True)
