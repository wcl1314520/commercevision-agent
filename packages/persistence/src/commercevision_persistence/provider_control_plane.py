"""MySQL adapters for Provider identity and immutable capability publication."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from types import TracebackType
from typing import Any, cast

from commercevision_domain import (
    ConcurrencyError,
    ImageRole,
    ModelRoutePolicy,
    ModelRoutePolicyHead,
    ModelRoutePolicyVersion,
    ProviderCapability,
    ProviderDataRetentionMode,
    ProviderDiscoveryCandidate,
    ProviderEndpointCapabilityHead,
    ProviderEndpointCapabilityVersion,
    ProviderEndpointObservation,
    ProviderExecutionMode,
    ProviderIdentity,
    ProviderPricingUnit,
    ProviderProtocol,
    ProviderTrainingUsePolicy,
)
from sqlalchemy import CursorResult, literal_column, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from .database import enter_unit_of_work, exit_unit_of_work
from .integrity import classify_database_error
from .provider_control_plane_models import (
    ModelRoutePolicyHeadModel,
    ModelRoutePolicyVersionModel,
    ProviderDiscoveryCandidateModel,
    ProviderEndpointCapabilityHeadModel,
    ProviderEndpointCapabilityVersionModel,
    ProviderEndpointObservationModel,
    ProviderIdentityModel,
)
from .repositories import AuditRepository, IdempotencyRepository


def _provider_from_model(model: ProviderIdentityModel) -> ProviderIdentity:
    return ProviderIdentity(
        id=model.id,
        display_name=model.display_name,
        enabled=model.enabled,
        version=model.version,
        created_by=model.created_by,
        created_at=model.created_at,
        updated_by=model.updated_by,
        updated_at=model.updated_at,
    )


def _head_from_model(model: ProviderEndpointCapabilityHeadModel) -> ProviderEndpointCapabilityHead:
    return ProviderEndpointCapabilityHead(
        provider_id=model.provider_id,
        endpoint_id=model.endpoint_id,
        current_version_id=model.current_version_id,
        current_version_number=model.current_version_number,
        latest_version_number=model.latest_version_number,
        version=model.version,
        updated_by=model.updated_by,
        updated_at=model.updated_at,
    )


def _parse_canonical_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise RuntimeError("persisted Provider capability time is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise RuntimeError("persisted Provider capability time is invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RuntimeError("persisted Provider capability time is not UTC")
    return parsed.astimezone(UTC)


def capability_from_model(
    model: ProviderEndpointCapabilityVersionModel,
) -> ProviderEndpointCapabilityVersion:
    data = model.capability_json
    if data.get("schema_version") != "provider-endpoint-capability.v1":
        raise RuntimeError("persisted Provider capability schema is unsupported")
    try:
        capability = ProviderEndpointCapabilityVersion.create(
            id=model.id,
            provider_id=model.provider_id,
            endpoint_id=model.endpoint_id,
            version_number=model.version_number,
            endpoint_host=str(data["endpoint_host"]),
            endpoint_region=str(data["endpoint_region"]),
            model_family=str(data["model_family"]),
            model_id=str(data["model_id"]),
            model_revision=str(data["model_revision"]),
            adapter_version=str(data["adapter_version"]),
            configuration_sha256=model.configuration_sha256,
            capabilities=frozenset(
                ProviderCapability(str(value)) for value in data["capabilities"]
            ),
            protocol=ProviderProtocol(str(data["protocol"])),
            execution_mode=ProviderExecutionMode(str(data["execution_mode"])),
            supports_query=data["supports_query"],
            supports_cancel=data["supports_cancel"],
            supports_provider_idempotency=data["supports_provider_idempotency"],
            allowed_categories=frozenset(str(value) for value in data["allowed_categories"]),
            allowed_image_roles=frozenset(
                ImageRole(str(value)) for value in data["allowed_image_roles"]
            ),
            output_formats=frozenset(str(value) for value in data["output_formats"]),
            minimum_width=int(data["minimum_width"]),
            maximum_width=int(data["maximum_width"]),
            minimum_height=int(data["minimum_height"]),
            maximum_height=int(data["maximum_height"]),
            maximum_candidates=int(data["maximum_candidates"]),
            safety_policy_version=str(data["safety_policy_version"]),
            data_region=str(data["data_region"]),
            data_retention_mode=ProviderDataRetentionMode(str(data["data_retention_mode"])),
            maximum_retention_days=int(data["maximum_retention_days"]),
            training_use_policy=ProviderTrainingUsePolicy(str(data["training_use_policy"])),
            secret_reference=model.secret_reference,
            maximum_reference_images=int(data["maximum_reference_images"]),
            supports_mask=data["supports_mask"],
            supports_seed=data["supports_seed"],
            supports_lora=data["supports_lora"],
            maximum_request_bytes=int(data["maximum_request_bytes"]),
            maximum_result_bytes=int(data["maximum_result_bytes"]),
            pricing_unit=ProviderPricingUnit(str(data["pricing_unit"])),
            enabled=data["enabled"],
            unit_price=Decimal(model.unit_price),
            currency=model.currency,
            created_at=_parse_canonical_datetime(data["created_at"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("persisted Provider capability is invalid") from exc
    if capability.capability_sha256 != model.capability_sha256:
        raise RuntimeError("persisted Provider capability checksum is inconsistent")
    return capability


def _route_policy_head_from_model(model: ModelRoutePolicyHeadModel) -> ModelRoutePolicyHead:
    return ModelRoutePolicyHead(
        workspace_id=model.workspace_id,
        policy_key=model.policy_key,
        current_version_id=model.current_version_id,
        current_version_number=model.current_version_number,
        latest_version_number=model.latest_version_number,
        version=model.version,
        updated_by=model.updated_by,
        updated_at=model.updated_at,
    )


def route_policy_version_from_model(
    model: ModelRoutePolicyVersionModel,
) -> ModelRoutePolicyVersion:
    data = model.policy_json
    try:
        policy_version = ModelRoutePolicyVersion(
            id=model.id,
            workspace_id=model.workspace_id,
            policy_key=model.policy_key,
            version_number=model.version_number,
            policy=ModelRoutePolicy(
                version=model.policy_version,
                quality_weight=Decimal(model.quality_weight),
                availability_weight=Decimal(model.availability_weight),
                latency_weight=Decimal(model.latency_weight),
                quota_weight=Decimal(model.quota_weight),
                price_weight=Decimal(model.price_weight),
                maximum_observation_age_seconds=int(data["maximum_observation_age_seconds"]),
            ),
            created_by=model.created_by,
            created_at=model.created_at,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("persisted Route policy is invalid") from exc
    if policy_version.policy_sha256 != model.policy_sha256:
        raise RuntimeError("persisted Route policy checksum is inconsistent")
    return policy_version


class ProviderControlPlaneRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_provider(
        self, provider_id: str, *, for_update: bool = False
    ) -> ProviderIdentity | None:
        statement = select(ProviderIdentityModel).where(ProviderIdentityModel.id == provider_id)
        if for_update:
            statement = statement.with_for_update()
        model = self.session.scalar(statement)
        return _provider_from_model(model) if model is not None else None

    def add_provider(self, provider: ProviderIdentity) -> None:
        self.session.add(
            ProviderIdentityModel(
                id=provider.id,
                display_name=provider.display_name,
                enabled=provider.enabled,
                version=provider.version,
                created_by=provider.created_by,
                created_at=provider.created_at,
                updated_by=provider.updated_by,
                updated_at=provider.updated_at,
            )
        )

    def get_or_create_capability_head_for_update(
        self,
        *,
        provider_id: str,
        endpoint_id: str,
        now: datetime,
    ) -> ProviderEndpointCapabilityHead:
        self.session.execute(
            mysql_insert(ProviderEndpointCapabilityHeadModel)
            .values(
                provider_id=provider_id,
                endpoint_id=endpoint_id,
                current_version_id=None,
                current_version_number=0,
                latest_version_number=0,
                version=0,
                updated_by=None,
                updated_at=now,
            )
            .prefix_with("IGNORE")
        )
        model = self.session.scalar(
            select(ProviderEndpointCapabilityHeadModel)
            .where(
                ProviderEndpointCapabilityHeadModel.provider_id == provider_id,
                ProviderEndpointCapabilityHeadModel.endpoint_id == endpoint_id,
            )
            .with_for_update()
        )
        if model is None:
            raise RuntimeError("Provider capability head could not be created")
        return _head_from_model(model)

    def get_capability_version(
        self,
        *,
        provider_id: str,
        endpoint_id: str,
        capability_version_id: str,
    ) -> ProviderEndpointCapabilityVersion | None:
        model = self.session.scalar(
            select(ProviderEndpointCapabilityVersionModel).where(
                ProviderEndpointCapabilityVersionModel.provider_id == provider_id,
                ProviderEndpointCapabilityVersionModel.endpoint_id == endpoint_id,
                ProviderEndpointCapabilityVersionModel.id == capability_version_id,
            )
        )
        return capability_from_model(model) if model is not None else None

    def add_capability_version(
        self,
        capability: ProviderEndpointCapabilityVersion,
        *,
        actor_id: str,
    ) -> None:
        data = capability.to_canonical_data()
        data.pop("secret_reference")
        self.session.add(
            ProviderEndpointCapabilityVersionModel(
                id=capability.id,
                provider_id=capability.provider_id,
                endpoint_id=capability.endpoint_id,
                version_number=capability.version_number,
                capability_sha256=capability.capability_sha256,
                configuration_sha256=capability.configuration_sha256,
                secret_reference=capability.secret_reference,
                capability_json=data,
                unit_price=capability.unit_price,
                currency=capability.currency,
                created_by=actor_id,
                created_at=capability.created_at,
            )
        )
        # The head has a composite FK to this immutable row. The shared session
        # disables autoflush, so establish the referenced row before its CAS update.
        self.session.flush()

    def save_capability_head(
        self,
        head: ProviderEndpointCapabilityHead,
        *,
        expected_version: int,
    ) -> None:
        result = cast(
            CursorResult[Any],
            self.session.execute(
                update(ProviderEndpointCapabilityHeadModel)
                .where(
                    ProviderEndpointCapabilityHeadModel.provider_id == head.provider_id,
                    ProviderEndpointCapabilityHeadModel.endpoint_id == head.endpoint_id,
                    ProviderEndpointCapabilityHeadModel.version == expected_version,
                )
                .values(
                    current_version_id=head.current_version_id,
                    current_version_number=head.current_version_number,
                    latest_version_number=head.latest_version_number,
                    version=head.version,
                    updated_by=head.updated_by,
                    updated_at=head.updated_at,
                )
            ),
        )
        if result.rowcount != 1:
            raise ConcurrencyError("capability head version changed")

    def get_or_create_route_policy_head_for_update(
        self,
        *,
        workspace_id: str,
        policy_key: str,
        now: datetime,
    ) -> ModelRoutePolicyHead:
        self.session.execute(
            mysql_insert(ModelRoutePolicyHeadModel)
            .values(
                workspace_id=workspace_id,
                policy_key=policy_key,
                current_version_id=None,
                current_version_number=0,
                latest_version_number=0,
                version=0,
                updated_by=None,
                updated_at=now,
            )
            .prefix_with("IGNORE")
        )
        model = self.session.scalar(
            select(ModelRoutePolicyHeadModel)
            .where(
                ModelRoutePolicyHeadModel.workspace_id == workspace_id,
                ModelRoutePolicyHeadModel.policy_key == policy_key,
            )
            .with_for_update()
        )
        if model is None:
            raise RuntimeError("Route policy head could not be created")
        return _route_policy_head_from_model(model)

    def get_route_policy_version(
        self,
        *,
        workspace_id: str,
        policy_key: str,
        policy_version_id: str,
    ) -> ModelRoutePolicyVersion | None:
        model = self.session.scalar(
            select(ModelRoutePolicyVersionModel).where(
                ModelRoutePolicyVersionModel.workspace_id == workspace_id,
                ModelRoutePolicyVersionModel.policy_key == policy_key,
                ModelRoutePolicyVersionModel.id == policy_version_id,
            )
        )
        return route_policy_version_from_model(model) if model is not None else None

    def add_route_policy_version(self, policy_version: ModelRoutePolicyVersion) -> None:
        policy = policy_version.policy
        self.session.add(
            ModelRoutePolicyVersionModel(
                workspace_id=policy_version.workspace_id,
                id=policy_version.id,
                policy_key=policy_version.policy_key,
                version_number=policy_version.version_number,
                policy_version=policy.version,
                policy_sha256=policy_version.policy_sha256,
                policy_json=policy_version.to_canonical_data(),
                quality_weight=policy.quality_weight,
                availability_weight=policy.availability_weight,
                latency_weight=policy.latency_weight,
                quota_weight=policy.quota_weight,
                price_weight=policy.price_weight,
                created_by=policy_version.created_by,
                created_at=policy_version.created_at,
            )
        )
        self.session.flush()

    def save_route_policy_head(
        self,
        head: ModelRoutePolicyHead,
        *,
        expected_version: int,
    ) -> None:
        result = cast(
            CursorResult[Any],
            self.session.execute(
                update(ModelRoutePolicyHeadModel)
                .where(
                    ModelRoutePolicyHeadModel.workspace_id == head.workspace_id,
                    ModelRoutePolicyHeadModel.policy_key == head.policy_key,
                    ModelRoutePolicyHeadModel.version == expected_version,
                )
                .values(
                    current_version_id=head.current_version_id,
                    current_version_number=head.current_version_number,
                    latest_version_number=head.latest_version_number,
                    version=head.version,
                    updated_by=head.updated_by,
                    updated_at=head.updated_at,
                )
            ),
        )
        if result.rowcount != 1:
            raise ConcurrencyError("Route policy head version changed")

    def add_discovery_candidate(self, candidate: ProviderDiscoveryCandidate) -> None:
        self.session.add(
            ProviderDiscoveryCandidateModel(
                workspace_id=candidate.workspace_id,
                id=candidate.id,
                provider_id=candidate.provider_id,
                endpoint_id=candidate.endpoint_id,
                discovered_model_id=candidate.discovered_model_id,
                discovery_sha256=candidate.discovery_sha256,
                discovery_json=json.loads(candidate.discovery_json),
                state=candidate.state.value,
                discovered_by=candidate.discovered_by,
                discovered_at=candidate.discovered_at,
                reviewed_by=candidate.reviewed_by,
                reviewed_at=candidate.reviewed_at,
            )
        )

    def has_capability_version(self, capability_version_id: str) -> bool:
        return (
            self.session.scalar(
                select(ProviderEndpointCapabilityVersionModel.id).where(
                    ProviderEndpointCapabilityVersionModel.id == capability_version_id
                )
            )
            is not None
        )

    def add_endpoint_observation(self, observation: ProviderEndpointObservation) -> None:
        value = observation.observation
        self.session.add(
            ProviderEndpointObservationModel(
                workspace_id=observation.workspace_id,
                id=observation.id,
                endpoint_capability_version_id=value.endpoint_capability_version_id,
                quality_score=value.quality_score,
                availability_score=value.availability_score,
                latency_score=value.latency_score,
                quota_score=value.quota_score,
                circuit_state=value.circuit_state.value,
                remaining_quota_units=value.remaining_quota_units,
                observation_source=observation.observation_source,
                idempotency_key_sha256=observation.idempotency_key_sha256,
                observed_at=value.observed_at,
                created_by=observation.created_by,
                created_at=observation.created_at,
            )
        )


class SqlAlchemyProviderControlPlaneUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self._depth_token: object | None = None
        self._committed = False

    def __enter__(self) -> SqlAlchemyProviderControlPlaneUnitOfWork:
        self._session = self._session_factory()
        self._depth_token = enter_unit_of_work()
        self.control_plane = ProviderControlPlaneRepository(self._session)
        self.idempotency = IdempotencyRepository(self._session)
        self.audit = AuditRepository(self._session)
        return self

    def database_now(self) -> datetime:
        if self._session is None:
            raise RuntimeError("Provider control-plane unit of work is not active")
        value = self._session.scalar(select(literal_column("UTC_TIMESTAMP(6)")))
        if not isinstance(value, datetime):
            raise RuntimeError("database did not return a timestamp")
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("Provider control-plane unit of work is not active")
        try:
            self._session.commit()
        except DBAPIError as exc:
            self._session.rollback()
            classified = classify_database_error(exc)
            if classified is None:
                raise
            raise classified from exc
        self._committed = True

    def rollback(self) -> None:
        if self._session is not None:
            self._session.rollback()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if exc_type is not None or not self._committed:
                self.rollback()
        finally:
            if self._session is not None:
                self._session.close()
            if self._depth_token is not None:
                exit_unit_of_work(self._depth_token)
