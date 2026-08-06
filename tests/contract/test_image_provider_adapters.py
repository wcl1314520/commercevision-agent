from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from commercevision_contracts.image_provider import (
    ControlledImageInput,
    ImageEditingProviderRequest,
    ImageGenerationProviderRequest,
    ImageProviderAdapter,
    ImageProviderCallOutcome,
    ImageProviderCancelRequest,
    ImageProviderError,
    ImageProviderErrorCategory,
    ImageProviderInputRole,
    ImageProviderMediaRequirements,
    ImageProviderOutputFormat,
    ImageProviderQueryRequest,
    ImageProviderRequestIdentity,
    ImageProviderResult,
    ImageProviderTaskState,
    ImageProviderUsage,
    ImageProviderUsageUnit,
    NormalizedImageProviderOutcome,
)
from commercevision_providers.image_provider import (
    DeterministicImageProviderAdapter,
    DeterministicImageProviderScenario,
)

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def _generation_request() -> ImageGenerationProviderRequest:
    return ImageGenerationProviderRequest(
        provider_idempotency_key="candidate.slot.0.attempt.1",
        prompt_text="Studio product photograph on a neutral background.",
        negative_prompt_text="watermark, distorted product",
        media=ImageProviderMediaRequirements(
            width=1024,
            height=1024,
            output_format=ImageProviderOutputFormat.PNG,
            seed=17,
        ),
        reference_images=(),
        deadline=NOW + timedelta(seconds=30),
    )


def _pre_dispatch_outcome() -> NormalizedImageProviderOutcome:
    return NormalizedImageProviderOutcome(
        call_outcome=ImageProviderCallOutcome.SAFE_TO_RETRY_PRE_DISPATCH,
        task_state=None,
        identity=None,
        result=None,
        usage=None,
        error=ImageProviderError(
            category=ImageProviderErrorCategory.PROVIDER_UNAVAILABLE,
            code="TEST_PRE_DISPATCH",
            retry_after_seconds=None,
        ),
        latency_ms=0,
    )


def test_call_outcomes_freeze_the_five_dispatch_meanings() -> None:
    assert {outcome.value for outcome in ImageProviderCallOutcome} == {
        "CONFIRMED_SUCCESS",
        "CONFIRMED_FAILURE",
        "CONTENT_REJECTED",
        "SAFE_TO_RETRY_PRE_DISPATCH",
        "UNKNOWN_AFTER_POSSIBLE_DISPATCH",
    }


def test_image_provider_contract_and_fixture_are_public_package_exports() -> None:
    from commercevision_contracts import ImageProviderAdapter as PublicAdapter
    from commercevision_contracts import (
        NormalizedImageProviderOutcome as PublicOutcome,
    )
    from commercevision_providers import (
        DeterministicImageProviderAdapter as PublicDeterministicAdapter,
    )

    assert PublicAdapter is ImageProviderAdapter
    assert PublicOutcome is NormalizedImageProviderOutcome
    assert PublicDeterministicAdapter is DeterministicImageProviderAdapter


def test_provider_outcomes_bind_bounded_identity_result_usage_and_error_facts() -> None:
    identity = ImageProviderRequestIdentity(
        provider_request_id="request-123",
        provider_task_id=None,
    )
    result = ImageProviderResult(
        provider_result_id="result-123",
        content=b"deterministic-image-bytes",
        content_sha256="bc6f661175264e0ea3ccbfb70adbb3e4226b7f612aaab94aaa47ea725eb73880",
        media_type="image/png",
        width=1024,
        height=1024,
    )
    usage = ImageProviderUsage(
        unit=ImageProviderUsageUnit.IMAGE,
        quantity=Decimal("1.000000"),
        evidence_sha256="d" * 64,
    )

    success = NormalizedImageProviderOutcome(
        call_outcome=ImageProviderCallOutcome.CONFIRMED_SUCCESS,
        task_state=ImageProviderTaskState.SUCCEEDED,
        identity=identity,
        result=result,
        usage=usage,
        error=None,
        latency_ms=12,
    )
    assert not success.must_reconcile
    assert not success.is_automatic_resubmission_safe

    pre_dispatch = NormalizedImageProviderOutcome(
        call_outcome=ImageProviderCallOutcome.SAFE_TO_RETRY_PRE_DISPATCH,
        task_state=None,
        identity=None,
        result=None,
        usage=None,
        error=ImageProviderError(
            category=ImageProviderErrorCategory.PROVIDER_UNAVAILABLE,
            code="CONCURRENCY_SATURATED",
            retry_after_seconds=1,
        ),
        latency_ms=1,
    )
    assert pre_dispatch.is_automatic_resubmission_safe
    assert not pre_dispatch.must_reconcile

    unknown = NormalizedImageProviderOutcome(
        call_outcome=ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH,
        task_state=None,
        identity=identity,
        result=None,
        usage=None,
        error=ImageProviderError(
            category=ImageProviderErrorCategory.TIMEOUT,
            code="RESPONSE_LOST",
            retry_after_seconds=None,
        ),
        latency_ms=30_000,
    )
    assert unknown.must_reconcile
    assert not unknown.is_automatic_resubmission_safe

    with pytest.raises(ValueError, match="pre-dispatch"):
        NormalizedImageProviderOutcome(
            call_outcome=ImageProviderCallOutcome.SAFE_TO_RETRY_PRE_DISPATCH,
            task_state=None,
            identity=identity,
            result=None,
            usage=None,
            error=pre_dispatch.error,
            latency_ms=1,
        )

    with pytest.raises(ValueError, match="pre-dispatch.*category"):
        NormalizedImageProviderOutcome(
            call_outcome=ImageProviderCallOutcome.SAFE_TO_RETRY_PRE_DISPATCH,
            task_state=None,
            identity=None,
            result=None,
            usage=None,
            error=ImageProviderError(
                category=ImageProviderErrorCategory.CONTENT_POLICY,
                code="WRONG_RETRY_CLASS",
                retry_after_seconds=None,
            ),
            latency_ms=1,
        )

    with pytest.raises(ValueError, match="content-policy"):
        NormalizedImageProviderOutcome(
            call_outcome=ImageProviderCallOutcome.CONFIRMED_FAILURE,
            task_state=ImageProviderTaskState.FAILED,
            identity=identity,
            result=None,
            usage=None,
            error=ImageProviderError(
                category=ImageProviderErrorCategory.CONTENT_POLICY,
                code="MISCLASSIFIED_REJECTION",
                retry_after_seconds=None,
            ),
            latency_ms=1,
        )

    with pytest.raises(ValueError, match="unknown.*retry-after"):
        NormalizedImageProviderOutcome(
            call_outcome=ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH,
            task_state=None,
            identity=identity,
            result=None,
            usage=None,
            error=ImageProviderError(
                category=ImageProviderErrorCategory.TIMEOUT,
                code="UNKNOWN_WITH_RETRY_HINT",
                retry_after_seconds=1,
            ),
            latency_ms=1,
        )

    with pytest.raises(ValueError, match="failed task state"):
        NormalizedImageProviderOutcome(
            call_outcome=ImageProviderCallOutcome.CONFIRMED_FAILURE,
            task_state=ImageProviderTaskState.NOT_FOUND,
            identity=identity,
            result=None,
            usage=None,
            error=ImageProviderError(
                category=ImageProviderErrorCategory.INVALID_REQUEST,
                code="TASK_NOT_FOUND",
                retry_after_seconds=None,
            ),
            latency_ms=1,
        )

    with pytest.raises(ValueError, match="content-rejected.*retry-after"):
        NormalizedImageProviderOutcome(
            call_outcome=ImageProviderCallOutcome.CONTENT_REJECTED,
            task_state=ImageProviderTaskState.REJECTED,
            identity=identity,
            result=None,
            usage=None,
            error=ImageProviderError(
                category=ImageProviderErrorCategory.CONTENT_POLICY,
                code="REJECTION_WITH_RETRY_HINT",
                retry_after_seconds=1,
            ),
            latency_ms=1,
        )

    with pytest.raises(ValueError, match="unknown.*category"):
        NormalizedImageProviderOutcome(
            call_outcome=ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH,
            task_state=None,
            identity=identity,
            result=None,
            usage=None,
            error=ImageProviderError(
                category=ImageProviderErrorCategory.CONTENT_POLICY,
                code="UNKNOWN_CONTENT_POLICY",
                retry_after_seconds=None,
            ),
            latency_ms=1,
        )

    with pytest.raises(ValueError, match="successful"):
        NormalizedImageProviderOutcome(
            call_outcome=ImageProviderCallOutcome.CONFIRMED_SUCCESS,
            task_state=ImageProviderTaskState.SUCCEEDED,
            identity=identity,
            result=None,
            usage=usage,
            error=None,
            latency_ms=12,
        )


def test_adapter_contract_rejects_urls_credentials_and_unverified_result_bytes() -> None:
    with pytest.raises(ValueError, match="opaque handle"):
        ControlledImageInput(
            handle="https://untrusted.example/input.png",
            role=ImageProviderInputRole.REFERENCE,
            content_sha256="a" * 64,
            media_type="image/png",
            width=10,
            height=10,
        )

    with pytest.raises(ValueError, match="identity"):
        ImageProviderRequestIdentity(
            provider_request_id="https://untrusted.example/result.png",
            provider_task_id=None,
        )

    credential_shaped = "".join(("s", "k-", "not-a-real-credential"))
    with pytest.raises(ValueError, match="credential-like"):
        ImageProviderRequestIdentity(
            provider_request_id=credential_shaped,
            provider_task_id=None,
        )

    with pytest.raises(ValueError, match="hash"):
        ImageProviderResult(
            provider_result_id="result-123",
            content=b"provider-result",
            content_sha256="e" * 64,
            media_type="image/webp",
            width=10,
            height=10,
        )


def test_adapter_contract_repr_redacts_prompt_raw_identity_and_result_bytes() -> None:
    prompt = "private campaign prompt"
    request_id = "provider-request-private"
    result_bytes = b"private-provider-result-bytes"
    request = ImageGenerationProviderRequest(
        provider_idempotency_key="private-idempotency-key",
        prompt_text=prompt,
        negative_prompt_text="private negative prompt",
        media=ImageProviderMediaRequirements(
            width=10,
            height=10,
            output_format=ImageProviderOutputFormat.PNG,
            seed=None,
        ),
        reference_images=(),
        deadline=NOW + timedelta(seconds=10),
    )
    identity = ImageProviderRequestIdentity(
        provider_request_id=request_id,
        provider_task_id=None,
    )
    result = ImageProviderResult(
        provider_result_id="provider-result-private",
        content=result_bytes,
        content_sha256="e3cdab2dc68f39d3c5d7cd43d49cc57dc6f97ec77a1c41052f4641d340a400db",
        media_type="image/png",
        width=10,
        height=10,
    )

    combined = repr((request, identity, result))

    assert prompt not in combined
    assert "private negative prompt" not in combined
    assert "private-idempotency-key" not in combined
    assert request_id not in combined
    assert "provider-result-private" not in combined
    assert result_bytes.decode() not in combined


def test_adapter_port_exposes_only_submit_query_and_cancel_contracts() -> None:
    identity = ImageProviderRequestIdentity(
        provider_request_id="request-123",
        provider_task_id="task-456",
    )
    query = ImageProviderQueryRequest(
        identity=identity,
        deadline=NOW + timedelta(seconds=10),
    )
    cancel = ImageProviderCancelRequest(
        identity=identity,
        deadline=NOW + timedelta(seconds=10),
    )

    class RecordingAdapter:
        def __init__(self) -> None:
            self.actions: list[str] = []

        def submit(
            self,
            request: ImageGenerationProviderRequest | ImageEditingProviderRequest,
        ) -> NormalizedImageProviderOutcome:
            self.actions.append(f"submit:{request.provider_idempotency_key}")
            return _pre_dispatch_outcome()

        def query(
            self,
            request: ImageProviderQueryRequest,
        ) -> NormalizedImageProviderOutcome:
            self.actions.append(f"query:{request.identity.provider_task_id}")
            return _pre_dispatch_outcome()

        def cancel(
            self,
            request: ImageProviderCancelRequest,
        ) -> NormalizedImageProviderOutcome:
            self.actions.append(f"cancel:{request.identity.provider_task_id}")
            return _pre_dispatch_outcome()

    adapter: ImageProviderAdapter = RecordingAdapter()
    generation = _generation_request()

    adapter.submit(generation)
    adapter.query(query)
    adapter.cancel(cancel)

    assert adapter.actions == [
        "submit:candidate.slot.0.attempt.1",
        "query:task-456",
        "cancel:task-456",
    ]
    assert {field.name for field in fields(query)} == {"identity", "deadline"}
    assert {field.name for field in fields(cancel)} == {"identity", "deadline"}


@pytest.mark.parametrize(
    ("scenario", "expected_outcome", "expected_task_state"),
    [
        (
            DeterministicImageProviderScenario.SUCCESS,
            ImageProviderCallOutcome.CONFIRMED_SUCCESS,
            ImageProviderTaskState.SUCCEEDED,
        ),
        (
            DeterministicImageProviderScenario.CONFIRMED_FAILURE,
            ImageProviderCallOutcome.CONFIRMED_FAILURE,
            ImageProviderTaskState.FAILED,
        ),
        (
            DeterministicImageProviderScenario.CONTENT_REJECTED,
            ImageProviderCallOutcome.CONTENT_REJECTED,
            ImageProviderTaskState.REJECTED,
        ),
        (
            DeterministicImageProviderScenario.SAFE_TO_RETRY_PRE_DISPATCH,
            ImageProviderCallOutcome.SAFE_TO_RETRY_PRE_DISPATCH,
            None,
        ),
        (
            DeterministicImageProviderScenario.UNKNOWN_AFTER_POSSIBLE_DISPATCH,
            ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH,
            None,
        ),
    ],
)
def test_deterministic_adapter_reproduces_submit_scenarios(
    scenario: DeterministicImageProviderScenario,
    expected_outcome: ImageProviderCallOutcome,
    expected_task_state: ImageProviderTaskState | None,
) -> None:
    adapter = DeterministicImageProviderAdapter(scenario=scenario, clock=lambda: NOW)

    first = adapter.submit(_generation_request())
    replay = adapter.submit(_generation_request())

    assert first == replay
    assert first.call_outcome is expected_outcome
    assert first.task_state is expected_task_state
    assert first.must_reconcile is (
        scenario is DeterministicImageProviderScenario.UNKNOWN_AFTER_POSSIBLE_DISPATCH
    )
    assert first.is_automatic_resubmission_safe is (
        scenario is DeterministicImageProviderScenario.SAFE_TO_RETRY_PRE_DISPATCH
    )


@pytest.mark.parametrize(
    ("scenario", "expected_query_state"),
    [
        (DeterministicImageProviderScenario.ASYNC_PENDING, ImageProviderTaskState.PENDING),
        (DeterministicImageProviderScenario.ASYNC_SUCCESS, ImageProviderTaskState.SUCCEEDED),
    ],
)
def test_deterministic_adapter_reproduces_query_and_cancel_fixtures(
    scenario: DeterministicImageProviderScenario,
    expected_query_state: ImageProviderTaskState,
) -> None:
    adapter = DeterministicImageProviderAdapter(scenario=scenario, clock=lambda: NOW)
    submitted = adapter.submit(_generation_request())
    assert submitted.task_state is ImageProviderTaskState.PENDING
    assert submitted.identity is not None
    task_request = ImageProviderQueryRequest(
        identity=submitted.identity,
        deadline=NOW + timedelta(seconds=10),
    )

    queried = adapter.query(task_request)

    assert queried.task_state is expected_query_state
    assert queried.identity == submitted.identity
    assert (queried.result is not None) is (
        expected_query_state is ImageProviderTaskState.SUCCEEDED
    )

    pending_adapter = DeterministicImageProviderAdapter(
        scenario=DeterministicImageProviderScenario.ASYNC_PENDING,
        clock=lambda: NOW,
    )
    pending = pending_adapter.submit(_generation_request())
    assert pending.identity is not None
    cancel_request = ImageProviderCancelRequest(
        identity=pending.identity,
        deadline=NOW + timedelta(seconds=10),
    )
    cancelled = pending_adapter.cancel(cancel_request)

    assert cancelled.call_outcome is ImageProviderCallOutcome.CONFIRMED_SUCCESS
    assert cancelled.task_state is ImageProviderTaskState.CANCELLED
    assert (
        pending_adapter.query(
            ImageProviderQueryRequest(
                identity=pending.identity,
                deadline=NOW + timedelta(seconds=10),
            )
        ).task_state
        is ImageProviderTaskState.CANCELLED
    )
    assert pending_adapter.submit(_generation_request()).task_state is (
        ImageProviderTaskState.CANCELLED
    )


def test_query_not_found_never_becomes_a_dispatch_failure_or_resubmit_signal() -> None:
    adapter = DeterministicImageProviderAdapter(
        scenario=DeterministicImageProviderScenario.UNKNOWN_AFTER_POSSIBLE_DISPATCH,
        clock=lambda: NOW,
    )
    unknown = adapter.submit(_generation_request())
    assert unknown.identity is not None

    queried = adapter.query(
        ImageProviderQueryRequest(
            identity=unknown.identity,
            deadline=NOW + timedelta(seconds=10),
        )
    )

    assert queried.call_outcome is ImageProviderCallOutcome.CONFIRMED_SUCCESS
    assert queried.task_state is ImageProviderTaskState.NOT_FOUND
    assert queried.error is None
    assert not queried.must_reconcile
    assert not queried.is_automatic_resubmission_safe


def test_deterministic_success_is_terminal_across_cancel_and_submit_replay() -> None:
    adapter = DeterministicImageProviderAdapter(
        scenario=DeterministicImageProviderScenario.ASYNC_SUCCESS,
        clock=lambda: NOW,
    )
    submitted = adapter.submit(_generation_request())
    assert submitted.identity is not None
    queried = adapter.query(
        ImageProviderQueryRequest(
            identity=submitted.identity,
            deadline=NOW + timedelta(seconds=10),
        )
    )
    assert queried.task_state is ImageProviderTaskState.SUCCEEDED

    cancelled = adapter.cancel(
        ImageProviderCancelRequest(
            identity=submitted.identity,
            deadline=NOW + timedelta(seconds=10),
        )
    )

    assert cancelled.task_state is ImageProviderTaskState.SUCCEEDED
    assert cancelled.result == queried.result
    assert adapter.submit(_generation_request()).task_state is ImageProviderTaskState.SUCCEEDED


def test_deterministic_unknown_fixture_can_represent_lost_response_identity() -> None:
    adapter = DeterministicImageProviderAdapter(
        scenario=DeterministicImageProviderScenario.UNKNOWN_WITHOUT_IDENTITY,
        clock=lambda: NOW,
    )

    outcome = adapter.submit(_generation_request())

    assert outcome.call_outcome is ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH
    assert outcome.identity is None
    assert outcome.must_reconcile
    assert not outcome.is_automatic_resubmission_safe


def test_generation_request_contains_only_normalized_media_and_controlled_inputs() -> None:
    reference = ControlledImageInput(
        handle="controlled-input.reference.0",
        role=ImageProviderInputRole.REFERENCE,
        content_sha256="a" * 64,
        media_type="image/png",
        width=1024,
        height=1024,
    )
    request = ImageGenerationProviderRequest(
        provider_idempotency_key="candidate.slot.0.attempt.1",
        prompt_text="Studio product photograph on a neutral background.",
        negative_prompt_text="watermark, distorted product",
        media=ImageProviderMediaRequirements(
            width=1024,
            height=1024,
            output_format=ImageProviderOutputFormat.PNG,
            seed=17,
        ),
        reference_images=(reference,),
        deadline=NOW + timedelta(seconds=30),
    )

    assert request.reference_images == (reference,)
    assert request.media.output_format is ImageProviderOutputFormat.PNG
    assert request.deadline == NOW + timedelta(seconds=30)
    assert {field.name for field in fields(request)}.isdisjoint(
        {
            "workspace_id",
            "workflow_id",
            "candidate_slot_id",
            "route_decision_id",
            "provider",
            "endpoint",
            "model",
            "secret",
            "url",
            "candidate_image_id",
        }
    )


def test_editing_request_uses_distinct_source_and_mask_handles_without_authority() -> None:
    source = ControlledImageInput(
        handle="controlled-input.source",
        role=ImageProviderInputRole.SOURCE,
        content_sha256="b" * 64,
        media_type="image/jpeg",
        width=1200,
        height=1200,
    )
    mask = ControlledImageInput(
        handle="controlled-input.mask",
        role=ImageProviderInputRole.MASK,
        content_sha256="c" * 64,
        media_type="image/png",
        width=1200,
        height=1200,
    )

    request = ImageEditingProviderRequest(
        provider_idempotency_key="candidate.slot.1.attempt.1",
        prompt_text="Repair only the masked background region.",
        negative_prompt_text=None,
        media=ImageProviderMediaRequirements(
            width=1200,
            height=1200,
            output_format=ImageProviderOutputFormat.JPEG,
            seed=None,
        ),
        source_image=source,
        mask_image=mask,
        deadline=NOW + timedelta(seconds=20),
    )

    assert request.source_image.role is ImageProviderInputRole.SOURCE
    assert request.mask_image.role is ImageProviderInputRole.MASK
    assert {field.name for field in fields(request)}.isdisjoint(
        {
            "approved_repair_scope",
            "rights_policy_version",
            "tool_policy_version",
            "route_decision_id",
            "candidate_image_id",
        }
    )
    outcome = DeterministicImageProviderAdapter(
        scenario=DeterministicImageProviderScenario.SUCCESS,
        clock=lambda: NOW,
    ).submit(request)
    assert outcome.call_outcome is ImageProviderCallOutcome.CONFIRMED_SUCCESS
    assert outcome.task_state is ImageProviderTaskState.SUCCEEDED
