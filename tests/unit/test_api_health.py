import commercevision_api.main as api_main
from commercevision_api.main import create_app
from commercevision_contracts import Settings
from fastapi.testclient import TestClient


def test_liveness_contract() -> None:
    app = create_app(Settings(environment="ci", readiness_probe_external=False))
    with TestClient(app) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "commercevision",
        "version": "0.1.0",
        "checks": {"process": "ok"},
    }


def test_readiness_skips_external_dependencies_by_default() -> None:
    app = create_app(Settings(environment="ci", readiness_probe_external=False))
    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["checks"] == {
        "configuration": "ok",
        "external_dependencies": "skipped",
    }


def test_metadata_is_versioned() -> None:
    app = create_app(Settings(environment="ci"))
    with TestClient(app) as client:
        response = client.get("/api/v1/meta")

    assert response.status_code == 200
    assert response.json()["phase"] == "phase-1"


def test_readiness_reports_dependency_failure(monkeypatch) -> None:
    async def failed_dependencies(_settings: Settings) -> dict[str, str]:
        return {
            "mysql": "ok",
            "redis": "ok",
            "rabbitmq": "ok",
            "object_store": "failed",
            "milvus": "ok",
        }

    monkeypatch.setattr(api_main, "probe_dependencies", failed_dependencies)
    app = create_app(Settings(environment="ci", readiness_probe_external=True))
    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["checks"]["object_store"] == "failed"
