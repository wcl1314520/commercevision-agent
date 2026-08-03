import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime

from commercevision_contracts import McpToolBudgetV1, McpToolIdentityV1, Settings
from commercevision_domain import NotFoundError
from commercevision_mcp.main import create_server
from fastapi.testclient import TestClient
from pydantic import SecretStr

TOOL_NAMES = {
    "catalog.get_product.v1",
    "catalog.get_product_brief.v1",
    "brand.get_profile.v1",
    "assets.search.v1",
    "assets.get_temporary_reference.v1",
}


class _UnavailablePorts:
    retrieval_policy_version = "retrieval-policy-v1"

    def __init__(self) -> None:
        self.catalog = self
        self.product_briefs = self
        self.brand_profiles = self
        self.retrieval = self
        self.retrieval_runs = self
        self.retrieval_previews = self

    def get_product(self, **kwargs):
        raise NotFoundError("resource was not found")

    def get(self, **kwargs):
        raise NotFoundError("resource was not found")

    def get_version(self, **kwargs):
        raise NotFoundError("resource was not found")

    def execute(self, query):
        raise NotFoundError("resource was not found")

    def record(self, query, response):
        raise NotFoundError("resource was not found")

    def exchange(self, **kwargs):
        return None

    def close(self) -> None:
        pass

    def assert_database_ready(self) -> None:
        pass

    def assert_object_storage_ready(self) -> None:
        pass


def _token(
    secret: str,
    *,
    scopes: tuple[str, ...] = ("catalog.read", "brand.read", "assets.search", "assets.read"),
    max_result_count: int = 10,
) -> str:
    identity = McpToolIdentityV1(
        workspace_id="workspace-mcp",
        actor_id="agent-1",
        workflow_id="workflow-1",
        invocation_id="invocation-0001",
        scopes=scopes,
        purpose="CREATIVE_REFERENCE",
        provider="fixture-provider",
        requires_derivative=False,
        budget=McpToolBudgetV1(
            max_result_count=max_result_count,
            max_candidate_count=100,
            max_output_bytes=262144,
        ),
        issued_at=int(datetime.now(UTC).timestamp()),
    )
    encoded = (
        base64.urlsafe_b64encode(
            json.dumps(identity.model_dump(mode="json"), separators=(",", ":")).encode()
        )
        .decode()
        .rstrip("=")
    )
    signature = hmac.new(
        secret.encode(), f"test-key.{encoded}".encode(), hashlib.sha256
    ).hexdigest()
    return f"test-key.{encoded}.{signature}"


def test_mcp_transport_enumerates_closed_schemas_and_executes_every_tool() -> None:
    secret = "mcp-contract-secret-at-least-thirty-two-bytes"
    settings = Settings(
        service_name="mcp-server",
        trusted_principal_current_key_id="test-key",
        trusted_principal_current_hmac_secret=SecretStr(secret),
    )
    _, holder = create_server(settings, container_factory=lambda _: _UnavailablePorts())
    app = holder["http_app"]
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "X-Trusted-Principal": _token(secret),
    }
    with TestClient(app) as client:
        initialized = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "contract", "version": "1"},
                },
            },
        )
        assert initialized.status_code == 200
        listed = client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        assert listed.status_code == 200
        tools = {item["name"]: item for item in listed.json()["result"]["tools"]}
        assert set(tools) == TOOL_NAMES
        assert all(item["inputSchema"]["additionalProperties"] is False for item in tools.values())
        assert all(item["outputSchema"]["additionalProperties"] is False for item in tools.values())
        assert tools["assets.search.v1"]["inputSchema"]["properties"]["top_k"]["maximum"] == 50
        calls = {
            "catalog.get_product.v1": {"product_id": "019f8a00-0000-7000-8000-000000000001"},
            "catalog.get_product_brief.v1": {
                "product_brief_id": "019f8a00-0000-7000-8000-000000000002"
            },
            "brand.get_profile.v1": {"profile_id": "019f8a00-0000-7000-8000-000000000003"},
            "assets.search.v1": {
                "product_id": "019f8a00-0000-7000-8000-000000000001",
                "vector_kinds": ["PRODUCT_FUSED"],
                "query_text": "product",
            },
            "assets.get_temporary_reference.v1": {
                "retrieval_run_id": "019f8a00-0000-7000-8000-000000000004",
                "rank": 1,
                "preview_reference_token": "a" * 32,
            },
        }
        for index, (name, arguments) in enumerate(calls.items(), start=10):
            response = client.post(
                "/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": index,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                },
            )
            assert response.status_code == 200
            assert response.json()["result"]["isError"] is True
            assert "NOT_FOUND" in response.json()["result"]["content"][0]["text"]

        unauthorized = client.post(
            "/mcp",
            headers={key: value for key, value in headers.items() if key != "X-Trusted-Principal"},
            json={
                "jsonrpc": "2.0",
                "id": 20,
                "method": "tools/call",
                "params": {
                    "name": "catalog.get_product.v1",
                    "arguments": calls["catalog.get_product.v1"],
                },
            },
        )
        assert "AUTHENTICATION_REQUIRED" in unauthorized.json()["result"]["content"][0]["text"]

        denied_headers = headers | {"X-Trusted-Principal": _token(secret, scopes=("assets.read",))}
        denied = client.post(
            "/mcp",
            headers=denied_headers,
            json={
                "jsonrpc": "2.0",
                "id": 21,
                "method": "tools/call",
                "params": {
                    "name": "catalog.get_product.v1",
                    "arguments": calls["catalog.get_product.v1"],
                },
            },
        )
        assert "TOOL_POLICY_DENIED" in denied.json()["result"]["content"][0]["text"]

        budget_headers = headers | {"X-Trusted-Principal": _token(secret, max_result_count=2)}
        over_budget = client.post(
            "/mcp",
            headers=budget_headers,
            json={
                "jsonrpc": "2.0",
                "id": 22,
                "method": "tools/call",
                "params": {
                    "name": "assets.search.v1",
                    "arguments": calls["assets.search.v1"] | {"top_k": 3},
                },
            },
        )
        assert "TOOL_EXECUTION_REJECTED" in over_budget.json()["result"]["content"][0]["text"]

        cross_workspace = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 23,
                "method": "tools/call",
                "params": {
                    "name": "catalog.get_product.v1",
                    "arguments": calls["catalog.get_product.v1"] | {"workspace_id": "other"},
                },
            },
        )
        assert cross_workspace.json()["result"]["isError"] is True


class _DegradedPorts(_UnavailablePorts):
    def assert_database_ready(self) -> None:
        pass

    def assert_object_storage_ready(self) -> None:
        raise RuntimeError("secret-storage-credential")


def test_mcp_readiness_reports_dependencies_independently_without_error_details() -> None:
    settings = Settings(service_name="mcp-server")
    _, holder = create_server(settings, container_factory=lambda _: _DegradedPorts())

    with TestClient(holder["http_app"]) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "service": "mcp-server",
        "version": "0.1.0",
        "checks": {"mysql": "ok", "object_storage": "failed"},
    }
    assert "credential" not in response.text
