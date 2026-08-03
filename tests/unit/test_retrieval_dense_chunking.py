from commercevision_contracts import MilvusAnnSearchHitV1
from commercevision_domain import VectorKind
from commercevision_retrieval import ChunkedMilvusAnnSearch

IDS = tuple(f"0198f4d8-1f7c-7b2d-8da9-214a92a884a{index}" for index in range(1, 6))
ASSET_VERSION_IDS = tuple(f"0198f4d8-1f7c-7b2d-8da9-214a92a884b{index}" for index in range(1, 6))


class _RecordingIndex:
    def __init__(self) -> None:
        self.requests = []

    def search(self, request):
        self.requests.append(request)
        scores = {candidate_id: index / 10 for index, candidate_id in enumerate(IDS, start=1)}
        return tuple(
            MilvusAnnSearchHitV1(
                embedding_record_id=candidate_id,
                asset_version_id=ASSET_VERSION_IDS[IDS.index(candidate_id)],
                input_hash="a" * 64,
                embedding_spec_sha256="b" * 64,
                write_generation=1,
                score=scores[candidate_id],
            )
            for candidate_id in sorted(
                request.eligible_embedding_record_ids,
                key=scores.__getitem__,
                reverse=True,
            )[: request.limit]
        )


def test_dense_recall_chunks_the_entire_eligible_fence_and_globally_merges_hits() -> None:
    index = _RecordingIndex()
    search = ChunkedMilvusAnnSearch(index=index, maximum_filter_ids=2)

    hits = search.search(
        collection_name="cv_product_fused_fixture_4d_v1",
        workspace_id="workspace-retrieval",
        vector_kind=VectorKind.PRODUCT_FUSED,
        eligible_embedding_record_ids=IDS,
        query_vector=(0.1, 0.2, 0.3, 0.4),
        limit=3,
    )

    assert [hit.embedding_record_id for hit in hits] == [IDS[4], IDS[3], IDS[2]]
    assert len(index.requests) == 3
    assert {
        candidate
        for request in index.requests
        for candidate in request.eligible_embedding_record_ids
    } == set(IDS)
    assert all(len(request.eligible_embedding_record_ids) <= 2 for request in index.requests)
    assert all(
        request.limit <= len(request.eligible_embedding_record_ids) for request in index.requests
    )
