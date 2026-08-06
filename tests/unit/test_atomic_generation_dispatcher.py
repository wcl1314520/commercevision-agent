from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from commercevision_application import (
    AuthorizedGenerationDispatch,
    GenerationDispatchAttemptClaim,
    GenerationDispatchAttemptCoordinator,
    GenerationSuccessCommit,
    OperationExecutionFailure,
    OperationExecutionRequest,
    OperationExecutionResult,
    UnknownOperationOutcome,
)
from commercevision_contracts.image_provider import (
    ImageGenerationProviderRequest,
    ImageProviderCallOutcome,
    ImageProviderError,
    ImageProviderErrorCategory,
    ImageProviderMediaRequirements,
    ImageProviderMediaType,
    ImageProviderOutputFormat,
    ImageProviderRequestIdentity,
    ImageProviderResult,
    ImageProviderTaskState,
    ImageProviderUsage,
    ImageProviderUsageUnit,
    NormalizedImageProviderOutcome,
)
from commercevision_contracts.object_storage import (
    GenerationMediaWriteRequest,
    ObjectStat,
    ServerSideEncryptionState,
)
from commercevision_domain import OperationKind, StorageBackend
from commercevision_worker.generation import AtomicGenerationProviderDispatcher

NOW = datetime(2026, 8, 6, 16, 0, tzinfo=UTC)


def _operation() -> OperationExecutionRequest:
    return OperationExecutionRequest(
        operation_id="019b0000-0000-7000-8000-000000000921",
        workspace_id="workspace-phase4",
        kind=OperationKind.IMAGE_GENERATION,
        target_type="generation-candidate-slot",
        target_id="019b0000-0000-7000-8000-000000000922",
        target_version=1,
        input_hash="a" * 64,
        input_ref=None,
        provider_request_id=None,
        attempt_count=1,
        idempotency_key="durable-operation:019b0000-0000-7000-8000-000000000921",
        execution_version=3,
        lease_token="019b0000-0000-7000-8000-000000000923",
        lease_expires_at=NOW + timedelta(minutes=2),
    )


def _provider_request() -> ImageGenerationProviderRequest:
    return ImageGenerationProviderRequest(
        provider_idempotency_key=_operation().idempotency_key,
        prompt_text="Approved product image",
        negative_prompt_text=None,
        media=ImageProviderMediaRequirements(
            width=1024,
            height=1024,
            output_format=ImageProviderOutputFormat.PNG,
        ),
        reference_images=(),
        deadline=NOW + timedelta(minutes=1),
    )


def _dispatch() -> AuthorizedGenerationDispatch:
    return AuthorizedGenerationDispatch(
        operation=_operation(),
        endpoint_capability_version_id="019b0000-0000-7000-8000-000000000924",
        adapter_configuration_sha256="b" * 64,
        provider_request=_provider_request(),
    )


def _success() -> NormalizedImageProviderOutcome:
    payload = b"validated-generation-image"
    return NormalizedImageProviderOutcome(
        call_outcome=ImageProviderCallOutcome.CONFIRMED_SUCCESS,
        task_state=ImageProviderTaskState.SUCCEEDED,
        identity=ImageProviderRequestIdentity(
            provider_request_id="provider-request-1",
            provider_task_id=None,
        ),
        result=ImageProviderResult(
            provider_result_id="provider-result-1",
            content=payload,
            content_sha256=hashlib.sha256(payload).hexdigest(),
            media_type=ImageProviderMediaType.PNG,
            width=1024,
            height=1024,
        ),
        usage=ImageProviderUsage(
            unit=ImageProviderUsageUnit.IMAGE,
            quantity=Decimal("1.000000"),
            evidence_sha256="d" * 64,
        ),
        error=None,
        latency_ms=50,
    )


class _Adapter:
    def __init__(
        self,
        outcome: NormalizedImageProviderOutcome,
        *,
        events: list[str] | None = None,
    ) -> None:
        self.outcome = outcome
        self.requests = []
        self.events = events

    def submit(self, request):
        if self.events is not None:
            self.events.append("provider.submit")
        self.requests.append(request)
        return self.outcome


class _Resolver:
    def __init__(self, adapter: _Adapter) -> None:
        self.adapter = adapter
        self.calls = []

    def resolve(self, *, endpoint_capability_version_id: str, configuration_sha256: str):
        self.calls.append((endpoint_capability_version_id, configuration_sha256))
        return self.adapter


class _Admission:
    def __init__(self) -> None:
        self.calls = []

    def admit(self, *, dispatch, outcome) -> str:
        self.calls.append((dispatch, outcome))
        return "e" * 64


class _Attempts(GenerationDispatchAttemptCoordinator):
    def __init__(
        self,
        *,
        submit_authorized: bool = True,
        events: list[str] | None = None,
    ) -> None:
        self.submit_authorized = submit_authorized
        self.events = events
        self.claims: list[AuthorizedGenerationDispatch] = []
        self.outcomes: list[NormalizedImageProviderOutcome] = []

    def claim(self, dispatch: AuthorizedGenerationDispatch) -> GenerationDispatchAttemptClaim:
        if self.events is not None:
            self.events.append("attempt.claim")
        self.claims.append(dispatch)
        return GenerationDispatchAttemptClaim(
            attempt_id="019b0000-0000-7000-8000-000000000925",
            submit_authorized=self.submit_authorized,
            provider_request_id=(None if self.submit_authorized else "provider-request-existing"),
            provider_task_id=None,
        )

    def record_outcome(
        self,
        *,
        claim: GenerationDispatchAttemptClaim,
        dispatch: AuthorizedGenerationDispatch,
        outcome: NormalizedImageProviderOutcome,
    ) -> None:
        assert claim.attempt_id == "019b0000-0000-7000-8000-000000000925"
        assert dispatch == _dispatch()
        if self.events is not None:
            self.events.append("attempt.record")
        self.outcomes.append(outcome)


class _Storage:
    def __init__(self, outcome: NormalizedImageProviderOutcome) -> None:
        self.outcome = outcome
        self.requests: list[GenerationMediaWriteRequest] = []

    def write_generation_media_if_absent(self, request: GenerationMediaWriteRequest) -> ObjectStat:
        self.requests.append(request)
        result = self.outcome.result
        assert result is not None
        return ObjectStat(
            reference=request.reference.model_copy(update={"version_id": "version-1"}),
            backend=StorageBackend.MINIO,
            bucket="task-assets",
            etag='"etag-1"',
            content_length=len(result.content),
            content_type=result.media_type.value,
            checksum_sha256_base64=None,
            metadata={"sha256": result.content_sha256},
            last_modified=NOW,
            server_side_encryption=ServerSideEncryptionState.AES256,
        )


class _Converger:
    def __init__(self) -> None:
        self.commits: list[GenerationSuccessCommit] = []

    def commit_success(self, commit: GenerationSuccessCommit) -> OperationExecutionResult:
        self.commits.append(commit)
        return OperationExecutionResult(
            operation_id=commit.operation.operation_id,
            output_ref="asset-version:result-1",
            provider_request_id="provider-request-1",
            completion_committed=True,
        )


def test_atomic_generation_dispatcher_admits_writes_and_commits_one_success() -> None:
    outcome = _success()
    events: list[str] = []
    adapter = _Adapter(outcome, events=events)
    resolver = _Resolver(adapter)
    attempts = _Attempts(events=events)
    admission = _Admission()
    storage = _Storage(outcome)
    converger = _Converger()
    dispatcher = AtomicGenerationProviderDispatcher(
        attempts=attempts,
        adapters=resolver,
        admission=admission,
        storage=storage,
        converger=converger,
    )

    result = dispatcher.submit(_dispatch())

    assert result.completion_committed is True
    assert len(_dispatch().request_sha256) == 64
    assert events == ["attempt.claim", "provider.submit", "attempt.record"]
    assert attempts.claims == [_dispatch()]
    assert attempts.outcomes == [outcome]
    assert adapter.requests == [_provider_request()]
    assert len(admission.calls) == 1
    assert len(storage.requests) == 1
    assert storage.requests[0].candidate_slot_id == _operation().target_id
    assert len(converger.commits) == 1
    assert converger.commits[0].moderation_decision_sha256 == "e" * 64


def test_atomic_generation_dispatcher_never_publishes_unknown_or_predispatch_failure() -> None:
    for outcome, exception in (
        (
            NormalizedImageProviderOutcome(
                call_outcome=ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH,
                task_state=None,
                identity=None,
                result=None,
                usage=None,
                error=ImageProviderError(
                    category=ImageProviderErrorCategory.TIMEOUT,
                    code="timeout",
                    retry_after_seconds=None,
                ),
                latency_ms=50,
            ),
            UnknownOperationOutcome,
        ),
        (
            NormalizedImageProviderOutcome(
                call_outcome=ImageProviderCallOutcome.SAFE_TO_RETRY_PRE_DISPATCH,
                task_state=None,
                identity=None,
                result=None,
                usage=None,
                error=ImageProviderError(
                    category=ImageProviderErrorCategory.RATE_LIMITED,
                    code="rate-limited",
                    retry_after_seconds=2,
                ),
                latency_ms=50,
            ),
            OperationExecutionFailure,
        ),
    ):
        adapter = _Adapter(outcome)
        admission = _Admission()
        storage = _Storage(outcome)
        converger = _Converger()
        dispatcher = AtomicGenerationProviderDispatcher(
            attempts=_Attempts(),
            adapters=_Resolver(adapter),
            admission=admission,
            storage=storage,
            converger=converger,
        )

        with pytest.raises(exception):
            dispatcher.submit(_dispatch())

        assert admission.calls == []
        assert storage.requests == []
        assert converger.commits == []


def test_atomic_generation_dispatcher_never_resubmits_an_existing_attempt() -> None:
    adapter = _Adapter(_success())
    attempts = _Attempts(submit_authorized=False)
    dispatcher = AtomicGenerationProviderDispatcher(
        attempts=attempts,
        adapters=_Resolver(adapter),
        admission=_Admission(),
        storage=_Storage(_success()),
        converger=_Converger(),
    )

    with pytest.raises(UnknownOperationOutcome) as captured:
        dispatcher.submit(_dispatch())

    assert captured.value.error.provider_request_id == "provider-request-existing"
    assert adapter.requests == []
    assert attempts.outcomes == []
