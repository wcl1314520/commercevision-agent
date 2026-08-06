"""Typed persistence seams for deterministic model routing."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from types import TracebackType
from typing import Protocol

from commercevision_domain import (
    EndpointRouteObservation,
    ModelRouteDecision,
    ModelRoutePolicy,
    ModelRouteRequest,
    ProviderEndpointCapabilityVersion,
)

from commercevision_application.asset_ports import AssetAuditPort, AssetIdempotencyPort


@dataclass(frozen=True, slots=True)
class ModelRoutingAuthoritySnapshot:
    policy_version_id: str
    policy: ModelRoutePolicy
    capabilities: tuple[ProviderEndpointCapabilityVersion, ...]
    observations: tuple[EndpointRouteObservation, ...]


@dataclass(frozen=True, slots=True)
class ModelRouteDecisionWrite:
    workspace_id: str
    request: ModelRouteRequest
    policy_key: str
    policy_version_id: str
    decision: ModelRouteDecision
    idempotency_scope_sha256: str
    idempotency_key_sha256: str
    estimated_cost: Decimal
    currency: str
    created_by: str


@dataclass(frozen=True, slots=True)
class PersistedModelRouteDecision:
    policy_version_id: str
    decision: ModelRouteDecision
    estimated_cost: Decimal
    currency: str


class ModelRouteAuthorityRepositoryPort(Protocol):
    def load_current_authority(
        self,
        *,
        request: ModelRouteRequest,
        policy_key: str,
    ) -> ModelRoutingAuthoritySnapshot: ...

    def add_decision(self, record: ModelRouteDecisionWrite) -> None: ...

    def get_decision(
        self,
        *,
        workspace_id: str,
        decision_sha256: str,
    ) -> PersistedModelRouteDecision | None: ...


class ModelRouterUnitOfWorkPort(Protocol):
    route_authority: ModelRouteAuthorityRepositoryPort
    idempotency: AssetIdempotencyPort
    audit: AssetAuditPort

    def __enter__(self) -> ModelRouterUnitOfWorkPort: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def database_now(self) -> datetime: ...

    def commit(self) -> None: ...


ModelRouterUnitOfWorkFactory = Callable[[], ModelRouterUnitOfWorkPort]
