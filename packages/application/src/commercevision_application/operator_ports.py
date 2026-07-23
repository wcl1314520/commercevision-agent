"""Typed seams for operation inspection and dead-letter administration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from commercevision_domain.messaging import DeadLetterMessage, DeadLetterReplay, OutboxEvent
from commercevision_domain.workspace_identity import validate_workspace_id


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    actor_id: str
    workspace_ids: frozenset[str]
    admin_workspace_ids: frozenset[str]
    system_admin: bool = False

    def __post_init__(self) -> None:
        for workspace_id in self.workspace_ids | self.admin_workspace_ids:
            validate_workspace_id(workspace_id)


class OperatorAccessPolicyPort(Protocol):
    def require_workspace(
        self,
        *,
        workspace_id: str,
        principal: AuthenticatedPrincipal,
    ) -> None: ...
    def require_admin(
        self,
        *,
        workspace_id: str,
        principal: AuthenticatedPrincipal,
    ) -> None: ...
    def require_system_admin(self, *, principal: AuthenticatedPrincipal) -> None: ...


class OperatorDeadLetterRepositoryPort(Protocol):
    def get_by_id(
        self,
        *,
        workspace_id: str,
        dead_letter_id: str,
        for_update: bool = False,
    ) -> DeadLetterMessage | None: ...
    def list(
        self,
        *,
        workspace_id: str,
        limit: int,
        cursor: tuple[datetime, str] | None,
    ) -> list[DeadLetterMessage]: ...
    def add_replay(self, replay: DeadLetterReplay) -> None: ...
    def get_replay(self, replay_id: str) -> DeadLetterReplay | None: ...
    def list_replays(
        self,
        *,
        source_dead_letter_id: str,
        limit: int,
        cursor: tuple[datetime, str] | None,
    ) -> list[DeadLetterReplay]: ...
    def list_children(
        self,
        *,
        source_dead_letter_id: str,
        workspace_id: str | None,
        limit: int,
        cursor: tuple[datetime, str] | None,
    ) -> list[DeadLetterMessage]: ...
    def next_replay_attempt(self, source_dead_letter_id: str) -> int: ...
    def get_legacy(
        self,
        *,
        dead_letter_id: str,
    ) -> DeadLetterMessage | None: ...
    def list_legacy(
        self,
        *,
        limit: int,
        cursor: tuple[datetime, str] | None,
    ) -> list[DeadLetterMessage]: ...


class OperatorOutboxPort(Protocol):
    def add(self, event: OutboxEvent) -> None: ...
    def get(self, event_id: str, *, for_update: bool = False) -> OutboxEvent | None: ...


class OperatorIdempotencyRecord(Protocol):
    request_hash: str
    resource_id: str
    response_data: dict[str, object] | None
    status: str


class OperatorIdempotencyPort(Protocol):
    def claim(
        self,
        *,
        scope: str,
        key_hash: str,
        request_hash: str,
        expires_at: datetime,
    ) -> OperatorIdempotencyRecord: ...
    def complete(
        self,
        *,
        scope: str,
        key_hash: str,
        request_hash: str,
        resource_type: str,
        resource_id: str,
        response_data: dict[str, object],
    ) -> None: ...


class OperatorAuditPort(Protocol):
    def add(
        self,
        *,
        workspace_id: str,
        actor_type: str,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        trace_id: str,
        metadata: dict[str, object],
        created_at: datetime,
        expires_at: datetime,
    ) -> None: ...


class OperatorUnitOfWorkPort(Protocol):
    dead_letters: OperatorDeadLetterRepositoryPort
    outbox: OperatorOutboxPort
    idempotency: OperatorIdempotencyPort
    audit: OperatorAuditPort

    def __enter__(self) -> OperatorUnitOfWorkPort: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None: ...
    def flush(self) -> None: ...
    def commit(self) -> None: ...


OperatorUnitOfWorkFactory = Callable[[], OperatorUnitOfWorkPort]
