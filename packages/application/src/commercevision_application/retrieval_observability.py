"""Technology-neutral observability seam for rights-first retrieval."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Protocol


class RetrievalObserver(Protocol):
    @contextmanager
    def span(
        self,
        *,
        step: str,
        workspace_id: str,
        policy_id: str,
        component: str | None = None,
    ) -> Iterator[None]: ...

    def degraded(self, *, component: str, code: str) -> None: ...

    def provider_result(
        self,
        *,
        workspace_id: str,
        policy_id: str,
        provider: str,
        outcome: str,
        latency_ms: int,
        provider_request_id: str | None,
    ) -> None: ...

    def completed(
        self,
        *,
        outcome: str,
        latency_ms: int,
        eligible_candidates: int,
        fused_candidates: int,
        authorized_candidates: int,
        unauthorized_results: int,
    ) -> None: ...


class NullRetrievalObserver:
    @contextmanager
    def span(
        self,
        *,
        step: str,
        workspace_id: str,
        policy_id: str,
        component: str | None = None,
    ) -> Iterator[None]:
        del step, workspace_id, policy_id, component
        yield

    def degraded(self, *, component: str, code: str) -> None:
        del component, code

    def provider_result(
        self,
        *,
        workspace_id: str,
        policy_id: str,
        provider: str,
        outcome: str,
        latency_ms: int,
        provider_request_id: str | None,
    ) -> None:
        del (
            workspace_id,
            policy_id,
            provider,
            outcome,
            latency_ms,
            provider_request_id,
        )

    def completed(
        self,
        *,
        outcome: str,
        latency_ms: int,
        eligible_candidates: int,
        fused_candidates: int,
        authorized_candidates: int,
        unauthorized_results: int,
    ) -> None:
        del (
            outcome,
            latency_ms,
            eligible_candidates,
            fused_candidates,
            authorized_candidates,
            unauthorized_results,
        )
