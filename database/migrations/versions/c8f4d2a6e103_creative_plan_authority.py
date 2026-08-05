"""Add Creative Plan immutable versions and optimistic head.

Revision ID: c8f4d2a6e103
Revises: b7e3c9d5a102
Create Date: 2026-08-05 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "c8f4d2a6e103"
down_revision = "b7e3c9d5a102"
branch_labels = None
depends_on = None

_WORKSPACE = sa.String(128, collation="utf8mb4_0900_bin")
_EXACT_64 = sa.String(64, collation="utf8mb4_0900_bin")
_EXACT_128 = sa.String(128, collation="utf8mb4_0900_bin")

_TRIGGERS = (
    "trg_creative_plan_versions_immutable",
    "trg_creative_plan_versions_retain",
    "trg_creative_plans_head_guard",
    "trg_creative_plans_retain",
)


def upgrade() -> None:
    op.create_table(
        "creative_plan_versions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("workspace_id", _WORKSPACE, nullable=False),
        sa.Column("workflow_id", sa.String(36), nullable=False),
        sa.Column("creative_plan_id", sa.String(36), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("supersedes_version_id", sa.String(36), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("payload_sha256", _EXACT_64, nullable=False),
        sa.Column("product_brief_id", sa.String(36), nullable=False),
        sa.Column("product_brief_version", sa.Integer(), nullable=False),
        sa.Column("product_brief_sha256", _EXACT_64, nullable=False),
        sa.Column("brand_profile_id", sa.String(36), nullable=True),
        sa.Column("brand_profile_version", sa.Integer(), nullable=True),
        sa.Column("brand_profile_sha256", _EXACT_64, nullable=True),
        sa.Column("retrieval_run_id", sa.String(36), nullable=False),
        sa.Column("retrieval_citation_ids_json", sa.JSON(), nullable=False),
        sa.Column("context_policy_version", _EXACT_128, nullable=False),
        sa.Column("context_sha256", _EXACT_64, nullable=False),
        sa.Column("prompt_id", _EXACT_128, nullable=False),
        sa.Column("prompt_revision", _EXACT_128, nullable=False),
        sa.Column("prompt_sha256", _EXACT_64, nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("revision_reason", sa.String(512), nullable=True),
        sa.Column("retain_until", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "id", name="pk_creative_plan_versions"),
        sa.UniqueConstraint(
            "workspace_id",
            "workflow_id",
            "creative_plan_id",
            "version_number",
            name="uq_creative_plan_versions_logical",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "workflow_id",
            "creative_plan_id",
            "id",
            name="uq_creative_plan_versions_identity",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "workflow_id",
            "creative_plan_id",
            "id",
            "version_number",
            name="uq_creative_plan_versions_head_target",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            name="fk_creative_plan_versions_workflow",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "workspace_id",
                "workflow_id",
                "creative_plan_id",
                "supersedes_version_id",
            ],
            [
                "creative_plan_versions.workspace_id",
                "creative_plan_versions.workflow_id",
                "creative_plan_versions.creative_plan_id",
                "creative_plan_versions.id",
            ],
            name="fk_creative_plan_versions_supersedes",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("version_number > 0", name="ck_creative_plan_versions_number"),
        sa.CheckConstraint(
            "(version_number = 1 AND supersedes_version_id IS NULL) OR "
            "(version_number > 1 AND supersedes_version_id IS NOT NULL)",
            name="ck_creative_plan_versions_lineage",
        ),
        sa.CheckConstraint(
            "source IN ('AGENT', 'USER')",
            name="ck_creative_plan_versions_source",
        ),
        sa.CheckConstraint(
            "(source = 'AGENT' AND revision_reason IS NULL) OR "
            "(source = 'USER' AND revision_reason IS NOT NULL)",
            name="ck_creative_plan_versions_revision_reason",
        ),
        sa.CheckConstraint(
            "payload_sha256 REGEXP '^[0-9a-f]{64}$' AND "
            "product_brief_sha256 REGEXP '^[0-9a-f]{64}$' AND "
            "context_sha256 REGEXP '^[0-9a-f]{64}$' AND "
            "prompt_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_creative_plan_versions_hashes",
        ),
        sa.CheckConstraint(
            "(brand_profile_id IS NULL AND brand_profile_version IS NULL "
            "AND brand_profile_sha256 IS NULL) OR "
            "(brand_profile_id IS NOT NULL AND brand_profile_version > 0 "
            "AND brand_profile_sha256 REGEXP '^[0-9a-f]{64}$')",
            name="ck_creative_plan_versions_brand_provenance",
        ),
        sa.CheckConstraint(
            "product_brief_version > 0",
            name="ck_creative_plan_versions_product_brief_version",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index(
        "ix_creative_plan_versions_history",
        "creative_plan_versions",
        ["workspace_id", "workflow_id", "creative_plan_id", "version_number"],
    )
    op.create_index(
        "ix_creative_plan_versions_retention",
        "creative_plan_versions",
        ["workspace_id", "retain_until", "creative_plan_id"],
    )

    op.create_table(
        "creative_plans",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("workspace_id", _WORKSPACE, nullable=False),
        sa.Column("workflow_id", sa.String(36), nullable=False),
        sa.Column("current_version_id", sa.String(36), nullable=False),
        sa.Column("current_version_number", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("retain_until", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "id", name="pk_creative_plans"),
        sa.UniqueConstraint(
            "workspace_id",
            "workflow_id",
            "id",
            name="uq_creative_plans_workspace_workflow_id",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            name="fk_creative_plans_workflow",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "workspace_id",
                "workflow_id",
                "id",
                "current_version_id",
                "current_version_number",
            ],
            [
                "creative_plan_versions.workspace_id",
                "creative_plan_versions.workflow_id",
                "creative_plan_versions.creative_plan_id",
                "creative_plan_versions.id",
                "creative_plan_versions.version_number",
            ],
            name="fk_creative_plans_current_version",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "current_version_number > 0 AND version = current_version_number",
            name="ck_creative_plans_head_version",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index(
        "ix_creative_plans_workspace_workflow",
        "creative_plans",
        ["workspace_id", "workflow_id", "updated_at", "id"],
    )
    op.create_index(
        "ix_creative_plans_retention",
        "creative_plans",
        ["workspace_id", "retain_until", "id"],
    )

    op.execute(
        """
        CREATE TRIGGER trg_creative_plan_versions_immutable
        BEFORE UPDATE ON creative_plan_versions FOR EACH ROW
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Creative Plan version is immutable'
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_creative_plan_versions_retain
        BEFORE DELETE ON creative_plan_versions FOR EACH ROW
        BEGIN
          IF OLD.retain_until > UTC_TIMESTAMP(6) THEN
            SIGNAL SQLSTATE '45000'
              SET MESSAGE_TEXT = 'Creative Plan version retention is active';
          END IF;
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_creative_plans_head_guard
        BEFORE UPDATE ON creative_plans FOR EACH ROW
        BEGIN
          IF NOT (
            NEW.id <=> OLD.id AND
            NEW.workspace_id <=> OLD.workspace_id AND
            NEW.workflow_id <=> OLD.workflow_id AND
            NEW.retain_until <=> OLD.retain_until AND
            NEW.created_at <=> OLD.created_at
          ) THEN
            SIGNAL SQLSTATE '45000'
              SET MESSAGE_TEXT = 'Creative Plan identity and retention are immutable';
          END IF;
          IF NEW.version <> OLD.version + 1
             OR NEW.current_version_number <> OLD.current_version_number + 1
             OR NEW.version <> NEW.current_version_number
             OR NEW.updated_at < OLD.updated_at THEN
            SIGNAL SQLSTATE '45000'
              SET MESSAGE_TEXT = 'Creative Plan head advancement is invalid';
          END IF;
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_creative_plans_retain
        BEFORE DELETE ON creative_plans FOR EACH ROW
        BEGIN
          IF OLD.retain_until > UTC_TIMESTAMP(6) THEN
            SIGNAL SQLSTATE '45000'
              SET MESSAGE_TEXT = 'Creative Plan retention is active';
          END IF;
        END
        """
    )


def downgrade() -> None:
    connection = op.get_bind()
    version_count = connection.scalar(sa.text("SELECT COUNT(*) FROM creative_plan_versions"))
    head_count = connection.scalar(sa.text("SELECT COUNT(*) FROM creative_plans"))
    if version_count or head_count:
        raise RuntimeError("cannot downgrade while immutable Creative Plan facts exist")
    for trigger_name in _TRIGGERS:
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS {trigger_name}"))
    op.drop_table("creative_plans")
    op.drop_table("creative_plan_versions")
