"""Add retained immutable Planning Context snapshots.

Revision ID: b7e3c9d5a102
Revises: a9d2f6c4e801
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "b7e3c9d5a102"
down_revision: str | Sequence[str] | None = "a9d2f6c4e801"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WORKSPACE = sa.String(128, collation="utf8mb4_0900_bin")
_EXACT_64 = sa.String(64, collation="utf8mb4_0900_bin")
_EXACT_128 = sa.String(128, collation="utf8mb4_0900_bin")


def upgrade() -> None:
    op.create_table(
        "planning_context_snapshots",
        sa.Column("workspace_id", _WORKSPACE, nullable=False),
        sa.Column("workflow_id", sa.String(36), nullable=False),
        sa.Column("context_sha256", _EXACT_64, nullable=False),
        sa.Column("storage_sha256", _EXACT_64, nullable=False),
        sa.Column("schema_version", _EXACT_128, nullable=False),
        sa.Column("policy_version", _EXACT_128, nullable=False),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False),
        sa.Column("retain_until", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.PrimaryKeyConstraint(
            "workspace_id",
            "workflow_id",
            "context_sha256",
            name="pk_planning_context_snapshots",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            name="fk_planning_context_snapshot_workflow",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "source_count BETWEEN 1 AND 104",
            name="ck_planning_context_source_count",
        ),
        sa.CheckConstraint(
            "context_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_planning_context_context_sha256",
        ),
        sa.CheckConstraint(
            "storage_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_planning_context_storage_sha256",
        ),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_planning_context_snapshots_retention",
        "planning_context_snapshots",
        ["workspace_id", "retain_until", "workflow_id"],
    )
    op.execute(
        """
        CREATE TRIGGER trg_planning_context_snapshots_immutable
        BEFORE UPDATE ON planning_context_snapshots FOR EACH ROW
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Planning Context snapshot is immutable'
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_planning_context_snapshots_retain
        BEFORE DELETE ON planning_context_snapshots FOR EACH ROW
        BEGIN
          IF OLD.retain_until > UTC_TIMESTAMP(6) THEN
            SIGNAL SQLSTATE '45000'
              SET MESSAGE_TEXT = 'Planning Context is retained for the Workflow lifetime';
          END IF;
        END
        """
    )


def downgrade() -> None:
    connection = op.get_bind()
    snapshot_count = connection.scalar(sa.text("SELECT COUNT(*) FROM planning_context_snapshots"))
    if snapshot_count:
        raise RuntimeError("cannot downgrade while immutable Planning Context facts exist")
    op.execute("DROP TRIGGER IF EXISTS trg_planning_context_snapshots_retain")
    op.execute("DROP TRIGGER IF EXISTS trg_planning_context_snapshots_immutable")
    op.drop_index(
        "ix_planning_context_snapshots_retention",
        table_name="planning_context_snapshots",
    )
    op.drop_table("planning_context_snapshots")
