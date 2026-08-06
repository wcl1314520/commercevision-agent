"""Atomic command that materializes one exact approved generation intent."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid5

from commercevision_contracts.events import (
    EventType,
    GenerationCandidateRequestedPayload,
)
from commercevision_domain import (
    CandidateSlot,
    GenerationBatch,
    NotFoundError,
    OperationKind,
    create_candidate_slots,
    new_uuid7,
    validate_workspace_id,
)
from commercevision_domain.messaging import EventEnvelope, OutboxEvent
from commercevision_domain.operations import DurableOperation
from commercevision_domain.workflow.errors import (
    ConcurrencyError,
    IdempotencyConflictError,
    UniqueConstraintError,
)

from .asset_idempotency import canonical_hash, key_hash, workspace_hash
from .generation_command_ports import (
    ApprovedGenerationUnitOfWorkFactory,
    ApprovedGenerationUnitOfWorkPort,
)

_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)
_TRACE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$", re.ASCII)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_IDEMPOTENCY_TTL = timedelta(days=30)
_AUDIT_TTL = timedelta(days=3650)


def _uuid(value: str, name: str) -> str:
    try:
        canonical = str(UUID(value))
    except (TypeError, ValueError, AttributeError):
        raise ValueError(f"{name} must be a canonical UUID") from None
    if canonical != value:
        raise ValueError(f"{name} must be a canonical UUID")
    return value


def _token(value: str, name: str) -> str:
    if not isinstance(value, str) or _TOKEN_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{name} is invalid")
    return value


def _sha256(value: str, name: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{name} must be lowercase SHA-256")
    return value


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be timezone-aware UTC")
    return value


@dataclass(frozen=True, slots=True)
class ApprovedPlanGenerationCommand:
    workspace_id: str
    workflow_id: str
    expected_workflow_version: int
    creative_plan_id: str
    creative_plan_version_id: str
    creative_plan_version: int
    approval_id: str
    direction_key: str
    tool_intent_key: str
    route_decision_sha256: str

    def __post_init__(self) -> None:
        validate_workspace_id(self.workspace_id)
        for value, name in (
            (self.workflow_id, "Workflow id"),
            (self.creative_plan_id, "Creative Plan id"),
            (self.creative_plan_version_id, "Creative Plan Version id"),
            (self.approval_id, "Approval id"),
        ):
            _uuid(value, name)
        for value, name in (
            (self.direction_key, "Direction key"),
            (self.tool_intent_key, "Tool Intent key"),
        ):
            _token(value, name)
        _sha256(self.route_decision_sha256, "Route Decision hash")
        if self.expected_workflow_version < 1 or self.creative_plan_version < 1:
            raise ValueError("Generation command versions must be positive")

    def canonical_data(self) -> dict[str, object]:
        return {
            "workspace_id": self.workspace_id,
            "workflow_id": self.workflow_id,
            "expected_workflow_version": self.expected_workflow_version,
            "creative_plan_id": self.creative_plan_id,
            "creative_plan_version_id": self.creative_plan_version_id,
            "creative_plan_version": self.creative_plan_version,
            "approval_id": self.approval_id,
            "direction_key": self.direction_key,
            "tool_intent_key": self.tool_intent_key,
            "route_decision_sha256": self.route_decision_sha256,
        }


@dataclass(frozen=True, slots=True)
class ApprovedGenerationAuthority:
    workspace_id: str
    workflow_id: str
    workflow_version: int
    creative_plan_id: str
    creative_plan_version_id: str
    creative_plan_version: int
    approval_id: str
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
    retention_deadline: datetime
    created_by: str

    def __post_init__(self) -> None:
        validate_workspace_id(self.workspace_id)
        for value, name in (
            (self.workflow_id, "Workflow id"),
            (self.creative_plan_id, "Creative Plan id"),
            (self.creative_plan_version_id, "Creative Plan Version id"),
            (self.approval_id, "Approval id"),
        ):
            _uuid(value, name)
        for value, name in (
            (self.tool_intent_sha256, "Tool Intent hash"),
            (self.prompt_sha256, "Prompt hash"),
            (self.context_sha256, "Context hash"),
            (self.route_decision_sha256, "Route Decision hash"),
            (self.route_request_sha256, "Route Request hash"),
        ):
            _sha256(value, name)
        for value, name in (
            (self.direction_key, "Direction key"),
            (self.tool_intent_key, "Tool Intent key"),
            (self.route_policy_version, "Route Policy version"),
            (self.tool_policy_version, "Tool Policy version"),
            (self.rights_policy_version, "Rights Policy version"),
            (self.safety_policy_version, "Safety Policy version"),
            (self.created_by, "Generation actor"),
        ):
            _token(value, name)
        object.__setattr__(self, "operation_kind", OperationKind(self.operation_kind))
        if self.operation_kind is not OperationKind.IMAGE_GENERATION:
            raise ValueError("approved-plan generation requires IMAGE_GENERATION")
        if not 1 <= self.candidate_count <= 16:
            raise ValueError("Generation candidate count must be between 1 and 16")
        if self.workflow_version < 1 or self.creative_plan_version < 1:
            raise ValueError("Generation authority versions must be positive")
        if not isinstance(self.authorized_asset_version_ids, tuple) or len(
            set(self.authorized_asset_version_ids)
        ) != len(self.authorized_asset_version_ids):
            raise ValueError("authorized Asset Version identities are invalid")
        for asset_id in self.authorized_asset_version_ids:
            _uuid(asset_id, "authorized Asset Version id")
        _utc(self.workflow_deadline, "Workflow deadline")
        _utc(self.retention_deadline, "retention deadline")
        if self.source_rights_deadline is not None:
            _utc(self.source_rights_deadline, "source Rights deadline")

    def assert_matches(self, command: ApprovedPlanGenerationCommand) -> None:
        expected = (
            command.workspace_id,
            command.workflow_id,
            command.expected_workflow_version,
            command.creative_plan_id,
            command.creative_plan_version_id,
            command.creative_plan_version,
            command.approval_id,
            command.direction_key,
            command.tool_intent_key,
            command.route_decision_sha256,
        )
        actual = (
            self.workspace_id,
            self.workflow_id,
            self.workflow_version,
            self.creative_plan_id,
            self.creative_plan_version_id,
            self.creative_plan_version,
            self.approval_id,
            self.direction_key,
            self.tool_intent_key,
            self.route_decision_sha256,
        )
        if actual != expected:
            raise ConcurrencyError("generation authority does not match the exact approved intent")


@dataclass(frozen=True, slots=True)
class ApprovedPlanGenerationResult:
    batch: GenerationBatch
    slots: tuple[CandidateSlot, ...]
    operations: tuple[DurableOperation, ...]
    replayed: bool


class ApprovedPlanGenerationService:
    def __init__(self, uow_factory: ApprovedGenerationUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def start(
        self,
        *,
        command: ApprovedPlanGenerationCommand,
        idempotency_key: str,
        trace_id: str,
    ) -> ApprovedPlanGenerationResult:
        try:
            return self._start_once(
                command=command,
                idempotency_key=idempotency_key,
                trace_id=trace_id,
            )
        except UniqueConstraintError as conflict:
            return self._converge_logical_winner(
                command=command,
                idempotency_key=idempotency_key,
                conflict=conflict,
            )

    def get(
        self,
        *,
        workspace_id: str,
        batch_id: str,
    ) -> ApprovedPlanGenerationResult:
        validate_workspace_id(workspace_id)
        _uuid(batch_id, "Generation Batch id")
        with self._uow_factory() as uow:
            batch = uow.generation_batches.get(batch_id, workspace_id=workspace_id)
            if batch is None:
                raise NotFoundError("Generation Batch was not found")
            return self._load_replay(
                uow=uow,
                workspace_id=workspace_id,
                batch_id=batch_id,
                batch=batch,
            )

    def _start_once(
        self,
        *,
        command: ApprovedPlanGenerationCommand,
        idempotency_key: str,
        trace_id: str,
    ) -> ApprovedPlanGenerationResult:
        if not isinstance(command, ApprovedPlanGenerationCommand):
            raise ValueError("approved-plan generation command is invalid")
        if not isinstance(trace_id, str) or _TRACE_PATTERN.fullmatch(trace_id) is None:
            raise ValueError("Generation trace is invalid")
        scope = "generation:" + canonical_hash(
            {
                "workspace_sha256": workspace_hash(command.workspace_id),
                "workflow_id": command.workflow_id,
                "creative_plan_version_id": command.creative_plan_version_id,
                "tool_intent_key": command.tool_intent_key,
            }
        )
        request_hash = canonical_hash(command.canonical_data())
        with self._uow_factory() as uow:
            now = uow.database_now()
            claim = uow.idempotency.claim(
                scope=scope,
                key_hash=key_hash(idempotency_key),
                request_hash=request_hash,
                expires_at=now + _IDEMPOTENCY_TTL,
            )
            if claim.request_hash != request_hash:
                raise IdempotencyConflictError(
                    "idempotency key was already used with a different generation command"
                )
            if claim.status == "COMPLETED":
                if claim.resource_type != "generation-batch" or not claim.resource_id:
                    raise ConcurrencyError(
                        "generation idempotency record does not identify its batch"
                    )
                return self._load_replay(
                    uow=uow,
                    workspace_id=command.workspace_id,
                    batch_id=claim.resource_id,
                )
            if claim.status != "PENDING":
                raise ConcurrencyError("generation command idempotency record is not pending")
            existing_batch = uow.generation_batches.get_by_logical_identity(
                workspace_id=command.workspace_id,
                workflow_id=command.workflow_id,
                workflow_version=command.expected_workflow_version,
                creative_plan_version_id=command.creative_plan_version_id,
                direction_key=command.direction_key,
                tool_intent_key=command.tool_intent_key,
            )
            if existing_batch is not None:
                if existing_batch.route_decision_sha256 != command.route_decision_sha256:
                    raise ConcurrencyError(
                        "generation intent already uses a different Route Decision"
                    )
                result = self._load_replay(
                    uow=uow,
                    workspace_id=command.workspace_id,
                    batch_id=existing_batch.id,
                )
                uow.idempotency.complete(
                    scope=scope,
                    key_hash=key_hash(idempotency_key),
                    request_hash=request_hash,
                    resource_type="generation-batch",
                    resource_id=existing_batch.id,
                    response_data={"generation_batch_id": existing_batch.id},
                )
                uow.commit()
                return result
            authority = uow.generation_authority.load_current_authority(command)
            if not isinstance(authority, ApprovedGenerationAuthority):
                raise ConcurrencyError("generation authority returned an invalid snapshot")
            authority.assert_matches(command)
            batch = GenerationBatch(
                id=new_uuid7(),
                workspace_id=authority.workspace_id,
                workflow_id=authority.workflow_id,
                workflow_version=authority.workflow_version,
                creative_plan_version_id=authority.creative_plan_version_id,
                plan_approval_id=authority.approval_id,
                direction_key=authority.direction_key,
                tool_intent_key=authority.tool_intent_key,
                tool_intent_sha256=authority.tool_intent_sha256,
                prompt_sha256=authority.prompt_sha256,
                context_sha256=authority.context_sha256,
                route_decision_sha256=authority.route_decision_sha256,
                route_request_sha256=authority.route_request_sha256,
                operation_kind=authority.operation_kind,
                authorized_asset_version_ids=authority.authorized_asset_version_ids,
                candidate_count=authority.candidate_count,
                route_policy_version=authority.route_policy_version,
                tool_policy_version=authority.tool_policy_version,
                rights_policy_version=authority.rights_policy_version,
                safety_policy_version=authority.safety_policy_version,
                workflow_deadline=authority.workflow_deadline,
                source_rights_deadline=authority.source_rights_deadline,
                edit_source_asset_version_id=None,
                edit_mask_asset_version_id=None,
                approved_repair_scope=(),
                retention_deadline=authority.retention_deadline,
                created_by=authority.created_by,
                created_at=now,
            )
            provisional_operation_ids = tuple(
                str(uuid5(UUID(batch.id), f"candidate-operation:{index}"))
                for index in range(batch.candidate_count)
            )
            provisional_slots = create_candidate_slots(
                batch=batch,
                durable_operation_ids=provisional_operation_ids,
            )
            execution_window = batch.retention_deadline - now
            operations = tuple(
                DurableOperation.create(
                    workspace_id=batch.workspace_id,
                    kind=batch.operation_kind,
                    target_type="generation-candidate-slot",
                    target_id=slot.id,
                    target_version=1,
                    input_hash=slot.logical_identity_sha256,
                    max_attempts=3,
                    max_reconciliation_attempts=8,
                    execution_max_elapsed=execution_window,
                    now=now,
                )
                for slot in provisional_slots
            )
            slots = create_candidate_slots(
                batch=batch,
                durable_operation_ids=tuple(operation.id for operation in operations),
            )
            uow.generation_batches.add(batch)
            for operation in operations:
                uow.operations.add(operation)
            uow.flush()
            for slot, operation in zip(slots, operations, strict=True):
                uow.candidate_slots.add(slot)
                uow.outbox.add(
                    OutboxEvent(
                        envelope=EventEnvelope.create(
                            event_type=EventType.GENERATION_CANDIDATE_REQUESTED.value,
                            aggregate_type="generation-batch",
                            aggregate_id=batch.id,
                            aggregate_version=1,
                            trace_id=trace_id,
                            payload=GenerationCandidateRequestedPayload(
                                workspace_id=batch.workspace_id,
                                workflow_id=batch.workflow_id,
                                generation_batch_id=batch.id,
                                candidate_slot_id=slot.id,
                                operation_id=operation.id,
                                operation_kind=OperationKind.IMAGE_GENERATION,
                            ).model_dump(mode="json"),
                            now=now,
                        ),
                        available_at=now,
                        workspace_id=batch.workspace_id,
                    )
                )
            uow.idempotency.complete(
                scope=scope,
                key_hash=key_hash(idempotency_key),
                request_hash=request_hash,
                resource_type="generation-batch",
                resource_id=batch.id,
                response_data={"generation_batch_id": batch.id},
            )
            uow.audit.add(
                workspace_id=batch.workspace_id,
                actor_type="SERVICE",
                actor_id=batch.created_by,
                action="generation-batch.created",
                resource_type="generation-batch",
                resource_id=batch.id,
                trace_id=trace_id,
                metadata={
                    "workflow_id": batch.workflow_id,
                    "creative_plan_version_id": batch.creative_plan_version_id,
                    "approval_id": batch.plan_approval_id,
                    "direction_key": batch.direction_key,
                    "tool_intent_key": batch.tool_intent_key,
                    "route_decision_sha256": batch.route_decision_sha256,
                    "candidate_count": batch.candidate_count,
                    "operation_kind": batch.operation_kind.value,
                },
                created_at=now,
                expires_at=min(batch.retention_deadline, now + _AUDIT_TTL),
            )
            uow.commit()
            return ApprovedPlanGenerationResult(
                batch=batch,
                slots=slots,
                operations=operations,
                replayed=False,
            )

    def _converge_logical_winner(
        self,
        *,
        command: ApprovedPlanGenerationCommand,
        idempotency_key: str,
        conflict: UniqueConstraintError,
    ) -> ApprovedPlanGenerationResult:
        scope = "generation:" + canonical_hash(
            {
                "workspace_sha256": workspace_hash(command.workspace_id),
                "workflow_id": command.workflow_id,
                "creative_plan_version_id": command.creative_plan_version_id,
                "tool_intent_key": command.tool_intent_key,
            }
        )
        request_hash = canonical_hash(command.canonical_data())
        with self._uow_factory() as uow:
            now = uow.database_now()
            claim = uow.idempotency.claim(
                scope=scope,
                key_hash=key_hash(idempotency_key),
                request_hash=request_hash,
                expires_at=now + _IDEMPOTENCY_TTL,
            )
            if claim.request_hash != request_hash:
                raise IdempotencyConflictError(
                    "idempotency key was already used with a different generation command"
                )
            if claim.status == "COMPLETED":
                if claim.resource_type != "generation-batch" or not claim.resource_id:
                    raise ConcurrencyError(
                        "generation idempotency record does not identify its batch"
                    )
                return self._load_replay(
                    uow=uow,
                    workspace_id=command.workspace_id,
                    batch_id=claim.resource_id,
                )
            if claim.status != "PENDING":
                raise ConcurrencyError("generation command idempotency record is not pending")
            batch = uow.generation_batches.get_by_logical_identity(
                workspace_id=command.workspace_id,
                workflow_id=command.workflow_id,
                workflow_version=command.expected_workflow_version,
                creative_plan_version_id=command.creative_plan_version_id,
                direction_key=command.direction_key,
                tool_intent_key=command.tool_intent_key,
            )
            if batch is None:
                raise conflict
            if batch.route_decision_sha256 != command.route_decision_sha256:
                raise ConcurrencyError("generation intent already uses a different Route Decision")
            result = self._load_replay(
                uow=uow,
                workspace_id=command.workspace_id,
                batch_id=batch.id,
            )
            uow.idempotency.complete(
                scope=scope,
                key_hash=key_hash(idempotency_key),
                request_hash=request_hash,
                resource_type="generation-batch",
                resource_id=batch.id,
                response_data={"generation_batch_id": batch.id},
            )
            uow.commit()
            return result

    @staticmethod
    def _load_replay(
        *,
        uow: ApprovedGenerationUnitOfWorkPort,
        workspace_id: str,
        batch_id: str,
        batch: GenerationBatch | None = None,
    ) -> ApprovedPlanGenerationResult:
        batch = batch or uow.generation_batches.get(batch_id, workspace_id=workspace_id)
        if batch is None:
            raise ConcurrencyError("generation idempotency batch is missing")
        slots = tuple(
            sorted(
                uow.candidate_slots.list_for_batch(
                    workspace_id=workspace_id,
                    generation_batch_id=batch.id,
                ),
                key=lambda item: item.candidate_index,
            )
        )
        if len(slots) != batch.candidate_count or tuple(
            item.candidate_index for item in slots
        ) != tuple(range(batch.candidate_count)):
            raise ConcurrencyError("persisted Candidate Slots do not match their batch")
        operations: list[DurableOperation] = []
        for slot in slots:
            operation = uow.operations.get(
                slot.durable_operation_id,
                workspace_id=workspace_id,
            )
            if (
                operation is None
                or operation.kind is not batch.operation_kind
                or operation.target_type != "generation-candidate-slot"
                or operation.target_id != slot.id
                or operation.target_version != 1
                or operation.input_hash != slot.logical_identity_sha256
            ):
                raise ConcurrencyError(
                    "persisted Durable Operation does not match its Candidate Slot"
                )
            operations.append(operation)
        if (
            create_candidate_slots(
                batch=batch,
                durable_operation_ids=tuple(item.id for item in operations),
            )
            != slots
        ):
            raise ConcurrencyError("persisted Candidate Slot identity is invalid")
        return ApprovedPlanGenerationResult(
            batch=batch,
            slots=slots,
            operations=tuple(operations),
            replayed=True,
        )
