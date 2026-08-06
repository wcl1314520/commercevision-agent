"""Transport-neutral image Provider Adapter contracts.

The application owns authorization, routing, provider configuration, credentials and
persistence.  Adapters receive only normalized media instructions and opaque handles
for inputs that were already placed under application control.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Protocol, runtime_checkable

_HANDLE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)
_PROVIDER_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,255}$", re.ASCII)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_MAX_IMAGE_DIMENSION = 16_384
_MAX_REFERENCE_IMAGES = 16
_MAX_PROMPT_BYTES = 16_384
_MAX_NEGATIVE_PROMPT_BYTES = 8_192
_MAX_RESULT_BYTES = 32 * 1024 * 1024
_USAGE_QUANTUM = Decimal("0.000001")
_MAX_USAGE_QUANTITY = Decimal("1000000000.000000")


class ImageProviderInputRole(StrEnum):
    REFERENCE = "REFERENCE"
    SOURCE = "SOURCE"
    MASK = "MASK"


class ImageProviderMediaType(StrEnum):
    PNG = "image/png"
    JPEG = "image/jpeg"
    WEBP = "image/webp"


class ImageProviderOutputFormat(StrEnum):
    PNG = "PNG"
    JPEG = "JPEG"
    WEBP = "WEBP"


class ImageProviderCallOutcome(StrEnum):
    CONFIRMED_SUCCESS = "CONFIRMED_SUCCESS"
    CONFIRMED_FAILURE = "CONFIRMED_FAILURE"
    CONTENT_REJECTED = "CONTENT_REJECTED"
    SAFE_TO_RETRY_PRE_DISPATCH = "SAFE_TO_RETRY_PRE_DISPATCH"
    UNKNOWN_AFTER_POSSIBLE_DISPATCH = "UNKNOWN_AFTER_POSSIBLE_DISPATCH"


class ImageProviderTaskState(StrEnum):
    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    NOT_FOUND = "NOT_FOUND"


class ImageProviderErrorCategory(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"
    AUTHENTICATION = "AUTHENTICATION"
    RATE_LIMITED = "RATE_LIMITED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    CONTENT_POLICY = "CONTENT_POLICY"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    INTERNAL = "INTERNAL"


class ImageProviderUsageUnit(StrEnum):
    IMAGE = "IMAGE"
    MEGAPIXEL = "MEGAPIXEL"
    REQUEST = "REQUEST"


_PRE_DISPATCH_ERROR_CATEGORIES = {
    ImageProviderErrorCategory.RATE_LIMITED,
    ImageProviderErrorCategory.PROVIDER_UNAVAILABLE,
    ImageProviderErrorCategory.TIMEOUT,
}
_UNKNOWN_ERROR_CATEGORIES = {
    ImageProviderErrorCategory.INTERNAL,
    ImageProviderErrorCategory.MALFORMED_RESPONSE,
    ImageProviderErrorCategory.PROVIDER_UNAVAILABLE,
    ImageProviderErrorCategory.TIMEOUT,
}


def _validate_handle(value: str, field_name: str) -> str:
    if isinstance(value, str) and _is_credential_like(value):
        raise ValueError(f"{field_name} contains a credential-like value")
    if not isinstance(value, str) or _HANDLE_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a bounded opaque handle")
    return value


def _validate_dimension(value: int, field_name: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 1 <= value <= _MAX_IMAGE_DIMENSION
    ):
        raise ValueError(f"{field_name} must be between 1 and {_MAX_IMAGE_DIMENSION}")
    return value


def _validate_prompt(value: str, field_name: str, *, maximum_bytes: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    if len(value.encode("utf-8")) > maximum_bytes:
        raise ValueError(f"{field_name} exceeds the byte budget")
    if any(
        ord(character) < 32 and character not in {"\n", "\r", "\t"} or ord(character) == 127
        for character in value
    ):
        raise ValueError(f"{field_name} contains a control character")
    return value


def _validate_utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must be timezone-aware UTC")
    return value


def _validate_submit_request(
    *,
    provider_idempotency_key: str,
    prompt_text: str,
    negative_prompt_text: str | None,
    media: ImageProviderMediaRequirements,
    deadline: datetime,
    request_kind: str,
) -> None:
    _validate_handle(provider_idempotency_key, "Provider idempotency key")
    _validate_prompt(prompt_text, "prompt text", maximum_bytes=_MAX_PROMPT_BYTES)
    if negative_prompt_text is not None:
        _validate_prompt(
            negative_prompt_text,
            "negative prompt text",
            maximum_bytes=_MAX_NEGATIVE_PROMPT_BYTES,
        )
    if not isinstance(media, ImageProviderMediaRequirements):
        raise ValueError(f"{request_kind} media requirements are invalid")
    _validate_utc(deadline, "Provider request deadline")


def _validate_provider_text(value: str, field_name: str, *, maximum: int = 256) -> str:
    if isinstance(value, str) and _is_credential_like(value):
        raise ValueError(f"{field_name} contains a credential-like value")
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or _PROVIDER_ID_PATTERN.fullmatch(value) is None
    ):
        raise ValueError(f"{field_name} is invalid")
    return value


def _is_credential_like(value: str) -> bool:
    normalized = value.lower()
    return normalized.startswith("sk-") or normalized.startswith("bearer ")


@dataclass(frozen=True, slots=True)
class ControlledImageInput:
    handle: str = field(repr=False)
    role: ImageProviderInputRole
    content_sha256: str
    media_type: ImageProviderMediaType
    width: int
    height: int

    def __post_init__(self) -> None:
        _validate_handle(self.handle, "controlled image input handle")
        object.__setattr__(self, "role", ImageProviderInputRole(self.role))
        if (
            not isinstance(self.content_sha256, str)
            or _SHA256_PATTERN.fullmatch(self.content_sha256) is None
        ):
            raise ValueError("controlled image input hash must be lowercase SHA-256")
        object.__setattr__(self, "media_type", ImageProviderMediaType(self.media_type))
        _validate_dimension(self.width, "controlled image input width")
        _validate_dimension(self.height, "controlled image input height")


@dataclass(frozen=True, slots=True)
class ImageProviderMediaRequirements:
    width: int
    height: int
    output_format: ImageProviderOutputFormat
    seed: int | None = None

    def __post_init__(self) -> None:
        _validate_dimension(self.width, "output width")
        _validate_dimension(self.height, "output height")
        object.__setattr__(self, "output_format", ImageProviderOutputFormat(self.output_format))
        if self.seed is not None and (
            not isinstance(self.seed, int)
            or isinstance(self.seed, bool)
            or not 0 <= self.seed <= 4_294_967_295
        ):
            raise ValueError("seed must be an unsigned 32-bit integer")


@dataclass(frozen=True, slots=True)
class ImageGenerationProviderRequest:
    provider_idempotency_key: str = field(repr=False)
    prompt_text: str = field(repr=False)
    negative_prompt_text: str | None = field(repr=False)
    media: ImageProviderMediaRequirements
    reference_images: tuple[ControlledImageInput, ...]
    deadline: datetime

    def __post_init__(self) -> None:
        _validate_submit_request(
            provider_idempotency_key=self.provider_idempotency_key,
            prompt_text=self.prompt_text,
            negative_prompt_text=self.negative_prompt_text,
            media=self.media,
            deadline=self.deadline,
            request_kind="generation",
        )
        if (
            not isinstance(self.reference_images, tuple)
            or len(self.reference_images) > _MAX_REFERENCE_IMAGES
            or any(not isinstance(image, ControlledImageInput) for image in self.reference_images)
            or any(
                image.role is not ImageProviderInputRole.REFERENCE
                for image in self.reference_images
            )
            or len({image.handle for image in self.reference_images}) != len(self.reference_images)
        ):
            raise ValueError("generation reference images are invalid")


@dataclass(frozen=True, slots=True)
class ImageEditingProviderRequest:
    provider_idempotency_key: str = field(repr=False)
    prompt_text: str = field(repr=False)
    negative_prompt_text: str | None = field(repr=False)
    media: ImageProviderMediaRequirements
    source_image: ControlledImageInput
    mask_image: ControlledImageInput
    deadline: datetime

    def __post_init__(self) -> None:
        _validate_submit_request(
            provider_idempotency_key=self.provider_idempotency_key,
            prompt_text=self.prompt_text,
            negative_prompt_text=self.negative_prompt_text,
            media=self.media,
            deadline=self.deadline,
            request_kind="editing",
        )
        if (
            not isinstance(self.source_image, ControlledImageInput)
            or self.source_image.role is not ImageProviderInputRole.SOURCE
        ):
            raise ValueError("editing source image is invalid")
        if (
            not isinstance(self.mask_image, ControlledImageInput)
            or self.mask_image.role is not ImageProviderInputRole.MASK
        ):
            raise ValueError("editing mask image is invalid")
        if self.source_image.handle == self.mask_image.handle:
            raise ValueError("editing source and mask handles must differ")
        if (self.source_image.width, self.source_image.height) != (
            self.mask_image.width,
            self.mask_image.height,
        ):
            raise ValueError("editing source and mask dimensions must match")


ImageProviderSubmitRequest = ImageGenerationProviderRequest | ImageEditingProviderRequest


@dataclass(frozen=True, slots=True)
class ImageProviderRequestIdentity:
    provider_request_id: str | None = field(repr=False)
    provider_task_id: str | None = field(repr=False)

    def __post_init__(self) -> None:
        if self.provider_request_id is None and self.provider_task_id is None:
            raise ValueError("Provider identity requires a request or task identity")
        if self.provider_request_id is not None:
            _validate_provider_text(self.provider_request_id, "Provider request identity")
        if self.provider_task_id is not None:
            _validate_provider_text(self.provider_task_id, "Provider task identity")


@dataclass(frozen=True, slots=True)
class ImageProviderResult:
    provider_result_id: str = field(repr=False)
    content: bytes = field(repr=False)
    content_sha256: str
    media_type: ImageProviderMediaType
    width: int
    height: int

    def __post_init__(self) -> None:
        _validate_provider_text(self.provider_result_id, "Provider result identity")
        if (
            not isinstance(self.content, bytes)
            or not self.content
            or len(self.content) > _MAX_RESULT_BYTES
        ):
            raise ValueError("Provider result bytes are invalid")
        if (
            not isinstance(self.content_sha256, str)
            or _SHA256_PATTERN.fullmatch(self.content_sha256) is None
            or hashlib.sha256(self.content).hexdigest() != self.content_sha256
        ):
            raise ValueError("Provider result hash does not match its bytes")
        object.__setattr__(self, "media_type", ImageProviderMediaType(self.media_type))
        _validate_dimension(self.width, "Provider result width")
        _validate_dimension(self.height, "Provider result height")


@dataclass(frozen=True, slots=True)
class ImageProviderUsage:
    unit: ImageProviderUsageUnit
    quantity: Decimal
    evidence_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "unit", ImageProviderUsageUnit(self.unit))
        if not isinstance(self.quantity, Decimal):
            raise ValueError("Provider usage quantity must be Decimal")
        try:
            if (
                not self.quantity.is_finite()
                or not Decimal("0") < self.quantity <= _MAX_USAGE_QUANTITY
                or self.quantity.quantize(_USAGE_QUANTUM) != self.quantity
            ):
                raise ValueError("Provider usage quantity is out of range")
        except InvalidOperation:
            raise ValueError("Provider usage quantity is invalid") from None
        if (
            not isinstance(self.evidence_sha256, str)
            or _SHA256_PATTERN.fullmatch(self.evidence_sha256) is None
        ):
            raise ValueError("Provider usage evidence must be lowercase SHA-256")


@dataclass(frozen=True, slots=True)
class ImageProviderError:
    category: ImageProviderErrorCategory
    code: str
    retry_after_seconds: int | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "category", ImageProviderErrorCategory(self.category))
        _validate_handle(self.code, "Provider error code")
        if self.retry_after_seconds is not None and (
            not isinstance(self.retry_after_seconds, int)
            or isinstance(self.retry_after_seconds, bool)
            or not 0 <= self.retry_after_seconds <= 300
        ):
            raise ValueError("Provider retry-after exceeds the configured bound")


@dataclass(frozen=True, slots=True)
class NormalizedImageProviderOutcome:
    call_outcome: ImageProviderCallOutcome
    task_state: ImageProviderTaskState | None
    identity: ImageProviderRequestIdentity | None
    result: ImageProviderResult | None
    usage: ImageProviderUsage | None
    error: ImageProviderError | None
    latency_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "call_outcome", ImageProviderCallOutcome(self.call_outcome))
        if self.task_state is not None:
            object.__setattr__(self, "task_state", ImageProviderTaskState(self.task_state))
        if self.identity is not None and not isinstance(
            self.identity, ImageProviderRequestIdentity
        ):
            raise ValueError("Provider outcome identity is invalid")
        if self.result is not None and not isinstance(self.result, ImageProviderResult):
            raise ValueError("Provider outcome result is invalid")
        if self.usage is not None and not isinstance(self.usage, ImageProviderUsage):
            raise ValueError("Provider outcome usage is invalid")
        if self.error is not None and not isinstance(self.error, ImageProviderError):
            raise ValueError("Provider outcome error is invalid")
        if (
            not isinstance(self.latency_ms, int)
            or isinstance(self.latency_ms, bool)
            or not 0 <= self.latency_ms <= 86_400_000
        ):
            raise ValueError("Provider outcome latency is invalid")
        self._validate_facts()

    def _validate_facts(self) -> None:
        if self.call_outcome is ImageProviderCallOutcome.CONFIRMED_SUCCESS:
            if (
                self.identity is None
                or self.error is not None
                or self.task_state
                not in {
                    ImageProviderTaskState.PENDING,
                    ImageProviderTaskState.SUCCEEDED,
                    ImageProviderTaskState.CANCELLED,
                    ImageProviderTaskState.NOT_FOUND,
                }
            ):
                raise ValueError("successful Provider outcome facts are inconsistent")
            if self.task_state is ImageProviderTaskState.SUCCEEDED:
                if self.result is None:
                    raise ValueError("successful completed Provider outcome requires a result")
            elif self.result is not None or self.usage is not None:
                raise ValueError("successful non-result Provider outcome cannot carry result facts")
            return
        if self.call_outcome is ImageProviderCallOutcome.SAFE_TO_RETRY_PRE_DISPATCH:
            if (
                self.identity is not None
                or self.task_state is not None
                or self.result is not None
                or self.usage is not None
                or self.error is None
            ):
                raise ValueError("pre-dispatch retry outcome contains dispatched facts")
            if self.error.category not in _PRE_DISPATCH_ERROR_CATEGORIES:
                raise ValueError("pre-dispatch retry error category is not transient")
            return
        if self.call_outcome is ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH:
            if (
                self.task_state is not None
                or self.result is not None
                or self.usage is not None
                or self.error is None
            ):
                raise ValueError("unknown Provider outcome contains terminal facts")
            if self.error.retry_after_seconds is not None:
                raise ValueError("unknown Provider outcome cannot carry retry-after")
            if self.error.category not in _UNKNOWN_ERROR_CATEGORIES:
                raise ValueError("unknown Provider outcome error category is invalid")
            return
        if self.result is not None or self.usage is not None or self.error is None:
            raise ValueError("failed Provider outcome facts are inconsistent")
        if self.call_outcome is ImageProviderCallOutcome.CONTENT_REJECTED:
            if (
                self.task_state is not ImageProviderTaskState.REJECTED
                or self.error.category is not ImageProviderErrorCategory.CONTENT_POLICY
            ):
                raise ValueError("content-rejected Provider outcome facts are inconsistent")
            if self.error.retry_after_seconds is not None:
                raise ValueError("content-rejected Provider outcome cannot carry retry-after")
        else:
            if self.error.category is ImageProviderErrorCategory.CONTENT_POLICY:
                raise ValueError("content-policy errors require a content-rejected outcome")
            if self.task_state is not ImageProviderTaskState.FAILED:
                raise ValueError("confirmed Provider failure requires a failed task state")

    @property
    def must_reconcile(self) -> bool:
        return self.call_outcome is ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH

    @property
    def is_automatic_resubmission_safe(self) -> bool:
        return self.call_outcome is ImageProviderCallOutcome.SAFE_TO_RETRY_PRE_DISPATCH


@dataclass(frozen=True, slots=True)
class ImageProviderQueryRequest:
    identity: ImageProviderRequestIdentity
    deadline: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ImageProviderRequestIdentity):
            raise ValueError("Provider query identity is invalid")
        _validate_utc(self.deadline, "Provider query deadline")


@dataclass(frozen=True, slots=True)
class ImageProviderCancelRequest:
    identity: ImageProviderRequestIdentity
    deadline: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ImageProviderRequestIdentity):
            raise ValueError("Provider cancellation identity is invalid")
        _validate_utc(self.deadline, "Provider cancellation deadline")


@runtime_checkable
class ImageProviderAdapter(Protocol):
    def submit(self, request: ImageProviderSubmitRequest) -> NormalizedImageProviderOutcome: ...

    def query(self, request: ImageProviderQueryRequest) -> NormalizedImageProviderOutcome: ...

    def cancel(self, request: ImageProviderCancelRequest) -> NormalizedImageProviderOutcome: ...
