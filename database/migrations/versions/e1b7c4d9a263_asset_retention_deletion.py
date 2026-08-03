"""asset retention deletion tombstones and progress

Revision ID: e1b7c4d9a263
Revises: a3f8c2d9e714
Create Date: 2026-08-04 01:30:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "e1b7c4d9a263"
down_revision = "a3f8c2d9e714"
branch_labels = None
depends_on = None

_BINARY_COLLATION = "utf8mb4_0900_bin"
_TASK_PAYLOAD_TABLES = ("product_brief_fields", "product_brief_evidence")


def _datetime() -> mysql.DATETIME:
    return mysql.DATETIME(fsp=6)


def _install_task_payload_delete_guards() -> None:
    for table_name in _TASK_PAYLOAD_TABLES:
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS trg_{table_name}_no_delete"))
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_{table_name}_no_delete "
                f"BEFORE DELETE ON {table_name} FOR EACH ROW BEGIN "
                "IF (SELECT COUNT(*) FROM product_briefs AS pb "
                "INNER JOIN assets AS a ON a.workspace_id = pb.workspace_id "
                "AND a.workflow_id = pb.workflow_id "
                "INNER JOIN asset_deletion_tombstones AS t "
                "ON t.workspace_id = a.workspace_id "
                "AND a.deletion_operation_id = t.operation_id "
                "WHERE pb.workspace_id = OLD.workspace_id "
                "AND pb.id = OLD.product_brief_id "
                "AND pb.retention_class = 'TASK' "
                "AND pb.retention_deadline <= UTC_TIMESTAMP(6) "
                "AND a.retention_class = 'TASK' "
                "AND a.status IN ('DELETING', 'DELETED') "
                "AND t.reason = 'RETENTION_EXPIRED') = 0 THEN "
                "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = "
                "'task ProductBrief payload cannot be deleted without its retention tombstone'; "
                "END IF; END"
            )
        )


def _restore_product_brief_delete_guards() -> None:
    for table_name in _TASK_PAYLOAD_TABLES:
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS trg_{table_name}_no_delete"))
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_{table_name}_no_delete "
                f"BEFORE DELETE ON {table_name} FOR EACH ROW "
                "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = "
                f"'{table_name} cannot be deleted'"
            )
        )


def upgrade() -> None:
    op.add_column(
        "assets",
        sa.Column("deletion_generation", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("assets", sa.Column("deletion_operation_id", sa.String(36), nullable=True))
    op.add_column("assets", sa.Column("deletion_reason", sa.String(32), nullable=True))
    op.add_column("assets", sa.Column("deletion_requested_at", _datetime(), nullable=True))
    op.add_column("assets", sa.Column("deletion_completed_at", _datetime(), nullable=True))
    op.create_foreign_key(
        "fk_assets_deletion_operation",
        "assets",
        "durable_operations",
        ["workspace_id", "deletion_operation_id"],
        ["workspace_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_assets_deletion_identity",
        "assets",
        "deletion_generation >= 0 AND ((deletion_generation = 0 "
        "AND deletion_operation_id IS NULL AND deletion_reason IS NULL "
        "AND deletion_requested_at IS NULL AND deletion_completed_at IS NULL) "
        "OR (deletion_generation > 0 AND deletion_operation_id IS NOT NULL "
        "AND deletion_reason IS NOT NULL AND deletion_requested_at IS NOT NULL))",
    )
    op.create_check_constraint(
        "ck_assets_deletion_completion",
        "assets",
        "deletion_completed_at IS NULL OR status = 'DELETED'",
    )
    op.create_index(
        "ix_assets_deletion_progress",
        "assets",
        ["status", "deletion_requested_at", "workspace_id", "id"],
    )
    op.create_index(
        "ix_assets_retention_cleanup_due",
        "assets",
        ["retention_class", "deletion_operation_id", "retention_deadline", "id"],
    )

    op.create_table(
        "asset_deletion_tombstones",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(128, collation=_BINARY_COLLATION), nullable=False),
        sa.Column("asset_id", sa.String(36), nullable=False),
        sa.Column("target_asset_version_id", sa.String(36), nullable=False),
        sa.Column("deletion_generation", sa.Integer(), nullable=False),
        sa.Column("operation_id", sa.String(36), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("requested_by", sa.String(128, collation=_BINARY_COLLATION), nullable=False),
        sa.Column("requested_at", _datetime(), nullable=False),
        sa.CheckConstraint("deletion_generation > 0", name="ck_asset_deletion_generation"),
        sa.CheckConstraint(
            "reason IN ('RETENTION_EXPIRED', 'RIGHTS_EXPIRED', 'ADMINISTRATOR_DELETE')",
            name="ck_asset_deletion_reason",
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_asset_deletion_tombstones_workspace_id"),
        sa.UniqueConstraint("asset_id", "deletion_generation", name="uq_asset_deletion_generation"),
        sa.UniqueConstraint("operation_id", name="uq_asset_deletion_operation"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "asset_id"],
            ["assets.workspace_id", "assets.id"],
            name="fk_asset_deletion_tombstone_asset",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "target_asset_version_id", "asset_id"],
            ["asset_versions.workspace_id", "asset_versions.id", "asset_versions.asset_id"],
            name="fk_asset_deletion_tombstone_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "operation_id"],
            ["durable_operations.workspace_id", "durable_operations.id"],
            name="fk_asset_deletion_tombstone_operation",
            ondelete="RESTRICT",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index(
        "ix_asset_deletion_tombstones_requested",
        "asset_deletion_tombstones",
        ["requested_at", "workspace_id", "id"],
    )

    op.create_table(
        "asset_deletion_progress",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(128, collation=_BINARY_COLLATION), nullable=False),
        sa.Column("tombstone_id", sa.String(36), nullable=False),
        sa.Column("component", sa.String(32), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("cursor_value", sa.String(512, collation=_BINARY_COLLATION), nullable=True),
        sa.Column("observed_count", sa.Integer(), nullable=False),
        sa.Column("converged_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("created_at", _datetime(), nullable=False),
        sa.CheckConstraint(
            "component IN ('OBJECTS', 'VECTORS', 'SEARCH_DOCUMENTS', 'PROVIDER_ARTIFACTS', "
            "'TEMPORARY_REFERENCES', 'CACHES', 'PRODUCT_BRIEFS', 'RETRIEVAL_RUNS', "
            "'CHECKPOINTS', 'QUARANTINE', 'OPERATIONS')",
            name="ck_asset_deletion_progress_component",
        ),
        sa.CheckConstraint(
            "state IN ('PENDING', 'CONVERGED', 'RETRYABLE_FAILED')",
            name="ck_asset_deletion_progress_state",
        ),
        sa.CheckConstraint(
            "observed_count >= 0 AND converged_count >= 0 AND converged_count <= observed_count",
            name="ck_asset_deletion_progress_counts",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "tombstone_id"],
            ["asset_deletion_tombstones.workspace_id", "asset_deletion_tombstones.id"],
            name="fk_asset_deletion_progress_tombstone",
            ondelete="RESTRICT",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index(
        "ix_asset_deletion_progress_tombstone",
        "asset_deletion_progress",
        ["workspace_id", "tombstone_id", "created_at", "id"],
    )

    op.create_table(
        "provider_artifact_deletion_progress",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(128, collation=_BINARY_COLLATION), nullable=False),
        sa.Column("provider_artifact_id", sa.String(36), nullable=False),
        sa.Column("tombstone_id", sa.String(36), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column(
            "provider_version_id", sa.String(256, collation=_BINARY_COLLATION), nullable=True
        ),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("created_at", _datetime(), nullable=False),
        sa.CheckConstraint(
            "state IN ('DISCOVERED', 'CONVERGED', 'RETRYABLE_FAILED')",
            name="ck_provider_artifact_deletion_progress_state",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "provider_artifact_id"],
            [
                "product_brief_provider_artifacts.workspace_id",
                "product_brief_provider_artifacts.id",
            ],
            name="fk_provider_artifact_deletion_progress_artifact",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "tombstone_id"],
            ["asset_deletion_tombstones.workspace_id", "asset_deletion_tombstones.id"],
            name="fk_provider_artifact_deletion_progress_tombstone",
            ondelete="RESTRICT",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index(
        "ix_provider_artifact_deletion_progress_artifact",
        "provider_artifact_deletion_progress",
        ["workspace_id", "provider_artifact_id", "created_at", "id"],
    )

    for table_name in (
        "asset_deletion_tombstones",
        "asset_deletion_progress",
        "provider_artifact_deletion_progress",
    ):
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_{table_name}_no_update BEFORE UPDATE ON {table_name} "
                "FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = "
                "'append-only deletion fact'"
            )
        )
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_{table_name}_no_delete BEFORE DELETE ON {table_name} "
                "FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = "
                "'append-only deletion fact'"
            )
        )
    _install_task_payload_delete_guards()


def downgrade() -> None:
    _restore_product_brief_delete_guards()
    for table_name in (
        "provider_artifact_deletion_progress",
        "asset_deletion_progress",
        "asset_deletion_tombstones",
    ):
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS trg_{table_name}_no_delete"))
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS trg_{table_name}_no_update"))
        op.drop_table(table_name)

    op.drop_index("ix_assets_retention_cleanup_due", table_name="assets")
    op.drop_index("ix_assets_deletion_progress", table_name="assets")
    op.drop_constraint("ck_assets_deletion_completion", "assets", type_="check")
    op.drop_constraint("ck_assets_deletion_identity", "assets", type_="check")
    op.drop_constraint("fk_assets_deletion_operation", "assets", type_="foreignkey")
    op.drop_column("assets", "deletion_completed_at")
    op.drop_column("assets", "deletion_requested_at")
    op.drop_column("assets", "deletion_reason")
    op.drop_column("assets", "deletion_operation_id")
    op.drop_column("assets", "deletion_generation")
