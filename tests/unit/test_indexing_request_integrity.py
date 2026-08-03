from __future__ import annotations

import pytest
from commercevision_persistence.indexing_requests import (
    is_expected_image_index_request_race,
    is_expected_index_request_race,
)
from sqlalchemy.exc import IntegrityError


def _integrity(message: str) -> IntegrityError:
    return IntegrityError("INSERT", {}, Exception(message))


@pytest.mark.parametrize(
    "constraint",
    [
        "uq_durable_operation_logical",
        "uq_embedding_records_asset_spec",
        "uq_embedding_records_operation",
        "uq_product_search_documents_asset_input",
        "uq_product_search_documents_workspace_id",
        "uq_product_search_documents_embedding_record",
    ],
)
def test_known_duplicate_request_constraints_are_concurrent_winners(
    constraint: str,
) -> None:
    error = _integrity(f"Duplicate entry 'same' for key '{constraint}'")

    assert is_expected_index_request_race(error) is True
    assert is_expected_image_index_request_race(error) is True


@pytest.mark.parametrize(
    "message",
    [
        "Cannot add or update a child row: a foreign key constraint fails "
        "CONSTRAINT `fk_embedding_records_asset_version`",
        "Check constraint 'ck_embedding_records_positive_versions' is violated",
        "database rejected an unknown integrity condition",
    ],
)
def test_non_duplicate_integrity_failures_are_not_treated_as_winners(
    message: str,
) -> None:
    error = _integrity(message)

    assert is_expected_image_index_request_race(error) is False
