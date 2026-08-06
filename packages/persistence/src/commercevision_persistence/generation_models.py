"""SQLAlchemy models for immutable generation batches and candidate slots."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

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


class GenerationBatchModel(Base):
    __tablename__ = "generation_batches"
    __table_args__ = (
        PrimaryKeyConstraint("workspace_id", "id", name="pk_generation_batch"),
        UniqueConstraint(
            "workspace_id",
            "batch_sha256",
            name="uq_generation_batch_hash",
        ),
        UniqueConstraint(
            "workspace_id",
            "workflow_id",
            "workflow_version",
            "creative_plan_version_id",
            "direction_key",
            "tool_intent_key",
            name="uq_generation_batch_logical",
        ),
        UniqueConstraint(
            "workspace_id",
            "id",
            "workflow_id",
            "creative_plan_version_id",
            name="uq_generation_batch_candidate_identity",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            name="fk_generation_batch_workflow",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "creative_plan_version_id"],
            ["creative_plan_versions.workspace_id", "creative_plan_versions.id"],
            name="fk_generation_batch_plan_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["plan_approval_id"],
            ["workflow_approvals.id"],
            name="fk_generation_batch_approval",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "route_decision_sha256"],
            ["model_route_decisions.workspace_id", "model_route_decisions.decision_sha256"],
            name="fk_generation_batch_route_decision",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "candidate_count BETWEEN 1 AND 16 AND workflow_version > 0",
            name="ck_generation_batch_counts",
        ),
        CheckConstraint(
            "operation_kind IN ('IMAGE_GENERATION', 'IMAGE_EDITING')",
            name="ck_generation_batch_kind",
        ),
        CheckConstraint(
            "retention_deadline > created_at "
            "AND retention_deadline <= workflow_deadline "
            "AND (source_rights_deadline IS NULL "
            "OR retention_deadline <= source_rights_deadline)",
            name="ck_generation_batch_retention",
        ),
        CheckConstraint(
            "batch_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND tool_intent_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND prompt_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND context_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND route_decision_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND route_request_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_generation_batch_hashes",
        ),
        Index(
            "ix_generation_batch_workflow",
            "workspace_id",
            "workflow_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_generation_batch_retention",
            "workspace_id",
            "retention_deadline",
            "id",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    id: Mapped[str] = mapped_column(exact_string_sql_type(36), nullable=False)
    batch_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    workflow_id: Mapped[str] = mapped_column(String(36), nullable=False)
    workflow_version: Mapped[int] = mapped_column(Integer, nullable=False)
    creative_plan_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    plan_approval_id: Mapped[str] = mapped_column(String(36), nullable=False)
    direction_key: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    tool_intent_key: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    tool_intent_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    prompt_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    context_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    route_decision_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    route_request_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    operation_kind: Mapped[str] = mapped_column(exact_string_sql_type(40), nullable=False)
    authorized_asset_version_ids_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    route_policy_version: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    tool_policy_version: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    rights_policy_version: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    safety_policy_version: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    workflow_deadline: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    source_rights_deadline: Mapped[datetime | None] = mapped_column(UTCDateTime())
    edit_source_asset_version_id: Mapped[str | None] = mapped_column(String(36))
    edit_mask_asset_version_id: Mapped[str | None] = mapped_column(String(36))
    approved_repair_scope_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    retention_deadline: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_by: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class CandidateSlotModel(Base):
    __tablename__ = "candidate_slots"
    __table_args__ = (
        PrimaryKeyConstraint("workspace_id", "id", name="pk_candidate_slot"),
        UniqueConstraint(
            "workspace_id",
            "generation_batch_id",
            "candidate_index",
            name="uq_candidate_slot_index",
        ),
        UniqueConstraint(
            "workspace_id",
            "durable_operation_id",
            name="uq_candidate_slot_operation",
        ),
        UniqueConstraint(
            "workspace_id",
            "logical_identity_sha256",
            name="uq_candidate_slot_logical_identity",
        ),
        UniqueConstraint(
            "workspace_id",
            "id",
            "generation_batch_id",
            name="uq_candidate_slot_batch_identity",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "generation_batch_id"],
            ["generation_batches.workspace_id", "generation_batches.id"],
            name="fk_candidate_slot_batch",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "durable_operation_id"],
            ["durable_operations.workspace_id", "durable_operations.id"],
            name="fk_candidate_slot_operation",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "candidate_index BETWEEN 0 AND 15",
            name="ck_candidate_slot_index",
        ),
        CheckConstraint(
            "operation_kind IN ('IMAGE_GENERATION', 'IMAGE_EDITING')",
            name="ck_candidate_slot_kind",
        ),
        CheckConstraint(
            "logical_identity_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_candidate_slot_hash",
        ),
        Index(
            "ix_candidate_slot_batch",
            "workspace_id",
            "generation_batch_id",
            "candidate_index",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    id: Mapped[str] = mapped_column(exact_string_sql_type(36), nullable=False)
    generation_batch_id: Mapped[str] = mapped_column(exact_string_sql_type(36), nullable=False)
    candidate_index: Mapped[int] = mapped_column(Integer, nullable=False)
    durable_operation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    operation_kind: Mapped[str] = mapped_column(exact_string_sql_type(40), nullable=False)
    logical_identity_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    operation_idempotency_key: Mapped[str] = mapped_column(
        exact_string_sql_type(128), nullable=False
    )


class GenerationDispatchAttemptModel(Base):
    __tablename__ = "generation_dispatch_attempts"
    __table_args__ = (
        PrimaryKeyConstraint("workspace_id", "id", name="pk_generation_dispatch_attempt"),
        UniqueConstraint(
            "workspace_id",
            "durable_operation_id",
            "operation_attempt",
            "call_index",
            name="uq_generation_dispatch_attempt_operation",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "candidate_slot_id"],
            ["candidate_slots.workspace_id", "candidate_slots.id"],
            name="fk_generation_dispatch_attempt_slot",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "durable_operation_id"],
            ["durable_operations.workspace_id", "durable_operations.id"],
            name="fk_generation_dispatch_attempt_operation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["endpoint_capability_version_id"],
            ["provider_endpoint_capability_versions.id"],
            name="fk_generation_dispatch_attempt_endpoint",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "operation_attempt > 0 AND call_index BETWEEN 0 AND 7",
            name="ck_generation_dispatch_attempt_index",
        ),
        CheckConstraint(
            "state IN ('DISPATCHING', 'OUTCOME_RECORDED')",
            name="ck_generation_dispatch_attempt_state",
        ),
        CheckConstraint(
            "outcome IS NULL OR outcome IN ('CONFIRMED_SUCCESS', 'CONFIRMED_FAILURE', "
            "'CONTENT_REJECTED', 'SAFE_TO_RETRY_PRE_DISPATCH', "
            "'UNKNOWN_AFTER_POSSIBLE_DISPATCH')",
            name="ck_generation_dispatch_attempt_outcome",
        ),
        CheckConstraint(
            "(state = 'DISPATCHING' AND outcome IS NULL "
            "AND provider_request_id IS NULL AND provider_request_id_sha256 IS NULL "
            "AND provider_task_id IS NULL AND provider_task_id_sha256 IS NULL) OR "
            "(state = 'OUTCOME_RECORDED' AND outcome IS NOT NULL "
            "AND (provider_request_id IS NULL) = (provider_request_id_sha256 IS NULL) "
            "AND (provider_task_id IS NULL) = (provider_task_id_sha256 IS NULL))",
            name="ck_generation_dispatch_attempt_facts",
        ),
        CheckConstraint(
            "request_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND adapter_configuration_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND idempotency_key_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND (provider_request_id_sha256 IS NULL OR "
            "provider_request_id_sha256 REGEXP '^[0-9a-f]{64}$') "
            "AND (provider_task_id_sha256 IS NULL OR "
            "provider_task_id_sha256 REGEXP '^[0-9a-f]{64}$')",
            name="ck_generation_dispatch_attempt_hashes",
        ),
        Index(
            "ix_generation_dispatch_attempt_operation",
            "workspace_id",
            "durable_operation_id",
            "operation_attempt",
            "call_index",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    id: Mapped[str] = mapped_column(exact_string_sql_type(36), nullable=False)
    candidate_slot_id: Mapped[str] = mapped_column(exact_string_sql_type(36), nullable=False)
    durable_operation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    operation_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    call_index: Mapped[int] = mapped_column(Integer, nullable=False)
    endpoint_capability_version_id: Mapped[str] = mapped_column(
        exact_string_sql_type(36), nullable=False
    )
    request_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    adapter_configuration_sha256: Mapped[str] = mapped_column(
        exact_string_sql_type(64), nullable=False
    )
    idempotency_key_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    state: Mapped[str] = mapped_column(exact_string_sql_type(24), nullable=False)
    outcome: Mapped[str | None] = mapped_column(exact_string_sql_type(40))
    provider_request_id: Mapped[str | None] = mapped_column(exact_string_sql_type(256))
    provider_request_id_sha256: Mapped[str | None] = mapped_column(exact_string_sql_type(64))
    provider_task_id: Mapped[str | None] = mapped_column(exact_string_sql_type(256))
    provider_task_id_sha256: Mapped[str | None] = mapped_column(exact_string_sql_type(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ProviderCallModel(Base):
    __tablename__ = "generation_provider_calls"
    __table_args__ = (
        PrimaryKeyConstraint("workspace_id", "id", name="pk_provider_call"),
        UniqueConstraint(
            "workspace_id",
            "durable_operation_id",
            "operation_attempt",
            "call_index",
            name="uq_provider_call_attempt",
        ),
        UniqueConstraint(
            "workspace_id",
            "id",
            "candidate_slot_id",
            "endpoint_capability_version_id",
            name="uq_provider_call_slot_identity",
        ),
        UniqueConstraint(
            "workspace_id",
            "id",
            "durable_operation_id",
            "endpoint_capability_version_id",
            name="uq_provider_call_operation_identity",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "id"],
            ["generation_dispatch_attempts.workspace_id", "generation_dispatch_attempts.id"],
            name="fk_provider_call_dispatch_attempt",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "candidate_slot_id"],
            ["candidate_slots.workspace_id", "candidate_slots.id"],
            name="fk_provider_call_slot",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "durable_operation_id"],
            ["durable_operations.workspace_id", "durable_operations.id"],
            name="fk_provider_call_operation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "route_decision_sha256"],
            ["model_route_decisions.workspace_id", "model_route_decisions.decision_sha256"],
            name="fk_provider_call_route_decision",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["endpoint_capability_version_id"],
            ["provider_endpoint_capability_versions.id"],
            name="fk_provider_call_endpoint",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "operation_attempt > 0 AND call_index BETWEEN 0 AND 7",
            name="ck_provider_call_attempt",
        ),
        CheckConstraint(
            "outcome IN ('CONFIRMED_SUCCESS', 'CONFIRMED_FAILURE', 'CONTENT_REJECTED', "
            "'SAFE_TO_RETRY_PRE_DISPATCH', 'UNKNOWN_AFTER_POSSIBLE_DISPATCH')",
            name="ck_provider_call_outcome",
        ),
        CheckConstraint(
            "(outcome = 'SAFE_TO_RETRY_PRE_DISPATCH' AND possible_dispatch = 0 "
            "AND provider_request_id_sha256 IS NULL) OR "
            "(outcome <> 'SAFE_TO_RETRY_PRE_DISPATCH' AND possible_dispatch = 1)",
            name="ck_provider_call_dispatch_facts",
        ),
        CheckConstraint(
            "route_decision_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND request_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND idempotency_key_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND (provider_request_id_sha256 IS NULL "
            "OR provider_request_id_sha256 REGEXP '^[0-9a-f]{64}$')",
            name="ck_provider_call_hashes",
        ),
        CheckConstraint(
            "latency_ms BETWEEN 0 AND 86400000",
            name="ck_provider_call_latency",
        ),
        Index(
            "ix_provider_call_slot",
            "workspace_id",
            "candidate_slot_id",
            "observed_at",
            "id",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    id: Mapped[str] = mapped_column(exact_string_sql_type(36), nullable=False)
    candidate_slot_id: Mapped[str] = mapped_column(exact_string_sql_type(36), nullable=False)
    durable_operation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    operation_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    call_index: Mapped[int] = mapped_column(Integer, nullable=False)
    route_decision_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    endpoint_capability_version_id: Mapped[str] = mapped_column(
        exact_string_sql_type(36), nullable=False
    )
    provider: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    model: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    request_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    idempotency_key_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    outcome: Mapped[str] = mapped_column(exact_string_sql_type(40), nullable=False)
    possible_dispatch: Mapped[bool] = mapped_column(Boolean, nullable=False)
    provider_request_id_sha256: Mapped[str | None] = mapped_column(exact_string_sql_type(64))
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class UsageRecordModel(Base):
    __tablename__ = "generation_usage_records"
    __table_args__ = (
        PrimaryKeyConstraint("workspace_id", "id", name="pk_usage_record"),
        UniqueConstraint(
            "workspace_id",
            "provider_call_identity_sha256",
            name="uq_usage_record_call_identity",
        ),
        UniqueConstraint(
            "workspace_id",
            "id",
            "provider_call_id",
            name="uq_usage_record_call",
        ),
        ForeignKeyConstraint(
            [
                "workspace_id",
                "provider_call_id",
                "durable_operation_id",
                "endpoint_capability_version_id",
            ],
            [
                "generation_provider_calls.workspace_id",
                "generation_provider_calls.id",
                "generation_provider_calls.durable_operation_id",
                "generation_provider_calls.endpoint_capability_version_id",
            ],
            name="fk_usage_record_provider_call",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "durable_operation_id"],
            ["durable_operations.workspace_id", "durable_operations.id"],
            name="fk_usage_record_operation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["endpoint_capability_version_id"],
            ["provider_endpoint_capability_versions.id"],
            name="fk_usage_record_endpoint",
            ondelete="RESTRICT",
        ),
        CheckConstraint("operation_attempt > 0", name="ck_usage_record_attempt"),
        CheckConstraint(
            "estimated_quantity > 0 AND configured_unit_price >= 0 "
            "AND estimated_amount >= 0 AND "
            "(provider_reported_quantity IS NULL OR provider_reported_quantity > 0) "
            "AND (actual_amount IS NULL OR actual_amount >= 0)",
            name="ck_usage_record_amounts",
        ),
        CheckConstraint(
            "resolution_status IN ('UNRESOLVED', 'FINALIZED')",
            name="ck_usage_record_resolution",
        ),
        CheckConstraint(
            "(resolution_status = 'UNRESOLVED' AND actual_amount IS NULL "
            "AND final_cost_evidence_sha256 IS NULL) OR "
            "(resolution_status = 'FINALIZED' AND provider_reported_quantity IS NOT NULL "
            "AND provider_usage_evidence_sha256 IS NOT NULL AND actual_amount IS NOT NULL "
            "AND final_cost_evidence_sha256 IS NOT NULL)",
            name="ck_usage_record_resolution_facts",
        ),
        CheckConstraint(
            "provider_call_identity_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND pricing_evidence_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND (provider_usage_evidence_sha256 IS NULL OR "
            "provider_usage_evidence_sha256 REGEXP '^[0-9a-f]{64}$') "
            "AND (final_cost_evidence_sha256 IS NULL OR "
            "final_cost_evidence_sha256 REGEXP '^[0-9a-f]{64}$')",
            name="ck_usage_record_hashes",
        ),
        CheckConstraint(
            "currency REGEXP '^[A-Z]{3}$'",
            name="ck_usage_record_currency",
        ),
        CheckConstraint(
            "evidence_source IN ('DIRECT_RESPONSE', 'PROVIDER_RECONCILIATION', "
            "'OPERATOR_RECONCILIATION')",
            name="ck_usage_record_evidence_source",
        ),
        CheckConstraint(
            "latency_ms BETWEEN 0 AND 86400000",
            name="ck_usage_record_latency",
        ),
        Index(
            "ix_usage_record_operation",
            "workspace_id",
            "durable_operation_id",
            "recorded_at",
            "id",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    id: Mapped[str] = mapped_column(exact_string_sql_type(36), nullable=False)
    provider_call_id: Mapped[str] = mapped_column(exact_string_sql_type(36), nullable=False)
    provider_call_identity_sha256: Mapped[str] = mapped_column(
        exact_string_sql_type(64), nullable=False
    )
    durable_operation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    operation_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    model: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    endpoint_capability_version_id: Mapped[str] = mapped_column(
        exact_string_sql_type(36), nullable=False
    )
    pricing_unit: Mapped[str] = mapped_column(exact_string_sql_type(32), nullable=False)
    estimated_quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    provider_reported_quantity: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    configured_unit_price: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    estimated_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    actual_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    currency: Mapped[str] = mapped_column(exact_string_sql_type(3), nullable=False)
    unit_price_version: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    provider_usage_evidence_sha256: Mapped[str | None] = mapped_column(exact_string_sql_type(64))
    pricing_evidence_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    final_cost_evidence_sha256: Mapped[str | None] = mapped_column(exact_string_sql_type(64))
    resolution_status: Mapped[str] = mapped_column(exact_string_sql_type(24), nullable=False)
    evidence_source: Mapped[str] = mapped_column(exact_string_sql_type(32), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class CandidateImageModel(Base):
    __tablename__ = "candidate_images"
    __table_args__ = (
        PrimaryKeyConstraint("workspace_id", "id", name="pk_candidate_image"),
        UniqueConstraint("workspace_id", "candidate_slot_id", name="uq_candidate_image_slot"),
        UniqueConstraint(
            "workspace_id",
            "task_asset_version_id",
            name="uq_candidate_image_asset_version",
        ),
        ForeignKeyConstraint(
            [
                "workspace_id",
                "generation_batch_id",
                "workflow_id",
                "creative_plan_version_id",
            ],
            [
                "generation_batches.workspace_id",
                "generation_batches.id",
                "generation_batches.workflow_id",
                "generation_batches.creative_plan_version_id",
            ],
            name="fk_candidate_image_batch",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "candidate_slot_id", "generation_batch_id"],
            [
                "candidate_slots.workspace_id",
                "candidate_slots.id",
                "candidate_slots.generation_batch_id",
            ],
            name="fk_candidate_image_slot",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "task_asset_version_id"],
            ["asset_versions.workspace_id", "asset_versions.id"],
            name="fk_candidate_image_asset_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "creative_plan_version_id"],
            ["creative_plan_versions.workspace_id", "creative_plan_versions.id"],
            name="fk_candidate_image_plan_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["endpoint_capability_version_id"],
            ["provider_endpoint_capability_versions.id"],
            name="fk_candidate_image_endpoint",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "workspace_id",
                "provider_call_id",
                "candidate_slot_id",
                "endpoint_capability_version_id",
            ],
            [
                "generation_provider_calls.workspace_id",
                "generation_provider_calls.id",
                "generation_provider_calls.candidate_slot_id",
                "generation_provider_calls.endpoint_capability_version_id",
            ],
            name="fk_candidate_image_provider_call",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "usage_record_id", "provider_call_id"],
            [
                "generation_usage_records.workspace_id",
                "generation_usage_records.id",
                "generation_usage_records.provider_call_id",
            ],
            name="fk_candidate_image_usage_record",
            ondelete="RESTRICT",
        ),
        CheckConstraint("width > 0 AND height > 0", name="ck_candidate_image_dimensions"),
        CheckConstraint("retention_deadline > created_at", name="ck_candidate_image_retention"),
        CheckConstraint(
            "content_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND prompt_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND context_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND retrieval_snapshot_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND provider_request_id_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND moderation_decision_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_candidate_image_hashes",
        ),
        Index(
            "ix_candidate_image_workflow",
            "workspace_id",
            "workflow_id",
            "created_at",
            "id",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    id: Mapped[str] = mapped_column(exact_string_sql_type(36), nullable=False)
    workflow_id: Mapped[str] = mapped_column(String(36), nullable=False)
    generation_batch_id: Mapped[str] = mapped_column(exact_string_sql_type(36), nullable=False)
    candidate_slot_id: Mapped[str] = mapped_column(exact_string_sql_type(36), nullable=False)
    task_asset_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    content_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    image_format: Mapped[str] = mapped_column(exact_string_sql_type(16), nullable=False)
    source_asset_version_ids_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    creative_plan_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    prompt_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    context_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    retrieval_snapshot_sha256: Mapped[str] = mapped_column(
        exact_string_sql_type(64), nullable=False
    )
    endpoint_capability_version_id: Mapped[str] = mapped_column(
        exact_string_sql_type(36), nullable=False
    )
    provider_call_id: Mapped[str] = mapped_column(exact_string_sql_type(36), nullable=False)
    provider_request_id_sha256: Mapped[str] = mapped_column(
        exact_string_sql_type(64), nullable=False
    )
    moderation_decision_sha256: Mapped[str] = mapped_column(
        exact_string_sql_type(64), nullable=False
    )
    usage_record_id: Mapped[str] = mapped_column(exact_string_sql_type(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    retention_deadline: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
