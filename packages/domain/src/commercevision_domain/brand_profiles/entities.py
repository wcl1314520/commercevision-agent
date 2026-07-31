"""Versioned Brand Profile aggregate and immutable publication facts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from unicodedata import category

from commercevision_domain.ids import canonicalize_uuid, new_uuid7
from commercevision_domain.workflow.errors import ConcurrencyError, InvalidTransitionError
from commercevision_domain.workspace_identity import validate_workspace_id

from .enums import (
    BrandProfileMemberRole,
    BrandProfileState,
    BrandRuleScope,
)

_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)
_COLOR_PATTERN = re.compile(r"^#[0-9A-F]{6}(?:[0-9A-F]{2})?$", re.ASCII)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$", re.ASCII)

_MAX_RULES = 64
_MAX_COLORS = 32
_MAX_TEXT_ITEMS = 64
_MAX_MEMBERS = 64


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _require_utc(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must be timezone-aware UTC")


def _validate_text(value: str, field: str, *, maximum: int) -> None:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum:
        raise ValueError(f"{field} is invalid")
    if any(category(character) == "Cc" for character in value):
        raise ValueError(f"{field} is invalid")


def _validate_token(value: str, field: str) -> None:
    if not isinstance(value, str) or _TOKEN_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")


def _validate_uuid(value: str, field: str) -> None:
    try:
        canonical = canonicalize_uuid(value)
    except ValueError:
        raise ValueError(f"{field} must be a canonical UUID") from None
    if canonical != value:
        raise ValueError(f"{field} must be a canonical UUID")


def _validate_integer(value: object, field: str, *, minimum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        qualifier = "positive" if minimum == 1 else "non-negative"
        raise ValueError(f"{field} must be a {qualifier} integer")
    return value


def _validate_tuple(value: object, field: str, *, maximum: int) -> tuple[Any, ...]:
    if not isinstance(value, tuple):
        raise ValueError(f"{field} must be an immutable tuple")
    if len(value) > maximum:
        raise ValueError(f"{field} exceeds the item limit")
    return value


def _validate_text_tuple(value: object, field: str) -> tuple[str, ...]:
    items = _validate_tuple(value, field, maximum=_MAX_TEXT_ITEMS)
    for item in items:
        _validate_text(item, field, maximum=512)
    if len(set(items)) != len(items):
        raise ValueError(f"{field} contains duplicates")
    return items


@dataclass(frozen=True, slots=True)
class BrandRule:
    code: str
    scope: BrandRuleScope
    instruction: str

    def __post_init__(self) -> None:
        _validate_token(self.code, "Brand rule code")
        if not isinstance(self.scope, BrandRuleScope):
            raise ValueError("Brand rule scope is invalid")
        _validate_text(self.instruction, "Brand rule instruction", maximum=1024)

    def to_canonical_data(self) -> dict[str, str]:
        return {
            "code": self.code,
            "scope": self.scope.value,
            "instruction": self.instruction,
        }


@dataclass(frozen=True, slots=True)
class BrandColor:
    name: str
    value: str

    def __post_init__(self) -> None:
        _validate_text(self.name, "Brand color name", maximum=64)
        if not isinstance(self.value, str) or _COLOR_PATTERN.fullmatch(self.value) is None:
            raise ValueError("Brand color value must be canonical uppercase hex")

    def to_canonical_data(self) -> dict[str, str]:
        return {"name": self.name, "value": self.value}


@dataclass(frozen=True, slots=True)
class BrandProfileMemberSelection:
    asset_version_id: str
    role: BrandProfileMemberRole

    def __post_init__(self) -> None:
        _validate_uuid(self.asset_version_id, "selected Asset Version id")
        if not isinstance(self.role, BrandProfileMemberRole):
            raise ValueError("Brand Profile member role is invalid")

    def to_canonical_data(self) -> dict[str, str]:
        return {
            "asset_version_id": self.asset_version_id,
            "role": self.role.value,
        }


@dataclass(frozen=True, slots=True)
class BrandProfileDraft:
    rules: tuple[BrandRule, ...]
    approved_colors: tuple[BrandColor, ...]
    required_marks: tuple[str, ...]
    prohibited_elements: tuple[str, ...]
    tone_constraints: tuple[str, ...]
    copy_constraints: tuple[str, ...]
    purpose: str
    provider: str
    requires_derivative: bool
    selected_assets: tuple[BrandProfileMemberSelection, ...]

    def __post_init__(self) -> None:
        rules = _validate_tuple(self.rules, "rules", maximum=_MAX_RULES)
        if any(not isinstance(rule, BrandRule) for rule in rules):
            raise ValueError("rules contains an invalid Brand rule")
        if len({rule.code for rule in rules}) != len(rules):
            raise ValueError("rules contains duplicate rule codes")

        colors = _validate_tuple(
            self.approved_colors,
            "approved_colors",
            maximum=_MAX_COLORS,
        )
        if any(not isinstance(color, BrandColor) for color in colors):
            raise ValueError("approved_colors contains an invalid Brand color")
        if len({color.name for color in colors}) != len(colors):
            raise ValueError("approved_colors contains duplicate names")

        _validate_text_tuple(self.required_marks, "required_marks")
        _validate_text_tuple(self.prohibited_elements, "prohibited_elements")
        _validate_text_tuple(self.tone_constraints, "tone_constraints")
        _validate_text_tuple(self.copy_constraints, "copy_constraints")
        _validate_token(self.purpose, "Brand Profile purpose")
        _validate_token(self.provider, "Brand Profile provider")
        if not isinstance(self.requires_derivative, bool):
            raise ValueError("requires_derivative must be a boolean")

        selections = _validate_tuple(
            self.selected_assets,
            "selected_assets",
            maximum=_MAX_MEMBERS,
        )
        if any(not isinstance(item, BrandProfileMemberSelection) for item in selections):
            raise ValueError("selected_assets contains an invalid member")
        selected_version_ids = [item.asset_version_id for item in selections]
        if len(set(selected_version_ids)) != len(selected_version_ids):
            raise ValueError("selected Asset Version may appear only once")

    def to_canonical_data(self) -> dict[str, object]:
        return {
            "schema_version": "brand-profile.v1",
            "rules": [rule.to_canonical_data() for rule in self.rules],
            "approved_colors": [color.to_canonical_data() for color in self.approved_colors],
            "required_marks": list(self.required_marks),
            "prohibited_elements": list(self.prohibited_elements),
            "tone_constraints": list(self.tone_constraints),
            "copy_constraints": list(self.copy_constraints),
            "purpose": self.purpose,
            "provider": self.provider,
            "requires_derivative": self.requires_derivative,
            "selected_assets": [item.to_canonical_data() for item in self.selected_assets],
        }


@dataclass(frozen=True, slots=True)
class BrandProfilePublishedMember:
    ordinal: int
    asset_id: str
    asset_version_id: str
    role: BrandProfileMemberRole
    rights_record_id: str
    rights_record_version: int

    def __post_init__(self) -> None:
        if not isinstance(self.ordinal, int) or isinstance(self.ordinal, bool) or self.ordinal < 0:
            raise ValueError("Brand Profile member ordinal is invalid")
        _validate_uuid(self.asset_id, "Brand Profile member Asset id")
        _validate_uuid(self.asset_version_id, "Brand Profile member Asset Version id")
        _validate_uuid(self.rights_record_id, "Brand Profile member Rights Record id")
        if not isinstance(self.role, BrandProfileMemberRole):
            raise ValueError("Brand Profile member role is invalid")
        if (
            not isinstance(self.rights_record_version, int)
            or isinstance(self.rights_record_version, bool)
            or self.rights_record_version < 1
        ):
            raise ValueError("Brand Profile member Rights Record version must be positive")

    def to_canonical_data(self) -> dict[str, object]:
        return {
            "ordinal": self.ordinal,
            "asset_id": self.asset_id,
            "asset_version_id": self.asset_version_id,
            "role": self.role.value,
            "rights_record_id": self.rights_record_id,
            "rights_record_version": self.rights_record_version,
        }


def _publication_content_sha256(
    *,
    draft: BrandProfileDraft,
    members: tuple[BrandProfilePublishedMember, ...],
) -> str:
    payload = {
        "draft": draft.to_canonical_data(),
        "members": [member.to_canonical_data() for member in members],
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True, slots=True)
class BrandProfileVersion:
    id: str
    workspace_id: str
    profile_id: str
    version_number: int
    draft: BrandProfileDraft
    members: tuple[BrandProfilePublishedMember, ...]
    content_sha256: str
    published_by: str
    published_at: datetime

    def __post_init__(self) -> None:
        _validate_uuid(self.id, "Brand Profile Version id")
        validate_workspace_id(self.workspace_id)
        _validate_uuid(self.profile_id, "Brand Profile id")
        _validate_integer(
            self.version_number,
            "Brand Profile Version number",
            minimum=1,
        )
        if not isinstance(self.draft, BrandProfileDraft):
            raise ValueError("Brand Profile Version draft is invalid")
        _validate_published_members(self.draft, self.members)
        if _SHA256_PATTERN.fullmatch(self.content_sha256) is None:
            raise ValueError("Brand Profile content_sha256 must be a lowercase SHA-256")
        if self.content_sha256 != self.calculate_content_sha256(members=self.members):
            raise ValueError("Brand Profile content_sha256 does not match its publication")
        _validate_text(self.published_by, "Brand Profile publisher", maximum=128)
        _require_utc(self.published_at, "published_at")

    def calculate_content_sha256(
        self,
        *,
        members: tuple[BrandProfilePublishedMember, ...],
    ) -> str:
        return _publication_content_sha256(draft=self.draft, members=members)


def _validate_published_members(
    draft: BrandProfileDraft,
    members: object,
) -> tuple[BrandProfilePublishedMember, ...]:
    validated = _validate_tuple(members, "Brand Profile members", maximum=_MAX_MEMBERS)
    if any(not isinstance(member, BrandProfilePublishedMember) for member in validated):
        raise ValueError("Brand Profile members contains an invalid member")
    if len(validated) != len(draft.selected_assets):
        raise ValueError("published members must resolve every selected Asset Version")
    if tuple(member.ordinal for member in validated) != tuple(range(len(validated))):
        raise ValueError("Brand Profile member ordinals must be contiguous")
    for selection, member in zip(draft.selected_assets, validated, strict=True):
        if selection.asset_version_id != member.asset_version_id or selection.role != member.role:
            raise ValueError("published member does not match the selected Asset Version")
    if len({member.asset_version_id for member in validated}) != len(validated):
        raise ValueError("published Asset Version may appear only once")
    return validated


@dataclass(slots=True)
class BrandProfile:
    id: str
    workspace_id: str
    brand: str
    profile_key: str
    state: BrandProfileState
    draft: BrandProfileDraft
    current_version_id: str | None
    current_version_number: int
    version: int
    stale_at: datetime | None
    created_by: str
    created_at: datetime
    updated_by: str
    updated_at: datetime

    def __post_init__(self) -> None:
        _validate_uuid(self.id, "Brand Profile id")
        validate_workspace_id(self.workspace_id)
        _validate_text(self.brand, "Brand Profile brand", maximum=128)
        _validate_token(self.profile_key, "Brand Profile key")
        if not isinstance(self.state, BrandProfileState):
            raise ValueError("Brand Profile state is invalid")
        if not isinstance(self.draft, BrandProfileDraft):
            raise ValueError("Brand Profile draft is invalid")
        if self.current_version_id is not None:
            _validate_uuid(self.current_version_id, "current Brand Profile Version id")
        _validate_integer(
            self.current_version_number,
            "current Brand Profile Version number",
            minimum=0,
        )
        _validate_integer(
            self.version,
            "Brand Profile optimistic version",
            minimum=1,
        )
        if (self.current_version_id is None) != (self.current_version_number == 0):
            raise ValueError("Brand Profile current Version identity is inconsistent")
        if self.state == BrandProfileState.DRAFT and self.current_version_id is not None:
            raise ValueError("a draft-only Brand Profile cannot have a current Version")
        if (
            self.state in {BrandProfileState.ACTIVE, BrandProfileState.NEEDS_REPUBLISH}
            and self.current_version_id is None
        ):
            raise ValueError("a published Brand Profile requires a current Version")
        if self.state == BrandProfileState.NEEDS_REPUBLISH and self.stale_at is None:
            raise ValueError("a stale Brand Profile requires stale_at")
        if self.state != BrandProfileState.NEEDS_REPUBLISH and self.stale_at is not None:
            raise ValueError("only NEEDS_REPUBLISH may retain stale_at")
        _validate_text(self.created_by, "Brand Profile creator", maximum=128)
        _validate_text(self.updated_by, "Brand Profile updater", maximum=128)
        _require_utc(self.created_at, "created_at")
        _require_utc(self.updated_at, "updated_at")
        if self.stale_at is not None:
            _require_utc(self.stale_at, "stale_at")

    @classmethod
    def create(
        cls,
        *,
        workspace_id: str,
        brand: str,
        profile_key: str,
        draft: BrandProfileDraft,
        actor_id: str,
        now: datetime | None = None,
    ) -> BrandProfile:
        created_at = now or _utc_now()
        return cls(
            id=new_uuid7(),
            workspace_id=workspace_id,
            brand=brand,
            profile_key=profile_key,
            state=BrandProfileState.DRAFT,
            draft=draft,
            current_version_id=None,
            current_version_number=0,
            version=1,
            stale_at=None,
            created_by=actor_id,
            created_at=created_at,
            updated_by=actor_id,
            updated_at=created_at,
        )

    def assert_version(self, expected_version: int) -> None:
        _validate_integer(
            expected_version,
            "expected Brand Profile optimistic version",
            minimum=1,
        )
        if self.version != expected_version:
            raise ConcurrencyError(
                f"Brand Profile {self.id} version is {self.version}, expected {expected_version}"
            )

    def update_draft(
        self,
        *,
        expected_version: int,
        draft: BrandProfileDraft,
        actor_id: str,
        now: datetime | None = None,
    ) -> None:
        self.assert_version(expected_version)
        if self.state == BrandProfileState.ARCHIVED:
            raise InvalidTransitionError("an archived Brand Profile cannot be edited")
        if not isinstance(draft, BrandProfileDraft):
            raise ValueError("Brand Profile draft is invalid")
        _validate_text(actor_id, "Brand Profile updater", maximum=128)
        updated_at = now or _utc_now()
        _require_utc(updated_at, "updated_at")
        self.draft = draft
        self.version += 1
        self.updated_by = actor_id
        self.updated_at = updated_at

    def publish(
        self,
        *,
        expected_version: int,
        members: tuple[BrandProfilePublishedMember, ...],
        actor_id: str,
        now: datetime | None = None,
    ) -> BrandProfileVersion:
        self.assert_version(expected_version)
        if self.state == BrandProfileState.ARCHIVED:
            raise InvalidTransitionError("an archived Brand Profile cannot be published")
        validated_members = _validate_published_members(self.draft, members)
        _validate_text(actor_id, "Brand Profile publisher", maximum=128)
        published_at = now or _utc_now()
        _require_utc(published_at, "published_at")
        next_version_number = self.current_version_number + 1
        version = BrandProfileVersion(
            id=new_uuid7(),
            workspace_id=self.workspace_id,
            profile_id=self.id,
            version_number=next_version_number,
            draft=self.draft,
            members=validated_members,
            content_sha256=_publication_content_sha256(
                draft=self.draft,
                members=validated_members,
            ),
            published_by=actor_id,
            published_at=published_at,
        )
        self.current_version_id = version.id
        self.current_version_number = version.version_number
        self.state = BrandProfileState.ACTIVE
        self.stale_at = None
        self.version += 1
        self.updated_by = actor_id
        self.updated_at = published_at
        return version

    def mark_needs_republish(
        self,
        *,
        expected_current_version_id: str,
        stale_at: datetime,
    ) -> bool:
        _validate_uuid(expected_current_version_id, "expected current Brand Profile Version id")
        _require_utc(stale_at, "stale_at")
        if self.current_version_id != expected_current_version_id:
            return False
        if self.state != BrandProfileState.ACTIVE:
            return False
        self.state = BrandProfileState.NEEDS_REPUBLISH
        self.stale_at = stale_at
        self.version += 1
        self.updated_at = stale_at
        return True

    def archive(
        self,
        *,
        expected_version: int,
        actor_id: str,
        now: datetime | None = None,
    ) -> None:
        self.assert_version(expected_version)
        if self.state == BrandProfileState.ARCHIVED:
            return
        _validate_text(actor_id, "Brand Profile archiver", maximum=128)
        archived_at = now or _utc_now()
        _require_utc(archived_at, "archived_at")
        self.state = BrandProfileState.ARCHIVED
        self.stale_at = None
        self.version += 1
        self.updated_by = actor_id
        self.updated_at = archived_at
