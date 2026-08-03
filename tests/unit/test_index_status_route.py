from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from commercevision_api.asset_routes import asset_router
from commercevision_api.errors import install_error_handlers
from commercevision_application import AuthenticatedPrincipal
from commercevision_contracts import AssetIndexStatusResponseV1
from commercevision_domain import NotFoundError
from fastapi import FastAPI
from fastapi.testclient import TestClient

ASSET_ID = "018f5f4d-7c11-7d11-8a11-333333333333"
VERSION_ID = "018f5f4d-7c11-7d11-8a11-444444444444"


class _PrincipalResolver:
    def resolve(self, token: str | None) -> AuthenticatedPrincipal:
        del token
        return AuthenticatedPrincipal(
            actor_id="asset-reader",
            workspace_ids=frozenset({"workspace-index-route", "workspace-other"}),
            admin_workspace_ids=frozenset(),
        )


class _AccessPolicy:
    def require_workspace(
        self,
        *,
        workspace_id: str,
        principal: AuthenticatedPrincipal,
    ) -> None:
        assert workspace_id in principal.workspace_ids


class _Status:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def get_current(
        self,
        *,
        workspace_id: str,
        asset_id: str,
    ) -> AssetIndexStatusResponseV1:
        self.calls.append((workspace_id, asset_id))
        if workspace_id != "workspace-index-route" or asset_id != ASSET_ID:
            raise NotFoundError("Asset was not found")
        return AssetIndexStatusResponseV1(
            asset_id=asset_id,
            asset_version_id=VERSION_ID,
            state="INDEXED",
            retryable=False,
            failure_reason=None,
            indexed_at=datetime(2026, 7, 31, tzinfo=UTC),
            updated_at=datetime(2026, 7, 31, tzinfo=UTC),
        )


def test_index_status_is_workspace_scoped_and_bounded() -> None:
    status = _Status()
    app = FastAPI()
    app.state.container = SimpleNamespace(
        principal_resolver=_PrincipalResolver(),
        access_policy=_AccessPolicy(),
        image_index_status=status,
    )
    app.include_router(asset_router)

    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/assets/{ASSET_ID}/index-status",
            headers={"X-Workspace-Id": "workspace-index-route"},
        )

    assert response.status_code == 200
    assert status.calls == [("workspace-index-route", ASSET_ID)]
    assert set(response.json()) == {
        "asset_id",
        "asset_version_id",
        "state",
        "retryable",
        "failure_reason",
        "indexed_at",
        "updated_at",
    }
    assert {
        "collection_id",
        "collection_name",
        "milvus_primary_key",
        "provider_request_id",
    }.isdisjoint(response.json())


def test_unknown_and_cross_workspace_asset_have_same_non_enumerating_projection() -> None:
    status = _Status()
    app = FastAPI()
    app.state.container = SimpleNamespace(
        principal_resolver=_PrincipalResolver(),
        access_policy=_AccessPolicy(),
        image_index_status=status,
    )
    install_error_handlers(app)

    @app.middleware("http")
    async def request_context(request, call_next):
        request.state.request_id = "request-index-status"
        request.state.trace_id = "trace-index-status"
        return await call_next(request)

    app.include_router(asset_router)
    unknown_id = "018f5f4d-7c11-7d11-8a11-999999999999"

    with TestClient(app) as client:
        unknown = client.get(
            f"/api/v1/assets/{unknown_id}/index-status",
            headers={"X-Workspace-Id": "workspace-index-route"},
        )
        cross_workspace = client.get(
            f"/api/v1/assets/{ASSET_ID}/index-status",
            headers={"X-Workspace-Id": "workspace-other"},
        )

    assert unknown.status_code == cross_workspace.status_code == 404
    assert unknown.json() == cross_workspace.json()
