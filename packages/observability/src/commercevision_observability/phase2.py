"""Sanitized, bounded-cardinality telemetry for the Phase 2 lifecycle."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, fields
from enum import StrEnum

from opentelemetry import metrics, trace
from opentelemetry.metrics import Meter
from opentelemetry.trace import Status, StatusCode, Tracer

from ._safe_telemetry import (
    StructuredLogger as _StructuredLogger,
)
from ._safe_telemetry import (
    bounded_dimension as _dimension,
)
from ._safe_telemetry import (
    safe_token as _token,
)
from .logging import get_logger


class Phase2Span(StrEnum):
    HTTP_REQUEST = "http.request"
    EVENT_CONSUME = "event.consume"
    UPLOAD_CREATE = "upload.create"
    UPLOAD_FINALIZE = "upload.finalize"
    UPLOAD_PROMOTION = "upload.promotion"
    VALIDATION = "validation"
    RIGHTS_DECISION = "rights.decision"
    VISION_REQUEST = "vision.request"
    PRODUCT_BRIEF_PERSISTENCE = "product_brief.persistence"
    PRODUCT_BRIEF_CONFIRMATION = "product_brief.confirmation"
    EMBEDDING_REQUEST = "embedding.request"
    MILVUS_COLLECTION = "milvus.collection"
    MILVUS_UPSERT = "milvus.upsert"
    MILVUS_DELETE = "milvus.delete"
    MILVUS_SEARCH = "milvus.search"
    LEXICAL_SEARCH = "lexical.search"
    FUSION = "retrieval.fusion"
    RERANK = "retrieval.rerank"
    FINAL_RIGHTS = "retrieval.final_rights"
    TEMPORARY_REFERENCE = "temporary_reference"
    DELETION = "deletion"
    RECONCILIATION = "reconciliation"
    REBUILD_BATCH = "rebuild.batch"
    MCP_TOOL = "mcp.tool"
    SCHEDULER_SCAN = "scheduler.scan"
    RETRIEVAL = "retrieval"
    INDEX_COMMIT = "index.commit"


@dataclass(frozen=True, slots=True)
class TelemetryIdentity:
    """Cross-boundary identifiers allowed in spans and structured logs, never metrics."""

    trace_id: str | None = None
    operation_id: str | None = None
    workspace_id: str | None = None
    target_id: str | None = None
    target_version: int | str | None = None
    event_id: str | None = None
    provider_request_id: str | None = None
    policy_id: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "operation_id",
            "workspace_id",
            "target_id",
            "event_id",
            "policy_id",
        ):
            object.__setattr__(self, name, _token(getattr(self, name)))
        object.__setattr__(self, "trace_id", _token(self.trace_id, always_hash=True))
        object.__setattr__(
            self,
            "provider_request_id",
            _token(self.provider_request_id, always_hash=True),
        )
        version = self.target_version
        if version is not None and not isinstance(version, int | str):
            raise TypeError("telemetry target version must be an integer or string")
        if isinstance(version, str):
            object.__setattr__(self, "target_version", _token(version))

    def attributes(self) -> dict[str, str | int]:
        names = {
            "trace_id": "commercevision.request.trace_id",
            "operation_id": "commercevision.operation.id",
            "workspace_id": "commercevision.workspace.id",
            "target_id": "commercevision.target.id",
            "target_version": "commercevision.target.version",
            "event_id": "commercevision.event.id",
            "provider_request_id": "commercevision.provider.request_id",
            "policy_id": "commercevision.policy.id",
        }
        return {
            names[field.name]: value
            for field in fields(self)
            if (value := getattr(self, field.name)) is not None
        }

    def log_fields(self) -> dict[str, str | int]:
        return {
            ("request_trace_id" if field.name == "trace_id" else field.name): value
            for field in fields(self)
            if (value := getattr(self, field.name)) is not None
        }


@dataclass(frozen=True, slots=True)
class TelemetryDimensions:
    """Low-cardinality symbolic dimensions accepted by lifecycle spans."""

    component: str | None = None
    phase: str | None = None
    channel: str | None = None
    vector_kind: str | None = None
    provider: str | None = None
    model: str | None = None
    outcome: str | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        for field in fields(self):
            object.__setattr__(self, field.name, _dimension(field.name, getattr(self, field.name)))

    def attributes(self) -> dict[str, str]:
        return {
            f"commercevision.{field.name}": value
            for field in fields(self)
            if (value := getattr(self, field.name)) is not None
        }


@dataclass(frozen=True, slots=True)
class TelemetryError:
    code: str
    category: str
    retryable: bool
    error_class: str

    def __post_init__(self) -> None:
        for name in ("code", "category", "error_class"):
            object.__setattr__(self, name, _dimension(name, getattr(self, name)))


class Phase2Telemetry:
    """One safe emission surface for all Phase 2 processes and adapters."""

    def __init__(
        self,
        *,
        logger: _StructuredLogger | None = None,
        tracer: Tracer | None = None,
        meter: Meter | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._logger = logger or get_logger("commercevision.phase2")
        self._tracer = tracer or trace.get_tracer("commercevision.phase2")
        resolved_meter = meter or metrics.get_meter("commercevision.phase2")
        self._monotonic = monotonic or time.monotonic
        self._quarantine_age = resolved_meter.create_histogram(
            "commercevision.phase2.quarantine.age", unit="s"
        )
        self._validation_outcomes = resolved_meter.create_counter(
            "commercevision.phase2.validation.outcomes", unit="{result}"
        )
        self._rights_decisions = resolved_meter.create_counter(
            "commercevision.phase2.rights.decisions", unit="{decision}"
        )
        self._provider_calls = resolved_meter.create_counter(
            "commercevision.phase2.provider.calls", unit="{call}"
        )
        self._provider_duration = resolved_meter.create_histogram(
            "commercevision.phase2.provider.duration", unit="ms"
        )
        self._provider_errors = resolved_meter.create_counter(
            "commercevision.phase2.provider.errors", unit="{error}"
        )
        self._provider_rate_limits = resolved_meter.create_counter(
            "commercevision.phase2.provider.rate_limits", unit="{limit}"
        )
        self._operation_events = resolved_meter.create_counter(
            "commercevision.phase2.operation.events", unit="{event}"
        )
        self._operation_lease_age = resolved_meter.create_histogram(
            "commercevision.phase2.operation.lease_age", unit="s"
        )
        self._operation_retries = resolved_meter.create_counter(
            "commercevision.phase2.operation.retries", unit="{retry}"
        )
        self._operation_dlq = resolved_meter.create_counter(
            "commercevision.phase2.operation.dlq", unit="{operation}"
        )
        self._confirmations = resolved_meter.create_counter(
            "commercevision.phase2.confirmations", unit="{confirmation}"
        )
        self._index_lag = resolved_meter.create_histogram(
            "commercevision.phase2.index.lag", unit="s"
        )
        self._stale_vectors = resolved_meter.create_histogram(
            "commercevision.phase2.index.stale_vectors", unit="{vector}"
        )
        self._retrieval_duration = resolved_meter.create_histogram(
            "commercevision.phase2.retrieval.duration", unit="ms"
        )
        self._retrieval_candidates = resolved_meter.create_histogram(
            "commercevision.phase2.retrieval.candidates", unit="{candidate}"
        )
        self._retrieval_degraded = resolved_meter.create_counter(
            "commercevision.phase2.retrieval.degraded", unit="{request}"
        )
        self._unauthorized_recall = resolved_meter.create_counter(
            "commercevision.phase2.retrieval.unauthorized_recall", unit="{result}"
        )
        self._deletion_backlog = resolved_meter.create_histogram(
            "commercevision.phase2.deletion.backlog", unit="{asset}"
        )
        self._rebuild_processed = resolved_meter.create_histogram(
            "commercevision.phase2.rebuild.processed", unit="{record}"
        )
        self._rebuild_remaining = resolved_meter.create_histogram(
            "commercevision.phase2.rebuild.remaining", unit="{record}"
        )

    @contextmanager
    def span(
        self,
        name: Phase2Span,
        *,
        identity: TelemetryIdentity | None = None,
        dimensions: TelemetryDimensions | None = None,
    ) -> Iterator[None]:
        identity = identity or TelemetryIdentity()
        dimensions = dimensions or TelemetryDimensions()
        attributes = {**identity.attributes(), **dimensions.attributes()}
        started = self._monotonic()
        event = name.value.replace(".", "_")
        with self._tracer.start_as_current_span(
            f"commercevision.phase2.{name.value}",
            attributes=attributes,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            self._logger.info(
                f"phase2_{event}_started",
                **identity.log_fields(),
                **self._trace_fields(),
            )
            try:
                yield
            except Exception as exc:
                error_type = _dimension("error_class", type(exc).__name__)
                span.set_attributes(
                    {
                        "error.type": error_type,
                        "commercevision.error.code": "UNCLASSIFIED",
                        "commercevision.error.category": "internal",
                        "commercevision.error.retryable": False,
                    }
                )
                span.set_status(Status(StatusCode.ERROR, "UNCLASSIFIED"))
                self._logger.error(
                    f"phase2_{event}_failed",
                    **identity.log_fields(),
                    error_code="UNCLASSIFIED",
                    error_category="internal",
                    error_type=error_type,
                    retryable=False,
                    **self._trace_fields(),
                )
                raise
            else:
                span.set_status(Status(StatusCode.OK))
                self._logger.info(
                    f"phase2_{event}_completed",
                    **identity.log_fields(),
                    duration_ms=round(max(0.0, self._monotonic() - started) * 1000, 3),
                    **self._trace_fields(),
                )

    def error(self, error: TelemetryError, *, identity: TelemetryIdentity | None = None) -> None:
        identity = identity or TelemetryIdentity()
        span = trace.get_current_span()
        span.set_attributes(
            {
                "error.type": error.error_class,
                "commercevision.error.code": error.code,
                "commercevision.error.category": error.category,
                "commercevision.error.retryable": error.retryable,
            }
        )
        span.set_status(Status(StatusCode.ERROR, error.code))
        self._logger.warning(
            "phase2_error",
            **identity.log_fields(),
            error_code=error.code,
            error_category=error.category,
            error_type=error.error_class,
            retryable=error.retryable,
            **self._trace_fields(),
        )

    @staticmethod
    def annotate(identity: TelemetryIdentity) -> None:
        trace.get_current_span().set_attributes(identity.attributes())

    def record_quarantine(self, *, age_seconds: float, state: str) -> None:
        self._quarantine_age.record(max(0.0, age_seconds), {"state": _dimension("state", state)})

    def record_validation(self, *, stage: str, verdict: str, reused: bool) -> None:
        self._validation_outcomes.add(
            1,
            {
                "stage": _dimension("stage", stage),
                "verdict": _dimension("verdict", verdict),
                "reused": reused,
            },
        )

    def record_rights(self, *, decision: str, reason: str, count: int = 1) -> None:
        self._rights_decisions.add(
            max(0, count),
            {
                "decision": _dimension("decision", decision),
                "reason": _dimension("reason", reason),
            },
        )

    def record_provider(
        self, *, provider: str, operation: str, outcome: str, latency_ms: float
    ) -> None:
        attributes = {
            "provider": _dimension("provider", provider),
            "operation": _dimension("operation", operation),
            "outcome": _dimension("outcome", outcome),
        }
        self._provider_calls.add(1, attributes)
        self._provider_duration.record(max(0.0, latency_ms), attributes)
        if outcome != "succeeded":
            self._provider_errors.add(1, attributes)
        if outcome == "throttled":
            self._provider_rate_limits.add(1, attributes)

    def record_operation(
        self,
        *,
        kind: str,
        outcome: str,
        lease_age_seconds: float,
        attempt: int,
    ) -> None:
        attributes = {
            "kind": _dimension("kind", kind),
            "outcome": _dimension("outcome", outcome),
        }
        self._operation_events.add(1, attributes)
        self._operation_lease_age.record(max(0.0, lease_age_seconds), attributes)
        if outcome == "retry_scheduled":
            self._operation_retries.add(1, {**attributes, "attempt": attempt})
        if outcome == "dead_lettered":
            self._operation_dlq.add(1, attributes)

    def record_confirmation(self, *, outcome: str) -> None:
        self._confirmations.add(1, {"outcome": _dimension("outcome", outcome)})

    def record_index(
        self, *, index_lag_seconds: float, stale_vectors: int, vector_kind: str
    ) -> None:
        attributes = {"vector_kind": _dimension("vector_kind", vector_kind)}
        self._index_lag.record(max(0.0, index_lag_seconds), attributes)
        self._stale_vectors.record(max(0, stale_vectors), attributes)

    def record_retrieval(
        self,
        *,
        outcome: str,
        latency_ms: float,
        eligible_candidates: int,
        fused_candidates: int,
        authorized_candidates: int,
        degradation: str | None,
        unauthorized_results: int,
    ) -> None:
        attributes = {"outcome": _dimension("outcome", outcome)}
        self._retrieval_duration.record(max(0.0, latency_ms), attributes)
        for stage, count in (
            ("eligible", eligible_candidates),
            ("fused", fused_candidates),
            ("authorized", authorized_candidates),
        ):
            self._retrieval_candidates.record(max(0, count), {**attributes, "stage": stage})
        if degradation is not None:
            self.record_retrieval_degradation(reason=degradation, outcome=outcome)
        self._unauthorized_recall.add(max(0, unauthorized_results), attributes)

    def record_retrieval_degradation(self, *, reason: str, outcome: str = "degraded") -> None:
        self._retrieval_degraded.add(
            1,
            {
                "outcome": _dimension("outcome", outcome),
                "reason": _dimension("degradation", reason),
            },
        )

    def record_deletion(self, *, backlog: int, outcome: str) -> None:
        self._deletion_backlog.record(max(0, backlog), {"outcome": _dimension("outcome", outcome)})

    def record_rebuild(
        self,
        *,
        phase: str,
        processed: int,
        remaining: int | None,
        outcome: str,
    ) -> None:
        attributes = {
            "phase": _dimension("phase", phase),
            "outcome": _dimension("outcome", outcome),
        }
        self._rebuild_processed.record(max(0, processed), attributes)
        if remaining is not None:
            self._rebuild_remaining.record(max(0, remaining), attributes)

    @staticmethod
    def _trace_fields() -> dict[str, str]:
        context = trace.get_current_span().get_span_context()
        if not context.is_valid:
            return {}
        return {
            "trace_id": f"{context.trace_id:032x}",
            "span_id": f"{context.span_id:016x}",
        }


def safe_metric_attributes(values: Mapping[str, str]) -> dict[str, str]:
    """Validate Adapter-supplied metric labels before they reach a Meter."""

    return {name: _dimension(name, value) for name, value in values.items()}
