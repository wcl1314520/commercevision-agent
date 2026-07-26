"""Durable, append-only validation and promotion for quarantined Asset Versions."""

from __future__ import annotations

import hashlib
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import BinaryIO, Protocol

from commercevision_contracts.object_storage import (
    BoundedReadRequest,
    ObjectReference,
    ObjectStorage,
    TemporaryReadRequest,
)
from commercevision_contracts.validation import (
    ContentSafetyConfiguredIdentity,
    ContentSafetyImageRequest,
    ContentSafetyOutcome,
    ContentSafetyResult,
    MalwareScanOutcome,
    MalwareScanResult,
    ProvenanceConfiguredIdentity,
    ProvenanceEvidenceStatus,
    ProvenanceVerificationOutcome,
    ProvenanceVerificationResult,
)
from commercevision_domain import (
    AssetKind,
    AssetObject,
    AssetState,
    AssetValidationResult,
    AssetVersion,
    NormalizedOperationError,
    ObjectMismatchError,
    ReconciliationOutcome,
    StoragePreconditionError,
    StorageUnavailableError,
    UploadObjectMissingError,
    ValidationStage,
    ValidationVerdict,
)

from .asset_local_validation import (
    AssetLocalValidationError,
    AssetLocalValidationRequest,
    AssetLocalValidator,
)
from .asset_ports import AssetUnitOfWorkFactory
from .asset_promotion import UploadPromoter
from .asset_validation_evidence import (
    AssetValidationEvidenceError,
    AssetValidationEvidenceStore,
    assert_source_evidence_identity,
)
from .asset_validation_identity import (
    NON_IMAGE_CONTENT_SAFETY_IDENTITY,
    NON_IMAGE_PROVENANCE_IDENTITY,
    assert_content_safety_stage_identity,
    assert_provenance_stage_identity,
)
from .asset_validation_lifecycle import (
    AssetValidationLifecycleCoordinator,
    AssetValidationLifecycleError,
)
from .asset_validation_observability import (
    AssetValidationObserver,
    NullAssetValidationObserver,
)
from .asset_validation_promotion import (
    AssetValidationPromotionCoordinator,
    AssetValidationPromotionError,
)
from .asset_validation_retention import (
    AssetValidationRetentionCoordinator,
    AssetValidationRetentionError,
)
from .asset_validation_target import (
    AssetValidationTarget,
    AssetValidationTargetBinder,
    AssetValidationTargetError,
)
from .asset_validation_transfer import (
    SECURITY_VALIDATION_PURPOSE,
    ValidationDataTransferAuthorization,
    ValidationDataTransferDenied,
    ValidationDataTransferPolicy,
)
from .operations import (
    OperationExecutionFailure,
    OperationExecutionRequest,
    OperationExecutionResult,
    OperationReconciliationResult,
)

_LOCAL_VALIDATOR = "commercevision-local"
_LOCAL_VALIDATOR_VERSION = "asset-local-validator-v1"


class MalwareScanner(Protocol):
    def identity(self) -> str: ...

    def scan(
        self,
        chunks: Iterable[bytes],
        *,
        content_length: int,
    ) -> MalwareScanResult: ...


class ContentSafetyAdapter(Protocol):
    @property
    def configured_identity(self) -> ContentSafetyConfiguredIdentity: ...

    def moderate(self, request: ContentSafetyImageRequest) -> ContentSafetyResult: ...


class ProvenanceAdapter(Protocol):
    @property
    def configured_identity(self) -> ProvenanceConfiguredIdentity: ...

    def verify(
        self,
        *,
        mime_type: str,
        stream: BinaryIO,
        byte_length: int,
    ) -> ProvenanceVerificationResult: ...


class ContentSafetyRequestFactory(Protocol):
    external_transfer: bool
    transfer_provider: str
    transfer_endpoint_region: str

    def __call__(
        self,
        *,
        asset_version: AssetVersion,
        object_fact: AssetObject,
        expires_at: datetime,
    ) -> ContentSafetyImageRequest: ...


class PresignedContentSafetyRequestFactory:
    """Issue a bounded internal read reference for a remote moderation provider."""

    external_transfer = True

    def __init__(
        self,
        storage: ObjectStorage,
        *,
        provider: str = "alibaba-green",
        endpoint_region: str = "unspecified",
    ) -> None:
        self._storage = storage
        self.transfer_provider = provider
        self.transfer_endpoint_region = endpoint_region

    def __call__(
        self,
        *,
        asset_version: AssetVersion,
        object_fact: AssetObject,
        expires_at: datetime,
    ) -> ContentSafetyImageRequest:
        reference = ObjectReference(
            location=object_fact.location,
            key=object_fact.key,
            version_id=object_fact.provider_version_id,
        )
        read = self._storage.temporary_read(
            TemporaryReadRequest(
                reference=reference,
                expires_at=expires_at,
                expected_etag=object_fact.etag,
            )
        )
        if read.method != "GET" or read.required_headers:
            raise StoragePreconditionError(
                "content-safety provider reference requires unsupported headers"
            )
        return ContentSafetyImageRequest(
            data_id=asset_version.id,
            content_sha256=asset_version.sha256,
            image_url=read.url,
            image_url_expires_at=read.expires_at,
            controlled_reference_id=f"asset-validation:{asset_version.id}",
        )


class DeterministicContentSafetyRequestFactory:
    """Create a non-fetching controlled request for the deterministic Adapter."""

    external_transfer = False
    transfer_provider = "deterministic-local"
    transfer_endpoint_region = "local"

    def __call__(
        self,
        *,
        asset_version: AssetVersion,
        object_fact: AssetObject,
        expires_at: datetime,
    ) -> ContentSafetyImageRequest:
        del object_fact
        return ContentSafetyImageRequest(
            data_id=asset_version.id,
            content_sha256=asset_version.sha256,
            image_url=f"https://deterministic.invalid/assets/{asset_version.id}",
            image_url_expires_at=expires_at,
            controlled_reference_id=f"asset-validation:{asset_version.id}",
        )


@dataclass(frozen=True, slots=True)
class AssetValidationExecutorPolicy:
    content_reference_lifetime: timedelta = timedelta(seconds=60)
    content_reference_minimum_validity: timedelta = timedelta(seconds=20)
    stream_chunk_bytes: int = 64 * 1024
    spool_memory_bytes: int = 1024 * 1024

    def __post_init__(self) -> None:
        if self.content_reference_lifetime <= timedelta(0):
            raise ValueError("content-safety reference lifetime must be positive")
        if self.content_reference_minimum_validity <= timedelta(0):
            raise ValueError("content-safety minimum reference validity must be positive")
        if self.content_reference_minimum_validity > self.content_reference_lifetime:
            raise ValueError("content-safety minimum validity cannot exceed reference lifetime")
        if self.stream_chunk_bytes < 1024:
            raise ValueError("validation stream chunks must be at least 1024 bytes")
        if self.spool_memory_bytes < self.stream_chunk_bytes:
            raise ValueError("validation spool memory must cover one stream chunk")


class AssetValidationExecutor:
    """Converge one Durable Operation through validation and verified promotion."""

    def __init__(
        self,
        *,
        uow_factory: AssetUnitOfWorkFactory,
        storage: ObjectStorage,
        local_validator: AssetLocalValidator,
        malware_scanner: MalwareScanner,
        content_safety: ContentSafetyAdapter,
        content_safety_request_factory: ContentSafetyRequestFactory,
        provenance: ProvenanceAdapter,
        promoter: UploadPromoter,
        validation_transfer_policy: ValidationDataTransferPolicy | None = None,
        observer: AssetValidationObserver | None = None,
        policy: AssetValidationExecutorPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._observer = observer or NullAssetValidationObserver()
        self._target_binder = AssetValidationTargetBinder(uow_factory=uow_factory)
        self._evidence = AssetValidationEvidenceStore(uow_factory=uow_factory)
        self._retention = AssetValidationRetentionCoordinator(
            uow_factory=uow_factory,
            promoter=promoter,
            clock=self._clock,
        )
        self._promotion = AssetValidationPromotionCoordinator(
            uow_factory=uow_factory,
            promoter=promoter,
            retention=self._retention,
            clock=self._clock,
        )
        self._lifecycle = AssetValidationLifecycleCoordinator(
            uow_factory=uow_factory,
            storage=storage,
            retention=self._retention,
            clock=self._clock,
        )
        self._storage = storage
        self._local_validator = local_validator
        self._malware_scanner = malware_scanner
        self._content_safety = content_safety
        self._content_safety_request_factory = content_safety_request_factory
        self._provenance = provenance
        self._validation_transfer_policy = (
            validation_transfer_policy or ValidationDataTransferPolicy.deny_all()
        )
        self._policy = policy or AssetValidationExecutorPolicy()

    def execute(self, request: OperationExecutionRequest) -> OperationExecutionResult:
        with self._observer.operation(request=request, mode="execute"):
            self._converge(request)
        return OperationExecutionResult(
            operation_id=request.operation_id,
            output_ref=(f"mysql://asset-versions/{request.target_id}/validation-results"),
        )

    def record_terminal_failure(
        self,
        request: OperationExecutionRequest,
        error: NormalizedOperationError,
    ) -> None:
        self._lifecycle.record_terminal_failure(request, error)

    def reconcile(
        self,
        request: OperationExecutionRequest,
    ) -> OperationReconciliationResult:
        try:
            with self._observer.operation(request=request, mode="reconcile"):
                known_failure = self._confirmed_attempt_failure(request)
                if known_failure is not None:
                    error, retry_at = self._operation_failure_from_result(known_failure)
                    return OperationReconciliationResult(
                        operation_id=request.operation_id,
                        outcome=ReconciliationOutcome.CONFIRMED_FAILURE,
                        error=error,
                        retry_at=retry_at,
                        provider_request_id=error.provider_request_id,
                    )
                self._converge(request)
        except OperationExecutionFailure as exc:
            if exc.error.retryable:
                return OperationReconciliationResult(
                    operation_id=request.operation_id,
                    outcome=ReconciliationOutcome.PENDING,
                    error=exc.error,
                    retry_at=exc.retry_at,
                    provider_request_id=exc.error.provider_request_id,
                )
            return OperationReconciliationResult(
                operation_id=request.operation_id,
                outcome=ReconciliationOutcome.CONFIRMED_FAILURE,
                error=exc.error,
                provider_request_id=exc.error.provider_request_id,
            )
        return OperationReconciliationResult(
            operation_id=request.operation_id,
            outcome=ReconciliationOutcome.CONFIRMED_SUCCESS,
            output_ref=(f"mysql://asset-versions/{request.target_id}/validation-results"),
        )

    def _confirmed_attempt_failure(
        self,
        request: OperationExecutionRequest,
    ) -> AssetValidationResult | None:
        target = self._load_target(request)
        self._guard_retention(target)
        for stage, result in self._attempt_results(request).items():
            if (
                result.attempt_number != request.attempt_count
                or result.verdict != ValidationVerdict.RETRYABLE_FAILURE
            ):
                continue
            try:
                assert_source_evidence_identity(
                    target=target,
                    request=request,
                    result=result,
                    expected_stage=stage,
                )
            except AssetValidationEvidenceError as exc:
                raise self._evidence_failure(exc) from exc
            return result
        return None

    def _operation_failure_from_result(
        self,
        result: AssetValidationResult,
    ) -> tuple[NormalizedOperationError, datetime | None]:
        evidence = result.evidence_dict()
        provider_request_id = evidence.get("request_id")
        retry_after_seconds = evidence.get("retry_after_seconds")
        return (
            NormalizedOperationError(
                code=result.reason_code or "VALIDATION_RETRYABLE_FAILURE",
                category=result.stage.value.lower(),
                message="validation dependency is temporarily unavailable",
                retryable=True,
                provider_request_id=(
                    provider_request_id if isinstance(provider_request_id, str) else None
                ),
            ),
            (
                result.created_at + timedelta(seconds=retry_after_seconds)
                if isinstance(retry_after_seconds, int) and retry_after_seconds >= 0
                else None
            ),
        )

    def _converge(self, request: OperationExecutionRequest) -> None:
        target = self._load_target(request)
        self._observer.target_bound(request=request, target=target)
        self._guard_retention(target)
        if target.asset.status in {AssetState.DELETING, AssetState.DELETED}:
            raise self._terminal(
                "ASSET_DELETION_IN_PROGRESS",
                "Asset validation cannot continue while deletion is in progress",
                category="retention",
            )
        if target.asset.status == AssetState.BLOCKED:
            self._cleanup_rejected(
                target,
                reason_code=target.asset.block_reason or "BLOCKED",
            )
        if target.asset.status == AssetState.PENDING_RIGHTS:
            self._promote(request, target)
            self._observer.completed(
                request=request,
                target=target,
                outcome="PENDING_RIGHTS",
            )
            return
        if target.asset.status == AssetState.PENDING_REVIEW:
            self._observer.completed(
                request=request,
                target=target,
                outcome="PENDING_REVIEW",
            )
            return
        self._begin_validation(target)
        target = self._load_target(request)
        self._guard_retention(target)
        existing = self._attempt_results(request)

        with tempfile.SpooledTemporaryFile(max_size=self._policy.spool_memory_bytes) as spool:
            stream: BinaryIO | None = None
            if self._needs_stream(target, existing):
                self._download_verified(request, target, spool)
                stream = spool

            local = existing.get(ValidationStage.LOCAL_FORMAT)
            local_reused = local is not None
            with self._observer.stage(
                request=request,
                target=target,
                stage=ValidationStage.LOCAL_FORMAT,
                reused=local_reused,
            ):
                if local is None:
                    assert stream is not None
                    local = self._run_local(request, target, stream)
                else:
                    self._observer.result(result=local, reused=True)
                self._enforce_stage_result(
                    request,
                    target,
                    local,
                    expected_stage=ValidationStage.LOCAL_FORMAT,
                )

            malware = existing.get(ValidationStage.MALWARE)
            malware_reused = malware is not None
            with self._observer.stage(
                request=request,
                target=target,
                stage=ValidationStage.MALWARE,
                reused=malware_reused,
            ):
                if malware is None:
                    assert stream is not None
                    malware = self._run_malware(request, target, stream)
                else:
                    self._observer.result(result=malware, reused=True)
                self._enforce_stage_result(
                    request,
                    target,
                    malware,
                    expected_stage=ValidationStage.MALWARE,
                )

            content = existing.get(ValidationStage.CONTENT_SAFETY)
            content_reused = content is not None
            with self._observer.stage(
                request=request,
                target=target,
                stage=ValidationStage.CONTENT_SAFETY,
                reused=content_reused,
            ):
                if content is None:
                    content = self._run_content_safety(request, target)
                else:
                    self._observer.result(result=content, reused=True)
                self._enforce_stage_result(
                    request,
                    target,
                    content,
                    expected_stage=ValidationStage.CONTENT_SAFETY,
                )

            provenance = existing.get(ValidationStage.PROVENANCE)
            provenance_reused = provenance is not None
            with self._observer.stage(
                request=request,
                target=target,
                stage=ValidationStage.PROVENANCE,
                reused=provenance_reused,
            ):
                if provenance is None:
                    provenance = self._run_provenance(request, target, stream)
                else:
                    self._observer.result(result=provenance, reused=True)
                self._enforce_stage_result(
                    request,
                    target,
                    provenance,
                    expected_stage=ValidationStage.PROVENANCE,
                )

            if (
                content.verdict == ValidationVerdict.REVIEW
                or provenance.verdict == ValidationVerdict.REVIEW
            ):
                self._mark_pending_review(
                    request,
                    target,
                    reason_code=content.reason_code or provenance.reason_code,
                )
                self._observer.completed(
                    request=request,
                    target=target,
                    outcome="PENDING_REVIEW",
                )
                return

        self._promote(request, target)
        self._observer.completed(
            request=request,
            target=target,
            outcome="PENDING_RIGHTS",
        )

    def _load_target(
        self,
        request: OperationExecutionRequest,
    ) -> AssetValidationTarget:
        try:
            return self._target_binder.load(request)
        except AssetValidationTargetError as exc:
            raise self._terminal(
                exc.code,
                exc.message,
                category=exc.category,
            ) from exc

    def _begin_validation(self, target: AssetValidationTarget) -> None:
        with self._uow_factory() as uow:
            asset = uow.assets.get(
                workspace_id=target.asset.workspace_id,
                asset_id=target.asset.id,
                for_update=True,
            )
            if asset is None:
                raise self._terminal(
                    "VALIDATION_TARGET_NOT_FOUND",
                    "validation Asset is unavailable",
                )
            if asset.status == AssetState.QUARANTINED:
                asset.begin_validation(now=self._clock())
                uow.assets.save_asset(asset)
                uow.commit()
            elif asset.status == AssetState.FAILED:
                asset.resume_failed_validation(now=self._clock())
                uow.assets.save_asset(asset)
                uow.commit()
            elif asset.status not in {
                AssetState.VALIDATING,
                AssetState.PENDING_RIGHTS,
                AssetState.PENDING_REVIEW,
            }:
                raise self._terminal(
                    "INVALID_ASSET_STATE",
                    "Asset cannot enter validation from its current state",
                )

    def _attempt_results(
        self,
        request: OperationExecutionRequest,
    ) -> dict[ValidationStage, AssetValidationResult]:
        try:
            return self._evidence.attempt_results(request)
        except AssetValidationEvidenceError as exc:
            raise self._evidence_failure(exc) from exc

    @staticmethod
    def _needs_stream(
        target: AssetValidationTarget,
        existing: dict[ValidationStage, AssetValidationResult],
    ) -> bool:
        if ValidationStage.LOCAL_FORMAT not in existing:
            return True
        if ValidationStage.MALWARE not in existing:
            return True
        return target.asset.kind == AssetKind.IMAGE and ValidationStage.PROVENANCE not in existing

    def _download_verified(
        self,
        request: OperationExecutionRequest,
        target: AssetValidationTarget,
        stream: BinaryIO,
    ) -> None:
        source = target.source_object
        reference = ObjectReference(
            location=source.location,
            key=source.key,
            version_id=source.provider_version_id,
        )
        try:
            stat = self._storage.stat(reference)
            if (
                stat.backend != source.backend
                or stat.bucket != source.bucket
                or stat.reference != reference
                or stat.etag != source.etag
                or stat.content_length != source.byte_size
                or stat.content_length != target.asset_version.byte_size
            ):
                raise ObjectMismatchError("quarantine object identity changed before validation")
            digest = hashlib.sha256()
            byte_size = 0
            with self._storage.open_bounded_read(
                BoundedReadRequest(
                    reference=reference,
                    maximum_bytes=source.byte_size,
                    expected_etag=source.etag,
                )
            ) as chunks:
                for chunk in chunks:
                    byte_size += len(chunk)
                    if byte_size > source.byte_size:
                        raise ObjectMismatchError(
                            "quarantine object exceeded its immutable byte size"
                        )
                    digest.update(chunk)
                    stream.write(chunk)
            if (
                byte_size != source.byte_size
                or digest.hexdigest() != source.sha256
                or source.sha256 != target.asset_version.sha256
            ):
                raise ObjectMismatchError(
                    "quarantine object content identity changed before validation"
                )
            stream.seek(0)
        except StorageUnavailableError as exc:
            raise self._retryable(
                "VALIDATION_STORAGE_UNAVAILABLE",
                "validation object storage is temporarily unavailable",
            ) from exc
        except UploadObjectMissingError as exc:
            raise self._terminal(
                "VALIDATION_OBJECT_MISSING",
                "quarantined validation object is missing",
            ) from exc
        except (ObjectMismatchError, StoragePreconditionError) as exc:
            self._reject(
                request,
                target,
                reason_code="VALIDATION_OBJECT_MISMATCH",
            )
            raise self._terminal(
                "VALIDATION_OBJECT_MISMATCH",
                "quarantined validation object no longer matches its Asset Version",
            ) from exc

    def _run_local(
        self,
        request: OperationExecutionRequest,
        target: AssetValidationTarget,
        stream: BinaryIO,
    ) -> AssetValidationResult:
        local_request = AssetLocalValidationRequest(
            asset_kind=target.asset.kind,
            filename=target.asset_version.filename,
            declared_mime=target.asset_version.declared_mime,
            byte_size=target.asset_version.byte_size,
        )
        try:
            stream.seek(0)
            local = self._local_validator.validate(local_request, stream)
        except AssetLocalValidationError as exc:
            self._guard_retention(target)
            result = self._new_result(
                request=request,
                target=target,
                stage=ValidationStage.LOCAL_FORMAT,
                validator_name=_LOCAL_VALIDATOR,
                validator_version=_LOCAL_VALIDATOR_VERSION,
                verdict=ValidationVerdict.BLOCK,
                reason_code=exc.code,
                evidence={
                    "asset_kind": target.asset.kind.value,
                    "byte_size": target.asset_version.byte_size,
                },
            )
            return self._append_result(result)
        self._guard_retention(target)
        result = self._new_result(
            request=request,
            target=target,
            stage=ValidationStage.LOCAL_FORMAT,
            validator_name=_LOCAL_VALIDATOR,
            validator_version=_LOCAL_VALIDATOR_VERSION,
            verdict=ValidationVerdict.PASS,
            reason_code=None,
            evidence={
                "asset_kind": target.asset.kind.value,
                "byte_size": target.asset_version.byte_size,
                "detected_mime": local.detected_mime,
                "format_name": local.format_name,
                "facts": local.facts,
            },
        )
        return self._append_result(result)

    def _run_malware(
        self,
        request: OperationExecutionRequest,
        target: AssetValidationTarget,
        stream: BinaryIO,
    ) -> AssetValidationResult:
        self._guard_retention(target)
        stream.seek(0)

        def chunks() -> Iterable[bytes]:
            while chunk := stream.read(self._policy.stream_chunk_bytes):
                yield chunk

        result = self._malware_scanner.scan(
            chunks(),
            content_length=target.asset_version.byte_size,
        )
        self._guard_retention(target)
        if result.outcome == MalwareScanOutcome.CLEAN:
            verdict = ValidationVerdict.PASS
            reason = None
        elif result.outcome == MalwareScanOutcome.INFECTED:
            verdict = ValidationVerdict.BLOCK
            reason = "MALWARE_DETECTED"
        else:
            verdict = ValidationVerdict.RETRYABLE_FAILURE
            reason = (
                "MALWARE_SCAN_TIMEOUT"
                if result.outcome == MalwareScanOutcome.TIMEOUT
                else "MALWARE_SCANNER_UNAVAILABLE"
            )
        evidence = {
            "asset_kind": target.asset.kind.value,
            "latency_ms": result.latency_ms,
            "outcome": result.outcome.value,
            "scanner_version": result.scanner_version,
            "signature": result.signature,
        }
        validation_result = self._new_result(
            request=request,
            target=target,
            stage=ValidationStage.MALWARE,
            validator_name="clamav",
            validator_version=result.scanner_version or "clamav-unavailable",
            verdict=verdict,
            reason_code=reason,
            evidence=evidence,
        )
        return self._append_result(validation_result)

    def _run_content_safety(
        self,
        request: OperationExecutionRequest,
        target: AssetValidationTarget,
    ) -> AssetValidationResult:
        self._guard_retention(target)
        if target.asset.kind != AssetKind.IMAGE:
            identity = NON_IMAGE_CONTENT_SAFETY_IDENTITY
            return self._append_result(
                self._new_result(
                    request=request,
                    target=target,
                    stage=ValidationStage.CONTENT_SAFETY,
                    validator_name=identity.provider,
                    validator_version=identity.sdk_version,
                    verdict=ValidationVerdict.NOT_APPLICABLE,
                    reason_code=None,
                    evidence={
                        "asset_kind": target.asset.kind.value,
                        "endpoint": identity.endpoint,
                        "mapping_version": identity.mapping_version,
                        "outcome": ValidationVerdict.NOT_APPLICABLE.value,
                        "policy_version": identity.policy_version,
                        "provider": identity.provider,
                        "sdk_version": identity.sdk_version,
                        "service": identity.service,
                        **self._content_transfer_evidence(
                            target=target,
                            authorization=None,
                            external_transfer=False,
                        ),
                    },
                )
            )
        authorization: ValidationDataTransferAuthorization | None = None
        try:
            decision_at = self._guard_retention(target)
            reference_expires_at = decision_at + self._policy.content_reference_lifetime
            if target.asset.retention_deadline is not None:
                reference_expires_at = min(
                    reference_expires_at,
                    target.asset.retention_deadline,
                )
            external_transfer = getattr(
                self._content_safety_request_factory,
                "external_transfer",
                True,
            )
            if (
                external_transfer
                and reference_expires_at - decision_at
                < self._policy.content_reference_minimum_validity
            ):
                raise self._terminal(
                    "CONTENT_SAFETY_RETENTION_WINDOW_INSUFFICIENT",
                    "remaining Asset retention cannot cover the provider deadline",
                    category="retention",
                )
            if external_transfer:
                authorization = self._authorize_content_transfer(
                    request=request,
                    target=target,
                )
            provider_request = self._content_safety_request_factory(
                asset_version=target.asset_version,
                object_fact=target.source_object,
                expires_at=reference_expires_at,
            )
            dispatch_at = self._guard_retention(target)
            if (
                provider_request.image_url_expires_at > reference_expires_at
                or target.asset.retention_deadline is not None
                and provider_request.image_url_expires_at > target.asset.retention_deadline
            ):
                raise self._terminal(
                    "CONTENT_SAFETY_REFERENCE_BOUNDARY_MISMATCH",
                    "content-safety reference exceeds the authorized retention boundary",
                    category="integrity",
                )
            if (
                external_transfer
                and provider_request.image_url_expires_at - dispatch_at
                < self._policy.content_reference_minimum_validity
            ):
                raise self._terminal(
                    "CONTENT_SAFETY_RETENTION_WINDOW_INSUFFICIENT",
                    "remaining Asset retention cannot cover the provider deadline",
                    category="retention",
                )
            if external_transfer:
                authorization = self._authorize_content_transfer(
                    request=request,
                    target=target,
                )
            result = self._content_safety.moderate(provider_request)
            self._guard_retention(target)
        except (StorageUnavailableError, TimeoutError) as exc:
            raise self._retryable(
                "CONTENT_SAFETY_REFERENCE_UNAVAILABLE",
                "content-safety reference is temporarily unavailable",
            ) from exc
        if result.outcome == ContentSafetyOutcome.PASS:
            verdict = ValidationVerdict.PASS
            reason = None
        elif result.outcome == ContentSafetyOutcome.REVIEW:
            verdict = ValidationVerdict.REVIEW
            reason = "CONTENT_SAFETY_REVIEW"
        elif result.outcome == ContentSafetyOutcome.BLOCK:
            verdict = ValidationVerdict.BLOCK
            reason = "CONTENT_SAFETY_BLOCKED"
        elif result.outcome == ContentSafetyOutcome.TERMINAL_FAILURE:
            verdict = ValidationVerdict.TERMINAL_FAILURE
            reason = result.failure_code or "CONTENT_SAFETY_PROVIDER_REJECTED"
        else:
            verdict = ValidationVerdict.RETRYABLE_FAILURE
            reason = result.failure_code or "CONTENT_SAFETY_UNAVAILABLE"
        evidence = {
            "asset_kind": target.asset.kind.value,
            "endpoint": result.endpoint,
            "failure_code": result.failure_code,
            "labels": [
                {"code": label.code, "confidence": label.confidence} for label in result.labels
            ],
            "latency_ms": result.latency_ms,
            "mapping_version": result.mapping_version,
            "outcome": result.outcome.value,
            "policy_version": result.policy_version,
            "provider": result.provider,
            "request_id": result.request_id,
            "retry_after_seconds": result.retry_after_seconds,
            "risk_level": result.risk_level,
            "sdk_version": result.sdk_version,
            "service": result.service,
            **self._content_transfer_evidence(
                target=target,
                authorization=authorization,
                external_transfer=external_transfer,
            ),
        }
        validation_result = self._new_result(
            request=request,
            target=target,
            stage=ValidationStage.CONTENT_SAFETY,
            validator_name=result.provider,
            validator_version=result.sdk_version,
            verdict=verdict,
            reason_code=reason,
            evidence=evidence,
        )
        persisted = self._append_result(validation_result)
        if verdict == ValidationVerdict.RETRYABLE_FAILURE:
            retry_at = (
                self._clock() + timedelta(seconds=result.retry_after_seconds)
                if result.retry_after_seconds is not None
                else None
            )
            raise OperationExecutionFailure(
                NormalizedOperationError(
                    code=reason,
                    category="content_safety",
                    message="content-safety provider is temporarily unavailable",
                    retryable=True,
                    provider_request_id=result.request_id,
                ),
                retry_at=retry_at,
            )
        if verdict == ValidationVerdict.TERMINAL_FAILURE:
            raise OperationExecutionFailure(
                NormalizedOperationError(
                    code=reason,
                    category="content_safety",
                    message="content-safety provider permanently rejected the validation request",
                    retryable=False,
                    provider_request_id=result.request_id,
                )
            )
        return persisted

    def _authorize_content_transfer(
        self,
        *,
        request: OperationExecutionRequest,
        target: AssetValidationTarget,
    ) -> ValidationDataTransferAuthorization:
        provider = getattr(
            self._content_safety_request_factory,
            "transfer_provider",
            "",
        )
        endpoint_region = getattr(
            self._content_safety_request_factory,
            "transfer_endpoint_region",
            "",
        )
        endpoint_host = self._content_safety.configured_identity.endpoint
        try:
            return self._validation_transfer_policy.authorize(
                persisted_policy_version=(target.asset_version.validation_transfer_policy_version),
                persisted_policy_snapshot_sha256=(
                    target.asset_version.validation_transfer_policy_snapshot_sha256
                ),
                workspace_id=target.asset.workspace_id,
                asset_version_id=target.asset_version.id,
                asset_kind=target.asset.kind,
                retention_class=target.asset.retention_class,
                provider=provider,
                endpoint_region=endpoint_region,
                endpoint_host=endpoint_host,
                purpose=SECURITY_VALIDATION_PURPOSE,
            )
        except ValidationDataTransferDenied as exc:
            denial = self._new_result(
                request=request,
                target=target,
                stage=ValidationStage.CONTENT_SAFETY,
                validator_name="validation-transfer-policy",
                validator_version=self._validation_transfer_policy.version,
                verdict=ValidationVerdict.TERMINAL_FAILURE,
                reason_code=exc.code,
                evidence={
                    "asset_kind": target.asset.kind.value,
                    "endpoint": endpoint_region,
                    "failure_code": exc.code,
                    "labels": [],
                    "latency_ms": 0,
                    "mapping_version": "validation-transfer-v1",
                    "outcome": ValidationVerdict.TERMINAL_FAILURE.value,
                    "policy_version": target.asset_version.validation_policy_version,
                    "provider": provider or "unconfigured",
                    "request_id": None,
                    "retry_after_seconds": None,
                    "risk_level": None,
                    "sdk_version": "not-dispatched",
                    "service": SECURITY_VALIDATION_PURPOSE,
                    **self._content_transfer_evidence(
                        target=target,
                        authorization=None,
                        external_transfer=True,
                    ),
                },
            )
            self._append_result(denial)
            raise self._terminal(
                exc.code,
                exc.message,
                category="validation_transfer",
            ) from exc

    def _content_transfer_evidence(
        self,
        *,
        target: AssetValidationTarget,
        authorization: ValidationDataTransferAuthorization | None,
        external_transfer: bool,
    ) -> dict[str, object]:
        return {
            "transfer_authorized": authorization is not None,
            "transfer_endpoint_region": getattr(
                self._content_safety_request_factory,
                "transfer_endpoint_region",
                "local",
            ),
            "transfer_endpoint_host": self._content_safety.configured_identity.endpoint,
            "transfer_external": external_transfer,
            "transfer_policy_snapshot_sha256": (
                target.asset_version.validation_transfer_policy_snapshot_sha256
            ),
            "transfer_policy_version": (target.asset_version.validation_transfer_policy_version),
            "transfer_provider": getattr(
                self._content_safety_request_factory,
                "transfer_provider",
                "deterministic-local",
            ),
            "transfer_purpose": SECURITY_VALIDATION_PURPOSE,
        }

    def _run_provenance(
        self,
        request: OperationExecutionRequest,
        target: AssetValidationTarget,
        stream: BinaryIO | None,
    ) -> AssetValidationResult:
        self._guard_retention(target)
        if target.asset.kind != AssetKind.IMAGE:
            identity = NON_IMAGE_PROVENANCE_IDENTITY
            return self._append_result(
                self._new_result(
                    request=request,
                    target=target,
                    stage=ValidationStage.PROVENANCE,
                    validator_name=identity.validator,
                    validator_version=identity.sdk_version,
                    verdict=ValidationVerdict.NOT_APPLICABLE,
                    reason_code=None,
                    evidence={
                        "asset_kind": target.asset.kind.value,
                        "outcome": ValidationVerdict.NOT_APPLICABLE.value,
                        "sdk_version": identity.sdk_version,
                        "trust_config_sha256": identity.trust_config_sha256,
                        "trust_config_version": identity.trust_config_version,
                        "validator": identity.validator,
                    },
                )
            )
        assert stream is not None
        stream.seek(0)
        result = self._provenance.verify(
            mime_type=target.asset_version.declared_mime,
            stream=stream,
            byte_length=target.asset_version.byte_size,
        )
        self._guard_retention(target)
        if result.outcome == ProvenanceVerificationOutcome.RETRYABLE_FAILURE:
            verdict = ValidationVerdict.RETRYABLE_FAILURE
            reason = result.failure_code or "PROVENANCE_UNAVAILABLE"
        elif result.status == ProvenanceEvidenceStatus.CONFLICTING:
            verdict = ValidationVerdict.REVIEW
            reason = "PROVENANCE_CONFLICTING"
        else:
            verdict = ValidationVerdict.PASS
            reason = None
        evidence = {
            "asset_kind": target.asset.kind.value,
            "failure_code": result.failure_code,
            "failure_codes": list(result.failure_codes),
            "latency_ms": result.latency_ms,
            "manifest_count": result.manifest_count,
            "outcome": result.outcome.value,
            "remote_manifest_fetch": result.remote_manifest_fetch,
            "sdk_version": result.sdk_version,
            "status": result.status.value if result.status is not None else None,
            "trust_config_sha256": result.trust_config_sha256,
            "trust_config_version": result.trust_config_version,
            "validation_state": result.validation_state,
            "validator": result.validator,
        }
        validation_result = self._new_result(
            request=request,
            target=target,
            stage=ValidationStage.PROVENANCE,
            validator_name=result.validator,
            validator_version=result.sdk_version,
            verdict=verdict,
            reason_code=reason,
            evidence=evidence,
        )
        persisted = self._append_result(validation_result)
        if verdict == ValidationVerdict.RETRYABLE_FAILURE:
            raise self._retryable(
                reason or "PROVENANCE_UNAVAILABLE",
                "provenance validation is temporarily unavailable",
                category="provenance",
            )
        return persisted

    def _enforce_stage_result(
        self,
        request: OperationExecutionRequest,
        target: AssetValidationTarget,
        result: AssetValidationResult,
        *,
        expected_stage: ValidationStage,
    ) -> None:
        try:
            assert_source_evidence_identity(
                target=target,
                request=request,
                result=result,
                expected_stage=expected_stage,
            )
        except AssetValidationEvidenceError as exc:
            raise self._evidence_failure(exc) from exc
        if expected_stage == ValidationStage.CONTENT_SAFETY:
            try:
                assert_content_safety_stage_identity(
                    result=result,
                    asset_kind=target.asset.kind,
                    configured_identity=self._content_safety.configured_identity,
                )
            except AssetValidationEvidenceError as exc:
                raise self._evidence_failure(exc) from exc
            self._enforce_content_transfer_identity(target=target, result=result)
        if expected_stage == ValidationStage.PROVENANCE:
            try:
                assert_provenance_stage_identity(
                    result=result,
                    asset_kind=target.asset.kind,
                    configured_identity=self._provenance.configured_identity,
                )
            except AssetValidationEvidenceError as exc:
                raise self._evidence_failure(exc) from exc
        if expected_stage == ValidationStage.MALWARE and result.verdict == ValidationVerdict.PASS:
            try:
                scanner_identity = self._malware_scanner.identity()
            except (RuntimeError, TimeoutError) as exc:
                raise self._retryable(
                    "MALWARE_SCANNER_IDENTITY_UNAVAILABLE",
                    "malware scanner identity is temporarily unavailable",
                ) from exc
            if (
                result.validator_name != "clamav"
                or result.validator_version != scanner_identity
                or result.evidence.get("scanner_version") != scanner_identity
            ):
                raise self._evidence_failure(
                    AssetValidationEvidenceError(
                        code="MALWARE_EVIDENCE_IDENTITY_MISMATCH",
                        message=(
                            "malware PASS evidence does not match the active "
                            "scanner identity and validation policy"
                        ),
                    )
                )
        if result.verdict == ValidationVerdict.BLOCK:
            reason = result.reason_code or "VALIDATION_REJECTED"
            self._reject(request, target, reason_code=reason)
        if result.verdict == ValidationVerdict.RETRYABLE_FAILURE:
            raise self._retryable(
                result.reason_code or "VALIDATION_RETRYABLE_FAILURE",
                "validation dependency is temporarily unavailable",
            )
        if result.verdict == ValidationVerdict.TERMINAL_FAILURE:
            raise self._terminal(
                result.reason_code or "VALIDATION_TERMINAL_FAILURE",
                "validation provider permanently rejected the request",
                category=result.stage.value.lower(),
            )

    def _enforce_content_transfer_identity(
        self,
        *,
        target: AssetValidationTarget,
        result: AssetValidationResult,
    ) -> None:
        evidence = result.evidence
        expected = {
            "transfer_policy_version": (target.asset_version.validation_transfer_policy_version),
            "transfer_policy_snapshot_sha256": (
                target.asset_version.validation_transfer_policy_snapshot_sha256
            ),
            "transfer_purpose": SECURITY_VALIDATION_PURPOSE,
        }
        if any(evidence.get(key) != value for key, value in expected.items()):
            raise self._terminal(
                "VALIDATION_TRANSFER_EVIDENCE_MISMATCH",
                "content-safety evidence does not match the immutable transfer policy",
                category="integrity",
            )
        if evidence.get("transfer_external") is not True:
            return
        provider = getattr(
            self._content_safety_request_factory,
            "transfer_provider",
            "",
        )
        endpoint_region = getattr(
            self._content_safety_request_factory,
            "transfer_endpoint_region",
            "",
        )
        endpoint_host = self._content_safety.configured_identity.endpoint
        if (
            evidence.get("transfer_provider") != provider
            or evidence.get("transfer_endpoint_region") != endpoint_region
            or evidence.get("transfer_endpoint_host") != endpoint_host
        ):
            raise self._terminal(
                "VALIDATION_TRANSFER_EVIDENCE_MISMATCH",
                "content-safety evidence provider scope is no longer compatible",
                category="integrity",
            )
        try:
            self._validation_transfer_policy.authorize(
                persisted_policy_version=(target.asset_version.validation_transfer_policy_version),
                persisted_policy_snapshot_sha256=(
                    target.asset_version.validation_transfer_policy_snapshot_sha256
                ),
                workspace_id=target.asset.workspace_id,
                asset_version_id=target.asset_version.id,
                asset_kind=target.asset.kind,
                retention_class=target.asset.retention_class,
                provider=provider,
                endpoint_region=endpoint_region,
                endpoint_host=endpoint_host,
                purpose=SECURITY_VALIDATION_PURPOSE,
            )
        except ValidationDataTransferDenied as exc:
            raise self._terminal(
                exc.code,
                exc.message,
                category="validation_transfer",
            ) from exc

    def _mark_pending_review(
        self,
        request: OperationExecutionRequest,
        target: AssetValidationTarget,
        *,
        reason_code: str | None,
    ) -> None:
        try:
            self._lifecycle.mark_pending_review(
                request,
                target,
                reason_code=reason_code,
            )
        except AssetValidationLifecycleError as exc:
            raise self._lifecycle_failure(exc) from exc

    def _promote(
        self,
        request: OperationExecutionRequest,
        target: AssetValidationTarget,
    ) -> None:
        with self._observer.stage(
            request=request,
            target=target,
            stage=ValidationStage.PROMOTION,
            reused=target.asset.status == AssetState.PENDING_RIGHTS,
        ):
            try:
                self._promotion.promote(request=request, target=target)
            except AssetValidationPromotionError as exc:
                if exc.rejection_reason is not None:
                    self._reject(
                        request,
                        target,
                        reason_code=exc.rejection_reason,
                    )
                factory = self._retryable if exc.retryable else self._terminal
                raise factory(
                    exc.code,
                    exc.message,
                    category=exc.category,
                ) from exc

    def _reject(
        self,
        request: OperationExecutionRequest,
        target: AssetValidationTarget,
        *,
        reason_code: str,
    ) -> None:
        try:
            self._lifecycle.reject(
                request,
                target,
                reason_code=reason_code,
            )
        except AssetValidationLifecycleError as exc:
            raise self._lifecycle_failure(exc) from exc

    def _cleanup_rejected(
        self,
        target: AssetValidationTarget,
        *,
        reason_code: str,
    ) -> None:
        try:
            self._lifecycle.cleanup_rejected(
                target,
                reason_code=reason_code,
            )
        except AssetValidationLifecycleError as exc:
            raise self._lifecycle_failure(exc) from exc

    def _new_result(
        self,
        *,
        request: OperationExecutionRequest,
        target: AssetValidationTarget,
        stage: ValidationStage,
        validator_name: str,
        validator_version: str,
        verdict: ValidationVerdict,
        reason_code: str | None,
        evidence: dict[str, object],
    ) -> AssetValidationResult:
        source = target.source_object
        now = self._guard_retention(target)
        return AssetValidationResult.create(
            workspace_id=request.workspace_id,
            operation_id=request.operation_id,
            asset_version_id=target.asset_version.id,
            asset_object_id=source.id,
            attempt_number=request.attempt_count,
            stage=stage,
            validator_name=validator_name,
            validator_version=validator_version,
            policy_version=target.asset_version.validation_policy_version,
            verdict=verdict,
            reason_code=reason_code,
            object_provider_version_id=source.provider_version_id or "",
            object_etag=source.etag,
            content_sha256=source.sha256,
            evidence=evidence,
            retention_deadline=target.asset.retention_deadline,
            now=now,
        )

    def _guard_retention(self, target: AssetValidationTarget) -> datetime:
        try:
            return self._retention.guard(target)
        except AssetValidationRetentionError as exc:
            factory = self._retryable if exc.retryable else self._terminal
            raise factory(
                exc.code,
                exc.message,
                category=exc.category,
            ) from exc

    def _append_result(
        self,
        result: AssetValidationResult,
    ) -> AssetValidationResult:
        try:
            persisted = self._evidence.append(result)
        except AssetValidationEvidenceError as exc:
            raise self._evidence_failure(exc) from exc
        self._observer.result(
            result=persisted,
            reused=persisted.id != result.id,
        )
        return persisted

    def _evidence_failure(
        self,
        error: AssetValidationEvidenceError,
    ) -> OperationExecutionFailure:
        factory = self._retryable if error.retryable else self._terminal
        return factory(
            error.code,
            error.message,
            category="integrity",
        )

    def _lifecycle_failure(
        self,
        error: AssetValidationLifecycleError,
    ) -> OperationExecutionFailure:
        factory = self._retryable if error.retryable else self._terminal
        return factory(
            error.code,
            error.message,
            category=error.category,
        )

    @staticmethod
    def _retryable(
        code: str,
        message: str,
        *,
        category: str = "validation",
    ) -> OperationExecutionFailure:
        return OperationExecutionFailure(
            NormalizedOperationError(
                code=code,
                category=category,
                message=message,
                retryable=True,
            )
        )

    @staticmethod
    def _terminal(
        code: str,
        message: str,
        *,
        category: str = "validation",
    ) -> OperationExecutionFailure:
        return OperationExecutionFailure(
            NormalizedOperationError(
                code=code,
                category=category,
                message=message,
                retryable=False,
            )
        )
