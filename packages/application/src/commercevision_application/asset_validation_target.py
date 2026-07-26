"""Immutable target binding for Asset validation Durable Operations."""

from __future__ import annotations

from dataclasses import dataclass

from commercevision_domain import (
    Asset,
    AssetObject,
    AssetObjectState,
    AssetState,
    AssetVersion,
    OperationKind,
    UploadSession,
    UploadSessionState,
)

from .asset_idempotency import canonical_hash
from .asset_ports import AssetUnitOfWorkFactory
from .operations import OperationExecutionRequest


@dataclass(frozen=True, slots=True)
class AssetValidationTarget:
    asset: Asset
    asset_version: AssetVersion
    source_object: AssetObject
    upload_session: UploadSession


@dataclass(frozen=True, slots=True)
class AssetValidationTargetError(Exception):
    code: str
    message: str
    category: str

    def __str__(self) -> str:
        return self.message


def asset_validation_input_hash(
    asset: Asset,
    asset_version: AssetVersion,
    source_object: AssetObject,
) -> str:
    """Bind an operation to immutable Asset, policy, version, and object identities."""

    return canonical_hash(
        {
            "asset_id": asset_version.asset_id,
            "asset_kind": asset.kind.value,
            "asset_retention_class": asset.retention_class.value,
            "asset_retention_deadline": (
                asset.retention_deadline.isoformat()
                if asset.retention_deadline is not None
                else None
            ),
            "asset_version_id": asset_version.id,
            "asset_version_number": asset_version.version_number,
            "byte_size": asset_version.byte_size,
            "content_sha256": asset_version.sha256,
            "declared_mime": asset_version.declared_mime,
            "integrity_policy_version": asset_version.integrity_policy_version,
            "object_backend": source_object.backend.value,
            "object_bucket": source_object.bucket,
            "object_etag": source_object.etag,
            "object_fact_id": source_object.id,
            "object_key": source_object.key,
            "object_location": source_object.location.value,
            "object_provider_version_id": source_object.provider_version_id,
            "object_role": source_object.role,
            "upload_session_id": asset_version.upload_session_id,
            "validation_policy_version": asset_version.validation_policy_version,
            "validation_transfer_policy_snapshot_sha256": (
                asset_version.validation_transfer_policy_snapshot_sha256
            ),
            "validation_transfer_policy_version": (
                asset_version.validation_transfer_policy_version
            ),
        }
    )


class AssetValidationTargetBinder:
    """Resolve and prove the exact immutable facts addressed by an Operation."""

    def __init__(self, *, uow_factory: AssetUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def load(self, request: OperationExecutionRequest) -> AssetValidationTarget:
        return self._load(request, historical=False)

    def load_historical(
        self,
        request: OperationExecutionRequest,
    ) -> AssetValidationTarget:
        """Prove immutable validation identity without requiring an active state."""

        return self._load(request, historical=True)

    def _load(
        self,
        request: OperationExecutionRequest,
        *,
        historical: bool,
    ) -> AssetValidationTarget:
        self._validate_operation_contract(request)
        with self._uow_factory() as uow:
            asset_version = uow.assets.get_version(
                workspace_id=request.workspace_id,
                asset_version_id=request.target_id,
            )
            if asset_version is None:
                self._fail(
                    "VALIDATION_TARGET_NOT_FOUND",
                    "validation target Asset Version is unavailable",
                )
            assert asset_version is not None
            asset = uow.assets.get(
                workspace_id=request.workspace_id,
                asset_id=asset_version.asset_id,
            )
            source = uow.assets.get_object(
                workspace_id=request.workspace_id,
                asset_version_id=asset_version.id,
                role="ORIGINAL",
            )
            upload_session = uow.upload_sessions.get(
                workspace_id=request.workspace_id,
                upload_session_id=asset_version.upload_session_id,
            )
        if asset is None or source is None or upload_session is None:
            self._fail(
                "VALIDATION_TARGET_NOT_FOUND",
                "validation target facts are incomplete",
            )
        assert asset is not None
        assert source is not None
        assert upload_session is not None
        if not self._facts_match(
            request=request,
            asset=asset,
            asset_version=asset_version,
            source=source,
            upload_session=upload_session,
            historical=historical,
        ):
            self._fail(
                "VALIDATION_FACT_MISMATCH",
                "validation Operation does not match current immutable Asset facts",
            )
        return AssetValidationTarget(
            asset=asset,
            asset_version=asset_version,
            source_object=source,
            upload_session=upload_session,
        )

    @staticmethod
    def _validate_operation_contract(request: OperationExecutionRequest) -> None:
        if request.kind != OperationKind.ASSET_VALIDATION:
            AssetValidationTargetBinder._fail(
                "VALIDATION_KIND_MISMATCH",
                "validation executor received the wrong Operation kind",
                category="contract",
            )
        if request.target_type != "ASSET_VERSION":
            AssetValidationTargetBinder._fail(
                "VALIDATION_TARGET_MISMATCH",
                "validation executor only accepts Asset Version targets",
                category="contract",
            )
        if request.input_ref != f"mysql://asset-versions/{request.target_id}":
            AssetValidationTargetBinder._fail(
                "VALIDATION_INPUT_REF_MISMATCH",
                "validation Operation input reference is invalid",
                category="contract",
            )

    @staticmethod
    def _facts_match(
        *,
        request: OperationExecutionRequest,
        asset: Asset,
        asset_version: AssetVersion,
        source: AssetObject,
        upload_session: UploadSession,
        historical: bool,
    ) -> bool:
        return all(
            (
                asset_version.version_number == request.target_version,
                asset.workspace_id == request.workspace_id,
                asset.current_version_id == asset_version.id,
                asset.kind == upload_session.asset_kind,
                asset.retention_class == upload_session.retention_class,
                asset.workflow_id == upload_session.workflow_id,
                asset.product_id == upload_session.product_id,
                asset.sku_id == upload_session.sku_id,
                asset_version.workspace_id == request.workspace_id,
                asset_version.asset_id == asset.id,
                asset_version.upload_session_id == upload_session.id,
                asset_version.id == upload_session.reserved_asset_version_id,
                asset_version.id == upload_session.finalized_asset_version_id,
                asset.id == upload_session.reserved_asset_id,
                asset_version.filename == upload_session.filename,
                asset_version.declared_mime == upload_session.declared_mime,
                asset_version.byte_size == upload_session.expected_byte_length,
                asset_version.sha256 == upload_session.expected_sha256,
                asset_version.category == upload_session.category,
                asset_version.role == upload_session.role,
                asset_version.integrity_policy_version == upload_session.integrity_policy_version,
                asset_version.validation_transfer_policy_version
                == upload_session.validation_transfer_policy_version,
                asset_version.validation_transfer_policy_snapshot_sha256
                == upload_session.validation_transfer_policy_snapshot_sha256,
                upload_session.workspace_id == request.workspace_id,
                upload_session.state == UploadSessionState.FINALIZED,
                upload_session.validation_operation_id == request.operation_id,
                source.workspace_id == request.workspace_id,
                source.asset_version_id == asset_version.id,
                source.role == "ORIGINAL",
                source.backend == upload_session.storage_backend,
                source.location == upload_session.storage_location,
                source.bucket == upload_session.storage_bucket,
                source.key == upload_session.storage_key,
                source.byte_size == asset_version.byte_size,
                source.sha256 == asset_version.sha256,
                (
                    _historical_source_state_matches(source.state)
                    if historical
                    else _source_state_matches(asset.status, source.state)
                ),
                asset_validation_input_hash(asset, asset_version, source) == request.input_hash,
            )
        )

    @staticmethod
    def _fail(
        code: str,
        message: str,
        *,
        category: str = "integrity",
    ) -> None:
        raise AssetValidationTargetError(
            code=code,
            category=category,
            message=message,
        )


def _source_state_matches(
    asset_state: AssetState,
    source_state: AssetObjectState,
) -> bool:
    if asset_state in {
        AssetState.QUARANTINED,
        AssetState.VALIDATING,
        AssetState.PENDING_REVIEW,
        AssetState.FAILED,
    }:
        return source_state == AssetObjectState.QUARANTINED
    if asset_state == AssetState.BLOCKED:
        return source_state in {
            AssetObjectState.DELETE_PENDING,
            AssetObjectState.DELETED,
        }
    if asset_state == AssetState.PENDING_RIGHTS:
        return source_state == AssetObjectState.DELETED
    if asset_state == AssetState.DELETING:
        return source_state in {
            AssetObjectState.QUARANTINED,
            AssetObjectState.DELETE_PENDING,
            AssetObjectState.DELETED,
        }
    if asset_state == AssetState.DELETED:
        return source_state == AssetObjectState.DELETED
    return False


def _historical_source_state_matches(source_state: AssetObjectState) -> bool:
    return source_state in {
        AssetObjectState.QUARANTINED,
        AssetObjectState.DELETE_PENDING,
        AssetObjectState.DELETED,
    }
