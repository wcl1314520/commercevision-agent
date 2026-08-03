from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.integration


@pytest.fixture
def retention_migration_database(integration_settings, monkeypatch):
    source_url = make_url(integration_settings.mysql_dsn)
    database_name = f"commercevision_ticket13_migration_{uuid.uuid4().hex[:8]}"
    admin_engine = create_engine(source_url.set(database="mysql"))
    test_url = source_url.set(database=database_name)
    engine = create_engine(test_url)
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    monkeypatch.setenv("CV_MIGRATION_MYSQL_DSN", test_url.render_as_string(hide_password=False))
    try:
        with admin_engine.begin() as connection:
            connection.execute(
                text(
                    f"CREATE DATABASE `{database_name}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
                )
            )
        yield config, engine
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS `{database_name}`"))
        admin_engine.dispose()


def test_retention_migration_creates_fenced_append_only_deletion_facts(
    retention_migration_database,
) -> None:
    config, engine = retention_migration_database
    command.upgrade(config, "head")

    inspector = inspect(engine)
    assert {
        "asset_deletion_tombstones",
        "asset_deletion_progress",
        "provider_artifact_deletion_progress",
    }.issubset(inspector.get_table_names())
    asset_columns = {column["name"]: column for column in inspector.get_columns("assets")}
    assert asset_columns["deletion_generation"]["nullable"] is False
    for name in ("deletion_requested_at", "deletion_completed_at"):
        assert isinstance(asset_columns[name]["type"], DATETIME)
        assert asset_columns[name]["type"].fsp == 6
    with engine.connect() as connection:
        triggers = set(
            connection.execute(
                text(
                    "SELECT TRIGGER_NAME FROM information_schema.TRIGGERS "
                    "WHERE TRIGGER_SCHEMA = DATABASE()"
                )
            ).scalars()
        )
    for table_name in (
        "asset_deletion_tombstones",
        "asset_deletion_progress",
        "provider_artifact_deletion_progress",
    ):
        assert f"trg_{table_name}_no_update" in triggers
        assert f"trg_{table_name}_no_delete" in triggers
    assert "trg_product_brief_fields_no_delete" in triggers
    assert "trg_product_brief_evidence_no_delete" in triggers
    command.check(config)
