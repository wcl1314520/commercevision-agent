from __future__ import annotations

from typing import cast

from commercevision_persistence.generation_models import (
    CandidateSlotModel,
    GenerationBatchModel,
)
from commercevision_persistence.models import UTCDateTime
from sqlalchemy import JSON, String, Table, UniqueConstraint


def test_generation_schema_is_tenant_first_exact_and_atomic_ready() -> None:
    batches = cast(Table, GenerationBatchModel.__table__)
    slots = cast(Table, CandidateSlotModel.__table__)

    assert tuple(column.name for column in batches.primary_key) == (
        "workspace_id",
        "id",
    )
    assert tuple(column.name for column in slots.primary_key) == (
        "workspace_id",
        "id",
    )
    assert isinstance(batches.c.authorized_asset_version_ids_json.type, JSON)
    assert isinstance(batches.c.approved_repair_scope_json.type, JSON)
    for column_name in (
        "workflow_deadline",
        "source_rights_deadline",
        "retention_deadline",
        "created_at",
    ):
        assert isinstance(batches.c[column_name].type, UTCDateTime)

    for table, column_names in (
        (
            batches,
            (
                "workspace_id",
                "batch_sha256",
                "tool_intent_sha256",
                "prompt_sha256",
                "context_sha256",
                "route_decision_sha256",
                "route_request_sha256",
                "direction_key",
                "tool_intent_key",
                "route_policy_version",
                "tool_policy_version",
                "rights_policy_version",
                "safety_policy_version",
                "created_by",
            ),
        ),
        (
            slots,
            (
                "workspace_id",
                "logical_identity_sha256",
                "operation_idempotency_key",
            ),
        ),
    ):
        for column_name in column_names:
            assert cast(String, table.c[column_name].type).collation == "utf8mb4_0900_bin"

    assert {constraint.name for constraint in batches.foreign_key_constraints} == {
        "fk_generation_batch_workflow",
        "fk_generation_batch_plan_version",
        "fk_generation_batch_approval",
        "fk_generation_batch_route_decision",
    }
    assert {constraint.name for constraint in slots.foreign_key_constraints} == {
        "fk_candidate_slot_batch",
        "fk_candidate_slot_operation",
    }
    batch_uniques = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in batches.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert batch_uniques["uq_generation_batch_logical"] == (
        "workspace_id",
        "workflow_id",
        "workflow_version",
        "creative_plan_version_id",
        "direction_key",
        "tool_intent_key",
    )
    uniques = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in slots.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert uniques == {
        "uq_candidate_slot_index": (
            "workspace_id",
            "generation_batch_id",
            "candidate_index",
        ),
        "uq_candidate_slot_operation": ("workspace_id", "durable_operation_id"),
        "uq_candidate_slot_logical_identity": (
            "workspace_id",
            "logical_identity_sha256",
        ),
    }
