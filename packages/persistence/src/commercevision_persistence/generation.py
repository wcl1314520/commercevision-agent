"""MySQL persistence for atomic approved-plan generation commands."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import TracebackType

from commercevision_application.asset_idempotency import canonical_hash
from commercevision_application.generation_command_ports import (
    ApprovedGenerationAuthorityPort,
)
from commercevision_application.generation_commands import (
    ApprovedGenerationAuthority,
    ApprovedPlanGenerationCommand,
)
from commercevision_application.tool_authorization import (
    ToolAuthorizationEntitlements,
    ToolAuthorizationPolicy,
)
from commercevision_domain import (
    ApprovalDecision,
    ApprovalType,
    CandidateSlot,
    CircuitState,
    ConcurrencyError,
    CreativePlanVersion,
    GenerationBatch,
    OperationKind,
)
from commercevision_tool_runtime import (
    ToolAuthorizationDecision,
    ToolAuthorizationFacts,
    ToolIntentAuthorizer,
    ToolIntentCandidate,
)
from sqlalchemy import exists, literal_column, or_, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from .creative_plans import CreativePlanRepository
from .database import enter_unit_of_work, exit_unit_of_work
from .generation_models import CandidateSlotModel, GenerationBatchModel
from .integrity import classify_database_error
from .model_router_models import ModelRouteDecisionModel
from .models import (
    AssetModel,
    AssetVersionModel,
    RightsRecordModel,
    RightsRecordProviderModel,
    RightsRecordUseModel,
    WorkflowModel,
)
from .operations import OperationRepository
from .provider_control_plane_models import (
    ModelRoutePolicyVersionModel,
    ProviderEndpointCapabilityVersionModel,
    ProviderEndpointObservationModel,
    ProviderIdentityModel,
)
from .repositories import (
    ApprovalRepository,
    AuditRepository,
    IdempotencyRepository,
    OutboxRepository,
)

GenerationAuthorityFactory = Callable[[Session], ApprovedGenerationAuthorityPort]


class MySqlApprovedGenerationAuthority:
    """Reconstruct one approved generation intent from locked server-owned facts."""

    def __init__(
        self,
        session: Session,
        *,
        authorizer: ToolIntentAuthorizer,
        policy: ToolAuthorizationPolicy,
        entitlements: ToolAuthorizationEntitlements,
        generation_tools: Mapping[tuple[str, str], str],
        rights_policy_version: str,
        safety_policy_version: str,
        actor_id: str,
        required_rights_use: str = "IMAGE_GENERATION",
    ) -> None:
        if authorizer.policy_version != policy.version:
            raise ValueError("Generation Tool Policy versions do not match")
        if not generation_tools:
            raise ValueError("Generation Tool Policy has no allowed tools")
        self._session = session
        self._authorizer = authorizer
        self._policy = policy
        self._entitlements = entitlements
        self._generation_tools = dict(generation_tools)
        self._rights_policy_version = rights_policy_version
        self._safety_policy_version = safety_policy_version
        self._actor_id = actor_id
        self._required_rights_use = required_rights_use

    def load_current_authority(
        self,
        command: ApprovedPlanGenerationCommand,
    ) -> ApprovedGenerationAuthority:
        now = self._database_now()
        workflow = self._session.scalar(
            select(WorkflowModel)
            .where(
                WorkflowModel.workspace_id == command.workspace_id,
                WorkflowModel.id == command.workflow_id,
            )
            .with_for_update()
        )
        if (
            workflow is None
            or workflow.workflow_type != "CREATIVE_PRODUCTION"
            or workflow.status != "GENERATING"
            or workflow.retention_status != "ACTIVE"
            or workflow.current_node != "approve_plan"
            or workflow.version != command.expected_workflow_version
            or workflow.cancellation_requested_at is not None
            or now >= workflow.expires_at
        ):
            raise ConcurrencyError(
                "generation Workflow is not the current approved execution authority"
            )

        plans = CreativePlanRepository(self._session)
        head = plans.get_head(
            workspace_id=command.workspace_id,
            workflow_id=command.workflow_id,
            creative_plan_id=command.creative_plan_id,
            for_update=True,
        )
        if (
            head is None
            or head.current_version_id != command.creative_plan_version_id
            or head.current_version_number != command.creative_plan_version
            or now >= head.retain_until
        ):
            raise ConcurrencyError("generation Creative Plan is not the current approved version")
        plan = plans.get_version(
            workspace_id=command.workspace_id,
            workflow_id=command.workflow_id,
            creative_plan_id=command.creative_plan_id,
            version_id=command.creative_plan_version_id,
        )
        approval = ApprovalRepository(self._session).get(
            command.approval_id,
            workflow_id=command.workflow_id,
        )
        if (
            plan is None
            or plan.version_number != command.creative_plan_version
            or approval is None
            or approval.approval_type is not ApprovalType.CREATIVE_PLAN
            or approval.decision is not ApprovalDecision.APPROVE
            or approval.subject_id != command.creative_plan_id
            or approval.subject_version != command.creative_plan_version
            or approval.expected_workflow_version + 1 != workflow.version
        ):
            raise ConcurrencyError("generation approval does not match the current Creative Plan")

        route_row = self._session.execute(
            select(
                ModelRouteDecisionModel,
                ProviderEndpointCapabilityVersionModel,
                ProviderIdentityModel,
                ModelRoutePolicyVersionModel,
            )
            .join(
                ProviderEndpointCapabilityVersionModel,
                ProviderEndpointCapabilityVersionModel.id
                == ModelRouteDecisionModel.endpoint_capability_version_id,
            )
            .join(
                ProviderIdentityModel,
                ProviderIdentityModel.id == ProviderEndpointCapabilityVersionModel.provider_id,
            )
            .join(
                ModelRoutePolicyVersionModel,
                (ModelRoutePolicyVersionModel.workspace_id == ModelRouteDecisionModel.workspace_id)
                & (ModelRoutePolicyVersionModel.policy_key == ModelRouteDecisionModel.policy_key)
                & (ModelRoutePolicyVersionModel.id == ModelRouteDecisionModel.policy_version_id),
            )
            .where(
                ModelRouteDecisionModel.workspace_id == command.workspace_id,
                ModelRouteDecisionModel.decision_sha256 == command.route_decision_sha256,
            )
            .with_for_update()
        ).one_or_none()
        if route_row is None:
            raise ConcurrencyError("generation Route Decision does not exist")
        route, endpoint, provider, route_policy = route_row
        if (
            route.workflow_id != command.workflow_id
            or route.creative_plan_version_id != command.creative_plan_version_id
            or route.plan_approval_id != command.approval_id
            or not provider.enabled
            or route_policy.policy_version != route.route_policy_version
            or endpoint.provider_id not in self._entitlements.allowed_providers
            or endpoint.capability_json.get("safety_policy_version") != self._safety_policy_version
        ):
            raise ConcurrencyError(
                "generation Route Decision does not match the approved authority"
            )
        latest_observation = self._session.scalar(
            select(ProviderEndpointObservationModel)
            .where(
                ProviderEndpointObservationModel.workspace_id == command.workspace_id,
                ProviderEndpointObservationModel.endpoint_capability_version_id == endpoint.id,
            )
            .order_by(
                ProviderEndpointObservationModel.observed_at.desc(),
                ProviderEndpointObservationModel.id.desc(),
            )
            .limit(1)
            .with_for_update()
        )
        maximum_observation_age_seconds = route_policy.policy_json.get(
            "maximum_observation_age_seconds"
        )
        observation_age = (
            now - latest_observation.observed_at if latest_observation is not None else None
        )
        if (
            latest_observation is None
            or latest_observation.circuit_state != CircuitState.CLOSED.value
            or route.route_candidate_count is None
            or latest_observation.remaining_quota_units < route.route_candidate_count
            or type(maximum_observation_age_seconds) is not int
            or not 1 <= maximum_observation_age_seconds <= 86_400
            or observation_age is None
            or not timedelta(0)
            <= observation_age
            <= timedelta(seconds=maximum_observation_age_seconds)
        ):
            raise ConcurrencyError(
                "generation routed endpoint circuit, quota, or freshness is not dispatchable"
            )

        direction = next(
            (item for item in plan.payload.directions if item.key == command.direction_key),
            None,
        )
        if direction is None:
            raise ConcurrencyError("generation direction is not in the current Creative Plan")
        intent = next(
            (item for item in direction.tool_intents if item.intent_key == command.tool_intent_key),
            None,
        )
        if intent is None:
            raise ConcurrencyError("generation Tool Intent is not in the current Creative Plan")
        expected_provider = self._generation_tools.get((intent.tool_name, intent.schema_version))
        if expected_provider is None or expected_provider != endpoint.provider_id:
            raise ConcurrencyError("generation Tool Intent does not authorize the routed Provider")

        if route.authorized_asset_version_ids_json is None:
            raise ConcurrencyError("generation Route Decision does not bind authorized Assets")
        route_entitlements = replace(
            self._entitlements,
            authorized_resource_ids=(
                self._entitlements.authorized_resource_ids
                | frozenset(route.authorized_asset_version_ids_json)
            ),
        )
        decisions = self._authorize_plan_intents(
            plan=plan,
            approval_id=approval.id,
            approved_by=approval.approved_by,
            workflow_version=workflow.version,
            entitlements=route_entitlements,
        )
        decision = next(
            (item for item in decisions if item.intent_key == command.tool_intent_key),
            None,
        )
        if decision is None or not decision.allowed:
            raise ConcurrencyError("generation Tool Policy denied the approved intent")
        if tuple(route.authorized_asset_version_ids_json) != decision.resource_ids:
            raise ConcurrencyError("generation Route Decision does not match the authorized Assets")
        if route.route_candidate_count != direction.candidate_count:
            raise ConcurrencyError(
                "generation Route Decision does not match the approved candidate count"
            )
        authorized_asset_version_ids, source_rights_deadline = self._load_source_rights(
            workspace_id=command.workspace_id,
            asset_version_ids=decision.resource_ids,
            provider_id=endpoint.provider_id,
            now=now,
        )

        retention_deadline = min(
            deadline
            for deadline in (
                workflow.expires_at,
                head.retain_until,
                source_rights_deadline,
            )
            if deadline is not None
        )
        return ApprovedGenerationAuthority(
            workspace_id=command.workspace_id,
            workflow_id=command.workflow_id,
            workflow_version=workflow.version,
            creative_plan_id=command.creative_plan_id,
            creative_plan_version_id=plan.id,
            creative_plan_version=plan.version_number,
            approval_id=approval.id,
            direction_key=direction.key,
            tool_intent_key=intent.intent_key,
            tool_intent_sha256=canonical_hash(intent.to_canonical_data()),
            prompt_sha256=plan.provenance.prompt_sha256,
            context_sha256=plan.provenance.context_sha256,
            route_decision_sha256=route.decision_sha256,
            route_request_sha256=route.route_request_sha256,
            operation_kind=OperationKind.IMAGE_GENERATION,
            authorized_asset_version_ids=authorized_asset_version_ids,
            candidate_count=direction.candidate_count,
            route_policy_version=route.route_policy_version,
            tool_policy_version=self._policy.version,
            rights_policy_version=self._rights_policy_version,
            safety_policy_version=self._safety_policy_version,
            workflow_deadline=workflow.expires_at,
            source_rights_deadline=source_rights_deadline,
            retention_deadline=retention_deadline,
            created_by=self._actor_id,
        )

    def _load_source_rights(
        self,
        *,
        workspace_id: str,
        asset_version_ids: tuple[str, ...],
        provider_id: str,
        now: datetime,
    ) -> tuple[tuple[str, ...], datetime | None]:
        deadlines: list[datetime] = []
        for asset_version_id in asset_version_ids:
            has_use = exists().where(
                RightsRecordUseModel.workspace_id == workspace_id,
                RightsRecordUseModel.asset_id == AssetModel.id,
                RightsRecordUseModel.rights_record_id == RightsRecordModel.id,
                RightsRecordUseModel.allowed_use == self._required_rights_use,
            )
            has_provider = exists().where(
                RightsRecordProviderModel.workspace_id == workspace_id,
                RightsRecordProviderModel.asset_id == AssetModel.id,
                RightsRecordProviderModel.rights_record_id == RightsRecordModel.id,
                RightsRecordProviderModel.allowed_provider == provider_id,
            )
            row = self._session.execute(
                select(AssetModel, RightsRecordModel)
                .join(
                    AssetVersionModel,
                    (AssetVersionModel.workspace_id == AssetModel.workspace_id)
                    & (AssetVersionModel.asset_id == AssetModel.id)
                    & (AssetVersionModel.id == asset_version_id),
                )
                .join(
                    RightsRecordModel,
                    (RightsRecordModel.workspace_id == AssetModel.workspace_id)
                    & (RightsRecordModel.asset_id == AssetModel.id)
                    & (RightsRecordModel.id == AssetModel.current_rights_record_id),
                )
                .where(
                    AssetModel.workspace_id == workspace_id,
                    AssetModel.current_version_id == asset_version_id,
                    AssetModel.status == "AVAILABLE",
                    or_(
                        AssetModel.retention_deadline.is_(None),
                        AssetModel.retention_deadline > now,
                    ),
                    RightsRecordModel.decision == "GRANT",
                    RightsRecordModel.permissions_sealed_at.is_not(None),
                    RightsRecordModel.valid_from <= now,
                    or_(
                        RightsRecordModel.perpetual.is_(True),
                        RightsRecordModel.valid_until > now,
                    ),
                    RightsRecordModel.derivative_allowed.is_(True),
                    or_(
                        RightsRecordModel.asset_version_id.is_(None),
                        RightsRecordModel.asset_version_id == asset_version_id,
                    ),
                    has_use,
                    has_provider,
                )
                .with_for_update()
            ).one_or_none()
            if row is None:
                raise ConcurrencyError(
                    "generation source Asset Rights are not current and authorized"
                )
            asset, rights = row
            if asset.retention_deadline is not None:
                deadlines.append(asset.retention_deadline)
            if rights.valid_until is not None:
                deadlines.append(rights.valid_until)
        return asset_version_ids, min(deadlines) if deadlines else None

    def _authorize_plan_intents(
        self,
        *,
        plan: CreativePlanVersion,
        approval_id: str,
        approved_by: str,
        workflow_version: int,
        entitlements: ToolAuthorizationEntitlements,
    ) -> tuple[ToolAuthorizationDecision, ...]:
        intents = tuple(
            intent for direction in plan.payload.directions for intent in direction.tool_intents
        )
        if len(intents) > self._policy.maximum_intents:
            raise ConcurrencyError("generation Tool Policy intent limit was exceeded")
        remaining_quota = entitlements.remaining_quota_units
        remaining_budget = entitlements.remaining_budget_units
        decisions: list[ToolAuthorizationDecision] = []
        for intent in intents:
            decision = self._authorizer.authorize(
                candidate=ToolIntentCandidate(
                    intent_key=intent.intent_key,
                    tool_name=intent.tool_name,
                    schema_version=intent.schema_version,
                    purpose=intent.purpose,
                    arguments_json=intent.arguments_json,
                    estimated_cost_units=intent.estimated_cost_units,
                ),
                facts=ToolAuthorizationFacts(
                    workspace_id=plan.workspace_id,
                    actor_id=approved_by,
                    workflow_id=plan.workflow_id,
                    workflow_version=workflow_version,
                    creative_plan_id=plan.creative_plan_id,
                    creative_plan_version_id=plan.id,
                    creative_plan_version=plan.version_number,
                    approval_id=approval_id,
                    node=self._policy.node,
                    granted_scopes=entitlements.granted_scopes,
                    authorized_resource_ids=entitlements.authorized_resource_ids,
                    allowed_providers=entitlements.allowed_providers,
                    allowed_cost_classes=entitlements.allowed_cost_classes,
                    remaining_quota_units=remaining_quota,
                    remaining_budget_units=remaining_budget,
                ),
            )
            decisions.append(decision)
            if decision.allowed:
                remaining_quota -= intent.estimated_cost_units
                remaining_budget -= intent.estimated_cost_units
        return tuple(decisions)

    def _database_now(self) -> datetime:
        value = self._session.scalar(select(literal_column("UTC_TIMESTAMP(6)")))
        if not isinstance(value, datetime):
            raise RuntimeError("database did not return a timestamp")
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _batch_from_model(model: GenerationBatchModel) -> GenerationBatch:
    batch = GenerationBatch(
        id=model.id,
        workspace_id=model.workspace_id,
        workflow_id=model.workflow_id,
        workflow_version=model.workflow_version,
        creative_plan_version_id=model.creative_plan_version_id,
        plan_approval_id=model.plan_approval_id,
        direction_key=model.direction_key,
        tool_intent_key=model.tool_intent_key,
        tool_intent_sha256=model.tool_intent_sha256,
        prompt_sha256=model.prompt_sha256,
        context_sha256=model.context_sha256,
        route_decision_sha256=model.route_decision_sha256,
        route_request_sha256=model.route_request_sha256,
        operation_kind=OperationKind(model.operation_kind),
        authorized_asset_version_ids=tuple(model.authorized_asset_version_ids_json),
        candidate_count=model.candidate_count,
        route_policy_version=model.route_policy_version,
        tool_policy_version=model.tool_policy_version,
        rights_policy_version=model.rights_policy_version,
        safety_policy_version=model.safety_policy_version,
        workflow_deadline=model.workflow_deadline,
        source_rights_deadline=model.source_rights_deadline,
        edit_source_asset_version_id=model.edit_source_asset_version_id,
        edit_mask_asset_version_id=model.edit_mask_asset_version_id,
        approved_repair_scope=tuple(model.approved_repair_scope_json),
        retention_deadline=model.retention_deadline,
        created_by=model.created_by,
        created_at=model.created_at,
    )
    if batch.batch_sha256 != model.batch_sha256:
        raise RuntimeError("persisted Generation Batch checksum is inconsistent")
    return batch


class GenerationBatchRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, batch: GenerationBatch) -> None:
        self._session.add(
            GenerationBatchModel(
                workspace_id=batch.workspace_id,
                id=batch.id,
                batch_sha256=batch.batch_sha256,
                workflow_id=batch.workflow_id,
                workflow_version=batch.workflow_version,
                creative_plan_version_id=batch.creative_plan_version_id,
                plan_approval_id=batch.plan_approval_id,
                direction_key=batch.direction_key,
                tool_intent_key=batch.tool_intent_key,
                tool_intent_sha256=batch.tool_intent_sha256,
                prompt_sha256=batch.prompt_sha256,
                context_sha256=batch.context_sha256,
                route_decision_sha256=batch.route_decision_sha256,
                route_request_sha256=batch.route_request_sha256,
                operation_kind=batch.operation_kind.value,
                authorized_asset_version_ids_json=list(batch.authorized_asset_version_ids),
                candidate_count=batch.candidate_count,
                route_policy_version=batch.route_policy_version,
                tool_policy_version=batch.tool_policy_version,
                rights_policy_version=batch.rights_policy_version,
                safety_policy_version=batch.safety_policy_version,
                workflow_deadline=batch.workflow_deadline,
                source_rights_deadline=batch.source_rights_deadline,
                edit_source_asset_version_id=batch.edit_source_asset_version_id,
                edit_mask_asset_version_id=batch.edit_mask_asset_version_id,
                approved_repair_scope_json=list(batch.approved_repair_scope),
                retention_deadline=batch.retention_deadline,
                created_by=batch.created_by,
                created_at=batch.created_at,
            )
        )

    def get(self, batch_id: str, *, workspace_id: str) -> GenerationBatch | None:
        model = self._session.get(
            GenerationBatchModel,
            {"workspace_id": workspace_id, "id": batch_id},
        )
        return _batch_from_model(model) if model is not None else None

    def get_by_logical_identity(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        workflow_version: int,
        creative_plan_version_id: str,
        direction_key: str,
        tool_intent_key: str,
    ) -> GenerationBatch | None:
        model = self._session.scalar(
            select(GenerationBatchModel)
            .where(
                GenerationBatchModel.workspace_id == workspace_id,
                GenerationBatchModel.workflow_id == workflow_id,
                GenerationBatchModel.workflow_version == workflow_version,
                GenerationBatchModel.creative_plan_version_id == creative_plan_version_id,
                GenerationBatchModel.direction_key == direction_key,
                GenerationBatchModel.tool_intent_key == tool_intent_key,
            )
            .with_for_update()
        )
        return _batch_from_model(model) if model is not None else None


class CandidateSlotRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, slot: CandidateSlot) -> None:
        self._session.add(
            CandidateSlotModel(
                workspace_id=slot.workspace_id,
                id=slot.id,
                generation_batch_id=slot.generation_batch_id,
                candidate_index=slot.candidate_index,
                durable_operation_id=slot.durable_operation_id,
                operation_kind=slot.operation_kind.value,
                logical_identity_sha256=slot.logical_identity_sha256,
                operation_idempotency_key=slot.operation_idempotency_key,
            )
        )

    def list_for_batch(
        self, *, workspace_id: str, generation_batch_id: str
    ) -> tuple[CandidateSlot, ...]:
        models = self._session.scalars(
            select(CandidateSlotModel)
            .where(
                CandidateSlotModel.workspace_id == workspace_id,
                CandidateSlotModel.generation_batch_id == generation_batch_id,
            )
            .order_by(CandidateSlotModel.candidate_index)
        )
        return tuple(
            CandidateSlot(
                id=model.id,
                workspace_id=model.workspace_id,
                generation_batch_id=model.generation_batch_id,
                candidate_index=model.candidate_index,
                durable_operation_id=model.durable_operation_id,
                operation_kind=OperationKind(model.operation_kind),
                logical_identity_sha256=model.logical_identity_sha256,
                operation_idempotency_key=model.operation_idempotency_key,
            )
            for model in models
        )


class SqlAlchemyApprovedGenerationUnitOfWork:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        authority_factory: GenerationAuthorityFactory,
    ) -> None:
        self._session_factory = session_factory
        self._authority_factory = authority_factory
        self._session: Session | None = None
        self._depth_token: object | None = None
        self._committed = False

    def __enter__(self) -> SqlAlchemyApprovedGenerationUnitOfWork:
        self._session = self._session_factory()
        self._depth_token = enter_unit_of_work()
        self.generation_authority = self._authority_factory(self._session)
        self.generation_batches = GenerationBatchRepository(self._session)
        self.candidate_slots = CandidateSlotRepository(self._session)
        self.operations = OperationRepository(self._session)
        self.outbox = OutboxRepository(self._session)
        self.idempotency = IdempotencyRepository(self._session)
        self.audit = AuditRepository(self._session)
        return self

    def database_now(self) -> datetime:
        if self._session is None:
            raise RuntimeError("Generation unit of work is not active")
        value = self._session.scalar(select(literal_column("UTC_TIMESTAMP(6)")))
        if not isinstance(value, datetime):
            raise RuntimeError("database did not return a timestamp")
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("Generation unit of work is not active")
        try:
            self._session.commit()
        except DBAPIError as exc:
            self._session.rollback()
            classified = classify_database_error(exc)
            if classified is None:
                raise
            raise classified from exc
        self._committed = True

    def flush(self) -> None:
        if self._session is None:
            raise RuntimeError("Generation unit of work is not active")
        try:
            self._session.flush()
        except DBAPIError as exc:
            self._session.rollback()
            classified = classify_database_error(exc)
            if classified is None:
                raise
            raise classified from exc

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
