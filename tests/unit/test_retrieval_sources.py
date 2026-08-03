import pytest
from commercevision_application import (
    DenseEmbeddingCandidate,
    DenseRetrievalIndexUnavailable,
    DenseRetrievalSource,
    DenseRetrievalTarget,
    ExplicitReferenceRetrievalSource,
    ProviderDenseQueryVectorService,
    RetrievalQueryImageUnavailable,
    RetrievalRecallHit,
    RetrievalSourceUnavailable,
)
from commercevision_contracts import (
    EmbeddingProviderResultV1,
    EmbeddingVectorV1,
    MilvusAnnSearchHitV1,
    RetrievalQueryV1,
)
from commercevision_domain import RetrievalChannel, VectorKind
from commercevision_persistence import MySqlLexicalRetrievalSource, ProductLexicalHit

VERSION_A = "0198f4d8-1f7c-7b2d-8da9-214a92a884a1"
VERSION_B = "0198f4d8-1f7c-7b2d-8da9-214a92a884a2"
VERSION_OUTSIDE = "0198f4d8-1f7c-7b2d-8da9-214a92a884a3"
EMBEDDING_A = "0198f4d8-1f7c-7b2d-8da9-214a92a884b1"
EMBEDDING_B = "0198f4d8-1f7c-7b2d-8da9-214a92a884b2"


def _query() -> RetrievalQueryV1:
    return RetrievalQueryV1.model_validate_json(
        f"""{{
          "workspace_id":"workspace-retrieval",
          "requester_id":"agent:test",
          "product_id":"0198f4d8-1f7c-7b2d-8da9-214a92a884d1",
          "purpose":"RETRIEVAL",
          "provider":"fixture",
          "requires_derivative":false,
          "roles":[],
          "vector_kinds":["PRODUCT_FUSED"],
          "query_text":"Lipstick",
          "explicit_reference_asset_version_ids":["{VERSION_OUTSIDE}","{VERSION_B}"],
          "result_limit":2,
          "candidate_limit":10,
          "retrieval_policy_version":"retrieval-policy-v1"
        }}"""
    )


def test_explicit_source_preserves_request_order_and_intersects_eligible_set() -> None:
    batch = ExplicitReferenceRetrievalSource().recall(
        _query(),
        eligible_asset_version_ids=(VERSION_A, VERSION_B),
        limit=10,
    )

    assert batch.channel is RetrievalChannel.EXPLICIT
    assert batch.hits == (RetrievalRecallHit(asset_version_id=VERSION_B),)


class _LexicalSearch:
    def __init__(self) -> None:
        self.call: dict[str, object] | None = None

    def search_eligible(self, **kwargs: object) -> tuple[ProductLexicalHit, ...]:
        self.call = kwargs
        return (
            ProductLexicalHit(
                search_document_id="document-a",
                asset_version_id=VERSION_B,
                embedding_record_id="embedding-a",
                score=9.5,
            ),
        )


def test_lexical_source_pushes_the_complete_eligible_intersection_into_mysql() -> None:
    search = _LexicalSearch()
    source = MySqlLexicalRetrievalSource(search)

    batch = source.recall(
        _query(),
        eligible_asset_version_ids=(VERSION_A, VERSION_B),
        limit=7,
    )

    assert search.call == {
        "workspace_id": "workspace-retrieval",
        "query": "lipstick",
        "eligible_asset_version_ids": (VERSION_A, VERSION_B),
        "limit": 7,
    }
    assert batch.channel is RetrievalChannel.LEXICAL
    assert batch.hits == (RetrievalRecallHit(asset_version_id=VERSION_B, raw_score=9.5),)


class _DenseCatalog:
    received: tuple[str, ...] | None = None

    def load_target(self, query, *, vector_kind, eligible_asset_version_ids):
        self.received = eligible_asset_version_ids
        return DenseRetrievalTarget(
            collection_name="cv_product_fused_fixture_4d_v1",
            vector_kind=VectorKind.PRODUCT_FUSED,
            dimension=4,
            provider="fixture",
            model_id="qwen3-vl-embedding",
            pinned_revision="embedding-eval-v1",
            model_configuration_version="embedding-config-v1",
            preprocessing_version="product-fused-v1",
            candidates=(
                DenseEmbeddingCandidate(EMBEDDING_A, VERSION_A),
                DenseEmbeddingCandidate(EMBEDDING_B, VERSION_B),
            ),
        )


class _QueryVectors:
    def embed_query(self, query, *, target):
        assert target.dimension == 4
        return (0.1, 0.2, 0.3, 0.4)


class _DenseSearch:
    call: dict[str, object] | None = None

    def search(self, **kwargs):
        self.call = kwargs
        return (
            MilvusAnnSearchHitV1(
                embedding_record_id=EMBEDDING_B,
                asset_version_id=VERSION_B,
                input_hash="a" * 64,
                embedding_spec_sha256="b" * 64,
                write_generation=1,
                score=0.91,
            ),
        )


def test_dense_source_uses_mysql_routing_and_maps_only_eligible_embedding_records() -> None:
    catalog = _DenseCatalog()
    search = _DenseSearch()
    source = DenseRetrievalSource(
        vector_kind=VectorKind.PRODUCT_FUSED,
        catalog=catalog,
        query_vectors=_QueryVectors(),
        search=search,
    )

    batch = source.recall(
        _query(),
        eligible_asset_version_ids=(VERSION_A, VERSION_B),
        limit=2,
    )

    assert catalog.received == (VERSION_A, VERSION_B)
    assert search.call == {
        "collection_name": "cv_product_fused_fixture_4d_v1",
        "workspace_id": "workspace-retrieval",
        "vector_kind": VectorKind.PRODUCT_FUSED,
        "eligible_embedding_record_ids": (EMBEDDING_A, EMBEDDING_B),
        "query_vector": (0.1, 0.2, 0.3, 0.4),
        "limit": 2,
    }
    assert batch.channel is RetrievalChannel.PRODUCT_FUSED_DENSE
    assert batch.hits == (RetrievalRecallHit(asset_version_id=VERSION_B, raw_score=0.91),)


class _UnavailableDenseCatalog:
    @staticmethod
    def load_target(query, *, vector_kind, eligible_asset_version_ids):
        raise DenseRetrievalIndexUnavailable(
            code="DENSE_INDEX_UNAVAILABLE",
            message="dense index has no current records",
        )


class _UnavailableQueryImage:
    @staticmethod
    def embed_query(query, *, target):
        raise RetrievalQueryImageUnavailable(
            code="DENSE_QUERY_IMAGE_UNAVAILABLE",
            message="query image is no longer authorized",
        )


def test_dense_source_reports_missing_index_as_an_explicit_degradation() -> None:
    source = DenseRetrievalSource(
        vector_kind=VectorKind.PRODUCT_FUSED,
        catalog=_UnavailableDenseCatalog(),
        query_vectors=_QueryVectors(),
        search=_DenseSearch(),
    )

    with pytest.raises(RetrievalSourceUnavailable) as failure:
        source.recall(
            _query(),
            eligible_asset_version_ids=(VERSION_A, VERSION_B),
            limit=2,
        )

    assert failure.value.code == "DENSE_INDEX_UNAVAILABLE"


def test_dense_source_degrades_when_query_image_loses_current_authority() -> None:
    source = DenseRetrievalSource(
        vector_kind=VectorKind.PRODUCT_FUSED,
        catalog=_DenseCatalog(),
        query_vectors=_UnavailableQueryImage(),
        search=_DenseSearch(),
    )

    with pytest.raises(RetrievalSourceUnavailable) as failure:
        source.recall(
            _query(),
            eligible_asset_version_ids=(VERSION_A, VERSION_B),
            limit=2,
        )

    assert failure.value.code == "DENSE_QUERY_IMAGE_UNAVAILABLE"


class _EmbeddingProvider:
    request = None

    def embed(self, request):
        self.request = request
        return EmbeddingProviderResultV1(
            vectors=[EmbeddingVectorV1(values=[0.1, 0.2, 0.3, 0.4])],
            provider=request.provider,
            provider_request_id="query-vector-fixture",
            actual_model=request.model_id,
            latency_ms=1,
        )


def test_query_vector_service_uses_active_collection_identity_for_text_query() -> None:
    provider = _EmbeddingProvider()
    service = ProviderDenseQueryVectorService(embedding=provider)
    target = _DenseCatalog().load_target(
        _query(),
        vector_kind=VectorKind.PRODUCT_FUSED,
        eligible_asset_version_ids=(VERSION_A, VERSION_B),
    )
    assert target is not None

    vector = service.embed_query(_query(), target=target)

    assert vector == (0.1, 0.2, 0.3, 0.4)
    assert provider.request.provider == "fixture"
    assert provider.request.model_id == "qwen3-vl-embedding"
    assert provider.request.pinned_revision == "embedding-eval-v1"
    assert provider.request.preprocessing_version == "product-fused-v1"
    assert provider.request.vector_kind is VectorKind.PRODUCT_FUSED
    assert provider.request.controlled_text == "lipstick"
    assert provider.request.images == []
