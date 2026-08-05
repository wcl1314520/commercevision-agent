from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from commercevision_application import (
    PlanningContextApplicationService,
    PlanningContextAuthorizedSource,
    PlanningContextBuildRequest,
    PlanningContextExactReference,
)
from commercevision_domain import (
    NotFoundError,
    PlanningContextPolicy,
    PlanningContextSnapshot,
    PlanningContextSource,
    PlanningContextSourceKind,
    RightsDeniedError,
)

NOW = datetime(2026, 8, 5, 2, 0, tzinfo=UTC)
WORKSPACE_ID = "planning-domain"
WORKFLOW_ID = "019b0000-0000-7000-8000-000000000401"
PRODUCT_BRIEF_ID = "019b0000-0000-7000-8000-000000000402"
ASSET_VERSION_ID = "019b0000-0000-7000-8000-000000000403"
RIGHTS_RECORD_ID = "019b0000-0000-7000-8000-000000000404"
RETRIEVAL_RUN_ID = "019b0000-0000-7000-8000-000000000405"


def _product_brief() -> PlanningContextSource:
    return PlanningContextSource.create(
        kind=PlanningContextSourceKind.PRODUCT_BRIEF,
        source_id=PRODUCT_BRIEF_ID,
        version_number=3,
        content_sha256="1" * 64,
        content={"title": "Travel mug"},
    )


def _citation() -> PlanningContextSource:
    return PlanningContextSource.create(
        kind=PlanningContextSourceKind.RETRIEVAL_CITATION,
        source_id=ASSET_VERSION_ID,
        version_number=None,
        content_sha256="2" * 64,
        content={"caption": "A warm kitchen scene"},
        authority_id=RIGHTS_RECORD_ID,
        authority_version=7,
        retrieval_run_id=RETRIEVAL_RUN_ID,
        retrieval_policy_version="retrieval-v1",
        retrieval_rank=1,
        citation_id="retrieval-1",
        image_count=1,
    )


@dataclass
class _Authority:
    sources: dict[PlanningContextExactReference, PlanningContextAuthorizedSource]

    def database_now(self) -> datetime:
        return NOW

    def load_policy(self, *, version: str) -> PlanningContextPolicy | None:
        if version != "planning-context-v1":
            return None
        return PlanningContextPolicy(
            version=version,
            maximum_tokens=2_000,
            maximum_images=4,
        )

    def workflow_retention_deadline(
        self, *, workspace_id: str, workflow_id: str
    ) -> datetime | None:
        if (workspace_id, workflow_id) != (WORKSPACE_ID, WORKFLOW_ID):
            return None
        return NOW + timedelta(days=30)

    def load_authorized_source(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        purpose: str,
        reference: PlanningContextExactReference,
        at: datetime,
    ) -> PlanningContextAuthorizedSource | None:
        assert (workspace_id, workflow_id, purpose, at) == (
            WORKSPACE_ID,
            WORKFLOW_ID,
            "creative-planning",
            NOW,
        )
        return self.sources.get(reference)


class _Snapshots:
    def __init__(self) -> None:
        self.saved: PlanningContextSnapshot | None = None
        self.retain_until: datetime | None = None

    def save(self, snapshot: PlanningContextSnapshot, *, retain_until: datetime) -> None:
        self.saved = snapshot
        self.retain_until = retain_until

    def get(
        self, *, workspace_id: str, workflow_id: str, context_sha256: str
    ) -> PlanningContextSnapshot | None:
        if self.saved is None or self.saved.context_sha256 != context_sha256:
            return None
        if (self.saved.workspace_id, self.saved.workflow_id) != (workspace_id, workflow_id):
            return None
        return self.saved


def _authorized(
    source: PlanningContextSource, *, usable_until: datetime
) -> PlanningContextAuthorizedSource:
    return PlanningContextAuthorizedSource(
        workspace_id=WORKSPACE_ID,
        workflow_id=WORKFLOW_ID,
        purpose="creative-planning",
        source=source,
        usable_until=usable_until,
    )


def _request(
    product_brief: PlanningContextSource, citation: PlanningContextSource
) -> PlanningContextBuildRequest:
    return PlanningContextBuildRequest(
        workspace_id=WORKSPACE_ID,
        workflow_id=WORKFLOW_ID,
        purpose="creative-planning",
        product_brief=PlanningContextExactReference.from_source(product_brief),
        brand_profile=None,
        retrieval_citations=(PlanningContextExactReference.from_source(citation),),
        context_policy_version="planning-context-v1",
    )


def test_build_revalidates_exact_authority_and_persists_reconstructable_snapshot() -> None:
    product_brief = _product_brief()
    citation = _citation()
    product_reference = PlanningContextExactReference.from_source(product_brief)
    citation_reference = PlanningContextExactReference.from_source(citation)
    authority = _Authority(
        sources={
            product_reference: _authorized(product_brief, usable_until=NOW + timedelta(days=30)),
            citation_reference: _authorized(citation, usable_until=NOW + timedelta(hours=1)),
        }
    )
    snapshots = _Snapshots()
    observations: list[tuple[str, dict[str, object]]] = []

    class Observer:
        @contextmanager
        def observe(self, **values):
            observations.append(("span", values))
            yield

        def annotate(self, **values):
            observations.append(("annotate", values))

        def record_context(self, **values):
            observations.append(("context", values))

    service = PlanningContextApplicationService(
        authority=authority,
        snapshots=snapshots,
        observer=Observer(),
    )

    context = service.build(_request(product_brief, citation))

    assert snapshots.saved == context
    assert snapshots.retain_until == NOW + timedelta(days=30)
    assert observations == [
        (
            "span",
            {
                "step": "context.build",
                "workflow_id": WORKFLOW_ID,
                "policy_id": "planning-context-v1",
            },
        ),
        ("annotate", {"context_hash": context.context_sha256}),
        ("context", {"outcome": "complete", "clipped_sources": 0}),
    ]
    assert (
        service.reconstruct(
            workspace_id=WORKSPACE_ID,
            workflow_id=WORKFLOW_ID,
            context_sha256=context.context_sha256,
        )
        == context
    )


def test_build_denies_expired_retrieval_rights() -> None:
    product_brief = _product_brief()
    citation = _citation()
    authority = _Authority(
        sources={
            PlanningContextExactReference.from_source(product_brief): _authorized(
                product_brief, usable_until=NOW + timedelta(days=30)
            ),
            PlanningContextExactReference.from_source(citation): _authorized(
                citation, usable_until=NOW
            ),
        }
    )
    service = PlanningContextApplicationService(authority=authority, snapshots=_Snapshots())

    with pytest.raises(RightsDeniedError, match="no longer usable"):
        service.build(_request(product_brief, citation))


def test_build_treats_stale_or_foreign_exact_reference_as_missing() -> None:
    product_brief = _product_brief()
    citation = _citation()
    stale_product_brief = PlanningContextSource.create(
        kind=PlanningContextSourceKind.PRODUCT_BRIEF,
        source_id=PRODUCT_BRIEF_ID,
        version_number=4,
        content_sha256="9" * 64,
        content={"title": "Changed after the requested confirmation"},
    )
    authority = _Authority(
        sources={
            PlanningContextExactReference.from_source(product_brief): _authorized(
                stale_product_brief,
                usable_until=NOW + timedelta(days=30),
            )
        }
    )
    service = PlanningContextApplicationService(authority=authority, snapshots=_Snapshots())

    with pytest.raises(NotFoundError, match="source was not found"):
        service.build(_request(product_brief, citation))
