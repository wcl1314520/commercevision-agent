from commercevision_persistence.models import Base, UTCDateTime


def test_index_registry_schema_keeps_mysql_as_the_complete_index_fact_source() -> None:
    collections = Base.metadata.tables["collection_registry"]
    embeddings = Base.metadata.tables["embedding_records"]

    assert {
        "model_family",
        "model_id",
        "pinned_revision",
        "dimension",
        "vector_kind",
        "schema_version",
        "index_spec_version",
        "dynamic_fields_enabled",
        "physical_name",
        "state",
    }.issubset(collections.columns.keys())
    assert collections.c.dynamic_fields_enabled.default.arg is False
    assert {
        "asset_version_id",
        "rights_record_id",
        "collection_id",
        "input_hash",
        "embedding_spec_hash",
        "milvus_primary_key",
        "provider",
        "provider_request_id",
        "actual_model",
        "state",
        "indexed_at",
        "stale_at",
        "created_at",
        "updated_at",
    }.issubset(embeddings.columns.keys())
    assert isinstance(embeddings.c.indexed_at.type, UTCDateTime)
    assert isinstance(embeddings.c.stale_at.type, UTCDateTime)
    assert isinstance(embeddings.c.created_at.type, UTCDateTime)
    assert isinstance(embeddings.c.updated_at.type, UTCDateTime)

    unique_sets = {
        tuple(constraint.columns.keys())
        for constraint in embeddings.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("asset_version_id", "embedding_spec_hash") in unique_sets
    assert ("collection_id", "milvus_primary_key") in unique_sets
