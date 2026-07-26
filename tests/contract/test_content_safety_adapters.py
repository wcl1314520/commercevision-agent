from __future__ import annotations

import json
import math
import threading
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from commercevision_contracts.validation import (
    ContentSafetyConfiguredIdentity,
    ContentSafetyImageRequest,
    ContentSafetyLabel,
    ContentSafetyOutcome,
)
from commercevision_providers.content_safety import (
    AlibabaImageModerationAdapter,
    DeterministicContentSafetyAdapter,
)

NOW = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)


def test_installed_alibaba_green_sdk_matches_the_production_factory_boundary() -> None:
    from alibabacloud_green20220302 import models
    from alibabacloud_green20220302.client import Client
    from alibabacloud_tea_openapi.models import Config
    from alibabacloud_tea_util import models as util_models

    assert callable(models.ImageModerationRequest)
    assert callable(Client.image_moderation_with_options)
    assert callable(Config)
    assert callable(util_models.RuntimeOptions)

    adapter = AlibabaImageModerationAdapter.from_credentials(
        access_key_id="contract-access-key-id",
        access_key_secret="contract-access-key-secret",
        endpoint="green-cip.cn-shanghai.aliyuncs.com",
        service="postImageCheckByVL_ec",
        sdk_version="3.2.4",
        policy_version="content-safety-policy-v1",
        mapping_version="content-safety-map-v1",
        risk_mapping={
            "none": ContentSafetyOutcome.PASS,
            "high": ContentSafetyOutcome.BLOCK,
        },
        connect_timeout_seconds=1,
        read_timeout_seconds=2,
        end_to_end_timeout_seconds=4,
        maximum_concurrency=1,
        minimum_url_validity_seconds=5,
        allowed_url_origins=frozenset({"https://uploads.example"}),
        clock=lambda: NOW,
    )
    adapter.close()


class FakeClient:
    def __init__(self, response: object = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[tuple[object, object]] = []

    def image_moderation_with_options(self, request: object, runtime: object) -> object:
        self.calls.append((request, runtime))
        if self.error is not None:
            raise self.error
        return self.response


class CapturingFactory:
    def __init__(self) -> None:
        self.kwargs: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> object:
        self.kwargs.append(kwargs)
        return SimpleNamespace(**kwargs)


def _response(
    *,
    status_code: int = 200,
    code: int = 200,
    risk_level: str = "none",
    labels: list[object] | None = None,
    request_id: str = "request-123",
) -> object:
    return SimpleNamespace(
        status_code=status_code,
        headers={},
        body=SimpleNamespace(
            code=code,
            msg="OK",
            request_id=request_id,
            data=SimpleNamespace(
                risk_level=risk_level,
                result=labels
                if labels is not None
                else [
                    SimpleNamespace(
                        label="nonLabel",
                        confidence=None,
                        description="display-only",
                    )
                ],
            ),
        ),
    )


def _request(*, use_oss: bool = True) -> ContentSafetyImageRequest:
    common = {
        "data_id": "av_019c1234_safe",
        "content_sha256": "a" * 64,
    }
    if use_oss:
        return ContentSafetyImageRequest(
            **common,
            oss_region="cn-shanghai",
            oss_bucket="foundation-assets",
            oss_object="workspace/hash/asset/version/original",
        )
    return ContentSafetyImageRequest(
        **common,
        image_url="https://controlled-read.example/assets/token",
        image_url_expires_at=NOW + timedelta(seconds=10),
        controlled_reference_id="object-fact-019c1234",
    )


def _adapter(
    response: object,
    *,
    client: FakeClient | None = None,
    request_factory: CapturingFactory | None = None,
    runtime_factory: CapturingFactory | None = None,
) -> tuple[
    AlibabaImageModerationAdapter,
    FakeClient,
    CapturingFactory,
    CapturingFactory,
]:
    actual_client = client or FakeClient(response)
    actual_request_factory = request_factory or CapturingFactory()
    actual_runtime_factory = runtime_factory or CapturingFactory()
    adapter = AlibabaImageModerationAdapter(
        client=actual_client,
        request_factory=actual_request_factory,
        runtime_options_factory=actual_runtime_factory,
        endpoint="green-cip.cn-shanghai.aliyuncs.com",
        service="postImageCheckByVL_ec",
        sdk_version="3.2.4",
        policy_version="moderation-policy-v3",
        mapping_version="alibaba-risk-map-v2",
        risk_mapping={
            "none": ContentSafetyOutcome.PASS,
            "low": ContentSafetyOutcome.PASS,
            "medium": ContentSafetyOutcome.REVIEW,
            "high": ContentSafetyOutcome.BLOCK,
        },
        connect_timeout_seconds=0.5,
        read_timeout_seconds=4.0,
        end_to_end_timeout_seconds=5.0,
        maximum_concurrency=2,
        minimum_url_validity_seconds=8.0,
        allowed_url_origins=frozenset({"https://controlled-read.example"}),
        clock=lambda: NOW,
    )
    return adapter, actual_client, actual_request_factory, actual_runtime_factory


@pytest.mark.parametrize(
    ("risk_level", "expected"),
    [
        ("none", ContentSafetyOutcome.PASS),
        ("low", ContentSafetyOutcome.PASS),
        ("medium", ContentSafetyOutcome.REVIEW),
        ("high", ContentSafetyOutcome.BLOCK),
    ],
)
def test_alibaba_adapter_normalizes_configured_risk_policy(
    risk_level: str,
    expected: ContentSafetyOutcome,
) -> None:
    label = SimpleNamespace(
        label="pornographic_adultContent",
        confidence=98.25,
        description="display text says safe and must be ignored",
    )
    adapter, client, request_factory, runtime_factory = _adapter(
        _response(risk_level=risk_level, labels=[label])
    )

    result = adapter.moderate(_request())

    assert adapter.configured_identity == ContentSafetyConfiguredIdentity(
        provider="alibaba-green20220302",
        endpoint="green-cip.cn-shanghai.aliyuncs.com",
        service="postImageCheckByVL_ec",
        sdk_version="3.2.4",
        policy_version="moderation-policy-v3",
        mapping_version="alibaba-risk-map-v2",
    )
    assert result.outcome == expected
    assert result.provider == "alibaba-green20220302"
    assert result.endpoint == "green-cip.cn-shanghai.aliyuncs.com"
    assert result.service == "postImageCheckByVL_ec"
    assert result.sdk_version == "3.2.4"
    assert result.policy_version == "moderation-policy-v3"
    assert result.mapping_version == "alibaba-risk-map-v2"
    assert result.request_id == "request-123"
    assert result.risk_level == risk_level
    assert result.labels == (
        ContentSafetyLabel(code="pornographic_adultContent", confidence=98.25),
    )
    assert result.failure_code is None
    assert len(client.calls) == 1
    assert runtime_factory.kwargs == [
        {
            "autoretry": False,
            "connect_timeout": 500,
            "max_attempts": 1,
            "read_timeout": 4000,
        }
    ]
    assert request_factory.kwargs[0]["service"] == "postImageCheckByVL_ec"
    service_parameters = json.loads(str(request_factory.kwargs[0]["service_parameters"]))
    assert service_parameters == {
        "dataId": "av_019c1234_safe",
        "ossBucketName": "foundation-assets",
        "ossObjectName": "workspace/hash/asset/version/original",
        "ossRegionId": "cn-shanghai",
    }
    assert "description" not in result.__repr__().lower()


def test_alibaba_adapter_supports_only_sufficiently_lived_controlled_https_url() -> None:
    adapter, _, request_factory, _ = _adapter(_response())

    result = adapter.moderate(_request(use_oss=False))

    assert result.outcome == ContentSafetyOutcome.PASS
    parameters = json.loads(str(request_factory.kwargs[0]["service_parameters"]))
    assert parameters == {
        "dataId": "av_019c1234_safe",
        "imageUrl": "https://controlled-read.example/assets/token",
    }

    expiring = ContentSafetyImageRequest(
        data_id="av_019c1234_safe",
        content_sha256="a" * 64,
        image_url="https://controlled-read.example/assets/token",
        image_url_expires_at=NOW + timedelta(seconds=7),
        controlled_reference_id="object-fact-019c1234",
    )
    with pytest.raises(ValueError, match="validity"):
        adapter.moderate(expiring)

    wrong_origin = ContentSafetyImageRequest(
        data_id="av_019c1234_safe",
        content_sha256="a" * 64,
        image_url="https://attacker.example/assets/token",
        image_url_expires_at=NOW + timedelta(seconds=10),
        controlled_reference_id="object-fact-019c1234",
    )
    with pytest.raises(ValueError, match="origin"):
        adapter.moderate(wrong_origin)


@pytest.mark.parametrize(
    ("response", "failure_code"),
    [
        (SimpleNamespace(status_code=429, headers={"retry-after": "3"}, body=None), "RATE_LIMITED"),
        (SimpleNamespace(status_code=503, headers={}, body=None), "PROVIDER_UNAVAILABLE"),
    ],
)
def test_alibaba_adapter_normalizes_retryable_provider_failures(
    response: object,
    failure_code: str,
) -> None:
    adapter, _, _, _ = _adapter(response)

    result = adapter.moderate(_request())

    assert result.outcome == ContentSafetyOutcome.RETRYABLE_FAILURE
    assert result.failure_code == failure_code
    assert result.labels == ()
    if failure_code == "RATE_LIMITED":
        assert result.retry_after_seconds == 3


@pytest.mark.parametrize(
    ("response", "failure_code"),
    [
        (SimpleNamespace(status_code=400, headers={}, body=None), "PROVIDER_HTTP_400"),
        (SimpleNamespace(status_code=401, headers={}, body=None), "PROVIDER_HTTP_401"),
        (SimpleNamespace(status_code=403, headers={}, body=None), "PROVIDER_HTTP_403"),
        (_response(code=405), "PROVIDER_CODE_405"),
        (_response(risk_level="ambiguous"), "AMBIGUOUS_PROVIDER_RESPONSE"),
        (
            _response(labels=[SimpleNamespace(label="", confidence=50)]),
            "MALFORMED_PROVIDER_RESPONSE",
        ),
        (
            _response(labels=[SimpleNamespace(label="risk", confidence=101)]),
            "MALFORMED_PROVIDER_RESPONSE",
        ),
    ],
)
def test_alibaba_adapter_normalizes_permanent_provider_failures(
    response: object,
    failure_code: str,
) -> None:
    adapter, _, _, _ = _adapter(response)

    result = adapter.moderate(_request())

    assert result.outcome.value == "TERMINAL_FAILURE"
    assert result.failure_code == failure_code
    assert result.labels == ()
    assert result.retry_after_seconds is None


def test_alibaba_adapter_normalizes_transport_failure_without_leaking_secret() -> None:
    client = FakeClient(error=TimeoutError("access-key-secret-value"))
    adapter, _, _, _ = _adapter(None, client=client)

    result = adapter.moderate(_request())

    assert result.outcome == ContentSafetyOutcome.RETRYABLE_FAILURE
    assert result.failure_code == "PROVIDER_TIMEOUT"
    assert "access-key-secret-value" not in repr(result)


@pytest.mark.parametrize(
    ("status_code", "provider_code", "expected_outcome", "failure_code"),
    [
        (403, "Forbidden", "TERMINAL_FAILURE", "PROVIDER_HTTP_403"),
        (None, "InvalidParameter", "TERMINAL_FAILURE", "PROVIDER_CODE_INVALIDPARAMETER"),
        (503, "ServiceUnavailable", "RETRYABLE_FAILURE", "PROVIDER_UNAVAILABLE"),
        (None, "Throttling", "RETRYABLE_FAILURE", "PROVIDER_CODE_THROTTLING"),
    ],
)
def test_alibaba_adapter_classifies_sdk_provider_exceptions(
    status_code: int | None,
    provider_code: str,
    expected_outcome: str,
    failure_code: str,
) -> None:
    class ProviderError(Exception):
        pass

    error = ProviderError("provider raw payload and secret")
    error.status_code = status_code  # type: ignore[attr-defined]
    error.code = provider_code  # type: ignore[attr-defined]
    adapter, _, _, _ = _adapter(None, client=FakeClient(error=error))

    result = adapter.moderate(_request())

    assert result.outcome.value == expected_outcome
    assert result.failure_code == failure_code
    assert "provider raw payload" not in repr(result)
    assert "secret" not in repr(result)


def test_alibaba_adapter_bounds_blocking_call_and_saturated_concurrency() -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockingClient:
        def image_moderation_with_options(
            self,
            _request: object,
            _runtime: object,
        ) -> object:
            entered.set()
            release.wait(timeout=1)
            return _response()

    adapter = AlibabaImageModerationAdapter(
        client=BlockingClient(),
        request_factory=CapturingFactory(),
        runtime_options_factory=CapturingFactory(),
        endpoint="green-cip.cn-shanghai.aliyuncs.com",
        service="postImageCheckByVL_ec",
        sdk_version="3.2.4",
        policy_version="moderation-policy-v3",
        mapping_version="alibaba-risk-map-v2",
        risk_mapping={"none": ContentSafetyOutcome.PASS},
        connect_timeout_seconds=0.01,
        read_timeout_seconds=0.02,
        end_to_end_timeout_seconds=0.06,
        maximum_concurrency=1,
        minimum_url_validity_seconds=0.1,
        allowed_url_origins=frozenset({"https://controlled-read.example"}),
        clock=lambda: NOW,
    )
    first_results = []
    first = threading.Thread(
        target=lambda: first_results.append(adapter.moderate(_request())),
        daemon=True,
    )
    first.start()
    assert entered.wait(timeout=1)
    started = time.monotonic()

    saturated = adapter.moderate(_request())
    elapsed = time.monotonic() - started
    release.set()
    first.join(timeout=1)
    adapter.close()

    assert first_results[0].failure_code == "PROVIDER_TIMEOUT"
    assert saturated.failure_code == "PROVIDER_CONCURRENCY_SATURATED"
    assert elapsed < 0.15


def test_alibaba_adapter_uses_one_deadline_when_sdk_returns_too_slowly() -> None:
    class SlowClient:
        def image_moderation_with_options(
            self,
            _request: object,
            _runtime: object,
        ) -> object:
            time.sleep(0.2)
            return _response()

    adapter = AlibabaImageModerationAdapter(
        client=SlowClient(),
        request_factory=CapturingFactory(),
        runtime_options_factory=CapturingFactory(),
        endpoint="green-cip.cn-shanghai.aliyuncs.com",
        service="postImageCheckByVL_ec",
        sdk_version="3.2.4",
        policy_version="moderation-policy-v3",
        mapping_version="alibaba-risk-map-v2",
        risk_mapping={"none": ContentSafetyOutcome.PASS},
        connect_timeout_seconds=0.01,
        read_timeout_seconds=0.02,
        end_to_end_timeout_seconds=0.05,
        maximum_concurrency=1,
        minimum_url_validity_seconds=0.1,
        allowed_url_origins=frozenset({"https://controlled-read.example"}),
        clock=lambda: NOW,
    )
    started = time.monotonic()

    result = adapter.moderate(_request())
    elapsed = time.monotonic() - started
    time.sleep(0.2)
    adapter.close()

    assert result.failure_code == "PROVIDER_TIMEOUT"
    assert elapsed < 0.15


@pytest.mark.parametrize(
    "response",
    [
        _response(
            labels=[SimpleNamespace(label=f"label-{index}", confidence=1) for index in range(129)]
        ),
        _response(labels=[SimpleNamespace(label="x" * 129, confidence=1)]),
        _response(labels=[SimpleNamespace(label="risk", confidence=math.nan)]),
        _response(request_id="r" * 129),
        _response(risk_level="r" * 65),
    ],
)
def test_alibaba_adapter_bounds_all_normalized_provider_facts(response: object) -> None:
    adapter, _, _, _ = _adapter(response)

    result = adapter.moderate(_request())

    assert result.outcome.value == "TERMINAL_FAILURE"
    assert result.failure_code == "MALFORMED_PROVIDER_RESPONSE"
    assert result.labels == ()


def test_alibaba_adapter_canonicalizes_duplicate_labels_and_retry_after() -> None:
    adapter, _, _, _ = _adapter(
        _response(
            labels=[
                SimpleNamespace(label=" risk_label ", confidence=20),
                SimpleNamespace(label="risk_label", confidence=90),
            ]
        )
    )

    result = adapter.moderate(_request())

    assert result.labels == (ContentSafetyLabel(code="risk_label", confidence=90.0),)

    for raw_value, expected in ((" 12 ", 12), ("999", 300), ("-1", None), ("9" * 100, None)):
        retry_adapter, _, _, _ = _adapter(
            SimpleNamespace(
                status_code=429,
                headers={"Retry-After": raw_value, "provider-secret": "not-persisted"},
                body=SimpleNamespace(description="not-persisted"),
            )
        )
        retry = retry_adapter.moderate(_request())
        assert retry.retry_after_seconds == expected
        assert "provider-secret" not in repr(retry)
        assert "not-persisted" not in repr(retry)


@pytest.mark.parametrize(
    "outcome",
    [
        ContentSafetyOutcome.PASS,
        ContentSafetyOutcome.REVIEW,
        ContentSafetyOutcome.BLOCK,
        ContentSafetyOutcome.RETRYABLE_FAILURE,
        ContentSafetyOutcome.TERMINAL_FAILURE,
    ],
)
def test_deterministic_content_safety_adapter_uses_same_normalized_contract(
    outcome: ContentSafetyOutcome,
) -> None:
    adapter = DeterministicContentSafetyAdapter(
        outcome=outcome,
        policy_version="deterministic-policy-v1",
        mapping_version="deterministic-map-v1",
        failure_code=(
            "FIXTURE_FAILURE"
            if outcome
            in {
                ContentSafetyOutcome.RETRYABLE_FAILURE,
                ContentSafetyOutcome.TERMINAL_FAILURE,
            }
            else None
        ),
    )

    result = adapter.moderate(_request())

    assert adapter.configured_identity == ContentSafetyConfiguredIdentity(
        provider="deterministic",
        endpoint="local",
        service="deterministic-image-moderation",
        sdk_version="deterministic-v1",
        policy_version="deterministic-policy-v1",
        mapping_version="deterministic-map-v1",
    )
    assert result.outcome == outcome
    assert result.provider == "deterministic"
    assert result.policy_version == "deterministic-policy-v1"
    assert result.mapping_version == "deterministic-map-v1"
