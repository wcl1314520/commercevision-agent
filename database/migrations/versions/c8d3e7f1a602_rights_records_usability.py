"""Add immutable Rights Records and the Asset current-rights pointer.

Revision ID: c8d3e7f1a602
Revises: a7c4e2d9b831
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "c8d3e7f1a602"
down_revision: str | Sequence[str] | None = "a7c4e2d9b831"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WORKSPACE = sa.String(128, collation="utf8mb4_0900_bin")
_EXACT_64 = sa.String(64, collation="utf8mb4_0900_bin")
_PERMISSION = sa.String(128, collation="utf8mb4_0900_bin")
_RIGHTS_IMMUTABLE_COLUMNS = (
    "id",
    "workspace_id",
    "asset_id",
    "asset_version_id",
    "version_number",
    "decision",
    "owner_reference",
    "source",
    "license_reference",
    "derivative_allowed",
    "public_demo_allowed",
    "evidence_reference",
    "terms_sha256",
    "valid_from",
    "valid_until",
    "perpetual",
    "supersedes_record_id",
    "created_by",
    "created_at",
)


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_asset_versions_workspace_id_asset",
        "asset_versions",
        ["workspace_id", "id", "asset_id"],
    )
    op.create_table(
        "rights_records",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("workspace_id", _WORKSPACE, nullable=False),
        sa.Column("asset_id", sa.String(36), nullable=False),
        sa.Column("asset_version_id", sa.String(36), nullable=True),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("owner_reference", sa.String(256), nullable=False),
        sa.Column("source", sa.String(256), nullable=False),
        sa.Column("license_reference", sa.String(256), nullable=False),
        sa.Column("derivative_allowed", sa.Boolean(), nullable=False),
        sa.Column("public_demo_allowed", sa.Boolean(), nullable=False),
        sa.Column("evidence_reference", sa.String(512), nullable=False),
        sa.Column("terms_sha256", _EXACT_64, nullable=False),
        sa.Column("valid_from", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("valid_until", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("perpetual", sa.Boolean(), nullable=False),
        sa.Column("supersedes_record_id", sa.String(36), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("permissions_sealed_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_rights_records"),
        sa.UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_rights_records_workspace_id",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "id",
            "asset_id",
            name="uq_rights_records_workspace_id_asset",
        ),
        sa.UniqueConstraint(
            "asset_id",
            "version_number",
            name="uq_rights_records_asset_version",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "asset_id"],
            ["assets.workspace_id", "assets.id"],
            name="fk_rights_records_workspace_asset",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "asset_version_id", "asset_id"],
            [
                "asset_versions.workspace_id",
                "asset_versions.id",
                "asset_versions.asset_id",
            ],
            name="fk_rights_records_workspace_asset_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "supersedes_record_id", "asset_id"],
            ["rights_records.workspace_id", "rights_records.id", "rights_records.asset_id"],
            name="fk_rights_records_supersedes",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "version_number > 0",
            name="ck_rights_records_version",
        ),
        sa.CheckConstraint(
            "decision IN ('GRANT', 'REVOKE')",
            name="ck_rights_records_decision",
        ),
        sa.CheckConstraint(
            "(perpetual = 1 AND valid_until IS NULL) OR "
            "(perpetual = 0 AND valid_until IS NOT NULL AND valid_until > valid_from)",
            name="ck_rights_records_validity",
        ),
        sa.CheckConstraint(
            "terms_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_rights_records_terms_sha256",
        ),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_rights_records_current_expiry",
        "rights_records",
        ["perpetual", "valid_until", "asset_id", "id"],
    )
    op.create_index(
        "ix_rights_records_activation",
        "rights_records",
        ["decision", "valid_from", "valid_until", "asset_id", "id"],
    )
    op.create_index(
        "ix_rights_records_asset_created",
        "rights_records",
        ["workspace_id", "asset_id", "version_number"],
    )

    for table_name, permission_column in (
        ("rights_record_uses", "allowed_use"),
        ("rights_record_providers", "allowed_provider"),
    ):
        op.create_table(
            table_name,
            sa.Column("workspace_id", _WORKSPACE, nullable=False),
            sa.Column("asset_id", sa.String(36), nullable=False),
            sa.Column("rights_record_id", sa.String(36), nullable=False),
            sa.Column(permission_column, _PERMISSION, nullable=False),
            sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
            sa.PrimaryKeyConstraint(
                "rights_record_id",
                permission_column,
                name=f"pk_{table_name}",
            ),
            sa.ForeignKeyConstraint(
                ["workspace_id", "rights_record_id", "asset_id"],
                [
                    "rights_records.workspace_id",
                    "rights_records.id",
                    "rights_records.asset_id",
                ],
                name=f"fk_{table_name}_rights_record",
                ondelete="RESTRICT",
            ),
            mysql_charset="utf8mb4",
            mysql_engine="InnoDB",
        )
        op.create_index(
            f"ix_{table_name}_authorization",
            table_name,
            ["workspace_id", permission_column, "asset_id", "rights_record_id"],
        )

    op.add_column(
        "assets",
        sa.Column("current_rights_record_id", sa.String(36), nullable=True),
    )
    op.create_foreign_key(
        "fk_assets_current_rights_record",
        "assets",
        "rights_records",
        ["workspace_id", "current_rights_record_id", "id"],
        ["workspace_id", "id", "asset_id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_assets_current_rights",
        "assets",
        ["workspace_id", "current_rights_record_id", "status"],
    )

    unchanged_rights = " AND ".join(
        f"(OLD.{column_name} <=> NEW.{column_name})" for column_name in _RIGHTS_IMMUTABLE_COLUMNS
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_rights_records_no_update "
            "BEFORE UPDATE ON rights_records FOR EACH ROW "
            "BEGIN "
            "IF NOT (OLD.permissions_sealed_at IS NULL "
            "AND NEW.permissions_sealed_at IS NOT NULL "
            f"AND {unchanged_rights}) THEN "
            "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = "
            "'rights_records are immutable'; "
            "END IF; "
            "END"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_rights_records_no_delete "
            "BEFORE DELETE ON rights_records FOR EACH ROW "
            "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = "
            "'rights_records are append-only'"
        )
    )
    for table_name in ("rights_record_uses", "rights_record_providers"):
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_{table_name}_sealed_insert "
                f"BEFORE INSERT ON {table_name} FOR EACH ROW "
                "BEGIN "
                "IF EXISTS ("
                "SELECT 1 FROM rights_records "
                "WHERE id = NEW.rights_record_id "
                "AND permissions_sealed_at IS NOT NULL"
                ") THEN "
                "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = "
                f"'{table_name} permissions are sealed'; "
                "END IF; "
                "END"
            )
        )
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
    existing = op.get_bind().execute(sa.text("SELECT COUNT(*) FROM rights_records")).scalar_one()
    if existing:
        raise RuntimeError("cannot downgrade while immutable Rights Record history exists")
    for table_name in (
        "rights_record_providers",
        "rights_record_uses",
        "rights_records",
    ):
        if table_name != "rights_records":
            op.execute(sa.text(f"DROP TRIGGER IF EXISTS trg_{table_name}_sealed_insert"))
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS trg_{table_name}_no_delete"))
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS trg_{table_name}_no_update"))
    op.drop_index("ix_assets_current_rights", table_name="assets")
    op.drop_constraint(
        "fk_assets_current_rights_record",
        "assets",
        type_="foreignkey",
    )
    op.drop_column("assets", "current_rights_record_id")
    op.drop_table("rights_record_providers")
    op.drop_table("rights_record_uses")
    op.drop_table("rights_records")
    op.drop_constraint(
        "uq_asset_versions_workspace_id_asset",
        "asset_versions",
        type_="unique",
    )
