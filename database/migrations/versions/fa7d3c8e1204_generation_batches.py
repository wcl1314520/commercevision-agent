"""Add immutable generation batches and candidate slots.

Revision ID: fa7d3c8e1204
Revises: f8c2a7d5e913
Create Date: 2026-08-06 16:15:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "fa7d3c8e1204"
down_revision = "f8c2a7d5e913"
branch_labels = None
depends_on = None

_WORKSPACE = sa.String(128, collation="utf8mb4_0900_bin")
_EXACT_36 = sa.String(36, collation="utf8mb4_0900_bin")
_EXACT_40 = sa.String(40, collation="utf8mb4_0900_bin")
_EXACT_64 = sa.String(64, collation="utf8mb4_0900_bin")
_EXACT_128 = sa.String(128, collation="utf8mb4_0900_bin")
_UUID = sa.String(36)
_DATETIME = mysql.DATETIME(fsp=6)


def upgrade() -> None:
    op.add_column(
        "model_route_decisions",
        sa.Column("authorized_asset_version_ids_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "model_route_decisions",
        sa.Column("route_candidate_count", sa.Integer(), nullable=True),
    )
    op.create_table(
        "generation_batches",
        sa.Column("workspace_id", _WORKSPACE, nullable=False),
        sa.Column("id", _EXACT_36, nullable=False),
        sa.Column("batch_sha256", _EXACT_64, nullable=False),
        sa.Column("workflow_id", _UUID, nullable=False),
        sa.Column("workflow_version", sa.Integer(), nullable=False),
        sa.Column("creative_plan_version_id", _UUID, nullable=False),
        sa.Column("plan_approval_id", _UUID, nullable=False),
        sa.Column("direction_key", _EXACT_128, nullable=False),
        sa.Column("tool_intent_key", _EXACT_128, nullable=False),
        sa.Column("tool_intent_sha256", _EXACT_64, nullable=False),
        sa.Column("prompt_sha256", _EXACT_64, nullable=False),
        sa.Column("context_sha256", _EXACT_64, nullable=False),
        sa.Column("route_decision_sha256", _EXACT_64, nullable=False),
        sa.Column("route_request_sha256", _EXACT_64, nullable=False),
        sa.Column("operation_kind", _EXACT_40, nullable=False),
        sa.Column("authorized_asset_version_ids_json", sa.JSON(), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("route_policy_version", _EXACT_128, nullable=False),
        sa.Column("tool_policy_version", _EXACT_128, nullable=False),
        sa.Column("rights_policy_version", _EXACT_128, nullable=False),
        sa.Column("safety_policy_version", _EXACT_128, nullable=False),
        sa.Column("workflow_deadline", _DATETIME, nullable=False),
        sa.Column("source_rights_deadline", _DATETIME),
        sa.Column("edit_source_asset_version_id", _UUID),
        sa.Column("edit_mask_asset_version_id", _UUID),
        sa.Column("approved_repair_scope_json", sa.JSON(), nullable=False),
        sa.Column("retention_deadline", _DATETIME, nullable=False),
        sa.Column("created_by", _EXACT_128, nullable=False),
        sa.Column("created_at", _DATETIME, nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "id", name="pk_generation_batch"),
        sa.UniqueConstraint(
            "workspace_id",
            "batch_sha256",
            name="uq_generation_batch_hash",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "workflow_id",
            "workflow_version",
            "creative_plan_version_id",
            "direction_key",
            "tool_intent_key",
            name="uq_generation_batch_logical",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            name="fk_generation_batch_workflow",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "creative_plan_version_id"],
            ["creative_plan_versions.workspace_id", "creative_plan_versions.id"],
            name="fk_generation_batch_plan_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["plan_approval_id"],
            ["workflow_approvals.id"],
            name="fk_generation_batch_approval",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "route_decision_sha256"],
            ["model_route_decisions.workspace_id", "model_route_decisions.decision_sha256"],
            name="fk_generation_batch_route_decision",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "candidate_count BETWEEN 1 AND 16 AND workflow_version > 0",
            name="ck_generation_batch_counts",
        ),
        sa.CheckConstraint(
            "operation_kind IN ('IMAGE_GENERATION', 'IMAGE_EDITING')",
            name="ck_generation_batch_kind",
        ),
        sa.CheckConstraint(
            "retention_deadline > created_at "
            "AND retention_deadline <= workflow_deadline "
            "AND (source_rights_deadline IS NULL "
            "OR retention_deadline <= source_rights_deadline)",
            name="ck_generation_batch_retention",
        ),
        sa.CheckConstraint(
            "batch_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND tool_intent_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND prompt_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND context_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND route_decision_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND route_request_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_generation_batch_hashes",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index(
        "ix_generation_batch_workflow",
        "generation_batches",
        ["workspace_id", "workflow_id", "created_at", "id"],
    )
    op.create_index(
        "ix_generation_batch_retention",
        "generation_batches",
        ["workspace_id", "retention_deadline", "id"],
    )

    op.create_table(
        "candidate_slots",
        sa.Column("workspace_id", _WORKSPACE, nullable=False),
        sa.Column("id", _EXACT_36, nullable=False),
        sa.Column("generation_batch_id", _EXACT_36, nullable=False),
        sa.Column("candidate_index", sa.Integer(), nullable=False),
        sa.Column("durable_operation_id", _UUID, nullable=False),
        sa.Column("operation_kind", _EXACT_40, nullable=False),
        sa.Column("logical_identity_sha256", _EXACT_64, nullable=False),
        sa.Column("operation_idempotency_key", _EXACT_128, nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "id", name="pk_candidate_slot"),
        sa.UniqueConstraint(
            "workspace_id",
            "generation_batch_id",
            "candidate_index",
            name="uq_candidate_slot_index",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "durable_operation_id",
            name="uq_candidate_slot_operation",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "logical_identity_sha256",
            name="uq_candidate_slot_logical_identity",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "generation_batch_id"],
            ["generation_batches.workspace_id", "generation_batches.id"],
            name="fk_candidate_slot_batch",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "durable_operation_id"],
            ["durable_operations.workspace_id", "durable_operations.id"],
            name="fk_candidate_slot_operation",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "candidate_index BETWEEN 0 AND 15",
            name="ck_candidate_slot_index",
        ),
        sa.CheckConstraint(
            "operation_kind IN ('IMAGE_GENERATION', 'IMAGE_EDITING')",
            name="ck_candidate_slot_kind",
        ),
        sa.CheckConstraint(
            "logical_identity_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_candidate_slot_hash",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index(
        "ix_candidate_slot_batch",
        "candidate_slots",
        ["workspace_id", "generation_batch_id", "candidate_index"],
    )
    op.execute(
        """
        CREATE TRIGGER trg_generation_batches_immutable
        BEFORE UPDATE ON generation_batches FOR EACH ROW
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Generation Batch is immutable'
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_candidate_slots_immutable
        BEFORE UPDATE ON candidate_slots FOR EACH ROW
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Candidate Slot is immutable'
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_candidate_slots_immutable")
    op.execute("DROP TRIGGER IF EXISTS trg_generation_batches_immutable")
    op.drop_table("candidate_slots")
    op.drop_table("generation_batches")
    op.drop_column("model_route_decisions", "route_candidate_count")
    op.drop_column("model_route_decisions", "authorized_asset_version_ids_json")
