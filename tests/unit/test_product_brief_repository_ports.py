from __future__ import annotations

from typing import get_protocol_members, get_type_hints
from unittest.mock import MagicMock

from commercevision_application.ports import (
    ProductBriefContinuationConfirmationRepositoryPort,
    ProductBriefContinuationRepositoryPort,
    UnitOfWorkPort,
)
from commercevision_application.product_brief_ports import (
    ProductBriefAnalysisRepositoryPort,
    ProductBriefArtifactRepositoryPort,
    ProductBriefConfirmationRepositoryPort,
    ProductBriefRepositoryPort,
)
from commercevision_persistence import SqlAlchemyProductBriefUnitOfWork


def test_product_brief_persistence_ports_are_narrow_and_disjoint() -> None:
    brief_members = get_protocol_members(ProductBriefRepositoryPort)
    analysis_members = get_protocol_members(ProductBriefAnalysisRepositoryPort)
    artifact_members = get_protocol_members(ProductBriefArtifactRepositoryPort)
    confirmation_members = get_protocol_members(ProductBriefConfirmationRepositoryPort)

    assert brief_members == {
        "add",
        "add_version",
        "get",
        "get_by_operation",
        "get_by_workflow_product",
        "get_model_version_by_operation",
        "get_version",
        "list_versions",
        "next_version_number",
        "operation_id",
        "save",
    }
    assert analysis_members == {
        "add_analysis",
        "add_provider_attempt",
        "add_provider_calls",
        "get_analysis_by_operation",
        "get_provider_attempt",
        "get_provider_call",
        "list_provider_attempts",
        "list_provider_calls",
    }
    assert artifact_members == {
        "add_provider_artifact",
        "get_provider_artifact",
        "get_provider_artifact_by_id",
        "list_provider_artifacts",
        "list_provider_artifacts_for_reconciliation",
        "save_provider_artifact",
    }
    assert confirmation_members == {"add_confirmation", "get_confirmation"}

    member_sets = (
        brief_members,
        analysis_members,
        artifact_members,
        confirmation_members,
    )
    for index, members in enumerate(member_sets):
        for other_members in member_sets[index + 1 :]:
            assert members.isdisjoint(other_members)


def test_product_brief_uow_composes_narrow_ports_over_one_repository() -> None:
    session = MagicMock()
    unit_of_work = SqlAlchemyProductBriefUnitOfWork(MagicMock(return_value=session))

    with unit_of_work as active:
        assert active.product_briefs is active.product_brief_analyses
        assert active.product_briefs is active.product_brief_artifacts
        assert active.product_briefs is active.product_brief_confirmations


def test_generic_workflow_uow_exposes_only_typed_continuation_ports() -> None:
    assert get_protocol_members(ProductBriefContinuationRepositoryPort) == {
        "get",
        "get_version",
    }
    assert get_protocol_members(ProductBriefContinuationConfirmationRepositoryPort) == {
        "get_confirmation",
    }
    uow_hints = get_type_hints(UnitOfWorkPort)
    assert uow_hints["product_briefs"] is ProductBriefContinuationRepositoryPort
    assert (
        uow_hints["product_brief_confirmations"]
        is ProductBriefContinuationConfirmationRepositoryPort
    )
