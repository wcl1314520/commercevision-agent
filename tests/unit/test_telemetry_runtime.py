from __future__ import annotations

import pytest
from commercevision_observability import (
    Phase2Span,
    build_telemetry_runtime,
    configure_telemetry,
)
from commercevision_observability import runtime as runtime_module
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


def test_telemetry_runtime_exports_resource_scoped_traces_and_metrics() -> None:
    spans = InMemorySpanExporter()
    metrics = InMemoryMetricReader()
    runtime = build_telemetry_runtime(
        service_name="control-api",
        service_version="2.0.0",
        environment="test",
        span_processor=SimpleSpanProcessor(spans),
        metric_reader=metrics,
    )

    telemetry = runtime.phase2()
    with telemetry.span(Phase2Span.HTTP_REQUEST):
        telemetry.record_deletion(backlog=1, outcome="pending")

    span = spans.get_finished_spans()[0]
    resource = dict(span.resource.attributes)
    assert resource["service.name"] == "control-api"
    assert resource["service.version"] == "2.0.0"
    assert resource["deployment.environment.name"] == "test"
    assert any(
        metric.name == "commercevision.phase2.deletion.backlog"
        for resource_metric in metrics.get_metrics_data().resource_metrics
        for scope_metric in resource_metric.scope_metrics
        for metric in scope_metric.metrics
    )
    runtime.shutdown()


@pytest.mark.parametrize(
    "endpoint",
    [
        "ftp://collector:4318",
        "http://user:secret@collector:4318",
        "http://collector:4318/path?token=secret",
    ],
)
def test_telemetry_runtime_rejects_unsafe_collector_endpoint(endpoint: str) -> None:
    with pytest.raises(ValueError, match="OTLP endpoint"):
        build_telemetry_runtime(
            service_name="worker",
            service_version="2.0.0",
            environment="test",
            endpoint=endpoint,
        )


def test_configure_telemetry_replaces_a_closed_process_runtime(monkeypatch) -> None:
    built = []
    monkeypatch.setattr(runtime_module, "_runtime_by_pid", {})
    monkeypatch.setattr(runtime_module.trace, "set_tracer_provider", lambda _provider: None)
    monkeypatch.setattr(runtime_module.metrics, "set_meter_provider", lambda _provider: None)

    def _build(**kwargs):
        del kwargs
        runtime = build_telemetry_runtime(
            service_name="worker",
            service_version="2.0.0",
            environment="test",
        )
        built.append(runtime)
        return runtime

    monkeypatch.setattr("commercevision_observability.runtime.build_telemetry_runtime", _build)
    first = configure_telemetry(
        service_name="worker",
        service_version="2.0.0",
        environment="test",
        endpoint="http://collector:4318",
    )
    assert first is not None
    first.shutdown()

    second = configure_telemetry(
        service_name="worker",
        service_version="2.0.0",
        environment="test",
        endpoint="http://collector:4318",
    )

    assert second is not first
    assert len(built) == 2
    second.shutdown()
