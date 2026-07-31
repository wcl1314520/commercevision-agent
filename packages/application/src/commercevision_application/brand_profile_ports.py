"""Narrow persistence seams for Brand Profile publication and invalidation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from commercevision_domain import (
    Asset,
    AssetVersion,
    BrandProfile,
    BrandProfileVersion,
    RightsRecord,
)

from .ports import (
    AuditRepositoryPort,
    IdempotencyRepositoryPort,
    OutboxRepositoryPort,
)


@dataclass(frozen=True, slots=True)
class BrandProfileAssetAuthoritySnapshot:
    """Publication-time facts locked under one database transaction."""

    asset: Asset
    asset_version: AssetVersion
    current_rights_record: RightsRecord | None


@dataclass(frozen=True, slots=True)
class BrandProfileCurrentAssetSnapshot:
    """Current Asset and Rights facts used to annotate immutable history."""

    asset: Asset
    current_rights_record: RightsRecord | None


@dataclass(frozen=True, slots=True)
class BrandProfileCurrentAssetSnapshotBatch:
    """One database-time decision boundary over shared-locked authority rows."""

    decided_at: datetime
    snapshots: Mapping[str, BrandProfileCurrentAssetSnapshot]


@dataclass(frozen=True, slots=True)
class BrandProfileInvalidationCandidate:
    """An ACTIVE head locked together with its exact immutable publication."""

    profile: BrandProfile
    publication: BrandProfileVersion


class BrandProfileIdentityRepositoryPort(Protocol):
    def add(self, profile: BrandProfile) -> None: ...

    def get(
        self,
        *,
        workspace_id: str,
        profile_id: str,
        for_update: bool = False,
    ) -> BrandProfile | None: ...

    def get_by_key(
        self,
        *,
        workspace_id: str,
        brand: str,
        profile_key: str,
        for_update: bool = False,
    ) -> BrandProfile | None: ...

    def save(self, profile: BrandProfile, *, expected_version: int) -> None: ...

    def list(
        self,
        *,
        workspace_id: str,
        brand: str | None,
        cursor: tuple[datetime, str] | None,
        limit: int,
    ) -> tuple[BrandProfile, ...]: ...

    def lock_current_profiles_referencing_asset(
        self,
        *,
        workspace_id: str,
        asset_id: str,
    ) -> tuple[BrandProfileInvalidationCandidate, ...]: ...


class BrandProfilePublicationRepositoryPort(Protocol):
    def add(self, version: BrandProfileVersion) -> None: ...

    def get_version(
        self,
        *,
        workspace_id: str,
        profile_id: str,
        version_number: int,
    ) -> BrandProfileVersion | None: ...

    def list_versions(
        self,
        *,
        workspace_id: str,
        profile_id: str,
        cursor: int | None,
        limit: int,
    ) -> tuple[BrandProfileVersion, ...]: ...


class BrandProfileAssetAuthorityPort(Protocol):
    def lock_for_publication(
        self,
        *,
        workspace_id: str,
        selected_version_ids: tuple[str, ...],
    ) -> Mapping[str, BrandProfileAssetAuthoritySnapshot]: ...

    def current_snapshots(
        self,
        *,
        workspace_id: str,
        asset_ids: tuple[str, ...],
    ) -> BrandProfileCurrentAssetSnapshotBatch: ...

    def lock_current_asset(
        self,
        *,
        workspace_id: str,
        asset_id: str,
    ) -> BrandProfileCurrentAssetSnapshot | None: ...

    def lock_asset_deletion_lineage(
        self,
        *,
        workspace_id: str,
        asset_id: str,
    ) -> Asset | None: ...


class BrandProfileUnitOfWorkPort(Protocol):
    brand_profiles: BrandProfileIdentityRepositoryPort
    brand_profile_publications: BrandProfilePublicationRepositoryPort
    brand_profile_assets: BrandProfileAssetAuthorityPort
    idempotency: IdempotencyRepositoryPort
    outbox: OutboxRepositoryPort
    audit: AuditRepositoryPort

    def __enter__(self) -> BrandProfileUnitOfWorkPort: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None: ...

    def database_now(self) -> datetime: ...

    def commit(self) -> None: ...


BrandProfileUnitOfWorkFactory = Callable[[], BrandProfileUnitOfWorkPort]
