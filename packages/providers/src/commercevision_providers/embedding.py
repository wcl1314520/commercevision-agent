"""Deterministic and Alibaba Model Studio vector embedding adapters.

DashScope currently returns no resolved revision for qwen3-vl-embedding. ``actual_model``
therefore records the submitted model ID; ``pinned_revision`` is the internal collection
epoch. A changed mainline alias must stop writes, bump this epoch, create a new collection,
and pass evaluation before activation; the adapter never treats it as provider-confirmed.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from enum import StrEnum
from types import TracebackType
from typing import Any
from urllib.parse import urlsplit

import httpx
from commercevision_contracts import (
    EmbeddingProviderErrorV1,
    EmbeddingProviderFailure,
    EmbeddingProviderRequestV1,
    EmbeddingProviderResultV1,
    EmbeddingVectorV1,
)
from pydantic import ValidationError

from .embedding_transport import (
    AsyncEmbeddingHttpTransport,
    EmbeddingHeadersObservedOutcomeUnknownError,
    EmbeddingPreDispatchCancelledTransportError,
    EmbeddingPreSubmissionTimeoutTransportError,
)
from .vision_credentials import (
    StaticVisionApiKeyProvider,
    VisionApiKeyProvider,
)
from .vision_transport import (
    VisionCredentialUnavailableTransportError,
    VisionSafeToRetryTransportError,
    VisionSubmissionOutcomeUnknownError,
)

_QWEN3_DIMENSIONS = frozenset({256, 512, 768, 1024, 1536, 2048, 2560})
_PROVIDER = "alibaba-model-studio"
_MAXIMUM_IMAGE_BYTES = 5 * 1024 * 1024
_MAXIMUM_FLOAT32 = 3.4028234663852886e38


class DeterministicEmbeddingScenario(StrEnum):
    SUCCESS = "SUCCESS"
    THROTTLED = "THROTTLED"
    TIMEOUT = "TIMEOUT"
    UNAVAILABLE = "UNAVAILABLE"
    REJECTED = "REJECTED"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    UNKNOWN = "UNKNOWN"


def _normalized_fixture_vector(seed: bytes, dimension: int) -> list[float]:
    raw = hashlib.shake_256(seed).digest(dimension * 4)
    values = [
        int.from_bytes(raw[offset : offset + 4], "big", signed=False) / 2**31 - 1.0
        for offset in range(0, len(raw), 4)
    ]
    magnitude = math.sqrt(sum(value * value for value in values))
    if magnitude == 0:
        values[0] = 1.0
        magnitude = 1.0
    return [value / magnitude for value in values]


class DeterministicEmbeddingProvider:
    """Stable fixture implementing the public vector embedding seam."""

    __slots__ = (
        "_clock",
        "_model_configuration_version",
        "_model_id",
        "_pinned_revision",
        "_preprocessing_version",
        "_preprocessing_versions",
        "_provider",
        "_scenario",
    )

    def __init__(
        self,
        *,
        provider: str,
        model_id: str,
        pinned_revision: str,
        model_configuration_version: str,
        preprocessing_version: str = "image-preprocess-v1",
        additional_preprocessing_versions: frozenset[str] = frozenset(),
        scenario: DeterministicEmbeddingScenario = DeterministicEmbeddingScenario.SUCCESS,
        clock: Any | None = None,
    ) -> None:
        if not all(
            (
                provider,
                model_id,
                pinned_revision,
                model_configuration_version,
                preprocessing_version,
            )
        ):
            raise ValueError("Deterministic embedding provider identity is incomplete")
        self._provider = provider
        self._model_id = model_id
        self._pinned_revision = pinned_revision
        self._model_configuration_version = model_configuration_version
        self._preprocessing_version = preprocessing_version
        self._preprocessing_versions = frozenset(
            {preprocessing_version, *additional_preprocessing_versions}
        )
        if any(not value or value != value.strip() for value in self._preprocessing_versions):
            raise ValueError("Deterministic embedding preprocessing identity is invalid")
        self._scenario = scenario
        self._clock = clock or (lambda: datetime.now(UTC))

    def embed(self, request: EmbeddingProviderRequestV1) -> EmbeddingProviderResultV1:
        self._validate_request(request)
        self._raise_scenario()
        vectors = [
            EmbeddingVectorV1(
                values=_normalized_fixture_vector(
                    (
                        f"{request.input_hash}:{image.asset_version_id}:"
                        f"{image.content_sha256}:{index}"
                    ).encode("ascii"),
                    request.expected_dimension,
                )
            )
            for index, image in enumerate(request.images)
        ]
        result = EmbeddingProviderResultV1(
            vectors=vectors,
            provider=self._provider,
            provider_request_id=(
                "fixture-"
                + hashlib.sha256(
                    (
                        f"{request.input_hash}:{request.model_configuration_version}:"
                        f"{len(request.images)}"
                    ).encode("ascii")
                ).hexdigest()
            ),
            actual_model=self._model_id,
            latency_ms=0,
            usage={"image_count": len(request.images)},
        )
        result.validate_for(request)
        return result

    def _validate_request(self, request: EmbeddingProviderRequestV1) -> None:
        if request.provider != self._provider:
            self._raise_failure(
                code="EMBEDDING_REQUEST_IDENTITY_MISMATCH",
                category="REJECTED",
                safe_message="Embedding request provider does not match the adapter",
                retryable=False,
            )
        if request.model_id != self._model_id:
            self._raise_failure(
                code="EMBEDDING_REQUEST_IDENTITY_MISMATCH",
                category="REJECTED",
                safe_message="Embedding request model does not match the adapter",
                retryable=False,
            )
        if request.pinned_revision != self._pinned_revision:
            self._raise_failure(
                code="EMBEDDING_REQUEST_IDENTITY_MISMATCH",
                category="REJECTED",
                safe_message="Embedding request revision does not match the adapter",
                retryable=False,
            )
        if request.model_configuration_version != self._model_configuration_version:
            self._raise_failure(
                code="EMBEDDING_REQUEST_IDENTITY_MISMATCH",
                category="REJECTED",
                safe_message="Embedding request configuration does not match the adapter",
                retryable=False,
            )
        if request.preprocessing_version not in self._preprocessing_versions:
            self._raise_failure(
                code="EMBEDDING_REQUEST_IDENTITY_MISMATCH",
                category="REJECTED",
                safe_message=("Embedding request preprocessing version does not match the adapter"),
                retryable=False,
            )
        if request.vector_kind.value not in {"IMAGE", "PRODUCT_FUSED"}:
            self._raise_failure(
                code="EMBEDDING_VECTOR_KIND_UNSUPPORTED",
                category="REJECTED",
                safe_message="Embedding provider does not support the requested vector kind",
                retryable=False,
            )
        if any(not 1 <= image.byte_size <= _MAXIMUM_IMAGE_BYTES for image in request.images):
            self._raise_failure(
                code="EMBEDDING_IMAGE_SIZE_UNSUPPORTED",
                category="REJECTED",
                safe_message="Embedding image exceeds the configured byte limit",
                retryable=False,
            )
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Embedding provider clock must be timezone-aware")
        if any(image.expires_at <= now for image in request.images):
            self._raise_failure(
                code="EMBEDDING_IMAGE_REFERENCE_EXPIRING",
                category="REJECTED",
                safe_message="Embedding image reference is expired",
                retryable=False,
            )

    def _raise_scenario(self) -> None:
        if self._scenario is DeterministicEmbeddingScenario.SUCCESS:
            return
        values = {
            DeterministicEmbeddingScenario.THROTTLED: (
                "EMBEDDING_THROTTLED",
                "THROTTLED",
                "Embedding provider throttled the request",
                True,
                5,
                False,
            ),
            DeterministicEmbeddingScenario.TIMEOUT: (
                "EMBEDDING_TIMEOUT",
                "TIMEOUT",
                "Embedding provider timed out before submission",
                True,
                None,
                False,
            ),
            DeterministicEmbeddingScenario.UNAVAILABLE: (
                "EMBEDDING_UNAVAILABLE",
                "UNAVAILABLE",
                "Embedding provider is temporarily unavailable",
                True,
                None,
                False,
            ),
            DeterministicEmbeddingScenario.REJECTED: (
                "EMBEDDING_REJECTED",
                "REJECTED",
                "Embedding provider rejected the request",
                False,
                None,
                False,
            ),
            DeterministicEmbeddingScenario.INVALID_RESPONSE: (
                "EMBEDDING_INVALID_RESPONSE",
                "INVALID_RESPONSE",
                "Embedding provider returned an invalid response",
                False,
                None,
                False,
            ),
            DeterministicEmbeddingScenario.UNKNOWN: (
                "EMBEDDING_SUBMISSION_OUTCOME_UNKNOWN",
                "UNKNOWN",
                "Embedding provider submission outcome is unknown",
                False,
                None,
                True,
            ),
        }
        code, category, message, retryable, retry_after, unknown = values[self._scenario]
        self._raise_failure(
            code=code,
            category=category,
            safe_message=message,
            retryable=retryable,
            retry_after_seconds=retry_after,
            outcome_unknown=unknown,
        )

    @staticmethod
    def _raise_failure(
        *,
        code: str,
        category: str,
        safe_message: str,
        retryable: bool,
        retry_after_seconds: int | None = None,
        outcome_unknown: bool = False,
    ) -> None:
        raise EmbeddingProviderFailure(
            EmbeddingProviderErrorV1(
                code=code,
                category=category,
                safe_message=safe_message,
                retryable=retryable,
                retry_after_seconds=retry_after_seconds,
                provider_request_id=None,
                outcome_unknown=outcome_unknown,
            )
        )

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(provider={self._provider!r}, "
            f"model_id={self._model_id!r}, pinned_revision={self._pinned_revision!r}, "
            "model_configuration_version="
            f"{self._model_configuration_version!r}, preprocessing_version="
            f"{self._preprocessing_version!r}, scenario={self._scenario!r})"
        )


class AlibabaEmbeddingProvider:
    """Bounded DashScope adapter for qwen3-vl-embedding vectors."""

    __slots__ = (
        "_active_lifecycles",
        "_allowed_image_origins",
        "_clock",
        "_close_lock",
        "_closed",
        "_closing",
        "_deadline",
        "_endpoint",
        "_endpoint_region",
        "_lifecycle_condition",
        "_maximum_response_bytes",
        "_model_configuration_version",
        "_model_id",
        "_pinned_revision",
        "_preprocessing_version",
        "_preprocessing_versions",
        "_transport",
    )

    def __init__(
        self,
        *,
        api_key: str | None = None,
        credential_provider: VisionApiKeyProvider | None = None,
        endpoint: str,
        endpoint_region: str,
        model_id: str,
        pinned_revision: str,
        model_configuration_version: str,
        preprocessing_version: str = "image-preprocess-v1",
        additional_preprocessing_versions: frozenset[str] = frozenset(),
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        end_to_end_timeout_seconds: float,
        maximum_concurrency: int,
        maximum_response_bytes: int,
        allowed_image_origins: frozenset[str],
        client: httpx.AsyncClient | None = None,
        clock: Any | None = None,
    ) -> None:
        if (api_key is None) == (credential_provider is None):
            raise ValueError(
                "Alibaba embedding requires exactly one static or provider credential source"
            )
        if credential_provider is None:
            assert api_key is not None
            credential_provider = StaticVisionApiKeyProvider(api_key)
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "https"
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Alibaba embedding endpoint must be a credential-free HTTPS URL")
        if parsed.path.rstrip("/") != "/api/v1":
            raise ValueError("Alibaba embedding endpoint must identify the /api/v1 base")
        if model_id != "qwen3-vl-embedding":
            raise ValueError("Alibaba vector embedding requires qwen3-vl-embedding")
        if not all(
            (
                endpoint_region,
                pinned_revision,
                model_configuration_version,
                preprocessing_version,
            )
        ):
            raise ValueError("Alibaba embedding provider identity is incomplete")
        if connect_timeout_seconds <= 0 or read_timeout_seconds <= 0:
            raise ValueError("Alibaba embedding transport timeouts must be positive")
        if end_to_end_timeout_seconds <= (connect_timeout_seconds + read_timeout_seconds):
            raise ValueError("Alibaba embedding deadline must exceed its transport timeout budget")
        if maximum_concurrency < 1:
            raise ValueError("Alibaba embedding concurrency must be positive")
        if not 1 <= maximum_response_bytes < 8 * 1024 * 1024:
            raise ValueError("Alibaba embedding response bound is invalid")
        normalized_origins = frozenset(
            self._normalize_origin(origin) for origin in allowed_image_origins
        )
        if not normalized_origins:
            raise ValueError("Alibaba embedding requires controlled image URL origins")

        self._endpoint = endpoint.rstrip("/")
        self._endpoint_region = endpoint_region
        self._model_id = model_id
        self._pinned_revision = pinned_revision
        self._model_configuration_version = model_configuration_version
        self._preprocessing_version = preprocessing_version
        self._preprocessing_versions = frozenset(
            {preprocessing_version, *additional_preprocessing_versions}
        )
        if any(not value or value != value.strip() for value in self._preprocessing_versions):
            raise ValueError("Alibaba embedding preprocessing identity is invalid")
        self._deadline = end_to_end_timeout_seconds
        self._maximum_response_bytes = maximum_response_bytes
        self._allowed_image_origins = normalized_origins
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lifecycle_condition = threading.Condition()
        self._close_lock = threading.Lock()
        self._active_lifecycles = 0
        self._closing = False
        self._closed = False
        self._transport = AsyncEmbeddingHttpTransport(
            credential_provider=credential_provider,
            endpoint=self._endpoint,
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=read_timeout_seconds,
            maximum_concurrency=maximum_concurrency,
            maximum_response_bytes=maximum_response_bytes,
            client=client,
        )

    def embed(self, request: EmbeddingProviderRequestV1) -> EmbeddingProviderResultV1:
        entered = False
        failure_error: EmbeddingProviderErrorV1 | None = None
        try:
            with self._lifecycle_condition:
                if self._closing:
                    raise EmbeddingProviderFailure(
                        EmbeddingProviderErrorV1(
                            code="EMBEDDING_UNAVAILABLE",
                            category="UNAVAILABLE",
                            safe_message="Embedding provider is temporarily unavailable",
                            retryable=True,
                            retry_after_seconds=None,
                            provider_request_id=None,
                            outcome_unknown=False,
                        )
                    )
                self._transport.assert_ready()
                self._active_lifecycles += 1
                entered = True
            return self._embed(request)
        except EmbeddingProviderFailure as failure:
            failure_error = failure.error
        except VisionSafeToRetryTransportError:
            failure_error = EmbeddingProviderErrorV1(
                code="EMBEDDING_UNAVAILABLE",
                category="UNAVAILABLE",
                safe_message="Embedding provider is temporarily unavailable",
                retryable=True,
                retry_after_seconds=None,
                provider_request_id=None,
                outcome_unknown=False,
            )
        finally:
            if entered:
                with self._lifecycle_condition:
                    self._active_lifecycles -= 1
                    self._lifecycle_condition.notify_all()
        assert failure_error is not None
        raise EmbeddingProviderFailure(failure_error) from None

    def _embed(self, request: EmbeddingProviderRequestV1) -> EmbeddingProviderResultV1:
        self._validate_request(request)
        started = time.monotonic()
        deadline_at = started + self._deadline
        contents = []
        if request.controlled_text is not None:
            contents.append({"text": request.controlled_text})
        contents.extend({"image": image.url.get_secret_value()} for image in request.images)
        request_bytes = json.dumps(
            {
                "model": self._model_id,
                "input": {"contents": contents},
                "parameters": {
                    "dimension": request.expected_dimension,
                    "enable_fusion": request.vector_kind.value == "PRODUCT_FUSED",
                    "output_type": "dense",
                },
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            evidence = self._transport.send(
                request_bytes,
                deadline_at=deadline_at,
            )
        except EmbeddingHeadersObservedOutcomeUnknownError as exc:
            self._raise_failure(
                code="EMBEDDING_RESPONSE_COMPLETION_UNKNOWN",
                category="UNKNOWN",
                safe_message="Embedding provider response completion is unknown",
                retryable=False,
                provider_request_id=exc.provider_request_id,
                outcome_unknown=True,
            )
        except VisionSubmissionOutcomeUnknownError:
            self._raise_failure(
                code="EMBEDDING_SUBMISSION_OUTCOME_UNKNOWN",
                category="UNKNOWN",
                safe_message="Embedding provider submission outcome is unknown",
                retryable=False,
                outcome_unknown=True,
            )
        except EmbeddingPreDispatchCancelledTransportError:
            self._raise_failure(
                code="EMBEDDING_CANCELLED_BEFORE_SUBMISSION",
                category="UNAVAILABLE",
                safe_message="Embedding provider call ended before submission",
                retryable=True,
            )
        except VisionCredentialUnavailableTransportError:
            self._raise_failure(
                code="EMBEDDING_CREDENTIAL_UNAVAILABLE",
                category="AUTHENTICATION",
                safe_message="Embedding provider credential is temporarily unavailable",
                retryable=True,
            )
        except EmbeddingPreSubmissionTimeoutTransportError:
            self._raise_failure(
                code="EMBEDDING_TIMEOUT",
                category="TIMEOUT",
                safe_message="Embedding provider timed out before submission",
                retryable=True,
            )
        except VisionSafeToRetryTransportError:
            timed_out = time.monotonic() >= deadline_at
            self._raise_failure(
                code="EMBEDDING_TIMEOUT" if timed_out else "EMBEDDING_UNAVAILABLE",
                category="TIMEOUT" if timed_out else "UNAVAILABLE",
                safe_message=(
                    "Embedding provider timed out before submission"
                    if timed_out
                    else "Embedding provider is temporarily unavailable"
                ),
                retryable=True,
            )
        response = evidence.response
        request_id = self._response_request_id(response)
        if response.status_code != 200:
            self._raise_http_failure(response, request_id=request_id)
        if evidence.completion_uncertain:
            self._raise_failure(
                code="EMBEDDING_RESPONSE_COMPLETION_UNKNOWN",
                category="UNKNOWN",
                safe_message="Embedding provider response completion is unknown",
                retryable=False,
                provider_request_id=request_id,
                outcome_unknown=True,
            )
        if evidence.body_too_large:
            self._raise_failure(
                code="EMBEDDING_RESPONSE_TOO_LARGE",
                category="INVALID_RESPONSE",
                safe_message="Embedding provider response exceeded the configured bound",
                retryable=False,
                provider_request_id=request_id,
            )
        if time.monotonic() >= deadline_at:
            self._raise_failure(
                code="EMBEDDING_POST_SUBMISSION_DEADLINE_EXCEEDED",
                category="UNKNOWN",
                safe_message="Embedding provider completion crossed the request deadline",
                retryable=False,
                provider_request_id=request_id,
                outcome_unknown=True,
            )
        try:
            result = self._normalize_success(
                response,
                request=request,
                latency_ms=self._latency(started),
                header_request_id=request_id,
            )
            result.validate_for(request)
        except EmbeddingProviderFailure:
            raise
        except (OverflowError, RecursionError, TypeError, ValueError, ValidationError):
            self._raise_failure(
                code="EMBEDDING_INVALID_RESPONSE",
                category="INVALID_RESPONSE",
                safe_message="Embedding provider returned an invalid response",
                retryable=False,
                provider_request_id=request_id,
            )
        return result

    def _validate_request(self, request: EmbeddingProviderRequestV1) -> None:
        mismatches = (
            (request.provider, _PROVIDER, "provider"),
            (request.model_id, self._model_id, "model"),
            (request.pinned_revision, self._pinned_revision, "revision"),
            (
                request.model_configuration_version,
                self._model_configuration_version,
                "configuration",
            ),
        )
        for actual, expected, label in mismatches:
            if actual != expected:
                self._raise_failure(
                    code="EMBEDDING_REQUEST_IDENTITY_MISMATCH",
                    category="REJECTED",
                    safe_message=f"Embedding request {label} does not match the adapter",
                    retryable=False,
                )
        if request.preprocessing_version not in self._preprocessing_versions:
            self._raise_failure(
                code="EMBEDDING_REQUEST_IDENTITY_MISMATCH",
                category="REJECTED",
                safe_message=("Embedding request preprocessing version does not match the adapter"),
                retryable=False,
            )
        if request.vector_kind.value not in {"IMAGE", "PRODUCT_FUSED"}:
            self._raise_failure(
                code="EMBEDDING_VECTOR_KIND_UNSUPPORTED",
                category="REJECTED",
                safe_message="Embedding provider does not support the requested vector kind",
                retryable=False,
            )
        if request.expected_dimension not in _QWEN3_DIMENSIONS:
            self._raise_failure(
                code="EMBEDDING_DIMENSION_UNSUPPORTED",
                category="REJECTED",
                safe_message="Embedding dimension is unsupported by the configured model",
                retryable=False,
            )
        if len(request.images) > 5:
            self._raise_failure(
                code="EMBEDDING_IMAGE_LIMIT_EXCEEDED",
                category="REJECTED",
                safe_message="Embedding request exceeds the configured image limit",
                retryable=False,
            )
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Embedding provider clock must be timezone-aware")
        minimum_expiry = now + timedelta(seconds=self._deadline)
        for image in request.images:
            if not 1 <= image.byte_size <= _MAXIMUM_IMAGE_BYTES:
                self._raise_failure(
                    code="EMBEDDING_IMAGE_SIZE_UNSUPPORTED",
                    category="REJECTED",
                    safe_message="Embedding image exceeds the configured byte limit",
                    retryable=False,
                )
            raw_url = image.url.get_secret_value()
            if len(raw_url.encode("utf-8")) > 8192:
                self._raise_failure(
                    code="EMBEDDING_IMAGE_URL_INVALID",
                    category="REJECTED",
                    safe_message="Embedding image URL exceeds the configured bound",
                    retryable=False,
                )
            try:
                origin = self._normalize_origin(raw_url)
            except ValueError:
                self._raise_failure(
                    code="EMBEDDING_IMAGE_URL_INVALID",
                    category="REJECTED",
                    safe_message="Embedding image URL is invalid",
                    retryable=False,
                )
            if origin not in self._allowed_image_origins:
                self._raise_failure(
                    code="EMBEDDING_IMAGE_ORIGIN_REJECTED",
                    category="REJECTED",
                    safe_message="Embedding image URL is outside the controlled origin allowlist",
                    retryable=False,
                )
            if image.expires_at < minimum_expiry:
                self._raise_failure(
                    code="EMBEDDING_IMAGE_REFERENCE_EXPIRING",
                    category="REJECTED",
                    safe_message=(
                        "Embedding image URL does not cover the provider request deadline"
                    ),
                    retryable=False,
                )
            if image.required_headers:
                self._raise_failure(
                    code="EMBEDDING_IMAGE_HEADERS_UNSUPPORTED",
                    category="REJECTED",
                    safe_message=("Alibaba embedding image URLs cannot require forwarding headers"),
                    retryable=False,
                )

    def _normalize_success(
        self,
        response: httpx.Response,
        *,
        request: EmbeddingProviderRequestV1,
        latency_ms: int,
        header_request_id: str | None,
    ) -> EmbeddingProviderResultV1:
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError
        request_id = self._opaque_request_id(payload.get("request_id"))
        if header_request_id is not None and request_id != header_request_id:
            raise ValueError
        output = payload.get("output")
        if not isinstance(output, dict):
            raise ValueError
        raw_embeddings = output.get("embeddings")
        if not isinstance(raw_embeddings, list):
            raise ValueError
        by_index: dict[int, EmbeddingVectorV1] = {}
        expected_type = "fusion" if request.vector_kind.value == "PRODUCT_FUSED" else "vl"
        for item in raw_embeddings:
            if not isinstance(item, dict) or item.get("type") != expected_type:
                raise ValueError
            index = self._nonnegative_int(item.get("index"))
            if index in by_index:
                raise ValueError
            raw_vector = item.get("embedding")
            if not isinstance(raw_vector, list):
                raise ValueError
            values: list[float] = []
            for value in raw_vector:
                if not isinstance(value, int | float) or isinstance(value, bool):
                    raise ValueError
                normalized = float(value)
                if not math.isfinite(normalized) or abs(normalized) > _MAXIMUM_FLOAT32:
                    raise ValueError
                values.append(normalized)
            by_index[index] = EmbeddingVectorV1(values=values)
        if set(by_index) != set(range(len(request.images))):
            raise ValueError
        usage = self._normalize_usage(payload.get("usage"))
        return EmbeddingProviderResultV1(
            vectors=[by_index[index] for index in range(len(request.images))],
            provider=_PROVIDER,
            provider_request_id=request_id,
            # The official API does not expose a resolved model revision.
            actual_model=self._model_id,
            latency_ms=latency_ms,
            usage=usage,
        )

    @classmethod
    def _normalize_usage(cls, raw: object) -> dict[str, int]:
        if not isinstance(raw, dict):
            raise ValueError
        usage = {
            name: cls._nonnegative_int(raw.get(name))
            for name in ("input_tokens", "image_tokens", "total_tokens")
        }
        if usage["total_tokens"] != usage["input_tokens"] + usage["image_tokens"]:
            raise ValueError
        return usage

    def _raise_http_failure(
        self,
        response: httpx.Response,
        *,
        request_id: str | None,
    ) -> None:
        retry_after = self._retry_after(response)
        if response.status_code == 429:
            self._raise_failure(
                code="EMBEDDING_THROTTLED",
                category="THROTTLED",
                safe_message="Embedding provider throttled the request",
                retryable=True,
                retry_after_seconds=retry_after,
                provider_request_id=request_id,
            )
        if response.status_code == 408:
            self._raise_failure(
                code="EMBEDDING_TIMEOUT",
                category="TIMEOUT",
                safe_message="Embedding provider timed out while processing the request",
                retryable=True,
                retry_after_seconds=retry_after,
                provider_request_id=request_id,
            )
        if response.status_code >= 500 and self._provider_error_code(response) in {
            "InternalError.Timeout",
            "RequestTimeOut",
        }:
            self._raise_failure(
                code="EMBEDDING_TIMEOUT",
                category="TIMEOUT",
                safe_message="Embedding provider timed out while processing the request",
                retryable=True,
                retry_after_seconds=retry_after,
                provider_request_id=request_id,
            )
        if response.status_code >= 500:
            self._raise_failure(
                code="EMBEDDING_UNAVAILABLE",
                category="UNAVAILABLE",
                safe_message="Embedding provider is temporarily unavailable",
                retryable=True,
                retry_after_seconds=retry_after,
                provider_request_id=request_id,
            )
        self._raise_failure(
            code="EMBEDDING_REJECTED",
            category=("AUTHENTICATION" if response.status_code in {401, 403} else "REJECTED"),
            safe_message="Embedding provider rejected the request",
            retryable=False,
            provider_request_id=request_id,
        )

    @staticmethod
    def _raise_failure(
        *,
        code: str,
        category: str,
        safe_message: str,
        retryable: bool,
        retry_after_seconds: int | None = None,
        provider_request_id: str | None = None,
        outcome_unknown: bool = False,
    ) -> None:
        raise EmbeddingProviderFailure(
            EmbeddingProviderErrorV1(
                code=code,
                category=category,
                safe_message=safe_message,
                retryable=retryable,
                retry_after_seconds=retry_after_seconds,
                provider_request_id=provider_request_id,
                outcome_unknown=outcome_unknown,
            )
        )

    def _response_request_id(self, response: httpx.Response) -> str | None:
        for name in ("x-request-id", "x-dashscope-request-id"):
            value = response.headers.get(name)
            if value:
                return self._opaque_request_id(value)
        if not response.content:
            return None
        try:
            payload = response.json()
        except (RecursionError, ValueError):
            return None
        if not isinstance(payload, dict) or payload.get("request_id") is None:
            return None
        try:
            return self._opaque_request_id(payload.get("request_id"))
        except ValueError:
            return None

    @staticmethod
    def _provider_error_code(response: httpx.Response) -> str | None:
        if not response.content:
            return None
        try:
            payload = response.json()
        except (RecursionError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        value = payload.get("code")
        if not isinstance(value, str) or not 1 <= len(value) <= 128:
            return None
        return value

    @staticmethod
    def _opaque_request_id(value: object) -> str:
        if not isinstance(value, str):
            raise ValueError
        normalized = value.strip()
        if not normalized:
            raise ValueError
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", normalized):
            return normalized
        return f"sha256:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()}"

    @staticmethod
    def _normalize_origin(value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ValueError("Embedding image origins must be credential-free HTTPS origins")
        port = parsed.port or 443
        suffix = f":{port}" if port != 443 else ""
        return f"https://{parsed.hostname.lower()}{suffix}"

    def _retry_after(self, response: httpx.Response) -> int | None:
        raw = response.headers.get("retry-after")
        if raw is None:
            return None
        try:
            value = int(raw)
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(raw)
            except (OverflowError, TypeError, ValueError):
                return None
            if retry_at.tzinfo is None or retry_at.utcoffset() is None:
                return None
            value = math.ceil((retry_at - self._clock()).total_seconds())
        return min(max(value, 0), 86_400)

    @staticmethod
    def _nonnegative_int(value: object) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError
        return value

    @staticmethod
    def _latency(started: float) -> int:
        return max(0, round((time.monotonic() - started) * 1000))

    def assert_ready(self) -> str:
        with self._lifecycle_condition:
            if self._closing:
                raise ConnectionError("Alibaba embedding provider is unavailable")
        try:
            return self._transport.assert_ready()
        except Exception:
            pass
        raise ConnectionError("Alibaba embedding provider is unavailable")

    @property
    def shutdown_drained(self) -> bool:
        with self._lifecycle_condition:
            return self._closed and self._active_lifecycles == 0

    def close(self) -> None:
        with self._close_lock:
            with self._lifecycle_condition:
                if self._closed:
                    return
                self._closing = True
            shutdown_deadline = time.monotonic() + self._deadline
            failures: list[Exception] = []
            try:
                self._transport.close()
            except Exception:
                failures.append(ConnectionError("Alibaba embedding transport shutdown failed"))
            with self._lifecycle_condition:
                while self._active_lifecycles:
                    remaining = shutdown_deadline - time.monotonic()
                    if remaining <= 0:
                        failures.append(
                            TimeoutError(
                                "Alibaba embedding provider shutdown timed out with active calls"
                            )
                        )
                        break
                    self._lifecycle_condition.wait(remaining)
                self._closed = True
            if failures:
                raise ExceptionGroup("Alibaba embedding provider shutdown failed", failures)

    def __enter__(self) -> AlibabaEmbeddingProvider:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(endpoint_region={self._endpoint_region!r}, "
            f"model_id={self._model_id!r}, pinned_revision={self._pinned_revision!r}, "
            "model_configuration_version="
            f"{self._model_configuration_version!r}, "
            f"preprocessing_version={self._preprocessing_version!r})"
        )
