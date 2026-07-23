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


# Reserved for Phase 4 cost accounting without a destructive type migration.
MONEY_AMOUNT_TYPE = Numeric(20, 6)
MONEY_AMOUNT_PYTHON_TYPE = Decimal
SEQUENCE_TYPE = BigInteger
