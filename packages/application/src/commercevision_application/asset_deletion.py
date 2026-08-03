"""Atomic Asset tombstoning and durable deletion dispatch."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from commercevision_contracts import (
    AssetDeletionProgressItemV1,
    AssetDeletionStatusResponseV1,
    ValidationOperationSummaryV1,
)
from commercevision_contracts.events import AssetDeleteRequestedPayload, EventType
from commercevision_domain import (
    Asset,
    AssetDeletionReason,
    AssetDeletionTombstone,
    ConcurrencyError,
    DurableOperation,
    NotFoundError,
    OperationKind,
    RetentionClass,
    new_uuid7,
)
from commercevision_domain.messaging import EventEnvelope, OutboxEvent

from .asset_idempotency import canonical_hash
from .asset_ports import AssetUnitOfWorkFactory, AssetUnitOfWorkPort
from .asset_registry_facts import canonicalize_resource_id


@dataclass(frozen=True, slots=True)
class AssetDeletionConvergenceResult:
    output_ref: str


@dataclass(frozen=True, slots=True)
class AssetDeletionRequestResult:
    asset_id: str
    asset_version_id: str
    deletion_generation: int
    deletion_reason: AssetDeletionReason
    operation: DurableOperation


@dataclass(frozen=True, slots=True)
class AssetDeletionPolicy:
    max_attempts: int
    max_reconciliation_attempts: int
    execution_max_elapsed: timedelta

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("Asset deletion attempts must be positive")
        if self.max_reconciliation_attempts < 1:
            raise ValueError("Asset deletion reconciliation attempts must be positive")
        if self.execution_max_elapsed <= timedelta(0):
            raise ValueError("Asset deletion execution budget must be positive")


def asset_deletion_input_hash(
    asset: Asset,
    *,
    reason: AssetDeletionReason,
    deletion_generation: int,
) -> str:
    return canonical_hash(
        {
            "cleanup_contract": "asset-deletion-v1",
            "workspace_id": asset.workspace_id,
            "asset_id": asset.id,
            "target_asset_version_id": asset.current_version_id,
            "deletion_generation": deletion_generation,
            "reason": reason.value,
            "retention_class": asset.retention_class.value,
            "workflow_id": asset.workflow_id,
            "retention_deadline": (
                asset.retention_deadline.isoformat()
                if asset.retention_deadline is not None
                else None
            ),
        }
    )


def schedule_asset_deletion(
    *,
    uow: AssetUnitOfWorkPort,
    asset: Asset,
    reason: AssetDeletionReason,
    requested_by: str,
    trace_id: str,
    policy: AssetDeletionPolicy,
    now: datetime,
) -> DurableOperation:
    """Persist the unusable head, immutable tombstone, Operation, and command together."""

    if asset.deletion_operation_id is not None:
        operation = uow.operations.get(
            asset.deletion_operation_id,
            workspace_id=asset.workspace_id,
        )
        if operation is None:
            raise RuntimeError("Asset deletion Operation binding is missing")
        return operation

    deletion_generation = asset.deletion_generation + 1
    input_hash = asset_deletion_input_hash(
        asset,
        reason=reason,
        deletion_generation=deletion_generation,
    )
    operation = DurableOperation.create(
        workspace_id=asset.workspace_id,
        kind=OperationKind.ASSET_DELETION,
        target_type="ASSET",
        target_id=asset.id,
        target_version=deletion_generation,
        input_hash=input_hash,
        input_ref=f"mysql://assets/{asset.id}/deletions/{deletion_generation}",
        max_attempts=policy.max_attempts,
        max_reconciliation_attempts=policy.max_reconciliation_attempts,
        execution_max_elapsed=policy.execution_max_elapsed,
        now=now,
    )
    observed_generation = asset.request_deletion(
        reason=reason,
        operation_id=operation.id,
        target_asset_version_id=asset.current_version_id,
        now=now,
    )
    if observed_generation != deletion_generation:
        raise RuntimeError("Asset deletion generation changed while scheduling")
    tombstone = AssetDeletionTombstone(
        id=new_uuid7(),
        workspace_id=asset.workspace_id,
        asset_id=asset.id,
        target_asset_version_id=asset.current_version_id,
        deletion_generation=deletion_generation,
        operation_id=operation.id,
        reason=reason,
        requested_by=requested_by,
        requested_at=now,
    )
    payload = AssetDeleteRequestedPayload(
        operation_id=operation.id,
        workspace_id=asset.workspace_id,
        target_type="ASSET",
        target_id=asset.id,
        target_version=deletion_generation,
        reason=reason,
        asset_version_id=asset.current_version_id,
        deletion_generation=deletion_generation,
    )
    event = OutboxEvent(
        envelope=EventEnvelope.create(
            event_type=EventType.ASSET_DELETE_REQUESTED.value,
            aggregate_type="DurableOperation",
            aggregate_id=operation.id,
            aggregate_version=operation.version,
            trace_id=trace_id,
            payload=payload.model_dump(mode="json"),
            now=now,
        ),
        available_at=now,
        workspace_id=asset.workspace_id,
    )
    uow.operations.add(operation)
    uow.assets.save_asset(asset)
    uow.asset_deletions.add(tombstone)
    uow.outbox.add(event)
    return operation


class AssetRetentionApplicationService:
    """Start exact-deadline Task cleanup and administrator Foundation deletion."""

    def __init__(
        self,
        *,
        uow_factory: AssetUnitOfWorkFactory,
        policy: AssetDeletionPolicy,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._policy = policy
        self._clock = clock or (lambda: datetime.now(UTC))

    def expire_due_once(self, *, limit: int) -> int:
        if limit < 1:
            raise ValueError("Asset retention scan limit must be positive")
        processed = 0
        for _ in range(limit):
            with self._uow_factory() as uow:
                claims = uow.assets.claim_expired_assets(limit=1)
                if not claims:
                    break
                claim = claims[0]
                schedule_asset_deletion(
                    uow=uow,
                    asset=claim.asset,
                    reason=AssetDeletionReason.RETENTION_EXPIRED,
                    requested_by="asset-retention-scheduler",
                    trace_id=f"asset-retention:{claim.asset.id}",
                    policy=self._policy,
                    now=claim.database_now,
                )
                uow.commit()
                processed += 1
        return processed

    def request_administrator_deletion(
        self,
        *,
        workspace_id: str,
        asset_id: str,
        actor_id: str,
        expected_version: int,
        trace_id: str,
    ) -> AssetDeletionRequestResult:
        asset_id = canonicalize_resource_id(asset_id, resource="Asset")
        with self._uow_factory() as uow:
            asset = uow.assets.get(
                workspace_id=workspace_id,
                asset_id=asset_id,
                for_update=True,
            )
            if asset is None:
                raise NotFoundError(f"Asset {asset_id} was not found")
            if asset.retention_class != RetentionClass.FOUNDATION:
                raise ValueError("only Foundation Assets support administrator deletion")
            if asset.deletion_operation_id is not None:
                existing = uow.operations.get(
                    asset.deletion_operation_id,
                    workspace_id=workspace_id,
                )
                if existing is None:
                    raise RuntimeError("Asset deletion Operation binding is missing")
                assert asset.deletion_reason is not None
                return AssetDeletionRequestResult(
                    asset_id=asset.id,
                    asset_version_id=asset.current_version_id,
                    deletion_generation=asset.deletion_generation,
                    deletion_reason=asset.deletion_reason,
                    operation=existing,
                )
            if asset.version != expected_version:
                raise ConcurrencyError("Asset version changed before administrator deletion")
            operation = schedule_asset_deletion(
                uow=uow,
                asset=asset,
                reason=AssetDeletionReason.ADMINISTRATOR_DELETE,
                requested_by=actor_id,
                trace_id=trace_id,
                policy=self._policy,
                now=self._clock(),
            )
            uow.commit()
            assert asset.deletion_reason is not None
            return AssetDeletionRequestResult(
                asset_id=asset.id,
                asset_version_id=asset.current_version_id,
                deletion_generation=asset.deletion_generation,
                deletion_reason=asset.deletion_reason,
                operation=operation,
            )

    def status(
        self,
        *,
        workspace_id: str,
        asset_id: str,
    ) -> AssetDeletionStatusResponseV1:
        asset_id = canonicalize_resource_id(asset_id, resource="Asset")
        with self._uow_factory() as uow:
            asset = uow.assets.get(workspace_id=workspace_id, asset_id=asset_id)
            if asset is None:
                raise NotFoundError(f"Asset {asset_id} was not found")
            if (
                asset.deletion_operation_id is None
                or asset.deletion_reason is None
                or asset.deletion_requested_at is None
            ):
                raise NotFoundError(f"Asset {asset_id} has no deletion operation")
            operation = uow.operations.get(
                asset.deletion_operation_id,
                workspace_id=workspace_id,
            )
            if operation is None:
                raise RuntimeError("Asset deletion Operation binding is missing")
            progress = uow.asset_deletions.list_latest_progress(
                workspace_id=workspace_id,
                operation_id=operation.id,
            )
        return AssetDeletionStatusResponseV1(
            asset_id=asset.id,
            asset_version_id=asset.current_version_id,
            asset_state=asset.status,
            deletion_generation=asset.deletion_generation,
            deletion_reason=asset.deletion_reason,
            requested_at=asset.deletion_requested_at,
            completed_at=asset.deletion_completed_at,
            operation=ValidationOperationSummaryV1(
                id=operation.id,
                state=operation.state,
                target_id=operation.target_id,
                target_version=operation.target_version,
                version=operation.version,
            ),
            progress=[
                AssetDeletionProgressItemV1(
                    component=item.component,
                    state=item.state,
                    observed_count=item.observed_count,
                    converged_count=item.converged_count,
                    error_code=item.error_code,
                    observed_at=item.observed_at,
                )
                for item in progress
            ],
        )
