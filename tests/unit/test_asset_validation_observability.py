from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from commercevision_application import OperationExecutionRequest
from commercevision_domain import (
    AssetKind,
    OperationKind,
    ValidationStage,
    ValidationVerdict,
)
from commercevision_worker.asset_validation_observability import (
    AssetValidationTelemetry,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)


class RecordingLogger:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict[str, object]]] = []

    def info(self, event: str, **values: object) -> None:
        self.records.append(("info", event, values))

    def warning(self, event: str, **values: object) -> None:
        self.records.append(("warning", event, values))

    def error(self, event: str, **values: object) -> None:
        self.records.append(("error", event, values))


def _request() -> OperationExecutionRequest:
    return OperationExecutionRequest(
        operation_id="019f8a00-0000-7000-8000-000000000701",
        workspace_id="validation-observability-workspace",
        kind=OperationKind.ASSET_VALIDATION,
        target_type="ASSET_VERSION",
        target_id="019f8a00-0000-7000-8000-000000000702",
        target_version=1,
        input_hash="a" * 64,
        input_ref=("mysql://asset-versions/019f8a00-0000-7000-8000-000000000702"),
        provider_request_id=None,
        attempt_count=2,
        idempotency_key=("durable-operation:019f8a00-0000-7000-8000-000000000701"),
    )


def _telemetry() -> tuple[
    AssetValidationTelemetry,
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
    telemetry = AssetValidationTelemetry(
        logger=logger,
        tracer=tracer_provider.get_tracer("asset-validation-test"),
        meter=meter_provider.get_meter("asset-validation-test"),
        clock=lambda: NOW,
    )
    return telemetry, logger, span_exporter, metric_reader


def test_asset_validation_telemetry_emits_sanitized_stage_and_lifecycle_signals() -> None:
    telemetry, logger, span_exporter, metric_reader = _telemetry()
    request = _request()
    target = SimpleNamespace(
        asset=SimpleNamespace(
            id="019f8a00-0000-7000-8000-000000000703",
            kind=AssetKind.IMAGE,
            created_at=NOW - timedelta(seconds=90),
        ),
        asset_version=SimpleNamespace(
            id=request.target_id,
            validation_policy_version="asset-validation-v1",
        ),
    )
    result = SimpleNamespace(
        id="019f8a00-0000-7000-8000-000000000704",
        operation_id=request.operation_id,
        workspace_id=request.workspace_id,
        asset_version_id=request.target_id,
        attempt_number=request.attempt_count,
        stage=ValidationStage.MALWARE,
        verdict=ValidationVerdict.PASS,
        reason_code=None,
        validator_name="clamav",
        validator_version="ClamAV 1.5.3",
        policy_version="asset-validation-v1",
    )

    with telemetry.operation(request=request, mode="execute"):
        telemetry.target_bound(request=request, target=target)  # type: ignore[arg-type]
        with telemetry.stage(
            request=request,
            target=target,  # type: ignore[arg-type]
            stage=ValidationStage.MALWARE,
            reused=False,
        ):
            telemetry.result(result=result, reused=False)  # type: ignore[arg-type]
        telemetry.completed(
            request=request,
            target=target,  # type: ignore[arg-type]
            outcome="PENDING_RIGHTS",
        )

    spans = span_exporter.get_finished_spans()
    assert [span.name for span in spans] == [
        "commercevision.asset.validation.malware",
        "commercevision.asset.validation",
    ]
    assert spans[0].attributes["commercevision.validation.stage"] == "MALWARE"
    assert spans[1].attributes["commercevision.asset.kind"] == "IMAGE"

    metrics = metric_reader.get_metrics_data()
    metric_names = {
        metric.name
        for resource_metric in metrics.resource_metrics
        for scope_metric in resource_metric.scope_metrics
        for metric in scope_metric.metrics
    }
    assert metric_names >= {
        "commercevision.asset_validation.operations",
        "commercevision.asset_validation.completions",
        "commercevision.asset_validation.stage_runs",
        "commercevision.asset_validation.stage_results",
        "commercevision.asset_validation.operation.duration",
        "commercevision.asset_validation.stage.duration",
        "commercevision.asset_validation.quarantine.age",
    }

    serialized_logs = json.dumps(logger.records, sort_keys=True)
    assert "asset_validation_stage_result" in serialized_logs
    assert "PENDING_RIGHTS" in serialized_logs
    assert "trace_id" in serialized_logs
    for forbidden in (
        "evidence",
        "content_sha256",
        "object_etag",
        "object_key",
        "provider_payload",
        "signed_url",
    ):
        assert forbidden not in serialized_logs


def test_asset_validation_telemetry_does_not_record_unclassified_exception_message() -> None:
    telemetry, logger, span_exporter, _ = _telemetry()
    secret_payload = "provider-secret raw-payload https://signed.invalid/object"

    with (
        pytest.raises(RuntimeError, match="provider-secret"),
        telemetry.operation(request=_request(), mode="execute"),
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
    assert "UNCLASSIFIED" in serialized_logs
