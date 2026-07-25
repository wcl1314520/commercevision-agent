"""Lease-based three-phase finalize protocol for direct uploads."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from commercevision_contracts import (
    UploadFinalizeResponseV1,
    UploadSessionMutationRequestV1,
)
from commercevision_domain import (
    Asset,
    AssetObject,
    AssetVersion,
    ConcurrencyError,
    LeaseConflictError,
    ObjectMismatchError,
    StoragePreconditionError,
    StorageUnavailableError,
    UniqueConstraintError,
    UploadExpiredError,
    UploadObjectMissingError,
    UploadSession,
    UploadSessionState,
    validate_workspace_id,
)

from .asset_cleanup_dispatch import (
    UploadCleanupPolicy,
    schedule_abandoned_upload_cleanup,
)
from .asset_contract_mapping import finalize_response
from .asset_idempotency import (
    canonical_hash,
    claim_idempotency,
    complete_finalize_idempotency,
    idempotency_scope,
    key_hash,
    replay_finalize,
)
from .asset_integrity import ImageUploadIntegrityVerifier, VerifiedUpload
from .asset_ports import AssetUnitOfWorkFactory, AssetUnitOfWorkPort
from .asset_registry_facts import (
    add_upload_audit,
    canonicalize_resource_id,
    idempotency_expiry,
    load_asset_version,
    load_upload_session,
    retention_deadline,
)
from .asset_validation_dispatch import (
    AssetValidationPolicy,
    build_upload_finalized_event,
    build_validation_event,
    build_validation_operation,
)


class UploadFinalizeCoordinator:
    """Hide finalize leases, storage recovery, and the atomic result commit."""

    def __init__(
        self,
        *,
        uow_factory: AssetUnitOfWorkFactory,
        verifier: ImageUploadIntegrityVerifier,
        finalize_lease_duration: timedelta,
        validation_policy: AssetValidationPolicy,
        cleanup_policy: UploadCleanupPolicy,
        lease_owner: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._verifier = verifier
        self._finalize_lease_duration = finalize_lease_duration
        self._validation_policy = validation_policy
        self._cleanup_policy = cleanup_policy
        self._lease_owner = lease_owner
        self._clock = clock or (lambda: datetime.now(UTC))

    def finalize(
        self,
        *,
        upload_session_id: str,
        request: UploadSessionMutationRequestV1,
        workspace_id: str,
        actor_id: str,
        idempotency_key: str,
        trace_id: str,
    ) -> UploadFinalizeResponseV1:
        validate_workspace_id(workspace_id)
        upload_session_id = canonicalize_resource_id(
            upload_session_id,
            resource="upload session",
        )
        scope = idempotency_scope("upload-finalize", workspace_id, upload_session_id)
        key_digest = key_hash(idempotency_key)
        request_hash = canonical_hash(request.model_dump(mode="json"))

        replay, lease_token = self._claim(
            upload_session_id=upload_session_id,
            request=request,
            workspace_id=workspace_id,
            scope=scope,
            key_digest=key_digest,
            request_hash=request_hash,
            trace_id=trace_id,
        )
        if replay is not None:
            return replay
        assert lease_token is not None

        upload_session = self._read_session(workspace_id, upload_session_id)
        try:
            verified = self._verifier.verify(upload_session)
        except ObjectMismatchError as exc:
            self._reject(
                workspace_id=workspace_id,
                upload_session_id=upload_session_id,
                lease_token=lease_token,
                scope=scope,
                key_digest=key_digest,
                request_hash=request_hash,
                actor_id=actor_id,
                trace_id=trace_id,
                error=exc,
            )
            raise
        except (StorageUnavailableError, UploadObjectMissingError):
            self._release(
                workspace_id=workspace_id,
                upload_session_id=upload_session_id,
                lease_token=lease_token,
                trace_id=trace_id,
            )
            raise
        except StoragePreconditionError as exc:
            mismatch = ObjectMismatchError("uploaded object changed during finalize verification")
            self._reject(
                workspace_id=workspace_id,
                upload_session_id=upload_session_id,
                lease_token=lease_token,
                scope=scope,
                key_digest=key_digest,
                request_hash=request_hash,
                actor_id=actor_id,
                trace_id=trace_id,
                error=mismatch,
            )
            raise mismatch from exc

        return self._commit(
            workspace_id=workspace_id,
            upload_session_id=upload_session_id,
            lease_token=lease_token,
            verified=verified,
            scope=scope,
            key_digest=key_digest,
            request_hash=request_hash,
            actor_id=actor_id,
            trace_id=trace_id,
        )

    def _claim(
        self,
        *,
        upload_session_id: str,
        request: UploadSessionMutationRequestV1,
        workspace_id: str,
        scope: str,
        key_digest: str,
        request_hash: str,
        trace_id: str,
    ) -> tuple[UploadFinalizeResponseV1 | None, str | None]:
        expired_session: UploadSession | None = None
        with self._uow_factory() as uow:
            upload_session = load_upload_session(
                uow,
                workspace_id=workspace_id,
                upload_session_id=upload_session_id,
                for_update=True,
            )
            now = self._clock()
            deadline = retention_deadline(uow, upload_session)
            retention_expired = (
                deadline is not None
                and deadline <= now
                and upload_session.expire_for_retention(now=now)
            )
            abandoned_expired = not retention_expired and upload_session.expire_abandoned(now=now)
            if (
                retention_expired
                or abandoned_expired
                or upload_session.state == UploadSessionState.EXPIRED
            ):
                cleanup_scheduled = schedule_abandoned_upload_cleanup(
                    uow=uow,
                    upload_session=upload_session,
                    trace_id=trace_id,
                    policy=self._cleanup_policy,
                    now=now,
                )
                if (retention_expired or abandoned_expired) and not cleanup_scheduled:
                    uow.upload_sessions.save(upload_session)
                if retention_expired or abandoned_expired or cleanup_scheduled:
                    uow.commit()
                expired_session = upload_session
                lease_token = None
            else:
                record = claim_idempotency(
                    uow=uow,
                    scope=scope,
                    key_digest=key_digest,
                    request_hash=request_hash,
                    expires_at=idempotency_expiry(
                        now=now,
                        retention_deadline=deadline,
                    ),
                )
                if record.status == "COMPLETED":
                    return replay_finalize(record), None
                resumed_request = (
                    record.resource_type == "upload-finalize"
                    and record.resource_id == upload_session_id
                )
                if record.resource_id and not resumed_request:
                    raise ConcurrencyError(
                        "finalize idempotency progress belongs to another resource"
                    )

                if upload_session.state == UploadSessionState.FINALIZED:
                    response = self._load_response(uow, upload_session)
                    complete_finalize_idempotency(
                        uow=uow,
                        scope=scope,
                        key_digest=key_digest,
                        request_hash=request_hash,
                        response=response,
                    )
                    uow.commit()
                    return response, None
                expected_version = (
                    upload_session.version if resumed_request else request.expected_version
                )
                lease_token = upload_session.claim_finalize(
                    expected_version=expected_version,
                    owner=self._lease_owner,
                    lease_duration=self._finalize_lease_duration,
                    now=now,
                )
                uow.upload_sessions.save(upload_session)
                uow.idempotency.mark_pending(
                    scope=scope,
                    key_hash=key_digest,
                    request_hash=request_hash,
                    resource_type="upload-finalize",
                    resource_id=upload_session.id,
                    response_data={"phase": "VERIFYING"},
                )
                uow.commit()
        if expired_session is not None:
            raise UploadExpiredError(f"upload session {upload_session_id} has expired")
        return None, lease_token

    def _commit(
        self,
        *,
        workspace_id: str,
        upload_session_id: str,
        lease_token: str,
        verified: VerifiedUpload,
        scope: str,
        key_digest: str,
        request_hash: str,
        actor_id: str,
        trace_id: str,
    ) -> UploadFinalizeResponseV1:
        expired_session: UploadSession | None = None
        response: UploadFinalizeResponseV1 | None = None
        try:
            with self._uow_factory() as uow:
                upload_session = load_upload_session(
                    uow,
                    workspace_id=workspace_id,
                    upload_session_id=upload_session_id,
                    for_update=True,
                )
                now = self._clock()
                if upload_session.state == UploadSessionState.FINALIZED:
                    response = self._load_response(uow, upload_session)
                    complete_finalize_idempotency(
                        uow=uow,
                        scope=scope,
                        key_digest=key_digest,
                        request_hash=request_hash,
                        response=response,
                    )
                    uow.commit()
                    return response
                deadline = retention_deadline(uow, upload_session)
                if (
                    deadline is not None
                    and deadline <= now
                    and upload_session.expire_for_retention(now=now)
                ):
                    cleanup_scheduled = schedule_abandoned_upload_cleanup(
                        uow=uow,
                        upload_session=upload_session,
                        trace_id=trace_id,
                        policy=self._cleanup_policy,
                        now=now,
                    )
                    if not cleanup_scheduled:
                        uow.upload_sessions.save(upload_session)
                    uow.commit()
                    expired_session = upload_session
                else:
                    self._assert_live_lease(upload_session, lease_token, now=now)
                    asset_version = AssetVersion.create(
                        asset_version_id=upload_session.reserved_asset_version_id,
                        workspace_id=workspace_id,
                        asset_id=upload_session.reserved_asset_id,
                        upload_session_id=upload_session.id,
                        filename=upload_session.filename,
                        sha256=verified.sha256,
                        byte_size=verified.byte_size,
                        declared_mime=upload_session.declared_mime,
                        detected_mime=verified.detected_mime,
                        image_format=verified.image_format,
                        width=verified.width,
                        height=verified.height,
                        frame_count=verified.frame_count,
                        category=upload_session.category,
                        role=upload_session.role,
                        integrity_policy_version=upload_session.integrity_policy_version,
                        now=now,
                    )
                    asset = Asset.create_quarantined(
                        asset_id=upload_session.reserved_asset_id,
                        workspace_id=workspace_id,
                        retention_class=upload_session.retention_class,
                        kind=upload_session.asset_kind,
                        workflow_id=upload_session.workflow_id,
                        product_id=upload_session.product_id,
                        sku_id=upload_session.sku_id,
                        current_version_id=asset_version.id,
                        retention_deadline=deadline,
                        now=now,
                    )
                    object_fact = AssetObject.create_quarantined(
                        workspace_id=workspace_id,
                        asset_version_id=asset_version.id,
                        backend=verified.stat.backend,
                        location=verified.stat.reference.location,
                        bucket=verified.stat.bucket,
                        key=verified.stat.reference.key,
                        provider_version_id=verified.stat.reference.version_id,
                        etag=verified.stat.etag,
                        byte_size=verified.byte_size,
                        sha256=verified.sha256,
                        now=now,
                    )
                    operation = build_validation_operation(
                        asset_version=asset_version,
                        object_fact=object_fact,
                        input_hash=canonical_hash(
                            {
                                "asset_version_id": asset_version.id,
                                "content_sha256": asset_version.sha256,
                                "integrity_policy_version": (
                                    asset_version.integrity_policy_version
                                ),
                                "object_fact_id": object_fact.id,
                            }
                        ),
                        policy=self._validation_policy,
                        now=now,
                    )
                    uow.assets.add_quarantined(
                        asset=asset,
                        asset_version=asset_version,
                        object_fact=object_fact,
                    )
                    uow.operations.add(operation)
                    uow.outbox.add(
                        build_upload_finalized_event(
                            upload_session=upload_session,
                            asset=asset,
                            asset_version=asset_version,
                            object_fact=object_fact,
                            operation=operation,
                            trace_id=trace_id,
                            now=now,
                        )
                    )
                    uow.outbox.add(
                        build_validation_event(
                            operation=operation,
                            asset=asset,
                            asset_version=asset_version,
                            object_fact=object_fact,
                            trace_id=trace_id,
                            now=now,
                        )
                    )
                    upload_session.finalize(
                        lease_token=lease_token,
                        asset_version_id=asset_version.id,
                        validation_operation_id=operation.id,
                        now=now,
                    )
                    uow.upload_sessions.save(upload_session)
                    response = finalize_response(
                        upload_session=upload_session,
                        asset=asset,
                        asset_version=asset_version,
                        object_fact=object_fact,
                        operation=operation,
                    )
                    complete_finalize_idempotency(
                        uow=uow,
                        scope=scope,
                        key_digest=key_digest,
                        request_hash=request_hash,
                        response=response,
                    )
                    add_upload_audit(
                        uow=uow,
                        upload_session=upload_session,
                        actor_id=actor_id,
                        action="asset.upload.finalized",
                        trace_id=trace_id,
                        now=now,
                    )
                    uow.commit()
            if expired_session is not None:
                raise UploadExpiredError(f"upload session {upload_session_id} has expired")
            if response is None:
                raise RuntimeError("finalize transaction completed without a response")
            return response
        except UniqueConstraintError:
            return self._recover_committed(
                workspace_id=workspace_id,
                upload_session_id=upload_session_id,
                scope=scope,
                key_digest=key_digest,
                request_hash=request_hash,
            )

    def _release(
        self,
        *,
        workspace_id: str,
        upload_session_id: str,
        lease_token: str,
        trace_id: str,
    ) -> None:
        with self._uow_factory() as uow:
            upload_session = load_upload_session(
                uow,
                workspace_id=workspace_id,
                upload_session_id=upload_session_id,
                for_update=True,
            )
            now = self._clock()
            if (
                upload_session.state != UploadSessionState.FINALIZING
                or upload_session.finalize_lease_token != lease_token
            ):
                return
            upload_session.release_finalize(lease_token=lease_token, now=now)
            cleanup_scheduled = schedule_abandoned_upload_cleanup(
                uow=uow,
                upload_session=upload_session,
                trace_id=trace_id,
                policy=self._cleanup_policy,
                now=now,
            )
            if not cleanup_scheduled:
                uow.upload_sessions.save(upload_session)
            uow.commit()

    def _reject(
        self,
        *,
        workspace_id: str,
        upload_session_id: str,
        lease_token: str,
        scope: str,
        key_digest: str,
        request_hash: str,
        actor_id: str,
        trace_id: str,
        error: ObjectMismatchError,
    ) -> None:
        with self._uow_factory() as uow:
            upload_session = load_upload_session(
                uow,
                workspace_id=workspace_id,
                upload_session_id=upload_session_id,
                for_update=True,
            )
            now = self._clock()
            if (
                upload_session.state != UploadSessionState.FINALIZING
                or upload_session.finalize_lease_token != lease_token
            ):
                return
            upload_session.reject_finalize(
                lease_token=lease_token,
                failure_code="OBJECT_MISMATCH",
                now=now,
            )
            cleanup_scheduled = schedule_abandoned_upload_cleanup(
                uow=uow,
                upload_session=upload_session,
                trace_id=trace_id,
                policy=self._cleanup_policy,
                now=now,
            )
            if not cleanup_scheduled:
                uow.upload_sessions.save(upload_session)
            uow.idempotency.complete(
                scope=scope,
                key_hash=key_digest,
                request_hash=request_hash,
                resource_type="upload-finalize-error",
                resource_id=upload_session.id,
                response_data={
                    "code": "OBJECT_MISMATCH",
                    "message": str(error),
                },
            )
            add_upload_audit(
                uow=uow,
                upload_session=upload_session,
                actor_id=actor_id,
                action="asset.upload.rejected",
                trace_id=trace_id,
                now=now,
            )
            uow.commit()

    def _recover_committed(
        self,
        *,
        workspace_id: str,
        upload_session_id: str,
        scope: str,
        key_digest: str,
        request_hash: str,
    ) -> UploadFinalizeResponseV1:
        with self._uow_factory() as uow:
            upload_session = load_upload_session(
                uow,
                workspace_id=workspace_id,
                upload_session_id=upload_session_id,
                for_update=True,
            )
            if upload_session.state != UploadSessionState.FINALIZED:
                raise ConcurrencyError("another finalize transaction won without a durable result")
            response = self._load_response(uow, upload_session)
            complete_finalize_idempotency(
                uow=uow,
                scope=scope,
                key_digest=key_digest,
                request_hash=request_hash,
                response=response,
            )
            uow.commit()
        return response

    def _read_session(
        self,
        workspace_id: str,
        upload_session_id: str,
    ) -> UploadSession:
        with self._uow_factory() as uow:
            return load_upload_session(
                uow,
                workspace_id=workspace_id,
                upload_session_id=upload_session_id,
            )

    @staticmethod
    def _load_response(
        uow: AssetUnitOfWorkPort,
        upload_session: UploadSession,
    ) -> UploadFinalizeResponseV1:
        asset = uow.assets.get(
            workspace_id=upload_session.workspace_id,
            asset_id=upload_session.reserved_asset_id,
        )
        if (
            asset is None
            or upload_session.finalized_asset_version_id is None
            or upload_session.validation_operation_id is None
        ):
            raise RuntimeError("finalized upload session facts are incomplete")
        asset_version, object_fact = load_asset_version(
            uow,
            workspace_id=upload_session.workspace_id,
            asset_version_id=upload_session.finalized_asset_version_id,
        )
        operation = uow.operations.get(
            upload_session.validation_operation_id,
            workspace_id=upload_session.workspace_id,
        )
        if operation is None:
            raise RuntimeError("finalized upload session operation is missing")
        return finalize_response(
            upload_session=upload_session,
            asset=asset,
            asset_version=asset_version,
            object_fact=object_fact,
            operation=operation,
        )

    @staticmethod
    def _assert_live_lease(
        upload_session: UploadSession,
        lease_token: str,
        *,
        now: datetime,
    ) -> None:
        if (
            upload_session.state != UploadSessionState.FINALIZING
            or upload_session.finalize_lease_token != lease_token
            or upload_session.finalize_lease_expires_at is None
            or upload_session.finalize_lease_expires_at <= now
        ):
            raise LeaseConflictError(f"upload session {upload_session.id} finalize lease was lost")
