"""Event-driven Brand Profile staleness convergence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from commercevision_domain import (
    Asset,
    AssetState,
    BrandProfilePublishedMember,
    RetentionClass,
    evaluate_current_usability,
    validate_workspace_id,
)

from .asset_registry_facts import canonicalize_resource_id
from .brand_profile_ports import (
    BrandProfileCurrentAssetSnapshot,
    BrandProfileInvalidationCandidate,
    BrandProfileUnitOfWorkFactory,
)


@dataclass(frozen=True, slots=True)
class BrandProfileInvalidationResult:
    matched_profiles: int
    marked_profiles: int


class BrandProfileDeletionLineageError(RuntimeError):
    """The deletion observation contradicts locked Asset aggregate facts."""

    reason = "asset_deletion_lineage_mismatch"

    def __init__(self) -> None:
        super().__init__("Asset deletion lineage does not match current authority")


class BrandProfileInvalidationPort(Protocol):
    """Inbound seam consumed by event-driven runtimes."""

    def invalidate_asset(
        self,
        *,
        workspace_id: str,
        asset_id: str,
        occurred_at: datetime,
    ) -> BrandProfileInvalidationResult: ...

    def invalidate_foundation_asset_deletion(
        self,
        *,
        workspace_id: str,
        asset_id: str,
        asset_version_id: str,
        deletion_generation: int,
        occurred_at: datetime,
    ) -> BrandProfileInvalidationResult: ...


class BrandProfileInvalidationApplicationService:
    """Marks active heads stale without coupling Asset Rights to Brand Profiles."""

    def __init__(self, uow_factory: BrandProfileUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def invalidate_asset(
        self,
        *,
        workspace_id: str,
        asset_id: str,
        occurred_at: datetime,
    ) -> BrandProfileInvalidationResult:
        validate_workspace_id(workspace_id)
        asset_id = canonicalize_resource_id(asset_id, resource="Asset")
        if occurred_at.tzinfo is None or occurred_at.utcoffset() != timedelta(0):
            raise ValueError("occurred_at must be timezone-aware UTC")

        with self._uow_factory() as uow:
            candidates = uow.brand_profiles.lock_current_profiles_referencing_asset(
                workspace_id=workspace_id,
                asset_id=asset_id,
            )
            current = uow.brand_profile_assets.lock_current_asset(
                workspace_id=workspace_id,
                asset_id=asset_id,
            )
            # The event timestamp is causal audit metadata. Authority and stale_at
            # are decided against one database clock only after every mutable row
            # participating in the decision has been locked.
            decided_at = uow.database_now()
            marked_profiles = 0
            for candidate in candidates:
                if self._has_live_pinned_authority(
                    candidate=candidate,
                    current=current,
                    asset_id=asset_id,
                    decided_at=decided_at,
                ):
                    continue
                profile = candidate.profile
                expected_version = profile.version
                if not profile.mark_needs_republish(
                    expected_current_version_id=candidate.publication.id,
                    stale_at=decided_at,
                ):
                    continue
                uow.brand_profiles.save(profile, expected_version=expected_version)
                marked_profiles += 1
            uow.commit()
        return BrandProfileInvalidationResult(
            matched_profiles=len(candidates),
            marked_profiles=marked_profiles,
        )

    def invalidate_foundation_asset_deletion(
        self,
        *,
        workspace_id: str,
        asset_id: str,
        asset_version_id: str,
        deletion_generation: int,
        occurred_at: datetime,
    ) -> BrandProfileInvalidationResult:
        """Invalidate one exact deleted Asset generation without trusting the event."""

        validate_workspace_id(workspace_id)
        asset_id = canonicalize_resource_id(asset_id, resource="Asset")
        asset_version_id = canonicalize_resource_id(
            asset_version_id,
            resource="Asset Version",
        )
        if (
            not isinstance(deletion_generation, int)
            or isinstance(deletion_generation, bool)
            or deletion_generation < 1
        ):
            raise ValueError("deletion_generation must be a positive integer")
        if occurred_at.tzinfo is None or occurred_at.utcoffset() != timedelta(0):
            raise ValueError("occurred_at must be timezone-aware UTC")

        with self._uow_factory() as uow:
            candidates = uow.brand_profiles.lock_current_profiles_referencing_asset(
                workspace_id=workspace_id,
                asset_id=asset_id,
            )
            asset = uow.brand_profile_assets.lock_asset_deletion_lineage(
                workspace_id=workspace_id,
                asset_id=asset_id,
            )
            if asset is None or asset.workspace_id != workspace_id or asset.id != asset_id:
                raise BrandProfileDeletionLineageError

            # A later aggregate generation is authoritative evidence that this
            # observation was superseded. It must not invalidate a replacement
            # Asset Version, even when an old publication still references the id.
            if asset.version > deletion_generation:
                uow.commit()
                return BrandProfileInvalidationResult(
                    matched_profiles=len(candidates),
                    marked_profiles=0,
                )
            self._assert_current_deletion_lineage(
                asset=asset,
                asset_version_id=asset_version_id,
                deletion_generation=deletion_generation,
            )

            exact_candidates = tuple(
                candidate
                for candidate in candidates
                if any(
                    member.asset_version_id == asset_version_id
                    for member in self._members_for_asset(
                        candidate=candidate,
                        asset_id=asset_id,
                    )
                )
            )
            decided_at = uow.database_now()
            marked_profiles = 0
            for candidate in exact_candidates:
                profile = candidate.profile
                expected_version = profile.version
                if not profile.mark_needs_republish(
                    expected_current_version_id=candidate.publication.id,
                    stale_at=decided_at,
                ):
                    continue
                uow.brand_profiles.save(profile, expected_version=expected_version)
                marked_profiles += 1
            uow.commit()
        return BrandProfileInvalidationResult(
            matched_profiles=len(exact_candidates),
            marked_profiles=marked_profiles,
        )

    @staticmethod
    def _assert_current_deletion_lineage(
        *,
        asset: Asset,
        asset_version_id: str,
        deletion_generation: int,
    ) -> None:
        if (
            asset.version != deletion_generation
            or asset.current_version_id != asset_version_id
            or asset.retention_class != RetentionClass.FOUNDATION
            or asset.status != AssetState.DELETED
        ):
            raise BrandProfileDeletionLineageError

    @staticmethod
    def _members_for_asset(
        *,
        candidate: BrandProfileInvalidationCandidate,
        asset_id: str,
    ) -> tuple[BrandProfilePublishedMember, ...]:
        profile = candidate.profile
        publication = candidate.publication
        if (
            profile.current_version_id != publication.id
            or profile.current_version_number != publication.version_number
            or publication.profile_id != profile.id
            or publication.workspace_id != profile.workspace_id
        ):
            raise RuntimeError("locked Brand Profile head does not match its publication")
        members = tuple(member for member in publication.members if member.asset_id == asset_id)
        if not members:
            raise RuntimeError("invalidation candidate does not reference the requested Asset")
        return members

    @staticmethod
    def _has_live_pinned_authority(
        *,
        candidate: BrandProfileInvalidationCandidate,
        current: BrandProfileCurrentAssetSnapshot | None,
        asset_id: str,
        decided_at: datetime,
    ) -> bool:
        profile = candidate.profile
        publication = candidate.publication
        members = BrandProfileInvalidationApplicationService._members_for_asset(
            candidate=candidate,
            asset_id=asset_id,
        )
        if (
            current is None
            or current.asset.workspace_id != profile.workspace_id
            or current.asset.id != asset_id
            or current.asset.retention_class != RetentionClass.FOUNDATION
        ):
            return False
        rights = current.current_rights_record
        for member in members:
            if (
                rights is None
                or rights.id != member.rights_record_id
                or rights.version_number != member.rights_record_version
            ):
                return False
            decision = evaluate_current_usability(
                asset=current.asset,
                rights_record=rights,
                asset_version_id=member.asset_version_id,
                purpose=publication.draft.purpose,
                provider=publication.draft.provider,
                requires_derivative=publication.draft.requires_derivative,
                decision_time=decided_at,
            )
            if not decision.authorized:
                return False
        return True
