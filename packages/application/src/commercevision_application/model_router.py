"""Transactional application command for deterministic model routing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from commercevision_domain import (
    ConcurrencyError,
    ModelRouteCandidateScore,
    ModelRouteDecision,
    ModelRouteRejectionCode,
    ModelRouteRequest,
    ProviderPricingUnit,
    select_model_route,
)
from commercevision_domain.workflow.errors import IdempotencyConflictError

from .asset_idempotency import canonical_hash, key_hash, workspace_hash
from .asset_ports import IdempotencyRecordPort
from .model_router_ports import (
    ModelRouteDecisionWrite,
    ModelRouterUnitOfWorkFactory,
    ModelRouterUnitOfWorkPort,
    PersistedModelRouteDecision,
)

_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)
_TRACE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$", re.ASCII)
_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$", re.ASCII)
_MONEY_QUANTUM = Decimal("0.000001")
_MAX_MONEY = Decimal("99999999999999.999999")
_IDEMPOTENCY_TTL = timedelta(days=30)
_AUDIT_TTL = timedelta(days=3650)


@dataclass(frozen=True, slots=True)
class ModelRouteDecisionResult:
    policy_version_id: str
    decision: ModelRouteDecision
    estimated_cost: Decimal
    currency: str
    replayed: bool


class ModelRouterApplicationService:
    def __init__(self, uow_factory: ModelRouterUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def route(
        self,
        *,
        request: ModelRouteRequest,
        policy_key: str,
        actor_id: str,
        idempotency_key: str,
        trace_id: str,
    ) -> ModelRouteDecisionResult:
        if not isinstance(request, ModelRouteRequest):
            raise ValueError("Route request is invalid")
        _validate_token(policy_key, "Route policy key")
        _validate_token(actor_id, "Route actor")
        if not isinstance(trace_id, str) or _TRACE_PATTERN.fullmatch(trace_id) is None:
            raise ValueError("Route trace is invalid")
        scope = (
            f"model-route:{workspace_hash(request.workspace_id)}:"
            f"{request.workflow_id}:{request.creative_plan_version_id}"
        )
        request_hash = canonical_hash(
            {
                "route_request_sha256": request.request_sha256,
                "policy_key": policy_key,
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
                result = _result_from_data(replay.response_data, replayed=True)
                if (
                    replay.resource_type != "model-route-decision"
                    or replay.resource_id != result.decision.decision_sha256
                ):
                    raise ConcurrencyError(
                        "idempotency record does not match its model route decision"
                    )
                persisted = uow.route_authority.get_decision(
                    workspace_id=request.workspace_id,
                    decision_sha256=result.decision.decision_sha256,
                )
                if persisted is None or persisted != _persisted_result(result):
                    raise ConcurrencyError(
                        "persisted model route decision does not match its idempotency record"
                    )
                return _application_result(persisted, replayed=True)
            authority = uow.route_authority.load_current_authority(
                request=request,
                policy_key=policy_key,
            )
            decision = select_model_route(
                request=request,
                capabilities=authority.capabilities,
                policy=authority.policy,
                observations=authority.observations,
                now=now,
            )
            selected_capability = next(
                item
                for item in authority.capabilities
                if item.id == decision.endpoint_capability_version_id
            )
            estimated_cost = selected_capability.unit_price * Decimal(
                request.candidate_count
                if selected_capability.pricing_unit is ProviderPricingUnit.IMAGE
                else request.required_quota_units
            )
            result = ModelRouteDecisionResult(
                policy_version_id=authority.policy_version_id,
                decision=decision,
                estimated_cost=estimated_cost,
                currency=selected_capability.currency,
                replayed=False,
            )
            uow.route_authority.add_decision(
                ModelRouteDecisionWrite(
                    workspace_id=request.workspace_id,
                    request=request,
                    policy_key=policy_key,
                    policy_version_id=result.policy_version_id,
                    decision=decision,
                    idempotency_scope_sha256=canonical_hash(
                        {"model_route_idempotency_scope": scope}
                    ),
                    idempotency_key_sha256=key_hash(idempotency_key),
                    estimated_cost=estimated_cost,
                    currency=selected_capability.currency,
                    created_by=actor_id,
                )
            )
            response_data = _result_data(result)
            uow.idempotency.complete(
                scope=scope,
                key_hash=key_hash(idempotency_key),
                request_hash=request_hash,
                resource_type="model-route-decision",
                resource_id=decision.decision_sha256,
                response_data=response_data,
            )
            uow.audit.add(
                workspace_id=request.workspace_id,
                actor_type="SERVICE",
                actor_id=actor_id,
                action="model-route.decided",
                resource_type="model-route-decision",
                resource_id=decision.decision_sha256,
                trace_id=trace_id,
                metadata={
                    "workflow_id": request.workflow_id,
                    "creative_plan_version_id": request.creative_plan_version_id,
                    "plan_approval_id": request.plan_approval_id,
                    "policy_version_id": result.policy_version_id,
                    "route_policy_version": decision.route_policy_version,
                    "route_request_sha256": decision.request_sha256,
                    "endpoint_capability_version_id": decision.endpoint_capability_version_id,
                    "estimated_cost": str(estimated_cost),
                    "currency": selected_capability.currency,
                    "fallback_count": len(decision.fallback_endpoint_capability_version_ids),
                    "rejection_counts": {
                        code.value: count for code, count in decision.rejection_counts
                    },
                },
                created_at=now,
                expires_at=now + _AUDIT_TTL,
            )
            uow.commit()
            return result

    @staticmethod
    def _claim(
        *,
        uow: ModelRouterUnitOfWorkPort,
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
                "idempotency key was already used with a different model route request"
            )
        if record.status == "COMPLETED":
            return record
        if record.status != "PENDING":
            raise ConcurrencyError("idempotency record has an unsupported status")
        return None


def _result_data(result: ModelRouteDecisionResult) -> dict[str, object]:
    decision = result.decision
    return {
        "policy_version_id": result.policy_version_id,
        "estimated_cost": str(result.estimated_cost),
        "currency": result.currency,
        "decision": {
            "endpoint_capability_version_id": decision.endpoint_capability_version_id,
            "fallback_endpoint_capability_version_ids": list(
                decision.fallback_endpoint_capability_version_ids
            ),
            "route_policy_version": decision.route_policy_version,
            "request_sha256": decision.request_sha256,
            "candidate_scores": [
                {
                    "endpoint_capability_version_id": item.endpoint_capability_version_id,
                    "score": str(item.score),
                }
                for item in decision.candidate_scores
            ],
            "rejection_counts": [
                {"code": code.value, "count": count} for code, count in decision.rejection_counts
            ],
            "decided_at": decision.decided_at.isoformat(),
            "decision_sha256": decision.decision_sha256,
        },
    }


def _persisted_result(result: ModelRouteDecisionResult) -> PersistedModelRouteDecision:
    return PersistedModelRouteDecision(
        policy_version_id=result.policy_version_id,
        decision=result.decision,
        estimated_cost=result.estimated_cost,
        currency=result.currency,
    )


def _application_result(
    persisted: PersistedModelRouteDecision,
    *,
    replayed: bool,
) -> ModelRouteDecisionResult:
    return ModelRouteDecisionResult(
        policy_version_id=persisted.policy_version_id,
        decision=persisted.decision,
        estimated_cost=persisted.estimated_cost,
        currency=persisted.currency,
        replayed=replayed,
    )


def _result_from_data(
    data: dict[str, object] | None,
    *,
    replayed: bool,
) -> ModelRouteDecisionResult:
    if not isinstance(data, dict) or not isinstance(data.get("decision"), dict):
        raise ConcurrencyError("idempotency record lacks a model route decision")
    value = data["decision"]
    try:
        assert isinstance(value, dict)
        candidate_scores_data = value["candidate_scores"]
        rejection_counts_data = value["rejection_counts"]
        if not isinstance(candidate_scores_data, list) or not isinstance(
            rejection_counts_data, list
        ):
            raise TypeError
        decision = ModelRouteDecision(
            endpoint_capability_version_id=_require_str(value["endpoint_capability_version_id"]),
            fallback_endpoint_capability_version_ids=tuple(
                _require_str(item)
                for item in _require_list(value["fallback_endpoint_capability_version_ids"])
            ),
            route_policy_version=_require_str(value["route_policy_version"]),
            request_sha256=_require_str(value["request_sha256"]),
            candidate_scores=tuple(
                ModelRouteCandidateScore(
                    endpoint_capability_version_id=_require_str(
                        _require_dict(item)["endpoint_capability_version_id"]
                    ),
                    score=Decimal(_require_str(_require_dict(item)["score"])),
                )
                for item in candidate_scores_data
            ),
            rejection_counts=tuple(
                (
                    ModelRouteRejectionCode(_require_str(_require_dict(item)["code"])),
                    _require_int(_require_dict(item)["count"]),
                )
                for item in rejection_counts_data
            ),
            decided_at=_require_utc_datetime(value["decided_at"]),
        )
        if decision.decision_sha256 != _require_str(value["decision_sha256"]):
            raise ValueError
        return ModelRouteDecisionResult(
            policy_version_id=_require_uuid(data["policy_version_id"]),
            decision=decision,
            estimated_cost=_require_money(data["estimated_cost"]),
            currency=_require_currency(data["currency"]),
            replayed=replayed,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConcurrencyError("idempotency model route decision is invalid") from exc


def _validate_token(value: str, field_name: str) -> str:
    if not isinstance(value, str) or _TOKEN_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} is invalid")
    return value


def _require_str(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError
    return value


def _require_int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError
    return value


def _require_list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise TypeError
    return value


def _require_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError
    return value


def _require_uuid(value: object) -> str:
    parsed = _require_str(value)
    if str(UUID(parsed)) != parsed:
        raise ValueError
    return parsed


def _require_utc_datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(_require_str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError
    return parsed.astimezone(UTC)


def _require_money(value: object) -> Decimal:
    parsed = Decimal(_require_str(value))
    if (
        not parsed.is_finite()
        or not Decimal("0") <= parsed <= _MAX_MONEY
        or parsed.quantize(_MONEY_QUANTUM) != parsed
    ):
        raise ValueError
    return parsed


def _require_currency(value: object) -> str:
    parsed = _require_str(value)
    if _CURRENCY_PATTERN.fullmatch(parsed) is None:
        raise ValueError
    return parsed
