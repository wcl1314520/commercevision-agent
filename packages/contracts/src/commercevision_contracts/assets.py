"""Versioned public contracts for direct upload and quarantined assets."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from commercevision_domain import (
    UUID_PATTERN,
    AssetKind,
    AssetObjectState,
    AssetState,
    OperationState,
    RetentionClass,
    UploadSessionState,
    canonicalize_uuid,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .workspace_identity import WorkspaceId

SHA256_PATTERN = r"^[0-9a-f]{64}$"
SUPPORTED_IMAGE_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})


class AssetContractV1(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UploadSessionCreateRequestV1(AssetContractV1):
    retention_class: RetentionClass
    asset_kind: AssetKind = AssetKind.IMAGE
    filename: str = Field(min_length=1, max_length=255)
    declared_mime: str = Field(min_length=1, max_length=128)
    byte_length: int = Field(ge=1, le=10 * 1024 * 1024)
    sha256: str = Field(pattern=SHA256_PATTERN)
    workflow_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    product_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    sku_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    category: str = Field(min_length=1, max_length=128)
    role: str = Field(min_length=1, max_length=64)

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("filename must not contain control characters")
        if "/" in value or "\\" in value:
            raise ValueError("filename must not contain path separators")
        return value

    @field_validator("workflow_id", "product_id", "sku_id")
    @classmethod
    def validate_association_id(cls, value: str | None) -> str | None:
        return canonicalize_uuid(value) if value is not None else None

    @model_validator(mode="after")
    def validate_associations(self) -> UploadSessionCreateRequestV1:
        if self.retention_class == RetentionClass.TASK and self.workflow_id is None:
            raise ValueError("Task Assets require workflow_id")
        if self.retention_class == RetentionClass.FOUNDATION and self.workflow_id is not None:
            raise ValueError("Foundation Assets must not reference a Workflow")
        if self.sku_id is not None and self.product_id is None:
            raise ValueError("sku_id requires product_id")
        return self


class UploadSessionMutationRequestV1(AssetContractV1):
    expected_version: int = Field(ge=1)


class PresignedUploadV1(BaseModel):
    method: Literal["PUT"]
    url: str = Field(min_length=1)
    required_headers: dict[str, str]
    maximum_bytes: int = Field(ge=1)
    checksum_algorithm: Literal["SHA-256"]
    expires_at: datetime


class UploadSessionResponseV1(BaseModel):
    id: str
    workspace_id: WorkspaceId
    reserved_asset_id: str
    retention_class: RetentionClass
    asset_kind: AssetKind
    filename: str
    declared_mime: str
    expected_byte_length: int
    expected_sha256: str
    workflow_id: str | None
    product_id: str | None
    sku_id: str | None
    category: str
    role: str
    upload_policy_version: str
    integrity_policy_version: str
    status: UploadSessionState
    failure_code: str | None
    asset_version_id: str | None
    validation_operation_id: str | None
    cleanup_operation_id: str | None
    expires_at: datetime
    version: int
    created_at: datetime
    updated_at: datetime


class UploadSessionCreateResponseV1(UploadSessionResponseV1):
    upload: PresignedUploadV1


class AssetVersionResponseV1(BaseModel):
    id: str
    workspace_id: WorkspaceId
    asset_id: str
    version_number: int
    upload_session_id: str
    filename: str
    sha256: str
    byte_size: int
    declared_mime: str
    detected_mime: str
    image_format: str
    width: int
    height: int
    frame_count: int
    category: str
    role: str
    integrity_policy_version: str
    object_state: AssetObjectState
    created_at: datetime


class AssetResponseV1(BaseModel):
    id: str
    workspace_id: WorkspaceId
    retention_class: RetentionClass
    asset_kind: AssetKind
    workflow_id: str | None
    product_id: str | None
    sku_id: str | None
    status: AssetState
    current_version_id: str
    retention_deadline: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime
    current_version: AssetVersionResponseV1 | None = None


class ValidationOperationSummaryV1(BaseModel):
    id: str
    state: OperationState
    target_id: str
    target_version: int
    version: int


class UploadFinalizeResponseV1(BaseModel):
    upload_session: UploadSessionResponseV1
    asset: AssetResponseV1
    asset_version: AssetVersionResponseV1
    validation_operation: ValidationOperationSummaryV1
