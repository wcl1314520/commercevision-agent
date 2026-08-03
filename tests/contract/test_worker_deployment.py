"""Deployment contracts for the production Worker queue boundary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from commercevision_contracts import Settings
from commercevision_domain import OperationKind, StorageBackend, StorageLocationClass
from commercevision_object_storage import ObjectStorageReadinessError
from commercevision_providers import AlibabaVisionAnalyzer
from commercevision_worker import runtime as worker_runtime_module
from commercevision_worker.runtime import WorkerRuntime

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
_PRODUCT_BRIEF_POLICY_KEYS = (
    "CV_VISION_ADAPTER",
    "CV_VISION_PROMPT_VERSION",
    "CV_VISION_PRODUCT_FACTS_MAXIMUM_BYTES",
    "CV_VISION_PRODUCT_FACTS_MAXIMUM_DEPTH",
    "CV_VISION_PRODUCT_FACTS_MAXIMUM_NODES",
    "CV_VISION_PRODUCT_FACTS_MAXIMUM_STRING_BYTES",
    "CV_PRODUCT_BRIEF_REVIEW_POLICY_VERSION",
    "CV_PRODUCT_BRIEF_CONFIDENCE_THRESHOLD",
    "CV_PRODUCT_BRIEF_MANDATORY_REVIEW_PATHS",
    "CV_PRODUCT_BRIEF_SENSITIVE_CLAIM_PATHS",
    "CV_VISION_DATA_TRANSFER_ENABLED",
    "CV_VISION_DATA_TRANSFER_POLICY_VERSION",
    "CV_VISION_DATA_TRANSFER_ALLOWED_WORKSPACE_IDS",
    "CV_VISION_DATA_TRANSFER_ALLOWED_RETENTION_CLASSES",
    "CV_VISION_DATA_TRANSFER_ALLOWED_PROVIDERS",
    "CV_VISION_DATA_TRANSFER_ALLOWED_ENDPOINT_REGIONS",
    "CV_VISION_DATA_TRANSFER_ALLOWED_ENDPOINT_HOSTS",
    "CV_ALIBABA_VISION_ENDPOINT",
    "CV_ALIBABA_VISION_ENDPOINT_REGION",
    "CV_ALIBABA_VISION_MODEL",
    "CV_ALIBABA_VISION_MODEL_SNAPSHOT",
    "CV_ALIBABA_VISION_ADAPTER_VERSION",
    "CV_ALIBABA_VISION_CONNECT_TIMEOUT_SECONDS",
    "CV_ALIBABA_VISION_READ_TIMEOUT_SECONDS",
    "CV_ALIBABA_VISION_END_TO_END_TIMEOUT_SECONDS",
    "CV_ALIBABA_VISION_MAXIMUM_CONCURRENCY",
    "CV_ALIBABA_VISION_MAXIMUM_RESPONSE_BYTES",
    "CV_ALIBABA_VISION_MAXIMUM_OUTPUT_TOKENS",
    "CV_ALIBABA_VISION_MAXIMUM_REPAIR_ATTEMPTS",
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


class _ReadyOperationExecutors:
    registered_kinds = frozenset({OperationKind.PRODUCT_BRIEF_ANALYSIS})

    @staticmethod
    def missing(_required: frozenset[OperationKind]) -> frozenset[OperationKind]:
        return frozenset()


def _runtime_for_readiness(
    storage: object,
    *,
    settings: Settings | None = None,
) -> WorkerRuntime:
    return WorkerRuntime(
        database=object(),
        settings=(
            settings
            or Settings(
                environment="ci",
                worker_queues=["commercevision.asset"],
                worker_required_operation_kinds=[OperationKind.PRODUCT_BRIEF_ANALYSIS],
            )
        ),
        worker_id="readiness-test",
        inbox=object(),
        agent=object(),
        event_router=object(),
        operation_worker=object(),
        operation_executors=_ReadyOperationExecutors(),
        object_storage=storage,
        resources=(),
    )


def test_product_brief_worker_readiness_probes_provider_result_storage() -> None:
    class ReadyStorage:
        def __init__(self) -> None:
            self.calls: list[tuple[StorageLocationClass, ...]] = []

        def assert_ready(self, locations) -> None:
            self.calls.append(tuple(locations))

    storage = ReadyStorage()

    readiness = _runtime_for_readiness(storage).operation_executor_readiness()

    assert readiness["ready"] is True
    assert readiness["vision_credential"] == "not_required"
    assert readiness["provider_result_storage"] == "ok"
    assert storage.calls == [(StorageLocationClass.PROVIDER_RESULT,)]


def test_product_brief_worker_readiness_rejects_invalid_provider_result_bucket() -> None:
    class InvalidStorage:
        @staticmethod
        def assert_ready(locations) -> None:
            assert tuple(locations) == (StorageLocationClass.PROVIDER_RESULT,)
            raise ObjectStorageReadinessError("provider-result bucket versioning is not enabled")

    with pytest.raises(ObjectStorageReadinessError, match="provider-result.*versioning"):
        _runtime_for_readiness(InvalidStorage()).operation_executor_readiness()


def test_product_brief_worker_readiness_rereads_mounted_vision_credential(
    tmp_path: Path,
) -> None:
    class ReadyStorage:
        @staticmethod
        def assert_ready(_locations) -> None:
            pass

    credential_path = (tmp_path / "model-studio-api-key").resolve()
    credential_path.write_text("valid-mounted-key\n", encoding="utf-8")
    settings = Settings(
        environment="ci",
        worker_queues=["commercevision.asset"],
        worker_required_operation_kinds=[OperationKind.PRODUCT_BRIEF_ANALYSIS],
        vision_adapter="alibaba",
        alibaba_vision_api_key_file=str(credential_path),
        alibaba_vision_allowed_image_origins=["https://assets.example.com"],
        vision_data_transfer_enabled=True,
        vision_data_transfer_allowed_workspace_ids=["Catalog-A"],
        vision_data_transfer_allowed_retention_classes=["TASK"],
        vision_data_transfer_allowed_providers=["alibaba-model-studio"],
        vision_data_transfer_allowed_endpoint_regions=["cn-beijing"],
        vision_data_transfer_allowed_endpoint_hosts=["dashscope.aliyuncs.com"],
    )
    runtime = _runtime_for_readiness(ReadyStorage(), settings=settings)

    assert runtime.operation_executor_readiness()["vision_credential"] == "ok"

    credential_path.write_text("invalid\nsecond-line", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Vision API key is unavailable"):
        runtime.operation_executor_readiness()


def test_worker_runtime_injects_versioned_vision_budgets(monkeypatch) -> None:
    class RuntimeStorage:
        backend = StorageBackend.MINIO

        @staticmethod
        def configured_bucket(_location: StorageLocationClass) -> str:
            return "provider-results"

        @staticmethod
        def close() -> None:
            pass

    class ReadyArtifactTargetQuery:
        def __init__(self, _session_factory: object) -> None:
            pass

        def list_reconciliation_targets(self, *, limit: int) -> tuple[object, ...]:
            assert limit == 1
            return ()

    monkeypatch.setattr(
        worker_runtime_module,
        "build_object_storage",
        lambda _settings: RuntimeStorage(),
    )
    monkeypatch.setattr(
        worker_runtime_module.product_brief,
        "SqlAlchemyProviderArtifactTargetReadinessQuery",
        ReadyArtifactTargetQuery,
    )
    boundary = {
        "environment": "ci",
        "worker_queues": ["commercevision.asset"],
        "worker_required_operation_kinds": [OperationKind.PRODUCT_BRIEF_ANALYSIS],
        "vision_adapter": "alibaba",
        "alibaba_vision_api_key": "vision-secret",
        "alibaba_vision_allowed_image_origins": ["https://assets.example.com"],
        "vision_data_transfer_enabled": True,
        "vision_data_transfer_allowed_workspace_ids": ["Catalog-A"],
        "vision_data_transfer_allowed_retention_classes": ["TASK"],
        "vision_data_transfer_allowed_providers": ["alibaba-model-studio"],
        "vision_data_transfer_allowed_endpoint_regions": ["cn-beijing"],
        "vision_data_transfer_allowed_endpoint_hosts": ["dashscope.aliyuncs.com"],
    }
    first = WorkerRuntime.build(
        Settings(**boundary, alibaba_vision_maximum_output_tokens=2048),
        operation_executors={OperationKind.ASSET_VALIDATION: object()},
    )
    second = WorkerRuntime.build(
        Settings(**boundary, alibaba_vision_maximum_output_tokens=2049),
        operation_executors={OperationKind.ASSET_VALIDATION: object()},
    )
    try:
        first_analyzer = next(
            resource for resource in first.resources if isinstance(resource, AlibabaVisionAnalyzer)
        )
        second_analyzer = next(
            resource for resource in second.resources if isinstance(resource, AlibabaVisionAnalyzer)
        )
        assert (
            first_analyzer.configured_identity.configuration_snapshot_sha256
            != second_analyzer.configured_identity.configuration_snapshot_sha256
        )
    finally:
        first.close()
        second.close()


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
    assert environment["CV_MILVUS_URI"] == "http://127.0.0.1:19531"
    assert environment["CV_MILVUS_READINESS_TIMEOUT_SECONDS"] == "10"
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


def test_compose_worker_consumes_all_deployed_operation_queues() -> None:
    worker_environment = _compose_services()["worker"]["environment"]
    configured = json.loads(worker_environment["CV_WORKER_QUEUES"])
    required = json.loads(worker_environment["CV_WORKER_REQUIRED_OPERATION_KINDS"])

    assert configured == [
        "commercevision.workflow",
        "commercevision.asset",
        "commercevision.index",
        "commercevision.maintenance",
    ]
    assert required == [
        "ASSET_VALIDATION",
        "ASSET_DELETION",
        "ASSET_INDEXING",
        "PRODUCT_BRIEF_ANALYSIS",
    ]


def test_compose_scheduler_uses_the_shared_authenticated_rabbitmq_boundary() -> None:
    services = _compose_services()
    rabbitmq_url = "amqp://commercevision:${CV_RABBITMQ_PASSWORD:-commercevision}@rabbitmq:5672//"
    assert services["rabbitmq"]["environment"]["RABBITMQ_DEFAULT_PASS"] == (
        "${CV_RABBITMQ_PASSWORD:-commercevision}"
    )
    for service_name in ("api", "worker", "scheduler"):
        assert services[service_name]["environment"]["CV_RABBITMQ_URL"] == rabbitmq_url

    scheduler = services["scheduler"]
    assert scheduler["depends_on"]["rabbitmq"] == {"condition": "service_healthy"}
    scheduler_healthcheck = " ".join(scheduler["healthcheck"]["test"])
    assert "/health/ready" in scheduler_healthcheck
    assert "/health/live" not in scheduler_healthcheck

    example_environment = (_REPOSITORY_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "CV_RABBITMQ_PASSWORD=commercevision" in example_environment


def test_compose_mcp_has_authenticated_dependencies_and_readiness() -> None:
    service = _compose_services()["mcp-server"]
    environment = service["environment"]

    assert environment["CV_MYSQL_DSN"].startswith("mysql+pymysql://")
    assert environment["CV_TRUSTED_PRINCIPAL_CURRENT_KEY_ID"]
    assert environment["CV_TRUSTED_PRINCIPAL_CURRENT_HMAC_SECRET"]
    assert environment["CV_MCP_TOOL_POLICY_VERSION"] == (
        "${CV_MCP_TOOL_POLICY_VERSION:-mcp-tool-policy-v1}"
    )
    assert service["depends_on"] == {
        "mysql": {"condition": "service_healthy"},
        "migrate": {"condition": "service_completed_successfully"},
        "object-storage-init": {"condition": "service_completed_successfully"},
        "milvus": {"condition": "service_healthy"},
    }
    healthcheck = " ".join(service["healthcheck"]["test"])
    assert "/health/ready" in healthcheck
    assert "/health/live" not in healthcheck

    mcp_project = (_REPOSITORY_ROOT / "services/mcp-server/pyproject.toml").read_text(
        encoding="utf-8"
    )
    assert "commercevision-bootstrap" in mcp_project
    assert "commercevision-api" not in mcp_project


def test_compose_shares_product_brief_policy_without_exposing_worker_secret() -> None:
    services = _compose_services()
    api_environment = services["api"]["environment"]
    worker_environment = services["worker"]["environment"]
    deterministic_credential_fixture = (
        _REPOSITORY_ROOT / "infra/compose/fixtures/blank-alibaba-vision-api-key"
    ).resolve()

    assert {key: worker_environment.get(key) for key in _PRODUCT_BRIEF_POLICY_KEYS} == {
        key: api_environment.get(key) for key in _PRODUCT_BRIEF_POLICY_KEYS
    }
    assert all(worker_environment.get(key) is not None for key in _PRODUCT_BRIEF_POLICY_KEYS)
    assert "CV_ALIBABA_VISION_API_KEY" not in api_environment
    assert "CV_ALIBABA_VISION_API_KEY_FILE" not in api_environment
    assert "CV_ALIBABA_VISION_ALLOWED_IMAGE_ORIGINS" not in api_environment
    assert "CV_ALIBABA_VISION_API_KEY" not in worker_environment
    assert worker_environment["CV_ALIBABA_VISION_API_KEY_FILE"] == (
        "/run/secrets/alibaba-vision-api-key"
    )
    assert worker_environment["CV_ALIBABA_VISION_API_KEY_FILE_MAX_BYTES"] == (
        "${CV_ALIBABA_VISION_API_KEY_FILE_MAX_BYTES:-4096}"
    )
    assert "CV_ALIBABA_VISION_ALLOWED_IMAGE_ORIGINS" in worker_environment
    assert worker_environment["CV_VISION_PREFLIGHT_BUDGET_SECONDS"] == (
        "${CV_VISION_PREFLIGHT_BUDGET_SECONDS:-10}"
    )
    assert worker_environment["CV_VISION_OPERATION_LEASE_MARGIN_SECONDS"] == (
        "${CV_VISION_OPERATION_LEASE_MARGIN_SECONDS:-15}"
    )
    assert worker_environment["CV_PROVIDER_ARTIFACT_RECONCILIATION_TARGETS"] == (
        "${CV_PROVIDER_ARTIFACT_RECONCILIATION_TARGETS:-[]}"
    )
    assert "CV_PROVIDER_ARTIFACT_RECONCILIATION_TARGETS" not in api_environment
    assert {
        "type": "bind",
        "source": (
            "${CV_ALIBABA_VISION_API_KEY_HOST_PATH:-./fixtures/blank-alibaba-vision-api-key}"
        ),
        "target": "/run/secrets/alibaba-vision-api-key",
        "read_only": True,
    } in services["worker"]["volumes"]
    assert deterministic_credential_fixture.is_file()
    assert deterministic_credential_fixture.read_bytes().strip() == b""

    alibaba_settings = Settings(
        environment="ci",
        service_name="worker",
        worker_queues=["commercevision.asset"],
        worker_required_operation_kinds=[OperationKind.PRODUCT_BRIEF_ANALYSIS],
        vision_adapter="alibaba",
        alibaba_vision_api_key_file=str(deterministic_credential_fixture),
        alibaba_vision_allowed_image_origins=["https://assets.example.com"],
        vision_data_transfer_enabled=True,
        vision_data_transfer_allowed_workspace_ids=["Catalog-A"],
        vision_data_transfer_allowed_retention_classes=["TASK"],
        vision_data_transfer_allowed_providers=["alibaba-model-studio"],
        vision_data_transfer_allowed_endpoint_regions=["cn-beijing"],
        vision_data_transfer_allowed_endpoint_hosts=["dashscope.aliyuncs.com"],
    )
    with pytest.raises(RuntimeError, match="Vision API key is unavailable"):
        worker_runtime_module.product_brief.validate_product_brief_vision_credential(
            alibaba_settings
        )


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


def test_compose_worker_uses_maintainable_live_readiness_cli() -> None:
    worker = _compose_services()["worker"]
    assert "--pool=prefork" in worker["command"]
    assert worker["healthcheck"]["test"] == [
        "CMD",
        "python",
        "-m",
        "commercevision_worker.readiness",
        "healthcheck",
    ]


def test_worker_readiness_lease_is_explicit_across_runtime_config_surfaces() -> None:
    worker = _compose_services()["worker"]
    base_settings = yaml.safe_load(
        (_REPOSITORY_ROOT / "config/base.yaml").read_text(encoding="utf-8")
    )
    example_environment = {
        key: value
        for line in (_REPOSITORY_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
        for key, value in (line.split("=", 1),)
    }

    assert worker["environment"]["CV_WORKER_READINESS_MAX_AGE_SECONDS"] == (
        "${CV_WORKER_READINESS_MAX_AGE_SECONDS:-50}"
    )
    assert base_settings["worker_readiness_max_age_seconds"] == 50
    assert example_environment["CV_WORKER_READINESS_MAX_AGE_SECONDS"] == "50"

    settings = Settings(
        service_name="worker",
        worker_queues=["commercevision.asset"],
        worker_required_operation_kinds=[OperationKind.PRODUCT_BRIEF_ANALYSIS],
        asset_malware_adapter="clamav",
    )
    assert settings.worker_readiness_cycle_budget_seconds == 46
    assert settings.worker_readiness_max_age_seconds == 50


def test_compose_worker_shutdown_grace_uses_the_validated_execution_budget() -> None:
    worker = _compose_services()["worker"]

    assert worker["environment"]["CV_WORKER_STOP_GRACE_PERIOD_SECONDS"] == (
        "${CV_WORKER_STOP_GRACE_PERIOD_SECONDS:-90}"
    )
    assert worker["stop_grace_period"] == ("${CV_WORKER_STOP_GRACE_PERIOD_SECONDS:-90}s")
    assert Settings().worker_stop_grace_period_seconds == 90


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


def test_docker_build_context_excludes_generated_browser_test_artifacts() -> None:
    ignored = {
        line.strip()
        for line in (_REPOSITORY_ROOT / ".dockerignore").read_text("utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert "**/test-results" in ignored
    assert "**/playwright-report" in ignored
    assert "**/.mypy_cache" in ignored


def test_docker_build_context_recursively_excludes_runtime_secrets_and_environment_files() -> None:
    ignored = {
        line.strip()
        for line in (_REPOSITORY_ROOT / ".dockerignore").read_text("utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert "**/secrets" in ignored
    assert "**/.env" in ignored
    assert "**/.env.*" in ignored
    assert "!**/.env.example" in ignored
