from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from commercevision_contracts import Settings
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.integration


@pytest.fixture
def planning_context_migration_database(
    integration_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[Engine, Config]]:
    source_url = make_url(integration_settings.mysql_dsn)
    database_name = f"commercevision_phase3_context_{uuid.uuid4().hex[:8]}"
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
        command.upgrade(config, "head")
        yield engine, config
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS `{database_name}`"))
        admin_engine.dispose()


def _insert_workflow(connection) -> None:
    connection.execute(
        text(
            """
            INSERT INTO workflows (
              id, workspace_id, created_by, workflow_type, status, retention_status,
              current_node, version, input_json, result_json, expires_at,
              cancellation_requested_at, created_at, updated_at
            ) VALUES (
              :id, 'planning-domain', 'fixture', 'creative-planning', 'RUNNING', 'ACTIVE',
              'planner', 1, JSON_OBJECT(), NULL,
              UTC_TIMESTAMP(6) + INTERVAL 30 DAY, NULL, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6)
            )
            """
        ),
        {"id": "019b0000-0000-7000-8000-000000000601"},
    )


def _insert_snapshot(connection, *, context_sha256: str, expired: bool = False) -> None:
    retention = (
        "UTC_TIMESTAMP(6) - INTERVAL 1 SECOND"
        if expired
        else ("UTC_TIMESTAMP(6) + INTERVAL 30 DAY")
    )
    connection.execute(
        text(
            f"""
            INSERT INTO planning_context_snapshots (
              workspace_id, workflow_id, context_sha256, schema_version, policy_version,
              storage_sha256, snapshot_json, source_count, retain_until, created_at
            ) VALUES (
              'planning-domain', '019b0000-0000-7000-8000-000000000601', :hash,
              'planning-context.v1', 'planning-context-v1', :storage_hash,
              JSON_OBJECT('canonical', JSON_OBJECT()),
              1, {retention}, UTC_TIMESTAMP(6)
            )
            """
        ),
        {"hash": context_sha256, "storage_hash": "f" * 64},
    )


def test_schema_is_tenant_first_microsecond_precise_and_immutable_until_retention(
    planning_context_migration_database: tuple[Engine, Config],
) -> None:
    engine, _ = planning_context_migration_database
    inspector = inspect(engine)
    assert "planning_context_snapshots" in inspector.get_table_names()
    primary_key = inspector.get_pk_constraint("planning_context_snapshots")
    assert tuple(primary_key["constrained_columns"]) == (
        "workspace_id",
        "workflow_id",
        "context_sha256",
    )
    columns = {
        column["name"]: column for column in inspector.get_columns("planning_context_snapshots")
    }
    for name in ("retain_until", "created_at"):
        assert isinstance(columns[name]["type"], DATETIME)
        assert columns[name]["type"].fsp == 6
    with engine.connect() as connection:
        triggers = {row["Trigger"] for row in connection.execute(text("SHOW TRIGGERS")).mappings()}
    assert {
        "trg_planning_context_snapshots_immutable",
        "trg_planning_context_snapshots_retain",
    }.issubset(triggers)

    with engine.begin() as connection:
        _insert_workflow(connection)
        _insert_snapshot(connection, context_sha256="1" * 64)
    with pytest.raises(DBAPIError), engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE planning_context_snapshots SET policy_version = 'tampered' "
                "WHERE workspace_id = 'planning-domain' AND context_sha256 = :hash"
            ),
            {"hash": "1" * 64},
        )
    with pytest.raises(DBAPIError), engine.begin() as connection:
        connection.execute(
            text(
                "DELETE FROM planning_context_snapshots "
                "WHERE workspace_id = 'planning-domain' AND context_sha256 = :hash"
            ),
            {"hash": "1" * 64},
        )
    with engine.begin() as connection:
        _insert_snapshot(connection, context_sha256="2" * 64, expired=True)
        connection.execute(
            text(
                "DELETE FROM planning_context_snapshots "
                "WHERE workspace_id = 'planning-domain' AND context_sha256 = :hash"
            ),
            {"hash": "2" * 64},
        )


def test_downgrade_refuses_to_discard_retained_context_facts(
    planning_context_migration_database: tuple[Engine, Config],
) -> None:
    engine, config = planning_context_migration_database
    with engine.begin() as connection:
        _insert_workflow(connection)
        _insert_snapshot(connection, context_sha256="3" * 64)

    with pytest.raises(RuntimeError, match="immutable Planning Context facts"):
        command.downgrade(config, "a9d2f6c4e801")
