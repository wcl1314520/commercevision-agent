"""Idempotency identity and replay rules for Asset Registry commands."""

import hashlib
import json
from datetime import datetime

from commercevision_contracts import (
    UploadFinalizeResponseV1,
    UploadSessionResponseV1,
)
from commercevision_domain import ConcurrencyError, ObjectMismatchError
from commercevision_domain.workflow.errors import IdempotencyConflictError

from .asset_ports import AssetUnitOfWorkPort, IdempotencyRecordPort


def canonical_hash(value: dict[str, object]) -> str:
    canonical = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def key_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def workspace_hash(workspace_id: str) -> str:
    return hashlib.sha256(workspace_id.encode()).hexdigest()


def idempotency_scope(
    operation: str,
    workspace_id: str,
    resource_id: str | None = None,
) -> str:
    suffix = f":{resource_id}" if resource_id is not None else ""
    return f"assets:{operation}:{workspace_hash(workspace_id)}{suffix}"


def claim_idempotency(
    *,
    uow: AssetUnitOfWorkPort,
    scope: str,
    key_digest: str,
    request_hash: str,
    expires_at: datetime,
) -> IdempotencyRecordPort:
    record = uow.idempotency.claim(
        scope=scope,
        key_hash=key_digest,
        request_hash=request_hash,
        expires_at=expires_at,
    )
    if record.request_hash != request_hash:
        raise IdempotencyConflictError("idempotency key was already used with a different request")
    if record.status not in {"PENDING", "COMPLETED"}:
        raise ConcurrencyError("idempotency record has an unsupported status")
    return record


def complete_finalize_idempotency(
    *,
    uow: AssetUnitOfWorkPort,
    scope: str,
    key_digest: str,
    request_hash: str,
    response: UploadFinalizeResponseV1,
) -> None:
    uow.idempotency.complete(
        scope=scope,
        key_hash=key_digest,
        request_hash=request_hash,
        resource_type="upload-finalize",
        resource_id=response.upload_session.id,
        response_data=response.model_dump(mode="json"),
    )


def replay_upload_session(
    record: IdempotencyRecordPort,
) -> UploadSessionResponseV1:
    if record.resource_type != "upload-session" or record.response_data is None:
        raise ConcurrencyError("idempotency record has no upload session response")
    return UploadSessionResponseV1.model_validate(record.response_data)


def replay_finalize(record: IdempotencyRecordPort) -> UploadFinalizeResponseV1:
    if record.resource_type == "upload-finalize-error":
        response_data = record.response_data or {}
        raise ObjectMismatchError(
            str(response_data.get("message", "uploaded object did not match"))
        )
    if record.resource_type != "upload-finalize" or record.response_data is None:
        raise ConcurrencyError("idempotency record has no finalize response")
    return UploadFinalizeResponseV1.model_validate(record.response_data)
