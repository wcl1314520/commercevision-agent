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
def creative_plan_migration_database(
    integration_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[Engine, Config]]:
    source_url = make_url(integration_settings.mysql_dsn)
    database_name = f"commercevision_phase3_plan_{uuid.uuid4().hex[:8]}"
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


def test_schema_is_tenant_first_binary_exact_microsecond_and_reversible_when_empty(
    creative_plan_migration_database: tuple[Engine, Config],
) -> None:
    engine, config = creative_plan_migration_database
    inspector = inspect(engine)
    assert {"creative_plans", "creative_plan_versions"}.issubset(inspector.get_table_names())
    assert tuple(inspector.get_pk_constraint("creative_plans")["constrained_columns"]) == (
        "workspace_id",
        "id",
    )
    assert tuple(inspector.get_pk_constraint("creative_plan_versions")["constrained_columns"]) == (
        "workspace_id",
        "id",
    )
    version_unique = {
        item["name"]: tuple(item["column_names"])
        for item in inspector.get_unique_constraints("creative_plan_versions")
    }
    assert version_unique["uq_creative_plan_versions_logical"] == (
        "workspace_id",
        "workflow_id",
        "creative_plan_id",
        "version_number",
    )
    head_unique = {
        item["name"]: tuple(item["column_names"])
        for item in inspector.get_unique_constraints("creative_plans")
    }
    assert head_unique["uq_creative_plans_workspace_workflow_id"] == (
        "workspace_id",
        "workflow_id",
        "id",
    )
    for table_name, names in (
        ("creative_plans", ("retain_until", "created_at", "updated_at")),
        ("creative_plan_versions", ("retain_until", "created_at")),
    ):
        columns = {column["name"]: column for column in inspector.get_columns(table_name)}
        for name in names:
            assert isinstance(columns[name]["type"], DATETIME)
            assert columns[name]["type"].fsp == 6
    with engine.connect() as connection:
        collations = {
            row["TABLE_NAME"]: row["COLLATION_NAME"]
            for row in connection.execute(
                text(
                    "SELECT TABLE_NAME, COLLATION_NAME FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND COLUMN_NAME = 'workspace_id' "
                    "AND TABLE_NAME IN ('creative_plans', 'creative_plan_versions')"
                )
            ).mappings()
        }
        triggers = {row["Trigger"] for row in connection.execute(text("SHOW TRIGGERS")).mappings()}
    assert collations == {
        "creative_plans": "utf8mb4_0900_bin",
        "creative_plan_versions": "utf8mb4_0900_bin",
    }
    assert {
        "trg_creative_plan_versions_immutable",
        "trg_creative_plan_versions_retain",
        "trg_creative_plans_head_guard",
        "trg_creative_plans_retain",
    }.issubset(triggers)

    command.downgrade(config, "b7e3c9d5a102")
    assert not {"creative_plans", "creative_plan_versions"}.intersection(
        inspect(engine).get_table_names()
    )
    command.upgrade(config, "head")
    command.check(config)


def _insert_workflow_and_plan(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO workflows (
                  id, workspace_id, created_by, workflow_type, status, retention_status,
                  current_node, version, input_json, result_json, expires_at,
                  cancellation_requested_at, created_at, updated_at
                ) VALUES (
                  '019b0000-0000-7000-8000-000000000720', 'planning-domain',
                  'operator', 'creative-planning', 'PLANNING', 'ACTIVE', 'create_plan', 7,
                  JSON_OBJECT(), NULL, UTC_TIMESTAMP(6) + INTERVAL 30 DAY,
                  NULL, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6)
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO creative_plan_versions (
                  id, workspace_id, workflow_id, creative_plan_id, version_number,
                  supersedes_version_id, source, payload_json, payload_sha256,
                  product_brief_id, product_brief_version, product_brief_sha256,
                  brand_profile_id, brand_profile_version, brand_profile_sha256,
                  retrieval_run_id, retrieval_citation_ids_json, context_policy_version,
                  context_sha256, prompt_id, prompt_revision, prompt_sha256,
                  actor_id, revision_reason, retain_until, created_at
                ) VALUES (
                  '019b0000-0000-7000-8000-000000000721', 'planning-domain',
                  '019b0000-0000-7000-8000-000000000720',
                  '019b0000-0000-7000-8000-000000000722', 1, NULL, 'AGENT',
                  JSON_OBJECT('schema_version', 'creative-plan.v1', 'directions', JSON_ARRAY()),
                  REPEAT('1', 64), '019b0000-0000-7000-8000-000000000723', 1,
                  REPEAT('2', 64), NULL, NULL, NULL,
                  '019b0000-0000-7000-8000-000000000724', JSON_ARRAY(),
                  'planning-context-v1', REPEAT('3', 64), 'creative-planner', '1.0.0',
                  REPEAT('4', 64), 'fixture-planner', NULL,
                  UTC_TIMESTAMP(6) + INTERVAL 30 DAY, UTC_TIMESTAMP(6)
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO creative_plans (
                  id, workspace_id, workflow_id, current_version_id,
                  current_version_number, version, retain_until, created_at, updated_at
                ) VALUES (
                  '019b0000-0000-7000-8000-000000000722', 'planning-domain',
                  '019b0000-0000-7000-8000-000000000720',
                  '019b0000-0000-7000-8000-000000000721', 1, 1,
                  UTC_TIMESTAMP(6) + INTERVAL 30 DAY, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6)
                )
                """
            )
        )


def test_immutable_facts_and_active_retention_fail_closed(
    creative_plan_migration_database: tuple[Engine, Config],
) -> None:
    engine, config = creative_plan_migration_database
    _insert_workflow_and_plan(engine)

    with pytest.raises(DBAPIError), engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE creative_plan_versions SET actor_id = 'tampered' "
                "WHERE id = '019b0000-0000-7000-8000-000000000721'"
            )
        )
    with pytest.raises(DBAPIError), engine.begin() as connection:
        connection.execute(
            text("DELETE FROM creative_plans WHERE id = '019b0000-0000-7000-8000-000000000722'")
        )
    with pytest.raises(RuntimeError, match="immutable Creative Plan facts"):
        command.downgrade(config, "b7e3c9d5a102")
