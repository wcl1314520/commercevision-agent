from __future__ import annotations

import asyncio
import gzip
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from commercevision_providers.vision_credentials import StaticVisionApiKeyProvider
from commercevision_providers.vision_transport import (
    AsyncVisionHttpTransport,
    VisionSubmissionOutcomeUnknownError,
    VisionTransportUnhealthyError,
)


class _BlockingReadStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.closed = threading.Event()

    async def __aiter__(self):
        self.entered.set()
        await asyncio.Event().wait()
        yield b"unreachable"

    async def aclose(self) -> None:
        self.closed.set()


class _CancellationResistantCloseStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.close_entered = threading.Event()
        self.release_close = threading.Event()
        self.close_finished = threading.Event()

    async def __aiter__(self):
        yield b"first"

    async def aclose(self) -> None:
        self.close_entered.set()
        while not self.release_close.is_set():
            try:
                await asyncio.sleep(0.005)
            except asyncio.CancelledError:
                continue
        self.close_finished.set()


class _FailingCloseStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.close_calls = 0

    async def __aiter__(self):
        yield b"complete"

    async def aclose(self) -> None:
        self.close_calls += 1
        raise RuntimeError("private response close failure")


class _CancellationResistantReadStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.read_entered = threading.Event()
        self.release_read = threading.Event()
        self.closed = threading.Event()
        self._finished = False

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        if self._finished:
            raise StopAsyncIteration
        self.read_entered.set()
        while not self.release_read.is_set():
            try:
                await asyncio.sleep(0.005)
            except asyncio.CancelledError:
                continue
        self._finished = True
        return b"late"

    async def aclose(self) -> None:
        self.closed.set()


class _BlockingCloseClient(httpx.AsyncClient):
    def __init__(self) -> None:
        super().__init__(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
        self.close_entered = threading.Event()
        self.release_close = threading.Event()

    async def aclose(self) -> None:
        self.close_entered.set()
        while not self.release_close.is_set():
            await asyncio.sleep(0.005)


class _CancellationResistantRetirementClient(httpx.AsyncClient):
    def __init__(self, handler) -> None:
        super().__init__(transport=httpx.MockTransport(handler))
        self.close_calls = 0
        self.close_entered = threading.Event()
        self.release_close = threading.Event()
        self.close_finished = threading.Event()

    async def aclose(self) -> None:
        self.close_calls += 1
        self.close_entered.set()
        while not self.release_close.is_set():
            try:
                await asyncio.sleep(0.005)
            except asyncio.CancelledError:
                continue
        try:
            await super().aclose()
        finally:
            self.close_finished.set()


class _QueueObservingTransport(AsyncVisionHttpTransport):
    def __init__(self, **kwargs) -> None:
        self._send_entries = 0
        self._send_entries_lock = threading.Lock()
        self.second_send_entered = threading.Event()
        super().__init__(**kwargs)

    async def _send(self, request_bytes: bytes, *, deadline_at: float):
        with self._send_entries_lock:
            self._send_entries += 1
            if self._send_entries == 2:
                self.second_send_entered.set()
        return await super()._send(request_bytes, deadline_at=deadline_at)


class _TrackingReadStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.chunks_read = 0
        self.closed = threading.Event()

    async def __aiter__(self):
        for chunk in self._chunks:
            self.chunks_read += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed.set()


def _transport(
    handler,
    *,
    read_timeout_seconds: float = 0.1,
    client: httpx.AsyncClient | None = None,
) -> AsyncVisionHttpTransport:
    resolved_client = client or httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return AsyncVisionHttpTransport(
        credential_provider=StaticVisionApiKeyProvider("secret-api-key"),
        endpoint="https://dashscope.aliyuncs.com/compatible-mode/v1",
        connect_timeout_seconds=0.1,
        read_timeout_seconds=read_timeout_seconds,
        maximum_concurrency=1,
        maximum_response_bytes=64,
        client=resolved_client,
    )


@pytest.mark.parametrize(
    "protected_header",
    ["Authorization", "Content-Type", "Host", "Accept-Encoding"],
)
def test_request_headers_cannot_override_transport_security_headers(
    protected_header: str,
) -> None:
    with pytest.raises(ValueError, match="protected headers"):
        AsyncVisionHttpTransport(
            credential_provider=StaticVisionApiKeyProvider("secret-api-key"),
            endpoint="https://dashscope.aliyuncs.com/compatible-mode/v1",
            connect_timeout_seconds=0.1,
            read_timeout_seconds=0.1,
            maximum_concurrency=1,
            maximum_response_bytes=64,
            request_headers={protected_header: "attacker-controlled"},
        )


def test_close_does_not_swallow_active_response_cancellation() -> None:
    stream = _BlockingReadStream()
    transport = _transport(lambda _: httpx.Response(200, stream=stream))

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            transport.send,
            b"{}",
            deadline_at=time.monotonic() + 2,
        )
        assert stream.entered.wait(1)
        transport.close()

        with pytest.raises(VisionSubmissionOutcomeUnknownError):
            pending.result(timeout=1)

    assert stream.closed.wait(1)


def test_cancellation_resistant_response_cleanup_poison_transport() -> None:
    first_stream = _CancellationResistantCloseStream()
    call_count = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(200, stream=first_stream)
        return httpx.Response(200, content=b"second")

    transport = _transport(handler, read_timeout_seconds=0.05)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            transport.send,
            b"{}",
            deadline_at=time.monotonic() + 1,
        )
        assert first_stream.close_entered.wait(1)
        try:
            first = pending.result(timeout=0.3)
            with pytest.raises(VisionTransportUnhealthyError, match="response close"):
                transport.assert_ready()
            with pytest.raises(VisionTransportUnhealthyError, match="response close"):
                transport.send(b"{}", deadline_at=time.monotonic() + 0.3)
        finally:
            first_stream.release_close.set()
            transport.close()

    assert first.completion_uncertain is True
    assert call_count == 1


def test_response_close_failure_poison_transport_before_another_dispatch() -> None:
    stream = _FailingCloseStream()
    call_count = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(200, stream=stream)
        return httpx.Response(200, content=b"must-not-dispatch")

    transport = _transport(handler)
    try:
        first = transport.send(b"{}", deadline_at=time.monotonic() + 1)

        with pytest.raises(VisionTransportUnhealthyError, match="response close"):
            transport.assert_ready()
        with pytest.raises(VisionTransportUnhealthyError, match="response close"):
            transport.send(b"{}", deadline_at=time.monotonic() + 1)
    finally:
        transport.close()

    assert first.completion_uncertain is True
    assert stream.close_calls == 1
    assert call_count == 1


def test_poison_rejects_already_queued_request_and_retires_client_once() -> None:
    first_stream = _CancellationResistantCloseStream()
    call_count = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(200, stream=first_stream)
        return httpx.Response(200, content=b"must-not-dispatch")

    client = _CancellationResistantRetirementClient(handler)
    transport = _QueueObservingTransport(
        credential_provider=StaticVisionApiKeyProvider("secret-api-key"),
        endpoint="https://dashscope.aliyuncs.com/compatible-mode/v1",
        connect_timeout_seconds=0.1,
        read_timeout_seconds=0.05,
        maximum_concurrency=1,
        maximum_response_bytes=64,
        client=client,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_pending = pool.submit(
            transport.send,
            b'{"request":1}',
            deadline_at=time.monotonic() + 1,
        )
        assert first_stream.close_entered.wait(1)
        second_pending = pool.submit(
            transport.send,
            b'{"request":2}',
            deadline_at=time.monotonic() + 1,
        )
        assert transport.second_send_entered.wait(1)

        first = first_pending.result(timeout=0.5)
        with pytest.raises(VisionTransportUnhealthyError, match="response close"):
            second_pending.result(timeout=0.5)
        assert client.close_entered.wait(0.5)

        first_stream.release_close.set()
        client.release_close.set()
        assert first_stream.close_finished.wait(1)
        assert client.close_finished.wait(1)
        transport.close()

    assert first.completion_uncertain is True
    assert call_count == 1
    assert client.close_calls == 1


def test_cancellation_resistant_response_read_poison_transport() -> None:
    first_stream = _CancellationResistantReadStream()
    call_count = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(200, stream=first_stream)
        return httpx.Response(200, content=b"second")

    transport = _transport(handler, read_timeout_seconds=0.05)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            transport.send,
            b"{}",
            deadline_at=time.monotonic() + 1,
        )
        assert first_stream.read_entered.wait(1)
        try:
            first = pending.result(timeout=0.3)
            with pytest.raises(VisionTransportUnhealthyError, match="response read"):
                transport.assert_ready()
            with pytest.raises(VisionTransportUnhealthyError, match="response read"):
                transport.send(b"{}", deadline_at=time.monotonic() + 0.3)
        finally:
            first_stream.release_read.set()
            transport.close()

    assert first.completion_uncertain is True
    assert first.response.content == b""
    assert first_stream.closed.wait(1)
    assert call_count == 1


def test_client_shutdown_is_explicitly_bounded() -> None:
    client = _BlockingCloseClient()
    transport = _transport(
        lambda _: httpx.Response(200),
        read_timeout_seconds=0.05,
        client=client,
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        closing = pool.submit(transport.close)
        assert client.close_entered.wait(1)
        try:
            time.sleep(0.2)
            assert closing.done()
        finally:
            client.release_close.set()
        with pytest.raises(TimeoutError, match="shutdown timed out"):
            closing.result(timeout=1)


def test_non_identity_content_encoding_is_rejected_without_reading_compressed_body() -> None:
    stream = _TrackingReadStream([gzip.compress(b"x" * 4096)])
    transport = _transport(
        lambda _: httpx.Response(
            200,
            headers={"Content-Encoding": "gzip"},
            stream=stream,
        )
    )
    try:
        evidence = transport.send(b"{}", deadline_at=time.monotonic() + 1)
    finally:
        transport.close()

    assert evidence.completion_uncertain is True
    assert evidence.response.content == b""
    assert stream.chunks_read == 0
    assert stream.closed.wait(1)


def test_raw_response_retains_only_one_bounded_overflow_byte() -> None:
    stream = _TrackingReadStream([b"x" * 64, b"overflow", b"must-not-be-read"])
    transport = _transport(lambda _: httpx.Response(200, stream=stream))
    try:
        evidence = transport.send(b"{}", deadline_at=time.monotonic() + 1)
    finally:
        transport.close()

    assert evidence.body_too_large is True
    assert evidence.response.content == (b"x" * 64) + b"o"
    assert stream.chunks_read == 2
    assert stream.closed.wait(1)
