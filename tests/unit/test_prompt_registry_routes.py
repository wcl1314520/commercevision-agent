from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from commercevision_api.errors import install_error_handlers
from commercevision_api.prompt_registry_routes import router
from commercevision_application import AuthenticatedPrincipal
from commercevision_contracts import (
    PromptProductionPointerResponseV1,
    PromptRevisionResponseV1,
)
from commercevision_domain import PromptRevisionStatus
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

REVISION_ID = "019f8a00-0000-7000-8000-000000000801"
NOW = datetime(2026, 8, 4, 11, 0, tzinfo=UTC)


def _revision_response() -> PromptRevisionResponseV1:
    return PromptRevisionResponseV1(
        id=REVISION_ID,
        workspace_id="planning-domain",
        prompt_id="creative-planner",
        semantic_revision="1.0.0",
        node="CREATE_CREATIVE_PLAN",
        category_applicability=["beauty"],
        model_family_applicability=["fixture-planner"],
        input_schema_version="planning-context.v1",
        output_schema_version="creative-plan.v1",
        policy_version="prompt-policy.v1",
        content="Plan {{ planning_context }} into {{ output_schema }}.",
        variables=[
            {"name": "planning_context", "required": True},
            {"name": "output_schema", "required": True},
        ],
        content_sha256="a" * 64,
        status=PromptRevisionStatus.DRAFT,
        version=1,
        created_by="prompt-admin",
        change_summary="Initial revision",
        created_at=NOW,
        updated_at=NOW,
    )


class _PromptRegistry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def create_revision(self, **kwargs: object) -> PromptRevisionResponseV1:
        self.calls.append(("create_revision", kwargs))
        return _revision_response()

    def submit_for_review(self, **kwargs: object) -> PromptRevisionResponseV1:
        self.calls.append(("submit_for_review", kwargs))
        return _revision_response()

    def stage(self, **kwargs: object) -> PromptRevisionResponseV1:
        self.calls.append(("stage", kwargs))
        return _revision_response()

    def publish(self, **kwargs: object) -> PromptRevisionResponseV1:
        self.calls.append(("publish", kwargs))
        return _revision_response()

    def deprecate(self, **kwargs: object) -> PromptRevisionResponseV1:
        self.calls.append(("deprecate", kwargs))
        return _revision_response()

    def select_production(self, **kwargs: object) -> PromptProductionPointerResponseV1:
        self.calls.append(("select_production", kwargs))
        return PromptProductionPointerResponseV1(
            workspace_id="planning-domain",
            prompt_id="creative-planner",
            node="CREATE_CREATIVE_PLAN",
            revision_id=REVISION_ID,
            semantic_revision="1.0.0",
            content_sha256="a" * 64,
            version=2,
            updated_by="prompt-admin",
            updated_at=NOW,
        )

    def resolve_production(self, **kwargs: object) -> PromptRevisionResponseV1:
        self.calls.append(("resolve_production", kwargs))
        return _revision_response()


class _PrincipalResolver:
    def resolve(self, token: str | None) -> AuthenticatedPrincipal:
        del token
        return AuthenticatedPrincipal(
            actor_id="prompt-admin",
            workspace_ids=frozenset({"planning-domain"}),
            admin_workspace_ids=frozenset({"planning-domain"}),
        )


class _AccessPolicy:
    def __init__(self) -> None:
        self.workspace_checks = 0
        self.admin_checks = 0

    def require_workspace(self, **_: object) -> None:
        self.workspace_checks += 1

    def require_admin(self, **_: object) -> None:
        self.admin_checks += 1


def _test_app() -> tuple[FastAPI, _PromptRegistry, _AccessPolicy]:
    app = FastAPI()
    install_error_handlers(app)
    prompts = _PromptRegistry()
    access_policy = _AccessPolicy()
    app.state.container = SimpleNamespace(
        principal_resolver=_PrincipalResolver(),
        access_policy=access_policy,
        prompt_registry=prompts,
    )

    @app.middleware("http")
    async def correlation_context(request: Request, call_next):
        request.state.request_id = "request-prompt-route"
        request.state.trace_id = "trace-prompt-route"
        return await call_next(request)

    app.include_router(router)
    return app, prompts, access_policy


def _create_payload() -> dict[str, object]:
    return {
        "prompt_id": "creative-planner",
        "semantic_revision": "1.0.0",
        "node": "CREATE_CREATIVE_PLAN",
        "category_applicability": ["beauty"],
        "model_family_applicability": ["fixture-planner"],
        "input_schema_version": "planning-context.v1",
        "output_schema_version": "creative-plan.v1",
        "policy_version": "prompt-policy.v1",
        "content": "Plan {{ planning_context }} into {{ output_schema }}.",
        "variables": [
            {"name": "planning_context", "required": True},
            {"name": "output_schema", "required": True},
        ],
        "change_summary": "Initial revision",
    }


def test_prompt_create_requires_admin_and_forwards_idempotency() -> None:
    app, prompts, access_policy = _test_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/prompts/revisions",
            headers={
                "X-Workspace-Id": "planning-domain",
                "X-Actor-Id": "prompt-admin",
                "Idempotency-Key": "test-test-test",
            },
            json=_create_payload(),
        )

    assert response.status_code == 201
    assert access_policy.admin_checks == 1
    name, call = prompts.calls[0]
    assert name == "create_revision"
    assert call["actor_id"] == "prompt-admin"
    assert call["idempotency_key"] == "test-test-test"
    assert call["trace_id"] == "trace-prompt-route"


def test_prompt_resolve_requires_membership_and_forwards_exact_dimensions() -> None:
    app, prompts, access_policy = _test_app()
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/prompts/creative-planner/production",
            headers={"X-Workspace-Id": "planning-domain"},
            params={
                "node": "CREATE_CREATIVE_PLAN",
                "category": "beauty",
                "model_family": "fixture-planner",
            },
        )

    assert response.status_code == 200
    assert access_policy.workspace_checks == 1
    assert access_policy.admin_checks == 0
    assert prompts.calls == [
        (
            "resolve_production",
            {
                "workspace_id": "planning-domain",
                "prompt_id": "creative-planner",
                "node": "CREATE_CREATIVE_PLAN",
                "category": "beauty",
                "model_family": "fixture-planner",
            },
        )
    ]


def test_prompt_mutation_rejects_noncanonical_revision_id_before_service() -> None:
    app, prompts, _ = _test_app()
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/prompts/revisions/{REVISION_ID.upper()}:publish",
            headers={
                "X-Workspace-Id": "planning-domain",
                "X-Actor-Id": "prompt-admin",
                "Idempotency-Key": "test-test-test",
            },
            json={"expected_version": 3},
        )

    assert response.status_code == 422
    assert prompts.calls == []


def test_prompt_production_selection_forwards_expected_pointer_version() -> None:
    app, prompts, access_policy = _test_app()
    with TestClient(app) as client:
        response = client.put(
            "/api/v1/prompts/creative-planner/production",
            headers={
                "X-Workspace-Id": "planning-domain",
                "X-Actor-Id": "prompt-admin",
                "Idempotency-Key": "test-test-test",
            },
            json={"revision_id": REVISION_ID, "expected_pointer_version": 1},
        )

    assert response.status_code == 200
    assert access_policy.admin_checks == 1
    name, call = prompts.calls[0]
    assert name == "select_production"
    assert call["prompt_id"] == "creative-planner"
    assert call["request"].expected_pointer_version == 1


def test_prompt_registry_openapi_exposes_required_tenant_and_command_headers() -> None:
    app, _, _ = _test_app()
    schema = app.openapi()
    create_operation = schema["paths"]["/api/v1/prompts/revisions"]["post"]
    headers = {
        parameter["name"]: parameter["required"]
        for parameter in create_operation["parameters"]
        if parameter["in"] == "header"
    }
    resolve_operation = schema["paths"]["/api/v1/prompts/{prompt_id}/production"]["get"]
    required_queries = {
        parameter["name"]
        for parameter in resolve_operation["parameters"]
        if parameter["in"] == "query" and parameter["required"]
    }

    assert headers == {
        "X-Workspace-Id": True,
        "X-Actor-Id": True,
        "Idempotency-Key": True,
        "X-Trusted-Principal": False,
    }
    assert required_queries == {"node", "category", "model_family"}
