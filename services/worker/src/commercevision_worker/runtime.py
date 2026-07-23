"""Worker dependency composition and Outbox event processing."""

from __future__ import annotations

import socket
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from commercevision_agent_core import (
    FixtureAgentRuntime,
    FixtureAgentState,
    build_fixture_graph,
)
from commercevision_application import (
    DurableNodeLifecycle,
    DurableOperationWorker,
    EventRoutingError,
    EventRoutingRegistry,
    InboxCoordinator,
    OperationApplicationService,
    OperationExecutionBoundary,
    OperationExecutor,
    OperationExecutorRegistry,
    OperationReconciliationPolicy,
    OperationRetryPolicy,
    build_event_routing_registry,
)
from commercevision_contracts import Settings
from commercevision_contracts.events import (
    DEAD_LETTER_REPLAY_RECORDED_V1,
    OPERATION_RECOVERY_REQUESTED_V1,
    WORKFLOW_CANCELLED_V1,
    WORKFLOW_FAILED_V1,
    WORKFLOW_HUMAN_INPUT_RECEIVED_V1,
    WORKFLOW_HUMAN_INPUT_REQUIRED_V1,
    WORKFLOW_NODE_COMPLETED_V1,
    WORKFLOW_NODE_STARTED_V1,
    WORKFLOW_RESUME_REQUESTED_V1,
    WORKFLOW_RUN_REQUESTED_V1,
    EventQueue,
    EventType,
)
from commercevision_domain import LeaseConflictError, NotFoundError, OperationKind
from commercevision_domain.messaging import OutboxEvent
from commercevision_persistence import (
    Database,
    MySQLCheckpointSaver,
    SqlAlchemyOperationUnitOfWork,
    SqlAlchemyUnitOfWork,
    create_database,
    is_unit_of_work_active,
)
from commercevision_tool_runtime import (
    FixtureImageTool,
    ToolDefinition,
    ToolExecutionGateway,
    ToolRegistry,
)
from commercevision_tool_runtime.policy import ToolPolicy


@dataclass(slots=True)
class WorkerRuntime:
    database: Database
    settings: Settings
    worker_id: str
    inbox: InboxCoordinator
    agent: FixtureAgentRuntime
    event_router: EventRoutingRegistry
    operation_worker: DurableOperationWorker
    operation_executors: OperationExecutorRegistry

    @classmethod
    def build(
        cls,
        settings: Settings,
        *,
        operation_executors: Mapping[OperationKind, OperationExecutor] | None = None,
    ) -> WorkerRuntime:
        configured_executors = dict(operation_executors or {})
        missing_executors = set(settings.worker_required_operation_kinds).difference(
            configured_executors
        )
        if missing_executors:
            missing = ", ".join(sorted(kind.value for kind in missing_executors))
            raise RuntimeError(f"required operation executors are unavailable: {missing}")
        database = create_database(settings)

        def uow_factory() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(database.session_factory)

        def operation_uow_factory() -> SqlAlchemyOperationUnitOfWork:
            return SqlAlchemyOperationUnitOfWork(database.session_factory)

        worker_id = f"{socket.gethostname()}:{settings.service_name}"
        lifecycle = DurableNodeLifecycle(
            uow_factory=uow_factory,
            lease_duration=timedelta(seconds=settings.workflow_step_lease_seconds),
        )
        fixture_tool = FixtureImageTool()
        registry = ToolRegistry(
            [
                ToolDefinition(
                    name=fixture_tool.name,
                    version=fixture_tool.version,
                    description="Deterministic Phase 1 fixture image generation",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "count": {"type": "integer", "minimum": 1, "maximum": 10},
                            "delay_seconds": {
                                "type": "number",
                                "minimum": 0,
                                "maximum": 120,
                            },
                        },
                    },
                    output_schema={
                        "type": "object",
                        "required": ["candidates"],
                    },
                    implementation=fixture_tool,
                )
            ]
        )
        gateway = ToolExecutionGateway(
            registry=registry,
            policy=ToolPolicy(
                version="tool-policy-v1",
                allowed_tools=frozenset({fixture_tool.name}),
                transaction_active=is_unit_of_work_active,
            ),
        )
        checkpointer = MySQLCheckpointSaver(
            database.session_factory,
            retention=timedelta(hours=settings.workflow_retention_hours),
        )
        graph = build_fixture_graph(
            lifecycle=lifecycle,
            tool_gateway=gateway,
            checkpointer=checkpointer,
            worker_id=worker_id,
        )
        executor_registry = OperationExecutorRegistry()
        for kind, executor in configured_executors.items():
            executor_registry.register(kind=kind, executor=executor)
        operation_worker = DurableOperationWorker(
            operations=OperationApplicationService(
                uow_factory=operation_uow_factory,
                execution_max_elapsed=timedelta(
                    seconds=settings.operation_retry_max_elapsed_seconds
                ),
            ),
            execution=OperationExecutionBoundary(
                executor=executor_registry,
                transaction_active=is_unit_of_work_active,
            ),
            owner=worker_id,
            lease_duration=timedelta(seconds=settings.workflow_step_lease_seconds),
            retry_policy=OperationRetryPolicy(
                initial_delay=timedelta(seconds=settings.operation_retry_initial_seconds),
                maximum_delay=timedelta(seconds=settings.operation_retry_max_seconds),
                maximum_elapsed=timedelta(seconds=settings.operation_retry_max_elapsed_seconds),
            ),
            reconciliation_policy=OperationReconciliationPolicy(
                initial_delay=timedelta(seconds=settings.operation_reconciliation_initial_seconds),
                maximum_delay=timedelta(seconds=settings.operation_reconciliation_max_seconds),
                maximum_elapsed=timedelta(
                    seconds=settings.operation_reconciliation_max_elapsed_seconds
                ),
            ),
        )
        runtime = cls(
            database=database,
            settings=settings,
            worker_id=worker_id,
            inbox=InboxCoordinator(
                uow_factory=uow_factory,
                consumer=settings.worker_consumer_name,
                owner=worker_id,
                lease_duration=timedelta(seconds=settings.workflow_step_lease_seconds),
                max_attempts=settings.workflow_message_max_attempts,
                retry_initial=timedelta(seconds=settings.worker_message_retry_initial_seconds),
                retry_max=timedelta(seconds=settings.worker_message_retry_max_seconds),
            ),
            agent=FixtureAgentRuntime(graph, checkpointer),
            operation_worker=operation_worker,
            operation_executors=executor_registry,
            event_router=build_event_routing_registry(
                {
                    EventQueue.WORKFLOW: settings.workflow_queue_name,
                    EventQueue.ASSET: settings.asset_queue_name,
                    EventQueue.INDEX: settings.index_queue_name,
                    EventQueue.MAINTENANCE: settings.maintenance_queue_name,
                }
            ),
        )
        runtime.event_router.register_handler(
            contract=WORKFLOW_RUN_REQUESTED_V1,
            handler=runtime._handle_workflow_event,
        )
        runtime.event_router.register_handler(
            contract=WORKFLOW_RESUME_REQUESTED_V1,
            handler=runtime._handle_workflow_event,
        )
        for contract in (
            WORKFLOW_NODE_STARTED_V1,
            WORKFLOW_NODE_COMPLETED_V1,
            WORKFLOW_HUMAN_INPUT_REQUIRED_V1,
            WORKFLOW_HUMAN_INPUT_RECEIVED_V1,
            WORKFLOW_FAILED_V1,
            WORKFLOW_CANCELLED_V1,
        ):
            runtime.event_router.register_handler(
                contract=contract,
                handler=runtime._observe_workflow_event,
            )
        runtime.event_router.register_handler(
            contract=OPERATION_RECOVERY_REQUESTED_V1,
            handler=runtime._handle_operation_recovery,
        )
        runtime.event_router.register_handler(
            contract=DEAD_LETTER_REPLAY_RECORDED_V1,
            handler=runtime._observe_replay_event,
        )
        return runtime

    def operation_executor_readiness(self) -> dict[str, object]:
        required = frozenset(self.settings.worker_required_operation_kinds)
        missing = self.operation_executors.missing(required)
        return {
            "ready": not missing,
            "required_kinds": sorted(kind.value for kind in required),
            "registered_kinds": sorted(
                kind.value for kind in self.operation_executors.registered_kinds
            ),
            "missing_kinds": sorted(kind.value for kind in missing),
        }

    def process_event(self, event_id: str) -> str:
        claim, event = self.inbox.claim(event_id)
        if claim.already_processed:
            return "duplicate"
        if claim.dead:
            return "dead-lettered"
        if claim.retry_not_ready:
            return "retry-not-ready"
        if not claim.should_process or claim.lease_token is None:
            raise LeaseConflictError(f"message {event_id} is being processed")

        try:
            self.event_router.resolve(event.envelope)(event)
        except EventRoutingError as exc:
            self.inbox.mark_permanent_failed(
                event_id,
                claim.lease_token,
                exc,
                delivery_attempt=claim.delivery_attempt,
            )
            return "dead-lettered"
        except Exception as exc:
            self.inbox.schedule_retry(
                event_id,
                claim.lease_token,
                exc,
                delivery_attempt=claim.delivery_attempt,
            )
            return "retry-scheduled"
        self.inbox.mark_processed(event_id, claim.lease_token)
        return "processed"

    def close(self) -> None:
        self.database.dispose()

    def _load_initial_state(self, workflow_id: str, *, trace_id: str) -> FixtureAgentState:
        with SqlAlchemyUnitOfWork(self.database.session_factory) as uow:
            workflow = uow.workflows.get(workflow_id)
            if workflow is None:
                raise NotFoundError(f"workflow {workflow_id} was not found")
            input_data: dict[str, Any] = workflow.input_data
        fixture_config = input_data.get("fixture_config", input_data)
        return FixtureAgentState(
            workflow_id=workflow.id,
            workflow_version=workflow.version,
            workspace_id=workflow.workspace_id,
            actor_id=workflow.created_by,
            trace_id=trace_id,
            input_ref=f"mysql://workflows/{workflow.id}/input",
            fixture_config=fixture_config,
            current_node=workflow.current_node or "validate_input",
        )

    def _handle_workflow_event(self, event: OutboxEvent) -> None:
        initial_state = self._load_initial_state(
            event.envelope.aggregate_id,
            trace_id=event.envelope.trace_id,
        )
        resume_payload = (
            event.envelope.payload
            if event.envelope.event_type == EventType.WORKFLOW_RESUME_REQUESTED
            else None
        )
        try:
            self.agent.run(
                initial_state=initial_state,
                resume_payload=resume_payload,
            )
        except Exception:
            if self._workflow_outcome_was_durably_recorded(event):
                return
            raise

    def _workflow_outcome_was_durably_recorded(self, event: OutboxEvent) -> bool:
        with SqlAlchemyUnitOfWork(self.database.session_factory) as uow:
            workflow = uow.workflows.get(event.envelope.aggregate_id)
            if workflow is None:
                return False
            if workflow.status.terminal:
                return True
            return uow.outbox.has_unpublished(
                aggregate_id=workflow.id,
                event_type=EventType.WORKFLOW_RUN_REQUESTED.value,
                exclude_event_id=event.envelope.event_id,
            )

    def _handle_operation_recovery(self, event: OutboxEvent) -> None:
        self.operation_worker.handle_recovery_event(event)

    @staticmethod
    def _observe_replay_event(_event: OutboxEvent) -> None:
        """Acknowledge immutable replay audit observations through the Inbox."""

    @staticmethod
    def _observe_workflow_event(_event: OutboxEvent) -> None:
        """Acknowledge a durable Phase 1 notification through the Inbox audit trail."""
