from __future__ import annotations

import pytest
from commercevision_contracts.events import (
    EVENT_CONTRACTS,
    PHASE1_EVENT_CONTRACTS,
    DeadLetterReplayRecordedPayload,
    EventHandling,
    EventQueue,
    EventType,
    OperationRecoveryRequestedPayload,
    WorkflowNodeStartedPayload,
    WorkflowRunRequestedPayload,
    event_contract_for,
)
from pydantic import BaseModel, ValidationError


def test_phase1_event_contracts_enumerate_every_current_emitter() -> None:
    assert {
        (contract.event_type, contract.schema_version, contract.queue, contract.handling)
        for contract in PHASE1_EVENT_CONTRACTS
    } == {
        (
            EventType.WORKFLOW_RUN_REQUESTED,
            1,
            EventQueue.WORKFLOW,
            EventHandling.COMMAND,
        ),
        (
            EventType.WORKFLOW_RESUME_REQUESTED,
            1,
            EventQueue.WORKFLOW,
            EventHandling.COMMAND,
        ),
        (
            EventType.WORKFLOW_NODE_STARTED,
            1,
            EventQueue.WORKFLOW,
            EventHandling.OBSERVATION,
        ),
        (
            EventType.WORKFLOW_NODE_COMPLETED,
            1,
            EventQueue.WORKFLOW,
            EventHandling.OBSERVATION,
        ),
        (
            EventType.WORKFLOW_HUMAN_INPUT_REQUIRED,
            1,
            EventQueue.WORKFLOW,
            EventHandling.OBSERVATION,
        ),
        (
            EventType.WORKFLOW_HUMAN_INPUT_RECEIVED,
            1,
            EventQueue.WORKFLOW,
            EventHandling.OBSERVATION,
        ),
        (
            EventType.WORKFLOW_FAILED,
            1,
            EventQueue.WORKFLOW,
            EventHandling.OBSERVATION,
        ),
        (
            EventType.WORKFLOW_CANCELLED,
            1,
            EventQueue.WORKFLOW,
            EventHandling.OBSERVATION,
        ),
    }


def test_every_event_contract_has_a_versioned_pydantic_payload() -> None:
    assert EVENT_CONTRACTS
    assert all(contract.schema_version >= 1 for contract in EVENT_CONTRACTS)
    assert all(issubclass(contract.payload_model, BaseModel) for contract in EVENT_CONTRACTS)


def test_event_contract_validates_payload_and_ignores_compatible_extra_fields() -> None:
    contract = event_contract_for(EventType.WORKFLOW_NODE_STARTED, 1)

    payload = contract.validate_payload(
        {
            "node": "validate_input",
            "step_id": "step-1",
            "step_key": "validate_input",
            "future_optional_field": "compatible",
        }
    )

    assert isinstance(payload, WorkflowNodeStartedPayload)
    assert payload.step_id == "step-1"
    assert "future_optional_field" not in payload.model_dump()


def test_event_contract_rejects_malformed_payload() -> None:
    contract = event_contract_for(EventType.WORKFLOW_RUN_REQUESTED, 1)

    with pytest.raises(ValidationError):
        contract.validate_payload({"action": "start"})


def test_workflow_run_payload_accepts_all_existing_phase1_actions() -> None:
    for action in ("start", "retry", "recover"):
        payload = WorkflowRunRequestedPayload(
            workflow_id="workflow-1",
            action=action,
            reason="expired_step_lease" if action == "recover" else None,
        )
        assert payload.action == action


def test_operation_recovery_contract_is_typed_and_routes_to_maintenance() -> None:
    contract = event_contract_for(EventType.OPERATION_RECOVERY_REQUESTED, 1)

    payload = contract.validate_payload(
        {
            "operation_id": "operation-1",
            "workspace_id": "workspace-a",
            "operation_kind": "ASSET_INDEXING",
            "recovery_reason": "UNKNOWN_EXTERNAL_OUTCOME",
        }
    )

    assert isinstance(payload, OperationRecoveryRequestedPayload)
    assert contract.queue == EventQueue.MAINTENANCE
    assert contract.handling == EventHandling.COMMAND


def test_dead_letter_replay_contract_carries_source_identity_and_attempt() -> None:
    contract = event_contract_for(EventType.DEAD_LETTER_REPLAY_RECORDED, 1)

    payload = contract.validate_payload(
        {
            "source_dead_letter_id": "dead-letter-1",
            "replay_id": "replay-1",
            "workspace_id": "workspace-a",
            "replay_attempt": 2,
        }
    )

    assert isinstance(payload, DeadLetterReplayRecordedPayload)
    assert payload.replay_attempt == 2
    assert contract.handling == EventHandling.OBSERVATION
