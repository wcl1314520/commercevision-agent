"""Bind validation data transfer policy snapshots to immutable Asset facts.

Revision ID: a7c4e2d9b831
Revises: f6c1a9d4e827
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c4e2d9b831"
down_revision: str | Sequence[str] | None = "f6c1a9d4e827"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEGACY_DENY_VERSION = "legacy-validation-transfer-deny-v1"
_LEGACY_DENY_SNAPSHOT = "0" * 64
_EXACT_64 = sa.String(64, collation="utf8mb4_0900_bin")


def upgrade() -> None:
    for table_name in ("upload_sessions", "asset_versions"):
        op.add_column(
            table_name,
            sa.Column(
                "validation_transfer_policy_version",
                sa.String(64),
                nullable=False,
                server_default=_LEGACY_DENY_VERSION,
            ),
        )
        op.add_column(
            table_name,
            sa.Column(
                "validation_transfer_policy_snapshot_sha256",
                _EXACT_64,
                nullable=False,
                server_default=_LEGACY_DENY_SNAPSHOT,
            ),
        )
        op.alter_column(
            table_name,
            "validation_transfer_policy_version",
            existing_type=sa.String(64),
            server_default=None,
        )
        op.alter_column(
            table_name,
            "validation_transfer_policy_snapshot_sha256",
            existing_type=_EXACT_64,
            server_default=None,
        )
    op.create_check_constraint(
        "ck_upload_session_transfer_policy_version",
        "upload_sessions",
        "CHAR_LENGTH(TRIM(validation_transfer_policy_version)) > 0",
    )
    op.create_check_constraint(
        "ck_upload_session_transfer_policy_snapshot",
        "upload_sessions",
        "validation_transfer_policy_snapshot_sha256 REGEXP '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "ck_asset_version_transfer_policy_version",
        "asset_versions",
        "CHAR_LENGTH(TRIM(validation_transfer_policy_version)) > 0",
    )
    op.create_check_constraint(
        "ck_asset_version_transfer_policy_snapshot",
        "asset_versions",
        "validation_transfer_policy_snapshot_sha256 REGEXP '^[0-9a-f]{64}$'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_asset_version_transfer_policy_snapshot",
        "asset_versions",
        type_="check",
    )
    op.drop_constraint(
        "ck_asset_version_transfer_policy_version",
        "asset_versions",
        type_="check",
    )
    op.drop_constraint(
        "ck_upload_session_transfer_policy_snapshot",
        "upload_sessions",
        type_="check",
    )
    op.drop_constraint(
        "ck_upload_session_transfer_policy_version",
        "upload_sessions",
        type_="check",
    )
    for table_name in ("asset_versions", "upload_sessions"):
        op.drop_column(
            table_name,
            "validation_transfer_policy_snapshot_sha256",
        )
        op.drop_column(table_name, "validation_transfer_policy_version")
