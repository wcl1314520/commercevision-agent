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
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, UTCDateTime
from .workspace_identity import exact_string_sql_type, workspace_id_sql_type


class CollectionRegistryModel(Base):
    __tablename__ = "collection_registry"
    __table_args__ = (
        UniqueConstraint(
            "logical_key", "instance_generation", name="uq_collection_registry_logical_instance"
        ),
        UniqueConstraint(
            "spec_hash", "instance_generation", name="uq_collection_registry_spec_instance"
        ),
        UniqueConstraint("rebuild_id", name="uq_collection_registry_rebuild"),
        UniqueConstraint("physical_name", name="uq_collection_registry_physical_name"),
        CheckConstraint("dimension > 0", name="ck_collection_registry_dimension"),
        CheckConstraint("schema_version > 0", name="ck_collection_registry_schema_version"),
        CheckConstraint(
            "dynamic_fields_enabled = 0",
            name="ck_collection_registry_dynamic_fields_disabled",
        ),
        CheckConstraint(
            "(instance_generation = 0 AND rebuild_id IS NULL) OR "
            "(instance_generation > 0 AND rebuild_id IS NOT NULL)",
            name="ck_collection_registry_instance_identity",
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
    instance_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    rebuild_id: Mapped[str | None] = mapped_column(String(36))
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    is_read_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_write_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    validation_summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class RetrievalPolicyPointerModel(Base):
    __tablename__ = "retrieval_policy_pointers"
    __table_args__ = (
        UniqueConstraint("collection_id", name="uq_retrieval_policy_pointer_collection"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    vector_kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    collection_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("collection_registry.id", ondelete="RESTRICT"),
        nullable=False,
    )
    retrieval_policy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class CollectionRebuildModel(Base):
    __tablename__ = "collection_rebuilds"
    __table_args__ = (
        UniqueConstraint("operation_id", name="uq_collection_rebuild_operation"),
        UniqueConstraint("candidate_collection_id", name="uq_collection_rebuild_candidate"),
        CheckConstraint("generation > 0 AND version > 0", name="ck_collection_rebuild_versions"),
        CheckConstraint(
            "state IN ('REQUESTED', 'PROVISIONING', 'BACKFILLING', 'REPLAYING', "
            "'RIGHTS_RESCAN', 'AWAITING_VALIDATION', 'VALIDATING', 'READY', "
            "'ACTIVATING', 'ACTIVE', 'FAILED', 'RETIRING', 'RETIRED')",
            name="ck_collection_rebuild_state",
        ),
        Index("ix_collection_rebuild_state", "state", "updated_at", "id"),
        Index("ix_collection_rebuild_retirement", "state", "retire_after", "id"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_collection_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("collection_registry.id", ondelete="RESTRICT"), nullable=False
    )
    candidate_collection_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("collection_registry.id", ondelete="RESTRICT"), nullable=False
    )
    vector_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    source_collection_version: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_pointer_version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_watermark: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    backfill_cursor: Mapped[str | None] = mapped_column(String(36))
    replay_watermark: Mapped[datetime | None] = mapped_column(UTCDateTime())
    replay_cursor_occurred_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    replay_cursor_event_id: Mapped[str | None] = mapped_column(String(36))
    rights_cursor: Mapped[str | None] = mapped_column(String(36))
    processed_count: Mapped[int] = mapped_column(Integer, nullable=False)
    validation_summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    validation_watermark: Mapped[datetime | None] = mapped_column(UTCDateTime())
    failure_code: Mapped[str | None] = mapped_column(String(64))
    retire_after: Mapped[datetime | None] = mapped_column(UTCDateTime())
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class CollectionRebuildPlacementModel(Base):
    __tablename__ = "collection_rebuild_placements"
    __table_args__ = (
        UniqueConstraint(
            "rebuild_id", "milvus_primary_key", name="uq_collection_rebuild_placement_pk"
        ),
        Index("ix_collection_rebuild_placement_asset", "rebuild_id", "asset_id"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    rebuild_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("collection_rebuilds.id", ondelete="RESTRICT"), primary_key=True
    )
    embedding_record_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("embedding_records.id", ondelete="RESTRICT"), primary_key=True
    )
    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(36), nullable=False)
    asset_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    milvus_primary_key: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    input_hash: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    embedding_spec_hash: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    write_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    placed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class CollectionRebuildProgressModel(Base):
    __tablename__ = "collection_rebuild_progress"
    __table_args__ = (
        UniqueConstraint("rebuild_id", "sequence", name="uq_collection_rebuild_progress_seq"),
        Index("ix_collection_rebuild_progress_latest", "rebuild_id", "sequence"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    rebuild_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("collection_rebuilds.id", ondelete="RESTRICT"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    processed_count: Mapped[int] = mapped_column(Integer, nullable=False)
    message_code: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class EmbeddingRecordModel(Base):
    __tablename__ = "embedding_records"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_embedding_records_workspace_id",
        ),
        UniqueConstraint(
            "asset_version_id",
            "embedding_spec_hash",
            "input_hash",
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
            ["workspace_id", "product_brief_version_id"],
            ["product_brief_versions.workspace_id", "product_brief_versions.id"],
            name="fk_embedding_records_product_brief_version",
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
        CheckConstraint(
            "(vector_kind = 'IMAGE' AND product_brief_version_id IS NULL "
            "AND controlled_text_sha256 IS NULL) OR "
            "(vector_kind = 'PRODUCT_FUSED' AND product_brief_version_id IS NOT NULL "
            "AND controlled_text_sha256 REGEXP '^[0-9a-f]{64}$')",
            name="ck_embedding_records_controlled_text",
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
    product_brief_version_id: Mapped[str | None] = mapped_column(String(36))
    controlled_text_sha256: Mapped[str | None] = mapped_column(exact_string_sql_type(64))
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


class ProductSearchDocumentModel(Base):
    __tablename__ = "product_search_documents"
    __table_args__ = (
        UniqueConstraint(
            "asset_version_id",
            "input_hash",
            name="uq_product_search_documents_asset_input",
        ),
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_product_search_documents_workspace_id",
        ),
        UniqueConstraint(
            "embedding_record_id",
            name="uq_product_search_documents_embedding_record",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_product_search_documents_product",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_brief_id"],
            ["product_briefs.workspace_id", "product_briefs.id"],
            name="fk_product_search_documents_brief",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_brief_version_id", "product_brief_id"],
            [
                "product_brief_versions.workspace_id",
                "product_brief_versions.id",
                "product_brief_versions.product_brief_id",
            ],
            name="fk_product_search_documents_brief_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "asset_version_id", "asset_id"],
            ["asset_versions.workspace_id", "asset_versions.id", "asset_versions.asset_id"],
            name="fk_product_search_documents_asset_version",
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
            name="fk_product_search_documents_rights",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "embedding_record_id"],
            ["embedding_records.workspace_id", "embedding_records.id"],
            name="fk_product_search_documents_embedding",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "state IN ('PENDING', 'INDEXED', 'STALE', 'DELETE_PENDING', 'DELETED')",
            name="ck_product_search_documents_state",
        ),
        CheckConstraint(
            "rights_record_version > 0 AND version > 0",
            name="ck_product_search_documents_positive_versions",
        ),
        CheckConstraint(
            "input_hash REGEXP '^[0-9a-f]{64}$' AND controlled_text_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_product_search_documents_hashes",
        ),
        CheckConstraint(
            "(retention_class = 'TASK' AND retention_deadline IS NOT NULL) OR "
            "(retention_class = 'FOUNDATION' AND retention_deadline IS NULL)",
            name="ck_product_search_documents_retention",
        ),
        Index(
            "ix_product_search_documents_authority",
            "workspace_id",
            "state",
            "rights_record_id",
            "rights_record_version",
            "asset_version_id",
        ),
        Index(
            "ft_product_search_cjk",
            "title",
            "labels",
            "ocr_summary",
            "product_brief_summary",
            "approved_notes",
            mysql_prefix="FULLTEXT",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    product_id: Mapped[str] = mapped_column(String(36), nullable=False)
    product_brief_id: Mapped[str] = mapped_column(String(36), nullable=False)
    product_brief_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(36), nullable=False)
    asset_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    rights_record_id: Mapped[str] = mapped_column(String(36), nullable=False)
    rights_record_version: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding_record_id: Mapped[str] = mapped_column(String(36), nullable=False)
    input_hash: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    controlled_text_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    preprocessing_version: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    labels: Mapped[str] = mapped_column(Text, nullable=False)
    ocr_summary: Mapped[str] = mapped_column(Text, nullable=False)
    product_brief_summary: Mapped[str] = mapped_column(Text, nullable=False)
    approved_notes: Mapped[str] = mapped_column(Text, nullable=False)
    retention_class: Mapped[str] = mapped_column(String(16), nullable=False)
    retention_deadline: Mapped[datetime | None] = mapped_column(UTCDateTime())
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
