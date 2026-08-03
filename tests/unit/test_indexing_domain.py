from dataclasses import replace

import pytest
from commercevision_domain import (
    CollectionSpec,
    EmbeddingRecord,
    EmbeddingState,
    VectorKind,
    compute_embedding_input_hash,
    new_uuid7,
)


def test_collection_identity_isolated_by_every_vector_compatibility_dimension() -> None:
    spec = CollectionSpec.create(
        model_family="qwen3-vl-embedding",
        pinned_revision="2026-06-30",
        dimension=1024,
        vector_kind=VectorKind.IMAGE,
        schema_version=1,
        index_spec_version="hnsw-cosine-v1",
    )

    assert spec.logical_key == ("qwen3-vl-embedding:2026-06-30:1024:IMAGE:1:hnsw-cosine-v1")
    assert spec.physical_name == "cv_image_d7083964a52ed775"

    variants = (
        replace(spec, model_family="qwen3-vl-embedding-next"),
        replace(spec, pinned_revision="2026-07-01"),
        replace(spec, dimension=2048),
        replace(spec, vector_kind=VectorKind.PRODUCT_FUSED),
        replace(spec, schema_version=2),
        replace(spec, index_spec_version="hnsw-cosine-v2"),
    )
    assert all(candidate.logical_key != spec.logical_key for candidate in variants)
    assert all(candidate.physical_name != spec.physical_name for candidate in variants)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("model_family", ""),
        ("pinned_revision", "latest"),
        ("dimension", 0),
        ("schema_version", 0),
        ("index_spec_version", ""),
    ],
)
def test_collection_identity_rejects_unpinned_or_invalid_components(
    field: str,
    value: object,
) -> None:
    arguments: dict[str, object] = {
        "model_family": "qwen3-vl-embedding",
        "pinned_revision": "2026-06-30",
        "dimension": 1024,
        "vector_kind": VectorKind.IMAGE,
        "schema_version": 1,
        "index_spec_version": "hnsw-cosine-v1",
    }
    arguments[field] = value

    with pytest.raises(ValueError):
        CollectionSpec.create(**arguments)  # type: ignore[arg-type]


def test_image_input_hash_and_embedding_identity_are_replay_stable() -> None:
    input_hash = compute_embedding_input_hash(
        content_sha256="a" * 64,
        provider="alibaba-model-studio",
        preprocessing_version="image-preprocess-v1",
        model_configuration_version="embedding-config-v1",
        vector_kind=VectorKind.IMAGE,
    )
    asset_version_id = new_uuid7()
    collection_id = new_uuid7()

    first = EmbeddingRecord.create(
        workspace_id="workspace-index",
        asset_id=new_uuid7(),
        asset_version_id=asset_version_id,
        asset_version_number=1,
        rights_record_id=new_uuid7(),
        rights_record_version=2,
        collection_id=collection_id,
        embedding_spec_hash="b" * 64,
        input_hash=input_hash,
        vector_kind=VectorKind.IMAGE,
    )
    duplicate = EmbeddingRecord.create(
        workspace_id="workspace-index",
        asset_id=new_uuid7(),
        asset_version_id=asset_version_id,
        asset_version_number=1,
        rights_record_id=new_uuid7(),
        rights_record_version=3,
        collection_id=collection_id,
        embedding_spec_hash="b" * 64,
        input_hash=input_hash,
        vector_kind=VectorKind.IMAGE,
    )

    assert input_hash == "288cac8a1d1b2b851f45cdba348afac122ee4effa09819cc53205ff886a306c1"
    assert first.id == duplicate.id
    assert first.milvus_primary_key == duplicate.milvus_primary_key


def test_generation_specific_milvus_key_does_not_change_logical_record_identity() -> None:
    record = EmbeddingRecord.create(
        workspace_id="workspace-index",
        asset_id=new_uuid7(),
        asset_version_id=new_uuid7(),
        asset_version_number=1,
        rights_record_id=new_uuid7(),
        rights_record_version=1,
        collection_id=new_uuid7(),
        embedding_spec_hash="b" * 64,
        input_hash="c" * 64,
        vector_kind=VectorKind.IMAGE,
    )

    first_generation = record.begin_processing()
    record.mark_stale(write_generation=first_generation, reason="RETRY")
    second_generation = record.begin_processing()

    assert record.milvus_key_for(first_generation) != record.milvus_key_for(second_generation)
    assert record.id in record.milvus_key_for(first_generation)


def test_stale_delete_generation_cannot_remove_a_regranted_vector() -> None:
    record = EmbeddingRecord.create(
        workspace_id="workspace-index",
        asset_id=new_uuid7(),
        asset_version_id=new_uuid7(),
        asset_version_number=1,
        rights_record_id=new_uuid7(),
        rights_record_version=1,
        collection_id=new_uuid7(),
        embedding_spec_hash="b" * 64,
        input_hash="c" * 64,
        vector_kind=VectorKind.IMAGE,
    )

    first_generation = record.begin_processing()
    record.mark_stale(write_generation=first_generation, reason="RIGHTS_INVALID")
    record.begin_delete(write_generation=first_generation)
    second_generation = record.begin_processing()
    record.mark_indexed(
        write_generation=second_generation,
        provider_request_id="provider-2",
        actual_model="qwen3-vl-embedding-2026-06-30",
    )

    assert record.complete_delete(write_generation=first_generation) is False
    assert record.state is EmbeddingState.INDEXED
    assert record.write_generation == second_generation
