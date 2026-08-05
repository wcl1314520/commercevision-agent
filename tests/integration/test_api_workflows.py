from datetime import UTC, datetime

import pytest
from commercevision_api.main import create_app
from commercevision_application import WorkflowEventCursorCodec
from commercevision_domain import NotFoundError
from fastapi.testclient import TestClient
from pydantic import SecretStr

pytestmark = pytest.mark.integration


def test_workflow_event_page_resumes_strictly_after_persisted_mysql_boundary(
    integration_database,
    integration_settings,
) -> None:
    del integration_database
    workspace_id = "integration-sse"
    sse_settings = integration_settings.model_copy(
        update={
            "trusted_principal_current_key_id": "integration-sse-key",
            "trusted_principal_current_hmac_secret": SecretStr("s" * 32),
        }
    )
    app = create_app(sse_settings)
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/workflows",
            headers={
                "X-Workspace-Id": workspace_id,
                "X-Actor-Id": "integration-user",
                "Idempotency-Key": "sse-create-0001",
                "X-Trace-Id": "sse-trace-0001",
            },
            json={
                "workflow_type": "FIXTURE_IMAGE_GENERATION",
                "input_data": {"fixture_config": {"count": 1}},
                "retention_hours": 72,
            },
        )
        assert created.status_code == 202, created.text
        workflow_id = created.json()["id"]
        first = app.state.container.workflow_events.event_page(
            workflow_id=workflow_id,
            workspace_id=workspace_id,
            cursor=None,
        )
        assert len(first.items) == 1
        cursor = first.items[0].cursor
        resumed = app.state.container.workflow_events.event_page(
            workflow_id=workflow_id,
            workspace_id=workspace_id,
            cursor=cursor,
        )

        assert resumed.items == ()
        assert first.items[0].event.event_type == "workflow.run.requested"
        assert first.items[0].event.event_id not in cursor
        with pytest.raises(ValueError, match="Workflow event cursor is invalid"):
            app.state.container.workflow_events.event_page(
                workflow_id=workflow_id,
                workspace_id=workspace_id,
                cursor=cursor[:-1] + ("A" if cursor[-1] != "A" else "B"),
            )
        with pytest.raises(NotFoundError):
            app.state.container.workflow_events.event_page(
                workflow_id=workflow_id,
                workspace_id="integration-sse-foreign",
                cursor=None,
            )
        forged = WorkflowEventCursorCodec(
            current_key_id="integration-sse-key",
            current_secret="s" * 32,
            max_age_seconds=sse_settings.workflow_event_cursor_max_age_seconds,
            future_skew_seconds=sse_settings.workflow_event_cursor_future_skew_seconds,
            clock=lambda: datetime.now(UTC),
        ).encode(
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            occurred_at=first.items[0].event.occurred_at,
            event_id="019fac40-0000-7000-8000-000000000099",
            retain_until=datetime.fromisoformat(created.json()["expires_at"]),
        )
        with pytest.raises(ValueError, match="Workflow event cursor is invalid"):
            app.state.container.workflow_events.event_page(
                workflow_id=workflow_id,
                workspace_id=workspace_id,
                cursor=forged,
            )


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
