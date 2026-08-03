from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from commercevision_contracts import EmbeddingProviderResultV1, EmbeddingVectorV1
from commercevision_contracts.events import (
    AssetIndexDeleteRequestedPayload,
    AssetIndexRequestedPayload,
)
from commercevision_domain import CollectionSpec, VectorKind, new_uuid7
from commercevision_persistence import (
    MySqlIndexingAuthority,
    MySqlProductFusedIndexRequestService,
    MySqlProductLexicalSearch,
)
from sqlalchemy import text

pytestmark = pytest.mark.integration

WORKSPACE = "workspace-product-fused"
PROVIDER = "alibaba-model-studio"


def _seed_confirmed_brief_with_image(database) -> tuple[str, str, str]:
    product_id = new_uuid7()
    product_brief_id = new_uuid7()
    product_brief_version_id = new_uuid7()
    asset_id = new_uuid7()
    asset_version_id = new_uuid7()
    rights_record_id = new_uuid7()
    field_ids = [new_uuid7() for _ in range(6)]
    now = datetime.now(UTC).replace(tzinfo=None)
    deadline = now + timedelta(hours=24)

    fields = (
        (
            field_ids[0],
            "common.identity",
            {"kind": "IDENTITY", "display_name": "鎏金口红"},
            "PRODUCT_DATA",
        ),
        (field_ids[1], "common.brand", {"kind": "TEXT", "text": "星河 Beauty"}, "PRODUCT_DATA"),
        (
            field_ids[2],
            "common.visible_text_summary",
            {"kind": "TEXT", "text": "净含量 3.5g"},
            "MODEL",
        ),
        (
            field_ids[3],
            "common.approved_labels",
            {"kind": "TEXT_LIST", "items": ["哑光", "Summer", "Lipstick"]},
            "HUMAN",
        ),
        (
            field_ids[4],
            "common.approved_notes",
            {"kind": "TEXT_LIST", "items": ["柜台核验"]},
            "HUMAN",
        ),
        (
            field_ids[5],
            "internal.raw_prompt",
            {"kind": "TEXT", "text": "SECRET RAW PROMPT"},
            "MODEL",
        ),
    )

    with database.engine.begin() as connection:
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        connection.execute(
            text(
                "INSERT INTO products "
                "(id, workspace_id, source_namespace, external_id, source_version, title, "
                "category_code, brand, attributes_json, expires_at, version, created_at, "
                "updated_at) "
                "VALUES (:id, :workspace, 'fixture', 'sku-001', '1', :title, 'BEAUTY', "
                ":brand, '{}', :deadline, 1, :now, :now)"
            ),
            {
                "id": product_id,
                "workspace": WORKSPACE,
                "title": "鎏金口红 Summer",
                "brand": "星河 Beauty",
                "deadline": deadline,
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO assets "
                "(id, workspace_id, retention_class, asset_kind, workflow_id, product_id, sku_id, "
                "status, block_reason, current_version_id, current_rights_record_id, "
                "retention_deadline, version, created_at, updated_at) VALUES "
                "(:asset, :workspace, 'FOUNDATION', 'IMAGE', NULL, :product, NULL, 'AVAILABLE', "
                "NULL, :asset_version, :rights, NULL, 3, :now, :now)"
            ),
            {
                "asset": asset_id,
                "workspace": WORKSPACE,
                "product": product_id,
                "asset_version": asset_version_id,
                "rights": rights_record_id,
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO asset_versions "
                "(id, workspace_id, asset_id, version_number, upload_session_id, filename, sha256, "
                "byte_size, declared_mime, detected_mime, image_format, width, height, "
                "frame_count, "
                "category, role, integrity_policy_version, validation_policy_version, "
                "validation_transfer_policy_version, validation_transfer_policy_snapshot_sha256, "
                "created_at) VALUES (:version, :workspace, :asset, 1, :upload, 'lipstick.png', "
                ":sha, 128, 'image/png', 'image/png', 'PNG', 64, 64, 1, 'BEAUTY', 'HERO', "
                "'integrity-v1', 'validation-v1', 'transfer-v1', :transfer_sha, :now)"
            ),
            {
                "version": asset_version_id,
                "workspace": WORKSPACE,
                "asset": asset_id,
                "upload": new_uuid7(),
                "sha": "a" * 64,
                "transfer_sha": "b" * 64,
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO rights_records "
                "(id, workspace_id, asset_id, asset_version_id, version_number, decision, "
                "owner_reference, source, license_reference, derivative_allowed, "
                "public_demo_allowed, evidence_reference, terms_sha256, valid_from, valid_until, "
                "perpetual, supersedes_record_id, created_by, created_at, permissions_sealed_at) "
                "VALUES (:rights, :workspace, :asset, :version, 1, 'GRANT', 'owner', 'contract', "
                "'license', 0, 0, 'evidence://fused', :terms, :valid_from, NULL, 1, NULL, 'test', "
                ":now, NULL)"
            ),
            {
                "rights": rights_record_id,
                "workspace": WORKSPACE,
                "asset": asset_id,
                "version": asset_version_id,
                "terms": "c" * 64,
                "valid_from": now - timedelta(days=1),
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO rights_record_uses "
                "(workspace_id, asset_id, rights_record_id, allowed_use, created_at) "
                "VALUES (:workspace, :asset, :rights, 'RETRIEVAL', :now)"
            ),
            {"workspace": WORKSPACE, "asset": asset_id, "rights": rights_record_id, "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO rights_record_providers "
                "(workspace_id, asset_id, rights_record_id, allowed_provider, created_at) "
                "VALUES (:workspace, :asset, :rights, :provider, :now)"
            ),
            {
                "workspace": WORKSPACE,
                "asset": asset_id,
                "rights": rights_record_id,
                "provider": PROVIDER,
                "now": now,
            },
        )
        connection.execute(
            text("UPDATE rights_records SET permissions_sealed_at = :now WHERE id = :rights"),
            {"rights": rights_record_id, "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO product_briefs "
                "(id, workspace_id, workflow_id, product_id, operation_id, created_by, state, "
                "current_version_id, confirmed_version_id, version, retention_class, "
                "retention_deadline, created_at, updated_at) VALUES "
                "(:brief, :workspace, :workflow, :product, :operation, 'reviewer', 'CONFIRMED', "
                ":version, :version, 2, 'TASK', :deadline, :now, :now)"
            ),
            {
                "brief": product_brief_id,
                "workspace": WORKSPACE,
                "workflow": new_uuid7(),
                "product": product_id,
                "operation": new_uuid7(),
                "version": product_brief_version_id,
                "deadline": deadline,
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO product_brief_versions "
                "(id, workspace_id, product_brief_id, version_number, supersedes_version_id, "
                "category, common_schema_version, category_schema_version, payload_sha256, "
                "changed_paths_json, confirmation_required, unresolved_field_count, "
                "review_policy_version, source, prompt_version, provider_call_id, actor_id, "
                "revision_reason, retention_class, retention_deadline, created_at) VALUES "
                "(:version, :workspace, :brief, 1, NULL, 'BEAUTY', 'common-v1', 'beauty-v1', "
                ":payload_sha, :changed_paths, 0, 0, 'review-v1', 'HUMAN', NULL, NULL, "
                "'reviewer', 'approved for indexing', 'TASK', :deadline, :now)"
            ),
            {
                "version": product_brief_version_id,
                "workspace": WORKSPACE,
                "brief": product_brief_id,
                "payload_sha": "d" * 64,
                "changed_paths": json.dumps([field[1] for field in fields]),
                "deadline": deadline,
                "now": now,
            },
        )
        for field_id, path, value, source in fields:
            connection.execute(
                text(
                    "INSERT INTO product_brief_fields "
                    "(id, workspace_id, product_brief_id, product_brief_version_id, path, "
                    "value_json, confidence, source, conflict, review_required, `sensitive`, "
                    "review_reasons_json, created_at) VALUES "
                    "(:id, :workspace, :brief, :version, :path, :value, 1, :source, 'NONE', "
                    "0, 0, '[]', :now)"
                ),
                {
                    "id": field_id,
                    "workspace": WORKSPACE,
                    "brief": product_brief_id,
                    "version": product_brief_version_id,
                    "path": path,
                    "value": json.dumps(value, ensure_ascii=False),
                    "source": source,
                    "now": now,
                },
            )
        connection.execute(
            text(
                "INSERT INTO product_brief_evidence "
                "(id, workspace_id, product_brief_id, product_brief_version_id, field_id, "
                "source_asset_version_id, kind, reference, region_json, excerpt_sha256, "
                "created_at) "
                "VALUES (:id, :workspace, :brief, :brief_version, :field, :asset_version, "
                "'IMAGE_REGION', 'asset://controlled', NULL, NULL, :now)"
            ),
            {
                "id": new_uuid7(),
                "workspace": WORKSPACE,
                "brief": product_brief_id,
                "brief_version": product_brief_version_id,
                "field": field_ids[0],
                "asset_version": asset_version_id,
                "now": now,
            },
        )
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
    return product_brief_id, product_brief_version_id, asset_version_id


def _requests(database) -> MySqlProductFusedIndexRequestService:
    return MySqlProductFusedIndexRequestService(
        session_factory=database.session_factory,
        collection_spec=CollectionSpec.create(
            model_family="qwen3-vl-embedding",
            pinned_revision="2026-06-30",
            dimension=4,
            vector_kind=VectorKind.PRODUCT_FUSED,
            schema_version=1,
            index_spec_version="hnsw-cosine-v1",
        ),
        provider=PROVIDER,
        model_id="qwen3-vl-embedding",
        model_configuration_version="embedding-config-v1",
        preprocessing_version="product-fused-v1",
    )


def _confirm_revised_brief(
    database,
    *,
    product_brief_id: str,
    previous_version_id: str,
    source_asset_version_id: str,
    controlled_change: bool = True,
) -> str:
    version_id = new_uuid7()
    identity_field_id = new_uuid7()
    now = datetime.now(UTC).replace(tzinfo=None)
    deadline = now + timedelta(hours=24)
    fields = (
        (
            identity_field_id,
            "common.identity",
            {"kind": "IDENTITY", "display_name": "鎏金口红"},
            "PRODUCT_DATA",
        ),
        (
            new_uuid7(),
            "common.brand",
            {"kind": "TEXT", "text": "星河 Pro" if controlled_change else "星河 Beauty"},
            "HUMAN" if controlled_change else "PRODUCT_DATA",
        ),
        (
            new_uuid7(),
            "common.visible_text_summary",
            {"kind": "TEXT", "text": "净含量 3.5g"},
            "MODEL",
        ),
        (
            new_uuid7(),
            "common.approved_labels",
            {
                "kind": "TEXT_LIST",
                "items": ["哑光", "Summer"]
                if controlled_change
                else ["哑光", "Summer", "Lipstick"],
            },
            "HUMAN",
        ),
        (
            new_uuid7(),
            "common.approved_notes",
            {"kind": "TEXT_LIST", "items": ["柜台核验"]},
            "HUMAN",
        ),
    )
    with database.engine.begin() as connection:
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        connection.execute(
            text(
                "INSERT INTO product_brief_versions "
                "(id, workspace_id, product_brief_id, version_number, supersedes_version_id, "
                "category, common_schema_version, category_schema_version, payload_sha256, "
                "changed_paths_json, confirmation_required, unresolved_field_count, "
                "review_policy_version, source, prompt_version, provider_call_id, actor_id, "
                "revision_reason, retention_class, retention_deadline, created_at) VALUES "
                "(:version, :workspace, :brief, 2, :previous, 'BEAUTY', 'common-v1', "
                "'beauty-v1', :payload_sha, '[\"common.brand\"]', 0, 0, 'review-v1', "
                "'HUMAN', NULL, NULL, 'reviewer', 'approved revision', 'TASK', :deadline, :now)"
            ),
            {
                "version": version_id,
                "workspace": WORKSPACE,
                "brief": product_brief_id,
                "previous": previous_version_id,
                "payload_sha": "e" * 64,
                "deadline": deadline,
                "now": now,
            },
        )
        for field_id, path, value, source in fields:
            connection.execute(
                text(
                    "INSERT INTO product_brief_fields "
                    "(id, workspace_id, product_brief_id, product_brief_version_id, path, "
                    "value_json, confidence, source, conflict, review_required, `sensitive`, "
                    "review_reasons_json, created_at) VALUES "
                    "(:id, :workspace, :brief, :version, :path, :value, 1, :source, 'NONE', "
                    "0, 0, '[]', :now)"
                ),
                {
                    "id": field_id,
                    "workspace": WORKSPACE,
                    "brief": product_brief_id,
                    "version": version_id,
                    "path": path,
                    "value": json.dumps(value, ensure_ascii=False),
                    "source": source,
                    "now": now,
                },
            )
        connection.execute(
            text(
                "INSERT INTO product_brief_evidence "
                "(id, workspace_id, product_brief_id, product_brief_version_id, field_id, "
                "source_asset_version_id, kind, reference, region_json, excerpt_sha256, "
                "created_at) VALUES (:id, :workspace, :brief, :version, :field, "
                ":asset_version, 'IMAGE_REGION', 'asset://controlled', NULL, NULL, :now)"
            ),
            {
                "id": new_uuid7(),
                "workspace": WORKSPACE,
                "brief": product_brief_id,
                "version": version_id,
                "field": identity_field_id,
                "asset_version": source_asset_version_id,
                "now": now,
            },
        )
        connection.execute(
            text(
                "UPDATE product_briefs SET current_version_id = :version, "
                "confirmed_version_id = :version, version = version + 1, updated_at = :now "
                "WHERE workspace_id = :workspace AND id = :brief"
            ),
            {
                "version": version_id,
                "now": now,
                "workspace": WORKSPACE,
                "brief": product_brief_id,
            },
        )
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
    return version_id


def test_confirmed_brief_request_is_atomic_and_idempotent(integration_database) -> None:
    brief_id, brief_version_id, asset_version_id = _seed_confirmed_brief_with_image(
        integration_database
    )
    requests = _requests(integration_database)

    first = requests.request_confirmed_brief(
        workspace_id=WORKSPACE,
        product_brief_id=brief_id,
        product_brief_version_id=brief_version_id,
    )
    replay = requests.request_confirmed_brief(
        workspace_id=WORKSPACE,
        product_brief_id=brief_id,
        product_brief_version_id=brief_version_id,
    )

    assert len(first) == 1
    assert first[0].created is True
    assert first[0].asset_version_id == asset_version_id
    assert len(replay) == 1
    assert replay[0].created is False
    assert replay[0].embedding_record_id == first[0].embedding_record_id
    assert replay[0].search_document_id == first[0].search_document_id

    with integration_database.engine.connect() as connection:
        counts = connection.execute(
            text(
                "SELECT "
                "(SELECT COUNT(*) FROM embedding_records WHERE vector_kind = 'PRODUCT_FUSED'), "
                "(SELECT COUNT(*) FROM product_search_documents), "
                "(SELECT COUNT(*) FROM durable_operations WHERE kind = 'ASSET_INDEXING'), "
                "(SELECT COUNT(*) FROM outbox_events WHERE event_type = 'asset.index.requested')"
            )
        ).one()
        document = (
            connection.execute(
                text(
                    "SELECT title, labels, ocr_summary, product_brief_summary, approved_notes, "
                    "retention_class, retention_deadline, state FROM product_search_documents"
                )
            )
            .mappings()
            .one()
        )
        event_payload = connection.scalar(
            text(
                "SELECT payload_json FROM outbox_events WHERE event_type = 'asset.index.requested'"
            )
        )

    assert counts == (1, 1, 1, 1)
    assert document["title"] == "鎏金口红"
    assert document["labels"] == "lipstick\nsummer\n哑光"
    assert document["ocr_summary"] == "净含量 3.5g"
    assert "common.identity=鎏金口红" in document["product_brief_summary"]
    assert "secret raw prompt" not in document["product_brief_summary"]
    assert document["approved_notes"] == "柜台核验"
    assert document["retention_class"] == "TASK"
    assert document["retention_deadline"] is not None
    assert document["state"] == "PENDING"

    payload = AssetIndexRequestedPayload.model_validate(json.loads(event_payload))
    assert payload.vector_kind == "PRODUCT_FUSED"
    assert payload.product_brief_version_id == brief_version_id
    assert payload.controlled_text_sha256 is not None
    authority = MySqlIndexingAuthority(integration_database.session_factory)
    assert authority.validate_request_event(payload) is True
    assert (
        authority.validate_request_event(
            payload.model_copy(update={"controlled_text_sha256": "f" * 64})
        )
        is False
    )
    assert (
        authority.validate_request_event(
            payload.model_copy(update={"product_brief_version_id": new_uuid7()})
        )
        is False
    )


def test_concurrent_confirmed_brief_requests_reload_one_atomic_winner(
    integration_database,
) -> None:
    brief_id, brief_version_id, _ = _seed_confirmed_brief_with_image(integration_database)
    requests = _requests(integration_database)

    def request_once(_: int):
        return requests.request_confirmed_brief(
            workspace_id=WORKSPACE,
            product_brief_id=brief_id,
            product_brief_version_id=brief_version_id,
        )[0]

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(request_once, range(4)))

    assert sum(result.created for result in results) == 1
    assert len({result.embedding_record_id for result in results}) == 1
    assert len({result.search_document_id for result in results}) == 1
    assert len({result.operation.operation_id for result in results}) == 1
    with integration_database.engine.connect() as connection:
        counts = connection.execute(
            text(
                "SELECT (SELECT COUNT(*) FROM embedding_records), "
                "(SELECT COUNT(*) FROM product_search_documents), "
                "(SELECT COUNT(*) FROM durable_operations WHERE kind = 'ASSET_INDEXING'), "
                "(SELECT COUNT(*) FROM outbox_events WHERE event_type = 'asset.index.requested')"
            )
        ).one()
    assert counts == (1, 1, 1, 1)


def test_unconfirmed_product_title_drift_does_not_change_fused_identity(
    integration_database,
) -> None:
    brief_id, brief_version_id, _ = _seed_confirmed_brief_with_image(integration_database)
    requests = _requests(integration_database)
    first = requests.request_confirmed_brief(
        workspace_id=WORKSPACE,
        product_brief_id=brief_id,
        product_brief_version_id=brief_version_id,
    )[0]
    with integration_database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE products SET title = 'UNCONFIRMED SIDEBAND TITLE', "
                "version = version + 1 WHERE workspace_id = :workspace"
            ),
            {"workspace": WORKSPACE},
        )

    replay = requests.request_confirmed_brief(
        workspace_id=WORKSPACE,
        product_brief_id=brief_id,
        product_brief_version_id=brief_version_id,
    )[0]

    assert replay.created is False
    assert replay.embedding_record_id == first.embedding_record_id
    with integration_database.engine.connect() as connection:
        title = connection.scalar(text("SELECT title FROM product_search_documents"))
        count = connection.scalar(text("SELECT COUNT(*) FROM embedding_records"))
    assert title == "鎏金口红"
    assert count == 1


def test_changed_confirmed_brief_creates_one_new_record_and_stales_old_input(
    integration_database,
) -> None:
    brief_id, first_version_id, asset_version_id = _seed_confirmed_brief_with_image(
        integration_database
    )
    requests = _requests(integration_database)
    first = requests.request_confirmed_brief(
        workspace_id=WORKSPACE,
        product_brief_id=brief_id,
        product_brief_version_id=first_version_id,
    )[0]
    second_version_id = _confirm_revised_brief(
        integration_database,
        product_brief_id=brief_id,
        previous_version_id=first_version_id,
        source_asset_version_id=asset_version_id,
    )

    second = requests.request_confirmed_brief(
        workspace_id=WORKSPACE,
        product_brief_id=brief_id,
        product_brief_version_id=second_version_id,
    )[0]
    replay = requests.request_confirmed_brief(
        workspace_id=WORKSPACE,
        product_brief_id=brief_id,
        product_brief_version_id=second_version_id,
    )[0]

    assert second.created is True
    assert second.embedding_record_id != first.embedding_record_id
    assert replay.created is False
    assert replay.embedding_record_id == second.embedding_record_id
    with integration_database.engine.connect() as connection:
        states = connection.execute(
            text(
                "SELECT e.id, e.state, d.state FROM embedding_records e "
                "JOIN product_search_documents d ON d.embedding_record_id = e.id "
                "ORDER BY e.created_at, e.id"
            )
        ).all()
        requested_event_count = connection.scalar(
            text("SELECT COUNT(*) FROM outbox_events WHERE event_type = 'asset.index.requested'")
        )
    assert states == [
        (first.embedding_record_id, "STALE", "STALE"),
        (second.embedding_record_id, "PENDING", "PENDING"),
    ]
    assert requested_event_count == 2


def test_equivalent_confirmed_brief_version_advances_provenance_without_reindex(
    integration_database,
) -> None:
    brief_id, brief_version_id, asset_version_id = _seed_confirmed_brief_with_image(
        integration_database
    )
    requests = _requests(integration_database)
    first = requests.request_confirmed_brief(
        workspace_id=WORKSPACE,
        product_brief_id=brief_id,
        product_brief_version_id=brief_version_id,
    )[0]
    with integration_database.engine.connect() as connection:
        original_payload = AssetIndexRequestedPayload.model_validate(
            json.loads(
                connection.scalar(
                    text(
                        "SELECT payload_json FROM outbox_events "
                        "WHERE event_type = 'asset.index.requested'"
                    )
                )
            )
        )
    equivalent_version_id = _confirm_revised_brief(
        integration_database,
        product_brief_id=brief_id,
        previous_version_id=brief_version_id,
        source_asset_version_id=asset_version_id,
        controlled_change=False,
    )

    replay = requests.request_confirmed_brief(
        workspace_id=WORKSPACE,
        product_brief_id=brief_id,
        product_brief_version_id=equivalent_version_id,
    )[0]

    assert replay.created is False
    assert replay.embedding_record_id == first.embedding_record_id
    with integration_database.engine.connect() as connection:
        facts = connection.execute(
            text(
                "SELECT e.product_brief_version_id, d.product_brief_version_id, "
                "e.operation_id FROM embedding_records e "
                "JOIN product_search_documents d ON d.embedding_record_id = e.id"
            )
        ).one()
        request_count = connection.scalar(
            text("SELECT COUNT(*) FROM outbox_events WHERE event_type = 'asset.index.requested'")
        )
    assert facts == (
        equivalent_version_id,
        equivalent_version_id,
        first.operation.operation_id,
    )
    assert request_count == 1
    assert (
        MySqlIndexingAuthority(integration_database.session_factory).validate_request_event(
            original_payload
        )
        is True
    )


def test_changed_confirmed_brief_can_delete_the_indexed_superseded_generation(
    integration_database,
) -> None:
    brief_id, brief_version_id, asset_version_id = _seed_confirmed_brief_with_image(
        integration_database
    )
    requests = _requests(integration_database)
    first = requests.request_confirmed_brief(
        workspace_id=WORKSPACE,
        product_brief_id=brief_id,
        product_brief_version_id=brief_version_id,
    )[0]
    authority = MySqlIndexingAuthority(integration_database.session_factory)
    target = authority.load_for_provisioning(first.operation)
    authority.activate_collection(target)
    claimed = authority.claim_for_submission(first.operation)
    with_provider = authority.record_provider_result(
        claimed,
        EmbeddingProviderResultV1(
            vectors=[EmbeddingVectorV1(values=[0.1, 0.2, 0.3, 0.4])],
            provider=PROVIDER,
            provider_request_id="provider-before-brief-revision",
            actual_model="qwen3-vl-embedding-2026-06-30",
            latency_ms=1,
        ),
    )
    assert authority.commit_after_upsert(with_provider).indexed is True
    revised_version_id = _confirm_revised_brief(
        integration_database,
        product_brief_id=brief_id,
        previous_version_id=brief_version_id,
        source_asset_version_id=asset_version_id,
    )
    second = requests.request_confirmed_brief(
        workspace_id=WORKSPACE,
        product_brief_id=brief_id,
        product_brief_version_id=revised_version_id,
    )[0]
    assert second.created is True
    with integration_database.engine.connect() as connection:
        delete_payload = AssetIndexDeleteRequestedPayload.model_validate(
            json.loads(
                connection.scalar(
                    text(
                        "SELECT payload_json FROM outbox_events "
                        "WHERE event_type = 'asset.index.delete-requested'"
                    )
                )
            )
        )

    assert authority.complete_delete(delete_payload) is True
    with integration_database.engine.connect() as connection:
        states = connection.execute(
            text(
                "SELECT e.id, e.state, d.state FROM embedding_records e "
                "JOIN product_search_documents d ON d.embedding_record_id = e.id "
                "ORDER BY e.created_at, e.id"
            )
        ).all()
    assert states == [
        (first.embedding_record_id, "DELETED", "DELETED"),
        (second.embedding_record_id, "PENDING", "PENDING"),
    ]


def test_product_fused_authority_loads_controlled_text_and_commits_document(
    integration_database,
) -> None:
    brief_id, brief_version_id, _ = _seed_confirmed_brief_with_image(integration_database)
    requested = _requests(integration_database).request_confirmed_brief(
        workspace_id=WORKSPACE,
        product_brief_id=brief_id,
        product_brief_version_id=brief_version_id,
    )[0]
    authority = MySqlIndexingAuthority(integration_database.session_factory)

    target = authority.load_for_provisioning(requested.operation)
    assert target.vector_kind is VectorKind.PRODUCT_FUSED
    assert target.product_brief_version_id == brief_version_id
    assert target.controlled_text_sha256 is not None
    assert target.controlled_text is not None
    assert '"title":"鎏金口红"' in target.controlled_text
    assert '"summer"' in target.controlled_text
    assert "secret raw prompt" not in target.controlled_text

    authority.activate_collection(target)
    claimed = authority.claim_for_submission(requested.operation)
    with_provider = authority.record_provider_result(
        claimed,
        EmbeddingProviderResultV1(
            vectors=[EmbeddingVectorV1(values=[0.1, 0.2, 0.3, 0.4])],
            provider=PROVIDER,
            provider_request_id="provider-fused-1",
            actual_model="qwen3-vl-embedding-2026-06-30",
            latency_ms=1,
        ),
    )

    assert authority.commit_after_upsert(with_provider).indexed is True
    with integration_database.engine.connect() as connection:
        states = connection.execute(
            text(
                "SELECT e.state, d.state FROM embedding_records e "
                "JOIN product_search_documents d ON d.embedding_record_id = e.id"
            )
        ).one()
    assert states == ("INDEXED", "INDEXED")


def test_rights_invalidation_converges_fused_vector_and_document_to_deleted(
    integration_database,
) -> None:
    brief_id, brief_version_id, asset_version_id = _seed_confirmed_brief_with_image(
        integration_database
    )
    requested = _requests(integration_database).request_confirmed_brief(
        workspace_id=WORKSPACE,
        product_brief_id=brief_id,
        product_brief_version_id=brief_version_id,
    )[0]
    authority = MySqlIndexingAuthority(integration_database.session_factory)
    target = authority.load_for_provisioning(requested.operation)
    authority.activate_collection(target)
    claimed = authority.claim_for_submission(requested.operation)
    with_provider = authority.record_provider_result(
        claimed,
        EmbeddingProviderResultV1(
            vectors=[EmbeddingVectorV1(values=[0.1, 0.2, 0.3, 0.4])],
            provider=PROVIDER,
            provider_request_id="provider-fused-delete",
            actual_model="qwen3-vl-embedding-2026-06-30",
            latency_ms=1,
        ),
    )
    assert authority.commit_after_upsert(with_provider).indexed is True

    with integration_database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE assets SET status = 'BLOCKED', block_reason = 'RIGHTS_INVALID', "
                "version = version + 1 WHERE workspace_id = :workspace AND id = :asset"
            ),
            {"workspace": WORKSPACE, "asset": target.asset_id},
        )

    assert (
        authority.mark_current_asset_stale(
            workspace_id=WORKSPACE,
            asset_id=target.asset_id,
            asset_version_id=asset_version_id,
            reason="RIGHTS_INVALID",
        )
        == 1
    )
    with integration_database.engine.connect() as connection:
        states = connection.execute(
            text(
                "SELECT e.state, d.state FROM embedding_records e "
                "JOIN product_search_documents d ON d.embedding_record_id = e.id"
            )
        ).one()
        delete_payload_json = connection.scalar(
            text(
                "SELECT payload_json FROM outbox_events "
                "WHERE event_type = 'asset.index.delete-requested'"
            )
        )
    assert states == ("DELETE_PENDING", "DELETE_PENDING")
    assert (
        MySqlProductLexicalSearch(integration_database.session_factory).search(
            workspace_id=WORKSPACE,
            query="鎏金口红",
            limit=10,
        )
        == ()
    )

    delete_payload = AssetIndexDeleteRequestedPayload.model_validate(
        json.loads(delete_payload_json)
    )
    assert delete_payload.embedding_record_id == requested.embedding_record_id
    assert authority.complete_delete(delete_payload) is True
    with integration_database.engine.connect() as connection:
        deleted = connection.execute(
            text(
                "SELECT e.state, d.state FROM embedding_records e "
                "JOIN product_search_documents d ON d.embedding_record_id = e.id"
            )
        ).one()
    assert deleted == ("DELETED", "DELETED")


def test_rights_regrant_reopens_same_fused_input_with_a_new_operation(
    integration_database,
) -> None:
    brief_id, brief_version_id, asset_version_id = _seed_confirmed_brief_with_image(
        integration_database
    )
    requests = _requests(integration_database)
    first = requests.request_confirmed_brief(
        workspace_id=WORKSPACE,
        product_brief_id=brief_id,
        product_brief_version_id=brief_version_id,
    )[0]
    authority = MySqlIndexingAuthority(integration_database.session_factory)
    target = authority.load_for_provisioning(first.operation)
    authority.activate_collection(target)
    claimed = authority.claim_for_submission(first.operation)
    with_provider = authority.record_provider_result(
        claimed,
        EmbeddingProviderResultV1(
            vectors=[EmbeddingVectorV1(values=[0.1, 0.2, 0.3, 0.4])],
            provider=PROVIDER,
            provider_request_id="provider-before-regrant",
            actual_model="qwen3-vl-embedding-2026-06-30",
            latency_ms=1,
        ),
    )
    assert authority.commit_after_upsert(with_provider).indexed is True
    with integration_database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE assets SET status = 'BLOCKED', block_reason = 'RIGHTS_INVALID', "
                "version = version + 1 WHERE workspace_id = :workspace AND id = :asset"
            ),
            {"workspace": WORKSPACE, "asset": target.asset_id},
        )
    assert (
        authority.mark_current_asset_stale(
            workspace_id=WORKSPACE,
            asset_id=target.asset_id,
            asset_version_id=asset_version_id,
            reason="RIGHTS_INVALID",
        )
        == 1
    )
    with integration_database.engine.connect() as connection:
        delete_payload = AssetIndexDeleteRequestedPayload.model_validate(
            json.loads(
                connection.scalar(
                    text(
                        "SELECT payload_json FROM outbox_events "
                        "WHERE event_type = 'asset.index.delete-requested'"
                    )
                )
            )
        )
    assert authority.complete_delete(delete_payload) is True
    with integration_database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE assets SET status = 'AVAILABLE', block_reason = NULL, "
                "version = version + 1 WHERE workspace_id = :workspace AND id = :asset"
            ),
            {"workspace": WORKSPACE, "asset": target.asset_id},
        )

    reopened = requests.request_current_product_fused_for_asset(
        workspace_id=WORKSPACE,
        asset_id=target.asset_id,
    )[0]

    assert reopened.created is True
    assert reopened.embedding_record_id == first.embedding_record_id
    assert reopened.operation.operation_id != first.operation.operation_id
    with integration_database.engine.connect() as connection:
        facts = connection.execute(
            text(
                "SELECT e.state, d.state, e.operation_id FROM embedding_records e "
                "JOIN product_search_documents d ON d.embedding_record_id = e.id"
            )
        ).one()
        request_count = connection.scalar(
            text("SELECT COUNT(*) FROM outbox_events WHERE event_type = 'asset.index.requested'")
        )
    assert facts == ("PENDING", "PENDING", reopened.operation.operation_id)
    assert request_count == 2


def test_rights_invalidation_stales_pending_fused_facts_without_delete_command(
    integration_database,
) -> None:
    brief_id, brief_version_id, asset_version_id = _seed_confirmed_brief_with_image(
        integration_database
    )
    requested = _requests(integration_database).request_confirmed_brief(
        workspace_id=WORKSPACE,
        product_brief_id=brief_id,
        product_brief_version_id=brief_version_id,
    )[0]
    with integration_database.engine.begin() as connection:
        asset_id = connection.scalar(
            text("SELECT asset_id FROM embedding_records WHERE id = :embedding_record_id"),
            {"embedding_record_id": requested.embedding_record_id},
        )
        connection.execute(
            text(
                "UPDATE assets SET status = 'BLOCKED', block_reason = 'RIGHTS_INVALID', "
                "version = version + 1 WHERE workspace_id = :workspace AND id = :asset"
            ),
            {"workspace": WORKSPACE, "asset": asset_id},
        )

    authority = MySqlIndexingAuthority(integration_database.session_factory)
    assert (
        authority.mark_current_asset_stale(
            workspace_id=WORKSPACE,
            asset_id=asset_id,
            asset_version_id=asset_version_id,
            reason="RIGHTS_INVALID",
        )
        == 1
    )
    with integration_database.engine.connect() as connection:
        facts = connection.execute(
            text(
                "SELECT e.state, d.state FROM embedding_records e "
                "JOIN product_search_documents d ON d.embedding_record_id = e.id"
            )
        ).one()
        delete_count = connection.scalar(
            text(
                "SELECT COUNT(*) FROM outbox_events "
                "WHERE event_type = 'asset.index.delete-requested'"
            )
        )
    assert facts == ("STALE", "STALE")
    assert delete_count == 0


def test_terminal_fused_failure_stales_lexical_document_atomically(
    integration_database,
) -> None:
    brief_id, brief_version_id, _ = _seed_confirmed_brief_with_image(integration_database)
    requested = _requests(integration_database).request_confirmed_brief(
        workspace_id=WORKSPACE,
        product_brief_id=brief_id,
        product_brief_version_id=brief_version_id,
    )[0]
    authority = MySqlIndexingAuthority(integration_database.session_factory)

    assert authority.mark_terminal_failure(requested.operation) is True
    assert authority.mark_terminal_failure(requested.operation) is True

    with integration_database.engine.connect() as connection:
        facts = connection.execute(
            text(
                "SELECT e.state, d.state FROM embedding_records e "
                "JOIN product_search_documents d ON d.embedding_record_id = e.id"
            )
        ).one()
    assert facts == ("PERMANENT_FAILED", "STALE")


@pytest.mark.parametrize("query", ["鎏金口红", "summer lipstick", "鎏金 summer"])
def test_cjk_fulltext_returns_literal_chinese_english_and_mixed_queries(
    integration_database,
    query: str,
) -> None:
    brief_id, brief_version_id, asset_version_id = _seed_confirmed_brief_with_image(
        integration_database
    )
    requested = _requests(integration_database).request_confirmed_brief(
        workspace_id=WORKSPACE,
        product_brief_id=brief_id,
        product_brief_version_id=brief_version_id,
    )[0]
    authority = MySqlIndexingAuthority(integration_database.session_factory)
    target = authority.load_for_provisioning(requested.operation)
    authority.activate_collection(target)
    claimed = authority.claim_for_submission(requested.operation)
    with_provider = authority.record_provider_result(
        claimed,
        EmbeddingProviderResultV1(
            vectors=[EmbeddingVectorV1(values=[0.1, 0.2, 0.3, 0.4])],
            provider=PROVIDER,
            provider_request_id=f"provider-{query}",
            actual_model="qwen3-vl-embedding-2026-06-30",
            latency_ms=1,
        ),
    )
    assert authority.commit_after_upsert(with_provider).indexed is True

    hits = MySqlProductLexicalSearch(integration_database.session_factory).search(
        workspace_id=WORKSPACE, query=query, limit=10
    )

    assert len(hits) == 1
    assert hits[0].asset_version_id == asset_version_id
    assert hits[0].embedding_record_id == requested.embedding_record_id
    assert hits[0].score > 0


def test_cjk_fulltext_hides_an_indexed_task_document_after_retention_expiry(
    integration_database,
) -> None:
    brief_id, brief_version_id, _ = _seed_confirmed_brief_with_image(integration_database)
    requested = _requests(integration_database).request_confirmed_brief(
        workspace_id=WORKSPACE,
        product_brief_id=brief_id,
        product_brief_version_id=brief_version_id,
    )[0]
    authority = MySqlIndexingAuthority(integration_database.session_factory)
    target = authority.load_for_provisioning(requested.operation)
    authority.activate_collection(target)
    claimed = authority.claim_for_submission(requested.operation)
    with_provider = authority.record_provider_result(
        claimed,
        EmbeddingProviderResultV1(
            vectors=[EmbeddingVectorV1(values=[0.1, 0.2, 0.3, 0.4])],
            provider=PROVIDER,
            provider_request_id="provider-before-retention-expiry",
            actual_model="qwen3-vl-embedding-2026-06-30",
            latency_ms=1,
        ),
    )
    assert authority.commit_after_upsert(with_provider).indexed is True
    with integration_database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE product_search_documents "
                "SET retention_deadline = UTC_TIMESTAMP(6) - INTERVAL 1 SECOND"
            )
        )

    assert (
        MySqlProductLexicalSearch(integration_database.session_factory).search(
            workspace_id=WORKSPACE,
            query="鎏金口红",
            limit=10,
        )
        == ()
    )


def test_cjk_fulltext_query_plan_uses_ngram_index(integration_database) -> None:
    with integration_database.engine.connect() as connection:
        plan = (
            connection.execute(
                text(
                    "EXPLAIN SELECT id FROM product_search_documents "
                    "FORCE INDEX (ft_product_search_cjk) "
                    "WHERE workspace_id = :workspace AND state = 'INDEXED' AND "
                    "MATCH(title, labels, ocr_summary, product_brief_summary, approved_notes) "
                    "AGAINST (:query IN NATURAL LANGUAGE MODE) > 0 "
                    "ORDER BY MATCH(title, labels, ocr_summary, product_brief_summary, "
                    "approved_notes) AGAINST (:query IN NATURAL LANGUAGE MODE) DESC LIMIT 10"
                ),
                {"workspace": WORKSPACE, "query": "鎏金 summer"},
            )
            .mappings()
            .one()
        )
    assert plan["key"] == "ft_product_search_cjk"
