"""Strict HTTP-neutral contracts for Prompt Registry revisions."""

from __future__ import annotations

from datetime import datetime

from commercevision_domain import PromptRevisionStatus
from pydantic import BaseModel, ConfigDict, Field

from .workspace_identity import WorkspaceId

TOKEN_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
VARIABLE_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]{0,63}$"
SEMANTIC_REVISION_PATTERN = (
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
SHA256_PATTERN = r"^[0-9a-f]{64}$"
UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


class PromptRegistryContractV1(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PromptTemplateVariableV1(PromptRegistryContractV1):
    name: str = Field(pattern=VARIABLE_PATTERN)
    required: bool = Field(strict=True)


class PromptRevisionCreateRequestV1(PromptRegistryContractV1):
    prompt_id: str = Field(pattern=TOKEN_PATTERN)
    semantic_revision: str = Field(pattern=SEMANTIC_REVISION_PATTERN)
    node: str = Field(pattern=TOKEN_PATTERN)
    category_applicability: list[str] = Field(min_length=1, max_length=32)
    model_family_applicability: list[str] = Field(min_length=1, max_length=32)
    input_schema_version: str = Field(pattern=TOKEN_PATTERN)
    output_schema_version: str = Field(pattern=TOKEN_PATTERN)
    policy_version: str = Field(pattern=TOKEN_PATTERN)
    content: str = Field(min_length=1, max_length=32_768)
    variables: list[PromptTemplateVariableV1] = Field(max_length=64)
    change_summary: str = Field(min_length=1, max_length=512)


class PromptRevisionTransitionRequestV1(PromptRegistryContractV1):
    expected_version: int = Field(ge=1, strict=True)


class PromptProductionSelectionRequestV1(PromptRegistryContractV1):
    revision_id: str = Field(pattern=UUID_PATTERN)
    expected_pointer_version: int = Field(ge=1, strict=True)


class PromptProductionPointerResponseV1(PromptRegistryContractV1):
    workspace_id: WorkspaceId
    prompt_id: str = Field(pattern=TOKEN_PATTERN)
    node: str = Field(pattern=TOKEN_PATTERN)
    revision_id: str = Field(pattern=UUID_PATTERN)
    semantic_revision: str = Field(pattern=SEMANTIC_REVISION_PATTERN)
    content_sha256: str = Field(pattern=SHA256_PATTERN)
    version: int = Field(ge=1, strict=True)
    updated_by: str = Field(min_length=1, max_length=128)
    updated_at: datetime


class PromptRevisionResponseV1(PromptRegistryContractV1):
    id: str = Field(pattern=UUID_PATTERN)
    workspace_id: WorkspaceId
    prompt_id: str = Field(pattern=TOKEN_PATTERN)
    semantic_revision: str = Field(pattern=SEMANTIC_REVISION_PATTERN)
    node: str = Field(pattern=TOKEN_PATTERN)
    category_applicability: list[str] = Field(min_length=1, max_length=32)
    model_family_applicability: list[str] = Field(min_length=1, max_length=32)
    input_schema_version: str = Field(pattern=TOKEN_PATTERN)
    output_schema_version: str = Field(pattern=TOKEN_PATTERN)
    policy_version: str = Field(pattern=TOKEN_PATTERN)
    content: str = Field(min_length=1, max_length=32_768)
    variables: list[PromptTemplateVariableV1] = Field(max_length=64)
    content_sha256: str = Field(pattern=SHA256_PATTERN)
    status: PromptRevisionStatus
    version: int = Field(ge=1, strict=True)
    created_by: str = Field(min_length=1, max_length=128)
    change_summary: str = Field(min_length=1, max_length=512)
    created_at: datetime
    updated_at: datetime
    submitted_by: str | None = Field(default=None, min_length=1, max_length=128)
    submitted_at: datetime | None = None
    reviewed_by: str | None = Field(default=None, min_length=1, max_length=128)
    reviewed_at: datetime | None = None
    published_by: str | None = Field(default=None, min_length=1, max_length=128)
    published_at: datetime | None = None
    deprecated_by: str | None = Field(default=None, min_length=1, max_length=128)
    deprecated_at: datetime | None = None
