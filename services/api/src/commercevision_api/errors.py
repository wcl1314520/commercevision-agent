"""FastAPI exception mapping for stable public error contracts."""

from __future__ import annotations

from commercevision_contracts import ErrorResponse
from commercevision_domain import (
    AdminRequiredError,
    AuthenticationError,
    AuthorizationError,
    ConcurrencyError,
    DomainError,
    DuplicateExternalIdentifierError,
    InvalidDataError,
    InvalidTransitionError,
    LeaseConflictError,
    NotFoundError,
    ObjectMismatchError,
    ProductBriefConfirmationRequiredError,
    ProductBriefRetentionExpiredError,
    ProviderPolicyDeniedError,
    ReferenceConstraintError,
    RightsDeniedError,
    StorageUnavailableError,
    UniqueConstraintError,
    UnsupportedAssetKindError,
    UploadAbortedError,
    UploadBusyError,
    UploadExpiredError,
    UploadObjectMissingError,
    WorkflowCancellationRefusedError,
    WorkspaceAccessError,
)
from commercevision_domain.workflow.errors import (
    ApprovalConflictError,
    IdempotencyConflictError,
    RetryNotReadyError,
)
from commercevision_observability import get_logger
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = get_logger("commercevision.api.errors")


def _public_validation_errors(
    exc: RequestValidationError,
) -> list[dict[str, str | list[str | int]]]:
    return [
        {
            "type": str(error.get("type", "validation_error")),
            "loc": [
                item if isinstance(item, (str, int)) else str(item) for item in error.get("loc", ())
            ],
            "msg": str(error.get("msg", "request value is invalid")),
        }
        for error in exc.errors()
    ]


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def request_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        payload = ErrorResponse(
            code="VALIDATION_ERROR",
            message="request validation failed",
            category="validation",
            retryable=False,
            details={"errors": _public_validation_errors(exc)},
            request_id=request.state.request_id,
            trace_id=request.state.trace_id,
        )
        return JSONResponse(status_code=422, content=payload.model_dump(mode="json"))

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError) -> JSONResponse:
        status_code, code, category, retryable = _classification(exc)
        payload = ErrorResponse(
            code=code,
            message=str(exc),
            category=category,
            retryable=retryable,
            details={},
            request_id=request.state.request_id,
            trace_id=request.state.trace_id,
        )
        return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))

    @app.exception_handler(ValueError)
    async def value_error(request: Request, exc: ValueError) -> JSONResponse:
        payload = ErrorResponse(
            code="INVALID_ARGUMENT",
            message=str(exc),
            category="validation",
            retryable=False,
            details={},
            request_id=request.state.request_id,
            trace_id=request.state.trace_id,
        )
        return JSONResponse(status_code=400, content=payload.model_dump(mode="json"))

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "unavailable")
        trace_id = getattr(request.state, "trace_id", request_id)
        logger.error(
            "unhandled_api_exception",
            request_id=request_id,
            trace_id=trace_id,
            method=request.method,
            path=request.url.path,
            exception_type=type(exc).__name__,
        )
        payload = ErrorResponse(
            code="INTERNAL_ERROR",
            message="an unexpected internal error occurred",
            category="internal",
            retryable=False,
            details={},
            request_id=request_id,
            trace_id=trace_id,
        )
        return JSONResponse(
            status_code=500,
            content=payload.model_dump(mode="json"),
            headers={
                "X-Request-Id": request_id,
                "X-Trace-Id": trace_id,
            },
        )


def _classification(exc: DomainError) -> tuple[int, str, str, bool]:
    if isinstance(exc, AuthenticationError):
        return 401, "AUTHENTICATION_REQUIRED", "authentication", False
    if isinstance(exc, WorkspaceAccessError):
        return 403, "WORKSPACE_ACCESS_DENIED", "authorization", False
    if isinstance(exc, AdminRequiredError):
        return 403, "ADMIN_REQUIRED", "authorization", False
    if isinstance(exc, RightsDeniedError):
        return 403, "RIGHTS_DENIED", "authorization", False
    if isinstance(exc, ProviderPolicyDeniedError):
        return 403, "PROVIDER_POLICY_DENIED", "authorization", False
    if isinstance(exc, AuthorizationError):
        return 403, "AUTHORIZATION_DENIED", "authorization", False
    if isinstance(exc, NotFoundError):
        return 404, "NOT_FOUND", "not_found", False
    if isinstance(exc, UploadExpiredError):
        return 410, "UPLOAD_EXPIRED", "state", False
    if isinstance(exc, UploadAbortedError):
        return 409, "UPLOAD_ABORTED", "state", False
    if isinstance(exc, UploadBusyError):
        return 409, "UPLOAD_BUSY", "transient", True
    if isinstance(exc, UploadObjectMissingError):
        return 409, "UPLOAD_OBJECT_MISSING", "transient", True
    if isinstance(exc, ObjectMismatchError):
        return 422, "OBJECT_MISMATCH", "validation", False
    if isinstance(exc, UnsupportedAssetKindError):
        return 422, "UNSUPPORTED_ASSET_KIND", "validation", False
    if isinstance(exc, StorageUnavailableError):
        return 503, "STORAGE_UNAVAILABLE", "transient", True
    if isinstance(exc, IdempotencyConflictError):
        return 409, "IDEMPOTENCY_CONFLICT", "conflict", False
    if isinstance(exc, DuplicateExternalIdentifierError):
        return 409, "DUPLICATE_EXTERNAL_IDENTIFIER", "conflict", False
    if isinstance(exc, UniqueConstraintError):
        return 409, "UNIQUE_CONSTRAINT_CONFLICT", "conflict", False
    if isinstance(exc, ReferenceConstraintError):
        return 409, "REFERENCE_CONSTRAINT_CONFLICT", "conflict", False
    if isinstance(exc, InvalidDataError):
        return 422, "INVALID_DATA", "validation", False
    if isinstance(exc, (ConcurrencyError, ApprovalConflictError)):
        return 409, "VERSION_CONFLICT", "conflict", False
    if isinstance(exc, InvalidTransitionError):
        return 409, "INVALID_TRANSITION", "state", False
    if isinstance(exc, ProductBriefConfirmationRequiredError):
        return 409, "PRODUCT_BRIEF_CONFIRMATION_REQUIRED", "state", False
    if isinstance(exc, ProductBriefRetentionExpiredError):
        return 410, "PRODUCT_BRIEF_RETENTION_EXPIRED", "state", False
    if isinstance(exc, WorkflowCancellationRefusedError):
        return 409, "WORKFLOW_CANCELLATION_REFUSED", "state", False
    if isinstance(exc, (LeaseConflictError, RetryNotReadyError)):
        return 409, "EXECUTION_BUSY", "transient", True
    return 422, "DOMAIN_ERROR", "domain", False
