from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from commercevision_application import (
    CollectionRebuildRunner,
    CollectionRebuildTarget,
    RebuildValidationExpected,
)
from commercevision_contracts import (
    CollectionRebuildRequestV1,
    MilvusUpsertRequestV1,
    MilvusVectorRowV1,
    collection_create_request,
)
from commercevision_contracts.events import (
    CollectionRebuildCommand,
    CollectionRebuildRequestedPayload,
)
from commercevision_domain import (
    CollectionRebuildState,
    CollectionSpec,
    CollectionState,
    VectorKind,
    collection_instance_name,
    new_uuid7,
)
from commercevision_persistence import (
    MySqlCollectionRebuildControl,
    MySqlCollectionRebuildRepository,
)
from commercevision_persistence.indexing_models import (
    CollectionRebuildModel,
    CollectionRegistryModel,
    RetrievalPolicyPointerModel,
)
from commercevision_persistence.models import OutboxEventModel
from commercevision_retrieval import MilvusVectorIndexAdapter
from sqlalchemy import select

pytestmark = pytest.mark.integration

_MILVUS_URI = os.getenv("CV_TEST_MILVUS_URI", "http://127.0.0.1:19531")


class _UnusedReferences:
    def temporary_input(self, _target):
        raise AssertionError("empty rebuild must not resolve object storage")


class _UnusedProvider:
    def embed(self, _request):
        raise AssertionError("empty rebuild must not call the embedding provider")


class _ValidationRepository:
    def __init__(self, target: CollectionRebuildTarget) -> None:
        self.target = target
        self.report = None

    def load_command(self, _payload):
        return self.target

    def validation_expected(self, _target):
        return RebuildValidationExpected(frozenset())

    def begin_validation(self, target):
        return target

    def complete_validation(self, _target, report):
        self.report = report


def _runner(database, vectors) -> CollectionRebuildRunner:
    return CollectionRebuildRunner(
        repository=MySqlCollectionRebuildRepository(database.session_factory),
        references=_UnusedReferences(),
        provider=_UnusedProvider(),
        vectors=vectors,
        batch_size=2,
        validation_maximum_rows=100,
        validation_sample_size=10,
        minimum_ann_recall_at_10=0.95,
    )


def test_real_candidate_recovers_after_deletion_and_switches_atomically(
    integration_database,
) -> None:
    unique = uuid.uuid4().hex[:12]
    spec = CollectionSpec.create(
        model_family=f"ticket14-{unique}",
        pinned_revision=f"revision-{unique}",
        dimension=4,
        vector_kind=VectorKind.IMAGE,
        schema_version=1,
        index_spec_version="hnsw-cosine-v1",
    )
    source_id = new_uuid7()
    now = datetime.now(UTC)
    vectors = MilvusVectorIndexAdapter(
        uri=_MILVUS_URI,
        timeout_seconds=15,
        readiness_timeout_seconds=10,
    )
    candidate_name = None
    milvus_ready = False
    try:
        try:
            vectors.assert_ready()
        except (ConnectionError, TimeoutError, OSError) as exc:
            pytest.skip(f"Milvus integration service unavailable: {exc}")
        milvus_ready = True
        vectors.ensure_collection(collection_create_request(spec))
        with integration_database.session_factory.begin() as session:
            session.add(
                CollectionRegistryModel(
                    id=source_id,
                    logical_key=spec.logical_key,
                    spec_hash=spec.spec_hash,
                    physical_name=spec.physical_name,
                    model_family=spec.model_family,
                    model_id=f"model-{unique}",
                    pinned_revision=spec.pinned_revision,
                    dimension=spec.dimension,
                    vector_kind=spec.vector_kind.value,
                    schema_version=spec.schema_version,
                    index_spec_version=spec.index_spec_version,
                    dynamic_fields_enabled=False,
                    instance_generation=0,
                    rebuild_id=None,
                    state=CollectionState.ACTIVE.value,
                    is_read_enabled=True,
                    is_write_enabled=True,
                    validation_summary_json={"verified": True},
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                RetrievalPolicyPointerModel(
                    vector_kind=VectorKind.IMAGE.value,
                    collection_id=source_id,
                    retrieval_policy_version="retrieval-policy-v1",
                    version=1,
                    updated_at=now,
                )
            )

        control = MySqlCollectionRebuildControl(
            integration_database.session_factory,
            retirement_delay=timedelta(milliseconds=1),
        )
        response = control.request(
            workspace_id="workspace-ticket14",
            idempotency_key="ticket14-real-rebuild",
            trace_id=new_uuid7(),
            request=CollectionRebuildRequestV1(
                vector_kind=VectorKind.IMAGE,
                model_family=spec.model_family,
                model_id=f"model-{unique}",
                pinned_revision=spec.pinned_revision,
                dimension=spec.dimension,
                schema_version=spec.schema_version,
                index_spec_version=spec.index_spec_version,
                expected_active_collection_version=1,
                expected_policy_pointer_version=1,
            ),
        )
        command = CollectionRebuildRequestedPayload(
            workspace_id="workspace-ticket14",
            rebuild_id=response.id,
            operation_id=response.operation_id,
            generation=1,
            command=CollectionRebuildCommand.CONTINUE,
        )

        # A fresh runner instance per boundary proves checkpoints, not process memory, resume.
        _runner(integration_database, vectors).process(command)
        with integration_database.session_factory() as session:
            rebuild = session.get(CollectionRebuildModel, response.id)
            assert rebuild is not None
            candidate = session.get(CollectionRegistryModel, rebuild.candidate_collection_id)
            assert candidate is not None
            candidate_name = candidate.physical_name
        assert vectors.retire_collection(candidate_name)

        for expected_state in (
            CollectionRebuildState.REPLAYING,
            CollectionRebuildState.RIGHTS_RESCAN,
            CollectionRebuildState.AWAITING_VALIDATION,
        ):
            _runner(integration_database, vectors).process(command)
            current = control.get(workspace_id="workspace-ticket14", rebuild_id=response.id)
            assert current.state is expected_state

        _runner(integration_database, vectors).process(
            command.model_copy(update={"command": CollectionRebuildCommand.VALIDATE})
        )
        ready = control.get(workspace_id="workspace-ticket14", rebuild_id=response.id)
        assert ready.state is CollectionRebuildState.READY
        assert ready.validation is not None and ready.validation.accepted

        # MySQL DATETIME(6) can assign the exact validation watermark to a fact
        # committed at the boundary. It must be replayed, never skipped by `>`.
        with integration_database.session_factory.begin() as session:
            rebuild = session.get(CollectionRebuildModel, response.id)
            assert rebuild is not None and rebuild.validation_watermark is not None
            boundary = rebuild.validation_watermark
            session.add(
                OutboxEventModel(
                    id=new_uuid7(),
                    aggregate_type="asset",
                    aggregate_id=new_uuid7(),
                    event_type="asset.rights.changed",
                    schema_version=1,
                    aggregate_version=1,
                    trace_id=new_uuid7(),
                    payload_json={"asset_id": new_uuid7()},
                    occurred_at=boundary,
                    available_at=boundary,
                    published_at=boundary,
                    publish_attempts=1,
                    workspace_id="workspace-ticket14",
                    replay_attempt=0,
                )
            )

        replaying = control.activate(
            workspace_id="workspace-ticket14",
            rebuild_id=response.id,
            expected_version=ready.version,
            trace_id=new_uuid7(),
        )
        assert replaying.state is CollectionRebuildState.REPLAYING
        with integration_database.session_factory() as session:
            pointer = session.get(RetrievalPolicyPointerModel, VectorKind.IMAGE.value)
            assert pointer is not None and pointer.collection_id == source_id

        _runner(integration_database, vectors).process(command)
        _runner(integration_database, vectors).process(command)
        _runner(integration_database, vectors).process(
            command.model_copy(update={"command": CollectionRebuildCommand.VALIDATE})
        )
        ready = control.get(workspace_id="workspace-ticket14", rebuild_id=response.id)
        assert ready.state is CollectionRebuildState.READY

        retiring = control.activate(
            workspace_id="workspace-ticket14",
            rebuild_id=response.id,
            expected_version=ready.version,
            trace_id=new_uuid7(),
        )
        assert retiring.state is CollectionRebuildState.RETIRING
        with integration_database.session_factory() as session:
            pointer = session.get(RetrievalPolicyPointerModel, VectorKind.IMAGE.value)
            rebuild = session.get(CollectionRebuildModel, response.id)
            source = session.get(CollectionRegistryModel, source_id)
            assert pointer is not None and rebuild is not None and source is not None
            assert pointer.collection_id == rebuild.candidate_collection_id
            assert source.state == CollectionState.RETIRING.value
            assert source.is_read_enabled and not source.is_write_enabled

        with integration_database.session_factory.begin() as session:
            rebuild = session.scalar(
                select(CollectionRebuildModel)
                .where(CollectionRebuildModel.id == response.id)
                .with_for_update()
            )
            assert rebuild is not None
            rebuild.retire_after = datetime(2020, 1, 1, tzinfo=UTC)
        _runner(integration_database, vectors).process(
            command.model_copy(update={"command": CollectionRebuildCommand.RETIRE})
        )
        retired = control.get(workspace_id="workspace-ticket14", rebuild_id=response.id)
        assert retired.state is CollectionRebuildState.RETIRED
    finally:
        if milvus_ready:
            if candidate_name is not None:
                vectors.retire_collection(candidate_name)
            vectors.retire_collection(spec.physical_name)
        vectors.close()


def test_real_validation_rejects_an_unknown_candidate_vector() -> None:
    rebuild_id = new_uuid7()
    embedding_id = new_uuid7()
    asset_version_id = new_uuid7()
    spec = CollectionSpec.create(
        model_family="ticket14-unauthorized",
        pinned_revision=f"revision-{uuid.uuid4().hex[:12]}",
        dimension=4,
        vector_kind=VectorKind.IMAGE,
        schema_version=1,
        index_spec_version="hnsw-cosine-v1",
    )
    candidate_name = collection_instance_name(spec, rebuild_id=rebuild_id)
    vectors = MilvusVectorIndexAdapter(
        uri=_MILVUS_URI,
        timeout_seconds=15,
        readiness_timeout_seconds=10,
    )
    ready = False
    try:
        try:
            vectors.assert_ready()
        except (ConnectionError, TimeoutError, OSError) as exc:
            pytest.skip(f"Milvus integration service unavailable: {exc}")
        ready = True
        vectors.ensure_collection(collection_create_request(spec, collection_name=candidate_name))
        vectors.upsert(
            MilvusUpsertRequestV1(
                collection_name=candidate_name,
                row=MilvusVectorRowV1(
                    embedding_record_id=embedding_id,
                    milvus_primary_key=f"{embedding_id}:g1",
                    asset_version_id=asset_version_id,
                    workspace_id="workspace-ticket14",
                    rights_record_version=1,
                    category="test",
                    brand="",
                    asset_role="REFERENCE",
                    vector_kind=VectorKind.IMAGE,
                    model_configuration_version="fixture-v1",
                    input_hash="1" * 64,
                    embedding_spec_sha256="2" * 64,
                    write_generation=1,
                    indexed_at_epoch_micros=1,
                    vector=[1.0, 0.0, 0.0, 0.0],
                ),
            )
        )
        target = CollectionRebuildTarget(
            id=rebuild_id,
            workspace_id="workspace-ticket14",
            operation_id=rebuild_id,
            source_collection_id=new_uuid7(),
            source_collection_name=spec.physical_name,
            candidate_collection_id=new_uuid7(),
            candidate_collection_name=candidate_name,
            collection_spec=spec,
            model_id="fixture-model",
            state=CollectionRebuildState.AWAITING_VALIDATION,
            generation=1,
            processed_count=0,
        )
        repository = _ValidationRepository(target)
        runner = CollectionRebuildRunner(
            repository=repository,
            references=_UnusedReferences(),
            provider=_UnusedProvider(),
            vectors=vectors,
            batch_size=10,
            validation_maximum_rows=100,
            validation_sample_size=10,
            minimum_ann_recall_at_10=0.95,
        )
        runner.process(
            CollectionRebuildRequestedPayload(
                workspace_id=target.workspace_id,
                rebuild_id=target.id,
                operation_id=target.operation_id,
                generation=1,
                command=CollectionRebuildCommand.VALIDATE,
            )
        )

        assert repository.report is not None
        assert not repository.report.accepted
        assert repository.report.unauthorized_result_count == 1
        assert repository.report.unexpected_primary_key_count == 1
    finally:
        if ready:
            vectors.retire_collection(candidate_name)
        vectors.close()
