"""Outbox, Inbox, retry, DLQ, and recovery coordination."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from commercevision_contracts.events import EventType, WorkflowRunRequestedPayload
from commercevision_domain import (
    LeaseConflictError,
    NotFoundError,
    ProductBriefState,
)
from commercevision_domain.messaging import DeadLetterMessage, EventEnvelope, OutboxEvent

from .execution import ProductBriefGenerationAuthority
from .ports import MessagePublisher, UnitOfWorkFactory
from .product_brief_authority import (
    ProductBriefWorkflowAuthorityState,
    evaluate_product_brief_workflow_authority,
    has_active_product_brief_workflow_retention,
)
from .routing import EventRoutingError


@dataclass(frozen=True, slots=True)
class MessageClaim:
    should_process: bool
    already_processed: bool
    dead: bool
    retry_not_ready: bool
    lease_token: str | None
    delivery_attempt: int


@dataclass(frozen=True, slots=True)
class ProductBriefGenerationResolution:
    state: Literal["none", "active", "stale", "binding_mismatch"]
    generation: ProductBriefGenerationAuthority | None
    product_brief_version_id: str | None = None
    product_brief_version_number: int | None = None
    trace_id: str | None = None


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
                self._publisher.publish_event(event)
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
                try:
                    with self._uow_factory() as uow:
                        uow.outbox.mark_published(
                            event.envelope.event_id,
                            lock_token,
                            now=datetime.now(UTC),
                        )
                        uow.commit()
                except LeaseConflictError:
                    if not self._delivery_state_advanced(event):
                        raise
                published += 1
        return published, failed

    def _delivery_state_advanced(self, delivered_event: OutboxEvent) -> bool:
        with self._uow_factory() as uow:
            current = uow.outbox.get(delivered_event.envelope.event_id)
        return current is not None and current.available_at > delivered_event.available_at


class InboxCoordinator:
    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        consumer: str,
        owner: str,
        lease_duration: timedelta,
        max_attempts: int,
        retry_initial: timedelta = timedelta(seconds=1),
        retry_max: timedelta = timedelta(minutes=5),
    ) -> None:
        self._uow_factory = uow_factory
        self._consumer = consumer
        self._owner = owner
        self._lease_duration = lease_duration
        self._max_attempts = max_attempts
        self._retry_initial = retry_initial
        self._retry_max = retry_max

    def claim(self, event_id: str) -> tuple[MessageClaim, OutboxEvent]:
        now = datetime.now(UTC)
        with self._uow_factory() as uow:
            event = uow.outbox.get(event_id)
            if event is None:
                raise NotFoundError(f"outbox event {event_id} was not found")
            if event.published_at is None and event.available_at > now:
                return (
                    MessageClaim(
                        should_process=False,
                        already_processed=False,
                        dead=False,
                        retry_not_ready=True,
                        lease_token=None,
                        delivery_attempt=0,
                    ),
                    event,
                )
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
                        workspace_id=event.workspace_id,
                        source_dead_letter_id=event.source_dead_letter_id,
                        replay_attempt=event.replay_attempt,
                        now=now,
                    )
                )
            uow.commit()
        return (
            MessageClaim(
                should_process=raw_claim.should_process,
                already_processed=raw_claim.already_processed,
                dead=raw_claim.dead,
                retry_not_ready=False,
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

    def schedule_retry(
        self,
        event_id: str,
        lease_token: str,
        error: Exception,
        *,
        delivery_attempt: int,
    ) -> datetime:
        now = datetime.now(UTC)
        retry_delay = self._retry_initial * (2 ** max(delivery_attempt - 1, 0))
        available_at = now + min(retry_delay, self._retry_max)
        error_message = f"{type(error).__name__}: {error}"
        with self._uow_factory() as uow:
            uow.inbox.mark_failed(
                consumer=self._consumer,
                message_id=event_id,
                lease_token=lease_token,
                now=now,
                error_class=type(error).__name__,
                error_message=str(error),
            )
            uow.outbox.schedule_retry(
                event_id,
                available_at=available_at,
                error_message=error_message,
            )
            uow.commit()
        return available_at

    def mark_permanent_failed(
        self,
        event_id: str,
        lease_token: str,
        error: EventRoutingError,
        delivery_attempt: int,
    ) -> None:
        now = datetime.now(UTC)
        with self._uow_factory() as uow:
            event = uow.outbox.get(event_id)
            if event is None:
                raise NotFoundError(f"outbox event {event_id} was not found")
            uow.inbox.mark_dead(
                consumer=self._consumer,
                message_id=event_id,
                lease_token=lease_token,
                now=now,
                error_class=type(error).__name__,
                error_message=str(error),
            )
            uow.dead_letters.add(
                DeadLetterMessage.create(
                    consumer=self._consumer,
                    message_id=event_id,
                    event_type=event.envelope.event_type,
                    payload=event.envelope.payload,
                    reason=error.reason,
                    attempt_count=delivery_attempt,
                    original_created_at=event.envelope.occurred_at,
                    error_class=type(error).__name__,
                    error_message=str(error),
                    workspace_id=event.workspace_id,
                    source_dead_letter_id=event.source_dead_letter_id,
                    replay_attempt=event.replay_attempt,
                    now=now,
                )
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
        recovered_steps = 0
        recovered_workflows = 0
        with self._uow_factory() as uow:
            now = uow.database_now()
            for step in uow.steps.list_expired_leases(now=now, limit=self._batch_size):
                workflow = uow.workflows.get(step.workflow_id)
                if workflow is None:
                    raise NotFoundError(f"workflow {step.workflow_id} was not found")
                step_generation = (
                    ProductBriefGenerationAuthority.from_step(step)
                    if workflow.workflow_type == "COMMERCE_IMAGE_GENERATION"
                    else None
                )
                generation_resolution = self._resolve_product_brief_generation(
                    uow=uow,
                    workflow=workflow,
                    now=now,
                    preferred=step_generation,
                )
                if generation_resolution.state in {"stale", "binding_mismatch"}:
                    step.cancel(now=now)
                    uow.steps.save(step)
                    recovered_steps += 1
                    continue
                step.recover_expired_lease(retry_at=now, now=now)
                uow.steps.save(step)
                if not uow.outbox.has_unpublished(
                    aggregate_id=step.workflow_id,
                    event_type=EventType.WORKFLOW_RUN_REQUESTED.value,
                ):
                    uow.outbox.add(
                        self._run_event(
                            workspace_id=workflow.workspace_id,
                            workflow_id=step.workflow_id,
                            workflow_version=step.expected_workflow_version,
                            reason="expired_step_lease",
                            now=now,
                            trace_id=self._recovery_trace_id(
                                workflow_id=workflow.id,
                                generation_resolution=generation_resolution,
                            ),
                            product_brief_version_id=(
                                generation_resolution.product_brief_version_id
                            ),
                            product_brief_version_number=(
                                generation_resolution.product_brief_version_number
                            ),
                        )
                    )
                recovered_steps += 1

            stale_before = now - self._stale_after
            for workflow in uow.workflows.list_recoverable(
                stale_before=stale_before,
                limit=self._batch_size,
            ):
                generation_resolution = self._resolve_product_brief_generation(
                    uow=uow,
                    workflow=workflow,
                    now=now,
                )
                if generation_resolution.state in {"stale", "binding_mismatch"}:
                    self._cancel_nonterminal_steps(
                        uow=uow,
                        workflow_id=workflow.id,
                        now=now,
                    )
                    workflow.record_recovery_observation(observed_at=now)
                    uow.workflows.save(workflow)
                    recovered_workflows += 1
                    continue
                if (
                    workflow.workflow_type == "COMMERCE_IMAGE_GENERATION"
                    and generation_resolution.state == "none"
                ):
                    workflow.record_recovery_observation(observed_at=now)
                    uow.workflows.save(workflow)
                    recovered_workflows += 1
                    continue
                if not uow.outbox.has_unpublished(
                    aggregate_id=workflow.id,
                    event_type=EventType.WORKFLOW_RUN_REQUESTED.value,
                ):
                    uow.outbox.add(
                        self._run_event(
                            workspace_id=workflow.workspace_id,
                            workflow_id=workflow.id,
                            workflow_version=workflow.version,
                            reason="stale_workflow",
                            now=now,
                            trace_id=self._recovery_trace_id(
                                workflow_id=workflow.id,
                                generation_resolution=generation_resolution,
                            ),
                            product_brief_version_id=(
                                generation_resolution.product_brief_version_id
                            ),
                            product_brief_version_number=(
                                generation_resolution.product_brief_version_number
                            ),
                        )
                    )
                    workflow.record_recovery_observation(observed_at=now)
                    uow.workflows.save(workflow)
                    recovered_workflows += 1
            uow.commit()
        return recovered_steps, recovered_workflows

    @staticmethod
    def _run_event(
        *,
        workspace_id: str,
        workflow_id: str,
        workflow_version: int,
        reason: str,
        now: datetime,
        trace_id: str,
        product_brief_version_id: str | None = None,
        product_brief_version_number: int | None = None,
    ) -> OutboxEvent:
        return OutboxEvent(
            envelope=EventEnvelope.create(
                event_type=EventType.WORKFLOW_RUN_REQUESTED.value,
                aggregate_type="workflow",
                aggregate_id=workflow_id,
                aggregate_version=workflow_version,
                trace_id=trace_id,
                payload=WorkflowRunRequestedPayload(
                    workflow_id=workflow_id,
                    action="recover",
                    reason=reason,
                    product_brief_version_id=product_brief_version_id,
                    product_brief_version_number=product_brief_version_number,
                ).model_dump(mode="json"),
                now=now,
            ),
            available_at=now,
            workspace_id=workspace_id,
        )

    @staticmethod
    def _recovery_trace_id(
        *,
        workflow_id: str,
        generation_resolution: ProductBriefGenerationResolution,
    ) -> str:
        if generation_resolution.state == "active":
            if not generation_resolution.trace_id:
                raise RuntimeError("active ProductBrief recovery trace lineage is unavailable")
            return generation_resolution.trace_id
        return f"recovery:{workflow_id}"

    @staticmethod
    def _resolve_product_brief_generation(
        *,
        uow,
        workflow,
        now: datetime,
        preferred: ProductBriefGenerationAuthority | None = None,
    ) -> ProductBriefGenerationResolution:
        if workflow.workflow_type != "COMMERCE_IMAGE_GENERATION":
            return ProductBriefGenerationResolution(state="none", generation=None)
        product_id = workflow.input_data.get("product_id")
        if not isinstance(product_id, str) or not product_id:
            return ProductBriefGenerationResolution(state="none", generation=None)
        if not has_active_product_brief_workflow_retention(
            workflow=workflow,
            now=now,
        ):
            return ProductBriefGenerationResolution(state="stale", generation=None)
        candidates = (
            [preferred]
            if preferred is not None
            else [
                authority
                for step in uow.steps.list_for_workflow(workflow.id)
                if (authority := ProductBriefGenerationAuthority.from_step(step)) is not None
            ]
        )
        for authority in reversed(candidates):
            if (
                authority.workspace_id != workflow.workspace_id
                or authority.workflow_id != workflow.id
                or authority.product_id != product_id
            ):
                continue
            product_brief = uow.product_briefs.get(
                workspace_id=workflow.workspace_id,
                product_brief_id=authority.product_brief_id,
            )
            if (
                product_brief is None
                or product_brief.state != ProductBriefState.CONFIRMED
                or product_brief.current_version_id != authority.product_brief_version_id
                or product_brief.confirmed_version_id != authority.product_brief_version_id
            ):
                continue
            workflow_authority = evaluate_product_brief_workflow_authority(
                workflow=workflow,
                product_brief=product_brief,
                now=now,
            )
            if workflow_authority.state == ProductBriefWorkflowAuthorityState.BINDING_MISMATCH:
                return ProductBriefGenerationResolution(
                    state="binding_mismatch",
                    generation=None,
                )
            if workflow_authority.state == ProductBriefWorkflowAuthorityState.EXPIRED:
                return ProductBriefGenerationResolution(state="stale", generation=None)
            stored_version = uow.product_briefs.get_version(
                workspace_id=workflow.workspace_id,
                product_brief_version_id=authority.product_brief_version_id,
            )
            if (
                stored_version is None
                or stored_version.version.version_number != authority.product_brief_version_number
                or stored_version.version.product_brief_id != authority.product_brief_id
            ):
                continue
            if stored_version.version.confirmation_required:
                confirmation = uow.product_brief_confirmations.get_confirmation(
                    workspace_id=workflow.workspace_id,
                    product_brief_id=authority.product_brief_id,
                    product_brief_version_id=authority.product_brief_version_id,
                )
                if (
                    confirmation is None
                    or confirmation.workflow_id != workflow.id
                    or confirmation.product_brief_version_number
                    != authority.product_brief_version_number
                    or confirmation.approval_id != authority.approval_id
                ):
                    continue
            elif authority.approval_id is not None:
                continue
            trace_id = uow.product_brief_lineage.analysis_trace_id(
                workspace_id=workflow.workspace_id,
                product_brief_id=product_brief.id,
            )
            if not trace_id:
                return ProductBriefGenerationResolution(
                    state="binding_mismatch",
                    generation=None,
                )
            return ProductBriefGenerationResolution(
                state="active",
                generation=authority,
                product_brief_version_id=authority.product_brief_version_id,
                product_brief_version_number=authority.product_brief_version_number,
                trace_id=trace_id,
            )
        if preferred is not None:
            return ProductBriefGenerationResolution(state="stale", generation=None)

        product_brief = uow.product_briefs.get_by_workflow_product(
            workspace_id=workflow.workspace_id,
            workflow_id=workflow.id,
            product_id=product_id,
        )
        if product_brief is None:
            return ProductBriefGenerationResolution(
                state="stale" if candidates else "none",
                generation=None,
            )
        if (
            product_brief.state != ProductBriefState.CONFIRMED
            or product_brief.current_version_id is None
            or product_brief.confirmed_version_id != product_brief.current_version_id
        ):
            return ProductBriefGenerationResolution(
                state="stale" if candidates else "none",
                generation=None,
            )
        workflow_authority = evaluate_product_brief_workflow_authority(
            workflow=workflow,
            product_brief=product_brief,
            now=now,
        )
        if workflow_authority.state == ProductBriefWorkflowAuthorityState.BINDING_MISMATCH:
            return ProductBriefGenerationResolution(
                state="binding_mismatch",
                generation=None,
            )
        if workflow_authority.state == ProductBriefWorkflowAuthorityState.EXPIRED:
            return ProductBriefGenerationResolution(state="stale", generation=None)
        stored_version = uow.product_briefs.get_version(
            workspace_id=workflow.workspace_id,
            product_brief_version_id=product_brief.current_version_id,
        )
        if stored_version is None or stored_version.version.product_brief_id != product_brief.id:
            return ProductBriefGenerationResolution(state="stale", generation=None)
        if stored_version.version.confirmation_required:
            confirmation = uow.product_brief_confirmations.get_confirmation(
                workspace_id=workflow.workspace_id,
                product_brief_id=product_brief.id,
                product_brief_version_id=product_brief.current_version_id,
            )
            if (
                confirmation is None
                or confirmation.workflow_id != workflow.id
                or confirmation.product_brief_version_number
                != stored_version.version.version_number
            ):
                return ProductBriefGenerationResolution(state="stale", generation=None)
        trace_id = uow.product_brief_lineage.analysis_trace_id(
            workspace_id=workflow.workspace_id,
            product_brief_id=product_brief.id,
        )
        if not trace_id:
            return ProductBriefGenerationResolution(
                state="binding_mismatch",
                generation=None,
            )
        return ProductBriefGenerationResolution(
            state="active",
            generation=None,
            product_brief_version_id=stored_version.version.id,
            product_brief_version_number=stored_version.version.version_number,
            trace_id=trace_id,
        )

    @staticmethod
    def _cancel_nonterminal_steps(
        *,
        uow,
        workflow_id: str,
        now: datetime,
    ) -> None:
        for step in uow.steps.list_for_workflow(workflow_id):
            if step.status.terminal:
                continue
            step.cancel(now=now)
            uow.steps.save(step)
