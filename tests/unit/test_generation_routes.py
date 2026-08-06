from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from commercevision_api.errors import install_error_handlers
from commercevision_api.generation_routes import router
from commercevision_application import ApprovedPlanGenerationCommand, AuthenticatedPrincipal
from commercevision_domain.operations import OperationKind, OperationState
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

WORKSPACE_ID = "phase4-generation-api"
ACTOR_ID = "generation-service"
WORKFLOW_ID = "019b0000-0000-7000-8000-000000000701"
PLAN_ID = "019b0000-0000-7000-8000-000000000702"
PLAN_VERSION_ID = "019b0000-0000-7000-8000-000000000703"
APPROVAL_ID = "019b0000-0000-7000-8000-000000000704"
BATCH_ID = "019b0000-0000-7000-8000-000000000705"
SLOT_ID = "019b0000-0000-7000-8000-000000000706"
OPERATION_ID = "019b0000-0000-7000-8000-000000000707"
NOW = datetime(2026, 8, 6, 8, 0, tzinfo=UTC)


class _Resolver:
    @staticmethod
    def resolve(_: str | None) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(
            actor_id=ACTOR_ID,
            workspace_ids=frozenset({WORKSPACE_ID}),
            admin_workspace_ids=frozenset(),
        )


class _Access:
    def __init__(self) -> None:
        self.workspace_checks: list[str] = []

    def require_workspace(self, *, workspace_id: str, principal: object) -> None:
        del principal
        self.workspace_checks.append(workspace_id)


def _result() -> SimpleNamespace:
    operation = SimpleNamespace(
        id=OPERATION_ID,
        kind=OperationKind.IMAGE_GENERATION,
        state=OperationState.PENDING,
        attempt_count=0,
        max_attempts=3,
        execution_deadline_at=NOW + timedelta(hours=1),
        created_at=NOW,
        updated_at=NOW,
        version=1,
        provider_request_id="must-not-leak",
        lease_token="must-not-leak",
    )
    slot = SimpleNamespace(
        id=SLOT_ID,
        candidate_index=0,
        durable_operation_id=OPERATION_ID,
        operation_kind=OperationKind.IMAGE_GENERATION,
        logical_identity_sha256="8" * 64,
        operation_idempotency_key="must-not-leak",
    )
    batch = SimpleNamespace(
        id=BATCH_ID,
        batch_sha256="9" * 64,
        workspace_id=WORKSPACE_ID,
        workflow_id=WORKFLOW_ID,
        workflow_version=2,
        creative_plan_version_id=PLAN_VERSION_ID,
        plan_approval_id=APPROVAL_ID,
        direction_key="main-image",
        tool_intent_key="generate-main-image",
        tool_intent_sha256="a" * 64,
        prompt_sha256="b" * 64,
        context_sha256="c" * 64,
        route_decision_sha256="d" * 64,
        route_request_sha256="e" * 64,
        operation_kind=OperationKind.IMAGE_GENERATION,
        authorized_asset_version_ids=(),
        candidate_count=1,
        route_policy_version="route-policy.v1",
        tool_policy_version="tool-policy.v1",
        rights_policy_version="rights-policy.v1",
        safety_policy_version="safety-policy.v1",
        workflow_deadline=NOW + timedelta(hours=2),
        source_rights_deadline=None,
        retention_deadline=NOW + timedelta(hours=1),
        created_by=ACTOR_ID,
        created_at=NOW,
        provider_id="must-not-leak",
        endpoint_id="must-not-leak",
    )
    return SimpleNamespace(batch=batch, slots=(slot,), operations=(operation,), replayed=False)


class _GenerationService:
    def __init__(self) -> None:
        self.starts: list[dict[str, object]] = []
        self.reads: list[dict[str, str]] = []

    def start(self, **kwargs: object) -> SimpleNamespace:
        self.starts.append(kwargs)
        return _result()

    def get(self, **kwargs: str) -> SimpleNamespace:
        self.reads.append(kwargs)
        return _result()


def _test_app() -> tuple[FastAPI, _GenerationService, _Access]:
    app = FastAPI()
    install_error_handlers(app)
    service = _GenerationService()
    access = _Access()
    app.state.container = SimpleNamespace(
        principal_resolver=_Resolver(),
        access_policy=access,
        generation=service,
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = "request-generation-route"
        request.state.trace_id = "trace-generation-route"
        return await call_next(request)

    app.include_router(router)
    return app, service, access


def _payload() -> dict[str, object]:
    return {
        "workflow_id": WORKFLOW_ID,
        "expected_workflow_version": 2,
        "creative_plan_id": PLAN_ID,
        "creative_plan_version_id": PLAN_VERSION_ID,
        "creative_plan_version": 1,
        "approval_id": APPROVAL_ID,
        "direction_key": "main-image",
        "tool_intent_key": "generate-main-image",
        "route_decision_sha256": "d" * 64,
    }


def _assert_no_private_provider_details(value: object) -> None:
    private_tokens = {
        "provider_id",
        "provider_request_id",
        "endpoint_id",
        "endpoint_host",
        "lease_token",
        "operation_idempotency_key",
        "arguments",
    }
    if isinstance(value, dict):
        assert private_tokens.isdisjoint(value)
        for nested in value.values():
            _assert_no_private_provider_details(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_no_private_provider_details(nested)


def test_start_and_read_generation_batch_keep_exact_workspace_and_safe_provenance() -> None:
    app, service, access = _test_app()
    headers = {
        "X-Workspace-Id": WORKSPACE_ID,
        "X-Actor-Id": ACTOR_ID,
        "Idempotency-Key": "generation-command-001",
        "X-Trusted-Principal": "fixture",
    }

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/generation-batches",
            headers=headers,
            json=_payload(),
        )
        loaded = client.get(
            f"/api/v1/generation-batches/{BATCH_ID}",
            headers={
                "X-Workspace-Id": WORKSPACE_ID,
                "X-Trusted-Principal": "fixture",
            },
        )

    assert created.status_code == 201, created.text
    assert loaded.status_code == 200, loaded.text
    assert created.json() == loaded.json()
    assert access.workspace_checks == [WORKSPACE_ID, WORKSPACE_ID]
    assert service.starts == [
        {
            "command": ApprovedPlanGenerationCommand(
                workspace_id=WORKSPACE_ID,
                workflow_id=WORKFLOW_ID,
                expected_workflow_version=2,
                creative_plan_id=PLAN_ID,
                creative_plan_version_id=PLAN_VERSION_ID,
                creative_plan_version=1,
                approval_id=APPROVAL_ID,
                direction_key="main-image",
                tool_intent_key="generate-main-image",
                route_decision_sha256="d" * 64,
            ),
            "idempotency_key": "generation-command-001",
            "trace_id": "trace-generation-route",
        }
    ]
    assert service.reads == [{"workspace_id": WORKSPACE_ID, "batch_id": BATCH_ID}]
    body = created.json()
    assert body["batch_sha256"] == "9" * 64
    assert body["plan_approval_id"] == APPROVAL_ID
    assert body["route_decision_sha256"] == "d" * 64
    assert body["slots"][0]["operation"]["state"] == "PENDING"
    _assert_no_private_provider_details(body)


def test_generation_openapi_locks_command_and_read_headers() -> None:
    app, _, _ = _test_app()
    schema = app.openapi()

    create_headers = {
        item["name"]: item["required"]
        for item in schema["paths"]["/api/v1/generation-batches"]["post"]["parameters"]
        if item["in"] == "header"
    }
    read_headers = {
        item["name"]: item["required"]
        for item in schema["paths"]["/api/v1/generation-batches/{batch_id}"]["get"]["parameters"]
        if item["in"] == "header"
    }

    assert create_headers == {
        "X-Workspace-Id": True,
        "X-Actor-Id": True,
        "Idempotency-Key": True,
        "X-Trusted-Principal": False,
    }
    assert read_headers == {
        "X-Workspace-Id": True,
        "X-Trusted-Principal": False,
    }
