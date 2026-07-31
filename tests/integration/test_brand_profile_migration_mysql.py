from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.integration

_PRE_BRAND_PROFILE_REVISION = "a4c8e7f3b219"


@pytest.fixture
def brand_profile_migration_database(integration_settings, monkeypatch):
    source_url = make_url(integration_settings.mysql_dsn)
    database_name = f"commercevision_ticket08_migration_{uuid.uuid4().hex[:8]}"
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


def test_brand_profile_schema_enforces_ownership_heads_and_append_only_history(
    brand_profile_migration_database,
) -> None:
    config, engine = brand_profile_migration_database
    inspector = inspect(engine)
    assert {
        "brand_profiles",
        "brand_profile_versions",
        "brand_profile_members",
    }.issubset(inspector.get_table_names())

    profile_columns = {column["name"]: column for column in inspector.get_columns("brand_profiles")}
    version_columns = {
        column["name"]: column for column in inspector.get_columns("brand_profile_versions")
    }
    for columns, names in (
        (profile_columns, ("stale_at", "created_at", "updated_at")),
        (version_columns, ("published_at",)),
    ):
        for name in names:
            assert isinstance(columns[name]["type"], DATETIME)
            assert columns[name]["type"].fsp == 6

    profile_unique = {
        constraint["name"]: tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("brand_profiles")
    }
    assert profile_unique["uq_brand_profiles_workspace_identity"] == (
        "workspace_id",
        "brand",
        "profile_key",
    )
    assert profile_unique["uq_brand_profiles_workspace_id"] == ("workspace_id", "id")
    profile_indexes = {
        index["name"]: tuple(index["column_names"])
        for index in inspector.get_indexes("brand_profiles")
    }
    assert profile_indexes["ix_brand_profiles_workspace_created"] == (
        "workspace_id",
        "created_at",
        "id",
    )
    assert profile_indexes["ix_brand_profiles_workspace_brand_created"] == (
        "workspace_id",
        "brand",
        "created_at",
        "id",
    )

    profile_foreign_keys = {
        foreign_key["name"]: (
            tuple(foreign_key["constrained_columns"]),
            tuple(foreign_key["referred_columns"]),
            foreign_key["options"].get("ondelete"),
        )
        for foreign_key in inspector.get_foreign_keys("brand_profiles")
    }
    assert profile_foreign_keys["fk_brand_profiles_current_version"] == (
        ("workspace_id", "id", "current_version_id", "current_version_number"),
        ("workspace_id", "profile_id", "id", "version_number"),
        "RESTRICT",
    )

    member_foreign_keys = {
        foreign_key["name"]: (
            tuple(foreign_key["constrained_columns"]),
            tuple(foreign_key["referred_columns"]),
            foreign_key["options"].get("ondelete"),
        )
        for foreign_key in inspector.get_foreign_keys("brand_profile_members")
    }
    assert member_foreign_keys["fk_brand_profile_members_asset_version"] == (
        ("workspace_id", "asset_version_id", "asset_id"),
        ("workspace_id", "id", "asset_id"),
        "RESTRICT",
    )
    assert member_foreign_keys["fk_brand_profile_members_rights_record"] == (
        ("workspace_id", "rights_record_id", "asset_id", "rights_record_version"),
        ("workspace_id", "id", "asset_id", "version_number"),
        "RESTRICT",
    )
    assert member_foreign_keys["fk_brand_profile_members_profile_version"] == (
        (
            "workspace_id",
            "profile_id",
            "profile_version_id",
            "profile_version_number",
        ),
        ("workspace_id", "profile_id", "id", "version_number"),
        "RESTRICT",
    )

    rights_unique = {
        constraint["name"]: tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("rights_records")
    }
    assert rights_unique["uq_rights_records_exact_version"] == (
        "workspace_id",
        "id",
        "asset_id",
        "version_number",
    )

    member_indexes = {
        index["name"]: tuple(index["column_names"])
        for index in inspector.get_indexes("brand_profile_members")
    }
    assert member_indexes["ix_brand_profile_members_current_invalidation"] == (
        "workspace_id",
        "asset_id",
        "profile_id",
        "profile_version_id",
    )

    with engine.connect() as connection:
        triggers = {row["Trigger"] for row in connection.execute(text("SHOW TRIGGERS")).mappings()}
    assert {
        "trg_brand_profiles_no_delete",
        "trg_brand_profile_versions_no_update",
        "trg_brand_profile_versions_no_delete",
        "trg_brand_profile_members_no_update",
        "trg_brand_profile_members_no_delete",
    }.issubset(triggers)

    now = datetime(2026, 7, 30, 8, 0, 0, 123456)
    with engine.begin() as connection, pytest.raises(DBAPIError):
        connection.execute(
            text(
                "INSERT INTO brand_profiles "
                "(id, workspace_id, brand, profile_key, state, draft_json, "
                "draft_sha256, current_version_id, current_version_number, version, "
                "stale_at, created_by, created_at, updated_by, updated_at) VALUES "
                "('019c2000-0000-7000-8000-000000000099', 'migration-workspace', "
                "'Acme', 'invalid-active-head', 'ACTIVE', "
                "JSON_OBJECT('schema_version', 'brand-profile.v1'), :sha, "
                "NULL, 0, 1, NULL, 'actor', :now, 'actor', :now)"
            ),
            {"sha": "f" * 64, "now": now},
        )

    profile_id = "019c2000-0000-7000-8000-000000000001"
    version_id = "019c2000-0000-7000-8000-000000000002"
    with engine.begin() as connection:
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        connection.execute(
            text(
                "INSERT INTO brand_profiles "
                "(id, workspace_id, brand, profile_key, state, draft_json, "
                "draft_sha256, current_version_id, current_version_number, version, "
                "stale_at, created_by, created_at, updated_by, updated_at) VALUES "
                "(:id, 'migration-workspace', 'Acme', 'default', 'ACTIVE', "
                "JSON_OBJECT('schema_version', 'brand-profile.v1'), :sha, "
                ":version_id, 1, 2, NULL, 'actor', :now, 'actor', :now)"
            ),
            {"id": profile_id, "version_id": version_id, "sha": "a" * 64, "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO brand_profile_versions "
                "(id, workspace_id, profile_id, version_number, content_json, "
                "content_sha256, purpose, provider, requires_derivative, "
                "published_by, published_at) VALUES "
                "(:version_id, 'migration-workspace', :profile_id, 1, "
                "JSON_OBJECT('schema_version', 'brand-profile.v1'), :sha, "
                "'RETRIEVAL', 'milvus', 0, 'actor', :now)"
            ),
            {
                "version_id": version_id,
                "profile_id": profile_id,
                "sha": "b" * 64,
                "now": now,
            },
        )
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 1"))

    with engine.begin() as connection, pytest.raises(DBAPIError):
        connection.execute(
            text("UPDATE brand_profile_versions SET provider = 'changed' WHERE id = :version_id"),
            {"version_id": version_id},
        )
    with engine.begin() as connection, pytest.raises(DBAPIError):
        connection.execute(
            text("DELETE FROM brand_profiles WHERE id = :profile_id"),
            {"profile_id": profile_id},
        )
    with pytest.raises(RuntimeError, match="Brand Profile history"):
        command.downgrade(config, _PRE_BRAND_PROFILE_REVISION)

    with engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT provider, MICROSECOND(published_at) AS micros "
                    "FROM brand_profile_versions WHERE id = :version_id"
                ),
                {"version_id": version_id},
            )
            .mappings()
            .one()
        )
    assert row == {"provider": "milvus", "micros": 123456}


def test_brand_profile_schema_downgrades_and_reupgrades_when_empty(
    brand_profile_migration_database,
) -> None:
    config, engine = brand_profile_migration_database

    command.downgrade(config, _PRE_BRAND_PROFILE_REVISION)
    inspector = inspect(engine)
    assert {
        "brand_profiles",
        "brand_profile_versions",
        "brand_profile_members",
    }.isdisjoint(inspector.get_table_names())
    assert "uq_rights_records_exact_version" not in {
        constraint["name"] for constraint in inspector.get_unique_constraints("rights_records")
    }

    command.upgrade(config, "head")
    assert {
        "brand_profiles",
        "brand_profile_versions",
        "brand_profile_members",
    }.issubset(inspect(engine).get_table_names())
