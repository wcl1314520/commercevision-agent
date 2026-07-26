from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from commercevision_application import OperationExecutionRequest
from commercevision_application.asset_integrity import VerifiedUpload
from commercevision_application.asset_validation_evidence import (
    AssetValidationEvidenceError,
    assert_source_evidence_identity,
)
from commercevision_application.asset_validation_promotion import (
    AssetValidationPromotionError,
    assert_controlled_object_identity,
)
from commercevision_application.asset_validation_target import (
    AssetValidationTargetBinder,
    AssetValidationTargetError,
    asset_validation_input_hash,
)
from commercevision_contracts.object_storage import ObjectReference, ObjectStat
from commercevision_domain import (
    Asset,
    AssetKind,
    AssetObject,
    AssetObjectState,
    AssetValidationResult,
    AssetVersion,
    OperationKind,
    RetentionClass,
    StorageBackend,
    StorageLocationClass,
    UploadSession,
    ValidationStage,
    ValidationVerdict,
    new_uuid7,
)

NOW = datetime(2026, 7, 26, 6, 0, tzinfo=UTC)


class AssetRepository:
    def __init__(
        self,
        *,
        asset: Asset,
        asset_version: AssetVersion,
        source: AssetObject,
    ) -> None:
        self.asset = asset
        self.asset_version = asset_version
        self.source = source

    def get_version(self, **_kwargs: object) -> AssetVersion:
        return self.asset_version

    def get(self, **_kwargs: object) -> Asset:
        return self.asset

    def get_object(self, **_kwargs: object) -> AssetObject:
        return self.source


class UploadSessionRepository:
    def __init__(self, upload_session: UploadSession) -> None:
        self.upload_session = upload_session

    def get(self, **_kwargs: object) -> UploadSession:
        return self.upload_session


class AssetUnitOfWork:
    def __init__(
        self,
        *,
        asset: Asset,
        asset_version: AssetVersion,
        source: AssetObject,
        upload_session: UploadSession,
    ) -> None:
        self.assets = AssetRepository(
            asset=asset,
            asset_version=asset_version,
            source=source,
        )
        self.upload_sessions = UploadSessionRepository(upload_session)

    def __enter__(self) -> AssetUnitOfWork:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def _target_facts() -> tuple[
    Asset,
    AssetVersion,
    AssetObject,
    UploadSession,
    OperationExecutionRequest,
]:
    asset_id = new_uuid7()
    asset_version_id = new_uuid7()
    upload_session = UploadSession.create(
        workspace_id="validation-target",
        actor_id="validation-user",
        reserved_asset_id=asset_id,
        reserved_asset_version_id=asset_version_id,
        retention_class=RetentionClass.FOUNDATION,
        asset_kind=AssetKind.IMAGE,
        filename="pixel.png",
        declared_mime="image/png",
        expected_byte_length=68,
        expected_sha256="a" * 64,
        workflow_id=None,
        product_id=None,
        sku_id=None,
        category="beauty",
        role="primary",
        upload_policy_version="direct-put-v1",
        integrity_policy_version="image-integrity-v1",
        storage_backend=StorageBackend.MINIO,
        storage_bucket="quarantine",
        storage_key=f"validation-target/{asset_version_id}",
        destination_location=StorageLocationClass.FOUNDATION,
        destination_bucket="foundation",
        destination_key=f"controlled/{asset_version_id}",
        expires_at=NOW + timedelta(minutes=15),
        now=NOW,
    )
    asset_version = AssetVersion.create(
        asset_version_id=asset_version_id,
        workspace_id=upload_session.workspace_id,
        asset_id=asset_id,
        upload_session_id=upload_session.id,
        filename=upload_session.filename,
        sha256=upload_session.expected_sha256,
        byte_size=upload_session.expected_byte_length,
        declared_mime=upload_session.declared_mime,
        detected_mime="image/png",
        image_format="PNG",
        width=1,
        height=1,
        frame_count=1,
        category=upload_session.category,
        role=upload_session.role,
        integrity_policy_version=upload_session.integrity_policy_version,
        validation_policy_version="asset-validation-v1",
        now=NOW + timedelta(seconds=1),
    )
    asset = Asset.create_quarantined(
        asset_id=asset_id,
        workspace_id=upload_session.workspace_id,
        retention_class=upload_session.retention_class,
        kind=upload_session.asset_kind,
        workflow_id=None,
        product_id=None,
        sku_id=None,
        current_version_id=asset_version_id,
        retention_deadline=None,
        now=NOW + timedelta(seconds=1),
    )
    source = AssetObject.create_quarantined(
        workspace_id=upload_session.workspace_id,
        asset_version_id=asset_version_id,
        backend=upload_session.storage_backend,
        location=upload_session.storage_location,
        bucket=upload_session.storage_bucket,
        key=upload_session.storage_key,
        provider_version_id="source-version-1",
        etag='"source-etag"',
        byte_size=upload_session.expected_byte_length,
        sha256=upload_session.expected_sha256,
        now=NOW + timedelta(seconds=1),
    )
    operation_id = new_uuid7()
    lease_token = upload_session.claim_finalize(
        expected_version=upload_session.version,
        owner="api",
        lease_duration=timedelta(minutes=1),
        now=NOW + timedelta(seconds=1),
    )
    upload_session.finalize(
        lease_token=lease_token,
        asset_version_id=asset_version_id,
        validation_operation_id=operation_id,
        now=NOW + timedelta(seconds=2),
    )
    request = OperationExecutionRequest(
        operation_id=operation_id,
        workspace_id=upload_session.workspace_id,
        kind=OperationKind.ASSET_VALIDATION,
        target_type="ASSET_VERSION",
        target_id=asset_version_id,
        target_version=asset_version.version_number,
        input_hash=asset_validation_input_hash(asset, asset_version, source),
        input_ref=f"mysql://asset-versions/{asset_version_id}",
        provider_request_id=None,
        attempt_count=1,
        idempotency_key=f"durable-operation:{operation_id}",
    )
    return asset, asset_version, source, upload_session, request


def _binder(
    *,
    asset: Asset,
    asset_version: AssetVersion,
    source: AssetObject,
    upload_session: UploadSession,
) -> AssetValidationTargetBinder:
    return AssetValidationTargetBinder(
        uow_factory=lambda: AssetUnitOfWork(
            asset=asset,
            asset_version=asset_version,
            source=source,
            upload_session=upload_session,
        )
    )


def test_validation_input_hash_binds_policy_and_exact_source_identity() -> None:
    asset, asset_version, source, _, _ = _target_facts()
    baseline = asset_validation_input_hash(asset, asset_version, source)

    assert (
        asset_validation_input_hash(
            asset,
            replace(asset_version, validation_policy_version="asset-validation-v2"),
            source,
        )
        != baseline
    )
    assert (
        asset_validation_input_hash(
            asset,
            asset_version,
            replace(source, etag='"changed-etag"'),
        )
        != baseline
    )
    assert (
        asset_validation_input_hash(
            asset,
            asset_version,
            replace(source, provider_version_id="source-version-2"),
        )
        != baseline
    )
    task_asset = replace(
        asset,
        retention_class=RetentionClass.TASK,
        workflow_id=new_uuid7(),
        retention_deadline=NOW + timedelta(days=1),
    )
    task_baseline = asset_validation_input_hash(task_asset, asset_version, source)
    assert (
        asset_validation_input_hash(
            replace(task_asset, retention_deadline=NOW + timedelta(days=2)),
            asset_version,
            source,
        )
        != task_baseline
    )


def test_validation_target_binds_operation_session_and_quarantine_source() -> None:
    asset, asset_version, source, upload_session, request = _target_facts()

    target = _binder(
        asset=asset,
        asset_version=asset_version,
        source=source,
        upload_session=upload_session,
    ).load(request)

    assert target.asset_version.id == request.target_id
    assert target.source_object.id == source.id
    assert target.upload_session.validation_operation_id == request.operation_id


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"kind": OperationKind.ASSET_DELETION}, "VALIDATION_KIND_MISMATCH"),
        ({"target_type": "UPLOAD_SESSION"}, "VALIDATION_TARGET_MISMATCH"),
        ({"input_ref": "mysql://asset-versions/different"}, "VALIDATION_INPUT_REF_MISMATCH"),
        ({"input_hash": "f" * 64}, "VALIDATION_FACT_MISMATCH"),
    ],
)
def test_validation_target_rejects_operation_binding_drift(
    change: dict[str, object],
    code: str,
) -> None:
    asset, asset_version, source, upload_session, request = _target_facts()

    with pytest.raises(AssetValidationTargetError) as failed:
        _binder(
            asset=asset,
            asset_version=asset_version,
            source=source,
            upload_session=upload_session,
        ).load(replace(request, **change))

    assert failed.value.code == code


def test_validation_target_rejects_detached_operation_and_invalid_source_state() -> None:
    asset, asset_version, source, upload_session, request = _target_facts()
    detached = replace(upload_session, validation_operation_id=new_uuid7())

    with pytest.raises(AssetValidationTargetError) as detached_failure:
        _binder(
            asset=asset,
            asset_version=asset_version,
            source=source,
            upload_session=detached,
        ).load(request)
    assert detached_failure.value.code == "VALIDATION_FACT_MISMATCH"

    with pytest.raises(AssetValidationTargetError) as state_failure:
        _binder(
            asset=asset,
            asset_version=asset_version,
            source=replace(source, state=AssetObjectState.DELETE_PENDING),
            upload_session=upload_session,
        ).load(request)
    assert state_failure.value.code == "VALIDATION_FACT_MISMATCH"


@pytest.mark.parametrize(
    "change",
    [
        {"asset_object_id": new_uuid7()},
        {"object_provider_version_id": "source-version-2"},
        {"object_etag": '"changed-etag"'},
        {"content_sha256": "b" * 64},
        {"policy_version": "asset-validation-v2"},
        {"operation_id": new_uuid7()},
    ],
)
def test_reused_stage_evidence_must_match_the_exact_source_and_policy(
    change: dict[str, object],
) -> None:
    asset, asset_version, source, upload_session, request = _target_facts()
    target = _binder(
        asset=asset,
        asset_version=asset_version,
        source=source,
        upload_session=upload_session,
    ).load(request)
    result = AssetValidationResult.create(
        workspace_id=request.workspace_id,
        operation_id=request.operation_id,
        asset_version_id=asset_version.id,
        asset_object_id=source.id,
        attempt_number=request.attempt_count,
        stage=ValidationStage.LOCAL_FORMAT,
        validator_name="commercevision-local",
        validator_version="asset-local-validator-v1",
        policy_version=asset_version.validation_policy_version,
        verdict=ValidationVerdict.PASS,
        reason_code=None,
        object_provider_version_id=source.provider_version_id or "",
        object_etag=source.etag,
        content_sha256=source.sha256,
        evidence={
            "asset_kind": asset.kind.value,
            "byte_size": asset_version.byte_size,
        },
        retention_deadline=asset.retention_deadline,
        now=NOW + timedelta(seconds=3),
    )

    assert_source_evidence_identity(
        target=target,
        request=request,
        result=result,
        expected_stage=ValidationStage.LOCAL_FORMAT,
    )
    with pytest.raises(AssetValidationEvidenceError) as failed:
        assert_source_evidence_identity(
            target=target,
            request=request,
            result=replace(result, evidence=result.evidence_dict(), **change),
            expected_stage=ValidationStage.LOCAL_FORMAT,
        )

    assert failed.value.code == "VALIDATION_EVIDENCE_IDENTITY_MISMATCH"


@pytest.mark.parametrize(
    "change",
    [
        {"workspace_id": "another-workspace"},
        {"asset_version_id": new_uuid7()},
        {"role": "ORIGINAL"},
        {"backend": StorageBackend.OSS},
        {"location": StorageLocationClass.TASK},
        {"bucket": "another-foundation"},
        {"key": "controlled/another-key"},
        {"provider_version_id": "destination-version-2"},
        {"etag": '"changed-etag"'},
        {"byte_size": 69},
        {"sha256": "b" * 64},
        {"state": AssetObjectState.DELETE_PENDING},
    ],
)
def test_existing_controlled_object_requires_full_verified_destination_identity(
    change: dict[str, object],
) -> None:
    asset, asset_version, source, upload_session, request = _target_facts()
    target = _binder(
        asset=asset,
        asset_version=asset_version,
        source=source,
        upload_session=upload_session,
    ).load(request)
    controlled = AssetObject.create_controlled(
        workspace_id=asset.workspace_id,
        asset_version_id=asset_version.id,
        backend=StorageBackend.MINIO,
        location=StorageLocationClass.FOUNDATION,
        bucket="foundation",
        key=upload_session.destination_key,
        provider_version_id="destination-version-1",
        etag='"destination-etag"',
        byte_size=asset_version.byte_size,
        sha256=asset_version.sha256,
        now=NOW + timedelta(seconds=3),
    )
    verified = VerifiedUpload(
        stat=ObjectStat(
            reference=ObjectReference(
                location=controlled.location,
                key=controlled.key,
                version_id=controlled.provider_version_id,
            ),
            backend=controlled.backend,
            bucket=controlled.bucket,
            etag=controlled.etag,
            content_length=controlled.byte_size,
            content_type=asset_version.declared_mime,
            checksum_sha256_base64=None,
            metadata={
                "sha256": asset_version.sha256,
                "upload-session-id": upload_session.id,
            },
            last_modified=NOW + timedelta(seconds=3),
        ),
        sha256=controlled.sha256,
        byte_size=controlled.byte_size,
        detected_mime=asset_version.detected_mime,
        image_format=asset_version.image_format,
        width=asset_version.width,
        height=asset_version.height,
        frame_count=asset_version.frame_count,
    )

    assert_controlled_object_identity(
        target=target,
        controlled=controlled,
        verified=verified,
    )
    with pytest.raises(AssetValidationPromotionError) as failed:
        assert_controlled_object_identity(
            target=target,
            controlled=replace(controlled, **change),
            verified=verified,
        )

    assert failed.value.code == "PROMOTION_CONTROLLED_OBJECT_MISMATCH"
