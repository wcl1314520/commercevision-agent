from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from commercevision_agent_core import FixtureAgentRuntime, FixtureAgentState
from commercevision_domain import ApprovalDecision, ApprovalType
from commercevision_persistence import MySQLCheckpointSaver
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

pytestmark = pytest.mark.integration


def _checkpoint(checkpoint_id: str, value: str) -> dict:
    checkpoint = empty_checkpoint()
    checkpoint.update(
        {
            "id": checkpoint_id,
            "ts": datetime.now(UTC).isoformat(),
            "channel_values": {"value": value},
            "channel_versions": {"value": checkpoint_id},
            "versions_seen": {},
            "updated_channels": ["value"],
        }
    )
    return checkpoint


def test_mysql_checkpointer_parent_chain_writes_filters_and_copy(integration_database) -> None:
    saver = MySQLCheckpointSaver(integration_database.session_factory)
    first_config = {
        "configurable": {
            "thread_id": "thread-checkpointer",
            "checkpoint_ns": "",
            "workflow_id": "workflow-checkpointer",
            "workflow_version": 1,
        },
        "metadata": {"run_id": "run-checkpointer", "stage": "first"},
    }
    first = saver.put(
        first_config,
        _checkpoint("00000000-0000-7000-8000-000000000001", "first"),
        {"source": "input", "step": 0, "parents": {}, "run_id": "run-checkpointer"},
        {"value": "1"},
    )
    saver.put_writes(
        {**first, "configurable": {**first["configurable"]}},
        [("value", {"pending": 1}), ("__interrupt__", {"kind": "approval"})],
        task_id="task-1",
        task_path="node",
    )
    second = saver.put(
        first,
        _checkpoint("00000000-0000-7000-8000-000000000002", "second"),
        {"source": "loop", "step": 1, "parents": {}, "run_id": "run-checkpointer"},
        {"value": "2"},
    )

    latest = saver.get_tuple({"configurable": second["configurable"]})
    assert latest is not None
    assert latest.checkpoint["channel_values"]["value"] == "second"
    assert (
        latest.parent_config["configurable"]["checkpoint_id"]
        == first["configurable"]["checkpoint_id"]
    )
    assert latest.pending_writes == []
    parent = saver.get_tuple({"configurable": first["configurable"]})
    assert parent is not None
    assert len(parent.pending_writes) == 2

    listed = list(
        saver.list(
            {"configurable": {"thread_id": "thread-checkpointer", "checkpoint_ns": ""}},
            filter={"stage": "first"},
        )
    )
    assert len(listed) == 1
    assert listed[0].checkpoint["channel_values"]["value"] == "first"
    listed = list(
        saver.list(
            {"configurable": {"thread_id": "thread-checkpointer", "checkpoint_ns": ""}},
            limit=1,
        )
    )
    assert len(listed) == 1
    assert listed[0].checkpoint["channel_values"]["value"] == "second"

    saver.copy_thread("thread-checkpointer", "thread-checkpointer-copy")
    copied = saver.get_tuple(
        {
            "configurable": {
                "thread_id": "thread-checkpointer-copy",
                "checkpoint_ns": "",
            }
        }
    )
    assert copied is not None
    assert copied.parent_config["configurable"]["thread_id"] == "thread-checkpointer-copy"

    saver.delete_for_runs(["run-checkpointer"])
    assert (
        saver.get_tuple(
            {
                "configurable": {
                    "thread_id": "thread-checkpointer",
                    "checkpoint_ns": "",
                }
            }
        )
        is None
    )
    assert (
        saver.get_tuple(
            {
                "configurable": {
                    "thread_id": "thread-checkpointer-copy",
                    "checkpoint_ns": "",
                }
            }
        )
        is None
    )


@pytest.mark.asyncio
async def test_mysql_checkpointer_async_contract(integration_database) -> None:
    saver = MySQLCheckpointSaver(integration_database.session_factory)
    config = {
        "configurable": {
            "thread_id": "thread-checkpointer-async",
            "checkpoint_ns": "",
        }
    }
    await saver.aput(
        config,
        _checkpoint("00000000-0000-7000-8000-000000000003", "async"),
        {"source": "input", "step": 0, "parents": {}, "run_id": "run-async"},
        {"value": "1"},
    )
    result = await saver.aget_tuple(config)
    assert result is not None
    assert result.checkpoint["channel_values"]["value"] == "async"


@pytest.mark.asyncio
async def test_mysql_checkpointer_async_plan_interrupt_survives_runtime_restart(
    integration_database,
) -> None:
    saver = MySQLCheckpointSaver(integration_database.session_factory)
    resumed: list[str] = []

    def build_graph() -> Any:
        def wait_for_plan(raw_state: FixtureAgentState) -> dict[str, str]:
            state = FixtureAgentState.model_validate(raw_state)
            decision = interrupt(
                {
                    "workflow_id": state.workflow_id,
                    "expected_workflow_version": state.workflow_version,
                    "subject_id": state.creative_plan_ref,
                    "subject_version": state.creative_plan_version,
                }
            )["decision"]
            resumed.append(str(decision))
            return {"plan_decision": str(decision), "current_node": "completed"}

        graph = StateGraph(FixtureAgentState)
        graph.add_node("wait_for_plan", wait_for_plan)
        graph.add_edge(START, "wait_for_plan")
        graph.add_edge("wait_for_plan", END)
        return graph.compile(checkpointer=saver)

    workflow_id = "mysql-async-plan-interrupt"
    plan_id = "plan-mysql-async-v1"
    first_runtime = FixtureAgentRuntime(build_graph(), saver)
    interrupted = await first_runtime.arun(
        initial_state=FixtureAgentState(
            workflow_id=workflow_id,
            workflow_version=7,
            workspace_id="mysql-checkpointer",
            actor_id="mysql-checkpointer-test",
            trace_id="mysql-async-interrupt-trace",
            creative_plan_ref=plan_id,
            creative_plan_version_id="plan-version-mysql-async-v1",
            creative_plan_version=1,
        )
    )
    assert "__interrupt__" in interrupted

    restarted_runtime = FixtureAgentRuntime(build_graph(), saver)
    result = await restarted_runtime.arun(
        initial_state=FixtureAgentState(
            workflow_id=workflow_id,
            workflow_version=8,
            workspace_id="mysql-checkpointer",
            actor_id="mysql-checkpointer-test",
            trace_id="mysql-async-resume-trace",
            current_node="approve_plan",
        ),
        resume_payload={
            "workflow_id": workflow_id,
            "approval_id": "approval-mysql-async-v1",
            "approval_type": ApprovalType.CREATIVE_PLAN.value,
            "decision": ApprovalDecision.APPROVE.value,
            "expected_workflow_version": 7,
            "resulting_workflow_version": 8,
            "subject_id": plan_id,
            "subject_version": 1,
        },
    )

    assert result["plan_decision"] == ApprovalDecision.APPROVE.value
    assert resumed == [ApprovalDecision.APPROVE.value]
