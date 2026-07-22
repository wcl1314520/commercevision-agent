"""Stable domain enumerations persisted by name."""

from enum import StrEnum


class WorkflowStatus(StrEnum):
    DRAFT = "DRAFT"
    INGESTING = "INGESTING"
    UNDERSTANDING = "UNDERSTANDING"
    AWAITING_PRODUCT_CONFIRMATION = "AWAITING_PRODUCT_CONFIRMATION"
    RETRIEVING = "RETRIEVING"
    PLANNING = "PLANNING"
    AWAITING_PLAN_APPROVAL = "AWAITING_PLAN_APPROVAL"
    GENERATING = "GENERATING"
    EVALUATING = "EVALUATING"
    REPAIRING = "REPAIRING"
    AWAITING_RESULT_APPROVAL = "AWAITING_RESULT_APPROVAL"
    EXPORTING = "EXPORTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def terminal(self) -> bool:
        return self in {
            WorkflowStatus.COMPLETED,
            WorkflowStatus.FAILED,
            WorkflowStatus.CANCELLED,
        }

    @property
    def waiting_for_human(self) -> bool:
        return self in {
            WorkflowStatus.AWAITING_PRODUCT_CONFIRMATION,
            WorkflowStatus.AWAITING_PLAN_APPROVAL,
            WorkflowStatus.AWAITING_RESULT_APPROVAL,
        }


class RetentionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    EXPIRING = "EXPIRING"
    DELETING = "DELETING"
    EXPIRED = "EXPIRED"


class StepStatus(StrEnum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    CLAIMED = "CLAIMED"
    RUNNING = "RUNNING"
    WAITING_HUMAN = "WAITING_HUMAN"
    SUCCEEDED = "SUCCEEDED"
    RETRYABLE_FAILED = "RETRYABLE_FAILED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def terminal(self) -> bool:
        return self in {StepStatus.SUCCEEDED, StepStatus.FAILED, StepStatus.CANCELLED}


class AttemptStatus(StrEnum):
    CREATED = "CREATED"
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"
    POLLING = "POLLING"
    SUCCEEDED = "SUCCEEDED"
    UNKNOWN = "UNKNOWN"
    RETRYABLE_FAILED = "RETRYABLE_FAILED"
    PERMANENT_FAILED = "PERMANENT_FAILED"
    CANCELLED = "CANCELLED"

    @property
    def terminal(self) -> bool:
        return self in {
            AttemptStatus.SUCCEEDED,
            AttemptStatus.PERMANENT_FAILED,
            AttemptStatus.CANCELLED,
        }


class StepType(StrEnum):
    VALIDATE_INPUT = "VALIDATE_INPUT"
    UNDERSTAND_PRODUCT = "UNDERSTAND_PRODUCT"
    RETRIEVE_REFERENCES = "RETRIEVE_REFERENCES"
    CREATE_PLAN = "CREATE_PLAN"
    APPROVE_PLAN = "APPROVE_PLAN"
    EXECUTE_TOOL = "EXECUTE_TOOL"
    EVALUATE_RESULTS = "EVALUATE_RESULTS"
    APPROVE_RESULTS = "APPROVE_RESULTS"
    EXPORT = "EXPORT"


class ApprovalType(StrEnum):
    PRODUCT_BRIEF = "PRODUCT_BRIEF"
    CREATIVE_PLAN = "CREATIVE_PLAN"
    RESULTS = "RESULTS"


class ApprovalDecision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REGENERATE = "REGENERATE"


class InboxStatus(StrEnum):
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"
    DEAD = "DEAD"
