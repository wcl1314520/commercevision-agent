import pytest
from commercevision_contracts import Settings
from commercevision_contracts.config import load_settings
from commercevision_domain import OperationKind
from pydantic import ValidationError


def test_settings_reject_unknown_environment() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="invalid")


def test_tool_intent_policy_configuration_is_bounded_and_server_owned() -> None:
    settings = Settings(
        tool_intent_policy_version="tool-intent-policy-v2",
        tool_intent_granted_scopes=["image.generate"],
        tool_intent_allowed_providers=["fixture"],
        tool_intent_allowed_cost_classes=["low"],
        tool_intent_quota_units=8,
        tool_intent_budget_units=7,
        tool_intent_maximum_intents=6,
        generation_rights_policy_version="asset-rights.v2",
        generation_actor_id="generation-command-service",
    )

    assert settings.tool_intent_policy_version == "tool-intent-policy-v2"
    assert settings.tool_intent_granted_scopes == ["image.generate"]
    assert settings.tool_intent_quota_units == 8
    assert settings.tool_intent_budget_units == 7
    assert settings.tool_intent_maximum_intents == 6
    assert settings.generation_rights_policy_version == "asset-rights.v2"
    assert settings.generation_actor_id == "generation-command-service"

    for invalid in (
        {"tool_intent_policy_version": "policy with spaces"},
        {"tool_intent_quota_units": -1},
        {"tool_intent_budget_units": -1},
        {"tool_intent_maximum_intents": 193},
        {"tool_intent_allowed_cost_classes": ["unbounded"]},
        {"generation_rights_policy_version": "policy with spaces"},
        {"generation_actor_id": "actor with spaces"},
    ):
        with pytest.raises(ValidationError):
            Settings(**invalid)


def test_retrieval_settings_enforce_short_lived_preview_and_retention_bounds() -> None:
    settings = Settings(
        retrieval_preview_token_lifetime_seconds=30,
        retrieval_preview_reference_lifetime_seconds=60,
        retrieval_run_retention_seconds=61,
    )

    assert settings.retrieval_rrf_k == 60
    assert settings.retrieval_preview_token_lifetime_seconds == 30
    assert settings.retrieval_preview_reference_lifetime_seconds == 60

    for invalid in (
        {"retrieval_preview_token_lifetime_seconds": 29},
        {"retrieval_preview_reference_lifetime_seconds": 61},
        {"retrieval_run_retention_seconds": 60},
        {"retrieval_milvus_maximum_filter_ids": 1001},
    ):
        with pytest.raises(ValidationError):
            Settings(**invalid)


def test_vision_transfer_and_provider_configuration_deny_by_default() -> None:
    settings = Settings()

    assert settings.vision_adapter == "deterministic"
    assert settings.vision_data_transfer_enabled is False
    assert settings.vision_data_transfer_allowed_workspace_ids == []
    assert settings.alibaba_vision_api_key is None
    assert settings.alibaba_vision_api_key_file is None
    assert settings.alibaba_vision_maximum_output_tokens == 4096
    assert settings.vision_product_facts_maximum_bytes == 64 * 1024
    assert settings.vision_product_facts_maximum_depth == 8
    assert settings.vision_product_facts_maximum_nodes == 1024
    assert settings.vision_product_facts_maximum_string_bytes == 4096
    assert "common.sensitive_claims" in settings.product_brief_sensitive_claim_paths
    assert "beauty.medical_like_claim_flags" in (settings.product_brief_sensitive_claim_paths)


def test_image_embedding_defaults_are_local_fixture_only() -> None:
    settings = Settings()

    assert settings.embedding_adapter == "deterministic"
    assert settings.embedding_provider == "fixture"
    assert settings.embedding_dimension == 256
    assert settings.alibaba_embedding_api_key is None
    assert settings.alibaba_embedding_api_key_file is None
    assert settings.embedding_data_transfer_enabled is False
    assert settings.embedding_data_transfer_allowed_workspace_ids == []


def test_production_image_index_worker_requires_controlled_alibaba_boundary() -> None:
    common = {
        "environment": "production",
        "worker_queues": ["commercevision.index"],
        "worker_required_operation_kinds": [OperationKind.ASSET_INDEXING],
        "object_store_endpoint": "https://object-storage.internal.example",
        "object_store_presign_endpoint": "https://assets.example",
        "object_store_secret_key": "production-object-store-secret",
        "object_store_require_encryption": True,
        "embedding_adapter": "alibaba",
        "embedding_provider": "alibaba-model-studio",
        "embedding_model_family": "qwen3-vl-embedding",
        "embedding_model_id": "qwen3-vl-embedding",
        "embedding_pinned_revision": "commercevision-qwen3-vl-embedding-epoch-2026-07-31",
        "embedding_dimension": 1024,
        "alibaba_embedding_allowed_image_origins": ["https://assets.example"],
        "embedding_data_transfer_enabled": True,
        "embedding_data_transfer_policy_version": "embedding-transfer-v1",
        "embedding_data_transfer_allowed_workspace_ids": ["Catalog-A"],
        "embedding_data_transfer_allowed_retention_classes": ["TASK", "FOUNDATION"],
        "embedding_data_transfer_allowed_providers": ["alibaba-model-studio"],
        "embedding_data_transfer_allowed_endpoint_regions": ["cn-beijing"],
        "embedding_data_transfer_allowed_endpoint_hosts": ["dashscope.aliyuncs.com"],
        "milvus_uri": "https://milvus.internal.example:19530",
        "milvus_token": "milvus-production-secret",
    }

    with pytest.raises(ValidationError, match="mounted API key"):
        Settings(**common)

    settings = Settings(
        **common,
        alibaba_embedding_api_key_file="/run/secrets/model-studio-api-key",
    )

    assert settings.alibaba_embedding_endpoint == "https://dashscope.aliyuncs.com/api/v1"
    assert settings.alibaba_embedding_endpoint_host == "dashscope.aliyuncs.com"
    assert settings.embedding_pinned_revision.startswith("commercevision-")
    assert "milvus-production-secret" not in repr(settings)

    without_milvus_auth = dict(common)
    without_milvus_auth.pop("milvus_token")
    with pytest.raises(ValidationError, match="Milvus authentication"):
        Settings(
            **without_milvus_auth,
            alibaba_embedding_api_key_file="/run/secrets/model-studio-api-key",
        )

    with pytest.raises(ValidationError, match="Milvus TLS"):
        Settings(
            **{**common, "milvus_uri": "http://milvus.internal.example:19530"},
            alibaba_embedding_api_key_file="/run/secrets/model-studio-api-key",
        )

    with pytest.raises(ValidationError, match="credential-free"):
        Settings(
            **{
                **common,
                "milvus_uri": "https://root:uri-secret@milvus.internal.example:19530",
            },
            alibaba_embedding_api_key_file="/run/secrets/model-studio-api-key",
        )

    for invalid_endpoint in (
        "https://milvus.internal.example:19530/tenant",
        "https://milvus.internal.example:19530?token=uri-secret",
        "https://milvus.internal.example:19530#uri-secret",
    ):
        with pytest.raises(ValidationError, match="without path"):
            Settings(
                **{**common, "milvus_uri": invalid_endpoint},
                alibaba_embedding_api_key_file="/run/secrets/model-studio-api-key",
            )

    with pytest.raises(ValidationError, match="default Milvus credential"):
        Settings(
            **{**common, "milvus_token": "root:Milvus"},
            alibaba_embedding_api_key_file="/run/secrets/model-studio-api-key",
        )


def test_alibaba_embedding_rejects_provider_snapshot_semantics_and_static_prod_secret() -> None:
    with pytest.raises(ValidationError, match="qwen3-vl-embedding"):
        Settings(
            embedding_adapter="alibaba",
            embedding_provider="alibaba-model-studio",
            embedding_model_id="qwen3-vl-embedding-2026-07-31",
        )

    with pytest.raises(ValidationError, match="mounted API key"):
        Settings(
            environment="production",
            worker_queues=["commercevision.index"],
            worker_required_operation_kinds=[OperationKind.ASSET_INDEXING],
            object_store_endpoint="https://object-storage.internal.example",
            object_store_presign_endpoint="https://assets.example",
            object_store_secret_key="production-object-store-secret",
            object_store_require_encryption=True,
            embedding_adapter="alibaba",
            embedding_provider="alibaba-model-studio",
            embedding_model_family="qwen3-vl-embedding",
            embedding_model_id="qwen3-vl-embedding",
            embedding_pinned_revision="commercevision-qwen3-vl-embedding-epoch-2026-07-31",
            embedding_dimension=1024,
            alibaba_embedding_api_key="must-not-be-static-in-production",
            alibaba_embedding_allowed_image_origins=["https://assets.example"],
            embedding_data_transfer_enabled=True,
            embedding_data_transfer_policy_version="embedding-transfer-v1",
            embedding_data_transfer_allowed_workspace_ids=["Catalog-A"],
            embedding_data_transfer_allowed_retention_classes=["TASK"],
            embedding_data_transfer_allowed_providers=["alibaba-model-studio"],
            embedding_data_transfer_allowed_endpoint_regions=["cn-beijing"],
            embedding_data_transfer_allowed_endpoint_hosts=["dashscope.aliyuncs.com"],
        )


def test_vision_provider_budgets_are_explicitly_bounded() -> None:
    settings = Settings(
        alibaba_vision_maximum_output_tokens=2048,
        vision_product_facts_maximum_bytes=32 * 1024,
        vision_product_facts_maximum_depth=6,
        vision_product_facts_maximum_nodes=512,
        vision_product_facts_maximum_string_bytes=2048,
    )

    assert settings.alibaba_vision_maximum_output_tokens == 2048
    assert settings.vision_product_facts_maximum_bytes == 32 * 1024
    assert settings.vision_product_facts_maximum_depth == 6
    assert settings.vision_product_facts_maximum_nodes == 512
    assert settings.vision_product_facts_maximum_string_bytes == 2048

    invalid_budgets = (
        {"alibaba_vision_maximum_output_tokens": 0},
        {"vision_product_facts_maximum_bytes": 1},
        {"vision_product_facts_maximum_depth": 0},
        {"vision_product_facts_maximum_nodes": 0},
        {"vision_product_facts_maximum_string_bytes": 0},
        {"alibaba_vision_maximum_response_bytes": 2 * 1024 * 1024},
    )
    for invalid in invalid_budgets:
        with pytest.raises(ValidationError):
            Settings(**invalid)


@pytest.mark.parametrize(
    "scenario",
    ["rejected", "throttled", "unknown"],
)
def test_deterministic_vision_failure_scenarios_are_configurable(scenario: str) -> None:
    settings = Settings(deterministic_vision_scenario=scenario)

    assert settings.deterministic_vision_scenario == scenario


def test_product_brief_review_policy_rejects_unknown_or_duplicate_field_paths() -> None:
    with pytest.raises(ValidationError, match="review policy field"):
        Settings(product_brief_sensitive_claim_paths=["unknown.field"])
    with pytest.raises(ValidationError, match="unique"):
        Settings(
            product_brief_mandatory_review_paths=[
                "common.identity",
                "common.identity",
            ]
        )


def test_alibaba_vision_configuration_requires_complete_controlled_boundary() -> None:
    with pytest.raises(ValidationError, match="Alibaba Vision API key"):
        Settings(
            vision_adapter="alibaba",
            worker_required_operation_kinds=[OperationKind.PRODUCT_BRIEF_ANALYSIS],
        )
    with pytest.raises(ValidationError, match="Alibaba Vision API key"):
        Settings(
            vision_adapter="alibaba",
            worker_required_operation_kinds=[OperationKind.PRODUCT_BRIEF_ANALYSIS],
            alibaba_vision_api_key=" ",
            alibaba_vision_allowed_image_origins=["https://assets.example.com"],
            vision_data_transfer_enabled=True,
            vision_data_transfer_allowed_workspace_ids=["Catalog-A"],
            vision_data_transfer_allowed_retention_classes=["TASK"],
            vision_data_transfer_allowed_providers=["alibaba-model-studio"],
            vision_data_transfer_allowed_endpoint_regions=["cn-beijing"],
            vision_data_transfer_allowed_endpoint_hosts=["dashscope.aliyuncs.com"],
        )

    settings = Settings(
        vision_adapter="alibaba",
        worker_required_operation_kinds=[OperationKind.PRODUCT_BRIEF_ANALYSIS],
        alibaba_vision_api_key="vision-secret",
        alibaba_vision_allowed_image_origins=["https://assets.example.com"],
        vision_data_transfer_enabled=True,
        vision_data_transfer_allowed_workspace_ids=["Catalog-A"],
        vision_data_transfer_allowed_retention_classes=["TASK"],
        vision_data_transfer_allowed_providers=["alibaba-model-studio"],
        vision_data_transfer_allowed_endpoint_regions=["cn-beijing"],
        vision_data_transfer_allowed_endpoint_hosts=["dashscope.aliyuncs.com"],
    )

    assert settings.alibaba_vision_endpoint_host == "dashscope.aliyuncs.com"
    assert settings.alibaba_vision_allowed_image_origins == ["https://assets.example.com"]


def test_alibaba_vision_static_and_mounted_file_credentials_are_exclusive() -> None:
    boundary = {
        "vision_adapter": "alibaba",
        "worker_required_operation_kinds": [OperationKind.PRODUCT_BRIEF_ANALYSIS],
        "alibaba_vision_allowed_image_origins": ["https://assets.example.com"],
        "vision_data_transfer_enabled": True,
        "vision_data_transfer_allowed_workspace_ids": ["Catalog-A"],
        "vision_data_transfer_allowed_retention_classes": ["TASK"],
        "vision_data_transfer_allowed_providers": ["alibaba-model-studio"],
        "vision_data_transfer_allowed_endpoint_regions": ["cn-beijing"],
        "vision_data_transfer_allowed_endpoint_hosts": ["dashscope.aliyuncs.com"],
    }

    with pytest.raises(ValidationError, match="exactly one.*credential source"):
        Settings(
            **boundary,
            alibaba_vision_api_key="static-secret",
            alibaba_vision_api_key_file="/run/secrets/model-studio-api-key",
        )
    with pytest.raises(ValidationError, match="absolute"):
        Settings(
            **boundary,
            alibaba_vision_api_key_file="relative/model-studio-api-key",
        )

    settings = Settings(
        **boundary,
        alibaba_vision_api_key_file="/run/secrets/model-studio-api-key",
        alibaba_vision_api_key_file_max_bytes=256,
    )

    assert settings.alibaba_vision_api_key is None
    assert settings.alibaba_vision_api_key_file == "/run/secrets/model-studio-api-key"
    assert settings.alibaba_vision_api_key_file_max_bytes == 256


def test_alibaba_vision_policy_identity_does_not_expose_worker_secret_to_api() -> None:
    settings = Settings(
        vision_adapter="alibaba",
        vision_data_transfer_enabled=True,
        vision_data_transfer_allowed_workspace_ids=["Catalog-A"],
        vision_data_transfer_allowed_retention_classes=["TASK"],
        vision_data_transfer_allowed_providers=["alibaba-model-studio"],
        vision_data_transfer_allowed_endpoint_regions=["cn-beijing"],
        vision_data_transfer_allowed_endpoint_hosts=["dashscope.aliyuncs.com"],
    )

    assert settings.alibaba_vision_api_key is None
    assert settings.alibaba_vision_allowed_image_origins == []


@pytest.mark.parametrize(
    ("environment_name", "environment_value"),
    [
        ("CV_ALIBABA_VISION_API_KEY", "vision-secret"),
        (
            "CV_ALIBABA_VISION_API_KEY_FILE",
            "/run/secrets/model-studio-api-key",
        ),
        (
            "CV_ALIBABA_VISION_ALLOWED_IMAGE_ORIGINS",
            '["https://assets.example.com"]',
        ),
    ],
)
@pytest.mark.parametrize(
    "service_name",
    [
        "api",
        "control-api",
        "scheduler",
        "migration",
        "mcp-server",
        "object-storage-init",
    ],
)
def test_load_settings_rejects_vision_execution_inputs_in_non_product_brief_process(
    monkeypatch: pytest.MonkeyPatch,
    environment_name: str,
    environment_value: str,
    service_name: str,
) -> None:
    monkeypatch.setenv("CV_SERVICE_NAME", "worker")
    monkeypatch.setenv(
        "CV_WORKER_REQUIRED_OPERATION_KINDS",
        '["PRODUCT_BRIEF_ANALYSIS"]',
    )
    monkeypatch.setenv(environment_name, environment_value)

    with pytest.raises(ValidationError, match="ProductBrief-executing process"):
        load_settings(service_name)


def test_load_settings_preserves_product_brief_worker_execution_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CV_SERVICE_NAME", "api")
    monkeypatch.setenv("CV_VISION_ADAPTER", "alibaba")
    monkeypatch.setenv(
        "CV_WORKER_REQUIRED_OPERATION_KINDS",
        '["PRODUCT_BRIEF_ANALYSIS"]',
    )
    monkeypatch.setenv("CV_ALIBABA_VISION_API_KEY", "vision-secret")
    monkeypatch.setenv(
        "CV_ALIBABA_VISION_ALLOWED_IMAGE_ORIGINS",
        '["https://assets.example.com"]',
    )
    monkeypatch.setenv("CV_VISION_DATA_TRANSFER_ENABLED", "true")
    monkeypatch.setenv(
        "CV_VISION_DATA_TRANSFER_ALLOWED_WORKSPACE_IDS",
        '["Catalog-A"]',
    )
    monkeypatch.setenv(
        "CV_VISION_DATA_TRANSFER_ALLOWED_RETENTION_CLASSES",
        '["TASK"]',
    )
    monkeypatch.setenv(
        "CV_VISION_DATA_TRANSFER_ALLOWED_PROVIDERS",
        '["alibaba-model-studio"]',
    )
    monkeypatch.setenv(
        "CV_VISION_DATA_TRANSFER_ALLOWED_ENDPOINT_REGIONS",
        '["cn-beijing"]',
    )
    monkeypatch.setenv(
        "CV_VISION_DATA_TRANSFER_ALLOWED_ENDPOINT_HOSTS",
        '["dashscope.aliyuncs.com"]',
    )

    settings = load_settings("worker")

    assert settings.service_name == "worker"
    assert settings.alibaba_vision_api_key is not None
    assert settings.alibaba_vision_allowed_image_origins == ["https://assets.example.com"]


def test_load_settings_rejects_vision_inputs_for_worker_without_product_brief_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CV_ALIBABA_VISION_API_KEY", "vision-secret")

    with pytest.raises(ValidationError, match="ProductBrief-executing process"):
        load_settings("worker")


def test_alibaba_vision_temporary_reference_must_outlive_execution_deadline() -> None:
    with pytest.raises(
        ValidationError,
        match="temporary reference lifetime must exceed provider execution",
    ):
        Settings(
            vision_adapter="alibaba",
            worker_required_operation_kinds=[OperationKind.PRODUCT_BRIEF_ANALYSIS],
            alibaba_vision_api_key="vision-secret",
            alibaba_vision_allowed_image_origins=["https://assets.example.com"],
            alibaba_vision_connect_timeout_seconds=3,
            alibaba_vision_read_timeout_seconds=30,
            alibaba_vision_end_to_end_timeout_seconds=45,
            vision_temporary_reference_lifetime_seconds=45,
            vision_data_transfer_enabled=True,
            vision_data_transfer_allowed_workspace_ids=["Catalog-A"],
            vision_data_transfer_allowed_retention_classes=["TASK"],
            vision_data_transfer_allowed_providers=["alibaba-model-studio"],
            vision_data_transfer_allowed_endpoint_regions=["cn-beijing"],
            vision_data_transfer_allowed_endpoint_hosts=["dashscope.aliyuncs.com"],
        )


def test_alibaba_vision_operation_lease_covers_preflight_provider_and_commit_budgets() -> None:
    provider_boundary = {
        "vision_adapter": "alibaba",
        "worker_required_operation_kinds": [OperationKind.PRODUCT_BRIEF_ANALYSIS],
        "alibaba_vision_api_key": "vision-secret",
        "alibaba_vision_allowed_image_origins": ["https://assets.example.com"],
        "alibaba_vision_connect_timeout_seconds": 3,
        "alibaba_vision_read_timeout_seconds": 30,
        "alibaba_vision_end_to_end_timeout_seconds": 45,
        "vision_data_transfer_enabled": True,
        "vision_data_transfer_allowed_workspace_ids": ["Catalog-A"],
        "vision_data_transfer_allowed_retention_classes": ["TASK"],
        "vision_data_transfer_allowed_providers": ["alibaba-model-studio"],
        "vision_data_transfer_allowed_endpoint_regions": ["cn-beijing"],
        "vision_data_transfer_allowed_endpoint_hosts": ["dashscope.aliyuncs.com"],
    }

    with pytest.raises(
        ValidationError,
        match="operation lease must cover preflight, provider, and commit budgets",
    ):
        Settings(
            **provider_boundary,
            workflow_step_lease_seconds=69,
            vision_preflight_budget_seconds=10,
            vision_operation_lease_margin_seconds=15,
        )

    settings = Settings(
        **provider_boundary,
        workflow_step_lease_seconds=70,
        vision_preflight_budget_seconds=10,
        vision_operation_lease_margin_seconds=15,
    )

    assert settings.workflow_step_lease_seconds == 70


def test_product_brief_worker_shutdown_grace_covers_the_execution_budget() -> None:
    provider_boundary = {
        "service_name": "worker",
        "vision_adapter": "alibaba",
        "worker_queues": ["commercevision.asset"],
        "worker_required_operation_kinds": [OperationKind.PRODUCT_BRIEF_ANALYSIS],
        "alibaba_vision_api_key": "vision-secret",
        "alibaba_vision_allowed_image_origins": ["https://assets.example.com"],
        "alibaba_vision_connect_timeout_seconds": 3,
        "alibaba_vision_read_timeout_seconds": 30,
        "alibaba_vision_end_to_end_timeout_seconds": 45,
        "vision_data_transfer_enabled": True,
        "vision_data_transfer_allowed_workspace_ids": ["Catalog-A"],
        "vision_data_transfer_allowed_retention_classes": ["TASK"],
        "vision_data_transfer_allowed_providers": ["alibaba-model-studio"],
        "vision_data_transfer_allowed_endpoint_regions": ["cn-beijing"],
        "vision_data_transfer_allowed_endpoint_hosts": ["dashscope.aliyuncs.com"],
        "vision_preflight_budget_seconds": 10,
        "vision_operation_lease_margin_seconds": 15,
    }

    with pytest.raises(
        ValidationError,
        match="shutdown grace must cover preflight, provider, and cleanup budgets",
    ):
        Settings(
            **provider_boundary,
            worker_stop_grace_period_seconds=69,
        )

    settings = Settings(
        **provider_boundary,
        worker_stop_grace_period_seconds=70,
    )

    assert settings.worker_stop_grace_period_seconds == 70


def test_provider_artifact_reconciliation_targets_are_explicit_worker_configuration() -> None:
    target = {
        "object_store_backend": "oss",
        "object_store_credential_mode": "ecs_ram_role",
        "object_store_endpoint": "https://oss-cn-hangzhou-internal.aliyuncs.com",
        "object_store_presign_endpoint": "https://oss-cn-hangzhou.aliyuncs.com",
        "object_store_region": "cn-hangzhou",
        "object_store_provider_result_bucket": "legacy-provider-results",
        "object_store_force_path_style": False,
    }

    settings = Settings(
        service_name="worker",
        worker_queues=["commercevision.asset"],
        worker_required_operation_kinds=[OperationKind.PRODUCT_BRIEF_ANALYSIS],
        provider_artifact_reconciliation_targets=[target],
        worker_readiness_max_age_seconds=67,
    )

    assert len(settings.provider_artifact_reconciliation_targets) == 1
    configured = settings.provider_artifact_reconciliation_targets[0]
    assert configured.object_store_backend == "oss"
    assert configured.object_store_provider_result_bucket == "legacy-provider-results"

    with pytest.raises(ValidationError, match="exact targets must be unique"):
        Settings(
            service_name="worker",
            worker_queues=["commercevision.asset"],
            worker_required_operation_kinds=[OperationKind.PRODUCT_BRIEF_ANALYSIS],
            provider_artifact_reconciliation_targets=[target, target],
            worker_readiness_max_age_seconds=67,
        )

    with pytest.raises(ValidationError, match="ProductBrief-executing Worker"):
        Settings(
            service_name="api",
            provider_artifact_reconciliation_targets=[target],
        )


def test_worker_readiness_marker_lease_covers_the_full_remote_probe_cycle() -> None:
    worker_boundary = {
        "service_name": "worker",
        "worker_queues": ["commercevision.asset"],
        "worker_required_operation_kinds": [OperationKind.PRODUCT_BRIEF_ANALYSIS],
        "object_store_readiness_timeout_seconds": 2,
        "mysql_connect_timeout_seconds": 7,
        "asset_malware_adapter": "clamav",
        "clamav_timeout_seconds": 11,
        "provider_artifact_reconciliation_targets": [
            {
                "object_store_backend": "oss",
                "object_store_credential_mode": "ecs_ram_role",
                "object_store_endpoint": "https://oss-cn-hangzhou-internal.aliyuncs.com",
                "object_store_presign_endpoint": "https://oss-cn-hangzhou.aliyuncs.com",
                "object_store_region": "cn-hangzhou",
                "object_store_provider_result_bucket": "provider-results-legacy",
                "object_store_force_path_style": False,
                "object_store_readiness_timeout_seconds": 4,
                "object_store_credential_refresh_timeout_seconds": 3,
            }
        ],
    }

    with pytest.raises(
        ValidationError,
        match="readiness marker lease must cover the full remote probe cycle",
    ):
        Settings(
            **worker_boundary,
            worker_readiness_max_age_seconds=82.99,
        )

    settings = Settings(
        **worker_boundary,
        worker_readiness_max_age_seconds=83,
    )

    assert settings.worker_readiness_cycle_budget_seconds == 83
    assert settings.worker_readiness_max_age_seconds == 83


def test_production_alibaba_vision_requires_a_dated_immutable_model_snapshot() -> None:
    production_boundary = {
        "environment": "production",
        "worker_queues": ["commercevision.workflow"],
        "worker_required_operation_kinds": [OperationKind.PRODUCT_BRIEF_ANALYSIS],
        "object_store_endpoint": "https://minio.internal.example",
        "object_store_presign_endpoint": "https://assets.example",
        "object_store_secret_key": "production-object-store-secret",
        "object_store_require_encryption": True,
        "vision_adapter": "alibaba",
        "alibaba_vision_api_key_file": "/run/secrets/model-studio-api-key",
        "alibaba_vision_model": "qwen3-vl-plus",
        "alibaba_vision_allowed_image_origins": ["https://assets.example.com"],
        "vision_data_transfer_enabled": True,
        "vision_data_transfer_allowed_workspace_ids": ["Catalog-A"],
        "vision_data_transfer_allowed_retention_classes": ["TASK"],
        "vision_data_transfer_allowed_providers": ["alibaba-model-studio"],
        "vision_data_transfer_allowed_endpoint_regions": ["cn-beijing"],
        "vision_data_transfer_allowed_endpoint_hosts": ["dashscope.aliyuncs.com"],
    }

    with pytest.raises(ValidationError, match="dated immutable model snapshot"):
        Settings(
            **production_boundary,
            alibaba_vision_model_snapshot="qwen3-vl-plus",
        )

    settings = Settings(
        **production_boundary,
        alibaba_vision_model_snapshot="qwen3-vl-plus-2025-12-19",
    )

    assert settings.alibaba_vision_model_snapshot == "qwen3-vl-plus-2025-12-19"


def test_production_alibaba_vision_rejects_static_only_credentials() -> None:
    with pytest.raises(ValidationError, match="mounted API key file"):
        Settings(
            environment="production",
            worker_queues=["commercevision.workflow"],
            worker_required_operation_kinds=[OperationKind.PRODUCT_BRIEF_ANALYSIS],
            object_store_endpoint="https://minio.internal.example",
            object_store_presign_endpoint="https://assets.example",
            object_store_secret_key="production-object-store-secret",
            object_store_require_encryption=True,
            vision_adapter="alibaba",
            alibaba_vision_api_key="vision-secret",
            alibaba_vision_model="qwen3-vl-plus",
            alibaba_vision_model_snapshot="qwen3-vl-plus-2025-12-19",
            alibaba_vision_allowed_image_origins=["https://assets.example.com"],
            vision_data_transfer_enabled=True,
            vision_data_transfer_allowed_workspace_ids=["Catalog-A"],
            vision_data_transfer_allowed_retention_classes=["TASK"],
            vision_data_transfer_allowed_providers=["alibaba-model-studio"],
            vision_data_transfer_allowed_endpoint_regions=["cn-beijing"],
            vision_data_transfer_allowed_endpoint_hosts=["dashscope.aliyuncs.com"],
        )


def test_validation_transfer_workspace_allowlist_preserves_binary_identity() -> None:
    settings = Settings(
        validation_data_transfer_allowed_workspace_ids=["Catalog-A", "catalog-a"],
        validation_data_transfer_allowed_providers=[" ALIBABA-GREEN "],
        validation_data_transfer_allowed_endpoint_regions=[" CN-SHANGHAI "],
    )

    assert settings.validation_data_transfer_allowed_workspace_ids == [
        "Catalog-A",
        "catalog-a",
    ]
    assert settings.validation_data_transfer_allowed_providers == ["alibaba-green"]
    assert settings.validation_data_transfer_allowed_endpoint_regions == ["cn-shanghai"]


@pytest.mark.parametrize(
    "workspace_id",
    [
        " Catalog-A",
        "Catalog-A ",
        "catalog workspace",
        "catalog/workspace",
        ".catalog",
        "-catalog",
        "",
        "\u5546\u54c1\u5de5\u4f5c\u533a",
    ],
)
def test_validation_transfer_workspace_allowlist_rejects_noncanonical_identity(
    workspace_id: str,
) -> None:
    with pytest.raises(
        ValidationError,
        match="validation data transfer workspace allowlist is invalid",
    ):
        Settings(validation_data_transfer_allowed_workspace_ids=[workspace_id])


def test_validation_transfer_workspace_allowlist_rejects_exact_duplicates() -> None:
    with pytest.raises(ValidationError, match="workspace allowlist must be unique"):
        Settings(
            validation_data_transfer_allowed_workspace_ids=[
                "Catalog-A",
                "Catalog-A",
            ]
        )


def test_validation_transfer_endpoint_hosts_are_exact_canonical_dns_names() -> None:
    settings = Settings(
        alibaba_content_safety_endpoint="green-cip.cn-shanghai.aliyuncs.com",
        validation_data_transfer_allowed_endpoint_hosts=["green-cip.cn-shanghai.aliyuncs.com"],
    )

    assert settings.alibaba_content_safety_endpoint == "green-cip.cn-shanghai.aliyuncs.com"
    assert settings.validation_data_transfer_allowed_endpoint_hosts == [
        "green-cip.cn-shanghai.aliyuncs.com"
    ]


@pytest.mark.parametrize(
    "endpoint_host",
    [
        " green-cip.cn-shanghai.aliyuncs.com",
        "Green-CIP.cn-shanghai.aliyuncs.com",
        "https://green-cip.cn-shanghai.aliyuncs.com",
        "green-cip.cn-shanghai.aliyuncs.com:443",
        "green-cip.cn-shanghai.aliyuncs.com/path",
        "green-cip.cn-shanghai.aliyuncs.com.",
        "*.aliyuncs.com",
        "127.0.0.1",
        "localhost",
        "\u5185\u5bb9\u5b89\u5168.example",
    ],
)
def test_settings_rejects_noncanonical_provider_endpoint_hosts(
    endpoint_host: str,
) -> None:
    with pytest.raises(ValidationError, match="canonical DNS hostname|IP literal"):
        Settings(alibaba_content_safety_endpoint=endpoint_host)
    with pytest.raises(ValidationError, match="canonical DNS hostname|IP literal"):
        Settings(validation_data_transfer_allowed_endpoint_hosts=[endpoint_host])


def test_load_settings_sets_process_name(monkeypatch) -> None:
    monkeypatch.delenv("CV_SERVICE_NAME", raising=False)
    settings = load_settings("scheduler")

    assert settings.service_name == "scheduler"
    assert settings.cors_origins == ["http://localhost:13000"]


def test_environment_overrides_base_yaml(monkeypatch) -> None:
    monkeypatch.setenv("CV_LOG_LEVEL", "DEBUG")

    settings = Settings()

    assert settings.log_level == "DEBUG"


def test_secret_file_source_uses_cv_prefixed_filename(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CV_SECRETS_DIR", str(tmp_path))
    monkeypatch.delenv("CV_OBJECT_STORE_SECRET_KEY", raising=False)
    (tmp_path / "CV_OBJECT_STORE_SECRET_KEY").write_text("from-secret-file", encoding="utf-8")

    settings = Settings()

    assert settings.object_store_secret_key.get_secret_value() == "from-secret-file"


def test_environment_overrides_secret_file(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CV_SECRETS_DIR", str(tmp_path))
    monkeypatch.setenv("CV_OBJECT_STORE_SECRET_KEY", "from-environment")
    (tmp_path / "CV_OBJECT_STORE_SECRET_KEY").write_text("from-secret-file", encoding="utf-8")

    settings = Settings()

    assert settings.object_store_secret_key.get_secret_value() == "from-environment"


def test_trusted_principal_rotation_secrets_load_from_secret_files(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("CV_SECRETS_DIR", str(tmp_path))
    monkeypatch.setenv("CV_TRUSTED_PRINCIPAL_CURRENT_KEY_ID", "gateway-current")
    monkeypatch.setenv("CV_TRUSTED_PRINCIPAL_PREVIOUS_KEY_ID", "gateway-previous")
    monkeypatch.delenv("CV_TRUSTED_PRINCIPAL_CURRENT_HMAC_SECRET", raising=False)
    monkeypatch.delenv("CV_TRUSTED_PRINCIPAL_PREVIOUS_HMAC_SECRET", raising=False)
    (tmp_path / "CV_TRUSTED_PRINCIPAL_CURRENT_HMAC_SECRET").write_text(
        "current-secret-from-file-000000000001",
        encoding="utf-8",
    )
    (tmp_path / "CV_TRUSTED_PRINCIPAL_PREVIOUS_HMAC_SECRET").write_text(
        "previous-secret-from-file-0000000001",
        encoding="utf-8",
    )

    settings = Settings()

    assert settings.trusted_principal_current_key_id == "gateway-current"
    assert (
        settings.trusted_principal_current_hmac_secret.get_secret_value()
        == "current-secret-from-file-000000000001"
    )
    assert settings.trusted_principal_previous_key_id == "gateway-previous"
    assert (
        settings.trusted_principal_previous_hmac_secret.get_secret_value()
        == "previous-secret-from-file-0000000001"
    )


def test_trusted_principal_rotation_configuration_is_atomic_and_distinct() -> None:
    with pytest.raises(ValidationError, match="current trusted-principal key"):
        Settings(trusted_principal_current_key_id="gateway-current")
    with pytest.raises(ValidationError, match="previous trusted-principal key"):
        Settings(
            trusted_principal_current_key_id="gateway-current",
            trusted_principal_current_hmac_secret="current-secret-00000000000000000001",
            trusted_principal_previous_key_id="gateway-previous",
        )
    with pytest.raises(ValidationError, match="must be distinct"):
        Settings(
            trusted_principal_current_key_id="gateway-current",
            trusted_principal_current_hmac_secret="current-secret-00000000000000000001",
            trusted_principal_previous_key_id="gateway-current",
            trusted_principal_previous_hmac_secret="previous-secret-000000000000000001",
        )


def test_brand_profile_cursor_lifetime_defaults_and_bounds_are_explicit() -> None:
    settings = Settings()

    assert settings.brand_profile_cursor_max_age_seconds == 86_400
    assert settings.brand_profile_cursor_future_skew_seconds == 30
    assert (
        Settings(
            brand_profile_cursor_max_age_seconds=60,
            brand_profile_cursor_future_skew_seconds=0,
        ).brand_profile_cursor_max_age_seconds
        == 60
    )
    assert (
        Settings(
            brand_profile_cursor_max_age_seconds=604_800,
            brand_profile_cursor_future_skew_seconds=300,
        ).brand_profile_cursor_future_skew_seconds
        == 300
    )

    for invalid in (
        {"brand_profile_cursor_max_age_seconds": 59},
        {"brand_profile_cursor_max_age_seconds": 604_801},
        {"brand_profile_cursor_future_skew_seconds": -1},
        {"brand_profile_cursor_future_skew_seconds": 301},
    ):
        with pytest.raises(ValidationError):
            Settings(**invalid)


def test_creative_plan_cursor_lifetime_defaults_and_bounds_are_explicit() -> None:
    settings = Settings()

    assert settings.creative_plan_cursor_max_age_seconds == 86_400
    assert settings.creative_plan_cursor_future_skew_seconds == 30
    assert (
        Settings(
            creative_plan_cursor_max_age_seconds=60,
            creative_plan_cursor_future_skew_seconds=0,
        ).creative_plan_cursor_max_age_seconds
        == 60
    )
    assert (
        Settings(
            creative_plan_cursor_max_age_seconds=604_800,
            creative_plan_cursor_future_skew_seconds=300,
        ).creative_plan_cursor_future_skew_seconds
        == 300
    )

    for invalid in (
        {"creative_plan_cursor_max_age_seconds": 59},
        {"creative_plan_cursor_max_age_seconds": 604_801},
        {"creative_plan_cursor_future_skew_seconds": -1},
        {"creative_plan_cursor_future_skew_seconds": 301},
    ):
        with pytest.raises(ValidationError):
            Settings(**invalid)


def test_workflow_event_stream_budgets_are_bounded() -> None:
    settings = Settings()

    assert settings.workflow_event_cursor_max_age_seconds == 3600
    assert settings.workflow_event_cursor_future_skew_seconds == 30
    assert settings.workflow_event_page_size == 100
    assert settings.workflow_event_poll_interval_seconds == 1.0
    assert settings.workflow_event_heartbeat_seconds == 15.0
    assert settings.workflow_event_retry_milliseconds == 3000
    assert settings.workflow_event_max_session_seconds == 300.0
    assert settings.workflow_event_max_pages_per_session == 100

    for invalid in (
        {"workflow_event_cursor_max_age_seconds": 59},
        {"workflow_event_cursor_future_skew_seconds": 301},
        {"workflow_event_page_size": 201},
        {"workflow_event_poll_interval_seconds": 0},
        {"workflow_event_heartbeat_seconds": 61},
        {"workflow_event_retry_milliseconds": 30_001},
        {"workflow_event_max_session_seconds": 3601},
        {"workflow_event_max_pages_per_session": 1001},
    ):
        with pytest.raises(ValidationError):
            Settings(**invalid)


def test_empty_compose_previous_trusted_principal_pair_is_absent_but_not_partial() -> None:
    settings = Settings(
        trusted_principal_previous_key_id="",
        trusted_principal_previous_hmac_secret="",
    )

    assert settings.trusted_principal_previous_key_id is None
    assert settings.trusted_principal_previous_hmac_secret is None

    with pytest.raises(ValidationError):
        Settings(
            trusted_principal_current_key_id="gateway-current",
            trusted_principal_current_hmac_secret=("current-secret-00000000000000000001"),
            trusted_principal_previous_key_id="gateway-previous",
            trusted_principal_previous_hmac_secret="",
        )


def test_settings_reject_unknown_mcp_transport() -> None:
    with pytest.raises(ValidationError):
        Settings(mcp_transport="websocket")


@pytest.mark.parametrize(
    "field_name",
    [
        "worker_consumer_name",
        "workflow_queue_name",
        "asset_queue_name",
        "index_queue_name",
        "maintenance_queue_name",
    ],
)
def test_settings_trim_queue_and_consumer_identities(field_name: str) -> None:
    settings = Settings(**{field_name: "  configured-name  "})

    assert getattr(settings, field_name) == "configured-name"


@pytest.mark.parametrize(
    "field_name",
    [
        "worker_consumer_name",
        "workflow_queue_name",
        "asset_queue_name",
        "index_queue_name",
        "maintenance_queue_name",
    ],
)
def test_settings_reject_blank_queue_and_consumer_identities(field_name: str) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field_name: "   "})


def test_settings_reject_duplicate_logical_queue_names() -> None:
    with pytest.raises(ValidationError):
        Settings(
            workflow_queue_name="commercevision.shared",
            asset_queue_name=" commercevision.shared ",
        )


def test_worker_queues_none_selects_all_configured_queues() -> None:
    settings = Settings(worker_queues=None)

    assert settings.configured_worker_queues == (
        settings.workflow_queue_name,
        settings.asset_queue_name,
        settings.index_queue_name,
        settings.maintenance_queue_name,
    )


def test_settings_reject_explicit_empty_worker_queue_selection() -> None:
    with pytest.raises(ValidationError):
        Settings(worker_queues=[])


def test_settings_trim_and_preserve_explicit_worker_queue_selection() -> None:
    settings = Settings(
        worker_queues=[" commercevision.asset ", "commercevision.index"],
    )

    assert settings.configured_worker_queues == (
        "commercevision.asset",
        "commercevision.index",
    )


def test_settings_reject_duplicate_worker_queue_selection() -> None:
    with pytest.raises(ValidationError):
        Settings(
            worker_queues=["commercevision.asset", " commercevision.asset "],
        )


def test_settings_reject_unknown_worker_queue_selection() -> None:
    with pytest.raises(ValidationError):
        Settings(worker_queues=["commercevision.unknown"])


def test_settings_accept_bounded_worker_message_retry_backoff() -> None:
    settings = Settings(
        worker_message_retry_initial_seconds=0.4,
        worker_message_retry_max_seconds=30,
    )

    assert settings.worker_message_retry_initial_seconds == 0.4
    assert settings.worker_message_retry_max_seconds == 30


def test_settings_reject_retry_max_below_initial_delay() -> None:
    with pytest.raises(ValidationError):
        Settings(
            worker_message_retry_initial_seconds=10,
            worker_message_retry_max_seconds=5,
        )


def test_settings_validate_operation_retry_policy() -> None:
    settings = Settings(
        operation_retry_initial_seconds=2,
        operation_retry_max_seconds=30,
        operation_retry_max_elapsed_seconds=600,
    )

    assert settings.operation_retry_max_elapsed_seconds == 600
    with pytest.raises(ValidationError):
        Settings(
            operation_retry_initial_seconds=10,
            operation_retry_max_seconds=5,
        )


def test_settings_validate_bounded_retention_version_cleanup() -> None:
    settings = Settings(
        asset_retention_cleanup_version_page_size=50,
        asset_retention_cleanup_max_version_pages=20,
        asset_retention_cleanup_max_versions=500,
        asset_retention_cleanup_stable_empty_passes=2,
    )

    assert settings.asset_retention_cleanup_version_page_size == 50
    assert settings.asset_retention_cleanup_max_versions == 500
    with pytest.raises(
        ValidationError,
        match="page budget must cover stable empty scans",
    ):
        Settings(
            asset_retention_cleanup_max_version_pages=2,
            asset_retention_cleanup_stable_empty_passes=3,
        )


def test_production_requires_explicit_operation_executor_kinds() -> None:
    with pytest.raises(ValidationError, match="required operation kinds"):
        Settings(environment="production")

    settings = Settings(
        environment="production",
        worker_queues=["commercevision.maintenance"],
        worker_required_operation_kinds=[OperationKind.ASSET_DELETION],
        object_store_endpoint="https://minio.internal.example",
        object_store_presign_endpoint="https://assets.example",
        object_store_secret_key="production-object-store-secret",
        object_store_require_encryption=True,
    )

    assert settings.worker_required_operation_kinds == [OperationKind.ASSET_DELETION]

    workflow_only = Settings(
        environment="production",
        worker_queues=["commercevision.workflow"],
        object_store_endpoint="https://minio.internal.example",
        object_store_presign_endpoint="https://assets.example",
        object_store_secret_key="production-object-store-secret",
        object_store_require_encryption=True,
    )
    assert workflow_only.worker_required_operation_kinds == []
    assert workflow_only.worker_requires_object_storage is False


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://",
        "https://user:password@assets.example",
        "https://assets.example/prefix",
        "https://assets.example?credential=value",
        "https://assets.example#fragment",
    ],
)
def test_object_store_endpoints_must_be_credential_free_origins(endpoint: str) -> None:
    with pytest.raises(ValidationError, match="object-store endpoints"):
        Settings(object_store_endpoint=endpoint)


def test_legacy_object_store_bucket_remains_the_task_bucket_fallback() -> None:
    legacy = Settings(object_store_bucket="legacy-task-assets")
    overridden = Settings(
        object_store_bucket="legacy-task-assets",
        object_store_task_bucket="retained-task-assets",
    )

    assert legacy.object_store_task_bucket == "legacy-task-assets"
    assert overridden.object_store_task_bucket == "retained-task-assets"


def test_object_store_request_budget_must_fit_inside_finalize_lease() -> None:
    with pytest.raises(ValidationError, match="request timeout budget"):
        Settings(
            object_store_connect_timeout_seconds=5,
            object_store_read_timeout_seconds=5,
            upload_finalize_lease_seconds=30,
        )

    settings = Settings(
        object_store_connect_timeout_seconds=4,
        object_store_read_timeout_seconds=5,
        upload_finalize_lease_seconds=30,
    )

    assert settings.upload_finalize_lease_seconds == 30


def test_upload_cleanup_presign_grace_is_positive_and_bounded() -> None:
    assert Settings().upload_cleanup_presign_grace_seconds == 30
    with pytest.raises(ValidationError):
        Settings(upload_cleanup_presign_grace_seconds=0)
    with pytest.raises(ValidationError):
        Settings(upload_cleanup_presign_grace_seconds=301)


def test_upload_cleanup_retry_budgets_cover_their_full_windows() -> None:
    settings = Settings()

    assert settings.upload_cleanup_max_attempts == 600
    assert settings.upload_cleanup_reconcile_max_attempts == 80
    with pytest.raises(ValidationError, match="execution attempts do not cover"):
        Settings(upload_cleanup_max_attempts=5)
    with pytest.raises(ValidationError, match="reconciliation attempts do not cover"):
        Settings(upload_cleanup_reconcile_max_attempts=72)
    with pytest.raises(ValidationError, match="maximum delay must cover"):
        Settings(operation_reconciliation_max_seconds=60)
    with pytest.raises(ValidationError, match="elapsed budget must exceed"):
        Settings(operation_reconciliation_max_elapsed_seconds=259200)


def test_production_rejects_disabled_tls_verification_and_default_storage_secret() -> None:
    production = {
        "environment": "production",
        "worker_required_operation_kinds": [OperationKind.ASSET_VALIDATION],
        "object_store_endpoint": "https://minio.internal.example",
        "object_store_presign_endpoint": "https://assets.example",
        "object_store_require_encryption": True,
    }
    with pytest.raises(ValidationError, match="storage secret"):
        Settings(**production)
    with pytest.raises(ValidationError, match="TLS verification"):
        Settings(
            **production,
            object_store_secret_key="production-object-store-secret",
            object_store_tls_verify=False,
        )


def test_production_requires_distinct_internal_and_browser_storage_origins() -> None:
    with pytest.raises(ValidationError, match="distinct browser presign origin"):
        Settings(
            environment="production",
            worker_required_operation_kinds=[OperationKind.ASSET_VALIDATION],
            object_store_endpoint="https://assets.example",
            object_store_presign_endpoint="https://ASSETS.example",
            object_store_secret_key="production-object-store-secret",
            object_store_require_encryption=True,
        )


def test_production_oss_requires_virtual_hosted_addressing() -> None:
    production_oss = {
        "environment": "production",
        "worker_queues": ["commercevision.maintenance"],
        "worker_required_operation_kinds": [OperationKind.ASSET_DELETION],
        "object_store_backend": "oss",
        "object_store_credential_mode": "ecs_ram_role",
        "object_store_endpoint": "https://oss-cn-hangzhou-internal.aliyuncs.com",
        "object_store_presign_endpoint": "https://oss-cn-hangzhou.aliyuncs.com",
        "object_store_require_encryption": True,
    }

    with pytest.raises(ValidationError, match="virtual-hosted"):
        Settings(**production_oss, object_store_force_path_style=True)

    settings = Settings(**production_oss, object_store_force_path_style=False)
    assert settings.object_store_force_path_style is False


def test_production_oss_requires_renewable_workload_identity() -> None:
    production_oss = {
        "environment": "production",
        "worker_queues": ["commercevision.maintenance"],
        "worker_required_operation_kinds": [OperationKind.ASSET_DELETION],
        "object_store_backend": "oss",
        "object_store_endpoint": "https://oss-cn-hangzhou-internal.aliyuncs.com",
        "object_store_presign_endpoint": "https://oss-cn-hangzhou.aliyuncs.com",
        "object_store_force_path_style": False,
        "object_store_require_encryption": True,
    }

    with pytest.raises(ValidationError, match="renewable workload identity"):
        Settings(
            **production_oss,
            object_store_credential_mode="static",
            object_store_secret_key="production-object-store-secret",
        )

    ecs = Settings(
        **production_oss,
        object_store_credential_mode="ecs_ram_role",
    )
    assert ecs.object_store_credential_mode == "ecs_ram_role"


def test_production_object_storage_requires_distinct_retention_buckets() -> None:
    with pytest.raises(ValidationError, match="distinct physical buckets"):
        Settings(
            environment="production",
            worker_required_operation_kinds=[OperationKind.ASSET_VALIDATION],
            object_store_backend="oss",
            object_store_credential_mode="ecs_ram_role",
            object_store_endpoint="https://oss-cn-hangzhou-internal.aliyuncs.com",
            object_store_presign_endpoint="https://oss-cn-hangzhou.aliyuncs.com",
            object_store_force_path_style=False,
            object_store_require_encryption=True,
            object_store_task_bucket="shared-retained-assets",
            object_store_foundation_bucket="shared-retained-assets",
        )


def test_oss_oidc_workload_identity_configuration_is_atomic() -> None:
    oidc = {
        "object_store_backend": "oss",
        "object_store_credential_mode": "oidc_role_arn",
        "object_store_oidc_role_arn": "acs:ram::1234567890123456:role/commercevision",
        "object_store_oidc_provider_arn": (
            "acs:ram::1234567890123456:oidc-provider/commercevision"
        ),
        "object_store_oidc_token_file_path": "/var/run/secrets/aliyun/token",
    }

    settings = Settings(**oidc)
    assert settings.object_store_role_session_name == "commercevision-object-storage"

    for missing in (
        "object_store_oidc_role_arn",
        "object_store_oidc_provider_arn",
        "object_store_oidc_token_file_path",
    ):
        incomplete = {key: value for key, value in oidc.items() if key != missing}
        with pytest.raises(ValidationError, match="OIDC workload identity"):
            Settings(**incomplete)

    with pytest.raises(ValidationError, match="absolute"):
        Settings(
            **{
                **oidc,
                "object_store_oidc_token_file_path": "relative/token",
            }
        )


def test_production_oidc_requires_an_explicit_sts_endpoint() -> None:
    production_oidc = {
        "environment": "production",
        "worker_queues": ["commercevision.maintenance"],
        "worker_required_operation_kinds": [OperationKind.ASSET_DELETION],
        "object_store_backend": "oss",
        "object_store_credential_mode": "oidc_role_arn",
        "object_store_endpoint": "https://oss-cn-hangzhou-internal.aliyuncs.com",
        "object_store_presign_endpoint": "https://oss-cn-hangzhou.aliyuncs.com",
        "object_store_force_path_style": False,
        "object_store_require_encryption": True,
        "object_store_oidc_role_arn": "acs:ram::1234567890123456:role/commercevision",
        "object_store_oidc_provider_arn": (
            "acs:ram::1234567890123456:oidc-provider/commercevision"
        ),
        "object_store_oidc_token_file_path": "/var/run/secrets/aliyun/token",
    }
    with pytest.raises(ValidationError, match="STS endpoint"):
        Settings(**production_oidc)

    settings = Settings(
        **production_oidc,
        object_store_sts_endpoint="sts-vpc.cn-hangzhou.aliyuncs.com",
    )
    assert settings.object_store_sts_endpoint == "sts-vpc.cn-hangzhou.aliyuncs.com"


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://sts-vpc.cn-hangzhou.aliyuncs.com",
        "user@sts-vpc.cn-hangzhou.aliyuncs.com",
        "sts-vpc.cn-hangzhou.aliyuncs.com/path",
        "sts_vpc.cn-hangzhou.aliyuncs.com",
        "杭州.aliyuncs.com",
        "127.0.0.1",
    ],
)
def test_oss_sts_endpoint_is_a_credential_free_dns_hostname(endpoint: str) -> None:
    with pytest.raises(ValidationError, match="STS endpoint"):
        Settings(
            object_store_backend="oss",
            object_store_credential_mode="oidc_role_arn",
            object_store_oidc_role_arn="acs:ram::1234567890123456:role/commercevision",
            object_store_oidc_provider_arn=(
                "acs:ram::1234567890123456:oidc-provider/commercevision"
            ),
            object_store_oidc_token_file_path="/var/run/secrets/aliyun/token",
            object_store_sts_endpoint=endpoint,
        )


def test_sts_endpoint_is_rejected_outside_oidc_mode() -> None:
    with pytest.raises(ValidationError, match="requires oidc_role_arn"):
        Settings(
            object_store_backend="oss",
            object_store_credential_mode="ecs_ram_role",
            object_store_sts_endpoint="sts-vpc.cn-hangzhou.aliyuncs.com",
        )


def test_blank_optional_object_storage_environment_is_absent() -> None:
    settings = Settings(
        object_store_session_token="",
        object_store_ram_role_name=" ",
        object_store_sts_endpoint="",
    )

    assert settings.object_store_session_token is None
    assert settings.object_store_ram_role_name is None
    assert settings.object_store_sts_endpoint is None


def test_required_operation_executor_kinds_must_be_unique() -> None:
    with pytest.raises(ValidationError, match="unique"):
        Settings(
            worker_required_operation_kinds=[
                OperationKind.ASSET_INDEXING,
                OperationKind.ASSET_INDEXING,
            ]
        )
