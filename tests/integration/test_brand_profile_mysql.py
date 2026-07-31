from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import UTC, datetime, timedelta
from functools import partial
from time import sleep

import pytest
from commercevision_application.brand_profile_cursors import BrandProfileCursorCodec
from commercevision_application.brand_profile_invalidation import (
    BrandProfileInvalidationApplicationService,
)
from commercevision_application.brand_profiles import (
    BrandProfileApplicationService,
    BrandProfilePublicationRejected,
)
from commercevision_contracts import BrandProfilePublishRequestV1
from commercevision_domain import (
    BrandColor,
    BrandProfile,
    BrandProfileDraft,
    BrandProfileMemberRole,
    BrandProfileMemberSelection,
    BrandProfilePublishedMember,
    BrandProfileState,
    BrandRule,
    BrandRuleScope,
    ConcurrencyError,
    ReferenceConstraintError,
    new_uuid7,
)
from commercevision_persistence import SqlAlchemyBrandProfileUnitOfWork
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.integration


def _draft(*, asset_version_id: str) -> BrandProfileDraft:
    return BrandProfileDraft(
        rules=(
            BrandRule(
                code="logo-clearspace",
                scope=BrandRuleScope.VISUAL,
                instruction="Keep one mark-width of clear space.",
            ),
        ),
        approved_colors=(BrandColor(name="Primary", value="#123456"),),
        required_marks=("Registered mark",),
        prohibited_elements=("Competitor marks",),
        tone_constraints=("Precise",),
        copy_constraints=("No unsupported claims",),
        purpose="RETRIEVAL",
        provider="milvus",
        requires_derivative=False,
        selected_assets=(
            BrandProfileMemberSelection(
                asset_version_id=asset_version_id,
                role=BrandProfileMemberRole.LOGO,
            ),
        ),
    )


def _profile(
    *,
    workspace_id: str,
    brand: str,
    profile_key: str,
    asset_version_id: str,
    now: datetime,
) -> BrandProfile:
    return BrandProfile.create(
        workspace_id=workspace_id,
        brand=brand,
        profile_key=profile_key,
        draft=_draft(asset_version_id=asset_version_id),
        actor_id="brand-admin",
        now=now,
    )


def _seed_foundation_asset(
    integration_database,
    *,
    workspace_id: str,
    now: datetime,
    allowed_use: str = "RETRIEVAL",
    allowed_provider: str = "milvus",
    derivative_allowed: bool = True,
    bind_rights_to_asset_version: bool = True,
    inject_foreign_permissions: bool = False,
    rights_valid_until: datetime | None = None,
) -> tuple[str, str, str]:
    asset_id = new_uuid7()
    asset_version_id = new_uuid7()
    rights_record_id = new_uuid7()
    stored_now = now.replace(tzinfo=None)
    with integration_database.engine.begin() as connection:
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        connection.execute(
            text(
                "INSERT INTO assets "
                "(id, workspace_id, retention_class, asset_kind, workflow_id, "
                "product_id, sku_id, status, block_reason, current_version_id, "
                "current_rights_record_id, retention_deadline, version, created_at, updated_at) "
                "VALUES (:asset_id, :workspace_id, 'FOUNDATION', 'IMAGE', NULL, "
                "NULL, NULL, 'AVAILABLE', NULL, :asset_version_id, "
                ":rights_record_id, NULL, 3, :now, :now)"
            ),
            {
                "asset_id": asset_id,
                "workspace_id": workspace_id,
                "asset_version_id": asset_version_id,
                "rights_record_id": rights_record_id,
                "now": stored_now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO asset_versions "
                "(id, workspace_id, asset_id, version_number, upload_session_id, "
                "filename, sha256, byte_size, declared_mime, detected_mime, "
                "image_format, width, height, frame_count, category, role, "
                "integrity_policy_version, validation_policy_version, "
                "validation_transfer_policy_version, "
                "validation_transfer_policy_snapshot_sha256, created_at) VALUES "
                "(:version_id, :workspace_id, :asset_id, 1, :upload_id, "
                "'logo.png', :sha, 128, 'image/png', 'image/png', 'PNG', "
                "64, 64, 1, 'BRAND', 'LOGO', 'integrity-v1', 'validation-v1', "
                "'transfer-v1', :transfer_sha, :now)"
            ),
            {
                "version_id": asset_version_id,
                "workspace_id": workspace_id,
                "asset_id": asset_id,
                "upload_id": new_uuid7(),
                "sha": "a" * 64,
                "transfer_sha": "b" * 64,
                "now": stored_now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO rights_records "
                "(id, workspace_id, asset_id, asset_version_id, version_number, "
                "decision, owner_reference, source, license_reference, "
                "derivative_allowed, public_demo_allowed, evidence_reference, "
                "terms_sha256, valid_from, valid_until, perpetual, "
                "supersedes_record_id, created_by, created_at, permissions_sealed_at) VALUES "
                "(:rights_id, :workspace_id, :asset_id, :rights_asset_version_id, "
                "1, 'GRANT', "
                "'owner', 'contract', 'license-1', :derivative_allowed, 0, "
                "'evidence://license-1', :sha, :valid_from, :valid_until, "
                ":perpetual, NULL, "
                "'rights-admin', :now, NULL)"
            ),
            {
                "rights_id": rights_record_id,
                "workspace_id": workspace_id,
                "asset_id": asset_id,
                "rights_asset_version_id": (
                    asset_version_id if bind_rights_to_asset_version else None
                ),
                "derivative_allowed": derivative_allowed,
                "sha": "c" * 64,
                "valid_from": (now - timedelta(days=1)).replace(tzinfo=None),
                "valid_until": (
                    rights_valid_until.replace(tzinfo=None)
                    if rights_valid_until is not None
                    else None
                ),
                "perpetual": rights_valid_until is None,
                "now": stored_now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO rights_record_uses "
                "(workspace_id, asset_id, rights_record_id, allowed_use, created_at) "
                "VALUES (:workspace_id, :asset_id, :rights_id, :allowed_use, :now)"
            ),
            {
                "workspace_id": workspace_id,
                "asset_id": asset_id,
                "rights_id": rights_record_id,
                "allowed_use": allowed_use,
                "now": stored_now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO rights_record_providers "
                "(workspace_id, asset_id, rights_record_id, allowed_provider, created_at) "
                "VALUES (:workspace_id, :asset_id, :rights_id, :allowed_provider, :now)"
            ),
            {
                "workspace_id": workspace_id,
                "asset_id": asset_id,
                "rights_id": rights_record_id,
                "allowed_provider": allowed_provider,
                "now": stored_now,
            },
        )
        if inject_foreign_permissions:
            connection.execute(
                text(
                    "INSERT INTO rights_record_uses "
                    "(workspace_id, asset_id, rights_record_id, allowed_use, created_at) "
                    "VALUES ('foreign-workspace', :asset_id, :rights_id, "
                    "'FOREIGN_USE', :now)"
                ),
                {
                    "asset_id": asset_id,
                    "rights_id": rights_record_id,
                    "now": stored_now,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO rights_record_providers "
                    "(workspace_id, asset_id, rights_record_id, allowed_provider, created_at) "
                    "VALUES (:workspace_id, :foreign_asset_id, :rights_id, "
                    "'foreign-provider', :now)"
                ),
                {
                    "workspace_id": workspace_id,
                    "foreign_asset_id": new_uuid7(),
                    "rights_id": rights_record_id,
                    "now": stored_now,
                },
            )
        connection.execute(
            text("UPDATE rights_records SET permissions_sealed_at = :now WHERE id = :rights_id"),
            {"rights_id": rights_record_id, "now": stored_now},
        )
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
    return asset_id, asset_version_id, rights_record_id


def _publish(
    integration_database,
    *,
    profile: BrandProfile,
    asset_id: str,
    asset_version_id: str,
    rights_record_id: str,
    now: datetime,
) -> str:
    member = BrandProfilePublishedMember(
        ordinal=0,
        asset_id=asset_id,
        asset_version_id=asset_version_id,
        role=BrandProfileMemberRole.LOGO,
        rights_record_id=rights_record_id,
        rights_record_version=1,
    )
    with SqlAlchemyBrandProfileUnitOfWork(integration_database.session_factory) as uow:
        loaded = uow.brand_profiles.get(
            workspace_id=profile.workspace_id,
            profile_id=profile.id,
            for_update=True,
        )
        assert loaded is not None
        publication = loaded.publish(
            expected_version=loaded.version,
            members=(member,),
            actor_id="brand-admin",
            now=now,
        )
        uow.brand_profile_publications.add(publication)
        uow.brand_profiles.save(loaded, expected_version=loaded.version - 1)
        uow.commit()
    return publication.id


def _replace_current_rights(
    integration_database,
    *,
    workspace_id: str,
    asset_id: str,
    asset_version_id: str,
    supersedes_record_id: str,
    now: datetime,
) -> str:
    rights_record_id = new_uuid7()
    stored_now = now.replace(tzinfo=None)
    with integration_database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO rights_records "
                "(id, workspace_id, asset_id, asset_version_id, version_number, "
                "decision, owner_reference, source, license_reference, "
                "derivative_allowed, public_demo_allowed, evidence_reference, "
                "terms_sha256, valid_from, valid_until, perpetual, "
                "supersedes_record_id, created_by, created_at, permissions_sealed_at) VALUES "
                "(:rights_id, :workspace_id, :asset_id, :version_id, 2, 'GRANT', "
                "'owner', 'replacement-contract', 'license-2', 1, 0, "
                "'evidence://license-2', :sha, :valid_from, NULL, 1, "
                ":supersedes_record_id, 'rights-admin', :now, NULL)"
            ),
            {
                "rights_id": rights_record_id,
                "workspace_id": workspace_id,
                "asset_id": asset_id,
                "version_id": asset_version_id,
                "sha": "d" * 64,
                "valid_from": (now - timedelta(seconds=1)).replace(tzinfo=None),
                "supersedes_record_id": supersedes_record_id,
                "now": stored_now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO rights_record_uses "
                "(workspace_id, asset_id, rights_record_id, allowed_use, created_at) "
                "VALUES (:workspace_id, :asset_id, :rights_id, 'RETRIEVAL', :now)"
            ),
            {
                "workspace_id": workspace_id,
                "asset_id": asset_id,
                "rights_id": rights_record_id,
                "now": stored_now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO rights_record_providers "
                "(workspace_id, asset_id, rights_record_id, allowed_provider, created_at) "
                "VALUES (:workspace_id, :asset_id, :rights_id, 'milvus', :now)"
            ),
            {
                "workspace_id": workspace_id,
                "asset_id": asset_id,
                "rights_id": rights_record_id,
                "now": stored_now,
            },
        )
        connection.execute(
            text("UPDATE rights_records SET permissions_sealed_at = :now WHERE id = :rights_id"),
            {"rights_id": rights_record_id, "now": stored_now},
        )
        connection.execute(
            text(
                "UPDATE assets SET current_rights_record_id = :rights_id, "
                "version = version + 1, updated_at = :now "
                "WHERE workspace_id = :workspace_id AND id = :asset_id"
            ),
            {
                "rights_id": rights_record_id,
                "workspace_id": workspace_id,
                "asset_id": asset_id,
                "now": stored_now,
            },
        )
    return rights_record_id


def test_brand_profile_identity_round_trips_with_binary_keys_and_cas(
    integration_database,
) -> None:
    now = datetime(2026, 7, 30, 9, 0, 0, 123456, tzinfo=UTC)
    _, asset_version_id, _ = _seed_foundation_asset(
        integration_database,
        workspace_id="brand-workspace",
        now=now,
    )
    profile = _profile(
        workspace_id="brand-workspace",
        brand="Acme",
        profile_key="Default",
        asset_version_id=asset_version_id,
        now=now,
    )
    case_distinct = _profile(
        workspace_id="brand-workspace",
        brand="acme",
        profile_key="default",
        asset_version_id=asset_version_id,
        now=now,
    )

    with SqlAlchemyBrandProfileUnitOfWork(integration_database.session_factory) as uow:
        uow.brand_profiles.add(profile)
        uow.brand_profiles.add(case_distinct)
        uow.commit()

    with SqlAlchemyBrandProfileUnitOfWork(integration_database.session_factory) as uow:
        loaded = uow.brand_profiles.get_by_key(
            workspace_id="brand-workspace",
            brand="Acme",
            profile_key="Default",
        )
        assert loaded == profile
        assert (
            uow.brand_profiles.get(
                workspace_id="other-workspace",
                profile_id=profile.id,
            )
            is None
        )
        profiles = uow.brand_profiles.list(
            workspace_id="brand-workspace",
            brand=None,
            cursor=None,
            limit=10,
        )
        assert {item.id for item in profiles} == {profile.id, case_distinct.id}

    with SqlAlchemyBrandProfileUnitOfWork(integration_database.session_factory) as first:
        first_copy = first.brand_profiles.get(
            workspace_id=profile.workspace_id,
            profile_id=profile.id,
        )
    with SqlAlchemyBrandProfileUnitOfWork(integration_database.session_factory) as second:
        stale_copy = second.brand_profiles.get(
            workspace_id=profile.workspace_id,
            profile_id=profile.id,
        )
    assert first_copy is not None and stale_copy is not None
    first_copy.update_draft(
        expected_version=1,
        draft=first_copy.draft,
        actor_id="editor-a",
        now=now + timedelta(seconds=1),
    )
    stale_copy.update_draft(
        expected_version=1,
        draft=stale_copy.draft,
        actor_id="editor-b",
        now=now + timedelta(seconds=2),
    )
    with SqlAlchemyBrandProfileUnitOfWork(integration_database.session_factory) as uow:
        uow.brand_profiles.save(first_copy, expected_version=1)
        uow.commit()
    with (
        SqlAlchemyBrandProfileUnitOfWork(integration_database.session_factory) as uow,
        pytest.raises(ConcurrencyError),
    ):
        uow.brand_profiles.save(stale_copy, expected_version=1)

    with integration_database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE brand_profiles "
                "SET draft_json = JSON_SET(draft_json, '$.purpose', 'OTHER_USE') "
                "WHERE id = :profile_id"
            ),
            {"profile_id": profile.id},
        )
    with (
        SqlAlchemyBrandProfileUnitOfWork(integration_database.session_factory) as uow,
        pytest.raises(RuntimeError, match="draft checksum"),
    ):
        uow.brand_profiles.get(
            workspace_id=profile.workspace_id,
            profile_id=profile.id,
        )


def test_publication_locks_authority_and_persists_exact_immutable_members(
    integration_database,
) -> None:
    now = datetime(2026, 7, 30, 10, 0, 0, 654321, tzinfo=UTC)
    asset_id, asset_version_id, rights_record_id = _seed_foundation_asset(
        integration_database,
        workspace_id="publish-workspace",
        now=now,
    )
    profile = _profile(
        workspace_id="publish-workspace",
        brand="Acme",
        profile_key="generation",
        asset_version_id=asset_version_id,
        now=now,
    )
    with SqlAlchemyBrandProfileUnitOfWork(integration_database.session_factory) as uow:
        uow.brand_profiles.add(profile)
        uow.commit()

    with SqlAlchemyBrandProfileUnitOfWork(integration_database.session_factory) as uow:
        snapshots = uow.brand_profile_assets.lock_for_publication(
            workspace_id=profile.workspace_id,
            selected_version_ids=(asset_version_id,),
        )
        snapshot = snapshots[asset_version_id]
        assert snapshot.asset.id == asset_id
        assert snapshot.asset_version.id == asset_version_id
        assert snapshot.current_rights_record is not None
        assert snapshot.current_rights_record.id == rights_record_id
        assert (
            uow.brand_profile_assets.lock_for_publication(
                workspace_id="other-workspace",
                selected_version_ids=(asset_version_id,),
            )
            == {}
        )

    version_id = _publish(
        integration_database,
        profile=profile,
        asset_id=asset_id,
        asset_version_id=asset_version_id,
        rights_record_id=rights_record_id,
        now=now + timedelta(seconds=1),
    )
    with SqlAlchemyBrandProfileUnitOfWork(integration_database.session_factory) as uow:
        loaded = uow.brand_profile_publications.get_version(
            workspace_id=profile.workspace_id,
            profile_id=profile.id,
            version_number=1,
        )
        assert loaded is not None
        assert loaded.id == version_id
        assert loaded.members[0].rights_record_id == rights_record_id
        assert loaded.content_sha256 == loaded.calculate_content_sha256(members=loaded.members)
        assert uow.brand_profile_publications.list_versions(
            workspace_id=profile.workspace_id,
            profile_id=profile.id,
            cursor=None,
            limit=10,
        ) == (loaded,)
        current = uow.brand_profile_assets.current_snapshots(
            workspace_id=profile.workspace_id,
            asset_ids=(asset_id,),
        )
        assert current.snapshots[asset_id].asset.current_version_id == asset_version_id
        assert current.snapshots[asset_id].current_rights_record is not None
        assert current.snapshots[asset_id].current_rights_record.id == rights_record_id
        assert (
            uow.brand_profile_publications.get_version(
                workspace_id="other-workspace",
                profile_id=profile.id,
                version_number=1,
            )
            is None
        )

    with integration_database.engine.begin() as connection, pytest.raises(DBAPIError):
        connection.execute(
            text(
                "UPDATE brand_profile_members SET role = 'LORA' "
                "WHERE profile_version_id = :version_id"
            ),
            {"version_id": version_id},
        )
    with integration_database.engine.begin() as connection, pytest.raises(DBAPIError):
        connection.execute(
            text("DELETE FROM brand_profile_members WHERE profile_version_id = :version_id"),
            {"version_id": version_id},
        )
    with integration_database.engine.begin() as connection, pytest.raises(DBAPIError):
        connection.execute(
            text(
                "UPDATE brand_profile_versions SET provider = 'other-provider' "
                "WHERE id = :version_id"
            ),
            {"version_id": version_id},
        )
    with integration_database.engine.begin() as connection, pytest.raises(DBAPIError):
        connection.execute(
            text("DELETE FROM brand_profile_versions WHERE id = :version_id"),
            {"version_id": version_id},
        )
    with integration_database.engine.begin() as connection, pytest.raises(DBAPIError):
        connection.execute(
            text("DELETE FROM brand_profiles WHERE id = :profile_id"),
            {"profile_id": profile.id},
        )


def test_current_snapshot_permission_sets_cannot_cross_workspace_or_asset_scope(
    integration_database,
) -> None:
    now = datetime(2026, 7, 30, 10, 20, 0, tzinfo=UTC)
    asset_id, _, rights_record_id = _seed_foundation_asset(
        integration_database,
        workspace_id="permission-scope-workspace",
        now=now,
        inject_foreign_permissions=True,
    )

    with SqlAlchemyBrandProfileUnitOfWork(integration_database.session_factory) as uow:
        snapshots = uow.brand_profile_assets.current_snapshots(
            workspace_id="permission-scope-workspace",
            asset_ids=(asset_id,),
        )
        rights = snapshots.snapshots[asset_id].current_rights_record

    assert rights is not None
    assert rights.allowed_uses == frozenset({"RETRIEVAL"})
    assert rights.allowed_providers == frozenset({"milvus"})


def test_current_snapshot_holds_shared_authority_lock_until_decision_finishes(
    integration_database,
) -> None:
    now = datetime.now(UTC) - timedelta(minutes=1)
    asset_id, _, _ = _seed_foundation_asset(
        integration_database,
        workspace_id="snapshot-lock-workspace",
        now=now,
    )

    def mutate_authority() -> None:
        with integration_database.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE assets SET version = version + 1 "
                    "WHERE workspace_id = :workspace_id AND id = :asset_id"
                ),
                {
                    "workspace_id": "snapshot-lock-workspace",
                    "asset_id": asset_id,
                },
            )

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        with SqlAlchemyBrandProfileUnitOfWork(integration_database.session_factory) as uow:
            snapshots = uow.brand_profile_assets.current_snapshots(
                workspace_id="snapshot-lock-workspace",
                asset_ids=(asset_id,),
            )
            assert snapshots.snapshots[asset_id].asset.id == asset_id
            mutation = executor.submit(mutate_authority)
            with pytest.raises(FutureTimeoutError):
                mutation.result(timeout=0.5)
        mutation.result(timeout=10)
    finally:
        executor.shutdown(wait=True)


def test_current_snapshot_samples_database_time_after_waiting_for_authority_lock(
    integration_database,
) -> None:
    with integration_database.engine.connect() as connection:
        database_now = connection.scalar(text("SELECT UTC_TIMESTAMP(6)")).replace(tzinfo=UTC)
    asset_id, _, _ = _seed_foundation_asset(
        integration_database,
        workspace_id="snapshot-decision-time-workspace",
        now=database_now - timedelta(minutes=1),
        rights_valid_until=database_now + timedelta(seconds=1),
    )

    def read_snapshot():
        with SqlAlchemyBrandProfileUnitOfWork(integration_database.session_factory) as uow:
            return uow.brand_profile_assets.current_snapshots(
                workspace_id="snapshot-decision-time-workspace",
                asset_ids=(asset_id,),
            )

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        with integration_database.engine.connect() as lock_connection:
            transaction = lock_connection.begin()
            lock_connection.execute(
                text(
                    "SELECT id FROM assets "
                    "WHERE workspace_id = :workspace_id AND id = :asset_id "
                    "FOR UPDATE"
                ),
                {
                    "workspace_id": "snapshot-decision-time-workspace",
                    "asset_id": asset_id,
                },
            )
            snapshot_future = executor.submit(read_snapshot)
            with pytest.raises(FutureTimeoutError):
                snapshot_future.result(timeout=0.25)
            sleep(1.25)
            released_at = lock_connection.scalar(text("SELECT UTC_TIMESTAMP(6)"))
            transaction.commit()

        snapshot_batch = snapshot_future.result(timeout=10)
    finally:
        executor.shutdown(wait=True)

    assert snapshot_batch.decided_at.replace(tzinfo=None) >= released_at
    current_rights = snapshot_batch.snapshots[asset_id].current_rights_record
    assert current_rights is not None
    assert current_rights.valid_until is not None
    assert current_rights.valid_until <= snapshot_batch.decided_at


def test_cross_workspace_member_is_rejected_by_exact_database_ownership(
    integration_database,
) -> None:
    now = datetime(2026, 7, 30, 10, 30, 0, tzinfo=UTC)
    foreign_asset_id, foreign_version_id, foreign_rights_id = _seed_foundation_asset(
        integration_database,
        workspace_id="foreign-workspace",
        now=now,
    )
    profile = _profile(
        workspace_id="profile-workspace",
        brand="Acme",
        profile_key="ownership",
        asset_version_id=foreign_version_id,
        now=now,
    )
    with SqlAlchemyBrandProfileUnitOfWork(integration_database.session_factory) as uow:
        uow.brand_profiles.add(profile)
        uow.commit()
    publication = profile.publish(
        expected_version=1,
        members=(
            BrandProfilePublishedMember(
                ordinal=0,
                asset_id=foreign_asset_id,
                asset_version_id=foreign_version_id,
                role=BrandProfileMemberRole.LOGO,
                rights_record_id=foreign_rights_id,
                rights_record_version=1,
            ),
        ),
        actor_id="brand-admin",
        now=now + timedelta(seconds=1),
    )

    with (
        SqlAlchemyBrandProfileUnitOfWork(integration_database.session_factory) as uow,
        pytest.raises(ReferenceConstraintError),
    ):
        uow.brand_profile_publications.add(publication)

    with integration_database.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM brand_profile_versions WHERE profile_id = :profile_id"),
                {"profile_id": profile.id},
            ).scalar_one()
            == 0
        )


@pytest.mark.parametrize(
    ("authority_case", "expected_reason_code"),
    [
        pytest.param(
            "administratively-blocked",
            "ADMINISTRATIVELY_BLOCKED",
            id="asset-administratively-blocked",
        ),
        pytest.param(
            "expired-rights",
            "RIGHTS_EXPIRED",
            id="current-rights-expired",
        ),
    ],
)
def test_publish_rejects_member_invalid_under_mysql_lock_without_partial_writes(
    integration_database,
    authority_case: str,
    expected_reason_code: str,
) -> None:
    now = datetime.now(UTC) - timedelta(minutes=10)
    workspace_id = f"invalid-publish-{authority_case}"
    asset_id, asset_version_id, _ = _seed_foundation_asset(
        integration_database,
        workspace_id=workspace_id,
        now=now,
        rights_valid_until=(
            now + timedelta(minutes=1) if authority_case == "expired-rights" else None
        ),
    )
    profile = _profile(
        workspace_id=workspace_id,
        brand="Acme",
        profile_key="invalid-authority",
        asset_version_id=asset_version_id,
        now=now,
    )
    with SqlAlchemyBrandProfileUnitOfWork(integration_database.session_factory) as uow:
        uow.brand_profiles.add(profile)
        uow.commit()

    if authority_case == "administratively-blocked":
        with integration_database.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE assets SET status = 'BLOCKED', "
                    "block_reason = 'ADMINISTRATIVELY_BLOCKED', "
                    "version = version + 1, updated_at = UTC_TIMESTAMP(6) "
                    "WHERE workspace_id = :workspace_id AND id = :asset_id"
                ),
                {"workspace_id": workspace_id, "asset_id": asset_id},
            )

    profile_query = text(
        "SELECT state, draft_json, draft_sha256, current_version_id, "
        "current_version_number, version, stale_at, updated_by, updated_at, "
        "JSON_UNQUOTE(JSON_EXTRACT("
        "draft_json, '$.selected_assets[0].asset_version_id'"
        ")) AS draft_asset_version_id, "
        "JSON_UNQUOTE(JSON_EXTRACT("
        "draft_json, '$.selected_assets[0].role'"
        ")) AS draft_asset_role "
        "FROM brand_profiles "
        "WHERE workspace_id = :workspace_id AND id = :profile_id"
    )
    side_effect_query = text(
        "SELECT "
        "(SELECT COUNT(*) FROM brand_profile_versions "
        " WHERE profile_id = :profile_id) AS publication_count, "
        "(SELECT COUNT(*) FROM brand_profile_members "
        " WHERE profile_id = :profile_id) AS member_count, "
        "(SELECT COUNT(*) FROM outbox_events "
        " WHERE aggregate_type = 'BrandProfile' "
        "   AND aggregate_id = :profile_id) AS outbox_count, "
        "(SELECT COUNT(*) FROM audit_events "
        " WHERE resource_type = 'brand-profile' "
        "   AND resource_id = :profile_id) AS audit_count, "
        "(SELECT COUNT(*) FROM idempotency_keys "
        " WHERE scope LIKE :idempotency_scope) AS idempotency_count"
    )
    query_parameters = {
        "workspace_id": workspace_id,
        "profile_id": profile.id,
        "idempotency_scope": f"brand-profile:publish:%:{profile.id}",
    }
    with integration_database.engine.connect() as connection:
        original_head = connection.execute(profile_query, query_parameters).mappings().one()
        original_side_effects = (
            connection.execute(side_effect_query, query_parameters).mappings().one()
        )
    assert original_head["state"] == "DRAFT"
    assert original_head["current_version_id"] is None
    assert original_head["current_version_number"] == 0
    assert original_head["version"] == 1
    assert original_head["draft_asset_version_id"] == asset_version_id
    assert original_head["draft_asset_role"] == "LOGO"
    assert set(original_side_effects.values()) == {0}

    service = BrandProfileApplicationService(
        partial(
            SqlAlchemyBrandProfileUnitOfWork,
            integration_database.session_factory,
        ),
        cursor_codec=BrandProfileCursorCodec(
            current_key_id="integration-current",
            current_secret="integration-brand-profile-cursor-secret-000001",
            max_age_seconds=86_400,
            future_skew_seconds=30,
            clock=lambda: now,
        ),
    )

    with pytest.raises(BrandProfilePublicationRejected) as captured:
        service.publish(
            workspace_id=workspace_id,
            profile_id=profile.id,
            actor_id="brand-admin",
            request=BrandProfilePublishRequestV1(expected_version=1),
            idempotency_key=f"publish-invalid-{authority_case}",
            trace_id=f"brand-profile-invalid-{authority_case}",
        )

    assert [issue.reason_code for issue in captured.value.issues] == [expected_reason_code]
    with integration_database.engine.connect() as connection:
        retained_head = connection.execute(profile_query, query_parameters).mappings().one()
        retained_side_effects = (
            connection.execute(side_effect_query, query_parameters).mappings().one()
        )
    assert retained_head == original_head
    assert retained_side_effects == original_side_effects


def test_concurrent_publication_has_one_serialized_winner(
    integration_database,
) -> None:
    now = datetime.now(UTC)
    _, asset_version_id, _ = _seed_foundation_asset(
        integration_database,
        workspace_id="concurrent-workspace",
        now=now,
    )
    profile = _profile(
        workspace_id="concurrent-workspace",
        brand="Acme",
        profile_key="concurrent",
        asset_version_id=asset_version_id,
        now=now,
    )
    with SqlAlchemyBrandProfileUnitOfWork(integration_database.session_factory) as uow:
        uow.brand_profiles.add(profile)
        uow.commit()

    service = BrandProfileApplicationService(
        partial(
            SqlAlchemyBrandProfileUnitOfWork,
            integration_database.session_factory,
        ),
        cursor_codec=BrandProfileCursorCodec(
            current_key_id="integration-current",
            current_secret="integration-brand-profile-cursor-secret-000001",
            max_age_seconds=86_400,
            future_skew_seconds=30,
            clock=lambda: now,
        ),
    )

    def publish(index: int):
        return service.publish(
            workspace_id=profile.workspace_id,
            profile_id=profile.id,
            actor_id=f"publisher-{index}",
            request=BrandProfilePublishRequestV1(expected_version=1),
            idempotency_key=f"concurrent-publication-{index}",
            trace_id=f"brand-profile-concurrent-{index}",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(publish, index) for index in range(2)]
        outcomes: list[object] = []
        for future in futures:
            try:
                outcomes.append(future.result(timeout=20))
            except Exception as exc:  # noqa: BLE001 - the competing result is asserted below
                outcomes.append(exc)

    assert sum(not isinstance(outcome, Exception) for outcome in outcomes) == 1
    assert sum(isinstance(outcome, ConcurrencyError) for outcome in outcomes) == 1
    with integration_database.engine.connect() as connection:
        persisted = (
            connection.execute(
                text(
                    "SELECT profile.current_version_number, "
                    "(SELECT COUNT(*) FROM brand_profile_versions AS publication "
                    " WHERE publication.profile_id = profile.id) AS publication_count "
                    "FROM brand_profiles AS profile WHERE profile.id = :profile_id"
                ),
                {"profile_id": profile.id},
            )
            .mappings()
            .one()
        )
    assert persisted == {"current_version_number": 1, "publication_count": 1}


def test_invalidation_uses_current_head_and_live_authority_fences(
    integration_database,
) -> None:
    now = datetime(2026, 7, 30, 11, 0, 0, tzinfo=UTC)
    asset_id, asset_version_id, rights_record_id = _seed_foundation_asset(
        integration_database,
        workspace_id="invalidation-workspace",
        now=now,
        bind_rights_to_asset_version=False,
    )
    profile = _profile(
        workspace_id="invalidation-workspace",
        brand="Acme",
        profile_key="active",
        asset_version_id=asset_version_id,
        now=now,
    )
    with SqlAlchemyBrandProfileUnitOfWork(integration_database.session_factory) as uow:
        uow.brand_profiles.add(profile)
        uow.commit()
    published_version_id = _publish(
        integration_database,
        profile=profile,
        asset_id=asset_id,
        asset_version_id=asset_version_id,
        rights_record_id=rights_record_id,
        now=now + timedelta(seconds=1),
    )

    service = BrandProfileInvalidationApplicationService(
        partial(
            SqlAlchemyBrandProfileUnitOfWork,
            integration_database.session_factory,
        )
    )
    unchanged = service.invalidate_asset(
        workspace_id=profile.workspace_id,
        asset_id=asset_id,
        occurred_at=now + timedelta(seconds=2),
    )
    assert unchanged.matched_profiles == 1
    assert unchanged.marked_profiles == 0

    with integration_database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE assets SET status = 'BLOCKED', "
                "block_reason = 'ADMINISTRATIVELY_BLOCKED', version = version + 1 "
                "WHERE workspace_id = :workspace_id AND id = :asset_id"
            ),
            {"workspace_id": profile.workspace_id, "asset_id": asset_id},
        )

    with integration_database.engine.connect() as connection:
        lower_stale_bound = connection.scalar(text("SELECT UTC_TIMESTAMP(6)"))
    changed = service.invalidate_asset(
        workspace_id=profile.workspace_id,
        asset_id=asset_id,
        occurred_at=now + timedelta(seconds=3),
    )
    with integration_database.engine.connect() as connection:
        upper_stale_bound = connection.scalar(text("SELECT UTC_TIMESTAMP(6)"))
    assert changed.matched_profiles == 1
    assert changed.marked_profiles == 1
    with SqlAlchemyBrandProfileUnitOfWork(integration_database.session_factory) as uow:
        loaded = uow.brand_profiles.get(
            workspace_id=profile.workspace_id,
            profile_id=profile.id,
        )
    assert loaded is not None
    assert loaded.state == BrandProfileState.NEEDS_REPUBLISH
    assert loaded.current_version_id == published_version_id
    assert loaded.stale_at is not None
    assert lower_stale_bound <= loaded.stale_at.replace(tzinfo=None) <= upper_stale_bound

    duplicate = service.invalidate_asset(
        workspace_id=profile.workspace_id,
        asset_id=asset_id,
        occurred_at=now + timedelta(seconds=3),
    )
    assert duplicate.matched_profiles == 0
    assert duplicate.marked_profiles == 0

    with integration_database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE assets SET status = 'AVAILABLE', block_reason = NULL, "
                "version = version + 1 "
                "WHERE workspace_id = :workspace_id AND id = :asset_id"
            ),
            {"workspace_id": profile.workspace_id, "asset_id": asset_id},
        )
    republished_version_id = _publish(
        integration_database,
        profile=profile,
        asset_id=asset_id,
        asset_version_id=asset_version_id,
        rights_record_id=rights_record_id,
        now=now + timedelta(seconds=4),
    )
    late_old_event = service.invalidate_asset(
        workspace_id=profile.workspace_id,
        asset_id=asset_id,
        occurred_at=now + timedelta(seconds=3),
    )
    with SqlAlchemyBrandProfileUnitOfWork(integration_database.session_factory) as uow:
        republished = uow.brand_profiles.get(
            workspace_id=profile.workspace_id,
            profile_id=profile.id,
        )
    assert late_old_event.matched_profiles == 1
    assert late_old_event.marked_profiles == 0
    assert republished is not None
    assert republished.state == BrandProfileState.ACTIVE
    assert republished.current_version_id == republished_version_id
    assert republished.current_version_number == 2


def test_foundation_deletion_requires_exact_locked_lineage_before_invalidation(
    integration_database,
) -> None:
    now = datetime(2026, 7, 30, 11, 15, 0, tzinfo=UTC)
    asset_id, asset_version_id, rights_record_id = _seed_foundation_asset(
        integration_database,
        workspace_id="deletion-lineage-workspace",
        now=now,
    )
    profile = _profile(
        workspace_id="deletion-lineage-workspace",
        brand="Acme",
        profile_key="deleted-foundation",
        asset_version_id=asset_version_id,
        now=now,
    )
    with SqlAlchemyBrandProfileUnitOfWork(integration_database.session_factory) as uow:
        uow.brand_profiles.add(profile)
        uow.commit()
    _publish(
        integration_database,
        profile=profile,
        asset_id=asset_id,
        asset_version_id=asset_version_id,
        rights_record_id=rights_record_id,
        now=now + timedelta(seconds=1),
    )
    with integration_database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE assets SET status = 'DELETED', block_reason = NULL, "
                "version = 4, updated_at = :now "
                "WHERE workspace_id = :workspace_id AND id = :asset_id"
            ),
            {
                "workspace_id": profile.workspace_id,
                "asset_id": asset_id,
                "now": (now + timedelta(seconds=2)).replace(tzinfo=None),
            },
        )

    service = BrandProfileInvalidationApplicationService(
        partial(
            SqlAlchemyBrandProfileUnitOfWork,
            integration_database.session_factory,
        )
    )
    wrong_version_id = new_uuid7()
    with pytest.raises(RuntimeError, match="lineage"):
        service.invalidate_foundation_asset_deletion(
            workspace_id=profile.workspace_id,
            asset_id=asset_id,
            asset_version_id=wrong_version_id,
            deletion_generation=4,
            occurred_at=now + timedelta(seconds=2),
        )
    with pytest.raises(RuntimeError, match="lineage"):
        service.invalidate_foundation_asset_deletion(
            workspace_id=profile.workspace_id,
            asset_id=asset_id,
            asset_version_id=asset_version_id,
            deletion_generation=5,
            occurred_at=now + timedelta(seconds=2),
        )

    with SqlAlchemyBrandProfileUnitOfWork(integration_database.session_factory) as uow:
        unchanged = uow.brand_profiles.get(
            workspace_id=profile.workspace_id,
            profile_id=profile.id,
        )
    assert unchanged is not None
    assert unchanged.state == BrandProfileState.ACTIVE

    result = service.invalidate_foundation_asset_deletion(
        workspace_id=profile.workspace_id,
        asset_id=asset_id,
        asset_version_id=asset_version_id,
        deletion_generation=4,
        occurred_at=now + timedelta(seconds=2),
    )

    assert result.matched_profiles == 1
    assert result.marked_profiles == 1
    with SqlAlchemyBrandProfileUnitOfWork(integration_database.session_factory) as uow:
        stale = uow.brand_profiles.get(
            workspace_id=profile.workspace_id,
            profile_id=profile.id,
        )
    assert stale is not None
    assert stale.state == BrandProfileState.NEEDS_REPUBLISH

    duplicate = service.invalidate_foundation_asset_deletion(
        workspace_id=profile.workspace_id,
        asset_id=asset_id,
        asset_version_id=asset_version_id,
        deletion_generation=4,
        occurred_at=now + timedelta(seconds=2),
    )
    assert duplicate.matched_profiles == 0
    assert duplicate.marked_profiles == 0


def test_old_foundation_deletion_cannot_invalidate_a_newer_asset_version(
    integration_database,
) -> None:
    now = datetime(2026, 7, 30, 11, 30, 0, tzinfo=UTC)
    asset_id, asset_version_id, rights_record_id = _seed_foundation_asset(
        integration_database,
        workspace_id="old-deletion-workspace",
        now=now,
    )
    profile = _profile(
        workspace_id="old-deletion-workspace",
        brand="Acme",
        profile_key="old-deletion",
        asset_version_id=asset_version_id,
        now=now,
    )
    with SqlAlchemyBrandProfileUnitOfWork(integration_database.session_factory) as uow:
        uow.brand_profiles.add(profile)
        uow.commit()
    _publish(
        integration_database,
        profile=profile,
        asset_id=asset_id,
        asset_version_id=asset_version_id,
        rights_record_id=rights_record_id,
        now=now + timedelta(seconds=1),
    )
    newer_asset_version_id = new_uuid7()
    with integration_database.engine.begin() as connection:
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        connection.execute(
            text(
                "INSERT INTO asset_versions "
                "(id, workspace_id, asset_id, version_number, upload_session_id, "
                "filename, sha256, byte_size, declared_mime, detected_mime, "
                "image_format, width, height, frame_count, category, role, "
                "integrity_policy_version, validation_policy_version, "
                "validation_transfer_policy_version, "
                "validation_transfer_policy_snapshot_sha256, created_at) VALUES "
                "(:version_id, :workspace_id, :asset_id, 2, :upload_id, "
                "'logo-v2.png', :sha, 128, 'image/png', 'image/png', 'PNG', "
                "64, 64, 1, 'BRAND', 'LOGO', 'integrity-v1', 'validation-v1', "
                "'transfer-v1', :transfer_sha, :now)"
            ),
            {
                "version_id": newer_asset_version_id,
                "workspace_id": profile.workspace_id,
                "asset_id": asset_id,
                "upload_id": new_uuid7(),
                "sha": "e" * 64,
                "transfer_sha": "f" * 64,
                "now": (now + timedelta(seconds=2)).replace(tzinfo=None),
            },
        )
        connection.execute(
            text(
                "UPDATE assets SET current_version_id = :version_id, "
                "status = 'AVAILABLE', block_reason = NULL, version = 5, updated_at = :now "
                "WHERE workspace_id = :workspace_id AND id = :asset_id"
            ),
            {
                "version_id": newer_asset_version_id,
                "workspace_id": profile.workspace_id,
                "asset_id": asset_id,
                "now": (now + timedelta(seconds=2)).replace(tzinfo=None),
            },
        )
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 1"))

    service = BrandProfileInvalidationApplicationService(
        partial(
            SqlAlchemyBrandProfileUnitOfWork,
            integration_database.session_factory,
        )
    )
    result = service.invalidate_foundation_asset_deletion(
        workspace_id=profile.workspace_id,
        asset_id=asset_id,
        asset_version_id=asset_version_id,
        deletion_generation=4,
        occurred_at=now + timedelta(seconds=2),
    )

    assert result.matched_profiles == 1
    assert result.marked_profiles == 0
    with SqlAlchemyBrandProfileUnitOfWork(integration_database.session_factory) as uow:
        unchanged = uow.brand_profiles.get(
            workspace_id=profile.workspace_id,
            profile_id=profile.id,
        )
    assert unchanged is not None
    assert unchanged.state == BrandProfileState.ACTIVE


def test_slow_event_clock_cannot_hide_a_live_authority_revocation(
    integration_database,
) -> None:
    now = datetime.now(UTC) - timedelta(minutes=5)
    asset_id, asset_version_id, rights_record_id = _seed_foundation_asset(
        integration_database,
        workspace_id="slow-clock-workspace",
        now=now,
    )
    profile = _profile(
        workspace_id="slow-clock-workspace",
        brand="Acme",
        profile_key="slow-clock",
        asset_version_id=asset_version_id,
        now=now,
    )
    with SqlAlchemyBrandProfileUnitOfWork(integration_database.session_factory) as uow:
        uow.brand_profiles.add(profile)
        uow.commit()
    published_at = now + timedelta(minutes=1)
    _publish(
        integration_database,
        profile=profile,
        asset_id=asset_id,
        asset_version_id=asset_version_id,
        rights_record_id=rights_record_id,
        now=published_at,
    )
    with integration_database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE assets SET status = 'BLOCKED', "
                "block_reason = 'ADMINISTRATIVELY_BLOCKED', version = version + 1 "
                "WHERE workspace_id = :workspace_id AND id = :asset_id"
            ),
            {"workspace_id": profile.workspace_id, "asset_id": asset_id},
        )

    service = BrandProfileInvalidationApplicationService(
        partial(
            SqlAlchemyBrandProfileUnitOfWork,
            integration_database.session_factory,
        )
    )
    result = service.invalidate_asset(
        workspace_id=profile.workspace_id,
        asset_id=asset_id,
        occurred_at=now - timedelta(days=1),
    )

    assert result.matched_profiles == 1
    assert result.marked_profiles == 1
    with SqlAlchemyBrandProfileUnitOfWork(integration_database.session_factory) as uow:
        loaded = uow.brand_profiles.get(
            workspace_id=profile.workspace_id,
            profile_id=profile.id,
        )
    assert loaded is not None
    assert loaded.state == BrandProfileState.NEEDS_REPUBLISH
    assert loaded.stale_at is not None
    assert loaded.stale_at > published_at


def test_rights_replacement_invalidates_only_active_current_workspace_heads(
    integration_database,
) -> None:
    now = datetime(2026, 7, 30, 12, 0, 0, tzinfo=UTC)
    asset_id, asset_version_id, original_rights_id = _seed_foundation_asset(
        integration_database,
        workspace_id="replacement-workspace",
        now=now,
    )
    active = _profile(
        workspace_id="replacement-workspace",
        brand="Acme",
        profile_key="active",
        asset_version_id=asset_version_id,
        now=now,
    )
    archived = _profile(
        workspace_id="replacement-workspace",
        brand="Acme",
        profile_key="archived",
        asset_version_id=asset_version_id,
        now=now,
    )
    with SqlAlchemyBrandProfileUnitOfWork(integration_database.session_factory) as uow:
        uow.brand_profiles.add(active)
        uow.brand_profiles.add(archived)
        uow.commit()
    active_version_id = _publish(
        integration_database,
        profile=active,
        asset_id=asset_id,
        asset_version_id=asset_version_id,
        rights_record_id=original_rights_id,
        now=now + timedelta(seconds=1),
    )
    _publish(
        integration_database,
        profile=archived,
        asset_id=asset_id,
        asset_version_id=asset_version_id,
        rights_record_id=original_rights_id,
        now=now + timedelta(seconds=1),
    )
    with SqlAlchemyBrandProfileUnitOfWork(integration_database.session_factory) as uow:
        archived_head = uow.brand_profiles.get(
            workspace_id=archived.workspace_id,
            profile_id=archived.id,
            for_update=True,
        )
        assert archived_head is not None
        previous_version = archived_head.version
        archived_head.archive(
            expected_version=previous_version,
            actor_id="brand-admin",
            now=now + timedelta(seconds=2),
        )
        uow.brand_profiles.save(archived_head, expected_version=previous_version)
        uow.commit()

    _replace_current_rights(
        integration_database,
        workspace_id=active.workspace_id,
        asset_id=asset_id,
        asset_version_id=asset_version_id,
        supersedes_record_id=original_rights_id,
        now=now + timedelta(seconds=3),
    )
    service = BrandProfileInvalidationApplicationService(
        partial(
            SqlAlchemyBrandProfileUnitOfWork,
            integration_database.session_factory,
        )
    )
    wrong_workspace = service.invalidate_asset(
        workspace_id="other-workspace",
        asset_id=asset_id,
        occurred_at=now + timedelta(seconds=3),
    )
    changed = service.invalidate_asset(
        workspace_id=active.workspace_id,
        asset_id=asset_id,
        occurred_at=now + timedelta(seconds=3),
    )
    assert wrong_workspace.matched_profiles == 0
    assert wrong_workspace.marked_profiles == 0
    assert changed.matched_profiles == 1
    assert changed.marked_profiles == 1

    with SqlAlchemyBrandProfileUnitOfWork(integration_database.session_factory) as uow:
        active_head = uow.brand_profiles.get(
            workspace_id=active.workspace_id,
            profile_id=active.id,
        )
        archived_head = uow.brand_profiles.get(
            workspace_id=archived.workspace_id,
            profile_id=archived.id,
        )
    assert active_head is not None
    assert active_head.state == BrandProfileState.NEEDS_REPUBLISH
    assert active_head.current_version_id == active_version_id
    assert archived_head is not None
    assert archived_head.state == BrandProfileState.ARCHIVED
