from commercevision_api.errors import _classification
from commercevision_domain import (
    DuplicateExternalIdentifierError,
    InvalidDataError,
    ReferenceConstraintError,
    RightsDeniedError,
    UniqueConstraintError,
)


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
