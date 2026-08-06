from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

import pytest
from commercevision_application import (
    EventRoutingError,
    GenerationWorkflowContinuationClaim,
)
from commercevision_contracts import Settings
from commercevision_contracts.events import (
    GENERATION_CANDIDATE_READY_V1,
    GENERATION_CANDIDATE_REQUESTED_V1,
    EventQueue,
    GenerationCandidateReadyPayload,
    GenerationCandidateRequestedPayload,
)
from commercevision_domain import OperationKind, OperationState
from commercevision_domain.messaging import EventEnvelope, OutboxEvent
from commercevision_worker.runtime import WorkerRuntime


class _NeverCalledExecutor:
    @staticmethod
    def execute(_request: object) -> object:
        raise AssertionError("the routing contract must not execute the Provider adapter")

    @staticmethod
    def reconcile(_request: object) -> object:
        raise AssertionError("the routing contract must not reconcile a Provider request")


class _ClosableStorage:
    def close(self) -> None:
        return None


class _ObservedOperation:
    state = OperationState.SUCCEEDED
    dead_letter_id = None
    last_attempt_at = None
    kind = OperationKind.IMAGE_GENERATION
    attempt_count = 1


class _RecordingOperationWorker:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def execute(self, *, workspace_id: str, operation_id: str) -> _ObservedOperation:
        self.calls.append((workspace_id, operation_id))
        return _ObservedOperation()


def _generation_event() -> OutboxEvent:
    payload = GenerationCandidateRequestedPayload(
        workspace_id="catalog-workspace",
        workflow_id="018f5f4d-7c11-7d11-8a11-111111111111",
        generation_batch_id="018f5f4d-7c11-7d11-8a11-222222222222",
        candidate_slot_id="018f5f4d-7c11-7d11-8a11-333333333333",
        operation_id="018f5f4d-7c11-7d11-8a11-444444444444",
        operation_kind=OperationKind.IMAGE_GENERATION,
    )
    envelope = EventEnvelope.create(
        event_type=GENERATION_CANDIDATE_REQUESTED_V1.event_type.value,
        aggregate_type="generation-batch",
        aggregate_id=payload.generation_batch_id,
        aggregate_version=1,
        trace_id=payload.operation_id,
        payload=payload.model_dump(mode="json"),
    )
    return OutboxEvent(
        envelope=envelope,
        available_at=envelope.occurred_at,
        workspace_id=payload.workspace_id,
    )


def _generation_ready_event() -> OutboxEvent:
    payload = GenerationCandidateReadyPayload(
        workspace_id="catalog-workspace",
        workflow_id="018f5f4d-7c11-7d11-8a11-111111111111",
        generation_batch_id="018f5f4d-7c11-7d11-8a11-222222222222",
        candidate_slot_id="018f5f4d-7c11-7d11-8a11-333333333333",
        candidate_image_id="018f5f4d-7c11-7d11-8a11-555555555555",
        asset_version_id="018f5f4d-7c11-7d11-8a11-666666666666",
        operation_id="018f5f4d-7c11-7d11-8a11-444444444444",
        usage_record_id="018f5f4d-7c11-7d11-8a11-777777777777",
    )
    envelope = EventEnvelope.create(
        event_type=GENERATION_CANDIDATE_READY_V1.event_type.value,
        aggregate_type="generation-batch",
        aggregate_id=payload.generation_batch_id,
        aggregate_version=1,
        trace_id=payload.operation_id,
        payload=payload.model_dump(mode="json"),
    )
    return OutboxEvent(
        envelope=envelope,
        available_at=envelope.occurred_at,
        workspace_id=payload.workspace_id,
    )


def test_generation_candidate_uses_isolated_existing_worker_route(monkeypatch) -> None:
    assert GENERATION_CANDIDATE_REQUESTED_V1.queue is EventQueue.GENERATION

    settings = Settings(
        environment="ci",
        worker_queues=["commercevision.generation"],
        worker_required_operation_kinds=[OperationKind.IMAGE_GENERATION],
    )
    assert settings.configured_worker_queues == (settings.generation_queue_name,)
    assert settings.worker_requires_object_storage is True

    monkeypatch.setattr(
        "commercevision_worker.runtime.build_object_storage",
        lambda _settings: _ClosableStorage(),
    )
    runtime = WorkerRuntime.build(
        settings,
        operation_executors={OperationKind.IMAGE_GENERATION: _NeverCalledExecutor()},
    )
    operation_worker = _RecordingOperationWorker()
    runtime.operation_worker = cast(Any, operation_worker)
    event = _generation_event()

    try:
        handler = runtime.event_router.resolve(event.envelope)
        assert handler.__self__ is runtime
        assert handler.__name__ == "_handle_generation_candidate"

        handler(event)
        assert operation_worker.calls == [
            ("catalog-workspace", "018f5f4d-7c11-7d11-8a11-444444444444")
        ]

        for invalid_event in (
            replace(event, workspace_id="other-workspace"),
            replace(
                event,
                envelope=replace(
                    event.envelope, aggregate_id="018f5f4d-7c11-7d11-8a11-555555555555"
                ),
            ),
        ):
            with pytest.raises(EventRoutingError):
                handler(invalid_event)
        assert len(operation_worker.calls) == 1
    finally:
        runtime.close()


def test_generation_ready_route_uses_mysql_claim_to_start_evaluation(monkeypatch) -> None:
    settings = Settings(
        environment="ci",
        worker_queues=["commercevision.generation"],
        worker_required_operation_kinds=[OperationKind.IMAGE_GENERATION],
    )
    monkeypatch.setattr(
        "commercevision_worker.runtime.build_object_storage",
        lambda _settings: _ClosableStorage(),
    )
    runtime = WorkerRuntime.build(
        settings,
        operation_executors={OperationKind.IMAGE_GENERATION: _NeverCalledExecutor()},
    )
    event = _generation_ready_event()

    class Authority:
        def __init__(self) -> None:
            self.calls: list[dict[str, str]] = []

        def claim_ready_batch(self, **kwargs: str) -> GenerationWorkflowContinuationClaim:
            self.calls.append(kwargs)
            return GenerationWorkflowContinuationClaim(
                workspace_id="catalog-workspace",
                workflow_id="018f5f4d-7c11-7d11-8a11-111111111111",
                workflow_version=7,
                actor_id="generation-owner",
                input_data={"fixture_config": {"quality": "production"}},
                generation_batch_id="018f5f4d-7c11-7d11-8a11-222222222222",
                creative_plan_id="018f5f4d-7c11-7d11-8a11-888888888888",
                creative_plan_version_id="018f5f4d-7c11-7d11-8a11-999999999999",
                creative_plan_version=3,
                generation_iteration=2,
                candidate_refs=("mysql://candidate-images/018f5f4d-7c11-7d11-8a11-555555555555",),
            )

    class Agent:
        def __init__(self) -> None:
            self.states: list[object] = []

        def run(self, *, initial_state: object) -> dict[str, object]:
            self.states.append(initial_state)
            return {}

    authority = Authority()
    agent = Agent()
    runtime.generation_continuations = cast(Any, authority)
    runtime.agent = cast(Any, agent)
    try:
        handler = runtime.event_router.resolve(event.envelope)
        assert handler.__name__ == "_handle_generation_candidate_ready"

        handler(event)

        assert authority.calls == [event.envelope.payload]
        assert len(agent.states) == 1
        state = agent.states[0]
        assert state.current_node == "evaluate_results"
        assert state.initial_entry_reason == "GENERATION_CANDIDATES_READY"
        assert state.generation_iteration == 2
        assert state.candidate_refs == [
            "mysql://candidate-images/018f5f4d-7c11-7d11-8a11-555555555555"
        ]
    finally:
        runtime.close()
