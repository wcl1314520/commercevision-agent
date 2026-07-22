"""Outbox, Inbox, retry, DLQ, and recovery coordination."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from commercevision_domain import NotFoundError
from commercevision_domain.messaging import DeadLetterMessage, EventEnvelope, OutboxEvent

from .ports import MessagePublisher, UnitOfWorkFactory


@dataclass(frozen=True, slots=True)
class MessageClaim:
    should_process: bool
    already_processed: bool
    dead: bool
    lease_token: str | None
    delivery_attempt: int


class OutboxDispatcher:
    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        publisher: MessagePublisher,
        owner: str,
        lease_duration: timedelta,
        batch_size: int,
    ) -> None:
        self._uow_factory = uow_factory
        self._publisher = publisher
        self._owner = owner
        self._lease_duration = lease_duration
        self._batch_size = batch_size

    def dispatch_once(self) -> tuple[int, int]:
        now = datetime.now(UTC)
        with self._uow_factory() as uow:
            events = uow.outbox.claim_ready(
                now=now,
                owner=self._owner,
                lease_duration=self._lease_duration,
                limit=self._batch_size,
            )
            uow.commit()

        published = 0
        failed = 0
        for event in events:
            lock_token = event.lock_token
            if lock_token is None:
                continue
            try:
                self._publisher.publish(event.envelope.event_id)
            except Exception as exc:
                failed += 1
                delay_seconds = min(300, 2 ** min(event.publish_attempts, 8))
                with self._uow_factory() as uow:
                    uow.outbox.mark_publish_failed(
                        event.envelope.event_id,
                        lock_token,
                        available_at=datetime.now(UTC) + timedelta(seconds=delay_seconds),
                        error_message=f"{type(exc).__name__}: {exc}",
                    )
                    uow.commit()
            else:
                published += 1
                with self._uow_factory() as uow:
                    uow.outbox.mark_published(
                        event.envelope.event_id,
                        lock_token,
                        now=datetime.now(UTC),
                    )
                    uow.commit()
        return published, failed


class InboxCoordinator:
    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        consumer: str,
        owner: str,
        lease_duration: timedelta,
        max_attempts: int,
    ) -> None:
        self._uow_factory = uow_factory
        self._consumer = consumer
        self._owner = owner
        self._lease_duration = lease_duration
        self._max_attempts = max_attempts

    def claim(self, event_id: str) -> tuple[MessageClaim, OutboxEvent]:
        now = datetime.now(UTC)
        with self._uow_factory() as uow:
            event = uow.outbox.get(event_id)
            if event is None:
                raise NotFoundError(f"outbox event {event_id} was not found")
            raw_claim = uow.inbox.claim(
                consumer=self._consumer,
                message_id=event_id,
                owner=self._owner,
                now=now,
                lease_duration=self._lease_duration,
                max_attempts=self._max_attempts,
            )
            if raw_claim.dead:
                uow.dead_letters.add(
                    DeadLetterMessage.create(
                        consumer=self._consumer,
                        message_id=event_id,
                        event_type=event.envelope.event_type,
                        payload=event.envelope.payload,
                        reason="message retry budget exhausted",
                        attempt_count=raw_claim.delivery_attempt,
                        original_created_at=event.envelope.occurred_at,
                        now=now,
                    )
                )
            uow.commit()
        return (
            MessageClaim(
                should_process=raw_claim.should_process,
                already_processed=raw_claim.already_processed,
                dead=raw_claim.dead,
                lease_token=raw_claim.lease_token,
                delivery_attempt=raw_claim.delivery_attempt,
            ),
            event,
        )

    def mark_processed(self, event_id: str, lease_token: str) -> None:
        with self._uow_factory() as uow:
            uow.inbox.mark_processed(
                consumer=self._consumer,
                message_id=event_id,
                lease_token=lease_token,
                now=datetime.now(UTC),
            )
            uow.commit()

    def mark_failed(self, event_id: str, lease_token: str, error: Exception) -> None:
        with self._uow_factory() as uow:
            uow.inbox.mark_failed(
                consumer=self._consumer,
                message_id=event_id,
                lease_token=lease_token,
                now=datetime.now(UTC),
                error_class=type(error).__name__,
                error_message=str(error),
            )
            uow.commit()


class RecoveryService:
    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        batch_size: int,
        stale_after: timedelta,
    ) -> None:
        self._uow_factory = uow_factory
        self._batch_size = batch_size
        self._stale_after = stale_after

    def recover_once(self) -> tuple[int, int]:
        now = datetime.now(UTC)
        recovered_steps = 0
        recovered_workflows = 0
        with self._uow_factory() as uow:
            for step in uow.steps.list_expired_leases(now=now, limit=self._batch_size):
                step.recover_expired_lease(retry_at=now, now=now)
                uow.steps.save(step)
                if not uow.outbox.has_unpublished(
                    aggregate_id=step.workflow_id,
                    event_type="workflow.run.requested",
                ):
                    uow.outbox.add(
                        self._run_event(
                            workflow_id=step.workflow_id,
                            workflow_version=step.expected_workflow_version,
                            reason="expired_step_lease",
                            now=now,
                        )
                    )
                recovered_steps += 1

            stale_before = now - self._stale_after
            for workflow in uow.workflows.list_recoverable(
                stale_before=stale_before,
                limit=self._batch_size,
            ):
                if not uow.outbox.has_unpublished(
                    aggregate_id=workflow.id,
                    event_type="workflow.run.requested",
                ):
                    uow.outbox.add(
                        self._run_event(
                            workflow_id=workflow.id,
                            workflow_version=workflow.version,
                            reason="stale_workflow",
                            now=now,
                        )
                    )
                    recovered_workflows += 1
            uow.commit()
        return recovered_steps, recovered_workflows

    @staticmethod
    def _run_event(
        *,
        workflow_id: str,
        workflow_version: int,
        reason: str,
        now: datetime,
    ) -> OutboxEvent:
        return OutboxEvent(
            envelope=EventEnvelope.create(
                event_type="workflow.run.requested",
                aggregate_type="workflow",
                aggregate_id=workflow_id,
                aggregate_version=workflow_version,
                trace_id=f"recovery:{workflow_id}",
                payload={"workflow_id": workflow_id, "action": "recover", "reason": reason},
                now=now,
            ),
            available_at=now,
        )
