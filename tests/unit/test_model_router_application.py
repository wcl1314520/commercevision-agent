from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from commercevision_application import (
    ModelRouterApplicationService,
    ModelRoutingAuthoritySnapshot,
)
from commercevision_application.model_router_ports import PersistedModelRouteDecision
from commercevision_domain import (
    CircuitState,
    EndpointRouteObservation,
    ImageRole,
    ModelRoutePolicy,
    ModelRouteRejectionCode,
    ModelRouteRequest,
    NoEligibleModelRouteError,
    ProviderCapability,
    ProviderDataRetentionMode,
    ProviderEndpointCapabilityVersion,
    ProviderExecutionMode,
    ProviderPricingUnit,
    ProviderProtocol,
    ProviderTrainingUsePolicy,
)

NOW = datetime(2026, 8, 6, 10, 0, tzinfo=UTC)
WORKFLOW_ID = "019b0000-0000-7000-8000-000000000401"
PLAN_VERSION_ID = "019b0000-0000-7000-8000-000000000402"
APPROVAL_ID = "019b0000-0000-7000-8000-000000000403"
POLICY_VERSION_ID = "019b0000-0000-7000-8000-000000000404"


def _capability(
    *, capability_id: str, provider_id: str, unit_price: str
) -> ProviderEndpointCapabilityVersion:
    return ProviderEndpointCapabilityVersion.create(
        id=capability_id,
        provider_id=provider_id,
        endpoint_id=f"{provider_id}-images",
        version_number=1,
        endpoint_host=f"{provider_id}.example.com",
        endpoint_region="cn-beijing",
        model_family="image-generation",
        model_id="image-model-v1",
        model_revision="2026-08-01",
        adapter_version="1.0.0",
        configuration_sha256="a" * 64,
        capabilities=frozenset({ProviderCapability.IMAGE_GENERATION}),
        protocol=ProviderProtocol.OPENAI_IMAGES_JSON,
        execution_mode=ProviderExecutionMode.SYNC,
        supports_query=False,
        supports_cancel=False,
        supports_provider_idempotency=False,
        allowed_categories=frozenset({"general-merchandise"}),
        allowed_image_roles=frozenset({ImageRole.MAIN}),
        output_formats=frozenset({"image/png"}),
        minimum_width=512,
        maximum_width=2048,
        minimum_height=512,
        maximum_height=2048,
        maximum_candidates=4,
        safety_policy_version="media-safety.v1",
        data_region="cn-beijing",
        data_retention_mode=ProviderDataRetentionMode.NONE,
        maximum_retention_days=0,
        training_use_policy=ProviderTrainingUsePolicy.PROHIBITED,
        secret_reference="secret-ref:phase4-provider-key",
        maximum_reference_images=0,
        supports_mask=False,
        supports_seed=False,
        supports_lora=False,
        maximum_request_bytes=4 * 1024 * 1024,
        maximum_result_bytes=16 * 1024 * 1024,
        pricing_unit=ProviderPricingUnit.IMAGE,
        enabled=True,
        unit_price=Decimal(unit_price),
        currency="CNY",
        created_at=NOW,
    )


def _request() -> ModelRouteRequest:
    return ModelRouteRequest(
        workspace_id="phase4-router",
        workflow_id=WORKFLOW_ID,
        creative_plan_version_id=PLAN_VERSION_ID,
        plan_approval_id=APPROVAL_ID,
        required_capability=ProviderCapability.IMAGE_GENERATION,
        allowed_providers=frozenset({"provider-a", "provider-b"}),
        allowed_endpoint_regions=frozenset({"cn-beijing"}),
        maximum_cost=Decimal("1.00"),
        currency="CNY",
        route_policy_version="price-first.v1",
        deadline_at=NOW + timedelta(minutes=2),
        required_execution_mode=ProviderExecutionMode.SYNC,
        requires_query=False,
        required_quota_units=1,
        product_category="general-merchandise",
        image_role=ImageRole.MAIN,
        required_output_format="image/png",
        width=1024,
        height=1024,
        candidate_count=1,
        required_safety_policy_version="media-safety.v1",
        allowed_data_regions=frozenset({"cn-beijing"}),
        maximum_retention_days=0,
        allow_training_use=False,
    )


def _observation(capability_id: str) -> EndpointRouteObservation:
    return EndpointRouteObservation(
        endpoint_capability_version_id=capability_id,
        quality_score=Decimal("1"),
        availability_score=Decimal("1"),
        latency_score=Decimal("1"),
        quota_score=Decimal("1"),
        circuit_state=CircuitState.CLOSED,
        remaining_quota_units=100,
        observed_at=NOW,
    )


@dataclass
class _IdempotencyRecord:
    request_hash: str
    status: str = "PENDING"
    response_data: dict[str, Any] | None = None
    resource_type: str = ""
    resource_id: str = ""


class _IdempotencyRepository:
    def __init__(self) -> None:
        self.records: dict[tuple[str, str], _IdempotencyRecord] = {}

    def claim(
        self,
        *,
        scope: str,
        key_hash: str,
        request_hash: str,
        expires_at: datetime,
    ) -> _IdempotencyRecord:
        del expires_at
        return self.records.setdefault((scope, key_hash), _IdempotencyRecord(request_hash))

    def complete(
        self,
        *,
        scope: str,
        key_hash: str,
        request_hash: str,
        resource_type: str,
        resource_id: str,
        response_data: dict[str, Any],
    ) -> None:
        record = self.records[(scope, key_hash)]
        assert record.request_hash == request_hash
        record.status = "COMPLETED"
        record.response_data = response_data
        record.resource_type = resource_type
        record.resource_id = resource_id


class _RouteAuthorityRepository:
    def __init__(self, snapshot: ModelRoutingAuthoritySnapshot) -> None:
        self.snapshot = snapshot
        self.decisions: list[Any] = []

    def load_current_authority(
        self,
        *,
        request: ModelRouteRequest,
        policy_key: str,
    ) -> ModelRoutingAuthoritySnapshot:
        assert request.workspace_id == "phase4-router"
        assert policy_key == "default-images"
        assert request.route_policy_version == "price-first.v1"
        return self.snapshot

    def add_decision(self, record: Any) -> None:
        self.decisions.append(record)

    def get_decision(
        self,
        *,
        workspace_id: str,
        decision_sha256: str,
    ) -> PersistedModelRouteDecision | None:
        record = next(
            (
                item
                for item in self.decisions
                if item.workspace_id == workspace_id
                and item.decision.decision_sha256 == decision_sha256
            ),
            None,
        )
        if record is None:
            return None
        return PersistedModelRouteDecision(
            policy_version_id=record.policy_version_id,
            decision=record.decision,
            estimated_cost=record.estimated_cost,
            currency=record.currency,
        )


class _AuditRepository:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def add(self, **kwargs: Any) -> None:
        self.records.append(kwargs)


class _UnitOfWork:
    def __init__(self, snapshot: ModelRoutingAuthoritySnapshot) -> None:
        self.route_authority = _RouteAuthorityRepository(snapshot)
        self.idempotency = _IdempotencyRepository()
        self.audit = _AuditRepository()
        self.commits = 0

    def __enter__(self) -> _UnitOfWork:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def database_now(self) -> datetime:
        return NOW

    def commit(self) -> None:
        self.commits += 1


def test_route_uses_server_authority_and_replays_the_original_immutable_decision() -> None:
    expensive = _capability(
        capability_id="019b0000-0000-7000-8000-000000000411",
        provider_id="provider-a",
        unit_price="0.20",
    )
    cheaper = _capability(
        capability_id="019b0000-0000-7000-8000-000000000412",
        provider_id="provider-b",
        unit_price="0.10",
    )
    policy = ModelRoutePolicy(
        version="price-first.v1",
        quality_weight=Decimal("0"),
        availability_weight=Decimal("0"),
        latency_weight=Decimal("0"),
        quota_weight=Decimal("0"),
        price_weight=Decimal("1"),
    )
    unit_of_work = _UnitOfWork(
        ModelRoutingAuthoritySnapshot(
            policy_version_id=POLICY_VERSION_ID,
            policy=policy,
            capabilities=(expensive, cheaper),
            observations=(_observation(expensive.id), _observation(cheaper.id)),
        )
    )
    service = ModelRouterApplicationService(lambda: unit_of_work)

    first = service.route(
        request=_request(),
        policy_key="default-images",
        actor_id="generation-service",
        idempotency_key="route-command-1",
        trace_id="trace-route-1",
    )
    replay = service.route(
        request=_request(),
        policy_key="default-images",
        actor_id="generation-service",
        idempotency_key="route-command-1",
        trace_id="trace-route-2",
    )

    assert first.decision.endpoint_capability_version_id == cheaper.id
    assert first.policy_version_id == POLICY_VERSION_ID
    assert first.estimated_cost == Decimal("0.10")
    assert first.currency == "CNY"
    assert first.replayed is False
    assert replay.decision == first.decision
    assert replay.policy_version_id == POLICY_VERSION_ID
    assert replay.replayed is True
    assert len(unit_of_work.route_authority.decisions) == 1
    persisted = unit_of_work.route_authority.decisions[0]
    assert persisted.workspace_id == "phase4-router"
    assert persisted.policy_version_id == POLICY_VERSION_ID
    assert persisted.decision == first.decision
    assert persisted.estimated_cost == Decimal("0.10")
    assert persisted.currency == "CNY"
    assert len(unit_of_work.audit.records) == 1
    assert unit_of_work.commits == 1


def test_route_fails_closed_without_current_capabilities_and_has_no_side_effects() -> None:
    policy = ModelRoutePolicy(
        version="price-first.v1",
        quality_weight=Decimal("0"),
        availability_weight=Decimal("0"),
        latency_weight=Decimal("0"),
        quota_weight=Decimal("0"),
        price_weight=Decimal("1"),
    )
    unit_of_work = _UnitOfWork(
        ModelRoutingAuthoritySnapshot(
            policy_version_id=POLICY_VERSION_ID,
            policy=policy,
            capabilities=(),
            observations=(),
        )
    )
    service = ModelRouterApplicationService(lambda: unit_of_work)

    with pytest.raises(NoEligibleModelRouteError) as caught:
        service.route(
            request=_request(),
            policy_key="default-images",
            actor_id="generation-service",
            idempotency_key="route-command-empty",
            trace_id="trace-route-empty",
        )

    assert caught.value.rejection_counts == ((ModelRouteRejectionCode.NO_CURRENT_CAPABILITY, 1),)
    assert unit_of_work.route_authority.decisions == []
    assert unit_of_work.audit.records == []
    assert unit_of_work.commits == 0
