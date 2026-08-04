"""SQLAlchemy models for Phase 1 durable runtime state."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import DATETIME as MYSQL_DATETIME
from sqlalchemy.dialects.mysql import MEDIUMBLOB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from .workspace_identity import exact_string_sql_type, workspace_id_sql_type

MYSQL_DATETIME_FSP = 6


class UTCDateTime(TypeDecorator[datetime]):
    """Persist UTC with microsecond precision and restore an aware datetime."""

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "mysql":
            return dialect.type_descriptor(MYSQL_DATETIME(fsp=MYSQL_DATETIME_FSP))
        return dialect.type_descriptor(DateTime(timezone=False))

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("datetime values must be timezone-aware")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    type_annotation_map = {
        dict[str, Any]: JSON,
        datetime: UTCDateTime(),
    }


class WorkflowModel(Base):
    __tablename__ = "workflows"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_workflows_workspace_id"),
        Index("ix_workflows_workspace_created", "workspace_id", "created_at", "id"),
        Index("ix_workflows_status_updated", "status", "updated_at"),
        Index("ix_workflows_retention_expires", "retention_status", "expires_at"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    workflow_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    retention_status: Mapped[str] = mapped_column(String(24), nullable=False)
    current_node: Mapped[str | None] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    cancellation_requested_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class CatalogExternalIdentityModel(Base):
    __tablename__ = "catalog_external_identities"
    __table_args__ = (
        PrimaryKeyConstraint(
            "workspace_id",
            "source_namespace",
            "external_id",
            name="pk_catalog_external_identity",
        ),
        Index("ix_catalog_external_identity_owner", "owner_type", "owner_id"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    source_namespace: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_type: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ProductModel(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "source_namespace",
            "external_id",
            name="uq_products_external_identity",
        ),
        UniqueConstraint("workspace_id", "id", name="uq_products_workspace_id"),
        Index("ix_products_workspace_created", "workspace_id", "created_at", "id"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    source_namespace: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_version: Mapped[str | None] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    category_code: Mapped[str] = mapped_column(String(128), nullable=False)
    brand: Mapped[str] = mapped_column(String(128), nullable=False)
    attributes_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class SKUModel(Base):
    __tablename__ = "skus"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "source_namespace",
            "external_id",
            name="uq_skus_external_identity",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_skus_workspace_product",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id",
            "product_id",
            "id",
            name="uq_skus_workspace_product_id",
        ),
        Index("ix_skus_workspace_product", "workspace_id", "product_id", "created_at", "id"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    product_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_namespace: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_version: Mapped[str | None] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    category_code: Mapped[str] = mapped_column(String(128), nullable=False)
    brand: Mapped[str] = mapped_column(String(128), nullable=False)
    attributes_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class UploadSessionModel(Base):
    __tablename__ = "upload_sessions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_upload_session_workspace_id"),
        UniqueConstraint("reserved_asset_id", name="uq_upload_session_reserved_asset"),
        UniqueConstraint(
            "reserved_asset_version_id",
            name="uq_upload_session_reserved_asset_version",
        ),
        UniqueConstraint(
            "cleanup_operation_id",
            name="uq_upload_session_cleanup_operation",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            name="fk_upload_session_workspace_workflow",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_upload_session_workspace_product",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "sku_id"],
            ["skus.workspace_id", "skus.product_id", "skus.id"],
            name="fk_upload_session_workspace_sku",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "finalized_asset_version_id"],
            ["asset_versions.workspace_id", "asset_versions.id"],
            name="fk_upload_session_finalized_version",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["workspace_id", "validation_operation_id"],
            ["durable_operations.workspace_id", "durable_operations.id"],
            name="fk_upload_session_validation_operation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "cleanup_operation_id"],
            ["durable_operations.workspace_id", "durable_operations.id"],
            name="fk_upload_session_cleanup_operation",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(retention_class = 'TASK' AND workflow_id IS NOT NULL) "
            "OR (retention_class = 'FOUNDATION' AND workflow_id IS NULL)",
            name="ck_upload_session_retention_owner",
        ),
        CheckConstraint(
            "sku_id IS NULL OR product_id IS NOT NULL",
            name="ck_upload_session_sku_product",
        ),
        CheckConstraint(
            "(retention_class = 'TASK' AND destination_location = 'TASK') "
            "OR (retention_class = 'FOUNDATION' AND destination_location = 'FOUNDATION')",
            name="ck_upload_session_destination_retention",
        ),
        CheckConstraint(
            "storage_location = 'QUARANTINE'",
            name="ck_upload_session_source_quarantine",
        ),
        CheckConstraint(
            "storage_bucket <> destination_bucket OR storage_key <> destination_key",
            name="ck_upload_session_distinct_storage",
        ),
        CheckConstraint(
            "(state = 'FINALIZING' AND finalize_lease_owner IS NOT NULL "
            "AND finalize_lease_token IS NOT NULL AND finalize_lease_expires_at IS NOT NULL) "
            "OR (state <> 'FINALIZING' AND finalize_lease_owner IS NULL "
            "AND finalize_lease_token IS NULL AND finalize_lease_expires_at IS NULL)",
            name="ck_upload_session_finalize_lease",
        ),
        CheckConstraint(
            "(state = 'FINALIZED' AND finalized_asset_version_id IS NOT NULL "
            "AND validation_operation_id IS NOT NULL) "
            "OR (state <> 'FINALIZED' AND finalized_asset_version_id IS NULL "
            "AND validation_operation_id IS NULL)",
            name="ck_upload_session_finalize_result",
        ),
        CheckConstraint(
            "finalized_asset_version_id IS NULL "
            "OR finalized_asset_version_id = reserved_asset_version_id",
            name="ck_upload_session_reserved_result",
        ),
        CheckConstraint(
            "(state = 'ABORTED' AND failure_code IS NOT NULL) "
            "OR (state <> 'ABORTED' AND failure_code IS NULL)",
            name="ck_upload_session_failure_state",
        ),
        CheckConstraint(
            "cleanup_operation_id IS NULL OR state IN ('FINALIZED', 'EXPIRED', 'ABORTED')",
            name="ck_upload_session_cleanup_state",
        ),
        CheckConstraint(
            "(cleanup_operation_id IS NULL AND cleanup_reconcile_until IS NULL) "
            "OR (cleanup_operation_id IS NOT NULL "
            "AND cleanup_reconcile_until IS NOT NULL "
            "AND state IN ('FINALIZED', 'EXPIRED', 'ABORTED'))",
            name="ck_upload_session_cleanup_reconcile_window",
        ),
        CheckConstraint("expected_byte_length > 0", name="ck_upload_session_byte_length"),
        CheckConstraint(
            "version > 0 AND finalize_attempts >= 0",
            name="ck_upload_session_counters",
        ),
        CheckConstraint(
            "CHAR_LENGTH(TRIM(validation_transfer_policy_version)) > 0",
            name="ck_upload_session_transfer_policy_version",
        ),
        CheckConstraint(
            "validation_transfer_policy_snapshot_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_upload_session_transfer_policy_snapshot",
        ),
        Index("ix_upload_session_workspace_state", "workspace_id", "state", "expires_at"),
        Index("ix_upload_session_finalize_lease", "state", "finalize_lease_expires_at"),
        Index("ix_upload_session_expiry_scan", "state", "expires_at", "id"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    reserved_asset_id: Mapped[str] = mapped_column(String(36), nullable=False)
    reserved_asset_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    retention_class: Mapped[str] = mapped_column(String(16), nullable=False)
    asset_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    declared_mime: Mapped[str] = mapped_column(String(128), nullable=False)
    expected_byte_length: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expected_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    workflow_id: Mapped[str | None] = mapped_column(String(36))
    product_id: Mapped[str | None] = mapped_column(String(36))
    sku_id: Mapped[str | None] = mapped_column(String(36))
    category: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    upload_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    integrity_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    validation_transfer_policy_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    validation_transfer_policy_snapshot_sha256: Mapped[str] = mapped_column(
        exact_string_sql_type(64),
        nullable=False,
    )
    storage_backend: Mapped[str] = mapped_column(String(16), nullable=False)
    storage_location: Mapped[str] = mapped_column(String(24), nullable=False)
    storage_bucket: Mapped[str] = mapped_column(exact_string_sql_type(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(exact_string_sql_type(512), nullable=False)
    destination_location: Mapped[str] = mapped_column(String(24), nullable=False)
    destination_bucket: Mapped[str] = mapped_column(exact_string_sql_type(255), nullable=False)
    destination_key: Mapped[str] = mapped_column(exact_string_sql_type(512), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    finalize_lease_owner: Mapped[str | None] = mapped_column(String(128))
    finalize_lease_token: Mapped[str | None] = mapped_column(String(36))
    finalize_lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finalize_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(128))
    finalized_asset_version_id: Mapped[str | None] = mapped_column(String(36))
    validation_operation_id: Mapped[str | None] = mapped_column(String(36))
    cleanup_operation_id: Mapped[str | None] = mapped_column(String(36))
    cleanup_reconcile_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class AssetModel(Base):
    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_assets_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            name="fk_assets_workspace_workflow",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_assets_workspace_product",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "sku_id"],
            ["skus.workspace_id", "skus.product_id", "skus.id"],
            name="fk_assets_workspace_sku",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "current_version_id"],
            ["asset_versions.workspace_id", "asset_versions.id"],
            name="fk_assets_workspace_current_version",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        CheckConstraint(
            "(retention_class = 'TASK' AND workflow_id IS NOT NULL) "
            "OR (retention_class = 'FOUNDATION' AND workflow_id IS NULL)",
            name="ck_assets_retention_owner",
        ),
        CheckConstraint(
            "(retention_class = 'TASK' AND retention_deadline IS NOT NULL) "
            "OR (retention_class = 'FOUNDATION' AND retention_deadline IS NULL)",
            name="ck_assets_retention_deadline",
        ),
        CheckConstraint("sku_id IS NULL OR product_id IS NOT NULL", name="ck_assets_sku_product"),
        CheckConstraint(
            "(status = 'BLOCKED' AND block_reason IS NOT NULL) "
            "OR (status <> 'BLOCKED' AND block_reason IS NULL)",
            name="ck_assets_block_reason",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "current_rights_record_id", "id"],
            ["rights_records.workspace_id", "rights_records.id", "rights_records.asset_id"],
            name="fk_assets_current_rights_record",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["workspace_id", "deletion_operation_id"],
            ["durable_operations.workspace_id", "durable_operations.id"],
            name="fk_assets_deletion_operation",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        CheckConstraint(
            "deletion_generation >= 0 AND ((deletion_generation = 0 "
            "AND deletion_operation_id IS NULL AND deletion_reason IS NULL "
            "AND deletion_requested_at IS NULL AND deletion_completed_at IS NULL) "
            "OR (deletion_generation > 0 AND deletion_operation_id IS NOT NULL "
            "AND deletion_reason IS NOT NULL AND deletion_requested_at IS NOT NULL))",
            name="ck_assets_deletion_identity",
        ),
        CheckConstraint(
            "deletion_completed_at IS NULL OR status = 'DELETED'",
            name="ck_assets_deletion_completion",
        ),
        Index("ix_assets_workspace_status", "workspace_id", "status", "updated_at", "id"),
        Index("ix_assets_retention_deadline", "status", "retention_deadline"),
        Index(
            "ix_assets_retention_cleanup_due",
            "retention_class",
            "deletion_operation_id",
            "retention_deadline",
            "id",
        ),
        Index(
            "ix_assets_deletion_progress",
            "status",
            "deletion_requested_at",
            "workspace_id",
            "id",
        ),
        Index(
            "ix_assets_current_rights",
            "workspace_id",
            "current_rights_record_id",
            "status",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    retention_class: Mapped[str] = mapped_column(String(16), nullable=False)
    asset_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    workflow_id: Mapped[str | None] = mapped_column(String(36))
    product_id: Mapped[str | None] = mapped_column(String(36))
    sku_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    block_reason: Mapped[str | None] = mapped_column(String(64))
    current_version_id: Mapped[str | None] = mapped_column(String(36))
    current_rights_record_id: Mapped[str | None] = mapped_column(String(36))
    retention_deadline: Mapped[datetime | None] = mapped_column(UTCDateTime())
    deletion_generation: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    deletion_operation_id: Mapped[str | None] = mapped_column(String(36))
    deletion_reason: Mapped[str | None] = mapped_column(String(32))
    deletion_requested_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    deletion_completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class AssetVersionModel(Base):
    __tablename__ = "asset_versions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_asset_version_workspace_id"),
        UniqueConstraint(
            "workspace_id",
            "id",
            "asset_id",
            name="uq_asset_versions_workspace_id_asset",
        ),
        UniqueConstraint("upload_session_id", name="uq_asset_version_upload_session"),
        UniqueConstraint(
            "asset_id",
            "version_number",
            name="uq_asset_version_number",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "asset_id"],
            ["assets.workspace_id", "assets.id"],
            name="fk_asset_version_workspace_asset",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "upload_session_id"],
            ["upload_sessions.workspace_id", "upload_sessions.id"],
            name="fk_asset_version_workspace_upload",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version_number > 0", name="ck_asset_version_number"),
        CheckConstraint("byte_size > 0", name="ck_asset_version_byte_size"),
        CheckConstraint(
            "(detected_mime IS NULL AND image_format IS NULL "
            "AND width IS NULL AND height IS NULL AND frame_count IS NULL) OR "
            "(detected_mime IS NOT NULL AND image_format IS NOT NULL "
            "AND width > 0 AND height > 0 AND frame_count > 0)",
            name="ck_asset_version_image_facts",
        ),
        CheckConstraint(
            "CHAR_LENGTH(TRIM(validation_transfer_policy_version)) > 0",
            name="ck_asset_version_transfer_policy_version",
        ),
        CheckConstraint(
            "validation_transfer_policy_snapshot_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_asset_version_transfer_policy_snapshot",
        ),
        Index("ix_asset_version_workspace_sha", "workspace_id", "sha256"),
        Index("ix_asset_version_asset_created", "asset_id", "created_at", "id"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(36), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    upload_session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    declared_mime: Mapped[str] = mapped_column(String(128), nullable=False)
    detected_mime: Mapped[str | None] = mapped_column(String(128))
    image_format: Mapped[str | None] = mapped_column(String(16))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    frame_count: Mapped[int | None] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    integrity_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    validation_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    validation_transfer_policy_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    validation_transfer_policy_snapshot_sha256: Mapped[str] = mapped_column(
        exact_string_sql_type(64),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class RightsRecordModel(Base):
    __tablename__ = "rights_records"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_rights_records_workspace_id",
        ),
        UniqueConstraint(
            "workspace_id",
            "id",
            "asset_id",
            name="uq_rights_records_workspace_id_asset",
        ),
        UniqueConstraint(
            "workspace_id",
            "id",
            "asset_id",
            "version_number",
            name="uq_rights_records_exact_version",
        ),
        UniqueConstraint(
            "asset_id",
            "version_number",
            name="uq_rights_records_asset_version",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "asset_id"],
            ["assets.workspace_id", "assets.id"],
            name="fk_rights_records_workspace_asset",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "asset_version_id", "asset_id"],
            [
                "asset_versions.workspace_id",
                "asset_versions.id",
                "asset_versions.asset_id",
            ],
            name="fk_rights_records_workspace_asset_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "supersedes_record_id", "asset_id"],
            ["rights_records.workspace_id", "rights_records.id", "rights_records.asset_id"],
            name="fk_rights_records_supersedes",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version_number > 0", name="ck_rights_records_version"),
        CheckConstraint(
            "decision IN ('GRANT', 'REVOKE')",
            name="ck_rights_records_decision",
        ),
        CheckConstraint(
            "(perpetual = 1 AND valid_until IS NULL) OR "
            "(perpetual = 0 AND valid_until IS NOT NULL AND valid_until > valid_from)",
            name="ck_rights_records_validity",
        ),
        CheckConstraint(
            "terms_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_rights_records_terms_sha256",
        ),
        Index(
            "ix_rights_records_current_expiry",
            "perpetual",
            "valid_until",
            "asset_id",
            "id",
        ),
        Index(
            "ix_rights_records_activation",
            "decision",
            "valid_from",
            "valid_until",
            "asset_id",
            "id",
        ),
        Index(
            "ix_rights_records_asset_created",
            "workspace_id",
            "asset_id",
            "version_number",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(36), nullable=False)
    asset_version_id: Mapped[str | None] = mapped_column(String(36))
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_reference: Mapped[str] = mapped_column(String(256), nullable=False)
    source: Mapped[str] = mapped_column(String(256), nullable=False)
    license_reference: Mapped[str] = mapped_column(String(256), nullable=False)
    derivative_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    public_demo_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    evidence_reference: Mapped[str] = mapped_column(String(512), nullable=False)
    terms_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    perpetual: Mapped[bool] = mapped_column(Boolean, nullable=False)
    supersedes_record_id: Mapped[str | None] = mapped_column(String(36))
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    permissions_sealed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class RightsRecordUseModel(Base):
    __tablename__ = "rights_record_uses"
    __table_args__ = (
        PrimaryKeyConstraint(
            "rights_record_id",
            "allowed_use",
            name="pk_rights_record_uses",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "rights_record_id", "asset_id"],
            ["rights_records.workspace_id", "rights_records.id", "rights_records.asset_id"],
            name="fk_rights_record_uses_rights_record",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_rights_record_uses_authorization",
            "workspace_id",
            "allowed_use",
            "asset_id",
            "rights_record_id",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(36), nullable=False)
    rights_record_id: Mapped[str] = mapped_column(String(36), nullable=False)
    allowed_use: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class RightsRecordProviderModel(Base):
    __tablename__ = "rights_record_providers"
    __table_args__ = (
        PrimaryKeyConstraint(
            "rights_record_id",
            "allowed_provider",
            name="pk_rights_record_providers",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "rights_record_id", "asset_id"],
            ["rights_records.workspace_id", "rights_records.id", "rights_records.asset_id"],
            name="fk_rights_record_providers_rights_record",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_rights_record_providers_authorization",
            "workspace_id",
            "allowed_provider",
            "asset_id",
            "rights_record_id",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(36), nullable=False)
    rights_record_id: Mapped[str] = mapped_column(String(36), nullable=False)
    allowed_provider: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class AssetObjectModel(Base):
    __tablename__ = "asset_objects"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_asset_object_workspace_id"),
        UniqueConstraint(
            "asset_version_id",
            "role",
            name="uq_asset_object_version_role",
        ),
        UniqueConstraint(
            "workspace_id",
            "id",
            "asset_version_id",
            name="uq_asset_object_workspace_id_version",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "asset_version_id"],
            ["asset_versions.workspace_id", "asset_versions.id"],
            name="fk_asset_object_workspace_version",
            ondelete="RESTRICT",
        ),
        CheckConstraint("byte_size > 0", name="ck_asset_object_byte_size"),
        CheckConstraint(
            "CHAR_LENGTH(TRIM(provider_version_id)) > 0 "
            "AND LOWER(TRIM(provider_version_id)) <> 'null'",
            name="ck_asset_object_provider_version",
        ),
        CheckConstraint(
            "state <> 'QUARANTINED' OR location = 'QUARANTINE'",
            name="ck_asset_object_quarantine_location",
        ),
        Index("ix_asset_object_workspace_state", "workspace_id", "state", "updated_at"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    asset_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    backend: Mapped[str] = mapped_column(String(16), nullable=False)
    location: Mapped[str] = mapped_column(String(24), nullable=False)
    bucket: Mapped[str] = mapped_column(exact_string_sql_type(255), nullable=False)
    key: Mapped[str] = mapped_column(exact_string_sql_type(512), nullable=False)
    provider_version_id: Mapped[str] = mapped_column(
        exact_string_sql_type(256),
        nullable=False,
    )
    etag: Mapped[str] = mapped_column(exact_string_sql_type(512), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class AssetValidationResultModel(Base):
    __tablename__ = "asset_validation_results"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_asset_validation_workspace_id",
        ),
        UniqueConstraint(
            "workspace_id",
            "asset_version_id",
            "attempt_number",
            "stage",
            "validator_name",
            "validator_version",
            "policy_version",
            name="uq_asset_validation_stage_attempt",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "operation_id"],
            ["durable_operations.workspace_id", "durable_operations.id"],
            name="fk_asset_validation_workspace_operation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "asset_version_id"],
            ["asset_versions.workspace_id", "asset_versions.id"],
            name="fk_asset_validation_workspace_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "asset_object_id", "asset_version_id"],
            [
                "asset_objects.workspace_id",
                "asset_objects.id",
                "asset_objects.asset_version_id",
            ],
            name="fk_asset_validation_workspace_object_version",
            ondelete="RESTRICT",
        ),
        CheckConstraint("attempt_number > 0", name="ck_asset_validation_attempt"),
        CheckConstraint(
            "stage IN ('LOCAL_FORMAT', 'MALWARE', 'CONTENT_SAFETY', 'PROVENANCE', 'PROMOTION')",
            name="ck_asset_validation_stage",
        ),
        CheckConstraint(
            "CHAR_LENGTH(TRIM(validator_name)) BETWEEN 1 AND 64 "
            "AND CHAR_LENGTH(TRIM(validator_version)) BETWEEN 1 AND 128 "
            "AND CHAR_LENGTH(TRIM(policy_version)) BETWEEN 1 AND 64",
            name="ck_asset_validation_validator_identity",
        ),
        CheckConstraint(
            "(verdict IN ('PASS', 'NOT_APPLICABLE') AND reason_code IS NULL) "
            "OR (verdict IN ('REVIEW', 'BLOCK', 'RETRYABLE_FAILURE', "
            "'TERMINAL_FAILURE') "
            "AND reason_code IS NOT NULL)",
            name="ck_asset_validation_verdict_reason",
        ),
        CheckConstraint(
            "CHAR_LENGTH(TRIM(object_provider_version_id)) > 0 "
            "AND LOWER(TRIM(object_provider_version_id)) <> 'null'",
            name="ck_asset_validation_provider_version",
        ),
        CheckConstraint(
            "CHAR_LENGTH(TRIM(object_etag)) > 0",
            name="ck_asset_validation_object_identity",
        ),
        CheckConstraint(
            "content_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_asset_validation_content_sha256",
        ),
        CheckConstraint(
            "retention_deadline IS NULL OR retention_deadline > created_at",
            name="ck_asset_validation_retention",
        ),
        CheckConstraint(
            "JSON_TYPE(evidence_json) = 'OBJECT'",
            name="ck_asset_validation_evidence_object",
        ),
        Index(
            "ix_asset_validation_version_stage",
            "workspace_id",
            "asset_version_id",
            "stage",
            "created_at",
            "id",
        ),
        Index(
            "ix_asset_validation_operation_attempt",
            "workspace_id",
            "operation_id",
            "attempt_number",
            "created_at",
        ),
        Index(
            "ix_asset_validation_retention",
            "retention_deadline",
            "id",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    asset_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    asset_object_id: Mapped[str] = mapped_column(String(36), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    validator_name: Mapped[str] = mapped_column(String(64), nullable=False)
    validator_version: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    verdict: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64))
    object_provider_version_id: Mapped[str] = mapped_column(
        exact_string_sql_type(256),
        nullable=False,
    )
    object_etag: Mapped[str] = mapped_column(
        exact_string_sql_type(512),
        nullable=False,
    )
    content_sha256: Mapped[str] = mapped_column(
        exact_string_sql_type(64),
        nullable=False,
    )
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    retention_deadline: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class WorkflowStepModel(Base):
    __tablename__ = "workflow_steps"
    __table_args__ = (
        UniqueConstraint("workflow_id", "step_key", name="uq_workflow_steps_key"),
        Index("ix_workflow_steps_workflow_sequence", "workflow_id", "sequence"),
        Index("ix_workflow_steps_lease", "status", "lease_expires_at"),
        Index("ix_workflow_steps_retry", "status", "next_attempt_at"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False
    )
    step_key: Mapped[str] = mapped_column(String(160), nullable=False)
    step_type: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_workflow_version: Mapped[int] = mapped_column(Integer, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    input_ref: Mapped[str | None] = mapped_column(String(512))
    output_ref: Mapped[str | None] = mapped_column(String(512))
    input_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_class: Mapped[str | None] = mapped_column(String(160))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)


class WorkflowAttemptModel(Base):
    __tablename__ = "workflow_attempts"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_workflow_attempts_idempotency"),
        UniqueConstraint("step_id", "attempt_number", name="uq_workflow_attempts_number"),
        Index("ix_workflow_attempts_workflow", "workflow_id", "created_at"),
        Index("ix_workflow_attempts_status", "status", "updated_at"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False
    )
    step_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workflow_steps.id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_request_id: Mapped[str | None] = mapped_column(String(256))
    request_ref: Mapped[str | None] = mapped_column(String(512))
    result_ref: Mapped[str | None] = mapped_column(String(512))
    request_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_class: Mapped[str | None] = mapped_column(String(160))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    version: Mapped[int] = mapped_column(Integer, nullable=False)


class ApprovalModel(Base):
    __tablename__ = "workflow_approvals"
    __table_args__ = (
        Index("ix_workflow_approvals_workflow", "workflow_id", "created_at"),
        UniqueConstraint(
            "workflow_id",
            "approval_type",
            "subject_id",
            "subject_version",
            "decision",
            "expected_workflow_version",
            name="uq_workflow_approvals_replay",
        ),
        UniqueConstraint(
            "id",
            "workflow_id",
            "subject_id",
            "subject_version",
            "approval_type",
            "decision",
            name="uq_workflow_approvals_confirmation_subject",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False
    )
    approval_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(128), nullable=False)
    subject_version: Mapped[int] = mapped_column(Integer, nullable=False)
    decision: Mapped[str] = mapped_column(String(24), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(128))
    comment_ref: Mapped[str | None] = mapped_column(String(512))
    approved_by: Mapped[str] = mapped_column(String(128), nullable=False)
    expected_workflow_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class IdempotencyKeyModel(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("scope", "key_hash", name="uq_idempotency_scope_key"),
        Index("ix_idempotency_expires", "expires_at"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scope: Mapped[str] = mapped_column(exact_string_sql_type(160), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    response_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class OutboxEventModel(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_outbox_workspace_id",
        ),
        CheckConstraint(
            "source_dead_letter_id IS NULL OR workspace_id IS NOT NULL",
            name="ck_outbox_source_workspace",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "source_dead_letter_id"],
            ["dead_letter_messages.workspace_id", "dead_letter_messages.id"],
            name="fk_outbox_source_dead_letter",
            ondelete="RESTRICT",
        ),
        Index("ix_outbox_ready", "published_at", "available_at", "locked_until"),
        Index("ix_outbox_aggregate", "aggregate_type", "aggregate_id", "occurred_at"),
        Index("ix_outbox_rebuild_replay", "event_type", "occurred_at", "id"),
        Index(
            "ix_outbox_workspace_source_dead_letter",
            "workspace_id",
            "source_dead_letter_id",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    available_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    publish_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lock_owner: Mapped[str | None] = mapped_column(String(128))
    lock_token: Mapped[str | None] = mapped_column(String(36))
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(Text)
    workspace_id: Mapped[str | None] = mapped_column(workspace_id_sql_type())
    source_dead_letter_id: Mapped[str | None] = mapped_column(String(36))
    replay_attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class InboxMessageModel(Base):
    __tablename__ = "inbox_messages"
    __table_args__ = (
        Index("ix_inbox_lease", "status", "lease_expires_at"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    consumer: Mapped[str] = mapped_column(String(128), primary_key=True)
    message_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    delivery_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    error_class: Mapped[str | None] = mapped_column(String(160))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class DurableOperationModel(Base):
    __tablename__ = "durable_operations"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "kind",
            "target_type",
            "target_id",
            "target_version",
            "input_hash",
            name="uq_durable_operation_logical",
        ),
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_durable_operation_workspace_id",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "dead_letter_id"],
            ["dead_letter_messages.workspace_id", "dead_letter_messages.id"],
            name="fk_durable_operation_dead_letter",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "replay_source_dead_letter_id"],
            ["dead_letter_messages.workspace_id", "dead_letter_messages.id"],
            name="fk_durable_operation_replay_source",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_durable_operation_ready",
            "state",
            "next_attempt_at",
            "next_reconciliation_at",
            "lease_expires_at",
        ),
        Index(
            "ix_durable_operation_workspace_created",
            "workspace_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_durable_operation_recovery_scan",
            "state",
            "recovery_pending",
            "updated_at",
            "id",
        ),
        Index(
            "ix_durable_operation_workspace_dead_letter",
            "workspace_id",
            "dead_letter_id",
        ),
        Index(
            "ix_durable_operation_workspace_replay_source",
            "workspace_id",
            "replay_source_dead_letter_id",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str] = mapped_column(String(128), nullable=False)
    target_version: Mapped[int] = mapped_column(Integer, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_ref: Mapped[str | None] = mapped_column(String(512))
    output_ref: Mapped[str | None] = mapped_column(String(512))
    provider_request_id: Mapped[str | None] = mapped_column(String(256))
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    execution_deadline_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    reconciliation_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    max_reconciliation_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    next_reconciliation_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    reconciliation_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    reconciliation_deadline_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    reconciliation_required: Mapped[bool] = mapped_column(nullable=False)
    reconciliation_outcome: Mapped[str] = mapped_column(String(40), nullable=False)
    dead_letter_id: Mapped[str | None] = mapped_column(String(36))
    replay_source_dead_letter_id: Mapped[str | None] = mapped_column(String(36))
    replay_attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recovery_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recovery_consumed_generation: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    recovery_pending: Mapped[bool | None] = mapped_column(
        Boolean,
        Computed("recovery_generation <> recovery_consumed_generation", persisted=True),
        nullable=True,
    )
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_category: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    error_retryable: Mapped[bool | None] = mapped_column()
    error_provider_request_id: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    version: Mapped[int] = mapped_column(Integer, nullable=False)


class DeadLetterMessageModel(Base):
    __tablename__ = "dead_letter_messages"
    __table_args__ = (
        UniqueConstraint("consumer", "message_id", name="uq_dead_letter_message"),
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_dead_letter_workspace_id",
        ),
        CheckConstraint(
            "source_dead_letter_id IS NULL OR workspace_id IS NOT NULL",
            name="ck_dead_letter_source_workspace",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "source_dead_letter_id"],
            ["dead_letter_messages.workspace_id", "dead_letter_messages.id"],
            name="fk_dead_letter_source",
            ondelete="RESTRICT",
        ),
        Index("ix_dead_letter_created", "created_at"),
        Index("ix_dead_letter_workspace_created", "workspace_id", "created_at", "id"),
        Index(
            "ix_dead_letter_workspace_source",
            "workspace_id",
            "source_dead_letter_id",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    consumer: Mapped[str] = mapped_column(String(128), nullable=False)
    message_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    reason: Mapped[str] = mapped_column(String(256), nullable=False)
    error_class: Mapped[str | None] = mapped_column(String(160))
    error_message: Mapped[str | None] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    original_created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    replayed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    workspace_id: Mapped[str | None] = mapped_column(workspace_id_sql_type())
    source_dead_letter_id: Mapped[str | None] = mapped_column(String(36))
    replay_attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class DeadLetterReplayModel(Base):
    __tablename__ = "dead_letter_replays"
    __table_args__ = (
        UniqueConstraint(
            "source_dead_letter_id",
            "replay_attempt",
            name="uq_dead_letter_replay_attempt",
        ),
        UniqueConstraint(
            "replay_event_id",
            name="uq_dead_letter_replay_event",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "source_dead_letter_id"],
            ["dead_letter_messages.workspace_id", "dead_letter_messages.id"],
            name="fk_dead_letter_replay_source",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "replay_event_id"],
            ["outbox_events.workspace_id", "outbox_events.id"],
            name="fk_dead_letter_replay_event",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "operation_id"],
            ["durable_operations.workspace_id", "durable_operations.id"],
            name="fk_dead_letter_replay_operation",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_dead_letter_replay_source",
            "source_dead_letter_id",
            "replayed_at",
            "id",
        ),
        Index(
            "ix_dead_letter_replay_claim",
            "operation_id",
            "lifecycle_state",
            "claim_token",
        ),
        Index(
            "ix_dead_letter_replay_workspace_source",
            "workspace_id",
            "source_dead_letter_id",
        ),
        Index(
            "ix_dead_letter_replay_workspace_event",
            "workspace_id",
            "replay_event_id",
        ),
        Index(
            "ix_dead_letter_replay_workspace_operation",
            "workspace_id",
            "operation_id",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_dead_letter_id: Mapped[str] = mapped_column(String(36), nullable=False)
    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(String(512), nullable=False)
    replayed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    replay_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    replay_event_id: Mapped[str] = mapped_column(String(36), nullable=False)
    lifecycle_state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="RECORDED",
        server_default="RECORDED",
    )
    operation_id: Mapped[str | None] = mapped_column(String(36))
    preparation_kind: Mapped[str | None] = mapped_column(String(24))
    work_kind: Mapped[str | None] = mapped_column(String(20))
    prepared_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    prepared_operation_version: Mapped[int | None] = mapped_column(Integer)
    claim_token: Mapped[str | None] = mapped_column(String(36))
    claimed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    claimed_operation_version: Mapped[int | None] = mapped_column(Integer)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    completed_operation_version: Mapped[int | None] = mapped_column(Integer)


class AgentCheckpointModel(Base):
    __tablename__ = "agent_checkpoints"
    __table_args__ = (
        Index("ix_agent_checkpoints_latest", "thread_id", "checkpoint_namespace", "checkpoint_id"),
        Index("ix_agent_checkpoints_expires", "expires_at"),
        Index("ix_agent_checkpoints_run", "run_id"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    thread_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    checkpoint_namespace: Mapped[str] = mapped_column(String(256), primary_key=True, default="")
    checkpoint_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    parent_checkpoint_id: Mapped[str | None] = mapped_column(String(64))
    workflow_id: Mapped[str | None] = mapped_column(String(36))
    workflow_version: Mapped[int | None] = mapped_column(Integer)
    run_id: Mapped[str | None] = mapped_column(String(64))
    checkpoint_type: Mapped[str] = mapped_column(String(64), nullable=False)
    checkpoint_blob: Mapped[bytes] = mapped_column(MEDIUMBLOB, nullable=False)
    metadata_type: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_blob: Mapped[bytes] = mapped_column(MEDIUMBLOB, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class AgentCheckpointWriteModel(Base):
    __tablename__ = "agent_checkpoint_writes"
    __table_args__ = (
        Index(
            "ix_checkpoint_writes_checkpoint",
            "thread_id",
            "checkpoint_namespace",
            "checkpoint_id",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    thread_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    checkpoint_namespace: Mapped[str] = mapped_column(String(256), primary_key=True, default="")
    checkpoint_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    write_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_path: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    channel: Mapped[str] = mapped_column(String(256), nullable=False)
    value_type: Mapped[str] = mapped_column(String(64), nullable=False)
    value_blob: Mapped[bytes] = mapped_column(MEDIUMBLOB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class AuditEventModel(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_resource", "resource_type", "resource_id", "created_at"),
        Index("ix_audit_expires", "expires_at"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


from . import brand_profile_models as _brand_profile_models  # noqa: E402, F401, I001
from . import indexing_models as _indexing_models  # noqa: E402, F401, I001
from . import product_brief_models as _product_brief_models  # noqa: E402, F401, I001
from . import prompt_registry_models as _prompt_registry_models  # noqa: E402, F401, I001
from . import retention_models as _retention_models  # noqa: E402, F401, I001
from . import retrieval_models as _retrieval_models  # noqa: E402, F401, I001


# Reserved for Phase 4 cost accounting without a destructive type migration.
MONEY_AMOUNT_TYPE = Numeric(20, 6)
MONEY_AMOUNT_PYTHON_TYPE = Decimal
SEQUENCE_TYPE = BigInteger
