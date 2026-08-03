from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest
from commercevision_contracts import (
    MilvusAnnSearchRequestV1,
    MilvusCollectionCreateRequestV1,
    MilvusUpsertRequestV1,
    MilvusVectorIdentityV1,
    MilvusVectorRowV1,
    collection_create_request,
)
from commercevision_domain import CollectionSpec, VectorKind, new_uuid7
from commercevision_retrieval import MilvusVectorIndexAdapter

pytestmark = pytest.mark.integration

_MILVUS_URI = os.getenv("CV_TEST_MILVUS_URI", "http://127.0.0.1:19531")
_OWNED_PREFIX = "cv_ticket09_"
_FUSED_PREFIX = "cv_ticket10_"


def _collection_request() -> MilvusCollectionCreateRequestV1:
    base = collection_create_request(
        CollectionSpec.create(
            model_family="qwen3-vl-embedding",
            pinned_revision="2026-06-30",
            dimension=4,
            vector_kind=VectorKind.IMAGE,
            schema_version=1,
            index_spec_version="hnsw-cosine-v1",
        )
    )
    return base.model_copy(update={"collection_name": f"{_OWNED_PREFIX}{uuid.uuid4().hex}"})


def _fused_collection_request() -> MilvusCollectionCreateRequestV1:
    base = collection_create_request(
        CollectionSpec.create(
            model_family="qwen3-vl-embedding",
            pinned_revision="2026-06-30",
            dimension=4,
            vector_kind=VectorKind.PRODUCT_FUSED,
            schema_version=1,
            index_spec_version="hnsw-cosine-v1",
        )
    )
    return base.model_copy(update={"collection_name": f"{_FUSED_PREFIX}{uuid.uuid4().hex}"})


@pytest.fixture
def real_collection() -> Iterator[tuple[MilvusVectorIndexAdapter, MilvusCollectionCreateRequestV1]]:
    request = _collection_request()
    adapter = MilvusVectorIndexAdapter(
        uri=_MILVUS_URI,
        timeout_seconds=15,
        readiness_timeout_seconds=2,
    )
    try:
        yield adapter, request
    finally:
        try:
            adapter.close()
        finally:
            assert request.collection_name.startswith(_OWNED_PREFIX)
            from pymilvus import MilvusClient

            admin = MilvusClient(uri=_MILVUS_URI, timeout=5)
            try:
                if admin.has_collection(
                    collection_name=request.collection_name,
                    timeout=5,
                    retry_times=0,
                    retry_on_rate_limit=False,
                ):
                    admin.drop_collection(
                        collection_name=request.collection_name,
                        timeout=5,
                    )
            finally:
                admin.close()


@pytest.fixture
def real_fused_collection() -> Iterator[
    tuple[MilvusVectorIndexAdapter, MilvusCollectionCreateRequestV1]
]:
    request = _fused_collection_request()
    adapter = MilvusVectorIndexAdapter(
        uri=_MILVUS_URI,
        timeout_seconds=15,
        readiness_timeout_seconds=2,
    )
    try:
        yield adapter, request
    finally:
        try:
            adapter.close()
        finally:
            assert request.collection_name.startswith(_FUSED_PREFIX)
            from pymilvus import MilvusClient

            admin = MilvusClient(uri=_MILVUS_URI, timeout=5)
            try:
                if admin.has_collection(
                    collection_name=request.collection_name,
                    timeout=5,
                    retry_times=0,
                    retry_on_rate_limit=False,
                ):
                    admin.drop_collection(
                        collection_name=request.collection_name,
                        timeout=5,
                    )
            finally:
                admin.close()


def _upsert_request(
    *,
    collection_name: str,
    embedding_record_id: str,
    generation: int,
    asset_version_id: str | None = None,
    vector_kind: VectorKind = VectorKind.IMAGE,
    vector: list[float] | None = None,
) -> MilvusUpsertRequestV1:
    return MilvusUpsertRequestV1(
        collection_name=collection_name,
        row=MilvusVectorRowV1(
            embedding_record_id=embedding_record_id,
            milvus_primary_key=f"{embedding_record_id}:g{generation}",
            asset_version_id=asset_version_id or new_uuid7(),
            workspace_id="workspace-index-integration",
            rights_record_version=4,
            category="BEAUTY",
            brand="Example",
            asset_role="HERO",
            vector_kind=vector_kind,
            model_configuration_version="embedding-config-v1",
            input_hash="a" * 64,
            embedding_spec_sha256="b" * 64,
            write_generation=generation,
            indexed_at_epoch_micros=1_785_456_000_000_000,
            vector=vector or [0.1, 0.2, 0.3, 0.4],
        ),
    )


def _identity(request: MilvusUpsertRequestV1) -> MilvusVectorIdentityV1:
    return MilvusVectorIdentityV1(
        collection_name=request.collection_name,
        embedding_record_id=request.row.embedding_record_id,
        milvus_primary_key=request.row.milvus_primary_key,
        input_hash=request.row.input_hash,
        embedding_spec_sha256=request.row.embedding_spec_sha256,
        write_generation=request.row.write_generation,
    )


def test_real_milvus_repeated_upsert_is_proven_by_exact_generation_pk(
    real_collection: tuple[
        MilvusVectorIndexAdapter,
        MilvusCollectionCreateRequestV1,
    ],
) -> None:
    adapter, collection = real_collection
    request = _upsert_request(
        collection_name=collection.collection_name,
        embedding_record_id=new_uuid7(),
        generation=2,
    )

    adapter.ensure_collection(collection)
    adapter.upsert(request)
    adapter.upsert(request)

    assert adapter.prove(_identity(request)).matches(_identity(request))


def test_real_milvus_new_then_late_old_upsert_and_old_delete_preserves_new(
    real_collection: tuple[
        MilvusVectorIndexAdapter,
        MilvusCollectionCreateRequestV1,
    ],
) -> None:
    adapter, collection = real_collection
    record_id = new_uuid7()
    old_request = _upsert_request(
        collection_name=collection.collection_name,
        embedding_record_id=record_id,
        generation=2,
    )
    new_request = _upsert_request(
        collection_name=collection.collection_name,
        embedding_record_id=record_id,
        generation=3,
    )
    adapter.ensure_collection(collection)
    adapter.upsert(new_request)
    adapter.upsert(old_request)

    assert adapter.delete_if_generation(_identity(old_request)) is True
    assert adapter.prove(_identity(old_request)).exists is False
    assert adapter.prove(_identity(new_request)).matches(_identity(new_request))


def test_real_milvus_concurrent_ensure_converges_on_one_exact_collection(
    real_collection: tuple[
        MilvusVectorIndexAdapter,
        MilvusCollectionCreateRequestV1,
    ],
) -> None:
    first, collection = real_collection
    second = MilvusVectorIndexAdapter(
        uri=_MILVUS_URI,
        timeout_seconds=15,
        readiness_timeout_seconds=2,
    )
    start = threading.Barrier(2)

    def ensure_after_barrier(adapter: MilvusVectorIndexAdapter) -> None:
        start.wait(timeout=5)
        adapter.ensure_collection(collection)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(
                executor.map(
                    ensure_after_barrier,
                    (first, second),
                )
            )
        assert outcomes == [None, None]
    finally:
        second.close()


@pytest.mark.parametrize("literal_query", ["鎏金口红", "summer lipstick", "鎏金 summer"])
def test_real_product_fused_ann_returns_only_mysql_eligible_literal_fixture(
    real_fused_collection: tuple[
        MilvusVectorIndexAdapter,
        MilvusCollectionCreateRequestV1,
    ],
    literal_query: str,
) -> None:
    adapter, collection = real_fused_collection
    lipstick_record_id = new_uuid7()
    lipstick_asset_version_id = new_uuid7()
    car_record_id = new_uuid7()
    adapter.ensure_collection(collection)
    adapter.upsert(
        _upsert_request(
            collection_name=collection.collection_name,
            embedding_record_id=lipstick_record_id,
            generation=1,
            asset_version_id=lipstick_asset_version_id,
            vector_kind=VectorKind.PRODUCT_FUSED,
            vector=[1.0, 0.0, 0.0, 0.0],
        )
    )
    adapter.upsert(
        _upsert_request(
            collection_name=collection.collection_name,
            embedding_record_id=car_record_id,
            generation=1,
            vector_kind=VectorKind.PRODUCT_FUSED,
            vector=[0.0, 1.0, 0.0, 0.0],
        )
    )

    query_vectors = {
        "鎏金口红": [1.0, 0.0, 0.0, 0.0],
        "summer lipstick": [1.0, 0.0, 0.0, 0.0],
        "鎏金 summer": [1.0, 0.0, 0.0, 0.0],
    }
    hits = adapter.search(
        MilvusAnnSearchRequestV1(
            collection_name=collection.collection_name,
            workspace_id="workspace-index-integration",
            vector_kind=VectorKind.PRODUCT_FUSED,
            eligible_embedding_record_ids=[lipstick_record_id],
            query_vector=query_vectors[literal_query],
            limit=1,
        )
    )

    assert len(hits) == 1
    assert hits[0].embedding_record_id == lipstick_record_id
    assert hits[0].asset_version_id == lipstick_asset_version_id
    assert hits[0].score > 0.99
