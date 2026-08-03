from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from commercevision_api.errors import install_error_handlers
from commercevision_api.retrieval_routes import router
from commercevision_application import AuthenticatedPrincipal
from commercevision_contracts import RetrievalResponseV1, RetrievalTemporaryReferenceV1
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

WORKSPACE = "workspace-retrieval-route"
ACTOR = "artist-1"
RUN_ID = "0198f4d8-1f7c-7b2d-8da9-214a92a884a1"


class _Resolver:
    @staticmethod
    def resolve(token):
        return AuthenticatedPrincipal(
            actor_id=ACTOR,
            workspace_ids=frozenset({WORKSPACE}),
            admin_workspace_ids=frozenset(),
        )


class _Access:
    @staticmethod
    def require_workspace(*, workspace_id, principal):
        assert workspace_id in principal.workspace_ids


class _Retrieval:
    query = None

    def execute(self, query):
        self.query = query
        return RetrievalResponseV1(
            retrieval_policy_version="retrieval-policy-v1",
            complete_hybrid=True,
            degradations=[],
            eligible_asset_version_count=0,
            fused_candidate_count=0,
            final_authorized_candidate_count=0,
            latency_ms=1,
            citations=[],
        )


class _Runs:
    query = None
    response = None

    def record(self, query, response):
        self.query = query
        self.response = response
        return response.model_copy(update={"retrieval_run_id": RUN_ID})

    def get(self, *, workspace_id, run_id):
        assert workspace_id == WORKSPACE and run_id == RUN_ID
        return RetrievalResponseV1(
            retrieval_run_id=RUN_ID,
            retrieval_policy_version="retrieval-policy-v1",
            complete_hybrid=True,
            degradations=[],
            eligible_asset_version_count=0,
            fused_candidate_count=0,
            final_authorized_candidate_count=0,
            latency_ms=1,
            citations=[],
        )


class _Previews:
    call = None

    def exchange(self, **kwargs):
        self.call = kwargs
        return RetrievalTemporaryReferenceV1(
            method="GET",
            url="https://controlled.invalid/preview",
            required_headers={},
            expires_at=datetime.now(UTC) + timedelta(seconds=45),
        )


def _client():
    app = FastAPI()
    install_error_handlers(app)
    retrieval = _Retrieval()
    runs = _Runs()
    previews = _Previews()
    app.state.container = SimpleNamespace(
        principal_resolver=_Resolver(),
        access_policy=_Access(),
        retrieval=retrieval,
        retrieval_runs=runs,
        retrieval_previews=previews,
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = "request-1"
        request.state.trace_id = "trace-1"
        return await call_next(request)

    app.include_router(router)
    return TestClient(app), retrieval, runs, previews


def _query():
    return {
        "workspace_id": WORKSPACE,
        "requester_id": ACTOR,
        "product_id": "0198f4d8-1f7c-7b2d-8da9-214a92a884b1",
        "purpose": "RETRIEVAL",
        "provider": "fixture",
        "requires_derivative": False,
        "roles": [],
        "vector_kinds": ["PRODUCT_FUSED"],
        "query_text": "lipstick",
        "explicit_reference_asset_version_ids": [],
        "result_limit": 5,
        "candidate_limit": 20,
        "retrieval_policy_version": "retrieval-policy-v1",
    }


def _headers():
    return {
        "X-Workspace-Id": WORKSPACE,
        "X-Actor-Id": ACTOR,
        "X-Trusted-Principal": "fixture",
    }


def test_execute_retains_the_authorized_response() -> None:
    client, retrieval, runs, _ = _client()

    response = client.post("/api/v1/retrieval-runs", headers=_headers(), json=_query())

    assert response.status_code == 200
    assert response.json()["retrieval_run_id"] == RUN_ID
    assert retrieval.query.requester_id == ACTOR
    assert runs.query == retrieval.query


def test_get_and_preview_keep_workspace_and_requester_boundaries() -> None:
    client, _, _, previews = _client()

    loaded = client.get(
        f"/api/v1/retrieval-runs/{RUN_ID}",
        headers={
            "X-Workspace-Id": WORKSPACE,
            "X-Trusted-Principal": "fixture",
        },
    )
    preview = client.post(
        f"/api/v1/retrieval-runs/{RUN_ID}/results/1:preview",
        headers=_headers(),
        json={"preview_reference_token": "a" * 43},
    )

    assert loaded.status_code == 200
    assert preview.status_code == 200
    assert previews.call == {
        "workspace_id": WORKSPACE,
        "requester_id": ACTOR,
        "run_id": RUN_ID,
        "rank": 1,
        "token": "a" * 43,
    }
