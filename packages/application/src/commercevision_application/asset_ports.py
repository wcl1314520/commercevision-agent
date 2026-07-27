"""Typed seams for the Asset Registry application module."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from commercevision_domain import (
    Asset,
    AssetObject,
    AssetValidationResult,
    AssetVersion,
    RightsRecord,
    UploadSession,
    ValidationStage,
)
from commercevision_domain.messaging import OutboxEvent
from commercevision_domain.operations import DurableOperation


class IdempotencyRecordPort(Protocol):
    request_hash: str
    resource_type: str
    resource_id: str
    response_data: dict[str, object] | None
    status: str


@dataclass(frozen=True, slots=True)
class WorkflowRetentionFacts:
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class CurrentUsabilitySnapshot:
    asset: Asset
    rights_record: RightsRecord | None
    database_now: datetime


@dataclass(frozen=True, slots=True)
class RightsScanClaim:
    asset: Asset
    rights_record: RightsRecord
    database_now: datetime


@dataclass(frozen=True, slots=True)
class AssetRetentionCommitExpiredError(Exception):
    observed_at: datetime
    retention_deadline: datetime

    def __str__(self) -> str:
        return "Asset retention expired at the database commit boundary"


class UploadSessionRepositoryPort(Protocol):
    def add(self, upload_session: UploadSession) -> None: ...

    def get(
        self,
        *,
        workspace_id: str,
        upload_session_id: str,
        for_update: bool = False,
    ) -> UploadSession | None: ...

    def save(self, upload_session: UploadSession) -> None: ...

    def claim_expired(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> list[UploadSession]: ...


class AssetRepositoryPort(Protocol):
    def add_quarantined(
        self,
        *,
        asset: Asset,
        asset_version: AssetVersion,
        object_fact: AssetObject,
    ) -> None: ...

    def get(
        self,
        *,
        workspace_id: str,
        asset_id: str,
        for_update: bool = False,
    ) -> Asset | None: ...

    def save_asset(self, asset: Asset) -> None: ...

    def get_version(
        self,
        *,
        workspace_id: str,
        asset_version_id: str,
    ) -> AssetVersion | None: ...

    def get_version_by_upload_session(
        self,
        *,
        workspace_id: str,
        upload_session_id: str,
    ) -> AssetVersion | None: ...

    def get_object(
        self,
        *,
        workspace_id: str,
        asset_version_id: str,
        role: str = "ORIGINAL",
        for_update: bool = False,
    ) -> AssetObject | None: ...

    def add_object(self, object_fact: AssetObject) -> None: ...

    def save_object(self, object_fact: AssetObject) -> None: ...

    def add_validation_result(self, result: AssetValidationResult) -> None: ...

    def get_validation_result(
        self,
        *,
        workspace_id: str,
        asset_version_id: str,
        operation_id: str,
        attempt_number: int,
        stage: ValidationStage,
        validator_name: str,
        validator_version: str,
        policy_version: str,
    ) -> AssetValidationResult | None: ...

    def list_validation_results(
        self,
        *,
        workspace_id: str,
        asset_version_id: str,
    ) -> list[AssetValidationResult]: ...

    def add_rights_record(self, rights_record: RightsRecord) -> None: ...

    def get_rights_record(
        self,
        *,
        workspace_id: str,
        rights_record_id: str,
    ) -> RightsRecord | None: ...

    def get_current_usability_snapshot(
        self,
        *,
        workspace_id: str,
        asset_id: str,
    ) -> CurrentUsabilitySnapshot | None: ...

    def list_rights_records(
        self,
        *,
        workspace_id: str,
        asset_id: str,
        before_version: int | None,
        limit: int,
    ) -> list[RightsRecord]: ...

    def claim_expired_rights(
        self,
        *,
        limit: int,
    ) -> list[RightsScanClaim]: ...

    def claim_activatable_rights(
        self,
        *,
        limit: int,
    ) -> list[RightsScanClaim]: ...


class AssetAssociationPort(Protocol):
    def workflow_retention_facts(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
    ) -> WorkflowRetentionFacts | None: ...

    def product_exists(self, *, workspace_id: str, product_id: str) -> bool: ...

    def sku_exists(
        self,
        *,
        workspace_id: str,
        product_id: str,
        sku_id: str,
    ) -> bool: ...


class AssetIdempotencyPort(Protocol):
    def claim(
        self,
        *,
        scope: str,
        key_hash: str,
        request_hash: str,
        expires_at: datetime,
    ) -> IdempotencyRecordPort: ...

    def complete(
        self,
        *,
        scope: str,
        key_hash: str,
        request_hash: str,
        resource_type: str,
        resource_id: str,
        response_data: dict[str, object],
    ) -> None: ...

    def mark_pending(
        self,
        *,
        scope: str,
        key_hash: str,
        request_hash: str,
        resource_type: str,
        resource_id: str,
        response_data: dict[str, object],
    ) -> None: ...


class AssetOperationPort(Protocol):
    def add(self, operation: DurableOperation) -> None: ...

    def get(
        self,
        operation_id: str,
        *,
        workspace_id: str | None = None,
        for_update: bool = False,
    ) -> DurableOperation | None: ...


class AssetOutboxPort(Protocol):
    def add(self, event: OutboxEvent) -> None: ...


class AssetAuditPort(Protocol):
    def add(
        self,
        *,
        workspace_id: str,
        actor_type: str,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        trace_id: str,
        metadata: dict[str, object],
        created_at: datetime,
        expires_at: datetime,
    ) -> None: ...


class AssetUnitOfWorkPort(Protocol):
    upload_sessions: UploadSessionRepositoryPort
    assets: AssetRepositoryPort
    associations: AssetAssociationPort
    idempotency: AssetIdempotencyPort
    operations: AssetOperationPort
    outbox: AssetOutboxPort
    audit: AssetAuditPort

    def __enter__(self) -> AssetUnitOfWorkPort: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None: ...

    def commit(self) -> None: ...

    def commit_before_retention_deadline(
        self,
        *,
        workspace_id: str,
        asset_id: str,
        retention_deadline: datetime,
        clock: Callable[[], datetime],
    ) -> None: ...

    def commit_rights_mutation(
        self,
        *,
        workspace_id: str,
        asset_id: str,
        retention_deadline: datetime | None,
        available_rights_record_id: str | None,
        clock: Callable[[], datetime],
    ) -> None: ...


AssetUnitOfWorkFactory = Callable[[], AssetUnitOfWorkPort]
