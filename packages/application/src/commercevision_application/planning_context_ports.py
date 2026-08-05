"""Narrow authority and persistence seams for Planning Context assembly."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from commercevision_domain import PlanningContextPolicy, PlanningContextSnapshot

from .planning_contexts import PlanningContextAuthorizedSource, PlanningContextExactReference


class PlanningContextAuthorityPort(Protocol):
    """Resolve only current, same-tenant, purpose-authorized source facts.

    Implementations revalidate ProductBrief confirmation, Brand Profile publication/current
    usability, Retrieval Rights, and retention at ``at``. Foreign and missing identities both
    return ``None``.
    """

    def database_now(self) -> datetime: ...

    def load_policy(self, *, version: str) -> PlanningContextPolicy | None: ...

    def workflow_retention_deadline(
        self, *, workspace_id: str, workflow_id: str
    ) -> datetime | None: ...

    def load_authorized_source(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        purpose: str,
        reference: PlanningContextExactReference,
        at: datetime,
    ) -> PlanningContextAuthorizedSource | None: ...


class PlanningContextSnapshotRepositoryPort(Protocol):
    def save(self, snapshot: PlanningContextSnapshot, *, retain_until: datetime) -> None: ...

    def get(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        context_sha256: str,
    ) -> PlanningContextSnapshot | None: ...
