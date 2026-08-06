from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from threading import Barrier
from types import SimpleNamespace

import pytest
from commercevision_api.errors import install_error_handlers
from commercevision_api.generation_routes import router as generation_router
from commercevision_application import (
    ApprovedGenerationAuthority,
    ApprovedPlanGenerationCommand,
    ApprovedPlanGenerationService,
    AuthenticatedPrincipal,
    AuthorizedGenerationDispatch,
    DurableNodeLifecycle,
    GenerationDispatchAttemptClaim,
    GenerationDispatchFacts,
    GenerationSuccessCommit,
    ModelRouterApplicationService,
    OperationApplicationService,
    OperationExecutionFailure,
    OperationExecutionRequest,
    OperationExecutionResult,
    StructuredGenerationDispatchBuilder,
    ToolAuthorizationEntitlements,
    ToolAuthorizationPolicy,
    UnknownOperationOutcome,
)
from commercevision_contracts.image_provider import (
    ImageGenerationProviderRequest,
    ImageProviderCallOutcome,
    ImageProviderMediaRequirements,
    ImageProviderMediaType,
    ImageProviderOutputFormat,
    ImageProviderRequestIdentity,
    ImageProviderResult,
    ImageProviderTaskState,
    ImageProviderUsage,
    ImageProviderUsageUnit,
    NormalizedImageProviderOutcome,
)
from commercevision_contracts.object_storage import (
    ObjectReference,
    ObjectStat,
    ServerSideEncryptionState,
)
from commercevision_domain import (
    CircuitState,
    ConcurrencyError,
    CreativePlanDirection,
    CreativePlanPayload,
    ImageRole,
    StepType,
    StorageBackend,
    StorageLocationClass,
    ToolIntentProposal,
    WorkflowStatus,
)
from commercevision_persistence import (
    MySqlApprovedGenerationAuthority,
    MySqlGenerationDispatchAttemptCoordinator,
    MySqlGenerationDispatchAuthority,
    MySqlGenerationResultConverger,
    MySqlGenerationWorkflowContinuationAuthority,
    SqlAlchemyApprovedGenerationUnitOfWork,
    SqlAlchemyModelRouterUnitOfWork,
    SqlAlchemyOperationUnitOfWork,
    SqlAlchemyUnitOfWork,
    is_unit_of_work_active,
)
from commercevision_persistence.generation_models import (
    CandidateImageModel,
    CandidateSlotModel,
    GenerationBatchModel,
    GenerationDispatchAttemptModel,
    ProviderCallModel,
    UsageRecordModel,
)
from commercevision_persistence.model_router_models import ModelRouteDecisionModel
from commercevision_persistence.models import (
    ApprovalModel,
    AssetVersionModel,
    AuditEventModel,
    DurableOperationModel,
    IdempotencyKeyModel,
    OutboxEventModel,
    WorkflowModel,
)
from commercevision_persistence.provider_control_plane_models import (
    ProviderEndpointObservationModel,
)
from commercevision_tool_runtime import (
    ToolCostClass,
    ToolDefinition,
    ToolIntentAuthorizer,
    ToolRegistry,
)
from commercevision_tool_runtime.fixture import fixture_image_intent_definition
from commercevision_worker.generation import (
    AtomicGenerationProviderDispatcher,
    GenerationOperationExecutor,
)
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import DBAPIError
from test_model_router_mysql import (
    _database_now,
    _request,
    _seed_route_authority,
)

pytestmark = pytest.mark.integration

WORKSPACE_ID = "phase4-router-mysql"
WORKFLOW_ID = "019b0000-0000-7000-8000-000000000701"
PLAN_ID = "019b0000-0000-7000-8000-000000000702"
PLAN_VERSION_ID = "019b0000-0000-7000-8000-000000000703"
APPROVAL_ID = "019b0000-0000-7000-8000-000000000704"
POLICY_VERSION_ID = "019b0000-0000-7000-8000-000000000705"
CAPABILITY_VERSION_ID = "019b0000-0000-7000-8000-000000000706"
OBSERVATION_ID = "019b0000-0000-7000-8000-000000000707"
SOURCE_ASSET_ID = "019b0000-0000-7000-8000-000000000720"
SOURCE_ASSET_VERSION_ID = "019b0000-0000-7000-8000-000000000721"
SOURCE_RIGHTS_RECORD_ID = "019b0000-0000-7000-8000-000000000722"
REVOKED_RIGHTS_RECORD_ID = "019b0000-0000-7000-8000-000000000723"


class _GenerationSourceIntentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    count: int = Field(ge=1, le=16)
    source_asset_version_id: str


def _generation_source_resources(arguments: BaseModel) -> tuple[str, ...]:
    assert isinstance(arguments, _GenerationSourceIntentInput)
    return (arguments.source_asset_version_id,)


def _generation_plan_payload(
    *,
    source_asset_version_id: str | None = None,
    candidate_count: int = 1,
) -> CreativePlanPayload:
    arguments: dict[str, object] = {"count": candidate_count}
    if source_asset_version_id is not None:
        arguments["source_asset_version_id"] = source_asset_version_id
    return CreativePlanPayload(
        directions=(
            CreativePlanDirection(
                key="main-image",
                image_role=ImageRole.MAIN,
                scene="Clean product studio",
                composition="Centered hero composition",
                camera="Front three-quarter view",
                lighting="Soft commercial key light",
                color_direction="Neutral brand-safe palette",
                product_constraints=("Preserve exact product geometry",),
                required_elements=("Single product",),
                prohibited_elements=(),
                citation_selections=(),
                candidate_count=candidate_count,
                quality_targets=("Sharp product detail",),
                repair_scope=(),
                tool_intents=(
                    ToolIntentProposal.create(
                        intent_key="generate-main-image",
                        tool_name="provider-a.image.generate",
                        schema_version="1.0",
                        purpose="Generate the approved main image candidate",
                        arguments=arguments,
                        estimated_cost_units=1,
                    ),
                ),
            ),
        )
    )


def _seed_generation_source_asset(
    integration_database,
    *,
    now: datetime,
) -> tuple[str, datetime]:
    rights_deadline = now + timedelta(hours=2)
    parameters = {
        "workspace_id": WORKSPACE_ID,
        "asset_id": SOURCE_ASSET_ID,
        "asset_version_id": SOURCE_ASSET_VERSION_ID,
        "rights_record_id": SOURCE_RIGHTS_RECORD_ID,
        "upload_session_id": "019b0000-0000-7000-8000-000000000723",
        "now": now.replace(tzinfo=None),
        "valid_from": (now - timedelta(hours=1)).replace(tzinfo=None),
        "valid_until": rights_deadline.replace(tzinfo=None),
    }
    with integration_database.engine.begin() as connection:
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        connection.execute(
            text(
                "INSERT INTO assets "
                "(id, workspace_id, retention_class, asset_kind, workflow_id, product_id, "
                "sku_id, status, block_reason, current_version_id, current_rights_record_id, "
                "retention_deadline, version, created_at, updated_at) VALUES "
                "(:asset_id, :workspace_id, 'FOUNDATION', 'IMAGE', NULL, NULL, NULL, "
                "'AVAILABLE', NULL, :asset_version_id, :rights_record_id, NULL, 1, :now, :now)"
            ),
            parameters,
        )
        connection.execute(
            text(
                "INSERT INTO asset_versions "
                "(id, workspace_id, asset_id, version_number, upload_session_id, filename, "
                "sha256, byte_size, declared_mime, detected_mime, image_format, width, height, "
                "frame_count, category, role, integrity_policy_version, validation_policy_version, "
                "validation_transfer_policy_version, validation_transfer_policy_snapshot_sha256, "
                "created_at) VALUES (:asset_version_id, :workspace_id, :asset_id, 1, "
                ":upload_session_id, 'source.png', :asset_sha256, 128, 'image/png', 'image/png', "
                "'PNG', 64, 64, 1, 'general-merchandise', 'REFERENCE', 'integrity-v1', "
                "'validation-v1', 'transfer-v1', :transfer_sha256, :now)"
            ),
            parameters | {"asset_sha256": "8" * 64, "transfer_sha256": "9" * 64},
        )
        connection.execute(
            text(
                "INSERT INTO rights_records "
                "(id, workspace_id, asset_id, asset_version_id, version_number, decision, "
                "owner_reference, source, license_reference, derivative_allowed, "
                "public_demo_allowed, evidence_reference, terms_sha256, valid_from, valid_until, "
                "perpetual, supersedes_record_id, created_by, created_at, permissions_sealed_at) "
                "VALUES (:rights_record_id, :workspace_id, :asset_id, :asset_version_id, 1, "
                "'GRANT', 'owner', 'contract', 'license-1', 1, 0, 'evidence://license-1', "
                ":terms_sha256, :valid_from, :valid_until, 0, NULL, 'rights-admin', :now, NULL)"
            ),
            parameters | {"terms_sha256": "a" * 64},
        )
        connection.execute(
            text(
                "INSERT INTO rights_record_uses "
                "(workspace_id, asset_id, rights_record_id, allowed_use, created_at) VALUES "
                "(:workspace_id, :asset_id, :rights_record_id, 'IMAGE_GENERATION', :now)"
            ),
            parameters,
        )
        connection.execute(
            text(
                "INSERT INTO rights_record_providers "
                "(workspace_id, asset_id, rights_record_id, allowed_provider, created_at) VALUES "
                "(:workspace_id, :asset_id, :rights_record_id, 'provider-a', :now)"
            ),
            parameters,
        )
        connection.execute(
            text(
                "UPDATE rights_records SET permissions_sealed_at = :now "
                "WHERE id = :rights_record_id"
            ),
            parameters,
        )
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
    return SOURCE_ASSET_VERSION_ID, rights_deadline


def _production_generation_service(
    integration_database,
    *,
    definition: ToolDefinition,
    authorized_asset_version_ids: frozenset[str],
    rights_policy_version: str,
    remaining_budget_units: int = 1,
) -> ApprovedPlanGenerationService:
    return ApprovedPlanGenerationService(
        lambda: SqlAlchemyApprovedGenerationUnitOfWork(
            integration_database.session_factory,
            lambda session: _approved_generation_authority(
                session,
                definition=definition,
                authorized_asset_version_ids=authorized_asset_version_ids,
                rights_policy_version=rights_policy_version,
                remaining_budget_units=remaining_budget_units,
            ),
        )
    )


def _approved_generation_authority(
    session,
    *,
    definition: ToolDefinition,
    authorized_asset_version_ids: frozenset[str],
    rights_policy_version: str,
    remaining_budget_units: int = 1,
) -> MySqlApprovedGenerationAuthority:
    return MySqlApprovedGenerationAuthority(
        session,
        authorizer=ToolIntentAuthorizer(
            registry=ToolRegistry([definition]),
            policy_version="tool-intent-policy-v1",
        ),
        policy=ToolAuthorizationPolicy(
            version="tool-intent-policy-v1",
            node="execute_tool",
        ),
        entitlements=ToolAuthorizationEntitlements(
            granted_scopes=frozenset({"image.generate"}),
            authorized_resource_ids=authorized_asset_version_ids,
            allowed_providers=frozenset({"provider-a"}),
            allowed_cost_classes=frozenset({ToolCostClass.LOW}),
            remaining_quota_units=1,
            remaining_budget_units=remaining_budget_units,
        ),
        generation_tools={("provider-a.image.generate", "1.0"): "provider-a"},
        rights_policy_version=rights_policy_version,
        safety_policy_version="media-safety.v1",
        actor_id="generation-service",
    )


def _start_running_generation_dispatch(integration_database, *, suffix: str):
    payload = _generation_plan_payload()
    now = _seed_route_authority(
        integration_database,
        plan_payload=payload,
        workflow_version=2,
        workflow_node="approve_plan",
        approval_expected_workflow_version=1,
        approval_decision="APPROVE",
    )
    route = ModelRouterApplicationService(
        lambda: SqlAlchemyModelRouterUnitOfWork(integration_database.session_factory)
    ).route(
        request=_request(now),
        policy_key="default-images",
        actor_id="generation-service",
        idempotency_key=f"test:generation-dispatch-route-{suffix}",
        trace_id=f"trace-generation-dispatch-route-{suffix}",
    )
    definition = replace(
        fixture_image_intent_definition(),
        name="provider-a.image.generate",
        provider="provider-a",
    )
    result = _production_generation_service(
        integration_database,
        definition=definition,
        authorized_asset_version_ids=frozenset(),
        rights_policy_version="rights-none.v1",
    ).start(
        command=ApprovedPlanGenerationCommand(
            workspace_id=WORKSPACE_ID,
            workflow_id=WORKFLOW_ID,
            expected_workflow_version=2,
            creative_plan_id=PLAN_ID,
            creative_plan_version_id=PLAN_VERSION_ID,
            creative_plan_version=1,
            approval_id=APPROVAL_ID,
            direction_key="main-image",
            tool_intent_key="generate-main-image",
            route_decision_sha256=route.decision.decision_sha256,
        ),
        idempotency_key=f"test:generation-dispatch-command-{suffix}",
        trace_id=f"trace-generation-dispatch-command-{suffix}",
    )
    operations = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    with integration_database.session_factory() as session:
        claimed_at = _database_now(session)
    lease_token = operations.claim(
        workspace_id=WORKSPACE_ID,
        operation_id=result.operations[0].id,
        owner="generation-worker-1",
        lease_duration=timedelta(minutes=2),
        now=claimed_at,
    )
    running = operations.start(
        workspace_id=WORKSPACE_ID,
        operation_id=result.operations[0].id,
        lease_token=lease_token,
        now=claimed_at,
    )
    return definition, result, running, claimed_at


def _generation_http_app(service: ApprovedPlanGenerationService) -> FastAPI:
    class _Resolver:
        @staticmethod
        def resolve(_: str | None) -> AuthenticatedPrincipal:
            return AuthenticatedPrincipal(
                actor_id="generation-service",
                workspace_ids=frozenset({WORKSPACE_ID, "foreign-generation-workspace"}),
                admin_workspace_ids=frozenset(),
            )

    class _Access:
        @staticmethod
        def require_workspace(*, workspace_id: str, principal: AuthenticatedPrincipal) -> None:
            assert workspace_id in principal.workspace_ids

    app = FastAPI()
    install_error_handlers(app)
    app.state.container = SimpleNamespace(
        principal_resolver=_Resolver(),
        access_policy=_Access(),
        generation=service,
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = "request-generation-mysql"
        request.state.trace_id = "trace-generation-mysql"
        return await call_next(request)

    app.include_router(generation_router)
    return app


class _ExactGenerationAuthority:
    def __init__(self, authority: ApprovedGenerationAuthority) -> None:
        self._authority = authority

    def load_current_authority(
        self, command: ApprovedPlanGenerationCommand
    ) -> ApprovedGenerationAuthority:
        del command
        return self._authority


class _NeverGenerationDispatchBuilder:
    def __init__(self) -> None:
        self.calls = 0

    def build(self, _facts: object) -> object:
        self.calls += 1
        raise AssertionError("stale authority must be denied before request construction")


class _NeverGenerationProviderDispatcher:
    def __init__(self) -> None:
        self.calls = 0

    def submit(self, _dispatch: object) -> object:
        self.calls += 1
        raise AssertionError("stale authority must be denied before Provider dispatch")


class _CapturingGenerationDispatchBuilder:
    def __init__(self) -> None:
        self.facts: list[GenerationDispatchFacts] = []
        self._builder = StructuredGenerationDispatchBuilder()

    def build(self, facts: GenerationDispatchFacts) -> AuthorizedGenerationDispatch:
        assert is_unit_of_work_active() is False
        self.facts.append(facts)
        return self._builder.build(facts)


class _CapturingGenerationProviderDispatcher:
    def __init__(self) -> None:
        self.dispatches: list[AuthorizedGenerationDispatch] = []

    def submit(self, dispatch: AuthorizedGenerationDispatch) -> OperationExecutionResult:
        assert is_unit_of_work_active() is False
        self.dispatches.append(dispatch)
        return OperationExecutionResult(
            operation_id=dispatch.operation_id,
            output_ref="provider-result://generation-test",
            provider_request_id="provider-request-generation-test",
        )


class _BarrierGenerationAuthority(_ExactGenerationAuthority):
    def __init__(
        self,
        authority: ApprovedGenerationAuthority,
        barrier: Barrier,
    ) -> None:
        super().__init__(authority)
        self._barrier = barrier

    def load_current_authority(
        self, command: ApprovedPlanGenerationCommand
    ) -> ApprovedGenerationAuthority:
        self._barrier.wait(timeout=10)
        return super().load_current_authority(command)


def _generation_success_commit(
    *,
    running,
    claimed_at: datetime,
    suffix: str,
) -> GenerationSuccessCommit:
    payload = f"validated-generation-result:{suffix}".encode()
    content_sha256 = hashlib.sha256(payload).hexdigest()
    return GenerationSuccessCommit(
        operation=OperationExecutionRequest.from_operation(running),
        provider_outcome=NormalizedImageProviderOutcome(
            call_outcome=ImageProviderCallOutcome.CONFIRMED_SUCCESS,
            task_state=ImageProviderTaskState.SUCCEEDED,
            identity=ImageProviderRequestIdentity(
                provider_request_id=f"provider-request-{suffix}",
                provider_task_id=None,
            ),
            result=ImageProviderResult(
                provider_result_id=f"provider-result-{suffix}",
                content=payload,
                content_sha256=content_sha256,
                media_type=ImageProviderMediaType.PNG,
                width=1024,
                height=1024,
            ),
            usage=ImageProviderUsage(
                unit=ImageProviderUsageUnit.IMAGE,
                quantity=Decimal("1.000000"),
                evidence_sha256="b" * 64,
            ),
            error=None,
            latency_ms=250,
        ),
        controlled_object=ObjectStat(
            reference=ObjectReference(
                location=StorageLocationClass.TASK,
                key=f"generation/{running.id}/candidate.png",
                version_id=f"provider-version-{suffix}",
            ),
            backend=StorageBackend.MINIO,
            bucket="task-assets",
            etag='"generation-etag"',
            content_length=len(payload),
            content_type="image/png",
            checksum_sha256_base64=None,
            metadata={"sha256": content_sha256},
            last_modified=claimed_at,
            server_side_encryption=ServerSideEncryptionState.AES256,
        ),
        request_sha256="c" * 64,
        moderation_decision_sha256="d" * 64,
        trace_id=f"trace-generation-{suffix}",
    )


def _record_generation_dispatch_attempt(
    integration_database,
    *,
    definition: ToolDefinition,
    running,
    outcome: NormalizedImageProviderOutcome,
) -> AuthorizedGenerationDispatch:
    dispatch = MySqlGenerationDispatchAuthority(
        integration_database.session_factory,
        approved_authority_factory=lambda session: _approved_generation_authority(
            session,
            definition=definition,
            authorized_asset_version_ids=frozenset(),
            rights_policy_version="rights-none.v1",
        ),
        dispatch_builder=StructuredGenerationDispatchBuilder(),
    ).prepare_dispatch(OperationExecutionRequest.from_operation(running))
    attempts = MySqlGenerationDispatchAttemptCoordinator(integration_database.session_factory)
    claim = attempts.claim(dispatch)
    attempts.record_outcome(
        claim=claim,
        dispatch=dispatch,
        outcome=outcome,
    )
    return dispatch


def test_approved_generation_command_persists_and_replays_one_atomic_aggregate(
    integration_database,
) -> None:
    now = _seed_route_authority(integration_database)
    route = ModelRouterApplicationService(
        lambda: SqlAlchemyModelRouterUnitOfWork(integration_database.session_factory)
    ).route(
        request=_request(now),
        policy_key="default-images",
        actor_id="generation-service",
        idempotency_key="test:generation-route-001",
        trace_id="trace-generation-route-001",
    )
    authority = ApprovedGenerationAuthority(
        workspace_id=WORKSPACE_ID,
        workflow_id=WORKFLOW_ID,
        workflow_version=1,
        creative_plan_id=PLAN_ID,
        creative_plan_version_id=PLAN_VERSION_ID,
        creative_plan_version=1,
        approval_id=APPROVAL_ID,
        direction_key="main-image",
        tool_intent_key="generate-main-image",
        tool_intent_sha256="4" * 64,
        prompt_sha256="e" * 64,
        context_sha256="d" * 64,
        route_decision_sha256=route.decision.decision_sha256,
        route_request_sha256=route.decision.request_sha256,
        operation_kind="IMAGE_GENERATION",
        authorized_asset_version_ids=(),
        candidate_count=1,
        route_policy_version=route.decision.route_policy_version,
        tool_policy_version="tool-intent-policy-v1",
        rights_policy_version="rights-none.v1",
        safety_policy_version="media-safety.v1",
        workflow_deadline=now + timedelta(days=1),
        source_rights_deadline=None,
        retention_deadline=now + timedelta(hours=6),
        created_by="generation-service",
    )
    service = ApprovedPlanGenerationService(
        lambda: SqlAlchemyApprovedGenerationUnitOfWork(
            integration_database.session_factory,
            lambda session: _ExactGenerationAuthority(authority),
        )
    )
    command = ApprovedPlanGenerationCommand(
        workspace_id=WORKSPACE_ID,
        workflow_id=WORKFLOW_ID,
        expected_workflow_version=1,
        creative_plan_id=PLAN_ID,
        creative_plan_version_id=PLAN_VERSION_ID,
        creative_plan_version=1,
        approval_id=APPROVAL_ID,
        direction_key="main-image",
        tool_intent_key="generate-main-image",
        route_decision_sha256=route.decision.decision_sha256,
    )

    first = service.start(
        command=command,
        idempotency_key="test:approved-generation-001",
        trace_id="trace-approved-generation-001",
    )
    replay = service.start(
        command=command,
        idempotency_key="test:approved-generation-001",
        trace_id="trace-approved-generation-replay",
    )

    assert first.replayed is False
    assert replay.replayed is True
    assert replay == replace(first, replayed=True)
    with integration_database.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(GenerationBatchModel)) == 1
        assert session.scalar(select(func.count()).select_from(CandidateSlotModel)) == 1
        assert session.scalar(select(func.count()).select_from(DurableOperationModel)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(OutboxEventModel)
                .where(OutboxEventModel.event_type == "generation.candidate.requested")
            )
            == 1
        )
        persisted_batch = session.scalar(select(GenerationBatchModel))
        persisted_slot = session.scalar(select(CandidateSlotModel))
        assert persisted_batch is not None
        assert persisted_slot is not None
        assert persisted_batch.route_decision_sha256 == route.decision.decision_sha256
        assert persisted_slot.generation_batch_id == persisted_batch.id
        assert persisted_slot.durable_operation_id == first.operations[0].id

    with (
        pytest.raises(DBAPIError, match="immutable"),
        integration_database.engine.begin() as connection,
    ):
        connection.execute(
            text(
                "UPDATE generation_batches SET candidate_count = 2 "
                "WHERE workspace_id = :workspace_id AND id = :batch_id"
            ),
            {"workspace_id": WORKSPACE_ID, "batch_id": first.batch.id},
        )
    with (
        pytest.raises(DBAPIError, match="immutable"),
        integration_database.engine.begin() as connection,
    ):
        connection.execute(
            text(
                "UPDATE candidate_slots SET candidate_index = 1 "
                "WHERE workspace_id = :workspace_id AND id = :slot_id"
            ),
            {"workspace_id": WORKSPACE_ID, "slot_id": first.slots[0].id},
        )


def test_concurrent_generation_commands_with_distinct_idempotency_keys_converge(
    integration_database,
) -> None:
    now = _seed_route_authority(integration_database)
    route = ModelRouterApplicationService(
        lambda: SqlAlchemyModelRouterUnitOfWork(integration_database.session_factory)
    ).route(
        request=_request(now),
        policy_key="default-images",
        actor_id="generation-service",
        idempotency_key="test:generation-concurrent-route-001",
        trace_id="trace-generation-concurrent-route-001",
    )
    authority = ApprovedGenerationAuthority(
        workspace_id=WORKSPACE_ID,
        workflow_id=WORKFLOW_ID,
        workflow_version=1,
        creative_plan_id=PLAN_ID,
        creative_plan_version_id=PLAN_VERSION_ID,
        creative_plan_version=1,
        approval_id=APPROVAL_ID,
        direction_key="main-image",
        tool_intent_key="generate-main-image",
        tool_intent_sha256="4" * 64,
        prompt_sha256="e" * 64,
        context_sha256="d" * 64,
        route_decision_sha256=route.decision.decision_sha256,
        route_request_sha256=route.decision.request_sha256,
        operation_kind="IMAGE_GENERATION",
        authorized_asset_version_ids=(),
        candidate_count=1,
        route_policy_version=route.decision.route_policy_version,
        tool_policy_version="tool-intent-policy-v1",
        rights_policy_version="rights-none.v1",
        safety_policy_version="media-safety.v1",
        workflow_deadline=now + timedelta(days=1),
        source_rights_deadline=None,
        retention_deadline=now + timedelta(hours=6),
        created_by="generation-service",
    )
    barrier = Barrier(2)
    service = ApprovedPlanGenerationService(
        lambda: SqlAlchemyApprovedGenerationUnitOfWork(
            integration_database.session_factory,
            lambda session: _BarrierGenerationAuthority(authority, barrier),
        )
    )
    command = ApprovedPlanGenerationCommand(
        workspace_id=WORKSPACE_ID,
        workflow_id=WORKFLOW_ID,
        expected_workflow_version=1,
        creative_plan_id=PLAN_ID,
        creative_plan_version_id=PLAN_VERSION_ID,
        creative_plan_version=1,
        approval_id=APPROVAL_ID,
        direction_key="main-image",
        tool_intent_key="generate-main-image",
        route_decision_sha256=route.decision.decision_sha256,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                service.start,
                command=command,
                idempotency_key=f"test:approved-generation-concurrent-{index}",
                trace_id=f"trace-approved-generation-concurrent-{index}",
            )
            for index in range(2)
        ]
        results = [future.result(timeout=15) for future in futures]

    assert len({result.batch.id for result in results}) == 1
    assert sorted(result.replayed for result in results) == [False, True]
    assert results[0].slots == results[1].slots
    assert results[0].operations == results[1].operations
    with integration_database.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(GenerationBatchModel)) == 1
        assert session.scalar(select(func.count()).select_from(CandidateSlotModel)) == 1
        assert session.scalar(select(func.count()).select_from(DurableOperationModel)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(OutboxEventModel)
                .where(OutboxEventModel.event_type == "generation.candidate.requested")
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEventModel)
                .where(AuditEventModel.action == "generation-batch.created")
            )
            == 1
        )
        idempotency_records = tuple(
            session.scalars(
                select(IdempotencyKeyModel).where(
                    IdempotencyKeyModel.resource_type == "generation-batch"
                )
            )
        )
        assert len(idempotency_records) == 2
        assert {record.status for record in idempotency_records} == {"COMPLETED"}
        assert {record.resource_id for record in idempotency_records} == {results[0].batch.id}


def test_generation_command_revalidates_real_mysql_approval_plan_policy_and_route(
    integration_database,
) -> None:
    payload = _generation_plan_payload()
    now = _seed_route_authority(
        integration_database,
        plan_payload=payload,
        workflow_version=2,
        workflow_node="approve_plan",
        approval_expected_workflow_version=1,
        approval_decision="APPROVE",
    )
    route = ModelRouterApplicationService(
        lambda: SqlAlchemyModelRouterUnitOfWork(integration_database.session_factory)
    ).route(
        request=_request(now),
        policy_key="default-images",
        actor_id="generation-service",
        idempotency_key="test:generation-production-route-001",
        trace_id="trace-generation-production-route-001",
    )
    definition = replace(
        fixture_image_intent_definition(),
        name="provider-a.image.generate",
        provider="provider-a",
    )
    service = ApprovedPlanGenerationService(
        lambda: SqlAlchemyApprovedGenerationUnitOfWork(
            integration_database.session_factory,
            lambda session: MySqlApprovedGenerationAuthority(
                session,
                authorizer=ToolIntentAuthorizer(
                    registry=ToolRegistry([definition]),
                    policy_version="tool-intent-policy-v1",
                ),
                policy=ToolAuthorizationPolicy(
                    version="tool-intent-policy-v1",
                    node="execute_tool",
                ),
                entitlements=ToolAuthorizationEntitlements(
                    granted_scopes=frozenset({"image.generate"}),
                    authorized_resource_ids=frozenset(),
                    allowed_providers=frozenset({"provider-a"}),
                    allowed_cost_classes=frozenset({ToolCostClass.LOW}),
                    remaining_quota_units=1,
                    remaining_budget_units=1,
                ),
                generation_tools={
                    ("provider-a.image.generate", "1.0"): "provider-a",
                },
                rights_policy_version="rights-none.v1",
                safety_policy_version="media-safety.v1",
                actor_id="generation-service",
            ),
        )
    )

    result = service.start(
        command=ApprovedPlanGenerationCommand(
            workspace_id=WORKSPACE_ID,
            workflow_id=WORKFLOW_ID,
            expected_workflow_version=2,
            creative_plan_id=PLAN_ID,
            creative_plan_version_id=PLAN_VERSION_ID,
            creative_plan_version=1,
            approval_id=APPROVAL_ID,
            direction_key="main-image",
            tool_intent_key="generate-main-image",
            route_decision_sha256=route.decision.decision_sha256,
        ),
        idempotency_key="test:approved-generation-production-001",
        trace_id="trace-approved-generation-production-001",
    )

    assert result.replayed is False
    assert result.batch.workflow_version == 2
    assert result.batch.candidate_count == 1
    assert result.batch.prompt_sha256 == "e" * 64
    assert result.batch.context_sha256 == "d" * 64
    assert result.batch.route_decision_sha256 == route.decision.decision_sha256
    assert result.batch.route_request_sha256 == route.decision.request_sha256
    assert result.batch.tool_policy_version == "tool-intent-policy-v1"
    assert result.batch.rights_policy_version == "rights-none.v1"
    assert result.batch.safety_policy_version == "media-safety.v1"
    with integration_database.session_factory() as session:
        persisted_route = session.scalar(
            select(ModelRouteDecisionModel).where(
                ModelRouteDecisionModel.workspace_id == WORKSPACE_ID,
                ModelRouteDecisionModel.decision_sha256 == route.decision.decision_sha256,
            )
        )
        assert persisted_route is not None
        assert persisted_route.route_request_json == _request(now).canonical_data()


def test_generation_dispatch_uses_revalidated_facts_outside_mysql_transaction(
    integration_database,
) -> None:
    definition, result, running, _claimed_at = _start_running_generation_dispatch(
        integration_database,
        suffix="authorized",
    )
    builder = _CapturingGenerationDispatchBuilder()
    dispatcher = _CapturingGenerationProviderDispatcher()
    executor = GenerationOperationExecutor(
        authority=MySqlGenerationDispatchAuthority(
            integration_database.session_factory,
            approved_authority_factory=lambda session: _approved_generation_authority(
                session,
                definition=definition,
                authorized_asset_version_ids=frozenset(),
                rights_policy_version="rights-none.v1",
            ),
            dispatch_builder=builder,
        ),
        dispatcher=dispatcher,
    )

    execution_result = executor.execute(OperationExecutionRequest.from_operation(running))

    assert execution_result.operation_id == running.id
    assert execution_result.provider_request_id == "provider-request-generation-test"
    assert len(builder.facts) == 1
    facts = builder.facts[0]
    assert facts.operation.operation_id == running.id
    assert facts.batch == result.batch
    assert facts.slot == result.slots[0]
    assert facts.approved_authority.route_decision_sha256 == result.batch.route_decision_sha256
    assert facts.creative_plan.id == PLAN_VERSION_ID
    assert facts.route_request.request_sha256 == result.batch.route_request_sha256
    assert facts.endpoint_capability_version_id == CAPABILITY_VERSION_ID
    assert len(dispatcher.dispatches) == 1
    dispatch = dispatcher.dispatches[0]
    assert dispatch.operation_id == running.id
    assert isinstance(dispatch.provider_request, ImageGenerationProviderRequest)
    assert dispatch.provider_request.provider_idempotency_key == (f"durable-operation:{running.id}")
    assert dispatch.provider_request.media == ImageProviderMediaRequirements(
        width=facts.route_request.width,
        height=facts.route_request.height,
        output_format=ImageProviderOutputFormat.PNG,
    )
    assert dispatch.provider_request.reference_images == ()
    assert dispatch.provider_request.negative_prompt_text is None
    assert dispatch.provider_request.deadline == min(
        facts.route_request.deadline_at,
        facts.batch.retention_deadline,
        running.lease_expires_at,
    )
    assert dispatch.provider_request.prompt_text == "\n".join(
        (
            "CommerceVision approved image direction (creative-plan-image.v1)",
            "Image role: MAIN",
            "Execution purpose: Generate the approved main image candidate",
            "Scene: Clean product studio",
            "Composition: Centered hero composition",
            "Camera: Front three-quarter view",
            "Lighting: Soft commercial key light",
            "Color direction: Neutral brand-safe palette",
            "Product constraints:",
            "- Preserve exact product geometry",
            "Required elements:",
            "- Single product",
            "Prohibited elements:",
            "- None",
            "Quality targets:",
            "- Sharp product detail",
        )
    )


def test_generation_dispatch_attempt_is_durable_and_never_regrants_submission(
    integration_database,
) -> None:
    definition, _generation, running, _claimed_at = _start_running_generation_dispatch(
        integration_database,
        suffix="attempt-fence",
    )
    authority = MySqlGenerationDispatchAuthority(
        integration_database.session_factory,
        approved_authority_factory=lambda session: _approved_generation_authority(
            session,
            definition=definition,
            authorized_asset_version_ids=frozenset(),
            rights_policy_version="rights-none.v1",
        ),
        dispatch_builder=StructuredGenerationDispatchBuilder(),
    )
    dispatch = authority.prepare_dispatch(OperationExecutionRequest.from_operation(running))
    first_coordinator = MySqlGenerationDispatchAttemptCoordinator(
        integration_database.session_factory
    )

    first = first_coordinator.claim(dispatch)
    after_restart = MySqlGenerationDispatchAttemptCoordinator(
        integration_database.session_factory
    ).claim(dispatch)

    assert first == GenerationDispatchAttemptClaim(
        attempt_id=first.attempt_id,
        submit_authorized=True,
        provider_request_id=None,
        provider_task_id=None,
    )
    assert after_restart == GenerationDispatchAttemptClaim(
        attempt_id=first.attempt_id,
        submit_authorized=False,
        provider_request_id=None,
        provider_task_id=None,
    )
    outcome = NormalizedImageProviderOutcome(
        call_outcome=ImageProviderCallOutcome.CONFIRMED_SUCCESS,
        task_state=ImageProviderTaskState.PENDING,
        identity=ImageProviderRequestIdentity(
            provider_request_id="provider-request-attempt-fence",
            provider_task_id="provider-task-attempt-fence",
        ),
        result=None,
        usage=None,
        error=None,
        latency_ms=50,
    )
    first_coordinator.record_outcome(
        claim=first,
        dispatch=dispatch,
        outcome=outcome,
    )

    recorded = MySqlGenerationDispatchAttemptCoordinator(
        integration_database.session_factory
    ).claim(dispatch)
    assert recorded == GenerationDispatchAttemptClaim(
        attempt_id=first.attempt_id,
        submit_authorized=False,
        provider_request_id="provider-request-attempt-fence",
        provider_task_id="provider-task-attempt-fence",
    )
    with integration_database.session_factory() as session:
        attempt = session.get(
            GenerationDispatchAttemptModel,
            (WORKSPACE_ID, first.attempt_id),
        )
        assert attempt is not None
        assert attempt.state == "OUTCOME_RECORDED"
        assert attempt.outcome == "CONFIRMED_SUCCESS"
        assert (
            attempt.provider_request_id_sha256
            == hashlib.sha256(b"provider-request-attempt-fence").hexdigest()
        )

    conflicting = replace(
        outcome,
        identity=ImageProviderRequestIdentity(
            provider_request_id="provider-request-conflict",
            provider_task_id=None,
        ),
    )
    with pytest.raises(ConcurrencyError, match="Provider identity"):
        first_coordinator.record_outcome(
            claim=first,
            dispatch=dispatch,
            outcome=conflicting,
        )


def test_generation_dispatch_crash_before_outcome_record_never_resubmits(
    integration_database,
) -> None:
    definition, _generation, running, _claimed_at = _start_running_generation_dispatch(
        integration_database,
        suffix="crash-after-provider",
    )
    dispatch = MySqlGenerationDispatchAuthority(
        integration_database.session_factory,
        approved_authority_factory=lambda session: _approved_generation_authority(
            session,
            definition=definition,
            authorized_asset_version_ids=frozenset(),
            rights_policy_version="rights-none.v1",
        ),
        dispatch_builder=StructuredGenerationDispatchBuilder(),
    ).prepare_dispatch(OperationExecutionRequest.from_operation(running))

    class CrashingAdapter:
        def __init__(self) -> None:
            self.submit_calls = 0

        def submit(self, _request):
            self.submit_calls += 1
            raise TimeoutError("injected crash after possible dispatch")

    class Resolver:
        def __init__(self, adapter) -> None:
            self.adapter = adapter

        def resolve(self, **_kwargs):
            return self.adapter

    class MustNotRun:
        def __getattr__(self, name: str):
            raise AssertionError(f"{name} must not run after unknown dispatch")

    adapter = CrashingAdapter()

    def restarted_dispatcher() -> AtomicGenerationProviderDispatcher:
        return AtomicGenerationProviderDispatcher(
            attempts=MySqlGenerationDispatchAttemptCoordinator(
                integration_database.session_factory
            ),
            adapters=Resolver(adapter),
            admission=MustNotRun(),
            storage=MustNotRun(),
            converger=MustNotRun(),
        )

    with pytest.raises(UnknownOperationOutcome):
        restarted_dispatcher().submit(dispatch)
    with pytest.raises(UnknownOperationOutcome) as restarted:
        restarted_dispatcher().submit(dispatch)

    assert restarted.value.error.code == "GENERATION_DISPATCH_ALREADY_STARTED"
    assert adapter.submit_calls == 1
    with integration_database.session_factory() as session:
        attempt = session.scalar(select(GenerationDispatchAttemptModel))
        assert attempt is not None
        assert attempt.state == "DISPATCHING"
        assert attempt.outcome is None


def test_generation_result_converges_asset_candidate_usage_event_and_operation_atomically(
    integration_database,
) -> None:
    definition, generation, running, claimed_at = _start_running_generation_dispatch(
        integration_database,
        suffix="converge-success",
    )
    provider_request_id = "provider-request-converge-success"
    commit = _generation_success_commit(
        running=running,
        claimed_at=claimed_at,
        suffix="converge-success",
    )
    converger = MySqlGenerationResultConverger(
        integration_database.session_factory,
        approved_authority_factory=lambda session: _approved_generation_authority(
            session,
            definition=definition,
            authorized_asset_version_ids=frozenset(),
            rights_policy_version="rights-none.v1",
        ),
    )

    with pytest.raises(ConcurrencyError, match="dispatch Attempt"):
        converger.commit_success(commit)
    dispatch = _record_generation_dispatch_attempt(
        integration_database,
        definition=definition,
        running=running,
        outcome=commit.provider_outcome,
    )
    commit = replace(commit, request_sha256=dispatch.request_sha256)

    completed = converger.commit_success(commit)

    assert completed.completion_committed is True
    assert completed.operation_id == running.id
    assert completed.provider_request_id == provider_request_id
    with integration_database.session_factory() as session:
        operation = session.get(DurableOperationModel, running.id)
        assert operation is not None
        assert operation.state == "SUCCEEDED"
        assert operation.output_ref == completed.output_ref
        assert session.scalar(select(func.count()).select_from(ProviderCallModel)) == 1
        assert session.scalar(select(func.count()).select_from(UsageRecordModel)) == 1
        assert session.scalar(select(func.count()).select_from(CandidateImageModel)) == 1
        generated_version = session.scalar(
            select(AssetVersionModel).where(
                AssetVersionModel.workspace_id == WORKSPACE_ID,
                AssetVersionModel.upload_session_id.is_(None),
            )
        )
        assert generated_version is not None
        assert generated_version.generation_provider_call_id is not None
        assert (
            session.scalar(
                select(func.count())
                .select_from(OutboxEventModel)
                .where(OutboxEventModel.event_type == "generation.candidate.ready")
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEventModel)
                .where(AuditEventModel.action == "generation-candidate.ready")
            )
            == 1
        )
        assert generation.slots[0].durable_operation_id == running.id
        candidate = session.scalar(select(CandidateImageModel))
        assert candidate is not None
        ready_identity = {
            "candidate_slot_id": candidate.candidate_slot_id,
            "candidate_image_id": candidate.id,
            "asset_version_id": candidate.task_asset_version_id,
            "operation_id": running.id,
            "usage_record_id": candidate.usage_record_id,
        }

    continuation = MySqlGenerationWorkflowContinuationAuthority(
        integration_database.session_factory
    ).claim_ready_batch(
        workspace_id=WORKSPACE_ID,
        workflow_id=WORKFLOW_ID,
        generation_batch_id=generation.batch.id,
        **ready_identity,
    )

    assert continuation is not None
    assert continuation.workflow_id == WORKFLOW_ID
    assert continuation.generation_batch_id == generation.batch.id
    assert continuation.creative_plan_id == PLAN_ID
    assert continuation.creative_plan_version_id == PLAN_VERSION_ID
    assert continuation.generation_iteration == 0
    assert continuation.candidate_refs == (
        f"mysql://candidate-images/{ready_identity['candidate_image_id']}",
    )
    with pytest.raises(ConcurrencyError, match="does not match MySQL authority"):
        MySqlGenerationWorkflowContinuationAuthority(
            integration_database.session_factory
        ).claim_ready_batch(
            workspace_id=WORKSPACE_ID,
            workflow_id=WORKFLOW_ID,
            generation_batch_id=generation.batch.id,
            **{**ready_identity, "usage_record_id": "019b0000-0000-7000-8000-000000000899"},
        )
    lifecycle = DurableNodeLifecycle(
        uow_factory=lambda: SqlAlchemyUnitOfWork(integration_database.session_factory),
        lease_duration=timedelta(minutes=2),
    )
    evaluation_claim = lifecycle.begin_node(
        workflow_id=WORKFLOW_ID,
        expected_workflow_version=continuation.workflow_version,
        step_key=f"evaluate_results:generation-batch:{generation.batch.id}",
        step_type=StepType.EVALUATE_RESULTS,
        running_state=WorkflowStatus.EVALUATING,
        node_name="evaluate_results",
        lease_owner="generation-workflow-worker",
        trace_id="trace-generation-evaluation-restart",
    )
    restarted_continuation = MySqlGenerationWorkflowContinuationAuthority(
        integration_database.session_factory
    ).claim_ready_batch(
        workspace_id=WORKSPACE_ID,
        workflow_id=WORKFLOW_ID,
        generation_batch_id=generation.batch.id,
        **ready_identity,
    )
    assert restarted_continuation is not None
    assert restarted_continuation.workflow_version == evaluation_claim.workflow_version

    lifecycle.complete_node(
        workflow_id=WORKFLOW_ID,
        step_id=evaluation_claim.step_id,
        lease_token=evaluation_claim.lease_token,
        target_state=WorkflowStatus.AWAITING_RESULT_APPROVAL,
        next_node="approve_results",
        trace_id="trace-generation-evaluation-complete",
        output_data={"evaluation_report_ref": "fixture://evaluation/ready"},
        expected_workflow_version=evaluation_claim.workflow_version,
    )
    database_ahead_recovery = MySqlGenerationWorkflowContinuationAuthority(
        integration_database.session_factory
    ).claim_ready_batch(
        workspace_id=WORKSPACE_ID,
        workflow_id=WORKFLOW_ID,
        generation_batch_id=generation.batch.id,
        **ready_identity,
    )
    assert database_ahead_recovery is not None
    assert database_ahead_recovery.workflow_version == evaluation_claim.workflow_version + 1

    with integration_database.session_factory.begin() as session:
        workflow = session.get(WorkflowModel, WORKFLOW_ID)
        assert workflow is not None
        workflow.status = "EXPORTING"
        workflow.current_node = "export"
        workflow.version += 1
    assert (
        MySqlGenerationWorkflowContinuationAuthority(
            integration_database.session_factory
        ).claim_ready_batch(
            workspace_id=WORKSPACE_ID,
            workflow_id=WORKFLOW_ID,
            generation_batch_id=generation.batch.id,
            **ready_identity,
        )
        is None
    )


def test_generation_result_database_fault_rolls_back_every_publishable_fact(
    integration_database,
) -> None:
    definition, _generation, running, claimed_at = _start_running_generation_dispatch(
        integration_database,
        suffix="converge-rollback",
    )
    converger = MySqlGenerationResultConverger(
        integration_database.session_factory,
        approved_authority_factory=lambda session: _approved_generation_authority(
            session,
            definition=definition,
            authorized_asset_version_ids=frozenset(),
            rights_policy_version="rights-none.v1",
        ),
    )
    commit = _generation_success_commit(
        running=running,
        claimed_at=claimed_at,
        suffix="converge-rollback",
    )
    dispatch = _record_generation_dispatch_attempt(
        integration_database,
        definition=definition,
        running=running,
        outcome=commit.provider_outcome,
    )
    commit = replace(commit, request_sha256=dispatch.request_sha256)
    with integration_database.engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TRIGGER trg_test_candidate_insert_failure "
                "BEFORE INSERT ON candidate_images FOR EACH ROW "
                "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'injected candidate failure'"
            )
        )
    try:
        with pytest.raises(DBAPIError, match="injected candidate failure"):
            converger.commit_success(commit)
    finally:
        with integration_database.engine.begin() as connection:
            connection.execute(text("DROP TRIGGER IF EXISTS trg_test_candidate_insert_failure"))

    with integration_database.session_factory() as session:
        operation = session.get(DurableOperationModel, running.id)
        assert operation is not None
        assert operation.state == "RUNNING"
        assert session.scalar(select(func.count()).select_from(ProviderCallModel)) == 0
        assert session.scalar(select(func.count()).select_from(UsageRecordModel)) == 0
        assert session.scalar(select(func.count()).select_from(CandidateImageModel)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(AssetVersionModel)
                .where(AssetVersionModel.upload_session_id.is_(None))
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(OutboxEventModel)
                .where(OutboxEventModel.event_type == "generation.candidate.ready")
            )
            == 0
        )


def test_generation_dispatch_denies_cancelled_workflow_before_provider_call(
    integration_database,
) -> None:
    definition, _result, running, claimed_at = _start_running_generation_dispatch(
        integration_database,
        suffix="cancelled",
    )
    with integration_database.session_factory.begin() as session:
        workflow = session.get(WorkflowModel, WORKFLOW_ID)
        assert workflow is not None
        workflow.cancellation_requested_at = claimed_at

    builder = _NeverGenerationDispatchBuilder()
    dispatcher = _NeverGenerationProviderDispatcher()
    authority = MySqlGenerationDispatchAuthority(
        integration_database.session_factory,
        approved_authority_factory=lambda session: _approved_generation_authority(
            session,
            definition=definition,
            authorized_asset_version_ids=frozenset(),
            rights_policy_version="rights-none.v1",
        ),
        dispatch_builder=builder,
    )
    executor = GenerationOperationExecutor(
        authority=authority,
        dispatcher=dispatcher,
    )

    with pytest.raises(OperationExecutionFailure) as captured:
        executor.execute(OperationExecutionRequest.from_operation(running))

    assert captured.value.error.code == "GENERATION_AUTHORITY_DENIED"
    assert captured.value.error.retryable is False
    assert builder.calls == 0
    assert dispatcher.calls == 0


def test_generation_http_command_replays_reads_and_hides_foreign_workspace(
    integration_database,
) -> None:
    payload = _generation_plan_payload()
    now = _seed_route_authority(
        integration_database,
        plan_payload=payload,
        workflow_version=2,
        workflow_node="approve_plan",
        approval_expected_workflow_version=1,
        approval_decision="APPROVE",
    )
    route = ModelRouterApplicationService(
        lambda: SqlAlchemyModelRouterUnitOfWork(integration_database.session_factory)
    ).route(
        request=_request(now),
        policy_key="default-images",
        actor_id="generation-service",
        idempotency_key="test:generation-http-route-001",
        trace_id="trace-generation-http-route-001",
    )
    definition = replace(
        fixture_image_intent_definition(),
        name="provider-a.image.generate",
        provider="provider-a",
    )
    service = _production_generation_service(
        integration_database,
        definition=definition,
        authorized_asset_version_ids=frozenset(),
        rights_policy_version="rights-none.v1",
    )
    app = _generation_http_app(service)
    headers = {
        "X-Workspace-Id": WORKSPACE_ID,
        "X-Actor-Id": "generation-service",
        "Idempotency-Key": "test:approved-generation-http-001",
        "X-Trusted-Principal": "fixture",
    }
    request = {
        "workflow_id": WORKFLOW_ID,
        "expected_workflow_version": 2,
        "creative_plan_id": PLAN_ID,
        "creative_plan_version_id": PLAN_VERSION_ID,
        "creative_plan_version": 1,
        "approval_id": APPROVAL_ID,
        "direction_key": "main-image",
        "tool_intent_key": "generate-main-image",
        "route_decision_sha256": route.decision.decision_sha256,
    }

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/generation-batches",
            headers=headers,
            json=request,
        )
        replayed = client.post(
            "/api/v1/generation-batches",
            headers=headers,
            json=request,
        )
        batch_id = str(created.json()["id"])
        loaded = client.get(
            f"/api/v1/generation-batches/{batch_id}",
            headers={
                "X-Workspace-Id": WORKSPACE_ID,
                "X-Trusted-Principal": "fixture",
            },
        )
        foreign = client.get(
            f"/api/v1/generation-batches/{batch_id}",
            headers={
                "X-Workspace-Id": "foreign-generation-workspace",
                "X-Trusted-Principal": "fixture",
            },
        )

    assert created.status_code == 201, created.text
    assert replayed.status_code == 201, replayed.text
    assert loaded.status_code == 200, loaded.text
    assert replayed.json() == created.json() == loaded.json()
    assert created.json()["route_decision_sha256"] == route.decision.decision_sha256
    assert created.json()["slots"][0]["operation"]["state"] == "PENDING"
    assert foreign.status_code == 404
    assert foreign.json()["code"] == "NOT_FOUND"
    with integration_database.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(GenerationBatchModel)) == 1
        assert session.scalar(select(func.count()).select_from(DurableOperationModel)) == 1


@pytest.mark.parametrize(
    "authority_case",
    ["unapproved", "rejected", "stale", "expired", "revoked", "budget", "foreign"],
)
def test_generation_command_denials_create_no_dispatchable_work(
    integration_database,
    authority_case: str,
) -> None:
    revoked_rights = authority_case == "revoked"
    payload = _generation_plan_payload(
        source_asset_version_id=(SOURCE_ASSET_VERSION_ID if revoked_rights else None)
    )
    now = _seed_route_authority(
        integration_database,
        plan_payload=payload,
        workflow_version=2,
        workflow_node="approve_plan",
        approval_expected_workflow_version=1,
        approval_decision="APPROVE",
        maximum_reference_images=(1 if revoked_rights else 0),
    )
    source_asset_version_id = None
    if revoked_rights:
        source_asset_version_id, _ = _seed_generation_source_asset(
            integration_database,
            now=now,
        )
    route_request = _request(now)
    if source_asset_version_id is not None:
        route_request = replace(
            route_request,
            authorized_asset_version_ids=(source_asset_version_id,),
            reference_image_count=1,
        )
    route = ModelRouterApplicationService(
        lambda: SqlAlchemyModelRouterUnitOfWork(integration_database.session_factory)
    ).route(
        request=route_request,
        policy_key="default-images",
        actor_id="generation-service",
        idempotency_key=f"test:generation-denied-route-{authority_case}",
        trace_id=f"trace-generation-denied-route-{authority_case}",
    )
    with integration_database.session_factory.begin() as session:
        workflow = session.get(WorkflowModel, WORKFLOW_ID)
        approval = session.get(ApprovalModel, APPROVAL_ID)
        assert workflow is not None and approval is not None
        if authority_case == "unapproved":
            approval.approval_type = "PRODUCT_BRIEF"
        elif authority_case == "rejected":
            approval.decision = "REJECT"
        elif authority_case == "stale":
            workflow.version = 3
        elif authority_case == "expired":
            workflow.expires_at = now - timedelta(microseconds=1)
        elif authority_case == "revoked":
            session.execute(
                text(
                    "INSERT INTO rights_records "
                    "(id, workspace_id, asset_id, asset_version_id, version_number, decision, "
                    "owner_reference, source, license_reference, derivative_allowed, "
                    "public_demo_allowed, evidence_reference, terms_sha256, valid_from, "
                    "valid_until, perpetual, supersedes_record_id, created_by, created_at, "
                    "permissions_sealed_at) "
                    "SELECT :revoked_id, workspace_id, asset_id, asset_version_id, 2, 'REVOKE', "
                    "owner_reference, source, license_reference, 0, public_demo_allowed, "
                    "evidence_reference, terms_sha256, :now, NULL, 1, id, "
                    "'rights-admin', :now, :now FROM rights_records WHERE id = :grant_id"
                ),
                {
                    "now": now,
                    "revoked_id": REVOKED_RIGHTS_RECORD_ID,
                    "grant_id": SOURCE_RIGHTS_RECORD_ID,
                },
            )
            session.execute(
                text(
                    "UPDATE assets SET current_rights_record_id = :revoked_id, "
                    "version = version + 1, updated_at = :now WHERE id = :asset_id"
                ),
                {
                    "now": now,
                    "revoked_id": REVOKED_RIGHTS_RECORD_ID,
                    "asset_id": SOURCE_ASSET_ID,
                },
            )

    definition = replace(
        fixture_image_intent_definition(),
        name="provider-a.image.generate",
        provider="provider-a",
    )
    if revoked_rights:
        definition = replace(
            definition,
            input_model=_GenerationSourceIntentInput,
            input_schema=_GenerationSourceIntentInput.model_json_schema(),
            resource_resolver=_generation_source_resources,
        )
    service = _production_generation_service(
        integration_database,
        definition=definition,
        authorized_asset_version_ids=frozenset(),
        rights_policy_version="rights-none.v1",
        remaining_budget_units=(0 if authority_case == "budget" else 1),
    )
    command_workspace = (
        "foreign-generation-workspace" if authority_case == "foreign" else WORKSPACE_ID
    )
    with integration_database.session_factory() as session:
        before = {
            "batches": session.scalar(select(func.count()).select_from(GenerationBatchModel)),
            "slots": session.scalar(select(func.count()).select_from(CandidateSlotModel)),
            "operations": session.scalar(select(func.count()).select_from(DurableOperationModel)),
            "outbox": session.scalar(select(func.count()).select_from(OutboxEventModel)),
            "audit": session.scalar(select(func.count()).select_from(AuditEventModel)),
            "idempotency": session.scalar(select(func.count()).select_from(IdempotencyKeyModel)),
        }

    with pytest.raises(ConcurrencyError):
        service.start(
            command=ApprovedPlanGenerationCommand(
                workspace_id=command_workspace,
                workflow_id=WORKFLOW_ID,
                expected_workflow_version=2,
                creative_plan_id=PLAN_ID,
                creative_plan_version_id=PLAN_VERSION_ID,
                creative_plan_version=1,
                approval_id=APPROVAL_ID,
                direction_key="main-image",
                tool_intent_key="generate-main-image",
                route_decision_sha256=route.decision.decision_sha256,
            ),
            idempotency_key=f"test:approved-generation-denied-{authority_case}",
            trace_id=f"trace-approved-generation-denied-{authority_case}",
        )

    with integration_database.session_factory() as session:
        after = {
            "batches": session.scalar(select(func.count()).select_from(GenerationBatchModel)),
            "slots": session.scalar(select(func.count()).select_from(CandidateSlotModel)),
            "operations": session.scalar(select(func.count()).select_from(DurableOperationModel)),
            "outbox": session.scalar(select(func.count()).select_from(OutboxEventModel)),
            "audit": session.scalar(select(func.count()).select_from(AuditEventModel)),
            "idempotency": session.scalar(select(func.count()).select_from(IdempotencyKeyModel)),
        }
    assert after == before


def test_generation_command_rolls_back_every_fact_when_outbox_insert_fails(
    integration_database,
) -> None:
    payload = _generation_plan_payload()
    now = _seed_route_authority(
        integration_database,
        plan_payload=payload,
        workflow_version=2,
        workflow_node="approve_plan",
        approval_expected_workflow_version=1,
        approval_decision="APPROVE",
    )
    route = ModelRouterApplicationService(
        lambda: SqlAlchemyModelRouterUnitOfWork(integration_database.session_factory)
    ).route(
        request=_request(now),
        policy_key="default-images",
        actor_id="generation-service",
        idempotency_key="test:generation-rollback-route-001",
        trace_id="trace-generation-rollback-route-001",
    )
    definition = replace(
        fixture_image_intent_definition(),
        name="provider-a.image.generate",
        provider="provider-a",
    )
    service = _production_generation_service(
        integration_database,
        definition=definition,
        authorized_asset_version_ids=frozenset(),
        rights_policy_version="rights-none.v1",
    )

    def fail_generation_outbox(
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        if "INSERT INTO outbox_events" in statement:
            raise RuntimeError("injected generation outbox failure")

    event.listen(
        integration_database.engine,
        "before_cursor_execute",
        fail_generation_outbox,
    )
    try:
        with pytest.raises(RuntimeError, match="injected generation outbox failure"):
            service.start(
                command=ApprovedPlanGenerationCommand(
                    workspace_id=WORKSPACE_ID,
                    workflow_id=WORKFLOW_ID,
                    expected_workflow_version=2,
                    creative_plan_id=PLAN_ID,
                    creative_plan_version_id=PLAN_VERSION_ID,
                    creative_plan_version=1,
                    approval_id=APPROVAL_ID,
                    direction_key="main-image",
                    tool_intent_key="generate-main-image",
                    route_decision_sha256=route.decision.decision_sha256,
                ),
                idempotency_key="test:approved-generation-rollback-001",
                trace_id="trace-approved-generation-rollback-001",
            )
    finally:
        event.remove(
            integration_database.engine,
            "before_cursor_execute",
            fail_generation_outbox,
        )

    with integration_database.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(GenerationBatchModel)) == 0
        assert session.scalar(select(func.count()).select_from(CandidateSlotModel)) == 0
        assert session.scalar(select(func.count()).select_from(DurableOperationModel)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(OutboxEventModel)
                .where(OutboxEventModel.event_type == "generation.candidate.requested")
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEventModel)
                .where(AuditEventModel.action == "generation-batch.created")
            )
            == 0
        )


def test_generation_command_locks_current_source_rights_and_clamps_retention(
    integration_database,
) -> None:
    payload = _generation_plan_payload(source_asset_version_id=SOURCE_ASSET_VERSION_ID)
    now = _seed_route_authority(
        integration_database,
        plan_payload=payload,
        workflow_version=2,
        workflow_node="approve_plan",
        approval_expected_workflow_version=1,
        approval_decision="APPROVE",
        maximum_reference_images=1,
    )
    source_asset_version_id, rights_deadline = _seed_generation_source_asset(
        integration_database,
        now=now,
    )
    route = ModelRouterApplicationService(
        lambda: SqlAlchemyModelRouterUnitOfWork(integration_database.session_factory)
    ).route(
        request=replace(
            _request(now),
            authorized_asset_version_ids=(source_asset_version_id,),
            reference_image_count=1,
        ),
        policy_key="default-images",
        actor_id="generation-service",
        idempotency_key="test:generation-rights-route-001",
        trace_id="trace-generation-rights-route-001",
    )
    definition = replace(
        fixture_image_intent_definition(),
        name="provider-a.image.generate",
        provider="provider-a",
        input_model=_GenerationSourceIntentInput,
        input_schema=_GenerationSourceIntentInput.model_json_schema(),
        resource_resolver=_generation_source_resources,
    )
    service = ApprovedPlanGenerationService(
        lambda: SqlAlchemyApprovedGenerationUnitOfWork(
            integration_database.session_factory,
            lambda session: MySqlApprovedGenerationAuthority(
                session,
                authorizer=ToolIntentAuthorizer(
                    registry=ToolRegistry([definition]),
                    policy_version="tool-intent-policy-v1",
                ),
                policy=ToolAuthorizationPolicy(
                    version="tool-intent-policy-v1",
                    node="execute_tool",
                ),
                entitlements=ToolAuthorizationEntitlements(
                    granted_scopes=frozenset({"image.generate"}),
                    authorized_resource_ids=frozenset(),
                    allowed_providers=frozenset({"provider-a"}),
                    allowed_cost_classes=frozenset({ToolCostClass.LOW}),
                    remaining_quota_units=1,
                    remaining_budget_units=1,
                ),
                generation_tools={
                    ("provider-a.image.generate", "1.0"): "provider-a",
                },
                rights_policy_version="asset-rights.v1",
                safety_policy_version="media-safety.v1",
                actor_id="generation-service",
            ),
        )
    )

    result = service.start(
        command=ApprovedPlanGenerationCommand(
            workspace_id=WORKSPACE_ID,
            workflow_id=WORKFLOW_ID,
            expected_workflow_version=2,
            creative_plan_id=PLAN_ID,
            creative_plan_version_id=PLAN_VERSION_ID,
            creative_plan_version=1,
            approval_id=APPROVAL_ID,
            direction_key="main-image",
            tool_intent_key="generate-main-image",
            route_decision_sha256=route.decision.decision_sha256,
        ),
        idempotency_key="test:approved-generation-rights-001",
        trace_id="trace-approved-generation-rights-001",
    )

    assert result.batch.authorized_asset_version_ids == (source_asset_version_id,)
    assert result.batch.source_rights_deadline == rights_deadline
    assert result.batch.retention_deadline == rights_deadline
    assert result.batch.rights_policy_version == "asset-rights.v1"


def test_generation_command_rejects_route_decision_for_different_authorized_assets(
    integration_database,
) -> None:
    payload = _generation_plan_payload(source_asset_version_id=SOURCE_ASSET_VERSION_ID)
    now = _seed_route_authority(
        integration_database,
        plan_payload=payload,
        workflow_version=2,
        workflow_node="approve_plan",
        approval_expected_workflow_version=1,
        approval_decision="APPROVE",
        maximum_reference_images=1,
    )
    source_asset_version_id, _ = _seed_generation_source_asset(
        integration_database,
        now=now,
    )
    route = ModelRouterApplicationService(
        lambda: SqlAlchemyModelRouterUnitOfWork(integration_database.session_factory)
    ).route(
        request=_request(now),
        policy_key="default-images",
        actor_id="generation-service",
        idempotency_key="test:generation-mismatched-assets-route-001",
        trace_id="trace-generation-mismatched-assets-route-001",
    )
    definition = replace(
        fixture_image_intent_definition(),
        name="provider-a.image.generate",
        provider="provider-a",
        input_model=_GenerationSourceIntentInput,
        input_schema=_GenerationSourceIntentInput.model_json_schema(),
        resource_resolver=_generation_source_resources,
    )
    service = _production_generation_service(
        integration_database,
        definition=definition,
        authorized_asset_version_ids=frozenset({source_asset_version_id}),
        rights_policy_version="asset-rights.v1",
    )

    with pytest.raises(ConcurrencyError, match="Route Decision"):
        service.start(
            command=ApprovedPlanGenerationCommand(
                workspace_id=WORKSPACE_ID,
                workflow_id=WORKFLOW_ID,
                expected_workflow_version=2,
                creative_plan_id=PLAN_ID,
                creative_plan_version_id=PLAN_VERSION_ID,
                creative_plan_version=1,
                approval_id=APPROVAL_ID,
                direction_key="main-image",
                tool_intent_key="generate-main-image",
                route_decision_sha256=route.decision.decision_sha256,
            ),
            idempotency_key="test:approved-generation-mismatched-assets-001",
            trace_id="trace-approved-generation-mismatched-assets-001",
        )

    with integration_database.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(GenerationBatchModel)) == 0
        assert session.scalar(select(func.count()).select_from(DurableOperationModel)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(OutboxEventModel)
                .where(OutboxEventModel.event_type == "generation.candidate.requested")
            )
            == 0
        )


def test_generation_command_rejects_route_decision_for_different_candidate_count(
    integration_database,
) -> None:
    payload = _generation_plan_payload(candidate_count=1)
    now = _seed_route_authority(
        integration_database,
        plan_payload=payload,
        workflow_version=2,
        workflow_node="approve_plan",
        approval_expected_workflow_version=1,
        approval_decision="APPROVE",
    )
    route = ModelRouterApplicationService(
        lambda: SqlAlchemyModelRouterUnitOfWork(integration_database.session_factory)
    ).route(
        request=replace(
            _request(now),
            candidate_count=2,
            maximum_cost=Decimal("0.20"),
        ),
        policy_key="default-images",
        actor_id="generation-service",
        idempotency_key="test:generation-mismatched-count-route-001",
        trace_id="trace-generation-mismatched-count-route-001",
    )
    definition = replace(
        fixture_image_intent_definition(),
        name="provider-a.image.generate",
        provider="provider-a",
    )
    service = _production_generation_service(
        integration_database,
        definition=definition,
        authorized_asset_version_ids=frozenset(),
        rights_policy_version="rights-none.v1",
    )

    with pytest.raises(ConcurrencyError, match="Route Decision"):
        service.start(
            command=ApprovedPlanGenerationCommand(
                workspace_id=WORKSPACE_ID,
                workflow_id=WORKFLOW_ID,
                expected_workflow_version=2,
                creative_plan_id=PLAN_ID,
                creative_plan_version_id=PLAN_VERSION_ID,
                creative_plan_version=1,
                approval_id=APPROVAL_ID,
                direction_key="main-image",
                tool_intent_key="generate-main-image",
                route_decision_sha256=route.decision.decision_sha256,
            ),
            idempotency_key="test:approved-generation-mismatched-count-001",
            trace_id="trace-approved-generation-mismatched-count-001",
        )

    with integration_database.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(GenerationBatchModel)) == 0
        assert session.scalar(select(func.count()).select_from(DurableOperationModel)) == 0


@pytest.mark.parametrize("endpoint_failure", ["open", "stale"])
def test_generation_command_rechecks_latest_endpoint_circuit_quota_and_freshness(
    integration_database,
    endpoint_failure: str,
) -> None:
    payload = _generation_plan_payload()
    now = _seed_route_authority(
        integration_database,
        plan_payload=payload,
        workflow_version=2,
        workflow_node="approve_plan",
        approval_expected_workflow_version=1,
        approval_decision="APPROVE",
    )
    route = ModelRouterApplicationService(
        lambda: SqlAlchemyModelRouterUnitOfWork(integration_database.session_factory)
    ).route(
        request=_request(now),
        policy_key="default-images",
        actor_id="generation-service",
        idempotency_key=f"test:generation-endpoint-{endpoint_failure}-route-001",
        trace_id=f"trace-generation-endpoint-{endpoint_failure}-route-001",
    )
    with integration_database.session_factory.begin() as session:
        observed_at = _database_now(session)
        if endpoint_failure == "stale":
            session.execute(
                text(
                    "DELETE FROM provider_endpoint_observations "
                    "WHERE workspace_id = :workspace_id "
                    "AND endpoint_capability_version_id = :endpoint_id"
                ),
                {"workspace_id": WORKSPACE_ID, "endpoint_id": CAPABILITY_VERSION_ID},
            )
        session.add(
            ProviderEndpointObservationModel(
                workspace_id=WORKSPACE_ID,
                id="019b0000-0000-7000-8000-000000000724",
                endpoint_capability_version_id=CAPABILITY_VERSION_ID,
                quality_score=Decimal("0"),
                availability_score=Decimal("0"),
                latency_score=Decimal("0"),
                quota_score=Decimal("0"),
                circuit_state=(
                    CircuitState.OPEN.value
                    if endpoint_failure == "open"
                    else CircuitState.CLOSED.value
                ),
                remaining_quota_units=(0 if endpoint_failure == "open" else 1),
                observation_source="CIRCUIT_TRANSITION",
                idempotency_key_sha256="7" * 64,
                observed_at=(
                    observed_at
                    if endpoint_failure == "open"
                    else observed_at - timedelta(seconds=61)
                ),
                created_by="health-monitor",
                created_at=observed_at,
            )
        )
    definition = replace(
        fixture_image_intent_definition(),
        name="provider-a.image.generate",
        provider="provider-a",
    )
    service = _production_generation_service(
        integration_database,
        definition=definition,
        authorized_asset_version_ids=frozenset(),
        rights_policy_version="rights-none.v1",
    )

    with pytest.raises(ConcurrencyError, match="quota|circuit|fresh"):
        service.start(
            command=ApprovedPlanGenerationCommand(
                workspace_id=WORKSPACE_ID,
                workflow_id=WORKFLOW_ID,
                expected_workflow_version=2,
                creative_plan_id=PLAN_ID,
                creative_plan_version_id=PLAN_VERSION_ID,
                creative_plan_version=1,
                approval_id=APPROVAL_ID,
                direction_key="main-image",
                tool_intent_key="generate-main-image",
                route_decision_sha256=route.decision.decision_sha256,
            ),
            idempotency_key=f"test:approved-generation-endpoint-{endpoint_failure}-001",
            trace_id=f"trace-approved-generation-endpoint-{endpoint_failure}-001",
        )

    with integration_database.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(GenerationBatchModel)) == 0
        assert session.scalar(select(func.count()).select_from(DurableOperationModel)) == 0
