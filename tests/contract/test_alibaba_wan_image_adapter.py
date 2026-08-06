from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from commercevision_contracts.image_provider import (
    ControlledImageInput,
    ImageGenerationProviderRequest,
    ImageProviderAdapter,
    ImageProviderCallOutcome,
    ImageProviderCancelRequest,
    ImageProviderErrorCategory,
    ImageProviderInputRole,
    ImageProviderMediaRequirements,
    ImageProviderMediaType,
    ImageProviderOutputFormat,
    ImageProviderQueryRequest,
    ImageProviderRequestIdentity,
    ImageProviderTaskState,
    ImageProviderUsageUnit,
    NormalizedImageProviderOutcome,
)
from commercevision_providers import (
    ControlledImageInputUnavailableError,
    MountedFileVisionApiKeyProvider,
    StaticVisionApiKeyProvider,
)
from commercevision_providers.alibaba_wan_image import AlibabaWanAsyncImageAdapter
from PIL import Image

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def _valid_png() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (1024, 1024), (240, 240, 240)).save(buffer, format="PNG")
    return buffer.getvalue()


IMAGE_BYTES = _valid_png()


def _generation_request() -> ImageGenerationProviderRequest:
    return ImageGenerationProviderRequest(
        provider_idempotency_key="candidate.slot.0.attempt.1",
        prompt_text="Studio product photograph on a neutral background.",
        negative_prompt_text=None,
        media=ImageProviderMediaRequirements(
            width=1024,
            height=1024,
            output_format=ImageProviderOutputFormat.PNG,
            seed=None,
        ),
        reference_images=(),
        deadline=NOW + timedelta(seconds=30),
    )


def test_async_generation_submit_persists_provider_request_and_task_identity() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url) == (
            "https://workspace-a.cn-beijing.maas.aliyuncs.com/api/v1/"
            "services/aigc/image-generation/generation"
        )
        assert request.headers["authorization"] == "Bearer unit-test-token"
        assert request.headers["content-type"] == "application/json"
        assert request.headers["x-dashscope-async"] == "enable"
        assert json.loads(request.content) == {
            "model": "wan2.7-image-pro",
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"text": ("Studio product photograph on a neutral background.")}
                        ],
                    }
                ]
            },
            "parameters": {
                "size": "1024*1024",
                "n": 1,
                "watermark": False,
                "thinking_mode": True,
            },
        }
        return httpx.Response(
            200,
            json={
                "output": {
                    "task_status": "PENDING",
                    "task_id": "0385dc79-5ff8-4d82-bcb6-task0001",
                },
                "request_id": "4909100c-7b5a-4f92-bfe5-request001",
            },
        )

    adapter = AlibabaWanAsyncImageAdapter(
        credential_provider=StaticVisionApiKeyProvider("unit-test-token"),
        endpoint="https://workspace-a.cn-beijing.maas.aliyuncs.com/api/v1",
        endpoint_region="cn-beijing",
        workspace_id="workspace-a",
        model="wan2.7-image-pro",
        connect_timeout_seconds=1.0,
        read_timeout_seconds=2.0,
        maximum_concurrency=1,
        maximum_response_bytes=16 * 1024,
        maximum_result_bytes=16 * 1024,
        allowed_result_hosts=frozenset({"dashscope-result-bj.oss-cn-beijing.aliyuncs.com"}),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        wall_clock=lambda: NOW,
    )
    try:
        outcome = adapter.submit(_generation_request())
    finally:
        adapter.close()

    assert outcome.call_outcome is ImageProviderCallOutcome.CONFIRMED_SUCCESS
    assert outcome.task_state is ImageProviderTaskState.PENDING
    assert outcome.identity is not None
    assert outcome.identity.provider_request_id == "4909100c-7b5a-4f92-bfe5-request001"
    assert outcome.identity.provider_task_id == "0385dc79-5ff8-4d82-bcb6-task0001"
    assert outcome.result is None
    assert outcome.usage is None
    assert outcome.error is None
    assert not outcome.must_reconcile
    assert not outcome.is_automatic_resubmission_safe


def test_endpoint_identity_freezes_workspace_region_model_protocol_and_configuration() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("configuration inspection must not perform HTTP")

    adapter = AlibabaWanAsyncImageAdapter(
        credential_provider=StaticVisionApiKeyProvider("unit-test-token"),
        endpoint="https://workspace-a.cn-beijing.maas.aliyuncs.com/api/v1",
        endpoint_region="cn-beijing",
        workspace_id="workspace-a",
        model="wan2.7-image-pro",
        connect_timeout_seconds=1.0,
        read_timeout_seconds=2.0,
        maximum_concurrency=1,
        maximum_response_bytes=16 * 1024,
        maximum_result_bytes=16 * 1024,
        allowed_result_hosts=frozenset({"dashscope-result-bj.oss-cn-beijing.aliyuncs.com"}),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        wall_clock=lambda: NOW,
    )
    try:
        identity = adapter.endpoint_identity
    finally:
        adapter.close()

    configuration = {
        "adapter_version": "alibaba-wan-async-v1",
        "allowed_result_hosts": ["dashscope-result-bj.oss-cn-beijing.aliyuncs.com"],
        "connect_timeout_seconds": 1.0,
        "endpoint": "https://workspace-a.cn-beijing.maas.aliyuncs.com/api/v1",
        "endpoint_region": "cn-beijing",
        "maximum_concurrency": 1,
        "maximum_input_bytes": 20 * 1024 * 1024,
        "maximum_response_bytes": 16 * 1024,
        "maximum_result_bytes": 16 * 1024,
        "maximum_seed": 2_147_483_647,
        "model": "wan2.7-image-pro",
        "n": 1,
        "protocol_mode": "DASHSCOPE_ASYNC_V1",
        "read_timeout_seconds": 2.0,
        "schema_version": 1,
        "thinking_mode": True,
        "watermark": False,
        "workspace_id": "workspace-a",
    }
    expected_hash = hashlib.sha256(
        json.dumps(configuration, sort_keys=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()
    assert identity.endpoint_host == "workspace-a.cn-beijing.maas.aliyuncs.com"
    assert identity.endpoint_region == "cn-beijing"
    assert identity.workspace_id == "workspace-a"
    assert identity.model == "wan2.7-image-pro"
    assert identity.protocol_mode == "DASHSCOPE_ASYNC_V1"
    assert identity.adapter_version == "alibaba-wan-async-v1"
    assert identity.configuration_sha256 == expected_hash


@pytest.mark.parametrize(
    ("endpoint", "endpoint_region", "workspace_id"),
    [
        ("https://dashscope.aliyuncs.com/api/v1", "cn-beijing", "workspace-a"),
        (
            "https://workspace-a.ap-southeast-1.maas.aliyuncs.com/api/v1",
            "cn-beijing",
            "workspace-a",
        ),
        (
            "https://workspace-b.cn-beijing.maas.aliyuncs.com/api/v1",
            "cn-beijing",
            "workspace-a",
        ),
        (
            "https://workspace-a.jp-northeast-1.maas.aliyuncs.com/api/v1",
            "jp-northeast-1",
            "workspace-a",
        ),
    ],
)
def test_endpoint_configuration_rejects_unpinned_or_unpublished_region_hosts(
    endpoint: str,
    endpoint_region: str,
    workspace_id: str,
) -> None:
    with pytest.raises(ValueError, match="workspace region"):
        AlibabaWanAsyncImageAdapter(
            credential_provider=StaticVisionApiKeyProvider("unit-test-token"),
            endpoint=endpoint,
            endpoint_region=endpoint_region,
            workspace_id=workspace_id,
            model="wan2.7-image-pro",
            connect_timeout_seconds=1.0,
            read_timeout_seconds=2.0,
            maximum_concurrency=1,
            maximum_response_bytes=16 * 1024,
            maximum_result_bytes=16 * 1024,
            allowed_result_hosts=frozenset({"dashscope-result-bj.oss-cn-beijing.aliyuncs.com"}),
            wall_clock=lambda: NOW,
        )


def test_mounted_secret_is_resolved_per_submit_and_redacted(tmp_path: Path) -> None:
    secret_file = tmp_path / "wan-api-key"
    secret_file.write_text("rotating-token-a", encoding="utf-8")
    credential_provider = MountedFileVisionApiKeyProvider(
        path=secret_file.resolve(),
        maximum_bytes=128,
    )
    observed_authorization: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        observed_authorization.append(request.headers["authorization"])
        index = len(observed_authorization)
        return httpx.Response(
            200,
            json={
                "output": {
                    "task_status": "PENDING",
                    "task_id": f"0385dc79-5ff8-4d82-bcb6-rotate{index:02d}",
                },
                "request_id": f"4909100c-7b5a-4f92-bfe5-rotate{index:02d}",
            },
        )

    adapter = AlibabaWanAsyncImageAdapter(
        credential_provider=credential_provider,
        endpoint="https://workspace-a.cn-beijing.maas.aliyuncs.com/api/v1",
        endpoint_region="cn-beijing",
        workspace_id="workspace-a",
        model="wan2.7-image-pro",
        connect_timeout_seconds=1.0,
        read_timeout_seconds=2.0,
        maximum_concurrency=1,
        maximum_response_bytes=16 * 1024,
        maximum_result_bytes=16 * 1024,
        allowed_result_hosts=frozenset({"dashscope-result-bj.oss-cn-beijing.aliyuncs.com"}),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        wall_clock=lambda: NOW,
    )
    try:
        first = adapter.submit(_generation_request())
        secret_file.write_text("rotating-token-b", encoding="utf-8")
        second = adapter.submit(_generation_request())
    finally:
        adapter.close()

    assert observed_authorization == ["Bearer rotating-token-a", "Bearer rotating-token-b"]
    representations = repr((adapter, first, second, credential_provider))
    assert "rotating-token-a" not in representations
    assert "rotating-token-b" not in representations


@pytest.mark.parametrize(
    ("status_code", "call_outcome", "task_state", "category"),
    [
        (
            400,
            ImageProviderCallOutcome.CONFIRMED_FAILURE,
            ImageProviderTaskState.FAILED,
            ImageProviderErrorCategory.INVALID_REQUEST,
        ),
        (
            401,
            ImageProviderCallOutcome.CONFIRMED_FAILURE,
            ImageProviderTaskState.FAILED,
            ImageProviderErrorCategory.AUTHENTICATION,
        ),
        (
            403,
            ImageProviderCallOutcome.CONFIRMED_FAILURE,
            ImageProviderTaskState.FAILED,
            ImageProviderErrorCategory.AUTHENTICATION,
        ),
        (
            404,
            ImageProviderCallOutcome.CONFIRMED_FAILURE,
            ImageProviderTaskState.FAILED,
            ImageProviderErrorCategory.INVALID_REQUEST,
        ),
        (
            429,
            ImageProviderCallOutcome.CONFIRMED_FAILURE,
            ImageProviderTaskState.FAILED,
            ImageProviderErrorCategory.RATE_LIMITED,
        ),
        (
            500,
            ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH,
            None,
            ImageProviderErrorCategory.PROVIDER_UNAVAILABLE,
        ),
        (
            503,
            ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH,
            None,
            ImageProviderErrorCategory.PROVIDER_UNAVAILABLE,
        ),
    ],
)
def test_submit_http_failures_preserve_identity_and_retry_safety(
    status_code: int,
    call_outcome: ImageProviderCallOutcome,
    task_state: ImageProviderTaskState | None,
    category: ImageProviderErrorCategory,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            json={
                "code": "ProviderTestError",
                "message": "provider test message must not be retained",
                "request_id": "7438d53d-6eb8-4596-8835-httpfail01",
            },
        )

    adapter = AlibabaWanAsyncImageAdapter(
        credential_provider=StaticVisionApiKeyProvider("unit-test-token"),
        endpoint="https://workspace-a.cn-beijing.maas.aliyuncs.com/api/v1",
        endpoint_region="cn-beijing",
        workspace_id="workspace-a",
        model="wan2.7-image-pro",
        connect_timeout_seconds=1.0,
        read_timeout_seconds=2.0,
        maximum_concurrency=1,
        maximum_response_bytes=16 * 1024,
        maximum_result_bytes=16 * 1024,
        allowed_result_hosts=frozenset({"dashscope-result-bj.oss-cn-beijing.aliyuncs.com"}),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        wall_clock=lambda: NOW,
    )
    try:
        outcome = adapter.submit(_generation_request())
    finally:
        adapter.close()

    assert outcome.call_outcome is call_outcome
    assert outcome.task_state is task_state
    assert outcome.identity is not None
    assert outcome.identity.provider_request_id == "7438d53d-6eb8-4596-8835-httpfail01"
    assert outcome.identity.provider_task_id is None
    assert outcome.error is not None
    assert outcome.error.category is category
    assert "provider test message" not in repr(outcome)
    assert not outcome.is_automatic_resubmission_safe


@pytest.mark.parametrize(
    ("provider_status", "provider_code", "call_outcome", "task_state", "category"),
    [
        (
            "FAILED",
            "ModelServiceFailed",
            ImageProviderCallOutcome.CONFIRMED_FAILURE,
            ImageProviderTaskState.FAILED,
            ImageProviderErrorCategory.PROVIDER_UNAVAILABLE,
        ),
        (
            "FAILED",
            "DataInspectionFailed",
            ImageProviderCallOutcome.CONTENT_REJECTED,
            ImageProviderTaskState.REJECTED,
            ImageProviderErrorCategory.CONTENT_POLICY,
        ),
        (
            "CANCELED",
            None,
            ImageProviderCallOutcome.CONFIRMED_SUCCESS,
            ImageProviderTaskState.CANCELLED,
            None,
        ),
        (
            "UNKNOWN",
            None,
            ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH,
            None,
            ImageProviderErrorCategory.PROVIDER_UNAVAILABLE,
        ),
    ],
)
def test_async_query_maps_provider_terminal_statuses_without_resubmission(
    provider_status: str,
    provider_code: str | None,
    call_outcome: ImageProviderCallOutcome,
    task_state: ImageProviderTaskState | None,
    category: ImageProviderErrorCategory | None,
) -> None:
    task_id = "0385dc79-5ff8-4d82-bcb6-task0004"
    request_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "output": {"task_status": "PENDING", "task_id": task_id},
                    "request_id": "4909100c-7b5a-4f92-bfe5-submit004",
                },
            )
        output: dict[str, object] = {
            "task_status": provider_status,
            "task_id": task_id,
        }
        if provider_code is not None:
            output["code"] = provider_code
            output["message"] = "redacted provider test message"
        return httpx.Response(
            200,
            json={
                "output": output,
                "request_id": "810fa5f5-334c-4df3-aaa4-query0004",
            },
        )

    adapter = AlibabaWanAsyncImageAdapter(
        credential_provider=StaticVisionApiKeyProvider("unit-test-token"),
        endpoint="https://workspace-a.cn-beijing.maas.aliyuncs.com/api/v1",
        endpoint_region="cn-beijing",
        workspace_id="workspace-a",
        model="wan2.7-image-pro",
        connect_timeout_seconds=1.0,
        read_timeout_seconds=2.0,
        maximum_concurrency=1,
        maximum_response_bytes=16 * 1024,
        maximum_result_bytes=16 * 1024,
        allowed_result_hosts=frozenset({"dashscope-result-bj.oss-cn-beijing.aliyuncs.com"}),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        wall_clock=lambda: NOW,
    )
    try:
        submitted = adapter.submit(_generation_request())
        assert submitted.identity is not None
        outcome = adapter.query(
            ImageProviderQueryRequest(
                identity=submitted.identity,
                deadline=NOW + timedelta(seconds=30),
            )
        )
    finally:
        adapter.close()

    assert request_count == 2
    assert outcome.call_outcome is call_outcome
    assert outcome.task_state is task_state
    assert outcome.identity is not None
    assert outcome.identity.provider_task_id == task_id
    assert outcome.result is None
    assert outcome.usage is None
    if category is None:
        assert outcome.error is None
    else:
        assert outcome.error is not None
        assert outcome.error.category is category
    assert not outcome.is_automatic_resubmission_safe
    assert outcome.must_reconcile is (
        call_outcome is ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH
    )


def test_async_query_maps_pending_without_resubmission() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "output": {
                        "task_status": "PENDING",
                        "task_id": "0385dc79-5ff8-4d82-bcb6-task0002",
                    },
                    "request_id": "4909100c-7b5a-4f92-bfe5-submit002",
                },
            )
        assert request.method == "GET"
        assert str(request.url) == (
            "https://workspace-a.cn-beijing.maas.aliyuncs.com/api/v1/tasks/"
            "0385dc79-5ff8-4d82-bcb6-task0002"
        )
        assert request.headers["authorization"] == "Bearer unit-test-token"
        assert "x-dashscope-async" not in request.headers
        return httpx.Response(
            200,
            json={
                "output": {
                    "task_status": "RUNNING",
                    "task_id": "0385dc79-5ff8-4d82-bcb6-task0002",
                },
                "request_id": "810fa5f5-334c-4df3-aaa4-query0002",
            },
        )

    adapter = AlibabaWanAsyncImageAdapter(
        credential_provider=StaticVisionApiKeyProvider("unit-test-token"),
        endpoint="https://workspace-a.cn-beijing.maas.aliyuncs.com/api/v1",
        endpoint_region="cn-beijing",
        workspace_id="workspace-a",
        model="wan2.7-image-pro",
        connect_timeout_seconds=1.0,
        read_timeout_seconds=2.0,
        maximum_concurrency=1,
        maximum_response_bytes=16 * 1024,
        maximum_result_bytes=16 * 1024,
        allowed_result_hosts=frozenset({"dashscope-result-bj.oss-cn-beijing.aliyuncs.com"}),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        wall_clock=lambda: NOW,
    )
    try:
        submitted = adapter.submit(_generation_request())
        assert submitted.identity is not None
        outcome = adapter.query(
            ImageProviderQueryRequest(
                identity=submitted.identity,
                deadline=NOW + timedelta(seconds=30),
            )
        )
    finally:
        adapter.close()

    assert len(requests) == 2
    assert outcome.call_outcome is ImageProviderCallOutcome.CONFIRMED_SUCCESS
    assert outcome.task_state is ImageProviderTaskState.PENDING
    assert outcome.identity is not None
    assert outcome.identity.provider_request_id == "810fa5f5-334c-4df3-aaa4-query0002"
    assert outcome.identity.provider_task_id == "0385dc79-5ff8-4d82-bcb6-task0002"
    assert outcome.result is None
    assert outcome.error is None


def test_async_query_downloads_and_validates_succeeded_result() -> None:
    task_id = "0385dc79-5ff8-4d82-bcb6-task0003"
    result_url = (
        "https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/output.png"
        "?Expires=1786000000&Signature=opaque"
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "output": {"task_status": "PENDING", "task_id": task_id},
                    "request_id": "4909100c-7b5a-4f92-bfe5-submit003",
                },
            )
        if request.url.host == "workspace-a.cn-beijing.maas.aliyuncs.com":
            return httpx.Response(
                200,
                json={
                    "request_id": "810fa5f5-334c-4df3-aaa4-query0003",
                    "output": {
                        "task_id": task_id,
                        "task_status": "SUCCEEDED",
                        "finished": True,
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "message": {
                                    "role": "assistant",
                                    "content": [{"image": result_url, "type": "image"}],
                                },
                            }
                        ],
                    },
                    "usage": {"size": "1024*1024", "image_count": 1},
                },
            )
        assert str(request.url) == result_url
        assert "authorization" not in request.headers
        return httpx.Response(
            200,
            headers={"Content-Type": "image/png"},
            content=IMAGE_BYTES,
        )

    adapter = AlibabaWanAsyncImageAdapter(
        credential_provider=StaticVisionApiKeyProvider("unit-test-token"),
        endpoint="https://workspace-a.cn-beijing.maas.aliyuncs.com/api/v1",
        endpoint_region="cn-beijing",
        workspace_id="workspace-a",
        model="wan2.7-image-pro",
        connect_timeout_seconds=1.0,
        read_timeout_seconds=2.0,
        maximum_concurrency=1,
        maximum_response_bytes=16 * 1024,
        maximum_result_bytes=16 * 1024,
        allowed_result_hosts=frozenset({"dashscope-result-bj.oss-cn-beijing.aliyuncs.com"}),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        wall_clock=lambda: NOW,
    )
    try:
        submitted = adapter.submit(_generation_request())
        assert submitted.identity is not None
        outcome = adapter.query(
            ImageProviderQueryRequest(
                identity=submitted.identity,
                deadline=NOW + timedelta(seconds=30),
            )
        )
    finally:
        adapter.close()

    assert outcome.call_outcome is ImageProviderCallOutcome.CONFIRMED_SUCCESS
    assert outcome.task_state is ImageProviderTaskState.SUCCEEDED
    assert outcome.identity is not None
    assert outcome.identity.provider_request_id == "810fa5f5-334c-4df3-aaa4-query0003"
    assert outcome.identity.provider_task_id == task_id
    assert outcome.result is not None
    assert outcome.result.provider_result_id == f"{task_id}:0"
    assert outcome.result.content == IMAGE_BYTES
    assert outcome.result.content_sha256 == hashlib.sha256(IMAGE_BYTES).hexdigest()
    assert outcome.result.media_type is ImageProviderMediaType.PNG
    assert (outcome.result.width, outcome.result.height) == (1024, 1024)
    assert outcome.usage is not None
    assert outcome.usage.unit is ImageProviderUsageUnit.IMAGE
    assert outcome.usage.quantity == Decimal("1.000000")
    assert outcome.error is None


def test_succeeded_query_rejects_partial_malformed_content_without_committing_result() -> None:
    task_id = "0385dc79-5ff8-4d82-bcb6-partial001"
    result_url = "https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/output.png"

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "output": {"task_status": "PENDING", "task_id": task_id},
                    "request_id": "4909100c-7b5a-4f92-bfe5-partial001",
                },
            )
        if request.url.host == "workspace-a.cn-beijing.maas.aliyuncs.com":
            return httpx.Response(
                200,
                json={
                    "request_id": "810fa5f5-334c-4df3-aaa4-partial001",
                    "output": {
                        "task_id": task_id,
                        "task_status": "SUCCEEDED",
                        "finished": True,
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "message": {
                                    "role": "assistant",
                                    "content": [
                                        {"image": result_url, "type": "image"},
                                        {"type": "text"},
                                    ],
                                },
                            }
                        ],
                    },
                    "usage": {"size": "1024*1024", "image_count": 1},
                },
            )
        return httpx.Response(
            200,
            headers={"Content-Type": "image/png"},
            content=IMAGE_BYTES,
        )

    adapter = AlibabaWanAsyncImageAdapter(
        credential_provider=StaticVisionApiKeyProvider("unit-test-token"),
        endpoint="https://workspace-a.cn-beijing.maas.aliyuncs.com/api/v1",
        endpoint_region="cn-beijing",
        workspace_id="workspace-a",
        model="wan2.7-image-pro",
        connect_timeout_seconds=1.0,
        read_timeout_seconds=2.0,
        maximum_concurrency=1,
        maximum_response_bytes=16 * 1024,
        maximum_result_bytes=16 * 1024,
        allowed_result_hosts=frozenset({"dashscope-result-bj.oss-cn-beijing.aliyuncs.com"}),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        wall_clock=lambda: NOW,
    )
    try:
        submitted = adapter.submit(_generation_request())
        assert submitted.identity is not None
        outcome = adapter.query(
            ImageProviderQueryRequest(
                identity=submitted.identity,
                deadline=NOW + timedelta(seconds=30),
            )
        )
    finally:
        adapter.close()

    assert outcome.call_outcome is ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH
    assert outcome.task_state is None
    assert outcome.result is None
    assert outcome.usage is None
    assert outcome.must_reconcile


def test_adapter_protocol_disables_unverified_cancel_without_http() -> None:
    request_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        raise AssertionError("unverified cancellation must not perform HTTP")

    adapter = AlibabaWanAsyncImageAdapter(
        credential_provider=StaticVisionApiKeyProvider("unit-test-token"),
        endpoint="https://workspace-a.cn-beijing.maas.aliyuncs.com/api/v1",
        endpoint_region="cn-beijing",
        workspace_id="workspace-a",
        model="wan2.7-image-pro",
        connect_timeout_seconds=1.0,
        read_timeout_seconds=2.0,
        maximum_concurrency=1,
        maximum_response_bytes=16 * 1024,
        maximum_result_bytes=16 * 1024,
        allowed_result_hosts=frozenset({"dashscope-result-bj.oss-cn-beijing.aliyuncs.com"}),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        wall_clock=lambda: NOW,
    )
    identity = ImageProviderRequestIdentity(
        provider_request_id="4909100c-7b5a-4f92-bfe5-cancel001",
        provider_task_id="0385dc79-5ff8-4d82-bcb6-cancel001",
    )
    try:
        assert isinstance(adapter, ImageProviderAdapter)
        outcome = adapter.cancel(
            ImageProviderCancelRequest(
                identity=identity,
                deadline=NOW + timedelta(seconds=30),
            )
        )
    finally:
        adapter.close()

    assert request_count == 0
    assert outcome.call_outcome is ImageProviderCallOutcome.CONFIRMED_FAILURE
    assert outcome.task_state is ImageProviderTaskState.FAILED
    assert outcome.identity == identity
    assert outcome.error is not None
    assert outcome.error.category is ImageProviderErrorCategory.INVALID_REQUEST
    assert outcome.error.code == "UNVERIFIED_WAN_CANCELLATION"


def test_reference_generation_resolves_controlled_bytes_without_sending_handle() -> None:
    reference = ControlledImageInput(
        handle="asset-version:reference-001",
        role=ImageProviderInputRole.REFERENCE,
        content_sha256=hashlib.sha256(IMAGE_BYTES).hexdigest(),
        media_type=ImageProviderMediaType.PNG,
        width=1024,
        height=1024,
    )

    class Resolver:
        def resolve(
            self,
            image: ControlledImageInput,
            *,
            maximum_bytes: int,
            deadline: datetime,
        ) -> bytes:
            assert image == reference
            assert maximum_bytes == 20 * 1024 * 1024
            assert deadline == NOW + timedelta(seconds=30)
            return IMAGE_BYTES

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "asset-version:reference-001" not in request.content.decode("utf-8")
        assert body["input"]["messages"][0]["content"] == [
            {"image": ("data:image/png;base64," + base64.b64encode(IMAGE_BYTES).decode("ascii"))},
            {"text": "Studio product photograph on a neutral background."},
        ]
        return httpx.Response(
            200,
            json={
                "output": {
                    "task_status": "PENDING",
                    "task_id": "0385dc79-5ff8-4d82-bcb6-reference01",
                },
                "request_id": "4909100c-7b5a-4f92-bfe5-reference01",
            },
        )

    adapter = AlibabaWanAsyncImageAdapter(
        credential_provider=StaticVisionApiKeyProvider("unit-test-token"),
        input_resolver=Resolver(),
        endpoint="https://workspace-a.cn-beijing.maas.aliyuncs.com/api/v1",
        endpoint_region="cn-beijing",
        workspace_id="workspace-a",
        model="wan2.7-image-pro",
        connect_timeout_seconds=1.0,
        read_timeout_seconds=2.0,
        maximum_concurrency=1,
        maximum_response_bytes=16 * 1024,
        maximum_result_bytes=16 * 1024,
        allowed_result_hosts=frozenset({"dashscope-result-bj.oss-cn-beijing.aliyuncs.com"}),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        wall_clock=lambda: NOW,
    )
    request = replace(_generation_request(), reference_images=(reference,))
    try:
        outcome = adapter.submit(request)
    finally:
        adapter.close()

    assert outcome.call_outcome is ImageProviderCallOutcome.CONFIRMED_SUCCESS
    assert outcome.task_state is ImageProviderTaskState.PENDING


def test_text_generation_sends_verified_seed_and_explicit_thinking_mode() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        parameters = json.loads(request.content)["parameters"]
        assert parameters == {
            "size": "1024*1024",
            "n": 1,
            "watermark": False,
            "thinking_mode": True,
            "seed": 17,
        }
        return httpx.Response(
            200,
            json={
                "output": {
                    "task_status": "PENDING",
                    "task_id": "0385dc79-5ff8-4d82-bcb6-seed0001",
                },
                "request_id": "4909100c-7b5a-4f92-bfe5-seed0001",
            },
        )

    adapter = AlibabaWanAsyncImageAdapter(
        credential_provider=StaticVisionApiKeyProvider("unit-test-token"),
        endpoint="https://workspace-a.cn-beijing.maas.aliyuncs.com/api/v1",
        endpoint_region="cn-beijing",
        workspace_id="workspace-a",
        model="wan2.7-image-pro",
        connect_timeout_seconds=1.0,
        read_timeout_seconds=2.0,
        maximum_concurrency=1,
        maximum_response_bytes=16 * 1024,
        maximum_result_bytes=16 * 1024,
        allowed_result_hosts=frozenset({"dashscope-result-bj.oss-cn-beijing.aliyuncs.com"}),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        wall_clock=lambda: NOW,
    )
    request = replace(
        _generation_request(),
        media=replace(_generation_request().media, seed=17),
    )
    try:
        outcome = adapter.submit(request)
    finally:
        adapter.close()

    assert outcome.call_outcome is ImageProviderCallOutcome.CONFIRMED_SUCCESS


def _query_result_outcome(
    result_url: str,
    *,
    result_status: int = 200,
    result_headers: dict[str, str] | None = None,
    result_content: bytes = IMAGE_BYTES,
    maximum_result_bytes: int = 16 * 1024,
) -> tuple[NormalizedImageProviderOutcome, int]:
    task_id = "0385dc79-5ff8-4d82-bcb6-resultguard"
    result_fetches = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal result_fetches
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "output": {"task_status": "PENDING", "task_id": task_id},
                    "request_id": "4909100c-7b5a-4f92-bfe5-resultguard",
                },
            )
        if request.url.host == "workspace-a.cn-beijing.maas.aliyuncs.com":
            return httpx.Response(
                200,
                json={
                    "request_id": "810fa5f5-334c-4df3-aaa4-resultguard",
                    "output": {
                        "task_id": task_id,
                        "task_status": "SUCCEEDED",
                        "finished": True,
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "message": {
                                    "role": "assistant",
                                    "content": [{"image": result_url, "type": "image"}],
                                },
                            }
                        ],
                    },
                    "usage": {"size": "1024*1024", "image_count": 1},
                },
            )
        result_fetches += 1
        return httpx.Response(
            result_status,
            headers=result_headers or {"Content-Type": "image/png"},
            content=result_content,
        )

    adapter = AlibabaWanAsyncImageAdapter(
        credential_provider=StaticVisionApiKeyProvider("unit-test-token"),
        endpoint="https://workspace-a.cn-beijing.maas.aliyuncs.com/api/v1",
        endpoint_region="cn-beijing",
        workspace_id="workspace-a",
        model="wan2.7-image-pro",
        connect_timeout_seconds=1.0,
        read_timeout_seconds=2.0,
        maximum_concurrency=1,
        maximum_response_bytes=16 * 1024,
        maximum_result_bytes=maximum_result_bytes,
        allowed_result_hosts=frozenset({"dashscope-result-bj.oss-cn-beijing.aliyuncs.com"}),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        wall_clock=lambda: NOW,
    )
    try:
        submitted = adapter.submit(_generation_request())
        assert submitted.identity is not None
        outcome = adapter.query(
            ImageProviderQueryRequest(
                identity=submitted.identity,
                deadline=NOW + timedelta(seconds=30),
            )
        )
    finally:
        adapter.close()
    return outcome, result_fetches


@pytest.mark.parametrize(
    "result_url",
    [
        "http://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/output.png",
        "https://evil.example/output.png",
        "https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com.evil.example/output.png",
        "https://user@dashscope-result-bj.oss-cn-beijing.aliyuncs.com/output.png",
        "https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com:444/output.png",
        "https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/output.png#fragment",
        "https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/output.png?" + "x" * 4096,
    ],
)
def test_result_url_ssrf_guards_reject_before_fetch(result_url: str) -> None:
    outcome, result_fetches = _query_result_outcome(result_url)

    assert result_fetches == 0
    assert outcome.call_outcome is ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH
    assert outcome.result is None
    assert outcome.must_reconcile


@pytest.mark.parametrize(
    ("result_status", "result_headers", "result_content", "maximum_result_bytes"),
    [
        (302, {"Location": "https://evil.example/output.png"}, b"", 16 * 1024),
        (206, {"Content-Type": "image/png"}, IMAGE_BYTES, 16 * 1024),
        (200, {"Content-Type": "image/jpeg"}, IMAGE_BYTES, 16 * 1024),
        (200, {"Content-Type": "image/png"}, b"not-a-png", 16 * 1024),
        (200, {"Content-Type": "image/png"}, IMAGE_BYTES, 128),
        (
            200,
            {"Content-Type": "image/png", "Content-Encoding": "gzip"},
            IMAGE_BYTES,
            16 * 1024,
        ),
    ],
)
def test_result_response_guards_reject_untrusted_media(
    result_status: int,
    result_headers: dict[str, str],
    result_content: bytes,
    maximum_result_bytes: int,
) -> None:
    outcome, result_fetches = _query_result_outcome(
        "https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/output.png",
        result_status=result_status,
        result_headers=result_headers,
        result_content=result_content,
        maximum_result_bytes=maximum_result_bytes,
    )

    assert result_fetches == 1
    assert outcome.call_outcome is ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH
    assert outcome.result is None
    assert outcome.must_reconcile


def test_expired_submit_deadline_fails_before_http() -> None:
    request_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        raise AssertionError("expired request must not perform HTTP")

    adapter = AlibabaWanAsyncImageAdapter(
        credential_provider=StaticVisionApiKeyProvider("unit-test-token"),
        endpoint="https://workspace-a.cn-beijing.maas.aliyuncs.com/api/v1",
        endpoint_region="cn-beijing",
        workspace_id="workspace-a",
        model="wan2.7-image-pro",
        connect_timeout_seconds=1.0,
        read_timeout_seconds=2.0,
        maximum_concurrency=1,
        maximum_response_bytes=16 * 1024,
        maximum_result_bytes=16 * 1024,
        allowed_result_hosts=frozenset({"dashscope-result-bj.oss-cn-beijing.aliyuncs.com"}),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        wall_clock=lambda: NOW,
    )
    try:
        outcome = adapter.submit(replace(_generation_request(), deadline=NOW))
    finally:
        adapter.close()

    assert request_count == 0
    assert outcome.call_outcome is ImageProviderCallOutcome.SAFE_TO_RETRY_PRE_DISPATCH
    assert outcome.identity is None
    assert outcome.is_automatic_resubmission_safe


def test_expired_reference_submit_does_not_resolve_input_or_perform_http() -> None:
    reference = ControlledImageInput(
        handle="asset-version:reference-expired",
        role=ImageProviderInputRole.REFERENCE,
        content_sha256=hashlib.sha256(IMAGE_BYTES).hexdigest(),
        media_type=ImageProviderMediaType.PNG,
        width=1024,
        height=1024,
    )

    class Resolver:
        def resolve(
            self,
            image: ControlledImageInput,
            *,
            maximum_bytes: int,
            deadline: datetime,
        ) -> bytes:
            raise AssertionError("expired request must not resolve controlled input")

    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("expired request must not perform HTTP")

    adapter = AlibabaWanAsyncImageAdapter(
        credential_provider=StaticVisionApiKeyProvider("unit-test-token"),
        input_resolver=Resolver(),
        endpoint="https://workspace-a.cn-beijing.maas.aliyuncs.com/api/v1",
        endpoint_region="cn-beijing",
        workspace_id="workspace-a",
        model="wan2.7-image-pro",
        connect_timeout_seconds=1.0,
        read_timeout_seconds=2.0,
        maximum_concurrency=1,
        maximum_response_bytes=16 * 1024,
        maximum_result_bytes=16 * 1024,
        allowed_result_hosts=frozenset({"dashscope-result-bj.oss-cn-beijing.aliyuncs.com"}),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        wall_clock=lambda: NOW,
    )
    request = replace(
        _generation_request(),
        reference_images=(reference,),
        deadline=NOW,
    )
    try:
        outcome = adapter.submit(request)
    finally:
        adapter.close()

    assert outcome.call_outcome is ImageProviderCallOutcome.SAFE_TO_RETRY_PRE_DISPATCH
    assert outcome.identity is None


def test_async_submit_rejects_sync_response_without_protocol_fallback() -> None:
    request_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(
            200,
            json={
                "output": {"choices": [], "finished": True},
                "request_id": "4909100c-7b5a-4f92-bfe5-syncshape01",
            },
        )

    adapter = AlibabaWanAsyncImageAdapter(
        credential_provider=StaticVisionApiKeyProvider("unit-test-token"),
        endpoint="https://workspace-a.cn-beijing.maas.aliyuncs.com/api/v1",
        endpoint_region="cn-beijing",
        workspace_id="workspace-a",
        model="wan2.7-image-pro",
        connect_timeout_seconds=1.0,
        read_timeout_seconds=2.0,
        maximum_concurrency=1,
        maximum_response_bytes=16 * 1024,
        maximum_result_bytes=16 * 1024,
        allowed_result_hosts=frozenset({"dashscope-result-bj.oss-cn-beijing.aliyuncs.com"}),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        wall_clock=lambda: NOW,
    )
    try:
        outcome = adapter.submit(_generation_request())
    finally:
        adapter.close()

    assert request_count == 1
    assert outcome.call_outcome is ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH
    assert outcome.task_state is None
    assert outcome.result is None
    assert outcome.must_reconcile


def test_submit_response_byte_bound_prevents_truncated_identity_parsing() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "padding": "x" * 4096,
                "output": {
                    "task_status": "PENDING",
                    "task_id": "0385dc79-5ff8-4d82-bcb6-oversize01",
                },
                "request_id": "4909100c-7b5a-4f92-bfe5-oversize01",
            },
        )

    adapter = AlibabaWanAsyncImageAdapter(
        credential_provider=StaticVisionApiKeyProvider("unit-test-token"),
        endpoint="https://workspace-a.cn-beijing.maas.aliyuncs.com/api/v1",
        endpoint_region="cn-beijing",
        workspace_id="workspace-a",
        model="wan2.7-image-pro",
        connect_timeout_seconds=1.0,
        read_timeout_seconds=2.0,
        maximum_concurrency=1,
        maximum_response_bytes=128,
        maximum_result_bytes=16 * 1024,
        allowed_result_hosts=frozenset({"dashscope-result-bj.oss-cn-beijing.aliyuncs.com"}),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        wall_clock=lambda: NOW,
    )
    try:
        outcome = adapter.submit(_generation_request())
    finally:
        adapter.close()

    assert outcome.call_outcome is ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH
    assert outcome.identity is None
    assert outcome.result is None


def test_malformed_query_status_is_not_treated_as_success_or_failure() -> None:
    task_id = "0385dc79-5ff8-4d82-bcb6-badstatus1"

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "output": {"task_status": "PENDING", "task_id": task_id},
                    "request_id": "4909100c-7b5a-4f92-bfe5-badstatus1",
                },
            )
        return httpx.Response(
            200,
            json={
                "output": {"task_status": "COMPLETED", "task_id": task_id},
                "request_id": "810fa5f5-334c-4df3-aaa4-badstatus1",
            },
        )

    adapter = AlibabaWanAsyncImageAdapter(
        credential_provider=StaticVisionApiKeyProvider("unit-test-token"),
        endpoint="https://workspace-a.cn-beijing.maas.aliyuncs.com/api/v1",
        endpoint_region="cn-beijing",
        workspace_id="workspace-a",
        model="wan2.7-image-pro",
        connect_timeout_seconds=1.0,
        read_timeout_seconds=2.0,
        maximum_concurrency=1,
        maximum_response_bytes=16 * 1024,
        maximum_result_bytes=16 * 1024,
        allowed_result_hosts=frozenset({"dashscope-result-bj.oss-cn-beijing.aliyuncs.com"}),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        wall_clock=lambda: NOW,
    )
    try:
        submitted = adapter.submit(_generation_request())
        assert submitted.identity is not None
        outcome = adapter.query(
            ImageProviderQueryRequest(
                identity=submitted.identity,
                deadline=NOW + timedelta(seconds=30),
            )
        )
    finally:
        adapter.close()

    assert outcome.call_outcome is ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH
    assert outcome.task_state is None
    assert outcome.identity is not None
    assert outcome.identity.provider_task_id == task_id
    assert outcome.result is None


@pytest.mark.parametrize(
    ("model", "width", "height", "with_reference"),
    [
        ("wan2.7-image-pro", 512, 512, False),
        ("wan2.7-image-pro", 9216, 1024, False),
        ("wan2.7-image-pro", 4097, 4096, False),
        ("wan2.7-image", 4096, 4096, False),
        ("wan2.7-image-pro", 4096, 4096, True),
    ],
)
def test_model_and_reference_dimension_limits_fail_before_resolver_or_http(
    model: str,
    width: int,
    height: int,
    with_reference: bool,
) -> None:
    reference = ControlledImageInput(
        handle="asset-version:dimension-reference",
        role=ImageProviderInputRole.REFERENCE,
        content_sha256=hashlib.sha256(IMAGE_BYTES).hexdigest(),
        media_type=ImageProviderMediaType.PNG,
        width=1024,
        height=1024,
    )

    class Resolver:
        def resolve(
            self,
            image: ControlledImageInput,
            *,
            maximum_bytes: int,
            deadline: datetime,
        ) -> bytes:
            raise AssertionError("invalid output dimensions must fail before input resolution")

    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("invalid output dimensions must fail before HTTP")

    adapter = AlibabaWanAsyncImageAdapter(
        credential_provider=StaticVisionApiKeyProvider("unit-test-token"),
        input_resolver=Resolver(),
        endpoint="https://workspace-a.cn-beijing.maas.aliyuncs.com/api/v1",
        endpoint_region="cn-beijing",
        workspace_id="workspace-a",
        model=model,
        connect_timeout_seconds=1.0,
        read_timeout_seconds=2.0,
        maximum_concurrency=1,
        maximum_response_bytes=16 * 1024,
        maximum_result_bytes=16 * 1024,
        allowed_result_hosts=frozenset({"dashscope-result-bj.oss-cn-beijing.aliyuncs.com"}),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        wall_clock=lambda: NOW,
    )
    request = replace(
        _generation_request(),
        media=replace(_generation_request().media, width=width, height=height),
        reference_images=(reference,) if with_reference else (),
    )
    try:
        outcome = adapter.submit(request)
    finally:
        adapter.close()

    assert outcome.call_outcome is ImageProviderCallOutcome.CONFIRMED_FAILURE
    assert outcome.error is not None
    assert outcome.error.code == "UNSUPPORTED_WAN_DIMENSIONS"


@pytest.mark.parametrize(
    ("provider_status", "finished"),
    [
        ("PENDING", True),
        ("RUNNING", True),
        ("FAILED", False),
        ("CANCELED", False),
    ],
)
def test_query_status_and_finished_conflicts_require_reconciliation(
    provider_status: str,
    finished: bool,
) -> None:
    task_id = "0385dc79-5ff8-4d82-bcb6-stateconflict"

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "output": {"task_status": "PENDING", "task_id": task_id},
                    "request_id": "4909100c-7b5a-4f92-bfe5-stateconflict",
                },
            )
        return httpx.Response(
            200,
            json={
                "output": {
                    "task_status": provider_status,
                    "task_id": task_id,
                    "finished": finished,
                    "code": "ModelServiceFailed",
                },
                "request_id": "810fa5f5-334c-4df3-aaa4-stateconflict",
            },
        )

    adapter = AlibabaWanAsyncImageAdapter(
        credential_provider=StaticVisionApiKeyProvider("unit-test-token"),
        endpoint="https://workspace-a.cn-beijing.maas.aliyuncs.com/api/v1",
        endpoint_region="cn-beijing",
        workspace_id="workspace-a",
        model="wan2.7-image-pro",
        connect_timeout_seconds=1.0,
        read_timeout_seconds=2.0,
        maximum_concurrency=1,
        maximum_response_bytes=16 * 1024,
        maximum_result_bytes=16 * 1024,
        allowed_result_hosts=frozenset({"dashscope-result-bj.oss-cn-beijing.aliyuncs.com"}),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        wall_clock=lambda: NOW,
    )
    try:
        submitted = adapter.submit(_generation_request())
        assert submitted.identity is not None
        outcome = adapter.query(
            ImageProviderQueryRequest(
                identity=submitted.identity,
                deadline=NOW + timedelta(seconds=30),
            )
        )
    finally:
        adapter.close()

    assert outcome.call_outcome is ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH
    assert outcome.task_state is None
    assert outcome.result is None
    assert outcome.must_reconcile


@pytest.mark.parametrize("resolver_scenario", ["unavailable", "hash-mismatch", "alpha"])
def test_controlled_input_failures_are_classified_before_http(
    resolver_scenario: str,
) -> None:
    alpha_buffer = BytesIO()
    Image.new("RGBA", (1024, 1024), (240, 240, 240, 128)).save(
        alpha_buffer,
        format="PNG",
    )
    alpha_bytes = alpha_buffer.getvalue()
    expected_bytes = alpha_bytes if resolver_scenario == "alpha" else IMAGE_BYTES
    reference = ControlledImageInput(
        handle="asset-version:input-failure",
        role=ImageProviderInputRole.REFERENCE,
        content_sha256=(
            "0" * 64
            if resolver_scenario == "hash-mismatch"
            else hashlib.sha256(expected_bytes).hexdigest()
        ),
        media_type=ImageProviderMediaType.PNG,
        width=1024,
        height=1024,
    )

    class Resolver:
        def resolve(
            self,
            image: ControlledImageInput,
            *,
            maximum_bytes: int,
            deadline: datetime,
        ) -> bytes:
            if resolver_scenario == "unavailable":
                raise ControlledImageInputUnavailableError("test input unavailable")
            return expected_bytes

    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("invalid or unavailable input must not perform HTTP")

    adapter = AlibabaWanAsyncImageAdapter(
        credential_provider=StaticVisionApiKeyProvider("unit-test-token"),
        input_resolver=Resolver(),
        endpoint="https://workspace-a.cn-beijing.maas.aliyuncs.com/api/v1",
        endpoint_region="cn-beijing",
        workspace_id="workspace-a",
        model="wan2.7-image-pro",
        connect_timeout_seconds=1.0,
        read_timeout_seconds=2.0,
        maximum_concurrency=1,
        maximum_response_bytes=16 * 1024,
        maximum_result_bytes=16 * 1024,
        allowed_result_hosts=frozenset({"dashscope-result-bj.oss-cn-beijing.aliyuncs.com"}),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        wall_clock=lambda: NOW,
    )
    try:
        outcome = adapter.submit(replace(_generation_request(), reference_images=(reference,)))
    finally:
        adapter.close()

    assert outcome.identity is None
    assert outcome.result is None
    assert outcome.error is not None
    if resolver_scenario == "unavailable":
        assert outcome.call_outcome is ImageProviderCallOutcome.SAFE_TO_RETRY_PRE_DISPATCH
        assert outcome.error.code == "CONTROLLED_IMAGE_UNAVAILABLE"
    else:
        assert outcome.call_outcome is ImageProviderCallOutcome.CONFIRMED_FAILURE
        assert outcome.error.code == "CONTROLLED_IMAGE_INVALID"
