"""Workspace-safe Prompt Registry administration and exact production resolution."""

from typing import Annotated, Any, cast

from commercevision_application import AuthenticatedPrincipal
from commercevision_contracts import (
    ErrorResponse,
    PromptProductionPointerResponseV1,
    PromptProductionSelectionRequestV1,
    PromptRevisionCreateRequestV1,
    PromptRevisionResponseV1,
    PromptRevisionTransitionRequestV1,
)
from commercevision_domain import AuthenticationError, canonicalize_uuid
from fastapi import APIRouter, Header, Path, Query, Request, status
from pydantic import AfterValidator

from .workspace_identity import WorkspaceHeader

router = APIRouter(prefix="/api/v1/prompts", tags=["prompt-registry"])

TOKEN_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
CANONICAL_UUID_PATTERN = (
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}$"
)

ActorHeader = Annotated[str, Header(alias="X-Actor-Id", min_length=1, max_length=128)]
IdempotencyHeader = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=8, max_length=255),
]
PrincipalHeader = Annotated[
    str | None,
    Header(alias="X-Trusted-Principal", max_length=4096),
]
PromptId = Annotated[str, Path(pattern=TOKEN_PATTERN)]
PromptDimension = Annotated[str, Query(pattern=TOKEN_PATTERN)]


def _canonical_revision_id(value: str) -> str:
    canonical = canonicalize_uuid(value)
    if canonical != value:
        raise ValueError("revision_id must be a canonical lowercase UUID")
    return value


RevisionId = Annotated[
    str,
    Path(pattern=CANONICAL_UUID_PATTERN),
    AfterValidator(_canonical_revision_id),
]

PROMPT_REGISTRY_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse, "description": "Invalid Prompt Registry argument"},
    401: {"model": ErrorResponse, "description": "Trusted principal required"},
    403: {
        "model": ErrorResponse,
        "description": "Workspace membership or administrator privilege required",
    },
    404: {"model": ErrorResponse, "description": "Prompt Registry resource not found"},
    409: {
        "model": ErrorResponse,
        "description": "Prompt lifecycle, version, identity, or idempotency conflict",
    },
    422: {"model": ErrorResponse, "description": "Prompt contract validation failed"},
    500: {"model": ErrorResponse, "description": "Unexpected internal error"},
    503: {"model": ErrorResponse, "description": "Prompt Registry dependency unavailable"},
}


def _workspace_principal(
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


def _admin_principal(
    request: Request,
    *,
    workspace_id: str,
    actor_id: str,
    trusted_principal: str | None,
) -> AuthenticatedPrincipal:
    principal = request.app.state.container.principal_resolver.resolve(trusted_principal)
    if actor_id != principal.actor_id:
        raise AuthenticationError("actor header does not match the trusted principal")
    request.app.state.container.access_policy.require_admin(
        workspace_id=workspace_id,
        principal=principal,
    )
    return cast(AuthenticatedPrincipal, principal)


@router.post(
    "/revisions",
    response_model=PromptRevisionResponseV1,
    status_code=status.HTTP_201_CREATED,
    responses=PROMPT_REGISTRY_ERROR_RESPONSES,
)
def create_prompt_revision(
    payload: PromptRevisionCreateRequestV1,
    request: Request,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    idempotency_key: IdempotencyHeader,
    trusted_principal: PrincipalHeader = None,
) -> PromptRevisionResponseV1:
    principal = _admin_principal(
        request,
        workspace_id=workspace_id,
        actor_id=actor_id,
        trusted_principal=trusted_principal,
    )
    return cast(
        PromptRevisionResponseV1,
        request.app.state.container.prompt_registry.create_revision(
            workspace_id=workspace_id,
            actor_id=principal.actor_id,
            request=payload,
            trace_id=request.state.trace_id,
            idempotency_key=idempotency_key,
        ),
    )


def _transition_prompt_revision(
    *,
    operation: str,
    revision_id: str,
    payload: PromptRevisionTransitionRequestV1,
    request: Request,
    workspace_id: str,
    actor_id: str,
    idempotency_key: str,
    trusted_principal: str | None,
) -> PromptRevisionResponseV1:
    principal = _admin_principal(
        request,
        workspace_id=workspace_id,
        actor_id=actor_id,
        trusted_principal=trusted_principal,
    )
    command = getattr(request.app.state.container.prompt_registry, operation)
    return cast(
        PromptRevisionResponseV1,
        command(
            workspace_id=workspace_id,
            revision_id=revision_id,
            actor_id=principal.actor_id,
            request=payload,
            trace_id=request.state.trace_id,
            idempotency_key=idempotency_key,
        ),
    )


@router.post(
    "/revisions/{revision_id}:submit-review",
    response_model=PromptRevisionResponseV1,
    responses=PROMPT_REGISTRY_ERROR_RESPONSES,
)
def submit_prompt_revision_for_review(
    revision_id: RevisionId,
    payload: PromptRevisionTransitionRequestV1,
    request: Request,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    idempotency_key: IdempotencyHeader,
    trusted_principal: PrincipalHeader = None,
) -> PromptRevisionResponseV1:
    return _transition_prompt_revision(
        operation="submit_for_review",
        revision_id=revision_id,
        payload=payload,
        request=request,
        workspace_id=workspace_id,
        actor_id=actor_id,
        idempotency_key=idempotency_key,
        trusted_principal=trusted_principal,
    )


@router.post(
    "/revisions/{revision_id}:stage",
    response_model=PromptRevisionResponseV1,
    responses=PROMPT_REGISTRY_ERROR_RESPONSES,
)
def stage_prompt_revision(
    revision_id: RevisionId,
    payload: PromptRevisionTransitionRequestV1,
    request: Request,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    idempotency_key: IdempotencyHeader,
    trusted_principal: PrincipalHeader = None,
) -> PromptRevisionResponseV1:
    return _transition_prompt_revision(
        operation="stage",
        revision_id=revision_id,
        payload=payload,
        request=request,
        workspace_id=workspace_id,
        actor_id=actor_id,
        idempotency_key=idempotency_key,
        trusted_principal=trusted_principal,
    )


@router.post(
    "/revisions/{revision_id}:publish",
    response_model=PromptRevisionResponseV1,
    responses=PROMPT_REGISTRY_ERROR_RESPONSES,
)
def publish_prompt_revision(
    revision_id: RevisionId,
    payload: PromptRevisionTransitionRequestV1,
    request: Request,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    idempotency_key: IdempotencyHeader,
    trusted_principal: PrincipalHeader = None,
) -> PromptRevisionResponseV1:
    return _transition_prompt_revision(
        operation="publish",
        revision_id=revision_id,
        payload=payload,
        request=request,
        workspace_id=workspace_id,
        actor_id=actor_id,
        idempotency_key=idempotency_key,
        trusted_principal=trusted_principal,
    )


@router.post(
    "/revisions/{revision_id}:deprecate",
    response_model=PromptRevisionResponseV1,
    responses=PROMPT_REGISTRY_ERROR_RESPONSES,
)
def deprecate_prompt_revision(
    revision_id: RevisionId,
    payload: PromptRevisionTransitionRequestV1,
    request: Request,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    idempotency_key: IdempotencyHeader,
    trusted_principal: PrincipalHeader = None,
) -> PromptRevisionResponseV1:
    return _transition_prompt_revision(
        operation="deprecate",
        revision_id=revision_id,
        payload=payload,
        request=request,
        workspace_id=workspace_id,
        actor_id=actor_id,
        idempotency_key=idempotency_key,
        trusted_principal=trusted_principal,
    )


@router.put(
    "/{prompt_id}/production",
    response_model=PromptProductionPointerResponseV1,
    responses=PROMPT_REGISTRY_ERROR_RESPONSES,
)
def select_prompt_production(
    prompt_id: PromptId,
    payload: PromptProductionSelectionRequestV1,
    request: Request,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    idempotency_key: IdempotencyHeader,
    trusted_principal: PrincipalHeader = None,
) -> PromptProductionPointerResponseV1:
    principal = _admin_principal(
        request,
        workspace_id=workspace_id,
        actor_id=actor_id,
        trusted_principal=trusted_principal,
    )
    return cast(
        PromptProductionPointerResponseV1,
        request.app.state.container.prompt_registry.select_production(
            workspace_id=workspace_id,
            prompt_id=prompt_id,
            actor_id=principal.actor_id,
            request=payload,
            trace_id=request.state.trace_id,
            idempotency_key=idempotency_key,
        ),
    )


@router.get(
    "/{prompt_id}/production",
    response_model=PromptRevisionResponseV1,
    responses=PROMPT_REGISTRY_ERROR_RESPONSES,
)
def resolve_prompt_production(
    prompt_id: PromptId,
    request: Request,
    workspace_id: WorkspaceHeader,
    node: PromptDimension,
    category: PromptDimension,
    model_family: PromptDimension,
    trusted_principal: PrincipalHeader = None,
) -> PromptRevisionResponseV1:
    _workspace_principal(
        request,
        workspace_id=workspace_id,
        trusted_principal=trusted_principal,
    )
    return cast(
        PromptRevisionResponseV1,
        request.app.state.container.prompt_registry.resolve_production(
            workspace_id=workspace_id,
            prompt_id=prompt_id,
            node=node,
            category=category,
            model_family=model_family,
        ),
    )
