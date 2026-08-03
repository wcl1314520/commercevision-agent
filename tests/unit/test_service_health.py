from commercevision_contracts import Settings
from commercevision_mcp.main import create_server
from commercevision_scheduler.main import create_app
from fastapi.testclient import TestClient


def test_scheduler_liveness_contract() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "scheduler",
        "version": "0.1.0",
    }


def test_scheduler_readiness_exposes_independent_scanner_status() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert set(response.json()["scanners"]) == {
        "outbox_dispatch",
        "workflow_recovery",
        "operation_recovery",
        "upload_session_expiry",
        "rights_activation",
        "rights_expiry",
        "asset_retention_expiry",
    }
    assert response.json()["expired_uploads_total"] == 0
    assert response.json()["expired_rights_total"] == 0
    assert response.json()["activated_rights_total"] == 0
    assert set(response.json()["scanners"]["operation_recovery"]) == {
        "last_started_at",
        "last_success_at",
        "last_error",
        "last_duration_ms",
        "last_count",
        "total_count",
        "in_progress",
        "timed_out",
        "timeout_count",
    }


def test_mcp_liveness_contract() -> None:
    class Container:
        def close(self) -> None:
            pass

    _, holder = create_server(
        Settings(service_name="mcp-server"), container_factory=lambda _: Container()
    )
    with TestClient(holder["http_app"]) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "mcp-server",
        "version": "0.1.0",
    }
