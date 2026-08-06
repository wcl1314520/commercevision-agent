from __future__ import annotations

from typing import cast

from commercevision_persistence.model_router_models import ModelRouteDecisionModel
from commercevision_persistence.models import UTCDateTime
from sqlalchemy import JSON, Numeric, String, Table, UniqueConstraint


def test_model_route_decision_schema_is_tenant_first_exact_and_immutable_ready() -> None:
    table = cast(Table, ModelRouteDecisionModel.__table__)

    assert table.name == "model_route_decisions"
    assert tuple(column.name for column in table.primary_key) == (
        "workspace_id",
        "decision_sha256",
    )
    assert isinstance(table.c.decided_at.type, UTCDateTime)
    assert isinstance(table.c.fallback_endpoint_capability_version_ids_json.type, JSON)
    assert isinstance(table.c.authorized_asset_version_ids_json.type, JSON)
    assert isinstance(table.c.candidate_scores_json.type, JSON)
    assert isinstance(table.c.rejection_counts_json.type, JSON)
    assert isinstance(table.c.estimated_cost.type, Numeric)
    assert (table.c.estimated_cost.type.precision, table.c.estimated_cost.type.scale) == (20, 6)

    for column_name in (
        "workspace_id",
        "decision_sha256",
        "idempotency_scope_sha256",
        "idempotency_key_sha256",
        "route_request_sha256",
        "policy_key",
        "policy_version_id",
        "route_policy_version",
        "endpoint_capability_version_id",
    ):
        column_type = cast(String, table.c[column_name].type)
        assert column_type.collation == "utf8mb4_0900_bin"

    foreign_keys = {constraint.name for constraint in table.foreign_key_constraints}
    assert foreign_keys == {
        "fk_model_route_decision_workflow",
        "fk_model_route_decision_plan_version",
        "fk_model_route_decision_approval",
        "fk_model_route_decision_policy",
        "fk_model_route_decision_endpoint",
    }
    idempotency_identity = next(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
        and constraint.name == "uq_model_route_decision_idempotency"
    )
    assert tuple(column.name for column in idempotency_identity.columns) == (
        "workspace_id",
        "idempotency_scope_sha256",
        "idempotency_key_sha256",
    )
