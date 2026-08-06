"""Workspace-scoped approved-plan generation commands and reads."""

from __future__ import annotations

from typing import Annotated, Any, cast

from commercevision_application import (
    ApprovedPlanGenerationCommand,
    ApprovedPlanGenerationResult,
    AuthenticatedPrincipal,
)
from commercevision_contracts import (
    ApprovedPlanGenerationRequestV1,
    ErrorResponse,
    GenerationBatchResponseV1,
    GenerationCandidateSlotResponseV1,
    GenerationOperationResponseV1,
)
from commercevision_domain import AuthenticationError, canonicalize_uuid
from fastapi import APIRouter, Header, Path, Request, status
from pydantic import AfterValidator

from .workspace_identity import WorkspaceHeader

router = APIRouter(prefix="/api/v1/generation-batches", tags=["generation-batches"])

CANONICAL_UUID_PATTERN = (
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}$"
)
PrincipalHeader = Annotated[
    str | None,
    Header(alias="X-Trusted-Principal", max_length=4096),
]
ActorHeader = Annotated[str, Header(alias="X-Actor-Id", min_length=1, max_length=128)]
IdempotencyHeader = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=8, max_length=255),
]


def _canonical_uuid(value: str) -> str:
    canonical = canonicalize_uuid(value)
    if canonical != value:
        raise ValueError("identifier must be a canonical lowercase UUID")
    return value


GenerationBatchId = Annotated[
    str,
    Path(pattern=CANONICAL_UUID_PATTERN),
    AfterValidator(_canonical_uuid),
]

GENERATION_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse, "description": "Invalid generation argument"},
    401: {"model": ErrorResponse, "description": "Trusted principal required"},
    403: {"model": ErrorResponse, "description": "Generation authority denied"},
    404: {"model": ErrorResponse, "description": "Generation Batch not found"},
    409: {"model": ErrorResponse, "description": "Generation state or version conflict"},
    410: {"model": ErrorResponse, "description": "Generation authority expired"},
    422: {"model": ErrorResponse, "description": "Generation contract validation failed"},
    500: {"model": ErrorResponse, "description": "Unexpected internal error"},
    503: {"model": ErrorResponse, "description": "Generation dependency unavailable"},
}


def _require_workspace(
    request: Request,
    *,
    workspace_id: str,
    trusted_principal: str | None,
) -> AuthenticatedPrincipal:
    principal = request.app.state.container.principal_resolver.resolve(trusted_principal)
    request.app.state.container.access_policy.require_workspace(
        workspace_id=workspace_id,
        principal=principal,
    )
    return cast(AuthenticatedPrincipal, principal)


def _command_principal(
    request: Request,
    *,
    workspace_id: str,
    actor_id: str,
    trusted_principal: str | None,
) -> AuthenticatedPrincipal:
    principal = _require_workspace(
        request,
        workspace_id=workspace_id,
        trusted_principal=trusted_principal,
    )
    if actor_id != principal.actor_id:
        raise AuthenticationError("actor header does not match the trusted principal")
    return principal


def _response(result: ApprovedPlanGenerationResult) -> GenerationBatchResponseV1:
    batch = result.batch
    operations = {operation.id: operation for operation in result.operations}
    return GenerationBatchResponseV1(
        id=batch.id,
        batch_sha256=batch.batch_sha256,
        workspace_id=batch.workspace_id,
        workflow_id=batch.workflow_id,
        workflow_version=batch.workflow_version,
        creative_plan_version_id=batch.creative_plan_version_id,
        plan_approval_id=batch.plan_approval_id,
        direction_key=batch.direction_key,
        tool_intent_key=batch.tool_intent_key,
        tool_intent_sha256=batch.tool_intent_sha256,
        prompt_sha256=batch.prompt_sha256,
        context_sha256=batch.context_sha256,
        route_decision_sha256=batch.route_decision_sha256,
        route_request_sha256=batch.route_request_sha256,
        operation_kind=batch.operation_kind,
        authorized_asset_version_ids=list(batch.authorized_asset_version_ids),
        candidate_count=batch.candidate_count,
        route_policy_version=batch.route_policy_version,
        tool_policy_version=batch.tool_policy_version,
        rights_policy_version=batch.rights_policy_version,
        safety_policy_version=batch.safety_policy_version,
        workflow_deadline=batch.workflow_deadline,
        source_rights_deadline=batch.source_rights_deadline,
        retention_deadline=batch.retention_deadline,
        created_by=batch.created_by,
        created_at=batch.created_at,
        slots=[
            GenerationCandidateSlotResponseV1(
                id=slot.id,
                candidate_index=slot.candidate_index,
                logical_identity_sha256=slot.logical_identity_sha256,
                operation=GenerationOperationResponseV1(
                    id=operations[slot.durable_operation_id].id,
                    kind=operations[slot.durable_operation_id].kind,
                    state=operations[slot.durable_operation_id].state,
                    attempt_count=operations[slot.durable_operation_id].attempt_count,
                    max_attempts=operations[slot.durable_operation_id].max_attempts,
                    execution_deadline_at=(
                        operations[slot.durable_operation_id].execution_deadline_at
                    ),
                    created_at=operations[slot.durable_operation_id].created_at,
                    updated_at=operations[slot.durable_operation_id].updated_at,
                    version=operations[slot.durable_operation_id].version,
                ),
            )
            for slot in result.slots
        ],
    )


@router.post(
    "",
    response_model=GenerationBatchResponseV1,
    status_code=status.HTTP_201_CREATED,
    responses=GENERATION_ERROR_RESPONSES,
)
def start_generation_batch(
    payload: ApprovedPlanGenerationRequestV1,
    request: Request,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    idempotency_key: IdempotencyHeader,
    trusted_principal: PrincipalHeader = None,
) -> GenerationBatchResponseV1:
    _command_principal(
        request,
        workspace_id=workspace_id,
        actor_id=actor_id,
        trusted_principal=trusted_principal,
    )
    result = request.app.state.container.generation.start(
        command=ApprovedPlanGenerationCommand(
            workspace_id=workspace_id,
            **payload.model_dump(),
        ),
        idempotency_key=idempotency_key,
        trace_id=request.state.trace_id,
    )
    return _response(cast(ApprovedPlanGenerationResult, result))


@router.get(
    "/{batch_id}",
    response_model=GenerationBatchResponseV1,
    responses=GENERATION_ERROR_RESPONSES,
)
def get_generation_batch(
    batch_id: GenerationBatchId,
    request: Request,
    workspace_id: WorkspaceHeader,
    trusted_principal: PrincipalHeader = None,
) -> GenerationBatchResponseV1:
    _require_workspace(
        request,
        workspace_id=workspace_id,
        trusted_principal=trusted_principal,
    )
    result = request.app.state.container.generation.get(
        workspace_id=workspace_id,
        batch_id=batch_id,
    )
    return _response(cast(ApprovedPlanGenerationResult, result))
