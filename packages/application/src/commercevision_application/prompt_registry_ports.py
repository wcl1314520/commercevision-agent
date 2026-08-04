"""Narrow persistence seams for Prompt Registry commands and resolution."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from commercevision_domain import PromptProductionPointer, PromptRevision

from .ports import AuditRepositoryPort, OutboxRepositoryPort


class PromptIdempotencyRecordPort(Protocol):
    request_hash: str
    resource_type: str
    resource_id: str
    response_data: dict[str, object] | None
    status: str


class PromptIdempotencyRepositoryPort(Protocol):
    def claim(
        self,
        *,
        scope: str,
        key_hash: str,
        request_hash: str,
        expires_at: datetime,
    ) -> PromptIdempotencyRecordPort: ...

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


class PromptRevisionRepositoryPort(Protocol):
    def add(self, revision: PromptRevision) -> None: ...

    def get(
        self, *, workspace_id: str, revision_id: str, for_update: bool = False
    ) -> PromptRevision | None: ...

    def get_by_semantic_revision(
        self,
        *,
        workspace_id: str,
        prompt_id: str,
        semantic_revision: str,
        for_update: bool = False,
    ) -> PromptRevision | None: ...

    def save_lifecycle(self, revision: PromptRevision, *, expected_version: int) -> None: ...

    def get_pointer(
        self, *, workspace_id: str, prompt_id: str, for_update: bool = False
    ) -> PromptProductionPointer | None: ...

    def add_pointer(self, pointer: PromptProductionPointer) -> None: ...

    def save_pointer(self, pointer: PromptProductionPointer, *, expected_version: int) -> None: ...

    def resolve_production(
        self,
        *,
        workspace_id: str,
        prompt_id: str,
        node: str,
        category: str,
        model_family: str,
    ) -> PromptRevision | None: ...


class PromptRegistryUnitOfWorkPort(Protocol):
    prompt_revisions: PromptRevisionRepositoryPort
    idempotency: PromptIdempotencyRepositoryPort
    outbox: OutboxRepositoryPort
    audit: AuditRepositoryPort

    def __enter__(self) -> PromptRegistryUnitOfWorkPort: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None: ...

    def database_now(self) -> datetime: ...

    def commit(self) -> None: ...


PromptRegistryUnitOfWorkFactory = Callable[[], PromptRegistryUnitOfWorkPort]
