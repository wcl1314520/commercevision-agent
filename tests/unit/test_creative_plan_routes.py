from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from commercevision_api.creative_plan_routes import router
from commercevision_api.errors import install_error_handlers
from commercevision_application import AuthenticatedPrincipal, CreativePlanWriteResult
from commercevision_domain import (
    ConcurrencyError,
    CreativePlanDirection,
    CreativePlanHead,
    CreativePlanPayload,
    CreativePlanProvenance,
    CreativePlanSource,
    CreativePlanVersion,
    ImageRole,
    InvalidTransitionError,
    NotFoundError,
)
from commercevision_domain.workflow.errors import IdempotencyConflictError
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

NOW = datetime(2026, 8, 5, 6, 0, tzinfo=UTC)
WORKFLOW_ID = "019b0000-0000-7000-8000-000000000810"
PLAN_ID = "019b0000-0000-7000-8000-000000000813"


def _current_plan() -> CreativePlanWriteResult:
    version = CreativePlanVersion.create(
        workspace_id="planning-domain",
        workflow_id=WORKFLOW_ID,
        creative_plan_id=PLAN_ID,
        version_number=1,
        supersedes_version_id=None,
        source=CreativePlanSource.AGENT,
        payload=CreativePlanPayload(
            directions=(
                CreativePlanDirection(
                    key="hero",
                    image_role=ImageRole.HERO,
                    scene="Clean studio",
                    composition="Centered product",
                    camera="Eye level",
                    lighting="Soft key light",
                    color_direction="Brand blue",
                    product_constraints=("Preserve packaging",),
                    required_elements=("Product",),
                    prohibited_elements=(),
                    citation_selections=(),
                    candidate_count=1,
                    quality_targets=("Sharp label",),
                    repair_scope=(),
                    tool_intents=(),
                ),
            )
        ),
        provenance=CreativePlanProvenance(
            product_brief_id="019b0000-0000-7000-8000-000000000811",
            product_brief_version=3,
            product_brief_sha256="1" * 64,
            brand_profile_id=None,
            brand_profile_version=None,
            brand_profile_sha256=None,
            retrieval_run_id="019b0000-0000-7000-8000-000000000812",
            retrieval_citation_ids=(),
            context_policy_version="planning-context-v1",
            context_sha256="2" * 64,
            prompt_id="creative-planner",
            prompt_revision="1.0.0",
            prompt_sha256="3" * 64,
        ),
        actor_id="fixture-planner",
        revision_reason=None,
        now=NOW,
    )
    return CreativePlanWriteResult(
        head=CreativePlanHead.from_first_version(
            version,
            retain_until=NOW + timedelta(days=30),
        ),
        version=version,
    )


class _CreativePlans:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.current = _current_plan()
        self.failure: Exception | None = None

    def _raise_failure(self) -> None:
        if self.failure is not None:
            raise self.failure

    def get_current(self, **kwargs: object) -> CreativePlanWriteResult:
        self.calls.append(kwargs)
        self._raise_failure()
        return self.current

    def get_version(self, **kwargs: object) -> CreativePlanVersion:
        self.calls.append(kwargs)
        return self.current.version

    def list_version_page(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(
            items=(self.current.version,),
            next_cursor="v1.test.opaque.signature",
        )

    def create_plan(self, **kwargs: object) -> CreativePlanWriteResult:
        self.calls.append(kwargs)
        self._raise_failure()
        return self.current

    def revise_plan(self, **kwargs: object) -> CreativePlanWriteResult:
        self.calls.append(kwargs)
        return self.current


class _PrincipalResolver:
    def resolve(self, token: str | None) -> AuthenticatedPrincipal:
        del token
        return AuthenticatedPrincipal(
            actor_id="creative-reviewer",
            workspace_ids=frozenset({"planning-domain"}),
            admin_workspace_ids=frozenset(),
        )


class _AccessPolicy:
    def __init__(self) -> None:
        self.workspace_checks = 0

    def require_workspace(self, **_: object) -> None:
        self.workspace_checks += 1


def _test_app() -> tuple[FastAPI, _CreativePlans, _AccessPolicy]:
    app = FastAPI()
    install_error_handlers(app)
    creative_plans = _CreativePlans()
    access_policy = _AccessPolicy()
    app.state.container = SimpleNamespace(
        principal_resolver=_PrincipalResolver(),
        access_policy=access_policy,
        creative_plans=creative_plans,
    )

    @app.middleware("http")
    async def correlation_context(request: Request, call_next):
        request.state.request_id = "request-creative-plan-route"
        request.state.trace_id = "trace-creative-plan-route"
        return await call_next(request)

    app.include_router(router)
    return app, creative_plans, access_policy


def _payload_json() -> dict[str, object]:
    return _current_plan().version.payload.to_canonical_data()


def _provenance_json() -> dict[str, object]:
    provenance = _current_plan().version.provenance
    return {
        "product_brief_id": provenance.product_brief_id,
        "product_brief_version": provenance.product_brief_version,
        "product_brief_sha256": provenance.product_brief_sha256,
        "brand_profile_id": provenance.brand_profile_id,
        "brand_profile_version": provenance.brand_profile_version,
        "brand_profile_sha256": provenance.brand_profile_sha256,
        "retrieval_run_id": provenance.retrieval_run_id,
        "retrieval_citation_ids": list(provenance.retrieval_citation_ids),
        "context_policy_version": provenance.context_policy_version,
        "context_sha256": provenance.context_sha256,
        "prompt_id": provenance.prompt_id,
        "prompt_revision": provenance.prompt_revision,
        "prompt_sha256": provenance.prompt_sha256,
    }


def test_get_current_creative_plan_is_workspace_scoped_and_exposes_exact_facts() -> None:
    app, creative_plans, access_policy = _test_app()

    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/creative-plans/{PLAN_ID}",
            headers={"X-Workspace-Id": "planning-domain"},
            params={"workflow_id": WORKFLOW_ID},
        )

    assert response.status_code == 200
    assert access_policy.workspace_checks == 1
    assert creative_plans.calls == [
        {
            "workspace_id": "planning-domain",
            "workflow_id": WORKFLOW_ID,
            "creative_plan_id": PLAN_ID,
        }
    ]
    body = response.json()
    assert body["head"]["current_version_number"] == 1
    assert body["version"]["creative_plan_id"] == PLAN_ID
    assert body["version"]["payload"]["directions"][0]["key"] == "hero"
    assert body["version"]["provenance"]["prompt_sha256"] == "3" * 64
    assert "object_key" not in response.text
    assert "secret" not in response.text


def test_get_exact_creative_plan_version_forwards_bounded_version_identity() -> None:
    app, creative_plans, access_policy = _test_app()

    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/creative-plans/{PLAN_ID}/versions/1",
            headers={"X-Workspace-Id": "planning-domain"},
            params={"workflow_id": WORKFLOW_ID},
        )

    assert response.status_code == 200
    assert access_policy.workspace_checks == 1
    assert creative_plans.calls == [
        {
            "workspace_id": "planning-domain",
            "workflow_id": WORKFLOW_ID,
            "creative_plan_id": PLAN_ID,
            "version_number": 1,
        }
    ]
    assert response.json()["id"] == creative_plans.current.version.id


def test_creative_plan_read_rejects_noncanonical_identity_before_service() -> None:
    app, creative_plans, _ = _test_app()

    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/creative-plans/{PLAN_ID.upper()}/versions/1",
            headers={"X-Workspace-Id": "planning-domain"},
            params={"workflow_id": WORKFLOW_ID},
        )

    assert response.status_code == 422
    assert creative_plans.calls == []


def test_creative_plan_history_is_bounded_and_returns_opaque_cursor() -> None:
    app, creative_plans, access_policy = _test_app()

    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/creative-plans/{PLAN_ID}/versions",
            headers={"X-Workspace-Id": "planning-domain"},
            params={"workflow_id": WORKFLOW_ID, "limit": 1},
        )

    assert response.status_code == 200
    assert access_policy.workspace_checks == 1
    assert creative_plans.calls == [
        {
            "workspace_id": "planning-domain",
            "workflow_id": WORKFLOW_ID,
            "creative_plan_id": PLAN_ID,
            "limit": 1,
            "cursor": None,
        }
    ]
    assert len(response.json()["items"]) == 1
    assert response.json()["next_cursor"] == "v1.test.opaque.signature"


def test_create_creative_plan_requires_actor_idempotency_and_expected_versions() -> None:
    app, creative_plans, access_policy = _test_app()

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/creative-plans",
            headers={
                "X-Workspace-Id": "planning-domain",
                "X-Actor-Id": "creative-reviewer",
                "Idempotency-Key": "create-plan-test",
            },
            json={
                "workflow_id": WORKFLOW_ID,
                "creative_plan_id": PLAN_ID,
                "payload": _payload_json(),
                "provenance": _provenance_json(),
                "expected_workflow_version": 7,
                "expected_head_version": 0,
            },
        )

    assert response.status_code == 201
    assert access_policy.workspace_checks == 1
    call = creative_plans.calls[0]
    assert call["workspace_id"] == "planning-domain"
    assert call["actor_id"] == "creative-reviewer"
    assert call["idempotency_key"] == "create-plan-test"
    assert call["trace_id"] == "trace-creative-plan-route"
    assert call["request"].expected_workflow_version == 7
    assert response.json()["head"]["current_version_number"] == 1


def test_revise_creative_plan_requires_reason_and_both_expected_versions() -> None:
    app, creative_plans, access_policy = _test_app()

    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/creative-plans/{PLAN_ID}:revise",
            headers={
                "X-Workspace-Id": "planning-domain",
                "X-Actor-Id": "creative-reviewer",
                "Idempotency-Key": "revise-plan-test",
            },
            json={
                "workflow_id": WORKFLOW_ID,
                "payload": _payload_json(),
                "revision_reason": "Use the approved retail setting",
                "expected_workflow_version": 8,
                "expected_head_version": 1,
            },
        )

    assert response.status_code == 201
    assert access_policy.workspace_checks == 1
    call = creative_plans.calls[0]
    assert call["creative_plan_id"] == PLAN_ID
    assert call["actor_id"] == "creative-reviewer"
    assert call["request"].expected_head_version == 1
    assert call["idempotency_key"] == "revise-plan-test"


@pytest.mark.parametrize(
    ("failure", "expected_status", "expected_code"),
    (
        (NotFoundError("Creative Plan does not exist"), 404, "NOT_FOUND"),
        (ConcurrencyError("head changed"), 409, "VERSION_CONFLICT"),
        (InvalidTransitionError("workflow moved"), 409, "INVALID_TRANSITION"),
        (
            IdempotencyConflictError("key already used"),
            409,
            "IDEMPOTENCY_CONFLICT",
        ),
    ),
)
def test_creative_plan_public_errors_have_stable_distinct_codes(
    failure: Exception,
    expected_status: int,
    expected_code: str,
) -> None:
    app, creative_plans, _ = _test_app()
    creative_plans.failure = failure

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            f"/api/v1/creative-plans/{PLAN_ID}",
            headers={"X-Workspace-Id": "planning-domain"},
            params={"workflow_id": WORKFLOW_ID},
        )

    assert response.status_code == expected_status
    assert response.json()["code"] == expected_code
    assert response.json()["retryable"] is False


def test_creative_plan_history_rejects_oversized_cursor_before_service() -> None:
    app, creative_plans, _ = _test_app()

    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/creative-plans/{PLAN_ID}/versions",
            headers={"X-Workspace-Id": "planning-domain"},
            params={"workflow_id": WORKFLOW_ID, "cursor": "x" * 257},
        )

    assert response.status_code == 422
    assert creative_plans.calls == []
