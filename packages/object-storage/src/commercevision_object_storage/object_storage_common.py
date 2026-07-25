"""Provider-neutral implementation helpers for object-storage adapters."""

import math
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime

from commercevision_contracts.object_storage import ObjectReference, ObjectStat
from commercevision_domain import StorageLocationClass, StoragePreconditionError

READ_CHUNK_BYTES = 64 * 1024


def validated_response_version(
    reference: ObjectReference,
    observed_version_id: object,
) -> str | None:
    version_id = (
        observed_version_id
        if isinstance(observed_version_id, str)
        and observed_version_id
        and observed_version_id.lower() != "null"
        else None
    )
    if reference.version_id is not None and version_id != reference.version_id:
        raise StoragePreconditionError(
            "object storage returned a different provider version than requested"
        )
    return version_id


def seconds_until(expires_at: datetime) -> int:
    if expires_at.tzinfo is None or expires_at.utcoffset() is None:
        raise ValueError("object-storage expiry must be timezone-aware")
    seconds = math.ceil((expires_at - datetime.now(UTC)).total_seconds())
    if seconds < 1:
        raise ValueError("object-storage expiry must be in the future")
    return seconds


def select_storage_locations[BucketValue](
    configured: Mapping[StorageLocationClass, BucketValue],
    required: Iterable[StorageLocationClass] | None,
) -> tuple[StorageLocationClass, ...]:
    """Resolve a process dependency set against configured storage locations."""

    selected = tuple(configured) if required is None else tuple(dict.fromkeys(required))
    if not selected:
        raise ValueError("at least one object-storage location must be selected")
    missing = set(selected).difference(configured)
    if missing:
        names = ", ".join(sorted(location.value for location in missing))
        raise ValueError(f"object-storage locations are not configured: {names}")
    return selected


def metadata_matches(
    stat: ObjectStat,
    *,
    expected_length: int,
    expected_sha256: str,
    expected_upload_session_id: str,
    expected_content_type: str,
) -> bool:
    return (
        stat.content_length == expected_length
        and stat.metadata.get("sha256") == expected_sha256
        and stat.metadata.get("upload-session-id") == expected_upload_session_id
        and stat.content_type is not None
        and stat.content_type.partition(";")[0].strip().lower() == expected_content_type.lower()
    )
