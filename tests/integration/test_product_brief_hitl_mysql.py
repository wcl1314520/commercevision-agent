from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from commercevision_api.main import create_app
from commercevision_application import (
    DurableOperationWorker,
    OperationApplicationService,
    OperationCreateCommand,
    OperationExecutionBoundary,
    OperationExecutionFailure,
    OperationExecutionRequest,
    OperationRecoveryService,
    ProductBriefAnalysisExecutor,
    ProductBriefApplicationService,
    ProductBriefContinuation,
    ProductBriefPolicy,
    ProductBriefProviderArtifactReconciler,
    ProductBriefProviderArtifactService,
    RecoveryService,
    StaleProductBriefContinuation,
    UnknownOperationOutcome,
    VisionDataTransferPolicy,
    WorkflowApplicationService,
)
from commercevision_contracts import Settings
from commercevision_contracts.object_storage import ObjectVersionPage, PresignedRequest
from commercevision_contracts.product_briefs import (
    PreparedProviderArtifact,
    ProductBriefAnalysisRequestV1,
    ProviderArtifactKind,
    ProviderArtifactPersistenceError,
    ProviderArtifactReference,
    ProviderArtifactState,
    ProviderArtifactWrite,
    ProviderArtifactWriteOutcomeUnknownError,
    ProviderArtifactWriteSafeToRetryError,
    VisionAnalysisRequest,
    VisionAnalyzerIdentity,
    VisionCallLifecycle,
    VisionProviderCall,
    VisionProviderError,
    VisionProviderOutcome,
    VisionProviderStatus,
    VisionProviderUsage,
)
from commercevision_contracts.workflow import (
    ApprovalRequest,
    product_brief_checkpoint_generation,
)
from commercevision_domain import (
    ApprovalDecision,
    ApprovalType,
    NormalizedOperationError,
    OperationKind,
    OperationState,
    ProductBriefRetentionExpiredError,
    StepType,
    StorageBackend,
    StorageLocationClass,
    StoragePreconditionError,
    StorageUnavailableError,
    UploadObjectMissingError,
    WorkflowStatus,
    new_uuid7,
)
from commercevision_domain.messaging import EventEnvelope, OutboxEvent
from commercevision_persistence import (
    MySQLCheckpointSaver,
    SqlAlchemyOperationUnitOfWork,
    SqlAlchemyProductBriefUnitOfWork,
    SqlAlchemyUnitOfWork,
    is_unit_of_work_active,
)
from commercevision_providers import (
    DeterministicVisionAnalyzer,
    DeterministicVisionScenario,
)
from commercevision_tool_runtime import FixtureImageTool, ToolExecutionError
from commercevision_worker.runtime import WorkerRuntime
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.integration

WORKSPACE_ID = "product-brief-hitl"
ISOLATED_WORKSPACE_ID = "product-brief-isolated"
TRUSTED_PRINCIPAL_KEY_ID = "product-brief-gateway-2026-07"
TRUSTED_PRINCIPAL_SECRET = "product-brief-test-key-" + ("0" * 32)


class MemoryArtifactSink:
    def __init__(self) -> None:
        self.artifacts: list[ProviderArtifactWrite] = []

    def write(self, artifact: ProviderArtifactWrite) -> ProviderArtifactReference:
        assert not is_unit_of_work_active()
        self.artifacts.append(artifact)
        return ProviderArtifactReference(
            storage_backend=StorageBackend.MINIO.value,
            location=StorageLocationClass.PROVIDER_RESULT,
            bucket="provider-results",
            key=(
                f"product-brief/{artifact.operation_id}/"
                f"attempt-{artifact.operation_attempt}/call-{artifact.call_index}/"
                f"{artifact.kind.value.lower()}.json"
            ),
            provider_version_id=f"version-{artifact.sha256[:16]}",
            etag=artifact.sha256,
            sha256=artifact.sha256,
            byte_size=len(artifact.payload),
            retention_class=artifact.retention_class,
            retention_deadline=artifact.retention_deadline,
        )

    def prepare(
        self,
        artifact: ProviderArtifactWrite,
        *,
        ledger_id: str,
        write_fence: str,
    ) -> PreparedProviderArtifact:
        key = (
            f"product-brief/{artifact.operation_id}/"
            f"attempt-{artifact.operation_attempt}/call-{artifact.call_index}/"
            f"{artifact.kind.value.lower()}.json"
        )
        return PreparedProviderArtifact(
            ledger_id=ledger_id,
            key_schema_version="test-v1",
            storage_backend=StorageBackend.MINIO.value,
            location=StorageLocationClass.PROVIDER_RESULT,
            bucket="provider-results",
            key=key,
            target_sha256=hashlib.sha256(
                f"MINIO\0PROVIDER_RESULT\0provider-results\0{key}".encode()
            ).hexdigest(),
            content_type=artifact.content_type,
            expected_sha256=artifact.sha256,
            expected_byte_size=len(artifact.payload),
            retention_class=artifact.retention_class,
            retention_deadline=artifact.retention_deadline,
            write_fence=write_fence,
        )

    def write_prepared(
        self,
        artifact: ProviderArtifactWrite,
        target: PreparedProviderArtifact,
    ) -> ProviderArtifactReference:
        self.artifacts.append(artifact)
        return ProviderArtifactReference(
            storage_backend=target.storage_backend,
            location=target.location,
            bucket=target.bucket,
            key=target.key,
            provider_version_id=f"version-{artifact.sha256[:16]}",
            etag=artifact.sha256,
            sha256=artifact.sha256,
            byte_size=len(artifact.payload),
            retention_class=artifact.retention_class,
            retention_deadline=artifact.retention_deadline,
        )


class SimulatedProcessDeath(BaseException):
    """Model a worker process disappearing without running exception cleanup."""


class CrashBeforeResponseArtifactSink(MemoryArtifactSink):
    def write_prepared(
        self,
        artifact: ProviderArtifactWrite,
        target: PreparedProviderArtifact,
    ) -> ProviderArtifactReference:
        if artifact.kind == ProviderArtifactKind.RESPONSE:
            raise SimulatedProcessDeath
        return super().write_prepared(artifact, target)


class EmptyVersionStorage:
    def list_versions(
        self,
        _target,
        *,
        page_size: int,
        continuation_token: str | None,
    ) -> ObjectVersionPage:
        del page_size, continuation_token
        return ObjectVersionPage(entries=(), continuation_token=None)


class FailingArtifactSink(MemoryArtifactSink):
    def write(self, artifact: ProviderArtifactWrite) -> ProviderArtifactReference:
        raise ProviderArtifactWriteSafeToRetryError("raw provider storage detail")

    def write_prepared(
        self,
        artifact: ProviderArtifactWrite,
        target: PreparedProviderArtifact,
    ) -> ProviderArtifactReference:
        del artifact, target
        raise ProviderArtifactWriteSafeToRetryError("raw provider storage detail")


class UnknownArtifactSink(MemoryArtifactSink):
    def write(self, artifact: ProviderArtifactWrite) -> ProviderArtifactReference:
        del artifact
        raise ProviderArtifactWriteOutcomeUnknownError("raw provider write outcome unknown")

    def write_prepared(
        self,
        artifact: ProviderArtifactWrite,
        target: PreparedProviderArtifact,
    ) -> ProviderArtifactReference:
        del artifact, target
        raise ProviderArtifactWriteOutcomeUnknownError("raw provider write outcome unknown")


class ResponseArtifactFailureSink(MemoryArtifactSink):
    def __init__(self, failure_kind: str) -> None:
        super().__init__()
        self._failure_kind = failure_kind
        self.response_write_count = 0

    def write_prepared(
        self,
        artifact: ProviderArtifactWrite,
        target: PreparedProviderArtifact,
    ) -> ProviderArtifactReference:
        if artifact.kind == ProviderArtifactKind.RESPONSE:
            self.response_write_count += 1
            if self._failure_kind == "safe-prewrite":
                raise ProviderArtifactWriteSafeToRetryError(
                    "response artifact storage unavailable before write"
                )
            if self._failure_kind == "outcome-unknown":
                raise ProviderArtifactWriteOutcomeUnknownError(
                    "response artifact write outcome unknown"
                )
            raise AssertionError(f"unexpected response failure kind: {self._failure_kind}")
        return super().write_prepared(artifact, target)


class ConflictingArtifactSink(MemoryArtifactSink):
    def write(self, artifact: ProviderArtifactWrite) -> ProviderArtifactReference:
        raise StoragePreconditionError("raw provider artifact integrity detail")

    def write_prepared(
        self,
        artifact: ProviderArtifactWrite,
        target: PreparedProviderArtifact,
    ) -> ProviderArtifactReference:
        del artifact, target
        raise StoragePreconditionError("raw provider artifact integrity detail")


def _artifact_service(
    database,
    artifact_store: MemoryArtifactSink | None = None,
) -> ProductBriefProviderArtifactService:
    return ProductBriefProviderArtifactService(
        uow_factory=lambda: SqlAlchemyProductBriefUnitOfWork(database.session_factory),
        artifact_store=artifact_store or MemoryArtifactSink(),
        clock=lambda: datetime.now(UTC),
    )


class ControlledReadStorage:
    backend = StorageBackend.MINIO

    def temporary_read(self, request) -> PresignedRequest:
        assert not is_unit_of_work_active()
        assert request.reference.version_id == "controlled-version-1"
        assert request.expected_etag == "controlled-etag-1"
        assert request.expected_sha256 == "a" * 64
        return PresignedRequest(
            method="GET",
            url="https://controlled-assets.example/source.png",
            required_headers={},
            expires_at=request.expires_at,
        )


class CountingReadStorage(ControlledReadStorage):
    def __init__(self) -> None:
        self.read_count = 0

    def temporary_read(self, request) -> PresignedRequest:
        self.read_count += 1
        return super().temporary_read(request)


class MutableClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class FailingReadStorage(ControlledReadStorage):
    def temporary_read(self, request) -> PresignedRequest:
        raise StorageUnavailableError("source storage detail")


class MissingReadStorage(ControlledReadStorage):
    def temporary_read(self, request) -> PresignedRequest:
        raise UploadObjectMissingError("exact source object version is missing")


class MismatchedReadStorage(ControlledReadStorage):
    def temporary_read(self, request) -> PresignedRequest:
        raise StoragePreconditionError("exact source object version does not match")


class RevokingReadStorage(ControlledReadStorage):
    def __init__(self, database, *, mutation: str) -> None:
        self._database = database
        self._mutation = mutation
        self.read_count = 0

    def temporary_read(self, request) -> PresignedRequest:
        temporary = super().temporary_read(request)
        self.read_count += 1
        now = datetime.now(UTC).replace(tzinfo=None)
        asset_version_id = request.reference.key.split("/")[-2]
        with self._database.engine.begin() as connection:
            if self._mutation == "asset":
                connection.execute(
                    text(
                        "UPDATE assets SET status = 'BLOCKED', "
                        "block_reason = 'RIGHTS_REVOKED', version = version + 1, "
                        "updated_at = :now "
                        "WHERE workspace_id = :workspace "
                        "AND current_version_id = :asset_version_id"
                    ),
                    {
                        "asset_version_id": asset_version_id,
                        "now": now,
                        "workspace": WORKSPACE_ID,
                    },
                )
            else:
                connection.execute(
                    text(
                        "UPDATE asset_objects SET state = 'DELETE_PENDING', "
                        "version = version + 1, updated_at = :now "
                        "WHERE workspace_id = :workspace "
                        "AND asset_version_id = :asset_version_id "
                        "AND role = 'ORIGINAL'"
                    ),
                    {
                        "asset_version_id": asset_version_id,
                        "now": now,
                        "workspace": WORKSPACE_ID,
                    },
                )
        return temporary


class DurableAttemptAssertingAnalyzer:
    def __init__(self, database, delegate) -> None:
        self._database = database
        self._delegate = delegate
        self.observed_persisted_attempt = False

    @property
    def configured_identity(self):
        return self._delegate.configured_identity

    def analyze(
        self,
        request: VisionAnalysisRequest,
        *,
        lifecycle: VisionCallLifecycle | None = None,
    ) -> VisionProviderOutcome:
        assert lifecycle is not None
        return self._delegate.analyze(
            request,
            lifecycle=_DurableAttemptCheckingLifecycle(
                owner=self,
                delegate=lifecycle,
                request=request,
            ),
        )

    def assert_attempt(self, request: VisionAnalysisRequest, call_index: int) -> None:
        assert not is_unit_of_work_active()
        with SqlAlchemyOperationUnitOfWork(self._database.session_factory) as uow:
            operation = uow.operations.get(
                request.operation_id,
                workspace_id=WORKSPACE_ID,
            )
        assert operation is not None
        assert operation.state == OperationState.RUNNING
        assert operation.attempt_count == request.operation_attempt
        with self._database.engine.connect() as connection:
            attempt = (
                connection.execute(
                    text(
                        "SELECT submission_key_sha256, input_sha256 "
                        "FROM product_brief_provider_attempts "
                        "WHERE workspace_id = :workspace "
                        "AND operation_id = :operation_id "
                        "AND operation_attempt = :operation_attempt "
                        "AND call_index = :call_index"
                    ),
                    {
                        "call_index": call_index,
                        "operation_attempt": request.operation_attempt,
                        "operation_id": request.operation_id,
                        "workspace": WORKSPACE_ID,
                    },
                )
                .mappings()
                .one()
            )
        assert (
            attempt["submission_key_sha256"]
            == hashlib.sha256(f"durable-operation:{request.operation_id}".encode()).hexdigest()
        )
        assert attempt["input_sha256"] == operation.input_hash
        self.observed_persisted_attempt = True


class _DurableAttemptCheckingLifecycle:
    def __init__(
        self,
        *,
        owner: DurableAttemptAssertingAnalyzer,
        delegate: VisionCallLifecycle,
        request: VisionAnalysisRequest,
    ) -> None:
        self._owner = owner
        self._delegate = delegate
        self._request = request

    def store_artifact(
        self,
        artifact: ProviderArtifactWrite,
    ) -> ProviderArtifactReference | None:
        return self._delegate.store_artifact(artifact)

    def before_submission(self, call_index: int) -> None:
        self._delegate.before_submission(call_index)
        self._owner.assert_attempt(self._request, call_index)

    def persist_completed_call(self, call: VisionProviderCall) -> None:
        self._delegate.persist_completed_call(call)


class UnknownAfterProviderSuccessAnalyzer:
    def __init__(self, database, delegate) -> None:
        self._asserting = DurableAttemptAssertingAnalyzer(database, delegate)
        self.call_count = 0

    @property
    def configured_identity(self):
        return self._asserting.configured_identity

    def analyze(
        self,
        request: VisionAnalysisRequest,
        *,
        lifecycle: VisionCallLifecycle | None = None,
    ) -> VisionProviderOutcome:
        self.call_count += 1
        outcome = self._asserting.analyze(request, lifecycle=lifecycle)
        raise UnknownOperationOutcome(
            NormalizedOperationError(
                code="VISION_RESPONSE_PERSISTENCE_INTERRUPTED",
                category="worker_interruption",
                message="worker stopped after provider success and before outcome persistence",
                retryable=True,
                provider_request_id=outcome.request_id,
            )
        )


class MismatchedIdentityAnalyzer:
    def __init__(self, delegate) -> None:
        self._delegate = delegate
        self.called = False

    @property
    def configured_identity(self) -> VisionAnalyzerIdentity:
        return self._delegate.configured_identity.model_copy(
            update={"endpoint_host": "unapproved-provider.invalid"}
        )

    def analyze(
        self,
        request: VisionAnalysisRequest,
        *,
        lifecycle: VisionCallLifecycle | None = None,
    ) -> VisionProviderOutcome:
        self.called = True
        return self._delegate.analyze(request, lifecycle=lifecycle)


class SequentialAnalyzer:
    def __init__(self, *delegates) -> None:
        if not delegates:
            raise ValueError("at least one Vision analyzer is required")
        self._delegates = delegates
        self._next = 0

    @property
    def configured_identity(self) -> VisionAnalyzerIdentity:
        return self._delegates[0].configured_identity

    def analyze(
        self,
        request: VisionAnalysisRequest,
        *,
        lifecycle: VisionCallLifecycle | None = None,
    ) -> VisionProviderOutcome:
        delegate = self._delegates[self._next]
        self._next += 1
        return delegate.analyze(request, lifecycle=lifecycle)


class CountingAnalyzer:
    def __init__(self, delegate) -> None:
        self._delegate = delegate
        self.call_count = 0

    @property
    def configured_identity(self) -> VisionAnalyzerIdentity:
        return self._delegate.configured_identity

    def analyze(
        self,
        request: VisionAnalysisRequest,
        *,
        lifecycle: VisionCallLifecycle | None = None,
    ) -> VisionProviderOutcome:
        self.call_count += 1
        return self._delegate.analyze(request, lifecycle=lifecycle)


class SuccessfulOutcomeWithoutResponseArtifactAnalyzer:
    """Bypass contract construction to simulate a compromised provider adapter."""

    def __init__(self, delegate) -> None:
        self._delegate = delegate

    @property
    def configured_identity(self) -> VisionAnalyzerIdentity:
        return self._delegate.configured_identity

    def analyze(
        self,
        request: VisionAnalysisRequest,
        *,
        lifecycle: VisionCallLifecycle | None = None,
    ) -> VisionProviderOutcome:
        assert lifecycle is not None
        request_payload = b'{"request":"malicious-success-without-response"}'
        request_artifact = lifecycle.store_artifact(
            ProviderArtifactWrite(
                operation_id=request.operation_id,
                operation_attempt=request.operation_attempt,
                call_index=0,
                kind=ProviderArtifactKind.REQUEST,
                content_type="application/json",
                payload=request_payload,
                sha256=hashlib.sha256(request_payload).hexdigest(),
                retention_class=request.retention_class,
                retention_deadline=request.retention_deadline,
            )
        )
        assert request_artifact is not None
        lifecycle.before_submission(0)
        valid = self._delegate.analyze(request)
        forged_call = valid.calls[-1].model_copy(
            update={
                "request_artifact": request_artifact,
                "response_artifact": None,
            }
        )
        return valid.model_copy(
            update={
                "request_artifact": request_artifact,
                "response_artifact": None,
                "calls": (*valid.calls[:-1], forged_call),
            }
        )


class ResponseArtifactAwareAnalyzer:
    def __init__(self, identity: VisionAnalyzerIdentity) -> None:
        self._identity = identity
        self.call_count = 0
        self.submission_count = 0

    @property
    def configured_identity(self) -> VisionAnalyzerIdentity:
        return self._identity

    def analyze(
        self,
        request: VisionAnalysisRequest,
        *,
        lifecycle: VisionCallLifecycle | None = None,
    ) -> VisionProviderOutcome:
        assert lifecycle is not None
        self.call_count += 1
        request_artifact = self._store(
            request=request,
            lifecycle=lifecycle,
            kind=ProviderArtifactKind.REQUEST,
            payload=b'{"request":"provider-dispatch"}',
        )
        lifecycle.before_submission(0)
        self.submission_count += 1
        try:
            self._store(
                request=request,
                lifecycle=lifecycle,
                kind=ProviderArtifactKind.RESPONSE,
                payload=b'{"response":"received"}',
            )
        except ProviderArtifactPersistenceError:
            error = VisionProviderError(
                code="PROVIDER_RESPONSE_ARTIFACT_OUTCOME_UNKNOWN",
                category="unknown_outcome",
                message=(
                    "Vision provider responded but its response artifact could not be persisted"
                ),
                retryable=False,
            )
            usage = VisionProviderUsage(input_tokens=0, output_tokens=0, total_tokens=0)
            call = VisionProviderCall(
                call_index=0,
                status=VisionProviderStatus.UNKNOWN,
                provider=self._identity.provider,
                endpoint_region=self._identity.endpoint_region,
                endpoint_host=self._identity.endpoint_host,
                requested_model=self._identity.requested_model,
                submitted_model_snapshot=self._identity.submitted_model_snapshot,
                resolved_model=None,
                prompt_version=self._identity.prompt_version,
                config_snapshot_sha256=self._identity.configuration_snapshot_sha256,
                request_id="provider-request-response-received",
                usage=usage,
                latency_ms=25,
                request_artifact=request_artifact,
                response_artifact=None,
                error=error,
            )
            return VisionProviderOutcome(
                status=VisionProviderStatus.UNKNOWN,
                provider=self._identity.provider,
                endpoint_region=self._identity.endpoint_region,
                endpoint_host=self._identity.endpoint_host,
                requested_model=self._identity.requested_model,
                submitted_model_snapshot=self._identity.submitted_model_snapshot,
                resolved_model=None,
                prompt_version=self._identity.prompt_version,
                config_snapshot_sha256=self._identity.configuration_snapshot_sha256,
                request_id=call.request_id,
                usage=usage,
                latency_ms=call.latency_ms,
                request_artifact=request_artifact,
                response_artifact=None,
                output=None,
                error=error,
                calls=(call,),
            )
        raise AssertionError("response artifact failure was not raised")

    @staticmethod
    def _store(
        *,
        request: VisionAnalysisRequest,
        lifecycle: VisionCallLifecycle,
        kind: ProviderArtifactKind,
        payload: bytes,
    ) -> ProviderArtifactReference:
        return lifecycle.store_artifact(
            ProviderArtifactWrite(
                operation_id=request.operation_id,
                operation_attempt=request.operation_attempt,
                call_index=0,
                kind=kind,
                content_type="application/json",
                payload=payload,
                sha256=hashlib.sha256(payload).hexdigest(),
                retention_class=request.retention_class,
                retention_deadline=request.retention_deadline,
            )
        )


class SignalingAnalyzer(CountingAnalyzer):
    def __init__(self, delegate) -> None:
        super().__init__(delegate)
        self.called = threading.Event()

    def analyze(
        self,
        request: VisionAnalysisRequest,
        *,
        lifecycle: VisionCallLifecycle | None = None,
    ) -> VisionProviderOutcome:
        assert lifecycle is not None
        self.call_count += 1
        return self._delegate.analyze(
            request,
            lifecycle=_SubmissionSignalingLifecycle(
                delegate=lifecycle,
                submitted=self.called,
            ),
        )


class BlockingAfterProviderAnalyzer(CountingAnalyzer):
    def __init__(self, delegate) -> None:
        super().__init__(delegate)
        self.provider_completed = threading.Event()
        self.release = threading.Event()

    def analyze(
        self,
        request: VisionAnalysisRequest,
        *,
        lifecycle: VisionCallLifecycle | None = None,
    ) -> VisionProviderOutcome:
        outcome = super().analyze(request, lifecycle=lifecycle)
        self.provider_completed.set()
        if not self.release.wait(10):
            raise TimeoutError("timed out waiting to release the provider result")
        return outcome


class BlockingBeforeSubmissionAnalyzer(CountingAnalyzer):
    def __init__(self, delegate) -> None:
        super().__init__(delegate)
        self.entered = threading.Event()
        self.release = threading.Event()
        self.submitted = threading.Event()

    def analyze(
        self,
        request: VisionAnalysisRequest,
        *,
        lifecycle: VisionCallLifecycle | None = None,
    ) -> VisionProviderOutcome:
        assert lifecycle is not None
        self.call_count += 1
        self.entered.set()
        if not self.release.wait(10):
            raise TimeoutError("timed out waiting to approach provider submission")
        return self._delegate.analyze(
            request,
            lifecycle=_SubmissionSignalingLifecycle(
                delegate=lifecycle,
                submitted=self.submitted,
            ),
        )


class BlockingBeforeProviderAnalyzer(CountingAnalyzer):
    def __init__(self, delegate) -> None:
        super().__init__(delegate)
        self.entered = threading.Event()
        self.release = threading.Event()

    def analyze(
        self,
        request: VisionAnalysisRequest,
        *,
        lifecycle: VisionCallLifecycle | None = None,
    ) -> VisionProviderOutcome:
        assert lifecycle is not None
        self.call_count += 1
        return self._delegate.analyze(
            request,
            lifecycle=_BlockingSubmissionLifecycle(
                delegate=lifecycle,
                entered=self.entered,
                release=self.release,
            ),
        )


class MutatingAfterProviderAnalyzer(CountingAnalyzer):
    def __init__(
        self,
        delegate,
        mutate: Callable[[VisionAnalysisRequest], None],
    ) -> None:
        super().__init__(delegate)
        self._mutate = mutate

    def analyze(
        self,
        request: VisionAnalysisRequest,
        *,
        lifecycle: VisionCallLifecycle | None = None,
    ) -> VisionProviderOutcome:
        outcome = super().analyze(request, lifecycle=lifecycle)
        self._mutate(request)
        return outcome


class PersistedUnknownAnalyzer(CountingAnalyzer):
    def __init__(self, delegate, *, retryable: bool = False) -> None:
        super().__init__(delegate)
        self._retryable = retryable

    def analyze(
        self,
        request: VisionAnalysisRequest,
        *,
        lifecycle: VisionCallLifecycle | None = None,
    ) -> VisionProviderOutcome:
        self.call_count += 1
        outcome = self._delegate.analyze(request, lifecycle=lifecycle)
        error = VisionProviderError(
            code="PROVIDER_SUBMISSION_OUTCOME_UNKNOWN",
            category="unknown_outcome",
            message="Vision provider submission outcome is unknown",
            retryable=self._retryable,
        )
        call = outcome.calls[-1].model_copy(
            update={
                "status": VisionProviderStatus.UNKNOWN,
                "error": error,
            }
        )
        return outcome.model_copy(
            update={
                "status": VisionProviderStatus.UNKNOWN,
                "error": error,
                "calls": (call,),
            }
        )


class _SubmissionSignalingLifecycle:
    def __init__(
        self,
        *,
        delegate: VisionCallLifecycle,
        submitted: threading.Event,
    ) -> None:
        self._delegate = delegate
        self._submitted = submitted

    def store_artifact(
        self,
        artifact: ProviderArtifactWrite,
    ) -> ProviderArtifactReference | None:
        return self._delegate.store_artifact(artifact)

    def before_submission(self, call_index: int) -> None:
        self._delegate.before_submission(call_index)
        self._submitted.set()

    def persist_completed_call(self, call: VisionProviderCall) -> None:
        self._delegate.persist_completed_call(call)


class _BlockingSubmissionLifecycle:
    def __init__(
        self,
        *,
        delegate: VisionCallLifecycle,
        entered: threading.Event,
        release: threading.Event,
    ) -> None:
        self._delegate = delegate
        self._entered = entered
        self._release = release

    def store_artifact(
        self,
        artifact: ProviderArtifactWrite,
    ) -> ProviderArtifactReference | None:
        return self._delegate.store_artifact(artifact)

    def before_submission(self, call_index: int) -> None:
        self._delegate.before_submission(call_index)
        self._entered.set()
        if not self._release.wait(10):
            raise TimeoutError("timed out waiting to submit the provider request")

    def persist_completed_call(self, call: VisionProviderCall) -> None:
        self._delegate.persist_completed_call(call)


class _ArtifactOnlyLifecycle:
    def __init__(self, delegate: VisionCallLifecycle, *, call_index: int) -> None:
        self._delegate = delegate
        self._call_index = call_index

    def store_artifact(
        self,
        artifact: ProviderArtifactWrite,
    ) -> ProviderArtifactReference | None:
        return self._delegate.store_artifact(
            artifact.model_copy(update={"call_index": self._call_index})
        )

    def before_submission(self, call_index: int) -> None:
        del call_index

    def persist_completed_call(self, call: VisionProviderCall) -> None:
        del call


class RepairLifecycleAnalyzer:
    def __init__(
        self,
        artifact_sink: MemoryArtifactSink,
        *,
        block_after_repair_intent: bool = False,
        crash_after_repair_intent: bool = False,
    ) -> None:
        self._malformed = DeterministicVisionAnalyzer(
            scenario=DeterministicVisionScenario.MALFORMED,
            artifact_sink=artifact_sink,
        )
        self._success = DeterministicVisionAnalyzer(
            scenario=DeterministicVisionScenario.SUCCESS,
            artifact_sink=artifact_sink,
        )
        self._block_after_repair_intent = block_after_repair_intent
        self._crash_after_repair_intent = crash_after_repair_intent
        self.repair_intent_persisted = threading.Event()
        self.release_repair = threading.Event()

    @property
    def configured_identity(self) -> VisionAnalyzerIdentity:
        return self._success.configured_identity

    def analyze(
        self,
        request: VisionAnalysisRequest,
        *,
        lifecycle: VisionCallLifecycle | None = None,
    ) -> VisionProviderOutcome:
        assert lifecycle is not None
        lifecycle.before_submission(0)
        malformed = self._malformed.analyze(
            request,
            lifecycle=_ArtifactOnlyLifecycle(lifecycle, call_index=0),
        )
        first_call = malformed.calls[0]
        lifecycle.persist_completed_call(first_call)
        lifecycle.before_submission(1)
        self.repair_intent_persisted.set()
        if self._block_after_repair_intent and not self.release_repair.wait(10):
            raise TimeoutError("timed out waiting to release the repair request")
        if self._crash_after_repair_intent:
            raise UnknownOperationOutcome(
                NormalizedOperationError(
                    code="VISION_REPAIR_RESPONSE_INTERRUPTED",
                    category="worker_interruption",
                    message="worker stopped after repair submission",
                    retryable=False,
                )
            )
        success = self._success.analyze(
            request,
            lifecycle=_ArtifactOnlyLifecycle(lifecycle, call_index=1),
        )
        final_call = success.calls[0].model_copy(update={"call_index": 1})
        return success.model_copy(
            update={
                "calls": (first_call, final_call),
                "request_artifact": final_call.request_artifact,
                "response_artifact": final_call.response_artifact,
            }
        )


class _FailBeforeVersionCommitRepository:
    def __init__(self, delegate) -> None:
        self._delegate = delegate

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)

    def add_version(self, _version) -> None:
        raise RuntimeError("injected failure before ProductBrief version commit")


class FailBeforeVersionCommitUnitOfWork:
    def __init__(self, session_factory) -> None:
        self._delegate = SqlAlchemyProductBriefUnitOfWork(session_factory)

    def __enter__(self):
        self._delegate.__enter__()
        self.product_briefs = _FailBeforeVersionCommitRepository(self._delegate.product_briefs)
        return self

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._delegate.__exit__(exc_type, exc, traceback)


def _settings(integration_settings: Settings) -> Settings:
    return Settings(
        environment="ci",
        service_name="product-brief-integration",
        mysql_dsn=integration_settings.mysql_dsn,
        object_store_endpoint="http://127.0.0.1:19000",
        object_store_presign_endpoint="http://127.0.0.1:19000",
        object_store_access_key="commercevision",
        object_store_secret_key="commercevision-secret",
        worker_queues=["commercevision.asset"],
        worker_required_operation_kinds=[OperationKind.PRODUCT_BRIEF_ANALYSIS],
        trusted_principal_current_key_id=TRUSTED_PRINCIPAL_KEY_ID,
        trusted_principal_current_hmac_secret=TRUSTED_PRINCIPAL_SECRET,
        workflow_step_lease_seconds=30,
        workflow_message_max_attempts=3,
        vision_data_transfer_enabled=True,
        vision_data_transfer_policy_version="vision-transfer-test-v1",
        vision_data_transfer_allowed_workspace_ids=[
            WORKSPACE_ID,
            ISOLATED_WORKSPACE_ID,
        ],
        vision_data_transfer_allowed_retention_classes=["TASK"],
        vision_data_transfer_allowed_providers=["deterministic-vision"],
        vision_data_transfer_allowed_endpoint_regions=["local"],
        vision_data_transfer_allowed_endpoint_hosts=["deterministic.invalid"],
    )


def _seed_authorized_source(
    database,
    *,
    category_code: str = "beauty.skincare.serum",
    workspace_id: str = WORKSPACE_ID,
) -> tuple[str, str, str]:
    workflow_id = new_uuid7()
    product_id = new_uuid7()
    asset_id = new_uuid7()
    asset_version_id = new_uuid7()
    rights_record_id = new_uuid7()
    now = datetime.now(UTC).replace(tzinfo=None)
    expires_at = now + timedelta(hours=72)
    with database.engine.begin() as connection:
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        connection.execute(
            text(
                "INSERT INTO workflows "
                "(id, workspace_id, created_by, workflow_type, status, "
                "retention_status, current_node, version, input_json, result_json, "
                "expires_at, cancellation_requested_at, created_at, updated_at) VALUES "
                "(:id, :workspace, 'brief-reviewer', 'COMMERCE_IMAGE_GENERATION', "
                "'UNDERSTANDING', 'ACTIVE', 'understand_product', 3, "
                "JSON_OBJECT('schema_version', '1.0', 'product_id', :product_id), "
                "NULL, :expires_at, NULL, :now, :now)"
            ),
            {
                "id": workflow_id,
                "product_id": product_id,
                "workspace": workspace_id,
                "expires_at": expires_at,
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO products "
                "(id, workspace_id, source_namespace, external_id, source_version, "
                "title, category_code, brand, attributes_json, expires_at, version, "
                "created_at, updated_at) VALUES "
                "(:id, :workspace, 'MANUAL', :external_id, 'manual-v1', "
                "'Hydrating Serum', :category_code, 'Northstar Labs', "
                "JSON_OBJECT('volume_ml', 30), NULL, 1, :now, :now)"
            ),
            {
                "id": product_id,
                "workspace": workspace_id,
                "external_id": f"BEAUTY-{product_id[-12:]}",
                "category_code": category_code,
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO assets "
                "(id, workspace_id, retention_class, asset_kind, workflow_id, "
                "product_id, sku_id, status, block_reason, current_version_id, "
                "current_rights_record_id, retention_deadline, version, created_at, "
                "updated_at) VALUES "
                "(:id, :workspace, 'TASK', 'IMAGE', :workflow_id, :product_id, NULL, "
                "'AVAILABLE', NULL, :asset_version_id, :rights_record_id, "
                ":expires_at, 4, :now, :now)"
            ),
            {
                "id": asset_id,
                "workspace": workspace_id,
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_id": asset_version_id,
                "rights_record_id": rights_record_id,
                "expires_at": expires_at,
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO asset_versions "
                "(id, workspace_id, asset_id, version_number, upload_session_id, "
                "filename, sha256, byte_size, declared_mime, detected_mime, "
                "image_format, width, height, frame_count, category, role, "
                "integrity_policy_version, validation_policy_version, "
                "validation_transfer_policy_version, "
                "validation_transfer_policy_snapshot_sha256, created_at) VALUES "
                "(:id, :workspace, :asset_id, 1, :upload_id, 'serum.png', :sha, "
                "1024, 'image/png', 'image/png', 'PNG', 512, 512, 1, 'beauty', "
                "'product', 'integrity-v1', 'validation-v1', 'validation-transfer-v1', "
                ":transfer_sha, :now)"
            ),
            {
                "id": asset_version_id,
                "workspace": workspace_id,
                "asset_id": asset_id,
                "upload_id": new_uuid7(),
                "sha": "a" * 64,
                "transfer_sha": "b" * 64,
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO rights_records "
                "(id, workspace_id, asset_id, asset_version_id, version_number, "
                "decision, owner_reference, source, license_reference, "
                "derivative_allowed, public_demo_allowed, evidence_reference, "
                "terms_sha256, valid_from, valid_until, perpetual, "
                "supersedes_record_id, created_by, created_at, "
                "permissions_sealed_at) VALUES "
                "(:id, :workspace, :asset_id, :asset_version_id, 1, 'GRANT', "
                "'Northstar Labs', 'brand-library', 'license://northstar/2026', "
                "0, 0, 'evidence://rights/beauty-001', :terms_sha, :valid_from, "
                ":valid_until, 0, NULL, 'rights-admin', :now, NULL)"
            ),
            {
                "id": rights_record_id,
                "workspace": workspace_id,
                "asset_id": asset_id,
                "asset_version_id": asset_version_id,
                "terms_sha": "c" * 64,
                "valid_from": now - timedelta(minutes=1),
                "valid_until": expires_at,
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO rights_record_uses "
                "(workspace_id, asset_id, rights_record_id, allowed_use, created_at) "
                "VALUES (:workspace, :asset_id, :rights_id, 'VISION_ANALYSIS', :now)"
            ),
            {
                "workspace": workspace_id,
                "asset_id": asset_id,
                "rights_id": rights_record_id,
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO rights_record_providers "
                "(workspace_id, asset_id, rights_record_id, allowed_provider, "
                "created_at) VALUES "
                "(:workspace, :asset_id, :rights_id, 'deterministic-vision', :now)"
            ),
            {
                "workspace": workspace_id,
                "asset_id": asset_id,
                "rights_id": rights_record_id,
                "now": now,
            },
        )
        connection.execute(
            text("UPDATE rights_records SET permissions_sealed_at = :now WHERE id = :rights_id"),
            {"now": now, "rights_id": rights_record_id},
        )
        connection.execute(
            text(
                "INSERT INTO asset_objects "
                "(id, workspace_id, asset_version_id, role, backend, location, "
                "bucket, `key`, provider_version_id, etag, byte_size, sha256, state, "
                "version, created_at, updated_at) VALUES "
                "(:id, :workspace, :asset_version_id, 'ORIGINAL', 'MINIO', 'TASK', "
                "'task-assets', :key, 'controlled-version-1', 'controlled-etag-1', "
                "1024, :sha, 'CONTROLLED', 2, :now, :now)"
            ),
            {
                "id": new_uuid7(),
                "workspace": workspace_id,
                "asset_version_id": asset_version_id,
                "key": f"tasks/{workflow_id}/{asset_version_id}/serum.png",
                "sha": "a" * 64,
                "now": now,
            },
        )
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
    return workflow_id, product_id, asset_version_id


def test_http_product_brief_category_routing_is_deny_by_default(
    integration_database,
    integration_settings,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(
        integration_database,
        category_code="anti-beauty",
    )

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers("brief-unsupported-category-0001"),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": 3,
            },
        )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_ARGUMENT"


def test_product_brief_http_resources_are_binary_workspace_isolated(
    integration_database,
    integration_settings,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(
        integration_database,
        workspace_id=ISOLATED_WORKSPACE_ID,
    )
    app = create_app(settings)

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers(
                "brief-isolated-analysis-0001",
                workspace_id=ISOLATED_WORKSPACE_ID,
            ),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": 3,
            },
        )
        assert created.status_code == 202, created.text
        product_brief_id = created.json()["product_brief"]["id"]
        operation_id = created.json()["operation_id"]

        with integration_database.engine.connect() as connection:
            before = {
                "approvals": connection.scalar(text("SELECT COUNT(*) FROM workflow_approvals")),
                "idempotency": connection.scalar(text("SELECT COUNT(*) FROM idempotency_keys")),
                "outbox": connection.scalar(text("SELECT COUNT(*) FROM outbox_events")),
                "brief": tuple(
                    connection.execute(
                        text(
                            "SELECT state, version, current_version_id, confirmed_version_id "
                            "FROM product_briefs "
                            "WHERE workspace_id = :workspace AND id = :brief_id"
                        ),
                        {
                            "brief_id": product_brief_id,
                            "workspace": ISOLATED_WORKSPACE_ID,
                        },
                    ).one()
                ),
            }

        responses = (
            client.get(
                f"/api/v1/product-briefs/{product_brief_id}",
                headers=_read_headers(),
            ),
            client.get(
                f"/api/v1/product-briefs/{product_brief_id}/versions",
                headers=_read_headers(),
            ),
            client.get(
                f"/api/v1/product-briefs/workflow-context/{workflow_id}"
                f"?product_brief_id={product_brief_id}",
                headers=_read_headers(),
            ),
            client.get(
                f"/api/v1/product-briefs/{product_brief_id}/operations/{operation_id}",
                headers=_read_headers(),
            ),
            client.post(
                f"/api/v1/product-briefs/{product_brief_id}:revise",
                headers=_mutation_headers("brief-isolated-revision-denied-0001"),
                json={
                    "expected_product_brief_version": 1,
                    "base_version_id": new_uuid7(),
                    "reason": "Cross-workspace revision must not resolve the resource",
                    "fields": [
                        {
                            "path": "common.brand",
                            "value": {"kind": "TEXT", "text": "Denied"},
                            "confidence": 1,
                            "conflict": "RESOLVED",
                            "review_required": False,
                            "sensitive": False,
                            "evidence": [
                                {
                                    "source_asset_version_id": asset_version_id,
                                    "kind": "IMAGE_REGION",
                                    "reference": f"asset-region://{'d' * 64}",
                                    "region": [0.1, 0.1, 0.9, 0.9],
                                    "excerpt_sha256": "a" * 64,
                                }
                            ],
                        }
                    ],
                },
            ),
            client.post(
                f"/api/v1/product-briefs/{product_brief_id}:confirm",
                headers=_mutation_headers("brief-isolated-confirmation-denied-0001"),
                json={
                    "expected_product_brief_version": 1,
                    "product_brief_version_id": new_uuid7(),
                    "expected_workflow_version": 3,
                    "reason_code": "CROSS_WORKSPACE_DENIED",
                },
            ),
        )

    assert [response.status_code for response in responses] == [404] * len(responses)
    with integration_database.engine.connect() as connection:
        after = {
            "approvals": connection.scalar(text("SELECT COUNT(*) FROM workflow_approvals")),
            "idempotency": connection.scalar(text("SELECT COUNT(*) FROM idempotency_keys")),
            "outbox": connection.scalar(text("SELECT COUNT(*) FROM outbox_events")),
            "brief": tuple(
                connection.execute(
                    text(
                        "SELECT state, version, current_version_id, confirmed_version_id "
                        "FROM product_briefs "
                        "WHERE workspace_id = :workspace AND id = :brief_id"
                    ),
                    {
                        "brief_id": product_brief_id,
                        "workspace": ISOLATED_WORKSPACE_ID,
                    },
                ).one()
            ),
        }
    assert after == before


def _mutation_headers(
    idempotency_key: str,
    *,
    workspace_id: str = WORKSPACE_ID,
    actor_id: str = "brief-reviewer",
) -> dict[str, str]:
    return {
        **_read_headers(workspace_id=workspace_id, actor_id=actor_id),
        "X-Actor-Id": actor_id,
        "Idempotency-Key": idempotency_key,
    }


def _read_headers(
    *,
    workspace_id: str = WORKSPACE_ID,
    actor_id: str = "brief-reviewer",
) -> dict[str, str]:
    claims = {
        "actor_id": actor_id,
        "workspace_ids": [workspace_id],
        "admin_workspace_ids": [],
        "system_admin": False,
        "issued_at": int(datetime.now(UTC).timestamp()),
    }
    encoded = (
        base64.urlsafe_b64encode(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )
    signature = hmac.new(
        TRUSTED_PRINCIPAL_SECRET.encode(),
        f"{TRUSTED_PRINCIPAL_KEY_ID}.{encoded}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-Workspace-Id": workspace_id,
        "X-Trusted-Principal": (f"{TRUSTED_PRINCIPAL_KEY_ID}.{encoded}.{signature}"),
    }


def _human_revision_fields(version: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "path": field["path"],
            "value": (
                {**field["value"], "display_name": "Human verified product"}
                if field["path"] == "common.identity"
                else field["value"]
            ),
            "confidence": field["confidence"],
            "conflict": field["conflict"],
            "review_required": field["review_required"],
            "sensitive": field["sensitive"],
            "evidence": [
                {
                    "source_asset_version_id": evidence["source_asset_version_id"],
                    "kind": evidence["kind"],
                    "reference": evidence["reference"],
                    "region": evidence["region"],
                    "excerpt_sha256": evidence["excerpt_sha256"],
                }
                for evidence in field["evidence"]
            ],
        }
        for field in version["fields"]
    ]


def test_legacy_overlong_workflow_caps_product_brief_and_provider_artifacts(
    integration_database,
    integration_settings,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(integration_database)
    with integration_database.engine.begin() as connection:
        workflow_created_at = connection.scalar(
            text(
                "SELECT created_at FROM workflows "
                "WHERE workspace_id = :workspace AND id = :workflow_id"
            ),
            {
                "workspace": WORKSPACE_ID,
                "workflow_id": workflow_id,
            },
        )
        assert isinstance(workflow_created_at, datetime)
        canonical_deadline = workflow_created_at + timedelta(hours=72)
        connection.execute(
            text(
                "UPDATE workflows SET expires_at = :legacy_deadline "
                "WHERE workspace_id = :workspace AND id = :workflow_id"
            ),
            {
                "legacy_deadline": workflow_created_at + timedelta(hours=168),
                "workspace": WORKSPACE_ID,
                "workflow_id": workflow_id,
            },
        )

    artifact_sink = MemoryArtifactSink()
    executor = ProductBriefAnalysisExecutor(
        uow_factory=lambda: SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory),
        object_storage=ControlledReadStorage(),
        analyzer=DeterministicVisionAnalyzer(
            scenario=DeterministicVisionScenario.SUCCESS,
            artifact_sink=artifact_sink,
        ),
        policy=ProductBriefPolicy.from_settings(settings),
        transfer_policy=VisionDataTransferPolicy.from_settings(settings),
        artifact_service=_artifact_service(integration_database, artifact_sink),
    )
    with TestClient(create_app(settings)) as client:
        requested = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers("legacy-overlong-retention-0001"),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": 3,
            },
        )
        assert requested.status_code == 202, requested.text
        body = requested.json()
        product_brief_id = body["product_brief"]["id"]
        response_deadline = datetime.fromisoformat(
            body["product_brief"]["retention_deadline"].replace("Z", "+00:00")
        ).replace(tzinfo=None)
        with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
            requested_event = next(
                event
                for event in uow.outbox.list_for_aggregate(product_brief_id)
                if event.envelope.event_type == "product-brief.requested"
            )
        worker = WorkerRuntime.build(
            settings,
            operation_executors={
                OperationKind.PRODUCT_BRIEF_ANALYSIS: executor,
            },
        )
        try:
            assert worker.process_event(requested_event.envelope.event_id) == "processed"
        finally:
            worker.close()

    with integration_database.engine.connect() as connection:
        product_brief_deadline = connection.scalar(
            text(
                "SELECT retention_deadline FROM product_briefs "
                "WHERE workspace_id = :workspace AND id = :product_brief_id"
            ),
            {
                "workspace": WORKSPACE_ID,
                "product_brief_id": product_brief_id,
            },
        )
        analysis_deadline = connection.scalar(
            text(
                "SELECT retention_deadline FROM product_brief_analysis_requests "
                "WHERE workspace_id = :workspace AND product_brief_id = :product_brief_id"
            ),
            {
                "workspace": WORKSPACE_ID,
                "product_brief_id": product_brief_id,
            },
        )
        artifact_rows = connection.execute(
            text(
                "SELECT kind, state, retention_deadline "
                "FROM product_brief_provider_artifacts "
                "WHERE workspace_id = :workspace AND product_brief_id = :product_brief_id "
                "ORDER BY kind"
            ),
            {
                "workspace": WORKSPACE_ID,
                "product_brief_id": product_brief_id,
            },
        ).all()
        provider_call_deadline = connection.scalar(
            text(
                "SELECT retention_deadline FROM product_brief_provider_calls "
                "WHERE workspace_id = :workspace AND product_brief_id = :product_brief_id"
            ),
            {
                "workspace": WORKSPACE_ID,
                "product_brief_id": product_brief_id,
            },
        )

    assert response_deadline == canonical_deadline
    assert product_brief_deadline == canonical_deadline
    assert analysis_deadline == canonical_deadline
    assert [(row.kind, row.state) for row in artifact_rows] == [
        ("REQUEST", "STORED"),
        ("RESPONSE", "STORED"),
    ]
    assert {row.retention_deadline for row in artifact_rows} == {canonical_deadline}
    assert provider_call_deadline == canonical_deadline
    assert len(artifact_sink.artifacts) == 2
    assert {
        artifact.retention_deadline.replace(tzinfo=None) for artifact in artifact_sink.artifacts
    } == {canonical_deadline}


def test_http_worker_human_revision_and_exact_confirmation(
    integration_database,
    integration_settings,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(integration_database)
    cross_workflow_id, cross_product_id, cross_asset_version_id = _seed_authorized_source(
        integration_database,
        workspace_id=ISOLATED_WORKSPACE_ID,
    )
    artifact_sink = MemoryArtifactSink()
    analyzer = DurableAttemptAssertingAnalyzer(
        integration_database,
        DeterministicVisionAnalyzer(
            scenario=DeterministicVisionScenario.LOW_CONFIDENCE,
            artifact_sink=artifact_sink,
        ),
    )
    executor = ProductBriefAnalysisExecutor(
        uow_factory=lambda: SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory),
        object_storage=ControlledReadStorage(),
        analyzer=analyzer,
        policy=ProductBriefPolicy.from_settings(settings),
        transfer_policy=VisionDataTransferPolicy.from_settings(settings),
        artifact_service=_artifact_service(integration_database, artifact_sink),
    )
    app = create_app(settings)

    with TestClient(app) as client:
        api_engine = app.state.container.database.engine
        requested = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers("brief-analysis-0001"),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": 3,
            },
        )
        assert requested.status_code == 202, requested.text
        product_brief = requested.json()["product_brief"]
        operation_id = requested.json()["operation_id"]
        cross_requested = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers(
                "brief-analysis-cross-brief-0001",
                workspace_id=ISOLATED_WORKSPACE_ID,
            ),
            json={
                "workflow_id": cross_workflow_id,
                "product_id": cross_product_id,
                "asset_version_ids": [cross_asset_version_id],
                "expected_workflow_version": 3,
            },
        )
        assert cross_requested.status_code == 202, cross_requested.text
        cross_product_brief_id = cross_requested.json()["product_brief"]["id"]
        unauthenticated = client.get(
            f"/api/v1/product-briefs/{product_brief['id']}",
            headers={"X-Workspace-Id": WORKSPACE_ID},
        )
        assert unauthenticated.status_code == 401

        with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
            events = uow.outbox.list_for_aggregate(product_brief["id"])
        requested_event = next(
            event for event in events if event.envelope.event_type == "product-brief.requested"
        )
        worker = WorkerRuntime.build(
            settings,
            operation_executors={
                OperationKind.PRODUCT_BRIEF_ANALYSIS: executor,
            },
        )
        try:
            processing_result = worker.process_event(requested_event.envelope.event_id)
            with integration_database.engine.connect() as connection:
                processing_error = connection.scalar(
                    text("SELECT error_message FROM inbox_messages WHERE message_id = :message_id"),
                    {"message_id": requested_event.envelope.event_id},
                )
            assert processing_result == "processed", processing_error
            assert worker.process_event(requested_event.envelope.event_id) == "duplicate"
        finally:
            worker.close()

        awaiting = client.get(
            f"/api/v1/product-briefs/{product_brief['id']}",
            headers=_read_headers(),
        )
        operation = client.get(
            f"/api/v1/operations/{operation_id}",
            headers=_read_headers(),
        )
        assert awaiting.status_code == 200
        assert awaiting.json()["state"] == "AWAITING_CONFIRMATION", operation.text
        assert operation.status_code == 200, operation.text
        assert operation.json()["state"] == "WAITING_HUMAN"
        model_version = awaiting.json()["current_version"]
        assert model_version["source"] == "MODEL"
        assert model_version["provider_call"]["provider"] == "deterministic-vision"
        assert model_version["provider_call"]["resolved_model"] == ("deterministic-vision-v1")
        assert "request_artifact_ref" not in model_version["provider_call"]
        assert "response_artifact_ref" not in model_version["provider_call"]
        provider_supplied_reference = (
            "asset-region://"
            + hashlib.sha256(
                f"{asset_version_id}:deterministic-provider-evidence".encode()
            ).hexdigest()
        )
        projected_references = {
            evidence["reference"]
            for field in model_version["fields"]
            for evidence in field["evidence"]
        }
        assert provider_supplied_reference not in projected_references
        assert all(
            reference.startswith("asset-region://")
            and len(reference.removeprefix("asset-region://")) == 64
            and all(
                character in "0123456789abcdef"
                for character in reference.removeprefix("asset-region://")
            )
            for reference in projected_references
        )

        revised_fields = [
            {
                "path": field["path"],
                "value": (
                    {**field["value"], "display_name": "Human verified serum"}
                    if field["path"] == "common.identity"
                    else field["value"]
                ),
                "confidence": field["confidence"],
                "conflict": field["conflict"],
                "review_required": field["review_required"],
                "sensitive": field["sensitive"],
                "evidence": [
                    {
                        "source_asset_version_id": evidence["source_asset_version_id"],
                        "kind": evidence["kind"],
                        "reference": evidence["reference"],
                        "region": evidence["region"],
                        "excerpt_sha256": evidence["excerpt_sha256"],
                    }
                    for evidence in field["evidence"]
                ],
            }
            for field in model_version["fields"]
        ]
        revision_request = {
            "expected_product_brief_version": awaiting.json()["version"],
            "base_version_id": model_version["id"],
            "reason": "Verified against the controlled source image",
            "fields": revised_fields,
        }
        revised = client.post(
            f"/api/v1/product-briefs/{product_brief['id']}:revise",
            headers=_mutation_headers("brief-revision-0001"),
            json=revision_request,
        )
        assert revised.status_code == 200, revised.text
        assert revised.json()["current_version"]["source"] == "HUMAN"
        assert revised.json()["current_version"]["supersedes_version_id"] == (model_version["id"])
        assert revised.json()["current_version"]["changed_field_paths"] == ["common.identity"]
        revised_sources = {
            field["path"]: field["source"] for field in revised.json()["current_version"]["fields"]
        }
        assert revised_sources["common.identity"] == "HUMAN"
        assert revised_sources["common.brand"] == "MODEL"

        captured_selects: list[str] = []

        def capture_selects(
            _connection,
            _cursor,
            statement,
            _parameters,
            _context,
            _executemany,
        ) -> None:
            if statement.lstrip().upper().startswith("SELECT"):
                captured_selects.append(" ".join(statement.lower().split()))

        event.listen(api_engine, "before_cursor_execute", capture_selects)
        try:
            history_page = client.get(
                f"/api/v1/product-briefs/{product_brief['id']}/versions?limit=2",
                headers=_read_headers(),
            )
        finally:
            event.remove(api_engine, "before_cursor_execute", capture_selects)

        assert history_page.status_code == 200, history_page.text
        assert [item["version_number"] for item in history_page.json()["items"]] == [2, 1]
        assert all("fields" not in item for item in history_page.json()["items"])
        assert history_page.json()["next_cursor"] is None
        assert len(captured_selects) <= 4
        assert sum("product_brief_versions" in statement for statement in captured_selects) == 1
        assert (
            sum("product_brief_provider_calls" in statement for statement in captured_selects) == 1
        )
        assert not any(
            table in statement
            for table in ("product_brief_fields", "product_brief_evidence")
            for statement in captured_selects
        )

        first_history_page = client.get(
            f"/api/v1/product-briefs/{product_brief['id']}/versions?limit=1",
            headers=_read_headers(),
        )
        assert first_history_page.status_code == 200, first_history_page.text
        assert [item["version_number"] for item in first_history_page.json()["items"]] == [2]
        assert first_history_page.json()["next_cursor"] == 2
        second_history_page = client.get(
            f"/api/v1/product-briefs/{product_brief['id']}/versions"
            f"?limit=1&cursor={first_history_page.json()['next_cursor']}",
            headers=_read_headers(),
        )
        assert second_history_page.status_code == 200, second_history_page.text
        assert [item["version_number"] for item in second_history_page.json()["items"]] == [1]
        assert second_history_page.json()["next_cursor"] is None

        stale_revision = client.post(
            f"/api/v1/product-briefs/{product_brief['id']}:revise",
            headers=_mutation_headers("brief-revision-stale-0001"),
            json={
                "expected_product_brief_version": awaiting.json()["version"],
                "base_version_id": model_version["id"],
                "reason": "Stale reviewer submission",
                "fields": revised_fields,
            },
        )
        assert stale_revision.status_code == 409
        assert stale_revision.json()["code"] == "VERSION_CONFLICT"

        workflow = client.get(
            f"/api/v1/workflows/{workflow_id}",
            headers={"X-Workspace-Id": WORKSPACE_ID},
        ).json()
        confirmation_request = {
            "expected_product_brief_version": revised.json()["version"],
            "product_brief_version_id": revised.json()["current_version_id"],
            "expected_workflow_version": workflow["version"],
            "reason_code": "HUMAN_VERIFIED",
            "comment_ref": "comment://product-brief/review-0001",
        }
        confirmed = client.post(
            f"/api/v1/product-briefs/{product_brief['id']}:confirm",
            headers=_mutation_headers("brief-confirmation-0001"),
            json=confirmation_request,
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["product_brief"]["state"] == "CONFIRMED"
        assert confirmed.json()["workflow_status"] == "RETRIEVING"
        product_brief_alias = product_brief["id"].upper()
        replayed_revision = client.post(
            f"/api/v1/product-briefs/{product_brief_alias}:revise",
            headers=_mutation_headers("brief-revision-0001"),
            json=revision_request,
        )
        replayed_confirmation = client.post(
            f"/api/v1/product-briefs/{product_brief_alias}:confirm",
            headers=_mutation_headers("brief-confirmation-0001"),
            json=confirmation_request,
        )
        assert replayed_revision.status_code == 200, replayed_revision.text
        assert replayed_revision.json() == revised.json()
        assert replayed_confirmation.status_code == 200, replayed_confirmation.text
        assert replayed_confirmation.json() == confirmed.json()
        alias_sql_parameters: list[object] = []

        def capture_alias_parameters(
            _connection,
            _cursor,
            statement,
            parameters,
            _context,
            _executemany,
        ) -> None:
            if "product_brief" in statement.lower():
                alias_sql_parameters.append(parameters)

        event.listen(
            api_engine,
            "before_cursor_execute",
            capture_alias_parameters,
        )
        try:
            alias_get = client.get(
                f"/api/v1/product-briefs/{product_brief_alias}",
                headers=_read_headers(),
            )
            alias_list = client.get(
                f"/api/v1/product-briefs/{product_brief_alias}/versions?limit=1",
                headers=_read_headers(),
            )
            alias_workflow_context = client.get(
                f"/api/v1/product-briefs/workflow-context/{workflow_id}"
                f"?product_brief_id={product_brief_alias}",
                headers=_read_headers(),
            )
        finally:
            event.remove(
                api_engine,
                "before_cursor_execute",
                capture_alias_parameters,
            )
        assert alias_get.status_code == 200, alias_get.text
        assert alias_list.status_code == 200, alias_list.text
        assert alias_workflow_context.status_code == 200, alias_workflow_context.text
        serialized_sql_parameters = repr(alias_sql_parameters)
        assert product_brief_alias not in serialized_sql_parameters
        assert product_brief["id"] in serialized_sql_parameters
        duplicate_confirmation = client.post(
            f"/api/v1/product-briefs/{product_brief['id']}:confirm",
            headers=_mutation_headers("brief-confirmation-distinct-key"),
            json={
                "expected_product_brief_version": revised.json()["version"],
                "product_brief_version_id": revised.json()["current_version_id"],
                "expected_workflow_version": workflow["version"],
                "reason_code": "HUMAN_VERIFIED",
                "comment_ref": "comment://product-brief/review-0001",
            },
        )
        assert duplicate_confirmation.status_code == 409
        assert duplicate_confirmation.json()["code"] == "VERSION_CONFLICT"
        completed_operation = client.get(
            f"/api/v1/operations/{operation_id}",
            headers=_read_headers(),
        )
        assert completed_operation.status_code == 200, completed_operation.text
        assert completed_operation.json()["state"] == "SUCCEEDED"

        confirmed_brief = confirmed.json()["product_brief"]
        confirmed_fields = confirmed_brief["current_version"]["fields"]
        later_fields = [
            {
                "path": field["path"],
                "value": (
                    {**field["value"], "text": "Northstar Labs verified"}
                    if field["path"] == "common.brand"
                    else field["value"]
                ),
                "confidence": field["confidence"],
                "conflict": field["conflict"],
                "review_required": field["review_required"],
                "sensitive": field["sensitive"],
                "evidence": [
                    {
                        "source_asset_version_id": evidence["source_asset_version_id"],
                        "kind": evidence["kind"],
                        "reference": evidence["reference"],
                        "region": evidence["region"],
                        "excerpt_sha256": evidence["excerpt_sha256"],
                    }
                    for evidence in field["evidence"]
                ],
            }
            for field in confirmed_fields
        ]
        later_revision = client.post(
            f"/api/v1/product-briefs/{product_brief['id']}:revise",
            headers=_mutation_headers("brief-revision-after-confirmation-0001"),
            json={
                "expected_product_brief_version": confirmed_brief["version"],
                "base_version_id": confirmed_brief["current_version_id"],
                "reason": "Brand value was verified after the first confirmation",
                "fields": later_fields,
            },
        )
        assert later_revision.status_code == 200, later_revision.text
        assert later_revision.json()["state"] == "AWAITING_CONFIRMATION"
        assert (
            later_revision.json()["confirmed_version_id"]
            == (confirmed_brief["confirmed_version_id"])
        )
        assert (
            later_revision.json()["current_version_id"] != (confirmed_brief["current_version_id"])
        )
        assert later_revision.json()["operation_id"] != operation_id
        reopened_operation = client.get(
            f"/api/v1/operations/{later_revision.json()['operation_id']}",
            headers=_read_headers(),
        )
        reopened_workflow = client.get(
            f"/api/v1/workflows/{workflow_id}",
            headers={"X-Workspace-Id": WORKSPACE_ID},
        )
        assert reopened_operation.status_code == 200, reopened_operation.text
        assert reopened_operation.json()["state"] == "WAITING_HUMAN"
        assert reopened_workflow.status_code == 200, reopened_workflow.text
        assert reopened_workflow.json()["status"] == "AWAITING_PRODUCT_CONFIRMATION"

        reconfirmed = client.post(
            f"/api/v1/product-briefs/{product_brief['id']}:confirm",
            headers=_mutation_headers("brief-confirmation-after-revision-0001"),
            json={
                "expected_product_brief_version": later_revision.json()["version"],
                "product_brief_version_id": later_revision.json()["current_version_id"],
                "expected_workflow_version": reopened_workflow.json()["version"],
                "reason_code": "HUMAN_REVERIFIED",
                "comment_ref": "comment://product-brief/review-0002",
            },
        )
        assert reconfirmed.status_code == 200, reconfirmed.text
        assert reconfirmed.json()["product_brief"]["state"] == "CONFIRMED"

        history = client.get(
            f"/api/v1/product-briefs/{product_brief['id']}/versions",
            headers=_read_headers(),
        )
        assert history.status_code == 200, history.text
        assert len(history.content) < 2 * 1024 * 1024
        assert all("fields" not in item for item in history.json()["items"])
        assert [item["source"] for item in history.json()["items"]] == [
            "HUMAN",
            "HUMAN",
            "MODEL",
        ]
        assert [item["effective_state"] for item in history.json()["items"]] == [
            "CONFIRMED",
            "ARCHIVED",
            "ARCHIVED",
        ]

    assert len(artifact_sink.artifacts) == 2
    assert analyzer.observed_persisted_attempt is True
    assert all(
        artifact.retention_deadline.isoformat().replace("+00:00", "Z")
        == product_brief["retention_deadline"]
        for artifact in artifact_sink.artifacts
    )
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        all_events = uow.outbox.list_for_aggregate(product_brief["id"])
    with SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory) as uow:
        provider_calls = uow.product_briefs.list_provider_calls(
            workspace_id=WORKSPACE_ID,
            operation_id=operation_id,
            operation_attempt=1,
        )
    assert provider_calls[0].request_artifact.storage_backend == StorageBackend.MINIO.value
    assert provider_calls[0].request_artifact.location.value == "PROVIDER_RESULT"
    assert provider_calls[0].request_artifact.bucket == "provider-results"
    assert provider_calls[0].request_artifact.provider_version_id.startswith("version-")
    assert provider_calls[0].request_artifact.etag
    assert len(provider_calls[0].request_artifact.sha256) == 64
    assert provider_calls[0].request_artifact.byte_size > 0
    assert provider_calls[0].response_artifact is not None
    assert provider_calls[0].response_artifact.retention_deadline == (
        provider_calls[0].retention_deadline
    )
    negative_confirmation_cases = (
        {
            "name": "wrong version number",
            "approval_type": "PRODUCT_BRIEF",
            "approval_decision": "APPROVE",
            "confirmation_product_brief_id": product_brief["id"],
            "subject_version": model_version["version_number"] + 100,
        },
        {
            "name": "wrong approval type",
            "approval_type": "ASSET",
            "approval_decision": "APPROVE",
            "confirmation_product_brief_id": product_brief["id"],
            "subject_version": model_version["version_number"],
        },
        {
            "name": "wrong approval decision",
            "approval_type": "PRODUCT_BRIEF",
            "approval_decision": "REJECT",
            "confirmation_product_brief_id": product_brief["id"],
            "subject_version": model_version["version_number"],
        },
        {
            "name": "cross ProductBrief version",
            "approval_type": "PRODUCT_BRIEF",
            "approval_decision": "APPROVE",
            "confirmation_product_brief_id": cross_product_brief_id,
            "subject_version": model_version["version_number"],
        },
    )
    for case in negative_confirmation_cases:
        approval_id = new_uuid7()
        with (
            pytest.raises(IntegrityError, match="foreign key constraint fails"),
            integration_database.engine.begin() as connection,
        ):
            connection.execute(
                text(
                    "INSERT INTO workflow_approvals "
                    "(id, workflow_id, approval_type, subject_id, subject_version, "
                    "decision, reason_code, comment_ref, approved_by, "
                    "expected_workflow_version, created_at) "
                    "VALUES (:id, :workflow_id, :approval_type, "
                    ":subject_id, :subject_version, :approval_decision, NULL, NULL, "
                    "'reviewer-mismatch', 999, UTC_TIMESTAMP(6))"
                ),
                {
                    "id": approval_id,
                    "approval_type": case["approval_type"],
                    "approval_decision": case["approval_decision"],
                    "subject_id": model_version["id"],
                    "subject_version": case["subject_version"],
                    "workflow_id": workflow_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO product_brief_confirmations "
                    "(id, workspace_id, product_brief_id, product_brief_version_id, "
                    "product_brief_version_number, workflow_id, operation_id, "
                    "approval_id, approval_type, approval_decision, confirmed_by, "
                    "reason_code, comment_ref, expected_product_brief_version, "
                    "expected_workflow_version, created_at) "
                    "VALUES (:id, :workspace_id, :product_brief_id, "
                    ":product_brief_version_id, :product_brief_version_number, "
                    ":workflow_id, :operation_id, :approval_id, 'PRODUCT_BRIEF', "
                    "'APPROVE', 'reviewer-mismatch', NULL, NULL, 1, 999, "
                    "UTC_TIMESTAMP(6))"
                ),
                {
                    "approval_id": approval_id,
                    "id": new_uuid7(),
                    "operation_id": operation_id,
                    "product_brief_id": case["confirmation_product_brief_id"],
                    "product_brief_version_id": model_version["id"],
                    "product_brief_version_number": case["subject_version"],
                    "workflow_id": workflow_id,
                    "workspace_id": WORKSPACE_ID,
                },
            )
    serialized_payloads = json.dumps(
        [event.envelope.payload for event in all_events],
        sort_keys=True,
    )
    assert "Hydrating Serum" not in serialized_payloads
    assert "common.identity" not in serialized_payloads


def test_product_brief_confirmation_event_continues_agent_from_retrieval(
    integration_database,
    integration_settings,
    seed_fixture_planner_prompt,
) -> None:
    settings = _settings(integration_settings)
    seed_fixture_planner_prompt(integration_database, workspace_id=WORKSPACE_ID)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(integration_database)
    executor = ProductBriefAnalysisExecutor(
        uow_factory=lambda: SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory),
        object_storage=ControlledReadStorage(),
        analyzer=DeterministicVisionAnalyzer(
            scenario=DeterministicVisionScenario.LOW_CONFIDENCE,
            artifact_sink=MemoryArtifactSink(),
        ),
        policy=ProductBriefPolicy.from_settings(settings),
        transfer_policy=VisionDataTransferPolicy.from_settings(settings),
        artifact_service=_artifact_service(integration_database),
    )
    runtime = WorkerRuntime.build(
        settings,
        operation_executors={
            OperationKind.PRODUCT_BRIEF_ANALYSIS: executor,
        },
    )
    app = create_app(settings)

    try:
        with TestClient(app) as client:
            requested = client.post(
                "/api/v1/product-briefs:analyze",
                headers=_mutation_headers("brief-agent-continuation-analysis-0001"),
                json={
                    "workflow_id": workflow_id,
                    "product_id": product_id,
                    "asset_version_ids": [asset_version_id],
                    "expected_workflow_version": 3,
                },
            )
            assert requested.status_code == 202, requested.text
            product_brief_id = requested.json()["product_brief"]["id"]
            with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
                analysis_event = next(
                    event
                    for event in uow.outbox.list_for_aggregate(product_brief_id)
                    if event.envelope.event_type == "product-brief.requested"
                )
            assert runtime.process_event(analysis_event.envelope.event_id) == "processed"

            awaiting = client.get(
                f"/api/v1/product-briefs/{product_brief_id}",
                headers=_read_headers(),
            )
            workflow = client.get(
                f"/api/v1/workflows/{workflow_id}",
                headers=_read_headers(),
            )
            assert awaiting.status_code == 200, awaiting.text
            assert workflow.status_code == 200, workflow.text
            assert awaiting.json()["state"] == "AWAITING_CONFIRMATION"
            model_version = awaiting.json()["current_version"]

            confirmed = client.post(
                f"/api/v1/product-briefs/{product_brief_id}:confirm",
                headers=_mutation_headers("brief-agent-continuation-confirm-0001"),
                json={
                    "expected_product_brief_version": awaiting.json()["version"],
                    "product_brief_version_id": model_version["id"],
                    "expected_workflow_version": workflow.json()["version"],
                    "reason_code": "HUMAN_VERIFIED",
                },
            )
            assert confirmed.status_code == 200, confirmed.text
            with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
                continuation_event = next(
                    event
                    for event in uow.outbox.list_for_aggregate(workflow_id)
                    if event.envelope.event_type == "workflow.resume.requested"
                    and event.envelope.payload.get("approval_type") == "PRODUCT_BRIEF"
                )

            assert runtime.process_event(continuation_event.envelope.event_id) == "processed"
            assert runtime.process_event(continuation_event.envelope.event_id) == "duplicate"
            progressed = client.get(
                f"/api/v1/workflows/{workflow_id}",
                headers=_read_headers(),
            )
    finally:
        runtime.close()

    assert progressed.status_code == 200, progressed.text
    assert progressed.json()["status"] == "AWAITING_PLAN_APPROVAL"
    assert progressed.json()["current_node"] == "approve_plan"
    step_keys = {step["step_key"] for step in progressed.json()["steps"]}
    assert step_keys.issuperset({"create_plan:0", "approve_plan:0"})
    assert f"retrieve_references:product-brief:{model_version['id']}" in step_keys


def test_public_commerce_workflow_restarts_through_product_brief_human_wait(
    integration_database,
    integration_settings,
    seed_fixture_planner_prompt,
) -> None:
    settings = _settings(integration_settings)
    seed_fixture_planner_prompt(integration_database, workspace_id=WORKSPACE_ID)
    _, product_id, asset_version_id = _seed_authorized_source(integration_database)
    artifact_sink = MemoryArtifactSink()
    executor = ProductBriefAnalysisExecutor(
        uow_factory=lambda: SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory),
        object_storage=ControlledReadStorage(),
        analyzer=DeterministicVisionAnalyzer(
            scenario=DeterministicVisionScenario.LOW_CONFIDENCE,
            artifact_sink=artifact_sink,
        ),
        policy=ProductBriefPolicy.from_settings(settings),
        transfer_policy=VisionDataTransferPolicy.from_settings(settings),
        artifact_service=_artifact_service(integration_database, artifact_sink),
    )
    app = create_app(settings)
    analysis_trace_id = "public-commerce-product-brief-analysis-trace"

    with TestClient(app) as client:
        workflow_created = client.post(
            "/api/v1/workflows",
            headers=_mutation_headers("public-commerce-workflow-create-0001"),
            json={
                "workflow_type": "COMMERCE_IMAGE_GENERATION",
                "input_data": {
                    "schema_version": "1.0",
                    "product_id": product_id,
                },
                "retention_hours": 72,
            },
        )
        assert workflow_created.status_code == 202, workflow_created.text
        workflow_id = workflow_created.json()["id"]
        assert workflow_created.json()["status"] == "UNDERSTANDING"
        assert workflow_created.json()["current_node"] == "understand_product"
        with integration_database.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE assets SET workflow_id = :workflow_id "
                    "WHERE workspace_id = :workspace "
                    "AND current_version_id = :asset_version_id"
                ),
                {
                    "asset_version_id": asset_version_id,
                    "workflow_id": workflow_id,
                    "workspace": WORKSPACE_ID,
                },
            )

        requested = client.post(
            "/api/v1/product-briefs:analyze",
            headers={
                **_mutation_headers("public-commerce-analysis-0001"),
                "X-Trace-Id": analysis_trace_id,
            },
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": workflow_created.json()["version"],
            },
        )
        assert requested.status_code == 202, requested.text
        product_brief_id = requested.json()["product_brief"]["id"]
        with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
            analysis_event = next(
                event
                for event in uow.outbox.list_for_aggregate(product_brief_id)
                if event.envelope.event_type == "product-brief.requested"
            )

        first_worker = WorkerRuntime.build(
            settings,
            operation_executors={
                OperationKind.PRODUCT_BRIEF_ANALYSIS: executor,
            },
        )
        try:
            assert first_worker.process_event(analysis_event.envelope.event_id) == "processed"
        finally:
            first_worker.close()

        awaiting = client.get(
            f"/api/v1/product-briefs/{product_brief_id}",
            headers=_read_headers(),
        )
        workflow = client.get(
            f"/api/v1/workflows/{workflow_id}",
            headers=_read_headers(),
        )
        assert awaiting.status_code == 200, awaiting.text
        assert workflow.status_code == 200, workflow.text
        assert awaiting.json()["state"] == "AWAITING_CONFIRMATION"
        with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
            awaiting_event = next(
                event
                for event in uow.outbox.list_for_aggregate(product_brief_id)
                if event.envelope.event_type == "product-brief.awaiting-confirmation"
            )
        assert awaiting_event.envelope.trace_id == analysis_trace_id

        confirmed = client.post(
            f"/api/v1/product-briefs/{product_brief_id}:confirm",
            headers=_mutation_headers("public-commerce-confirm-0001"),
            json={
                "expected_product_brief_version": awaiting.json()["version"],
                "product_brief_version_id": awaiting.json()["current_version"]["id"],
                "expected_workflow_version": workflow.json()["version"],
                "reason_code": "HUMAN_VERIFIED",
            },
        )
        assert confirmed.status_code == 200, confirmed.text
        with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
            continuation_event = next(
                event
                for event in uow.outbox.list_for_aggregate(workflow_id)
                if event.envelope.event_type == "workflow.resume.requested"
                and event.envelope.payload.get("approval_type") == "PRODUCT_BRIEF"
            )
            confirmed_event = next(
                event
                for event in uow.outbox.list_for_aggregate(product_brief_id)
                if event.envelope.event_type == "product-brief.confirmed"
            )
        assert continuation_event.envelope.trace_id == analysis_trace_id
        assert confirmed_event.envelope.trace_id == analysis_trace_id

        restarted_worker = WorkerRuntime.build(
            settings,
            operation_executors={
                OperationKind.PRODUCT_BRIEF_ANALYSIS: executor,
            },
        )
        try:
            assert (
                restarted_worker.process_event(continuation_event.envelope.event_id) == "processed"
            )
        finally:
            restarted_worker.close()
        progressed = client.get(
            f"/api/v1/workflows/{workflow_id}",
            headers=_read_headers(),
        )

    assert progressed.status_code == 200, progressed.text
    assert progressed.json()["status"] == "AWAITING_PLAN_APPROVAL"
    assert progressed.json()["current_node"] == "approve_plan"
    step_keys = {step["step_key"] for step in progressed.json()["steps"]}
    assert step_keys.issuperset({"create_plan:0", "approve_plan:0"})
    assert (
        f"retrieve_references:product-brief:{awaiting.json()['current_version']['id']}"
    ) in step_keys


def test_public_commerce_workflow_restarts_from_policy_confirmed_product_brief(
    integration_database,
    integration_settings,
    seed_fixture_planner_prompt,
) -> None:
    settings = _settings(integration_settings)
    seed_fixture_planner_prompt(integration_database, workspace_id=WORKSPACE_ID)
    _, product_id, asset_version_id = _seed_authorized_source(integration_database)
    artifact_sink = MemoryArtifactSink()
    executor = ProductBriefAnalysisExecutor(
        uow_factory=lambda: SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory),
        object_storage=ControlledReadStorage(),
        analyzer=DeterministicVisionAnalyzer(
            scenario=DeterministicVisionScenario.SUCCESS,
            artifact_sink=artifact_sink,
        ),
        policy=ProductBriefPolicy.from_settings(settings),
        transfer_policy=VisionDataTransferPolicy.from_settings(settings),
        artifact_service=_artifact_service(integration_database, artifact_sink),
    )
    app = create_app(settings)
    analysis_trace_id = "public-commerce-policy-confirmed-trace"

    with TestClient(app) as client:
        workflow_created = client.post(
            "/api/v1/workflows",
            headers=_mutation_headers("public-commerce-policy-workflow-0001"),
            json={
                "workflow_type": "COMMERCE_IMAGE_GENERATION",
                "input_data": {
                    "schema_version": "1.0",
                    "product_id": product_id,
                },
                "retention_hours": 72,
            },
        )
        assert workflow_created.status_code == 202, workflow_created.text
        workflow_id = workflow_created.json()["id"]
        with integration_database.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE assets SET workflow_id = :workflow_id "
                    "WHERE workspace_id = :workspace "
                    "AND current_version_id = :asset_version_id"
                ),
                {
                    "asset_version_id": asset_version_id,
                    "workflow_id": workflow_id,
                    "workspace": WORKSPACE_ID,
                },
            )

        requested = client.post(
            "/api/v1/product-briefs:analyze",
            headers={
                **_mutation_headers("public-commerce-policy-analysis-0001"),
                "X-Trace-Id": analysis_trace_id,
            },
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": workflow_created.json()["version"],
            },
        )
        assert requested.status_code == 202, requested.text
        product_brief_id = requested.json()["product_brief"]["id"]
        with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
            analysis_event = next(
                event
                for event in uow.outbox.list_for_aggregate(product_brief_id)
                if event.envelope.event_type == "product-brief.requested"
            )

        first_worker = WorkerRuntime.build(
            settings,
            operation_executors={
                OperationKind.PRODUCT_BRIEF_ANALYSIS: executor,
            },
        )
        try:
            assert first_worker.process_event(analysis_event.envelope.event_id) == "processed"
        finally:
            first_worker.close()

        product_brief = client.get(
            f"/api/v1/product-briefs/{product_brief_id}",
            headers=_read_headers(),
        )
        workflow = client.get(
            f"/api/v1/workflows/{workflow_id}",
            headers=_read_headers(),
        )
        assert product_brief.status_code == 200, product_brief.text
        assert workflow.status_code == 200, workflow.text
        assert product_brief.json()["state"] == "CONFIRMED"
        assert workflow.json()["status"] == "RETRIEVING"
        confirmed_version = product_brief.json()["current_version"]
        with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
            continuation_event = next(
                event
                for event in uow.outbox.list_for_aggregate(workflow_id)
                if event.envelope.event_type == "workflow.run.requested"
                and event.envelope.payload.get("reason") == "product-brief-policy-confirmed"
            )
        assert continuation_event.envelope.trace_id == analysis_trace_id
        assert (
            continuation_event.envelope.payload["product_brief_version_id"]
            == (confirmed_version["id"])
        )
        assert (
            continuation_event.envelope.payload["product_brief_version_number"]
            == confirmed_version["version_number"]
        )

        restarted_worker = WorkerRuntime.build(
            settings,
            operation_executors={
                OperationKind.PRODUCT_BRIEF_ANALYSIS: executor,
            },
        )
        try:
            assert (
                restarted_worker.process_event(continuation_event.envelope.event_id) == "processed"
            )
        finally:
            restarted_worker.close()
        progressed = client.get(
            f"/api/v1/workflows/{workflow_id}",
            headers=_read_headers(),
        )

    assert progressed.status_code == 200, progressed.text
    assert progressed.json()["status"] == "AWAITING_PLAN_APPROVAL"
    assert progressed.json()["current_node"] == "approve_plan"
    step_keys = {step["step_key"] for step in progressed.json()["steps"]}
    assert step_keys.issuperset({"create_plan:0", "approve_plan:0"})
    assert f"retrieve_references:product-brief:{confirmed_version['id']}" in step_keys


def _prepare_unconsumed_product_brief_continuation(
    *,
    database,
    settings: Settings,
    confirmation_source: str,
) -> tuple[OutboxEvent, str, str, str, str, ProductBriefAnalysisExecutor]:
    workflow_id, product_id, asset_version_id = _seed_authorized_source(database)
    artifact_sink = MemoryArtifactSink()
    scenario = (
        DeterministicVisionScenario.CONFLICT
        if confirmation_source == "HUMAN"
        else DeterministicVisionScenario.SUCCESS
    )
    executor = ProductBriefAnalysisExecutor(
        uow_factory=lambda: SqlAlchemyProductBriefUnitOfWork(database.session_factory),
        object_storage=ControlledReadStorage(),
        analyzer=DeterministicVisionAnalyzer(
            scenario=scenario,
            artifact_sink=artifact_sink,
        ),
        policy=ProductBriefPolicy.from_settings(settings),
        transfer_policy=VisionDataTransferPolicy.from_settings(settings),
        artifact_service=_artifact_service(database, artifact_sink),
    )
    with TestClient(create_app(settings)) as client:
        requested = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers(f"stale-{confirmation_source.lower()}-analysis-0001"),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": 3,
            },
        )
        assert requested.status_code == 202, requested.text
        product_brief_id = requested.json()["product_brief"]["id"]
        with SqlAlchemyUnitOfWork(database.session_factory) as uow:
            analysis_event = next(
                event
                for event in uow.outbox.list_for_aggregate(product_brief_id)
                if event.envelope.event_type == "product-brief.requested"
            )
        analysis_worker = WorkerRuntime.build(
            settings,
            operation_executors={
                OperationKind.PRODUCT_BRIEF_ANALYSIS: executor,
            },
        )
        try:
            assert analysis_worker.process_event(analysis_event.envelope.event_id) == "processed"
        finally:
            analysis_worker.close()

        if confirmation_source == "HUMAN":
            awaiting = client.get(
                f"/api/v1/product-briefs/{product_brief_id}",
                headers=_read_headers(),
            )
            workflow = client.get(
                f"/api/v1/workflows/{workflow_id}",
                headers=_read_headers(),
            )
            assert awaiting.status_code == 200, awaiting.text
            assert awaiting.json()["state"] == "AWAITING_CONFIRMATION"
            confirmed = client.post(
                f"/api/v1/product-briefs/{product_brief_id}:confirm",
                headers=_mutation_headers("stale-human-confirmation-0001"),
                json={
                    "expected_product_brief_version": awaiting.json()["version"],
                    "product_brief_version_id": awaiting.json()["current_version_id"],
                    "expected_workflow_version": workflow.json()["version"],
                    "reason_code": "HUMAN_VERIFIED",
                },
            )
            assert confirmed.status_code == 200, confirmed.text
            continuation_event_type = "workflow.resume.requested"
        else:
            confirmed = client.get(
                f"/api/v1/product-briefs/{product_brief_id}",
                headers=_read_headers(),
            )
            assert confirmed.status_code == 200, confirmed.text
            assert confirmed.json()["state"] == "CONFIRMED"
            continuation_event_type = "workflow.run.requested"

    with SqlAlchemyUnitOfWork(database.session_factory) as uow:
        continuation_event = next(
            event
            for event in uow.outbox.list_for_aggregate(workflow_id)
            if event.envelope.event_type == continuation_event_type
            and (
                event.envelope.payload.get("approval_type") == "PRODUCT_BRIEF"
                if confirmation_source == "HUMAN"
                else event.envelope.payload.get("reason") == "product-brief-policy-confirmed"
            )
        )
    return (
        continuation_event,
        workflow_id,
        product_brief_id,
        product_id,
        asset_version_id,
        executor,
    )


def _continuation_effect_snapshot(
    database,
    *,
    workflow_id: str,
    product_brief_id: str,
) -> dict[str, int]:
    with database.engine.connect() as connection:
        return {
            "steps": connection.scalar(
                text("SELECT COUNT(*) FROM workflow_steps WHERE workflow_id = :workflow_id"),
                {"workflow_id": workflow_id},
            ),
            "attempts": connection.scalar(
                text("SELECT COUNT(*) FROM workflow_attempts WHERE workflow_id = :workflow_id"),
                {"workflow_id": workflow_id},
            ),
            "checkpoints": connection.scalar(
                text("SELECT COUNT(*) FROM agent_checkpoints WHERE thread_id = :workflow_id"),
                {"workflow_id": workflow_id},
            ),
            "checkpoint_writes": connection.scalar(
                text("SELECT COUNT(*) FROM agent_checkpoint_writes WHERE thread_id = :workflow_id"),
                {"workflow_id": workflow_id},
            ),
            "outbox": connection.scalar(
                text("SELECT COUNT(*) FROM outbox_events WHERE aggregate_id = :workflow_id"),
                {"workflow_id": workflow_id},
            ),
            "provider_calls": connection.scalar(
                text(
                    "SELECT COUNT(*) FROM product_brief_provider_calls "
                    "WHERE product_brief_id = :product_brief_id"
                ),
                {"product_brief_id": product_brief_id},
            ),
            "dead_letters": connection.scalar(text("SELECT COUNT(*) FROM dead_letter_messages")),
        }


def _prepare_product_brief_generation_retry(
    *,
    database,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    seed_fixture_planner_prompt,
) -> tuple[
    OutboxEvent,
    str,
    str,
    ProductBriefAnalysisExecutor,
    dict[str, int],
]:
    tool_calls = {"count": 0}
    fixture_call = FixtureImageTool.__call__

    def fail_first_generation(
        fixture: FixtureImageTool,
        context: Any,
        invocation: Any,
    ):
        tool_calls["count"] += 1
        if tool_calls["count"] == 1:
            raise ToolExecutionError("transient generation failure", retryable=True)
        return fixture_call(fixture, context, invocation)

    monkeypatch.setattr(FixtureImageTool, "__call__", fail_first_generation)
    (
        continuation_event,
        workflow_id,
        product_brief_id,
        _product_id,
        _asset_version_id,
        executor,
    ) = _prepare_unconsumed_product_brief_continuation(
        database=database,
        settings=settings,
        confirmation_source="POLICY",
    )
    service = WorkflowApplicationService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(database.session_factory)
    )
    seed_fixture_planner_prompt(database, workspace_id=WORKSPACE_ID)
    runtime = WorkerRuntime.build(
        settings,
        operation_executors={
            OperationKind.PRODUCT_BRIEF_ANALYSIS: executor,
        },
    )
    try:
        assert runtime.process_event(continuation_event.envelope.event_id) == "processed"
        awaiting_plan = service.get(
            workflow_id=workflow_id,
            workspace_id=WORKSPACE_ID,
        )
        plan_step = next(
            step for step in awaiting_plan.steps if step.step_type == StepType.CREATE_PLAN
        )
        assert plan_step.output_data is not None
        plan_approval_trace_id = f"plan-approval-{workflow_id}"
        service.approve(
            workflow_id=workflow_id,
            workspace_id=WORKSPACE_ID,
            actor_id="brief-reviewer",
            approval_type=ApprovalType.CREATIVE_PLAN,
            request=ApprovalRequest(
                expected_workflow_version=awaiting_plan.version,
                subject_id=plan_step.output_data["creative_plan_ref"],
                subject_version=1,
                decision=ApprovalDecision.APPROVE,
            ),
            idempotency_key=f"product-brief-retry-plan-{workflow_id}",
            trace_id=plan_approval_trace_id,
        )
        with SqlAlchemyUnitOfWork(database.session_factory) as uow:
            plan_resume_event = next(
                event
                for event in uow.outbox.list_for_aggregate(workflow_id)
                if event.envelope.event_type == "workflow.resume.requested"
                and event.envelope.payload.get("approval_type") == "CREATIVE_PLAN"
            )
        assert plan_resume_event.envelope.trace_id == plan_approval_trace_id
        assert plan_resume_event.envelope.trace_id != continuation_event.envelope.trace_id
        assert runtime.process_event(plan_resume_event.envelope.event_id) == "processed"
    finally:
        runtime.close()

    with SqlAlchemyUnitOfWork(database.session_factory) as uow:
        retry_event = next(
            event
            for event in uow.outbox.list_for_aggregate(workflow_id)
            if event.envelope.event_type == "workflow.run.requested"
            and event.envelope.payload.get("reason") == "product-brief-generation-retry"
        )
    assert tool_calls["count"] == 1
    assert (
        retry_event.envelope.payload["product_brief_version_id"]
        == (continuation_event.envelope.payload["product_brief_version_id"])
    )
    assert (
        retry_event.envelope.payload["product_brief_version_number"]
        == (continuation_event.envelope.payload["product_brief_version_number"])
    )
    assert retry_event.envelope.trace_id == continuation_event.envelope.trace_id
    return retry_event, workflow_id, product_brief_id, executor, tool_calls


def _release_product_brief_generation_retry(
    database,
    *,
    retry_event_id: str,
    workflow_id: str,
) -> None:
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE outbox_events "
                "SET available_at = CURRENT_TIMESTAMP(6) - INTERVAL 1 SECOND "
                "WHERE id = :event_id"
            ),
            {"event_id": retry_event_id},
        )
        connection.execute(
            text(
                "UPDATE workflow_steps "
                "SET next_attempt_at = CURRENT_TIMESTAMP(6) - INTERVAL 1 SECOND "
                "WHERE workflow_id = :workflow_id "
                "AND step_key = 'execute_tool:0' "
                "AND status = 'RETRYABLE_FAILED'"
            ),
            {"workflow_id": workflow_id},
        )


def test_product_brief_retry_restores_original_generation_and_analysis_trace(
    integration_database,
    integration_settings,
    monkeypatch: pytest.MonkeyPatch,
    seed_fixture_planner_prompt,
) -> None:
    settings = _settings(integration_settings)
    retry_event, workflow_id, _product_brief_id, executor, tool_calls = (
        _prepare_product_brief_generation_retry(
            database=integration_database,
            settings=settings,
            monkeypatch=monkeypatch,
            seed_fixture_planner_prompt=seed_fixture_planner_prompt,
        )
    )
    _release_product_brief_generation_retry(
        integration_database,
        retry_event_id=retry_event.envelope.event_id,
        workflow_id=workflow_id,
    )

    restarted_runtime = WorkerRuntime.build(
        settings,
        operation_executors={
            OperationKind.PRODUCT_BRIEF_ANALYSIS: executor,
        },
    )
    try:
        assert restarted_runtime.process_event(retry_event.envelope.event_id) == "processed"
    finally:
        restarted_runtime.close()

    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        workflow = uow.workflows.get(workflow_id)
        retrieval_steps = [
            step
            for step in uow.steps.list_for_workflow(workflow_id)
            if step.step_type == StepType.RETRIEVE_REFERENCES
        ]
        node_events = [
            event
            for event in uow.outbox.list_for_aggregate(workflow_id)
            if event.envelope.event_type in {"workflow.node.started", "workflow.node.completed"}
        ]
    assert workflow is not None
    assert tool_calls["count"] == 2, {
        "workflow_status": workflow.status.value,
        "workflow_node": workflow.current_node,
        "workflow_version": workflow.version,
        "retry_event_version": retry_event.envelope.aggregate_version,
    }
    assert workflow.status == WorkflowStatus.AWAITING_RESULT_APPROVAL
    assert workflow.current_node == "approve_results"
    assert len(retrieval_steps) == 1
    assert {event.envelope.trace_id for event in node_events} == {retry_event.envelope.trace_id}


@pytest.mark.parametrize("stale_reason", ["expired", "superseded"])
def test_product_brief_retry_rechecks_authority_before_generation(
    integration_database,
    integration_settings,
    monkeypatch: pytest.MonkeyPatch,
    seed_fixture_planner_prompt,
    stale_reason: str,
) -> None:
    settings = _settings(integration_settings)
    retry_event, workflow_id, _product_brief_id, executor, tool_calls = (
        _prepare_product_brief_generation_retry(
            database=integration_database,
            settings=settings,
            monkeypatch=monkeypatch,
            seed_fixture_planner_prompt=seed_fixture_planner_prompt,
        )
    )
    if stale_reason == "expired":
        with integration_database.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE workflows SET retention_status = 'EXPIRING' "
                    "WHERE workspace_id = :workspace AND id = :workflow_id"
                ),
                {
                    "workspace": WORKSPACE_ID,
                    "workflow_id": workflow_id,
                },
            )
    else:
        with integration_database.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE workflows SET version = version + 1 "
                    "WHERE workspace_id = :workspace AND id = :workflow_id"
                ),
                {
                    "workspace": WORKSPACE_ID,
                    "workflow_id": workflow_id,
                },
            )
    _release_product_brief_generation_retry(
        integration_database,
        retry_event_id=retry_event.envelope.event_id,
        workflow_id=workflow_id,
    )

    restarted_runtime = WorkerRuntime.build(
        settings,
        operation_executors={
            OperationKind.PRODUCT_BRIEF_ANALYSIS: executor,
        },
    )
    try:
        assert restarted_runtime.process_event(retry_event.envelope.event_id) == "processed"
    finally:
        restarted_runtime.close()

    assert tool_calls["count"] == 1
    with integration_database.engine.connect() as connection:
        inbox_status = connection.scalar(
            text(
                "SELECT status FROM inbox_messages "
                "WHERE consumer = :consumer AND message_id = :message_id"
            ),
            {
                "consumer": settings.worker_consumer_name,
                "message_id": retry_event.envelope.event_id,
            },
        )
        dead_letter_count = connection.scalar(
            text(
                "SELECT COUNT(*) FROM dead_letter_messages "
                "WHERE consumer = :consumer AND message_id = :message_id"
            ),
            {
                "consumer": settings.worker_consumer_name,
                "message_id": retry_event.envelope.event_id,
            },
        )
    assert inbox_status == "PROCESSED"
    assert dead_letter_count == 0


@pytest.mark.parametrize("confirmation_source", ["HUMAN", "POLICY"])
def test_product_brief_continuation_rejects_future_retention_deadline_mismatch(
    integration_database,
    integration_settings,
    confirmation_source: str,
) -> None:
    settings = _settings(integration_settings)
    (
        continuation_event,
        workflow_id,
        product_brief_id,
        _product_id,
        _asset_version_id,
        executor,
    ) = _prepare_unconsumed_product_brief_continuation(
        database=integration_database,
        settings=settings,
        confirmation_source=confirmation_source,
    )
    with integration_database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE product_briefs "
                "SET retention_deadline = retention_deadline - INTERVAL 1 SECOND "
                "WHERE workspace_id = :workspace AND id = :product_brief_id"
            ),
            {
                "workspace": WORKSPACE_ID,
                "product_brief_id": product_brief_id,
            },
        )
    before = _continuation_effect_snapshot(
        integration_database,
        workflow_id=workflow_id,
        product_brief_id=product_brief_id,
    )

    worker = WorkerRuntime.build(
        settings,
        operation_executors={
            OperationKind.PRODUCT_BRIEF_ANALYSIS: executor,
        },
    )
    try:
        assert worker.process_event(continuation_event.envelope.event_id) == "dead-lettered"
    finally:
        worker.close()

    after = _continuation_effect_snapshot(
        integration_database,
        workflow_id=workflow_id,
        product_brief_id=product_brief_id,
    )
    assert {key: value for key, value in after.items() if key != "dead_letters"} == {
        key: value for key, value in before.items() if key != "dead_letters"
    }
    assert after["dead_letters"] == before["dead_letters"] + 1
    with integration_database.engine.connect() as connection:
        reason = connection.scalar(
            text(
                "SELECT reason FROM dead_letter_messages "
                "WHERE consumer = :consumer AND message_id = :message_id"
            ),
            {
                "consumer": settings.worker_consumer_name,
                "message_id": continuation_event.envelope.event_id,
            },
        )
    assert reason == "product_brief_retention_binding_mismatch"


def _assert_stale_continuation_was_processed_without_side_effects(
    database,
    *,
    settings: Settings,
    event: OutboxEvent,
    workflow_id: str,
    product_brief_id: str,
    executor: ProductBriefAnalysisExecutor,
) -> None:
    before = _continuation_effect_snapshot(
        database,
        workflow_id=workflow_id,
        product_brief_id=product_brief_id,
    )
    worker = WorkerRuntime.build(
        settings,
        operation_executors={
            OperationKind.PRODUCT_BRIEF_ANALYSIS: executor,
        },
    )
    try:
        assert worker.process_event(event.envelope.event_id) == "processed"
    finally:
        worker.close()
    after = _continuation_effect_snapshot(
        database,
        workflow_id=workflow_id,
        product_brief_id=product_brief_id,
    )
    assert after == before
    with database.engine.connect() as connection:
        inbox = (
            connection.execute(
                text(
                    "SELECT status, delivery_attempts, error_class, error_message "
                    "FROM inbox_messages WHERE consumer = :consumer "
                    "AND message_id = :message_id"
                ),
                {
                    "consumer": settings.worker_consumer_name,
                    "message_id": event.envelope.event_id,
                },
            )
            .mappings()
            .one()
        )
    assert dict(inbox) == {
        "status": "PROCESSED",
        "delivery_attempts": 1,
        "error_class": None,
        "error_message": None,
    }


def test_confirmed_product_brief_recovers_before_initial_step_claim(
    integration_database,
    integration_settings,
    seed_fixture_planner_prompt,
) -> None:
    settings = _settings(integration_settings)
    seed_fixture_planner_prompt(integration_database, workspace_id=WORKSPACE_ID)
    (
        continuation_event,
        workflow_id,
        _product_brief_id,
        _product_id,
        _asset_version_id,
        executor,
    ) = _prepare_unconsumed_product_brief_continuation(
        database=integration_database,
        settings=settings,
        confirmation_source="POLICY",
    )
    version_id = continuation_event.envelope.payload["product_brief_version_id"]
    version_number = continuation_event.envelope.payload["product_brief_version_number"]
    with integration_database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE outbox_events SET published_at = CURRENT_TIMESTAMP(6) WHERE id = :event_id"
            ),
            {"event_id": continuation_event.envelope.event_id},
        )
        connection.execute(
            text(
                "UPDATE workflows "
                "SET updated_at = CURRENT_TIMESTAMP(6) - INTERVAL 2 DAY "
                "WHERE id = :workflow_id"
            ),
            {"workflow_id": workflow_id},
        )

    recovery = RecoveryService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(integration_database.session_factory),
        batch_size=20,
        stale_after=timedelta(days=1),
    )
    assert recovery.recover_once() == (0, 1)
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        recovery_events = [
            event
            for event in uow.outbox.list_for_aggregate(workflow_id)
            if event.envelope.event_type == "workflow.run.requested"
            and event.envelope.payload.get("reason") == "stale_workflow"
        ]
    assert len(recovery_events) == 1
    recovery_event = recovery_events[0]
    assert recovery_event.envelope.payload["product_brief_version_id"] == version_id
    assert recovery_event.envelope.payload["product_brief_version_number"] == version_number
    assert recovery_event.envelope.trace_id == continuation_event.envelope.trace_id

    restarted_runtime = WorkerRuntime.build(
        settings,
        operation_executors={
            OperationKind.PRODUCT_BRIEF_ANALYSIS: executor,
        },
    )
    try:
        assert restarted_runtime.process_event(recovery_event.envelope.event_id) == "processed"
    finally:
        restarted_runtime.close()

    with TestClient(create_app(settings)) as client:
        progressed = client.get(
            f"/api/v1/workflows/{workflow_id}",
            headers=_read_headers(),
        )
    assert progressed.status_code == 200, progressed.text
    assert progressed.json()["status"] == "AWAITING_PLAN_APPROVAL"
    assert progressed.json()["current_node"] == "approve_plan"
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        recovered_node_events = [
            event
            for event in uow.outbox.list_for_aggregate(workflow_id)
            if event.envelope.event_type
            in {
                "workflow.node.started",
                "workflow.node.completed",
            }
        ]
    assert recovered_node_events
    assert {event.envelope.trace_id for event in recovered_node_events} == {
        continuation_event.envelope.trace_id
    }


@pytest.mark.parametrize("confirmation_source", ["HUMAN", "POLICY"])
@pytest.mark.parametrize("expiry_mode", ["workflow-deadline", "legacy-task-deadline"])
def test_expired_product_brief_continuation_is_a_processed_stale_noop(
    integration_database,
    integration_settings,
    confirmation_source: str,
    expiry_mode: str,
) -> None:
    settings = _settings(integration_settings)
    (
        continuation_event,
        workflow_id,
        product_brief_id,
        _,
        _,
        executor,
    ) = _prepare_unconsumed_product_brief_continuation(
        database=integration_database,
        settings=settings,
        confirmation_source=confirmation_source,
    )
    database_now = datetime.now(UTC).replace(tzinfo=None)
    expired_at = database_now - timedelta(seconds=1)
    with integration_database.engine.begin() as connection:
        if expiry_mode == "legacy-task-deadline":
            legacy_created_at = database_now - timedelta(hours=73)
            expired_at = legacy_created_at + timedelta(hours=72)
            connection.execute(
                text(
                    "UPDATE workflows "
                    "SET created_at = :created_at, expires_at = :workflow_expires_at "
                    "WHERE workspace_id = :workspace AND id = :workflow_id"
                ),
                {
                    "created_at": legacy_created_at,
                    "workflow_expires_at": legacy_created_at + timedelta(hours=168),
                    "workflow_id": workflow_id,
                    "workspace": WORKSPACE_ID,
                },
            )
        else:
            connection.execute(
                text(
                    "UPDATE workflows SET expires_at = :expired_at "
                    "WHERE workspace_id = :workspace AND id = :workflow_id"
                ),
                {
                    "expired_at": expired_at,
                    "workflow_id": workflow_id,
                    "workspace": WORKSPACE_ID,
                },
            )
        connection.execute(
            text(
                "UPDATE product_briefs SET retention_deadline = :expired_at "
                "WHERE workspace_id = :workspace AND id = :product_brief_id"
            ),
            {
                "expired_at": expired_at,
                "product_brief_id": product_brief_id,
                "workspace": WORKSPACE_ID,
            },
        )

    _assert_stale_continuation_was_processed_without_side_effects(
        integration_database,
        settings=settings,
        event=continuation_event,
        workflow_id=workflow_id,
        product_brief_id=product_brief_id,
        executor=executor,
    )


def test_expiry_does_not_hide_tampered_product_brief_version_authority(
    integration_database,
    integration_settings,
) -> None:
    settings = _settings(integration_settings)
    (
        continuation_event,
        workflow_id,
        product_brief_id,
        _,
        _,
        executor,
    ) = _prepare_unconsumed_product_brief_continuation(
        database=integration_database,
        settings=settings,
        confirmation_source="POLICY",
    )
    expired_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
    tampered_version = int(continuation_event.envelope.payload["product_brief_version_number"]) + 1
    with integration_database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE workflows SET expires_at = :expired_at "
                "WHERE workspace_id = :workspace AND id = :workflow_id"
            ),
            {
                "expired_at": expired_at,
                "workflow_id": workflow_id,
                "workspace": WORKSPACE_ID,
            },
        )
        connection.execute(
            text(
                "UPDATE product_briefs SET retention_deadline = :expired_at "
                "WHERE workspace_id = :workspace AND id = :product_brief_id"
            ),
            {
                "expired_at": expired_at,
                "product_brief_id": product_brief_id,
                "workspace": WORKSPACE_ID,
            },
        )
        connection.execute(
            text(
                "UPDATE outbox_events SET payload_json = JSON_SET("
                "payload_json, '$.product_brief_version_number', :tampered_version) "
                "WHERE id = :event_id"
            ),
            {
                "event_id": continuation_event.envelope.event_id,
                "tampered_version": tampered_version,
            },
        )

    before = _continuation_effect_snapshot(
        integration_database,
        workflow_id=workflow_id,
        product_brief_id=product_brief_id,
    )
    worker = WorkerRuntime.build(
        settings,
        operation_executors={
            OperationKind.PRODUCT_BRIEF_ANALYSIS: executor,
        },
    )
    try:
        assert worker.process_event(continuation_event.envelope.event_id) == "dead-lettered"
    finally:
        worker.close()
    after = _continuation_effect_snapshot(
        integration_database,
        workflow_id=workflow_id,
        product_brief_id=product_brief_id,
    )
    assert {key: value for key, value in after.items() if key != "dead_letters"} == {
        key: value for key, value in before.items() if key != "dead_letters"
    }
    assert after["dead_letters"] == before["dead_letters"] + 1
    with integration_database.engine.connect() as connection:
        reason = connection.scalar(
            text(
                "SELECT reason FROM dead_letter_messages "
                "WHERE consumer = :consumer AND message_id = :message_id"
            ),
            {
                "consumer": settings.worker_consumer_name,
                "message_id": continuation_event.envelope.event_id,
            },
        )
    assert reason == "product_brief_version_mismatch"


@pytest.mark.parametrize("confirmation_source", ["HUMAN", "POLICY"])
def test_superseded_product_brief_continuation_is_a_processed_stale_noop(
    integration_database,
    integration_settings,
    confirmation_source: str,
) -> None:
    settings = _settings(integration_settings)
    (
        continuation_event,
        workflow_id,
        product_brief_id,
        product_id,
        asset_version_id,
        executor,
    ) = _prepare_unconsumed_product_brief_continuation(
        database=integration_database,
        settings=settings,
        confirmation_source=confirmation_source,
    )
    with TestClient(create_app(settings)) as client:
        workflow = client.get(
            f"/api/v1/workflows/{workflow_id}",
            headers=_read_headers(),
        )
        assert workflow.status_code == 200, workflow.text
        replacement = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers(f"superseding-{confirmation_source.lower()}-analysis-0002"),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": workflow.json()["version"],
            },
        )
    assert replacement.status_code == 202, replacement.text
    assert replacement.json()["product_brief"]["id"] == product_brief_id
    assert replacement.json()["product_brief"]["state"] == "DRAFT"

    _assert_stale_continuation_was_processed_without_side_effects(
        integration_database,
        settings=settings,
        event=continuation_event,
        workflow_id=workflow_id,
        product_brief_id=product_brief_id,
        executor=executor,
    )


@pytest.mark.parametrize(
    ("authority_mutation", "expected_status", "expected_code"),
    [
        ("workflow_type", 409, "VERSION_CONFLICT"),
        ("product_id", 409, "VERSION_CONFLICT"),
        ("retention_status", 410, "PRODUCT_BRIEF_RETENTION_EXPIRED"),
    ],
)
def test_product_brief_confirmation_rejects_workflow_authority_drift(
    integration_database,
    integration_settings,
    authority_mutation: str,
    expected_status: int,
    expected_code: str,
) -> None:
    settings = _settings(integration_settings)
    (
        _continuation_event,
        workflow_id,
        product_brief_id,
        _product_id,
        _asset_version_id,
        _executor,
    ) = _prepare_unconsumed_product_brief_continuation(
        database=integration_database,
        settings=settings,
        confirmation_source="HUMAN",
    )
    with TestClient(create_app(settings)) as client:
        confirmed = client.get(
            f"/api/v1/product-briefs/{product_brief_id}",
            headers=_read_headers(),
        )
        assert confirmed.status_code == 200, confirmed.text
        revision = client.post(
            f"/api/v1/product-briefs/{product_brief_id}:revise",
            headers=_mutation_headers("binding-drift-revision-0001"),
            json={
                "expected_product_brief_version": confirmed.json()["version"],
                "base_version_id": confirmed.json()["current_version_id"],
                "reason": "Prepare an awaiting version before binding drift",
                "fields": _human_revision_fields(confirmed.json()["current_version"]),
            },
        )
        assert revision.status_code == 200, revision.text
        workflow = client.get(
            f"/api/v1/workflows/{workflow_id}",
            headers=_read_headers(),
        )
        assert workflow.status_code == 200, workflow.text
        with integration_database.engine.begin() as connection:
            if authority_mutation == "workflow_type":
                mutation = "workflow_type = 'FIXTURE_IMAGE_GENERATION'"
                parameters = {}
            elif authority_mutation == "product_id":
                mutation = (
                    "input_json = JSON_SET(input_json, '$.product_id', :replacement_product_id)"
                )
                parameters = {"replacement_product_id": new_uuid7()}
            else:
                mutation = "retention_status = 'EXPIRING'"
                parameters = {}
            connection.execute(
                text(
                    f"UPDATE workflows SET {mutation} "
                    "WHERE workspace_id = :workspace AND id = :workflow_id"
                ),
                {
                    **parameters,
                    "workflow_id": workflow_id,
                    "workspace": WORKSPACE_ID,
                },
            )

        response = client.post(
            f"/api/v1/product-briefs/{product_brief_id}:confirm",
            headers=_mutation_headers("binding-drift-confirmation-0001"),
            json={
                "expected_product_brief_version": revision.json()["version"],
                "product_brief_version_id": revision.json()["current_version_id"],
                "expected_workflow_version": workflow.json()["version"],
                "reason_code": "HUMAN_VERIFIED",
            },
        )

    assert response.status_code == expected_status
    assert response.json()["code"] == expected_code


@pytest.mark.parametrize(
    ("authority_mutation", "expected_status", "expected_code"),
    [
        ("workflow_type", 409, "VERSION_CONFLICT"),
        ("product_id", 409, "VERSION_CONFLICT"),
        ("retention_status", 410, "PRODUCT_BRIEF_RETENTION_EXPIRED"),
    ],
)
def test_product_brief_revision_rejects_workflow_authority_drift(
    integration_database,
    integration_settings,
    authority_mutation: str,
    expected_status: int,
    expected_code: str,
) -> None:
    settings = _settings(integration_settings)
    (
        _continuation_event,
        workflow_id,
        product_brief_id,
        _product_id,
        _asset_version_id,
        _executor,
    ) = _prepare_unconsumed_product_brief_continuation(
        database=integration_database,
        settings=settings,
        confirmation_source="HUMAN",
    )
    with TestClient(create_app(settings)) as client:
        confirmed = client.get(
            f"/api/v1/product-briefs/{product_brief_id}",
            headers=_read_headers(),
        )
        assert confirmed.status_code == 200, confirmed.text
        with integration_database.engine.begin() as connection:
            if authority_mutation == "workflow_type":
                mutation = "workflow_type = 'FIXTURE_IMAGE_GENERATION'"
                parameters = {}
            elif authority_mutation == "product_id":
                mutation = (
                    "input_json = JSON_SET(input_json, '$.product_id', :replacement_product_id)"
                )
                parameters = {"replacement_product_id": new_uuid7()}
            else:
                mutation = "retention_status = 'EXPIRING'"
                parameters = {}
            connection.execute(
                text(
                    f"UPDATE workflows SET {mutation} "
                    "WHERE workspace_id = :workspace AND id = :workflow_id"
                ),
                {
                    **parameters,
                    "workflow_id": workflow_id,
                    "workspace": WORKSPACE_ID,
                },
            )

        response = client.post(
            f"/api/v1/product-briefs/{product_brief_id}:revise",
            headers=_mutation_headers("revision-binding-drift-0001"),
            json={
                "expected_product_brief_version": confirmed.json()["version"],
                "base_version_id": confirmed.json()["current_version_id"],
                "reason": "This revision must not cross a Workflow binding drift",
                "fields": _human_revision_fields(confirmed.json()["current_version"]),
            },
        )

    assert response.status_code == expected_status
    assert response.json()["code"] == expected_code


@pytest.mark.parametrize(
    "stale_detection",
    ["redelivery", "expired-step", "stale-workflow"],
)
def test_claimed_v1_is_cancelled_when_reanalysis_supersedes_before_recovery(
    integration_database,
    integration_settings,
    seed_fixture_planner_prompt,
    stale_detection: str,
) -> None:
    settings = _settings(integration_settings)
    seed_fixture_planner_prompt(integration_database, workspace_id=WORKSPACE_ID)
    (
        v1_event,
        workflow_id,
        product_brief_id,
        product_id,
        asset_version_id,
        executor,
    ) = _prepare_unconsumed_product_brief_continuation(
        database=integration_database,
        settings=settings,
        confirmation_source="POLICY",
    )
    continuation = ProductBriefContinuation(
        workspace_id=WORKSPACE_ID,
        product_brief_version_id=v1_event.envelope.payload["product_brief_version_id"],
        product_brief_version_number=v1_event.envelope.payload["product_brief_version_number"],
        approval_id=None,
    )
    runtime = WorkerRuntime.build(
        settings,
        operation_executors={
            OperationKind.PRODUCT_BRIEF_ANALYSIS: executor,
        },
    )
    assert runtime.lifecycle is not None
    try:
        claim = runtime.lifecycle.claim_product_brief_continuation(
            workflow_id=workflow_id,
            expected_workflow_version=v1_event.envelope.aggregate_version,
            continuation=continuation,
            lease_owner="crashed-v1-worker",
            trace_id=v1_event.envelope.trace_id,
        )
        assert claim.node_claim is not None
        assert claim.node_claim.lease_token is not None

        with integration_database.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE outbox_events SET published_at = CURRENT_TIMESTAMP(6) "
                    "WHERE id = :event_id"
                ),
                {"event_id": v1_event.envelope.event_id},
            )

        with TestClient(create_app(settings)) as client:
            workflow = client.get(
                f"/api/v1/workflows/{workflow_id}",
                headers=_read_headers(),
            )
            assert workflow.status_code == 200, workflow.text
            replacement = client.post(
                "/api/v1/product-briefs:analyze",
                headers=_mutation_headers("claimed-v1-superseding-analysis-0002"),
                json={
                    "workflow_id": workflow_id,
                    "product_id": product_id,
                    "asset_version_ids": [asset_version_id],
                    "expected_workflow_version": workflow.json()["version"],
                },
            )
        assert replacement.status_code == 202, replacement.text

        recovery = RecoveryService(
            uow_factory=lambda: SqlAlchemyUnitOfWork(integration_database.session_factory),
            batch_size=20,
            stale_after=timedelta(days=1),
        )
        if stale_detection == "expired-step":
            with integration_database.engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE workflow_steps "
                        "SET lease_expires_at = CURRENT_TIMESTAMP(6) - INTERVAL 1 SECOND "
                        "WHERE id = :step_id"
                    ),
                    {"step_id": claim.node_claim.step_id},
                )
            assert recovery.recover_once() == (1, 0)
        elif stale_detection == "stale-workflow":
            with integration_database.engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE workflows "
                        "SET updated_at = CURRENT_TIMESTAMP(6) - INTERVAL 2 DAY "
                        "WHERE id = :workflow_id"
                    ),
                    {"workflow_id": workflow_id},
                )
            assert recovery.recover_once() == (0, 1)
            assert recovery.recover_once() == (0, 0)
        assert runtime.process_event(v1_event.envelope.event_id) == "processed"
        with integration_database.engine.connect() as connection:
            stale_step = (
                connection.execute(
                    text(
                        "SELECT status, lease_token, lease_expires_at "
                        "FROM workflow_steps WHERE id = :step_id"
                    ),
                    {"step_id": claim.node_claim.step_id},
                )
                .mappings()
                .one()
            )
        assert dict(stale_step) == {
            "status": "CANCELLED",
            "lease_token": None,
            "lease_expires_at": None,
        }

        if stale_detection == "redelivery":
            assert recovery.recover_once() == (0, 0)
        with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
            recovery_events = [
                event
                for event in uow.outbox.list_for_aggregate(workflow_id)
                if event.envelope.event_type == "workflow.run.requested"
                and event.envelope.payload.get("reason") in {"expired_step_lease", "stale_workflow"}
            ]
        assert recovery_events == []
        if stale_detection == "stale-workflow":
            with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
                v2_analysis_event = next(
                    event
                    for event in uow.outbox.list_for_aggregate(product_brief_id)
                    if event.envelope.event_type == "product-brief.requested"
                    and event.envelope.aggregate_version
                    == replacement.json()["product_brief"]["version"]
                )
            assert runtime.process_event(v2_analysis_event.envelope.event_id) == "processed"
            with TestClient(create_app(settings)) as client:
                v2_brief = client.get(
                    f"/api/v1/product-briefs/{product_brief_id}",
                    headers=_read_headers(),
                )
            assert v2_brief.status_code == 200, v2_brief.text
            assert v2_brief.json()["state"] == "CONFIRMED"
            with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
                v2_continuation_event = next(
                    event
                    for event in uow.outbox.list_for_aggregate(workflow_id)
                    if event.envelope.event_type == "workflow.run.requested"
                    and event.envelope.payload.get("reason") == "product-brief-policy-confirmed"
                    and event.envelope.payload.get("product_brief_version_id")
                    == v2_brief.json()["current_version_id"]
                )
            assert runtime.process_event(v2_continuation_event.envelope.event_id) == "processed"
            with TestClient(create_app(settings)) as client:
                progressed = client.get(
                    f"/api/v1/workflows/{workflow_id}",
                    headers=_read_headers(),
                )
            assert progressed.status_code == 200, progressed.text
            assert progressed.json()["status"] == "AWAITING_PLAN_APPROVAL"
    finally:
        runtime.close()


def test_expired_active_product_brief_step_recovers_with_exact_authority(
    integration_database,
    integration_settings,
    seed_fixture_planner_prompt,
) -> None:
    settings = _settings(integration_settings)
    seed_fixture_planner_prompt(integration_database, workspace_id=WORKSPACE_ID)
    (
        continuation_event,
        workflow_id,
        _product_brief_id,
        _product_id,
        _asset_version_id,
        executor,
    ) = _prepare_unconsumed_product_brief_continuation(
        database=integration_database,
        settings=settings,
        confirmation_source="HUMAN",
    )
    version_id = continuation_event.envelope.payload["subject_id"]
    version_number = continuation_event.envelope.payload["subject_version"]
    continuation = ProductBriefContinuation(
        workspace_id=WORKSPACE_ID,
        product_brief_version_id=version_id,
        product_brief_version_number=version_number,
        approval_id=continuation_event.envelope.payload["approval_id"],
    )
    crashed_runtime = WorkerRuntime.build(
        settings,
        operation_executors={
            OperationKind.PRODUCT_BRIEF_ANALYSIS: executor,
        },
    )
    assert crashed_runtime.lifecycle is not None
    try:
        crashed_claim = crashed_runtime.lifecycle.claim_product_brief_continuation(
            workflow_id=workflow_id,
            expected_workflow_version=continuation_event.envelope.aggregate_version,
            continuation=continuation,
            lease_owner="expired-product-brief-worker",
            trace_id=continuation_event.envelope.trace_id,
        )
        assert crashed_claim.node_claim is not None
        assert crashed_claim.node_claim.lease_token is not None
        original_lease_token = crashed_claim.node_claim.lease_token
        with integration_database.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE outbox_events SET published_at = CURRENT_TIMESTAMP(6) "
                    "WHERE id = :event_id"
                ),
                {"event_id": continuation_event.envelope.event_id},
            )
            connection.execute(
                text(
                    "UPDATE workflow_steps "
                    "SET lease_expires_at = CURRENT_TIMESTAMP(6) - INTERVAL 1 SECOND "
                    "WHERE id = :step_id"
                ),
                {"step_id": crashed_claim.node_claim.step_id},
            )
    finally:
        crashed_runtime.close()

    recovery = RecoveryService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(integration_database.session_factory),
        batch_size=20,
        stale_after=timedelta(days=1),
    )
    assert recovery.recover_once() == (1, 0)
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        recovery_event = next(
            event
            for event in uow.outbox.list_for_aggregate(workflow_id)
            if event.envelope.event_type == "workflow.run.requested"
            and event.envelope.payload.get("reason") == "expired_step_lease"
        )
    assert recovery_event.envelope.payload["product_brief_version_id"] == version_id
    assert recovery_event.envelope.payload["product_brief_version_number"] == version_number
    assert "initial_step_lease_token" not in recovery_event.envelope.payload
    assert recovery_event.envelope.trace_id == continuation_event.envelope.trace_id

    restarted_runtime = WorkerRuntime.build(
        settings,
        operation_executors={
            OperationKind.PRODUCT_BRIEF_ANALYSIS: executor,
        },
    )
    recovery_preclaim: dict[str, str | None] = {}
    original_agent_run = restarted_runtime.agent.run

    def capture_recovery_preclaim(**kwargs):
        recovery_preclaim["step_id"] = kwargs.get("preclaimed_step_id")
        recovery_preclaim["lease_token"] = kwargs.get("preclaimed_lease_token")
        return original_agent_run(**kwargs)

    restarted_runtime.agent.run = capture_recovery_preclaim
    try:
        assert restarted_runtime.process_event(recovery_event.envelope.event_id) == "processed"
    finally:
        restarted_runtime.close()

    with TestClient(create_app(settings)) as client:
        progressed = client.get(
            f"/api/v1/workflows/{workflow_id}",
            headers=_read_headers(),
        )
    assert progressed.status_code == 200, progressed.text
    assert progressed.json()["status"] == "AWAITING_PLAN_APPROVAL"
    recovered_step = next(
        step
        for step in progressed.json()["steps"]
        if step["id"] == crashed_claim.node_claim.step_id
    )
    assert recovered_step["status"] == "SUCCEEDED"
    assert recovered_step["attempt_count"] == 2
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        recovered_node_events = [
            event
            for event in uow.outbox.list_for_aggregate(workflow_id)
            if event.envelope.event_type
            in {
                "workflow.node.started",
                "workflow.node.completed",
            }
        ]
    assert recovered_node_events
    assert {event.envelope.trace_id for event in recovered_node_events} == {
        continuation_event.envelope.trace_id
    }
    with integration_database.engine.connect() as connection:
        persisted_authority = connection.scalar(
            text("SELECT input_json FROM workflow_steps WHERE id = :step_id"),
            {"step_id": crashed_claim.node_claim.step_id},
        )
    if isinstance(persisted_authority, str):
        persisted_authority = json.loads(persisted_authority)
    persisted_generation = persisted_authority["product_brief_generation"]
    assert "initial_step_lease_token" not in persisted_generation
    assert persisted_generation["checkpoint_generation"] != product_brief_checkpoint_generation(
        workspace_id=WORKSPACE_ID,
        product_brief_version_id=version_id,
        initial_step_id=crashed_claim.node_claim.step_id,
        initial_step_lease_token=original_lease_token,
    )
    assert recovery_preclaim["step_id"] == crashed_claim.node_claim.step_id
    recovered_lease_token = recovery_preclaim["lease_token"]
    assert isinstance(recovered_lease_token, str) and recovered_lease_token
    checkpoint_history = list(
        MySQLCheckpointSaver(integration_database.session_factory).list(
            {"configurable": {"thread_id": workflow_id}}
        )
    )
    assert checkpoint_history
    serialized_history = repr(
        [
            (
                saved.config,
                saved.checkpoint,
                saved.metadata,
                saved.parent_config,
                saved.pending_writes,
            )
            for saved in checkpoint_history
        ]
    )
    assert recovered_lease_token not in serialized_history
    assert "initial_step_lease_token" not in serialized_history


def test_stale_product_brief_workflow_recovers_original_checkpoint_generation(
    integration_database,
    integration_settings,
    seed_fixture_planner_prompt,
) -> None:
    settings = _settings(integration_settings)
    seed_fixture_planner_prompt(integration_database, workspace_id=WORKSPACE_ID)
    (
        continuation_event,
        workflow_id,
        _product_brief_id,
        _product_id,
        _asset_version_id,
        executor,
    ) = _prepare_unconsumed_product_brief_continuation(
        database=integration_database,
        settings=settings,
        confirmation_source="POLICY",
    )
    version_id = continuation_event.envelope.payload["product_brief_version_id"]
    version_number = continuation_event.envelope.payload["product_brief_version_number"]
    with integration_database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE outbox_events SET published_at = CURRENT_TIMESTAMP(6) WHERE id = :event_id"
            ),
            {"event_id": continuation_event.envelope.event_id},
        )

    crashed_runtime = WorkerRuntime.build(
        settings,
        operation_executors={
            OperationKind.PRODUCT_BRIEF_ANALYSIS: executor,
        },
    )
    assert crashed_runtime.lifecycle is not None
    original_complete_node = crashed_runtime.lifecycle.complete_node

    def crash_after_retrieval_commit(**kwargs):
        completed_version = original_complete_node(**kwargs)
        if kwargs["next_node"] == "create_plan":
            raise SimulatedProcessDeath
        return completed_version

    crashed_runtime.lifecycle.complete_node = crash_after_retrieval_commit
    try:
        with pytest.raises(SimulatedProcessDeath):
            crashed_runtime.process_event(continuation_event.envelope.event_id)
    finally:
        crashed_runtime.close()

    with integration_database.engine.begin() as connection:
        crashed_workflow = (
            connection.execute(
                text(
                    "SELECT status, current_node FROM workflows "
                    "WHERE workspace_id = :workspace_id AND id = :workflow_id"
                ),
                {"workspace_id": WORKSPACE_ID, "workflow_id": workflow_id},
            )
            .mappings()
            .one()
        )
        assert dict(crashed_workflow) == {
            "status": "PLANNING",
            "current_node": "create_plan",
        }
        connection.execute(
            text(
                "UPDATE workflows "
                "SET updated_at = CURRENT_TIMESTAMP(6) - INTERVAL 2 DAY "
                "WHERE id = :workflow_id"
            ),
            {"workflow_id": workflow_id},
        )

    recovery = RecoveryService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(integration_database.session_factory),
        batch_size=20,
        stale_after=timedelta(days=1),
    )
    assert recovery.recover_once() == (0, 1)
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        recovery_event = next(
            event
            for event in uow.outbox.list_for_aggregate(workflow_id)
            if event.envelope.event_type == "workflow.run.requested"
            and event.envelope.payload.get("reason") == "stale_workflow"
        )
    assert recovery_event.envelope.payload["product_brief_version_id"] == version_id
    assert recovery_event.envelope.payload["product_brief_version_number"] == version_number

    restarted_runtime = WorkerRuntime.build(
        settings,
        operation_executors={
            OperationKind.PRODUCT_BRIEF_ANALYSIS: executor,
        },
    )
    try:
        assert restarted_runtime.process_event(recovery_event.envelope.event_id) == "processed"
    finally:
        restarted_runtime.close()

    with TestClient(create_app(settings)) as client:
        progressed = client.get(
            f"/api/v1/workflows/{workflow_id}",
            headers=_read_headers(),
        )
    assert progressed.status_code == 200, progressed.text
    assert progressed.json()["status"] == "AWAITING_PLAN_APPROVAL"
    assert progressed.json()["current_node"] == "approve_plan"


def test_stale_workflow_with_expired_product_brief_generation_converges_without_recovery(
    integration_database,
    integration_settings,
) -> None:
    settings = _settings(integration_settings)
    (
        continuation_event,
        workflow_id,
        product_brief_id,
        _product_id,
        _asset_version_id,
        executor,
    ) = _prepare_unconsumed_product_brief_continuation(
        database=integration_database,
        settings=settings,
        confirmation_source="POLICY",
    )
    continuation = ProductBriefContinuation(
        workspace_id=WORKSPACE_ID,
        product_brief_version_id=continuation_event.envelope.payload["product_brief_version_id"],
        product_brief_version_number=continuation_event.envelope.payload[
            "product_brief_version_number"
        ],
        approval_id=None,
    )
    with integration_database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE outbox_events SET published_at = CURRENT_TIMESTAMP(6) WHERE id = :event_id"
            ),
            {"event_id": continuation_event.envelope.event_id},
        )

    crashed_runtime = WorkerRuntime.build(
        settings,
        operation_executors={
            OperationKind.PRODUCT_BRIEF_ANALYSIS: executor,
        },
    )
    assert crashed_runtime.lifecycle is not None
    original_complete_node = crashed_runtime.lifecycle.complete_node

    def crash_after_retrieval_commit(**kwargs):
        completed_version = original_complete_node(**kwargs)
        if kwargs["next_node"] == "create_plan":
            raise SimulatedProcessDeath
        return completed_version

    crashed_runtime.lifecycle.complete_node = crash_after_retrieval_commit
    try:
        with pytest.raises(SimulatedProcessDeath):
            crashed_runtime.process_event(continuation_event.envelope.event_id)
        with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
            workflow = uow.workflows.get(workflow_id)
        assert workflow is not None
        create_plan_claim = crashed_runtime.lifecycle.begin_node(
            workflow_id=workflow_id,
            expected_workflow_version=workflow.version,
            step_key="create_plan:0",
            step_type=StepType.CREATE_PLAN,
            running_state=WorkflowStatus.PLANNING,
            node_name="create_plan",
            lease_owner="expired-generation-create-plan-worker",
            trace_id=continuation_event.envelope.trace_id,
            product_brief_continuation=continuation,
        )
        assert create_plan_claim.lease_token is not None
    finally:
        crashed_runtime.close()

    with integration_database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE product_briefs "
                "SET retention_deadline = CURRENT_TIMESTAMP(6) - INTERVAL 1 SECOND "
                "WHERE workspace_id = :workspace_id AND id = :product_brief_id"
            ),
            {
                "workspace_id": WORKSPACE_ID,
                "product_brief_id": product_brief_id,
            },
        )
        connection.execute(
            text(
                "UPDATE workflows "
                "SET updated_at = CURRENT_TIMESTAMP(6) - INTERVAL 2 DAY "
                "WHERE workspace_id = :workspace_id AND id = :workflow_id"
            ),
            {"workspace_id": WORKSPACE_ID, "workflow_id": workflow_id},
        )
        workflow_before_recovery = (
            connection.execute(
                text(
                    "SELECT status, version, updated_at FROM workflows "
                    "WHERE workspace_id = :workspace_id AND id = :workflow_id"
                ),
                {"workspace_id": WORKSPACE_ID, "workflow_id": workflow_id},
            )
            .mappings()
            .one()
        )
        before_inbox = connection.scalar(text("SELECT COUNT(*) FROM inbox_messages"))
        before_dead_letters = connection.scalar(text("SELECT COUNT(*) FROM dead_letter_messages"))

    recovery = RecoveryService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(integration_database.session_factory),
        batch_size=20,
        stale_after=timedelta(days=1),
    )
    assert recovery.recover_once() == (0, 1)
    assert recovery.recover_once() == (0, 0)

    with integration_database.engine.connect() as connection:
        create_plan_status = connection.scalar(
            text("SELECT status FROM workflow_steps WHERE id = :step_id"),
            {"step_id": create_plan_claim.step_id},
        )
        recovery_event_count = connection.scalar(
            text(
                "SELECT COUNT(*) FROM outbox_events "
                "WHERE aggregate_id = :workflow_id "
                "AND event_type = 'workflow.run.requested' "
                "AND JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.reason')) = 'stale_workflow'"
            ),
            {"workflow_id": workflow_id},
        )
        after_inbox = connection.scalar(text("SELECT COUNT(*) FROM inbox_messages"))
        after_dead_letters = connection.scalar(text("SELECT COUNT(*) FROM dead_letter_messages"))
        workflow_after_recovery = (
            connection.execute(
                text(
                    "SELECT status, version, updated_at FROM workflows "
                    "WHERE workspace_id = :workspace_id AND id = :workflow_id"
                ),
                {"workspace_id": WORKSPACE_ID, "workflow_id": workflow_id},
            )
            .mappings()
            .one()
        )
    assert create_plan_status == "CANCELLED"
    assert recovery_event_count == 0
    assert after_inbox == before_inbox
    assert after_dead_letters == before_dead_letters
    assert workflow_after_recovery["status"] == workflow_before_recovery["status"]
    assert workflow_after_recovery["version"] == workflow_before_recovery["version"]
    assert workflow_after_recovery["updated_at"] > workflow_before_recovery["updated_at"]


@pytest.mark.parametrize("recovery_path", ["expired-step", "stale-workflow"])
def test_product_brief_recovery_fail_closes_future_retention_deadline_mismatch(
    integration_database,
    integration_settings,
    recovery_path: str,
) -> None:
    settings = _settings(integration_settings)
    (
        continuation_event,
        workflow_id,
        product_brief_id,
        _product_id,
        _asset_version_id,
        executor,
    ) = _prepare_unconsumed_product_brief_continuation(
        database=integration_database,
        settings=settings,
        confirmation_source="POLICY",
    )
    step_id: str | None = None
    if recovery_path == "expired-step":
        runtime = WorkerRuntime.build(
            settings,
            operation_executors={
                OperationKind.PRODUCT_BRIEF_ANALYSIS: executor,
            },
        )
        assert runtime.lifecycle is not None
        try:
            claim = runtime.lifecycle.claim_product_brief_continuation(
                workflow_id=workflow_id,
                expected_workflow_version=continuation_event.envelope.aggregate_version,
                continuation=ProductBriefContinuation(
                    workspace_id=WORKSPACE_ID,
                    product_brief_version_id=continuation_event.envelope.payload[
                        "product_brief_version_id"
                    ],
                    product_brief_version_number=continuation_event.envelope.payload[
                        "product_brief_version_number"
                    ],
                    approval_id=None,
                ),
                lease_owner="deadline-binding-recovery-worker",
                trace_id=continuation_event.envelope.trace_id,
            )
            assert claim.node_claim is not None
            step_id = claim.node_claim.step_id
        finally:
            runtime.close()

    with integration_database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE outbox_events SET published_at = CURRENT_TIMESTAMP(6) WHERE id = :event_id"
            ),
            {"event_id": continuation_event.envelope.event_id},
        )
        connection.execute(
            text(
                "UPDATE product_briefs "
                "SET retention_deadline = retention_deadline - INTERVAL 1 SECOND "
                "WHERE workspace_id = :workspace AND id = :product_brief_id"
            ),
            {
                "workspace": WORKSPACE_ID,
                "product_brief_id": product_brief_id,
            },
        )
        if recovery_path == "expired-step":
            assert step_id is not None
            connection.execute(
                text(
                    "UPDATE workflow_steps "
                    "SET lease_expires_at = CURRENT_TIMESTAMP(6) - INTERVAL 1 SECOND "
                    "WHERE id = :step_id"
                ),
                {"step_id": step_id},
            )
        else:
            connection.execute(
                text(
                    "UPDATE workflows "
                    "SET updated_at = CURRENT_TIMESTAMP(6) - INTERVAL 2 DAY "
                    "WHERE workspace_id = :workspace AND id = :workflow_id"
                ),
                {
                    "workspace": WORKSPACE_ID,
                    "workflow_id": workflow_id,
                },
            )
    before = _continuation_effect_snapshot(
        integration_database,
        workflow_id=workflow_id,
        product_brief_id=product_brief_id,
    )

    recovery = RecoveryService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(integration_database.session_factory),
        batch_size=20,
        stale_after=timedelta(days=1),
    )
    expected = (1, 0) if recovery_path == "expired-step" else (0, 1)
    assert recovery.recover_once() == expected
    assert recovery.recover_once() == (0, 0)

    after = _continuation_effect_snapshot(
        integration_database,
        workflow_id=workflow_id,
        product_brief_id=product_brief_id,
    )
    assert after == before
    with integration_database.engine.connect() as connection:
        recovery_event_count = connection.scalar(
            text(
                "SELECT COUNT(*) FROM outbox_events "
                "WHERE aggregate_id = :workflow_id "
                "AND event_type = 'workflow.run.requested' "
                "AND JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.reason')) "
                "IN ('expired_step_lease', 'stale_workflow')"
            ),
            {"workflow_id": workflow_id},
        )
        step_status = (
            None
            if step_id is None
            else connection.scalar(
                text("SELECT status FROM workflow_steps WHERE id = :step_id"),
                {"step_id": step_id},
            )
        )
    assert recovery_event_count == 0
    if step_id is not None:
        assert step_status == "CANCELLED"


def test_inflight_v1_completion_cannot_overwrite_confirmed_v2_continuation(
    integration_database,
    integration_settings,
    seed_fixture_planner_prompt,
) -> None:
    settings = _settings(integration_settings)
    seed_fixture_planner_prompt(integration_database, workspace_id=WORKSPACE_ID)
    (
        v1_event,
        workflow_id,
        product_brief_id,
        product_id,
        asset_version_id,
        executor,
    ) = _prepare_unconsumed_product_brief_continuation(
        database=integration_database,
        settings=settings,
        confirmation_source="POLICY",
    )
    v1_continuation = ProductBriefContinuation(
        workspace_id=WORKSPACE_ID,
        product_brief_version_id=v1_event.envelope.payload["product_brief_version_id"],
        product_brief_version_number=v1_event.envelope.payload["product_brief_version_number"],
        approval_id=None,
    )
    runtime = WorkerRuntime.build(
        settings,
        operation_executors={
            OperationKind.PRODUCT_BRIEF_ANALYSIS: executor,
        },
    )
    assert runtime.lifecycle is not None
    try:
        v1_claim = runtime.lifecycle.claim_product_brief_continuation(
            workflow_id=workflow_id,
            expected_workflow_version=v1_event.envelope.aggregate_version,
            continuation=v1_continuation,
            lease_owner="inflight-v1-worker",
            trace_id=v1_event.envelope.trace_id,
        )
        assert v1_claim.stale_reason is None
        assert v1_claim.node_claim is not None
        assert v1_claim.node_claim.lease_token is not None

        with TestClient(create_app(settings)) as client:
            workflow = client.get(
                f"/api/v1/workflows/{workflow_id}",
                headers=_read_headers(),
            )
            assert workflow.status_code == 200, workflow.text
            replacement = client.post(
                "/api/v1/product-briefs:analyze",
                headers=_mutation_headers("inflight-superseding-analysis-0002"),
                json={
                    "workflow_id": workflow_id,
                    "product_id": product_id,
                    "asset_version_ids": [asset_version_id],
                    "expected_workflow_version": workflow.json()["version"],
                },
            )
            assert replacement.status_code == 202, replacement.text
            with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
                v2_analysis_event = next(
                    event
                    for event in uow.outbox.list_for_aggregate(product_brief_id)
                    if event.envelope.event_type == "product-brief.requested"
                    and event.envelope.event_id != v1_event.envelope.event_id
                    and event.envelope.aggregate_version
                    == replacement.json()["product_brief"]["version"]
                )
            assert runtime.process_event(v2_analysis_event.envelope.event_id) == "processed"

            v2_brief = client.get(
                f"/api/v1/product-briefs/{product_brief_id}",
                headers=_read_headers(),
            )
            before_stale_completion = client.get(
                f"/api/v1/workflows/{workflow_id}",
                headers=_read_headers(),
            )
            assert v2_brief.status_code == 200, v2_brief.text
            assert v2_brief.json()["state"] == "CONFIRMED"
            assert v2_brief.json()["current_version_id"] != v1_continuation.product_brief_version_id
            assert before_stale_completion.status_code == 200
            assert before_stale_completion.json()["status"] == "RETRIEVING"

            with pytest.raises(StaleProductBriefContinuation, match="superseded"):
                runtime.lifecycle.complete_node(
                    workflow_id=workflow_id,
                    step_id=v1_claim.node_claim.step_id,
                    lease_token=v1_claim.node_claim.lease_token,
                    expected_workflow_version=v1_claim.node_claim.workflow_version,
                    target_state=WorkflowStatus.PLANNING,
                    next_node="create_plan",
                    trace_id=v1_event.envelope.trace_id,
                    output_data={"retrieved_asset_refs": ["stale-v1-ref"]},
                    product_brief_continuation=v1_continuation,
                )

            after_stale_completion = client.get(
                f"/api/v1/workflows/{workflow_id}",
                headers=_read_headers(),
            )
            assert after_stale_completion.status_code == 200
            assert after_stale_completion.json()["status"] == "RETRIEVING"
            assert (
                after_stale_completion.json()["version"]
                == before_stale_completion.json()["version"]
            )
            stale_step = next(
                step
                for step in after_stale_completion.json()["steps"]
                if step["id"] == v1_claim.node_claim.step_id
            )
            assert stale_step["status"] == "CANCELLED"
            assert stale_step["output_data"] is None
            with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
                v2_event = next(
                    event
                    for event in uow.outbox.list_for_aggregate(workflow_id)
                    if event.envelope.event_type == "workflow.run.requested"
                    and event.envelope.payload.get("reason") == "product-brief-policy-confirmed"
                    and event.envelope.payload.get("product_brief_version_id")
                    == v2_brief.json()["current_version_id"]
                )

        assert runtime.process_event(v2_event.envelope.event_id) == "processed"
        with TestClient(create_app(settings)) as client:
            progressed = client.get(
                f"/api/v1/workflows/{workflow_id}",
                headers=_read_headers(),
            )
        assert progressed.status_code == 200, progressed.text
        assert progressed.json()["status"] == "AWAITING_PLAN_APPROVAL"
        assert progressed.json()["current_node"] == "approve_plan"
        assert (f"retrieve_references:product-brief:{v2_brief.json()['current_version_id']}") in {
            step["step_key"] for step in progressed.json()["steps"]
        }
    finally:
        runtime.close()


@pytest.mark.parametrize(
    ("authority_mutation", "expected_reason"),
    [
        ("workflow_type", "product_brief_workflow_type_mismatch"),
        ("product_id", "product_brief_product_mismatch"),
    ],
)
def test_product_brief_worker_fails_closed_on_workflow_binding_drift(
    integration_database,
    integration_settings,
    authority_mutation: str,
    expected_reason: str,
) -> None:
    settings = _settings(integration_settings)
    (
        continuation_event,
        workflow_id,
        product_brief_id,
        _,
        _,
        executor,
    ) = _prepare_unconsumed_product_brief_continuation(
        database=integration_database,
        settings=settings,
        confirmation_source="POLICY",
    )
    with integration_database.engine.begin() as connection:
        if authority_mutation == "workflow_type":
            connection.execute(
                text(
                    "UPDATE workflows SET workflow_type = 'FIXTURE_IMAGE_GENERATION' "
                    "WHERE workspace_id = :workspace AND id = :workflow_id"
                ),
                {
                    "workflow_id": workflow_id,
                    "workspace": WORKSPACE_ID,
                },
            )
        else:
            connection.execute(
                text(
                    "UPDATE workflows SET input_json = JSON_SET("
                    "input_json, '$.product_id', :replacement_product_id) "
                    "WHERE workspace_id = :workspace AND id = :workflow_id"
                ),
                {
                    "replacement_product_id": new_uuid7(),
                    "workflow_id": workflow_id,
                    "workspace": WORKSPACE_ID,
                },
            )
    before = _continuation_effect_snapshot(
        integration_database,
        workflow_id=workflow_id,
        product_brief_id=product_brief_id,
    )
    worker = WorkerRuntime.build(
        settings,
        operation_executors={
            OperationKind.PRODUCT_BRIEF_ANALYSIS: executor,
        },
    )
    try:
        assert worker.process_event(continuation_event.envelope.event_id) == "dead-lettered"
    finally:
        worker.close()
    after = _continuation_effect_snapshot(
        integration_database,
        workflow_id=workflow_id,
        product_brief_id=product_brief_id,
    )
    assert {key: value for key, value in after.items() if key != "dead_letters"} == {
        key: value for key, value in before.items() if key != "dead_letters"
    }
    assert after["dead_letters"] == before["dead_letters"] + 1
    with integration_database.engine.connect() as connection:
        dead_letter = (
            connection.execute(
                text(
                    "SELECT reason, attempt_count FROM dead_letter_messages "
                    "WHERE consumer = :consumer AND message_id = :message_id"
                ),
                {
                    "consumer": settings.worker_consumer_name,
                    "message_id": continuation_event.envelope.event_id,
                },
            )
            .mappings()
            .one()
        )
        inbox_status = connection.scalar(
            text(
                "SELECT status FROM inbox_messages WHERE consumer = :consumer "
                "AND message_id = :message_id"
            ),
            {
                "consumer": settings.worker_consumer_name,
                "message_id": continuation_event.envelope.event_id,
            },
        )
    assert dict(dead_letter) == {
        "reason": expected_reason,
        "attempt_count": 1,
    }
    assert inbox_status == "DEAD"


def test_commerce_workflow_rejects_analysis_for_a_different_product(
    integration_database,
    integration_settings,
) -> None:
    settings = _settings(integration_settings)
    _, product_id, asset_version_id = _seed_authorized_source(integration_database)
    app = create_app(settings)

    with TestClient(app) as client:
        workflow_created = client.post(
            "/api/v1/workflows",
            headers=_mutation_headers("commerce-product-binding-workflow-0001"),
            json={
                "workflow_type": "COMMERCE_IMAGE_GENERATION",
                "input_data": {
                    "schema_version": "1.0",
                    "product_id": new_uuid7(),
                },
                "retention_hours": 72,
            },
        )
        assert workflow_created.status_code == 202, workflow_created.text

        requested = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers("commerce-product-binding-analysis-0001"),
            json={
                "workflow_id": workflow_created.json()["id"],
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": workflow_created.json()["version"],
            },
        )

    assert requested.status_code == 409, requested.text
    assert requested.json()["code"] == "INVALID_TRANSITION"
    assert "workflow product" in requested.json()["message"]
    with integration_database.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM product_briefs WHERE workflow_id = :workflow_id"),
                {"workflow_id": workflow_created.json()["id"]},
            ).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM durable_operations WHERE target_type = 'product_brief'")
            ).scalar_one()
            == 0
        )


def test_product_brief_analysis_rejects_non_commerce_workflow(
    integration_database,
    integration_settings,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(integration_database)
    with integration_database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE workflows SET workflow_type = 'FIXTURE_IMAGE_GENERATION' "
                "WHERE workspace_id = :workspace AND id = :workflow_id"
            ),
            {
                "workflow_id": workflow_id,
                "workspace": WORKSPACE_ID,
            },
        )

    with TestClient(create_app(settings)) as client:
        requested = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers("non-commerce-product-brief-denied-0001"),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": 3,
            },
        )

    assert requested.status_code == 409, requested.text
    assert requested.json()["code"] == "INVALID_TRANSITION"
    assert "COMMERCE_IMAGE_GENERATION" in requested.json()["message"]
    with integration_database.engine.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT COUNT(*) FROM product_briefs "
                    "WHERE workspace_id = :workspace AND workflow_id = :workflow_id"
                ),
                {
                    "workflow_id": workflow_id,
                    "workspace": WORKSPACE_ID,
                },
            )
            == 0
        )


def _analysis_command_effect_snapshot(database, *, workflow_id: str) -> dict[str, object]:
    with database.engine.connect() as connection:
        workflow = (
            connection.execute(
                text(
                    "SELECT status, retention_status, current_node, version, expires_at "
                    "FROM workflows WHERE workspace_id = :workspace AND id = :workflow_id"
                ),
                {
                    "workspace": WORKSPACE_ID,
                    "workflow_id": workflow_id,
                },
            )
            .mappings()
            .one()
        )
        product_briefs = tuple(
            tuple(row)
            for row in connection.execute(
                text(
                    "SELECT id, state, current_version_id, confirmed_version_id, version, "
                    "retention_deadline, operation_id FROM product_briefs "
                    "WHERE workspace_id = :workspace AND workflow_id = :workflow_id "
                    "ORDER BY id"
                ),
                {
                    "workspace": WORKSPACE_ID,
                    "workflow_id": workflow_id,
                },
            ).all()
        )
        return {
            "workflow": dict(workflow),
            "product_briefs": product_briefs,
            "operations": connection.scalar(text("SELECT COUNT(*) FROM durable_operations")),
            "analyses": connection.scalar(
                text("SELECT COUNT(*) FROM product_brief_analysis_requests")
            ),
            "outbox": connection.scalar(text("SELECT COUNT(*) FROM outbox_events")),
            "audit": connection.scalar(text("SELECT COUNT(*) FROM audit_events")),
            "idempotency": connection.scalar(text("SELECT COUNT(*) FROM idempotency_keys")),
        }


def test_product_brief_analysis_rejects_legacy_workflow_after_task_deadline(
    integration_database,
    integration_settings,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(integration_database)
    with integration_database.engine.begin() as connection:
        database_now = connection.scalar(text("SELECT CURRENT_TIMESTAMP(6)"))
        assert isinstance(database_now, datetime)
        legacy_created_at = database_now - timedelta(hours=73)
        connection.execute(
            text(
                "UPDATE workflows "
                "SET created_at = :created_at, expires_at = :workflow_expires_at "
                "WHERE workspace_id = :workspace AND id = :workflow_id"
            ),
            {
                "created_at": legacy_created_at,
                "workflow_expires_at": legacy_created_at + timedelta(hours=168),
                "workspace": WORKSPACE_ID,
                "workflow_id": workflow_id,
            },
        )
    before = _analysis_command_effect_snapshot(
        integration_database,
        workflow_id=workflow_id,
    )

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers("legacy-expired-task-retention-0001"),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": 3,
            },
        )

    assert response.status_code == 410, response.text
    assert response.json()["code"] == "PRODUCT_BRIEF_RETENTION_EXPIRED"
    assert (
        _analysis_command_effect_snapshot(
            integration_database,
            workflow_id=workflow_id,
        )
        == before
    )


@pytest.mark.parametrize("retention_status", ["EXPIRING", "DELETING", "EXPIRED"])
def test_product_brief_analysis_rejects_revoked_workflow_retention_without_side_effects(
    integration_database,
    integration_settings,
    retention_status: str,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(integration_database)
    with integration_database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE workflows SET retention_status = :retention_status "
                "WHERE workspace_id = :workspace AND id = :workflow_id"
            ),
            {
                "retention_status": retention_status,
                "workspace": WORKSPACE_ID,
                "workflow_id": workflow_id,
            },
        )
    before = _analysis_command_effect_snapshot(
        integration_database,
        workflow_id=workflow_id,
    )

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers(f"revoked-retention-{retention_status.lower()}-0001"),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": 3,
            },
        )

    assert response.status_code == 410, response.text
    assert response.json()["code"] == "PRODUCT_BRIEF_RETENTION_EXPIRED"
    assert (
        _analysis_command_effect_snapshot(
            integration_database,
            workflow_id=workflow_id,
        )
        == before
    )


@pytest.mark.parametrize(
    ("authority_mutation", "expected_status", "expected_code"),
    [
        ("retention_status", 410, "PRODUCT_BRIEF_RETENTION_EXPIRED"),
        ("deadline_binding", 409, "VERSION_CONFLICT"),
    ],
)
def test_product_brief_analysis_idempotent_replay_revalidates_current_authority(
    integration_database,
    integration_settings,
    authority_mutation: str,
    expected_status: int,
    expected_code: str,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(integration_database)
    idempotency_key = f"analysis-replay-authority-{authority_mutation}-0001"
    payload = {
        "workflow_id": workflow_id,
        "product_id": product_id,
        "asset_version_ids": [asset_version_id],
        "expected_workflow_version": 3,
    }
    app = create_app(settings)
    with TestClient(app) as client:
        accepted = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers(idempotency_key),
            json=payload,
        )
        assert accepted.status_code == 202, accepted.text
        product_brief_id = accepted.json()["product_brief"]["id"]
        with integration_database.engine.begin() as connection:
            if authority_mutation == "retention_status":
                connection.execute(
                    text(
                        "UPDATE workflows SET retention_status = 'EXPIRING' "
                        "WHERE workspace_id = :workspace AND id = :workflow_id"
                    ),
                    {
                        "workspace": WORKSPACE_ID,
                        "workflow_id": workflow_id,
                    },
                )
            else:
                connection.execute(
                    text(
                        "UPDATE product_briefs "
                        "SET retention_deadline = retention_deadline - INTERVAL 1 SECOND "
                        "WHERE workspace_id = :workspace AND id = :product_brief_id"
                    ),
                    {
                        "workspace": WORKSPACE_ID,
                        "product_brief_id": product_brief_id,
                    },
                )
        before = _analysis_command_effect_snapshot(
            integration_database,
            workflow_id=workflow_id,
        )
        replay = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers(idempotency_key),
            json=payload,
        )

    assert replay.status_code == expected_status, replay.text
    assert replay.json()["code"] == expected_code
    assert "product_brief" not in replay.json()
    assert (
        _analysis_command_effect_snapshot(
            integration_database,
            workflow_id=workflow_id,
        )
        == before
    )


@pytest.mark.parametrize("retention_status", ["EXPIRING", "DELETING", "EXPIRED"])
def test_product_brief_reanalysis_rejects_revoked_workflow_retention_without_side_effects(
    integration_database,
    integration_settings,
    retention_status: str,
) -> None:
    settings = _settings(integration_settings)
    (
        _continuation_event,
        workflow_id,
        _product_brief_id,
        product_id,
        asset_version_id,
        _executor,
    ) = _prepare_unconsumed_product_brief_continuation(
        database=integration_database,
        settings=settings,
        confirmation_source="POLICY",
    )
    with integration_database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE workflows SET retention_status = :retention_status "
                "WHERE workspace_id = :workspace AND id = :workflow_id"
            ),
            {
                "retention_status": retention_status,
                "workspace": WORKSPACE_ID,
                "workflow_id": workflow_id,
            },
        )
        workflow_version = connection.scalar(
            text(
                "SELECT version FROM workflows "
                "WHERE workspace_id = :workspace AND id = :workflow_id"
            ),
            {
                "workspace": WORKSPACE_ID,
                "workflow_id": workflow_id,
            },
        )
    before = _analysis_command_effect_snapshot(
        integration_database,
        workflow_id=workflow_id,
    )

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers(f"revoked-reanalysis-{retention_status.lower()}-0001"),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": workflow_version,
            },
        )

    assert response.status_code == 410, response.text
    assert response.json()["code"] == "PRODUCT_BRIEF_RETENTION_EXPIRED"
    assert (
        _analysis_command_effect_snapshot(
            integration_database,
            workflow_id=workflow_id,
        )
        == before
    )


def test_product_brief_reanalysis_rejects_future_retention_deadline_mismatch(
    integration_database,
    integration_settings,
) -> None:
    settings = _settings(integration_settings)
    (
        _continuation_event,
        workflow_id,
        product_brief_id,
        product_id,
        asset_version_id,
        _executor,
    ) = _prepare_unconsumed_product_brief_continuation(
        database=integration_database,
        settings=settings,
        confirmation_source="POLICY",
    )
    with integration_database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE product_briefs "
                "SET retention_deadline = retention_deadline - INTERVAL 1 SECOND "
                "WHERE workspace_id = :workspace AND id = :product_brief_id"
            ),
            {
                "workspace": WORKSPACE_ID,
                "product_brief_id": product_brief_id,
            },
        )
        workflow_version = connection.scalar(
            text(
                "SELECT version FROM workflows "
                "WHERE workspace_id = :workspace AND id = :workflow_id"
            ),
            {
                "workspace": WORKSPACE_ID,
                "workflow_id": workflow_id,
            },
        )
    before = _analysis_command_effect_snapshot(
        integration_database,
        workflow_id=workflow_id,
    )

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers("reanalysis-deadline-binding-0001"),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": workflow_version,
            },
        )

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "VERSION_CONFLICT"
    assert (
        _analysis_command_effect_snapshot(
            integration_database,
            workflow_id=workflow_id,
        )
        == before
    )


def test_confirmed_product_brief_can_start_a_new_analysis_cycle(
    integration_database,
    integration_settings,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(integration_database)
    artifact_sink = MemoryArtifactSink()
    analyzer = SequentialAnalyzer(
        DeterministicVisionAnalyzer(
            scenario=DeterministicVisionScenario.SUCCESS,
            artifact_sink=artifact_sink,
        ),
        DeterministicVisionAnalyzer(
            scenario=DeterministicVisionScenario.CONFLICT,
            artifact_sink=artifact_sink,
        ),
    )
    executor = ProductBriefAnalysisExecutor(
        uow_factory=lambda: SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory),
        object_storage=ControlledReadStorage(),
        analyzer=analyzer,
        policy=ProductBriefPolicy.from_settings(settings),
        transfer_policy=VisionDataTransferPolicy.from_settings(settings),
        artifact_service=_artifact_service(integration_database, artifact_sink),
    )
    app = create_app(settings)

    with TestClient(app) as client:
        first = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers("brief-analysis-cycle-0001"),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": 3,
            },
        )
        assert first.status_code == 202, first.text
        first_body = first.json()
        with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
            first_event = next(
                event
                for event in uow.outbox.list_for_aggregate(first_body["product_brief"]["id"])
                if event.envelope.event_type == "product-brief.requested"
            )
        worker = WorkerRuntime.build(
            settings,
            operation_executors={
                OperationKind.PRODUCT_BRIEF_ANALYSIS: executor,
            },
        )
        try:
            assert worker.process_event(first_event.envelope.event_id) == "processed"
            first_confirmed = client.get(
                f"/api/v1/product-briefs/{first_body['product_brief']['id']}",
                headers=_read_headers(),
            )
            workflow = client.get(
                f"/api/v1/workflows/{workflow_id}",
                headers={"X-Workspace-Id": WORKSPACE_ID},
            )
            assert first_confirmed.status_code == 200, first_confirmed.text
            assert first_confirmed.json()["state"] == "CONFIRMED"
            assert workflow.json()["status"] == "RETRIEVING"
            first_version_id = first_confirmed.json()["current_version_id"]

            second = client.post(
                "/api/v1/product-briefs:analyze",
                headers=_mutation_headers("brief-analysis-cycle-0002"),
                json={
                    "workflow_id": workflow_id,
                    "product_id": product_id,
                    "asset_version_ids": [asset_version_id],
                    "expected_workflow_version": workflow.json()["version"],
                },
            )
            assert second.status_code == 202, second.text
            second_body = second.json()
            assert second_body["product_brief"]["id"] == (first_body["product_brief"]["id"])
            assert second_body["operation_id"] != first_body["operation_id"]
            assert second_body["product_brief"]["state"] == "DRAFT"
            assert second_body["product_brief"]["confirmed_version_id"] == first_version_id
            pending_history = client.get(
                f"/api/v1/product-briefs/{second_body['product_brief']['id']}/versions",
                headers=_read_headers(),
            )
            assert pending_history.status_code == 200, pending_history.text
            assert pending_history.json()["items"][0]["effective_state"] == "CONFIRMED"

            with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
                second_event = next(
                    event
                    for event in uow.outbox.list_for_aggregate(second_body["product_brief"]["id"])
                    if event.envelope.event_type == "product-brief.requested"
                    and event.envelope.payload["operation_id"] == second_body["operation_id"]
                )
            assert worker.process_event(second_event.envelope.event_id) == "processed"
        finally:
            worker.close()

        awaiting = client.get(
            f"/api/v1/product-briefs/{second_body['product_brief']['id']}",
            headers=_read_headers(),
        )
        history = client.get(
            f"/api/v1/product-briefs/{second_body['product_brief']['id']}/versions",
            headers=_read_headers(),
        )

    assert awaiting.status_code == 200, awaiting.text
    assert awaiting.json()["state"] == "AWAITING_CONFIRMATION"
    assert awaiting.json()["confirmed_version_id"] == first_version_id
    assert awaiting.json()["current_version_id"] != first_version_id
    assert [item["effective_state"] for item in history.json()["items"]] == [
        "AWAITING_CONFIRMATION",
        "CONFIRMED",
    ]


def test_old_operation_reconciliation_uses_its_immutable_output_after_reanalysis(
    integration_database,
    integration_settings,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(integration_database)
    analyzer = CountingAnalyzer(
        DeterministicVisionAnalyzer(
            scenario=DeterministicVisionScenario.SUCCESS,
            artifact_sink=MemoryArtifactSink(),
        )
    )
    executor = ProductBriefAnalysisExecutor(
        uow_factory=lambda: SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory),
        object_storage=ControlledReadStorage(),
        analyzer=analyzer,
        policy=ProductBriefPolicy.from_settings(settings),
        transfer_policy=VisionDataTransferPolicy.from_settings(settings),
        artifact_service=_artifact_service(integration_database),
    )
    app = create_app(settings)

    with TestClient(app) as client:
        first = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers("brief-old-operation-0001"),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": 3,
            },
        )
        assert first.status_code == 202, first.text
        first_body = first.json()
        operation_service = OperationApplicationService(
            uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
        )
        old_operation = operation_service.get(
            workspace_id=WORKSPACE_ID,
            operation_id=first_body["operation_id"],
        )
        claimed_at = datetime.now(UTC)
        lease_token = operation_service.claim(
            workspace_id=WORKSPACE_ID,
            operation_id=old_operation.id,
            owner="crashing-product-brief-worker",
            lease_duration=timedelta(seconds=30),
            now=claimed_at,
        )
        running = operation_service.start(
            workspace_id=WORKSPACE_ID,
            operation_id=old_operation.id,
            lease_token=lease_token,
            now=claimed_at,
        )
        first_result = executor.execute(OperationExecutionRequest.from_operation(running))
        assert first_result.output_ref is not None
        first_version_id = first_result.output_ref.rsplit("/", 1)[-1]
        still_running = operation_service.get(
            workspace_id=WORKSPACE_ID,
            operation_id=old_operation.id,
        )
        assert still_running.state == OperationState.RUNNING
        confirmed = client.get(
            f"/api/v1/product-briefs/{first_body['product_brief']['id']}",
            headers=_read_headers(),
        )
        workflow = client.get(
            f"/api/v1/workflows/{workflow_id}",
            headers={"X-Workspace-Id": WORKSPACE_ID},
        )
        assert confirmed.json()["state"] == "CONFIRMED"
        assert confirmed.json()["current_version_id"] == first_version_id
        assert workflow.json()["status"] == "RETRIEVING"

        second = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers("brief-old-operation-0002"),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": workflow.json()["version"],
            },
        )
        assert second.status_code == 202, second.text
        second_body = second.json()
        assert second_body["operation_id"] != old_operation.id
        assert second_body["product_brief"]["state"] == "DRAFT"

    assert running.lease_expires_at is not None
    scanner = OperationRecoveryService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory),
        batch_size=1,
        reconciliation_max_elapsed=timedelta(minutes=5),
    )
    assert scanner.recover_once(now=running.lease_expires_at) == 1
    recovery_clock = MutableClock(running.lease_expires_at + timedelta(microseconds=1))
    recovery_worker = DurableOperationWorker(
        operations=operation_service,
        execution=OperationExecutionBoundary(
            executor=executor,
            transaction_active=is_unit_of_work_active,
        ),
        owner="restarted-product-brief-worker",
        lease_duration=timedelta(seconds=30),
        clock=recovery_clock,
    )

    recovered = recovery_worker.execute(
        workspace_id=WORKSPACE_ID,
        operation_id=old_operation.id,
    )

    assert recovered.state == OperationState.SUCCEEDED
    assert recovered.reconciliation_outcome.value == "CONFIRMED_SUCCESS"
    assert recovered.output_ref == first_result.output_ref
    assert recovered.output_ref.endswith(first_version_id)
    assert analyzer.call_count == 1


def test_cancelled_workflow_before_worker_consumption_makes_no_analyzer_call(
    integration_database,
    integration_settings,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(integration_database)
    analyzer = CountingAnalyzer(
        DeterministicVisionAnalyzer(
            scenario=DeterministicVisionScenario.SUCCESS,
            artifact_sink=MemoryArtifactSink(),
        )
    )
    executor = ProductBriefAnalysisExecutor(
        uow_factory=lambda: SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory),
        object_storage=ControlledReadStorage(),
        analyzer=analyzer,
        policy=ProductBriefPolicy.from_settings(settings),
        transfer_policy=VisionDataTransferPolicy.from_settings(settings),
        artifact_service=_artifact_service(integration_database),
    )
    app = create_app(settings)

    with TestClient(app) as client:
        requested = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers("brief-cancel-before-consume-0001"),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": 3,
            },
        )
        assert requested.status_code == 202, requested.text
        body = requested.json()
        cancelled = client.post(
            f"/api/v1/workflows/{workflow_id}:cancel",
            headers=_mutation_headers("brief-workflow-cancel-0001"),
            json={"expected_workflow_version": 3},
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "CANCELLED"
        with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
            requested_event = next(
                event
                for event in uow.outbox.list_for_aggregate(body["product_brief"]["id"])
                if event.envelope.event_type == "product-brief.requested"
            )
        worker = WorkerRuntime.build(
            settings,
            operation_executors={
                OperationKind.PRODUCT_BRIEF_ANALYSIS: executor,
            },
        )
        try:
            assert worker.process_event(requested_event.envelope.event_id) == "processed"
        finally:
            worker.close()
        operation = client.get(
            f"/api/v1/operations/{body['operation_id']}",
            headers=_read_headers(),
        )

    assert analyzer.call_count == 0
    assert operation.status_code == 200, operation.text
    assert operation.json()["state"] == "FAILED"
    assert operation.json()["error"]["code"] == "PRODUCT_BRIEF_WORKFLOW_NOT_EXECUTABLE"


@pytest.mark.parametrize(
    "authority_mutation",
    ["workflow_type", "product_id", "retention_status"],
)
def test_workflow_authority_drift_before_worker_consumption_makes_no_analyzer_call(
    integration_database,
    integration_settings,
    authority_mutation: str,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(integration_database)
    analyzer = CountingAnalyzer(
        DeterministicVisionAnalyzer(
            scenario=DeterministicVisionScenario.SUCCESS,
            artifact_sink=MemoryArtifactSink(),
        )
    )
    executor = ProductBriefAnalysisExecutor(
        uow_factory=lambda: SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory),
        object_storage=ControlledReadStorage(),
        analyzer=analyzer,
        policy=ProductBriefPolicy.from_settings(settings),
        transfer_policy=VisionDataTransferPolicy.from_settings(settings),
        artifact_service=_artifact_service(integration_database),
    )

    with TestClient(create_app(settings)) as client:
        requested = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers(
                f"brief-authority-drift-before-consume-{authority_mutation}-0001"
            ),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": 3,
            },
        )
        assert requested.status_code == 202, requested.text
        body = requested.json()
        with integration_database.engine.begin() as connection:
            if authority_mutation == "workflow_type":
                mutation = "workflow_type = 'FIXTURE_IMAGE_GENERATION'"
                parameters = {}
            elif authority_mutation == "product_id":
                mutation = (
                    "input_json = JSON_SET(input_json, '$.product_id', :replacement_product_id)"
                )
                parameters = {"replacement_product_id": new_uuid7()}
            else:
                mutation = "retention_status = 'EXPIRING'"
                parameters = {}
            connection.execute(
                text(
                    f"UPDATE workflows SET {mutation} "
                    "WHERE workspace_id = :workspace AND id = :workflow_id"
                ),
                {
                    **parameters,
                    "workflow_id": workflow_id,
                    "workspace": WORKSPACE_ID,
                },
            )
        with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
            requested_event = next(
                event
                for event in uow.outbox.list_for_aggregate(body["product_brief"]["id"])
                if event.envelope.event_type == "product-brief.requested"
            )
        worker = WorkerRuntime.build(
            settings,
            operation_executors={
                OperationKind.PRODUCT_BRIEF_ANALYSIS: executor,
            },
        )
        try:
            assert worker.process_event(requested_event.envelope.event_id) == "processed"
        finally:
            worker.close()
        operation = client.get(
            f"/api/v1/operations/{body['operation_id']}",
            headers=_read_headers(),
        )

    assert analyzer.call_count == 0
    assert operation.status_code == 200, operation.text
    assert operation.json()["state"] == "FAILED"
    assert operation.json()["error"]["code"] == "PRODUCT_BRIEF_WORKFLOW_NOT_EXECUTABLE"


@pytest.mark.parametrize(
    "authority_mutation",
    ["workflow_type", "product_id", "retention_status"],
)
def test_workflow_authority_drift_after_analyzer_entry_prevents_provider_submission(
    integration_database,
    integration_settings,
    authority_mutation: str,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(integration_database)
    with TestClient(create_app(settings)) as client:
        requested = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers(
                f"brief-authority-drift-before-submit-{authority_mutation}-0001"
            ),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": 3,
            },
        )
    assert requested.status_code == 202, requested.text
    body = requested.json()
    operation_service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    claimed_at = datetime.now(UTC)
    lease_token = operation_service.claim(
        workspace_id=WORKSPACE_ID,
        operation_id=body["operation_id"],
        owner="authority-drift-before-submission-worker",
        lease_duration=timedelta(seconds=30),
        now=claimed_at,
    )
    running = operation_service.start(
        workspace_id=WORKSPACE_ID,
        operation_id=body["operation_id"],
        lease_token=lease_token,
        now=claimed_at,
    )
    analyzer = BlockingBeforeSubmissionAnalyzer(
        DeterministicVisionAnalyzer(
            scenario=DeterministicVisionScenario.SUCCESS,
            artifact_sink=MemoryArtifactSink(),
        )
    )
    executor = ProductBriefAnalysisExecutor(
        uow_factory=lambda: SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory),
        object_storage=ControlledReadStorage(),
        analyzer=analyzer,
        policy=ProductBriefPolicy.from_settings(settings),
        transfer_policy=VisionDataTransferPolicy.from_settings(settings),
        artifact_service=_artifact_service(integration_database),
    )

    failure: OperationExecutionFailure | None = None
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(
                executor.execute,
                OperationExecutionRequest.from_operation(running),
            )
            assert analyzer.entered.wait(5)
            with integration_database.engine.begin() as connection:
                if authority_mutation == "workflow_type":
                    mutation = "workflow_type = 'FIXTURE_IMAGE_GENERATION'"
                    parameters = {}
                elif authority_mutation == "product_id":
                    mutation = (
                        "input_json = JSON_SET(input_json, '$.product_id', :replacement_product_id)"
                    )
                    parameters = {"replacement_product_id": new_uuid7()}
                else:
                    mutation = "retention_status = 'EXPIRING'"
                    parameters = {}
                connection.execute(
                    text(
                        f"UPDATE workflows SET {mutation} "
                        "WHERE workspace_id = :workspace AND id = :workflow_id"
                    ),
                    {
                        **parameters,
                        "workflow_id": workflow_id,
                        "workspace": WORKSPACE_ID,
                    },
                )
            analyzer.release.set()
            try:
                pending.result(timeout=10)
            except OperationExecutionFailure as exc:
                failure = exc
    finally:
        analyzer.release.set()

    assert analyzer.call_count == 1
    assert analyzer.submitted.is_set() is False
    assert failure is not None
    assert failure.error.code == "PRODUCT_BRIEF_WORKFLOW_NOT_EXECUTABLE"


def test_cancellation_committed_before_provider_submission_prevents_provider_call(
    integration_database,
    integration_settings,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(integration_database)
    app = create_app(settings)
    with TestClient(app) as client:
        requested = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers("brief-cancel-submission-race-0001"),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": 3,
            },
        )
    assert requested.status_code == 202, requested.text
    body = requested.json()
    operation_service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    claimed_at = datetime.now(UTC)
    lease_token = operation_service.claim(
        workspace_id=WORKSPACE_ID,
        operation_id=body["operation_id"],
        owner="cancel-submission-race-worker",
        lease_duration=timedelta(seconds=30),
        now=claimed_at,
    )
    running = operation_service.start(
        workspace_id=WORKSPACE_ID,
        operation_id=body["operation_id"],
        lease_token=lease_token,
        now=claimed_at,
    )
    analyzer = SignalingAnalyzer(
        DeterministicVisionAnalyzer(
            scenario=DeterministicVisionScenario.SUCCESS,
            artifact_sink=MemoryArtifactSink(),
        )
    )
    executor = ProductBriefAnalysisExecutor(
        uow_factory=lambda: SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory),
        object_storage=ControlledReadStorage(),
        analyzer=analyzer,
        policy=ProductBriefPolicy.from_settings(settings),
        transfer_policy=VisionDataTransferPolicy.from_settings(settings),
        artifact_service=_artifact_service(integration_database),
    )

    blocker = integration_database.engine.connect()
    transaction = blocker.begin()
    blocker.execute(
        text(
            "UPDATE workflows SET status = 'CANCELLED', "
            "cancellation_requested_at = UTC_TIMESTAMP(6), version = version + 1, "
            "updated_at = UTC_TIMESTAMP(6) "
            "WHERE workspace_id = :workspace AND id = :workflow_id"
        ),
        {"workflow_id": workflow_id, "workspace": WORKSPACE_ID},
    )
    failure: OperationExecutionFailure | None = None
    provider_called_before_cancel_commit = False
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(
                executor.execute,
                OperationExecutionRequest.from_operation(running),
            )
            provider_called_before_cancel_commit = analyzer.called.wait(1)
            transaction.commit()
            try:
                pending.result(timeout=5)
            except OperationExecutionFailure as exc:
                failure = exc
    finally:
        if transaction.is_active:
            transaction.rollback()
        blocker.close()

    with SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory) as uow:
        attempt = uow.product_briefs.get_provider_attempt(
            workspace_id=WORKSPACE_ID,
            operation_id=body["operation_id"],
            operation_attempt=1,
            call_index=0,
        )
    assert provider_called_before_cancel_commit is False
    assert analyzer.called.is_set() is False
    assert attempt is None
    assert failure is not None
    assert failure.error.code == "PRODUCT_BRIEF_WORKFLOW_NOT_EXECUTABLE"


def test_provider_submission_intent_refuses_concurrent_workflow_cancellation(
    integration_database,
    integration_settings,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(integration_database)
    analyzer = BlockingBeforeProviderAnalyzer(
        DeterministicVisionAnalyzer(
            scenario=DeterministicVisionScenario.SUCCESS,
            artifact_sink=MemoryArtifactSink(),
        )
    )
    executor = ProductBriefAnalysisExecutor(
        uow_factory=lambda: SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory),
        object_storage=ControlledReadStorage(),
        analyzer=analyzer,
        policy=ProductBriefPolicy.from_settings(settings),
        transfer_policy=VisionDataTransferPolicy.from_settings(settings),
        artifact_service=_artifact_service(integration_database),
    )
    operation_service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    worker = DurableOperationWorker(
        operations=operation_service,
        execution=OperationExecutionBoundary(
            executor=executor,
            transaction_active=is_unit_of_work_active,
        ),
        owner="provider-submission-cancellation-race-worker",
        lease_duration=timedelta(seconds=30),
    )
    app = create_app(settings)

    try:
        with TestClient(app) as client:
            requested = client.post(
                "/api/v1/product-briefs:analyze",
                headers=_mutation_headers("brief-submission-cancel-race-0001"),
                json={
                    "workflow_id": workflow_id,
                    "product_id": product_id,
                    "asset_version_ids": [asset_version_id],
                    "expected_workflow_version": 3,
                },
            )
            assert requested.status_code == 202, requested.text
            operation_id = requested.json()["operation_id"]

            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(
                    worker.execute,
                    workspace_id=WORKSPACE_ID,
                    operation_id=operation_id,
                )
                assert analyzer.entered.wait(5)
                assert analyzer.call_count == 1

                cancelled = client.post(
                    f"/api/v1/workflows/{workflow_id}:cancel",
                    headers=_mutation_headers("brief-workflow-cancel-race-0001"),
                    json={"expected_workflow_version": 3},
                )
                assert cancelled.status_code == 409, cancelled.text
                assert cancelled.json()["code"] == "WORKFLOW_CANCELLATION_REFUSED"

                workflow = client.get(
                    f"/api/v1/workflows/{workflow_id}",
                    headers=_read_headers(),
                )
                assert workflow.status_code == 200, workflow.text
                assert workflow.json()["status"] == "UNDERSTANDING"

                analyzer.release.set()
                completed = pending.result(timeout=10)
    finally:
        analyzer.release.set()

    assert analyzer.call_count == 1
    assert completed.state == OperationState.SUCCEEDED


def test_repair_submission_intent_refuses_concurrent_workflow_cancellation(
    integration_database,
    integration_settings,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(integration_database)
    analyzer = RepairLifecycleAnalyzer(
        MemoryArtifactSink(),
        block_after_repair_intent=True,
    )
    executor = ProductBriefAnalysisExecutor(
        uow_factory=lambda: SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory),
        object_storage=ControlledReadStorage(),
        analyzer=analyzer,
        policy=ProductBriefPolicy.from_settings(settings),
        transfer_policy=VisionDataTransferPolicy.from_settings(settings),
        artifact_service=_artifact_service(integration_database),
    )
    operation_service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    worker = DurableOperationWorker(
        operations=operation_service,
        execution=OperationExecutionBoundary(
            executor=executor,
            transaction_active=is_unit_of_work_active,
        ),
        owner="repair-submission-cancellation-race-worker",
        lease_duration=timedelta(seconds=30),
    )
    app = create_app(settings)

    try:
        with TestClient(app) as client:
            requested = client.post(
                "/api/v1/product-briefs:analyze",
                headers=_mutation_headers("brief-repair-cancel-race-0001"),
                json={
                    "workflow_id": workflow_id,
                    "product_id": product_id,
                    "asset_version_ids": [asset_version_id],
                    "expected_workflow_version": 3,
                },
            )
            assert requested.status_code == 202, requested.text
            operation_id = requested.json()["operation_id"]

            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(
                    worker.execute,
                    workspace_id=WORKSPACE_ID,
                    operation_id=operation_id,
                )
                assert analyzer.repair_intent_persisted.wait(5)

                with SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory) as uow:
                    attempts = uow.product_briefs.list_provider_attempts(
                        workspace_id=WORKSPACE_ID,
                        operation_id=operation_id,
                        operation_attempt=1,
                    )
                    calls = uow.product_briefs.list_provider_calls(
                        workspace_id=WORKSPACE_ID,
                        operation_id=operation_id,
                        operation_attempt=1,
                    )
                assert [attempt.call_index for attempt in attempts] == [0, 1]
                assert [call.call_index for call in calls] == [0]

                try:
                    cancelled = client.post(
                        f"/api/v1/workflows/{workflow_id}:cancel",
                        headers=_mutation_headers("brief-repair-workflow-cancel-race-0001"),
                        json={"expected_workflow_version": 3},
                    )
                    assert cancelled.status_code == 409, cancelled.text
                    assert cancelled.json()["code"] == "WORKFLOW_CANCELLATION_REFUSED"
                finally:
                    analyzer.release_repair.set()
                completed = pending.result(timeout=10)
    finally:
        analyzer.release_repair.set()

    assert completed.state == OperationState.SUCCEEDED


def test_provider_submission_uses_the_global_aggregate_lock_order(
    integration_database,
    integration_settings,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(integration_database)
    app = create_app(settings)
    with TestClient(app) as client:
        requested = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers("brief-lock-order-0001"),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": 3,
            },
        )
    assert requested.status_code == 202, requested.text
    body = requested.json()
    operation_service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    claimed_at = datetime.now(UTC)
    lease_token = operation_service.claim(
        workspace_id=WORKSPACE_ID,
        operation_id=body["operation_id"],
        owner="product-brief-lock-order-worker",
        lease_duration=timedelta(seconds=30),
        now=claimed_at,
    )
    running = operation_service.start(
        workspace_id=WORKSPACE_ID,
        operation_id=body["operation_id"],
        lease_token=lease_token,
        now=claimed_at,
    )
    executor = ProductBriefAnalysisExecutor(
        uow_factory=lambda: SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory),
        object_storage=ControlledReadStorage(),
        analyzer=DeterministicVisionAnalyzer(
            scenario=DeterministicVisionScenario.SUCCESS,
            artifact_sink=MemoryArtifactSink(),
        ),
        policy=ProductBriefPolicy.from_settings(settings),
        transfer_policy=VisionDataTransferPolicy.from_settings(settings),
        artifact_service=_artifact_service(integration_database),
    )
    lock_order: list[str] = []

    def capture_lock_order(
        connection,
        cursor,
        statement,
        parameters,
        context,
        executemany,
    ) -> None:
        del connection, cursor, parameters, context, executemany
        normalized = " ".join(statement.lower().replace("`", "").split())
        if " for update" not in normalized:
            return
        if " from workflows " in f" {normalized} ":
            lock_order.append("workflow")
        elif " from product_briefs " in f" {normalized} ":
            lock_order.append("product_brief")
        elif " from durable_operations " in f" {normalized} ":
            lock_order.append("operation")

    event.listen(
        integration_database.engine,
        "before_cursor_execute",
        capture_lock_order,
    )
    try:
        executor.execute(OperationExecutionRequest.from_operation(running))
    finally:
        event.remove(
            integration_database.engine,
            "before_cursor_execute",
            capture_lock_order,
        )

    first_workflow_lock = lock_order.index("workflow")
    assert lock_order[first_workflow_lock : first_workflow_lock + 3] == [
        "workflow",
        "product_brief",
        "operation",
    ]


def test_recovery_before_late_commit_fences_stale_product_brief_worker(
    integration_database,
    integration_settings,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(integration_database)
    app = create_app(settings)
    with TestClient(app) as client:
        requested = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers("brief-recovery-before-late-commit-0001"),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": 3,
            },
        )
    assert requested.status_code == 202, requested.text
    body = requested.json()
    operation_service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    claimed_at = datetime.now(UTC)
    lease_token = operation_service.claim(
        workspace_id=WORKSPACE_ID,
        operation_id=body["operation_id"],
        owner="late-product-brief-worker",
        lease_duration=timedelta(seconds=30),
        now=claimed_at,
    )
    running = operation_service.start(
        workspace_id=WORKSPACE_ID,
        operation_id=body["operation_id"],
        lease_token=lease_token,
        now=claimed_at,
    )
    analyzer = BlockingAfterProviderAnalyzer(
        DeterministicVisionAnalyzer(
            scenario=DeterministicVisionScenario.SUCCESS,
            artifact_sink=MemoryArtifactSink(),
        )
    )
    executor = ProductBriefAnalysisExecutor(
        uow_factory=lambda: SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory),
        object_storage=ControlledReadStorage(),
        analyzer=analyzer,
        policy=ProductBriefPolicy.from_settings(settings),
        transfer_policy=VisionDataTransferPolicy.from_settings(settings),
        artifact_service=_artifact_service(integration_database),
    )
    scanner = OperationRecoveryService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory),
        batch_size=1,
        reconciliation_max_elapsed=timedelta(minutes=5),
    )

    late_failure: OperationExecutionFailure | None = None
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(
                executor.execute,
                OperationExecutionRequest.from_operation(running),
            )
            assert analyzer.provider_completed.wait(5)
            assert running.lease_expires_at is not None
            assert scanner.recover_once(now=running.lease_expires_at) == 1
            analyzer.release.set()
            try:
                pending.result(timeout=5)
            except OperationExecutionFailure as exc:
                late_failure = exc
    finally:
        analyzer.release.set()

    recovered = operation_service.get(
        workspace_id=WORKSPACE_ID,
        operation_id=body["operation_id"],
    )
    with SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory) as uow:
        product_brief = uow.product_briefs.get(
            workspace_id=WORKSPACE_ID,
            product_brief_id=body["product_brief"]["id"],
        )
        calls = uow.product_briefs.list_provider_calls(
            workspace_id=WORKSPACE_ID,
            operation_id=body["operation_id"],
            operation_attempt=1,
        )
    assert recovered.state == OperationState.RECONCILING
    assert late_failure is not None
    assert late_failure.error.code == "PRODUCT_BRIEF_OPERATION_MISMATCH"
    assert product_brief is not None
    assert product_brief.state.value == "DRAFT"
    assert product_brief.current_version_id is None
    assert calls == ()


@pytest.mark.parametrize(
    ("mutation", "expected_error_code"),
    [
        ("current-operation", "PRODUCT_BRIEF_OPERATION_MISMATCH"),
        ("workflow-cancellation", "PRODUCT_BRIEF_WORKFLOW_NOT_EXECUTABLE"),
    ],
)
def test_successful_provider_call_rolls_back_with_conditional_publish_rejection(
    integration_database,
    integration_settings,
    mutation: str,
    expected_error_code: str,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(integration_database)
    app = create_app(settings)
    with TestClient(app) as client:
        requested = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers(f"brief-provenance-publish-{mutation}-0001"),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": 3,
            },
        )
    assert requested.status_code == 202, requested.text
    body = requested.json()
    operation_service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    replacement_operation = operation_service.create(
        OperationCreateCommand(
            workspace_id=WORKSPACE_ID,
            kind=OperationKind.PRODUCT_BRIEF_ANALYSIS,
            target_type="product_brief_fence",
            target_id=new_uuid7(),
            target_version=1,
            input_hash=hashlib.sha256(mutation.encode()).hexdigest(),
            max_attempts=1,
        )
    )
    claimed_at = datetime.now(UTC)
    lease_token = operation_service.claim(
        workspace_id=WORKSPACE_ID,
        operation_id=body["operation_id"],
        owner=f"publish-conflict-{mutation}-worker",
        lease_duration=timedelta(seconds=30),
        now=claimed_at,
    )
    running = operation_service.start(
        workspace_id=WORKSPACE_ID,
        operation_id=body["operation_id"],
        lease_token=lease_token,
        now=claimed_at,
    )

    def mutate_after_provider(_request: VisionAnalysisRequest) -> None:
        with integration_database.engine.begin() as connection:
            if mutation == "current-operation":
                connection.execute(
                    text(
                        "UPDATE product_briefs SET operation_id = :replacement_operation_id, "
                        "version = version + 1, updated_at = UTC_TIMESTAMP(6) "
                        "WHERE workspace_id = :workspace AND id = :product_brief_id"
                    ),
                    {
                        "product_brief_id": body["product_brief"]["id"],
                        "replacement_operation_id": replacement_operation.id,
                        "workspace": WORKSPACE_ID,
                    },
                )
            else:
                connection.execute(
                    text(
                        "UPDATE workflows SET status = 'CANCELLED', "
                        "cancellation_requested_at = UTC_TIMESTAMP(6), version = version + 1, "
                        "updated_at = UTC_TIMESTAMP(6) "
                        "WHERE workspace_id = :workspace AND id = :workflow_id"
                    ),
                    {"workflow_id": workflow_id, "workspace": WORKSPACE_ID},
                )

    analyzer = MutatingAfterProviderAnalyzer(
        DeterministicVisionAnalyzer(
            scenario=DeterministicVisionScenario.SUCCESS,
            artifact_sink=MemoryArtifactSink(),
        ),
        mutate_after_provider,
    )
    executor = ProductBriefAnalysisExecutor(
        uow_factory=lambda: SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory),
        object_storage=ControlledReadStorage(),
        analyzer=analyzer,
        policy=ProductBriefPolicy.from_settings(settings),
        transfer_policy=VisionDataTransferPolicy.from_settings(settings),
        artifact_service=_artifact_service(integration_database),
    )

    failure: OperationExecutionFailure | None = None
    try:
        executor.execute(OperationExecutionRequest.from_operation(running))
    except OperationExecutionFailure as exc:
        failure = exc

    with SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory) as uow:
        product_brief = uow.product_briefs.get(
            workspace_id=WORKSPACE_ID,
            product_brief_id=body["product_brief"]["id"],
        )
        attempt = uow.product_briefs.get_provider_attempt(
            workspace_id=WORKSPACE_ID,
            operation_id=body["operation_id"],
            operation_attempt=1,
            call_index=0,
        )
        calls = uow.product_briefs.list_provider_calls(
            workspace_id=WORKSPACE_ID,
            operation_id=body["operation_id"],
            operation_attempt=1,
        )
    assert analyzer.call_count == 1
    assert failure is not None
    assert failure.error.code == expected_error_code
    assert product_brief is not None
    assert product_brief.state.value == "DRAFT"
    assert product_brief.current_version_id is None
    assert attempt is not None
    assert calls == ()


def test_database_time_rejects_insufficient_submission_lease_despite_stale_app_clock(
    integration_database,
    integration_settings,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(integration_database)
    app = create_app(settings)
    with TestClient(app) as client:
        requested = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers("brief-database-lease-reserve-0001"),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": 3,
            },
        )
    assert requested.status_code == 202, requested.text
    operation_id = requested.json()["operation_id"]
    operation_service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    claimed_at = datetime.now(UTC)
    lease_token = operation_service.claim(
        workspace_id=WORKSPACE_ID,
        operation_id=operation_id,
        owner="database-lease-reserve-worker",
        lease_duration=timedelta(seconds=30),
        now=claimed_at,
    )
    operation_service.start(
        workspace_id=WORKSPACE_ID,
        operation_id=operation_id,
        lease_token=lease_token,
        now=claimed_at,
    )
    with integration_database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE durable_operations "
                "SET lease_expires_at = UTC_TIMESTAMP(6) + INTERVAL 5 SECOND "
                "WHERE workspace_id = :workspace AND id = :operation_id"
            ),
            {"operation_id": operation_id, "workspace": WORKSPACE_ID},
        )
    short_lease = operation_service.get(
        workspace_id=WORKSPACE_ID,
        operation_id=operation_id,
    )
    stale_clock = MutableClock(claimed_at - timedelta(hours=1))
    analyzer = SignalingAnalyzer(
        DeterministicVisionAnalyzer(
            scenario=DeterministicVisionScenario.SUCCESS,
            artifact_sink=MemoryArtifactSink(),
        )
    )
    executor = ProductBriefAnalysisExecutor(
        uow_factory=lambda: SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory),
        object_storage=ControlledReadStorage(),
        analyzer=analyzer,
        policy=ProductBriefPolicy.from_settings(settings),
        transfer_policy=VisionDataTransferPolicy.from_settings(settings),
        artifact_service=_artifact_service(integration_database),
        submission_reserve=timedelta(seconds=10),
        clock=stale_clock,
    )

    with pytest.raises(OperationExecutionFailure) as failure:
        executor.execute(OperationExecutionRequest.from_operation(short_lease))

    assert failure.value.error.code == "VISION_OPERATION_LEASE_RESERVE_INSUFFICIENT"
    assert failure.value.error.retryable is True
    assert analyzer.called.is_set() is False
    with SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory) as uow:
        attempt = uow.product_briefs.get_provider_attempt(
            workspace_id=WORKSPACE_ID,
            operation_id=operation_id,
            operation_attempt=1,
            call_index=0,
        )
    assert attempt is None


@pytest.mark.parametrize(
    ("mutation", "expected_error_code"),
    [
        ("asset", "RIGHTS_ASSET_BLOCKED"),
        ("object", "VISION_SOURCE_NOT_CONTROLLED"),
    ],
)
def test_worker_rechecks_source_after_temporary_read_before_provider_transfer(
    integration_database,
    integration_settings,
    mutation: str,
    expected_error_code: str,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(integration_database)
    storage = RevokingReadStorage(integration_database, mutation=mutation)
    artifact_sink = MemoryArtifactSink()
    executor = ProductBriefAnalysisExecutor(
        uow_factory=lambda: SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory),
        object_storage=storage,
        analyzer=DeterministicVisionAnalyzer(
            scenario=DeterministicVisionScenario.SUCCESS,
            artifact_sink=artifact_sink,
        ),
        policy=ProductBriefPolicy.from_settings(settings),
        transfer_policy=VisionDataTransferPolicy.from_settings(settings),
        artifact_service=_artifact_service(integration_database, artifact_sink),
    )
    app = create_app(settings)

    with TestClient(app) as client:
        requested = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers(f"brief-source-race-{mutation}-0001"),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": 3,
            },
        )
        assert requested.status_code == 202, requested.text
        body = requested.json()
        with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
            events = uow.outbox.list_for_aggregate(body["product_brief"]["id"])
        requested_event = next(
            event for event in events if event.envelope.event_type == "product-brief.requested"
        )
        worker = WorkerRuntime.build(
            settings,
            operation_executors={
                OperationKind.PRODUCT_BRIEF_ANALYSIS: executor,
            },
        )
        try:
            assert worker.process_event(requested_event.envelope.event_id) == "processed"
        finally:
            worker.close()

        operation = client.get(
            f"/api/v1/operations/{body['operation_id']}",
            headers=_read_headers(),
        )
        current = client.get(
            f"/api/v1/product-briefs/{body['product_brief']['id']}",
            headers=_read_headers(),
        )

    assert operation.status_code == 200, operation.text
    assert operation.json()["state"] == "FAILED"
    assert operation.json()["error"]["code"] == expected_error_code
    assert current.status_code == 200
    assert current.json()["state"] == "DRAFT"
    assert storage.read_count == 1
    assert artifact_sink.artifacts == []


@pytest.mark.parametrize("tamper_target", ["envelope-version", "payload-version"])
def test_worker_dead_letters_product_brief_event_not_bound_to_aggregate(
    integration_database,
    integration_settings,
    tamper_target: str,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(integration_database)
    executor = ProductBriefAnalysisExecutor(
        uow_factory=lambda: SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory),
        object_storage=ControlledReadStorage(),
        analyzer=DeterministicVisionAnalyzer(
            scenario=DeterministicVisionScenario.SUCCESS,
            artifact_sink=MemoryArtifactSink(),
        ),
        policy=ProductBriefPolicy.from_settings(settings),
        transfer_policy=VisionDataTransferPolicy.from_settings(settings),
        artifact_service=_artifact_service(integration_database),
    )
    app = create_app(settings)

    with TestClient(app) as client:
        requested = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers("brief-event-binding-0001"),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": 3,
            },
        )
        assert requested.status_code == 202, requested.text
        body = requested.json()
        with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
            original = next(
                event
                for event in uow.outbox.list_for_aggregate(body["product_brief"]["id"])
                if event.envelope.event_type == "product-brief.requested"
            )
        tampered_version = original.envelope.aggregate_version + 1
        tampered = OutboxEvent(
            envelope=EventEnvelope.create(
                event_type=original.envelope.event_type,
                aggregate_type=original.envelope.aggregate_type,
                aggregate_id=original.envelope.aggregate_id,
                aggregate_version=tampered_version,
                trace_id=original.envelope.trace_id,
                payload={
                    **original.envelope.payload,
                    "product_brief_version": (
                        tampered_version
                        if tamper_target == "payload-version"
                        else original.envelope.payload["product_brief_version"]
                    ),
                },
            ),
            available_at=datetime.now(UTC),
            workspace_id=WORKSPACE_ID,
        )
        with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
            uow.outbox.add(tampered)
            uow.commit()

        worker = WorkerRuntime.build(
            settings,
            operation_executors={
                OperationKind.PRODUCT_BRIEF_ANALYSIS: executor,
            },
        )
        try:
            assert worker.process_event(tampered.envelope.event_id) == "dead-lettered"
        finally:
            worker.close()

        operation = client.get(
            f"/api/v1/operations/{body['operation_id']}",
            headers=_read_headers(),
        )

    assert operation.status_code == 200, operation.text
    assert operation.json()["state"] == "PENDING"
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        dead_letter = uow.dead_letters.get(
            consumer=settings.worker_consumer_name,
            message_id=tampered.envelope.event_id,
        )
    assert dead_letter is not None
    assert dead_letter.reason == "aggregate_mismatch"


def test_retention_expiry_becomes_terminal_durable_operation_failure(
    integration_database,
    integration_settings,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(integration_database)
    executor = ProductBriefAnalysisExecutor(
        uow_factory=lambda: SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory),
        object_storage=ControlledReadStorage(),
        analyzer=DeterministicVisionAnalyzer(
            scenario=DeterministicVisionScenario.SUCCESS,
            artifact_sink=MemoryArtifactSink(),
        ),
        policy=ProductBriefPolicy.from_settings(settings),
        transfer_policy=VisionDataTransferPolicy.from_settings(settings),
        artifact_service=_artifact_service(integration_database),
        clock=lambda: datetime.now(UTC) + timedelta(days=4),
    )
    app = create_app(settings)

    with TestClient(app) as client:
        requested = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers("brief-retention-expired-0001"),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": 3,
            },
        )
        assert requested.status_code == 202, requested.text
        body = requested.json()
        with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
            requested_event = next(
                event
                for event in uow.outbox.list_for_aggregate(body["product_brief"]["id"])
                if event.envelope.event_type == "product-brief.requested"
            )
        worker = WorkerRuntime.build(
            settings,
            operation_executors={
                OperationKind.PRODUCT_BRIEF_ANALYSIS: executor,
            },
        )
        try:
            assert worker.process_event(requested_event.envelope.event_id) == "processed"
        finally:
            worker.close()

        operation = client.get(
            f"/api/v1/operations/{body['operation_id']}",
            headers=_read_headers(),
        )
        current = client.get(
            f"/api/v1/product-briefs/{body['product_brief']['id']}",
            headers=_read_headers(),
        )

    assert operation.status_code == 200, operation.text
    assert operation.json()["state"] == "FAILED"
    assert operation.json()["error"]["code"] == "PRODUCT_BRIEF_RETENTION_EXPIRED"
    assert current.status_code == 200, current.text
    assert current.json()["state"] == "DRAFT"
    with SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory) as uow:
        provider_calls = uow.product_briefs.list_provider_calls(
            workspace_id=WORKSPACE_ID,
            operation_id=body["operation_id"],
            operation_attempt=1,
        )
    assert provider_calls == ()


@pytest.mark.parametrize(
    (
        "failure_point",
        "expected_error_code",
        "expected_error_category",
        "expected_operation_state",
        "expected_retryable",
        "expected_analyzer_calls",
    ),
    [
        (
            "temporary-read",
            "VISION_STORAGE_UNAVAILABLE",
            "storage",
            "RETRYABLE_FAILED",
            True,
            0,
        ),
        (
            "source-object-missing",
            "VISION_STORAGE_INTEGRITY",
            "storage_integrity",
            "FAILED",
            False,
            0,
        ),
        (
            "source-object-mismatch",
            "VISION_STORAGE_INTEGRITY",
            "storage_integrity",
            "FAILED",
            False,
            0,
        ),
        (
            "artifact-write",
            "VISION_ARTIFACT_STORAGE_UNAVAILABLE",
            "storage",
            "RETRYABLE_FAILED",
            True,
            1,
        ),
        (
            "artifact-write-unknown",
            "VISION_ARTIFACT_OUTCOME_UNKNOWN",
            "storage_integrity",
            "RECONCILING",
            False,
            1,
        ),
        (
            "artifact-integrity",
            "VISION_ARTIFACT_INTEGRITY_CONFLICT",
            "storage_integrity",
            "FAILED",
            False,
            1,
        ),
    ],
)
def test_external_storage_failures_are_normalized_by_the_durable_operation(
    integration_database,
    integration_settings,
    failure_point: str,
    expected_error_code: str,
    expected_error_category: str,
    expected_operation_state: str,
    expected_retryable: bool,
    expected_analyzer_calls: int,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(integration_database)
    read_storage = {
        "temporary-read": FailingReadStorage,
        "source-object-missing": MissingReadStorage,
        "source-object-mismatch": MismatchedReadStorage,
    }
    artifact_sink = (
        FailingArtifactSink()
        if failure_point == "artifact-write"
        else (
            UnknownArtifactSink()
            if failure_point == "artifact-write-unknown"
            else (
                ConflictingArtifactSink()
                if failure_point == "artifact-integrity"
                else MemoryArtifactSink()
            )
        )
    )
    analyzer = CountingAnalyzer(
        DeterministicVisionAnalyzer(
            scenario=DeterministicVisionScenario.SUCCESS,
            artifact_sink=artifact_sink,
        )
    )
    executor = ProductBriefAnalysisExecutor(
        uow_factory=lambda: SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory),
        object_storage=read_storage.get(failure_point, ControlledReadStorage)(),
        analyzer=analyzer,
        policy=ProductBriefPolicy.from_settings(settings),
        transfer_policy=VisionDataTransferPolicy.from_settings(settings),
        artifact_service=_artifact_service(integration_database, artifact_sink),
    )
    app = create_app(settings)

    with TestClient(app) as client:
        requested = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers(f"brief-storage-{failure_point}-0001"),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": 3,
            },
        )
        assert requested.status_code == 202, requested.text
        body = requested.json()
        with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
            requested_event = next(
                event
                for event in uow.outbox.list_for_aggregate(body["product_brief"]["id"])
                if event.envelope.event_type == "product-brief.requested"
            )
        worker = WorkerRuntime.build(
            settings,
            operation_executors={
                OperationKind.PRODUCT_BRIEF_ANALYSIS: executor,
            },
        )
        try:
            assert worker.process_event(requested_event.envelope.event_id) == "processed"
        finally:
            worker.close()

        operation = client.get(
            f"/api/v1/operations/{body['operation_id']}",
            headers=_read_headers(),
        )
        current = client.get(
            f"/api/v1/product-briefs/{body['product_brief']['id']}",
            headers=_read_headers(),
        )

    assert operation.status_code == 200, operation.text
    assert operation.json()["state"] == expected_operation_state
    assert operation.json()["error"]["code"] == expected_error_code
    assert operation.json()["error"]["category"] == expected_error_category
    assert operation.json()["error"]["retryable"] is expected_retryable
    assert current.status_code == 200, current.text
    assert current.json()["state"] == "DRAFT"
    assert analyzer.call_count == expected_analyzer_calls


@pytest.mark.parametrize(
    ("failure_kind", "expected_response_state"),
    [
        ("safe-prewrite", ProviderArtifactState.INTENDED),
        ("outcome-unknown", ProviderArtifactState.UNKNOWN),
    ],
)
def test_response_artifact_failure_preserves_provider_provenance_without_redispatch(
    integration_database,
    integration_settings,
    failure_kind: str,
    expected_response_state: ProviderArtifactState,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(integration_database)
    artifact_sink = ResponseArtifactFailureSink(failure_kind)
    identity = DeterministicVisionAnalyzer(
        scenario=DeterministicVisionScenario.SUCCESS,
        artifact_sink=MemoryArtifactSink(),
    ).configured_identity
    analyzer = ResponseArtifactAwareAnalyzer(identity)
    executor = ProductBriefAnalysisExecutor(
        uow_factory=lambda: SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory),
        object_storage=ControlledReadStorage(),
        analyzer=analyzer,
        policy=ProductBriefPolicy.from_settings(settings),
        transfer_policy=VisionDataTransferPolicy.from_settings(settings),
        artifact_service=_artifact_service(integration_database, artifact_sink),
    )
    app = create_app(settings)

    with TestClient(app) as client:
        requested = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers(f"brief-response-artifact-{failure_kind}-0001"),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": 3,
            },
        )
        assert requested.status_code == 202, requested.text
        body = requested.json()
        with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
            requested_event = next(
                event
                for event in uow.outbox.list_for_aggregate(body["product_brief"]["id"])
                if event.envelope.event_type == "product-brief.requested"
            )
        worker = WorkerRuntime.build(
            settings,
            operation_executors={
                OperationKind.PRODUCT_BRIEF_ANALYSIS: executor,
            },
        )
        try:
            assert worker.process_event(requested_event.envelope.event_id) == "processed"
            assert worker.process_event(requested_event.envelope.event_id) == "duplicate"
        finally:
            worker.close()

        operation = client.get(
            f"/api/v1/operations/{body['operation_id']}",
            headers=_read_headers(),
        )

    assert operation.status_code == 200, operation.text
    assert operation.json()["state"] == "RECONCILING"
    assert operation.json()["error"]["code"] == "PROVIDER_RESPONSE_ARTIFACT_OUTCOME_UNKNOWN"
    assert operation.json()["error"]["provider_request_id"] == "provider-request-response-received"
    assert analyzer.call_count == 1
    assert analyzer.submission_count == 1
    assert artifact_sink.response_write_count == 1
    with SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory) as uow:
        calls = uow.product_brief_analyses.list_provider_calls(
            workspace_id=WORKSPACE_ID,
            operation_id=body["operation_id"],
            operation_attempt=1,
        )
        artifacts = uow.product_brief_artifacts.list_provider_artifacts(
            workspace_id=WORKSPACE_ID,
            operation_id=body["operation_id"],
            operation_attempt=1,
        )
    assert len(calls) == 1
    assert calls[0].status == VisionProviderStatus.UNKNOWN
    assert calls[0].request_id == "provider-request-response-received"
    assert calls[0].error_code == "PROVIDER_RESPONSE_ARTIFACT_OUTCOME_UNKNOWN"
    assert calls[0].response_artifact is None
    assert calls[0].response_artifact_id is None
    assert [(artifact.kind, artifact.state) for artifact in artifacts] == [
        (ProviderArtifactKind.REQUEST, ProviderArtifactState.STORED),
        (ProviderArtifactKind.RESPONSE, expected_response_state),
    ]


@pytest.mark.parametrize(
    (
        "scenario",
        "expected_operation_state",
        "expected_brief_state",
        "expected_error_code",
    ),
    [
        (DeterministicVisionScenario.SUCCESS, "SUCCEEDED", "CONFIRMED", None),
        (
            DeterministicVisionScenario.CONFLICT,
            "WAITING_HUMAN",
            "AWAITING_CONFIRMATION",
            None,
        ),
        (
            DeterministicVisionScenario.SENSITIVE,
            "WAITING_HUMAN",
            "AWAITING_CONFIRMATION",
            None,
        ),
        (
            DeterministicVisionScenario.MALFORMED,
            "FAILED",
            "DRAFT",
            "MALFORMED_PROVIDER_OUTPUT",
        ),
        (
            DeterministicVisionScenario.TIMEOUT,
            "RETRYABLE_FAILED",
            "DRAFT",
            "PROVIDER_TIMEOUT",
        ),
    ],
)
def test_mysql_worker_persists_product_brief_provider_scenarios(
    integration_database,
    integration_settings,
    scenario: DeterministicVisionScenario,
    expected_operation_state: str,
    expected_brief_state: str,
    expected_error_code: str | None,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(integration_database)
    artifact_sink = MemoryArtifactSink()
    executor = ProductBriefAnalysisExecutor(
        uow_factory=lambda: SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory),
        object_storage=ControlledReadStorage(),
        analyzer=DeterministicVisionAnalyzer(
            scenario=scenario,
            artifact_sink=artifact_sink,
        ),
        policy=ProductBriefPolicy.from_settings(settings),
        transfer_policy=VisionDataTransferPolicy.from_settings(settings),
        artifact_service=_artifact_service(integration_database, artifact_sink),
    )
    app = create_app(settings)

    with TestClient(app) as client:
        requested = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers(f"brief-scenario-{scenario.value.lower()}"),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": 3,
            },
        )
        assert requested.status_code == 202, requested.text
        body = requested.json()
        with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
            events = uow.outbox.list_for_aggregate(body["product_brief"]["id"])
        requested_event = next(
            event for event in events if event.envelope.event_type == "product-brief.requested"
        )
        worker = WorkerRuntime.build(
            settings,
            operation_executors={
                OperationKind.PRODUCT_BRIEF_ANALYSIS: executor,
            },
        )
        try:
            assert worker.process_event(requested_event.envelope.event_id) == "processed"
        finally:
            worker.close()

        operation = client.get(
            f"/api/v1/operations/{body['operation_id']}",
            headers=_read_headers(),
        )
        current = client.get(
            f"/api/v1/product-briefs/{body['product_brief']['id']}",
            headers=_read_headers(),
        )

    assert operation.status_code == 200, operation.text
    assert operation.json()["state"] == expected_operation_state
    assert current.status_code == 200
    assert current.json()["state"] == expected_brief_state
    if expected_error_code is None:
        assert operation.json()["error"] is None
    else:
        assert operation.json()["error"]["code"] == expected_error_code
    with SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory) as uow:
        calls = uow.product_briefs.list_provider_calls(
            workspace_id=WORKSPACE_ID,
            operation_id=body["operation_id"],
            operation_attempt=1,
        )
        attempt = uow.product_briefs.get_provider_attempt(
            workspace_id=WORKSPACE_ID,
            operation_id=body["operation_id"],
            operation_attempt=1,
            call_index=0,
        )
        analysis = uow.product_briefs.get_analysis_by_operation(
            workspace_id=WORKSPACE_ID,
            operation_id=body["operation_id"],
        )
    assert analysis is not None
    assert attempt is not None
    assert analysis.submitted_model_snapshot == "deterministic-vision-v1"
    assert attempt.submitted_model_snapshot == "deterministic-vision-v1"
    assert calls[-1].submitted_model_snapshot == "deterministic-vision-v1"
    assert calls[-1].status.value == (
        "SUCCEEDED"
        if scenario
        in {
            DeterministicVisionScenario.SUCCESS,
            DeterministicVisionScenario.CONFLICT,
            DeterministicVisionScenario.SENSITIVE,
        }
        else scenario.value
    )
    if scenario == DeterministicVisionScenario.SUCCESS:
        assert calls[-1].request_artifact.byte_size > 0
        assert calls[-1].response_artifact is not None
        assert calls[-1].response_artifact.byte_size > 0


def test_analysis_idempotency_serializes_concurrent_identical_requests(
    integration_database,
    integration_settings,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(integration_database)
    service = ProductBriefApplicationService(
        uow_factory=lambda: SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory),
        policy=ProductBriefPolicy.from_settings(settings),
        transfer_policy=VisionDataTransferPolicy.from_settings(settings),
    )
    request = ProductBriefAnalysisRequestV1(
        workflow_id=workflow_id,
        product_id=product_id,
        asset_version_ids=(asset_version_id,),
        expected_workflow_version=3,
    )
    start = threading.Barrier(2)

    def submit(trace_id: str):
        start.wait(timeout=5)
        return service.request_analysis(
            workspace_id=WORKSPACE_ID,
            actor_id="brief-reviewer",
            request=request,
            idempotency_key="concurrent-analysis-idempotency",
            trace_id=trace_id,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = tuple(
            future.result(timeout=15)
            for future in (
                pool.submit(submit, "concurrent-analysis-a"),
                pool.submit(submit, "concurrent-analysis-b"),
            )
        )

    assert responses[0].model_dump(mode="json") == responses[1].model_dump(mode="json")
    with integration_database.engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM product_briefs "
                    "WHERE workspace_id = :workspace AND workflow_id = :workflow "
                    "AND product_id = :product"
                ),
                {
                    "product": product_id,
                    "workflow": workflow_id,
                    "workspace": WORKSPACE_ID,
                },
            ).scalar_one()
            == 1
        )


def test_replayed_attempt_is_fenced_before_provider_resubmission(
    integration_database,
    integration_settings,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(integration_database)
    app = create_app(settings)
    with TestClient(app) as client:
        requested = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers("brief-provider-call-replay-0001"),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": 3,
            },
        )
    assert requested.status_code == 202, requested.text
    operation_id = requested.json()["operation_id"]
    operation_service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    claimed_at = datetime.now(UTC)
    lease_token = operation_service.claim(
        workspace_id=WORKSPACE_ID,
        operation_id=operation_id,
        owner="provider-call-replay-worker",
        lease_duration=timedelta(seconds=30),
        now=claimed_at,
    )
    operation = operation_service.start(
        workspace_id=WORKSPACE_ID,
        operation_id=operation_id,
        lease_token=lease_token,
        now=claimed_at,
    )
    execution = OperationExecutionRequest.from_operation(operation)
    artifact_sink = MemoryArtifactSink()
    analyzer = SequentialAnalyzer(
        DeterministicVisionAnalyzer(
            scenario=DeterministicVisionScenario.TIMEOUT,
            artifact_sink=artifact_sink,
        ),
        DeterministicVisionAnalyzer(
            scenario=DeterministicVisionScenario.MALFORMED,
            artifact_sink=artifact_sink,
        ),
    )
    executor = ProductBriefAnalysisExecutor(
        uow_factory=lambda: SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory),
        object_storage=ControlledReadStorage(),
        analyzer=analyzer,
        policy=ProductBriefPolicy.from_settings(settings),
        transfer_policy=VisionDataTransferPolicy.from_settings(settings),
        artifact_service=_artifact_service(integration_database, artifact_sink),
    )

    with pytest.raises(OperationExecutionFailure) as first:
        executor.execute(execution)
    with pytest.raises(UnknownOperationOutcome) as replay:
        executor.execute(execution)

    assert first.value.error.code == "PROVIDER_TIMEOUT"
    assert replay.value.error.code == "VISION_SUBMISSION_ALREADY_FENCED"
    with SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory) as uow:
        calls = uow.product_briefs.list_provider_calls(
            workspace_id=WORKSPACE_ID,
            operation_id=operation.id,
            operation_attempt=1,
        )
    assert len(calls) == 1
    assert calls[0].status.value == "TIMEOUT"


def test_successful_provider_calls_and_model_version_commit_atomically(
    integration_database,
    integration_settings,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(integration_database)
    app = create_app(settings)
    with TestClient(app) as client:
        requested = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers("brief-provider-atomic-result-0001"),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": 3,
            },
        )
    assert requested.status_code == 202, requested.text
    body = requested.json()
    operation_service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    claimed_at = datetime.now(UTC)
    lease_token = operation_service.claim(
        workspace_id=WORKSPACE_ID,
        operation_id=body["operation_id"],
        owner="atomic-result-worker",
        lease_duration=timedelta(seconds=30),
        now=claimed_at,
    )
    running = operation_service.start(
        workspace_id=WORKSPACE_ID,
        operation_id=body["operation_id"],
        lease_token=lease_token,
        now=claimed_at,
    )
    executor = ProductBriefAnalysisExecutor(
        uow_factory=lambda: FailBeforeVersionCommitUnitOfWork(integration_database.session_factory),
        object_storage=ControlledReadStorage(),
        analyzer=DeterministicVisionAnalyzer(
            scenario=DeterministicVisionScenario.SUCCESS,
            artifact_sink=MemoryArtifactSink(),
        ),
        policy=ProductBriefPolicy.from_settings(settings),
        transfer_policy=VisionDataTransferPolicy.from_settings(settings),
        artifact_service=_artifact_service(integration_database),
    )

    with pytest.raises(
        RuntimeError,
        match="injected failure before ProductBrief version commit",
    ):
        executor.execute(OperationExecutionRequest.from_operation(running))

    with SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory) as uow:
        calls = uow.product_briefs.list_provider_calls(
            workspace_id=WORKSPACE_ID,
            operation_id=body["operation_id"],
            operation_attempt=1,
        )
        output = uow.product_briefs.get_model_version_by_operation(
            workspace_id=WORKSPACE_ID,
            operation_id=body["operation_id"],
        )
        attempt = uow.product_briefs.get_provider_attempt(
            workspace_id=WORKSPACE_ID,
            operation_id=body["operation_id"],
            operation_attempt=1,
            call_index=0,
        )

    assert attempt is not None
    assert calls == ()
    assert output is None


def test_successful_provider_outcome_without_response_artifact_cannot_publish_version(
    integration_database,
    integration_settings,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(integration_database)
    app = create_app(settings)
    with TestClient(app) as client:
        requested = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers("brief-provider-missing-response-0001"),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": 3,
            },
        )
    assert requested.status_code == 202, requested.text
    body = requested.json()
    operation_service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    claimed_at = datetime.now(UTC)
    lease_token = operation_service.claim(
        workspace_id=WORKSPACE_ID,
        operation_id=body["operation_id"],
        owner="missing-response-worker",
        lease_duration=timedelta(seconds=30),
        now=claimed_at,
    )
    running = operation_service.start(
        workspace_id=WORKSPACE_ID,
        operation_id=body["operation_id"],
        lease_token=lease_token,
        now=claimed_at,
    )
    artifact_sink = MemoryArtifactSink()
    analyzer = SuccessfulOutcomeWithoutResponseArtifactAnalyzer(
        DeterministicVisionAnalyzer(
            scenario=DeterministicVisionScenario.SUCCESS,
            artifact_sink=MemoryArtifactSink(),
        )
    )
    executor = ProductBriefAnalysisExecutor(
        uow_factory=lambda: SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory),
        object_storage=ControlledReadStorage(),
        analyzer=analyzer,
        policy=ProductBriefPolicy.from_settings(settings),
        transfer_policy=VisionDataTransferPolicy.from_settings(settings),
        artifact_service=_artifact_service(integration_database, artifact_sink),
    )

    with pytest.raises(OperationExecutionFailure) as failure:
        executor.execute(OperationExecutionRequest.from_operation(running))

    assert failure.value.error.code == "VISION_PROVIDER_ARTIFACT_LEDGER_MISMATCH"
    with SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory) as uow:
        calls = uow.product_brief_analyses.list_provider_calls(
            workspace_id=WORKSPACE_ID,
            operation_id=body["operation_id"],
            operation_attempt=1,
        )
        output = uow.product_brief_analyses.get_model_version_by_operation(
            workspace_id=WORKSPACE_ID,
            operation_id=body["operation_id"],
        )
        artifacts = uow.product_brief_artifacts.list_provider_artifacts(
            workspace_id=WORKSPACE_ID,
            operation_id=body["operation_id"],
            operation_attempt=1,
        )

    assert calls == ()
    assert output is None
    assert [(artifact.kind, artifact.state) for artifact in artifacts] == [
        (ProviderArtifactKind.REQUEST, ProviderArtifactState.STORED)
    ]


def test_repair_submission_intents_and_completed_prefix_are_durable(
    integration_database,
    integration_settings,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(integration_database)
    app = create_app(settings)
    with TestClient(app) as client:
        requested = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers("brief-provider-repair-intents-0001"),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": 3,
            },
        )
    assert requested.status_code == 202, requested.text
    body = requested.json()
    operation_service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    claimed_at = datetime.now(UTC)
    lease_token = operation_service.claim(
        workspace_id=WORKSPACE_ID,
        operation_id=body["operation_id"],
        owner="provider-repair-intent-worker",
        lease_duration=timedelta(seconds=30),
        now=claimed_at,
    )
    running = operation_service.start(
        workspace_id=WORKSPACE_ID,
        operation_id=body["operation_id"],
        lease_token=lease_token,
        now=claimed_at,
    )
    artifact_sink = MemoryArtifactSink()
    executor = ProductBriefAnalysisExecutor(
        uow_factory=lambda: SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory),
        object_storage=ControlledReadStorage(),
        analyzer=RepairLifecycleAnalyzer(artifact_sink),
        policy=ProductBriefPolicy.from_settings(settings),
        transfer_policy=VisionDataTransferPolicy.from_settings(settings),
        artifact_service=_artifact_service(integration_database, artifact_sink),
    )

    result = executor.execute(OperationExecutionRequest.from_operation(running))

    with SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory) as uow:
        attempts = uow.product_briefs.list_provider_attempts(
            workspace_id=WORKSPACE_ID,
            operation_id=body["operation_id"],
            operation_attempt=1,
        )
        calls = uow.product_briefs.list_provider_calls(
            workspace_id=WORKSPACE_ID,
            operation_id=body["operation_id"],
            operation_attempt=1,
        )
        output = uow.product_briefs.get_model_version_by_operation(
            workspace_id=WORKSPACE_ID,
            operation_id=body["operation_id"],
        )

    assert result.output_ref is not None
    assert [attempt.call_index for attempt in attempts] == [0, 1]
    assert [call.call_index for call in calls] == [0, 1]
    assert [call.status for call in calls] == [
        VisionProviderStatus.MALFORMED,
        VisionProviderStatus.SUCCEEDED,
    ]
    assert output is not None
    assert output.version.provider_call_id == calls[1].id


def test_reconciliation_detects_repair_intent_without_a_completed_call(
    integration_database,
    integration_settings,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(integration_database)
    app = create_app(settings)
    with TestClient(app) as client:
        requested = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers("brief-provider-repair-crash-0001"),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": 3,
            },
        )
    assert requested.status_code == 202, requested.text
    body = requested.json()
    operation_service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    claimed_at = datetime.now(UTC)
    lease_token = operation_service.claim(
        workspace_id=WORKSPACE_ID,
        operation_id=body["operation_id"],
        owner="provider-repair-crash-worker",
        lease_duration=timedelta(seconds=30),
        now=claimed_at,
    )
    running = operation_service.start(
        workspace_id=WORKSPACE_ID,
        operation_id=body["operation_id"],
        lease_token=lease_token,
        now=claimed_at,
    )
    execution = OperationExecutionRequest.from_operation(running)
    artifact_sink = MemoryArtifactSink()
    executor = ProductBriefAnalysisExecutor(
        uow_factory=lambda: SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory),
        object_storage=ControlledReadStorage(),
        analyzer=RepairLifecycleAnalyzer(
            artifact_sink,
            crash_after_repair_intent=True,
        ),
        policy=ProductBriefPolicy.from_settings(settings),
        transfer_policy=VisionDataTransferPolicy.from_settings(settings),
        artifact_service=_artifact_service(integration_database, artifact_sink),
    )

    with pytest.raises(UnknownOperationOutcome):
        executor.execute(execution)
    reconciled = executor.reconcile(execution)

    with SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory) as uow:
        attempts = uow.product_briefs.list_provider_attempts(
            workspace_id=WORKSPACE_ID,
            operation_id=body["operation_id"],
            operation_attempt=1,
        )
        calls = uow.product_briefs.list_provider_calls(
            workspace_id=WORKSPACE_ID,
            operation_id=body["operation_id"],
            operation_attempt=1,
        )

    assert [attempt.call_index for attempt in attempts] == [0, 1]
    assert [call.call_index for call in calls] == [0]
    assert reconciled.error is not None
    assert reconciled.error.code == "VISION_SUBMISSION_OUTCOME_UNKNOWN"
    assert reconciled.error.retryable is False


def test_worker_recovers_an_attempt_recorded_before_provider_success_was_persisted(
    integration_database,
    integration_settings,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(integration_database)
    analyzer = UnknownAfterProviderSuccessAnalyzer(
        integration_database,
        DeterministicVisionAnalyzer(
            scenario=DeterministicVisionScenario.SUCCESS,
            artifact_sink=MemoryArtifactSink(),
        ),
    )
    executor = ProductBriefAnalysisExecutor(
        uow_factory=lambda: SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory),
        object_storage=ControlledReadStorage(),
        analyzer=analyzer,
        policy=ProductBriefPolicy.from_settings(settings),
        transfer_policy=VisionDataTransferPolicy.from_settings(settings),
        artifact_service=_artifact_service(integration_database),
    )
    app = create_app(settings)

    with TestClient(app) as client:
        requested = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers("brief-provider-crash-window-0001"),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": 3,
            },
        )
        assert requested.status_code == 202, requested.text
        body = requested.json()
        with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
            requested_event = next(
                event
                for event in uow.outbox.list_for_aggregate(body["product_brief"]["id"])
                if event.envelope.event_type == "product-brief.requested"
            )
        worker = WorkerRuntime.build(
            settings,
            operation_executors={
                OperationKind.PRODUCT_BRIEF_ANALYSIS: executor,
            },
        )
        try:
            assert worker.process_event(requested_event.envelope.event_id) == "processed"
            uncertain = client.get(
                f"/api/v1/operations/{body['operation_id']}",
                headers=_read_headers(),
            )
            assert uncertain.status_code == 200, uncertain.text
            assert uncertain.json()["state"] == "RECONCILING"

            recovered = worker.operation_worker.execute(
                workspace_id=WORKSPACE_ID,
                operation_id=body["operation_id"],
            )
            redelivered = worker.operation_worker.execute(
                workspace_id=WORKSPACE_ID,
                operation_id=body["operation_id"],
            )
        finally:
            worker.close()

    assert recovered.state == OperationState.FAILED
    assert recovered.error is not None
    assert recovered.error.code == "VISION_SUBMISSION_OUTCOME_UNKNOWN"
    assert recovered.error.retryable is False
    assert recovered.dead_letter_id is not None
    assert redelivered.state == OperationState.FAILED
    assert redelivered.dead_letter_id == recovered.dead_letter_id
    assert analyzer.call_count == 1
    with SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory) as uow:
        calls = uow.product_briefs.list_provider_calls(
            workspace_id=WORKSPACE_ID,
            operation_id=body["operation_id"],
            operation_attempt=1,
        )
        attempt = uow.product_briefs.get_provider_attempt(
            workspace_id=WORKSPACE_ID,
            operation_id=body["operation_id"],
            operation_attempt=1,
            call_index=0,
        )
    assert calls == ()
    assert attempt is not None
    assert attempt.submitted_model_snapshot == "deterministic-vision-v1"


def test_response_artifact_intent_after_submission_is_never_retryable(
    integration_database,
    integration_settings,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(integration_database)
    artifact_sink = CrashBeforeResponseArtifactSink()
    analyzer = CountingAnalyzer(
        DeterministicVisionAnalyzer(
            scenario=DeterministicVisionScenario.SUCCESS,
            artifact_sink=artifact_sink,
        )
    )
    artifact_service = _artifact_service(integration_database, artifact_sink)
    executor = ProductBriefAnalysisExecutor(
        uow_factory=lambda: SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory),
        object_storage=ControlledReadStorage(),
        analyzer=analyzer,
        policy=ProductBriefPolicy.from_settings(settings),
        transfer_policy=VisionDataTransferPolicy.from_settings(settings),
        artifact_service=artifact_service,
        artifact_reconciler=ProductBriefProviderArtifactReconciler(
            uow_factory=lambda: SqlAlchemyProductBriefUnitOfWork(
                integration_database.session_factory
            ),
            artifact_reader=EmptyVersionStorage(),  # type: ignore[arg-type]
            artifact_store=artifact_sink,
            clock=lambda: datetime.now(UTC),
        ),
    )
    app = create_app(settings)

    with TestClient(app) as client:
        requested = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers("brief-response-artifact-crash-0001"),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": 3,
            },
        )
    assert requested.status_code == 202, requested.text
    operation_id = requested.json()["operation_id"]
    operation_service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    claimed_at = datetime.now(UTC)
    lease_token = operation_service.claim(
        workspace_id=WORKSPACE_ID,
        operation_id=operation_id,
        owner="response-artifact-crash-worker",
        lease_duration=timedelta(seconds=30),
        now=claimed_at,
    )
    running = operation_service.start(
        workspace_id=WORKSPACE_ID,
        operation_id=operation_id,
        lease_token=lease_token,
        now=claimed_at,
    )
    execution = OperationExecutionRequest.from_operation(running)

    with pytest.raises(SimulatedProcessDeath):
        executor.execute(execution)
    reconciled = executor.reconcile(execution)
    with pytest.raises(UnknownOperationOutcome):
        executor.execute(execution)

    assert analyzer.call_count == 1
    assert reconciled.error is not None
    assert reconciled.error.code == "VISION_SUBMISSION_OUTCOME_UNKNOWN"
    assert reconciled.error.retryable is False
    with SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory) as uow:
        response_artifact = uow.product_briefs.get_provider_artifact(
            workspace_id=WORKSPACE_ID,
            operation_id=operation_id,
            operation_attempt=1,
            call_index=0,
            kind=ProviderArtifactKind.RESPONSE,
        )
    assert response_artifact is not None
    assert response_artifact.state.value == "INTENDED"


def test_retryable_unknown_provider_call_is_forced_to_manual_dlq_without_resubmission(
    integration_database,
    integration_settings,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(integration_database)
    analyzer = PersistedUnknownAnalyzer(
        DeterministicVisionAnalyzer(
            scenario=DeterministicVisionScenario.TIMEOUT,
            artifact_sink=MemoryArtifactSink(),
        ),
        retryable=True,
    )
    executor = ProductBriefAnalysisExecutor(
        uow_factory=lambda: SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory),
        object_storage=ControlledReadStorage(),
        analyzer=analyzer,
        policy=ProductBriefPolicy.from_settings(settings),
        transfer_policy=VisionDataTransferPolicy.from_settings(settings),
        artifact_service=_artifact_service(integration_database),
    )
    app = create_app(settings)

    with TestClient(app) as client:
        requested = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers("brief-persisted-unknown-0001"),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": 3,
            },
        )
        assert requested.status_code == 202, requested.text
        body = requested.json()
        with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
            requested_event = next(
                event
                for event in uow.outbox.list_for_aggregate(body["product_brief"]["id"])
                if event.envelope.event_type == "product-brief.requested"
            )
        worker = WorkerRuntime.build(
            settings,
            operation_executors={
                OperationKind.PRODUCT_BRIEF_ANALYSIS: executor,
            },
        )
        try:
            assert worker.process_event(requested_event.envelope.event_id) == "processed"
            uncertain = client.get(
                f"/api/v1/operations/{body['operation_id']}",
                headers=_read_headers(),
            )
            assert uncertain.status_code == 200, uncertain.text
            assert uncertain.json()["state"] == "RECONCILING"
            recovered = worker.operation_worker.execute(
                workspace_id=WORKSPACE_ID,
                operation_id=body["operation_id"],
            )
            redelivered = worker.operation_worker.execute(
                workspace_id=WORKSPACE_ID,
                operation_id=body["operation_id"],
            )
        finally:
            worker.close()

    assert recovered.state == OperationState.FAILED
    assert recovered.error is not None
    assert recovered.error.code == "PROVIDER_SUBMISSION_OUTCOME_UNKNOWN"
    assert recovered.error.retryable is False
    assert recovered.dead_letter_id is not None
    assert redelivered.dead_letter_id == recovered.dead_letter_id
    assert analyzer.call_count == 1
    with SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory) as uow:
        calls = uow.product_briefs.list_provider_calls(
            workspace_id=WORKSPACE_ID,
            operation_id=body["operation_id"],
            operation_attempt=1,
        )
    assert len(calls) == 1
    assert calls[0].status == VisionProviderStatus.UNKNOWN
    assert calls[0].error_retryable is False
    assert calls[0].submitted_model_snapshot == "deterministic-vision-v1"


def test_product_brief_reads_fail_closed_after_retention_expiry(
    integration_database,
    integration_settings,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(integration_database)
    app = create_app(settings)

    with TestClient(app) as client:
        requested = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers("brief-retention-read-0001"),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": 3,
            },
        )
        assert requested.status_code == 202, requested.text
        product_brief_id = requested.json()["product_brief"]["id"]
        operation_id = requested.json()["operation_id"]
        expired_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
        with integration_database.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE product_briefs SET retention_deadline = :expired_at "
                    "WHERE workspace_id = :workspace AND id = :product_brief_id"
                ),
                {
                    "expired_at": expired_at,
                    "product_brief_id": product_brief_id,
                    "workspace": WORKSPACE_ID,
                },
            )
            connection.execute(
                text(
                    "UPDATE workflows SET expires_at = :expired_at "
                    "WHERE workspace_id = :workspace AND id = :workflow_id"
                ),
                {
                    "expired_at": expired_at,
                    "workflow_id": workflow_id,
                    "workspace": WORKSPACE_ID,
                },
            )

        current = client.get(
            f"/api/v1/product-briefs/{product_brief_id}",
            headers=_read_headers(),
        )
        versions = client.get(
            f"/api/v1/product-briefs/{product_brief_id}/versions",
            headers=_read_headers(),
        )
        projection_sql: list[str] = []

        def capture_projection_sql(
            connection,
            cursor,
            statement,
            parameters,
            context,
            executemany,
        ) -> None:
            del connection, cursor, parameters, context, executemany
            normalized = " ".join(statement.lower().replace("`", "").split())
            if ("from workflows" in normalized or "from durable_operations" in normalized) and (
                "product_briefs" in normalized
            ):
                projection_sql.append(normalized)

        event.listen(
            Engine,
            "before_cursor_execute",
            capture_projection_sql,
        )
        try:
            workflow_context = client.get(
                f"/api/v1/product-briefs/workflow-context/{workflow_id}"
                f"?product_brief_id={product_brief_id}",
                headers=_read_headers(),
            )
            operation = client.get(
                f"/api/v1/product-briefs/{product_brief_id}/operations/{operation_id}",
                headers=_read_headers(),
            )
        finally:
            event.remove(
                Engine,
                "before_cursor_execute",
                capture_projection_sql,
            )

    assert current.status_code == 410
    assert current.json()["code"] == "PRODUCT_BRIEF_RETENTION_EXPIRED"
    assert versions.status_code == 410
    assert versions.json()["code"] == "PRODUCT_BRIEF_RETENTION_EXPIRED"
    assert workflow_context.status_code == 410
    assert workflow_context.json()["code"] == "PRODUCT_BRIEF_RETENTION_EXPIRED"
    assert operation.status_code == 410
    assert operation.json()["code"] == "PRODUCT_BRIEF_RETENTION_EXPIRED"

    workflow_sql = [statement for statement in projection_sql if "from workflows" in statement]
    operation_sql = [
        statement for statement in projection_sql if "from durable_operations" in statement
    ]
    assert len(workflow_sql) == 2, projection_sql
    assert len(operation_sql) == 2, projection_sql

    workflow_active_sql, workflow_expiry_sql = workflow_sql
    assert "workflows.expires_at > utc_timestamp(6)" in workflow_active_sql
    assert "product_briefs.retention_deadline > utc_timestamp(6)" in workflow_active_sql
    assert "product_briefs.id =" in workflow_active_sql
    assert "product_briefs.workflow_id = workflows.id" in workflow_active_sql
    workflow_expiry_select = workflow_expiry_sql.split(" from ", maxsplit=1)[0]
    for business_column in (
        "workflows.id",
        "workflows.status",
        "workflows.version",
        "workflows.expires_at",
        "product_briefs.retention_deadline",
    ):
        assert business_column not in workflow_expiry_select

    operation_active_sql, operation_expiry_sql = operation_sql
    assert "product_briefs.retention_deadline > utc_timestamp(6)" in operation_active_sql
    for exact_filter in (
        "durable_operations.kind =",
        "durable_operations.target_type =",
        "durable_operations.target_id =",
        "product_briefs.id =",
    ):
        assert exact_filter in operation_active_sql
        assert exact_filter in operation_expiry_sql
    operation_expiry_select = operation_expiry_sql.split(" from ", maxsplit=1)[0]
    for business_column in (
        "durable_operations.id",
        "durable_operations.state",
        "durable_operations.attempt_count",
        "durable_operations.max_attempts",
        "durable_operations.error_code",
        "durable_operations.version",
        "product_briefs.retention_deadline",
    ):
        assert business_column not in operation_expiry_select


def test_pre_analysis_workflow_context_is_a_safe_mysql_projection(
    integration_database,
    integration_settings,
) -> None:
    settings = _settings(integration_settings)
    active_workflow_id, _, _ = _seed_authorized_source(integration_database)
    wrong_status_workflow_id, _, _ = _seed_authorized_source(integration_database)
    expired_workflow_id, _, _ = _seed_authorized_source(integration_database)
    non_commerce_workflow_id, _, _ = _seed_authorized_source(integration_database)
    expired_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
    with integration_database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE workflows SET status = 'AWAITING_PRODUCT_CONFIRMATION' "
                "WHERE workspace_id = :workspace AND id = :workflow_id"
            ),
            {
                "workflow_id": wrong_status_workflow_id,
                "workspace": WORKSPACE_ID,
            },
        )
        connection.execute(
            text(
                "UPDATE workflows SET expires_at = :expired_at "
                "WHERE workspace_id = :workspace AND id = :workflow_id"
            ),
            {
                "expired_at": expired_at,
                "workflow_id": expired_workflow_id,
                "workspace": WORKSPACE_ID,
            },
        )
        connection.execute(
            text(
                "UPDATE workflows SET workflow_type = 'FIXTURE_IMAGE_GENERATION' "
                "WHERE workspace_id = :workspace AND id = :workflow_id"
            ),
            {
                "workflow_id": non_commerce_workflow_id,
                "workspace": WORKSPACE_ID,
            },
        )
        assert (
            connection.scalar(
                text(
                    "SELECT COUNT(*) FROM product_briefs "
                    "WHERE workspace_id = :workspace AND workflow_id = :workflow_id"
                ),
                {
                    "workflow_id": active_workflow_id,
                    "workspace": WORKSPACE_ID,
                },
            )
            == 0
        )

    def get_with_projection_sql(
        client: TestClient,
        workflow_id: str,
        *,
        workspace_id: str = WORKSPACE_ID,
    ):
        statements: list[str] = []

        def capture_projection_sql(
            connection,
            cursor,
            statement,
            parameters,
            context,
            executemany,
        ) -> None:
            del connection, cursor, parameters, context, executemany
            normalized = " ".join(statement.lower().replace("`", "").split())
            if "from workflows" in normalized:
                statements.append(normalized)

        event.listen(Engine, "before_cursor_execute", capture_projection_sql)
        try:
            response = client.get(
                f"/api/v1/product-briefs/analysis-workflow-context/{workflow_id}",
                headers=_read_headers(workspace_id=workspace_id),
            )
        finally:
            event.remove(Engine, "before_cursor_execute", capture_projection_sql)
        return response, statements

    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        active, active_sql = get_with_projection_sql(client, active_workflow_id)
        case_mismatch, case_mismatch_sql = get_with_projection_sql(
            client,
            active_workflow_id,
            workspace_id=WORKSPACE_ID.upper(),
        )
        wrong_status, wrong_status_sql = get_with_projection_sql(
            client,
            wrong_status_workflow_id,
        )
        expired, expired_sql = get_with_projection_sql(client, expired_workflow_id)
        non_commerce, non_commerce_sql = get_with_projection_sql(
            client,
            non_commerce_workflow_id,
        )

    assert active.status_code == 200, active.text
    assert active.json().keys() == {
        "id",
        "status",
        "version",
        "retention_deadline",
    }
    assert active.json()["id"] == active_workflow_id
    assert active.json()["status"] == "UNDERSTANDING"
    assert active.json()["version"] == 3
    assert case_mismatch.status_code == 404
    assert wrong_status.status_code == 404
    assert expired.status_code == 410
    assert expired.json()["code"] == "PRODUCT_BRIEF_RETENTION_EXPIRED"
    assert non_commerce.status_code == 404

    assert len(active_sql) == 1, active_sql
    active_select = active_sql[0].split(" from ", maxsplit=1)[0]
    assert "workflows.id" in active_select
    assert "workflows.status" in active_select
    assert "workflows.version" in active_select
    assert "workflows.created_at" in active_select
    assert "workflows.expires_at" in active_select
    for forbidden in (
        "workflows.created_by",
        "workflows.current_node",
        "workflows.input_json",
        "workflows.result_json",
        "product_briefs",
        "workflow_steps",
        "provider",
    ):
        assert forbidden not in active_sql[0]
    for exact_filter in (
        "workflows.workspace_id =",
        "workflows.id =",
        "workflows.status =",
        "workflows.workflow_type =",
        "workflows.retention_status =",
        (
            "least(workflows.expires_at, timestampadd(hour, 72, "
            "workflows.created_at)) > utc_timestamp(6)"
        ),
    ):
        assert exact_filter in active_sql[0]

    assert len(case_mismatch_sql) == 2, case_mismatch_sql
    assert len(wrong_status_sql) == 2, wrong_status_sql
    assert len(expired_sql) == 2, expired_sql
    assert len(non_commerce_sql) == 2, non_commerce_sql
    expiry_probe = expired_sql[1]
    assert "workflows.workspace_id =" in expiry_probe
    assert "workflows.id =" in expiry_probe
    assert "workflows.status =" in expiry_probe
    assert "workflows.retention_status !=" in expiry_probe
    assert (
        "least(workflows.expires_at, timestampadd(hour, 72, "
        "workflows.created_at)) <= utc_timestamp(6)"
    ) in expiry_probe
    expiry_select = expiry_probe.split(" from ", maxsplit=1)[0]
    assert "workflows." not in expiry_select


def test_pre_analysis_workflow_context_caps_legacy_retention_to_task_boundary(
    integration_database,
    integration_settings,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, _, _ = _seed_authorized_source(integration_database)
    with integration_database.engine.begin() as connection:
        created_at = connection.scalar(
            text(
                "SELECT created_at FROM workflows "
                "WHERE workspace_id = :workspace AND id = :workflow_id"
            ),
            {
                "workspace": WORKSPACE_ID,
                "workflow_id": workflow_id,
            },
        )
        assert isinstance(created_at, datetime)
        connection.execute(
            text(
                "UPDATE workflows SET expires_at = :legacy_deadline "
                "WHERE workspace_id = :workspace AND id = :workflow_id"
            ),
            {
                "legacy_deadline": created_at + timedelta(hours=168),
                "workspace": WORKSPACE_ID,
                "workflow_id": workflow_id,
            },
        )

    with TestClient(create_app(settings)) as client:
        response = client.get(
            f"/api/v1/product-briefs/analysis-workflow-context/{workflow_id}",
            headers=_read_headers(),
        )

    assert response.status_code == 200, response.text
    observed_deadline = datetime.fromisoformat(
        response.json()["retention_deadline"].replace("Z", "+00:00")
    )
    assert observed_deadline == created_at.replace(tzinfo=UTC) + timedelta(hours=72)


def test_pre_analysis_workflow_context_preserves_shorter_active_deadline(
    integration_database,
    integration_settings,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, _, _ = _seed_authorized_source(integration_database)
    with integration_database.engine.begin() as connection:
        created_at = connection.scalar(
            text(
                "SELECT created_at FROM workflows "
                "WHERE workspace_id = :workspace AND id = :workflow_id"
            ),
            {
                "workspace": WORKSPACE_ID,
                "workflow_id": workflow_id,
            },
        )
        database_now = connection.scalar(text("SELECT UTC_TIMESTAMP(6)"))
        assert isinstance(created_at, datetime)
        assert isinstance(database_now, datetime)
        short_deadline = created_at + timedelta(hours=1)
        assert short_deadline > database_now
        connection.execute(
            text(
                "UPDATE workflows SET expires_at = :short_deadline "
                "WHERE workspace_id = :workspace AND id = :workflow_id"
            ),
            {
                "short_deadline": short_deadline,
                "workspace": WORKSPACE_ID,
                "workflow_id": workflow_id,
            },
        )

    with TestClient(create_app(settings)) as client:
        response = client.get(
            f"/api/v1/product-briefs/analysis-workflow-context/{workflow_id}",
            headers=_read_headers(),
        )

    assert response.status_code == 200, response.text
    observed_deadline = datetime.fromisoformat(
        response.json()["retention_deadline"].replace("Z", "+00:00")
    )
    assert observed_deadline == short_deadline.replace(tzinfo=UTC)


def test_pre_analysis_workflow_context_expires_legacy_workflow_at_task_boundary(
    integration_database,
    integration_settings,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, _, _ = _seed_authorized_source(integration_database)
    with integration_database.engine.begin() as connection:
        database_now = connection.scalar(text("SELECT UTC_TIMESTAMP(6)"))
        assert isinstance(database_now, datetime)
        legacy_created_at = database_now - timedelta(hours=73)
        connection.execute(
            text(
                "UPDATE workflows "
                "SET created_at = :created_at, expires_at = :legacy_deadline "
                "WHERE workspace_id = :workspace AND id = :workflow_id"
            ),
            {
                "created_at": legacy_created_at,
                "legacy_deadline": legacy_created_at + timedelta(hours=168),
                "workspace": WORKSPACE_ID,
                "workflow_id": workflow_id,
            },
        )

    with TestClient(create_app(settings)) as client:
        response = client.get(
            f"/api/v1/product-briefs/analysis-workflow-context/{workflow_id}",
            headers=_read_headers(),
        )

    assert response.status_code == 410, response.text
    assert response.json()["code"] == "PRODUCT_BRIEF_RETENTION_EXPIRED"


@pytest.mark.parametrize("retention_status", ["EXPIRING", "DELETING", "EXPIRED"])
def test_pre_analysis_workflow_context_rejects_revoked_workflow_retention(
    integration_database,
    integration_settings,
    retention_status: str,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, _, _ = _seed_authorized_source(integration_database)
    with integration_database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE workflows SET retention_status = :retention_status "
                "WHERE workspace_id = :workspace AND id = :workflow_id"
            ),
            {
                "retention_status": retention_status,
                "workspace": WORKSPACE_ID,
                "workflow_id": workflow_id,
            },
        )

    with TestClient(create_app(settings)) as client:
        response = client.get(
            f"/api/v1/product-briefs/analysis-workflow-context/{workflow_id}",
            headers=_read_headers(),
        )

    assert response.status_code == 410, response.text
    assert response.json()["code"] == "PRODUCT_BRIEF_RETENTION_EXPIRED"


def test_product_brief_view_queries_enforce_workspace_kind_and_target_binding(
    integration_database,
    integration_settings,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(integration_database)
    unrelated_workflow_id, _, _ = _seed_authorized_source(integration_database)
    app = create_app(settings)
    with TestClient(app) as client:
        requested = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers("brief-view-projection-0001"),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": 3,
            },
        )
        assert requested.status_code == 202, requested.text
        body = requested.json()
        product_brief_id = body["product_brief"]["id"]
        operation_id = body["operation_id"]
        wrong_kind_operation = OperationApplicationService(
            uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
        ).create(
            OperationCreateCommand(
                workspace_id=WORKSPACE_ID,
                kind=OperationKind.ASSET_VALIDATION,
                target_type="product_brief",
                target_id=product_brief_id,
                target_version=1,
                input_hash=hashlib.sha256(b"wrong-kind-projection").hexdigest(),
                max_attempts=1,
            )
        )

        workflow = client.get(
            f"/api/v1/product-briefs/workflow-context/{workflow_id}"
            f"?product_brief_id={product_brief_id}",
            headers=_read_headers(),
        )
        operation = client.get(
            f"/api/v1/product-briefs/{product_brief_id}/operations/{operation_id}",
            headers=_read_headers(),
        )
        cross_workspace = client.get(
            f"/api/v1/product-briefs/workflow-context/{workflow_id}"
            f"?product_brief_id={product_brief_id}",
            headers=_read_headers(workspace_id=ISOLATED_WORKSPACE_ID),
        )
        unrelated_workflow = client.get(
            f"/api/v1/product-briefs/workflow-context/{unrelated_workflow_id}"
            f"?product_brief_id={product_brief_id}",
            headers=_read_headers(),
        )
        wrong_workflow_target = client.get(
            f"/api/v1/product-briefs/workflow-context/{workflow_id}?product_brief_id={new_uuid7()}",
            headers=_read_headers(),
        )
        wrong_target = client.get(
            f"/api/v1/product-briefs/{new_uuid7()}/operations/{operation_id}",
            headers=_read_headers(),
        )
        wrong_kind = client.get(
            (f"/api/v1/product-briefs/{product_brief_id}/operations/{wrong_kind_operation.id}"),
            headers=_read_headers(),
        )
        with integration_database.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE workflows SET input_json = JSON_SET("
                    "input_json, '$.product_id', :replacement_product_id) "
                    "WHERE workspace_id = :workspace AND id = :workflow_id"
                ),
                {
                    "replacement_product_id": new_uuid7(),
                    "workflow_id": workflow_id,
                    "workspace": WORKSPACE_ID,
                },
            )
        product_binding_drift = client.get(
            f"/api/v1/product-briefs/workflow-context/{workflow_id}"
            f"?product_brief_id={product_brief_id}",
            headers=_read_headers(),
        )
        with integration_database.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE workflows SET workflow_type = 'FIXTURE_IMAGE_GENERATION', "
                    "input_json = JSON_SET(input_json, '$.product_id', :product_id) "
                    "WHERE workspace_id = :workspace AND id = :workflow_id"
                ),
                {
                    "product_id": product_id,
                    "workflow_id": workflow_id,
                    "workspace": WORKSPACE_ID,
                },
            )
        workflow_type_drift = client.get(
            f"/api/v1/product-briefs/workflow-context/{workflow_id}"
            f"?product_brief_id={product_brief_id}",
            headers=_read_headers(),
        )

    assert workflow.status_code == 200, workflow.text
    assert workflow.json()["status"] == "UNDERSTANDING"
    assert operation.status_code == 200, operation.text
    assert operation.json()["state"] == "PENDING"
    assert cross_workspace.status_code == 404
    assert unrelated_workflow.status_code == 404
    assert wrong_workflow_target.status_code == 404
    assert wrong_target.status_code == 404
    assert wrong_kind.status_code == 404
    assert product_binding_drift.status_code == 404
    assert workflow_type_drift.status_code == 404
    serialized = f"{workflow.text}\n{operation.text}"
    for forbidden in (
        "input_ref",
        "output_ref",
        "provider_request_id",
        "lease_owner",
    ):
        assert forbidden not in serialized


def test_product_brief_commit_rechecks_database_time_after_waiting_for_its_lock(
    integration_database,
    integration_settings,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(integration_database)
    app = create_app(settings)
    with TestClient(app) as client:
        requested = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers("brief-retention-lock-wait-0001"),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": 3,
            },
        )
    assert requested.status_code == 202, requested.text
    product_brief_id = requested.json()["product_brief"]["id"]
    retention_deadline = datetime.now(UTC) + timedelta(milliseconds=400)
    with integration_database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE product_briefs SET retention_deadline = :deadline "
                "WHERE workspace_id = :workspace AND id = :product_brief_id"
            ),
            {
                "deadline": retention_deadline.replace(tzinfo=None),
                "product_brief_id": product_brief_id,
                "workspace": WORKSPACE_ID,
            },
        )

    blocker = integration_database.engine.connect()
    transaction = blocker.begin()
    blocker.execute(
        text(
            "SELECT id FROM product_briefs "
            "WHERE workspace_id = :workspace AND id = :product_brief_id "
            "FOR UPDATE"
        ),
        {
            "product_brief_id": product_brief_id,
            "workspace": WORKSPACE_ID,
        },
    )
    started = threading.Event()

    def commit_after_lock_wait() -> None:
        with SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory) as uow:
            started.set()
            uow.commit_before_retention_deadline(
                workspace_id=WORKSPACE_ID,
                product_brief_id=product_brief_id,
                retention_deadline=retention_deadline,
                clock=lambda: datetime.now(UTC),
            )

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(commit_after_lock_wait)
            assert started.wait(1)
            while datetime.now(UTC) <= retention_deadline:
                threading.Event().wait(0.02)
            transaction.commit()
            with pytest.raises(ProductBriefRetentionExpiredError):
                pending.result(timeout=2)
    finally:
        if transaction.is_active:
            transaction.rollback()
        blocker.close()


def test_worker_rejects_provider_identity_drift_before_signing_source_urls(
    integration_database,
    integration_settings,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(integration_database)
    app = create_app(settings)

    with TestClient(app) as client:
        requested = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers("brief-frozen-policy-0001"),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": 3,
            },
        )
        assert requested.status_code == 202, requested.text
        body = requested.json()
        with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
            events = uow.outbox.list_for_aggregate(body["product_brief"]["id"])
        requested_event = next(
            event for event in events if event.envelope.event_type == "product-brief.requested"
        )
        drifted_analyzer = MismatchedIdentityAnalyzer(
            DeterministicVisionAnalyzer(
                scenario=DeterministicVisionScenario.LOW_CONFIDENCE,
                artifact_sink=MemoryArtifactSink(),
            )
        )
        storage = CountingReadStorage()
        executor = ProductBriefAnalysisExecutor(
            uow_factory=lambda: SqlAlchemyProductBriefUnitOfWork(
                integration_database.session_factory
            ),
            object_storage=storage,
            analyzer=drifted_analyzer,
            policy=ProductBriefPolicy.from_settings(
                settings.model_copy(update={"product_brief_confidence_threshold": "0.10"})
            ),
            transfer_policy=VisionDataTransferPolicy.from_settings(settings),
            artifact_service=_artifact_service(integration_database),
        )
        worker = WorkerRuntime.build(
            settings,
            operation_executors={
                OperationKind.PRODUCT_BRIEF_ANALYSIS: executor,
            },
        )
        try:
            assert worker.process_event(requested_event.envelope.event_id) == "processed"
        finally:
            worker.close()
        operation = client.get(
            f"/api/v1/operations/{body['operation_id']}",
            headers=_read_headers(),
        )

    assert operation.status_code == 200
    assert operation.json()["state"] == "FAILED"
    assert operation.json()["error"]["code"] == "VISION_PROVIDER_IDENTITY_MISMATCH"
    assert storage.read_count == 0
    assert drifted_analyzer.called is False


def test_worker_uses_the_review_policy_snapshot_frozen_at_request_time(
    integration_database,
    integration_settings,
) -> None:
    settings = _settings(integration_settings)
    workflow_id, product_id, asset_version_id = _seed_authorized_source(integration_database)
    app = create_app(settings)

    with TestClient(app) as client:
        requested = client.post(
            "/api/v1/product-briefs:analyze",
            headers=_mutation_headers("brief-frozen-review-policy-0001"),
            json={
                "workflow_id": workflow_id,
                "product_id": product_id,
                "asset_version_ids": [asset_version_id],
                "expected_workflow_version": 3,
            },
        )
        assert requested.status_code == 202, requested.text
        body = requested.json()
        with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
            events = uow.outbox.list_for_aggregate(body["product_brief"]["id"])
        requested_event = next(
            event for event in events if event.envelope.event_type == "product-brief.requested"
        )
        executor = ProductBriefAnalysisExecutor(
            uow_factory=lambda: SqlAlchemyProductBriefUnitOfWork(
                integration_database.session_factory
            ),
            object_storage=ControlledReadStorage(),
            analyzer=DeterministicVisionAnalyzer(
                scenario=DeterministicVisionScenario.LOW_CONFIDENCE,
                artifact_sink=MemoryArtifactSink(),
            ),
            policy=ProductBriefPolicy.from_settings(
                settings.model_copy(update={"product_brief_confidence_threshold": "0.10"})
            ),
            transfer_policy=VisionDataTransferPolicy.from_settings(settings),
            artifact_service=_artifact_service(integration_database),
        )
        worker = WorkerRuntime.build(
            settings,
            operation_executors={
                OperationKind.PRODUCT_BRIEF_ANALYSIS: executor,
            },
        )
        try:
            assert worker.process_event(requested_event.envelope.event_id) == "processed"
        finally:
            worker.close()
        operation = client.get(
            f"/api/v1/operations/{body['operation_id']}",
            headers=_read_headers(),
        )
        current = client.get(
            f"/api/v1/product-briefs/{body['product_brief']['id']}",
            headers=_read_headers(),
        )

    assert operation.status_code == 200
    assert operation.json()["state"] == "WAITING_HUMAN"
    assert current.status_code == 200
    assert current.json()["state"] == "AWAITING_CONFIRMATION"
    assert current.json()["current_version"]["review_policy_version"] == ("product-brief-review-v1")
