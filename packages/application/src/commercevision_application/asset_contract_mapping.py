"""Public-contract mapping for Asset Registry domain objects."""

from commercevision_contracts import (
    AssetResponseV1,
    AssetVersionResponseV1,
    UploadFinalizeResponseV1,
    UploadSessionResponseV1,
    ValidationOperationSummaryV1,
)
from commercevision_domain import Asset, AssetObject, AssetVersion, DurableOperation, UploadSession


def upload_session_response(upload_session: UploadSession) -> UploadSessionResponseV1:
    return UploadSessionResponseV1(
        id=upload_session.id,
        workspace_id=upload_session.workspace_id,
        reserved_asset_id=upload_session.reserved_asset_id,
        retention_class=upload_session.retention_class,
        asset_kind=upload_session.asset_kind,
        filename=upload_session.filename,
        declared_mime=upload_session.declared_mime,
        expected_byte_length=upload_session.expected_byte_length,
        expected_sha256=upload_session.expected_sha256,
        workflow_id=upload_session.workflow_id,
        product_id=upload_session.product_id,
        sku_id=upload_session.sku_id,
        category=upload_session.category,
        role=upload_session.role,
        upload_policy_version=upload_session.upload_policy_version,
        integrity_policy_version=upload_session.integrity_policy_version,
        status=upload_session.state,
        failure_code=upload_session.failure_code,
        asset_version_id=upload_session.finalized_asset_version_id,
        validation_operation_id=upload_session.validation_operation_id,
        cleanup_operation_id=upload_session.cleanup_operation_id,
        expires_at=upload_session.expires_at,
        version=upload_session.version,
        created_at=upload_session.created_at,
        updated_at=upload_session.updated_at,
    )


def asset_version_response(
    asset_version: AssetVersion,
    object_fact: AssetObject,
) -> AssetVersionResponseV1:
    return AssetVersionResponseV1(
        id=asset_version.id,
        workspace_id=asset_version.workspace_id,
        asset_id=asset_version.asset_id,
        version_number=asset_version.version_number,
        upload_session_id=asset_version.upload_session_id,
        filename=asset_version.filename,
        sha256=asset_version.sha256,
        byte_size=asset_version.byte_size,
        declared_mime=asset_version.declared_mime,
        detected_mime=asset_version.detected_mime,
        image_format=asset_version.image_format,
        width=asset_version.width,
        height=asset_version.height,
        frame_count=asset_version.frame_count,
        category=asset_version.category,
        role=asset_version.role,
        integrity_policy_version=asset_version.integrity_policy_version,
        object_state=object_fact.state,
        created_at=asset_version.created_at,
    )


def asset_response(
    asset: Asset,
    *,
    current_version: AssetVersionResponseV1 | None,
) -> AssetResponseV1:
    return AssetResponseV1(
        id=asset.id,
        workspace_id=asset.workspace_id,
        retention_class=asset.retention_class,
        asset_kind=asset.kind,
        workflow_id=asset.workflow_id,
        product_id=asset.product_id,
        sku_id=asset.sku_id,
        status=asset.status,
        current_version_id=asset.current_version_id,
        retention_deadline=asset.retention_deadline,
        version=asset.version,
        created_at=asset.created_at,
        updated_at=asset.updated_at,
        current_version=current_version,
    )


def operation_response(operation: DurableOperation) -> ValidationOperationSummaryV1:
    return ValidationOperationSummaryV1(
        id=operation.id,
        state=operation.state,
        target_id=operation.target_id,
        target_version=operation.target_version,
        version=operation.version,
    )


def finalize_response(
    *,
    upload_session: UploadSession,
    asset: Asset,
    asset_version: AssetVersion,
    object_fact: AssetObject,
    operation: DurableOperation,
) -> UploadFinalizeResponseV1:
    version_response = asset_version_response(asset_version, object_fact)
    return UploadFinalizeResponseV1(
        upload_session=upload_session_response(upload_session),
        asset=asset_response(asset, current_version=version_response),
        asset_version=version_response,
        validation_operation=operation_response(operation),
    )
