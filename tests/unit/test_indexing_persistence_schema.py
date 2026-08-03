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
    assert ("asset_version_id", "embedding_spec_hash", "input_hash") in unique_sets
    assert ("collection_id", "milvus_primary_key") in unique_sets


def test_collection_rebuild_schema_persists_checkpoints_and_placement_facts() -> None:
    rebuilds = Base.metadata.tables["collection_rebuilds"]
    placements = Base.metadata.tables["collection_rebuild_placements"]
    progress = Base.metadata.tables["collection_rebuild_progress"]
    pointers = Base.metadata.tables["retrieval_policy_pointers"]

    assert {
        "source_collection_version",
        "policy_pointer_version",
        "snapshot_watermark",
        "backfill_cursor",
        "replay_watermark",
        "replay_cursor_occurred_at",
        "replay_cursor_event_id",
        "rights_cursor",
        "validation_summary_json",
        "validation_watermark",
        "retire_after",
    }.issubset(rebuilds.c.keys())
    operation_foreign_keys = {
        tuple(foreign_key.parent.name for foreign_key in constraint.elements)
        for constraint in rebuilds.foreign_key_constraints
    }
    assert ("workspace_id", "operation_id") not in operation_foreign_keys
    assert {"rebuild_id", "embedding_record_id", "milvus_primary_key"}.issubset(placements.c.keys())
    assert {"rebuild_id", "sequence", "state", "observed_at"}.issubset(progress.c.keys())
    assert pointers.c.vector_kind.primary_key
    assert pointers.c.collection_id.nullable is False
