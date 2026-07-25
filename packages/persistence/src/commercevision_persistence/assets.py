"""MySQL adapters for direct-upload sessions and quarantined Assets."""

from __future__ import annotations

from datetime import datetime
from types import TracebackType

from commercevision_application.asset_ports import WorkflowRetentionFacts
from commercevision_domain import (
    Asset,
    AssetKind,
    AssetObject,
    AssetObjectState,
    AssetState,
    AssetVersion,
    ConcurrencyError,
    RetentionClass,
    StorageBackend,
    StorageLocationClass,
    UploadSession,
    UploadSessionState,
)
from sqlalchemy import and_, exists, or_, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from .database import enter_unit_of_work, exit_unit_of_work
from .integrity import (
    classify_database_error,
    execute_with_integrity_classification,
    flush_with_integrity_classification,
)
from .models import (
    AssetModel,
    AssetObjectModel,
    AssetVersionModel,
    ProductModel,
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
        current_version_id=model.current_version_id,
        retention_deadline=model.retention_deadline,
        version=model.version,
        created_at=model.created_at,
        updated_at=model.updated_at,
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
                current_version_id=None,
                retention_deadline=asset.retention_deadline,
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

    def get(self, *, workspace_id: str, asset_id: str) -> Asset | None:
        model = self._session.scalar(
            select(AssetModel).where(
                AssetModel.workspace_id == workspace_id,
                AssetModel.id == asset_id,
            )
        )
        return _asset_from_model(model) if model is not None else None

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
    ) -> AssetObject | None:
        model = self._session.scalar(
            select(AssetObjectModel).where(
                AssetObjectModel.workspace_id == workspace_id,
                AssetObjectModel.asset_version_id == asset_version_id,
                AssetObjectModel.role == role,
            )
        )
        return _asset_object_from_model(model) if model is not None else None


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
