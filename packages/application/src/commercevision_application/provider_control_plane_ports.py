"""Typed persistence seams for Provider control-plane commands."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from types import TracebackType
from typing import Any, Protocol

from commercevision_domain import (
    ModelRoutePolicyHead,
    ModelRoutePolicyVersion,
    ProviderDiscoveryCandidate,
    ProviderEndpointCapabilityHead,
    ProviderEndpointCapabilityVersion,
    ProviderEndpointObservation,
    ProviderIdentity,
)

from commercevision_application.asset_ports import IdempotencyRecordPort


class ProviderControlPlaneRepositoryPort(Protocol):
    def get_provider(
        self, provider_id: str, *, for_update: bool = False
    ) -> ProviderIdentity | None: ...

    def add_provider(self, provider: ProviderIdentity) -> None: ...

    def get_or_create_capability_head_for_update(
        self,
        *,
        provider_id: str,
        endpoint_id: str,
        now: datetime,
    ) -> ProviderEndpointCapabilityHead: ...

    def get_capability_version(
        self,
        *,
        provider_id: str,
        endpoint_id: str,
        capability_version_id: str,
    ) -> ProviderEndpointCapabilityVersion | None: ...

    def add_capability_version(
        self,
        capability: ProviderEndpointCapabilityVersion,
        *,
        actor_id: str,
    ) -> None: ...

    def save_capability_head(
        self,
        head: ProviderEndpointCapabilityHead,
        *,
        expected_version: int,
    ) -> None: ...

    def get_or_create_route_policy_head_for_update(
        self,
        *,
        workspace_id: str,
        policy_key: str,
        now: datetime,
    ) -> ModelRoutePolicyHead: ...

    def get_route_policy_version(
        self,
        *,
        workspace_id: str,
        policy_key: str,
        policy_version_id: str,
    ) -> ModelRoutePolicyVersion | None: ...

    def add_route_policy_version(self, policy_version: ModelRoutePolicyVersion) -> None: ...

    def save_route_policy_head(
        self,
        head: ModelRoutePolicyHead,
        *,
        expected_version: int,
    ) -> None: ...

    def add_discovery_candidate(self, candidate: ProviderDiscoveryCandidate) -> None: ...

    def has_capability_version(self, capability_version_id: str) -> bool: ...

    def add_endpoint_observation(self, observation: ProviderEndpointObservation) -> None: ...


class ProviderControlPlaneIdempotencyPort(Protocol):
    def claim(
        self,
        *,
        scope: str,
        key_hash: str,
        request_hash: str,
        expires_at: datetime,
    ) -> IdempotencyRecordPort: ...

    def complete(
        self,
        *,
        scope: str,
        key_hash: str,
        request_hash: str,
        resource_type: str,
        resource_id: str,
        response_data: dict[str, Any],
    ) -> None: ...


class ProviderControlPlaneAuditPort(Protocol):
    def add(self, **kwargs: Any) -> None: ...


class ProviderControlPlaneUnitOfWorkPort(Protocol):
    control_plane: ProviderControlPlaneRepositoryPort
    idempotency: ProviderControlPlaneIdempotencyPort
    audit: ProviderControlPlaneAuditPort

    def __enter__(self) -> ProviderControlPlaneUnitOfWorkPort: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def database_now(self) -> datetime: ...

    def commit(self) -> None: ...


ProviderControlPlaneUnitOfWorkFactory = Callable[[], ProviderControlPlaneUnitOfWorkPort]
