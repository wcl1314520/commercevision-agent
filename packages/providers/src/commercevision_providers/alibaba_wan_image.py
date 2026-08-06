"""Region-bound asynchronous Alibaba Model Studio Wan image adapter."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
from commercevision_contracts.image_provider import (
    ControlledImageInput,
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
from PIL import Image, UnidentifiedImageError

from .vision_credentials import VisionApiKeyProvider
from .vision_transport import (
    AsyncVisionHttpTransport,
    VisionSafeToRetryTransportError,
    VisionSubmissionOutcomeUnknownError,
)

_WORKSPACE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,62}$", re.ASCII)
_REGION_HOST_SUFFIXES = {
    "cn-beijing": "cn-beijing.maas.aliyuncs.com",
    "ap-southeast-1": "ap-southeast-1.maas.aliyuncs.com",
}
_MODELS = {"wan2.7-image", "wan2.7-image-pro"}
_MAXIMUM_DECODED_PIXELS = 64_000_000
_MAXIMUM_INPUT_BYTES = 20 * 1024 * 1024
_MAXIMUM_SEED = 2_147_483_647
_INPUT_MEDIA_FORMATS = {
    ImageProviderMediaType.PNG: "PNG",
    ImageProviderMediaType.JPEG: "JPEG",
    ImageProviderMediaType.WEBP: "WEBP",
}


class ControlledImageInputUnavailableError(RuntimeError):
    """Controlled input bytes could not be resolved before provider dispatch."""


class ControlledImageInputResolver(Protocol):
    def resolve(
        self,
        image: ControlledImageInput,
        *,
        maximum_bytes: int,
        deadline: datetime,
    ) -> bytes: ...


@dataclass(frozen=True, slots=True)
class AlibabaWanEndpointIdentity:
    """Credential-free identity matched against a published endpoint capability."""

    endpoint_host: str
    endpoint_region: str
    workspace_id: str
    model: str
    protocol_mode: str
    adapter_version: str
    configuration_sha256: str


class AlibabaWanAsyncImageAdapter:
    """Submit Wan 2.7 image tasks through its native asynchronous protocol."""

    def __init__(
        self,
        *,
        credential_provider: VisionApiKeyProvider,
        input_resolver: ControlledImageInputResolver | None = None,
        endpoint: str,
        endpoint_region: str,
        workspace_id: str,
        model: str,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        maximum_concurrency: int,
        maximum_response_bytes: int,
        maximum_result_bytes: int,
        allowed_result_hosts: frozenset[str],
        client: httpx.AsyncClient | None = None,
        wall_clock: Callable[[], datetime] | None = None,
    ) -> None:
        suffix = _REGION_HOST_SUFFIXES.get(endpoint_region)
        parsed = urlsplit(endpoint)
        if (
            suffix is None
            or _WORKSPACE_ID.fullmatch(workspace_id) is None
            or parsed.scheme != "https"
            or parsed.hostname != f"{workspace_id}.{suffix}"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 443}
            or parsed.path.rstrip("/") != "/api/v1"
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Alibaba Wan endpoint must match its supported workspace region")
        if model not in _MODELS:
            raise ValueError("Alibaba Wan model is unsupported")
        if connect_timeout_seconds <= 0 or read_timeout_seconds <= 0:
            raise ValueError("Alibaba Wan transport timeouts must be positive")
        if maximum_concurrency < 1:
            raise ValueError("Alibaba Wan concurrency must be positive")
        if not 1 <= maximum_response_bytes <= 64 * 1024 * 1024:
            raise ValueError("Alibaba Wan response bound is invalid")
        if not 1 <= maximum_result_bytes <= 32 * 1024 * 1024:
            raise ValueError("Alibaba Wan result bound is invalid")
        normalized_result_hosts = frozenset(host.lower() for host in allowed_result_hosts)
        if any(
            not host or len(host) > 253 or any(character in host for character in "/@:#?[]")
            for host in normalized_result_hosts
        ):
            raise ValueError("Alibaba Wan result host allowlist is invalid")
        if client is not None and client.follow_redirects:
            raise ValueError("Alibaba Wan HTTP redirects must remain disabled")

        endpoint = endpoint.rstrip("/")
        configuration = {
            "adapter_version": "alibaba-wan-async-v1",
            "allowed_result_hosts": sorted(normalized_result_hosts),
            "connect_timeout_seconds": connect_timeout_seconds,
            "endpoint": endpoint,
            "endpoint_region": endpoint_region,
            "maximum_concurrency": maximum_concurrency,
            "maximum_input_bytes": _MAXIMUM_INPUT_BYTES,
            "maximum_response_bytes": maximum_response_bytes,
            "maximum_result_bytes": maximum_result_bytes,
            "maximum_seed": _MAXIMUM_SEED,
            "model": model,
            "n": 1,
            "protocol_mode": "DASHSCOPE_ASYNC_V1",
            "read_timeout_seconds": read_timeout_seconds,
            "schema_version": 1,
            "thinking_mode": True,
            "watermark": False,
            "workspace_id": workspace_id,
        }
        self._endpoint_identity = AlibabaWanEndpointIdentity(
            endpoint_host=parsed.hostname,
            endpoint_region=endpoint_region,
            workspace_id=workspace_id,
            model=model,
            protocol_mode="DASHSCOPE_ASYNC_V1",
            adapter_version="alibaba-wan-async-v1",
            configuration_sha256=hashlib.sha256(
                json.dumps(
                    configuration,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("ascii")
            ).hexdigest(),
        )
        self._model = model
        self._input_resolver = input_resolver
        self._maximum_result_bytes = maximum_result_bytes
        self._allowed_result_hosts = normalized_result_hosts
        self._wall_clock = wall_clock or (lambda: datetime.now(UTC))
        self._transport = AsyncVisionHttpTransport(
            credential_provider=credential_provider,
            endpoint=endpoint,
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=read_timeout_seconds,
            maximum_concurrency=maximum_concurrency,
            maximum_response_bytes=maximum_response_bytes,
            request_path="/services/aigc/image-generation/generation",
            request_headers={"X-DashScope-Async": "enable"},
            client=client,
        )

    @property
    def endpoint_identity(self) -> AlibabaWanEndpointIdentity:
        return self._endpoint_identity

    def submit(self, request: ImageProviderSubmitRequest) -> NormalizedImageProviderOutcome:
        if not isinstance(request, ImageGenerationProviderRequest):
            return self._failure("UNVERIFIED_WAN_EXACT_EDITING")
        if (
            request.negative_prompt_text is not None
            or (request.media.seed is not None and request.media.seed > _MAXIMUM_SEED)
            or request.media.output_format is not ImageProviderOutputFormat.PNG
            or len(request.prompt_text) > 5000
            or len(request.reference_images) > 9
        ):
            return self._failure("UNVERIFIED_WAN_CAPABILITY")
        if not self._dimensions_allowed(request):
            return self._failure("UNSUPPORTED_WAN_DIMENSIONS")

        started_at = time.monotonic()
        remaining_seconds = (request.deadline - self._wall_clock()).total_seconds()
        if remaining_seconds <= 0:
            return self._transport_failure(
                ImageProviderCallOutcome.SAFE_TO_RETRY_PRE_DISPATCH,
                "REQUEST_DEADLINE_EXPIRED",
                started_at,
            )
        deadline_at = time.monotonic() + remaining_seconds
        resolved_content = self._resolve_request_content(request)
        if isinstance(resolved_content, NormalizedImageProviderOutcome):
            return resolved_content
        parameters: dict[str, object] = {
            "size": f"{request.media.width}*{request.media.height}",
            "n": 1,
            "watermark": False,
        }
        if not request.reference_images:
            parameters["thinking_mode"] = True
        if request.media.seed is not None:
            parameters["seed"] = request.media.seed
        payload = json.dumps(
            {
                "model": self._model,
                "input": {
                    "messages": [
                        {
                            "role": "user",
                            "content": [*resolved_content, {"text": request.prompt_text}],
                        }
                    ]
                },
                "parameters": parameters,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            evidence = self._transport.send(payload, deadline_at=deadline_at)
        except VisionSafeToRetryTransportError:
            return self._transport_failure(
                ImageProviderCallOutcome.SAFE_TO_RETRY_PRE_DISPATCH,
                "PRE_DISPATCH_TRANSPORT_FAILURE",
                started_at,
            )
        except VisionSubmissionOutcomeUnknownError:
            return self._transport_failure(
                ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH,
                "SUBMISSION_OUTCOME_UNKNOWN",
                started_at,
            )

        if evidence.body_too_large or evidence.completion_uncertain:
            return self._unknown("PROVIDER_RESPONSE_UNUSABLE", started_at)
        if evidence.response.status_code != 200:
            return self._submit_http_failure(evidence.response, started_at=started_at)
        try:
            body: Any = evidence.response.json()
            output = body["output"]
            if not isinstance(body, dict) or not isinstance(output, dict):
                raise TypeError
            if output.get("task_status") != "PENDING":
                raise ValueError
            identity = ImageProviderRequestIdentity(
                provider_request_id=body.get("request_id"),
                provider_task_id=output.get("task_id"),
            )
            if identity.provider_request_id is None or identity.provider_task_id is None:
                raise ValueError
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return self._unknown("MALFORMED_PROVIDER_RESPONSE", started_at)

        return NormalizedImageProviderOutcome(
            call_outcome=ImageProviderCallOutcome.CONFIRMED_SUCCESS,
            task_state=ImageProviderTaskState.PENDING,
            identity=identity,
            result=None,
            usage=None,
            error=None,
            latency_ms=self._latency_ms(started_at),
        )

    def _dimensions_allowed(self, request: ImageGenerationProviderRequest) -> bool:
        width = request.media.width
        height = request.media.height
        minimum = min(width, height)
        maximum = max(width, height)
        if maximum > minimum * 8:
            return False
        area = width * height
        if area < 768 * 768:
            return False
        maximum_edge: int = (
            4096 if self._model == "wan2.7-image-pro" and not request.reference_images else 2048
        )
        return area <= maximum_edge * maximum_edge

    def _resolve_request_content(
        self,
        request: ImageGenerationProviderRequest,
    ) -> list[dict[str, str]] | NormalizedImageProviderOutcome:
        if not request.reference_images:
            return []
        if self._input_resolver is None:
            return self._failure("CONTROLLED_IMAGE_RESOLVER_REQUIRED")
        content: list[dict[str, str]] = []
        for image in request.reference_images:
            try:
                image_bytes = self._input_resolver.resolve(
                    image,
                    maximum_bytes=_MAXIMUM_INPUT_BYTES,
                    deadline=request.deadline,
                )
            except ControlledImageInputUnavailableError:
                return self._transport_failure(
                    ImageProviderCallOutcome.SAFE_TO_RETRY_PRE_DISPATCH,
                    "CONTROLLED_IMAGE_UNAVAILABLE",
                    time.monotonic(),
                )
            if not self._is_valid_controlled_input(image_bytes, image=image):
                return self._failure("CONTROLLED_IMAGE_INVALID")
            encoded = base64.b64encode(image_bytes).decode("ascii")
            content.append({"image": f"data:{image.media_type.value};base64,{encoded}"})
        return content

    @staticmethod
    def _is_valid_controlled_input(content: bytes, *, image: ControlledImageInput) -> bool:
        if (
            not isinstance(content, bytes)
            or not 1 <= len(content) <= _MAXIMUM_INPUT_BYTES
            or hashlib.sha256(content).hexdigest() != image.content_sha256
        ):
            return False
        expected_format = _INPUT_MEDIA_FORMATS[image.media_type]
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(content)) as probe:
                    if (
                        probe.format != expected_format
                        or probe.size != (image.width, image.height)
                        or int(getattr(probe, "n_frames", 1)) != 1
                        or probe.width * probe.height > _MAXIMUM_DECODED_PIXELS
                        or "A" in probe.getbands()
                        or "transparency" in probe.info
                    ):
                        return False
                    probe.verify()
                with Image.open(BytesIO(content)) as loaded:
                    if loaded.format != expected_format or loaded.size != probe.size:
                        return False
                    loaded.load()
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

    def _submit_http_failure(
        self,
        response: httpx.Response,
        *,
        started_at: float,
    ) -> NormalizedImageProviderOutcome:
        identity = self._response_identity(response)
        categories = {
            400: ImageProviderErrorCategory.INVALID_REQUEST,
            401: ImageProviderErrorCategory.AUTHENTICATION,
            403: ImageProviderErrorCategory.AUTHENTICATION,
            404: ImageProviderErrorCategory.INVALID_REQUEST,
            429: ImageProviderErrorCategory.RATE_LIMITED,
        }
        category = categories.get(response.status_code)
        if category is None:
            return NormalizedImageProviderOutcome(
                call_outcome=ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH,
                task_state=None,
                identity=identity,
                result=None,
                usage=None,
                error=ImageProviderError(
                    category=ImageProviderErrorCategory.PROVIDER_UNAVAILABLE,
                    code="PROVIDER_SUBMISSION_HTTP_UNKNOWN",
                    retry_after_seconds=None,
                ),
                latency_ms=self._latency_ms(started_at),
            )
        codes = {
            400: "PROVIDER_INVALID_REQUEST",
            401: "PROVIDER_AUTHENTICATION_FAILED",
            403: "PROVIDER_ACCESS_DENIED",
            404: "PROVIDER_ENDPOINT_NOT_FOUND",
            429: "PROVIDER_RATE_LIMITED",
        }
        return NormalizedImageProviderOutcome(
            call_outcome=ImageProviderCallOutcome.CONFIRMED_FAILURE,
            task_state=ImageProviderTaskState.FAILED,
            identity=identity,
            result=None,
            usage=None,
            error=ImageProviderError(
                category=category,
                code=codes[response.status_code],
                retry_after_seconds=None,
            ),
            latency_ms=self._latency_ms(started_at),
        )

    @staticmethod
    def _response_identity(response: httpx.Response) -> ImageProviderRequestIdentity | None:
        try:
            body: Any = response.json()
            if not isinstance(body, dict):
                return None
            request_id = body.get("request_id")
            if not isinstance(request_id, str):
                return None
            return ImageProviderRequestIdentity(
                provider_request_id=request_id,
                provider_task_id=None,
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    def close(self) -> None:
        self._transport.close()

    def query(self, request: ImageProviderQueryRequest) -> NormalizedImageProviderOutcome:
        task_id = request.identity.provider_task_id
        if task_id is None:
            return self._failure("WAN_TASK_ID_REQUIRED")
        started_at = time.monotonic()
        deadline_at = time.monotonic() + max(
            0.0,
            (request.deadline - self._wall_clock()).total_seconds(),
        )
        try:
            evidence = self._transport.send_get(
                f"/tasks/{task_id}",
                deadline_at=deadline_at,
            )
        except VisionSafeToRetryTransportError:
            return self._transport_failure(
                ImageProviderCallOutcome.SAFE_TO_RETRY_PRE_DISPATCH,
                "PRE_DISPATCH_QUERY_FAILURE",
                started_at,
            )
        except VisionSubmissionOutcomeUnknownError:
            return self._transport_failure(
                ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH,
                "QUERY_OUTCOME_UNKNOWN",
                started_at,
                identity=request.identity,
            )
        if (
            evidence.response.status_code != 200
            or evidence.body_too_large
            or evidence.completion_uncertain
        ):
            return self._unknown(
                "PROVIDER_QUERY_RESPONSE_UNUSABLE",
                started_at,
                identity=request.identity,
            )
        try:
            body: Any = evidence.response.json()
            output = body["output"]
            if not isinstance(body, dict) or not isinstance(output, dict):
                raise TypeError
            if output.get("task_id") != task_id:
                raise ValueError
            identity = ImageProviderRequestIdentity(
                provider_request_id=body.get("request_id"),
                provider_task_id=output.get("task_id"),
            )
            if identity.provider_request_id is None:
                raise ValueError
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return self._unknown(
                "MALFORMED_PROVIDER_QUERY_RESPONSE",
                started_at,
                identity=request.identity,
            )
        task_status = output.get("task_status")
        finished = output.get("finished")
        if (
            (finished is not None and not isinstance(finished, bool))
            or (task_status in {"PENDING", "RUNNING"} and finished is True)
            or (task_status in {"SUCCEEDED", "FAILED", "CANCELED"} and finished is False)
        ):
            return self._unknown(
                "PROVIDER_QUERY_STATE_CONFLICT",
                started_at,
                identity=identity,
            )
        if task_status == "SUCCEEDED":
            return self._succeeded_query(
                output=output,
                body=body,
                identity=identity,
                deadline_at=deadline_at,
                started_at=started_at,
            )
        if task_status == "CANCELED":
            return NormalizedImageProviderOutcome(
                call_outcome=ImageProviderCallOutcome.CONFIRMED_SUCCESS,
                task_state=ImageProviderTaskState.CANCELLED,
                identity=identity,
                result=None,
                usage=None,
                error=None,
                latency_ms=self._latency_ms(started_at),
            )
        if task_status == "FAILED":
            provider_code = output.get("code", body.get("code"))
            is_content_rejection = provider_code == "DataInspectionFailed"
            return NormalizedImageProviderOutcome(
                call_outcome=(
                    ImageProviderCallOutcome.CONTENT_REJECTED
                    if is_content_rejection
                    else ImageProviderCallOutcome.CONFIRMED_FAILURE
                ),
                task_state=(
                    ImageProviderTaskState.REJECTED
                    if is_content_rejection
                    else ImageProviderTaskState.FAILED
                ),
                identity=identity,
                result=None,
                usage=None,
                error=ImageProviderError(
                    category=(
                        ImageProviderErrorCategory.CONTENT_POLICY
                        if is_content_rejection
                        else ImageProviderErrorCategory.PROVIDER_UNAVAILABLE
                    ),
                    code=(
                        "PROVIDER_CONTENT_REJECTED"
                        if is_content_rejection
                        else "PROVIDER_TASK_FAILED"
                    ),
                    retry_after_seconds=None,
                ),
                latency_ms=self._latency_ms(started_at),
            )
        if task_status == "UNKNOWN":
            return self._query_unknown(identity=identity, started_at=started_at)
        if task_status not in {"PENDING", "RUNNING"}:
            return self._unknown(
                "MALFORMED_PROVIDER_QUERY_STATUS",
                started_at,
                identity=identity,
            )
        return NormalizedImageProviderOutcome(
            call_outcome=ImageProviderCallOutcome.CONFIRMED_SUCCESS,
            task_state=ImageProviderTaskState.PENDING,
            identity=identity,
            result=None,
            usage=None,
            error=None,
            latency_ms=self._latency_ms(started_at),
        )

    def cancel(self, request: ImageProviderCancelRequest) -> NormalizedImageProviderOutcome:
        return NormalizedImageProviderOutcome(
            call_outcome=ImageProviderCallOutcome.CONFIRMED_FAILURE,
            task_state=ImageProviderTaskState.FAILED,
            identity=request.identity,
            result=None,
            usage=None,
            error=ImageProviderError(
                category=ImageProviderErrorCategory.INVALID_REQUEST,
                code="UNVERIFIED_WAN_CANCELLATION",
                retry_after_seconds=None,
            ),
            latency_ms=0,
        )

    @classmethod
    def _query_unknown(
        cls,
        *,
        identity: ImageProviderRequestIdentity,
        started_at: float,
    ) -> NormalizedImageProviderOutcome:
        return NormalizedImageProviderOutcome(
            call_outcome=ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH,
            task_state=None,
            identity=identity,
            result=None,
            usage=None,
            error=ImageProviderError(
                category=ImageProviderErrorCategory.PROVIDER_UNAVAILABLE,
                code="PROVIDER_TASK_STATUS_UNKNOWN",
                retry_after_seconds=None,
            ),
            latency_ms=cls._latency_ms(started_at),
        )

    def _succeeded_query(
        self,
        *,
        output: dict[str, Any],
        body: dict[str, Any],
        identity: ImageProviderRequestIdentity,
        deadline_at: float,
        started_at: float,
    ) -> NormalizedImageProviderOutcome:
        try:
            if output.get("finished") is not True:
                raise ValueError
            choices = output["choices"]
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError
            choice = choices[0]
            message = choice["message"]
            content_items = message["content"]
            if (
                not isinstance(choice, dict)
                or choice.get("finish_reason") != "stop"
                or not isinstance(message, dict)
                or message.get("role") != "assistant"
                or not isinstance(content_items, list)
            ):
                raise ValueError
            image_urls: list[str] = []
            for item in content_items:
                if not isinstance(item, dict):
                    raise TypeError
                if item.get("type") == "image" and isinstance(item.get("image"), str):
                    image_urls.append(item["image"])
                elif item.get("type") == "text" and isinstance(item.get("text"), str):
                    continue
                else:
                    raise ValueError
            if len(image_urls) != 1:
                raise ValueError
            usage_document = body["usage"]
            if not isinstance(usage_document, dict) or usage_document.get("image_count") != 1:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            return self._unknown(
                "MALFORMED_PROVIDER_SUCCESS_RESPONSE",
                started_at,
                identity=identity,
            )

        content = self._download_result(
            image_urls[0],
            deadline_at=deadline_at,
        )
        if content is None:
            return self._unknown(
                "PROVIDER_RESULT_FETCH_INVALID",
                started_at,
                identity=identity,
            )
        dimensions = self._png_dimensions(content)
        if dimensions is None:
            return self._unknown(
                "PROVIDER_RESULT_MEDIA_INVALID",
                started_at,
                identity=identity,
            )
        usage_size = usage_document.get("size")
        if usage_size != f"{dimensions[0]}*{dimensions[1]}":
            return self._unknown(
                "PROVIDER_RESULT_DIMENSIONS_INCONSISTENT",
                started_at,
                identity=identity,
            )
        task_id = identity.provider_task_id
        assert task_id is not None
        return NormalizedImageProviderOutcome(
            call_outcome=ImageProviderCallOutcome.CONFIRMED_SUCCESS,
            task_state=ImageProviderTaskState.SUCCEEDED,
            identity=identity,
            result=ImageProviderResult(
                provider_result_id=f"{task_id}:0",
                content=content,
                content_sha256=hashlib.sha256(content).hexdigest(),
                media_type=ImageProviderMediaType.PNG,
                width=dimensions[0],
                height=dimensions[1],
            ),
            usage=ImageProviderUsage(
                unit=ImageProviderUsageUnit.IMAGE,
                quantity=Decimal("1.000000"),
                evidence_sha256=hashlib.sha256(
                    json.dumps(
                        usage_document,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
            ),
            error=None,
            latency_ms=self._latency_ms(started_at),
        )

    def _download_result(self, url: str, *, deadline_at: float) -> bytes | None:
        if len(url.encode("utf-8")) > 4096:
            return None
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
            return None
        try:
            evidence = self._transport.fetch(
                url,
                deadline_at=deadline_at,
                maximum_response_bytes=self._maximum_result_bytes,
            )
        except (VisionSafeToRetryTransportError, VisionSubmissionOutcomeUnknownError):
            return None
        content_type = evidence.response.headers.get("Content-Type", "")
        if (
            evidence.body_too_large
            or evidence.completion_uncertain
            or evidence.response.status_code != 200
            or evidence.response.is_redirect
            or content_type.partition(";")[0].strip().lower() != "image/png"
            or not evidence.response.content
        ):
            return None
        return evidence.response.content

    @staticmethod
    def _png_dimensions(content: bytes) -> tuple[int, int] | None:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(content)) as probe:
                    if (
                        probe.format != "PNG"
                        or int(getattr(probe, "n_frames", 1)) != 1
                        or probe.width * probe.height > _MAXIMUM_DECODED_PIXELS
                    ):
                        return None
                    dimensions = probe.size
                    probe.verify()
                with Image.open(BytesIO(content)) as image:
                    if image.format != "PNG" or image.size != dimensions:
                        return None
                    image.load()
        except (
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
            UnidentifiedImageError,
            OSError,
            SyntaxError,
            ValueError,
        ):
            return None
        return dimensions

    @staticmethod
    def _failure(code: str) -> NormalizedImageProviderOutcome:
        return NormalizedImageProviderOutcome(
            call_outcome=ImageProviderCallOutcome.CONFIRMED_FAILURE,
            task_state=ImageProviderTaskState.FAILED,
            identity=None,
            result=None,
            usage=None,
            error=ImageProviderError(
                category=ImageProviderErrorCategory.INVALID_REQUEST,
                code=code,
                retry_after_seconds=None,
            ),
            latency_ms=0,
        )

    @classmethod
    def _unknown(
        cls,
        code: str,
        started_at: float,
        *,
        identity: ImageProviderRequestIdentity | None = None,
    ) -> NormalizedImageProviderOutcome:
        return NormalizedImageProviderOutcome(
            call_outcome=ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH,
            task_state=None,
            identity=identity,
            result=None,
            usage=None,
            error=ImageProviderError(
                category=ImageProviderErrorCategory.MALFORMED_RESPONSE,
                code=code,
                retry_after_seconds=None,
            ),
            latency_ms=cls._latency_ms(started_at),
        )

    @staticmethod
    def _transport_failure(
        call_outcome: ImageProviderCallOutcome,
        code: str,
        started_at: float,
        *,
        identity: ImageProviderRequestIdentity | None = None,
    ) -> NormalizedImageProviderOutcome:
        return NormalizedImageProviderOutcome(
            call_outcome=call_outcome,
            task_state=None,
            identity=identity,
            result=None,
            usage=None,
            error=ImageProviderError(
                category=ImageProviderErrorCategory.PROVIDER_UNAVAILABLE,
                code=code,
                retry_after_seconds=None,
            ),
            latency_ms=AlibabaWanAsyncImageAdapter._latency_ms(started_at),
        )

    @staticmethod
    def _latency_ms(started_at: float) -> int:
        return max(0, int((time.monotonic() - started_at) * 1000))
