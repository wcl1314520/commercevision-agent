from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import socket
import struct
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event, Lock
from urllib.parse import unquote, urlparse

import boto3
import commercevision_api.container as api_container
import httpx
import pytest
from botocore.client import Config
from botocore.exceptions import ClientError
from commercevision_api.main import create_app
from commercevision_application import (
    AssetValidationExecutor,
    AssetValidationExecutorPolicy,
    AuthenticatedPrincipal,
    DeadLetterOperatorService,
    DeterministicContentSafetyRequestFactory,
    OperationApplicationService,
    OperationExecutionFailure,
    OperationExecutionRequest,
    OperationExecutionResult,
    OperationReconciliationResult,
    OperationRecoveryService,
    UploadSessionMaintenanceService,
    ValidationDataTransferPolicy,
)
from commercevision_application.asset_cleanup_dispatch import UploadCleanupPolicy
from commercevision_application.asset_integrity import ImageUploadIntegrityVerifier
from commercevision_application.asset_local_validation import (
    AssetLocalValidationError,
    AssetLocalValidationRequest,
    AssetLocalValidator,
)
from commercevision_application.asset_ports import AssetRetentionCommitExpiredError
from commercevision_application.asset_promotion import UploadPromoter
from commercevision_application.asset_validation_promotion import (
    AssetValidationPromotionCoordinator,
)
from commercevision_application.asset_validation_retention import (
    AssetValidationRetentionCoordinator,
    AssetValidationRetentionError,
)
from commercevision_application.asset_validation_target import (
    AssetValidationTargetBinder,
    AssetValidationTargetError,
)
from commercevision_contracts import Settings
from commercevision_contracts.object_storage import (
    BoundedReadRequest,
    ConditionalCopyRequest,
    ConditionalDeleteRequest,
    ObjectReference,
    ObjectStat,
    ObjectStorage,
    ObjectVersionListRequest,
    ObjectVersionPage,
    PresignedRequest,
    PresignPutRequest,
    TemporaryReadRequest,
)
from commercevision_contracts.validation import (
    ContentSafetyImageRequest,
    ContentSafetyOutcome,
    MalwareScanOutcome,
    ProvenanceEvidenceStatus,
    ProvenanceVerificationOutcome,
    ProvenanceVerificationResult,
)
from commercevision_domain import (
    AssetKind,
    NormalizedOperationError,
    OperationKind,
    ReconciliationOutcome,
    RetentionClass,
    StorageLocationClass,
    StoragePreconditionError,
    StorageUnavailableError,
    UniqueConstraintError,
    UploadObjectMissingError,
    new_uuid7,
)
from commercevision_object_storage import build_object_storage, close_object_storage
from commercevision_persistence import (
    SqlAlchemyAssetUnitOfWork,
    SqlAlchemyOperationUnitOfWork,
    SqlAlchemyOperatorUnitOfWork,
    SqlAlchemyUnitOfWork,
    is_unit_of_work_active,
)
from commercevision_providers import (
    DeterministicContentSafetyAdapter,
    DeterministicMalwareScanner,
    DeterministicProvenanceAdapter,
)
from commercevision_worker.readiness import probe_worker_dependencies
from commercevision_worker.runtime import WorkerRuntime
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError

VALID_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
VALID_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAMCAgMCAgMDAwMEAwMEBQgFBQQEBQoH"
    "BwYIDAoMDAsKCwsNDhIQDQ4RDgsLEBYQERMUFRUVDA8XGBYUGBIUFRT/2wBDAQME"
    "BAUEBQkFBQkUDQsNFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFB"
    "QUFBQUFBQUFBQUFBQUFBT/wAARCAADAAIDASIAAhEBAxEB/8QAHwAAAQUBAQEB"
    "AQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQR"
    "BRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3"
    "ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWW"
    "l5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo"
    "6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QA"
    "tREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMz"
    "UvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVm"
    "Z2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6"
    "wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEA"
    "PwDxKiiiv6oP5/P/2Q=="
)
VALID_WEBP = base64.b64decode(
    "UklGRkAAAABXRUJQVlA4IDQAAADQAQCdASoCAAMAAMASJaACdLoB+AADsAD+98Rf"
    "/6FD+hQ/oUP/ntH/3+q974e98P9iwAAA"
)
OVERSIZED_DIMENSION_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAABQEAAAABCAIAAADhIP+cAAAAHUlEQVR4nO3BAQ0A"
    "AAgDoF/7d7aHA9rZAAAAQP47eBIACKoGDtcAAAAASUVORK5CYII="
)
ANIMATED_WEBP = base64.b64decode(
    "UklGRoQAAABXRUJQVlA4WAoAAAACAAAAAQAAAQAAQU5JTQYAAAAAAAAAAABBTk1G"
    "KAAAAAAAAAAAAAEAAAEAAGQAAAJWUDhMDwAAAC8BQAAABxD9j/4HIqL/AQBBTk1G"
    "KAAAAAAAAAAAAAEAAAEAAGQAAABWUDhMDwAAAC8BQAAAB9D/iP4HIqL/AQA="
)
TRUSTED_KEY_ID = "upload-integration"
TRUSTED_SECRET = "upload-integration-secret-0000000000000001"


class AllowWorkspaceAdminPolicy:
    def require_admin(
        self,
        *,
        workspace_id: str,
        principal: AuthenticatedPrincipal,
    ) -> None:
        assert workspace_id in principal.admin_workspace_ids


def _real_clamav_endpoint() -> tuple[str, int]:
    host = os.getenv("CV_REAL_CLAMAV_HOST", "127.0.0.1")
    port = int(os.getenv("CV_REAL_CLAMAV_PORT", "13310"))
    try:
        with socket.create_connection((host, port), timeout=0.5):
            pass
    except OSError:
        pytest.skip(
            "real ClamAV is unavailable; start the explicit ClamAV test surface "
            "or configure CV_REAL_CLAMAV_HOST/CV_REAL_CLAMAV_PORT"
        )
    return host, port


class SuccessfulAssetValidationExecutor:
    def __init__(self) -> None:
        self.requests: list[OperationExecutionRequest] = []

    def execute(self, request: OperationExecutionRequest) -> OperationExecutionResult:
        self.requests.append(request)
        return OperationExecutionResult(
            operation_id=request.operation_id,
            output_ref=f"mysql://asset-validation-results/{request.target_id}",
        )

    def reconcile(
        self,
        request: OperationExecutionRequest,
    ) -> OperationReconciliationResult:
        return OperationReconciliationResult(
            operation_id=request.operation_id,
            outcome=ReconciliationOutcome.CONFIRMED_SUCCESS,
            output_ref=f"mysql://asset-validation-results/{request.target_id}",
        )


class SimulatedFinalizeCrash(RuntimeError):
    pass


class SimulatedValidationWorkerDeath(BaseException):
    pass


class CrashAfterCommittedValidationFailureExecutor:
    def __init__(self, delegate: AssetValidationExecutor) -> None:
        self._delegate = delegate
        self.crashed = False

    def execute(self, request: OperationExecutionRequest) -> OperationExecutionResult:
        try:
            return self._delegate.execute(request)
        except OperationExecutionFailure as exc:
            self.crashed = True
            raise SimulatedValidationWorkerDeath(
                "worker died after validation evidence committed"
            ) from exc

    def reconcile(
        self,
        request: OperationExecutionRequest,
    ) -> OperationReconciliationResult:
        return self._delegate.reconcile(request)


class BarrierAssetLocalValidator:
    def __init__(self, delegate: AssetLocalValidator, barrier: Barrier) -> None:
        self._delegate = delegate
        self._barrier = barrier

    def validate(self, request, stream):
        result = self._delegate.validate(request, stream)
        self._barrier.wait(timeout=15)
        return result


class PausingAssetLocalValidator:
    def __init__(self, delegate: AssetLocalValidator) -> None:
        self._delegate = delegate
        self.paused = Event()
        self.resume = Event()

    def validate(self, request, stream):
        result = self._delegate.validate(request, stream)
        self.paused.set()
        if not self.resume.wait(timeout=30):
            raise TimeoutError("validation lease-expiry test was not resumed")
        return result


class MutableValidationClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def set(self, value: datetime) -> None:
        self.value = value


class ExpireAfterMalwareScan:
    def __init__(
        self,
        delegate: DeterministicMalwareScanner,
        *,
        clock: MutableValidationClock,
        expires_at: datetime,
    ) -> None:
        self._delegate = delegate
        self._clock = clock
        self._expires_at = expires_at

    def identity(self) -> str:
        return self._delegate.identity()

    def scan(self, chunks, *, content_length: int):
        result = self._delegate.scan(chunks, content_length=content_length)
        self._clock.set(self._expires_at)
        return result


class RecordingContentSafetyAdapter:
    def __init__(
        self,
        *,
        outcome: ContentSafetyOutcome = ContentSafetyOutcome.PASS,
        failure_code: str | None = None,
        policy_version: str = "content-safety-policy-v1",
        mapping_version: str = "content-safety-map-v1",
        endpoint: str | None = None,
    ) -> None:
        self.calls = 0
        self._endpoint = endpoint
        self._delegate = DeterministicContentSafetyAdapter(
            outcome=outcome,
            policy_version=policy_version,
            mapping_version=mapping_version,
            failure_code=failure_code,
        )

    @property
    def configured_identity(self):
        identity = self._delegate.configured_identity
        return replace(identity, endpoint=self._endpoint) if self._endpoint else identity

    def moderate(self, request):
        self.calls += 1
        result = self._delegate.moderate(request)
        return replace(result, endpoint=self._endpoint) if self._endpoint else result


class CrashingContentSafetyAdapter:
    def __init__(self) -> None:
        self._delegate = DeterministicContentSafetyAdapter(
            outcome=ContentSafetyOutcome.PASS,
            policy_version="content-safety-policy-v1",
            mapping_version="content-safety-map-v1",
        )

    @property
    def configured_identity(self):
        return self._delegate.configured_identity

    def moderate(self, request):
        del request
        raise SimulatedValidationWorkerDeath(
            "worker died while the content-safety outcome was unknown"
        )


class RecordingProvenanceAdapter:
    def __init__(
        self,
        *,
        status: ProvenanceEvidenceStatus | None = ProvenanceEvidenceStatus.NOT_PRESENT,
        failure_code: str | None = None,
    ) -> None:
        if (status is None) == (failure_code is None):
            raise ValueError("provenance fixture requires evidence or one failure")
        self.calls = 0
        self._status = status
        self._failure_code = failure_code
        identity_delegate = DeterministicProvenanceAdapter(
            status=status or ProvenanceEvidenceStatus.NOT_PRESENT,
            trust_config_version="c2pa-trust-v1",
        )
        self._delegate = identity_delegate if status is not None else None
        self._configured_identity = identity_delegate.configured_identity

    @property
    def configured_identity(self):
        return self._configured_identity

    def verify(self, *, mime_type, stream, byte_length):
        self.calls += 1
        if self._delegate is not None:
            return self._delegate.verify(
                mime_type=mime_type,
                stream=stream,
                byte_length=byte_length,
            )
        del mime_type, stream, byte_length
        assert self._failure_code is not None
        identity = self._configured_identity
        return ProvenanceVerificationResult(
            outcome=ProvenanceVerificationOutcome.RETRYABLE_FAILURE,
            status=None,
            validator=identity.validator,
            sdk_version=identity.sdk_version,
            trust_config_version=identity.trust_config_version,
            trust_config_sha256=identity.trust_config_sha256,
            validation_state=None,
            manifest_count=0,
            failure_codes=(),
            remote_manifest_fetch=False,
            failure_code=self._failure_code,
            latency_ms=0,
        )


class RecordingExternalContentSafetyRequestFactory:
    external_transfer = True
    transfer_provider = "alibaba-green"
    transfer_endpoint_region = "cn-shanghai"

    def __init__(self) -> None:
        self.calls = 0
        self.expires_at: list[datetime] = []

    def __call__(
        self,
        *,
        asset_version,
        object_fact,
        expires_at: datetime,
    ) -> ContentSafetyImageRequest:
        del object_fact
        self.calls += 1
        self.expires_at.append(expires_at)
        return ContentSafetyImageRequest(
            data_id=asset_version.id,
            content_sha256=asset_version.sha256,
            image_url=f"https://controlled.example.test/{asset_version.id}",
            image_url_expires_at=expires_at,
            controlled_reference_id=f"asset-validation:{asset_version.id}",
        )


class PromotionUniqueFault:
    def __init__(self, fault: str) -> None:
        self.fault = fault
        self.triggered = False

    def raise_once(self, boundary: str) -> None:
        if self.triggered or self.fault != boundary:
            return
        self.triggered = True
        raise UniqueConstraintError(f"simulated concurrent unique winner at {boundary}")


class PromotionUniqueFaultAssetRepository:
    def __init__(self, delegate: object, fault: PromotionUniqueFault) -> None:
        self._delegate = delegate
        self._fault = fault

    def add_object(self, object_fact) -> None:
        if object_fact.role == "CONTROLLED_ORIGINAL":
            self._fault.raise_once("controlled_object")
        self._delegate.add_object(object_fact)  # type: ignore[attr-defined]

    def add_validation_result(self, result) -> None:
        if result.stage.value == "PROMOTION":
            self._fault.raise_once("promotion_result")
        self._delegate.add_validation_result(result)  # type: ignore[attr-defined]

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)


class PromotionUniqueFaultUnitOfWork:
    def __init__(
        self,
        session_factory: object,
        fault: PromotionUniqueFault,
    ) -> None:
        self._delegate = SqlAlchemyAssetUnitOfWork(session_factory)  # type: ignore[arg-type]
        self._fault = fault

    def __enter__(self):
        entered = self._delegate.__enter__()
        self.upload_sessions = entered.upload_sessions
        self.assets = PromotionUniqueFaultAssetRepository(entered.assets, self._fault)
        self.associations = entered.associations
        self.idempotency = entered.idempotency
        self.operations = entered.operations
        self.outbox = entered.outbox
        self.audit = entered.audit
        return self

    def __exit__(self, *args: object) -> None:
        self._delegate.__exit__(*args)  # type: ignore[arg-type]

    def commit(self) -> None:
        self._delegate.commit()


class MarkPromotionCommitAssetRepository:
    def __init__(
        self,
        delegate: object,
        mark_promotion: Callable[[], None],
    ) -> None:
        self._delegate = delegate
        self._mark_promotion = mark_promotion

    def add_validation_result(self, result) -> None:
        self._delegate.add_validation_result(result)  # type: ignore[attr-defined]
        if result.stage.value == "PROMOTION":
            self._mark_promotion()

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)


class ExpireAtPromotionCommitUnitOfWork:
    def __init__(
        self,
        session_factory: object,
        *,
        clock: MutableValidationClock,
        expires_at: datetime,
    ) -> None:
        self._delegate = SqlAlchemyAssetUnitOfWork(session_factory)  # type: ignore[arg-type]
        self._clock = clock
        self._expires_at = expires_at
        self._promotion_pending = False

    def __enter__(self):
        entered = self._delegate.__enter__()
        self.upload_sessions = entered.upload_sessions
        self.assets = MarkPromotionCommitAssetRepository(
            entered.assets,
            self._mark_promotion,
        )
        self.associations = entered.associations
        self.idempotency = entered.idempotency
        self.operations = entered.operations
        self.outbox = entered.outbox
        self.audit = entered.audit
        return self

    def __exit__(self, *args: object) -> None:
        self._delegate.__exit__(*args)  # type: ignore[arg-type]

    def commit(self) -> None:
        if self._promotion_pending:
            self._clock.set(self._expires_at)
        self._delegate.commit()

    def commit_before_retention_deadline(self, **kwargs: object) -> None:
        if self._promotion_pending:
            self._clock.set(self._expires_at)
        self._delegate.commit_before_retention_deadline(**kwargs)  # type: ignore[arg-type]

    def _mark_promotion(self) -> None:
        self._promotion_pending = True


class ExpireAfterTerminalAssetLockRepository:
    def __init__(
        self,
        delegate: object,
        *,
        clock: MutableValidationClock,
        expires_at: datetime,
    ) -> None:
        self._delegate = delegate
        self._clock = clock
        self._expires_at = expires_at

    def get(self, *args: object, **kwargs: object):
        asset = self._delegate.get(*args, **kwargs)  # type: ignore[attr-defined]
        if kwargs.get("for_update") is True:
            self._clock.set(self._expires_at)
        return asset

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)


class ExpireAfterTerminalAssetLockUnitOfWork:
    def __init__(
        self,
        session_factory: object,
        *,
        clock: MutableValidationClock,
        expires_at: datetime,
    ) -> None:
        self._delegate = SqlAlchemyAssetUnitOfWork(session_factory)  # type: ignore[arg-type]
        self._clock = clock
        self._expires_at = expires_at

    def __enter__(self):
        entered = self._delegate.__enter__()
        self.upload_sessions = entered.upload_sessions
        self.assets = ExpireAfterTerminalAssetLockRepository(
            entered.assets,
            clock=self._clock,
            expires_at=self._expires_at,
        )
        self.associations = entered.associations
        self.idempotency = entered.idempotency
        self.operations = entered.operations
        self.outbox = entered.outbox
        self.audit = entered.audit
        return self

    def __exit__(self, *args: object) -> None:
        self._delegate.__exit__(*args)  # type: ignore[arg-type]

    def commit(self) -> None:
        self._delegate.commit()


class SimulatedTerminalEventCommitFailure(RuntimeError):
    pass


class TerminalEventCommitFailureUnitOfWork:
    def __init__(self, session_factory: object) -> None:
        self._delegate = SqlAlchemyAssetUnitOfWork(session_factory)  # type: ignore[arg-type]

    def __enter__(self):
        entered = self._delegate.__enter__()
        self.upload_sessions = entered.upload_sessions
        self.assets = entered.assets
        self.associations = entered.associations
        self.idempotency = entered.idempotency
        self.operations = entered.operations
        self.outbox = entered.outbox
        self.audit = entered.audit
        return self

    def __exit__(self, *args: object) -> None:
        self._delegate.__exit__(*args)  # type: ignore[arg-type]

    def commit(self) -> None:
        raise SimulatedTerminalEventCommitFailure(
            "transaction stopped before Asset and terminal Outbox commit"
        )


class CrashOnFirstStatStorage:
    def __init__(self, delegate: ObjectStorage) -> None:
        self._delegate = delegate
        self._crash = True

    @property
    def backend(self):
        return self._delegate.backend

    def stat(self, reference: ObjectReference) -> ObjectStat:
        if self._crash:
            self._crash = False
            raise SimulatedFinalizeCrash("process stopped after claiming finalize")
        return self._delegate.stat(reference)

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)


class CrashAfterFirstCopyStorage:
    def __init__(self, delegate: ObjectStorage) -> None:
        self._delegate = delegate
        self._crash_after_copy = True

    @staticmethod
    def _assert_outside_transaction() -> None:
        assert not is_unit_of_work_active()

    @property
    def backend(self):
        return self._delegate.backend

    def presign_put(self, request: PresignPutRequest) -> PresignedRequest:
        self._assert_outside_transaction()
        return self._delegate.presign_put(request)

    def stat(self, reference: ObjectReference) -> ObjectStat:
        self._assert_outside_transaction()
        return self._delegate.stat(reference)

    def open_bounded_read(self, request: BoundedReadRequest):
        self._assert_outside_transaction()
        return self._delegate.open_bounded_read(request)

    def copy_if_absent(self, request: ConditionalCopyRequest) -> ObjectStat:
        self._assert_outside_transaction()
        result = self._delegate.copy_if_absent(request)
        if self._crash_after_copy:
            self._crash_after_copy = False
            raise SimulatedFinalizeCrash("process stopped after object copy")
        return result

    def delete_if_match(self, request: ConditionalDeleteRequest) -> bool:
        self._assert_outside_transaction()
        return self._delegate.delete_if_match(request)

    def temporary_read(self, request: TemporaryReadRequest) -> PresignedRequest:
        self._assert_outside_transaction()
        return self._delegate.temporary_read(request)


class BoundaryFaultStorage:
    def __init__(
        self,
        delegate: ObjectStorage,
        *,
        fault: str | None = None,
    ) -> None:
        self._delegate = delegate
        self._fault = fault
        self._armed = False
        self._fault_triggered = False
        self._first_io_observer: Callable[[], None] | None = None
        self.observed_finalize_io = False

    @property
    def backend(self):
        return self._delegate.backend

    def arm(
        self,
        *,
        first_io_observer: Callable[[], None] | None = None,
    ) -> None:
        self._armed = True
        self._first_io_observer = first_io_observer

    def _before_finalize_io(self) -> None:
        if not self._armed:
            return
        assert not is_unit_of_work_active()
        if not self.observed_finalize_io:
            if self._first_io_observer is not None:
                self._first_io_observer()
            self.observed_finalize_io = True

    def _should_fault(self, boundary: str) -> bool:
        if self._fault_triggered or self._fault != boundary:
            return False
        self._fault_triggered = True
        return True

    def presign_put(self, request: PresignPutRequest) -> PresignedRequest:
        return self._delegate.presign_put(request)

    def stat(self, reference: ObjectReference) -> ObjectStat:
        self._before_finalize_io()
        if reference.location == StorageLocationClass.QUARANTINE and self._should_fault(
            "source-stat"
        ):
            raise StorageUnavailableError("simulated source HEAD outage")
        return self._delegate.stat(reference)

    def open_bounded_read(self, request: BoundedReadRequest):
        self._before_finalize_io()
        if self._should_fault("bounded-read"):
            raise StorageUnavailableError("simulated bounded-read outage")
        return self._delegate.open_bounded_read(request)

    def copy_if_absent(self, request: ConditionalCopyRequest) -> ObjectStat:
        self._before_finalize_io()
        return self._delegate.copy_if_absent(request)

    def delete_if_match(self, request: ConditionalDeleteRequest) -> bool:
        self._before_finalize_io()
        return self._delegate.delete_if_match(request)

    def temporary_read(self, request: TemporaryReadRequest) -> PresignedRequest:
        self._before_finalize_io()
        return self._delegate.temporary_read(request)


class FailFirstRetentionDeleteStorage:
    def __init__(self, delegate: ObjectStorage) -> None:
        self._delegate = delegate
        self.failed = False

    @property
    def backend(self):
        return self._delegate.backend

    def delete_if_match(self, request: ConditionalDeleteRequest) -> bool:
        if not self.failed and request.reference.location == StorageLocationClass.QUARANTINE:
            self.failed = True
            raise StorageUnavailableError("simulated retention cleanup outage")
        return self._delegate.delete_if_match(request)

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)


class ConcurrentRetentionRaceStorage:
    """Pause promotion so cleanup's first destination sweep wins the race."""

    def __init__(
        self,
        delegate: ObjectStorage,
        *,
        clock: MutableValidationClock,
        expires_at: datetime,
        destination: ObjectReference,
    ) -> None:
        self._delegate = delegate
        self._clock = clock
        self._expires_at = expires_at
        self._destination = destination
        self.promotion_ready = Event()
        self.cleanup_swept_destination = Event()
        self.promotion_copied = Event()

    @property
    def backend(self):
        return self._delegate.backend

    def stat(self, reference: ObjectReference) -> ObjectStat:
        if reference != self._destination:
            return self._delegate.stat(reference)
        before_expiry = self._clock() < self._expires_at
        try:
            return self._delegate.stat(reference)
        except UploadObjectMissingError:
            if before_expiry:
                self.promotion_ready.set()
                if not self.cleanup_swept_destination.wait(timeout=30):
                    raise TimeoutError("retention cleanup never swept destination") from None
            else:
                self.cleanup_swept_destination.set()
                if not self.promotion_copied.wait(timeout=30):
                    raise TimeoutError("concurrent promotion never copied destination") from None
            raise

    def copy_if_absent(self, request: ConditionalCopyRequest) -> ObjectStat:
        copied = self._delegate.copy_if_absent(request)
        self.promotion_copied.set()
        return copied

    def list_versions(self, request: ObjectVersionListRequest) -> ObjectVersionPage:
        if request.reference != self._destination or self._clock() < self._expires_at:
            return self._delegate.list_versions(request)
        page = self._delegate.list_versions(request)
        if not self.cleanup_swept_destination.is_set():
            self.cleanup_swept_destination.set()
            if not self.promotion_copied.wait(timeout=30):
                raise TimeoutError("concurrent promotion never copied destination")
        return page

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)


class ForcedConcurrentVersionStorage:
    """Model a versioned store where two absent checks can both create a version."""

    def __init__(
        self,
        delegate: ObjectStorage,
        *,
        client: object,
        buckets: dict[StorageLocationClass, str],
        destination: ObjectReference,
        absent_barrier: Barrier,
        copy_barrier: Barrier,
        copied_barrier: Barrier,
    ) -> None:
        self._delegate = delegate
        self._client = client
        self._buckets = buckets
        self._destination = destination
        self._absent_barrier = absent_barrier
        self._copy_barrier = copy_barrier
        self._copied_barrier = copied_barrier
        self._observed_absent = False
        self.copy_version_ids: list[str] = []

    @property
    def backend(self):
        return self._delegate.backend

    def stat(self, reference: ObjectReference) -> ObjectStat:
        if (
            reference.location == self._destination.location
            and reference.key == self._destination.key
            and reference.version_id is None
            and not self._observed_absent
        ):
            try:
                return self._delegate.stat(reference)
            except UploadObjectMissingError:
                self._observed_absent = True
                self._absent_barrier.wait(timeout=30)
                raise
        return self._delegate.stat(reference)

    def copy_if_absent(self, request: ConditionalCopyRequest) -> ObjectStat:
        self._copy_barrier.wait(timeout=30)
        copy_source: dict[str, str] = {
            "Bucket": self._buckets[request.source.location],
            "Key": request.source.key,
        }
        if request.source.version_id is not None:
            copy_source["VersionId"] = request.source.version_id
        response = self._client.copy_object(  # type: ignore[attr-defined]
            Bucket=self._buckets[request.destination.location],
            Key=request.destination.key,
            CopySource=copy_source,
            CopySourceIfMatch=request.source_etag,
            ContentType=request.content_type,
            Metadata={
                "sha256": request.expected_sha256,
                "upload-session-id": request.upload_session_id,
            },
            MetadataDirective="REPLACE",
        )
        version_id = response.get("VersionId")
        assert isinstance(version_id, str) and version_id
        self.copy_version_ids.append(version_id)
        copied = self._delegate.stat(
            request.destination.model_copy(update={"version_id": version_id})
        )
        self._copied_barrier.wait(timeout=30)
        return copied

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)


class ControlledObjectCommitBarrier:
    def __init__(self) -> None:
        self._barrier = Barrier(2)
        self._lock = Lock()
        self._observations = 0

    def observe(self, controlled: object | None) -> None:
        with self._lock:
            if self._observations >= 2:
                return
            self._observations += 1
        assert controlled is None
        self._barrier.wait(timeout=30)


class ControlledObjectBarrierRepository:
    def __init__(self, delegate: object, barrier: ControlledObjectCommitBarrier) -> None:
        self._delegate = delegate
        self._barrier = barrier

    def get_object(self, **kwargs: object):
        query = kwargs
        if kwargs.get("role") == "ORIGINAL" and kwargs.get("for_update") is True:
            query = {**kwargs, "for_update": False}
        controlled = self._delegate.get_object(**query)  # type: ignore[attr-defined]
        if kwargs.get("role") == "CONTROLLED_ORIGINAL" and kwargs.get("for_update") is True:
            self._barrier.observe(controlled)
        return controlled

    def get(self, **kwargs: object):
        if kwargs.get("for_update") is True:
            kwargs = {**kwargs, "for_update": False}
        return self._delegate.get(**kwargs)  # type: ignore[attr-defined]

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)


class ControlledObjectBarrierUnitOfWork:
    def __init__(
        self,
        session_factory: object,
        barrier: ControlledObjectCommitBarrier,
    ) -> None:
        self._delegate = SqlAlchemyAssetUnitOfWork(session_factory)  # type: ignore[arg-type]
        self._barrier = barrier

    def __enter__(self):
        entered = self._delegate.__enter__()
        self.upload_sessions = entered.upload_sessions
        self.assets = ControlledObjectBarrierRepository(entered.assets, self._barrier)
        self.associations = entered.associations
        self.idempotency = entered.idempotency
        self.operations = entered.operations
        self.outbox = entered.outbox
        self.audit = entered.audit
        return self

    def __exit__(self, *args: object) -> None:
        self._delegate.__exit__(*args)  # type: ignore[arg-type]

    def commit(self) -> None:
        self._delegate.commit()


@pytest.fixture
def upload_settings(integration_settings: Settings) -> Settings:
    return Settings(
        environment="ci",
        service_name="upload-integration",
        mysql_dsn=integration_settings.mysql_dsn,
        rabbitmq_url=os.getenv(
            "CV_TEST_RABBITMQ_URL",
            "amqp://commercevision:commercevision@127.0.0.1:15673//",
        ),
        object_store_backend="minio",
        object_store_endpoint="http://127.0.0.1:19000",
        object_store_presign_endpoint="http://127.0.0.1:19000",
        object_store_access_key="commercevision",
        object_store_secret_key="commercevision-secret",
        object_store_region="us-east-1",
        object_store_force_path_style=True,
        trusted_principal_current_key_id=TRUSTED_KEY_ID,
        trusted_principal_current_hmac_secret=TRUSTED_SECRET,
        validation_data_transfer_enabled=True,
        validation_data_transfer_policy_version="integration-security-validation-v1",
        validation_data_transfer_allowed_workspace_ids=["upload-workspace"],
        validation_data_transfer_allowed_asset_kinds=["IMAGE"],
        validation_data_transfer_allowed_retention_classes=["TASK", "FOUNDATION"],
        validation_data_transfer_allowed_providers=["alibaba-green"],
        validation_data_transfer_allowed_endpoint_regions=["cn-shanghai"],
        validation_data_transfer_allowed_endpoint_hosts=["green-cip.cn-shanghai.aliyuncs.com"],
    )


@pytest.fixture
def minio_client(upload_settings: Settings) -> Iterator[object]:
    client = boto3.client(
        "s3",
        endpoint_url=upload_settings.object_store_endpoint,
        aws_access_key_id=upload_settings.object_store_access_key,
        aws_secret_access_key=upload_settings.object_store_secret_key.get_secret_value(),
        region_name=upload_settings.object_store_region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    try:
        for bucket in upload_settings.object_store_buckets.values():
            with suppress(client.exceptions.BucketAlreadyOwnedByYou):
                client.create_bucket(Bucket=bucket)
            client.put_bucket_versioning(
                Bucket=bucket,
                VersioningConfiguration={"Status": "Enabled"},
            )
    except Exception as exc:
        pytest.skip(f"MinIO integration service unavailable: {exc}")
    yield client


def test_real_worker_preflight_queries_mysql_and_authenticated_bucket_controls(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    del integration_database, minio_client

    assert probe_worker_dependencies(upload_settings) == {
        "broker": "ok",
        "mysql": "ok",
        "object_storage": "ok",
        "malware_scanner": "ok",
        "provider_result_storage": "ok",
        "milvus": "ok",
        "embedding_provider": "not_required",
        "vision_credential": "not_required",
    }


def _headers(
    key: str,
    workspace_id: str = "upload-workspace",
    *,
    actor_id: str = "upload-tester",
) -> dict[str, str]:
    return {
        **_read_headers(workspace_id, actor_id=actor_id),
        "X-Actor-Id": actor_id,
        "Idempotency-Key": key,
    }


def _read_headers(
    workspace_id: str = "upload-workspace",
    *,
    actor_id: str = "upload-tester",
) -> dict[str, str]:
    encoded = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "actor_id": actor_id,
                    "workspace_ids": [workspace_id],
                    "admin_workspace_ids": [],
                    "system_admin": False,
                    "issued_at": int(datetime.now(UTC).timestamp()),
                },
                separators=(",", ":"),
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    signature = hmac.new(
        TRUSTED_SECRET.encode(),
        f"{TRUSTED_KEY_ID}.{encoded}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-Workspace-Id": workspace_id,
        "X-Trusted-Principal": f"{TRUSTED_KEY_ID}.{encoded}.{signature}",
    }


def _create_session(
    client: TestClient,
    *,
    idempotency_key: str,
    content: bytes = VALID_PNG,
    sha256: str | None = None,
    filename: str = "pixel.png",
    declared_mime: str = "image/png",
    retention_class: str = "FOUNDATION",
    workflow_id: str | None = None,
    product_id: str | None = None,
    sku_id: str | None = None,
    asset_kind: str = "IMAGE",
) -> dict[str, object]:
    response = client.post(
        "/api/v1/upload-sessions",
        headers=_headers(idempotency_key),
        json=_upload_payload(
            content=content,
            sha256=sha256,
            filename=filename,
            declared_mime=declared_mime,
            retention_class=retention_class,
            workflow_id=workflow_id,
            product_id=product_id,
            sku_id=sku_id,
            asset_kind=asset_kind,
        ),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _upload_maintenance(
    *,
    integration_database: object,
    upload_settings: Settings,
) -> UploadSessionMaintenanceService:
    return UploadSessionMaintenanceService(
        uow_factory=lambda: SqlAlchemyAssetUnitOfWork(
            integration_database.session_factory  # type: ignore[attr-defined]
        ),
        batch_size=upload_settings.scheduler_batch_size,
        cleanup_policy=UploadCleanupPolicy(
            max_attempts=upload_settings.upload_cleanup_max_attempts,
            max_reconciliation_attempts=(upload_settings.upload_cleanup_reconcile_max_attempts),
            execution_max_elapsed=timedelta(
                seconds=upload_settings.operation_retry_max_elapsed_seconds
            ),
            presign_replay_grace=timedelta(
                seconds=upload_settings.upload_cleanup_presign_grace_seconds
            ),
            reconciliation_horizon=timedelta(
                seconds=upload_settings.upload_cleanup_reconcile_horizon_seconds
            ),
        ),
    )


def _asset_local_validator(
    upload_settings: Settings,
    *,
    maximum_image_decoded_bytes: int = 32 * 1024 * 1024,
) -> AssetLocalValidator:
    return AssetLocalValidator(
        maximum_image_bytes=upload_settings.upload_max_bytes,
        maximum_image_dimension=upload_settings.upload_max_image_dimension,
        maximum_image_pixels=upload_settings.upload_max_image_pixels,
        maximum_image_frames=upload_settings.upload_max_image_frames,
        maximum_image_decoded_bytes=maximum_image_decoded_bytes,
        maximum_metadata_bytes=upload_settings.upload_max_metadata_bytes,
        maximum_lora_bytes=100 * 1024 * 1024,
        maximum_safetensors_header_bytes=1024 * 1024,
        maximum_safetensors_tensors=4096,
        maximum_safetensors_rank=8,
        maximum_safetensors_dimension=1_000_000,
        maximum_safetensors_elements=100_000_000,
        maximum_prompt_bytes=256 * 1024,
        maximum_model_configuration_bytes=64 * 1024,
    )


def _validation_executor(
    *,
    integration_database: object,
    upload_settings: Settings,
    storage: ObjectStorage,
    local_validator: object | None = None,
    malware_scanner: object | None = None,
    content_safety: object | None = None,
    content_safety_request_factory: object | None = None,
    provenance: object | None = None,
    uow_factory: Callable[[], object] | None = None,
    clock: Callable[[], datetime] | None = None,
    policy: AssetValidationExecutorPolicy | None = None,
    validation_transfer_policy: ValidationDataTransferPolicy | None = None,
) -> AssetValidationExecutor:
    verifier = ImageUploadIntegrityVerifier(
        storage=storage,
        transaction_active=is_unit_of_work_active,
        maximum_bytes=upload_settings.upload_max_bytes,
        maximum_dimension=upload_settings.upload_max_image_dimension,
        maximum_pixels=upload_settings.upload_max_image_pixels,
        maximum_frames=upload_settings.upload_max_image_frames,
        maximum_metadata_bytes=upload_settings.upload_max_metadata_bytes,
    )
    resolved_uow_factory = uow_factory or (
        lambda: SqlAlchemyAssetUnitOfWork(
            integration_database.session_factory  # type: ignore[attr-defined]
        )
    )
    return AssetValidationExecutor(
        uow_factory=resolved_uow_factory,  # type: ignore[arg-type]
        storage=storage,
        local_validator=local_validator or _asset_local_validator(upload_settings),  # type: ignore[arg-type]
        malware_scanner=malware_scanner or DeterministicMalwareScanner(),  # type: ignore[arg-type]
        content_safety=content_safety
        or DeterministicContentSafetyAdapter(
            outcome=ContentSafetyOutcome.PASS,
            policy_version=upload_settings.content_safety_policy_version,
            mapping_version=upload_settings.content_safety_mapping_version,
        ),  # type: ignore[arg-type]
        content_safety_request_factory=content_safety_request_factory
        or DeterministicContentSafetyRequestFactory(),  # type: ignore[arg-type]
        provenance=provenance
        or DeterministicProvenanceAdapter(
            status=ProvenanceEvidenceStatus.NOT_PRESENT,
            trust_config_version=upload_settings.c2pa_trust_config_version,
        ),
        promoter=UploadPromoter(
            storage=storage,
            verifier=verifier,
            retention_version_page_size=(upload_settings.asset_retention_cleanup_version_page_size),
            retention_max_version_pages=(upload_settings.asset_retention_cleanup_max_version_pages),
            retention_max_versions=(upload_settings.asset_retention_cleanup_max_versions),
            retention_stable_empty_passes=(
                upload_settings.asset_retention_cleanup_stable_empty_passes
            ),
        ),
        validation_transfer_policy=validation_transfer_policy
        or ValidationDataTransferPolicy.from_settings(upload_settings),
        clock=clock,
        policy=policy,
    )


def _validation_operation_request(
    *,
    integration_database: object,
    operation_id: str,
) -> OperationExecutionRequest:
    with SqlAlchemyOperationUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        operation = uow.operations.get(
            operation_id,
            workspace_id="upload-workspace",
        )
    assert operation is not None
    return replace(
        OperationExecutionRequest.from_operation(operation),
        attempt_count=1,
    )


def test_upload_http_requires_a_trusted_workspace_principal(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    del minio_client
    payload = _upload_payload()

    with TestClient(create_app(upload_settings)) as client:
        missing = client.post(
            "/api/v1/upload-sessions",
            headers={
                "X-Workspace-Id": "upload-workspace",
                "X-Actor-Id": "browser-forged-actor",
                "Idempotency-Key": "upload-auth-missing-principal-0001",
            },
            json=payload,
        )
        assert missing.status_code == 401, missing.text

        forged = client.post(
            "/api/v1/upload-sessions",
            headers={
                "X-Workspace-Id": "upload-workspace",
                "X-Actor-Id": "browser-forged-actor",
                "X-Trusted-Principal": "forged.principal.signature",
                "Idempotency-Key": "upload-auth-forged-principal-0001",
            },
            json=payload,
        )
        assert forged.status_code == 401, forged.text

        wrong_workspace = client.post(
            "/api/v1/upload-sessions",
            headers={
                **_read_headers("other-workspace"),
                "X-Workspace-Id": "upload-workspace",
                "X-Actor-Id": "browser-forged-actor",
                "Idempotency-Key": "upload-auth-wrong-workspace-0001",
            },
            json=payload,
        )
        assert wrong_workspace.status_code == 403, wrong_workspace.text

        missing_actor = client.post(
            "/api/v1/upload-sessions",
            headers={
                **_read_headers(actor_id="trusted-upload-actor"),
                "Idempotency-Key": "upload-auth-missing-actor-0001",
            },
            json=payload,
        )
        assert missing_actor.status_code == 422, missing_actor.text
        assert missing_actor.json()["code"] == "VALIDATION_ERROR"

        mismatched_actor = client.post(
            "/api/v1/upload-sessions",
            headers={
                **_read_headers(actor_id="trusted-upload-actor"),
                "X-Actor-Id": "different-actor",
                "Idempotency-Key": "upload-auth-mismatched-actor-0001",
            },
            json=payload,
        )
        assert mismatched_actor.status_code == 401, mismatched_actor.text
        assert mismatched_actor.json()["code"] == "AUTHENTICATION_REQUIRED"

        trusted = client.post(
            "/api/v1/upload-sessions",
            headers=_headers(
                "upload-auth-trusted-principal-0001",
                actor_id="trusted-upload-actor",
            ),
            json=payload,
        )
        assert trusted.status_code == 201, trusted.text
        upload_session_id = trusted.json()["id"]

        missing_read_principal = client.get(
            f"/api/v1/upload-sessions/{upload_session_id}",
            headers={"X-Workspace-Id": "upload-workspace"},
        )
        assert missing_read_principal.status_code == 401

    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        persisted_actor = connection.execute(
            text("SELECT actor_id FROM upload_sessions WHERE id = :upload_session_id"),
            {"upload_session_id": upload_session_id},
        ).scalar_one()
    assert persisted_actor == "trusted-upload-actor"


def _upload_payload(
    *,
    content: bytes = VALID_PNG,
    sha256: str | None = None,
    filename: str = "pixel.png",
    declared_mime: str = "image/png",
    retention_class: str = "FOUNDATION",
    workflow_id: str | None = None,
    product_id: str | None = None,
    sku_id: str | None = None,
    asset_kind: str = "IMAGE",
) -> dict[str, object]:
    return {
        "retention_class": retention_class,
        "asset_kind": asset_kind,
        "filename": filename,
        "declared_mime": declared_mime,
        "byte_length": len(content),
        "sha256": sha256 or hashlib.sha256(content).hexdigest(),
        "workflow_id": workflow_id,
        "product_id": product_id,
        "sku_id": sku_id,
        "category": "beauty.skincare",
        "role": "product-primary",
    }


def _safetensors_fixture() -> bytes:
    header = json.dumps(
        {
            "lora.weight": {
                "dtype": "F32",
                "shape": [1],
                "data_offsets": [0, 4],
            }
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return struct.pack("<Q", len(header)) + header + b"\0" * 4


def _jpeg_with_icc_and_exif() -> bytes:
    target = io.BytesIO()
    image = Image.new("RGB", (4, 4), color="white")
    exif = Image.Exif()
    exif[0x9286] = b"ASCII\0\0\0" + b"E" * (48 * 1024)
    image.save(
        target,
        format="JPEG",
        quality=90,
        icc_profile=b"I" * (96 * 1024),
        exif=exif,
    )
    return target.getvalue()


def _catalog_payload(*, external_id: str, title: str) -> dict[str, object]:
    return {
        "source_namespace": "MANUAL",
        "external_id": external_id,
        "source_version": "manual-v1",
        "title": title,
        "category_code": "beauty.skincare",
        "brand": "Upload Integration",
        "attributes": {},
        "expires_at": None,
    }


def _direct_upload(session: dict[str, object], content: bytes = VALID_PNG) -> None:
    upload = session["upload"]
    assert isinstance(upload, dict)
    required_headers = upload["required_headers"]
    assert isinstance(required_headers, dict)
    direct_headers = {
        str(name): str(value)
        for name, value in required_headers.items()
        if str(name).lower() != "content-length"
    }
    uploaded = httpx.put(
        str(upload["url"]),
        headers=direct_headers,
        content=content,
    )
    assert uploaded.status_code == 200, uploaded.text


def _object_location(session: dict[str, object]) -> tuple[str, str]:
    upload = session["upload"]
    assert isinstance(upload, dict)
    path = unquote(urlparse(str(upload["url"])).path).lstrip("/")
    bucket, key = path.split("/", 1)
    return bucket, key


def _overwrite_uploaded_object(
    minio_client: object,
    *,
    session: dict[str, object],
    content: bytes,
) -> None:
    bucket, key = _object_location(session)
    minio_client.put_object(  # type: ignore[attr-defined]
        Bucket=bucket,
        Key=key,
        Body=content,
        ContentType="image/png",
        Metadata={
            "upload-session-id": str(session["id"]),
            "sha256": base64.b64encode(hashlib.sha256(content).digest()).decode(),
        },
    )


@pytest.mark.parametrize(
    ("asset_kind", "filename", "declared_mime", "content"),
    [
        (
            "LORA",
            "catalog-style.safetensors",
            "application/x-safetensors",
            _safetensors_fixture(),
        ),
        (
            "PROMPT_TEMPLATE",
            "catalog.prompt.json",
            "application/json",
            json.dumps(
                {
                    "schema_version": "commercevision.prompt-template.v1",
                    "name": "catalog",
                    "template": "Create {{ product_name }}",
                    "variables": [{"name": "product_name", "required": True}],
                },
                separators=(",", ":"),
            ).encode(),
        ),
        (
            "MODEL_CONFIGURATION",
            "catalog.model.json",
            "application/json",
            json.dumps(
                {
                    "schema_version": "commercevision.model-configuration.v1",
                    "provider": "alibaba",
                    "model_id": "wanx-v1",
                    "model_revision": "2026-07-01",
                    "parameters": {"steps": 30},
                },
                separators=(",", ":"),
            ).encode(),
        ),
    ],
)
def test_foundation_asset_kinds_direct_upload_and_finalize_in_quarantine(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
    asset_kind: str,
    filename: str,
    declared_mime: str,
    content: bytes,
) -> None:
    del integration_database, minio_client
    identity = asset_kind.lower().replace("_", "-")
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key=f"create-{identity}-upload-0001",
            asset_kind=asset_kind,
            filename=filename,
            declared_mime=declared_mime,
            content=content,
        )
        _direct_upload(session, content)

        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers(f"finalize-{identity}-upload-0001"),
            json={"expected_version": session["version"]},
        )

        assert finalized.status_code == 202, finalized.text
        body = finalized.json()
        assert body["asset"]["asset_kind"] == asset_kind
        assert body["asset"]["status"] == "QUARANTINED"
        assert body["asset_version"]["detected_mime"] is None
        assert body["asset_version"]["image_format"] is None
        assert body["asset_version"]["width"] is None
        assert body["asset_version"]["height"] is None
        assert body["asset_version"]["frame_count"] is None
        assert body["asset_version"]["object_state"] == "QUARANTINED"
        assert body["validation_operation"]["state"] == "PENDING"


def test_browser_can_direct_upload_and_finalize_one_quarantined_asset(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    sha256 = hashlib.sha256(VALID_PNG).hexdigest()
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(client, idempotency_key="create-upload-0001")
        source_bucket, source_key = _object_location(session)
        assert session["status"] == "OPEN"
        assert "storage_key" not in session
        assert "bucket" not in session

        _direct_upload(session)

        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-upload-0001"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
        body = finalized.json()
        assert body["upload_session"]["status"] == "FINALIZED"
        assert body["asset"]["status"] == "QUARANTINED"
        assert body["asset_version"]["sha256"] == sha256
        assert body["asset_version"]["detected_mime"] == "image/png"
        assert body["asset_version"]["object_state"] == "QUARANTINED"
        assert body["validation_operation"]["state"] == "PENDING"

        refreshed = client.get(
            f"/api/v1/upload-sessions/{session['id']}",
            headers=_read_headers(),
        )
        assert refreshed.status_code == 200
        assert refreshed.json()["asset_version_id"] == body["asset_version"]["id"]

        asset = client.get(
            f"/api/v1/assets/{body['asset']['id']}",
            headers=_read_headers(),
        )
        assert asset.status_code == 200
        assert asset.json()["current_version"]["id"] == body["asset_version"]["id"]

        hidden_asset = client.get(
            f"/api/v1/assets/{body['asset']['id']}",
            headers=_read_headers("upload-other-workspace"),
        )
        assert hidden_asset.status_code == 404
        assert hidden_asset.json()["code"] == "NOT_FOUND"

    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        object_fact = (
            connection.execute(
                text(
                    "SELECT ao.location, ao.bucket, ao.`key`, "
                    "us.destination_bucket, us.destination_key, us.cleanup_operation_id "
                    "FROM asset_objects AS ao "
                    "JOIN asset_versions AS av ON av.id = ao.asset_version_id "
                    "JOIN upload_sessions AS us ON us.id = av.upload_session_id "
                    "WHERE av.upload_session_id = :upload_session_id"
                ),
                {"upload_session_id": session["id"]},
            )
            .mappings()
            .one()
        )
    assert object_fact["location"] == "QUARANTINE"
    assert (object_fact["bucket"], object_fact["key"]) == (source_bucket, source_key)
    assert object_fact["cleanup_operation_id"] is None
    assert minio_client.head_object(  # type: ignore[attr-defined]
        Bucket=source_bucket,
        Key=source_key,
    )["ContentLength"] == len(VALID_PNG)
    with pytest.raises(ClientError) as missing_destination:
        minio_client.head_object(  # type: ignore[attr-defined]
            Bucket=object_fact["destination_bucket"],
            Key=object_fact["destination_key"],
        )
    assert missing_destination.value.response["ResponseMetadata"]["HTTPStatusCode"] == 404


def test_mysql_rejects_invalid_quarantined_object_facts(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    del minio_client
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-object-constraint-0001",
        )
        _direct_upload(session)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-object-constraint-0001"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text

    asset_version_id = finalized.json()["asset_version"]["id"]
    with (
        pytest.raises((IntegrityError, OperationalError)),
        integration_database.engine.begin() as connection,  # type: ignore[attr-defined]
    ):
        connection.execute(
            text(
                "UPDATE asset_objects SET provider_version_id = NULL "
                "WHERE asset_version_id = :asset_version_id"
            ),
            {"asset_version_id": asset_version_id},
        )
    with (
        pytest.raises((IntegrityError, OperationalError)),
        integration_database.engine.begin() as connection,  # type: ignore[attr-defined]
    ):
        connection.execute(
            text(
                "UPDATE asset_objects SET location = 'FOUNDATION' "
                "WHERE asset_version_id = :asset_version_id"
            ),
            {"asset_version_id": asset_version_id},
        )


def test_upload_session_id_is_canonicalized_before_mysql_lookup(
    integration_database: object,
    upload_settings: Settings,
) -> None:
    canonical_id = "019f8a00-0000-7000-8000-000000000001"
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-canonical-upload-id-0001",
        )
        with integration_database.engine.begin() as connection:  # type: ignore[attr-defined]
            connection.execute(
                text("UPDATE upload_sessions SET id = :canonical_id WHERE id = :upload_session_id"),
                {
                    "canonical_id": canonical_id,
                    "upload_session_id": session["id"],
                },
            )

        canonicalized = client.get(
            f"/api/v1/upload-sessions/{canonical_id.upper()}",
            headers=_read_headers(),
        )
        confusable = client.get(
            f"/api/v1/upload-sessions/{canonical_id.replace('a', 'á', 1)}",
            headers=_read_headers(),
        )

    assert canonicalized.status_code == 200, canonicalized.text
    assert canonicalized.json()["id"] == canonical_id
    assert confusable.status_code == 404, confusable.text


def test_finalize_commits_lease_before_io_and_all_business_facts_atomically(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del minio_client
    storage = BoundaryFaultStorage(build_object_storage(upload_settings))
    monkeypatch.setattr(api_container, "build_object_storage", lambda _settings: storage)

    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(client, idempotency_key="create-atomic-finalize-0001")
        _direct_upload(session)

        def assert_claimed_lease_is_visible() -> None:
            with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
                claim = (
                    connection.execute(
                        text(
                            "SELECT workspace_id, state, finalize_lease_token, "
                            "finalize_lease_expires_at, version "
                            "FROM upload_sessions WHERE id = :upload_session_id"
                        ),
                        {"upload_session_id": session["id"]},
                    )
                    .mappings()
                    .one()
                )
            assert claim["workspace_id"] == "upload-workspace"
            assert claim["state"] == "FINALIZING"
            assert claim["finalize_lease_token"] is not None
            assert claim["finalize_lease_expires_at"] is not None
            assert claim["version"] == int(session["version"]) + 1

        storage.arm(first_io_observer=assert_claimed_lease_is_visible)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-atomic-finalize-0001"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
        assert storage.observed_finalize_io
        body = finalized.json()
        replayed_after_response_loss = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-atomic-finalize-0001"),
            json={"expected_version": session["version"]},
        )
        assert replayed_after_response_loss.status_code == 202
        assert (
            replayed_after_response_loss.json()["asset_version"]["id"]
            == body["asset_version"]["id"]
        )
        asset_id = body["asset"]["id"]
        asset_version_id = body["asset_version"]["id"]
        operation_id = body["validation_operation"]["id"]

        with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
            facts = (
                connection.execute(
                    text(
                        "SELECT "
                        "(SELECT COUNT(*) FROM assets WHERE id = :asset_id "
                        "AND workspace_id = :workspace_id AND status = 'QUARANTINED') AS assets, "
                        "(SELECT COUNT(*) FROM asset_versions "
                        "WHERE id = :asset_version_id AND upload_session_id = :upload_session_id) "
                        "AS versions, "
                        "(SELECT COUNT(*) FROM asset_objects "
                        "WHERE asset_version_id = :asset_version_id "
                        "AND state = 'QUARANTINED' AND provider_version_id IS NOT NULL) "
                        "AS objects, "
                        "(SELECT COUNT(*) FROM durable_operations "
                        "WHERE id = :operation_id AND workspace_id = :workspace_id) AS operations, "
                        "(SELECT COUNT(*) FROM outbox_events "
                        "WHERE aggregate_id = :operation_id "
                        "AND event_type = 'asset.validation.requested') AS outbox_events, "
                        "(SELECT COUNT(*) FROM outbox_events "
                        "WHERE aggregate_id = :asset_id "
                        "AND event_type = 'asset.upload.finalized') AS finalized_events, "
                        "(SELECT COUNT(*) FROM audit_events "
                        "WHERE resource_id = :upload_session_id "
                        "AND action = 'asset.upload.finalized') AS audit_events, "
                        "(SELECT COUNT(*) FROM upload_sessions "
                        "WHERE id = :upload_session_id AND workspace_id = :workspace_id "
                        "AND state = 'FINALIZED' "
                        "AND finalized_asset_version_id = :asset_version_id "
                        "AND validation_operation_id = :operation_id) AS finalized_sessions"
                    ),
                    {
                        "asset_id": asset_id,
                        "asset_version_id": asset_version_id,
                        "operation_id": operation_id,
                        "upload_session_id": session["id"],
                        "workspace_id": "upload-workspace",
                    },
                )
                .mappings()
                .one()
            )
        assert set(facts.values()) == {1}


def test_finalize_cannot_reclaim_after_session_and_crashed_lease_expire(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del minio_client
    storage = CrashOnFirstStatStorage(build_object_storage(upload_settings))
    monkeypatch.setattr(api_container, "build_object_storage", lambda _settings: storage)

    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-crashed-expired-finalize-0001",
        )
        _direct_upload(session)
        finalize_key = "finalize-crashed-expired-finalize-0001"
        with pytest.raises(SimulatedFinalizeCrash, match="after claiming finalize"):
            client.post(
                f"/api/v1/upload-sessions/{session['id']}:finalize",
                headers=_headers(finalize_key),
                json={"expected_version": session["version"]},
            )

        expired_at = datetime.now(UTC) - timedelta(seconds=1)
        with integration_database.engine.begin() as connection:  # type: ignore[attr-defined]
            connection.execute(
                text(
                    "UPDATE upload_sessions "
                    "SET expires_at = :expired_at, finalize_lease_expires_at = :expired_at "
                    "WHERE id = :upload_session_id"
                ),
                {
                    "expired_at": expired_at.replace(tzinfo=None),
                    "upload_session_id": session["id"],
                },
            )

        retried = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers(finalize_key),
            json={"expected_version": session["version"]},
        )

    assert retried.status_code == 410, retried.text
    assert retried.json()["code"] == "UPLOAD_EXPIRED"
    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        facts = (
            connection.execute(
                text(
                    "SELECT "
                    "(SELECT state FROM upload_sessions WHERE id = :upload_session_id) "
                    "AS session_state, "
                    "(SELECT COUNT(*) FROM asset_versions "
                    "WHERE upload_session_id = :upload_session_id) AS asset_versions"
                ),
                {"upload_session_id": session["id"]},
            )
            .mappings()
            .one()
        )
    assert facts["session_state"] == "EXPIRED"
    assert facts["asset_versions"] == 0


def test_upload_and_finalize_idempotency_keys_bind_the_exact_request_hash(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    del integration_database, minio_client
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(client, idempotency_key="create-hash-binding-0001")
        create_conflict = client.post(
            "/api/v1/upload-sessions",
            headers=_headers("create-hash-binding-0001"),
            json=_upload_payload(filename="different.png"),
        )
        assert create_conflict.status_code == 409
        assert create_conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"

        _direct_upload(session)
        finalize_key = "finalize-hash-binding-0001"
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers(finalize_key),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text

        finalize_conflict = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers(finalize_key),
            json={"expected_version": int(session["version"]) + 1},
        )
        assert finalize_conflict.status_code == 409
        assert finalize_conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"


def test_upload_associations_are_workspace_scoped_and_composite(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    del integration_database, minio_client
    with TestClient(create_app(upload_settings)) as client:
        product = client.post(
            "/api/v1/products",
            headers=_headers("create-upload-product-0001"),
            json=_catalog_payload(
                external_id="UPLOAD-PRODUCT-001",
                title="Upload Product",
            ),
        )
        assert product.status_code == 201, product.text
        product_id = product.json()["id"]
        sku = client.post(
            f"/api/v1/products/{product_id}/skus",
            headers=_headers("create-upload-sku-0001"),
            json=_catalog_payload(
                external_id="UPLOAD-SKU-001",
                title="Upload SKU",
            ),
        )
        assert sku.status_code == 201, sku.text
        sku_id = sku.json()["id"]

        session = _create_session(
            client,
            idempotency_key="create-associated-upload-0001",
            product_id=product_id,
            sku_id=sku_id,
        )
        hidden_read = client.get(
            f"/api/v1/upload-sessions/{session['id']}",
            headers=_read_headers("upload-other-workspace"),
        )
        assert hidden_read.status_code == 404
        assert hidden_read.json()["code"] == "NOT_FOUND"

        hidden_finalize = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers(
                "finalize-associated-hidden-0001",
                "upload-other-workspace",
            ),
            json={"expected_version": session["version"]},
        )
        assert hidden_finalize.status_code == 404
        assert hidden_finalize.json()["code"] == "NOT_FOUND"

        cross_workspace_association = client.post(
            "/api/v1/upload-sessions",
            headers=_headers(
                "create-associated-hidden-0001",
                "upload-other-workspace",
            ),
            json=_upload_payload(product_id=product_id, sku_id=sku_id),
        )
        assert cross_workspace_association.status_code == 404
        assert cross_workspace_association.json()["code"] == "NOT_FOUND"

        _direct_upload(session)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-associated-upload-0001"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
        assert finalized.json()["asset"]["product_id"] == product_id
        assert finalized.json()["asset"]["sku_id"] == sku_id


def test_task_asset_retention_is_anchored_to_its_workflow(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    with TestClient(create_app(upload_settings)) as client:
        workflow = client.post(
            "/api/v1/workflows",
            headers=_headers("create-task-workflow-0001"),
            json={
                "workflow_type": "FIXTURE_IMAGE_GENERATION",
                "input_data": {"fixture_config": {"count": 1}},
                "retention_hours": 168,
            },
        )
        assert workflow.status_code == 202, workflow.text
        workflow_body = workflow.json()

        session = _create_session(
            client,
            idempotency_key="create-task-upload-0001",
            retention_class="TASK",
            workflow_id=workflow_body["id"],
        )
        session_expiry = datetime.fromisoformat(session["expires_at"])
        workflow_created = datetime.fromisoformat(workflow_body["created_at"])
        workflow_expiry = datetime.fromisoformat(workflow_body["expires_at"])
        task_asset_deadline = min(
            workflow_expiry,
            workflow_created + timedelta(hours=72),
        )
        assert session_expiry <= task_asset_deadline

        _direct_upload(session)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-task-upload-0001"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
        asset = finalized.json()["asset"]
        assert asset["retention_class"] == "TASK"
        assert asset["workflow_id"] == workflow_body["id"]
        assert datetime.fromisoformat(asset["retention_deadline"]) == task_asset_deadline

        with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
            command_expiries = list(
                connection.execute(
                    text(
                        "SELECT expires_at FROM idempotency_keys "
                        "WHERE resource_id = :upload_session_id"
                    ),
                    {"upload_session_id": session["id"]},
                ).scalars()
            )
            assert len(command_expiries) == 2
            assert all(
                expiry.replace(tzinfo=UTC) <= task_asset_deadline for expiry in command_expiries
            )

        rejected_session = _create_session(
            client,
            idempotency_key="create-task-rejected-upload-0001",
            retention_class="TASK",
            workflow_id=workflow_body["id"],
        )
        _direct_upload(rejected_session)
        _overwrite_uploaded_object(
            minio_client,
            session=rejected_session,
            content=VALID_PNG + b"x",
        )
        rejected = client.post(
            f"/api/v1/upload-sessions/{rejected_session['id']}:finalize",
            headers=_headers("finalize-task-rejected-upload-0001"),
            json={"expected_version": rejected_session["version"]},
        )
        assert rejected.status_code == 422, rejected.text

        with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
            rejected_audit = (
                connection.execute(
                    text(
                        "SELECT created_at, expires_at FROM audit_events "
                        "WHERE resource_id = :upload_session_id "
                        "AND action = 'asset.upload.rejected'"
                    ),
                    {"upload_session_id": rejected_session["id"]},
                )
                .mappings()
                .one()
            )
        rejected_audit_created = rejected_audit["created_at"].replace(tzinfo=UTC)
        rejected_audit_expiry = rejected_audit["expires_at"].replace(tzinfo=UTC)
        assert rejected_audit_expiry - rejected_audit_created == timedelta(days=180)
        assert rejected_audit_expiry > workflow_expiry


@pytest.mark.parametrize(
    ("content", "filename", "mime_type", "image_format"),
    [
        (VALID_JPEG, "pixel.jpg", "image/jpeg", "JPEG"),
        (VALID_WEBP, "pixel.webp", "image/webp", "WEBP"),
    ],
)
def test_finalize_accepts_each_supported_raster_format(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
    content: bytes,
    filename: str,
    mime_type: str,
    image_format: str,
) -> None:
    del integration_database, minio_client
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key=f"create-format-{image_format.lower()}",
            content=content,
            filename=filename,
            declared_mime=mime_type,
        )
        _direct_upload(session, content)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers(f"finalize-format-{image_format.lower()}"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
        assert finalized.json()["asset_version"]["image_format"] == image_format
        assert finalized.json()["asset_version"]["detected_mime"] == mime_type


@pytest.mark.parametrize(
    ("content", "filename", "mime_type", "case"),
    [
        (VALID_PNG, "pixel.jpg", "image/jpeg", "mime"),
        (VALID_PNG[:-8], "pixel.png", "image/png", "truncated"),
        (OVERSIZED_DIMENSION_PNG, "wide.png", "image/png", "dimensions"),
        (ANIMATED_WEBP, "animated.webp", "image/webp", "frames"),
    ],
)
def test_finalize_rejects_unsafe_or_inconsistent_image_decode(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
    content: bytes,
    filename: str,
    mime_type: str,
    case: str,
) -> None:
    del integration_database, minio_client
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key=f"create-invalid-image-{case}",
            content=content,
            filename=filename,
            declared_mime=mime_type,
        )
        _direct_upload(session, content)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers(f"finalize-invalid-image-{case}"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 422
        assert finalized.json()["code"] == "OBJECT_MISMATCH"


@pytest.mark.parametrize(
    ("tampered", "case"),
    [
        (VALID_PNG[:-1] + bytes([VALID_PNG[-1] ^ 1]), "checksum"),
        (VALID_PNG + b"x", "length"),
    ],
)
def test_finalize_rejects_tampered_object_facts(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
    tampered: bytes,
    case: str,
) -> None:
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key=f"create-mismatch-{case}",
        )
        _direct_upload(session)
        _overwrite_uploaded_object(
            minio_client,
            session=session,
            content=tampered,
        )

        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers(f"finalize-mismatch-{case}"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 422
        assert finalized.json()["code"] == "OBJECT_MISMATCH"

        replay = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers(f"finalize-mismatch-{case}"),
            json={"expected_version": session["version"]},
        )
        assert replay.status_code == 422
        assert replay.json()["code"] == "OBJECT_MISMATCH"

        current = client.get(
            f"/api/v1/upload-sessions/{session['id']}",
            headers=_read_headers(),
        )
        assert current.status_code == 200
        assert current.json()["status"] == "ABORTED"
        assert current.json()["failure_code"] == "OBJECT_MISMATCH"

        if case == "length":
            cleanup_operation_id = current.json()["cleanup_operation_id"]
            assert cleanup_operation_id is not None
            with SqlAlchemyUnitOfWork(
                integration_database.session_factory  # type: ignore[attr-defined]
            ) as uow:
                cleanup_event = next(
                    event
                    for event in uow.outbox.list_for_aggregate(cleanup_operation_id)
                    if event.envelope.event_type == "asset.delete.requested"
                )
            simulated_now = datetime.now(UTC)
            with integration_database.engine.begin() as connection:  # type: ignore[attr-defined]
                connection.execute(
                    text(
                        "UPDATE upload_sessions SET expires_at = :expired_at "
                        "WHERE id = :upload_session_id"
                    ),
                    {
                        "expired_at": (simulated_now - timedelta(seconds=1)).replace(tzinfo=None),
                        "upload_session_id": session["id"],
                    },
                )
                connection.execute(
                    text(
                        "UPDATE outbox_events SET available_at = :available_at WHERE id = :event_id"
                    ),
                    {
                        "available_at": (simulated_now - timedelta(seconds=1)).replace(tzinfo=None),
                        "event_id": cleanup_event.envelope.event_id,
                    },
                )
            worker = WorkerRuntime.build(upload_settings)
            try:
                assert worker.process_event(cleanup_event.envelope.event_id) == "processed"
            finally:
                worker.close()
            with SqlAlchemyOperationUnitOfWork(
                integration_database.session_factory  # type: ignore[attr-defined]
            ) as uow:
                cleanup_operation = uow.operations.get(
                    cleanup_operation_id,
                    workspace_id="upload-workspace",
                )
            assert cleanup_operation is not None
            assert cleanup_operation.state.value == "RECONCILING"
            with integration_database.engine.begin() as connection:  # type: ignore[attr-defined]
                connection.execute(
                    text(
                        "UPDATE durable_operations "
                        "SET next_reconciliation_at = :next_reconciliation_at "
                        "WHERE id = :operation_id"
                    ),
                    {
                        "next_reconciliation_at": (
                            datetime.now(UTC) - timedelta(seconds=1)
                        ).replace(tzinfo=None),
                        "operation_id": cleanup_operation_id,
                    },
                )
            scanner = OperationRecoveryService(
                uow_factory=lambda: SqlAlchemyOperationUnitOfWork(
                    integration_database.session_factory  # type: ignore[attr-defined]
                ),
                batch_size=10,
            )
            assert scanner.recover_once(now=datetime.now(UTC)) == 1
            with SqlAlchemyUnitOfWork(
                integration_database.session_factory  # type: ignore[attr-defined]
            ) as uow:
                recovery_event = next(
                    event
                    for event in uow.outbox.list_for_aggregate(cleanup_operation_id)
                    if event.envelope.event_type == "operation.recovery.requested"
                )
            recovery_worker = WorkerRuntime.build(upload_settings)
            try:
                assert (
                    recovery_worker.process_event(recovery_event.envelope.event_id) == "processed"
                )
            finally:
                recovery_worker.close()
            source = _object_location(session)
            with pytest.raises(ClientError) as missing:
                minio_client.head_object(  # type: ignore[attr-defined]
                    Bucket=source[0],
                    Key=source[1],
                )
            assert missing.value.response["ResponseMetadata"]["HTTPStatusCode"] == 404


def test_expired_and_aborted_sessions_cannot_finalize(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    del minio_client
    with TestClient(create_app(upload_settings)) as client:
        expired = _create_session(client, idempotency_key="create-expired-0001")
        with integration_database.engine.begin() as connection:  # type: ignore[attr-defined]
            connection.execute(
                text(
                    "UPDATE upload_sessions SET expires_at = :expires_at "
                    "WHERE id = :upload_session_id"
                ),
                {
                    "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).replace(tzinfo=None),
                    "upload_session_id": expired["id"],
                },
            )
        expired_create_replay = client.post(
            "/api/v1/upload-sessions",
            headers=_headers("create-expired-0001"),
            json=_upload_payload(),
        )
        assert expired_create_replay.status_code == 410
        assert expired_create_replay.json()["code"] == "UPLOAD_EXPIRED"

        expired_finalize = client.post(
            f"/api/v1/upload-sessions/{expired['id']}:finalize",
            headers=_headers("finalize-expired-0001"),
            json={"expected_version": expired["version"]},
        )
        assert expired_finalize.status_code == 410
        assert expired_finalize.json()["code"] == "UPLOAD_EXPIRED"

        aborted = _create_session(client, idempotency_key="create-aborted-0001")
        abort_response = client.post(
            f"/api/v1/upload-sessions/{aborted['id']}:abort",
            headers=_headers("abort-upload-0001"),
            json={"expected_version": aborted["version"]},
        )
        assert abort_response.status_code == 200
        assert abort_response.json()["status"] == "ABORTED"
        aborted_finalize = client.post(
            f"/api/v1/upload-sessions/{aborted['id']}:finalize",
            headers=_headers("finalize-aborted-0001"),
            json={"expected_version": abort_response.json()["version"]},
        )
        assert aborted_finalize.status_code == 409
        assert aborted_finalize.json()["code"] == "UPLOAD_ABORTED"


def test_abort_defers_cleanup_until_the_presigned_put_can_no_longer_replay(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    delayed_cleanup_settings = upload_settings.model_copy(
        update={"operation_retry_max_elapsed_seconds": 60.0}
    )
    with TestClient(create_app(delayed_cleanup_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-abort-replay-window-0001",
        )
        aborted = client.post(
            f"/api/v1/upload-sessions/{session['id']}:abort",
            headers=_headers("abort-replay-window-0001"),
            json={"expected_version": session["version"]},
        )
        assert aborted.status_code == 200, aborted.text
        cleanup_operation_id = aborted.json()["cleanup_operation_id"]
        assert cleanup_operation_id

        # An issued presigned URL cannot be revoked by changing MySQL state.
        _direct_upload(session)
        bucket, key = _object_location(session)
        assert minio_client.head_object(Bucket=bucket, Key=key)["ContentLength"] == len(VALID_PNG)  # type: ignore[attr-defined]

    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        cleanup_schedule = (
            connection.execute(
                text(
                    "SELECT oe.id AS event_id, oe.available_at, op.execution_deadline_at "
                    "FROM outbox_events AS oe "
                    "JOIN durable_operations AS op ON op.id = oe.aggregate_id "
                    "WHERE oe.aggregate_id = :cleanup_operation_id "
                    "AND oe.event_type = 'asset.delete.requested'"
                ),
                {"cleanup_operation_id": cleanup_operation_id},
            )
            .mappings()
            .one()
        )

    session_expires_at = datetime.fromisoformat(session["expires_at"])
    available_at = cleanup_schedule["available_at"].replace(tzinfo=UTC)
    assert available_at == session_expires_at + timedelta(
        seconds=delayed_cleanup_settings.upload_cleanup_presign_grace_seconds
    )
    assert cleanup_schedule["execution_deadline_at"].replace(
        tzinfo=UTC
    ) == available_at + timedelta(
        seconds=delayed_cleanup_settings.operation_retry_max_elapsed_seconds
    )

    worker = WorkerRuntime.build(delayed_cleanup_settings)
    try:
        assert worker.process_event(cleanup_schedule["event_id"]) == "retry-not-ready"
        with integration_database.engine.begin() as connection:  # type: ignore[attr-defined]
            connection.execute(
                text("UPDATE outbox_events SET available_at = :available_at WHERE id = :event_id"),
                {
                    "available_at": (datetime.now(UTC) - timedelta(seconds=1)).replace(tzinfo=None),
                    "event_id": cleanup_schedule["event_id"],
                },
            )
        assert worker.process_event(cleanup_schedule["event_id"]) == "processed"
    finally:
        worker.close()

    with pytest.raises(ClientError) as missing:
        minio_client.head_object(Bucket=bucket, Key=key)  # type: ignore[attr-defined]
    assert missing.value.response["ResponseMetadata"]["HTTPStatusCode"] == 404


def test_scheduler_expires_an_abandoned_open_upload_without_api_traffic(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-autonomous-expiry-0001",
        )
        _direct_upload(session)
    bucket, key = _object_location(session)
    expired_at = datetime.now(UTC) - timedelta(
        seconds=upload_settings.upload_cleanup_presign_grace_seconds + 1
    )
    with integration_database.engine.begin() as connection:  # type: ignore[attr-defined]
        connection.execute(
            text(
                "UPDATE upload_sessions SET expires_at = :expires_at WHERE id = :upload_session_id"
            ),
            {
                "expires_at": expired_at.replace(tzinfo=None),
                "upload_session_id": session["id"],
            },
        )

    maintenance = _upload_maintenance(
        integration_database=integration_database,
        upload_settings=upload_settings,
    )
    assert maintenance.expire_due_once(now=datetime.now(UTC)) == 1

    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        facts = (
            connection.execute(
                text(
                    "SELECT us.state, us.cleanup_operation_id, "
                    "op.max_attempts, oe.id AS event_id "
                    "FROM upload_sessions AS us "
                    "JOIN durable_operations AS op "
                    "ON op.workspace_id = us.workspace_id "
                    "AND op.id = us.cleanup_operation_id "
                    "JOIN outbox_events AS oe ON oe.aggregate_id = us.cleanup_operation_id "
                    "WHERE us.id = :upload_session_id "
                    "AND oe.event_type = 'asset.delete.requested'"
                ),
                {"upload_session_id": session["id"]},
            )
            .mappings()
            .one()
        )
    assert facts["state"] == "EXPIRED"
    assert facts["cleanup_operation_id"]
    assert facts["max_attempts"] == upload_settings.upload_cleanup_max_attempts

    worker = WorkerRuntime.build(upload_settings)
    try:
        assert worker.process_event(facts["event_id"]) == "processed"
    finally:
        worker.close()

    with pytest.raises(ClientError) as missing:
        minio_client.head_object(Bucket=bucket, Key=key)  # type: ignore[attr-defined]
    assert missing.value.response["ResponseMetadata"]["HTTPStatusCode"] == 404


def test_durable_operation_reconciles_a_presigned_put_replayed_after_initial_cleanup(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-late-put-reconciliation-0001",
        )
        aborted = client.post(
            f"/api/v1/upload-sessions/{session['id']}:abort",
            headers=_headers("abort-late-put-reconciliation-0001"),
            json={"expected_version": session["version"]},
        )
        assert aborted.status_code == 200, aborted.text
        initial_cleanup_operation_id = aborted.json()["cleanup_operation_id"]

    with integration_database.engine.begin() as connection:  # type: ignore[attr-defined]
        initial_event_id = connection.execute(
            text(
                "SELECT id FROM outbox_events "
                "WHERE aggregate_id = :operation_id "
                "AND event_type = 'asset.delete.requested'"
            ),
            {"operation_id": initial_cleanup_operation_id},
        ).scalar_one()
        connection.execute(
            text("UPDATE outbox_events SET available_at = :available_at WHERE id = :event_id"),
            {
                "available_at": (datetime.now(UTC) - timedelta(seconds=1)).replace(tzinfo=None),
                "event_id": initial_event_id,
            },
        )

    worker = WorkerRuntime.build(upload_settings)
    try:
        assert worker.process_event(initial_event_id) == "processed"
    finally:
        worker.close()

    # The URL remains a bearer capability until its signed expiry.
    _direct_upload(session)
    bucket, key = _object_location(session)
    assert minio_client.head_object(Bucket=bucket, Key=key)["ContentLength"] == len(VALID_PNG)  # type: ignore[attr-defined]

    with SqlAlchemyOperationUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        cleanup_operation = uow.operations.get(
            initial_cleanup_operation_id,
            workspace_id="upload-workspace",
        )
    assert cleanup_operation is not None
    assert cleanup_operation.state.value == "RECONCILING"

    recovery = OperationRecoveryService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(
            integration_database.session_factory  # type: ignore[attr-defined]
        ),
        batch_size=upload_settings.scheduler_batch_size,
        reconciliation_max_elapsed=timedelta(
            seconds=upload_settings.operation_reconciliation_max_elapsed_seconds
        ),
    )
    assert recovery.recover_once(now=datetime.now(UTC)) == 1
    with SqlAlchemyUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        recovery_event = next(
            event
            for event in uow.outbox.list_for_aggregate(initial_cleanup_operation_id)
            if event.envelope.event_type == "operation.recovery.requested"
        )
    with integration_database.engine.begin() as connection:  # type: ignore[attr-defined]
        connection.execute(
            text("UPDATE outbox_events SET available_at = :available_at WHERE id = :event_id"),
            {
                "available_at": (datetime.now(UTC) - timedelta(seconds=1)).replace(tzinfo=None),
                "event_id": recovery_event.envelope.event_id,
            },
        )

    worker = WorkerRuntime.build(upload_settings)
    try:
        assert worker.process_event(recovery_event.envelope.event_id) == "processed"
    finally:
        worker.close()

    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        operation_facts = (
            connection.execute(
                text(
                    "SELECT us.cleanup_operation_id, op.state, op.max_attempts "
                    "FROM upload_sessions AS us "
                    "JOIN durable_operations AS op ON op.id = us.cleanup_operation_id "
                    "WHERE us.id = :upload_session_id"
                ),
                {"upload_session_id": session["id"]},
            )
            .mappings()
            .one()
        )
    assert operation_facts == {
        "cleanup_operation_id": initial_cleanup_operation_id,
        "state": "RECONCILING",
        "max_attempts": upload_settings.upload_cleanup_max_attempts,
    }
    with pytest.raises(ClientError) as missing:
        minio_client.head_object(Bucket=bucket, Key=key)  # type: ignore[attr-defined]
    assert missing.value.response["ResponseMetadata"]["HTTPStatusCode"] == 404


def test_expiry_atomically_schedules_one_durable_cleanup_command(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    del minio_client
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-expiry-cleanup-command-0001",
        )
        with integration_database.engine.begin() as connection:  # type: ignore[attr-defined]
            connection.execute(
                text(
                    "UPDATE upload_sessions SET expires_at = :expires_at "
                    "WHERE id = :upload_session_id"
                ),
                {
                    "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).replace(tzinfo=None),
                    "upload_session_id": session["id"],
                },
            )

        expired = client.get(
            f"/api/v1/upload-sessions/{session['id']}",
            headers=_read_headers(),
        )
        assert expired.status_code == 200, expired.text
        expired_body = expired.json()
        assert expired_body["status"] == "EXPIRED"
        cleanup_operation_id = expired_body["cleanup_operation_id"]
        assert cleanup_operation_id

        repeated = client.get(
            f"/api/v1/upload-sessions/{session['id']}",
            headers=_read_headers(),
        )
        assert repeated.status_code == 200
        assert repeated.json()["cleanup_operation_id"] == cleanup_operation_id
        assert repeated.json()["version"] == expired_body["version"]

        with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
            facts = (
                connection.execute(
                    text(
                        "SELECT us.cleanup_operation_id, us.version AS session_version, "
                        "op.kind, op.target_type, op.target_id, op.target_version, op.state, "
                        "oe.event_type, oe.aggregate_id, oe.workspace_id, oe.payload_json "
                        "FROM upload_sessions AS us "
                        "JOIN durable_operations AS op "
                        "ON op.workspace_id = us.workspace_id "
                        "AND op.id = us.cleanup_operation_id "
                        "JOIN outbox_events AS oe "
                        "ON oe.workspace_id = op.workspace_id "
                        "AND oe.aggregate_id = op.id "
                        "WHERE us.workspace_id = :workspace_id "
                        "AND us.id = :upload_session_id"
                    ),
                    {
                        "workspace_id": "upload-workspace",
                        "upload_session_id": session["id"],
                    },
                )
                .mappings()
                .one()
            )

        assert facts["cleanup_operation_id"] == cleanup_operation_id
        assert facts["kind"] == "ASSET_DELETION"
        assert facts["target_type"] == "UPLOAD_SESSION"
        assert facts["target_id"] == session["id"]
        assert facts["target_version"] == facts["session_version"]
        assert facts["state"] == "PENDING"
        assert facts["event_type"] == "asset.delete.requested"
        assert facts["aggregate_id"] == cleanup_operation_id
        payload = json.loads(facts["payload_json"])
        assert payload == {
            "operation_id": cleanup_operation_id,
            "workspace_id": "upload-workspace",
            "target_type": "UPLOAD_SESSION",
            "target_id": session["id"],
            "target_version": facts["session_version"],
            "reason": "UPLOAD_EXPIRED",
        }


def test_duplicate_and_concurrent_finalize_converge_on_one_asset_version(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    del integration_database, minio_client
    app_one = create_app(upload_settings)
    app_two = create_app(upload_settings)
    with TestClient(app_one) as first_client, TestClient(app_two) as second_client:
        session = _create_session(first_client, idempotency_key="create-concurrent-0001")
        _direct_upload(session)

        def finalize(client: TestClient, key: str):
            return client.post(
                f"/api/v1/upload-sessions/{session['id']}:finalize",
                headers=_headers(key),
                json={"expected_version": session["version"]},
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(finalize, first_client, "finalize-concurrent-a"),
                executor.submit(finalize, second_client, "finalize-concurrent-b"),
            ]
            responses = [future.result(timeout=30) for future in futures]

        final_bodies: list[dict[str, object]] = []
        for index, response in enumerate(responses):
            if response.status_code != 202:
                assert response.status_code == 409, response.text
                response = finalize(
                    first_client,
                    "finalize-concurrent-a" if index == 0 else "finalize-concurrent-b",
                )
            assert response.status_code == 202, response.text
            final_bodies.append(response.json())

        first_version = final_bodies[0]["asset_version"]
        second_version = final_bodies[1]["asset_version"]
        assert isinstance(first_version, dict)
        assert isinstance(second_version, dict)
        assert first_version["id"] == second_version["id"]

        duplicate = finalize(first_client, "finalize-concurrent-a")
        assert duplicate.status_code == 202
        assert duplicate.json()["asset_version"]["id"] == first_version["id"]


def test_storage_outage_releases_finalize_for_same_idempotent_retry(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    del integration_database, minio_client
    with TestClient(create_app(upload_settings)) as healthy_client:
        session = _create_session(healthy_client, idempotency_key="create-outage-0001")
        _direct_upload(session)

    unavailable_settings = upload_settings.model_copy(
        update={
            "object_store_endpoint": "http://127.0.0.1:19999",
            "object_store_presign_endpoint": "http://127.0.0.1:19999",
            "object_store_connect_timeout_seconds": 0.2,
            "object_store_read_timeout_seconds": 0.2,
        }
    )
    finalize_idempotency = "finalize-outage-0001"
    with TestClient(create_app(unavailable_settings)) as unavailable_client:
        unavailable = unavailable_client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers(finalize_idempotency),
            json={"expected_version": session["version"]},
        )
        assert unavailable.status_code == 503
        assert unavailable.json()["code"] == "STORAGE_UNAVAILABLE"

    with TestClient(create_app(upload_settings)) as recovered_client:
        current = recovered_client.get(
            f"/api/v1/upload-sessions/{session['id']}",
            headers=_read_headers(),
        )
        assert current.status_code == 200
        assert current.json()["status"] == "OPEN"

        recovered = recovered_client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers(finalize_idempotency),
            json={"expected_version": session["version"]},
        )
        assert recovered.status_code == 202, recovered.text
        assert recovered.json()["upload_session"]["status"] == "FINALIZED"


def test_storage_outage_crossing_expiry_schedules_one_durable_cleanup(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = BoundaryFaultStorage(
        build_object_storage(upload_settings),
        fault="source-stat",
    )
    monkeypatch.setattr(api_container, "build_object_storage", lambda _settings: storage)

    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-expiring-storage-outage-0001",
        )
        source = _object_location(session)
        _direct_upload(session)

        def expire_during_storage_io() -> None:
            with integration_database.engine.begin() as connection:  # type: ignore[attr-defined]
                connection.execute(
                    text(
                        "UPDATE upload_sessions SET expires_at = :expires_at "
                        "WHERE id = :upload_session_id"
                    ),
                    {
                        "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).replace(
                            tzinfo=None
                        ),
                        "upload_session_id": session["id"],
                    },
                )

        storage.arm(first_io_observer=expire_during_storage_io)
        unavailable = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-expiring-storage-outage-0001"),
            json={"expected_version": session["version"]},
        )
        assert unavailable.status_code == 503, unavailable.text
        assert unavailable.json()["code"] == "STORAGE_UNAVAILABLE"

    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        facts = (
            connection.execute(
                text(
                    "SELECT us.state, us.cleanup_operation_id, "
                    "(SELECT COUNT(*) FROM durable_operations AS op "
                    "WHERE op.target_type = 'UPLOAD_SESSION' "
                    "AND op.target_id = us.id AND op.kind = 'ASSET_DELETION') "
                    "AS cleanup_operations, "
                    "(SELECT COUNT(*) FROM outbox_events AS oe "
                    "JOIN durable_operations AS op ON op.id = oe.aggregate_id "
                    "WHERE op.target_type = 'UPLOAD_SESSION' "
                    "AND op.target_id = us.id "
                    "AND oe.event_type = 'asset.delete.requested') AS cleanup_events "
                    "FROM upload_sessions AS us WHERE us.id = :upload_session_id"
                ),
                {"upload_session_id": session["id"]},
            )
            .mappings()
            .one()
        )
    assert facts == {
        "state": "EXPIRED",
        "cleanup_operation_id": facts["cleanup_operation_id"],
        "cleanup_operations": 1,
        "cleanup_events": 1,
    }
    assert facts["cleanup_operation_id"] is not None
    assert minio_client.head_object(  # type: ignore[attr-defined]
        Bucket=source[0],
        Key=source[1],
    )["ContentLength"] == len(VALID_PNG)


@pytest.mark.parametrize(
    "fault",
    [
        "source-stat",
        "bounded-read",
    ],
)
def test_finalize_recovers_from_each_storage_io_boundary(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    del minio_client
    storage = BoundaryFaultStorage(build_object_storage(upload_settings), fault=fault)
    monkeypatch.setattr(api_container, "build_object_storage", lambda _settings: storage)

    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key=f"create-boundary-{fault}-0001",
        )
        _direct_upload(session)
        storage.arm()
        finalize_path = f"/api/v1/upload-sessions/{session['id']}:finalize"
        finalize_headers = _headers(f"finalize-boundary-{fault}-0001")
        finalize_body = {"expected_version": session["version"]}

        unavailable = client.post(
            finalize_path,
            headers=finalize_headers,
            json=finalize_body,
        )
        assert unavailable.status_code == 503, unavailable.text
        assert unavailable.json()["code"] == "STORAGE_UNAVAILABLE"

        with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
            before_retry = (
                connection.execute(
                    text(
                        "SELECT "
                        "(SELECT state FROM upload_sessions "
                        "WHERE id = :upload_session_id) AS session_state, "
                        "(SELECT COUNT(*) FROM asset_versions "
                        "WHERE upload_session_id = :upload_session_id) AS versions, "
                        "(SELECT COUNT(*) FROM assets "
                        "WHERE id = :asset_id) AS assets"
                    ),
                    {
                        "asset_id": session["reserved_asset_id"],
                        "upload_session_id": session["id"],
                    },
                )
                .mappings()
                .one()
            )
        assert before_retry == {"session_state": "OPEN", "versions": 0, "assets": 0}

        recovered = client.post(
            finalize_path,
            headers=finalize_headers,
            json=finalize_body,
        )
        assert recovered.status_code == 202, recovered.text
        assert recovered.json()["upload_session"]["status"] == "FINALIZED"


def test_upload_promoter_recovers_after_process_crash_following_object_copy(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = CrashAfterFirstCopyStorage(build_object_storage(upload_settings))
    monkeypatch.setattr(api_container, "build_object_storage", lambda _settings: storage)

    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-copy-crash-recovery-0001",
        )
        _direct_upload(session)
        with SqlAlchemyAssetUnitOfWork(
            integration_database.session_factory  # type: ignore[attr-defined]
        ) as uow:
            upload_session = uow.upload_sessions.get(
                workspace_id="upload-workspace",
                upload_session_id=str(session["id"]),
            )
        assert upload_session is not None
        verifier = ImageUploadIntegrityVerifier(
            storage=storage,
            transaction_active=is_unit_of_work_active,
            maximum_bytes=upload_settings.upload_max_bytes,
            maximum_dimension=upload_settings.upload_max_image_dimension,
            maximum_pixels=upload_settings.upload_max_image_pixels,
            maximum_frames=upload_settings.upload_max_image_frames,
            maximum_metadata_bytes=upload_settings.upload_max_metadata_bytes,
        )
        promoter = UploadPromoter(storage=storage, verifier=verifier)

        with pytest.raises(SimulatedFinalizeCrash):
            promoter.verify_and_promote(upload_session)
        recovery_storage = build_object_storage(upload_settings)
        try:
            recovery_verifier = ImageUploadIntegrityVerifier(
                storage=recovery_storage,
                transaction_active=is_unit_of_work_active,
                maximum_bytes=upload_settings.upload_max_bytes,
                maximum_dimension=upload_settings.upload_max_image_dimension,
                maximum_pixels=upload_settings.upload_max_image_pixels,
                maximum_frames=upload_settings.upload_max_image_frames,
                maximum_metadata_bytes=upload_settings.upload_max_metadata_bytes,
            )
            recovered = UploadPromoter(
                storage=recovery_storage,
                verifier=recovery_verifier,
            ).verify_and_promote(upload_session)
        finally:
            close_object_storage(recovery_storage)

    assert recovered.stat.reference.location == upload_session.destination_location
    assert recovered.stat.reference.key == upload_session.destination_key
    assert minio_client.head_object(  # type: ignore[attr-defined]
        Bucket=upload_session.destination_bucket,
        Key=upload_session.destination_key,
    )["ContentLength"] == len(VALID_PNG)
    with pytest.raises(ClientError) as missing_source:
        minio_client.head_object(  # type: ignore[attr-defined]
            Bucket=upload_session.storage_bucket,
            Key=upload_session.storage_key,
        )
    assert missing_source.value.response["ResponseMetadata"]["HTTPStatusCode"] == 404


def test_task_finalize_cannot_commit_after_retention_expires_during_verification(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = BoundaryFaultStorage(build_object_storage(upload_settings))
    monkeypatch.setattr(api_container, "build_object_storage", lambda _settings: storage)

    with TestClient(create_app(upload_settings)) as client:
        workflow = client.post(
            "/api/v1/workflows",
            headers=_headers("create-expiring-finalize-workflow-0001"),
            json={
                "workflow_type": "FIXTURE_IMAGE_GENERATION",
                "input_data": {"fixture_config": {"count": 1}},
                "retention_hours": 72,
            },
        )
        assert workflow.status_code == 202, workflow.text
        session = _create_session(
            client,
            idempotency_key="create-expiring-finalize-upload-0001",
            retention_class="TASK",
            workflow_id=workflow.json()["id"],
        )
        source = _object_location(session)
        _direct_upload(session)
        finalize_path = f"/api/v1/upload-sessions/{session['id']}:finalize"
        finalize_headers = _headers("finalize-expiring-finalize-upload-0001")
        finalize_body = {"expected_version": session["version"]}

        def expire_workflow_during_verification() -> None:
            expired_at = (datetime.now(UTC) - timedelta(seconds=1)).replace(tzinfo=None)
            with integration_database.engine.begin() as connection:  # type: ignore[attr-defined]
                connection.execute(
                    text("UPDATE workflows SET expires_at = :expired_at WHERE id = :workflow_id"),
                    {
                        "expired_at": expired_at,
                        "workflow_id": workflow.json()["id"],
                    },
                )

        storage.arm(first_io_observer=expire_workflow_during_verification)
        expired = client.post(
            finalize_path,
            headers=finalize_headers,
            json=finalize_body,
        )
        assert expired.status_code == 410, expired.text

        with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
            facts = (
                connection.execute(
                    text(
                        "SELECT "
                        "(SELECT state FROM upload_sessions "
                        "WHERE id = :upload_session_id) AS session_state, "
                        "(SELECT cleanup_operation_id FROM upload_sessions "
                        "WHERE id = :upload_session_id) AS cleanup_operation_id, "
                        "(SELECT COUNT(*) FROM assets WHERE id = :asset_id) AS assets, "
                        "(SELECT COUNT(*) FROM asset_versions "
                        "WHERE upload_session_id = :upload_session_id) AS versions, "
                        "(SELECT COUNT(*) FROM durable_operations "
                        "WHERE target_id IN (SELECT id FROM asset_versions "
                        "WHERE upload_session_id = :upload_session_id)) AS operations, "
                        "(SELECT COUNT(*) FROM durable_operations "
                        "WHERE target_type = 'UPLOAD_SESSION' "
                        "AND target_id = :upload_session_id "
                        "AND kind = 'ASSET_DELETION') AS cleanup_operations, "
                        "(SELECT COUNT(*) FROM outbox_events AS oe "
                        "JOIN durable_operations AS op ON op.id = oe.aggregate_id "
                        "WHERE op.target_type = 'UPLOAD_SESSION' "
                        "AND op.target_id = :upload_session_id "
                        "AND oe.event_type = 'asset.delete.requested') AS cleanup_events"
                    ),
                    {
                        "upload_session_id": session["id"],
                        "asset_id": session["reserved_asset_id"],
                    },
                )
                .mappings()
                .one()
            )
        assert facts == {
            "session_state": "EXPIRED",
            "cleanup_operation_id": facts["cleanup_operation_id"],
            "assets": 0,
            "versions": 0,
            "operations": 0,
            "cleanup_operations": 1,
            "cleanup_events": 1,
        }
        assert facts["cleanup_operation_id"] is not None

        assert minio_client.head_object(  # type: ignore[attr-defined]
            Bucket=source[0],
            Key=source[1],
        )["ContentLength"] == len(VALID_PNG)


def test_cleanup_worker_recovers_after_expiry_transaction_without_sync_deletion(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-durable-expiry-cleanup-0001",
        )
        source = _object_location(session)
        _direct_upload(session)

        expired_at = (
            datetime.now(UTC)
            - timedelta(seconds=upload_settings.upload_cleanup_presign_grace_seconds + 1)
        ).replace(tzinfo=None)
        with integration_database.engine.begin() as connection:  # type: ignore[attr-defined]
            connection.execute(
                text(
                    "UPDATE upload_sessions "
                    "SET expires_at = :expired_at "
                    "WHERE id = :upload_session_id"
                ),
                {
                    "expired_at": expired_at,
                    "upload_session_id": session["id"],
                },
            )

        expired = client.get(
            f"/api/v1/upload-sessions/{session['id']}",
            headers=_read_headers(),
        )
        assert expired.status_code == 200
        assert expired.json()["status"] == "EXPIRED"

    object_locations = (source,)
    present = minio_client.head_object(  # type: ignore[attr-defined]
        Bucket=source[0],
        Key=source[1],
    )
    assert present["ContentLength"] == len(VALID_PNG)

    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        cleanup_operation_id = connection.execute(
            text("SELECT cleanup_operation_id FROM upload_sessions WHERE id = :upload_session_id"),
            {"upload_session_id": session["id"]},
        ).scalar_one()
    with SqlAlchemyUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        events = uow.outbox.list_for_aggregate(cleanup_operation_id)
    cleanup_events = [
        event for event in events if event.envelope.event_type == "asset.delete.requested"
    ]
    assert len(cleanup_events) == 1

    worker = WorkerRuntime.build(upload_settings)
    try:
        event_id = cleanup_events[0].envelope.event_id
        assert worker.process_event(event_id) == "processed"
        assert worker.process_event(event_id) == "duplicate"
    finally:
        worker.close()

    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        cleanup_state = connection.execute(
            text(
                "SELECT state FROM durable_operations "
                "WHERE id = (SELECT cleanup_operation_id FROM upload_sessions "
                "WHERE id = :upload_session_id)"
            ),
            {"upload_session_id": session["id"]},
        ).scalar_one()
    assert cleanup_state == "RECONCILING"
    for bucket, key in object_locations:
        with pytest.raises(ClientError) as missing:
            minio_client.head_object(Bucket=bucket, Key=key)  # type: ignore[attr-defined]
        assert missing.value.response["ResponseMetadata"]["HTTPStatusCode"] == 404


def test_cleanup_storage_outage_uses_durable_retry_and_recovery_event(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-cleanup-outage-upload-0001",
        )
        source = _object_location(session)
        _direct_upload(session)
        with integration_database.engine.begin() as connection:  # type: ignore[attr-defined]
            connection.execute(
                text(
                    "UPDATE upload_sessions SET expires_at = :expires_at "
                    "WHERE id = :upload_session_id"
                ),
                {
                    "expires_at": (
                        datetime.now(UTC)
                        - timedelta(
                            seconds=(upload_settings.upload_cleanup_presign_grace_seconds + 1)
                        )
                    ).replace(tzinfo=None),
                    "upload_session_id": session["id"],
                },
            )
        expired = client.get(
            f"/api/v1/upload-sessions/{session['id']}",
            headers=_read_headers(),
        )
        assert expired.status_code == 200
        cleanup_operation_id = expired.json()["cleanup_operation_id"]

    with SqlAlchemyUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        cleanup_event = next(
            event
            for event in uow.outbox.list_for_aggregate(cleanup_operation_id)
            if event.envelope.event_type == "asset.delete.requested"
        )
    unavailable_settings = upload_settings.model_copy(
        update={
            "object_store_endpoint": "http://127.0.0.1:19999",
            "object_store_presign_endpoint": "http://127.0.0.1:19999",
            "object_store_connect_timeout_seconds": 0.2,
            "object_store_read_timeout_seconds": 0.2,
        }
    )
    unavailable_worker = WorkerRuntime.build(unavailable_settings)
    try:
        assert unavailable_worker.process_event(cleanup_event.envelope.event_id) == "processed"
    finally:
        unavailable_worker.close()

    with SqlAlchemyOperationUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        failed = uow.operations.get(
            cleanup_operation_id,
            workspace_id="upload-workspace",
        )
    assert failed is not None
    assert failed.state.value == "RETRYABLE_FAILED"
    assert failed.next_attempt_at is not None
    assert failed.error is not None
    assert failed.error.code == "UPLOAD_CLEANUP_STORAGE_UNAVAILABLE"

    scanner = OperationRecoveryService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(
            integration_database.session_factory  # type: ignore[attr-defined]
        ),
        batch_size=10,
    )
    assert scanner.recover_once(now=failed.next_attempt_at) == 1
    with SqlAlchemyUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        recovery_event = next(
            event
            for event in uow.outbox.list_for_aggregate(cleanup_operation_id)
            if event.envelope.event_type == "operation.recovery.requested"
        )

    retry_delay = (failed.next_attempt_at - datetime.now(UTC)).total_seconds()
    if retry_delay > 0:
        time.sleep(retry_delay + 0.05)
    recovered_worker = WorkerRuntime.build(upload_settings)
    try:
        assert recovered_worker.process_event(recovery_event.envelope.event_id) == "processed"
    finally:
        recovered_worker.close()

    with SqlAlchemyOperationUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        recovered = uow.operations.get(
            cleanup_operation_id,
            workspace_id="upload-workspace",
        )
    assert recovered is not None
    assert recovered.state.value == "RECONCILING"
    assert recovered.reconciliation_required is True
    with pytest.raises(ClientError) as missing:
        minio_client.head_object(Bucket=source[0], Key=source[1])  # type: ignore[attr-defined]
    assert missing.value.response["ResponseMetadata"]["HTTPStatusCode"] == 404


def test_finalize_isolated_from_an_untrusted_destination_object(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-isolated-destination-0001",
        )
        _direct_upload(session)
        finalize_path = f"/api/v1/upload-sessions/{session['id']}:finalize"
        finalize_headers = _headers("finalize-isolated-destination-0001")
        finalize_body = {"expected_version": session["version"]}

        with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
            destination = (
                connection.execute(
                    text(
                        "SELECT destination_bucket, destination_key FROM upload_sessions "
                        "WHERE id = :upload_session_id"
                    ),
                    {"upload_session_id": session["id"]},
                )
                .mappings()
                .one()
            )
        minio_client.put_object(  # type: ignore[attr-defined]
            Bucket=destination["destination_bucket"],
            Key=destination["destination_key"],
            Body=b"conflicting object",
            ContentType="image/png",
            Metadata={
                "sha256": hashlib.sha256(b"conflicting object").hexdigest(),
                "upload-session-id": "another-session",
            },
        )

        finalized = client.post(
            finalize_path,
            headers=finalize_headers,
            json=finalize_body,
        )
        assert finalized.status_code == 202, finalized.text
        assert finalized.json()["upload_session"]["cleanup_operation_id"] is None
        with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
            facts = (
                connection.execute(
                    text(
                        "SELECT "
                        "(SELECT state FROM upload_sessions "
                        "WHERE id = :upload_session_id) AS session_state, "
                        "(SELECT location FROM asset_objects "
                        "WHERE asset_version_id = (SELECT id FROM asset_versions "
                        "WHERE upload_session_id = :upload_session_id)) AS object_location"
                    ),
                    {"upload_session_id": session["id"]},
                )
                .mappings()
                .one()
            )
        assert facts == {
            "session_state": "FINALIZED",
            "object_location": "QUARANTINE",
        }
    assert minio_client.head_object(  # type: ignore[attr-defined]
        Bucket=destination["destination_bucket"],
        Key=destination["destination_key"],
    )["ContentLength"] == len(b"conflicting object")


def test_minio_copy_retry_after_lost_response_returns_same_destination(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    del integration_database
    storage = build_object_storage(upload_settings)
    content = VALID_PNG
    sha256 = hashlib.sha256(content).hexdigest()
    source = ObjectReference(
        location=StorageLocationClass.QUARANTINE,
        key=f"integration/copy-source/{new_uuid7()}",
    )
    destination = ObjectReference(
        location=StorageLocationClass.FOUNDATION,
        key=f"integration/copy-destination/{new_uuid7()}",
    )
    minio_client.put_object(  # type: ignore[attr-defined]
        Bucket=upload_settings.object_store_quarantine_bucket,
        Key=source.key,
        Body=content,
        ContentType="image/png",
        Metadata={"sha256": sha256},
    )
    source_stat = storage.stat(source)
    request = ConditionalCopyRequest(
        source=source_stat.reference,
        destination=destination,
        source_etag=source_stat.etag,
        expected_content_length=len(content),
        expected_sha256=sha256,
        content_type="image/png",
        upload_session_id="019f8a00-0000-7000-8000-000000000001",
    )

    first = storage.copy_if_absent(request)
    recovered = storage.copy_if_absent(request)

    assert recovered.etag == first.etag
    assert recovered.content_length == len(content)
    assert recovered.metadata["sha256"] == sha256
    assert recovered.metadata["upload-session-id"] == request.upload_session_id

    temporary = storage.temporary_read(
        TemporaryReadRequest(
            reference=recovered.reference,
            expires_at=datetime.now(UTC) + timedelta(seconds=30),
            expected_etag=recovered.etag,
        )
    )
    downloaded = httpx.get(temporary.url, headers=temporary.required_headers)
    assert downloaded.status_code == 200
    assert downloaded.content == content

    with pytest.raises(StoragePreconditionError):
        storage.delete_if_match(
            ConditionalDeleteRequest(
                reference=recovered.reference,
                expected_etag='"different-etag"',
            )
        )
    assert storage.delete_if_match(
        ConditionalDeleteRequest(
            reference=recovered.reference,
            expected_etag=recovered.etag,
        )
    )
    with pytest.raises(UploadObjectMissingError):
        storage.stat(recovered.reference)


def test_finalize_and_local_validation_share_one_metadata_accounting_policy(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    del integration_database, minio_client
    payload = _jpeg_with_icc_and_exif()
    metadata_limit = 160 * 1024
    settings = upload_settings.model_copy(update={"upload_max_metadata_bytes": metadata_limit})

    local_result = _asset_local_validator(settings).validate(
        AssetLocalValidationRequest(
            asset_kind=AssetKind.IMAGE,
            filename="metadata.jpg",
            declared_mime="image/jpeg",
            byte_size=len(payload),
        ),
        io.BytesIO(payload),
    )
    assert local_result.facts["metadata_bytes"] < metadata_limit

    with TestClient(create_app(settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-canonical-metadata-0001",
            content=payload,
            filename="metadata.jpg",
            declared_mime="image/jpeg",
        )
        _direct_upload(session, payload)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-canonical-metadata-0001"),
            json={"expected_version": session["version"]},
        )

    assert finalized.status_code == 202, finalized.text
    assert finalized.json()["asset_version"]["image_format"] == "JPEG"


def test_finalize_and_local_validation_reject_the_same_metadata_overage(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    del integration_database, minio_client
    payload = _jpeg_with_icc_and_exif()
    settings = upload_settings.model_copy(update={"upload_max_metadata_bytes": 140 * 1024})

    with pytest.raises(AssetLocalValidationError) as local_rejection:
        _asset_local_validator(settings).validate(
            AssetLocalValidationRequest(
                asset_kind=AssetKind.IMAGE,
                filename="metadata.jpg",
                declared_mime="image/jpeg",
                byte_size=len(payload),
            ),
            io.BytesIO(payload),
        )
    assert local_rejection.value.code == "IMAGE_METADATA_EXCEEDED"

    with TestClient(create_app(settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-metadata-overage-0001",
            content=payload,
            filename="metadata.jpg",
            declared_mime="image/jpeg",
        )
        _direct_upload(session, payload)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-metadata-overage-0001"),
            json={"expected_version": session["version"]},
        )

    assert finalized.status_code == 422
    assert finalized.json()["code"] == "OBJECT_MISMATCH"
    assert finalized.json()["message"] == ("uploaded image metadata exceeds the configured limit")


def test_validation_event_crosses_durable_worker_and_duplicate_delivery_seam(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    del minio_client
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(client, idempotency_key="create-worker-event-0001")
        _direct_upload(session)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-worker-event-0001"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
        operation_id = finalized.json()["validation_operation"]["id"]

        with SqlAlchemyUnitOfWork(
            integration_database.session_factory  # type: ignore[attr-defined]
        ) as uow:
            events = uow.outbox.list_for_aggregate(operation_id)
        validation_events = [
            event for event in events if event.envelope.event_type == "asset.validation.requested"
        ]
        assert len(validation_events) == 1

        executor = SuccessfulAssetValidationExecutor()
        worker = WorkerRuntime.build(
            upload_settings,
            operation_executors={OperationKind.ASSET_VALIDATION: executor},
        )
        try:
            event_id = validation_events[0].envelope.event_id
            assert worker.process_event(event_id) == "processed"
            assert worker.process_event(event_id) == "duplicate"
        finally:
            worker.close()

        operation = client.get(
            f"/api/v1/operations/{operation_id}",
            headers=_read_headers(),
        )
        assert operation.status_code == 200, operation.text
        assert operation.json()["state"] == "SUCCEEDED"
        assert len(executor.requests) == 1
        assert executor.requests[0].input_ref == (
            f"mysql://asset-versions/{finalized.json()['asset_version']['id']}"
        )


def test_finalize_events_cross_real_validation_worker_without_dead_letters(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-validation-pipeline-0001",
        )
        source_bucket, source_key = _object_location(session)
        _direct_upload(session)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-validation-pipeline-0001"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
        body = finalized.json()

        with SqlAlchemyUnitOfWork(
            integration_database.session_factory  # type: ignore[attr-defined]
        ) as uow:
            asset_events = uow.outbox.list_for_aggregate(body["asset"]["id"])
            operation_events = uow.outbox.list_for_aggregate(body["validation_operation"]["id"])
        upload_events = [
            event for event in asset_events if event.envelope.event_type == "asset.upload.finalized"
        ]
        validation_events = [
            event
            for event in operation_events
            if event.envelope.event_type == "asset.validation.requested"
        ]
        assert len(upload_events) == 1
        assert len(validation_events) == 1
        event_ids = [
            upload_events[0].envelope.event_id,
            validation_events[0].envelope.event_id,
        ]

        worker = WorkerRuntime.build(upload_settings)
        try:
            assert [worker.process_event(event_id) for event_id in event_ids] == [
                "processed",
                "processed",
            ]
            assert [worker.process_event(event_id) for event_id in event_ids] == [
                "duplicate",
                "duplicate",
            ]
        finally:
            worker.close()

        asset = client.get(
            f"/api/v1/assets/{body['asset']['id']}",
            headers=_read_headers(),
        )
        assert asset.status_code == 200, asset.text
        assert asset.json()["status"] == "PENDING_RIGHTS"
        validation = client.get(
            f"/api/v1/assets/{body['asset']['id']}/validation",
            headers=_read_headers(),
        )
        assert validation.status_code == 200, validation.text
        validation_body = validation.json()
        assert validation_body["asset_id"] == body["asset"]["id"]
        assert validation_body["asset_version_id"] == body["asset_version"]["id"]
        assert validation_body["asset_status"] == "PENDING_RIGHTS"
        assert validation_body["validation_policy_version"] == "asset-validation-v1"
        assert validation_body["operation"] == {
            "id": body["validation_operation"]["id"],
            "state": "SUCCEEDED",
            "attempt_count": 1,
            "max_attempts": 5,
            "next_attempt_at": None,
            "retryable": False,
            "failure_code": None,
            "failure_category": None,
            "completed_at": validation_body["operation"]["completed_at"],
        }
        assert [stage["stage"] for stage in validation_body["stages"]] == [
            "LOCAL_FORMAT",
            "MALWARE",
            "CONTENT_SAFETY",
            "PROVENANCE",
            "PROMOTION",
        ]
        assert all(stage["verdict"] == "PASS" for stage in validation_body["stages"])
        serialized_projection = json.dumps(validation_body, sort_keys=True).lower()
        assert "object_provider_version_id" not in serialized_projection
        assert "object_etag" not in serialized_projection
        assert "content_sha256" not in serialized_projection
        assert "raw" not in serialized_projection

    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        stages = connection.execute(
            text(
                "SELECT stage, verdict, evidence_json "
                "FROM asset_validation_results "
                "WHERE asset_version_id = :asset_version_id "
                "ORDER BY created_at, id"
            ),
            {"asset_version_id": body["asset_version"]["id"]},
        ).mappings()
        stage_rows = list(stages)
        objects = list(
            connection.execute(
                text(
                    "SELECT role, state, bucket, `key` FROM asset_objects "
                    "WHERE asset_version_id = :asset_version_id ORDER BY role"
                ),
                {"asset_version_id": body["asset_version"]["id"]},
            ).mappings()
        )
        inbox_rows = list(
            connection.execute(
                text(
                    "SELECT message_id, status FROM inbox_messages "
                    "WHERE consumer = :consumer "
                    "AND message_id IN (:upload_event_id, :validation_event_id)"
                ),
                {
                    "consumer": upload_settings.worker_consumer_name,
                    "upload_event_id": event_ids[0],
                    "validation_event_id": event_ids[1],
                },
            ).mappings()
        )
        dead_letter_count = connection.execute(
            text(
                "SELECT COUNT(*) FROM dead_letter_messages "
                "WHERE message_id IN (:upload_event_id, :validation_event_id)"
            ),
            {
                "upload_event_id": event_ids[0],
                "validation_event_id": event_ids[1],
            },
        ).scalar_one()
    assert [(row["stage"], row["verdict"]) for row in stage_rows] == [
        ("LOCAL_FORMAT", "PASS"),
        ("MALWARE", "PASS"),
        ("CONTENT_SAFETY", "PASS"),
        ("PROVENANCE", "PASS"),
        ("PROMOTION", "PASS"),
    ]
    assert all("raw" not in str(row["evidence_json"]).lower() for row in stage_rows)
    assert [(row["role"], row["state"]) for row in objects] == [
        ("CONTROLLED_ORIGINAL", "CONTROLLED"),
        ("ORIGINAL", "DELETED"),
    ]
    assert {row["message_id"]: row["status"] for row in inbox_rows} == {
        event_ids[0]: "PROCESSED",
        event_ids[1]: "PROCESSED",
    }
    assert dead_letter_count == 0
    controlled = objects[0]
    assert minio_client.head_object(  # type: ignore[attr-defined]
        Bucket=controlled["bucket"],
        Key=controlled["key"],
    )["ContentLength"] == len(VALID_PNG)
    with pytest.raises(ClientError) as missing_source:
        minio_client.head_object(Bucket=source_bucket, Key=source_key)  # type: ignore[attr-defined]
    assert missing_source.value.response["ResponseMetadata"]["HTTPStatusCode"] == 404


def test_task_validation_expiry_before_content_provider_cleans_without_dispatch(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    with TestClient(create_app(upload_settings)) as client:
        workflow = client.post(
            "/api/v1/workflows",
            headers=_headers("create-validation-expiry-workflow-0001"),
            json={
                "workflow_type": "FIXTURE_IMAGE_GENERATION",
                "input_data": {"fixture_config": {"count": 1}},
                "retention_hours": 72,
            },
        )
        assert workflow.status_code == 202, workflow.text
        session = _create_session(
            client,
            idempotency_key="create-validation-expiry-upload-0001",
            retention_class="TASK",
            workflow_id=workflow.json()["id"],
        )
        source_bucket, source_key = _object_location(session)
        _direct_upload(session)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-validation-expiry-upload-0001"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
    body = finalized.json()
    deadline = datetime.fromisoformat(body["asset"]["retention_deadline"])
    clock = MutableValidationClock(deadline - timedelta(microseconds=1))
    content_safety = RecordingContentSafetyAdapter()
    scanner = ExpireAfterMalwareScan(
        DeterministicMalwareScanner(),
        clock=clock,
        expires_at=deadline,
    )
    operation_id = body["validation_operation"]["id"]
    with SqlAlchemyUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        validation_event = next(
            event
            for event in uow.outbox.list_for_aggregate(operation_id)
            if event.envelope.event_type == "asset.validation.requested"
        )

    storage = build_object_storage(upload_settings)
    worker = WorkerRuntime.build(
        upload_settings,
        operation_executors={
            OperationKind.ASSET_VALIDATION: _validation_executor(
                integration_database=integration_database,
                upload_settings=upload_settings,
                storage=storage,
                malware_scanner=scanner,
                content_safety=content_safety,
                clock=clock,
            )
        },
    )
    try:
        assert worker.process_event(validation_event.envelope.event_id) == "processed"
    finally:
        worker.close()
        close_object_storage(storage)

    assert content_safety.calls == 0
    with SqlAlchemyOperationUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        operation = uow.operations.get(operation_id, workspace_id="upload-workspace")
    assert operation is not None
    assert operation.state.value == "FAILED"
    assert operation.error is not None
    assert operation.error.code == "ASSET_RETENTION_EXPIRED"
    assert operation.error.retryable is False
    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        facts = (
            connection.execute(
                text(
                    "SELECT "
                    "(SELECT status FROM assets WHERE id = :asset_id) AS asset_status, "
                    "(SELECT state FROM asset_objects "
                    "WHERE asset_version_id = :asset_version_id "
                    "AND role = 'ORIGINAL') AS source_state, "
                    "(SELECT COUNT(*) FROM asset_objects "
                    "WHERE asset_version_id = :asset_version_id "
                    "AND role = 'CONTROLLED_ORIGINAL') AS controlled_count, "
                    "(SELECT COUNT(*) FROM asset_validation_results "
                    "WHERE asset_version_id = :asset_version_id "
                    "AND stage = 'CONTENT_SAFETY') AS content_results"
                ),
                {
                    "asset_id": body["asset"]["id"],
                    "asset_version_id": body["asset_version"]["id"],
                },
            )
            .mappings()
            .one()
        )
    assert facts == {
        "asset_status": "DELETED",
        "source_state": "DELETED",
        "controlled_count": 0,
        "content_results": 0,
    }
    with pytest.raises(ClientError) as missing_source:
        minio_client.head_object(Bucket=source_bucket, Key=source_key)  # type: ignore[attr-defined]
    assert missing_source.value.response["ResponseMetadata"]["HTTPStatusCode"] == 404


def test_task_validation_retention_cleanup_recovers_after_storage_outage(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    with TestClient(create_app(upload_settings)) as client:
        workflow = client.post(
            "/api/v1/workflows",
            headers=_headers("create-retention-outage-workflow-0001"),
            json={
                "workflow_type": "FIXTURE_IMAGE_GENERATION",
                "input_data": {"fixture_config": {"count": 1}},
                "retention_hours": 72,
            },
        )
        assert workflow.status_code == 202, workflow.text
        session = _create_session(
            client,
            idempotency_key="create-retention-outage-upload-0001",
            retention_class="TASK",
            workflow_id=workflow.json()["id"],
        )
        source_bucket, source_key = _object_location(session)
        _direct_upload(session)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-retention-outage-upload-0001"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
    body = finalized.json()
    deadline = datetime.fromisoformat(body["asset"]["retention_deadline"])
    clock = MutableValidationClock(deadline - timedelta(microseconds=1))
    content_safety = RecordingContentSafetyAdapter()
    scanner = ExpireAfterMalwareScan(
        DeterministicMalwareScanner(),
        clock=clock,
        expires_at=deadline,
    )
    operation_id = body["validation_operation"]["id"]
    with SqlAlchemyUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        validation_event = next(
            event
            for event in uow.outbox.list_for_aggregate(operation_id)
            if event.envelope.event_type == "asset.validation.requested"
        )

    storage = FailFirstRetentionDeleteStorage(build_object_storage(upload_settings))
    executor = _validation_executor(
        integration_database=integration_database,
        upload_settings=upload_settings,
        storage=storage,
        malware_scanner=scanner,
        content_safety=content_safety,
        clock=clock,
    )
    first_worker = WorkerRuntime.build(
        upload_settings,
        operation_executors={OperationKind.ASSET_VALIDATION: executor},
    )
    try:
        assert first_worker.process_event(validation_event.envelope.event_id) == "processed"
    finally:
        first_worker.close()

    assert storage.failed
    assert content_safety.calls == 0
    with SqlAlchemyOperationUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        retryable = uow.operations.get(operation_id, workspace_id="upload-workspace")
    assert retryable is not None
    assert retryable.state.value == "RETRYABLE_FAILED"
    assert retryable.attempt_count == 1
    assert retryable.next_attempt_at is not None
    assert retryable.error is not None
    assert retryable.error.code == "RETENTION_CLEANUP_STORAGE_UNAVAILABLE"
    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        pending = (
            connection.execute(
                text(
                    "SELECT "
                    "(SELECT status FROM assets WHERE id = :asset_id) AS asset_status, "
                    "(SELECT state FROM asset_objects "
                    "WHERE asset_version_id = :asset_version_id "
                    "AND role = 'ORIGINAL') AS source_state"
                ),
                {
                    "asset_id": body["asset"]["id"],
                    "asset_version_id": body["asset_version"]["id"],
                },
            )
            .mappings()
            .one()
        )
    assert pending == {
        "asset_status": "DELETING",
        "source_state": "DELETE_PENDING",
    }
    assert minio_client.head_object(  # type: ignore[attr-defined]
        Bucket=source_bucket,
        Key=source_key,
    )["ContentLength"] == len(VALID_PNG)

    recovery = OperationRecoveryService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(
            integration_database.session_factory  # type: ignore[attr-defined]
        ),
        batch_size=10,
    )
    assert recovery.recover_once(now=retryable.next_attempt_at) == 1
    with SqlAlchemyUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        recovery_event = next(
            event
            for event in uow.outbox.list_for_aggregate(operation_id)
            if event.envelope.event_type == "operation.recovery.requested"
        )
    retry_delay = (retryable.next_attempt_at - datetime.now(UTC)).total_seconds()
    if retry_delay > 0:
        time.sleep(retry_delay + 0.05)
    recovered_worker = WorkerRuntime.build(
        upload_settings,
        operation_executors={OperationKind.ASSET_VALIDATION: executor},
    )
    try:
        assert recovered_worker.process_event(recovery_event.envelope.event_id) == "processed"
    finally:
        recovered_worker.close()
        close_object_storage(storage)

    with SqlAlchemyOperationUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        completed = uow.operations.get(operation_id, workspace_id="upload-workspace")
    assert completed is not None
    assert completed.state.value == "FAILED"
    assert completed.attempt_count == 2
    assert completed.error is not None
    assert completed.error.code == "ASSET_RETENTION_EXPIRED"
    assert completed.error.retryable is False
    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        final = (
            connection.execute(
                text(
                    "SELECT "
                    "(SELECT status FROM assets WHERE id = :asset_id) AS asset_status, "
                    "(SELECT state FROM asset_objects "
                    "WHERE asset_version_id = :asset_version_id "
                    "AND role = 'ORIGINAL') AS source_state, "
                    "(SELECT COUNT(*) FROM asset_objects "
                    "WHERE asset_version_id = :asset_version_id "
                    "AND role = 'CONTROLLED_ORIGINAL') AS controlled_count, "
                    "(SELECT COUNT(*) FROM asset_validation_results "
                    "WHERE asset_version_id = :asset_version_id "
                    "AND stage = 'CONTENT_SAFETY') AS content_results"
                ),
                {
                    "asset_id": body["asset"]["id"],
                    "asset_version_id": body["asset_version"]["id"],
                },
            )
            .mappings()
            .one()
        )
    assert final == {
        "asset_status": "DELETED",
        "source_state": "DELETED",
        "controlled_count": 0,
        "content_results": 0,
    }
    assert content_safety.calls == 0
    with pytest.raises(ClientError) as missing_source:
        minio_client.head_object(Bucket=source_bucket, Key=source_key)  # type: ignore[attr-defined]
    assert missing_source.value.response["ResponseMetadata"]["HTTPStatusCode"] == 404


@pytest.mark.parametrize(
    ("remaining_seconds", "expected_factory_calls", "expected_state", "expected_code"),
    [
        (30, 1, "SUCCEEDED", None),
        (
            19,
            0,
            "FAILED",
            "CONTENT_SAFETY_RETENTION_WINDOW_INSUFFICIENT",
        ),
    ],
)
def test_task_content_reference_never_exceeds_retention_or_short_window(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
    remaining_seconds: int,
    expected_factory_calls: int,
    expected_state: str,
    expected_code: str | None,
) -> None:
    del minio_client
    suffix = str(remaining_seconds)
    with TestClient(create_app(upload_settings)) as client:
        workflow = client.post(
            "/api/v1/workflows",
            headers=_headers(f"create-content-window-workflow-{suffix}"),
            json={
                "workflow_type": "FIXTURE_IMAGE_GENERATION",
                "input_data": {"fixture_config": {"count": 1}},
                "retention_hours": 72,
            },
        )
        assert workflow.status_code == 202, workflow.text
        session = _create_session(
            client,
            idempotency_key=f"create-content-window-upload-{suffix}",
            retention_class="TASK",
            workflow_id=workflow.json()["id"],
        )
        _direct_upload(session)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers(f"finalize-content-window-upload-{suffix}"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
    body = finalized.json()
    deadline = datetime.fromisoformat(body["asset"]["retention_deadline"])
    clock = MutableValidationClock(deadline - timedelta(seconds=remaining_seconds))
    request_factory = RecordingExternalContentSafetyRequestFactory()
    content_safety = RecordingContentSafetyAdapter(endpoint="green-cip.cn-shanghai.aliyuncs.com")
    operation_id = body["validation_operation"]["id"]
    with SqlAlchemyUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        validation_event = next(
            event
            for event in uow.outbox.list_for_aggregate(operation_id)
            if event.envelope.event_type == "asset.validation.requested"
        )

    storage = build_object_storage(upload_settings)
    worker = WorkerRuntime.build(
        upload_settings,
        operation_executors={
            OperationKind.ASSET_VALIDATION: _validation_executor(
                integration_database=integration_database,
                upload_settings=upload_settings,
                storage=storage,
                content_safety=content_safety,
                content_safety_request_factory=request_factory,
                clock=clock,
                policy=AssetValidationExecutorPolicy(
                    content_reference_lifetime=timedelta(seconds=60),
                    content_reference_minimum_validity=timedelta(seconds=20),
                ),
            )
        },
    )
    try:
        assert worker.process_event(validation_event.envelope.event_id) == "processed"
    finally:
        worker.close()
        close_object_storage(storage)

    assert request_factory.calls == expected_factory_calls
    assert content_safety.calls == expected_factory_calls
    if request_factory.expires_at:
        assert request_factory.expires_at == [deadline]
    with SqlAlchemyOperationUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        operation = uow.operations.get(operation_id, workspace_id="upload-workspace")
    assert operation is not None
    assert operation.state.value == expected_state
    if expected_code is None:
        assert operation.error is None
    else:
        assert operation.error is not None
        assert operation.error.code == expected_code
        assert operation.error.retryable is False
    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        content_results = connection.execute(
            text(
                "SELECT COUNT(*) FROM asset_validation_results "
                "WHERE asset_version_id = :asset_version_id "
                "AND stage = 'CONTENT_SAFETY'"
            ),
            {"asset_version_id": body["asset_version"]["id"]},
        ).scalar_one()
    assert content_results == expected_factory_calls


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("workspace-denied", "VALIDATION_TRANSFER_WORKSPACE_DENIED"),
        ("policy-revoked", "VALIDATION_TRANSFER_POLICY_MISMATCH"),
        ("endpoint-denied", "VALIDATION_TRANSFER_ENDPOINT_DENIED"),
    ],
)
def test_external_validation_transfer_denial_prevents_url_and_provider_dispatch(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
    case: str,
    expected_code: str,
) -> None:
    api_settings = upload_settings
    if case == "workspace-denied":
        api_settings = upload_settings.model_copy(
            update={"validation_data_transfer_allowed_workspace_ids": ["different-workspace"]}
        )
    with TestClient(create_app(api_settings)) as client:
        session = _create_session(
            client,
            idempotency_key=f"create-transfer-denial-{case}",
        )
        source_bucket, source_key = _object_location(session)
        _direct_upload(session)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers(f"finalize-transfer-denial-{case}"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
    body = finalized.json()
    operation_id = body["validation_operation"]["id"]
    worker_policy = ValidationDataTransferPolicy.from_settings(api_settings)
    if case == "policy-revoked":
        worker_policy = ValidationDataTransferPolicy(
            enabled=True,
            version="integration-security-validation-revoked-v2",
            allowed_workspace_ids=frozenset({"upload-workspace"}),
            allowed_asset_kinds=frozenset({AssetKind.IMAGE}),
            allowed_retention_classes=frozenset({RetentionClass.TASK, RetentionClass.FOUNDATION}),
            allowed_providers=frozenset({"alibaba-green"}),
            allowed_endpoint_regions=frozenset({"cn-shanghai"}),
            allowed_endpoint_hosts=frozenset({"green-cip.cn-shanghai.aliyuncs.com"}),
        )
    request_factory = RecordingExternalContentSafetyRequestFactory()
    content_safety = RecordingContentSafetyAdapter(
        endpoint=(
            "collector.example"
            if case == "endpoint-denied"
            else "green-cip.cn-shanghai.aliyuncs.com"
        )
    )
    with SqlAlchemyUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        validation_event = next(
            event
            for event in uow.outbox.list_for_aggregate(operation_id)
            if event.envelope.event_type == "asset.validation.requested"
        )

    storage = build_object_storage(upload_settings)
    worker = WorkerRuntime.build(
        upload_settings,
        operation_executors={
            OperationKind.ASSET_VALIDATION: _validation_executor(
                integration_database=integration_database,
                upload_settings=upload_settings,
                storage=storage,
                content_safety=content_safety,
                content_safety_request_factory=request_factory,
                validation_transfer_policy=worker_policy,
            )
        },
    )
    try:
        assert worker.process_event(validation_event.envelope.event_id) == "processed"
        assert worker.process_event(validation_event.envelope.event_id) == "duplicate"
        with SqlAlchemyUnitOfWork(
            integration_database.session_factory  # type: ignore[attr-defined]
        ) as uow:
            terminal_events = [
                event
                for event in uow.outbox.list_for_aggregate(body["asset"]["id"])
                if event.envelope.event_type
                in {
                    "asset.validation.completed",
                    "asset.validation.failed",
                }
            ]
        terminal_deliveries = [
            (
                event.envelope.event_type,
                worker.process_event(event.envelope.event_id),
                worker.process_event(event.envelope.event_id),
            )
            for event in terminal_events
        ]
    finally:
        worker.close()
        close_object_storage(storage)

    assert request_factory.calls == 0
    assert content_safety.calls == 0
    assert minio_client.head_object(  # type: ignore[attr-defined]
        Bucket=source_bucket,
        Key=source_key,
    )["ContentLength"] == len(VALID_PNG)
    with SqlAlchemyOperationUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        operation = uow.operations.get(operation_id, workspace_id="upload-workspace")
    assert operation is not None
    assert operation.state.value == "FAILED"
    assert operation.attempt_count == 1
    assert operation.error is not None
    assert operation.error.code == expected_code
    assert operation.error.retryable is False
    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        facts = (
            connection.execute(
                text(
                    "SELECT "
                    "(SELECT status FROM assets WHERE id = :asset_id) AS asset_status, "
                    "(SELECT state FROM asset_objects "
                    "WHERE asset_version_id = :asset_version_id "
                    "AND role = 'ORIGINAL') AS source_state, "
                    "(SELECT COUNT(*) FROM asset_objects "
                    "WHERE asset_version_id = :asset_version_id "
                    "AND role = 'CONTROLLED_ORIGINAL') AS controlled_count, "
                    "(SELECT COUNT(*) FROM dead_letter_messages "
                    "WHERE workspace_id = 'upload-workspace' "
                    "AND message_id = :event_id) AS validation_event_dead_letters, "
                    "(SELECT reason FROM dead_letter_messages "
                    "WHERE id = :dead_letter_id) AS operation_dead_letter_reason"
                ),
                {
                    "asset_id": body["asset"]["id"],
                    "asset_version_id": body["asset_version"]["id"],
                    "dead_letter_id": operation.dead_letter_id,
                    "event_id": validation_event.envelope.event_id,
                },
            )
            .mappings()
            .one()
        )
        content_result = (
            connection.execute(
                text(
                    "SELECT verdict, reason_code, evidence_json "
                    "FROM asset_validation_results "
                    "WHERE asset_version_id = :asset_version_id "
                    "AND stage = 'CONTENT_SAFETY'"
                ),
                {"asset_version_id": body["asset_version"]["id"]},
            )
            .mappings()
            .one()
        )
    assert facts == {
        "asset_status": "FAILED",
        "source_state": "QUARANTINED",
        "controlled_count": 0,
        "validation_event_dead_letters": 0,
        "operation_dead_letter_reason": "operation_terminal_failure",
    }
    assert operation.dead_letter_id is not None
    assert terminal_deliveries == [("asset.validation.failed", "processed", "duplicate")]
    assert content_result["verdict"] == "TERMINAL_FAILURE"
    assert content_result["reason_code"] == expected_code
    evidence = content_result["evidence_json"]
    if isinstance(evidence, str):
        evidence = json.loads(evidence)
    assert evidence["transfer_authorized"] is False
    assert evidence["transfer_external"] is True
    assert evidence["transfer_provider"] == "alibaba-green"
    assert evidence["transfer_endpoint_region"] == "cn-shanghai"
    assert evidence["transfer_endpoint_host"] == (
        "collector.example" if case == "endpoint-denied" else "green-cip.cn-shanghai.aliyuncs.com"
    )
    assert evidence["transfer_purpose"] == "SECURITY_VALIDATION"
    assert "raw" not in json.dumps(evidence, sort_keys=True).lower()


def test_asset_uow_database_clock_refuses_commit_after_retention_deadline(
    integration_database: object,
    upload_settings: Settings,
) -> None:
    with TestClient(create_app(upload_settings)) as client:
        workflow = client.post(
            "/api/v1/workflows",
            headers=_headers("create-database-retention-fence-workflow-0001"),
            json={
                "workflow_type": "FIXTURE_IMAGE_GENERATION",
                "input_data": {"fixture_config": {"count": 1}},
                "retention_hours": 72,
            },
        )
        assert workflow.status_code == 202, workflow.text
        session = _create_session(
            client,
            idempotency_key="create-database-retention-fence-upload-0001",
            retention_class="TASK",
            workflow_id=workflow.json()["id"],
        )
        _direct_upload(session)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-database-retention-fence-upload-0001"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
    asset_id = finalized.json()["asset"]["id"]
    with integration_database.engine.begin() as connection:  # type: ignore[attr-defined]
        connection.execute(
            text(
                "UPDATE assets "
                "SET retention_deadline = UTC_TIMESTAMP(6) - INTERVAL 1 SECOND "
                "WHERE id = :asset_id"
            ),
            {"asset_id": asset_id},
        )

    with SqlAlchemyAssetUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        asset = uow.assets.get(
            workspace_id="upload-workspace",
            asset_id=asset_id,
            for_update=True,
        )
        assert asset is not None
        assert asset.retention_deadline is not None
        app_now = asset.retention_deadline - timedelta(microseconds=1)
        asset.begin_validation(now=app_now)
        uow.assets.save_asset(asset)
        with pytest.raises(AssetRetentionCommitExpiredError):
            uow.commit_before_retention_deadline(
                workspace_id=asset.workspace_id,
                asset_id=asset.id,
                retention_deadline=asset.retention_deadline,
                clock=lambda: app_now,
            )

    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        status = connection.scalar(
            text("SELECT status FROM assets WHERE id = :asset_id"),
            {"asset_id": asset_id},
        )
    assert status == "QUARANTINED"


def test_task_validation_expiry_at_promotion_commit_compensates_exact_copy(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    with TestClient(create_app(upload_settings)) as client:
        workflow = client.post(
            "/api/v1/workflows",
            headers=_headers("create-promotion-commit-expiry-workflow-0001"),
            json={
                "workflow_type": "FIXTURE_IMAGE_GENERATION",
                "input_data": {"fixture_config": {"count": 1}},
                "retention_hours": 72,
            },
        )
        assert workflow.status_code == 202, workflow.text
        session = _create_session(
            client,
            idempotency_key="create-promotion-commit-expiry-upload-0001",
            retention_class="TASK",
            workflow_id=workflow.json()["id"],
        )
        source_bucket, source_key = _object_location(session)
        _direct_upload(session)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-promotion-commit-expiry-upload-0001"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
    body = finalized.json()
    deadline = datetime.fromisoformat(body["asset"]["retention_deadline"])
    clock = MutableValidationClock(deadline - timedelta(seconds=30))
    operation_id = body["validation_operation"]["id"]
    with SqlAlchemyAssetUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        upload_session = uow.upload_sessions.get(
            workspace_id="upload-workspace",
            upload_session_id=str(session["id"]),
        )
        validation_event = next(
            event
            for event in uow.outbox.list_for_aggregate(operation_id)
            if event.envelope.event_type == "asset.validation.requested"
        )
    assert upload_session is not None

    storage = build_object_storage(upload_settings)
    worker = WorkerRuntime.build(
        upload_settings,
        operation_executors={
            OperationKind.ASSET_VALIDATION: _validation_executor(
                integration_database=integration_database,
                upload_settings=upload_settings,
                storage=storage,
                uow_factory=lambda: ExpireAtPromotionCommitUnitOfWork(
                    integration_database.session_factory,  # type: ignore[attr-defined]
                    clock=clock,
                    expires_at=deadline,
                ),
                clock=clock,
            )
        },
    )
    try:
        assert worker.process_event(validation_event.envelope.event_id) == "processed"
    finally:
        worker.close()
        close_object_storage(storage)

    with SqlAlchemyOperationUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        operation = uow.operations.get(operation_id, workspace_id="upload-workspace")
    assert operation is not None
    assert operation.state.value == "FAILED"
    assert operation.error is not None
    assert operation.error.code == "ASSET_RETENTION_EXPIRED"
    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        facts = (
            connection.execute(
                text(
                    "SELECT "
                    "(SELECT status FROM assets WHERE id = :asset_id) AS asset_status, "
                    "(SELECT state FROM asset_objects "
                    "WHERE asset_version_id = :asset_version_id "
                    "AND role = 'ORIGINAL') AS source_state, "
                    "(SELECT COUNT(*) FROM asset_objects "
                    "WHERE asset_version_id = :asset_version_id "
                    "AND role = 'CONTROLLED_ORIGINAL') AS controlled_count, "
                    "(SELECT COUNT(*) FROM asset_validation_results "
                    "WHERE asset_version_id = :asset_version_id "
                    "AND stage = 'PROMOTION') AS promotion_results"
                ),
                {
                    "asset_id": body["asset"]["id"],
                    "asset_version_id": body["asset_version"]["id"],
                },
            )
            .mappings()
            .one()
        )
    assert facts == {
        "asset_status": "DELETED",
        "source_state": "DELETED",
        "controlled_count": 0,
        "promotion_results": 0,
    }
    for bucket, key in (
        (source_bucket, source_key),
        (upload_session.destination_bucket, upload_session.destination_key),
    ):
        with pytest.raises(ClientError) as missing:
            minio_client.head_object(Bucket=bucket, Key=key)  # type: ignore[attr-defined]
        assert missing.value.response["ResponseMetadata"]["HTTPStatusCode"] == 404


def test_task_retention_cleanup_converges_with_inflight_promotion_copy(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    with TestClient(create_app(upload_settings)) as client:
        workflow = client.post(
            "/api/v1/workflows",
            headers=_headers("create-concurrent-retention-workflow-0001"),
            json={
                "workflow_type": "FIXTURE_IMAGE_GENERATION",
                "input_data": {"fixture_config": {"count": 1}},
                "retention_hours": 72,
            },
        )
        assert workflow.status_code == 202, workflow.text
        session = _create_session(
            client,
            idempotency_key="create-concurrent-retention-upload-0001",
            retention_class="TASK",
            workflow_id=workflow.json()["id"],
        )
        source_bucket, source_key = _object_location(session)
        _direct_upload(session)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-concurrent-retention-upload-0001"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
    body = finalized.json()
    deadline = datetime.fromisoformat(body["asset"]["retention_deadline"])
    clock = MutableValidationClock(deadline - timedelta(seconds=30))
    request = _validation_operation_request(
        integration_database=integration_database,
        operation_id=body["validation_operation"]["id"],
    )
    with SqlAlchemyAssetUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        upload_session = uow.upload_sessions.get(
            workspace_id="upload-workspace",
            upload_session_id=str(session["id"]),
        )
    assert upload_session is not None
    destination = ObjectReference(
        location=upload_session.destination_location,
        key=upload_session.destination_key,
    )
    storage = ConcurrentRetentionRaceStorage(
        build_object_storage(upload_settings),
        clock=clock,
        expires_at=deadline,
        destination=destination,
    )
    content_safety = RecordingContentSafetyAdapter()
    executor = _validation_executor(
        integration_database=integration_database,
        upload_settings=upload_settings,
        storage=storage,
        content_safety=content_safety,
        clock=clock,
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            promotion = pool.submit(executor.execute, request)
            assert storage.promotion_ready.wait(timeout=30)
            clock.set(deadline)
            cleanup = pool.submit(executor.execute, request)
            failures: list[OperationExecutionFailure] = []
            for future in (promotion, cleanup):
                with pytest.raises(OperationExecutionFailure) as failed:
                    future.result(timeout=30)
                failures.append(failed.value)
        with pytest.raises(OperationExecutionFailure) as converged:
            executor.execute(request)
        failures.append(converged.value)
    finally:
        close_object_storage(storage)

    assert content_safety.calls == 1
    assert all(
        failure.error.code
        in {
            "ASSET_RETENTION_EXPIRED",
            "RETENTION_CLEANUP_CONCURRENT_WRITE",
        }
        for failure in failures
    )
    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        facts = (
            connection.execute(
                text(
                    "SELECT "
                    "(SELECT status FROM assets WHERE id = :asset_id) AS asset_status, "
                    "(SELECT state FROM asset_objects "
                    "WHERE asset_version_id = :asset_version_id "
                    "AND role = 'ORIGINAL') AS source_state, "
                    "(SELECT COUNT(*) FROM asset_objects "
                    "WHERE asset_version_id = :asset_version_id "
                    "AND role = 'CONTROLLED_ORIGINAL') AS controlled_count, "
                    "(SELECT COUNT(*) FROM asset_validation_results "
                    "WHERE asset_version_id = :asset_version_id "
                    "AND stage = 'CONTENT_SAFETY') AS content_results"
                ),
                {
                    "asset_id": body["asset"]["id"],
                    "asset_version_id": body["asset_version"]["id"],
                },
            )
            .mappings()
            .one()
        )
    assert facts == {
        "asset_status": "DELETED",
        "source_state": "DELETED",
        "controlled_count": 0,
        "content_results": 1,
    }
    for bucket, key in (
        (source_bucket, source_key),
        (upload_session.destination_bucket, upload_session.destination_key),
    ):
        with pytest.raises(ClientError) as missing:
            minio_client.head_object(Bucket=bucket, Key=key)  # type: ignore[attr-defined]
        assert missing.value.response["ResponseMetadata"]["HTTPStatusCode"] == 404


def test_task_retention_deletes_all_owned_versions_before_marking_deleted(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    with TestClient(create_app(upload_settings)) as client:
        workflow = client.post(
            "/api/v1/workflows",
            headers=_headers("create-retention-all-versions-workflow-0001"),
            json={
                "workflow_type": "FIXTURE_IMAGE_GENERATION",
                "input_data": {"fixture_config": {"count": 1}},
                "retention_hours": 72,
            },
        )
        assert workflow.status_code == 202, workflow.text
        session = _create_session(
            client,
            idempotency_key="create-retention-all-versions-upload-0001",
            retention_class="TASK",
            workflow_id=workflow.json()["id"],
        )
        _direct_upload(session)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-retention-all-versions-upload-0001"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
    body = finalized.json()
    request = _validation_operation_request(
        integration_database=integration_database,
        operation_id=body["validation_operation"]["id"],
    )
    with SqlAlchemyAssetUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        upload_session = uow.upload_sessions.get(
            workspace_id="upload-workspace",
            upload_session_id=session["id"],
        )
    assert upload_session is not None

    for _ in range(3):
        minio_client.put_object(  # type: ignore[attr-defined]
            Bucket=upload_session.destination_bucket,
            Key=upload_session.destination_key,
            Body=VALID_PNG,
            ContentType="image/png",
            Metadata={
                "sha256": upload_session.expected_sha256,
                "upload-session-id": upload_session.id,
            },
        )
    versions_before = [
        item
        for item in minio_client.list_object_versions(  # type: ignore[attr-defined]
            Bucket=upload_session.destination_bucket,
            Prefix=upload_session.destination_key,
        ).get("Versions", [])
        if item["Key"] == upload_session.destination_key
    ]
    assert len(versions_before) >= 3

    target = AssetValidationTargetBinder(
        uow_factory=lambda: SqlAlchemyAssetUnitOfWork(
            integration_database.session_factory  # type: ignore[attr-defined]
        )
    ).load_historical(request)
    assert target.asset.retention_deadline is not None
    storage = build_object_storage(upload_settings)
    verifier = ImageUploadIntegrityVerifier(
        storage=storage,
        transaction_active=is_unit_of_work_active,
        maximum_bytes=upload_settings.upload_max_bytes,
        maximum_dimension=upload_settings.upload_max_image_dimension,
        maximum_pixels=upload_settings.upload_max_image_pixels,
        maximum_frames=upload_settings.upload_max_image_frames,
        maximum_metadata_bytes=upload_settings.upload_max_metadata_bytes,
    )
    bounded_retention = AssetValidationRetentionCoordinator(
        uow_factory=lambda: SqlAlchemyAssetUnitOfWork(
            integration_database.session_factory  # type: ignore[attr-defined]
        ),
        promoter=UploadPromoter(
            storage=storage,
            verifier=verifier,
            retention_max_versions=2,
        ),
        clock=MutableValidationClock(target.asset.retention_deadline),
    )
    try:
        with pytest.raises(AssetValidationRetentionError) as bounded:
            bounded_retention.expire(target)
        assert bounded.value.code == "RETENTION_CLEANUP_STORAGE_UNAVAILABLE"
        with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
            pending = (
                connection.execute(
                    text(
                        "SELECT "
                        "(SELECT status FROM assets WHERE id = :asset_id) AS asset_status, "
                        "(SELECT state FROM asset_objects "
                        "WHERE asset_version_id = :asset_version_id "
                        "AND role = 'ORIGINAL') AS source_state"
                    ),
                    {
                        "asset_id": body["asset"]["id"],
                        "asset_version_id": body["asset_version"]["id"],
                    },
                )
                .mappings()
                .one()
            )
        assert pending == {
            "asset_status": "DELETING",
            "source_state": "DELETE_PENDING",
        }
        retention = AssetValidationRetentionCoordinator(
            uow_factory=lambda: SqlAlchemyAssetUnitOfWork(
                integration_database.session_factory  # type: ignore[attr-defined]
            ),
            promoter=UploadPromoter(storage=storage, verifier=verifier),
            clock=MutableValidationClock(target.asset.retention_deadline),
        )
        retention.expire(target)
    finally:
        close_object_storage(storage)

    versions_after = [
        item
        for item in minio_client.list_object_versions(  # type: ignore[attr-defined]
            Bucket=upload_session.destination_bucket,
            Prefix=upload_session.destination_key,
        ).get("Versions", [])
        if item["Key"] == upload_session.destination_key
    ]
    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        facts = (
            connection.execute(
                text(
                    "SELECT "
                    "(SELECT status FROM assets WHERE id = :asset_id) AS asset_status, "
                    "(SELECT state FROM asset_objects "
                    "WHERE asset_version_id = :asset_version_id "
                    "AND role = 'ORIGINAL') AS source_state"
                ),
                {
                    "asset_id": body["asset"]["id"],
                    "asset_version_id": body["asset_version"]["id"],
                },
            )
            .mappings()
            .one()
        )
    assert versions_after == []
    assert facts == {
        "asset_status": "DELETED",
        "source_state": "DELETED",
    }


@pytest.mark.integration
def test_validation_worker_child_persists_real_clamav_scanner_identity(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    del minio_client
    clamav_host, clamav_port = _real_clamav_endpoint()
    real_clamav_settings = upload_settings.model_copy(
        update={
            "asset_malware_adapter": "clamav",
            "clamav_host": clamav_host,
            "clamav_port": clamav_port,
            "clamav_timeout_seconds": 15.0,
        }
    )
    readiness = probe_worker_dependencies(real_clamav_settings)
    assert readiness["malware_scanner"] == "ok"

    with TestClient(create_app(real_clamav_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-real-clamav-child-0001",
        )
        _direct_upload(session)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-real-clamav-child-0001"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
    body = finalized.json()
    operation_id = body["validation_operation"]["id"]
    with SqlAlchemyUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        validation_event = next(
            event
            for event in uow.outbox.list_for_aggregate(operation_id)
            if event.envelope.event_type == "asset.validation.requested"
        )

    child_worker = WorkerRuntime.build(real_clamav_settings)
    try:
        assert child_worker.process_event(validation_event.envelope.event_id) == "processed"
    finally:
        child_worker.close()

    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        malware_evidence = (
            connection.execute(
                text(
                    "SELECT verdict, validator_version, evidence_json "
                    "FROM asset_validation_results "
                    "WHERE asset_version_id = :asset_version_id "
                    "AND stage = 'MALWARE'"
                ),
                {"asset_version_id": body["asset_version"]["id"]},
            )
            .mappings()
            .one()
        )
    assert malware_evidence["verdict"] == "PASS"
    assert malware_evidence["validator_version"].startswith("ClamAV ")
    assert malware_evidence["validator_version"] != "clamav-unavailable"
    evidence = malware_evidence["evidence_json"]
    if isinstance(evidence, str):
        evidence = json.loads(evidence)
    assert evidence["scanner_version"] == malware_evidence["validator_version"]


def test_asset_validation_http_projection_is_workspace_scoped(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    del integration_database, minio_client
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-validation-workspace-scope-0001",
        )
        _direct_upload(session)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-validation-workspace-scope-0001"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text

        hidden = client.get(
            f"/api/v1/assets/{finalized.json()['asset']['id']}/validation",
            headers=_read_headers(
                "other-upload-workspace",
                actor_id="other-upload-tester",
            ),
        )

    assert hidden.status_code == 404
    assert hidden.json()["code"] == "NOT_FOUND"
    assert hidden.json()["category"] == "not_found"
    assert hidden.json()["retryable"] is False
    assert hidden.json()["details"] == {}
    assert "stages" not in hidden.json()


def test_asset_validation_http_projection_rejects_invalid_operation_binding(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    del minio_client
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-validation-invalid-binding-0001",
        )
        _direct_upload(session)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-validation-invalid-binding-0001"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
        body = finalized.json()

        with integration_database.engine.begin() as connection:  # type: ignore[attr-defined]
            connection.execute(
                text(
                    "UPDATE durable_operations SET input_hash = :input_hash "
                    "WHERE id = :operation_id"
                ),
                {
                    "input_hash": "0" * 64,
                    "operation_id": body["validation_operation"]["id"],
                },
            )

        invalid = client.get(
            f"/api/v1/assets/{body['asset']['id']}/validation",
            headers=_read_headers(),
        )

    assert invalid.status_code == 422
    assert invalid.json()["code"] == "DOMAIN_ERROR"
    assert invalid.json()["category"] == "domain"
    assert invalid.json()["retryable"] is False
    assert invalid.json()["details"] == {}
    assert invalid.json()["message"] == "Asset validation operation binding is invalid"
    assert "stages" not in invalid.json()


def test_asset_validation_http_projection_survives_later_asset_lifecycle_states(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    del minio_client
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-validation-history-0001",
        )
        _direct_upload(session)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-validation-history-0001"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
        body = finalized.json()
        with SqlAlchemyUnitOfWork(
            integration_database.session_factory  # type: ignore[attr-defined]
        ) as uow:
            validation_event = next(
                event
                for event in uow.outbox.list_for_aggregate(body["validation_operation"]["id"])
                if event.envelope.event_type == "asset.validation.requested"
            )
        worker = WorkerRuntime.build(upload_settings)
        try:
            assert worker.process_event(validation_event.envelope.event_id) == "processed"
        finally:
            worker.close()

        for asset_state in ("AVAILABLE", "RIGHTS_EXPIRED", "DELETING", "DELETED"):
            with integration_database.engine.begin() as connection:  # type: ignore[attr-defined]
                connection.execute(
                    text("UPDATE assets SET status = :asset_state WHERE id = :asset_id"),
                    {
                        "asset_state": asset_state,
                        "asset_id": body["asset"]["id"],
                    },
                )
            historical = client.get(
                f"/api/v1/assets/{body['asset']['id']}/validation",
                headers=_read_headers(),
            )
            assert historical.status_code == 200, historical.text
            assert historical.json()["asset_status"] == asset_state
            assert historical.json()["operation"]["state"] == "SUCCEEDED"
            assert [stage["stage"] for stage in historical.json()["stages"]] == [
                "LOCAL_FORMAT",
                "MALWARE",
                "CONTENT_SAFETY",
                "PROVENANCE",
                "PROMOTION",
            ]


def test_asset_validation_historical_projection_rejects_object_identity_tampering(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    del minio_client
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-validation-history-tamper-0001",
        )
        _direct_upload(session)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-validation-history-tamper-0001"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
        body = finalized.json()
        with integration_database.engine.begin() as connection:  # type: ignore[attr-defined]
            connection.execute(
                text(
                    "UPDATE asset_objects SET etag = :etag "
                    "WHERE asset_version_id = :asset_version_id "
                    "AND role = 'ORIGINAL'"
                ),
                {
                    "etag": "tampered-object-etag",
                    "asset_version_id": body["asset_version"]["id"],
                },
            )

        invalid = client.get(
            f"/api/v1/assets/{body['asset']['id']}/validation",
            headers=_read_headers(),
        )

    assert invalid.status_code == 422
    assert invalid.json()["code"] == "DOMAIN_ERROR"
    assert invalid.json()["message"] == "Asset validation operation binding is invalid"


@pytest.mark.parametrize("fault_boundary", ["controlled_object", "promotion_result"])
def test_validation_promotion_unique_races_are_retryable_and_recoverable(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
    fault_boundary: str,
) -> None:
    del minio_client
    identity = fault_boundary.replace("_", "-")
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key=f"create-{identity}-race-0001",
        )
        _direct_upload(session)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers(f"finalize-{identity}-race-0001"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
    body = finalized.json()
    request = _validation_operation_request(
        integration_database=integration_database,
        operation_id=body["validation_operation"]["id"],
    )
    storage = build_object_storage(upload_settings)
    fault = PromotionUniqueFault(fault_boundary)
    faulting_executor = _validation_executor(
        integration_database=integration_database,
        upload_settings=upload_settings,
        storage=storage,
        uow_factory=lambda: PromotionUniqueFaultUnitOfWork(
            integration_database.session_factory,  # type: ignore[attr-defined]
            fault,
        ),
    )
    try:
        with pytest.raises(OperationExecutionFailure) as failed:
            faulting_executor.execute(request)
        assert failed.value.error.retryable is True
        assert failed.value.error.code == "PROMOTION_CONCURRENT_WRITE"
        assert fault.triggered

        recovered = _validation_executor(
            integration_database=integration_database,
            upload_settings=upload_settings,
            storage=storage,
        ).execute(request)
        assert recovered.operation_id == request.operation_id
    finally:
        close_object_storage(storage)

    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        facts = (
            connection.execute(
                text(
                    "SELECT "
                    "(SELECT status FROM assets WHERE id = :asset_id) AS asset_status, "
                    "(SELECT COUNT(*) FROM asset_objects "
                    "WHERE asset_version_id = :asset_version_id "
                    "AND role = 'CONTROLLED_ORIGINAL' AND state = 'CONTROLLED') "
                    "AS controlled_objects, "
                    "(SELECT COUNT(*) FROM asset_validation_results "
                    "WHERE asset_version_id = :asset_version_id "
                    "AND stage = 'PROMOTION') AS promotion_results"
                ),
                {
                    "asset_id": body["asset"]["id"],
                    "asset_version_id": body["asset_version"]["id"],
                },
            )
            .mappings()
            .one()
        )
    assert facts == {
        "asset_status": "PENDING_RIGHTS",
        "controlled_objects": 1,
        "promotion_results": 1,
    }


def test_validation_pass_evidence_rejects_an_incompatible_malware_identity(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    del minio_client
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-malware-identity-drift-0001",
        )
        _direct_upload(session)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-malware-identity-drift-0001"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
    body = finalized.json()
    request = _validation_operation_request(
        integration_database=integration_database,
        operation_id=body["validation_operation"]["id"],
    )
    storage = build_object_storage(upload_settings)
    fault = PromotionUniqueFault("controlled_object")
    first_executor = _validation_executor(
        integration_database=integration_database,
        upload_settings=upload_settings,
        storage=storage,
        malware_scanner=DeterministicMalwareScanner(
            scanner_version="deterministic-clamav-signatures-v1",
        ),
        uow_factory=lambda: PromotionUniqueFaultUnitOfWork(
            integration_database.session_factory,  # type: ignore[attr-defined]
            fault,
        ),
    )
    try:
        with pytest.raises(OperationExecutionFailure) as interrupted:
            first_executor.execute(request)
        assert interrupted.value.error.code == "PROMOTION_CONCURRENT_WRITE"
        assert fault.triggered

        second_executor = _validation_executor(
            integration_database=integration_database,
            upload_settings=upload_settings,
            storage=storage,
            malware_scanner=DeterministicMalwareScanner(
                scanner_version="deterministic-clamav-signatures-v2",
            ),
        )
        with pytest.raises(OperationExecutionFailure) as incompatible:
            second_executor.execute(replace(request, attempt_count=2))
    finally:
        close_object_storage(storage)

    assert incompatible.value.error.retryable is False
    assert incompatible.value.error.code == "MALWARE_EVIDENCE_IDENTITY_MISMATCH"
    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        malware_rows = list(
            connection.execute(
                text(
                    "SELECT attempt_number, verdict, validator_version "
                    "FROM asset_validation_results "
                    "WHERE asset_version_id = :asset_version_id "
                    "AND stage = 'MALWARE' ORDER BY attempt_number"
                ),
                {"asset_version_id": body["asset_version"]["id"]},
            ).mappings()
        )
    assert malware_rows == [
        {
            "attempt_number": 1,
            "verdict": "PASS",
            "validator_version": "deterministic-clamav-signatures-v1",
        }
    ]


def test_validation_worker_retry_reuses_prior_pass_evidence_after_external_promotion(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-validation-post-copy-retry-0001",
        )
        source_bucket, source_key = _object_location(session)
        _direct_upload(session)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-validation-post-copy-retry-0001"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
    body = finalized.json()
    operation_id = body["validation_operation"]["id"]
    with SqlAlchemyUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        validation_event = next(
            event
            for event in uow.outbox.list_for_aggregate(operation_id)
            if event.envelope.event_type == "asset.validation.requested"
        )

    storage = build_object_storage(upload_settings)
    fault = PromotionUniqueFault("controlled_object")
    faulting_worker = WorkerRuntime.build(
        upload_settings,
        operation_executors={
            OperationKind.ASSET_VALIDATION: _validation_executor(
                integration_database=integration_database,
                upload_settings=upload_settings,
                storage=storage,
                uow_factory=lambda: PromotionUniqueFaultUnitOfWork(
                    integration_database.session_factory,  # type: ignore[attr-defined]
                    fault,
                ),
            )
        },
    )
    try:
        assert faulting_worker.process_event(validation_event.envelope.event_id) == "processed"
    finally:
        faulting_worker.close()
    assert fault.triggered

    with SqlAlchemyOperationUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        failed = uow.operations.get(
            operation_id,
            workspace_id="upload-workspace",
        )
    assert failed is not None
    assert failed.state.value == "RETRYABLE_FAILED"
    assert failed.attempt_count == 1
    assert failed.next_attempt_at is not None
    assert failed.error is not None
    assert failed.error.code == "PROMOTION_CONCURRENT_WRITE"

    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        storage_facts = (
            connection.execute(
                text(
                    "SELECT destination_bucket, destination_key "
                    "FROM upload_sessions WHERE id = :upload_session_id"
                ),
                {"upload_session_id": session["id"]},
            )
            .mappings()
            .one()
        )
        first_attempt_stages = list(
            connection.execute(
                text(
                    "SELECT attempt_number, stage, verdict "
                    "FROM asset_validation_results "
                    "WHERE asset_version_id = :asset_version_id "
                    "ORDER BY stage"
                ),
                {"asset_version_id": body["asset_version"]["id"]},
            ).mappings()
        )
    assert [
        (row["attempt_number"], row["stage"], row["verdict"]) for row in first_attempt_stages
    ] == [
        (1, "CONTENT_SAFETY", "PASS"),
        (1, "LOCAL_FORMAT", "PASS"),
        (1, "MALWARE", "PASS"),
        (1, "PROVENANCE", "PASS"),
    ]
    with pytest.raises(ClientError) as missing_source:
        minio_client.head_object(Bucket=source_bucket, Key=source_key)  # type: ignore[attr-defined]
    assert missing_source.value.response["ResponseMetadata"]["HTTPStatusCode"] == 404
    assert minio_client.head_object(  # type: ignore[attr-defined]
        Bucket=storage_facts["destination_bucket"],
        Key=storage_facts["destination_key"],
    )["ContentLength"] == len(VALID_PNG)

    recovery = OperationRecoveryService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(
            integration_database.session_factory  # type: ignore[attr-defined]
        ),
        batch_size=10,
    )
    assert recovery.recover_once(now=failed.next_attempt_at) == 1
    with SqlAlchemyUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        recovery_event = next(
            event
            for event in uow.outbox.list_for_aggregate(operation_id)
            if event.envelope.event_type == "operation.recovery.requested"
        )

    retry_delay = (failed.next_attempt_at - datetime.now(UTC)).total_seconds()
    if retry_delay > 0:
        time.sleep(retry_delay + 0.05)
    recovered_worker = WorkerRuntime.build(upload_settings)
    try:
        assert recovered_worker.process_event(recovery_event.envelope.event_id) == "processed"
    finally:
        recovered_worker.close()
        close_object_storage(storage)

    with SqlAlchemyOperationUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        recovered = uow.operations.get(
            operation_id,
            workspace_id="upload-workspace",
        )
    assert recovered is not None
    assert recovered.state.value == "SUCCEEDED"
    assert recovered.attempt_count == 2
    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        final_facts = (
            connection.execute(
                text(
                    "SELECT "
                    "(SELECT status FROM assets WHERE id = :asset_id) AS asset_status, "
                    "(SELECT state FROM asset_objects "
                    "WHERE asset_version_id = :asset_version_id "
                    "AND role = 'ORIGINAL') AS source_state"
                ),
                {
                    "asset_id": body["asset"]["id"],
                    "asset_version_id": body["asset_version"]["id"],
                },
            )
            .mappings()
            .one()
        )
        final_stages = list(
            connection.execute(
                text(
                    "SELECT attempt_number, stage, verdict "
                    "FROM asset_validation_results "
                    "WHERE asset_version_id = :asset_version_id "
                    "ORDER BY stage"
                ),
                {"asset_version_id": body["asset_version"]["id"]},
            ).mappings()
        )
    assert final_facts == {
        "asset_status": "PENDING_RIGHTS",
        "source_state": "DELETED",
    }
    assert [(row["attempt_number"], row["stage"], row["verdict"]) for row in final_stages] == [
        (1, "CONTENT_SAFETY", "PASS"),
        (1, "LOCAL_FORMAT", "PASS"),
        (1, "MALWARE", "PASS"),
        (2, "PROMOTION", "PASS"),
        (1, "PROVENANCE", "PASS"),
    ]


def test_validation_worker_reconciles_committed_retryable_evidence_after_process_death(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-validation-evidence-crash-0001",
        )
        source_bucket, source_key = _object_location(session)
        _direct_upload(session)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-validation-evidence-crash-0001"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
    body = finalized.json()
    operation_id = body["validation_operation"]["id"]
    with SqlAlchemyUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        validation_event = next(
            event
            for event in uow.outbox.list_for_aggregate(operation_id)
            if event.envelope.event_type == "asset.validation.requested"
        )

    storage = build_object_storage(upload_settings)
    crashing_executor = CrashAfterCommittedValidationFailureExecutor(
        _validation_executor(
            integration_database=integration_database,
            upload_settings=upload_settings,
            storage=storage,
            malware_scanner=DeterministicMalwareScanner(
                outcome=MalwareScanOutcome.UNAVAILABLE,
            ),
        )
    )
    crashing_worker = WorkerRuntime.build(
        upload_settings,
        operation_executors={OperationKind.ASSET_VALIDATION: crashing_executor},
    )
    try:
        with pytest.raises(SimulatedValidationWorkerDeath):
            crashing_worker.process_event(validation_event.envelope.event_id)
    finally:
        crashing_worker.close()
    assert crashing_executor.crashed

    with integration_database.engine.begin() as connection:  # type: ignore[attr-defined]
        operation_before_recovery = (
            connection.execute(
                text(
                    "SELECT state, attempt_count FROM durable_operations WHERE id = :operation_id"
                ),
                {"operation_id": operation_id},
            )
            .mappings()
            .one()
        )
        committed_stages = list(
            connection.execute(
                text(
                    "SELECT attempt_number, stage, verdict, reason_code "
                    "FROM asset_validation_results "
                    "WHERE asset_version_id = :asset_version_id "
                    "ORDER BY created_at, id"
                ),
                {"asset_version_id": body["asset_version"]["id"]},
            ).mappings()
        )
        connection.execute(
            text(
                "UPDATE durable_operations SET lease_expires_at = :expired_at "
                "WHERE id = :operation_id"
            ),
            {
                "expired_at": (datetime.now(UTC) - timedelta(seconds=1)).replace(tzinfo=None),
                "operation_id": operation_id,
            },
        )
    assert operation_before_recovery == {"state": "RUNNING", "attempt_count": 1}
    assert [
        (
            row["attempt_number"],
            row["stage"],
            row["verdict"],
            row["reason_code"],
        )
        for row in committed_stages
    ] == [
        (1, "LOCAL_FORMAT", "PASS", None),
        (
            1,
            "MALWARE",
            "RETRYABLE_FAILURE",
            "MALWARE_SCANNER_UNAVAILABLE",
        ),
    ]

    recovery = OperationRecoveryService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(
            integration_database.session_factory  # type: ignore[attr-defined]
        ),
        batch_size=10,
    )
    assert recovery.recover_once() == 1
    with SqlAlchemyUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        reconciliation_event = [
            event
            for event in uow.outbox.list_for_aggregate(operation_id)
            if event.envelope.event_type == "operation.recovery.requested"
        ][-1]

    recovered_worker = WorkerRuntime.build(
        upload_settings,
        operation_executors={
            OperationKind.ASSET_VALIDATION: _validation_executor(
                integration_database=integration_database,
                upload_settings=upload_settings,
                storage=storage,
            )
        },
    )
    try:
        assert recovered_worker.process_event(reconciliation_event.envelope.event_id) == "processed"
        with SqlAlchemyOperationUnitOfWork(
            integration_database.session_factory  # type: ignore[attr-defined]
        ) as uow:
            retryable = uow.operations.get(
                operation_id,
                workspace_id="upload-workspace",
            )
        assert retryable is not None
        assert retryable.state.value == "RETRYABLE_FAILED"
        assert retryable.reconciliation_outcome.value == "CONFIRMED_FAILURE"
        assert retryable.attempt_count == 1
        assert retryable.next_attempt_at is not None
        assert retryable.error is not None
        assert retryable.error.code == "MALWARE_SCANNER_UNAVAILABLE"
        assert retryable.error.retryable is True

        with integration_database.engine.begin() as connection:  # type: ignore[attr-defined]
            connection.execute(
                text("UPDATE outbox_events SET published_at = :published_at WHERE id = :event_id"),
                {
                    "published_at": datetime.now(UTC).replace(tzinfo=None),
                    "event_id": reconciliation_event.envelope.event_id,
                },
            )
        assert recovery.recover_once(now=retryable.next_attempt_at) == 1
        with SqlAlchemyUnitOfWork(
            integration_database.session_factory  # type: ignore[attr-defined]
        ) as uow:
            retry_event = [
                event
                for event in uow.outbox.list_for_aggregate(operation_id)
                if event.envelope.event_type == "operation.recovery.requested"
            ][-1]
        retry_delay = (retryable.next_attempt_at - datetime.now(UTC)).total_seconds()
        if retry_delay > 0:
            time.sleep(retry_delay + 0.05)
        assert recovered_worker.process_event(retry_event.envelope.event_id) == "processed"
    finally:
        recovered_worker.close()
        close_object_storage(storage)

    with SqlAlchemyOperationUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        succeeded = uow.operations.get(
            operation_id,
            workspace_id="upload-workspace",
        )
    assert succeeded is not None
    assert succeeded.state.value == "SUCCEEDED"
    assert succeeded.attempt_count == 2
    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        final_stages = list(
            connection.execute(
                text(
                    "SELECT attempt_number, stage, verdict "
                    "FROM asset_validation_results "
                    "WHERE asset_version_id = :asset_version_id "
                    "ORDER BY attempt_number, created_at, id"
                ),
                {"asset_version_id": body["asset_version"]["id"]},
            ).mappings()
        )
    assert [(row["attempt_number"], row["stage"], row["verdict"]) for row in final_stages] == [
        (1, "LOCAL_FORMAT", "PASS"),
        (1, "MALWARE", "RETRYABLE_FAILURE"),
        (2, "MALWARE", "PASS"),
        (2, "CONTENT_SAFETY", "PASS"),
        (2, "PROVENANCE", "PASS"),
        (2, "PROMOTION", "PASS"),
    ]
    with pytest.raises(ClientError) as missing_source:
        minio_client.head_object(Bucket=source_bucket, Key=source_key)  # type: ignore[attr-defined]
    assert missing_source.value.response["ResponseMetadata"]["HTTPStatusCode"] == 404


def test_first_validation_delivery_after_deadline_converges_zero_attempt_failure(
    integration_database: object,
    upload_settings: Settings,
) -> None:
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-validation-zero-attempt-deadline-0001",
        )
        _direct_upload(session)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-validation-zero-attempt-deadline-0001"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
    body = finalized.json()
    operation_id = body["validation_operation"]["id"]
    asset_id = body["asset"]["id"]
    asset_version_id = body["asset_version"]["id"]
    with SqlAlchemyUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        validation_event = next(
            event
            for event in uow.outbox.list_for_aggregate(operation_id)
            if event.envelope.event_type == "asset.validation.requested"
        )
    with integration_database.engine.begin() as connection:  # type: ignore[attr-defined]
        connection.execute(
            text(
                "UPDATE durable_operations SET execution_deadline_at = :expired_at "
                "WHERE id = :operation_id"
            ),
            {
                "expired_at": (datetime.now(UTC) - timedelta(seconds=1)).replace(tzinfo=None),
                "operation_id": operation_id,
            },
        )

    storage = build_object_storage(upload_settings)
    worker = WorkerRuntime.build(
        upload_settings,
        operation_executors={
            OperationKind.ASSET_VALIDATION: _validation_executor(
                integration_database=integration_database,
                upload_settings=upload_settings,
                storage=storage,
            )
        },
    )
    try:
        assert worker.process_event(validation_event.envelope.event_id) == "processed"
    finally:
        worker.close()
        close_object_storage(storage)

    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        facts = (
            connection.execute(
                text(
                    "SELECT "
                    "(SELECT state FROM durable_operations WHERE id = :operation_id) "
                    "AS operation_state, "
                    "(SELECT attempt_count FROM durable_operations WHERE id = :operation_id) "
                    "AS attempt_count, "
                    "(SELECT status FROM assets WHERE id = :asset_id) AS asset_status, "
                    "(SELECT state FROM asset_objects "
                    "WHERE asset_version_id = :asset_version_id "
                    "AND role = 'ORIGINAL') AS source_state, "
                    "(SELECT COUNT(*) FROM asset_validation_results "
                    "WHERE asset_version_id = :asset_version_id) AS stage_count"
                ),
                {
                    "asset_id": asset_id,
                    "asset_version_id": asset_version_id,
                    "operation_id": operation_id,
                },
            )
            .mappings()
            .one()
        )
    with SqlAlchemyUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        failed_events = [
            event
            for event in uow.outbox.list_for_aggregate(asset_id)
            if event.envelope.event_type == "asset.validation.failed"
        ]

    assert facts == {
        "operation_state": "FAILED",
        "attempt_count": 0,
        "asset_status": "FAILED",
        "source_state": "QUARANTINED",
        "stage_count": 0,
    }
    assert len(failed_events) == 1
    assert failed_events[0].envelope.payload == {
        "workspace_id": "upload-workspace",
        "asset_id": asset_id,
        "asset_version_id": asset_version_id,
        "operation_id": operation_id,
        "attempt_number": 0,
        "outcome": "FAILED",
        "reason_code": "OPERATION_MAXIMUM_ELAPSED",
    }


def test_recovery_scanner_terminal_exhaustion_converges_asset_and_event(
    integration_database: object,
    upload_settings: Settings,
) -> None:
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-validation-terminal-scanner-0001",
        )
        _direct_upload(session)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-validation-terminal-scanner-0001"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
    body = finalized.json()
    operation_id = body["validation_operation"]["id"]
    asset_id = body["asset"]["id"]
    with SqlAlchemyUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        validation_event = next(
            event
            for event in uow.outbox.list_for_aggregate(operation_id)
            if event.envelope.event_type == "asset.validation.requested"
        )

    storage = build_object_storage(upload_settings)
    crashing_worker = WorkerRuntime.build(
        upload_settings,
        operation_executors={
            OperationKind.ASSET_VALIDATION: _validation_executor(
                integration_database=integration_database,
                upload_settings=upload_settings,
                storage=storage,
                content_safety=CrashingContentSafetyAdapter(),
            )
        },
    )
    try:
        with pytest.raises(SimulatedValidationWorkerDeath):
            crashing_worker.process_event(validation_event.envelope.event_id)
    finally:
        crashing_worker.close()

    first_scan_at = datetime.now(UTC)
    with integration_database.engine.begin() as connection:  # type: ignore[attr-defined]
        connection.execute(
            text(
                "UPDATE durable_operations SET lease_expires_at = :expired_at "
                "WHERE id = :operation_id"
            ),
            {
                "expired_at": (first_scan_at - timedelta(seconds=1)).replace(tzinfo=None),
                "operation_id": operation_id,
            },
        )
    recovery = OperationRecoveryService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(
            integration_database.session_factory  # type: ignore[attr-defined]
        ),
        batch_size=10,
        reconciliation_max_elapsed=timedelta(seconds=10),
    )
    assert recovery.recover_once(now=first_scan_at) == 1
    with SqlAlchemyUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        recovery_event = next(
            event
            for event in reversed(uow.outbox.list_for_aggregate(operation_id))
            if event.published_at is None
            and event.envelope.event_type == "operation.recovery.requested"
        )

    recovering_worker = WorkerRuntime.build(
        upload_settings,
        operation_executors={
            OperationKind.ASSET_VALIDATION: _validation_executor(
                integration_database=integration_database,
                upload_settings=upload_settings,
                storage=storage,
                content_safety=RecordingContentSafetyAdapter(
                    outcome=ContentSafetyOutcome.RETRYABLE_FAILURE,
                    failure_code="PROVIDER_TIMEOUT",
                ),
            )
        },
    )
    try:
        assert recovering_worker.process_event(recovery_event.envelope.event_id) == "processed"
        with integration_database.engine.begin() as connection:  # type: ignore[attr-defined]
            before_exhaustion = (
                connection.execute(
                    text(
                        "SELECT "
                        "(SELECT state FROM durable_operations WHERE id = :operation_id) "
                        "AS operation_state, "
                        "(SELECT status FROM assets WHERE id = :asset_id) AS asset_status"
                    ),
                    {"asset_id": asset_id, "operation_id": operation_id},
                )
                .mappings()
                .one()
            )
            connection.execute(
                text("UPDATE outbox_events SET published_at = :now WHERE id = :event_id"),
                {
                    "event_id": recovery_event.envelope.event_id,
                    "now": datetime.now(UTC).replace(tzinfo=None),
                },
            )
        assert before_exhaustion == {
            "operation_state": "RECONCILING",
            "asset_status": "VALIDATING",
        }

        assert recovery.recover_once(now=first_scan_at + timedelta(seconds=10)) == 1
        with SqlAlchemyUnitOfWork(
            integration_database.session_factory  # type: ignore[attr-defined]
        ) as uow:
            terminal_recovery_event = next(
                event
                for event in reversed(uow.outbox.list_for_aggregate(operation_id))
                if event.published_at is None
                and event.envelope.payload["recovery_reason"] == "TERMINAL_FAILURE"
            )
        with integration_database.engine.begin() as connection:  # type: ignore[attr-defined]
            connection.execute(
                text("UPDATE outbox_events SET available_at = :now WHERE id = :event_id"),
                {
                    "event_id": terminal_recovery_event.envelope.event_id,
                    "now": datetime.now(UTC).replace(tzinfo=None),
                },
            )
        assert (
            recovering_worker.process_event(terminal_recovery_event.envelope.event_id)
            == "processed"
        )
        assert (
            recovering_worker.process_event(terminal_recovery_event.envelope.event_id)
            == "duplicate"
        )
    finally:
        recovering_worker.close()
        close_object_storage(storage)

    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        converged = (
            connection.execute(
                text(
                    "SELECT "
                    "(SELECT state FROM durable_operations WHERE id = :operation_id) "
                    "AS operation_state, "
                    "(SELECT status FROM assets WHERE id = :asset_id) AS asset_status, "
                    "(SELECT state FROM asset_objects "
                    "WHERE asset_version_id = :asset_version_id "
                    "AND role = 'ORIGINAL') AS source_state, "
                    "(SELECT COUNT(*) FROM outbox_events "
                    "WHERE aggregate_id = :asset_id "
                    "AND event_type = 'asset.validation.failed') AS failed_events"
                ),
                {
                    "asset_id": asset_id,
                    "asset_version_id": body["asset_version"]["id"],
                    "operation_id": operation_id,
                },
            )
            .mappings()
            .one()
        )
    assert converged == {
        "operation_state": "FAILED",
        "asset_status": "FAILED",
        "source_state": "QUARANTINED",
        "failed_events": 1,
    }


def test_delayed_terminal_convergence_expires_task_asset_instead_of_retaining_source(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    one_attempt = upload_settings.model_copy(update={"asset_validation_max_attempts": 1})
    with TestClient(create_app(one_attempt)) as client:
        workflow = client.post(
            "/api/v1/workflows",
            headers=_headers("create-terminal-retention-workflow-0001"),
            json={
                "workflow_type": "FIXTURE_IMAGE_GENERATION",
                "input_data": {"fixture_config": {"count": 1}},
                "retention_hours": 72,
            },
        )
        assert workflow.status_code == 202, workflow.text
        session = _create_session(
            client,
            idempotency_key="create-terminal-retention-upload-0001",
            retention_class="TASK",
            workflow_id=workflow.json()["id"],
        )
        source_bucket, source_key = _object_location(session)
        _direct_upload(session)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-terminal-retention-upload-0001"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
    body = finalized.json()
    operation_id = body["validation_operation"]["id"]
    deadline = datetime.fromisoformat(body["asset"]["retention_deadline"])
    service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(
            integration_database.session_factory  # type: ignore[attr-defined]
        )
    )
    claimed_at = datetime.now(UTC) - timedelta(seconds=1)
    service.claim(
        workspace_id="upload-workspace",
        operation_id=operation_id,
        owner="terminal-retention-worker",
        lease_duration=timedelta(microseconds=1),
        now=claimed_at,
    )
    assert (
        OperationRecoveryService(
            uow_factory=lambda: SqlAlchemyOperationUnitOfWork(
                integration_database.session_factory  # type: ignore[attr-defined]
            ),
            batch_size=1,
        ).recover_once(now=datetime.now(UTC))
        == 1
    )
    with SqlAlchemyUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        terminal_recovery_event = next(
            event
            for event in reversed(uow.outbox.list_for_aggregate(operation_id))
            if event.envelope.payload.get("recovery_reason") == "TERMINAL_FAILURE"
        )

    clock = MutableValidationClock(deadline + timedelta(microseconds=1))
    storage = build_object_storage(one_attempt)
    worker = WorkerRuntime.build(
        one_attempt,
        operation_executors={
            OperationKind.ASSET_VALIDATION: _validation_executor(
                integration_database=integration_database,
                upload_settings=one_attempt,
                storage=storage,
                clock=clock,
            )
        },
    )
    try:
        assert worker.process_event(terminal_recovery_event.envelope.event_id) == "processed"
    finally:
        worker.close()
        close_object_storage(storage)

    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        facts = (
            connection.execute(
                text(
                    "SELECT "
                    "(SELECT state FROM durable_operations WHERE id = :operation_id) "
                    "AS operation_state, "
                    "(SELECT recovery_generation FROM durable_operations "
                    "WHERE id = :operation_id) AS recovery_generation, "
                    "(SELECT recovery_consumed_generation FROM durable_operations "
                    "WHERE id = :operation_id) AS recovery_consumed_generation, "
                    "(SELECT status FROM assets WHERE id = :asset_id) AS asset_status, "
                    "(SELECT state FROM asset_objects "
                    "WHERE asset_version_id = :asset_version_id "
                    "AND role = 'ORIGINAL') AS source_state, "
                    "(SELECT COUNT(*) FROM outbox_events "
                    "WHERE aggregate_id = :asset_id "
                    "AND event_type = 'asset.validation.failed') AS failed_events"
                ),
                {
                    "asset_id": body["asset"]["id"],
                    "asset_version_id": body["asset_version"]["id"],
                    "operation_id": operation_id,
                },
            )
            .mappings()
            .one()
        )
    assert facts == {
        "operation_state": "FAILED",
        "recovery_generation": 1,
        "recovery_consumed_generation": 1,
        "asset_status": "DELETED",
        "source_state": "DELETED",
        "failed_events": 0,
    }
    with pytest.raises(ClientError) as missing_source:
        minio_client.head_object(Bucket=source_bucket, Key=source_key)  # type: ignore[attr-defined]
    assert missing_source.value.response["ResponseMetadata"]["HTTPStatusCode"] == 404


def test_terminal_failure_crossing_retention_while_locking_expires_task_asset(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    with TestClient(create_app(upload_settings)) as client:
        workflow = client.post(
            "/api/v1/workflows",
            headers=_headers("create-terminal-lock-retention-workflow-0001"),
            json={
                "workflow_type": "FIXTURE_IMAGE_GENERATION",
                "input_data": {"fixture_config": {"count": 1}},
                "retention_hours": 72,
            },
        )
        assert workflow.status_code == 202, workflow.text
        session = _create_session(
            client,
            idempotency_key="create-terminal-lock-retention-upload-0001",
            retention_class="TASK",
            workflow_id=workflow.json()["id"],
        )
        _direct_upload(session)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-terminal-lock-retention-upload-0001"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
    body = finalized.json()
    asset_id = body["asset"]["id"]
    asset_version_id = body["asset_version"]["id"]
    deadline = datetime.fromisoformat(body["asset"]["retention_deadline"])
    request = _validation_operation_request(
        integration_database=integration_database,
        operation_id=body["validation_operation"]["id"],
    )
    with SqlAlchemyAssetUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        source = uow.assets.get_object(
            workspace_id="upload-workspace",
            asset_version_id=asset_version_id,
            role="ORIGINAL",
        )
    assert source is not None
    assert source.provider_version_id is not None
    minio_client.head_object(  # type: ignore[attr-defined]
        Bucket=source.bucket,
        Key=source.key,
        VersionId=source.provider_version_id,
    )

    clock = MutableValidationClock(deadline - timedelta(microseconds=1))
    storage = build_object_storage(upload_settings)
    executor = _validation_executor(
        integration_database=integration_database,
        upload_settings=upload_settings,
        storage=storage,
        uow_factory=lambda: ExpireAfterTerminalAssetLockUnitOfWork(
            integration_database.session_factory,  # type: ignore[attr-defined]
            clock=clock,
            expires_at=deadline + timedelta(microseconds=1),
        ),
        clock=clock,
    )
    try:
        executor.record_terminal_failure(
            request,
            NormalizedOperationError(
                code="PROVIDER_HTTP_403",
                category="provider",
                message="provider rejected the validation request",
                retryable=False,
            ),
        )
    finally:
        close_object_storage(storage)

    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        facts = (
            connection.execute(
                text(
                    "SELECT "
                    "(SELECT status FROM assets WHERE id = :asset_id) AS asset_status, "
                    "(SELECT state FROM asset_objects "
                    "WHERE asset_version_id = :asset_version_id "
                    "AND role = 'ORIGINAL') AS source_state, "
                    "(SELECT COUNT(*) FROM outbox_events "
                    "WHERE aggregate_id = :asset_id "
                    "AND event_type = 'asset.validation.failed') AS failed_events"
                ),
                {
                    "asset_id": asset_id,
                    "asset_version_id": asset_version_id,
                },
            )
            .mappings()
            .one()
        )
    assert facts == {
        "asset_status": "DELETED",
        "source_state": "DELETED",
        "failed_events": 0,
    }
    with pytest.raises(ClientError) as missing_source:
        minio_client.head_object(  # type: ignore[attr-defined]
            Bucket=source.bucket,
            Key=source.key,
            VersionId=source.provider_version_id,
        )
    assert missing_source.value.response["ResponseMetadata"]["HTTPStatusCode"] == 404


def test_validation_worker_fails_once_for_a_permanent_content_provider_error(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-terminal-content-provider-0001",
        )
        source_bucket, source_key = _object_location(session)
        _direct_upload(session)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-terminal-content-provider-0001"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
    body = finalized.json()
    operation_id = body["validation_operation"]["id"]
    with SqlAlchemyUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        validation_event = next(
            event
            for event in uow.outbox.list_for_aggregate(operation_id)
            if event.envelope.event_type == "asset.validation.requested"
        )

    storage = build_object_storage(upload_settings)
    worker = WorkerRuntime.build(
        upload_settings,
        operation_executors={
            OperationKind.ASSET_VALIDATION: _validation_executor(
                integration_database=integration_database,
                upload_settings=upload_settings,
                storage=storage,
                content_safety=DeterministicContentSafetyAdapter(
                    outcome=ContentSafetyOutcome.TERMINAL_FAILURE,
                    policy_version=upload_settings.content_safety_policy_version,
                    mapping_version=upload_settings.content_safety_mapping_version,
                    failure_code="PROVIDER_HTTP_403",
                ),
            )
        },
    )
    try:
        assert worker.process_event(validation_event.envelope.event_id) == "processed"
        assert worker.process_event(validation_event.envelope.event_id) == "duplicate"
        with SqlAlchemyUnitOfWork(
            integration_database.session_factory  # type: ignore[attr-defined]
        ) as uow:
            terminal_events = [
                event
                for event in uow.outbox.list_for_aggregate(body["asset"]["id"])
                if event.envelope.event_type
                in {
                    "asset.validation.completed",
                    "asset.validation.failed",
                }
            ]
        terminal_deliveries = [
            (
                event.envelope.event_type,
                worker.process_event(event.envelope.event_id),
                worker.process_event(event.envelope.event_id),
            )
            for event in terminal_events
        ]
    finally:
        worker.close()
        close_object_storage(storage)

    with SqlAlchemyOperationUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        operation = uow.operations.get(
            operation_id,
            workspace_id="upload-workspace",
        )
    assert operation is not None
    assert operation.state.value == "FAILED"
    assert operation.attempt_count == 1
    assert operation.next_attempt_at is None
    assert operation.error is not None
    assert operation.error.code == "PROVIDER_HTTP_403"
    assert operation.error.retryable is False

    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        facts = (
            connection.execute(
                text(
                    "SELECT "
                    "(SELECT status FROM assets WHERE id = :asset_id) AS asset_status, "
                    "(SELECT state FROM asset_objects "
                    "WHERE asset_version_id = :asset_version_id "
                    "AND role = 'ORIGINAL') AS source_state, "
                    "(SELECT COUNT(*) FROM asset_objects "
                    "WHERE asset_version_id = :asset_version_id "
                    "AND role = 'CONTROLLED_ORIGINAL') AS controlled_count, "
                    "(SELECT COUNT(*) FROM dead_letter_messages "
                    "WHERE workspace_id = 'upload-workspace' "
                    "AND message_id = :event_id) AS validation_event_dead_letters, "
                    "(SELECT reason FROM dead_letter_messages "
                    "WHERE id = :dead_letter_id) AS operation_dead_letter_reason"
                ),
                {
                    "asset_id": body["asset"]["id"],
                    "asset_version_id": body["asset_version"]["id"],
                    "dead_letter_id": operation.dead_letter_id,
                    "event_id": validation_event.envelope.event_id,
                },
            )
            .mappings()
            .one()
        )
        stages = list(
            connection.execute(
                text(
                    "SELECT attempt_number, stage, verdict, reason_code, evidence_json "
                    "FROM asset_validation_results "
                    "WHERE asset_version_id = :asset_version_id "
                    "ORDER BY created_at, id"
                ),
                {"asset_version_id": body["asset_version"]["id"]},
            ).mappings()
        )
    assert facts == {
        "asset_status": "FAILED",
        "source_state": "QUARANTINED",
        "controlled_count": 0,
        "validation_event_dead_letters": 0,
        "operation_dead_letter_reason": "operation_terminal_failure",
    }
    assert operation.dead_letter_id is not None
    assert terminal_deliveries == [("asset.validation.failed", "processed", "duplicate")]
    assert [
        (row["attempt_number"], row["stage"], row["verdict"], row["reason_code"]) for row in stages
    ] == [
        (1, "LOCAL_FORMAT", "PASS", None),
        (1, "MALWARE", "PASS", None),
        (1, "CONTENT_SAFETY", "TERMINAL_FAILURE", "PROVIDER_HTTP_403"),
    ]
    serialized_evidence = json.dumps(
        [
            json.loads(row["evidence_json"])
            if isinstance(row["evidence_json"], str)
            else row["evidence_json"]
            for row in stages
        ],
        sort_keys=True,
    ).lower()
    assert "raw" not in serialized_evidence
    assert "secret" not in serialized_evidence
    assert minio_client.head_object(Bucket=source_bucket, Key=source_key)[  # type: ignore[attr-defined]
        "ContentLength"
    ] == len(VALID_PNG)


def test_validation_retry_budget_exhaustion_marks_failed_and_retains_quarantine(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    one_attempt = upload_settings.model_copy(update={"asset_validation_max_attempts": 1})

    result = _run_validation_worker_matrix_case(
        integration_database=integration_database,
        upload_settings=one_attempt,
        case_id="malware-unavailable-budget-exhausted",
        content=VALID_PNG,
        malware_scanner=DeterministicMalwareScanner(outcome=MalwareScanOutcome.UNAVAILABLE),
    )

    operation = result["operation"]
    assert operation.state.value == "FAILED"
    assert operation.attempt_count == 1
    assert operation.error is not None
    assert operation.error.code == "MALWARE_SCANNER_UNAVAILABLE"
    assert operation.error.retryable is False
    assert operation.dead_letter_id is not None
    assert result["facts"]["asset_status"] == "FAILED"
    assert result["facts"]["source_state"] == "QUARANTINED"
    assert result["facts"]["operation_dead_letter_reason"] == "operation_terminal_failure"
    assert result["terminal_deliveries"] == [("asset.validation.failed", "processed", "duplicate")]
    _assert_unpromoted_validation_storage(
        result,
        minio_client=minio_client,
        expected_source_state="QUARANTINED",
        expected_size=len(VALID_PNG),
    )


def test_validation_terminal_asset_and_outbox_rollback_as_one_mysql_transaction(
    integration_database: object,
    upload_settings: Settings,
) -> None:
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-terminal-rollback-0001",
        )
        _direct_upload(session)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-terminal-rollback-0001"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
    body = finalized.json()
    asset_id = body["asset"]["id"]
    operation_id = body["validation_operation"]["id"]
    with SqlAlchemyAssetUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        asset = uow.assets.get(
            workspace_id="upload-workspace",
            asset_id=asset_id,
            for_update=True,
        )
        assert asset is not None
        asset.begin_validation(now=datetime.now(UTC))
        uow.assets.save_asset(asset)
        uow.commit()
    with SqlAlchemyOperationUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        operation = uow.operations.get(
            operation_id,
            workspace_id="upload-workspace",
        )
    assert operation is not None
    request = replace(
        OperationExecutionRequest.from_operation(operation),
        attempt_count=1,
    )
    error = NormalizedOperationError(
        code="PROVIDER_HTTP_403",
        category="content_safety",
        message="provider credentials were rejected",
        retryable=False,
    )

    storage = build_object_storage(upload_settings)
    faulting = _validation_executor(
        integration_database=integration_database,
        upload_settings=upload_settings,
        storage=storage,
        uow_factory=lambda: TerminalEventCommitFailureUnitOfWork(
            integration_database.session_factory  # type: ignore[attr-defined]
        ),
    )
    try:
        with pytest.raises(SimulatedTerminalEventCommitFailure):
            faulting.record_terminal_failure(request, error)
    finally:
        close_object_storage(storage)

    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        rolled_back = (
            connection.execute(
                text(
                    "SELECT "
                    "(SELECT status FROM assets WHERE id = :asset_id) AS asset_status, "
                    "(SELECT COUNT(*) FROM outbox_events "
                    "WHERE aggregate_id = :asset_id "
                    "AND event_type = 'asset.validation.failed') AS failed_events"
                ),
                {"asset_id": asset_id},
            )
            .mappings()
            .one()
        )
    assert rolled_back == {
        "asset_status": "VALIDATING",
        "failed_events": 0,
    }

    storage = build_object_storage(upload_settings)
    recovered = _validation_executor(
        integration_database=integration_database,
        upload_settings=upload_settings,
        storage=storage,
    )
    try:
        recovered.record_terminal_failure(request, error)
        recovered.record_terminal_failure(request, error)
    finally:
        close_object_storage(storage)
    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        committed = (
            connection.execute(
                text(
                    "SELECT "
                    "(SELECT status FROM assets WHERE id = :asset_id) AS asset_status, "
                    "(SELECT COUNT(*) FROM outbox_events "
                    "WHERE aggregate_id = :asset_id "
                    "AND event_type = 'asset.validation.failed') AS failed_events"
                ),
                {"asset_id": asset_id},
            )
            .mappings()
            .one()
        )
    assert committed == {
        "asset_status": "FAILED",
        "failed_events": 1,
    }


def test_validation_terminal_callback_rejects_input_identity_drift(
    integration_database: object,
    upload_settings: Settings,
) -> None:
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-terminal-identity-drift-0001",
        )
        _direct_upload(session)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-terminal-identity-drift-0001"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
    body = finalized.json()
    request = _validation_operation_request(
        integration_database=integration_database,
        operation_id=body["validation_operation"]["id"],
    )
    error = NormalizedOperationError(
        code="PROVIDER_HTTP_403",
        category="content_safety",
        message="provider credentials were rejected",
        retryable=False,
    )
    storage = build_object_storage(upload_settings)
    executor = _validation_executor(
        integration_database=integration_database,
        upload_settings=upload_settings,
        storage=storage,
    )
    try:
        with pytest.raises(AssetValidationTargetError) as drift:
            executor.record_terminal_failure(
                replace(request, input_hash="f" * 64),
                error,
            )
    finally:
        close_object_storage(storage)

    assert drift.value.code == "VALIDATION_FACT_MISMATCH"
    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        facts = (
            connection.execute(
                text(
                    "SELECT "
                    "(SELECT status FROM assets WHERE id = :asset_id) AS asset_status, "
                    "(SELECT COUNT(*) FROM outbox_events "
                    "WHERE aggregate_id = :asset_id "
                    "AND event_type = 'asset.validation.failed') AS failed_events"
                ),
                {"asset_id": body["asset"]["id"]},
            )
            .mappings()
            .one()
        )
    assert facts == {
        "asset_status": "QUARANTINED",
        "failed_events": 0,
    }


def test_failed_validation_dlq_replay_with_fixed_provider_reuses_quarantine_and_succeeds(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    failed = _run_validation_worker_matrix_case(
        integration_database=integration_database,
        upload_settings=upload_settings,
        case_id="provider-terminal-then-replay",
        content=VALID_PNG,
        content_safety=RecordingContentSafetyAdapter(
            outcome=ContentSafetyOutcome.TERMINAL_FAILURE,
            failure_code="PROVIDER_HTTP_403",
        ),
    )
    failed_operation = failed["operation"]
    assert failed_operation.dead_letter_id is not None
    assert failed["facts"]["asset_status"] == "FAILED"
    assert failed["facts"]["source_state"] == "QUARANTINED"

    dead_letters = DeadLetterOperatorService(
        uow_factory=lambda: SqlAlchemyOperatorUnitOfWork(
            integration_database.session_factory  # type: ignore[attr-defined]
        ),
        access_policy=AllowWorkspaceAdminPolicy(),
    )
    replay = dead_letters.replay(
        workspace_id="upload-workspace",
        dead_letter_id=failed_operation.dead_letter_id,
        principal=AuthenticatedPrincipal(
            actor_id="asset-validation-admin",
            workspace_ids=frozenset({"upload-workspace"}),
            admin_workspace_ids=frozenset({"upload-workspace"}),
        ),
        reason="provider credentials repaired",
        idempotency_key="replay-fixed-provider-0001",
        trace_id="replay-fixed-provider-trace",
    )
    with SqlAlchemyUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        replay_event = uow.outbox.get(replay.replay_event_id)
    assert replay_event is not None

    storage = build_object_storage(upload_settings)
    worker = WorkerRuntime.build(
        upload_settings,
        operation_executors={
            OperationKind.ASSET_VALIDATION: _validation_executor(
                integration_database=integration_database,
                upload_settings=upload_settings,
                storage=storage,
                content_safety=RecordingContentSafetyAdapter(),
            )
        },
    )
    try:
        assert worker.process_event(replay_event.envelope.event_id) == "processed"
        assert worker.process_event(replay_event.envelope.event_id) == "duplicate"
        with SqlAlchemyUnitOfWork(
            integration_database.session_factory  # type: ignore[attr-defined]
        ) as uow:
            terminal_events = [
                event
                for event in uow.outbox.list_for_aggregate(str(failed["asset_id"]))
                if event.envelope.event_type
                in {
                    "asset.validation.completed",
                    "asset.validation.failed",
                }
            ]
        completed = [
            event
            for event in terminal_events
            if event.envelope.event_type == "asset.validation.completed"
        ]
        assert len(completed) == 1
        assert worker.process_event(completed[0].envelope.event_id) == "processed"
        assert worker.process_event(completed[0].envelope.event_id) == "duplicate"
    finally:
        worker.close()
        close_object_storage(storage)

    with SqlAlchemyOperationUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        operation = uow.operations.get(
            str(failed["operation_id"]),
            workspace_id="upload-workspace",
        )
    assert operation is not None
    assert operation.state.value == "SUCCEEDED"
    assert operation.attempt_count == 2
    assert operation.replay_source_dead_letter_id == failed_operation.dead_letter_id
    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        facts = (
            connection.execute(
                text(
                    "SELECT "
                    "(SELECT status FROM assets WHERE id = :asset_id) AS asset_status, "
                    "(SELECT state FROM asset_objects "
                    "WHERE asset_version_id = :asset_version_id "
                    "AND role = 'ORIGINAL') AS source_state, "
                    "(SELECT COUNT(*) FROM asset_objects "
                    "WHERE asset_version_id = :asset_version_id "
                    "AND role = 'CONTROLLED_ORIGINAL') AS controlled_count"
                ),
                {
                    "asset_id": failed["asset_id"],
                    "asset_version_id": failed["asset_version_id"],
                },
            )
            .mappings()
            .one()
        )
        stages = list(
            connection.execute(
                text(
                    "SELECT attempt_number, stage, verdict "
                    "FROM asset_validation_results "
                    "WHERE asset_version_id = :asset_version_id "
                    "ORDER BY attempt_number, created_at, id"
                ),
                {"asset_version_id": failed["asset_version_id"]},
            ).mappings()
        )
    assert facts == {
        "asset_status": "PENDING_RIGHTS",
        "source_state": "DELETED",
        "controlled_count": 1,
    }
    assert [(row["attempt_number"], row["stage"], row["verdict"]) for row in stages] == [
        (1, "LOCAL_FORMAT", "PASS"),
        (1, "MALWARE", "PASS"),
        (1, "CONTENT_SAFETY", "TERMINAL_FAILURE"),
        (2, "CONTENT_SAFETY", "PASS"),
        (2, "PROVENANCE", "PASS"),
        (2, "PROMOTION", "PASS"),
    ]
    assert [
        (
            event.envelope.event_type,
            event.envelope.payload["attempt_number"],
            event.envelope.payload["outcome"],
        )
        for event in terminal_events
    ] == [
        ("asset.validation.failed", 1, "FAILED"),
        ("asset.validation.completed", 2, "PENDING_RIGHTS"),
    ]


def test_concurrent_validation_duplicates_converge_on_unique_stage_evidence(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-concurrent-validation-0001",
        )
        _direct_upload(session)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-concurrent-validation-0001"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
    body = finalized.json()
    request = _validation_operation_request(
        integration_database=integration_database,
        operation_id=body["validation_operation"]["id"],
    )
    barrier = Barrier(2)
    storage_a = build_object_storage(upload_settings)
    storage_b = build_object_storage(upload_settings)
    executor_a = _validation_executor(
        integration_database=integration_database,
        upload_settings=upload_settings,
        storage=storage_a,
        local_validator=BarrierAssetLocalValidator(
            _asset_local_validator(upload_settings),
            barrier,
        ),
    )
    executor_b = _validation_executor(
        integration_database=integration_database,
        upload_settings=upload_settings,
        storage=storage_b,
        local_validator=BarrierAssetLocalValidator(
            _asset_local_validator(upload_settings),
            barrier,
        ),
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(executor_a.execute, request),
                pool.submit(executor_b.execute, request),
            ]
            results = [future.result(timeout=30) for future in futures]
        assert {result.operation_id for result in results} == {request.operation_id}
    finally:
        close_object_storage(storage_a)
        close_object_storage(storage_b)

    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        stages = list(
            connection.execute(
                text(
                    "SELECT stage, COUNT(*) AS result_count "
                    "FROM asset_validation_results "
                    "WHERE asset_version_id = :asset_version_id "
                    "GROUP BY stage ORDER BY stage"
                ),
                {"asset_version_id": body["asset_version"]["id"]},
            ).mappings()
        )
        controlled = (
            connection.execute(
                text(
                    "SELECT ao.provider_version_id, us.destination_bucket, us.destination_key "
                    "FROM asset_objects ao "
                    "JOIN asset_versions av ON av.id = ao.asset_version_id "
                    "JOIN upload_sessions us ON us.id = av.upload_session_id "
                    "WHERE ao.asset_version_id = :asset_version_id "
                    "AND ao.role = 'CONTROLLED_ORIGINAL'"
                ),
                {"asset_version_id": body["asset_version"]["id"]},
            )
            .mappings()
            .one()
        )
    assert {row["stage"]: row["result_count"] for row in stages} == {
        "CONTENT_SAFETY": 1,
        "LOCAL_FORMAT": 1,
        "MALWARE": 1,
        "PROMOTION": 1,
        "PROVENANCE": 1,
    }
    object_versions = [
        version
        for version in minio_client.list_object_versions(  # type: ignore[attr-defined]
            Bucket=controlled["destination_bucket"],
            Prefix=controlled["destination_key"],
        ).get("Versions", [])
        if version["Key"] == controlled["destination_key"]
    ]
    assert [version["VersionId"] for version in object_versions] == [
        controlled["provider_version_id"]
    ]


def test_concurrent_promotion_commit_loser_reconciles_its_exact_storage_version(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-concurrent-promotion-commit-0001",
        )
        _direct_upload(session)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-concurrent-promotion-commit-0001"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
    body = finalized.json()
    request = _validation_operation_request(
        integration_database=integration_database,
        operation_id=body["validation_operation"]["id"],
    )
    with SqlAlchemyAssetUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        upload_session = uow.upload_sessions.get(
            workspace_id="upload-workspace",
            upload_session_id=session["id"],
        )
        asset = uow.assets.get(
            workspace_id="upload-workspace",
            asset_id=body["asset"]["id"],
            for_update=True,
        )
        assert asset is not None
        asset.begin_validation(now=datetime.now(UTC))
        uow.assets.save_asset(asset)
        uow.commit()
    assert upload_session is not None
    target = AssetValidationTargetBinder(
        uow_factory=lambda: SqlAlchemyAssetUnitOfWork(
            integration_database.session_factory  # type: ignore[attr-defined]
        )
    ).load(request)
    destination = ObjectReference(
        location=upload_session.destination_location,
        key=upload_session.destination_key,
    )
    absent_barrier = Barrier(2)
    copy_barrier = Barrier(2)
    copied_barrier = Barrier(2)
    commit_barrier = ControlledObjectCommitBarrier()
    delegate_a = build_object_storage(upload_settings)
    delegate_b = build_object_storage(upload_settings)
    buckets = dict(upload_settings.object_store_buckets)
    storage_a = ForcedConcurrentVersionStorage(
        delegate_a,
        client=minio_client,
        buckets=buckets,
        destination=destination,
        absent_barrier=absent_barrier,
        copy_barrier=copy_barrier,
        copied_barrier=copied_barrier,
    )
    storage_b = ForcedConcurrentVersionStorage(
        delegate_b,
        client=minio_client,
        buckets=buckets,
        destination=destination,
        absent_barrier=absent_barrier,
        copy_barrier=copy_barrier,
        copied_barrier=copied_barrier,
    )

    def barrier_uow():
        return ControlledObjectBarrierUnitOfWork(
            integration_database.session_factory,  # type: ignore[attr-defined]
            commit_barrier,
        )

    def promotion_coordinator(storage: ObjectStorage):
        verifier = ImageUploadIntegrityVerifier(
            storage=storage,
            transaction_active=is_unit_of_work_active,
            maximum_bytes=upload_settings.upload_max_bytes,
            maximum_dimension=upload_settings.upload_max_image_dimension,
            maximum_pixels=upload_settings.upload_max_image_pixels,
            maximum_frames=upload_settings.upload_max_image_frames,
            maximum_metadata_bytes=upload_settings.upload_max_metadata_bytes,
        )
        promoter = UploadPromoter(storage=storage, verifier=verifier)
        return AssetValidationPromotionCoordinator(
            uow_factory=barrier_uow,  # type: ignore[arg-type]
            promoter=promoter,
            retention=AssetValidationRetentionCoordinator(
                uow_factory=barrier_uow,  # type: ignore[arg-type]
                promoter=promoter,
            ),
        )

    coordinator_a = promotion_coordinator(storage_a)
    coordinator_b = promotion_coordinator(storage_b)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(coordinator_a.promote, request=request, target=target),
                pool.submit(coordinator_b.promote, request=request, target=target),
            ]
            results = [future.result(timeout=45) for future in futures]
        assert results == [None, None]
    finally:
        close_object_storage(delegate_a)
        close_object_storage(delegate_b)

    observed_versions = {
        *storage_a.copy_version_ids,
        *storage_b.copy_version_ids,
    }
    assert len(observed_versions) == 2
    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        controlled = (
            connection.execute(
                text(
                    "SELECT ao.provider_version_id, us.destination_bucket, us.destination_key "
                    "FROM asset_objects ao "
                    "JOIN asset_versions av ON av.id = ao.asset_version_id "
                    "JOIN upload_sessions us ON us.id = av.upload_session_id "
                    "WHERE ao.asset_version_id = :asset_version_id "
                    "AND ao.role = 'CONTROLLED_ORIGINAL'"
                ),
                {"asset_version_id": body["asset_version"]["id"]},
            )
            .mappings()
            .one()
        )
    object_versions = [
        version
        for version in minio_client.list_object_versions(  # type: ignore[attr-defined]
            Bucket=controlled["destination_bucket"],
            Prefix=controlled["destination_key"],
        ).get("Versions", [])
        if version["Key"] == controlled["destination_key"]
    ]
    assert [version["VersionId"] for version in object_versions] == [
        controlled["provider_version_id"]
    ]
    assert controlled["provider_version_id"] in observed_versions


def test_expired_validation_worker_lease_recovers_while_original_resumes_late(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    del minio_client
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-validation-lease-recovery-0001",
        )
        _direct_upload(session)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-validation-lease-recovery-0001"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
    body = finalized.json()
    operation_id = body["validation_operation"]["id"]
    with SqlAlchemyUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        validation_event = next(
            event
            for event in uow.outbox.list_for_aggregate(operation_id)
            if event.envelope.event_type == "asset.validation.requested"
        )

    pausing_validator = PausingAssetLocalValidator(_asset_local_validator(upload_settings))
    storage_a = build_object_storage(upload_settings)
    storage_b = build_object_storage(upload_settings)
    worker_a = WorkerRuntime.build(
        upload_settings,
        operation_executors={
            OperationKind.ASSET_VALIDATION: _validation_executor(
                integration_database=integration_database,
                upload_settings=upload_settings,
                storage=storage_a,
                local_validator=pausing_validator,
            )
        },
    )
    worker_b = WorkerRuntime.build(
        upload_settings,
        operation_executors={
            OperationKind.ASSET_VALIDATION: _validation_executor(
                integration_database=integration_database,
                upload_settings=upload_settings,
                storage=storage_b,
            )
        },
    )
    original_result: list[str] = []
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            original = pool.submit(
                worker_a.process_event,
                validation_event.envelope.event_id,
            )
            assert pausing_validator.paused.wait(timeout=15)
            expired_at = datetime.now(UTC) - timedelta(microseconds=1)
            with integration_database.engine.begin() as connection:  # type: ignore[attr-defined]
                connection.execute(
                    text(
                        "UPDATE durable_operations SET lease_expires_at = :expired_at "
                        "WHERE id = :operation_id AND state = 'RUNNING'"
                    ),
                    {
                        "expired_at": expired_at.replace(tzinfo=None),
                        "operation_id": operation_id,
                    },
                )
            recovery = OperationRecoveryService(
                uow_factory=lambda: SqlAlchemyOperationUnitOfWork(
                    integration_database.session_factory  # type: ignore[attr-defined]
                ),
                batch_size=10,
            )
            assert recovery.recover_once() == 1
            with SqlAlchemyUnitOfWork(
                integration_database.session_factory  # type: ignore[attr-defined]
            ) as uow:
                recovery_event = next(
                    event
                    for event in uow.outbox.list_for_aggregate(operation_id)
                    if event.envelope.event_type == "operation.recovery.requested"
                )
            assert worker_b.process_event(recovery_event.envelope.event_id) == "processed"
            pausing_validator.resume.set()
            original_result.append(original.result(timeout=15))
    finally:
        pausing_validator.resume.set()
        worker_a.close()
        worker_b.close()
        close_object_storage(storage_a)
        close_object_storage(storage_b)

    with SqlAlchemyOperationUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        operation = uow.operations.get(
            operation_id,
            workspace_id="upload-workspace",
        )
    assert operation is not None
    assert operation.state.value == "SUCCEEDED"
    assert operation.reconciliation_outcome.value == "CONFIRMED_SUCCESS"
    assert original_result == ["processed"]
    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        counts = (
            connection.execute(
                text(
                    "SELECT "
                    "(SELECT COUNT(*) FROM asset_validation_results "
                    "WHERE asset_version_id = :asset_version_id) AS results, "
                    "(SELECT COUNT(*) FROM asset_objects "
                    "WHERE asset_version_id = :asset_version_id "
                    "AND role = 'CONTROLLED_ORIGINAL') AS controlled"
                ),
                {"asset_version_id": body["asset_version"]["id"]},
            )
            .mappings()
            .one()
        )
    assert counts == {"results": 5, "controlled": 1}


def _run_validation_worker_matrix_case(
    *,
    integration_database: object,
    upload_settings: Settings,
    case_id: str,
    content: bytes,
    asset_kind: str = "IMAGE",
    filename: str = "pixel.png",
    declared_mime: str = "image/png",
    local_validator: object | None = None,
    malware_scanner: object | None = None,
    content_safety: RecordingContentSafetyAdapter | None = None,
    provenance: RecordingProvenanceAdapter | None = None,
) -> dict[str, object]:
    content_adapter = content_safety or RecordingContentSafetyAdapter()
    provenance_adapter = provenance or RecordingProvenanceAdapter()
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key=f"create-validation-matrix-{case_id}",
            content=content,
            asset_kind=asset_kind,
            filename=filename,
            declared_mime=declared_mime,
        )
        source_bucket, source_key = _object_location(session)
        _direct_upload(session, content)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers(f"finalize-validation-matrix-{case_id}"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
    body = finalized.json()
    operation_id = body["validation_operation"]["id"]
    with SqlAlchemyUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        validation_event = next(
            event
            for event in uow.outbox.list_for_aggregate(operation_id)
            if event.envelope.event_type == "asset.validation.requested"
        )

    storage = build_object_storage(upload_settings)
    worker = WorkerRuntime.build(
        upload_settings,
        operation_executors={
            OperationKind.ASSET_VALIDATION: _validation_executor(
                integration_database=integration_database,
                upload_settings=upload_settings,
                storage=storage,
                local_validator=local_validator,
                malware_scanner=malware_scanner,
                content_safety=content_adapter,
                provenance=provenance_adapter,
            )
        },
    )
    try:
        assert worker.process_event(validation_event.envelope.event_id) == "processed"
        assert worker.process_event(validation_event.envelope.event_id) == "duplicate"
        with SqlAlchemyUnitOfWork(
            integration_database.session_factory  # type: ignore[attr-defined]
        ) as uow:
            terminal_events = [
                event
                for event in uow.outbox.list_for_aggregate(body["asset"]["id"])
                if event.envelope.event_type
                in {
                    "asset.validation.completed",
                    "asset.validation.failed",
                }
            ]
        terminal_deliveries = [
            (
                event.envelope.event_type,
                worker.process_event(event.envelope.event_id),
                worker.process_event(event.envelope.event_id),
            )
            for event in terminal_events
        ]
    finally:
        worker.close()
        close_object_storage(storage)

    with SqlAlchemyOperationUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        operation = uow.operations.get(
            operation_id,
            workspace_id="upload-workspace",
        )
    assert operation is not None
    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        facts = (
            connection.execute(
                text(
                    "SELECT "
                    "(SELECT status FROM assets WHERE id = :asset_id) AS asset_status, "
                    "(SELECT state FROM asset_objects "
                    "WHERE asset_version_id = :asset_version_id "
                    "AND role = 'ORIGINAL') AS source_state, "
                    "(SELECT COUNT(*) FROM asset_objects "
                    "WHERE asset_version_id = :asset_version_id "
                    "AND role = 'CONTROLLED_ORIGINAL') AS controlled_count, "
                    "(SELECT COUNT(*) FROM dead_letter_messages "
                    "WHERE workspace_id = 'upload-workspace' "
                    "AND message_id = :event_id) AS validation_event_dead_letters, "
                    "(SELECT reason FROM dead_letter_messages "
                    "WHERE id = :dead_letter_id) AS operation_dead_letter_reason, "
                    "(SELECT message_id FROM dead_letter_messages "
                    "WHERE id = :dead_letter_id) AS operation_dead_letter_message_id, "
                    "destination_bucket, destination_key "
                    "FROM upload_sessions WHERE id = :upload_session_id"
                ),
                {
                    "asset_id": body["asset"]["id"],
                    "asset_version_id": body["asset_version"]["id"],
                    "dead_letter_id": operation.dead_letter_id,
                    "event_id": validation_event.envelope.event_id,
                    "upload_session_id": session["id"],
                },
            )
            .mappings()
            .one()
        )
        stages = list(
            connection.execute(
                text(
                    "SELECT stage, verdict, reason_code, evidence_json "
                    "FROM asset_validation_results "
                    "WHERE asset_version_id = :asset_version_id "
                    "ORDER BY created_at, id"
                ),
                {"asset_version_id": body["asset_version"]["id"]},
            ).mappings()
        )
    return {
        "asset_version_id": body["asset_version"]["id"],
        "asset_id": body["asset"]["id"],
        "content_calls": content_adapter.calls,
        "destination_bucket": facts["destination_bucket"],
        "destination_key": facts["destination_key"],
        "facts": facts,
        "operation": operation,
        "operation_id": operation_id,
        "provenance_calls": provenance_adapter.calls,
        "source_bucket": source_bucket,
        "source_key": source_key,
        "stages": stages,
        "terminal_deliveries": terminal_deliveries,
        "terminal_event_ids": [event.envelope.event_id for event in terminal_events],
    }


def _assert_no_object_versions(
    minio_client: object,
    *,
    bucket: str,
    key: str,
) -> None:
    versions = minio_client.list_object_versions(  # type: ignore[attr-defined]
        Bucket=bucket,
        Prefix=key,
    )
    matching = [
        entry
        for collection in ("Versions", "DeleteMarkers")
        for entry in versions.get(collection, [])
        if entry["Key"] == key
    ]
    assert matching == []


def _assert_unpromoted_validation_storage(
    result: dict[str, object],
    *,
    minio_client: object,
    expected_source_state: str,
    expected_size: int,
) -> None:
    facts = result["facts"]
    assert facts["controlled_count"] == 0
    assert facts["validation_event_dead_letters"] == 0
    _assert_no_object_versions(
        minio_client,
        bucket=str(result["destination_bucket"]),
        key=str(result["destination_key"]),
    )
    if expected_source_state == "DELETED":
        _assert_no_object_versions(
            minio_client,
            bucket=str(result["source_bucket"]),
            key=str(result["source_key"]),
        )
    else:
        assert (
            minio_client.head_object(  # type: ignore[attr-defined]
                Bucket=result["source_bucket"],
                Key=result["source_key"],
            )["ContentLength"]
            == expected_size
        )


@pytest.mark.parametrize(
    (
        "case_id",
        "asset_kind",
        "filename",
        "declared_mime",
        "content",
        "expected_provider_calls",
    ),
    [
        (
            "success-image",
            "IMAGE",
            "catalog.png",
            "image/png",
            VALID_PNG,
            1,
        ),
        (
            "success-lora",
            "LORA",
            "catalog-style.safetensors",
            "application/x-safetensors",
            _safetensors_fixture(),
            0,
        ),
        (
            "success-prompt",
            "PROMPT_TEMPLATE",
            "catalog.prompt.json",
            "application/json",
            json.dumps(
                {
                    "schema_version": "commercevision.prompt-template.v1",
                    "name": "catalog",
                    "template": "Create {{ product_name }}",
                    "variables": [{"name": "product_name", "required": True}],
                },
                separators=(",", ":"),
            ).encode(),
            0,
        ),
        (
            "success-model",
            "MODEL_CONFIGURATION",
            "catalog.model.json",
            "application/json",
            json.dumps(
                {
                    "schema_version": "commercevision.model-configuration.v1",
                    "provider": "alibaba",
                    "model_id": "wanx-v1",
                    "model_revision": "2026-07-01",
                    "parameters": {"steps": 30},
                },
                separators=(",", ":"),
            ).encode(),
            0,
        ),
    ],
)
def test_real_worker_successfully_validates_and_promotes_every_asset_kind(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
    case_id: str,
    asset_kind: str,
    filename: str,
    declared_mime: str,
    content: bytes,
    expected_provider_calls: int,
) -> None:
    result = _run_validation_worker_matrix_case(
        integration_database=integration_database,
        upload_settings=upload_settings,
        case_id=case_id,
        content=content,
        asset_kind=asset_kind,
        filename=filename,
        declared_mime=declared_mime,
    )

    operation = result["operation"]
    assert operation.state.value == "SUCCEEDED"
    assert operation.attempt_count == 1
    assert operation.dead_letter_id is None
    assert result["facts"]["asset_status"] == "PENDING_RIGHTS"
    assert result["facts"]["source_state"] == "DELETED"
    assert result["facts"]["controlled_count"] == 1
    assert result["content_calls"] == expected_provider_calls
    assert result["provenance_calls"] == expected_provider_calls
    assert [(row["stage"], row["verdict"]) for row in result["stages"]] == [
        ("LOCAL_FORMAT", "PASS"),
        ("MALWARE", "PASS"),
        (
            "CONTENT_SAFETY",
            "PASS" if asset_kind == "IMAGE" else "NOT_APPLICABLE",
        ),
        (
            "PROVENANCE",
            "PASS" if asset_kind == "IMAGE" else "NOT_APPLICABLE",
        ),
        ("PROMOTION", "PASS"),
    ]
    assert result["terminal_deliveries"] == [
        ("asset.validation.completed", "processed", "duplicate")
    ]
    _assert_no_object_versions(
        minio_client,
        bucket=str(result["source_bucket"]),
        key=str(result["source_key"]),
    )
    assert minio_client.head_object(  # type: ignore[attr-defined]
        Bucket=result["destination_bucket"],
        Key=result["destination_key"],
    )["ContentLength"] == len(content)


@pytest.mark.parametrize(
    (
        "case_id",
        "asset_kind",
        "filename",
        "declared_mime",
        "content",
        "strict_image_limit",
        "reason_code",
    ),
    [
        (
            "local-image",
            "IMAGE",
            "pixel.png",
            "image/png",
            VALID_PNG,
            True,
            "IMAGE_DECOMPRESSION_LIMIT",
        ),
        (
            "local-lora",
            "LORA",
            "invalid.safetensors",
            "application/x-safetensors",
            struct.pack("<Q", 2) + b"{}",
            False,
            "MALFORMED_SAFETENSORS",
        ),
        (
            "local-prompt",
            "PROMPT_TEMPLATE",
            "invalid.prompt.json",
            "application/json",
            b'{"schema_version":"wrong"}',
            False,
            "INVALID_PROMPT_TEMPLATE_SCHEMA",
        ),
        (
            "local-model",
            "MODEL_CONFIGURATION",
            "invalid.model.json",
            "application/json",
            b'{"schema_version":"wrong"}',
            False,
            "INVALID_MODEL_CONFIGURATION_SCHEMA",
        ),
    ],
)
def test_real_worker_local_rejection_matrix_never_promotes_or_calls_provider(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
    case_id: str,
    asset_kind: str,
    filename: str,
    declared_mime: str,
    content: bytes,
    strict_image_limit: bool,
    reason_code: str,
) -> None:
    local_validator = (
        _asset_local_validator(
            upload_settings,
            maximum_image_decoded_bytes=1,
        )
        if strict_image_limit
        else None
    )
    result = _run_validation_worker_matrix_case(
        integration_database=integration_database,
        upload_settings=upload_settings,
        case_id=case_id,
        content=content,
        asset_kind=asset_kind,
        filename=filename,
        declared_mime=declared_mime,
        local_validator=local_validator,
    )

    operation = result["operation"]
    assert operation.state.value == "FAILED"
    assert operation.attempt_count == 1
    assert operation.next_attempt_at is None
    assert operation.error is not None
    assert operation.error.code == reason_code
    assert operation.error.retryable is False
    assert result["facts"]["asset_status"] == "BLOCKED"
    assert result["facts"]["source_state"] == "DELETED"
    assert result["content_calls"] == 0
    assert result["provenance_calls"] == 0
    assert [(row["stage"], row["verdict"], row["reason_code"]) for row in result["stages"]] == [
        ("LOCAL_FORMAT", "BLOCK", reason_code)
    ]
    _assert_unpromoted_validation_storage(
        result,
        minio_client=minio_client,
        expected_source_state="DELETED",
        expected_size=len(content),
    )
    assert result["terminal_deliveries"] == [("asset.validation.failed", "processed", "duplicate")]
    assert operation.dead_letter_id is not None
    assert result["facts"]["operation_dead_letter_reason"] == "operation_terminal_failure"
    assert result["facts"]["operation_dead_letter_message_id"] != result["operation_id"]


@pytest.mark.parametrize(
    (
        "outcome",
        "expected_verdict",
        "reason_code",
        "operation_state",
        "asset_status",
        "source_state",
        "retryable",
    ),
    [
        (
            MalwareScanOutcome.INFECTED,
            "BLOCK",
            "MALWARE_DETECTED",
            "FAILED",
            "BLOCKED",
            "DELETED",
            False,
        ),
        (
            MalwareScanOutcome.TIMEOUT,
            "RETRYABLE_FAILURE",
            "MALWARE_SCAN_TIMEOUT",
            "RETRYABLE_FAILED",
            "VALIDATING",
            "QUARANTINED",
            True,
        ),
        (
            MalwareScanOutcome.UNAVAILABLE,
            "RETRYABLE_FAILURE",
            "MALWARE_SCANNER_UNAVAILABLE",
            "RETRYABLE_FAILED",
            "VALIDATING",
            "QUARANTINED",
            True,
        ),
    ],
)
def test_real_worker_malware_failure_matrix_converges_without_provider_use(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
    outcome: MalwareScanOutcome,
    expected_verdict: str,
    reason_code: str,
    operation_state: str,
    asset_status: str,
    source_state: str,
    retryable: bool,
) -> None:
    result = _run_validation_worker_matrix_case(
        integration_database=integration_database,
        upload_settings=upload_settings,
        case_id=f"malware-{outcome.value.lower()}",
        content=VALID_PNG,
        malware_scanner=DeterministicMalwareScanner(outcome=outcome),
    )

    operation = result["operation"]
    assert operation.state.value == operation_state
    assert operation.attempt_count == 1
    assert (operation.next_attempt_at is not None) is retryable
    assert operation.error is not None
    assert operation.error.code == reason_code
    assert operation.error.retryable is retryable
    assert result["facts"]["asset_status"] == asset_status
    assert result["facts"]["source_state"] == source_state
    assert result["content_calls"] == 0
    assert result["provenance_calls"] == 0
    assert [(row["stage"], row["verdict"], row["reason_code"]) for row in result["stages"]] == [
        ("LOCAL_FORMAT", "PASS", None),
        ("MALWARE", expected_verdict, reason_code),
    ]
    _assert_unpromoted_validation_storage(
        result,
        minio_client=minio_client,
        expected_source_state=source_state,
        expected_size=len(VALID_PNG),
    )
    if operation_state == "FAILED":
        assert result["terminal_deliveries"] == [
            ("asset.validation.failed", "processed", "duplicate")
        ]
        assert operation.dead_letter_id is not None
        assert result["facts"]["operation_dead_letter_reason"] == "operation_terminal_failure"
    else:
        assert result["terminal_deliveries"] == []
        assert operation.dead_letter_id is None
        assert result["facts"]["operation_dead_letter_reason"] is None


@pytest.mark.parametrize(
    (
        "outcome",
        "failure_code",
        "expected_verdict",
        "reason_code",
        "operation_state",
        "asset_status",
        "source_state",
        "retryable",
        "provenance_calls",
    ),
    [
        (
            ContentSafetyOutcome.REVIEW,
            None,
            "REVIEW",
            "CONTENT_SAFETY_REVIEW",
            "SUCCEEDED",
            "PENDING_REVIEW",
            "QUARANTINED",
            False,
            1,
        ),
        (
            ContentSafetyOutcome.BLOCK,
            None,
            "BLOCK",
            "CONTENT_SAFETY_BLOCKED",
            "FAILED",
            "BLOCKED",
            "DELETED",
            False,
            0,
        ),
        (
            ContentSafetyOutcome.RETRYABLE_FAILURE,
            "PROVIDER_TIMEOUT",
            "RETRYABLE_FAILURE",
            "PROVIDER_TIMEOUT",
            "RETRYABLE_FAILED",
            "VALIDATING",
            "QUARANTINED",
            True,
            0,
        ),
        (
            ContentSafetyOutcome.TERMINAL_FAILURE,
            "PROVIDER_HTTP_403",
            "TERMINAL_FAILURE",
            "PROVIDER_HTTP_403",
            "FAILED",
            "FAILED",
            "QUARANTINED",
            False,
            0,
        ),
    ],
)
def test_real_worker_content_safety_decision_and_failure_matrix(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
    outcome: ContentSafetyOutcome,
    failure_code: str | None,
    expected_verdict: str,
    reason_code: str,
    operation_state: str,
    asset_status: str,
    source_state: str,
    retryable: bool,
    provenance_calls: int,
) -> None:
    content_adapter = RecordingContentSafetyAdapter(
        outcome=outcome,
        failure_code=failure_code,
    )
    result = _run_validation_worker_matrix_case(
        integration_database=integration_database,
        upload_settings=upload_settings,
        case_id=f"content-{outcome.value.lower()}",
        content=VALID_PNG,
        content_safety=content_adapter,
    )

    operation = result["operation"]
    assert operation.state.value == operation_state
    assert operation.attempt_count == 1
    if operation_state == "SUCCEEDED":
        assert operation.error is None
        assert operation.next_attempt_at is None
    else:
        assert operation.error is not None
        assert operation.error.code == reason_code
        assert operation.error.retryable is retryable
        assert (operation.next_attempt_at is not None) is retryable
    assert result["facts"]["asset_status"] == asset_status
    assert result["facts"]["source_state"] == source_state
    assert result["content_calls"] == 1
    assert result["provenance_calls"] == provenance_calls
    stages = [(row["stage"], row["verdict"], row["reason_code"]) for row in result["stages"]]
    expected_stages = [
        ("LOCAL_FORMAT", "PASS", None),
        ("MALWARE", "PASS", None),
        ("CONTENT_SAFETY", expected_verdict, reason_code),
    ]
    if outcome == ContentSafetyOutcome.REVIEW:
        expected_stages.append(("PROVENANCE", "PASS", None))
    assert stages == expected_stages
    content_evidence = result["stages"][2]["evidence_json"]
    if isinstance(content_evidence, str):
        content_evidence = json.loads(content_evidence)
    assert content_evidence["transfer_external"] is False
    assert content_evidence["transfer_authorized"] is False
    assert content_evidence["transfer_provider"] == "deterministic-local"
    _assert_unpromoted_validation_storage(
        result,
        minio_client=minio_client,
        expected_source_state=source_state,
        expected_size=len(VALID_PNG),
    )
    if operation_state == "FAILED":
        assert result["terminal_deliveries"] == [
            ("asset.validation.failed", "processed", "duplicate")
        ]
        assert operation.dead_letter_id is not None
        assert result["facts"]["operation_dead_letter_reason"] == "operation_terminal_failure"
    elif operation_state == "SUCCEEDED":
        assert result["terminal_deliveries"] == [
            ("asset.validation.completed", "processed", "duplicate")
        ]
        assert operation.dead_letter_id is None
    else:
        assert result["terminal_deliveries"] == []
        assert operation.dead_letter_id is None


@pytest.mark.parametrize(
    (
        "status",
        "failure_code",
        "expected_verdict",
        "reason_code",
        "operation_state",
        "asset_status",
        "retryable",
    ),
    [
        (
            ProvenanceEvidenceStatus.CONFLICTING,
            None,
            "REVIEW",
            "PROVENANCE_CONFLICTING",
            "SUCCEEDED",
            "PENDING_REVIEW",
            False,
        ),
        (
            None,
            "C2PA_PROCESS_TIMEOUT",
            "RETRYABLE_FAILURE",
            "C2PA_PROCESS_TIMEOUT",
            "RETRYABLE_FAILED",
            "VALIDATING",
            True,
        ),
    ],
)
def test_real_worker_provenance_review_and_transient_failure_matrix(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
    status: ProvenanceEvidenceStatus | None,
    failure_code: str | None,
    expected_verdict: str,
    reason_code: str,
    operation_state: str,
    asset_status: str,
    retryable: bool,
) -> None:
    provenance_adapter = RecordingProvenanceAdapter(
        status=status,
        failure_code=failure_code,
    )
    result = _run_validation_worker_matrix_case(
        integration_database=integration_database,
        upload_settings=upload_settings,
        case_id=(
            f"provenance-{status.value.lower()}" if status is not None else "provenance-transient"
        ),
        content=VALID_PNG,
        provenance=provenance_adapter,
    )

    operation = result["operation"]
    assert operation.state.value == operation_state
    assert operation.attempt_count == 1
    if operation_state == "SUCCEEDED":
        assert operation.error is None
        assert operation.next_attempt_at is None
    else:
        assert operation.error is not None
        assert operation.error.code == reason_code
        assert operation.error.retryable is retryable
        assert operation.next_attempt_at is not None
    assert result["facts"]["asset_status"] == asset_status
    assert result["facts"]["source_state"] == "QUARANTINED"
    assert result["content_calls"] == 1
    assert result["provenance_calls"] == 1
    assert [(row["stage"], row["verdict"], row["reason_code"]) for row in result["stages"]] == [
        ("LOCAL_FORMAT", "PASS", None),
        ("MALWARE", "PASS", None),
        ("CONTENT_SAFETY", "PASS", None),
        ("PROVENANCE", expected_verdict, reason_code),
    ]
    provenance_evidence = result["stages"][3]["evidence_json"]
    if isinstance(provenance_evidence, str):
        provenance_evidence = json.loads(provenance_evidence)
    assert provenance_evidence["remote_manifest_fetch"] is False
    assert provenance_evidence["failure_code"] == failure_code
    assert "raw" not in json.dumps(provenance_evidence, sort_keys=True).lower()
    _assert_unpromoted_validation_storage(
        result,
        minio_client=minio_client,
        expected_source_state="QUARANTINED",
        expected_size=len(VALID_PNG),
    )
