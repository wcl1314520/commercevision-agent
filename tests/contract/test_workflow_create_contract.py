from __future__ import annotations

import pytest
from commercevision_contracts.workflow import WorkflowCreateRequest
from pydantic import ValidationError


@pytest.mark.parametrize("retention_hours", [73, 168])
def test_commerce_workflow_request_rejects_retention_beyond_task_boundary(
    retention_hours: int,
) -> None:
    with pytest.raises(ValidationError, match="retention_hours"):
        WorkflowCreateRequest(
            workflow_type="COMMERCE_IMAGE_GENERATION",
            input_data={
                "schema_version": "1.0",
                "product_id": "019b0000-0000-7000-8000-000000000002",
            },
            retention_hours=retention_hours,
        )


def test_fixture_workflow_request_preserves_longer_retention_contract() -> None:
    request = WorkflowCreateRequest(
        workflow_type="FIXTURE_IMAGE_GENERATION",
        retention_hours=168,
    )

    assert request.retention_hours == 168
