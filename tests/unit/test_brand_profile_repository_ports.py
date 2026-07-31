from __future__ import annotations

from typing import get_protocol_members, get_type_hints

from commercevision_application.brand_profile_ports import (
    BrandProfileAssetAuthorityPort,
    BrandProfileIdentityRepositoryPort,
    BrandProfilePublicationRepositoryPort,
    BrandProfileUnitOfWorkPort,
)
from commercevision_application.ports import (
    AuditRepositoryPort,
    IdempotencyRepositoryPort,
    OutboxRepositoryPort,
)


def test_brand_profile_repository_ports_are_narrow_and_disjoint() -> None:
    identity_members = get_protocol_members(BrandProfileIdentityRepositoryPort)
    publication_members = get_protocol_members(BrandProfilePublicationRepositoryPort)
    authority_members = get_protocol_members(BrandProfileAssetAuthorityPort)

    assert identity_members == {
        "add",
        "get",
        "get_by_key",
        "list",
        "lock_current_profiles_referencing_asset",
        "save",
    }
    assert publication_members == {"add", "get_version", "list_versions"}
    assert authority_members == {
        "current_snapshots",
        "lock_asset_deletion_lineage",
        "lock_current_asset",
        "lock_for_publication",
    }
    assert (identity_members - {"add"}).isdisjoint(publication_members - {"add"})
    assert identity_members.isdisjoint(authority_members)
    assert publication_members.isdisjoint(authority_members)


def test_brand_profile_uow_composes_authority_and_reliability_ports() -> None:
    hints = get_type_hints(BrandProfileUnitOfWorkPort)

    assert hints["brand_profiles"] is BrandProfileIdentityRepositoryPort
    assert hints["brand_profile_publications"] is BrandProfilePublicationRepositoryPort
    assert hints["brand_profile_assets"] is BrandProfileAssetAuthorityPort
    assert hints["idempotency"] is IdempotencyRepositoryPort
    assert hints["outbox"] is OutboxRepositoryPort
    assert hints["audit"] is AuditRepositoryPort
