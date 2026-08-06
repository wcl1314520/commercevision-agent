from __future__ import annotations

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
    ModelRouterApplicationService,
    ToolAuthorizationEntitlements,
    ToolAuthorizationPolicy,
)
from commercevision_domain import (
    CircuitState,
    ConcurrencyError,
    CreativePlanDirection,
    CreativePlanPayload,
    ImageRole,
    ToolIntentProposal,
)
from commercevision_persistence import (
    MySqlApprovedGenerationAuthority,
    SqlAlchemyApprovedGenerationUnitOfWork,
    SqlAlchemyModelRouterUnitOfWork,
)
from commercevision_persistence.generation_models import (
    CandidateSlotModel,
    GenerationBatchModel,
)
from commercevision_persistence.models import (
    ApprovalModel,
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
                    authorized_resource_ids=authorized_asset_version_ids,
                    allowed_providers=frozenset({"provider-a"}),
                    allowed_cost_classes=frozenset({ToolCostClass.LOW}),
                    remaining_quota_units=1,
                    remaining_budget_units=remaining_budget_units,
                ),
                generation_tools={
                    ("provider-a.image.generate", "1.0"): "provider-a",
                },
                rights_policy_version=rights_policy_version,
                safety_policy_version="media-safety.v1",
                actor_id="generation-service",
            ),
        )
    )


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
