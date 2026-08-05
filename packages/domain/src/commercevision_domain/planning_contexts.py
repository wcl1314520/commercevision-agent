"""Immutable, bounded Planning Context values.

This module is deliberately free of repositories and provider adapters. Application
services load and authorize exact source versions, then pass the resulting facts to
``build_planning_context``. Source text is always serialized below ``source_data``;
it never participates in the policy or authority model.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from unicodedata import category

from .ids import canonicalize_uuid
from .workspace_identity import validate_workspace_id

_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_MAX_SOURCES = 52
_MAX_CONTENT_BYTES = 64 * 1024
_MAX_CONTEXT_BYTES = 4 * 1024 * 1024
_MAX_JSON_DEPTH = 8
_MAX_JSON_NODES = 1_024
_MAX_COLLECTION_ITEMS = 128
_MAX_STRING_CHARACTERS = 16_384


class PlanningContextSourceKind(StrEnum):
    PRODUCT_BRIEF = "PRODUCT_BRIEF"
    BRAND_PROFILE = "BRAND_PROFILE"
    RETRIEVAL_CITATION = "RETRIEVAL_CITATION"


class PlanningContextOmissionReason(StrEnum):
    DUPLICATE_CONTENT = "DUPLICATE_CONTENT"
    TOKEN_BUDGET_EXCEEDED = "TOKEN_BUDGET_EXCEEDED"
    IMAGE_BUDGET_EXCEEDED = "IMAGE_BUDGET_EXCEEDED"


def _validate_token(value: str, field: str) -> str:
    if not isinstance(value, str) or _TOKEN_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")
    return value


def _validate_uuid(value: str, field: str) -> str:
    try:
        canonical = canonicalize_uuid(value)
    except ValueError:
        raise ValueError(f"{field} must be a canonical UUID") from None
    if canonical != value:
        raise ValueError(f"{field} must be a canonical UUID")
    return value


def _validate_positive_integer(value: int, field: str, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{field} must be between 1 and {maximum}")
    return value


def _validate_json_shape(value: object) -> None:
    stack: list[tuple[object, int]] = [(value, 0)]
    visited = 0
    while stack:
        current, depth = stack.pop()
        visited += 1
        if visited > _MAX_JSON_NODES:
            raise ValueError("Planning Context source content exceeds the node limit")
        if depth > _MAX_JSON_DEPTH:
            raise ValueError("Planning Context source content exceeds the depth limit")
        if current is None or isinstance(current, bool | int):
            continue
        if isinstance(current, float):
            continue
        if isinstance(current, str):
            if len(current) > _MAX_STRING_CHARACTERS or any(
                category(character) == "Cc" and character not in "\t\n\r" for character in current
            ):
                raise ValueError("Planning Context source text is invalid")
            continue
        if isinstance(current, Mapping):
            if len(current) > _MAX_COLLECTION_ITEMS or any(
                not isinstance(key, str) or not key for key in current
            ):
                raise ValueError("Planning Context source object is invalid")
            stack.extend((item, depth + 1) for pair in current.items() for item in pair)
            continue
        if isinstance(current, (list, tuple)):
            if len(current) > _MAX_COLLECTION_ITEMS:
                raise ValueError("Planning Context source collection is too large")
            stack.extend((item, depth + 1) for item in current)
            continue
        raise ValueError("Planning Context source content must be JSON-compatible")


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
        raise ValueError("Planning Context source content must be finite JSON") from exc
    if len(encoded.encode("utf-8")) > _MAX_CONTENT_BYTES:
        raise ValueError("Planning Context source content exceeds the byte limit")
    return encoded


def _canonical_snapshot_json(value: object) -> str:
    """Serialize already-validated source values under the aggregate bound."""

    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (RecursionError, TypeError, ValueError) as exc:
        raise ValueError("Planning Context snapshot must be finite JSON") from exc
    if len(encoded.encode("utf-8")) > _MAX_CONTEXT_BYTES:
        raise ValueError("Planning Context snapshot exceeds the byte limit")
    return encoded


@dataclass(frozen=True, slots=True)
class PlanningContextPolicy:
    version: str
    maximum_tokens: int
    maximum_images: int

    def __post_init__(self) -> None:
        _validate_token(self.version, "Planning Context policy version")
        _validate_positive_integer(self.maximum_tokens, "token budget", 1_000_000)
        if type(self.maximum_images) is not int or not 0 <= self.maximum_images <= 1_000:
            raise ValueError("image budget must be between 0 and 1000")

    def to_canonical_data(self) -> dict[str, object]:
        return {
            "version": self.version,
            "maximum_tokens": self.maximum_tokens,
            "maximum_images": self.maximum_images,
        }


@dataclass(frozen=True, slots=True)
class PlanningContextSource:
    kind: PlanningContextSourceKind
    source_id: str
    version_number: int | None
    content_sha256: str
    content_json: str
    authority_id: str | None = None
    authority_version: int | None = None
    retrieval_run_id: str | None = None
    retrieval_policy_version: str | None = None
    retrieval_rank: int | None = None
    citation_id: str | None = None
    image_count: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", PlanningContextSourceKind(self.kind))
        _validate_uuid(self.source_id, "Planning Context source id")
        if self.version_number is not None:
            _validate_positive_integer(self.version_number, "source version", 2_147_483_647)
        if (
            not isinstance(self.content_sha256, str)
            or _SHA256_PATTERN.fullmatch(self.content_sha256) is None
        ):
            raise ValueError("source content hash must be lowercase SHA-256")
        if not isinstance(self.content_json, str):
            raise ValueError("source content must be canonical JSON")
        try:
            content = json.loads(self.content_json)
        except (RecursionError, json.JSONDecodeError) as exc:
            raise ValueError("source content must be canonical JSON") from exc
        if not isinstance(content, dict) or _canonical_json(content) != self.content_json:
            raise ValueError("source content must be a canonical JSON object")
        if type(self.image_count) is not int or not 0 <= self.image_count <= 16:
            raise ValueError("source image count must be between 0 and 16")
        if self.kind == PlanningContextSourceKind.RETRIEVAL_CITATION:
            if (
                self.version_number is not None
                or self.authority_id is None
                or self.authority_version is None
                or self.retrieval_run_id is None
                or self.retrieval_policy_version is None
                or self.retrieval_rank is None
                or self.citation_id is None
            ):
                raise ValueError(
                    "Retrieval Citation requires exact Rights, rank, and citation identity"
                )
            _validate_uuid(self.authority_id, "Retrieval Citation Rights Record id")
            _validate_uuid(self.retrieval_run_id, "Retrieval Run id")
            _validate_token(self.retrieval_policy_version, "retrieval policy version")
            _validate_positive_integer(
                self.authority_version,
                "Retrieval Citation Rights Record version",
                2_147_483_647,
            )
            _validate_positive_integer(self.retrieval_rank, "retrieval rank", 1_000)
            _validate_token(self.citation_id, "retrieval citation id")
        elif self.version_number is None:
            raise ValueError("ProductBrief and Brand Profile sources require exact versions")
        elif any(
            value is not None
            for value in (
                self.authority_id,
                self.authority_version,
                self.retrieval_run_id,
                self.retrieval_policy_version,
                self.retrieval_rank,
                self.citation_id,
            )
        ):
            raise ValueError("only Retrieval Citations can carry Rights and retrieval identity")
        elif self.image_count:
            raise ValueError("only Retrieval Citations can consume the image budget")

    @classmethod
    def create(
        cls,
        *,
        kind: PlanningContextSourceKind | str,
        source_id: str,
        version_number: int | None,
        content_sha256: str,
        content: Mapping[str, object],
        authority_id: str | None = None,
        authority_version: int | None = None,
        retrieval_run_id: str | None = None,
        retrieval_policy_version: str | None = None,
        retrieval_rank: int | None = None,
        citation_id: str | None = None,
        image_count: int = 0,
    ) -> PlanningContextSource:
        return cls(
            kind=PlanningContextSourceKind(kind),
            source_id=source_id,
            version_number=version_number,
            content_sha256=content_sha256,
            content_json=_canonical_json(dict(content)),
            authority_id=authority_id,
            authority_version=authority_version,
            retrieval_run_id=retrieval_run_id,
            retrieval_policy_version=retrieval_policy_version,
            retrieval_rank=retrieval_rank,
            citation_id=citation_id,
            image_count=image_count,
        )

    @property
    def token_count(self) -> int:
        return max(1, (len(self.content_json.encode("utf-8")) + 3) // 4)

    def content(self) -> dict[str, object]:
        value = json.loads(self.content_json)
        assert isinstance(value, dict)
        return value

    def identity_data(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "source_id": self.source_id,
            "version_number": self.version_number,
            "content_sha256": self.content_sha256,
            "authority_id": self.authority_id,
            "authority_version": self.authority_version,
            "retrieval_run_id": self.retrieval_run_id,
            "retrieval_policy_version": self.retrieval_policy_version,
            "retrieval_rank": self.retrieval_rank,
            "citation_id": self.citation_id,
        }


@dataclass(frozen=True, slots=True)
class PlanningContextIncludedSource:
    source: PlanningContextSource
    citation_number: int | None

    def __post_init__(self) -> None:
        if not isinstance(self.source, PlanningContextSource):
            raise ValueError("included Planning Context source is invalid")
        is_citation = self.source.kind == PlanningContextSourceKind.RETRIEVAL_CITATION
        if is_citation != (self.citation_number is not None):
            raise ValueError("only Retrieval Citations receive citation numbers")
        if self.citation_number is not None:
            _validate_positive_integer(self.citation_number, "citation number", 1_000)

    def to_canonical_data(self) -> dict[str, object]:
        return {
            **self.source.identity_data(),
            "citation_number": self.citation_number,
            "content": self.source.content(),
        }


@dataclass(frozen=True, slots=True)
class PlanningContextOmittedSource:
    source: PlanningContextSource
    reason: PlanningContextOmissionReason

    def __post_init__(self) -> None:
        if not isinstance(self.source, PlanningContextSource):
            raise ValueError("omitted Planning Context source is invalid")
        object.__setattr__(self, "reason", PlanningContextOmissionReason(self.reason))

    def to_canonical_data(self) -> dict[str, object]:
        return {**self.source.identity_data(), "reason": self.reason.value}


@dataclass(frozen=True, slots=True)
class PlanningContextSnapshot:
    workspace_id: str
    workflow_id: str
    policy: PlanningContextPolicy
    included_sources: tuple[PlanningContextIncludedSource, ...]
    omitted_sources: tuple[PlanningContextOmittedSource, ...]
    used_tokens: int
    used_images: int
    schema_version: str = "planning-context.v1"

    def __post_init__(self) -> None:
        validate_workspace_id(self.workspace_id)
        _validate_uuid(self.workflow_id, "Planning Context workflow id")
        if not isinstance(self.policy, PlanningContextPolicy):
            raise ValueError("Planning Context policy is invalid")
        if self.schema_version != "planning-context.v1":
            raise ValueError("Planning Context schema version is unsupported")
        if (
            not isinstance(self.included_sources, tuple)
            or not 1 <= len(self.included_sources) <= _MAX_SOURCES
        ):
            raise ValueError("included Planning Context sources are invalid")
        if any(
            not isinstance(item, PlanningContextIncludedSource) for item in self.included_sources
        ):
            raise ValueError("included Planning Context sources are invalid")
        if not isinstance(self.omitted_sources, tuple) or len(self.omitted_sources) > _MAX_SOURCES:
            raise ValueError("omitted Planning Context sources are invalid")
        if any(not isinstance(item, PlanningContextOmittedSource) for item in self.omitted_sources):
            raise ValueError("omitted Planning Context sources are invalid")
        if (
            type(self.used_tokens) is not int
            or not 1 <= self.used_tokens <= self.policy.maximum_tokens
        ):
            raise ValueError("Planning Context token usage is invalid")
        if (
            type(self.used_images) is not int
            or not 0 <= self.used_images <= self.policy.maximum_images
        ):
            raise ValueError("Planning Context image usage is invalid")
        sources = tuple(item.source for item in self.included_sources)
        if sources != tuple(sorted(sources, key=_source_order)):
            raise ValueError("included Planning Context source ordering is invalid")
        kinds = tuple(source.kind for source in sources)
        if (
            kinds.count(PlanningContextSourceKind.PRODUCT_BRIEF) != 1
            or kinds.count(PlanningContextSourceKind.BRAND_PROFILE) > 1
        ):
            raise ValueError("Planning Context authoritative source cardinality is invalid")
        citation_numbers = tuple(
            item.citation_number
            for item in self.included_sources
            if item.source.kind == PlanningContextSourceKind.RETRIEVAL_CITATION
        )
        expected_citation_numbers = tuple(range(1, len(citation_numbers) + 1))
        if citation_numbers != expected_citation_numbers:
            raise ValueError("Planning Context citation numbering is invalid")
        if self.used_tokens != sum(source.token_count for source in sources):
            raise ValueError("Planning Context token usage is inconsistent")
        if self.used_images != sum(source.image_count for source in sources):
            raise ValueError("Planning Context image usage is inconsistent")

    def to_canonical_data(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "workspace_id": self.workspace_id,
            "workflow_id": self.workflow_id,
            "policy": self.policy.to_canonical_data(),
            "budget_usage": {
                "tokens": self.used_tokens,
                "images": self.used_images,
            },
            "source_data": [item.to_canonical_data() for item in self.included_sources],
            "omitted_sources": [item.to_canonical_data() for item in self.omitted_sources],
        }

    @property
    def context_sha256(self) -> str:
        encoded = _canonical_snapshot_json(self.to_canonical_data())
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _source_order(source: PlanningContextSource) -> tuple[int, int, str, int]:
    kind_order = {
        PlanningContextSourceKind.PRODUCT_BRIEF: 0,
        PlanningContextSourceKind.BRAND_PROFILE: 1,
        PlanningContextSourceKind.RETRIEVAL_CITATION: 2,
    }
    return (
        kind_order[source.kind],
        source.retrieval_rank or 0,
        source.source_id,
        source.version_number or 0,
    )


def build_planning_context(
    *,
    workspace_id: str,
    workflow_id: str,
    product_brief: PlanningContextSource,
    brand_profile: PlanningContextSource | None,
    retrieval_citations: Sequence[PlanningContextSource],
    policy: PlanningContextPolicy,
) -> PlanningContextSnapshot:
    """Build the value from facts already authorized by the application layer."""

    if product_brief.kind != PlanningContextSourceKind.PRODUCT_BRIEF:
        raise ValueError("Planning Context requires one ProductBrief source")
    if brand_profile is not None and brand_profile.kind != PlanningContextSourceKind.BRAND_PROFILE:
        raise ValueError("Planning Context Brand Profile source is invalid")
    citations = tuple(retrieval_citations)
    if len(citations) > 50 or any(
        item.kind != PlanningContextSourceKind.RETRIEVAL_CITATION for item in citations
    ):
        raise ValueError("Planning Context Retrieval Citations are invalid")
    citation_ids = [item.citation_id for item in citations]
    retrieval_ranks = [item.retrieval_rank for item in citations]
    source_versions = [
        (
            item.retrieval_run_id,
            item.source_id,
            item.authority_id,
            item.authority_version,
        )
        for item in citations
    ]
    if (
        len(set(citation_ids)) != len(citation_ids)
        or len(set(retrieval_ranks)) != len(retrieval_ranks)
        or len(set(source_versions)) != len(source_versions)
    ):
        raise ValueError("Retrieval Citations require unique exact identities")
    if not isinstance(policy, PlanningContextPolicy):
        raise ValueError("Planning Context policy is invalid")

    required = (product_brief,) + ((brand_profile,) if brand_profile is not None else ())
    required_tokens = sum(source.token_count for source in required)
    if required_tokens > policy.maximum_tokens:
        raise ValueError("authoritative Planning Context sources exceed the token budget")

    included = list(required)
    omitted: list[PlanningContextOmittedSource] = []
    used_tokens = required_tokens
    used_images = 0
    seen_hashes: set[str] = set()
    for citation in sorted(citations, key=_source_order):
        if citation.content_sha256 in seen_hashes:
            omitted.append(
                PlanningContextOmittedSource(
                    source=citation,
                    reason=PlanningContextOmissionReason.DUPLICATE_CONTENT,
                )
            )
            continue
        seen_hashes.add(citation.content_sha256)
        if used_images + citation.image_count > policy.maximum_images:
            omitted.append(
                PlanningContextOmittedSource(
                    source=citation,
                    reason=PlanningContextOmissionReason.IMAGE_BUDGET_EXCEEDED,
                )
            )
            continue
        if used_tokens + citation.token_count > policy.maximum_tokens:
            omitted.append(
                PlanningContextOmittedSource(
                    source=citation,
                    reason=PlanningContextOmissionReason.TOKEN_BUDGET_EXCEEDED,
                )
            )
            continue
        included.append(citation)
        used_tokens += citation.token_count
        used_images += citation.image_count

    included.sort(key=_source_order)
    citation_number = 0
    included_values: list[PlanningContextIncludedSource] = []
    for source in included:
        number: int | None = None
        if source.kind == PlanningContextSourceKind.RETRIEVAL_CITATION:
            citation_number += 1
            number = citation_number
        included_values.append(PlanningContextIncludedSource(source=source, citation_number=number))
    omitted.sort(key=lambda item: (*_source_order(item.source), item.reason.value))
    return PlanningContextSnapshot(
        workspace_id=workspace_id,
        workflow_id=workflow_id,
        policy=policy,
        included_sources=tuple(included_values),
        omitted_sources=tuple(omitted),
        used_tokens=used_tokens,
        used_images=used_images,
    )
