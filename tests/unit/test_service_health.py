from commercevision_mcp.main import server
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


def test_mcp_liveness_contract() -> None:
    with TestClient(server.streamable_http_app()) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "mcp-server",
        "version": "0.1.0",
    }
