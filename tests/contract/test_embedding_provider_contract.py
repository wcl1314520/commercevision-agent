from collections.abc import Callable
from datetime import UTC, datetime, timedelta

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


def _request(provider: str) -> EmbeddingProviderRequestV1:
    return EmbeddingProviderRequestV1(
        provider=provider,
        model_id="qwen3-vl-embedding",
        pinned_revision="embedding-eval-2026-07-31",
        model_configuration_version="embedding-config-v1",
        preprocessing_version="image-preprocess-v1",
        vector_kind=VectorKind.IMAGE,
        expected_dimension=256,
        input_hash="a" * 64,
        images=[
            EmbeddingImageInputV1(
                asset_version_id=new_uuid7(),
                content_sha256="f" * 64,
                byte_size=1024,
                url=SecretStr("https://controlled.invalid/read?token=provider-contract-secret"),
                expires_at=NOW + timedelta(minutes=5),
            )
        ],
    )


def _deterministic() -> DeterministicEmbeddingProvider:
    return DeterministicEmbeddingProvider(
        provider="deterministic",
        model_id="qwen3-vl-embedding",
        pinned_revision="embedding-eval-2026-07-31",
        model_configuration_version="embedding-config-v1",
        clock=lambda: NOW,
    )


def _alibaba(
    handler: Callable[[httpx.Request], httpx.Response],
) -> AlibabaEmbeddingProvider:
    return AlibabaEmbeddingProvider(
        api_key="provider-contract-api-key",
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


def _official_success(_: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "output": {
                "embeddings": [
                    {
                        "index": 0,
                        "embedding": [0.03125] * 256,
                        "type": "vl",
                    }
                ]
            },
            "usage": {
                "input_tokens": 0,
                "image_tokens": 8,
                "total_tokens": 8,
            },
            "request_id": "provider-contract-request",
        },
    )


@pytest.mark.parametrize(
    ("provider_name", "factory"),
    [
        ("deterministic", _deterministic),
        ("alibaba-model-studio", lambda: _alibaba(_official_success)),
    ],
)
def test_embedding_adapters_share_the_typed_success_contract(
    provider_name: str,
    factory,
) -> None:
    adapter = factory()
    request = _request(provider_name)
    try:
        result = adapter.embed(request)
    finally:
        close = getattr(adapter, "close", None)
        if close is not None:
            close()

    result.validate_for(request)
    assert result.provider == provider_name
    assert result.actual_model == "qwen3-vl-embedding"
    assert result.provider_request_id
    assert "provider-contract-secret" not in repr(result)
    assert "provider-contract-api-key" not in repr(adapter)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: DeterministicEmbeddingProvider(
            provider="deterministic",
            model_id="qwen3-vl-embedding",
            pinned_revision="embedding-eval-2026-07-31",
            model_configuration_version="embedding-config-v1",
            scenario=DeterministicEmbeddingScenario.THROTTLED,
            clock=lambda: NOW,
        ),
        lambda: _alibaba(
            lambda _: httpx.Response(
                429,
                headers={"retry-after": "9"},
                json={
                    "code": "Throttling",
                    "message": "untrusted provider detail",
                    "request_id": "provider-contract-throttled",
                },
            )
        ),
    ],
)
def test_embedding_adapters_share_the_normalized_failure_contract(factory) -> None:
    adapter = factory()
    provider_name = (
        "alibaba-model-studio" if isinstance(adapter, AlibabaEmbeddingProvider) else "deterministic"
    )
    try:
        with pytest.raises(EmbeddingProviderFailure) as captured:
            adapter.embed(_request(provider_name))
    finally:
        close = getattr(adapter, "close", None)
        if close is not None:
            close()

    error = captured.value.error
    assert error.code == "EMBEDDING_THROTTLED"
    assert error.category == "THROTTLED"
    assert error.retryable is True
    assert error.retry_after_seconds in {5, 9}
    assert error.outcome_unknown is False
    assert "untrusted provider detail" not in str(captured.value)


def test_alibaba_embedding_adapter_maps_an_http_timeout_without_raw_details() -> None:
    adapter = _alibaba(
        lambda _: httpx.Response(
            408,
            json={
                "code": "RequestTimeOut",
                "message": "raw timeout detail must-not-cross-provider-seam",
                "request_id": "provider-contract-timeout",
            },
        )
    )
    try:
        with pytest.raises(EmbeddingProviderFailure) as captured:
            adapter.embed(_request("alibaba-model-studio"))
    finally:
        adapter.close()

    error = captured.value.error
    assert error.code == "EMBEDDING_TIMEOUT"
    assert error.category == "TIMEOUT"
    assert error.retryable is True
    assert error.outcome_unknown is False
    assert error.provider_request_id == "provider-contract-timeout"
    assert "must-not-cross-provider-seam" not in repr(captured.value)


def test_alibaba_embedding_adapter_runs_contract_validation_before_return() -> None:
    adapter = _alibaba(
        lambda _: httpx.Response(
            200,
            json={
                "output": {
                    "embeddings": [
                        {
                            "index": 0,
                            "embedding": [0.5] * 255,
                            "type": "vl",
                        }
                    ]
                },
                "usage": {
                    "input_tokens": 0,
                    "image_tokens": 8,
                    "total_tokens": 8,
                },
                "request_id": "provider-contract-wrong-dimension",
            },
        )
    )
    try:
        with pytest.raises(EmbeddingProviderFailure) as captured:
            adapter.embed(_request("alibaba-model-studio"))
    finally:
        adapter.close()

    assert captured.value.error.code == "EMBEDDING_INVALID_RESPONSE"
    assert captured.value.error.provider_request_id == ("provider-contract-wrong-dimension")


def test_alibaba_embedding_adapter_reports_only_the_submitted_model_alias() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "provider-claimed-revision-must-not-be-trusted",
                "output": {
                    "model": "different-provider-claimed-revision",
                    "embeddings": [
                        {
                            "index": 0,
                            "embedding": [0.03125] * 256,
                            "type": "vl",
                        }
                    ],
                },
                "usage": {
                    "input_tokens": 0,
                    "image_tokens": 8,
                    "total_tokens": 8,
                },
                "request_id": "provider-contract-model-alias",
            },
        )

    adapter = _alibaba(handler)
    try:
        result = adapter.embed(_request("alibaba-model-studio"))
    finally:
        adapter.close()

    assert result.actual_model == "qwen3-vl-embedding"
    assert result.actual_model != "embedding-eval-2026-07-31"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("model_id", "qwen3-vl-embedding-new-alias"),
        ("pinned_revision", "embedding-eval-2026-08-01"),
    ],
)
def test_alibaba_embedding_adapter_rejects_identity_drift_before_dispatch(
    field: str,
    value: str,
) -> None:
    dispatches = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal dispatches
        dispatches += 1
        return _official_success(_)

    adapter = _alibaba(handler)
    request = _request("alibaba-model-studio").model_copy(update={field: value})
    try:
        with pytest.raises(EmbeddingProviderFailure) as captured:
            adapter.embed(request)
    finally:
        adapter.close()

    assert captured.value.error.code == "EMBEDDING_REQUEST_IDENTITY_MISMATCH"
    assert dispatches == 0


@pytest.mark.parametrize(
    ("provider_name", "factory"),
    [
        ("deterministic", _deterministic),
        ("alibaba-model-studio", lambda: _alibaba(_official_success)),
    ],
)
def test_embedding_adapters_defend_against_bypassed_image_size_validation(
    provider_name: str,
    factory,
) -> None:
    adapter = factory()
    request = _request(provider_name)
    oversized = request.images[0].model_copy(update={"byte_size": 5 * 1024 * 1024 + 1})
    malformed = request.model_copy(update={"images": [oversized]})
    try:
        with pytest.raises(EmbeddingProviderFailure) as captured:
            adapter.embed(malformed)
    finally:
        close = getattr(adapter, "close", None)
        if close is not None:
            close()

    assert captured.value.error.code == "EMBEDDING_IMAGE_SIZE_UNSUPPORTED"
