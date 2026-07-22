"""Versioned event contracts shared by producers, routing, and workers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from commercevision_domain import ApprovalDecision, ApprovalType, WorkflowStatus
from pydantic import BaseModel, ConfigDict, Field, JsonValue, RootModel


class EventQueue(StrEnum):
    WORKFLOW = "workflow"
    ASSET = "asset"
    INDEX = "index"
    MAINTENANCE = "maintenance"


class EventHandling(StrEnum):
    COMMAND = "command"
    OBSERVATION = "observation"


class EventType(StrEnum):
    WORKFLOW_RUN_REQUESTED = "workflow.run.requested"
    WORKFLOW_RESUME_REQUESTED = "workflow.resume.requested"
    WORKFLOW_NODE_STARTED = "workflow.node.started"
    WORKFLOW_NODE_COMPLETED = "workflow.node.completed"
    WORKFLOW_HUMAN_INPUT_REQUIRED = "workflow.human_input.required"
    WORKFLOW_HUMAN_INPUT_RECEIVED = "workflow.human_input.received"
    WORKFLOW_FAILED = "workflow.failed"
    WORKFLOW_CANCELLED = "workflow.cancelled"

    ASSET_UPLOAD_FINALIZED = "asset.upload.finalized"
    ASSET_VALIDATION_REQUESTED = "asset.validation.requested"
    ASSET_VALIDATION_COMPLETED = "asset.validation.completed"
    ASSET_VALIDATION_FAILED = "asset.validation.failed"
    ASSET_RIGHTS_CHANGED = "asset.rights.changed"
    ASSET_RIGHTS_EXPIRED = "asset.rights.expired"
    PRODUCT_BRIEF_REQUESTED = "product-brief.requested"
    PRODUCT_BRIEF_AWAITING_CONFIRMATION = "product-brief.awaiting-confirmation"
    PRODUCT_BRIEF_CONFIRMED = "product-brief.confirmed"
    BRAND_PROFILE_PUBLISHED = "brand-profile.published"
    ASSET_INDEX_REQUESTED = "asset.index.requested"
    ASSET_INDEX_COMPLETED = "asset.index.completed"
    ASSET_INDEX_DELETE_REQUESTED = "asset.index.delete-requested"
    COLLECTION_REBUILD_REQUESTED = "collection-rebuild.requested"
    COLLECTION_REBUILD_PROGRESSED = "collection-rebuild.progressed"
    COLLECTION_REBUILD_COMPLETED = "collection-rebuild.completed"
    ASSET_DELETE_REQUESTED = "asset.delete.requested"
    ASSET_DELETE_COMPLETED = "asset.delete.completed"
    RECONCILIATION_REQUESTED = "reconciliation.requested"


class CompatibleEventPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")


class WorkflowRunRequestedPayload(CompatibleEventPayload):
    workflow_id: str = Field(min_length=1, max_length=36)
    action: Literal["start", "retry", "recover"]
    reason: str | None = Field(default=None, min_length=1, max_length=160)


class WorkflowResumeRequestedPayload(CompatibleEventPayload):
    workflow_id: str = Field(min_length=1, max_length=36)
    approval_id: str = Field(min_length=1, max_length=36)
    approval_type: ApprovalType
    decision: ApprovalDecision
    expected_workflow_version: int = Field(ge=1)
    resulting_workflow_version: int = Field(ge=1)
    subject_id: str = Field(min_length=1, max_length=128)
    subject_version: int = Field(ge=1)


class WorkflowNodeStartedPayload(CompatibleEventPayload):
    node: str = Field(min_length=1, max_length=128)
    step_id: str = Field(min_length=1, max_length=36)
    step_key: str = Field(min_length=1, max_length=160)


class WorkflowNodeCompletedPayload(CompatibleEventPayload):
    node: str = Field(min_length=1, max_length=128)
    completed_step_id: str = Field(min_length=1, max_length=36)
    status: WorkflowStatus


class WorkflowHumanInputRequiredPayload(CompatibleEventPayload):
    step_id: str = Field(min_length=1, max_length=36)
    step_key: str = Field(min_length=1, max_length=160)


class WorkflowHumanInputReceivedPayload(CompatibleEventPayload):
    step_id: str = Field(min_length=1, max_length=36)
    decision: ApprovalDecision | None = None


class WorkflowFailedPayload(CompatibleEventPayload):
    workflow_id: str = Field(min_length=1, max_length=36)
    step_id: str = Field(min_length=1, max_length=36)
    error_class: str = Field(min_length=1, max_length=160)


class WorkflowCancelledPayload(CompatibleEventPayload):
    workflow_id: str = Field(min_length=1, max_length=36)


class PendingPhase2Payload(RootModel[dict[str, JsonValue]]):
    """JSON payload boundary for Phase 2 events whose owning ticket defines fields later."""


@dataclass(frozen=True, slots=True)
class EventContract:
    event_type: EventType
    schema_version: int
    queue: EventQueue
    payload_model: type[BaseModel]
    handling: EventHandling

    def validate_payload(self, payload: object) -> BaseModel:
        return self.payload_model.model_validate(payload)


WORKFLOW_RUN_REQUESTED_V1 = EventContract(
    EventType.WORKFLOW_RUN_REQUESTED,
    1,
    EventQueue.WORKFLOW,
    WorkflowRunRequestedPayload,
    EventHandling.COMMAND,
)
WORKFLOW_RESUME_REQUESTED_V1 = EventContract(
    EventType.WORKFLOW_RESUME_REQUESTED,
    1,
    EventQueue.WORKFLOW,
    WorkflowResumeRequestedPayload,
    EventHandling.COMMAND,
)
WORKFLOW_NODE_STARTED_V1 = EventContract(
    EventType.WORKFLOW_NODE_STARTED,
    1,
    EventQueue.WORKFLOW,
    WorkflowNodeStartedPayload,
    EventHandling.OBSERVATION,
)
WORKFLOW_NODE_COMPLETED_V1 = EventContract(
    EventType.WORKFLOW_NODE_COMPLETED,
    1,
    EventQueue.WORKFLOW,
    WorkflowNodeCompletedPayload,
    EventHandling.OBSERVATION,
)
WORKFLOW_HUMAN_INPUT_REQUIRED_V1 = EventContract(
    EventType.WORKFLOW_HUMAN_INPUT_REQUIRED,
    1,
    EventQueue.WORKFLOW,
    WorkflowHumanInputRequiredPayload,
    EventHandling.OBSERVATION,
)
WORKFLOW_HUMAN_INPUT_RECEIVED_V1 = EventContract(
    EventType.WORKFLOW_HUMAN_INPUT_RECEIVED,
    1,
    EventQueue.WORKFLOW,
    WorkflowHumanInputReceivedPayload,
    EventHandling.OBSERVATION,
)
WORKFLOW_FAILED_V1 = EventContract(
    EventType.WORKFLOW_FAILED,
    1,
    EventQueue.WORKFLOW,
    WorkflowFailedPayload,
    EventHandling.OBSERVATION,
)
WORKFLOW_CANCELLED_V1 = EventContract(
    EventType.WORKFLOW_CANCELLED,
    1,
    EventQueue.WORKFLOW,
    WorkflowCancelledPayload,
    EventHandling.OBSERVATION,
)

PHASE1_EVENT_CONTRACTS = (
    WORKFLOW_RUN_REQUESTED_V1,
    WORKFLOW_RESUME_REQUESTED_V1,
    WORKFLOW_NODE_STARTED_V1,
    WORKFLOW_NODE_COMPLETED_V1,
    WORKFLOW_HUMAN_INPUT_REQUIRED_V1,
    WORKFLOW_HUMAN_INPUT_RECEIVED_V1,
    WORKFLOW_FAILED_V1,
    WORKFLOW_CANCELLED_V1,
)


def _phase2_contract(
    event_type: EventType,
    queue: EventQueue,
    handling: EventHandling,
) -> EventContract:
    return EventContract(event_type, 1, queue, PendingPhase2Payload, handling)


PHASE2_EVENT_CONTRACTS = (
    _phase2_contract(
        EventType.ASSET_UPLOAD_FINALIZED,
        EventQueue.ASSET,
        EventHandling.OBSERVATION,
    ),
    _phase2_contract(
        EventType.ASSET_VALIDATION_REQUESTED,
        EventQueue.ASSET,
        EventHandling.COMMAND,
    ),
    _phase2_contract(
        EventType.ASSET_VALIDATION_COMPLETED,
        EventQueue.ASSET,
        EventHandling.OBSERVATION,
    ),
    _phase2_contract(
        EventType.ASSET_VALIDATION_FAILED,
        EventQueue.ASSET,
        EventHandling.OBSERVATION,
    ),
    _phase2_contract(
        EventType.ASSET_RIGHTS_CHANGED,
        EventQueue.ASSET,
        EventHandling.OBSERVATION,
    ),
    _phase2_contract(
        EventType.ASSET_RIGHTS_EXPIRED,
        EventQueue.ASSET,
        EventHandling.OBSERVATION,
    ),
    _phase2_contract(
        EventType.PRODUCT_BRIEF_REQUESTED,
        EventQueue.ASSET,
        EventHandling.COMMAND,
    ),
    _phase2_contract(
        EventType.PRODUCT_BRIEF_AWAITING_CONFIRMATION,
        EventQueue.ASSET,
        EventHandling.OBSERVATION,
    ),
    _phase2_contract(
        EventType.PRODUCT_BRIEF_CONFIRMED,
        EventQueue.ASSET,
        EventHandling.OBSERVATION,
    ),
    _phase2_contract(
        EventType.BRAND_PROFILE_PUBLISHED,
        EventQueue.ASSET,
        EventHandling.OBSERVATION,
    ),
    _phase2_contract(
        EventType.ASSET_INDEX_REQUESTED,
        EventQueue.INDEX,
        EventHandling.COMMAND,
    ),
    _phase2_contract(
        EventType.ASSET_INDEX_COMPLETED,
        EventQueue.INDEX,
        EventHandling.OBSERVATION,
    ),
    _phase2_contract(
        EventType.ASSET_INDEX_DELETE_REQUESTED,
        EventQueue.INDEX,
        EventHandling.COMMAND,
    ),
    _phase2_contract(
        EventType.COLLECTION_REBUILD_REQUESTED,
        EventQueue.INDEX,
        EventHandling.COMMAND,
    ),
    _phase2_contract(
        EventType.COLLECTION_REBUILD_PROGRESSED,
        EventQueue.INDEX,
        EventHandling.OBSERVATION,
    ),
    _phase2_contract(
        EventType.COLLECTION_REBUILD_COMPLETED,
        EventQueue.INDEX,
        EventHandling.OBSERVATION,
    ),
    _phase2_contract(
        EventType.ASSET_DELETE_REQUESTED,
        EventQueue.MAINTENANCE,
        EventHandling.COMMAND,
    ),
    _phase2_contract(
        EventType.ASSET_DELETE_COMPLETED,
        EventQueue.MAINTENANCE,
        EventHandling.OBSERVATION,
    ),
    _phase2_contract(
        EventType.RECONCILIATION_REQUESTED,
        EventQueue.MAINTENANCE,
        EventHandling.COMMAND,
    ),
)

EVENT_CONTRACTS = (*PHASE1_EVENT_CONTRACTS, *PHASE2_EVENT_CONTRACTS)
_EVENT_CONTRACTS_BY_KEY = {
    (contract.event_type.value, contract.schema_version): contract for contract in EVENT_CONTRACTS
}


def event_contract_for(event_type: EventType | str, schema_version: int) -> EventContract:
    return _EVENT_CONTRACTS_BY_KEY[(str(event_type), schema_version)]


def event_contracts_for_type(event_type: EventType | str) -> tuple[EventContract, ...]:
    event_type_value = str(event_type)
    return tuple(
        contract for contract in EVENT_CONTRACTS if contract.event_type.value == event_type_value
    )
