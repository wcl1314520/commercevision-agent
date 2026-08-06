"""Transactional Provider control-plane commands."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from commercevision_domain import (
    ConcurrencyError,
    ModelRoutePolicyHead,
    ModelRoutePolicyVersion,
    NotFoundError,
    ProviderDiscoveryCandidate,
    ProviderEndpointCapabilityHead,
    ProviderEndpointCapabilityVersion,
    ProviderEndpointObservation,
    ProviderIdentity,
    UniqueConstraintError,
    validate_workspace_id,
)
from commercevision_domain.workflow.errors import IdempotencyConflictError

from commercevision_application.asset_ports import IdempotencyRecordPort

from .asset_idempotency import canonical_hash, key_hash, workspace_hash
from .provider_control_plane_ports import (
    ProviderControlPlaneUnitOfWorkFactory,
    ProviderControlPlaneUnitOfWorkPort,
)

_IDEMPOTENCY_TTL = timedelta(days=30)
_AUDIT_TTL = timedelta(days=3650)


@dataclass(frozen=True, slots=True)
class ProviderIdentityResult:
    provider_id: str
    display_name: str
    enabled: bool
    version: int
    replayed: bool


@dataclass(frozen=True, slots=True)
class ProviderCapabilityPointerResult:
    provider_id: str
    endpoint_id: str
    current_version_id: str
    current_version_number: int
    latest_version_number: int
    head_version: int
    capability_sha256: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class ModelRoutePolicyPointerResult:
    workspace_id: str
    policy_key: str
    current_version_id: str
    current_version_number: int
    latest_version_number: int
    head_version: int
    policy_sha256: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class ProviderDiscoveryCandidateResult:
    candidate_id: str
    provider_id: str
    endpoint_id: str
    discovered_model_id: str
    discovery_sha256: str
    state: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class ProviderEndpointObservationResult:
    observation_id: str
    endpoint_capability_version_id: str
    circuit_state: str
    remaining_quota_units: int
    observed_at: str
    replayed: bool


class ProviderControlPlaneApplicationService:
    def __init__(self, uow_factory: ProviderControlPlaneUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def register_provider(
        self,
        *,
        workspace_id: str,
        provider_id: str,
        display_name: str,
        actor_id: str,
        idempotency_key: str,
        trace_id: str,
    ) -> ProviderIdentityResult:
        validate_workspace_id(workspace_id)
        scope = f"provider:register:{workspace_hash(workspace_id)}:{provider_id}"
        request_hash = canonical_hash(
            {"provider_id": provider_id, "display_name": display_name, "actor_id": actor_id}
        )
        with self._uow_factory() as uow:
            now = uow.database_now()
            replay = self._claim(
                uow=uow,
                scope=scope,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                now=now,
            )
            if replay is not None:
                return _identity_result_from_data(replay.response_data, replayed=True)
            if uow.control_plane.get_provider(provider_id, for_update=True) is not None:
                raise UniqueConstraintError(f"Provider {provider_id} already exists")
            provider = ProviderIdentity.create(
                provider_id=provider_id,
                display_name=display_name,
                actor_id=actor_id,
                now=now,
            )
            uow.control_plane.add_provider(provider)
            result = _identity_result(provider, replayed=False)
            self._complete(
                uow=uow,
                scope=scope,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                resource_type="provider",
                resource_id=provider.id,
                response_data=asdict(result),
            )
            self._audit(
                uow=uow,
                workspace_id=workspace_id,
                actor_id=actor_id,
                action="provider.registered",
                resource_type="provider",
                resource_id=provider.id,
                trace_id=trace_id,
                metadata={"display_name": provider.display_name, "enabled": provider.enabled},
                now=now,
            )
            uow.commit()
            return result

    def publish_capability(
        self,
        *,
        workspace_id: str,
        capability: ProviderEndpointCapabilityVersion,
        expected_head_version: int,
        actor_id: str,
        idempotency_key: str,
        trace_id: str,
    ) -> ProviderCapabilityPointerResult:
        validate_workspace_id(workspace_id)
        scope = _capability_scope(workspace_id, capability.provider_id, capability.endpoint_id)
        request_hash = canonical_hash(
            {
                "capability_sha256": capability.capability_sha256,
                "expected_head_version": expected_head_version,
                "actor_id": actor_id,
            }
        )
        with self._uow_factory() as uow:
            now = uow.database_now()
            replay = self._claim(
                uow=uow,
                scope=scope,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                now=now,
            )
            if replay is not None:
                return _pointer_result_from_data(replay.response_data, replayed=True)
            provider = uow.control_plane.get_provider(capability.provider_id, for_update=True)
            if provider is None:
                raise NotFoundError(f"Provider {capability.provider_id} was not found")
            if not provider.enabled:
                raise ConcurrencyError("disabled Provider cannot publish a capability")
            head = uow.control_plane.get_or_create_capability_head_for_update(
                provider_id=capability.provider_id,
                endpoint_id=capability.endpoint_id,
                now=now,
            )
            head.publish(
                capability,
                expected_version=expected_head_version,
                actor_id=actor_id,
                now=now,
            )
            uow.control_plane.add_capability_version(capability, actor_id=actor_id)
            uow.control_plane.save_capability_head(head, expected_version=expected_head_version)
            result = _pointer_result(head, capability, replayed=False)
            self._complete_pointer(
                uow=uow,
                scope=scope,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                result=result,
            )
            self._audit_pointer(
                uow=uow,
                workspace_id=workspace_id,
                actor_id=actor_id,
                action="provider-capability.published",
                result=result,
                trace_id=trace_id,
                now=now,
            )
            uow.commit()
            return result

    def rollback_capability(
        self,
        *,
        workspace_id: str,
        provider_id: str,
        endpoint_id: str,
        target_version_id: str,
        expected_head_version: int,
        actor_id: str,
        idempotency_key: str,
        trace_id: str,
    ) -> ProviderCapabilityPointerResult:
        validate_workspace_id(workspace_id)
        scope = _capability_scope(workspace_id, provider_id, endpoint_id)
        request_hash = canonical_hash(
            {
                "target_version_id": target_version_id,
                "expected_head_version": expected_head_version,
                "actor_id": actor_id,
            }
        )
        with self._uow_factory() as uow:
            now = uow.database_now()
            replay = self._claim(
                uow=uow,
                scope=scope,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                now=now,
            )
            if replay is not None:
                return _pointer_result_from_data(replay.response_data, replayed=True)
            head = uow.control_plane.get_or_create_capability_head_for_update(
                provider_id=provider_id,
                endpoint_id=endpoint_id,
                now=now,
            )
            target = uow.control_plane.get_capability_version(
                provider_id=provider_id,
                endpoint_id=endpoint_id,
                capability_version_id=target_version_id,
            )
            if target is None:
                raise NotFoundError(
                    f"Provider capability version {target_version_id} was not found"
                )
            head.rollback(
                target=target,
                expected_version=expected_head_version,
                actor_id=actor_id,
                now=now,
            )
            uow.control_plane.save_capability_head(head, expected_version=expected_head_version)
            result = _pointer_result(head, target, replayed=False)
            self._complete_pointer(
                uow=uow,
                scope=scope,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                result=result,
            )
            self._audit_pointer(
                uow=uow,
                workspace_id=workspace_id,
                actor_id=actor_id,
                action="provider-capability.rolled-back",
                result=result,
                trace_id=trace_id,
                now=now,
            )
            uow.commit()
            return result

    def publish_route_policy(
        self,
        *,
        workspace_id: str,
        policy_version: ModelRoutePolicyVersion,
        expected_head_version: int,
        actor_id: str,
        idempotency_key: str,
        trace_id: str,
    ) -> ModelRoutePolicyPointerResult:
        validate_workspace_id(workspace_id)
        if policy_version.workspace_id != workspace_id:
            raise ValueError("Route policy belongs to another workspace")
        scope = _route_policy_scope(workspace_id, policy_version.policy_key)
        request_hash = canonical_hash(
            {
                "policy_sha256": policy_version.policy_sha256,
                "expected_head_version": expected_head_version,
                "actor_id": actor_id,
            }
        )
        with self._uow_factory() as uow:
            now = uow.database_now()
            replay = self._claim(
                uow=uow,
                scope=scope,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                now=now,
            )
            if replay is not None:
                return _route_policy_result_from_data(replay.response_data, replayed=True)
            head = uow.control_plane.get_or_create_route_policy_head_for_update(
                workspace_id=workspace_id,
                policy_key=policy_version.policy_key,
                now=now,
            )
            head.publish(
                policy_version,
                expected_version=expected_head_version,
                actor_id=actor_id,
                now=now,
            )
            uow.control_plane.add_route_policy_version(policy_version)
            uow.control_plane.save_route_policy_head(
                head,
                expected_version=expected_head_version,
            )
            result = _route_policy_result(head, policy_version, replayed=False)
            self._complete_route_policy(
                uow=uow,
                scope=scope,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                result=result,
            )
            self._audit_route_policy(
                uow=uow,
                actor_id=actor_id,
                action="model-route-policy.published",
                result=result,
                trace_id=trace_id,
                now=now,
            )
            uow.commit()
            return result

    def rollback_route_policy(
        self,
        *,
        workspace_id: str,
        policy_key: str,
        target_version_id: str,
        expected_head_version: int,
        actor_id: str,
        idempotency_key: str,
        trace_id: str,
    ) -> ModelRoutePolicyPointerResult:
        validate_workspace_id(workspace_id)
        scope = _route_policy_scope(workspace_id, policy_key)
        request_hash = canonical_hash(
            {
                "target_version_id": target_version_id,
                "expected_head_version": expected_head_version,
                "actor_id": actor_id,
            }
        )
        with self._uow_factory() as uow:
            now = uow.database_now()
            replay = self._claim(
                uow=uow,
                scope=scope,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                now=now,
            )
            if replay is not None:
                return _route_policy_result_from_data(replay.response_data, replayed=True)
            head = uow.control_plane.get_or_create_route_policy_head_for_update(
                workspace_id=workspace_id,
                policy_key=policy_key,
                now=now,
            )
            target = uow.control_plane.get_route_policy_version(
                workspace_id=workspace_id,
                policy_key=policy_key,
                policy_version_id=target_version_id,
            )
            if target is None:
                raise NotFoundError(f"Route policy version {target_version_id} was not found")
            head.rollback(
                target=target,
                expected_version=expected_head_version,
                actor_id=actor_id,
                now=now,
            )
            uow.control_plane.save_route_policy_head(
                head,
                expected_version=expected_head_version,
            )
            result = _route_policy_result(head, target, replayed=False)
            self._complete_route_policy(
                uow=uow,
                scope=scope,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                result=result,
            )
            self._audit_route_policy(
                uow=uow,
                actor_id=actor_id,
                action="model-route-policy.rolled-back",
                result=result,
                trace_id=trace_id,
                now=now,
            )
            uow.commit()
            return result

    def record_discovery_candidate(
        self,
        *,
        candidate: ProviderDiscoveryCandidate,
        idempotency_key: str,
        trace_id: str,
    ) -> ProviderDiscoveryCandidateResult:
        scope = _discovery_scope(
            candidate.workspace_id,
            candidate.provider_id,
            candidate.endpoint_id,
        )
        request_hash = canonical_hash(
            {
                "candidate_id": candidate.id,
                "discovery_sha256": candidate.discovery_sha256,
                "discovered_by": candidate.discovered_by,
            }
        )
        with self._uow_factory() as uow:
            now = uow.database_now()
            replay = self._claim(
                uow=uow,
                scope=scope,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                now=now,
            )
            if replay is not None:
                return _discovery_result_from_data(replay.response_data, replayed=True)
            if candidate.discovered_at > now:
                raise ValueError("Provider discovery candidate cannot come from the future")
            if uow.control_plane.get_provider(candidate.provider_id) is None:
                raise NotFoundError(f"Provider {candidate.provider_id} was not found")
            uow.control_plane.add_discovery_candidate(candidate)
            result = _discovery_result(candidate, replayed=False)
            self._complete(
                uow=uow,
                scope=scope,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                resource_type="provider-discovery-candidate",
                resource_id=candidate.id,
                response_data=asdict(result),
            )
            self._audit(
                uow=uow,
                workspace_id=candidate.workspace_id,
                actor_id=candidate.discovered_by,
                action="provider-discovery.candidate-recorded",
                resource_type="provider-discovery-candidate",
                resource_id=candidate.id,
                trace_id=trace_id,
                metadata={
                    "provider_id": candidate.provider_id,
                    "endpoint_id": candidate.endpoint_id,
                    "discovered_model_id": candidate.discovered_model_id,
                    "discovery_sha256": candidate.discovery_sha256,
                    "state": candidate.state.value,
                },
                now=now,
            )
            uow.commit()
            return result

    def record_endpoint_observation(
        self,
        *,
        observation: ProviderEndpointObservation,
        idempotency_key: str,
        trace_id: str,
    ) -> ProviderEndpointObservationResult:
        if observation.idempotency_key_sha256 != key_hash(idempotency_key):
            raise ValueError("Provider observation idempotency hash does not match its key")
        value = observation.observation
        scope = _observation_scope(
            observation.workspace_id,
            value.endpoint_capability_version_id,
        )
        request_hash = canonical_hash(
            {
                "observation_id": observation.id,
                "endpoint_capability_version_id": value.endpoint_capability_version_id,
                "quality_score": str(value.quality_score),
                "availability_score": str(value.availability_score),
                "latency_score": str(value.latency_score),
                "quota_score": str(value.quota_score),
                "circuit_state": value.circuit_state.value,
                "remaining_quota_units": value.remaining_quota_units,
                "observed_at": value.observed_at.isoformat(),
                "observation_source": observation.observation_source,
                "created_by": observation.created_by,
            }
        )
        with self._uow_factory() as uow:
            now = uow.database_now()
            replay = self._claim(
                uow=uow,
                scope=scope,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                now=now,
            )
            if replay is not None:
                return _observation_result_from_data(replay.response_data, replayed=True)
            if observation.created_at > now:
                raise ValueError("Provider endpoint observation cannot come from the future")
            if not uow.control_plane.has_capability_version(value.endpoint_capability_version_id):
                raise NotFoundError(
                    f"Provider capability version {value.endpoint_capability_version_id} "
                    "was not found"
                )
            uow.control_plane.add_endpoint_observation(observation)
            result = _observation_result(observation, replayed=False)
            self._complete(
                uow=uow,
                scope=scope,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                resource_type="provider-endpoint-observation",
                resource_id=observation.id,
                response_data=asdict(result),
            )
            self._audit(
                uow=uow,
                workspace_id=observation.workspace_id,
                actor_id=observation.created_by,
                action="provider-endpoint.observation-recorded",
                resource_type="provider-endpoint-observation",
                resource_id=observation.id,
                trace_id=trace_id,
                metadata={
                    "endpoint_capability_version_id": value.endpoint_capability_version_id,
                    "circuit_state": value.circuit_state.value,
                    "remaining_quota_units": value.remaining_quota_units,
                    "observation_source": observation.observation_source,
                    "observed_at": value.observed_at.isoformat(),
                },
                now=now,
            )
            uow.commit()
            return result

    @staticmethod
    def _claim(
        *,
        uow: ProviderControlPlaneUnitOfWorkPort,
        scope: str,
        idempotency_key: str,
        request_hash: str,
        now: datetime,
    ) -> IdempotencyRecordPort | None:
        record = uow.idempotency.claim(
            scope=scope,
            key_hash=key_hash(idempotency_key),
            request_hash=request_hash,
            expires_at=now + _IDEMPOTENCY_TTL,
        )
        if record.request_hash != request_hash:
            raise IdempotencyConflictError(
                "idempotency key was already used with a different control-plane request"
            )
        if record.status == "COMPLETED":
            return record
        if record.status != "PENDING":
            raise ConcurrencyError("idempotency record has an unsupported status")
        return None

    @staticmethod
    def _complete(
        *,
        uow: ProviderControlPlaneUnitOfWorkPort,
        scope: str,
        idempotency_key: str,
        request_hash: str,
        resource_type: str,
        resource_id: str,
        response_data: dict[str, object],
    ) -> None:
        uow.idempotency.complete(
            scope=scope,
            key_hash=key_hash(idempotency_key),
            request_hash=request_hash,
            resource_type=resource_type,
            resource_id=resource_id,
            response_data=response_data,
        )

    @classmethod
    def _complete_pointer(
        cls,
        *,
        uow: ProviderControlPlaneUnitOfWorkPort,
        scope: str,
        idempotency_key: str,
        request_hash: str,
        result: ProviderCapabilityPointerResult,
    ) -> None:
        cls._complete(
            uow=uow,
            scope=scope,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            resource_type="provider-capability-head",
            resource_id=canonical_hash(
                {"provider_id": result.provider_id, "endpoint_id": result.endpoint_id}
            ),
            response_data=asdict(result),
        )

    @classmethod
    def _complete_route_policy(
        cls,
        *,
        uow: ProviderControlPlaneUnitOfWorkPort,
        scope: str,
        idempotency_key: str,
        request_hash: str,
        result: ModelRoutePolicyPointerResult,
    ) -> None:
        cls._complete(
            uow=uow,
            scope=scope,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            resource_type="model-route-policy-head",
            resource_id=canonical_hash(
                {"workspace_id": result.workspace_id, "policy_key": result.policy_key}
            ),
            response_data=asdict(result),
        )

    @staticmethod
    def _audit(
        *,
        uow: ProviderControlPlaneUnitOfWorkPort,
        workspace_id: str,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        trace_id: str,
        metadata: dict[str, object],
        now: datetime,
    ) -> None:
        uow.audit.add(
            workspace_id=workspace_id,
            actor_type="USER",
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            trace_id=trace_id,
            metadata=metadata,
            created_at=now,
            expires_at=now + _AUDIT_TTL,
        )

    @classmethod
    def _audit_pointer(
        cls,
        *,
        uow: ProviderControlPlaneUnitOfWorkPort,
        workspace_id: str,
        actor_id: str,
        action: str,
        result: ProviderCapabilityPointerResult,
        trace_id: str,
        now: datetime,
    ) -> None:
        cls._audit(
            uow=uow,
            workspace_id=workspace_id,
            actor_id=actor_id,
            action=action,
            resource_type="provider-capability-head",
            resource_id=canonical_hash(
                {"provider_id": result.provider_id, "endpoint_id": result.endpoint_id}
            ),
            trace_id=trace_id,
            metadata={
                "provider_id": result.provider_id,
                "endpoint_id": result.endpoint_id,
                "current_version_id": result.current_version_id,
                "current_version_number": result.current_version_number,
                "latest_version_number": result.latest_version_number,
                "head_version": result.head_version,
                "capability_sha256": result.capability_sha256,
            },
            now=now,
        )

    @classmethod
    def _audit_route_policy(
        cls,
        *,
        uow: ProviderControlPlaneUnitOfWorkPort,
        actor_id: str,
        action: str,
        result: ModelRoutePolicyPointerResult,
        trace_id: str,
        now: datetime,
    ) -> None:
        cls._audit(
            uow=uow,
            workspace_id=result.workspace_id,
            actor_id=actor_id,
            action=action,
            resource_type="model-route-policy-head",
            resource_id=canonical_hash(
                {"workspace_id": result.workspace_id, "policy_key": result.policy_key}
            ),
            trace_id=trace_id,
            metadata={
                "policy_key": result.policy_key,
                "current_version_id": result.current_version_id,
                "current_version_number": result.current_version_number,
                "latest_version_number": result.latest_version_number,
                "head_version": result.head_version,
                "policy_sha256": result.policy_sha256,
            },
            now=now,
        )


def _capability_scope(workspace_id: str, provider_id: str, endpoint_id: str) -> str:
    identity = canonical_hash({"provider_id": provider_id, "endpoint_id": endpoint_id})
    return f"provider-capability:{workspace_hash(workspace_id)}:{identity}"


def _route_policy_scope(workspace_id: str, policy_key: str) -> str:
    identity = canonical_hash({"workspace_id": workspace_id, "policy_key": policy_key})
    return f"model-route-policy:{workspace_hash(workspace_id)}:{identity}"


def _discovery_scope(workspace_id: str, provider_id: str, endpoint_id: str) -> str:
    identity = canonical_hash({"provider_id": provider_id, "endpoint_id": endpoint_id})
    return f"provider-discovery:{workspace_hash(workspace_id)}:{identity}"


def _observation_scope(workspace_id: str, capability_version_id: str) -> str:
    return f"provider-observation:{workspace_hash(workspace_id)}:{capability_version_id}"


def _identity_result(provider: ProviderIdentity, *, replayed: bool) -> ProviderIdentityResult:
    return ProviderIdentityResult(
        provider_id=provider.id,
        display_name=provider.display_name,
        enabled=provider.enabled,
        version=provider.version,
        replayed=replayed,
    )


def _identity_result_from_data(
    data: dict[str, object] | None,
    *,
    replayed: bool,
) -> ProviderIdentityResult:
    if not isinstance(data, dict):
        raise ConcurrencyError("idempotency record lacks a Provider response")
    try:
        return ProviderIdentityResult(
            provider_id=_require_str(data["provider_id"]),
            display_name=_require_str(data["display_name"]),
            enabled=_require_bool(data["enabled"]),
            version=_require_int(data["version"]),
            replayed=replayed,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConcurrencyError("idempotency Provider response is invalid") from exc


def _pointer_result(
    head: ProviderEndpointCapabilityHead,
    capability: ProviderEndpointCapabilityVersion,
    *,
    replayed: bool,
) -> ProviderCapabilityPointerResult:
    if head.current_version_id is None:
        raise ConcurrencyError("Provider capability head has no current version")
    return ProviderCapabilityPointerResult(
        provider_id=head.provider_id,
        endpoint_id=head.endpoint_id,
        current_version_id=head.current_version_id,
        current_version_number=head.current_version_number,
        latest_version_number=head.latest_version_number,
        head_version=head.version,
        capability_sha256=capability.capability_sha256,
        replayed=replayed,
    )


def _pointer_result_from_data(
    data: dict[str, object] | None,
    *,
    replayed: bool,
) -> ProviderCapabilityPointerResult:
    if not isinstance(data, dict):
        raise ConcurrencyError("idempotency record lacks a capability pointer response")
    try:
        return ProviderCapabilityPointerResult(
            provider_id=_require_str(data["provider_id"]),
            endpoint_id=_require_str(data["endpoint_id"]),
            current_version_id=_require_str(data["current_version_id"]),
            current_version_number=_require_int(data["current_version_number"]),
            latest_version_number=_require_int(data["latest_version_number"]),
            head_version=_require_int(data["head_version"]),
            capability_sha256=_require_str(data["capability_sha256"]),
            replayed=replayed,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConcurrencyError("idempotency capability pointer response is invalid") from exc


def _route_policy_result(
    head: ModelRoutePolicyHead,
    policy_version: ModelRoutePolicyVersion,
    *,
    replayed: bool,
) -> ModelRoutePolicyPointerResult:
    if head.current_version_id is None:
        raise ConcurrencyError("Route policy head has no current version")
    return ModelRoutePolicyPointerResult(
        workspace_id=head.workspace_id,
        policy_key=head.policy_key,
        current_version_id=head.current_version_id,
        current_version_number=head.current_version_number,
        latest_version_number=head.latest_version_number,
        head_version=head.version,
        policy_sha256=policy_version.policy_sha256,
        replayed=replayed,
    )


def _route_policy_result_from_data(
    data: dict[str, object] | None,
    *,
    replayed: bool,
) -> ModelRoutePolicyPointerResult:
    if not isinstance(data, dict):
        raise ConcurrencyError("idempotency record lacks a Route policy response")
    try:
        return ModelRoutePolicyPointerResult(
            workspace_id=_require_str(data["workspace_id"]),
            policy_key=_require_str(data["policy_key"]),
            current_version_id=_require_str(data["current_version_id"]),
            current_version_number=_require_int(data["current_version_number"]),
            latest_version_number=_require_int(data["latest_version_number"]),
            head_version=_require_int(data["head_version"]),
            policy_sha256=_require_str(data["policy_sha256"]),
            replayed=replayed,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConcurrencyError("idempotency Route policy response is invalid") from exc


def _discovery_result(
    candidate: ProviderDiscoveryCandidate,
    *,
    replayed: bool,
) -> ProviderDiscoveryCandidateResult:
    return ProviderDiscoveryCandidateResult(
        candidate_id=candidate.id,
        provider_id=candidate.provider_id,
        endpoint_id=candidate.endpoint_id,
        discovered_model_id=candidate.discovered_model_id,
        discovery_sha256=candidate.discovery_sha256,
        state=candidate.state.value,
        replayed=replayed,
    )


def _discovery_result_from_data(
    data: dict[str, object] | None,
    *,
    replayed: bool,
) -> ProviderDiscoveryCandidateResult:
    if not isinstance(data, dict):
        raise ConcurrencyError("idempotency record lacks a Provider discovery response")
    try:
        return ProviderDiscoveryCandidateResult(
            candidate_id=_require_str(data["candidate_id"]),
            provider_id=_require_str(data["provider_id"]),
            endpoint_id=_require_str(data["endpoint_id"]),
            discovered_model_id=_require_str(data["discovered_model_id"]),
            discovery_sha256=_require_str(data["discovery_sha256"]),
            state=_require_str(data["state"]),
            replayed=replayed,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConcurrencyError("idempotency Provider discovery response is invalid") from exc


def _observation_result(
    record: ProviderEndpointObservation,
    *,
    replayed: bool,
) -> ProviderEndpointObservationResult:
    observation = record.observation
    return ProviderEndpointObservationResult(
        observation_id=record.id,
        endpoint_capability_version_id=observation.endpoint_capability_version_id,
        circuit_state=observation.circuit_state.value,
        remaining_quota_units=observation.remaining_quota_units,
        observed_at=observation.observed_at.isoformat(),
        replayed=replayed,
    )


def _observation_result_from_data(
    data: dict[str, object] | None,
    *,
    replayed: bool,
) -> ProviderEndpointObservationResult:
    if not isinstance(data, dict):
        raise ConcurrencyError("idempotency record lacks a Provider observation response")
    try:
        return ProviderEndpointObservationResult(
            observation_id=_require_str(data["observation_id"]),
            endpoint_capability_version_id=_require_str(data["endpoint_capability_version_id"]),
            circuit_state=_require_str(data["circuit_state"]),
            remaining_quota_units=_require_int(data["remaining_quota_units"]),
            observed_at=_require_str(data["observed_at"]),
            replayed=replayed,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConcurrencyError("idempotency Provider observation response is invalid") from exc


def _require_str(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("expected a string")
    return value


def _require_int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError("expected an integer")
    return value


def _require_bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise TypeError("expected a boolean")
    return value
