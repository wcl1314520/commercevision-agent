"""Provider-neutral implementation helpers for object-storage adapters."""

import base64
import json
import math
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime

from commercevision_contracts.object_storage import (
    ObjectReference,
    ObjectStat,
    ServerSideEncryptionState,
)
from commercevision_domain import StorageLocationClass, StoragePreconditionError

READ_CHUNK_BYTES = 64 * 1024
_ENCRYPTED_SERVER_SIDE_ENCRYPTION_STATES = frozenset(
    {
        ServerSideEncryptionState.AES256,
        ServerSideEncryptionState.KMS,
        ServerSideEncryptionState.KMS_DSSE,
        ServerSideEncryptionState.SM4,
    }
)


def close_resources_best_effort(
    resources: Iterable[object | None],
    *,
    message: str,
) -> None:
    failures: list[Exception] = []
    closed_resource_ids: set[int] = set()
    for resource in resources:
        if resource is None or id(resource) in closed_resource_ids:
            continue
        closed_resource_ids.add(id(resource))
        close = getattr(resource, "close", None)
        if not callable(close):
            continue
        try:
            close()
        except Exception as exc:
            failures.append(exc)
    if failures:
        raise ExceptionGroup(message, failures)


def normalize_server_side_encryption(value: object) -> ServerSideEncryptionState:
    if not isinstance(value, str) or not value.strip():
        return ServerSideEncryptionState.NONE
    normalized = value.strip().lower()
    return {
        "aes256": ServerSideEncryptionState.AES256,
        "aws:kms": ServerSideEncryptionState.KMS,
        "kms": ServerSideEncryptionState.KMS,
        "aws:kms:dsse": ServerSideEncryptionState.KMS_DSSE,
        "sm4": ServerSideEncryptionState.SM4,
    }.get(normalized, ServerSideEncryptionState.UNKNOWN)


def has_verified_server_side_encryption(stat: ObjectStat) -> bool:
    return stat.server_side_encryption in _ENCRYPTED_SERVER_SIDE_ENCRYPTION_STATES


def encode_version_cursor(
    *,
    provider: str,
    key_marker: str,
    version_marker: str,
) -> str:
    payload = json.dumps(
        {
            "provider": provider,
            "key_marker": key_marker,
            "version_marker": version_marker,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_version_cursor(
    token: str | None,
    *,
    provider: str,
) -> tuple[str, str]:
    if token is None:
        return "", ""
    try:
        padded = token + "=" * (-len(token) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StoragePreconditionError("object version continuation token is invalid") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"provider", "key_marker", "version_marker"}
        or payload.get("provider") != provider
        or not isinstance(payload.get("key_marker"), str)
        or not isinstance(payload.get("version_marker"), str)
    ):
        raise StoragePreconditionError("object version continuation token is invalid")
    return payload["key_marker"], payload["version_marker"]


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


def written_object_matches(
    stat: ObjectStat,
    *,
    expected_length: int,
    expected_sha256: str,
    expected_content_type: str,
    expected_metadata: Mapping[str, str],
    require_encryption: bool = False,
) -> bool:
    return (
        stat.content_length == expected_length
        and stat.metadata.get("sha256") == expected_sha256
        and all(stat.metadata.get(name) == value for name, value in expected_metadata.items())
        and stat.content_type is not None
        and stat.content_type.partition(";")[0].strip().lower() == expected_content_type.lower()
        and (not require_encryption or has_verified_server_side_encryption(stat))
    )
