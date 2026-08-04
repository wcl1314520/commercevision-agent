"""Add immutable Prompt Revisions and exact production pointers.

Revision ID: a9d2f6c4e801
Revises: f4c8a1e7b205
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "a9d2f6c4e801"
down_revision: str | Sequence[str] | None = "f4c8a1e7b205"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WORKSPACE = sa.String(128, collation="utf8mb4_0900_bin")
_EXACT_128 = sa.String(128, collation="utf8mb4_0900_bin")
_SHA256 = sa.String(64, collation="utf8mb4_0900_bin")


def upgrade() -> None:
    op.create_table(
        "prompt_revisions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("workspace_id", _WORKSPACE, nullable=False),
        sa.Column("prompt_id", _EXACT_128, nullable=False),
        sa.Column("semantic_revision", _EXACT_128, nullable=False),
        sa.Column("node", _EXACT_128, nullable=False),
        sa.Column("category_applicability_json", sa.JSON(), nullable=False),
        sa.Column("model_family_applicability_json", sa.JSON(), nullable=False),
        sa.Column("input_schema_version", _EXACT_128, nullable=False),
        sa.Column("output_schema_version", _EXACT_128, nullable=False),
        sa.Column("policy_version", _EXACT_128, nullable=False),
        sa.Column("content", mysql.MEDIUMTEXT(), nullable=False),
        sa.Column("variables_json", sa.JSON(), nullable=False),
        sa.Column("content_sha256", _SHA256, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("change_summary", sa.String(512), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("submitted_by", sa.String(128), nullable=True),
        sa.Column("submitted_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("reviewed_by", sa.String(128), nullable=True),
        sa.Column("reviewed_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("published_by", sa.String(128), nullable=True),
        sa.Column("published_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("deprecated_by", sa.String(128), nullable=True),
        sa.Column("deprecated_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_prompt_revisions"),
        sa.UniqueConstraint(
            "workspace_id",
            "prompt_id",
            "semantic_revision",
            name="uq_prompt_revisions_workspace_semantic",
        ),
        sa.UniqueConstraint(
            "workspace_id", "id", name="uq_prompt_revisions_workspace_id"
        ),
        sa.CheckConstraint("version > 0", name="ck_prompt_revisions_version"),
        sa.CheckConstraint(
            "status IN ('DRAFT','REVIEW','STAGING','PRODUCTION','DEPRECATED')",
            name="ck_prompt_revisions_status",
        ),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_prompt_revisions_workspace_status",
        "prompt_revisions",
        ["workspace_id", "prompt_id", "status", "semantic_revision"],
    )

    op.create_table(
        "prompt_production_pointers",
        sa.Column("workspace_id", _WORKSPACE, nullable=False),
        sa.Column("prompt_id", _EXACT_128, nullable=False),
        sa.Column("node", _EXACT_128, nullable=False),
        sa.Column("revision_id", sa.String(36), nullable=False),
        sa.Column("semantic_revision", _EXACT_128, nullable=False),
        sa.Column("content_sha256", _SHA256, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.String(128), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.PrimaryKeyConstraint(
            "workspace_id", "prompt_id", name="pk_prompt_production_pointers"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "revision_id"],
            ["prompt_revisions.workspace_id", "prompt_revisions.id"],
            name="fk_prompt_pointer_exact_revision",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("version > 0", name="ck_prompt_pointer_version"),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )

    op.execute(
        """
        CREATE TRIGGER trg_prompt_revisions_immutable_content
        BEFORE UPDATE ON prompt_revisions FOR EACH ROW
        BEGIN
          IF NOT (
            NEW.id <=> OLD.id AND NEW.workspace_id <=> OLD.workspace_id
            AND NEW.prompt_id <=> OLD.prompt_id
            AND NEW.semantic_revision <=> OLD.semantic_revision
            AND NEW.node <=> OLD.node
            AND NEW.category_applicability_json <=> OLD.category_applicability_json
            AND NEW.model_family_applicability_json <=> OLD.model_family_applicability_json
            AND NEW.input_schema_version <=> OLD.input_schema_version
            AND NEW.output_schema_version <=> OLD.output_schema_version
            AND NEW.policy_version <=> OLD.policy_version
            AND NEW.content <=> OLD.content AND NEW.variables_json <=> OLD.variables_json
            AND NEW.content_sha256 <=> OLD.content_sha256
            AND NEW.created_by <=> OLD.created_by
            AND NEW.change_summary <=> OLD.change_summary
            AND NEW.created_at <=> OLD.created_at
          ) THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Prompt Revision content is immutable';
          END IF;
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_prompt_revisions_no_delete
        BEFORE DELETE ON prompt_revisions FOR EACH ROW
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Prompt Revision history is append-only'
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_prompt_production_pointers_no_delete
        BEFORE DELETE ON prompt_production_pointers FOR EACH ROW
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Prompt production pointer is durable'
        """
    )
    for action in ("INSERT", "UPDATE"):
        suffix = action.lower()
        op.execute(
            f"""
            CREATE TRIGGER trg_prompt_pointer_validate_{suffix}
            BEFORE {action} ON prompt_production_pointers FOR EACH ROW
            BEGIN
              IF (SELECT COUNT(*) FROM prompt_revisions r
                  WHERE r.workspace_id = NEW.workspace_id AND r.id = NEW.revision_id
                    AND r.prompt_id = NEW.prompt_id AND r.node = NEW.node
                    AND r.semantic_revision = NEW.semantic_revision
                    AND r.content_sha256 = NEW.content_sha256
                    AND r.status = 'PRODUCTION') <> 1 THEN
                SIGNAL SQLSTATE '45000'
                  SET MESSAGE_TEXT = 'Prompt pointer must select one exact production revision';
              END IF;
            END
            """
        )
    op.execute(
        """
        CREATE TRIGGER trg_prompt_revision_active_no_deprecate
        BEFORE UPDATE ON prompt_revisions FOR EACH ROW
        BEGIN
          IF NEW.status = 'DEPRECATED' AND OLD.status <> 'DEPRECATED'
             AND EXISTS (SELECT 1 FROM prompt_production_pointers p
                         WHERE p.workspace_id = OLD.workspace_id
                           AND p.revision_id = OLD.id) THEN
            SIGNAL SQLSTATE '45000'
              SET MESSAGE_TEXT = 'Active Prompt Revision cannot be deprecated';
          END IF;
        END
        """
    )


def downgrade() -> None:
    connection = op.get_bind()
    revision_count = connection.scalar(sa.text("SELECT COUNT(*) FROM prompt_revisions"))
    pointer_count = connection.scalar(sa.text("SELECT COUNT(*) FROM prompt_production_pointers"))
    if revision_count or pointer_count:
        raise RuntimeError("cannot downgrade Prompt Registry while immutable facts exist")
    op.execute("DROP TRIGGER IF EXISTS trg_prompt_revision_active_no_deprecate")
    op.execute("DROP TRIGGER IF EXISTS trg_prompt_pointer_validate_update")
    op.execute("DROP TRIGGER IF EXISTS trg_prompt_pointer_validate_insert")
    op.execute("DROP TRIGGER IF EXISTS trg_prompt_production_pointers_no_delete")
    op.execute("DROP TRIGGER IF EXISTS trg_prompt_revisions_no_delete")
    op.execute("DROP TRIGGER IF EXISTS trg_prompt_revisions_immutable_content")
    op.drop_table("prompt_production_pointers")
    op.drop_index("ix_prompt_revisions_workspace_status", table_name="prompt_revisions")
    op.drop_table("prompt_revisions")
