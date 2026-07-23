from datetime import UTC, datetime, timedelta

import pytest
from commercevision_application import (
    AuthenticatedPrincipal,
    DeadLetterOperatorService,
    InboxCoordinator,
)
from commercevision_contracts.events import EventType
from commercevision_domain.messaging import DeadLetterMessage, EventEnvelope, OutboxEvent
from commercevision_persistence import (
    SqlAlchemyOperatorUnitOfWork,
    SqlAlchemyUnitOfWork,
)
from commercevision_persistence.models import AuditEventModel
from sqlalchemy import select

pytestmark = pytest.mark.integration


class AllowAdminPolicy:
    def require_admin(
        self,
        *,
        workspace_id: str,
        principal: AuthenticatedPrincipal,
    ) -> None:
        assert workspace_id
        assert principal.actor_id


def principal(actor_id: str) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        actor_id=actor_id,
        workspace_ids=frozenset({"workspace-dlq"}),
        admin_workspace_ids=frozenset({"workspace-dlq"}),
    )


def seed_dead_letter(integration_database) -> DeadLetterMessage:
    now = datetime(2026, 7, 23, 11, 0, 0, 456789, tzinfo=UTC)
    event = OutboxEvent(
        envelope=EventEnvelope.create(
            event_type="workflow.run.requested",
            aggregate_type="workflow",
            aggregate_id="workflow-dead-letter",
            aggregate_version=1,
            trace_id="trace-dead-letter",
            payload={
                "workflow_id": "workflow-dead-letter",
                "action": "recover",
            },
            now=now,
        ),
        available_at=now,
        workspace_id="workspace-dlq",
    )
    dead_letter = DeadLetterMessage.create(
        consumer="worker-a",
        message_id=event.envelope.event_id,
        event_type=event.envelope.event_type,
        payload=event.envelope.payload,
        reason="message retry budget exhausted",
        attempt_count=3,
        original_created_at=now,
        workspace_id="workspace-dlq",
        now=now,
    )
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        uow.outbox.add(event)
        uow.dead_letters.add(dead_letter)
        uow.commit()
    return dead_letter


def test_dead_letter_replay_is_idempotent_and_append_only(integration_database) -> None:
    dead_letter = seed_dead_letter(integration_database)
    service = DeadLetterOperatorService(
        uow_factory=lambda: SqlAlchemyOperatorUnitOfWork(integration_database.session_factory),
        access_policy=AllowAdminPolicy(),
    )

    first = service.replay(
        workspace_id="workspace-dlq",
        dead_letter_id=dead_letter.id,
        principal=principal("admin-a"),
        reason="provider incident resolved",
        idempotency_key="replay-dead-letter-0001",
        trace_id="trace-replay-1",
    )
    duplicate = service.replay(
        workspace_id="workspace-dlq",
        dead_letter_id=dead_letter.id,
        principal=principal("admin-a"),
        reason="provider incident resolved",
        idempotency_key="replay-dead-letter-0001",
        trace_id="trace-replay-1",
    )
    second = service.replay(
        workspace_id="workspace-dlq",
        dead_letter_id=dead_letter.id,
        principal=principal("admin-b"),
        reason="retry after second operator review",
        idempotency_key="replay-dead-letter-0002",
        trace_id="trace-replay-2",
    )

    assert duplicate.id == first.id
    assert first.replay_attempt == 1
    assert second.replay_attempt == 2

    restored = service.get(
        workspace_id="workspace-dlq",
        dead_letter_id=dead_letter.id,
        principal=principal("admin-a"),
    )
    assert restored.dead_letter.id == dead_letter.id
    assert restored.dead_letter.reason == "message retry budget exhausted"
    assert restored.dead_letter.created_at == dead_letter.created_at
    assert [(item.actor_id, item.reason) for item in restored.replays] == [
        ("admin-a", "provider incident resolved"),
        ("admin-b", "retry after second operator review"),
    ]
    assert all(item.source_dead_letter_id == dead_letter.id for item in restored.replays)
    with integration_database.session_factory() as session:
        audit_events = list(
            session.scalars(
                select(AuditEventModel)
                .where(
                    AuditEventModel.action == "dead_letter.replayed",
                    AuditEventModel.resource_id == dead_letter.id,
                )
                .order_by(AuditEventModel.created_at)
            )
        )
    assert [(event.actor_id, event.metadata_json["reason"]) for event in audit_events] == [
        ("admin-a", "provider incident resolved"),
        ("admin-b", "retry after second operator review"),
    ]


def test_replayed_event_preserves_source_failure_identity(integration_database) -> None:
    dead_letter = seed_dead_letter(integration_database)
    service = DeadLetterOperatorService(
        uow_factory=lambda: SqlAlchemyOperatorUnitOfWork(integration_database.session_factory),
        access_policy=AllowAdminPolicy(),
    )
    replay = service.replay(
        workspace_id="workspace-dlq",
        dead_letter_id=dead_letter.id,
        principal=principal("admin-a"),
        reason="replay for failure-history test",
        idempotency_key="replay-dead-letter-0003",
        trace_id="trace-replay-3",
    )

    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        replayed_event = uow.outbox.get(replay.replay_event_id)
        replay_observations = [
            event
            for event in uow.outbox.list_for_aggregate(dead_letter.id)
            if event.envelope.event_type == EventType.DEAD_LETTER_REPLAY_RECORDED
        ]

    assert replayed_event is not None
    assert replayed_event.envelope.event_type == "workflow.run.requested"
    assert replayed_event.source_dead_letter_id == dead_letter.id
    assert replayed_event.replay_attempt == 1
    assert replayed_event.workspace_id == "workspace-dlq"
    assert len(replay_observations) == 1
    assert replay_observations[0].envelope.payload["replay_id"] == replay.id

    coordinator = InboxCoordinator(
        uow_factory=lambda: SqlAlchemyUnitOfWork(integration_database.session_factory),
        consumer="replay-worker",
        owner="worker-a",
        lease_duration=timedelta(seconds=30),
        max_attempts=1,
    )
    claim, _ = coordinator.claim(replay.replay_event_id)
    assert claim.lease_token is not None
    coordinator.mark_failed(
        replay.replay_event_id,
        claim.lease_token,
        RuntimeError("replayed work failed"),
    )
    dead_claim, _ = coordinator.claim(replay.replay_event_id)
    assert dead_claim.dead is True

    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        repeated_failure = uow.dead_letters.get(
            consumer="replay-worker",
            message_id=replay.replay_event_id,
        )
    assert repeated_failure is not None
    assert repeated_failure.source_dead_letter_id == dead_letter.id
    assert repeated_failure.replay_attempt == 1
    assert repeated_failure.workspace_id == "workspace-dlq"
    detail = service.get(
        workspace_id="workspace-dlq",
        dead_letter_id=dead_letter.id,
        principal=principal("admin-a"),
    )
    assert [item.id for item in detail.child_dead_letters] == [repeated_failure.id]
