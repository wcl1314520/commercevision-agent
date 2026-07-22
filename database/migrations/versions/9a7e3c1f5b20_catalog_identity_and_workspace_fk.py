"""catalog identity registry and workspace-safe SKU ownership

Revision ID: 9a7e3c1f5b20
Revises: 8d2f4c7a9b01
Create Date: 2026-07-22 23:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "9a7e3c1f5b20"
down_revision: str | Sequence[str] | None = "8d2f4c7a9b01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "catalog_external_identities",
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("source_namespace", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        sa.Column("owner_type", sa.String(length=16), nullable=False),
        sa.Column("owner_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.PrimaryKeyConstraint(
            "workspace_id",
            "source_namespace",
            "external_id",
            name="pk_catalog_external_identity",
        ),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_catalog_external_identity_owner",
        "catalog_external_identities",
        ["owner_type", "owner_id"],
        unique=False,
    )
    op.create_unique_constraint(
        "uq_products_workspace_id",
        "products",
        ["workspace_id", "id"],
    )

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for foreign_key in inspector.get_foreign_keys("skus"):
        if foreign_key["constrained_columns"] == ["product_id"]:
            op.drop_constraint(foreign_key["name"], "skus", type_="foreignkey")
    op.create_foreign_key(
        "fk_skus_workspace_product",
        "skus",
        "products",
        ["workspace_id", "product_id"],
        ["workspace_id", "id"],
        ondelete="RESTRICT",
    )

    op.execute(
        sa.text(
            """
            INSERT INTO catalog_external_identities
                (workspace_id, source_namespace, external_id, owner_type, owner_id, created_at)
            SELECT workspace_id, source_namespace, external_id, 'PRODUCT', id, created_at
            FROM products
            UNION ALL
            SELECT workspace_id, source_namespace, external_id, 'SKU', id, created_at
            FROM skus
            """
        )
    )


def downgrade() -> None:
    op.drop_constraint("fk_skus_workspace_product", "skus", type_="foreignkey")
    op.create_foreign_key(
        "fk_skus_product",
        "skus",
        "products",
        ["product_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint("uq_products_workspace_id", "products", type_="unique")
    op.drop_index(
        "ix_catalog_external_identity_owner",
        table_name="catalog_external_identities",
    )
    op.drop_table("catalog_external_identities")
