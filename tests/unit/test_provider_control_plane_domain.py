from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from commercevision_domain import (
    CircuitState,
    ConcurrencyError,
    EndpointRouteObservation,
    ImageRole,
    ModelRoutePolicy,
    ModelRoutePolicyHead,
    ModelRoutePolicyVersion,
    ProviderCapability,
    ProviderDataRetentionMode,
    ProviderDiscoveryCandidate,
    ProviderDiscoveryCandidateState,
    ProviderEndpointCapabilityHead,
    ProviderEndpointCapabilityVersion,
    ProviderEndpointObservation,
    ProviderExecutionMode,
    ProviderPricingUnit,
    ProviderProtocol,
    ProviderTrainingUsePolicy,
)

NOW = datetime(2026, 8, 6, 13, 0, tzinfo=UTC)


def _capability(*, capability_id: str, version_number: int) -> ProviderEndpointCapabilityVersion:
    return ProviderEndpointCapabilityVersion.create(
        id=capability_id,
        provider_id="kuaipao",
        endpoint_id="kuaipao-images",
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
        secret_reference="secret-ref:phase4-provider-key",
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
        created_at=NOW,
    )


def test_capability_publish_and_rollback_only_cas_move_the_current_pointer() -> None:
    first = _capability(
        capability_id="019b0000-0000-7000-8000-000000000601",
        version_number=1,
    )
    second = replace(
        _capability(
            capability_id="019b0000-0000-7000-8000-000000000602",
            version_number=2,
        ),
        unit_price=Decimal("0.18"),
    )
    head = ProviderEndpointCapabilityHead.create(
        provider_id="kuaipao",
        endpoint_id="kuaipao-images",
        now=NOW,
    )

    head.publish(first, expected_version=0, actor_id="admin-1", now=NOW)
    head.publish(second, expected_version=1, actor_id="admin-2", now=NOW)

    assert head.current_version_id == second.id
    assert head.current_version_number == 2
    assert head.version == 2

    head.rollback(
        target=first,
        expected_version=2,
        actor_id="admin-3",
        now=NOW,
    )

    assert head.current_version_id == first.id
    assert head.current_version_number == 1
    assert head.version == 3
    assert first.version_number == 1
    assert second.version_number == 2
    assert second.unit_price == Decimal("0.18")

    stale_snapshot = replace(head)
    with pytest.raises(ConcurrencyError, match="capability head version"):
        stale_snapshot.publish(second, expected_version=2, actor_id="admin-stale", now=NOW)
    assert stale_snapshot == head

    head.rollback(
        target=second,
        expected_version=3,
        actor_id="admin-4",
        now=NOW,
    )

    assert head.current_version_id == second.id
    assert head.current_version_number == 2
    assert head.latest_version_number == 2
    assert head.version == 4


def test_discovery_and_observation_are_bounded_immutable_control_plane_facts() -> None:
    candidate = ProviderDiscoveryCandidate.create(
        id="019b0000-0000-7000-8000-000000000721",
        workspace_id="workspace-discovery",
        provider_id="kuaipao",
        endpoint_id="images",
        discovered_model_id="gpt-image-1",
        evidence={"owner": "provider", "capabilities": ["image-generation"]},
        discovered_by="contract-probe",
        now=NOW,
    )
    observation = ProviderEndpointObservation.create(
        id="019b0000-0000-7000-8000-000000000722",
        workspace_id="workspace-discovery",
        observation=EndpointRouteObservation(
            endpoint_capability_version_id="019b0000-0000-7000-8000-000000000601",
            quality_score=Decimal("0.900000"),
            availability_score=Decimal("0.800000"),
            latency_score=Decimal("0.700000"),
            quota_score=Decimal("0.600000"),
            circuit_state=CircuitState.CLOSED,
            remaining_quota_units=100,
            observed_at=NOW,
        ),
        observation_source="provider-health-probe",
        idempotency_key_sha256="a" * 64,
        actor_id="health-worker",
        now=NOW,
    )

    assert candidate.state is ProviderDiscoveryCandidateState.PENDING_REVIEW
    assert len(candidate.discovery_sha256) == 64
    assert observation.observation.circuit_state is CircuitState.CLOSED

    approved = replace(
        candidate,
        state=ProviderDiscoveryCandidateState.APPROVED,
        reviewed_by="provider-admin",
        reviewed_at=NOW,
    )
    assert approved.state.value == "APPROVED"

    with pytest.raises(ValueError, match="credential-like"):
        ProviderDiscoveryCandidate.create(
            id="019b0000-0000-7000-8000-000000000723",
            workspace_id="workspace-discovery",
            provider_id="kuaipao",
            endpoint_id="images",
            discovered_model_id="gpt-image-1",
            evidence={"api_key": "must-not-persist"},
            discovered_by="contract-probe",
            now=NOW,
        )

    with pytest.raises(ValueError, match="credential-like"):
        ProviderDiscoveryCandidate.create(
            id="019b0000-0000-7000-8000-000000000725",
            workspace_id="workspace-discovery",
            provider_id="kuaipao",
            endpoint_id="images",
            discovered_model_id="gpt-image-1",
            evidence={"value": "sk-test-raw-credential"},
            discovered_by="contract-probe",
            now=NOW,
        )

    with pytest.raises(ValueError, match="Secret Reference"):
        replace(
            _capability(
                capability_id="019b0000-0000-7000-8000-000000000724",
                version_number=1,
            ),
            secret_reference="sk-test-raw-credential",
        )


def test_route_policy_publish_and_rollback_preserve_immutable_versions() -> None:
    policy = ModelRoutePolicy(
        version="route-policy.v1",
        quality_weight=Decimal("0.300000"),
        availability_weight=Decimal("0.250000"),
        latency_weight=Decimal("0.200000"),
        quota_weight=Decimal("0.150000"),
        price_weight=Decimal("0.100000"),
    )
    first = ModelRoutePolicyVersion.create(
        id="019b0000-0000-7000-8000-000000000701",
        workspace_id="workspace-route-policy",
        policy_key="image-generation",
        version_number=1,
        policy=policy,
        actor_id="route-admin",
        now=NOW,
    )
    second = ModelRoutePolicyVersion.create(
        id="019b0000-0000-7000-8000-000000000702",
        workspace_id="workspace-route-policy",
        policy_key="image-generation",
        version_number=2,
        policy=replace(policy, version="route-policy.v2"),
        actor_id="route-admin",
        now=NOW,
    )
    head = ModelRoutePolicyHead.create(
        workspace_id="workspace-route-policy",
        policy_key="image-generation",
        now=NOW,
    )

    head.publish(first, expected_version=0, actor_id="route-admin", now=NOW)
    head.publish(second, expected_version=1, actor_id="route-admin", now=NOW)
    head.rollback(target=first, expected_version=2, actor_id="route-admin", now=NOW)
    head.rollback(target=second, expected_version=3, actor_id="route-admin", now=NOW)

    assert first.version_number == 1
    assert second.version_number == 2
    assert head.current_version_id == second.id
    assert head.current_version_number == 2
    assert head.latest_version_number == 2
    assert head.version == 4


def test_route_policy_hash_survives_fixed_scale_mysql_decimal_round_trip() -> None:
    original = ModelRoutePolicyVersion.create(
        id="019b0000-0000-7000-8000-000000000703",
        workspace_id="workspace-route-policy",
        policy_key="image-generation",
        version_number=1,
        policy=ModelRoutePolicy(
            version="price-first.v1",
            quality_weight=Decimal("0"),
            availability_weight=Decimal("0"),
            latency_weight=Decimal("0"),
            quota_weight=Decimal("0"),
            price_weight=Decimal("1"),
        ),
        actor_id="route-admin",
        now=NOW,
    )
    reconstructed = replace(
        original,
        policy=ModelRoutePolicy(
            version="price-first.v1",
            quality_weight=Decimal("0.000000"),
            availability_weight=Decimal("0.000000"),
            latency_weight=Decimal("0.000000"),
            quota_weight=Decimal("0.000000"),
            price_weight=Decimal("1.000000"),
        ),
    )

    assert reconstructed.policy_sha256 == original.policy_sha256
