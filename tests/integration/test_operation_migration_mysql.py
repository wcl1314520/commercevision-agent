import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from commercevision_persistence.models import MYSQL_DATETIME_FSP
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.integration

WORKSPACE_ID_TABLES = {
    "audit_events",
    "catalog_external_identities",
    "dead_letter_messages",
    "dead_letter_replays",
    "durable_operations",
    "outbox_events",
    "products",
    "skus",
    "workflows",
}
PARENT_WORKSPACE_ID_TABLES = {
    "audit_events",
    "catalog_external_identities",
    "products",
    "skus",
    "workflows",
}
WORKSPACE_ID_COLLATION = "utf8mb4_0900_bin"
PARENT_IDENTITY_COLLATION = "utf8mb4_0900_ai_ci"


def _workspace_collations(engine) -> dict[str, str]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT TABLE_NAME, COLLATION_NAME
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND COLUMN_NAME = 'workspace_id'
                """
            )
        )
        return {row.TABLE_NAME: row.COLLATION_NAME for row in rows}


def _column_collation(engine, *, table_name: str, column_name: str) -> str:
    with engine.connect() as connection:
        return connection.execute(
            text(
                """
                SELECT COLLATION_NAME
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = :table_name
                  AND COLUMN_NAME = :column_name
                """
            ),
            {"table_name": table_name, "column_name": column_name},
        ).scalar_one()


def test_operation_migration_preserves_phase1_and_catalog_upgrade_paths(
    integration_settings,
    monkeypatch,
) -> None:
    monkeypatch.setenv("CV_MYSQL_DSN", integration_settings.mysql_dsn)
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    engine = create_engine(integration_settings.mysql_dsn)
    try:
        inspector = inspect(engine)
        assert {"durable_operations", "dead_letter_replays"} <= set(inspector.get_table_names())
        assert _workspace_collations(engine) == {
            table_name: WORKSPACE_ID_COLLATION for table_name in WORKSPACE_ID_TABLES
        }
        assert (
            _column_collation(
                engine,
                table_name="idempotency_keys",
                column_name="scope",
            )
            == WORKSPACE_ID_COLLATION
        )
        operation_columns = {
            column["name"]: column["type"] for column in inspector.get_columns("durable_operations")
        }
        for column_name in (
            "lease_expires_at",
            "next_attempt_at",
            "execution_deadline_at",
            "next_reconciliation_at",
            "reconciliation_started_at",
            "reconciliation_deadline_at",
            "created_at",
            "updated_at",
            "last_attempt_at",
            "started_at",
            "completed_at",
        ):
            assert isinstance(operation_columns[column_name], DATETIME)
            assert operation_columns[column_name].fsp == MYSQL_DATETIME_FSP
        assert {
            "provider_request_id",
            "error_provider_request_id",
            "recovery_generation",
            "recovery_consumed_generation",
            "recovery_pending",
        } <= set(operation_columns)
        assert {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("durable_operations")
        } >= {
            "uq_durable_operation_logical",
            "uq_durable_operation_workspace_id",
        }
        operation_foreign_keys = {
            foreign_key["name"]: foreign_key
            for foreign_key in inspector.get_foreign_keys("durable_operations")
        }
        assert operation_foreign_keys["fk_durable_operation_dead_letter"][
            "constrained_columns"
        ] == ["workspace_id", "dead_letter_id"]
        assert operation_foreign_keys["fk_durable_operation_dead_letter"]["referred_columns"] == [
            "workspace_id",
            "id",
        ]
        assert operation_foreign_keys["fk_durable_operation_replay_source"][
            "constrained_columns"
        ] == ["workspace_id", "replay_source_dead_letter_id"]
        assert operation_foreign_keys["fk_durable_operation_replay_source"]["referred_columns"] == [
            "workspace_id",
            "id",
        ]
        operation_indexes = {
            index["name"]: index["column_names"]
            for index in inspector.get_indexes("durable_operations")
        }
        assert set(operation_indexes) >= {
            "ix_durable_operation_recovery_scan",
            "ix_durable_operation_workspace_dead_letter",
            "ix_durable_operation_workspace_replay_source",
        }
        assert operation_indexes["ix_durable_operation_recovery_scan"] == [
            "state",
            "recovery_pending",
            "updated_at",
            "id",
        ]
        replay_indexes = {
            index["name"]: index["column_names"]
            for index in inspector.get_indexes("dead_letter_replays")
        }
        assert replay_indexes["ix_dead_letter_replay_source"] == [
            "source_dead_letter_id",
            "replayed_at",
            "id",
        ]
        assert replay_indexes["ix_dead_letter_replay_claim"] == [
            "operation_id",
            "lifecycle_state",
            "claim_token",
        ]
        assert replay_indexes["ix_dead_letter_replay_workspace_source"] == [
            "workspace_id",
            "source_dead_letter_id",
        ]
        assert replay_indexes["ix_dead_letter_replay_workspace_event"] == [
            "workspace_id",
            "replay_event_id",
        ]
        assert replay_indexes["ix_dead_letter_replay_workspace_operation"] == [
            "workspace_id",
            "operation_id",
        ]
        replay_columns = {
            column["name"]: column["type"]
            for column in inspector.get_columns("dead_letter_replays")
        }
        for column_name in ("replayed_at", "prepared_at", "claimed_at", "completed_at"):
            assert isinstance(replay_columns[column_name], DATETIME)
            assert replay_columns[column_name].fsp == MYSQL_DATETIME_FSP
        assert {
            "lifecycle_state",
            "operation_id",
            "preparation_kind",
            "work_kind",
            "prepared_operation_version",
            "claim_token",
            "claimed_operation_version",
            "completed_operation_version",
        } <= set(replay_columns)
        assert {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("dead_letter_replays")
        } >= {
            "uq_dead_letter_replay_attempt",
            "uq_dead_letter_replay_event",
        }
        assert {
            foreign_key["name"] for foreign_key in inspector.get_foreign_keys("dead_letter_replays")
        } >= {
            "fk_dead_letter_replay_source",
            "fk_dead_letter_replay_event",
            "fk_dead_letter_replay_operation",
        }
        replay_foreign_keys = {
            foreign_key["name"]: foreign_key
            for foreign_key in inspector.get_foreign_keys("dead_letter_replays")
        }
        for constraint_name, child_column in (
            ("fk_dead_letter_replay_source", "source_dead_letter_id"),
            ("fk_dead_letter_replay_event", "replay_event_id"),
            ("fk_dead_letter_replay_operation", "operation_id"),
        ):
            assert replay_foreign_keys[constraint_name]["constrained_columns"] == [
                "workspace_id",
                child_column,
            ]
            assert replay_foreign_keys[constraint_name]["referred_columns"] == [
                "workspace_id",
                "id",
            ]
        for table_name, unique_name, foreign_key_name, child_index, check_name in (
            (
                "dead_letter_messages",
                "uq_dead_letter_workspace_id",
                "fk_dead_letter_source",
                "ix_dead_letter_workspace_source",
                "ck_dead_letter_source_workspace",
            ),
            (
                "outbox_events",
                "uq_outbox_workspace_id",
                "fk_outbox_source_dead_letter",
                "ix_outbox_workspace_source_dead_letter",
                "ck_outbox_source_workspace",
            ),
        ):
            assert unique_name in {
                constraint["name"] for constraint in inspector.get_unique_constraints(table_name)
            }
            ownership_foreign_key = next(
                foreign_key
                for foreign_key in inspector.get_foreign_keys(table_name)
                if foreign_key["name"] == foreign_key_name
            )
            assert ownership_foreign_key["constrained_columns"] == [
                "workspace_id",
                "source_dead_letter_id",
            ]
            assert ownership_foreign_key["referred_columns"] == ["workspace_id", "id"]
            assert child_index in {index["name"] for index in inspector.get_indexes(table_name)}
            assert check_name in {
                constraint["name"] for constraint in inspector.get_check_constraints(table_name)
            }

        command.downgrade(config, "9a7e3c1f5b20")
        downgraded_tables = set(inspect(engine).get_table_names())
        assert "durable_operations" not in downgraded_tables
        assert "dead_letter_replays" not in downgraded_tables
        assert {"products", "skus", "outbox_events", "dead_letter_messages"} <= downgraded_tables
        assert _workspace_collations(engine) == {
            table_name: PARENT_IDENTITY_COLLATION for table_name in PARENT_WORKSPACE_ID_TABLES
        }
        assert (
            _column_collation(
                engine,
                table_name="idempotency_keys",
                column_name="scope",
            )
            == PARENT_IDENTITY_COLLATION
        )

        now = datetime(2026, 7, 23, 14, 0, 0, 123456, tzinfo=UTC)
        provenance_cases = (
            ("valid", "workspace-json-string", "workspace-json-string"),
            ("leading-space", " workspace-json-string", None),
            ("trailing-space", "workspace-json-string ", None),
            ("null", None, None),
            ("number", 42, None),
            ("object", {"nested": "workspace-object"}, None),
            ("array", ["workspace-array"], None),
            ("empty", "", None),
            ("blank", "   ", None),
            ("tab-only", "\t\t", None),
            ("newline-only", "\r\n", None),
            ("mixed-ascii-blank", " \t\r\n\v\f ", None),
            (
                "ascii-surrounded",
                "\t\r\n workspace-ascii-canonical \v\f",
                None,
            ),
            ("unicode-cjk", "工作区", None),
            ("unicode-nfc", "café", None),
            ("unicode-nfd", "cafe\u0301", None),
            ("oversized", "w" * 129, None),
        )
        raw_provenance_cases = (
            ("raw-valid", '{"workspace_id":"workspace-raw-valid"}', "workspace-raw-valid"),
            ("raw-leading", '{"workspace_id":" workspace-raw-leading"}', None),
            ("raw-trailing", '{"workspace_id":"workspace-raw-trailing "}', None),
            ("raw-lead-tab", '{"workspace_id":"\\tworkspace-raw"}', None),
            ("raw-tail-tab", '{"workspace_id":"workspace-raw\\t"}', None),
            ("raw-lead-nl", '{"workspace_id":"\\nworkspace-raw"}', None),
            ("raw-tail-nl", '{"workspace_id":"workspace-raw\\n"}', None),
            ("raw-unicode", '{"workspace_id":"工作区"}', None),
        )
        with engine.begin() as connection:
            connection.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
            connection.execute(text("DELETE FROM dead_letter_messages"))
            connection.execute(text("DELETE FROM outbox_events"))
            connection.execute(text("DELETE FROM workflows"))
            connection.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
            connection.execute(
                text(
                    """
                    INSERT INTO workflows (
                        id, workspace_id, created_by, workflow_type, status,
                        retention_status, current_node, version, input_json,
                        result_json, expires_at, cancellation_requested_at,
                        created_at, updated_at
                    ) VALUES (
                        :id, :workspace_id, 'migration-admin', 'asset_generation',
                        'QUEUED', 'ACTIVE', NULL, 1, :input_json, NULL,
                        :expires_at, NULL, :created_at, :updated_at
                    )
                    """
                ),
                {
                    "id": "workflow-migration-backfill",
                    "workspace_id": "workspace-migration-backfill",
                    "input_json": json.dumps({"source": "phase1"}),
                    "expires_at": now + timedelta(days=1),
                    "created_at": now,
                    "updated_at": now,
                },
            )
            event_seeds = (
                (
                    (
                        "event-migration-workflow",
                        "workflow",
                        "workflow-migration-backfill",
                        json.dumps(
                            {
                                "workflow_id": "workflow-migration-backfill",
                                "action": "recover",
                            }
                        ),
                    ),
                    (
                        "event-migration-payload",
                        "legacy",
                        "legacy-with-workspace-payload",
                        '{"workspace_id":"workspace-payload-backfill"}',
                    ),
                    (
                        "event-migration-orphan",
                        "legacy",
                        "legacy-orphan",
                        '{"legacy_id":"legacy-orphan"}',
                    ),
                )
                + tuple(
                    (
                        f"event-json-{case_name}",
                        "legacy",
                        f"legacy-json-{case_name}",
                        json.dumps({"workspace_id": value}),
                    )
                    for case_name, value, _expected in provenance_cases
                )
                + tuple(
                    (
                        f"event-json-{case_name}",
                        "legacy",
                        f"legacy-json-{case_name}",
                        payload_json,
                    )
                    for case_name, payload_json, _expected in raw_provenance_cases
                )
            )
            for event_id, aggregate_type, aggregate_id, payload_json in event_seeds:
                connection.execute(
                    text(
                        """
                        INSERT INTO outbox_events (
                            id, aggregate_type, aggregate_id, event_type,
                            schema_version, aggregate_version, trace_id,
                            payload_json, occurred_at, available_at, published_at,
                            publish_attempts, lock_owner, lock_token, locked_until,
                            last_error
                        ) VALUES (
                            :id, :aggregate_type, :aggregate_id, 'legacy.event',
                            1, 1, 'migration-trace', :payload_json,
                            :occurred_at, :available_at, NULL, 0,
                            NULL, NULL, NULL, NULL
                        )
                        """
                    ),
                    {
                        "id": event_id,
                        "aggregate_type": aggregate_type,
                        "aggregate_id": aggregate_id,
                        "payload_json": payload_json,
                        "occurred_at": now,
                        "available_at": now,
                    },
                )
            dead_letter_seeds = (
                (
                    (
                        "dead-letter-migration-derived",
                        "event-migration-workflow",
                        '{"workflow_id":"workflow-migration-backfill"}',
                    ),
                    (
                        "dead-letter-migration-orphan",
                        "missing-event-migration-orphan",
                        '{"legacy_id":"legacy-orphan"}',
                    ),
                )
                + tuple(
                    (
                        f"dead-letter-json-{case_name}",
                        f"missing-json-{case_name}",
                        json.dumps({"workspace_id": value}),
                    )
                    for case_name, value, _expected in provenance_cases
                )
                + tuple(
                    (
                        f"dead-letter-json-{case_name}",
                        f"missing-json-{case_name}",
                        payload_json,
                    )
                    for case_name, payload_json, _expected in raw_provenance_cases
                )
            )
            for dead_letter_id, message_id, payload_json in dead_letter_seeds:
                connection.execute(
                    text(
                        """
                        INSERT INTO dead_letter_messages (
                            id, consumer, message_id, event_type, payload_json,
                            reason, error_class, error_message, attempt_count,
                            original_created_at, created_at, replayed_at
                        ) VALUES (
                            :id, 'migration-worker', :message_id, 'legacy.event',
                            :payload_json, 'legacy failure', NULL, NULL, 1,
                            :original_created_at, :created_at, NULL
                        )
                        """
                    ),
                    {
                        "id": dead_letter_id,
                        "message_id": message_id,
                        "payload_json": payload_json,
                        "original_created_at": now,
                        "created_at": now,
                    },
                )

        command.upgrade(config, "head")
        upgraded_tables = set(inspect(engine).get_table_names())
        assert {"durable_operations", "dead_letter_replays", "products", "skus"} <= upgraded_tables
        assert _workspace_collations(engine) == {
            table_name: WORKSPACE_ID_COLLATION for table_name in WORKSPACE_ID_TABLES
        }
        assert (
            _column_collation(
                engine,
                table_name="idempotency_keys",
                column_name="scope",
            )
            == WORKSPACE_ID_COLLATION
        )
        with engine.connect() as connection:
            event_workspaces = {
                row.id: row.workspace_id
                for row in connection.execute(
                    text(
                        """
                        SELECT id, workspace_id
                        FROM outbox_events
                        WHERE id LIKE 'event-migration-%'
                           OR id LIKE 'event-json-%'
                        """
                    )
                )
            }
            dead_letter_workspaces = {
                row.id: row.workspace_id
                for row in connection.execute(
                    text(
                        """
                        SELECT id, workspace_id
                        FROM dead_letter_messages
                        WHERE id LIKE 'dead-letter-migration-%'
                           OR id LIKE 'dead-letter-json-%'
                        """
                    )
                )
            }
        expected_event_workspaces = {
            "event-migration-workflow": "workspace-migration-backfill",
            "event-migration-payload": "workspace-payload-backfill",
            "event-migration-orphan": None,
            **{
                f"event-json-{case_name}": expected
                for case_name, _value, expected in provenance_cases
            },
            **{
                f"event-json-{case_name}": expected
                for case_name, _payload_json, expected in raw_provenance_cases
            },
        }
        expected_dead_letter_workspaces = {
            "dead-letter-migration-derived": "workspace-migration-backfill",
            "dead-letter-migration-orphan": None,
            **{
                f"dead-letter-json-{case_name}": expected
                for case_name, _value, expected in provenance_cases
            },
            **{
                f"dead-letter-json-{case_name}": expected
                for case_name, _payload_json, expected in raw_provenance_cases
            },
        }
        assert event_workspaces == expected_event_workspaces
        assert dead_letter_workspaces == expected_dead_letter_workspaces

        command.downgrade(config, "9a7e3c1f5b20")
        command.upgrade(config, "head")
        assert _workspace_collations(engine) == {
            table_name: WORKSPACE_ID_COLLATION for table_name in WORKSPACE_ID_TABLES
        }
        assert (
            _column_collation(
                engine,
                table_name="idempotency_keys",
                column_name="scope",
            )
            == WORKSPACE_ID_COLLATION
        )
        with engine.connect() as connection:
            reupgraded_event_workspaces = {
                row.id: row.workspace_id
                for row in connection.execute(
                    text(
                        """
                        SELECT id, workspace_id
                        FROM outbox_events
                        WHERE id LIKE 'event-migration-%'
                           OR id LIKE 'event-json-%'
                        """
                    )
                )
            }
            reupgraded_dead_letter_workspaces = {
                row.id: row.workspace_id
                for row in connection.execute(
                    text(
                        """
                        SELECT id, workspace_id
                        FROM dead_letter_messages
                        WHERE id LIKE 'dead-letter-migration-%'
                           OR id LIKE 'dead-letter-json-%'
                        """
                    )
                )
            }
        assert reupgraded_event_workspaces == expected_event_workspaces
        assert reupgraded_dead_letter_workspaces == expected_dead_letter_workspaces
    finally:
        engine.dispose()


def test_operation_migration_refuses_unsafe_workspace_identity_downgrade(
    integration_settings,
    monkeypatch,
) -> None:
    source_url = make_url(integration_settings.mysql_dsn)
    database_name = f"commercevision_ticket02_identity_{uuid.uuid4().hex[:8]}"
    admin_url = source_url.set(database="mysql")
    test_url = source_url.set(database=database_name)
    admin_engine = create_engine(admin_url)
    engine = create_engine(test_url)
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    monkeypatch.setenv("CV_MYSQL_DSN", test_url.render_as_string(hide_password=False))
    try:
        with admin_engine.begin() as connection:
            connection.execute(
                text(
                    f"CREATE DATABASE `{database_name}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
                )
            )
        command.upgrade(config, "head")
        now = datetime(2026, 7, 24, 4, 0, 0, 123456, tzinfo=UTC)
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO workflows (
                        id, workspace_id, created_by, workflow_type, status,
                        retention_status, current_node, version, input_json,
                        result_json, expires_at, cancellation_requested_at,
                        created_at, updated_at
                    ) VALUES (
                        :id, :workspace_id, 'identity-admin', 'asset_generation',
                        'QUEUED', 'ACTIVE', NULL, 1, :input_json, NULL,
                        :expires_at, NULL, :created_at, :updated_at
                    )
                    """
                ),
                [
                    {
                        "id": "workflow-identity-upper",
                        "workspace_id": "Workspace-Collision",
                        "input_json": json.dumps({"case": "upper"}),
                        "expires_at": now + timedelta(days=1),
                        "created_at": now,
                        "updated_at": now,
                    },
                    {
                        "id": "workflow-identity-lower",
                        "workspace_id": "workspace-collision",
                        "input_json": json.dumps({"case": "lower"}),
                        "expires_at": now + timedelta(days=1),
                        "created_at": now,
                        "updated_at": now,
                    },
                ],
            )

        with pytest.raises(RuntimeError, match="workspace identities"):
            command.downgrade(config, "9a7e3c1f5b20")

        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "b1c8e4f2a703"
            )
            assert "durable_operations" in inspect(connection).get_table_names()

        with engine.begin() as connection:
            connection.execute(text("DELETE FROM workflows"))
            connection.execute(
                text(
                    """
                    INSERT INTO idempotency_keys (
                        id, scope, key_hash, request_hash, resource_type,
                        resource_id, response_json, status, created_at, expires_at
                    ) VALUES (
                        :id, :scope, :key_hash, :request_hash, 'workflow',
                        :resource_id, NULL, 'PENDING', :created_at, :expires_at
                    )
                    """
                ),
                [
                    {
                        "id": "idempotency-identity-upper",
                        "scope": "workflow:create:Workspace-Collision",
                        "key_hash": "a" * 64,
                        "request_hash": "b" * 64,
                        "resource_id": "workflow-identity-upper",
                        "created_at": now,
                        "expires_at": now + timedelta(days=1),
                    },
                    {
                        "id": "idempotency-identity-lower",
                        "scope": "workflow:create:workspace-collision",
                        "key_hash": "a" * 64,
                        "request_hash": "c" * 64,
                        "resource_id": "workflow-identity-lower",
                        "created_at": now,
                        "expires_at": now + timedelta(days=1),
                    },
                ],
            )

        with pytest.raises(RuntimeError, match="idempotency scopes"):
            command.downgrade(config, "9a7e3c1f5b20")

        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "b1c8e4f2a703"
            )
            assert "durable_operations" in inspect(connection).get_table_names()
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS `{database_name}`"))
        admin_engine.dispose()


def test_operation_migration_refuses_invalid_existing_workspace_before_ddl(
    integration_settings,
    monkeypatch,
) -> None:
    source_url = make_url(integration_settings.mysql_dsn)
    database_name = f"commercevision_ticket02_invalid_workspace_{uuid.uuid4().hex[:8]}"
    admin_url = source_url.set(database="mysql")
    test_url = source_url.set(database=database_name)
    admin_engine = create_engine(admin_url)
    engine = create_engine(test_url)
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    monkeypatch.setenv("CV_MYSQL_DSN", test_url.render_as_string(hide_password=False))
    try:
        with admin_engine.begin() as connection:
            connection.execute(
                text(
                    f"CREATE DATABASE `{database_name}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
                )
            )
        command.upgrade(config, "9a7e3c1f5b20")
        now = datetime(2026, 7, 24, 5, 0, 0, 123456, tzinfo=UTC)
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO workflows (
                        id, workspace_id, created_by, workflow_type, status,
                        retention_status, current_node, version, input_json,
                        result_json, expires_at, cancellation_requested_at,
                        created_at, updated_at
                    ) VALUES (
                        'workflow-invalid-existing-workspace', :workspace_id,
                        'migration-admin', 'asset_generation', 'QUEUED',
                        'ACTIVE', NULL, 1, :input_json, NULL, :expires_at,
                        NULL, :created_at, :updated_at
                    )
                    """
                ),
                {
                    "workspace_id": "legacy-workspace\n",
                    "input_json": '{"source":"legacy"}',
                    "expires_at": now + timedelta(days=1),
                    "created_at": now,
                    "updated_at": now,
                },
            )

        with pytest.raises(RuntimeError, match="existing workspace identity"):
            command.upgrade(config, "head")

        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "9a7e3c1f5b20"
            )
            assert "durable_operations" not in inspect(connection).get_table_names()
            assert (
                connection.execute(
                    text(
                        "SELECT workspace_id FROM workflows "
                        "WHERE id = 'workflow-invalid-existing-workspace'"
                    )
                ).scalar_one()
                == "legacy-workspace\n"
            )
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS `{database_name}`"))
        admin_engine.dispose()


def test_operation_migration_leaves_malformed_legacy_json_unscoped(
    integration_settings,
    monkeypatch,
) -> None:
    source_url = make_url(integration_settings.mysql_dsn)
    database_name = f"commercevision_ticket02_malformed_{uuid.uuid4().hex[:8]}"
    admin_url = source_url.set(database="mysql")
    test_url = source_url.set(database=database_name)
    admin_engine = create_engine(admin_url)
    engine = create_engine(test_url)
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    monkeypatch.setenv("CV_MYSQL_DSN", test_url.render_as_string(hide_password=False))
    try:
        with admin_engine.begin() as connection:
            connection.execute(
                text(
                    f"CREATE DATABASE `{database_name}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
                )
            )
        command.upgrade(config, "9a7e3c1f5b20")
        now = datetime(2026, 7, 24, 1, 0, 0, 123456, tzinfo=UTC)
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE outbox_events MODIFY payload_json LONGTEXT NOT NULL")
            )
            connection.execute(
                text("ALTER TABLE dead_letter_messages MODIFY payload_json LONGTEXT NOT NULL")
            )
            connection.execute(
                text(
                    """
                    INSERT INTO outbox_events (
                        id, aggregate_type, aggregate_id, event_type,
                        schema_version, aggregate_version, trace_id,
                        payload_json, occurred_at, available_at, published_at,
                        publish_attempts, lock_owner, lock_token, locked_until,
                        last_error
                    ) VALUES (
                        'event-malformed-json', 'legacy', 'legacy-malformed',
                        'legacy.event', 1, 1, 'malformed-trace',
                        :payload_json, :occurred_at, :available_at, NULL, 0,
                        NULL, NULL, NULL, NULL
                    )
                    """
                ),
                {
                    "payload_json": '{"workspace_id": "unterminated"',
                    "occurred_at": now,
                    "available_at": now,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO dead_letter_messages (
                        id, consumer, message_id, event_type, payload_json,
                        reason, error_class, error_message, attempt_count,
                        original_created_at, created_at, replayed_at
                    ) VALUES (
                        'dead-letter-malformed-json', 'migration-worker',
                        'missing-malformed-json', 'legacy.event',
                        :payload_json, 'legacy malformed failure',
                        NULL, NULL, 1, :original_created_at, :created_at, NULL
                    )
                    """
                ),
                {
                    "payload_json": '{"workspace_id": [malformed]}',
                    "original_created_at": now,
                    "created_at": now,
                },
            )

        for _round in range(2):
            command.upgrade(config, "head")
            with engine.connect() as connection:
                assert (
                    connection.execute(
                        text(
                            "SELECT workspace_id FROM outbox_events "
                            "WHERE id = 'event-malformed-json'"
                        )
                    ).scalar_one()
                    is None
                )
                assert (
                    connection.execute(
                        text(
                            "SELECT workspace_id FROM dead_letter_messages "
                            "WHERE id = 'dead-letter-malformed-json'"
                        )
                    ).scalar_one()
                    is None
                )
            if _round == 0:
                command.downgrade(config, "9a7e3c1f5b20")
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS `{database_name}`"))
        admin_engine.dispose()
