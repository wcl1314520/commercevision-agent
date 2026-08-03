from __future__ import annotations

import pytest
from commercevision_domain import (
    CollectionRebuildState,
    CollectionSpec,
    VectorKind,
    collection_instance_name,
)


def _spec() -> CollectionSpec:
    return CollectionSpec.create(
        model_family="deterministic-image-embedding",
        pinned_revision="fixture-epoch-v2",
        dimension=256,
        vector_kind=VectorKind.IMAGE,
        schema_version=2,
        index_spec_version="hnsw-cosine-v1",
    )


def test_candidate_collection_name_binds_the_immutable_spec_and_rebuild_identity() -> None:
    rebuild_id = "019f8a00-0000-7000-8000-000000000140"

    name = collection_instance_name(_spec(), rebuild_id=rebuild_id)

    assert name == f"{_spec().physical_name}_019f8a000000"
    assert len(name) <= 255


@pytest.mark.parametrize(
    "rebuild_id",
    [
        "019F8A00-0000-7000-8000-000000000140",
        "not-a-uuid",
        " 019f8a00-0000-7000-8000-000000000140",
    ],
)
def test_candidate_collection_name_rejects_noncanonical_rebuild_identity(
    rebuild_id: str,
) -> None:
    with pytest.raises(ValueError, match="canonical"):
        collection_instance_name(_spec(), rebuild_id=rebuild_id)


def test_rebuild_state_exposes_only_the_durable_lifecycle() -> None:
    assert [state.value for state in CollectionRebuildState] == [
        "REQUESTED",
        "PROVISIONING",
        "BACKFILLING",
        "REPLAYING",
        "RIGHTS_RESCAN",
        "AWAITING_VALIDATION",
        "VALIDATING",
        "READY",
        "ACTIVATING",
        "ACTIVE",
        "FAILED",
        "RETIRING",
        "RETIRED",
    ]
