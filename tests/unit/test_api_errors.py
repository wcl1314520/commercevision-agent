from commercevision_api.errors import _classification, install_error_handlers
from commercevision_domain import (
    DuplicateExternalIdentifierError,
    InvalidDataError,
    ReferenceConstraintError,
    RightsDeniedError,
    UniqueConstraintError,
)
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field


def test_integrity_errors_have_stable_non_retryable_api_classification() -> None:
    assert _classification(UniqueConstraintError("database unique constraint was violated")) == (
        409,
        "UNIQUE_CONSTRAINT_CONFLICT",
        "conflict",
        False,
    )
    assert _classification(
        ReferenceConstraintError("database reference constraint was violated")
    ) == (
        409,
        "REFERENCE_CONSTRAINT_CONFLICT",
        "conflict",
        False,
    )
    assert _classification(InvalidDataError("database rejected invalid data")) == (
        422,
        "INVALID_DATA",
        "validation",
        False,
    )
    assert _classification(DuplicateExternalIdentifierError("duplicate external identity")) == (
        409,
        "DUPLICATE_EXTERNAL_IDENTIFIER",
        "conflict",
        False,
    )


def test_rights_denial_has_a_stable_authorization_code() -> None:
    assert _classification(RightsDeniedError("provider is not permitted")) == (
        403,
        "RIGHTS_DENIED",
        "authorization",
        False,
    )


def test_unhandled_exception_returns_stable_non_leaking_error_envelope() -> None:
    app = FastAPI()
    install_error_handlers(app)

    @app.middleware("http")
    async def correlation_context(request: Request, call_next):
        request.state.request_id = "request-safe"
        request.state.trace_id = "trace-safe"
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        response.headers["X-Trace-Id"] = request.state.trace_id
        return response

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("provider-secret-and-stack-detail")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/boom")

    assert response.status_code == 500
    assert response.json() == {
        "code": "INTERNAL_ERROR",
        "message": "an unexpected internal error occurred",
        "category": "internal",
        "retryable": False,
        "details": {},
        "request_id": "request-safe",
        "trace_id": "trace-safe",
    }
    assert "provider-secret-and-stack-detail" not in response.text
    assert response.headers["X-Request-Id"] == "request-safe"
    assert response.headers["X-Trace-Id"] == "trace-safe"


def test_request_validation_error_does_not_echo_invalid_input() -> None:
    app = FastAPI()
    install_error_handlers(app)

    @app.middleware("http")
    async def correlation_context(request: Request, call_next):
        request.state.request_id = "request-validation-safe"
        request.state.trace_id = "trace-validation-safe"
        return await call_next(request)

    class SensitivePayload(BaseModel):
        product_text: str = Field(min_length=256)

    @app.post("/validate")
    def validate_payload(payload: SensitivePayload) -> None:
        del payload

    sensitive_input = "signed-object-location?credential=must-not-leak"
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/validate", json={"product_text": sensitive_input})

    assert response.status_code == 422
    assert sensitive_input not in response.text
    assert response.json()["details"] == {
        "errors": [
            {
                "type": "string_too_short",
                "loc": ["body", "product_text"],
                "msg": "String should have at least 256 characters",
            }
        ]
    }
