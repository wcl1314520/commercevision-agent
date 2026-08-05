from datetime import UTC, datetime, timedelta

import pytest
from commercevision_application.workflow_event_cursors import WorkflowEventCursorCodec

NOW = datetime(2026, 8, 5, 14, 0, tzinfo=UTC)
WORKFLOW_ID = "019fac40-0000-7000-8000-000000000001"
EVENT_ID = "019fac40-0000-7000-8000-000000000002"


def test_workflow_event_cursor_round_trips_exact_scoped_boundary() -> None:
    codec = WorkflowEventCursorCodec(
        current_key_id="current",
        current_secret="c" * 32,
        max_age_seconds=3600,
        future_skew_seconds=30,
        clock=lambda: NOW,
    )
    occurred_at = NOW - timedelta(seconds=5)
    retain_until = NOW + timedelta(minutes=30)

    token = codec.encode(
        workspace_id="workspace-a",
        workflow_id=WORKFLOW_ID,
        occurred_at=occurred_at,
        event_id=EVENT_ID,
        retain_until=retain_until,
    )

    assert codec.decode(
        token,
        workspace_id="workspace-a",
        workflow_id=WORKFLOW_ID,
        retain_until=retain_until,
    ) == (occurred_at, EVENT_ID)
    assert EVENT_ID not in token
    assert WORKFLOW_ID not in token


@pytest.mark.parametrize(
    ("workspace_id", "workflow_id", "retention_delta"),
    [
        ("workspace-b", WORKFLOW_ID, timedelta()),
        ("workspace-a", "019fac40-0000-7000-8000-000000000099", timedelta()),
        ("workspace-a", WORKFLOW_ID, timedelta(seconds=1)),
    ],
)
def test_workflow_event_cursor_rejects_foreign_or_changed_scope(
    workspace_id: str,
    workflow_id: str,
    retention_delta: timedelta,
) -> None:
    retain_until = NOW + timedelta(minutes=30)
    codec = WorkflowEventCursorCodec(
        current_key_id="current",
        current_secret="c" * 32,
        max_age_seconds=3600,
        future_skew_seconds=30,
        clock=lambda: NOW,
    )
    token = codec.encode(
        workspace_id="workspace-a",
        workflow_id=WORKFLOW_ID,
        occurred_at=NOW - timedelta(seconds=1),
        event_id=EVENT_ID,
        retain_until=retain_until,
    )

    with pytest.raises(ValueError, match="^Workflow event cursor is invalid$"):
        codec.decode(
            token,
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            retain_until=retain_until + retention_delta,
        )


def test_workflow_event_cursor_rejects_tamper_oversize_and_expiry_identically() -> None:
    clock = [NOW]
    retain_until = NOW + timedelta(hours=2)
    codec = WorkflowEventCursorCodec(
        current_key_id="current",
        current_secret="c" * 32,
        max_age_seconds=3600,
        future_skew_seconds=30,
        clock=lambda: clock[0],
    )
    token = codec.encode(
        workspace_id="workspace-a",
        workflow_id=WORKFLOW_ID,
        occurred_at=NOW,
        event_id=EVENT_ID,
        retain_until=retain_until,
    )
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    clock[0] = NOW + timedelta(hours=1, microseconds=1)

    for invalid in (tampered, "x" * 257, token):
        with pytest.raises(ValueError, match="^Workflow event cursor is invalid$"):
            codec.decode(
                invalid,
                workspace_id="workspace-a",
                workflow_id=WORKFLOW_ID,
                retain_until=retain_until,
            )


def test_workflow_event_cursor_accepts_previous_key_during_rotation() -> None:
    retain_until = NOW + timedelta(minutes=30)
    old = WorkflowEventCursorCodec(
        current_key_id="old",
        current_secret="o" * 32,
        max_age_seconds=3600,
        future_skew_seconds=30,
        clock=lambda: NOW,
    )
    token = old.encode(
        workspace_id="workspace-a",
        workflow_id=WORKFLOW_ID,
        occurred_at=NOW,
        event_id=EVENT_ID,
        retain_until=retain_until,
    )
    rotated = WorkflowEventCursorCodec(
        current_key_id="new",
        current_secret="n" * 32,
        previous_key_id="old",
        previous_secret="o" * 32,
        max_age_seconds=3600,
        future_skew_seconds=30,
        clock=lambda: NOW,
    )

    assert rotated.decode(
        token,
        workspace_id="workspace-a",
        workflow_id=WORKFLOW_ID,
        retain_until=retain_until,
    ) == (NOW, EVENT_ID)
