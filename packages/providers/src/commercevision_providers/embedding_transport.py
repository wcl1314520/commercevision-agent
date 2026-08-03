"""Alibaba IMAGE embedding transport built on the bounded Vision HTTP runtime."""

from __future__ import annotations

import asyncio
import hashlib
import re
import threading
import time
from concurrent.futures import CancelledError
from contextvars import ContextVar
from dataclasses import dataclass

import httpx

from .vision_credentials import VisionApiKeyUnavailableError
from .vision_transport import (
    AsyncVisionHttpTransport,
    VisionCredentialUnavailableTransportError,
    VisionHttpResponseEvidence,
    VisionSafeToRetryTransportError,
    VisionSubmissionOutcomeUnknownError,
)


class EmbeddingPreSubmissionTimeoutTransportError(VisionSafeToRetryTransportError):
    """The request timed out before the provider could accept it."""


class EmbeddingPreDispatchCancelledTransportError(VisionSafeToRetryTransportError):
    """The call was cancelled while still waiting for bounded capacity."""


class EmbeddingHeadersObservedOutcomeUnknownError(VisionSubmissionOutcomeUnknownError):
    """The call was cancelled after response headers became observable."""

    def __init__(self, provider_request_id: str | None) -> None:
        super().__init__("Embedding response completion is unknown")
        self.provider_request_id = provider_request_id


@dataclass(slots=True)
class _EmbeddingCallState:
    dispatched: bool = False
    headers_observed: bool = False
    provider_request_id: str | None = None


_CALL_STATE: ContextVar[_EmbeddingCallState | None] = ContextVar(
    "embedding_http_call_state",
    default=None,
)


class AsyncEmbeddingHttpTransport(AsyncVisionHttpTransport):
    """Reuse bounded concurrency/cancellation with the DashScope embedding path."""

    def send(
        self,
        request_bytes: bytes,
        *,
        deadline_at: float,
    ) -> VisionHttpResponseEvidence:
        completed = threading.Event()
        state = _EmbeddingCallState()
        with self._state_lock:
            self._assert_healthy_locked()
            future = asyncio.run_coroutine_threadsafe(
                self._send_with_embedding_state(
                    request_bytes,
                    deadline_at=deadline_at,
                    completed=completed,
                    state=state,
                ),
                self._loop,
            )

        interrupted = False
        guard_timeout = max(0.0, deadline_at - time.monotonic()) + 0.5
        try:
            return future.result(timeout=guard_timeout)
        except TimeoutError:
            future.cancel()
            self._wait_for_cancellation(completed)
            interrupted = True
        except CancelledError:
            self._wait_for_cancellation(completed)
            interrupted = True
        except VisionSubmissionOutcomeUnknownError:
            interrupted = True

        assert interrupted
        if not state.dispatched:
            raise EmbeddingPreDispatchCancelledTransportError(
                "Embedding call ended before dispatch"
            ) from None
        if state.headers_observed:
            raise EmbeddingHeadersObservedOutcomeUnknownError(state.provider_request_id) from None
        raise VisionSubmissionOutcomeUnknownError(
            "Embedding submission outcome is unknown"
        ) from None

    async def _send_with_embedding_state(
        self,
        request_bytes: bytes,
        *,
        deadline_at: float,
        completed: threading.Event,
        state: _EmbeddingCallState,
    ) -> VisionHttpResponseEvidence:
        token = _CALL_STATE.set(state)
        try:
            return await super()._send_with_completion(
                request_bytes,
                deadline_at=deadline_at,
                completed=completed,
            )
        finally:
            _CALL_STATE.reset(token)

    async def _post_bounded(self, request_bytes: bytes) -> VisionHttpResponseEvidence:
        assert self._client is not None
        credential_unavailable = False
        try:
            api_key = self._credential_provider.resolve()
        except VisionApiKeyUnavailableError:
            credential_unavailable = True
            api_key = ""
        if credential_unavailable:
            raise VisionCredentialUnavailableTransportError(
                "Embedding credential is unavailable before submission"
            ) from None
        request = self._client.build_request(
            "POST",
            (f"{self._endpoint}/services/embeddings/multimodal-embedding/multimodal-embedding"),
            content=request_bytes,
            headers={
                "Accept-Encoding": "identity",
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(
                connect=self._connect_timeout,
                read=self._read_timeout,
                write=self._read_timeout,
                pool=self._connect_timeout,
            ),
        )
        response_bytes = bytearray()
        response_too_large = False
        completion_uncertain = False
        state = _CALL_STATE.get()
        assert state is not None
        state.dispatched = True
        transport_error: Exception | None = None
        try:
            response = await self._client.send(request, stream=True)
        except (httpx.ConnectTimeout, httpx.PoolTimeout):
            transport_error = EmbeddingPreSubmissionTimeoutTransportError(
                "Embedding HTTP timed out before submission"
            )
        except httpx.ConnectError:
            transport_error = VisionSafeToRetryTransportError(
                "Embedding HTTP connection failed before submission"
            )
        except httpx.HTTPError:
            transport_error = VisionSubmissionOutcomeUnknownError(
                "Embedding HTTP transport failed after dispatch"
            )
        if transport_error is not None:
            raise transport_error from None
        state.headers_observed = True
        state.provider_request_id = self._request_id_from_headers(response)
        try:
            content_encoding = response.headers.get("Content-Encoding")
            if (
                content_encoding is not None and content_encoding.strip().lower() != "identity"
            ) or not isinstance(response.stream, httpx.AsyncByteStream):
                completion_uncertain = True
            else:
                try:
                    stream = response.stream.__aiter__()
                    while True:
                        try:
                            chunk = await self._next_response_chunk(stream)
                        except StopAsyncIteration:
                            break
                        remaining = self._maximum_response_bytes + 1 - len(response_bytes)
                        if remaining > 0:
                            response_bytes.extend(chunk[:remaining])
                        if len(response_bytes) > self._maximum_response_bytes:
                            response_too_large = True
                            break
                except Exception:
                    completion_uncertain = True
        finally:
            if not await self._close_response_bounded(response):
                completion_uncertain = True
        bounded_response = httpx.Response(
            response.status_code,
            headers=response.headers,
            content=bytes(response_bytes),
            request=request,
        )
        return VisionHttpResponseEvidence(
            response=bounded_response,
            body_too_large=response_too_large,
            completion_uncertain=completion_uncertain,
        )

    @staticmethod
    def _request_id_from_headers(response: httpx.Response) -> str | None:
        for name in ("x-request-id", "x-dashscope-request-id"):
            value = response.headers.get(name)
            if not value:
                continue
            normalized = value.strip()
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", normalized):
                return normalized
            return f"sha256:{hashlib.sha256(normalized.encode()).hexdigest()}"
        return None
