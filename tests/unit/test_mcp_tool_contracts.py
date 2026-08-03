import pytest
from commercevision_contracts import Settings
from commercevision_contracts.mcp_tools import (
    AssetsSearchInputV1,
    CatalogGetProductInputV1,
    McpToolBudgetV1,
)
from commercevision_mcp.container import McpContainer, McpTrustConfigurationError
from pydantic import ValidationError


def test_mcp_product_schema_is_closed_and_rejects_cross_boundary_arguments() -> None:
    with pytest.raises(ValidationError):
        CatalogGetProductInputV1.model_validate(
            {"product_id": "019f8a00-0000-7000-8000-000000000001", "workspace_id": "other"}
        )


@pytest.mark.parametrize(
    "forbidden",
    ["url", "sql", "bucket", "object_key", "file_path", "model_id", "secret_ref"],
)
def test_mcp_search_schema_rejects_arbitrary_access_arguments(forbidden: str) -> None:
    payload = {
        "product_id": "019f8a00-0000-7000-8000-000000000001",
        "vector_kinds": ["PRODUCT_FUSED"],
        "query_text": "lipstick",
        forbidden: "controlled-by-model",
    }
    with pytest.raises(ValidationError):
        AssetsSearchInputV1.model_validate(payload)


def test_mcp_search_normalizes_controlled_text_before_execution() -> None:
    request = AssetsSearchInputV1.model_validate(
        {
            "product_id": "019f8a00-0000-7000-8000-000000000001",
            "vector_kinds": ["PRODUCT_FUSED"],
            "query_text": "  PRODUCT\n  Name  ",
            "category": "  Beauty\tCare ",
        }
    )

    assert request.query_text == "product name"
    assert request.category == "Beauty Care"


def test_mcp_budget_is_strict_and_bounded() -> None:
    budget = McpToolBudgetV1(
        max_result_count=10,
        max_candidate_count=100,
        max_output_bytes=65536,
    )
    assert budget.max_candidate_count >= budget.max_result_count
    with pytest.raises(ValidationError):
        McpToolBudgetV1.model_validate(
            {
                "max_result_count": True,
                "max_candidate_count": 100,
                "max_output_bytes": 65536,
            }
        )


def test_production_mcp_requires_nonpublic_trust_material_before_adapters_start() -> None:
    settings = Settings(
        environment="production",
        worker_queues=["commercevision.workflow"],
        object_store_endpoint="https://minio.internal.example",
        object_store_presign_endpoint="https://assets.example",
        object_store_secret_key="production-object-store-secret",
        object_store_require_encryption=True,
    )
    with pytest.raises(McpTrustConfigurationError):
        McpContainer.build(settings)
