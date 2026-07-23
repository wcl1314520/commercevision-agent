from __future__ import annotations

import base64
import hashlib
import hmac
import json
import socket
import time
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from threading import Thread
from urllib.parse import quote

import httpx
import pytest
import uvicorn
from commercevision_api.main import create_app
from commercevision_contracts import Settings
from commercevision_domain.messaging import DeadLetterMessage, EventEnvelope, OutboxEvent
from commercevision_persistence import SqlAlchemyUnitOfWork
from commercevision_persistence.models import DeadLetterReplayModel
from sqlalchemy import select

pytestmark = pytest.mark.integration

_KEY_ID = "gateway-workspace-contract"
_SECRET = "workspace-contract-test-key-" + ("0" * 32)
_CANONICAL_DEAD_LETTER_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def _api_settings(integration_settings: Settings) -> Settings:
    return Settings(
        environment="ci",
        service_name="control-api",
        mysql_dsn=integration_settings.mysql_dsn,
        trusted_principal_current_key_id=_KEY_ID,
        trusted_principal_current_hmac_secret=_SECRET,
    )


def _principal(
    *,
    workspace_ids: list[str],
    admin_workspace_ids: list[str] | None = None,
) -> str:
    claims = {
        "actor_id": "workspace-contract-admin",
        "workspace_ids": workspace_ids,
        "admin_workspace_ids": admin_workspace_ids or [],
        "system_admin": False,
        "issued_at": int(datetime.now(UTC).timestamp()),
    }
    encoded = (
        base64.urlsafe_b64encode(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )
    signature = hmac.new(
        _SECRET.encode(),
        f"{_KEY_ID}.{encoded}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{_KEY_ID}.{encoded}.{signature}"


@contextmanager
def _running_api(settings: Settings):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    host, port = listener.getsockname()
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(settings),
            host=host,
            port=port,
            log_level="error",
        )
    )
    thread = Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError("test Uvicorn server did not start")
    try:
        yield host, port
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        if thread.is_alive():
            server.force_exit = True
            thread.join(timeout=5)


def _raw_workspace_status(host: str, port: int, workspace_id: bytes) -> int:
    request = (
        b"GET /api/v1/products HTTP/1.1\r\n"
        + f"Host: {host}:{port}\r\n".encode()
        + b"X-Workspace-Id: "
        + workspace_id
        + b"\r\nConnection: close\r\n\r\n"
    )
    with socket.create_connection((host, port), timeout=5) as connection:
        connection.sendall(request)
        response = bytearray()
        while chunk := connection.recv(4096):
            response.extend(chunk)
    return int(bytes(response).split(b" ", 2)[1])


def _seed_dead_letter(integration_database, *, workspace_id: str) -> DeadLetterMessage:
    now = datetime(2026, 7, 24, 12, 0, 0, 123456, tzinfo=UTC)
    event = OutboxEvent(
        envelope=EventEnvelope.create(
            event_type="workflow.run.requested",
            aggregate_type="workflow",
            aggregate_id="workflow-uuid-boundary",
            aggregate_version=1,
            trace_id="trace-uuid-boundary",
            payload={"workflow_id": "workflow-uuid-boundary", "action": "recover"},
            now=now,
        ),
        available_at=now,
        workspace_id=workspace_id,
    )
    dead_letter = replace(
        DeadLetterMessage.create(
            consumer="worker-uuid-boundary",
            message_id=event.envelope.event_id,
            event_type=event.envelope.event_type,
            payload=event.envelope.payload,
            reason="uuid boundary fixture",
            attempt_count=1,
            original_created_at=now,
            workspace_id=workspace_id,
            now=now,
        ),
        id=_CANONICAL_DEAD_LETTER_ID,
    )
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        uow.outbox.add(event)
        uow.dead_letters.add(dead_letter)
        uow.commit()
    return dead_letter


def _error_signature(response: httpx.Response) -> tuple[object, ...]:
    body = response.json()
    return (
        response.status_code,
        body["code"],
        body["message"],
        body["category"],
        body["retryable"],
        body["details"],
    )


def test_uvicorn_rejects_non_ascii_and_control_workspace_headers_before_access(
    integration_database,
    integration_settings,
) -> None:
    invalid_wire_values = (
        "工作区".encode(),
        "café".encode(),
        "cafe\u0301".encode(),
        b"caf\xe9",
        b"workspace\tid",
        b"workspace\x01id",
    )

    with _running_api(_api_settings(integration_settings)) as (host, port):
        statuses = [
            _raw_workspace_status(host, port, workspace_id) for workspace_id in invalid_wire_values
        ]

    assert all(400 <= status < 500 for status in statuses)
    assert statuses[:5] == [422, 422, 422, 422, 422]


@pytest.mark.parametrize("workspace_id", ["A", "W" * 128])
def test_ascii_workspace_boundaries_work_across_workflow_catalog_and_operator_http(
    integration_database,
    integration_settings,
    workspace_id: str,
) -> None:
    settings = _api_settings(integration_settings)
    principal = _principal(
        workspace_ids=[workspace_id],
        admin_workspace_ids=[workspace_id],
    )
    common_headers = {
        "X-Workspace-Id": workspace_id,
        "X-Actor-Id": "workspace-contract-user",
    }
    workspace_digest = hashlib.sha256(workspace_id.encode()).hexdigest()

    with (
        _running_api(settings) as (host, port),
        httpx.Client(base_url=f"http://{host}:{port}", timeout=10) as client,
    ):
        product = client.post(
            "/api/v1/products",
            headers={
                **common_headers,
                "Idempotency-Key": f"product-{workspace_digest}",
            },
            json={
                "source_namespace": "MANUAL",
                "external_id": "WORKSPACE-CONTRACT-PRODUCT",
                "source_version": "v1",
                "title": "Workspace contract product",
                "category_code": "security.workspace",
                "brand": "Boundary",
                "attributes": {},
                "expires_at": None,
            },
        )
        workflow = client.post(
            "/api/v1/workflows",
            headers={
                **common_headers,
                "Idempotency-Key": f"workflow-{workspace_digest}",
            },
            json={
                "workflow_type": "FIXTURE_IMAGE_GENERATION",
                "input_data": {},
                "retention_hours": 72,
            },
        )
        dead_letters = client.get(
            "/api/v1/operator/dead-letters",
            headers={
                "X-Workspace-Id": workspace_id,
                "X-Trusted-Principal": principal,
            },
        )

    assert product.status_code == 201
    assert product.json()["workspace_id"] == workspace_id
    assert workflow.status_code == 202
    assert workflow.json()["workspace_id"] == workspace_id
    assert dead_letters.status_code == 200


@pytest.mark.parametrize(
    "invalid_workspace_id",
    [
        " workspace",
        "workspace ",
        "work\tspace",
        "workspace\n",
        "café",
        "cafe\u0301",
        "工作区",
    ],
)
def test_trusted_claims_reject_noncanonical_workspace_ids(
    integration_database,
    integration_settings,
    invalid_workspace_id: str,
) -> None:
    with _running_api(_api_settings(integration_settings)) as (host, port):
        response = httpx.get(
            f"http://{host}:{port}/api/v1/operations",
            headers={
                "X-Workspace-Id": "workspace-valid",
                "X-Trusted-Principal": _principal(
                    workspace_ids=[invalid_workspace_id],
                ),
            },
            timeout=10,
        )

    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_REQUIRED"


def test_dead_letter_uuid_boundary_is_strict_and_non_enumerating_over_uvicorn_mysql(
    integration_database,
    integration_settings,
) -> None:
    workspace_id = "workspace-dead-letter-uuid"
    other_workspace_id = "workspace-dead-letter-uuid-other"
    dead_letter = _seed_dead_letter(
        integration_database,
        workspace_id=workspace_id,
    )
    headers = {
        "X-Workspace-Id": workspace_id,
        "X-Trusted-Principal": _principal(
            workspace_ids=[workspace_id],
            admin_workspace_ids=[workspace_id],
        ),
    }
    other_workspace_headers = {
        "X-Workspace-Id": other_workspace_id,
        "X-Trusted-Principal": _principal(
            workspace_ids=[other_workspace_id],
            admin_workspace_ids=[other_workspace_id],
        ),
    }
    replay_headers = {
        **headers,
        "Idempotency-Key": "dead-letter-uuid-boundary-key",
    }
    replay_body = {"reason": "verify strict canonical dead-letter identity"}
    accented_nfc = dead_letter.id.replace("a", "\u00e1", 1)
    invalid_aliases = (
        accented_nfc,
        dead_letter.id.replace("a", "a\u0301", 1),
        dead_letter.id.replace("a", "\uff41", 1),
        dead_letter.id[:1] + "\u200b" + dead_letter.id[1:],
        "not-a-uuid",
        f" {dead_letter.id}",
        f"{dead_letter.id} ",
        f"{dead_letter.id}a",
    )

    with (
        _running_api(_api_settings(integration_settings)) as (host, port),
        httpx.Client(base_url=f"http://{host}:{port}", timeout=10) as client,
    ):
        lower_get = client.get(
            f"/api/v1/operator/dead-letters/{dead_letter.id}",
            headers=headers,
        )
        upper_get = client.get(
            f"/api/v1/operator/dead-letters/{dead_letter.id.upper()}",
            headers=headers,
        )
        upper_replay = client.post(
            f"/api/v1/operator/dead-letters/{dead_letter.id.upper()}:replay",
            headers=replay_headers,
            json=replay_body,
        )
        lower_replay = client.post(
            f"/api/v1/operator/dead-letters/{dead_letter.id}:replay",
            headers=replay_headers,
            json=replay_body,
        )
        wrong_workspace_get = client.get(
            f"/api/v1/operator/dead-letters/{dead_letter.id}",
            headers=other_workspace_headers,
        )
        wrong_workspace_replay = client.post(
            f"/api/v1/operator/dead-letters/{dead_letter.id}:replay",
            headers={
                **other_workspace_headers,
                "Idempotency-Key": "dead-letter-uuid-wrong-workspace",
            },
            json=replay_body,
        )
        invalid_gets = [
            client.get(
                f"/api/v1/operator/dead-letters/{quote(alias, safe='')}",
                headers=headers,
            )
            for alias in invalid_aliases
        ]
        invalid_replays = [
            client.post(
                f"/api/v1/operator/dead-letters/{quote(alias, safe='')}:replay",
                headers={
                    **headers,
                    "Idempotency-Key": f"invalid-dead-letter-{index:02d}",
                },
                json=replay_body,
            )
            for index, alias in enumerate(invalid_aliases)
        ]

    assert lower_get.status_code == 200
    assert upper_get.status_code == 200
    assert lower_get.json()["dead_letter"]["id"] == dead_letter.id
    assert upper_get.json()["dead_letter"]["id"] == dead_letter.id
    assert upper_replay.status_code == 202
    assert lower_replay.status_code == 202
    assert upper_replay.json() == lower_replay.json()
    expected_not_found = _error_signature(wrong_workspace_get)
    assert expected_not_found[:2] == (404, "NOT_FOUND")
    assert _error_signature(wrong_workspace_replay) == expected_not_found
    assert all(_error_signature(response) == expected_not_found for response in invalid_gets)
    assert all(_error_signature(response) == expected_not_found for response in invalid_replays)
    with integration_database.session_factory() as session:
        replays = list(session.scalars(select(DeadLetterReplayModel)))
    assert len(replays) == 1
    assert replays[0].source_dead_letter_id == dead_letter.id
