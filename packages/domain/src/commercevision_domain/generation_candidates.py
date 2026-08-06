"""Immutable generation batches and deterministic candidate slot identities."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid5

from commercevision_domain.ids import canonicalize_uuid
from commercevision_domain.operations import OperationKind
from commercevision_domain.workspace_identity import validate_workspace_id

_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_MAX_CANDIDATES = 16
_MAX_AUTHORIZED_ASSETS = 16
_MAX_RETENTION = timedelta(hours=72)
_MAX_REFERENCE_ASSETS = 16
_MAX_REPAIR_SCOPES = 16
_GENERATION_OPERATION_KINDS = {
    OperationKind.IMAGE_GENERATION,
    OperationKind.IMAGE_EDITING,
}


def _validate_uuid(value: str, field_name: str) -> str:
    try:
        canonical = canonicalize_uuid(value)
    except ValueError:
        raise ValueError(f"{field_name} must be a canonical UUID") from None
    if canonical != value:
        raise ValueError(f"{field_name} must be a canonical UUID")
    return value


def _validate_token(value: str, field_name: str) -> str:
    if not isinstance(value, str) or _TOKEN_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} is invalid")
    return value


def _validate_sha256(value: str, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be lowercase SHA-256")
    return value


def _validate_positive_integer(value: int, field_name: str, *, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
        raise ValueError(f"{field_name} must be between 1 and {maximum}")
    return value


def _validate_utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must be timezone-aware UTC")
    return value


def _canonical_datetime(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canonical_sha256(value: dict[str, object]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class GenerationBatch:
    id: str
    workspace_id: str
    workflow_id: str
    workflow_version: int
    creative_plan_version_id: str
    plan_approval_id: str
    direction_key: str
    tool_intent_key: str
    tool_intent_sha256: str
    prompt_sha256: str
    context_sha256: str
    route_decision_sha256: str
    route_request_sha256: str
    operation_kind: OperationKind
    authorized_asset_version_ids: tuple[str, ...]
    candidate_count: int
    route_policy_version: str
    tool_policy_version: str
    rights_policy_version: str
    safety_policy_version: str
    workflow_deadline: datetime
    source_rights_deadline: datetime | None
    edit_source_asset_version_id: str | None
    edit_mask_asset_version_id: str | None
    approved_repair_scope: tuple[str, ...]
    retention_deadline: datetime
    created_by: str
    created_at: datetime

    def __post_init__(self) -> None:
        _validate_uuid(self.id, "Generation Batch id")
        validate_workspace_id(self.workspace_id)
        _validate_uuid(self.workflow_id, "Workflow id")
        _validate_positive_integer(
            self.workflow_version,
            "Workflow version",
            maximum=2_147_483_647,
        )
        _validate_uuid(self.creative_plan_version_id, "Creative Plan Version id")
        _validate_uuid(self.plan_approval_id, "Plan Approval id")
        for value, field_name in (
            (self.direction_key, "Direction key"),
            (self.tool_intent_key, "Tool Intent key"),
            (self.route_policy_version, "Route Policy version"),
            (self.tool_policy_version, "Tool Policy version"),
            (self.rights_policy_version, "Rights Policy version"),
            (self.safety_policy_version, "Safety Policy version"),
            (self.created_by, "Generation Batch actor"),
        ):
            _validate_token(value, field_name)
        _validate_sha256(self.tool_intent_sha256, "Tool Intent hash")
        _validate_sha256(self.prompt_sha256, "Prompt hash")
        _validate_sha256(self.context_sha256, "Context hash")
        _validate_sha256(self.route_decision_sha256, "Route Decision hash")
        _validate_sha256(self.route_request_sha256, "Route Request hash")
        object.__setattr__(self, "operation_kind", OperationKind(self.operation_kind))
        if self.operation_kind not in _GENERATION_OPERATION_KINDS:
            raise ValueError("Generation Batch operation kind is invalid")
        if (
            not isinstance(self.authorized_asset_version_ids, tuple)
            or len(self.authorized_asset_version_ids) > _MAX_AUTHORIZED_ASSETS
            or len(set(self.authorized_asset_version_ids)) != len(self.authorized_asset_version_ids)
        ):
            raise ValueError("authorized Asset Version identities are invalid")
        for asset_version_id in self.authorized_asset_version_ids:
            _validate_uuid(asset_version_id, "authorized Asset Version id")
        _validate_positive_integer(
            self.candidate_count,
            "Generation Batch candidate count",
            maximum=_MAX_CANDIDATES,
        )
        _validate_utc(self.created_at, "Generation Batch created_at")
        _validate_utc(self.workflow_deadline, "Generation Batch Workflow deadline")
        if self.source_rights_deadline is not None:
            _validate_utc(
                self.source_rights_deadline,
                "Generation Batch source Rights deadline",
            )
        _validate_utc(self.retention_deadline, "Generation Batch retention deadline")
        if not self.created_at < self.retention_deadline <= self.created_at + _MAX_RETENTION:
            raise ValueError("Generation Batch retention deadline is invalid")
        if self.retention_deadline > self.workflow_deadline:
            raise ValueError("Generation Batch cannot outlive its Workflow deadline")
        if (
            self.source_rights_deadline is not None
            and self.retention_deadline > self.source_rights_deadline
        ):
            raise ValueError("Generation Batch cannot outlive its source Rights deadline")
        self._validate_edit_authority()

    def _validate_edit_authority(self) -> None:
        edit_asset_ids = (
            self.edit_source_asset_version_id,
            self.edit_mask_asset_version_id,
        )
        if self.operation_kind is OperationKind.IMAGE_GENERATION:
            if any(value is not None for value in edit_asset_ids) or self.approved_repair_scope:
                raise ValueError("image generation cannot carry edit authority")
            return
        if any(value is None for value in edit_asset_ids):
            raise ValueError("image editing requires exact source and mask Asset Versions")
        source_id = self.edit_source_asset_version_id
        mask_id = self.edit_mask_asset_version_id
        if source_id == mask_id:
            raise ValueError("image editing source and mask Asset Versions must differ")
        if source_id not in self.authorized_asset_version_ids or mask_id not in (
            self.authorized_asset_version_ids
        ):
            raise ValueError("image editing source and mask must be authorized")
        if (
            not isinstance(self.approved_repair_scope, tuple)
            or not 1 <= len(self.approved_repair_scope) <= _MAX_REPAIR_SCOPES
            or len(set(self.approved_repair_scope)) != len(self.approved_repair_scope)
        ):
            raise ValueError("approved repair scope is invalid")
        for scope in self.approved_repair_scope:
            _validate_token(scope, "approved repair scope")

    @property
    def batch_sha256(self) -> str:
        return _canonical_sha256(
            {
                "schema_version": "generation-batch.v1",
                "id": self.id,
                "workspace_id": self.workspace_id,
                "workflow_id": self.workflow_id,
                "workflow_version": self.workflow_version,
                "creative_plan_version_id": self.creative_plan_version_id,
                "plan_approval_id": self.plan_approval_id,
                "direction_key": self.direction_key,
                "tool_intent_key": self.tool_intent_key,
                "tool_intent_sha256": self.tool_intent_sha256,
                "prompt_sha256": self.prompt_sha256,
                "context_sha256": self.context_sha256,
                "route_decision_sha256": self.route_decision_sha256,
                "route_request_sha256": self.route_request_sha256,
                "operation_kind": self.operation_kind.value,
                "authorized_asset_version_ids": list(self.authorized_asset_version_ids),
                "candidate_count": self.candidate_count,
                "route_policy_version": self.route_policy_version,
                "tool_policy_version": self.tool_policy_version,
                "rights_policy_version": self.rights_policy_version,
                "safety_policy_version": self.safety_policy_version,
                "workflow_deadline": _canonical_datetime(self.workflow_deadline),
                "source_rights_deadline": (
                    _canonical_datetime(self.source_rights_deadline)
                    if self.source_rights_deadline is not None
                    else None
                ),
                "edit_source_asset_version_id": self.edit_source_asset_version_id,
                "edit_mask_asset_version_id": self.edit_mask_asset_version_id,
                "approved_repair_scope": list(self.approved_repair_scope),
                "retention_deadline": _canonical_datetime(self.retention_deadline),
                "created_by": self.created_by,
                "created_at": _canonical_datetime(self.created_at),
            }
        )


@dataclass(frozen=True, slots=True)
class CandidateSlot:
    id: str
    workspace_id: str
    generation_batch_id: str
    candidate_index: int
    durable_operation_id: str
    operation_kind: OperationKind
    logical_identity_sha256: str
    operation_idempotency_key: str

    def __post_init__(self) -> None:
        _validate_uuid(self.id, "Candidate Slot id")
        validate_workspace_id(self.workspace_id)
        _validate_uuid(self.generation_batch_id, "Generation Batch id")
        if (
            not isinstance(self.candidate_index, int)
            or isinstance(self.candidate_index, bool)
            or not 0 <= self.candidate_index < _MAX_CANDIDATES
        ):
            raise ValueError("Candidate Slot index is invalid")
        _validate_uuid(self.durable_operation_id, "Durable Operation id")
        object.__setattr__(self, "operation_kind", OperationKind(self.operation_kind))
        if self.operation_kind not in _GENERATION_OPERATION_KINDS:
            raise ValueError("Candidate Slot operation kind is invalid")
        _validate_sha256(self.logical_identity_sha256, "Candidate Slot logical identity")
        _validate_token(self.operation_idempotency_key, "Operation idempotency key")


@dataclass(frozen=True, slots=True)
class ImageGenerationRequest:
    candidate_slot_id: str
    prompt_sha256: str
    context_sha256: str
    reference_asset_version_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_uuid(self.candidate_slot_id, "Candidate Slot id")
        _validate_sha256(self.prompt_sha256, "Prompt hash")
        _validate_sha256(self.context_sha256, "Context hash")
        if (
            not isinstance(self.reference_asset_version_ids, tuple)
            or len(self.reference_asset_version_ids) > _MAX_REFERENCE_ASSETS
            or len(set(self.reference_asset_version_ids)) != len(self.reference_asset_version_ids)
        ):
            raise ValueError("reference Asset Version identities are invalid")
        for asset_version_id in self.reference_asset_version_ids:
            _validate_uuid(asset_version_id, "reference Asset Version id")

    @property
    def operation_kind(self) -> OperationKind:
        return OperationKind.IMAGE_GENERATION

    @property
    def request_sha256(self) -> str:
        return _canonical_sha256(
            {
                "schema_version": "image-generation-request.v1",
                "candidate_slot_id": self.candidate_slot_id,
                "prompt_sha256": self.prompt_sha256,
                "context_sha256": self.context_sha256,
                "reference_asset_version_ids": list(self.reference_asset_version_ids),
            }
        )


@dataclass(frozen=True, slots=True)
class ImageEditingRequest:
    candidate_slot_id: str
    prompt_sha256: str
    context_sha256: str
    source_asset_version_id: str
    mask_asset_version_id: str
    approved_repair_scope: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_uuid(self.candidate_slot_id, "Candidate Slot id")
        _validate_sha256(self.prompt_sha256, "Prompt hash")
        _validate_sha256(self.context_sha256, "Context hash")
        _validate_uuid(self.source_asset_version_id, "source Asset Version id")
        _validate_uuid(self.mask_asset_version_id, "mask Asset Version id")
        if self.source_asset_version_id == self.mask_asset_version_id:
            raise ValueError("source and mask Asset Version identities must differ")
        if (
            not isinstance(self.approved_repair_scope, tuple)
            or not 1 <= len(self.approved_repair_scope) <= _MAX_REPAIR_SCOPES
            or len(set(self.approved_repair_scope)) != len(self.approved_repair_scope)
        ):
            raise ValueError("approved repair scope is invalid")
        for scope in self.approved_repair_scope:
            _validate_token(scope, "approved repair scope")

    @property
    def operation_kind(self) -> OperationKind:
        return OperationKind.IMAGE_EDITING

    @property
    def request_sha256(self) -> str:
        return _canonical_sha256(
            {
                "schema_version": "image-editing-request.v1",
                "candidate_slot_id": self.candidate_slot_id,
                "prompt_sha256": self.prompt_sha256,
                "context_sha256": self.context_sha256,
                "source_asset_version_id": self.source_asset_version_id,
                "mask_asset_version_id": self.mask_asset_version_id,
                "approved_repair_scope": list(self.approved_repair_scope),
            }
        )


CandidateRequest = ImageGenerationRequest | ImageEditingRequest


def validate_candidate_request_authority(
    *,
    batch: GenerationBatch,
    slot: CandidateSlot,
    request: CandidateRequest,
) -> None:
    """Reject any request that diverges from the batch's approved authority."""

    if not isinstance(batch, GenerationBatch) or not isinstance(slot, CandidateSlot):
        raise ValueError("Candidate request authority binding is invalid")
    if slot.generation_batch_id != batch.id or slot.workspace_id != batch.workspace_id:
        raise ValueError("Candidate Slot does not belong to the Generation Batch")
    if request.candidate_slot_id != slot.id:
        raise ValueError("Candidate request does not belong to the Candidate Slot")
    if request.operation_kind is not slot.operation_kind:
        raise ValueError("Candidate request operation kind does not match its slot")
    if request.prompt_sha256 != batch.prompt_sha256 or request.context_sha256 != (
        batch.context_sha256
    ):
        raise ValueError("Candidate request prompt or context is not authorized")
    if isinstance(request, ImageGenerationRequest):
        if request.reference_asset_version_ids != batch.authorized_asset_version_ids:
            raise ValueError("Candidate generation references are not authorized")
        return
    if (
        request.source_asset_version_id != batch.edit_source_asset_version_id
        or request.mask_asset_version_id != batch.edit_mask_asset_version_id
    ):
        raise ValueError("Candidate editing source or mask is not authorized")
    if request.approved_repair_scope != batch.approved_repair_scope:
        raise ValueError("Candidate editing repair scope is not authorized")


@dataclass(frozen=True, slots=True)
class CandidateImage:
    """Available candidate fact backed by one controlled Task Asset Version."""

    id: str
    workspace_id: str
    workflow_id: str
    generation_batch_id: str
    candidate_slot_id: str
    task_asset_version_id: str
    content_sha256: str
    width: int
    height: int
    image_format: str
    source_asset_version_ids: tuple[str, ...]
    creative_plan_version_id: str
    prompt_sha256: str
    context_sha256: str
    retrieval_snapshot_sha256: str
    endpoint_capability_version_id: str
    provider_call_id: str
    provider_request_id_sha256: str
    moderation_decision_sha256: str
    usage_record_id: str
    created_at: datetime
    retention_deadline: datetime

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.id, "Candidate Image id"),
            (self.workflow_id, "Workflow id"),
            (self.generation_batch_id, "Generation Batch id"),
            (self.candidate_slot_id, "Candidate Slot id"),
            (self.task_asset_version_id, "Task Asset Version id"),
            (self.creative_plan_version_id, "Creative Plan Version id"),
            (self.endpoint_capability_version_id, "Endpoint Capability Version id"),
            (self.provider_call_id, "Provider Call id"),
            (self.usage_record_id, "Usage Record id"),
        ):
            _validate_uuid(value, field_name)
        validate_workspace_id(self.workspace_id)
        for value, field_name in (
            (self.content_sha256, "Candidate Image content hash"),
            (self.prompt_sha256, "Prompt hash"),
            (self.context_sha256, "Context hash"),
            (self.retrieval_snapshot_sha256, "Retrieval Snapshot hash"),
            (self.provider_request_id_sha256, "Provider Request id hash"),
            (self.moderation_decision_sha256, "Moderation Decision hash"),
        ):
            _validate_sha256(value, field_name)
        _validate_positive_integer(self.width, "Candidate Image width", maximum=32_768)
        _validate_positive_integer(self.height, "Candidate Image height", maximum=32_768)
        _validate_token(self.image_format, "Candidate Image format")
        if (
            not isinstance(self.source_asset_version_ids, tuple)
            or len(self.source_asset_version_ids) > _MAX_REFERENCE_ASSETS
            or len(set(self.source_asset_version_ids)) != len(self.source_asset_version_ids)
        ):
            raise ValueError("source Asset Version identities are invalid")
        for asset_version_id in self.source_asset_version_ids:
            _validate_uuid(asset_version_id, "source Asset Version id")
        if self.task_asset_version_id in self.source_asset_version_ids:
            raise ValueError("Candidate Image output cannot be reused as a source")
        _validate_utc(self.created_at, "Candidate Image created_at")
        _validate_utc(self.retention_deadline, "Candidate Image retention deadline")
        if not self.created_at < self.retention_deadline <= self.created_at + _MAX_RETENTION:
            raise ValueError("Candidate Image retention deadline is invalid")


def create_candidate_slots(
    *,
    batch: GenerationBatch,
    durable_operation_ids: tuple[str, ...],
) -> tuple[CandidateSlot, ...]:
    """Create one deterministic slot identity per exact candidate index."""

    if not isinstance(batch, GenerationBatch):
        raise ValueError("Generation Batch is invalid")
    if (
        not isinstance(durable_operation_ids, tuple)
        or len(durable_operation_ids) != batch.candidate_count
        or len(set(durable_operation_ids)) != len(durable_operation_ids)
    ):
        raise ValueError("Durable Operation identities must match the candidate count")
    for operation_id in durable_operation_ids:
        _validate_uuid(operation_id, "Durable Operation id")

    slots: list[CandidateSlot] = []
    for candidate_index, operation_id in enumerate(durable_operation_ids):
        logical_identity = _canonical_sha256(
            {
                "schema_version": "candidate-slot-identity.v1",
                "generation_batch_sha256": batch.batch_sha256,
                "candidate_index": candidate_index,
            }
        )
        slot_id = str(
            uuid5(
                UUID(batch.id),
                f"candidate-slot:{logical_identity}",
            )
        )
        slots.append(
            CandidateSlot(
                id=slot_id,
                workspace_id=batch.workspace_id,
                generation_batch_id=batch.id,
                candidate_index=candidate_index,
                durable_operation_id=operation_id,
                operation_kind=batch.operation_kind,
                logical_identity_sha256=logical_identity,
                operation_idempotency_key=f"generation-slot:{logical_identity}",
            )
        )
    return tuple(slots)
