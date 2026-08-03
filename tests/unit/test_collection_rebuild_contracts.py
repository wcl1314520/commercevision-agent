from __future__ import annotations

from datetime import UTC, datetime

import pytest
from commercevision_contracts import (
    CollectionRebuildActionRequestV1,
    CollectionRebuildRequestV1,
    CollectionRebuildResponseV1,
    CollectionRebuildValidationV1,
)
from commercevision_contracts.events import (
    CollectionRebuildCommand,
    CollectionRebuildCompletedPayload,
    CollectionRebuildRequestedPayload,
)
from commercevision_domain import CollectionRebuildState, VectorKind
from pydantic import ValidationError


def _request() -> dict[str, object]:
    return {
        "vector_kind": "IMAGE",
        "model_family": "deterministic-image-embedding",
        "model_id": "deterministic-image-embedding-v2",
        "pinned_revision": "fixture-epoch-v2",
        "dimension": 256,
        "schema_version": 2,
        "index_spec_version": "hnsw-cosine-v1",
        "expected_active_collection_version": 4,
        "expected_policy_pointer_version": 2,
    }


def test_rebuild_request_is_a_strict_immutable_collection_spec() -> None:
    request = CollectionRebuildRequestV1(**_request())

    assert request.vector_kind is VectorKind.IMAGE
    assert request.collection_spec.logical_key.endswith(":IMAGE:2:hnsw-cosine-v1")

    with pytest.raises(ValidationError):
        CollectionRebuildRequestV1(**(_request() | {"dimension": "256"}))
    with pytest.raises(ValidationError):
        CollectionRebuildRequestV1(**(_request() | {"unexpected": True}))
    with pytest.raises(ValidationError, match="pinned"):
        CollectionRebuildRequestV1(**(_request() | {"pinned_revision": "latest"}))


def test_rebuild_commands_bind_one_generation_and_action() -> None:
    payload = CollectionRebuildRequestedPayload(
        workspace_id="catalog-demo",
        rebuild_id="019f8a00-0000-7000-8000-000000000140",
        operation_id="019f8a00-0000-7000-8000-000000000141",
        generation=3,
        command=CollectionRebuildCommand.CONTINUE,
    )

    assert payload.generation == 3
    with pytest.raises(ValidationError):
        CollectionRebuildRequestedPayload(
            workspace_id="catalog-demo",
            rebuild_id=payload.rebuild_id,
            operation_id=payload.operation_id,
            generation=0,
            command="CONTINUE",
        )


def test_completion_event_never_leaks_a_physical_collection_name() -> None:
    payload = CollectionRebuildCompletedPayload(
        workspace_id="catalog-demo",
        rebuild_id="019f8a00-0000-7000-8000-000000000140",
        operation_id="019f8a00-0000-7000-8000-000000000141",
        candidate_collection_id="019f8a00-0000-7000-8000-000000000142",
        retired_collection_id="019f8a00-0000-7000-8000-000000000143",
        vector_kind="IMAGE",
        activated_at=datetime(2026, 8, 4, tzinfo=UTC),
    )

    assert "physical_name" not in payload.model_dump(mode="json")


def test_rebuild_response_requires_zero_unauthorized_results_before_ready() -> None:
    validation = CollectionRebuildValidationV1(
        expected_row_count=10,
        actual_row_count=10,
        missing_primary_key_count=0,
        unexpected_primary_key_count=0,
        sampled_visibility_count=5,
        sampled_visibility_failures=0,
        ann_recall_at_10=1.0,
        minimum_ann_recall_at_10=0.95,
        fixed_query_pass_count=5,
        fixed_query_total_count=5,
        unauthorized_result_count=0,
        queries_with_unauthorized_results=0,
        accepted=True,
    )
    response = CollectionRebuildResponseV1(
        id="019f8a00-0000-7000-8000-000000000140",
        operation_id="019f8a00-0000-7000-8000-000000000141",
        vector_kind="IMAGE",
        state=CollectionRebuildState.READY,
        version=8,
        snapshot_watermark=datetime(2026, 8, 4, tzinfo=UTC),
        replay_watermark=datetime(2026, 8, 4, 0, 1, tzinfo=UTC),
        backfill_cursor="019f8a00-0000-7000-8000-000000000149",
        replay_cursor=None,
        processed_count=10,
        validation=validation,
        failure_code=None,
        retire_after=None,
        created_at=datetime(2026, 8, 4, tzinfo=UTC),
        updated_at=datetime(2026, 8, 4, 0, 2, tzinfo=UTC),
    )

    assert response.validation is not None and response.validation.accepted

    with pytest.raises(ValidationError, match="unauthorized"):
        CollectionRebuildValidationV1(
            **(validation.model_dump() | {"unauthorized_result_count": 1, "accepted": True})
        )

    with pytest.raises(ValidationError, match="fixed query total"):
        CollectionRebuildValidationV1(
            **(
                validation.model_dump()
                | {
                    "fixed_query_pass_count": 4,
                    "fixed_query_total_count": 4,
                    "accepted": False,
                }
            )
        )

    with pytest.raises(ValidationError, match="accepted validation"):
        CollectionRebuildResponseV1(
            **(
                response.model_dump()
                | {
                    "state": CollectionRebuildState.RETIRING,
                    "validation": None,
                }
            )
        )


@pytest.mark.parametrize("expected_version", [True, 0, -1, "1"])
def test_rebuild_action_requires_a_strict_positive_version(expected_version: object) -> None:
    with pytest.raises(ValidationError):
        CollectionRebuildActionRequestV1(expected_version=expected_version)
