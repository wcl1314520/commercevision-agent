import pytest
from commercevision_api.main import create_app
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def test_workflow_http_idempotency_and_error_contract(
    integration_database,
    integration_settings,
) -> None:
    headers = {
        "X-Workspace-Id": "integration-api",
        "X-Actor-Id": "integration-user",
        "Idempotency-Key": "api-create-0001",
        "X-Trace-Id": "api-trace-0001",
    }
    app = create_app(integration_settings)
    with TestClient(app) as client:
        first = client.post(
            "/api/v1/workflows",
            headers=headers,
            json={
                "workflow_type": "FIXTURE_IMAGE_GENERATION",
                "input_data": {"fixture_config": {"count": 2}},
                "retention_hours": 72,
            },
        )
        duplicate = client.post(
            "/api/v1/workflows",
            headers=headers,
            json={
                "workflow_type": "FIXTURE_IMAGE_GENERATION",
                "input_data": {"fixture_config": {"count": 2}},
                "retention_hours": 72,
            },
        )
        conflict = client.post(
            "/api/v1/workflows",
            headers=headers,
            json={
                "workflow_type": "FIXTURE_IMAGE_GENERATION",
                "input_data": {"fixture_config": {"count": 3}},
                "retention_hours": 72,
            },
        )
        missing_key = client.post(
            "/api/v1/workflows",
            headers={
                "X-Workspace-Id": "integration-api",
                "X-Actor-Id": "integration-user",
            },
            json={"input_data": {}},
        )

    assert first.status_code == 202
    assert duplicate.status_code == 202
    assert first.json()["id"] == duplicate.json()["id"]
    assert first.headers["X-Trace-Id"] == "api-trace-0001"
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    assert conflict.json()["request_id"]
    assert missing_key.status_code == 422


def test_commerce_workflow_opens_at_the_product_brief_gate_without_running_ahead(
    integration_database,
    integration_settings,
) -> None:
    del integration_database
    headers = {
        "X-Workspace-Id": "integration-api",
        "X-Actor-Id": "integration-user",
        "Idempotency-Key": "commerce-workflow-create-0001",
        "X-Trace-Id": "commerce-workflow-trace-0001",
    }
    app = create_app(integration_settings)

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/workflows",
            headers=headers,
            json={
                "workflow_type": "COMMERCE_IMAGE_GENERATION",
                "input_data": {
                    "schema_version": "1.0",
                    "product_id": "019fac40-0000-7000-8000-000000000001",
                },
                "retention_hours": 72,
            },
        )
        assert created.status_code == 202, created.text
        events = client.get(
            f"/api/v1/workflows/{created.json()['id']}/events",
            headers={
                "X-Workspace-Id": "integration-api",
                "X-Actor-Id": "integration-user",
            },
        )

    assert created.json()["status"] == "UNDERSTANDING"
    assert created.json()["current_node"] == "understand_product"
    assert created.json()["version"] == 3
    assert events.status_code == 200, events.text
    assert all(event["event_type"] != "workflow.run.requested" for event in events.json())
