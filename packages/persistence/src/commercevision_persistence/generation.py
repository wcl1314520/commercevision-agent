"""MySQL persistence for atomic approved-plan generation commands."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import TracebackType
from uuid import UUID, uuid5

from commercevision_application.asset_idempotency import canonical_hash
from commercevision_application.generation_command_ports import (
    ApprovedGenerationAuthorityPort,
)
from commercevision_application.generation_commands import (
    ApprovedGenerationAuthority,
    ApprovedPlanGenerationCommand,
)
from commercevision_application.generation_execution import (
    AuthorizedGenerationDispatch,
    GenerationDispatchAttemptClaim,
    GenerationDispatchAuthorityDenied,
    GenerationDispatchBuilder,
    GenerationDispatchFacts,
    GenerationSuccessCommit,
    GenerationWorkflowContinuationClaim,
    generation_provider_call_id,
)
from commercevision_application.operations import (
    OperationExecutionRequest,
    OperationExecutionResult,
)
from commercevision_application.tool_authorization import (
    ToolAuthorizationEntitlements,
    ToolAuthorizationPolicy,
)
from commercevision_contracts.events import (
    EventType,
    GenerationCandidateReadyPayload,
)
from commercevision_contracts.image_provider import NormalizedImageProviderOutcome
from commercevision_domain import (
    ApprovalDecision,
    ApprovalType,
    Asset,
    AssetKind,
    AssetObject,
    AssetObjectState,
    AssetState,
    AssetVersion,
    CandidateImage,
    CandidateSlot,
    CircuitState,
    ConcurrencyError,
    CreativePlanVersion,
    DurableOperation,
    GenerationBatch,
    ModelRouteRequest,
    OperationKind,
    OperationState,
    ProviderCall,
    ProviderCallOutcome,
    ProviderEndpointCapabilityVersion,
    ProviderPricingUnit,
    RetentionClass,
    UniqueConstraintError,
    UsageEvidenceSource,
    UsageRecord,
    UsageResolutionStatus,
)
from commercevision_domain.messaging.events import EventEnvelope, OutboxEvent
from commercevision_tool_runtime import (
    ToolAuthorizationDecision,
    ToolAuthorizationFacts,
    ToolIntentAuthorizer,
    ToolIntentCandidate,
)
from sqlalchemy import exists, literal_column, or_, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from .assets import AssetRepository
from .creative_plan_models import CreativePlanModel, CreativePlanVersionModel
from .creative_plans import CreativePlanRepository
from .database import enter_unit_of_work, exit_unit_of_work
from .generation_models import (
    CandidateImageModel,
    CandidateSlotModel,
    GenerationBatchModel,
    GenerationDispatchAttemptModel,
    ProviderCallModel,
    UsageRecordModel,
)
from .integrity import classify_database_error, flush_with_integrity_classification
from .model_router_models import ModelRouteDecisionModel
from .models import (
    AssetModel,
    AssetVersionModel,
    DurableOperationModel,
    RightsRecordModel,
    RightsRecordProviderModel,
    RightsRecordUseModel,
    WorkflowModel,
    WorkflowStepModel,
)
from .operations import OperationRepository
from .provider_control_plane import capability_from_model
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


class MySqlGenerationDispatchAuthority:
    """Lock and revalidate one dispatch without crossing the Provider boundary."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        approved_authority_factory: GenerationAuthorityFactory,
        dispatch_builder: GenerationDispatchBuilder,
    ) -> None:
        self._session_factory = session_factory
        self._approved_authority_factory = approved_authority_factory
        self._dispatch_builder = dispatch_builder

    def prepare_dispatch(
        self,
        request: OperationExecutionRequest,
    ) -> AuthorizedGenerationDispatch:
        depth_token = enter_unit_of_work()
        try:
            with self._session_factory() as session, session.begin():
                try:
                    facts = self._load_current_facts(session, request)
                except (ConcurrencyError, ValueError):
                    raise GenerationDispatchAuthorityDenied from None
        finally:
            exit_unit_of_work(depth_token)
        return self._dispatch_builder.build(facts)

    def _load_current_facts(
        self,
        session: Session,
        request: OperationExecutionRequest,
    ) -> GenerationDispatchFacts:
        identity = session.execute(
            select(
                CandidateSlotModel.generation_batch_id,
                CreativePlanVersionModel.creative_plan_id,
            )
            .join(
                GenerationBatchModel,
                (GenerationBatchModel.workspace_id == CandidateSlotModel.workspace_id)
                & (GenerationBatchModel.id == CandidateSlotModel.generation_batch_id),
            )
            .join(
                CreativePlanVersionModel,
                (CreativePlanVersionModel.workspace_id == GenerationBatchModel.workspace_id)
                & (CreativePlanVersionModel.id == GenerationBatchModel.creative_plan_version_id),
            )
            .where(
                CandidateSlotModel.workspace_id == request.workspace_id,
                CandidateSlotModel.id == request.target_id,
                CandidateSlotModel.durable_operation_id == request.operation_id,
            )
        ).one_or_none()
        if identity is None:
            raise ConcurrencyError("generation dispatch identity does not exist")
        generation_batch_id, creative_plan_id = identity

        batch_preview = session.get(
            GenerationBatchModel,
            {"workspace_id": request.workspace_id, "id": generation_batch_id},
        )
        if batch_preview is None:
            raise ConcurrencyError("generation dispatch batch does not exist")
        command = ApprovedPlanGenerationCommand(
            workspace_id=batch_preview.workspace_id,
            workflow_id=batch_preview.workflow_id,
            expected_workflow_version=batch_preview.workflow_version,
            creative_plan_id=creative_plan_id,
            creative_plan_version_id=batch_preview.creative_plan_version_id,
            creative_plan_version=self._creative_plan_version_number(
                session,
                workspace_id=batch_preview.workspace_id,
                version_id=batch_preview.creative_plan_version_id,
            ),
            approval_id=batch_preview.plan_approval_id,
            direction_key=batch_preview.direction_key,
            tool_intent_key=batch_preview.tool_intent_key,
            route_decision_sha256=batch_preview.route_decision_sha256,
        )
        approved = self._approved_authority_factory(session).load_current_authority(command)
        now = self._database_now(session)

        operation = OperationRepository(session).get(
            request.operation_id,
            workspace_id=request.workspace_id,
            for_update=True,
        )
        slot_model = session.scalar(
            select(CandidateSlotModel)
            .where(
                CandidateSlotModel.workspace_id == request.workspace_id,
                CandidateSlotModel.id == request.target_id,
            )
            .with_for_update()
        )
        batch_model = session.scalar(
            select(GenerationBatchModel)
            .where(
                GenerationBatchModel.workspace_id == request.workspace_id,
                GenerationBatchModel.id == generation_batch_id,
            )
            .with_for_update()
        )
        route_row = session.execute(
            select(ModelRouteDecisionModel, ProviderEndpointCapabilityVersionModel)
            .join(
                ProviderEndpointCapabilityVersionModel,
                ProviderEndpointCapabilityVersionModel.id
                == ModelRouteDecisionModel.endpoint_capability_version_id,
            )
            .where(
                ModelRouteDecisionModel.workspace_id == request.workspace_id,
                ModelRouteDecisionModel.decision_sha256 == command.route_decision_sha256,
            )
            .with_for_update()
        ).one_or_none()
        plan = CreativePlanRepository(session).get_version(
            workspace_id=command.workspace_id,
            workflow_id=command.workflow_id,
            creative_plan_id=command.creative_plan_id,
            version_id=command.creative_plan_version_id,
        )
        if operation is None or slot_model is None or batch_model is None or route_row is None:
            raise ConcurrencyError("generation dispatch authority is incomplete")
        route, endpoint = route_row
        if plan is None:
            raise ConcurrencyError("generation dispatch Creative Plan does not exist")
        try:
            batch = _batch_from_model(batch_model)
        except RuntimeError as exc:
            raise ConcurrencyError("generation dispatch batch integrity check failed") from exc
        slot = _slot_from_model(slot_model)
        self._validate_operation(
            request=request,
            operation=operation,
            slot=slot,
            batch=batch,
            now=now,
        )
        self._validate_approved_batch(
            approved=approved,
            batch=batch,
            creative_plan_id=plan.creative_plan_id,
            creative_plan_version=plan.version_number,
        )
        if route.route_request_json is None:
            raise ConcurrencyError("generation Route Request projection does not exist")
        route_request = ModelRouteRequest.from_canonical_data(route.route_request_json)
        if (
            route.route_request_sha256 != route_request.request_sha256
            or route.route_request_sha256 != batch.route_request_sha256
            or route.endpoint_capability_version_id != endpoint.id
            or route.workflow_id != batch.workflow_id
            or route.creative_plan_version_id != batch.creative_plan_version_id
            or route.plan_approval_id != batch.plan_approval_id
            or route_request.workspace_id != batch.workspace_id
            or route_request.workflow_id != batch.workflow_id
            or route_request.creative_plan_version_id != batch.creative_plan_version_id
            or route_request.plan_approval_id != batch.plan_approval_id
            or route_request.authorized_asset_version_ids != batch.authorized_asset_version_ids
            or route_request.candidate_count != batch.candidate_count
        ):
            raise ConcurrencyError("generation Route Request does not match its batch")
        return GenerationDispatchFacts(
            operation=request,
            batch=batch,
            slot=slot,
            approved_authority=approved,
            creative_plan=plan,
            route_request=route_request,
            endpoint_capability_version_id=endpoint.id,
            adapter_configuration_sha256=endpoint.configuration_sha256,
        )

    @staticmethod
    def _creative_plan_version_number(
        session: Session,
        *,
        workspace_id: str,
        version_id: str,
    ) -> int:
        version_number = session.scalar(
            select(CreativePlanVersionModel.version_number).where(
                CreativePlanVersionModel.workspace_id == workspace_id,
                CreativePlanVersionModel.id == version_id,
            )
        )
        if not isinstance(version_number, int) or isinstance(version_number, bool):
            raise ConcurrencyError("generation Creative Plan version does not exist")
        return version_number

    @staticmethod
    def _validate_operation(
        *,
        request: OperationExecutionRequest,
        operation: DurableOperation,
        slot: CandidateSlot,
        batch: GenerationBatch,
        now: datetime,
    ) -> None:
        expected = (
            request.operation_id,
            request.workspace_id,
            request.kind,
            request.target_type,
            request.target_id,
            request.target_version,
            request.input_hash,
            request.input_ref,
            request.provider_request_id,
            request.attempt_count,
            request.execution_version,
            request.lease_token,
            request.lease_expires_at,
            request.replay_source_dead_letter_id,
            request.replay_attempt,
        )
        actual = (
            operation.id,
            operation.workspace_id,
            operation.kind,
            operation.target_type,
            operation.target_id,
            operation.target_version,
            operation.input_hash,
            operation.input_ref,
            operation.provider_request_id,
            operation.attempt_count,
            operation.version,
            operation.lease_token,
            operation.lease_expires_at,
            operation.replay_source_dead_letter_id,
            operation.replay_attempt,
        )
        if (
            actual != expected
            or request.idempotency_key != f"durable-operation:{operation.id}"
            or operation.state is not OperationState.RUNNING
            or operation.lease_expires_at is None
            or operation.lease_expires_at <= now
            or operation.execution_deadline_at <= now
            or slot.workspace_id != batch.workspace_id
            or slot.generation_batch_id != batch.id
            or slot.durable_operation_id != operation.id
            or slot.operation_kind is not batch.operation_kind
            or slot.logical_identity_sha256 != operation.input_hash
        ):
            raise ConcurrencyError("generation Durable Operation is not dispatchable")

    @staticmethod
    def _database_now(session: Session) -> datetime:
        value = session.scalar(select(literal_column("UTC_TIMESTAMP(6)")))
        if not isinstance(value, datetime):
            raise RuntimeError("database did not return a timestamp")
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    @staticmethod
    def _validate_approved_batch(
        *,
        approved: ApprovedGenerationAuthority,
        batch: GenerationBatch,
        creative_plan_id: str,
        creative_plan_version: int,
    ) -> None:
        authority_facts = (
            approved.workspace_id,
            approved.workflow_id,
            approved.workflow_version,
            approved.creative_plan_id,
            approved.creative_plan_version_id,
            approved.creative_plan_version,
            approved.approval_id,
            approved.direction_key,
            approved.tool_intent_key,
            approved.tool_intent_sha256,
            approved.prompt_sha256,
            approved.context_sha256,
            approved.route_decision_sha256,
            approved.route_request_sha256,
            approved.operation_kind,
            approved.authorized_asset_version_ids,
            approved.candidate_count,
            approved.route_policy_version,
            approved.tool_policy_version,
            approved.rights_policy_version,
            approved.safety_policy_version,
            approved.workflow_deadline,
            approved.source_rights_deadline,
            approved.retention_deadline,
            approved.created_by,
        )
        batch_facts = (
            batch.workspace_id,
            batch.workflow_id,
            batch.workflow_version,
            creative_plan_id,
            batch.creative_plan_version_id,
            creative_plan_version,
            batch.plan_approval_id,
            batch.direction_key,
            batch.tool_intent_key,
            batch.tool_intent_sha256,
            batch.prompt_sha256,
            batch.context_sha256,
            batch.route_decision_sha256,
            batch.route_request_sha256,
            batch.operation_kind,
            batch.authorized_asset_version_ids,
            batch.candidate_count,
            batch.route_policy_version,
            batch.tool_policy_version,
            batch.rights_policy_version,
            batch.safety_policy_version,
            batch.workflow_deadline,
            batch.source_rights_deadline,
            batch.retention_deadline,
            batch.created_by,
        )
        if authority_facts != batch_facts:
            raise ConcurrencyError("generation batch no longer matches approved authority")


class MySqlGenerationResultConverger:
    """Atomically publish one already-controlled Provider result under a live lease."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        approved_authority_factory: GenerationAuthorityFactory,
    ) -> None:
        self._session_factory = session_factory
        self._authority = MySqlGenerationDispatchAuthority(
            session_factory,
            approved_authority_factory=approved_authority_factory,
            dispatch_builder=_ResultFactsOnlyBuilder(),
        )

    def commit_success(self, commit: GenerationSuccessCommit) -> OperationExecutionResult:
        depth_token = enter_unit_of_work()
        try:
            with self._session_factory() as session, session.begin():
                facts = self._authority._load_current_facts(session, commit.operation)
                now = self._authority._database_now(session)
                if (
                    commit.controlled_object.reference.location.value != "TASK"
                    or now >= facts.batch.retention_deadline
                ):
                    raise ConcurrencyError("generation result is no longer publishable")
                endpoint_model = session.get(
                    ProviderEndpointCapabilityVersionModel,
                    facts.endpoint_capability_version_id,
                )
                if endpoint_model is None:
                    raise ConcurrencyError("generation endpoint capability does not exist")
                endpoint = capability_from_model(endpoint_model)
                provider_request_id = self._provider_request_id(commit)
                provider_request_sha256 = hashlib.sha256(
                    provider_request_id.encode("utf-8")
                ).hexdigest()
                ids = _generation_result_ids(
                    operation=commit.operation,
                )
                dispatch_attempt = session.get(
                    GenerationDispatchAttemptModel,
                    (facts.batch.workspace_id, ids.provider_call_id),
                    with_for_update=True,
                )
                self._validate_dispatch_attempt(
                    model=dispatch_attempt,
                    commit=commit,
                    facts=facts,
                    endpoint=endpoint,
                )
                provider_call = ProviderCall(
                    id=ids.provider_call_id,
                    workspace_id=facts.batch.workspace_id,
                    candidate_slot_id=facts.slot.id,
                    durable_operation_id=commit.operation.operation_id,
                    operation_attempt=commit.operation.attempt_count,
                    call_index=0,
                    route_decision_sha256=facts.batch.route_decision_sha256,
                    endpoint_capability_version_id=endpoint.id,
                    provider=endpoint.provider_id,
                    model=endpoint.model_id,
                    request_sha256=commit.request_sha256,
                    idempotency_key_sha256=hashlib.sha256(
                        commit.operation.idempotency_key.encode("utf-8")
                    ).hexdigest(),
                    outcome=ProviderCallOutcome.CONFIRMED_SUCCESS,
                    possible_dispatch=True,
                    provider_request_id_sha256=provider_request_sha256,
                    latency_ms=commit.provider_outcome.latency_ms,
                    observed_at=now,
                )
                usage = self._usage_record(
                    commit=commit,
                    provider_call=provider_call,
                    endpoint=endpoint,
                    usage_record_id=ids.usage_record_id,
                    now=now,
                )
                asset, asset_version, object_fact, candidate = self._candidate_facts(
                    commit=commit,
                    facts=facts,
                    provider_call=provider_call,
                    usage=usage,
                    ids=ids,
                    provider_request_sha256=provider_request_sha256,
                    now=now,
                )
                session.add(_provider_call_to_model(provider_call))
                flush_with_integrity_classification(session)
                AssetRepository(session).add_controlled_generation(
                    asset=asset,
                    asset_version=asset_version,
                    object_fact=object_fact,
                )
                session.add(_usage_record_to_model(usage))
                flush_with_integrity_classification(session)
                session.add(_candidate_image_to_model(candidate))
                operations = OperationRepository(session)
                operation = operations.get(
                    commit.operation.operation_id,
                    workspace_id=commit.operation.workspace_id,
                    for_update=True,
                )
                if operation is None:
                    raise ConcurrencyError("generation Operation disappeared before commit")
                output_ref = f"asset-version:{asset_version.id}"
                operation.succeed(
                    lease_token=commit.operation.lease_token or "",
                    output_ref=output_ref,
                    provider_request_id=provider_request_id,
                    expected_execution_version=commit.operation.execution_version,
                    expected_attempt_count=commit.operation.attempt_count,
                    now=now,
                )
                operations.save(operation)
                OutboxRepository(session).add(
                    OutboxEvent(
                        envelope=EventEnvelope.create(
                            event_type=EventType.GENERATION_CANDIDATE_READY.value,
                            aggregate_type="generation-batch",
                            aggregate_id=facts.batch.id,
                            aggregate_version=1,
                            trace_id=commit.trace_id,
                            payload=GenerationCandidateReadyPayload(
                                workspace_id=facts.batch.workspace_id,
                                workflow_id=facts.batch.workflow_id,
                                generation_batch_id=facts.batch.id,
                                candidate_slot_id=facts.slot.id,
                                candidate_image_id=candidate.id,
                                asset_version_id=asset_version.id,
                                operation_id=operation.id,
                                usage_record_id=usage.id,
                            ).model_dump(mode="json"),
                            now=now,
                        ),
                        available_at=now,
                        workspace_id=facts.batch.workspace_id,
                    )
                )
                AuditRepository(session).add(
                    workspace_id=facts.batch.workspace_id,
                    actor_type="SERVICE",
                    actor_id="generation-worker",
                    action="generation-candidate.ready",
                    resource_type="candidate-image",
                    resource_id=candidate.id,
                    trace_id=commit.trace_id,
                    metadata={
                        "workflow_id": facts.batch.workflow_id,
                        "generation_batch_id": facts.batch.id,
                        "candidate_slot_id": facts.slot.id,
                        "asset_version_id": asset_version.id,
                        "usage_record_id": usage.id,
                    },
                    created_at=now,
                    expires_at=min(facts.batch.retention_deadline, now + timedelta(days=30)),
                )
        except (ConcurrencyError, ValueError):
            raise
        finally:
            exit_unit_of_work(depth_token)
        return OperationExecutionResult(
            operation_id=commit.operation.operation_id,
            output_ref=output_ref,
            provider_request_id=provider_request_id,
            completion_committed=True,
        )

    @staticmethod
    def _provider_request_id(commit: GenerationSuccessCommit) -> str:
        identity = commit.provider_outcome.identity
        assert identity is not None
        value = identity.provider_request_id or identity.provider_task_id
        if not isinstance(value, str):
            raise ValueError("generation Provider identity is unavailable")
        return value

    @staticmethod
    def _validate_dispatch_attempt(
        *,
        model: GenerationDispatchAttemptModel | None,
        commit: GenerationSuccessCommit,
        facts: GenerationDispatchFacts,
        endpoint: ProviderEndpointCapabilityVersion,
    ) -> None:
        if model is None:
            raise ConcurrencyError("generation result has no durable dispatch Attempt")
        identity = commit.provider_outcome.identity
        assert identity is not None
        request_id = (
            identity.provider_request_id if isinstance(identity.provider_request_id, str) else None
        )
        task_id = identity.provider_task_id if isinstance(identity.provider_task_id, str) else None
        operation = commit.operation
        expected = (
            operation.workspace_id,
            generation_provider_call_id(operation),
            facts.slot.id,
            operation.operation_id,
            operation.attempt_count,
            0,
            endpoint.id,
            commit.request_sha256,
            endpoint.configuration_sha256,
            hashlib.sha256(operation.idempotency_key.encode("utf-8")).hexdigest(),
            "OUTCOME_RECORDED",
            commit.provider_outcome.call_outcome.value,
            request_id,
            task_id,
        )
        actual = (
            model.workspace_id,
            model.id,
            model.candidate_slot_id,
            model.durable_operation_id,
            model.operation_attempt,
            model.call_index,
            model.endpoint_capability_version_id,
            model.request_sha256,
            model.adapter_configuration_sha256,
            model.idempotency_key_sha256,
            model.state,
            model.outcome,
            model.provider_request_id,
            model.provider_task_id,
        )
        if actual != expected:
            raise ConcurrencyError("generation result does not match its durable dispatch Attempt")

    @staticmethod
    def _usage_record(
        *,
        commit: GenerationSuccessCommit,
        provider_call: ProviderCall,
        endpoint: ProviderEndpointCapabilityVersion,
        usage_record_id: str,
        now: datetime,
    ) -> UsageRecord:
        estimated_quantity = _estimated_quantity(endpoint.pricing_unit, commit)
        estimated_amount = (estimated_quantity * endpoint.unit_price).quantize(Decimal("0.000001"))
        provider_usage = commit.provider_outcome.usage
        if provider_usage is None:
            reported_quantity = None
            provider_evidence = None
            actual_amount = None
            final_evidence = None
            resolution = UsageResolutionStatus.UNRESOLVED
        else:
            if provider_usage.unit.value != endpoint.pricing_unit.value:
                raise ValueError("generation Provider usage unit does not match route pricing")
            reported_quantity = provider_usage.quantity
            provider_evidence = provider_usage.evidence_sha256
            actual_amount = (reported_quantity * endpoint.unit_price).quantize(Decimal("0.000001"))
            final_evidence = canonical_hash(
                {
                    "provider_usage_evidence_sha256": provider_evidence,
                    "pricing_evidence_sha256": endpoint.capability_sha256,
                    "actual_amount": str(actual_amount),
                    "currency": endpoint.currency,
                }
            )
            resolution = UsageResolutionStatus.FINALIZED
        return UsageRecord(
            id=usage_record_id,
            workspace_id=provider_call.workspace_id,
            provider_call_id=provider_call.id,
            provider_call_identity_sha256=provider_call.call_identity_sha256,
            durable_operation_id=provider_call.durable_operation_id,
            operation_attempt=provider_call.operation_attempt,
            provider=provider_call.provider,
            model=provider_call.model,
            endpoint_capability_version_id=provider_call.endpoint_capability_version_id,
            pricing_unit=endpoint.pricing_unit,
            estimated_quantity=estimated_quantity,
            provider_reported_quantity=reported_quantity,
            configured_unit_price=endpoint.unit_price,
            estimated_amount=estimated_amount,
            actual_amount=actual_amount,
            currency=endpoint.currency,
            unit_price_version=endpoint.id,
            provider_usage_evidence_sha256=provider_evidence,
            pricing_evidence_sha256=endpoint.capability_sha256,
            final_cost_evidence_sha256=final_evidence,
            resolution_status=resolution,
            evidence_source=UsageEvidenceSource.DIRECT_RESPONSE,
            latency_ms=commit.provider_outcome.latency_ms,
            recorded_at=now,
        )

    @staticmethod
    def _candidate_facts(
        *,
        commit: GenerationSuccessCommit,
        facts: GenerationDispatchFacts,
        provider_call: ProviderCall,
        usage: UsageRecord,
        ids: _GenerationResultIds,
        provider_request_sha256: str,
        now: datetime,
    ) -> tuple[Asset, AssetVersion, AssetObject, CandidateImage]:
        result = commit.provider_outcome.result
        assert result is not None
        stat = commit.controlled_object
        version_id = stat.reference.version_id
        if stat.reference.location.value != "TASK" or version_id is None:
            raise ValueError("generation result object is not a controlled Task object")
        asset = Asset(
            id=ids.asset_id,
            workspace_id=facts.batch.workspace_id,
            retention_class=RetentionClass.TASK,
            kind=AssetKind.IMAGE,
            workflow_id=facts.batch.workflow_id,
            product_id=None,
            sku_id=None,
            status=AssetState.AVAILABLE,
            block_reason=None,
            current_version_id=ids.asset_version_id,
            retention_deadline=facts.batch.retention_deadline,
            version=1,
            created_at=now,
            updated_at=now,
        )
        asset_version = AssetVersion(
            id=ids.asset_version_id,
            workspace_id=facts.batch.workspace_id,
            asset_id=ids.asset_id,
            version_number=1,
            upload_session_id=None,
            filename=f"candidate-{facts.slot.candidate_index}.{result.media_type.value.split('/')[-1]}",
            sha256=result.content_sha256,
            byte_size=len(result.content),
            declared_mime=result.media_type.value,
            detected_mime=result.media_type.value,
            image_format=result.media_type.name,
            width=result.width,
            height=result.height,
            frame_count=1,
            category="generated-candidate",
            role="candidate",
            integrity_policy_version="generated-result-sha256.v1",
            validation_policy_version=facts.batch.safety_policy_version,
            created_at=now,
            validation_transfer_policy_version="generation-controlled.v1",
            validation_transfer_policy_snapshot_sha256=commit.moderation_decision_sha256,
            generation_provider_call_id=provider_call.id,
        )
        object_fact = AssetObject(
            id=ids.asset_object_id,
            workspace_id=facts.batch.workspace_id,
            asset_version_id=asset_version.id,
            role="CONTROLLED_ORIGINAL",
            backend=stat.backend,
            location=stat.reference.location,
            bucket=stat.bucket,
            key=stat.reference.key,
            provider_version_id=version_id,
            etag=stat.etag,
            byte_size=stat.content_length,
            sha256=result.content_sha256,
            state=AssetObjectState.CONTROLLED,
            version=1,
            created_at=now,
            updated_at=now,
        )
        candidate = CandidateImage(
            id=ids.candidate_image_id,
            workspace_id=facts.batch.workspace_id,
            workflow_id=facts.batch.workflow_id,
            generation_batch_id=facts.batch.id,
            candidate_slot_id=facts.slot.id,
            task_asset_version_id=asset_version.id,
            content_sha256=result.content_sha256,
            width=result.width,
            height=result.height,
            image_format=result.media_type.name,
            source_asset_version_ids=facts.batch.authorized_asset_version_ids,
            creative_plan_version_id=facts.batch.creative_plan_version_id,
            prompt_sha256=facts.batch.prompt_sha256,
            context_sha256=facts.batch.context_sha256,
            retrieval_snapshot_sha256=canonical_hash(
                {
                    "retrieval_run_id": facts.creative_plan.provenance.retrieval_run_id,
                    "citation_ids": facts.creative_plan.provenance.retrieval_citation_ids,
                }
            ),
            endpoint_capability_version_id=provider_call.endpoint_capability_version_id,
            provider_call_id=provider_call.id,
            provider_request_id_sha256=provider_request_sha256,
            moderation_decision_sha256=commit.moderation_decision_sha256,
            usage_record_id=usage.id,
            created_at=now,
            retention_deadline=facts.batch.retention_deadline,
        )
        return asset, asset_version, object_fact, candidate


class _ResultFactsOnlyBuilder:
    def build(self, _facts: GenerationDispatchFacts) -> AuthorizedGenerationDispatch:
        raise RuntimeError("result convergence cannot construct a Provider dispatch")


class MySqlGenerationDispatchAttemptCoordinator:
    """Persist a one-way no-resubmit fence before each external Provider call."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def claim(
        self,
        dispatch: AuthorizedGenerationDispatch,
    ) -> GenerationDispatchAttemptClaim:
        if not isinstance(dispatch, AuthorizedGenerationDispatch):
            raise ValueError("generation dispatch Attempt requires an authorized dispatch")
        depth_token = enter_unit_of_work()
        try:
            try:
                return self._claim_once(dispatch)
            except UniqueConstraintError:
                return self._load_existing_claim(dispatch)
        finally:
            exit_unit_of_work(depth_token)

    def _claim_once(
        self,
        dispatch: AuthorizedGenerationDispatch,
    ) -> GenerationDispatchAttemptClaim:
        operation_request = dispatch.operation
        attempt_id = generation_provider_call_id(operation_request)
        with self._session_factory() as session, session.begin():
            existing = session.get(
                GenerationDispatchAttemptModel,
                (operation_request.workspace_id, attempt_id),
                with_for_update=True,
            )
            if existing is not None:
                return self._claim_from_model(existing, dispatch=dispatch)
            now = MySqlGenerationDispatchAuthority._database_now(session)
            self._validate_operation_snapshot(
                session=session,
                request=operation_request,
                now=now,
            )
            session.add(
                GenerationDispatchAttemptModel(
                    workspace_id=operation_request.workspace_id,
                    id=attempt_id,
                    candidate_slot_id=operation_request.target_id,
                    durable_operation_id=operation_request.operation_id,
                    operation_attempt=operation_request.attempt_count,
                    call_index=0,
                    endpoint_capability_version_id=(dispatch.endpoint_capability_version_id),
                    request_sha256=dispatch.request_sha256,
                    adapter_configuration_sha256=(dispatch.adapter_configuration_sha256),
                    idempotency_key_sha256=hashlib.sha256(
                        operation_request.idempotency_key.encode("utf-8")
                    ).hexdigest(),
                    state="DISPATCHING",
                    outcome=None,
                    provider_request_id=None,
                    provider_request_id_sha256=None,
                    provider_task_id=None,
                    provider_task_id_sha256=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            flush_with_integrity_classification(session)
        return GenerationDispatchAttemptClaim(
            attempt_id=attempt_id,
            submit_authorized=True,
        )

    def _load_existing_claim(
        self,
        dispatch: AuthorizedGenerationDispatch,
    ) -> GenerationDispatchAttemptClaim:
        operation_request = dispatch.operation
        attempt_id = generation_provider_call_id(operation_request)
        with self._session_factory() as session, session.begin():
            existing = session.get(
                GenerationDispatchAttemptModel,
                (operation_request.workspace_id, attempt_id),
                with_for_update=True,
            )
            if existing is None:
                raise ConcurrencyError("generation dispatch Attempt identity conflicted")
            return self._claim_from_model(existing, dispatch=dispatch)

    def record_outcome(
        self,
        *,
        claim: GenerationDispatchAttemptClaim,
        dispatch: AuthorizedGenerationDispatch,
        outcome: NormalizedImageProviderOutcome,
    ) -> None:
        if (
            not isinstance(claim, GenerationDispatchAttemptClaim)
            or not claim.submit_authorized
            or not isinstance(dispatch, AuthorizedGenerationDispatch)
            or not isinstance(outcome, NormalizedImageProviderOutcome)
        ):
            raise ValueError("generation dispatch outcome record is invalid")
        operation_request = dispatch.operation
        expected_attempt_id = generation_provider_call_id(operation_request)
        if claim.attempt_id != expected_attempt_id:
            raise ConcurrencyError("generation dispatch Attempt claim is stale")
        depth_token = enter_unit_of_work()
        try:
            with self._session_factory() as session, session.begin():
                model = session.get(
                    GenerationDispatchAttemptModel,
                    (operation_request.workspace_id, claim.attempt_id),
                    with_for_update=True,
                )
                if model is None:
                    raise ConcurrencyError("generation dispatch Attempt does not exist")
                self._validate_model(model, dispatch=dispatch)
                request_id, task_id = self._outcome_identity(outcome)
                self._retain_first_identity(
                    existing=model.provider_request_id,
                    observed=request_id,
                    identity_name="Request",
                )
                self._retain_first_identity(
                    existing=model.provider_task_id,
                    observed=task_id,
                    identity_name="Task",
                )
                if model.provider_request_id is None and request_id is not None:
                    model.provider_request_id = request_id
                    model.provider_request_id_sha256 = hashlib.sha256(
                        request_id.encode("utf-8")
                    ).hexdigest()
                if model.provider_task_id is None and task_id is not None:
                    model.provider_task_id = task_id
                    model.provider_task_id_sha256 = hashlib.sha256(
                        task_id.encode("utf-8")
                    ).hexdigest()
                model.state = "OUTCOME_RECORDED"
                model.outcome = outcome.call_outcome.value
                model.updated_at = MySqlGenerationDispatchAuthority._database_now(session)
                flush_with_integrity_classification(session)
        finally:
            exit_unit_of_work(depth_token)

    @staticmethod
    def _validate_operation_snapshot(
        *,
        session: Session,
        request: OperationExecutionRequest,
        now: datetime,
    ) -> None:
        operation = OperationRepository(session).get(
            request.operation_id,
            workspace_id=request.workspace_id,
            for_update=True,
        )
        if operation is None:
            raise ConcurrencyError("generation dispatch Operation does not exist")
        expected = (
            request.operation_id,
            request.workspace_id,
            request.kind,
            request.target_type,
            request.target_id,
            request.target_version,
            request.input_hash,
            request.input_ref,
            request.provider_request_id,
            request.attempt_count,
            request.execution_version,
            request.lease_token,
            request.lease_expires_at,
            request.replay_source_dead_letter_id,
            request.replay_attempt,
        )
        actual = (
            operation.id,
            operation.workspace_id,
            operation.kind,
            operation.target_type,
            operation.target_id,
            operation.target_version,
            operation.input_hash,
            operation.input_ref,
            operation.provider_request_id,
            operation.attempt_count,
            operation.version,
            operation.lease_token,
            operation.lease_expires_at,
            operation.replay_source_dead_letter_id,
            operation.replay_attempt,
        )
        if (
            actual != expected
            or operation.state is not OperationState.RUNNING
            or operation.lease_expires_at is None
            or operation.lease_expires_at <= now
            or operation.execution_deadline_at <= now
        ):
            raise ConcurrencyError("generation dispatch Operation lease is no longer current")

    @classmethod
    def _claim_from_model(
        cls,
        model: GenerationDispatchAttemptModel,
        *,
        dispatch: AuthorizedGenerationDispatch,
    ) -> GenerationDispatchAttemptClaim:
        cls._validate_model(model, dispatch=dispatch)
        return GenerationDispatchAttemptClaim(
            attempt_id=model.id,
            submit_authorized=False,
            provider_request_id=model.provider_request_id,
            provider_task_id=model.provider_task_id,
        )

    @staticmethod
    def _validate_model(
        model: GenerationDispatchAttemptModel,
        *,
        dispatch: AuthorizedGenerationDispatch,
    ) -> None:
        operation = dispatch.operation
        expected = (
            operation.workspace_id,
            generation_provider_call_id(operation),
            operation.target_id,
            operation.operation_id,
            operation.attempt_count,
            0,
            dispatch.endpoint_capability_version_id,
            dispatch.request_sha256,
            dispatch.adapter_configuration_sha256,
            hashlib.sha256(operation.idempotency_key.encode("utf-8")).hexdigest(),
        )
        actual = (
            model.workspace_id,
            model.id,
            model.candidate_slot_id,
            model.durable_operation_id,
            model.operation_attempt,
            model.call_index,
            model.endpoint_capability_version_id,
            model.request_sha256,
            model.adapter_configuration_sha256,
            model.idempotency_key_sha256,
        )
        if actual != expected:
            raise ConcurrencyError("generation dispatch Attempt facts do not match")

    @staticmethod
    def _outcome_identity(
        outcome: NormalizedImageProviderOutcome,
    ) -> tuple[str | None, str | None]:
        identity = outcome.identity
        if identity is None:
            return None, None
        request_id = (
            identity.provider_request_id if isinstance(identity.provider_request_id, str) else None
        )
        task_id = identity.provider_task_id if isinstance(identity.provider_task_id, str) else None
        return request_id, task_id

    @staticmethod
    def _retain_first_identity(
        *,
        existing: str | None,
        observed: str | None,
        identity_name: str,
    ) -> None:
        if existing is not None and existing != observed:
            raise ConcurrencyError(f"generation Provider identity changed for {identity_name}")


class MySqlGenerationWorkflowContinuationAuthority:
    """Authorize evaluation only when an exact Generation Batch is fully converged."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def claim_ready_batch(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        generation_batch_id: str,
        candidate_slot_id: str,
        candidate_image_id: str,
        asset_version_id: str,
        operation_id: str,
        usage_record_id: str,
    ) -> GenerationWorkflowContinuationClaim | None:
        depth_token = enter_unit_of_work()
        try:
            with self._session_factory() as session, session.begin():
                now = MySqlGenerationDispatchAuthority._database_now(session)
                workflow = session.scalar(
                    select(WorkflowModel)
                    .where(
                        WorkflowModel.workspace_id == workspace_id,
                        WorkflowModel.id == workflow_id,
                    )
                    .with_for_update()
                )
                if workflow is None:
                    raise ConcurrencyError("generation Workflow does not exist")
                if (
                    workflow.retention_status != "ACTIVE"
                    or workflow.cancellation_requested_at is not None
                    or workflow.expires_at <= now
                ):
                    raise ConcurrencyError("generation Workflow is no longer eligible")

                batch = session.get(
                    GenerationBatchModel,
                    (workspace_id, generation_batch_id),
                    with_for_update=True,
                )
                if (
                    batch is None
                    or batch.workflow_id != workflow_id
                    or batch.retention_deadline <= now
                    or batch.workflow_deadline <= now
                ):
                    raise ConcurrencyError("Generation Batch is no longer current")

                slot = session.get(
                    CandidateSlotModel,
                    (workspace_id, candidate_slot_id),
                    with_for_update=True,
                )
                operation = session.scalar(
                    select(DurableOperationModel)
                    .where(
                        DurableOperationModel.workspace_id == workspace_id,
                        DurableOperationModel.id == operation_id,
                    )
                    .with_for_update()
                )
                candidate = session.get(
                    CandidateImageModel,
                    (workspace_id, candidate_image_id),
                    with_for_update=True,
                )
                usage = session.get(
                    UsageRecordModel,
                    (workspace_id, usage_record_id),
                    with_for_update=True,
                )
                if (
                    slot is None
                    or operation is None
                    or candidate is None
                    or usage is None
                    or slot.generation_batch_id != batch.id
                    or slot.durable_operation_id != operation.id
                    or candidate.workflow_id != workflow.id
                    or candidate.generation_batch_id != batch.id
                    or candidate.candidate_slot_id != slot.id
                    or candidate.task_asset_version_id != asset_version_id
                    or candidate.usage_record_id != usage.id
                    or usage.durable_operation_id != operation.id
                    or operation.state != OperationState.SUCCEEDED.value
                ):
                    raise ConcurrencyError(
                        "generation Candidate Ready event does not match MySQL authority"
                    )

                slots = tuple(
                    session.scalars(
                        select(CandidateSlotModel)
                        .where(
                            CandidateSlotModel.workspace_id == workspace_id,
                            CandidateSlotModel.generation_batch_id == batch.id,
                        )
                        .order_by(CandidateSlotModel.candidate_index)
                        .with_for_update()
                    )
                )
                if len(slots) != batch.candidate_count or tuple(
                    item.candidate_index for item in slots
                ) != tuple(range(batch.candidate_count)):
                    raise ConcurrencyError("Generation Batch slot authority is inconsistent")

                candidates = tuple(
                    session.scalars(
                        select(CandidateImageModel)
                        .where(
                            CandidateImageModel.workspace_id == workspace_id,
                            CandidateImageModel.generation_batch_id == batch.id,
                        )
                        .with_for_update()
                    )
                )
                candidates_by_slot = {item.candidate_slot_id: item for item in candidates}
                operation_states = {
                    operation_id: state
                    for operation_id, state in session.execute(
                        select(DurableOperationModel.id, DurableOperationModel.state).where(
                            DurableOperationModel.workspace_id == workspace_id,
                            DurableOperationModel.id.in_(
                                tuple(item.durable_operation_id for item in slots)
                            ),
                        )
                    ).tuples()
                }
                if len(candidates_by_slot) < batch.candidate_count or any(
                    operation_states.get(item.durable_operation_id)
                    != OperationState.SUCCEEDED.value
                    for item in slots
                ):
                    return None
                if len(candidates_by_slot) != batch.candidate_count:
                    raise ConcurrencyError("Generation Batch Candidate authority is inconsistent")

                evaluation_step = session.scalar(
                    select(WorkflowStepModel)
                    .where(
                        WorkflowStepModel.workflow_id == workflow.id,
                        WorkflowStepModel.step_key
                        == f"evaluate_results:generation-batch:{batch.id}",
                    )
                    .with_for_update()
                )
                latest_evaluation_step = session.scalar(
                    select(WorkflowStepModel)
                    .where(
                        WorkflowStepModel.workflow_id == workflow.id,
                        WorkflowStepModel.step_type == "EVALUATE_RESULTS",
                    )
                    .order_by(WorkflowStepModel.sequence.desc())
                    .limit(1)
                    .with_for_update()
                )

                if workflow.status == "GENERATING":
                    if workflow.version != batch.workflow_version:
                        current_batch_exists = session.scalar(
                            select(
                                exists().where(
                                    GenerationBatchModel.workspace_id == workspace_id,
                                    GenerationBatchModel.workflow_id == workflow.id,
                                    GenerationBatchModel.workflow_version == workflow.version,
                                )
                            )
                        )
                        if current_batch_exists:
                            return None
                        raise ConcurrencyError(
                            "Generation Batch does not match the current Workflow version"
                        )
                elif workflow.status == "EVALUATING" and workflow.current_node == (
                    "evaluate_results"
                ):
                    if evaluation_step is None or evaluation_step.status not in {
                        "QUEUED",
                        "CLAIMED",
                        "RUNNING",
                        "RETRYABLE_FAILED",
                    }:
                        raise ConcurrencyError(
                            "Generation Batch evaluation continuation is unavailable"
                        )
                elif (
                    workflow.status == "AWAITING_RESULT_APPROVAL"
                    and workflow.current_node == "approve_results"
                ):
                    if (
                        evaluation_step is None
                        or evaluation_step.status != "SUCCEEDED"
                        or latest_evaluation_step is None
                        or latest_evaluation_step.id != evaluation_step.id
                    ):
                        return None
                elif workflow.status in {
                    "REPAIRING",
                    "EXPORTING",
                    "COMPLETED",
                    "FAILED",
                }:
                    return None
                else:
                    raise ConcurrencyError("generation Workflow is no longer eligible")

                plan_version = session.get(
                    CreativePlanVersionModel,
                    (workspace_id, batch.creative_plan_version_id),
                )
                if plan_version is None or plan_version.workflow_id != workflow.id:
                    raise ConcurrencyError("Generation Batch Creative Plan does not exist")
                plan = session.get(
                    CreativePlanModel,
                    (workspace_id, plan_version.creative_plan_id),
                )
                if (
                    plan is None
                    or plan.workflow_id != workflow.id
                    or plan.current_version_id != plan_version.id
                    or plan.current_version_number != plan_version.version_number
                ):
                    raise ConcurrencyError("Generation Batch Creative Plan is no longer current")

                candidate_refs = tuple(
                    f"mysql://candidate-images/{candidates_by_slot[item.id].id}" for item in slots
                )
                batch_order = tuple(
                    session.scalars(
                        select(GenerationBatchModel.id)
                        .where(
                            GenerationBatchModel.workspace_id == workspace_id,
                            GenerationBatchModel.workflow_id == workflow.id,
                        )
                        .order_by(GenerationBatchModel.created_at, GenerationBatchModel.id)
                    )
                )
                try:
                    generation_iteration = batch_order.index(batch.id)
                except ValueError:
                    raise ConcurrencyError(
                        "Generation Batch iteration authority is inconsistent"
                    ) from None
                return GenerationWorkflowContinuationClaim(
                    workspace_id=workspace_id,
                    workflow_id=workflow.id,
                    workflow_version=workflow.version,
                    actor_id=workflow.created_by,
                    input_data=dict(workflow.input_json),
                    generation_batch_id=batch.id,
                    creative_plan_id=plan.id,
                    creative_plan_version_id=plan_version.id,
                    creative_plan_version=plan_version.version_number,
                    generation_iteration=generation_iteration,
                    candidate_refs=candidate_refs,
                )
        finally:
            exit_unit_of_work(depth_token)


@dataclass(frozen=True, slots=True)
class _GenerationResultIds:
    provider_call_id: str
    usage_record_id: str
    asset_id: str
    asset_version_id: str
    asset_object_id: str
    candidate_image_id: str


def _generation_result_ids(*, operation: OperationExecutionRequest) -> _GenerationResultIds:
    slot_namespace = UUID(operation.target_id)
    provider_call_id = generation_provider_call_id(operation)
    asset_id = str(uuid5(slot_namespace, "asset"))
    asset_version_id = str(uuid5(UUID(asset_id), "version:1"))
    return _GenerationResultIds(
        provider_call_id=provider_call_id,
        usage_record_id=str(uuid5(UUID(provider_call_id), "usage")),
        asset_id=asset_id,
        asset_version_id=asset_version_id,
        asset_object_id=str(uuid5(UUID(asset_version_id), "controlled-object")),
        candidate_image_id=str(uuid5(slot_namespace, "candidate-image")),
    )


def _estimated_quantity(
    pricing_unit: ProviderPricingUnit, commit: GenerationSuccessCommit
) -> Decimal:
    result = commit.provider_outcome.result
    assert result is not None
    if pricing_unit is ProviderPricingUnit.IMAGE:
        return Decimal("1.000000")
    raise ValueError("generation pricing unit is unsupported")


def _provider_call_to_model(value: ProviderCall) -> ProviderCallModel:
    return ProviderCallModel(
        workspace_id=value.workspace_id,
        id=value.id,
        candidate_slot_id=value.candidate_slot_id,
        durable_operation_id=value.durable_operation_id,
        operation_attempt=value.operation_attempt,
        call_index=value.call_index,
        route_decision_sha256=value.route_decision_sha256,
        endpoint_capability_version_id=value.endpoint_capability_version_id,
        provider=value.provider,
        model=value.model,
        request_sha256=value.request_sha256,
        idempotency_key_sha256=value.idempotency_key_sha256,
        outcome=value.outcome.value,
        possible_dispatch=value.possible_dispatch,
        provider_request_id_sha256=value.provider_request_id_sha256,
        latency_ms=value.latency_ms,
        observed_at=value.observed_at,
    )


def _usage_record_to_model(value: UsageRecord) -> UsageRecordModel:
    return UsageRecordModel(
        **{
            field: getattr(value, field)
            for field in (
                "workspace_id",
                "id",
                "provider_call_id",
                "provider_call_identity_sha256",
                "durable_operation_id",
                "operation_attempt",
                "provider",
                "model",
                "endpoint_capability_version_id",
                "estimated_quantity",
                "provider_reported_quantity",
                "configured_unit_price",
                "estimated_amount",
                "actual_amount",
                "currency",
                "unit_price_version",
                "provider_usage_evidence_sha256",
                "pricing_evidence_sha256",
                "final_cost_evidence_sha256",
                "latency_ms",
                "recorded_at",
            )
        },
        pricing_unit=value.pricing_unit.value,
        resolution_status=value.resolution_status.value,
        evidence_source=value.evidence_source.value,
    )


def _candidate_image_to_model(value: CandidateImage) -> CandidateImageModel:
    return CandidateImageModel(
        workspace_id=value.workspace_id,
        id=value.id,
        workflow_id=value.workflow_id,
        generation_batch_id=value.generation_batch_id,
        candidate_slot_id=value.candidate_slot_id,
        task_asset_version_id=value.task_asset_version_id,
        content_sha256=value.content_sha256,
        width=value.width,
        height=value.height,
        image_format=value.image_format,
        source_asset_version_ids_json=list(value.source_asset_version_ids),
        creative_plan_version_id=value.creative_plan_version_id,
        prompt_sha256=value.prompt_sha256,
        context_sha256=value.context_sha256,
        retrieval_snapshot_sha256=value.retrieval_snapshot_sha256,
        endpoint_capability_version_id=value.endpoint_capability_version_id,
        provider_call_id=value.provider_call_id,
        provider_request_id_sha256=value.provider_request_id_sha256,
        moderation_decision_sha256=value.moderation_decision_sha256,
        usage_record_id=value.usage_record_id,
        created_at=value.created_at,
        retention_deadline=value.retention_deadline,
    )


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


def _slot_from_model(model: CandidateSlotModel) -> CandidateSlot:
    return CandidateSlot(
        id=model.id,
        workspace_id=model.workspace_id,
        generation_batch_id=model.generation_batch_id,
        candidate_index=model.candidate_index,
        durable_operation_id=model.durable_operation_id,
        operation_kind=OperationKind(model.operation_kind),
        logical_identity_sha256=model.logical_identity_sha256,
        operation_idempotency_key=model.operation_idempotency_key,
    )


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
        return tuple(_slot_from_model(model) for model in models)


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
