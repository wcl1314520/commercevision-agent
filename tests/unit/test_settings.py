import pytest
from commercevision_contracts import Settings
from commercevision_contracts.config import load_settings
from commercevision_domain import OperationKind
from pydantic import ValidationError


def test_settings_reject_unknown_environment() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="invalid")


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
