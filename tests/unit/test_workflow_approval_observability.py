from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from commercevision_application import WorkflowApplicationService
from commercevision_contracts.workflow import ApprovalRequest
from commercevision_domain import (
    ApprovalDecision,
    ApprovalType,
    ConcurrencyError,
    RetentionStatus,
    Workflow,
    WorkflowStatus,
)

NOW = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)
WORKFLOW_ID = "019fac40-0000-7000-8000-000000000101"


class _Repository:
    def __init__(self) -> None:
        self.items: list[object] = []

    def add(self, item: object = None, **values: object) -> None:
        self.items.append(item if item is not None else values)


class _UnitOfWork:
    def __init__(self, workflow: Workflow) -> None:
        self.workflow = workflow
        self.committed = False
        self.approvals = _Repository()
        self.outbox = _Repository()
        self.audit = _Repository()
        self.creative_plans = SimpleNamespace()
        self.workflows = SimpleNamespace(get=self._get_workflow, save=lambda value: None)
        self.idempotency = SimpleNamespace(claim=self._claim, complete=lambda **values: None)

    def __enter__(self) -> _UnitOfWork:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def _get_workflow(self, workflow_id: str, **values: object) -> Workflow | None:
        del values
        return self.workflow if workflow_id == self.workflow.id else None

    @staticmethod
    def _claim(**values: object) -> SimpleNamespace:
        return SimpleNamespace(request_hash=values["request_hash"], status="PENDING")

    def database_now(self) -> datetime:
        return NOW

    def commit(self) -> None:
        self.committed = True


def test_exact_approval_observes_wait_confirmation_and_resume_event_after_commit() -> None:
    workflow = Workflow(
        id=WORKFLOW_ID,
        workspace_id="planning-domain",
        created_by="operator",
        workflow_type="creative-planning",
        status=WorkflowStatus.AWAITING_PRODUCT_CONFIRMATION,
        retention_status=RetentionStatus.ACTIVE,
        current_node="understand_product",
        version=4,
        input_data={},
        result_data=None,
        expires_at=NOW + timedelta(days=1),
        cancellation_requested_at=None,
        created_at=NOW - timedelta(hours=1),
        updated_at=NOW - timedelta(seconds=30),
    )
    unit_of_work = _UnitOfWork(workflow)
    observations: list[tuple[str, dict[str, object], bool]] = []

    class Observer:
        @contextmanager
        def observe(self, **values):
            observations.append(("span", values, unit_of_work.committed))
            yield

        def annotate(self, **values):
            observations.append(("annotate", values, unit_of_work.committed))

        def record_approval(self, **values):
            observations.append(("approval", values, unit_of_work.committed))

        def record_human(self, **values):
            observations.append(("human", values, unit_of_work.committed))

    response = WorkflowApplicationService(
        uow_factory=lambda: unit_of_work,  # type: ignore[arg-type]
        observer=Observer(),  # type: ignore[arg-type]
    ).approve(
        workflow_id=WORKFLOW_ID,
        workspace_id="planning-domain",
        actor_id="reviewer",
        approval_type=ApprovalType.PRODUCT_BRIEF,
        request=ApprovalRequest(
            expected_workflow_version=4,
            subject_id="019fac40-0000-7000-8000-000000000102",
            subject_version=2,
            decision=ApprovalDecision.APPROVE,
        ),
        idempotency_key="approval-operation-secret",
        trace_id="approval-trace-secret",
    )

    assert response.status is WorkflowStatus.RETRIEVING
    assert observations[0] == (
        "span",
        {
            "step": "approval",
            "trace_id": "approval-trace-secret",
            "workflow_id": WORKFLOW_ID,
            "plan_id": None,
            "plan_version": None,
            "operation_id": "approval-operation-secret",
        },
        False,
    )
    assert observations[1][0] == "annotate"
    assert set(observations[1][1]) == {"approval_id", "event_id"}
    assert observations[1][2] is True
    assert observations[2:] == [
        ("approval", {"outcome": "approve"}, True),
        ("human", {"outcome": "approve", "wait_seconds": 30.0}, True),
    ]


def test_stale_approval_is_counted_without_recording_a_confirmation() -> None:
    workflow = Workflow(
        id=WORKFLOW_ID,
        workspace_id="planning-domain",
        created_by="operator",
        workflow_type="creative-planning",
        status=WorkflowStatus.AWAITING_PRODUCT_CONFIRMATION,
        retention_status=RetentionStatus.ACTIVE,
        current_node="understand_product",
        version=4,
        input_data={},
        result_data=None,
        expires_at=NOW + timedelta(days=1),
        cancellation_requested_at=None,
        created_at=NOW - timedelta(hours=1),
        updated_at=NOW - timedelta(seconds=30),
    )
    unit_of_work = _UnitOfWork(workflow)
    observations: list[tuple[str, dict[str, object]]] = []

    class Observer:
        @contextmanager
        def observe(self, **values):
            del values
            yield

        def record_approval(self, **values):
            observations.append(("approval", values))

        def annotate(self, **values):
            observations.append(("annotate", values))

        def record_human(self, **values):
            observations.append(("human", values))

    service = WorkflowApplicationService(
        uow_factory=lambda: unit_of_work,  # type: ignore[arg-type]
        observer=Observer(),  # type: ignore[arg-type]
    )

    with pytest.raises(ConcurrencyError):
        service.approve(
            workflow_id=WORKFLOW_ID,
            workspace_id="planning-domain",
            actor_id="reviewer",
            approval_type=ApprovalType.PRODUCT_BRIEF,
            request=ApprovalRequest(
                expected_workflow_version=3,
                subject_id="019fac40-0000-7000-8000-000000000102",
                subject_version=2,
                decision=ApprovalDecision.APPROVE,
            ),
            idempotency_key="stale-approval-operation",
            trace_id="stale-approval-trace",
        )

    assert observations == [("approval", {"outcome": "stale"})]
