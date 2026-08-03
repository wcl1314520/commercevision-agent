"""retrieval runs and preview grants

Revision ID: a3f8c2d9e714
Revises: f5a1c3e7b902
Create Date: 2026-08-03 20:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "a3f8c2d9e714"
down_revision = "f5a1c3e7b902"
branch_labels = None
depends_on = None

_BINARY_COLLATION = "utf8mb4_0900_bin"


def upgrade() -> None:
    op.create_table(
        "retrieval_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(128, collation=_BINARY_COLLATION), nullable=False),
        sa.Column("requester_id", sa.String(128, collation=_BINARY_COLLATION), nullable=False),
        sa.Column("query_json", mysql.JSON(), nullable=False),
        sa.Column("query_sha256", sa.String(64, collation=_BINARY_COLLATION), nullable=False),
        sa.Column(
            "retrieval_policy_version",
            sa.String(128, collation=_BINARY_COLLATION),
            nullable=False,
        ),
        sa.Column("complete_hybrid", sa.Boolean(), nullable=False),
        sa.Column("degradations_json", mysql.JSON(), nullable=False),
        sa.Column("eligible_asset_version_count", sa.Integer(), nullable=False),
        sa.Column("fused_candidate_count", sa.Integer(), nullable=False),
        sa.Column("final_authorized_candidate_count", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("expires_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.CheckConstraint(
            "query_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_retrieval_runs_query_sha256",
        ),
        sa.CheckConstraint(
            "eligible_asset_version_count >= fused_candidate_count "
            "AND fused_candidate_count >= final_authorized_candidate_count "
            "AND final_authorized_candidate_count >= 0",
            name="ck_retrieval_runs_candidate_counts",
        ),
        sa.CheckConstraint("latency_ms >= 0", name="ck_retrieval_runs_latency_ms"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_retrieval_runs_workspace_id"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index(
        "ix_retrieval_runs_workspace_created",
        "retrieval_runs",
        ["workspace_id", "created_at", "id"],
    )
    op.create_index("ix_retrieval_runs_expiry", "retrieval_runs", ["expires_at", "id"])
    op.create_table(
        "retrieval_results",
        sa.Column("retrieval_run_id", sa.String(36), nullable=False),
        sa.Column("result_rank", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.String(128, collation=_BINARY_COLLATION), nullable=False),
        sa.Column("asset_id", sa.String(36), nullable=False),
        sa.Column("asset_version_id", sa.String(36), nullable=False),
        sa.Column("rights_record_id", sa.String(36), nullable=False),
        sa.Column("rights_record_version", sa.Integer(), nullable=False),
        sa.Column("brand_profile_version", sa.Integer(), nullable=True),
        sa.Column("channels_json", mysql.JSON(), nullable=False),
        sa.Column("score_json", mysql.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("decided_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column(
            "preview_token_sha256",
            sa.String(64, collation=_BINARY_COLLATION),
            nullable=False,
        ),
        sa.Column("preview_expires_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.CheckConstraint("result_rank > 0", name="ck_retrieval_results_rank"),
        sa.CheckConstraint(
            "preview_token_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_retrieval_results_preview_token_sha256",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "retrieval_run_id"],
            ["retrieval_runs.workspace_id", "retrieval_runs.id"],
            name="fk_retrieval_results_run",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "retrieval_run_id", "result_rank", name="pk_retrieval_results"
        ),
        sa.UniqueConstraint(
            "preview_token_sha256",
            name="uq_retrieval_results_preview_token_sha256",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index(
        "ix_retrieval_results_asset",
        "retrieval_results",
        ["workspace_id", "asset_version_id"],
    )
    op.create_index(
        "ix_retrieval_results_preview_expiry",
        "retrieval_results",
        ["preview_expires_at"],
    )


def downgrade() -> None:
    op.drop_table("retrieval_results")
    op.drop_table("retrieval_runs")
