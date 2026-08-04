"""Rights-first hybrid retrieval, retained runs, and controlled previews."""

from typing import Annotated

from commercevision_application import AuthenticatedPrincipal
from commercevision_contracts import (
    ErrorResponse,
    RetrievalPreviewExchangeV1,
    RetrievalQueryV1,
    RetrievalResponseV1,
    RetrievalTemporaryReferenceV1,
)
from commercevision_domain import AuthenticationError, NotFoundError, canonicalize_uuid
from commercevision_observability import (
    Phase2Span,
    Phase2Telemetry,
    TelemetryDimensions,
    TelemetryIdentity,
)
from fastapi import APIRouter, Header, Path, Request
from pydantic import AfterValidator

from .workspace_identity import WorkspaceHeader

router = APIRouter(prefix="/api/v1/retrieval-runs", tags=["retrieval"])

ActorHeader = Annotated[str, Header(alias="X-Actor-Id", min_length=1, max_length=128)]
PrincipalHeader = Annotated[
    str | None,
    Header(alias="X-Trusted-Principal", max_length=4096),
]


def _canonical_run_id(value: str) -> str:
    if canonicalize_uuid(value) != value:
        raise ValueError("run_id must be a canonical lowercase UUID")
    return value


RunId = Annotated[
    str,
    Path(
        pattern=(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
            r"[0-9a-f]{4}-[0-9a-f]{12}$"
        )
    ),
    AfterValidator(_canonical_run_id),
]

RETRIEVAL_ERROR_RESPONSES = {
    400: {"model": ErrorResponse, "description": "Invalid Retrieval Query"},
    401: {"model": ErrorResponse, "description": "Trusted principal required"},
    403: {"model": ErrorResponse, "description": "Workspace membership required"},
    404: {"model": ErrorResponse, "description": "Retrieval Run or preview unavailable"},
    422: {"model": ErrorResponse, "description": "Retrieval request validation failed"},
    500: {"model": ErrorResponse, "description": "Unexpected internal error"},
    503: {"model": ErrorResponse, "description": "Retrieval dependency unavailable"},
}


def _principal(
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


@router.post(
    "",
    response_model=RetrievalResponseV1,
    responses=RETRIEVAL_ERROR_RESPONSES,
)
def execute_retrieval(
    payload: RetrievalQueryV1,
    request: Request,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    trusted_principal: PrincipalHeader = None,
) -> RetrievalResponseV1:
    principal = _principal(
        request,
        workspace_id=workspace_id,
        trusted_principal=trusted_principal,
    )
    if payload.workspace_id != workspace_id:
        raise AuthenticationError("query workspace does not match the workspace header")
    if actor_id != principal.actor_id or payload.requester_id != principal.actor_id:
        raise AuthenticationError("query requester does not match the trusted principal")
    response = request.app.state.container.retrieval.execute(payload)
    return request.app.state.container.retrieval_runs.record(payload, response)


@router.get(
    "/{run_id}",
    response_model=RetrievalResponseV1,
    responses=RETRIEVAL_ERROR_RESPONSES,
)
def get_retrieval_run(
    run_id: RunId,
    request: Request,
    workspace_id: WorkspaceHeader,
    trusted_principal: PrincipalHeader = None,
) -> RetrievalResponseV1:
    _principal(
        request,
        workspace_id=workspace_id,
        trusted_principal=trusted_principal,
    )
    response = request.app.state.container.retrieval_runs.get(
        workspace_id=workspace_id,
        run_id=run_id,
    )
    if response is None:
        raise NotFoundError("Retrieval Run not found")
    return response


@router.post(
    "/{run_id}/results/{rank}:preview",
    response_model=RetrievalTemporaryReferenceV1,
    responses=RETRIEVAL_ERROR_RESPONSES,
)
def exchange_retrieval_preview(
    run_id: RunId,
    rank: Annotated[int, Path(ge=1, le=50)],
    payload: RetrievalPreviewExchangeV1,
    request: Request,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    trusted_principal: PrincipalHeader = None,
) -> RetrievalTemporaryReferenceV1:
    principal = _principal(
        request,
        workspace_id=workspace_id,
        trusted_principal=trusted_principal,
    )
    if actor_id != principal.actor_id:
        raise AuthenticationError("actor header does not match the trusted principal")
    telemetry = getattr(request.app.state, "telemetry", None) or Phase2Telemetry()
    with telemetry.span(
        Phase2Span.TEMPORARY_REFERENCE,
        identity=TelemetryIdentity(
            trace_id=request.state.trace_id,
            workspace_id=workspace_id,
            target_id=run_id,
            target_version=rank,
        ),
        dimensions=TelemetryDimensions(phase="preview_exchange"),
    ):
        reference = request.app.state.container.retrieval_previews.exchange(
            workspace_id=workspace_id,
            requester_id=principal.actor_id,
            run_id=run_id,
            rank=rank,
            token=payload.preview_reference_token,
        )
    if reference is None:
        raise NotFoundError("Retrieval preview is unavailable")
    return reference
