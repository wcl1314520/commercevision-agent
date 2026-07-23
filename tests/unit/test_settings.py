import pytest
from commercevision_contracts import Settings
from commercevision_contracts.config import load_settings
from commercevision_domain import OperationKind
from pydantic import ValidationError


def test_settings_reject_unknown_environment() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="invalid")


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

    assert settings.object_store_secret_key == "from-secret-file"


def test_environment_overrides_secret_file(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CV_SECRETS_DIR", str(tmp_path))
    monkeypatch.setenv("CV_OBJECT_STORE_SECRET_KEY", "from-environment")
    (tmp_path / "CV_OBJECT_STORE_SECRET_KEY").write_text("from-secret-file", encoding="utf-8")

    settings = Settings()

    assert settings.object_store_secret_key == "from-environment"


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


def test_production_requires_explicit_operation_executor_kinds() -> None:
    with pytest.raises(ValidationError, match="required operation kinds"):
        Settings(environment="production")

    settings = Settings(
        environment="production",
        worker_required_operation_kinds=[OperationKind.ASSET_VALIDATION],
    )

    assert settings.worker_required_operation_kinds == [OperationKind.ASSET_VALIDATION]


def test_required_operation_executor_kinds_must_be_unique() -> None:
    with pytest.raises(ValidationError, match="unique"):
        Settings(
            worker_required_operation_kinds=[
                OperationKind.ASSET_INDEXING,
                OperationKind.ASSET_INDEXING,
            ]
        )
