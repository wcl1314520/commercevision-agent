"""Sanitized observability boundary for ProductBrief analysis and confirmation."""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from typing import Literal, Protocol

ProductBriefPersistencePhase = Literal["model_result", "provider_failure"]
ProductBriefConfirmationResult = Literal["confirmed", "failed"]


class ProductBriefObserver(Protocol):
    """Observe normalized ProductBrief facts without raw provider or object data."""

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
    ) -> AbstractContextManager[None]: ...

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
    ) -> None: ...

    def persistence(
        self,
        *,
        operation_id: str,
        workspace_id: str,
        product_brief_id: str,
        phase: ProductBriefPersistencePhase,
    ) -> AbstractContextManager[None]: ...

    def confirmation(
        self,
        *,
        trace_id: str,
        workspace_id: str,
        product_brief_id: str,
        product_brief_version_id: str,
    ) -> AbstractContextManager[None]: ...

    def confirmation_result(
        self,
        *,
        workspace_id: str,
        product_brief_id: str,
        product_brief_version_id: str,
        result: ProductBriefConfirmationResult,
    ) -> None: ...


class NullProductBriefObserver:
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
    ) -> AbstractContextManager[None]:
        del (
            operation_id,
            operation_attempt,
            workspace_id,
            product_brief_id,
            provider,
            endpoint_region,
            requested_model,
        )
        return nullcontext()

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
        del (
            operation_id,
            operation_attempt,
            workspace_id,
            product_brief_id,
            provider,
            requested_model,
            status,
            latency_ms,
            error_category,
            retryable,
            provider_request_id,
        )

    def persistence(
        self,
        *,
        operation_id: str,
        workspace_id: str,
        product_brief_id: str,
        phase: ProductBriefPersistencePhase,
    ) -> AbstractContextManager[None]:
        del operation_id, workspace_id, product_brief_id, phase
        return nullcontext()

    def confirmation(
        self,
        *,
        trace_id: str,
        workspace_id: str,
        product_brief_id: str,
        product_brief_version_id: str,
    ) -> AbstractContextManager[None]:
        del trace_id, workspace_id, product_brief_id, product_brief_version_id
        return nullcontext()

    def confirmation_result(
        self,
        *,
        workspace_id: str,
        product_brief_id: str,
        product_brief_version_id: str,
        result: ProductBriefConfirmationResult,
    ) -> None:
        del workspace_id, product_brief_id, product_brief_version_id, result
