"""Brand Profile drafting, validation, publication, and immutable history."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from commercevision_contracts import (
    BrandColorV1,
    BrandProfileCreateRequestV1,
    BrandProfileDraftV1,
    BrandProfileListResponseV1,
    BrandProfileMemberSelectionV1,
    BrandProfilePublishedMemberV1,
    BrandProfilePublishRequestV1,
    BrandProfileResponseV1,
    BrandProfileUpdateDraftRequestV1,
    BrandProfileValidateRequestV1,
    BrandProfileValidationIssueV1,
    BrandProfileValidationResponseV1,
    BrandProfileVersionListResponseV1,
    BrandProfileVersionResponseV1,
    BrandRuleV1,
)
from commercevision_contracts.events import BrandProfilePublishedPayload, EventType
from commercevision_domain import (
    BrandColor,
    BrandProfile,
    BrandProfileDraft,
    BrandProfileMemberSelection,
    BrandProfilePublishedMember,
    BrandProfileVersion,
    BrandRule,
    ConcurrencyError,
    InvalidDataError,
    NotFoundError,
    RetentionClass,
    RightsDecisionCode,
    UniqueConstraintError,
    evaluate_current_usability,
    validate_workspace_id,
)
from commercevision_domain.messaging import EventEnvelope, OutboxEvent

from .asset_idempotency import canonical_hash, key_hash, workspace_hash
from .asset_ports import IdempotencyRecordPort
from .asset_registry_facts import canonicalize_resource_id
from .brand_profile_cursors import BrandProfileCursorCodec
from .brand_profile_ports import (
    BrandProfileAssetAuthoritySnapshot,
    BrandProfileCurrentAssetSnapshot,
    BrandProfileUnitOfWorkFactory,
    BrandProfileUnitOfWorkPort,
)


class BrandProfilePublicationRejected(InvalidDataError):
    """The exact draft snapshot was not currently publishable."""

    def __init__(self, issues: tuple[BrandProfileValidationIssueV1, ...]) -> None:
        super().__init__("Brand Profile publication validation failed")
        self.issues = issues


@dataclass(frozen=True, slots=True)
class _DraftAssessment:
    validation: BrandProfileValidationResponseV1
    members: tuple[BrandProfilePublishedMember, ...]


def draft_from_contract(draft: BrandProfileDraftV1) -> BrandProfileDraft:
    return BrandProfileDraft(
        rules=tuple(
            BrandRule(
                code=rule.code,
                scope=rule.scope,
                instruction=rule.instruction,
            )
            for rule in draft.rules
        ),
        approved_colors=tuple(
            BrandColor(name=color.name, value=color.value) for color in draft.approved_colors
        ),
        required_marks=tuple(draft.required_marks),
        prohibited_elements=tuple(draft.prohibited_elements),
        tone_constraints=tuple(draft.tone_constraints),
        copy_constraints=tuple(draft.copy_constraints),
        purpose=draft.purpose,
        provider=draft.provider,
        requires_derivative=draft.requires_derivative,
        selected_assets=tuple(
            BrandProfileMemberSelection(
                asset_version_id=selection.asset_version_id,
                role=selection.role,
            )
            for selection in draft.selected_assets
        ),
    )


def draft_to_contract(draft: BrandProfileDraft) -> BrandProfileDraftV1:
    return BrandProfileDraftV1(
        rules=[
            BrandRuleV1(
                code=rule.code,
                scope=rule.scope,
                instruction=rule.instruction,
            )
            for rule in draft.rules
        ],
        approved_colors=[
            BrandColorV1(name=color.name, value=color.value) for color in draft.approved_colors
        ],
        required_marks=list(draft.required_marks),
        prohibited_elements=list(draft.prohibited_elements),
        tone_constraints=list(draft.tone_constraints),
        copy_constraints=list(draft.copy_constraints),
        purpose=draft.purpose,
        provider=draft.provider,
        requires_derivative=draft.requires_derivative,
        selected_assets=[
            BrandProfileMemberSelectionV1(
                asset_version_id=selection.asset_version_id,
                role=selection.role,
            )
            for selection in draft.selected_assets
        ],
    )


class BrandProfileApplicationService:
    """Deep application module for Brand Profile lifecycle and publication."""

    def __init__(
        self,
        uow_factory: BrandProfileUnitOfWorkFactory,
        *,
        cursor_codec: BrandProfileCursorCodec,
    ) -> None:
        self._uow_factory = uow_factory
        self._cursor_codec = cursor_codec

    def create(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        request: BrandProfileCreateRequestV1,
        idempotency_key: str,
        trace_id: str,
    ) -> BrandProfileResponseV1:
        validate_workspace_id(workspace_id)
        identity_digest = canonical_hash(
            {"brand": request.brand, "profile_key": request.profile_key}
        )
        scope = f"brand-profile:create:{workspace_hash(workspace_id)}:{identity_digest}"
        key_digest = key_hash(idempotency_key)
        request_digest = canonical_hash({"actor_id": actor_id, **request.model_dump(mode="json")})
        with self._uow_factory() as uow:
            now = uow.database_now()
            replay = self._claim_idempotency(
                uow=uow,
                scope=scope,
                key_digest=key_digest,
                request_digest=request_digest,
                now=now,
            )
            if replay is not None:
                return self._profile_replay(replay)
            existing = uow.brand_profiles.get_by_key(
                workspace_id=workspace_id,
                brand=request.brand,
                profile_key=request.profile_key,
                for_update=True,
            )
            if existing is not None:
                raise UniqueConstraintError(
                    "Brand Profile brand and profile_key already exist in this workspace"
                )
            profile = BrandProfile.create(
                workspace_id=workspace_id,
                brand=request.brand,
                profile_key=request.profile_key,
                draft=draft_from_contract(request.draft),
                actor_id=actor_id,
                now=now,
            )
            uow.brand_profiles.add(profile)
            response = profile_to_contract(profile)
            self._complete_idempotency(
                uow=uow,
                scope=scope,
                key_digest=key_digest,
                request_digest=request_digest,
                response=response,
            )
            self._add_audit(
                uow=uow,
                profile=profile,
                actor_id=actor_id,
                action="brand-profile.created",
                trace_id=trace_id,
                now=now,
                metadata={"brand": profile.brand, "profile_key": profile.profile_key},
            )
            uow.commit()
            return response

    def get(
        self,
        *,
        workspace_id: str,
        profile_id: str,
    ) -> BrandProfileResponseV1:
        validate_workspace_id(workspace_id)
        profile_id = canonicalize_resource_id(profile_id, resource="Brand Profile")
        with self._uow_factory() as uow:
            profile = uow.brand_profiles.get(
                workspace_id=workspace_id,
                profile_id=profile_id,
            )
            if profile is None:
                raise NotFoundError(f"Brand Profile {profile_id} was not found")
            return profile_to_contract(profile)

    def list_profiles(
        self,
        *,
        workspace_id: str,
        brand: str | None,
        limit: int,
        cursor: str | None,
    ) -> BrandProfileListResponseV1:
        validate_workspace_id(workspace_id)
        if not 1 <= limit <= 100:
            raise ValueError("Brand Profile page limit must be between 1 and 100")
        if brand is not None and (
            not brand
            or len(brand) > 128
            or brand != brand.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in brand)
        ):
            raise ValueError("Brand Profile brand filter is invalid")
        decoded_cursor = (
            self._cursor_codec.decode_profiles(
                cursor,
                workspace_id=workspace_id,
                brand=brand,
            )
            if cursor is not None
            else None
        )
        with self._uow_factory() as uow:
            profiles = uow.brand_profiles.list(
                workspace_id=workspace_id,
                brand=brand,
                cursor=decoded_cursor,
                limit=limit + 1,
            )
        page = profiles[:limit]
        return BrandProfileListResponseV1(
            items=[profile_to_contract(profile) for profile in page],
            next_cursor=(
                self._cursor_codec.encode_profiles(
                    workspace_id=workspace_id,
                    brand=brand,
                    created_at=page[-1].created_at,
                    profile_id=page[-1].id,
                )
                if len(profiles) > limit and page
                else None
            ),
        )

    def update_draft(
        self,
        *,
        workspace_id: str,
        profile_id: str,
        actor_id: str,
        request: BrandProfileUpdateDraftRequestV1,
        idempotency_key: str,
        trace_id: str,
    ) -> BrandProfileResponseV1:
        validate_workspace_id(workspace_id)
        profile_id = canonicalize_resource_id(profile_id, resource="Brand Profile")
        scope = f"brand-profile:update-draft:{workspace_hash(workspace_id)}:{profile_id}"
        key_digest = key_hash(idempotency_key)
        request_digest = canonical_hash({"actor_id": actor_id, **request.model_dump(mode="json")})
        with self._uow_factory() as uow:
            profile = uow.brand_profiles.get(
                workspace_id=workspace_id,
                profile_id=profile_id,
                for_update=True,
            )
            if profile is None:
                raise NotFoundError(f"Brand Profile {profile_id} was not found")
            now = uow.database_now()
            replay = self._claim_idempotency(
                uow=uow,
                scope=scope,
                key_digest=key_digest,
                request_digest=request_digest,
                now=now,
            )
            if replay is not None:
                return self._profile_replay(replay)
            previous_version = profile.version
            profile.update_draft(
                expected_version=request.expected_version,
                draft=draft_from_contract(request.draft),
                actor_id=actor_id,
                now=now,
            )
            uow.brand_profiles.save(profile, expected_version=previous_version)
            response = profile_to_contract(profile)
            self._complete_idempotency(
                uow=uow,
                scope=scope,
                key_digest=key_digest,
                request_digest=request_digest,
                response=response,
            )
            self._add_audit(
                uow=uow,
                profile=profile,
                actor_id=actor_id,
                action="brand-profile.draft-updated",
                trace_id=trace_id,
                now=now,
                metadata={
                    "profile_version": profile.version,
                    "selected_asset_count": len(profile.draft.selected_assets),
                    "rule_count": len(profile.draft.rules),
                },
            )
            uow.commit()
            return response

    def publish(
        self,
        *,
        workspace_id: str,
        profile_id: str,
        actor_id: str,
        request: BrandProfilePublishRequestV1,
        idempotency_key: str,
        trace_id: str,
    ) -> BrandProfileResponseV1:
        validate_workspace_id(workspace_id)
        profile_id = canonicalize_resource_id(profile_id, resource="Brand Profile")
        scope = f"brand-profile:publish:{workspace_hash(workspace_id)}:{profile_id}"
        key_digest = key_hash(idempotency_key)
        request_digest = canonical_hash({"actor_id": actor_id, **request.model_dump(mode="json")})
        with self._uow_factory() as uow:
            profile = uow.brand_profiles.get(
                workspace_id=workspace_id,
                profile_id=profile_id,
                for_update=True,
            )
            if profile is None:
                raise NotFoundError(f"Brand Profile {profile_id} was not found")
            snapshots = uow.brand_profile_assets.lock_for_publication(
                workspace_id=workspace_id,
                selected_version_ids=tuple(
                    selection.asset_version_id for selection in profile.draft.selected_assets
                ),
            )
            now = uow.database_now()
            replay = self._claim_idempotency(
                uow=uow,
                scope=scope,
                key_digest=key_digest,
                request_digest=request_digest,
                now=now,
            )
            if replay is not None:
                self._assert_profile_replay_identity(
                    replay,
                    profile_id=profile.id,
                )
                return profile_to_contract(profile)
            profile.assert_version(request.expected_version)
            assessment = self._assess_draft(
                profile_id=profile.id,
                profile_version=profile.version,
                draft=profile.draft,
                snapshots=snapshots,
                now=now,
            )
            if not assessment.validation.valid:
                raise BrandProfilePublicationRejected(tuple(assessment.validation.issues))
            previous_version = profile.version
            publication = profile.publish(
                expected_version=request.expected_version,
                members=assessment.members,
                actor_id=actor_id,
                now=now,
            )
            uow.brand_profile_publications.add(publication)
            uow.brand_profiles.save(profile, expected_version=previous_version)
            response = profile_to_contract(profile)
            payload = BrandProfilePublishedPayload(
                workspace_id=workspace_id,
                profile_id=profile.id,
                profile_version_id=publication.id,
                profile_version_number=publication.version_number,
                content_sha256=publication.content_sha256,
                member_count=len(publication.members),
                published_by=actor_id,
            )
            uow.outbox.add(
                OutboxEvent(
                    envelope=EventEnvelope.create(
                        event_type=EventType.BRAND_PROFILE_PUBLISHED.value,
                        aggregate_type="BrandProfile",
                        aggregate_id=profile.id,
                        aggregate_version=profile.version,
                        trace_id=trace_id,
                        payload=payload.model_dump(mode="json"),
                        now=now,
                    ),
                    available_at=now,
                    workspace_id=workspace_id,
                )
            )
            self._complete_idempotency(
                uow=uow,
                scope=scope,
                key_digest=key_digest,
                request_digest=request_digest,
                response=response,
            )
            self._add_audit(
                uow=uow,
                profile=profile,
                actor_id=actor_id,
                action="brand-profile.published",
                trace_id=trace_id,
                now=now,
                metadata={
                    "profile_version_id": publication.id,
                    "profile_version_number": publication.version_number,
                    "content_sha256": publication.content_sha256,
                    "member_count": len(publication.members),
                },
            )
            uow.commit()
            return response

    def get_version(
        self,
        *,
        workspace_id: str,
        profile_id: str,
        version_number: int,
    ) -> BrandProfileVersionResponseV1:
        validate_workspace_id(workspace_id)
        profile_id = canonicalize_resource_id(profile_id, resource="Brand Profile")
        if version_number < 1:
            raise NotFoundError("Brand Profile Version was not found")
        with self._uow_factory() as uow:
            profile = uow.brand_profiles.get(
                workspace_id=workspace_id,
                profile_id=profile_id,
            )
            if profile is None:
                raise NotFoundError(f"Brand Profile {profile_id} was not found")
            version = uow.brand_profile_publications.get_version(
                workspace_id=workspace_id,
                profile_id=profile_id,
                version_number=version_number,
            )
            if version is None:
                raise NotFoundError("Brand Profile Version was not found")
            snapshot_batch = uow.brand_profile_assets.current_snapshots(
                workspace_id=workspace_id,
                asset_ids=tuple(member.asset_id for member in version.members),
            )
            return version_to_contract(
                version=version,
                current_snapshots=snapshot_batch.snapshots,
                now=snapshot_batch.decided_at,
            )

    def list_versions(
        self,
        *,
        workspace_id: str,
        profile_id: str,
        limit: int,
        cursor: str | None,
    ) -> BrandProfileVersionListResponseV1:
        validate_workspace_id(workspace_id)
        profile_id = canonicalize_resource_id(profile_id, resource="Brand Profile")
        if not 1 <= limit <= 100:
            raise ValueError("Brand Profile Version page limit must be between 1 and 100")
        decoded_cursor = (
            self._cursor_codec.decode_versions(
                cursor,
                workspace_id=workspace_id,
                profile_id=profile_id,
            )
            if cursor is not None
            else None
        )
        with self._uow_factory() as uow:
            profile = uow.brand_profiles.get(
                workspace_id=workspace_id,
                profile_id=profile_id,
            )
            if profile is None:
                raise NotFoundError(f"Brand Profile {profile_id} was not found")
            versions = uow.brand_profile_publications.list_versions(
                workspace_id=workspace_id,
                profile_id=profile_id,
                cursor=decoded_cursor,
                limit=limit + 1,
            )
            page = versions[:limit]
            if page:
                asset_ids = tuple(
                    dict.fromkeys(member.asset_id for version in page for member in version.members)
                )
                snapshot_batch = uow.brand_profile_assets.current_snapshots(
                    workspace_id=workspace_id,
                    asset_ids=asset_ids,
                )
                items = [
                    version_to_contract(
                        version=version,
                        current_snapshots=snapshot_batch.snapshots,
                        now=snapshot_batch.decided_at,
                    )
                    for version in page
                ]
            else:
                items = []
        return BrandProfileVersionListResponseV1(
            items=items,
            next_cursor=(
                self._cursor_codec.encode_versions(
                    workspace_id=workspace_id,
                    profile_id=profile_id,
                    version_number=page[-1].version_number,
                )
                if len(versions) > limit and page
                else None
            ),
        )

    def validate(
        self,
        *,
        workspace_id: str,
        profile_id: str,
        request: BrandProfileValidateRequestV1,
    ) -> BrandProfileValidationResponseV1:
        validate_workspace_id(workspace_id)
        profile_id = canonicalize_resource_id(profile_id, resource="Brand Profile")
        with self._uow_factory() as uow:
            profile = uow.brand_profiles.get(
                workspace_id=workspace_id,
                profile_id=profile_id,
                for_update=True,
            )
            if profile is None:
                raise NotFoundError(f"Brand Profile {profile_id} was not found")
            profile.assert_version(request.expected_version)
            snapshots = uow.brand_profile_assets.lock_for_publication(
                workspace_id=workspace_id,
                selected_version_ids=tuple(
                    selection.asset_version_id for selection in profile.draft.selected_assets
                ),
            )
            now = uow.database_now()
            return self._assess_draft(
                profile_id=profile.id,
                profile_version=profile.version,
                draft=profile.draft,
                snapshots=snapshots,
                now=now,
            ).validation

    @staticmethod
    def _assess_draft(
        *,
        profile_id: str,
        profile_version: int,
        draft: BrandProfileDraft,
        snapshots: Mapping[str, BrandProfileAssetAuthoritySnapshot],
        now: datetime,
    ) -> _DraftAssessment:
        issues: list[BrandProfileValidationIssueV1] = []
        members: list[BrandProfilePublishedMember] = []
        for ordinal, selection in enumerate(draft.selected_assets):
            snapshot = snapshots.get(selection.asset_version_id)
            if (
                snapshot is None
                or snapshot.asset_version.id != selection.asset_version_id
                or snapshot.asset_version.asset_id != snapshot.asset.id
                or snapshot.asset_version.workspace_id != snapshot.asset.workspace_id
            ):
                issues.append(
                    BrandProfileValidationIssueV1(
                        asset_version_id=selection.asset_version_id,
                        role=selection.role,
                        reason_code="ASSET_VERSION_NOT_FOUND",
                        message="Selected Asset Version was not found in this workspace.",
                    )
                )
                continue
            if snapshot.asset.retention_class != RetentionClass.FOUNDATION:
                issues.append(
                    BrandProfileValidationIssueV1(
                        asset_version_id=selection.asset_version_id,
                        role=selection.role,
                        reason_code="NOT_FOUNDATION_ASSET",
                        message="Brand Profiles may contain only Foundation Assets.",
                    )
                )
                continue
            decision = evaluate_current_usability(
                asset=snapshot.asset,
                rights_record=snapshot.current_rights_record,
                asset_version_id=selection.asset_version_id,
                purpose=draft.purpose,
                provider=draft.provider,
                requires_derivative=draft.requires_derivative,
                decision_time=now,
            )
            if not decision.authorized:
                issues.append(
                    BrandProfileValidationIssueV1(
                        asset_version_id=selection.asset_version_id,
                        role=selection.role,
                        reason_code=decision.reason_code.value,
                        message=(
                            "Selected Foundation Asset is not currently usable: "
                            f"{decision.reason_code.value}."
                        ),
                    )
                )
                continue
            rights_record = snapshot.current_rights_record
            if rights_record is None:
                raise RuntimeError("authorized Brand Profile member has no current Rights Record")
            members.append(
                BrandProfilePublishedMember(
                    ordinal=ordinal,
                    asset_id=snapshot.asset.id,
                    asset_version_id=selection.asset_version_id,
                    role=selection.role,
                    rights_record_id=rights_record.id,
                    rights_record_version=rights_record.version_number,
                )
            )
        validation = BrandProfileValidationResponseV1(
            profile_id=profile_id,
            profile_version=profile_version,
            valid=not issues,
            decided_at=now,
            issues=issues,
        )
        return _DraftAssessment(validation=validation, members=tuple(members))

    @staticmethod
    def _claim_idempotency(
        *,
        uow: BrandProfileUnitOfWorkPort,
        scope: str,
        key_digest: str,
        request_digest: str,
        now: datetime,
    ) -> IdempotencyRecordPort | None:
        record: IdempotencyRecordPort = uow.idempotency.claim(
            scope=scope,
            key_hash=key_digest,
            request_hash=request_digest,
            expires_at=now + timedelta(days=30),
        )
        if record.request_hash != request_digest:
            from commercevision_domain.workflow.errors import IdempotencyConflictError

            raise IdempotencyConflictError(
                "idempotency key was already used with a different request"
            )
        if record.status == "COMPLETED":
            return record
        if record.status != "PENDING":
            raise ConcurrencyError("idempotency record has an unsupported status")
        return None

    @staticmethod
    def _profile_replay(record: IdempotencyRecordPort) -> BrandProfileResponseV1:
        if record.resource_type != "brand-profile" or not isinstance(record.response_data, dict):
            raise ConcurrencyError("idempotency record does not contain a Brand Profile response")
        return BrandProfileResponseV1.model_validate(record.response_data)

    @staticmethod
    def _assert_profile_replay_identity(
        record: IdempotencyRecordPort,
        *,
        profile_id: str,
    ) -> None:
        if (
            record.resource_type != "brand-profile"
            or record.resource_id != profile_id
            or not isinstance(record.response_data, dict)
        ):
            raise ConcurrencyError("idempotency record does not contain the expected Brand Profile")

    @staticmethod
    def _complete_idempotency(
        *,
        uow: BrandProfileUnitOfWorkPort,
        scope: str,
        key_digest: str,
        request_digest: str,
        response: BrandProfileResponseV1,
    ) -> None:
        uow.idempotency.complete(
            scope=scope,
            key_hash=key_digest,
            request_hash=request_digest,
            resource_type="brand-profile",
            resource_id=response.id,
            response_data=response.model_dump(mode="json"),
        )

    @staticmethod
    def _add_audit(
        *,
        uow: BrandProfileUnitOfWorkPort,
        profile: BrandProfile,
        actor_id: str,
        action: str,
        trace_id: str,
        now: datetime,
        metadata: dict[str, object],
    ) -> None:
        uow.audit.add(
            workspace_id=profile.workspace_id,
            actor_type="USER",
            actor_id=actor_id,
            action=action,
            resource_type="brand-profile",
            resource_id=profile.id,
            trace_id=trace_id,
            metadata=metadata,
            created_at=now,
            expires_at=now + timedelta(days=180),
        )


def profile_to_contract(profile: BrandProfile) -> BrandProfileResponseV1:
    return BrandProfileResponseV1(
        id=profile.id,
        workspace_id=profile.workspace_id,
        brand=profile.brand,
        profile_key=profile.profile_key,
        state=profile.state,
        draft=draft_to_contract(profile.draft),
        current_version_id=profile.current_version_id,
        current_version_number=profile.current_version_number,
        version=profile.version,
        stale_at=profile.stale_at,
        created_by=profile.created_by,
        created_at=profile.created_at,
        updated_by=profile.updated_by,
        updated_at=profile.updated_at,
    )


def version_to_contract(
    *,
    version: BrandProfileVersion,
    current_snapshots: Mapping[str, BrandProfileCurrentAssetSnapshot],
    now: datetime,
) -> BrandProfileVersionResponseV1:
    members: list[BrandProfilePublishedMemberV1] = []
    for member in version.members:
        snapshot = current_snapshots.get(member.asset_id)
        if snapshot is None or snapshot.asset.workspace_id != version.workspace_id:
            currently_usable = False
            reason_code = RightsDecisionCode.ASSET_NOT_AVAILABLE
            current_rights_record_id = None
            current_rights_record_version = None
        else:
            decision = evaluate_current_usability(
                asset=snapshot.asset,
                rights_record=snapshot.current_rights_record,
                asset_version_id=member.asset_version_id,
                purpose=version.draft.purpose,
                provider=version.draft.provider,
                requires_derivative=version.draft.requires_derivative,
                decision_time=now,
            )
            currently_usable = decision.authorized
            reason_code = decision.reason_code
            current_rights_record_id = decision.rights_record_id
            current_rights_record_version = decision.rights_record_version
        members.append(
            BrandProfilePublishedMemberV1(
                ordinal=member.ordinal,
                asset_id=member.asset_id,
                asset_version_id=member.asset_version_id,
                role=member.role,
                published_rights_record_id=member.rights_record_id,
                published_rights_record_version=member.rights_record_version,
                currently_usable=currently_usable,
                current_reason_code=reason_code,
                current_rights_record_id=current_rights_record_id,
                current_rights_record_version=current_rights_record_version,
                decided_at=now,
            )
        )
    return BrandProfileVersionResponseV1(
        id=version.id,
        workspace_id=version.workspace_id,
        profile_id=version.profile_id,
        version_number=version.version_number,
        draft=draft_to_contract(version.draft),
        content_sha256=version.content_sha256,
        published_by=version.published_by,
        published_at=version.published_at,
        members=members,
    )
