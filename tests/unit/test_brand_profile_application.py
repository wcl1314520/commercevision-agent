from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from commercevision_application.brand_profile_cursors import BrandProfileCursorCodec
from commercevision_application.brand_profile_invalidation import (
    BrandProfileInvalidationApplicationService,
)
from commercevision_application.brand_profile_ports import (
    BrandProfileAssetAuthoritySnapshot,
    BrandProfileCurrentAssetSnapshot,
    BrandProfileCurrentAssetSnapshotBatch,
    BrandProfileInvalidationCandidate,
)
from commercevision_application.brand_profiles import (
    BrandProfileApplicationService,
    BrandProfilePublicationRejected,
)
from commercevision_contracts import (
    BrandColorV1,
    BrandProfileCreateRequestV1,
    BrandProfileDraftV1,
    BrandProfileMemberSelectionV1,
    BrandProfilePublishRequestV1,
    BrandProfileUpdateDraftRequestV1,
    BrandProfileValidateRequestV1,
    BrandRuleV1,
)
from commercevision_domain import (
    Asset,
    AssetKind,
    AssetState,
    AssetVersion,
    BrandProfile,
    BrandProfileMemberRole,
    BrandProfilePublishedMember,
    BrandProfileState,
    BrandRuleScope,
    NotFoundError,
    RetentionClass,
    RightsDecisionCode,
    RightsRecord,
    RightsRecordDecision,
)
from commercevision_domain.workflow.errors import IdempotencyConflictError

ASSET_ID = "018f5f4d-7c11-7d11-8a11-111111111111"
PROFILE_ID = "018f5f4d-7c11-7d11-8a11-222222222222"
PROFILE_VERSION_ID = "018f5f4d-7c11-7d11-8a11-333333333333"
ASSET_VERSION_ID = "018f5f4d-7c11-7d11-8a11-444444444444"
RIGHTS_ID = "018f5f4d-7c11-7d11-8a11-555555555555"
REVOKED_RIGHTS_ID = "018f5f4d-7c11-7d11-8a11-888888888888"
NOW = datetime(2026, 7, 30, 8, 0, tzinfo=UTC)
DATABASE_NOW = NOW + timedelta(minutes=10)
CURSOR_SECRET = "brand-profile-cursor-test-secret-000000000001"


def _cursor_codec() -> BrandProfileCursorCodec:
    return BrandProfileCursorCodec(
        current_key_id="brand-profile-current",
        current_secret=CURSOR_SECRET,
        max_age_seconds=86_400,
        future_skew_seconds=30,
        clock=lambda: NOW,
    )


class _InvalidationRepository:
    def __init__(
        self,
        *,
        candidate: BrandProfileInvalidationCandidate,
        events: list[str],
    ) -> None:
        self.candidate = candidate
        self.events = events
        self.calls: list[dict[str, object]] = []
        self.saved: list[tuple[BrandProfile, int]] = []

    def lock_current_profiles_referencing_asset(
        self,
        *,
        workspace_id: str,
        asset_id: str,
    ) -> tuple[BrandProfileInvalidationCandidate, ...]:
        self.events.append("profile-lock")
        self.calls.append(
            {
                "workspace_id": workspace_id,
                "asset_id": asset_id,
            }
        )
        return (self.candidate,)

    def save(self, profile: BrandProfile, *, expected_version: int) -> None:
        self.events.append("profile-save")
        self.saved.append((profile, expected_version))


class _InvalidationAssetAuthority:
    def __init__(
        self,
        *,
        snapshot: BrandProfileCurrentAssetSnapshot | None,
        events: list[str],
    ) -> None:
        self.snapshot = snapshot
        self.events = events

    def lock_current_asset(
        self,
        *,
        workspace_id: str,
        asset_id: str,
    ) -> BrandProfileCurrentAssetSnapshot | None:
        self.events.append("asset-lock")
        if self.snapshot is None:
            return None
        if self.snapshot.asset.workspace_id != workspace_id or self.snapshot.asset.id != asset_id:
            return None
        return self.snapshot


class _InvalidationUnitOfWork:
    def __init__(
        self,
        *,
        candidate: BrandProfileInvalidationCandidate,
        snapshot: BrandProfileCurrentAssetSnapshot | None,
    ) -> None:
        self.events: list[str] = []
        self.brand_profiles = _InvalidationRepository(
            candidate=candidate,
            events=self.events,
        )
        self.brand_profile_publications = SimpleNamespace()
        self.brand_profile_assets = _InvalidationAssetAuthority(
            snapshot=snapshot,
            events=self.events,
        )
        self.idempotency = SimpleNamespace()
        self.outbox = SimpleNamespace()
        self.audit = SimpleNamespace()
        self.commits = 0

    def __enter__(self) -> _InvalidationUnitOfWork:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def commit(self) -> None:
        self.events.append("commit")
        self.commits += 1

    def database_now(self) -> datetime:
        self.events.append("database-now")
        return DATABASE_NOW


def _draft() -> BrandProfileDraftV1:
    return BrandProfileDraftV1(
        rules=[
            BrandRuleV1(
                code="logo.clear-space",
                scope=BrandRuleScope.VISUAL,
                instruction="Keep one mark-width of clear space.",
            )
        ],
        approved_colors=[BrandColorV1(name="Primary", value="#0A5CFF")],
        required_marks=["CommerceVision wordmark"],
        prohibited_elements=["Competitor marks"],
        tone_constraints=["Confident"],
        copy_constraints=["No unverified superlatives"],
        purpose="commerce.image-generation",
        provider="alibaba",
        requires_derivative=True,
        selected_assets=[
            BrandProfileMemberSelectionV1(
                asset_version_id=ASSET_VERSION_ID,
                role=BrandProfileMemberRole.LOGO,
            )
        ],
    )


def _asset(
    *,
    retention_class: RetentionClass = RetentionClass.FOUNDATION,
    status: AssetState = AssetState.AVAILABLE,
    rights_record_id: str = RIGHTS_ID,
) -> Asset:
    return Asset(
        id=ASSET_ID,
        workspace_id="workspace-a",
        retention_class=retention_class,
        kind=AssetKind.IMAGE,
        workflow_id=(
            "018f5f4d-7c11-7d11-8a11-777777777777"
            if retention_class == RetentionClass.TASK
            else None
        ),
        product_id=None,
        sku_id=None,
        status=status,
        block_reason=("RIGHTS_REVOKED" if status == AssetState.BLOCKED else None),
        current_version_id=ASSET_VERSION_ID,
        retention_deadline=(
            NOW + timedelta(hours=1) if retention_class == RetentionClass.TASK else None
        ),
        version=3,
        created_at=NOW - timedelta(days=1),
        updated_at=NOW,
        current_rights_record_id=rights_record_id,
    )


def _asset_version() -> AssetVersion:
    return AssetVersion(
        id=ASSET_VERSION_ID,
        workspace_id="workspace-a",
        asset_id=ASSET_ID,
        version_number=1,
        upload_session_id="018f5f4d-7c11-7d11-8a11-666666666666",
        filename="logo.png",
        sha256="a" * 64,
        byte_size=1024,
        declared_mime="image/png",
        detected_mime="image/png",
        image_format="PNG",
        width=128,
        height=128,
        frame_count=1,
        category="brand",
        role="logo",
        integrity_policy_version="integrity-v1",
        validation_policy_version="validation-v1",
        created_at=NOW - timedelta(days=1),
    )


def _rights(
    *,
    rights_id: str = RIGHTS_ID,
    version_number: int = 1,
    decision: RightsRecordDecision = RightsRecordDecision.GRANT,
) -> RightsRecord:
    return RightsRecord(
        id=rights_id,
        workspace_id="workspace-a",
        asset_id=ASSET_ID,
        asset_version_id=ASSET_VERSION_ID,
        version_number=version_number,
        decision=decision,
        owner_reference="owner:commercevision",
        source="contract",
        license_reference="license:brand",
        allowed_uses=(
            frozenset({"commerce.image-generation"})
            if decision == RightsRecordDecision.GRANT
            else frozenset()
        ),
        allowed_providers=(
            frozenset({"alibaba"}) if decision == RightsRecordDecision.GRANT else frozenset()
        ),
        derivative_allowed=decision == RightsRecordDecision.GRANT,
        public_demo_allowed=False,
        evidence_reference="evidence://rights",
        terms_sha256="b" * 64,
        valid_from=NOW - timedelta(days=1),
        valid_until=None,
        perpetual=True,
        supersedes_record_id=None,
        created_by="rights-admin",
        created_at=NOW - timedelta(days=1),
    )


class _IdentityRepository:
    def __init__(self) -> None:
        self.items: dict[str, BrandProfile] = {}
        self.get_calls: list[tuple[str, str]] = []
        self.list_calls: list[dict[str, object]] = []

    def add(self, profile: BrandProfile) -> None:
        self.items[profile.id] = profile

    def get(
        self,
        *,
        workspace_id: str,
        profile_id: str,
        for_update: bool = False,
    ) -> BrandProfile | None:
        self.get_calls.append((workspace_id, profile_id))
        profile = self.items.get(profile_id)
        return profile if profile is not None and profile.workspace_id == workspace_id else None

    def get_by_key(
        self,
        *,
        workspace_id: str,
        brand: str,
        profile_key: str,
        for_update: bool = False,
    ) -> BrandProfile | None:
        return next(
            (
                profile
                for profile in self.items.values()
                if profile.workspace_id == workspace_id
                and profile.brand == brand
                and profile.profile_key == profile_key
            ),
            None,
        )

    def save(self, profile: BrandProfile, *, expected_version: int) -> None:
        persisted = self.items.get(profile.id)
        if persisted is not profile:
            raise AssertionError("fake repository must retain aggregate identity")
        if profile.version != expected_version + 1:
            raise AssertionError("save must use the pre-mutation optimistic version")

    def list(
        self,
        *,
        workspace_id: str,
        brand: str | None,
        cursor: tuple[datetime, str] | None,
        limit: int,
    ) -> tuple[BrandProfile, ...]:
        self.list_calls.append(
            {
                "workspace_id": workspace_id,
                "brand": brand,
                "cursor": cursor,
                "limit": limit,
            }
        )
        items = [
            profile
            for profile in self.items.values()
            if profile.workspace_id == workspace_id and (brand is None or profile.brand == brand)
        ]
        items.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        if cursor is not None:
            items = [item for item in items if (item.created_at, item.id) < cursor]
        return tuple(items[:limit])


class _PublicationRepository:
    def __init__(self) -> None:
        self.items: list[object] = []
        self.list_calls: list[dict[str, object]] = []

    def add(self, version: object) -> None:
        self.items.append(version)

    def get_version(
        self,
        *,
        workspace_id: str,
        profile_id: str,
        version_number: int,
    ):
        return next(
            (
                version
                for version in self.items
                if version.workspace_id == workspace_id
                and version.profile_id == profile_id
                and version.version_number == version_number
            ),
            None,
        )

    def list_versions(
        self,
        *,
        workspace_id: str,
        profile_id: str,
        cursor: int | None,
        limit: int,
    ) -> tuple[object, ...]:
        self.list_calls.append(
            {
                "workspace_id": workspace_id,
                "profile_id": profile_id,
                "cursor": cursor,
                "limit": limit,
            }
        )
        items = [
            version
            for version in self.items
            if version.workspace_id == workspace_id
            and version.profile_id == profile_id
            and (cursor is None or version.version_number < cursor)
        ]
        items.sort(key=lambda version: version.version_number, reverse=True)
        return tuple(items[:limit])


class _IdempotencyRepository:
    def __init__(self) -> None:
        self.records: dict[tuple[str, str], SimpleNamespace] = {}

    def claim(
        self,
        *,
        scope: str,
        key_hash: str,
        request_hash: str,
        expires_at: datetime,
    ) -> SimpleNamespace:
        key = (scope, key_hash)
        record = self.records.get(key)
        if record is None:
            record = SimpleNamespace(
                request_hash=request_hash,
                resource_type="pending",
                resource_id="pending",
                response_data=None,
                status="PENDING",
            )
            self.records[key] = record
        return record

    def complete(
        self,
        *,
        scope: str,
        key_hash: str,
        request_hash: str,
        resource_type: str,
        resource_id: str,
        response_data: dict[str, object],
    ) -> None:
        record = self.records[(scope, key_hash)]
        record.request_hash = request_hash
        record.resource_type = resource_type
        record.resource_id = resource_id
        record.response_data = response_data
        record.status = "COMPLETED"


class _RecordingRepository:
    def __init__(self) -> None:
        self.items: list[object] = []

    def add(self, item: object = None, **kwargs: object) -> None:
        self.items.append(item if item is not None else kwargs)


class _AuthorityRepository:
    def __init__(
        self,
        *,
        asset: Asset | None,
        rights: RightsRecord | None,
    ) -> None:
        self.asset = asset
        self.rights = rights
        self.lock_calls: list[tuple[str, ...]] = []
        self.events: list[str] = []

    def lock_for_publication(
        self,
        *,
        workspace_id: str,
        selected_version_ids: tuple[str, ...],
    ) -> dict[str, BrandProfileAssetAuthoritySnapshot]:
        self.events.append("asset-lock")
        self.lock_calls.append(selected_version_ids)
        if self.asset is None or self.asset.workspace_id != workspace_id:
            return {}
        return {
            ASSET_VERSION_ID: BrandProfileAssetAuthoritySnapshot(
                asset=self.asset,
                asset_version=_asset_version(),
                current_rights_record=self.rights,
            )
        }

    def current_snapshots(
        self,
        *,
        workspace_id: str,
        asset_ids: tuple[str, ...],
    ) -> BrandProfileCurrentAssetSnapshotBatch:
        if self.asset is None or self.asset.workspace_id != workspace_id:
            return BrandProfileCurrentAssetSnapshotBatch(
                decided_at=NOW,
                snapshots={},
            )
        return BrandProfileCurrentAssetSnapshotBatch(
            decided_at=NOW,
            snapshots={
                ASSET_ID: BrandProfileCurrentAssetSnapshot(
                    asset=self.asset,
                    current_rights_record=self.rights,
                )
            },
        )


class _ApplicationUnitOfWork:
    def __init__(
        self,
        *,
        profile: BrandProfile | None,
        asset: Asset | None,
        rights: RightsRecord | None,
    ) -> None:
        self.brand_profiles = _IdentityRepository()
        if profile is not None:
            self.brand_profiles.items[profile.id] = profile
        self.brand_profile_publications = _PublicationRepository()
        self.brand_profile_assets = _AuthorityRepository(asset=asset, rights=rights)
        self.events = self.brand_profile_assets.events
        self.idempotency = _IdempotencyRepository()
        self.outbox = _RecordingRepository()
        self.audit = _RecordingRepository()
        self.commits = 0
        self.database_now_calls = 0

    def __enter__(self) -> _ApplicationUnitOfWork:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def database_now(self) -> datetime:
        self.events.append("database-now")
        self.database_now_calls += 1
        return NOW

    def commit(self) -> None:
        self.commits += 1


def _application_service(
    unit_of_work: _ApplicationUnitOfWork,
) -> BrandProfileApplicationService:
    return BrandProfileApplicationService(
        lambda: unit_of_work,
        cursor_codec=_cursor_codec(),
    )


def _profile() -> BrandProfile:
    from commercevision_application.brand_profiles import draft_from_contract

    profile = BrandProfile.create(
        workspace_id="workspace-a",
        brand="CommerceVision",
        profile_key="cn-primary",
        draft=draft_from_contract(_draft()),
        actor_id="brand-admin",
        now=NOW - timedelta(minutes=5),
    )
    profile.id = PROFILE_ID
    return profile


def _published_invalidation_candidate() -> BrandProfileInvalidationCandidate:
    profile = _profile()
    publication = profile.publish(
        expected_version=1,
        members=(
            BrandProfilePublishedMember(
                ordinal=0,
                asset_id=ASSET_ID,
                asset_version_id=ASSET_VERSION_ID,
                role=BrandProfileMemberRole.LOGO,
                rights_record_id=RIGHTS_ID,
                rights_record_version=1,
            ),
        ),
        actor_id="brand-admin",
        now=NOW,
    )
    return BrandProfileInvalidationCandidate(
        profile=profile,
        publication=publication,
    )


def test_invalidation_locks_heads_then_authority_and_uses_database_time() -> None:
    candidate = _published_invalidation_candidate()
    unit_of_work = _InvalidationUnitOfWork(
        candidate=candidate,
        snapshot=BrandProfileCurrentAssetSnapshot(
            asset=_asset(
                status=AssetState.BLOCKED,
                rights_record_id=REVOKED_RIGHTS_ID,
            ),
            current_rights_record=_rights(
                rights_id=REVOKED_RIGHTS_ID,
                version_number=2,
                decision=RightsRecordDecision.REVOKE,
            ),
        ),
    )
    service = BrandProfileInvalidationApplicationService(lambda: unit_of_work)

    result = service.invalidate_asset(
        workspace_id="workspace-a",
        asset_id=ASSET_ID,
        occurred_at=NOW - timedelta(days=7),
    )

    assert result.matched_profiles == 1
    assert result.marked_profiles == 1
    assert unit_of_work.brand_profiles.calls == [
        {
            "workspace_id": "workspace-a",
            "asset_id": ASSET_ID,
        }
    ]
    assert unit_of_work.events == [
        "profile-lock",
        "asset-lock",
        "database-now",
        "profile-save",
        "commit",
    ]
    assert unit_of_work.brand_profiles.saved == [(candidate.profile, 2)]
    assert candidate.profile.state == BrandProfileState.NEEDS_REPUBLISH
    assert candidate.profile.stale_at == DATABASE_NOW
    assert unit_of_work.commits == 1

    replay = service.invalidate_asset(
        workspace_id="workspace-a",
        asset_id=ASSET_ID,
        occurred_at=NOW - timedelta(days=7),
    )

    assert replay.matched_profiles == 1
    assert replay.marked_profiles == 0
    assert unit_of_work.brand_profiles.saved == [(candidate.profile, 2)]
    assert unit_of_work.commits == 2


def test_delayed_replay_does_not_invalidate_a_head_with_live_pinned_authority() -> None:
    candidate = _published_invalidation_candidate()
    unit_of_work = _InvalidationUnitOfWork(
        candidate=candidate,
        snapshot=BrandProfileCurrentAssetSnapshot(
            asset=_asset(),
            current_rights_record=_rights(),
        ),
    )
    service = BrandProfileInvalidationApplicationService(lambda: unit_of_work)

    result = service.invalidate_asset(
        workspace_id="workspace-a",
        asset_id=ASSET_ID,
        occurred_at=NOW - timedelta(days=7),
    )

    assert result.matched_profiles == 1
    assert result.marked_profiles == 0
    assert candidate.profile.state == BrandProfileState.ACTIVE
    assert unit_of_work.brand_profiles.saved == []
    assert unit_of_work.events == [
        "profile-lock",
        "asset-lock",
        "database-now",
        "commit",
    ]


def test_validate_reports_non_foundation_member_without_mutating_profile() -> None:
    unit_of_work = _ApplicationUnitOfWork(
        profile=_profile(),
        asset=_asset(retention_class=RetentionClass.TASK),
        rights=_rights(),
    )
    service = _application_service(unit_of_work)

    response = service.validate(
        workspace_id="workspace-a",
        profile_id=PROFILE_ID,
        request=BrandProfileValidateRequestV1(expected_version=1),
    )

    assert response.valid is False
    assert [issue.reason_code for issue in response.issues] == ["NOT_FOUNDATION_ASSET"]
    assert unit_of_work.brand_profile_assets.lock_calls == [(ASSET_VERSION_ID,)]
    assert unit_of_work.events == ["asset-lock", "database-now"]
    assert unit_of_work.database_now_calls == 1
    assert unit_of_work.commits == 0


def test_create_is_idempotent_and_returns_only_identity_and_head_facts() -> None:
    unit_of_work = _ApplicationUnitOfWork(
        profile=None,
        asset=_asset(),
        rights=_rights(),
    )
    service = _application_service(unit_of_work)
    request = BrandProfileCreateRequestV1(
        brand="CommerceVision",
        profile_key="cn-primary",
        draft=_draft(),
    )

    created = service.create(
        workspace_id="workspace-a",
        actor_id="brand-admin",
        request=request,
        idempotency_key="create-profile-key",
        trace_id="trace-create",
    )
    replayed = service.create(
        workspace_id="workspace-a",
        actor_id="brand-admin",
        request=request,
        idempotency_key="create-profile-key",
        trace_id="trace-create-replay",
    )

    assert replayed == created
    assert created.current_version_id is None
    assert created.current_version_number == 0
    assert not hasattr(created, "current_usability")
    assert len(unit_of_work.brand_profiles.items) == 1
    assert unit_of_work.commits == 1


def test_create_idempotency_key_is_reusable_across_distinct_scoped_identities() -> None:
    unit_of_work = _ApplicationUnitOfWork(
        profile=None,
        asset=_asset(),
        rights=_rights(),
    )
    service = _application_service(unit_of_work)
    requests = (
        (
            "workspace-a",
            BrandProfileCreateRequestV1(
                brand="CommerceVision",
                profile_key="cn-primary",
                draft=_draft(),
            ),
        ),
        (
            "workspace-a",
            BrandProfileCreateRequestV1(
                brand="CommerceVision",
                profile_key="cn-secondary",
                draft=_draft(),
            ),
        ),
        (
            "workspace-a",
            BrandProfileCreateRequestV1(
                brand="CommerceVision Labs",
                profile_key="cn-primary",
                draft=_draft(),
            ),
        ),
        (
            "workspace-b",
            BrandProfileCreateRequestV1(
                brand="CommerceVision",
                profile_key="cn-primary",
                draft=_draft(),
            ),
        ),
    )

    created = [
        service.create(
            workspace_id=workspace_id,
            actor_id="brand-admin",
            request=request,
            idempotency_key="shared-create-key",
            trace_id=f"trace-{index}",
        )
        for index, (workspace_id, request) in enumerate(requests)
    ]

    assert len({profile.id for profile in created}) == len(requests)
    assert len(unit_of_work.brand_profiles.items) == len(requests)
    assert len(unit_of_work.idempotency.records) == len(requests)
    assert all(len(scope) <= 160 for scope, _ in unit_of_work.idempotency.records)


def test_create_idempotency_key_conflicts_for_a_changed_request_on_same_identity() -> None:
    unit_of_work = _ApplicationUnitOfWork(
        profile=None,
        asset=_asset(),
        rights=_rights(),
    )
    service = _application_service(unit_of_work)
    request = BrandProfileCreateRequestV1(
        brand="CommerceVision",
        profile_key="cn-primary",
        draft=_draft(),
    )
    changed_request = request.model_copy(
        update={
            "draft": request.draft.model_copy(update={"tone_constraints": ["Quietly confident"]})
        }
    )
    service.create(
        workspace_id="workspace-a",
        actor_id="brand-admin",
        request=request,
        idempotency_key="create-conflict-key",
        trace_id="trace-create-original",
    )

    with pytest.raises(IdempotencyConflictError):
        service.create(
            workspace_id="workspace-a",
            actor_id="brand-admin",
            request=changed_request,
            idempotency_key="create-conflict-key",
            trace_id="trace-create-changed",
        )


def test_update_draft_applies_optimistic_version_and_is_idempotent() -> None:
    profile = _profile()
    unit_of_work = _ApplicationUnitOfWork(
        profile=profile,
        asset=_asset(),
        rights=_rights(),
    )
    service = _application_service(unit_of_work)
    changed_draft = _draft().model_copy(update={"tone_constraints": ["Quietly confident"]})
    request = BrandProfileUpdateDraftRequestV1(
        expected_version=1,
        draft=changed_draft,
    )

    updated = service.update_draft(
        workspace_id="workspace-a",
        profile_id=PROFILE_ID,
        actor_id="brand-admin",
        request=request,
        idempotency_key="update-profile-key",
        trace_id="trace-update",
    )
    replayed = service.update_draft(
        workspace_id="workspace-a",
        profile_id=PROFILE_ID,
        actor_id="brand-admin",
        request=request,
        idempotency_key="update-profile-key",
        trace_id="trace-update-replay",
    )

    assert updated.version == 2
    assert updated.draft.tone_constraints == ["Quietly confident"]
    assert replayed == updated
    assert profile.version == 2
    assert unit_of_work.commits == 1


def test_publish_atomically_freezes_exact_asset_and_rights_references() -> None:
    profile = _profile()
    unit_of_work = _ApplicationUnitOfWork(
        profile=profile,
        asset=_asset(),
        rights=_rights(),
    )
    service = _application_service(unit_of_work)
    request = BrandProfilePublishRequestV1(expected_version=1)

    published = service.publish(
        workspace_id="workspace-a",
        profile_id=PROFILE_ID,
        actor_id="brand-admin",
        request=request,
        idempotency_key="publish-profile-key",
        trace_id="trace-publish",
    )

    assert published.current_version_number == 1
    assert published.current_version_id is not None
    assert published.version == 2
    assert len(unit_of_work.brand_profile_publications.items) == 1
    version = unit_of_work.brand_profile_publications.items[0]
    member = version.members[0]
    assert member.asset_id == ASSET_ID
    assert member.asset_version_id == ASSET_VERSION_ID
    assert member.rights_record_id == RIGHTS_ID
    assert member.rights_record_version == 1
    assert len(version.content_sha256) == 64
    event = unit_of_work.outbox.items[0]
    assert event.envelope.event_type == "brand-profile.published"
    assert event.envelope.aggregate_id == PROFILE_ID
    assert event.envelope.aggregate_version == 2
    assert event.envelope.payload["profile_version_id"] == version.id
    assert event.envelope.payload["member_count"] == 1
    assert unit_of_work.events == ["asset-lock", "database-now"]
    assert unit_of_work.database_now_calls == 1
    assert unit_of_work.commits == 1


def test_publish_rejects_invalid_member_without_partial_publication() -> None:
    profile = _profile()
    unit_of_work = _ApplicationUnitOfWork(
        profile=profile,
        asset=_asset(retention_class=RetentionClass.TASK),
        rights=_rights(),
    )
    service = _application_service(unit_of_work)

    with pytest.raises(BrandProfilePublicationRejected) as captured:
        service.publish(
            workspace_id="workspace-a",
            profile_id=PROFILE_ID,
            actor_id="brand-admin",
            request=BrandProfilePublishRequestV1(expected_version=1),
            idempotency_key="publish-invalid-key",
            trace_id="trace-publish-invalid",
        )

    assert [issue.reason_code for issue in captured.value.issues] == ["NOT_FOUNDATION_ASSET"]
    assert profile.current_version_id is None
    assert unit_of_work.brand_profile_publications.items == []
    assert unit_of_work.outbox.items == []
    assert unit_of_work.commits == 0


def test_publish_idempotency_replay_rehydrates_mutable_profile_state() -> None:
    profile = _profile()
    unit_of_work = _ApplicationUnitOfWork(
        profile=profile,
        asset=_asset(),
        rights=_rights(),
    )
    service = _application_service(unit_of_work)
    request = BrandProfilePublishRequestV1(expected_version=1)
    published = service.publish(
        workspace_id="workspace-a",
        profile_id=PROFILE_ID,
        actor_id="brand-admin",
        request=request,
        idempotency_key="publish-rehydrate-key",
        trace_id="trace-publish-rehydrate",
    )
    assert published.state == BrandProfileState.ACTIVE
    assert profile.current_version_id is not None
    profile.mark_needs_republish(
        expected_current_version_id=profile.current_version_id,
        stale_at=NOW + timedelta(seconds=1),
    )

    replayed = service.publish(
        workspace_id="workspace-a",
        profile_id=PROFILE_ID,
        actor_id="brand-admin",
        request=request,
        idempotency_key="publish-rehydrate-key",
        trace_id="trace-publish-rehydrate-replay",
    )

    assert replayed.state == BrandProfileState.NEEDS_REPUBLISH
    assert replayed.version == profile.version
    assert unit_of_work.commits == 1


def test_historical_version_keeps_publication_facts_but_rechecks_current_rights() -> None:
    profile = _profile()
    unit_of_work = _ApplicationUnitOfWork(
        profile=profile,
        asset=_asset(),
        rights=_rights(),
    )
    service = _application_service(unit_of_work)
    service.publish(
        workspace_id="workspace-a",
        profile_id=PROFILE_ID,
        actor_id="brand-admin",
        request=BrandProfilePublishRequestV1(expected_version=1),
        idempotency_key="publish-history-key",
        trace_id="trace-publish-history",
    )
    unit_of_work.brand_profile_assets.asset = _asset(
        status=AssetState.BLOCKED,
        rights_record_id=REVOKED_RIGHTS_ID,
    )
    unit_of_work.brand_profile_assets.rights = _rights(
        rights_id=REVOKED_RIGHTS_ID,
        version_number=2,
        decision=RightsRecordDecision.REVOKE,
    )

    response = service.get_version(
        workspace_id="workspace-a",
        profile_id=PROFILE_ID,
        version_number=1,
    )

    member = response.members[0]
    assert member.published_rights_record_id == RIGHTS_ID
    assert member.published_rights_record_version == 1
    assert member.currently_usable is False
    assert member.current_reason_code == RightsDecisionCode.RIGHTS_REVOKED
    assert member.current_rights_record_id == REVOKED_RIGHTS_ID
    assert member.current_rights_record_version == 2
    assert unit_of_work.database_now_calls == 1


def test_profile_list_uses_opaque_monotonic_cursor_and_workspace_scope() -> None:
    newest = _profile()
    newest.created_at = NOW
    newest.updated_at = NOW
    older = _profile()
    older.id = "018f5f4d-7c11-7d11-8a11-999999999999"
    older.created_at = NOW - timedelta(minutes=1)
    older.updated_at = NOW - timedelta(minutes=1)
    unit_of_work = _ApplicationUnitOfWork(
        profile=newest,
        asset=_asset(),
        rights=_rights(),
    )
    unit_of_work.brand_profiles.items[older.id] = older
    service = _application_service(unit_of_work)

    first = service.list_profiles(
        workspace_id="workspace-a",
        brand="CommerceVision",
        limit=1,
        cursor=None,
    )
    # Draft edits between pages must not move an unseen identity across the
    # keyset boundary. The cursor is based on immutable creation identity.
    older.updated_at = NOW + timedelta(minutes=1)
    second = service.list_profiles(
        workspace_id="workspace-a",
        brand="CommerceVision",
        limit=1,
        cursor=first.next_cursor,
    )

    assert [item.id for item in first.items] == [newest.id]
    assert first.next_cursor is not None
    assert ":" not in first.next_cursor
    assert [item.id for item in second.items] == [older.id]
    assert second.next_cursor is None
    with pytest.raises(NotFoundError):
        service.get(workspace_id="workspace-b", profile_id=newest.id)


def test_profile_cursor_rejects_scope_reuse_and_tampering_before_repository_access() -> None:
    codec = _cursor_codec()
    token = codec.encode_profiles(
        workspace_id="workspace-a",
        brand="CommerceVision",
        created_at=NOW,
        profile_id=PROFILE_ID,
    )
    version, key_id, payload, signature = token.split(".")
    tampered_signature = ("A" if signature[0] != "A" else "B") + signature[1:]
    invalid_requests = (
        {
            "workspace_id": "workspace-b",
            "brand": "CommerceVision",
            "cursor": token,
        },
        {
            "workspace_id": "workspace-a",
            "brand": "Other Brand",
            "cursor": token,
        },
        {
            "workspace_id": "workspace-a",
            "brand": "CommerceVision",
            "cursor": ".".join((version, key_id, payload, tampered_signature)),
        },
        {
            "workspace_id": "workspace-a",
            "brand": "CommerceVision",
            "cursor": token + ".extra",
        },
        {
            "workspace_id": "workspace-a",
            "brand": "CommerceVision",
            "cursor": "eyJraW5kIjoicHJvZmlsZSJ9",
        },
    )
    unit_of_work = _ApplicationUnitOfWork(
        profile=_profile(),
        asset=_asset(),
        rights=_rights(),
    )
    service = BrandProfileApplicationService(
        lambda: unit_of_work,
        cursor_codec=codec,
    )

    for request in invalid_requests:
        with pytest.raises(ValueError, match=r"^Brand Profile cursor is invalid$"):
            service.list_profiles(limit=20, **request)

    assert unit_of_work.brand_profiles.list_calls == []


def test_version_cursor_is_profile_bound_and_decoded_before_repository_access() -> None:
    codec = _cursor_codec()
    token = codec.encode_versions(
        workspace_id="workspace-a",
        profile_id=PROFILE_ID,
        version_number=2,
    )
    unit_of_work = _ApplicationUnitOfWork(
        profile=_profile(),
        asset=_asset(),
        rights=_rights(),
    )
    service = BrandProfileApplicationService(
        lambda: unit_of_work,
        cursor_codec=codec,
    )

    for workspace_id, profile_id in (
        ("workspace-b", PROFILE_ID),
        ("workspace-a", "018f5f4d-7c11-7d11-8a11-999999999999"),
    ):
        with pytest.raises(ValueError, match=r"^Brand Profile cursor is invalid$"):
            service.list_versions(
                workspace_id=workspace_id,
                profile_id=profile_id,
                limit=20,
                cursor=token,
            )

    assert unit_of_work.brand_profiles.get_calls == []
    response = service.list_versions(
        workspace_id="workspace-a",
        profile_id=PROFILE_ID,
        limit=20,
        cursor=token,
    )
    assert response.items == []
    assert unit_of_work.brand_profile_publications.list_calls == [
        {
            "workspace_id": "workspace-a",
            "profile_id": PROFILE_ID,
            "cursor": 2,
            "limit": 21,
        }
    ]


def test_version_list_issues_authenticated_cursor_for_immutable_history() -> None:
    profile = _profile()
    members = (
        BrandProfilePublishedMember(
            ordinal=0,
            asset_id=ASSET_ID,
            asset_version_id=ASSET_VERSION_ID,
            role=BrandProfileMemberRole.LOGO,
            rights_record_id=RIGHTS_ID,
            rights_record_version=1,
        ),
    )
    first_version = profile.publish(
        expected_version=1,
        members=members,
        actor_id="brand-admin",
        now=NOW - timedelta(minutes=2),
    )
    profile.update_draft(
        expected_version=2,
        draft=profile.draft,
        actor_id="brand-admin",
        now=NOW - timedelta(minutes=1),
    )
    second_version = profile.publish(
        expected_version=3,
        members=members,
        actor_id="brand-admin",
        now=NOW,
    )
    unit_of_work = _ApplicationUnitOfWork(
        profile=profile,
        asset=_asset(),
        rights=_rights(),
    )
    unit_of_work.brand_profile_publications.items.extend((first_version, second_version))
    service = BrandProfileApplicationService(
        lambda: unit_of_work,
        cursor_codec=_cursor_codec(),
    )

    first_page = service.list_versions(
        workspace_id="workspace-a",
        profile_id=PROFILE_ID,
        limit=1,
        cursor=None,
    )
    second_page = service.list_versions(
        workspace_id="workspace-a",
        profile_id=PROFILE_ID,
        limit=1,
        cursor=first_page.next_cursor,
    )

    assert [version.version_number for version in first_page.items] == [2]
    assert first_page.next_cursor is not None
    assert first_page.next_cursor.startswith("v1.brand-profile-current.")
    assert [version.version_number for version in second_page.items] == [1]
    assert second_page.next_cursor is None
