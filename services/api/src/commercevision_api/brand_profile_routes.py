"""Versioned Brand Profile administration and immutable history routes."""

from typing import Annotated

from commercevision_application import AuthenticatedPrincipal
from commercevision_contracts import (
    BrandProfileCreateRequestV1,
    BrandProfileListResponseV1,
    BrandProfilePublishRequestV1,
    BrandProfileResponseV1,
    BrandProfileUpdateDraftRequestV1,
    BrandProfileValidateRequestV1,
    BrandProfileValidationResponseV1,
    BrandProfileVersionListResponseV1,
    BrandProfileVersionResponseV1,
    ErrorResponse,
)
from commercevision_domain import (
    AuthenticationError,
    canonicalize_uuid,
)
from fastapi import APIRouter, Header, Path, Query, Request, status
from pydantic import AfterValidator

from .workspace_identity import WorkspaceHeader

router = APIRouter(prefix="/api/v1/brand-profiles", tags=["brand-profiles"])

CANONICAL_PROFILE_ID_PATTERN = (
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}$"
)

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


def _canonical_profile_id(value: str) -> str:
    canonical = canonicalize_uuid(value)
    if canonical != value:
        raise ValueError("profile_id must be a canonical lowercase UUID")
    return value


def _brand_filter(value: str | None) -> str | None:
    if value is not None and (
        value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("brand must be trimmed and contain no control characters")
    return value


CanonicalProfileId = Annotated[
    str,
    Path(pattern=CANONICAL_PROFILE_ID_PATTERN),
    AfterValidator(_canonical_profile_id),
]
BrandFilter = Annotated[
    str | None,
    Query(min_length=1, max_length=128),
    AfterValidator(_brand_filter),
]

BRAND_PROFILE_ERROR_RESPONSES = {
    400: {"model": ErrorResponse, "description": "Invalid Brand Profile argument"},
    401: {"model": ErrorResponse, "description": "Trusted principal required"},
    403: {
        "model": ErrorResponse,
        "description": "Workspace membership or administrator privilege required",
    },
    404: {"model": ErrorResponse, "description": "Brand Profile resource not found"},
    409: {
        "model": ErrorResponse,
        "description": "Brand Profile version, key, or idempotency conflict",
    },
    422: {
        "model": ErrorResponse,
        "description": "Brand Profile draft or publication validation failed",
    },
    500: {"model": ErrorResponse, "description": "Unexpected internal error"},
    503: {
        "model": ErrorResponse,
        "description": "Brand Profile dependency unavailable",
    },
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
    return principal


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
    return principal


@router.post(
    "",
    response_model=BrandProfileResponseV1,
    status_code=status.HTTP_201_CREATED,
    responses=BRAND_PROFILE_ERROR_RESPONSES,
)
def create_brand_profile(
    payload: BrandProfileCreateRequestV1,
    request: Request,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    idempotency_key: IdempotencyHeader,
    trusted_principal: PrincipalHeader = None,
) -> BrandProfileResponseV1:
    principal = _admin_principal(
        request,
        workspace_id=workspace_id,
        actor_id=actor_id,
        trusted_principal=trusted_principal,
    )
    return request.app.state.container.brand_profiles.create(
        workspace_id=workspace_id,
        actor_id=principal.actor_id,
        request=payload,
        idempotency_key=idempotency_key,
        trace_id=request.state.trace_id,
    )


@router.get(
    "",
    response_model=BrandProfileListResponseV1,
    responses=BRAND_PROFILE_ERROR_RESPONSES,
)
def list_brand_profiles(
    request: Request,
    workspace_id: WorkspaceHeader,
    brand: BrandFilter = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(min_length=1, max_length=256)] = None,
    trusted_principal: PrincipalHeader = None,
) -> BrandProfileListResponseV1:
    _workspace_principal(
        request,
        workspace_id=workspace_id,
        trusted_principal=trusted_principal,
    )
    return request.app.state.container.brand_profiles.list_profiles(
        workspace_id=workspace_id,
        brand=brand,
        limit=limit,
        cursor=cursor,
    )


@router.put(
    "/{profile_id}/draft",
    response_model=BrandProfileResponseV1,
    responses=BRAND_PROFILE_ERROR_RESPONSES,
)
def update_brand_profile_draft(
    profile_id: CanonicalProfileId,
    payload: BrandProfileUpdateDraftRequestV1,
    request: Request,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    idempotency_key: IdempotencyHeader,
    trusted_principal: PrincipalHeader = None,
) -> BrandProfileResponseV1:
    principal = _admin_principal(
        request,
        workspace_id=workspace_id,
        actor_id=actor_id,
        trusted_principal=trusted_principal,
    )
    return request.app.state.container.brand_profiles.update_draft(
        workspace_id=workspace_id,
        profile_id=profile_id,
        actor_id=principal.actor_id,
        request=payload,
        idempotency_key=idempotency_key,
        trace_id=request.state.trace_id,
    )


@router.post(
    "/{profile_id}:validate",
    response_model=BrandProfileValidationResponseV1,
    responses=BRAND_PROFILE_ERROR_RESPONSES,
)
def validate_brand_profile(
    profile_id: CanonicalProfileId,
    payload: BrandProfileValidateRequestV1,
    request: Request,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    trusted_principal: PrincipalHeader = None,
) -> BrandProfileValidationResponseV1:
    _admin_principal(
        request,
        workspace_id=workspace_id,
        actor_id=actor_id,
        trusted_principal=trusted_principal,
    )
    return request.app.state.container.brand_profiles.validate(
        workspace_id=workspace_id,
        profile_id=profile_id,
        request=payload,
    )


@router.post(
    "/{profile_id}:publish",
    response_model=BrandProfileResponseV1,
    status_code=status.HTTP_201_CREATED,
    responses=BRAND_PROFILE_ERROR_RESPONSES,
)
def publish_brand_profile(
    profile_id: CanonicalProfileId,
    payload: BrandProfilePublishRequestV1,
    request: Request,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    idempotency_key: IdempotencyHeader,
    trusted_principal: PrincipalHeader = None,
) -> BrandProfileResponseV1:
    principal = _admin_principal(
        request,
        workspace_id=workspace_id,
        actor_id=actor_id,
        trusted_principal=trusted_principal,
    )
    return request.app.state.container.brand_profiles.publish(
        workspace_id=workspace_id,
        profile_id=profile_id,
        actor_id=principal.actor_id,
        request=payload,
        idempotency_key=idempotency_key,
        trace_id=request.state.trace_id,
    )


@router.get(
    "/{profile_id}/versions",
    response_model=BrandProfileVersionListResponseV1,
    responses=BRAND_PROFILE_ERROR_RESPONSES,
)
def list_brand_profile_versions(
    profile_id: CanonicalProfileId,
    request: Request,
    workspace_id: WorkspaceHeader,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(min_length=1, max_length=256)] = None,
    trusted_principal: PrincipalHeader = None,
) -> BrandProfileVersionListResponseV1:
    _workspace_principal(
        request,
        workspace_id=workspace_id,
        trusted_principal=trusted_principal,
    )
    return request.app.state.container.brand_profiles.list_versions(
        workspace_id=workspace_id,
        profile_id=profile_id,
        limit=limit,
        cursor=cursor,
    )


@router.get(
    "/{profile_id}/versions/{version_number}",
    response_model=BrandProfileVersionResponseV1,
    responses=BRAND_PROFILE_ERROR_RESPONSES,
)
def get_brand_profile_version(
    profile_id: CanonicalProfileId,
    version_number: Annotated[int, Path(ge=1)],
    request: Request,
    workspace_id: WorkspaceHeader,
    trusted_principal: PrincipalHeader = None,
) -> BrandProfileVersionResponseV1:
    _workspace_principal(
        request,
        workspace_id=workspace_id,
        trusted_principal=trusted_principal,
    )
    return request.app.state.container.brand_profiles.get_version(
        workspace_id=workspace_id,
        profile_id=profile_id,
        version_number=version_number,
    )


@router.get(
    "/{profile_id}",
    response_model=BrandProfileResponseV1,
    responses=BRAND_PROFILE_ERROR_RESPONSES,
)
def get_brand_profile(
    profile_id: CanonicalProfileId,
    request: Request,
    workspace_id: WorkspaceHeader,
    trusted_principal: PrincipalHeader = None,
) -> BrandProfileResponseV1:
    _workspace_principal(
        request,
        workspace_id=workspace_id,
        trusted_principal=trusted_principal,
    )
    return request.app.state.container.brand_profiles.get(
        workspace_id=workspace_id,
        profile_id=profile_id,
    )
