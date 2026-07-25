"""Typed seams for the Asset Registry application module."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from commercevision_domain import Asset, AssetObject, AssetVersion, UploadSession
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
    ) -> Asset | None: ...

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
    ) -> AssetObject | None: ...


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


AssetUnitOfWorkFactory = Callable[[], AssetUnitOfWorkPort]
