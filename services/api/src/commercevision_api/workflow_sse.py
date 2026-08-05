"""Bounded Server-Sent Event framing for persisted Workflow facts."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

from commercevision_application import WorkflowEventStreamService
from commercevision_application.workflow_events import (
    WorkflowEventDelivery,
    WorkflowEventPage,
)
from commercevision_contracts import Settings
from commercevision_domain import NotFoundError
from fastapi import Request
from starlette.concurrency import run_in_threadpool

_MAX_CURSOR_BYTES = 256


@dataclass(frozen=True, slots=True)
class WorkflowSsePolicy:
    poll_interval_seconds: float
    heartbeat_seconds: float
    retry_milliseconds: int
    max_session_seconds: float
    max_pages_per_session: int = 100

    @classmethod
    def from_settings(cls, settings: Settings) -> WorkflowSsePolicy:
        return cls(
            poll_interval_seconds=settings.workflow_event_poll_interval_seconds,
            heartbeat_seconds=settings.workflow_event_heartbeat_seconds,
            retry_milliseconds=settings.workflow_event_retry_milliseconds,
            max_session_seconds=settings.workflow_event_max_session_seconds,
            max_pages_per_session=settings.workflow_event_max_pages_per_session,
        )

    def __post_init__(self) -> None:
        if not 0.05 <= self.poll_interval_seconds <= 10:
            raise ValueError("Workflow SSE poll interval is invalid")
        if not 1 <= self.heartbeat_seconds <= 60:
            raise ValueError("Workflow SSE heartbeat interval is invalid")
        if type(self.retry_milliseconds) is not int or not 100 <= self.retry_milliseconds <= 30_000:
            raise ValueError("Workflow SSE retry interval is invalid")
        if not 15 <= self.max_session_seconds <= 3600:
            raise ValueError("Workflow SSE maximum session is invalid")
        if (
            type(self.max_pages_per_session) is not int
            or not 1 <= self.max_pages_per_session <= 1000
        ):
            raise ValueError("Workflow SSE page budget is invalid")


def encode_workflow_event(
    delivery: WorkflowEventDelivery,
    *,
    retry_milliseconds: int,
) -> bytes:
    """Encode one persisted event without permitting SSE field injection."""

    _validate_retry_milliseconds(retry_milliseconds)
    cursor = delivery.cursor
    try:
        cursor_bytes = cursor.encode("ascii")
    except (AttributeError, UnicodeEncodeError):
        raise ValueError("Workflow SSE cursor is invalid") from None
    if (
        not cursor_bytes
        or len(cursor_bytes) > _MAX_CURSOR_BYTES
        or b"\r" in cursor_bytes
        or b"\n" in cursor_bytes
    ):
        raise ValueError("Workflow SSE cursor is invalid")
    data = delivery.event.model_dump_json()
    return (
        f"retry: {retry_milliseconds}\nid: {cursor}\nevent: workflow.event\ndata: {data}\n\n"
    ).encode()


def encode_heartbeat() -> bytes:
    """Return an SSE comment that cannot mutate the browser resume cursor."""

    return b": heartbeat\n\n"


def encode_retry_hint(retry_milliseconds: int) -> bytes:
    """Tell the browser how quickly to reconnect without changing its cursor."""

    _validate_retry_milliseconds(retry_milliseconds)
    return f"retry: {retry_milliseconds}\n\n".encode()


def _validate_retry_milliseconds(value: object) -> None:
    if type(value) is not int or not 100 <= value <= 30_000:
        raise ValueError("Workflow SSE retry interval is invalid")


async def stream_workflow_events(
    *,
    request: Request,
    service: WorkflowEventStreamService,
    workspace_id: str,
    workflow_id: str,
    cursor: str | None,
    first_page: WorkflowEventPage,
    policy: WorkflowSsePolicy,
    monotonic: Callable[[], float] = time.monotonic,
) -> AsyncIterator[bytes]:
    """Poll short transactions and stop on disconnect or the session budget."""

    started_at = monotonic()
    deadline = started_at + policy.max_session_seconds
    next_heartbeat = started_at + policy.heartbeat_seconds
    page = first_page
    pages_read = 1
    current_cursor = cursor
    if await request.is_disconnected():
        return
    yield encode_retry_hint(policy.retry_milliseconds)
    while monotonic() < deadline:
        had_items = bool(page.items)
        for delivery in page.items:
            if await request.is_disconnected():
                return
            yield encode_workflow_event(
                delivery,
                retry_milliseconds=policy.retry_milliseconds,
            )
            current_cursor = delivery.cursor
        if await request.is_disconnected():
            return
        if not had_items:
            now = monotonic()
            if now >= next_heartbeat:
                yield encode_heartbeat()
                next_heartbeat = now + policy.heartbeat_seconds
            sleep_for = min(
                policy.poll_interval_seconds,
                max(0.0, next_heartbeat - now),
                max(0.0, deadline - now),
            )
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
            if await request.is_disconnected() or monotonic() >= deadline:
                return
        if pages_read >= policy.max_pages_per_session:
            return
        try:
            page = await run_in_threadpool(
                service.event_page,
                workflow_id=workflow_id,
                workspace_id=workspace_id,
                cursor=current_cursor,
            )
        except (NotFoundError, ValueError):
            return
        pages_read += 1
