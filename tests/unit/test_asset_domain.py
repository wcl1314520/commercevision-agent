from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from commercevision_domain import (
    Asset,
    AssetKind,
    AssetObject,
    AssetObjectState,
    LeaseConflictError,
    RetentionClass,
    StorageBackend,
    StorageLocationClass,
    UploadExpiredError,
    UploadSession,
    UploadSessionState,
    new_uuid7,
)

NOW = datetime(2026, 7, 24, 8, 0, tzinfo=UTC)


def _asset_object(**changes: object) -> AssetObject:
    values: dict[str, object] = {
        "id": new_uuid7(),
        "workspace_id": "asset-domain",
        "asset_version_id": new_uuid7(),
        "role": "ORIGINAL",
        "backend": StorageBackend.MINIO,
        "location": StorageLocationClass.QUARANTINE,
        "bucket": "quarantine",
        "key": f"server/{new_uuid7()}",
        "provider_version_id": "provider-version-1",
        "etag": '"opaque-etag"',
        "byte_size": 68,
        "sha256": "a" * 64,
        "state": AssetObjectState.QUARANTINED,
        "version": 1,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(changes)
    return AssetObject(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("provider_version_id", [None, "", " ", "null", " NULL "])
def test_asset_object_requires_an_exact_provider_version(
    provider_version_id: str | None,
) -> None:
    with pytest.raises(ValueError, match="provider version"):
        _asset_object(provider_version_id=provider_version_id)


def test_quarantined_asset_object_cannot_reference_retained_storage() -> None:
    with pytest.raises(ValueError, match="quarantine storage"):
        _asset_object(location=StorageLocationClass.FOUNDATION)


def _session(
    *,
    retention_class: RetentionClass = RetentionClass.FOUNDATION,
    workflow_id: str | None = None,
) -> UploadSession:
    reserved_asset_version_id = new_uuid7()
    destination_location = (
        StorageLocationClass.TASK
        if retention_class == RetentionClass.TASK
        else StorageLocationClass.FOUNDATION
    )
    return UploadSession.create(
        workspace_id="asset-domain",
        actor_id="asset-user",
        reserved_asset_id=new_uuid7(),
        reserved_asset_version_id=reserved_asset_version_id,
        retention_class=retention_class,
        asset_kind=AssetKind.IMAGE,
        filename="pixel.png",
        declared_mime="image/png",
        expected_byte_length=68,
        expected_sha256="a" * 64,
        workflow_id=workflow_id,
        product_id=None,
        sku_id=None,
        category="beauty.skincare",
        role="product-primary",
        upload_policy_version="direct-put-v1",
        integrity_policy_version="image-integrity-v1",
        storage_backend=StorageBackend.MINIO,
        storage_bucket="quarantine",
        storage_key=f"server/{new_uuid7()}",
        destination_location=destination_location,
        destination_bucket=destination_location.value.lower(),
        destination_key=f"server/{reserved_asset_version_id}/{new_uuid7()}",
        expires_at=NOW + timedelta(minutes=15),
        now=NOW,
    )


def test_finalize_lease_can_be_recovered_at_the_exact_expiry_boundary() -> None:
    session = _session()
    first_token = session.claim_finalize(
        expected_version=1,
        owner="worker-a",
        lease_duration=timedelta(seconds=30),
        now=NOW,
    )
    assert session.state == UploadSessionState.FINALIZING

    recovered_token = session.claim_finalize(
        expected_version=2,
        owner="worker-b",
        lease_duration=timedelta(seconds=30),
        now=NOW + timedelta(seconds=30),
    )
    assert recovered_token != first_token
    assert session.finalize_attempts == 2
    with pytest.raises(LeaseConflictError):
        session.release_finalize(
            lease_token=first_token,
            now=NOW + timedelta(seconds=31),
        )
    with pytest.raises(LeaseConflictError):
        session.finalize(
            lease_token=first_token,
            asset_version_id=session.reserved_asset_version_id,
            validation_operation_id=new_uuid7(),
            now=NOW + timedelta(seconds=31),
        )

    session.finalize(
        lease_token=recovered_token,
        asset_version_id=session.reserved_asset_version_id,
        validation_operation_id=new_uuid7(),
        now=NOW + timedelta(seconds=31),
    )
    assert session.state == UploadSessionState.FINALIZED
    assert session.finalize_lease_token is None


def test_expired_finalize_lease_cannot_commit_a_terminal_result() -> None:
    session = _session()
    lease_token = session.claim_finalize(
        expected_version=1,
        owner="worker-a",
        lease_duration=timedelta(seconds=30),
        now=NOW,
    )
    expired_at = NOW + timedelta(seconds=30)

    with pytest.raises(LeaseConflictError, match="expired"):
        session.reject_finalize(
            lease_token=lease_token,
            failure_code="OBJECT_MISMATCH",
            now=expired_at,
        )
    with pytest.raises(LeaseConflictError, match="expired"):
        session.finalize(
            lease_token=lease_token,
            asset_version_id=session.reserved_asset_version_id,
            validation_operation_id=new_uuid7(),
            now=expired_at,
        )

    assert session.state == UploadSessionState.FINALIZING
    assert session.failure_code is None
    assert session.finalized_asset_version_id is None


def test_expiry_is_terminal_for_finalize_and_does_not_create_an_asset_result() -> None:
    session = _session()
    assert session.expire_if_due(now=session.expires_at)
    cleanup_operation_id = new_uuid7()
    session.schedule_cleanup(
        operation_id=cleanup_operation_id,
        reconcile_until=session.expires_at + timedelta(days=3),
        now=session.expires_at + timedelta(microseconds=1),
    )
    expired_version = session.version
    assert session.state == UploadSessionState.EXPIRED
    assert session.finalized_asset_version_id is None
    assert session.validation_operation_id is None
    assert session.cleanup_operation_id == cleanup_operation_id
    session.schedule_cleanup(
        operation_id=cleanup_operation_id,
        reconcile_until=session.expires_at + timedelta(days=3),
        now=session.expires_at + timedelta(microseconds=2),
    )
    assert not session.expire_if_due(now=session.expires_at + timedelta(seconds=1))
    assert session.version == expired_version
    with pytest.raises(UploadExpiredError):
        session.claim_finalize(
            expected_version=session.version,
            owner="worker",
            lease_duration=timedelta(seconds=30),
            now=session.expires_at,
        )


def test_cleanup_operation_can_only_be_attached_once_to_a_terminal_session() -> None:
    open_session = _session()
    with pytest.raises(ValueError, match="terminal"):
        open_session.schedule_cleanup(
            operation_id=new_uuid7(),
            reconcile_until=NOW + timedelta(days=3),
            now=NOW,
        )

    aborted_session = _session()
    aborted_session.abort(expected_version=1, now=NOW + timedelta(seconds=1))
    operation_id = new_uuid7()
    aborted_session.schedule_cleanup(
        operation_id=operation_id,
        reconcile_until=NOW + timedelta(days=3),
        now=NOW + timedelta(seconds=2),
    )

    assert aborted_session.cleanup_operation_id == operation_id
    with pytest.raises(ValueError, match="already"):
        aborted_session.schedule_cleanup(
            operation_id=new_uuid7(),
            reconcile_until=NOW + timedelta(days=3),
            now=NOW + timedelta(seconds=3),
        )

    finalized_session = _session()
    lease_token = finalized_session.claim_finalize(
        expected_version=1,
        owner="worker-a",
        lease_duration=timedelta(seconds=30),
        now=NOW,
    )
    finalized_session.finalize(
        lease_token=lease_token,
        asset_version_id=finalized_session.reserved_asset_version_id,
        validation_operation_id=new_uuid7(),
        now=NOW + timedelta(seconds=1),
    )
    finalized_cleanup_id = new_uuid7()
    finalized_session.schedule_cleanup(
        operation_id=finalized_cleanup_id,
        reconcile_until=NOW + timedelta(days=3),
        now=NOW + timedelta(seconds=2),
    )
    assert finalized_session.cleanup_operation_id == finalized_cleanup_id


def test_finalize_claimed_before_upload_expiry_can_complete_under_its_lease() -> None:
    session = _session()
    claimed_at = session.expires_at - timedelta(seconds=1)
    lease_token = session.claim_finalize(
        expected_version=session.version,
        owner="worker-a",
        lease_duration=timedelta(seconds=30),
        now=claimed_at,
    )

    assert not session.expire_if_due(now=session.expires_at)
    session.finalize(
        lease_token=lease_token,
        asset_version_id=session.reserved_asset_version_id,
        validation_operation_id=new_uuid7(),
        now=session.expires_at + timedelta(seconds=1),
    )

    assert session.state == UploadSessionState.FINALIZED


def test_abandoned_expiry_waits_for_the_exact_finalize_lease_boundary() -> None:
    session = _session()
    claimed_at = session.expires_at - timedelta(seconds=1)
    session.claim_finalize(
        expected_version=session.version,
        owner="worker-a",
        lease_duration=timedelta(seconds=30),
        now=claimed_at,
    )
    lease_expires_at = session.finalize_lease_expires_at
    assert lease_expires_at is not None

    assert not session.expire_abandoned(now=session.expires_at)
    assert not session.expire_abandoned(now=lease_expires_at - timedelta(microseconds=1))
    assert session.expire_abandoned(now=lease_expires_at)

    assert session.state == UploadSessionState.EXPIRED
    assert session.finalize_lease_owner is None
    assert session.finalize_lease_token is None
    assert session.finalize_lease_expires_at is None


def test_task_retention_expiry_revokes_an_inflight_finalize_lease() -> None:
    session = _session(
        retention_class=RetentionClass.TASK,
        workflow_id=new_uuid7(),
    )
    session.claim_finalize(
        expected_version=session.version,
        owner="worker-a",
        lease_duration=timedelta(minutes=30),
        now=NOW,
    )

    assert session.expire_for_retention(now=NOW + timedelta(minutes=1))
    assert session.state == UploadSessionState.EXPIRED
    assert session.finalize_lease_owner is None
    assert session.finalize_lease_token is None
    assert session.finalize_lease_expires_at is None


def test_upload_session_rejects_invalid_ownership_and_persisted_state_shapes() -> None:
    with pytest.raises(ValueError, match="Task Assets require"):
        _session(retention_class=RetentionClass.TASK)

    session = _session()
    with pytest.raises(ValueError, match="FINALIZING"):
        replace(session, state=UploadSessionState.FINALIZING)
    with pytest.raises(ValueError, match="FINALIZED"):
        replace(
            session,
            state=UploadSessionState.FINALIZED,
            finalized_asset_version_id=new_uuid7(),
        )


def test_asset_retention_deadline_matches_its_retention_class() -> None:
    common = {
        "asset_id": new_uuid7(),
        "workspace_id": "asset-domain",
        "kind": AssetKind.IMAGE,
        "product_id": None,
        "sku_id": None,
        "current_version_id": new_uuid7(),
        "now": NOW,
    }
    with pytest.raises(ValueError, match="retention deadline"):
        Asset.create_quarantined(
            **common,
            retention_class=RetentionClass.TASK,
            workflow_id=new_uuid7(),
            retention_deadline=None,
        )
    with pytest.raises(ValueError, match="retention deadline"):
        Asset.create_quarantined(
            **common,
            retention_class=RetentionClass.FOUNDATION,
            workflow_id=None,
            retention_deadline=NOW + timedelta(days=3),
        )
