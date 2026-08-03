"""Versioned direct-upload and quarantined Asset HTTP routes."""

from typing import Annotated

from commercevision_application import AuthenticatedPrincipal
from commercevision_contracts import (
    AssetAdministratorBlockRequestV1,
    AssetIndexStatusResponseV1,
    AssetResponseV1,
    AssetValidationStatusResponseV1,
    ErrorResponse,
    RightsHistoryResponseV1,
    RightsMutationResponseV1,
    RightsRecordMutationRequestV1,
    RightsRecordRevokeRequestV1,
    RightsUsabilityRequestV1,
    RightsUsabilityResponseV1,
    UploadFinalizeResponseV1,
    UploadSessionCreateRequestV1,
    UploadSessionCreateResponseV1,
    UploadSessionMutationRequestV1,
    UploadSessionResponseV1,
)
from commercevision_domain import AuthenticationError
from fastapi import APIRouter, Header, Query, Request, status

from .workspace_identity import WorkspaceHeader

upload_router = APIRouter(prefix="/api/v1/upload-sessions", tags=["upload-sessions"])
asset_router = APIRouter(prefix="/api/v1/assets", tags=["assets"])

IdempotencyHeader = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=256)]
ActorHeader = Annotated[str, Header(alias="X-Actor-Id", min_length=1, max_length=128)]
PrincipalHeader = Annotated[
    str | None,
    Header(alias="X-Trusted-Principal", max_length=4096),
]

UPLOAD_ERROR_RESPONSES = {
    400: {"model": ErrorResponse, "description": "Invalid upload argument"},
    401: {"model": ErrorResponse, "description": "Trusted principal required"},
    403: {"model": ErrorResponse, "description": "Workspace membership required"},
    404: {"model": ErrorResponse, "description": "Upload or Asset not found"},
    409: {"model": ErrorResponse, "description": "Upload conflict"},
    410: {"model": ErrorResponse, "description": "Upload session expired"},
    422: {"model": ErrorResponse, "description": "Uploaded object rejected"},
    503: {"model": ErrorResponse, "description": "Object storage unavailable"},
}
RIGHTS_READ_ERROR_RESPONSES = {
    400: {"model": ErrorResponse, "description": "Invalid Rights argument"},
    401: {"model": ErrorResponse, "description": "Trusted principal required"},
    403: {"model": ErrorResponse, "description": "Workspace authorization required"},
    404: {"model": ErrorResponse, "description": "Asset not found"},
    422: {"model": ErrorResponse, "description": "Rights request validation failed"},
}
RIGHTS_MUTATION_ERROR_RESPONSES = {
    **RIGHTS_READ_ERROR_RESPONSES,
    409: {
        "model": ErrorResponse,
        "description": "Rights version, state, or idempotency conflict",
    },
}


def _require_workspace_principal(
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
    return principal


def _require_mutation_principal(
    request: Request,
    *,
    workspace_id: str,
    actor_id: str,
    trusted_principal: str | None,
) -> AuthenticatedPrincipal:
    principal = _require_workspace_principal(
        request,
        workspace_id=workspace_id,
        trusted_principal=trusted_principal,
    )
    if actor_id != principal.actor_id:
        raise AuthenticationError("actor header does not match the trusted principal")
    return principal


@upload_router.post(
    "",
    response_model=UploadSessionCreateResponseV1,
    status_code=status.HTTP_201_CREATED,
    responses=UPLOAD_ERROR_RESPONSES,
)
def create_upload_session(
    payload: UploadSessionCreateRequestV1,
    request: Request,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    idempotency_key: IdempotencyHeader,
    trusted_principal: PrincipalHeader = None,
) -> UploadSessionCreateResponseV1:
    principal = _require_mutation_principal(
        request,
        workspace_id=workspace_id,
        actor_id=actor_id,
        trusted_principal=trusted_principal,
    )
    return request.app.state.container.assets.create_upload_session(
        request=payload,
        workspace_id=workspace_id,
        actor_id=principal.actor_id,
        idempotency_key=idempotency_key,
        trace_id=request.state.trace_id,
    )


@upload_router.get(
    "/{upload_session_id}",
    response_model=UploadSessionResponseV1,
    responses=UPLOAD_ERROR_RESPONSES,
)
def get_upload_session(
    upload_session_id: str,
    request: Request,
    workspace_id: WorkspaceHeader,
    trusted_principal: PrincipalHeader = None,
) -> UploadSessionResponseV1:
    _require_workspace_principal(
        request,
        workspace_id=workspace_id,
        trusted_principal=trusted_principal,
    )
    return request.app.state.container.assets.get_upload_session(
        workspace_id=workspace_id,
        upload_session_id=upload_session_id,
        trace_id=request.state.trace_id,
    )


@upload_router.post(
    "/{upload_session_id}:abort",
    response_model=UploadSessionResponseV1,
    responses=UPLOAD_ERROR_RESPONSES,
)
def abort_upload_session(
    upload_session_id: str,
    payload: UploadSessionMutationRequestV1,
    request: Request,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    idempotency_key: IdempotencyHeader,
    trusted_principal: PrincipalHeader = None,
) -> UploadSessionResponseV1:
    principal = _require_mutation_principal(
        request,
        workspace_id=workspace_id,
        actor_id=actor_id,
        trusted_principal=trusted_principal,
    )
    return request.app.state.container.assets.abort_upload_session(
        upload_session_id=upload_session_id,
        request=payload,
        workspace_id=workspace_id,
        actor_id=principal.actor_id,
        idempotency_key=idempotency_key,
        trace_id=request.state.trace_id,
    )


@upload_router.post(
    "/{upload_session_id}:finalize",
    response_model=UploadFinalizeResponseV1,
    status_code=status.HTTP_202_ACCEPTED,
    responses=UPLOAD_ERROR_RESPONSES,
)
def finalize_upload_session(
    upload_session_id: str,
    payload: UploadSessionMutationRequestV1,
    request: Request,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    idempotency_key: IdempotencyHeader,
    trusted_principal: PrincipalHeader = None,
) -> UploadFinalizeResponseV1:
    principal = _require_mutation_principal(
        request,
        workspace_id=workspace_id,
        actor_id=actor_id,
        trusted_principal=trusted_principal,
    )
    return request.app.state.container.assets.finalize_upload_session(
        upload_session_id=upload_session_id,
        request=payload,
        workspace_id=workspace_id,
        actor_id=principal.actor_id,
        idempotency_key=idempotency_key,
        trace_id=request.state.trace_id,
    )


@asset_router.get(
    "/{asset_id}",
    response_model=AssetResponseV1,
    responses=UPLOAD_ERROR_RESPONSES,
)
def get_asset(
    asset_id: str,
    request: Request,
    workspace_id: WorkspaceHeader,
    trusted_principal: PrincipalHeader = None,
) -> AssetResponseV1:
    _require_workspace_principal(
        request,
        workspace_id=workspace_id,
        trusted_principal=trusted_principal,
    )
    return request.app.state.container.assets.get_asset(
        workspace_id=workspace_id,
        asset_id=asset_id,
    )


@asset_router.get(
    "/{asset_id}/validation",
    response_model=AssetValidationStatusResponseV1,
    responses=UPLOAD_ERROR_RESPONSES,
)
def get_asset_validation(
    asset_id: str,
    request: Request,
    workspace_id: WorkspaceHeader,
    trusted_principal: PrincipalHeader = None,
) -> AssetValidationStatusResponseV1:
    _require_workspace_principal(
        request,
        workspace_id=workspace_id,
        trusted_principal=trusted_principal,
    )
    return request.app.state.container.assets.get_asset_validation(
        workspace_id=workspace_id,
        asset_id=asset_id,
    )


@asset_router.get(
    "/{asset_id}/index-status",
    response_model=AssetIndexStatusResponseV1,
    responses=UPLOAD_ERROR_RESPONSES,
)
def get_asset_index_status(
    asset_id: str,
    request: Request,
    workspace_id: WorkspaceHeader,
    trusted_principal: PrincipalHeader = None,
) -> AssetIndexStatusResponseV1:
    _require_workspace_principal(
        request,
        workspace_id=workspace_id,
        trusted_principal=trusted_principal,
    )
    return request.app.state.container.image_index_status.get_current(
        workspace_id=workspace_id,
        asset_id=asset_id,
    )


@asset_router.post(
    "/{asset_id}/rights",
    response_model=RightsMutationResponseV1,
    status_code=status.HTTP_201_CREATED,
    responses=RIGHTS_MUTATION_ERROR_RESPONSES,
)
def register_asset_rights(
    asset_id: str,
    payload: RightsRecordMutationRequestV1,
    request: Request,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    idempotency_key: IdempotencyHeader,
    trusted_principal: PrincipalHeader = None,
) -> RightsMutationResponseV1:
    principal = _require_mutation_principal(
        request,
        workspace_id=workspace_id,
        actor_id=actor_id,
        trusted_principal=trusted_principal,
    )
    return request.app.state.container.rights.register(
        workspace_id=workspace_id,
        asset_id=asset_id,
        actor_id=principal.actor_id,
        idempotency_key=idempotency_key,
        trace_id=request.state.trace_id,
        request=payload,
    )


@asset_router.post(
    "/{asset_id}/rights:replace",
    response_model=RightsMutationResponseV1,
    responses=RIGHTS_MUTATION_ERROR_RESPONSES,
)
def replace_asset_rights(
    asset_id: str,
    payload: RightsRecordMutationRequestV1,
    request: Request,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    idempotency_key: IdempotencyHeader,
    trusted_principal: PrincipalHeader = None,
) -> RightsMutationResponseV1:
    principal = _require_mutation_principal(
        request,
        workspace_id=workspace_id,
        actor_id=actor_id,
        trusted_principal=trusted_principal,
    )
    return request.app.state.container.rights.replace(
        workspace_id=workspace_id,
        asset_id=asset_id,
        actor_id=principal.actor_id,
        idempotency_key=idempotency_key,
        trace_id=request.state.trace_id,
        request=payload,
    )


@asset_router.post(
    "/{asset_id}/rights:revoke",
    response_model=RightsMutationResponseV1,
    responses=RIGHTS_MUTATION_ERROR_RESPONSES,
)
def revoke_asset_rights(
    asset_id: str,
    payload: RightsRecordRevokeRequestV1,
    request: Request,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    idempotency_key: IdempotencyHeader,
    trusted_principal: PrincipalHeader = None,
) -> RightsMutationResponseV1:
    principal = _require_mutation_principal(
        request,
        workspace_id=workspace_id,
        actor_id=actor_id,
        trusted_principal=trusted_principal,
    )
    return request.app.state.container.rights.revoke(
        workspace_id=workspace_id,
        asset_id=asset_id,
        actor_id=principal.actor_id,
        idempotency_key=idempotency_key,
        trace_id=request.state.trace_id,
        request=payload,
    )


@asset_router.post(
    "/{asset_id}:block",
    response_model=RightsMutationResponseV1,
    responses=RIGHTS_MUTATION_ERROR_RESPONSES,
)
def administrator_block_asset(
    asset_id: str,
    payload: AssetAdministratorBlockRequestV1,
    request: Request,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    idempotency_key: IdempotencyHeader,
    trusted_principal: PrincipalHeader = None,
) -> RightsMutationResponseV1:
    principal = _require_mutation_principal(
        request,
        workspace_id=workspace_id,
        actor_id=actor_id,
        trusted_principal=trusted_principal,
    )
    request.app.state.container.access_policy.require_admin(
        workspace_id=workspace_id,
        principal=principal,
    )
    return request.app.state.container.rights.administrator_block(
        workspace_id=workspace_id,
        asset_id=asset_id,
        actor_id=principal.actor_id,
        idempotency_key=idempotency_key,
        trace_id=request.state.trace_id,
        request=payload,
    )


@asset_router.get(
    "/{asset_id}/rights",
    response_model=RightsHistoryResponseV1,
    responses=RIGHTS_READ_ERROR_RESPONSES,
)
def list_asset_rights(
    asset_id: str,
    request: Request,
    workspace_id: WorkspaceHeader,
    before_version: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    trusted_principal: PrincipalHeader = None,
) -> RightsHistoryResponseV1:
    _require_workspace_principal(
        request,
        workspace_id=workspace_id,
        trusted_principal=trusted_principal,
    )
    return request.app.state.container.rights.history(
        workspace_id=workspace_id,
        asset_id=asset_id,
        before_version=before_version,
        limit=limit,
    )


@asset_router.post(
    "/{asset_id}/usability:check",
    response_model=RightsUsabilityResponseV1,
    responses=RIGHTS_READ_ERROR_RESPONSES,
)
def check_asset_usability(
    asset_id: str,
    payload: RightsUsabilityRequestV1,
    request: Request,
    workspace_id: WorkspaceHeader,
    trusted_principal: PrincipalHeader = None,
) -> RightsUsabilityResponseV1:
    _require_workspace_principal(
        request,
        workspace_id=workspace_id,
        trusted_principal=trusted_principal,
    )
    return request.app.state.container.rights.current_usability(
        workspace_id=workspace_id,
        asset_id=asset_id,
        request=payload,
    )
