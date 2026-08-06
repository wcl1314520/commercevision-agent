from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from commercevision_domain import (
    CircuitState,
    EndpointRouteObservation,
    ImageRole,
    ModelRouteCandidateScore,
    ModelRouteFailoverCause,
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
    select_model_route,
)

NOW = datetime(2026, 8, 6, 10, 0, tzinfo=UTC)
WORKFLOW_ID = "019b0000-0000-7000-8000-000000000401"
PLAN_VERSION_ID = "019b0000-0000-7000-8000-000000000402"
APPROVAL_ID = "019b0000-0000-7000-8000-000000000403"


def _capability(
    *,
    capability_id: str,
    provider_id: str,
    endpoint_id: str,
    endpoint_host: str,
    endpoint_region: str,
    unit_price: str,
    execution_mode: ProviderExecutionMode = ProviderExecutionMode.SYNC,
    supports_query: bool = False,
    data_retention_mode: ProviderDataRetentionMode = ProviderDataRetentionMode.NONE,
    training_use_policy: ProviderTrainingUsePolicy = ProviderTrainingUsePolicy.PROHIBITED,
    capabilities: frozenset[ProviderCapability] = frozenset({ProviderCapability.IMAGE_GENERATION}),
    protocol: ProviderProtocol = ProviderProtocol.OPENAI_IMAGES_JSON,
) -> ProviderEndpointCapabilityVersion:
    return ProviderEndpointCapabilityVersion.create(
        id=capability_id,
        provider_id=provider_id,
        endpoint_id=endpoint_id,
        version_number=1,
        endpoint_host=endpoint_host,
        endpoint_region=endpoint_region,
        model_family="image-generation",
        model_id="image-model-v1",
        model_revision="2026-08-01",
        adapter_version="1.0.0",
        configuration_sha256="a" * 64,
        capabilities=capabilities,
        protocol=protocol,
        execution_mode=execution_mode,
        supports_query=supports_query,
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
        data_retention_mode=data_retention_mode,
        maximum_retention_days=0,
        training_use_policy=training_use_policy,
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


def _observation(
    capability: ProviderEndpointCapabilityVersion,
    *,
    circuit_state: CircuitState = CircuitState.CLOSED,
    remaining_quota_units: int = 100,
    quality_score: str = "1",
) -> EndpointRouteObservation:
    return EndpointRouteObservation(
        endpoint_capability_version_id=capability.id,
        quality_score=Decimal(quality_score),
        availability_score=Decimal("1"),
        latency_score=Decimal("1"),
        quota_score=Decimal("1"),
        circuit_state=circuit_state,
        remaining_quota_units=remaining_quota_units,
        observed_at=NOW,
    )


def test_model_route_applies_provider_and_region_hard_filters_before_price_score() -> None:
    cheaper_but_denied = _capability(
        capability_id="019b0000-0000-7000-8000-000000000411",
        provider_id="kuaipao",
        endpoint_id="kuaipao-images",
        endpoint_host="kuaipao.pro",
        endpoint_region="unknown",
        unit_price="0.01",
    )
    allowed = _capability(
        capability_id="019b0000-0000-7000-8000-000000000412",
        provider_id="alibaba-model-studio",
        endpoint_id="wan-beijing",
        endpoint_host="workspace.cn-beijing.maas.aliyuncs.com",
        endpoint_region="cn-beijing",
        unit_price="0.20",
    )
    request = ModelRouteRequest(
        workspace_id="phase4-domain",
        workflow_id=WORKFLOW_ID,
        creative_plan_version_id=PLAN_VERSION_ID,
        plan_approval_id=APPROVAL_ID,
        required_capability=ProviderCapability.IMAGE_GENERATION,
        allowed_providers=frozenset({"alibaba-model-studio"}),
        allowed_endpoint_regions=frozenset({"cn-beijing"}),
        maximum_cost=Decimal("1.00"),
        currency="CNY",
        route_policy_version="route-policy.v1",
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
    policy = ModelRoutePolicy(
        version="route-policy.v1",
        quality_weight=Decimal("0"),
        availability_weight=Decimal("0"),
        latency_weight=Decimal("0"),
        quota_weight=Decimal("0"),
        price_weight=Decimal("1"),
    )

    decision = select_model_route(
        request=request,
        capabilities=(cheaper_but_denied, allowed),
        policy=policy,
        observations=(_observation(cheaper_but_denied), _observation(allowed)),
        now=NOW,
    )

    assert decision.endpoint_capability_version_id == allowed.id
    assert decision.fallback_endpoint_capability_version_ids == ()
    assert decision.route_policy_version == "route-policy.v1"


def test_model_route_scores_eligible_endpoints_with_the_versioned_policy() -> None:
    cheaper = _capability(
        capability_id="019b0000-0000-7000-8000-000000000421",
        provider_id="kuaipao",
        endpoint_id="kuaipao-images",
        endpoint_host="kuaipao.pro",
        endpoint_region="cn-beijing",
        unit_price="0.10",
    )
    higher_quality = _capability(
        capability_id="019b0000-0000-7000-8000-000000000422",
        provider_id="alibaba-model-studio",
        endpoint_id="wan-beijing",
        endpoint_host="workspace.cn-beijing.maas.aliyuncs.com",
        endpoint_region="cn-beijing",
        unit_price="0.20",
    )
    request = ModelRouteRequest(
        workspace_id="phase4-domain",
        workflow_id=WORKFLOW_ID,
        creative_plan_version_id=PLAN_VERSION_ID,
        plan_approval_id=APPROVAL_ID,
        required_capability=ProviderCapability.IMAGE_GENERATION,
        allowed_providers=frozenset({"alibaba-model-studio", "kuaipao"}),
        allowed_endpoint_regions=frozenset({"cn-beijing"}),
        maximum_cost=Decimal("1.00"),
        currency="CNY",
        route_policy_version="quality-first.v1",
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
    policy = ModelRoutePolicy(
        version="quality-first.v1",
        quality_weight=Decimal("0.75"),
        availability_weight=Decimal("0"),
        latency_weight=Decimal("0"),
        quota_weight=Decimal("0"),
        price_weight=Decimal("0.25"),
    )
    observations = (
        EndpointRouteObservation(
            endpoint_capability_version_id=cheaper.id,
            quality_score=Decimal("0.10"),
            availability_score=Decimal("1"),
            latency_score=Decimal("1"),
            quota_score=Decimal("1"),
            circuit_state=CircuitState.CLOSED,
            remaining_quota_units=100,
            observed_at=NOW,
        ),
        EndpointRouteObservation(
            endpoint_capability_version_id=higher_quality.id,
            quality_score=Decimal("1"),
            availability_score=Decimal("1"),
            latency_score=Decimal("1"),
            quota_score=Decimal("1"),
            circuit_state=CircuitState.CLOSED,
            remaining_quota_units=100,
            observed_at=NOW,
        ),
    )

    decision = select_model_route(
        request=request,
        capabilities=(cheaper, higher_quality),
        policy=policy,
        observations=observations,
        now=NOW,
    )

    assert decision.endpoint_capability_version_id == higher_quality.id
    assert decision.fallback_endpoint_capability_version_ids == (cheaper.id,)


def test_model_route_requires_positive_async_query_capabilities() -> None:
    sync_without_query = _capability(
        capability_id="019b0000-0000-7000-8000-000000000431",
        provider_id="kuaipao",
        endpoint_id="kuaipao-images",
        endpoint_host="kuaipao.pro",
        endpoint_region="cn-beijing",
        unit_price="0.01",
    )
    async_with_query = _capability(
        capability_id="019b0000-0000-7000-8000-000000000432",
        provider_id="alibaba-model-studio",
        endpoint_id="wan-async-beijing",
        endpoint_host="workspace.cn-beijing.maas.aliyuncs.com",
        endpoint_region="cn-beijing",
        unit_price="0.20",
        execution_mode=ProviderExecutionMode.ASYNC,
        supports_query=True,
    )
    request = ModelRouteRequest(
        workspace_id="phase4-domain",
        workflow_id=WORKFLOW_ID,
        creative_plan_version_id=PLAN_VERSION_ID,
        plan_approval_id=APPROVAL_ID,
        required_capability=ProviderCapability.IMAGE_GENERATION,
        allowed_providers=frozenset({"alibaba-model-studio", "kuaipao"}),
        allowed_endpoint_regions=frozenset({"cn-beijing"}),
        maximum_cost=Decimal("1.00"),
        currency="CNY",
        route_policy_version="price-first.v1",
        deadline_at=NOW + timedelta(minutes=2),
        required_execution_mode=ProviderExecutionMode.ASYNC,
        requires_query=True,
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
    policy = ModelRoutePolicy(
        version="price-first.v1",
        quality_weight=Decimal("0"),
        availability_weight=Decimal("0"),
        latency_weight=Decimal("0"),
        quota_weight=Decimal("0"),
        price_weight=Decimal("1"),
    )

    decision = select_model_route(
        request=request,
        capabilities=(sync_without_query, async_with_query),
        policy=policy,
        observations=(_observation(sync_without_query), _observation(async_with_query)),
        now=NOW,
    )

    assert decision.endpoint_capability_version_id == async_with_query.id
    assert decision.fallback_endpoint_capability_version_ids == ()


def test_model_route_reports_stable_circuit_and_quota_rejections() -> None:
    circuit_open = _capability(
        capability_id="019b0000-0000-7000-8000-000000000441",
        provider_id="kuaipao",
        endpoint_id="kuaipao-images",
        endpoint_host="kuaipao.pro",
        endpoint_region="cn-beijing",
        unit_price="0.10",
    )
    quota_exhausted = _capability(
        capability_id="019b0000-0000-7000-8000-000000000442",
        provider_id="alibaba-model-studio",
        endpoint_id="wan-beijing",
        endpoint_host="workspace.cn-beijing.maas.aliyuncs.com",
        endpoint_region="cn-beijing",
        unit_price="0.20",
    )
    request = ModelRouteRequest(
        workspace_id="phase4-domain",
        workflow_id=WORKFLOW_ID,
        creative_plan_version_id=PLAN_VERSION_ID,
        plan_approval_id=APPROVAL_ID,
        required_capability=ProviderCapability.IMAGE_GENERATION,
        allowed_providers=frozenset({"alibaba-model-studio", "kuaipao"}),
        allowed_endpoint_regions=frozenset({"cn-beijing"}),
        maximum_cost=Decimal("1.00"),
        currency="CNY",
        route_policy_version="price-first.v1",
        deadline_at=NOW + timedelta(minutes=2),
        required_execution_mode=ProviderExecutionMode.SYNC,
        requires_query=False,
        required_quota_units=2,
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
    policy = ModelRoutePolicy(
        version="price-first.v1",
        quality_weight=Decimal("0"),
        availability_weight=Decimal("0"),
        latency_weight=Decimal("0"),
        quota_weight=Decimal("0"),
        price_weight=Decimal("1"),
    )

    with pytest.raises(NoEligibleModelRouteError) as caught:
        select_model_route(
            request=request,
            capabilities=(circuit_open, quota_exhausted),
            policy=policy,
            observations=(
                _observation(circuit_open, circuit_state=CircuitState.OPEN),
                _observation(quota_exhausted, remaining_quota_units=1),
            ),
            now=NOW,
        )

    assert caught.value.rejection_counts == (
        (ModelRouteRejectionCode.CIRCUIT_OPEN, 1),
        (ModelRouteRejectionCode.QUOTA_EXHAUSTED, 1),
    )


def test_model_route_rejects_incompatible_media_and_safety_capabilities() -> None:
    base = _capability(
        capability_id="019b0000-0000-7000-8000-000000000450",
        provider_id="alibaba-model-studio",
        endpoint_id="wan-beijing",
        endpoint_host="workspace.cn-beijing.maas.aliyuncs.com",
        endpoint_region="cn-beijing",
        unit_price="0.20",
    )
    denied = (
        replace(
            base,
            id="019b0000-0000-7000-8000-000000000451",
            allowed_categories=frozenset({"restricted-category"}),
        ),
        replace(
            base,
            id="019b0000-0000-7000-8000-000000000452",
            allowed_image_roles=frozenset({ImageRole.DETAIL}),
        ),
        replace(
            base,
            id="019b0000-0000-7000-8000-000000000453",
            output_formats=frozenset({"image/jpeg"}),
        ),
        replace(
            base,
            id="019b0000-0000-7000-8000-000000000454",
            maximum_width=512,
        ),
        replace(
            base,
            id="019b0000-0000-7000-8000-000000000455",
            maximum_candidates=1,
        ),
        replace(
            base,
            id="019b0000-0000-7000-8000-000000000456",
            safety_policy_version="media-safety.v2",
        ),
    )
    request = ModelRouteRequest(
        workspace_id="phase4-domain",
        workflow_id=WORKFLOW_ID,
        creative_plan_version_id=PLAN_VERSION_ID,
        plan_approval_id=APPROVAL_ID,
        required_capability=ProviderCapability.IMAGE_GENERATION,
        allowed_providers=frozenset({"alibaba-model-studio"}),
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
        candidate_count=2,
        required_safety_policy_version="media-safety.v1",
        allowed_data_regions=frozenset({"cn-beijing"}),
        maximum_retention_days=0,
        allow_training_use=False,
    )
    policy = ModelRoutePolicy(
        version="price-first.v1",
        quality_weight=Decimal("0"),
        availability_weight=Decimal("0"),
        latency_weight=Decimal("0"),
        quota_weight=Decimal("0"),
        price_weight=Decimal("1"),
    )

    with pytest.raises(NoEligibleModelRouteError) as caught:
        select_model_route(
            request=request,
            capabilities=denied,
            policy=policy,
            observations=tuple(_observation(item) for item in denied),
            now=NOW,
        )

    assert caught.value.rejection_counts == (
        (ModelRouteRejectionCode.CANDIDATE_COUNT_EXCEEDED, 1),
        (ModelRouteRejectionCode.CATEGORY_NOT_ALLOWED, 1),
        (ModelRouteRejectionCode.FORMAT_UNSUPPORTED, 1),
        (ModelRouteRejectionCode.IMAGE_ROLE_NOT_ALLOWED, 1),
        (ModelRouteRejectionCode.SAFETY_POLICY_MISMATCH, 1),
        (ModelRouteRejectionCode.SIZE_UNSUPPORTED, 1),
    )


def test_model_route_fails_closed_on_unknown_provider_data_policy() -> None:
    reviewed = _capability(
        capability_id="019b0000-0000-7000-8000-000000000462",
        provider_id="alibaba-model-studio",
        endpoint_id="wan-beijing",
        endpoint_host="workspace.cn-beijing.maas.aliyuncs.com",
        endpoint_region="cn-beijing",
        unit_price="0.20",
    )
    unknown = replace(
        reviewed,
        id="019b0000-0000-7000-8000-000000000461",
        provider_id="kuaipao",
        endpoint_id="kuaipao-images",
        endpoint_host="kuaipao.pro",
        data_region="unknown",
        data_retention_mode=ProviderDataRetentionMode.UNKNOWN,
        training_use_policy=ProviderTrainingUsePolicy.UNKNOWN,
        unit_price=Decimal("0.01"),
    )
    request = ModelRouteRequest(
        workspace_id="phase4-domain",
        workflow_id=WORKFLOW_ID,
        creative_plan_version_id=PLAN_VERSION_ID,
        plan_approval_id=APPROVAL_ID,
        required_capability=ProviderCapability.IMAGE_GENERATION,
        allowed_providers=frozenset({"alibaba-model-studio", "kuaipao"}),
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
    policy = ModelRoutePolicy(
        version="price-first.v1",
        quality_weight=Decimal("0"),
        availability_weight=Decimal("0"),
        latency_weight=Decimal("0"),
        quota_weight=Decimal("0"),
        price_weight=Decimal("1"),
    )

    decision = select_model_route(
        request=request,
        capabilities=(unknown, reviewed),
        policy=policy,
        observations=(_observation(unknown), _observation(reviewed)),
        now=NOW,
    )

    assert decision.endpoint_capability_version_id == reviewed.id
    assert decision.fallback_endpoint_capability_version_ids == ()
    assert not hasattr(decision, "secret_reference")


def test_model_route_decision_hash_is_stable_for_reconstructed_inputs() -> None:
    alpha = _capability(
        capability_id="019b0000-0000-7000-8000-000000000471",
        provider_id="alibaba-model-studio",
        endpoint_id="wan-alpha",
        endpoint_host="alpha.cn-beijing.maas.aliyuncs.com",
        endpoint_region="cn-beijing",
        unit_price="0.20",
    )
    zulu = replace(
        alpha,
        id="019b0000-0000-7000-8000-000000000472",
        endpoint_id="wan-zulu",
        endpoint_host="zulu.cn-beijing.maas.aliyuncs.com",
    )
    request = ModelRouteRequest(
        workspace_id="phase4-domain",
        workflow_id=WORKFLOW_ID,
        creative_plan_version_id=PLAN_VERSION_ID,
        plan_approval_id=APPROVAL_ID,
        required_capability=ProviderCapability.IMAGE_GENERATION,
        allowed_providers=frozenset({"alibaba-model-studio"}),
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
    policy = ModelRoutePolicy(
        version="price-first.v1",
        quality_weight=Decimal("0"),
        availability_weight=Decimal("0"),
        latency_weight=Decimal("0"),
        quota_weight=Decimal("0"),
        price_weight=Decimal("1"),
    )

    first = select_model_route(
        request=request,
        capabilities=(zulu, alpha),
        policy=policy,
        observations=(_observation(zulu), _observation(alpha)),
        now=NOW,
    )
    reconstructed = select_model_route(
        request=request,
        capabilities=(alpha, zulu),
        policy=policy,
        observations=(_observation(alpha), _observation(zulu)),
        now=NOW,
    )
    later = select_model_route(
        request=request,
        capabilities=(alpha, zulu),
        policy=policy,
        observations=(_observation(alpha), _observation(zulu)),
        now=NOW + timedelta(seconds=1),
    )

    assert len(alpha.capability_sha256) == 64
    assert len(request.request_sha256) == 64
    assert request.canonical_data()["width"] == 1024
    assert request.canonical_data()["required_output_format"] == "image/png"
    assert ModelRouteRequest.from_canonical_data(request.canonical_data()) == request
    with pytest.raises(ValueError, match="projection"):
        ModelRouteRequest.from_canonical_data(request.canonical_data() | {"unexpected": True})
    assert first.request_sha256 == request.request_sha256
    assert first.decision_sha256 == reconstructed.decision_sha256
    assert first.decision_sha256 != later.decision_sha256
    request_with_reference = replace(
        request,
        reference_image_count=1,
        authorized_asset_version_ids=("019b0000-0000-7000-8000-000000000479",),
    )
    assert request_with_reference.request_sha256 != request.request_sha256
    with pytest.raises(ValueError, match="authorized Asset Version"):
        replace(request, reference_image_count=1)
    with pytest.raises(FrozenInstanceError):
        first.route_policy_version = "attacker-policy.v1"  # type: ignore[misc]


def test_model_route_decision_explains_scores_and_aggregate_rejections() -> None:
    denied = _capability(
        capability_id="019b0000-0000-7000-8000-000000000481",
        provider_id="kuaipao",
        endpoint_id="kuaipao-images",
        endpoint_host="kuaipao.pro",
        endpoint_region="cn-beijing",
        unit_price="0.01",
    )
    allowed = _capability(
        capability_id="019b0000-0000-7000-8000-000000000482",
        provider_id="alibaba-model-studio",
        endpoint_id="wan-beijing",
        endpoint_host="workspace.cn-beijing.maas.aliyuncs.com",
        endpoint_region="cn-beijing",
        unit_price="0.20",
    )
    request = ModelRouteRequest(
        workspace_id="phase4-domain",
        workflow_id=WORKFLOW_ID,
        creative_plan_version_id=PLAN_VERSION_ID,
        plan_approval_id=APPROVAL_ID,
        required_capability=ProviderCapability.IMAGE_GENERATION,
        allowed_providers=frozenset({"alibaba-model-studio"}),
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
    policy = ModelRoutePolicy(
        version="price-first.v1",
        quality_weight=Decimal("0"),
        availability_weight=Decimal("0"),
        latency_weight=Decimal("0"),
        quota_weight=Decimal("0"),
        price_weight=Decimal("1"),
    )

    decision = select_model_route(
        request=request,
        capabilities=(denied, allowed),
        policy=policy,
        observations=(_observation(denied), _observation(allowed)),
        now=NOW,
    )

    assert decision.candidate_scores == (
        ModelRouteCandidateScore(
            endpoint_capability_version_id=allowed.id,
            score=Decimal("0.800000"),
        ),
    )
    assert decision.rejection_counts == ((ModelRouteRejectionCode.PROVIDER_NOT_ALLOWED, 1),)
    assert denied.id not in repr(decision)
    assert denied.secret_reference not in repr(decision)


def test_model_route_decision_allows_only_safe_failover_causes() -> None:
    alpha = _capability(
        capability_id="019b0000-0000-7000-8000-000000000491",
        provider_id="alibaba-model-studio",
        endpoint_id="wan-alpha",
        endpoint_host="alpha.cn-beijing.maas.aliyuncs.com",
        endpoint_region="cn-beijing",
        unit_price="0.10",
    )
    zulu = replace(
        alpha,
        id="019b0000-0000-7000-8000-000000000492",
        endpoint_id="wan-zulu",
        endpoint_host="zulu.cn-beijing.maas.aliyuncs.com",
        unit_price=Decimal("0.20"),
    )
    request = ModelRouteRequest(
        workspace_id="phase4-domain",
        workflow_id=WORKFLOW_ID,
        creative_plan_version_id=PLAN_VERSION_ID,
        plan_approval_id=APPROVAL_ID,
        required_capability=ProviderCapability.IMAGE_GENERATION,
        allowed_providers=frozenset({"alibaba-model-studio"}),
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
    policy = ModelRoutePolicy(
        version="price-first.v1",
        quality_weight=Decimal("0"),
        availability_weight=Decimal("0"),
        latency_weight=Decimal("0"),
        quota_weight=Decimal("0"),
        price_weight=Decimal("1"),
    )
    decision = select_model_route(
        request=request,
        capabilities=(zulu, alpha),
        policy=policy,
        observations=(_observation(zulu), _observation(alpha)),
        now=NOW,
    )

    assert (
        decision.next_fallback(
            cause=ModelRouteFailoverCause.SAFE_PRE_DISPATCH_FAILURE,
            attempted_endpoint_capability_version_ids=(alpha.id,),
        )
        == zulu.id
    )
    assert (
        decision.next_fallback(
            cause=ModelRouteFailoverCause.CONFIRMED_RETRYABLE_FAILURE,
            attempted_endpoint_capability_version_ids=(alpha.id,),
        )
        == zulu.id
    )
    for forbidden in (
        ModelRouteFailoverCause.CONTENT_REJECTION,
        ModelRouteFailoverCause.POLICY_DENIAL,
        ModelRouteFailoverCause.INVALID_INPUT,
        ModelRouteFailoverCause.UNKNOWN_OUTCOME,
    ):
        assert (
            decision.next_fallback(
                cause=forbidden,
                attempted_endpoint_capability_version_ids=(alpha.id,),
            )
            is None
        )
    with pytest.raises(ValueError, match="attempted endpoint"):
        decision.next_fallback(
            cause=ModelRouteFailoverCause.SAFE_PRE_DISPATCH_FAILURE,
            attempted_endpoint_capability_version_ids=(zulu.id, alpha.id),
        )


def test_model_route_requires_the_exact_positive_provider_protocol() -> None:
    json_edit = _capability(
        capability_id="019b0000-0000-7000-8000-0000000004a1",
        provider_id="kuaipao",
        endpoint_id="kuaipao-edit-json",
        endpoint_host="kuaipao.pro",
        endpoint_region="cn-beijing",
        unit_price="0.01",
        capabilities=frozenset({ProviderCapability.IMAGE_EDITING}),
        protocol=ProviderProtocol.OPENAI_IMAGES_JSON,
    )
    multipart_edit = replace(
        json_edit,
        id="019b0000-0000-7000-8000-0000000004a2",
        endpoint_id="kuaipao-edit-multipart",
        protocol=ProviderProtocol.OPENAI_IMAGES_MULTIPART,
        maximum_reference_images=1,
        supports_mask=True,
        unit_price=Decimal("0.20"),
    )
    request = ModelRouteRequest(
        workspace_id="phase4-domain",
        workflow_id=WORKFLOW_ID,
        creative_plan_version_id=PLAN_VERSION_ID,
        plan_approval_id=APPROVAL_ID,
        required_capability=ProviderCapability.IMAGE_EDITING,
        allowed_providers=frozenset({"kuaipao"}),
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
        required_protocol=ProviderProtocol.OPENAI_IMAGES_MULTIPART,
    )
    policy = ModelRoutePolicy(
        version="price-first.v1",
        quality_weight=Decimal("0"),
        availability_weight=Decimal("0"),
        latency_weight=Decimal("0"),
        quota_weight=Decimal("0"),
        price_weight=Decimal("1"),
    )

    decision = select_model_route(
        request=request,
        capabilities=(json_edit, multipart_edit),
        policy=policy,
        observations=(_observation(json_edit), _observation(multipart_edit)),
        now=NOW,
    )

    assert decision.endpoint_capability_version_id == multipart_edit.id
    assert decision.fallback_endpoint_capability_version_ids == ()
    assert json_edit.capability_sha256 != multipart_edit.capability_sha256


def test_model_route_applies_budget_to_the_full_candidate_quantity() -> None:
    within_budget = _capability(
        capability_id="019b0000-0000-7000-8000-0000000004b1",
        provider_id="alibaba-model-studio",
        endpoint_id="wan-within-budget",
        endpoint_host="within.cn-beijing.maas.aliyuncs.com",
        endpoint_region="cn-beijing",
        unit_price="0.30",
    )
    over_budget = replace(
        within_budget,
        id="019b0000-0000-7000-8000-0000000004b2",
        endpoint_id="wan-over-budget",
        endpoint_host="over.cn-beijing.maas.aliyuncs.com",
        unit_price=Decimal("0.40"),
    )
    request = ModelRouteRequest(
        workspace_id="phase4-domain",
        workflow_id=WORKFLOW_ID,
        creative_plan_version_id=PLAN_VERSION_ID,
        plan_approval_id=APPROVAL_ID,
        required_capability=ProviderCapability.IMAGE_GENERATION,
        allowed_providers=frozenset({"alibaba-model-studio"}),
        allowed_endpoint_regions=frozenset({"cn-beijing"}),
        maximum_cost=Decimal("1.00"),
        currency="CNY",
        route_policy_version="price-first.v1",
        deadline_at=NOW + timedelta(minutes=2),
        required_execution_mode=ProviderExecutionMode.SYNC,
        requires_query=False,
        required_quota_units=3,
        product_category="general-merchandise",
        image_role=ImageRole.MAIN,
        required_output_format="image/png",
        width=1024,
        height=1024,
        candidate_count=3,
        required_safety_policy_version="media-safety.v1",
        allowed_data_regions=frozenset({"cn-beijing"}),
        maximum_retention_days=0,
        allow_training_use=False,
    )
    policy = ModelRoutePolicy(
        version="price-first.v1",
        quality_weight=Decimal("0"),
        availability_weight=Decimal("0"),
        latency_weight=Decimal("0"),
        quota_weight=Decimal("0"),
        price_weight=Decimal("1"),
    )

    decision = select_model_route(
        request=request,
        capabilities=(over_budget, within_budget),
        policy=policy,
        observations=(_observation(over_budget), _observation(within_budget)),
        now=NOW,
    )

    assert decision.endpoint_capability_version_id == within_budget.id
    assert decision.fallback_endpoint_capability_version_ids == ()
    assert decision.rejection_counts == ((ModelRouteRejectionCode.BUDGET_EXCEEDED, 1),)


def test_model_route_rejects_unsupported_inputs_and_byte_budgets() -> None:
    fully_capable = replace(
        _capability(
            capability_id="019b0000-0000-7000-8000-0000000004c0",
            provider_id="alibaba-model-studio",
            endpoint_id="wan-edit",
            endpoint_host="edit.cn-beijing.maas.aliyuncs.com",
            endpoint_region="cn-beijing",
            unit_price="0.20",
            capabilities=frozenset({ProviderCapability.IMAGE_EDITING}),
            protocol=ProviderProtocol.OPENAI_IMAGES_MULTIPART,
        ),
        maximum_reference_images=2,
        supports_mask=True,
        supports_seed=True,
        supports_lora=True,
        maximum_request_bytes=16 * 1024 * 1024,
        maximum_result_bytes=32 * 1024 * 1024,
    )
    denied = (
        replace(
            fully_capable,
            id="019b0000-0000-7000-8000-0000000004c1",
            maximum_reference_images=0,
            supports_mask=False,
        ),
        replace(
            fully_capable,
            id="019b0000-0000-7000-8000-0000000004c2",
            supports_mask=False,
        ),
        replace(
            fully_capable,
            id="019b0000-0000-7000-8000-0000000004c3",
            supports_seed=False,
        ),
        replace(
            fully_capable,
            id="019b0000-0000-7000-8000-0000000004c4",
            supports_lora=False,
        ),
        replace(
            fully_capable,
            id="019b0000-0000-7000-8000-0000000004c5",
            maximum_request_bytes=1024,
        ),
        replace(
            fully_capable,
            id="019b0000-0000-7000-8000-0000000004c6",
            maximum_result_bytes=1024,
        ),
    )
    request = ModelRouteRequest(
        workspace_id="phase4-domain",
        workflow_id=WORKFLOW_ID,
        creative_plan_version_id=PLAN_VERSION_ID,
        plan_approval_id=APPROVAL_ID,
        required_capability=ProviderCapability.IMAGE_EDITING,
        allowed_providers=frozenset({"alibaba-model-studio"}),
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
        required_protocol=ProviderProtocol.OPENAI_IMAGES_MULTIPART,
        reference_image_count=1,
        authorized_asset_version_ids=("019b0000-0000-7000-8000-0000000004c7",),
        requires_mask=True,
        requires_seed=True,
        requires_lora=True,
        estimated_request_bytes=8 * 1024 * 1024,
        required_result_byte_limit=16 * 1024 * 1024,
    )
    policy = ModelRoutePolicy(
        version="price-first.v1",
        quality_weight=Decimal("0"),
        availability_weight=Decimal("0"),
        latency_weight=Decimal("0"),
        quota_weight=Decimal("0"),
        price_weight=Decimal("1"),
    )

    with pytest.raises(NoEligibleModelRouteError) as caught:
        select_model_route(
            request=request,
            capabilities=denied,
            policy=policy,
            observations=tuple(_observation(item) for item in denied),
            now=NOW,
        )

    assert caught.value.rejection_counts == (
        (ModelRouteRejectionCode.LORA_UNSUPPORTED, 1),
        (ModelRouteRejectionCode.MASK_UNSUPPORTED, 1),
        (ModelRouteRejectionCode.REFERENCE_IMAGES_UNSUPPORTED, 1),
        (ModelRouteRejectionCode.REQUEST_TOO_LARGE, 1),
        (ModelRouteRejectionCode.RESULT_LIMIT_UNSUPPORTED, 1),
        (ModelRouteRejectionCode.SEED_UNSUPPORTED, 1),
    )


def test_provider_capability_rejects_raw_credential_as_secret_reference() -> None:
    capability = _capability(
        capability_id="019b0000-0000-7000-8000-0000000004d1",
        provider_id="kuaipao",
        endpoint_id="kuaipao-images",
        endpoint_host="kuaipao.pro",
        endpoint_region="cn-beijing",
        unit_price="0.10",
    )

    with pytest.raises(ValueError, match="Secret Reference"):
        replace(capability, secret_reference="sk-" + "x" * 40)


def test_model_route_fails_closed_on_stale_or_future_observations() -> None:
    capability = _capability(
        capability_id="019b0000-0000-7000-8000-000000000421",
        provider_id="alibaba-model-studio",
        endpoint_id="wan-beijing",
        endpoint_host="workspace.cn-beijing.maas.aliyuncs.com",
        endpoint_region="cn-beijing",
        unit_price="0.20",
    )
    request = ModelRouteRequest(
        workspace_id="phase4-domain",
        workflow_id=WORKFLOW_ID,
        creative_plan_version_id=PLAN_VERSION_ID,
        plan_approval_id=APPROVAL_ID,
        required_capability=ProviderCapability.IMAGE_GENERATION,
        allowed_providers=frozenset({"alibaba-model-studio"}),
        allowed_endpoint_regions=frozenset({"cn-beijing"}),
        maximum_cost=Decimal("1.00"),
        currency="CNY",
        route_policy_version="route-policy.v1",
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
    policy = ModelRoutePolicy(
        version="route-policy.v1",
        quality_weight=Decimal("1"),
        availability_weight=Decimal("0"),
        latency_weight=Decimal("0"),
        quota_weight=Decimal("0"),
        price_weight=Decimal("0"),
        maximum_observation_age_seconds=60,
    )

    for observed_at in (NOW - timedelta(seconds=61), NOW + timedelta(microseconds=1)):
        observation = replace(_observation(capability), observed_at=observed_at)
        with pytest.raises(NoEligibleModelRouteError) as exc_info:
            select_model_route(
                request=request,
                capabilities=(capability,),
                policy=policy,
                observations=(observation,),
                now=NOW,
            )

        assert exc_info.value.rejection_counts == ((ModelRouteRejectionCode.OBSERVATION_STALE, 1),)


def test_model_route_supports_planning_json_endpoint_as_positive_capability() -> None:
    capability = replace(
        _capability(
            capability_id="019b0000-0000-7000-8000-000000000422",
            provider_id="alibaba-model-studio",
            endpoint_id="qwen-planning-beijing",
            endpoint_host="dashscope.aliyuncs.com",
            endpoint_region="cn-beijing",
            unit_price="0.000001",
            capabilities=frozenset({ProviderCapability.PLAN}),
            protocol=ProviderProtocol.QWEN_CHAT_JSON,
        ),
        model_family="qwen",
        model_id="qwen-plus",
        output_formats=frozenset({"application/json"}),
        pricing_unit=ProviderPricingUnit.OUTPUT_TOKEN,
    )
    request = ModelRouteRequest(
        workspace_id="phase4-domain",
        workflow_id=WORKFLOW_ID,
        creative_plan_version_id=PLAN_VERSION_ID,
        plan_approval_id=APPROVAL_ID,
        required_capability=ProviderCapability.PLAN,
        allowed_providers=frozenset({"alibaba-model-studio"}),
        allowed_endpoint_regions=frozenset({"cn-beijing"}),
        maximum_cost=Decimal("0.01"),
        currency="CNY",
        route_policy_version="route-policy.v1",
        deadline_at=NOW + timedelta(minutes=2),
        required_execution_mode=ProviderExecutionMode.SYNC,
        requires_query=False,
        required_quota_units=1000,
        product_category="general-merchandise",
        image_role=ImageRole.MAIN,
        required_output_format="application/json",
        width=1024,
        height=1024,
        candidate_count=1,
        required_safety_policy_version="media-safety.v1",
        allowed_data_regions=frozenset({"cn-beijing"}),
        maximum_retention_days=0,
        allow_training_use=False,
        required_protocol=ProviderProtocol.QWEN_CHAT_JSON,
    )
    policy = ModelRoutePolicy(
        version="route-policy.v1",
        quality_weight=Decimal("1"),
        availability_weight=Decimal("0"),
        latency_weight=Decimal("0"),
        quota_weight=Decimal("0"),
        price_weight=Decimal("0"),
    )

    decision = select_model_route(
        request=request,
        capabilities=(capability,),
        policy=policy,
        observations=(_observation(capability, remaining_quota_units=2_000),),
        now=NOW,
    )

    assert decision.endpoint_capability_version_id == capability.id
