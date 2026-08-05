"""Structured Creative Plan values and immutable version facts."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from unicodedata import category

from commercevision_domain.ids import canonicalize_uuid, new_uuid7
from commercevision_domain.workflow.errors import ConcurrencyError
from commercevision_domain.workspace_identity import validate_workspace_id

_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_EXTERNAL_REFERENCE_PATTERN = re.compile(r"^(?:[a-z][a-z0-9+.-]*://|data:)", re.IGNORECASE)
_WINDOWS_ABSOLUTE_PATH_PATTERN = re.compile(r"^[A-Za-z]:[\\/]", re.ASCII)
_FIELD_NAME_BOUNDARY_PATTERN = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_FIELD_NAME_SEPARATOR_PATTERN = re.compile(r"[^A-Za-z0-9]+", re.ASCII)
_MAX_DIRECTIONS = 12
_MAX_ITEMS = 32
_MAX_TOOL_INTENTS = 16
_MAX_JSON_DEPTH = 8
_MAX_JSON_NODES = 512
_MAX_JSON_COLLECTION_ITEMS = 64
_MAX_JSON_STRING_LENGTH = 4096
_EXTERNAL_AUTHORITY_FIELDS = frozenset(
    {
        "access_key",
        "actor_id",
        "api_key",
        "approval_id",
        "approval_token",
        "authorization",
        "bucket",
        "credential",
        "credentials",
        "endpoint",
        "file_path",
        "headers",
        "http_method",
        "idempotency_key",
        "lease_token",
        "object_key",
        "password",
        "path",
        "principal",
        "secret",
        "sql",
        "token",
        "uri",
        "url",
        "workspace_id",
    }
)


class CreativePlanSource(StrEnum):
    AGENT = "AGENT"
    USER = "USER"


class ImageRole(StrEnum):
    MAIN = "MAIN"
    HERO = "HERO"
    SCENE = "SCENE"
    DETAIL = "DETAIL"
    SELLING_POINT = "SELLING_POINT"


def _validate_token(value: str, field_name: str) -> str:
    if not isinstance(value, str) or _TOKEN_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} is invalid")
    return value


def _validate_uuid(value: str, field_name: str) -> str:
    try:
        canonical = canonicalize_uuid(value)
    except ValueError:
        raise ValueError(f"{field_name} must be a canonical UUID") from None
    if canonical != value:
        raise ValueError(f"{field_name} must be a canonical UUID")
    return value


def _validate_sha256(value: str, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be lowercase SHA-256")
    return value


def _validate_text(value: str, field_name: str, *, maximum: int = 1024) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(category(character) == "Cc" for character in value)
    ):
        raise ValueError(f"{field_name} is invalid")
    return value


def _validate_positive_integer(value: int, field_name: str, *, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
        raise ValueError(f"{field_name} must be between 1 and {maximum}")
    return value


def _validate_text_tuple(
    value: tuple[str, ...],
    field_name: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, tuple) or len(value) > _MAX_ITEMS or (not value and not allow_empty):
        raise ValueError(f"{field_name} is invalid")
    for item in value:
        _validate_text(item, field_name, maximum=512)
    if len(set(value)) != len(value):
        raise ValueError(f"{field_name} contains duplicates")
    return value


def _validate_token_tuple(
    value: tuple[str, ...],
    field_name: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, tuple) or len(value) > _MAX_ITEMS or (not value and not allow_empty):
        raise ValueError(f"{field_name} is invalid")
    for item in value:
        _validate_token(item, field_name)
    if len(set(value)) != len(value):
        raise ValueError(f"{field_name} contains duplicates")
    return value


def _validate_json_shape(value: object) -> None:
    stack: list[tuple[object, int]] = [(value, 0)]
    visited = 0
    while stack:
        current, depth = stack.pop()
        visited += 1
        if visited > _MAX_JSON_NODES:
            raise ValueError("Creative Plan content exceeds the node limit")
        if depth > _MAX_JSON_DEPTH:
            raise ValueError("Creative Plan content exceeds the depth limit")
        if isinstance(current, str):
            if len(current) > _MAX_JSON_STRING_LENGTH:
                raise ValueError("Creative Plan content exceeds the string limit")
            if any(category(character) == "Cc" for character in current):
                raise ValueError("Creative Plan content contains a control character")
        elif isinstance(current, dict):
            if len(current) > _MAX_JSON_COLLECTION_ITEMS:
                raise ValueError("Creative Plan content exceeds the collection limit")
            if any(not isinstance(key, str) for key in current):
                raise ValueError("Creative Plan JSON object keys must be strings")
            stack.extend((item, depth + 1) for pair in current.items() for item in pair)
        elif isinstance(current, (list, tuple)):
            if len(current) > _MAX_JSON_COLLECTION_ITEMS:
                raise ValueError("Creative Plan content exceeds the collection limit")
            stack.extend((item, depth + 1) for item in current)


def _canonical_json(value: object) -> str:
    _validate_json_shape(value)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (RecursionError, TypeError, ValueError) as exc:
        raise ValueError("Creative Plan content must be finite JSON") from exc
    if len(encoded.encode("utf-8")) > 64 * 1024:
        raise ValueError("Creative Plan content exceeds the byte limit")
    return encoded


def _reject_external_authority(value: object) -> None:
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, item in current.items():
                normalized_key = _FIELD_NAME_SEPARATOR_PATTERN.sub(
                    "_", _FIELD_NAME_BOUNDARY_PATTERN.sub("_", key)
                ).strip("_")
                if normalized_key.casefold() in _EXTERNAL_AUTHORITY_FIELDS:
                    raise ValueError("Tool Intent arguments cannot contain external authority")
                stack.append(item)
        elif isinstance(current, (list, tuple)):
            stack.extend(current)
        elif isinstance(current, str):
            candidate = current.strip()
            if (
                _EXTERNAL_REFERENCE_PATTERN.match(candidate) is not None
                or _WINDOWS_ABSOLUTE_PATH_PATTERN.match(candidate) is not None
                or candidate.startswith(("/", "\\"))
            ):
                raise ValueError("Tool Intent arguments cannot contain external authority")


@dataclass(frozen=True, slots=True)
class ToolIntentProposal:
    intent_key: str
    tool_name: str
    schema_version: str
    purpose: str
    arguments_json: str
    estimated_cost_units: int

    def __post_init__(self) -> None:
        _validate_token(self.intent_key, "Tool Intent key")
        _validate_token(self.tool_name, "Tool Intent name")
        _validate_token(self.schema_version, "Tool Intent schema version")
        _validate_text(self.purpose, "Tool Intent purpose", maximum=512)
        if not isinstance(self.arguments_json, str) or len(self.arguments_json) > 16 * 1024:
            raise ValueError("Tool Intent arguments are invalid")
        try:
            arguments = json.loads(self.arguments_json)
        except (RecursionError, json.JSONDecodeError) as exc:
            raise ValueError("Tool Intent arguments must be canonical JSON") from exc
        if not isinstance(arguments, dict) or _canonical_json(arguments) != self.arguments_json:
            raise ValueError("Tool Intent arguments must be a canonical JSON object")
        _reject_external_authority(arguments)
        _validate_positive_integer(
            self.estimated_cost_units,
            "Tool Intent estimated cost",
            maximum=1_000_000,
        )

    @classmethod
    def create(
        cls,
        *,
        intent_key: str,
        tool_name: str,
        schema_version: str,
        purpose: str,
        arguments: Mapping[str, object],
        estimated_cost_units: int,
    ) -> ToolIntentProposal:
        return cls(
            intent_key=intent_key,
            tool_name=tool_name,
            schema_version=schema_version,
            purpose=purpose,
            arguments_json=_canonical_json(dict(arguments)),
            estimated_cost_units=estimated_cost_units,
        )

    def to_canonical_data(self) -> dict[str, object]:
        return {
            "intent_key": self.intent_key,
            "tool_name": self.tool_name,
            "schema_version": self.schema_version,
            "purpose": self.purpose,
            "arguments": json.loads(self.arguments_json),
            "estimated_cost_units": self.estimated_cost_units,
        }


@dataclass(frozen=True, slots=True)
class CreativePlanCitationSelection:
    citation_id: str
    reason: str

    def __post_init__(self) -> None:
        _validate_token(self.citation_id, "Creative Plan citation id")
        _validate_text(self.reason, "Creative Plan citation reason", maximum=512)

    def to_canonical_data(self) -> dict[str, str]:
        return {"citation_id": self.citation_id, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class CreativePlanDirection:
    key: str
    image_role: ImageRole
    scene: str
    composition: str
    camera: str
    lighting: str
    color_direction: str
    product_constraints: tuple[str, ...]
    required_elements: tuple[str, ...]
    prohibited_elements: tuple[str, ...]
    citation_selections: tuple[CreativePlanCitationSelection, ...]
    candidate_count: int
    quality_targets: tuple[str, ...]
    repair_scope: tuple[str, ...]
    tool_intents: tuple[ToolIntentProposal, ...]

    def __post_init__(self) -> None:
        _validate_token(self.key, "Creative Plan direction key")
        object.__setattr__(self, "image_role", ImageRole(self.image_role))
        for field_name in ("scene", "composition", "camera", "lighting", "color_direction"):
            _validate_text(getattr(self, field_name), f"Creative Plan {field_name}")
        _validate_text_tuple(self.product_constraints, "product_constraints")
        _validate_text_tuple(self.required_elements, "required_elements")
        _validate_text_tuple(self.prohibited_elements, "prohibited_elements", allow_empty=True)
        if (
            not isinstance(self.citation_selections, tuple)
            or len(self.citation_selections) > _MAX_ITEMS
            or any(
                not isinstance(item, CreativePlanCitationSelection)
                for item in self.citation_selections
            )
        ):
            raise ValueError("citation_selections is invalid")
        citation_ids = [item.citation_id for item in self.citation_selections]
        if len(citation_ids) != len(set(citation_ids)):
            raise ValueError("citation_selections contains duplicate citations")
        object.__setattr__(
            self,
            "citation_selections",
            tuple(sorted(self.citation_selections, key=lambda item: item.citation_id)),
        )
        _validate_positive_integer(self.candidate_count, "candidate_count", maximum=16)
        _validate_text_tuple(self.quality_targets, "quality_targets")
        _validate_token_tuple(self.repair_scope, "repair_scope", allow_empty=True)
        if not isinstance(self.tool_intents, tuple) or len(self.tool_intents) > _MAX_TOOL_INTENTS:
            raise ValueError("tool_intents is invalid")
        if any(not isinstance(item, ToolIntentProposal) for item in self.tool_intents):
            raise ValueError("tool_intents contains an invalid proposal")
        keys = [item.intent_key for item in self.tool_intents]
        if len(keys) != len(set(keys)):
            raise ValueError("tool_intents contains duplicate keys")
        object.__setattr__(
            self,
            "tool_intents",
            tuple(sorted(self.tool_intents, key=lambda item: item.intent_key)),
        )

    def to_canonical_data(self) -> dict[str, object]:
        return {
            "key": self.key,
            "image_role": self.image_role.value,
            "scene": self.scene,
            "composition": self.composition,
            "camera": self.camera,
            "lighting": self.lighting,
            "color_direction": self.color_direction,
            "product_constraints": list(self.product_constraints),
            "required_elements": list(self.required_elements),
            "prohibited_elements": list(self.prohibited_elements),
            "citation_selections": [item.to_canonical_data() for item in self.citation_selections],
            "candidate_count": self.candidate_count,
            "quality_targets": list(self.quality_targets),
            "repair_scope": list(self.repair_scope),
            "tool_intents": [item.to_canonical_data() for item in self.tool_intents],
        }


@dataclass(frozen=True, slots=True)
class CreativePlanPayload:
    directions: tuple[CreativePlanDirection, ...]
    schema_version: str = "creative-plan.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "creative-plan.v1":
            raise ValueError("Creative Plan schema version is unsupported")
        if (
            not isinstance(self.directions, tuple)
            or not 1 <= len(self.directions) <= _MAX_DIRECTIONS
        ):
            raise ValueError("Creative Plan directions are invalid")
        if any(not isinstance(item, CreativePlanDirection) for item in self.directions):
            raise ValueError("Creative Plan directions contain an invalid value")
        keys = [item.key for item in self.directions]
        if len(keys) != len(set(keys)):
            raise ValueError("Creative Plan directions contain duplicate keys")
        intent_keys = [
            intent.intent_key for direction in self.directions for intent in direction.tool_intents
        ]
        if len(intent_keys) != len(set(intent_keys)):
            raise ValueError("Creative Plan contains duplicate Tool Intent keys")
        object.__setattr__(
            self, "directions", tuple(sorted(self.directions, key=lambda item: item.key))
        )

    def to_canonical_data(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "directions": [item.to_canonical_data() for item in self.directions],
        }

    @property
    def payload_sha256(self) -> str:
        return hashlib.sha256(_canonical_json(self.to_canonical_data()).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CreativePlanProvenance:
    product_brief_id: str
    product_brief_version: int
    product_brief_sha256: str
    brand_profile_id: str | None
    brand_profile_version: int | None
    brand_profile_sha256: str | None
    retrieval_run_id: str
    retrieval_citation_ids: tuple[str, ...]
    context_policy_version: str
    context_sha256: str
    prompt_id: str
    prompt_revision: str
    prompt_sha256: str

    def __post_init__(self) -> None:
        _validate_uuid(self.product_brief_id, "ProductBrief id")
        _validate_positive_integer(
            self.product_brief_version, "ProductBrief version", maximum=1_000_000
        )
        _validate_sha256(self.product_brief_sha256, "ProductBrief hash")
        brand_values = (
            self.brand_profile_id,
            self.brand_profile_version,
            self.brand_profile_sha256,
        )
        if any(value is None for value in brand_values) != all(
            value is None for value in brand_values
        ):
            raise ValueError("Brand Profile provenance must be complete or absent")
        if self.brand_profile_id is not None:
            _validate_uuid(self.brand_profile_id, "Brand Profile id")
            assert self.brand_profile_version is not None
            assert self.brand_profile_sha256 is not None
            _validate_positive_integer(
                self.brand_profile_version,
                "Brand Profile version",
                maximum=1_000_000,
            )
            _validate_sha256(self.brand_profile_sha256, "Brand Profile hash")
        _validate_uuid(self.retrieval_run_id, "Retrieval Run id")
        _validate_token_tuple(
            self.retrieval_citation_ids,
            "Retrieval Citation ids",
            allow_empty=True,
        )
        _validate_token(self.context_policy_version, "Planning Context policy version")
        _validate_sha256(self.context_sha256, "Planning Context hash")
        _validate_token(self.prompt_id, "Prompt id")
        _validate_token(self.prompt_revision, "Prompt revision")
        _validate_sha256(self.prompt_sha256, "Prompt hash")


@dataclass(frozen=True, slots=True)
class CreativePlanVersion:
    id: str
    workspace_id: str
    workflow_id: str
    creative_plan_id: str
    version_number: int
    supersedes_version_id: str | None
    source: CreativePlanSource
    payload: CreativePlanPayload
    provenance: CreativePlanProvenance
    payload_sha256: str
    actor_id: str
    revision_reason: str | None
    created_at: datetime

    def __post_init__(self) -> None:
        _validate_uuid(self.id, "Creative Plan Version id")
        validate_workspace_id(self.workspace_id)
        _validate_uuid(self.workflow_id, "Workflow id")
        _validate_uuid(self.creative_plan_id, "Creative Plan id")
        _validate_positive_integer(self.version_number, "Creative Plan version", maximum=1_000_000)
        if self.version_number == 1 and self.supersedes_version_id is not None:
            raise ValueError("first Creative Plan version cannot supersede another version")
        if self.version_number > 1 and self.supersedes_version_id is None:
            raise ValueError("later Creative Plan version requires a superseded version")
        if self.supersedes_version_id is not None:
            _validate_uuid(self.supersedes_version_id, "superseded Creative Plan Version id")
        object.__setattr__(self, "source", CreativePlanSource(self.source))
        if not isinstance(self.payload, CreativePlanPayload):
            raise ValueError("Creative Plan payload is invalid")
        if not isinstance(self.provenance, CreativePlanProvenance):
            raise ValueError("Creative Plan provenance is invalid")
        if self.payload_sha256 != self.payload.payload_sha256:
            raise ValueError("Creative Plan payload hash does not match its payload")
        selected_citations = {
            selection.citation_id
            for direction in self.payload.directions
            for selection in direction.citation_selections
        }
        if not selected_citations.issubset(self.provenance.retrieval_citation_ids):
            raise ValueError("Creative Plan references a citation outside its Planning Context")
        _validate_token(self.actor_id, "Creative Plan actor id")
        if self.source == CreativePlanSource.USER:
            if self.version_number == 1 or not self.revision_reason:
                raise ValueError("human Creative Plan revision requires prior version and reason")
            _validate_text(self.revision_reason, "Creative Plan revision reason", maximum=512)
        elif self.revision_reason is not None:
            raise ValueError("Agent Creative Plan version cannot contain a human revision reason")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() != timedelta(0):
            raise ValueError("Creative Plan creation time must be timezone-aware UTC")

    @classmethod
    def create(
        cls,
        *,
        workspace_id: str,
        workflow_id: str,
        creative_plan_id: str,
        version_number: int,
        supersedes_version_id: str | None,
        source: CreativePlanSource | str,
        payload: CreativePlanPayload,
        provenance: CreativePlanProvenance,
        actor_id: str,
        revision_reason: str | None,
        now: datetime | None = None,
    ) -> CreativePlanVersion:
        return cls(
            id=new_uuid7(),
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            creative_plan_id=creative_plan_id,
            version_number=version_number,
            supersedes_version_id=supersedes_version_id,
            source=CreativePlanSource(source),
            payload=payload,
            provenance=provenance,
            payload_sha256=payload.payload_sha256,
            actor_id=actor_id,
            revision_reason=revision_reason,
            created_at=now or datetime.now(UTC),
        )

    def revise_by_user(
        self,
        *,
        payload: CreativePlanPayload,
        actor_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> CreativePlanVersion:
        return type(self).create(
            workspace_id=self.workspace_id,
            workflow_id=self.workflow_id,
            creative_plan_id=self.creative_plan_id,
            version_number=self.version_number + 1,
            supersedes_version_id=self.id,
            source=CreativePlanSource.USER,
            payload=payload,
            provenance=self.provenance,
            actor_id=actor_id,
            revision_reason=reason,
            now=now,
        )

    def revise_by_agent(
        self,
        *,
        payload: CreativePlanPayload,
        provenance: CreativePlanProvenance,
        actor_id: str,
        now: datetime | None = None,
    ) -> CreativePlanVersion:
        return type(self).create(
            workspace_id=self.workspace_id,
            workflow_id=self.workflow_id,
            creative_plan_id=self.creative_plan_id,
            version_number=self.version_number + 1,
            supersedes_version_id=self.id,
            source=CreativePlanSource.AGENT,
            payload=payload,
            provenance=provenance,
            actor_id=actor_id,
            revision_reason=None,
            now=now,
        )


@dataclass(frozen=True, slots=True)
class CreativePlanHead:
    """Mutable-head fact represented as an immutable optimistic snapshot."""

    workspace_id: str
    workflow_id: str
    creative_plan_id: str
    current_version_id: str
    current_version_number: int
    version: int
    retain_until: datetime
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        validate_workspace_id(self.workspace_id)
        _validate_uuid(self.workflow_id, "Workflow id")
        _validate_uuid(self.creative_plan_id, "Creative Plan id")
        _validate_uuid(self.current_version_id, "Creative Plan current version id")
        _validate_positive_integer(
            self.current_version_number,
            "Creative Plan current version",
            maximum=1_000_000,
        )
        _validate_positive_integer(
            self.version,
            "Creative Plan head version",
            maximum=1_000_000,
        )
        if self.version != self.current_version_number:
            raise ValueError("Creative Plan head version diverges from its current version")
        for value, field_name in (
            (self.retain_until, "retention deadline"),
            (self.created_at, "creation time"),
            (self.updated_at, "update time"),
        ):
            if value.tzinfo is None or value.utcoffset() != timedelta(0):
                raise ValueError(f"Creative Plan {field_name} must be timezone-aware UTC")
        if self.updated_at < self.created_at:
            raise ValueError("Creative Plan update time precedes its creation time")
        if self.retain_until < self.created_at:
            raise ValueError("Creative Plan retention ends before its creation time")

    @classmethod
    def from_first_version(
        cls,
        version: CreativePlanVersion,
        *,
        retain_until: datetime,
    ) -> CreativePlanHead:
        if version.version_number != 1 or version.supersedes_version_id is not None:
            raise ValueError("Creative Plan head requires an initial version")
        return cls(
            workspace_id=version.workspace_id,
            workflow_id=version.workflow_id,
            creative_plan_id=version.creative_plan_id,
            current_version_id=version.id,
            current_version_number=version.version_number,
            version=1,
            retain_until=retain_until,
            created_at=version.created_at,
            updated_at=version.created_at,
        )

    def advance(
        self,
        version: CreativePlanVersion,
        *,
        expected_version: int,
    ) -> CreativePlanHead:
        if type(expected_version) is not int or expected_version != self.version:
            raise ConcurrencyError(
                f"Creative Plan head version is {self.version}, expected {expected_version}"
            )
        if (
            version.workspace_id != self.workspace_id
            or version.workflow_id != self.workflow_id
            or version.creative_plan_id != self.creative_plan_id
            or version.version_number != self.current_version_number + 1
            or version.supersedes_version_id != self.current_version_id
            or version.created_at < self.updated_at
        ):
            raise ConcurrencyError("Creative Plan version does not advance the current head")
        return type(self)(
            workspace_id=self.workspace_id,
            workflow_id=self.workflow_id,
            creative_plan_id=self.creative_plan_id,
            current_version_id=version.id,
            current_version_number=version.version_number,
            version=self.version + 1,
            retain_until=self.retain_until,
            created_at=self.created_at,
            updated_at=version.created_at,
        )
