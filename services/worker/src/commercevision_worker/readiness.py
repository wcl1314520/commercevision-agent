"""Process-wide dependency probes for the Celery Worker."""

from commercevision_contracts import Settings
from commercevision_domain import StorageLocationClass
from commercevision_object_storage import build_object_storage, close_object_storage
from commercevision_persistence import create_database
from sqlalchemy import text

from .asset_validation import build_malware_scanner


def probe_worker_dependencies(settings: Settings) -> dict[str, str]:
    """Verify remote dependencies before the Celery master starts consumers."""

    database = create_database(settings)
    try:
        with database.engine.connect() as connection:
            if connection.execute(text("SELECT 1")).scalar_one() != 1:
                raise RuntimeError("MySQL readiness query returned an unexpected result")
    finally:
        database.dispose()

    object_storage_status = "not_required"
    if settings.worker_requires_object_storage:
        object_storage = build_object_storage(settings)
        try:
            object_storage.assert_ready(
                (
                    StorageLocationClass.QUARANTINE,
                    StorageLocationClass.TASK,
                    StorageLocationClass.FOUNDATION,
                )
            )
        finally:
            close_object_storage(object_storage)
        object_storage_status = "ok"
    malware_status = "not_required"
    if settings.worker_requires_asset_validation:
        scanner = build_malware_scanner(settings)
        scanner.assert_ready()
        malware_status = "ok"
    return {
        "mysql": "ok",
        "object_storage": object_storage_status,
        "malware_scanner": malware_status,
    }
