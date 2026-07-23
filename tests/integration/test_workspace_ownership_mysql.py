from __future__ import annotations

from datetime import UTC, datetime, timedelta
from importlib import import_module

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from commercevision_domain import DurableOperation, OperationKind
from commercevision_domain.messaging import (
    DeadLetterMessage,
    DeadLetterReplay,
    EventEnvelope,
    OutboxEvent,
)
from commercevision_persistence.mappers import (
    dead_letter_replay_to_model,
    dead_letter_to_model,
    outbox_to_model,
)
from commercevision_persistence.operation_mappers import operation_to_model
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.integration


def _event(
    *,
    workspace_id: str | None,
    suffix: str,
    now: datetime,
    source_dead_letter_id: str | None = None,
) -> OutboxEvent:
    return OutboxEvent(
        envelope=EventEnvelope.create(
            event_type="operation.recovery.requested",
            aggregate_type="durable_operation",
            aggregate_id=f"operation-{suffix}",
            aggregate_version=1,
            trace_id=f"trace-{suffix}",
            payload={"operation_id": f"operation-{suffix}"},
            now=now,
        ),
        available_at=now,
        workspace_id=workspace_id,
        source_dead_letter_id=source_dead_letter_id,
    )


def _dead_letter(
    *,
    workspace_id: str | None,
    suffix: str,
    now: datetime,
    source_dead_letter_id: str | None = None,
) -> DeadLetterMessage:
    return DeadLetterMessage.create(
        consumer=f"ownership-worker-{suffix}",
        message_id=f"ownership-message-{suffix}",
        event_type="operation.recovery.requested",
        payload={"operation_id": f"operation-{suffix}"},
        reason="ownership constraint probe",
        attempt_count=1,
        original_created_at=now,
        workspace_id=workspace_id,
        source_dead_letter_id=source_dead_letter_id,
        now=now,
    )


def _operation(*, workspace_id: str, suffix: str, now: datetime) -> DurableOperation:
    return DurableOperation.create(
        workspace_id=workspace_id,
        kind=OperationKind.COLLECTION_REBUILD,
        target_type="collection",
        target_id=f"ownership-{suffix}",
        target_version=1,
        input_hash=(suffix.encode().hex() + ("0" * 64))[:64],
        max_attempts=2,
        execution_max_elapsed=timedelta(hours=1),
        now=now,
    )


def _update(
    integration_database,
    *,
    table_name: str,
    column_name: str,
    row_id: str,
    target_id: str,
) -> None:
    with integration_database.engine.begin() as connection:
        connection.execute(
            text(f"UPDATE {table_name} SET {column_name} = :target_id WHERE id = :row_id"),
            {"target_id": target_id, "row_id": row_id},
        )


def _insert_rejected(integration_database, model: object) -> None:
    with integration_database.session_factory() as session:
        session.add(model)
        with pytest.raises(DBAPIError):
            session.commit()


def test_ticket02_workspace_ownership_edges_are_database_enforced(
    integration_database,
) -> None:
    now = datetime(2026, 7, 24, 6, 0, 0, 123456, tzinfo=UTC)
    workspace_a = "workspace-ownership-a"
    workspace_b = "workspace-ownership-b"

    dead_a1 = _dead_letter(workspace_id=workspace_a, suffix="dead-a1", now=now)
    dead_a2 = _dead_letter(workspace_id=workspace_a, suffix="dead-a2", now=now)
    dead_b = _dead_letter(workspace_id=workspace_b, suffix="dead-b", now=now)
    legacy_dead = _dead_letter(workspace_id=None, suffix="dead-legacy", now=now)
    event_a1 = _event(workspace_id=workspace_a, suffix="event-a1", now=now)
    event_a2 = _event(workspace_id=workspace_a, suffix="event-a2", now=now)
    event_b = _event(workspace_id=workspace_b, suffix="event-b", now=now)
    legacy_event = _event(workspace_id=None, suffix="event-legacy", now=now)
    operation_a1 = _operation(workspace_id=workspace_a, suffix="operation-a1", now=now)
    operation_a2 = _operation(workspace_id=workspace_a, suffix="operation-a2", now=now)
    operation_b = _operation(workspace_id=workspace_b, suffix="operation-b", now=now)

    child_dead = _dead_letter(
        workspace_id=workspace_a,
        suffix="dead-child",
        now=now,
        source_dead_letter_id=dead_a1.id,
    )
    source_event = _event(
        workspace_id=workspace_a,
        suffix="event-source",
        now=now,
        source_dead_letter_id=dead_a1.id,
    )
    operation_a1.dead_letter_id = dead_a1.id
    operation_a2.replay_source_dead_letter_id = dead_a1.id

    with integration_database.session_factory() as session:
        session.add_all(
            [
                outbox_to_model(event_a1),
                outbox_to_model(event_a2),
                outbox_to_model(event_b),
                outbox_to_model(legacy_event),
                dead_letter_to_model(dead_a1),
                dead_letter_to_model(dead_a2),
                dead_letter_to_model(dead_b),
                dead_letter_to_model(legacy_dead),
            ]
        )
        session.flush()
        session.add_all(
            [
                dead_letter_to_model(child_dead),
                outbox_to_model(source_event),
                operation_to_model(operation_a1),
                operation_to_model(operation_a2),
                operation_to_model(operation_b),
            ]
        )
        session.flush()
        replay = DeadLetterReplay.create(
            source_dead_letter_id=dead_a1.id,
            workspace_id=workspace_a,
            actor_id="ownership-admin",
            reason="prove composite replay ownership",
            replay_attempt=1,
            replay_event_id=event_a1.envelope.event_id,
            now=now,
        )
        replay_model = dead_letter_replay_to_model(replay)
        replay_model.operation_id = operation_a1.id
        session.add(replay_model)
        session.commit()

    cross_dead_insert = _dead_letter(
        workspace_id=workspace_a,
        suffix="dead-cross-insert",
        now=now,
        source_dead_letter_id=dead_b.id,
    )
    _insert_rejected(
        integration_database,
        dead_letter_to_model(cross_dead_insert),
    )
    cross_event_insert = _event(
        workspace_id=workspace_a,
        suffix="event-cross-insert",
        now=now,
        source_dead_letter_id=dead_b.id,
    )
    _insert_rejected(
        integration_database,
        outbox_to_model(cross_event_insert),
    )
    cross_dead_operation = _operation(
        workspace_id=workspace_a,
        suffix="operation-cross-dead-insert",
        now=now,
    )
    cross_dead_operation.dead_letter_id = dead_b.id
    _insert_rejected(
        integration_database,
        operation_to_model(cross_dead_operation),
    )
    cross_source_operation = _operation(
        workspace_id=workspace_a,
        suffix="operation-cross-source-insert",
        now=now,
    )
    cross_source_operation.replay_source_dead_letter_id = dead_b.id
    _insert_rejected(
        integration_database,
        operation_to_model(cross_source_operation),
    )

    def replay_insert(
        *,
        suffix: str,
        source_dead_letter_id: str,
        replay_event_id: str,
        operation_id: str,
    ) -> object:
        inserted = DeadLetterReplay.create(
            source_dead_letter_id=source_dead_letter_id,
            workspace_id=workspace_a,
            actor_id="ownership-admin",
            reason=f"reject cross-workspace replay {suffix}",
            replay_attempt=1,
            replay_event_id=replay_event_id,
            now=now,
        )
        inserted_model = dead_letter_replay_to_model(inserted)
        inserted_model.operation_id = operation_id
        return inserted_model

    _insert_rejected(
        integration_database,
        replay_insert(
            suffix="source",
            source_dead_letter_id=dead_b.id,
            replay_event_id=event_a2.envelope.event_id,
            operation_id=operation_a2.id,
        ),
    )
    _insert_rejected(
        integration_database,
        replay_insert(
            suffix="event",
            source_dead_letter_id=dead_a2.id,
            replay_event_id=event_b.envelope.event_id,
            operation_id=operation_a2.id,
        ),
    )
    _insert_rejected(
        integration_database,
        replay_insert(
            suffix="operation",
            source_dead_letter_id=dead_a2.id,
            replay_event_id=event_a2.envelope.event_id,
            operation_id=operation_b.id,
        ),
    )

    same_workspace_updates = (
        ("dead_letter_messages", "source_dead_letter_id", child_dead.id, dead_a2.id),
        ("outbox_events", "source_dead_letter_id", source_event.envelope.event_id, dead_a2.id),
        ("durable_operations", "dead_letter_id", operation_a1.id, dead_a2.id),
        (
            "durable_operations",
            "replay_source_dead_letter_id",
            operation_a2.id,
            dead_a2.id,
        ),
        ("dead_letter_replays", "source_dead_letter_id", replay.id, dead_a2.id),
        ("dead_letter_replays", "replay_event_id", replay.id, event_a2.envelope.event_id),
        ("dead_letter_replays", "operation_id", replay.id, operation_a2.id),
    )
    for table_name, column_name, row_id, target_id in same_workspace_updates:
        _update(
            integration_database,
            table_name=table_name,
            column_name=column_name,
            row_id=row_id,
            target_id=target_id,
        )

    cross_workspace_updates = (
        ("dead_letter_messages", "source_dead_letter_id", child_dead.id, dead_b.id),
        ("outbox_events", "source_dead_letter_id", source_event.envelope.event_id, dead_b.id),
        ("durable_operations", "dead_letter_id", operation_a1.id, dead_b.id),
        (
            "durable_operations",
            "replay_source_dead_letter_id",
            operation_a2.id,
            dead_b.id,
        ),
        ("dead_letter_replays", "source_dead_letter_id", replay.id, dead_b.id),
        ("dead_letter_replays", "replay_event_id", replay.id, event_b.envelope.event_id),
        ("dead_letter_replays", "operation_id", replay.id, operation_b.id),
    )
    for table_name, column_name, row_id, target_id in cross_workspace_updates:
        with pytest.raises(DBAPIError):
            _update(
                integration_database,
                table_name=table_name,
                column_name=column_name,
                row_id=row_id,
                target_id=target_id,
            )

    with pytest.raises(DBAPIError):
        _update(
            integration_database,
            table_name="dead_letter_messages",
            column_name="source_dead_letter_id",
            row_id=legacy_dead.id,
            target_id=dead_a1.id,
        )
    with pytest.raises(DBAPIError):
        _update(
            integration_database,
            table_name="outbox_events",
            column_name="source_dead_letter_id",
            row_id=legacy_event.envelope.event_id,
            target_id=dead_a1.id,
        )

    with integration_database.engine.begin() as connection:
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        connection.execute(
            text(
                """
                UPDATE outbox_events
                SET source_dead_letter_id = :cross_workspace_source
                WHERE id = :event_id
                """
            ),
            {
                "cross_workspace_source": dead_b.id,
                "event_id": source_event.envelope.event_id,
            },
        )
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 1"))

    migration = import_module(
        "database.migrations.versions.b1c8e4f2a703_durable_operations_recovery"
    )
    with (
        integration_database.engine.connect() as connection,
        Operations.context(MigrationContext.configure(connection)),
        pytest.raises(RuntimeError, match="provenance violates workspace ownership"),
    ):
        migration._assert_workspace_ownership_safe()

    _update(
        integration_database,
        table_name="outbox_events",
        column_name="source_dead_letter_id",
        row_id=source_event.envelope.event_id,
        target_id=dead_a2.id,
    )
