from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from commercevision_application import (
    ApprovedGenerationAuthority,
    ApprovedPlanGenerationCommand,
    ApprovedPlanGenerationService,
)
from commercevision_contracts.events import (
    EventType,
    GenerationCandidateRequestedPayload,
)
from commercevision_domain import ConcurrencyError, OperationKind, OperationState
from commercevision_domain.workflow.errors import IdempotencyConflictError

NOW = datetime(2026, 8, 6, 8, 30, tzinfo=UTC)
WORKSPACE_ID = "phase4-generation"
WORKFLOW_ID = "019b0000-0000-7000-8000-000000000501"
PLAN_ID = "019b0000-0000-7000-8000-000000000502"
PLAN_VERSION_ID = "019b0000-0000-7000-8000-000000000503"
APPROVAL_ID = "019b0000-0000-7000-8000-000000000504"
ROUTE_DECISION_SHA256 = "a" * 64


def _command() -> ApprovedPlanGenerationCommand:
    return ApprovedPlanGenerationCommand(
        workspace_id=WORKSPACE_ID,
        workflow_id=WORKFLOW_ID,
        expected_workflow_version=9,
        creative_plan_id=PLAN_ID,
        creative_plan_version_id=PLAN_VERSION_ID,
        creative_plan_version=3,
        approval_id=APPROVAL_ID,
        direction_key="main-image",
        tool_intent_key="generate-main-image",
        route_decision_sha256=ROUTE_DECISION_SHA256,
    )


def _authority() -> ApprovedGenerationAuthority:
    return ApprovedGenerationAuthority(
        workspace_id=WORKSPACE_ID,
        workflow_id=WORKFLOW_ID,
        workflow_version=9,
        creative_plan_id=PLAN_ID,
        creative_plan_version_id=PLAN_VERSION_ID,
        creative_plan_version=3,
        approval_id=APPROVAL_ID,
        direction_key="main-image",
        tool_intent_key="generate-main-image",
        tool_intent_sha256="b" * 64,
        prompt_sha256="c" * 64,
        context_sha256="d" * 64,
        route_decision_sha256=ROUTE_DECISION_SHA256,
        route_request_sha256="e" * 64,
        operation_kind=OperationKind.IMAGE_GENERATION,
        authorized_asset_version_ids=("019b0000-0000-7000-8000-000000000511",),
        candidate_count=2,
        route_policy_version="image-route.v1",
        tool_policy_version="tool-policy.v3",
        rights_policy_version="rights.v2",
        safety_policy_version="media-safety.v4",
        workflow_deadline=NOW + timedelta(hours=24),
        source_rights_deadline=NOW + timedelta(hours=12),
        retention_deadline=NOW + timedelta(hours=6),
        created_by="generation-service",
    )


@dataclass
class _IdempotencyRecord:
    request_hash: str
    status: str = "PENDING"
    response_data: dict[str, Any] | None = None
    resource_type: str = ""
    resource_id: str = ""


class _Repository:
    def __init__(self) -> None:
        self.items: list[Any] = []

    def add(self, item: Any) -> None:
        self.items.append(item)

    def get(self, item_id: str, *, workspace_id: str) -> Any | None:
        return next(
            (
                item
                for item in self.items
                if item.id == item_id and item.workspace_id == workspace_id
            ),
            None,
        )

    def list_for_batch(self, *, workspace_id: str, generation_batch_id: str) -> tuple[Any, ...]:
        return tuple(
            item
            for item in self.items
            if item.workspace_id == workspace_id and item.generation_batch_id == generation_batch_id
        )

    def get_by_logical_identity(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        workflow_version: int,
        creative_plan_version_id: str,
        direction_key: str,
        tool_intent_key: str,
    ) -> Any | None:
        return next(
            (
                item
                for item in self.items
                if item.workspace_id == workspace_id
                and item.workflow_id == workflow_id
                and item.workflow_version == workflow_version
                and item.creative_plan_version_id == creative_plan_version_id
                and item.direction_key == direction_key
                and item.tool_intent_key == tool_intent_key
            ),
            None,
        )


class _OutboxRepository(_Repository):
    pass


class _AuditRepository:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def add(self, **record: Any) -> None:
        self.records.append(record)


class _IdempotencyRepository:
    def __init__(self) -> None:
        self.records: dict[tuple[str, str], _IdempotencyRecord] = {}

    def claim(
        self,
        *,
        scope: str,
        key_hash: str,
        request_hash: str,
        expires_at: datetime,
    ) -> _IdempotencyRecord:
        del expires_at
        return self.records.setdefault((scope, key_hash), _IdempotencyRecord(request_hash))

    def complete(
        self,
        *,
        scope: str,
        key_hash: str,
        request_hash: str,
        resource_type: str,
        resource_id: str,
        response_data: dict[str, Any],
    ) -> None:
        record = self.records[(scope, key_hash)]
        assert record.request_hash == request_hash
        record.status = "COMPLETED"
        record.resource_type = resource_type
        record.resource_id = resource_id
        record.response_data = response_data


class _AuthorityRepository:
    def __init__(self, authority: ApprovedGenerationAuthority) -> None:
        self.authority = authority
        self.calls: list[ApprovedPlanGenerationCommand] = []

    def load_current_authority(
        self, command: ApprovedPlanGenerationCommand
    ) -> ApprovedGenerationAuthority:
        self.calls.append(command)
        return self.authority


class _UnitOfWork:
    def __init__(self, authority: ApprovedGenerationAuthority) -> None:
        self.generation_authority = _AuthorityRepository(authority)
        self.generation_batches = _Repository()
        self.candidate_slots = _Repository()
        self.operations = _Repository()
        self.outbox = _OutboxRepository()
        self.audit = _AuditRepository()
        self.idempotency = _IdempotencyRepository()
        self.commits = 0

    def __enter__(self) -> _UnitOfWork:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def database_now(self) -> datetime:
        return NOW

    def commit(self) -> None:
        self.commits += 1

    def flush(self) -> None:
        return None


def test_exact_approved_plan_command_creates_one_atomic_generation_batch() -> None:
    unit_of_work = _UnitOfWork(_authority())
    service = ApprovedPlanGenerationService(lambda: unit_of_work)

    result = service.start(
        command=_command(),
        idempotency_key="test-test-test",
        trace_id="trace-generation-1",
    )

    assert result.replayed is False
    assert result.batch.route_decision_sha256 == ROUTE_DECISION_SHA256
    assert result.batch.candidate_count == 2
    assert result.batch.workflow_version == 9
    assert result.batch.retention_deadline == NOW + timedelta(hours=6)
    assert result.slots == tuple(unit_of_work.candidate_slots.items)
    assert result.operations == tuple(unit_of_work.operations.items)
    assert len(result.slots) == len(result.operations) == 2
    assert tuple(slot.candidate_index for slot in result.slots) == (0, 1)
    assert tuple(slot.durable_operation_id for slot in result.slots) == tuple(
        operation.id for operation in result.operations
    )
    assert all(operation.kind is OperationKind.IMAGE_GENERATION for operation in result.operations)
    assert all(operation.state is OperationState.PENDING for operation in result.operations)
    assert all(
        operation.target_type == "generation-candidate-slot" for operation in result.operations
    )
    assert tuple(operation.target_id for operation in result.operations) == tuple(
        slot.id for slot in result.slots
    )
    assert len(unit_of_work.generation_batches.items) == 1
    assert len(unit_of_work.outbox.items) == 2
    assert all(
        event.envelope.event_type == EventType.GENERATION_CANDIDATE_REQUESTED.value
        for event in unit_of_work.outbox.items
    )
    assert all(
        GenerationCandidateRequestedPayload.model_validate(event.envelope.payload)
        for event in unit_of_work.outbox.items
    )
    assert {event.envelope.payload["candidate_slot_id"] for event in unit_of_work.outbox.items} == {
        slot.id for slot in result.slots
    }
    assert len(unit_of_work.audit.records) == 1
    assert unit_of_work.audit.records[0]["metadata"] == {
        "workflow_id": WORKFLOW_ID,
        "creative_plan_version_id": PLAN_VERSION_ID,
        "approval_id": APPROVAL_ID,
        "direction_key": "main-image",
        "tool_intent_key": "generate-main-image",
        "route_decision_sha256": ROUTE_DECISION_SHA256,
        "candidate_count": 2,
        "operation_kind": "IMAGE_GENERATION",
    }
    record = next(iter(unit_of_work.idempotency.records.values()))
    assert record.status == "COMPLETED"
    assert record.resource_type == "generation-batch"
    assert record.resource_id == result.batch.id
    assert unit_of_work.commits == 1


def test_generation_command_replays_original_aggregate_and_rejects_conflicting_reuse() -> None:
    unit_of_work = _UnitOfWork(_authority())
    service = ApprovedPlanGenerationService(lambda: unit_of_work)

    first = service.start(
        command=_command(),
        idempotency_key="approved-plan-generation-replay",
        trace_id="trace-generation-first",
    )
    replay = service.start(
        command=_command(),
        idempotency_key="approved-plan-generation-replay",
        trace_id="trace-generation-replay",
    )

    assert replay.replayed is True
    assert replay.batch == first.batch
    assert replay.slots == first.slots
    assert replay.operations == first.operations

    alias = service.start(
        command=_command(),
        idempotency_key="approved-plan-generation-alias",
        trace_id="trace-generation-alias",
    )
    assert alias == replay
    assert len(unit_of_work.generation_authority.calls) == 1
    assert len(unit_of_work.generation_batches.items) == 1
    assert len(unit_of_work.candidate_slots.items) == 2
    assert len(unit_of_work.operations.items) == 2
    assert len(unit_of_work.outbox.items) == 2
    assert len(unit_of_work.audit.records) == 1
    assert unit_of_work.commits == 2

    with pytest.raises(ConcurrencyError, match="different Route Decision"):
        service.start(
            command=replace(_command(), route_decision_sha256="f" * 64),
            idempotency_key="approved-plan-generation-route-conflict",
            trace_id="trace-generation-route-conflict",
        )

    with pytest.raises(IdempotencyConflictError, match="different generation command"):
        service.start(
            command=replace(_command(), expected_workflow_version=10),
            idempotency_key="approved-plan-generation-replay",
            trace_id="trace-generation-conflict",
        )

    assert len(unit_of_work.generation_authority.calls) == 1
    assert len(unit_of_work.generation_batches.items) == 1
    assert len(unit_of_work.candidate_slots.items) == 2
    assert len(unit_of_work.operations.items) == 2
    assert len(unit_of_work.outbox.items) == 2
    assert len(unit_of_work.audit.records) == 1
    assert unit_of_work.commits == 2
