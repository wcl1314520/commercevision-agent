import pytest
from commercevision_contracts import Settings
from commercevision_contracts.config import load_settings
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


def test_settings_reject_unknown_mcp_transport() -> None:
    with pytest.raises(ValidationError):
        Settings(mcp_transport="websocket")
