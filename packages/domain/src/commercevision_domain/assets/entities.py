"""Pure aggregates for direct upload and quarantined asset identity."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from commercevision_domain.ids import new_uuid7
from commercevision_domain.workflow.errors import ConcurrencyError, LeaseConflictError
from commercevision_domain.workspace_identity import validate_workspace_id

from .enums import (
    AssetKind,
    AssetObjectState,
    AssetState,
    RetentionClass,
    StorageBackend,
    StorageLocationClass,
    UploadSessionState,
)
from .errors import UploadAbortedError, UploadBusyError, UploadExpiredError


def _require_utc(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must be timezone-aware UTC")


@dataclass(slots=True)
class UploadSession:
    id: str
    workspace_id: str
    actor_id: str
    reserved_asset_id: str
    reserved_asset_version_id: str
    retention_class: RetentionClass
    asset_kind: AssetKind
    filename: str
    declared_mime: str
    expected_byte_length: int
    expected_sha256: str
    workflow_id: str | None
    product_id: str | None
    sku_id: str | None
    category: str
    role: str
    upload_policy_version: str
    integrity_policy_version: str
    storage_backend: StorageBackend
    storage_location: StorageLocationClass
    storage_bucket: str
    storage_key: str
    destination_location: StorageLocationClass
    destination_bucket: str
    destination_key: str
    state: UploadSessionState
    finalize_lease_owner: str | None
    finalize_lease_token: str | None
    finalize_lease_expires_at: datetime | None
    finalize_attempts: int
    failure_code: str | None
    finalized_asset_version_id: str | None
    validation_operation_id: str | None
    cleanup_operation_id: str | None
    cleanup_reconcile_until: datetime | None
    expires_at: datetime
    version: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        validate_workspace_id(self.workspace_id)
        _require_utc(self.expires_at, "expires_at")
        _require_utc(self.created_at, "created_at")
        _require_utc(self.updated_at, "updated_at")
        if self.finalize_lease_expires_at is not None:
            _require_utc(self.finalize_lease_expires_at, "finalize_lease_expires_at")
        if self.cleanup_reconcile_until is not None:
            _require_utc(self.cleanup_reconcile_until, "cleanup_reconcile_until")
        if self.retention_class == RetentionClass.TASK and self.workflow_id is None:
            raise ValueError("Task Assets require a Workflow")
        if self.retention_class == RetentionClass.FOUNDATION and self.workflow_id is not None:
            raise ValueError("Foundation Assets must not reference a Workflow")
        if self.sku_id is not None and self.product_id is None:
            raise ValueError("an Upload Session SKU requires a Product")
        if self.expected_byte_length < 1:
            raise ValueError("expected byte length must be positive")
        if len(self.expected_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.expected_sha256
        ):
            raise ValueError("expected SHA-256 must be a lowercase hexadecimal digest")
        if self.storage_location != StorageLocationClass.QUARANTINE:
            raise ValueError("upload sessions must target quarantine storage")
        if not all(
            (
                self.reserved_asset_id,
                self.reserved_asset_version_id,
                self.storage_bucket,
                self.storage_key,
                self.destination_bucket,
                self.destination_key,
            )
        ):
            raise ValueError("upload session storage identities must not be blank")
        expected_destination = (
            StorageLocationClass.TASK
            if self.retention_class == RetentionClass.TASK
            else StorageLocationClass.FOUNDATION
        )
        if self.destination_location != expected_destination:
            raise ValueError("upload session destination must match its retention class")
        if (
            self.storage_key == self.destination_key
            and self.storage_bucket == self.destination_bucket
        ):
            raise ValueError("upload source and destination must be distinct")
        if self.version < 1 or self.finalize_attempts < 0:
            raise ValueError("upload session counters must not be negative")

        lease_values = (
            self.finalize_lease_owner,
            self.finalize_lease_token,
            self.finalize_lease_expires_at,
        )
        if self.state == UploadSessionState.FINALIZING:
            if any(value is None for value in lease_values):
                raise ValueError("FINALIZING Upload Sessions require a complete lease")
        elif any(value is not None for value in lease_values):
            raise ValueError("only FINALIZING Upload Sessions may hold a lease")

        result_values = (
            self.finalized_asset_version_id,
            self.validation_operation_id,
        )
        if self.state == UploadSessionState.FINALIZED:
            if any(value is None for value in result_values):
                raise ValueError("FINALIZED Upload Sessions require complete result facts")
            if self.finalized_asset_version_id != self.reserved_asset_version_id:
                raise ValueError("FINALIZED Upload Sessions must use the reserved Asset Version")
        elif any(value is not None for value in result_values):
            raise ValueError("only FINALIZED Upload Sessions may hold result facts")

        cleanup_values = (self.cleanup_operation_id, self.cleanup_reconcile_until)
        if any(value is None for value in cleanup_values) and any(
            value is not None for value in cleanup_values
        ):
            raise ValueError("cleanup operation and reconciliation window must be paired")
        if self.cleanup_operation_id is not None:
            if self.state not in {
                UploadSessionState.FINALIZED,
                UploadSessionState.EXPIRED,
                UploadSessionState.ABORTED,
            }:
                raise ValueError("only terminal Upload Sessions may hold a cleanup operation")
            assert self.cleanup_reconcile_until is not None
            if self.cleanup_reconcile_until <= self.updated_at:
                raise ValueError("cleanup reconciliation window must end after its update")

        if self.state == UploadSessionState.ABORTED:
            if not self.failure_code:
                raise ValueError("ABORTED Upload Sessions require a failure code")
        elif self.failure_code is not None:
            raise ValueError("only ABORTED Upload Sessions may hold a failure code")

    @classmethod
    def create(
        cls,
        *,
        workspace_id: str,
        actor_id: str,
        reserved_asset_id: str,
        reserved_asset_version_id: str,
        retention_class: RetentionClass,
        asset_kind: AssetKind,
        filename: str,
        declared_mime: str,
        expected_byte_length: int,
        expected_sha256: str,
        workflow_id: str | None,
        product_id: str | None,
        sku_id: str | None,
        category: str,
        role: str,
        upload_policy_version: str,
        integrity_policy_version: str,
        storage_backend: StorageBackend,
        storage_bucket: str,
        storage_key: str,
        destination_location: StorageLocationClass,
        destination_bucket: str,
        destination_key: str,
        expires_at: datetime,
        now: datetime | None = None,
    ) -> UploadSession:
        created_at = now or datetime.now(UTC)
        _require_utc(created_at, "now")
        _require_utc(expires_at, "expires_at")
        if expires_at <= created_at:
            raise ValueError("upload session expiry must be in the future")
        if expected_byte_length < 1:
            raise ValueError("expected byte length must be positive")
        if len(expected_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in expected_sha256
        ):
            raise ValueError("expected SHA-256 must be a lowercase hexadecimal digest")
        return cls(
            id=new_uuid7(),
            workspace_id=workspace_id,
            actor_id=actor_id,
            reserved_asset_id=reserved_asset_id,
            reserved_asset_version_id=reserved_asset_version_id,
            retention_class=retention_class,
            asset_kind=asset_kind,
            filename=filename,
            declared_mime=declared_mime,
            expected_byte_length=expected_byte_length,
            expected_sha256=expected_sha256,
            workflow_id=workflow_id,
            product_id=product_id,
            sku_id=sku_id,
            category=category,
            role=role,
            upload_policy_version=upload_policy_version,
            integrity_policy_version=integrity_policy_version,
            storage_backend=storage_backend,
            storage_location=StorageLocationClass.QUARANTINE,
            storage_bucket=storage_bucket,
            storage_key=storage_key,
            destination_location=destination_location,
            destination_bucket=destination_bucket,
            destination_key=destination_key,
            state=UploadSessionState.OPEN,
            finalize_lease_owner=None,
            finalize_lease_token=None,
            finalize_lease_expires_at=None,
            finalize_attempts=0,
            failure_code=None,
            finalized_asset_version_id=None,
            validation_operation_id=None,
            cleanup_operation_id=None,
            cleanup_reconcile_until=None,
            expires_at=expires_at,
            version=1,
            created_at=created_at,
            updated_at=created_at,
        )

    def expire_if_due(self, *, now: datetime) -> bool:
        _require_utc(now, "now")
        if self.state != UploadSessionState.OPEN:
            return False
        if self.expires_at > now:
            return False
        self.state = UploadSessionState.EXPIRED
        self._clear_lease()
        self._touch(now)
        return True

    def expire_for_retention(self, *, now: datetime) -> bool:
        """Apply the owning Workflow's hard retention boundary."""

        _require_utc(now, "now")
        if self.state not in {
            UploadSessionState.OPEN,
            UploadSessionState.FINALIZING,
        }:
            return False
        self.state = UploadSessionState.EXPIRED
        self._clear_lease()
        self._touch(now)
        return True

    def expire_abandoned(self, *, now: datetime) -> bool:
        """Expire an upload only after both upload and active finalize leases end."""

        _require_utc(now, "now")
        if self.expires_at > now:
            return False
        if self.state == UploadSessionState.OPEN or (
            self.state == UploadSessionState.FINALIZING
            and self.finalize_lease_expires_at is not None
            and self.finalize_lease_expires_at <= now
        ):
            self.state = UploadSessionState.EXPIRED
        else:
            return False
        self._clear_lease()
        self._touch(now)
        return True

    def schedule_cleanup(
        self,
        *,
        operation_id: str,
        reconcile_until: datetime,
        now: datetime,
    ) -> None:
        _require_utc(now, "now")
        _require_utc(reconcile_until, "reconcile_until")
        if self.state not in {
            UploadSessionState.FINALIZED,
            UploadSessionState.EXPIRED,
            UploadSessionState.ABORTED,
        }:
            raise ValueError("cleanup requires a terminal Upload Session")
        if not operation_id or len(operation_id) > 36:
            raise ValueError("cleanup operation id must contain 1-36 characters")
        if self.cleanup_operation_id == operation_id:
            if self.cleanup_reconcile_until != reconcile_until:
                raise ValueError("cleanup reconciliation window does not match")
            return
        if self.cleanup_operation_id is not None:
            raise ValueError("cleanup operation is already attached")
        if reconcile_until <= now:
            raise ValueError("cleanup reconciliation window must end in the future")
        self.cleanup_operation_id = operation_id
        self.cleanup_reconcile_until = reconcile_until
        self._touch(now)

    def claim_finalize(
        self,
        *,
        expected_version: int,
        owner: str,
        lease_duration: timedelta,
        now: datetime,
    ) -> str:
        if self.expire_if_due(now=now):
            raise UploadExpiredError(f"upload session {self.id} has expired")
        if self.state == UploadSessionState.EXPIRED:
            raise UploadExpiredError(f"upload session {self.id} has expired")
        if self.state == UploadSessionState.ABORTED:
            raise UploadAbortedError(f"upload session {self.id} was aborted")
        self.assert_version(expected_version)
        if self.state == UploadSessionState.FINALIZED:
            raise LeaseConflictError(f"upload session {self.id} is already finalized")
        if (
            self.state == UploadSessionState.FINALIZING
            and self.finalize_lease_expires_at is not None
            and self.finalize_lease_expires_at > now
        ):
            raise UploadBusyError(f"upload session {self.id} is being finalized")
        if not owner:
            raise ValueError("finalize lease owner must not be blank")
        if lease_duration <= timedelta(0):
            raise ValueError("finalize lease duration must be positive")
        self.state = UploadSessionState.FINALIZING
        self.finalize_lease_owner = owner
        self.finalize_lease_token = new_uuid7()
        self.finalize_lease_expires_at = now + lease_duration
        self.finalize_attempts += 1
        self.failure_code = None
        self._touch(now)
        return self.finalize_lease_token

    def release_finalize(self, *, lease_token: str, now: datetime) -> None:
        self._assert_lease(lease_token)
        self.state = (
            UploadSessionState.EXPIRED if self.expires_at <= now else UploadSessionState.OPEN
        )
        self._clear_lease()
        self._touch(now)

    def reject_finalize(self, *, lease_token: str, failure_code: str, now: datetime) -> None:
        self._assert_live_lease(lease_token, now=now)
        self.state = UploadSessionState.ABORTED
        self.failure_code = failure_code
        self._clear_lease()
        self._touch(now)

    def finalize(
        self,
        *,
        lease_token: str,
        asset_version_id: str,
        validation_operation_id: str,
        now: datetime,
    ) -> None:
        self._assert_live_lease(lease_token, now=now)
        if asset_version_id != self.reserved_asset_version_id:
            raise ValueError("finalize must use the reserved Asset Version ID")
        self.state = UploadSessionState.FINALIZED
        self.finalized_asset_version_id = asset_version_id
        self.validation_operation_id = validation_operation_id
        self._clear_lease()
        self._touch(now)

    def abort(self, *, expected_version: int, now: datetime) -> None:
        self.assert_version(expected_version)
        if self.state == UploadSessionState.FINALIZED:
            raise LeaseConflictError(f"upload session {self.id} is already finalized")
        if self.state == UploadSessionState.FINALIZING:
            raise UploadBusyError(f"upload session {self.id} is being finalized")
        if self.state in {UploadSessionState.ABORTED, UploadSessionState.EXPIRED}:
            return
        self.state = UploadSessionState.ABORTED
        self.failure_code = "CLIENT_ABORTED"
        self._clear_lease()
        self._touch(now)

    def assert_version(self, expected_version: int) -> None:
        if self.version != expected_version:
            raise ConcurrencyError(
                f"upload session {self.id} version is {self.version}, expected {expected_version}"
            )

    def _assert_lease(self, lease_token: str) -> None:
        if self.state != UploadSessionState.FINALIZING or self.finalize_lease_token != lease_token:
            raise LeaseConflictError(f"upload session {self.id} finalize lease was lost")

    def _assert_live_lease(self, lease_token: str, *, now: datetime) -> None:
        _require_utc(now, "now")
        self._assert_lease(lease_token)
        if self.finalize_lease_expires_at is None or self.finalize_lease_expires_at <= now:
            raise LeaseConflictError(f"upload session {self.id} finalize lease has expired")

    def _clear_lease(self) -> None:
        self.finalize_lease_owner = None
        self.finalize_lease_token = None
        self.finalize_lease_expires_at = None

    def _touch(self, now: datetime) -> None:
        _require_utc(now, "now")
        self.version += 1
        self.updated_at = now


@dataclass(slots=True)
class Asset:
    id: str
    workspace_id: str
    retention_class: RetentionClass
    kind: AssetKind
    workflow_id: str | None
    product_id: str | None
    sku_id: str | None
    status: AssetState
    current_version_id: str
    retention_deadline: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        validate_workspace_id(self.workspace_id)
        _require_utc(self.created_at, "created_at")
        _require_utc(self.updated_at, "updated_at")
        if self.retention_deadline is not None:
            _require_utc(self.retention_deadline, "retention_deadline")
        if self.retention_class == RetentionClass.TASK and self.workflow_id is None:
            raise ValueError("Task Assets require a Workflow")
        if self.retention_class == RetentionClass.FOUNDATION and self.workflow_id is not None:
            raise ValueError("Foundation Assets must not reference a Workflow")
        if self.retention_class == RetentionClass.TASK and self.retention_deadline is None:
            raise ValueError("Task Assets require a retention deadline")
        if (
            self.retention_class == RetentionClass.FOUNDATION
            and self.retention_deadline is not None
        ):
            raise ValueError("Foundation Assets must not have a task retention deadline")
        if self.sku_id is not None and self.product_id is None:
            raise ValueError("an Asset SKU requires a Product")
        if not self.current_version_id:
            raise ValueError("an Asset must have a current version")

    @classmethod
    def create_quarantined(
        cls,
        *,
        asset_id: str,
        workspace_id: str,
        retention_class: RetentionClass,
        kind: AssetKind,
        workflow_id: str | None,
        product_id: str | None,
        sku_id: str | None,
        current_version_id: str,
        retention_deadline: datetime | None,
        now: datetime,
    ) -> Asset:
        return cls(
            id=asset_id,
            workspace_id=workspace_id,
            retention_class=retention_class,
            kind=kind,
            workflow_id=workflow_id,
            product_id=product_id,
            sku_id=sku_id,
            status=AssetState.QUARANTINED,
            current_version_id=current_version_id,
            retention_deadline=retention_deadline,
            version=1,
            created_at=now,
            updated_at=now,
        )


@dataclass(frozen=True, slots=True)
class AssetVersion:
    id: str
    workspace_id: str
    asset_id: str
    version_number: int
    upload_session_id: str
    filename: str
    sha256: str
    byte_size: int
    declared_mime: str
    detected_mime: str
    image_format: str
    width: int
    height: int
    frame_count: int
    category: str
    role: str
    integrity_policy_version: str
    created_at: datetime

    def __post_init__(self) -> None:
        validate_workspace_id(self.workspace_id)
        _require_utc(self.created_at, "created_at")
        if self.version_number < 1:
            raise ValueError("Asset Version number must be positive")
        if self.byte_size < 1:
            raise ValueError("Asset Version byte size must be positive")
        if self.width < 1 or self.height < 1 or self.frame_count < 1:
            raise ValueError("Asset Version image facts must be positive")
        if len(self.sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.sha256
        ):
            raise ValueError("Asset Version SHA-256 must be a lowercase hexadecimal digest")

    @classmethod
    def create(
        cls,
        *,
        asset_version_id: str,
        workspace_id: str,
        asset_id: str,
        upload_session_id: str,
        filename: str,
        sha256: str,
        byte_size: int,
        declared_mime: str,
        detected_mime: str,
        image_format: str,
        width: int,
        height: int,
        frame_count: int,
        category: str,
        role: str,
        integrity_policy_version: str,
        now: datetime,
    ) -> AssetVersion:
        return cls(
            id=asset_version_id,
            workspace_id=workspace_id,
            asset_id=asset_id,
            version_number=1,
            upload_session_id=upload_session_id,
            filename=filename,
            sha256=sha256,
            byte_size=byte_size,
            declared_mime=declared_mime,
            detected_mime=detected_mime,
            image_format=image_format,
            width=width,
            height=height,
            frame_count=frame_count,
            category=category,
            role=role,
            integrity_policy_version=integrity_policy_version,
            created_at=now,
        )


@dataclass(slots=True)
class AssetObject:
    id: str
    workspace_id: str
    asset_version_id: str
    role: str
    backend: StorageBackend
    location: StorageLocationClass
    bucket: str
    key: str
    provider_version_id: str | None
    etag: str
    byte_size: int
    sha256: str
    state: AssetObjectState
    version: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        validate_workspace_id(self.workspace_id)
        _require_utc(self.created_at, "created_at")
        _require_utc(self.updated_at, "updated_at")
        if self.byte_size < 1:
            raise ValueError("Asset object byte size must be positive")
        if len(self.sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.sha256
        ):
            raise ValueError("Asset object SHA-256 must be a lowercase hexadecimal digest")
        if not self.etag:
            raise ValueError("Asset object ETag must not be blank")
        if (
            self.provider_version_id is None
            or not self.provider_version_id.strip()
            or self.provider_version_id.strip().lower() == "null"
        ):
            raise ValueError("Asset object provider version must identify one exact version")
        if (
            self.state == AssetObjectState.QUARANTINED
            and self.location != StorageLocationClass.QUARANTINE
        ):
            raise ValueError("quarantined Asset objects must remain in quarantine storage")

    @classmethod
    def create_quarantined(
        cls,
        *,
        workspace_id: str,
        asset_version_id: str,
        backend: StorageBackend,
        location: StorageLocationClass,
        bucket: str,
        key: str,
        provider_version_id: str | None,
        etag: str,
        byte_size: int,
        sha256: str,
        now: datetime,
    ) -> AssetObject:
        return cls(
            id=new_uuid7(),
            workspace_id=workspace_id,
            asset_version_id=asset_version_id,
            role="ORIGINAL",
            backend=backend,
            location=location,
            bucket=bucket,
            key=key,
            provider_version_id=provider_version_id,
            etag=etag,
            byte_size=byte_size,
            sha256=sha256,
            state=AssetObjectState.QUARANTINED,
            version=1,
            created_at=now,
            updated_at=now,
        )
