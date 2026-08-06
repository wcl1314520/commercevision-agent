from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from io import BytesIO

import httpx
import pytest
from commercevision_contracts.image_provider import (
    ControlledImageInput,
    ImageEditingProviderRequest,
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
)
from commercevision_providers import MountedFileVisionApiKeyProvider, StaticVisionApiKeyProvider
from commercevision_providers.image_provider import (
    DeterministicImageProviderAdapter,
    DeterministicImageProviderScenario,
)
from commercevision_providers.kuaipao_image import KuaipaoSyncImageAdapter
from commercevision_providers.vision_credentials import VisionApiKeyProvider
from PIL import Image

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def _valid_png() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (1024, 1024), (240, 240, 240)).save(buffer, format="PNG")
    return buffer.getvalue()


IMAGE_BYTES = _valid_png()


class _FailingCloseStream(httpx.AsyncByteStream):
    def __init__(self, content: bytes) -> None:
        self._content = content

    async def __aiter__(self):
        yield self._content

    async def aclose(self) -> None:
        raise httpx.ReadError("unit-test response close failure")


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


def _adapter(
    handler,
    *,
    credential_provider: VisionApiKeyProvider | None = None,
    maximum_response_bytes: int = 16 * 1024,
    maximum_result_bytes: int = 16 * 1024,
    allowed_result_hosts: frozenset[str] = frozenset(),
) -> KuaipaoSyncImageAdapter:
    return KuaipaoSyncImageAdapter(
        credential_provider=credential_provider or StaticVisionApiKeyProvider("unit-test-token"),
        endpoint="https://kuaipao.pro/v1",
        endpoint_region="global",
        allowed_hosts=frozenset({"kuaipao.pro"}),
        allowed_regions=frozenset({"global"}),
        allowed_result_hosts=allowed_result_hosts,
        model="gpt-image-2-1k",
        connect_timeout_seconds=1.0,
        read_timeout_seconds=2.0,
        maximum_concurrency=1,
        maximum_response_bytes=maximum_response_bytes,
        maximum_result_bytes=maximum_result_bytes,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        wall_clock=lambda: NOW,
    )


def test_sync_generation_maps_bounded_b64_response_to_shared_contract() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url) == "https://kuaipao.pro/v1/images/generations"
        assert request.headers["authorization"] == "Bearer unit-test-token"
        assert request.headers["accept-encoding"] == "identity"
        assert json.loads(request.content) == {
            "model": "gpt-image-2-1k",
            "prompt": "Studio product photograph on a neutral background.",
            "n": 1,
            "size": "1024x1024",
            "response_format": "b64_json",
        }
        return httpx.Response(
            200,
            headers={"X-Oneapi-Request-Id": "req-generation-1"},
            json={
                "created": 1_786_000_000,
                "data": [{"b64_json": base64.b64encode(IMAGE_BYTES).decode("ascii")}],
            },
        )

    adapter = _adapter(handler)
    try:
        outcome = adapter.submit(_generation_request())
    finally:
        adapter.close()

    assert outcome.call_outcome is ImageProviderCallOutcome.CONFIRMED_SUCCESS
    assert outcome.task_state is ImageProviderTaskState.SUCCEEDED
    assert outcome.identity is not None
    assert outcome.identity.provider_request_id == "req-generation-1"
    assert outcome.identity.provider_task_id is None
    assert outcome.result is not None
    assert outcome.result.provider_result_id == "req-generation-1:0"
    assert outcome.result.content == IMAGE_BYTES
    assert outcome.result.content_sha256 == hashlib.sha256(IMAGE_BYTES).hexdigest()
    assert outcome.result.media_type is ImageProviderMediaType.PNG
    assert (outcome.result.width, outcome.result.height) == (1024, 1024)
    assert outcome.usage is None
    assert outcome.error is None
    assert not outcome.must_reconcile


def test_connection_failure_is_safe_to_retry_before_dispatch() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unit-test connection failure", request=request)

    adapter = _adapter(handler)
    try:
        outcome = adapter.submit(_generation_request())
    finally:
        adapter.close()

    assert outcome.call_outcome is ImageProviderCallOutcome.SAFE_TO_RETRY_PRE_DISPATCH
    assert outcome.task_state is None
    assert outcome.identity is None
    assert outcome.result is None
    assert outcome.usage is None
    assert outcome.error is not None
    assert outcome.error.category is ImageProviderErrorCategory.PROVIDER_UNAVAILABLE
    assert outcome.error.code == "PRE_DISPATCH_TRANSPORT_FAILURE"
    assert outcome.error.retry_after_seconds is None
    assert outcome.is_automatic_resubmission_safe


def test_response_loss_is_unknown_and_never_automatically_resubmitted() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("unit-test response loss", request=request)

    adapter = _adapter(handler)
    try:
        outcome = adapter.submit(_generation_request())
    finally:
        adapter.close()

    assert outcome.call_outcome is ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH
    assert outcome.task_state is None
    assert outcome.identity is None
    assert outcome.result is None
    assert outcome.usage is None
    assert outcome.error is not None
    assert outcome.error.category is ImageProviderErrorCategory.TIMEOUT
    assert outcome.error.code == "PROVIDER_RESPONSE_LOST"
    assert outcome.error.retry_after_seconds is None
    assert outcome.must_reconcile
    assert not outcome.is_automatic_resubmission_safe


def test_oversized_response_is_bounded_and_requires_reconciliation() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"X-Oneapi-Request-Id": "req-oversized-1"},
            content=b'{"data":[{"b64_json":"' + b"A" * 256 + b'"}]}',
        )

    adapter = _adapter(handler, maximum_response_bytes=64)
    try:
        outcome = adapter.submit(_generation_request())
    finally:
        adapter.close()

    assert outcome.call_outcome is ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH
    assert outcome.identity is not None
    assert outcome.identity.provider_request_id == "req-oversized-1"
    assert outcome.result is None
    assert outcome.error is not None
    assert outcome.error.category is ImageProviderErrorCategory.MALFORMED_RESPONSE
    assert outcome.error.code == "PROVIDER_RESPONSE_TOO_LARGE"
    assert outcome.must_reconcile


def test_redirect_response_is_not_followed_and_requires_reconciliation() -> None:
    requested_urls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(
            307,
            headers={
                "Location": "https://untrusted.example.test/generated",
                "X-Oneapi-Request-Id": "req-redirect-1",
            },
        )

    adapter = _adapter(handler)
    try:
        outcome = adapter.submit(_generation_request())
    finally:
        adapter.close()

    assert requested_urls == ["https://kuaipao.pro/v1/images/generations"]
    assert outcome.call_outcome is ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH
    assert outcome.identity is not None
    assert outcome.identity.provider_request_id == "req-redirect-1"
    assert outcome.error is not None
    assert outcome.error.category is ImageProviderErrorCategory.MALFORMED_RESPONSE
    assert outcome.error.code == "PROVIDER_REDIRECT_REJECTED"


def test_client_configured_to_follow_redirects_is_rejected() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("constructor must fail before dispatch")

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    )
    adapter: KuaipaoSyncImageAdapter | None = None
    try:
        with pytest.raises(ValueError, match="redirect"):
            adapter = KuaipaoSyncImageAdapter(
                credential_provider=StaticVisionApiKeyProvider("unit-test-token"),
                endpoint="https://kuaipao.pro/v1",
                endpoint_region="global",
                allowed_hosts=frozenset({"kuaipao.pro"}),
                allowed_regions=frozenset({"global"}),
                model="gpt-image-2-1k",
                connect_timeout_seconds=1.0,
                read_timeout_seconds=2.0,
                maximum_concurrency=1,
                maximum_response_bytes=1024,
                maximum_result_bytes=1024,
                client=client,
                wall_clock=lambda: NOW,
            )
    finally:
        if adapter is not None:
            adapter.close()
        else:
            asyncio.run(client.aclose())


def test_malformed_success_json_is_redacted_to_unknown_outcome() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"X-Oneapi-Request-Id": "req-malformed-1"},
            content=b"{malformed-provider-body",
        )

    adapter = _adapter(handler)
    try:
        outcome = adapter.submit(_generation_request())
    finally:
        adapter.close()

    assert outcome.call_outcome is ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH
    assert outcome.identity is not None
    assert outcome.identity.provider_request_id == "req-malformed-1"
    assert outcome.result is None
    assert outcome.error is not None
    assert outcome.error.category is ImageProviderErrorCategory.MALFORMED_RESPONSE
    assert outcome.error.code == "PROVIDER_RESPONSE_MALFORMED"
    assert "malformed-provider-body" not in repr(outcome)


@pytest.mark.parametrize(
    "provider_document",
    [
        {},
        {"data": "not-a-list"},
        {"data": []},
        {"data": [{}, {}]},
        {"data": [{}]},
        {"data": [{"b64_json": "%%%invalid%%%"}]},
    ],
)
def test_malformed_success_schema_and_base64_are_unknown(
    provider_document: object,
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"X-Oneapi-Request-Id": "req-schema-1"},
            json=provider_document,
        )

    adapter = _adapter(handler)
    try:
        outcome = adapter.submit(_generation_request())
    finally:
        adapter.close()

    assert outcome.call_outcome is ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH
    assert outcome.identity is not None
    assert outcome.identity.provider_request_id == "req-schema-1"
    assert outcome.result is None
    assert outcome.error is not None
    assert outcome.error.category is ImageProviderErrorCategory.MALFORMED_RESPONSE
    assert outcome.error.code == "PROVIDER_RESPONSE_MALFORMED"


@pytest.mark.parametrize(
    ("status_code", "expected_category", "expected_code"),
    [
        (400, ImageProviderErrorCategory.INVALID_REQUEST, "PROVIDER_INVALID_REQUEST"),
        (401, ImageProviderErrorCategory.AUTHENTICATION, "PROVIDER_AUTHENTICATION_FAILED"),
        (403, ImageProviderErrorCategory.AUTHENTICATION, "PROVIDER_ACCESS_DENIED"),
        (413, ImageProviderErrorCategory.INVALID_REQUEST, "PROVIDER_REQUEST_TOO_LARGE"),
    ],
)
def test_explicit_non_safety_client_errors_are_confirmed_failures(
    status_code: int,
    expected_category: ImageProviderErrorCategory,
    expected_code: str,
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            headers={"X-Oneapi-Request-Id": "req-client-error-1"},
            json={
                "error": {
                    "type": "provider-private-type",
                    "message": "provider-private-body",
                }
            },
        )

    adapter = _adapter(handler)
    try:
        outcome = adapter.submit(_generation_request())
    finally:
        adapter.close()

    assert outcome.call_outcome is ImageProviderCallOutcome.CONFIRMED_FAILURE
    assert outcome.task_state is ImageProviderTaskState.FAILED
    assert outcome.identity is not None
    assert outcome.identity.provider_request_id == "req-client-error-1"
    assert outcome.result is None
    assert outcome.error is not None
    assert outcome.error.category is expected_category
    assert outcome.error.code == expected_code
    assert outcome.error.retry_after_seconds is None
    assert "provider-private" not in repr(outcome)


@pytest.mark.parametrize("status_code", [429, 500, 503])
def test_throttle_and_server_errors_are_unknown_without_retry_hint(
    status_code: int,
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            headers={
                "X-Oneapi-Request-Id": "req-uncertain-status-1",
                "Retry-After": "1",
            },
            json={"error": {"message": "provider-private-body"}},
        )

    adapter = _adapter(handler)
    try:
        outcome = adapter.submit(_generation_request())
    finally:
        adapter.close()

    assert outcome.call_outcome is ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH
    assert outcome.identity is not None
    assert outcome.identity.provider_request_id == "req-uncertain-status-1"
    assert outcome.error is not None
    assert outcome.error.category is ImageProviderErrorCategory.PROVIDER_UNAVAILABLE
    assert outcome.error.code == "PROVIDER_STATUS_UNCERTAIN"
    assert outcome.error.retry_after_seconds is None
    assert outcome.must_reconcile
    assert not outcome.is_automatic_resubmission_safe


@pytest.mark.parametrize("request_id", [None, "https://untrusted.example.test/id"])
def test_success_without_bounded_request_identity_is_unknown(
    request_id: str | None,
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        headers = {"X-Oneapi-Request-Id": request_id} if request_id is not None else {}
        return httpx.Response(
            200,
            headers=headers,
            json={"data": [{"b64_json": base64.b64encode(IMAGE_BYTES).decode("ascii")}]},
        )

    adapter = _adapter(handler)
    try:
        outcome = adapter.submit(_generation_request())
    finally:
        adapter.close()

    assert outcome.call_outcome is ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH
    assert outcome.identity is None
    assert outcome.result is None
    assert outcome.error is not None
    assert outcome.error.category is ImageProviderErrorCategory.MALFORMED_RESPONSE
    assert outcome.error.code == "PROVIDER_IDENTITY_MISSING_OR_INVALID"


def test_mounted_secret_is_reread_for_every_submission(tmp_path) -> None:
    secret_file = tmp_path / "kuaipao-token"
    secret_file.write_text("rotation-token-one", encoding="utf-8")
    authorizations: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        authorizations.append(request.headers["authorization"])
        request_number = len(authorizations)
        return httpx.Response(
            200,
            headers={"X-Oneapi-Request-Id": f"req-rotation-{request_number}"},
            json={"data": [{"b64_json": base64.b64encode(IMAGE_BYTES).decode("ascii")}]},
        )

    credential_provider = MountedFileVisionApiKeyProvider(
        path=secret_file,
        maximum_bytes=256,
    )
    adapter = _adapter(handler, credential_provider=credential_provider)
    try:
        first = adapter.submit(_generation_request())
        secret_file.write_text("rotation-token-two", encoding="utf-8")
        second = adapter.submit(_generation_request())
    finally:
        adapter.close()

    assert authorizations == [
        "Bearer rotation-token-one",
        "Bearer rotation-token-two",
    ]
    assert "rotation-token" not in repr(adapter)
    assert "rotation-token" not in repr(first)
    assert "rotation-token" not in repr(second)


def test_async_query_and_cancel_are_explicitly_disabled_contract_outcomes() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("query and cancel must not dispatch HTTP")

    adapter = _adapter(handler)
    identity = ImageProviderRequestIdentity(
        provider_request_id="req-synchronous-1",
        provider_task_id=None,
    )
    try:
        assert isinstance(adapter, ImageProviderAdapter)
        queried = adapter.query(
            ImageProviderQueryRequest(
                identity=identity,
                deadline=NOW + timedelta(seconds=30),
            )
        )
        cancelled = adapter.cancel(
            ImageProviderCancelRequest(
                identity=identity,
                deadline=NOW + timedelta(seconds=30),
            )
        )
    finally:
        adapter.close()

    for outcome, expected_code in (
        (queried, "SYNCHRONOUS_QUERY_DISABLED"),
        (cancelled, "SYNCHRONOUS_CANCEL_DISABLED"),
    ):
        assert outcome.call_outcome is ImageProviderCallOutcome.CONFIRMED_FAILURE
        assert outcome.task_state is ImageProviderTaskState.FAILED
        assert outcome.identity == identity
        assert outcome.result is None
        assert outcome.error is not None
        assert outcome.error.category is ImageProviderErrorCategory.INVALID_REQUEST
        assert outcome.error.code == expected_code


def test_editing_is_explicitly_disabled_without_authenticated_capability_evidence() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("disabled editing must not dispatch HTTP")

    adapter = _adapter(handler)
    try:
        outcome = adapter.submit(
            ImageEditingProviderRequest(
                provider_idempotency_key="candidate.slot.0.edit.1",
                prompt_text="Preserve the product and replace the background.",
                negative_prompt_text=None,
                media=ImageProviderMediaRequirements(
                    width=1024,
                    height=1024,
                    output_format=ImageProviderOutputFormat.PNG,
                    seed=None,
                ),
                source_image=ControlledImageInput(
                    handle="controlled-source-1",
                    role=ImageProviderInputRole.SOURCE,
                    content_sha256="a" * 64,
                    media_type=ImageProviderMediaType.PNG,
                    width=1024,
                    height=1024,
                ),
                mask_image=ControlledImageInput(
                    handle="controlled-mask-1",
                    role=ImageProviderInputRole.MASK,
                    content_sha256="b" * 64,
                    media_type=ImageProviderMediaType.PNG,
                    width=1024,
                    height=1024,
                ),
                deadline=NOW + timedelta(seconds=30),
            )
        )
    finally:
        adapter.close()

    assert outcome.call_outcome is ImageProviderCallOutcome.CONFIRMED_FAILURE
    assert outcome.task_state is ImageProviderTaskState.FAILED
    assert outcome.identity is None
    assert outcome.result is None
    assert outcome.error is not None
    assert outcome.error.category is ImageProviderErrorCategory.INVALID_REQUEST
    assert outcome.error.code == "SYNCHRONOUS_EDITING_DISABLED"


@pytest.mark.parametrize(
    "generation_request",
    [
        replace(_generation_request(), negative_prompt_text="watermark"),
        replace(
            _generation_request(),
            reference_images=(
                ControlledImageInput(
                    handle="controlled-reference-1",
                    role=ImageProviderInputRole.REFERENCE,
                    content_sha256="c" * 64,
                    media_type=ImageProviderMediaType.PNG,
                    width=1024,
                    height=1024,
                ),
            ),
        ),
        replace(
            _generation_request(),
            media=replace(_generation_request().media, seed=17),
        ),
    ],
)
def test_unverified_generation_capabilities_fail_closed_without_dispatch(
    generation_request: ImageGenerationProviderRequest,
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("unverified capabilities must not dispatch HTTP")

    adapter = _adapter(handler)
    try:
        outcome = adapter.submit(generation_request)
    finally:
        adapter.close()

    assert outcome.call_outcome is ImageProviderCallOutcome.CONFIRMED_FAILURE
    assert outcome.task_state is ImageProviderTaskState.FAILED
    assert outcome.identity is None
    assert outcome.result is None
    assert outcome.error is not None
    assert outcome.error.category is ImageProviderErrorCategory.INVALID_REQUEST
    assert outcome.error.code == "UNVERIFIED_GENERATION_CAPABILITY"


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://kuaipao.pro:8443/v1",
        "https://kuaipao.pro/alternate-api",
    ],
)
def test_endpoint_port_and_base_path_are_exact(endpoint: str) -> None:
    adapter: KuaipaoSyncImageAdapter | None = None
    try:
        with pytest.raises(ValueError, match="endpoint"):
            adapter = KuaipaoSyncImageAdapter(
                credential_provider=StaticVisionApiKeyProvider("unit-test-token"),
                endpoint=endpoint,
                endpoint_region="global",
                allowed_hosts=frozenset({"kuaipao.pro"}),
                allowed_regions=frozenset({"global"}),
                model="gpt-image-2-1k",
                connect_timeout_seconds=1.0,
                read_timeout_seconds=2.0,
                maximum_concurrency=1,
                maximum_response_bytes=1024,
                maximum_result_bytes=1024,
                wall_clock=lambda: NOW,
            )
    finally:
        if adapter is not None:
            adapter.close()


def test_allowed_https_url_result_is_downloaded_internally_without_bearer() -> None:
    requested_urls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if request.method == "POST":
            return httpx.Response(
                200,
                headers={"X-Oneapi-Request-Id": "req-url-result-1"},
                json={"data": [{"url": "https://images.kuaipao.pro/result.png?sig=opaque"}]},
            )
        assert request.method == "GET"
        assert "authorization" not in request.headers
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(
            200,
            headers={"Content-Type": "image/png"},
            content=IMAGE_BYTES,
        )

    adapter = _adapter(
        handler,
        allowed_result_hosts=frozenset({"images.kuaipao.pro"}),
    )
    try:
        outcome = adapter.submit(_generation_request())
    finally:
        adapter.close()

    assert requested_urls == [
        "https://kuaipao.pro/v1/images/generations",
        "https://images.kuaipao.pro/result.png?sig=opaque",
    ]
    assert outcome.call_outcome is ImageProviderCallOutcome.CONFIRMED_SUCCESS
    assert outcome.identity is not None
    assert outcome.identity.provider_request_id == "req-url-result-1"
    assert outcome.result is not None
    assert outcome.result.content == IMAGE_BYTES
    assert outcome.result.provider_result_id == "req-url-result-1:0"


@pytest.mark.parametrize("content_type", ["text/html", "image/jpeg"])
def test_url_result_media_type_must_match_requested_format(content_type: str) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200,
                headers={"X-Oneapi-Request-Id": "req-url-media-1"},
                json={"data": [{"url": "https://images.kuaipao.pro/result.png"}]},
            )
        return httpx.Response(
            200,
            headers={"Content-Type": content_type},
            content=IMAGE_BYTES,
        )

    adapter = _adapter(
        handler,
        allowed_result_hosts=frozenset({"images.kuaipao.pro"}),
    )
    try:
        outcome = adapter.submit(_generation_request())
    finally:
        adapter.close()

    assert outcome.call_outcome is ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH
    assert outcome.identity is not None
    assert outcome.identity.provider_request_id == "req-url-media-1"
    assert outcome.result is None
    assert outcome.error is not None
    assert outcome.error.category is ImageProviderErrorCategory.MALFORMED_RESPONSE
    assert outcome.error.code == "PROVIDER_RESULT_MEDIA_INVALID"


@pytest.mark.parametrize(
    "result_url",
    [
        "http://images.kuaipao.pro/result.png",
        "https://untrusted.example.test/result.png",
        "https://images.kuaipao.pro.untrusted.example/result.png",
        "https://user@images.kuaipao.pro/result.png",
        "https://images.kuaipao.pro:8443/result.png",
        "https://images.kuaipao.pro/result.png#fragment",
        "https://images.kuaipao.pro/" + "a" * 5000,
    ],
)
def test_result_url_ssrf_variants_fail_before_download(result_url: str) -> None:
    requested_urls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(
            200,
            headers={"X-Oneapi-Request-Id": "req-url-rejected-1"},
            json={"data": [{"url": result_url}]},
        )

    adapter = _adapter(
        handler,
        allowed_result_hosts=frozenset({"images.kuaipao.pro"}),
    )
    try:
        outcome = adapter.submit(_generation_request())
    finally:
        adapter.close()

    assert requested_urls == ["https://kuaipao.pro/v1/images/generations"]
    assert outcome.call_outcome is ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH
    assert outcome.error is not None
    assert outcome.error.code == "PROVIDER_RESULT_URL_REJECTED"


@pytest.mark.parametrize("download_status", [302, 307])
def test_result_download_redirect_is_not_followed(download_status: int) -> None:
    requested_urls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if request.method == "POST":
            return httpx.Response(
                200,
                headers={"X-Oneapi-Request-Id": "req-result-redirect-1"},
                json={"data": [{"url": "https://images.kuaipao.pro/result.png"}]},
            )
        return httpx.Response(
            download_status,
            headers={"Location": "https://untrusted.example.test/result.png"},
        )

    adapter = _adapter(
        handler,
        allowed_result_hosts=frozenset({"images.kuaipao.pro"}),
    )
    try:
        outcome = adapter.submit(_generation_request())
    finally:
        adapter.close()

    assert len(requested_urls) == 2
    assert outcome.call_outcome is ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH
    assert outcome.error is not None
    assert outcome.error.code == "PROVIDER_RESULT_FETCH_INVALID"


def test_result_download_stops_at_the_result_byte_bound() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200,
                headers={"X-Oneapi-Request-Id": "req-result-oversized-1"},
                json={"data": [{"url": "https://images.kuaipao.pro/result.png"}]},
            )
        return httpx.Response(
            200,
            headers={"Content-Type": "image/png"},
            content=b"x" * 128,
        )

    adapter = _adapter(
        handler,
        maximum_result_bytes=32,
        allowed_result_hosts=frozenset({"images.kuaipao.pro"}),
    )
    try:
        outcome = adapter.submit(_generation_request())
    finally:
        adapter.close()

    assert outcome.call_outcome is ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH
    assert outcome.error is not None
    assert outcome.error.code == "PROVIDER_RESULT_FETCH_INVALID"


@pytest.mark.parametrize("result_kind", ["b64_json", "url"])
def test_result_bytes_must_decode_as_the_requested_image(result_kind: str) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            result = (
                {"b64_json": base64.b64encode(b"not-an-image").decode("ascii")}
                if result_kind == "b64_json"
                else {"url": "https://images.kuaipao.pro/result.png"}
            )
            return httpx.Response(
                200,
                headers={"X-Oneapi-Request-Id": "req-invalid-image-1"},
                json={"data": [result]},
            )
        return httpx.Response(
            200,
            headers={"Content-Type": "image/png"},
            content=b"not-an-image",
        )

    adapter = _adapter(
        handler,
        allowed_result_hosts=frozenset({"images.kuaipao.pro"}),
    )
    try:
        outcome = adapter.submit(_generation_request())
    finally:
        adapter.close()

    assert outcome.call_outcome is ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH
    assert outcome.identity is not None
    assert outcome.identity.provider_request_id == "req-invalid-image-1"
    assert outcome.result is None
    assert outcome.error is not None
    assert outcome.error.category is ImageProviderErrorCategory.MALFORMED_RESPONSE
    assert outcome.error.code == "PROVIDER_RESULT_MEDIA_INVALID"


@pytest.mark.parametrize("status_code", [201, 202, 204])
def test_only_http_200_can_form_a_synchronous_success(status_code: int) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            headers={"X-Oneapi-Request-Id": "req-unexpected-status-1"},
            json={"data": [{"b64_json": base64.b64encode(IMAGE_BYTES).decode("ascii")}]}
            if status_code != 204
            else None,
        )

    adapter = _adapter(handler)
    try:
        outcome = adapter.submit(_generation_request())
    finally:
        adapter.close()

    assert outcome.call_outcome is ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH
    assert outcome.identity is not None
    assert outcome.identity.provider_request_id == "req-unexpected-status-1"
    assert outcome.result is None
    assert outcome.error is not None
    assert outcome.error.category is ImageProviderErrorCategory.PROVIDER_UNAVAILABLE
    assert outcome.error.code == "PROVIDER_STATUS_UNEXPECTED"


@pytest.mark.parametrize("adapter_kind", ["deterministic", "bounded-http"])
def test_synchronous_adapters_share_the_same_success_contract(adapter_kind: str) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"X-Oneapi-Request-Id": "req-contract-parity-1"},
            json={"data": [{"b64_json": base64.b64encode(IMAGE_BYTES).decode("ascii")}]},
        )

    adapter: ImageProviderAdapter
    close = None
    if adapter_kind == "deterministic":
        adapter = DeterministicImageProviderAdapter(
            scenario=DeterministicImageProviderScenario.SUCCESS,
            clock=lambda: NOW,
        )
    else:
        bounded_http = _adapter(handler)
        adapter = bounded_http
        close = bounded_http.close
    try:
        outcome = adapter.submit(_generation_request())
    finally:
        if close is not None:
            close()

    assert outcome.call_outcome is ImageProviderCallOutcome.CONFIRMED_SUCCESS
    assert outcome.task_state is ImageProviderTaskState.SUCCEEDED
    assert outcome.identity is not None
    assert outcome.result is not None
    assert outcome.result.content
    assert outcome.result.content_sha256 == hashlib.sha256(outcome.result.content).hexdigest()
    assert outcome.result.media_type is ImageProviderMediaType.PNG
    assert (outcome.result.width, outcome.result.height) == (1024, 1024)
    assert outcome.error is None
    assert not outcome.must_reconcile
    assert not outcome.is_automatic_resubmission_safe


def test_response_cleanup_failure_cannot_form_success() -> None:
    response_body = json.dumps(
        {"data": [{"b64_json": base64.b64encode(IMAGE_BYTES).decode("ascii")}]},
        separators=(",", ":"),
    ).encode("utf-8")

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"X-Oneapi-Request-Id": "req-cleanup-uncertain-1"},
            stream=_FailingCloseStream(response_body),
        )

    adapter = _adapter(handler)
    try:
        outcome = adapter.submit(_generation_request())
    finally:
        adapter.close()

    assert outcome.call_outcome is ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH
    assert outcome.identity is not None
    assert outcome.identity.provider_request_id == "req-cleanup-uncertain-1"
    assert outcome.result is None
    assert outcome.error is not None
    assert outcome.error.category is ImageProviderErrorCategory.MALFORMED_RESPONSE
    assert outcome.error.code == "PROVIDER_RESPONSE_INCOMPLETE"
