from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from commercevision_persistence.models import ProductModel, SKUModel
from sqlalchemy import create_engine, inspect
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_catalog_migration_round_trips_and_preserves_mysql_contract(
    integration_settings,
    monkeypatch,
) -> None:
    monkeypatch.setenv("CV_MYSQL_DSN", integration_settings.mysql_dsn)
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    engine = create_engine(integration_settings.mysql_dsn)
    try:
        inspector = inspect(engine)
        assert "products" in inspector.get_table_names()
        assert "skus" in inspector.get_table_names()
        products = {column["name"]: column["type"] for column in inspector.get_columns("products")}
        skus = {column["name"]: column["type"] for column in inspector.get_columns("skus")}
        assert isinstance(products["expires_at"], DATETIME)
        assert products["expires_at"].fsp == 6
        assert isinstance(skus["updated_at"], DATETIME)
        assert skus["updated_at"].fsp == 6
        assert {
            constraint["name"] for constraint in inspector.get_unique_constraints("products")
        } >= {"uq_products_external_identity"}
        assert {constraint["name"] for constraint in inspector.get_unique_constraints("skus")} >= {
            "uq_skus_external_identity"
        }
        assert {
            constraint["name"] for constraint in inspector.get_unique_constraints("products")
        } >= {"uq_products_workspace_id"}
        assert "catalog_external_identities" in inspector.get_table_names()
        assert any(
            foreign_key["constrained_columns"] == ["workspace_id", "product_id"]
            and foreign_key["referred_columns"] == ["workspace_id", "id"]
            for foreign_key in inspector.get_foreign_keys("skus")
        )

        with Session(engine) as session:
            product = ProductModel(
                id="019f8a00-0000-7000-8000-000000000090",
                workspace_id="workspace-a",
                source_namespace="MANUAL",
                external_id="DIRECT-001",
                source_version=None,
                title="Direct product",
                category_code="test",
                brand="Test",
                attributes_json={},
                expires_at=None,
                version=1,
                created_at=datetime(2026, 7, 22, 12, 0, tzinfo=UTC),
                updated_at=datetime(2026, 7, 22, 12, 0, tzinfo=UTC),
            )
            session.add(product)
            session.commit()
            session.add(
                SKUModel(
                    id="019f8a00-0000-7000-8000-000000000091",
                    workspace_id="workspace-b",
                    product_id=product.id,
                    source_namespace="MANUAL",
                    external_id="DIRECT-SKU-001",
                    source_version=None,
                    title="Mismatched SKU",
                    category_code="test",
                    brand="Test",
                    attributes_json={},
                    expires_at=None,
                    version=1,
                    created_at=datetime(2026, 7, 22, 12, 0, tzinfo=UTC),
                    updated_at=datetime(2026, 7, 22, 12, 0, tzinfo=UTC),
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

        command.downgrade(config, "7f4a2b9c1d6e")
        assert "products" not in inspect(engine).get_table_names()
        assert "skus" not in inspect(engine).get_table_names()

        command.upgrade(config, "head")
        assert "products" in inspect(engine).get_table_names()
        assert "skus" in inspect(engine).get_table_names()
    finally:
        engine.dispose()
