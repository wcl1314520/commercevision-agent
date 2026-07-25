"""Autonomous expiry recovery for abandoned Upload Sessions."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from .asset_cleanup_dispatch import (
    UploadCleanupPolicy,
    schedule_abandoned_upload_cleanup,
)
from .asset_ports import AssetUnitOfWorkFactory


class UploadSessionMaintenanceService:
    """Expire abandoned uploads and atomically issue their cleanup command."""

    def __init__(
        self,
        *,
        uow_factory: AssetUnitOfWorkFactory,
        batch_size: int,
        cleanup_policy: UploadCleanupPolicy,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if batch_size < 1:
            raise ValueError("upload maintenance batch_size must be positive")
        self._uow_factory = uow_factory
        self._batch_size = batch_size
        self._cleanup_policy = cleanup_policy
        self._clock = clock or (lambda: datetime.now(UTC))

    def expire_due_once(self, *, now: datetime | None = None) -> int:
        scanned_at = now or self._clock()
        with self._uow_factory() as uow:
            upload_sessions = uow.upload_sessions.claim_expired(
                now=scanned_at,
                limit=self._batch_size,
            )
            expired = 0
            for upload_session in upload_sessions:
                if not upload_session.expire_abandoned(now=scanned_at):
                    continue
                schedule_abandoned_upload_cleanup(
                    uow=uow,
                    upload_session=upload_session,
                    trace_id=f"upload-expiry:{upload_session.id}",
                    policy=self._cleanup_policy,
                    now=scanned_at,
                )
                expired += 1
            uow.commit()
        return expired
