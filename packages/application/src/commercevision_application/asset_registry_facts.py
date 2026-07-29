"""Shared transactional facts and retention policy for the Asset Registry."""

from datetime import datetime, timedelta

from commercevision_domain import (
    AssetObject,
    AssetVersion,
    NotFoundError,
    RetentionClass,
    UploadSession,
    canonical_task_retention_deadline,
    canonicalize_uuid,
)

from .asset_ports import AssetUnitOfWorkPort

_IDEMPOTENCY_RETENTION = timedelta(days=30)
_AUDIT_RETENTION = timedelta(days=180)


def canonicalize_resource_id(value: str, *, resource: str) -> str:
    try:
        return canonicalize_uuid(value)
    except ValueError:
        raise NotFoundError(f"{resource} was not found") from None


def load_upload_session(
    uow: AssetUnitOfWorkPort,
    *,
    workspace_id: str,
    upload_session_id: str,
    for_update: bool = False,
) -> UploadSession:
    upload_session_id = canonicalize_resource_id(
        upload_session_id,
        resource="upload session",
    )
    upload_session = uow.upload_sessions.get(
        workspace_id=workspace_id,
        upload_session_id=upload_session_id,
        for_update=for_update,
    )
    if upload_session is None:
        raise NotFoundError(f"upload session {upload_session_id} was not found")
    return upload_session


def load_asset_version(
    uow: AssetUnitOfWorkPort,
    *,
    workspace_id: str,
    asset_version_id: str,
) -> tuple[AssetVersion, AssetObject]:
    asset_version = uow.assets.get_version(
        workspace_id=workspace_id,
        asset_version_id=asset_version_id,
    )
    object_fact = uow.assets.get_object(
        workspace_id=workspace_id,
        asset_version_id=asset_version_id,
    )
    if asset_version is None or object_fact is None:
        raise RuntimeError(f"Asset Version {asset_version_id} facts are incomplete")
    return asset_version, object_fact


def retention_deadline(
    uow: AssetUnitOfWorkPort,
    upload_session: UploadSession,
) -> datetime | None:
    if upload_session.retention_class == RetentionClass.FOUNDATION:
        return None
    assert upload_session.workflow_id is not None
    deadline = task_asset_retention_deadline(
        uow,
        workspace_id=upload_session.workspace_id,
        workflow_id=upload_session.workflow_id,
    )
    if deadline is None:
        raise RuntimeError("Task Asset Workflow disappeared despite RESTRICT history")
    return deadline


def task_asset_retention_deadline(
    uow: AssetUnitOfWorkPort,
    *,
    workspace_id: str,
    workflow_id: str,
) -> datetime | None:
    facts = uow.associations.workflow_retention_facts(
        workspace_id=workspace_id,
        workflow_id=workflow_id,
    )
    if facts is None:
        return None
    return canonical_task_retention_deadline(
        created_at=facts.created_at,
        expires_at=facts.expires_at,
    )


def idempotency_expiry(
    *,
    now: datetime,
    retention_deadline: datetime | None,
) -> datetime:
    expiry = now + _IDEMPOTENCY_RETENTION
    if retention_deadline is not None:
        expiry = min(expiry, retention_deadline)
    return expiry


def add_upload_audit(
    *,
    uow: AssetUnitOfWorkPort,
    upload_session: UploadSession,
    actor_id: str,
    action: str,
    trace_id: str,
    now: datetime,
) -> None:
    expires_at = now + _AUDIT_RETENTION
    uow.audit.add(
        workspace_id=upload_session.workspace_id,
        actor_type="USER",
        actor_id=actor_id,
        action=action,
        resource_type="upload-session",
        resource_id=upload_session.id,
        trace_id=trace_id,
        metadata={
            "retention_class": upload_session.retention_class.value,
            "asset_kind": upload_session.asset_kind.value,
            "state": upload_session.state.value,
            "reserved_asset_id": upload_session.reserved_asset_id,
        },
        created_at=now,
        expires_at=expires_at,
    )
