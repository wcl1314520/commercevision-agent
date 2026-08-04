"""Process-local OpenTelemetry SDK lifecycle and OTLP export wiring."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from threading import Lock
from urllib.parse import urlsplit

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from .phase2 import Phase2Telemetry


def _validated_endpoint(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("OTLP endpoint must be an origin without credentials or parameters")
    return endpoint.rstrip("/")


@dataclass(slots=True)
class TelemetryRuntime:
    """Own SDK providers so every service can flush cleanly during shutdown."""

    tracer_provider: TracerProvider
    meter_provider: MeterProvider
    _closed: bool = field(default=False, init=False)
    _lock: Lock = field(default_factory=Lock, init=False)

    def phase2(self) -> Phase2Telemetry:
        return Phase2Telemetry(
            tracer=self.tracer_provider.get_tracer("commercevision.phase2"),
            meter=self.meter_provider.get_meter("commercevision.phase2"),
        )

    @property
    def closed(self) -> bool:
        return self._closed

    def shutdown(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self.tracer_provider.shutdown()
        self.meter_provider.shutdown()


def build_telemetry_runtime(
    *,
    service_name: str,
    service_version: str,
    environment: str,
    endpoint: str | None = None,
    span_processor: SpanProcessor | None = None,
    metric_reader: MetricReader | None = None,
) -> TelemetryRuntime:
    """Build an isolated SDK runtime; callers decide whether to install it globally."""

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": service_version,
            "deployment.environment.name": environment,
        }
    )
    tracer_provider = TracerProvider(resource=resource)
    readers: list[MetricReader] = []
    if endpoint is not None:
        origin = _validated_endpoint(endpoint)
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        span_processor = span_processor or BatchSpanProcessor(
            OTLPSpanExporter(endpoint=f"{origin}/v1/traces")
        )
        metric_reader = metric_reader or PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=f"{origin}/v1/metrics")
        )
    if span_processor is not None:
        tracer_provider.add_span_processor(span_processor)
    if metric_reader is not None:
        readers.append(metric_reader)
    meter_provider = MeterProvider(resource=resource, metric_readers=readers)
    return TelemetryRuntime(
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
    )


_runtime_lock = Lock()
_runtime_by_pid: dict[int, TelemetryRuntime] = {}


def configure_telemetry(
    *,
    service_name: str,
    service_version: str,
    environment: str,
    endpoint: str | None = None,
) -> TelemetryRuntime | None:
    """Install one fail-open OTLP runtime in the current process when configured."""

    configured_endpoint = endpoint or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    disabled = os.getenv("OTEL_SDK_DISABLED", "false").lower() == "true"
    if disabled or not configured_endpoint:
        return None
    pid = os.getpid()
    with _runtime_lock:
        existing = _runtime_by_pid.get(pid)
        if existing is not None and not existing.closed:
            return existing
        runtime = build_telemetry_runtime(
            service_name=service_name,
            service_version=service_version,
            environment=environment,
            endpoint=configured_endpoint,
        )
        trace.set_tracer_provider(runtime.tracer_provider)
        metrics.set_meter_provider(runtime.meter_provider)
        _runtime_by_pid[pid] = runtime
        return runtime
