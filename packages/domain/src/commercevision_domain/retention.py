"""Canonical retention boundaries shared by task-scoped domain resources."""

from datetime import datetime, timedelta

TASK_RETENTION_MAX_HOURS = 72
_TASK_RETENTION_LIMIT = timedelta(hours=TASK_RETENTION_MAX_HOURS)


def canonical_task_retention_deadline(
    *,
    created_at: datetime,
    expires_at: datetime,
) -> datetime:
    """Cap a Workflow-provided deadline at the immutable Task retention limit."""

    return min(expires_at, created_at + _TASK_RETENTION_LIMIT)
