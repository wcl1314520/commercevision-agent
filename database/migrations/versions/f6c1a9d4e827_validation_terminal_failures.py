"""Represent terminal validation provider failures.

Revision ID: f6c1a9d4e827
Revises: e5f8b2d6c914
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6c1a9d4e827"
down_revision: str | Sequence[str] | None = "e5f8b2d6c914"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TERMINAL_FAILURE_CHECK = (
    "(verdict IN ('PASS', 'NOT_APPLICABLE') AND reason_code IS NULL) "
    "OR (verdict IN ('REVIEW', 'BLOCK', 'RETRYABLE_FAILURE', "
    "'TERMINAL_FAILURE') AND reason_code IS NOT NULL)"
)
_PREVIOUS_CHECK = (
    "(verdict IN ('PASS', 'NOT_APPLICABLE') AND reason_code IS NULL) "
    "OR (verdict IN ('REVIEW', 'BLOCK', 'RETRYABLE_FAILURE') "
    "AND reason_code IS NOT NULL)"
)


def upgrade() -> None:
    op.drop_constraint(
        "ck_asset_validation_verdict_reason",
        "asset_validation_results",
        type_="check",
    )
    op.create_check_constraint(
        "ck_asset_validation_verdict_reason",
        "asset_validation_results",
        _TERMINAL_FAILURE_CHECK,
    )


def downgrade() -> None:
    terminal_rows = op.get_bind().execute(
        sa.text(
            "SELECT COUNT(*) FROM asset_validation_results "
            "WHERE verdict = 'TERMINAL_FAILURE'"
        )
    ).scalar_one()
    if terminal_rows:
        raise RuntimeError(
            "cannot downgrade while immutable terminal validation evidence exists"
        )
    op.drop_constraint(
        "ck_asset_validation_verdict_reason",
        "asset_validation_results",
        type_="check",
    )
    op.create_check_constraint(
        "ck_asset_validation_verdict_reason",
        "asset_validation_results",
        _PREVIOUS_CHECK,
    )
