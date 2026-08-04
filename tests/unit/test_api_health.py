import asyncio
import secrets

import commercevision_api.main as api_main
import commercevision_api.readiness as readiness
import pytest
from commercevision_api.main import create_app
from commercevision_contracts import Settings
from fastapi.testclient import TestClient


def _production_api_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "service_name": "control-api",
        "environment": "production",
        "worker_queues": ["commercevision.workflow"],
        "object_store_endpoint": "https://storage.internal.example",
        "object_store_presign_endpoint": "https://assets.example",
        "object_store_secret_key": "production-object-store-secret",
        "object_store_require_encryption": True,
        "readiness_probe_external": False,
    }
    values.update(overrides)
    return Settings(**values)


def test_production_api_rejects_missing_trusted_principal_key_before_startup() -> None:
    with pytest.raises(
        RuntimeError,
        match="production Control API requires a current trusted-principal key",
    ):
        create_app(_production_api_settings())


def test_production_api_rejects_whitespace_only_trusted_principal_secret() -> None:
    with pytest.raises(
        RuntimeError,
        match="production Control API trusted-principal key is not production-safe",
    ):
        create_app(
            _production_api_settings(
                trusted_principal_current_key_id="gateway-current",
                trusted_principal_current_hmac_secret=" " * 32,
            )
        )


def test_production_api_rejects_public_local_trust_material_without_disclosure() -> None:
    public_local_secret = "local-web-gateway-secret-change-before-production"

    with pytest.raises(
        RuntimeError,
        match="production Control API trusted-principal key is not production-safe",
    ) as error:
        create_app(
            _production_api_settings(
                trusted_principal_current_key_id="local-web-gateway",
                trusted_principal_current_hmac_secret=public_local_secret,
            )
        )

    assert public_local_secret not in str(error.value)


def test_production_api_rejects_public_local_secret_under_a_different_key_id() -> None:
    with pytest.raises(
        RuntimeError,
        match="production Control API trusted-principal key is not production-safe",
    ):
        create_app(
            _production_api_settings(
                trusted_principal_current_key_id="renamed-production-gateway",
                trusted_principal_current_hmac_secret=(
                    "local-web-gateway-secret-change-before-production"
                ),
            )
        )


@pytest.mark.parametrize(
    "previous_key_id,previous_secret",
    [
        (
            "gateway-previous",
            "local-web-gateway-secret-change-before-production",
        ),
        ("gateway-previous", " " * 32),
    ],
)
def test_production_api_rejects_unsafe_previous_trusted_principal_key_without_disclosure(
    previous_key_id: str,
    previous_secret: str,
) -> None:
    current_secret = secrets.token_urlsafe(32)

    with pytest.raises(
        RuntimeError,
        match="production Control API trusted-principal key is not production-safe",
    ) as error:
        create_app(
            _production_api_settings(
                trusted_principal_current_key_id="gateway-current",
                trusted_principal_current_hmac_secret=current_secret,
                trusted_principal_previous_key_id=previous_key_id,
                trusted_principal_previous_hmac_secret=previous_secret,
            )
        )

    assert current_secret not in str(error.value)
    assert previous_secret not in str(error.value)


def test_production_api_starts_with_complete_trusted_principal_current_key() -> None:
    production_secret = secrets.token_urlsafe(32)
    app = create_app(
        _production_api_settings(
            trusted_principal_current_key_id="gateway-current",
            trusted_principal_current_hmac_secret=production_secret,
        )
    )

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["checks"]["configuration"] == "ok"


def test_production_api_starts_with_two_complete_trusted_principal_keys() -> None:
    app = create_app(
        _production_api_settings(
            trusted_principal_current_key_id="gateway-current",
            trusted_principal_current_hmac_secret=secrets.token_urlsafe(32),
            trusted_principal_previous_key_id="gateway-previous",
            trusted_principal_previous_hmac_secret=secrets.token_urlsafe(32),
        )
    )

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["checks"]["configuration"] == "ok"


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
            "object_store": "failed",
        }

    monkeypatch.setattr(api_main, "probe_dependencies", failed_dependencies)
    app = create_app(Settings(environment="ci", readiness_probe_external=True))
    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["checks"]["object_store"] == "failed"


def test_dependency_probe_uses_authenticated_object_storage_readiness(monkeypatch) -> None:
    storage_probe_calls = 0

    async def healthy_dependency(_settings: Settings) -> None:
        return None

    def object_storage_probe() -> None:
        nonlocal storage_probe_calls
        storage_probe_calls += 1

    monkeypatch.setattr(readiness, "_probe_mysql", healthy_dependency)

    checks = asyncio.run(
        readiness.probe_dependencies(
            Settings(
                environment="ci",
                object_store_backend="oss",
                object_store_endpoint="https://oss-cn-hangzhou.aliyuncs.com",
            ),
            object_storage_probe=object_storage_probe,
        )
    )

    assert checks == {
        "mysql": "ok",
        "object_store": "ok",
    }
    assert storage_probe_calls == 1
