from __future__ import annotations

from typing import Any, cast

from commercevision_persistence.models import UTCDateTime
from commercevision_persistence.provider_control_plane_models import (
    ModelRoutePolicyHeadModel,
    ModelRoutePolicyVersionModel,
    ProviderDiscoveryCandidateModel,
    ProviderEndpointCapabilityHeadModel,
    ProviderEndpointCapabilityVersionModel,
    ProviderEndpointObservationModel,
    ProviderIdentityModel,
)
from sqlalchemy import Numeric, String, Table


def _table(model: Any) -> Table:
    return cast(Table, model.__table__)


def test_provider_control_plane_schema_uses_exact_authority_and_money_types() -> None:
    assert _table(ProviderIdentityModel).name == "provider_identities"
    assert _table(ProviderEndpointCapabilityVersionModel).name == (
        "provider_endpoint_capability_versions"
    )
    assert _table(ProviderEndpointCapabilityHeadModel).name == (
        "provider_endpoint_capability_heads"
    )
    assert _table(ProviderDiscoveryCandidateModel).name == "provider_discovery_candidates"
    assert _table(ModelRoutePolicyVersionModel).name == "model_route_policy_versions"
    assert _table(ModelRoutePolicyHeadModel).name == "model_route_policy_heads"
    assert _table(ProviderEndpointObservationModel).name == "provider_endpoint_observations"

    assert tuple(column.name for column in _table(ModelRoutePolicyHeadModel).primary_key) == (
        "workspace_id",
        "policy_key",
    )
    observation_primary_key = tuple(
        column.name for column in _table(ProviderEndpointObservationModel).primary_key
    )
    assert observation_primary_key == (
        "workspace_id",
        "id",
    )

    unit_price = _table(ProviderEndpointCapabilityVersionModel).c.unit_price.type
    assert isinstance(unit_price, Numeric)
    assert (unit_price.precision, unit_price.scale) == (20, 6)
    timestamp_columns: tuple[tuple[type[object], str], ...] = (
        (ProviderEndpointCapabilityVersionModel, "created_at"),
        (ProviderEndpointCapabilityHeadModel, "updated_at"),
        (ProviderDiscoveryCandidateModel, "discovered_at"),
        (ModelRoutePolicyVersionModel, "created_at"),
        (ProviderEndpointObservationModel, "observed_at"),
    )
    for model, column_name in timestamp_columns:
        assert isinstance(_table(model).c[column_name].type, UTCDateTime)

    exact_columns: tuple[tuple[type[object], str], ...] = (
        (ProviderIdentityModel, "id"),
        (ProviderEndpointCapabilityVersionModel, "id"),
        (ProviderEndpointCapabilityVersionModel, "provider_id"),
        (ProviderEndpointCapabilityVersionModel, "endpoint_id"),
        (ProviderEndpointCapabilityHeadModel, "provider_id"),
        (ProviderEndpointCapabilityHeadModel, "endpoint_id"),
        (ProviderDiscoveryCandidateModel, "id"),
        (ModelRoutePolicyVersionModel, "id"),
        (ProviderEndpointObservationModel, "id"),
    )
    for model, column_name in exact_columns:
        column_type = cast(String, _table(model).c[column_name].type)
        assert column_type.collation == "utf8mb4_0900_bin"
