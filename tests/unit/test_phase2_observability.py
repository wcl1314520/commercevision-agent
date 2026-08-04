from __future__ import annotations

import json
from dataclasses import asdict

import pytest
from commercevision_observability import (
    Phase2Span,
    Phase2Telemetry,
    RetrievalTelemetry,
    TelemetryDimensions,
    TelemetryError,
    TelemetryIdentity,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


class _MemoryLogger:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def info(self, event: str, **values: object) -> None:
        self.events.append({"level": "info", "event": event, **values})

    def warning(self, event: str, **values: object) -> None:
        self.events.append({"level": "warning", "event": event, **values})

    def error(self, event: str, **values: object) -> None:
        self.events.append({"level": "error", "event": event, **values})


def _telemetry() -> tuple[
    Phase2Telemetry,
    _MemoryLogger,
    InMemorySpanExporter,
    InMemoryMetricReader,
]:
    trace_provider = TracerProvider()
    span_exporter = InMemorySpanExporter()
    trace_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[metric_reader])
    logger = _MemoryLogger()
    telemetry = Phase2Telemetry(
        logger=logger,
        tracer=trace_provider.get_tracer("phase2-test"),
        meter=meter_provider.get_meter("phase2-test"),
        monotonic=lambda: 10.0,
    )
    return telemetry, logger, span_exporter, metric_reader


def _identity(**overrides: object) -> TelemetryIdentity:
    values: dict[str, object] = {
        "trace_id": "trace-safe",
        "operation_id": "operation-safe",
        "workspace_id": "workspace-safe",
        "target_id": "target-safe",
        "target_version": 7,
        "event_id": "event-safe",
        "provider_request_id": "provider-request-super-secret",
        "policy_id": "retrieval-policy-v1",
    }
    values.update(overrides)
    return TelemetryIdentity(**values)  # type: ignore[arg-type]


def _metric_points(reader: InMemoryMetricReader) -> dict[str, list[object]]:
    data = reader.get_metrics_data()
    return {
        metric.name: list(metric.data.data_points)
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }


def test_phase2_span_propagates_only_sanitized_stable_attributes() -> None:
    telemetry, logger, span_exporter, _ = _telemetry()

    with telemetry.span(
        Phase2Span.MILVUS_SEARCH,
        identity=_identity(trace_id="https://signed.example/object?token=secret"),
        dimensions=TelemetryDimensions(
            component="dense_retrieval",
            channel="IMAGE_DENSE",
            vector_kind="IMAGE",
            provider="fixture",
            model="embedding-v1",
            outcome="succeeded",
        ),
    ):
        pass

    span = span_exporter.get_finished_spans()[0]
    assert span.name == "commercevision.phase2.milvus.search"
    assert span.attributes["commercevision.operation.id"] == "operation-safe"
    assert span.attributes["commercevision.target.version"] == 7
    assert span.attributes["commercevision.event.id"] == "event-safe"
    assert span.attributes["commercevision.request.trace_id"].startswith("sha256:")
    assert span.attributes["commercevision.provider.request_id"].startswith("sha256:")

    serialized = json.dumps(
        {
            "logs": logger.events,
            "spans": [dict(item.attributes) for item in span_exporter.get_finished_spans()],
            "identity": asdict(_identity()),
        },
        default=str,
    )
    assert "signed.example" not in serialized
    assert "token=secret" not in serialized
    assert _identity().trace_id is not None
    assert _identity().trace_id.startswith("sha256:")
    assert "provider-request-super-secret" not in serialized


def test_phase2_span_records_stable_error_without_exception_message() -> None:
    telemetry, logger, span_exporter, _ = _telemetry()

    with (
        pytest.raises(RuntimeError, match="raw prompt and signed-url-secret"),
        telemetry.span(Phase2Span.REBUILD_BATCH, identity=_identity()),
    ):
        raise RuntimeError("raw prompt and signed-url-secret")

    span = span_exporter.get_finished_spans()[0]
    assert span.status.status_code.name == "ERROR"
    assert span.attributes["error.type"] == "RuntimeError"
    assert span.attributes["commercevision.error.code"] == "UNCLASSIFIED"
    assert "raw prompt" not in json.dumps(logger.events, default=str)

    telemetry.error(
        TelemetryError(
            code="PROVIDER_THROTTLED",
            category="provider",
            retryable=True,
            error_class="ProviderFailure",
        ),
        identity=_identity(),
    )
    assert logger.events[-1]["error_code"] == "PROVIDER_THROTTLED"
    assert logger.events[-1]["retryable"] is True


def test_phase2_metrics_cover_operational_failure_and_progress_signals() -> None:
    telemetry, _, _, metric_reader = _telemetry()

    telemetry.record_quarantine(age_seconds=17.5, state="open")
    telemetry.record_validation(stage="MALWARE", verdict="PASS", reused=False)
    telemetry.record_rights(decision="denied", reason="expired")
    telemetry.record_provider(
        provider="fixture", operation="embedding", outcome="throttled", latency_ms=41
    )
    telemetry.record_operation(
        kind="ASSET_INDEXING",
        outcome="retry_scheduled",
        lease_age_seconds=3.5,
        attempt=2,
    )
    telemetry.record_operation(
        kind="ASSET_INDEXING",
        outcome="dead_lettered",
        lease_age_seconds=7.0,
        attempt=5,
    )
    telemetry.record_confirmation(outcome="confirmed")
    telemetry.record_index(index_lag_seconds=8.0, stale_vectors=2, vector_kind="IMAGE")
    telemetry.record_retrieval(
        outcome="degraded",
        latency_ms=23,
        eligible_candidates=6,
        fused_candidates=4,
        authorized_candidates=3,
        degradation="RERANKER_UNAVAILABLE",
        unauthorized_results=0,
    )
    telemetry.record_deletion(backlog=5, outcome="pending")
    telemetry.record_rebuild(phase="BACKFILLING", processed=10, remaining=20, outcome="progress")

    points = _metric_points(metric_reader)
    expected = {
        "commercevision.phase2.quarantine.age",
        "commercevision.phase2.validation.outcomes",
        "commercevision.phase2.rights.decisions",
        "commercevision.phase2.provider.calls",
        "commercevision.phase2.provider.duration",
        "commercevision.phase2.provider.errors",
        "commercevision.phase2.provider.rate_limits",
        "commercevision.phase2.operation.events",
        "commercevision.phase2.operation.lease_age",
        "commercevision.phase2.operation.retries",
        "commercevision.phase2.operation.dlq",
        "commercevision.phase2.confirmations",
        "commercevision.phase2.index.lag",
        "commercevision.phase2.index.stale_vectors",
        "commercevision.phase2.retrieval.duration",
        "commercevision.phase2.retrieval.candidates",
        "commercevision.phase2.retrieval.degraded",
        "commercevision.phase2.retrieval.unauthorized_recall",
        "commercevision.phase2.deletion.backlog",
        "commercevision.phase2.rebuild.processed",
        "commercevision.phase2.rebuild.remaining",
    }
    assert expected <= points.keys()
    assert all(points[name] for name in expected)


def test_telemetry_dimensions_reject_unbounded_or_sensitive_values() -> None:
    with pytest.raises(ValueError, match="component"):
        TelemetryDimensions(component="x" * 65)
    with pytest.raises(ValueError, match="provider"):
        TelemetryDimensions(provider="https://user:secret@example.test/path")


def test_retrieval_telemetry_maps_application_steps_and_metrics() -> None:
    telemetry, _, span_exporter, metric_reader = _telemetry()
    observer = RetrievalTelemetry(telemetry)

    for step, component in (
        ("request", None),
        ("initial_rights", None),
        ("temporary_reference", "fixture"),
        ("embedding", "fixture"),
        ("milvus_search", "PRODUCT_FUSED_DENSE"),
        ("lexical_search", "LEXICAL"),
        ("fusion", None),
        ("rerank", None),
        ("final_rights", None),
    ):
        with observer.span(
            step=step,
            workspace_id="workspace-safe",
            policy_id="retrieval-policy-v1",
            component=component,
        ):
            pass
    observer.degraded(component="RERANKER", code="RERANKER_UNAVAILABLE")
    observer.provider_result(
        workspace_id="workspace-safe",
        policy_id="retrieval-policy-v1",
        provider="fixture",
        outcome="succeeded",
        latency_ms=7,
        provider_request_id="provider-result-secret",
    )
    observer.completed(
        outcome="degraded",
        latency_ms=12,
        eligible_candidates=4,
        fused_candidates=3,
        authorized_candidates=2,
        unauthorized_results=0,
    )

    assert [span.name for span in span_exporter.get_finished_spans()] == [
        "commercevision.phase2.retrieval",
        "commercevision.phase2.rights.decision",
        "commercevision.phase2.temporary_reference",
        "commercevision.phase2.embedding.request",
        "commercevision.phase2.milvus.search",
        "commercevision.phase2.lexical.search",
        "commercevision.phase2.retrieval.fusion",
        "commercevision.phase2.retrieval.rerank",
        "commercevision.phase2.retrieval.final_rights",
    ]
    points = _metric_points(metric_reader)
    degraded = points["commercevision.phase2.retrieval.degraded"]
    assert degraded[0].attributes["reason"] == "RERANKER_UNAVAILABLE"
