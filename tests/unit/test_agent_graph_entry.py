from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from commercevision_agent_core import (
    FixtureAgentRuntime,
    FixtureAgentState,
    ResumeCheckpointConflictError,
    build_fixture_graph,
)
from commercevision_contracts.workflow import product_brief_checkpoint_generation
from commercevision_domain import ApprovalDecision, ApprovalType
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt


def _product_brief_state(
    *,
    workflow_id: str,
    version_id: str,
    step_id: str,
    lease_token: str,
) -> FixtureAgentState:
    return FixtureAgentState(
        workflow_id=workflow_id,
        workflow_version=7,
        workspace_id="checkpoint-entry",
        actor_id="checkpoint-entry-test",
        trace_id="checkpoint-entry-trace",
        product_brief_ref=f"mysql://product-brief-versions/{version_id}",
        product_brief_version_id=version_id,
        product_brief_version_number=1,
        product_brief_checkpoint_generation=product_brief_checkpoint_generation(
            workspace_id="checkpoint-entry",
            product_brief_version_id=version_id,
            initial_step_id=step_id,
            initial_step_lease_token=lease_token,
        ),
        initial_step_id=step_id,
        current_node="retrieve_references",
        initial_entry_reason="PRODUCT_BRIEF_CONFIRMED",
    )


def _build_crash_probe_graph(
    checkpointer: InMemorySaver,
    seen: list[tuple[str | None, str | None]],
) -> object:
    crashed = False

    def probe(raw_state: FixtureAgentState) -> dict[str, str]:
        nonlocal crashed
        state = FixtureAgentState.model_validate(raw_state)
        seen.append(
            (
                state.product_brief_version_id,
                state.product_brief_checkpoint_generation,
            )
        )
        if not crashed:
            crashed = True
            raise RuntimeError("simulated crash after checkpoint")
        return {"current_node": "completed"}

    graph = StateGraph(FixtureAgentState)
    graph.add_node("probe", probe)
    graph.add_edge(START, "probe")
    graph.add_edge("probe", END)
    return graph.compile(checkpointer=checkpointer)


def _build_human_wait_probe_graph(
    checkpointer: InMemorySaver,
    resumed_decisions: list[str],
    resumed_plan_versions: list[tuple[str | None, int | None]] | None = None,
) -> object:
    def wait_for_approval(raw_state: FixtureAgentState) -> dict[str, str]:
        state = FixtureAgentState.model_validate(raw_state)
        resumed = interrupt(
            {
                "workflow_id": state.workflow_id,
                "expected_workflow_version": state.workflow_version,
                "subject_id": state.creative_plan_ref,
            }
        )
        if resumed_plan_versions is not None:
            resumed_plan_versions.append(
                (state.creative_plan_version_id, state.creative_plan_version)
            )
        resumed_decisions.append(str(resumed["decision"]))
        return {
            "current_node": "completed",
            "plan_decision": str(resumed["decision"]),
        }

    graph = StateGraph(FixtureAgentState)
    graph.add_node("wait_for_approval", wait_for_approval)
    graph.add_edge(START, "wait_for_approval")
    graph.add_edge("wait_for_approval", END)
    return graph.compile(checkpointer=checkpointer)


class _CheckpointHistoryLifecycle:
    def begin_node(self, **kwargs: Any) -> SimpleNamespace:
        step_key = str(kwargs["step_key"])
        return SimpleNamespace(
            workflow_version=int(kwargs["expected_workflow_version"]),
            step_id=f"{step_key}-id",
            lease_token=f"{step_key}-lease",
            already_completed=False,
            output_data=None,
        )

    def complete_node(self, **kwargs: Any) -> int:
        return int(kwargs["expected_workflow_version"]) + 1

    def begin_human_wait(self, **kwargs: Any) -> SimpleNamespace:
        step_key = str(kwargs["step_key"])
        return SimpleNamespace(
            workflow_version=int(kwargs["expected_workflow_version"]) + 1,
            step_id=f"{step_key}-id",
            already_completed=False,
            output_data=None,
        )

    def fail_node(self, **kwargs: Any) -> None:
        del kwargs


class _FixturePlanner:
    def create_plan(self, **kwargs: Any) -> SimpleNamespace:
        if kwargs["product_brief_version_id"] is None:
            raise ValueError("Fixture Planner requires an exact confirmed ProductBrief")
        workflow_id = str(kwargs["workflow_id"])
        return SimpleNamespace(
            to_step_output=lambda: {
                "creative_plan_ref": f"plan-{workflow_id}",
                "creative_plan_version_id": f"plan-version-{workflow_id}",
                "creative_plan_version": 1,
                "creative_plan_payload_sha256": "1" * 64,
                "planning_context_sha256": "2" * 64,
                "prompt_id": "creative-planner",
                "prompt_revision": "1.0.0",
                "plan_decision": None,
            }
        )


class _ExistingVersionFixturePlanner(_FixturePlanner):
    def create_plan(self, **kwargs: Any) -> SimpleNamespace:
        result = super().create_plan(**kwargs)
        output = result.to_step_output()
        output.update(
            {
                "creative_plan_version_id": "plan-version-existing-v7",
                "creative_plan_version": 7,
            }
        )
        return SimpleNamespace(to_step_output=lambda: output)


def test_plan_interrupt_uses_the_exact_persisted_plan_version() -> None:
    checkpointer = InMemorySaver()
    graph = build_fixture_graph(
        lifecycle=_CheckpointHistoryLifecycle(),  # type: ignore[arg-type]
        planner=_ExistingVersionFixturePlanner(),  # type: ignore[arg-type]
        tool_gateway=object(),  # type: ignore[arg-type]
        checkpointer=checkpointer,
        worker_id="exact-plan-interrupt-test",
    )
    runtime = FixtureAgentRuntime(graph, checkpointer)
    initial = _product_brief_state(
        workflow_id="exact-plan-interrupt",
        version_id="product-brief-v1",
        step_id="retrieval-step-v1",
        lease_token="retrieval-lease-v1",
    )

    result = runtime.run(
        initial_state=initial,
        preclaimed_step_id="retrieval-step-v1",
        preclaimed_lease_token="retrieval-lease-v1",
    )

    interrupt_payload = result["__interrupt__"][0].value
    assert interrupt_payload == {
        "interrupt_type": ApprovalType.CREATIVE_PLAN.value,
        "workflow_id": initial.workflow_id,
        "expected_workflow_version": 10,
        "subject_id": f"plan-{initial.workflow_id}",
        "subject_version_id": "plan-version-existing-v7",
        "subject_version": 7,
        "allowed_actions": [
            ApprovalDecision.APPROVE.value,
            ApprovalDecision.REJECT.value,
        ],
    }


def _graph_with_two_product_brief_generations(workflow_id: str) -> Any:
    checkpointer = InMemorySaver()
    graph = build_fixture_graph(
        lifecycle=_CheckpointHistoryLifecycle(),  # type: ignore[arg-type]
        planner=_FixturePlanner(),  # type: ignore[arg-type]
        tool_gateway=object(),  # type: ignore[arg-type]
        checkpointer=checkpointer,
        worker_id="checkpoint-history-test",
    )
    runtime = FixtureAgentRuntime(graph, checkpointer)
    with pytest.raises(ValueError, match="requires an exact confirmed ProductBrief"):
        runtime.run(
            initial_state=FixtureAgentState(
                workflow_id=workflow_id,
                workflow_version=1,
                workspace_id="checkpoint-entry",
                actor_id="checkpoint-entry-test",
                trace_id="checkpoint-entry-legacy-trace",
            )
        )
    for generation in ("v1", "v2"):
        runtime.run(
            initial_state=_product_brief_state(
                workflow_id=workflow_id,
                version_id=f"product-brief-{generation}",
                step_id=f"retrieval-step-{generation}",
                lease_token=f"retrieval-lease-{generation}",
            ),
            preclaimed_step_id=f"retrieval-step-{generation}",
            preclaimed_lease_token=f"retrieval-lease-{generation}",
        )
    return graph


def test_thread_wide_checkpoint_history_includes_every_generation() -> None:
    workflow_id = "checkpoint-thread-wide-history"
    graph = _graph_with_two_product_brief_generations(workflow_id)

    snapshots = list(
        graph.get_state_history(
            {"configurable": {"thread_id": workflow_id}},
        )
    )

    assert {snapshot.values.get("product_brief_version_id") for snapshot in snapshots} >= {
        "product-brief-v1",
        "product-brief-v2",
    }
    assert any(
        str(
            snapshot.config["configurable"].get("commercevision_checkpoint_generation", "")
        ).startswith("product-brief:v1:")
        and snapshot.config["configurable"].get("checkpoint_ns") == ""
        for snapshot in snapshots
    )
    assert all(
        "__commercevision_thread_wide_checkpoint_history" not in snapshot.config["configurable"]
        for snapshot in snapshots
    )


async def test_async_thread_wide_checkpoint_history_includes_every_generation() -> None:
    workflow_id = "checkpoint-async-thread-wide-history"
    graph = _graph_with_two_product_brief_generations(workflow_id)

    snapshots = [
        snapshot
        async for snapshot in graph.aget_state_history(
            {"configurable": {"thread_id": workflow_id}},
        )
    ]

    assert {snapshot.values.get("product_brief_version_id") for snapshot in snapshots} >= {
        "product-brief-v1",
        "product-brief-v2",
    }


def test_saver_thread_wide_list_without_namespace_includes_every_generation() -> None:
    workflow_id = "checkpoint-direct-thread-wide-history"
    graph = _graph_with_two_product_brief_generations(workflow_id)

    saved_checkpoints = list(graph.checkpointer.list({"configurable": {"thread_id": workflow_id}}))

    assert {
        saved.checkpoint["channel_values"].get("product_brief_version_id")
        for saved in saved_checkpoints
    } >= {"product-brief-v1", "product-brief-v2"}


def test_product_brief_checkpoint_history_never_contains_live_lease_token() -> None:
    workflow_id = "checkpoint-live-lease-token"
    lease_token = "live-lease-token-that-must-never-be-checkpointed"
    checkpointer = InMemorySaver()
    runtime = FixtureAgentRuntime(
        _build_crash_probe_graph(checkpointer, []),
        checkpointer,
    )

    with pytest.raises(RuntimeError, match="simulated crash"):
        runtime.run(
            initial_state=_product_brief_state(
                workflow_id=workflow_id,
                version_id="product-brief-v1",
                step_id="retrieval-step-v1",
                lease_token=lease_token,
            ),
            preclaimed_step_id="retrieval-step-v1",
            preclaimed_lease_token=lease_token,
        )

    history = list(checkpointer.list({"configurable": {"thread_id": workflow_id}}))
    assert history
    serialized_history = repr(
        [
            (
                saved.config,
                saved.checkpoint,
                saved.metadata,
                saved.parent_config,
                saved.pending_writes,
            )
            for saved in history
        ]
    )
    assert lease_token not in serialized_history


def test_explicit_empty_checkpoint_namespace_only_lists_legacy_root() -> None:
    workflow_id = "checkpoint-explicit-legacy-history"
    graph = _graph_with_two_product_brief_generations(workflow_id)

    saved_checkpoints = list(
        graph.checkpointer.list(
            {
                "configurable": {
                    "thread_id": workflow_id,
                    "checkpoint_ns": "",
                }
            }
        )
    )

    assert saved_checkpoints
    assert {
        saved.checkpoint["channel_values"].get("product_brief_version_id")
        for saved in saved_checkpoints
    } == {None}
    assert {saved.config["configurable"].get("checkpoint_ns") for saved in saved_checkpoints} == {
        ""
    }


def test_new_product_brief_generation_does_not_restore_a_crashed_prior_version() -> None:
    checkpointer = InMemorySaver()
    seen: list[tuple[str | None, str | None]] = []
    runtime = FixtureAgentRuntime(
        _build_crash_probe_graph(checkpointer, seen),
        checkpointer,
    )

    with pytest.raises(RuntimeError, match="simulated crash"):
        runtime.run(
            initial_state=_product_brief_state(
                workflow_id="checkpoint-generation-version",
                version_id="product-brief-v1",
                step_id="retrieval-step-v1",
                lease_token="retrieval-lease-v1",
            ),
            preclaimed_step_id="retrieval-step-v1",
            preclaimed_lease_token="retrieval-lease-v1",
        )

    runtime.run(
        initial_state=_product_brief_state(
            workflow_id="checkpoint-generation-version",
            version_id="product-brief-v2",
            step_id="retrieval-step-v2",
            lease_token="retrieval-lease-v2",
        ),
        preclaimed_step_id="retrieval-step-v2",
        preclaimed_lease_token="retrieval-lease-v2",
    )

    assert [version_id for version_id, _ in seen] == [
        "product-brief-v1",
        "product-brief-v2",
    ]
    assert seen[0][1] != seen[1][1]


def test_reclaimed_product_brief_step_uses_the_new_lease_generation() -> None:
    checkpointer = InMemorySaver()
    seen: list[tuple[str | None, str | None]] = []
    runtime = FixtureAgentRuntime(
        _build_crash_probe_graph(checkpointer, seen),
        checkpointer,
    )

    with pytest.raises(RuntimeError, match="simulated crash"):
        runtime.run(
            initial_state=_product_brief_state(
                workflow_id="checkpoint-generation-lease",
                version_id="product-brief-v1",
                step_id="retrieval-step-v1",
                lease_token="expired-retrieval-lease",
            ),
            preclaimed_step_id="retrieval-step-v1",
            preclaimed_lease_token="expired-retrieval-lease",
        )

    runtime.run(
        initial_state=_product_brief_state(
            workflow_id="checkpoint-generation-lease",
            version_id="product-brief-v1",
            step_id="retrieval-step-v1",
            lease_token="reclaimed-retrieval-lease",
        ),
        preclaimed_step_id="retrieval-step-v1",
        preclaimed_lease_token="reclaimed-retrieval-lease",
    )

    assert [version_id for version_id, _ in seen] == [
        "product-brief-v1",
        "product-brief-v1",
    ]
    assert seen[0][1] != seen[1][1]


def test_human_resume_finds_the_checkpoint_for_the_active_product_brief_generation() -> None:
    checkpointer = InMemorySaver()
    resumed_decisions: list[str] = []
    runtime = FixtureAgentRuntime(
        _build_human_wait_probe_graph(checkpointer, resumed_decisions),
        checkpointer,
    )
    workflow_id = "checkpoint-generation-human-wait"
    subject_id = "fixture://creative-plan/checkpoint-generation-human-wait/v1"
    initial_result = runtime.run(
        initial_state=_product_brief_state(
            workflow_id=workflow_id,
            version_id="product-brief-v1",
            step_id="retrieval-step-v1",
            lease_token="retrieval-lease-v1",
        ).model_copy(
            update={
                "creative_plan_ref": subject_id,
                "creative_plan_version_id": "creative-plan-version-v1",
                "creative_plan_version": 1,
            }
        ),
        preclaimed_step_id="retrieval-step-v1",
        preclaimed_lease_token="retrieval-lease-v1",
    )
    assert "__interrupt__" in initial_result

    result = runtime.run(
        initial_state=FixtureAgentState(
            workflow_id=workflow_id,
            workflow_version=8,
            workspace_id="checkpoint-entry",
            actor_id="checkpoint-entry-test",
            trace_id="checkpoint-entry-resume-trace",
            current_node="approve_plan",
        ),
        resume_payload={
            "workflow_id": workflow_id,
            "approval_id": "approval-0001",
            "approval_type": ApprovalType.CREATIVE_PLAN.value,
            "decision": ApprovalDecision.APPROVE.value,
            "expected_workflow_version": 7,
            "resulting_workflow_version": 8,
            "subject_id": subject_id,
            "subject_version": 1,
        },
    )

    assert resumed_decisions == [ApprovalDecision.APPROVE.value]
    assert result["plan_decision"] == ApprovalDecision.APPROVE.value


def test_mysql_authority_can_resume_an_edited_plan_version_from_the_original_wait() -> None:
    checkpointer = InMemorySaver()
    resumed_decisions: list[str] = []
    resumed_plan_versions: list[tuple[str | None, int | None]] = []
    runtime = FixtureAgentRuntime(
        _build_human_wait_probe_graph(
            checkpointer,
            resumed_decisions,
            resumed_plan_versions,
        ),
        checkpointer,
    )
    workflow_id = "checkpoint-edited-plan-resume"
    subject_id = "fixture://creative-plan/checkpoint-edited-plan-resume"
    interrupted = runtime.run(
        initial_state=_product_brief_state(
            workflow_id=workflow_id,
            version_id="product-brief-v1",
            step_id="retrieval-step-v1",
            lease_token="retrieval-lease-v1",
        ).model_copy(
            update={
                "creative_plan_ref": subject_id,
                "creative_plan_version_id": "creative-plan-version-v1",
                "creative_plan_version": 1,
            }
        ),
        preclaimed_step_id="retrieval-step-v1",
        preclaimed_lease_token="retrieval-lease-v1",
    )
    assert "__interrupt__" in interrupted

    result = runtime.run(
        initial_state=FixtureAgentState(
            workflow_id=workflow_id,
            workflow_version=8,
            workspace_id="checkpoint-entry",
            actor_id="checkpoint-entry-test",
            trace_id="checkpoint-edited-plan-resume-trace",
            current_node="approve_plan",
        ),
        resume_payload={
            "workflow_id": workflow_id,
            "approval_id": "approval-edited-plan-v2",
            "approval_type": ApprovalType.CREATIVE_PLAN.value,
            "decision": ApprovalDecision.APPROVE.value,
            "expected_workflow_version": 7,
            "resulting_workflow_version": 8,
            "subject_id": subject_id,
            "subject_version": 2,
        },
        trusted_creative_plan_version_id="creative-plan-version-v2",
    )

    assert result["plan_decision"] == ApprovalDecision.APPROVE.value
    assert resumed_decisions == [ApprovalDecision.APPROVE.value]
    assert resumed_plan_versions == [("creative-plan-version-v2", 2)]


async def test_async_human_resume_survives_runtime_restart() -> None:
    checkpointer = InMemorySaver()
    resumed_decisions: list[str] = []
    workflow_id = "checkpoint-async-human-wait"
    subject_id = "fixture://creative-plan/checkpoint-async-human-wait/v1"
    first_runtime = FixtureAgentRuntime(
        _build_human_wait_probe_graph(checkpointer, resumed_decisions),
        checkpointer,
    )
    initial = _product_brief_state(
        workflow_id=workflow_id,
        version_id="product-brief-v1",
        step_id="retrieval-step-v1",
        lease_token="retrieval-lease-v1",
    ).model_copy(
        update={
            "creative_plan_ref": subject_id,
            "creative_plan_version_id": "creative-plan-version-v1",
            "creative_plan_version": 1,
        }
    )
    interrupted = await first_runtime.arun(
        initial_state=initial,
        preclaimed_step_id="retrieval-step-v1",
        preclaimed_lease_token="retrieval-lease-v1",
    )
    assert "__interrupt__" in interrupted

    restarted_runtime = FixtureAgentRuntime(
        _build_human_wait_probe_graph(checkpointer, resumed_decisions),
        checkpointer,
    )
    result = await restarted_runtime.arun(
        initial_state=FixtureAgentState(
            workflow_id=workflow_id,
            workflow_version=8,
            workspace_id="checkpoint-entry",
            actor_id="checkpoint-entry-test",
            trace_id="checkpoint-async-resume-trace",
            current_node="approve_plan",
        ),
        resume_payload={
            "workflow_id": workflow_id,
            "approval_id": "approval-async-0001",
            "approval_type": ApprovalType.CREATIVE_PLAN.value,
            "decision": ApprovalDecision.APPROVE.value,
            "expected_workflow_version": 7,
            "resulting_workflow_version": 8,
            "subject_id": subject_id,
            "subject_version": 1,
        },
    )

    assert result["plan_decision"] == ApprovalDecision.APPROVE.value
    assert resumed_decisions == [ApprovalDecision.APPROVE.value]


def test_resume_without_a_matching_checkpoint_is_a_stable_conflict() -> None:
    checkpointer = InMemorySaver()
    observations: list[tuple[str, dict[str, object]]] = []

    class Observer:
        @contextmanager
        def observe(self, **values):
            observations.append(("span", values))
            yield

        def record_resume(self, **values):
            observations.append(("resume", values))

    runtime = FixtureAgentRuntime(
        _build_human_wait_probe_graph(checkpointer, []),
        checkpointer,
        observer=Observer(),
    )

    with pytest.raises(ResumeCheckpointConflictError, match="no matching durable checkpoint"):
        runtime.run(
            initial_state=FixtureAgentState(
                workflow_id="checkpoint-missing-resume",
                workflow_version=8,
                workspace_id="checkpoint-entry",
                actor_id="checkpoint-entry-test",
                trace_id="checkpoint-missing-resume-trace",
                current_node="approve_plan",
            ),
            resume_payload={
                "workflow_id": "checkpoint-missing-resume",
                "approval_id": "approval-missing-0001",
                "approval_type": ApprovalType.CREATIVE_PLAN.value,
                "decision": ApprovalDecision.APPROVE.value,
                "expected_workflow_version": 7,
                "resulting_workflow_version": 8,
                "subject_id": "plan-missing-v1",
                "subject_version": 1,
            },
        )

    assert observations == [
        (
            "span",
            {
                "step": "langgraph.resume",
                "trace_id": "checkpoint-missing-resume-trace",
                "workflow_id": "checkpoint-missing-resume",
                "plan_id": "plan-missing-v1",
                "plan_version": 1,
                "approval_id": "approval-missing-0001",
            },
        ),
        ("resume", {"outcome": "checkpoint_mismatch"}),
    ]


@pytest.mark.parametrize("current_node", ["execute_tool", "export"])
def test_missing_checkpoint_cannot_enter_a_side_effecting_node(current_node: str) -> None:
    checkpointer = InMemorySaver()
    graph = build_fixture_graph(
        lifecycle=object(),  # type: ignore[arg-type]
        planner=_FixturePlanner(),  # type: ignore[arg-type]
        tool_gateway=object(),  # type: ignore[arg-type]
        checkpointer=checkpointer,
        worker_id="checkpoint-entry-test",
    )
    runtime = FixtureAgentRuntime(graph, checkpointer)

    with pytest.raises(
        ValueError,
        match=rf"workflow node {current_node} requires an existing durable checkpoint",
    ):
        runtime.run(
            initial_state=FixtureAgentState(
                workflow_id=f"missing-checkpoint-{current_node}",
                workflow_version=7,
                workspace_id="checkpoint-entry",
                actor_id="checkpoint-entry-test",
                trace_id="checkpoint-entry-trace",
                current_node=current_node,
            )
        )


def test_missing_checkpoint_retrieval_requires_an_exact_confirmed_product_brief() -> None:
    checkpointer = InMemorySaver()
    graph = build_fixture_graph(
        lifecycle=object(),  # type: ignore[arg-type]
        planner=_FixturePlanner(),  # type: ignore[arg-type]
        tool_gateway=object(),  # type: ignore[arg-type]
        checkpointer=checkpointer,
        worker_id="checkpoint-entry-test",
    )
    runtime = FixtureAgentRuntime(graph, checkpointer)

    with pytest.raises(
        ValueError,
        match="retrieve_references requires an exact confirmed ProductBrief",
    ):
        runtime.run(
            initial_state=FixtureAgentState(
                workflow_id="missing-product-brief",
                workflow_version=7,
                workspace_id="checkpoint-entry",
                actor_id="checkpoint-entry-test",
                trace_id="checkpoint-entry-trace",
                current_node="retrieve_references",
            )
        )


def test_exact_confirmed_entry_still_requires_runtime_private_preclaim() -> None:
    workflow_id = "missing-private-product-brief-preclaim"
    checkpointer = InMemorySaver()
    graph = build_fixture_graph(
        lifecycle=object(),  # type: ignore[arg-type]
        planner=_FixturePlanner(),  # type: ignore[arg-type]
        tool_gateway=object(),  # type: ignore[arg-type]
        checkpointer=checkpointer,
        worker_id="checkpoint-entry-test",
    )
    runtime = FixtureAgentRuntime(graph, checkpointer)
    state = _product_brief_state(
        workflow_id=workflow_id,
        version_id="product-brief-v1",
        step_id="retrieval-step-v1",
        lease_token="lease-that-is-not-in-state",
    )
    assert "initial_step_lease_token" not in state.model_dump(mode="json")

    with pytest.raises(
        ValueError,
        match="confirmed ProductBrief runtime preclaim is inconsistent",
    ):
        runtime.run(initial_state=state)

    assert list(checkpointer.list({"configurable": {"thread_id": workflow_id}})) == []
