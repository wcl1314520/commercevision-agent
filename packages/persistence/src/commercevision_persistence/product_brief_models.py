"""SQLAlchemy models for ProductBrief analysis and immutable review history."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, UTCDateTime
from .workspace_identity import exact_string_sql_type, workspace_id_sql_type


class ProductBriefModel(Base):
    __tablename__ = "product_briefs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_product_briefs_workspace_id"),
        UniqueConstraint(
            "workspace_id",
            "workflow_id",
            "product_id",
            name="uq_product_briefs_workflow_product",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            name="fk_product_briefs_workspace_workflow",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_product_briefs_workspace_product",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "operation_id"],
            ["durable_operations.workspace_id", "durable_operations.id"],
            name="fk_product_briefs_workspace_operation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "current_version_id", "id"],
            [
                "product_brief_versions.workspace_id",
                "product_brief_versions.id",
                "product_brief_versions.product_brief_id",
            ],
            name="fk_product_briefs_current_version",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["workspace_id", "confirmed_version_id", "id"],
            [
                "product_brief_versions.workspace_id",
                "product_brief_versions.id",
                "product_brief_versions.product_brief_id",
            ],
            name="fk_product_briefs_confirmed_version",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        CheckConstraint(
            "(retention_class = 'TASK' AND retention_deadline IS NOT NULL) "
            "OR (retention_class = 'FOUNDATION' AND retention_deadline IS NULL)",
            name="ck_product_briefs_retention",
        ),
        CheckConstraint("version > 0", name="ck_product_briefs_version"),
        CheckConstraint(
            "state IN ('DRAFT', 'AWAITING_CONFIRMATION', 'CONFIRMED', 'ARCHIVED')",
            name="ck_product_briefs_state",
        ),
        Index(
            "ix_product_briefs_workspace_updated",
            "workspace_id",
            "updated_at",
            "id",
        ),
        Index(
            "ix_product_briefs_retention",
            "retention_class",
            "retention_deadline",
            "state",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    workflow_id: Mapped[str] = mapped_column(String(36), nullable=False)
    product_id: Mapped[str] = mapped_column(String(36), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    current_version_id: Mapped[str | None] = mapped_column(String(36))
    confirmed_version_id: Mapped[str | None] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    retention_class: Mapped[str] = mapped_column(String(16), nullable=False)
    retention_deadline: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ProductBriefAnalysisRequestModel(Base):
    __tablename__ = "product_brief_analysis_requests"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_product_brief_analysis_requests_workspace_id",
        ),
        UniqueConstraint(
            "workspace_id",
            "operation_id",
            name="uq_product_brief_analysis_requests_operation",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_brief_id"],
            ["product_briefs.workspace_id", "product_briefs.id"],
            name="fk_product_brief_analysis_requests_brief",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "operation_id"],
            ["durable_operations.workspace_id", "durable_operations.id"],
            name="fk_product_brief_analysis_requests_operation",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "category IN ('BEAUTY', 'AUTOMOTIVE')",
            name="ck_product_brief_analysis_requests_category",
        ),
        CheckConstraint(
            "transfer_policy_snapshot_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_product_brief_analysis_requests_transfer_snapshot",
        ),
        CheckConstraint(
            "provider_configuration_snapshot_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_product_brief_analysis_requests_provider_snapshot",
        ),
        CheckConstraint(
            "review_policy_snapshot_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_product_brief_analysis_requests_review_snapshot",
        ),
        CheckConstraint(
            "review_confidence_threshold >= 0 AND review_confidence_threshold <= 1",
            name="ck_product_brief_analysis_requests_confidence",
        ),
        CheckConstraint(
            "(retention_class = 'TASK' AND retention_deadline IS NOT NULL) "
            "OR (retention_class = 'FOUNDATION' AND retention_deadline IS NULL)",
            name="ck_product_brief_analysis_requests_retention",
        ),
        Index(
            "ix_product_brief_analysis_requests_workspace_created",
            "workspace_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_product_brief_analysis_requests_brief_created",
            "workspace_id",
            "product_brief_id",
            "created_at",
            "id",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    product_brief_id: Mapped[str] = mapped_column(String(36), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    expected_workflow_version: Mapped[int] = mapped_column(Integer, nullable=False)
    product_catalog_version: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    endpoint_region: Mapped[str] = mapped_column(String(64), nullable=False)
    endpoint_host: Mapped[str] = mapped_column(exact_string_sql_type(255), nullable=False)
    requested_model: Mapped[str] = mapped_column(String(128), nullable=False)
    submitted_model_snapshot: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_configuration_snapshot_sha256: Mapped[str] = mapped_column(
        exact_string_sql_type(64),
        nullable=False,
    )
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    review_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    review_confidence_threshold: Mapped[Decimal] = mapped_column(
        Numeric(5, 4),
        nullable=False,
    )
    review_mandatory_paths_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    review_sensitive_claim_paths_json: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
    )
    review_policy_snapshot_sha256: Mapped[str] = mapped_column(
        exact_string_sql_type(64),
        nullable=False,
    )
    transfer_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    transfer_policy_snapshot_sha256: Mapped[str] = mapped_column(
        exact_string_sql_type(64),
        nullable=False,
    )
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    retention_class: Mapped[str] = mapped_column(String(16), nullable=False)
    retention_deadline: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ProductBriefSourceAssetModel(Base):
    __tablename__ = "product_brief_source_assets"
    __table_args__ = (
        PrimaryKeyConstraint(
            "analysis_request_id",
            "asset_version_id",
            name="pk_product_brief_source_assets",
        ),
        UniqueConstraint(
            "analysis_request_id",
            "ordinal",
            name="uq_product_brief_source_assets_ordinal",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "analysis_request_id"],
            [
                "product_brief_analysis_requests.workspace_id",
                "product_brief_analysis_requests.id",
            ],
            name="fk_product_brief_source_assets_request",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "asset_version_id", "asset_id"],
            [
                "asset_versions.workspace_id",
                "asset_versions.id",
                "asset_versions.asset_id",
            ],
            name="fk_product_brief_source_assets_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "asset_object_id", "asset_version_id"],
            [
                "asset_objects.workspace_id",
                "asset_objects.id",
                "asset_objects.asset_version_id",
            ],
            name="fk_product_brief_source_assets_object",
            ondelete="RESTRICT",
        ),
        CheckConstraint("ordinal >= 0 AND ordinal < 8", name="ck_product_brief_source_ordinal"),
        Index(
            "ix_product_brief_source_assets_version",
            "workspace_id",
            "asset_version_id",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    analysis_request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(36), nullable=False)
    asset_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    asset_object_id: Mapped[str] = mapped_column(String(36), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ProductBriefProviderAttemptModel(Base):
    __tablename__ = "product_brief_provider_attempts"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_product_brief_provider_attempts_workspace_id",
        ),
        UniqueConstraint(
            "operation_id",
            "operation_attempt",
            "call_index",
            name="uq_product_brief_provider_attempts_operation",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_brief_id"],
            ["product_briefs.workspace_id", "product_briefs.id"],
            name="fk_product_brief_provider_attempts_brief",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "operation_id"],
            ["durable_operations.workspace_id", "durable_operations.id"],
            name="fk_product_brief_provider_attempts_operation",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "operation_attempt > 0 AND call_index >= 0",
            name="ck_product_brief_provider_attempts_number",
        ),
        CheckConstraint(
            "submission_key_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND input_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND config_snapshot_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_product_brief_provider_attempts_hashes",
        ),
        CheckConstraint(
            "(retention_class = 'TASK' AND retention_deadline IS NOT NULL) "
            "OR (retention_class = 'FOUNDATION' AND retention_deadline IS NULL)",
            name="ck_product_brief_provider_attempts_retention",
        ),
        Index(
            "ix_product_brief_provider_attempts_brief_created",
            "workspace_id",
            "product_brief_id",
            "created_at",
            "id",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    product_brief_id: Mapped[str] = mapped_column(String(36), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    operation_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    call_index: Mapped[int] = mapped_column(Integer, nullable=False)
    submission_key_sha256: Mapped[str] = mapped_column(
        exact_string_sql_type(64),
        nullable=False,
    )
    input_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    endpoint_region: Mapped[str] = mapped_column(String(64), nullable=False)
    endpoint_host: Mapped[str] = mapped_column(exact_string_sql_type(255), nullable=False)
    requested_model: Mapped[str] = mapped_column(String(128), nullable=False)
    submitted_model_snapshot: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    config_snapshot_sha256: Mapped[str] = mapped_column(
        exact_string_sql_type(64),
        nullable=False,
    )
    retention_class: Mapped[str] = mapped_column(String(16), nullable=False)
    retention_deadline: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ProductBriefProviderArtifactModel(Base):
    __tablename__ = "product_brief_provider_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_pb_provider_artifacts_workspace_id",
        ),
        UniqueConstraint(
            "workspace_id",
            "id",
            "product_brief_id",
            name="uq_pb_provider_artifacts_workspace_brief",
        ),
        UniqueConstraint(
            "operation_id",
            "operation_attempt",
            "call_index",
            "kind",
            name="uq_pb_provider_artifacts_logical",
        ),
        UniqueConstraint(
            "storage_backend",
            "location",
            "target_sha256",
            name="uq_pb_provider_artifacts_physical",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_brief_id"],
            ["product_briefs.workspace_id", "product_briefs.id"],
            name="fk_pb_provider_artifacts_brief",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "operation_id"],
            ["durable_operations.workspace_id", "durable_operations.id"],
            name="fk_pb_provider_artifacts_operation",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "operation_attempt > 0 AND call_index >= 0",
            name="ck_pb_provider_artifacts_owner_numbers",
        ),
        CheckConstraint(
            "kind IN ('REQUEST', 'RESPONSE')",
            name="ck_pb_provider_artifacts_kind",
        ),
        CheckConstraint(
            "state IN ('INTENDED', 'STORED', 'UNKNOWN')",
            name="ck_pb_provider_artifacts_state",
        ),
        CheckConstraint(
            "storage_backend IN ('MINIO', 'OSS') AND location = 'PROVIDER_RESULT'",
            name="ck_pb_provider_artifacts_storage",
        ),
        CheckConstraint(
            "target_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND expected_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND write_fence REGEXP '^[0-9a-f]{64}$'",
            name="ck_pb_provider_artifacts_hashes",
        ),
        CheckConstraint(
            "target_sha256 = SHA2(CONCAT("
            "storage_backend, CHAR(0), location, CHAR(0), "
            "bucket, CHAR(0), object_key), 256)",
            name="ck_pb_provider_artifacts_target_identity",
        ),
        CheckConstraint(
            "expected_byte_size BETWEEN 0 AND 2097152",
            name="ck_pb_provider_artifacts_size",
        ),
        CheckConstraint(
            "(retention_class = 'TASK' AND retention_deadline IS NOT NULL) "
            "OR (retention_class = 'FOUNDATION' AND retention_deadline IS NULL)",
            name="ck_pb_provider_artifacts_retention",
        ),
        CheckConstraint(
            "(state = 'STORED' AND provider_version_id IS NOT NULL "
            "AND etag IS NOT NULL AND stored_at IS NOT NULL "
            "AND unknown_reason IS NULL) "
            "OR (state = 'INTENDED' AND provider_version_id IS NULL "
            "AND etag IS NULL AND stored_at IS NULL AND unknown_reason IS NULL) "
            "OR (state = 'UNKNOWN' AND provider_version_id IS NULL "
            "AND etag IS NULL AND stored_at IS NULL AND unknown_reason IS NOT NULL)",
            name="ck_pb_provider_artifacts_lifecycle",
        ),
        CheckConstraint(
            "version > 0",
            name="ck_pb_provider_artifacts_version",
        ),
        Index(
            "ix_pb_provider_artifacts_reconciliation",
            "state",
            "updated_at",
            "id",
        ),
        Index(
            "ix_pb_provider_artifacts_brief_created",
            "workspace_id",
            "product_brief_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_pb_provider_artifacts_retention",
            "retention_class",
            "retention_deadline",
            "state",
            "id",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    product_brief_id: Mapped[str] = mapped_column(String(36), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    operation_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    call_index: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    key_schema_version: Mapped[str] = mapped_column(
        exact_string_sql_type(32),
        nullable=False,
    )
    storage_backend: Mapped[str] = mapped_column(String(16), nullable=False)
    location: Mapped[str] = mapped_column(String(32), nullable=False)
    bucket: Mapped[str] = mapped_column(exact_string_sql_type(255), nullable=False)
    object_key: Mapped[str] = mapped_column(exact_string_sql_type(1024), nullable=False)
    target_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    content_type: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    expected_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    expected_byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    retention_class: Mapped[str] = mapped_column(String(16), nullable=False)
    retention_deadline: Mapped[datetime | None] = mapped_column(UTCDateTime())
    write_fence: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    provider_version_id: Mapped[str | None] = mapped_column(exact_string_sql_type(256))
    etag: Mapped[str | None] = mapped_column(exact_string_sql_type(512))
    unknown_reason: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    stored_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ProductBriefProviderCallModel(Base):
    __tablename__ = "product_brief_provider_calls"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_product_brief_provider_calls_workspace_id",
        ),
        UniqueConstraint(
            "workspace_id",
            "id",
            "product_brief_id",
            name="uq_product_brief_provider_calls_workspace_brief",
        ),
        UniqueConstraint(
            "operation_id",
            "operation_attempt",
            "call_index",
            name="uq_product_brief_provider_calls_attempt",
        ),
        UniqueConstraint(
            "request_artifact_id",
            name="uq_pb_provider_calls_request_artifact",
        ),
        UniqueConstraint(
            "response_artifact_id",
            name="uq_pb_provider_calls_response_artifact",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_brief_id"],
            ["product_briefs.workspace_id", "product_briefs.id"],
            name="fk_product_brief_provider_calls_brief",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "operation_id"],
            ["durable_operations.workspace_id", "durable_operations.id"],
            name="fk_product_brief_provider_calls_operation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "request_artifact_id", "product_brief_id"],
            [
                "product_brief_provider_artifacts.workspace_id",
                "product_brief_provider_artifacts.id",
                "product_brief_provider_artifacts.product_brief_id",
            ],
            name="fk_pb_provider_calls_request_artifact",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "response_artifact_id", "product_brief_id"],
            [
                "product_brief_provider_artifacts.workspace_id",
                "product_brief_provider_artifacts.id",
                "product_brief_provider_artifacts.product_brief_id",
            ],
            name="fk_pb_provider_calls_response_artifact",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('SUCCEEDED', 'MALFORMED', 'THROTTLED', 'TIMEOUT', "
            "'UNAVAILABLE', 'REJECTED', 'UNKNOWN')",
            name="ck_product_brief_provider_calls_status",
        ),
        CheckConstraint(
            "config_snapshot_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_product_brief_provider_calls_config_snapshot",
        ),
        CheckConstraint(
            "(retention_class = 'TASK' AND retention_deadline IS NOT NULL) "
            "OR (retention_class = 'FOUNDATION' AND retention_deadline IS NULL)",
            name="ck_product_brief_provider_calls_retention",
        ),
        CheckConstraint(
            "request_artifact_storage_backend IN ('MINIO', 'OSS') "
            "AND request_artifact_location = 'PROVIDER_RESULT' "
            "AND request_artifact_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND request_artifact_byte_size BETWEEN 0 AND 2097152",
            name="ck_pb_provider_calls_request_artifact",
        ),
        CheckConstraint(
            "(response_artifact_storage_backend IS NULL "
            "AND response_artifact_location IS NULL "
            "AND response_artifact_bucket IS NULL "
            "AND response_artifact_key IS NULL "
            "AND response_artifact_provider_version_id IS NULL "
            "AND response_artifact_etag IS NULL "
            "AND response_artifact_sha256 IS NULL "
            "AND response_artifact_byte_size IS NULL) "
            "OR (response_artifact_storage_backend IS NOT NULL "
            "AND response_artifact_location IS NOT NULL "
            "AND response_artifact_bucket IS NOT NULL "
            "AND response_artifact_key IS NOT NULL "
            "AND response_artifact_provider_version_id IS NOT NULL "
            "AND response_artifact_etag IS NOT NULL "
            "AND response_artifact_sha256 IS NOT NULL "
            "AND response_artifact_byte_size IS NOT NULL)",
            name="ck_pb_provider_calls_response_presence",
        ),
        CheckConstraint(
            "response_artifact_storage_backend IS NULL "
            "OR (response_artifact_storage_backend IN ('MINIO', 'OSS') "
            "AND response_artifact_location = 'PROVIDER_RESULT' "
            "AND response_artifact_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND response_artifact_byte_size BETWEEN 0 AND 2097152)",
            name="ck_pb_provider_calls_response_artifact",
        ),
        CheckConstraint(
            "(response_artifact_id IS NULL "
            "AND response_artifact_storage_backend IS NULL) "
            "OR (response_artifact_id IS NOT NULL "
            "AND response_artifact_storage_backend IS NOT NULL)",
            name="ck_pb_provider_calls_response_ledger",
        ),
        Index(
            "ix_product_brief_provider_calls_brief_created",
            "workspace_id",
            "product_brief_id",
            "created_at",
            "id",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    product_brief_id: Mapped[str] = mapped_column(String(36), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    operation_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    call_index: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    endpoint_region: Mapped[str] = mapped_column(String(64), nullable=False)
    endpoint_host: Mapped[str] = mapped_column(exact_string_sql_type(255), nullable=False)
    requested_model: Mapped[str] = mapped_column(String(128), nullable=False)
    submitted_model_snapshot: Mapped[str] = mapped_column(String(128), nullable=False)
    resolved_model: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    config_snapshot_sha256: Mapped[str] = mapped_column(
        exact_string_sql_type(64),
        nullable=False,
    )
    request_id: Mapped[str | None] = mapped_column(String(256))
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    request_artifact_id: Mapped[str] = mapped_column(String(36), nullable=False)
    request_artifact_storage_backend: Mapped[str] = mapped_column(String(16), nullable=False)
    request_artifact_location: Mapped[str] = mapped_column(String(32), nullable=False)
    request_artifact_bucket: Mapped[str] = mapped_column(
        exact_string_sql_type(255),
        nullable=False,
    )
    request_artifact_key: Mapped[str] = mapped_column(
        exact_string_sql_type(1024),
        nullable=False,
    )
    request_artifact_provider_version_id: Mapped[str] = mapped_column(
        exact_string_sql_type(256),
        nullable=False,
    )
    request_artifact_etag: Mapped[str] = mapped_column(
        exact_string_sql_type(512),
        nullable=False,
    )
    request_artifact_sha256: Mapped[str] = mapped_column(
        exact_string_sql_type(64),
        nullable=False,
    )
    request_artifact_byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    response_artifact_id: Mapped[str | None] = mapped_column(String(36))
    response_artifact_storage_backend: Mapped[str | None] = mapped_column(String(16))
    response_artifact_location: Mapped[str | None] = mapped_column(String(32))
    response_artifact_bucket: Mapped[str | None] = mapped_column(exact_string_sql_type(255))
    response_artifact_key: Mapped[str | None] = mapped_column(exact_string_sql_type(1024))
    response_artifact_provider_version_id: Mapped[str | None] = mapped_column(
        exact_string_sql_type(256)
    )
    response_artifact_etag: Mapped[str | None] = mapped_column(exact_string_sql_type(512))
    response_artifact_sha256: Mapped[str | None] = mapped_column(exact_string_sql_type(64))
    response_artifact_byte_size: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_category: Mapped[str | None] = mapped_column(String(64))
    error_retryable: Mapped[bool | None] = mapped_column(Boolean)
    retention_class: Mapped[str] = mapped_column(String(16), nullable=False)
    retention_deadline: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ProductBriefVersionModel(Base):
    __tablename__ = "product_brief_versions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_product_brief_versions_workspace_id",
        ),
        UniqueConstraint(
            "workspace_id",
            "id",
            "product_brief_id",
            name="uq_product_brief_versions_workspace_brief",
        ),
        UniqueConstraint(
            "workspace_id",
            "id",
            "product_brief_id",
            "version_number",
            name="uq_product_brief_versions_exact",
        ),
        UniqueConstraint(
            "product_brief_id",
            "version_number",
            name="uq_product_brief_versions_number",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_brief_id"],
            ["product_briefs.workspace_id", "product_briefs.id"],
            name="fk_product_brief_versions_brief",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "supersedes_version_id", "product_brief_id"],
            [
                "product_brief_versions.workspace_id",
                "product_brief_versions.id",
                "product_brief_versions.product_brief_id",
            ],
            name="fk_product_brief_versions_supersedes",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "provider_call_id", "product_brief_id"],
            [
                "product_brief_provider_calls.workspace_id",
                "product_brief_provider_calls.id",
                "product_brief_provider_calls.product_brief_id",
            ],
            name="fk_product_brief_versions_provider_call",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version_number > 0", name="ck_product_brief_versions_number"),
        CheckConstraint(
            "category IN ('BEAUTY', 'AUTOMOTIVE')",
            name="ck_product_brief_versions_category",
        ),
        CheckConstraint(
            "payload_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_product_brief_versions_payload_sha256",
        ),
        CheckConstraint(
            "JSON_LENGTH(changed_paths_json) > 0",
            name="ck_product_brief_versions_changed_paths",
        ),
        CheckConstraint(
            "(source = 'MODEL' AND prompt_version IS NOT NULL "
            "AND provider_call_id IS NOT NULL AND revision_reason IS NULL) OR "
            "(source = 'HUMAN' AND prompt_version IS NULL "
            "AND provider_call_id IS NULL AND revision_reason IS NOT NULL)",
            name="ck_product_brief_versions_provenance",
        ),
        CheckConstraint(
            "(retention_class = 'TASK' AND retention_deadline IS NOT NULL) "
            "OR (retention_class = 'FOUNDATION' AND retention_deadline IS NULL)",
            name="ck_product_brief_versions_retention",
        ),
        Index(
            "ix_product_brief_versions_history",
            "workspace_id",
            "product_brief_id",
            "version_number",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    product_brief_id: Mapped[str] = mapped_column(String(36), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_version_id: Mapped[str | None] = mapped_column(String(36))
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    common_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    category_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    changed_paths_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    confirmation_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    unresolved_field_count: Mapped[int] = mapped_column(Integer, nullable=False)
    review_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    provider_call_id: Mapped[str | None] = mapped_column(String(36))
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    revision_reason: Mapped[str | None] = mapped_column(String(512))
    retention_class: Mapped[str] = mapped_column(String(16), nullable=False)
    retention_deadline: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ProductBriefFieldModel(Base):
    __tablename__ = "product_brief_fields"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "id",
            "product_brief_version_id",
            "product_brief_id",
            name="uq_product_brief_fields_version",
        ),
        UniqueConstraint(
            "product_brief_version_id",
            "path",
            name="uq_product_brief_fields_path",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_brief_version_id", "product_brief_id"],
            [
                "product_brief_versions.workspace_id",
                "product_brief_versions.id",
                "product_brief_versions.product_brief_id",
            ],
            name="fk_product_brief_fields_version",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_product_brief_fields_confidence",
        ),
        CheckConstraint(
            "source IN ('MODEL', 'HUMAN', 'PRODUCT_DATA')",
            name="ck_product_brief_fields_source",
        ),
        CheckConstraint(
            "conflict IN ('NONE', 'CONFLICTING', 'RESOLVED')",
            name="ck_product_brief_fields_conflict",
        ),
        Index(
            "ix_product_brief_fields_review",
            "workspace_id",
            "product_brief_id",
            "review_required",
            "sensitive",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    product_brief_id: Mapped[str] = mapped_column(String(36), nullable=False)
    product_brief_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    path: Mapped[str] = mapped_column(String(160), nullable=False)
    value_json: Mapped[Any] = mapped_column(JSON, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    conflict: Mapped[str] = mapped_column(String(24), nullable=False)
    review_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    sensitive: Mapped[bool] = mapped_column(Boolean, nullable=False)
    review_reasons_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ProductBriefEvidenceModel(Base):
    __tablename__ = "product_brief_evidence"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_product_brief_evidence_workspace_id",
        ),
        ForeignKeyConstraint(
            [
                "workspace_id",
                "field_id",
                "product_brief_version_id",
                "product_brief_id",
            ],
            [
                "product_brief_fields.workspace_id",
                "product_brief_fields.id",
                "product_brief_fields.product_brief_version_id",
                "product_brief_fields.product_brief_id",
            ],
            name="fk_product_brief_evidence_field",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "source_asset_version_id"],
            ["asset_versions.workspace_id", "asset_versions.id"],
            name="fk_product_brief_evidence_source_version",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "kind IN ('IMAGE_REGION', 'VISIBLE_TEXT', 'PRODUCT_DATA', 'HUMAN_NOTE')",
            name="ck_product_brief_evidence_kind",
        ),
        CheckConstraint(
            "excerpt_sha256 IS NULL OR excerpt_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_product_brief_evidence_excerpt_sha256",
        ),
        Index(
            "ix_product_brief_evidence_source",
            "workspace_id",
            "source_asset_version_id",
            "created_at",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    product_brief_id: Mapped[str] = mapped_column(String(36), nullable=False)
    product_brief_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    field_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_asset_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    reference: Mapped[str] = mapped_column(String(512), nullable=False)
    region_json: Mapped[list[float] | None] = mapped_column(JSON)
    excerpt_sha256: Mapped[str | None] = mapped_column(exact_string_sql_type(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ProductBriefConfirmationModel(Base):
    __tablename__ = "product_brief_confirmations"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_product_brief_confirmations_workspace_id",
        ),
        UniqueConstraint(
            "product_brief_id",
            "product_brief_version_id",
            name="uq_product_brief_confirmations_exact_version",
        ),
        UniqueConstraint(
            "approval_id",
            name="uq_product_brief_confirmations_approval",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_brief_id"],
            ["product_briefs.workspace_id", "product_briefs.id"],
            name="fk_product_brief_confirmations_brief",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "workspace_id",
                "product_brief_version_id",
                "product_brief_id",
                "product_brief_version_number",
            ],
            [
                "product_brief_versions.workspace_id",
                "product_brief_versions.id",
                "product_brief_versions.product_brief_id",
                "product_brief_versions.version_number",
            ],
            name="fk_product_brief_confirmations_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            name="fk_product_brief_confirmations_workflow",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "operation_id"],
            ["durable_operations.workspace_id", "durable_operations.id"],
            name="fk_product_brief_confirmations_operation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "approval_id",
                "workflow_id",
                "product_brief_version_id",
                "product_brief_version_number",
                "approval_type",
                "approval_decision",
            ],
            [
                "workflow_approvals.id",
                "workflow_approvals.workflow_id",
                "workflow_approvals.subject_id",
                "workflow_approvals.subject_version",
                "workflow_approvals.approval_type",
                "workflow_approvals.decision",
            ],
            name="fk_product_brief_confirmations_approval_subject",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "approval_type = 'PRODUCT_BRIEF'",
            name="ck_product_brief_confirmations_approval_type",
        ),
        CheckConstraint(
            "approval_decision = 'APPROVE'",
            name="ck_product_brief_confirmations_approval_decision",
        ),
        Index(
            "ix_product_brief_confirmations_history",
            "workspace_id",
            "product_brief_id",
            "created_at",
            "id",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    product_brief_id: Mapped[str] = mapped_column(String(36), nullable=False)
    product_brief_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    product_brief_version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    workflow_id: Mapped[str] = mapped_column(String(36), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    approval_id: Mapped[str] = mapped_column(String(36), nullable=False)
    approval_type: Mapped[str] = mapped_column(String(32), nullable=False)
    approval_decision: Mapped[str] = mapped_column(String(24), nullable=False)
    confirmed_by: Mapped[str] = mapped_column(String(128), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(128))
    comment_ref: Mapped[str | None] = mapped_column(String(512))
    expected_product_brief_version: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_workflow_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
