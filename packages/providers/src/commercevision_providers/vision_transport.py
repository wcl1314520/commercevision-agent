"""Cancellable bounded HTTP transport for synchronous Vision adapters."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import AsyncIterator
from concurrent.futures import CancelledError
from dataclasses import dataclass

import httpx

from .vision_credentials import (
    VisionApiKeyProvider,
    VisionApiKeyUnavailableError,
)


class VisionSafeToRetryTransportError(RuntimeError):
    """The request was not submitted to the provider."""


class VisionSubmissionOutcomeUnknownError(RuntimeError):
    """The provider may have accepted the request."""


class VisionTransportClosedError(VisionSafeToRetryTransportError):
    """The owning worker closed the transport before a call was submitted."""


class VisionCredentialUnavailableTransportError(VisionSafeToRetryTransportError):
    """Credential resolution failed before a call was submitted."""


class VisionTransportUnhealthyError(VisionSafeToRetryTransportError):
    """A prior request left transport or connection cleanup unverified."""


class _VisionResponseReadTimedOut(TimeoutError):
    pass


class _VisionTransportCleanupError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class VisionHttpResponseEvidence:
    """Bounded bytes and HTTP facts observed after response headers arrived."""

    response: httpx.Response
    body_too_large: bool
    completion_uncertain: bool


class AsyncVisionHttpTransport:
    """Own one async HTTP runtime while exposing a bounded synchronous call."""

    def __init__(
        self,
        *,
        credential_provider: VisionApiKeyProvider,
        endpoint: str,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        maximum_concurrency: int,
        maximum_response_bytes: int,
        request_path: str = "/chat/completions",
        request_headers: dict[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._credential_provider = credential_provider
        self._endpoint = endpoint
        self._connect_timeout = connect_timeout_seconds
        self._read_timeout = read_timeout_seconds
        self._cleanup_timeout = min(read_timeout_seconds, 0.5)
        self._maximum_concurrency = maximum_concurrency
        self._maximum_response_bytes = maximum_response_bytes
        if not request_path.startswith("/") or request_path.startswith("//"):
            raise ValueError("Vision HTTP request path must be absolute")
        self._request_path = request_path
        controlled_headers = request_headers or {}
        protected_headers = {"accept-encoding", "authorization", "content-type", "host"}
        if any(name.lower() in protected_headers for name in controlled_headers):
            raise ValueError("Vision HTTP request headers cannot override protected headers")
        try:
            self._request_headers = dict(httpx.Headers(controlled_headers))
        except (TypeError, ValueError) as exc:
            raise ValueError("Vision HTTP request headers are invalid") from exc
        self._provided_client = client
        self._client: httpx.AsyncClient | None = None
        self._client_close_task: asyncio.Task[None] | None = None
        self._retirement_task: asyncio.Task[None] | None = None
        self._retirement_requested = False
        self._capacity: asyncio.Semaphore | None = None
        self._background_tasks: set[asyncio.Task[object]] = set()
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._state_lock = threading.Lock()
        self._closed = False
        self._failure: BaseException | None = None
        self._startup_error: BaseException | None = None
        self._thread = threading.Thread(
            target=self._run,
            name="alibaba-product-brief-vision-http",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(5):
            raise RuntimeError("Vision HTTP runtime did not start")
        if self._startup_error is not None:
            raise RuntimeError("Vision HTTP runtime failed to start") from self._startup_error

    def send(
        self,
        request_bytes: bytes,
        *,
        deadline_at: float,
    ) -> VisionHttpResponseEvidence:
        completed = threading.Event()
        with self._state_lock:
            self._assert_healthy_locked()
            future = asyncio.run_coroutine_threadsafe(
                self._send_with_completion(
                    request_bytes,
                    deadline_at=deadline_at,
                    completed=completed,
                ),
                self._loop,
            )

        guard_timeout = max(0.0, deadline_at - time.monotonic()) + 0.5
        try:
            return future.result(timeout=guard_timeout)
        except TimeoutError:
            future.cancel()
            self._wait_for_cancellation(completed)
            raise VisionSubmissionOutcomeUnknownError(
                "Vision HTTP completion was interrupted after dispatch"
            ) from None
        except CancelledError as exc:
            self._wait_for_cancellation(completed)
            raise VisionSubmissionOutcomeUnknownError(
                "Vision HTTP request was cancelled after dispatch"
            ) from exc

    def fetch(
        self,
        url: str,
        *,
        deadline_at: float,
        maximum_response_bytes: int,
    ) -> VisionHttpResponseEvidence:
        completed = threading.Event()
        with self._state_lock:
            self._assert_healthy_locked()
            future = asyncio.run_coroutine_threadsafe(
                self._fetch_with_completion(
                    url,
                    deadline_at=deadline_at,
                    maximum_response_bytes=maximum_response_bytes,
                    completed=completed,
                ),
                self._loop,
            )

        guard_timeout = max(0.0, deadline_at - time.monotonic()) + 0.5
        try:
            return future.result(timeout=guard_timeout)
        except TimeoutError:
            future.cancel()
            self._wait_for_cancellation(completed)
            raise VisionSubmissionOutcomeUnknownError(
                "Vision HTTP result fetch was interrupted"
            ) from None
        except CancelledError as exc:
            self._wait_for_cancellation(completed)
            raise VisionSubmissionOutcomeUnknownError(
                "Vision HTTP result fetch was cancelled"
            ) from exc

    def send_get(
        self,
        request_path: str,
        *,
        deadline_at: float,
    ) -> VisionHttpResponseEvidence:
        if not request_path.startswith("/") or request_path.startswith("//"):
            raise ValueError("Vision HTTP GET path must be absolute")
        completed = threading.Event()
        with self._state_lock:
            self._assert_healthy_locked()
            future = asyncio.run_coroutine_threadsafe(
                self._get_with_completion(
                    request_path,
                    deadline_at=deadline_at,
                    completed=completed,
                ),
                self._loop,
            )

        guard_timeout = max(0.0, deadline_at - time.monotonic()) + 0.5
        try:
            return future.result(timeout=guard_timeout)
        except TimeoutError:
            future.cancel()
            self._wait_for_cancellation(completed)
            raise VisionSubmissionOutcomeUnknownError(
                "Vision HTTP GET completion was interrupted"
            ) from None
        except CancelledError as exc:
            self._wait_for_cancellation(completed)
            raise VisionSubmissionOutcomeUnknownError(
                "Vision HTTP GET request was cancelled"
            ) from exc

    def assert_ready(self) -> str:
        with self._state_lock:
            self._assert_healthy_locked()
        return "ok"

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            future = asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
        try:
            future.result(timeout=5)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5)
        if self._thread.is_alive():
            raise RuntimeError("Vision HTTP runtime did not stop")

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._capacity = asyncio.Semaphore(self._maximum_concurrency)
            self._client = self._provided_client or httpx.AsyncClient()
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()
            self._loop.close()
            return
        self._ready.set()
        self._loop.run_forever()
        self._loop.close()

    async def _send(
        self,
        request_bytes: bytes,
        *,
        deadline_at: float,
    ) -> VisionHttpResponseEvidence:
        remaining = deadline_at - time.monotonic()
        if remaining <= 0:
            raise VisionSafeToRetryTransportError("Vision HTTP deadline expired before dispatch")
        assert self._capacity is not None
        try:
            async with asyncio.timeout(remaining):
                await self._capacity.acquire()
        except TimeoutError as exc:
            raise VisionSafeToRetryTransportError(
                "Vision HTTP capacity expired before dispatch"
            ) from exc
        try:
            remaining = deadline_at - time.monotonic()
            if remaining <= 0:
                raise VisionSafeToRetryTransportError(
                    "Vision HTTP deadline expired before dispatch"
                )
            self._assert_dispatch_allowed()
            try:
                async with asyncio.timeout(remaining):
                    return await self._post_bounded(request_bytes)
            except VisionSafeToRetryTransportError:
                raise
            except VisionSubmissionOutcomeUnknownError:
                raise
            except TimeoutError as exc:
                raise VisionSubmissionOutcomeUnknownError(
                    "Vision HTTP deadline expired after dispatch"
                ) from exc
        finally:
            self._capacity.release()

    async def _send_with_completion(
        self,
        request_bytes: bytes,
        *,
        deadline_at: float,
        completed: threading.Event,
    ) -> VisionHttpResponseEvidence:
        try:
            return await self._send(request_bytes, deadline_at=deadline_at)
        finally:
            completed.set()

    async def _fetch_with_completion(
        self,
        url: str,
        *,
        deadline_at: float,
        maximum_response_bytes: int,
        completed: threading.Event,
    ) -> VisionHttpResponseEvidence:
        try:
            return await self._fetch(
                url,
                deadline_at=deadline_at,
                maximum_response_bytes=maximum_response_bytes,
            )
        finally:
            completed.set()

    async def _get_with_completion(
        self,
        request_path: str,
        *,
        deadline_at: float,
        completed: threading.Event,
    ) -> VisionHttpResponseEvidence:
        try:
            return await self._get(
                request_path,
                deadline_at=deadline_at,
            )
        finally:
            completed.set()

    async def _get(
        self,
        request_path: str,
        *,
        deadline_at: float,
    ) -> VisionHttpResponseEvidence:
        remaining = deadline_at - time.monotonic()
        if remaining <= 0:
            raise VisionSafeToRetryTransportError("Vision HTTP GET deadline expired")
        assert self._capacity is not None
        try:
            async with asyncio.timeout(remaining):
                await self._capacity.acquire()
        except TimeoutError as exc:
            raise VisionSafeToRetryTransportError("Vision HTTP GET capacity expired") from exc
        try:
            remaining = deadline_at - time.monotonic()
            if remaining <= 0:
                raise VisionSafeToRetryTransportError("Vision HTTP GET deadline expired")
            self._assert_dispatch_allowed()
            try:
                async with asyncio.timeout(remaining):
                    return await self._get_authenticated_bounded(request_path)
            except (VisionSafeToRetryTransportError, VisionSubmissionOutcomeUnknownError):
                raise
            except TimeoutError as exc:
                raise VisionSubmissionOutcomeUnknownError(
                    "Vision HTTP GET deadline expired after dispatch"
                ) from exc
        finally:
            self._capacity.release()

    async def _fetch(
        self,
        url: str,
        *,
        deadline_at: float,
        maximum_response_bytes: int,
    ) -> VisionHttpResponseEvidence:
        remaining = deadline_at - time.monotonic()
        if remaining <= 0:
            raise VisionSafeToRetryTransportError(
                "Vision HTTP result deadline expired before fetch"
            )
        assert self._capacity is not None
        try:
            async with asyncio.timeout(remaining):
                await self._capacity.acquire()
        except TimeoutError as exc:
            raise VisionSafeToRetryTransportError(
                "Vision HTTP result capacity expired before fetch"
            ) from exc
        try:
            remaining = deadline_at - time.monotonic()
            if remaining <= 0:
                raise VisionSafeToRetryTransportError(
                    "Vision HTTP result deadline expired before fetch"
                )
            self._assert_dispatch_allowed()
            try:
                async with asyncio.timeout(remaining):
                    return await self._get_bounded(
                        url,
                        maximum_response_bytes=maximum_response_bytes,
                    )
            except (VisionSafeToRetryTransportError, VisionSubmissionOutcomeUnknownError):
                raise
            except TimeoutError as exc:
                raise VisionSubmissionOutcomeUnknownError(
                    "Vision HTTP result deadline expired during fetch"
                ) from exc
        finally:
            self._capacity.release()

    async def _post_bounded(self, request_bytes: bytes) -> VisionHttpResponseEvidence:
        assert self._client is not None
        try:
            api_key = self._credential_provider.resolve()
        except VisionApiKeyUnavailableError as exc:
            raise VisionCredentialUnavailableTransportError(
                "Vision credential is unavailable before submission"
            ) from exc
        request = self._client.build_request(
            "POST",
            f"{self._endpoint}{self._request_path}",
            content=request_bytes,
            headers={
                "Accept-Encoding": "identity",
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                **self._request_headers,
            },
            timeout=httpx.Timeout(
                connect=self._connect_timeout,
                read=self._read_timeout,
                write=self._read_timeout,
                pool=self._connect_timeout,
            ),
        )
        return await self._request_bounded(
            request,
            maximum_response_bytes=self._maximum_response_bytes,
        )

    async def _get_bounded(
        self,
        url: str,
        *,
        maximum_response_bytes: int,
    ) -> VisionHttpResponseEvidence:
        assert self._client is not None
        request = self._client.build_request(
            "GET",
            url,
            headers={"Accept-Encoding": "identity"},
            timeout=httpx.Timeout(
                connect=self._connect_timeout,
                read=self._read_timeout,
                write=self._read_timeout,
                pool=self._connect_timeout,
            ),
        )
        return await self._request_bounded(
            request,
            maximum_response_bytes=maximum_response_bytes,
        )

    async def _get_authenticated_bounded(
        self,
        request_path: str,
    ) -> VisionHttpResponseEvidence:
        assert self._client is not None
        try:
            api_key = self._credential_provider.resolve()
        except VisionApiKeyUnavailableError as exc:
            raise VisionCredentialUnavailableTransportError(
                "Vision credential is unavailable before GET"
            ) from exc
        request = self._client.build_request(
            "GET",
            f"{self._endpoint}{request_path}",
            headers={
                "Accept-Encoding": "identity",
                "Authorization": f"Bearer {api_key}",
            },
            timeout=httpx.Timeout(
                connect=self._connect_timeout,
                read=self._read_timeout,
                write=self._read_timeout,
                pool=self._connect_timeout,
            ),
        )
        return await self._request_bounded(
            request,
            maximum_response_bytes=self._maximum_response_bytes,
        )

    async def _request_bounded(
        self,
        request: httpx.Request,
        *,
        maximum_response_bytes: int,
    ) -> VisionHttpResponseEvidence:
        assert self._client is not None
        response_bytes = bytearray()
        response_too_large = False
        completion_uncertain = False
        try:
            response = await self._client.send(request, stream=True)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise VisionSafeToRetryTransportError(
                "Vision HTTP connection failed before submission"
            ) from exc
        except httpx.HTTPError as exc:
            raise VisionSubmissionOutcomeUnknownError(
                "Vision HTTP transport failed after dispatch"
            ) from exc
        try:
            content_encoding = response.headers.get("Content-Encoding")
            if content_encoding is not None and content_encoding.strip().lower() != "identity":
                completion_uncertain = True
            else:
                try:
                    if not isinstance(response.stream, httpx.AsyncByteStream):
                        raise TypeError("Vision HTTP response stream is not asynchronous")
                    stream = response.stream.__aiter__()
                    while True:
                        try:
                            chunk = await self._next_response_chunk(stream)
                        except StopAsyncIteration:
                            break
                        remaining = maximum_response_bytes + 1 - len(response_bytes)
                        if remaining > 0:
                            response_bytes.extend(chunk[:remaining])
                        if len(response_bytes) > maximum_response_bytes:
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

    async def _next_response_chunk(self, stream: AsyncIterator[bytes]) -> bytes:
        read_task: asyncio.Task[bytes] = asyncio.create_task(self._read_next(stream))
        self._track_background_task(read_task)
        try:
            done, _ = await asyncio.wait(
                {read_task},
                timeout=self._read_timeout,
            )
        except asyncio.CancelledError:
            await self._cancel_task_bounded(read_task, context="response read")
            raise
        if not done:
            await self._cancel_task_bounded(read_task, context="response read")
            raise _VisionResponseReadTimedOut
        return read_task.result()

    @staticmethod
    async def _read_next(stream: AsyncIterator[bytes]) -> bytes:
        return await anext(stream)

    async def _close_response_bounded(self, response: httpx.Response) -> bool:
        close_task = asyncio.create_task(response.aclose())
        self._track_background_task(close_task)
        try:
            done, _ = await asyncio.wait(
                {close_task},
                timeout=self._cleanup_timeout,
            )
        except asyncio.CancelledError:
            await self._cancel_task_bounded(close_task, context="response close")
            raise
        if not done:
            self._mark_unhealthy(
                _VisionTransportCleanupError(
                    "Vision HTTP response close exceeded its cleanup bound"
                )
            )
            await self._cancel_task_bounded(close_task, context="response close")
            return False
        if close_task.cancelled():
            self._mark_unhealthy(
                _VisionTransportCleanupError(
                    "Vision HTTP response close was cancelled before completion"
                )
            )
            return False
        try:
            close_task.result()
        except Exception:
            self._mark_unhealthy(_VisionTransportCleanupError("Vision HTTP response close failed"))
            return False
        return True

    def _track_background_task(self, task: asyncio.Task[object]) -> None:
        self._background_tasks.add(task)
        task.add_done_callback(self._background_task_done)

    def _background_task_done(self, task: asyncio.Task[object]) -> None:
        self._background_tasks.discard(task)
        if not task.cancelled():
            task.exception()

    async def _cancel_task_bounded(
        self,
        task: asyncio.Task[object],
        *,
        context: str,
    ) -> bool:
        if not task.done():
            task.cancel()
        done, _ = await asyncio.wait({task}, timeout=self._cleanup_timeout)
        if not done:
            self._mark_unhealthy(
                _VisionTransportCleanupError(
                    f"Vision HTTP {context} cancellation exceeded its cleanup bound"
                )
            )
            return False
        await asyncio.gather(task, return_exceptions=True)
        return True

    def _ensure_client_close_task(self) -> asyncio.Task[None] | None:
        if self._client is None:
            return None
        if self._client_close_task is None:
            self._client_close_task = asyncio.create_task(self._client.aclose())
            self._client_close_task.add_done_callback(self._consume_task_exception)
        return self._client_close_task

    def _ensure_retirement_task(self) -> None:
        if self._retirement_task is None:
            self._retirement_task = asyncio.create_task(self._retire_client_bounded())
            self._retirement_task.add_done_callback(self._consume_task_exception)

    async def _retire_client_bounded(self) -> None:
        await self._cancel_background_tasks_bounded()
        client_close_task = self._ensure_client_close_task()
        if client_close_task is not None:
            await self._settle_task_bounded(client_close_task)
        await self._cancel_background_tasks_bounded()

    async def _cancel_background_tasks_bounded(self) -> None:
        tasks = {task for task in self._background_tasks if not task.done()}
        if not tasks:
            return
        for task in tasks:
            task.cancel()
        done, pending = await asyncio.wait(tasks, timeout=self._cleanup_timeout)
        if done:
            await asyncio.gather(*done, return_exceptions=True)
        if not pending:
            return
        for task in pending:
            task.cancel()
        done, _ = await asyncio.wait(pending, timeout=self._cleanup_timeout)
        if done:
            await asyncio.gather(*done, return_exceptions=True)

    async def _settle_task_bounded(self, task: asyncio.Task[object]) -> None:
        done, _ = await asyncio.wait({task}, timeout=self._cleanup_timeout)
        if done:
            await asyncio.gather(*done, return_exceptions=True)
            return
        task.cancel()
        done, _ = await asyncio.wait({task}, timeout=self._cleanup_timeout)
        if done:
            await asyncio.gather(*done, return_exceptions=True)

    @staticmethod
    def _consume_task_exception(task: asyncio.Task[object]) -> None:
        if not task.cancelled():
            task.exception()

    async def _shutdown(self) -> None:
        if self._retirement_requested:
            self._ensure_retirement_task()
        current = asyncio.current_task()
        active = [task for task in asyncio.all_tasks() if task is not current and not task.done()]
        for task in active:
            task.cancel()
        client_close_task = self._ensure_client_close_task()
        shutdown_tasks = {*active}
        if client_close_task is not None:
            shutdown_tasks.add(client_close_task)
        if not shutdown_tasks:
            return
        done, pending = await asyncio.wait(
            shutdown_tasks,
            timeout=self._cleanup_timeout,
        )
        if pending:
            self._mark_unhealthy(
                _VisionTransportCleanupError("Vision HTTP shutdown exceeded its cleanup bound")
            )
            for task in pending:
                task.cancel()
            cancelled, still_pending = await asyncio.wait(
                pending,
                timeout=self._cleanup_timeout,
            )
            done.update(cancelled)
            if still_pending:
                self._mark_unhealthy(
                    _VisionTransportCleanupError("Vision HTTP shutdown cancellation did not finish")
                )
            if done:
                await asyncio.gather(*done, return_exceptions=True)
            raise TimeoutError("Vision HTTP shutdown timed out")
        await asyncio.gather(*done, return_exceptions=True)
        if client_close_task is not None:
            client_close_task.result()

    def _wait_for_cancellation(self, completed: threading.Event) -> None:
        if completed.wait(self._cleanup_timeout):
            return
        self._mark_unhealthy(
            _VisionTransportCleanupError(
                "Vision HTTP request cancellation exceeded its cleanup bound"
            )
        )

    def _assert_healthy_locked(self) -> None:
        if self._closed:
            raise VisionTransportClosedError("Vision HTTP transport is closed")
        if self._failure is not None:
            raise VisionTransportUnhealthyError(
                f"Vision HTTP transport is unhealthy: {self._failure}"
            ) from self._failure

    def _assert_dispatch_allowed(self) -> None:
        with self._state_lock:
            self._assert_healthy_locked()

    def _mark_unhealthy(self, failure: BaseException) -> None:
        schedule_retirement = False
        with self._state_lock:
            if self._failure is None:
                self._failure = failure
            if not self._closed and not self._retirement_requested:
                self._retirement_requested = True
                schedule_retirement = True
        if schedule_retirement:
            self._loop.call_soon_threadsafe(self._ensure_retirement_task)
