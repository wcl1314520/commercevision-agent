from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from commercevision_application import ImageIndexingTarget
from commercevision_application.collection_rebuild import (
    CollectionRebuildRunner,
    CollectionRebuildTarget,
    RebuildValidationExpected,
    RebuildWorkBatch,
)
from commercevision_contracts import (
    EmbeddingImageInputV1,
    EmbeddingProviderResultV1,
    EmbeddingVectorV1,
    MilvusAnnSearchHitV1,
    MilvusCollectionSnapshotV1,
)
from commercevision_contracts.events import (
    CollectionRebuildCommand,
    CollectionRebuildRequestedPayload,
)
from commercevision_domain import (
    CollectionRebuildState,
    CollectionSpec,
    RetentionClass,
    VectorKind,
)
from pydantic import SecretStr

REBUILD_ID = "019f8a00-0000-7000-8000-000000000140"
OPERATION_ID = "019f8a00-0000-7000-8000-000000000141"
CANDIDATE_ID = "019f8a00-0000-7000-8000-000000000142"
SOURCE_ID = "019f8a00-0000-7000-8000-000000000143"
EMBEDDING_ID = "019f8a00-0000-7000-8000-000000000150"
ASSET_ID = "019f8a00-0000-7000-8000-000000000151"
ASSET_VERSION_ID = "019f8a00-0000-7000-8000-000000000152"
RIGHTS_ID = "019f8a00-0000-7000-8000-000000000153"


def _spec() -> CollectionSpec:
    return CollectionSpec.create(
        model_family="deterministic-image-embedding",
        pinned_revision="fixture-epoch-v2",
        dimension=2,
        vector_kind=VectorKind.IMAGE,
        schema_version=2,
        index_spec_version="hnsw-cosine-v1",
    )


def _target(state: CollectionRebuildState, *, generation: int = 1) -> CollectionRebuildTarget:
    return CollectionRebuildTarget(
        id=REBUILD_ID,
        workspace_id="catalog-demo",
        operation_id=OPERATION_ID,
        source_collection_id=SOURCE_ID,
        source_collection_name="cv_image_source",
        candidate_collection_id=CANDIDATE_ID,
        candidate_collection_name=f"{_spec().physical_name}_019f8a000000",
        collection_spec=_spec(),
        model_id="deterministic-image-embedding-v2",
        state=state,
        generation=generation,
        processed_count=0,
    )


def _source() -> ImageIndexingTarget:
    return ImageIndexingTarget(
        operation_id=OPERATION_ID,
        embedding_record_id=EMBEDDING_ID,
        workspace_id="catalog-demo",
        asset_id=ASSET_ID,
        asset_version_id=ASSET_VERSION_ID,
        asset_version_number=1,
        rights_record_id=RIGHTS_ID,
        rights_record_version=1,
        collection_id=CANDIDATE_ID,
        collection_spec=_spec(),
        provider="fixture",
        model_id="deterministic-image-embedding-v2",
        model_configuration_version="embedding-config-v2",
        preprocessing_version="image-preprocess-v1",
        input_hash="a" * 64,
        embedding_spec_sha256="b" * 64,
        write_generation=1,
        category="beauty",
        brand="CV",
        asset_role="HERO",
        content_sha256="c" * 64,
        provider_request_id=None,
        actual_model=None,
        indexed_at=datetime(2026, 8, 4, tzinfo=UTC),
        retention_class=RetentionClass.FOUNDATION,
    )


def _command(command: CollectionRebuildCommand = CollectionRebuildCommand.CONTINUE):
    return CollectionRebuildRequestedPayload(
        workspace_id="catalog-demo",
        rebuild_id=REBUILD_ID,
        operation_id=OPERATION_ID,
        generation=1,
        command=command,
    )


class _Repository:
    def __init__(self, target: CollectionRebuildTarget) -> None:
        self.target = target
        self.provisioned = 0
        self.committed_rows = []
        self.validation = None
        self.validation_started = 0
        self.retired = 0
        self.batch = RebuildWorkBatch(upserts=(), deletes=(), cursor=None, phase_complete=True)
        self.expected = RebuildValidationExpected(embedding_record_ids=frozenset())

    def load_command(self, payload):
        if payload.generation != self.target.generation:
            return None
        return self.target

    def begin_provisioning(self, target):
        self.target = replace(target, state=CollectionRebuildState.PROVISIONING)
        return self.target

    def complete_provisioning(self, target):
        self.provisioned += 1

    def load_work_batch(self, target, *, limit):
        assert limit == 25
        return self.batch

    def commit_work_batch(self, target, batch, rows):
        self.committed_rows.extend(rows)

    def validation_expected(self, target):
        return self.expected

    def begin_validation(self, target):
        self.validation_started += 1
        self.target = replace(target, state=CollectionRebuildState.VALIDATING)
        return self.target

    def complete_validation(self, target, report):
        self.validation = report

    def complete_retirement(self, target):
        self.retired += 1

    def mark_failed(self, target, *, code):
        raise AssertionError(code)


class _References:
    def temporary_input(self, target):
        return EmbeddingImageInputV1(
            asset_version_id=target.asset_version_id,
            content_sha256=target.content_sha256,
            byte_size=4,
            url=SecretStr("https://objects.example/input"),
            expires_at=datetime(2026, 8, 4, 0, 1, tzinfo=UTC),
        )


class _Provider:
    def embed(self, request):
        return _provider_result()


def _provider_result() -> EmbeddingProviderResultV1:
    return EmbeddingProviderResultV1(
        vectors=[EmbeddingVectorV1(values=[1.0, 0.0])],
        provider="fixture",
        provider_request_id="provider-request-1",
        actual_model="deterministic-image-embedding-v2",
        latency_ms=1,
    )


class _Vectors:
    def __init__(self) -> None:
        self.ensured = []
        self.upserts = []
        self.snapshot = MilvusCollectionSnapshotV1(row_count=0, rows=[])
        self.retirements = []

    def ensure_collection(self, request):
        self.ensured.append(request)

    def upsert(self, request):
        self.upserts.append(request)

    def delete_if_generation(self, identity):
        return True

    def collection_snapshot(self, **_kwargs):
        return self.snapshot

    def search(self, request):
        row = self.snapshot.rows[0]
        return (
            MilvusAnnSearchHitV1(
                embedding_record_id=row.embedding_record_id,
                asset_version_id=row.asset_version_id,
                input_hash=row.input_hash,
                embedding_spec_sha256=row.embedding_spec_sha256,
                write_generation=row.write_generation,
                score=1.0,
            ),
        )

    def retire_collection(self, collection_name):
        self.retirements.append(collection_name)
        return True


def _runner(repository, vectors):
    return CollectionRebuildRunner(
        repository=repository,
        references=_References(),
        provider=_Provider(),
        vectors=vectors,
        batch_size=25,
        validation_maximum_rows=100,
        validation_sample_size=10,
        minimum_ann_recall_at_10=0.95,
    )


def test_provisioning_creates_only_the_candidate_and_durably_advances() -> None:
    repository = _Repository(_target(CollectionRebuildState.REQUESTED))
    vectors = _Vectors()

    _runner(repository, vectors).process(_command())

    assert [request.collection_name for request in vectors.ensured] == [
        _target(CollectionRebuildState.REQUESTED).candidate_collection_name
    ]
    assert repository.provisioned == 1


def test_backfill_upserts_the_candidate_and_checkpoints_only_after_external_success() -> None:
    repository = _Repository(_target(CollectionRebuildState.BACKFILLING))
    repository.batch = RebuildWorkBatch(
        upserts=(_source(),),
        deletes=(),
        cursor=EMBEDDING_ID,
        phase_complete=False,
    )
    vectors = _Vectors()

    _runner(repository, vectors).process(_command())

    assert len(vectors.ensured) == 1
    assert len(vectors.upserts) == 1
    assert vectors.upserts[0].collection_name == repository.target.candidate_collection_name
    assert vectors.upserts[0].row.embedding_record_id == EMBEDDING_ID
    assert [row.embedding_record_id for row in repository.committed_rows] == [EMBEDDING_ID]


def test_validation_rejects_any_unexpected_vector_as_unauthorized() -> None:
    repository = _Repository(_target(CollectionRebuildState.AWAITING_VALIDATION))
    vectors = _Vectors()
    source = _source()
    result = _provider_result()
    from commercevision_application import build_milvus_upsert_request

    vectors.snapshot = MilvusCollectionSnapshotV1(
        row_count=1,
        rows=[
            build_milvus_upsert_request(
                source,
                result,
                collection_name=repository.target.candidate_collection_name,
            ).row
        ],
    )

    _runner(repository, vectors).process(_command(CollectionRebuildCommand.VALIDATE))

    assert repository.validation_started == 1
    assert repository.validation is not None
    assert not repository.validation.accepted
    assert repository.validation.unauthorized_result_count == 1


def test_validation_accepts_exact_keys_visibility_and_ann_recall() -> None:
    repository = _Repository(_target(CollectionRebuildState.AWAITING_VALIDATION))
    repository.expected = RebuildValidationExpected(embedding_record_ids=frozenset({EMBEDDING_ID}))
    vectors = _Vectors()
    source = _source()
    result = _provider_result()
    from commercevision_application import build_milvus_upsert_request

    vectors.snapshot = MilvusCollectionSnapshotV1(
        row_count=1,
        rows=[
            build_milvus_upsert_request(
                source,
                result,
                collection_name=repository.target.candidate_collection_name,
            ).row
        ],
    )

    _runner(repository, vectors).process(_command(CollectionRebuildCommand.VALIDATE))

    assert repository.validation is not None and repository.validation.accepted
    assert repository.validation.ann_recall_at_10 == 1.0


def test_retirement_drops_only_the_recorded_old_collection() -> None:
    repository = _Repository(_target(CollectionRebuildState.RETIRING))
    vectors = _Vectors()

    _runner(repository, vectors).process(_command(CollectionRebuildCommand.RETIRE))

    assert vectors.retirements == [repository.target.source_collection_name]
    assert repository.retired == 1
