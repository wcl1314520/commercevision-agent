"""Typed contracts for asset validation adapters."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from urllib.parse import urlsplit


class MalwareScanOutcome(StrEnum):
    CLEAN = "CLEAN"
    INFECTED = "INFECTED"
    TIMEOUT = "TIMEOUT"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class MalwareScanResult:
    outcome: MalwareScanOutcome
    scanner_version: str | None
    signature: str | None
    latency_ms: int

    def __post_init__(self) -> None:
        if self.scanner_version is not None and (
            not self.scanner_version.strip() or len(self.scanner_version) > 128
        ):
            raise ValueError("malware scanner version is invalid")
        if isinstance(self.latency_ms, bool) or self.latency_ms < 0:
            raise ValueError("malware scan latency must not be negative")
        if self.outcome == MalwareScanOutcome.INFECTED:
            if (
                self.signature is None
                or not self.signature
                or len(self.signature) > 256
                or any(ord(character) < 32 or ord(character) == 127 for character in self.signature)
            ):
                raise ValueError("infected malware results require a bounded signature")
        elif self.signature is not None:
            raise ValueError("non-infected malware results must not carry a signature")


class ContentSafetyOutcome(StrEnum):
    PASS = "PASS"
    REVIEW = "REVIEW"
    BLOCK = "BLOCK"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    TERMINAL_FAILURE = "TERMINAL_FAILURE"


@dataclass(frozen=True, slots=True)
class ContentSafetyConfiguredIdentity:
    provider: str
    endpoint: str
    service: str
    sdk_version: str
    policy_version: str
    mapping_version: str

    def __post_init__(self) -> None:
        for value, field, maximum in (
            (self.provider, "provider", 64),
            (self.endpoint, "endpoint", 255),
            (self.service, "service", 128),
            (self.sdk_version, "sdk_version", 64),
            (self.policy_version, "policy_version", 64),
            (self.mapping_version, "mapping_version", 64),
        ):
            if (
                not value
                or len(value) > maximum
                or any(ord(character) < 32 or ord(character) == 127 for character in value)
            ):
                raise ValueError(f"content-safety configured {field} is invalid")


@dataclass(frozen=True, slots=True)
class ContentSafetyLabel:
    code: str
    confidence: float | None

    def __post_init__(self) -> None:
        normalized_code = self.code.strip()
        if (
            not normalized_code
            or len(normalized_code) > 128
            or not normalized_code.isascii()
            or not normalized_code[0].isalnum()
            or any(
                not (character.isalnum() or character in {"_", "-", ".", ":"})
                for character in normalized_code
            )
        ):
            raise ValueError("content-safety label code is invalid")
        if self.confidence is not None and (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float))
            or not math.isfinite(self.confidence)
            or not 0 <= self.confidence <= 100
        ):
            raise ValueError("content-safety confidence must be between 0 and 100")
        object.__setattr__(self, "code", normalized_code)
        if self.confidence is not None:
            object.__setattr__(self, "confidence", float(self.confidence))


@dataclass(frozen=True, slots=True)
class ContentSafetyImageRequest:
    data_id: str
    content_sha256: str
    image_url: str | None = None
    image_url_expires_at: datetime | None = None
    controlled_reference_id: str | None = None
    oss_region: str | None = None
    oss_bucket: str | None = None
    oss_object: str | None = None

    def __post_init__(self) -> None:
        if (
            not self.data_id
            or len(self.data_id) > 64
            or any(
                not character.isascii() or not (character.isalnum() or character in {"_", "-"})
                for character in self.data_id
            )
        ):
            raise ValueError("content-safety data_id must be a non-sensitive ASCII identity")
        if len(self.content_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.content_sha256
        ):
            raise ValueError("content-safety SHA-256 must be lowercase hexadecimal")

        url_values = (
            self.image_url,
            self.image_url_expires_at,
            self.controlled_reference_id,
        )
        oss_values = (self.oss_region, self.oss_bucket, self.oss_object)
        has_url = all(value is not None for value in url_values)
        has_oss = all(value is not None for value in oss_values)
        if (
            has_url == has_oss
            or (any(value is not None for value in url_values) and not has_url)
            or (any(value is not None for value in oss_values) and not has_oss)
        ):
            raise ValueError("content-safety request requires exactly one complete image source")
        if has_url:
            assert self.image_url is not None
            assert self.image_url_expires_at is not None
            assert self.controlled_reference_id is not None
            parsed = urlsplit(self.image_url)
            if (
                parsed.scheme != "https"
                or parsed.hostname is None
                or parsed.username is not None
                or parsed.password is not None
                or parsed.fragment
            ):
                raise ValueError(
                    "content-safety URL must be a controlled credential-free HTTPS URL"
                )
            if (
                self.image_url_expires_at.tzinfo is None
                or self.image_url_expires_at.utcoffset() != timedelta(0)
            ):
                raise ValueError("content-safety URL expiry must be timezone-aware UTC")
            if (
                not self.controlled_reference_id
                or len(self.controlled_reference_id) > 128
                or not self.controlled_reference_id.isascii()
                or any(
                    not (character.isalnum() or character in {"_", "-", ".", ":"})
                    for character in self.controlled_reference_id
                )
            ):
                raise ValueError(
                    "content-safety URL requires an application-issued reference identity"
                )
        else:
            assert self.oss_region is not None
            assert self.oss_bucket is not None
            assert self.oss_object is not None
            if self.controlled_reference_id is not None:
                raise ValueError("content-safety controlled reference identity is URL-only")
            for value, field, maximum in (
                (self.oss_region, "region", 64),
                (self.oss_bucket, "bucket", 255),
                (self.oss_object, "object", 1024),
            ):
                if (
                    not value
                    or len(value) > maximum
                    or any(ord(character) < 32 or ord(character) == 127 for character in value)
                ):
                    raise ValueError(f"content-safety OSS {field} is invalid")


@dataclass(frozen=True, slots=True)
class ContentSafetyResult:
    outcome: ContentSafetyOutcome
    provider: str
    endpoint: str
    service: str
    sdk_version: str
    policy_version: str
    mapping_version: str
    request_id: str | None
    risk_level: str | None
    labels: tuple[ContentSafetyLabel, ...]
    failure_code: str | None
    retry_after_seconds: int | None
    latency_ms: int

    def __post_init__(self) -> None:
        for value, field, maximum in (
            (self.provider, "provider", 64),
            (self.endpoint, "endpoint", 255),
            (self.service, "service", 128),
            (self.sdk_version, "sdk_version", 64),
            (self.policy_version, "policy_version", 64),
            (self.mapping_version, "mapping_version", 64),
        ):
            if (
                not value
                or len(value) > maximum
                or any(ord(character) < 32 or ord(character) == 127 for character in value)
            ):
                raise ValueError(f"content-safety {field} is invalid")
        for value, field, maximum in (
            (self.request_id, "request ID", 128),
            (self.risk_level, "risk level", 64),
            (self.failure_code, "failure code", 64),
        ):
            if value is not None and (
                not value
                or len(value) > maximum
                or not value.isascii()
                or any(
                    not (character.isalnum() or character in {"_", "-", ".", ":"})
                    for character in value
                )
            ):
                raise ValueError(f"content-safety {field} is invalid")
        if len(self.labels) > 128:
            raise ValueError("content-safety label count exceeds the configured bound")
        if any(not isinstance(label, ContentSafetyLabel) for label in self.labels):
            raise ValueError("content-safety labels must use the normalized contract")
        if len({label.code for label in self.labels}) != len(self.labels):
            raise ValueError("content-safety labels must be unique")
        if tuple(sorted(self.labels, key=lambda label: label.code)) != self.labels:
            raise ValueError("content-safety labels must be canonically ordered")
        if isinstance(self.latency_ms, bool) or self.latency_ms < 0:
            raise ValueError("content-safety latency must not be negative")
        if self.retry_after_seconds is not None and (
            isinstance(self.retry_after_seconds, bool) or not 0 <= self.retry_after_seconds <= 300
        ):
            raise ValueError("content-safety retry-after exceeds the configured bound")
        if self.outcome in {
            ContentSafetyOutcome.RETRYABLE_FAILURE,
            ContentSafetyOutcome.TERMINAL_FAILURE,
        }:
            if not self.failure_code or self.labels or self.risk_level is not None:
                raise ValueError("failed content-safety results require only failure facts")
            if (
                self.outcome == ContentSafetyOutcome.TERMINAL_FAILURE
                and self.retry_after_seconds is not None
            ):
                raise ValueError("terminal content-safety failures cannot request a retry")
        elif self.failure_code is not None or self.retry_after_seconds is not None:
            raise ValueError("content-safety policy results must not carry failure facts")


class ProvenanceVerificationOutcome(StrEnum):
    EVIDENCE = "EVIDENCE"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"


class ProvenanceEvidenceStatus(StrEnum):
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    CONFLICTING = "CONFLICTING"
    NOT_PRESENT = "NOT_PRESENT"


@dataclass(frozen=True, slots=True)
class ProvenanceConfiguredIdentity:
    validator: str
    sdk_version: str
    trust_config_version: str
    trust_config_sha256: str

    def __post_init__(self) -> None:
        for value, field, maximum in (
            (self.validator, "validator", 64),
            (self.sdk_version, "SDK version", 64),
            (self.trust_config_version, "trust configuration version", 128),
        ):
            if (
                not value
                or len(value) > maximum
                or not value.isascii()
                or any(
                    not (character.isalnum() or character in {"_", "-", ".", ":"})
                    for character in value
                )
            ):
                raise ValueError(f"provenance configured {field} is invalid")
        if len(self.trust_config_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.trust_config_sha256
        ):
            raise ValueError("provenance configured trust hash is invalid")


@dataclass(frozen=True, slots=True)
class ProvenanceVerificationResult:
    outcome: ProvenanceVerificationOutcome
    status: ProvenanceEvidenceStatus | None
    validator: str
    sdk_version: str
    trust_config_version: str
    trust_config_sha256: str
    validation_state: str | None
    manifest_count: int
    failure_codes: tuple[str, ...]
    remote_manifest_fetch: bool
    failure_code: str | None
    latency_ms: int

    def __post_init__(self) -> None:
        for value, field, maximum in (
            (self.validator, "validator", 64),
            (self.sdk_version, "SDK version", 64),
            (self.trust_config_version, "trust configuration version", 128),
        ):
            if (
                not value
                or len(value) > maximum
                or not value.isascii()
                or any(
                    not (character.isalnum() or character in {"_", "-", ".", ":"})
                    for character in value
                )
            ):
                raise ValueError(f"provenance {field} is invalid")
        if len(self.trust_config_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.trust_config_sha256
        ):
            raise ValueError("provenance trust configuration hash is invalid")
        if self.validation_state not in {None, "TRUSTED", "VALID", "INVALID"}:
            raise ValueError("provenance validation state is invalid")
        if isinstance(self.manifest_count, bool) or not 0 <= self.manifest_count <= 1024:
            raise ValueError("provenance manifest count is invalid")
        if len(self.failure_codes) > 128:
            raise ValueError("provenance failure-code count exceeds the bound")
        if tuple(sorted(set(self.failure_codes))) != self.failure_codes:
            raise ValueError("provenance failure codes must be unique and ordered")
        for code in self.failure_codes:
            if (
                not code
                or len(code) > 128
                or not code.isascii()
                or any(
                    not (character.isalnum() or character in {"_", "-", ".", ":"})
                    for character in code
                )
            ):
                raise ValueError("provenance failure code is invalid")
        if self.remote_manifest_fetch is not False:
            raise ValueError(
                "provenance remote manifest fetch requires a separate controlled fetcher"
            )
        if isinstance(self.latency_ms, bool) or self.latency_ms < 0:
            raise ValueError("provenance latency must not be negative")
        if self.failure_code is not None and (
            not self.failure_code
            or len(self.failure_code) > 64
            or not self.failure_code.isascii()
            or any(
                not (character.isalnum() or character in {"_", "-", ".", ":"})
                for character in self.failure_code
            )
        ):
            raise ValueError("provenance adapter failure code is invalid")
        if self.outcome == ProvenanceVerificationOutcome.RETRYABLE_FAILURE:
            if (
                self.status is not None
                or not self.failure_code
                or self.validation_state is not None
                or self.manifest_count != 0
                or self.failure_codes
            ):
                raise ValueError(
                    "retryable provenance results require only normalized failure facts"
                )
        elif self.status is None or self.failure_code is not None:
            raise ValueError("provenance evidence results require an evidence status")
