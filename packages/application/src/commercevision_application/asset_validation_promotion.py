"""Verified object promotion and atomic MySQL convergence."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from commercevision_contracts.object_storage import ObjectReference
from commercevision_domain import (
    Asset,
    AssetObject,
    AssetObjectState,
    AssetState,
    AssetValidationResult,
    ConcurrencyError,
    ObjectMismatchError,
    RetentionClass,
    StoragePreconditionError,
    StorageUnavailableError,
    UniqueConstraintError,
    UploadObjectMissingError,
    ValidationStage,
    ValidationVerdict,
)

from .asset_integrity import VerifiedUpload
from .asset_ports import (
    AssetRetentionCommitExpiredError,
    AssetUnitOfWorkFactory,
    AssetUnitOfWorkPort,
)
from .asset_promotion import UploadPromoter
from .asset_validation_events import build_validation_completed_event
from .asset_validation_retention import (
    AssetValidationRetentionCoordinator,
    AssetValidationRetentionError,
)
from .asset_validation_target import AssetValidationTarget
from .operations import OperationExecutionRequest

_PROMOTION_VALIDATOR = "commercevision-object-promotion"
_PROMOTION_VALIDATOR_VERSION = "verified-copy-v1"


@dataclass(frozen=True, slots=True)
class AssetValidationPromotionError(Exception):
    code: str
    message: str
    category: str
    retryable: bool
    rejection_reason: str | None = None

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class _ConcurrentControlledVersion(Exception):
    controlled: AssetObject


class AssetValidationPromotionCoordinator:
    """Verify storage first, then converge one controlled-object database fact."""

    def __init__(
        self,
        *,
        uow_factory: AssetUnitOfWorkFactory,
        promoter: UploadPromoter,
        retention: AssetValidationRetentionCoordinator,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._promoter = promoter
        self._retention = retention
        self._clock = clock or (lambda: datetime.now(UTC))

    def promote(
        self,
        *,
        request: OperationExecutionRequest,
        target: AssetValidationTarget,
    ) -> None:
        try:
            self._retention.guard(target)
            verified = self._verify_storage(target)
            self._retention.guard(target)
            self._persist_with_duplicate_recovery(
                request=request,
                target=target,
                verified=verified,
            )
        except (UniqueConstraintError, ConcurrencyError) as exc:
            try:
                self._retention.guard(target)
                self._recover_concurrent_commit(
                    request=request,
                    target=target,
                    verified=verified,
                    cause=exc,
                )
            except AssetValidationRetentionError as retention_error:
                self._raise_retention_failure(
                    target=target,
                    error=retention_error,
                )
        except AssetValidationRetentionError as exc:
            self._raise_retention_failure(target=target, error=exc)

    def _persist_with_duplicate_recovery(
        self,
        *,
        request: OperationExecutionRequest,
        target: AssetValidationTarget,
        verified: VerifiedUpload,
    ) -> None:
        current = verified
        for recovery_attempt in range(2):
            try:
                self._persist_verified(
                    request=request,
                    target=target,
                    verified=current,
                )
                return
            except _ConcurrentControlledVersion as conflict:
                if recovery_attempt > 0:
                    raise AssetValidationPromotionError(
                        code="PROMOTION_CONCURRENT_WRITE",
                        message="concurrent promotion versions did not converge",
                        category="concurrency",
                        retryable=True,
                    ) from conflict
                current = self._reconcile_concurrent_version(
                    target=target,
                    controlled=conflict.controlled,
                    observed=current,
                )

    def _verify_storage(self, target: AssetValidationTarget) -> VerifiedUpload:
        try:
            return self._promoter.verify_and_promote(target.upload_session)
        except StorageUnavailableError as exc:
            raise AssetValidationPromotionError(
                code="PROMOTION_STORAGE_UNAVAILABLE",
                message="promotion storage is temporarily unavailable",
                category="storage",
                retryable=True,
            ) from exc
        except UploadObjectMissingError as exc:
            raise AssetValidationPromotionError(
                code="PROMOTION_SOURCE_MISSING",
                message="promotion source and verified destination are unavailable",
                category="integrity",
                retryable=False,
                rejection_reason="PROMOTION_SOURCE_MISSING",
            ) from exc
        except (ObjectMismatchError, StoragePreconditionError) as exc:
            raise AssetValidationPromotionError(
                code="PROMOTION_OBJECT_MISMATCH",
                message="promotion object identity verification failed",
                category="integrity",
                retryable=False,
                rejection_reason="PROMOTION_OBJECT_MISMATCH",
            ) from exc

    def _persist_verified(
        self,
        *,
        request: OperationExecutionRequest,
        target: AssetValidationTarget,
        verified: VerifiedUpload,
    ) -> None:
        with self._uow_factory() as uow:
            asset, source, controlled = self._load_promotion_facts(
                uow,
                target=target,
                for_update=True,
            )
            now = self._clock()
            self._retention.assert_commit_active(
                asset,
                target=target,
                now=now,
            )
            if controlled is not None:
                if _is_same_content_concurrent_version(
                    target=target,
                    controlled=controlled,
                    verified=verified,
                ):
                    raise _ConcurrentControlledVersion(controlled)
                assert_controlled_object_identity(
                    target=target,
                    controlled=controlled,
                    verified=verified,
                )
            if asset.status == AssetState.PENDING_RIGHTS:
                if (
                    controlled is None
                    or source.state != AssetObjectState.DELETED
                    or not self._has_matching_promotion_evidence(
                        uow,
                        request=request,
                        target=target,
                        controlled=controlled,
                    )
                ):
                    self._integrity_failure(
                        "PROMOTION_COMMIT_INCOMPLETE",
                        "completed promotion facts are incomplete or inconsistent",
                    )
                return
            if asset.status != AssetState.VALIDATING:
                raise AssetValidationPromotionError(
                    code="INVALID_ASSET_STATE",
                    message="Asset cannot complete promotion from its current state",
                    category="state",
                    retryable=False,
                )
            if controlled is None:
                controlled = AssetObject.create_controlled(
                    workspace_id=target.asset.workspace_id,
                    asset_version_id=target.asset_version.id,
                    backend=verified.stat.backend,
                    location=verified.stat.reference.location,
                    bucket=verified.stat.bucket,
                    key=verified.stat.reference.key,
                    provider_version_id=verified.stat.reference.version_id,
                    etag=verified.stat.etag,
                    byte_size=verified.byte_size,
                    sha256=verified.sha256,
                    now=now,
                )
                uow.assets.add_object(controlled)
            source.mark_delete_pending(now=now)
            source.mark_deleted(now=now)
            uow.assets.save_object(source)
            asset.mark_pending_rights(now=now)
            uow.assets.save_asset(asset)
            uow.assets.add_validation_result(
                self._promotion_result(
                    request=request,
                    target=target,
                    controlled=controlled,
                    now=now,
                )
            )
            uow.outbox.add(
                build_validation_completed_event(
                    request=request,
                    asset=asset,
                    outcome="PENDING_RIGHTS",
                    reason_code=None,
                    now=now,
                )
            )
            self._retention.assert_commit_active(
                asset,
                target=target,
                now=self._clock(),
            )
            try:
                if asset.retention_class == RetentionClass.TASK:
                    assert asset.retention_deadline is not None
                    uow.commit_before_retention_deadline(
                        workspace_id=asset.workspace_id,
                        asset_id=asset.id,
                        retention_deadline=asset.retention_deadline,
                        clock=self._clock,
                    )
                else:
                    uow.commit()
            except AssetRetentionCommitExpiredError as exc:
                raise AssetValidationRetentionError(
                    code="ASSET_RETENTION_EXPIRED",
                    message="Task Asset retention expired at the promotion commit boundary",
                    retryable=False,
                    category="retention",
                ) from exc

    def _raise_retention_failure(
        self,
        *,
        target: AssetValidationTarget,
        error: AssetValidationRetentionError,
    ) -> None:
        resolved = error
        if error.code == "ASSET_RETENTION_EXPIRED":
            try:
                self._retention.expire(target)
            except AssetValidationRetentionError as cleanup_error:
                resolved = cleanup_error
        raise AssetValidationPromotionError(
            code=resolved.code,
            message=resolved.message,
            category=resolved.category,
            retryable=resolved.retryable,
        ) from resolved

    def _recover_concurrent_commit(
        self,
        *,
        request: OperationExecutionRequest,
        target: AssetValidationTarget,
        verified: VerifiedUpload,
        cause: Exception,
    ) -> None:
        concurrent_version: AssetObject | None = None
        with self._uow_factory() as uow:
            asset, source, controlled = self._load_promotion_facts(
                uow,
                target=target,
                for_update=False,
            )
            if controlled is not None:
                if _is_same_content_concurrent_version(
                    target=target,
                    controlled=controlled,
                    verified=verified,
                ):
                    concurrent_version = controlled
                else:
                    assert_controlled_object_identity(
                        target=target,
                        controlled=controlled,
                        verified=verified,
                    )
            if (
                asset.status == AssetState.PENDING_RIGHTS
                and source.state == AssetObjectState.DELETED
                and controlled is not None
                and concurrent_version is None
                and self._has_matching_promotion_evidence(
                    uow,
                    request=request,
                    target=target,
                    controlled=controlled,
                )
            ):
                return
        if concurrent_version is not None:
            canonical = self._reconcile_concurrent_version(
                target=target,
                controlled=concurrent_version,
                observed=verified,
            )
            try:
                self._persist_with_duplicate_recovery(
                    request=request,
                    target=target,
                    verified=canonical,
                )
            except (UniqueConstraintError, ConcurrencyError) as exc:
                raise AssetValidationPromotionError(
                    code="PROMOTION_CONCURRENT_WRITE",
                    message="concurrent promotion facts have not fully converged",
                    category="concurrency",
                    retryable=True,
                ) from exc
            return
        raise AssetValidationPromotionError(
            code="PROMOTION_CONCURRENT_WRITE",
            message="concurrent promotion facts have not fully converged",
            category="concurrency",
            retryable=True,
        ) from cause

    def _reconcile_concurrent_version(
        self,
        *,
        target: AssetValidationTarget,
        controlled: AssetObject,
        observed: VerifiedUpload,
    ) -> VerifiedUpload:
        try:
            self._retention.guard(target)
            canonical = self._promoter.reconcile_concurrent_destination(
                target.upload_session,
                canonical_reference=ObjectReference(
                    location=controlled.location,
                    key=controlled.key,
                    version_id=controlled.provider_version_id,
                ),
                observed=observed,
            )
            self._retention.guard(target)
            return canonical
        except (
            ObjectMismatchError,
            StoragePreconditionError,
            UploadObjectMissingError,
        ) as exc:
            raise AssetValidationPromotionError(
                code="PROMOTION_CONTROLLED_OBJECT_MISMATCH",
                message=(
                    "the committed controlled object version could not be "
                    "verified during concurrent promotion"
                ),
                category="integrity",
                retryable=False,
                rejection_reason="PROMOTION_CONTROLLED_OBJECT_MISMATCH",
            ) from exc
        except StorageUnavailableError as exc:
            raise AssetValidationPromotionError(
                code="PROMOTION_STORAGE_UNAVAILABLE",
                message="promotion storage is temporarily unavailable",
                category="storage",
                retryable=True,
            ) from exc

    def _load_promotion_facts(
        self,
        uow: AssetUnitOfWorkPort,
        *,
        target: AssetValidationTarget,
        for_update: bool,
    ) -> tuple[Asset, AssetObject, AssetObject | None]:
        asset = uow.assets.get(
            workspace_id=target.asset.workspace_id,
            asset_id=target.asset.id,
            for_update=for_update,
        )
        source = uow.assets.get_object(
            workspace_id=target.asset.workspace_id,
            asset_version_id=target.asset_version.id,
            role="ORIGINAL",
            for_update=for_update,
        )
        controlled = uow.assets.get_object(
            workspace_id=target.asset.workspace_id,
            asset_version_id=target.asset_version.id,
            role="CONTROLLED_ORIGINAL",
            for_update=for_update,
        )
        if asset is None or source is None:
            raise AssetValidationPromotionError(
                code="VALIDATION_TARGET_NOT_FOUND",
                message="promotion target facts are unavailable",
                category="integrity",
                retryable=False,
            )
        return asset, source, controlled

    @staticmethod
    def _has_matching_promotion_evidence(
        uow: AssetUnitOfWorkPort,
        *,
        request: OperationExecutionRequest,
        target: AssetValidationTarget,
        controlled: AssetObject,
    ) -> bool:
        return any(
            result.operation_id == request.operation_id
            and result.stage == ValidationStage.PROMOTION
            and result.validator_name == _PROMOTION_VALIDATOR
            and result.validator_version == _PROMOTION_VALIDATOR_VERSION
            and result.policy_version == target.asset_version.validation_policy_version
            and result.asset_object_id == controlled.id
            and result.object_provider_version_id == controlled.provider_version_id
            and result.object_etag == controlled.etag
            and result.content_sha256 == controlled.sha256
            for result in uow.assets.list_validation_results(
                workspace_id=target.asset.workspace_id,
                asset_version_id=target.asset_version.id,
            )
        )

    @staticmethod
    def _promotion_result(
        *,
        request: OperationExecutionRequest,
        target: AssetValidationTarget,
        controlled: AssetObject,
        now: datetime,
    ) -> AssetValidationResult:
        return AssetValidationResult.create(
            workspace_id=target.asset.workspace_id,
            operation_id=request.operation_id,
            asset_version_id=target.asset_version.id,
            asset_object_id=controlled.id,
            attempt_number=request.attempt_count,
            stage=ValidationStage.PROMOTION,
            validator_name=_PROMOTION_VALIDATOR,
            validator_version=_PROMOTION_VALIDATOR_VERSION,
            policy_version=target.asset_version.validation_policy_version,
            verdict=ValidationVerdict.PASS,
            reason_code=None,
            object_provider_version_id=controlled.provider_version_id or "",
            object_etag=controlled.etag,
            content_sha256=controlled.sha256,
            evidence={
                "backend": controlled.backend.value,
                "byte_size": controlled.byte_size,
                "destination_location": controlled.location.value,
                "destination_verified": True,
                "source_deleted": True,
            },
            retention_deadline=target.asset.retention_deadline,
            now=now,
        )

    @staticmethod
    def _integrity_failure(code: str, message: str) -> None:
        raise AssetValidationPromotionError(
            code=code,
            message=message,
            category="integrity",
            retryable=False,
            rejection_reason=code,
        )


def assert_controlled_object_identity(
    *,
    target: AssetValidationTarget,
    controlled: AssetObject,
    verified: VerifiedUpload,
) -> None:
    """Prove every persisted controlled-object field matches verified storage."""

    stat = verified.stat
    if not all(
        (
            controlled.workspace_id == target.asset.workspace_id,
            controlled.asset_version_id == target.asset_version.id,
            controlled.role == "CONTROLLED_ORIGINAL",
            controlled.backend == stat.backend,
            controlled.location == stat.reference.location,
            controlled.bucket == stat.bucket,
            controlled.key == stat.reference.key,
            controlled.provider_version_id == stat.reference.version_id,
            controlled.etag == stat.etag,
            controlled.byte_size == verified.byte_size,
            controlled.byte_size == stat.content_length,
            controlled.sha256 == verified.sha256,
            controlled.sha256 == target.asset_version.sha256,
            controlled.state == AssetObjectState.CONTROLLED,
        )
    ):
        AssetValidationPromotionCoordinator._integrity_failure(
            "PROMOTION_CONTROLLED_OBJECT_MISMATCH",
            "existing controlled object does not match the verified destination",
        )


def _is_same_content_concurrent_version(
    *,
    target: AssetValidationTarget,
    controlled: AssetObject,
    verified: VerifiedUpload,
) -> bool:
    stat = verified.stat
    return (
        controlled.provider_version_id is not None
        and stat.reference.version_id is not None
        and controlled.provider_version_id != stat.reference.version_id
        and controlled.workspace_id == target.asset.workspace_id
        and controlled.asset_version_id == target.asset_version.id
        and controlled.role == "CONTROLLED_ORIGINAL"
        and controlled.backend == stat.backend
        and controlled.location == stat.reference.location
        and controlled.bucket == stat.bucket
        and controlled.key == stat.reference.key
        and controlled.byte_size == verified.byte_size
        and controlled.byte_size == stat.content_length
        and controlled.sha256 == verified.sha256
        and controlled.sha256 == target.asset_version.sha256
        and controlled.state == AssetObjectState.CONTROLLED
    )
