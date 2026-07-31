"""Strict v1 contracts for Brand Profile drafting and immutable publication."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from commercevision_domain import (
    UUID_PATTERN,
    BrandProfileMemberRole,
    BrandProfileState,
    BrandRuleScope,
    RightsDecisionCode,
    canonicalize_uuid,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .workspace_identity import WorkspaceId

TOKEN_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
COLOR_PATTERN = r"^#[0-9A-F]{6}(?:[0-9A-F]{2})?$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
CURSOR_PATTERN = r"^v1\.[A-Za-z0-9_-]{1,64}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$"
_CONTROL_PATTERN = re.compile(r"[\x00-\x1f\x7f]")


def _validated_text(value: str, field: str) -> str:
    if value != value.strip() or _CONTROL_PATTERN.search(value) is not None:
        raise ValueError(f"{field} must be trimmed and contain no control characters")
    return value


def _utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must be timezone-aware UTC")
    return value


class BrandProfileContractV1(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BrandRuleV1(BrandProfileContractV1):
    code: str = Field(pattern=TOKEN_PATTERN)
    scope: BrandRuleScope
    instruction: str = Field(min_length=1, max_length=1024)

    @field_validator("instruction")
    @classmethod
    def validate_instruction(cls, value: str) -> str:
        return _validated_text(value, "instruction")


class BrandColorV1(BrandProfileContractV1):
    name: str = Field(min_length=1, max_length=64)
    value: str = Field(pattern=COLOR_PATTERN)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _validated_text(value, "name")


class BrandProfileMemberSelectionV1(BrandProfileContractV1):
    asset_version_id: str = Field(pattern=UUID_PATTERN)
    role: BrandProfileMemberRole

    @field_validator("asset_version_id")
    @classmethod
    def validate_asset_version_id(cls, value: str) -> str:
        return canonicalize_uuid(value)


class BrandProfileDraftV1(BrandProfileContractV1):
    rules: list[BrandRuleV1] = Field(max_length=64)
    approved_colors: list[BrandColorV1] = Field(max_length=32)
    required_marks: list[str] = Field(max_length=64)
    prohibited_elements: list[str] = Field(max_length=64)
    tone_constraints: list[str] = Field(max_length=64)
    copy_constraints: list[str] = Field(max_length=64)
    purpose: str = Field(pattern=TOKEN_PATTERN)
    provider: str = Field(pattern=TOKEN_PATTERN)
    requires_derivative: bool = Field(strict=True)
    selected_assets: list[BrandProfileMemberSelectionV1] = Field(max_length=64)

    @field_validator(
        "required_marks",
        "prohibited_elements",
        "tone_constraints",
        "copy_constraints",
    )
    @classmethod
    def validate_text_items(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("Brand Profile text items must not contain duplicates")
        for value in values:
            if not value or len(value) > 512:
                raise ValueError("Brand Profile text item must contain 1-512 characters")
            _validated_text(value, "Brand Profile text item")
        return values

    @model_validator(mode="after")
    def validate_unique_business_keys(self) -> BrandProfileDraftV1:
        if len({rule.code for rule in self.rules}) != len(self.rules):
            raise ValueError("Brand rule codes must be unique")
        if len({color.name for color in self.approved_colors}) != len(self.approved_colors):
            raise ValueError("Brand color names must be unique")
        version_ids = [selection.asset_version_id for selection in self.selected_assets]
        if len(set(version_ids)) != len(version_ids):
            raise ValueError("selected Asset Version may appear only once")
        return self


class BrandProfileCreateRequestV1(BrandProfileContractV1):
    brand: str = Field(min_length=1, max_length=128)
    profile_key: str = Field(pattern=TOKEN_PATTERN)
    draft: BrandProfileDraftV1

    @field_validator("brand")
    @classmethod
    def validate_brand(cls, value: str) -> str:
        return _validated_text(value, "brand")


class BrandProfileUpdateDraftRequestV1(BrandProfileContractV1):
    expected_version: int = Field(ge=1, strict=True)
    draft: BrandProfileDraftV1


class BrandProfileValidateRequestV1(BrandProfileContractV1):
    expected_version: int = Field(ge=1, strict=True)


class BrandProfilePublishRequestV1(BrandProfileContractV1):
    expected_version: int = Field(ge=1, strict=True)


class BrandProfileResponseV1(BrandProfileContractV1):
    id: str = Field(pattern=UUID_PATTERN)
    workspace_id: WorkspaceId
    brand: str
    profile_key: str
    state: BrandProfileState
    draft: BrandProfileDraftV1
    current_version_id: str | None
    current_version_number: int = Field(ge=0, strict=True)
    version: int = Field(ge=1, strict=True)
    stale_at: datetime | None
    created_by: str
    created_at: datetime
    updated_by: str
    updated_at: datetime

    @field_validator("id", "current_version_id")
    @classmethod
    def validate_ids(cls, value: str | None) -> str | None:
        return canonicalize_uuid(value) if value is not None else None

    @field_validator("created_at", "updated_at", "stale_at")
    @classmethod
    def validate_timestamps(cls, value: datetime | None, info) -> datetime | None:
        return _utc(value, info.field_name) if value is not None else None


class BrandProfileValidationIssueV1(BrandProfileContractV1):
    asset_version_id: str = Field(pattern=UUID_PATTERN)
    role: BrandProfileMemberRole
    reason_code: str = Field(pattern=TOKEN_PATTERN)
    message: str = Field(min_length=1, max_length=512)

    @field_validator("asset_version_id")
    @classmethod
    def validate_asset_version_id(cls, value: str) -> str:
        return canonicalize_uuid(value)

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        return _validated_text(value, "message")


class BrandProfileValidationResponseV1(BrandProfileContractV1):
    profile_id: str = Field(pattern=UUID_PATTERN)
    profile_version: int = Field(ge=1, strict=True)
    valid: bool = Field(strict=True)
    decided_at: datetime
    issues: list[BrandProfileValidationIssueV1] = Field(max_length=64)

    @field_validator("profile_id")
    @classmethod
    def validate_profile_id(cls, value: str) -> str:
        return canonicalize_uuid(value)

    @field_validator("decided_at")
    @classmethod
    def validate_decided_at(cls, value: datetime) -> datetime:
        return _utc(value, "decided_at")

    @model_validator(mode="after")
    def validate_issue_summary(self) -> BrandProfileValidationResponseV1:
        if self.valid == bool(self.issues):
            raise ValueError("valid must be true exactly when issues is empty")
        return self


class BrandProfilePublishedMemberV1(BrandProfileContractV1):
    ordinal: int = Field(ge=0, strict=True)
    asset_id: str = Field(pattern=UUID_PATTERN)
    asset_version_id: str = Field(pattern=UUID_PATTERN)
    role: BrandProfileMemberRole
    published_rights_record_id: str = Field(pattern=UUID_PATTERN)
    published_rights_record_version: int = Field(ge=1, strict=True)
    currently_usable: bool = Field(strict=True)
    current_reason_code: RightsDecisionCode
    current_rights_record_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    current_rights_record_version: int | None = Field(default=None, ge=1, strict=True)
    decided_at: datetime

    @field_validator(
        "asset_id",
        "asset_version_id",
        "published_rights_record_id",
        "current_rights_record_id",
    )
    @classmethod
    def validate_ids(cls, value: str | None) -> str | None:
        return canonicalize_uuid(value) if value is not None else None

    @field_validator("decided_at")
    @classmethod
    def validate_decided_at(cls, value: datetime) -> datetime:
        return _utc(value, "decided_at")

    @model_validator(mode="after")
    def validate_current_rights_identity(self) -> BrandProfilePublishedMemberV1:
        if (self.current_rights_record_id is None) != (self.current_rights_record_version is None):
            raise ValueError("current Rights Record identity must be complete")
        return self


class BrandProfileVersionResponseV1(BrandProfileContractV1):
    id: str = Field(pattern=UUID_PATTERN)
    workspace_id: WorkspaceId
    profile_id: str = Field(pattern=UUID_PATTERN)
    version_number: int = Field(ge=1, strict=True)
    draft: BrandProfileDraftV1
    content_sha256: str = Field(pattern=SHA256_PATTERN)
    published_by: str
    published_at: datetime
    members: list[BrandProfilePublishedMemberV1] = Field(max_length=64)

    @field_validator("id", "profile_id")
    @classmethod
    def validate_ids(cls, value: str) -> str:
        return canonicalize_uuid(value)

    @field_validator("published_at")
    @classmethod
    def validate_published_at(cls, value: datetime) -> datetime:
        return _utc(value, "published_at")


class BrandProfileListResponseV1(BrandProfileContractV1):
    items: list[BrandProfileResponseV1]
    next_cursor: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        pattern=CURSOR_PATTERN,
    )


class BrandProfileVersionListResponseV1(BrandProfileContractV1):
    items: list[BrandProfileVersionResponseV1]
    next_cursor: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        pattern=CURSOR_PATTERN,
    )
