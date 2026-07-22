"""Deterministic fixture graph proving durable Agent runtime semantics."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, cast

from commercevision_contracts.workflow import ResumePayload
from commercevision_domain import (
    ApprovalDecision,
    ApprovalType,
    StepType,
    WorkflowStatus,
)
from commercevision_tool_runtime import (
    ToolExecutionContext,
    ToolExecutionGateway,
    ToolInvocation,
)
from commercevision_tool_runtime.gateway import stable_tool_key
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .ports import NodeLifecyclePort
from .state import FixtureAgentState

StateUpdate = dict[str, Any]


def _state_values(state: FixtureAgentState | dict[str, Any]) -> FixtureAgentState:
    return (
        state if isinstance(state, FixtureAgentState) else FixtureAgentState.model_validate(state)
    )


class FixtureNodes:
    def __init__(
        self,
        *,
        lifecycle: NodeLifecyclePort,
        tool_gateway: ToolExecutionGateway,
        worker_id: str,
    ) -> None:
        self.lifecycle = lifecycle
        self.tool_gateway = tool_gateway
        self.worker_id = worker_id

    def validate_input(self, raw_state: FixtureAgentState) -> StateUpdate:
        state = _state_values(raw_state)
        output = {
            "input_ref": state.input_ref or f"mysql://workflows/{state.workflow_id}/input",
        }
        return self._durable_node(
            state=state,
            step_key="validate_input",
            step_type=StepType.VALIDATE_INPUT,
            running_state=WorkflowStatus.INGESTING,
            target_state=WorkflowStatus.UNDERSTANDING,
            node_name="validate_input",
            next_node="understand_product",
            output=output,
        )

    def understand_product(self, raw_state: FixtureAgentState) -> StateUpdate:
        state = _state_values(raw_state)
        output = {
            "product_brief_ref": f"fixture://product-brief/{state.workflow_id}/v1",
        }
        return self._durable_node(
            state=state,
            step_key="understand_product",
            step_type=StepType.UNDERSTAND_PRODUCT,
            running_state=WorkflowStatus.UNDERSTANDING,
            target_state=WorkflowStatus.RETRIEVING,
            node_name="understand_product",
            next_node="retrieve_references",
            output=output,
        )

    def retrieve_references(self, raw_state: FixtureAgentState) -> StateUpdate:
        state = _state_values(raw_state)
        output = {
            "retrieved_asset_refs": [
                f"fixture://reference/{state.workflow_id}/hero",
                f"fixture://reference/{state.workflow_id}/detail",
            ],
        }
        return self._durable_node(
            state=state,
            step_key="retrieve_references",
            step_type=StepType.RETRIEVE_REFERENCES,
            running_state=WorkflowStatus.RETRIEVING,
            target_state=WorkflowStatus.PLANNING,
            node_name="retrieve_references",
            next_node="create_plan",
            output=output,
        )

    def create_plan(self, raw_state: FixtureAgentState) -> StateUpdate:
        state = _state_values(raw_state)
        output = {
            "creative_plan_ref": (
                f"fixture://creative-plan/{state.workflow_id}/v{state.plan_iteration + 1}"
            ),
            "plan_decision": None,
        }
        return self._durable_node(
            state=state,
            step_key=f"create_plan:{state.plan_iteration}",
            step_type=StepType.CREATE_PLAN,
            running_state=WorkflowStatus.PLANNING,
            target_state=WorkflowStatus.AWAITING_PLAN_APPROVAL,
            node_name="create_plan",
            next_node="approve_plan",
            output=output,
        )

    def approve_plan(self, raw_state: FixtureAgentState) -> StateUpdate:
        state = _state_values(raw_state)
        wait = self.lifecycle.begin_human_wait(
            workflow_id=state.workflow_id,
            expected_workflow_version=state.workflow_version,
            step_key=f"approve_plan:{state.plan_iteration}",
            step_type=StepType.APPROVE_PLAN,
            lease_owner=self.worker_id,
            trace_id=state.trace_id,
        )
        if wait.already_completed:
            payload = ResumePayload.model_validate(wait.output_data)
        else:
            resumed = interrupt(
                {
                    "interrupt_type": ApprovalType.CREATIVE_PLAN.value,
                    "workflow_id": state.workflow_id,
                    "expected_workflow_version": wait.workflow_version,
                    "subject_id": state.creative_plan_ref,
                    "subject_version": state.plan_iteration + 1,
                    "allowed_actions": [
                        ApprovalDecision.APPROVE.value,
                        ApprovalDecision.REJECT.value,
                    ],
                }
            )
            payload = ResumePayload.model_validate(resumed)
            self._validate_resume(
                payload,
                expected_workflow_id=state.workflow_id,
                expected_type=ApprovalType.CREATIVE_PLAN,
                expected_version=wait.workflow_version,
            )
            self.lifecycle.complete_human_wait(
                workflow_id=state.workflow_id,
                step_id=wait.step_id,
                output_data=payload.model_dump(mode="json"),
                trace_id=state.trace_id,
            )
        return {
            "workflow_version": payload.resulting_workflow_version,
            "plan_decision": payload.decision.value,
            "current_node": "approve_plan",
        }

    def revise_plan(self, raw_state: FixtureAgentState) -> StateUpdate:
        state = _state_values(raw_state)
        return {
            "plan_iteration": state.plan_iteration + 1,
            "plan_decision": None,
            "creative_plan_ref": None,
            "current_node": "create_plan",
        }

    def execute_tool(self, raw_state: FixtureAgentState) -> StateUpdate:
        state = _state_values(raw_state)
        step_key = f"execute_tool:{state.generation_iteration}"
        claim = self.lifecycle.begin_node(
            workflow_id=state.workflow_id,
            expected_workflow_version=state.workflow_version,
            step_key=step_key,
            step_type=StepType.EXECUTE_TOOL,
            running_state=WorkflowStatus.GENERATING,
            node_name="execute_tool",
            lease_owner=self.worker_id,
            trace_id=state.trace_id,
            input_data=state.fixture_config,
        )
        if claim.already_completed:
            return {
                **(claim.output_data or {}),
                "workflow_version": claim.workflow_version,
                "current_node": "evaluate_results",
            }
        lease_token = cast(str, claim.lease_token)
        arguments = {
            "count": int(state.fixture_config.get("count", 3)),
            "delay_seconds": float(state.fixture_config.get("delay_seconds", 0)),
            **({"fail": state.fixture_config["fail"]} if state.fixture_config.get("fail") else {}),
        }
        idempotency_key = stable_tool_key(
            workflow_id=state.workflow_id,
            step_key=step_key,
            tool_name="fixture.generate_image",
            arguments=arguments,
        )
        attempt = self.lifecycle.begin_attempt(
            workflow_id=state.workflow_id,
            step_id=claim.step_id,
            idempotency_key=idempotency_key,
            request_data=arguments,
        )
        try:
            if attempt.already_completed:
                result_data = attempt.result_data or {}
            else:
                result = self.tool_gateway.execute(
                    context=ToolExecutionContext(
                        workflow_id=state.workflow_id,
                        workspace_id=state.workspace_id,
                        actor_id=state.actor_id,
                        trace_id=state.trace_id,
                        idempotency_key=idempotency_key,
                        policy_version="tool-policy-v1",
                    ),
                    invocation=ToolInvocation(
                        tool_name="fixture.generate_image",
                        tool_version="1.0.0",
                        arguments=arguments,
                        idempotency_key=idempotency_key,
                        policy_version="tool-policy-v1",
                        reason="Generate deterministic Phase 1 image candidates",
                    ),
                )
                result_data = self.lifecycle.complete_attempt(
                    idempotency_key=idempotency_key,
                    result=result,
                )
            candidates = [str(item["asset_ref"]) for item in result_data.get("candidates", [])]
            output = {
                "candidate_refs": candidates,
                "generation_attempt_refs": [
                    *state.generation_attempt_refs,
                    f"mysql://workflow-attempts/{attempt.attempt_id}",
                ],
            }
            version = self.lifecycle.complete_node(
                workflow_id=state.workflow_id,
                step_id=claim.step_id,
                lease_token=lease_token,
                target_state=WorkflowStatus.EVALUATING,
                next_node="evaluate_results",
                trace_id=state.trace_id,
                output_data=output,
            )
            return {
                **output,
                "workflow_version": version,
                "current_node": "evaluate_results",
            }
        except Exception as exc:
            self.lifecycle.fail_node(
                workflow_id=state.workflow_id,
                step_id=claim.step_id,
                lease_token=lease_token,
                trace_id=state.trace_id,
                error=exc,
                retryable=bool(getattr(exc, "retryable", False)),
                retry_delay=timedelta(seconds=2),
            )
            raise

    def evaluate_results(self, raw_state: FixtureAgentState) -> StateUpdate:
        state = _state_values(raw_state)
        output = {
            "evaluation_report_ref": (
                f"fixture://evaluation/{state.workflow_id}/generation-{state.generation_iteration}"
            ),
            "result_decision": None,
        }
        return self._durable_node(
            state=state,
            step_key=f"evaluate_results:{state.generation_iteration}",
            step_type=StepType.EVALUATE_RESULTS,
            running_state=WorkflowStatus.EVALUATING,
            target_state=WorkflowStatus.AWAITING_RESULT_APPROVAL,
            node_name="evaluate_results",
            next_node="approve_results",
            output=output,
        )

    def approve_results(self, raw_state: FixtureAgentState) -> StateUpdate:
        state = _state_values(raw_state)
        wait = self.lifecycle.begin_human_wait(
            workflow_id=state.workflow_id,
            expected_workflow_version=state.workflow_version,
            step_key=f"approve_results:{state.generation_iteration}",
            step_type=StepType.APPROVE_RESULTS,
            lease_owner=self.worker_id,
            trace_id=state.trace_id,
        )
        if wait.already_completed:
            payload = ResumePayload.model_validate(wait.output_data)
        else:
            resumed = interrupt(
                {
                    "interrupt_type": ApprovalType.RESULTS.value,
                    "workflow_id": state.workflow_id,
                    "expected_workflow_version": wait.workflow_version,
                    "subject_id": state.evaluation_report_ref,
                    "subject_version": state.generation_iteration + 1,
                    "allowed_actions": [
                        ApprovalDecision.APPROVE.value,
                        ApprovalDecision.REGENERATE.value,
                    ],
                }
            )
            payload = ResumePayload.model_validate(resumed)
            self._validate_resume(
                payload,
                expected_workflow_id=state.workflow_id,
                expected_type=ApprovalType.RESULTS,
                expected_version=wait.workflow_version,
            )
            self.lifecycle.complete_human_wait(
                workflow_id=state.workflow_id,
                step_id=wait.step_id,
                output_data=payload.model_dump(mode="json"),
                trace_id=state.trace_id,
            )
        return {
            "workflow_version": payload.resulting_workflow_version,
            "result_decision": payload.decision.value,
            "current_node": "approve_results",
        }

    def prepare_regeneration(self, raw_state: FixtureAgentState) -> StateUpdate:
        state = _state_values(raw_state)
        return {
            "generation_iteration": state.generation_iteration + 1,
            "candidate_refs": [],
            "evaluation_report_ref": None,
            "result_decision": None,
            "current_node": "execute_tool",
        }

    def export(self, raw_state: FixtureAgentState) -> StateUpdate:
        state = _state_values(raw_state)
        output = {
            "export_ref": (
                f"fixture://exports/{state.workflow_id}/generation-{state.generation_iteration}"
            )
        }
        return self._durable_node(
            state=state,
            step_key="export",
            step_type=StepType.EXPORT,
            running_state=WorkflowStatus.EXPORTING,
            target_state=WorkflowStatus.COMPLETED,
            node_name="export",
            next_node="completed",
            output=output,
            workflow_result={
                "export_ref": output["export_ref"],
                "candidate_refs": state.candidate_refs,
                "evaluation_report_ref": state.evaluation_report_ref,
            },
        )

    def _durable_node(
        self,
        *,
        state: FixtureAgentState,
        step_key: str,
        step_type: StepType,
        running_state: WorkflowStatus,
        target_state: WorkflowStatus,
        node_name: str,
        next_node: str,
        output: StateUpdate,
        workflow_result: dict[str, Any] | None = None,
    ) -> StateUpdate:
        claim = self.lifecycle.begin_node(
            workflow_id=state.workflow_id,
            expected_workflow_version=state.workflow_version,
            step_key=step_key,
            step_type=step_type,
            running_state=running_state,
            node_name=node_name,
            lease_owner=self.worker_id,
            trace_id=state.trace_id,
        )
        if claim.already_completed:
            return {
                **(claim.output_data or {}),
                "workflow_version": claim.workflow_version,
                "current_node": next_node,
            }
        version = self.lifecycle.complete_node(
            workflow_id=state.workflow_id,
            step_id=claim.step_id,
            lease_token=cast(str, claim.lease_token),
            target_state=target_state,
            next_node=next_node,
            trace_id=state.trace_id,
            output_data=output,
            workflow_result=workflow_result,
        )
        return {**output, "workflow_version": version, "current_node": next_node}

    @staticmethod
    def _validate_resume(
        payload: ResumePayload,
        *,
        expected_workflow_id: str,
        expected_type: ApprovalType,
        expected_version: int,
    ) -> None:
        if payload.workflow_id != expected_workflow_id:
            raise ValueError("resume payload belongs to a different workflow")
        if payload.approval_type != expected_type:
            raise ValueError(
                f"resume approval type is {payload.approval_type}, expected {expected_type}"
            )
        if payload.expected_workflow_version != expected_version:
            raise ValueError("resume payload does not match the interrupted workflow version")


def build_fixture_graph(
    *,
    lifecycle: NodeLifecyclePort,
    tool_gateway: ToolExecutionGateway,
    checkpointer: BaseCheckpointSaver[str],
    worker_id: str,
) -> Any:
    nodes = FixtureNodes(
        lifecycle=lifecycle,
        tool_gateway=tool_gateway,
        worker_id=worker_id,
    )
    graph = StateGraph(FixtureAgentState)
    graph.add_node("validate_input", nodes.validate_input)
    graph.add_node("understand_product", nodes.understand_product)
    graph.add_node("retrieve_references", nodes.retrieve_references)
    graph.add_node("create_plan", nodes.create_plan)
    graph.add_node("approve_plan", nodes.approve_plan)
    graph.add_node("revise_plan", nodes.revise_plan)
    graph.add_node("execute_tool", nodes.execute_tool)
    graph.add_node("evaluate_results", nodes.evaluate_results)
    graph.add_node("approve_results", nodes.approve_results)
    graph.add_node("prepare_regeneration", nodes.prepare_regeneration)
    graph.add_node("export", nodes.export)
    graph.add_edge(START, "validate_input")
    graph.add_edge("validate_input", "understand_product")
    graph.add_edge("understand_product", "retrieve_references")
    graph.add_edge("retrieve_references", "create_plan")
    graph.add_edge("create_plan", "approve_plan")
    graph.add_conditional_edges(
        "approve_plan",
        lambda state: _state_values(state).plan_decision,
        {
            ApprovalDecision.APPROVE.value: "execute_tool",
            ApprovalDecision.REJECT.value: "revise_plan",
        },
    )
    graph.add_edge("revise_plan", "create_plan")
    graph.add_edge("execute_tool", "evaluate_results")
    graph.add_edge("evaluate_results", "approve_results")
    graph.add_conditional_edges(
        "approve_results",
        lambda state: _state_values(state).result_decision,
        {
            ApprovalDecision.APPROVE.value: "export",
            ApprovalDecision.REGENERATE.value: "prepare_regeneration",
        },
    )
    graph.add_edge("prepare_regeneration", "execute_tool")
    graph.add_edge("export", END)
    return graph.compile(checkpointer=checkpointer, name="commercevision-fixture-agent-v1")


class FixtureAgentRuntime:
    def __init__(self, graph: Any, checkpointer: BaseCheckpointSaver[str]) -> None:
        self._graph = graph
        self._checkpointer = checkpointer

    def run(
        self,
        *,
        initial_state: FixtureAgentState,
        resume_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        config: RunnableConfig = {
            "configurable": {
                "thread_id": initial_state.workflow_id,
                "checkpoint_ns": "",
                "workflow_id": initial_state.workflow_id,
                "workflow_version": initial_state.workflow_version,
            },
            "metadata": {
                "workflow_id": initial_state.workflow_id,
                "trace_id": initial_state.trace_id,
                "graph_version": "fixture-agent-v1",
            },
        }
        existing = self._checkpointer.get_tuple(config)
        if resume_payload is not None:
            input_value: FixtureAgentState | Command[Any] | None = Command(resume=resume_payload)
        elif existing is None:
            input_value = initial_state
        else:
            input_value = None
        result = self._graph.invoke(input_value, config=config)
        return cast(dict[str, Any], result)
