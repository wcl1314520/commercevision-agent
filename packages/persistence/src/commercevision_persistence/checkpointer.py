"""MySQL-backed LangGraph checkpointer with safe JSON/msgpack serialization."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Iterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_serializable_checkpoint_metadata,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from sqlalchemy import delete, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session, sessionmaker

from .models import AgentCheckpointModel, AgentCheckpointWriteModel

_METADATA_KEY = re.compile(r"^[A-Za-z0-9_]{1,64}$")


class MySQLCheckpointSaver(BaseCheckpointSaver[str]):
    """Persist complete checkpoints and writes in MySQL.

    `keep_latest` intentionally preserves the complete chain because dropping
    arbitrary ancestors can corrupt DeltaChannel reconstruction. Task retention
    removes the complete thread with `delete_thread`.
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        retention: timedelta = timedelta(hours=72),
    ) -> None:
        super().__init__(serde=JsonPlusSerializer(pickle_fallback=False))
        self._session_factory = session_factory
        self._retention = retention

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        configurable = config["configurable"]
        thread_id = str(configurable["thread_id"])
        checkpoint_ns = str(configurable.get("checkpoint_ns", ""))
        checkpoint_id = get_checkpoint_id(config)
        with self._session_factory() as session:
            statement = select(AgentCheckpointModel).where(
                AgentCheckpointModel.thread_id == thread_id,
                AgentCheckpointModel.checkpoint_namespace == checkpoint_ns,
            )
            if checkpoint_id:
                statement = statement.where(AgentCheckpointModel.checkpoint_id == checkpoint_id)
            else:
                statement = statement.order_by(AgentCheckpointModel.checkpoint_id.desc()).limit(1)
            model = session.scalar(statement)
            if model is None:
                return None
            return self._tuple_from_model(session, model)

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        with self._session_factory() as session:
            statement = select(AgentCheckpointModel)
            if config:
                configurable = config["configurable"]
                statement = statement.where(
                    AgentCheckpointModel.thread_id == str(configurable["thread_id"])
                )
                if "checkpoint_ns" in configurable:
                    statement = statement.where(
                        AgentCheckpointModel.checkpoint_namespace
                        == str(configurable.get("checkpoint_ns", ""))
                    )
                if checkpoint_id := get_checkpoint_id(config):
                    statement = statement.where(AgentCheckpointModel.checkpoint_id == checkpoint_id)
            if before and (before_id := get_checkpoint_id(before)):
                statement = statement.where(AgentCheckpointModel.checkpoint_id < before_id)
            for key, value in (filter or {}).items():
                if not _METADATA_KEY.fullmatch(key):
                    return
                expression = AgentCheckpointModel.metadata_json[key]
                if isinstance(value, bool):
                    statement = statement.where(expression.as_boolean() == value)
                elif isinstance(value, int):
                    statement = statement.where(expression.as_integer() == value)
                elif isinstance(value, float):
                    statement = statement.where(expression.as_float() == value)
                elif value is None:
                    statement = statement.where(expression.is_(None))
                else:
                    statement = statement.where(expression.as_string() == str(value))
            statement = statement.order_by(AgentCheckpointModel.checkpoint_id.desc())
            if limit is not None:
                if limit <= 0:
                    return
                statement = statement.limit(limit)
            models = list(session.scalars(statement))
            tuples = [self._tuple_from_model(session, model) for model in models]
        yield from tuples

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        del new_versions
        configurable = config["configurable"]
        thread_id = str(configurable["thread_id"])
        checkpoint_ns = str(configurable.get("checkpoint_ns", ""))
        parent_checkpoint_id = get_checkpoint_id(config)
        checkpoint_type, checkpoint_blob = self.serde.dumps_typed(checkpoint)
        serializable_metadata = get_serializable_checkpoint_metadata(config, metadata)
        metadata_type, metadata_blob = self.serde.dumps_typed(serializable_metadata)
        now = datetime.now(UTC)
        channel_values = checkpoint.get("channel_values", {})
        workflow_version_raw = channel_values.get(
            "workflow_version", configurable.get("workflow_version")
        )
        workflow_version = int(workflow_version_raw) if workflow_version_raw is not None else None
        values = {
            "thread_id": thread_id,
            "checkpoint_namespace": checkpoint_ns,
            "checkpoint_id": checkpoint["id"],
            "parent_checkpoint_id": parent_checkpoint_id,
            "workflow_id": channel_values.get("workflow_id", configurable.get("workflow_id")),
            "workflow_version": workflow_version,
            "run_id": serializable_metadata.get("run_id"),
            "checkpoint_type": checkpoint_type,
            "checkpoint_blob": checkpoint_blob,
            "metadata_type": metadata_type,
            "metadata_blob": metadata_blob,
            "metadata_json": serializable_metadata,
            "created_at": now,
            "expires_at": now + self._retention,
        }
        statement = mysql_insert(AgentCheckpointModel).values(**values)
        statement = statement.on_duplicate_key_update(
            parent_checkpoint_id=statement.inserted.parent_checkpoint_id,
            workflow_id=statement.inserted.workflow_id,
            workflow_version=statement.inserted.workflow_version,
            run_id=statement.inserted.run_id,
            checkpoint_type=statement.inserted.checkpoint_type,
            checkpoint_blob=statement.inserted.checkpoint_blob,
            metadata_type=statement.inserted.metadata_type,
            metadata_blob=statement.inserted.metadata_blob,
            metadata_json=statement.inserted.metadata_json,
            expires_at=statement.inserted.expires_at,
        )
        with self._session_factory.begin() as session:
            session.execute(statement)
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint["id"],
                **(
                    {"workflow_id": configurable["workflow_id"]}
                    if configurable.get("workflow_id")
                    else {}
                ),
                **({"workflow_version": workflow_version} if workflow_version is not None else {}),
            }
        }

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        configurable = config["configurable"]
        thread_id = str(configurable["thread_id"])
        checkpoint_ns = str(configurable.get("checkpoint_ns", ""))
        checkpoint_id = str(configurable["checkpoint_id"])
        now = datetime.now(UTC)
        with self._session_factory.begin() as session:
            for index, (channel, value) in enumerate(writes):
                write_index = WRITES_IDX_MAP.get(channel, index)
                value_type, value_blob = self.serde.dumps_typed(value)
                values = {
                    "thread_id": thread_id,
                    "checkpoint_namespace": checkpoint_ns,
                    "checkpoint_id": checkpoint_id,
                    "task_id": task_id,
                    "write_index": write_index,
                    "task_path": task_path,
                    "channel": channel,
                    "value_type": value_type,
                    "value_blob": value_blob,
                    "created_at": now,
                }
                statement = mysql_insert(AgentCheckpointWriteModel).values(**values)
                if write_index < 0:
                    statement = statement.on_duplicate_key_update(
                        task_path=statement.inserted.task_path,
                        channel=statement.inserted.channel,
                        value_type=statement.inserted.value_type,
                        value_blob=statement.inserted.value_blob,
                    )
                else:
                    statement = statement.prefix_with("IGNORE")
                session.execute(statement)

    def delete_thread(self, thread_id: str) -> None:
        with self._session_factory.begin() as session:
            self._delete_thread(session, thread_id)

    def delete_for_runs(self, run_ids: Sequence[str]) -> None:
        if not run_ids:
            return
        with self._session_factory.begin() as session:
            thread_ids = list(
                session.scalars(
                    select(AgentCheckpointModel.thread_id)
                    .where(AgentCheckpointModel.run_id.in_(run_ids))
                    .distinct()
                )
            )
            for thread_id in thread_ids:
                self._delete_thread(session, thread_id)

    def copy_thread(self, source_thread_id: str, target_thread_id: str) -> None:
        if source_thread_id == target_thread_id:
            return
        with self._session_factory.begin() as session:
            target_exists = session.scalar(
                select(AgentCheckpointModel.thread_id)
                .where(AgentCheckpointModel.thread_id == target_thread_id)
                .limit(1)
            )
            if target_exists:
                raise ValueError(f"target checkpoint thread already exists: {target_thread_id}")
            checkpoints = list(
                session.scalars(
                    select(AgentCheckpointModel).where(
                        AgentCheckpointModel.thread_id == source_thread_id
                    )
                )
            )
            writes = list(
                session.scalars(
                    select(AgentCheckpointWriteModel).where(
                        AgentCheckpointWriteModel.thread_id == source_thread_id
                    )
                )
            )
            for model in checkpoints:
                session.add(
                    AgentCheckpointModel(
                        thread_id=target_thread_id,
                        checkpoint_namespace=model.checkpoint_namespace,
                        checkpoint_id=model.checkpoint_id,
                        parent_checkpoint_id=model.parent_checkpoint_id,
                        workflow_id=model.workflow_id,
                        workflow_version=model.workflow_version,
                        run_id=model.run_id,
                        checkpoint_type=model.checkpoint_type,
                        checkpoint_blob=model.checkpoint_blob,
                        metadata_type=model.metadata_type,
                        metadata_blob=model.metadata_blob,
                        metadata_json=model.metadata_json,
                        created_at=model.created_at,
                        expires_at=model.expires_at,
                    )
                )
            for model in writes:
                session.add(
                    AgentCheckpointWriteModel(
                        thread_id=target_thread_id,
                        checkpoint_namespace=model.checkpoint_namespace,
                        checkpoint_id=model.checkpoint_id,
                        task_id=model.task_id,
                        write_index=model.write_index,
                        task_path=model.task_path,
                        channel=model.channel,
                        value_type=model.value_type,
                        value_blob=model.value_blob,
                        created_at=model.created_at,
                    )
                )

    def prune(
        self,
        thread_ids: Sequence[str],
        *,
        strategy: str = "keep_latest",
    ) -> None:
        if strategy == "keep_latest":
            return
        if strategy != "delete":
            raise ValueError(f"unsupported checkpoint prune strategy: {strategy}")
        with self._session_factory.begin() as session:
            for thread_id in thread_ids:
                self._delete_thread(session, thread_id)

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return await asyncio.to_thread(self.get_tuple, config)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        tuples = await asyncio.to_thread(
            lambda: list(self.list(config, filter=filter, before=before, limit=limit))
        )
        for item in tuples:
            yield item

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return await asyncio.to_thread(self.put, config, checkpoint, metadata, new_versions)

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id: str) -> None:
        await asyncio.to_thread(self.delete_thread, thread_id)

    async def adelete_for_runs(self, run_ids: Sequence[str]) -> None:
        await asyncio.to_thread(self.delete_for_runs, run_ids)

    async def acopy_thread(self, source_thread_id: str, target_thread_id: str) -> None:
        await asyncio.to_thread(self.copy_thread, source_thread_id, target_thread_id)

    async def aprune(
        self,
        thread_ids: Sequence[str],
        *,
        strategy: str = "keep_latest",
    ) -> None:
        await asyncio.to_thread(self.prune, thread_ids, strategy=strategy)

    def _tuple_from_model(self, session: Session, model: AgentCheckpointModel) -> CheckpointTuple:
        writes = list(
            session.scalars(
                select(AgentCheckpointWriteModel)
                .where(
                    AgentCheckpointWriteModel.thread_id == model.thread_id,
                    AgentCheckpointWriteModel.checkpoint_namespace == model.checkpoint_namespace,
                    AgentCheckpointWriteModel.checkpoint_id == model.checkpoint_id,
                )
                .order_by(
                    AgentCheckpointWriteModel.task_id,
                    AgentCheckpointWriteModel.write_index,
                )
            )
        )
        config: RunnableConfig = {
            "configurable": {
                "thread_id": model.thread_id,
                "checkpoint_ns": model.checkpoint_namespace,
                "checkpoint_id": model.checkpoint_id,
                **({"workflow_id": model.workflow_id} if model.workflow_id else {}),
                **(
                    {"workflow_version": model.workflow_version}
                    if model.workflow_version is not None
                    else {}
                ),
            }
        }
        parent_config: RunnableConfig | None = None
        if model.parent_checkpoint_id:
            parent_config = {
                "configurable": {
                    "thread_id": model.thread_id,
                    "checkpoint_ns": model.checkpoint_namespace,
                    "checkpoint_id": model.parent_checkpoint_id,
                }
            }
        return CheckpointTuple(
            config=config,
            checkpoint=self.serde.loads_typed((model.checkpoint_type, model.checkpoint_blob)),
            metadata=self.serde.loads_typed((model.metadata_type, model.metadata_blob)),
            parent_config=parent_config,
            pending_writes=[
                (
                    write.task_id,
                    write.channel,
                    self.serde.loads_typed((write.value_type, write.value_blob)),
                )
                for write in writes
            ],
        )

    @staticmethod
    def _delete_thread(session: Session, thread_id: str) -> None:
        session.execute(
            delete(AgentCheckpointWriteModel).where(
                AgentCheckpointWriteModel.thread_id == thread_id
            )
        )
        session.execute(
            delete(AgentCheckpointModel).where(AgentCheckpointModel.thread_id == thread_id)
        )
