"""Add versioned Brand Profile publication and immutable member facts.

Revision ID: b8e1d4f7a203
Revises: a4c8e7f3b219
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "b8e1d4f7a203"
down_revision: str | Sequence[str] | None = "a4c8e7f3b219"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WORKSPACE = sa.String(128, collation="utf8mb4_0900_bin")
_EXACT_128 = sa.String(128, collation="utf8mb4_0900_bin")
_EXACT_64 = sa.String(64, collation="utf8mb4_0900_bin")
_TRIGGERS = (
    "trg_brand_profiles_no_delete",
    "trg_brand_profile_versions_no_update",
    "trg_brand_profile_versions_no_delete",
    "trg_brand_profile_members_no_update",
    "trg_brand_profile_members_no_delete",
)


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_rights_records_exact_version",
        "rights_records",
        ["workspace_id", "id", "asset_id", "version_number"],
    )

    op.create_table(
        "brand_profiles",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("workspace_id", _WORKSPACE, nullable=False),
        sa.Column("brand", _EXACT_128, nullable=False),
        sa.Column("profile_key", _EXACT_128, nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("draft_json", sa.JSON(), nullable=False),
        sa.Column("draft_sha256", _EXACT_64, nullable=False),
        sa.Column("current_version_id", sa.String(36), nullable=True),
        sa.Column("current_version_number", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("stale_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_by", sa.String(128), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_brand_profiles"),
        sa.UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_brand_profiles_workspace_id",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "brand",
            "profile_key",
            name="uq_brand_profiles_workspace_identity",
        ),
        sa.CheckConstraint(
            "state IN ('DRAFT', 'ACTIVE', 'NEEDS_REPUBLISH', 'ARCHIVED')",
            name="ck_brand_profiles_state",
        ),
        sa.CheckConstraint(
            "draft_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_brand_profiles_draft_sha256",
        ),
        sa.CheckConstraint(
            "version > 0 AND current_version_number >= 0",
            name="ck_brand_profiles_versions_positive",
        ),
        sa.CheckConstraint(
            "(current_version_id IS NULL AND current_version_number = 0) "
            "OR (current_version_id IS NOT NULL AND current_version_number > 0)",
            name="ck_brand_profiles_head_consistent",
        ),
        sa.CheckConstraint(
            "(state = 'DRAFT' AND current_version_id IS NULL) "
            "OR (state IN ('ACTIVE', 'NEEDS_REPUBLISH') "
            "AND current_version_id IS NOT NULL) "
            "OR state = 'ARCHIVED'",
            name="ck_brand_profiles_draft_head",
        ),
        sa.CheckConstraint(
            "(state = 'NEEDS_REPUBLISH' AND stale_at IS NOT NULL) "
            "OR (state <> 'NEEDS_REPUBLISH' AND stale_at IS NULL)",
            name="ck_brand_profiles_stale_state",
        ),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_brand_profiles_workspace_state",
        "brand_profiles",
        ["workspace_id", "state", "updated_at", "id"],
    )
    op.create_index(
        "ix_brand_profiles_workspace_created",
        "brand_profiles",
        ["workspace_id", "created_at", "id"],
    )
    op.create_index(
        "ix_brand_profiles_workspace_brand_created",
        "brand_profiles",
        ["workspace_id", "brand", "created_at", "id"],
    )

    op.create_table(
        "brand_profile_versions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("workspace_id", _WORKSPACE, nullable=False),
        sa.Column("profile_id", sa.String(36), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=False),
        sa.Column("content_sha256", _EXACT_64, nullable=False),
        sa.Column("purpose", _EXACT_128, nullable=False),
        sa.Column("provider", _EXACT_128, nullable=False),
        sa.Column("requires_derivative", sa.Boolean(), nullable=False),
        sa.Column("published_by", sa.String(128), nullable=False),
        sa.Column("published_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_brand_profile_versions"),
        sa.UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_brand_profile_versions_workspace_id",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "profile_id",
            "id",
            "version_number",
            name="uq_brand_profile_versions_head_identity",
        ),
        sa.UniqueConstraint(
            "profile_id",
            "version_number",
            name="uq_brand_profile_versions_number",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "profile_id"],
            ["brand_profiles.workspace_id", "brand_profiles.id"],
            name="fk_brand_profile_versions_profile",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "version_number > 0",
            name="ck_brand_profile_versions_number",
        ),
        sa.CheckConstraint(
            "content_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_brand_profile_versions_content_sha256",
        ),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_brand_profile_versions_history",
        "brand_profile_versions",
        ["workspace_id", "profile_id", "version_number", "id"],
    )

    op.create_table(
        "brand_profile_members",
        sa.Column("workspace_id", _WORKSPACE, nullable=False),
        sa.Column("profile_id", sa.String(36), nullable=False),
        sa.Column("profile_version_id", sa.String(36), nullable=False),
        sa.Column("profile_version_number", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("asset_id", sa.String(36), nullable=False),
        sa.Column("asset_version_id", sa.String(36), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("rights_record_id", sa.String(36), nullable=False),
        sa.Column("rights_record_version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint(
            "profile_version_id",
            "ordinal",
            name="pk_brand_profile_members",
        ),
        sa.UniqueConstraint(
            "profile_version_id",
            "asset_version_id",
            name="uq_brand_profile_members_asset_version",
        ),
        sa.ForeignKeyConstraint(
            [
                "workspace_id",
                "profile_id",
                "profile_version_id",
                "profile_version_number",
            ],
            [
                "brand_profile_versions.workspace_id",
                "brand_profile_versions.profile_id",
                "brand_profile_versions.id",
                "brand_profile_versions.version_number",
            ],
            name="fk_brand_profile_members_profile_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "asset_version_id", "asset_id"],
            ["asset_versions.workspace_id", "asset_versions.id", "asset_versions.asset_id"],
            name="fk_brand_profile_members_asset_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "rights_record_id", "asset_id", "rights_record_version"],
            [
                "rights_records.workspace_id",
                "rights_records.id",
                "rights_records.asset_id",
                "rights_records.version_number",
            ],
            name="fk_brand_profile_members_rights_record",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_brand_profile_members_ordinal"),
        sa.CheckConstraint(
            "profile_version_number > 0 AND rights_record_version > 0",
            name="ck_brand_profile_members_versions",
        ),
        sa.CheckConstraint(
            "role IN ('LOGO', 'REQUIRED_MARK', 'VISUAL_REFERENCE', "
            "'PROMPT_TEMPLATE', 'MODEL_CONFIGURATION', 'LORA')",
            name="ck_brand_profile_members_role",
        ),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_brand_profile_members_current_invalidation",
        "brand_profile_members",
        ["workspace_id", "asset_id", "profile_id", "profile_version_id"],
    )

    op.create_foreign_key(
        "fk_brand_profiles_current_version",
        "brand_profiles",
        "brand_profile_versions",
        ["workspace_id", "id", "current_version_id", "current_version_number"],
        ["workspace_id", "profile_id", "id", "version_number"],
        ondelete="RESTRICT",
        use_alter=True,
    )

    op.execute(
        sa.text(
            "CREATE TRIGGER trg_brand_profiles_no_delete "
            "BEFORE DELETE ON brand_profiles FOR EACH ROW "
            "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = "
            "'brand_profiles retain publication identity and history'"
        )
    )
    for table_name in ("brand_profile_versions", "brand_profile_members"):
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_{table_name}_no_update "
                f"BEFORE UPDATE ON {table_name} FOR EACH ROW "
                "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = "
                f"'{table_name} are immutable'"
            )
        )
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_{table_name}_no_delete "
                f"BEFORE DELETE ON {table_name} FOR EACH ROW "
                "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = "
                f"'{table_name} are append-only'"
            )
        )


def downgrade() -> None:
    connection = op.get_bind()
    existing = int(
        connection.execute(
            sa.text(
                "SELECT "
                "(SELECT COUNT(*) FROM brand_profiles) + "
                "(SELECT COUNT(*) FROM brand_profile_versions) + "
                "(SELECT COUNT(*) FROM brand_profile_members)"
            )
        ).scalar_one()
    )
    if existing:
        raise RuntimeError("cannot downgrade while Brand Profile history exists")

    for trigger_name in _TRIGGERS:
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS {trigger_name}"))
    op.drop_constraint(
        "fk_brand_profiles_current_version",
        "brand_profiles",
        type_="foreignkey",
    )
    op.drop_table("brand_profile_members")
    op.drop_table("brand_profile_versions")
    op.drop_table("brand_profiles")
    op.drop_constraint(
        "uq_rights_records_exact_version",
        "rights_records",
        type_="unique",
    )
