from __future__ import annotations

import hashlib
import json

import pytest
from commercevision_observability import ProductBriefTelemetry
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)


class RecordingLogger:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict[str, object]]] = []

    def info(self, event: str, **values: object) -> None:
        self.records.append(("info", event, values))

    def warning(self, event: str, **values: object) -> None:
        self.records.append(("warning", event, values))

    def error(self, event: str, **values: object) -> None:
        self.records.append(("error", event, values))


def _telemetry() -> tuple[
    ProductBriefTelemetry,
    RecordingLogger,
    InMemorySpanExporter,
    InMemoryMetricReader,
]:
    logger = RecordingLogger()
    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[metric_reader])
    telemetry = ProductBriefTelemetry(
        logger=logger,
        tracer=tracer_provider.get_tracer("product-brief-test"),
        meter=meter_provider.get_meter("product-brief-test"),
    )
    return telemetry, logger, span_exporter, metric_reader


def _serialized_telemetry(
    logger: RecordingLogger,
    span_exporter: InMemorySpanExporter,
    metric_reader: InMemoryMetricReader,
) -> str:
    metrics = metric_reader.get_metrics_data()
    return json.dumps(
        {
            "logs": logger.records,
            "spans": [
                {
                    "attributes": dict(span.attributes),
                    "events": [
                        {
                            "name": event.name,
                            "attributes": dict(event.attributes),
                        }
                        for event in span.events
                    ],
                    "name": span.name,
                    "status": span.status.description,
                }
                for span in span_exporter.get_finished_spans()
            ],
            "metrics": [
                {
                    "attributes": [
                        dict(data_point.attributes) for data_point in metric.data.data_points
                    ],
                    "name": metric.name,
                }
                for resource_metric in metrics.resource_metrics
                for scope_metric in resource_metric.scope_metrics
                for metric in scope_metric.metrics
            ],
        },
        sort_keys=True,
    )


def test_product_brief_telemetry_covers_vision_persistence_and_confirmation() -> None:
    telemetry, logger, span_exporter, metric_reader = _telemetry()

    with telemetry.vision_request(
        operation_id="operation-1",
        operation_attempt=2,
        workspace_id="workspace-1",
        product_brief_id="brief-1",
        provider="alibaba-model-studio",
        endpoint_region="cn-beijing",
        requested_model="qwen3-vl-plus",
    ):
        telemetry.provider_result(
            operation_id="operation-1",
            operation_attempt=2,
            workspace_id="workspace-1",
            product_brief_id="brief-1",
            provider="alibaba-model-studio",
            requested_model="qwen3-vl-plus",
            status="THROTTLED",
            latency_ms=81,
            error_category="rate_limit",
            retryable=True,
            provider_request_id="request-1",
        )
    with telemetry.persistence(
        operation_id="operation-1",
        workspace_id="workspace-1",
        product_brief_id="brief-1",
        phase="model_result",
    ):
        pass
    with telemetry.confirmation(
        trace_id="trace-1",
        workspace_id="workspace-1",
        product_brief_id="brief-1",
        product_brief_version_id="brief-version-1",
    ):
        telemetry.confirmation_result(
            workspace_id="workspace-1",
            product_brief_id="brief-1",
            product_brief_version_id="brief-version-1",
            result="confirmed",
        )

    spans = span_exporter.get_finished_spans()
    assert [span.name for span in spans] == [
        "commercevision.product_brief.vision",
        "commercevision.product_brief.persistence",
        "commercevision.product_brief.confirmation",
    ]
    assert spans[0].attributes["commercevision.provider.status"] == "THROTTLED"
    assert spans[2].attributes["commercevision.confirmation.result"] == "confirmed"

    metrics = metric_reader.get_metrics_data()
    metric_names = {
        metric.name
        for resource_metric in metrics.resource_metrics
        for scope_metric in resource_metric.scope_metrics
        for metric in scope_metric.metrics
    }
    assert metric_names >= {
        "commercevision.product_brief.provider.calls",
        "commercevision.product_brief.provider.duration",
        "commercevision.product_brief.provider.errors",
        "commercevision.product_brief.provider.rate_limits",
        "commercevision.product_brief.confirmations",
    }

    serialized_logs = json.dumps(logger.records, sort_keys=True)
    assert "product_brief_vision_completed" in serialized_logs
    assert "product_brief_confirmed" in serialized_logs
    for forbidden in (
        "bucket",
        "object_key",
        "prompt",
        "provider_payload",
        "request_artifact",
        "response_artifact",
        "signed_url",
    ):
        assert forbidden not in serialized_logs


def test_product_brief_telemetry_redacts_unclassified_exception_messages() -> None:
    telemetry, logger, span_exporter, _ = _telemetry()
    secret_payload = "provider-secret raw-payload https://signed.invalid/object"

    with (
        pytest.raises(RuntimeError, match="provider-secret"),
        telemetry.vision_request(
            operation_id="operation-1",
            operation_attempt=1,
            workspace_id="workspace-1",
            product_brief_id="brief-1",
            provider="alibaba-model-studio",
            endpoint_region="cn-beijing",
            requested_model="qwen3-vl-plus",
        ),
    ):
        raise RuntimeError(secret_payload)

    serialized_logs = json.dumps(logger.records, sort_keys=True)
    serialized_spans = json.dumps(
        [
            {
                "attributes": dict(span.attributes),
                "events": [event.name for event in span.events],
                "status": span.status.description,
            }
            for span in span_exporter.get_finished_spans()
        ],
        sort_keys=True,
    )
    assert secret_payload not in serialized_logs
    assert secret_payload not in serialized_spans
    assert "RuntimeError" in serialized_logs


@pytest.mark.parametrize(
    "raw_request_id",
    [
        "AKIA" + "IOSFODNN7EXAMPLE",
        "sk-" + "proj-test_0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        "sk_" + "live_51TEST0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        "provider-request-ordinary-42",
    ],
    ids=["aws-like", "openai-like", "stripe-like", "ordinary"],
)
def test_provider_request_ids_are_tokenized_before_any_telemetry(
    raw_request_id: str,
) -> None:
    telemetry, logger, span_exporter, metric_reader = _telemetry()

    with telemetry.vision_request(
        operation_id="operation-1",
        operation_attempt=1,
        workspace_id="workspace-1",
        product_brief_id="brief-1",
        provider="alibaba-model-studio",
        endpoint_region="cn-beijing",
        requested_model="qwen3-vl-plus",
    ):
        telemetry.provider_result(
            operation_id="operation-1",
            operation_attempt=1,
            workspace_id="workspace-1",
            product_brief_id="brief-1",
            provider="alibaba-model-studio",
            requested_model="qwen3-vl-plus",
            status="SUCCEEDED",
            latency_ms=7,
            error_category=None,
            retryable=None,
            provider_request_id=raw_request_id,
        )

    expected_token = f"sha256:{hashlib.sha256(raw_request_id.encode()).hexdigest()}"
    serialized = _serialized_telemetry(
        logger,
        span_exporter,
        metric_reader,
    )
    provider_log = next(
        values for _, event, values in logger.records if event == "product_brief_provider_result"
    )
    span = span_exporter.get_finished_spans()[0]

    assert provider_log["provider_request_id"] == expected_token
    assert span.attributes["commercevision.provider.request_id"] == expected_token
    assert expected_token in serialized
    assert raw_request_id not in serialized
