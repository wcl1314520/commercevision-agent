import json
from datetime import UTC, datetime

import pytest
from commercevision_contracts import (
    RetrievalCitationV1,
    RetrievalQueryV1,
    RetrievalResponseV1,
    RetrievalScoreBreakdownV1,
    RetrievalTemporaryReferenceV1,
)
from commercevision_domain import RetrievalChannel
from pydantic import ValidationError

WORKSPACE = "workspace-retrieval"
PRODUCT_ID = "0198f4d8-1f7c-7b2d-8da9-214a92a884a1"
QUERY_IMAGE_ID = "0198f4d8-1f7c-7b2d-8da9-214a92a884a2"
ASSET_ID = "0198f4d8-1f7c-7b2d-8da9-214a92a884a3"
ASSET_VERSION_ID = "0198f4d8-1f7c-7b2d-8da9-214a92a884a4"
RIGHTS_ID = "0198f4d8-1f7c-7b2d-8da9-214a92a884a5"


def _query(**updates: object) -> RetrievalQueryV1:
    values: dict[str, object] = {
        "workspace_id": WORKSPACE,
        "requester_id": "agent:creative-planner",
        "product_id": PRODUCT_ID,
        "purpose": "RETRIEVAL",
        "provider": "alibaba-model-studio",
        "requires_derivative": False,
        "roles": ["PRODUCT_HERO", "DETAIL"],
        "vector_kinds": ["IMAGE", "PRODUCT_FUSED"],
        "query_text": "  鎏金\u3000Lipstick  ",
        "query_image_asset_version_id": QUERY_IMAGE_ID,
        "explicit_reference_asset_version_ids": [],
        "result_limit": 12,
        "candidate_limit": 120,
        "retrieval_policy_version": "retrieval-policy-v1",
    }
    values.update(updates)
    return RetrievalQueryV1.model_validate_json(json.dumps(values, ensure_ascii=False))


def test_retrieval_query_normalizes_controlled_text_and_preserves_structured_intent() -> None:
    query = _query(category="  Beauty.Skincare ", brand="  Northstar Labs  ")

    assert query.query_text == "鎏金 lipstick"
    assert query.category == "Beauty.Skincare"
    assert query.brand == "Northstar Labs"
    assert query.vector_kinds == ["IMAGE", "PRODUCT_FUSED"]
    assert query.result_limit == 12


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"product_id": None}, "product or ProductBrief"),
        (
            {
                "query_text": None,
                "query_image_asset_version_id": None,
                "explicit_reference_asset_version_ids": [],
            },
            "query signal",
        ),
        (
            {"vector_kinds": ["IMAGE"], "query_image_asset_version_id": None},
            "IMAGE recall requires",
        ),
        ({"candidate_limit": 5}, "candidate limit"),
        ({"requires_derivative": 1}, "boolean"),
        ({"explicit_reference_asset_version_ids": [ASSET_VERSION_ID] * 2}, "unique"),
    ],
)
def test_retrieval_query_rejects_ambiguous_or_unsafe_shapes(
    updates: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        _query(**updates)


def test_retrieval_citation_requires_explainable_rights_and_ranking_facts() -> None:
    citation = RetrievalCitationV1.model_validate_json(
        json.dumps(
            {
                "asset_id": ASSET_ID,
                "asset_version_id": ASSET_VERSION_ID,
                "rights_record_id": RIGHTS_ID,
                "rights_record_version": 3,
                "retrieval_policy_version": "retrieval-policy-v1",
                "brand_profile_version": 4,
                "channels": ["BRAND_PROFILE", "PRODUCT_FUSED_DENSE"],
                "score": {
                    "channel_ranks": {"BRAND_PROFILE": 1, "PRODUCT_FUSED_DENSE": 3},
                    "channel_raw_scores": {"PRODUCT_FUSED_DENSE": 0.87},
                    "reciprocal_rank_fusion": 0.031,
                    "business_adjustment": 0.2,
                    "final_score": 0.231,
                    "rerank_position": 2,
                },
                "rank": 1,
                "reason": "required published brand member",
                "decided_at": datetime(2026, 8, 3, tzinfo=UTC).isoformat(),
            }
        )
    )

    assert citation.rights_record_version == 3
    assert citation.channels == ["BRAND_PROFILE", "PRODUCT_FUSED_DENSE"]
    with pytest.raises(ValidationError, match="immutable version"):
        RetrievalCitationV1.model_validate(citation.model_dump() | {"brand_profile_version": None})
    with pytest.raises(ValidationError, match="score ranks"):
        RetrievalCitationV1.model_validate(
            citation.model_dump() | {"channels": [RetrievalChannel.BRAND_PROFILE]}
        )


def test_retrieval_response_requires_consistent_timing_and_candidate_counts() -> None:
    response = RetrievalResponseV1(
        retrieval_policy_version="retrieval-policy-v1",
        complete_hybrid=True,
        degradations=[],
        eligible_asset_version_count=10_000,
        fused_candidate_count=8,
        final_authorized_candidate_count=7,
        latency_ms=43,
        citations=[],
    )

    assert response.eligible_asset_version_count == 10_000
    assert response.latency_ms == 43
    with pytest.raises(ValidationError, match="candidate counts"):
        RetrievalResponseV1(
            retrieval_policy_version="retrieval-policy-v1",
            complete_hybrid=True,
            degradations=[],
            eligible_asset_version_count=2,
            fused_candidate_count=3,
            final_authorized_candidate_count=1,
            latency_ms=1,
            citations=[],
        )


def test_retrieval_response_requires_one_consistent_ranked_evidence_set() -> None:
    citation = RetrievalCitationV1(
        asset_id=ASSET_ID,
        asset_version_id=ASSET_VERSION_ID,
        rights_record_id=RIGHTS_ID,
        rights_record_version=1,
        retrieval_policy_version="retrieval-policy-v1",
        channels=[RetrievalChannel.EXPLICIT],
        score=RetrievalScoreBreakdownV1(
            channel_ranks={RetrievalChannel.EXPLICIT: 1},
            reciprocal_rank_fusion=1 / 61,
            business_adjustment=0,
            final_score=1 / 61,
        ),
        rank=1,
        reason="explicit authorized reference",
        decided_at=datetime(2026, 8, 3, tzinfo=UTC),
    )
    common = {
        "retrieval_policy_version": "retrieval-policy-v1",
        "complete_hybrid": True,
        "degradations": [],
        "eligible_asset_version_count": 2,
        "fused_candidate_count": 2,
        "final_authorized_candidate_count": 2,
        "latency_ms": 1,
    }

    with pytest.raises(ValidationError, match="ordered and contiguous"):
        RetrievalResponseV1(**common, citations=[citation.model_copy(update={"rank": 2})])
    with pytest.raises(ValidationError, match="unique Asset Versions"):
        RetrievalResponseV1(
            **common,
            citations=[citation, citation.model_copy(update={"rank": 2})],
        )
    with pytest.raises(ValidationError, match="policy version is inconsistent"):
        RetrievalResponseV1(
            **common,
            citations=[
                citation.model_copy(update={"retrieval_policy_version": "retrieval-policy-v2"})
            ],
        )


@pytest.mark.parametrize(
    "reference",
    [
        {
            "method": "GET",
            "url": "data:image/png;base64,opaque",
            "required_headers": {},
            "expires_at": datetime(2026, 8, 3, tzinfo=UTC),
        },
        {
            "method": "GET",
            "url": "https://user:secret@objects.example/preview",
            "required_headers": {},
            "expires_at": datetime(2026, 8, 3, tzinfo=UTC),
        },
        {
            "method": "GET",
            "url": "https://objects.example/preview",
            "required_headers": {"x-preview": "safe\r\ninjected: value"},
            "expires_at": datetime(2026, 8, 3, tzinfo=UTC),
        },
    ],
)
def test_temporary_reference_rejects_unsafe_url_or_header_capabilities(
    reference: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        RetrievalTemporaryReferenceV1.model_validate(reference)
