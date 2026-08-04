"""OpenTelemetry and structured-log signals for Asset validation."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Protocol

from commercevision_application.asset_validation_observability import (
    AssetValidationCompletion,
    AssetValidationMode,
)
from commercevision_application.asset_validation_target import AssetValidationTarget
from commercevision_application.operations import (
    OperationExecutionFailure,
    OperationExecutionRequest,
)
from commercevision_domain import AssetValidationResult, ValidationStage
from commercevision_observability import Phase2Telemetry, get_logger
from opentelemetry import metrics, trace
from opentelemetry.metrics import Meter
from opentelemetry.trace import Status, StatusCode, Tracer


class _StructuredLogger(Protocol):
    def info(self, event: str, **values: object) -> object: ...

    def warning(self, event: str, **values: object) -> object: ...

    def error(self, event: str, **values: object) -> object: ...


class AssetValidationTelemetry:
    """Emit bounded-cardinality metrics and sanitized lifecycle diagnostics."""

    def __init__(
        self,
        *,
        logger: _StructuredLogger | None = None,
        tracer: Tracer | None = None,
        meter: Meter | None = None,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
        phase2: Phase2Telemetry | None = None,
    ) -> None:
        self._logger = logger or get_logger("commercevision.worker.asset_validation")
        self._tracer = tracer or trace.get_tracer("commercevision.worker.asset_validation")
        resolved_meter = meter or metrics.get_meter("commercevision.worker.asset_validation")
        self._clock = clock or (lambda: datetime.now(UTC))
        self._monotonic = monotonic or time.monotonic
        self._phase2 = phase2 or Phase2Telemetry(
            logger=self._logger,
            tracer=self._tracer,
            meter=resolved_meter,
            monotonic=self._monotonic,
        )
        self._operations = resolved_meter.create_counter(
            "commercevision.asset_validation.operations",
            unit="{operation}",
            description="Durable Asset validation operation outcomes",
        )
        self._completions = resolved_meter.create_counter(
            "commercevision.asset_validation.completions",
            unit="{asset}",
            description="Asset validation lifecycle completion states",
        )
        self._stage_runs = resolved_meter.create_counter(
            "commercevision.asset_validation.stage_runs",
            unit="{stage}",
            description="Asset validation stage executions and reuses",
        )
        self._stage_results = resolved_meter.create_counter(
            "commercevision.asset_validation.stage_results",
            unit="{result}",
            description="Normalized append-only validation verdicts",
        )
        self._operation_duration = resolved_meter.create_histogram(
            "commercevision.asset_validation.operation.duration",
            unit="s",
            description="End-to-end Asset validation operation duration",
        )
        self._stage_duration = resolved_meter.create_histogram(
            "commercevision.asset_validation.stage.duration",
            unit="s",
            description="Asset validation stage duration",
        )
        self._quarantine_age = resolved_meter.create_histogram(
            "commercevision.asset_validation.quarantine.age",
            unit="s",
            description="Quarantine age when a validation target is bound",
        )

    @contextmanager
    def operation(
        self,
        *,
        request: OperationExecutionRequest,
        mode: AssetValidationMode,
    ) -> Iterator[None]:
        started = self._monotonic()
        span_attributes = {
            "commercevision.operation.id": request.operation_id,
            "commercevision.operation.attempt": request.attempt_count,
            "commercevision.operation.mode": mode,
            "commercevision.workspace.id": request.workspace_id,
            "commercevision.asset_version.id": request.target_id,
        }
        result = "success"
        with self._tracer.start_as_current_span(
            "commercevision.asset.validation",
            attributes=span_attributes,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            self._logger.info(
                "asset_validation_started",
                **self._operation_log_fields(request=request, mode=mode),
            )
            try:
                yield
            except OperationExecutionFailure as exc:
                result = "retryable_failure" if exc.error.retryable else "terminal_failure"
                span.set_attributes(
                    {
                        "error.type": "OperationExecutionFailure",
                        "commercevision.error.code": exc.error.code,
                        "commercevision.error.category": exc.error.category,
                        "commercevision.error.retryable": exc.error.retryable,
                    }
                )
                span.set_status(Status(StatusCode.ERROR, exc.error.code))
                self._logger.warning(
                    "asset_validation_failed",
                    **self._operation_log_fields(request=request, mode=mode),
                    error_code=exc.error.code,
                    error_category=exc.error.category,
                    retryable=exc.error.retryable,
                )
                raise
            except Exception as exc:
                result = "unclassified_failure"
                error_type = type(exc).__name__
                span.set_attribute("error.type", error_type)
                span.set_status(Status(StatusCode.ERROR, "UNCLASSIFIED"))
                self._logger.error(
                    "asset_validation_failed",
                    **self._operation_log_fields(request=request, mode=mode),
                    error_code="UNCLASSIFIED",
                    error_category="internal",
                    error_type=error_type,
                    retryable=False,
                )
                raise
            else:
                span.set_status(Status(StatusCode.OK))
                self._logger.info(
                    "asset_validation_operation_completed",
                    **self._operation_log_fields(request=request, mode=mode),
                )
            finally:
                metric_attributes = {"mode": mode, "result": result}
                self._operations.add(1, metric_attributes)
                self._operation_duration.record(
                    max(0.0, self._monotonic() - started),
                    metric_attributes,
                )

    @contextmanager
    def stage(
        self,
        *,
        request: OperationExecutionRequest,
        target: AssetValidationTarget,
        stage: ValidationStage,
        reused: bool,
    ) -> Iterator[None]:
        started = self._monotonic()
        result = "success"
        attributes = {
            "commercevision.operation.id": request.operation_id,
            "commercevision.workspace.id": request.workspace_id,
            "commercevision.asset.id": target.asset.id,
            "commercevision.asset_version.id": target.asset_version.id,
            "commercevision.validation.stage": stage.value,
            "commercevision.validation.reused": reused,
        }
        with self._tracer.start_as_current_span(
            f"commercevision.asset.validation.{stage.value.lower()}",
            attributes=attributes,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                yield
            except OperationExecutionFailure as exc:
                result = "retryable_failure" if exc.error.retryable else "terminal_failure"
                span.set_attributes(
                    {
                        "commercevision.error.code": exc.error.code,
                        "commercevision.error.category": exc.error.category,
                        "commercevision.error.retryable": exc.error.retryable,
                    }
                )
                span.set_status(Status(StatusCode.ERROR, exc.error.code))
                raise
            except Exception as exc:
                result = "unclassified_failure"
                span.set_attribute("error.type", type(exc).__name__)
                span.set_status(Status(StatusCode.ERROR, "UNCLASSIFIED"))
                raise
            else:
                span.set_status(Status(StatusCode.OK))
            finally:
                metric_attributes = {
                    "stage": stage.value,
                    "reused": reused,
                    "result": result,
                }
                self._stage_runs.add(1, metric_attributes)
                self._stage_duration.record(
                    max(0.0, self._monotonic() - started),
                    metric_attributes,
                )

    def target_bound(
        self,
        *,
        request: OperationExecutionRequest,
        target: AssetValidationTarget,
    ) -> None:
        span = trace.get_current_span()
        span.set_attributes(
            {
                "commercevision.asset.id": target.asset.id,
                "commercevision.asset.kind": target.asset.kind.value,
                "commercevision.validation.policy_version": (
                    target.asset_version.validation_policy_version
                ),
            }
        )
        quarantine_age_seconds = max(
            0.0,
            (self._clock() - target.asset.created_at).total_seconds(),
        )
        self._quarantine_age.record(
            quarantine_age_seconds,
            {"asset_kind": target.asset.kind.value},
        )
        self._phase2.record_quarantine(
            age_seconds=quarantine_age_seconds,
            state="bound",
        )
        self._logger.info(
            "asset_validation_target_bound",
            **self._operation_log_fields(request=request, mode=None),
            asset_id=target.asset.id,
            asset_kind=target.asset.kind.value,
            validation_policy_version=target.asset_version.validation_policy_version,
            quarantine_age_seconds=round(quarantine_age_seconds, 3),
        )

    def result(
        self,
        *,
        result: AssetValidationResult,
        reused: bool,
    ) -> None:
        reason_code = result.reason_code or "NONE"
        self._stage_results.add(
            1,
            {
                "stage": result.stage.value,
                "verdict": result.verdict.value,
                "reason_code": reason_code,
                "validator": result.validator_name,
                "reused": reused,
            },
        )
        self._phase2.record_validation(
            stage=result.stage.value,
            verdict=result.verdict.value,
            reused=reused,
        )
        self._logger.info(
            "asset_validation_stage_result",
            operation_id=result.operation_id,
            workspace_id=result.workspace_id,
            asset_version_id=result.asset_version_id,
            validation_result_id=result.id,
            attempt_number=result.attempt_number,
            stage=result.stage.value,
            verdict=result.verdict.value,
            reason_code=reason_code,
            validator_name=result.validator_name,
            validator_version=result.validator_version,
            validation_policy_version=result.policy_version,
            reused=reused,
            **self._trace_fields(),
        )

    def completed(
        self,
        *,
        request: OperationExecutionRequest,
        target: AssetValidationTarget,
        outcome: AssetValidationCompletion,
    ) -> None:
        self._completions.add(
            1,
            {
                "asset_kind": target.asset.kind.value,
                "outcome": outcome,
            },
        )
        self._logger.info(
            "asset_validation_lifecycle_completed",
            **self._operation_log_fields(request=request, mode=None),
            asset_id=target.asset.id,
            asset_kind=target.asset.kind.value,
            outcome=outcome,
        )

    def _operation_log_fields(
        self,
        *,
        request: OperationExecutionRequest,
        mode: AssetValidationMode | None,
    ) -> dict[str, object]:
        values: dict[str, object] = {
            "operation_id": request.operation_id,
            "workspace_id": request.workspace_id,
            "asset_version_id": request.target_id,
            "attempt_number": request.attempt_count,
            **self._trace_fields(),
        }
        if mode is not None:
            values["mode"] = mode
        return values

    @staticmethod
    def _trace_fields() -> dict[str, str]:
        context = trace.get_current_span().get_span_context()
        if not context.is_valid:
            return {}
        return {
            "trace_id": f"{context.trace_id:032x}",
            "span_id": f"{context.span_id:016x}",
        }
