"""Deployment contracts for the production Worker queue boundary."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

_REPOSITORY_ROOT = Path(__file__).parents[2]
_OBJECT_STORAGE_RUNTIME_KEYS = (
    "CV_OBJECT_STORE_BACKEND",
    "CV_OBJECT_STORE_ENDPOINT",
    "CV_OBJECT_STORE_PRESIGN_ENDPOINT",
    "CV_OBJECT_STORE_ACCESS_KEY",
    "CV_OBJECT_STORE_SECRET_KEY",
    "CV_OBJECT_STORE_REGION",
    "CV_OBJECT_STORE_FORCE_PATH_STYLE",
    "CV_OBJECT_STORE_TLS_VERIFY",
    "CV_OBJECT_STORE_REQUIRE_ENCRYPTION",
    "CV_OBJECT_STORE_CONNECT_TIMEOUT_SECONDS",
    "CV_OBJECT_STORE_READ_TIMEOUT_SECONDS",
    "CV_OBJECT_STORE_READINESS_TIMEOUT_SECONDS",
    "CV_OBJECT_STORE_QUARANTINE_BUCKET",
    "CV_OBJECT_STORE_TASK_BUCKET",
    "CV_OBJECT_STORE_FOUNDATION_BUCKET",
    "CV_OBJECT_STORE_PROVIDER_RESULT_BUCKET",
)


def _compose_services() -> dict[str, object]:
    compose = yaml.safe_load(
        (_REPOSITORY_ROOT / "infra/compose/docker-compose.yml").read_text(encoding="utf-8")
    )
    return compose["services"]


def test_compose_worker_consumes_maintenance_without_claiming_future_asset_work() -> None:
    configured = json.loads(_compose_services()["worker"]["environment"]["CV_WORKER_QUEUES"])

    assert configured == [
        "commercevision.workflow",
        "commercevision.maintenance",
    ]


def test_compose_worker_uses_the_control_api_object_storage_identity() -> None:
    services = _compose_services()
    api_environment = services["api"]["environment"]
    worker_environment = services["worker"]["environment"]

    assert {key: worker_environment.get(key) for key in _OBJECT_STORAGE_RUNTIME_KEYS} == {
        key: api_environment.get(key) for key in _OBJECT_STORAGE_RUNTIME_KEYS
    }
    assert all(worker_environment.get(key) for key in _OBJECT_STORAGE_RUNTIME_KEYS)
    assert services["worker"]["depends_on"]["object-storage-init"] == {
        "condition": "service_completed_successfully"
    }


def test_compose_worker_fixes_prefork_and_validates_master_readiness_payload() -> None:
    worker = _compose_services()["worker"]
    assert "--pool=prefork" in worker["command"]

    healthcheck = " ".join(worker["healthcheck"]["test"])
    assert "json.loads" in healthcheck
    assert "consumer_ready" in healthcheck
    assert "master_pid" in healthcheck
    assert "object_storage" in healthcheck
    assert "missing_kinds" in healthcheck
    assert "CV_WORKER_CONCURRENCY" in healthcheck
    assert ".children" in healthcheck
