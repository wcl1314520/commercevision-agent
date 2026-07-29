from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from commercevision_application import ProductBriefPolicy
from commercevision_contracts import Settings
from commercevision_contracts.product_briefs import (
    ProviderArtifactKind,
    ProviderArtifactReference,
    ProviderArtifactUnavailableError,
    ProviderArtifactWrite,
    VisionAnalysisRequest,
    VisionImageInput,
    VisionProviderCall,
    VisionProviderOutcome,
    VisionProviderStatus,
    VisionStructuredOutput,
)
from commercevision_contracts.vision_configuration import (
    VISION_CONFIGURATION_SNAPSHOT_SCHEMA_VERSION,
)
from commercevision_domain import (
    ProductBriefCategory,
    ProductBriefFieldConflict,
    ProductBriefFieldValueKind,
    product_brief_field_paths,
    product_brief_field_value_kind,
)
from commercevision_providers import (
    AlibabaVisionAnalyzer,
    DeterministicVisionAnalyzer,
    DeterministicVisionScenario,
    MountedFileVisionApiKeyProvider,
)
from pydantic import ValidationError

NOW = datetime(2026, 7, 28, 8, 0, tzinfo=UTC)
ASSET_VERSION_ID = "019b0000-0000-7000-8000-000000000003"


def _field_value(path: str) -> dict[str, object]:
    kind = product_brief_field_value_kind(path)
    if kind == ProductBriefFieldValueKind.IDENTITY:
        return {
            "kind": "IDENTITY",
            "display_name": path,
            "model_number": None,
            "variant": None,
        }
    if kind == ProductBriefFieldValueKind.CATEGORY:
        return {"kind": "CATEGORY", "code": "beauty.skincare", "label": "Skin care"}
    if kind == ProductBriefFieldValueKind.TEXT:
        return {"kind": "TEXT", "text": path}
    if kind == ProductBriefFieldValueKind.TEXT_LIST:
        return {"kind": "TEXT_LIST", "items": [path]}
    if kind == ProductBriefFieldValueKind.STATEMENT_LIST:
        return {"kind": "STATEMENT_LIST", "statements": []}
    if kind == ProductBriefFieldValueKind.FLAG_LIST:
        return {"kind": "FLAG_LIST", "flags": []}
    if kind == ProductBriefFieldValueKind.DIMENSION_LIST:
        return {"kind": "DIMENSION_LIST", "dimensions": []}
    raise AssertionError(f"unexpected ProductBrief field kind: {kind}")


class MemoryArtifactSink:
    def __init__(self) -> None:
        self.writes: list[ProviderArtifactWrite] = []

    def write(self, artifact: ProviderArtifactWrite) -> ProviderArtifactReference:
        self.writes.append(artifact)
        return ProviderArtifactReference(
            storage_backend="MINIO",
            location="PROVIDER_RESULT",
            bucket="provider-results",
            key=f"provider/{artifact.operation_id}/{artifact.call_index}/{artifact.kind.value}",
            provider_version_id=f"version-{len(self.writes)}",
            etag=f'"etag-{len(self.writes)}"',
            sha256=artifact.sha256,
            byte_size=len(artifact.payload),
            retention_class=artifact.retention_class,
            retention_deadline=artifact.retention_deadline,
        )


class RecordingCallLifecycle:
    def __init__(self, sink: MemoryArtifactSink) -> None:
        self._sink = sink
        self.events: list[tuple[str, int]] = []

    def store_artifact(
        self,
        artifact: ProviderArtifactWrite,
    ) -> ProviderArtifactReference:
        self.events.append((f"artifact:{artifact.kind.value}", artifact.call_index))
        return self._sink.write(artifact)

    def before_submission(self, call_index: int) -> None:
        self.events.append(("intent", call_index))

    def persist_completed_call(self, call) -> None:
        self.events.append(("completed", call.call_index))


class BlockingRequestArtifactSink(MemoryArtifactSink):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def write(self, artifact: ProviderArtifactWrite) -> ProviderArtifactReference:
        if artifact.kind == ProviderArtifactKind.REQUEST and not self.entered.is_set():
            self.entered.set()
            self.release.wait(2)
        return super().write(artifact)


class DelayedResponseArtifactSink(MemoryArtifactSink):
    def __init__(self, delay_seconds: float) -> None:
        super().__init__()
        self._delay_seconds = delay_seconds

    def write(self, artifact: ProviderArtifactWrite) -> ProviderArtifactReference:
        if artifact.kind == ProviderArtifactKind.RESPONSE:
            time.sleep(self._delay_seconds)
        return super().write(artifact)


class DelayedRepairRequestArtifactSink(MemoryArtifactSink):
    def __init__(self, delay_seconds: float) -> None:
        super().__init__()
        self._delay_seconds = delay_seconds

    def write(self, artifact: ProviderArtifactWrite) -> ProviderArtifactReference:
        if artifact.kind == ProviderArtifactKind.REQUEST and artifact.call_index == 1:
            time.sleep(self._delay_seconds)
        return super().write(artifact)


class FailingResponseArtifactSink(MemoryArtifactSink):
    def write(self, artifact: ProviderArtifactWrite) -> ProviderArtifactReference:
        if artifact.kind == ProviderArtifactKind.RESPONSE:
            raise ProviderArtifactUnavailableError("provider response store unavailable")
        return super().write(artifact)


class BlockingResponseArtifactSink(MemoryArtifactSink):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def write(self, artifact: ProviderArtifactWrite) -> ProviderArtifactReference:
        if artifact.kind == ProviderArtifactKind.RESPONSE and not self.entered.is_set():
            self.entered.set()
            self.release.wait(2)
        return super().write(artifact)


class TrackingByteStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.chunks_read = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self._chunks:
            self.chunks_read += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


class FailingCloseByteStream(TrackingByteStream):
    async def aclose(self) -> None:
        self.closed = True
        raise httpx.ReadError("response close interrupted")


class FailingReadByteStream(TrackingByteStream):
    async def __aiter__(self):
        async for chunk in super().__aiter__():
            yield chunk
        raise httpx.ReadError("response body interrupted")


class HangingByteStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.closed = threading.Event()

    async def __aiter__(self):
        self.entered.set()
        yield b"{"
        while not self.release.is_set():
            await asyncio.sleep(0.005)

    async def aclose(self) -> None:
        self.closed.set()


class BlockingCloseClient(httpx.AsyncClient):
    def __init__(self, handler) -> None:
        super().__init__(transport=httpx.MockTransport(handler))
        self.close_entered = threading.Event()

    async def aclose(self) -> None:
        self.close_entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            await super().aclose()


def _request() -> VisionAnalysisRequest:
    return VisionAnalysisRequest(
        operation_id="019b0000-0000-7000-8000-000000000010",
        operation_attempt=1,
        product_brief_id="019b0000-0000-7000-8000-000000000011",
        category=ProductBriefCategory.BEAUTY,
        product_facts={
            "title": "Hydrating serum",
            "brand": "Example",
            "category_code": "beauty.skincare",
        },
        images=(
            VisionImageInput(
                asset_version_id=ASSET_VERSION_ID,
                content_sha256="a" * 64,
                url="https://assets.example.test/task/image?signature=secret",
                required_headers={},
                expires_at=NOW + timedelta(minutes=2),
            ),
        ),
        common_schema_version="product-brief-common-v1",
        category_schema_version="product-brief-beauty-v1",
        prompt_version="product-brief-prompt-v1",
        policy_version="product-brief-review-v1",
        retention_class="TASK",
        retention_deadline=NOW + timedelta(hours=71),
    )


def _provider_output(
    *,
    low_path: str | None = None,
    conflict_path: str | None = None,
    sensitive_path: str | None = None,
) -> dict[str, Any]:
    fields = []
    for path in product_brief_field_paths(ProductBriefCategory.BEAUTY):
        fields.append(
            {
                "path": path,
                "value": _field_value(path),
                "confidence": 0.45 if path == low_path else 0.96,
                "conflict": (
                    ProductBriefFieldConflict.CONFLICTING.value
                    if path == conflict_path
                    else ProductBriefFieldConflict.NONE.value
                ),
                "review_required": False,
                "sensitive": path == sensitive_path,
                "evidence": [
                    {
                        "source_asset_version_id": ASSET_VERSION_ID,
                        "kind": "IMAGE_REGION",
                        "reference": f"asset-region://{'b' * 64}",
                        "region": [0.1, 0.2, 0.7, 0.8],
                        "excerpt_sha256": "b" * 64,
                    }
                ],
            }
        )
    return {
        "common_schema_version": "product-brief-common-v1",
        "category_schema_version": "product-brief-beauty-v1",
        "category": "BEAUTY",
        "fields": fields,
    }


def _response(
    output: dict[str, Any] | str,
    *,
    status: int = 200,
    request_id: str = "req-vision-1",
) -> httpx.Response:
    content = output if isinstance(output, str) else json.dumps(output, separators=(",", ":"))
    return httpx.Response(
        status,
        headers={"x-request-id": request_id},
        json={
            "id": "chatcmpl-vision-1",
            "model": "qwen3-vl-plus-2026-01-25",
            "choices": [{"message": {"content": content}}],
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 80,
                "total_tokens": 200,
            },
        }
        if status == 200
        else {"code": "Throttling", "message": "too many requests"},
    )


def _adapter(
    handler,
    sink: MemoryArtifactSink,
    *,
    repair_attempts: int = 1,
    maximum_response_bytes: int = 512_000,
    maximum_output_tokens: int = 4096,
    product_facts_maximum_bytes: int = 64 * 1024,
    product_facts_maximum_depth: int = 8,
    product_facts_maximum_nodes: int = 1024,
    product_facts_maximum_string_bytes: int = 4096,
    maximum_concurrency: int = 2,
    connect_timeout_seconds: float = 1.0,
    read_timeout_seconds: float = 8.0,
    end_to_end_timeout_seconds: float = 12.0,
    endpoint: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
    credential_provider=None,
    client: httpx.AsyncClient | None = None,
) -> AlibabaVisionAnalyzer:
    client = client or httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        return AlibabaVisionAnalyzer(
            api_key="secret-api-key" if credential_provider is None else None,
            credential_provider=credential_provider,
            endpoint=endpoint,
            endpoint_region="cn-beijing",
            requested_model="qwen3-vl-plus",
            configured_snapshot="qwen3-vl-plus-2026-01-25",
            prompt_version="product-brief-prompt-v1",
            adapter_version="alibaba-vision-v1",
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=read_timeout_seconds,
            end_to_end_timeout_seconds=end_to_end_timeout_seconds,
            maximum_concurrency=maximum_concurrency,
            maximum_response_bytes=maximum_response_bytes,
            maximum_output_tokens=maximum_output_tokens,
            product_facts_maximum_bytes=product_facts_maximum_bytes,
            product_facts_maximum_depth=product_facts_maximum_depth,
            product_facts_maximum_nodes=product_facts_maximum_nodes,
            product_facts_maximum_string_bytes=product_facts_maximum_string_bytes,
            maximum_repair_attempts=repair_attempts,
            allowed_image_origins=frozenset({"https://assets.example.test"}),
            artifact_sink=sink,
            client=client,
            clock=lambda: NOW,
        )
    except Exception:
        asyncio.run(client.aclose())
        raise


@pytest.mark.parametrize(
    ("scenario", "path", "attribute"),
    [
        (DeterministicVisionScenario.LOW_CONFIDENCE, "common.brand", "confidence"),
        (DeterministicVisionScenario.CONFLICT, "common.colors", "conflict"),
        (
            DeterministicVisionScenario.SENSITIVE,
            "beauty.medical_like_claim_flags",
            "sensitive",
        ),
    ],
)
def test_deterministic_adapter_exposes_reproducible_review_scenarios(
    scenario: DeterministicVisionScenario,
    path: str,
    attribute: str,
) -> None:
    sink = MemoryArtifactSink()
    adapter = DeterministicVisionAnalyzer(
        scenario=scenario,
        artifact_sink=sink,
        clock=lambda: NOW,
    )

    result = adapter.analyze(_request())

    assert result.status == VisionProviderStatus.SUCCEEDED
    assert isinstance(result.output, VisionStructuredOutput)
    selected = next(field for field in result.output.fields if field.path == path)
    if attribute == "confidence":
        assert selected.confidence < 0.8
    elif attribute == "conflict":
        assert selected.conflict == ProductBriefFieldConflict.CONFLICTING
    else:
        assert selected.sensitive is True
    assert [write.kind for write in sink.writes] == [
        ProviderArtifactKind.REQUEST,
        ProviderArtifactKind.RESPONSE,
    ]


@pytest.mark.parametrize(
    ("scenario", "expected_status", "expected_retryable"),
    [
        ("REJECTED", VisionProviderStatus.REJECTED, False),
        ("THROTTLED", VisionProviderStatus.THROTTLED, True),
        ("UNKNOWN", VisionProviderStatus.UNKNOWN, False),
    ],
)
def test_deterministic_adapter_exposes_normalized_failure_scenarios(
    scenario: str,
    expected_status: VisionProviderStatus,
    expected_retryable: bool,
) -> None:
    sink = MemoryArtifactSink()
    adapter = DeterministicVisionAnalyzer(
        scenario=scenario,
        artifact_sink=sink,
        clock=lambda: NOW,
    )

    result = adapter.analyze(_request())

    assert result.status == expected_status
    assert result.error is not None
    assert result.error.retryable is expected_retryable
    assert result.output is None
    assert result.request_artifact is not None


def test_unknown_provider_outcome_cannot_cross_the_contract_as_retryable() -> None:
    successful = DeterministicVisionAnalyzer(
        artifact_sink=MemoryArtifactSink(),
        clock=lambda: NOW,
    ).analyze(_request())
    payload = successful.model_dump(mode="json")
    unknown_error = {
        "code": "PROVIDER_RESULT_UNKNOWN",
        "category": "unknown_outcome",
        "message": "Provider result is not provable",
        "retryable": True,
        "retry_after_seconds": None,
    }
    payload.update(
        {
            "status": VisionProviderStatus.UNKNOWN.value,
            "resolved_model": None,
            "output": None,
            "error": unknown_error,
        }
    )
    payload["calls"][-1].update(
        {
            "status": VisionProviderStatus.UNKNOWN.value,
            "resolved_model": None,
            "error": unknown_error,
        }
    )

    with pytest.raises(ValidationError, match="UNKNOWN.*non-retryable"):
        VisionProviderOutcome.model_validate(payload)


def test_successful_provider_call_requires_a_response_artifact() -> None:
    successful = DeterministicVisionAnalyzer(
        artifact_sink=MemoryArtifactSink(),
        clock=lambda: NOW,
    ).analyze(_request())
    payload = successful.calls[-1].model_dump(mode="json")
    payload["response_artifact"] = None

    with pytest.raises(ValidationError, match="successful.*response artifact"):
        VisionProviderCall.model_validate(payload)


def test_successful_provider_outcome_requires_a_response_artifact() -> None:
    successful = DeterministicVisionAnalyzer(
        artifact_sink=MemoryArtifactSink(),
        clock=lambda: NOW,
    ).analyze(_request())
    payload = successful.model_dump(mode="json")
    payload["response_artifact"] = None

    with pytest.raises(ValidationError, match="successful.*response artifact"):
        VisionProviderOutcome.model_validate(payload)


def test_alibaba_adapter_normalizes_success_and_redacts_sensitive_payloads() -> None:
    sink = MemoryArtifactSink()
    adapter = _adapter(lambda request: _response(_provider_output()), sink)

    result = adapter.analyze(_request())

    assert result.status == VisionProviderStatus.SUCCEEDED
    assert result.provider == "alibaba-model-studio"
    assert result.requested_model == "qwen3-vl-plus"
    assert result.submitted_model_snapshot == "qwen3-vl-plus-2026-01-25"
    assert result.resolved_model == "qwen3-vl-plus-2026-01-25"
    assert result.calls[0].submitted_model_snapshot == "qwen3-vl-plus-2026-01-25"
    assert result.request_id == "req-vision-1"
    assert result.usage.total_tokens == 200
    assert result.request_artifact is not None
    assert result.response_artifact is not None
    assert "signature=secret" not in repr(_request())
    assert "secret-api-key" not in repr(adapter)
    assert all("signature=secret" not in repr(write) for write in sink.writes)


def test_alibaba_adapter_rejects_usage_that_cannot_be_persisted() -> None:
    sink = MemoryArtifactSink()
    response = _response(_provider_output())
    payload = response.json()
    payload["usage"] = {
        "prompt_tokens": 2_147_483_648,
        "completion_tokens": 1,
        "total_tokens": 2_147_483_649,
    }

    result = _adapter(
        lambda _: httpx.Response(200, json=payload),
        sink,
        repair_attempts=0,
    ).analyze(_request())

    assert result.status == VisionProviderStatus.MALFORMED
    assert result.error is not None
    assert result.error.code == "MALFORMED_PROVIDER_OUTPUT"


@pytest.mark.parametrize("non_finite", [float("nan"), float("inf"), float("-inf")])
def test_alibaba_adapter_classifies_non_finite_output_as_malformed(
    non_finite: float,
) -> None:
    sink = MemoryArtifactSink()
    payload = _provider_output()
    payload["fields"][0]["value"] = {"measurement": non_finite}

    result = _adapter(
        lambda _: _response(payload),
        sink,
        repair_attempts=0,
    ).analyze(_request())

    assert result.status == VisionProviderStatus.MALFORMED
    assert result.error is not None
    assert result.error.code == "MALFORMED_PROVIDER_OUTPUT"
    assert result.error.retryable is False


def test_alibaba_adapter_rejects_a_resolved_model_outside_the_pinned_snapshot() -> None:
    sink = MemoryArtifactSink()
    response = _response(_provider_output())
    payload = response.json()
    payload["model"] = "qwen3-vl-plus-unpinned"

    result = _adapter(
        lambda _: httpx.Response(200, json=payload),
        sink,
        repair_attempts=0,
    ).analyze(_request())

    assert result.status == VisionProviderStatus.MALFORMED
    assert result.error is not None
    assert result.error.code == "MALFORMED_PROVIDER_OUTPUT"


def test_alibaba_adapter_freezes_the_complete_endpoint_in_its_identity() -> None:
    first = _adapter(
        lambda _: _response(_provider_output()),
        MemoryArtifactSink(),
        endpoint="https://vision-vpc.example.test/compatible-mode/v1",
    )
    second = _adapter(
        lambda _: _response(_provider_output()),
        MemoryArtifactSink(),
        endpoint="https://vision-vpc.example.test/compatible-mode/v2",
    )
    try:
        assert (
            first.configured_identity.configuration_snapshot_sha256
            != second.configured_identity.configuration_snapshot_sha256
        )
        assert first.configured_identity.endpoint_host == "vision-vpc.example.test"
        assert second.configured_identity.endpoint_host == "vision-vpc.example.test"
    finally:
        first.close()
        second.close()


def test_deterministic_adapter_identity_matches_the_api_frozen_policy() -> None:
    settings = Settings(
        deterministic_vision_scenario="sensitive",
        alibaba_vision_maximum_output_tokens=2048,
        vision_product_facts_maximum_bytes=32 * 1024,
        vision_product_facts_maximum_depth=6,
        vision_product_facts_maximum_nodes=512,
        vision_product_facts_maximum_string_bytes=2048,
    )
    adapter = DeterministicVisionAnalyzer(
        scenario=DeterministicVisionScenario.SENSITIVE,
        artifact_sink=MemoryArtifactSink(),
        prompt_version=settings.vision_prompt_version,
        maximum_output_tokens=settings.alibaba_vision_maximum_output_tokens,
        product_facts_maximum_bytes=settings.vision_product_facts_maximum_bytes,
        product_facts_maximum_depth=settings.vision_product_facts_maximum_depth,
        product_facts_maximum_nodes=settings.vision_product_facts_maximum_nodes,
        product_facts_maximum_string_bytes=(settings.vision_product_facts_maximum_string_bytes),
    )

    policy = ProductBriefPolicy.from_settings(settings)

    assert adapter.configured_identity.configuration_snapshot_sha256 == (
        policy.configuration_snapshot_sha256
    )


def test_provider_configuration_snapshot_schema_is_explicitly_versioned() -> None:
    assert VISION_CONFIGURATION_SNAPSHOT_SCHEMA_VERSION == 2


def test_alibaba_adapter_identity_matches_the_api_frozen_policy() -> None:
    settings = Settings(
        vision_adapter="alibaba",
        alibaba_vision_endpoint="https://dashscope.aliyuncs.com/compatible-mode/v1",
        alibaba_vision_endpoint_region="cn-beijing",
        alibaba_vision_model="qwen3-vl-plus",
        alibaba_vision_model_snapshot="qwen3-vl-plus-2026-01-25",
        alibaba_vision_adapter_version="alibaba-vision-v1",
        alibaba_vision_connect_timeout_seconds=1.0,
        alibaba_vision_read_timeout_seconds=8.0,
        alibaba_vision_end_to_end_timeout_seconds=12.0,
        alibaba_vision_maximum_concurrency=2,
        alibaba_vision_maximum_response_bytes=512_000,
        alibaba_vision_maximum_output_tokens=2048,
        alibaba_vision_maximum_repair_attempts=0,
        vision_product_facts_maximum_bytes=32 * 1024,
        vision_product_facts_maximum_depth=6,
        vision_product_facts_maximum_nodes=512,
        vision_product_facts_maximum_string_bytes=2048,
        vision_data_transfer_enabled=True,
        vision_data_transfer_allowed_workspace_ids=["catalog-a"],
        vision_data_transfer_allowed_retention_classes=["TASK"],
        vision_data_transfer_allowed_providers=["alibaba-model-studio"],
        vision_data_transfer_allowed_endpoint_regions=["cn-beijing"],
        vision_data_transfer_allowed_endpoint_hosts=["dashscope.aliyuncs.com"],
    )
    adapter = _adapter(
        lambda _: _response(_provider_output()),
        MemoryArtifactSink(),
        repair_attempts=settings.alibaba_vision_maximum_repair_attempts,
        connect_timeout_seconds=settings.alibaba_vision_connect_timeout_seconds,
        read_timeout_seconds=settings.alibaba_vision_read_timeout_seconds,
        end_to_end_timeout_seconds=settings.alibaba_vision_end_to_end_timeout_seconds,
        maximum_concurrency=settings.alibaba_vision_maximum_concurrency,
        maximum_response_bytes=settings.alibaba_vision_maximum_response_bytes,
        maximum_output_tokens=settings.alibaba_vision_maximum_output_tokens,
        product_facts_maximum_bytes=settings.vision_product_facts_maximum_bytes,
        product_facts_maximum_depth=settings.vision_product_facts_maximum_depth,
        product_facts_maximum_nodes=settings.vision_product_facts_maximum_nodes,
        product_facts_maximum_string_bytes=(settings.vision_product_facts_maximum_string_bytes),
    )
    try:
        policy = ProductBriefPolicy.from_settings(settings)

        assert adapter.configured_identity.configuration_snapshot_sha256 == (
            policy.configuration_snapshot_sha256
        )
    finally:
        adapter.close()


@pytest.mark.parametrize(
    ("configuration_field", "first_value", "second_value"),
    [
        ("connect_timeout_seconds", 1.0, 1.25),
        ("read_timeout_seconds", 8.0, 8.25),
        ("end_to_end_timeout_seconds", 12.0, 13.0),
        ("maximum_concurrency", 2, 3),
    ],
)
def test_alibaba_adapter_identity_independently_tracks_execution_controls(
    configuration_field: str,
    first_value: float | int,
    second_value: float | int,
) -> None:
    first = _adapter(
        lambda _: _response(_provider_output()),
        MemoryArtifactSink(),
        **{configuration_field: first_value},
    )
    second = _adapter(
        lambda _: _response(_provider_output()),
        MemoryArtifactSink(),
        **{configuration_field: second_value},
    )
    try:
        assert (
            first.configured_identity.configuration_snapshot_sha256
            != second.configured_identity.configuration_snapshot_sha256
        )
    finally:
        first.close()
        second.close()


def test_alibaba_adapter_submits_and_hashes_the_configured_output_token_budget() -> None:
    sink = MemoryArtifactSink()
    adapter = _adapter(
        lambda _: _response(_provider_output()),
        sink,
        repair_attempts=0,
        maximum_output_tokens=321,
    )
    different_budget = _adapter(
        lambda _: _response(_provider_output()),
        MemoryArtifactSink(),
        repair_attempts=0,
        maximum_output_tokens=322,
    )
    try:
        result = adapter.analyze(_request())
    finally:
        adapter.close()
        different_budget.close()

    request_document = json.loads(sink.writes[0].payload)
    assert request_document["max_tokens"] == 321
    assert result.config_snapshot_sha256 == (
        adapter.configured_identity.configuration_snapshot_sha256
    )
    assert result.config_snapshot_sha256 != (
        different_budget.configured_identity.configuration_snapshot_sha256
    )


@pytest.mark.parametrize(
    ("product_facts", "budget_overrides", "expected_message"),
    [
        (
            {"value": "0123456789"},
            {"product_facts_maximum_bytes": 16},
            "canonical byte budget",
        ),
        (
            {"level_1": {"level_2": {"level_3": "leaf"}}},
            {"product_facts_maximum_depth": 2},
            "depth budget",
        ),
        (
            {"items": [1, 2, 3]},
            {"product_facts_maximum_nodes": 4},
            "node budget",
        ),
        (
            {"value": "four"},
            {"product_facts_maximum_string_bytes": 3},
            "string budget",
        ),
    ],
)
def test_alibaba_adapter_rejects_product_facts_before_artifact_or_post(
    product_facts: dict[str, Any],
    budget_overrides: dict[str, int],
    expected_message: str,
) -> None:
    sink = MemoryArtifactSink()
    provider_calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal provider_calls
        provider_calls += 1
        return _response(_provider_output())

    adapter = _adapter(
        handler,
        sink,
        repair_attempts=0,
        **budget_overrides,
    )
    request = _request().model_copy(update={"product_facts": product_facts})
    try:
        with pytest.raises(ValueError, match=expected_message):
            adapter.analyze(request)
    finally:
        adapter.close()

    assert sink.writes == []
    assert provider_calls == 0


def test_alibaba_adapter_rejects_excessively_nested_field_values() -> None:
    sink = MemoryArtifactSink()
    output = _provider_output()
    nested: object = "leaf"
    for _ in range(20):
        nested = {"child": nested}
    output["fields"][0]["value"] = nested

    result = _adapter(
        lambda _: _response(output),
        sink,
        repair_attempts=0,
    ).analyze(_request())

    assert result.status == VisionProviderStatus.MALFORMED
    assert result.error is not None
    assert result.error.code == "MALFORMED_PROVIDER_OUTPUT"


def test_alibaba_adapter_repairs_malformed_structured_output_once() -> None:
    sink = MemoryArtifactSink()
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return (
            _response("{not-json", request_id="req-malformed")
            if calls == 1
            else _response(_provider_output(), request_id="req-repaired")
        )

    result = _adapter(handler, sink).analyze(_request())

    assert result.status == VisionProviderStatus.SUCCEEDED
    assert result.request_id == "req-repaired"
    assert len(result.calls) == 2
    assert result.calls[0].status == VisionProviderStatus.MALFORMED
    assert result.calls[1].status == VisionProviderStatus.SUCCEEDED
    assert [write.kind for write in sink.writes] == [
        ProviderArtifactKind.REQUEST,
        ProviderArtifactKind.RESPONSE,
        ProviderArtifactKind.REQUEST,
        ProviderArtifactKind.RESPONSE,
    ]


def test_alibaba_adapter_durably_fences_each_repair_submission() -> None:
    sink = MemoryArtifactSink()
    lifecycle = RecordingCallLifecycle(sink)
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _response("{not-json") if calls == 1 else _response(_provider_output())

    adapter = _adapter(handler, sink)
    try:
        result = adapter.analyze(_request(), lifecycle=lifecycle)
    finally:
        adapter.close()

    assert result.status == VisionProviderStatus.SUCCEEDED
    assert lifecycle.events == [
        ("artifact:REQUEST", 0),
        ("intent", 0),
        ("artifact:RESPONSE", 0),
        ("completed", 0),
        ("artifact:REQUEST", 1),
        ("intent", 1),
        ("artifact:RESPONSE", 1),
    ]


def test_alibaba_adapter_rejects_unknown_or_twice_malformed_output_terminally() -> None:
    sink = MemoryArtifactSink()
    malformed = _provider_output()
    malformed["fields"][0]["unexpected"] = "not allowed"

    result = _adapter(lambda _: _response(malformed), sink).analyze(_request())

    assert result.status == VisionProviderStatus.MALFORMED
    assert result.error is not None
    assert result.error.code == "MALFORMED_PROVIDER_OUTPUT"
    assert result.error.retryable is False
    assert len(result.calls) == 2
    assert result.output is None


def test_alibaba_adapter_rejects_unrestricted_evidence_urls() -> None:
    sink = MemoryArtifactSink()
    malformed = _provider_output()
    malformed["fields"][0]["evidence"][0]["reference"] = (
        "https://signed.invalid/source.png?secret=1"
    )

    result = _adapter(
        lambda _: _response(malformed),
        sink,
        repair_attempts=0,
    ).analyze(_request())

    assert result.status == VisionProviderStatus.MALFORMED
    assert result.error is not None
    assert result.error.code == "MALFORMED_PROVIDER_OUTPUT"


def test_alibaba_adapter_normalizes_throttling_without_leaking_provider_body() -> None:
    sink = MemoryArtifactSink()
    adapter = _adapter(
        lambda _: httpx.Response(
            429,
            headers={"retry-after": "7", "x-request-id": "req-throttled"},
            json={"message": "contains provider-only detail"},
        ),
        sink,
    )

    result = adapter.analyze(_request())

    assert result == VisionProviderOutcome.model_validate(result)
    assert result.status == VisionProviderStatus.THROTTLED
    assert result.error is not None
    assert result.error.code == "PROVIDER_THROTTLED"
    assert result.error.retryable is True
    assert result.error.retry_after_seconds == 7
    assert "provider-only detail" not in repr(result)


def test_alibaba_adapter_marks_response_read_timeout_as_unknown() -> None:
    sink = MemoryArtifactSink()

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("raw timeout detail", request=request)

    result = _adapter(timeout, sink).analyze(_request())

    assert result.status == VisionProviderStatus.UNKNOWN
    assert result.error is not None
    assert result.error.code == "PROVIDER_SUBMISSION_OUTCOME_UNKNOWN"
    assert result.error.retryable is False
    assert result.response_artifact is None
    assert "raw timeout detail" not in repr(result)


def test_alibaba_adapter_marks_response_close_interruption_as_unknown() -> None:
    sink = MemoryArtifactSink()
    payload = json.dumps(
        {
            "id": "chatcmpl-close-interruption",
            "model": "qwen3-vl-plus-2026-01-25",
            "choices": [{"message": {"content": json.dumps(_provider_output())}}],
            "usage": {},
        }
    ).encode()
    stream = FailingCloseByteStream([payload])

    result = _adapter(
        lambda _: httpx.Response(
            200,
            headers={"x-request-id": "req-close-interruption"},
            stream=stream,
        ),
        sink,
        repair_attempts=0,
    ).analyze(_request())

    assert stream.closed is True
    assert result.status == VisionProviderStatus.UNKNOWN
    assert result.error is not None
    assert result.error.code == "PROVIDER_RESPONSE_COMPLETION_UNKNOWN"
    assert result.error.retryable is False
    assert result.response_artifact is not None
    assert sink.writes[-1].payload == payload


@pytest.mark.parametrize("status_code", [400, 429, 503])
def test_alibaba_adapter_marks_partial_body_read_as_non_retryable_unknown(
    status_code: int,
) -> None:
    sink = MemoryArtifactSink()
    partial_body = b'{"partial":'
    stream = FailingReadByteStream([partial_body])

    result = _adapter(
        lambda _: httpx.Response(
            status_code,
            headers={"x-request-id": "req-partial-body", "retry-after": "11"},
            stream=stream,
        ),
        sink,
        repair_attempts=0,
    ).analyze(_request())

    assert stream.closed is True
    assert result.status == VisionProviderStatus.UNKNOWN
    assert result.error is not None
    assert result.error.code == "PROVIDER_RESPONSE_COMPLETION_UNKNOWN"
    assert result.error.retryable is False
    assert result.request_id == "req-partial-body"
    assert result.response_artifact is not None
    assert sink.writes[-1].payload == partial_body


def test_alibaba_adapter_marks_200_partial_body_read_as_non_retryable_unknown() -> None:
    sink = MemoryArtifactSink()
    partial_body = b'{"partial":'
    stream = FailingReadByteStream([partial_body])

    result = _adapter(
        lambda _: httpx.Response(
            200,
            headers={"x-request-id": "req-partial-success"},
            stream=stream,
        ),
        sink,
        repair_attempts=0,
    ).analyze(_request())

    assert stream.closed is True
    assert result.status == VisionProviderStatus.UNKNOWN
    assert result.error is not None
    assert result.error.retryable is False
    assert result.request_id == "req-partial-success"
    assert result.response_artifact is not None
    assert sink.writes[-1].payload == partial_body


@pytest.mark.parametrize("status_code", [400, 429, 503])
def test_alibaba_adapter_marks_cleanup_failure_as_non_retryable_unknown(
    status_code: int,
) -> None:
    sink = MemoryArtifactSink()
    body = b'{"provider":"error"}'
    stream = FailingCloseByteStream([body])

    result = _adapter(
        lambda _: httpx.Response(
            status_code,
            headers={"x-request-id": "req-cleanup-failure"},
            stream=stream,
        ),
        sink,
        repair_attempts=0,
    ).analyze(_request())

    assert stream.closed is True
    assert result.status == VisionProviderStatus.UNKNOWN
    assert result.error is not None
    assert result.error.code == "PROVIDER_RESPONSE_COMPLETION_UNKNOWN"
    assert result.error.retryable is False
    assert result.request_id == "req-cleanup-failure"
    assert result.response_artifact is not None
    assert sink.writes[-1].payload == body


def test_alibaba_adapter_marks_response_artifact_failure_after_success_as_unknown() -> None:
    sink = FailingResponseArtifactSink()
    adapter = _adapter(
        lambda _: _response(_provider_output(), request_id="req-artifact-window"),
        sink,
        repair_attempts=0,
    )

    result = adapter.analyze(_request())

    assert result.status == VisionProviderStatus.UNKNOWN
    assert result.request_id == "req-artifact-window"
    assert result.error is not None
    assert result.error.code == "PROVIDER_RESPONSE_ARTIFACT_OUTCOME_UNKNOWN"
    assert result.error.retryable is False
    assert result.request_artifact is not None
    assert result.response_artifact is None


@pytest.mark.parametrize("status_code", [400, 429, 503])
def test_alibaba_adapter_marks_artifact_write_failure_as_non_retryable_unknown(
    status_code: int,
) -> None:
    sink = FailingResponseArtifactSink()
    adapter = _adapter(
        lambda _: _response(
            {"error": "not persisted"},
            status=status_code,
            request_id="req-known-http-failure",
        ),
        sink,
        repair_attempts=0,
    )

    try:
        result = adapter.analyze(_request())
    finally:
        adapter.close()

    assert result.status == VisionProviderStatus.UNKNOWN
    assert result.error is not None
    assert result.error.code == "PROVIDER_RESPONSE_ARTIFACT_OUTCOME_UNKNOWN"
    assert result.error.retryable is False
    assert result.request_id == "req-known-http-failure"
    assert result.request_artifact is not None
    assert result.response_artifact is None


def test_alibaba_adapter_preserves_an_empty_response_artifact_exactly() -> None:
    sink = MemoryArtifactSink()
    result = _adapter(
        lambda _: httpx.Response(
            200,
            headers={"x-request-id": "req-empty-response"},
            content=b"",
        ),
        sink,
        repair_attempts=0,
    ).analyze(_request())

    assert result.status == VisionProviderStatus.MALFORMED
    assert result.error is not None
    assert result.error.code == "MALFORMED_PROVIDER_OUTPUT"
    assert sink.writes[-1].kind == ProviderArtifactKind.RESPONSE
    assert sink.writes[-1].payload == b""
    assert result.response_artifact is not None
    assert result.response_artifact.byte_size == 0


def test_alibaba_adapter_stops_reading_at_the_response_byte_bound() -> None:
    sink = MemoryArtifactSink()
    stream = TrackingByteStream([b"x" * 64, b"overflow", b"must-not-be-read"])

    def oversized(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"x-request-id": "req-oversized"},
            stream=stream,
        )

    result = _adapter(
        oversized,
        sink,
        repair_attempts=0,
        maximum_response_bytes=64,
    ).analyze(_request())

    assert result.status == VisionProviderStatus.MALFORMED
    assert result.error is not None
    assert result.error.code == "PROVIDER_RESPONSE_TOO_LARGE"
    assert stream.chunks_read == 2
    assert stream.closed is True
    assert sink.writes[-1].payload == (b"x" * 64) + b"o"


def test_alibaba_adapter_marks_oversized_response_artifact_failure_as_unknown() -> None:
    sink = FailingResponseArtifactSink()

    def oversized(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"x-request-id": "req-oversized-artifact-window"},
            stream=TrackingByteStream([b"x" * 64, b"overflow"]),
        )

    adapter = _adapter(
        oversized,
        sink,
        repair_attempts=0,
        maximum_response_bytes=64,
    )
    try:
        result = adapter.analyze(_request())
    finally:
        adapter.close()

    assert result.status == VisionProviderStatus.UNKNOWN
    assert result.error is not None
    assert result.error.code == "PROVIDER_RESPONSE_ARTIFACT_OUTCOME_UNKNOWN"
    assert result.error.retryable is False
    assert result.request_id == "req-oversized-artifact-window"
    assert result.response_artifact is None


@pytest.mark.parametrize(
    ("status_code", "expected_status", "expected_code"),
    [
        (429, VisionProviderStatus.THROTTLED, "PROVIDER_THROTTLED"),
        (503, VisionProviderStatus.UNAVAILABLE, "PROVIDER_UNAVAILABLE"),
    ],
)
def test_alibaba_adapter_preserves_confirmed_retryable_http_status_for_oversized_body(
    status_code: int,
    expected_status: VisionProviderStatus,
    expected_code: str,
) -> None:
    sink = MemoryArtifactSink()

    def oversized(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            headers={"x-request-id": "req-confirmed-retryable"},
            stream=TrackingByteStream([b"x" * 64, b"overflow"]),
        )

    result = _adapter(
        oversized,
        sink,
        repair_attempts=0,
        maximum_response_bytes=64,
    ).analyze(_request())

    assert result.status == expected_status
    assert result.error is not None
    assert result.error.code == expected_code
    assert result.error.retryable is True
    assert result.request_id == "req-confirmed-retryable"
    assert result.response_artifact is not None


def test_alibaba_adapter_end_to_end_deadline_includes_capacity_wait() -> None:
    sink = MemoryArtifactSink()
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()
    release_second = threading.Event()
    lock = threading.Lock()
    call_count = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal call_count
        with lock:
            call_count += 1
            current = call_count
        if current == 1:
            first_entered.set()
            while not release_first.is_set():
                await asyncio.sleep(0.005)
        else:
            second_entered.set()
            while not release_second.is_set():
                await asyncio.sleep(0.005)
        return _response(_provider_output(), request_id=f"request-{current}")

    adapter = _adapter(
        handler,
        sink,
        maximum_concurrency=1,
        connect_timeout_seconds=0.01,
        read_timeout_seconds=0.01,
        end_to_end_timeout_seconds=0.25,
    )
    second_request = _request().model_copy(
        update={
            "operation_id": "019b0000-0000-7000-8000-000000000020",
            "product_brief_id": "019b0000-0000-7000-8000-000000000021",
        }
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(adapter.analyze, _request())
            assert first_entered.wait(1)
            started = time.monotonic()
            second = pool.submit(adapter.analyze, second_request)
            time.sleep(0.05)
            release_first.set()
            assert second_entered.wait(1)
            result = second.result(timeout=1)
            elapsed = time.monotonic() - started
            release_second.set()
            assert first.result(timeout=1).status == VisionProviderStatus.SUCCEEDED
    finally:
        release_first.set()
        release_second.set()
        adapter.close()

    assert result.status == VisionProviderStatus.UNKNOWN
    assert result.error is not None
    assert result.error.retryable is False
    assert elapsed < 0.31


def test_alibaba_adapter_timeout_cancels_stream_and_recovers_capacity() -> None:
    sink = MemoryArtifactSink()
    hanging_stream = HangingByteStream()
    call_count = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(200, stream=hanging_stream)
        return _response(_provider_output(), request_id="request-after-timeout")

    adapter = _adapter(
        handler,
        sink,
        repair_attempts=0,
        maximum_concurrency=1,
        connect_timeout_seconds=0.01,
        read_timeout_seconds=0.01,
        end_to_end_timeout_seconds=0.08,
    )
    try:
        first = adapter.analyze(_request())
        assert hanging_stream.entered.wait(1)
        second = adapter.analyze(
            _request().model_copy(
                update={
                    "operation_id": "019b0000-0000-7000-8000-000000000030",
                    "product_brief_id": "019b0000-0000-7000-8000-000000000031",
                }
            )
        )
    finally:
        hanging_stream.release.set()
        adapter.close()

    assert first.status == VisionProviderStatus.UNKNOWN
    assert first.error is not None
    assert first.error.code == "PROVIDER_RESPONSE_COMPLETION_UNKNOWN"
    assert first.error.retryable is False
    assert hanging_stream.closed.wait(1)
    first_response_write = next(
        write
        for write in sink.writes
        if write.operation_id == _request().operation_id
        and write.kind == ProviderArtifactKind.RESPONSE
    )
    assert first_response_write.payload == b"{"
    assert second.status == VisionProviderStatus.SUCCEEDED


def test_alibaba_adapter_close_cancels_active_stream_before_client_shutdown() -> None:
    sink = MemoryArtifactSink()
    hanging_stream = HangingByteStream()
    adapter = _adapter(
        lambda _: httpx.Response(200, stream=hanging_stream),
        sink,
        repair_attempts=0,
        maximum_concurrency=1,
        connect_timeout_seconds=0.01,
        read_timeout_seconds=0.01,
        end_to_end_timeout_seconds=2,
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(adapter.analyze, _request())
        assert hanging_stream.entered.wait(1)
        adapter.close()
        result = pending.result(timeout=1)

    assert hanging_stream.closed.wait(1)
    assert result.status == VisionProviderStatus.UNKNOWN
    assert result.error is not None
    assert result.error.code == "PROVIDER_SUBMISSION_OUTCOME_UNKNOWN"
    assert result.error.retryable is False


def test_alibaba_adapter_close_waits_for_pending_request_artifact_persistence() -> None:
    sink = BlockingRequestArtifactSink()
    adapter = _adapter(
        lambda _: _response(_provider_output()),
        sink,
        repair_attempts=0,
        connect_timeout_seconds=0.01,
        read_timeout_seconds=0.01,
        end_to_end_timeout_seconds=2,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(adapter.analyze, _request())
        assert sink.entered.wait(1)
        closing = pool.submit(adapter.close)
        with pytest.raises(TimeoutError):
            closing.result(timeout=0.05)
        sink.release.set()
        result = pending.result(timeout=1)
        closing.result(timeout=1)

    assert result.status == VisionProviderStatus.UNAVAILABLE
    assert result.error is not None
    assert result.error.code == "PROVIDER_UNAVAILABLE"


def test_alibaba_adapter_close_waits_for_pending_response_artifact_persistence() -> None:
    sink = BlockingResponseArtifactSink()
    adapter = _adapter(
        lambda _: _response(_provider_output()),
        sink,
        repair_attempts=0,
        connect_timeout_seconds=0.01,
        read_timeout_seconds=0.01,
        end_to_end_timeout_seconds=2,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(adapter.analyze, _request())
        assert sink.entered.wait(1)
        closing = pool.submit(adapter.close)
        try:
            with pytest.raises(TimeoutError):
                closing.result(timeout=0.05)
        finally:
            sink.release.set()
        result = pending.result(timeout=1)
        closing.result(timeout=1)

    assert result.status == VisionProviderStatus.SUCCEEDED


def test_alibaba_adapter_close_aggregates_transport_failure_after_lifecycle_drains() -> None:
    sink = BlockingRequestArtifactSink()
    client = BlockingCloseClient(lambda _: _response(_provider_output()))
    adapter = _adapter(
        lambda _: _response(_provider_output()),
        sink,
        repair_attempts=0,
        connect_timeout_seconds=0.01,
        read_timeout_seconds=0.02,
        end_to_end_timeout_seconds=0.5,
        client=client,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(adapter.analyze, _request())
        assert sink.entered.wait(1)
        closing = pool.submit(adapter.close)
        assert client.close_entered.wait(1)
        time.sleep(0.1)
        assert closing.done() is False
        sink.release.set()
        pending.result(timeout=1)
        with pytest.raises(
            ExceptionGroup,
            match="Alibaba Vision analyzer shutdown failed",
        ) as captured:
            closing.result(timeout=1)

    assert adapter.shutdown_drained is True
    assert [type(error) for error in captured.value.exceptions] == [TimeoutError]
    assert [str(error) for error in captured.value.exceptions] == ["Vision HTTP shutdown timed out"]


def test_alibaba_adapter_close_aggregates_transport_and_drain_failures() -> None:
    sink = BlockingRequestArtifactSink()
    client = BlockingCloseClient(lambda _: _response(_provider_output()))
    adapter = _adapter(
        lambda _: _response(_provider_output()),
        sink,
        repair_attempts=0,
        connect_timeout_seconds=0.01,
        read_timeout_seconds=0.01,
        end_to_end_timeout_seconds=0.08,
        client=client,
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(adapter.analyze, _request())
        assert sink.entered.wait(1)
        with pytest.raises(
            ExceptionGroup,
            match="Alibaba Vision analyzer shutdown failed",
        ) as captured:
            adapter.close()
        assert adapter.shutdown_drained is False
        sink.release.set()
        pending.result(timeout=1)

    assert adapter.shutdown_drained is True
    assert [type(error) for error in captured.value.exceptions] == [
        TimeoutError,
        TimeoutError,
    ]
    assert [str(error) for error in captured.value.exceptions] == [
        "Vision HTTP shutdown timed out",
        "Alibaba Vision analyzer shutdown timed out with active calls",
    ]


def test_alibaba_adapter_close_fails_closed_when_artifact_persistence_does_not_finish() -> None:
    sink = BlockingRequestArtifactSink()
    adapter = _adapter(
        lambda _: _response(_provider_output()),
        sink,
        repair_attempts=0,
        connect_timeout_seconds=0.01,
        read_timeout_seconds=0.01,
        end_to_end_timeout_seconds=0.08,
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(adapter.analyze, _request())
        assert sink.entered.wait(1)
        started = time.monotonic()
        with pytest.raises(
            ExceptionGroup,
            match="Alibaba Vision analyzer shutdown failed",
        ) as captured:
            adapter.close()
        elapsed = time.monotonic() - started
        assert adapter.shutdown_drained is False
        with pytest.raises(RuntimeError, match="closing or closed"):
            adapter.analyze(_request())
        sink.release.set()
        result = pending.result(timeout=1)
        assert adapter.shutdown_drained is True
        adapter.close()

    assert elapsed < 0.3
    assert [type(error) for error in captured.value.exceptions] == [TimeoutError]
    assert [str(error) for error in captured.value.exceptions] == [
        "Alibaba Vision analyzer shutdown timed out with active calls"
    ]
    assert result.status == VisionProviderStatus.TIMEOUT
    assert result.error is not None
    assert result.error.retryable is True


def test_alibaba_adapter_rejects_calls_after_close_without_persisting_artifacts() -> None:
    sink = MemoryArtifactSink()
    adapter = _adapter(lambda _: _response(_provider_output()), sink, repair_attempts=0)

    adapter.close()

    with pytest.raises(RuntimeError, match="closing or closed"):
        adapter.analyze(_request())
    assert sink.writes == []


def test_alibaba_adapter_end_to_end_deadline_includes_repair_calls() -> None:
    sink = MemoryArtifactSink()
    call_count = 0
    release_second = threading.Event()

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            await asyncio.sleep(0.12)
            return _response("{not-json", request_id="request-malformed")
        while not release_second.is_set():
            await asyncio.sleep(0.005)
        return _response(_provider_output(), request_id="request-repaired")

    adapter = _adapter(
        handler,
        sink,
        connect_timeout_seconds=0.01,
        read_timeout_seconds=0.01,
        end_to_end_timeout_seconds=0.2,
    )
    try:
        started = time.monotonic()
        result = adapter.analyze(_request())
        elapsed = time.monotonic() - started
    finally:
        release_second.set()
        adapter.close()

    assert result.status == VisionProviderStatus.UNKNOWN
    assert result.error is not None
    assert result.error.retryable is False
    assert [call.status for call in result.calls] == [
        VisionProviderStatus.MALFORMED,
        VisionProviderStatus.UNKNOWN,
    ]
    assert elapsed < 0.27


def test_alibaba_adapter_marks_an_unsent_repair_as_pre_submission_timeout() -> None:
    sink = DelayedRepairRequestArtifactSink(0.08)
    provider_calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal provider_calls
        provider_calls += 1
        return _response("{not-json", request_id="request-malformed")

    adapter = _adapter(
        handler,
        sink,
        connect_timeout_seconds=0.01,
        read_timeout_seconds=0.01,
        end_to_end_timeout_seconds=0.05,
    )
    try:
        result = adapter.analyze(_request())
    finally:
        adapter.close()

    assert provider_calls == 1
    assert [call.status for call in result.calls] == [
        VisionProviderStatus.MALFORMED,
        VisionProviderStatus.TIMEOUT,
    ]
    assert result.error is not None
    assert result.error.code == "PROVIDER_TIMEOUT"
    assert result.error.retryable is True


def test_alibaba_adapter_tokenizes_untrusted_provider_request_identifiers() -> None:
    sink = MemoryArtifactSink()
    raw_identifier = "https://provider.invalid/request?credential=must-not-leak"
    adapter = _adapter(
        lambda _: _response(_provider_output(), request_id=raw_identifier),
        sink,
        repair_attempts=0,
    )
    try:
        result = adapter.analyze(_request())
    finally:
        adapter.close()

    assert result.request_id is not None
    assert result.request_id.startswith("sha256:")
    assert len(result.request_id) == 71
    assert raw_identifier not in result.model_dump_json()


def test_alibaba_adapter_rejects_success_after_response_artifact_crosses_deadline() -> None:
    sink = DelayedResponseArtifactSink(0.08)
    adapter = _adapter(
        lambda _: _response(_provider_output(), request_id="req-success"),
        sink,
        repair_attempts=0,
        connect_timeout_seconds=0.01,
        read_timeout_seconds=0.01,
        end_to_end_timeout_seconds=0.05,
    )
    try:
        result = adapter.analyze(_request())
    finally:
        adapter.close()

    assert result.status == VisionProviderStatus.UNKNOWN
    assert result.error is not None
    assert result.error.code == "PROVIDER_POST_SUBMISSION_DEADLINE_EXCEEDED"
    assert result.error.retryable is False
    assert result.request_id == "req-success"
    assert result.request_artifact is not None
    assert result.response_artifact is not None


def test_alibaba_adapter_rereads_mounted_api_key_for_each_submission(
    tmp_path,
) -> None:
    key_path = tmp_path / "model-studio-api-key"
    replacement_path = tmp_path / "model-studio-api-key.next"
    key_path.write_text("first-secret-key\n", encoding="utf-8")
    authorizations: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        authorizations.append(request.headers["Authorization"])
        return _response(_provider_output(), request_id=f"request-{len(authorizations)}")

    credential_provider = MountedFileVisionApiKeyProvider(
        path=key_path,
        maximum_bytes=64,
    )
    adapter = _adapter(
        handler,
        MemoryArtifactSink(),
        repair_attempts=0,
        credential_provider=credential_provider,
    )
    second_request = _request().model_copy(
        update={
            "operation_id": "019b0000-0000-7000-8000-000000000040",
            "product_brief_id": "019b0000-0000-7000-8000-000000000041",
        }
    )
    try:
        assert adapter.analyze(_request()).status == VisionProviderStatus.SUCCEEDED
        replacement_path.write_text("second-secret-key\n", encoding="utf-8")
        os.replace(replacement_path, key_path)
        assert adapter.analyze(second_request).status == VisionProviderStatus.SUCCEEDED
    finally:
        adapter.close()

    assert authorizations == [
        "Bearer first-secret-key",
        "Bearer second-secret-key",
    ]
    assert "first-secret-key" not in repr(adapter)
    assert "second-secret-key" not in repr(credential_provider)


def test_alibaba_adapter_fails_closed_when_mounted_api_key_exceeds_bound(
    tmp_path,
) -> None:
    key_path = tmp_path / "model-studio-api-key"
    key_path.write_text("credential-too-large", encoding="utf-8")
    provider_calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal provider_calls
        provider_calls += 1
        return _response(_provider_output())

    adapter = _adapter(
        handler,
        MemoryArtifactSink(),
        repair_attempts=0,
        credential_provider=MountedFileVisionApiKeyProvider(
            path=key_path,
            maximum_bytes=8,
        ),
    )
    try:
        result = adapter.analyze(_request())
    finally:
        adapter.close()

    assert result.status == VisionProviderStatus.UNAVAILABLE
    assert result.error is not None
    assert result.error.code == "PROVIDER_CREDENTIAL_UNAVAILABLE"
    assert result.error.retryable is True
    assert provider_calls == 0


def test_alibaba_adapter_fails_closed_when_mounted_api_key_cannot_be_opened(
    tmp_path,
) -> None:
    key_path = tmp_path / "model-studio-api-key"
    key_path.mkdir()
    provider_calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal provider_calls
        provider_calls += 1
        return _response(_provider_output())

    adapter = _adapter(
        handler,
        MemoryArtifactSink(),
        repair_attempts=0,
        credential_provider=MountedFileVisionApiKeyProvider(
            path=key_path,
            maximum_bytes=64,
        ),
    )
    try:
        result = adapter.analyze(_request())
    finally:
        adapter.close()

    assert result.status == VisionProviderStatus.UNAVAILABLE
    assert result.error is not None
    assert result.error.code == "PROVIDER_CREDENTIAL_UNAVAILABLE"
    assert provider_calls == 0


def test_alibaba_adapter_requires_url_lifetime_for_the_full_deadline() -> None:
    sink = MemoryArtifactSink()
    adapter = _adapter(lambda request: _response(_provider_output()), sink)
    expiring_request = _request().model_copy(
        update={
            "images": (
                _request().images[0].model_copy(update={"expires_at": NOW + timedelta(seconds=5)}),
            )
        }
    )
    try:
        with pytest.raises(ValueError, match="end-to-end provider deadline"):
            adapter.analyze(expiring_request)
    finally:
        adapter.close()

    assert sink.writes == []


def test_alibaba_adapter_rejects_deadline_shorter_than_transport_budget() -> None:
    sink = MemoryArtifactSink()

    with pytest.raises(ValueError, match="transport timeout budget"):
        _adapter(
            lambda request: _response(_provider_output()),
            sink,
            connect_timeout_seconds=0.06,
            read_timeout_seconds=0.06,
            end_to_end_timeout_seconds=0.1,
        )
