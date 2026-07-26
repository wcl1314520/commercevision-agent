"""asset validation evidence and multi-kind version facts

Revision ID: e5f8b2d6c914
Revises: d4e7a1c9b205
Create Date: 2026-07-26 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "e5f8b2d6c914"
down_revision: str | None = "d4e7a1c9b205"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DATETIME = mysql.DATETIME(fsp=6)
_WORKSPACE = sa.String(128, collation="utf8mb4_0900_bin")
_EXACT_64 = sa.String(64, collation="utf8mb4_0900_bin")
_EXACT_256 = sa.String(256, collation="utf8mb4_0900_bin")
_EXACT_512 = sa.String(512, collation="utf8mb4_0900_bin")
_TABLE_OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


def upgrade() -> None:
    op.add_column("assets", sa.Column("block_reason", sa.String(64), nullable=True))
    op.create_check_constraint(
        "ck_assets_block_reason",
        "assets",
        "(status = 'BLOCKED' AND block_reason IS NOT NULL) "
        "OR (status <> 'BLOCKED' AND block_reason IS NULL)",
    )

    op.alter_column(
        "asset_versions",
        "detected_mime",
        existing_type=sa.String(128),
        nullable=True,
    )
    op.alter_column(
        "asset_versions",
        "image_format",
        existing_type=sa.String(16),
        nullable=True,
    )
    op.alter_column(
        "asset_versions",
        "width",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.alter_column(
        "asset_versions",
        "height",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.alter_column(
        "asset_versions",
        "frame_count",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.add_column(
        "asset_versions",
        sa.Column(
            "validation_policy_version",
            sa.String(64),
            nullable=False,
            server_default="asset-validation-v1",
        ),
    )
    op.alter_column(
        "asset_versions",
        "validation_policy_version",
        existing_type=sa.String(64),
        server_default=None,
    )
    op.create_check_constraint(
        "ck_asset_version_image_facts",
        "asset_versions",
        "(detected_mime IS NULL AND image_format IS NULL "
        "AND width IS NULL AND height IS NULL AND frame_count IS NULL) OR "
        "(detected_mime IS NOT NULL AND image_format IS NOT NULL "
        "AND width > 0 AND height > 0 AND frame_count > 0)",
    )

    op.alter_column(
        "asset_objects",
        "provider_version_id",
        existing_type=sa.String(256),
        type_=_EXACT_256,
        existing_nullable=False,
    )
    op.create_unique_constraint(
        "uq_asset_object_workspace_id_version",
        "asset_objects",
        ["workspace_id", "id", "asset_version_id"],
    )

    op.create_table(
        "asset_validation_results",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("workspace_id", _WORKSPACE, nullable=False),
        sa.Column("operation_id", sa.String(36), nullable=False),
        sa.Column("asset_version_id", sa.String(36), nullable=False),
        sa.Column("asset_object_id", sa.String(36), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("validator_name", sa.String(64), nullable=False),
        sa.Column("validator_version", sa.String(128), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("verdict", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=True),
        sa.Column("object_provider_version_id", _EXACT_256, nullable=False),
        sa.Column("object_etag", _EXACT_512, nullable=False),
        sa.Column("content_sha256", _EXACT_64, nullable=False),
        sa.Column("evidence_json", mysql.JSON(), nullable=False),
        sa.Column("retention_deadline", _DATETIME, nullable=True),
        sa.Column("created_at", _DATETIME, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_asset_validation_workspace_id",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "asset_version_id",
            "attempt_number",
            "stage",
            "validator_name",
            "validator_version",
            "policy_version",
            name="uq_asset_validation_stage_attempt",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "operation_id"],
            ["durable_operations.workspace_id", "durable_operations.id"],
            name="fk_asset_validation_workspace_operation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "asset_version_id"],
            ["asset_versions.workspace_id", "asset_versions.id"],
            name="fk_asset_validation_workspace_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "asset_object_id", "asset_version_id"],
            ["asset_objects.workspace_id", "asset_objects.id", "asset_objects.asset_version_id"],
            name="fk_asset_validation_workspace_object_version",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "attempt_number > 0",
            name="ck_asset_validation_attempt",
        ),
        sa.CheckConstraint(
            "stage IN ('LOCAL_FORMAT', 'MALWARE', 'CONTENT_SAFETY', "
            "'PROVENANCE', 'PROMOTION')",
            name="ck_asset_validation_stage",
        ),
        sa.CheckConstraint(
            "CHAR_LENGTH(TRIM(validator_name)) BETWEEN 1 AND 64 "
            "AND CHAR_LENGTH(TRIM(validator_version)) BETWEEN 1 AND 128 "
            "AND CHAR_LENGTH(TRIM(policy_version)) BETWEEN 1 AND 64",
            name="ck_asset_validation_validator_identity",
        ),
        sa.CheckConstraint(
            "(verdict IN ('PASS', 'NOT_APPLICABLE') AND reason_code IS NULL) "
            "OR (verdict IN ('REVIEW', 'BLOCK', 'RETRYABLE_FAILURE') "
            "AND reason_code IS NOT NULL)",
            name="ck_asset_validation_verdict_reason",
        ),
        sa.CheckConstraint(
            "CHAR_LENGTH(TRIM(object_provider_version_id)) > 0 "
            "AND LOWER(TRIM(object_provider_version_id)) <> 'null'",
            name="ck_asset_validation_provider_version",
        ),
        sa.CheckConstraint(
            "CHAR_LENGTH(TRIM(object_etag)) > 0",
            name="ck_asset_validation_object_identity",
        ),
        sa.CheckConstraint(
            "content_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_asset_validation_content_sha256",
        ),
        sa.CheckConstraint(
            "retention_deadline IS NULL OR retention_deadline > created_at",
            name="ck_asset_validation_retention",
        ),
        sa.CheckConstraint(
            "JSON_TYPE(evidence_json) = 'OBJECT'",
            name="ck_asset_validation_evidence_object",
        ),
        **_TABLE_OPTIONS,
    )
    op.create_index(
        "ix_asset_validation_version_stage",
        "asset_validation_results",
        ["workspace_id", "asset_version_id", "stage", "created_at", "id"],
    )
    op.create_index(
        "ix_asset_validation_operation_attempt",
        "asset_validation_results",
        ["workspace_id", "operation_id", "attempt_number", "created_at"],
    )
    op.create_index(
        "ix_asset_validation_retention",
        "asset_validation_results",
        ["retention_deadline", "id"],
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_asset_validation_results_no_update "
            "BEFORE UPDATE ON asset_validation_results FOR EACH ROW "
            "SIGNAL SQLSTATE '45000' "
            "SET MESSAGE_TEXT = 'asset validation results are append-only'"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT id FROM asset_validation_results LIMIT 1")).first():
        raise RuntimeError(
            "cannot downgrade asset validation storage while validation evidence exists"
        )
    unsafe_version = bind.execute(
        sa.text(
            "SELECT id FROM asset_versions "
            "WHERE detected_mime IS NULL OR image_format IS NULL "
            "OR width IS NULL OR height IS NULL "
            "OR frame_count IS NULL LIMIT 1"
        )
    ).first()
    if unsafe_version is not None:
        raise RuntimeError(
            "cannot downgrade asset validation storage while non-image versions exist"
        )
    if bind.execute(
        sa.text("SELECT id FROM assets WHERE block_reason IS NOT NULL LIMIT 1")
    ).first():
        raise RuntimeError(
            "cannot downgrade asset validation storage while block reasons exist"
        )

    op.execute(sa.text("DROP TRIGGER trg_asset_validation_results_no_update"))
    op.drop_table("asset_validation_results")
    op.drop_constraint(
        "uq_asset_object_workspace_id_version",
        "asset_objects",
        type_="unique",
    )
    op.alter_column(
        "asset_objects",
        "provider_version_id",
        existing_type=_EXACT_256,
        type_=sa.String(256),
        existing_nullable=False,
    )
    op.drop_constraint(
        "ck_asset_version_image_facts",
        "asset_versions",
        type_="check",
    )
    op.drop_column("asset_versions", "validation_policy_version")
    op.alter_column(
        "asset_versions",
        "detected_mime",
        existing_type=sa.String(128),
        nullable=False,
    )
    op.alter_column(
        "asset_versions",
        "frame_count",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.alter_column(
        "asset_versions",
        "height",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.alter_column(
        "asset_versions",
        "width",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.alter_column(
        "asset_versions",
        "image_format",
        existing_type=sa.String(16),
        nullable=False,
    )
    op.drop_constraint("ck_assets_block_reason", "assets", type_="check")
    op.drop_column("assets", "block_reason")
