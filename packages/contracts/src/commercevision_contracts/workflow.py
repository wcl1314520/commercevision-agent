"""Versioned HTTP contracts for the durable workflow runtime."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from commercevision_domain import (
    ApprovalDecision,
    ApprovalType,
    AttemptStatus,
    RetentionStatus,
    StepStatus,
    StepType,
    WorkflowStatus,
)
from pydantic import BaseModel, ConfigDict, Field


class WorkflowCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_type: str = Field(default="FIXTURE_IMAGE_GENERATION", min_length=1, max_length=64)
    input_data: dict[str, Any] = Field(default_factory=dict)
    retention_hours: int = Field(default=72, ge=1, le=168)


class WorkflowCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_workflow_version: int = Field(ge=1)


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_workflow_version: int = Field(ge=1)
    subject_id: str = Field(min_length=1, max_length=128)
    subject_version: int = Field(ge=1)
    decision: ApprovalDecision
    reason_code: str | None = Field(default=None, max_length=128)
    comment_ref: str | None = Field(default=None, max_length=512)


class WorkflowStepResponse(BaseModel):
    id: str
    step_key: str
    step_type: StepType
    status: StepStatus
    sequence: int
    attempt_count: int
    max_attempts: int
    lease_expires_at: datetime | None
    output_ref: str | None
    output_data: dict[str, Any] | None
    error_class: str | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None


class WorkflowAttemptResponse(BaseModel):
    id: str
    step_id: str
    attempt_number: int
    idempotency_key: str
    status: AttemptStatus
    provider_request_id: str | None
    result_ref: str | None
    result_data: dict[str, Any] | None
    error_class: str | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None


class ApprovalResponse(BaseModel):
    id: str
    approval_type: ApprovalType
    subject_id: str
    subject_version: int
    decision: ApprovalDecision
    approved_by: str
    expected_workflow_version: int
    created_at: datetime


class WorkflowResponse(BaseModel):
    id: str
    workspace_id: str
    created_by: str
    workflow_type: str
    status: WorkflowStatus
    retention_status: RetentionStatus
    current_node: str | None
    version: int
    input_data: dict[str, Any]
    result_data: dict[str, Any] | None
    expires_at: datetime
    cancellation_requested_at: datetime | None
    created_at: datetime
    updated_at: datetime
    steps: list[WorkflowStepResponse] = Field(default_factory=list)
    attempts: list[WorkflowAttemptResponse] = Field(default_factory=list)
    approvals: list[ApprovalResponse] = Field(default_factory=list)


class WorkflowListResponse(BaseModel):
    items: list[WorkflowResponse]
    next_cursor: str | None


class EventResponse(BaseModel):
    event_id: str
    event_type: str
    schema_version: int
    aggregate_type: str
    aggregate_id: str
    aggregate_version: int
    occurred_at: datetime
    trace_id: str
    payload: dict[str, Any]


class ResumePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    approval_id: str
    approval_type: ApprovalType
    decision: ApprovalDecision
    expected_workflow_version: int
    resulting_workflow_version: int
    subject_id: str
    subject_version: int
