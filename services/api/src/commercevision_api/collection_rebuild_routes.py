"""Administrator routes for safe Milvus Collection rebuilds."""

from typing import Annotated

from commercevision_contracts import (
    CollectionRebuildActionRequestV1,
    CollectionRebuildRequestV1,
    CollectionRebuildResponseV1,
    ErrorResponse,
)
from commercevision_domain import AuthenticationError, canonicalize_uuid
from fastapi import APIRouter, Header, Path, Request, status
from pydantic import AfterValidator

from .workspace_identity import WorkspaceHeader

router = APIRouter(prefix="/api/v1/collections/rebuilds", tags=["collection-rebuilds"])

ActorHeader = Annotated[str, Header(alias="X-Actor-Id", min_length=1, max_length=128)]
IdempotencyHeader = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=256)]
PrincipalHeader = Annotated[str | None, Header(alias="X-Trusted-Principal", max_length=4096)]


def _canonical_rebuild_id(value: str) -> str:
    canonical = canonicalize_uuid(value)
    if canonical != value:
        raise ValueError("rebuild_id must be a canonical lowercase UUID")
    return value


RebuildId = Annotated[
    str,
    Path(
        pattern=(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
            r"[0-9a-f]{4}-[0-9a-f]{12}$"
        )
    ),
    AfterValidator(_canonical_rebuild_id),
]

ERRORS = {
    400: {"model": ErrorResponse, "description": "Invalid rebuild argument"},
    401: {"model": ErrorResponse, "description": "Trusted principal required"},
    403: {"model": ErrorResponse, "description": "Workspace administrator required"},
    404: {"model": ErrorResponse, "description": "Collection rebuild not found"},
    409: {"model": ErrorResponse, "description": "Version or rebuild state conflict"},
    422: {"model": ErrorResponse, "description": "Rebuild contract validation failed"},
    500: {"model": ErrorResponse, "description": "Unexpected internal error"},
    503: {"model": ErrorResponse, "description": "Rebuild dependency unavailable"},
}


def _require_admin(
    request: Request,
    *,
    workspace_id: str,
    trusted_principal: str | None,
    actor_id: str | None = None,
):
    principal = request.app.state.container.principal_resolver.resolve(trusted_principal)
    if actor_id is not None and actor_id != principal.actor_id:
        raise AuthenticationError("actor header does not match the trusted principal")
    request.app.state.container.access_policy.require_admin(
        workspace_id=workspace_id,
        principal=principal,
    )
    return principal


@router.post(
    "",
    response_model=CollectionRebuildResponseV1,
    status_code=status.HTTP_202_ACCEPTED,
    responses=ERRORS,
)
def request_rebuild(
    payload: CollectionRebuildRequestV1,
    request: Request,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    idempotency_key: IdempotencyHeader,
    trusted_principal: PrincipalHeader = None,
) -> CollectionRebuildResponseV1:
    _require_admin(
        request,
        workspace_id=workspace_id,
        trusted_principal=trusted_principal,
        actor_id=actor_id,
    )
    return request.app.state.container.collection_rebuilds.request(
        workspace_id=workspace_id,
        idempotency_key=idempotency_key,
        trace_id=request.state.trace_id,
        request=payload,
    )


@router.get("/{rebuild_id}", response_model=CollectionRebuildResponseV1, responses=ERRORS)
def get_rebuild(
    request: Request,
    rebuild_id: RebuildId,
    workspace_id: WorkspaceHeader,
    trusted_principal: PrincipalHeader = None,
) -> CollectionRebuildResponseV1:
    _require_admin(
        request,
        workspace_id=workspace_id,
        trusted_principal=trusted_principal,
    )
    return request.app.state.container.collection_rebuilds.get(
        workspace_id=workspace_id,
        rebuild_id=rebuild_id,
    )


@router.post(
    "/{rebuild_id}:validate",
    response_model=CollectionRebuildResponseV1,
    status_code=status.HTTP_202_ACCEPTED,
    responses=ERRORS,
)
def validate_rebuild(
    payload: CollectionRebuildActionRequestV1,
    request: Request,
    rebuild_id: RebuildId,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    trusted_principal: PrincipalHeader = None,
) -> CollectionRebuildResponseV1:
    _require_admin(
        request,
        workspace_id=workspace_id,
        trusted_principal=trusted_principal,
        actor_id=actor_id,
    )
    return request.app.state.container.collection_rebuilds.request_validation(
        workspace_id=workspace_id,
        rebuild_id=rebuild_id,
        expected_version=payload.expected_version,
        trace_id=request.state.trace_id,
    )


@router.post(
    "/{rebuild_id}:activate",
    response_model=CollectionRebuildResponseV1,
    responses=ERRORS,
)
def activate_rebuild(
    payload: CollectionRebuildActionRequestV1,
    request: Request,
    rebuild_id: RebuildId,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    trusted_principal: PrincipalHeader = None,
) -> CollectionRebuildResponseV1:
    _require_admin(
        request,
        workspace_id=workspace_id,
        trusted_principal=trusted_principal,
        actor_id=actor_id,
    )
    return request.app.state.container.collection_rebuilds.activate(
        workspace_id=workspace_id,
        rebuild_id=rebuild_id,
        expected_version=payload.expected_version,
        trace_id=request.state.trace_id,
    )
