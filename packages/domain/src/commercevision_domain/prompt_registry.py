"""Immutable Prompt Registry revisions and lifecycle values."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from unicodedata import category

from commercevision_domain.ids import canonicalize_uuid, new_uuid7
from commercevision_domain.workflow.errors import ConcurrencyError, InvalidTransitionError
from commercevision_domain.workspace_identity import validate_workspace_id

_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)
_VARIABLE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$", re.ASCII)
_PLACEHOLDER_PATTERN = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]{0,63})\s*}}")
_SECRET_VARIABLE_PATTERN = re.compile(
    r"(?:^|_)(?:api_key|access_key|secret|token|password|credential)(?:_|$)",
    re.IGNORECASE,
)
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"\b(?:api[_-]?key|access[_-]?key|[a-z0-9_]*(?:secret|token|password|credential)"
    r"[a-z0-9_]*)\s*[:=]",
    re.IGNORECASE,
)
_SEMANTIC_REVISION_PATTERN = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$",
    re.ASCII,
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_KNOWN_INPUT_SCHEMAS = frozenset({"planning-context.v1"})
_KNOWN_OUTPUT_SCHEMAS = frozenset({"creative-plan.v1"})
_MAX_APPLICABILITY_ITEMS = 32
_MAX_VARIABLES = 64
_MAX_CONTENT_CHARACTERS = 32_768


class PromptRevisionStatus(StrEnum):
    DRAFT = "DRAFT"
    REVIEW = "REVIEW"
    STAGING = "STAGING"
    PRODUCTION = "PRODUCTION"
    DEPRECATED = "DEPRECATED"


_PROMPT_REVISION_TRANSITIONS = {
    PromptRevisionStatus.DRAFT: PromptRevisionStatus.REVIEW,
    PromptRevisionStatus.REVIEW: PromptRevisionStatus.STAGING,
    PromptRevisionStatus.STAGING: PromptRevisionStatus.PRODUCTION,
    PromptRevisionStatus.PRODUCTION: PromptRevisionStatus.DEPRECATED,
}


def _validate_token(value: str, field_name: str) -> None:
    if not isinstance(value, str) or _TOKEN_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} is invalid")


def _validate_text(value: str, field_name: str, *, maximum: int) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(category(character) == "Cc" for character in value)
    ):
        raise ValueError(f"{field_name} is invalid")


def _validate_template_content(value: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > _MAX_CONTENT_CHARACTERS
        or any(category(character) == "Cc" and character != "\n" for character in value)
    ):
        raise ValueError("Prompt content is invalid")


def _validate_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must be timezone-aware UTC")


def _validate_uuid(value: str, field_name: str) -> None:
    try:
        canonical = canonicalize_uuid(value)
    except ValueError:
        raise ValueError(f"{field_name} must be a canonical UUID") from None
    if canonical != value:
        raise ValueError(f"{field_name} must be a canonical UUID")


def _validate_applicability(value: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    if not isinstance(value, tuple) or not value or len(value) > _MAX_APPLICABILITY_ITEMS:
        raise ValueError(f"{field_name} is invalid")
    for item in value:
        _validate_token(item, field_name)
    if len(value) != len(set(value)):
        raise ValueError(f"{field_name} contains duplicates")
    return tuple(sorted(value))


@dataclass(frozen=True, slots=True)
class PromptTemplateVariable:
    name: str
    required: bool

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or _VARIABLE_PATTERN.fullmatch(self.name) is None:
            raise ValueError("Prompt variable name is invalid")
        if not isinstance(self.required, bool):
            raise ValueError("Prompt variable required flag must be a boolean")


@dataclass(frozen=True, slots=True)
class PromptRevision:
    id: str
    workspace_id: str
    prompt_id: str
    semantic_revision: str
    node: str
    category_applicability: tuple[str, ...]
    model_family_applicability: tuple[str, ...]
    input_schema_version: str
    output_schema_version: str
    policy_version: str
    content: str
    variables: tuple[PromptTemplateVariable, ...]
    content_sha256: str
    status: PromptRevisionStatus
    version: int
    created_by: str
    change_summary: str
    created_at: datetime
    updated_at: datetime
    submitted_by: str | None = None
    submitted_at: datetime | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    published_by: str | None = None
    published_at: datetime | None = None
    deprecated_by: str | None = None
    deprecated_at: datetime | None = None

    def __post_init__(self) -> None:
        _validate_uuid(self.id, "Prompt Revision id")
        validate_workspace_id(self.workspace_id)
        _validate_token(self.prompt_id, "Prompt id")
        if (
            not isinstance(self.semantic_revision, str)
            or _SEMANTIC_REVISION_PATTERN.fullmatch(self.semantic_revision) is None
        ):
            raise ValueError("Prompt semantic revision is invalid")
        _validate_token(self.node, "Prompt node")
        object.__setattr__(
            self,
            "category_applicability",
            _validate_applicability(self.category_applicability, "Prompt category applicability"),
        )
        object.__setattr__(
            self,
            "model_family_applicability",
            _validate_applicability(
                self.model_family_applicability,
                "Prompt model-family applicability",
            ),
        )
        if self.input_schema_version not in _KNOWN_INPUT_SCHEMAS:
            raise ValueError("Prompt input schema version is unsupported")
        if self.output_schema_version not in _KNOWN_OUTPUT_SCHEMAS:
            raise ValueError("Prompt output schema version is unsupported")
        _validate_token(self.policy_version, "Prompt policy version")
        _validate_template_content(self.content)
        if (
            _SECRET_ASSIGNMENT_PATTERN.search(self.content) is not None
            or "-----BEGIN PRIVATE KEY-----" in self.content.upper()
        ):
            raise ValueError("Prompt content cannot contain secret material")
        if (
            not isinstance(self.variables, tuple)
            or len(self.variables) > _MAX_VARIABLES
            or any(not isinstance(item, PromptTemplateVariable) for item in self.variables)
        ):
            raise ValueError("Prompt variables are invalid")
        names = [item.name for item in self.variables]
        if len(names) != len(set(names)):
            raise ValueError("Prompt variables contain duplicate names")
        if any(_SECRET_VARIABLE_PATTERN.search(name) is not None for name in names):
            raise ValueError("Prompt variables cannot request secret material")
        if "{%" in self.content or "{#" in self.content:
            raise ValueError("Prompt templates may contain substitutions only")
        placeholders = set(_PLACEHOLDER_PATTERN.findall(self.content))
        stripped_content = _PLACEHOLDER_PATTERN.sub("", self.content)
        if "{{" in stripped_content or "}}" in stripped_content or placeholders != set(names):
            raise ValueError("Prompt placeholders do not match declared variables")
        object.__setattr__(
            self, "variables", tuple(sorted(self.variables, key=lambda item: item.name))
        )
        expected_hash = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        if (
            not isinstance(self.content_sha256, str)
            or _SHA256_PATTERN.fullmatch(self.content_sha256) is None
            or self.content_sha256 != expected_hash
        ):
            raise ValueError("Prompt content hash does not match its content")
        object.__setattr__(self, "status", PromptRevisionStatus(self.status))
        if not isinstance(self.version, int) or isinstance(self.version, bool) or self.version < 1:
            raise ValueError("Prompt Revision version must be positive")
        _validate_text(self.created_by, "Prompt creator", maximum=128)
        _validate_text(self.change_summary, "Prompt change summary", maximum=512)
        _validate_utc(self.created_at, "Prompt created_at")
        _validate_utc(self.updated_at, "Prompt updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("Prompt updated_at cannot precede created_at")
        lifecycle_facts = (
            (self.submitted_by, self.submitted_at, "Prompt review submission"),
            (self.reviewed_by, self.reviewed_at, "Prompt review"),
            (self.published_by, self.published_at, "Prompt publication"),
            (self.deprecated_by, self.deprecated_at, "Prompt deprecation"),
        )
        expected_facts = {
            PromptRevisionStatus.DRAFT: 0,
            PromptRevisionStatus.REVIEW: 1,
            PromptRevisionStatus.STAGING: 2,
            PromptRevisionStatus.PRODUCTION: 3,
            PromptRevisionStatus.DEPRECATED: 4,
        }[self.status]
        lifecycle_times: list[datetime] = []
        for index, (actor, occurred_at, field_name) in enumerate(lifecycle_facts):
            if (actor is None) != (occurred_at is None):
                raise ValueError(f"{field_name} actor and timestamp must be complete")
            if index < expected_facts:
                if actor is None or occurred_at is None:
                    raise ValueError(f"{field_name} is required for {self.status.value}")
                _validate_text(actor, f"{field_name} actor", maximum=128)
                _validate_utc(occurred_at, f"{field_name} timestamp")
                lifecycle_times.append(occurred_at)
            elif actor is not None:
                raise ValueError(f"{field_name} is invalid for {self.status.value}")
        if lifecycle_times:
            if lifecycle_times != sorted(lifecycle_times) or lifecycle_times[0] < self.created_at:
                raise ValueError("Prompt lifecycle timestamps are out of order")
            if lifecycle_times[-1] != self.updated_at:
                raise ValueError("Prompt updated_at must equal its latest lifecycle timestamp")
        elif self.updated_at != self.created_at:
            raise ValueError("Draft Prompt updated_at must equal created_at")

    @classmethod
    def create(
        cls,
        *,
        workspace_id: str,
        prompt_id: str,
        semantic_revision: str,
        node: str,
        category_applicability: tuple[str, ...],
        model_family_applicability: tuple[str, ...],
        input_schema_version: str,
        output_schema_version: str,
        policy_version: str,
        content: str,
        variables: tuple[PromptTemplateVariable, ...],
        created_by: str,
        change_summary: str,
        now: datetime | None = None,
    ) -> PromptRevision:
        created_at = now or datetime.now(UTC)
        return cls(
            id=new_uuid7(),
            workspace_id=workspace_id,
            prompt_id=prompt_id,
            semantic_revision=semantic_revision,
            node=node,
            category_applicability=category_applicability,
            model_family_applicability=model_family_applicability,
            input_schema_version=input_schema_version,
            output_schema_version=output_schema_version,
            policy_version=policy_version,
            content=content,
            variables=variables,
            content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            status=PromptRevisionStatus.DRAFT,
            version=1,
            created_by=created_by,
            change_summary=change_summary,
            created_at=created_at,
            updated_at=created_at,
        )

    def _transition_time(
        self,
        *,
        target: PromptRevisionStatus,
        expected_version: int,
        now: datetime | None,
    ) -> datetime:
        if (
            not isinstance(expected_version, int)
            or isinstance(expected_version, bool)
            or self.version != expected_version
        ):
            raise ConcurrencyError(
                f"Prompt Revision {self.id} version is {self.version}, expected {expected_version}"
            )
        if _PROMPT_REVISION_TRANSITIONS.get(self.status) != target:
            raise InvalidTransitionError(
                f"illegal Prompt Revision transition: {self.status.value} -> {target.value}"
            )
        occurred_at = now or datetime.now(UTC)
        _validate_utc(occurred_at, "Prompt lifecycle timestamp")
        if occurred_at < self.updated_at:
            raise ValueError("Prompt lifecycle timestamp cannot move backwards")
        return occurred_at

    def submit_for_review(
        self,
        *,
        expected_version: int,
        actor_id: str,
        now: datetime | None = None,
    ) -> PromptRevision:
        _validate_text(actor_id, "Prompt review submitter", maximum=128)
        occurred_at = self._transition_time(
            target=PromptRevisionStatus.REVIEW,
            expected_version=expected_version,
            now=now,
        )
        return replace(
            self,
            status=PromptRevisionStatus.REVIEW,
            version=self.version + 1,
            updated_at=occurred_at,
            submitted_by=actor_id,
            submitted_at=occurred_at,
        )

    def stage(
        self,
        *,
        expected_version: int,
        reviewer_id: str,
        now: datetime | None = None,
    ) -> PromptRevision:
        _validate_text(reviewer_id, "Prompt reviewer", maximum=128)
        occurred_at = self._transition_time(
            target=PromptRevisionStatus.STAGING,
            expected_version=expected_version,
            now=now,
        )
        return replace(
            self,
            status=PromptRevisionStatus.STAGING,
            version=self.version + 1,
            updated_at=occurred_at,
            reviewed_by=reviewer_id,
            reviewed_at=occurred_at,
        )

    def publish(
        self,
        *,
        expected_version: int,
        actor_id: str,
        now: datetime | None = None,
    ) -> PromptRevision:
        _validate_text(actor_id, "Prompt publisher", maximum=128)
        occurred_at = self._transition_time(
            target=PromptRevisionStatus.PRODUCTION,
            expected_version=expected_version,
            now=now,
        )
        return replace(
            self,
            status=PromptRevisionStatus.PRODUCTION,
            version=self.version + 1,
            updated_at=occurred_at,
            published_by=actor_id,
            published_at=occurred_at,
        )

    def deprecate(
        self,
        *,
        expected_version: int,
        actor_id: str,
        now: datetime | None = None,
    ) -> PromptRevision:
        _validate_text(actor_id, "Prompt deprecator", maximum=128)
        occurred_at = self._transition_time(
            target=PromptRevisionStatus.DEPRECATED,
            expected_version=expected_version,
            now=now,
        )
        return replace(
            self,
            status=PromptRevisionStatus.DEPRECATED,
            version=self.version + 1,
            updated_at=occurred_at,
            deprecated_by=actor_id,
            deprecated_at=occurred_at,
        )


@dataclass(frozen=True, slots=True)
class PromptProductionPointer:
    workspace_id: str
    prompt_id: str
    node: str
    revision_id: str
    semantic_revision: str
    content_sha256: str
    version: int
    updated_by: str
    updated_at: datetime

    def __post_init__(self) -> None:
        validate_workspace_id(self.workspace_id)
        _validate_token(self.prompt_id, "Prompt pointer id")
        _validate_token(self.node, "Prompt pointer node")
        _validate_uuid(self.revision_id, "Prompt pointer revision id")
        if (
            not isinstance(self.semantic_revision, str)
            or _SEMANTIC_REVISION_PATTERN.fullmatch(self.semantic_revision) is None
        ):
            raise ValueError("Prompt pointer semantic revision is invalid")
        if (
            not isinstance(self.content_sha256, str)
            or _SHA256_PATTERN.fullmatch(self.content_sha256) is None
        ):
            raise ValueError("Prompt pointer content hash is invalid")
        if not isinstance(self.version, int) or isinstance(self.version, bool) or self.version < 1:
            raise ValueError("Prompt pointer version must be positive")
        _validate_text(self.updated_by, "Prompt pointer actor", maximum=128)
        _validate_utc(self.updated_at, "Prompt pointer updated_at")

    @classmethod
    def create(
        cls,
        *,
        revision: PromptRevision,
        actor_id: str,
        now: datetime | None = None,
    ) -> PromptProductionPointer:
        if revision.status != PromptRevisionStatus.PRODUCTION:
            raise InvalidTransitionError("only a PRODUCTION Prompt Revision can be selected")
        selected_at = now or datetime.now(UTC)
        _validate_utc(selected_at, "Prompt pointer updated_at")
        assert revision.published_at is not None
        if selected_at < revision.published_at:
            raise ValueError("Prompt pointer cannot precede Prompt publication")
        return cls(
            workspace_id=revision.workspace_id,
            prompt_id=revision.prompt_id,
            node=revision.node,
            revision_id=revision.id,
            semantic_revision=revision.semantic_revision,
            content_sha256=revision.content_sha256,
            version=1,
            updated_by=actor_id,
            updated_at=selected_at,
        )

    def repoint(
        self,
        *,
        revision: PromptRevision,
        expected_version: int,
        actor_id: str,
        now: datetime | None = None,
    ) -> PromptProductionPointer:
        if (
            not isinstance(expected_version, int)
            or isinstance(expected_version, bool)
            or self.version != expected_version
        ):
            raise ConcurrencyError(
                f"Prompt pointer version is {self.version}, expected {expected_version}"
            )
        if revision.status != PromptRevisionStatus.PRODUCTION:
            raise InvalidTransitionError("only a PRODUCTION Prompt Revision can be selected")
        if (
            revision.workspace_id != self.workspace_id
            or revision.prompt_id != self.prompt_id
            or revision.node != self.node
        ):
            raise InvalidTransitionError("Prompt pointer target belongs to another identity")
        if revision.id == self.revision_id:
            return self
        selected_at = now or datetime.now(UTC)
        _validate_utc(selected_at, "Prompt pointer updated_at")
        if selected_at < self.updated_at:
            raise ValueError("Prompt pointer timestamp cannot move backwards")
        return replace(
            self,
            revision_id=revision.id,
            semantic_revision=revision.semantic_revision,
            content_sha256=revision.content_sha256,
            version=self.version + 1,
            updated_by=actor_id,
            updated_at=selected_at,
        )
