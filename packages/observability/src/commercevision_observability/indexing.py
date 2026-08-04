"""Adapter from the indexing application observer seam to Phase 2 telemetry."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Protocol

from .phase2 import (
    Phase2Span,
    Phase2Telemetry,
    TelemetryDimensions,
    TelemetryIdentity,
)

_STEP_SPANS = {
    "collection": Phase2Span.MILVUS_COLLECTION,
    "rights": Phase2Span.RIGHTS_DECISION,
    "temporary_reference": Phase2Span.TEMPORARY_REFERENCE,
    "embedding": Phase2Span.EMBEDDING_REQUEST,
    "milvus_upsert": Phase2Span.MILVUS_UPSERT,
    "commit": Phase2Span.INDEX_COMMIT,
    "reconcile": Phase2Span.RECONCILIATION,
    "milvus_proof": Phase2Span.MILVUS_SEARCH,
}


class _VectorKind(Protocol):
    value: str


class _CollectionSpec(Protocol):
    vector_kind: _VectorKind


class _IndexRequest(Protocol):
    operation_id: str
    workspace_id: str
    target_id: str
    target_version: int


class _IndexTarget(Protocol):
    provider: str
    provider_request_id: str | None
    indexed_at: datetime
    collection_spec: _CollectionSpec


def _identity(request: _IndexRequest, target: _IndexTarget | None = None) -> TelemetryIdentity:
    return TelemetryIdentity(
        operation_id=request.operation_id,
        workspace_id=request.workspace_id,
        target_id=request.target_id,
        target_version=request.target_version,
        provider_request_id=target.provider_request_id if target is not None else None,
    )


class IndexingTelemetry:
    """Emit provider/vector signals without observing embedding input material."""

    def __init__(self, telemetry: Phase2Telemetry | None = None) -> None:
        self._telemetry = telemetry or Phase2Telemetry()

    @contextmanager
    def span(
        self,
        *,
        step: str,
        request: _IndexRequest,
        target: _IndexTarget | None = None,
    ) -> Iterator[None]:
        span = _STEP_SPANS.get(step)
        if span is None:
            raise ValueError("indexing telemetry step is unsupported")
        vector_kind = target.collection_spec.vector_kind if target is not None else None
        with self._telemetry.span(
            span,
            identity=_identity(request, target),
            dimensions=TelemetryDimensions(
                component="indexing",
                provider=target.provider if target is not None else None,
                vector_kind=getattr(vector_kind, "value", None),
            ),
        ):
            yield

    def provider_result(
        self,
        *,
        request: _IndexRequest,
        target: _IndexTarget,
        outcome: str,
        latency_ms: int,
        provider_request_id: str | None,
    ) -> None:
        identity = _identity(request, target)
        if provider_request_id is not None:
            identity = TelemetryIdentity(
                operation_id=identity.operation_id,
                workspace_id=identity.workspace_id,
                target_id=identity.target_id,
                target_version=identity.target_version,
                provider_request_id=provider_request_id,
            )
        self._telemetry.annotate(identity)
        self._telemetry.record_provider(
            provider=target.provider,
            operation="embedding",
            outcome=outcome,
            latency_ms=latency_ms,
        )

    def completed(
        self,
        *,
        request: _IndexRequest,
        target: _IndexTarget,
        outcome: str,
    ) -> None:
        del request
        indexed_at = target.indexed_at
        lag = (
            max(0.0, (datetime.now(UTC) - indexed_at).total_seconds())
            if isinstance(indexed_at, datetime)
            else 0.0
        )
        vector_kind = target.collection_spec.vector_kind
        self._telemetry.record_index(
            index_lag_seconds=lag,
            stale_vectors=1 if outcome == "stale" else 0,
            vector_kind=vector_kind.value,
        )
