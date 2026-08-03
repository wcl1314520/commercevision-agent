from datetime import UTC, datetime

import pytest
from commercevision_application import (
    EligibleRetrievalAsset,
    RetrievalApplicationService,
    RetrievalEligibility,
    RetrievalRecallBatch,
    RetrievalRecallHit,
    RetrievalRerankerUnavailable,
    RetrievalSourceUnavailable,
)
from commercevision_contracts import RetrievalQueryV1
from commercevision_domain import RetentionClass, RetrievalChannel, RetrievalPolicy

VERSION_A = "0198f4d8-1f7c-7b2d-8da9-214a92a884a1"
VERSION_B = "0198f4d8-1f7c-7b2d-8da9-214a92a884a2"
VERSION_C = "0198f4d8-1f7c-7b2d-8da9-214a92a884a3"
VERSION_OUTSIDE = "0198f4d8-1f7c-7b2d-8da9-214a92a884a4"
ASSET_A = "0198f4d8-1f7c-7b2d-8da9-214a92a884b1"
ASSET_B = "0198f4d8-1f7c-7b2d-8da9-214a92a884b2"
ASSET_C = "0198f4d8-1f7c-7b2d-8da9-214a92a884b3"
RIGHTS_A = "0198f4d8-1f7c-7b2d-8da9-214a92a884c1"
RIGHTS_B = "0198f4d8-1f7c-7b2d-8da9-214a92a884c2"
RIGHTS_C = "0198f4d8-1f7c-7b2d-8da9-214a92a884c3"
PRODUCT_ID = "0198f4d8-1f7c-7b2d-8da9-214a92a884d1"
DECIDED_AT = datetime(2026, 8, 3, tzinfo=UTC)


def _asset(asset_id: str, version_id: str, rights_id: str, sha: str) -> EligibleRetrievalAsset:
    return EligibleRetrievalAsset(
        asset_id=asset_id,
        asset_version_id=version_id,
        content_sha256=sha * 64,
        product_id=PRODUCT_ID,
        category="BEAUTY",
        brand="星河",
        role="HERO",
        rights_record_id=rights_id,
        rights_record_version=1,
        retention_class=RetentionClass.FOUNDATION,
    )


ITEMS = (
    _asset(ASSET_A, VERSION_A, RIGHTS_A, "a"),
    _asset(ASSET_B, VERSION_B, RIGHTS_B, "b"),
    _asset(ASSET_C, VERSION_C, RIGHTS_C, "c"),
)


def _query() -> RetrievalQueryV1:
    return RetrievalQueryV1.model_validate_json(
        """{
          "workspace_id":"workspace-retrieval",
          "requester_id":"agent:test",
          "product_id":"0198f4d8-1f7c-7b2d-8da9-214a92a884d1",
          "purpose":"RETRIEVAL",
          "provider":"fixture",
          "requires_derivative":false,
          "roles":["HERO"],
          "vector_kinds":["PRODUCT_FUSED"],
          "query_text":"lipstick",
          "explicit_reference_asset_version_ids":[],
          "result_limit":2,
          "candidate_limit":10,
          "retrieval_policy_version":"retrieval-policy-v1"
        }"""
    )


def _policy() -> RetrievalPolicy:
    return RetrievalPolicy(
        version="retrieval-policy-v1",
        rrf_k=60,
        channel_weights={channel: 1.0 for channel in RetrievalChannel},
        maximum_business_adjustment=0.25,
    )


class _Authority:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def eligible_asset_versions(self, query):
        self.calls.append(("eligible", ()))
        return RetrievalEligibility(decided_at=DECIDED_AT, items=ITEMS)

    def revalidate_asset_versions(self, query, *, asset_version_ids):
        self.calls.append(("final", tuple(asset_version_ids)))
        allowed = set(asset_version_ids)
        return RetrievalEligibility(
            decided_at=DECIDED_AT,
            items=tuple(item for item in ITEMS[1:] if item.asset_version_id in allowed),
        )


class _Source:
    def __init__(self, channel: RetrievalChannel, hits: tuple[RetrievalRecallHit, ...]) -> None:
        self.channel = channel
        self.hits = hits
        self.received: tuple[str, ...] | None = None

    def recall(self, query, *, eligible_asset_version_ids, limit):
        self.received = tuple(eligible_asset_version_ids)
        return RetrievalRecallBatch(channel=self.channel, hits=self.hits)


def test_retrieval_filters_before_recall_and_revalidates_all_replacements_once() -> None:
    authority = _Authority()
    dense = _Source(
        RetrievalChannel.PRODUCT_FUSED_DENSE,
        (
            RetrievalRecallHit(asset_version_id=VERSION_A, raw_score=0.99),
            RetrievalRecallHit(asset_version_id=VERSION_B, raw_score=0.8),
        ),
    )
    lexical = _Source(
        RetrievalChannel.LEXICAL,
        (
            RetrievalRecallHit(asset_version_id=VERSION_B, raw_score=12.0),
            RetrievalRecallHit(asset_version_id=VERSION_C, raw_score=7.0),
        ),
    )
    service = RetrievalApplicationService(
        authority=authority,
        sources=(dense, lexical),
        policy=_policy(),
    )

    response = service.execute(_query())

    assert dense.received == (VERSION_A, VERSION_B, VERSION_C)
    assert lexical.received == dense.received
    assert authority.calls == [
        ("eligible", ()),
        ("final", (VERSION_B, VERSION_A, VERSION_C)),
    ]
    assert [citation.asset_version_id for citation in response.citations] == [
        VERSION_B,
        VERSION_C,
    ]
    assert response.citations[0].rights_record_id == RIGHTS_B
    assert response.citations[0].score.channel_raw_scores == {
        RetrievalChannel.PRODUCT_FUSED_DENSE: 0.8,
        RetrievalChannel.LEXICAL: 12.0,
    }
    assert response.complete_hybrid is True
    assert response.eligible_asset_version_count == 3
    assert response.fused_candidate_count == 3
    assert response.final_authorized_candidate_count == 2
    assert response.latency_ms >= 0


def test_fused_candidate_bound_preserves_required_brand_members() -> None:
    authority = _Authority()
    service = RetrievalApplicationService(
        authority=authority,
        sources=(
            _Source(
                RetrievalChannel.PRODUCT_FUSED_DENSE,
                (
                    RetrievalRecallHit(asset_version_id=VERSION_A),
                    RetrievalRecallHit(asset_version_id=VERSION_C),
                ),
            ),
            _Source(
                RetrievalChannel.LEXICAL,
                (
                    RetrievalRecallHit(asset_version_id=VERSION_A),
                    RetrievalRecallHit(asset_version_id=VERSION_C),
                ),
            ),
            _Source(
                RetrievalChannel.BRAND_PROFILE,
                (RetrievalRecallHit(asset_version_id=VERSION_B),),
            ),
        ),
        policy=_policy(),
    )

    response = service.execute(
        _query().model_copy(
            update={
                "brand_profile_id": VERSION_OUTSIDE,
                "brand_profile_version": 1,
                "candidate_limit": 2,
            }
        )
    )

    assert authority.calls[-1] == ("final", (VERSION_A, VERSION_B))
    assert response.fused_candidate_count == 2
    assert [citation.asset_version_id for citation in response.citations] == [VERSION_B]


def test_candidate_source_cannot_escape_the_mysql_eligible_set() -> None:
    source = _Source(
        RetrievalChannel.PRODUCT_FUSED_DENSE,
        (RetrievalRecallHit(asset_version_id=VERSION_OUTSIDE, raw_score=1.0),),
    )
    service = RetrievalApplicationService(
        authority=_Authority(),
        sources=(source,),
        policy=_policy(),
    )

    with pytest.raises(RuntimeError, match="eligible set"):
        service.execute(_query())


class _UnavailableDenseSource:
    channel = RetrievalChannel.PRODUCT_FUSED_DENSE

    @staticmethod
    def recall(query, *, eligible_asset_version_ids, limit):
        raise RetrievalSourceUnavailable(
            channel=RetrievalChannel.PRODUCT_FUSED_DENSE,
            code="MILVUS_UNAVAILABLE",
            message="dense recall unavailable",
        )


class _UnavailableReranker:
    @staticmethod
    def rerank(query, *, candidate_ids):
        raise RetrievalRerankerUnavailable("provider timed out")


def test_dense_failure_returns_explicit_degraded_lexical_results() -> None:
    lexical = _Source(
        RetrievalChannel.LEXICAL,
        (RetrievalRecallHit(asset_version_id=VERSION_B, raw_score=3.0),),
    )
    service = RetrievalApplicationService(
        authority=_Authority(),
        sources=(_UnavailableDenseSource(), lexical),
        policy=_policy(),
    )

    response = service.execute(_query())

    assert response.complete_hybrid is False
    assert [degradation.code for degradation in response.degradations] == ["MILVUS_UNAVAILABLE"]
    assert [citation.asset_version_id for citation in response.citations] == [VERSION_B]


def test_reranker_failure_is_an_explicit_degradation_and_preserves_fusion_order() -> None:
    dense = _Source(
        RetrievalChannel.PRODUCT_FUSED_DENSE,
        (
            RetrievalRecallHit(asset_version_id=VERSION_A, raw_score=0.9),
            RetrievalRecallHit(asset_version_id=VERSION_B, raw_score=0.8),
        ),
    )
    service = RetrievalApplicationService(
        authority=_Authority(),
        sources=(dense,),
        policy=_policy(),
        reranker=_UnavailableReranker(),
    )

    response = service.execute(_query())

    assert response.complete_hybrid is False
    assert [degradation.code for degradation in response.degradations] == ["RERANKER_UNAVAILABLE"]
    assert [citation.asset_version_id for citation in response.citations] == [VERSION_B]
