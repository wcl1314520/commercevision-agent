"""Immutable Rights Records and authoritative current-usability decisions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from commercevision_domain.workspace_identity import validate_workspace_id

from .entities import Asset
from .enums import AssetState

_PERMISSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class RightsRecordDecision(StrEnum):
    GRANT = "GRANT"
    REVOKE = "REVOKE"


class RightsDecisionCode(StrEnum):
    AUTHORIZED = "AUTHORIZED"
    NO_CURRENT_RIGHTS = "NO_CURRENT_RIGHTS"
    RIGHTS_REVOKED = "RIGHTS_REVOKED"
    RIGHTS_NOT_YET_VALID = "RIGHTS_NOT_YET_VALID"
    RIGHTS_EXPIRED = "RIGHTS_EXPIRED"
    RIGHTS_ASSET_VERSION_MISMATCH = "RIGHTS_ASSET_VERSION_MISMATCH"
    ASSET_VERSION_NOT_CURRENT = "ASSET_VERSION_NOT_CURRENT"
    ASSET_NOT_AVAILABLE = "ASSET_NOT_AVAILABLE"
    ASSET_RETENTION_EXPIRED = "ASSET_RETENTION_EXPIRED"
    ASSET_BLOCKED = "ASSET_BLOCKED"
    ADMINISTRATIVELY_BLOCKED = "ADMINISTRATIVELY_BLOCKED"
    USE_NOT_ALLOWED = "USE_NOT_ALLOWED"
    PROVIDER_NOT_ALLOWED = "PROVIDER_NOT_ALLOWED"
    DERIVATIVE_NOT_ALLOWED = "DERIVATIVE_NOT_ALLOWED"


def _require_utc(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must be timezone-aware UTC")


def _validate_reference(value: str, field: str, *, maximum: int = 512) -> None:
    if not value or value != value.strip() or len(value) > maximum:
        raise ValueError(f"{field} is invalid")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{field} is invalid")


def _validate_permissions(values: frozenset[str], field: str) -> None:
    if not isinstance(values, frozenset):
        raise ValueError(f"{field} must be an immutable set")
    if len(values) > 128:
        raise ValueError(f"{field} exceeds the permission count limit")
    if any(_PERMISSION_PATTERN.fullmatch(value) is None for value in values):
        raise ValueError(f"{field} contains an invalid permission identifier")


@dataclass(frozen=True, slots=True)
class RightsRecord:
    id: str
    workspace_id: str
    asset_id: str
    asset_version_id: str | None
    version_number: int
    decision: RightsRecordDecision
    owner_reference: str
    source: str
    license_reference: str
    allowed_uses: frozenset[str]
    allowed_providers: frozenset[str]
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

    def __post_init__(self) -> None:
        validate_workspace_id(self.workspace_id)
        _require_utc(self.valid_from, "valid_from")
        _require_utc(self.created_at, "created_at")
        if self.valid_until is not None:
            _require_utc(self.valid_until, "valid_until")
        if self.version_number < 1:
            raise ValueError("Rights Record version must be positive")
        for value, field, maximum in (
            (self.id, "Rights Record id", 36),
            (self.asset_id, "Rights Record asset id", 36),
            (self.owner_reference, "owner_reference", 256),
            (self.source, "source", 256),
            (self.license_reference, "license_reference", 256),
            (self.evidence_reference, "evidence_reference", 512),
            (self.created_by, "created_by", 128),
        ):
            _validate_reference(value, field, maximum=maximum)
        if self.asset_version_id is not None:
            _validate_reference(self.asset_version_id, "asset_version_id", maximum=36)
        if self.supersedes_record_id is not None:
            _validate_reference(
                self.supersedes_record_id,
                "supersedes_record_id",
                maximum=36,
            )
        if _SHA256_PATTERN.fullmatch(self.terms_sha256) is None:
            raise ValueError("terms_sha256 must be a lowercase SHA-256")
        _validate_permissions(self.allowed_uses, "allowed_uses")
        _validate_permissions(self.allowed_providers, "allowed_providers")
        if self.perpetual:
            if self.valid_until is not None:
                raise ValueError("perpetual Rights Records must not set valid_until")
        elif self.valid_until is None:
            raise ValueError("non-perpetual Rights Records require valid_until")
        elif self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be later than valid_from")


@dataclass(frozen=True, slots=True)
class CurrentUsabilityDecision:
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


def evaluate_current_usability(
    *,
    asset: Asset,
    rights_record: RightsRecord | None,
    asset_version_id: str,
    purpose: str,
    provider: str,
    requires_derivative: bool,
    decision_time: datetime,
) -> CurrentUsabilityDecision:
    """Evaluate one use without consulting any cache or vector store."""

    _require_utc(decision_time, "decision_time")
    _validate_reference(asset_version_id, "asset_version_id", maximum=36)
    if _PERMISSION_PATTERN.fullmatch(purpose) is None:
        raise ValueError("purpose is invalid")
    if _PERMISSION_PATTERN.fullmatch(provider) is None:
        raise ValueError("provider is invalid")

    def decision(code: RightsDecisionCode) -> CurrentUsabilityDecision:
        return CurrentUsabilityDecision(
            authorized=code == RightsDecisionCode.AUTHORIZED,
            reason_code=code,
            workspace_id=asset.workspace_id,
            asset_id=asset.id,
            asset_version_id=asset_version_id,
            rights_record_id=rights_record.id if rights_record is not None else None,
            rights_record_version=(
                rights_record.version_number if rights_record is not None else None
            ),
            purpose=purpose,
            provider=provider,
            requires_derivative=requires_derivative,
            decided_at=decision_time,
        )

    if asset_version_id != asset.current_version_id:
        return decision(RightsDecisionCode.ASSET_VERSION_NOT_CURRENT)
    if asset.status == AssetState.BLOCKED and asset.block_reason == "ADMINISTRATIVELY_BLOCKED":
        return decision(RightsDecisionCode.ADMINISTRATIVELY_BLOCKED)
    if rights_record is None or asset.current_rights_record_id != rights_record.id:
        return decision(RightsDecisionCode.NO_CURRENT_RIGHTS)
    if rights_record.workspace_id != asset.workspace_id or rights_record.asset_id != asset.id:
        return decision(RightsDecisionCode.NO_CURRENT_RIGHTS)
    if asset.retention_deadline is not None and decision_time >= asset.retention_deadline:
        return decision(RightsDecisionCode.ASSET_RETENTION_EXPIRED)
    if (
        rights_record.asset_version_id is not None
        and rights_record.asset_version_id != asset_version_id
    ):
        return decision(RightsDecisionCode.RIGHTS_ASSET_VERSION_MISMATCH)
    if rights_record.decision == RightsRecordDecision.REVOKE:
        return decision(RightsDecisionCode.RIGHTS_REVOKED)
    if decision_time < rights_record.valid_from:
        return decision(RightsDecisionCode.RIGHTS_NOT_YET_VALID)
    if rights_record.valid_until is not None and decision_time >= rights_record.valid_until:
        return decision(RightsDecisionCode.RIGHTS_EXPIRED)
    if asset.status == AssetState.RIGHTS_EXPIRED:
        return decision(RightsDecisionCode.RIGHTS_EXPIRED)
    if purpose not in rights_record.allowed_uses:
        return decision(RightsDecisionCode.USE_NOT_ALLOWED)
    if provider not in rights_record.allowed_providers:
        return decision(RightsDecisionCode.PROVIDER_NOT_ALLOWED)
    if requires_derivative and not rights_record.derivative_allowed:
        return decision(RightsDecisionCode.DERIVATIVE_NOT_ALLOWED)
    if asset.status == AssetState.BLOCKED:
        return decision(RightsDecisionCode.ASSET_BLOCKED)
    if asset.status != AssetState.AVAILABLE:
        return decision(RightsDecisionCode.ASSET_NOT_AVAILABLE)
    return decision(RightsDecisionCode.AUTHORIZED)
