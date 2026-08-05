"""Workspace-scoped Creative Plan reads and versioned commands."""

from __future__ import annotations

from typing import Annotated, Any, cast

from commercevision_application import AuthenticatedPrincipal, CreativePlanWriteResult
from commercevision_contracts import (
    CreativePlanCreateRequestV1,
    CreativePlanCurrentResponseV1,
    CreativePlanHeadResponseV1,
    CreativePlanPayloadV1,
    CreativePlanProvenanceV1,
    CreativePlanRevisionRequestV1,
    CreativePlanVersionListResponseV1,
    CreativePlanVersionResponseV1,
    ErrorResponse,
)
from commercevision_domain import AuthenticationError, CreativePlanVersion, canonicalize_uuid
from fastapi import APIRouter, Header, Path, Query, Request, status
from pydantic import AfterValidator

from .workspace_identity import WorkspaceHeader

router = APIRouter(prefix="/api/v1/creative-plans", tags=["creative-plans"])

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


CreativePlanId = Annotated[
    str,
    Path(pattern=CANONICAL_UUID_PATTERN),
    AfterValidator(_canonical_uuid),
]
WorkflowId = Annotated[
    str,
    Query(pattern=CANONICAL_UUID_PATTERN),
    AfterValidator(_canonical_uuid),
]
VersionNumber = Annotated[int, Path(ge=1, le=1_000_000)]
PageLimit = Annotated[int, Query(ge=1, le=100)]
PageCursor = Annotated[str | None, Query(min_length=1, max_length=256)]

CREATIVE_PLAN_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse, "description": "Invalid Creative Plan argument"},
    401: {"model": ErrorResponse, "description": "Trusted principal required"},
    403: {"model": ErrorResponse, "description": "Workspace membership required"},
    404: {"model": ErrorResponse, "description": "Creative Plan not found"},
    409: {
        "model": ErrorResponse,
        "description": "Creative Plan state, version, or idempotency conflict",
    },
    422: {"model": ErrorResponse, "description": "Creative Plan contract validation failed"},
    500: {"model": ErrorResponse, "description": "Unexpected internal error"},
    503: {"model": ErrorResponse, "description": "Creative Plan dependency unavailable"},
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


def _version_response(version: CreativePlanVersion) -> CreativePlanVersionResponseV1:
    provenance = version.provenance
    return CreativePlanVersionResponseV1(
        id=version.id,
        workspace_id=version.workspace_id,
        workflow_id=version.workflow_id,
        creative_plan_id=version.creative_plan_id,
        version_number=version.version_number,
        supersedes_version_id=version.supersedes_version_id,
        source=version.source,
        payload=CreativePlanPayloadV1.model_validate(version.payload.to_canonical_data()),
        provenance=CreativePlanProvenanceV1(
            product_brief_id=provenance.product_brief_id,
            product_brief_version=provenance.product_brief_version,
            product_brief_sha256=provenance.product_brief_sha256,
            brand_profile_id=provenance.brand_profile_id,
            brand_profile_version=provenance.brand_profile_version,
            brand_profile_sha256=provenance.brand_profile_sha256,
            retrieval_run_id=provenance.retrieval_run_id,
            retrieval_citation_ids=list(provenance.retrieval_citation_ids),
            context_policy_version=provenance.context_policy_version,
            context_sha256=provenance.context_sha256,
            prompt_id=provenance.prompt_id,
            prompt_revision=provenance.prompt_revision,
            prompt_sha256=provenance.prompt_sha256,
        ),
        payload_sha256=version.payload_sha256,
        actor_id=version.actor_id,
        revision_reason=version.revision_reason,
        created_at=version.created_at,
    )


def _current_response(result: CreativePlanWriteResult) -> CreativePlanCurrentResponseV1:
    return CreativePlanCurrentResponseV1(
        head=CreativePlanHeadResponseV1(
            workspace_id=result.head.workspace_id,
            workflow_id=result.head.workflow_id,
            creative_plan_id=result.head.creative_plan_id,
            current_version_id=result.head.current_version_id,
            current_version_number=result.head.current_version_number,
            version=result.head.version,
            retain_until=result.head.retain_until,
            created_at=result.head.created_at,
            updated_at=result.head.updated_at,
        ),
        version=_version_response(result.version),
    )


@router.post(
    "",
    response_model=CreativePlanCurrentResponseV1,
    status_code=status.HTTP_201_CREATED,
    responses=CREATIVE_PLAN_ERROR_RESPONSES,
)
def create_creative_plan(
    payload: CreativePlanCreateRequestV1,
    request: Request,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    idempotency_key: IdempotencyHeader,
    trusted_principal: PrincipalHeader = None,
) -> CreativePlanCurrentResponseV1:
    principal = _command_principal(
        request,
        workspace_id=workspace_id,
        actor_id=actor_id,
        trusted_principal=trusted_principal,
    )
    result = request.app.state.container.creative_plans.create_plan(
        workspace_id=workspace_id,
        actor_id=principal.actor_id,
        request=payload,
        trace_id=request.state.trace_id,
        idempotency_key=idempotency_key,
    )
    return _current_response(cast(CreativePlanWriteResult, result))


@router.post(
    "/{creative_plan_id}:revise",
    response_model=CreativePlanCurrentResponseV1,
    status_code=status.HTTP_201_CREATED,
    responses=CREATIVE_PLAN_ERROR_RESPONSES,
)
def revise_creative_plan(
    creative_plan_id: CreativePlanId,
    payload: CreativePlanRevisionRequestV1,
    request: Request,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    idempotency_key: IdempotencyHeader,
    trusted_principal: PrincipalHeader = None,
) -> CreativePlanCurrentResponseV1:
    principal = _command_principal(
        request,
        workspace_id=workspace_id,
        actor_id=actor_id,
        trusted_principal=trusted_principal,
    )
    result = request.app.state.container.creative_plans.revise_plan(
        workspace_id=workspace_id,
        creative_plan_id=creative_plan_id,
        actor_id=principal.actor_id,
        request=payload,
        trace_id=request.state.trace_id,
        idempotency_key=idempotency_key,
    )
    return _current_response(cast(CreativePlanWriteResult, result))


@router.get(
    "/{creative_plan_id}/versions",
    response_model=CreativePlanVersionListResponseV1,
    responses=CREATIVE_PLAN_ERROR_RESPONSES,
)
def list_creative_plan_versions(
    creative_plan_id: CreativePlanId,
    request: Request,
    workspace_id: WorkspaceHeader,
    workflow_id: WorkflowId,
    limit: PageLimit = 50,
    cursor: PageCursor = None,
    trusted_principal: PrincipalHeader = None,
) -> CreativePlanVersionListResponseV1:
    _require_workspace(
        request,
        workspace_id=workspace_id,
        trusted_principal=trusted_principal,
    )
    page = request.app.state.container.creative_plans.list_version_page(
        workspace_id=workspace_id,
        workflow_id=workflow_id,
        creative_plan_id=creative_plan_id,
        limit=limit,
        cursor=cursor,
    )
    return CreativePlanVersionListResponseV1(
        items=[_version_response(version) for version in page.items],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/{creative_plan_id}/versions/{version_number}",
    response_model=CreativePlanVersionResponseV1,
    responses=CREATIVE_PLAN_ERROR_RESPONSES,
)
def get_creative_plan_version(
    creative_plan_id: CreativePlanId,
    version_number: VersionNumber,
    request: Request,
    workspace_id: WorkspaceHeader,
    workflow_id: WorkflowId,
    trusted_principal: PrincipalHeader = None,
) -> CreativePlanVersionResponseV1:
    _require_workspace(
        request,
        workspace_id=workspace_id,
        trusted_principal=trusted_principal,
    )
    version = request.app.state.container.creative_plans.get_version(
        workspace_id=workspace_id,
        workflow_id=workflow_id,
        creative_plan_id=creative_plan_id,
        version_number=version_number,
    )
    return _version_response(cast(CreativePlanVersion, version))


@router.get(
    "/{creative_plan_id}",
    response_model=CreativePlanCurrentResponseV1,
    responses=CREATIVE_PLAN_ERROR_RESPONSES,
)
def get_current_creative_plan(
    creative_plan_id: CreativePlanId,
    request: Request,
    workspace_id: WorkspaceHeader,
    workflow_id: WorkflowId,
    trusted_principal: PrincipalHeader = None,
) -> CreativePlanCurrentResponseV1:
    _require_workspace(
        request,
        workspace_id=workspace_id,
        trusted_principal=trusted_principal,
    )
    result = request.app.state.container.creative_plans.get_current(
        workspace_id=workspace_id,
        workflow_id=workflow_id,
        creative_plan_id=creative_plan_id,
    )
    return _current_response(cast(CreativePlanWriteResult, result))
