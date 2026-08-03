"""Add versioned IMAGE collection registry and MySQL index facts.

Revision ID: c4d9e8f2a701
Revises: b8e1d4f7a203
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "c4d9e8f2a701"
down_revision: str | Sequence[str] | None = "b8e1d4f7a203"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WORKSPACE = sa.String(128, collation="utf8mb4_0900_bin")
_EXACT_64 = sa.String(64, collation="utf8mb4_0900_bin")
_EXACT_255 = sa.String(255, collation="utf8mb4_0900_bin")
_EXACT_512 = sa.String(512, collation="utf8mb4_0900_bin")


def upgrade() -> None:
    op.create_table(
        "collection_registry",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("logical_key", _EXACT_512, nullable=False),
        sa.Column("spec_hash", _EXACT_64, nullable=False),
        sa.Column("physical_name", _EXACT_255, nullable=False),
        sa.Column("model_family", sa.String(128), nullable=False),
        sa.Column("model_id", sa.String(128), nullable=False),
        sa.Column("pinned_revision", sa.String(128), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("vector_kind", sa.String(32), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("index_spec_version", sa.String(128), nullable=False),
        sa.Column(
            "dynamic_fields_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("is_read_enabled", sa.Boolean(), nullable=False),
        sa.Column("is_write_enabled", sa.Boolean(), nullable=False),
        sa.Column("validation_summary_json", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_collection_registry"),
        sa.UniqueConstraint("logical_key", name="uq_collection_registry_logical_key"),
        sa.UniqueConstraint("spec_hash", name="uq_collection_registry_spec_hash"),
        sa.UniqueConstraint("physical_name", name="uq_collection_registry_physical_name"),
        sa.CheckConstraint("dimension > 0", name="ck_collection_registry_dimension"),
        sa.CheckConstraint(
            "schema_version > 0",
            name="ck_collection_registry_schema_version",
        ),
        sa.CheckConstraint(
            "dynamic_fields_enabled = 0",
            name="ck_collection_registry_dynamic_fields_disabled",
        ),
        sa.CheckConstraint(
            "state IN ('PLANNED', 'CREATING', 'BACKFILLING', 'VERIFYING', "
            "'READY', 'ACTIVE', 'RETIRING', 'RETIRED', 'FAILED')",
            name="ck_collection_registry_state",
        ),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_collection_registry_routing",
        "collection_registry",
        ["vector_kind", "state", "is_read_enabled", "is_write_enabled"],
    )

    op.create_table(
        "embedding_records",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("workspace_id", _WORKSPACE, nullable=False),
        sa.Column("asset_id", sa.String(36), nullable=False),
        sa.Column("asset_version_id", sa.String(36), nullable=False),
        sa.Column("asset_version_number", sa.Integer(), nullable=False),
        sa.Column("rights_record_id", sa.String(36), nullable=False),
        sa.Column("rights_record_version", sa.Integer(), nullable=False),
        sa.Column("collection_id", sa.String(36), nullable=False),
        sa.Column("operation_id", sa.String(36), nullable=False),
        sa.Column("vector_kind", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model_family", sa.String(128), nullable=False),
        sa.Column("model_id", sa.String(128), nullable=False),
        sa.Column("pinned_revision", sa.String(128), nullable=False),
        sa.Column("model_configuration_version", sa.String(128), nullable=False),
        sa.Column("preprocessing_version", sa.String(128), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("input_hash", _EXACT_64, nullable=False),
        sa.Column("embedding_spec_hash", _EXACT_64, nullable=False),
        sa.Column("milvus_primary_key", _EXACT_64, nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("write_generation", sa.Integer(), nullable=False),
        sa.Column("provider_request_id", sa.String(256), nullable=True),
        sa.Column("actual_model", sa.String(256), nullable=True),
        sa.Column("indexed_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("stale_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("stale_reason", sa.String(64), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_embedding_records"),
        sa.UniqueConstraint(
            "asset_version_id",
            "embedding_spec_hash",
            name="uq_embedding_records_asset_spec",
        ),
        sa.UniqueConstraint(
            "collection_id",
            "milvus_primary_key",
            name="uq_embedding_records_collection_pk",
        ),
        sa.UniqueConstraint("operation_id", name="uq_embedding_records_operation"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "asset_version_id", "asset_id"],
            ["asset_versions.workspace_id", "asset_versions.id", "asset_versions.asset_id"],
            name="fk_embedding_records_asset_version",
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
            name="fk_embedding_records_rights_record",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["collection_id"],
            ["collection_registry.id"],
            name="fk_embedding_records_collection",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "operation_id"],
            ["durable_operations.workspace_id", "durable_operations.id"],
            name="fk_embedding_records_operation",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "state IN ('PENDING', 'PROCESSING', 'INDEXED', 'RETRYABLE_FAILED', "
            "'PERMANENT_FAILED', 'STALE', 'DELETE_PENDING', 'DELETED')",
            name="ck_embedding_records_state",
        ),
        sa.CheckConstraint(
            "asset_version_number > 0 AND rights_record_version > 0 "
            "AND dimension > 0 AND write_generation >= 0 AND version > 0",
            name="ck_embedding_records_positive_versions",
        ),
        sa.CheckConstraint(
            "input_hash REGEXP '^[0-9a-f]{64}$' "
            "AND embedding_spec_hash REGEXP '^[0-9a-f]{64}$'",
            name="ck_embedding_records_hashes",
        ),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_embedding_records_workspace_state",
        "embedding_records",
        ["workspace_id", "state", "updated_at", "id"],
    )
    op.create_index(
        "ix_embedding_records_asset_status",
        "embedding_records",
        ["workspace_id", "asset_id", "asset_version_id", "state"],
    )
    op.create_index(
        "ix_embedding_records_collection_status",
        "embedding_records",
        ["collection_id", "state", "updated_at"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    row_count = int(
        connection.execute(
            sa.text(
                "SELECT (SELECT COUNT(*) FROM embedding_records) + "
                "(SELECT COUNT(*) FROM collection_registry)"
            )
        ).scalar_one()
    )
    if row_count:
        raise RuntimeError("cannot downgrade while collection or embedding facts exist")
    op.drop_table("embedding_records")
    op.drop_table("collection_registry")
