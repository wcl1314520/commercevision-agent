"""Immutable Provider call and usage evidence for image execution."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from enum import StrEnum

from commercevision_domain.ids import canonicalize_uuid
from commercevision_domain.provider_routing import ProviderPricingUnit
from commercevision_domain.workspace_identity import validate_workspace_id

_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$", re.ASCII)
_DECIMAL_QUANTUM = Decimal("0.000001")
_MAX_DECIMAL = Decimal("99999999999999.999999")


class ProviderCallOutcome(StrEnum):
    CONFIRMED_SUCCESS = "CONFIRMED_SUCCESS"
    CONFIRMED_FAILURE = "CONFIRMED_FAILURE"
    CONTENT_REJECTED = "CONTENT_REJECTED"
    SAFE_TO_RETRY_PRE_DISPATCH = "SAFE_TO_RETRY_PRE_DISPATCH"
    UNKNOWN_AFTER_POSSIBLE_DISPATCH = "UNKNOWN_AFTER_POSSIBLE_DISPATCH"


class UsageResolutionStatus(StrEnum):
    UNRESOLVED = "UNRESOLVED"
    FINALIZED = "FINALIZED"


class UsageEvidenceSource(StrEnum):
    DIRECT_RESPONSE = "DIRECT_RESPONSE"
    PROVIDER_RECONCILIATION = "PROVIDER_RECONCILIATION"
    OPERATOR_RECONCILIATION = "OPERATOR_RECONCILIATION"


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


def _validate_sha256(value: str, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be lowercase SHA-256")
    return value


def _validate_utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must be timezone-aware UTC")
    return value


def _validate_decimal(
    value: Decimal,
    field_name: str,
    *,
    positive: bool = False,
) -> Decimal:
    if not isinstance(value, Decimal):
        raise ValueError(f"{field_name} must be Decimal")
    try:
        if (
            not value.is_finite()
            or value < 0
            or value > _MAX_DECIMAL
            or value.quantize(_DECIMAL_QUANTUM) != value
            or (positive and value == 0)
        ):
            raise ValueError(f"{field_name} is out of range")
    except InvalidOperation:
        raise ValueError(f"{field_name} is invalid") from None
    return value


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
class ProviderCall:
    id: str
    workspace_id: str
    candidate_slot_id: str
    durable_operation_id: str
    operation_attempt: int
    call_index: int
    route_decision_id: str
    endpoint_capability_version_id: str
    provider: str
    model: str
    request_sha256: str
    idempotency_key_sha256: str
    outcome: ProviderCallOutcome
    possible_dispatch: bool
    provider_request_id_sha256: str | None
    latency_ms: int
    observed_at: datetime

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.id, "Provider Call id"),
            (self.candidate_slot_id, "Candidate Slot id"),
            (self.durable_operation_id, "Durable Operation id"),
            (self.route_decision_id, "Model Route Decision id"),
            (self.endpoint_capability_version_id, "Endpoint Capability Version id"),
        ):
            _validate_uuid(value, field_name)
        validate_workspace_id(self.workspace_id)
        if (
            not isinstance(self.operation_attempt, int)
            or isinstance(self.operation_attempt, bool)
            or self.operation_attempt < 1
        ):
            raise ValueError("Provider Call operation attempt must be positive")
        if (
            not isinstance(self.call_index, int)
            or isinstance(self.call_index, bool)
            or not 0 <= self.call_index < 8
        ):
            raise ValueError("Provider Call index must be between 0 and 7")
        _validate_token(self.provider, "Provider")
        _validate_token(self.model, "Provider model")
        _validate_sha256(self.request_sha256, "Provider request payload hash")
        _validate_sha256(self.idempotency_key_sha256, "Provider idempotency key hash")
        object.__setattr__(self, "outcome", ProviderCallOutcome(self.outcome))
        if not isinstance(self.possible_dispatch, bool):
            raise ValueError("Provider Call possible_dispatch must be boolean")
        if self.provider_request_id_sha256 is not None:
            _validate_sha256(self.provider_request_id_sha256, "Provider Request id hash")
        if (
            not isinstance(self.latency_ms, int)
            or isinstance(self.latency_ms, bool)
            or not 0 <= self.latency_ms <= 86_400_000
        ):
            raise ValueError("Provider Call latency is invalid")
        _validate_utc(self.observed_at, "Provider Call observed_at")
        self._validate_outcome_facts()

    def _validate_outcome_facts(self) -> None:
        if (
            self.outcome is ProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH
            and not self.possible_dispatch
        ):
            raise ValueError("unknown outcome requires possible dispatch")
        if self.outcome is ProviderCallOutcome.SAFE_TO_RETRY_PRE_DISPATCH:
            if self.possible_dispatch:
                raise ValueError("pre-dispatch retry cannot record possible dispatch")
            if self.provider_request_id_sha256 is not None:
                raise ValueError("pre-dispatch retry cannot record a Provider request identity")
        elif not self.possible_dispatch:
            raise ValueError("dispatched Provider Call outcome requires possible dispatch")

    @property
    def call_identity_sha256(self) -> str:
        return _canonical_sha256(
            {
                "schema_version": "provider-call-identity.v1",
                "workspace_id": self.workspace_id,
                "candidate_slot_id": self.candidate_slot_id,
                "durable_operation_id": self.durable_operation_id,
                "operation_attempt": self.operation_attempt,
                "call_index": self.call_index,
            }
        )

    @property
    def must_reconcile(self) -> bool:
        return self.outcome is ProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH

    @property
    def is_automatic_resubmission_safe(self) -> bool:
        return self.outcome is ProviderCallOutcome.SAFE_TO_RETRY_PRE_DISPATCH


@dataclass(frozen=True, slots=True)
class UsageRecord:
    id: str
    workspace_id: str
    provider_call_id: str
    provider_call_identity_sha256: str
    durable_operation_id: str
    operation_attempt: int
    provider: str
    model: str
    endpoint_capability_version_id: str
    pricing_unit: ProviderPricingUnit
    estimated_quantity: Decimal
    provider_reported_quantity: Decimal | None
    configured_unit_price: Decimal
    estimated_amount: Decimal
    actual_amount: Decimal | None
    currency: str
    unit_price_version: str
    provider_usage_evidence_sha256: str | None
    pricing_evidence_sha256: str
    final_cost_evidence_sha256: str | None
    resolution_status: UsageResolutionStatus
    evidence_source: UsageEvidenceSource
    latency_ms: int
    recorded_at: datetime

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.id, "Usage Record id"),
            (self.provider_call_id, "Provider Call id"),
            (self.durable_operation_id, "Durable Operation id"),
            (self.endpoint_capability_version_id, "Endpoint Capability Version id"),
        ):
            _validate_uuid(value, field_name)
        validate_workspace_id(self.workspace_id)
        _validate_sha256(self.provider_call_identity_sha256, "Provider Call identity")
        if (
            not isinstance(self.operation_attempt, int)
            or isinstance(self.operation_attempt, bool)
            or self.operation_attempt < 1
        ):
            raise ValueError("Usage operation attempt must be positive")
        _validate_token(self.provider, "Usage Provider")
        _validate_token(self.model, "Usage model")
        object.__setattr__(self, "pricing_unit", ProviderPricingUnit(self.pricing_unit))
        _validate_decimal(self.estimated_quantity, "estimated quantity", positive=True)
        if self.provider_reported_quantity is not None:
            _validate_decimal(
                self.provider_reported_quantity,
                "Provider-reported quantity",
                positive=True,
            )
        _validate_decimal(self.configured_unit_price, "configured unit price")
        _validate_decimal(self.estimated_amount, "estimated amount")
        try:
            with localcontext() as context:
                context.prec = 48
                expected_estimate = (self.estimated_quantity * self.configured_unit_price).quantize(
                    _DECIMAL_QUANTUM
                )
        except InvalidOperation:
            raise ValueError("configured price estimate is out of range") from None
        if expected_estimate > _MAX_DECIMAL or self.estimated_amount != expected_estimate:
            raise ValueError("estimated amount must match configured price evidence")
        if self.actual_amount is not None:
            _validate_decimal(self.actual_amount, "actual amount")
        if not isinstance(self.currency, str) or _CURRENCY_PATTERN.fullmatch(self.currency) is None:
            raise ValueError("Usage currency must be an uppercase ISO-style code")
        _validate_token(self.unit_price_version, "unit price version")
        if self.provider_usage_evidence_sha256 is not None:
            _validate_sha256(
                self.provider_usage_evidence_sha256,
                "Provider usage evidence hash",
            )
        _validate_sha256(self.pricing_evidence_sha256, "pricing evidence hash")
        if self.final_cost_evidence_sha256 is not None:
            _validate_sha256(self.final_cost_evidence_sha256, "final cost evidence hash")
        object.__setattr__(
            self,
            "resolution_status",
            UsageResolutionStatus(self.resolution_status),
        )
        object.__setattr__(self, "evidence_source", UsageEvidenceSource(self.evidence_source))
        if (
            not isinstance(self.latency_ms, int)
            or isinstance(self.latency_ms, bool)
            or not 0 <= self.latency_ms <= 86_400_000
        ):
            raise ValueError("Usage latency is invalid")
        _validate_utc(self.recorded_at, "Usage recorded_at")
        self._validate_resolution_evidence()

    def _validate_resolution_evidence(self) -> None:
        provider_evidence = (
            self.provider_reported_quantity,
            self.provider_usage_evidence_sha256,
        )
        if any(value is None for value in provider_evidence) and any(
            value is not None for value in provider_evidence
        ):
            raise ValueError("Provider usage quantity and evidence must be recorded together")
        if self.resolution_status is UsageResolutionStatus.UNRESOLVED:
            if self.actual_amount is not None or self.final_cost_evidence_sha256 is not None:
                raise ValueError("unresolved usage cannot record actual or final cost evidence")
            return
        if any(value is None for value in provider_evidence):
            raise ValueError("finalized usage requires Provider-reported evidence")
        if self.actual_amount is None or self.final_cost_evidence_sha256 is None:
            raise ValueError("finalized usage requires actual and final cost evidence")

    @property
    def deduplication_key(self) -> str:
        return f"usage:{self.provider_call_identity_sha256}"

    @property
    def is_budget_releasable(self) -> bool:
        return self.resolution_status is UsageResolutionStatus.FINALIZED
