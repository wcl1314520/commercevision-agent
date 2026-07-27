"""Pure aggregates for direct upload and quarantined asset identity."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType

from commercevision_domain.ids import new_uuid7
from commercevision_domain.workflow.errors import (
    ConcurrencyError,
    InvalidTransitionError,
    LeaseConflictError,
)
from commercevision_domain.workspace_identity import validate_workspace_id

from .enums import (
    AssetKind,
    AssetObjectState,
    AssetState,
    RetentionClass,
    StorageBackend,
    StorageLocationClass,
    UploadSessionState,
    ValidationStage,
    ValidationVerdict,
)
from .errors import UploadAbortedError, UploadBusyError, UploadExpiredError


def _require_utc(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must be timezone-aware UTC")


def _validate_transfer_policy_identity(version: str, snapshot_sha256: str) -> None:
    if not version or len(version) > 64 or version != version.strip():
        raise ValueError("validation data transfer policy version is invalid")
    if len(snapshot_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in snapshot_sha256
    ):
        raise ValueError("validation data transfer policy snapshot must be a lowercase SHA-256")


_FORBIDDEN_EVIDENCE_KEYS = frozenset(
    {
        "access_key",
        "authorization",
        "description",
        "provider_payload",
        "raw",
        "raw_payload",
        "response_body",
        "secret",
        "service_parameters",
    }
)
_RIGHTS_BLOCK_REASONS = frozenset(
    {
        "RIGHTS_REVOKED",
        "RIGHTS_PERMISSION_EMPTY",
        "RIGHTS_NOT_ACTIVE",
    }
)
_LOCAL_FACT_KEYS = frozenset(
    {
        "character_count",
        "data_bytes",
        "decoded_bytes",
        "frame_count",
        "height",
        "metadata_bytes",
        "metadata_entries",
        "model_id",
        "parameter_count",
        "provider",
        "schema_version",
        "tensor_count",
        "variable_count",
        "width",
    }
)
_EVIDENCE_KEYS_BY_STAGE = {
    ValidationStage.LOCAL_FORMAT: frozenset(
        {"asset_kind", "byte_size", "detected_mime", "facts", "format_name"}
    ),
    ValidationStage.MALWARE: frozenset(
        {"asset_kind", "latency_ms", "outcome", "scanner_version", "signature"}
    ),
    ValidationStage.CONTENT_SAFETY: frozenset(
        {
            "asset_kind",
            "endpoint",
            "failure_code",
            "labels",
            "latency_ms",
            "mapping_version",
            "outcome",
            "policy_version",
            "provider",
            "request_id",
            "retry_after_seconds",
            "risk_level",
            "sdk_version",
            "service",
            "transfer_authorized",
            "transfer_endpoint_host",
            "transfer_endpoint_region",
            "transfer_external",
            "transfer_policy_snapshot_sha256",
            "transfer_policy_version",
            "transfer_provider",
            "transfer_purpose",
        }
    ),
    ValidationStage.PROVENANCE: frozenset(
        {
            "asset_kind",
            "failure_code",
            "failure_codes",
            "latency_ms",
            "manifest_count",
            "outcome",
            "remote_manifest_fetch",
            "sdk_version",
            "status",
            "trust_config_sha256",
            "trust_config_version",
            "validation_state",
            "validator",
        }
    ),
    ValidationStage.PROMOTION: frozenset(
        {
            "backend",
            "byte_size",
            "destination_location",
            "destination_verified",
            "source_deleted",
        }
    ),
}


def _validate_normalized_evidence(evidence: dict[str, object]) -> None:
    entry_count = 0

    def walk(value: object, *, depth: int) -> None:
        nonlocal entry_count
        if depth > 6:
            raise ValueError("validation evidence exceeds the nesting bound")
        if value is None or isinstance(value, (str, bool, int)):
            return
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError("validation evidence numbers must be finite")
            return
        if isinstance(value, list):
            if len(value) > 256:
                raise ValueError("validation evidence list exceeds the entry bound")
            for item in value:
                walk(item, depth=depth + 1)
            return
        if isinstance(value, dict):
            if len(value) > 256:
                raise ValueError("validation evidence object exceeds the entry bound")
            for key, item in value.items():
                entry_count += 1
                if (
                    not isinstance(key, str)
                    or not key
                    or len(key) > 128
                    or key.lower() in _FORBIDDEN_EVIDENCE_KEYS
                ):
                    raise ValueError("raw provider payload fields must not be persisted")
                walk(item, depth=depth + 1)
            return
        raise ValueError("validation evidence must be normalized JSON data")

    walk(evidence, depth=0)
    if entry_count > 1024:
        raise ValueError("validation evidence exceeds the total entry bound")
    encoded = json.dumps(
        evidence,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > 64 * 1024:
        raise ValueError("validation evidence exceeds the byte bound")


def _validate_stage_evidence(
    stage: ValidationStage,
    evidence: dict[str, object],
) -> None:
    allowed = _EVIDENCE_KEYS_BY_STAGE[stage]
    unexpected = set(evidence).difference(allowed)
    if unexpected:
        raise ValueError(f"{stage.value} validation evidence contains non-allowlisted fields")
    if stage == ValidationStage.LOCAL_FORMAT:
        facts = evidence.get("facts")
        if facts is not None and (
            not isinstance(facts, dict) or set(facts).difference(_LOCAL_FACT_KEYS)
        ):
            raise ValueError("LOCAL_FORMAT facts contain non-allowlisted fields")
    if stage == ValidationStage.CONTENT_SAFETY:
        labels = evidence.get("labels")
        if labels is not None:
            if not isinstance(labels, list):
                raise ValueError("CONTENT_SAFETY labels must be a normalized list")
            for label in labels:
                if not isinstance(label, dict) or set(label) != {"code", "confidence"}:
                    raise ValueError(
                        "CONTENT_SAFETY label evidence contains non-allowlisted fields"
                    )


def _freeze_json(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


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
    validation_transfer_policy_version: str = "legacy-validation-transfer-deny-v1"
    validation_transfer_policy_snapshot_sha256: str = "0" * 64

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
        _validate_transfer_policy_identity(
            self.validation_transfer_policy_version,
            self.validation_transfer_policy_snapshot_sha256,
        )
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
        validation_transfer_policy_version: str = ("legacy-validation-transfer-deny-v1"),
        validation_transfer_policy_snapshot_sha256: str = "0" * 64,
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
            validation_transfer_policy_version=validation_transfer_policy_version,
            validation_transfer_policy_snapshot_sha256=(validation_transfer_policy_snapshot_sha256),
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
    block_reason: str | None
    current_version_id: str
    retention_deadline: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime
    current_rights_record_id: str | None = None

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
        if self.status == AssetState.BLOCKED:
            if not self.block_reason:
                raise ValueError("BLOCKED Assets require a block reason")
        elif self.block_reason is not None:
            raise ValueError("only BLOCKED Assets may hold a block reason")

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
            block_reason=None,
            current_version_id=current_version_id,
            retention_deadline=retention_deadline,
            version=1,
            created_at=now,
            updated_at=now,
        )

    def begin_validation(self, *, now: datetime) -> None:
        _require_utc(now, "now")
        if self.status == AssetState.VALIDATING:
            return
        if self.status != AssetState.QUARANTINED:
            raise InvalidTransitionError(
                f"Asset {self.id} cannot begin validation from {self.status.value}"
            )
        self.status = AssetState.VALIDATING
        self._touch(now)

    def mark_pending_review(self, *, now: datetime) -> None:
        _require_utc(now, "now")
        if self.status == AssetState.PENDING_REVIEW:
            return
        if self.status != AssetState.VALIDATING:
            raise InvalidTransitionError(
                f"Asset {self.id} cannot require review from {self.status.value}"
            )
        self.status = AssetState.PENDING_REVIEW
        self._touch(now)

    def mark_pending_rights(self, *, now: datetime) -> None:
        _require_utc(now, "now")
        if self.status == AssetState.PENDING_RIGHTS:
            return
        if self.status != AssetState.VALIDATING:
            raise InvalidTransitionError(
                f"Asset {self.id} cannot enter pending rights from {self.status.value}"
            )
        self.status = AssetState.PENDING_RIGHTS
        self._touch(now)

    def fail_validation(self, *, now: datetime) -> None:
        _require_utc(now, "now")
        if self.status == AssetState.FAILED:
            return
        if self.status not in {
            AssetState.QUARANTINED,
            AssetState.VALIDATING,
        }:
            raise InvalidTransitionError(
                f"Asset {self.id} cannot fail validation from {self.status.value}"
            )
        self.status = AssetState.FAILED
        self._touch(now)

    def resume_failed_validation(self, *, now: datetime) -> None:
        _require_utc(now, "now")
        if self.status != AssetState.FAILED:
            raise InvalidTransitionError(
                f"Asset {self.id} cannot resume validation from {self.status.value}"
            )
        self.status = AssetState.VALIDATING
        self._touch(now)

    def block(self, *, reason_code: str, now: datetime) -> None:
        _require_utc(now, "now")
        if not reason_code or len(reason_code) > 64:
            raise ValueError("Asset block reason must contain 1-64 characters")
        if self.status == AssetState.BLOCKED:
            if self.block_reason == reason_code:
                return
            if (
                reason_code == "ADMINISTRATIVELY_BLOCKED"
                and self.block_reason in _RIGHTS_BLOCK_REASONS
            ):
                self.block_reason = reason_code
                self._touch(now)
                return
            raise InvalidTransitionError(f"Asset {self.id} is already blocked for another reason")
        if self.status not in {
            AssetState.VALIDATING,
            AssetState.PENDING_REVIEW,
            AssetState.PENDING_RIGHTS,
            AssetState.AVAILABLE,
            AssetState.RIGHTS_EXPIRED,
        }:
            raise InvalidTransitionError(
                f"Asset {self.id} cannot be blocked from {self.status.value}"
            )
        self.status = AssetState.BLOCKED
        self.block_reason = reason_code
        self._touch(now)

    def select_available_rights(self, *, rights_record_id: str, now: datetime) -> None:
        _require_utc(now, "now")
        if not rights_record_id:
            raise ValueError("Rights Record id must not be blank")
        if self.status not in {
            AssetState.PENDING_RIGHTS,
            AssetState.AVAILABLE,
            AssetState.RIGHTS_EXPIRED,
            AssetState.BLOCKED,
        }:
            raise InvalidTransitionError(
                f"Asset {self.id} cannot select usable rights from {self.status.value}"
            )
        if self.status == AssetState.BLOCKED and self.block_reason not in _RIGHTS_BLOCK_REASONS:
            raise InvalidTransitionError(f"Asset {self.id} cannot replace a non-rights block")
        self.current_rights_record_id = rights_record_id
        self.status = AssetState.AVAILABLE
        self.block_reason = None
        self._touch(now)

    def select_revoked_rights(self, *, rights_record_id: str, now: datetime) -> None:
        _require_utc(now, "now")
        if not rights_record_id:
            raise ValueError("Rights Record id must not be blank")
        if self.status in {AssetState.DELETING, AssetState.DELETED}:
            raise InvalidTransitionError(
                f"Asset {self.id} cannot revoke rights from {self.status.value}"
            )
        self.current_rights_record_id = rights_record_id
        if self.status == AssetState.BLOCKED and self.block_reason not in _RIGHTS_BLOCK_REASONS:
            self._touch(now)
            return
        self.status = AssetState.BLOCKED
        self.block_reason = "RIGHTS_REVOKED"
        self._touch(now)

    def select_pending_rights(self, *, rights_record_id: str, now: datetime) -> None:
        _require_utc(now, "now")
        if not rights_record_id:
            raise ValueError("Rights Record id must not be blank")
        if self.status != AssetState.PENDING_RIGHTS:
            raise InvalidTransitionError(
                f"Asset {self.id} cannot select pending rights from {self.status.value}"
            )
        self.current_rights_record_id = rights_record_id
        self._touch(now)

    def select_unusable_rights(
        self,
        *,
        rights_record_id: str,
        reason_code: str,
        expired: bool,
        now: datetime,
    ) -> None:
        _require_utc(now, "now")
        if not rights_record_id or not reason_code:
            raise ValueError("Rights Record id and denial reason must not be blank")
        if self.status in {
            AssetState.QUARANTINED,
            AssetState.VALIDATING,
            AssetState.PENDING_REVIEW,
            AssetState.DELETING,
            AssetState.DELETED,
            AssetState.FAILED,
        }:
            raise InvalidTransitionError(
                f"Asset {self.id} cannot select unusable rights from {self.status.value}"
            )
        self.current_rights_record_id = rights_record_id
        if self.status == AssetState.BLOCKED and self.block_reason not in _RIGHTS_BLOCK_REASONS:
            self._touch(now)
            return
        self.status = AssetState.RIGHTS_EXPIRED if expired else AssetState.BLOCKED
        self.block_reason = None if expired else reason_code
        self._touch(now)

    def expire_rights(self, *, now: datetime) -> None:
        _require_utc(now, "now")
        if self.status == AssetState.RIGHTS_EXPIRED:
            return
        if self.status != AssetState.AVAILABLE:
            raise InvalidTransitionError(
                f"Asset {self.id} cannot expire rights from {self.status.value}"
            )
        self.status = AssetState.RIGHTS_EXPIRED
        self.block_reason = None
        self._touch(now)

    def begin_retention_cleanup(self, *, now: datetime) -> None:
        _require_utc(now, "now")
        if self.retention_deadline is None:
            raise InvalidTransitionError(f"Foundation Asset {self.id} has no retention deadline")
        if now < self.retention_deadline:
            raise InvalidTransitionError(
                f"Asset {self.id} cannot expire before its retention deadline"
            )
        if self.status in {AssetState.DELETING, AssetState.DELETED}:
            return
        if self.status not in {
            AssetState.QUARANTINED,
            AssetState.VALIDATING,
            AssetState.PENDING_REVIEW,
            AssetState.PENDING_RIGHTS,
            AssetState.AVAILABLE,
            AssetState.BLOCKED,
            AssetState.RIGHTS_EXPIRED,
            AssetState.FAILED,
        }:
            raise InvalidTransitionError(
                f"Asset {self.id} cannot begin retention cleanup from {self.status.value}"
            )
        self.status = AssetState.DELETING
        self.block_reason = None
        self._touch(now)

    def expire_retention(self, *, now: datetime) -> None:
        _require_utc(now, "now")
        if self.retention_deadline is None:
            raise InvalidTransitionError(f"Foundation Asset {self.id} has no retention deadline")
        if now < self.retention_deadline:
            raise InvalidTransitionError(
                f"Asset {self.id} cannot expire before its retention deadline"
            )
        if self.status == AssetState.DELETED:
            return
        if self.status not in {
            AssetState.QUARANTINED,
            AssetState.VALIDATING,
            AssetState.PENDING_REVIEW,
            AssetState.PENDING_RIGHTS,
            AssetState.AVAILABLE,
            AssetState.BLOCKED,
            AssetState.RIGHTS_EXPIRED,
            AssetState.FAILED,
            AssetState.DELETING,
        }:
            raise InvalidTransitionError(f"Asset {self.id} cannot expire from {self.status.value}")
        self.status = AssetState.DELETED
        self.block_reason = None
        self._touch(now)

    def _touch(self, now: datetime) -> None:
        self.version += 1
        self.updated_at = now


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
    detected_mime: str | None
    image_format: str | None
    width: int | None
    height: int | None
    frame_count: int | None
    category: str
    role: str
    integrity_policy_version: str
    validation_policy_version: str
    created_at: datetime
    validation_transfer_policy_version: str = "legacy-validation-transfer-deny-v1"
    validation_transfer_policy_snapshot_sha256: str = "0" * 64

    def __post_init__(self) -> None:
        validate_workspace_id(self.workspace_id)
        _require_utc(self.created_at, "created_at")
        if self.version_number < 1:
            raise ValueError("Asset Version number must be positive")
        if self.byte_size < 1:
            raise ValueError("Asset Version byte size must be positive")
        image_facts = (
            self.image_format,
            self.width,
            self.height,
            self.frame_count,
        )
        if all(value is None for value in image_facts):
            if self.detected_mime is not None:
                raise ValueError(
                    "non-image Asset Versions keep detected MIME in validation evidence"
                )
        elif any(value is None for value in image_facts):
            raise ValueError("Asset Version image facts must be complete")
        elif (
            not self.image_format
            or self.width is None
            or self.height is None
            or self.frame_count is None
            or self.width < 1
            or self.height < 1
            or self.frame_count < 1
        ):
            raise ValueError("Asset Version image facts must be positive")
        if self.image_format is not None and self.detected_mime is None:
            raise ValueError("image Asset Versions require a detected MIME")
        if (
            not self.integrity_policy_version
            or len(self.integrity_policy_version) > 64
            or not self.validation_policy_version
            or len(self.validation_policy_version) > 64
        ):
            raise ValueError("Asset Version policy identities are invalid")
        _validate_transfer_policy_identity(
            self.validation_transfer_policy_version,
            self.validation_transfer_policy_snapshot_sha256,
        )
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
        detected_mime: str | None,
        image_format: str | None,
        width: int | None,
        height: int | None,
        frame_count: int | None,
        category: str,
        role: str,
        integrity_policy_version: str,
        validation_policy_version: str,
        validation_transfer_policy_version: str = ("legacy-validation-transfer-deny-v1"),
        validation_transfer_policy_snapshot_sha256: str = "0" * 64,
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
            validation_policy_version=validation_policy_version,
            validation_transfer_policy_version=validation_transfer_policy_version,
            validation_transfer_policy_snapshot_sha256=(validation_transfer_policy_snapshot_sha256),
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
        if self.state == AssetObjectState.CONTROLLED and self.location not in {
            StorageLocationClass.TASK,
            StorageLocationClass.FOUNDATION,
        }:
            raise ValueError("controlled Asset objects must use retained storage")

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

    @classmethod
    def create_controlled(
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
        if location not in {
            StorageLocationClass.TASK,
            StorageLocationClass.FOUNDATION,
        }:
            raise ValueError("controlled Asset objects require retained storage")
        return cls(
            id=new_uuid7(),
            workspace_id=workspace_id,
            asset_version_id=asset_version_id,
            role="CONTROLLED_ORIGINAL",
            backend=backend,
            location=location,
            bucket=bucket,
            key=key,
            provider_version_id=provider_version_id,
            etag=etag,
            byte_size=byte_size,
            sha256=sha256,
            state=AssetObjectState.CONTROLLED,
            version=1,
            created_at=now,
            updated_at=now,
        )

    def mark_delete_pending(self, *, now: datetime) -> None:
        _require_utc(now, "now")
        if self.state == AssetObjectState.DELETE_PENDING:
            return
        if self.state == AssetObjectState.DELETED:
            return
        if self.state not in {
            AssetObjectState.QUARANTINED,
            AssetObjectState.CONTROLLED,
        }:
            raise InvalidTransitionError(
                f"Asset object {self.id} cannot schedule deletion from {self.state.value}"
            )
        self.state = AssetObjectState.DELETE_PENDING
        self._touch(now)

    def mark_deleted(self, *, now: datetime) -> None:
        _require_utc(now, "now")
        if self.state == AssetObjectState.DELETED:
            return
        if self.state != AssetObjectState.DELETE_PENDING:
            raise InvalidTransitionError(
                f"Asset object {self.id} cannot be deleted from {self.state.value}"
            )
        self.state = AssetObjectState.DELETED
        self._touch(now)

    def _touch(self, now: datetime) -> None:
        self.version += 1
        self.updated_at = now


@dataclass(frozen=True, slots=True)
class AssetValidationResult:
    id: str
    workspace_id: str
    operation_id: str
    asset_version_id: str
    asset_object_id: str
    attempt_number: int
    stage: ValidationStage
    validator_name: str
    validator_version: str
    policy_version: str
    verdict: ValidationVerdict
    reason_code: str | None
    object_provider_version_id: str
    object_etag: str
    content_sha256: str
    evidence: Mapping[str, object]
    retention_deadline: datetime | None
    created_at: datetime

    def __post_init__(self) -> None:
        validate_workspace_id(self.workspace_id)
        _require_utc(self.created_at, "created_at")
        if self.retention_deadline is not None:
            _require_utc(self.retention_deadline, "retention_deadline")
            if self.retention_deadline <= self.created_at:
                raise ValueError("validation evidence retention must end after creation")
        for value, field, maximum in (
            (self.operation_id, "operation_id", 36),
            (self.asset_version_id, "asset_version_id", 36),
            (self.asset_object_id, "asset_object_id", 36),
            (self.validator_name, "validator_name", 64),
            (self.validator_version, "validator_version", 128),
            (self.policy_version, "policy_version", 64),
            (self.object_provider_version_id, "object_provider_version_id", 256),
            (self.object_etag, "object_etag", 512),
        ):
            if not value or len(value) > maximum:
                raise ValueError(f"{field} must contain 1-{maximum} characters")
        if self.attempt_number < 1:
            raise ValueError("validation attempt number must be positive")
        if len(self.content_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.content_sha256
        ):
            raise ValueError("validation content SHA-256 must be lowercase hexadecimal")
        if self.verdict in {
            ValidationVerdict.REVIEW,
            ValidationVerdict.BLOCK,
            ValidationVerdict.RETRYABLE_FAILURE,
            ValidationVerdict.TERMINAL_FAILURE,
        }:
            if not self.reason_code:
                raise ValueError("non-pass validation verdicts require a reason code")
        elif self.reason_code is not None:
            raise ValueError("pass validation verdicts must not carry a reason code")
        if self.reason_code is not None and len(self.reason_code) > 64:
            raise ValueError("validation reason code exceeds 64 characters")
        if not isinstance(self.evidence, dict):
            raise ValueError("validation evidence must be constructed from a JSON object")
        _validate_normalized_evidence(self.evidence)
        _validate_stage_evidence(self.stage, self.evidence)
        object.__setattr__(self, "evidence", _freeze_json(self.evidence))

    def evidence_dict(self) -> dict[str, object]:
        thawed = _thaw_json(self.evidence)
        if not isinstance(thawed, dict):
            raise RuntimeError("validation evidence root is not an object")
        return thawed

    @classmethod
    def create(
        cls,
        *,
        workspace_id: str,
        operation_id: str,
        asset_version_id: str,
        asset_object_id: str,
        attempt_number: int,
        stage: ValidationStage,
        validator_name: str,
        validator_version: str,
        policy_version: str,
        verdict: ValidationVerdict,
        reason_code: str | None,
        object_provider_version_id: str,
        object_etag: str,
        content_sha256: str,
        evidence: dict[str, object],
        retention_deadline: datetime | None,
        now: datetime,
    ) -> AssetValidationResult:
        return cls(
            id=new_uuid7(),
            workspace_id=workspace_id,
            operation_id=operation_id,
            asset_version_id=asset_version_id,
            asset_object_id=asset_object_id,
            attempt_number=attempt_number,
            stage=stage,
            validator_name=validator_name,
            validator_version=validator_version,
            policy_version=policy_version,
            verdict=verdict,
            reason_code=reason_code,
            object_provider_version_id=object_provider_version_id,
            object_etag=object_etag,
            content_sha256=content_sha256,
            evidence=evidence,
            retention_deadline=retention_deadline,
            created_at=now,
        )
