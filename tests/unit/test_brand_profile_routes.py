from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from commercevision_api.brand_profile_routes import router
from commercevision_api.errors import install_error_handlers
from commercevision_application import AuthenticatedPrincipal
from commercevision_contracts import (
    BrandProfileListResponseV1,
    BrandProfileResponseV1,
    BrandProfileValidationResponseV1,
    BrandProfileVersionListResponseV1,
)
from commercevision_domain import BrandProfileState
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

PROFILE_ID = "018f5f4d-7c11-7d11-8a11-333333333333"
NOW = datetime(2026, 7, 30, 8, 0, tzinfo=UTC)
SIGNED_CURSOR = "v1.current.cGF5bG9hZA.c2lnbmF0dXJl"


def _draft_payload() -> dict[str, object]:
    return {
        "rules": [],
        "approved_colors": [],
        "required_marks": [],
        "prohibited_elements": [],
        "tone_constraints": [],
        "copy_constraints": [],
        "purpose": "commerce.image-generation",
        "provider": "alibaba",
        "requires_derivative": False,
        "selected_assets": [],
    }


def _profile_response() -> BrandProfileResponseV1:
    return BrandProfileResponseV1(
        id=PROFILE_ID,
        workspace_id="workspace-route-test",
        brand="CommerceVision",
        profile_key="cn-primary",
        state=BrandProfileState.DRAFT,
        draft=_draft_payload(),
        current_version_id=None,
        current_version_number=0,
        version=1,
        stale_at=None,
        created_by="brand-admin",
        created_at=NOW,
        updated_by="brand-admin",
        updated_at=NOW,
    )


class _PrincipalResolver:
    def resolve(self, token: str | None) -> AuthenticatedPrincipal:
        del token
        return AuthenticatedPrincipal(
            actor_id="brand-admin",
            workspace_ids=frozenset({"workspace-route-test"}),
            admin_workspace_ids=frozenset({"workspace-route-test"}),
        )


class _AccessPolicy:
    def __init__(self) -> None:
        self.workspace_checks = 0
        self.admin_checks = 0

    def require_workspace(
        self,
        *,
        workspace_id: str,
        principal: AuthenticatedPrincipal,
    ) -> None:
        assert workspace_id in principal.workspace_ids
        self.workspace_checks += 1

    def require_admin(
        self,
        *,
        workspace_id: str,
        principal: AuthenticatedPrincipal,
    ) -> None:
        assert workspace_id in principal.admin_workspace_ids
        self.admin_checks += 1


class _BrandProfiles:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def create(self, **kwargs: object) -> BrandProfileResponseV1:
        self.calls.append(("create", kwargs))
        return _profile_response()

    def list_profiles(self, **kwargs: object) -> BrandProfileListResponseV1:
        self.calls.append(("list_profiles", kwargs))
        return BrandProfileListResponseV1(
            items=[_profile_response()],
            next_cursor=SIGNED_CURSOR,
        )

    def validate(self, **kwargs: object) -> BrandProfileValidationResponseV1:
        self.calls.append(("validate", kwargs))
        return BrandProfileValidationResponseV1(
            profile_id=PROFILE_ID,
            profile_version=1,
            valid=True,
            decided_at=NOW,
            issues=[],
        )

    def get(self, **kwargs: object) -> BrandProfileResponseV1:
        self.calls.append(("get", kwargs))
        return _profile_response()

    def update_draft(self, **kwargs: object) -> BrandProfileResponseV1:
        self.calls.append(("update_draft", kwargs))
        return _profile_response()

    def publish(self, **kwargs: object) -> BrandProfileResponseV1:
        self.calls.append(("publish", kwargs))
        return _profile_response()

    def list_versions(self, **kwargs: object) -> BrandProfileVersionListResponseV1:
        self.calls.append(("list_versions", kwargs))
        return BrandProfileVersionListResponseV1(items=[], next_cursor=None)

    def get_version(self, **kwargs: object) -> object:
        raise AssertionError("not exercised by this route test")


def _test_app() -> tuple[FastAPI, _BrandProfiles, _AccessPolicy]:
    app = FastAPI()
    install_error_handlers(app)
    brand_profiles = _BrandProfiles()
    access_policy = _AccessPolicy()
    app.state.container = SimpleNamespace(
        principal_resolver=_PrincipalResolver(),
        access_policy=access_policy,
        brand_profiles=brand_profiles,
    )

    @app.middleware("http")
    async def correlation_context(request: Request, call_next):
        request.state.request_id = "request-route-test"
        request.state.trace_id = "trace-route-test"
        return await call_next(request)

    app.include_router(router)
    return app, brand_profiles, access_policy


def test_brand_profile_mutations_require_admin_and_forward_idempotency() -> None:
    app, brand_profiles, access_policy = _test_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/brand-profiles",
            headers={
                "X-Workspace-Id": "workspace-route-test",
                "X-Actor-Id": "brand-admin",
                "Idempotency-Key": "create-profile-key",
            },
            json={
                "brand": "CommerceVision",
                "profile_key": "cn-primary",
                "draft": _draft_payload(),
            },
        )

    assert response.status_code == 201
    assert access_policy.admin_checks == 1
    name, call = brand_profiles.calls[0]
    assert name == "create"
    assert call["workspace_id"] == "workspace-route-test"
    assert call["actor_id"] == "brand-admin"
    assert call["idempotency_key"] == "create-profile-key"
    assert call["trace_id"] == "trace-route-test"


def test_brand_profile_reads_require_membership_but_not_admin() -> None:
    app, brand_profiles, access_policy = _test_app()
    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/brand-profiles?brand=CommerceVision&limit=7&cursor={SIGNED_CURSOR}",
            headers={"X-Workspace-Id": "workspace-route-test"},
        )

    assert response.status_code == 200
    assert access_policy.workspace_checks == 1
    assert access_policy.admin_checks == 0
    assert brand_profiles.calls == [
        (
            "list_profiles",
            {
                "workspace_id": "workspace-route-test",
                "brand": "CommerceVision",
                "limit": 7,
                "cursor": SIGNED_CURSOR,
            },
        )
    ]


def test_cryptographically_invalid_cursor_returns_stable_public_error() -> None:
    app, brand_profiles, _ = _test_app()
    invalid_cursor = "v1.current.cGF5bG9hZA.invalid_signature"

    def reject_cursor(**_: object) -> BrandProfileListResponseV1:
        raise ValueError("Brand Profile cursor is invalid")

    brand_profiles.list_profiles = reject_cursor  # type: ignore[method-assign]
    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/brand-profiles?cursor={invalid_cursor}",
            headers={"X-Workspace-Id": "workspace-route-test"},
        )

    assert response.status_code == 400
    assert response.json() == {
        "code": "INVALID_ARGUMENT",
        "message": "Brand Profile cursor is invalid",
        "category": "validation",
        "retryable": False,
        "details": {},
        "request_id": "request-route-test",
        "trace_id": "trace-route-test",
    }
    assert invalid_cursor not in response.text


def test_brand_profile_validate_is_admin_only_without_idempotency_header() -> None:
    app, brand_profiles, access_policy = _test_app()
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/brand-profiles/{PROFILE_ID}:validate",
            headers={
                "X-Workspace-Id": "workspace-route-test",
                "X-Actor-Id": "brand-admin",
            },
            json={"expected_version": 1},
        )

    assert response.status_code == 200
    assert access_policy.admin_checks == 1
    assert brand_profiles.calls[0][0] == "validate"


def test_publish_returns_created_and_profile_paths_require_canonical_uuid() -> None:
    app, brand_profiles, _ = _test_app()
    with TestClient(app) as client:
        published = client.post(
            f"/api/v1/brand-profiles/{PROFILE_ID}:publish",
            headers={
                "X-Workspace-Id": "workspace-route-test",
                "X-Actor-Id": "brand-admin",
                "Idempotency-Key": "publish-profile-key",
            },
            json={"expected_version": 1},
        )
        malformed = client.get(
            "/api/v1/brand-profiles/not-a-uuid",
            headers={"X-Workspace-Id": "workspace-route-test"},
        )
        noncanonical = client.get(
            f"/api/v1/brand-profiles/{PROFILE_ID.upper()}",
            headers={"X-Workspace-Id": "workspace-route-test"},
        )

    assert published.status_code == 201
    assert malformed.status_code == 422
    assert noncanonical.status_code == 422
    assert [name for name, _ in brand_profiles.calls] == ["publish"]


def test_profile_list_rejects_untrimmed_brand_filter_at_http_boundary() -> None:
    app, brand_profiles, _ = _test_app()
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/brand-profiles?brand=%20CommerceVision",
            headers={"X-Workspace-Id": "workspace-route-test"},
        )

    assert response.status_code == 422
    assert brand_profiles.calls == []
