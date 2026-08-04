"""Adapter from the retrieval application observer seam to Phase 2 telemetry."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from .phase2 import (
    Phase2Span,
    Phase2Telemetry,
    TelemetryDimensions,
    TelemetryIdentity,
)

_STEP_SPANS = {
    "request": Phase2Span.RETRIEVAL,
    "initial_rights": Phase2Span.RIGHTS_DECISION,
    "source_recall": Phase2Span.RETRIEVAL,
    "temporary_reference": Phase2Span.TEMPORARY_REFERENCE,
    "embedding": Phase2Span.EMBEDDING_REQUEST,
    "milvus_search": Phase2Span.MILVUS_SEARCH,
    "lexical_search": Phase2Span.LEXICAL_SEARCH,
    "fusion": Phase2Span.FUSION,
    "rerank": Phase2Span.RERANK,
    "final_rights": Phase2Span.FINAL_RIGHTS,
}


class RetrievalTelemetry:
    """Translate a narrow application seam into safe spans and retrieval metrics."""

    def __init__(self, telemetry: Phase2Telemetry | None = None) -> None:
        self._telemetry = telemetry or Phase2Telemetry()

    @contextmanager
    def span(
        self,
        *,
        step: str,
        workspace_id: str,
        policy_id: str,
        component: str | None = None,
    ) -> Iterator[None]:
        span = _STEP_SPANS.get(step)
        if span is None:
            raise ValueError("retrieval telemetry step is unsupported")
        with self._telemetry.span(
            span,
            identity=TelemetryIdentity(workspace_id=workspace_id, policy_id=policy_id),
            dimensions=TelemetryDimensions(component=component),
        ):
            yield

    def degraded(self, *, component: str, code: str) -> None:
        del component
        self._telemetry.record_retrieval_degradation(reason=code)

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
        self._telemetry.annotate(
            TelemetryIdentity(
                workspace_id=workspace_id,
                policy_id=policy_id,
                provider_request_id=provider_request_id,
            )
        )
        self._telemetry.record_provider(
            provider=provider,
            operation="retrieval_embedding",
            outcome=outcome,
            latency_ms=latency_ms,
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
        self._telemetry.record_rights(
            decision="allowed",
            reason="initial_eligibility",
            count=eligible_candidates,
        )
        self._telemetry.record_rights(
            decision="denied",
            reason="final_recheck",
            count=max(0, fused_candidates - authorized_candidates),
        )
        self._telemetry.record_retrieval(
            outcome=outcome,
            latency_ms=latency_ms,
            eligible_candidates=eligible_candidates,
            fused_candidates=fused_candidates,
            authorized_candidates=authorized_candidates,
            degradation=None,
            unauthorized_results=unauthorized_results,
        )
