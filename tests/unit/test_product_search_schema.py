from commercevision_persistence.indexing_models import ProductSearchDocumentModel
from sqlalchemy import Text


def test_product_search_document_schema_has_cjk_fulltext_and_authority_identity() -> None:
    table = ProductSearchDocumentModel.__table__

    assert {
        "workspace_id",
        "product_id",
        "product_brief_id",
        "product_brief_version_id",
        "asset_id",
        "asset_version_id",
        "rights_record_id",
        "rights_record_version",
        "embedding_record_id",
        "input_hash",
        "controlled_text_sha256",
        "title",
        "labels",
        "ocr_summary",
        "product_brief_summary",
        "approved_notes",
        "retention_class",
        "retention_deadline",
        "state",
    } <= set(table.columns.keys())
    assert all(
        isinstance(table.columns[name].type, Text)
        for name in (
            "labels",
            "ocr_summary",
            "product_brief_summary",
            "approved_notes",
        )
    )
    fulltext = next(index for index in table.indexes if index.name == "ft_product_search_cjk")
    assert [column.name for column in fulltext.columns] == [
        "title",
        "labels",
        "ocr_summary",
        "product_brief_summary",
        "approved_notes",
    ]
    assert fulltext.dialect_options["mysql"]["prefix"] == "FULLTEXT"
