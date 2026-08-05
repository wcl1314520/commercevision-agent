"""Bounded Planning Context application interface."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from commercevision_domain import (
    DataIntegrityError,
    NotFoundError,
    PlanningContextPolicy,
    PlanningContextSnapshot,
    PlanningContextSource,
    PlanningContextSourceKind,
    ProductBriefRetentionExpiredError,
    RightsDeniedError,
    build_planning_context,
    canonicalize_uuid,
    validate_workspace_id,
)

from .planning_observability import NullPlanningObserver, PlanningObserver

_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$", re.ASCII)

if TYPE_CHECKING:
    from .planning_context_ports import (
        PlanningContextAuthorityPort,
        PlanningContextSnapshotRepositoryPort,
    )


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


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class PlanningContextExactReference:
    """An immutable identity only; it intentionally carries no source payload."""

    kind: PlanningContextSourceKind
    source_id: str
    version_number: int | None
    content_sha256: str
    authority_id: str | None = None
    authority_version: int | None = None
    retrieval_run_id: str | None = None
    retrieval_policy_version: str | None = None
    retrieval_rank: int | None = None
    citation_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", PlanningContextSourceKind(self.kind))
        _validate_uuid(self.source_id, "Planning Context source id")
        if (
            not isinstance(self.content_sha256, str)
            or _SHA256_PATTERN.fullmatch(self.content_sha256) is None
        ):
            raise ValueError("Planning Context source hash must be lowercase SHA-256")
        if self.kind == PlanningContextSourceKind.RETRIEVAL_CITATION:
            self._validate_retrieval_identity()
        elif (
            type(self.version_number) is not int
            or self.version_number < 1
            or any(
                item is not None
                for item in (
                    self.authority_id,
                    self.authority_version,
                    self.retrieval_run_id,
                    self.retrieval_policy_version,
                    self.retrieval_rank,
                    self.citation_id,
                )
            )
        ):
            raise ValueError("versioned Planning Context reference is invalid")

    def _validate_retrieval_identity(self) -> None:
        if (
            self.version_number is not None
            or self.authority_id is None
            or self.authority_version is None
            or self.retrieval_run_id is None
            or self.retrieval_policy_version is None
            or self.retrieval_rank is None
            or self.citation_id is None
        ):
            raise ValueError("Retrieval Citation reference is incomplete")
        _validate_uuid(self.authority_id, "Rights Record id")
        _validate_uuid(self.retrieval_run_id, "Retrieval Run id")
        _validate_token(self.retrieval_policy_version, "retrieval policy version")
        if type(self.authority_version) is not int or self.authority_version < 1:
            raise ValueError("Rights Record version is invalid")
        if type(self.retrieval_rank) is not int or not 1 <= self.retrieval_rank <= 1_000:
            raise ValueError("retrieval rank is invalid")
        _validate_token(self.citation_id, "retrieval citation id")

    @classmethod
    def from_source(cls, source: PlanningContextSource) -> PlanningContextExactReference:
        if not isinstance(source, PlanningContextSource):
            raise ValueError("Planning Context source is invalid")
        return cls(
            kind=source.kind,
            source_id=source.source_id,
            version_number=source.version_number,
            content_sha256=source.content_sha256,
            authority_id=source.authority_id,
            authority_version=source.authority_version,
            retrieval_run_id=source.retrieval_run_id,
            retrieval_policy_version=source.retrieval_policy_version,
            retrieval_rank=source.retrieval_rank,
            citation_id=source.citation_id,
        )


@dataclass(frozen=True, slots=True)
class PlanningContextBuildRequest:
    workspace_id: str
    workflow_id: str
    purpose: str
    product_brief: PlanningContextExactReference
    brand_profile: PlanningContextExactReference | None
    retrieval_citations: tuple[PlanningContextExactReference, ...]
    context_policy_version: str

    def __post_init__(self) -> None:
        validate_workspace_id(self.workspace_id)
        _validate_uuid(self.workflow_id, "Planning Context workflow id")
        _validate_token(self.purpose, "Planning Context purpose")
        _validate_token(self.context_policy_version, "Planning Context policy version")
        if (
            not isinstance(self.product_brief, PlanningContextExactReference)
            or self.product_brief.kind != PlanningContextSourceKind.PRODUCT_BRIEF
        ):
            raise ValueError("Planning Context requires one exact ProductBrief reference")
        if self.brand_profile is not None and (
            not isinstance(self.brand_profile, PlanningContextExactReference)
            or self.brand_profile.kind != PlanningContextSourceKind.BRAND_PROFILE
        ):
            raise ValueError("Planning Context Brand Profile reference is invalid")
        if not isinstance(self.retrieval_citations, tuple) or len(self.retrieval_citations) > 50:
            raise ValueError("Planning Context Retrieval Citations are invalid")
        if any(
            not isinstance(item, PlanningContextExactReference)
            or item.kind != PlanningContextSourceKind.RETRIEVAL_CITATION
            for item in self.retrieval_citations
        ):
            raise ValueError("Planning Context Retrieval Citations are invalid")
        if len(set(self.retrieval_citations)) != len(self.retrieval_citations):
            raise ValueError("Planning Context Retrieval Citations must be unique")


@dataclass(frozen=True, slots=True)
class PlanningContextAuthorizedSource:
    workspace_id: str
    workflow_id: str
    purpose: str
    source: PlanningContextSource
    usable_until: datetime

    def __post_init__(self) -> None:
        validate_workspace_id(self.workspace_id)
        _validate_uuid(self.workflow_id, "authorized Planning Context workflow id")
        _validate_token(self.purpose, "authorized Planning Context purpose")
        if not isinstance(self.source, PlanningContextSource):
            raise ValueError("authorized Planning Context source is invalid")
        object.__setattr__(
            self,
            "usable_until",
            _utc(self.usable_until, "authorized source usability deadline"),
        )


class PlanningContextApplicationService:
    """Return a value while keeping authority loaders and storage private."""

    def __init__(
        self,
        *,
        authority: PlanningContextAuthorityPort,
        snapshots: PlanningContextSnapshotRepositoryPort,
        observer: PlanningObserver | None = None,
    ) -> None:
        self._authority = authority
        self._snapshots = snapshots
        self._observer = observer or NullPlanningObserver()

    def build(self, request: PlanningContextBuildRequest) -> PlanningContextSnapshot:
        if not isinstance(request, PlanningContextBuildRequest):
            raise ValueError("Planning Context build request is invalid")
        with self._observer.observe(
            step="context.build",
            workflow_id=request.workflow_id,
            policy_id=request.context_policy_version,
        ):
            snapshot = self._build(request)
            self._observer.annotate(context_hash=snapshot.context_sha256)
            self._observer.record_context(
                outcome=("clipped" if snapshot.omitted_sources else "complete"),
                clipped_sources=len(snapshot.omitted_sources),
            )
            return snapshot

    def _build(self, request: PlanningContextBuildRequest) -> PlanningContextSnapshot:
        now = _utc(self._authority.database_now(), "authority decision time")
        policy = self._authority.load_policy(version=request.context_policy_version)
        if not isinstance(policy, PlanningContextPolicy) or policy.version != (
            request.context_policy_version
        ):
            raise NotFoundError("Planning Context policy was not found")
        retain_until = self._authority.workflow_retention_deadline(
            workspace_id=request.workspace_id,
            workflow_id=request.workflow_id,
        )
        if retain_until is None:
            raise NotFoundError("Workflow was not found")
        retain_until = _utc(retain_until, "Workflow retention deadline")
        if retain_until <= now:
            raise NotFoundError("Workflow was not found")

        product_brief = self._resolve(request=request, reference=request.product_brief, now=now)
        brand_profile = (
            self._resolve(request=request, reference=request.brand_profile, now=now)
            if request.brand_profile is not None
            else None
        )
        citations = tuple(
            self._resolve(request=request, reference=reference, now=now)
            for reference in request.retrieval_citations
        )
        snapshot = build_planning_context(
            workspace_id=request.workspace_id,
            workflow_id=request.workflow_id,
            product_brief=product_brief,
            brand_profile=brand_profile,
            retrieval_citations=citations,
            policy=policy,
        )
        self._snapshots.save(snapshot, retain_until=retain_until)
        return snapshot

    def reconstruct(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        context_sha256: str,
    ) -> PlanningContextSnapshot:
        validate_workspace_id(workspace_id)
        _validate_uuid(workflow_id, "Planning Context workflow id")
        if not isinstance(context_sha256, str) or _SHA256_PATTERN.fullmatch(context_sha256) is None:
            raise ValueError("Planning Context hash must be lowercase SHA-256")
        snapshot = self._snapshots.get(
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            context_sha256=context_sha256,
        )
        if snapshot is None:
            raise NotFoundError("Planning Context was not found")
        if (
            snapshot.workspace_id != workspace_id
            or snapshot.workflow_id != workflow_id
            or snapshot.context_sha256 != context_sha256
        ):
            raise DataIntegrityError("stored Planning Context identity is inconsistent")
        return snapshot

    def _resolve(
        self,
        *,
        request: PlanningContextBuildRequest,
        reference: PlanningContextExactReference,
        now: datetime,
    ) -> PlanningContextSource:
        authorized = self._authority.load_authorized_source(
            workspace_id=request.workspace_id,
            workflow_id=request.workflow_id,
            purpose=request.purpose,
            reference=reference,
            at=now,
        )
        if authorized is None:
            raise NotFoundError("Planning Context source was not found")
        if (
            authorized.workspace_id != request.workspace_id
            or authorized.workflow_id != request.workflow_id
            or authorized.purpose != request.purpose
            or PlanningContextExactReference.from_source(authorized.source) != reference
        ):
            raise NotFoundError("Planning Context source was not found")
        if authorized.usable_until <= now:
            if reference.kind == PlanningContextSourceKind.RETRIEVAL_CITATION:
                raise RightsDeniedError("Retrieval Citation is no longer usable")
            if reference.kind == PlanningContextSourceKind.PRODUCT_BRIEF:
                raise ProductBriefRetentionExpiredError("ProductBrief source is no longer usable")
            raise NotFoundError("Planning Context source was not found")
        return authorized.source
