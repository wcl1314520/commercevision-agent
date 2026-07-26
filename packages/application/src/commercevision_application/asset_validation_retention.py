"""Task Asset retention checks and exact storage cleanup convergence."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from commercevision_domain import (
    Asset,
    AssetObjectState,
    AssetState,
    ConcurrencyError,
    RetentionClass,
    StoragePreconditionError,
    StorageUnavailableError,
)

from .asset_ports import AssetUnitOfWorkFactory
from .asset_promotion import UploadPromoter
from .asset_validation_target import AssetValidationTarget


@dataclass(frozen=True, slots=True)
class AssetValidationRetentionError(Exception):
    code: str
    message: str
    retryable: bool
    category: str

    def __str__(self) -> str:
        return self.message


class AssetValidationRetentionCoordinator:
    """Prevent post-deadline dispatch and converge exact expired object cleanup."""

    def __init__(
        self,
        *,
        uow_factory: AssetUnitOfWorkFactory,
        promoter: UploadPromoter,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._promoter = promoter
        self._clock = clock or (lambda: datetime.now(UTC))

    def guard(self, target: AssetValidationTarget) -> datetime:
        if target.asset.retention_class != RetentionClass.TASK:
            return self._clock()
        with self._uow_factory() as uow:
            asset = uow.assets.get(
                workspace_id=target.asset.workspace_id,
                asset_id=target.asset.id,
            )
        now = self._clock()
        self._assert_exact_deadline(asset, target=target)
        assert asset is not None
        assert asset.retention_deadline is not None
        if now < asset.retention_deadline:
            return now
        self.expire(target)
        raise AssetValidationRetentionError(
            code="ASSET_RETENTION_EXPIRED",
            message="Task Asset retention expired before validation completed",
            retryable=False,
            category="retention",
        )

    def expire_if_due(self, target: AssetValidationTarget) -> bool:
        """Converge an overdue Task Asset and report whether cleanup was required."""

        if target.asset.retention_class != RetentionClass.TASK:
            return False
        deadline = target.asset.retention_deadline
        if deadline is None:
            raise AssetValidationRetentionError(
                code="ASSET_RETENTION_STATE_MISMATCH",
                message="Task Asset retention identity is incomplete",
                retryable=False,
                category="integrity",
            )
        if self._clock() < deadline:
            return False
        self.expire(target)
        return True

    def expire(self, target: AssetValidationTarget) -> None:
        now = self._clock()
        deadline = target.asset.retention_deadline
        if deadline is None or now < deadline:
            raise AssetValidationRetentionError(
                code="ASSET_RETENTION_STATE_MISMATCH",
                message="Task Asset retention cleanup was requested before its exact deadline",
                retryable=False,
                category="integrity",
            )
        self._mark_cleanup_pending(target, now=now)
        try:
            self._promoter.discard_for_retention(target.upload_session)
        except StorageUnavailableError as exc:
            raise AssetValidationRetentionError(
                code="RETENTION_CLEANUP_STORAGE_UNAVAILABLE",
                message="expired Task Asset cleanup storage is temporarily unavailable",
                retryable=True,
                category="storage",
            ) from exc
        except StoragePreconditionError as exc:
            raise AssetValidationRetentionError(
                code="RETENTION_CLEANUP_OBJECT_MISMATCH",
                message="expired Task Asset cleanup object identity no longer matches",
                retryable=False,
                category="integrity",
            ) from exc

        try:
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
                controlled = uow.assets.get_object(
                    workspace_id=target.asset.workspace_id,
                    asset_version_id=target.asset_version.id,
                    role="CONTROLLED_ORIGINAL",
                    for_update=True,
                )
                self._assert_exact_deadline(asset, target=target)
                if source is None or source.id != target.source_object.id:
                    raise AssetValidationRetentionError(
                        code="ASSET_RETENTION_STATE_MISMATCH",
                        message="expired Task Asset source identity is unavailable",
                        retryable=False,
                        category="integrity",
                    )
                for object_fact in (source, controlled):
                    if object_fact is None or object_fact.state == AssetObjectState.DELETED:
                        continue
                    object_fact.mark_delete_pending(now=now)
                    object_fact.mark_deleted(now=now)
                    uow.assets.save_object(object_fact)
                assert asset is not None
                asset.expire_retention(now=now)
                uow.assets.save_asset(asset)
                uow.commit()
        except ConcurrencyError as exc:
            raise AssetValidationRetentionError(
                code="RETENTION_CLEANUP_CONCURRENT_WRITE",
                message="expired Task Asset cleanup has not converged",
                retryable=True,
                category="concurrency",
            ) from exc

    def _mark_cleanup_pending(
        self,
        target: AssetValidationTarget,
        *,
        now: datetime,
    ) -> None:
        try:
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
                controlled = uow.assets.get_object(
                    workspace_id=target.asset.workspace_id,
                    asset_version_id=target.asset_version.id,
                    role="CONTROLLED_ORIGINAL",
                    for_update=True,
                )
                self._assert_exact_deadline(asset, target=target)
                if source is None or source.id != target.source_object.id:
                    raise AssetValidationRetentionError(
                        code="ASSET_RETENTION_STATE_MISMATCH",
                        message="expired Task Asset source identity is unavailable",
                        retryable=False,
                        category="integrity",
                    )
                assert asset is not None
                asset.begin_retention_cleanup(now=now)
                uow.assets.save_asset(asset)
                for object_fact in (source, controlled):
                    if object_fact is None or object_fact.state in {
                        AssetObjectState.DELETE_PENDING,
                        AssetObjectState.DELETED,
                    }:
                        continue
                    object_fact.mark_delete_pending(now=now)
                    uow.assets.save_object(object_fact)
                uow.commit()
        except ConcurrencyError as exc:
            raise AssetValidationRetentionError(
                code="RETENTION_CLEANUP_CONCURRENT_WRITE",
                message="expired Task Asset cleanup intent has not converged",
                retryable=True,
                category="concurrency",
            ) from exc

    @staticmethod
    def assert_commit_active(
        asset: Asset,
        *,
        target: AssetValidationTarget,
        now: datetime,
    ) -> None:
        AssetValidationRetentionCoordinator._assert_exact_deadline(
            asset,
            target=target,
        )
        if (
            asset.status in {AssetState.DELETING, AssetState.DELETED}
            or asset.retention_deadline is not None
            and now >= asset.retention_deadline
        ):
            raise AssetValidationRetentionError(
                code="ASSET_RETENTION_EXPIRED",
                message="Task Asset retention expired before promotion commit",
                retryable=False,
                category="retention",
            )

    @staticmethod
    def _assert_exact_deadline(
        asset: Asset | None,
        *,
        target: AssetValidationTarget,
    ) -> None:
        if (
            asset is None
            or asset.retention_class != target.asset.retention_class
            or asset.retention_deadline != target.asset.retention_deadline
        ):
            raise AssetValidationRetentionError(
                code="ASSET_RETENTION_STATE_MISMATCH",
                message="Task Asset retention identity changed during validation",
                retryable=False,
                category="integrity",
            )
