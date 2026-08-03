from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from commercevision_application import (
    RetrievalApplicationService,
    RetrievalQueryImageUnavailable,
    RetrievalRecallBatch,
    RetrievalRecallHit,
)
from commercevision_contracts import (
    RetrievalCitationV1,
    RetrievalQueryV1,
    RetrievalResponseV1,
    RetrievalScoreBreakdownV1,
)
from commercevision_contracts.object_storage import PresignedRequest
from commercevision_domain import RetrievalChannel, RetrievalPolicy, new_uuid7
from commercevision_persistence import (
    MySqlBrandProfileRetrievalSource,
    MySqlRetrievalAuthority,
    MySqlRetrievalPreviewService,
    MySqlRetrievalQueryImageReference,
    MySqlRetrievalRunStore,
)
from sqlalchemy import text

pytestmark = pytest.mark.integration

WORKSPACE = "workspace-retrieval"
OTHER_WORKSPACE = "workspace-retrieval-other"
PROVIDER = "alibaba-model-studio"
PURPOSE = "CREATIVE_REFERENCE"


def _seed_product(database) -> str:
    product_id = new_uuid7()
    now = datetime.now(UTC).replace(tzinfo=None)
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO products "
                "(id, workspace_id, source_namespace, external_id, source_version, title, "
                "category_code, brand, attributes_json, expires_at, version, created_at, "
                "updated_at) VALUES (:id, :workspace, 'fixture', :external, NULL, "
                "'Retrieval fixture', 'BEAUTY', '星河', '{}', NULL, 1, :now, :now)"
            ),
            {
                "id": product_id,
                "workspace": WORKSPACE,
                "external": f"product-{product_id}",
                "now": now,
            },
        )
    return product_id


def _revoke_asset(database, asset: tuple[str, str, str]) -> None:
    revoked_rights_id = new_uuid7()
    now = datetime.now(UTC).replace(tzinfo=None)
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO rights_records "
                "(id, workspace_id, asset_id, asset_version_id, version_number, decision, "
                "owner_reference, source, license_reference, derivative_allowed, "
                "public_demo_allowed, evidence_reference, terms_sha256, valid_from, valid_until, "
                "perpetual, supersedes_record_id, created_by, created_at, permissions_sealed_at) "
                "VALUES (:rights, :workspace, :asset, :version, 2, 'REVOKE', 'owner', "
                "'revocation', 'license', 0, 0, 'evidence://revoked', :terms, :now, NULL, 1, "
                ":supersedes, 'test', :now, :now)"
            ),
            {
                "rights": revoked_rights_id,
                "workspace": WORKSPACE,
                "asset": asset[0],
                "version": asset[1],
                "terms": "d" * 64,
                "supersedes": asset[2],
                "now": now,
            },
        )
        connection.execute(
            text(
                "UPDATE assets SET current_rights_record_id = :rights, version = version + 1, "
                "updated_at = :now WHERE workspace_id = :workspace AND id = :asset"
            ),
            {
                "rights": revoked_rights_id,
                "now": now,
                "workspace": WORKSPACE,
                "asset": asset[0],
            },
        )


def _query(*, product_id: str, requires_derivative: bool = False) -> RetrievalQueryV1:
    return RetrievalQueryV1.model_validate_json(
        json.dumps(
            {
                "workspace_id": WORKSPACE,
                "requester_id": "agent:retrieval-test",
                "product_id": product_id,
                "purpose": PURPOSE,
                "provider": PROVIDER,
                "requires_derivative": requires_derivative,
                "roles": ["HERO"],
                "vector_kinds": ["PRODUCT_FUSED"],
                "query_text": "鎏金 lipstick",
                "explicit_reference_asset_version_ids": [],
                "result_limit": 5,
                "candidate_limit": 20,
                "retrieval_policy_version": "retrieval-policy-v1",
            },
            ensure_ascii=False,
        )
    )


def _seed_asset(
    database,
    *,
    product_id: str,
    workspace_id: str = WORKSPACE,
    status: str = "AVAILABLE",
    decision: str = "GRANT",
    provider: str = PROVIDER,
    purpose: str = PURPOSE,
    derivative_allowed: bool = False,
    valid_until: datetime | None = None,
) -> tuple[str, str, str]:
    asset_id = new_uuid7()
    version_id = new_uuid7()
    rights_id = new_uuid7()
    now = datetime.now(UTC).replace(tzinfo=None)
    perpetual = valid_until is None
    with database.engine.begin() as connection:
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        connection.execute(
            text(
                "INSERT INTO assets "
                "(id, workspace_id, retention_class, asset_kind, workflow_id, product_id, sku_id, "
                "status, block_reason, current_version_id, current_rights_record_id, "
                "retention_deadline, version, created_at, updated_at) VALUES "
                "(:asset, :workspace, 'FOUNDATION', 'IMAGE', NULL, :product, NULL, :status, "
                ":block_reason, :version, :rights, NULL, 1, :now, :now)"
            ),
            {
                "asset": asset_id,
                "workspace": workspace_id,
                "product": product_id,
                "status": status,
                "block_reason": "ADMINISTRATOR_BLOCKED" if status == "BLOCKED" else None,
                "version": version_id,
                "rights": rights_id,
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
                "created_at) VALUES "
                "(:version, :workspace, :asset, 1, :upload, 'fixture.png', :sha, 128, "
                "'image/png', 'image/png', 'PNG', 32, 32, 1, 'BEAUTY', 'HERO', "
                "'integrity-v1', 'validation-v1', 'transfer-v1', :transfer_sha, :now)"
            ),
            {
                "version": version_id,
                "workspace": workspace_id,
                "asset": asset_id,
                "upload": new_uuid7(),
                "sha": version_id.replace("-", "")[:32] * 2,
                "transfer_sha": "f" * 64,
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
                "VALUES (:rights, :workspace, :asset, :version, 1, :decision, 'owner', 'contract', "
                "'license', :derivative, 0, 'evidence://retrieval', :terms, :valid_from, "
                ":valid_until, :perpetual, NULL, 'test', :now, NULL)"
            ),
            {
                "rights": rights_id,
                "workspace": workspace_id,
                "asset": asset_id,
                "version": version_id,
                "decision": decision,
                "derivative": derivative_allowed,
                "terms": "e" * 64,
                "valid_from": now - timedelta(days=1),
                "valid_until": valid_until,
                "perpetual": perpetual,
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO rights_record_uses "
                "(workspace_id, asset_id, rights_record_id, allowed_use, created_at) "
                "VALUES (:workspace, :asset, :rights, :purpose, :now)"
            ),
            {
                "workspace": workspace_id,
                "asset": asset_id,
                "rights": rights_id,
                "purpose": purpose,
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO rights_record_providers "
                "(workspace_id, asset_id, rights_record_id, allowed_provider, created_at) "
                "VALUES (:workspace, :asset, :rights, :provider, :now)"
            ),
            {
                "workspace": workspace_id,
                "asset": asset_id,
                "rights": rights_id,
                "provider": provider,
                "now": now,
            },
        )
        connection.execute(
            text("UPDATE rights_records SET permissions_sealed_at = :now WHERE id = :rights"),
            {"rights": rights_id, "now": now},
        )
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
    return asset_id, version_id, rights_id


def test_mysql_generates_the_rights_filtered_set_before_recall_and_revalidates_it(
    integration_database,
) -> None:
    product_id = _seed_product(integration_database)
    eligible = _seed_asset(
        integration_database,
        product_id=product_id,
        derivative_allowed=True,
    )
    _seed_asset(
        integration_database,
        product_id=product_id,
        provider="some-other-provider",
        derivative_allowed=True,
    )
    _seed_asset(
        integration_database,
        product_id=product_id,
        decision="REVOKE",
        derivative_allowed=True,
    )
    _seed_asset(
        integration_database,
        product_id=product_id,
        valid_until=datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1),
        derivative_allowed=True,
    )
    _seed_asset(
        integration_database,
        product_id=product_id,
        status="BLOCKED",
        derivative_allowed=True,
    )
    _seed_asset(integration_database, product_id=product_id, derivative_allowed=False)
    _seed_asset(
        integration_database,
        product_id=product_id,
        workspace_id=OTHER_WORKSPACE,
        derivative_allowed=True,
    )
    authority = MySqlRetrievalAuthority(integration_database.session_factory)
    query = _query(product_id=product_id, requires_derivative=True)

    snapshot = authority.eligible_asset_versions(query)

    assert [
        (item.asset_id, item.asset_version_id, item.rights_record_id) for item in snapshot.items
    ] == [eligible]
    assert snapshot.decided_at.tzinfo is not None

    _revoke_asset(integration_database, eligible)

    final = authority.revalidate_asset_versions(
        query,
        asset_version_ids=(eligible[1],),
    )

    assert final.items == ()


def test_brand_profile_recall_uses_one_immutable_publication_and_sql_eligible_intersection(
    integration_database,
) -> None:
    product_id = new_uuid7()
    eligible = _seed_asset(integration_database, product_id=product_id)
    unauthorized = _seed_asset(
        integration_database,
        product_id=product_id,
        decision="REVOKE",
    )
    profile_id = new_uuid7()
    publication_id = new_uuid7()
    now = datetime.now(UTC).replace(tzinfo=None)
    with integration_database.engine.begin() as connection:
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        connection.execute(
            text(
                "INSERT INTO brand_profile_versions "
                "(id, workspace_id, profile_id, version_number, content_json, content_sha256, "
                "purpose, provider, requires_derivative, published_by, published_at) VALUES "
                "(:id, :workspace, :profile, 1, '{}', :sha, :purpose, :provider, 0, "
                "'test', :now)"
            ),
            {
                "id": publication_id,
                "workspace": WORKSPACE,
                "profile": profile_id,
                "sha": "c" * 64,
                "purpose": PURPOSE,
                "provider": PROVIDER,
                "now": now,
            },
        )
        for ordinal, member in enumerate((eligible, unauthorized)):
            connection.execute(
                text(
                    "INSERT INTO brand_profile_members "
                    "(workspace_id, profile_id, profile_version_id, profile_version_number, "
                    "ordinal, asset_id, asset_version_id, role, rights_record_id, "
                    "rights_record_version) VALUES (:workspace, :profile, :publication, 1, "
                    ":ordinal, :asset, :version, 'VISUAL_REFERENCE', :rights, 1)"
                ),
                {
                    "workspace": WORKSPACE,
                    "profile": profile_id,
                    "publication": publication_id,
                    "ordinal": ordinal,
                    "asset": member[0],
                    "version": member[1],
                    "rights": member[2],
                },
            )
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
    query = _query(product_id=product_id).model_copy(
        update={"brand_profile_id": profile_id, "brand_profile_version": 1}
    )

    distractors = tuple(new_uuid7() for _ in range(1_001))
    batch = MySqlBrandProfileRetrievalSource(integration_database.session_factory).recall(
        query,
        eligible_asset_version_ids=(*distractors, eligible[1]),
        limit=20,
    )

    assert batch.channel.value == "BRAND_PROFILE"
    assert [hit.asset_version_id for hit in batch.hits] == [eligible[1]]


def test_real_rights_revocation_between_recall_and_final_return_yields_zero_results(
    integration_database,
) -> None:
    product_id = _seed_product(integration_database)
    asset = _seed_asset(integration_database, product_id=product_id)
    query = _query(product_id=product_id)

    class _RevokingDenseSource:
        channel = RetrievalChannel.PRODUCT_FUSED_DENSE

        @staticmethod
        def recall(query, *, eligible_asset_version_ids, limit):
            assert eligible_asset_version_ids == (asset[1],)
            _revoke_asset(integration_database, asset)
            return RetrievalRecallBatch(
                channel=RetrievalChannel.PRODUCT_FUSED_DENSE,
                hits=(RetrievalRecallHit(asset_version_id=asset[1], raw_score=0.99),),
            )

    service = RetrievalApplicationService(
        authority=MySqlRetrievalAuthority(integration_database.session_factory),
        sources=(_RevokingDenseSource(),),
        policy=RetrievalPolicy(
            version="retrieval-policy-v1",
            rrf_k=60,
            channel_weights={channel: 1.0 for channel in RetrievalChannel},
            maximum_business_adjustment=0.25,
        ),
    )

    response = service.execute(query)

    assert response.citations == []


def test_query_image_reference_rechecks_current_rights_before_presigning(
    integration_database,
) -> None:
    product_id = _seed_product(integration_database)
    asset = _seed_asset(integration_database, product_id=product_id)
    content_sha256 = asset[1].replace("-", "")[:32] * 2
    now = datetime.now(UTC).replace(tzinfo=None)
    with integration_database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO asset_objects "
                "(id, workspace_id, asset_version_id, role, backend, location, bucket, `key`, "
                "provider_version_id, etag, byte_size, sha256, state, version, created_at, "
                "updated_at) VALUES (:id, :workspace, :asset_version, 'CONTROLLED_ORIGINAL', "
                "'MINIO', 'FOUNDATION', 'fixture', 'controlled/query.png', 'version-1', 'etag-1', "
                "128, :sha, 'CONTROLLED', 1, :now, :now)"
            ),
            {
                "id": new_uuid7(),
                "workspace": WORKSPACE,
                "asset_version": asset[1],
                "sha": content_sha256,
                "now": now,
            },
        )

    class _Storage:
        calls = []

        def temporary_read(self, request):
            self.calls.append(request)
            return PresignedRequest(
                method="GET",
                url="https://controlled.invalid/query?opaque=fixture",
                required_headers={},
                expires_at=datetime.now(UTC) + timedelta(seconds=90),
            )

    storage = _Storage()
    references = MySqlRetrievalQueryImageReference(
        session_factory=integration_database.session_factory,
        storage=storage,
        lifetime=timedelta(seconds=90),
    )
    query = _query(product_id=product_id).model_copy(
        update={"query_image_asset_version_id": asset[1]}
    )

    image = references.temporary_input(query, provider=PROVIDER)

    assert image.asset_version_id == asset[1]
    assert image.content_sha256 == content_sha256
    assert len(storage.calls) == 1

    _revoke_asset(integration_database, asset)
    with pytest.raises(RetrievalQueryImageUnavailable, match="currently authorized") as failure:
        references.temporary_input(query, provider=PROVIDER)
    assert failure.value.code == "DENSE_QUERY_IMAGE_UNAUTHORIZED"
    assert len(storage.calls) == 1


def test_retained_run_uses_hash_only_preview_grant_and_rechecks_rights_on_exchange(
    integration_database,
) -> None:
    product_id = _seed_product(integration_database)
    asset = _seed_asset(integration_database, product_id=product_id)
    content_sha256 = asset[1].replace("-", "")[:32] * 2
    now = datetime.now(UTC).replace(tzinfo=None)
    with integration_database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO asset_objects "
                "(id, workspace_id, asset_version_id, role, backend, location, bucket, `key`, "
                "provider_version_id, etag, byte_size, sha256, state, version, created_at, "
                "updated_at) VALUES (:id, :workspace, :asset_version, 'CONTROLLED_ORIGINAL', "
                "'MINIO', 'FOUNDATION', 'fixture', 'controlled/preview.png', 'version-1', "
                "'etag-1', 128, :sha, 'CONTROLLED', 1, :now, :now)"
            ),
            {
                "id": new_uuid7(),
                "workspace": WORKSPACE,
                "asset_version": asset[1],
                "sha": content_sha256,
                "now": now,
            },
        )
    query = _query(product_id=product_id)
    response = RetrievalResponseV1(
        retrieval_policy_version="retrieval-policy-v1",
        complete_hybrid=True,
        degradations=[],
        eligible_asset_version_count=1,
        fused_candidate_count=1,
        final_authorized_candidate_count=1,
        latency_ms=1,
        citations=[
            RetrievalCitationV1(
                asset_id=asset[0],
                asset_version_id=asset[1],
                rights_record_id=asset[2],
                rights_record_version=1,
                retrieval_policy_version="retrieval-policy-v1",
                channels=[RetrievalChannel.EXPLICIT],
                score=RetrievalScoreBreakdownV1(
                    channel_ranks={RetrievalChannel.EXPLICIT: 1},
                    reciprocal_rank_fusion=1 / 61,
                    business_adjustment=0,
                    final_score=1 / 61,
                ),
                rank=1,
                reason="explicit authorized reference",
                decided_at=datetime.now(UTC),
            )
        ],
    )
    store = MySqlRetrievalRunStore(
        integration_database.session_factory,
        run_retention=timedelta(hours=1),
        preview_token_lifetime=timedelta(seconds=45),
    )

    retained = store.record(query, response)

    assert retained.retrieval_run_id is not None
    token = retained.citations[0].preview_reference_token
    assert token is not None and len(token) >= 32
    loaded = store.get(workspace_id=WORKSPACE, run_id=retained.retrieval_run_id)
    assert loaded is not None
    assert loaded.eligible_asset_version_count == 1
    assert loaded.fused_candidate_count == 1
    assert loaded.final_authorized_candidate_count == 1
    assert loaded.latency_ms == 1
    assert loaded.citations[0].preview_reference_token is None

    class _Storage:
        calls = []

        def temporary_read(self, request):
            self.calls.append(request)
            return PresignedRequest(
                method="GET",
                url="https://controlled.invalid/preview?opaque=fixture",
                required_headers={},
                expires_at=datetime.now(UTC) + timedelta(seconds=45),
            )

    storage = _Storage()
    previews = MySqlRetrievalPreviewService(
        session_factory=integration_database.session_factory,
        storage=storage,
        reference_lifetime=timedelta(seconds=45),
    )
    assert (
        previews.exchange(
            workspace_id=WORKSPACE,
            requester_id="somebody-else",
            run_id=retained.retrieval_run_id,
            rank=1,
            token=token,
        )
        is None
    )
    temporary = previews.exchange(
        workspace_id=WORKSPACE,
        requester_id=query.requester_id,
        run_id=retained.retrieval_run_id,
        rank=1,
        token=token,
    )
    assert temporary is not None
    assert temporary.method == "GET"
    assert len(storage.calls) == 1

    _revoke_asset(integration_database, asset)
    assert (
        previews.exchange(
            workspace_id=WORKSPACE,
            requester_id=query.requester_id,
            run_id=retained.retrieval_run_id,
            rank=1,
            token=token,
        )
        is None
    )
    assert len(storage.calls) == 1
