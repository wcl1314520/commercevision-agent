"""Add the tenant-scoped Workflow event stream keyset index.

Revision ID: d9a6e4b2c517
Revises: c8f4d2a6e103
Create Date: 2026-08-05 22:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "d9a6e4b2c517"
down_revision: str | Sequence[str] | None = "c8f4d2a6e103"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_outbox_workflow_stream",
        "outbox_events",
        ["workspace_id", "aggregate_type", "aggregate_id", "occurred_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_workflow_stream", table_name="outbox_events")
