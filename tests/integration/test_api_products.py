from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from commercevision_api.main import create_app
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def _mutation_headers(
    *,
    workspace_id: str = "catalog-workspace-a",
    idempotency_key: str = "catalog-create-0001",
) -> dict[str, str]:
    return {
        "X-Workspace-Id": workspace_id,
        "X-Actor-Id": "catalog-user",
        "Idempotency-Key": idempotency_key,
    }


def _product_payload(
    *,
    external_id: str = "SERUM-001",
    title: str = "Hydrating Serum",
) -> dict[str, object]:
    return {
        "source_namespace": "MANUAL",
        "external_id": external_id,
        "source_version": "manual-v1",
        "title": title,
        "category_code": "beauty.skincare.serum",
        "brand": "Northstar Labs",
        "attributes": {"volume_ml": 30, "finish": "dewy"},
        "expires_at": None,
    }


def test_product_create_is_idempotent_workspace_scoped_and_uses_stable_errors(
    integration_database,
    integration_settings,
) -> None:
    app = create_app(integration_settings)
    with TestClient(app) as client:
        first = client.post(
            "/api/v1/products",
            headers=_mutation_headers(),
            json=_product_payload(),
        )
        replay = client.post(
            "/api/v1/products",
            headers=_mutation_headers(),
            json=_product_payload(),
        )
        idempotency_conflict = client.post(
            "/api/v1/products",
            headers=_mutation_headers(),
            json=_product_payload(title="Different title"),
        )
        duplicate_external_id = client.post(
            "/api/v1/products",
            headers=_mutation_headers(idempotency_key="catalog-create-0002"),
            json=_product_payload(),
        )
        other_workspace = client.post(
            "/api/v1/products",
            headers=_mutation_headers(
                workspace_id="catalog-workspace-b",
                idempotency_key="catalog-create-0003",
            ),
            json=_product_payload(),
        )
        hidden = client.get(
            f"/api/v1/products/{first.json()['id']}",
            headers={"X-Workspace-Id": "catalog-workspace-b"},
        )
        invalid = client.post(
            "/api/v1/products",
            headers=_mutation_headers(idempotency_key="catalog-create-0004"),
            json={**_product_payload(), "unexpected": True},
        )
        invalid_expiry = client.post(
            "/api/v1/products",
            headers=_mutation_headers(idempotency_key="catalog-create-0005"),
            json={**_product_payload(), "expires_at": "2026-07-22T12:00:00"},
        )

    assert first.status_code == 201
    assert replay.status_code == 201
    assert replay.json() == first.json()
    assert first.json()["workspace_id"] == "catalog-workspace-a"
    assert first.json()["version"] == 1

    assert idempotency_conflict.status_code == 409
    assert idempotency_conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"

    assert duplicate_external_id.status_code == 409
    assert duplicate_external_id.json()["code"] == "DUPLICATE_EXTERNAL_IDENTIFIER"

    assert other_workspace.status_code == 201
    assert other_workspace.json()["id"] != first.json()["id"]

    assert hidden.status_code == 404
    assert hidden.json()["code"] == "NOT_FOUND"

    assert invalid.status_code == 422
    assert invalid.json()["code"] == "VALIDATION_ERROR"
    assert invalid.json()["details"]["errors"]
    assert invalid_expiry.status_code == 422
    assert invalid_expiry.json()["code"] == "VALIDATION_ERROR"
    assert invalid_expiry.json()["details"]["errors"][0]["ctx"]["error"]


def test_product_cursor_is_stable_under_concurrent_inserts(
    integration_database,
    integration_settings,
) -> None:
    app = create_app(integration_settings)
    workspace_headers = {"X-Workspace-Id": "catalog-pagination"}
    with TestClient(app) as client:
        oldest = client.post(
            "/api/v1/products",
            headers=_mutation_headers(
                workspace_id="catalog-pagination",
                idempotency_key="catalog-page-0001",
            ),
            json=_product_payload(external_id="PAGE-001", title="Oldest"),
        ).json()
        newest_at_start = client.post(
            "/api/v1/products",
            headers=_mutation_headers(
                workspace_id="catalog-pagination",
                idempotency_key="catalog-page-0002",
            ),
            json=_product_payload(external_id="PAGE-002", title="Newest at start"),
        ).json()

        first_page = client.get(
            "/api/v1/products?limit=1",
            headers=workspace_headers,
        )
        concurrent = client.post(
            "/api/v1/products",
            headers=_mutation_headers(
                workspace_id="catalog-pagination",
                idempotency_key="catalog-page-0003",
            ),
            json=_product_payload(external_id="PAGE-003", title="Concurrent insert"),
        ).json()
        second_page = client.get(
            "/api/v1/products",
            headers=workspace_headers,
            params={"limit": 1, "cursor": first_page.json()["next_cursor"]},
        )
        invalid_cursor = client.get(
            "/api/v1/products?cursor=not-a-cursor",
            headers=workspace_headers,
        )
        malformed_cursor = client.get(
            "/api/v1/products?cursor=%25%25%25%25",
            headers=workspace_headers,
        )

    assert first_page.status_code == 200
    assert [item["id"] for item in first_page.json()["items"]] == [newest_at_start["id"]]
    assert first_page.json()["next_cursor"]

    assert second_page.status_code == 200
    assert [item["id"] for item in second_page.json()["items"]] == [oldest["id"]]
    assert concurrent["id"] not in {
        first_page.json()["items"][0]["id"],
        second_page.json()["items"][0]["id"],
    }
    assert second_page.json()["next_cursor"] is None

    assert invalid_cursor.status_code == 400
    assert invalid_cursor.json()["code"] == "INVALID_ARGUMENT"
    assert malformed_cursor.status_code == 400
    assert malformed_cursor.json()["code"] == "INVALID_ARGUMENT"


def test_product_and_sku_mutations_are_idempotent_and_version_checked(
    integration_database,
    integration_settings,
) -> None:
    app = create_app(integration_settings)
    workspace_id = "catalog-mutations"
    with TestClient(app) as client:
        product = client.post(
            "/api/v1/products",
            headers=_mutation_headers(
                workspace_id=workspace_id,
                idempotency_key="catalog-mutation-product",
            ),
            json=_product_payload(external_id="MUTATION-001"),
        ).json()
        product_update = {
            "expected_version": 1,
            "source_version": "manual-v2",
            "title": "Hydrating Serum 2",
            "category_code": "beauty.skincare.serum",
            "brand": "Northstar Labs",
            "attributes": {"volume_ml": 50, "finish": "dewy"},
            "expires_at": None,
        }
        update_headers = _mutation_headers(
            workspace_id=workspace_id,
            idempotency_key="catalog-product-update",
        )
        updated = client.put(
            f"/api/v1/products/{product['id']}",
            headers=update_headers,
            json=product_update,
        )
        update_replay = client.put(
            f"/api/v1/products/{product['id']}",
            headers=update_headers,
            json=product_update,
        )
        update_key_conflict = client.put(
            f"/api/v1/products/{product['id']}",
            headers=update_headers,
            json={**product_update, "title": "Different retry"},
        )
        stale_update = client.put(
            f"/api/v1/products/{product['id']}",
            headers=_mutation_headers(
                workspace_id=workspace_id,
                idempotency_key="catalog-product-stale",
            ),
            json=product_update,
        )
        hidden_update = client.put(
            f"/api/v1/products/{product['id']}",
            headers=_mutation_headers(
                workspace_id="catalog-other-workspace",
                idempotency_key="catalog-product-hidden",
            ),
            json={**product_update, "expected_version": 2},
        )

        sku_payload = {
            "source_namespace": "MANUAL",
            "external_id": "MUTATION-SKU-001",
            "source_version": "manual-v1",
            "title": "50 ml",
            "category_code": "beauty.skincare.serum",
            "brand": "Northstar Labs",
            "attributes": {"volume_ml": 50},
            "expires_at": None,
        }
        sku_headers = _mutation_headers(
            workspace_id=workspace_id,
            idempotency_key="catalog-sku-create",
        )
        sku = client.post(
            f"/api/v1/products/{product['id']}/skus",
            headers=sku_headers,
            json=sku_payload,
        )
        sku_replay = client.post(
            f"/api/v1/products/{product['id']}/skus",
            headers=sku_headers,
            json=sku_payload,
        )
        sku_duplicate = client.post(
            f"/api/v1/products/{product['id']}/skus",
            headers=_mutation_headers(
                workspace_id=workspace_id,
                idempotency_key="catalog-sku-duplicate",
            ),
            json=sku_payload,
        )
        sku_update_payload = {
            "expected_version": 1,
            "source_version": "manual-v2",
            "title": "50 ml refill",
            "category_code": "beauty.skincare.serum",
            "brand": "Northstar Labs",
            "attributes": {"volume_ml": 50, "pack": "refill"},
            "expires_at": None,
        }
        sku_update_headers = _mutation_headers(
            workspace_id=workspace_id,
            idempotency_key="catalog-sku-update",
        )
        sku_updated = client.put(
            f"/api/v1/products/{product['id']}/skus/{sku.json()['id']}",
            headers=sku_update_headers,
            json=sku_update_payload,
        )
        sku_update_replay = client.put(
            f"/api/v1/products/{product['id']}/skus/{sku.json()['id']}",
            headers=sku_update_headers,
            json=sku_update_payload,
        )
        sku_stale = client.put(
            f"/api/v1/products/{product['id']}/skus/{sku.json()['id']}",
            headers=_mutation_headers(
                workspace_id=workspace_id,
                idempotency_key="catalog-sku-stale",
            ),
            json=sku_update_payload,
        )
        sku_hidden = client.put(
            f"/api/v1/products/{product['id']}/skus/{sku.json()['id']}",
            headers=_mutation_headers(
                workspace_id="catalog-other-workspace",
                idempotency_key="catalog-sku-hidden",
            ),
            json={**sku_update_payload, "expected_version": 2},
        )
        inspected = client.get(
            f"/api/v1/products/{product['id']}",
            headers={"X-Workspace-Id": workspace_id},
        )
        sku_delete_headers = _mutation_headers(
            workspace_id=workspace_id,
            idempotency_key="catalog-sku-delete",
        )
        sku_deleted = client.request(
            "DELETE",
            f"/api/v1/products/{product['id']}/skus/{sku.json()['id']}",
            headers=sku_delete_headers,
            json={"expected_version": 2},
        )
        sku_delete_replay = client.request(
            "DELETE",
            f"/api/v1/products/{product['id']}/skus/{sku.json()['id']}",
            headers=sku_delete_headers,
            json={"expected_version": 2},
        )
        sku_delete_conflict = client.request(
            "DELETE",
            f"/api/v1/products/{product['id']}/skus/{sku.json()['id']}",
            headers=sku_delete_headers,
            json={"expected_version": 3},
        )
        product_delete_headers = _mutation_headers(
            workspace_id=workspace_id,
            idempotency_key="catalog-product-delete",
        )
        product_deleted = client.request(
            "DELETE",
            f"/api/v1/products/{product['id']}",
            headers=product_delete_headers,
            json={"expected_version": 2},
        )
        product_delete_replay = client.request(
            "DELETE",
            f"/api/v1/products/{product['id']}",
            headers=product_delete_headers,
            json={"expected_version": 2},
        )
        missing_after_delete = client.get(
            f"/api/v1/products/{product['id']}",
            headers={"X-Workspace-Id": workspace_id},
        )

    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert update_replay.json() == updated.json()
    assert update_key_conflict.status_code == 409
    assert update_key_conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    assert stale_update.status_code == 409
    assert stale_update.json()["code"] == "VERSION_CONFLICT"
    assert hidden_update.status_code == 404
    assert hidden_update.json()["code"] == "NOT_FOUND"

    assert sku.status_code == 201
    assert sku_replay.json() == sku.json()
    assert sku_duplicate.status_code == 409
    assert sku_duplicate.json()["code"] == "DUPLICATE_EXTERNAL_IDENTIFIER"
    assert sku_updated.status_code == 200
    assert sku_updated.json()["version"] == 2
    assert sku_update_replay.json() == sku_updated.json()
    assert sku_stale.status_code == 409
    assert sku_stale.json()["code"] == "VERSION_CONFLICT"
    assert sku_hidden.status_code == 404
    assert sku_hidden.json()["code"] == "NOT_FOUND"
    assert inspected.json()["skus"] == [sku_updated.json()]
    assert sku_deleted.status_code == 204
    assert sku_delete_replay.status_code == 204
    assert sku_delete_conflict.status_code == 409
    assert sku_delete_conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    assert product_deleted.status_code == 204
    assert product_delete_replay.status_code == 204
    assert missing_after_delete.status_code == 404


def _concurrent_request(
    app,
    barrier: Barrier,
    method: str,
    path: str,
    headers: dict[str, str],
    payload: dict[str, object],
):
    with TestClient(app) as client:
        barrier.wait()
        return client.request(method, path, headers=headers, json=payload)


def test_catalog_mutations_are_concurrent_idempotent_and_return_original_snapshots(
    integration_database,
    integration_settings,
) -> None:
    app = create_app(integration_settings)
    workspace_id = "catalog-concurrent"
    create_payload = _product_payload(external_id="CONCURRENT-001")
    create_headers = _mutation_headers(
        workspace_id=workspace_id,
        idempotency_key="catalog-concurrent-product-create",
    )
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                _concurrent_request,
                app,
                barrier,
                "POST",
                "/api/v1/products",
                create_headers,
                create_payload,
            )
            for _ in range(2)
        ]
        create_responses = [future.result() for future in futures]

    product = create_responses[0].json()
    update_payload = {
        "expected_version": 1,
        "source_version": "manual-v2",
        "title": "Concurrent update",
        "category_code": "beauty.skincare.serum",
        "brand": "Northstar Labs",
        "attributes": {"volume_ml": 40},
        "expires_at": None,
    }
    update_headers = _mutation_headers(
        workspace_id=workspace_id,
        idempotency_key="catalog-concurrent-product-update",
    )
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                _concurrent_request,
                app,
                barrier,
                "PUT",
                f"/api/v1/products/{product['id']}",
                update_headers,
                update_payload,
            )
            for _ in range(2)
        ]
        update_responses = [future.result() for future in futures]

    later_update = {
        **update_payload,
        "expected_version": 2,
        "title": "Later update",
    }
    with TestClient(app) as client:
        later = client.put(
            f"/api/v1/products/{product['id']}",
            headers=_mutation_headers(
                workspace_id=workspace_id,
                idempotency_key="catalog-concurrent-product-later",
            ),
            json=later_update,
        )
        update_replay = client.put(
            f"/api/v1/products/{product['id']}",
            headers=update_headers,
            json=update_payload,
        )

    sku_payload = {
        "source_namespace": "MANUAL",
        "external_id": "CONCURRENT-SKU-001",
        "source_version": "manual-v1",
        "title": "30 ml",
        "category_code": "beauty.skincare.serum",
        "brand": "Northstar Labs",
        "attributes": {"volume_ml": 30},
        "expires_at": None,
    }
    sku_headers = _mutation_headers(
        workspace_id=workspace_id,
        idempotency_key="catalog-concurrent-sku-create",
    )
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                _concurrent_request,
                app,
                barrier,
                "POST",
                f"/api/v1/products/{product['id']}/skus",
                sku_headers,
                sku_payload,
            )
            for _ in range(2)
        ]
        sku_responses = [future.result() for future in futures]

    sku_delete_payload = {"expected_version": sku_responses[0].json()["version"]}
    sku_delete_headers = _mutation_headers(
        workspace_id=workspace_id,
        idempotency_key="catalog-concurrent-sku-delete",
    )
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                _concurrent_request,
                app,
                barrier,
                "DELETE",
                f"/api/v1/products/{product['id']}/skus/{sku_responses[0].json()['id']}",
                sku_delete_headers,
                sku_delete_payload,
            )
            for _ in range(2)
        ]
        sku_delete_responses = [future.result() for future in futures]

    product_delete_payload = {"expected_version": later.json()["version"]}
    product_delete_headers = _mutation_headers(
        workspace_id=workspace_id,
        idempotency_key="catalog-concurrent-product-delete",
    )
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                _concurrent_request,
                app,
                barrier,
                "DELETE",
                f"/api/v1/products/{product['id']}",
                product_delete_headers,
                product_delete_payload,
            )
            for _ in range(2)
        ]
        product_delete_responses = [future.result() for future in futures]

    assert all(response.status_code == 201 for response in create_responses)
    assert create_responses[0].json() == create_responses[1].json()
    assert all(response.status_code == 200 for response in update_responses)
    assert update_responses[0].json() == update_responses[1].json()
    assert later.status_code == 200
    assert update_replay.status_code == 200
    assert update_replay.json() == update_responses[0].json()
    assert update_replay.json()["title"] == "Concurrent update"
    assert all(response.status_code == 201 for response in sku_responses)
    assert sku_responses[0].json() == sku_responses[1].json()
    assert [response.status_code for response in sku_delete_responses] == [204, 204]
    assert [response.status_code for response in product_delete_responses] == [204, 204]


def test_product_and_sku_external_identity_namespace_is_shared_under_concurrency(
    integration_database,
    integration_settings,
) -> None:
    app = create_app(integration_settings)
    workspace_id = "catalog-shared-identity"
    parent_payload = _product_payload(external_id="SHARED-PARENT")
    with TestClient(app) as client:
        parent = client.post(
            "/api/v1/products",
            headers=_mutation_headers(
                workspace_id=workspace_id,
                idempotency_key="catalog-shared-parent",
            ),
            json=parent_payload,
        ).json()

    product_payload = _product_payload(external_id="SHARED-001")
    sku_payload = {
        "source_namespace": "MANUAL",
        "external_id": "SHARED-001",
        "source_version": "manual-v1",
        "title": "30 ml",
        "category_code": "beauty.skincare.serum",
        "brand": "Northstar Labs",
        "attributes": {},
        "expires_at": None,
    }
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                _concurrent_request,
                app,
                barrier,
                "POST",
                "/api/v1/products",
                _mutation_headers(
                    workspace_id=workspace_id,
                    idempotency_key="catalog-shared-product",
                ),
                product_payload,
            ),
            executor.submit(
                _concurrent_request,
                app,
                barrier,
                "POST",
                f"/api/v1/products/{parent['id']}/skus",
                _mutation_headers(
                    workspace_id=workspace_id,
                    idempotency_key="catalog-shared-sku",
                ),
                sku_payload,
            ),
        ]
        responses = [future.result() for future in futures]

    assert sorted(response.status_code for response in responses) == [201, 409]
    assert sum(response.status_code == 409 for response in responses) == 1
    assert (
        next(response for response in responses if response.status_code == 409).json()["code"]
        == "DUPLICATE_EXTERNAL_IDENTIFIER"
    )


def test_catalog_expiry_round_trips_null_future_and_expired_values(
    integration_database,
    integration_settings,
) -> None:
    app = create_app(integration_settings)
    workspace_id = "catalog-expiry"
    with TestClient(app) as client:
        product = client.post(
            "/api/v1/products",
            headers=_mutation_headers(
                workspace_id=workspace_id,
                idempotency_key="catalog-expiry-product",
            ),
            json={
                **_product_payload(external_id="EXPIRY-001"),
                "expires_at": "2026-07-23T12:00:00+00:00",
            },
        )
        sku = client.post(
            f"/api/v1/products/{product.json()['id']}/skus",
            headers=_mutation_headers(
                workspace_id=workspace_id,
                idempotency_key="catalog-expiry-sku",
            ),
            json={
                "source_namespace": "MANUAL",
                "external_id": "EXPIRY-SKU-001",
                "source_version": "manual-v1",
                "title": "Expired sample",
                "category_code": "beauty.skincare.serum",
                "brand": "Northstar Labs",
                "attributes": {},
                "expires_at": "2026-07-21T12:00:00+00:00",
            },
        )
        product_read = client.get(
            f"/api/v1/products/{product.json()['id']}",
            headers={"X-Workspace-Id": workspace_id},
        )

    assert product.status_code == 201
    assert product.json()["expires_at"] == "2026-07-23T12:00:00Z"
    assert sku.status_code == 201
    assert sku.json()["expires_at"] == "2026-07-21T12:00:00Z"
    assert product_read.status_code == 200
    assert product_read.json()["expires_at"] == "2026-07-23T12:00:00Z"
    assert product_read.json()["skus"][0]["expires_at"] == "2026-07-21T12:00:00Z"
