"""Deterministic and Alibaba Model Studio VisionAnalyzer adapters."""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from types import TracebackType
from typing import Any
from urllib.parse import urlsplit

import httpx
from commercevision_contracts.product_briefs import (
    ProductBriefEvidenceKind,
    ProductBriefEvidenceOutput,
    ProductBriefFieldConflict,
    ProductBriefFieldOutput,
    ProductBriefFieldValueKind,
    ProviderArtifactKind,
    ProviderArtifactPersistenceError,
    ProviderArtifactReference,
    ProviderArtifactSink,
    ProviderArtifactWrite,
    VisionAnalysisRequest,
    VisionAnalyzerIdentity,
    VisionCallLifecycle,
    VisionProviderCall,
    VisionProviderError,
    VisionProviderOutcome,
    VisionProviderStatus,
    VisionProviderUsage,
    VisionStructuredOutput,
    product_brief_field_paths,
    product_brief_field_value_kind,
)
from commercevision_contracts.vision_configuration import (
    alibaba_vision_configuration_snapshot_sha256,
    deterministic_vision_configuration_snapshot_sha256,
)
from pydantic import ValidationError

from .vision_credentials import (
    StaticVisionApiKeyProvider,
    VisionApiKeyProvider,
)
from .vision_transport import (
    AsyncVisionHttpTransport,
    VisionCredentialUnavailableTransportError,
    VisionHttpResponseEvidence,
    VisionSafeToRetryTransportError,
    VisionSubmissionOutcomeUnknownError,
)


class DeterministicVisionScenario(StrEnum):
    SUCCESS = "SUCCESS"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    CONFLICT = "CONFLICT"
    SENSITIVE = "SENSITIVE"
    MALFORMED = "MALFORMED"
    TIMEOUT = "TIMEOUT"
    REJECTED = "REJECTED"
    THROTTLED = "THROTTLED"
    UNKNOWN = "UNKNOWN"


_FIELD_VALUE_PROMPT_CONTRACTS: dict[ProductBriefFieldValueKind, dict[str, object]] = {
    ProductBriefFieldValueKind.IDENTITY: {
        "kind": "IDENTITY",
        "display_name": "string",
        "model_number": "string or null",
        "variant": "string or null",
    },
    ProductBriefFieldValueKind.CATEGORY: {
        "kind": "CATEGORY",
        "code": "non-empty string",
        "label": "non-empty string",
    },
    ProductBriefFieldValueKind.TEXT: {
        "kind": "TEXT",
        "text": "string",
    },
    ProductBriefFieldValueKind.TEXT_LIST: {
        "kind": "TEXT_LIST",
        "items": "array of unique non-empty strings",
    },
    ProductBriefFieldValueKind.STATEMENT_LIST: {
        "kind": "STATEMENT_LIST",
        "statements": "array of unique non-empty strings",
    },
    ProductBriefFieldValueKind.FLAG_LIST: {
        "kind": "FLAG_LIST",
        "flags": "array of unique non-empty strings",
    },
    ProductBriefFieldValueKind.DIMENSION_LIST: {
        "kind": "DIMENSION_LIST",
        "dimensions": [
            {
                "name": "non-empty string",
                "value": "non-empty string",
                "unit": "string or null",
                "raw_text": "string or null",
            }
        ],
    },
}


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _assert_json_complexity(
    value: object,
    *,
    maximum_depth: int,
    maximum_nodes: int,
    maximum_string_bytes: int,
) -> None:
    nodes = 0
    active_containers: set[int] = set()
    pending: list[tuple[object, int, bool]] = [(value, 0, False)]
    while pending:
        current, depth, exiting = pending.pop()
        if exiting:
            active_containers.remove(id(current))
            continue
        nodes += 1
        if depth > maximum_depth:
            raise ValueError("Provider JSON exceeds the depth budget")
        if nodes > maximum_nodes:
            raise ValueError("Provider JSON exceeds the node budget")
        if isinstance(current, float) and not math.isfinite(current):
            raise ValueError("Provider JSON contains a non-finite number")
        if isinstance(current, str) and len(current.encode("utf-8")) > maximum_string_bytes:
            raise ValueError("Provider JSON exceeds the string budget")
        if isinstance(current, dict):
            if id(current) in active_containers:
                raise ValueError("Provider JSON contains a circular container")
            active_containers.add(id(current))
            pending.append((current, depth, True))
            for key in current:
                if not isinstance(key, str):
                    raise ValueError("Provider JSON object keys must be strings")
                if len(key.encode("utf-8")) > maximum_string_bytes:
                    raise ValueError("Provider JSON exceeds the string budget")
            pending.extend((item, depth + 1, False) for item in current.values())
        elif isinstance(current, list):
            if id(current) in active_containers:
                raise ValueError("Provider JSON contains a circular container")
            active_containers.add(id(current))
            pending.append((current, depth, True))
            pending.extend((item, depth + 1, False) for item in current)
        elif current is not None and not isinstance(current, str | int | float | bool):
            raise ValueError("Provider JSON contains an unsupported value")


def _assert_product_facts_budget(
    value: object,
    *,
    maximum_bytes: int,
    maximum_depth: int,
    maximum_nodes: int,
    maximum_string_bytes: int,
) -> None:
    _assert_json_complexity(
        value,
        maximum_depth=maximum_depth,
        maximum_nodes=maximum_nodes,
        maximum_string_bytes=maximum_string_bytes,
    )
    if len(_canonical_bytes(value)) > maximum_bytes:
        raise ValueError("Vision Product facts exceed the canonical byte budget")


def _write_artifact(
    *,
    sink: ProviderArtifactSink,
    request: VisionAnalysisRequest,
    lifecycle: VisionCallLifecycle | None,
    call_index: int,
    kind: ProviderArtifactKind,
    payload: bytes,
) -> ProviderArtifactReference:
    artifact = ProviderArtifactWrite(
        operation_id=request.operation_id,
        operation_attempt=request.operation_attempt,
        call_index=call_index,
        kind=kind,
        content_type="application/json",
        payload=payload,
        sha256=hashlib.sha256(payload).hexdigest(),
        retention_class=request.retention_class,
        retention_deadline=request.retention_deadline,
    )
    store_artifact = getattr(lifecycle, "store_artifact", None) if lifecycle is not None else None
    if store_artifact is not None:
        reference = store_artifact(artifact)
        if reference is not None:
            return reference
    return sink.write(artifact)


def _request_artifact_document(request: VisionAnalysisRequest) -> dict[str, object]:
    return {
        "category": request.category.value,
        "category_schema_version": request.category_schema_version,
        "common_schema_version": request.common_schema_version,
        "images": [
            {
                "asset_version_id": image.asset_version_id,
                "content_sha256": image.content_sha256,
                "expires_at": image.expires_at.isoformat(),
                "required_headers": {
                    name: value.get_secret_value() for name, value in image.required_headers.items()
                },
                "url": image.url.get_secret_value(),
            }
            for image in request.images
        ],
        "policy_version": request.policy_version,
        "product_facts": request.product_facts,
        "prompt_version": request.prompt_version,
    }


def _deterministic_output(
    request: VisionAnalysisRequest,
    scenario: DeterministicVisionScenario,
) -> VisionStructuredOutput:
    fields: list[ProductBriefFieldOutput] = []
    for path in product_brief_field_paths(request.category):
        confidence = Decimal("0.9600")
        conflict = ProductBriefFieldConflict.NONE
        sensitive = False
        if scenario == DeterministicVisionScenario.LOW_CONFIDENCE and path == "common.brand":
            confidence = Decimal("0.4500")
        if scenario == DeterministicVisionScenario.CONFLICT and path == "common.colors":
            conflict = ProductBriefFieldConflict.CONFLICTING
        if scenario == DeterministicVisionScenario.SENSITIVE and path in {
            "beauty.medical_like_claim_flags",
            "automotive.safety_critical_claim_flags",
        }:
            sensitive = True
        value = _deterministic_field_value(
            path=path,
            category=request.category.value,
            sensitive=sensitive,
        )
        fields.append(
            ProductBriefFieldOutput(
                path=path,
                value=value,
                confidence=confidence,
                conflict=conflict,
                review_required=False,
                sensitive=sensitive,
                evidence=(
                    ProductBriefEvidenceOutput(
                        source_asset_version_id=request.images[0].asset_version_id,
                        kind=ProductBriefEvidenceKind.IMAGE_REGION,
                        reference=(
                            "asset-region://"
                            + hashlib.sha256(
                                (
                                    f"{request.images[0].asset_version_id}:"
                                    "deterministic-provider-evidence"
                                ).encode()
                            ).hexdigest()
                        ),
                        region=(0.1, 0.1, 0.9, 0.9),
                        excerpt_sha256=request.images[0].content_sha256,
                    ),
                ),
            )
        )
    return VisionStructuredOutput(
        common_schema_version=request.common_schema_version,
        category_schema_version=request.category_schema_version,
        category=request.category,
        fields=tuple(fields),
    )


def _deterministic_field_value(
    *,
    path: str,
    category: str,
    sensitive: bool,
) -> dict[str, object]:
    kind = product_brief_field_value_kind(path)
    if kind == ProductBriefFieldValueKind.IDENTITY:
        return {
            "kind": kind.value,
            "display_name": path,
            "model_number": None,
            "variant": None,
        }
    if kind == ProductBriefFieldValueKind.CATEGORY:
        return {
            "kind": kind.value,
            "code": category.lower(),
            "label": category.title(),
        }
    if kind == ProductBriefFieldValueKind.TEXT:
        return {"kind": kind.value, "text": path}
    if kind == ProductBriefFieldValueKind.TEXT_LIST:
        return {"kind": kind.value, "items": [path]}
    if kind == ProductBriefFieldValueKind.STATEMENT_LIST:
        return {"kind": kind.value, "statements": []}
    if kind == ProductBriefFieldValueKind.FLAG_LIST:
        return {
            "kind": kind.value,
            "flags": ["sensitive deterministic claim"] if sensitive else [],
        }
    if kind == ProductBriefFieldValueKind.DIMENSION_LIST:
        return {"kind": kind.value, "dimensions": []}
    raise AssertionError(f"unhandled ProductBrief field value kind: {kind}")


class DeterministicVisionAnalyzer:
    """Stable fixture implementing the production adapter contract."""

    def __init__(
        self,
        *,
        scenario: DeterministicVisionScenario = DeterministicVisionScenario.SUCCESS,
        artifact_sink: ProviderArtifactSink,
        prompt_version: str = "product-brief-prompt-v1",
        maximum_output_tokens: int = 4096,
        product_facts_maximum_bytes: int = 64 * 1024,
        product_facts_maximum_depth: int = 8,
        product_facts_maximum_nodes: int = 1024,
        product_facts_maximum_string_bytes: int = 4096,
        clock: Any | None = None,
    ) -> None:
        if not 1 <= maximum_output_tokens <= 32_768:
            raise ValueError("Deterministic Vision output token budget is invalid")
        if not 2 <= product_facts_maximum_bytes <= 512 * 1024:
            raise ValueError("Vision Product facts canonical byte budget is invalid")
        if not 1 <= product_facts_maximum_depth <= 32:
            raise ValueError("Vision Product facts depth budget is invalid")
        if not 1 <= product_facts_maximum_nodes <= 10_000:
            raise ValueError("Vision Product facts node budget is invalid")
        if not 1 <= product_facts_maximum_string_bytes <= 64 * 1024:
            raise ValueError("Vision Product facts string budget is invalid")
        self._scenario = DeterministicVisionScenario(scenario)
        self._artifact_sink = artifact_sink
        self._prompt_version = prompt_version
        self._maximum_output_tokens = maximum_output_tokens
        self._product_facts_maximum_bytes = product_facts_maximum_bytes
        self._product_facts_maximum_depth = product_facts_maximum_depth
        self._product_facts_maximum_nodes = product_facts_maximum_nodes
        self._product_facts_maximum_string_bytes = product_facts_maximum_string_bytes
        self._clock = clock or (lambda: datetime.now(UTC))
        self._config_hash = deterministic_vision_configuration_snapshot_sha256(
            maximum_output_tokens=maximum_output_tokens,
            product_facts_maximum_bytes=product_facts_maximum_bytes,
            product_facts_maximum_depth=product_facts_maximum_depth,
            product_facts_maximum_nodes=product_facts_maximum_nodes,
            product_facts_maximum_string_bytes=product_facts_maximum_string_bytes,
            prompt_version=prompt_version,
        )

    @property
    def configured_identity(self) -> VisionAnalyzerIdentity:
        return VisionAnalyzerIdentity(
            provider="deterministic-vision",
            endpoint_region="local",
            endpoint_host="deterministic.invalid",
            requested_model="deterministic-vision-v1",
            submitted_model_snapshot="deterministic-vision-v1",
            prompt_version=self._prompt_version,
            configuration_snapshot_sha256=self._config_hash,
        )

    def analyze(
        self,
        request: VisionAnalysisRequest,
        *,
        lifecycle: VisionCallLifecycle | None = None,
    ) -> VisionProviderOutcome:
        _ = self._clock()
        if request.prompt_version != self._prompt_version:
            raise ValueError("Vision request prompt version does not match the adapter")
        _assert_product_facts_budget(
            request.product_facts,
            maximum_bytes=self._product_facts_maximum_bytes,
            maximum_depth=self._product_facts_maximum_depth,
            maximum_nodes=self._product_facts_maximum_nodes,
            maximum_string_bytes=self._product_facts_maximum_string_bytes,
        )
        request_ref = _write_artifact(
            sink=self._artifact_sink,
            request=request,
            lifecycle=lifecycle,
            call_index=0,
            kind=ProviderArtifactKind.REQUEST,
            payload=_canonical_bytes(_request_artifact_document(request)),
        )
        if lifecycle is not None:
            lifecycle.before_submission(0)
        failure = {
            DeterministicVisionScenario.TIMEOUT: (
                VisionProviderStatus.TIMEOUT,
                VisionProviderError(
                    code="PROVIDER_TIMEOUT",
                    category="timeout",
                    message="Vision provider timed out",
                    retryable=True,
                ),
            ),
            DeterministicVisionScenario.REJECTED: (
                VisionProviderStatus.REJECTED,
                VisionProviderError(
                    code="PROVIDER_REJECTED",
                    category="provider",
                    message="Vision provider rejected the request",
                    retryable=False,
                ),
            ),
            DeterministicVisionScenario.THROTTLED: (
                VisionProviderStatus.THROTTLED,
                VisionProviderError(
                    code="PROVIDER_THROTTLED",
                    category="throttling",
                    message="Vision provider throttled the request",
                    retryable=True,
                    retry_after_seconds=5,
                ),
            ),
            DeterministicVisionScenario.UNKNOWN: (
                VisionProviderStatus.UNKNOWN,
                VisionProviderError(
                    code="PROVIDER_SUBMISSION_OUTCOME_UNKNOWN",
                    category="unknown_outcome",
                    message="Vision provider submission outcome is unknown",
                    retryable=False,
                ),
            ),
        }.get(self._scenario)
        if failure is not None:
            status, error = failure
            call = self._call(
                request=request,
                status=status,
                config_hash=self._config_hash,
                request_ref=request_ref,
                response_ref=None,
                error=error,
            )
            return self._outcome(call=call, output=None, error=error)

        output = _deterministic_output(request, self._scenario)
        response_payload = output.model_dump_json().encode()
        if self._scenario == DeterministicVisionScenario.MALFORMED:
            response_payload = b"{malformed"
        response_ref = _write_artifact(
            sink=self._artifact_sink,
            request=request,
            lifecycle=lifecycle,
            call_index=0,
            kind=ProviderArtifactKind.RESPONSE,
            payload=response_payload,
        )
        if self._scenario == DeterministicVisionScenario.MALFORMED:
            error = VisionProviderError(
                code="MALFORMED_PROVIDER_OUTPUT",
                category="provider_schema",
                message="Vision provider returned invalid structured output",
                retryable=False,
            )
            call = self._call(
                request=request,
                status=VisionProviderStatus.MALFORMED,
                config_hash=self._config_hash,
                request_ref=request_ref,
                response_ref=response_ref,
                error=error,
            )
            return self._outcome(call=call, output=None, error=error)

        call = self._call(
            request=request,
            status=VisionProviderStatus.SUCCEEDED,
            config_hash=self._config_hash,
            request_ref=request_ref,
            response_ref=response_ref,
            error=None,
        )
        return self._outcome(call=call, output=output, error=None)

    @staticmethod
    def _call(
        *,
        request: VisionAnalysisRequest,
        status: VisionProviderStatus,
        config_hash: str,
        request_ref: ProviderArtifactReference,
        response_ref: ProviderArtifactReference | None,
        error: VisionProviderError | None,
    ) -> VisionProviderCall:
        succeeded = status == VisionProviderStatus.SUCCEEDED
        return VisionProviderCall(
            call_index=0,
            status=status,
            provider="deterministic-vision",
            endpoint_region="local",
            endpoint_host="deterministic.invalid",
            requested_model="deterministic-vision-v1",
            submitted_model_snapshot="deterministic-vision-v1",
            resolved_model="deterministic-vision-v1" if succeeded else None,
            prompt_version=request.prompt_version,
            config_snapshot_sha256=config_hash,
            request_id=(
                f"deterministic-{request.operation_id}-{request.operation_attempt}"
                if succeeded
                else None
            ),
            usage=(
                VisionProviderUsage(input_tokens=1, output_tokens=1, total_tokens=2)
                if succeeded
                else VisionProviderUsage()
            ),
            latency_ms=0,
            request_artifact=request_ref,
            response_artifact=response_ref,
            error=error,
        )

    @staticmethod
    def _outcome(
        *,
        call: VisionProviderCall,
        output: VisionStructuredOutput | None,
        error: VisionProviderError | None,
    ) -> VisionProviderOutcome:
        return VisionProviderOutcome(
            status=call.status,
            provider=call.provider,
            endpoint_region=call.endpoint_region,
            endpoint_host=call.endpoint_host,
            requested_model=call.requested_model,
            submitted_model_snapshot=call.submitted_model_snapshot,
            resolved_model=call.resolved_model,
            prompt_version=call.prompt_version,
            config_snapshot_sha256=call.config_snapshot_sha256,
            request_id=call.request_id,
            usage=call.usage,
            latency_ms=call.latency_ms,
            request_artifact=call.request_artifact,
            response_artifact=call.response_artifact,
            output=output,
            error=error,
            calls=(call,),
        )


class AlibabaVisionAnalyzer:
    """Bounded Alibaba OpenAI-compatible multimodal adapter."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        credential_provider: VisionApiKeyProvider | None = None,
        endpoint: str,
        endpoint_region: str,
        requested_model: str,
        configured_snapshot: str,
        prompt_version: str,
        adapter_version: str,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        end_to_end_timeout_seconds: float,
        maximum_concurrency: int,
        maximum_response_bytes: int,
        maximum_output_tokens: int = 4096,
        product_facts_maximum_bytes: int = 64 * 1024,
        product_facts_maximum_depth: int = 8,
        product_facts_maximum_nodes: int = 1024,
        product_facts_maximum_string_bytes: int = 4096,
        maximum_repair_attempts: int,
        allowed_image_origins: frozenset[str],
        artifact_sink: ProviderArtifactSink,
        client: httpx.AsyncClient | None = None,
        clock: Any | None = None,
    ) -> None:
        if (api_key is None) == (credential_provider is None):
            raise ValueError(
                "Alibaba Vision requires exactly one static or provider credential source"
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
            raise ValueError("Alibaba Vision endpoint must be a credential-free HTTPS URL")
        if not endpoint_region or not requested_model or not configured_snapshot:
            raise ValueError("Alibaba Vision provider identity is incomplete")
        if connect_timeout_seconds <= 0 or read_timeout_seconds <= 0:
            raise ValueError("Alibaba Vision transport timeouts must be positive")
        if end_to_end_timeout_seconds <= (connect_timeout_seconds + read_timeout_seconds):
            raise ValueError("Alibaba Vision deadline must exceed its transport timeout budget")
        if maximum_concurrency < 1:
            raise ValueError("Alibaba Vision concurrency must be positive")
        if not 1 <= maximum_response_bytes < 2 * 1024 * 1024:
            raise ValueError("Alibaba Vision response bound is invalid")
        if not 1 <= maximum_output_tokens <= 32_768:
            raise ValueError("Alibaba Vision output token budget is invalid")
        if not 2 <= product_facts_maximum_bytes <= 512 * 1024:
            raise ValueError("Vision Product facts canonical byte budget is invalid")
        if not 1 <= product_facts_maximum_depth <= 32:
            raise ValueError("Vision Product facts depth budget is invalid")
        if not 1 <= product_facts_maximum_nodes <= 10_000:
            raise ValueError("Vision Product facts node budget is invalid")
        if not 1 <= product_facts_maximum_string_bytes <= 64 * 1024:
            raise ValueError("Vision Product facts string budget is invalid")
        if not 0 <= maximum_repair_attempts <= 1:
            raise ValueError("Alibaba Vision supports at most one structured-output repair")
        normalized_origins = frozenset(
            self._normalize_origin(origin) for origin in allowed_image_origins
        )
        if not normalized_origins:
            raise ValueError("Alibaba Vision requires controlled image URL origins")

        self._endpoint = endpoint.rstrip("/")
        self._endpoint_region = endpoint_region
        self._endpoint_host = parsed.hostname
        self._requested_model = requested_model
        self._configured_snapshot = configured_snapshot
        self._prompt_version = prompt_version
        self._connect_timeout = connect_timeout_seconds
        self._read_timeout = read_timeout_seconds
        self._deadline = end_to_end_timeout_seconds
        self._maximum_response_bytes = maximum_response_bytes
        self._maximum_output_tokens = maximum_output_tokens
        self._product_facts_maximum_bytes = product_facts_maximum_bytes
        self._product_facts_maximum_depth = product_facts_maximum_depth
        self._product_facts_maximum_nodes = product_facts_maximum_nodes
        self._product_facts_maximum_string_bytes = product_facts_maximum_string_bytes
        self._maximum_repair_attempts = maximum_repair_attempts
        self._allowed_image_origins = normalized_origins
        self._artifact_sink = artifact_sink
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lifecycle_condition = threading.Condition()
        self._close_lock = threading.Lock()
        self._active_lifecycles = 0
        self._closing = False
        self._closed = False
        self._transport = AsyncVisionHttpTransport(
            credential_provider=credential_provider,
            endpoint=self._endpoint,
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=read_timeout_seconds,
            maximum_concurrency=maximum_concurrency,
            maximum_response_bytes=maximum_response_bytes,
            client=client,
        )
        self._config_hash = alibaba_vision_configuration_snapshot_sha256(
            adapter_version=adapter_version,
            configured_snapshot=configured_snapshot,
            connect_timeout_seconds=connect_timeout_seconds,
            end_to_end_timeout_seconds=end_to_end_timeout_seconds,
            endpoint=self._endpoint,
            endpoint_region=endpoint_region,
            maximum_concurrency=maximum_concurrency,
            maximum_output_tokens=maximum_output_tokens,
            maximum_repair_attempts=maximum_repair_attempts,
            maximum_response_bytes=maximum_response_bytes,
            product_facts_maximum_bytes=product_facts_maximum_bytes,
            product_facts_maximum_depth=product_facts_maximum_depth,
            product_facts_maximum_nodes=product_facts_maximum_nodes,
            product_facts_maximum_string_bytes=product_facts_maximum_string_bytes,
            prompt_version=prompt_version,
            read_timeout_seconds=read_timeout_seconds,
            requested_model=requested_model,
        )

    @property
    def configured_identity(self) -> VisionAnalyzerIdentity:
        return VisionAnalyzerIdentity(
            provider="alibaba-model-studio",
            endpoint_region=self._endpoint_region,
            endpoint_host=self._endpoint_host,
            requested_model=self._requested_model,
            submitted_model_snapshot=self._configured_snapshot,
            prompt_version=self._prompt_version,
            configuration_snapshot_sha256=self._config_hash,
        )

    def analyze(
        self,
        request: VisionAnalysisRequest,
        *,
        lifecycle: VisionCallLifecycle | None = None,
    ) -> VisionProviderOutcome:
        with self._lifecycle_condition:
            if self._closing:
                raise RuntimeError("Alibaba Vision analyzer is closing or closed")
            self._transport.assert_ready()
            self._active_lifecycles += 1
        try:
            return self._analyze(request, lifecycle=lifecycle)
        finally:
            with self._lifecycle_condition:
                self._active_lifecycles -= 1
                self._lifecycle_condition.notify_all()

    def _analyze(
        self,
        request: VisionAnalysisRequest,
        *,
        lifecycle: VisionCallLifecycle | None,
    ) -> VisionProviderOutcome:
        deadline_at = time.monotonic() + self._deadline
        self._validate_request(request)
        calls: list[VisionProviderCall] = []
        malformed_content: str | None = None
        for call_index in range(self._maximum_repair_attempts + 1):
            call_started = time.monotonic()
            body = self._request_body(request, malformed_content=malformed_content)
            request_bytes = _canonical_bytes(body)
            request_ref = _write_artifact(
                sink=self._artifact_sink,
                request=request,
                lifecycle=lifecycle,
                call_index=call_index,
                kind=ProviderArtifactKind.REQUEST,
                payload=request_bytes,
            )
            if time.monotonic() >= deadline_at:
                return self._deadline_outcome(
                    calls,
                    call_index=call_index,
                    request_ref=request_ref,
                    response_ref=None,
                    latency_ms=self._latency(call_started),
                )
            if lifecycle is not None:
                lifecycle.before_submission(call_index)
            response_evidence, transport_error, latency_ms = self._send(
                request_bytes,
                deadline_at=deadline_at,
            )
            if transport_error is not None:
                outcome_unknown = isinstance(
                    transport_error,
                    VisionSubmissionOutcomeUnknownError,
                )
                credential_unavailable = isinstance(
                    transport_error,
                    VisionCredentialUnavailableTransportError,
                )
                call = self._failed_call(
                    call_index=call_index,
                    status=(
                        VisionProviderStatus.UNKNOWN
                        if outcome_unknown
                        else VisionProviderStatus.UNAVAILABLE
                    ),
                    code=(
                        "PROVIDER_SUBMISSION_OUTCOME_UNKNOWN"
                        if outcome_unknown
                        else (
                            "PROVIDER_CREDENTIAL_UNAVAILABLE"
                            if credential_unavailable
                            else "PROVIDER_UNAVAILABLE"
                        )
                    ),
                    category=(
                        "unknown_outcome"
                        if outcome_unknown
                        else ("credential" if credential_unavailable else "transport")
                    ),
                    message=(
                        "Vision provider submission outcome is unknown"
                        if outcome_unknown
                        else (
                            "Vision provider credential is temporarily unavailable"
                            if credential_unavailable
                            else "Vision provider is temporarily unavailable"
                        )
                    ),
                    retryable=not outcome_unknown,
                    request_ref=request_ref,
                    response_ref=None,
                    latency_ms=latency_ms,
                )
                calls.append(call)
                return self._outcome(calls, output=None, error=call.error)
            assert response_evidence is not None
            response = response_evidence.response
            response_bytes = response.content
            try:
                response_ref = _write_artifact(
                    sink=self._artifact_sink,
                    request=request,
                    lifecycle=lifecycle,
                    call_index=call_index,
                    kind=ProviderArtifactKind.RESPONSE,
                    payload=response_bytes,
                )
            except ProviderArtifactPersistenceError:
                call = self._failed_call(
                    call_index=call_index,
                    status=VisionProviderStatus.UNKNOWN,
                    code="PROVIDER_RESPONSE_ARTIFACT_OUTCOME_UNKNOWN",
                    category="unknown_outcome",
                    message=(
                        "Vision provider responded but its response artifact could not be persisted"
                    ),
                    retryable=False,
                    request_ref=request_ref,
                    response_ref=None,
                    latency_ms=latency_ms,
                    request_id=self._request_id(response),
                )
                calls.append(call)
                return self._outcome(calls, output=None, error=call.error)
            if response_evidence.completion_uncertain:
                call = self._failed_call(
                    call_index=call_index,
                    status=VisionProviderStatus.UNKNOWN,
                    code="PROVIDER_RESPONSE_COMPLETION_UNKNOWN",
                    category="unknown_outcome",
                    message="Vision provider response read or cleanup did not complete",
                    retryable=False,
                    request_ref=request_ref,
                    response_ref=response_ref,
                    latency_ms=latency_ms,
                    request_id=self._request_id(response),
                )
                calls.append(call)
                return self._outcome(calls, output=None, error=call.error)
            http_failure = self._http_failure(
                response,
                call_index=call_index,
                request_ref=request_ref,
                response_ref=response_ref,
                latency_ms=latency_ms,
            )
            if http_failure is not None:
                calls.append(http_failure)
                return self._outcome(calls, output=None, error=http_failure.error)
            if response_evidence.body_too_large:
                if time.monotonic() >= deadline_at:
                    return self._deadline_outcome(
                        calls,
                        call_index=call_index,
                        request_ref=request_ref,
                        response_ref=response_ref,
                        latency_ms=self._latency(call_started),
                        request_id=self._request_id(response),
                    )
                call = self._failed_call(
                    call_index=call_index,
                    status=VisionProviderStatus.MALFORMED,
                    code="PROVIDER_RESPONSE_TOO_LARGE",
                    category="provider_schema",
                    message="Vision provider response exceeded the configured bound",
                    retryable=False,
                    request_ref=request_ref,
                    response_ref=response_ref,
                    latency_ms=latency_ms,
                    request_id=self._request_id(response),
                )
                calls.append(call)
                return self._outcome(calls, output=None, error=call.error)
            if time.monotonic() >= deadline_at:
                return self._deadline_outcome(
                    calls,
                    call_index=call_index,
                    request_ref=request_ref,
                    response_ref=response_ref,
                    latency_ms=self._latency(call_started),
                    request_id=self._request_id(response),
                )

            call, output, malformed_content = self._normalize_success(
                response,
                request=request,
                call_index=call_index,
                request_ref=request_ref,
                response_ref=response_ref,
                latency_ms=latency_ms,
            )
            if time.monotonic() >= deadline_at:
                return self._deadline_outcome(
                    calls,
                    call_index=call_index,
                    request_ref=request_ref,
                    response_ref=response_ref,
                    latency_ms=self._latency(call_started),
                    request_id=self._request_id(response),
                )
            calls.append(call)
            if output is not None:
                return self._outcome(calls, output=output, error=None)
            if call_index >= self._maximum_repair_attempts:
                return self._outcome(calls, output=None, error=call.error)
            if lifecycle is not None:
                lifecycle.persist_completed_call(call)
        raise AssertionError("bounded Vision repair loop did not return")

    def assert_ready(self) -> str:
        with self._lifecycle_condition:
            if self._closing:
                raise RuntimeError("Alibaba Vision analyzer is closing or closed")
        return self._transport.assert_ready()

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
            except Exception as exc:
                failures.append(exc)
            with self._lifecycle_condition:
                while self._active_lifecycles:
                    remaining = shutdown_deadline - time.monotonic()
                    if remaining <= 0:
                        failures.append(
                            TimeoutError(
                                "Alibaba Vision analyzer shutdown timed out with active calls"
                            )
                        )
                        break
                    self._lifecycle_condition.wait(remaining)
                self._closed = True
            if failures:
                raise ExceptionGroup("Alibaba Vision analyzer shutdown failed", failures)

    def __enter__(self) -> AlibabaVisionAnalyzer:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _validate_request(self, request: VisionAnalysisRequest) -> None:
        if request.prompt_version != self._prompt_version:
            raise ValueError("Vision request prompt version does not match the adapter")
        _assert_product_facts_budget(
            request.product_facts,
            maximum_bytes=self._product_facts_maximum_bytes,
            maximum_depth=self._product_facts_maximum_depth,
            maximum_nodes=self._product_facts_maximum_nodes,
            maximum_string_bytes=self._product_facts_maximum_string_bytes,
        )
        minimum_expiry = self._clock() + timedelta(seconds=self._deadline)
        for image in request.images:
            if (
                self._normalize_origin(image.url.get_secret_value())
                not in self._allowed_image_origins
            ):
                raise ValueError("Vision image URL is outside the controlled origin allowlist")
            if image.expires_at < minimum_expiry:
                raise ValueError("Vision image URL does not cover the end-to-end provider deadline")
            if image.required_headers:
                raise ValueError("Alibaba Vision image URLs cannot require forwarding headers")

    def _request_body(
        self,
        request: VisionAnalysisRequest,
        *,
        malformed_content: str | None,
    ) -> dict[str, object]:
        schema_instruction = {
            "category": request.category.value,
            "category_schema_version": request.category_schema_version,
            "common_schema_version": request.common_schema_version,
            "required_field_paths": list(product_brief_field_paths(request.category)),
            "value_contract_by_path": {
                path: _FIELD_VALUE_PROMPT_CONTRACTS[product_brief_field_value_kind(path)]
                for path in product_brief_field_paths(request.category)
            },
            "field_contract": {
                "confidence": "number from 0 to 1",
                "conflict": ["NONE", "CONFLICTING", "RESOLVED"],
                "evidence": [
                    {
                        "excerpt_sha256": "lowercase sha256 or null",
                        "kind": [kind.value for kind in ProductBriefEvidenceKind],
                        "reference": "bounded internal evidence reference",
                        "region": "[left, top, right, bottom] normalized or null",
                        "source_asset_version_id": "one supplied Asset Version id",
                    }
                ],
                "path": "one required_field_paths entry",
                "review_required": "boolean",
                "sensitive": "boolean",
                "value": (
                    "exact object selected by value_contract_by_path; "
                    "additional properties are forbidden"
                ),
            },
        }
        content: list[dict[str, object]] = [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "instruction": (
                            "Return JSON only. Describe visible product facts, preserve "
                            "uncertainty, and never infer unsupported claims."
                        ),
                        "product_facts": request.product_facts,
                        "schema": schema_instruction,
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
        ]
        content.extend(
            {
                "type": "image_url",
                "image_url": {"url": image.url.get_secret_value()},
            }
            for image in request.images
        )
        if malformed_content is not None:
            content.insert(
                0,
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "instruction": (
                                "Repair the prior output to the exact schema. Return JSON only."
                            ),
                            "prior_output": malformed_content[:32_768],
                        },
                        ensure_ascii=True,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                },
            )
        return {
            "enable_thinking": False,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a product evidence extractor. JSON output is mandatory. "
                        f"Prompt version: {self._prompt_version}."
                    ),
                },
                {"role": "user", "content": content},
            ],
            "max_tokens": self._maximum_output_tokens,
            "model": self._configured_snapshot,
            "response_format": {"type": "json_object"},
            "seed": 0,
            "temperature": 0,
        }

    def _send(
        self,
        request_bytes: bytes,
        *,
        deadline_at: float,
    ) -> tuple[VisionHttpResponseEvidence | None, Exception | None, int]:
        started = time.monotonic()
        try:
            response = self._transport.send(
                request_bytes,
                deadline_at=deadline_at,
            )
            return (
                response,
                None,
                self._latency(started),
            )
        except (
            VisionSafeToRetryTransportError,
            VisionSubmissionOutcomeUnknownError,
        ) as exc:
            return None, exc, self._latency(started)

    def _deadline_outcome(
        self,
        calls: list[VisionProviderCall],
        *,
        call_index: int,
        request_ref: ProviderArtifactReference,
        response_ref: ProviderArtifactReference | None,
        latency_ms: int,
        request_id: str | None = None,
    ) -> VisionProviderOutcome:
        post_dispatch = response_ref is not None or request_id is not None
        call = self._failed_call(
            call_index=call_index,
            status=(
                VisionProviderStatus.UNKNOWN if post_dispatch else VisionProviderStatus.TIMEOUT
            ),
            code=(
                "PROVIDER_POST_SUBMISSION_DEADLINE_EXCEEDED"
                if post_dispatch
                else "PROVIDER_TIMEOUT"
            ),
            category="unknown_outcome" if post_dispatch else "timeout",
            message=(
                "Vision provider completion crossed the post-submission deadline"
                if post_dispatch
                else "Vision provider timed out before submission"
            ),
            retryable=not post_dispatch,
            request_ref=request_ref,
            response_ref=response_ref,
            latency_ms=latency_ms,
            request_id=request_id,
        )
        calls.append(call)
        return self._outcome(calls, output=None, error=call.error)

    def _normalize_success(
        self,
        response: httpx.Response,
        *,
        request: VisionAnalysisRequest,
        call_index: int,
        request_ref: ProviderArtifactReference,
        response_ref: ProviderArtifactReference,
        latency_ms: int,
    ) -> tuple[VisionProviderCall, VisionStructuredOutput | None, str | None]:
        request_id = self._request_id(response)
        resolved_model: str | None = None
        usage = VisionProviderUsage()
        malformed_content: str | None = None
        try:
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError
            resolved_model = self._bounded_string(payload.get("model"), 128)
            if resolved_model != self._configured_snapshot:
                raise ValueError
            response_id = self._opaque_request_id(payload.get("id"))
            request_id = request_id or response_id
            choices = payload.get("choices")
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError
            choice = choices[0]
            if not isinstance(choice, dict):
                raise ValueError
            message = choice.get("message")
            if not isinstance(message, dict):
                raise ValueError
            content = message.get("content")
            if not isinstance(content, str) or len(content.encode()) > self._maximum_response_bytes:
                raise ValueError
            malformed_content = content
            raw_usage = payload.get("usage", {})
            if not isinstance(raw_usage, dict):
                raise ValueError
            usage = VisionProviderUsage(
                input_tokens=self._nonnegative_int(raw_usage.get("prompt_tokens", 0)),
                output_tokens=self._nonnegative_int(raw_usage.get("completion_tokens", 0)),
                total_tokens=self._nonnegative_int(raw_usage.get("total_tokens", 0)),
            )
            structured_payload = json.loads(content)
            _assert_json_complexity(
                structured_payload,
                maximum_depth=16,
                maximum_nodes=4096,
                maximum_string_bytes=4096,
            )
            output = VisionStructuredOutput.model_validate(structured_payload)
            if (
                output.category != request.category
                or output.common_schema_version != request.common_schema_version
                or output.category_schema_version != request.category_schema_version
            ):
                raise ValueError
            permitted_sources = {image.asset_version_id for image in request.images}
            if any(
                evidence.source_asset_version_id not in permitted_sources
                for field in output.fields
                for evidence in field.evidence
            ):
                raise ValueError
        except (
            RecursionError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
            ValidationError,
        ):
            error = VisionProviderError(
                code="MALFORMED_PROVIDER_OUTPUT",
                category="provider_schema",
                message="Vision provider returned invalid structured output",
                retryable=False,
            )
            return (
                VisionProviderCall(
                    call_index=call_index,
                    status=VisionProviderStatus.MALFORMED,
                    provider="alibaba-model-studio",
                    endpoint_region=self._endpoint_region,
                    endpoint_host=self._endpoint_host,
                    requested_model=self._requested_model,
                    submitted_model_snapshot=self._configured_snapshot,
                    resolved_model=resolved_model,
                    prompt_version=self._prompt_version,
                    config_snapshot_sha256=self._config_hash,
                    request_id=request_id,
                    usage=usage,
                    latency_ms=latency_ms,
                    request_artifact=request_ref,
                    response_artifact=response_ref,
                    error=error,
                ),
                None,
                malformed_content or response.text[:32_768],
            )
        return (
            VisionProviderCall(
                call_index=call_index,
                status=VisionProviderStatus.SUCCEEDED,
                provider="alibaba-model-studio",
                endpoint_region=self._endpoint_region,
                endpoint_host=self._endpoint_host,
                requested_model=self._requested_model,
                submitted_model_snapshot=self._configured_snapshot,
                resolved_model=resolved_model,
                prompt_version=self._prompt_version,
                config_snapshot_sha256=self._config_hash,
                request_id=request_id,
                usage=usage,
                latency_ms=latency_ms,
                request_artifact=request_ref,
                response_artifact=response_ref,
                error=None,
            ),
            output,
            None,
        )

    def _http_failure(
        self,
        response: httpx.Response,
        *,
        call_index: int,
        request_ref: ProviderArtifactReference,
        response_ref: ProviderArtifactReference | None,
        latency_ms: int,
    ) -> VisionProviderCall | None:
        if response.status_code == 200:
            return None
        retry_after = self._retry_after(response)
        request_id = self._request_id(response)
        if response.status_code == 429:
            return self._failed_call(
                call_index=call_index,
                status=VisionProviderStatus.THROTTLED,
                code="PROVIDER_THROTTLED",
                category="throttling",
                message="Vision provider throttled the request",
                retryable=True,
                request_ref=request_ref,
                response_ref=response_ref,
                latency_ms=latency_ms,
                request_id=request_id,
                retry_after_seconds=retry_after,
            )
        if response.status_code >= 500:
            return self._failed_call(
                call_index=call_index,
                status=VisionProviderStatus.UNAVAILABLE,
                code="PROVIDER_UNAVAILABLE",
                category="provider",
                message="Vision provider is temporarily unavailable",
                retryable=True,
                request_ref=request_ref,
                response_ref=response_ref,
                latency_ms=latency_ms,
                request_id=request_id,
                retry_after_seconds=retry_after,
            )
        return self._failed_call(
            call_index=call_index,
            status=VisionProviderStatus.REJECTED,
            code=f"PROVIDER_HTTP_{response.status_code}",
            category="provider",
            message="Vision provider rejected the request",
            retryable=False,
            request_ref=request_ref,
            response_ref=response_ref,
            latency_ms=latency_ms,
            request_id=request_id,
        )

    def _failed_call(
        self,
        *,
        call_index: int,
        status: VisionProviderStatus,
        code: str,
        category: str,
        message: str,
        retryable: bool,
        request_ref: ProviderArtifactReference,
        response_ref: ProviderArtifactReference | None,
        latency_ms: int,
        request_id: str | None = None,
        retry_after_seconds: int | None = None,
    ) -> VisionProviderCall:
        return VisionProviderCall(
            call_index=call_index,
            status=status,
            provider="alibaba-model-studio",
            endpoint_region=self._endpoint_region,
            endpoint_host=self._endpoint_host,
            requested_model=self._requested_model,
            submitted_model_snapshot=self._configured_snapshot,
            resolved_model=None,
            prompt_version=self._prompt_version,
            config_snapshot_sha256=self._config_hash,
            request_id=request_id,
            usage=VisionProviderUsage(),
            latency_ms=latency_ms,
            request_artifact=request_ref,
            response_artifact=response_ref,
            error=VisionProviderError(
                code=code,
                category=category,
                message=message,
                retryable=retryable,
                retry_after_seconds=retry_after_seconds,
            ),
        )

    @staticmethod
    def _outcome(
        calls: list[VisionProviderCall],
        *,
        output: VisionStructuredOutput | None,
        error: VisionProviderError | None,
    ) -> VisionProviderOutcome:
        final = calls[-1]
        return VisionProviderOutcome(
            status=final.status,
            provider=final.provider,
            endpoint_region=final.endpoint_region,
            endpoint_host=final.endpoint_host,
            requested_model=final.requested_model,
            submitted_model_snapshot=final.submitted_model_snapshot,
            resolved_model=final.resolved_model,
            prompt_version=final.prompt_version,
            config_snapshot_sha256=final.config_snapshot_sha256,
            request_id=final.request_id,
            usage=final.usage,
            latency_ms=sum(call.latency_ms for call in calls),
            request_artifact=final.request_artifact,
            response_artifact=final.response_artifact,
            output=output,
            error=error,
            calls=tuple(calls),
        )

    @staticmethod
    def _normalize_origin(value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("Vision image origins must be credential-free HTTPS origins")
        port = parsed.port or 443
        suffix = f":{port}" if port != 443 else ""
        return f"https://{parsed.hostname.lower()}{suffix}"

    @staticmethod
    def _request_id(response: httpx.Response) -> str | None:
        for name in ("x-request-id", "x-dashscope-request-id"):
            value = response.headers.get(name)
            if value:
                return AlibabaVisionAnalyzer._opaque_request_id(value)
        return None

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
    def _bounded_string(value: object, maximum: int) -> str:
        if not isinstance(value, str):
            raise ValueError
        normalized = value.strip()
        if not 1 <= len(normalized) <= maximum:
            raise ValueError
        return normalized

    @staticmethod
    def _nonnegative_int(value: object) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError
        return value

    @staticmethod
    def _retry_after(response: httpx.Response) -> int | None:
        raw = response.headers.get("retry-after")
        if raw is None:
            return None
        try:
            value = int(raw)
        except ValueError:
            return None
        return min(max(value, 0), 86400)

    @staticmethod
    def _latency(started: float) -> int:
        return max(0, round((time.monotonic() - started) * 1000))
