"""product catalog workspace

Revision ID: 8d2f4c7a9b01
Revises: 7f4a2b9c1d6e
Create Date: 2026-07-22 21:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "8d2f4c7a9b01"
down_revision: str | Sequence[str] | None = "7f4a2b9c1d6e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("source_namespace", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        sa.Column("source_version", sa.String(length=128), nullable=True),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("category_code", sa.String(length=128), nullable=False),
        sa.Column("brand", sa.String(length=128), nullable=False),
        sa.Column("attributes_json", sa.JSON(), nullable=False),
        sa.Column("expires_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "source_namespace",
            "external_id",
            name="uq_products_external_identity",
        ),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_products_workspace_created",
        "products",
        ["workspace_id", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "skus",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("product_id", sa.String(length=36), nullable=False),
        sa.Column("source_namespace", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        sa.Column("source_version", sa.String(length=128), nullable=True),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("category_code", sa.String(length=128), nullable=False),
        sa.Column("brand", sa.String(length=128), nullable=False),
        sa.Column("attributes_json", sa.JSON(), nullable=False),
        sa.Column("expires_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "source_namespace",
            "external_id",
            name="uq_skus_external_identity",
        ),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_skus_workspace_product",
        "skus",
        ["workspace_id", "product_id", "created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_skus_workspace_product", table_name="skus")
    op.drop_table("skus")
    op.drop_index("ix_products_workspace_created", table_name="products")
    op.drop_table("products")
