from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from commercevision_application.creative_plan_cursors import CreativePlanCursorCodec

NOW = datetime(2026, 8, 5, 6, 30, tzinfo=UTC)
WORKFLOW_ID = "019b0000-0000-7000-8000-000000000810"
PLAN_ID = "019b0000-0000-7000-8000-000000000813"


def _codec(*, now: datetime = NOW) -> CreativePlanCursorCodec:
    return CreativePlanCursorCodec(
        current_key_id="current",
        current_secret="c" * 32,
        max_age_seconds=300,
        future_skew_seconds=30,
        clock=lambda: now,
    )


def test_creative_plan_cursor_round_trips_exact_query_scope() -> None:
    codec = _codec()

    token = codec.encode(
        workspace_id="planning-domain",
        workflow_id=WORKFLOW_ID,
        creative_plan_id=PLAN_ID,
        version_number=7,
    )

    assert len(token) <= 256
    assert (
        codec.decode(
            token,
            workspace_id="planning-domain",
            workflow_id=WORKFLOW_ID,
            creative_plan_id=PLAN_ID,
        )
        == 7
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("workspace_id", "foreign-workspace"),
        ("workflow_id", "019b0000-0000-7000-8000-000000000899"),
        ("creative_plan_id", "019b0000-0000-7000-8000-000000000898"),
    ],
)
def test_creative_plan_cursor_rejects_foreign_scope(field: str, value: str) -> None:
    codec = _codec()
    token = codec.encode(
        workspace_id="planning-domain",
        workflow_id=WORKFLOW_ID,
        creative_plan_id=PLAN_ID,
        version_number=7,
    )
    scope = {
        "workspace_id": "planning-domain",
        "workflow_id": WORKFLOW_ID,
        "creative_plan_id": PLAN_ID,
    }
    scope[field] = value

    with pytest.raises(ValueError, match="Creative Plan cursor is invalid"):
        codec.decode(token, **scope)


def test_creative_plan_cursor_rejects_tampering_and_expiry() -> None:
    token = _codec().encode(
        workspace_id="planning-domain",
        workflow_id=WORKFLOW_ID,
        creative_plan_id=PLAN_ID,
        version_number=7,
    )
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")

    for candidate, codec in (
        (tampered, _codec()),
        (token, _codec(now=NOW + timedelta(seconds=301))),
    ):
        with pytest.raises(ValueError, match="Creative Plan cursor is invalid"):
            codec.decode(
                candidate,
                workspace_id="planning-domain",
                workflow_id=WORKFLOW_ID,
                creative_plan_id=PLAN_ID,
            )
