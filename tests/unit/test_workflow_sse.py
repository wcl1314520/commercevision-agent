import json
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from commercevision_api.workflow_routes import workflow_events
from commercevision_api.workflow_sse import (
    WorkflowSseClientTracker,
    WorkflowSsePolicy,
    encode_retry_hint,
    encode_workflow_event,
    stream_workflow_events,
)
from commercevision_application.workflow_events import WorkflowEventDelivery, WorkflowEventPage
from commercevision_contracts import Settings
from commercevision_contracts.workflow import EventResponse
from starlette.requests import Request
from starlette.responses import StreamingResponse


def test_workflow_sse_frame_carries_opaque_resume_cursor_and_safe_json() -> None:
    delivery = WorkflowEventDelivery(
        event=EventResponse(
            event_id="019fac40-0000-7000-8000-000000000002",
            event_type="workflow.resume-requested",
            schema_version=1,
            aggregate_type="workflow",
            aggregate_id="019fac40-0000-7000-8000-000000000001",
            aggregate_version=4,
            occurred_at=datetime(2026, 8, 5, 14, 0, tzinfo=UTC),
            trace_id="trace-safe",
            payload={"status": "GENERATING", "message": "line one\nline two"},
        ),
        cursor="v1.current.opaque.signature",
    )

    frame = encode_workflow_event(delivery, retry_milliseconds=3000).decode("utf-8")

    assert frame.startswith("retry: 3000\nid: v1.current.opaque.signature\n")
    assert "\nevent: workflow.event\n" in frame
    assert frame.endswith("\n\n")
    data_line = next(line for line in frame.splitlines() if line.startswith("data: "))
    body = json.loads(data_line.removeprefix("data: "))
    assert body["event_type"] == "workflow.resume-requested"
    assert body["payload"]["message"] == "line one\nline two"
    assert "line one\nline two" not in data_line


class _WorkflowEvents:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def event_page(self, **kwargs: object) -> WorkflowEventPage:
        self.calls.append(kwargs)
        return WorkflowEventPage(items=(), next_cursor=None)


class _DisconnectAfterDelivery:
    def __init__(self) -> None:
        self.calls = 0

    async def is_disconnected(self) -> bool:
        self.calls += 1
        return self.calls > 2


class _UnexpectedPoll:
    def event_page(self, **kwargs: object) -> WorkflowEventPage:
        raise AssertionError(f"disconnected stream polled again: {kwargs}")


class _MutableDisconnect:
    disconnected = False

    async def is_disconnected(self) -> bool:
        return self.disconnected


class _EmptyPages:
    def __init__(self) -> None:
        self.calls = 0

    def event_page(self, **kwargs: object) -> WorkflowEventPage:
        del kwargs
        self.calls += 1
        return WorkflowEventPage(items=(), next_cursor=None)


def test_workflow_sse_retry_hint_is_bounded_and_standalone() -> None:
    assert encode_retry_hint(3000) == b"retry: 3000\n\n"
    for invalid in (99, 30_001, True):
        with pytest.raises(ValueError, match="Workflow SSE retry interval is invalid"):
            encode_retry_hint(invalid)


@pytest.mark.asyncio
async def test_workflow_sse_disconnect_stops_without_another_database_poll() -> None:
    delivery = WorkflowEventDelivery(
        event=EventResponse(
            event_id="019fac40-0000-7000-8000-000000000002",
            event_type="workflow.cancelled",
            schema_version=1,
            aggregate_type="workflow",
            aggregate_id="019fac40-0000-7000-8000-000000000001",
            aggregate_version=4,
            occurred_at=datetime(2026, 8, 5, 14, 0, tzinfo=UTC),
            trace_id="trace-safe",
            payload={"workflow_id": "019fac40-0000-7000-8000-000000000001"},
        ),
        cursor="v1.current.opaque.signature",
    )
    chunks = [
        chunk
        async for chunk in stream_workflow_events(
            request=_DisconnectAfterDelivery(),  # type: ignore[arg-type]
            service=_UnexpectedPoll(),  # type: ignore[arg-type]
            workspace_id="workspace-a",
            workflow_id="019fac40-0000-7000-8000-000000000001",
            cursor=None,
            first_page=WorkflowEventPage(items=(delivery,), next_cursor=delivery.cursor),
            policy=WorkflowSsePolicy(
                poll_interval_seconds=1,
                heartbeat_seconds=15,
                retry_milliseconds=3000,
                max_session_seconds=300,
            ),
        )
    ]

    assert chunks[0] == b"retry: 3000\n\n"
    assert len(chunks) == 2
    assert b"id: v1.current.opaque.signature" in chunks[1]


@pytest.mark.asyncio
async def test_workflow_sse_observes_reconnect_lag_and_client_lifecycle() -> None:
    occurred_at = datetime(2026, 8, 5, 14, 0, tzinfo=UTC)
    delivery = WorkflowEventDelivery(
        event=EventResponse(
            event_id="019fac40-0000-7000-8000-000000000002",
            event_type="workflow.cancelled",
            schema_version=1,
            aggregate_type="workflow",
            aggregate_id="019fac40-0000-7000-8000-000000000001",
            aggregate_version=4,
            occurred_at=occurred_at,
            trace_id="trace-safe",
            payload={"arbitrary_user_text": "must never reach telemetry"},
        ),
        cursor="v1.current.opaque.signature",
    )
    observations: list[tuple[str, dict[str, object]]] = []

    class Observer:
        @contextmanager
        def observe(self, **values):
            observations.append(("span", values))
            yield

        def annotate(self, **values):
            observations.append(("annotate", values))

        def record_sse(self, **values):
            observations.append(("sse", values))

    stream = stream_workflow_events(
        request=_DisconnectAfterDelivery(),  # type: ignore[arg-type]
        service=_UnexpectedPoll(),  # type: ignore[arg-type]
        workspace_id="workspace-a",
        workflow_id="019fac40-0000-7000-8000-000000000001",
        cursor="v1.previous.opaque.signature",
        first_page=WorkflowEventPage(items=(delivery,), next_cursor=delivery.cursor),
        policy=WorkflowSsePolicy(
            poll_interval_seconds=1,
            heartbeat_seconds=15,
            retry_milliseconds=3000,
            max_session_seconds=300,
        ),
        observer=Observer(),
        client_tracker=WorkflowSseClientTracker(),
        utc_now=lambda: datetime(2026, 8, 5, 14, 0, 2, tzinfo=UTC),
    )

    assert [chunk async for chunk in stream]
    assert observations == [
        (
            "span",
            {
                "step": "sse",
                "trace_id": None,
                "workflow_id": "019fac40-0000-7000-8000-000000000001",
            },
        ),
        (
            "sse",
            {
                "outcome": "connected",
                "reconnect": True,
                "active_clients": 1,
                "lag_seconds": 0.0,
            },
        ),
        (
            "annotate",
            {
                "event_id": "019fac40-0000-7000-8000-000000000002",
            },
        ),
        (
            "sse",
            {
                "outcome": "emitted",
                "reconnect": False,
                "active_clients": 1,
                "lag_seconds": 2.0,
            },
        ),
        (
            "sse",
            {
                "outcome": "disconnected",
                "reconnect": False,
                "active_clients": 0,
                "lag_seconds": 0.0,
            },
        ),
    ]


@pytest.mark.asyncio
async def test_empty_prefetched_page_sends_retry_before_any_second_poll() -> None:
    stream = stream_workflow_events(
        request=_DisconnectAfterDelivery(),  # type: ignore[arg-type]
        service=_UnexpectedPoll(),  # type: ignore[arg-type]
        workspace_id="workspace-a",
        workflow_id="019fac40-0000-7000-8000-000000000001",
        cursor=None,
        first_page=WorkflowEventPage(items=(), next_cursor=None),
        policy=WorkflowSsePolicy(
            poll_interval_seconds=1,
            heartbeat_seconds=15,
            retry_milliseconds=3000,
            max_session_seconds=300,
        ),
    )

    assert await anext(stream) == b"retry: 3000\n\n"
    await stream.aclose()


@pytest.mark.asyncio
async def test_empty_prefetched_page_waits_before_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _MutableDisconnect()
    sleeps: list[float] = []

    async def _disconnect_during_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        request.disconnected = True

    monkeypatch.setattr("commercevision_api.workflow_sse.asyncio.sleep", _disconnect_during_sleep)
    stream = stream_workflow_events(
        request=request,  # type: ignore[arg-type]
        service=_UnexpectedPoll(),  # type: ignore[arg-type]
        workspace_id="workspace-a",
        workflow_id="019fac40-0000-7000-8000-000000000001",
        cursor=None,
        first_page=WorkflowEventPage(items=(), next_cursor=None),
        policy=WorkflowSsePolicy(
            poll_interval_seconds=1,
            heartbeat_seconds=15,
            retry_milliseconds=3000,
            max_session_seconds=300,
        ),
    )

    assert await anext(stream) == b"retry: 3000\n\n"
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert sleeps == [1]


@pytest.mark.asyncio
async def test_idle_workflow_sse_emits_heartbeat_on_bounded_cadence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = [0.0]
    service = _EmptyPages()

    async def _advance(seconds: float) -> None:
        current[0] += seconds

    monkeypatch.setattr("commercevision_api.workflow_sse.asyncio.sleep", _advance)
    stream = stream_workflow_events(
        request=_MutableDisconnect(),  # type: ignore[arg-type]
        service=service,  # type: ignore[arg-type]
        workspace_id="workspace-a",
        workflow_id="019fac40-0000-7000-8000-000000000001",
        cursor=None,
        first_page=WorkflowEventPage(items=(), next_cursor=None),
        policy=WorkflowSsePolicy(
            poll_interval_seconds=0.25,
            heartbeat_seconds=1,
            retry_milliseconds=3000,
            max_session_seconds=15,
        ),
        monotonic=lambda: current[0],
    )

    assert await anext(stream) == b"retry: 3000\n\n"
    assert await anext(stream) == b": heartbeat\n\n"
    assert service.calls == 4
    await stream.aclose()


@pytest.mark.asyncio
async def test_workflow_sse_stops_at_session_budget_without_background_work() -> None:
    current = [0.0]
    stream = stream_workflow_events(
        request=_MutableDisconnect(),  # type: ignore[arg-type]
        service=_UnexpectedPoll(),  # type: ignore[arg-type]
        workspace_id="workspace-a",
        workflow_id="019fac40-0000-7000-8000-000000000001",
        cursor=None,
        first_page=WorkflowEventPage(items=(), next_cursor=None),
        policy=WorkflowSsePolicy(
            poll_interval_seconds=1,
            heartbeat_seconds=15,
            retry_milliseconds=3000,
            max_session_seconds=15,
        ),
        monotonic=lambda: current[0],
    )

    assert await anext(stream) == b"retry: 3000\n\n"
    current[0] = 15
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


@pytest.mark.asyncio
async def test_workflow_sse_stops_at_page_budget_and_keeps_last_cursor() -> None:
    delivery = WorkflowEventDelivery(
        event=EventResponse(
            event_id="019fac40-0000-7000-8000-000000000002",
            event_type="workflow.cancelled",
            schema_version=1,
            aggregate_type="workflow",
            aggregate_id="019fac40-0000-7000-8000-000000000001",
            aggregate_version=4,
            occurred_at=datetime(2026, 8, 5, 14, 0, tzinfo=UTC),
            trace_id="trace-safe",
            payload={"workflow_id": "019fac40-0000-7000-8000-000000000001"},
        ),
        cursor="v1.current.final.signature",
    )
    stream = stream_workflow_events(
        request=_MutableDisconnect(),  # type: ignore[arg-type]
        service=_UnexpectedPoll(),  # type: ignore[arg-type]
        workspace_id="workspace-a",
        workflow_id="019fac40-0000-7000-8000-000000000001",
        cursor=None,
        first_page=WorkflowEventPage(items=(delivery,), next_cursor=delivery.cursor),
        policy=WorkflowSsePolicy(
            poll_interval_seconds=1,
            heartbeat_seconds=15,
            retry_milliseconds=3000,
            max_session_seconds=300,
            max_pages_per_session=1,
        ),
    )

    assert await anext(stream) == b"retry: 3000\n\n"
    assert b"id: v1.current.final.signature" in await anext(stream)
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


@pytest.mark.asyncio
async def test_workflow_events_negotiates_bounded_unbuffered_sse() -> None:
    service = _WorkflowEvents()
    app = SimpleNamespace(
        state=SimpleNamespace(
            container=SimpleNamespace(workflow_events=service),
            settings=Settings(),
        )
    )
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/workflows/test/events",
            "headers": [(b"accept", b"text/event-stream")],
            "app": app,
        }
    )

    response = await workflow_events(
        workflow_id="019fac40-0000-7000-8000-000000000001",
        request=request,
        workspace_id="workspace-a",
    )

    assert isinstance(response, StreamingResponse)
    assert response.media_type == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache, no-transform"
    assert response.headers["x-accel-buffering"] == "no"
    assert service.calls == [
        {
            "workflow_id": "019fac40-0000-7000-8000-000000000001",
            "workspace_id": "workspace-a",
            "cursor": None,
        }
    ]
