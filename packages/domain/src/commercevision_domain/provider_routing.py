"""Immutable Provider capability facts and deterministic model routing."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from commercevision_domain.creative_plans import ImageRole
from commercevision_domain.ids import canonicalize_uuid
from commercevision_domain.workflow.errors import DomainError
from commercevision_domain.workspace_identity import validate_workspace_id

_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)
_HOST_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.ASCII)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$", re.ASCII)
_MIME_TYPE_PATTERN = re.compile(
    r"^[a-z0-9][a-z0-9.+-]{0,63}/[a-z0-9][a-z0-9.+-]{0,63}$",
    re.ASCII,
)
_SECRET_REFERENCE_PATTERN = re.compile(
    r"^secret-ref:[A-Za-z0-9][A-Za-z0-9._:-]{0,116}$",
    re.ASCII,
)
_MAX_ENDPOINTS = 128
_MAX_PERMISSION_VALUES = 128
_MAX_MONEY = Decimal("99999999999999.999999")
_MONEY_QUANTUM = Decimal("0.000001")


class ProviderCapability(StrEnum):
    PLAN = "PLAN"
    VISION = "VISION"
    IMAGE_GENERATION = "IMAGE_GENERATION"
    IMAGE_EDITING = "IMAGE_EDITING"


class ProviderExecutionMode(StrEnum):
    SYNC = "SYNC"
    ASYNC = "ASYNC"


class ProviderDataRetentionMode(StrEnum):
    NONE = "NONE"
    BOUNDED = "BOUNDED"
    UNKNOWN = "UNKNOWN"


class ProviderTrainingUsePolicy(StrEnum):
    PROHIBITED = "PROHIBITED"
    PERMITTED = "PERMITTED"
    UNKNOWN = "UNKNOWN"


class ProviderProtocol(StrEnum):
    OPENAI_IMAGES_JSON = "OPENAI_IMAGES_JSON"
    OPENAI_IMAGES_MULTIPART = "OPENAI_IMAGES_MULTIPART"
    WAN_ASYNC_JSON = "WAN_ASYNC_JSON"
    QWEN_CHAT_JSON = "QWEN_CHAT_JSON"


class ProviderPricingUnit(StrEnum):
    IMAGE = "IMAGE"
    INPUT_TOKEN = "INPUT_TOKEN"
    OUTPUT_TOKEN = "OUTPUT_TOKEN"


class CircuitState(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class ModelRouteRejectionCode(StrEnum):
    NO_CURRENT_CAPABILITY = "NO_CURRENT_CAPABILITY"
    ENDPOINT_DISABLED = "ENDPOINT_DISABLED"
    CAPABILITY_MISMATCH = "CAPABILITY_MISMATCH"
    EXECUTION_MODE_MISMATCH = "EXECUTION_MODE_MISMATCH"
    QUERY_UNSUPPORTED = "QUERY_UNSUPPORTED"
    PROVIDER_NOT_ALLOWED = "PROVIDER_NOT_ALLOWED"
    REGION_NOT_ALLOWED = "REGION_NOT_ALLOWED"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    OBSERVATION_MISSING = "OBSERVATION_MISSING"
    OBSERVATION_STALE = "OBSERVATION_STALE"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    CATEGORY_NOT_ALLOWED = "CATEGORY_NOT_ALLOWED"
    IMAGE_ROLE_NOT_ALLOWED = "IMAGE_ROLE_NOT_ALLOWED"
    FORMAT_UNSUPPORTED = "FORMAT_UNSUPPORTED"
    SIZE_UNSUPPORTED = "SIZE_UNSUPPORTED"
    CANDIDATE_COUNT_EXCEEDED = "CANDIDATE_COUNT_EXCEEDED"
    SAFETY_POLICY_MISMATCH = "SAFETY_POLICY_MISMATCH"
    DATA_REGION_NOT_ALLOWED = "DATA_REGION_NOT_ALLOWED"
    RETENTION_POLICY_UNKNOWN = "RETENTION_POLICY_UNKNOWN"
    RETENTION_EXCEEDED = "RETENTION_EXCEEDED"
    TRAINING_POLICY_UNKNOWN = "TRAINING_POLICY_UNKNOWN"
    TRAINING_USE_NOT_ALLOWED = "TRAINING_USE_NOT_ALLOWED"
    PROTOCOL_MISMATCH = "PROTOCOL_MISMATCH"
    REFERENCE_IMAGES_UNSUPPORTED = "REFERENCE_IMAGES_UNSUPPORTED"
    MASK_UNSUPPORTED = "MASK_UNSUPPORTED"
    SEED_UNSUPPORTED = "SEED_UNSUPPORTED"
    LORA_UNSUPPORTED = "LORA_UNSUPPORTED"
    REQUEST_TOO_LARGE = "REQUEST_TOO_LARGE"
    RESULT_LIMIT_UNSUPPORTED = "RESULT_LIMIT_UNSUPPORTED"


class ModelRouteFailoverCause(StrEnum):
    SAFE_PRE_DISPATCH_FAILURE = "SAFE_PRE_DISPATCH_FAILURE"
    CONFIRMED_RETRYABLE_FAILURE = "CONFIRMED_RETRYABLE_FAILURE"
    CONTENT_REJECTION = "CONTENT_REJECTION"
    POLICY_DENIAL = "POLICY_DENIAL"
    INVALID_INPUT = "INVALID_INPUT"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"


class NoEligibleModelRouteError(DomainError):
    """All endpoint capability versions failed mandatory route filters."""

    def __init__(
        self,
        rejection_counts: tuple[tuple[ModelRouteRejectionCode, int], ...],
    ) -> None:
        super().__init__("no eligible Provider endpoint capability version")
        self.rejection_counts = rejection_counts


def _validate_uuid(value: str, field_name: str) -> str:
    try:
        canonical = canonicalize_uuid(value)
    except ValueError:
        raise ValueError(f"{field_name} must be a canonical UUID") from None
    if canonical != value:
        raise ValueError(f"{field_name} must be a canonical UUID")
    return value


def _validate_token(value: str, field_name: str) -> str:
    if not isinstance(value, str) or _TOKEN_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} is invalid")
    return value


def _validate_host(value: str) -> str:
    if (
        not isinstance(value, str)
        or value != value.lower()
        or len(value) > 253
        or "." not in value
        or any(_HOST_LABEL_PATTERN.fullmatch(label) is None for label in value.split("."))
    ):
        raise ValueError("Provider endpoint host is invalid")
    return value


def _validate_utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must be timezone-aware UTC")
    return value


def _validate_money(value: Decimal, field_name: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise ValueError(f"{field_name} must be Decimal")
    try:
        if not value.is_finite() or value < 0 or value > _MAX_MONEY:
            raise ValueError(f"{field_name} is out of range")
        if value.quantize(_MONEY_QUANTUM) != value:
            raise ValueError(f"{field_name} supports at most six decimal places")
    except InvalidOperation:
        raise ValueError(f"{field_name} is invalid") from None
    return value


def _validate_weight(value: Decimal, field_name: str) -> Decimal:
    _validate_money(value, field_name)
    if value > 1:
        raise ValueError(f"{field_name} must be between zero and one")
    return value


def _validate_permission_set(values: frozenset[str], field_name: str) -> frozenset[str]:
    if not isinstance(values, frozenset) or not values or len(values) > _MAX_PERMISSION_VALUES:
        raise ValueError(f"{field_name} must be a bounded non-empty frozenset")
    for value in values:
        _validate_token(value, field_name)
    return values


def _validate_positive_integer(value: int, field_name: str, *, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
        raise ValueError(f"{field_name} must be between 1 and {maximum}")
    return value


def _canonical_datetime(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canonical_decimal(value: Decimal) -> str:
    return format(value.quantize(_MONEY_QUANTUM), "f")


def _projection_string(data: dict[str, object], key: str) -> str:
    value = data[key]
    if not isinstance(value, str):
        raise TypeError(f"route request projection {key} must be a string")
    return value


def _projection_integer(data: dict[str, object], key: str) -> int:
    value = data[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"route request projection {key} must be an integer")
    return value


def _projection_boolean(data: dict[str, object], key: str) -> bool:
    value = data[key]
    if not isinstance(value, bool):
        raise TypeError(f"route request projection {key} must be a boolean")
    return value


def _projection_strings(data: dict[str, object], key: str) -> tuple[str, ...]:
    value = data[key]
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise TypeError(f"route request projection {key} must be a string list")
    return tuple(value)


def _canonical_sha256(value: dict[str, object]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class ProviderEndpointCapabilityVersion:
    id: str
    provider_id: str
    endpoint_id: str
    version_number: int
    endpoint_host: str
    endpoint_region: str
    model_family: str
    model_id: str
    model_revision: str
    adapter_version: str
    configuration_sha256: str
    capabilities: frozenset[ProviderCapability]
    protocol: ProviderProtocol
    execution_mode: ProviderExecutionMode
    supports_query: bool
    supports_cancel: bool
    supports_provider_idempotency: bool
    allowed_categories: frozenset[str]
    allowed_image_roles: frozenset[ImageRole]
    output_formats: frozenset[str]
    minimum_width: int
    maximum_width: int
    minimum_height: int
    maximum_height: int
    maximum_candidates: int
    safety_policy_version: str
    data_region: str
    data_retention_mode: ProviderDataRetentionMode
    maximum_retention_days: int
    training_use_policy: ProviderTrainingUsePolicy
    secret_reference: str
    maximum_reference_images: int
    supports_mask: bool
    supports_seed: bool
    supports_lora: bool
    maximum_request_bytes: int
    maximum_result_bytes: int
    pricing_unit: ProviderPricingUnit
    enabled: bool
    unit_price: Decimal
    currency: str
    created_at: datetime

    def __post_init__(self) -> None:
        _validate_uuid(self.id, "Provider endpoint capability version id")
        for value, field_name in (
            (self.provider_id, "Provider id"),
            (self.endpoint_id, "Provider endpoint id"),
            (self.endpoint_region, "Provider endpoint region"),
            (self.model_family, "Provider model family"),
            (self.model_id, "Provider model id"),
            (self.model_revision, "Provider model revision"),
            (self.adapter_version, "Provider adapter version"),
        ):
            _validate_token(value, field_name)
        if (
            not isinstance(self.version_number, int)
            or isinstance(self.version_number, bool)
            or not 1 <= self.version_number <= 1_000_000
        ):
            raise ValueError("Provider endpoint capability version must be positive")
        _validate_host(self.endpoint_host)
        if _SHA256_PATTERN.fullmatch(self.configuration_sha256) is None:
            raise ValueError("Provider configuration hash must be lowercase SHA-256")
        if not isinstance(self.capabilities, frozenset) or not self.capabilities:
            raise ValueError("Provider capabilities must be a non-empty frozenset")
        try:
            normalized_capabilities = frozenset(
                ProviderCapability(value) for value in self.capabilities
            )
        except ValueError:
            raise ValueError("Provider capabilities contain an unsupported value") from None
        object.__setattr__(self, "capabilities", normalized_capabilities)
        object.__setattr__(self, "protocol", ProviderProtocol(self.protocol))
        object.__setattr__(
            self,
            "execution_mode",
            ProviderExecutionMode(self.execution_mode),
        )
        for boolean_field_name, boolean_value in (
            ("supports_query", self.supports_query),
            ("supports_cancel", self.supports_cancel),
            ("supports_provider_idempotency", self.supports_provider_idempotency),
        ):
            if not isinstance(boolean_value, bool):
                raise ValueError(f"{boolean_field_name} must be boolean")
        if self.execution_mode is ProviderExecutionMode.SYNC and (
            self.supports_query or self.supports_cancel
        ):
            raise ValueError("synchronous Provider capability cannot query or cancel tasks")
        _validate_permission_set(self.allowed_categories, "allowed_categories")
        if not isinstance(self.allowed_image_roles, frozenset) or not self.allowed_image_roles:
            raise ValueError("allowed_image_roles must be a non-empty frozenset")
        try:
            normalized_roles = frozenset(ImageRole(value) for value in self.allowed_image_roles)
        except ValueError:
            raise ValueError("allowed_image_roles contains an unsupported value") from None
        object.__setattr__(self, "allowed_image_roles", normalized_roles)
        if (
            not isinstance(self.output_formats, frozenset)
            or not self.output_formats
            or len(self.output_formats) > 16
            or any(_MIME_TYPE_PATTERN.fullmatch(value) is None for value in self.output_formats)
        ):
            raise ValueError("Provider output formats are invalid")
        _validate_positive_integer(self.minimum_width, "minimum_width", maximum=16_384)
        _validate_positive_integer(self.maximum_width, "maximum_width", maximum=16_384)
        _validate_positive_integer(self.minimum_height, "minimum_height", maximum=16_384)
        _validate_positive_integer(self.maximum_height, "maximum_height", maximum=16_384)
        if self.minimum_width > self.maximum_width or self.minimum_height > self.maximum_height:
            raise ValueError("Provider dimension bounds are invalid")
        _validate_positive_integer(
            self.maximum_candidates,
            "maximum_candidates",
            maximum=16,
        )
        _validate_token(self.safety_policy_version, "Provider safety policy version")
        _validate_token(self.data_region, "Provider data region")
        object.__setattr__(
            self,
            "data_retention_mode",
            ProviderDataRetentionMode(self.data_retention_mode),
        )
        if (
            not isinstance(self.maximum_retention_days, int)
            or isinstance(self.maximum_retention_days, bool)
            or not 0 <= self.maximum_retention_days <= 3_650
        ):
            raise ValueError("Provider maximum retention days is invalid")
        if (
            self.data_retention_mode is ProviderDataRetentionMode.NONE
            and self.maximum_retention_days != 0
        ):
            raise ValueError("zero-retention Provider capability must retain for zero days")
        if (
            self.data_retention_mode is ProviderDataRetentionMode.BOUNDED
            and self.maximum_retention_days == 0
        ):
            raise ValueError("bounded-retention Provider capability needs a positive day limit")
        object.__setattr__(
            self,
            "training_use_policy",
            ProviderTrainingUsePolicy(self.training_use_policy),
        )
        if _SECRET_REFERENCE_PATTERN.fullmatch(self.secret_reference) is None:
            raise ValueError("Provider Secret Reference is invalid")
        if (
            not isinstance(self.maximum_reference_images, int)
            or isinstance(self.maximum_reference_images, bool)
            or not 0 <= self.maximum_reference_images <= 16
        ):
            raise ValueError("maximum_reference_images is invalid")
        for feature_field_name, feature_value in (
            ("supports_mask", self.supports_mask),
            ("supports_seed", self.supports_seed),
            ("supports_lora", self.supports_lora),
        ):
            if not isinstance(feature_value, bool):
                raise ValueError(f"{feature_field_name} must be boolean")
        if self.supports_mask and (
            ProviderCapability.IMAGE_EDITING not in self.capabilities
            or self.maximum_reference_images < 1
        ):
            raise ValueError("mask support requires image editing and a reference image")
        _validate_positive_integer(
            self.maximum_request_bytes,
            "maximum_request_bytes",
            maximum=1_073_741_824,
        )
        _validate_positive_integer(
            self.maximum_result_bytes,
            "maximum_result_bytes",
            maximum=1_073_741_824,
        )
        object.__setattr__(self, "pricing_unit", ProviderPricingUnit(self.pricing_unit))
        if not isinstance(self.enabled, bool):
            raise ValueError("Provider enabled state must be boolean")
        _validate_money(self.unit_price, "Provider unit price")
        if _CURRENCY_PATTERN.fullmatch(self.currency) is None:
            raise ValueError("Provider currency must be an ISO-style uppercase code")
        _validate_utc(self.created_at, "Provider capability created_at")

    @classmethod
    def create(
        cls,
        *,
        id: str,
        provider_id: str,
        endpoint_id: str,
        version_number: int,
        endpoint_host: str,
        endpoint_region: str,
        model_family: str,
        model_id: str,
        model_revision: str,
        adapter_version: str,
        configuration_sha256: str,
        capabilities: frozenset[ProviderCapability],
        protocol: ProviderProtocol,
        execution_mode: ProviderExecutionMode,
        supports_query: bool,
        supports_cancel: bool,
        supports_provider_idempotency: bool,
        allowed_categories: frozenset[str],
        allowed_image_roles: frozenset[ImageRole],
        output_formats: frozenset[str],
        minimum_width: int,
        maximum_width: int,
        minimum_height: int,
        maximum_height: int,
        maximum_candidates: int,
        safety_policy_version: str,
        data_region: str,
        data_retention_mode: ProviderDataRetentionMode,
        maximum_retention_days: int,
        training_use_policy: ProviderTrainingUsePolicy,
        secret_reference: str,
        maximum_reference_images: int,
        supports_mask: bool,
        supports_seed: bool,
        supports_lora: bool,
        maximum_request_bytes: int,
        maximum_result_bytes: int,
        pricing_unit: ProviderPricingUnit,
        enabled: bool,
        unit_price: Decimal,
        currency: str,
        created_at: datetime,
    ) -> ProviderEndpointCapabilityVersion:
        return cls(
            id=id,
            provider_id=provider_id,
            endpoint_id=endpoint_id,
            version_number=version_number,
            endpoint_host=endpoint_host,
            endpoint_region=endpoint_region,
            model_family=model_family,
            model_id=model_id,
            model_revision=model_revision,
            adapter_version=adapter_version,
            configuration_sha256=configuration_sha256,
            capabilities=capabilities,
            protocol=protocol,
            execution_mode=execution_mode,
            supports_query=supports_query,
            supports_cancel=supports_cancel,
            supports_provider_idempotency=supports_provider_idempotency,
            allowed_categories=allowed_categories,
            allowed_image_roles=allowed_image_roles,
            output_formats=output_formats,
            minimum_width=minimum_width,
            maximum_width=maximum_width,
            minimum_height=minimum_height,
            maximum_height=maximum_height,
            maximum_candidates=maximum_candidates,
            safety_policy_version=safety_policy_version,
            data_region=data_region,
            data_retention_mode=data_retention_mode,
            maximum_retention_days=maximum_retention_days,
            training_use_policy=training_use_policy,
            secret_reference=secret_reference,
            maximum_reference_images=maximum_reference_images,
            supports_mask=supports_mask,
            supports_seed=supports_seed,
            supports_lora=supports_lora,
            maximum_request_bytes=maximum_request_bytes,
            maximum_result_bytes=maximum_result_bytes,
            pricing_unit=pricing_unit,
            enabled=enabled,
            unit_price=unit_price,
            currency=currency,
            created_at=created_at,
        )

    @property
    def capability_sha256(self) -> str:
        return _canonical_sha256(self.to_canonical_data())

    def to_canonical_data(self) -> dict[str, object]:
        return {
            "schema_version": "provider-endpoint-capability.v1",
            "id": self.id,
            "provider_id": self.provider_id,
            "endpoint_id": self.endpoint_id,
            "version_number": self.version_number,
            "endpoint_host": self.endpoint_host,
            "endpoint_region": self.endpoint_region,
            "model_family": self.model_family,
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "adapter_version": self.adapter_version,
            "configuration_sha256": self.configuration_sha256,
            "capabilities": sorted(value.value for value in self.capabilities),
            "protocol": self.protocol.value,
            "execution_mode": self.execution_mode.value,
            "supports_query": self.supports_query,
            "supports_cancel": self.supports_cancel,
            "supports_provider_idempotency": self.supports_provider_idempotency,
            "allowed_categories": sorted(self.allowed_categories),
            "allowed_image_roles": sorted(value.value for value in self.allowed_image_roles),
            "output_formats": sorted(self.output_formats),
            "minimum_width": self.minimum_width,
            "maximum_width": self.maximum_width,
            "minimum_height": self.minimum_height,
            "maximum_height": self.maximum_height,
            "maximum_candidates": self.maximum_candidates,
            "safety_policy_version": self.safety_policy_version,
            "data_region": self.data_region,
            "data_retention_mode": self.data_retention_mode.value,
            "maximum_retention_days": self.maximum_retention_days,
            "training_use_policy": self.training_use_policy.value,
            "secret_reference": self.secret_reference,
            "maximum_reference_images": self.maximum_reference_images,
            "supports_mask": self.supports_mask,
            "supports_seed": self.supports_seed,
            "supports_lora": self.supports_lora,
            "maximum_request_bytes": self.maximum_request_bytes,
            "maximum_result_bytes": self.maximum_result_bytes,
            "pricing_unit": self.pricing_unit.value,
            "enabled": self.enabled,
            "unit_price": _canonical_decimal(self.unit_price),
            "currency": self.currency,
            "created_at": _canonical_datetime(self.created_at),
        }


@dataclass(frozen=True, slots=True)
class ModelRouteRequest:
    workspace_id: str
    workflow_id: str
    creative_plan_version_id: str
    plan_approval_id: str
    required_capability: ProviderCapability
    allowed_providers: frozenset[str]
    allowed_endpoint_regions: frozenset[str]
    maximum_cost: Decimal
    currency: str
    route_policy_version: str
    deadline_at: datetime
    required_execution_mode: ProviderExecutionMode
    requires_query: bool
    required_quota_units: int
    product_category: str
    image_role: ImageRole
    required_output_format: str
    width: int
    height: int
    candidate_count: int
    required_safety_policy_version: str
    allowed_data_regions: frozenset[str]
    maximum_retention_days: int
    allow_training_use: bool
    required_protocol: ProviderProtocol = ProviderProtocol.OPENAI_IMAGES_JSON
    reference_image_count: int = 0
    authorized_asset_version_ids: tuple[str, ...] = ()
    requires_mask: bool = False
    requires_seed: bool = False
    requires_lora: bool = False
    estimated_request_bytes: int = 1
    required_result_byte_limit: int = 1

    def __post_init__(self) -> None:
        validate_workspace_id(self.workspace_id)
        _validate_uuid(self.workflow_id, "Workflow id")
        _validate_uuid(self.creative_plan_version_id, "Creative Plan Version id")
        _validate_uuid(self.plan_approval_id, "Plan Approval id")
        object.__setattr__(
            self,
            "required_capability",
            ProviderCapability(self.required_capability),
        )
        _validate_permission_set(self.allowed_providers, "allowed_providers")
        _validate_permission_set(
            self.allowed_endpoint_regions,
            "allowed_endpoint_regions",
        )
        _validate_money(self.maximum_cost, "Route maximum cost")
        if _CURRENCY_PATTERN.fullmatch(self.currency) is None:
            raise ValueError("Route currency must be an ISO-style uppercase code")
        _validate_token(self.route_policy_version, "Route policy version")
        _validate_utc(self.deadline_at, "Route deadline")
        object.__setattr__(
            self,
            "required_execution_mode",
            ProviderExecutionMode(self.required_execution_mode),
        )
        if not isinstance(self.requires_query, bool):
            raise ValueError("requires_query must be boolean")
        if self.required_execution_mode is ProviderExecutionMode.SYNC and self.requires_query:
            raise ValueError("synchronous route request cannot require task query")
        if (
            not isinstance(self.required_quota_units, int)
            or isinstance(self.required_quota_units, bool)
            or not 1 <= self.required_quota_units <= 1_000_000
        ):
            raise ValueError("required_quota_units must be between 1 and 1000000")
        _validate_token(self.product_category, "Route product category")
        object.__setattr__(self, "image_role", ImageRole(self.image_role))
        if _MIME_TYPE_PATTERN.fullmatch(self.required_output_format) is None:
            raise ValueError("Route output format is invalid")
        _validate_positive_integer(self.width, "Route width", maximum=16_384)
        _validate_positive_integer(self.height, "Route height", maximum=16_384)
        _validate_positive_integer(self.candidate_count, "Route candidate count", maximum=16)
        _validate_token(
            self.required_safety_policy_version,
            "Route safety policy version",
        )
        _validate_permission_set(self.allowed_data_regions, "allowed_data_regions")
        if (
            not isinstance(self.maximum_retention_days, int)
            or isinstance(self.maximum_retention_days, bool)
            or not 0 <= self.maximum_retention_days <= 3_650
        ):
            raise ValueError("Route maximum retention days is invalid")
        if not isinstance(self.allow_training_use, bool):
            raise ValueError("allow_training_use must be boolean")
        object.__setattr__(self, "required_protocol", ProviderProtocol(self.required_protocol))
        if (
            not isinstance(self.reference_image_count, int)
            or isinstance(self.reference_image_count, bool)
            or not 0 <= self.reference_image_count <= 16
        ):
            raise ValueError("reference_image_count is invalid")
        if (
            not isinstance(self.authorized_asset_version_ids, tuple)
            or len(self.authorized_asset_version_ids) != self.reference_image_count
            or len(set(self.authorized_asset_version_ids)) != len(self.authorized_asset_version_ids)
        ):
            raise ValueError(
                "authorized Asset Version identities must match the reference image count"
            )
        for asset_version_id in self.authorized_asset_version_ids:
            _validate_uuid(asset_version_id, "authorized Asset Version id")
        for field_name, value in (
            ("requires_mask", self.requires_mask),
            ("requires_seed", self.requires_seed),
            ("requires_lora", self.requires_lora),
        ):
            if not isinstance(value, bool):
                raise ValueError(f"{field_name} must be boolean")
        if self.requires_mask and (
            self.required_capability is not ProviderCapability.IMAGE_EDITING
            or self.reference_image_count < 1
        ):
            raise ValueError("mask request requires image editing and a reference image")
        _validate_positive_integer(
            self.estimated_request_bytes,
            "estimated_request_bytes",
            maximum=1_073_741_824,
        )
        _validate_positive_integer(
            self.required_result_byte_limit,
            "required_result_byte_limit",
            maximum=1_073_741_824,
        )

    @classmethod
    def from_canonical_data(cls, data: dict[str, object]) -> ModelRouteRequest:
        """Reconstruct and fully revalidate one persisted route-request projection."""

        expected_keys = {
            "schema_version",
            "workspace_id",
            "workflow_id",
            "creative_plan_version_id",
            "plan_approval_id",
            "required_capability",
            "allowed_providers",
            "allowed_endpoint_regions",
            "maximum_cost",
            "currency",
            "route_policy_version",
            "deadline_at",
            "required_execution_mode",
            "requires_query",
            "required_quota_units",
            "product_category",
            "image_role",
            "required_output_format",
            "width",
            "height",
            "candidate_count",
            "required_safety_policy_version",
            "allowed_data_regions",
            "maximum_retention_days",
            "allow_training_use",
            "required_protocol",
            "reference_image_count",
            "authorized_asset_version_ids",
            "requires_mask",
            "requires_seed",
            "requires_lora",
            "estimated_request_bytes",
            "required_result_byte_limit",
        }
        if not isinstance(data, dict) or set(data) != expected_keys:
            raise ValueError("model route request projection fields are invalid")
        try:
            if _projection_string(data, "schema_version") != "model-route-request.v1":
                raise ValueError("model route request projection version is invalid")
            request = cls(
                workspace_id=_projection_string(data, "workspace_id"),
                workflow_id=_projection_string(data, "workflow_id"),
                creative_plan_version_id=_projection_string(data, "creative_plan_version_id"),
                plan_approval_id=_projection_string(data, "plan_approval_id"),
                required_capability=ProviderCapability(
                    _projection_string(data, "required_capability")
                ),
                allowed_providers=frozenset(_projection_strings(data, "allowed_providers")),
                allowed_endpoint_regions=frozenset(
                    _projection_strings(data, "allowed_endpoint_regions")
                ),
                maximum_cost=Decimal(_projection_string(data, "maximum_cost")),
                currency=_projection_string(data, "currency"),
                route_policy_version=_projection_string(data, "route_policy_version"),
                deadline_at=datetime.fromisoformat(_projection_string(data, "deadline_at")),
                required_execution_mode=ProviderExecutionMode(
                    _projection_string(data, "required_execution_mode")
                ),
                requires_query=_projection_boolean(data, "requires_query"),
                required_quota_units=_projection_integer(data, "required_quota_units"),
                product_category=_projection_string(data, "product_category"),
                image_role=ImageRole(_projection_string(data, "image_role")),
                required_output_format=_projection_string(data, "required_output_format"),
                width=_projection_integer(data, "width"),
                height=_projection_integer(data, "height"),
                candidate_count=_projection_integer(data, "candidate_count"),
                required_safety_policy_version=_projection_string(
                    data, "required_safety_policy_version"
                ),
                allowed_data_regions=frozenset(_projection_strings(data, "allowed_data_regions")),
                maximum_retention_days=_projection_integer(data, "maximum_retention_days"),
                allow_training_use=_projection_boolean(data, "allow_training_use"),
                required_protocol=ProviderProtocol(_projection_string(data, "required_protocol")),
                reference_image_count=_projection_integer(data, "reference_image_count"),
                authorized_asset_version_ids=_projection_strings(
                    data, "authorized_asset_version_ids"
                ),
                requires_mask=_projection_boolean(data, "requires_mask"),
                requires_seed=_projection_boolean(data, "requires_seed"),
                requires_lora=_projection_boolean(data, "requires_lora"),
                estimated_request_bytes=_projection_integer(data, "estimated_request_bytes"),
                required_result_byte_limit=_projection_integer(data, "required_result_byte_limit"),
            )
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("model route request projection is invalid") from exc
        if request.canonical_data() != data:
            raise ValueError("model route request projection is not canonical")
        return request

    def canonical_data(self) -> dict[str, object]:
        """Return the credential-free projection needed to prove this exact request."""

        return {
            "schema_version": "model-route-request.v1",
            "workspace_id": self.workspace_id,
            "workflow_id": self.workflow_id,
            "creative_plan_version_id": self.creative_plan_version_id,
            "plan_approval_id": self.plan_approval_id,
            "required_capability": self.required_capability.value,
            "allowed_providers": sorted(self.allowed_providers),
            "allowed_endpoint_regions": sorted(self.allowed_endpoint_regions),
            "maximum_cost": _canonical_decimal(self.maximum_cost),
            "currency": self.currency,
            "route_policy_version": self.route_policy_version,
            "deadline_at": _canonical_datetime(self.deadline_at),
            "required_execution_mode": self.required_execution_mode.value,
            "requires_query": self.requires_query,
            "required_quota_units": self.required_quota_units,
            "product_category": self.product_category,
            "image_role": self.image_role.value,
            "required_output_format": self.required_output_format,
            "width": self.width,
            "height": self.height,
            "candidate_count": self.candidate_count,
            "required_safety_policy_version": self.required_safety_policy_version,
            "allowed_data_regions": sorted(self.allowed_data_regions),
            "maximum_retention_days": self.maximum_retention_days,
            "allow_training_use": self.allow_training_use,
            "required_protocol": self.required_protocol.value,
            "reference_image_count": self.reference_image_count,
            "authorized_asset_version_ids": list(self.authorized_asset_version_ids),
            "requires_mask": self.requires_mask,
            "requires_seed": self.requires_seed,
            "requires_lora": self.requires_lora,
            "estimated_request_bytes": self.estimated_request_bytes,
            "required_result_byte_limit": self.required_result_byte_limit,
        }

    @property
    def request_sha256(self) -> str:
        return _canonical_sha256(self.canonical_data())


@dataclass(frozen=True, slots=True)
class ModelRoutePolicy:
    version: str
    quality_weight: Decimal
    availability_weight: Decimal
    latency_weight: Decimal
    quota_weight: Decimal
    price_weight: Decimal
    maximum_observation_age_seconds: int = 60

    def __post_init__(self) -> None:
        _validate_token(self.version, "Route policy version")
        weights = (
            self.quality_weight,
            self.availability_weight,
            self.latency_weight,
            self.quota_weight,
            self.price_weight,
        )
        for field_name, value in zip(
            (
                "quality_weight",
                "availability_weight",
                "latency_weight",
                "quota_weight",
                "price_weight",
            ),
            weights,
            strict=True,
        ):
            _validate_weight(value, field_name)
        if sum(weights, start=Decimal("0")) != Decimal("1"):
            raise ValueError("Route policy weights must sum to one")
        _validate_positive_integer(
            self.maximum_observation_age_seconds,
            "maximum_observation_age_seconds",
            maximum=86_400,
        )


@dataclass(frozen=True, slots=True)
class EndpointRouteObservation:
    endpoint_capability_version_id: str
    quality_score: Decimal
    availability_score: Decimal
    latency_score: Decimal
    quota_score: Decimal
    circuit_state: CircuitState
    remaining_quota_units: int
    observed_at: datetime

    def __post_init__(self) -> None:
        _validate_uuid(
            self.endpoint_capability_version_id,
            "observed endpoint capability version id",
        )
        for field_name, value in (
            ("quality_score", self.quality_score),
            ("availability_score", self.availability_score),
            ("latency_score", self.latency_score),
            ("quota_score", self.quota_score),
        ):
            _validate_weight(value, field_name)
        object.__setattr__(self, "circuit_state", CircuitState(self.circuit_state))
        if (
            not isinstance(self.remaining_quota_units, int)
            or isinstance(self.remaining_quota_units, bool)
            or not 0 <= self.remaining_quota_units <= 1_000_000_000
        ):
            raise ValueError("remaining_quota_units is invalid")
        _validate_utc(self.observed_at, "Route observation time")


@dataclass(frozen=True, slots=True)
class ModelRouteCandidateScore:
    endpoint_capability_version_id: str
    score: Decimal

    def __post_init__(self) -> None:
        _validate_uuid(
            self.endpoint_capability_version_id,
            "scored endpoint capability version id",
        )
        _validate_weight(self.score, "Route candidate score")
        if self.score.quantize(_MONEY_QUANTUM) != self.score:
            raise ValueError("Route candidate score supports at most six decimal places")


@dataclass(frozen=True, slots=True)
class ModelRouteDecision:
    endpoint_capability_version_id: str
    fallback_endpoint_capability_version_ids: tuple[str, ...]
    route_policy_version: str
    request_sha256: str
    candidate_scores: tuple[ModelRouteCandidateScore, ...]
    rejection_counts: tuple[tuple[ModelRouteRejectionCode, int], ...]
    decided_at: datetime

    def __post_init__(self) -> None:
        _validate_uuid(
            self.endpoint_capability_version_id,
            "selected endpoint capability version id",
        )
        if (
            not isinstance(self.fallback_endpoint_capability_version_ids, tuple)
            or len(self.fallback_endpoint_capability_version_ids) >= _MAX_ENDPOINTS
        ):
            raise ValueError("fallback endpoint capability versions are invalid")
        for value in self.fallback_endpoint_capability_version_ids:
            _validate_uuid(value, "fallback endpoint capability version id")
        if len(set(self.fallback_endpoint_capability_version_ids)) != len(
            self.fallback_endpoint_capability_version_ids
        ):
            raise ValueError("fallback endpoint capability versions contain duplicates")
        if self.endpoint_capability_version_id in self.fallback_endpoint_capability_version_ids:
            raise ValueError("selected endpoint cannot also be a fallback endpoint")
        _validate_token(self.route_policy_version, "Route policy version")
        if _SHA256_PATTERN.fullmatch(self.request_sha256) is None:
            raise ValueError("Route request hash must be lowercase SHA-256")
        if (
            not isinstance(self.candidate_scores, tuple)
            or not self.candidate_scores
            or any(not isinstance(item, ModelRouteCandidateScore) for item in self.candidate_scores)
        ):
            raise ValueError("Route candidate scores are invalid")
        score_ids = tuple(item.endpoint_capability_version_id for item in self.candidate_scores)
        if score_ids != (
            self.endpoint_capability_version_id,
            *self.fallback_endpoint_capability_version_ids,
        ):
            raise ValueError("Route candidate score order does not match selected endpoints")
        if not isinstance(self.rejection_counts, tuple):
            raise ValueError("Route rejection counts must be a tuple")
        normalized_rejections = tuple(sorted(self.rejection_counts, key=lambda item: item[0].value))
        if normalized_rejections != self.rejection_counts:
            raise ValueError("Route rejection counts must be stably ordered")
        if any(
            not isinstance(code, ModelRouteRejectionCode)
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count < 1
            for code, count in self.rejection_counts
        ):
            raise ValueError("Route rejection counts contain an invalid value")
        if len({code for code, _ in self.rejection_counts}) != len(self.rejection_counts):
            raise ValueError("Route rejection counts contain duplicate reasons")
        _validate_utc(self.decided_at, "Route decision time")

    @property
    def decision_sha256(self) -> str:
        return _canonical_sha256(
            {
                "schema_version": "model-route-decision.v1",
                "endpoint_capability_version_id": self.endpoint_capability_version_id,
                "fallback_endpoint_capability_version_ids": list(
                    self.fallback_endpoint_capability_version_ids
                ),
                "route_policy_version": self.route_policy_version,
                "request_sha256": self.request_sha256,
                "candidate_scores": [
                    {
                        "endpoint_capability_version_id": item.endpoint_capability_version_id,
                        "score": _canonical_decimal(item.score),
                    }
                    for item in self.candidate_scores
                ],
                "rejection_counts": [
                    {"code": code.value, "count": count} for code, count in self.rejection_counts
                ],
                "decided_at": _canonical_datetime(self.decided_at),
            }
        )

    def next_fallback(
        self,
        *,
        cause: ModelRouteFailoverCause,
        attempted_endpoint_capability_version_ids: tuple[str, ...],
    ) -> str | None:
        normalized_cause = ModelRouteFailoverCause(cause)
        route = (
            self.endpoint_capability_version_id,
            *self.fallback_endpoint_capability_version_ids,
        )
        if (
            not isinstance(attempted_endpoint_capability_version_ids, tuple)
            or not attempted_endpoint_capability_version_ids
            or attempted_endpoint_capability_version_ids
            != route[: len(attempted_endpoint_capability_version_ids)]
        ):
            raise ValueError("attempted endpoint capability versions are invalid")
        if normalized_cause not in {
            ModelRouteFailoverCause.SAFE_PRE_DISPATCH_FAILURE,
            ModelRouteFailoverCause.CONFIRMED_RETRYABLE_FAILURE,
        }:
            return None
        attempted = set(attempted_endpoint_capability_version_ids)
        return next((endpoint_id for endpoint_id in route if endpoint_id not in attempted), None)
