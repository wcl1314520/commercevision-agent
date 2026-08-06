"""Durable image-generation execution behind the existing Operation interface."""

from __future__ import annotations

from typing import Protocol

from commercevision_application import (
    AuthorizedGenerationDispatch,
    GenerationDispatchAttemptClaim,
    GenerationDispatchAttemptCoordinator,
    GenerationDispatchAuthority,
    GenerationDispatchAuthorityDenied,
    GenerationProviderDispatcher,
    GenerationResultConverger,
    GenerationSuccessCommit,
    OperationExecutionFailure,
    OperationExecutionRequest,
    OperationExecutionResult,
    OperationReconciliationResult,
    UnknownOperationOutcome,
    generation_provider_call_id,
)
from commercevision_contracts.image_provider import (
    ImageProviderAdapter,
    ImageProviderCallOutcome,
    ImageProviderTaskState,
    NormalizedImageProviderOutcome,
)
from commercevision_contracts.object_storage import (
    GenerationMediaWriteRequest,
    ObjectReference,
    ObjectStorage,
)
from commercevision_domain import (
    NormalizedOperationError,
    OperationKind,
    StorageLocationClass,
)


class GenerationAdapterResolver(Protocol):
    """Resolve one credential-owning adapter by immutable configuration identity."""

    def resolve(
        self,
        *,
        endpoint_capability_version_id: str,
        configuration_sha256: str,
    ) -> ImageProviderAdapter: ...


class GenerationResultAdmission(Protocol):
    """Admit terminal Provider bytes under the current generation safety policy."""

    def admit(
        self,
        *,
        dispatch: AuthorizedGenerationDispatch,
        outcome: NormalizedImageProviderOutcome,
    ) -> str: ...


class AtomicGenerationProviderDispatcher:
    """Dispatch, admit, control and atomically publish one terminal image result.

    This component deliberately does not own dispatch-attempt durability. Runtime
    wiring must place a persistent attempt coordinator in front of it before
    enabling paid Provider traffic.
    """

    def __init__(
        self,
        *,
        attempts: GenerationDispatchAttemptCoordinator,
        adapters: GenerationAdapterResolver,
        admission: GenerationResultAdmission,
        storage: ObjectStorage,
        converger: GenerationResultConverger,
    ) -> None:
        self._attempts = attempts
        self._adapters = adapters
        self._admission = admission
        self._storage = storage
        self._converger = converger

    def submit(
        self,
        dispatch: AuthorizedGenerationDispatch,
    ) -> OperationExecutionResult:
        if not isinstance(dispatch, AuthorizedGenerationDispatch):
            raise self._failure(
                code="GENERATION_DISPATCH_INVALID",
                category="integrity",
                message="generation dispatch contract is invalid",
                retryable=False,
            )
        try:
            adapter = self._adapters.resolve(
                endpoint_capability_version_id=(dispatch.endpoint_capability_version_id),
                configuration_sha256=dispatch.adapter_configuration_sha256,
            )
        except Exception as exc:
            raise self._failure(
                code="GENERATION_ADAPTER_UNAVAILABLE",
                category="configuration",
                message="generation Provider Adapter is unavailable",
                retryable=False,
            ) from exc

        try:
            claim = self._attempts.claim(dispatch)
        except Exception as exc:
            raise self._unknown(
                code="GENERATION_DISPATCH_FENCE_UNKNOWN",
                message="generation dispatch fence outcome is unknown",
            ) from exc
        if not isinstance(claim, GenerationDispatchAttemptClaim):
            raise self._unknown(
                code="GENERATION_DISPATCH_FENCE_INVALID",
                message="generation dispatch fence returned an invalid decision",
            )
        if not claim.submit_authorized:
            raise self._unknown(
                code="GENERATION_DISPATCH_ALREADY_STARTED",
                message="generation Provider dispatch may already have started",
                provider_request_id=(claim.provider_request_id or claim.provider_task_id),
            )

        try:
            outcome = adapter.submit(dispatch.provider_request)
        except Exception as exc:
            raise self._unknown(
                code="GENERATION_PROVIDER_OUTCOME_UNKNOWN",
                message="generation Provider outcome is unknown after dispatch",
            ) from exc
        if not isinstance(outcome, NormalizedImageProviderOutcome):
            raise self._unknown(
                code="GENERATION_PROVIDER_RESULT_INVALID",
                message="generation Provider returned an invalid normalized outcome",
            )
        try:
            self._attempts.record_outcome(
                claim=claim,
                dispatch=dispatch,
                outcome=outcome,
            )
        except Exception as exc:
            raise self._unknown(
                code="GENERATION_PROVIDER_OUTCOME_PERSISTENCE_UNKNOWN",
                message="generation Provider outcome could not be durably recorded",
                provider_request_id=self._provider_request_id(outcome),
            ) from exc
        return self._handle_outcome(dispatch=dispatch, outcome=outcome)

    def _handle_outcome(
        self,
        *,
        dispatch: AuthorizedGenerationDispatch,
        outcome: NormalizedImageProviderOutcome,
    ) -> OperationExecutionResult:
        if outcome.call_outcome is ImageProviderCallOutcome.SAFE_TO_RETRY_PRE_DISPATCH:
            assert outcome.error is not None
            raise self._failure(
                code="GENERATION_PROVIDER_PRE_DISPATCH_RETRY",
                category="provider",
                message="generation Provider did not accept the request",
                retryable=True,
                provider_request_id=self._provider_request_id(outcome),
                retry_after_seconds=outcome.error.retry_after_seconds,
            )
        if outcome.call_outcome is ImageProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH:
            raise self._unknown(
                code="GENERATION_PROVIDER_OUTCOME_UNKNOWN",
                message="generation Provider outcome requires reconciliation",
                provider_request_id=self._provider_request_id(outcome),
            )
        if outcome.call_outcome in {
            ImageProviderCallOutcome.CONFIRMED_FAILURE,
            ImageProviderCallOutcome.CONTENT_REJECTED,
        }:
            error = outcome.error
            assert error is not None
            raise self._failure(
                code=(
                    "GENERATION_CONTENT_REJECTED"
                    if outcome.call_outcome is ImageProviderCallOutcome.CONTENT_REJECTED
                    else "GENERATION_PROVIDER_CONFIRMED_FAILURE"
                ),
                category=(
                    "content-policy"
                    if outcome.call_outcome is ImageProviderCallOutcome.CONTENT_REJECTED
                    else "provider"
                ),
                message="generation Provider rejected the request",
                retryable=False,
                provider_request_id=self._provider_request_id(outcome),
            )
        if outcome.task_state is not ImageProviderTaskState.SUCCEEDED:
            if outcome.task_state is ImageProviderTaskState.PENDING:
                raise self._unknown(
                    code="GENERATION_PROVIDER_TASK_PENDING",
                    message="generation Provider task requires reconciliation",
                    provider_request_id=self._provider_request_id(outcome),
                )
            raise self._failure(
                code="GENERATION_PROVIDER_TASK_TERMINAL",
                category="provider",
                message="generation Provider task ended without an image",
                retryable=False,
                provider_request_id=self._provider_request_id(outcome),
            )

        result = outcome.result
        assert result is not None
        try:
            moderation_sha256 = self._admission.admit(
                dispatch=dispatch,
                outcome=outcome,
            )
            provider_call_id = generation_provider_call_id(dispatch.operation)
            controlled_object = self._storage.write_generation_media_if_absent(
                GenerationMediaWriteRequest(
                    reference=ObjectReference(
                        location=StorageLocationClass.TASK,
                        key=self._object_key(
                            candidate_slot_id=dispatch.operation.target_id,
                            provider_call_id=provider_call_id,
                            media_type=result.media_type.value,
                        ),
                    ),
                    payload=result.content,
                    expected_sha256=result.content_sha256,
                    content_type=result.media_type.value,
                    durable_operation_id=dispatch.operation.operation_id,
                    candidate_slot_id=dispatch.operation.target_id,
                    provider_call_id=provider_call_id,
                )
            )
            committed = self._converger.commit_success(
                GenerationSuccessCommit(
                    operation=dispatch.operation,
                    provider_outcome=outcome,
                    controlled_object=controlled_object,
                    request_sha256=dispatch.request_sha256,
                    moderation_decision_sha256=moderation_sha256,
                    trace_id=f"generation-operation:{dispatch.operation.operation_id}",
                )
            )
        except (OperationExecutionFailure, UnknownOperationOutcome):
            raise
        except Exception as exc:
            raise self._unknown(
                code="GENERATION_RESULT_CONVERGENCE_UNKNOWN",
                message="generation result could not be durably converged",
                provider_request_id=self._provider_request_id(outcome),
            ) from exc
        if (
            not isinstance(committed, OperationExecutionResult)
            or committed.operation_id != dispatch.operation.operation_id
            or not committed.completion_committed
        ):
            raise self._unknown(
                code="GENERATION_RESULT_COMMIT_INVALID",
                message="generation result convergence returned an invalid commit",
                provider_request_id=self._provider_request_id(outcome),
            )
        return committed

    @staticmethod
    def _object_key(
        *,
        candidate_slot_id: str,
        provider_call_id: str,
        media_type: str,
    ) -> str:
        extensions = {
            "image/png": "png",
            "image/jpeg": "jpg",
            "image/webp": "webp",
        }
        return f"generation/{candidate_slot_id}/{provider_call_id}.{extensions[media_type]}"

    @staticmethod
    def _provider_request_id(outcome: NormalizedImageProviderOutcome) -> str | None:
        identity = outcome.identity
        if identity is None:
            return None
        if isinstance(identity.provider_request_id, str):
            return identity.provider_request_id
        if isinstance(identity.provider_task_id, str):
            return identity.provider_task_id
        return None

    @staticmethod
    def _failure(
        *,
        code: str,
        category: str,
        message: str,
        retryable: bool,
        provider_request_id: str | None = None,
        retry_after_seconds: int | None = None,
    ) -> OperationExecutionFailure:
        return OperationExecutionFailure(
            NormalizedOperationError(
                code=code,
                category=category,
                message=message,
                retryable=retryable,
                provider_request_id=provider_request_id,
            ),
            retry_after_seconds=retry_after_seconds,
        )

    @staticmethod
    def _unknown(
        *,
        code: str,
        message: str,
        provider_request_id: str | None = None,
    ) -> UnknownOperationOutcome:
        return UnknownOperationOutcome(
            NormalizedOperationError(
                code=code,
                category="provider",
                message=message,
                retryable=True,
                provider_request_id=provider_request_id,
            )
        )


class GenerationOperationExecutor:
    """Recheck authority, then hand one immutable dispatch to the Provider module."""

    def __init__(
        self,
        *,
        authority: GenerationDispatchAuthority,
        dispatcher: GenerationProviderDispatcher,
    ) -> None:
        self._authority = authority
        self._dispatcher = dispatcher

    def execute(self, request: OperationExecutionRequest) -> OperationExecutionResult:
        self._validate_operation_request(request)
        try:
            dispatch = self._authority.prepare_dispatch(request)
        except GenerationDispatchAuthorityDenied:
            raise OperationExecutionFailure(
                NormalizedOperationError(
                    code="GENERATION_AUTHORITY_DENIED",
                    category="authorization",
                    message="generation dispatch is no longer authorized",
                    retryable=False,
                )
            ) from None
        if dispatch.operation_id != request.operation_id:
            raise OperationExecutionFailure(
                NormalizedOperationError(
                    code="GENERATION_AUTHORITY_MISMATCH",
                    category="integrity",
                    message="generation dispatch authority does not match its Operation",
                    retryable=False,
                )
            )
        result = self._dispatcher.submit(dispatch)
        if not isinstance(result, OperationExecutionResult):
            raise UnknownOperationOutcome(
                NormalizedOperationError(
                    code="GENERATION_DISPATCH_RESULT_INVALID",
                    category="provider",
                    message="generation Provider dispatch returned an invalid result",
                    retryable=False,
                )
            )
        return result

    def reconcile(
        self,
        _request: OperationExecutionRequest,
    ) -> OperationReconciliationResult:
        raise OperationExecutionFailure(
            NormalizedOperationError(
                code="GENERATION_RECONCILIATION_DISABLED",
                category="configuration",
                message="generation reconciliation is not enabled",
                retryable=False,
            )
        )

    @staticmethod
    def _validate_operation_request(request: OperationExecutionRequest) -> None:
        if (
            request.kind is not OperationKind.IMAGE_GENERATION
            or request.target_type != "generation-candidate-slot"
            or request.target_version != 1
        ):
            raise OperationExecutionFailure(
                NormalizedOperationError(
                    code="GENERATION_OPERATION_INVALID",
                    category="integrity",
                    message="Durable Operation is not an image-generation candidate",
                    retryable=False,
                )
            )
