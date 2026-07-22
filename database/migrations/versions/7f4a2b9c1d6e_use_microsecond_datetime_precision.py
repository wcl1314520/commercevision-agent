"""use microsecond datetime precision

Revision ID: 7f4a2b9c1d6e
Revises: 0d341554cdf1
Create Date: 2026-07-22 13:30:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "7f4a2b9c1d6e"
down_revision: str | Sequence[str] | None = "0d341554cdf1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DATETIME_COLUMNS: dict[str, tuple[tuple[str, bool], ...]] = {
    "agent_checkpoint_writes": (("created_at", False),),
    "agent_checkpoints": (
        ("created_at", False),
        ("expires_at", True),
    ),
    "audit_events": (
        ("created_at", False),
        ("expires_at", False),
    ),
    "dead_letter_messages": (
        ("original_created_at", False),
        ("created_at", False),
        ("replayed_at", True),
    ),
    "idempotency_keys": (
        ("created_at", False),
        ("expires_at", False),
    ),
    "inbox_messages": (
        ("lease_expires_at", True),
        ("processed_at", True),
        ("created_at", False),
        ("updated_at", False),
    ),
    "outbox_events": (
        ("occurred_at", False),
        ("available_at", False),
        ("published_at", True),
        ("locked_until", True),
    ),
    "workflows": (
        ("expires_at", False),
        ("cancellation_requested_at", True),
        ("created_at", False),
        ("updated_at", False),
    ),
    "workflow_approvals": (("created_at", False),),
    "workflow_steps": (
        ("lease_expires_at", True),
        ("next_attempt_at", True),
        ("started_at", True),
        ("completed_at", True),
        ("created_at", False),
        ("updated_at", False),
    ),
    "workflow_attempts": (
        ("created_at", False),
        ("updated_at", False),
        ("started_at", True),
        ("completed_at", True),
    ),
}


def _alter_datetime_precision(precision: int) -> None:
    data_type = "DATETIME" if precision == 0 else f"DATETIME({precision})"
    for table_name, columns in _DATETIME_COLUMNS.items():
        column_changes = ", ".join(
            f"MODIFY COLUMN `{column_name}` {data_type} "
            f"{'NULL' if nullable else 'NOT NULL'}"
            for column_name, nullable in columns
        )
        op.execute(
            f"ALTER TABLE `{table_name}` {column_changes}, "
            "ALGORITHM=COPY, LOCK=SHARED"
        )


def upgrade() -> None:
    """Preserve microseconds for ordering, lease, and retry timestamps."""

    _alter_datetime_precision(6)


def downgrade() -> None:
    """Remove fractional seconds, with the original rounding semantics."""

    _alter_datetime_precision(0)
