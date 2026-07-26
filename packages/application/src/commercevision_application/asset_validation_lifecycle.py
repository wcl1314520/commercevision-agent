"""Asset validation lifecycle convergence and terminal event publication."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from commercevision_contracts.object_storage import (
    ConditionalDeleteRequest,
    ObjectReference,
    ObjectStorage,
)
from commercevision_domain import (
    AssetObjectState,
    AssetState,
    NormalizedOperationError,
    StoragePreconditionError,
    StorageUnavailableError,
    UploadObjectMissingError,
)

from .asset_ports import AssetUnitOfWorkFactory
from .asset_validation_events import (
    build_validation_completed_event,
    build_validation_failed_event,
)
from .asset_validation_retention import (
    AssetValidationRetentionCoordinator,
    AssetValidationRetentionError,
)
from .asset_validation_target import (
    AssetValidationTarget,
    AssetValidationTargetBinder,
)
from .operations import OperationExecutionRequest


@dataclass(frozen=True, slots=True)
class AssetValidationLifecycleError(Exception):
    code: str
    message: str
    category: str = "validation"
    retryable: bool = False

    def __str__(self) -> str:
        return self.message


class AssetValidationLifecycleCoordinator:
    """Converge validation states and cleanup without owning stage execution."""

    def __init__(
        self,
        *,
        uow_factory: AssetUnitOfWorkFactory,
        storage: ObjectStorage,
        retention: AssetValidationRetentionCoordinator,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._storage = storage
        self._retention = retention
        self._clock = clock or (lambda: datetime.now(UTC))
        self._target_binder = AssetValidationTargetBinder(uow_factory=uow_factory)

    def record_terminal_failure(
        self,
        request: OperationExecutionRequest,
        error: NormalizedOperationError,
    ) -> None:
        """Converge a failed Durable Operation into one replayable Asset fact."""

        target = self._target_binder.load_historical(request)
        if self._retention.expire_if_due(target):
            return
        try:
            with self._uow_factory() as uow:
                asset = uow.assets.get(
                    workspace_id=request.workspace_id,
                    asset_id=target.asset.id,
                    for_update=True,
                )
                source = uow.assets.get_object(
                    workspace_id=request.workspace_id,
                    asset_version_id=request.target_id,
                    role="ORIGINAL",
                    for_update=True,
                )
                if asset is None or source is None:
                    self._fail(
                        "VALIDATION_TARGET_NOT_FOUND",
                        "terminal validation target facts are unavailable",
                        category="integrity",
                    )
                now = self._clock()
                self._retention.assert_commit_active(
                    asset,
                    target=target,
                    now=now,
                )
                if asset.status in {
                    AssetState.BLOCKED,
                    AssetState.PENDING_REVIEW,
                    AssetState.PENDING_RIGHTS,
                    AssetState.DELETING,
                    AssetState.DELETED,
                }:
                    return
                if source.state != AssetObjectState.QUARANTINED:
                    self._fail(
                        "VALIDATION_FACT_MISMATCH",
                        "failed validation must retain its exact quarantined source object",
                        category="integrity",
                    )
                if asset.status == AssetState.FAILED:
                    return
                if asset.status not in {
                    AssetState.QUARANTINED,
                    AssetState.VALIDATING,
                }:
                    self._fail(
                        "INVALID_ASSET_STATE",
                        "validation terminal failure cannot converge from the current Asset state",
                        category="state",
                    )
                asset.fail_validation(now=now)
                uow.assets.save_asset(asset)
                uow.outbox.add(
                    build_validation_failed_event(
                        request=request,
                        asset=asset,
                        outcome="FAILED",
                        reason_code=error.code,
                        now=now,
                    )
                )
                uow.commit()
        except AssetValidationRetentionError as exc:
            if exc.code != "ASSET_RETENTION_EXPIRED":
                raise
            self._retention.expire(target)

    def mark_pending_review(
        self,
        request: OperationExecutionRequest,
        target: AssetValidationTarget,
        *,
        reason_code: str | None,
    ) -> None:
        self._guard_retention(target)
        try:
            with self._uow_factory() as uow:
                asset = uow.assets.get(
                    workspace_id=target.asset.workspace_id,
                    asset_id=target.asset.id,
                    for_update=True,
                )
                if asset is None:
                    self._fail(
                        "VALIDATION_TARGET_NOT_FOUND",
                        "validation Asset is unavailable",
                    )
                if asset.status == AssetState.VALIDATING:
                    now = self._clock()
                    self._retention.assert_commit_active(
                        asset,
                        target=target,
                        now=now,
                    )
                    asset.mark_pending_review(now=now)
                    uow.assets.save_asset(asset)
                    uow.outbox.add(
                        build_validation_completed_event(
                            request=request,
                            asset=asset,
                            outcome="PENDING_REVIEW",
                            reason_code=reason_code,
                            now=now,
                        )
                    )
                    uow.commit()
                elif asset.status != AssetState.PENDING_REVIEW:
                    self._fail(
                        "INVALID_ASSET_STATE",
                        "Asset cannot enter pending review from its current state",
                    )
        except AssetValidationRetentionError:
            self._guard_retention(target)

    def reject(
        self,
        request: OperationExecutionRequest,
        target: AssetValidationTarget,
        *,
        reason_code: str,
    ) -> None:
        with self._uow_factory() as uow:
            asset = uow.assets.get(
                workspace_id=target.asset.workspace_id,
                asset_id=target.asset.id,
                for_update=True,
            )
            source = uow.assets.get_object(
                workspace_id=target.asset.workspace_id,
                asset_version_id=target.asset_version.id,
                role="ORIGINAL",
                for_update=True,
            )
            if asset is None or source is None:
                self._fail(
                    "VALIDATION_TARGET_NOT_FOUND",
                    "rejected validation target facts are unavailable",
                )
            now = self._clock()
            if asset.status != AssetState.BLOCKED:
                asset.block(reason_code=reason_code, now=now)
                uow.assets.save_asset(asset)
                uow.outbox.add(
                    build_validation_failed_event(
                        request=request,
                        asset=asset,
                        outcome="BLOCKED",
                        reason_code=reason_code,
                        now=now,
                    )
                )
            if source.state not in {
                AssetObjectState.DELETE_PENDING,
                AssetObjectState.DELETED,
            }:
                source.mark_delete_pending(now=now)
                uow.assets.save_object(source)
            uow.commit()
        self.cleanup_rejected(target, reason_code=reason_code)

    def cleanup_rejected(
        self,
        target: AssetValidationTarget,
        *,
        reason_code: str,
    ) -> None:
        source = target.source_object
        if source.state != AssetObjectState.DELETED:
            try:
                self._storage.delete_if_match(
                    ConditionalDeleteRequest(
                        reference=ObjectReference(
                            location=source.location,
                            key=source.key,
                            version_id=source.provider_version_id,
                        ),
                        expected_etag=source.etag,
                    )
                )
            except StorageUnavailableError as exc:
                raise AssetValidationLifecycleError(
                    code="QUARANTINE_CLEANUP_UNAVAILABLE",
                    message="rejected quarantine cleanup is temporarily unavailable",
                    category="storage",
                    retryable=True,
                ) from exc
            except UploadObjectMissingError:
                pass
            except StoragePreconditionError as exc:
                raise AssetValidationLifecycleError(
                    code="QUARANTINE_CLEANUP_MISMATCH",
                    message="rejected quarantine object changed before cleanup",
                ) from exc
            with self._uow_factory() as uow:
                current = uow.assets.get_object(
                    workspace_id=target.asset.workspace_id,
                    asset_version_id=target.asset_version.id,
                    role="ORIGINAL",
                    for_update=True,
                )
                if current is None:
                    self._fail(
                        "VALIDATION_TARGET_NOT_FOUND",
                        "rejected object fact is unavailable",
                    )
                if current.state == AssetObjectState.QUARANTINED:
                    current.mark_delete_pending(now=self._clock())
                if current.state == AssetObjectState.DELETE_PENDING:
                    current.mark_deleted(now=self._clock())
                    uow.assets.save_object(current)
                    uow.commit()
        self._fail(
            reason_code,
            "Asset validation rejected the quarantined content",
        )

    def _guard_retention(self, target: AssetValidationTarget) -> datetime:
        try:
            return self._retention.guard(target)
        except AssetValidationRetentionError as exc:
            raise AssetValidationLifecycleError(
                code=exc.code,
                message=exc.message,
                category=exc.category,
                retryable=exc.retryable,
            ) from exc

    @staticmethod
    def _fail(
        code: str,
        message: str,
        *,
        category: str = "validation",
        retryable: bool = False,
    ) -> None:
        raise AssetValidationLifecycleError(
            code=code,
            message=message,
            category=category,
            retryable=retryable,
        )
