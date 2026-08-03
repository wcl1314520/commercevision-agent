import pytest
from commercevision_domain import (
    RetrievalCandidate,
    RetrievalChannel,
    RetrievalPolicy,
    apply_bounded_rerank,
    bound_retrieval_candidates,
    deduplicate_retrieval_candidates,
    reciprocal_rank_fuse,
)

VERSION_A = "0198f4d8-1f7c-7b2d-8da9-214a92a884a1"
VERSION_B = "0198f4d8-1f7c-7b2d-8da9-214a92a884a2"
VERSION_C = "0198f4d8-1f7c-7b2d-8da9-214a92a884a3"
ASSET_A = "0198f4d8-1f7c-7b2d-8da9-214a92a884b1"
ASSET_B = "0198f4d8-1f7c-7b2d-8da9-214a92a884b2"
ASSET_C = "0198f4d8-1f7c-7b2d-8da9-214a92a884b3"


def _policy() -> RetrievalPolicy:
    return RetrievalPolicy(
        version="retrieval-policy-v1",
        rrf_k=60,
        channel_weights={
            RetrievalChannel.IMAGE_DENSE: 1.0,
            RetrievalChannel.PRODUCT_FUSED_DENSE: 1.0,
            RetrievalChannel.LEXICAL: 1.0,
            RetrievalChannel.BRAND_PROFILE: 1.5,
            RetrievalChannel.EXPLICIT: 2.0,
        },
        maximum_business_adjustment=0.25,
    )


def test_versioned_rrf_uses_ranks_and_never_adds_raw_channel_scores() -> None:
    fused = reciprocal_rank_fuse(
        rankings={
            RetrievalChannel.IMAGE_DENSE: (VERSION_A, VERSION_B),
            RetrievalChannel.LEXICAL: (VERSION_B, VERSION_A),
            RetrievalChannel.EXPLICIT: (VERSION_C,),
        },
        policy=_policy(),
        business_adjustments={VERSION_C: 0.1},
    )

    assert [candidate.asset_version_id for candidate in fused] == [
        VERSION_C,
        VERSION_A,
        VERSION_B,
    ]
    assert fused[0].channel_ranks == {RetrievalChannel.EXPLICIT: 1}
    assert fused[0].rrf_score == pytest.approx(2 / 61)
    assert fused[0].final_score == pytest.approx((2 / 61) + 0.1)


def test_reranker_must_return_an_exact_permutation_of_supplied_candidates() -> None:
    assert apply_bounded_rerank((VERSION_A, VERSION_B), (VERSION_B, VERSION_A)) == (
        VERSION_B,
        VERSION_A,
    )
    with pytest.raises(ValueError, match="permutation"):
        apply_bounded_rerank((VERSION_A, VERSION_B), (VERSION_A, VERSION_C))


def test_deduplication_prefers_required_brand_member_for_identical_content() -> None:
    ranked = (
        RetrievalCandidate(
            asset_id=ASSET_A,
            asset_version_id=VERSION_A,
            content_sha256="a" * 64,
            required_brand_member=False,
        ),
        RetrievalCandidate(
            asset_id=ASSET_B,
            asset_version_id=VERSION_B,
            content_sha256="a" * 64,
            required_brand_member=True,
        ),
        RetrievalCandidate(
            asset_id=ASSET_C,
            asset_version_id=VERSION_C,
            content_sha256="c" * 64,
            required_brand_member=False,
        ),
    )

    deduplicated = deduplicate_retrieval_candidates(ranked)

    assert [candidate.asset_version_id for candidate in deduplicated] == [VERSION_B, VERSION_C]


def test_candidate_bound_preserves_required_brand_members_and_fusion_order() -> None:
    ranked = (
        RetrievalCandidate(ASSET_A, VERSION_A, "a" * 64),
        RetrievalCandidate(ASSET_C, VERSION_C, "c" * 64),
        RetrievalCandidate(ASSET_B, VERSION_B, "b" * 64, required_brand_member=True),
    )

    bounded = bound_retrieval_candidates(ranked, limit=2)

    assert [candidate.asset_version_id for candidate in bounded] == [VERSION_A, VERSION_B]
