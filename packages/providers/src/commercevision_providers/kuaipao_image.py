"""Bounded synchronous OpenAI Images adapter for Kuaipao."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import time
import warnings
from collections.abc import Callable
from datetime import UTC, datetime
from io import BytesIO
from typing import Any
from urllib.parse import urlsplit

import httpx
from commercevision_contracts.image_provider import (
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
    NormalizedImageProviderOutcome,
)
from PIL import Image, UnidentifiedImageError

from .vision_credentials import VisionApiKeyProvider
from .vision_transport import (
    AsyncVisionHttpTransport,
    VisionSafeToRetryTransportError,
    VisionSubmissionOutcomeUnknownError,
)

_OUTPUT_MEDIA_TYPES = {
    ImageProviderOutputFormat.PNG: ImageProviderMediaType.PNG,
    ImageProviderOutputFormat.JPEG: ImageProviderMediaType.JPEG,
    ImageProviderOutputFormat.WEBP: ImageProviderMediaType.WEBP,
}
_CONFIRMED_CLIENT_ERRORS = {
    400: (ImageProviderErrorCategory.INVALID_REQUEST, "PROVIDER_INVALID_REQUEST"),
    401: (ImageProviderErrorCategory.AUTHENTICATION, "PROVIDER_AUTHENTICATION_FAILED"),
    403: (ImageProviderErrorCategory.AUTHENTICATION, "PROVIDER_ACCESS_DENIED"),
    413: (ImageProviderErrorCategory.INVALID_REQUEST, "PROVIDER_REQUEST_TOO_LARGE"),
}
_MEDIA_IMAGE_FORMATS = {
    ImageProviderMediaType.PNG: "PNG",
    ImageProviderMediaType.JPEG: "JPEG",
    ImageProviderMediaType.WEBP: "WEBP",
}
_MAXIMUM_DECODED_PIXELS = 64_000_000


class KuaipaoSyncImageAdapter:
    """Submit one synchronous, server-configured Kuaipao image generation."""

    def __init__(
        self,
        *,
        credential_provider: VisionApiKeyProvider,
        endpoint: str,
        endpoint_region: str,
        allowed_hosts: frozenset[str],
        allowed_regions: frozenset[str],
        model: str,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        maximum_concurrency: int,
        maximum_response_bytes: int,
        maximum_result_bytes: int,
        allowed_result_hosts: frozenset[str] = frozenset(),
        client: httpx.AsyncClient | None = None,
        wall_clock: Callable[[], datetime] | None = None,
    ) -> None:
        parsed = urlsplit(endpoint)
        normalized_hosts = frozenset(host.lower() for host in allowed_hosts)
        if (
            parsed.scheme != "https"
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.port not in {None, 443}
            or parsed.path.rstrip("/") != "/v1"
            or parsed.hostname.lower() not in normalized_hosts
        ):
            raise ValueError("Kuaipao endpoint must use an allowed credential-free HTTPS host")
        if not endpoint_region or endpoint_region not in allowed_regions:
            raise ValueError("Kuaipao endpoint region is not allowed")
        if not model or len(model.encode("utf-8")) > 256:
            raise ValueError("Kuaipao model is invalid")
        if connect_timeout_seconds <= 0 or read_timeout_seconds <= 0:
            raise ValueError("Kuaipao transport timeouts must be positive")
        if maximum_concurrency < 1:
            raise ValueError("Kuaipao concurrency must be positive")
        if not 1 <= maximum_response_bytes <= 64 * 1024 * 1024:
            raise ValueError("Kuaipao response bound is invalid")
        if not 1 <= maximum_result_bytes <= 32 * 1024 * 1024:
            raise ValueError("Kuaipao result bound is invalid")
        if client is not None and client.follow_redirects:
            raise ValueError("Kuaipao HTTP redirects must remain disabled")
        normalized_result_hosts = frozenset(host.lower() for host in allowed_result_hosts)
        if any(
            not host or len(host) > 253 or any(character in host for character in "/@:#?[]")
            for host in normalized_result_hosts
        ):
            raise ValueError("Kuaipao result host allowlist is invalid")

        self._model = model
        self._allowed_result_hosts = normalized_result_hosts
        self._maximum_result_bytes = maximum_result_bytes
        self._wall_clock = wall_clock or (lambda: datetime.now(UTC))
        self._transport = AsyncVisionHttpTransport(
            credential_provider=credential_provider,
            endpoint=endpoint.rstrip("/"),
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=read_timeout_seconds,
            maximum_concurrency=maximum_concurrency,
            maximum_response_bytes=maximum_response_bytes,
            request_path="/images/generations",
            client=client,
        )

    def submit(self, request: ImageProviderSubmitRequest) -> NormalizedImageProviderOutcome:
        if not isinstance(request, ImageGenerationProviderRequest):
            return self._disabled_operation(
                identity=None,
                code="SYNCHRONOUS_EDITING_DISABLED",
            )
        if request.negative_prompt_text is not None or request.reference_images:
            return self._disabled_operation(
                identity=None,
                code="UNVERIFIED_GENERATION_CAPABILITY",
            )
        if request.media.seed is not None:
            return self._disabled_operation(
                identity=None,
                code="UNVERIFIED_GENERATION_CAPABILITY",
            )
        if request.media.width * request.media.height > _MAXIMUM_DECODED_PIXELS:
            return self._disabled_operation(
                identity=None,
                code="RESULT_DIMENSION_LIMIT_EXCEEDED",
            )

        started_at = time.monotonic()
        remaining_seconds = (request.deadline - self._wall_clock()).total_seconds()
        deadline_at = time.monotonic() + max(0.0, remaining_seconds)
        payload = json.dumps(
            {
                "model": self._model,
                "prompt": request.prompt_text,
                "n": 1,
                "size": f"{request.media.width}x{request.media.height}",
                "response_format": "b64_json",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            evidence = self._transport.send(
                payload,
                deadline_at=deadline_at,
            )
        except VisionSafeToRetryTransportError:
            return NormalizedImageProviderOutcome(
                call_outcome=ImageProviderCallOutcome.SAFE_TO_RETRY_PRE_DISPATCH,
                task_state=None,
                identity=None,
                result=None,
                usage=None,
                error=ImageProviderError(
                    category=ImageProviderErrorCategory.PROVIDER_UNAVAILABLE,
                    code="PRE_DISPATCH_TRANSPORT_FAILURE",
                    retry_after_seconds=None,
                ),
                latency_ms=self._latency_ms(started_at),
            )
        except VisionSubmissionOutcomeUnknownError:
            return NormalizedImageProviderOutcome(
                call_outcome=ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH,
                task_state=None,
                identity=None,
                result=None,
                usage=None,
                error=ImageProviderError(
                    category=ImageProviderErrorCategory.TIMEOUT,
                    code="PROVIDER_RESPONSE_LOST",
                    retry_after_seconds=None,
                ),
                latency_ms=self._latency_ms(started_at),
            )
        response = evidence.response
        if evidence.body_too_large:
            return self._unknown_response(
                response=response,
                code="PROVIDER_RESPONSE_TOO_LARGE",
                started_at=started_at,
            )
        if evidence.completion_uncertain:
            return self._unknown_response(
                response=response,
                code="PROVIDER_RESPONSE_INCOMPLETE",
                started_at=started_at,
            )
        if response.is_redirect:
            return self._unknown_response(
                response=response,
                code="PROVIDER_REDIRECT_REJECTED",
                started_at=started_at,
            )
        confirmed_error = _CONFIRMED_CLIENT_ERRORS.get(response.status_code)
        if confirmed_error is not None:
            category, code = confirmed_error
            return self._confirmed_failure(
                response=response,
                category=category,
                code=code,
                started_at=started_at,
            )
        if response.status_code in {429, 500, 503}:
            return self._unknown_response(
                response=response,
                category=ImageProviderErrorCategory.PROVIDER_UNAVAILABLE,
                code="PROVIDER_STATUS_UNCERTAIN",
                started_at=started_at,
            )
        if response.status_code != 200:
            return self._unknown_response(
                response=response,
                category=ImageProviderErrorCategory.PROVIDER_UNAVAILABLE,
                code="PROVIDER_STATUS_UNEXPECTED",
                started_at=started_at,
            )
        try:
            document: Any = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._unknown_response(
                response=response,
                code="PROVIDER_RESPONSE_MALFORMED",
                started_at=started_at,
            )
        encoded_result: str | None = None
        result_url: str | None = None
        if isinstance(document, dict):
            data = document.get("data")
            if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
                candidate = data[0].get("b64_json")
                if isinstance(candidate, str):
                    encoded_result = candidate
                candidate_url = data[0].get("url")
                if isinstance(candidate_url, str):
                    result_url = candidate_url
        if encoded_result is None and result_url is not None:
            content = self._download_result(
                result_url,
                provider_response=response,
                expected_media_type=_OUTPUT_MEDIA_TYPES[request.media.output_format],
                deadline_at=deadline_at,
                started_at=started_at,
            )
            if isinstance(content, NormalizedImageProviderOutcome):
                return content
        else:
            content = None
        maximum_encoded_bytes = 4 * ((self._maximum_result_bytes + 2) // 3)
        if content is None and (
            encoded_result is None or not 1 <= len(encoded_result) <= maximum_encoded_bytes
        ):
            return self._unknown_response(
                response=response,
                code="PROVIDER_RESPONSE_MALFORMED",
                started_at=started_at,
            )
        if content is None:
            assert encoded_result is not None
            try:
                content = base64.b64decode(encoded_result, validate=True)
            except (binascii.Error, UnicodeEncodeError, ValueError):
                return self._unknown_response(
                    response=response,
                    code="PROVIDER_RESPONSE_MALFORMED",
                    started_at=started_at,
                )
        if not content or len(content) > self._maximum_result_bytes:
            return self._unknown_response(
                response=response,
                code="PROVIDER_RESPONSE_MALFORMED",
                started_at=started_at,
            )
        expected_media_type = _OUTPUT_MEDIA_TYPES[request.media.output_format]
        if not self._is_valid_image(
            content,
            expected_media_type=expected_media_type,
            expected_width=request.media.width,
            expected_height=request.media.height,
        ):
            return self._unknown_response(
                response=response,
                code="PROVIDER_RESULT_MEDIA_INVALID",
                started_at=started_at,
            )
        identity = self._response_identity(response)
        if identity is None or identity.provider_request_id is None:
            return self._unknown_response(
                response=response,
                code="PROVIDER_IDENTITY_MISSING_OR_INVALID",
                started_at=started_at,
            )
        request_id = identity.provider_request_id
        result = ImageProviderResult(
            provider_result_id=f"{request_id}:0",
            content=content,
            content_sha256=hashlib.sha256(content).hexdigest(),
            media_type=expected_media_type,
            width=request.media.width,
            height=request.media.height,
        )
        return NormalizedImageProviderOutcome(
            call_outcome=ImageProviderCallOutcome.CONFIRMED_SUCCESS,
            task_state=ImageProviderTaskState.SUCCEEDED,
            identity=identity,
            result=result,
            usage=None,
            error=None,
            latency_ms=self._latency_ms(started_at),
        )

    @staticmethod
    def _latency_ms(started_at: float) -> int:
        return min(86_400_000, max(0, int((time.monotonic() - started_at) * 1000)))

    def _unknown_response(
        self,
        *,
        response: httpx.Response,
        code: str,
        started_at: float,
        category: ImageProviderErrorCategory = ImageProviderErrorCategory.MALFORMED_RESPONSE,
    ) -> NormalizedImageProviderOutcome:
        return NormalizedImageProviderOutcome(
            call_outcome=ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH,
            task_state=None,
            identity=self._response_identity(response),
            result=None,
            usage=None,
            error=ImageProviderError(
                category=category,
                code=code,
                retry_after_seconds=None,
            ),
            latency_ms=self._latency_ms(started_at),
        )

    def _confirmed_failure(
        self,
        *,
        response: httpx.Response,
        category: ImageProviderErrorCategory,
        code: str,
        started_at: float,
    ) -> NormalizedImageProviderOutcome:
        return NormalizedImageProviderOutcome(
            call_outcome=ImageProviderCallOutcome.CONFIRMED_FAILURE,
            task_state=ImageProviderTaskState.FAILED,
            identity=self._response_identity(response),
            result=None,
            usage=None,
            error=ImageProviderError(
                category=category,
                code=code,
                retry_after_seconds=None,
            ),
            latency_ms=self._latency_ms(started_at),
        )

    @staticmethod
    def _response_identity(response: httpx.Response) -> ImageProviderRequestIdentity | None:
        request_id = response.headers.get("X-Oneapi-Request-Id")
        if not request_id:
            return None
        try:
            return ImageProviderRequestIdentity(
                provider_request_id=request_id,
                provider_task_id=None,
            )
        except ValueError:
            return None

    def close(self) -> None:
        self._transport.close()

    def _download_result(
        self,
        url: str,
        *,
        provider_response: httpx.Response,
        expected_media_type: ImageProviderMediaType,
        deadline_at: float,
        started_at: float,
    ) -> bytes | NormalizedImageProviderOutcome:
        if len(url.encode("utf-8")) > 4096:
            return self._unknown_response(
                response=provider_response,
                code="PROVIDER_RESULT_URL_REJECTED",
                started_at=started_at,
            )
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or parsed.port not in {None, 443}
            or parsed.hostname.lower() not in self._allowed_result_hosts
        ):
            return self._unknown_response(
                response=provider_response,
                code="PROVIDER_RESULT_URL_REJECTED",
                started_at=started_at,
            )
        try:
            evidence = self._transport.fetch(
                url,
                deadline_at=deadline_at,
                maximum_response_bytes=self._maximum_result_bytes,
            )
        except (VisionSafeToRetryTransportError, VisionSubmissionOutcomeUnknownError):
            return self._unknown_response(
                response=provider_response,
                category=ImageProviderErrorCategory.PROVIDER_UNAVAILABLE,
                code="PROVIDER_RESULT_FETCH_UNCERTAIN",
                started_at=started_at,
            )
        if (
            evidence.body_too_large
            or evidence.completion_uncertain
            or evidence.response.is_redirect
            or evidence.response.status_code != 200
            or not evidence.response.content
        ):
            return self._unknown_response(
                response=provider_response,
                code="PROVIDER_RESULT_FETCH_INVALID",
                started_at=started_at,
            )
        content_type = evidence.response.headers.get("Content-Type", "")
        if content_type.partition(";")[0].strip().lower() != expected_media_type.value:
            return self._unknown_response(
                response=provider_response,
                code="PROVIDER_RESULT_MEDIA_INVALID",
                started_at=started_at,
            )
        return evidence.response.content

    @staticmethod
    def _is_valid_image(
        content: bytes,
        *,
        expected_media_type: ImageProviderMediaType,
        expected_width: int,
        expected_height: int,
    ) -> bool:
        expected_format = _MEDIA_IMAGE_FORMATS[expected_media_type]
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(content)) as probe:
                    if (
                        probe.format != expected_format
                        or probe.size != (expected_width, expected_height)
                        or int(getattr(probe, "n_frames", 1)) != 1
                        or probe.width * probe.height > _MAXIMUM_DECODED_PIXELS
                    ):
                        return False
                    probe.verify()
                with Image.open(BytesIO(content)) as image:
                    if (
                        image.format != expected_format
                        or image.size != (expected_width, expected_height)
                        or int(getattr(image, "n_frames", 1)) != 1
                    ):
                        return False
                    image.load()
        except (
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
            UnidentifiedImageError,
            OSError,
            SyntaxError,
            ValueError,
        ):
            return False
        return True

    def query(self, request: ImageProviderQueryRequest) -> NormalizedImageProviderOutcome:
        if not isinstance(request, ImageProviderQueryRequest):
            raise ValueError("Kuaipao query request is invalid")
        return self._disabled_operation(
            identity=request.identity,
            code="SYNCHRONOUS_QUERY_DISABLED",
        )

    def cancel(self, request: ImageProviderCancelRequest) -> NormalizedImageProviderOutcome:
        if not isinstance(request, ImageProviderCancelRequest):
            raise ValueError("Kuaipao cancellation request is invalid")
        return self._disabled_operation(
            identity=request.identity,
            code="SYNCHRONOUS_CANCEL_DISABLED",
        )

    @staticmethod
    def _disabled_operation(
        *,
        identity: ImageProviderRequestIdentity | None,
        code: str,
    ) -> NormalizedImageProviderOutcome:
        return NormalizedImageProviderOutcome(
            call_outcome=ImageProviderCallOutcome.CONFIRMED_FAILURE,
            task_state=ImageProviderTaskState.FAILED,
            identity=identity,
            result=None,
            usage=None,
            error=ImageProviderError(
                category=ImageProviderErrorCategory.INVALID_REQUEST,
                code=code,
                retry_after_seconds=None,
            ),
            latency_ms=0,
        )
