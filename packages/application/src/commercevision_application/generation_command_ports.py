"""Transactional ports for exact approved-plan generation commands."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from types import TracebackType
from typing import TYPE_CHECKING, Protocol

from commercevision_domain import CandidateSlot, GenerationBatch
from commercevision_domain.messaging import OutboxEvent
from commercevision_domain.operations import DurableOperation

from .asset_ports import AssetAuditPort, AssetIdempotencyPort

if TYPE_CHECKING:
    from .generation_commands import (
        ApprovedGenerationAuthority,
        ApprovedPlanGenerationCommand,
    )


class ApprovedGenerationAuthorityPort(Protocol):
    def load_current_authority(
        self, command: ApprovedPlanGenerationCommand
    ) -> ApprovedGenerationAuthority: ...


class GenerationBatchRepositoryPort(Protocol):
    def add(self, batch: GenerationBatch) -> None: ...

    def get(self, batch_id: str, *, workspace_id: str) -> GenerationBatch | None: ...

    def get_by_logical_identity(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        workflow_version: int,
        creative_plan_version_id: str,
        direction_key: str,
        tool_intent_key: str,
    ) -> GenerationBatch | None: ...


class CandidateSlotRepositoryPort(Protocol):
    def add(self, slot: CandidateSlot) -> None: ...

    def list_for_batch(
        self, *, workspace_id: str, generation_batch_id: str
    ) -> tuple[CandidateSlot, ...]: ...


class GenerationOperationRepositoryPort(Protocol):
    def add(self, operation: DurableOperation) -> None: ...

    def get(self, operation_id: str, *, workspace_id: str) -> DurableOperation | None: ...


class GenerationOutboxPort(Protocol):
    def add(self, event: OutboxEvent) -> None: ...


class ApprovedGenerationUnitOfWorkPort(Protocol):
    generation_authority: ApprovedGenerationAuthorityPort
    generation_batches: GenerationBatchRepositoryPort
    candidate_slots: CandidateSlotRepositoryPort
    operations: GenerationOperationRepositoryPort
    outbox: GenerationOutboxPort
    idempotency: AssetIdempotencyPort
    audit: AssetAuditPort

    def __enter__(self) -> ApprovedGenerationUnitOfWorkPort: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def database_now(self) -> datetime: ...

    def flush(self) -> None: ...

    def commit(self) -> None: ...


ApprovedGenerationUnitOfWorkFactory = Callable[[], ApprovedGenerationUnitOfWorkPort]
