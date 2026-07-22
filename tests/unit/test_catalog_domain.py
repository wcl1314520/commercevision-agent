from datetime import UTC, datetime

import pytest
from commercevision_domain import SKU, ConcurrencyError, Product


def test_product_update_requires_current_version_and_preserves_external_identity() -> None:
    product = Product.create(
        workspace_id="workspace-a",
        source_namespace="MANUAL",
        external_id="PRODUCT-001",
        source_version="manual-v1",
        title="Serum",
        category_code="beauty.serum",
        brand="Northstar Labs",
        attributes={"volume_ml": 30},
        expires_at=None,
        now=datetime(2026, 7, 22, 12, 0, tzinfo=UTC),
    )

    product.update(
        expected_version=1,
        source_version="manual-v2",
        title="Serum 2",
        category_code="beauty.serum",
        brand="Northstar Labs",
        attributes={"volume_ml": 50},
        expires_at=None,
        now=datetime(2026, 7, 22, 12, 1, tzinfo=UTC),
    )

    assert product.version == 2
    assert product.external_id == "PRODUCT-001"
    assert product.source_namespace == "MANUAL"
    assert product.attributes == {"volume_ml": 50}
    with pytest.raises(ConcurrencyError):
        product.update(
            expected_version=1,
            source_version="manual-v3",
            title="Stale",
            category_code="beauty.serum",
            brand="Northstar Labs",
            attributes={},
            expires_at=None,
        )


def test_sku_rejects_naive_expiry_and_updates_version() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        SKU.create(
            workspace_id="workspace-a",
            product_id="product-001",
            source_namespace="MANUAL",
            external_id="SKU-001",
            source_version=None,
            title="30 ml",
            category_code="beauty.serum",
            brand="Northstar Labs",
            attributes={},
            expires_at=datetime(2026, 7, 23),
        )

    sku = SKU.create(
        workspace_id="workspace-a",
        product_id="product-001",
        source_namespace="MANUAL",
        external_id="SKU-001",
        source_version=None,
        title="30 ml",
        category_code="beauty.serum",
        brand="Northstar Labs",
        attributes={},
        expires_at=None,
    )
    sku.update(
        expected_version=1,
        source_version="manual-v2",
        title="30 ml refill",
        category_code="beauty.serum",
        brand="Northstar Labs",
        attributes={"refill": True},
        expires_at=None,
    )

    assert sku.version == 2
    assert sku.product_id == "product-001"
