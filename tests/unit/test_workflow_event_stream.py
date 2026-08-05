from datetime import UTC, datetime, timedelta

import pytest
from commercevision_application.workflow_event_cursors import WorkflowEventCursorCodec
from commercevision_application.workflow_events import WorkflowEventStreamService
from commercevision_domain import NotFoundError, Workflow
from commercevision_domain.messaging import EventEnvelope, OutboxEvent

NOW = datetime(2026, 8, 5, 14, 0, tzinfo=UTC)
WORKFLOW_ID = "019fac40-0000-7000-8000-000000000001"


def _event(*, event_id: str, occurred_at: datetime, version: int) -> OutboxEvent:
    return OutboxEvent(
        envelope=EventEnvelope(
            event_id=event_id,
            event_type="workflow.run.requested",
            schema_version=1,
            aggregate_type="workflow",
            aggregate_id=WORKFLOW_ID,
            aggregate_version=version,
            occurred_at=occurred_at,
            trace_id=f"trace-{version}",
            payload={
                "workflow_id": WORKFLOW_ID,
                "action": "start",
                "raw_prompt": "must-not-leave-the-application-boundary",
            },
        ),
        available_at=occurred_at,
        workspace_id="workspace-a",
    )


class _Workflows:
    def __init__(self, workflow: Workflow) -> None:
        self.workflow = workflow

    def get(self, workflow_id: str, *, workspace_id: str, for_update: bool = False) -> Workflow:
        del for_update
        assert workflow_id == WORKFLOW_ID
        assert workspace_id == "workspace-a"
        return self.workflow


class _Outbox:
    def __init__(self, events: list[OutboxEvent]) -> None:
        self.events = events
        self.calls: list[dict[str, object]] = []

    def list_for_workflow_stream(self, **kwargs: object) -> list[OutboxEvent]:
        self.calls.append(kwargs)
        return self.events


class _Uow:
    def __init__(self, workflow: Workflow, events: list[OutboxEvent]) -> None:
        self.workflows = _Workflows(workflow)
        self.outbox = _Outbox(events)

    def __enter__(self) -> "_Uow":
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def database_now(self) -> datetime:
        return NOW


def test_event_page_reads_persisted_order_and_cursors_the_last_delivered_event() -> None:
    workflow = Workflow.create(
        workspace_id="workspace-a",
        created_by="actor-a",
        workflow_type="COMMERCE_IMAGE_GENERATION",
        input_data={},
        retention=timedelta(hours=1),
        now=NOW,
    )
    workflow.id = WORKFLOW_ID
    events = [
        _event(
            event_id="019fac40-0000-7000-8000-000000000002",
            occurred_at=NOW + timedelta(seconds=1),
            version=1,
        ),
        _event(
            event_id="019fac40-0000-7000-8000-000000000003",
            occurred_at=NOW + timedelta(seconds=2),
            version=2,
        ),
    ]
    uow = _Uow(workflow, events)
    codec = WorkflowEventCursorCodec(
        current_key_id="current",
        current_secret="c" * 32,
        max_age_seconds=3600,
        future_skew_seconds=30,
        clock=lambda: NOW,
    )
    service = WorkflowEventStreamService(
        uow_factory=lambda: uow,  # type: ignore[arg-type]
        cursor_codec=codec,
        page_size=1,
    )

    page = service.event_page(
        workflow_id=WORKFLOW_ID,
        workspace_id="workspace-a",
        cursor=None,
    )

    assert [item.event.event_id for item in page.items] == [events[0].envelope.event_id]
    assert page.items[0].event.payload == {
        "workflow_id": WORKFLOW_ID,
        "action": "start",
    }
    assert page.items[0].cursor == page.next_cursor
    assert page.next_cursor is not None
    assert codec.decode(
        page.next_cursor,
        workspace_id="workspace-a",
        workflow_id=WORKFLOW_ID,
        retain_until=workflow.expires_at,
    ) == (events[0].envelope.occurred_at, events[0].envelope.event_id)
    assert uow.outbox.calls == [
        {
            "workspace_id": "workspace-a",
            "workflow_id": WORKFLOW_ID,
            "after": None,
            "limit": 2,
        }
    ]


def test_event_page_hides_workflow_at_its_retention_deadline() -> None:
    workflow = Workflow.create(
        workspace_id="workspace-a",
        created_by="actor-a",
        workflow_type="COMMERCE_IMAGE_GENERATION",
        input_data={},
        retention=timedelta(hours=1),
        now=NOW - timedelta(hours=1),
    )
    workflow.id = WORKFLOW_ID
    uow = _Uow(workflow, [])
    service = WorkflowEventStreamService(
        uow_factory=lambda: uow,  # type: ignore[arg-type]
        cursor_codec=WorkflowEventCursorCodec(
            current_key_id="current",
            current_secret="c" * 32,
            max_age_seconds=3600,
            future_skew_seconds=30,
            clock=lambda: NOW,
        ),
        page_size=100,
    )

    with pytest.raises(NotFoundError):
        service.event_page(
            workflow_id=WORKFLOW_ID,
            workspace_id="workspace-a",
            cursor=None,
        )
    assert uow.outbox.calls == []
