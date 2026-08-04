from __future__ import annotations

import json
from pathlib import Path

from commercevision_domain import RetrievalChannel, RetrievalPolicy, reciprocal_rank_fuse

ROOT = Path(__file__).resolve().parents[2]
DATASET_ROOT = ROOT / "evaluation" / "retrieval" / "daily-v1"


def test_daily_retrieval_dataset_has_licensed_sources_and_explicit_rights() -> None:
    manifest = json.loads((DATASET_ROOT / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["split"] == "validation"
    assert all(query["query_text"].strip() for query in manifest["queries"])
    assert {asset["category"] for asset in manifest["assets"]} == {
        "automotive-accessory",
        "beauty",
    }
    for asset in manifest["assets"]:
        assert asset["license"] == "CC0-1.0"
        assert asset["rights_record_id"]
        assert asset["rights_record_version"] >= 1
        assert (ROOT / asset["source_documentation"]).is_file()
    assert not any(DATASET_ROOT.rglob("*hidden-release*"))


def test_python_ci_runs_daily_retrieval_gate_and_retains_both_reports() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "uv run commercevision-retrieval-eval" in workflow
    assert "evaluation/retrieval/daily-v1/manifest.json" in workflow
    assert "evaluation/retrieval/daily-v1/observations.json" in workflow
    assert ".artifacts/evaluation/retrieval-daily-v1.json" in workflow
    assert ".artifacts/evaluation/retrieval-daily-v1.md" in workflow
    assert "name: retrieval-evaluation-daily" in workflow
    assert "uv run pytest tests/contract/test_retrieval_evaluation_release_gate.py -q" in workflow


def test_daily_observations_match_the_versioned_rrf_fixture() -> None:
    manifest = json.loads((DATASET_ROOT / "manifest.json").read_text(encoding="utf-8"))
    observations = json.loads((DATASET_ROOT / "observations.json").read_text(encoding="utf-8"))
    fixture = json.loads((DATASET_ROOT / "ranking-fixture.json").read_text(encoding="utf-8"))
    policy_data = fixture["policy"]
    policy = RetrievalPolicy(
        version=policy_data["version"],
        rrf_k=policy_data["rrf_k"],
        channel_weights={
            RetrievalChannel(channel): weight
            for channel, weight in policy_data["channel_weights"].items()
        },
        maximum_business_adjustment=policy_data["maximum_business_adjustment"],
    )
    observations_by_query = {
        observation["query_id"]: observation for observation in observations["queries"]
    }
    manifest_queries = {query["query_id"]: query for query in manifest["queries"]}

    for query_fixture in fixture["queries"]:
        query_id = query_fixture["query_id"]
        authorized = {
            decision["asset_version_id"]
            for decision in manifest_queries[query_id]["rights_snapshot"]
            if decision["authorized"]
        }
        fused = reciprocal_rank_fuse(
            rankings={
                RetrievalChannel(channel): tuple(ranking)
                for channel, ranking in query_fixture["channel_rankings"].items()
            },
            policy=policy,
            business_adjustments=query_fixture["business_adjustments"],
        )
        actual = [
            candidate.asset_version_id
            for candidate in fused
            if candidate.asset_version_id in authorized
        ]
        assert actual == observations_by_query[query_id]["retrieved_asset_version_ids"]
        exact_flat = [
            asset_version_id
            for asset_version_id, _score in sorted(
                query_fixture["exact_flat_cosine_scores"].items(),
                key=lambda item: (-item[1], item[0]),
            )
        ]
        assert exact_flat == manifest_queries[query_id]["exact_flat_asset_version_ids"]


def test_hidden_release_profile_is_external_and_has_a_documented_reproducible_command() -> None:
    ignore_rules = (ROOT / ".gitignore").read_text(encoding="utf-8")
    evaluation_docs = (ROOT / "docs" / "03-ai" / "evaluation-and-replay.md").read_text(
        encoding="utf-8"
    )
    release_docs = (ROOT / "docs" / "05-deployment" / "ci-cd-and-release.md").read_text(
        encoding="utf-8"
    )

    assert "evaluation/retrieval/hidden-release/" in ignore_rules
    assert "--profile release" in evaluation_docs
    assert "hidden-release/manifest.json" in evaluation_docs
    assert "hidden-release/observations.json" in evaluation_docs
    assert "confidence-bound" in evaluation_docs
    assert "UnauthorizedRecall@K" in evaluation_docs
    assert "retrieval-evaluation-release" in release_docs
