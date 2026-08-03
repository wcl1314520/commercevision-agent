from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from commercevision_api.collection_rebuild_routes import router
from commercevision_application import AuthenticatedPrincipal
from commercevision_contracts import CollectionRebuildResponseV1, CollectionRebuildValidationV1
from commercevision_domain import CollectionRebuildState, VectorKind
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

REBUILD_ID = "018f5f4d-7c11-7d11-8a11-444444444444"
NOW = datetime(2026, 8, 4, 8, 0, tzinfo=UTC)


def _response(state: CollectionRebuildState = CollectionRebuildState.REQUESTED):
    validation = (
        CollectionRebuildValidationV1(
            expected_row_count=0,
            actual_row_count=0,
            missing_primary_key_count=0,
            unexpected_primary_key_count=0,
            sampled_visibility_count=0,
            sampled_visibility_failures=0,
            ann_recall_at_10=1.0,
            minimum_ann_recall_at_10=0.95,
            fixed_query_pass_count=0,
            fixed_query_total_count=0,
            unauthorized_result_count=0,
            queries_with_unauthorized_results=0,
            accepted=True,
        )
        if state is CollectionRebuildState.RETIRING
        else None
    )
    return CollectionRebuildResponseV1(
        id=REBUILD_ID,
        operation_id=REBUILD_ID,
        vector_kind=VectorKind.IMAGE,
        state=state,
        version=1,
        snapshot_watermark=NOW,
        replay_watermark=None,
        backfill_cursor=None,
        replay_cursor=None,
        processed_count=0,
        validation=validation,
        failure_code=None,
        retire_after=None,
        created_at=NOW,
        updated_at=NOW,
        progress=[],
    )


class _Resolver:
    def resolve(self, _: str | None) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(
            actor_id="collection-admin",
            workspace_ids=frozenset({"workspace-rebuild"}),
            admin_workspace_ids=frozenset({"workspace-rebuild"}),
        )


class _Access:
    def __init__(self) -> None:
        self.admin_checks = 0

    def require_admin(self, **_: object) -> None:
        self.admin_checks += 1


class _Rebuilds:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def request(self, **kwargs: object) -> CollectionRebuildResponseV1:
        self.calls.append(("request", kwargs))
        return _response()

    def get(self, **kwargs: object) -> CollectionRebuildResponseV1:
        self.calls.append(("get", kwargs))
        return _response()

    def request_validation(self, **kwargs: object) -> CollectionRebuildResponseV1:
        self.calls.append(("validate", kwargs))
        return _response(CollectionRebuildState.AWAITING_VALIDATION)

    def activate(self, **kwargs: object) -> CollectionRebuildResponseV1:
        self.calls.append(("activate", kwargs))
        return _response(CollectionRebuildState.RETIRING)


def _app() -> tuple[FastAPI, _Rebuilds, _Access]:
    app = FastAPI()
    rebuilds = _Rebuilds()
    access = _Access()
    app.state.container = SimpleNamespace(
        principal_resolver=_Resolver(),
        access_policy=access,
        collection_rebuilds=rebuilds,
    )

    @app.middleware("http")
    async def context(request: Request, call_next):
        request.state.trace_id = "trace-rebuild"
        return await call_next(request)

    app.include_router(router)
    return app, rebuilds, access


def test_request_rebuild_is_admin_only_and_forwards_version_fences() -> None:
    app, rebuilds, access = _app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/collections/rebuilds",
            headers={
                "X-Workspace-Id": "workspace-rebuild",
                "X-Actor-Id": "collection-admin",
                "Idempotency-Key": "rebuild-request-1",
            },
            json={
                "vector_kind": "IMAGE",
                "model_family": "clip",
                "model_id": "clip-v1",
                "pinned_revision": "fixture-epoch-v2",
                "dimension": 4,
                "schema_version": 2,
                "index_spec_version": "hnsw-cosine-v2",
                "expected_active_collection_version": 3,
                "expected_policy_pointer_version": 2,
            },
        )

    assert response.status_code == 202
    assert access.admin_checks == 1
    name, call = rebuilds.calls[0]
    assert name == "request"
    assert call["workspace_id"] == "workspace-rebuild"
    assert call["idempotency_key"] == "rebuild-request-1"
    assert call["trace_id"] == "trace-rebuild"
    assert call["request"].expected_active_collection_version == 3


def test_rebuild_status_and_actions_are_workspace_scoped() -> None:
    app, rebuilds, access = _app()
    headers = {
        "X-Workspace-Id": "workspace-rebuild",
        "X-Actor-Id": "collection-admin",
    }
    with TestClient(app) as client:
        status_response = client.get(
            f"/api/v1/collections/rebuilds/{REBUILD_ID}",
            headers={"X-Workspace-Id": "workspace-rebuild"},
        )
        validate_response = client.post(
            f"/api/v1/collections/rebuilds/{REBUILD_ID}:validate",
            headers=headers,
            json={"expected_version": 7},
        )
        activate_response = client.post(
            f"/api/v1/collections/rebuilds/{REBUILD_ID}:activate",
            headers=headers,
            json={"expected_version": 8},
        )

    assert (status_response.status_code, validate_response.status_code) == (200, 202)
    assert activate_response.status_code == 200
    assert access.admin_checks == 3
    assert [name for name, _ in rebuilds.calls] == ["get", "validate", "activate"]
    assert rebuilds.calls[1][1]["expected_version"] == 7
    assert rebuilds.calls[2][1]["expected_version"] == 8
