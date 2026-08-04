"""Bounded-cardinality telemetry for ProductBrief analysis and confirmation."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Literal, Protocol

from opentelemetry import metrics, trace
from opentelemetry.metrics import Meter
from opentelemetry.trace import Status, StatusCode, Tracer

from .logging import get_logger
from .phase2 import Phase2Telemetry


class _StructuredLogger(Protocol):
    def info(self, event: str, **values: object) -> object: ...

    def warning(self, event: str, **values: object) -> object: ...

    def error(self, event: str, **values: object) -> object: ...


def _provider_request_id_token(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("provider request ID must be a string")
    return f"sha256:{hashlib.sha256(value.encode()).hexdigest()}"


class ProductBriefTelemetry:
    """Emit ProductBrief signals without prompts, payloads, URLs, or storage facts."""

    def __init__(
        self,
        *,
        logger: _StructuredLogger | None = None,
        tracer: Tracer | None = None,
        meter: Meter | None = None,
        phase2: Phase2Telemetry | None = None,
    ) -> None:
        self._logger = logger or get_logger("commercevision.product_brief")
        self._tracer = tracer or trace.get_tracer("commercevision.product_brief")
        resolved_meter = meter or metrics.get_meter("commercevision.product_brief")
        self._phase2 = phase2 or Phase2Telemetry(
            logger=self._logger,
            tracer=self._tracer,
            meter=resolved_meter,
        )
        self._provider_calls = resolved_meter.create_counter(
            "commercevision.product_brief.provider.calls",
            unit="{call}",
            description="Normalized Vision provider call outcomes",
        )
        self._provider_duration = resolved_meter.create_histogram(
            "commercevision.product_brief.provider.duration",
            unit="ms",
            description="Vision provider call duration",
        )
        self._provider_errors = resolved_meter.create_counter(
            "commercevision.product_brief.provider.errors",
            unit="{error}",
            description="Normalized Vision provider errors",
        )
        self._provider_rate_limits = resolved_meter.create_counter(
            "commercevision.product_brief.provider.rate_limits",
            unit="{limit}",
            description="Vision provider rate-limit responses",
        )
        self._confirmations = resolved_meter.create_counter(
            "commercevision.product_brief.confirmations",
            unit="{request}",
            description="ProductBrief confirmation request outcomes",
        )

    @contextmanager
    def vision_request(
        self,
        *,
        operation_id: str,
        operation_attempt: int,
        workspace_id: str,
        product_brief_id: str,
        provider: str,
        endpoint_region: str,
        requested_model: str,
    ) -> Iterator[None]:
        fields = {
            "operation_id": operation_id,
            "operation_attempt": operation_attempt,
            "workspace_id": workspace_id,
            "product_brief_id": product_brief_id,
            "provider": provider,
            "endpoint_region": endpoint_region,
            "requested_model": requested_model,
        }
        with self._tracer.start_as_current_span(
            "commercevision.product_brief.vision",
            attributes={
                "commercevision.operation.id": operation_id,
                "commercevision.operation.attempt": operation_attempt,
                "commercevision.workspace.id": workspace_id,
                "commercevision.product_brief.id": product_brief_id,
                "commercevision.provider.name": provider,
                "commercevision.provider.endpoint_region": endpoint_region,
                "commercevision.provider.requested_model": requested_model,
            },
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            self._logger.info(
                "product_brief_vision_started",
                **fields,
                **self._trace_fields(),
            )
            try:
                yield
            except Exception as exc:
                error_type = type(exc).__name__
                span.set_attribute("error.type", error_type)
                span.set_status(Status(StatusCode.ERROR, "UNCLASSIFIED"))
                self._logger.error(
                    "product_brief_vision_failed",
                    **fields,
                    error_code="UNCLASSIFIED",
                    error_type=error_type,
                    **self._trace_fields(),
                )
                raise
            else:
                self._logger.info(
                    "product_brief_vision_completed",
                    **fields,
                    **self._trace_fields(),
                )

    def provider_result(
        self,
        *,
        operation_id: str,
        operation_attempt: int,
        workspace_id: str,
        product_brief_id: str,
        provider: str,
        requested_model: str,
        status: str,
        latency_ms: int,
        error_category: str | None,
        retryable: bool | None,
        provider_request_id: str | None,
    ) -> None:
        provider_request_id_token = _provider_request_id_token(provider_request_id)
        metric_attributes = {
            "provider": provider,
            "requested_model": requested_model,
            "status": status,
        }
        self._provider_calls.add(1, metric_attributes)
        self._provider_duration.record(latency_ms, metric_attributes)
        self._phase2.record_provider(
            provider=provider,
            operation="vision",
            outcome=("succeeded" if status == "SUCCEEDED" else status.lower()),
            latency_ms=latency_ms,
        )
        if status != "SUCCEEDED":
            error_attributes = {
                **metric_attributes,
                "error_category": error_category or "unknown",
                "retryable": bool(retryable),
            }
            self._provider_errors.add(1, error_attributes)
            if status == "THROTTLED" or error_category == "rate_limit":
                self._provider_rate_limits.add(1, metric_attributes)

        span = trace.get_current_span()
        span.set_attributes(
            {
                "commercevision.provider.status": status,
                "commercevision.provider.latency_ms": latency_ms,
                "commercevision.provider.error_category": error_category or "none",
                "commercevision.provider.retryable": bool(retryable),
            }
        )
        if provider_request_id_token is not None:
            span.set_attribute(
                "commercevision.provider.request_id",
                provider_request_id_token,
            )
        if status == "SUCCEEDED":
            span.set_status(Status(StatusCode.OK))
        else:
            span.set_status(Status(StatusCode.ERROR, status))

        log = self._logger.info if status == "SUCCEEDED" else self._logger.warning
        log(
            "product_brief_provider_result",
            operation_id=operation_id,
            operation_attempt=operation_attempt,
            workspace_id=workspace_id,
            product_brief_id=product_brief_id,
            provider=provider,
            requested_model=requested_model,
            status=status,
            latency_ms=latency_ms,
            error_category=error_category,
            retryable=retryable,
            provider_request_id=provider_request_id_token,
            **self._trace_fields(),
        )

    @contextmanager
    def persistence(
        self,
        *,
        operation_id: str,
        workspace_id: str,
        product_brief_id: str,
        phase: Literal["model_result", "provider_failure"],
    ) -> Iterator[None]:
        fields = {
            "operation_id": operation_id,
            "workspace_id": workspace_id,
            "product_brief_id": product_brief_id,
            "phase": phase,
        }
        with self._span(
            name="commercevision.product_brief.persistence",
            attributes={
                "commercevision.operation.id": operation_id,
                "commercevision.workspace.id": workspace_id,
                "commercevision.product_brief.id": product_brief_id,
                "commercevision.product_brief.persistence_phase": phase,
            },
            event="product_brief_persistence",
            fields=fields,
        ):
            yield

    @contextmanager
    def confirmation(
        self,
        *,
        trace_id: str,
        workspace_id: str,
        product_brief_id: str,
        product_brief_version_id: str,
    ) -> Iterator[None]:
        fields = {
            "request_trace_id": trace_id,
            "workspace_id": workspace_id,
            "product_brief_id": product_brief_id,
            "product_brief_version_id": product_brief_version_id,
        }
        with self._span(
            name="commercevision.product_brief.confirmation",
            attributes={
                "commercevision.request.trace_id": trace_id,
                "commercevision.workspace.id": workspace_id,
                "commercevision.product_brief.id": product_brief_id,
                "commercevision.product_brief.version_id": product_brief_version_id,
            },
            event="product_brief_confirmation",
            fields=fields,
        ):
            yield

    def confirmation_result(
        self,
        *,
        workspace_id: str,
        product_brief_id: str,
        product_brief_version_id: str,
        result: Literal["confirmed", "failed"],
    ) -> None:
        self._confirmations.add(1, {"result": result})
        self._phase2.record_confirmation(outcome=result)
        span = trace.get_current_span()
        span.set_attribute("commercevision.confirmation.result", result)
        if result == "confirmed":
            span.set_status(Status(StatusCode.OK))
            event = "product_brief_confirmed"
            log = self._logger.info
        else:
            span.set_status(Status(StatusCode.ERROR, result))
            event = "product_brief_confirmation_failed"
            log = self._logger.warning
        log(
            event,
            workspace_id=workspace_id,
            product_brief_id=product_brief_id,
            product_brief_version_id=product_brief_version_id,
            result=result,
            **self._trace_fields(),
        )

    @contextmanager
    def _span(
        self,
        *,
        name: str,
        attributes: Mapping[str, object],
        event: str,
        fields: Mapping[str, object],
    ) -> Iterator[None]:
        with self._tracer.start_as_current_span(
            name,
            attributes=attributes,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            self._logger.info(f"{event}_started", **fields, **self._trace_fields())
            try:
                yield
            except Exception as exc:
                error_type = type(exc).__name__
                span.set_attribute("error.type", error_type)
                span.set_status(Status(StatusCode.ERROR, error_type))
                self._logger.error(
                    f"{event}_failed",
                    **fields,
                    error_code="UNCLASSIFIED",
                    error_type=error_type,
                    **self._trace_fields(),
                )
                raise
            else:
                span.set_status(Status(StatusCode.OK))
                self._logger.info(
                    f"{event}_completed",
                    **fields,
                    **self._trace_fields(),
                )

    @staticmethod
    def _trace_fields() -> dict[str, str]:
        context = trace.get_current_span().get_span_context()
        if not context.is_valid:
            return {}
        return {
            "trace_id": f"{context.trace_id:032x}",
            "span_id": f"{context.span_id:016x}",
        }
