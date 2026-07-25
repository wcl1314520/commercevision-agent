"""Direct-upload and quarantined Asset Registry use cases."""

from __future__ import annotations

import base64
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from commercevision_contracts import (
    AssetResponseV1,
    PresignedUploadV1,
    UploadFinalizeResponseV1,
    UploadSessionCreateRequestV1,
    UploadSessionCreateResponseV1,
    UploadSessionMutationRequestV1,
    UploadSessionResponseV1,
)
from commercevision_contracts.assets import SUPPORTED_IMAGE_MIME_TYPES
from commercevision_contracts.object_storage import (
    ObjectReference,
    ObjectStorage,
    PresignPutRequest,
)
from commercevision_domain import (
    AssetKind,
    ConcurrencyError,
    NotFoundError,
    ObjectMismatchError,
    RetentionClass,
    StorageLocationClass,
    UnsupportedAssetKindError,
    UploadAbortedError,
    UploadExpiredError,
    UploadSession,
    UploadSessionState,
    new_uuid7,
    validate_workspace_id,
)

from .asset_cleanup_dispatch import (
    UploadCleanupPolicy,
    schedule_abandoned_upload_cleanup,
)
from .asset_contract_mapping import (
    asset_response,
    asset_version_response,
    upload_session_response,
)
from .asset_finalize import UploadFinalizeCoordinator
from .asset_idempotency import (
    canonical_hash,
    claim_idempotency,
    idempotency_scope,
    replay_upload_session,
    workspace_hash,
)
from .asset_idempotency import (
    key_hash as hash_idempotency_key,
)
from .asset_integrity import ImageUploadIntegrityVerifier
from .asset_ports import AssetUnitOfWorkFactory, AssetUnitOfWorkPort
from .asset_registry_facts import (
    add_upload_audit,
    canonicalize_resource_id,
    idempotency_expiry,
    load_asset_version,
    load_upload_session,
    task_asset_retention_deadline,
)
from .asset_registry_facts import (
    retention_deadline as resolve_retention_deadline,
)
from .asset_validation_dispatch import AssetValidationPolicy


class AssetRegistryApplicationService:
    def __init__(
        self,
        *,
        uow_factory: AssetUnitOfWorkFactory,
        storage: ObjectStorage,
        verifier: ImageUploadIntegrityVerifier,
        quarantine_bucket: str,
        task_bucket: str,
        foundation_bucket: str,
        upload_session_lifetime: timedelta,
        finalize_lease_duration: timedelta,
        upload_policy_version: str,
        integrity_policy_version: str,
        maximum_bytes: int,
        validation_policy: AssetValidationPolicy,
        cleanup_policy: UploadCleanupPolicy,
        lease_owner: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if upload_session_lifetime <= timedelta(0):
            raise ValueError("upload session lifetime must be positive")
        if finalize_lease_duration <= timedelta(0):
            raise ValueError("finalize lease duration must be positive")
        if not lease_owner:
            raise ValueError("finalize lease owner must not be blank")
        if not quarantine_bucket or not task_bucket or not foundation_bucket:
            raise ValueError("Asset storage buckets must not be blank")
        self._uow_factory = uow_factory
        self._storage = storage
        self._finalizer = UploadFinalizeCoordinator(
            uow_factory=uow_factory,
            verifier=verifier,
            finalize_lease_duration=finalize_lease_duration,
            validation_policy=validation_policy,
            cleanup_policy=cleanup_policy,
            lease_owner=lease_owner,
            clock=clock,
        )
        self._quarantine_bucket = quarantine_bucket
        self._destination_buckets = {
            StorageLocationClass.TASK: task_bucket,
            StorageLocationClass.FOUNDATION: foundation_bucket,
        }
        self._upload_session_lifetime = upload_session_lifetime
        self._upload_policy_version = upload_policy_version
        self._integrity_policy_version = integrity_policy_version
        self._maximum_bytes = maximum_bytes
        self._cleanup_policy = cleanup_policy
        self._clock = clock or (lambda: datetime.now(UTC))

    def create_upload_session(
        self,
        *,
        request: UploadSessionCreateRequestV1,
        workspace_id: str,
        actor_id: str,
        idempotency_key: str,
        trace_id: str,
    ) -> UploadSessionCreateResponseV1:
        validate_workspace_id(workspace_id)
        self._validate_upload_request(request)
        now = self._clock()
        scope = idempotency_scope("upload-create", workspace_id)
        key_hash = hash_idempotency_key(idempotency_key)
        request_hash = canonical_hash(request.model_dump(mode="json"))

        with self._uow_factory() as uow:
            retention_deadline = self._validate_associations(
                uow=uow,
                request=request,
                workspace_id=workspace_id,
                now=now,
            )
            record = claim_idempotency(
                uow=uow,
                scope=scope,
                key_digest=key_hash,
                request_hash=request_hash,
                expires_at=idempotency_expiry(
                    now=now,
                    retention_deadline=retention_deadline,
                ),
            )
            if record.status == "COMPLETED":
                upload_session = load_upload_session(
                    uow,
                    workspace_id=workspace_id,
                    upload_session_id=record.resource_id,
                    for_update=True,
                )
                if upload_session.expire_if_due(now=now):
                    schedule_abandoned_upload_cleanup(
                        uow=uow,
                        upload_session=upload_session,
                        trace_id=trace_id,
                        policy=self._cleanup_policy,
                        now=now,
                    )
                    uow.commit()
            else:
                reserved_asset_id = new_uuid7()
                reserved_asset_version_id = new_uuid7()
                destination_location = (
                    StorageLocationClass.TASK
                    if request.retention_class == RetentionClass.TASK
                    else StorageLocationClass.FOUNDATION
                )
                expires_at = now + self._upload_session_lifetime
                if retention_deadline is not None:
                    expires_at = min(expires_at, retention_deadline)
                upload_session = UploadSession.create(
                    workspace_id=workspace_id,
                    actor_id=actor_id,
                    reserved_asset_id=reserved_asset_id,
                    reserved_asset_version_id=reserved_asset_version_id,
                    retention_class=request.retention_class,
                    asset_kind=request.asset_kind,
                    filename=request.filename,
                    declared_mime=request.declared_mime.lower(),
                    expected_byte_length=request.byte_length,
                    expected_sha256=request.sha256,
                    workflow_id=request.workflow_id,
                    product_id=request.product_id,
                    sku_id=request.sku_id,
                    category=request.category,
                    role=request.role,
                    upload_policy_version=self._upload_policy_version,
                    integrity_policy_version=self._integrity_policy_version,
                    storage_backend=self._storage.backend,
                    storage_bucket=self._quarantine_bucket,
                    storage_key=self._new_quarantine_key(
                        workspace_id=workspace_id,
                        retention_class=request.retention_class,
                        asset_id=reserved_asset_id,
                        asset_version_id=reserved_asset_version_id,
                    ),
                    destination_location=destination_location,
                    destination_bucket=self._destination_buckets[destination_location],
                    destination_key=self._new_destination_key(
                        workspace_id=workspace_id,
                        retention_class=request.retention_class,
                        asset_id=reserved_asset_id,
                        asset_version_id=reserved_asset_version_id,
                    ),
                    expires_at=expires_at,
                    now=now,
                )
                uow.upload_sessions.add(upload_session)
                response = upload_session_response(upload_session)
                uow.idempotency.complete(
                    scope=scope,
                    key_hash=key_hash,
                    request_hash=request_hash,
                    resource_type="upload-session",
                    resource_id=upload_session.id,
                    response_data=response.model_dump(mode="json"),
                )
                add_upload_audit(
                    uow=uow,
                    upload_session=upload_session,
                    actor_id=actor_id,
                    action="asset.upload_session.created",
                    trace_id=trace_id,
                    now=now,
                )
                uow.commit()

        return self._create_response(upload_session)

    def get_upload_session(
        self,
        *,
        workspace_id: str,
        upload_session_id: str,
        trace_id: str,
    ) -> UploadSessionResponseV1:
        validate_workspace_id(workspace_id)
        upload_session_id = canonicalize_resource_id(
            upload_session_id,
            resource="upload session",
        )
        with self._uow_factory() as uow:
            upload_session = load_upload_session(
                uow,
                workspace_id=workspace_id,
                upload_session_id=upload_session_id,
                for_update=True,
            )
            now = self._clock()
            expired_now = upload_session.expire_if_due(now=now)
            cleanup_scheduled = schedule_abandoned_upload_cleanup(
                uow=uow,
                upload_session=upload_session,
                trace_id=trace_id,
                policy=self._cleanup_policy,
                now=now,
            )
            if expired_now or cleanup_scheduled:
                uow.commit()
        return upload_session_response(upload_session)

    def abort_upload_session(
        self,
        *,
        upload_session_id: str,
        request: UploadSessionMutationRequestV1,
        workspace_id: str,
        actor_id: str,
        idempotency_key: str,
        trace_id: str,
    ) -> UploadSessionResponseV1:
        validate_workspace_id(workspace_id)
        upload_session_id = canonicalize_resource_id(
            upload_session_id,
            resource="upload session",
        )
        scope = idempotency_scope("upload-abort", workspace_id, upload_session_id)
        key_hash = hash_idempotency_key(idempotency_key)
        request_hash = canonical_hash(request.model_dump(mode="json"))
        with self._uow_factory() as uow:
            upload_session = load_upload_session(
                uow,
                workspace_id=workspace_id,
                upload_session_id=upload_session_id,
                for_update=True,
            )
            now = self._clock()
            retention_deadline = resolve_retention_deadline(uow, upload_session)
            record = claim_idempotency(
                uow=uow,
                scope=scope,
                key_digest=key_hash,
                request_hash=request_hash,
                expires_at=idempotency_expiry(
                    now=now,
                    retention_deadline=retention_deadline,
                ),
            )
            if record.status == "COMPLETED":
                return replay_upload_session(record)
            original_version = upload_session.version
            if not upload_session.expire_if_due(now=now):
                upload_session.abort(expected_version=request.expected_version, now=now)
            cleanup_scheduled = schedule_abandoned_upload_cleanup(
                uow=uow,
                upload_session=upload_session,
                trace_id=trace_id,
                policy=self._cleanup_policy,
                now=now,
            )
            if upload_session.version != original_version and not cleanup_scheduled:
                uow.upload_sessions.save(upload_session)
            response = upload_session_response(upload_session)
            uow.idempotency.complete(
                scope=scope,
                key_hash=key_hash,
                request_hash=request_hash,
                resource_type="upload-session",
                resource_id=upload_session.id,
                response_data=response.model_dump(mode="json"),
            )
            add_upload_audit(
                uow=uow,
                upload_session=upload_session,
                actor_id=actor_id,
                action="asset.upload_session.aborted",
                trace_id=trace_id,
                now=now,
            )
            uow.commit()
        return response

    def finalize_upload_session(
        self,
        *,
        upload_session_id: str,
        request: UploadSessionMutationRequestV1,
        workspace_id: str,
        actor_id: str,
        idempotency_key: str,
        trace_id: str,
    ) -> UploadFinalizeResponseV1:
        return self._finalizer.finalize(
            upload_session_id=upload_session_id,
            request=request,
            workspace_id=workspace_id,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            trace_id=trace_id,
        )

    def get_asset(self, *, workspace_id: str, asset_id: str) -> AssetResponseV1:
        validate_workspace_id(workspace_id)
        asset_id = canonicalize_resource_id(asset_id, resource="Asset")
        with self._uow_factory() as uow:
            asset = uow.assets.get(workspace_id=workspace_id, asset_id=asset_id)
            if asset is None:
                raise NotFoundError(f"Asset {asset_id} was not found")
            asset_version, object_fact = load_asset_version(
                uow,
                workspace_id=workspace_id,
                asset_version_id=asset.current_version_id,
            )
        version_response = asset_version_response(asset_version, object_fact)
        return asset_response(asset, current_version=version_response)

    def _create_response(
        self,
        upload_session: UploadSession,
    ) -> UploadSessionCreateResponseV1:
        if upload_session.state != UploadSessionState.OPEN:
            if upload_session.state == UploadSessionState.EXPIRED:
                raise UploadExpiredError(f"upload session {upload_session.id} has expired")
            if upload_session.state == UploadSessionState.ABORTED:
                raise UploadAbortedError(f"upload session {upload_session.id} was aborted")
            raise ConcurrencyError(
                f"upload session {upload_session.id} cannot issue an upload from "
                f"{upload_session.state.value}"
            )
        checksum = base64.b64encode(bytes.fromhex(upload_session.expected_sha256)).decode()
        presigned = self._storage.presign_put(
            PresignPutRequest(
                reference=ObjectReference(
                    location=upload_session.storage_location,
                    key=upload_session.storage_key,
                ),
                content_type=upload_session.declared_mime,
                content_length=upload_session.expected_byte_length,
                checksum_sha256_base64=checksum,
                upload_session_id=upload_session.id,
                expires_at=upload_session.expires_at,
            )
        )
        return UploadSessionCreateResponseV1(
            **upload_session_response(upload_session).model_dump(),
            upload=PresignedUploadV1(
                method="PUT",
                url=presigned.url,
                required_headers=presigned.required_headers,
                maximum_bytes=upload_session.expected_byte_length,
                checksum_algorithm="SHA-256",
                expires_at=presigned.expires_at,
            ),
        )

    def _validate_associations(
        self,
        *,
        uow: AssetUnitOfWorkPort,
        request: UploadSessionCreateRequestV1,
        workspace_id: str,
        now: datetime,
    ) -> datetime | None:
        retention_deadline: datetime | None = None
        if request.retention_class == RetentionClass.TASK:
            assert request.workflow_id is not None
            retention_deadline = task_asset_retention_deadline(
                uow,
                workspace_id=workspace_id,
                workflow_id=request.workflow_id,
            )
            if retention_deadline is None:
                raise NotFoundError(f"Workflow {request.workflow_id} was not found")
            if retention_deadline <= now:
                raise UploadExpiredError(f"Workflow {request.workflow_id} has expired")
        if request.product_id is not None and not uow.associations.product_exists(
            workspace_id=workspace_id,
            product_id=request.product_id,
        ):
            raise NotFoundError(f"Product {request.product_id} was not found")
        if request.sku_id is not None:
            assert request.product_id is not None
            if not uow.associations.sku_exists(
                workspace_id=workspace_id,
                product_id=request.product_id,
                sku_id=request.sku_id,
            ):
                raise NotFoundError(f"SKU {request.sku_id} was not found")
        return retention_deadline

    def _validate_upload_request(self, request: UploadSessionCreateRequestV1) -> None:
        if request.asset_kind != AssetKind.IMAGE:
            raise UnsupportedAssetKindError(
                f"Asset kind {request.asset_kind.value} is not supported by direct image upload"
            )
        if request.declared_mime.lower() not in SUPPORTED_IMAGE_MIME_TYPES:
            raise UnsupportedAssetKindError(f"MIME type {request.declared_mime} is not supported")
        if request.byte_length > self._maximum_bytes:
            raise ObjectMismatchError("declared byte length exceeds the configured limit")

    @staticmethod
    def _new_quarantine_key(
        *,
        workspace_id: str,
        retention_class: RetentionClass,
        asset_id: str,
        asset_version_id: str,
    ) -> str:
        return AssetRegistryApplicationService._new_object_key(
            workspace_id=workspace_id,
            retention_class=retention_class,
            asset_id=asset_id,
            asset_version_id=asset_version_id,
            stage="quarantine",
        )

    @staticmethod
    def _new_destination_key(
        *,
        workspace_id: str,
        retention_class: RetentionClass,
        asset_id: str,
        asset_version_id: str,
    ) -> str:
        return AssetRegistryApplicationService._new_object_key(
            workspace_id=workspace_id,
            retention_class=retention_class,
            asset_id=asset_id,
            asset_version_id=asset_version_id,
            stage="original",
        )

    @staticmethod
    def _new_object_key(
        *,
        workspace_id: str,
        retention_class: RetentionClass,
        asset_id: str,
        asset_version_id: str,
        stage: str,
    ) -> str:
        return (
            f"workspace/{workspace_hash(workspace_id)}"
            f"/retention/{retention_class.value.lower()}"
            f"/asset/{asset_id}/version/{asset_version_id}"
            f"/{stage}/{new_uuid7()}"
        )
