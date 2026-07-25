import asyncio

import commercevision_api.main as api_main
import commercevision_api.readiness as readiness
import httpx
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
    async def failed_dependencies(
        _settings: Settings,
        *,
        object_storage_probe,
    ) -> dict[str, str]:
        del object_storage_probe
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


def test_dependency_probe_uses_authenticated_object_storage_readiness(monkeypatch) -> None:
    requested_urls: list[str] = []
    storage_probe_calls = 0

    async def healthy_dependency(_settings: Settings) -> None:
        return None

    def object_storage_probe() -> None:
        nonlocal storage_probe_calls
        storage_probe_calls += 1

    class FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            assert timeout == 3

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, url: str) -> httpx.Response:
            requested_urls.append(url)
            return httpx.Response(200, request=httpx.Request("GET", url))

    monkeypatch.setattr(readiness, "_probe_mysql", healthy_dependency)
    monkeypatch.setattr(readiness, "_probe_redis", healthy_dependency)
    monkeypatch.setattr(readiness, "_probe_rabbitmq", healthy_dependency)
    monkeypatch.setattr(readiness.httpx, "AsyncClient", FakeAsyncClient)

    checks = asyncio.run(
        readiness.probe_dependencies(
            Settings(
                environment="ci",
                object_store_backend="oss",
                object_store_endpoint="https://oss-cn-hangzhou.aliyuncs.com",
                milvus_health_uri="https://milvus.internal.example/healthz",
            ),
            object_storage_probe=object_storage_probe,
        )
    )

    assert checks == {
        "mysql": "ok",
        "redis": "ok",
        "rabbitmq": "ok",
        "object_store": "ok",
        "milvus": "ok",
    }
    assert storage_probe_calls == 1
    assert requested_urls == ["https://milvus.internal.example/healthz"]
