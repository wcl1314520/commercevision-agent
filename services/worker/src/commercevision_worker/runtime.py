"""Worker dependency composition and Outbox event processing."""

from __future__ import annotations

import socket
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from commercevision_agent_core import (
    FixtureAgentRuntime,
    FixtureAgentState,
    build_fixture_graph,
)
from commercevision_application import DurableNodeLifecycle, InboxCoordinator
from commercevision_contracts import Settings
from commercevision_domain import LeaseConflictError, NotFoundError
from commercevision_persistence import (
    Database,
    MySQLCheckpointSaver,
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

    @classmethod
    def build(cls, settings: Settings) -> WorkerRuntime:
        database = create_database(settings)

        def uow_factory() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(database.session_factory)

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
        return cls(
            database=database,
            settings=settings,
            worker_id=worker_id,
            inbox=InboxCoordinator(
                uow_factory=uow_factory,
                consumer=settings.worker_consumer_name,
                owner=worker_id,
                lease_duration=timedelta(seconds=settings.workflow_step_lease_seconds),
                max_attempts=settings.workflow_message_max_attempts,
            ),
            agent=FixtureAgentRuntime(graph, checkpointer),
        )

    def process_event(self, event_id: str) -> str:
        claim, event = self.inbox.claim(event_id)
        if claim.already_processed:
            return "duplicate"
        if claim.dead:
            return "dead-lettered"
        if not claim.should_process or claim.lease_token is None:
            raise LeaseConflictError(f"message {event_id} is being processed")

        try:
            if event.envelope.event_type in {
                "workflow.run.requested",
                "workflow.resume.requested",
            }:
                initial_state = self._load_initial_state(
                    event.envelope.aggregate_id,
                    trace_id=event.envelope.trace_id,
                )
                resume_payload = (
                    event.envelope.payload
                    if event.envelope.event_type == "workflow.resume.requested"
                    else None
                )
                self.agent.run(
                    initial_state=initial_state,
                    resume_payload=resume_payload,
                )
            self.inbox.mark_processed(event_id, claim.lease_token)
        except Exception as exc:
            self.inbox.mark_failed(event_id, claim.lease_token, exc)
            raise
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
