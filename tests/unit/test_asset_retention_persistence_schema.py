from pathlib import Path

from commercevision_persistence.models import Base, UTCDateTime


def test_asset_deletion_schema_keeps_immutable_tombstones_and_append_only_progress() -> None:
    assets = Base.metadata.tables["assets"]
    tombstones = Base.metadata.tables["asset_deletion_tombstones"]
    progress = Base.metadata.tables["asset_deletion_progress"]
    artifacts = Base.metadata.tables["provider_artifact_deletion_progress"]

    assert {
        "deletion_generation",
        "deletion_operation_id",
        "deletion_reason",
        "deletion_requested_at",
        "deletion_completed_at",
    }.issubset(assets.columns.keys())
    assert {
        "workspace_id",
        "asset_id",
        "target_asset_version_id",
        "deletion_generation",
        "operation_id",
        "reason",
        "requested_by",
        "requested_at",
    }.issubset(tombstones.columns.keys())
    assert {
        "tombstone_id",
        "component",
        "state",
        "observed_count",
        "converged_count",
        "error_code",
        "created_at",
    }.issubset(progress.columns.keys())
    assert {
        "provider_artifact_id",
        "tombstone_id",
        "state",
        "provider_version_id",
        "error_code",
        "created_at",
    }.issubset(artifacts.columns.keys())
    for table, names in (
        (
            assets,
            ("deletion_requested_at", "deletion_completed_at"),
        ),
        (tombstones, ("requested_at",)),
        (progress, ("created_at",)),
        (artifacts, ("created_at",)),
    ):
        for name in names:
            assert isinstance(table.c[name].type, UTCDateTime)

    tombstone_unique_sets = {
        tuple(constraint.columns.keys())
        for constraint in tombstones.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("asset_id", "deletion_generation") in tombstone_unique_sets
    assert ("operation_id",) in tombstone_unique_sets
    asset_indexes = {index.name: tuple(index.columns.keys()) for index in assets.indexes}
    assert asset_indexes["ix_assets_retention_cleanup_due"] == (
        "retention_class",
        "deletion_operation_id",
        "retention_deadline",
        "id",
    )


def test_task_product_brief_payload_deletion_is_guarded_by_the_exact_tombstone() -> None:
    migration = Path(
        "database/migrations/versions/e1b7c4d9a263_asset_retention_deletion.py"
    ).read_text(encoding="utf-8")

    assert '_TASK_PAYLOAD_TABLES = ("product_brief_fields", "product_brief_evidence")' in migration
    assert 'f"CREATE TRIGGER trg_{table_name}_no_delete "' in migration
    assert "OLD.product_brief_id" in migration
    assert "a.deletion_operation_id = t.operation_id" in migration
    assert "t.reason = 'RETENTION_EXPIRED'" in migration
    assert "pb.retention_deadline <= UTC_TIMESTAMP(6)" in migration
