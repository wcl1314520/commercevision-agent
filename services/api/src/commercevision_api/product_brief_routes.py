"""Versioned ProductBrief analysis and human-review HTTP routes."""

from typing import Annotated

from commercevision_application import AuthenticatedPrincipal
from commercevision_contracts import ErrorResponse
from commercevision_contracts.product_brief_views import (
    ProductBriefOperationStatusResponseV1,
    ProductBriefWorkflowContextResponseV1,
)
from commercevision_contracts.product_briefs import (
    ProductBriefAnalysisAcceptedV1,
    ProductBriefAnalysisRequestV1,
    ProductBriefConfirmationRequestV1,
    ProductBriefConfirmationResponseV1,
    ProductBriefResponseV1,
    ProductBriefRevisionRequestV1,
    ProductBriefVersionListResponseV1,
)
from commercevision_domain import AuthenticationError
from fastapi import APIRouter, Header, Query, Request, status

from .workspace_identity import WorkspaceHeader

router = APIRouter(prefix="/api/v1/product-briefs", tags=["product-briefs"])
PRODUCT_BRIEF_ERROR_RESPONSES = {
    400: {"model": ErrorResponse, "description": "Invalid ProductBrief argument"},
    401: {"model": ErrorResponse, "description": "Trusted principal required"},
    403: {"model": ErrorResponse, "description": "Provider or rights policy denied"},
    404: {"model": ErrorResponse, "description": "ProductBrief resource not found"},
    409: {"model": ErrorResponse, "description": "ProductBrief version conflict"},
    410: {"model": ErrorResponse, "description": "ProductBrief retention expired"},
    422: {"model": ErrorResponse, "description": "ProductBrief validation failed"},
    500: {"model": ErrorResponse, "description": "Unexpected internal error"},
    503: {"model": ErrorResponse, "description": "ProductBrief dependency unavailable"},
}

ActorHeader = Annotated[
    str,
    Header(alias="X-Actor-Id", min_length=1, max_length=128),
]
IdempotencyHeader = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=8, max_length=256),
]
PrincipalHeader = Annotated[
    str | None,
    Header(alias="X-Trusted-Principal", max_length=4096),
]


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


@router.get(
    "/analysis-workflow-context/{workflow_id}",
    response_model=ProductBriefWorkflowContextResponseV1,
    responses=PRODUCT_BRIEF_ERROR_RESPONSES,
)
def get_product_brief_analysis_workflow_context(
    workflow_id: str,
    request: Request,
    workspace_id: WorkspaceHeader,
    trusted_principal: PrincipalHeader = None,
) -> ProductBriefWorkflowContextResponseV1:
    _require_workspace_principal(
        request,
        workspace_id=workspace_id,
        trusted_principal=trusted_principal,
    )
    return request.app.state.container.product_brief_views.analysis_workflow_context(
        workflow_id=workflow_id,
        workspace_id=workspace_id,
    )


@router.get(
    "/workflow-context/{workflow_id}",
    response_model=ProductBriefWorkflowContextResponseV1,
    responses=PRODUCT_BRIEF_ERROR_RESPONSES,
)
def get_product_brief_workflow_context(
    workflow_id: str,
    product_brief_id: str,
    request: Request,
    workspace_id: WorkspaceHeader,
    trusted_principal: PrincipalHeader = None,
) -> ProductBriefWorkflowContextResponseV1:
    _require_workspace_principal(
        request,
        workspace_id=workspace_id,
        trusted_principal=trusted_principal,
    )
    return request.app.state.container.product_brief_views.workflow_context(
        product_brief_id=product_brief_id,
        workflow_id=workflow_id,
        workspace_id=workspace_id,
    )


@router.get(
    "/{product_brief_id}/operations/{operation_id}",
    response_model=ProductBriefOperationStatusResponseV1,
    responses=PRODUCT_BRIEF_ERROR_RESPONSES,
)
def get_product_brief_operation_status(
    product_brief_id: str,
    operation_id: str,
    request: Request,
    workspace_id: WorkspaceHeader,
    trusted_principal: PrincipalHeader = None,
) -> ProductBriefOperationStatusResponseV1:
    _require_workspace_principal(
        request,
        workspace_id=workspace_id,
        trusted_principal=trusted_principal,
    )
    return request.app.state.container.product_brief_views.operation_status(
        workspace_id=workspace_id,
        product_brief_id=product_brief_id,
        operation_id=operation_id,
    )


@router.post(
    ":analyze",
    response_model=ProductBriefAnalysisAcceptedV1,
    status_code=status.HTTP_202_ACCEPTED,
    responses=PRODUCT_BRIEF_ERROR_RESPONSES,
)
def request_product_brief_analysis(
    payload: ProductBriefAnalysisRequestV1,
    request: Request,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    idempotency_key: IdempotencyHeader,
    trusted_principal: PrincipalHeader = None,
) -> ProductBriefAnalysisAcceptedV1:
    principal = _require_mutation_principal(
        request,
        workspace_id=workspace_id,
        actor_id=actor_id,
        trusted_principal=trusted_principal,
    )
    return request.app.state.container.product_briefs.request_analysis(
        workspace_id=workspace_id,
        actor_id=principal.actor_id,
        request=payload,
        idempotency_key=idempotency_key,
        trace_id=request.state.trace_id,
    )


@router.get(
    "/{product_brief_id}",
    response_model=ProductBriefResponseV1,
    responses=PRODUCT_BRIEF_ERROR_RESPONSES,
)
def get_product_brief(
    product_brief_id: str,
    request: Request,
    workspace_id: WorkspaceHeader,
    trusted_principal: PrincipalHeader = None,
) -> ProductBriefResponseV1:
    _require_workspace_principal(
        request,
        workspace_id=workspace_id,
        trusted_principal=trusted_principal,
    )
    return request.app.state.container.product_briefs.get(
        workspace_id=workspace_id,
        product_brief_id=product_brief_id,
    )


@router.get(
    "/{product_brief_id}/versions",
    response_model=ProductBriefVersionListResponseV1,
    responses=PRODUCT_BRIEF_ERROR_RESPONSES,
)
def list_product_brief_versions(
    product_brief_id: str,
    request: Request,
    workspace_id: WorkspaceHeader,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[int | None, Query(ge=1, le=2_147_483_647)] = None,
    trusted_principal: PrincipalHeader = None,
) -> ProductBriefVersionListResponseV1:
    _require_workspace_principal(
        request,
        workspace_id=workspace_id,
        trusted_principal=trusted_principal,
    )
    return request.app.state.container.product_briefs.list_versions(
        workspace_id=workspace_id,
        product_brief_id=product_brief_id,
        limit=limit,
        cursor=cursor,
    )


@router.post(
    "/{product_brief_id}:revise",
    response_model=ProductBriefResponseV1,
    responses=PRODUCT_BRIEF_ERROR_RESPONSES,
)
def revise_product_brief(
    product_brief_id: str,
    payload: ProductBriefRevisionRequestV1,
    request: Request,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    idempotency_key: IdempotencyHeader,
    trusted_principal: PrincipalHeader = None,
) -> ProductBriefResponseV1:
    principal = _require_mutation_principal(
        request,
        workspace_id=workspace_id,
        actor_id=actor_id,
        trusted_principal=trusted_principal,
    )
    return request.app.state.container.product_briefs.revise(
        workspace_id=workspace_id,
        product_brief_id=product_brief_id,
        actor_id=principal.actor_id,
        request=payload,
        idempotency_key=idempotency_key,
        trace_id=request.state.trace_id,
    )


@router.post(
    "/{product_brief_id}:confirm",
    response_model=ProductBriefConfirmationResponseV1,
    responses=PRODUCT_BRIEF_ERROR_RESPONSES,
)
def confirm_product_brief(
    product_brief_id: str,
    payload: ProductBriefConfirmationRequestV1,
    request: Request,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    idempotency_key: IdempotencyHeader,
    trusted_principal: PrincipalHeader = None,
) -> ProductBriefConfirmationResponseV1:
    principal = _require_mutation_principal(
        request,
        workspace_id=workspace_id,
        actor_id=actor_id,
        trusted_principal=trusted_principal,
    )
    return request.app.state.container.product_briefs.confirm(
        workspace_id=workspace_id,
        product_brief_id=product_brief_id,
        actor_id=principal.actor_id,
        request=payload,
        idempotency_key=idempotency_key,
        trace_id=request.state.trace_id,
    )
