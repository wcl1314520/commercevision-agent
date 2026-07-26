"""Bounded C2PA provenance verification and deterministic test adapter."""

from __future__ import annotations

import hashlib
import io
import json
import re
import threading
import time
from collections.abc import Callable
from importlib.metadata import version
from types import TracebackType
from typing import Any, BinaryIO, Protocol

from commercevision_contracts.validation import (
    ProvenanceConfiguredIdentity,
    ProvenanceEvidenceStatus,
    ProvenanceVerificationOutcome,
    ProvenanceVerificationResult,
)

from .c2pa_normalization import C2paEvidenceNormalizer, C2paReaderEvidence
from .c2pa_subprocess import (
    C2paReaderTimeout,
    C2paReaderUnavailable,
    KillableC2paReaderBoundary,
)


class _Reader(Protocol):
    def __enter__(self) -> _Reader: ...

    def __exit__(self, *args: object) -> object: ...

    def json(self) -> str: ...

    def get_validation_state(self) -> str | None: ...

    def get_validation_results(self) -> dict[str, object] | None: ...


class _ReaderFactory(Protocol):
    def __call__(
        self,
        mime_type: str,
        stream: io.BytesIO,
        *,
        context: object,
    ) -> _Reader | None: ...


class _Context(Protocol):
    def __enter__(self) -> object: ...

    def __exit__(self, *args: object) -> object: ...


class _ReaderBoundary(Protocol):
    def read(
        self,
        *,
        mime_type: str,
        asset_bytes: bytes,
        settings: dict[str, object],
        timeout_seconds: float,
    ) -> C2paReaderEvidence | None: ...

    def close(self) -> None: ...


class _InProcessReaderBoundary:
    """Test-only boundary for deterministic injected Reader doubles."""

    def __init__(
        self,
        *,
        reader_factory: _ReaderFactory,
        context_factory: Callable[[dict[str, object]], _Context],
        normalizer: C2paEvidenceNormalizer,
    ) -> None:
        self._reader_factory = reader_factory
        self._context_factory = context_factory
        self._normalizer = normalizer

    def read(
        self,
        *,
        mime_type: str,
        asset_bytes: bytes,
        settings: dict[str, object],
        timeout_seconds: float,
    ) -> C2paReaderEvidence | None:
        del timeout_seconds
        with self._context_factory(settings) as context:
            reader = self._reader_factory(
                mime_type,
                io.BytesIO(asset_bytes),
                context=context,
            )
            if reader is None:
                return None
            with reader:
                return self._normalizer.normalize(
                    manifest_json=reader.json(),
                    validation_state=reader.get_validation_state(),
                    validation_results=reader.get_validation_results(),
                )

    def close(self) -> None:
        return None


class C2paProvenanceAdapter:
    """Verify embedded credentials without remote fetches or claim persistence."""

    _SUPPORTED_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})

    def __init__(
        self,
        *,
        reader_factory: _ReaderFactory | None = None,
        context_factory: Callable[[dict[str, object]], _Context] | None = None,
        reader_boundary: _ReaderBoundary | None = None,
        sdk_version: str,
        trust_config_version: str,
        trust_anchors_pem: str,
        trust_eku_policy: str,
        timeout_seconds: float,
        maximum_concurrency: int,
        maximum_asset_bytes: int,
        maximum_report_bytes: int,
        maximum_report_depth: int,
        maximum_report_nodes: int,
        maximum_manifests: int,
        maximum_status_codes: int,
    ) -> None:
        if not sdk_version or len(sdk_version) > 64:
            raise ValueError("C2PA SDK version is invalid")
        if not trust_config_version or len(trust_config_version) > 128:
            raise ValueError("C2PA trust configuration version is invalid")
        if not trust_anchors_pem or len(trust_anchors_pem.encode("utf-8")) > 1024 * 1024:
            raise ValueError("C2PA trust anchors are missing or exceed the bound")
        eku_lines = tuple(line.strip() for line in trust_eku_policy.splitlines() if line.strip())
        if (
            not eku_lines
            or len(eku_lines) > 32
            or len(set(eku_lines)) != len(eku_lines)
            or any(
                len(line) > 128 or re.fullmatch(r"[0-2](?:\.(?:0|[1-9][0-9]*))+", line) is None
                for line in eku_lines
            )
        ):
            raise ValueError("C2PA trust EKU policy is invalid")
        if timeout_seconds <= 0 or maximum_concurrency < 1:
            raise ValueError("C2PA execution bounds are invalid")
        for value, name in (
            (maximum_asset_bytes, "asset bytes"),
            (maximum_report_bytes, "report bytes"),
            (maximum_report_depth, "report depth"),
            (maximum_report_nodes, "report nodes"),
            (maximum_manifests, "manifest count"),
            (maximum_status_codes, "status-code count"),
        ):
            if value < 1:
                raise ValueError(f"C2PA maximum {name} must be positive")
        if maximum_manifests > 1024 or maximum_status_codes > 128:
            raise ValueError("C2PA persisted evidence bounds exceed the contract")

        normalizer = C2paEvidenceNormalizer(
            maximum_report_bytes=maximum_report_bytes,
            maximum_report_depth=maximum_report_depth,
            maximum_report_nodes=maximum_report_nodes,
            maximum_manifests=maximum_manifests,
            maximum_status_codes=maximum_status_codes,
        )
        if reader_boundary is None:
            if reader_factory is None or context_factory is None:
                raise ValueError("C2PA Reader boundary is required")
            reader_boundary = _InProcessReaderBoundary(
                reader_factory=reader_factory,
                context_factory=context_factory,
                normalizer=normalizer,
            )
        elif reader_factory is not None or context_factory is not None:
            raise ValueError("C2PA Reader boundary configuration is ambiguous")
        self._reader_boundary = reader_boundary
        self._trust_anchors_pem = trust_anchors_pem
        self._trust_eku_policy = "\n".join(eku_lines)
        trust_config_sha256 = hashlib.sha256(
            json.dumps(
                {
                    "trust_anchors": trust_anchors_pem,
                    "trust_config": self._trust_eku_policy,
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self._configured_identity = ProvenanceConfiguredIdentity(
            validator="c2pa",
            sdk_version=sdk_version,
            trust_config_version=trust_config_version,
            trust_config_sha256=trust_config_sha256,
        )
        self._timeout_seconds = timeout_seconds
        self._maximum_asset_bytes = maximum_asset_bytes
        self._capacity = threading.BoundedSemaphore(maximum_concurrency)

    @classmethod
    def from_runtime(
        cls,
        *,
        subprocess_memory_limit_bytes: int = 512 * 1024 * 1024,
        subprocess_file_descriptor_limit: int = 64,
        **kwargs: Any,
    ) -> C2paProvenanceAdapter:
        """Build the official c2pa-python Reader/Context boundary."""

        boundary_values: dict[str, int] = {}
        for name in (
            "maximum_asset_bytes",
            "maximum_report_bytes",
            "maximum_report_depth",
            "maximum_report_nodes",
            "maximum_manifests",
            "maximum_status_codes",
        ):
            value = kwargs.get(name)
            if not isinstance(value, int):
                raise ValueError(f"C2PA {name} is required")
            boundary_values[name] = value
        return cls(
            reader_boundary=KillableC2paReaderBoundary(
                **boundary_values,
                memory_limit_bytes=subprocess_memory_limit_bytes,
                file_descriptor_limit=subprocess_file_descriptor_limit,
            ),
            sdk_version=version("c2pa-python"),
            **kwargs,
        )

    def verify(
        self,
        *,
        mime_type: str,
        stream: BinaryIO,
        byte_length: int,
    ) -> ProvenanceVerificationResult:
        started = time.monotonic()
        deadline = started + self._timeout_seconds
        if mime_type not in self._SUPPORTED_MIME_TYPES:
            return self._conflicting(
                "UNSUPPORTED_CREDENTIAL_CONTAINER",
                started=started,
            )
        asset_bytes = self._read_bounded(stream, byte_length=byte_length)
        if asset_bytes is None:
            return self._conflicting("MALFORMED_ASSET_STREAM", started=started)

        remaining = self._remaining(deadline)
        if remaining is None:
            return self._retryable("PROVENANCE_TIMEOUT", started=started)
        acquired = self._capacity.acquire(timeout=remaining)
        if not acquired:
            return self._retryable(
                "PROVENANCE_CONCURRENCY_SATURATED",
                started=started,
            )
        remaining = self._remaining(deadline)
        if remaining is None:
            self._capacity.release()
            return self._retryable("PROVENANCE_TIMEOUT", started=started)

        try:
            reader_evidence = self._reader_boundary.read(
                mime_type=mime_type,
                asset_bytes=asset_bytes,
                settings=self._reader_settings(),
                timeout_seconds=remaining,
            )
            if self._remaining(deadline) is None:
                return self._retryable("PROVENANCE_TIMEOUT", started=started)
        except (C2paReaderTimeout, TimeoutError):
            return self._retryable("PROVENANCE_TIMEOUT", started=started)
        except (MemoryError, C2paReaderUnavailable, OSError):
            return self._retryable("PROVENANCE_READER_UNAVAILABLE", started=started)
        except Exception:
            return self._conflicting("MALFORMED_CREDENTIAL", started=started)
        finally:
            self._capacity.release()

        if reader_evidence is None:
            return self._evidence(
                status=ProvenanceEvidenceStatus.NOT_PRESENT,
                validation_state=None,
                manifest_count=0,
                failure_codes=(),
                started=started,
            )
        return self._evidence(
            status=reader_evidence.status,
            validation_state=reader_evidence.validation_state,
            manifest_count=reader_evidence.manifest_count,
            failure_codes=reader_evidence.failure_codes,
            started=started,
        )

    @property
    def configured_identity(self) -> ProvenanceConfiguredIdentity:
        return self._configured_identity

    def close(self) -> None:
        self._reader_boundary.close()

    def __enter__(self) -> C2paProvenanceAdapter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _read_bounded(self, stream: BinaryIO, *, byte_length: int) -> bytes | None:
        if (
            isinstance(byte_length, bool)
            or byte_length < 1
            or byte_length > self._maximum_asset_bytes
        ):
            return None
        chunks: list[bytes] = []
        remaining = byte_length
        while remaining:
            chunk = stream.read(min(remaining, 64 * 1024))
            if not isinstance(chunk, bytes) or not chunk:
                return None
            chunks.append(chunk)
            remaining -= len(chunk)
        trailing = stream.read(1)
        if trailing not in {b"", None}:
            return None
        return b"".join(chunks)

    def _reader_settings(self) -> dict[str, object]:
        return {
            "version": 1,
            "core": {"allowed_network_hosts": []},
            "trust": {
                "trust_anchors": self._trust_anchors_pem,
                "trust_config": self._trust_eku_policy,
            },
            "verify": {
                "ocsp_fetch": False,
                "remote_manifest_fetch": False,
                "verify_after_reading": True,
                "verify_timestamp_trust": True,
                "verify_trust": True,
            },
        }

    def _conflicting(
        self,
        code: str,
        *,
        started: float,
    ) -> ProvenanceVerificationResult:
        return self._evidence(
            status=ProvenanceEvidenceStatus.CONFLICTING,
            validation_state=None,
            manifest_count=0,
            failure_codes=(code,),
            started=started,
        )

    def _evidence(
        self,
        *,
        status: ProvenanceEvidenceStatus,
        validation_state: str | None,
        manifest_count: int,
        failure_codes: tuple[str, ...],
        started: float,
    ) -> ProvenanceVerificationResult:
        return ProvenanceVerificationResult(
            outcome=ProvenanceVerificationOutcome.EVIDENCE,
            status=status,
            validator=self._configured_identity.validator,
            sdk_version=self._configured_identity.sdk_version,
            trust_config_version=self._configured_identity.trust_config_version,
            trust_config_sha256=self._configured_identity.trust_config_sha256,
            validation_state=validation_state,
            manifest_count=manifest_count,
            failure_codes=tuple(sorted(set(failure_codes))),
            remote_manifest_fetch=False,
            failure_code=None,
            latency_ms=self._latency(started),
        )

    def _retryable(
        self,
        failure_code: str,
        *,
        started: float,
    ) -> ProvenanceVerificationResult:
        return ProvenanceVerificationResult(
            outcome=ProvenanceVerificationOutcome.RETRYABLE_FAILURE,
            status=None,
            validator=self._configured_identity.validator,
            sdk_version=self._configured_identity.sdk_version,
            trust_config_version=self._configured_identity.trust_config_version,
            trust_config_sha256=self._configured_identity.trust_config_sha256,
            validation_state=None,
            manifest_count=0,
            failure_codes=(),
            remote_manifest_fetch=False,
            failure_code=failure_code,
            latency_ms=self._latency(started),
        )

    @staticmethod
    def _remaining(deadline: float) -> float | None:
        remaining = deadline - time.monotonic()
        return remaining if remaining > 0 else None

    @staticmethod
    def _latency(started: float) -> int:
        return max(0, round((time.monotonic() - started) * 1000))


class DeterministicProvenanceAdapter:
    """Explicit local/test adapter for provenance stage orchestration."""

    def __init__(
        self,
        *,
        status: ProvenanceEvidenceStatus,
        trust_config_version: str,
    ) -> None:
        self._status = status
        self._configured_identity = ProvenanceConfiguredIdentity(
            validator="deterministic-c2pa",
            sdk_version="deterministic-v1",
            trust_config_version=trust_config_version,
            trust_config_sha256=hashlib.sha256(trust_config_version.encode("ascii")).hexdigest(),
        )

    @property
    def configured_identity(self) -> ProvenanceConfiguredIdentity:
        return self._configured_identity

    def verify(
        self,
        *,
        mime_type: str,
        stream: BinaryIO,
        byte_length: int,
    ) -> ProvenanceVerificationResult:
        del mime_type, stream, byte_length
        return ProvenanceVerificationResult(
            outcome=ProvenanceVerificationOutcome.EVIDENCE,
            status=self._status,
            validator=self._configured_identity.validator,
            sdk_version=self._configured_identity.sdk_version,
            trust_config_version=self._configured_identity.trust_config_version,
            trust_config_sha256=self._configured_identity.trust_config_sha256,
            validation_state=(
                "TRUSTED" if self._status == ProvenanceEvidenceStatus.VERIFIED else None
            ),
            manifest_count=(0 if self._status == ProvenanceEvidenceStatus.NOT_PRESENT else 1),
            failure_codes=(
                ("DETERMINISTIC_CONFLICT",)
                if self._status == ProvenanceEvidenceStatus.CONFLICTING
                else ()
            ),
            remote_manifest_fetch=False,
            failure_code=None,
            latency_ms=0,
        )
