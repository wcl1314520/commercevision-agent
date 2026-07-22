"""FastAPI exception mapping for stable public error contracts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from commercevision_contracts import ErrorResponse
from commercevision_domain import (
    ConcurrencyError,
    DomainError,
    DuplicateExternalIdentifierError,
    InvalidTransitionError,
    LeaseConflictError,
    NotFoundError,
)
from commercevision_domain.workflow.errors import (
    ApprovalConflictError,
    IdempotencyConflictError,
    RetryNotReadyError,
)
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def _json_safe(value: Any) -> Any:
    if isinstance(value, BaseException):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value


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
            details={"errors": _json_safe(exc.errors())},
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


def _classification(exc: DomainError) -> tuple[int, str, str, bool]:
    if isinstance(exc, NotFoundError):
        return 404, "NOT_FOUND", "not_found", False
    if isinstance(exc, IdempotencyConflictError):
        return 409, "IDEMPOTENCY_CONFLICT", "conflict", False
    if isinstance(exc, DuplicateExternalIdentifierError):
        return 409, "DUPLICATE_EXTERNAL_IDENTIFIER", "conflict", False
    if isinstance(exc, (ConcurrencyError, ApprovalConflictError)):
        return 409, "VERSION_CONFLICT", "conflict", False
    if isinstance(exc, InvalidTransitionError):
        return 409, "INVALID_TRANSITION", "state", False
    if isinstance(exc, (LeaseConflictError, RetryNotReadyError)):
        return 409, "EXECUTION_BUSY", "transient", True
    return 422, "DOMAIN_ERROR", "domain", False
