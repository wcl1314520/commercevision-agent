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


def _clamav_test_override_services() -> dict[str, object]:
    compose = yaml.safe_load(
        (_REPOSITORY_ROOT / "infra/compose/docker-compose.clamav-test.yml").read_text(
            encoding="utf-8"
        )
    )
    return compose["services"]


def _ci_workflow() -> dict[str, object]:
    return yaml.safe_load(
        (_REPOSITORY_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    )


def test_compose_migration_uses_a_dedicated_ddl_identity() -> None:
    services = _compose_services()
    migration = services["migrate"]
    migration_dsn = migration["environment"]["CV_MIGRATION_MYSQL_DSN"]
    runtime_dsns = {
        service_name: services[service_name]["environment"]["CV_MYSQL_DSN"]
        for service_name in ("api", "worker", "scheduler")
    }

    assert migration_dsn.startswith("${CV_MIGRATION_MYSQL_DSN:-mysql+pymysql://root:")
    assert all("://commercevision:" in dsn for dsn in runtime_dsns.values())
    assert migration_dsn not in runtime_dsns.values()
    assert migration["depends_on"]["mysql-permissions"] == {
        "condition": "service_completed_successfully"
    }


def test_compose_reconciles_and_verifies_runtime_database_privileges() -> None:
    permissions = _compose_services()["mysql-permissions"]

    assert permissions["command"] == [
        "/bin/sh",
        "/opt/commercevision/reconcile-runtime-grants.sh",
    ]
    assert permissions["restart"] == "no"
    assert permissions["depends_on"]["mysql"] == {"condition": "service_healthy"}
    assert permissions["volumes"] == [
        "../mysql/reconcile-runtime-grants.sql:/opt/commercevision/reconcile-runtime-grants.sql:ro",
        "../mysql/reconcile-runtime-grants.sh:/opt/commercevision/reconcile-runtime-grants.sh:ro",
    ]


def test_ci_runs_alembic_with_the_migration_identity_only() -> None:
    python_job = _ci_workflow()["jobs"]["python"]
    environment = python_job["env"]
    steps = {step.get("name"): step for step in python_job["steps"]}

    assert environment["CV_MYSQL_DSN"].startswith("mysql+pymysql://commercevision:commercevision@")
    assert "CV_MIGRATION_MYSQL_DSN" not in environment
    assert steps["Reconcile runtime database grants"]["run"].endswith(
        "< infra/mysql/reconcile-runtime-grants.sql"
    )
    for step_name in ("Upgrade schema", "Check schema drift"):
        assert "CV_MYSQL_DSN" not in steps[step_name].get("env", {})
        assert steps[step_name]["env"]["CV_MIGRATION_MYSQL_DSN"].startswith(
            "mysql+pymysql://root:root-change-me@"
        )
    assert steps["Verify runtime database grants"]["run"] == (
        "uv run pytest tests/integration/test_mysql_runtime_privileges.py -q"
    )


def test_mysql_readiness_requires_authenticated_tcp_query() -> None:
    services = _compose_services()
    compose_healthcheck = " ".join(services["mysql"]["healthcheck"]["test"])
    ci_healthcheck = _ci_workflow()["jobs"]["python"]["services"]["mysql"]["options"]

    for healthcheck in (compose_healthcheck, ci_healthcheck):
        assert "mysqladmin ping" not in healthcheck
        assert "mysql --protocol=TCP" in healthcheck
        assert "--host=127.0.0.1" in healthcheck
        assert "--user=root" in healthcheck
        assert "SELECT 1" in healthcheck


def test_compose_worker_consumes_asset_validation_and_maintenance_work() -> None:
    worker_environment = _compose_services()["worker"]["environment"]
    configured = json.loads(worker_environment["CV_WORKER_QUEUES"])
    required = json.loads(worker_environment["CV_WORKER_REQUIRED_OPERATION_KINDS"])

    assert configured == [
        "commercevision.workflow",
        "commercevision.asset",
        "commercevision.maintenance",
    ]
    assert required == ["ASSET_VALIDATION", "ASSET_DELETION"]


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
    assert "malware_scanner" in healthcheck
    assert "missing_kinds" in healthcheck
    assert "CV_WORKER_CONCURRENCY" in healthcheck
    assert ".children" in healthcheck


def test_compose_asset_worker_depends_on_pinned_clamav_and_explicit_adapters() -> None:
    services = _compose_services()
    clamav = services["clamav"]
    worker = services["worker"]
    environment = worker["environment"]

    assert clamav["image"] == (
        "clamav/clamav:1.5.3_base"
        "@sha256:b2be682d7514281f20117fb8fe15a7f8da9e4f6ea0b4b819f6c74c84ce84d1d7"
    )
    assert "ports" not in clamav
    assert clamav["volumes"] == ["clamav_data:/var/lib/clamav"]
    assert worker["depends_on"]["clamav"] == {"condition": "service_healthy"}
    assert environment["CV_ASSET_MALWARE_ADAPTER"] == "clamav"
    assert environment["CV_CLAMAV_HOST"] == "clamav"
    assert environment["CV_CLAMAV_PORT"] == "3310"
    assert environment["CV_ASSET_CONTENT_SAFETY_ADAPTER"] == "deterministic"
    assert environment["CV_ASSET_PROVENANCE_ADAPTER"] == "deterministic"


def test_clamav_real_test_override_is_loopback_only_and_not_configurable() -> None:
    clamav = _clamav_test_override_services()["clamav"]

    assert clamav["ports"] == ["127.0.0.1:13310:3310"]
