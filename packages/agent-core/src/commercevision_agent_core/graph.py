"""Deterministic fixture graph proving durable Agent runtime semantics."""

from __future__ import annotations

from collections.abc import AsyncIterator, Collection, Iterator, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
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
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .ports import NodeLifecyclePort
from .state import FixtureAgentState

StateUpdate = dict[str, Any]

_SAFE_INITIAL_ENTRY_NODES = (
    "validate_input",
    "retrieve_references",
)
_CHECKPOINT_GENERATION_KEY = "commercevision_checkpoint_generation"
_CHECKPOINT_NAMESPACE_SEPARATOR = "|"
_THREAD_WIDE_HISTORY_KEY = "__commercevision_thread_wide_checkpoint_history"


@dataclass(frozen=True, slots=True)
class _ProductBriefContinuation:
    workspace_id: str
    product_brief_version_id: str
    product_brief_version_number: int
    approval_id: str | None


@dataclass(frozen=True, slots=True)
class _PreclaimedNodeAuthority:
    step_id: str
    lease_token: str | None


_PRECLAIMED_NODE_AUTHORITY: ContextVar[_PreclaimedNodeAuthority | None] = ContextVar(
    "commercevision_preclaimed_node_authority",
    default=None,
)


class _GenerationCheckpointSaver(BaseCheckpointSaver[Any]):
    """Map a root graph's logical namespace into its continuation generation."""

    def __init__(self, delegate: BaseCheckpointSaver[Any]) -> None:
        super().__init__(serde=delegate.serde)
        self.delegate = delegate

    @property
    def config_specs(self) -> list[Any]:
        return self.delegate.config_specs

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        generation = self._generation(config)
        saved = self.delegate.get_tuple(self._physical_config(config, generation))
        return self._logical_tuple(saved, generation)

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        generation = self._generation(config) if config is not None else ""
        thread_wide = self._is_thread_wide_list(config, generation)
        physical_config = (
            self._physical_list_config(config, generation) if config is not None else None
        )
        physical_before = (
            self._physical_list_config(before, generation) if before is not None else None
        )
        for saved in self.delegate.list(
            physical_config,
            filter=filter,
            before=physical_before,
            limit=limit,
        ):
            logical_generation = (
                self._generation_from_physical_config(saved.config) if thread_wide else generation
            )
            logical = self._logical_tuple(saved, logical_generation)
            if logical is not None:
                yield logical

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        generation = self._generation(config)
        saved_config = self.delegate.put(
            self._physical_config(config, generation),
            checkpoint,
            metadata,
            new_versions,
        )
        return self._logical_config(saved_config, generation)

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        generation = self._generation(config)
        self.delegate.put_writes(
            self._physical_config(config, generation),
            writes,
            task_id,
            task_path,
        )

    def delete_thread(self, thread_id: str) -> None:
        self.delegate.delete_thread(thread_id)

    def delete_for_runs(self, run_ids: Sequence[str]) -> None:
        self.delegate.delete_for_runs(run_ids)

    def copy_thread(self, source_thread_id: str, target_thread_id: str) -> None:
        self.delegate.copy_thread(source_thread_id, target_thread_id)

    def prune(
        self,
        thread_ids: Sequence[str],
        *,
        strategy: str = "keep_latest",
    ) -> None:
        self.delegate.prune(thread_ids, strategy=strategy)

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        generation = self._generation(config)
        saved = await self.delegate.aget_tuple(self._physical_config(config, generation))
        return self._logical_tuple(saved, generation)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        generation = self._generation(config) if config is not None else ""
        thread_wide = self._is_thread_wide_list(config, generation)
        physical_config = (
            self._physical_list_config(config, generation) if config is not None else None
        )
        physical_before = (
            self._physical_list_config(before, generation) if before is not None else None
        )
        async for saved in self.delegate.alist(
            physical_config,
            filter=filter,
            before=physical_before,
            limit=limit,
        ):
            logical_generation = (
                self._generation_from_physical_config(saved.config) if thread_wide else generation
            )
            logical = self._logical_tuple(saved, logical_generation)
            if logical is not None:
                yield logical

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        generation = self._generation(config)
        saved_config = await self.delegate.aput(
            self._physical_config(config, generation),
            checkpoint,
            metadata,
            new_versions,
        )
        return self._logical_config(saved_config, generation)

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        generation = self._generation(config)
        await self.delegate.aput_writes(
            self._physical_config(config, generation),
            writes,
            task_id,
            task_path,
        )

    async def adelete_thread(self, thread_id: str) -> None:
        await self.delegate.adelete_thread(thread_id)

    async def adelete_for_runs(self, run_ids: Sequence[str]) -> None:
        await self.delegate.adelete_for_runs(run_ids)

    async def acopy_thread(self, source_thread_id: str, target_thread_id: str) -> None:
        await self.delegate.acopy_thread(source_thread_id, target_thread_id)

    async def aprune(
        self,
        thread_ids: Sequence[str],
        *,
        strategy: str = "keep_latest",
    ) -> None:
        await self.delegate.aprune(thread_ids, strategy=strategy)

    def get_next_version(self, current: Any | None, channel: None) -> Any:
        return self.delegate.get_next_version(current, channel)

    def with_allowlist(
        self,
        extra_allowlist: Collection[tuple[str, ...]],
    ) -> BaseCheckpointSaver[Any]:
        return _GenerationCheckpointSaver(self.delegate.with_allowlist(extra_allowlist))

    @staticmethod
    def _generation(config: RunnableConfig) -> str:
        return str(config["configurable"].get(_CHECKPOINT_GENERATION_KEY, ""))

    @classmethod
    def _physical_config(
        cls,
        config: RunnableConfig,
        generation: str,
    ) -> RunnableConfig:
        configurable = dict(config["configurable"])
        logical_namespace = str(configurable.get("checkpoint_ns", ""))
        if generation and logical_namespace:
            physical_namespace = f"{generation}{_CHECKPOINT_NAMESPACE_SEPARATOR}{logical_namespace}"
        else:
            physical_namespace = generation or logical_namespace
        configurable["checkpoint_ns"] = physical_namespace
        return cast(RunnableConfig, {**config, "configurable": configurable})

    @classmethod
    def _physical_list_config(
        cls,
        config: RunnableConfig,
        fallback_generation: str,
    ) -> RunnableConfig:
        configurable = dict(config["configurable"])
        generation = cls._generation(config) or fallback_generation
        thread_wide = bool(configurable.pop(_THREAD_WIDE_HISTORY_KEY, False))
        config_without_marker = cast(
            RunnableConfig,
            {**config, "configurable": configurable},
        )
        if thread_wide and not generation:
            configurable.pop("checkpoint_ns", None)
            return config_without_marker
        if generation or "checkpoint_ns" in configurable:
            return cls._physical_config(config_without_marker, generation)
        return config_without_marker

    @staticmethod
    def _is_thread_wide_list(
        config: RunnableConfig | None,
        generation: str,
    ) -> bool:
        if generation:
            return False
        if config is None:
            return True
        configurable = config["configurable"]
        return bool(configurable.get(_THREAD_WIDE_HISTORY_KEY)) or (
            "checkpoint_ns" not in configurable
        )

    @staticmethod
    def _generation_from_physical_config(config: RunnableConfig) -> str:
        namespace = str(config["configurable"].get("checkpoint_ns", ""))
        root_namespace = namespace.split(_CHECKPOINT_NAMESPACE_SEPARATOR, maxsplit=1)[0]
        return root_namespace if root_namespace.startswith("product-brief:v1:") else ""

    @classmethod
    def _logical_config(
        cls,
        config: RunnableConfig,
        generation: str,
    ) -> RunnableConfig:
        configurable = dict(config["configurable"])
        physical_namespace = str(configurable.get("checkpoint_ns", ""))
        if not generation:
            logical_namespace = physical_namespace
        elif physical_namespace == generation:
            logical_namespace = ""
        else:
            prefix = f"{generation}{_CHECKPOINT_NAMESPACE_SEPARATOR}"
            if not physical_namespace.startswith(prefix):
                raise ValueError("checkpoint namespace belongs to another continuation generation")
            logical_namespace = physical_namespace.removeprefix(prefix)
        configurable["checkpoint_ns"] = logical_namespace
        if generation:
            configurable[_CHECKPOINT_GENERATION_KEY] = generation
        return cast(RunnableConfig, {**config, "configurable": configurable})

    @classmethod
    def _logical_tuple(
        cls,
        saved: CheckpointTuple | None,
        generation: str,
    ) -> CheckpointTuple | None:
        if saved is None:
            return None
        return CheckpointTuple(
            config=cls._logical_config(saved.config, generation),
            checkpoint=saved.checkpoint,
            metadata=saved.metadata,
            parent_config=(
                cls._logical_config(saved.parent_config, generation)
                if saved.parent_config is not None
                else None
            ),
            pending_writes=saved.pending_writes,
        )


class _GenerationAwareGraph:
    """Preserve caller intent when LangGraph normalizes root history configs."""

    def __init__(self, graph: Any) -> None:
        self._graph = graph

    @property
    def checkpointer(self) -> BaseCheckpointSaver[Any] | None:
        return self._graph.checkpointer

    @checkpointer.setter
    def checkpointer(self, value: BaseCheckpointSaver[Any]) -> None:
        self._graph.checkpointer = value

    def get_state_history(
        self,
        config: RunnableConfig,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[Any]:
        return self._graph.get_state_history(
            self._history_config(config),
            filter=filter,
            before=before,
            limit=limit,
        )

    def aget_state_history(
        self,
        config: RunnableConfig,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[Any]:
        return self._graph.aget_state_history(
            self._history_config(config),
            filter=filter,
            before=before,
            limit=limit,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._graph, name)

    @staticmethod
    def _history_config(config: RunnableConfig) -> RunnableConfig:
        configurable = config["configurable"]
        if "checkpoint_ns" in configurable:
            return config
        return cast(
            RunnableConfig,
            {
                **config,
                "configurable": {
                    **configurable,
                    _THREAD_WIDE_HISTORY_KEY: True,
                },
            },
        )


def _state_values(state: FixtureAgentState | dict[str, Any]) -> FixtureAgentState:
    return (
        state if isinstance(state, FixtureAgentState) else FixtureAgentState.model_validate(state)
    )


def _initial_entry_node(state: FixtureAgentState | dict[str, Any]) -> str:
    values = _state_values(state)
    current_node = values.current_node
    if current_node not in _SAFE_INITIAL_ENTRY_NODES:
        raise ValueError(f"workflow node {current_node} requires an existing durable checkpoint")
    if current_node == "retrieve_references" and (
        values.initial_entry_reason != "PRODUCT_BRIEF_CONFIRMED"
        or values.product_brief_ref is None
        or not values.product_brief_ref.startswith("mysql://product-brief-versions/")
        or values.product_brief_version_id is None
        or values.product_brief_version_number is None
        or values.initial_step_id is None
    ):
        raise ValueError(
            "retrieve_references requires an exact confirmed ProductBrief "
            "when no durable checkpoint exists"
        )
    return current_node


def _product_brief_continuation(
    state: FixtureAgentState,
) -> _ProductBriefContinuation | None:
    if state.product_brief_version_id is None:
        return None
    if state.product_brief_version_number is None:
        raise ValueError("ProductBrief continuation version number is unavailable")
    return _ProductBriefContinuation(
        workspace_id=state.workspace_id,
        product_brief_version_id=state.product_brief_version_id,
        product_brief_version_number=state.product_brief_version_number,
        approval_id=state.product_brief_approval_id,
    )


def _checkpoint_namespace(state: FixtureAgentState) -> str:
    if state.initial_entry_reason != "PRODUCT_BRIEF_CONFIRMED":
        return ""
    if state.product_brief_checkpoint_generation is None:
        raise ValueError("confirmed ProductBrief checkpoint generation is unavailable")
    return state.product_brief_checkpoint_generation


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
            product_brief_continuation=_product_brief_continuation(state),
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
                expected_workflow_version=payload.resulting_workflow_version,
                product_brief_continuation=_product_brief_continuation(state),
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
        continuation = _product_brief_continuation(state)
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
            product_brief_continuation=continuation,
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
            lease_token=lease_token,
            expected_workflow_version=claim.workflow_version,
            product_brief_continuation=continuation,
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
                    workflow_id=state.workflow_id,
                    step_id=claim.step_id,
                    lease_token=lease_token,
                    idempotency_key=idempotency_key,
                    result=result,
                    expected_workflow_version=claim.workflow_version,
                    product_brief_continuation=continuation,
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
                expected_workflow_version=claim.workflow_version,
                product_brief_continuation=continuation,
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
                attempt_id=attempt.attempt_id,
                lease_token=lease_token,
                trace_id=state.trace_id,
                error=exc,
                retryable=bool(getattr(exc, "retryable", False)),
                retry_delay=timedelta(seconds=2),
                expected_workflow_version=claim.workflow_version,
                product_brief_continuation=continuation,
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
            product_brief_continuation=_product_brief_continuation(state),
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
                expected_workflow_version=payload.resulting_workflow_version,
                product_brief_continuation=_product_brief_continuation(state),
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
        if node_name == "retrieve_references" and state.initial_step_id is not None:
            authority = _PRECLAIMED_NODE_AUTHORITY.get()
            if authority is None or authority.step_id != state.initial_step_id:
                raise ValueError("preclaimed retrieval authority is unavailable")
            step_id = state.initial_step_id
            lease_token = authority.lease_token
            claim_workflow_version = state.workflow_version
        else:
            claim = self.lifecycle.begin_node(
                workflow_id=state.workflow_id,
                expected_workflow_version=state.workflow_version,
                step_key=step_key,
                step_type=step_type,
                running_state=running_state,
                node_name=node_name,
                lease_owner=self.worker_id,
                trace_id=state.trace_id,
                product_brief_continuation=_product_brief_continuation(state),
            )
            if claim.already_completed:
                return {
                    **(claim.output_data or {}),
                    "workflow_version": claim.workflow_version,
                    "current_node": next_node,
                }
            step_id = claim.step_id
            lease_token = cast(str, claim.lease_token)
            claim_workflow_version = claim.workflow_version
        version = self.lifecycle.complete_node(
            workflow_id=state.workflow_id,
            step_id=step_id,
            lease_token=lease_token,
            target_state=target_state,
            next_node=next_node,
            trace_id=state.trace_id,
            output_data=output,
            workflow_result=workflow_result,
            expected_workflow_version=claim_workflow_version,
            product_brief_continuation=_product_brief_continuation(state),
        )
        return {
            **output,
            "workflow_version": version,
            "current_node": next_node,
            "initial_step_id": None,
        }

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
    graph.add_conditional_edges(
        START,
        _initial_entry_node,
        {node: node for node in _SAFE_INITIAL_ENTRY_NODES},
    )
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
    return _GenerationAwareGraph(
        graph.compile(
            checkpointer=_GenerationCheckpointSaver(checkpointer),
            name="commercevision-fixture-agent-v1",
        )
    )


class FixtureAgentRuntime:
    def __init__(self, graph: Any, checkpointer: BaseCheckpointSaver[str]) -> None:
        self._graph = (
            graph if isinstance(graph, _GenerationAwareGraph) else _GenerationAwareGraph(graph)
        )
        self._checkpointer = checkpointer
        graph_checkpointer = self._graph.checkpointer
        if isinstance(graph_checkpointer, _GenerationCheckpointSaver):
            if graph_checkpointer.delegate is not checkpointer:
                raise ValueError("graph and runtime must share the same checkpoint saver")
        else:
            self._graph.checkpointer = _GenerationCheckpointSaver(checkpointer)

    def run(
        self,
        *,
        initial_state: FixtureAgentState,
        resume_payload: dict[str, Any] | None = None,
        preclaimed_step_id: str | None = None,
        preclaimed_lease_token: str | None = None,
    ) -> dict[str, Any]:
        if preclaimed_step_id is None and preclaimed_lease_token is not None:
            raise ValueError("preclaimed lease token requires a step identity")
        preclaimed_authority = (
            _PreclaimedNodeAuthority(
                step_id=preclaimed_step_id,
                lease_token=preclaimed_lease_token,
            )
            if preclaimed_step_id is not None
            else None
        )
        checkpoint_namespace = (
            self._resume_checkpoint_namespace(
                initial_state=initial_state,
                resume_payload=resume_payload,
            )
            if resume_payload is not None
            else _checkpoint_namespace(initial_state)
        )
        configurable: dict[str, Any] = {
            "thread_id": initial_state.workflow_id,
            "checkpoint_ns": "",
            "workflow_id": initial_state.workflow_id,
            "workflow_version": initial_state.workflow_version,
        }
        if checkpoint_namespace:
            configurable[_CHECKPOINT_GENERATION_KEY] = checkpoint_namespace
        config: RunnableConfig = {
            "configurable": configurable,
            "metadata": {
                "workflow_id": initial_state.workflow_id,
                "trace_id": initial_state.trace_id,
                "graph_version": "fixture-agent-v1",
            },
        }
        lookup_config = cast(
            RunnableConfig,
            {
                **config,
                "configurable": {
                    **configurable,
                    "checkpoint_ns": checkpoint_namespace,
                },
            },
        )
        existing = self._checkpointer.get_tuple(lookup_config)
        if resume_payload is not None:
            input_value: FixtureAgentState | Command[Any] | None = Command(resume=resume_payload)
        elif existing is None:
            input_value = initial_state
        else:
            input_value = None
        if initial_state.initial_entry_reason == "PRODUCT_BRIEF_CONFIRMED":
            if (
                preclaimed_authority is None
                or initial_state.initial_step_id != preclaimed_authority.step_id
            ):
                raise ValueError("confirmed ProductBrief runtime preclaim is inconsistent")
            if existing is None and preclaimed_authority.lease_token is None:
                raise ValueError("new ProductBrief checkpoint generation requires a live lease")
        authority_token = _PRECLAIMED_NODE_AUTHORITY.set(preclaimed_authority)
        try:
            result = self._graph.invoke(input_value, config=config)
        finally:
            _PRECLAIMED_NODE_AUTHORITY.reset(authority_token)
        return cast(dict[str, Any], result)

    def _resume_checkpoint_namespace(
        self,
        *,
        initial_state: FixtureAgentState,
        resume_payload: dict[str, Any],
    ) -> str:
        payload = ResumePayload.model_validate(resume_payload)
        if payload.workflow_id != initial_state.workflow_id:
            raise ValueError("resume payload belongs to a different workflow")
        if payload.resulting_workflow_version != initial_state.workflow_version:
            raise ValueError("resume payload does not match the current workflow version")

        latest_namespaces: set[str] = set()
        matching_namespaces: list[str] = []
        thread_config = cast(
            RunnableConfig,
            {"configurable": {"thread_id": initial_state.workflow_id}},
        )
        for saved in self._checkpointer.list(thread_config):
            namespace = str(saved.config["configurable"].get("checkpoint_ns", ""))
            if namespace in latest_namespaces or not self._is_root_checkpoint_namespace(namespace):
                continue
            latest_namespaces.add(namespace)
            if self._checkpoint_matches_resume(
                saved=saved,
                initial_state=initial_state,
                payload=payload,
            ):
                matching_namespaces.append(namespace)

        if not matching_namespaces:
            raise ValueError("resume payload has no matching durable checkpoint")
        if len(matching_namespaces) > 1:
            raise ValueError("resume payload matches multiple checkpoint generations")
        return matching_namespaces[0]

    @staticmethod
    def _is_root_checkpoint_namespace(namespace: str) -> bool:
        return namespace == "" or (
            namespace.startswith("product-brief:v1:")
            and _CHECKPOINT_NAMESPACE_SEPARATOR not in namespace
        )

    @staticmethod
    def _checkpoint_matches_resume(
        *,
        saved: CheckpointTuple,
        initial_state: FixtureAgentState,
        payload: ResumePayload,
    ) -> bool:
        values = saved.checkpoint.get("channel_values", {})
        if (
            values.get("workflow_id") != initial_state.workflow_id
            or values.get("workspace_id") != initial_state.workspace_id
            or values.get("workflow_version") != payload.expected_workflow_version
        ):
            return False
        if payload.approval_type == ApprovalType.CREATIVE_PLAN:
            return (
                values.get("creative_plan_ref") == payload.subject_id
                and values.get("plan_iteration") == payload.subject_version - 1
            )
        if payload.approval_type == ApprovalType.RESULTS:
            return (
                values.get("evaluation_report_ref") == payload.subject_id
                and values.get("generation_iteration") == payload.subject_version - 1
            )
        return (
            values.get("product_brief_version_id") == payload.subject_id
            and values.get("product_brief_version_number") == payload.subject_version
        )
