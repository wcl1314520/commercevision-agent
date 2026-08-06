"""Provider control-plane pointer aggregate over immutable capability versions."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from commercevision_domain.ids import canonicalize_uuid
from commercevision_domain.provider_routing import (
    EndpointRouteObservation,
    ModelRoutePolicy,
    ProviderEndpointCapabilityVersion,
)
from commercevision_domain.workflow.errors import ConcurrencyError
from commercevision_domain.workspace_identity import validate_workspace_id

_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_SCORE_QUANTUM = Decimal("0.000001")
_FORBIDDEN_DISCOVERY_KEYS = frozenset(
    {"api_key", "apikey", "authorization", "credential", "password", "secret", "token"}
)


def _validate_token(value: str, field_name: str) -> str:
    if not isinstance(value, str) or _TOKEN_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} is invalid")
    return value


def _canonical_score(value: Decimal) -> str:
    return format(value.quantize(_SCORE_QUANTUM), "f")


def _validate_utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must be timezone-aware UTC")
    return value


@dataclass(slots=True)
class ProviderIdentity:
    id: str
    display_name: str
    enabled: bool
    version: int
    created_by: str
    created_at: datetime
    updated_by: str
    updated_at: datetime

    def __post_init__(self) -> None:
        _validate_token(self.id, "Provider id")
        if (
            not isinstance(self.display_name, str)
            or not self.display_name.strip()
            or len(self.display_name) > 160
        ):
            raise ValueError("Provider display name is invalid")
        if not isinstance(self.enabled, bool):
            raise ValueError("Provider enabled state must be boolean")
        if not isinstance(self.version, int) or isinstance(self.version, bool) or self.version < 1:
            raise ValueError("Provider identity version must be positive")
        _validate_token(self.created_by, "Provider creator")
        _validate_token(self.updated_by, "Provider updater")
        _validate_utc(self.created_at, "Provider created_at")
        _validate_utc(self.updated_at, "Provider updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("Provider update time cannot precede creation")

    @classmethod
    def create(
        cls,
        *,
        provider_id: str,
        display_name: str,
        actor_id: str,
        now: datetime,
    ) -> ProviderIdentity:
        return cls(
            id=provider_id,
            display_name=display_name.strip(),
            enabled=True,
            version=1,
            created_by=actor_id,
            created_at=now,
            updated_by=actor_id,
            updated_at=now,
        )

    def set_enabled(
        self,
        enabled: bool,
        *,
        expected_version: int,
        actor_id: str,
        now: datetime,
    ) -> None:
        if expected_version != self.version:
            raise ConcurrencyError("Provider identity version changed")
        if not isinstance(enabled, bool):
            raise ValueError("Provider enabled state must be boolean")
        _validate_token(actor_id, "Provider updater")
        _validate_utc(now, "Provider update time")
        if now < self.updated_at:
            raise ValueError("Provider update time cannot move backwards")
        if enabled == self.enabled:
            raise ValueError("Provider enabled state is unchanged")
        self.enabled = enabled
        self.version += 1
        self.updated_by = actor_id
        self.updated_at = now


@dataclass(slots=True)
class ProviderEndpointCapabilityHead:
    provider_id: str
    endpoint_id: str
    current_version_id: str | None
    current_version_number: int
    latest_version_number: int
    version: int
    updated_by: str | None
    updated_at: datetime

    def __post_init__(self) -> None:
        _validate_token(self.provider_id, "Provider id")
        _validate_token(self.endpoint_id, "Provider endpoint id")
        _validate_utc(self.updated_at, "Provider capability head updated_at")
        if min(self.current_version_number, self.latest_version_number, self.version) < 0:
            raise ValueError("Provider capability head counters cannot be negative")
        if self.current_version_number > self.latest_version_number:
            raise ValueError("current capability version cannot exceed latest version")
        if self.current_version_id is None:
            if self.current_version_number != 0:
                raise ValueError("empty capability head cannot have a current version number")
        elif self.current_version_number == 0:
            raise ValueError("current capability identity requires a version number")
        if self.updated_by is not None:
            _validate_token(self.updated_by, "Provider capability head actor")

    @classmethod
    def create(
        cls,
        *,
        provider_id: str,
        endpoint_id: str,
        now: datetime,
    ) -> ProviderEndpointCapabilityHead:
        return cls(
            provider_id=provider_id,
            endpoint_id=endpoint_id,
            current_version_id=None,
            current_version_number=0,
            latest_version_number=0,
            version=0,
            updated_by=None,
            updated_at=now,
        )

    def publish(
        self,
        capability: ProviderEndpointCapabilityVersion,
        *,
        expected_version: int,
        actor_id: str,
        now: datetime,
    ) -> None:
        self._assert_expected_version(expected_version)
        self._validate_command(actor_id=actor_id, now=now)
        self._assert_same_endpoint(capability)
        expected_capability_version = self.latest_version_number + 1
        if capability.version_number != expected_capability_version:
            raise ValueError(
                "published capability version must advance the latest immutable version"
            )
        if capability.created_at > now:
            raise ValueError("published capability cannot be created in the future")
        self.current_version_id = capability.id
        self.current_version_number = capability.version_number
        self.latest_version_number = capability.version_number
        self._touch(actor_id=actor_id, now=now)

    def rollback(
        self,
        *,
        target: ProviderEndpointCapabilityVersion,
        expected_version: int,
        actor_id: str,
        now: datetime,
    ) -> None:
        self._assert_expected_version(expected_version)
        self._validate_command(actor_id=actor_id, now=now)
        self._assert_same_endpoint(target)
        if self.current_version_id is None:
            raise ValueError("empty capability head cannot roll back")
        if (
            not 1 <= target.version_number <= self.latest_version_number
            or target.id == self.current_version_id
        ):
            raise ValueError("rollback target must be a different published capability version")
        self.current_version_id = target.id
        self.current_version_number = target.version_number
        self._touch(actor_id=actor_id, now=now)

    def _assert_expected_version(self, expected_version: int) -> None:
        if expected_version != self.version:
            raise ConcurrencyError("capability head version changed")

    def _validate_command(self, *, actor_id: str, now: datetime) -> None:
        _validate_token(actor_id, "Provider capability actor")
        _validate_utc(now, "Provider capability command time")
        if now < self.updated_at:
            raise ValueError("Provider capability command time cannot move backwards")

    def _assert_same_endpoint(self, capability: ProviderEndpointCapabilityVersion) -> None:
        if not isinstance(capability, ProviderEndpointCapabilityVersion):
            raise ValueError("Provider capability version is invalid")
        if capability.provider_id != self.provider_id or capability.endpoint_id != self.endpoint_id:
            raise ValueError("Provider capability version belongs to another endpoint")

    def _touch(self, *, actor_id: str, now: datetime) -> None:
        self.version += 1
        self.updated_by = actor_id
        self.updated_at = now


@dataclass(frozen=True, slots=True)
class ModelRoutePolicyVersion:
    id: str
    workspace_id: str
    policy_key: str
    version_number: int
    policy: ModelRoutePolicy
    created_by: str
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", canonicalize_uuid(self.id))
        validate_workspace_id(self.workspace_id)
        _validate_token(self.policy_key, "Route policy key")
        if (
            not isinstance(self.version_number, int)
            or isinstance(self.version_number, bool)
            or self.version_number < 1
        ):
            raise ValueError("Route policy version number must be positive")
        if not isinstance(self.policy, ModelRoutePolicy):
            raise ValueError("Route policy is invalid")
        _validate_token(self.created_by, "Route policy creator")
        _validate_utc(self.created_at, "Route policy created_at")

    @classmethod
    def create(
        cls,
        *,
        id: str,
        workspace_id: str,
        policy_key: str,
        version_number: int,
        policy: ModelRoutePolicy,
        actor_id: str,
        now: datetime,
    ) -> ModelRoutePolicyVersion:
        return cls(
            id=id,
            workspace_id=workspace_id,
            policy_key=policy_key,
            version_number=version_number,
            policy=policy,
            created_by=actor_id,
            created_at=now,
        )

    def to_canonical_data(self) -> dict[str, object]:
        return {
            "schema_version": "model-route-policy.v1",
            "id": self.id,
            "workspace_id": self.workspace_id,
            "policy_key": self.policy_key,
            "version_number": self.version_number,
            "policy_version": self.policy.version,
            "quality_weight": _canonical_score(self.policy.quality_weight),
            "availability_weight": _canonical_score(self.policy.availability_weight),
            "latency_weight": _canonical_score(self.policy.latency_weight),
            "quota_weight": _canonical_score(self.policy.quota_weight),
            "price_weight": _canonical_score(self.policy.price_weight),
            "maximum_observation_age_seconds": self.policy.maximum_observation_age_seconds,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat().replace("+00:00", "Z"),
        }

    @property
    def policy_sha256(self) -> str:
        payload = json.dumps(
            self.to_canonical_data(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


@dataclass(slots=True)
class ModelRoutePolicyHead:
    workspace_id: str
    policy_key: str
    current_version_id: str | None
    current_version_number: int
    latest_version_number: int
    version: int
    updated_by: str | None
    updated_at: datetime

    def __post_init__(self) -> None:
        validate_workspace_id(self.workspace_id)
        _validate_token(self.policy_key, "Route policy key")
        _validate_utc(self.updated_at, "Route policy head updated_at")
        if min(self.current_version_number, self.latest_version_number, self.version) < 0:
            raise ValueError("Route policy head counters cannot be negative")
        if self.current_version_number > self.latest_version_number:
            raise ValueError("current Route policy version cannot exceed latest version")
        if self.current_version_id is None:
            if self.current_version_number != 0:
                raise ValueError("empty Route policy head cannot have a current version number")
        else:
            self.current_version_id = canonicalize_uuid(self.current_version_id)
            if self.current_version_number == 0:
                raise ValueError("current Route policy identity requires a version number")
        if self.updated_by is not None:
            _validate_token(self.updated_by, "Route policy head actor")

    @classmethod
    def create(
        cls,
        *,
        workspace_id: str,
        policy_key: str,
        now: datetime,
    ) -> ModelRoutePolicyHead:
        return cls(
            workspace_id=workspace_id,
            policy_key=policy_key,
            current_version_id=None,
            current_version_number=0,
            latest_version_number=0,
            version=0,
            updated_by=None,
            updated_at=now,
        )

    def publish(
        self,
        policy_version: ModelRoutePolicyVersion,
        *,
        expected_version: int,
        actor_id: str,
        now: datetime,
    ) -> None:
        self._assert_expected_version(expected_version)
        self._validate_command(actor_id=actor_id, now=now)
        self._assert_same_policy(policy_version)
        if policy_version.version_number != self.latest_version_number + 1:
            raise ValueError("published Route policy must advance the latest immutable version")
        if policy_version.created_at > now:
            raise ValueError("published Route policy cannot be created in the future")
        self.current_version_id = policy_version.id
        self.current_version_number = policy_version.version_number
        self.latest_version_number = policy_version.version_number
        self._touch(actor_id=actor_id, now=now)

    def rollback(
        self,
        *,
        target: ModelRoutePolicyVersion,
        expected_version: int,
        actor_id: str,
        now: datetime,
    ) -> None:
        self._assert_expected_version(expected_version)
        self._validate_command(actor_id=actor_id, now=now)
        self._assert_same_policy(target)
        if self.current_version_id is None:
            raise ValueError("empty Route policy head cannot roll back")
        if (
            not 1 <= target.version_number <= self.latest_version_number
            or target.id == self.current_version_id
        ):
            raise ValueError("rollback target must be a different published Route policy version")
        self.current_version_id = target.id
        self.current_version_number = target.version_number
        self._touch(actor_id=actor_id, now=now)

    def _assert_expected_version(self, expected_version: int) -> None:
        if expected_version != self.version:
            raise ConcurrencyError("Route policy head version changed")

    def _validate_command(self, *, actor_id: str, now: datetime) -> None:
        _validate_token(actor_id, "Route policy actor")
        _validate_utc(now, "Route policy command time")
        if now < self.updated_at:
            raise ValueError("Route policy command time cannot move backwards")

    def _assert_same_policy(self, policy_version: ModelRoutePolicyVersion) -> None:
        if not isinstance(policy_version, ModelRoutePolicyVersion):
            raise ValueError("Route policy version is invalid")
        if (
            policy_version.workspace_id != self.workspace_id
            or policy_version.policy_key != self.policy_key
        ):
            raise ValueError("Route policy version belongs to another policy head")

    def _touch(self, *, actor_id: str, now: datetime) -> None:
        self.version += 1
        self.updated_by = actor_id
        self.updated_at = now


class ProviderDiscoveryCandidateState(StrEnum):
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class ProviderDiscoveryCandidate:
    id: str
    workspace_id: str
    provider_id: str
    endpoint_id: str
    discovered_model_id: str
    discovery_json: str
    discovery_sha256: str
    state: ProviderDiscoveryCandidateState
    discovered_by: str
    discovered_at: datetime
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", canonicalize_uuid(self.id))
        validate_workspace_id(self.workspace_id)
        _validate_token(self.provider_id, "Discovery Provider id")
        _validate_token(self.endpoint_id, "Discovery endpoint id")
        _validate_token(self.discovered_model_id, "Discovered model id")
        try:
            parsed = json.loads(self.discovery_json)
        except (TypeError, json.JSONDecodeError):
            raise ValueError("Provider discovery evidence is invalid") from None
        if not isinstance(parsed, dict):
            raise ValueError("Provider discovery evidence must be an object")
        canonical = _canonical_discovery_json(parsed)
        if canonical != self.discovery_json:
            raise ValueError("Provider discovery evidence is not canonical")
        if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != self.discovery_sha256:
            raise ValueError("Provider discovery checksum is inconsistent")
        object.__setattr__(self, "state", ProviderDiscoveryCandidateState(self.state))
        _validate_token(self.discovered_by, "Provider discovery actor")
        _validate_utc(self.discovered_at, "Provider discovery time")
        if (self.reviewed_by is None) != (self.reviewed_at is None):
            raise ValueError("Provider discovery review actor and time must appear together")
        if self.state is ProviderDiscoveryCandidateState.PENDING_REVIEW:
            if self.reviewed_by is not None:
                raise ValueError("pending Provider discovery cannot have review metadata")
        elif self.reviewed_by is None or self.reviewed_at is None:
            raise ValueError("reviewed Provider discovery requires review metadata")
        if self.reviewed_by is not None:
            _validate_token(self.reviewed_by, "Provider discovery reviewer")
        if self.reviewed_at is not None:
            _validate_utc(self.reviewed_at, "Provider discovery review time")
            if self.reviewed_at < self.discovered_at:
                raise ValueError("Provider discovery review cannot precede discovery")

    @classmethod
    def create(
        cls,
        *,
        id: str,
        workspace_id: str,
        provider_id: str,
        endpoint_id: str,
        discovered_model_id: str,
        evidence: dict[str, object],
        discovered_by: str,
        now: datetime,
    ) -> ProviderDiscoveryCandidate:
        canonical = _canonical_discovery_json(evidence)
        return cls(
            id=id,
            workspace_id=workspace_id,
            provider_id=provider_id,
            endpoint_id=endpoint_id,
            discovered_model_id=discovered_model_id,
            discovery_json=canonical,
            discovery_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            state=ProviderDiscoveryCandidateState.PENDING_REVIEW,
            discovered_by=discovered_by,
            discovered_at=now,
        )


@dataclass(frozen=True, slots=True)
class ProviderEndpointObservation:
    id: str
    workspace_id: str
    observation: EndpointRouteObservation
    observation_source: str
    idempotency_key_sha256: str
    created_by: str
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", canonicalize_uuid(self.id))
        validate_workspace_id(self.workspace_id)
        if not isinstance(self.observation, EndpointRouteObservation):
            raise ValueError("Provider endpoint observation is invalid")
        _validate_token(self.observation_source, "Provider observation source")
        if len(self.observation_source) > 32:
            raise ValueError("Provider observation source exceeds 32 characters")
        if _SHA256_PATTERN.fullmatch(self.idempotency_key_sha256) is None:
            raise ValueError("Provider observation idempotency hash is invalid")
        _validate_token(self.created_by, "Provider observation actor")
        _validate_utc(self.created_at, "Provider observation created_at")
        if self.observation.observed_at > self.created_at:
            raise ValueError("Provider observation cannot be created before it was observed")

    @classmethod
    def create(
        cls,
        *,
        id: str,
        workspace_id: str,
        observation: EndpointRouteObservation,
        observation_source: str,
        idempotency_key_sha256: str,
        actor_id: str,
        now: datetime,
    ) -> ProviderEndpointObservation:
        return cls(
            id=id,
            workspace_id=workspace_id,
            observation=observation,
            observation_source=observation_source,
            idempotency_key_sha256=idempotency_key_sha256,
            created_by=actor_id,
            created_at=now,
        )


def _canonical_discovery_json(evidence: dict[str, object]) -> str:
    if not isinstance(evidence, dict) or not evidence:
        raise ValueError("Provider discovery evidence must be a non-empty object")
    _reject_credential_like_fields(evidence)
    try:
        canonical = json.dumps(
            evidence,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError):
        raise ValueError("Provider discovery evidence must be JSON-compatible") from None
    if len(canonical.encode("utf-8")) > 16_384:
        raise ValueError("Provider discovery evidence exceeds 16384 bytes")
    return canonical


def _reject_credential_like_fields(value: object) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise ValueError("Provider discovery evidence keys must be strings")
            normalized = key.lower().replace("-", "_")
            if normalized in _FORBIDDEN_DISCOVERY_KEYS or normalized.endswith("_secret"):
                raise ValueError("Provider discovery evidence contains a credential-like field")
            _reject_credential_like_fields(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_credential_like_fields(nested)
    elif isinstance(value, str) and (
        value.lower().startswith("sk-") or value.lower().startswith("bearer ")
    ):
        raise ValueError("Provider discovery evidence contains a credential-like value")
