from __future__ import annotations

from types import SimpleNamespace

import commercevision_persistence.migration_identity as migration_identity
import pytest

resolve_migration_mysql_url = migration_identity.resolve_migration_mysql_url


def test_migration_identity_takes_precedence_over_runtime_identity(monkeypatch) -> None:
    monkeypatch.setenv("CV_ENVIRONMENT", "production")
    monkeypatch.setenv(
        "CV_MIGRATION_MYSQL_DSN",
        "mysql+pymysql://migrator:secret@db.example/commercevision",
    )
    monkeypatch.setenv(
        "CV_MYSQL_DSN",
        "mysql+pymysql://runtime:secret@db.example/runtime_must_not_be_used",
    )

    url = resolve_migration_mysql_url()

    assert url.username == "migrator"
    assert url.database == "commercevision"


@pytest.mark.parametrize("environment", ["staging", "demo", "production"])
def test_deployed_migration_requires_a_dedicated_identity(
    monkeypatch,
    environment: str,
) -> None:
    monkeypatch.setenv("CV_ENVIRONMENT", environment)
    monkeypatch.delenv("CV_MIGRATION_MYSQL_DSN", raising=False)
    monkeypatch.setenv(
        "CV_MYSQL_DSN",
        "mysql+pymysql://runtime:secret@db.example/commercevision",
    )
    monkeypatch.setenv("CV_WORKER_QUEUES", '["commercevision.maintenance"]')
    monkeypatch.setenv("CV_WORKER_REQUIRED_OPERATION_KINDS", '["ASSET_DELETION"]')
    monkeypatch.setenv("CV_OBJECT_STORE_ENDPOINT", "https://minio.internal.example")
    monkeypatch.setenv("CV_OBJECT_STORE_PRESIGN_ENDPOINT", "https://assets.example")
    monkeypatch.setenv("CV_OBJECT_STORE_SECRET_KEY", "production-object-store-secret")
    monkeypatch.setenv("CV_OBJECT_STORE_REQUIRE_ENCRYPTION", "true")

    with pytest.raises(
        RuntimeError,
        match="CV_MIGRATION_MYSQL_DSN is required",
    ):
        resolve_migration_mysql_url()


def test_deployed_environment_from_validated_settings_cannot_bypass_migration_identity(
    monkeypatch,
) -> None:
    monkeypatch.delenv("CV_ENVIRONMENT", raising=False)
    monkeypatch.delenv("CV_MIGRATION_MYSQL_DSN", raising=False)
    monkeypatch.setattr(
        migration_identity,
        "load_settings",
        lambda _service_name: SimpleNamespace(
            environment="production",
            mysql_dsn="mysql+pymysql://runtime:secret@db.example/commercevision",
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="CV_MIGRATION_MYSQL_DSN is required",
    ):
        resolve_migration_mysql_url()


def test_local_and_ci_migrations_can_use_the_explicit_runtime_override(monkeypatch) -> None:
    monkeypatch.setenv("CV_ENVIRONMENT", "ci")
    monkeypatch.delenv("CV_MIGRATION_MYSQL_DSN", raising=False)
    monkeypatch.setenv(
        "CV_MYSQL_DSN",
        "mysql+aiomysql://root:secret@127.0.0.1/commercevision_test",
    )

    url = resolve_migration_mysql_url()

    assert url.drivername == "mysql+pymysql"
    assert url.username == "root"
    assert url.database == "commercevision_test"


@pytest.mark.parametrize("value", ["", " ", " mysql+pymysql://root:secret@db/schema"])
def test_migration_identity_rejects_blank_or_noncanonical_values(
    monkeypatch,
    value: str,
) -> None:
    monkeypatch.setenv("CV_MIGRATION_MYSQL_DSN", value)

    with pytest.raises(RuntimeError, match="must be a non-empty canonical DSN"):
        resolve_migration_mysql_url()
