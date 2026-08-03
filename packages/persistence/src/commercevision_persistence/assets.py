"""MySQL adapters for direct-upload sessions and quarantined Assets."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from types import TracebackType

from commercevision_application.asset_ports import (
    AssetRetentionCommitExpiredError,
    AssetRetentionScanClaim,
    CurrentUsabilitySnapshot,
    RightsScanClaim,
    WorkflowRetentionFacts,
)
from commercevision_domain import (
    Asset,
    AssetDeletionReason,
    AssetKind,
    AssetObject,
    AssetObjectState,
    AssetState,
    AssetValidationResult,
    AssetVersion,
    ConcurrencyError,
    InvalidTransitionError,
    RetentionClass,
    RightsRecord,
    RightsRecordDecision,
    StorageBackend,
    StorageLocationClass,
    UploadSession,
    UploadSessionState,
    ValidationStage,
    ValidationVerdict,
)
from sqlalchemy import and_, exists, literal_column, or_, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from .asset_deletions import AssetDeletionRepository
from .database import enter_unit_of_work, exit_unit_of_work
from .integrity import (
    classify_database_error,
    execute_with_integrity_classification,
    flush_with_integrity_classification,
)
from .models import (
    AssetModel,
    AssetObjectModel,
    AssetValidationResultModel,
    AssetVersionModel,
    ProductModel,
    RightsRecordModel,
    RightsRecordProviderModel,
    RightsRecordUseModel,
    SKUModel,
    UploadSessionModel,
    WorkflowModel,
)
from .operations import OperationRepository
from .repositories import AuditRepository, IdempotencyRepository, OutboxRepository


def _upload_session_from_model(model: UploadSessionModel) -> UploadSession:
    return UploadSession(
        id=model.id,
        workspace_id=model.workspace_id,
        actor_id=model.actor_id,
        reserved_asset_id=model.reserved_asset_id,
        reserved_asset_version_id=model.reserved_asset_version_id,
        retention_class=RetentionClass(model.retention_class),
        asset_kind=AssetKind(model.asset_kind),
        filename=model.filename,
        declared_mime=model.declared_mime,
        expected_byte_length=model.expected_byte_length,
        expected_sha256=model.expected_sha256,
        workflow_id=model.workflow_id,
        product_id=model.product_id,
        sku_id=model.sku_id,
        category=model.category,
        role=model.role,
        upload_policy_version=model.upload_policy_version,
        integrity_policy_version=model.integrity_policy_version,
        validation_transfer_policy_version=model.validation_transfer_policy_version,
        validation_transfer_policy_snapshot_sha256=(
            model.validation_transfer_policy_snapshot_sha256
        ),
        storage_backend=StorageBackend(model.storage_backend),
        storage_location=StorageLocationClass(model.storage_location),
        storage_bucket=model.storage_bucket,
        storage_key=model.storage_key,
        destination_location=StorageLocationClass(model.destination_location),
        destination_bucket=model.destination_bucket,
        destination_key=model.destination_key,
        state=UploadSessionState(model.state),
        finalize_lease_owner=model.finalize_lease_owner,
        finalize_lease_token=model.finalize_lease_token,
        finalize_lease_expires_at=model.finalize_lease_expires_at,
        finalize_attempts=model.finalize_attempts,
        failure_code=model.failure_code,
        finalized_asset_version_id=model.finalized_asset_version_id,
        validation_operation_id=model.validation_operation_id,
        cleanup_operation_id=model.cleanup_operation_id,
        cleanup_reconcile_until=model.cleanup_reconcile_until,
        expires_at=model.expires_at,
        version=model.version,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _upload_session_to_model(upload_session: UploadSession) -> UploadSessionModel:
    return UploadSessionModel(
        id=upload_session.id,
        workspace_id=upload_session.workspace_id,
        actor_id=upload_session.actor_id,
        reserved_asset_id=upload_session.reserved_asset_id,
        reserved_asset_version_id=upload_session.reserved_asset_version_id,
        retention_class=upload_session.retention_class.value,
        asset_kind=upload_session.asset_kind.value,
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
        validation_transfer_policy_version=(upload_session.validation_transfer_policy_version),
        validation_transfer_policy_snapshot_sha256=(
            upload_session.validation_transfer_policy_snapshot_sha256
        ),
        storage_backend=upload_session.storage_backend.value,
        storage_location=upload_session.storage_location.value,
        storage_bucket=upload_session.storage_bucket,
        storage_key=upload_session.storage_key,
        destination_location=upload_session.destination_location.value,
        destination_bucket=upload_session.destination_bucket,
        destination_key=upload_session.destination_key,
        state=upload_session.state.value,
        finalize_lease_owner=upload_session.finalize_lease_owner,
        finalize_lease_token=upload_session.finalize_lease_token,
        finalize_lease_expires_at=upload_session.finalize_lease_expires_at,
        finalize_attempts=upload_session.finalize_attempts,
        failure_code=upload_session.failure_code,
        finalized_asset_version_id=upload_session.finalized_asset_version_id,
        validation_operation_id=upload_session.validation_operation_id,
        cleanup_operation_id=upload_session.cleanup_operation_id,
        cleanup_reconcile_until=upload_session.cleanup_reconcile_until,
        expires_at=upload_session.expires_at,
        version=upload_session.version,
        created_at=upload_session.created_at,
        updated_at=upload_session.updated_at,
    )


def _asset_from_model(model: AssetModel) -> Asset:
    if model.current_version_id is None:
        raise RuntimeError(f"Asset {model.id} has no current Asset Version")
    return Asset(
        id=model.id,
        workspace_id=model.workspace_id,
        retention_class=RetentionClass(model.retention_class),
        kind=AssetKind(model.asset_kind),
        workflow_id=model.workflow_id,
        product_id=model.product_id,
        sku_id=model.sku_id,
        status=AssetState(model.status),
        block_reason=model.block_reason,
        current_version_id=model.current_version_id,
        retention_deadline=model.retention_deadline,
        deletion_generation=model.deletion_generation,
        deletion_operation_id=model.deletion_operation_id,
        deletion_reason=(
            AssetDeletionReason(model.deletion_reason)
            if model.deletion_reason is not None
            else None
        ),
        deletion_requested_at=model.deletion_requested_at,
        deletion_completed_at=model.deletion_completed_at,
        version=model.version,
        created_at=model.created_at,
        updated_at=model.updated_at,
        current_rights_record_id=model.current_rights_record_id,
    )


def _asset_version_from_model(model: AssetVersionModel) -> AssetVersion:
    return AssetVersion(
        id=model.id,
        workspace_id=model.workspace_id,
        asset_id=model.asset_id,
        version_number=model.version_number,
        upload_session_id=model.upload_session_id,
        filename=model.filename,
        sha256=model.sha256,
        byte_size=model.byte_size,
        declared_mime=model.declared_mime,
        detected_mime=model.detected_mime,
        image_format=model.image_format,
        width=model.width,
        height=model.height,
        frame_count=model.frame_count,
        category=model.category,
        role=model.role,
        integrity_policy_version=model.integrity_policy_version,
        validation_policy_version=model.validation_policy_version,
        validation_transfer_policy_version=(model.validation_transfer_policy_version),
        validation_transfer_policy_snapshot_sha256=(
            model.validation_transfer_policy_snapshot_sha256
        ),
        created_at=model.created_at,
    )


def _rights_record_from_model(
    model: RightsRecordModel,
    *,
    allowed_uses: frozenset[str],
    allowed_providers: frozenset[str],
) -> RightsRecord:
    if model.permissions_sealed_at is None:
        raise RuntimeError(f"Rights Record {model.id} has unsealed permissions")
    return RightsRecord(
        id=model.id,
        workspace_id=model.workspace_id,
        asset_id=model.asset_id,
        asset_version_id=model.asset_version_id,
        version_number=model.version_number,
        decision=RightsRecordDecision(model.decision),
        owner_reference=model.owner_reference,
        source=model.source,
        license_reference=model.license_reference,
        allowed_uses=allowed_uses,
        allowed_providers=allowed_providers,
        derivative_allowed=model.derivative_allowed,
        public_demo_allowed=model.public_demo_allowed,
        evidence_reference=model.evidence_reference,
        terms_sha256=model.terms_sha256,
        valid_from=model.valid_from,
        valid_until=model.valid_until,
        perpetual=model.perpetual,
        supersedes_record_id=model.supersedes_record_id,
        created_by=model.created_by,
        created_at=model.created_at,
    )


def _asset_object_from_model(model: AssetObjectModel) -> AssetObject:
    return AssetObject(
        id=model.id,
        workspace_id=model.workspace_id,
        asset_version_id=model.asset_version_id,
        role=model.role,
        backend=StorageBackend(model.backend),
        location=StorageLocationClass(model.location),
        bucket=model.bucket,
        key=model.key,
        provider_version_id=model.provider_version_id,
        etag=model.etag,
        byte_size=model.byte_size,
        sha256=model.sha256,
        state=AssetObjectState(model.state),
        version=model.version,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _validation_result_from_model(
    model: AssetValidationResultModel,
) -> AssetValidationResult:
    return AssetValidationResult(
        id=model.id,
        workspace_id=model.workspace_id,
        operation_id=model.operation_id,
        asset_version_id=model.asset_version_id,
        asset_object_id=model.asset_object_id,
        attempt_number=model.attempt_number,
        stage=ValidationStage(model.stage),
        validator_name=model.validator_name,
        validator_version=model.validator_version,
        policy_version=model.policy_version,
        verdict=ValidationVerdict(model.verdict),
        reason_code=model.reason_code,
        object_provider_version_id=model.object_provider_version_id,
        object_etag=model.object_etag,
        content_sha256=model.content_sha256,
        evidence=model.evidence_json,
        retention_deadline=model.retention_deadline,
        created_at=model.created_at,
    )


def _validation_result_to_model(
    result: AssetValidationResult,
) -> AssetValidationResultModel:
    return AssetValidationResultModel(
        id=result.id,
        workspace_id=result.workspace_id,
        operation_id=result.operation_id,
        asset_version_id=result.asset_version_id,
        asset_object_id=result.asset_object_id,
        attempt_number=result.attempt_number,
        stage=result.stage.value,
        validator_name=result.validator_name,
        validator_version=result.validator_version,
        policy_version=result.policy_version,
        verdict=result.verdict.value,
        reason_code=result.reason_code,
        object_provider_version_id=result.object_provider_version_id,
        object_etag=result.object_etag,
        content_sha256=result.content_sha256,
        evidence_json=result.evidence_dict(),
        retention_deadline=result.retention_deadline,
        created_at=result.created_at,
    )


class UploadSessionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._loaded_versions: dict[str, int] = {}

    def add(self, upload_session: UploadSession) -> None:
        self._session.add(_upload_session_to_model(upload_session))
        self._loaded_versions[upload_session.id] = upload_session.version

    def get(
        self,
        *,
        workspace_id: str,
        upload_session_id: str,
        for_update: bool = False,
    ) -> UploadSession | None:
        statement = select(UploadSessionModel).where(
            UploadSessionModel.workspace_id == workspace_id,
            UploadSessionModel.id == upload_session_id,
        )
        if for_update:
            statement = statement.with_for_update()
        model = self._session.scalar(statement)
        if model is None:
            return None
        self._loaded_versions[model.id] = model.version
        return _upload_session_from_model(model)

    def save(self, upload_session: UploadSession) -> None:
        original_version = self._loaded_versions.get(upload_session.id)
        if original_version is None:
            raise ConcurrencyError(
                f"upload session {upload_session.id} was not loaded by this transaction"
            )
        values = _upload_session_to_model(upload_session)
        # Pending Asset/Version/Operation rows must exist before head-result FKs move.
        flush_with_integrity_classification(self._session)
        result = execute_with_integrity_classification(
            self._session,
            update(UploadSessionModel)
            .where(
                UploadSessionModel.workspace_id == upload_session.workspace_id,
                UploadSessionModel.id == upload_session.id,
                UploadSessionModel.version == original_version,
            )
            .values(
                state=values.state,
                finalize_lease_owner=values.finalize_lease_owner,
                finalize_lease_token=values.finalize_lease_token,
                finalize_lease_expires_at=values.finalize_lease_expires_at,
                finalize_attempts=values.finalize_attempts,
                failure_code=values.failure_code,
                finalized_asset_version_id=values.finalized_asset_version_id,
                validation_operation_id=values.validation_operation_id,
                cleanup_operation_id=values.cleanup_operation_id,
                cleanup_reconcile_until=values.cleanup_reconcile_until,
                version=values.version,
                updated_at=values.updated_at,
            ),
        )
        if result.rowcount != 1:
            raise ConcurrencyError(f"upload session {upload_session.id} was concurrently modified")
        self._loaded_versions[upload_session.id] = upload_session.version

    def claim_expired(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> list[UploadSession]:
        if limit < 1:
            raise ValueError("upload expiry scan limit must be positive")
        models = list(
            self._session.scalars(
                select(UploadSessionModel)
                .where(
                    UploadSessionModel.expires_at <= now,
                    or_(
                        UploadSessionModel.state == UploadSessionState.OPEN.value,
                        and_(
                            UploadSessionModel.state == UploadSessionState.FINALIZING.value,
                            UploadSessionModel.finalize_lease_expires_at <= now,
                        ),
                    ),
                )
                .order_by(UploadSessionModel.expires_at, UploadSessionModel.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        for model in models:
            self._loaded_versions[model.id] = model.version
        return [_upload_session_from_model(model) for model in models]


class AssetRepository:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._loaded_asset_versions: dict[str, int] = {}
        self._loaded_object_versions: dict[str, int] = {}

    def add_quarantined(
        self,
        *,
        asset: Asset,
        asset_version: AssetVersion,
        object_fact: AssetObject,
    ) -> None:
        if asset.current_version_id != asset_version.id:
            raise ValueError("Asset head must reference the supplied Asset Version")
        if asset.id != asset_version.asset_id:
            raise ValueError("Asset Version does not belong to the supplied Asset")
        if object_fact.asset_version_id != asset_version.id:
            raise ValueError("object fact does not belong to the supplied Asset Version")

        self._session.add(
            AssetModel(
                id=asset.id,
                workspace_id=asset.workspace_id,
                retention_class=asset.retention_class.value,
                asset_kind=asset.kind.value,
                workflow_id=asset.workflow_id,
                product_id=asset.product_id,
                sku_id=asset.sku_id,
                status=asset.status.value,
                block_reason=asset.block_reason,
                current_version_id=None,
                current_rights_record_id=None,
                retention_deadline=asset.retention_deadline,
                deletion_generation=asset.deletion_generation,
                deletion_operation_id=asset.deletion_operation_id,
                deletion_reason=(asset.deletion_reason.value if asset.deletion_reason else None),
                deletion_requested_at=asset.deletion_requested_at,
                deletion_completed_at=asset.deletion_completed_at,
                version=asset.version,
                created_at=asset.created_at,
                updated_at=asset.updated_at,
            )
        )
        flush_with_integrity_classification(self._session)
        self._session.add(
            AssetVersionModel(
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
                validation_policy_version=asset_version.validation_policy_version,
                validation_transfer_policy_version=(
                    asset_version.validation_transfer_policy_version
                ),
                validation_transfer_policy_snapshot_sha256=(
                    asset_version.validation_transfer_policy_snapshot_sha256
                ),
                created_at=asset_version.created_at,
            )
        )
        flush_with_integrity_classification(self._session)
        result = execute_with_integrity_classification(
            self._session,
            update(AssetModel)
            .where(
                AssetModel.workspace_id == asset.workspace_id,
                AssetModel.id == asset.id,
                AssetModel.current_version_id.is_(None),
            )
            .values(current_version_id=asset.current_version_id),
        )
        if result.rowcount != 1:
            raise ConcurrencyError(f"Asset {asset.id} head could not be established")
        self._session.add(
            AssetObjectModel(
                id=object_fact.id,
                workspace_id=object_fact.workspace_id,
                asset_version_id=object_fact.asset_version_id,
                role=object_fact.role,
                backend=object_fact.backend.value,
                location=object_fact.location.value,
                bucket=object_fact.bucket,
                key=object_fact.key,
                provider_version_id=object_fact.provider_version_id,
                etag=object_fact.etag,
                byte_size=object_fact.byte_size,
                sha256=object_fact.sha256,
                state=object_fact.state.value,
                version=object_fact.version,
                created_at=object_fact.created_at,
                updated_at=object_fact.updated_at,
            )
        )
        self._loaded_asset_versions[asset.id] = asset.version
        self._loaded_object_versions[object_fact.id] = object_fact.version

    def get(
        self,
        *,
        workspace_id: str,
        asset_id: str,
        for_update: bool = False,
    ) -> Asset | None:
        statement = select(AssetModel).where(
            AssetModel.workspace_id == workspace_id,
            AssetModel.id == asset_id,
        )
        if for_update:
            statement = statement.with_for_update()
        model = self._session.scalar(statement)
        if model is None:
            return None
        self._loaded_asset_versions[model.id] = model.version
        return _asset_from_model(model)

    def save_asset(self, asset: Asset) -> None:
        original_version = self._loaded_asset_versions.get(asset.id)
        if original_version is None:
            raise ConcurrencyError(f"Asset {asset.id} was not loaded by this transaction")
        if asset.deletion_operation_id is not None:
            # The pending Operation must exist before the Asset deletion head FK moves.
            flush_with_integrity_classification(self._session)
        result = execute_with_integrity_classification(
            self._session,
            update(AssetModel)
            .where(
                AssetModel.workspace_id == asset.workspace_id,
                AssetModel.id == asset.id,
                AssetModel.version == original_version,
            )
            .values(
                status=asset.status.value,
                block_reason=asset.block_reason,
                current_version_id=asset.current_version_id,
                current_rights_record_id=asset.current_rights_record_id,
                retention_deadline=asset.retention_deadline,
                deletion_generation=asset.deletion_generation,
                deletion_operation_id=asset.deletion_operation_id,
                deletion_reason=(asset.deletion_reason.value if asset.deletion_reason else None),
                deletion_requested_at=asset.deletion_requested_at,
                deletion_completed_at=asset.deletion_completed_at,
                version=asset.version,
                updated_at=asset.updated_at,
            ),
        )
        if result.rowcount != 1:
            raise ConcurrencyError(f"Asset {asset.id} was concurrently modified")
        self._loaded_asset_versions[asset.id] = asset.version

    def add_rights_record(self, rights_record: RightsRecord) -> None:
        model = RightsRecordModel(
            id=rights_record.id,
            workspace_id=rights_record.workspace_id,
            asset_id=rights_record.asset_id,
            asset_version_id=rights_record.asset_version_id,
            version_number=rights_record.version_number,
            decision=rights_record.decision.value,
            owner_reference=rights_record.owner_reference,
            source=rights_record.source,
            license_reference=rights_record.license_reference,
            derivative_allowed=rights_record.derivative_allowed,
            public_demo_allowed=rights_record.public_demo_allowed,
            evidence_reference=rights_record.evidence_reference,
            terms_sha256=rights_record.terms_sha256,
            valid_from=rights_record.valid_from,
            valid_until=rights_record.valid_until,
            perpetual=rights_record.perpetual,
            supersedes_record_id=rights_record.supersedes_record_id,
            created_by=rights_record.created_by,
            created_at=rights_record.created_at,
            permissions_sealed_at=None,
        )
        self._session.add(model)
        flush_with_integrity_classification(self._session)
        self._session.add_all(
            [
                RightsRecordUseModel(
                    workspace_id=rights_record.workspace_id,
                    asset_id=rights_record.asset_id,
                    rights_record_id=rights_record.id,
                    allowed_use=allowed_use,
                    created_at=rights_record.created_at,
                )
                for allowed_use in sorted(rights_record.allowed_uses)
            ]
            + [
                RightsRecordProviderModel(
                    workspace_id=rights_record.workspace_id,
                    asset_id=rights_record.asset_id,
                    rights_record_id=rights_record.id,
                    allowed_provider=allowed_provider,
                    created_at=rights_record.created_at,
                )
                for allowed_provider in sorted(rights_record.allowed_providers)
            ]
        )
        flush_with_integrity_classification(self._session)
        model.permissions_sealed_at = rights_record.created_at
        flush_with_integrity_classification(self._session)

    def get_rights_record(
        self,
        *,
        workspace_id: str,
        rights_record_id: str,
    ) -> RightsRecord | None:
        model = self._session.scalar(
            select(RightsRecordModel).where(
                RightsRecordModel.workspace_id == workspace_id,
                RightsRecordModel.id == rights_record_id,
            )
        )
        if model is None:
            return None
        uses, providers = self._permission_sets([model.id])
        return _rights_record_from_model(
            model,
            allowed_uses=uses.get(model.id, frozenset()),
            allowed_providers=providers.get(model.id, frozenset()),
        )

    def get_current_usability_snapshot(
        self,
        *,
        workspace_id: str,
        asset_id: str,
    ) -> CurrentUsabilitySnapshot | None:
        row = execute_with_integrity_classification(
            self._session,
            select(
                AssetModel,
                RightsRecordModel,
                literal_column("UTC_TIMESTAMP(6)").label("database_now"),
            )
            .outerjoin(
                RightsRecordModel,
                and_(
                    RightsRecordModel.workspace_id == AssetModel.workspace_id,
                    RightsRecordModel.id == AssetModel.current_rights_record_id,
                    RightsRecordModel.asset_id == AssetModel.id,
                ),
            )
            .where(
                AssetModel.workspace_id == workspace_id,
                AssetModel.id == asset_id,
            )
            .with_for_update(read=True),
        ).one_or_none()
        if row is None:
            return None
        asset_model, rights_model, database_now = row
        rights_record = None
        if rights_model is not None:
            uses, providers = self._permission_sets([rights_model.id])
            rights_record = _rights_record_from_model(
                rights_model,
                allowed_uses=uses.get(rights_model.id, frozenset()),
                allowed_providers=providers.get(rights_model.id, frozenset()),
            )
        if database_now.tzinfo is None:
            database_now = database_now.replace(tzinfo=UTC)
        return CurrentUsabilitySnapshot(
            asset=_asset_from_model(asset_model),
            rights_record=rights_record,
            database_now=database_now,
        )

    def list_rights_records(
        self,
        *,
        workspace_id: str,
        asset_id: str,
        before_version: int | None,
        limit: int,
    ) -> list[RightsRecord]:
        if limit < 1 or limit > 101:
            raise ValueError("Rights Record history limit must be between 1 and 101")
        statement = select(RightsRecordModel).where(
            RightsRecordModel.workspace_id == workspace_id,
            RightsRecordModel.asset_id == asset_id,
        )
        if before_version is not None:
            statement = statement.where(RightsRecordModel.version_number < before_version)
        models = list(
            self._session.scalars(
                statement.order_by(RightsRecordModel.version_number.desc()).limit(limit)
            )
        )
        uses, providers = self._permission_sets([model.id for model in models])
        return [
            _rights_record_from_model(
                model,
                allowed_uses=uses.get(model.id, frozenset()),
                allowed_providers=providers.get(model.id, frozenset()),
            )
            for model in models
        ]

    def claim_expired_rights(
        self,
        *,
        limit: int,
    ) -> list[RightsScanClaim]:
        if limit < 1:
            raise ValueError("rights expiry scan limit must be positive")
        database_now = literal_column("UTC_TIMESTAMP(6)")
        rows = list(
            self._session.execute(
                select(
                    AssetModel,
                    RightsRecordModel,
                    database_now.label("database_now"),
                )
                .join(
                    RightsRecordModel,
                    and_(
                        RightsRecordModel.workspace_id == AssetModel.workspace_id,
                        RightsRecordModel.id == AssetModel.current_rights_record_id,
                        RightsRecordModel.asset_id == AssetModel.id,
                    ),
                )
                .where(
                    or_(
                        AssetModel.status == AssetState.AVAILABLE.value,
                        AssetModel.status == AssetState.PENDING_RIGHTS.value,
                        and_(
                            AssetModel.status == AssetState.BLOCKED.value,
                            AssetModel.block_reason.in_(
                                (
                                    "RIGHTS_NOT_ACTIVE",
                                    "RIGHTS_PERMISSION_EMPTY",
                                )
                            ),
                        ),
                    ),
                    RightsRecordModel.perpetual.is_(False),
                    RightsRecordModel.valid_until <= database_now,
                )
                .order_by(RightsRecordModel.valid_until, AssetModel.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        record_ids = [rights_model.id for _, rights_model, _ in rows]
        uses, providers = self._permission_sets(record_ids)
        claimed: list[RightsScanClaim] = []
        for asset_model, rights_model, observed_database_now in rows:
            self._loaded_asset_versions[asset_model.id] = asset_model.version
            if observed_database_now.tzinfo is None:
                observed_database_now = observed_database_now.replace(tzinfo=UTC)
            claimed.append(
                RightsScanClaim(
                    asset=_asset_from_model(asset_model),
                    rights_record=_rights_record_from_model(
                        rights_model,
                        allowed_uses=uses.get(rights_model.id, frozenset()),
                        allowed_providers=providers.get(
                            rights_model.id,
                            frozenset(),
                        ),
                    ),
                    database_now=observed_database_now,
                )
            )
        return claimed

    def claim_expired_assets(self, *, limit: int) -> list[AssetRetentionScanClaim]:
        if limit < 1:
            raise ValueError("Asset retention scan limit must be positive")
        database_now = literal_column("UTC_TIMESTAMP(6)")
        rows = list(
            self._session.execute(
                select(AssetModel, database_now.label("database_now"))
                .where(
                    AssetModel.retention_class == RetentionClass.TASK.value,
                    AssetModel.retention_deadline.is_not(None),
                    AssetModel.retention_deadline <= database_now,
                    AssetModel.deletion_operation_id.is_(None),
                    AssetModel.status.not_in({AssetState.DELETING.value, AssetState.DELETED.value}),
                )
                .order_by(AssetModel.retention_deadline, AssetModel.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        claims: list[AssetRetentionScanClaim] = []
        for asset_model, observed_database_now in rows:
            self._loaded_asset_versions[asset_model.id] = asset_model.version
            if observed_database_now.tzinfo is None:
                observed_database_now = observed_database_now.replace(tzinfo=UTC)
            claims.append(
                AssetRetentionScanClaim(
                    asset=_asset_from_model(asset_model),
                    database_now=observed_database_now,
                )
            )
        return claims

    def claim_activatable_rights(
        self,
        *,
        limit: int,
    ) -> list[RightsScanClaim]:
        if limit < 1:
            raise ValueError("rights activation scan limit must be positive")
        database_now = literal_column("UTC_TIMESTAMP(6)")
        rows = list(
            self._session.execute(
                select(
                    AssetModel,
                    RightsRecordModel,
                    database_now.label("database_now"),
                )
                .join(
                    RightsRecordModel,
                    and_(
                        RightsRecordModel.workspace_id == AssetModel.workspace_id,
                        RightsRecordModel.id == AssetModel.current_rights_record_id,
                        RightsRecordModel.asset_id == AssetModel.id,
                    ),
                )
                .where(
                    or_(
                        AssetModel.status == AssetState.PENDING_RIGHTS.value,
                        and_(
                            AssetModel.status == AssetState.BLOCKED.value,
                            AssetModel.block_reason == "RIGHTS_NOT_ACTIVE",
                        ),
                    ),
                    RightsRecordModel.decision == RightsRecordDecision.GRANT.value,
                    RightsRecordModel.valid_from <= database_now,
                    or_(
                        RightsRecordModel.perpetual.is_(True),
                        RightsRecordModel.valid_until > database_now,
                    ),
                    exists().where(RightsRecordUseModel.rights_record_id == RightsRecordModel.id),
                    exists().where(
                        RightsRecordProviderModel.rights_record_id == RightsRecordModel.id
                    ),
                    or_(
                        AssetModel.retention_deadline.is_(None),
                        AssetModel.retention_deadline > database_now,
                    ),
                )
                .order_by(RightsRecordModel.valid_from, AssetModel.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        record_ids = [rights_model.id for _, rights_model, _ in rows]
        uses, providers = self._permission_sets(record_ids)
        claimed: list[RightsScanClaim] = []
        for asset_model, rights_model, observed_database_now in rows:
            self._loaded_asset_versions[asset_model.id] = asset_model.version
            if observed_database_now.tzinfo is None:
                observed_database_now = observed_database_now.replace(tzinfo=UTC)
            claimed.append(
                RightsScanClaim(
                    asset=_asset_from_model(asset_model),
                    rights_record=_rights_record_from_model(
                        rights_model,
                        allowed_uses=uses.get(rights_model.id, frozenset()),
                        allowed_providers=providers.get(
                            rights_model.id,
                            frozenset(),
                        ),
                    ),
                    database_now=observed_database_now,
                )
            )
        return claimed

    def _permission_sets(
        self,
        rights_record_ids: list[str],
    ) -> tuple[dict[str, frozenset[str]], dict[str, frozenset[str]]]:
        if not rights_record_ids:
            return {}, {}
        use_values: dict[str, set[str]] = {}
        for record_id, value in self._session.execute(
            select(
                RightsRecordUseModel.rights_record_id,
                RightsRecordUseModel.allowed_use,
            ).where(RightsRecordUseModel.rights_record_id.in_(rights_record_ids))
        ):
            use_values.setdefault(record_id, set()).add(value)
        provider_values: dict[str, set[str]] = {}
        for record_id, value in self._session.execute(
            select(
                RightsRecordProviderModel.rights_record_id,
                RightsRecordProviderModel.allowed_provider,
            ).where(RightsRecordProviderModel.rights_record_id.in_(rights_record_ids))
        ):
            provider_values.setdefault(record_id, set()).add(value)
        return (
            {key: frozenset(values) for key, values in use_values.items()},
            {key: frozenset(values) for key, values in provider_values.items()},
        )

    def get_version(
        self,
        *,
        workspace_id: str,
        asset_version_id: str,
    ) -> AssetVersion | None:
        model = self._session.scalar(
            select(AssetVersionModel).where(
                AssetVersionModel.workspace_id == workspace_id,
                AssetVersionModel.id == asset_version_id,
            )
        )
        return _asset_version_from_model(model) if model is not None else None

    def get_version_by_upload_session(
        self,
        *,
        workspace_id: str,
        upload_session_id: str,
    ) -> AssetVersion | None:
        model = self._session.scalar(
            select(AssetVersionModel).where(
                AssetVersionModel.workspace_id == workspace_id,
                AssetVersionModel.upload_session_id == upload_session_id,
            )
        )
        return _asset_version_from_model(model) if model is not None else None

    def get_object(
        self,
        *,
        workspace_id: str,
        asset_version_id: str,
        role: str = "ORIGINAL",
        for_update: bool = False,
    ) -> AssetObject | None:
        statement = select(AssetObjectModel).where(
            AssetObjectModel.workspace_id == workspace_id,
            AssetObjectModel.asset_version_id == asset_version_id,
            AssetObjectModel.role == role,
        )
        if for_update:
            statement = statement.with_for_update()
        model = self._session.scalar(statement)
        if model is None:
            return None
        self._loaded_object_versions[model.id] = model.version
        return _asset_object_from_model(model)

    def add_object(self, object_fact: AssetObject) -> None:
        self._session.add(
            AssetObjectModel(
                id=object_fact.id,
                workspace_id=object_fact.workspace_id,
                asset_version_id=object_fact.asset_version_id,
                role=object_fact.role,
                backend=object_fact.backend.value,
                location=object_fact.location.value,
                bucket=object_fact.bucket,
                key=object_fact.key,
                provider_version_id=object_fact.provider_version_id,
                etag=object_fact.etag,
                byte_size=object_fact.byte_size,
                sha256=object_fact.sha256,
                state=object_fact.state.value,
                version=object_fact.version,
                created_at=object_fact.created_at,
                updated_at=object_fact.updated_at,
            )
        )
        self._loaded_object_versions[object_fact.id] = object_fact.version

    def save_object(self, object_fact: AssetObject) -> None:
        original_version = self._loaded_object_versions.get(object_fact.id)
        if original_version is None:
            raise ConcurrencyError(
                f"Asset object {object_fact.id} was not loaded by this transaction"
            )
        result = execute_with_integrity_classification(
            self._session,
            update(AssetObjectModel)
            .where(
                AssetObjectModel.workspace_id == object_fact.workspace_id,
                AssetObjectModel.id == object_fact.id,
                AssetObjectModel.version == original_version,
            )
            .values(
                state=object_fact.state.value,
                version=object_fact.version,
                updated_at=object_fact.updated_at,
            ),
        )
        if result.rowcount != 1:
            raise ConcurrencyError(f"Asset object {object_fact.id} was concurrently modified")
        self._loaded_object_versions[object_fact.id] = object_fact.version

    def add_validation_result(self, result: AssetValidationResult) -> None:
        self._session.add(_validation_result_to_model(result))

    def get_validation_result(
        self,
        *,
        workspace_id: str,
        asset_version_id: str,
        operation_id: str,
        attempt_number: int,
        stage: ValidationStage,
        validator_name: str,
        validator_version: str,
        policy_version: str,
    ) -> AssetValidationResult | None:
        model = self._session.scalar(
            select(AssetValidationResultModel).where(
                AssetValidationResultModel.workspace_id == workspace_id,
                AssetValidationResultModel.asset_version_id == asset_version_id,
                AssetValidationResultModel.operation_id == operation_id,
                AssetValidationResultModel.attempt_number == attempt_number,
                AssetValidationResultModel.stage == stage.value,
                AssetValidationResultModel.validator_name == validator_name,
                AssetValidationResultModel.validator_version == validator_version,
                AssetValidationResultModel.policy_version == policy_version,
            )
        )
        return _validation_result_from_model(model) if model is not None else None

    def list_validation_results(
        self,
        *,
        workspace_id: str,
        asset_version_id: str,
    ) -> list[AssetValidationResult]:
        models = self._session.scalars(
            select(AssetValidationResultModel)
            .where(
                AssetValidationResultModel.workspace_id == workspace_id,
                AssetValidationResultModel.asset_version_id == asset_version_id,
            )
            .order_by(
                AssetValidationResultModel.created_at,
                AssetValidationResultModel.id,
            )
        )
        return [_validation_result_from_model(model) for model in models]


class AssetAssociationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def workflow_retention_facts(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
    ) -> WorkflowRetentionFacts | None:
        row = self._session.execute(
            select(WorkflowModel.created_at, WorkflowModel.expires_at).where(
                WorkflowModel.workspace_id == workspace_id,
                WorkflowModel.id == workflow_id,
            )
        ).one_or_none()
        if row is None:
            return None
        return WorkflowRetentionFacts(created_at=row[0], expires_at=row[1])

    def product_exists(self, *, workspace_id: str, product_id: str) -> bool:
        return bool(
            self._session.scalar(
                select(
                    exists().where(
                        ProductModel.workspace_id == workspace_id,
                        ProductModel.id == product_id,
                    )
                )
            )
        )

    def sku_exists(
        self,
        *,
        workspace_id: str,
        product_id: str,
        sku_id: str,
    ) -> bool:
        return bool(
            self._session.scalar(
                select(
                    exists().where(
                        SKUModel.workspace_id == workspace_id,
                        SKUModel.product_id == product_id,
                        SKUModel.id == sku_id,
                    )
                )
            )
        )


class SqlAlchemyAssetUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self._depth_token: object | None = None
        self._committed = False

    def __enter__(self) -> SqlAlchemyAssetUnitOfWork:
        self._session = self._session_factory()
        self._depth_token = enter_unit_of_work()
        self.upload_sessions = UploadSessionRepository(self._session)
        self.assets = AssetRepository(self._session)
        self.associations = AssetAssociationRepository(self._session)
        self.idempotency = IdempotencyRepository(self._session)
        self.operations = OperationRepository(self._session)
        self.asset_deletions = AssetDeletionRepository(self._session)
        self.outbox = OutboxRepository(self._session)
        self.audit = AuditRepository(self._session)
        return self

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("Asset Registry unit of work is not active")
        try:
            self._session.commit()
        except DBAPIError as exc:
            self._session.rollback()
            classified = classify_database_error(exc)
            if classified is None:
                raise
            raise classified from exc
        self._committed = True

    def commit_before_retention_deadline(
        self,
        *,
        workspace_id: str,
        asset_id: str,
        retention_deadline: datetime,
        clock: Callable[[], datetime],
    ) -> None:
        if self._session is None:
            raise RuntimeError("Asset Registry unit of work is not active")
        if retention_deadline.tzinfo is None or retention_deadline.utcoffset() != timedelta(0):
            raise ValueError("retention deadline must be timezone-aware UTC")
        observed_at = clock()
        if observed_at.tzinfo is None or observed_at.utcoffset() != timedelta(0):
            raise ValueError("retention commit clock must return timezone-aware UTC")
        flush_with_integrity_classification(self._session)
        row = execute_with_integrity_classification(
            self._session,
            select(
                AssetModel.retention_deadline,
                literal_column("UTC_TIMESTAMP(6)").label("database_now"),
            )
            .where(
                AssetModel.workspace_id == workspace_id,
                AssetModel.id == asset_id,
            )
            .with_for_update(),
        ).one_or_none()
        if row is None:
            self._session.rollback()
            raise ConcurrencyError(f"Asset {asset_id} disappeared before commit")
        persisted_deadline = row.retention_deadline
        database_now = row.database_now
        if persisted_deadline is None:
            self._session.rollback()
            raise ConcurrencyError(f"Task Asset {asset_id} lost its retention deadline")
        if persisted_deadline.tzinfo is None:
            persisted_deadline = persisted_deadline.replace(tzinfo=UTC)
        if database_now.tzinfo is None:
            database_now = database_now.replace(tzinfo=UTC)
        if persisted_deadline != retention_deadline:
            self._session.rollback()
            raise ConcurrencyError(f"Task Asset {asset_id} retention deadline changed")
        if observed_at >= retention_deadline or database_now >= retention_deadline:
            self._session.rollback()
            raise AssetRetentionCommitExpiredError(
                observed_at=max(observed_at, database_now),
                retention_deadline=retention_deadline,
            )
        self.commit()

    def commit_rights_mutation(
        self,
        *,
        workspace_id: str,
        asset_id: str,
        retention_deadline: datetime | None,
        available_rights_record_id: str | None,
        clock: Callable[[], datetime],
    ) -> None:
        if self._session is None:
            raise RuntimeError("Asset Registry unit of work is not active")
        for value, field_name in ((retention_deadline, "retention deadline"),):
            if value is not None and (value.tzinfo is None or value.utcoffset() != timedelta(0)):
                raise ValueError(f"{field_name} must be timezone-aware UTC")
        observed_at = clock()
        if observed_at.tzinfo is None or observed_at.utcoffset() != timedelta(0):
            raise ValueError("rights commit clock must return timezone-aware UTC")

        flush_with_integrity_classification(self._session)
        asset_row = execute_with_integrity_classification(
            self._session,
            select(
                AssetModel.status,
                AssetModel.retention_deadline,
                AssetModel.current_rights_record_id,
                literal_column("UTC_TIMESTAMP(6)").label("database_now"),
            )
            .where(
                AssetModel.workspace_id == workspace_id,
                AssetModel.id == asset_id,
            )
            .with_for_update(),
        ).one_or_none()
        if asset_row is None:
            self._session.rollback()
            raise ConcurrencyError(f"Asset {asset_id} disappeared before rights commit")

        persisted_retention = asset_row.retention_deadline
        database_now = asset_row.database_now
        if persisted_retention is not None and persisted_retention.tzinfo is None:
            persisted_retention = persisted_retention.replace(tzinfo=UTC)
        if database_now.tzinfo is None:
            database_now = database_now.replace(tzinfo=UTC)
        if persisted_retention != retention_deadline:
            self._session.rollback()
            raise ConcurrencyError(f"Asset {asset_id} retention deadline changed")
        if retention_deadline is not None and (
            observed_at >= retention_deadline or database_now >= retention_deadline
        ):
            self._session.rollback()
            raise InvalidTransitionError(
                "Task Asset retention expired at the database commit boundary"
            )

        if asset_row.status == AssetState.AVAILABLE.value:
            if (
                available_rights_record_id is None
                or asset_row.current_rights_record_id != available_rights_record_id
            ):
                self._session.rollback()
                raise ConcurrencyError(
                    f"Asset {asset_id} available Rights Record changed before commit"
                )
            rights_row = execute_with_integrity_classification(
                self._session,
                select(
                    RightsRecordModel.decision,
                    RightsRecordModel.valid_from,
                    RightsRecordModel.valid_until,
                ).where(
                    RightsRecordModel.workspace_id == workspace_id,
                    RightsRecordModel.asset_id == asset_id,
                    RightsRecordModel.id == available_rights_record_id,
                ),
            ).one_or_none()
            if rights_row is None or rights_row.decision != RightsRecordDecision.GRANT.value:
                self._session.rollback()
                raise ConcurrencyError(f"Asset {asset_id} available Rights Record is incomplete")
            valid_from = rights_row.valid_from
            valid_until = rights_row.valid_until
            if valid_from.tzinfo is None:
                valid_from = valid_from.replace(tzinfo=UTC)
            if valid_until is not None and valid_until.tzinfo is None:
                valid_until = valid_until.replace(tzinfo=UTC)
            if observed_at < valid_from or database_now < valid_from:
                self._session.rollback()
                raise InvalidTransitionError(
                    "Asset rights are not active at the database commit boundary"
                )
            if valid_until is not None and (
                observed_at >= valid_until or database_now >= valid_until
            ):
                self._session.rollback()
                raise InvalidTransitionError("Asset rights expired at the database commit boundary")
            final_database_now = literal_column("UTC_TIMESTAMP(6)")
            active_rights = exists().where(
                RightsRecordModel.workspace_id == workspace_id,
                RightsRecordModel.asset_id == asset_id,
                RightsRecordModel.id == available_rights_record_id,
                RightsRecordModel.permissions_sealed_at.is_not(None),
                RightsRecordModel.decision == RightsRecordDecision.GRANT.value,
                RightsRecordModel.valid_from <= final_database_now,
                or_(
                    RightsRecordModel.perpetual.is_(True),
                    RightsRecordModel.valid_until > final_database_now,
                ),
            )
            has_use = exists().where(
                RightsRecordUseModel.workspace_id == workspace_id,
                RightsRecordUseModel.asset_id == asset_id,
                RightsRecordUseModel.rights_record_id == available_rights_record_id,
            )
            has_provider = exists().where(
                RightsRecordProviderModel.workspace_id == workspace_id,
                RightsRecordProviderModel.asset_id == asset_id,
                RightsRecordProviderModel.rights_record_id == available_rights_record_id,
            )
            final_guard = execute_with_integrity_classification(
                self._session,
                update(AssetModel)
                .where(
                    AssetModel.workspace_id == workspace_id,
                    AssetModel.id == asset_id,
                    AssetModel.status == AssetState.AVAILABLE.value,
                    AssetModel.current_rights_record_id == available_rights_record_id,
                    or_(
                        AssetModel.retention_deadline.is_(None),
                        AssetModel.retention_deadline > final_database_now,
                    ),
                    active_rights,
                    has_use,
                    has_provider,
                )
                .values(updated_at=AssetModel.updated_at)
                .execution_options(synchronize_session=False),
            )
            if final_guard.rowcount != 1:
                self._session.rollback()
                raise InvalidTransitionError(
                    "Asset rights crossed a usability boundary before commit"
                )
        elif available_rights_record_id is not None:
            self._session.rollback()
            raise ConcurrencyError(f"Asset {asset_id} is no longer available at rights commit")
        self.commit()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if self._session is not None and (exc_type is not None or not self._committed):
                self._session.rollback()
        finally:
            if self._session is not None:
                self._session.close()
            if self._depth_token is not None:
                exit_unit_of_work(self._depth_token)
