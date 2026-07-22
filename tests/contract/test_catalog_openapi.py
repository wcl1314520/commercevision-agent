import json
from pathlib import Path

from commercevision_api.main import app


def test_committed_openapi_matches_fastapi_catalog_contract() -> None:
    committed = json.loads(
        (Path(__file__).parents[2] / "docs" / "api" / "openapi.json").read_text(encoding="utf-8")
    )
    generated = app.openapi()

    assert committed == generated
    assert "/api/v1/products" in generated["paths"]
    assert "/api/v1/products/{product_id}/skus/{sku_id}" in generated["paths"]
    assert generated["paths"]["/api/v1/products"]["post"]["responses"]["409"]["content"][
        "application/json"
    ]["schema"]["$ref"].endswith("/ErrorResponse")
