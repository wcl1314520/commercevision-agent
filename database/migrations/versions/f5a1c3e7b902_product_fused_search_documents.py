"""product fused search documents

Revision ID: f5a1c3e7b902
Revises: c4d9e8f2a701
Create Date: 2026-08-03 13:40:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "f5a1c3e7b902"
down_revision = "c4d9e8f2a701"
branch_labels = None
depends_on = None

_BINARY_COLLATION = "utf8mb4_0900_bin"


def upgrade() -> None:
    op.add_column(
        "embedding_records",
        sa.Column("product_brief_version_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "embedding_records",
        sa.Column(
            "controlled_text_sha256",
            sa.String(length=64, collation=_BINARY_COLLATION),
            nullable=True,
        ),
    )
    op.create_unique_constraint(
        "uq_embedding_records_workspace_id",
        "embedding_records",
        ["workspace_id", "id"],
    )
    op.drop_constraint(
        "uq_embedding_records_asset_spec",
        "embedding_records",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_embedding_records_asset_spec",
        "embedding_records",
        ["asset_version_id", "embedding_spec_hash", "input_hash"],
    )
    op.create_foreign_key(
        "fk_embedding_records_product_brief_version",
        "embedding_records",
        "product_brief_versions",
        ["workspace_id", "product_brief_version_id"],
        ["workspace_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_embedding_records_controlled_text",
        "embedding_records",
        "(vector_kind = 'IMAGE' AND product_brief_version_id IS NULL "
        "AND controlled_text_sha256 IS NULL) OR "
        "(vector_kind = 'PRODUCT_FUSED' AND product_brief_version_id IS NOT NULL "
        "AND controlled_text_sha256 REGEXP '^[0-9a-f]{64}$')",
    )

    op.create_table(
        "product_search_documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "workspace_id",
            sa.String(length=128, collation=_BINARY_COLLATION),
            nullable=False,
        ),
        sa.Column("product_id", sa.String(length=36), nullable=False),
        sa.Column("product_brief_id", sa.String(length=36), nullable=False),
        sa.Column("product_brief_version_id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=36), nullable=False),
        sa.Column("asset_version_id", sa.String(length=36), nullable=False),
        sa.Column("rights_record_id", sa.String(length=36), nullable=False),
        sa.Column("rights_record_version", sa.Integer(), nullable=False),
        sa.Column("embedding_record_id", sa.String(length=36), nullable=False),
        sa.Column(
            "input_hash",
            sa.String(length=64, collation=_BINARY_COLLATION),
            nullable=False,
        ),
        sa.Column(
            "controlled_text_sha256",
            sa.String(length=64, collation=_BINARY_COLLATION),
            nullable=False,
        ),
        sa.Column("preprocessing_version", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("labels", sa.Text(), nullable=False),
        sa.Column("ocr_summary", sa.Text(), nullable=False),
        sa.Column("product_brief_summary", sa.Text(), nullable=False),
        sa.Column("approved_notes", sa.Text(), nullable=False),
        sa.Column("retention_class", sa.String(length=16), nullable=False),
        sa.Column("retention_deadline", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.CheckConstraint(
            "state IN ('PENDING', 'INDEXED', 'STALE', 'DELETE_PENDING', 'DELETED')",
            name="ck_product_search_documents_state",
        ),
        sa.CheckConstraint(
            "rights_record_version > 0 AND version > 0",
            name="ck_product_search_documents_positive_versions",
        ),
        sa.CheckConstraint(
            "input_hash REGEXP '^[0-9a-f]{64}$' AND controlled_text_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_product_search_documents_hashes",
        ),
        sa.CheckConstraint(
            "(retention_class = 'TASK' AND retention_deadline IS NOT NULL) OR "
            "(retention_class = 'FOUNDATION' AND retention_deadline IS NULL)",
            name="ck_product_search_documents_retention",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_product_search_documents_product",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_brief_id"],
            ["product_briefs.workspace_id", "product_briefs.id"],
            name="fk_product_search_documents_brief",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_brief_version_id", "product_brief_id"],
            [
                "product_brief_versions.workspace_id",
                "product_brief_versions.id",
                "product_brief_versions.product_brief_id",
            ],
            name="fk_product_search_documents_brief_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "asset_version_id", "asset_id"],
            ["asset_versions.workspace_id", "asset_versions.id", "asset_versions.asset_id"],
            name="fk_product_search_documents_asset_version",
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
            name="fk_product_search_documents_rights",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "embedding_record_id"],
            ["embedding_records.workspace_id", "embedding_records.id"],
            name="fk_product_search_documents_embedding",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "asset_version_id",
            "input_hash",
            name="uq_product_search_documents_asset_input",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_product_search_documents_workspace_id",
        ),
        sa.UniqueConstraint(
            "embedding_record_id",
            name="uq_product_search_documents_embedding_record",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index(
        "ix_product_search_documents_authority",
        "product_search_documents",
        [
            "workspace_id",
            "state",
            "rights_record_id",
            "rights_record_version",
            "asset_version_id",
        ],
    )
    op.execute(
        "CREATE FULLTEXT INDEX ft_product_search_cjk ON product_search_documents "
        "(title, labels, ocr_summary, product_brief_summary, approved_notes) WITH PARSER ngram"
    )


def downgrade() -> None:
    fused_count = (
        op.get_bind()
        .execute(
            sa.text("SELECT COUNT(*) FROM embedding_records WHERE vector_kind = 'PRODUCT_FUSED'")
        )
        .scalar_one()
    )
    if fused_count:
        raise RuntimeError("refusing downgrade while PRODUCT_FUSED embedding history exists")
    op.drop_index("ft_product_search_cjk", table_name="product_search_documents")
    op.drop_index(
        "ix_product_search_documents_authority",
        table_name="product_search_documents",
    )
    op.drop_table("product_search_documents")
    op.drop_constraint(
        "ck_embedding_records_controlled_text",
        "embedding_records",
        type_="check",
    )
    op.drop_constraint(
        "fk_embedding_records_product_brief_version",
        "embedding_records",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_embedding_records_asset_spec",
        "embedding_records",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_embedding_records_asset_spec",
        "embedding_records",
        ["asset_version_id", "embedding_spec_hash"],
    )
    op.drop_constraint(
        "uq_embedding_records_workspace_id",
        "embedding_records",
        type_="unique",
    )
    op.drop_column("embedding_records", "controlled_text_sha256")
    op.drop_column("embedding_records", "product_brief_version_id")
