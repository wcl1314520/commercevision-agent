"""Deterministic image Provider Adapter used by public contract tests."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from commercevision_contracts.image_provider import (
    ImageEditingProviderRequest,
    ImageGenerationProviderRequest,
    ImageProviderCallOutcome,
    ImageProviderCancelRequest,
    ImageProviderError,
    ImageProviderErrorCategory,
    ImageProviderMediaType,
    ImageProviderOutputFormat,
    ImageProviderQueryRequest,
    ImageProviderRequestIdentity,
    ImageProviderResult,
    ImageProviderSubmitRequest,
    ImageProviderTaskState,
    ImageProviderUsage,
    ImageProviderUsageUnit,
    NormalizedImageProviderOutcome,
)


class DeterministicImageProviderScenario(StrEnum):
    SUCCESS = "SUCCESS"
    CONFIRMED_FAILURE = "CONFIRMED_FAILURE"
    CONTENT_REJECTED = "CONTENT_REJECTED"
    SAFE_TO_RETRY_PRE_DISPATCH = "SAFE_TO_RETRY_PRE_DISPATCH"
    UNKNOWN_AFTER_POSSIBLE_DISPATCH = "UNKNOWN_AFTER_POSSIBLE_DISPATCH"
    UNKNOWN_WITHOUT_IDENTITY = "UNKNOWN_WITHOUT_IDENTITY"
    ASYNC_PENDING = "ASYNC_PENDING"
    ASYNC_SUCCESS = "ASYNC_SUCCESS"


_MEDIA_TYPES = {
    ImageProviderOutputFormat.PNG: ImageProviderMediaType.PNG,
    ImageProviderOutputFormat.JPEG: ImageProviderMediaType.JPEG,
    ImageProviderOutputFormat.WEBP: ImageProviderMediaType.WEBP,
}
_RESULT_BYTES = b"deterministic-image-bytes"


@dataclass(slots=True)
class _DeterministicTask:
    identity: ImageProviderRequestIdentity
    request: ImageProviderSubmitRequest
    state: ImageProviderTaskState = ImageProviderTaskState.PENDING


class DeterministicImageProviderAdapter:
    """Pure, reproducible Adapter fixture with no external I/O or credentials."""

    def __init__(
        self,
        *,
        scenario: DeterministicImageProviderScenario,
        clock: Callable[[], datetime],
    ) -> None:
        self._scenario = DeterministicImageProviderScenario(scenario)
        self._clock = clock
        self._lock = threading.Lock()
        self._tasks: dict[str, _DeterministicTask] = {}

    def submit(self, request: ImageProviderSubmitRequest) -> NormalizedImageProviderOutcome:
        if not isinstance(request, ImageGenerationProviderRequest | ImageEditingProviderRequest):
            raise TypeError("deterministic image Adapter request is invalid")
        if self._clock() >= request.deadline:
            return self._pre_dispatch("REQUEST_DEADLINE_EXPIRED")

        digest = hashlib.sha256(request.provider_idempotency_key.encode("ascii")).hexdigest()
        is_async = self._scenario in {
            DeterministicImageProviderScenario.ASYNC_PENDING,
            DeterministicImageProviderScenario.ASYNC_SUCCESS,
        }
        identity = ImageProviderRequestIdentity(
            provider_request_id=f"det-request-{digest[:24]}",
            provider_task_id=f"det-task-{digest[:24]}" if is_async else None,
        )
        if is_async:
            assert identity.provider_task_id is not None
            with self._lock:
                task = self._tasks.setdefault(
                    identity.provider_task_id,
                    _DeterministicTask(identity=identity, request=request),
                )
                if task.state is ImageProviderTaskState.SUCCEEDED:
                    return self._result_success(task.identity, task.request)
                return self._non_result_success(task.identity, task.state)
        if self._scenario is DeterministicImageProviderScenario.SUCCESS:
            return self._result_success(identity, request)
        if self._scenario is DeterministicImageProviderScenario.CONFIRMED_FAILURE:
            return NormalizedImageProviderOutcome(
                call_outcome=ImageProviderCallOutcome.CONFIRMED_FAILURE,
                task_state=ImageProviderTaskState.FAILED,
                identity=identity,
                result=None,
                usage=None,
                error=ImageProviderError(
                    category=ImageProviderErrorCategory.PROVIDER_UNAVAILABLE,
                    code="FIXTURE_CONFIRMED_FAILURE",
                    retry_after_seconds=None,
                ),
                latency_ms=0,
            )
        if self._scenario is DeterministicImageProviderScenario.CONTENT_REJECTED:
            return NormalizedImageProviderOutcome(
                call_outcome=ImageProviderCallOutcome.CONTENT_REJECTED,
                task_state=ImageProviderTaskState.REJECTED,
                identity=identity,
                result=None,
                usage=None,
                error=ImageProviderError(
                    category=ImageProviderErrorCategory.CONTENT_POLICY,
                    code="FIXTURE_CONTENT_REJECTED",
                    retry_after_seconds=None,
                ),
                latency_ms=0,
            )
        if self._scenario is DeterministicImageProviderScenario.SAFE_TO_RETRY_PRE_DISPATCH:
            return self._pre_dispatch("FIXTURE_PRE_DISPATCH")
        return NormalizedImageProviderOutcome(
            call_outcome=ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH,
            task_state=None,
            identity=(
                None
                if self._scenario is DeterministicImageProviderScenario.UNKNOWN_WITHOUT_IDENTITY
                else identity
            ),
            result=None,
            usage=None,
            error=ImageProviderError(
                category=ImageProviderErrorCategory.TIMEOUT,
                code="FIXTURE_UNKNOWN",
                retry_after_seconds=None,
            ),
            latency_ms=0,
        )

    def query(self, request: ImageProviderQueryRequest) -> NormalizedImageProviderOutcome:
        if not isinstance(request, ImageProviderQueryRequest):
            raise TypeError("deterministic image Adapter query is invalid")
        if self._clock() >= request.deadline:
            return self._pre_dispatch("QUERY_DEADLINE_EXPIRED")
        task_id = request.identity.provider_task_id
        if task_id is None:
            return self._not_found(request.identity)
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.identity != request.identity:
                return self._not_found(request.identity)
            if task.state is ImageProviderTaskState.CANCELLED:
                return self._non_result_success(task.identity, ImageProviderTaskState.CANCELLED)
            if task.state is ImageProviderTaskState.SUCCEEDED:
                return self._result_success(task.identity, task.request)
            if self._scenario is DeterministicImageProviderScenario.ASYNC_SUCCESS:
                task.state = ImageProviderTaskState.SUCCEEDED
                return self._result_success(task.identity, task.request)
            return self._non_result_success(task.identity, ImageProviderTaskState.PENDING)

    def cancel(self, request: ImageProviderCancelRequest) -> NormalizedImageProviderOutcome:
        if not isinstance(request, ImageProviderCancelRequest):
            raise TypeError("deterministic image Adapter cancellation is invalid")
        if self._clock() >= request.deadline:
            return self._pre_dispatch("CANCEL_DEADLINE_EXPIRED")
        task_id = request.identity.provider_task_id
        if task_id is None:
            return self._not_found(request.identity)
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.identity != request.identity:
                return self._not_found(request.identity)
            if task.state is ImageProviderTaskState.SUCCEEDED:
                return self._result_success(task.identity, task.request)
            task.state = ImageProviderTaskState.CANCELLED
            return self._non_result_success(task.identity, ImageProviderTaskState.CANCELLED)

    @staticmethod
    def _non_result_success(
        identity: ImageProviderRequestIdentity,
        task_state: ImageProviderTaskState,
    ) -> NormalizedImageProviderOutcome:
        return NormalizedImageProviderOutcome(
            call_outcome=ImageProviderCallOutcome.CONFIRMED_SUCCESS,
            task_state=task_state,
            identity=identity,
            result=None,
            usage=None,
            error=None,
            latency_ms=0,
        )

    @staticmethod
    def _result_success(
        identity: ImageProviderRequestIdentity,
        request: ImageProviderSubmitRequest,
    ) -> NormalizedImageProviderOutcome:
        digest = hashlib.sha256(request.provider_idempotency_key.encode("ascii")).hexdigest()
        return NormalizedImageProviderOutcome(
            call_outcome=ImageProviderCallOutcome.CONFIRMED_SUCCESS,
            task_state=ImageProviderTaskState.SUCCEEDED,
            identity=identity,
            result=ImageProviderResult(
                provider_result_id=f"det-result-{digest[:24]}",
                content=_RESULT_BYTES,
                content_sha256=hashlib.sha256(_RESULT_BYTES).hexdigest(),
                media_type=_MEDIA_TYPES[request.media.output_format],
                width=request.media.width,
                height=request.media.height,
            ),
            usage=ImageProviderUsage(
                unit=ImageProviderUsageUnit.IMAGE,
                quantity=Decimal("1.000000"),
                evidence_sha256=hashlib.sha256(f"det-usage:{digest}".encode("ascii")).hexdigest(),
            ),
            error=None,
            latency_ms=0,
        )

    @staticmethod
    def _not_found(
        identity: ImageProviderRequestIdentity,
    ) -> NormalizedImageProviderOutcome:
        return NormalizedImageProviderOutcome(
            call_outcome=ImageProviderCallOutcome.CONFIRMED_SUCCESS,
            task_state=ImageProviderTaskState.NOT_FOUND,
            identity=identity,
            result=None,
            usage=None,
            error=None,
            latency_ms=0,
        )

    @staticmethod
    def _pre_dispatch(code: str) -> NormalizedImageProviderOutcome:
        return NormalizedImageProviderOutcome(
            call_outcome=ImageProviderCallOutcome.SAFE_TO_RETRY_PRE_DISPATCH,
            task_state=None,
            identity=None,
            result=None,
            usage=None,
            error=ImageProviderError(
                category=ImageProviderErrorCategory.PROVIDER_UNAVAILABLE,
                code=code,
                retry_after_seconds=None,
            ),
            latency_ms=0,
        )
