"""Alibaba Image Moderation 2.0 and deterministic content-safety adapters."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime
from types import TracebackType
from typing import Any, Protocol
from urllib.parse import urlsplit

from commercevision_contracts.validation import (
    ContentSafetyConfiguredIdentity,
    ContentSafetyImageRequest,
    ContentSafetyLabel,
    ContentSafetyOutcome,
    ContentSafetyResult,
)


class _ImageModerationClient(Protocol):
    def image_moderation_with_options(self, request: object, runtime: object) -> object: ...


class _KeywordFactory(Protocol):
    def __call__(self, **kwargs: object) -> object: ...


class AlibabaImageModerationAdapter:
    """Normalize Alibaba Green responses without retaining provider payloads."""

    def __init__(
        self,
        *,
        client: _ImageModerationClient,
        request_factory: _KeywordFactory,
        runtime_options_factory: _KeywordFactory,
        endpoint: str,
        service: str,
        sdk_version: str,
        policy_version: str,
        mapping_version: str,
        risk_mapping: Mapping[str, ContentSafetyOutcome],
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        end_to_end_timeout_seconds: float,
        maximum_concurrency: int,
        minimum_url_validity_seconds: float,
        allowed_url_origins: frozenset[str],
        clock: Callable[[], datetime],
    ) -> None:
        if not endpoint or "://" in endpoint or "/" in endpoint or len(endpoint) > 255:
            raise ValueError("Alibaba moderation endpoint must be a DNS hostname")
        if not service or len(service) > 128:
            raise ValueError("Alibaba moderation service is invalid")
        if connect_timeout_seconds <= 0 or read_timeout_seconds <= 0:
            raise ValueError("Alibaba moderation transport timeouts are invalid")
        if end_to_end_timeout_seconds <= (connect_timeout_seconds + read_timeout_seconds):
            raise ValueError("Alibaba end-to-end timeout must exceed transport timeouts")
        if maximum_concurrency < 1:
            raise ValueError("Alibaba moderation concurrency must be positive")
        if minimum_url_validity_seconds < end_to_end_timeout_seconds:
            raise ValueError("Alibaba URL validity must cover the end-to-end call budget")
        normalized_origins = {
            self._normalize_origin(origin, configured=True) for origin in allowed_url_origins
        }
        if not normalized_origins:
            raise ValueError("Alibaba moderation requires a controlled URL origin allowlist")
        normalized_mapping = {key.strip().lower(): value for key, value in risk_mapping.items()}
        if (
            not normalized_mapping
            or any(not key for key in normalized_mapping)
            or ContentSafetyOutcome.RETRYABLE_FAILURE in normalized_mapping.values()
        ):
            raise ValueError("Alibaba risk mapping must contain terminal normalized outcomes")
        self._client = client
        self._request_factory = request_factory
        self._runtime_options_factory = runtime_options_factory
        self._endpoint = endpoint
        self._service = service
        self._sdk_version = sdk_version
        self._policy_version = policy_version
        self._mapping_version = mapping_version
        self._configured_identity = ContentSafetyConfiguredIdentity(
            provider="alibaba-green20220302",
            endpoint=endpoint,
            service=service,
            sdk_version=sdk_version,
            policy_version=policy_version,
            mapping_version=mapping_version,
        )
        self._risk_mapping = normalized_mapping
        self._connect_timeout_ms = round(connect_timeout_seconds * 1000)
        self._read_timeout_ms = round(read_timeout_seconds * 1000)
        self._timeout_seconds = end_to_end_timeout_seconds
        self._minimum_url_validity_seconds = minimum_url_validity_seconds
        self._allowed_url_origins = frozenset(normalized_origins)
        self._clock = clock
        self._capacity = threading.BoundedSemaphore(maximum_concurrency)
        self._pool = ThreadPoolExecutor(
            max_workers=maximum_concurrency,
            thread_name_prefix="alibaba-image-moderation",
        )

    @classmethod
    def from_credentials(
        cls,
        *,
        access_key_id: str,
        access_key_secret: str,
        **kwargs: Any,
    ) -> AlibabaImageModerationAdapter:
        """Build the documented Green 20220302 SDK boundary without retaining credentials."""

        if not access_key_id or not access_key_secret:
            raise ValueError("Alibaba moderation credentials must not be blank")
        from alibabacloud_green20220302 import models
        from alibabacloud_green20220302.client import Client
        from alibabacloud_tea_openapi.models import Config
        from alibabacloud_tea_util import models as util_models

        endpoint = kwargs["endpoint"]
        client = Client(
            Config(
                access_key_id=access_key_id,
                access_key_secret=access_key_secret,
                endpoint=endpoint,
            )
        )
        return cls(
            client=client,
            request_factory=models.ImageModerationRequest,
            runtime_options_factory=util_models.RuntimeOptions,
            **kwargs,
        )

    @property
    def configured_identity(self) -> ContentSafetyConfiguredIdentity:
        return self._configured_identity

    def moderate(self, request: ContentSafetyImageRequest) -> ContentSafetyResult:
        started = time.monotonic()
        deadline = started + self._timeout_seconds
        if request.image_url_expires_at is not None:
            assert request.image_url is not None
            request_origin = self._normalize_origin(request.image_url, configured=False)
            if request_origin not in self._allowed_url_origins:
                raise ValueError(
                    "content-safety URL origin is not an application-controlled origin"
                )
            remaining_validity = (request.image_url_expires_at - self._clock()).total_seconds()
            if remaining_validity < self._minimum_url_validity_seconds:
                raise ValueError("content-safety URL validity does not cover provider download")
        service_parameters: dict[str, str] = {"dataId": request.data_id}
        if request.image_url is not None:
            service_parameters["imageUrl"] = request.image_url
        else:
            assert request.oss_region is not None
            assert request.oss_bucket is not None
            assert request.oss_object is not None
            service_parameters.update(
                {
                    "ossRegionId": request.oss_region,
                    "ossBucketName": request.oss_bucket,
                    "ossObjectName": request.oss_object,
                }
            )
        provider_request = self._request_factory(
            service=self._service,
            service_parameters=json.dumps(
                service_parameters,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        runtime_options = self._runtime_options_factory(
            autoretry=False,
            connect_timeout=self._connect_timeout_ms,
            max_attempts=1,
            read_timeout=self._read_timeout_ms,
        )
        remaining = self._remaining(deadline)
        if remaining is None:
            return self._failure("PROVIDER_TIMEOUT", started=started)
        acquired = self._capacity.acquire(timeout=remaining)
        if not acquired:
            return self._failure(
                "PROVIDER_CONCURRENCY_SATURATED",
                started=started,
            )
        remaining = self._remaining(deadline)
        if remaining is None:
            self._capacity.release()
            return self._failure("PROVIDER_TIMEOUT", started=started)
        future = self._pool.submit(
            self._client.image_moderation_with_options,
            provider_request,
            runtime_options,
        )
        release_in_callback = False
        try:
            response = future.result(timeout=remaining)
            if self._remaining(deadline) is None:
                return self._failure("PROVIDER_TIMEOUT", started=started)
        except (FutureTimeout, TimeoutError):
            release_in_callback = True
            future.add_done_callback(self._release_capacity)
            return self._failure("PROVIDER_TIMEOUT", started=started)
        except Exception as exc:
            return self._normalize_exception(exc, started=started)
        finally:
            if not release_in_callback:
                self._capacity.release()
        return self._normalize(response, started=started)

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def __enter__(self) -> AlibabaImageModerationAdapter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _normalize(self, response: object, *, started: float) -> ContentSafetyResult:
        status_code = self._field(response, "status_code")
        if not isinstance(status_code, int) or isinstance(status_code, bool):
            return self._terminal_failure("MALFORMED_PROVIDER_RESPONSE", started=started)
        if status_code != 200:
            retry_after = self._retry_after(self._field(response, "headers"))
            if status_code == 429:
                return self._failure(
                    "RATE_LIMITED",
                    started=started,
                    retry_after_seconds=retry_after,
                )
            if status_code >= 500:
                return self._failure(
                    "PROVIDER_UNAVAILABLE",
                    started=started,
                    retry_after_seconds=retry_after,
                )
            return self._terminal_failure(
                f"PROVIDER_HTTP_{status_code}",
                started=started,
            )

        body = self._field(response, "body")
        code = self._field(body, "code")
        request_id = self._field(body, "request_id")
        if not isinstance(request_id, str):
            return self._terminal_failure("MALFORMED_PROVIDER_RESPONSE", started=started)
        request_id = request_id.strip()
        if not self._bounded_fact(request_id, maximum=128):
            return self._terminal_failure("MALFORMED_PROVIDER_RESPONSE", started=started)
        if code != 200:
            if isinstance(code, int) and not isinstance(code, bool) and 100 <= code <= 99999:
                failure_code = f"PROVIDER_CODE_{code}"
                if code == 429 or code >= 500:
                    return self._failure(
                        failure_code,
                        started=started,
                        request_id=request_id,
                    )
            else:
                failure_code = "MALFORMED_PROVIDER_RESPONSE"
            return self._terminal_failure(
                failure_code,
                started=started,
                request_id=request_id,
            )
        data = self._field(body, "data")
        risk_level = self._field(data, "risk_level")
        raw_labels = self._field(data, "result")
        if (
            not isinstance(risk_level, str)
            or not isinstance(raw_labels, list)
            or not 1 <= len(raw_labels) <= 128
        ):
            return self._terminal_failure(
                "MALFORMED_PROVIDER_RESPONSE",
                started=started,
                request_id=request_id,
            )
        normalized_risk = risk_level.strip().lower()
        if not self._bounded_fact(normalized_risk, maximum=64):
            return self._terminal_failure(
                "MALFORMED_PROVIDER_RESPONSE",
                started=started,
                request_id=request_id,
            )
        outcome = self._risk_mapping.get(normalized_risk)
        if outcome is None:
            return self._terminal_failure(
                "AMBIGUOUS_PROVIDER_RESPONSE",
                started=started,
                request_id=request_id,
            )
        labels_by_code: dict[str, ContentSafetyLabel] = {}
        try:
            for raw_label in raw_labels:
                code_value = self._field(raw_label, "label")
                confidence = self._field(raw_label, "confidence")
                if not isinstance(code_value, str):
                    raise ValueError("label code is not a string")
                if confidence is not None and (
                    not isinstance(confidence, (int, float)) or isinstance(confidence, bool)
                ):
                    raise ValueError("confidence is not numeric")
                normalized_label = ContentSafetyLabel(
                    code=code_value,
                    confidence=float(confidence) if confidence is not None else None,
                )
                existing = labels_by_code.get(normalized_label.code)
                if existing is None or self._confidence_rank(
                    normalized_label.confidence
                ) > self._confidence_rank(existing.confidence):
                    labels_by_code[normalized_label.code] = normalized_label
        except ValueError:
            return self._terminal_failure(
                "MALFORMED_PROVIDER_RESPONSE",
                started=started,
                request_id=request_id,
            )
        return ContentSafetyResult(
            outcome=outcome,
            provider=self._configured_identity.provider,
            endpoint=self._configured_identity.endpoint,
            service=self._configured_identity.service,
            sdk_version=self._configured_identity.sdk_version,
            policy_version=self._configured_identity.policy_version,
            mapping_version=self._configured_identity.mapping_version,
            request_id=request_id,
            risk_level=normalized_risk,
            labels=tuple(sorted(labels_by_code.values(), key=lambda label: label.code)),
            failure_code=None,
            retry_after_seconds=None,
            latency_ms=self._latency(started),
        )

    def _failure(
        self,
        failure_code: str,
        *,
        started: float,
        request_id: str | None = None,
        retry_after_seconds: int | None = None,
    ) -> ContentSafetyResult:
        return self._failed_result(
            ContentSafetyOutcome.RETRYABLE_FAILURE,
            failure_code,
            started=started,
            request_id=request_id,
            retry_after_seconds=retry_after_seconds,
        )

    def _terminal_failure(
        self,
        failure_code: str,
        *,
        started: float,
        request_id: str | None = None,
    ) -> ContentSafetyResult:
        return self._failed_result(
            ContentSafetyOutcome.TERMINAL_FAILURE,
            failure_code,
            started=started,
            request_id=request_id,
            retry_after_seconds=None,
        )

    def _failed_result(
        self,
        outcome: ContentSafetyOutcome,
        failure_code: str,
        *,
        started: float,
        request_id: str | None,
        retry_after_seconds: int | None,
    ) -> ContentSafetyResult:
        return ContentSafetyResult(
            outcome=outcome,
            provider=self._configured_identity.provider,
            endpoint=self._configured_identity.endpoint,
            service=self._configured_identity.service,
            sdk_version=self._configured_identity.sdk_version,
            policy_version=self._configured_identity.policy_version,
            mapping_version=self._configured_identity.mapping_version,
            request_id=request_id,
            risk_level=None,
            labels=(),
            failure_code=failure_code,
            retry_after_seconds=retry_after_seconds,
            latency_ms=self._latency(started),
        )

    def _normalize_exception(
        self,
        error: Exception,
        *,
        started: float,
    ) -> ContentSafetyResult:
        status_code = self._field(error, "status_code")
        if isinstance(status_code, int) and not isinstance(status_code, bool):
            if status_code == 429:
                return self._failure("RATE_LIMITED", started=started)
            if status_code >= 500:
                return self._failure("PROVIDER_UNAVAILABLE", started=started)
            if 400 <= status_code < 500:
                return self._terminal_failure(
                    f"PROVIDER_HTTP_{status_code}",
                    started=started,
                )

        raw_code = self._field(error, "code")
        normalized_code = self._normalized_provider_code(raw_code)
        if normalized_code is None:
            return self._failure("PROVIDER_TRANSPORT_ERROR", started=started)
        failure_code = f"PROVIDER_CODE_{normalized_code}"
        if any(
            marker in normalized_code
            for marker in (
                "THROTTL",
                "RATELIMIT",
                "TIMEOUT",
                "UNAVAILABLE",
                "INTERNAL",
                "SERVICEBUSY",
                "TEMPORARY",
            )
        ):
            return self._failure(failure_code, started=started)
        return self._terminal_failure(failure_code, started=started)

    @staticmethod
    def _normalized_provider_code(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = "".join(character for character in value.upper() if character.isalnum())
        if not normalized:
            return None
        return normalized[:48]

    @staticmethod
    def _field(value: object, name: str) -> object:
        if isinstance(value, Mapping):
            return value.get(name)
        return getattr(value, name, None)

    @staticmethod
    def _retry_after(headers: object) -> int | None:
        if not isinstance(headers, Mapping):
            return None
        raw_value = headers.get("retry-after") or headers.get("Retry-After")
        if isinstance(raw_value, bool):
            return None
        if isinstance(raw_value, int):
            return min(raw_value, 300) if raw_value >= 0 else None
        if not isinstance(raw_value, str):
            return None
        normalized = raw_value.strip()
        if not normalized or len(normalized) > 10 or not normalized.isascii():
            return None
        if not normalized.isdecimal():
            return None
        return min(int(normalized), 300)

    @staticmethod
    def _remaining(deadline: float) -> float | None:
        remaining = deadline - time.monotonic()
        return remaining if remaining > 0 else None

    @staticmethod
    def _latency(started: float) -> int:
        return max(0, round((time.monotonic() - started) * 1000))

    def _release_capacity(self, _future: Future[object]) -> None:
        self._capacity.release()

    @staticmethod
    def _bounded_fact(value: str, *, maximum: int) -> bool:
        return (
            bool(value)
            and len(value) <= maximum
            and value.isascii()
            and all(character.isalnum() or character in {"_", "-", ".", ":"} for character in value)
        )

    @staticmethod
    def _confidence_rank(value: float | None) -> float:
        return value if value is not None else -1.0

    @staticmethod
    def _normalize_origin(value: str, *, configured: bool) -> str:
        parsed = urlsplit(value)
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("content-safety URL origin has an invalid port") from exc
        if (
            parsed.scheme.lower() != "https"
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or (configured and (parsed.path not in {"", "/"} or parsed.query))
        ):
            raise ValueError("content-safety URL origin must be credential-free HTTPS")
        host = parsed.hostname.lower()
        authority = host if port in {None, 443} else f"{host}:{port}"
        return f"https://{authority}"


class DeterministicContentSafetyAdapter:
    """Explicit local/test adapter implementing the production normalized contract."""

    def __init__(
        self,
        *,
        outcome: ContentSafetyOutcome,
        policy_version: str,
        mapping_version: str,
        failure_code: str | None = None,
    ) -> None:
        if outcome in {
            ContentSafetyOutcome.RETRYABLE_FAILURE,
            ContentSafetyOutcome.TERMINAL_FAILURE,
        }:
            if not failure_code:
                raise ValueError("deterministic failure outcomes require a failure code")
        elif failure_code is not None:
            raise ValueError("deterministic policy outcomes must not carry a failure code")
        self._outcome = outcome
        self._policy_version = policy_version
        self._mapping_version = mapping_version
        self._failure_code = failure_code
        self._configured_identity = ContentSafetyConfiguredIdentity(
            provider="deterministic",
            endpoint="local",
            service="deterministic-image-moderation",
            sdk_version="deterministic-v1",
            policy_version=policy_version,
            mapping_version=mapping_version,
        )

    @property
    def configured_identity(self) -> ContentSafetyConfiguredIdentity:
        return self._configured_identity

    def moderate(self, request: ContentSafetyImageRequest) -> ContentSafetyResult:
        failed = self._outcome in {
            ContentSafetyOutcome.RETRYABLE_FAILURE,
            ContentSafetyOutcome.TERMINAL_FAILURE,
        }
        return ContentSafetyResult(
            outcome=self._outcome,
            provider=self._configured_identity.provider,
            endpoint=self._configured_identity.endpoint,
            service=self._configured_identity.service,
            sdk_version=self._configured_identity.sdk_version,
            policy_version=self._configured_identity.policy_version,
            mapping_version=self._configured_identity.mapping_version,
            request_id=f"det-{request.data_id}",
            risk_level=None if failed else "fixture",
            labels=(),
            failure_code=self._failure_code,
            retry_after_seconds=None,
            latency_ms=0,
        )
