from __future__ import annotations

from typing import cast

from commercevision_persistence.generation_models import (
    CandidateImageModel,
    CandidateSlotModel,
    GenerationBatchModel,
    GenerationDispatchAttemptModel,
    ProviderCallModel,
    UsageRecordModel,
)
from commercevision_persistence.models import AssetVersionModel, UTCDateTime
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
    assert batch_uniques["uq_generation_batch_candidate_identity"] == (
        "workspace_id",
        "id",
        "workflow_id",
        "creative_plan_version_id",
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
        "uq_candidate_slot_batch_identity": (
            "workspace_id",
            "id",
            "generation_batch_id",
        ),
    }


def test_generation_convergence_schema_has_exact_tenant_owned_identities() -> None:
    attempts = cast(Table, GenerationDispatchAttemptModel.__table__)
    provider_calls = cast(Table, ProviderCallModel.__table__)
    usage_records = cast(Table, UsageRecordModel.__table__)
    candidates = cast(Table, CandidateImageModel.__table__)

    for table in (attempts, provider_calls, usage_records, candidates):
        assert tuple(column.name for column in table.primary_key) == (
            "workspace_id",
            "id",
        )

    assert {constraint.name for constraint in attempts.foreign_key_constraints} == {
        "fk_generation_dispatch_attempt_slot",
        "fk_generation_dispatch_attempt_operation",
        "fk_generation_dispatch_attempt_endpoint",
    }
    attempt_uniques = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in attempts.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert attempt_uniques["uq_generation_dispatch_attempt_operation"] == (
        "workspace_id",
        "durable_operation_id",
        "operation_attempt",
        "call_index",
    )
    for column_name in (
        "workspace_id",
        "id",
        "request_sha256",
        "adapter_configuration_sha256",
        "provider_request_id",
        "provider_task_id",
    ):
        assert cast(String, attempts.c[column_name].type).collation == "utf8mb4_0900_bin"

    assert {constraint.name for constraint in provider_calls.foreign_key_constraints} == {
        "fk_provider_call_dispatch_attempt",
        "fk_provider_call_slot",
        "fk_provider_call_operation",
        "fk_provider_call_route_decision",
        "fk_provider_call_endpoint",
    }
    assert {constraint.name for constraint in usage_records.foreign_key_constraints} == {
        "fk_usage_record_provider_call",
        "fk_usage_record_operation",
        "fk_usage_record_endpoint",
    }
    assert {constraint.name for constraint in candidates.foreign_key_constraints} == {
        "fk_candidate_image_batch",
        "fk_candidate_image_slot",
        "fk_candidate_image_asset_version",
        "fk_candidate_image_plan_version",
        "fk_candidate_image_endpoint",
        "fk_candidate_image_provider_call",
        "fk_candidate_image_usage_record",
    }
    provider_uniques = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in provider_calls.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert provider_uniques["uq_provider_call_attempt"] == (
        "workspace_id",
        "durable_operation_id",
        "operation_attempt",
        "call_index",
    )
    usage_uniques = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in usage_records.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert usage_uniques["uq_usage_record_call_identity"] == (
        "workspace_id",
        "provider_call_identity_sha256",
    )
    candidate_uniques = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in candidates.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert candidate_uniques["uq_candidate_image_slot"] == (
        "workspace_id",
        "candidate_slot_id",
    )
    assert candidate_uniques["uq_candidate_image_asset_version"] == (
        "workspace_id",
        "task_asset_version_id",
    )
    candidate_batch_fk = next(
        constraint
        for constraint in candidates.foreign_key_constraints
        if constraint.name == "fk_candidate_image_batch"
    )
    assert tuple(column.name for column in candidate_batch_fk.columns) == (
        "workspace_id",
        "generation_batch_id",
        "workflow_id",
        "creative_plan_version_id",
    )
    usage_check_names = {constraint.name for constraint in usage_records.constraints}
    assert {
        "ck_usage_record_hashes",
        "ck_usage_record_currency",
        "ck_usage_record_evidence_source",
    }.issubset(usage_check_names)


def test_asset_version_schema_requires_one_typed_origin() -> None:
    asset_versions = cast(Table, AssetVersionModel.__table__)

    assert asset_versions.c.upload_session_id.nullable is True
    assert asset_versions.c.generation_provider_call_id.nullable is True
    assert {constraint.name for constraint in asset_versions.foreign_key_constraints}.issuperset(
        {
            "fk_asset_version_workspace_upload",
            "fk_asset_version_generation_provider_call",
        }
    )
    unique_names = {
        constraint.name
        for constraint in asset_versions.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert "uq_asset_version_generation_provider_call" in unique_names
    assert "ck_asset_version_exactly_one_origin" in {
        constraint.name for constraint in asset_versions.constraints
    }
