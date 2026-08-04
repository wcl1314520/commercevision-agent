"""Technology-neutral observability seam for incremental indexing."""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .indexing import ImageIndexingTarget
    from .operations import OperationExecutionRequest


class IndexingObserver(Protocol):
    def span(
        self,
        *,
        step: str,
        request: OperationExecutionRequest,
        target: ImageIndexingTarget | None = None,
    ) -> AbstractContextManager[None]: ...

    def provider_result(
        self,
        *,
        request: OperationExecutionRequest,
        target: ImageIndexingTarget,
        outcome: str,
        latency_ms: int,
        provider_request_id: str | None,
    ) -> None: ...

    def completed(
        self,
        *,
        request: OperationExecutionRequest,
        target: ImageIndexingTarget,
        outcome: str,
    ) -> None: ...


class NullIndexingObserver:
    def span(
        self,
        *,
        step: str,
        request: OperationExecutionRequest,
        target: ImageIndexingTarget | None = None,
    ) -> AbstractContextManager[None]:
        del step, request, target
        return nullcontext()

    def provider_result(
        self,
        *,
        request: OperationExecutionRequest,
        target: ImageIndexingTarget,
        outcome: str,
        latency_ms: int,
        provider_request_id: str | None,
    ) -> None:
        del request, target, outcome, latency_ms, provider_request_id

    def completed(
        self,
        *,
        request: OperationExecutionRequest,
        target: ImageIndexingTarget,
        outcome: str,
    ) -> None:
        del request, target, outcome
