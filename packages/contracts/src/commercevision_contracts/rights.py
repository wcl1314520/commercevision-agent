"""Versioned Rights Record and current-usability contracts."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from commercevision_domain import (
    UUID_PATTERN,
    AssetState,
    RightsDecisionCode,
    RightsRecordDecision,
    canonicalize_uuid,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PERMISSION_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"


def _utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must be timezone-aware UTC")
    return value


def _validated_reference(value: str) -> str:
    if value != value.strip() or any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        raise ValueError("rights text must be trimmed and contain no controls")
    return value


class RightsContractV1(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RightsRecordMutationRequestV1(RightsContractV1):
    expected_asset_version: int = Field(ge=1)
    asset_version_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    owner_reference: str = Field(min_length=1, max_length=256)
    source: str = Field(min_length=1, max_length=256)
    license_reference: str = Field(min_length=1, max_length=256)
    allowed_uses: list[str] = Field(max_length=128)
    allowed_providers: list[str] = Field(max_length=128)
    derivative_allowed: bool
    public_demo_allowed: bool
    evidence_reference: str = Field(min_length=1, max_length=512)
    terms_sha256: str = Field(pattern=SHA256_PATTERN)
    valid_from: datetime
    valid_until: datetime | None
    perpetual: bool

    @field_validator("asset_version_id")
    @classmethod
    def validate_asset_version_id(cls, value: str | None) -> str | None:
        return canonicalize_uuid(value) if value is not None else None

    @field_validator(
        "owner_reference",
        "source",
        "license_reference",
        "evidence_reference",
    )
    @classmethod
    def validate_reference(cls, value: str) -> str:
        return _validated_reference(value)

    @field_validator("allowed_uses", "allowed_providers")
    @classmethod
    def validate_permissions(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("permission sets must not contain duplicate values")
        if any(re.fullmatch(PERMISSION_PATTERN, value) is None for value in values):
            raise ValueError("permission identifier is invalid")
        return values

    @field_validator("valid_from")
    @classmethod
    def validate_valid_from(cls, value: datetime) -> datetime:
        return _utc(value, "valid_from")

    @field_validator("valid_until")
    @classmethod
    def validate_valid_until(cls, value: datetime | None) -> datetime | None:
        return _utc(value, "valid_until") if value is not None else None

    @model_validator(mode="after")
    def validate_validity_policy(self) -> RightsRecordMutationRequestV1:
        if self.perpetual and self.valid_until is not None:
            raise ValueError("perpetual rights must not set valid_until")
        if not self.perpetual and self.valid_until is None:
            raise ValueError("non-perpetual rights require valid_until")
        if self.valid_until is not None and self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be later than valid_from")
        return self


class RightsRecordRevokeRequestV1(RightsContractV1):
    expected_asset_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=256)
    evidence_reference: str = Field(min_length=1, max_length=512)

    @field_validator("reason", "evidence_reference")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return _validated_reference(value)


class AssetAdministratorBlockRequestV1(RightsContractV1):
    expected_asset_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=256)
    evidence_reference: str = Field(min_length=1, max_length=512)

    @field_validator("reason", "evidence_reference")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return _validated_reference(value)


class RightsRecordResponseV1(RightsContractV1):
    id: str
    workspace_id: str
    asset_id: str
    asset_version_id: str | None
    version_number: int
    decision: RightsRecordDecision
    owner_reference: str
    source: str
    license_reference: str
    allowed_uses: list[str]
    allowed_providers: list[str]
    derivative_allowed: bool
    public_demo_allowed: bool
    evidence_reference: str
    terms_sha256: str
    valid_from: datetime
    valid_until: datetime | None
    perpetual: bool
    supersedes_record_id: str | None
    created_by: str
    created_at: datetime


class RightsHistoryResponseV1(RightsContractV1):
    items: list[RightsRecordResponseV1]
    next_cursor: int | None = Field(default=None, ge=1)


class RightsMutationResponseV1(RightsContractV1):
    asset_id: str
    asset_version: int = Field(ge=1)
    asset_state: AssetState
    current_rights_record: RightsRecordResponseV1 | None


class RightsUsabilityRequestV1(RightsContractV1):
    asset_version_id: str = Field(pattern=UUID_PATTERN)
    purpose: str = Field(pattern=PERMISSION_PATTERN)
    provider: str = Field(pattern=PERMISSION_PATTERN)
    requires_derivative: bool
    decision_time: datetime = Field(
        description=(
            "Caller-requested evaluation horizon. Current authorization clamps this value "
            "to at least the workspace-scoped MySQL UTC_TIMESTAMP(6), so backdating can "
            "never extend usability."
        )
    )

    @field_validator("asset_version_id")
    @classmethod
    def validate_version_id(cls, value: str) -> str:
        return canonicalize_uuid(value)

    @field_validator("decision_time")
    @classmethod
    def validate_decision_time(cls, value: datetime) -> datetime:
        return _utc(value, "decision_time")


class RightsUsabilityResponseV1(RightsContractV1):
    authorized: bool
    reason_code: RightsDecisionCode
    workspace_id: str
    asset_id: str
    asset_version_id: str
    rights_record_id: str | None
    rights_record_version: int | None
    purpose: str
    provider: str
    requires_derivative: bool
    decided_at: datetime
