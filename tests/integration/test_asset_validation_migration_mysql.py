from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
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
def validation_migration_database(integration_settings, monkeypatch):
    source_url = make_url(integration_settings.mysql_dsn)
    database_name = f"commercevision_ticket05_migration_{uuid.uuid4().hex[:8]}"
    admin_engine = create_engine(source_url.set(database="mysql"))
    test_url = source_url.set(database=database_name)
    engine = create_engine(test_url)
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    monkeypatch.setenv(
        "CV_MIGRATION_MYSQL_DSN",
        test_url.render_as_string(hide_password=False),
    )
    monkeypatch.setenv(
        "CV_MYSQL_DSN",
        source_url.set(database=f"runtime_forbidden_{uuid.uuid4().hex[:8]}").render_as_string(
            hide_password=False
        ),
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


def _validation_verdict_check(engine) -> str:
    constraints = {
        constraint["name"]: constraint["sqltext"]
        for constraint in inspect(engine).get_check_constraints("asset_validation_results")
    }
    return str(constraints["ck_asset_validation_verdict_reason"]).upper()


def test_terminal_failure_downgrade_refuses_before_ddl_and_round_trips(
    validation_migration_database,
) -> None:
    config, engine = validation_migration_database
    evidence_id = "019f8a00-0000-7000-8000-000000000601"
    now = datetime(2026, 7, 26, 9, 45, tzinfo=UTC).replace(tzinfo=None)
    command.downgrade(config, "f6c1a9d4e827")
    with engine.begin() as connection:
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        connection.execute(
            text(
                "INSERT INTO asset_validation_results "
                "(id, workspace_id, operation_id, asset_version_id, "
                "asset_object_id, attempt_number, stage, validator_name, "
                "validator_version, policy_version, verdict, reason_code, "
                "object_provider_version_id, object_etag, content_sha256, "
                "evidence_json, retention_deadline, created_at) VALUES "
                "(:id, 'migration-terminal-workspace', "
                "'019f8a00-0000-7000-8000-000000000602', "
                "'019f8a00-0000-7000-8000-000000000603', "
                "'019f8a00-0000-7000-8000-000000000604', 1, "
                "'CONTENT_SAFETY', 'alibaba-green', '3.2.4', "
                "'asset-validation-v1', 'TERMINAL_FAILURE', "
                "'PROVIDER_HTTP_403', 'minio-version-terminal', "
                "'\"terminal-etag\"', :sha256, :evidence, NULL, :created_at)"
            ),
            {
                "id": evidence_id,
                "sha256": "b" * 64,
                "evidence": json.dumps(
                    {
                        "failure_code": "PROVIDER_HTTP_403",
                        "outcome": "TERMINAL_FAILURE",
                        "provider": "alibaba-green",
                    }
                ),
                "created_at": now,
            },
        )
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 1"))

    assert "TERMINAL_FAILURE" in _validation_verdict_check(engine)
    with pytest.raises(
        RuntimeError,
        match="immutable terminal validation evidence",
    ):
        command.downgrade(config, "e5f8b2d6c914")

    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "f6c1a9d4e827"
        )
        immutable_row = (
            connection.execute(
                text(
                    "SELECT verdict, reason_code, evidence_json "
                    "FROM asset_validation_results WHERE id = :id"
                ),
                {"id": evidence_id},
            )
            .mappings()
            .one()
        )
    assert immutable_row["verdict"] == "TERMINAL_FAILURE"
    assert immutable_row["reason_code"] == "PROVIDER_HTTP_403"
    assert "PROVIDER_HTTP_403" in str(immutable_row["evidence_json"])
    assert "TERMINAL_FAILURE" in _validation_verdict_check(engine)

    with engine.begin() as connection:
        deleted = connection.execute(
            text("DELETE FROM asset_validation_results WHERE id = :id"),
            {"id": evidence_id},
        )
        assert deleted.rowcount == 1
    command.downgrade(config, "e5f8b2d6c914")
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "e5f8b2d6c914"
        )
    assert "TERMINAL_FAILURE" not in _validation_verdict_check(engine)

    command.upgrade(config, "f6c1a9d4e827")
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "f6c1a9d4e827"
        )
    assert "TERMINAL_FAILURE" in _validation_verdict_check(engine)
    command.upgrade(config, "head")


def test_asset_validation_migration_is_append_only_and_round_trips(
    validation_migration_database,
) -> None:
    config, engine = validation_migration_database
    now = datetime(2026, 7, 26, 9, 30, tzinfo=UTC)
    inspector = inspect(engine)
    assert "asset_validation_results" in inspector.get_table_names()
    columns = {
        column["name"]: column for column in inspector.get_columns("asset_validation_results")
    }
    assert isinstance(columns["created_at"]["type"], DATETIME)
    assert columns["created_at"]["type"].fsp == 6
    assert isinstance(columns["retention_deadline"]["type"], DATETIME)
    assert columns["retention_deadline"]["type"].fsp == 6
    assert "block_reason" in {column["name"] for column in inspector.get_columns("assets")}
    session_columns = {
        column["name"]: column for column in inspector.get_columns("upload_sessions")
    }
    version_columns = {column["name"]: column for column in inspector.get_columns("asset_versions")}
    for transfer_columns in (session_columns, version_columns):
        assert transfer_columns["validation_transfer_policy_version"]["nullable"] is False
        assert transfer_columns["validation_transfer_policy_snapshot_sha256"]["nullable"] is False
    assert version_columns["detected_mime"]["nullable"] is True
    assert version_columns["image_format"]["nullable"] is True
    assert version_columns["width"]["nullable"] is True
    assert version_columns["height"]["nullable"] is True
    assert version_columns["frame_count"]["nullable"] is True
    assert version_columns["validation_policy_version"]["nullable"] is False

    foreign_keys = {
        tuple(foreign_key["constrained_columns"])
        for foreign_key in inspector.get_foreign_keys("asset_validation_results")
    }
    assert foreign_keys >= {
        ("workspace_id", "asset_version_id"),
        (
            "workspace_id",
            "asset_object_id",
            "asset_version_id",
        ),
        ("workspace_id", "operation_id"),
    }
    unique_constraints = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("asset_validation_results")
    }
    assert unique_constraints >= {
        "uq_asset_validation_workspace_id",
        "uq_asset_validation_stage_attempt",
    }
    with engine.connect() as connection:
        trigger_names = {
            row["Trigger"]
            for row in connection.execute(
                text("SHOW TRIGGERS WHERE `Table` = 'asset_validation_results'")
            ).mappings()
        }
    assert trigger_names >= {
        "trg_asset_validation_results_no_update",
    }
    assert "trg_asset_validation_results_no_delete" not in trigger_names
    check_constraints = {
        constraint["name"]
        for constraint in inspector.get_check_constraints("asset_validation_results")
    }
    assert check_constraints >= {
        "ck_asset_validation_stage",
        "ck_asset_validation_content_sha256",
        "ck_asset_validation_validator_identity",
        "ck_asset_validation_object_identity",
    }
    assert {
        constraint["name"] for constraint in inspector.get_check_constraints("upload_sessions")
    } >= {
        "ck_upload_session_transfer_policy_version",
        "ck_upload_session_transfer_policy_snapshot",
    }
    assert {
        constraint["name"] for constraint in inspector.get_check_constraints("asset_versions")
    } >= {
        "ck_asset_version_transfer_policy_version",
        "ck_asset_version_transfer_policy_snapshot",
    }

    insert_statement = text(
        "INSERT INTO asset_validation_results "
        "(id, workspace_id, operation_id, asset_version_id, "
        "asset_object_id, attempt_number, stage, validator_name, "
        "validator_version, policy_version, verdict, reason_code, "
        "object_provider_version_id, object_etag, content_sha256, "
        "evidence_json, retention_deadline, created_at) "
        "VALUES "
        "(:id, :workspace_id, :operation_id, :asset_version_id, "
        ":asset_object_id, 1, :stage, :validator_name, '1.4.3/27000', "
        "'asset-validation-v1', 'PASS', NULL, :provider_version, "
        ":etag, :sha256, :evidence, :retention_deadline, :created_at)"
    )
    valid_parameters = {
        "id": "019f8a00-0000-7000-8000-000000000501",
        "workspace_id": "validation-workspace",
        "operation_id": "019f8a00-0000-7000-8000-000000000502",
        "asset_version_id": "019f8a00-0000-7000-8000-000000000503",
        "asset_object_id": "019f8a00-0000-7000-8000-000000000504",
        "stage": "MALWARE",
        "validator_name": "clamd",
        "provider_version": "minio-version-1",
        "etag": '"etag-1"',
        "sha256": "a" * 64,
        "evidence": json.dumps(
            {
                "outcome": "CLEAN",
                "scanner_version": "ClamAV 1.4.3/27000",
            }
        ),
        "retention_deadline": (now + timedelta(hours=72)).replace(tzinfo=None),
        "created_at": now.replace(tzinfo=None),
    }
    with engine.begin() as connection:
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        connection.execute(insert_statement, valid_parameters)
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 1"))

    invalid_facts = (
        {"stage": "UNREGISTERED"},
        {"sha256": "A" * 64},
        {"validator_name": "  "},
    )
    for index, invalid in enumerate(invalid_facts, start=1):
        with pytest.raises(DBAPIError), engine.begin() as connection:
            connection.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
            connection.execute(
                insert_statement,
                {
                    **valid_parameters,
                    "id": f"019f8a00-0000-7000-8000-{index:012d}",
                    **invalid,
                },
            )

    with engine.begin() as connection, pytest.raises(DBAPIError):
        connection.execute(
            text(
                "UPDATE asset_validation_results SET verdict = 'BLOCK' "
                "WHERE id = '019f8a00-0000-7000-8000-000000000501'"
            )
        )
    with engine.begin() as connection:
        deleted = connection.execute(
            text(
                "DELETE FROM asset_validation_results "
                "WHERE id = '019f8a00-0000-7000-8000-000000000501'"
            )
        )
        assert deleted.rowcount == 1

    command.downgrade(config, "d4e7a1c9b205")
    downgraded = inspect(engine)
    assert "asset_validation_results" not in downgraded.get_table_names()
    assert "block_reason" not in {column["name"] for column in downgraded.get_columns("assets")}
    assert "validation_policy_version" not in {
        column["name"] for column in downgraded.get_columns("asset_versions")
    }
    assert "validation_transfer_policy_version" not in {
        column["name"] for column in downgraded.get_columns("upload_sessions")
    }

    command.upgrade(config, "head")
    assert "asset_validation_results" in inspect(engine).get_table_names()
