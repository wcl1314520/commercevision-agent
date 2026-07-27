from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.integration


@pytest.fixture
def rights_migration_database(integration_settings, monkeypatch):
    source_url = make_url(integration_settings.mysql_dsn)
    database_name = f"commercevision_ticket06_migration_{uuid.uuid4().hex[:8]}"
    admin_engine = create_engine(source_url.set(database="mysql"))
    test_url = source_url.set(database=database_name)
    engine = create_engine(test_url)
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    monkeypatch.setenv(
        "CV_MIGRATION_MYSQL_DSN",
        test_url.render_as_string(hide_password=False),
    )
    try:
        with admin_engine.begin() as connection:
            connection.execute(
                text(
                    f"CREATE DATABASE `{database_name}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
                )
            )
        command.upgrade(config, "head")
        yield config, engine
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS `{database_name}`"))
        admin_engine.dispose()


def test_rights_schema_is_normalized_indexed_append_only_and_microsecond_precise(
    rights_migration_database,
) -> None:
    config, engine = rights_migration_database
    inspector = inspect(engine)
    assert {
        "rights_records",
        "rights_record_uses",
        "rights_record_providers",
    }.issubset(inspector.get_table_names())

    rights_columns = {column["name"]: column for column in inspector.get_columns("rights_records")}
    for timestamp in ("valid_from", "valid_until", "created_at"):
        assert isinstance(rights_columns[timestamp]["type"], DATETIME)
        assert rights_columns[timestamp]["type"].fsp == 6
    assert isinstance(rights_columns["permissions_sealed_at"]["type"], DATETIME)
    assert rights_columns["permissions_sealed_at"]["type"].fsp == 6
    assert rights_columns["permissions_sealed_at"]["nullable"] is True
    assert rights_columns["valid_until"]["nullable"] is True
    assert "current_rights_record_id" in {
        column["name"] for column in inspector.get_columns("assets")
    }
    asset_foreign_keys = {
        foreign_key["name"]: tuple(foreign_key["constrained_columns"])
        for foreign_key in inspector.get_foreign_keys("assets")
    }
    assert asset_foreign_keys["fk_assets_current_rights_record"] == (
        "workspace_id",
        "current_rights_record_id",
        "id",
    )
    rights_foreign_keys = {
        foreign_key["name"]: (
            tuple(foreign_key["constrained_columns"]),
            tuple(foreign_key["referred_columns"]),
        )
        for foreign_key in inspector.get_foreign_keys("rights_records")
    }
    assert rights_foreign_keys["fk_rights_records_workspace_asset_version"] == (
        ("workspace_id", "asset_version_id", "asset_id"),
        ("workspace_id", "id", "asset_id"),
    )
    rights_indexes = {
        index["name"]: tuple(index["column_names"])
        for index in inspector.get_indexes("rights_records")
    }
    assert rights_indexes["ix_rights_records_current_expiry"] == (
        "perpetual",
        "valid_until",
        "asset_id",
        "id",
    )
    assert rights_indexes["ix_rights_records_activation"] == (
        "decision",
        "valid_from",
        "valid_until",
        "asset_id",
        "id",
    )

    use_indexes = {
        index["name"]: tuple(index["column_names"])
        for index in inspector.get_indexes("rights_record_uses")
    }
    provider_indexes = {
        index["name"]: tuple(index["column_names"])
        for index in inspector.get_indexes("rights_record_providers")
    }
    assert use_indexes["ix_rights_record_uses_authorization"] == (
        "workspace_id",
        "allowed_use",
        "asset_id",
        "rights_record_id",
    )
    assert provider_indexes["ix_rights_record_providers_authorization"] == (
        "workspace_id",
        "allowed_provider",
        "asset_id",
        "rights_record_id",
    )

    with engine.connect() as connection:
        triggers = {row["Trigger"] for row in connection.execute(text("SHOW TRIGGERS")).mappings()}
    assert {
        "trg_rights_records_no_update",
        "trg_rights_records_no_delete",
        "trg_rights_record_uses_no_update",
        "trg_rights_record_uses_no_delete",
        "trg_rights_record_uses_sealed_insert",
        "trg_rights_record_providers_no_update",
        "trg_rights_record_providers_no_delete",
        "trg_rights_record_providers_sealed_insert",
    }.issubset(triggers)

    now = datetime(2026, 7, 27, 10, 0, 0, 123456, tzinfo=UTC).replace(tzinfo=None)
    with engine.begin() as connection:
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        connection.execute(
            text(
                "INSERT INTO rights_records "
                "(id, workspace_id, asset_id, asset_version_id, version_number, "
                "decision, owner_reference, source, license_reference, "
                "derivative_allowed, public_demo_allowed, evidence_reference, "
                "terms_sha256, valid_from, valid_until, perpetual, "
                "supersedes_record_id, created_by, created_at, permissions_sealed_at) VALUES "
                "('019c1000-0000-7000-8000-000000000001', 'rights-migration', "
                "'019c1000-0000-7000-8000-000000000002', NULL, 1, 'GRANT', "
                "'owner', 'source', 'license', 0, 0, 'evidence://1', :sha, "
                ":valid_from, NULL, 1, NULL, 'actor', :created_at, NULL)"
            ),
            {"sha": "a" * 64, "valid_from": now, "created_at": now},
        )
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
        connection.execute(
            text(
                "INSERT INTO rights_record_uses "
                "(workspace_id, asset_id, rights_record_id, allowed_use, created_at) VALUES "
                "('rights-migration', '019c1000-0000-7000-8000-000000000002', "
                "'019c1000-0000-7000-8000-000000000001', 'RETRIEVAL', :created_at)"
            ),
            {"created_at": now},
        )
        connection.execute(
            text(
                "INSERT INTO rights_record_providers "
                "(workspace_id, asset_id, rights_record_id, allowed_provider, created_at) VALUES "
                "('rights-migration', '019c1000-0000-7000-8000-000000000002', "
                "'019c1000-0000-7000-8000-000000000001', 'milvus', :created_at)"
            ),
            {"created_at": now},
        )
        connection.execute(
            text(
                "UPDATE rights_records SET permissions_sealed_at = :sealed_at "
                "WHERE id = '019c1000-0000-7000-8000-000000000001'"
            ),
            {"sealed_at": now},
        )

    with engine.begin() as connection, pytest.raises(DBAPIError):
        connection.execute(
            text(
                "UPDATE rights_records SET source = 'changed' "
                "WHERE id = '019c1000-0000-7000-8000-000000000001'"
            )
        )
    with engine.begin() as connection, pytest.raises(DBAPIError):
        connection.execute(
            text("DELETE FROM rights_records WHERE id = '019c1000-0000-7000-8000-000000000001'")
        )
    for table_name, permission_column, value in (
        ("rights_record_uses", "allowed_use", "VISION_ANALYSIS"),
        ("rights_record_providers", "allowed_provider", "qwen-vl"),
    ):
        with engine.begin() as connection, pytest.raises(DBAPIError):
            connection.execute(
                text(
                    f"INSERT INTO {table_name} "
                    f"(workspace_id, asset_id, rights_record_id, {permission_column}, created_at) "
                    "VALUES ('rights-migration', "
                    "'019c1000-0000-7000-8000-000000000002', "
                    "'019c1000-0000-7000-8000-000000000001', :value, :created_at)"
                ),
                {"value": value, "created_at": now},
            )
    with pytest.raises(RuntimeError, match="immutable Rights Record history"):
        command.downgrade(config, "a7c4e2d9b831")

    with engine.connect() as connection:
        persisted = (
            connection.execute(
                text(
                    "SELECT source, MICROSECOND(valid_from) AS micros "
                    "FROM rights_records "
                    "WHERE id = '019c1000-0000-7000-8000-000000000001'"
                )
            )
            .mappings()
            .one()
        )
    assert persisted == {"source": "source", "micros": 123456}


def test_rights_schema_downgrades_when_immutable_history_is_empty(
    rights_migration_database,
) -> None:
    config, engine = rights_migration_database

    command.downgrade(config, "a7c4e2d9b831")

    inspector = inspect(engine)
    assert {
        "rights_records",
        "rights_record_uses",
        "rights_record_providers",
    }.isdisjoint(inspector.get_table_names())
    assert "current_rights_record_id" not in {
        column["name"] for column in inspector.get_columns("assets")
    }
    assert "uq_asset_versions_workspace_id_asset" not in {
        constraint["name"] for constraint in inspector.get_unique_constraints("asset_versions")
    }
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "a7c4e2d9b831"
        )
