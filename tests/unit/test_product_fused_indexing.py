import pytest
from commercevision_domain import (
    EmbeddingRecord,
    VectorKind,
    build_controlled_product_text,
    compute_product_fused_input_hash,
)

CONTENT_SHA256 = "a" * 64
BRIEF_VERSION_ID = "0198f4d8-1f7c-7b2d-8da9-214a92a884a1"
BRIEF_ID = "0198f4d8-1f7c-7b2d-8da9-214a92a884a6"
OTHER_BRIEF_ID = "0198f4d8-1f7c-7b2d-8da9-214a92a884a7"
ASSET_ID = "0198f4d8-1f7c-7b2d-8da9-214a92a884a2"
ASSET_VERSION_ID = "0198f4d8-1f7c-7b2d-8da9-214a92a884a3"
RIGHTS_ID = "0198f4d8-1f7c-7b2d-8da9-214a92a884a4"
COLLECTION_ID = "0198f4d8-1f7c-7b2d-8da9-214a92a884a5"


def test_controlled_product_title_respects_the_search_document_column_limit() -> None:
    with pytest.raises(ValueError, match="title exceeds"):
        build_controlled_product_text(
            confirmed_product_brief_version_id=BRIEF_VERSION_ID,
            confirmed_fields={
                "common.identity": {
                    "kind": "IDENTITY",
                    "display_name": "x" * 513,
                }
            },
        )


def test_controlled_product_text_is_stable_across_equivalent_cjk_content() -> None:
    first = build_controlled_product_text(
        confirmed_product_brief_version_id=BRIEF_VERSION_ID,
        confirmed_fields={
            "common.identity": {
                "kind": "IDENTITY",
                "display_name": "  Ａurora\u3000口红  ",
            },
            "common.brand": {"kind": "TEXT", "text": " Aurora "},
            "common.colors": {"kind": "TEXT_LIST", "items": ["正红", " 玫瑰红 "]},
            "common.visible_text_summary": {"kind": "TEXT", "text": "持妆 12H"},
            "common.sensitive_claims": {
                "kind": "STATEMENT_LIST",
                "statements": ["raw medical claim must not enter search"],
            },
        },
        approved_labels=("唇妆", " 高光 ", "唇妆"),
        approved_notes=(" 适合晚宴 ",),
    )
    equivalent = build_controlled_product_text(
        confirmed_product_brief_version_id=BRIEF_VERSION_ID,
        confirmed_fields={
            "common.identity": {
                "kind": "IDENTITY",
                "display_name": "aurora 口红",
            },
            "common.visible_text_summary": {"kind": "TEXT", "text": " 持妆\t12h "},
            "common.colors": {"kind": "TEXT_LIST", "items": ["玫瑰红", "正红"]},
            "common.brand": {"kind": "TEXT", "text": "aurora"},
        },
        approved_labels=("高光", "唇妆"),
        approved_notes=("适合晚宴",),
    )

    assert first.canonical_text == equivalent.canonical_text
    assert first.content_sha256 == equivalent.content_sha256
    assert "raw medical claim" not in first.canonical_text
    assert first.ocr_summary == "持妆 12h"


def test_product_fused_hash_binds_brief_controlled_content_and_configuration() -> None:
    document = build_controlled_product_text(
        confirmed_product_brief_version_id=BRIEF_VERSION_ID,
        confirmed_fields={
            "common.identity": {
                "kind": "IDENTITY",
                "display_name": "磁吸手机支架",
            },
            "common.product_type": {"kind": "TEXT", "text": "车载支架"},
            "automotive.placement": {"kind": "TEXT", "text": "中控台"},
            "automotive.compatibility_evidence": {
                "kind": "STATEMENT_LIST",
                "statements": ["unapproved compatibility must stay out"],
            },
        },
        approved_labels=("汽车配件",),
        approved_notes=("已核验磁吸结构",),
    )

    baseline = compute_product_fused_input_hash(
        product_brief_id=BRIEF_ID,
        content_sha256=CONTENT_SHA256,
        controlled_text_sha256=document.content_sha256,
        provider="alibaba-model-studio",
        preprocessing_version="product-fused-text-v1",
        model_configuration_version="embedding-config-v1",
        vector_kind=VectorKind.PRODUCT_FUSED,
    )
    replay = compute_product_fused_input_hash(
        product_brief_id=BRIEF_ID,
        content_sha256=CONTENT_SHA256,
        controlled_text_sha256=document.content_sha256,
        provider="alibaba-model-studio",
        preprocessing_version="product-fused-text-v1",
        model_configuration_version="embedding-config-v1",
        vector_kind=VectorKind.PRODUCT_FUSED,
    )
    changed = compute_product_fused_input_hash(
        product_brief_id=BRIEF_ID,
        content_sha256=CONTENT_SHA256,
        controlled_text_sha256="b" * 64,
        provider="alibaba-model-studio",
        preprocessing_version="product-fused-text-v1",
        model_configuration_version="embedding-config-v1",
        vector_kind=VectorKind.PRODUCT_FUSED,
    )

    assert baseline == replay
    assert changed != baseline
    assert (
        compute_product_fused_input_hash(
            product_brief_id=OTHER_BRIEF_ID,
            content_sha256=CONTENT_SHA256,
            controlled_text_sha256=document.content_sha256,
            provider="alibaba-model-studio",
            preprocessing_version="product-fused-text-v1",
            model_configuration_version="embedding-config-v1",
            vector_kind=VectorKind.PRODUCT_FUSED,
        )
        != baseline
    )
    assert "unapproved compatibility" not in document.canonical_text


def test_product_fused_embedding_identity_changes_only_with_controlled_input() -> None:
    common = {
        "workspace_id": "workspace-a",
        "asset_id": ASSET_ID,
        "asset_version_id": ASSET_VERSION_ID,
        "asset_version_number": 1,
        "rights_record_id": RIGHTS_ID,
        "rights_record_version": 1,
        "collection_id": COLLECTION_ID,
        "embedding_spec_hash": "c" * 64,
        "vector_kind": VectorKind.PRODUCT_FUSED,
        "product_brief_version_id": BRIEF_VERSION_ID,
        "controlled_text_sha256": "d" * 64,
    }

    first = EmbeddingRecord.create(**common, input_hash="e" * 64)
    replay = EmbeddingRecord.create(**common, input_hash="e" * 64)
    changed = EmbeddingRecord.create(
        **(common | {"controlled_text_sha256": "f" * 64}),
        input_hash="1" * 64,
    )

    assert replay.id == first.id
    assert changed.id != first.id
