import math
from datetime import UTC, datetime, timedelta

import pytest
from commercevision_contracts import (
    EmbeddingImageInputV1,
    EmbeddingProviderErrorV1,
    EmbeddingProviderFailure,
    EmbeddingProviderRequestV1,
    EmbeddingProviderResultV1,
    EmbeddingVectorV1,
    MilvusAnnSearchRequestV1,
    MilvusCollectionCreateRequestV1,
    collection_create_request,
)
from commercevision_contracts.events import (
    AssetIndexCompletedPayload,
    AssetIndexDeleteRequestedPayload,
    AssetIndexRequestedPayload,
    EventHandling,
    EventQueue,
    EventType,
    event_contract_for,
)
from commercevision_domain import CollectionSpec, VectorKind, new_uuid7
from pydantic import SecretStr, ValidationError


def _spec() -> CollectionSpec:
    return CollectionSpec.create(
        model_family="qwen3-vl-embedding",
        pinned_revision="2026-06-30",
        dimension=4,
        vector_kind=VectorKind.IMAGE,
        schema_version=1,
        index_spec_version="hnsw-cosine-v1",
    )


def test_collection_admin_contract_disables_dynamic_fields_and_limits_scalars() -> None:
    request = collection_create_request(_spec())

    assert request.dynamic_fields_enabled is False
    assert [field.name for field in request.fields] == [
        "milvus_primary_key",
        "embedding_record_id",
        "asset_version_id",
        "workspace_id",
        "rights_record_version",
        "category",
        "brand",
        "asset_role",
        "vector_kind",
        "model_configuration_version",
        "input_hash",
        "embedding_spec_sha256",
        "write_generation",
        "indexed_at_epoch_micros",
        "vector",
    ]
    assert request.fields[-1].dimension == 4
    assert request.fields[-1].data_type == "FLOAT_VECTOR"
    with pytest.raises(ValidationError):
        MilvusCollectionCreateRequestV1(**(request.model_dump() | {"dynamic_fields_enabled": True}))


def test_collection_create_request_accepts_only_a_derived_candidate_instance_name() -> None:
    spec = CollectionSpec.create(
        model_family="qwen3-vl-embedding",
        pinned_revision="2026-06-30",
        dimension=256,
        vector_kind=VectorKind.IMAGE,
        schema_version=1,
        index_spec_version="hnsw-cosine-v1",
    )
    candidate_name = f"{spec.physical_name}_019f8a000000"

    assert collection_create_request(spec, collection_name=candidate_name).collection_name == (
        candidate_name
    )
    with pytest.raises(ValueError, match="derived from the immutable spec"):
        collection_create_request(spec, collection_name="caller_controlled_collection")


@pytest.mark.parametrize(
    ("eligible_ids", "limit"),
    [
        (["018f5f4d-7c11-7d11-8a11-111111111111"] * 2, 1),
        (["018f5f4d-7c11-7d11-8a11-111111111111"], 2),
        (["not-a-canonical-embedding-id"], 1),
        (["018F5F4D-7C11-7D11-8A11-111111111111"], 1),
    ],
)
def test_ann_request_rejects_unbounded_or_ambiguous_mysql_identity_fences(
    eligible_ids: list[str],
    limit: int,
) -> None:
    with pytest.raises(ValidationError):
        MilvusAnnSearchRequestV1(
            collection_name="cv_product_fused_test",
            workspace_id="catalog-workspace",
            vector_kind=VectorKind.PRODUCT_FUSED,
            eligible_embedding_record_ids=eligible_ids,
            query_vector=[0.1, 0.2, 0.3, 0.4],
            limit=limit,
        )


def test_ann_request_rejects_boolean_limit_under_strict_contracts() -> None:
    with pytest.raises(ValidationError):
        MilvusAnnSearchRequestV1(
            collection_name="cv_product_fused_test",
            workspace_id="catalog-workspace",
            vector_kind=VectorKind.PRODUCT_FUSED,
            eligible_embedding_record_ids=["018f5f4d-7c11-7d11-8a11-111111111111"],
            query_vector=[0.1, 0.2, 0.3, 0.4],
            limit=True,
        )


def test_ann_request_accepts_deterministic_embedding_uuid5_identities() -> None:
    request = MilvusAnnSearchRequestV1(
        collection_name="cv_product_fused_test",
        workspace_id="catalog-workspace",
        vector_kind=VectorKind.PRODUCT_FUSED,
        eligible_embedding_record_ids=["018f5f4d-7c11-5d11-8a11-111111111111"],
        query_vector=[0.1, 0.2, 0.3, 0.4],
        limit=1,
    )

    assert request.eligible_embedding_record_ids == ["018f5f4d-7c11-5d11-8a11-111111111111"]


@pytest.mark.parametrize(
    "vectors",
    [
        [EmbeddingVectorV1(values=[0.1, 0.2, 0.3])],
        [EmbeddingVectorV1.model_construct(values=[0.1, 0.2, 0.3, math.inf])],
        [
            EmbeddingVectorV1(values=[0.1, 0.2, 0.3, 0.4]),
            EmbeddingVectorV1(values=[0.4, 0.3, 0.2, 0.1]),
        ],
    ],
)
def test_embedding_result_rejects_wrong_count_dimension_or_non_finite_values(
    vectors: list[EmbeddingVectorV1],
) -> None:
    request = EmbeddingProviderRequestV1(
        provider="fixture",
        model_id="qwen3-vl-embedding",
        pinned_revision="2026-06-30",
        model_configuration_version="embedding-config-v1",
        preprocessing_version="image-preprocess-v1",
        vector_kind=VectorKind.IMAGE,
        expected_dimension=4,
        input_hash="a" * 64,
        images=[
            EmbeddingImageInputV1(
                asset_version_id=new_uuid7(),
                content_sha256="f" * 64,
                byte_size=1024,
                url=SecretStr("https://controlled.invalid/read-token"),
                required_headers={"authorization": SecretStr("Bearer secret")},
                expires_at=datetime(2026, 7, 31, tzinfo=UTC),
            )
        ],
    )
    result = EmbeddingProviderResultV1(
        vectors=vectors,
        provider="fixture",
        provider_request_id="request-1",
        actual_model="qwen3-vl-embedding-2026-06-30",
        latency_ms=12,
        usage={"image_count": 1},
    )

    with pytest.raises(ValueError):
        result.validate_for(request)


def test_embedding_result_requires_at_least_one_vector() -> None:
    with pytest.raises(ValidationError, match="at least 1 item"):
        EmbeddingProviderResultV1(
            vectors=[],
            provider="fixture",
            provider_request_id="request-1",
            actual_model="qwen3-vl-embedding-2026-06-30",
            latency_ms=12,
            usage={},
        )


def test_embedding_input_keeps_temporary_credentials_secret_and_requires_utc_expiry() -> None:
    image = EmbeddingImageInputV1(
        asset_version_id=new_uuid7(),
        content_sha256="f" * 64,
        byte_size=1024,
        url=SecretStr("https://controlled.invalid/read-token"),
        required_headers={"authorization": SecretStr("Bearer secret")},
        expires_at=datetime(2026, 7, 31, tzinfo=UTC),
    )

    assert "read-token" not in repr(image)
    assert "Bearer secret" not in repr(image)
    assert image.url.get_secret_value().endswith("/read-token")
    with pytest.raises(ValidationError):
        EmbeddingImageInputV1(
            asset_version_id=new_uuid7(),
            content_sha256="f" * 64,
            byte_size=1024,
            url=SecretStr("https://controlled.invalid/read-token"),
            expires_at=datetime(2026, 7, 31),
        )


def test_embedding_request_supports_product_fused_text_or_image_inputs() -> None:
    image = EmbeddingImageInputV1(
        asset_version_id=new_uuid7(),
        content_sha256="a" * 64,
        byte_size=128,
        url=SecretStr("https://controlled.invalid/exact-image"),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    common = {
        "provider": "alibaba-model-studio",
        "model_id": "qwen3-vl-embedding",
        "pinned_revision": "embedding-eval-2026-07-31",
        "model_configuration_version": "embedding-config-v1",
        "preprocessing_version": "product-fused-text-v1",
        "expected_dimension": 256,
        "input_hash": "b" * 64,
        "images": [image],
    }

    fused = EmbeddingProviderRequestV1(
        **common,
        vector_kind=VectorKind.PRODUCT_FUSED,
        controlled_text='{"title":"口红"}',
    )

    assert fused.controlled_text == '{"title":"口红"}'
    image_only = EmbeddingProviderRequestV1(
        **common,
        vector_kind=VectorKind.PRODUCT_FUSED,
    )
    text_only = EmbeddingProviderRequestV1(
        **(common | {"images": []}),
        vector_kind=VectorKind.PRODUCT_FUSED,
        controlled_text="口红",
    )
    assert image_only.images == [image]
    assert text_only.controlled_text == "口红"
    with pytest.raises(ValidationError, match="text or an image"):
        EmbeddingProviderRequestV1(
            **(common | {"images": []}),
            vector_kind=VectorKind.PRODUCT_FUSED,
        )
    with pytest.raises(ValidationError, match="controlled text"):
        EmbeddingProviderRequestV1(
            **common,
            vector_kind=VectorKind.IMAGE,
            controlled_text="must not be accepted",
        )


def test_embedding_provider_failure_is_strict_safe_and_provider_neutral() -> None:
    error = EmbeddingProviderErrorV1(
        code="EMBEDDING_THROTTLED",
        category="THROTTLED",
        safe_message="Embedding capacity is temporarily unavailable",
        retryable=True,
        retry_after_seconds=17,
        provider_request_id="provider-request-7",
        outcome_unknown=False,
    )

    failure = EmbeddingProviderFailure(error)

    assert failure.error == error
    assert "provider-request-7" not in str(failure)
    with pytest.raises(ValidationError):
        EmbeddingProviderErrorV1.model_validate(
            error.model_dump() | {"untyped_provider_payload": {"secret": "no"}}
        )


def test_asset_index_request_is_a_strict_typed_index_command() -> None:
    contract = event_contract_for(EventType.ASSET_INDEX_REQUESTED, 1)
    payload_data = {
        "operation_id": new_uuid7(),
        "operation_epoch": 3,
        "operation_input_hash": "a" * 64,
        "embedding_record_id": new_uuid7(),
        "workspace_id": "workspace-index",
        "asset_id": new_uuid7(),
        "asset_version_id": new_uuid7(),
        "asset_version_number": 1,
        "rights_record_id": new_uuid7(),
        "rights_record_version": 2,
        "collection_id": new_uuid7(),
        "vector_kind": "IMAGE",
        "provider": "alibaba-model-studio",
        "embedding_input_hash": "b" * 64,
        "embedding_spec_sha256": "c" * 64,
    }

    payload = contract.validate_payload(payload_data)

    assert isinstance(payload, AssetIndexRequestedPayload)
    assert payload.embedding_spec_sha256 == "c" * 64
    assert contract.queue is EventQueue.INDEX
    assert contract.handling is EventHandling.COMMAND
    with pytest.raises(ValidationError):
        contract.validate_payload(payload_data | {"untyped_escape": "forbidden"})

    fused = contract.validate_payload(
        payload_data
        | {
            "vector_kind": "PRODUCT_FUSED",
            "product_brief_version_id": new_uuid7(),
            "controlled_text_sha256": "d" * 64,
        }
    )
    assert fused.vector_kind == "PRODUCT_FUSED"
    with pytest.raises(ValidationError, match="ProductBrief"):
        contract.validate_payload(payload_data | {"vector_kind": "PRODUCT_FUSED"})


def test_index_terminal_events_carry_generation_fences_without_external_payloads() -> None:
    common = {
        "operation_id": new_uuid7(),
        "embedding_record_id": new_uuid7(),
        "workspace_id": "workspace-index",
        "asset_id": new_uuid7(),
        "asset_version_id": new_uuid7(),
        "collection_id": new_uuid7(),
        "input_hash": "b" * 64,
        "embedding_spec_sha256": "c" * 64,
        "write_generation": 3,
    }
    completed_contract = event_contract_for(EventType.ASSET_INDEX_COMPLETED, 1)
    delete_contract = event_contract_for(EventType.ASSET_INDEX_DELETE_REQUESTED, 1)

    completed = completed_contract.validate_payload(common | {"outcome": "INDEXED"})
    deletion = delete_contract.validate_payload(common | {"reason": "RIGHTS_INVALID"})

    assert isinstance(completed, AssetIndexCompletedPayload)
    assert isinstance(deletion, AssetIndexDeleteRequestedPayload)
    assert completed.write_generation == deletion.write_generation == 3
    assert "vector" not in completed.model_dump()
    assert "url" not in deletion.model_dump()
    assert delete_contract.handling is EventHandling.COMMAND
