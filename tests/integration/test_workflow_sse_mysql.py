from datetime import UTC, datetime, timedelta

import pytest
from commercevision_application import WorkflowEventCursorCodec, WorkflowEventStreamService
from commercevision_contracts.events import EventType, WorkflowNodeCompletedPayload
from commercevision_domain import Workflow, WorkflowStatus, new_uuid7
from commercevision_domain.messaging import EventEnvelope, OutboxEvent
from commercevision_persistence import SqlAlchemyUnitOfWork
from sqlalchemy import event as sqlalchemy_event

pytestmark = pytest.mark.integration


def test_workflow_sse_keyset_pages_and_reconnect_storm_have_fixed_query_budgets(
    integration_database,
) -> None:
    now = datetime.now(UTC)
    workflow = Workflow.create(
        workspace_id="integration-sse-load",
        created_by="load-test",
        workflow_type="COMMERCE_IMAGE_GENERATION",
        input_data={},
        retention=timedelta(hours=72),
        now=now,
    )
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        uow.workflows.add(workflow)
        for index in range(205):
            occurred_at = now + timedelta(microseconds=index)
            uow.outbox.add(
                OutboxEvent(
                    envelope=EventEnvelope.create(
                        event_type=EventType.WORKFLOW_NODE_COMPLETED.value,
                        aggregate_type="workflow",
                        aggregate_id=workflow.id,
                        aggregate_version=index + 1,
                        trace_id=f"sse-load-{index}",
                        payload=WorkflowNodeCompletedPayload(
                            node="fixture-node",
                            completed_step_id=new_uuid7(),
                            status=WorkflowStatus.PLANNING,
                        ).model_dump(mode="json"),
                        now=occurred_at,
                    ),
                    available_at=occurred_at,
                    workspace_id=workflow.workspace_id,
                )
            )
        uow.commit()

    service = WorkflowEventStreamService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(integration_database.session_factory),
        cursor_codec=WorkflowEventCursorCodec(
            current_key_id="load",
            current_secret="l" * 32,
            max_age_seconds=3600,
            future_skew_seconds=30,
            clock=lambda: now,
        ),
        page_size=100,
    )
    statements: list[str] = []

    def _record_statement(*args: object) -> None:
        statements.append(str(args[2]))

    sqlalchemy_event.listen(
        integration_database.engine,
        "before_cursor_execute",
        _record_statement,
    )
    try:
        delivered_ids: list[str] = []
        cursor = None
        page_sizes: list[int] = []
        while True:
            page = service.event_page(
                workflow_id=workflow.id,
                workspace_id=workflow.workspace_id,
                cursor=cursor,
            )
            page_sizes.append(len(page.items))
            delivered_ids.extend(item.event.event_id for item in page.items)
            if not page.items:
                break
            cursor = page.next_cursor

        assert page_sizes == [100, 100, 5, 0]
        assert len(delivered_ids) == len(set(delivered_ids)) == 205
        assert len(statements) == 15

        statements.clear()
        assert cursor is not None
        for _ in range(25):
            caught_up = service.event_page(
                workflow_id=workflow.id,
                workspace_id=workflow.workspace_id,
                cursor=cursor,
            )
            assert caught_up.items == ()
        assert len(statements) == 100
    finally:
        sqlalchemy_event.remove(
            integration_database.engine,
            "before_cursor_execute",
            _record_statement,
        )

    concurrent_at = now + timedelta(seconds=1)
    concurrent_envelope = EventEnvelope.create(
        event_type=EventType.WORKFLOW_NODE_COMPLETED.value,
        aggregate_type="workflow",
        aggregate_id=workflow.id,
        aggregate_version=206,
        trace_id="sse-concurrent",
        payload=WorkflowNodeCompletedPayload(
            node="concurrent-node",
            completed_step_id=new_uuid7(),
            status=WorkflowStatus.PLANNING,
        ).model_dump(mode="json"),
        now=concurrent_at,
    )
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        uow.outbox.add(
            OutboxEvent(
                envelope=concurrent_envelope,
                available_at=concurrent_at,
                workspace_id=workflow.workspace_id,
            )
        )
        uow.commit()

    concurrent_page = service.event_page(
        workflow_id=workflow.id,
        workspace_id=workflow.workspace_id,
        cursor=cursor,
    )
    assert [item.event.event_id for item in concurrent_page.items] == [concurrent_envelope.event_id]
