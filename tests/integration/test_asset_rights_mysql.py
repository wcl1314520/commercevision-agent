from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from commercevision_api.main import create_app
from commercevision_application import AssetRightsApplicationService
from commercevision_contracts import (
    RightsRecordMutationRequestV1,
    RightsUsabilityRequestV1,
    Settings,
)
from commercevision_domain import InvalidTransitionError, new_uuid7
from commercevision_persistence import SqlAlchemyAssetUnitOfWork
from commercevision_worker.runtime import WorkerRuntime
from fastapi.testclient import TestClient
from sqlalchemy import event, text

pytestmark = pytest.mark.integration

KEY_ID = "rights-integration"
SECRET = "rights-integration-secret-0000000000000001"
WORKSPACE_A = "rights-workspace-a"
WORKSPACE_B = "rights-workspace-b"


def _settings(integration_settings: Settings) -> Settings:
    return Settings(
        environment="ci",
        service_name="rights-integration",
        mysql_dsn=integration_settings.mysql_dsn,
        object_store_endpoint="http://127.0.0.1:19000",
        object_store_presign_endpoint="http://127.0.0.1:19000",
        object_store_access_key="commercevision",
        object_store_secret_key="commercevision-secret",
        trusted_principal_current_key_id=KEY_ID,
        trusted_principal_current_hmac_secret=SECRET,
    )


def _headers(
    key: str | None = None,
    *,
    workspace_id: str = WORKSPACE_A,
    admin: bool = False,
) -> dict[str, str]:
    actor_id = "rights-admin" if admin else "rights-reviewer"
    encoded = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "actor_id": actor_id,
                    "workspace_ids": [WORKSPACE_A, WORKSPACE_B],
                    "admin_workspace_ids": [WORKSPACE_A] if admin else [],
                    "system_admin": False,
                    "issued_at": int(datetime.now(UTC).timestamp()),
                },
                separators=(",", ":"),
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    signature = hmac.new(
        SECRET.encode(),
        f"{KEY_ID}.{encoded}".encode(),
        hashlib.sha256,
    ).hexdigest()
    headers = {
        "X-Workspace-Id": workspace_id,
        "X-Trusted-Principal": f"{KEY_ID}.{encoded}.{signature}",
    }
    if key is not None:
        headers.update({"X-Actor-Id": actor_id, "Idempotency-Key": key})
    return headers


def _seed_pending_rights_asset(
    database,
    *,
    workspace_id: str = WORKSPACE_A,
    retention_deadline: datetime | None = None,
) -> tuple[str, str]:
    asset_id = new_uuid7()
    asset_version_id = new_uuid7()
    now = datetime.now(UTC).replace(tzinfo=None)
    workflow_id = new_uuid7() if retention_deadline is not None else None
    retention_class = "TASK" if retention_deadline is not None else "FOUNDATION"
    persisted_deadline = (
        retention_deadline.astimezone(UTC).replace(tzinfo=None)
        if retention_deadline is not None
        else None
    )
    with database.engine.begin() as connection:
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        if workflow_id is not None:
            workflow_created_at = persisted_deadline - timedelta(hours=72)
            connection.execute(
                text(
                    "INSERT INTO workflows "
                    "(id, workspace_id, created_by, workflow_type, status, retention_status, "
                    "current_node, version, input_json, result_json, expires_at, "
                    "cancellation_requested_at, created_at, updated_at) VALUES "
                    "(:id, :workspace, 'rights-reviewer', 'asset-rights-test', 'RUNNING', "
                    "'ACTIVE', NULL, 1, JSON_OBJECT(), NULL, :expires_at, NULL, "
                    ":created_at, :created_at)"
                ),
                {
                    "id": workflow_id,
                    "workspace": workspace_id,
                    "expires_at": persisted_deadline,
                    "created_at": workflow_created_at,
                },
            )
        connection.execute(
            text(
                "INSERT INTO assets "
                "(id, workspace_id, retention_class, asset_kind, workflow_id, "
                "product_id, sku_id, status, block_reason, current_version_id, "
                "current_rights_record_id, retention_deadline, version, created_at, updated_at) "
                "VALUES (:id, :workspace, :retention_class, 'IMAGE', :workflow_id, NULL, NULL, "
                "'PENDING_RIGHTS', NULL, :version_id, NULL, :retention_deadline, 1, :now, :now)"
            ),
            {
                "id": asset_id,
                "workspace": workspace_id,
                "retention_class": retention_class,
                "workflow_id": workflow_id,
                "version_id": asset_version_id,
                "retention_deadline": persisted_deadline,
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO asset_versions "
                "(id, workspace_id, asset_id, version_number, upload_session_id, filename, "
                "sha256, byte_size, declared_mime, detected_mime, image_format, width, "
                "height, frame_count, category, role, integrity_policy_version, "
                "validation_policy_version, validation_transfer_policy_version, "
                "validation_transfer_policy_snapshot_sha256, created_at) VALUES "
                "(:id, :workspace, :asset_id, 1, :upload_id, 'asset.png', :sha, 68, "
                "'image/png', 'image/png', 'PNG', 1, 1, 1, 'beauty', 'reference', "
                "'integrity-v1', 'validation-v1', 'transfer-deny-v1', :sha, :now)"
            ),
            {
                "id": asset_version_id,
                "workspace": workspace_id,
                "asset_id": asset_id,
                "upload_id": new_uuid7(),
                "sha": "a" * 64,
                "now": now,
            },
        )
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
    return asset_id, asset_version_id


def _rights_payload(
    asset_version_id: str,
    *,
    expected_asset_version: int = 1,
    valid_from: datetime,
    valid_until: datetime,
    providers: list[str] | None = None,
) -> dict[str, object]:
    return {
        "expected_asset_version": expected_asset_version,
        "asset_version_id": asset_version_id,
        "owner_reference": "brand-owner-42",
        "source": "brand-dam",
        "license_reference": "enterprise-license-2026",
        "allowed_uses": ["RETRIEVAL", "VISION_ANALYSIS"],
        "allowed_providers": providers or ["milvus", "qwen-vl"],
        "derivative_allowed": False,
        "public_demo_allowed": False,
        "evidence_reference": "evidence://rights/42",
        "terms_sha256": "b" * 64,
        "valid_from": valid_from.isoformat(),
        "valid_until": valid_until.isoformat(),
        "perpetual": False,
    }


def _force_stale_rights_state(
    database,
    *,
    asset_id: str,
    asset_version_id: str,
    valid_from: datetime,
    valid_until: datetime,
    workspace_id: str = WORKSPACE_A,
    asset_state: str = "AVAILABLE",
) -> str:
    rights_record_id = new_uuid7()
    created_at = datetime.now(UTC).replace(tzinfo=None)
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO rights_records "
                "(id, workspace_id, asset_id, asset_version_id, version_number, decision, "
                "owner_reference, source, license_reference, derivative_allowed, "
                "public_demo_allowed, evidence_reference, terms_sha256, valid_from, "
                "valid_until, perpetual, supersedes_record_id, created_by, created_at, "
                "permissions_sealed_at) VALUES "
                "(:id, :workspace, :asset_id, :asset_version_id, 1, 'GRANT', "
                "'stale-owner', 'stale-state-test', 'stale-license', 0, 0, "
                "'evidence://stale-state', :terms_sha256, :valid_from, :valid_until, "
                "0, NULL, 'test-fixture', :created_at, NULL)"
            ),
            {
                "id": rights_record_id,
                "workspace": workspace_id,
                "asset_id": asset_id,
                "asset_version_id": asset_version_id,
                "terms_sha256": "d" * 64,
                "valid_from": valid_from.astimezone(UTC).replace(tzinfo=None),
                "valid_until": valid_until.astimezone(UTC).replace(tzinfo=None),
                "created_at": created_at,
            },
        )
        connection.execute(
            text(
                "INSERT INTO rights_record_uses "
                "(workspace_id, asset_id, rights_record_id, allowed_use, created_at) "
                "VALUES (:workspace, :asset_id, :rights_record_id, 'RETRIEVAL', :created_at)"
            ),
            {
                "workspace": workspace_id,
                "asset_id": asset_id,
                "rights_record_id": rights_record_id,
                "created_at": created_at,
            },
        )
        connection.execute(
            text(
                "INSERT INTO rights_record_providers "
                "(workspace_id, asset_id, rights_record_id, allowed_provider, created_at) "
                "VALUES (:workspace, :asset_id, :rights_record_id, 'milvus', :created_at)"
            ),
            {
                "workspace": workspace_id,
                "asset_id": asset_id,
                "rights_record_id": rights_record_id,
                "created_at": created_at,
            },
        )
        connection.execute(
            text(
                "UPDATE rights_records SET permissions_sealed_at = :created_at "
                "WHERE id = :rights_record_id"
            ),
            {"created_at": created_at, "rights_record_id": rights_record_id},
        )
        connection.execute(
            text(
                "UPDATE assets SET status = :asset_state, block_reason = NULL, "
                "current_rights_record_id = :rights_record_id, version = version + 1, "
                "updated_at = :created_at WHERE workspace_id = :workspace AND id = :asset_id"
            ),
            {
                "asset_state": asset_state,
                "rights_record_id": rights_record_id,
                "created_at": created_at,
                "workspace": workspace_id,
                "asset_id": asset_id,
            },
        )
    return rights_record_id


def test_http_rights_registration_history_and_exact_usability_boundaries(
    integration_database,
    integration_settings,
) -> None:
    asset_id, version_id = _seed_pending_rights_asset(integration_database)
    valid_from = datetime.now(UTC) - timedelta(minutes=1)
    valid_until = valid_from + timedelta(days=30, microseconds=1)
    app = create_app(_settings(integration_settings))

    with TestClient(app) as client:
        registered = client.post(
            f"/api/v1/assets/{asset_id}/rights",
            headers=_headers("rights-register-0001"),
            json=_rights_payload(
                version_id,
                valid_from=valid_from,
                valid_until=valid_until,
            ),
        )
        replay = client.post(
            f"/api/v1/assets/{asset_id}/rights",
            headers=_headers("rights-register-0001"),
            json=_rights_payload(
                version_id,
                valid_from=valid_from,
                valid_until=valid_until,
            ),
        )
        allowed = client.post(
            f"/api/v1/assets/{asset_id}/usability:check",
            headers=_headers(),
            json={
                "asset_version_id": version_id,
                "purpose": "RETRIEVAL",
                "provider": "milvus",
                "requires_derivative": False,
                "decision_time": (valid_until - timedelta(microseconds=1)).isoformat(),
            },
        )
        expired = client.post(
            f"/api/v1/assets/{asset_id}/usability:check",
            headers=_headers(),
            json={
                "asset_version_id": version_id,
                "purpose": "RETRIEVAL",
                "provider": "milvus",
                "requires_derivative": False,
                "decision_time": valid_until.isoformat(),
            },
        )
        provider_denied = client.post(
            f"/api/v1/assets/{asset_id}/usability:check",
            headers=_headers(),
            json={
                "asset_version_id": version_id,
                "purpose": "RETRIEVAL",
                "provider": "unapproved-provider",
                "requires_derivative": False,
                "decision_time": valid_from.isoformat(),
            },
        )
        derivative_denied = client.post(
            f"/api/v1/assets/{asset_id}/usability:check",
            headers=_headers(),
            json={
                "asset_version_id": version_id,
                "purpose": "RETRIEVAL",
                "provider": "milvus",
                "requires_derivative": True,
                "decision_time": valid_from.isoformat(),
            },
        )
        history = client.get(
            f"/api/v1/assets/{asset_id}/rights",
            headers=_headers(),
        )
        hidden = client.get(
            f"/api/v1/assets/{asset_id}/rights",
            headers=_headers(workspace_id=WORKSPACE_B),
        )
        hidden_replace = client.post(
            f"/api/v1/assets/{asset_id}/rights:replace",
            headers=_headers(
                "rights-hidden-replace-0001",
                workspace_id=WORKSPACE_B,
            ),
            json=_rights_payload(
                version_id,
                expected_asset_version=registered.json()["asset_version"],
                valid_from=valid_from,
                valid_until=valid_until,
            ),
        )
        hidden_revoke = client.post(
            f"/api/v1/assets/{asset_id}/rights:revoke",
            headers=_headers(
                "rights-hidden-revoke-0001",
                workspace_id=WORKSPACE_B,
            ),
            json={
                "expected_asset_version": registered.json()["asset_version"],
                "reason": "cross-workspace probe",
                "evidence_reference": "evidence://cross-workspace/probe",
            },
        )
        hidden_usability = client.post(
            f"/api/v1/assets/{asset_id}/usability:check",
            headers=_headers(workspace_id=WORKSPACE_B),
            json={
                "asset_version_id": version_id,
                "purpose": "RETRIEVAL",
                "provider": "milvus",
                "requires_derivative": False,
                "decision_time": valid_from.isoformat(),
            },
        )

    assert registered.status_code == 201
    assert replay.json() == registered.json()
    assert registered.json()["asset_state"] == "AVAILABLE"
    record = registered.json()["current_rights_record"]
    assert record["version_number"] == 1
    assert record["evidence_reference"] == "evidence://rights/42"
    assert allowed.json() == {
        "authorized": True,
        "reason_code": "AUTHORIZED",
        "workspace_id": WORKSPACE_A,
        "asset_id": asset_id,
        "asset_version_id": version_id,
        "rights_record_id": record["id"],
        "rights_record_version": 1,
        "purpose": "RETRIEVAL",
        "provider": "milvus",
        "requires_derivative": False,
        "decided_at": (valid_until - timedelta(microseconds=1)).isoformat().replace("+00:00", "Z"),
    }
    assert expired.json()["reason_code"] == "RIGHTS_EXPIRED"
    assert provider_denied.json()["reason_code"] == "PROVIDER_NOT_ALLOWED"
    assert derivative_denied.json()["reason_code"] == "DERIVATIVE_NOT_ALLOWED"
    assert [item["version_number"] for item in history.json()["items"]] == [1]
    assert hidden.status_code == 404
    assert hidden.json()["code"] == "NOT_FOUND"
    for hidden_response in (hidden_replace, hidden_revoke, hidden_usability):
        assert hidden_response.status_code == 404
        assert hidden_response.json()["code"] == "NOT_FOUND"


def test_current_usability_denies_stale_available_state_using_database_time(
    integration_database,
    integration_settings,
) -> None:
    now = datetime.now(UTC)
    expired_asset_id, expired_version_id = _seed_pending_rights_asset(integration_database)
    retention_asset_id, retention_version_id = _seed_pending_rights_asset(
        integration_database,
        retention_deadline=now - timedelta(days=1),
    )
    expired_rights_id = _force_stale_rights_state(
        integration_database,
        asset_id=expired_asset_id,
        asset_version_id=expired_version_id,
        valid_from=now - timedelta(days=3),
        valid_until=now - timedelta(days=1),
    )
    retention_rights_id = _force_stale_rights_state(
        integration_database,
        asset_id=retention_asset_id,
        asset_version_id=retention_version_id,
        valid_from=now - timedelta(days=3),
        valid_until=now + timedelta(days=30),
    )
    caller_backfill = now - timedelta(days=2)
    app = create_app(_settings(integration_settings))

    with TestClient(app) as client:
        responses = [
            client.post(
                f"/api/v1/assets/{asset_id}/usability:check",
                headers=_headers(),
                json={
                    "asset_version_id": version_id,
                    "purpose": "RETRIEVAL",
                    "provider": "milvus",
                    "requires_derivative": False,
                    "decision_time": caller_backfill.isoformat(),
                },
            )
            for asset_id, version_id in (
                (expired_asset_id, expired_version_id),
                (retention_asset_id, retention_version_id),
            )
        ]

    assert [response.status_code for response in responses] == [200, 200]
    assert [response.json()["reason_code"] for response in responses] == [
        "RIGHTS_EXPIRED",
        "ASSET_RETENTION_EXPIRED",
    ]
    assert [response.json()["rights_record_id"] for response in responses] == [
        expired_rights_id,
        retention_rights_id,
    ]
    assert all(
        datetime.fromisoformat(response.json()["decided_at"].replace("Z", "+00:00"))
        > caller_backfill
        for response in responses
    )


@pytest.mark.parametrize("boundary", ["rights", "retention"])
def test_registration_rolls_back_when_database_time_has_crossed_a_usability_boundary(
    integration_database,
    boundary: str,
) -> None:
    with integration_database.engine.connect() as connection:
        database_now = connection.execute(text("SELECT UTC_TIMESTAMP(6)")).scalar_one()
    database_now = database_now.replace(tzinfo=UTC)
    crossed_at = database_now - timedelta(seconds=1)
    application_now = crossed_at - timedelta(microseconds=1)
    retention_deadline = crossed_at if boundary == "retention" else None
    asset_id, version_id = _seed_pending_rights_asset(
        integration_database,
        retention_deadline=retention_deadline,
    )
    rights_valid_until = (
        application_now + timedelta(days=30) if boundary == "retention" else crossed_at
    )
    service = AssetRightsApplicationService(
        uow_factory=lambda: SqlAlchemyAssetUnitOfWork(integration_database.session_factory),
        clock=lambda: application_now,
    )

    with pytest.raises(InvalidTransitionError, match="commit boundary"):
        service.register(
            workspace_id=WORKSPACE_A,
            asset_id=asset_id,
            actor_id="rights-reviewer",
            idempotency_key=f"database-boundary-{boundary}-0001",
            trace_id=f"trace-database-boundary-{boundary}",
            request=RightsRecordMutationRequestV1.model_validate(
                _rights_payload(
                    version_id,
                    valid_from=application_now - timedelta(days=1),
                    valid_until=rights_valid_until,
                )
            ),
        )

    with integration_database.engine.connect() as connection:
        asset = (
            connection.execute(
                text(
                    "SELECT status, current_rights_record_id, version "
                    "FROM assets WHERE workspace_id = :workspace AND id = :asset_id"
                ),
                {"workspace": WORKSPACE_A, "asset_id": asset_id},
            )
            .mappings()
            .one()
        )
        rights_count = connection.execute(
            text("SELECT COUNT(*) FROM rights_records WHERE asset_id = :asset_id"),
            {"asset_id": asset_id},
        ).scalar_one()
        event_count = connection.execute(
            text("SELECT COUNT(*) FROM outbox_events WHERE aggregate_id = :asset_id"),
            {"asset_id": asset_id},
        ).scalar_one()
    assert asset == {
        "status": "PENDING_RIGHTS",
        "current_rights_record_id": None,
        "version": 1,
    }
    assert rights_count == 0
    assert event_count == 0


def test_registration_final_guard_rechecks_database_time_after_initial_sample(
    integration_database,
) -> None:
    with integration_database.engine.connect() as connection:
        database_now = connection.execute(text("SELECT UTC_TIMESTAMP(6)")).scalar_one()
    database_now = database_now.replace(tzinfo=UTC)
    valid_until = database_now + timedelta(seconds=4)
    asset_id, version_id = _seed_pending_rights_asset(integration_database)
    service = AssetRightsApplicationService(
        uow_factory=lambda: SqlAlchemyAssetUnitOfWork(integration_database.session_factory),
        clock=lambda: database_now,
    )
    initial_sample_observed = threading.Event()

    def cross_boundary_after_initial_sample(
        _conn,
        _cursor,
        statement,
        _parameters,
        _context,
        _many,
    ) -> None:
        normalized = statement.upper()
        if (
            not initial_sample_observed.is_set()
            and normalized.lstrip().startswith("SELECT")
            and "ASSETS.STATUS" in normalized
            and "UTC_TIMESTAMP(6)" in normalized
            and "FOR UPDATE" in normalized
        ):
            initial_sample_observed.set()
            threading.Event().wait(timeout=4.2)

    event.listen(
        integration_database.engine,
        "after_cursor_execute",
        cross_boundary_after_initial_sample,
    )
    try:
        with pytest.raises(
            InvalidTransitionError,
            match="crossed a usability boundary before commit",
        ):
            service.register(
                workspace_id=WORKSPACE_A,
                asset_id=asset_id,
                actor_id="rights-reviewer",
                idempotency_key="final-database-guard-0001",
                trace_id="trace-final-database-guard",
                request=RightsRecordMutationRequestV1.model_validate(
                    _rights_payload(
                        version_id,
                        valid_from=database_now - timedelta(days=1),
                        valid_until=valid_until,
                    )
                ),
            )
    finally:
        event.remove(
            integration_database.engine,
            "after_cursor_execute",
            cross_boundary_after_initial_sample,
        )

    assert initial_sample_observed.is_set()
    with integration_database.engine.connect() as connection:
        persisted = (
            connection.execute(
                text(
                    "SELECT status, current_rights_record_id, version FROM assets "
                    "WHERE workspace_id = :workspace AND id = :asset_id"
                ),
                {"workspace": WORKSPACE_A, "asset_id": asset_id},
            )
            .mappings()
            .one()
        )
        rights_count = connection.execute(
            text("SELECT COUNT(*) FROM rights_records WHERE asset_id = :asset_id"),
            {"asset_id": asset_id},
        ).scalar_one()
    assert persisted == {
        "status": "PENDING_RIGHTS",
        "current_rights_record_id": None,
        "version": 1,
    }
    assert rights_count == 0


def test_concurrent_replacement_serializes_versions_and_revocation_blocks_immediately(
    integration_database,
) -> None:
    asset_id, version_id = _seed_pending_rights_asset(integration_database)
    now = datetime.now(UTC)
    service = AssetRightsApplicationService(
        uow_factory=lambda: SqlAlchemyAssetUnitOfWork(integration_database.session_factory),
        clock=lambda: now,
    )
    initial = RightsRecordMutationRequestV1.model_validate(
        _rights_payload(
            version_id,
            valid_from=now - timedelta(minutes=1),
            valid_until=now + timedelta(days=30),
        )
    )
    registered = service.register(
        workspace_id=WORKSPACE_A,
        asset_id=asset_id,
        actor_id="rights-reviewer",
        idempotency_key="concurrent-register-0001",
        trace_id="trace-register",
        request=initial,
    )
    replacement = RightsRecordMutationRequestV1.model_validate(
        {
            **_rights_payload(
                version_id,
                expected_asset_version=registered.asset_version,
                valid_from=now,
                valid_until=now + timedelta(days=60),
            ),
            "allowed_uses": [],
        }
    )

    def replace(index: int):
        try:
            return service.replace(
                workspace_id=WORKSPACE_A,
                asset_id=asset_id,
                actor_id="rights-reviewer",
                idempotency_key=f"concurrent-replace-{index:04d}",
                trace_id=f"trace-replace-{index}",
                request=replacement,
            )
        except Exception as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(replace, (1, 2)))

    successes = [result for result in results if not isinstance(result, Exception)]
    failures = [result for result in results if isinstance(result, Exception)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert "does not match expected" in str(failures[0])
    assert successes[0].asset_state.value == "BLOCKED"

    history = service.history(
        workspace_id=WORKSPACE_A,
        asset_id=asset_id,
        before_version=None,
        limit=100,
    )
    assert [record.version_number for record in history.items] == [2, 1]

    from commercevision_contracts import RightsRecordRevokeRequestV1

    revoked = service.revoke(
        workspace_id=WORKSPACE_A,
        asset_id=asset_id,
        actor_id="rights-reviewer",
        idempotency_key="revoke-after-race-0001",
        trace_id="trace-revoke",
        request=RightsRecordRevokeRequestV1(
            expected_asset_version=successes[0].asset_version,
            reason="license withdrawn",
            evidence_reference="evidence://rights/revocation-42",
        ),
    )

    assert revoked.asset_state.value == "BLOCKED"
    assert revoked.current_rights_record is not None
    assert revoked.current_rights_record.version_number == 3
    revoke_decision = service.current_usability(
        workspace_id=WORKSPACE_A,
        asset_id=asset_id,
        request=RightsUsabilityRequestV1(
            asset_version_id=version_id,
            purpose="RETRIEVAL",
            provider="milvus",
            requires_derivative=False,
            decision_time=now,
        ),
    )
    assert revoke_decision.authorized is False
    assert revoke_decision.reason_code.value == "RIGHTS_REVOKED"
    with integration_database.engine.connect() as connection:
        persisted = (
            connection.execute(
                text(
                    "SELECT a.status, COUNT(o.id) AS events "
                    "FROM assets a JOIN outbox_events o "
                    "ON o.aggregate_id = a.id "
                    "WHERE a.workspace_id = :workspace AND a.id = :asset_id "
                    "AND o.event_type = 'asset.rights.changed' GROUP BY a.status"
                ),
                {"workspace": WORKSPACE_A, "asset_id": asset_id},
            )
            .mappings()
            .one()
        )
        audit_metadata = connection.execute(
            text(
                "SELECT metadata_json FROM audit_events "
                "WHERE workspace_id = :workspace AND resource_id = :asset_id "
                "AND action = 'asset.rights.revoked' "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"workspace": WORKSPACE_A, "asset_id": asset_id},
        ).scalar_one()
        replacement_payload = connection.execute(
            text(
                "SELECT payload_json FROM outbox_events "
                "WHERE aggregate_id = :asset_id "
                "AND event_type = 'asset.rights.changed' "
                "ORDER BY occurred_at LIMIT 1 OFFSET 1"
            ),
            {"asset_id": asset_id},
        ).scalar_one()
    assert persisted["status"] == "BLOCKED"
    assert persisted["events"] == 3
    replacement_payload = json.loads(replacement_payload)
    assert replacement_payload["change"] == "REPLACED"
    assert replacement_payload["required_convergence"] == "REMOVE_EXTERNAL_DERIVATIVES"
    audit_metadata = json.loads(audit_metadata)
    assert audit_metadata["reason"] == "license withdrawn"
    assert audit_metadata["evidence_reference"] == "evidence://rights/revocation-42"


def test_current_usability_shared_lock_linearizes_before_concurrent_revocation(
    integration_database,
) -> None:
    from commercevision_contracts import RightsRecordRevokeRequestV1

    asset_id, version_id = _seed_pending_rights_asset(integration_database)
    now = datetime.now(UTC)
    service = AssetRightsApplicationService(
        uow_factory=lambda: SqlAlchemyAssetUnitOfWork(integration_database.session_factory),
        clock=lambda: now,
    )
    registered = service.register(
        workspace_id=WORKSPACE_A,
        asset_id=asset_id,
        actor_id="rights-reviewer",
        idempotency_key="linearized-register-0001",
        trace_id="trace-linearized-register",
        request=RightsRecordMutationRequestV1.model_validate(
            _rights_payload(
                version_id,
                valid_from=now - timedelta(minutes=1),
                valid_until=now + timedelta(days=30),
            )
        ),
    )
    read_lock_acquired = threading.Event()
    release_read = threading.Event()
    revoke_started = threading.Event()
    revoke_done = threading.Event()

    def pause_after_shared_lock(_conn, _cursor, statement, _parameters, _context, _many) -> None:
        normalized = statement.upper()
        if (
            not read_lock_acquired.is_set()
            and "UTC_TIMESTAMP(6)" in normalized
            and "RIGHTS_RECORDS" in normalized
            and ("LOCK IN SHARE MODE" in normalized or "FOR SHARE" in normalized)
        ):
            read_lock_acquired.set()
            assert release_read.wait(timeout=5)

    event.listen(integration_database.engine, "after_cursor_execute", pause_after_shared_lock)
    request = RightsUsabilityRequestV1(
        asset_version_id=version_id,
        purpose="RETRIEVAL",
        provider="milvus",
        requires_derivative=False,
        decision_time=now,
    )

    def revoke():
        revoke_started.set()
        try:
            return service.revoke(
                workspace_id=WORKSPACE_A,
                asset_id=asset_id,
                actor_id="rights-reviewer",
                idempotency_key="linearized-revoke-0001",
                trace_id="trace-linearized-revoke",
                request=RightsRecordRevokeRequestV1(
                    expected_asset_version=registered.asset_version,
                    reason="linearization barrier",
                    evidence_reference="evidence://rights/linearization",
                ),
            )
        finally:
            revoke_done.set()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            usability_future = pool.submit(
                service.current_usability,
                workspace_id=WORKSPACE_A,
                asset_id=asset_id,
                request=request,
            )
            assert read_lock_acquired.wait(timeout=5)
            revoke_future = pool.submit(revoke)
            assert revoke_started.wait(timeout=2)
            assert not revoke_done.wait(timeout=0.2)
            release_read.set()
            usability = usability_future.result(timeout=5)
            revoked = revoke_future.result(timeout=5)
    finally:
        release_read.set()
        event.remove(integration_database.engine, "after_cursor_execute", pause_after_shared_lock)

    assert usability.authorized is True
    assert revoked.asset_state.value == "BLOCKED"
    after_revoke = service.current_usability(
        workspace_id=WORKSPACE_A,
        asset_id=asset_id,
        request=request,
    )
    assert after_revoke.authorized is False
    assert after_revoke.reason_code.value == "RIGHTS_REVOKED"


def test_expiry_and_administrator_block_publish_removal_before_return(
    integration_database,
    integration_settings,
) -> None:
    asset_id, version_id = _seed_pending_rights_asset(integration_database)
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=5)
    app = create_app(_settings(integration_settings))
    with TestClient(app) as client:
        registered = client.post(
            f"/api/v1/assets/{asset_id}/rights",
            headers=_headers("rights-expiry-register-0001"),
            json=_rights_payload(
                version_id,
                valid_from=now - timedelta(minutes=1),
                valid_until=expires_at,
            ),
        )
        blocked = client.post(
            f"/api/v1/assets/{asset_id}:block",
            headers=_headers("rights-admin-block-0001", admin=True),
            json={
                "expected_asset_version": registered.json()["asset_version"],
                "reason": "legal hold",
                "evidence_reference": "evidence://admin-block/42",
            },
        )
        blocked_decision = client.post(
            f"/api/v1/assets/{asset_id}/usability:check",
            headers=_headers(),
            json={
                "asset_version_id": version_id,
                "purpose": "RETRIEVAL",
                "provider": "milvus",
                "requires_derivative": False,
                "decision_time": now.isoformat(),
            },
        )

    assert blocked.status_code == 200
    assert blocked.json()["asset_state"] == "BLOCKED"
    assert blocked_decision.status_code == 200
    assert blocked_decision.json()["authorized"] is False
    assert blocked_decision.json()["reason_code"] == "ADMINISTRATIVELY_BLOCKED"
    with integration_database.engine.connect() as connection:
        block_event = (
            connection.execute(
                text(
                    "SELECT id, payload_json FROM outbox_events "
                    "WHERE aggregate_id = :asset_id "
                    "AND event_type = 'asset.rights.changed' "
                    "ORDER BY occurred_at DESC LIMIT 1"
                ),
                {"asset_id": asset_id},
            )
            .mappings()
            .one()
        )
        block_audit_metadata = connection.execute(
            text(
                "SELECT metadata_json FROM audit_events "
                "WHERE workspace_id = :workspace AND resource_id = :asset_id "
                "AND action = 'asset.rights.administrator_blocked' "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"workspace": WORKSPACE_A, "asset_id": asset_id},
        ).scalar_one()
    block_payload = json.loads(block_event["payload_json"])
    block_audit_metadata = json.loads(block_audit_metadata)
    assert block_payload["change"] == "ADMINISTRATOR_BLOCKED"
    assert block_payload["required_convergence"] == "REMOVE_EXTERNAL_DERIVATIVES"
    assert block_audit_metadata["reason"] == "legal hold"
    assert block_audit_metadata["evidence_reference"] == "evidence://admin-block/42"
    runtime = WorkerRuntime.build(
        _settings(integration_settings).model_copy(
            update={"worker_queues": ["commercevision.workflow"]}
        )
    )
    try:
        assert runtime.process_event(block_event["id"]) == "processed"
        assert runtime.process_event(block_event["id"]) == "duplicate"
    finally:
        runtime.close()

    activation_asset_id, activation_version_id = _seed_pending_rights_asset(
        integration_database,
        workspace_id=WORKSPACE_B,
    )
    activation_service = AssetRightsApplicationService(
        uow_factory=lambda: SqlAlchemyAssetUnitOfWork(integration_database.session_factory),
        clock=lambda: now,
    )
    registered_activation = activation_service.register(
        workspace_id=WORKSPACE_B,
        asset_id=activation_asset_id,
        actor_id="rights-reviewer",
        idempotency_key="backlog-rights-register-0001",
        trace_id="trace-backlog-rights",
        request=RightsRecordMutationRequestV1.model_validate(
            _rights_payload(
                activation_version_id,
                valid_from=now - timedelta(minutes=1),
                valid_until=now + timedelta(days=30),
            )
        ),
    )
    assert registered_activation.asset_state.value == "AVAILABLE"
    with integration_database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE assets SET status = 'PENDING_RIGHTS', block_reason = NULL, "
                "version = version + 1 WHERE workspace_id = :workspace AND id = :asset_id"
            ),
            {"workspace": WORKSPACE_B, "asset_id": activation_asset_id},
        )
    due_activation = AssetRightsApplicationService(
        uow_factory=lambda: SqlAlchemyAssetUnitOfWork(integration_database.session_factory),
        clock=lambda: now - timedelta(days=365),
    )
    assert due_activation.activate_due_once(limit=10) == 1
    assert due_activation.activate_due_once(limit=10) == 0
    with integration_database.engine.connect() as connection:
        activation = (
            connection.execute(
                text(
                    "SELECT a.status, o.payload_json "
                    "FROM assets a JOIN outbox_events o ON o.aggregate_id = a.id "
                    "WHERE a.id = :asset_id "
                    "AND JSON_UNQUOTE(JSON_EXTRACT(o.payload_json, '$.change')) = 'ACTIVATED'"
                ),
                {"asset_id": activation_asset_id},
            )
            .mappings()
            .one()
        )
    assert activation["status"] == "AVAILABLE"
    assert json.loads(activation["payload_json"])["required_convergence"] == "REINDEX"

    missed_asset_id, missed_version_id = _seed_pending_rights_asset(
        integration_database,
        workspace_id=WORKSPACE_B,
    )
    _force_stale_rights_state(
        integration_database,
        asset_id=missed_asset_id,
        asset_version_id=missed_version_id,
        workspace_id=WORKSPACE_B,
        asset_state="PENDING_RIGHTS",
        valid_from=now - timedelta(days=2),
        valid_until=now - timedelta(days=1),
    )
    missed_expiry = AssetRightsApplicationService(
        uow_factory=lambda: SqlAlchemyAssetUnitOfWork(integration_database.session_factory),
        clock=lambda: now - timedelta(days=365),
    )
    assert missed_expiry.expire_due_once(limit=10) == 1
    with integration_database.engine.connect() as connection:
        missed_status = connection.execute(
            text("SELECT status FROM assets WHERE id = :asset_id"),
            {"asset_id": missed_asset_id},
        ).scalar_one()
    assert missed_status == "RIGHTS_EXPIRED"

    expiry_asset_id, expiry_version_id = _seed_pending_rights_asset(
        integration_database,
        workspace_id=WORKSPACE_B,
    )
    _force_stale_rights_state(
        integration_database,
        asset_id=expiry_asset_id,
        asset_version_id=expiry_version_id,
        workspace_id=WORKSPACE_B,
        valid_from=now - timedelta(days=2),
        valid_until=now - timedelta(minutes=1),
    )
    boundary_service = AssetRightsApplicationService(
        uow_factory=lambda: SqlAlchemyAssetUnitOfWork(integration_database.session_factory),
        clock=lambda: now - timedelta(days=365),
    )

    assert boundary_service.expire_due_once(limit=10) == 1
    with integration_database.engine.connect() as connection:
        expiry = (
            connection.execute(
                text(
                    "SELECT a.status, o.event_type, o.payload_json "
                    "FROM assets a JOIN outbox_events o ON o.aggregate_id = a.id "
                    "WHERE a.id = :asset_id AND o.event_type = 'asset.rights.expired'"
                ),
                {"asset_id": expiry_asset_id},
            )
            .mappings()
            .one()
        )
        expiry_audit = (
            connection.execute(
                text(
                    "SELECT actor_type, actor_id, metadata_json FROM audit_events "
                    "WHERE workspace_id = :workspace AND resource_id = :asset_id "
                    "AND action = 'asset.rights.expired' LIMIT 1"
                ),
                {"workspace": WORKSPACE_B, "asset_id": expiry_asset_id},
            )
            .mappings()
            .one()
        )
    assert expiry["status"] == "RIGHTS_EXPIRED"
    expiry_payload = json.loads(expiry["payload_json"])
    assert expiry_payload["change"] == "EXPIRED"
    assert expiry_payload["required_convergence"] == ("REMOVE_EXTERNAL_DERIVATIVES")
    assert expiry_audit["actor_type"] == "SYSTEM"
    assert expiry_audit["actor_id"] == "rights-expiry-scheduler"
    assert json.loads(expiry_audit["metadata_json"])["rights_record_version"] == 1


def test_rights_scanners_use_mysql_time_instead_of_a_skewed_application_clock(
    integration_database,
) -> None:
    with integration_database.engine.connect() as connection:
        database_now = connection.execute(text("SELECT UTC_TIMESTAMP(6)")).scalar_one()
    database_now = database_now.replace(tzinfo=UTC)
    activation_asset_id, activation_version_id = _seed_pending_rights_asset(
        integration_database,
        workspace_id=WORKSPACE_B,
    )
    expiry_asset_id, expiry_version_id = _seed_pending_rights_asset(
        integration_database,
        workspace_id=WORKSPACE_B,
    )
    registration_service = AssetRightsApplicationService(
        uow_factory=lambda: SqlAlchemyAssetUnitOfWork(integration_database.session_factory),
        clock=lambda: database_now,
    )
    pending = registration_service.register(
        workspace_id=WORKSPACE_B,
        asset_id=activation_asset_id,
        actor_id="rights-reviewer",
        idempotency_key="clock-skew-activation-register-0001",
        trace_id="trace-clock-skew-activation",
        request=RightsRecordMutationRequestV1.model_validate(
            _rights_payload(
                activation_version_id,
                valid_from=database_now + timedelta(days=1),
                valid_until=database_now + timedelta(days=30),
            )
        ),
    )
    available = registration_service.register(
        workspace_id=WORKSPACE_B,
        asset_id=expiry_asset_id,
        actor_id="rights-reviewer",
        idempotency_key="clock-skew-expiry-register-0001",
        trace_id="trace-clock-skew-expiry",
        request=RightsRecordMutationRequestV1.model_validate(
            _rights_payload(
                expiry_version_id,
                valid_from=database_now - timedelta(days=1),
                valid_until=database_now + timedelta(days=1),
            )
        ),
    )
    assert pending.asset_state.value == "PENDING_RIGHTS"
    assert available.asset_state.value == "AVAILABLE"

    skewed_service = AssetRightsApplicationService(
        uow_factory=lambda: SqlAlchemyAssetUnitOfWork(integration_database.session_factory),
        clock=lambda: database_now + timedelta(days=2),
    )
    assert skewed_service.activate_due_once(limit=10) == 0
    assert skewed_service.expire_due_once(limit=10) == 0

    with integration_database.engine.connect() as connection:
        states = dict(
            connection.execute(
                text(
                    "SELECT id, status FROM assets "
                    "WHERE id IN (:activation_asset_id, :expiry_asset_id)"
                ),
                {
                    "activation_asset_id": activation_asset_id,
                    "expiry_asset_id": expiry_asset_id,
                },
            ).all()
        )
        scan_events = connection.execute(
            text(
                "SELECT COUNT(*) FROM outbox_events "
                "WHERE aggregate_id IN (:activation_asset_id, :expiry_asset_id) "
                "AND event_type IN ('asset.rights.expired', 'asset.rights.changed') "
                "AND JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.change')) "
                "IN ('ACTIVATED', 'EXPIRED')"
            ),
            {
                "activation_asset_id": activation_asset_id,
                "expiry_asset_id": expiry_asset_id,
            },
        ).scalar_one()
    assert states == {
        activation_asset_id: "PENDING_RIGHTS",
        expiry_asset_id: "AVAILABLE",
    }
    assert scan_events == 0


def test_rights_replacement_cannot_clear_an_administrator_block(
    integration_database,
    integration_settings,
) -> None:
    asset_id, version_id = _seed_pending_rights_asset(integration_database)
    now = datetime.now(UTC)
    app = create_app(_settings(integration_settings))

    with TestClient(app) as client:
        registered = client.post(
            f"/api/v1/assets/{asset_id}/rights",
            headers=_headers("rights-block-bypass-register-0001"),
            json=_rights_payload(
                version_id,
                valid_from=now - timedelta(minutes=1),
                valid_until=now + timedelta(days=30),
            ),
        )
        assert registered.status_code == 201

        blocked = client.post(
            f"/api/v1/assets/{asset_id}:block",
            headers=_headers("rights-block-bypass-admin-0001", admin=True),
            json={
                "expected_asset_version": registered.json()["asset_version"],
                "reason": "legal investigation",
                "evidence_reference": "evidence://admin-block/bypass-regression",
            },
        )
        assert blocked.status_code == 200

        unusable_payload = _rights_payload(
            version_id,
            expected_asset_version=blocked.json()["asset_version"],
            valid_from=now - timedelta(minutes=1),
            valid_until=now + timedelta(days=30),
        )
        unusable_payload["allowed_uses"] = []
        selected_unusable = client.post(
            f"/api/v1/assets/{asset_id}/rights:replace",
            headers=_headers("rights-block-bypass-empty-0001"),
            json=unusable_payload,
        )
        assert selected_unusable.status_code == 200
        assert selected_unusable.json()["asset_state"] == "BLOCKED"

        denied = client.post(
            f"/api/v1/assets/{asset_id}/rights:replace",
            headers=_headers("rights-block-bypass-active-0001"),
            json=_rights_payload(
                version_id,
                expected_asset_version=selected_unusable.json()["asset_version"],
                valid_from=now - timedelta(minutes=1),
                valid_until=now + timedelta(days=30),
            ),
        )

    assert denied.status_code == 409
    assert denied.json()["code"] == "INVALID_TRANSITION"
    with integration_database.engine.connect() as connection:
        asset = (
            connection.execute(
                text(
                    "SELECT status, block_reason, current_rights_record_id, version "
                    "FROM assets WHERE workspace_id = :workspace AND id = :asset_id"
                ),
                {"workspace": WORKSPACE_A, "asset_id": asset_id},
            )
            .mappings()
            .one()
        )
        rights_count = connection.execute(
            text("SELECT COUNT(*) FROM rights_records WHERE asset_id = :asset_id"),
            {"asset_id": asset_id},
        ).scalar_one()

    assert asset == {
        "status": "BLOCKED",
        "block_reason": "ADMINISTRATIVELY_BLOCKED",
        "current_rights_record_id": selected_unusable.json()["current_rights_record"]["id"],
        "version": selected_unusable.json()["asset_version"],
    }
    assert rights_count == 2


def test_activation_scanner_ignores_deny_by_default_rights(
    integration_database,
) -> None:
    asset_id, version_id = _seed_pending_rights_asset(integration_database)
    now = datetime.now(UTC)
    activates_at = now + timedelta(days=1)
    register_service = AssetRightsApplicationService(
        uow_factory=lambda: SqlAlchemyAssetUnitOfWork(integration_database.session_factory),
        clock=lambda: now,
    )
    payload = _rights_payload(
        version_id,
        valid_from=activates_at,
        valid_until=activates_at + timedelta(days=30),
    )
    payload["allowed_uses"] = []
    registered = register_service.register(
        workspace_id=WORKSPACE_A,
        asset_id=asset_id,
        actor_id="rights-reviewer",
        idempotency_key="deny-by-default-register-0001",
        trace_id="trace-deny-by-default-register",
        request=RightsRecordMutationRequestV1.model_validate(payload),
    )
    scanner = AssetRightsApplicationService(
        uow_factory=lambda: SqlAlchemyAssetUnitOfWork(integration_database.session_factory),
        clock=lambda: activates_at,
    )

    assert scanner.activate_due_once(limit=10) == 0
    assert scanner.activate_due_once(limit=10) == 0
    with integration_database.engine.connect() as connection:
        asset = (
            connection.execute(
                text(
                    "SELECT status, version FROM assets "
                    "WHERE workspace_id = :workspace AND id = :asset_id"
                ),
                {"workspace": WORKSPACE_A, "asset_id": asset_id},
            )
            .mappings()
            .one()
        )
        event_count = connection.execute(
            text("SELECT COUNT(*) FROM outbox_events WHERE aggregate_id = :asset_id"),
            {"asset_id": asset_id},
        ).scalar_one()

    assert asset == {
        "status": "PENDING_RIGHTS",
        "version": registered.asset_version,
    }
    assert event_count == 1


def test_administrator_block_upgrades_a_rights_owned_block(
    integration_database,
    integration_settings,
) -> None:
    asset_id, version_id = _seed_pending_rights_asset(integration_database)
    now = datetime.now(UTC)
    app = create_app(_settings(integration_settings))

    with TestClient(app) as client:
        registered = client.post(
            f"/api/v1/assets/{asset_id}/rights",
            headers=_headers("rights-admin-upgrade-register-0001"),
            json=_rights_payload(
                version_id,
                valid_from=now - timedelta(minutes=1),
                valid_until=now + timedelta(days=30),
            ),
        )
        assert registered.status_code == 201

        revoked = client.post(
            f"/api/v1/assets/{asset_id}/rights:revoke",
            headers=_headers("rights-admin-upgrade-revoke-0001"),
            json={
                "expected_asset_version": registered.json()["asset_version"],
                "reason": "license withdrawn",
                "evidence_reference": "evidence://rights/admin-upgrade-revoke",
            },
        )
        assert revoked.status_code == 200
        assert revoked.json()["asset_state"] == "BLOCKED"

        blocked = client.post(
            f"/api/v1/assets/{asset_id}:block",
            headers=_headers("rights-admin-upgrade-block-0001", admin=True),
            json={
                "expected_asset_version": revoked.json()["asset_version"],
                "reason": "legal hold",
                "evidence_reference": "evidence://rights/admin-upgrade-block",
            },
        )
        assert blocked.status_code == 200

        denied = client.post(
            f"/api/v1/assets/{asset_id}/rights:replace",
            headers=_headers("rights-admin-upgrade-active-0001"),
            json=_rights_payload(
                version_id,
                expected_asset_version=blocked.json()["asset_version"],
                valid_from=now - timedelta(minutes=1),
                valid_until=now + timedelta(days=30),
            ),
        )

    assert blocked.json()["asset_state"] == "BLOCKED"
    assert denied.status_code == 409
    assert denied.json()["code"] == "INVALID_TRANSITION"
    with integration_database.engine.connect() as connection:
        asset = (
            connection.execute(
                text(
                    "SELECT status, block_reason, current_rights_record_id, version "
                    "FROM assets WHERE workspace_id = :workspace AND id = :asset_id"
                ),
                {"workspace": WORKSPACE_A, "asset_id": asset_id},
            )
            .mappings()
            .one()
        )
        rights_count = connection.execute(
            text("SELECT COUNT(*) FROM rights_records WHERE asset_id = :asset_id"),
            {"asset_id": asset_id},
        ).scalar_one()

    assert asset == {
        "status": "BLOCKED",
        "block_reason": "ADMINISTRATIVELY_BLOCKED",
        "current_rights_record_id": revoked.json()["current_rights_record"]["id"],
        "version": blocked.json()["asset_version"],
    }
    assert rights_count == 2
