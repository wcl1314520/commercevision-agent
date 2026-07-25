"""direct upload sessions and quarantine assets

Revision ID: d4e7a1c9b205
Revises: b1c8e4f2a703
Create Date: 2026-07-24 14:50:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "d4e7a1c9b205"
down_revision: str | None = "b1c8e4f2a703"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DATETIME = mysql.DATETIME(fsp=6)
_WORKSPACE = sa.String(128, collation="utf8mb4_0900_bin")
_EXACT_64 = sa.String(64, collation="utf8mb4_0900_bin")
_EXACT_255 = sa.String(255, collation="utf8mb4_0900_bin")
_EXACT_512 = sa.String(512, collation="utf8mb4_0900_bin")
_TABLE_OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_workflows_workspace_id",
        "workflows",
        ["workspace_id", "id"],
    )
    op.create_unique_constraint(
        "uq_skus_workspace_product_id",
        "skus",
        ["workspace_id", "product_id", "id"],
    )

    op.create_table(
        "upload_sessions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("workspace_id", _WORKSPACE, nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("reserved_asset_id", sa.String(36), nullable=False),
        sa.Column("reserved_asset_version_id", sa.String(36), nullable=False),
        sa.Column("retention_class", sa.String(16), nullable=False),
        sa.Column("asset_kind", sa.String(32), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("declared_mime", sa.String(128), nullable=False),
        sa.Column("expected_byte_length", sa.BigInteger(), nullable=False),
        sa.Column("expected_sha256", _EXACT_64, nullable=False),
        sa.Column("workflow_id", sa.String(36), nullable=True),
        sa.Column("product_id", sa.String(36), nullable=True),
        sa.Column("sku_id", sa.String(36), nullable=True),
        sa.Column("category", sa.String(128), nullable=False),
        sa.Column("role", sa.String(64), nullable=False),
        sa.Column("upload_policy_version", sa.String(64), nullable=False),
        sa.Column("integrity_policy_version", sa.String(64), nullable=False),
        sa.Column("storage_backend", sa.String(16), nullable=False),
        sa.Column("storage_location", sa.String(24), nullable=False),
        sa.Column("storage_bucket", _EXACT_255, nullable=False),
        sa.Column("storage_key", _EXACT_512, nullable=False),
        sa.Column("destination_location", sa.String(24), nullable=False),
        sa.Column("destination_bucket", _EXACT_255, nullable=False),
        sa.Column("destination_key", _EXACT_512, nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("finalize_lease_owner", sa.String(128), nullable=True),
        sa.Column("finalize_lease_token", sa.String(36), nullable=True),
        sa.Column("finalize_lease_expires_at", _DATETIME, nullable=True),
        sa.Column("finalize_attempts", sa.Integer(), nullable=False),
        sa.Column("failure_code", sa.String(128), nullable=True),
        sa.Column("finalized_asset_version_id", sa.String(36), nullable=True),
        sa.Column("validation_operation_id", sa.String(36), nullable=True),
        sa.Column("cleanup_operation_id", sa.String(36), nullable=True),
        sa.Column("cleanup_reconcile_until", _DATETIME, nullable=True),
        sa.Column("expires_at", _DATETIME, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", _DATETIME, nullable=False),
        sa.Column("updated_at", _DATETIME, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_upload_session_workspace_id",
        ),
        sa.UniqueConstraint(
            "reserved_asset_id",
            name="uq_upload_session_reserved_asset",
        ),
        sa.UniqueConstraint(
            "reserved_asset_version_id",
            name="uq_upload_session_reserved_asset_version",
        ),
        sa.UniqueConstraint(
            "cleanup_operation_id",
            name="uq_upload_session_cleanup_operation",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            name="fk_upload_session_workspace_workflow",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_upload_session_workspace_product",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "sku_id"],
            ["skus.workspace_id", "skus.product_id", "skus.id"],
            name="fk_upload_session_workspace_sku",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "validation_operation_id"],
            ["durable_operations.workspace_id", "durable_operations.id"],
            name="fk_upload_session_validation_operation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "cleanup_operation_id"],
            ["durable_operations.workspace_id", "durable_operations.id"],
            name="fk_upload_session_cleanup_operation",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "(retention_class = 'TASK' AND workflow_id IS NOT NULL) "
            "OR (retention_class = 'FOUNDATION' AND workflow_id IS NULL)",
            name="ck_upload_session_retention_owner",
        ),
        sa.CheckConstraint(
            "sku_id IS NULL OR product_id IS NOT NULL",
            name="ck_upload_session_sku_product",
        ),
        sa.CheckConstraint(
            "(retention_class = 'TASK' AND destination_location = 'TASK') "
            "OR (retention_class = 'FOUNDATION' AND destination_location = 'FOUNDATION')",
            name="ck_upload_session_destination_retention",
        ),
        sa.CheckConstraint(
            "storage_location = 'QUARANTINE'",
            name="ck_upload_session_source_quarantine",
        ),
        sa.CheckConstraint(
            "storage_bucket <> destination_bucket OR storage_key <> destination_key",
            name="ck_upload_session_distinct_storage",
        ),
        sa.CheckConstraint(
            "(state = 'FINALIZING' AND finalize_lease_owner IS NOT NULL "
            "AND finalize_lease_token IS NOT NULL AND finalize_lease_expires_at IS NOT NULL) "
            "OR (state <> 'FINALIZING' AND finalize_lease_owner IS NULL "
            "AND finalize_lease_token IS NULL AND finalize_lease_expires_at IS NULL)",
            name="ck_upload_session_finalize_lease",
        ),
        sa.CheckConstraint(
            "(state = 'FINALIZED' AND finalized_asset_version_id IS NOT NULL "
            "AND validation_operation_id IS NOT NULL) "
            "OR (state <> 'FINALIZED' AND finalized_asset_version_id IS NULL "
            "AND validation_operation_id IS NULL)",
            name="ck_upload_session_finalize_result",
        ),
        sa.CheckConstraint(
            "finalized_asset_version_id IS NULL "
            "OR finalized_asset_version_id = reserved_asset_version_id",
            name="ck_upload_session_reserved_result",
        ),
        sa.CheckConstraint(
            "cleanup_operation_id IS NULL "
            "OR state IN ('FINALIZED', 'EXPIRED', 'ABORTED')",
            name="ck_upload_session_cleanup_state",
        ),
        sa.CheckConstraint(
            "(cleanup_operation_id IS NULL AND cleanup_reconcile_until IS NULL) "
            "OR (cleanup_operation_id IS NOT NULL "
            "AND cleanup_reconcile_until IS NOT NULL "
            "AND state IN ('FINALIZED', 'EXPIRED', 'ABORTED'))",
            name="ck_upload_session_cleanup_reconcile_window",
        ),
        sa.CheckConstraint(
            "(state = 'ABORTED' AND failure_code IS NOT NULL) "
            "OR (state <> 'ABORTED' AND failure_code IS NULL)",
            name="ck_upload_session_failure_state",
        ),
        sa.CheckConstraint(
            "expected_byte_length > 0",
            name="ck_upload_session_byte_length",
        ),
        sa.CheckConstraint(
            "version > 0 AND finalize_attempts >= 0",
            name="ck_upload_session_counters",
        ),
        **_TABLE_OPTIONS,
    )
    op.create_index(
        "ix_upload_session_workspace_state",
        "upload_sessions",
        ["workspace_id", "state", "expires_at"],
    )
    op.create_index(
        "ix_upload_session_finalize_lease",
        "upload_sessions",
        ["state", "finalize_lease_expires_at"],
    )
    op.create_index(
        "ix_upload_session_expiry_scan",
        "upload_sessions",
        ["state", "expires_at", "id"],
    )

    op.create_table(
        "assets",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("workspace_id", _WORKSPACE, nullable=False),
        sa.Column("retention_class", sa.String(16), nullable=False),
        sa.Column("asset_kind", sa.String(32), nullable=False),
        sa.Column("workflow_id", sa.String(36), nullable=True),
        sa.Column("product_id", sa.String(36), nullable=True),
        sa.Column("sku_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("current_version_id", sa.String(36), nullable=True),
        sa.Column("retention_deadline", _DATETIME, nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", _DATETIME, nullable=False),
        sa.Column("updated_at", _DATETIME, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_assets_workspace_id"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            name="fk_assets_workspace_workflow",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_assets_workspace_product",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "sku_id"],
            ["skus.workspace_id", "skus.product_id", "skus.id"],
            name="fk_assets_workspace_sku",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "(retention_class = 'TASK' AND workflow_id IS NOT NULL) "
            "OR (retention_class = 'FOUNDATION' AND workflow_id IS NULL)",
            name="ck_assets_retention_owner",
        ),
        sa.CheckConstraint(
            "(retention_class = 'TASK' AND retention_deadline IS NOT NULL) "
            "OR (retention_class = 'FOUNDATION' AND retention_deadline IS NULL)",
            name="ck_assets_retention_deadline",
        ),
        sa.CheckConstraint(
            "sku_id IS NULL OR product_id IS NOT NULL",
            name="ck_assets_sku_product",
        ),
        **_TABLE_OPTIONS,
    )
    op.create_index(
        "ix_assets_workspace_status",
        "assets",
        ["workspace_id", "status", "updated_at", "id"],
    )
    op.create_index(
        "ix_assets_retention_deadline",
        "assets",
        ["status", "retention_deadline"],
    )

    op.create_table(
        "asset_versions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("workspace_id", _WORKSPACE, nullable=False),
        sa.Column("asset_id", sa.String(36), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("upload_session_id", sa.String(36), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("sha256", _EXACT_64, nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("declared_mime", sa.String(128), nullable=False),
        sa.Column("detected_mime", sa.String(128), nullable=False),
        sa.Column("image_format", sa.String(16), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("frame_count", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(128), nullable=False),
        sa.Column("role", sa.String(64), nullable=False),
        sa.Column("integrity_policy_version", sa.String(64), nullable=False),
        sa.Column("created_at", _DATETIME, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_asset_version_workspace_id",
        ),
        sa.UniqueConstraint(
            "upload_session_id",
            name="uq_asset_version_upload_session",
        ),
        sa.UniqueConstraint(
            "asset_id",
            "version_number",
            name="uq_asset_version_number",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "asset_id"],
            ["assets.workspace_id", "assets.id"],
            name="fk_asset_version_workspace_asset",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "upload_session_id"],
            ["upload_sessions.workspace_id", "upload_sessions.id"],
            name="fk_asset_version_workspace_upload",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("version_number > 0", name="ck_asset_version_number"),
        sa.CheckConstraint("byte_size > 0", name="ck_asset_version_byte_size"),
        **_TABLE_OPTIONS,
    )
    op.create_index(
        "ix_asset_version_workspace_sha",
        "asset_versions",
        ["workspace_id", "sha256"],
    )
    op.create_index(
        "ix_asset_version_asset_created",
        "asset_versions",
        ["asset_id", "created_at", "id"],
    )

    op.create_table(
        "asset_objects",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("workspace_id", _WORKSPACE, nullable=False),
        sa.Column("asset_version_id", sa.String(36), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("backend", sa.String(16), nullable=False),
        sa.Column("location", sa.String(24), nullable=False),
        sa.Column("bucket", _EXACT_255, nullable=False),
        sa.Column("key", _EXACT_512, nullable=False),
        sa.Column("provider_version_id", sa.String(256), nullable=False),
        sa.Column("etag", _EXACT_512, nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", _EXACT_64, nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", _DATETIME, nullable=False),
        sa.Column("updated_at", _DATETIME, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_asset_object_workspace_id",
        ),
        sa.UniqueConstraint(
            "asset_version_id",
            "role",
            name="uq_asset_object_version_role",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "asset_version_id"],
            ["asset_versions.workspace_id", "asset_versions.id"],
            name="fk_asset_object_workspace_version",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("byte_size > 0", name="ck_asset_object_byte_size"),
        sa.CheckConstraint(
            "CHAR_LENGTH(TRIM(provider_version_id)) > 0 "
            "AND LOWER(TRIM(provider_version_id)) <> 'null'",
            name="ck_asset_object_provider_version",
        ),
        sa.CheckConstraint(
            "state <> 'QUARANTINED' OR location = 'QUARANTINE'",
            name="ck_asset_object_quarantine_location",
        ),
        **_TABLE_OPTIONS,
    )
    op.create_index(
        "ix_asset_object_workspace_state",
        "asset_objects",
        ["workspace_id", "state", "updated_at"],
    )

    op.create_foreign_key(
        "fk_assets_workspace_current_version",
        "assets",
        "asset_versions",
        ["workspace_id", "current_version_id"],
        ["workspace_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_upload_session_finalized_version",
        "upload_sessions",
        "asset_versions",
        ["workspace_id", "finalized_asset_version_id"],
        ["workspace_id", "id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    existing_upload = op.get_bind().execute(
        sa.text("SELECT id FROM upload_sessions LIMIT 1")
    ).first()
    if existing_upload is not None:
        raise RuntimeError(
            "cannot downgrade direct upload storage while Upload Session data exists"
        )
    op.drop_constraint(
        "fk_upload_session_finalized_version",
        "upload_sessions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_assets_workspace_current_version",
        "assets",
        type_="foreignkey",
    )
    op.drop_table("asset_objects")
    op.drop_table("asset_versions")
    op.drop_table("assets")
    op.drop_table("upload_sessions")
    op.drop_constraint(
        "uq_skus_workspace_product_id",
        "skus",
        type_="unique",
    )
    op.drop_constraint(
        "uq_workflows_workspace_id",
        "workflows",
        type_="unique",
    )
