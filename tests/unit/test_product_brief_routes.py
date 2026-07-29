from datetime import UTC, datetime
from types import SimpleNamespace

from commercevision_api.errors import install_error_handlers
from commercevision_api.product_brief_routes import router
from commercevision_application.product_brief_views import (
    ProductBriefAnalysisWorkflowProjection,
    ProductBriefOperationProjection,
    ProductBriefViewApplicationService,
    ProductBriefWorkflowProjection,
)
from commercevision_contracts.product_briefs import (
    ProductBriefProviderCallResponseV1,
    ProductBriefVersionListResponseV1,
    ProductBriefVersionSummaryResponseV1,
)
from commercevision_domain import (
    ProductBriefCategory,
    ProductBriefState,
    ProductBriefVersionSource,
    RetentionClass,
    WorkflowStatus,
    product_brief_field_paths,
)
from commercevision_domain.operations import OperationKind, OperationState
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

RETENTION_DEADLINE = datetime(2099, 1, 1, tzinfo=UTC)
BRIEF_ID = "019fab75-363d-773e-bd74-315a9ddd4324"


class _PrincipalResolver:
    def resolve(self, token: str | None) -> object:
        del token
        return object()


class _AccessPolicy:
    def require_workspace(self, *, workspace_id: str, principal: object) -> None:
        assert workspace_id == "workspace-route-test"
        assert principal is not None


class _ProductBriefViewQueries:
    def __init__(self, operation: object) -> None:
        self._operation = operation

    def get_analysis_workflow_context(
        self,
        *,
        workflow_id: str,
        workspace_id: str,
    ) -> ProductBriefAnalysisWorkflowProjection | None:
        assert workspace_id == "workspace-route-test"
        return ProductBriefAnalysisWorkflowProjection(
            id=workflow_id,
            status=WorkflowStatus.UNDERSTANDING,
            version=3,
            retention_deadline=RETENTION_DEADLINE,
        )

    def get_workflow_context(
        self,
        *,
        product_brief_id: str,
        workflow_id: str,
        workspace_id: str,
    ) -> ProductBriefWorkflowProjection | None:
        assert workspace_id == "workspace-route-test"
        if product_brief_id != BRIEF_ID:
            return None
        return ProductBriefWorkflowProjection(
            id=workflow_id,
            status=WorkflowStatus.AWAITING_PRODUCT_CONFIRMATION,
            version=7,
            retention_deadline=RETENTION_DEADLINE,
        )

    def get_operation_status(
        self,
        *,
        workspace_id: str,
        product_brief_id: str,
        operation_id: str,
    ) -> ProductBriefOperationProjection | None:
        assert workspace_id == "workspace-route-test"
        assert operation_id == self._operation.id
        if (
            self._operation.kind != OperationKind.PRODUCT_BRIEF_ANALYSIS
            or self._operation.target_type != "product_brief"
            or self._operation.target_id != product_brief_id
        ):
            return None
        return ProductBriefOperationProjection(
            id=self._operation.id,
            state=self._operation.state,
            attempt_count=self._operation.attempt_count,
            max_attempts=self._operation.max_attempts,
            error_code=self._operation.error.code,
            error_category=self._operation.error.category,
            error_retryable=self._operation.error.retryable,
            version=self._operation.version,
        )


class _ProductBriefCommands:
    def __init__(
        self,
        history: ProductBriefVersionListResponseV1 | None = None,
    ) -> None:
        self.list_versions_call: dict[str, object] | None = None
        self._history = history or ProductBriefVersionListResponseV1(
            items=(),
            next_cursor=17,
        )

    def list_versions(
        self,
        *,
        workspace_id: str,
        product_brief_id: str,
        limit: int,
        cursor: int | None,
    ) -> ProductBriefVersionListResponseV1:
        self.list_versions_call = {
            "workspace_id": workspace_id,
            "product_brief_id": product_brief_id,
            "limit": limit,
            "cursor": cursor,
        }
        return self._history


def _test_app(
    operation: object,
    *,
    product_briefs: _ProductBriefCommands | None = None,
) -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.state.container = SimpleNamespace(
        principal_resolver=_PrincipalResolver(),
        access_policy=_AccessPolicy(),
        product_briefs=product_briefs or _ProductBriefCommands(),
        product_brief_views=ProductBriefViewApplicationService(
            queries=_ProductBriefViewQueries(operation)
        ),
    )

    @app.middleware("http")
    async def correlation_context(request: Request, call_next):
        request.state.request_id = "request-route-test"
        request.state.trace_id = "trace-route-test"
        return await call_next(request)

    app.include_router(router)
    return app


def _operation(*, target_id: str = BRIEF_ID, kind: OperationKind | None = None) -> object:
    return SimpleNamespace(
        id="operation-1",
        workspace_id="workspace-route-test",
        kind=kind or OperationKind.PRODUCT_BRIEF_ANALYSIS,
        target_type="product_brief",
        target_id=target_id,
        state=OperationState.RETRYABLE_FAILED,
        attempt_count=2,
        max_attempts=4,
        error=SimpleNamespace(
            code="VISION_PROVIDER_TIMEOUT",
            category="provider",
            message="secret upstream endpoint and provider request identifier",
            retryable=True,
            provider_request_id="provider-request-secret",
        ),
        version=9,
        lease_owner="worker-secret",
        input_ref="provider-input-secret",
        output_ref="provider-output-secret",
        provider_request_id="provider-request-secret",
    )


def test_product_brief_browser_views_expose_only_safe_fields() -> None:
    with TestClient(_test_app(_operation())) as client:
        analysis_workflow = client.get(
            "/api/v1/product-briefs/analysis-workflow-context/workflow-1",
            headers={"X-Workspace-Id": "workspace-route-test"},
        )
        workflow = client.get(
            "/api/v1/product-briefs/workflow-context/workflow-1"
            f"?product_brief_id={BRIEF_ID.upper()}",
            headers={"X-Workspace-Id": "workspace-route-test"},
        )
        operation = client.get(
            f"/api/v1/product-briefs/{BRIEF_ID.upper()}/operations/operation-1",
            headers={"X-Workspace-Id": "workspace-route-test"},
        )

    assert analysis_workflow.status_code == 200
    assert analysis_workflow.json() == {
        "id": "workflow-1",
        "status": "UNDERSTANDING",
        "version": 3,
        "retention_deadline": "2099-01-01T00:00:00Z",
    }
    assert workflow.status_code == 200
    assert workflow.json() == {
        "id": "workflow-1",
        "status": "AWAITING_PRODUCT_CONFIRMATION",
        "version": 7,
        "retention_deadline": "2099-01-01T00:00:00Z",
    }
    assert operation.status_code == 200
    assert operation.json() == {
        "id": "operation-1",
        "state": "RETRYABLE_FAILED",
        "attempt_count": 2,
        "max_attempts": 4,
        "error": {
            "code": "VISION_PROVIDER_TIMEOUT",
            "category": "provider",
            "message": "Product analysis is temporarily unavailable and may be retried.",
            "retryable": True,
        },
        "version": 9,
    }
    serialized = f"{analysis_workflow.text}\n{workflow.text}\n{operation.text}"
    for forbidden in (
        "secret",
        "input_data",
        "result_data",
        "steps",
        "lease_owner",
        "input_ref",
        "output_ref",
        "provider_request_id",
    ):
        assert forbidden not in serialized


def test_bound_product_brief_workflow_view_requires_product_brief_identity() -> None:
    with TestClient(_test_app(_operation())) as client:
        response = client.get(
            "/api/v1/product-briefs/workflow-context/workflow-1",
            headers={"X-Workspace-Id": "workspace-route-test"},
        )

    assert response.status_code == 422


def test_product_brief_versions_route_passes_bounded_keyset_page_arguments() -> None:
    product_briefs = _ProductBriefCommands()
    with TestClient(_test_app(_operation(), product_briefs=product_briefs)) as client:
        response = client.get(
            "/api/v1/product-briefs/brief-1/versions?limit=7&cursor=23",
            headers={"X-Workspace-Id": "workspace-route-test"},
        )

    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": 17}
    assert product_briefs.list_versions_call == {
        "workspace_id": "workspace-route-test",
        "product_brief_id": "brief-1",
        "limit": 7,
        "cursor": 23,
    }


def test_product_brief_versions_route_serializes_three_bounded_summaries() -> None:
    paths = product_brief_field_paths(ProductBriefCategory.BEAUTY)
    history = ProductBriefVersionListResponseV1(
        items=tuple(
            ProductBriefVersionSummaryResponseV1(
                id=f"summary-version-{version_number}",
                product_brief_id=BRIEF_ID,
                version_number=version_number,
                supersedes_version_id=(
                    None if version_number == 1 else f"summary-version-{version_number - 1}"
                ),
                effective_state=(
                    ProductBriefState.AWAITING_CONFIRMATION
                    if version_number == 3
                    else ProductBriefState.ARCHIVED
                ),
                category=ProductBriefCategory.BEAUTY,
                common_schema_version="product-brief-common-v1",
                category_schema_version="product-brief-beauty-v1",
                payload_sha256=f"{version_number}" * 64,
                changed_field_paths=paths,
                confirmation_required=True,
                unresolved_field_count=1,
                review_policy_version="review-v1",
                source=ProductBriefVersionSource.MODEL,
                prompt_version="prompt-v1",
                provider_call=ProductBriefProviderCallResponseV1(
                    provider="deterministic-vision",
                    requested_model="vision-v1",
                    resolved_model="vision-v1",
                    latency_ms=125,
                ),
                actor_id="vision-provider",
                revision_reason=None,
                retention_class=RetentionClass.TASK,
                retention_deadline=RETENTION_DEADLINE,
                created_at=datetime(2098, 12, version_number, tzinfo=UTC),
            )
            for version_number in range(3, 0, -1)
        ),
        next_cursor=None,
    )
    product_briefs = _ProductBriefCommands(history)
    with TestClient(_test_app(_operation(), product_briefs=product_briefs)) as client:
        response = client.get(
            f"/api/v1/product-briefs/{BRIEF_ID}/versions?limit=3",
            headers={"X-Workspace-Id": "workspace-route-test"},
        )

    assert response.status_code == 200
    assert len(response.content) < 2 * 1024 * 1024
    assert [item["version_number"] for item in response.json()["items"]] == [3, 2, 1]
    assert all("fields" not in item for item in response.json()["items"])


def test_product_brief_operation_view_hides_non_product_brief_operations() -> None:
    app = _test_app(_operation(kind=OperationKind.ASSET_VALIDATION))
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            f"/api/v1/product-briefs/{BRIEF_ID}/operations/operation-1",
            headers={"X-Workspace-Id": "workspace-route-test"},
        )

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_product_brief_operation_view_requires_exact_target_binding() -> None:
    app = _test_app(_operation(target_id="119fab75-363d-773e-bd74-315a9ddd4324"))
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            f"/api/v1/product-briefs/{BRIEF_ID}/operations/operation-1",
            headers={"X-Workspace-Id": "workspace-route-test"},
        )

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"
