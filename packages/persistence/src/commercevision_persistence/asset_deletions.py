"""Persistence for immutable Asset deletion tombstones."""

from commercevision_application.asset_ports import AssetDeletionProgressSnapshot
from commercevision_domain import AssetDeletionTombstone
from sqlalchemy import select
from sqlalchemy.orm import Session

from .retention_models import AssetDeletionProgressModel, AssetDeletionTombstoneModel


class AssetDeletionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, tombstone: AssetDeletionTombstone) -> None:
        self._session.add(
            AssetDeletionTombstoneModel(
                id=tombstone.id,
                workspace_id=tombstone.workspace_id,
                asset_id=tombstone.asset_id,
                target_asset_version_id=tombstone.target_asset_version_id,
                deletion_generation=tombstone.deletion_generation,
                operation_id=tombstone.operation_id,
                reason=tombstone.reason.value,
                requested_by=tombstone.requested_by,
                requested_at=tombstone.requested_at,
            )
        )

    def list_latest_progress(
        self,
        *,
        workspace_id: str,
        operation_id: str,
    ) -> list[AssetDeletionProgressSnapshot]:
        rows = self._session.execute(
            select(AssetDeletionProgressModel)
            .join(
                AssetDeletionTombstoneModel,
                AssetDeletionTombstoneModel.id == AssetDeletionProgressModel.tombstone_id,
            )
            .where(
                AssetDeletionProgressModel.workspace_id == workspace_id,
                AssetDeletionTombstoneModel.operation_id == operation_id,
            )
            .order_by(
                AssetDeletionProgressModel.created_at.desc(),
                AssetDeletionProgressModel.id.desc(),
            )
        ).scalars()
        latest: dict[str, AssetDeletionProgressSnapshot] = {}
        for row in rows:
            if row.component in latest:
                continue
            latest[row.component] = AssetDeletionProgressSnapshot(
                component=row.component,
                state=row.state,
                observed_count=row.observed_count,
                converged_count=row.converged_count,
                error_code=row.error_code,
                observed_at=row.created_at,
            )
        return [latest[component] for component in sorted(latest)]
