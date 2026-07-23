from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from commercevision_api.errors import _classification
from commercevision_domain import (
    SKU,
    ConcurrencyError,
    DuplicateExternalIdentifierError,
    InvalidDataError,
    OperationKind,
    Product,
    ReferenceConstraintError,
    UniqueConstraintError,
)
from commercevision_domain.messaging import (
    DeadLetterReplay,
    EventEnvelope,
    OutboxEvent,
)
from commercevision_domain.operations import DurableOperation
from commercevision_persistence import (
    SqlAlchemyCatalogUnitOfWork,
    SqlAlchemyOperationUnitOfWork,
    SqlAlchemyOperatorUnitOfWork,
    SqlAlchemyUnitOfWork,
)
from sqlalchemy import CheckConstraint, Column, Integer, MetaData, Table
from sqlalchemy.orm import registry, sessionmaker

pytestmark = pytest.mark.integration


def _operation(*, target_id: str = "asset-integrity") -> DurableOperation:
    return DurableOperation.create(
        workspace_id="workspace-integrity",
        kind=OperationKind.ASSET_INDEXING,
        target_type="asset_version",
        target_id=target_id,
        target_version=1,
        input_hash="a" * 64,
        input_ref=None,
        max_attempts=3,
        now=datetime.now(UTC),
    )


def _product(*, external_id: str, product_id: str | None = None) -> Product:
    product = Product.create(
        workspace_id="workspace-integrity",
        source_namespace="erp",
        external_id=external_id,
        source_version="1",
        title=f"Product {external_id}",
        category_code="category",
        brand="brand",
        attributes={},
        expires_at=None,
        now=datetime.now(UTC),
    )
    if product_id is not None:
        product.id = product_id
    return product


def _outbox_event() -> OutboxEvent:
    now = datetime.now(UTC)
    return OutboxEvent(
        envelope=EventEnvelope.create(
            event_type="integration.integrity-probe",
            aggregate_type="integrity_probe",
            aggregate_id="integrity-probe",
            aggregate_version=1,
            trace_id="integrity-probe",
            payload={"workspace_id": "workspace-integrity"},
            now=now,
        ),
        available_at=now,
        workspace_id="workspace-integrity",
    )


def test_operation_uow_classifies_duplicate_logical_identity(
    integration_database,
) -> None:
    first = _operation()
    duplicate = _operation()
    with SqlAlchemyOperationUnitOfWork(integration_database.session_factory) as uow:
        uow.operations.add(first)
        uow.commit()

    with (
        pytest.raises(UniqueConstraintError, match="unique constraint"),
        SqlAlchemyOperationUnitOfWork(integration_database.session_factory) as uow,
    ):
        uow.operations.add(duplicate)
        uow.commit()


def test_operator_uow_classifies_foreign_key_violation(
    integration_database,
) -> None:
    replay = DeadLetterReplay.create(
        source_dead_letter_id="00000000-0000-0000-0000-000000000001",
        workspace_id="workspace-integrity",
        actor_id="operator-integrity",
        reason="classification proof",
        replay_attempt=1,
        replay_event_id="00000000-0000-0000-0000-000000000002",
        now=datetime.now(UTC),
    )

    with (
        pytest.raises(ReferenceConstraintError, match="reference constraint"),
        SqlAlchemyOperatorUnitOfWork(integration_database.session_factory) as uow,
    ):
        uow.dead_letters.add_replay(replay)
        uow.commit()


def test_operation_uow_classifies_null_as_invalid_data(
    integration_database,
) -> None:
    operation = _operation(target_id="asset-null")

    with (
        pytest.raises(InvalidDataError, match="invalid data"),
        SqlAlchemyOperationUnitOfWork(integration_database.session_factory) as uow,
    ):
        uow.operations.add(operation)
        model = next(iter(uow.session.new))
        model.workspace_id = None
        uow.flush()


def test_operation_uow_classifies_check_violation_as_invalid_data(
    integration_database,
) -> None:
    metadata = MetaData()
    probe_table = Table(
        "ticket02_integrity_check_probe",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("positive_value", Integer, nullable=False),
        CheckConstraint("positive_value > 0", name="ck_ticket02_positive_value"),
    )
    mapper = registry()

    class IntegrityCheckProbe:
        pass

    mapper.map_imperatively(IntegrityCheckProbe, probe_table)
    with integration_database.engine.begin() as connection:
        probe_table.drop(connection, checkfirst=True)
        probe_table.create(connection)

    try:
        probe_session_factory = sessionmaker(
            bind=integration_database.engine,
            expire_on_commit=False,
        )
        with (
            pytest.raises(InvalidDataError, match="invalid data"),
            SqlAlchemyOperationUnitOfWork(probe_session_factory) as uow,
        ):
            uow.session.add(IntegrityCheckProbe(positive_value=0))
            uow.flush()
    finally:
        with integration_database.engine.begin() as connection:
            probe_table.drop(connection, checkfirst=True)


def test_operation_optimistic_conflict_remains_concurrency_error(
    integration_database,
) -> None:
    operation = _operation(target_id="asset-optimistic")
    with SqlAlchemyOperationUnitOfWork(integration_database.session_factory) as uow:
        uow.operations.add(operation)
        uow.commit()

    first_uow = SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    second_uow = SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    with first_uow as first, second_uow as second:
        first_copy = first.operations.get(operation.id)
        stale_copy = second.operations.get(operation.id)
        assert first_copy is not None
        assert stale_copy is not None
        changed_at = operation.created_at + timedelta(microseconds=1)
        first_copy.cancel(expected_version=first_copy.version, now=changed_at)
        stale_copy.cancel(expected_version=stale_copy.version, now=changed_at)
        first.operations.save(first_copy)
        first.commit()

        with pytest.raises(ConcurrencyError, match="concurrently modified"):
            second.operations.save(stale_copy)


def test_generic_uow_classifies_unique_foreign_key_and_invalid_data(
    integration_database,
) -> None:
    event = _outbox_event()
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        uow.outbox.add(event)
        uow.commit()

    with (
        pytest.raises(UniqueConstraintError, match="unique constraint"),
        SqlAlchemyUnitOfWork(integration_database.session_factory) as uow,
    ):
        uow.outbox.add(event)
        uow.commit()

    replay = DeadLetterReplay.create(
        source_dead_letter_id="00000000-0000-0000-0000-000000000001",
        workspace_id="workspace-integrity",
        actor_id="generic-integrity",
        reason="generic foreign key classification",
        replay_attempt=1,
        replay_event_id="00000000-0000-0000-0000-000000000002",
        now=datetime.now(UTC),
    )
    with (
        pytest.raises(ReferenceConstraintError, match="reference constraint"),
        SqlAlchemyUnitOfWork(integration_database.session_factory) as uow,
    ):
        uow.dead_letters.add_replay(replay)
        uow.commit()

    invalid_event = _outbox_event()
    with (
        pytest.raises(InvalidDataError, match="invalid data"),
        SqlAlchemyUnitOfWork(integration_database.session_factory) as uow,
    ):
        uow.outbox.add(invalid_event)
        model = next(iter(uow.session.new))
        model.event_type = None
        uow.flush()


def test_catalog_uow_preserves_external_duplicate_but_classifies_other_integrity(
    integration_database,
) -> None:
    first = _product(external_id="catalog-logical-duplicate")
    with SqlAlchemyCatalogUnitOfWork(integration_database.session_factory) as uow:
        uow.products.add(first)
        uow.commit()

    with (
        pytest.raises(DuplicateExternalIdentifierError, match="external identifier"),
        SqlAlchemyCatalogUnitOfWork(integration_database.session_factory) as uow,
    ):
        uow.products.add(_product(external_id=first.external_id))
        uow.commit()

    primary_duplicate = _product(
        external_id="catalog-primary-duplicate",
        product_id=first.id,
    )
    with (
        pytest.raises(UniqueConstraintError, match="unique constraint"),
        SqlAlchemyCatalogUnitOfWork(integration_database.session_factory) as uow,
    ):
        uow.products.add(primary_duplicate)
        uow.commit()

    missing_product_sku = SKU.create(
        workspace_id="workspace-integrity",
        product_id="00000000-0000-0000-0000-000000000099",
        source_namespace="erp",
        external_id="catalog-missing-parent",
        source_version="1",
        title="Missing parent SKU",
        category_code="category",
        brand="brand",
        attributes={},
        expires_at=None,
        now=datetime.now(UTC),
    )
    with (
        pytest.raises(ReferenceConstraintError, match="reference constraint"),
        SqlAlchemyCatalogUnitOfWork(integration_database.session_factory) as uow,
    ):
        uow.skus.add(missing_product_sku)
        uow.commit()

    invalid_product = _product(external_id="catalog-invalid")
    invalid_product.title = cast(str, None)
    with (
        pytest.raises(InvalidDataError, match="invalid data"),
        SqlAlchemyCatalogUnitOfWork(integration_database.session_factory) as uow,
    ):
        uow.products.add(invalid_product)
        uow.commit()


def test_catalog_optimistic_conflict_remains_concurrency_error(
    integration_database,
) -> None:
    product = _product(external_id="catalog-optimistic")
    with SqlAlchemyCatalogUnitOfWork(integration_database.session_factory) as uow:
        uow.products.add(product)
        uow.commit()

    first_uow = SqlAlchemyCatalogUnitOfWork(integration_database.session_factory)
    second_uow = SqlAlchemyCatalogUnitOfWork(integration_database.session_factory)
    with first_uow as first, second_uow as second:
        first_copy = first.products.get(
            workspace_id=product.workspace_id,
            product_id=product.id,
        )
        stale_copy = second.products.get(
            workspace_id=product.workspace_id,
            product_id=product.id,
        )
        assert first_copy is not None
        assert stale_copy is not None
        changed_at = product.created_at + timedelta(microseconds=1)
        update_values = {
            "expected_version": 1,
            "source_version": "2",
            "title": "Updated",
            "category_code": "category",
            "brand": "brand",
            "attributes": {},
            "expires_at": None,
            "now": changed_at,
        }
        first_copy.update(**update_values)
        stale_copy.update(**update_values)
        first.products.save(first_copy)
        first.commit()

        with pytest.raises(ConcurrencyError, match="concurrently modified"):
            second.products.save(stale_copy)


def test_immediate_repository_insert_update_delete_errors_are_classified(
    integration_database,
) -> None:
    expires_at = datetime.now(UTC) + timedelta(days=1)
    with (
        pytest.raises(InvalidDataError, match="invalid data"),
        SqlAlchemyUnitOfWork(integration_database.session_factory) as uow,
    ):
        uow.idempotency.claim(
            scope="g" * 161,
            key_hash="1" * 64,
            request_hash="2" * 64,
            expires_at=expires_at,
        )

    with (
        pytest.raises(InvalidDataError, match="invalid data"),
        SqlAlchemyOperatorUnitOfWork(integration_database.session_factory) as uow,
    ):
        uow.idempotency.claim(
            scope="o" * 161,
            key_hash="3" * 64,
            request_hash="4" * 64,
            expires_at=expires_at,
        )

    operation = _operation(target_id="asset-immediate-update")
    with SqlAlchemyOperationUnitOfWork(integration_database.session_factory) as uow:
        uow.operations.add(operation)
        uow.commit()
    with (
        pytest.raises(InvalidDataError, match="invalid data") as update_error,
        SqlAlchemyOperationUnitOfWork(integration_database.session_factory) as uow,
    ):
        loaded = uow.operations.get(operation.id)
        assert loaded is not None
        loaded.output_ref = "x" * 513
        uow.operations.save(loaded)
    assert str(update_error.value) == "database rejected invalid data"
    assert _classification(update_error.value) == (
        422,
        "INVALID_DATA",
        "validation",
        False,
    )

    product = _product(external_id="catalog-immediate-delete")
    sku = SKU.create(
        workspace_id=product.workspace_id,
        product_id=product.id,
        source_namespace="erp",
        external_id="catalog-immediate-delete-sku",
        source_version="1",
        title="Immediate delete SKU",
        category_code="category",
        brand="brand",
        attributes={},
        expires_at=None,
        now=datetime.now(UTC),
    )
    with SqlAlchemyCatalogUnitOfWork(integration_database.session_factory) as uow:
        uow.products.add(product)
        uow.skus.add(sku)
        uow.commit()
    with (
        pytest.raises(ReferenceConstraintError, match="reference constraint"),
        SqlAlchemyCatalogUnitOfWork(integration_database.session_factory) as uow,
    ):
        uow.products.delete(
            workspace_id=product.workspace_id,
            product_id=product.id,
            expected_version=product.version,
        )
