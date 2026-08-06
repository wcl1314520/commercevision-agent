from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from threading import Barrier

import pytest
from commercevision_application import ProviderControlPlaneApplicationService
from commercevision_domain import (
    CircuitState,
    ConcurrencyError,
    EndpointRouteObservation,
    ImageRole,
    ModelRoutePolicy,
    ModelRoutePolicyVersion,
    ProviderCapability,
    ProviderDataRetentionMode,
    ProviderDiscoveryCandidate,
    ProviderEndpointCapabilityVersion,
    ProviderEndpointObservation,
    ProviderExecutionMode,
    ProviderPricingUnit,
    ProviderProtocol,
    ProviderTrainingUsePolicy,
)
from commercevision_domain.workflow.errors import IdempotencyConflictError
from commercevision_persistence import SqlAlchemyProviderControlPlaneUnitOfWork
from commercevision_persistence.models import AuditEventModel, IdempotencyKeyModel
from commercevision_persistence.provider_control_plane_models import (
    ModelRoutePolicyHeadModel,
    ModelRoutePolicyVersionModel,
    ProviderDiscoveryCandidateModel,
    ProviderEndpointCapabilityHeadModel,
    ProviderEndpointCapabilityVersionModel,
    ProviderEndpointObservationModel,
)
from sqlalchemy import select

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 8, 1, 13, 0, tzinfo=UTC)
_SECRET_REFERENCE = "secret-ref:test-provider-key"


def _idempotency_key(label: str) -> str:
    return f"test:{label}"


def _capability(*, capability_id: str, version_number: int) -> ProviderEndpointCapabilityVersion:
    return ProviderEndpointCapabilityVersion.create(
        id=capability_id,
        provider_id="kuaipao-test",
        endpoint_id="images",
        version_number=version_number,
        endpoint_host="kuaipao.pro",
        endpoint_region="unknown",
        model_family="image-generation",
        model_id="gpt-image-1",
        model_revision=f"2026-08-0{version_number}",
        adapter_version="1.0.0",
        configuration_sha256=f"{version_number}" * 64,
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
        data_region="unknown",
        data_retention_mode=ProviderDataRetentionMode.NONE,
        maximum_retention_days=0,
        training_use_policy=ProviderTrainingUsePolicy.PROHIBITED,
        secret_reference=_SECRET_REFERENCE,
        maximum_reference_images=0,
        supports_mask=False,
        supports_seed=False,
        supports_lora=False,
        maximum_request_bytes=4 * 1024 * 1024,
        maximum_result_bytes=16 * 1024 * 1024,
        pricing_unit=ProviderPricingUnit.IMAGE,
        enabled=True,
        unit_price=Decimal("0.20"),
        currency="CNY",
        created_at=_NOW,
    )


def test_provider_capability_commands_are_idempotent_immutable_and_secret_safe(
    integration_database,
) -> None:
    service = ProviderControlPlaneApplicationService(
        lambda: SqlAlchemyProviderControlPlaneUnitOfWork(integration_database.session_factory)
    )
    workspace_id = "provider-control-plane-workspace"
    first = _capability(
        capability_id="019b0000-0000-7000-8000-000000000611",
        version_number=1,
    )
    second = _capability(
        capability_id="019b0000-0000-7000-8000-000000000612",
        version_number=2,
    )

    registered = service.register_provider(
        workspace_id=workspace_id,
        provider_id="kuaipao-test",
        display_name="Kuaipao Test",
        actor_id="provider-admin",
        idempotency_key=_idempotency_key("register-provider-001"),
        trace_id="trace-register-provider-001",
    )
    replayed_registration = service.register_provider(
        workspace_id=workspace_id,
        provider_id="kuaipao-test",
        display_name="Kuaipao Test",
        actor_id="provider-admin",
        idempotency_key=_idempotency_key("register-provider-001"),
        trace_id="trace-register-provider-replay",
    )
    assert registered.replayed is False
    assert replayed_registration.replayed is True

    published_first = service.publish_capability(
        workspace_id=workspace_id,
        capability=first,
        expected_head_version=0,
        actor_id="provider-admin",
        idempotency_key=_idempotency_key("publish-capability-001"),
        trace_id="trace-publish-capability-001",
    )
    replayed_first = service.publish_capability(
        workspace_id=workspace_id,
        capability=first,
        expected_head_version=0,
        actor_id="provider-admin",
        idempotency_key=_idempotency_key("publish-capability-001"),
        trace_id="trace-publish-capability-replay",
    )
    assert published_first.replayed is False
    assert replayed_first.replayed is True

    with pytest.raises(IdempotencyConflictError):
        service.publish_capability(
            workspace_id=workspace_id,
            capability=second,
            expected_head_version=1,
            actor_id="provider-admin",
            idempotency_key=_idempotency_key("publish-capability-001"),
            trace_id="trace-publish-capability-conflict",
        )

    published_second = service.publish_capability(
        workspace_id=workspace_id,
        capability=second,
        expected_head_version=1,
        actor_id="provider-admin",
        idempotency_key=_idempotency_key("publish-capability-002"),
        trace_id="trace-publish-capability-002",
    )
    rolled_back = service.rollback_capability(
        workspace_id=workspace_id,
        provider_id=first.provider_id,
        endpoint_id=first.endpoint_id,
        target_version_id=first.id,
        expected_head_version=2,
        actor_id="provider-admin",
        idempotency_key=_idempotency_key("rollback-capability-001"),
        trace_id="trace-rollback-capability-001",
    )
    restored = service.rollback_capability(
        workspace_id=workspace_id,
        provider_id=second.provider_id,
        endpoint_id=second.endpoint_id,
        target_version_id=second.id,
        expected_head_version=3,
        actor_id="provider-admin",
        idempotency_key=_idempotency_key("restore-capability-001"),
        trace_id="trace-restore-capability-001",
    )

    assert published_second.current_version_number == 2
    assert rolled_back.current_version_number == 1
    assert restored.current_version_number == 2
    assert restored.latest_version_number == 2
    assert restored.head_version == 4

    with integration_database.session_factory() as session:
        versions = session.scalars(
            select(ProviderEndpointCapabilityVersionModel).order_by(
                ProviderEndpointCapabilityVersionModel.version_number
            )
        ).all()
        head = session.get(
            ProviderEndpointCapabilityHeadModel,
            (first.provider_id, first.endpoint_id),
        )
        idempotency_records = session.scalars(select(IdempotencyKeyModel)).all()
        audits = session.scalars(select(AuditEventModel)).all()

    assert [version.version_number for version in versions] == [1, 2]
    assert all(_SECRET_REFERENCE not in str(version.capability_json) for version in versions)
    assert head is not None
    assert head.current_version_id == second.id
    assert head.latest_version_number == 2
    assert len(audits) == 5
    assert all(_SECRET_REFERENCE not in str(record.response_json) for record in idempotency_records)
    assert all(_SECRET_REFERENCE not in str(audit.metadata_json) for audit in audits)


def test_discovery_and_endpoint_observations_append_without_mutating_live_capability(
    integration_database,
) -> None:
    service = ProviderControlPlaneApplicationService(
        lambda: SqlAlchemyProviderControlPlaneUnitOfWork(integration_database.session_factory)
    )
    workspace_id = "provider-observation-workspace"
    capability = _capability(
        capability_id="019b0000-0000-7000-8000-000000000731",
        version_number=1,
    )
    service.register_provider(
        workspace_id=workspace_id,
        provider_id=capability.provider_id,
        display_name="Kuaipao Test",
        actor_id="provider-admin",
        idempotency_key=_idempotency_key("register-observed-provider-001"),
        trace_id="trace-register-observed-provider-001",
    )
    published = service.publish_capability(
        workspace_id=workspace_id,
        capability=capability,
        expected_head_version=0,
        actor_id="provider-admin",
        idempotency_key=_idempotency_key("publish-observed-capability-001"),
        trace_id="trace-publish-observed-capability-001",
    )
    candidate = ProviderDiscoveryCandidate.create(
        id="019b0000-0000-7000-8000-000000000732",
        workspace_id=workspace_id,
        provider_id=capability.provider_id,
        endpoint_id=capability.endpoint_id,
        discovered_model_id="new-image-model",
        evidence={"owner": "provider", "capabilities": ["image-generation"]},
        discovered_by="contract-probe",
        now=_NOW,
    )
    discovery = service.record_discovery_candidate(
        candidate=candidate,
        idempotency_key=_idempotency_key("record-discovery-candidate-001"),
        trace_id="trace-record-discovery-candidate-001",
    )
    discovery_replay = service.record_discovery_candidate(
        candidate=candidate,
        idempotency_key=_idempotency_key("record-discovery-candidate-001"),
        trace_id="trace-record-discovery-candidate-replay",
    )
    observation_key = "record-provider-observation-001"
    observation = ProviderEndpointObservation.create(
        id="019b0000-0000-7000-8000-000000000733",
        workspace_id=workspace_id,
        observation=EndpointRouteObservation(
            endpoint_capability_version_id=capability.id,
            quality_score=Decimal("0.900000"),
            availability_score=Decimal("0.850000"),
            latency_score=Decimal("0.700000"),
            quota_score=Decimal("0.600000"),
            circuit_state=CircuitState.CLOSED,
            remaining_quota_units=100,
            observed_at=_NOW,
        ),
        observation_source="provider-health-probe",
        idempotency_key_sha256=hashlib.sha256(observation_key.encode()).hexdigest(),
        actor_id="health-worker",
        now=_NOW,
    )
    recorded = service.record_endpoint_observation(
        observation=observation,
        idempotency_key=observation_key,
        trace_id="trace-record-provider-observation-001",
    )
    recorded_replay = service.record_endpoint_observation(
        observation=observation,
        idempotency_key=observation_key,
        trace_id="trace-record-provider-observation-replay",
    )

    assert discovery.replayed is False
    assert discovery_replay.replayed is True
    assert discovery.state == "PENDING_REVIEW"
    assert recorded.replayed is False
    assert recorded_replay.replayed is True

    with integration_database.session_factory() as session:
        head = session.get(
            ProviderEndpointCapabilityHeadModel,
            (capability.provider_id, capability.endpoint_id),
        )
        candidates = session.scalars(select(ProviderDiscoveryCandidateModel)).all()
        observations = session.scalars(select(ProviderEndpointObservationModel)).all()
    assert head is not None
    assert head.current_version_id == capability.id
    assert head.version == published.head_version == 1
    assert len(candidates) == 1
    assert candidates[0].state == "PENDING_REVIEW"
    assert len(observations) == 1
    assert observations[0].remaining_quota_units == 100


def test_route_policy_commands_append_versions_and_cas_move_only_the_pointer(
    integration_database,
) -> None:
    service = ProviderControlPlaneApplicationService(
        lambda: SqlAlchemyProviderControlPlaneUnitOfWork(integration_database.session_factory)
    )
    workspace_id = "route-policy-workspace"
    policy = ModelRoutePolicy(
        version="route-policy.v1",
        quality_weight=Decimal("0.300000"),
        availability_weight=Decimal("0.250000"),
        latency_weight=Decimal("0.200000"),
        quota_weight=Decimal("0.150000"),
        price_weight=Decimal("0.100000"),
    )
    first = ModelRoutePolicyVersion.create(
        id="019b0000-0000-7000-8000-000000000711",
        workspace_id=workspace_id,
        policy_key="image-generation",
        version_number=1,
        policy=policy,
        actor_id="route-admin",
        now=_NOW,
    )
    second = ModelRoutePolicyVersion.create(
        id="019b0000-0000-7000-8000-000000000712",
        workspace_id=workspace_id,
        policy_key="image-generation",
        version_number=2,
        policy=ModelRoutePolicy(
            version="route-policy.v2",
            quality_weight=Decimal("0.350000"),
            availability_weight=Decimal("0.250000"),
            latency_weight=Decimal("0.150000"),
            quota_weight=Decimal("0.150000"),
            price_weight=Decimal("0.100000"),
        ),
        actor_id="route-admin",
        now=_NOW,
    )

    published_first = service.publish_route_policy(
        workspace_id=workspace_id,
        policy_version=first,
        expected_head_version=0,
        actor_id="route-admin",
        idempotency_key=_idempotency_key("publish-route-policy-001"),
        trace_id="trace-publish-route-policy-001",
    )
    replayed_first = service.publish_route_policy(
        workspace_id=workspace_id,
        policy_version=first,
        expected_head_version=0,
        actor_id="route-admin",
        idempotency_key=_idempotency_key("publish-route-policy-001"),
        trace_id="trace-publish-route-policy-replay",
    )
    published_second = service.publish_route_policy(
        workspace_id=workspace_id,
        policy_version=second,
        expected_head_version=1,
        actor_id="route-admin",
        idempotency_key=_idempotency_key("publish-route-policy-002"),
        trace_id="trace-publish-route-policy-002",
    )
    rolled_back = service.rollback_route_policy(
        workspace_id=workspace_id,
        policy_key="image-generation",
        target_version_id=first.id,
        expected_head_version=2,
        actor_id="route-admin",
        idempotency_key=_idempotency_key("rollback-route-policy-001"),
        trace_id="trace-rollback-route-policy-001",
    )
    restored = service.rollback_route_policy(
        workspace_id=workspace_id,
        policy_key="image-generation",
        target_version_id=second.id,
        expected_head_version=3,
        actor_id="route-admin",
        idempotency_key=_idempotency_key("restore-route-policy-001"),
        trace_id="trace-restore-route-policy-001",
    )

    assert published_first.current_version_number == 1
    assert replayed_first.replayed is True
    assert published_second.current_version_number == 2
    assert rolled_back.current_version_number == 1
    assert restored.current_version_number == 2
    assert restored.latest_version_number == 2
    assert restored.head_version == 4

    with integration_database.session_factory() as session:
        versions = session.scalars(
            select(ModelRoutePolicyVersionModel).order_by(
                ModelRoutePolicyVersionModel.version_number
            )
        ).all()
        head = session.get(
            ModelRoutePolicyHeadModel,
            (workspace_id, "image-generation"),
        )
    assert [version.id for version in versions] == [first.id, second.id]
    assert head is not None
    assert head.current_version_id == second.id
    assert head.latest_version_number == 2


def test_concurrent_capability_publish_has_exactly_one_cas_winner(
    integration_database,
) -> None:
    service = ProviderControlPlaneApplicationService(
        lambda: SqlAlchemyProviderControlPlaneUnitOfWork(integration_database.session_factory)
    )
    workspace_id = "provider-concurrency-workspace"
    first = _capability(
        capability_id="019b0000-0000-7000-8000-000000000741",
        version_number=1,
    )
    contenders = (
        _capability(
            capability_id="019b0000-0000-7000-8000-000000000742",
            version_number=2,
        ),
        _capability(
            capability_id="019b0000-0000-7000-8000-000000000743",
            version_number=2,
        ),
    )
    service.register_provider(
        workspace_id=workspace_id,
        provider_id=first.provider_id,
        display_name="Kuaipao Test",
        actor_id="provider-admin",
        idempotency_key=_idempotency_key("register-concurrent-provider-001"),
        trace_id="trace-register-concurrent-provider-001",
    )
    service.publish_capability(
        workspace_id=workspace_id,
        capability=first,
        expected_head_version=0,
        actor_id="provider-admin",
        idempotency_key=_idempotency_key("publish-concurrent-base-001"),
        trace_id="trace-publish-concurrent-base-001",
    )
    start = Barrier(2)

    def publish(contender: ProviderEndpointCapabilityVersion) -> str:
        start.wait(timeout=5)
        try:
            service.publish_capability(
                workspace_id=workspace_id,
                capability=contender,
                expected_head_version=1,
                actor_id="provider-admin",
                idempotency_key=_idempotency_key(f"publish-concurrent-{contender.id}"),
                trace_id=f"trace-publish-concurrent-{contender.id}",
            )
        except ConcurrencyError:
            return "stale"
        return "published"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(publish, contenders))

    assert sorted(outcomes) == ["published", "stale"]
    with integration_database.session_factory() as session:
        versions = session.scalars(
            select(ProviderEndpointCapabilityVersionModel).order_by(
                ProviderEndpointCapabilityVersionModel.version_number
            )
        ).all()
        head = session.get(
            ProviderEndpointCapabilityHeadModel,
            (first.provider_id, first.endpoint_id),
        )
    assert [version.version_number for version in versions] == [1, 2]
    assert head is not None
    assert head.version == 2
    assert head.current_version_id in {contender.id for contender in contenders}
