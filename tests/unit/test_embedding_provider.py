import asyncio
import json
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest
from commercevision_contracts import (
    EmbeddingImageInputV1,
    EmbeddingProviderFailure,
    EmbeddingProviderRequestV1,
)
from commercevision_domain import VectorKind, new_uuid7
from commercevision_providers import (
    AlibabaEmbeddingProvider,
    DeterministicEmbeddingProvider,
    DeterministicEmbeddingScenario,
)
from pydantic import SecretStr

NOW = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)


def _request(
    *,
    input_hash: str = "a" * 64,
    dimension: int = 4,
    provider: str = "deterministic",
    required_headers: bool = True,
) -> EmbeddingProviderRequestV1:
    return EmbeddingProviderRequestV1(
        provider=provider,
        model_id="qwen3-vl-embedding",
        pinned_revision="embedding-eval-2026-07-31",
        model_configuration_version="embedding-config-v1",
        preprocessing_version="image-preprocess-v1",
        vector_kind=VectorKind.IMAGE,
        expected_dimension=dimension,
        input_hash=input_hash,
        images=[
            EmbeddingImageInputV1(
                asset_version_id=new_uuid7(),
                content_sha256="f" * 64,
                byte_size=1024,
                url=SecretStr("https://controlled.invalid/read?token=must-not-leak"),
                required_headers=(
                    {"authorization": SecretStr("Bearer header-must-not-leak")}
                    if required_headers
                    else {}
                ),
                expires_at=NOW + timedelta(minutes=5),
            )
        ],
    )


def test_deterministic_embedding_provider_is_replay_stable_at_the_public_seam() -> None:
    provider = DeterministicEmbeddingProvider(
        provider="deterministic",
        model_id="qwen3-vl-embedding",
        pinned_revision="embedding-eval-2026-07-31",
        model_configuration_version="embedding-config-v1",
        clock=lambda: NOW,
    )
    request = _request()

    first = provider.embed(request)
    replay = provider.embed(request)

    first.validate_for(request)
    assert replay == first
    assert first.provider == "deterministic"
    assert first.actual_model == "qwen3-vl-embedding"
    assert first.usage == {"image_count": 1}
    assert "must-not-leak" not in repr(provider)
    assert "must-not-leak" not in repr(first)


def test_alibaba_embedding_provider_uses_the_official_image_http_shape() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "output": {
                    "embeddings": [
                        {
                            "index": 0,
                            "embedding": [0.0625] * 256,
                            "type": "vl",
                        }
                    ]
                },
                "usage": {
                    "input_tokens": 0,
                    "image_tokens": 12,
                    "total_tokens": 12,
                },
                "request_id": "provider-request-1",
            },
        )

    provider = AlibabaEmbeddingProvider(
        api_key="api-key-must-not-leak",
        endpoint="https://dashscope.aliyuncs.com/api/v1",
        endpoint_region="cn-beijing",
        model_id="qwen3-vl-embedding",
        pinned_revision="embedding-eval-2026-07-31",
        model_configuration_version="embedding-config-v1",
        connect_timeout_seconds=0.05,
        read_timeout_seconds=0.05,
        end_to_end_timeout_seconds=1,
        maximum_concurrency=1,
        maximum_response_bytes=64 * 1024,
        allowed_image_origins=frozenset({"https://controlled.invalid"}),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        clock=lambda: NOW,
    )
    request = _request(
        provider="alibaba-model-studio",
        dimension=256,
        required_headers=False,
    )
    try:
        result = provider.embed(request)
    finally:
        provider.close()

    result.validate_for(request)
    assert result.provider_request_id == "provider-request-1"
    assert result.actual_model == "qwen3-vl-embedding"
    assert result.usage == {
        "input_tokens": 0,
        "image_tokens": 12,
        "total_tokens": 12,
    }
    assert captured[0].url == (
        "https://dashscope.aliyuncs.com/api/v1/services/embeddings/"
        "multimodal-embedding/multimodal-embedding"
    )
    assert json.loads(captured[0].content) == {
        "model": "qwen3-vl-embedding",
        "input": {"contents": [{"image": ("https://controlled.invalid/read?token=must-not-leak")}]},
        "parameters": {
            "dimension": 256,
            "enable_fusion": False,
            "output_type": "dense",
        },
    }
    assert captured[0].headers["Authorization"] == "Bearer api-key-must-not-leak"
    assert "must-not-leak" not in repr(provider)
    assert "must-not-leak" not in repr(result)


def test_alibaba_embedding_provider_uses_the_official_product_fusion_shape() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "output": {
                    "embeddings": [{"index": 0, "embedding": [0.0625] * 256, "type": "fusion"}]
                },
                "usage": {"input_tokens": 8, "image_tokens": 12, "total_tokens": 20},
                "request_id": "provider-fused-request-1",
            },
        )

    provider = AlibabaEmbeddingProvider(
        api_key="api-key-must-not-leak",
        endpoint="https://dashscope.aliyuncs.com/api/v1",
        endpoint_region="cn-beijing",
        model_id="qwen3-vl-embedding",
        pinned_revision="embedding-eval-2026-07-31",
        model_configuration_version="embedding-config-v1",
        preprocessing_version="image-preprocess-v1",
        additional_preprocessing_versions=frozenset({"product-fused-v1"}),
        connect_timeout_seconds=0.05,
        read_timeout_seconds=0.05,
        end_to_end_timeout_seconds=1,
        maximum_concurrency=1,
        maximum_response_bytes=64 * 1024,
        allowed_image_origins=frozenset({"https://controlled.invalid"}),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        clock=lambda: NOW,
    )
    controlled_text = '{"title":"鎏金口红"}'
    request = _request(
        provider="alibaba-model-studio",
        dimension=256,
        required_headers=False,
    ).model_copy(
        update={
            "preprocessing_version": "product-fused-v1",
            "vector_kind": VectorKind.PRODUCT_FUSED,
            "controlled_text": controlled_text,
        }
    )
    try:
        result = provider.embed(request)
    finally:
        provider.close()

    result.validate_for(request)
    assert json.loads(captured[0].content) == {
        "model": "qwen3-vl-embedding",
        "input": {
            "contents": [
                {"text": controlled_text},
                {"image": "https://controlled.invalid/read?token=must-not-leak"},
            ]
        },
        "parameters": {
            "dimension": 256,
            "enable_fusion": True,
            "output_type": "dense",
        },
    }


def test_deterministic_embedding_provider_exposes_normalized_fixture_failures() -> None:
    provider = DeterministicEmbeddingProvider(
        provider="deterministic",
        model_id="qwen3-vl-embedding",
        pinned_revision="embedding-eval-2026-07-31",
        model_configuration_version="embedding-config-v1",
        scenario=DeterministicEmbeddingScenario.THROTTLED,
        clock=lambda: NOW,
    )

    with pytest.raises(EmbeddingProviderFailure) as captured:
        provider.embed(_request())

    assert captured.value.error.code == "EMBEDDING_THROTTLED"
    assert captured.value.error.retryable is True
    assert captured.value.error.retry_after_seconds == 5
    assert captured.value.error.outcome_unknown is False


def test_deterministic_embedding_provider_rejects_a_preprocessing_identity_mismatch() -> None:
    provider = DeterministicEmbeddingProvider(
        provider="deterministic",
        model_id="qwen3-vl-embedding",
        pinned_revision="embedding-eval-2026-07-31",
        model_configuration_version="embedding-config-v1",
        preprocessing_version="image-preprocess-v1",
        clock=lambda: NOW,
    )
    mismatched = _request().model_copy(update={"preprocessing_version": "image-preprocess-v2"})

    with pytest.raises(EmbeddingProviderFailure) as captured:
        provider.embed(mismatched)

    assert captured.value.error.code == "EMBEDDING_REQUEST_IDENTITY_MISMATCH"


def test_embedding_provider_accepts_the_configured_product_fused_preprocessing_identity() -> None:
    provider = DeterministicEmbeddingProvider(
        provider="deterministic",
        model_id="qwen3-vl-embedding",
        pinned_revision="embedding-eval-2026-07-31",
        model_configuration_version="embedding-config-v1",
        preprocessing_version="image-preprocess-v1",
        additional_preprocessing_versions=frozenset({"product-fused-v1"}),
        clock=lambda: NOW,
    )
    request = _request().model_copy(
        update={
            "preprocessing_version": "product-fused-v1",
            "vector_kind": VectorKind.PRODUCT_FUSED,
            "controlled_text": '{"title":"鎏金口红 summer"}',
        }
    )

    result = provider.embed(request)

    result.validate_for(request)
    assert len(result.vectors) == 1


@pytest.mark.parametrize(
    ("status_code", "headers", "expected"),
    [
        (
            429,
            {"retry-after": "17", "x-request-id": "request-throttled"},
            ("EMBEDDING_THROTTLED", "THROTTLED", True, 17, False),
        ),
        (
            503,
            {"retry-after": "invalid", "x-request-id": "request-unavailable"},
            ("EMBEDDING_UNAVAILABLE", "UNAVAILABLE", True, None, False),
        ),
        (
            400,
            {"x-request-id": "request-rejected"},
            ("EMBEDDING_REJECTED", "REJECTED", False, None, False),
        ),
    ],
)
def test_alibaba_embedding_provider_normalizes_http_failures(
    status_code: int,
    headers: dict[str, str],
    expected: tuple[str, str, bool, int | None, bool],
) -> None:
    provider = _alibaba(
        lambda _: httpx.Response(
            status_code,
            headers=headers,
            json={
                "code": "untrusted-provider-code",
                "message": "secret provider detail must-not-leak",
                "request_id": headers["x-request-id"],
            },
        )
    )
    try:
        with pytest.raises(EmbeddingProviderFailure) as captured:
            provider.embed(_alibaba_request())
    finally:
        provider.close()

    error = captured.value.error
    assert (
        error.code,
        error.category,
        error.retryable,
        error.retry_after_seconds,
        error.outcome_unknown,
    ) == expected
    assert error.provider_request_id == headers["x-request-id"]
    assert "must-not-leak" not in str(captured.value)
    assert "must-not-leak" not in repr(captured.value)


def test_alibaba_embedding_provider_prefers_429_headers_over_a_partial_body() -> None:
    provider = _alibaba(
        lambda _: httpx.Response(
            429,
            headers={
                "retry-after": "17",
                "x-request-id": "request-throttled-partial",
            },
            stream=_FailingReadByteStream(),
        )
    )
    try:
        with pytest.raises(EmbeddingProviderFailure) as captured:
            provider.embed(_alibaba_request())
    finally:
        provider.close()

    assert captured.value.error.code == "EMBEDDING_THROTTLED"
    assert captured.value.error.retryable is True
    assert captured.value.error.retry_after_seconds == 17
    assert captured.value.error.provider_request_id == "request-throttled-partial"
    assert captured.value.error.outcome_unknown is False


def test_alibaba_embedding_provider_supports_bounded_http_date_retry_after() -> None:
    provider = _alibaba(
        lambda _: httpx.Response(
            429,
            headers={
                "retry-after": format_datetime(
                    NOW + timedelta(seconds=17),
                    usegmt=True,
                ),
                "x-request-id": "request-throttled-date",
            },
        )
    )
    try:
        with pytest.raises(EmbeddingProviderFailure) as captured:
            provider.embed(_alibaba_request())
    finally:
        provider.close()

    assert captured.value.error.retry_after_seconds == 17


def test_alibaba_embedding_provider_maps_malformed_and_unknown_outcomes() -> None:
    malformed = _alibaba(
        lambda _: httpx.Response(
            200,
            json={
                "output": {"embeddings": []},
                "usage": {
                    "input_tokens": 0,
                    "image_tokens": 1,
                    "total_tokens": 1,
                },
                "request_id": "request-malformed",
            },
        )
    )
    unknown = _alibaba(
        lambda request: (_ for _ in ()).throw(
            httpx.ReadError(
                "transport detail with https://secret.invalid/?token=must-not-leak",
                request=request,
            )
        )
    )
    try:
        with pytest.raises(EmbeddingProviderFailure) as malformed_failure:
            malformed.embed(_alibaba_request())
        with pytest.raises(EmbeddingProviderFailure) as unknown_failure:
            unknown.embed(_alibaba_request())
    finally:
        malformed.close()
        unknown.close()

    assert malformed_failure.value.error.code == "EMBEDDING_INVALID_RESPONSE"
    assert malformed_failure.value.error.outcome_unknown is False
    assert unknown_failure.value.error.code == "EMBEDDING_SUBMISSION_OUTCOME_UNKNOWN"
    assert unknown_failure.value.error.outcome_unknown is True
    assert unknown_failure.value.error.retryable is False
    assert "must-not-leak" not in repr(unknown_failure.value)


def test_alibaba_embedding_provider_severs_every_sensitive_exception_chain() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError(
            "raw transport token=must-not-leak",
            request=request,
        )

    provider = _alibaba(handler)
    try:
        with pytest.raises(EmbeddingProviderFailure) as captured:
            provider.embed(_alibaba_request())
    finally:
        provider.close()

    failure = captured.value
    assert failure.__cause__ is None
    assert failure.__context__ is None
    rendered = "".join(traceback.format_exception(failure))
    assert "must-not-leak" not in rendered
    assert "Authorization" not in rendered
    assert "controlled.invalid" not in rendered


@pytest.mark.parametrize(
    ("handler", "expected_code"),
    [
        (
            lambda _: httpx.Response(
                500,
                json={
                    "code": "RequestTimeOut",
                    "message": "official provider timeout",
                    "request_id": "request-server-timeout",
                },
            ),
            "EMBEDDING_TIMEOUT",
        ),
        (
            lambda request: (_ for _ in ()).throw(
                httpx.ConnectTimeout("connection detail", request=request)
            ),
            "EMBEDDING_TIMEOUT",
        ),
    ],
)
def test_alibaba_embedding_provider_normalizes_server_and_connect_timeouts(
    handler,
    expected_code: str,
) -> None:
    provider = _alibaba(handler)
    try:
        with pytest.raises(EmbeddingProviderFailure) as captured:
            provider.embed(_alibaba_request())
    finally:
        provider.close()

    assert captured.value.error.code == expected_code
    assert captured.value.error.category == "TIMEOUT"
    assert captured.value.error.retryable is True
    assert captured.value.error.outcome_unknown is False


def test_alibaba_embedding_provider_rejects_headers_and_short_expiry_without_dispatch() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    provider = _alibaba(handler)
    short_lived = _alibaba_request().model_copy(
        update={
            "images": [
                _alibaba_request()
                .images[0]
                .model_copy(update={"expires_at": NOW + timedelta(milliseconds=500)})
            ]
        }
    )
    try:
        with pytest.raises(EmbeddingProviderFailure) as headers_failure:
            provider.embed(
                _alibaba_request().model_copy(
                    update={
                        "images": [
                            _alibaba_request()
                            .images[0]
                            .model_copy(
                                update={
                                    "required_headers": {
                                        "authorization": SecretStr("Bearer must-not-leak")
                                    }
                                }
                            )
                        ]
                    }
                )
            )
        with pytest.raises(EmbeddingProviderFailure) as expiry_failure:
            provider.embed(short_lived)
    finally:
        provider.close()

    assert headers_failure.value.error.code == "EMBEDDING_IMAGE_HEADERS_UNSUPPORTED"
    assert expiry_failure.value.error.code == "EMBEDDING_IMAGE_REFERENCE_EXPIRING"
    assert "must-not-leak" not in repr(headers_failure.value)
    assert calls == 0


def test_alibaba_embedding_provider_normalizes_an_invalid_secret_url() -> None:
    provider = _alibaba(lambda _: httpx.Response(500))
    invalid = _alibaba_request().model_copy(
        update={
            "images": [
                _alibaba_request()
                .images[0]
                .model_copy(
                    update={
                        "url": SecretStr(
                            "https://controlled.invalid:invalid/read?token=must-not-leak"
                        )
                    }
                )
            ]
        }
    )
    try:
        with pytest.raises(EmbeddingProviderFailure) as captured:
            provider.embed(invalid)
    finally:
        provider.close()

    assert captured.value.error.code == "EMBEDDING_IMAGE_URL_INVALID"
    assert "must-not-leak" not in repr(captured.value)


def test_alibaba_embedding_provider_close_cancels_an_active_response() -> None:
    stream = _HangingByteStream()
    provider = _alibaba(
        lambda _: httpx.Response(
            200,
            headers={"x-request-id": "request-active-at-close"},
            stream=stream,
        )
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(provider.embed, _alibaba_request())
        assert stream.entered.wait(1)
        provider.close()
        with pytest.raises(EmbeddingProviderFailure) as captured:
            pending.result(timeout=1)

    assert stream.closed.wait(1)
    assert provider.shutdown_drained is True
    assert captured.value.error.outcome_unknown is True
    assert captured.value.error.retryable is False
    assert captured.value.error.provider_request_id == "request-active-at-close"


def test_alibaba_embedding_provider_close_keeps_queued_work_safe_to_retry() -> None:
    stream = _HangingByteStream()
    provider = _alibaba(
        lambda _: httpx.Response(
            200,
            headers={"x-request-id": "request-dispatched-at-close"},
            stream=stream,
        ),
        read_timeout_seconds=0.5,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        dispatched = pool.submit(provider.embed, _alibaba_request())
        assert stream.entered.wait(1)
        queued = pool.submit(provider.embed, _alibaba_request())
        with provider._lifecycle_condition:
            assert provider._lifecycle_condition.wait_for(
                lambda: provider._active_lifecycles == 2,
                timeout=1,
            )
        provider.close()
        with pytest.raises(EmbeddingProviderFailure) as dispatched_failure:
            dispatched.result(timeout=1)
        with pytest.raises(EmbeddingProviderFailure) as queued_failure:
            queued.result(timeout=1)

    assert dispatched_failure.value.error.outcome_unknown is True
    assert dispatched_failure.value.error.provider_request_id == ("request-dispatched-at-close")
    assert queued_failure.value.error.outcome_unknown is False
    assert queued_failure.value.error.retryable is True


def test_alibaba_embedding_after_close_is_a_typed_safe_retry_failure() -> None:
    provider = _alibaba(lambda _: httpx.Response(500))
    provider.close()

    with pytest.raises(EmbeddingProviderFailure) as captured:
        provider.embed(_alibaba_request())

    assert captured.value.error.code == "EMBEDDING_UNAVAILABLE"
    assert captured.value.error.retryable is True
    assert captured.value.error.outcome_unknown is False
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_alibaba_readiness_and_close_normalize_raw_secret_exception_graphs() -> None:
    class FailingCloseClient(httpx.AsyncClient):
        async def aclose(self) -> None:
            raise RuntimeError("transport shutdown api-key-must-not-leak")

    client = FailingCloseClient(transport=httpx.MockTransport(lambda _: httpx.Response(500)))
    provider = AlibabaEmbeddingProvider(
        api_key="api-key-must-not-leak",
        endpoint="https://dashscope.aliyuncs.com/api/v1",
        endpoint_region="cn-beijing",
        model_id="qwen3-vl-embedding",
        pinned_revision="embedding-eval-2026-07-31",
        model_configuration_version="embedding-config-v1",
        connect_timeout_seconds=0.05,
        read_timeout_seconds=0.05,
        end_to_end_timeout_seconds=1,
        maximum_concurrency=1,
        maximum_response_bytes=64 * 1024,
        allowed_image_origins=frozenset({"https://controlled.invalid"}),
        client=client,
        clock=lambda: NOW,
    )
    with provider._transport._state_lock:  # noqa: SLF001 - boundary fault injection
        provider._transport._failure = RuntimeError(  # noqa: SLF001
            "readiness api-key-must-not-leak"
        )

    with pytest.raises(ConnectionError) as readiness:
        provider.assert_ready()
    assert "must-not-leak" not in str(readiness.value)
    assert readiness.value.__cause__ is None
    assert readiness.value.__context__ is None

    with pytest.raises(ExceptionGroup) as shutdown:
        provider.close()
    assert "must-not-leak" not in repr(shutdown.value)
    assert all(
        item.__cause__ is None and item.__context__ is None for item in shutdown.value.exceptions
    )


@pytest.mark.parametrize(
    "invalid_value",
    [
        "9" * 4000,
        "3.5e38",
    ],
)
def test_alibaba_embedding_provider_rejects_unsafe_float32_values(
    invalid_value: str,
) -> None:
    vector = ",".join([invalid_value, *(["0.0"] * 255)])
    provider = _alibaba(
        lambda _: httpx.Response(
            200,
            content=(
                '{"output":{"embeddings":[{"index":0,"embedding":['
                + vector
                + '],"type":"vl"}]},"usage":{"input_tokens":0,'
                '"image_tokens":1,"total_tokens":1},'
                '"request_id":"request-invalid-number"}'
            ).encode(),
        )
    )
    try:
        with pytest.raises(EmbeddingProviderFailure) as captured:
            provider.embed(_alibaba_request())
    finally:
        provider.close()

    assert captured.value.error.code == "EMBEDDING_INVALID_RESPONSE"


class _HangingByteStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.closed = threading.Event()

    async def __aiter__(self):
        self.entered.set()
        yield b"{"
        await asyncio.Event().wait()

    async def aclose(self) -> None:
        self.closed.set()


class _FailingReadByteStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b'{"code":'
        raise httpx.ReadError("partial provider body must-not-leak")

    async def aclose(self) -> None:
        return None


def _alibaba_request() -> EmbeddingProviderRequestV1:
    return _request(
        provider="alibaba-model-studio",
        dimension=256,
        required_headers=False,
    )


def _alibaba(
    handler,
    *,
    read_timeout_seconds: float = 0.05,
) -> AlibabaEmbeddingProvider:
    return AlibabaEmbeddingProvider(
        api_key="api-key-must-not-leak",
        endpoint="https://dashscope.aliyuncs.com/api/v1",
        endpoint_region="cn-beijing",
        model_id="qwen3-vl-embedding",
        pinned_revision="embedding-eval-2026-07-31",
        model_configuration_version="embedding-config-v1",
        connect_timeout_seconds=0.05,
        read_timeout_seconds=read_timeout_seconds,
        end_to_end_timeout_seconds=1,
        maximum_concurrency=1,
        maximum_response_bytes=64 * 1024,
        allowed_image_origins=frozenset({"https://controlled.invalid"}),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        clock=lambda: NOW,
    )
