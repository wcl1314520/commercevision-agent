from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from commercevision_application.asset_deletion import (
    AssetDeletionPolicy,
    AssetRetentionApplicationService,
    schedule_asset_deletion,
)
from commercevision_application.asset_ports import AssetDeletionProgressSnapshot
from commercevision_contracts.events import AssetDeleteRequestedPayload
from commercevision_domain import (
    Asset,
    AssetDeletionReason,
    AssetKind,
    AssetState,
    RetentionClass,
    new_uuid7,
)

NOW = datetime(2026, 8, 4, 2, 0, tzinfo=UTC)


class Recorder:
    def __init__(self) -> None:
        self.items: list[object] = []

    def add(self, item: object) -> None:
        self.items.append(item)


class AssetRecorder:
    def __init__(self) -> None:
        self.saved: list[Asset] = []

    def save_asset(self, asset: Asset) -> None:
        self.saved.append(asset)


def _uow() -> SimpleNamespace:
    return SimpleNamespace(
        operations=Recorder(),
        outbox=Recorder(),
        asset_deletions=Recorder(),
        assets=AssetRecorder(),
    )


def _foundation_asset() -> Asset:
    return Asset.create_quarantined(
        asset_id=new_uuid7(),
        workspace_id="asset-deletion",
        retention_class=RetentionClass.FOUNDATION,
        kind=AssetKind.IMAGE,
        workflow_id=None,
        product_id=None,
        sku_id=None,
        current_version_id=new_uuid7(),
        retention_deadline=None,
        now=NOW,
    )


def test_schedule_asset_deletion_persists_tombstone_operation_and_command_as_one_unit() -> None:
    uow = _uow()
    asset = _foundation_asset()

    operation = schedule_asset_deletion(
        uow=uow,
        asset=asset,
        reason=AssetDeletionReason.ADMINISTRATOR_DELETE,
        requested_by="asset-admin",
        trace_id="trace-delete",
        policy=AssetDeletionPolicy(
            max_attempts=8,
            max_reconciliation_attempts=20,
            execution_max_elapsed=timedelta(hours=6),
        ),
        now=NOW + timedelta(seconds=1),
    )

    assert asset.status == AssetState.DELETING
    assert asset.deletion_generation == 1
    assert asset.deletion_operation_id == operation.id
    assert operation.target_type == "ASSET"
    assert operation.target_id == asset.id
    assert operation.target_version == asset.deletion_generation
    assert len(uow.operations.items) == 1
    assert len(uow.asset_deletions.items) == 1
    tombstone = uow.asset_deletions.items[0]
    assert tombstone.asset_id == asset.id
    assert tombstone.target_asset_version_id == asset.current_version_id
    assert tombstone.operation_id == operation.id
    assert tombstone.deletion_generation == 1
    assert len(uow.outbox.items) == 1
    payload = AssetDeleteRequestedPayload.model_validate(uow.outbox.items[0].envelope.payload)
    assert payload.target_id == asset.id
    assert payload.asset_version_id == asset.current_version_id
    assert payload.deletion_generation == 1


class _StatusRepository:
    def __init__(self, asset: Asset, operation: object) -> None:
        self.asset = asset
        self.operation = operation
        self.assets = self
        self.operations = self
        self.asset_deletions = self

    def __enter__(self) -> _StatusRepository:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def get(self, *args: object, **kwargs: object) -> object | None:
        if args:
            return self.operation
        return self.asset

    def list_latest_progress(
        self,
        *,
        workspace_id: str,
        operation_id: str,
    ) -> list[AssetDeletionProgressSnapshot]:
        assert workspace_id == self.asset.workspace_id
        assert operation_id == self.operation.id
        return [
            AssetDeletionProgressSnapshot(
                component="OBJECTS",
                state="CONVERGED",
                observed_count=2,
                converged_count=2,
                error_code=None,
                observed_at=NOW + timedelta(seconds=2),
            )
        ]


def test_deletion_status_returns_persisted_operation_and_component_progress() -> None:
    asset = _foundation_asset()
    uow = _uow()
    operation = schedule_asset_deletion(
        uow=uow,
        asset=asset,
        reason=AssetDeletionReason.ADMINISTRATOR_DELETE,
        requested_by="asset-admin",
        trace_id="trace-delete",
        policy=AssetDeletionPolicy(
            max_attempts=8,
            max_reconciliation_attempts=20,
            execution_max_elapsed=timedelta(hours=6),
        ),
        now=NOW + timedelta(seconds=1),
    )
    repository = _StatusRepository(asset, operation)
    service = AssetRetentionApplicationService(
        uow_factory=lambda: repository,
        policy=AssetDeletionPolicy(
            max_attempts=8,
            max_reconciliation_attempts=20,
            execution_max_elapsed=timedelta(hours=6),
        ),
    )

    response = service.status(workspace_id=asset.workspace_id, asset_id=asset.id)

    assert response.asset_id == asset.id
    assert response.asset_version_id == asset.current_version_id
    assert response.asset_state == AssetState.DELETING
    assert response.deletion_generation == 1
    assert response.operation.id == operation.id
    assert response.progress[0].component == "OBJECTS"
    assert response.progress[0].converged_count == 2
