"""Persist the immutable model route request projection.

Revision ID: fb9e4c6a1205
Revises: fa7d3c8e1204
Create Date: 2026-08-06 23:30:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "fb9e4c6a1205"
down_revision = "fa7d3c8e1204"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "model_route_decisions",
        sa.Column("route_request_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("model_route_decisions", "route_request_json")
