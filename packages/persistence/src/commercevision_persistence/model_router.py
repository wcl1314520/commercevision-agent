"""MySQL authority and atomic persistence for deterministic model routing."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import TracebackType

from commercevision_application.model_router_ports import (
    ModelRouteDecisionWrite,
    ModelRoutingAuthoritySnapshot,
    PersistedModelRouteDecision,
)
from commercevision_domain import (
    CircuitState,
    ConcurrencyError,
    EndpointRouteObservation,
    ModelRouteCandidateScore,
    ModelRouteDecision,
    ModelRouteRejectionCode,
    ModelRouteRequest,
    NotFoundError,
)
from sqlalchemy import and_, exists, literal_column, or_, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, aliased, sessionmaker

from .database import enter_unit_of_work, exit_unit_of_work
from .integrity import classify_database_error
from .model_router_models import ModelRouteDecisionModel
from .provider_control_plane import capability_from_model, route_policy_version_from_model
from .provider_control_plane_models import (
    ModelRoutePolicyHeadModel,
    ModelRoutePolicyVersionModel,
    ProviderEndpointCapabilityHeadModel,
    ProviderEndpointCapabilityVersionModel,
    ProviderEndpointObservationModel,
    ProviderIdentityModel,
)
from .repositories import AuditRepository, IdempotencyRepository

_MAX_ROUTE_CAPABILITIES = 128


class ModelRouteAuthorityRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def load_current_authority(
        self,
        *,
        request: ModelRouteRequest,
        policy_key: str,
    ) -> ModelRoutingAuthoritySnapshot:
        policy_head = self.session.scalar(
            select(ModelRoutePolicyHeadModel)
            .where(
                ModelRoutePolicyHeadModel.workspace_id == request.workspace_id,
                ModelRoutePolicyHeadModel.policy_key == policy_key,
            )
            .with_for_update()
        )
        if policy_head is None or policy_head.current_version_id is None:
            raise NotFoundError("Current model route policy was not found")
        policy_model = self.session.scalar(
            select(ModelRoutePolicyVersionModel).where(
                ModelRoutePolicyVersionModel.workspace_id == request.workspace_id,
                ModelRoutePolicyVersionModel.policy_key == policy_key,
                ModelRoutePolicyVersionModel.id == policy_head.current_version_id,
            )
        )
        if policy_model is None:
            raise ConcurrencyError("Current model route policy pointer is invalid")
        policy_version = route_policy_version_from_model(policy_model)
        if policy_version.policy.version != request.route_policy_version:
            raise ConcurrencyError("Current model route policy version changed")

        capability_models = tuple(
            self.session.scalars(
                select(ProviderEndpointCapabilityVersionModel)
                .join(
                    ProviderEndpointCapabilityHeadModel,
                    and_(
                        ProviderEndpointCapabilityHeadModel.provider_id
                        == ProviderEndpointCapabilityVersionModel.provider_id,
                        ProviderEndpointCapabilityHeadModel.endpoint_id
                        == ProviderEndpointCapabilityVersionModel.endpoint_id,
                        ProviderEndpointCapabilityHeadModel.current_version_id
                        == ProviderEndpointCapabilityVersionModel.id,
                    ),
                )
                .join(
                    ProviderIdentityModel,
                    ProviderIdentityModel.id == ProviderEndpointCapabilityVersionModel.provider_id,
                )
                .where(
                    ProviderIdentityModel.enabled.is_(True),
                    ProviderEndpointCapabilityVersionModel.provider_id.in_(
                        request.allowed_providers
                    ),
                )
                .order_by(ProviderEndpointCapabilityVersionModel.id)
                .limit(_MAX_ROUTE_CAPABILITIES + 1)
                .with_for_update()
            )
        )
        if len(capability_models) > _MAX_ROUTE_CAPABILITIES:
            raise ConcurrencyError("Current model route capability set exceeds its bound")
        capabilities = tuple(capability_from_model(model) for model in capability_models)
        observations = self._latest_observations(
            workspace_id=request.workspace_id,
            capability_version_ids=tuple(item.id for item in capabilities),
        )
        return ModelRoutingAuthoritySnapshot(
            policy_version_id=policy_version.id,
            policy=policy_version.policy,
            capabilities=capabilities,
            observations=observations,
        )

    def _latest_observations(
        self,
        *,
        workspace_id: str,
        capability_version_ids: tuple[str, ...],
    ) -> tuple[EndpointRouteObservation, ...]:
        if not capability_version_ids:
            return ()
        newer = aliased(ProviderEndpointObservationModel)
        models = tuple(
            self.session.scalars(
                select(ProviderEndpointObservationModel)
                .where(
                    ProviderEndpointObservationModel.workspace_id == workspace_id,
                    ProviderEndpointObservationModel.endpoint_capability_version_id.in_(
                        capability_version_ids
                    ),
                    ~exists(
                        select(1).where(
                            newer.workspace_id == ProviderEndpointObservationModel.workspace_id,
                            newer.endpoint_capability_version_id
                            == ProviderEndpointObservationModel.endpoint_capability_version_id,
                            or_(
                                newer.observed_at > ProviderEndpointObservationModel.observed_at,
                                and_(
                                    newer.observed_at
                                    == ProviderEndpointObservationModel.observed_at,
                                    newer.id > ProviderEndpointObservationModel.id,
                                ),
                            ),
                        )
                    ),
                )
                .order_by(ProviderEndpointObservationModel.endpoint_capability_version_id)
                .with_for_update()
            )
        )
        return tuple(
            EndpointRouteObservation(
                endpoint_capability_version_id=model.endpoint_capability_version_id,
                quality_score=model.quality_score,
                availability_score=model.availability_score,
                latency_score=model.latency_score,
                quota_score=model.quota_score,
                circuit_state=CircuitState(model.circuit_state),
                remaining_quota_units=model.remaining_quota_units,
                observed_at=model.observed_at,
            )
            for model in models
        )

    def add_decision(self, record: ModelRouteDecisionWrite) -> None:
        decision = record.decision
        request = record.request
        self.session.add(
            ModelRouteDecisionModel(
                workspace_id=record.workspace_id,
                decision_sha256=decision.decision_sha256,
                idempotency_scope_sha256=record.idempotency_scope_sha256,
                idempotency_key_sha256=record.idempotency_key_sha256,
                workflow_id=request.workflow_id,
                creative_plan_version_id=request.creative_plan_version_id,
                plan_approval_id=request.plan_approval_id,
                route_request_sha256=decision.request_sha256,
                policy_key=record.policy_key,
                policy_version_id=record.policy_version_id,
                route_policy_version=decision.route_policy_version,
                endpoint_capability_version_id=decision.endpoint_capability_version_id,
                fallback_endpoint_capability_version_ids_json=list(
                    decision.fallback_endpoint_capability_version_ids
                ),
                candidate_scores_json=[
                    {
                        "endpoint_capability_version_id": item.endpoint_capability_version_id,
                        "score": str(item.score),
                    }
                    for item in decision.candidate_scores
                ],
                rejection_counts_json=[
                    {"code": code.value, "count": count}
                    for code, count in decision.rejection_counts
                ],
                estimated_cost=record.estimated_cost,
                currency=record.currency,
                decided_at=decision.decided_at,
                created_by=record.created_by,
            )
        )

    def get_decision(
        self,
        *,
        workspace_id: str,
        decision_sha256: str,
    ) -> PersistedModelRouteDecision | None:
        model = self.session.get(
            ModelRouteDecisionModel,
            {"workspace_id": workspace_id, "decision_sha256": decision_sha256},
        )
        if model is None:
            return None
        try:
            decision = ModelRouteDecision(
                endpoint_capability_version_id=model.endpoint_capability_version_id,
                fallback_endpoint_capability_version_ids=tuple(
                    model.fallback_endpoint_capability_version_ids_json
                ),
                route_policy_version=model.route_policy_version,
                request_sha256=model.route_request_sha256,
                candidate_scores=tuple(
                    ModelRouteCandidateScore(
                        endpoint_capability_version_id=item["endpoint_capability_version_id"],
                        score=Decimal(item["score"]),
                    )
                    for item in model.candidate_scores_json
                ),
                rejection_counts=tuple(
                    (
                        ModelRouteRejectionCode(str(item["code"])),
                        _require_persisted_count(item["count"]),
                    )
                    for item in model.rejection_counts_json
                ),
                decided_at=model.decided_at,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("persisted model route decision is invalid") from exc
        if decision.decision_sha256 != model.decision_sha256:
            raise RuntimeError("persisted model route decision checksum is inconsistent")
        return PersistedModelRouteDecision(
            policy_version_id=model.policy_version_id,
            decision=decision,
            estimated_cost=model.estimated_cost,
            currency=model.currency,
        )


def _require_persisted_count(value: str | int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError("persisted model route rejection count is invalid")
    return value


class SqlAlchemyModelRouterUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self._depth_token: object | None = None
        self._committed = False

    def __enter__(self) -> SqlAlchemyModelRouterUnitOfWork:
        self._session = self._session_factory()
        self._depth_token = enter_unit_of_work()
        self.route_authority = ModelRouteAuthorityRepository(self._session)
        self.idempotency = IdempotencyRepository(self._session)
        self.audit = AuditRepository(self._session)
        return self

    def database_now(self) -> datetime:
        if self._session is None:
            raise RuntimeError("Model Router unit of work is not active")
        value = self._session.scalar(select(literal_column("UTC_TIMESTAMP(6)")))
        if not isinstance(value, datetime):
            raise RuntimeError("database did not return a timestamp")
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("Model Router unit of work is not active")
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
