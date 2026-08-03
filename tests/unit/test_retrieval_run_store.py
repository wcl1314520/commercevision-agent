from datetime import timedelta

import pytest
from commercevision_contracts import RetrievalQueryV1, RetrievalResponseV1
from commercevision_persistence import MySqlRetrievalRunStore


def test_retrieval_run_store_rejects_mixed_policy_evidence_before_persistence() -> None:
    query = RetrievalQueryV1(
        workspace_id="workspace-retrieval",
        requester_id="agent:creative-planner",
        product_id="0198f4d8-1f7c-7b2d-8da9-214a92a884a1",
        purpose="RETRIEVAL",
        provider="fixture",
        requires_derivative=False,
        vector_kinds=["PRODUCT_FUSED"],
        query_text="lipstick",
        result_limit=10,
        candidate_limit=100,
        retrieval_policy_version="retrieval-policy-v1",
    )
    response = RetrievalResponseV1(
        retrieval_policy_version="retrieval-policy-v2",
        complete_hybrid=True,
        degradations=[],
        eligible_asset_version_count=0,
        fused_candidate_count=0,
        final_authorized_candidate_count=0,
        latency_ms=1,
        citations=[],
    )
    store = MySqlRetrievalRunStore(
        lambda: pytest.fail("persistence must not be entered"),  # type: ignore[arg-type]
        run_retention=timedelta(hours=1),
        preview_token_lifetime=timedelta(seconds=45),
    )

    with pytest.raises(ValueError, match="policy versions must match"):
        store.record(query, response)
