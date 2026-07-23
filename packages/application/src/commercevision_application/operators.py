"""Operator use cases for scoped dead-letter inspection and replay."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from commercevision_contracts.events import (
    DeadLetterReplayRecordedPayload,
    EventType,
)
from commercevision_domain import ConcurrencyError, NotFoundError
from commercevision_domain.messaging import (
    DeadLetterMessage,
    DeadLetterReplay,
    EventEnvelope,
    OutboxEvent,
)
from commercevision_domain.workflow.errors import IdempotencyConflictError

from .dead_letter_identity import canonicalize_dead_letter_id
from .operator_ports import (
    AuthenticatedPrincipal,
    OperatorAccessPolicyPort,
    OperatorUnitOfWorkFactory,
)


@dataclass(frozen=True, slots=True)
class DeadLetterDetail:
    dead_letter: DeadLetterMessage
    replays: tuple[DeadLetterReplay, ...]
    replays_next_cursor: tuple[datetime, str] | None
    child_dead_letters: tuple[DeadLetterMessage, ...]
    child_dead_letters_next_cursor: tuple[datetime, str] | None


class DeadLetterOperatorService:
    def __init__(
        self,
        *,
        uow_factory: OperatorUnitOfWorkFactory,
        access_policy: OperatorAccessPolicyPort,
    ) -> None:
        self._uow_factory = uow_factory
        self._access_policy = access_policy

    def list(
        self,
        *,
        workspace_id: str,
        principal: AuthenticatedPrincipal,
        limit: int,
        cursor: tuple[datetime, str] | None,
    ) -> list[DeadLetterMessage]:
        self._access_policy.require_admin(
            workspace_id=workspace_id,
            principal=principal,
        )
        with self._uow_factory() as uow:
            return uow.dead_letters.list(
                workspace_id=workspace_id,
                limit=limit,
                cursor=cursor,
            )

    def get(
        self,
        *,
        workspace_id: str,
        dead_letter_id: str,
        principal: AuthenticatedPrincipal,
        replay_limit: int = 100,
        replay_cursor: tuple[datetime, str] | None = None,
        child_limit: int = 100,
        child_cursor: tuple[datetime, str] | None = None,
    ) -> DeadLetterDetail:
        self._access_policy.require_admin(
            workspace_id=workspace_id,
            principal=principal,
        )
        dead_letter_id = canonicalize_dead_letter_id(dead_letter_id)
        with self._uow_factory() as uow:
            dead_letter = uow.dead_letters.get_by_id(
                workspace_id=workspace_id,
                dead_letter_id=dead_letter_id,
            )
            if dead_letter is None:
                raise NotFoundError("dead letter was not found")
            replays = uow.dead_letters.list_replays(
                source_dead_letter_id=dead_letter.id,
                limit=replay_limit + 1,
                cursor=replay_cursor,
            )
            children = uow.dead_letters.list_children(
                source_dead_letter_id=dead_letter.id,
                workspace_id=workspace_id,
                limit=child_limit + 1,
                cursor=child_cursor,
            )
        replay_page = tuple(replays[:replay_limit])
        child_page = tuple(children[:child_limit])
        return DeadLetterDetail(
            dead_letter=dead_letter,
            replays=replay_page,
            replays_next_cursor=(
                (replay_page[-1].replayed_at, replay_page[-1].id)
                if len(replays) > replay_limit
                else None
            ),
            child_dead_letters=child_page,
            child_dead_letters_next_cursor=(
                (child_page[-1].created_at, child_page[-1].id)
                if len(children) > child_limit
                else None
            ),
        )

    def list_legacy(
        self,
        *,
        principal: AuthenticatedPrincipal,
        limit: int,
        cursor: tuple[datetime, str] | None,
    ) -> list[DeadLetterMessage]:
        self._access_policy.require_system_admin(principal=principal)
        with self._uow_factory() as uow:
            return uow.dead_letters.list_legacy(limit=limit, cursor=cursor)

    def get_legacy(
        self,
        *,
        dead_letter_id: str,
        principal: AuthenticatedPrincipal,
        replay_limit: int = 100,
        replay_cursor: tuple[datetime, str] | None = None,
        child_limit: int = 100,
        child_cursor: tuple[datetime, str] | None = None,
    ) -> DeadLetterDetail:
        self._access_policy.require_system_admin(principal=principal)
        dead_letter_id = canonicalize_dead_letter_id(dead_letter_id)
        with self._uow_factory() as uow:
            dead_letter = uow.dead_letters.get_legacy(dead_letter_id=dead_letter_id)
            if dead_letter is None:
                raise NotFoundError("dead letter was not found")
            replays = uow.dead_letters.list_replays(
                source_dead_letter_id=dead_letter.id,
                limit=replay_limit + 1,
                cursor=replay_cursor,
            )
            children = uow.dead_letters.list_children(
                source_dead_letter_id=dead_letter.id,
                workspace_id=None,
                limit=child_limit + 1,
                cursor=child_cursor,
            )
        replay_page = tuple(replays[:replay_limit])
        child_page = tuple(children[:child_limit])
        return DeadLetterDetail(
            dead_letter=dead_letter,
            replays=replay_page,
            replays_next_cursor=(
                (replay_page[-1].replayed_at, replay_page[-1].id)
                if len(replays) > replay_limit
                else None
            ),
            child_dead_letters=child_page,
            child_dead_letters_next_cursor=(
                (child_page[-1].created_at, child_page[-1].id)
                if len(children) > child_limit
                else None
            ),
        )

    def replay(
        self,
        *,
        workspace_id: str,
        dead_letter_id: str,
        principal: AuthenticatedPrincipal,
        reason: str,
        idempotency_key: str,
        trace_id: str,
    ) -> DeadLetterReplay:
        self._access_policy.require_admin(
            workspace_id=workspace_id,
            principal=principal,
        )
        dead_letter_id = canonicalize_dead_letter_id(dead_letter_id)
        now = datetime.now(UTC)
        key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
        request_hash = hashlib.sha256(
            json.dumps(
                {"actor_id": principal.actor_id, "reason": reason},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        with self._uow_factory() as uow:
            dead_letter = uow.dead_letters.get_by_id(
                workspace_id=workspace_id,
                dead_letter_id=dead_letter_id,
                for_update=True,
            )
            if dead_letter is None:
                raise NotFoundError("dead letter was not found")
            canonical_dead_letter_id = dead_letter.id
            scope = _dead_letter_replay_idempotency_scope(
                workspace_id=workspace_id,
                dead_letter_id=canonical_dead_letter_id,
            )
            idempotency = uow.idempotency.claim(
                scope=scope,
                key_hash=key_hash,
                request_hash=request_hash,
                expires_at=now + timedelta(days=30),
            )
            if idempotency.request_hash != request_hash:
                raise IdempotencyConflictError(
                    "idempotency key was reused for a different replay request"
                )
            if idempotency.status == "COMPLETED":
                replay = uow.dead_letters.get_replay(idempotency.resource_id)
                if (
                    replay is None
                    or replay.workspace_id != workspace_id
                    or replay.source_dead_letter_id != canonical_dead_letter_id
                ):
                    raise ConcurrencyError("completed replay record was not found")
                return replay

            source_event = uow.outbox.get(dead_letter.message_id)
            if source_event is None or source_event.workspace_id != workspace_id:
                raise NotFoundError("source dead-letter event was not found")
            replay_attempt = uow.dead_letters.next_replay_attempt(dead_letter.id)
            replay_event = OutboxEvent(
                envelope=EventEnvelope.create(
                    event_type=source_event.envelope.event_type,
                    aggregate_type=source_event.envelope.aggregate_type,
                    aggregate_id=source_event.envelope.aggregate_id,
                    aggregate_version=source_event.envelope.aggregate_version,
                    trace_id=trace_id,
                    payload=source_event.envelope.payload,
                    schema_version=source_event.envelope.schema_version,
                    now=now,
                ),
                available_at=now,
                workspace_id=workspace_id,
                source_dead_letter_id=dead_letter.id,
                replay_attempt=replay_attempt,
            )
            replay = DeadLetterReplay.create(
                source_dead_letter_id=dead_letter.id,
                workspace_id=workspace_id,
                actor_id=principal.actor_id,
                reason=reason,
                replay_attempt=replay_attempt,
                replay_event_id=replay_event.envelope.event_id,
                now=now,
            )
            uow.outbox.add(replay_event)
            uow.outbox.add(
                self._replay_recorded_event(
                    replay=replay,
                    trace_id=trace_id,
                    now=now,
                )
            )
            uow.flush()
            uow.dead_letters.add_replay(replay)
            uow.idempotency.complete(
                scope=scope,
                key_hash=key_hash,
                request_hash=request_hash,
                resource_type="dead_letter_replay",
                resource_id=replay.id,
                response_data={
                    "replay_id": replay.id,
                    "replay_event_id": replay.replay_event_id,
                    "replay_attempt": replay.replay_attempt,
                },
            )
            uow.audit.add(
                workspace_id=workspace_id,
                actor_type="USER",
                actor_id=principal.actor_id,
                action="dead_letter.replayed",
                resource_type="dead_letter",
                resource_id=dead_letter.id,
                trace_id=trace_id,
                metadata={
                    "replay_id": replay.id,
                    "replay_attempt": replay.replay_attempt,
                    "reason": reason,
                },
                created_at=now,
                expires_at=now + timedelta(days=180),
            )
            uow.commit()
        return replay

    @staticmethod
    def _replay_recorded_event(
        *,
        replay: DeadLetterReplay,
        trace_id: str,
        now: datetime,
    ) -> OutboxEvent:
        payload = DeadLetterReplayRecordedPayload(
            source_dead_letter_id=replay.source_dead_letter_id,
            replay_id=replay.id,
            workspace_id=replay.workspace_id,
            replay_attempt=replay.replay_attempt,
        )
        return OutboxEvent(
            envelope=EventEnvelope.create(
                event_type=EventType.DEAD_LETTER_REPLAY_RECORDED.value,
                aggregate_type="dead_letter",
                aggregate_id=replay.source_dead_letter_id,
                aggregate_version=replay.replay_attempt,
                trace_id=trace_id,
                payload=payload.model_dump(mode="json"),
                now=now,
            ),
            available_at=now,
            workspace_id=replay.workspace_id,
            source_dead_letter_id=replay.source_dead_letter_id,
            replay_attempt=replay.replay_attempt,
        )


def _dead_letter_replay_idempotency_scope(
    *,
    workspace_id: str,
    dead_letter_id: str,
) -> str:
    workspace_digest = hashlib.sha256(workspace_id.encode("utf-8")).hexdigest()
    return f"dead-letter-replay:v1:{workspace_digest}:{dead_letter_id}"
