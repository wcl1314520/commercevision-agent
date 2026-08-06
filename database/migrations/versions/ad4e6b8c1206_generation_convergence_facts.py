"""Add immutable generation Provider, usage, and candidate facts.

Revision ID: ad4e6b8c1206
Revises: fb9e4c6a1205
Create Date: 2026-08-06 21:15:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "ad4e6b8c1206"
down_revision = "fb9e4c6a1205"
branch_labels = None
depends_on = None

_WORKSPACE = sa.String(128, collation="utf8mb4_0900_bin")
_EXACT_3 = sa.String(3, collation="utf8mb4_0900_bin")
_EXACT_16 = sa.String(16, collation="utf8mb4_0900_bin")
_EXACT_24 = sa.String(24, collation="utf8mb4_0900_bin")
_EXACT_32 = sa.String(32, collation="utf8mb4_0900_bin")
_EXACT_36 = sa.String(36, collation="utf8mb4_0900_bin")
_EXACT_40 = sa.String(40, collation="utf8mb4_0900_bin")
_EXACT_64 = sa.String(64, collation="utf8mb4_0900_bin")
_EXACT_128 = sa.String(128, collation="utf8mb4_0900_bin")
_EXACT_256 = sa.String(256, collation="utf8mb4_0900_bin")
_UUID = sa.String(36)
_MONEY = sa.Numeric(20, 6)
_DATETIME = mysql.DATETIME(fsp=6)


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_generation_batch_candidate_identity",
        "generation_batches",
        ["workspace_id", "id", "workflow_id", "creative_plan_version_id"],
    )
    op.create_unique_constraint(
        "uq_candidate_slot_batch_identity",
        "candidate_slots",
        ["workspace_id", "id", "generation_batch_id"],
    )
    op.create_table(
        "generation_dispatch_attempts",
        sa.Column("workspace_id", _WORKSPACE, nullable=False),
        sa.Column("id", _EXACT_36, nullable=False),
        sa.Column("candidate_slot_id", _EXACT_36, nullable=False),
        sa.Column("durable_operation_id", _UUID, nullable=False),
        sa.Column("operation_attempt", sa.Integer(), nullable=False),
        sa.Column("call_index", sa.Integer(), nullable=False),
        sa.Column("endpoint_capability_version_id", _EXACT_36, nullable=False),
        sa.Column("request_sha256", _EXACT_64, nullable=False),
        sa.Column("adapter_configuration_sha256", _EXACT_64, nullable=False),
        sa.Column("idempotency_key_sha256", _EXACT_64, nullable=False),
        sa.Column("state", _EXACT_24, nullable=False),
        sa.Column("outcome", _EXACT_40),
        sa.Column("provider_request_id", _EXACT_256),
        sa.Column("provider_request_id_sha256", _EXACT_64),
        sa.Column("provider_task_id", _EXACT_256),
        sa.Column("provider_task_id_sha256", _EXACT_64),
        sa.Column("created_at", _DATETIME, nullable=False),
        sa.Column("updated_at", _DATETIME, nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "id", name="pk_generation_dispatch_attempt"),
        sa.UniqueConstraint(
            "workspace_id",
            "durable_operation_id",
            "operation_attempt",
            "call_index",
            name="uq_generation_dispatch_attempt_operation",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "candidate_slot_id"],
            ["candidate_slots.workspace_id", "candidate_slots.id"],
            name="fk_generation_dispatch_attempt_slot",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "durable_operation_id"],
            ["durable_operations.workspace_id", "durable_operations.id"],
            name="fk_generation_dispatch_attempt_operation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["endpoint_capability_version_id"],
            ["provider_endpoint_capability_versions.id"],
            name="fk_generation_dispatch_attempt_endpoint",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "operation_attempt > 0 AND call_index BETWEEN 0 AND 7",
            name="ck_generation_dispatch_attempt_index",
        ),
        sa.CheckConstraint(
            "state IN ('DISPATCHING', 'OUTCOME_RECORDED')",
            name="ck_generation_dispatch_attempt_state",
        ),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('CONFIRMED_SUCCESS', 'CONFIRMED_FAILURE', "
            "'CONTENT_REJECTED', 'SAFE_TO_RETRY_PRE_DISPATCH', "
            "'UNKNOWN_AFTER_POSSIBLE_DISPATCH')",
            name="ck_generation_dispatch_attempt_outcome",
        ),
        sa.CheckConstraint(
            "(state = 'DISPATCHING' AND outcome IS NULL "
            "AND provider_request_id IS NULL AND provider_request_id_sha256 IS NULL "
            "AND provider_task_id IS NULL AND provider_task_id_sha256 IS NULL) OR "
            "(state = 'OUTCOME_RECORDED' AND outcome IS NOT NULL "
            "AND (provider_request_id IS NULL) = (provider_request_id_sha256 IS NULL) "
            "AND (provider_task_id IS NULL) = (provider_task_id_sha256 IS NULL))",
            name="ck_generation_dispatch_attempt_facts",
        ),
        sa.CheckConstraint(
            "request_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND adapter_configuration_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND idempotency_key_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND (provider_request_id_sha256 IS NULL OR "
            "provider_request_id_sha256 REGEXP '^[0-9a-f]{64}$') "
            "AND (provider_task_id_sha256 IS NULL OR "
            "provider_task_id_sha256 REGEXP '^[0-9a-f]{64}$')",
            name="ck_generation_dispatch_attempt_hashes",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index(
        "ix_generation_dispatch_attempt_operation",
        "generation_dispatch_attempts",
        ["workspace_id", "durable_operation_id", "operation_attempt", "call_index"],
    )
    op.execute(
        """
        CREATE TRIGGER trg_generation_dispatch_attempt_identity_immutable
        BEFORE UPDATE ON generation_dispatch_attempts
        FOR EACH ROW
        BEGIN
            IF NOT (
                NEW.workspace_id <=> OLD.workspace_id
                AND NEW.id <=> OLD.id
                AND NEW.candidate_slot_id <=> OLD.candidate_slot_id
                AND NEW.durable_operation_id <=> OLD.durable_operation_id
                AND NEW.operation_attempt <=> OLD.operation_attempt
                AND NEW.call_index <=> OLD.call_index
                AND NEW.endpoint_capability_version_id <=> OLD.endpoint_capability_version_id
                AND NEW.request_sha256 <=> OLD.request_sha256
                AND NEW.adapter_configuration_sha256 <=> OLD.adapter_configuration_sha256
                AND NEW.idempotency_key_sha256 <=> OLD.idempotency_key_sha256
                AND NEW.created_at <=> OLD.created_at
            ) THEN
                SIGNAL SQLSTATE '45000'
                SET MESSAGE_TEXT = 'Generation dispatch Attempt identity is immutable';
            END IF;
            IF OLD.provider_request_id IS NOT NULL AND NOT (
                NEW.provider_request_id <=> OLD.provider_request_id
                AND NEW.provider_request_id_sha256 <=> OLD.provider_request_id_sha256
            ) THEN
                SIGNAL SQLSTATE '45000'
                SET MESSAGE_TEXT = 'Generation Provider Request identity is immutable';
            END IF;
            IF OLD.provider_task_id IS NOT NULL AND NOT (
                NEW.provider_task_id <=> OLD.provider_task_id
                AND NEW.provider_task_id_sha256 <=> OLD.provider_task_id_sha256
            ) THEN
                SIGNAL SQLSTATE '45000'
                SET MESSAGE_TEXT = 'Generation Provider Task identity is immutable';
            END IF;
        END
        """
    )
    op.create_table(
        "generation_provider_calls",
        sa.Column("workspace_id", _WORKSPACE, nullable=False),
        sa.Column("id", _EXACT_36, nullable=False),
        sa.Column("candidate_slot_id", _EXACT_36, nullable=False),
        sa.Column("durable_operation_id", _UUID, nullable=False),
        sa.Column("operation_attempt", sa.Integer(), nullable=False),
        sa.Column("call_index", sa.Integer(), nullable=False),
        sa.Column("route_decision_sha256", _EXACT_64, nullable=False),
        sa.Column("endpoint_capability_version_id", _EXACT_36, nullable=False),
        sa.Column("provider", _EXACT_128, nullable=False),
        sa.Column("model", _EXACT_128, nullable=False),
        sa.Column("request_sha256", _EXACT_64, nullable=False),
        sa.Column("idempotency_key_sha256", _EXACT_64, nullable=False),
        sa.Column("outcome", _EXACT_40, nullable=False),
        sa.Column("possible_dispatch", sa.Boolean(), nullable=False),
        sa.Column("provider_request_id_sha256", _EXACT_64),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("observed_at", _DATETIME, nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "id", name="pk_provider_call"),
        sa.UniqueConstraint(
            "workspace_id",
            "durable_operation_id",
            "operation_attempt",
            "call_index",
            name="uq_provider_call_attempt",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "id",
            "candidate_slot_id",
            "endpoint_capability_version_id",
            name="uq_provider_call_slot_identity",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "id",
            "durable_operation_id",
            "endpoint_capability_version_id",
            name="uq_provider_call_operation_identity",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "id"],
            [
                "generation_dispatch_attempts.workspace_id",
                "generation_dispatch_attempts.id",
            ],
            name="fk_provider_call_dispatch_attempt",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "candidate_slot_id"],
            ["candidate_slots.workspace_id", "candidate_slots.id"],
            name="fk_provider_call_slot",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "durable_operation_id"],
            ["durable_operations.workspace_id", "durable_operations.id"],
            name="fk_provider_call_operation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "route_decision_sha256"],
            ["model_route_decisions.workspace_id", "model_route_decisions.decision_sha256"],
            name="fk_provider_call_route_decision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["endpoint_capability_version_id"],
            ["provider_endpoint_capability_versions.id"],
            name="fk_provider_call_endpoint",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "operation_attempt > 0 AND call_index BETWEEN 0 AND 7",
            name="ck_provider_call_attempt",
        ),
        sa.CheckConstraint(
            "outcome IN ('CONFIRMED_SUCCESS', 'CONFIRMED_FAILURE', 'CONTENT_REJECTED', "
            "'SAFE_TO_RETRY_PRE_DISPATCH', 'UNKNOWN_AFTER_POSSIBLE_DISPATCH')",
            name="ck_provider_call_outcome",
        ),
        sa.CheckConstraint(
            "(outcome = 'SAFE_TO_RETRY_PRE_DISPATCH' AND possible_dispatch = 0 "
            "AND provider_request_id_sha256 IS NULL) OR "
            "(outcome <> 'SAFE_TO_RETRY_PRE_DISPATCH' AND possible_dispatch = 1)",
            name="ck_provider_call_dispatch_facts",
        ),
        sa.CheckConstraint(
            "route_decision_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND request_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND idempotency_key_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND (provider_request_id_sha256 IS NULL OR "
            "provider_request_id_sha256 REGEXP '^[0-9a-f]{64}$')",
            name="ck_provider_call_hashes",
        ),
        sa.CheckConstraint(
            "latency_ms BETWEEN 0 AND 86400000",
            name="ck_provider_call_latency",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index(
        "ix_provider_call_slot",
        "generation_provider_calls",
        ["workspace_id", "candidate_slot_id", "observed_at", "id"],
    )
    op.alter_column(
        "asset_versions",
        "upload_session_id",
        existing_type=_UUID,
        nullable=True,
    )
    op.add_column(
        "asset_versions",
        sa.Column("generation_provider_call_id", _EXACT_36, nullable=True),
    )
    op.create_unique_constraint(
        "uq_asset_version_generation_provider_call",
        "asset_versions",
        ["workspace_id", "generation_provider_call_id"],
    )
    op.create_foreign_key(
        "fk_asset_version_generation_provider_call",
        "asset_versions",
        "generation_provider_calls",
        ["workspace_id", "generation_provider_call_id"],
        ["workspace_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_asset_version_exactly_one_origin",
        "asset_versions",
        "(upload_session_id IS NOT NULL AND generation_provider_call_id IS NULL) OR "
        "(upload_session_id IS NULL AND generation_provider_call_id IS NOT NULL)",
    )
    op.create_table(
        "generation_usage_records",
        sa.Column("workspace_id", _WORKSPACE, nullable=False),
        sa.Column("id", _EXACT_36, nullable=False),
        sa.Column("provider_call_id", _EXACT_36, nullable=False),
        sa.Column("provider_call_identity_sha256", _EXACT_64, nullable=False),
        sa.Column("durable_operation_id", _UUID, nullable=False),
        sa.Column("operation_attempt", sa.Integer(), nullable=False),
        sa.Column("provider", _EXACT_128, nullable=False),
        sa.Column("model", _EXACT_128, nullable=False),
        sa.Column("endpoint_capability_version_id", _EXACT_36, nullable=False),
        sa.Column("pricing_unit", _EXACT_32, nullable=False),
        sa.Column("estimated_quantity", _MONEY, nullable=False),
        sa.Column("provider_reported_quantity", _MONEY),
        sa.Column("configured_unit_price", _MONEY, nullable=False),
        sa.Column("estimated_amount", _MONEY, nullable=False),
        sa.Column("actual_amount", _MONEY),
        sa.Column("currency", _EXACT_3, nullable=False),
        sa.Column("unit_price_version", _EXACT_128, nullable=False),
        sa.Column("provider_usage_evidence_sha256", _EXACT_64),
        sa.Column("pricing_evidence_sha256", _EXACT_64, nullable=False),
        sa.Column("final_cost_evidence_sha256", _EXACT_64),
        sa.Column("resolution_status", _EXACT_24, nullable=False),
        sa.Column("evidence_source", _EXACT_32, nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("recorded_at", _DATETIME, nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "id", name="pk_usage_record"),
        sa.UniqueConstraint(
            "workspace_id",
            "provider_call_identity_sha256",
            name="uq_usage_record_call_identity",
        ),
        sa.UniqueConstraint("workspace_id", "id", "provider_call_id", name="uq_usage_record_call"),
        sa.ForeignKeyConstraint(
            [
                "workspace_id",
                "provider_call_id",
                "durable_operation_id",
                "endpoint_capability_version_id",
            ],
            [
                "generation_provider_calls.workspace_id",
                "generation_provider_calls.id",
                "generation_provider_calls.durable_operation_id",
                "generation_provider_calls.endpoint_capability_version_id",
            ],
            name="fk_usage_record_provider_call",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "durable_operation_id"],
            ["durable_operations.workspace_id", "durable_operations.id"],
            name="fk_usage_record_operation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["endpoint_capability_version_id"],
            ["provider_endpoint_capability_versions.id"],
            name="fk_usage_record_endpoint",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("operation_attempt > 0", name="ck_usage_record_attempt"),
        sa.CheckConstraint(
            "estimated_quantity > 0 AND configured_unit_price >= 0 "
            "AND estimated_amount >= 0 AND "
            "(provider_reported_quantity IS NULL OR provider_reported_quantity > 0) "
            "AND (actual_amount IS NULL OR actual_amount >= 0)",
            name="ck_usage_record_amounts",
        ),
        sa.CheckConstraint(
            "resolution_status IN ('UNRESOLVED', 'FINALIZED')",
            name="ck_usage_record_resolution",
        ),
        sa.CheckConstraint(
            "(resolution_status = 'UNRESOLVED' AND actual_amount IS NULL "
            "AND final_cost_evidence_sha256 IS NULL) OR "
            "(resolution_status = 'FINALIZED' AND provider_reported_quantity IS NOT NULL "
            "AND provider_usage_evidence_sha256 IS NOT NULL AND actual_amount IS NOT NULL "
            "AND final_cost_evidence_sha256 IS NOT NULL)",
            name="ck_usage_record_resolution_facts",
        ),
        sa.CheckConstraint(
            "provider_call_identity_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND pricing_evidence_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND (provider_usage_evidence_sha256 IS NULL OR "
            "provider_usage_evidence_sha256 REGEXP '^[0-9a-f]{64}$') "
            "AND (final_cost_evidence_sha256 IS NULL OR "
            "final_cost_evidence_sha256 REGEXP '^[0-9a-f]{64}$')",
            name="ck_usage_record_hashes",
        ),
        sa.CheckConstraint(
            "currency REGEXP '^[A-Z]{3}$'",
            name="ck_usage_record_currency",
        ),
        sa.CheckConstraint(
            "evidence_source IN ('DIRECT_RESPONSE', 'PROVIDER_RECONCILIATION', "
            "'OPERATOR_RECONCILIATION')",
            name="ck_usage_record_evidence_source",
        ),
        sa.CheckConstraint(
            "latency_ms BETWEEN 0 AND 86400000",
            name="ck_usage_record_latency",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index(
        "ix_usage_record_operation",
        "generation_usage_records",
        ["workspace_id", "durable_operation_id", "recorded_at", "id"],
    )
    op.create_table(
        "candidate_images",
        sa.Column("workspace_id", _WORKSPACE, nullable=False),
        sa.Column("id", _EXACT_36, nullable=False),
        sa.Column("workflow_id", _UUID, nullable=False),
        sa.Column("generation_batch_id", _EXACT_36, nullable=False),
        sa.Column("candidate_slot_id", _EXACT_36, nullable=False),
        sa.Column("task_asset_version_id", _UUID, nullable=False),
        sa.Column("content_sha256", _EXACT_64, nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("image_format", _EXACT_16, nullable=False),
        sa.Column("source_asset_version_ids_json", sa.JSON(), nullable=False),
        sa.Column("creative_plan_version_id", _UUID, nullable=False),
        sa.Column("prompt_sha256", _EXACT_64, nullable=False),
        sa.Column("context_sha256", _EXACT_64, nullable=False),
        sa.Column("retrieval_snapshot_sha256", _EXACT_64, nullable=False),
        sa.Column("endpoint_capability_version_id", _EXACT_36, nullable=False),
        sa.Column("provider_call_id", _EXACT_36, nullable=False),
        sa.Column("provider_request_id_sha256", _EXACT_64, nullable=False),
        sa.Column("moderation_decision_sha256", _EXACT_64, nullable=False),
        sa.Column("usage_record_id", _EXACT_36, nullable=False),
        sa.Column("created_at", _DATETIME, nullable=False),
        sa.Column("retention_deadline", _DATETIME, nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "id", name="pk_candidate_image"),
        sa.UniqueConstraint("workspace_id", "candidate_slot_id", name="uq_candidate_image_slot"),
        sa.UniqueConstraint(
            "workspace_id",
            "task_asset_version_id",
            name="uq_candidate_image_asset_version",
        ),
        sa.ForeignKeyConstraint(
            [
                "workspace_id",
                "generation_batch_id",
                "workflow_id",
                "creative_plan_version_id",
            ],
            [
                "generation_batches.workspace_id",
                "generation_batches.id",
                "generation_batches.workflow_id",
                "generation_batches.creative_plan_version_id",
            ],
            name="fk_candidate_image_batch",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "candidate_slot_id", "generation_batch_id"],
            [
                "candidate_slots.workspace_id",
                "candidate_slots.id",
                "candidate_slots.generation_batch_id",
            ],
            name="fk_candidate_image_slot",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "task_asset_version_id"],
            ["asset_versions.workspace_id", "asset_versions.id"],
            name="fk_candidate_image_asset_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "creative_plan_version_id"],
            ["creative_plan_versions.workspace_id", "creative_plan_versions.id"],
            name="fk_candidate_image_plan_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["endpoint_capability_version_id"],
            ["provider_endpoint_capability_versions.id"],
            name="fk_candidate_image_endpoint",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "workspace_id",
                "provider_call_id",
                "candidate_slot_id",
                "endpoint_capability_version_id",
            ],
            [
                "generation_provider_calls.workspace_id",
                "generation_provider_calls.id",
                "generation_provider_calls.candidate_slot_id",
                "generation_provider_calls.endpoint_capability_version_id",
            ],
            name="fk_candidate_image_provider_call",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "usage_record_id", "provider_call_id"],
            [
                "generation_usage_records.workspace_id",
                "generation_usage_records.id",
                "generation_usage_records.provider_call_id",
            ],
            name="fk_candidate_image_usage_record",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("width > 0 AND height > 0", name="ck_candidate_image_dimensions"),
        sa.CheckConstraint("retention_deadline > created_at", name="ck_candidate_image_retention"),
        sa.CheckConstraint(
            "content_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND prompt_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND context_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND retrieval_snapshot_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND provider_request_id_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND moderation_decision_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_candidate_image_hashes",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index(
        "ix_candidate_image_workflow",
        "candidate_images",
        ["workspace_id", "workflow_id", "created_at", "id"],
    )
    for table in (
        "generation_provider_calls",
        "generation_usage_records",
        "candidate_images",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE ON {table} "
            "FOR EACH ROW SIGNAL SQLSTATE '45000' "
            f"SET MESSAGE_TEXT = '{table} is immutable'"
        )


def downgrade() -> None:
    for table in (
        "candidate_images",
        "generation_usage_records",
        "generation_provider_calls",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_immutable")
    op.drop_table("candidate_images")
    op.drop_table("generation_usage_records")
    op.drop_constraint(
        "ck_asset_version_exactly_one_origin",
        "asset_versions",
        type_="check",
    )
    op.drop_constraint(
        "fk_asset_version_generation_provider_call",
        "asset_versions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_asset_version_generation_provider_call",
        "asset_versions",
        type_="unique",
    )
    op.drop_column("asset_versions", "generation_provider_call_id")
    op.alter_column(
        "asset_versions",
        "upload_session_id",
        existing_type=_UUID,
        nullable=False,
    )
    op.drop_table("generation_provider_calls")
    op.execute("DROP TRIGGER IF EXISTS trg_generation_dispatch_attempt_identity_immutable")
    op.drop_table("generation_dispatch_attempts")
    op.drop_constraint(
        "uq_candidate_slot_batch_identity",
        "candidate_slots",
        type_="unique",
    )
    op.drop_constraint(
        "uq_generation_batch_candidate_identity",
        "generation_batches",
        type_="unique",
    )
