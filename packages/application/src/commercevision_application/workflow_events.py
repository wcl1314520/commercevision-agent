"""Tenant-scoped, resumable reads of persisted Workflow events."""

from __future__ import annotations

from dataclasses import dataclass

from commercevision_contracts.events import event_contract_for
from commercevision_contracts.workflow import EventResponse
from commercevision_domain import NotFoundError, RetentionStatus, validate_workspace_id
from commercevision_domain.messaging import OutboxEvent

from .ports import UnitOfWorkFactory
from .workflow_event_cursors import WorkflowEventCursorCodec


@dataclass(frozen=True, slots=True)
class WorkflowEventDelivery:
    """One safe persisted event paired with its exact post-delivery cursor."""

    event: EventResponse
    cursor: str


@dataclass(frozen=True, slots=True)
class WorkflowEventPage:
    """One bounded page whose transaction is closed before delivery."""

    items: tuple[WorkflowEventDelivery, ...]
    next_cursor: str | None


class WorkflowEventStreamService:
    """Read safe event projections without holding a transaction during delivery."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        cursor_codec: WorkflowEventCursorCodec,
        page_size: int,
    ) -> None:
        if type(page_size) is not int or not 1 <= page_size <= 200:
            raise ValueError("Workflow event page size is invalid")
        self._uow_factory = uow_factory
        self._cursor_codec = cursor_codec
        self._page_size = page_size

    def events(self, *, workflow_id: str, workspace_id: str) -> list[EventResponse]:
        validate_workspace_id(workspace_id)
        with self._uow_factory() as uow:
            workflow = uow.workflows.get(workflow_id, workspace_id=workspace_id)
            if workflow is None:
                raise NotFoundError(f"workflow {workflow_id} was not found")
            now = uow.database_now()
            if (
                workflow.retention_status is not RetentionStatus.ACTIVE
                or now >= workflow.expires_at
            ):
                raise NotFoundError(f"workflow {workflow_id} was not found")
            events = uow.outbox.list_for_workflow_stream(
                workspace_id=workspace_id,
                workflow_id=workflow_id,
                after=None,
                limit=200,
            )
        return [_event_response(event) for event in events]

    def event_page(
        self,
        *,
        workflow_id: str,
        workspace_id: str,
        cursor: str | None,
    ) -> WorkflowEventPage:
        validate_workspace_id(workspace_id)
        with self._uow_factory() as uow:
            workflow = uow.workflows.get(workflow_id, workspace_id=workspace_id)
            if workflow is None:
                raise NotFoundError(f"workflow {workflow_id} was not found")
            now = uow.database_now()
            if (
                workflow.retention_status is not RetentionStatus.ACTIVE
                or now >= workflow.expires_at
            ):
                raise NotFoundError(f"workflow {workflow_id} was not found")
            after = (
                self._cursor_codec.decode(
                    cursor,
                    workspace_id=workspace_id,
                    workflow_id=workflow_id,
                    retain_until=workflow.expires_at,
                )
                if cursor is not None
                else None
            )
            if after is not None and not uow.outbox.workflow_stream_boundary_exists(
                workspace_id=workspace_id,
                workflow_id=workflow_id,
                boundary=after,
            ):
                raise ValueError("Workflow event cursor is invalid")
            events = uow.outbox.list_for_workflow_stream(
                workspace_id=workspace_id,
                workflow_id=workflow_id,
                after=after,
                limit=self._page_size + 1,
            )
        delivered = events[: self._page_size]
        for event in delivered:
            if (
                event.workspace_id != workspace_id
                or event.envelope.aggregate_type != "workflow"
                or event.envelope.aggregate_id != workflow_id
            ):
                raise RuntimeError("Workflow event repository returned an out-of-scope event")
        items = tuple(
            WorkflowEventDelivery(
                event=_event_response(event),
                cursor=self._cursor_codec.encode(
                    workspace_id=workspace_id,
                    workflow_id=workflow_id,
                    occurred_at=event.envelope.occurred_at,
                    event_id=event.envelope.event_id,
                    retain_until=workflow.expires_at,
                ),
            )
            for event in delivered
        )
        return WorkflowEventPage(
            items=items,
            next_cursor=items[-1].cursor if items else None,
        )


def _event_response(event: OutboxEvent) -> EventResponse:
    envelope = event.envelope
    try:
        payload = event_contract_for(
            envelope.event_type,
            envelope.schema_version,
        ).validate_payload(envelope.payload)
    except (KeyError, ValueError):
        raise RuntimeError("Persisted Workflow event failed its public contract") from None
    return EventResponse(
        event_id=envelope.event_id,
        event_type=envelope.event_type,
        schema_version=envelope.schema_version,
        aggregate_type=envelope.aggregate_type,
        aggregate_id=envelope.aggregate_id,
        aggregate_version=envelope.aggregate_version,
        occurred_at=envelope.occurred_at,
        trace_id=envelope.trace_id,
        payload=payload.model_dump(mode="json", exclude_none=True),
    )
