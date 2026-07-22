from __future__ import annotations

from datetime import UTC, datetime

import pytest
from commercevision_persistence import MySQLCheckpointSaver
from langgraph.checkpoint.base import empty_checkpoint

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
