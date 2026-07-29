from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url

pytestmark = pytest.mark.integration

WORKSPACE_ID = "trace-migration-workspace"
PRODUCT_BRIEF_ID = "019fac50-0000-7000-8000-000000000001"
OPERATION_ID = "019fac50-0000-7000-8000-000000000002"
ANALYSIS_ID = "019fac50-0000-7000-8000-000000000003"
EVENT_ID = "019fac50-0000-7000-8000-000000000004"
TRACE_ID = "product-brief-originating-http-trace"


@pytest.fixture
def trace_lineage_migration_database(integration_settings, monkeypatch):
    source_url = make_url(integration_settings.mysql_dsn)
    database_name = f"commercevision_ticket07_trace_{uuid.uuid4().hex[:8]}"
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
        command.upgrade(config, "f2a7c9d1e406")
        yield config, engine
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS `{database_name}`"))
        admin_engine.dispose()


def _seed_legacy_analysis(engine: Engine, *, include_request_event: bool) -> None:
    created_at = datetime(2026, 7, 29, 12, 0, 0, 123456)
    deadline = created_at + timedelta(hours=72)
    with engine.begin() as connection:
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        connection.execute(
            text(
                "INSERT INTO product_brief_analysis_requests "
                "(id, workspace_id, product_brief_id, operation_id, category, "
                "expected_workflow_version, product_catalog_version, provider, "
                "endpoint_region, endpoint_host, requested_model, "
                "submitted_model_snapshot, provider_configuration_snapshot_sha256, "
                "prompt_version, review_policy_version, review_confidence_threshold, "
                "review_mandatory_paths_json, review_sensitive_claim_paths_json, "
                "review_policy_snapshot_sha256, transfer_policy_version, "
                "transfer_policy_snapshot_sha256, created_by, retention_class, "
                "retention_deadline, created_at) VALUES "
                "(:id, :workspace_id, :product_brief_id, :operation_id, 'BEAUTY', "
                "3, 1, 'deterministic', 'local', 'vision.local', "
                "'deterministic-vision', 'deterministic-vision-v1', :provider_hash, "
                "'product-brief-v1', 'product-brief-review-v1', 0.9500, "
                "JSON_ARRAY(), JSON_ARRAY(), :review_hash, 'vision-transfer-v1', "
                ":transfer_hash, 'migration', 'TASK', :deadline, :created_at)"
            ),
            {
                "created_at": created_at,
                "deadline": deadline,
                "id": ANALYSIS_ID,
                "operation_id": OPERATION_ID,
                "product_brief_id": PRODUCT_BRIEF_ID,
                "provider_hash": "1" * 64,
                "review_hash": "2" * 64,
                "transfer_hash": "3" * 64,
                "workspace_id": WORKSPACE_ID,
            },
        )
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
        if include_request_event:
            connection.execute(
                text(
                    "INSERT INTO outbox_events "
                    "(id, aggregate_type, aggregate_id, event_type, schema_version, "
                    "aggregate_version, trace_id, payload_json, occurred_at, "
                    "available_at, published_at, publish_attempts, lock_owner, "
                    "lock_token, locked_until, last_error, workspace_id, "
                    "source_dead_letter_id, replay_attempt) VALUES "
                    "(:id, 'product_brief', :product_brief_id, "
                    "'product-brief.requested', 1, 1, :trace_id, :payload_json, "
                    ":created_at, :created_at, NULL, 0, NULL, NULL, NULL, NULL, "
                    ":workspace_id, NULL, 0)"
                ),
                {
                    "created_at": created_at,
                    "id": EVENT_ID,
                    "payload_json": json.dumps(
                        {
                            "operation_id": OPERATION_ID,
                            "product_brief_id": PRODUCT_BRIEF_ID,
                            "workspace_id": WORKSPACE_ID,
                        }
                    ),
                    "product_brief_id": PRODUCT_BRIEF_ID,
                    "trace_id": TRACE_ID,
                    "workspace_id": WORKSPACE_ID,
                },
            )


def test_trace_lineage_migration_backfills_exact_event_and_restores_immutability(
    trace_lineage_migration_database,
) -> None:
    config, engine = trace_lineage_migration_database
    _seed_legacy_analysis(engine, include_request_event=True)

    command.upgrade(config, "head")
    command.check(config)

    columns = {
        column["name"]: column
        for column in inspect(engine).get_columns("product_brief_analysis_requests")
    }
    assert columns["trace_id"]["nullable"] is False
    with engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT trace_id FROM product_brief_analysis_requests WHERE id = :analysis_id"
                ),
                {"analysis_id": ANALYSIS_ID},
            ).scalar_one()
            == TRACE_ID
        )
        triggers = {
            row["trigger_name"]
            for row in connection.execute(
                text(
                    "SELECT TRIGGER_NAME AS trigger_name "
                    "FROM information_schema.triggers "
                    "WHERE trigger_schema = DATABASE()"
                )
            ).mappings()
        }
    assert "trg_product_brief_analysis_requests_no_update" in triggers

    with pytest.raises(RuntimeError, match="ProductBrief trace lineage"):
        command.downgrade(config, "c8d3e7f1a602")

    columns_after_failed_downgrade = {
        column["name"]: column
        for column in inspect(engine).get_columns("product_brief_analysis_requests")
    }
    assert columns_after_failed_downgrade["trace_id"]["nullable"] is False
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "a4c8e7f3b219"
        )
        assert (
            connection.execute(
                text(
                    "SELECT trace_id FROM product_brief_analysis_requests WHERE id = :analysis_id"
                ),
                {"analysis_id": ANALYSIS_ID},
            ).scalar_one()
            == TRACE_ID
        )
        assert (
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM information_schema.triggers "
                    "WHERE trigger_schema = DATABASE() "
                    "AND trigger_name = "
                    "'trg_product_brief_analysis_requests_no_update'"
                )
            ).scalar_one()
            == 1
        )


def test_trace_lineage_migration_fails_before_schema_change_without_source_event(
    trace_lineage_migration_database,
) -> None:
    config, engine = trace_lineage_migration_database
    _seed_legacy_analysis(engine, include_request_event=False)

    with pytest.raises(RuntimeError, match="without exactly one request event"):
        command.upgrade(config, "head")

    assert "trace_id" not in {
        column["name"] for column in inspect(engine).get_columns("product_brief_analysis_requests")
    }
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "f2a7c9d1e406"
        )
