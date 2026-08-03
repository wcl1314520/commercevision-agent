"""MySQL checkpoints and source-of-truth projection for Collection rebuild execution."""

from __future__ import annotations

from datetime import UTC, datetime

from commercevision_application import (
    CollectionRebuildTarget,
    ImageIndexingTarget,
    RebuildValidationExpected,
    RebuildWorkBatch,
)
from commercevision_contracts import (
    CollectionRebuildValidationV1,
    MilvusVectorIdentityV1,
    MilvusVectorRowV1,
)
from commercevision_contracts.events import (
    COLLECTION_REBUILD_PROGRESSED_V1,
    COLLECTION_REBUILD_REQUESTED_V1,
    CollectionRebuildCommand,
    CollectionRebuildProgressedPayload,
    CollectionRebuildRequestedPayload,
)
from commercevision_domain import (
    CollectionRebuildState,
    CollectionSpec,
    CollectionState,
    EmbeddingState,
    VectorKind,
    new_uuid7,
)
from commercevision_domain.messaging import EventEnvelope, OutboxEvent
from sqlalchemy import and_, literal_column, or_, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session, sessionmaker

from .collection_rebuild_replay import REBUILD_REPLAY_EVENT_TYPES
from .indexing import MySqlIndexingAuthority
from .indexing_models import (
    CollectionRebuildModel,
    CollectionRebuildPlacementModel,
    CollectionRebuildProgressModel,
    CollectionRegistryModel,
    EmbeddingRecordModel,
)
from .models import OutboxEventModel
from .repositories import OutboxRepository


class MySqlCollectionRebuildRepository:
    """Advance one generation-fenced rebuild batch per Worker delivery."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._indexing = MySqlIndexingAuthority(session_factory)

    def load_command(
        self, payload: CollectionRebuildRequestedPayload
    ) -> CollectionRebuildTarget | None:
        with self._session_factory() as session:
            rebuild = session.scalar(
                select(CollectionRebuildModel).where(
                    CollectionRebuildModel.workspace_id == payload.workspace_id,
                    CollectionRebuildModel.id == payload.rebuild_id,
                    CollectionRebuildModel.operation_id == payload.operation_id,
                )
            )
            if rebuild is None or rebuild.generation != payload.generation:
                return None
            return self._target(session, rebuild)

    def begin_provisioning(self, target: CollectionRebuildTarget) -> CollectionRebuildTarget:
        with self._session_factory.begin() as session:
            rebuild = self._lock(session, target)
            candidate = self._candidate(session, rebuild)
            if rebuild.state == CollectionRebuildState.REQUESTED.value:
                now = self._database_now(session)
                rebuild.state = CollectionRebuildState.PROVISIONING.value
                rebuild.version += 1
                rebuild.updated_at = now
                candidate.state = CollectionState.CREATING.value
                candidate.version += 1
                candidate.updated_at = now
                self._progress(session, rebuild, "CANDIDATE_PROVISIONING", now)
            elif rebuild.state != CollectionRebuildState.PROVISIONING.value:
                return self._target(session, rebuild)
            session.flush()
            return self._target(session, rebuild)

    def complete_provisioning(self, target: CollectionRebuildTarget) -> None:
        with self._session_factory.begin() as session:
            rebuild = self._lock(session, target)
            if rebuild.state == CollectionRebuildState.BACKFILLING.value:
                return
            if rebuild.state != CollectionRebuildState.PROVISIONING.value:
                return
            now = self._database_now(session)
            candidate = self._candidate(session, rebuild)
            candidate.state = CollectionState.BACKFILLING.value
            candidate.version += 1
            candidate.updated_at = now
            rebuild.state = CollectionRebuildState.BACKFILLING.value
            rebuild.version += 1
            rebuild.updated_at = now
            self._progress(session, rebuild, "CANDIDATE_PROVISIONED", now)
            self._enqueue(session, rebuild, CollectionRebuildCommand.CONTINUE, now)

    def load_work_batch(self, target: CollectionRebuildTarget, *, limit: int) -> RebuildWorkBatch:
        with self._session_factory() as session:
            rebuild = session.get(CollectionRebuildModel, target.id)
            if rebuild is None or rebuild.generation != target.generation:
                return RebuildWorkBatch((), (), None, True)
            if rebuild.state == CollectionRebuildState.BACKFILLING.value:
                ids = tuple(
                    session.scalars(
                        select(EmbeddingRecordModel.id)
                        .where(
                            EmbeddingRecordModel.workspace_id == rebuild.workspace_id,
                            EmbeddingRecordModel.collection_id == rebuild.source_collection_id,
                            EmbeddingRecordModel.vector_kind == rebuild.vector_kind,
                            EmbeddingRecordModel.state == EmbeddingState.INDEXED.value,
                            EmbeddingRecordModel.updated_at <= rebuild.snapshot_watermark,
                            *(
                                (EmbeddingRecordModel.id > rebuild.backfill_cursor,)
                                if rebuild.backfill_cursor is not None
                                else ()
                            ),
                        )
                        .order_by(EmbeddingRecordModel.id)
                        .limit(limit)
                    )
                )
                upserts = self._load_targets(rebuild, ids)
                return RebuildWorkBatch(
                    upserts=upserts,
                    deletes=(),
                    cursor=ids[-1] if ids else rebuild.backfill_cursor,
                    phase_complete=len(ids) < limit,
                )
            if rebuild.state == CollectionRebuildState.REPLAYING.value:
                events = self._replay_events(session, rebuild, limit)
                asset_ids = {
                    str(event.payload_json.get("asset_id"))
                    for event in events
                    if event.payload_json.get("asset_id")
                }
                record_ids = (
                    tuple(
                        session.scalars(
                            select(EmbeddingRecordModel.id)
                            .where(
                                EmbeddingRecordModel.workspace_id == rebuild.workspace_id,
                                EmbeddingRecordModel.collection_id == rebuild.source_collection_id,
                                EmbeddingRecordModel.vector_kind == rebuild.vector_kind,
                                EmbeddingRecordModel.asset_id.in_(asset_ids),
                            )
                            .order_by(EmbeddingRecordModel.id)
                        )
                    )
                    if asset_ids
                    else ()
                )
                upserts, deletes = self._reconcile_records(session, rebuild, record_ids)
                cursor = (
                    f"{events[-1].occurred_at.isoformat()}|{events[-1].id}"
                    if events
                    else self._encode_replay_cursor(rebuild)
                )
                return RebuildWorkBatch(
                    upserts=upserts,
                    deletes=deletes,
                    cursor=cursor,
                    phase_complete=len(events) < limit,
                )
            if rebuild.state == CollectionRebuildState.RIGHTS_RESCAN.value:
                placements = tuple(
                    session.scalars(
                        select(CollectionRebuildPlacementModel)
                        .where(
                            CollectionRebuildPlacementModel.rebuild_id == rebuild.id,
                            *(
                                (
                                    CollectionRebuildPlacementModel.embedding_record_id
                                    > rebuild.rights_cursor,
                                )
                                if rebuild.rights_cursor is not None
                                else ()
                            ),
                        )
                        .order_by(CollectionRebuildPlacementModel.embedding_record_id)
                        .limit(limit)
                    )
                )
                record_ids = tuple(item.embedding_record_id for item in placements)
                upserts, deletes = self._reconcile_records(session, rebuild, record_ids)
                return RebuildWorkBatch(
                    upserts=upserts,
                    deletes=deletes,
                    cursor=record_ids[-1] if record_ids else rebuild.rights_cursor,
                    phase_complete=len(record_ids) < limit,
                )
            return RebuildWorkBatch((), (), None, True)

    def commit_work_batch(
        self,
        target: CollectionRebuildTarget,
        batch: RebuildWorkBatch,
        rows: tuple[MilvusVectorRowV1, ...],
    ) -> None:
        with self._session_factory.begin() as session:
            rebuild = self._lock(session, target)
            now = self._database_now(session)
            for row in rows:
                if row.workspace_id != rebuild.workspace_id:
                    raise ValueError("candidate row escaped the rebuild workspace")
                embedding = session.get(EmbeddingRecordModel, row.embedding_record_id)
                if embedding is None:
                    continue
                statement = mysql_insert(CollectionRebuildPlacementModel).values(
                    rebuild_id=rebuild.id,
                    embedding_record_id=row.embedding_record_id,
                    workspace_id=row.workspace_id,
                    asset_id=embedding.asset_id,
                    asset_version_id=row.asset_version_id,
                    milvus_primary_key=row.milvus_primary_key,
                    input_hash=row.input_hash,
                    embedding_spec_hash=row.embedding_spec_sha256,
                    write_generation=row.write_generation,
                    placed_at=now,
                )
                session.execute(
                    statement.on_duplicate_key_update(
                        asset_id=statement.inserted.asset_id,
                        asset_version_id=statement.inserted.asset_version_id,
                        milvus_primary_key=statement.inserted.milvus_primary_key,
                        input_hash=statement.inserted.input_hash,
                        embedding_spec_hash=statement.inserted.embedding_spec_hash,
                        write_generation=statement.inserted.write_generation,
                        placed_at=statement.inserted.placed_at,
                    )
                )
            for identity in batch.deletes:
                session.query(CollectionRebuildPlacementModel).filter(
                    CollectionRebuildPlacementModel.rebuild_id == rebuild.id,
                    CollectionRebuildPlacementModel.embedding_record_id
                    == identity.embedding_record_id,
                ).delete(synchronize_session=False)
            rebuild.processed_count += len(rows) + len(batch.deletes)
            if rebuild.state == CollectionRebuildState.BACKFILLING.value:
                rebuild.backfill_cursor = batch.cursor
                if batch.phase_complete:
                    rebuild.state = CollectionRebuildState.REPLAYING.value
                    rebuild.replay_watermark = now
            elif rebuild.state == CollectionRebuildState.REPLAYING.value:
                self._set_replay_cursor(rebuild, batch.cursor)
                if batch.phase_complete:
                    rebuild.state = CollectionRebuildState.RIGHTS_RESCAN.value
                    rebuild.rights_cursor = None
            elif rebuild.state == CollectionRebuildState.RIGHTS_RESCAN.value:
                rebuild.rights_cursor = batch.cursor
                if batch.phase_complete:
                    rebuild.state = CollectionRebuildState.AWAITING_VALIDATION.value
            rebuild.version += 1
            rebuild.updated_at = now
            message = f"{rebuild.state}_CHECKPOINT"
            self._progress(session, rebuild, message, now)
            command = (
                CollectionRebuildCommand.VALIDATE
                if rebuild.state == CollectionRebuildState.AWAITING_VALIDATION.value
                else CollectionRebuildCommand.CONTINUE
            )
            self._enqueue(session, rebuild, command, now)

    def validation_expected(self, target: CollectionRebuildTarget) -> RebuildValidationExpected:
        with self._session_factory() as session:
            record_ids = frozenset(
                session.scalars(
                    select(CollectionRebuildPlacementModel.embedding_record_id).where(
                        CollectionRebuildPlacementModel.rebuild_id == target.id
                    )
                )
            )
            return RebuildValidationExpected(record_ids)

    def begin_validation(self, target: CollectionRebuildTarget) -> CollectionRebuildTarget:
        with self._session_factory.begin() as session:
            rebuild = self._lock(session, target)
            if rebuild.state == CollectionRebuildState.AWAITING_VALIDATION.value:
                now = self._database_now(session)
                rebuild.state = CollectionRebuildState.VALIDATING.value
                rebuild.validation_watermark = now
                rebuild.version += 1
                rebuild.updated_at = now
                self._progress(session, rebuild, "VALIDATION_STARTED", now)
            if rebuild.state != CollectionRebuildState.VALIDATING.value:
                raise ValueError("collection rebuild is not ready for validation")
            if rebuild.validation_watermark is None:
                raise ValueError("collection validation watermark is missing")
            return self._target(session, rebuild)

    def complete_validation(
        self,
        target: CollectionRebuildTarget,
        report: CollectionRebuildValidationV1,
    ) -> None:
        with self._session_factory.begin() as session:
            rebuild = self._lock(session, target)
            if rebuild.state not in {
                CollectionRebuildState.AWAITING_VALIDATION.value,
                CollectionRebuildState.VALIDATING.value,
            }:
                return
            now = self._database_now(session)
            candidate = self._candidate(session, rebuild)
            rebuild.validation_summary_json = report.model_dump(mode="json")
            if rebuild.validation_watermark is None:
                raise ValueError("collection validation watermark is missing")
            rebuild.state = (
                CollectionRebuildState.READY.value
                if report.accepted
                else CollectionRebuildState.FAILED.value
            )
            rebuild.failure_code = None if report.accepted else "VALIDATION_REJECTED"
            candidate.state = (
                CollectionState.READY.value if report.accepted else CollectionState.FAILED.value
            )
            candidate.validation_summary_json = report.model_dump(mode="json")
            candidate.version += 1
            candidate.updated_at = now
            rebuild.version += 1
            rebuild.updated_at = now
            self._progress(
                session,
                rebuild,
                "VALIDATION_ACCEPTED" if report.accepted else "VALIDATION_REJECTED",
                now,
            )

    def complete_retirement(self, target: CollectionRebuildTarget) -> None:
        with self._session_factory.begin() as session:
            rebuild = self._lock(session, target)
            if rebuild.state != CollectionRebuildState.RETIRING.value:
                return
            now = self._database_now(session)
            if rebuild.retire_after is None or now < rebuild.retire_after:
                raise ValueError("collection retirement delay has not elapsed")
            source = session.get(CollectionRegistryModel, rebuild.source_collection_id)
            candidate = session.get(CollectionRegistryModel, rebuild.candidate_collection_id)
            if source is None or candidate is None:
                raise ValueError("collection retirement facts are incomplete")
            if candidate.state != CollectionState.ACTIVE.value or not candidate.is_read_enabled:
                raise ValueError("collection retirement would remove the active candidate")
            source.state = CollectionState.RETIRED.value
            source.is_read_enabled = False
            source.is_write_enabled = False
            source.version += 1
            source.updated_at = now
            rebuild.state = CollectionRebuildState.RETIRED.value
            rebuild.version += 1
            rebuild.updated_at = now
            self._progress(session, rebuild, "SOURCE_COLLECTION_RETIRED", now)

    def _load_targets(
        self, rebuild: CollectionRebuildModel, record_ids: tuple[str, ...]
    ) -> tuple[ImageIndexingTarget, ...]:
        candidate = self._candidate_spec_for_rebuild(rebuild.id)
        targets = []
        for record_id in record_ids:
            target = self._indexing.load_rebuild_source(
                workspace_id=rebuild.workspace_id,
                embedding_record_id=record_id,
                candidate_collection_id=rebuild.candidate_collection_id,
                candidate_spec=candidate,
            )
            if target is not None:
                targets.append(target)
        return tuple(targets)

    def _reconcile_records(
        self,
        session: Session,
        rebuild: CollectionRebuildModel,
        record_ids: tuple[str, ...],
    ) -> tuple[tuple[ImageIndexingTarget, ...], tuple[MilvusVectorIdentityV1, ...]]:
        candidate = self._candidate(session, rebuild)
        spec = self._spec(candidate)
        upserts = []
        deletes = []
        for record_id in dict.fromkeys(record_ids):
            source = self._indexing.load_rebuild_source(
                workspace_id=rebuild.workspace_id,
                embedding_record_id=record_id,
                candidate_collection_id=candidate.id,
                candidate_spec=spec,
            )
            placement = session.get(
                CollectionRebuildPlacementModel,
                {"rebuild_id": rebuild.id, "embedding_record_id": record_id},
            )
            if source is not None:
                upserts.append(source)
            elif placement is not None:
                deletes.append(
                    MilvusVectorIdentityV1(
                        collection_name=candidate.physical_name,
                        embedding_record_id=placement.embedding_record_id,
                        milvus_primary_key=placement.milvus_primary_key,
                        input_hash=placement.input_hash,
                        embedding_spec_sha256=placement.embedding_spec_hash,
                        write_generation=placement.write_generation,
                    )
                )
        return tuple(upserts), tuple(deletes)

    def _replay_events(
        self, session: Session, rebuild: CollectionRebuildModel, limit: int
    ) -> tuple[OutboxEventModel, ...]:
        if rebuild.replay_watermark is None:
            return ()
        cursor_filter = ()
        if rebuild.replay_cursor_occurred_at is not None:
            cursor_filter = (
                or_(
                    OutboxEventModel.occurred_at > rebuild.replay_cursor_occurred_at,
                    and_(
                        OutboxEventModel.occurred_at == rebuild.replay_cursor_occurred_at,
                        OutboxEventModel.id > rebuild.replay_cursor_event_id,
                    ),
                ),
            )
        return tuple(
            session.scalars(
                select(OutboxEventModel)
                .where(
                    OutboxEventModel.workspace_id == rebuild.workspace_id,
                    OutboxEventModel.event_type.in_(REBUILD_REPLAY_EVENT_TYPES),
                    # Fail safe at the DATETIME(6) boundary: a duplicate replay is
                    # generation-fenced, while a missed fact could restore access.
                    OutboxEventModel.occurred_at >= rebuild.snapshot_watermark,
                    OutboxEventModel.occurred_at <= rebuild.replay_watermark,
                    *cursor_filter,
                )
                .order_by(OutboxEventModel.occurred_at, OutboxEventModel.id)
                .limit(limit)
            )
        )

    def _candidate_spec_for_rebuild(self, rebuild_id: str) -> CollectionSpec:
        with self._session_factory() as session:
            rebuild = session.get(CollectionRebuildModel, rebuild_id)
            if rebuild is None:
                raise ValueError("collection rebuild disappeared")
            return self._spec(self._candidate(session, rebuild))

    @staticmethod
    def _target(session: Session, rebuild: CollectionRebuildModel) -> CollectionRebuildTarget:
        source = session.get(CollectionRegistryModel, rebuild.source_collection_id)
        candidate = session.get(CollectionRegistryModel, rebuild.candidate_collection_id)
        if source is None or candidate is None:
            raise ValueError("collection rebuild registry facts are incomplete")
        return CollectionRebuildTarget(
            id=rebuild.id,
            workspace_id=rebuild.workspace_id,
            operation_id=rebuild.operation_id,
            source_collection_id=source.id,
            source_collection_name=source.physical_name,
            candidate_collection_id=candidate.id,
            candidate_collection_name=candidate.physical_name,
            collection_spec=MySqlCollectionRebuildRepository._spec(candidate),
            model_id=candidate.model_id,
            state=CollectionRebuildState(rebuild.state),
            generation=rebuild.generation,
            processed_count=rebuild.processed_count,
        )

    @staticmethod
    def _spec(collection: CollectionRegistryModel) -> CollectionSpec:
        return CollectionSpec.create(
            model_family=collection.model_family,
            pinned_revision=collection.pinned_revision,
            dimension=collection.dimension,
            vector_kind=VectorKind(collection.vector_kind),
            schema_version=collection.schema_version,
            index_spec_version=collection.index_spec_version,
        )

    @staticmethod
    def _candidate(session: Session, rebuild: CollectionRebuildModel) -> CollectionRegistryModel:
        candidate = session.get(CollectionRegistryModel, rebuild.candidate_collection_id)
        if candidate is None or candidate.rebuild_id != rebuild.id:
            raise ValueError("candidate Collection identity is inconsistent")
        return candidate

    @staticmethod
    def _lock(session: Session, target: CollectionRebuildTarget) -> CollectionRebuildModel:
        rebuild = session.scalar(
            select(CollectionRebuildModel)
            .where(
                CollectionRebuildModel.id == target.id,
                CollectionRebuildModel.workspace_id == target.workspace_id,
                CollectionRebuildModel.generation == target.generation,
            )
            .with_for_update()
        )
        if rebuild is None:
            raise ValueError("collection rebuild command generation is stale")
        return rebuild

    @staticmethod
    def _database_now(session: Session) -> datetime:
        now = session.scalar(select(literal_column("UTC_TIMESTAMP(6)")))
        if now is None:
            raise RuntimeError("database time is unavailable")
        return now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)

    @staticmethod
    def _encode_replay_cursor(rebuild: CollectionRebuildModel) -> str | None:
        if rebuild.replay_cursor_occurred_at is None or rebuild.replay_cursor_event_id is None:
            return None
        return f"{rebuild.replay_cursor_occurred_at.isoformat()}|{rebuild.replay_cursor_event_id}"

    @staticmethod
    def _set_replay_cursor(rebuild: CollectionRebuildModel, cursor: str | None) -> None:
        if cursor is None:
            return
        occurred_at, event_id = cursor.split("|", 1)
        parsed = datetime.fromisoformat(occurred_at)
        rebuild.replay_cursor_occurred_at = (
            parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
        )
        rebuild.replay_cursor_event_id = event_id

    @staticmethod
    def _progress(
        session: Session,
        rebuild: CollectionRebuildModel,
        message_code: str,
        now: datetime,
    ) -> None:
        latest = session.scalar(
            select(CollectionRebuildProgressModel.sequence)
            .where(CollectionRebuildProgressModel.rebuild_id == rebuild.id)
            .order_by(CollectionRebuildProgressModel.sequence.desc())
            .limit(1)
        )
        sequence = int(latest or 0) + 1
        session.add(
            CollectionRebuildProgressModel(
                id=new_uuid7(),
                rebuild_id=rebuild.id,
                sequence=sequence,
                state=rebuild.state,
                processed_count=rebuild.processed_count,
                message_code=message_code,
                observed_at=now,
            )
        )
        payload = CollectionRebuildProgressedPayload(
            workspace_id=rebuild.workspace_id,
            rebuild_id=rebuild.id,
            operation_id=rebuild.operation_id,
            generation=rebuild.generation,
            state=rebuild.state,
            processed_count=rebuild.processed_count,
        )
        OutboxRepository(session).add(
            OutboxEvent(
                envelope=EventEnvelope.create(
                    event_type=COLLECTION_REBUILD_PROGRESSED_V1.event_type.value,
                    schema_version=COLLECTION_REBUILD_PROGRESSED_V1.schema_version,
                    aggregate_type="collection_rebuild",
                    aggregate_id=rebuild.id,
                    aggregate_version=rebuild.version,
                    trace_id=rebuild.operation_id,
                    payload=payload.model_dump(mode="json"),
                    now=now,
                ),
                available_at=now,
                workspace_id=rebuild.workspace_id,
            )
        )

    @staticmethod
    def _enqueue(
        session: Session,
        rebuild: CollectionRebuildModel,
        command: CollectionRebuildCommand,
        now: datetime,
    ) -> None:
        payload = CollectionRebuildRequestedPayload(
            workspace_id=rebuild.workspace_id,
            rebuild_id=rebuild.id,
            operation_id=rebuild.operation_id,
            generation=rebuild.generation,
            command=command,
        )
        OutboxRepository(session).add(
            OutboxEvent(
                envelope=EventEnvelope.create(
                    event_type=COLLECTION_REBUILD_REQUESTED_V1.event_type.value,
                    schema_version=COLLECTION_REBUILD_REQUESTED_V1.schema_version,
                    aggregate_type="collection_rebuild",
                    aggregate_id=rebuild.id,
                    aggregate_version=rebuild.version,
                    trace_id=rebuild.operation_id,
                    payload=payload.model_dump(mode="json"),
                    now=now,
                ),
                available_at=now,
                workspace_id=rebuild.workspace_id,
            )
        )
