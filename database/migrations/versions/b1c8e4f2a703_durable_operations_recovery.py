"""durable operations and recovery control plane

Revision ID: b1c8e4f2a703
Revises: 9a7e3c1f5b20
Create Date: 2026-07-23 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "b1c8e4f2a703"
down_revision: str | Sequence[str] | None = "9a7e3c1f5b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WORKSPACE_ID_COLLATION = "utf8mb4_0900_bin"
_WORKSPACE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
_EXISTING_WORKSPACE_COLUMNS = (
    ("audit_events", False),
    ("catalog_external_identities", False),
    ("products", False),
    ("skus", False),
    ("workflows", False),
)
_ALL_WORKSPACE_TABLES = (
    "audit_events",
    "catalog_external_identities",
    "dead_letter_messages",
    "dead_letter_replays",
    "durable_operations",
    "outbox_events",
    "products",
    "skus",
    "workflows",
)


def _workspace_id_type(*, exact: bool = True) -> sa.String:
    return sa.String(
        length=128,
        collation=_WORKSPACE_ID_COLLATION if exact else None,
    )


def _workspace_id_sql_predicate(expression: str) -> str:
    return (
        f"REGEXP_LIKE({expression}, '{_WORKSPACE_ID_PATTERN}', 'c') "
        "AND CHAR_LENGTH("
        f"REGEXP_SUBSTR({expression}, '{_WORKSPACE_ID_PATTERN}', 1, 1, 'c')"
        f") = CHAR_LENGTH({expression})"
    )


def _legacy_workspace_json_expression() -> str:
    return (
        "JSON_UNQUOTE(JSON_EXTRACT("
        "CASE WHEN JSON_VALID(payload_json) "
        "THEN payload_json ELSE JSON_OBJECT() END, "
        "'$.workspace_id'))"
    )


def _assert_existing_workspace_identities_valid() -> None:
    invalid_sources = "\nUNION ALL\n".join(
        (
            f"SELECT '{table_name}' AS table_name, workspace_id "
            f"FROM {table_name} "
            f"WHERE NOT ({_workspace_id_sql_predicate('workspace_id')})"
        )
        for table_name, _nullable in _EXISTING_WORKSPACE_COLUMNS
    )
    invalid = (
        op.get_bind()
        .execute(
            sa.text(
                f"""
            SELECT table_name, workspace_id
            FROM (
                {invalid_sources}
            ) AS invalid_workspace_identities
            LIMIT 1
            """
            )
        )
        .first()
    )
    if invalid is not None:
        raise RuntimeError(
            f"cannot upgrade: existing workspace identity violates {_WORKSPACE_ID_PATTERN}"
        )


def _assert_workspace_ownership_safe() -> None:
    violation = (
        op.get_bind()
        .execute(
            sa.text(
                """
            SELECT ownership_edge, child_id
            FROM (
                SELECT
                    'dead_letter_messages.source_dead_letter_id' AS ownership_edge,
                    child.id AS child_id
                FROM dead_letter_messages AS child
                WHERE child.source_dead_letter_id IS NOT NULL
                  AND (
                    child.workspace_id IS NULL
                    OR NOT EXISTS (
                        SELECT 1
                        FROM dead_letter_messages AS parent
                        WHERE parent.id = child.source_dead_letter_id
                          AND parent.workspace_id = child.workspace_id
                    )
                  )
                UNION ALL
                SELECT
                    'outbox_events.source_dead_letter_id',
                    child.id
                FROM outbox_events AS child
                WHERE child.source_dead_letter_id IS NOT NULL
                  AND (
                    child.workspace_id IS NULL
                    OR NOT EXISTS (
                        SELECT 1
                        FROM dead_letter_messages AS parent
                        WHERE parent.id = child.source_dead_letter_id
                          AND parent.workspace_id = child.workspace_id
                    )
                  )
                UNION ALL
                SELECT
                    'durable_operations.dead_letter_id',
                    child.id
                FROM durable_operations AS child
                WHERE child.dead_letter_id IS NOT NULL
                  AND NOT EXISTS (
                    SELECT 1
                    FROM dead_letter_messages AS parent
                    WHERE parent.id = child.dead_letter_id
                      AND parent.workspace_id = child.workspace_id
                  )
                UNION ALL
                SELECT
                    'durable_operations.replay_source_dead_letter_id',
                    child.id
                FROM durable_operations AS child
                WHERE child.replay_source_dead_letter_id IS NOT NULL
                  AND NOT EXISTS (
                    SELECT 1
                    FROM dead_letter_messages AS parent
                    WHERE parent.id = child.replay_source_dead_letter_id
                      AND parent.workspace_id = child.workspace_id
                  )
            ) AS ownership_violations
            LIMIT 1
            """
            )
        )
        .first()
    )
    if violation is not None:
        raise RuntimeError(
            "cannot upgrade: legacy Ticket 02 provenance violates workspace ownership"
        )


def _set_existing_identity_collations(*, exact: bool) -> None:
    op.drop_constraint("fk_skus_workspace_product", "skus", type_="foreignkey")
    for table_name, nullable in _EXISTING_WORKSPACE_COLUMNS:
        op.alter_column(
            table_name,
            "workspace_id",
            existing_type=sa.String(length=128),
            type_=_workspace_id_type(exact=exact),
            existing_nullable=nullable,
        )
    op.alter_column(
        "idempotency_keys",
        "scope",
        existing_type=sa.String(length=160),
        type_=sa.String(
            length=160,
            collation=_WORKSPACE_ID_COLLATION if exact else None,
        ),
        existing_nullable=False,
    )
    op.create_foreign_key(
        "fk_skus_workspace_product",
        "skus",
        "products",
        ["workspace_id", "product_id"],
        ["workspace_id", "id"],
        ondelete="RESTRICT",
    )


def _assert_legacy_identity_downgrade_safe() -> None:
    workspace_sources = "\nUNION ALL\n".join(
        (
            "SELECT "
            "workspace_id COLLATE utf8mb4_0900_ai_ci AS legacy_identity, "
            "HEX(workspace_id) AS exact_identity "
            f"FROM {table_name}"
        )
        for table_name in _ALL_WORKSPACE_TABLES
    )
    workspace_collision = (
        op.get_bind()
        .execute(
            sa.text(
                f"""
            SELECT legacy_identity
            FROM (
                {workspace_sources}
            ) AS workspace_identities
            WHERE exact_identity IS NOT NULL
            GROUP BY legacy_identity
            HAVING COUNT(DISTINCT exact_identity) > 1
            LIMIT 1
            """
            )
        )
        .first()
    )
    if workspace_collision is not None:
        raise RuntimeError(
            "cannot downgrade: exact workspace identities collide under utf8mb4_0900_ai_ci"
        )

    scope_collision = (
        op.get_bind()
        .execute(
            sa.text(
                """
            SELECT scope COLLATE utf8mb4_0900_ai_ci AS legacy_scope, key_hash
            FROM idempotency_keys
            GROUP BY legacy_scope, key_hash
            HAVING COUNT(DISTINCT HEX(scope)) > 1
            LIMIT 1
            """
            )
        )
        .first()
    )
    if scope_collision is not None:
        raise RuntimeError(
            "cannot downgrade: exact idempotency scopes collide under utf8mb4_0900_ai_ci"
        )


def upgrade() -> None:
    legacy_workspace = _legacy_workspace_json_expression()
    _assert_existing_workspace_identities_valid()
    _set_existing_identity_collations(exact=True)
    op.create_table(
        "durable_operations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", _workspace_id_type(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=False),
        sa.Column("target_version", sa.Integer(), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("input_ref", sa.String(length=512), nullable=True),
        sa.Column("output_ref", sa.String(length=512), nullable=True),
        sa.Column("provider_request_id", sa.String(length=256), nullable=True),
        sa.Column("state", sa.String(length=40), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_token", sa.String(length=36), nullable=True),
        sa.Column("lease_expires_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("execution_deadline_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("reconciliation_attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_reconciliation_attempts", sa.Integer(), nullable=False),
        sa.Column("next_reconciliation_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("reconciliation_started_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("reconciliation_deadline_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("reconciliation_required", sa.Boolean(), nullable=False),
        sa.Column("reconciliation_outcome", sa.String(length=40), nullable=False),
        sa.Column("dead_letter_id", sa.String(length=36), nullable=True),
        sa.Column("replay_source_dead_letter_id", sa.String(length=36), nullable=True),
        sa.Column(
            "replay_attempt",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "recovery_generation",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "recovery_consumed_generation",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "recovery_pending",
            sa.Boolean(),
            sa.Computed(
                "recovery_generation <> recovery_consumed_generation",
                persisted=True,
            ),
            nullable=True,
        ),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_category", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_retryable", sa.Boolean(), nullable=True),
        sa.Column("error_provider_request_id", sa.String(length=256), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("last_attempt_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("started_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("completed_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "kind",
            "target_type",
            "target_id",
            "target_version",
            "input_hash",
            name="uq_durable_operation_logical",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_durable_operation_workspace_id",
        ),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_durable_operation_ready",
        "durable_operations",
        ["state", "next_attempt_at", "next_reconciliation_at", "lease_expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_durable_operation_workspace_created",
        "durable_operations",
        ["workspace_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_durable_operation_recovery_scan",
        "durable_operations",
        ["state", "recovery_pending", "updated_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_durable_operation_workspace_dead_letter",
        "durable_operations",
        ["workspace_id", "dead_letter_id"],
        unique=False,
    )
    op.create_index(
        "ix_durable_operation_workspace_replay_source",
        "durable_operations",
        ["workspace_id", "replay_source_dead_letter_id"],
        unique=False,
    )
    op.add_column(
        "outbox_events",
        sa.Column("workspace_id", _workspace_id_type(), nullable=True),
    )
    op.add_column(
        "outbox_events",
        sa.Column("source_dead_letter_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "outbox_events",
        sa.Column(
            "replay_attempt",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "dead_letter_messages",
        sa.Column("workspace_id", _workspace_id_type(), nullable=True),
    )
    op.add_column(
        "dead_letter_messages",
        sa.Column("source_dead_letter_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "dead_letter_messages",
        sa.Column(
            "replay_attempt",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.execute(
        sa.text(
            """
            UPDATE outbox_events AS event
            INNER JOIN workflows AS workflow
                ON event.aggregate_type = 'workflow'
               AND event.aggregate_id = workflow.id
            SET event.workspace_id = workflow.workspace_id
            WHERE event.workspace_id IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            UPDATE outbox_events
            SET workspace_id = {legacy_workspace}
            WHERE workspace_id IS NULL
              AND JSON_VALID(payload_json) = 1
              AND JSON_TYPE(
                    JSON_EXTRACT(
                        CASE
                            WHEN JSON_VALID(payload_json) THEN payload_json
                            ELSE JSON_OBJECT()
                        END,
                        '$.workspace_id'
                    )
                  ) = 'STRING'
              AND {_workspace_id_sql_predicate(legacy_workspace)}
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE dead_letter_messages AS dead_letter
            INNER JOIN outbox_events AS event
                ON dead_letter.message_id = event.id
            SET dead_letter.workspace_id = event.workspace_id
            WHERE dead_letter.workspace_id IS NULL
              AND event.workspace_id IS NOT NULL
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            UPDATE dead_letter_messages
            SET workspace_id = {legacy_workspace}
            WHERE workspace_id IS NULL
              AND JSON_VALID(payload_json) = 1
              AND JSON_TYPE(
                    JSON_EXTRACT(
                        CASE
                            WHEN JSON_VALID(payload_json) THEN payload_json
                            ELSE JSON_OBJECT()
                        END,
                        '$.workspace_id'
                    )
                  ) = 'STRING'
              AND {_workspace_id_sql_predicate(legacy_workspace)}
            """
        )
    )
    _assert_workspace_ownership_safe()
    op.create_unique_constraint(
        "uq_dead_letter_workspace_id",
        "dead_letter_messages",
        ["workspace_id", "id"],
    )
    op.create_unique_constraint(
        "uq_outbox_workspace_id",
        "outbox_events",
        ["workspace_id", "id"],
    )
    op.create_check_constraint(
        "ck_dead_letter_source_workspace",
        "dead_letter_messages",
        "source_dead_letter_id IS NULL OR workspace_id IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_outbox_source_workspace",
        "outbox_events",
        "source_dead_letter_id IS NULL OR workspace_id IS NOT NULL",
    )
    op.create_index(
        "ix_dead_letter_workspace_created",
        "dead_letter_messages",
        ["workspace_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_dead_letter_workspace_source",
        "dead_letter_messages",
        ["workspace_id", "source_dead_letter_id"],
        unique=False,
    )
    op.create_index(
        "ix_outbox_workspace_source_dead_letter",
        "outbox_events",
        ["workspace_id", "source_dead_letter_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_dead_letter_source",
        "dead_letter_messages",
        "dead_letter_messages",
        ["workspace_id", "source_dead_letter_id"],
        ["workspace_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_outbox_source_dead_letter",
        "outbox_events",
        "dead_letter_messages",
        ["workspace_id", "source_dead_letter_id"],
        ["workspace_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_durable_operation_dead_letter",
        "durable_operations",
        "dead_letter_messages",
        ["workspace_id", "dead_letter_id"],
        ["workspace_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_durable_operation_replay_source",
        "durable_operations",
        "dead_letter_messages",
        ["workspace_id", "replay_source_dead_letter_id"],
        ["workspace_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_table(
        "dead_letter_replays",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_dead_letter_id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", _workspace_id_type(), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.String(length=512), nullable=False),
        sa.Column("replayed_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("replay_attempt", sa.Integer(), nullable=False),
        sa.Column("replay_event_id", sa.String(length=36), nullable=False),
        sa.Column(
            "lifecycle_state",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'RECORDED'"),
        ),
        sa.Column("operation_id", sa.String(length=36), nullable=True),
        sa.Column("preparation_kind", sa.String(length=24), nullable=True),
        sa.Column("work_kind", sa.String(length=20), nullable=True),
        sa.Column("prepared_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("prepared_operation_version", sa.Integer(), nullable=True),
        sa.Column("claim_token", sa.String(length=36), nullable=True),
        sa.Column("claimed_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("claimed_operation_version", sa.Integer(), nullable=True),
        sa.Column("completed_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("completed_operation_version", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id", "source_dead_letter_id"],
            ["dead_letter_messages.workspace_id", "dead_letter_messages.id"],
            name="fk_dead_letter_replay_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "replay_event_id"],
            ["outbox_events.workspace_id", "outbox_events.id"],
            name="fk_dead_letter_replay_event",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "operation_id"],
            ["durable_operations.workspace_id", "durable_operations.id"],
            name="fk_dead_letter_replay_operation",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_dead_letter_id",
            "replay_attempt",
            name="uq_dead_letter_replay_attempt",
        ),
        sa.UniqueConstraint(
            "replay_event_id",
            name="uq_dead_letter_replay_event",
        ),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_dead_letter_replay_source",
        "dead_letter_replays",
        ["source_dead_letter_id", "replayed_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_dead_letter_replay_claim",
        "dead_letter_replays",
        ["operation_id", "lifecycle_state", "claim_token"],
        unique=False,
    )
    op.create_index(
        "ix_dead_letter_replay_workspace_source",
        "dead_letter_replays",
        ["workspace_id", "source_dead_letter_id"],
        unique=False,
    )
    op.create_index(
        "ix_dead_letter_replay_workspace_event",
        "dead_letter_replays",
        ["workspace_id", "replay_event_id"],
        unique=False,
    )
    op.create_index(
        "ix_dead_letter_replay_workspace_operation",
        "dead_letter_replays",
        ["workspace_id", "operation_id"],
        unique=False,
    )


def downgrade() -> None:
    _assert_legacy_identity_downgrade_safe()
    op.drop_index("ix_dead_letter_replay_source", table_name="dead_letter_replays")
    op.drop_table("dead_letter_replays")
    op.drop_table("durable_operations")
    op.drop_constraint(
        "fk_outbox_source_dead_letter",
        "outbox_events",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_dead_letter_source",
        "dead_letter_messages",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_outbox_workspace_source_dead_letter",
        table_name="outbox_events",
    )
    op.drop_index(
        "ix_dead_letter_workspace_source",
        table_name="dead_letter_messages",
    )
    op.drop_constraint(
        "ck_outbox_source_workspace",
        "outbox_events",
        type_="check",
    )
    op.drop_constraint(
        "ck_dead_letter_source_workspace",
        "dead_letter_messages",
        type_="check",
    )
    op.drop_constraint(
        "uq_outbox_workspace_id",
        "outbox_events",
        type_="unique",
    )
    op.drop_constraint(
        "uq_dead_letter_workspace_id",
        "dead_letter_messages",
        type_="unique",
    )
    op.drop_index(
        "ix_dead_letter_workspace_created",
        table_name="dead_letter_messages",
    )
    op.drop_column("dead_letter_messages", "replay_attempt")
    op.drop_column("dead_letter_messages", "source_dead_letter_id")
    op.drop_column("dead_letter_messages", "workspace_id")
    op.drop_column("outbox_events", "replay_attempt")
    op.drop_column("outbox_events", "source_dead_letter_id")
    op.drop_column("outbox_events", "workspace_id")
    _set_existing_identity_collations(exact=False)
