"""Deterministic selection over immutable Provider routing facts."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from commercevision_domain.provider_routing import (
    _MAX_ENDPOINTS,
    _MONEY_QUANTUM,
    CircuitState,
    EndpointRouteObservation,
    ModelRouteCandidateScore,
    ModelRouteDecision,
    ModelRoutePolicy,
    ModelRouteRejectionCode,
    ModelRouteRequest,
    NoEligibleModelRouteError,
    ProviderDataRetentionMode,
    ProviderEndpointCapabilityVersion,
    ProviderPricingUnit,
    ProviderTrainingUsePolicy,
    _validate_utc,
)


def _estimated_cost(
    capability: ProviderEndpointCapabilityVersion,
    request: ModelRouteRequest,
) -> Decimal:
    quantity = (
        request.candidate_count
        if capability.pricing_unit is ProviderPricingUnit.IMAGE
        else request.required_quota_units
    )
    return capability.unit_price * Decimal(quantity)


def _rejection_for(
    capability: ProviderEndpointCapabilityVersion,
    *,
    request: ModelRouteRequest,
    policy: ModelRoutePolicy,
    observation: EndpointRouteObservation | None,
    now: datetime,
) -> ModelRouteRejectionCode | None:
    checks = (
        (not capability.enabled, ModelRouteRejectionCode.ENDPOINT_DISABLED),
        (
            request.required_capability not in capability.capabilities,
            ModelRouteRejectionCode.CAPABILITY_MISMATCH,
        ),
        (
            capability.execution_mode is not request.required_execution_mode,
            ModelRouteRejectionCode.EXECUTION_MODE_MISMATCH,
        ),
        (
            request.requires_query and not capability.supports_query,
            ModelRouteRejectionCode.QUERY_UNSUPPORTED,
        ),
        (
            capability.provider_id not in request.allowed_providers,
            ModelRouteRejectionCode.PROVIDER_NOT_ALLOWED,
        ),
        (
            capability.endpoint_region not in request.allowed_endpoint_regions,
            ModelRouteRejectionCode.REGION_NOT_ALLOWED,
        ),
        (capability.currency != request.currency, ModelRouteRejectionCode.CURRENCY_MISMATCH),
        (
            _estimated_cost(capability, request) > request.maximum_cost,
            ModelRouteRejectionCode.BUDGET_EXCEEDED,
        ),
        (observation is None, ModelRouteRejectionCode.OBSERVATION_MISSING),
        (
            observation is not None
            and not timedelta(0)
            <= now - observation.observed_at
            <= timedelta(seconds=policy.maximum_observation_age_seconds),
            ModelRouteRejectionCode.OBSERVATION_STALE,
        ),
        (
            observation is not None and observation.circuit_state is not CircuitState.CLOSED,
            ModelRouteRejectionCode.CIRCUIT_OPEN,
        ),
        (
            observation is not None
            and observation.remaining_quota_units < request.required_quota_units,
            ModelRouteRejectionCode.QUOTA_EXHAUSTED,
        ),
        (
            request.product_category not in capability.allowed_categories,
            ModelRouteRejectionCode.CATEGORY_NOT_ALLOWED,
        ),
        (
            request.image_role not in capability.allowed_image_roles,
            ModelRouteRejectionCode.IMAGE_ROLE_NOT_ALLOWED,
        ),
        (
            request.required_output_format not in capability.output_formats,
            ModelRouteRejectionCode.FORMAT_UNSUPPORTED,
        ),
        (
            not (
                capability.minimum_width <= request.width <= capability.maximum_width
                and capability.minimum_height <= request.height <= capability.maximum_height
            ),
            ModelRouteRejectionCode.SIZE_UNSUPPORTED,
        ),
        (
            request.candidate_count > capability.maximum_candidates,
            ModelRouteRejectionCode.CANDIDATE_COUNT_EXCEEDED,
        ),
        (
            request.required_safety_policy_version != capability.safety_policy_version,
            ModelRouteRejectionCode.SAFETY_POLICY_MISMATCH,
        ),
        (
            capability.data_region not in request.allowed_data_regions,
            ModelRouteRejectionCode.DATA_REGION_NOT_ALLOWED,
        ),
        (
            capability.data_retention_mode is ProviderDataRetentionMode.UNKNOWN,
            ModelRouteRejectionCode.RETENTION_POLICY_UNKNOWN,
        ),
        (
            capability.maximum_retention_days > request.maximum_retention_days,
            ModelRouteRejectionCode.RETENTION_EXCEEDED,
        ),
        (
            capability.training_use_policy is ProviderTrainingUsePolicy.UNKNOWN,
            ModelRouteRejectionCode.TRAINING_POLICY_UNKNOWN,
        ),
        (
            not request.allow_training_use
            and capability.training_use_policy is ProviderTrainingUsePolicy.PERMITTED,
            ModelRouteRejectionCode.TRAINING_USE_NOT_ALLOWED,
        ),
        (
            capability.protocol is not request.required_protocol,
            ModelRouteRejectionCode.PROTOCOL_MISMATCH,
        ),
        (
            request.reference_image_count > capability.maximum_reference_images,
            ModelRouteRejectionCode.REFERENCE_IMAGES_UNSUPPORTED,
        ),
        (
            request.requires_mask and not capability.supports_mask,
            ModelRouteRejectionCode.MASK_UNSUPPORTED,
        ),
        (
            request.requires_seed and not capability.supports_seed,
            ModelRouteRejectionCode.SEED_UNSUPPORTED,
        ),
        (
            request.requires_lora and not capability.supports_lora,
            ModelRouteRejectionCode.LORA_UNSUPPORTED,
        ),
        (
            request.estimated_request_bytes > capability.maximum_request_bytes,
            ModelRouteRejectionCode.REQUEST_TOO_LARGE,
        ),
        (
            request.required_result_byte_limit > capability.maximum_result_bytes,
            ModelRouteRejectionCode.RESULT_LIMIT_UNSUPPORTED,
        ),
    )
    return next((code for rejected, code in checks if rejected), None)


def _score(
    capability: ProviderEndpointCapabilityVersion,
    *,
    request: ModelRouteRequest,
    policy: ModelRoutePolicy,
    observation: EndpointRouteObservation,
) -> Decimal:
    price_score = (
        Decimal("1")
        if request.maximum_cost == 0
        else Decimal("1") - (_estimated_cost(capability, request) / request.maximum_cost)
    )
    return (
        policy.quality_weight * observation.quality_score
        + policy.availability_weight * observation.availability_score
        + policy.latency_weight * observation.latency_score
        + policy.quota_weight * observation.quota_score
        + policy.price_weight * price_score
    )


def select_model_route(
    *,
    request: ModelRouteRequest,
    capabilities: tuple[ProviderEndpointCapabilityVersion, ...],
    policy: ModelRoutePolicy,
    observations: tuple[EndpointRouteObservation, ...],
    now: datetime,
) -> ModelRouteDecision:
    """Select eligible endpoint versions after mandatory hard filters."""

    if not isinstance(request, ModelRouteRequest):
        raise ValueError("Route request is invalid")
    if not isinstance(policy, ModelRoutePolicy) or policy.version != request.route_policy_version:
        raise ValueError("Route policy version does not match the request")
    if not isinstance(capabilities, tuple) or not 1 <= len(capabilities) <= _MAX_ENDPOINTS:
        raise ValueError("Route capabilities must be a bounded non-empty tuple")
    if any(not isinstance(item, ProviderEndpointCapabilityVersion) for item in capabilities):
        raise ValueError("Route capabilities contain an invalid version")
    capability_ids = [item.id for item in capabilities]
    if len(capability_ids) != len(set(capability_ids)):
        raise ValueError("Route capabilities contain duplicate versions")
    if not isinstance(observations, tuple) or len(observations) > _MAX_ENDPOINTS:
        raise ValueError("Route observations must be a bounded tuple")
    if any(not isinstance(item, EndpointRouteObservation) for item in observations):
        raise ValueError("Route observations contain an invalid value")
    observation_ids = [item.endpoint_capability_version_id for item in observations]
    if len(observation_ids) != len(set(observation_ids)):
        raise ValueError("Route observations contain duplicate endpoint versions")
    if not set(observation_ids).issubset(capability_ids):
        raise ValueError("Route observation does not match a supplied capability version")
    _validate_utc(now, "Route decision time")
    if now >= request.deadline_at:
        raise ValueError("Route request deadline has expired")

    observations_by_endpoint = {item.endpoint_capability_version_id: item for item in observations}
    rejections = tuple(
        (
            capability,
            _rejection_for(
                capability,
                request=request,
                policy=policy,
                observation=observations_by_endpoint.get(capability.id),
                now=now,
            ),
        )
        for capability in capabilities
    )
    eligible = tuple(capability for capability, rejection in rejections if rejection is None)
    counts: dict[ModelRouteRejectionCode, int] = {}
    for _, rejection in rejections:
        if rejection is not None:
            counts[rejection] = counts.get(rejection, 0) + 1
    rejection_counts = tuple(sorted(counts.items(), key=lambda item: item[0].value))
    if not eligible:
        raise NoEligibleModelRouteError(rejection_counts)

    scores: dict[str, Decimal] = {}
    for capability in eligible:
        observation = observations_by_endpoint[capability.id]
        scores[capability.id] = _score(
            capability,
            request=request,
            policy=policy,
            observation=observation,
        ).quantize(_MONEY_QUANTUM)
    ordered = tuple(sorted(eligible, key=lambda item: (-scores[item.id], item.id)))
    selected, *fallbacks = ordered
    return ModelRouteDecision(
        endpoint_capability_version_id=selected.id,
        fallback_endpoint_capability_version_ids=tuple(item.id for item in fallbacks),
        route_policy_version=policy.version,
        request_sha256=request.request_sha256,
        candidate_scores=tuple(
            ModelRouteCandidateScore(
                endpoint_capability_version_id=item.id,
                score=scores[item.id],
            )
            for item in ordered
        ),
        rejection_counts=rejection_counts,
        decided_at=now,
    )
