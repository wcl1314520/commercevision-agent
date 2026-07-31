from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, exc, inspect, text
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.engine import Engine, make_url

pytestmark = pytest.mark.integration

WORKSPACE_ID = "ledger-migration-workspace"
WORKFLOW_ID = "019fa100-0000-7000-8000-000000000001"
PRODUCT_ID = "019fa100-0000-7000-8000-000000000002"
PRODUCT_BRIEF_ID = "019fa100-0000-7000-8000-000000000003"
OPERATION_ID = "019fa100-0000-7000-8000-000000000004"
CALL_ID = "019fa100-0000-7000-8000-000000000005"
DUPLICATE_TARGET_CALL_ID = "019fa100-0000-7000-8000-000000000006"

_LEDGER_TRIGGER_NAMES = {
    "trg_pb_provider_artifacts_lifecycle",
    "trg_pb_provider_artifacts_no_delete",
    "trg_pb_provider_calls_validate_artifacts",
}


@pytest.fixture
def provider_artifact_migration_database(integration_settings, monkeypatch):
    source_url = make_url(integration_settings.mysql_dsn)
    database_name = f"commercevision_ticket07_ledger_{uuid.uuid4().hex[:8]}"
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
        command.upgrade(config, "d9e4f7a2b610")
        yield config, engine
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS `{database_name}`"))
        admin_engine.dispose()


def _seed_legacy_completed_call(engine: Engine) -> None:
    now = datetime(2026, 7, 29, 8, 0)
    deadline = now + timedelta(hours=72)
    with engine.begin() as connection:
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        connection.execute(
            text(
                "INSERT INTO workflows "
                "(id, workspace_id, created_by, workflow_type, status, retention_status, "
                "current_node, version, input_json, result_json, expires_at, "
                "cancellation_requested_at, created_at, updated_at) VALUES "
                "(:id, :workspace, 'migration', 'product-understanding', "
                "'UNDERSTANDING', 'ACTIVE', 'understand_product', 1, JSON_OBJECT(), "
                "NULL, :deadline, NULL, :now, :now)"
            ),
            {
                "id": WORKFLOW_ID,
                "workspace": WORKSPACE_ID,
                "deadline": deadline,
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO products "
                "(id, workspace_id, source_namespace, external_id, source_version, "
                "title, category_code, brand, attributes_json, expires_at, version, "
                "created_at, updated_at) VALUES "
                "(:id, :workspace, 'MANUAL', 'ledger-product', 'v1', 'Ledger Product', "
                "'beauty.test', 'Ledger', JSON_OBJECT(), NULL, 1, :now, :now)"
            ),
            {"id": PRODUCT_ID, "workspace": WORKSPACE_ID, "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO durable_operations "
                "(id, workspace_id, kind, target_type, target_id, target_version, "
                "input_hash, input_ref, output_ref, provider_request_id, state, "
                "lease_owner, lease_token, lease_expires_at, attempt_count, max_attempts, "
                "next_attempt_at, execution_deadline_at, reconciliation_attempt_count, "
                "max_reconciliation_attempts, next_reconciliation_at, "
                "reconciliation_started_at, reconciliation_deadline_at, "
                "reconciliation_required, reconciliation_outcome, dead_letter_id, "
                "replay_source_dead_letter_id, replay_attempt, recovery_generation, "
                "recovery_consumed_generation, error_code, error_category, error_message, "
                "error_retryable, error_provider_request_id, created_at, updated_at, "
                "last_attempt_at, started_at, completed_at, version) VALUES "
                "(:id, :workspace, 'PRODUCT_BRIEF_ANALYSIS', 'product_brief', "
                ":brief_id, 1, :input_hash, 'mysql://analysis/legacy', NULL, NULL, "
                "'RUNNING', 'migration-worker', :lease_token, :lease_expires, 1, 3, "
                "NULL, :deadline, 0, 3, NULL, NULL, NULL, 0, 'NOT_REQUIRED', NULL, "
                "NULL, 0, 0, 0, NULL, NULL, NULL, NULL, NULL, :now, :now, :now, "
                ":now, NULL, 1)"
            ),
            {
                "id": OPERATION_ID,
                "workspace": WORKSPACE_ID,
                "brief_id": PRODUCT_BRIEF_ID,
                "input_hash": "1" * 64,
                "lease_token": "019fa100-0000-7000-8000-000000000099",
                "lease_expires": now + timedelta(hours=1),
                "deadline": deadline,
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO product_briefs "
                "(id, workspace_id, workflow_id, product_id, operation_id, created_by, "
                "state, current_version_id, confirmed_version_id, version, "
                "retention_class, retention_deadline, created_at, updated_at) VALUES "
                "(:id, :workspace, :workflow_id, :product_id, :operation_id, "
                "'migration', 'DRAFT', NULL, NULL, 1, 'TASK', :deadline, :now, :now)"
            ),
            {
                "id": PRODUCT_BRIEF_ID,
                "workspace": WORKSPACE_ID,
                "workflow_id": WORKFLOW_ID,
                "product_id": PRODUCT_ID,
                "operation_id": OPERATION_ID,
                "deadline": deadline,
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO product_brief_provider_calls "
                "(id, workspace_id, product_brief_id, operation_id, operation_attempt, "
                "call_index, status, provider, endpoint_region, endpoint_host, "
                "requested_model, submitted_model_snapshot, resolved_model, "
                "prompt_version, config_snapshot_sha256, request_id, input_tokens, "
                "output_tokens, total_tokens, latency_ms, "
                "request_artifact_storage_backend, request_artifact_location, "
                "request_artifact_bucket, request_artifact_key, "
                "request_artifact_provider_version_id, request_artifact_etag, "
                "request_artifact_sha256, request_artifact_byte_size, "
                "response_artifact_storage_backend, response_artifact_location, "
                "response_artifact_bucket, response_artifact_key, "
                "response_artifact_provider_version_id, response_artifact_etag, "
                "response_artifact_sha256, response_artifact_byte_size, error_code, "
                "error_category, error_retryable, retention_class, retention_deadline, "
                "created_at) VALUES "
                "(:id, :workspace, :brief_id, :operation_id, 1, 0, 'SUCCEEDED', "
                "'alibaba-model-studio', 'cn-hangzhou', 'dashscope.aliyuncs.com', "
                "'qwen-vl', 'qwen-vl-2026-07-01', 'qwen-vl-2026-07-01', 'prompt-v1', "
                ":config_hash, 'provider-request-1', 10, 20, 30, 250, "
                "'OSS', 'PROVIDER_RESULT', 'provider-results', "
                "'product-brief/legacy/attempt-1/call-0/request.json', "
                "'request-version-1', '\"request-etag\"', :request_hash, 128, "
                "'OSS', 'PROVIDER_RESULT', 'provider-results', "
                "'product-brief/legacy/attempt-1/call-0/response.json', "
                "'response-version-1', '\"response-etag\"', :response_hash, 256, "
                "NULL, NULL, NULL, 'TASK', :deadline, :now)"
            ),
            {
                "id": CALL_ID,
                "workspace": WORKSPACE_ID,
                "brief_id": PRODUCT_BRIEF_ID,
                "operation_id": OPERATION_ID,
                "config_hash": "2" * 64,
                "request_hash": "3" * 64,
                "response_hash": "4" * 64,
                "deadline": deadline,
                "now": now,
            },
        )
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 1"))


def _seed_legacy_call_with_duplicate_request_target(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO product_brief_provider_calls "
                "(id, workspace_id, product_brief_id, operation_id, "
                "operation_attempt, call_index, status, provider, endpoint_region, "
                "endpoint_host, requested_model, submitted_model_snapshot, "
                "resolved_model, prompt_version, config_snapshot_sha256, request_id, "
                "input_tokens, output_tokens, total_tokens, latency_ms, "
                "request_artifact_storage_backend, request_artifact_location, "
                "request_artifact_bucket, request_artifact_key, "
                "request_artifact_provider_version_id, request_artifact_etag, "
                "request_artifact_sha256, request_artifact_byte_size, "
                "response_artifact_storage_backend, response_artifact_location, "
                "response_artifact_bucket, response_artifact_key, "
                "response_artifact_provider_version_id, response_artifact_etag, "
                "response_artifact_sha256, response_artifact_byte_size, "
                "error_code, error_category, error_retryable, retention_class, "
                "retention_deadline, created_at) "
                "SELECT :duplicate_id, workspace_id, product_brief_id, operation_id, "
                "operation_attempt, 1, 'TIMEOUT', provider, endpoint_region, "
                "endpoint_host, requested_model, submitted_model_snapshot, "
                "NULL, prompt_version, config_snapshot_sha256, "
                "'provider-request-duplicate-target', input_tokens, output_tokens, "
                "total_tokens, latency_ms, request_artifact_storage_backend, "
                "request_artifact_location, request_artifact_bucket, "
                "request_artifact_key, request_artifact_provider_version_id, "
                "request_artifact_etag, request_artifact_sha256, "
                "request_artifact_byte_size, NULL, NULL, NULL, NULL, NULL, NULL, "
                "NULL, NULL, 'PROVIDER_TIMEOUT', 'timeout', 1, retention_class, "
                "retention_deadline, created_at "
                "FROM product_brief_provider_calls WHERE id = :source_id"
            ),
            {
                "duplicate_id": DUPLICATE_TARGET_CALL_ID,
                "source_id": CALL_ID,
            },
        )


def _trigger_names(engine: Engine) -> set[str]:
    with engine.connect() as connection:
        return {
            row["trigger_name"]
            for row in connection.execute(
                text(
                    "SELECT TRIGGER_NAME AS trigger_name "
                    "FROM information_schema.triggers "
                    "WHERE trigger_schema = DATABASE()"
                )
            ).mappings()
        }


def _migration_snapshot(engine: Engine) -> dict[str, object]:
    inspector = inspect(engine)
    with engine.connect() as connection:
        return {
            "revision": connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one(),
            "call_columns": tuple(
                column["name"] for column in inspector.get_columns("product_brief_provider_calls")
            ),
            "analysis_columns": tuple(
                column["name"]
                for column in inspector.get_columns("product_brief_analysis_requests")
            ),
            "artifact_columns": tuple(
                column["name"]
                for column in inspector.get_columns("product_brief_provider_artifacts")
            ),
            "calls": tuple(
                tuple(row)
                for row in connection.execute(
                    text(
                        "SELECT id, request_artifact_id, response_artifact_id, "
                        "request_artifact_key, response_artifact_key "
                        "FROM product_brief_provider_calls ORDER BY id"
                    )
                ).all()
            ),
            "artifacts": tuple(
                tuple(row)
                for row in connection.execute(
                    text(
                        "SELECT id, kind, state, object_key, provider_version_id, "
                        "etag, unknown_reason, version "
                        "FROM product_brief_provider_artifacts ORDER BY id"
                    )
                ).all()
            ),
            "triggers": frozenset(_trigger_names(engine)),
        }


def _insert_unrepresentable_artifact(engine: Engine, *, state: str) -> None:
    now = datetime(2026, 7, 29, 9, 0)
    deadline = now + timedelta(hours=72)
    artifact_id = {
        "INTENDED": "019fa100-0000-7000-8000-000000000010",
        "UNKNOWN": "019fa100-0000-7000-8000-000000000011",
        "STORED": "019fa100-0000-7000-8000-000000000012",
    }[state]
    provider_version_id = "unreferenced-version" if state == "STORED" else None
    etag = '"unreferenced-etag"' if state == "STORED" else None
    unknown_reason = "MIGRATION_TEST" if state == "UNKNOWN" else None
    stored_at = now if state == "STORED" else None
    object_key = f"product-brief/unrepresentable/{state.lower()}.json"
    target_hash = hashlib.sha256(
        f"OSS\0PROVIDER_RESULT\0provider-results\0{object_key}".encode()
    ).hexdigest()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO product_brief_provider_artifacts "
                "(id, workspace_id, product_brief_id, operation_id, "
                "operation_attempt, call_index, kind, state, key_schema_version, "
                "storage_backend, location, bucket, object_key, target_sha256, "
                "content_type, expected_sha256, expected_byte_size, retention_class, "
                "retention_deadline, write_fence, provider_version_id, etag, "
                "unknown_reason, version, stored_at, created_at, updated_at) VALUES "
                "(:id, :workspace, :brief_id, :operation_id, 2, 0, 'REQUEST', "
                ":state, 'provider-artifact-v1', 'OSS', 'PROVIDER_RESULT', "
                "'provider-results', :object_key, :target_hash, 'application/json', "
                ":content_hash, 64, 'TASK', :deadline, :write_fence, "
                ":provider_version_id, :etag, :unknown_reason, 1, :stored_at, "
                ":now, :now)"
            ),
            {
                "brief_id": PRODUCT_BRIEF_ID,
                "content_hash": "6" * 64,
                "deadline": deadline,
                "etag": etag,
                "id": artifact_id,
                "now": now,
                "object_key": object_key,
                "operation_id": OPERATION_ID,
                "provider_version_id": provider_version_id,
                "state": state,
                "stored_at": stored_at,
                "target_hash": target_hash,
                "unknown_reason": unknown_reason,
                "workspace": WORKSPACE_ID,
                "write_fence": "a" * 64,
            },
        )


def _make_legacy_call_inconsistent(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("DROP TRIGGER trg_product_brief_provider_calls_no_update"))
        connection.execute(
            text(
                "UPDATE product_brief_provider_calls "
                "SET request_artifact_key = "
                "'product-brief/legacy/inconsistent-request.json' "
                "WHERE id = :id"
            ),
            {"id": CALL_ID},
        )
        connection.execute(
            text(
                "CREATE TRIGGER trg_product_brief_provider_calls_no_update "
                "BEFORE UPDATE ON product_brief_provider_calls FOR EACH ROW "
                "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = "
                "'product_brief_provider_calls are immutable'"
            )
        )


def _remove_legacy_success_response_artifact(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("DROP TRIGGER trg_product_brief_provider_calls_no_update"))
        connection.execute(
            text(
                "UPDATE product_brief_provider_calls SET "
                "response_artifact_storage_backend = NULL, "
                "response_artifact_location = NULL, "
                "response_artifact_bucket = NULL, "
                "response_artifact_key = NULL, "
                "response_artifact_provider_version_id = NULL, "
                "response_artifact_etag = NULL, "
                "response_artifact_sha256 = NULL, "
                "response_artifact_byte_size = NULL "
                "WHERE id = :id"
            ),
            {"id": CALL_ID},
        )
        connection.execute(
            text(
                "CREATE TRIGGER trg_product_brief_provider_calls_no_update "
                "BEFORE UPDATE ON product_brief_provider_calls FOR EACH ROW "
                "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = "
                "'product_brief_provider_calls are immutable'"
            )
        )


def test_provider_artifact_migration_backfills_exact_completed_call_references(
    provider_artifact_migration_database,
) -> None:
    config, engine = provider_artifact_migration_database
    _seed_legacy_completed_call(engine)

    command.upgrade(config, "head")
    command.check(config)

    inspector = inspect(engine)
    assert "product_brief_provider_artifacts" in inspector.get_table_names()
    call_columns = {
        column["name"]: column for column in inspector.get_columns("product_brief_provider_calls")
    }
    assert call_columns["request_artifact_id"]["nullable"] is False
    assert call_columns["response_artifact_id"]["nullable"] is True
    columns = {
        column["name"]: column
        for column in inspector.get_columns("product_brief_provider_artifacts")
    }
    assert columns["provider_version_id"]["nullable"] is True
    assert columns["etag"]["nullable"] is True
    assert columns["version"]["nullable"] is False
    for timestamp_name in ("retention_deadline", "stored_at", "created_at", "updated_at"):
        assert isinstance(columns[timestamp_name]["type"], DATETIME)
        assert columns[timestamp_name]["type"].fsp == 6

    uniques = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("product_brief_provider_artifacts")
    }
    assert (
        "operation_id",
        "operation_attempt",
        "call_index",
        "kind",
    ) in uniques
    assert ("storage_backend", "location", "target_sha256") in uniques
    checks = {
        constraint["name"]
        for constraint in inspector.get_check_constraints("product_brief_provider_artifacts")
    }
    assert "ck_pb_provider_artifacts_target_identity" in checks

    with (
        engine.begin() as connection,
        pytest.raises(
            exc.DatabaseError,
            match="ck_pb_provider_artifacts_target_identity",
        ),
    ):
        connection.execute(
            text(
                "INSERT INTO product_brief_provider_artifacts "
                "(id, workspace_id, product_brief_id, operation_id, "
                "operation_attempt, call_index, kind, state, key_schema_version, "
                "storage_backend, location, bucket, object_key, target_sha256, "
                "content_type, expected_sha256, expected_byte_size, "
                "retention_class, retention_deadline, write_fence, "
                "provider_version_id, etag, unknown_reason, version, stored_at, "
                "created_at, updated_at) "
                "SELECT :new_id, workspace_id, product_brief_id, operation_id, "
                "operation_attempt, call_index + 10, kind, state, key_schema_version, "
                "storage_backend, location, bucket, object_key, :forged_target, "
                "content_type, expected_sha256, expected_byte_size, "
                "retention_class, retention_deadline, :write_fence, "
                "provider_version_id, etag, unknown_reason, version, stored_at, "
                "created_at, updated_at "
                "FROM product_brief_provider_artifacts "
                "WHERE kind = 'REQUEST' LIMIT 1"
            ),
            {
                "new_id": "019fa100-0000-7000-8000-0000000000f2",
                "forged_target": "0" * 64,
                "write_fence": "f" * 64,
            },
        )

    foreign_keys = {
        foreign_key["name"]: foreign_key
        for foreign_key in inspector.get_foreign_keys("product_brief_provider_artifacts")
    }
    assert foreign_keys["fk_pb_provider_artifacts_brief"]["options"]["ondelete"] == ("RESTRICT")
    assert foreign_keys["fk_pb_provider_artifacts_operation"]["options"]["ondelete"] == ("RESTRICT")

    with engine.connect() as connection:
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
        rows = (
            connection.execute(
                text(
                    "SELECT id, kind, state, key_schema_version, storage_backend, "
                    "location, bucket, object_key, expected_sha256, expected_byte_size, "
                    "provider_version_id, etag, unknown_reason, version "
                    "FROM product_brief_provider_artifacts ORDER BY kind"
                )
            )
            .mappings()
            .all()
        )
        call = (
            connection.execute(
                text(
                    "SELECT request_artifact_id, response_artifact_id "
                    "FROM product_brief_provider_calls WHERE id = :id"
                ),
                {"id": CALL_ID},
            )
            .mappings()
            .one()
        )

    assert {
        "trg_pb_provider_artifacts_lifecycle",
        "trg_pb_provider_artifacts_no_delete",
        "trg_pb_provider_calls_validate_artifacts",
    } <= triggers
    assert [row["kind"] for row in rows] == ["REQUEST", "RESPONSE"]
    assert all(row["state"] == "STORED" for row in rows)
    assert all(row["key_schema_version"] == "legacy-provider-artifact-v1" for row in rows)
    assert rows[0]["expected_sha256"] == "3" * 64
    assert rows[0]["expected_byte_size"] == 128
    assert rows[0]["provider_version_id"] == "request-version-1"
    assert rows[0]["etag"] == '"request-etag"'
    assert rows[0]["unknown_reason"] is None
    assert rows[0]["version"] == 1
    ids_by_kind = {row["kind"]: row["id"] for row in rows}
    assert call["request_artifact_id"] == ids_by_kind["REQUEST"]
    assert call["response_artifact_id"] == ids_by_kind["RESPONSE"]

    command.downgrade(config, "d9e4f7a2b610")
    downgraded = inspect(engine)
    assert "product_brief_provider_artifacts" not in downgraded.get_table_names()
    call_columns = {
        column["name"] for column in downgraded.get_columns("product_brief_provider_calls")
    }
    assert "request_artifact_id" not in call_columns
    assert "response_artifact_id" not in call_columns
    with engine.connect() as connection:
        downgraded_triggers = {
            row["trigger_name"]
            for row in connection.execute(
                text(
                    "SELECT TRIGGER_NAME AS trigger_name "
                    "FROM information_schema.triggers "
                    "WHERE trigger_schema = DATABASE()"
                )
            ).mappings()
        }
        assert (
            connection.execute(
                text(
                    "SELECT request_artifact_provider_version_id "
                    "FROM product_brief_provider_calls WHERE id = :id"
                ),
                {"id": CALL_ID},
            ).scalar_one()
            == "request-version-1"
        )
    assert (
        not {
            "trg_pb_provider_artifacts_lifecycle",
            "trg_pb_provider_artifacts_no_delete",
            "trg_pb_provider_calls_validate_artifacts",
        }
        & downgraded_triggers
    )

    command.upgrade(config, "head")
    assert "product_brief_provider_artifacts" in inspect(engine).get_table_names()


def test_provider_artifact_upgrade_restores_call_immutability_when_response_backfill_fails(
    provider_artifact_migration_database,
) -> None:
    config, engine = provider_artifact_migration_database
    _seed_legacy_completed_call(engine)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE migration_test_update_counter (update_count INTEGER NOT NULL)")
        )
        connection.execute(
            text("INSERT INTO migration_test_update_counter (update_count) VALUES (0)")
        )
        connection.execute(
            text(
                "CREATE TRIGGER trg_test_fail_response_artifact_backfill "
                "BEFORE UPDATE ON product_brief_provider_calls FOR EACH ROW "
                "BEGIN "
                "UPDATE migration_test_update_counter "
                "SET update_count = update_count + 1; "
                "IF (SELECT update_count FROM migration_test_update_counter) > 1 THEN "
                "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = "
                "'forced response backfill failure'; "
                "END IF; "
                "END"
            )
        )

    with pytest.raises(exc.DatabaseError, match="forced response backfill failure"):
        command.upgrade(config, "f2a7c9d1e406")

    assert "trg_product_brief_provider_calls_no_update" in _trigger_names(engine)
    with engine.begin() as connection:
        connection.execute(text("DROP TRIGGER trg_test_fail_response_artifact_backfill"))
    with (
        engine.begin() as connection,
        pytest.raises(
            exc.DatabaseError,
            match="product_brief_provider_calls are immutable",
        ),
    ):
        connection.execute(
            text("UPDATE product_brief_provider_calls SET status = status WHERE id = :id"),
            {"id": CALL_ID},
        )


def test_provider_artifact_upgrade_rejects_duplicate_legacy_physical_targets_before_ddl(
    provider_artifact_migration_database,
) -> None:
    config, engine = provider_artifact_migration_database
    _seed_legacy_completed_call(engine)
    _seed_legacy_call_with_duplicate_request_target(engine)
    inspector = inspect(engine)
    with engine.connect() as connection:
        before = {
            "revision": connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one(),
            "tables": frozenset(inspector.get_table_names()),
            "call_columns": tuple(
                column["name"] for column in inspector.get_columns("product_brief_provider_calls")
            ),
            "calls": tuple(
                tuple(row)
                for row in connection.execute(
                    text(
                        "SELECT id, call_index, request_artifact_storage_backend, "
                        "request_artifact_location, request_artifact_bucket, "
                        "request_artifact_key FROM product_brief_provider_calls "
                        "ORDER BY id"
                    )
                ).all()
            ),
            "triggers": frozenset(_trigger_names(engine)),
        }

    with pytest.raises(
        RuntimeError,
        match="legacy provider calls cannot be backfilled",
    ):
        command.upgrade(config, "f2a7c9d1e406")

    inspector = inspect(engine)
    with engine.connect() as connection:
        after = {
            "revision": connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one(),
            "tables": frozenset(inspector.get_table_names()),
            "call_columns": tuple(
                column["name"] for column in inspector.get_columns("product_brief_provider_calls")
            ),
            "calls": tuple(
                tuple(row)
                for row in connection.execute(
                    text(
                        "SELECT id, call_index, request_artifact_storage_backend, "
                        "request_artifact_location, request_artifact_bucket, "
                        "request_artifact_key FROM product_brief_provider_calls "
                        "ORDER BY id"
                    )
                ).all()
            ),
            "triggers": frozenset(_trigger_names(engine)),
        }
    assert after == before
    assert "product_brief_provider_artifacts" not in after["tables"]
    assert "request_artifact_id" not in after["call_columns"]
    assert "response_artifact_id" not in after["call_columns"]


def test_provider_artifact_upgrade_rejects_success_without_response_before_ddl(
    provider_artifact_migration_database,
) -> None:
    config, engine = provider_artifact_migration_database
    _seed_legacy_completed_call(engine)
    _remove_legacy_success_response_artifact(engine)
    inspector = inspect(engine)
    with engine.connect() as connection:
        before = {
            "revision": connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one(),
            "tables": frozenset(inspector.get_table_names()),
            "call_columns": tuple(
                column["name"] for column in inspector.get_columns("product_brief_provider_calls")
            ),
            "call": tuple(
                connection.execute(
                    text(
                        "SELECT status, response_artifact_storage_backend, "
                        "response_artifact_key FROM product_brief_provider_calls "
                        "WHERE id = :id"
                    ),
                    {"id": CALL_ID},
                ).one()
            ),
            "triggers": frozenset(_trigger_names(engine)),
        }

    with pytest.raises(
        RuntimeError,
        match="successful legacy provider calls require response artifacts",
    ):
        command.upgrade(config, "f2a7c9d1e406")

    inspector = inspect(engine)
    with engine.connect() as connection:
        after = {
            "revision": connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one(),
            "tables": frozenset(inspector.get_table_names()),
            "call_columns": tuple(
                column["name"] for column in inspector.get_columns("product_brief_provider_calls")
            ),
            "call": tuple(
                connection.execute(
                    text(
                        "SELECT status, response_artifact_storage_backend, "
                        "response_artifact_key FROM product_brief_provider_calls "
                        "WHERE id = :id"
                    ),
                    {"id": CALL_ID},
                ).one()
            ),
            "triggers": frozenset(_trigger_names(engine)),
        }
    assert after == before
    assert "product_brief_provider_artifacts" not in after["tables"]
    assert "request_artifact_id" not in after["call_columns"]
    assert "response_artifact_id" not in after["call_columns"]


@pytest.mark.parametrize(
    "incompatibility",
    ("INTENDED", "UNKNOWN", "UNREFERENCED_STORED", "LEGACY_MISMATCH"),
)
def test_provider_artifact_downgrade_fails_before_any_change_for_unrepresentable_ledger(
    provider_artifact_migration_database,
    incompatibility: str,
) -> None:
    config, engine = provider_artifact_migration_database
    _seed_legacy_completed_call(engine)
    # Exercise the artifact-ledger downgrade at its own revision boundary.
    # Later migrations use non-transactional DDL and may legitimately remove
    # their own objects before Alembic reaches this fail-closed guard.
    command.upgrade(config, "f2a7c9d1e406")
    if incompatibility == "UNREFERENCED_STORED":
        _insert_unrepresentable_artifact(engine, state="STORED")
    elif incompatibility == "LEGACY_MISMATCH":
        _make_legacy_call_inconsistent(engine)
    else:
        _insert_unrepresentable_artifact(engine, state=incompatibility)
    before = _migration_snapshot(engine)

    with pytest.raises(
        RuntimeError,
        match="provider artifact ledger cannot be represented",
    ):
        command.downgrade(config, "d9e4f7a2b610")

    assert _migration_snapshot(engine) == before
    assert before["triggers"] >= _LEDGER_TRIGGER_NAMES
