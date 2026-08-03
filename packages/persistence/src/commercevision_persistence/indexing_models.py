"""SQLAlchemy models for the versioned collection registry and index facts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, UTCDateTime
from .workspace_identity import exact_string_sql_type, workspace_id_sql_type


class CollectionRegistryModel(Base):
    __tablename__ = "collection_registry"
    __table_args__ = (
        UniqueConstraint("logical_key", name="uq_collection_registry_logical_key"),
        UniqueConstraint("spec_hash", name="uq_collection_registry_spec_hash"),
        UniqueConstraint("physical_name", name="uq_collection_registry_physical_name"),
        CheckConstraint("dimension > 0", name="ck_collection_registry_dimension"),
        CheckConstraint("schema_version > 0", name="ck_collection_registry_schema_version"),
        CheckConstraint(
            "dynamic_fields_enabled = 0",
            name="ck_collection_registry_dynamic_fields_disabled",
        ),
        CheckConstraint(
            "state IN ('PLANNED', 'CREATING', 'BACKFILLING', 'VERIFYING', "
            "'READY', 'ACTIVE', 'RETIRING', 'RETIRED', 'FAILED')",
            name="ck_collection_registry_state",
        ),
        Index(
            "ix_collection_registry_routing",
            "vector_kind",
            "state",
            "is_read_enabled",
            "is_write_enabled",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    logical_key: Mapped[str] = mapped_column(exact_string_sql_type(512), nullable=False)
    spec_hash: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    physical_name: Mapped[str] = mapped_column(exact_string_sql_type(255), nullable=False)
    model_family: Mapped[str] = mapped_column(String(128), nullable=False)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    pinned_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    vector_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    index_spec_version: Mapped[str] = mapped_column(String(128), nullable=False)
    dynamic_fields_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    is_read_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_write_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    validation_summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class EmbeddingRecordModel(Base):
    __tablename__ = "embedding_records"
    __table_args__ = (
        UniqueConstraint(
            "asset_version_id",
            "embedding_spec_hash",
            name="uq_embedding_records_asset_spec",
        ),
        UniqueConstraint(
            "collection_id",
            "milvus_primary_key",
            name="uq_embedding_records_collection_pk",
        ),
        UniqueConstraint("operation_id", name="uq_embedding_records_operation"),
        ForeignKeyConstraint(
            ["workspace_id", "asset_version_id", "asset_id"],
            ["asset_versions.workspace_id", "asset_versions.id", "asset_versions.asset_id"],
            name="fk_embedding_records_asset_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "rights_record_id", "asset_id", "rights_record_version"],
            [
                "rights_records.workspace_id",
                "rights_records.id",
                "rights_records.asset_id",
                "rights_records.version_number",
            ],
            name="fk_embedding_records_rights_record",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "operation_id"],
            ["durable_operations.workspace_id", "durable_operations.id"],
            name="fk_embedding_records_operation",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "state IN ('PENDING', 'PROCESSING', 'INDEXED', 'RETRYABLE_FAILED', "
            "'PERMANENT_FAILED', 'STALE', 'DELETE_PENDING', 'DELETED')",
            name="ck_embedding_records_state",
        ),
        CheckConstraint(
            "asset_version_number > 0 AND rights_record_version > 0 "
            "AND dimension > 0 AND write_generation >= 0 AND version > 0",
            name="ck_embedding_records_positive_versions",
        ),
        CheckConstraint(
            "input_hash REGEXP '^[0-9a-f]{64}$' AND embedding_spec_hash REGEXP '^[0-9a-f]{64}$'",
            name="ck_embedding_records_hashes",
        ),
        Index(
            "ix_embedding_records_workspace_state",
            "workspace_id",
            "state",
            "updated_at",
            "id",
        ),
        Index(
            "ix_embedding_records_asset_status",
            "workspace_id",
            "asset_id",
            "asset_version_id",
            "state",
        ),
        Index(
            "ix_embedding_records_collection_status",
            "collection_id",
            "state",
            "updated_at",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(36), nullable=False)
    asset_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    asset_version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    rights_record_id: Mapped[str] = mapped_column(String(36), nullable=False)
    rights_record_version: Mapped[int] = mapped_column(Integer, nullable=False)
    collection_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("collection_registry.id", ondelete="RESTRICT"),
        nullable=False,
    )
    operation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    vector_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model_family: Mapped[str] = mapped_column(String(128), nullable=False)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    pinned_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    model_configuration_version: Mapped[str] = mapped_column(String(128), nullable=False)
    preprocessing_version: Mapped[str] = mapped_column(String(128), nullable=False)
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    input_hash: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    embedding_spec_hash: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    milvus_primary_key: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    write_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_request_id: Mapped[str | None] = mapped_column(String(256))
    actual_model: Mapped[str | None] = mapped_column(String(256))
    indexed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    stale_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    stale_reason: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
