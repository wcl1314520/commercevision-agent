"""Add immutable model route decisions.

Revision ID: f8c2a7d5e913
Revises: e3b7a9c4d612
Create Date: 2026-08-06 15:10:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "f8c2a7d5e913"
down_revision = "e3b7a9c4d612"
branch_labels = None
depends_on = None

_WORKSPACE = sa.String(128, collation="utf8mb4_0900_bin")
_EXACT_3 = sa.String(3, collation="utf8mb4_0900_bin")
_EXACT_36 = sa.String(36, collation="utf8mb4_0900_bin")
_EXACT_64 = sa.String(64, collation="utf8mb4_0900_bin")
_EXACT_128 = sa.String(128, collation="utf8mb4_0900_bin")
_UUID = sa.String(36)
_DATETIME = mysql.DATETIME(fsp=6)
_MONEY = sa.Numeric(20, 6)
_IMMUTABLE_TRIGGER = "trg_model_route_decisions_immutable"


def upgrade() -> None:
    op.create_table(
        "model_route_decisions",
        sa.Column("workspace_id", _WORKSPACE, nullable=False),
        sa.Column("decision_sha256", _EXACT_64, nullable=False),
        sa.Column("idempotency_scope_sha256", _EXACT_64, nullable=False),
        sa.Column("idempotency_key_sha256", _EXACT_64, nullable=False),
        sa.Column("workflow_id", _UUID, nullable=False),
        sa.Column("creative_plan_version_id", _UUID, nullable=False),
        sa.Column("plan_approval_id", _UUID, nullable=False),
        sa.Column("route_request_sha256", _EXACT_64, nullable=False),
        sa.Column("policy_key", _EXACT_128, nullable=False),
        sa.Column("policy_version_id", _EXACT_36, nullable=False),
        sa.Column("route_policy_version", _EXACT_128, nullable=False),
        sa.Column("endpoint_capability_version_id", _EXACT_36, nullable=False),
        sa.Column("fallback_endpoint_capability_version_ids_json", sa.JSON(), nullable=False),
        sa.Column("candidate_scores_json", sa.JSON(), nullable=False),
        sa.Column("rejection_counts_json", sa.JSON(), nullable=False),
        sa.Column("estimated_cost", _MONEY, nullable=False),
        sa.Column("currency", _EXACT_3, nullable=False),
        sa.Column("decided_at", _DATETIME, nullable=False),
        sa.Column("created_by", _EXACT_128, nullable=False),
        sa.PrimaryKeyConstraint(
            "workspace_id",
            "decision_sha256",
            name="pk_model_route_decision",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "idempotency_scope_sha256",
            "idempotency_key_sha256",
            name="uq_model_route_decision_idempotency",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            name="fk_model_route_decision_workflow",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "creative_plan_version_id"],
            ["creative_plan_versions.workspace_id", "creative_plan_versions.id"],
            name="fk_model_route_decision_plan_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["plan_approval_id"],
            ["workflow_approvals.id"],
            name="fk_model_route_decision_approval",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "policy_key", "policy_version_id"],
            [
                "model_route_policy_versions.workspace_id",
                "model_route_policy_versions.policy_key",
                "model_route_policy_versions.id",
            ],
            name="fk_model_route_decision_policy",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["endpoint_capability_version_id"],
            ["provider_endpoint_capability_versions.id"],
            name="fk_model_route_decision_endpoint",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "decision_sha256 REGEXP '^[0-9a-f]{64}$' AND "
            "idempotency_scope_sha256 REGEXP '^[0-9a-f]{64}$' AND "
            "idempotency_key_sha256 REGEXP '^[0-9a-f]{64}$' AND "
            "route_request_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_model_route_decision_hashes",
        ),
        sa.CheckConstraint(
            "estimated_cost >= 0 AND currency REGEXP '^[A-Z]{3}$'",
            name="ck_model_route_decision_cost",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index(
        "ix_model_route_decision_workflow",
        "model_route_decisions",
        ["workspace_id", "workflow_id", "decided_at", "decision_sha256"],
    )
    op.execute(
        """
        CREATE TRIGGER trg_model_route_decisions_immutable
        BEFORE UPDATE ON model_route_decisions FOR EACH ROW
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Model route decision is immutable'
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {_IMMUTABLE_TRIGGER}")
    op.drop_table("model_route_decisions")
