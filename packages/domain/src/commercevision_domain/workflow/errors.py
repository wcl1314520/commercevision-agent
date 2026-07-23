"""Domain errors with transport-independent semantics."""


class DomainError(Exception):
    """Base class for expected domain failures."""


class NotFoundError(DomainError):
    """Requested aggregate does not exist in the caller's scope."""


class ConcurrencyError(DomainError):
    """Optimistic version validation failed."""


class DataIntegrityError(DomainError):
    """Persisted data violates a database-enforced invariant."""


class UniqueConstraintError(DataIntegrityError):
    """A unique logical or physical identity already exists."""


class ReferenceConstraintError(DataIntegrityError):
    """A referenced durable record does not exist or cannot be changed."""


class InvalidDataError(DataIntegrityError):
    """Data is null, malformed, out of range, or violates a check."""


class InvalidTransitionError(DomainError):
    """A state transition is not part of the declared state machine."""


class LeaseConflictError(DomainError):
    """A valid lease is owned by another executor."""


class IdempotencyConflictError(DomainError):
    """An idempotency key was reused with a different request."""


class ApprovalConflictError(DomainError):
    """Approval type, subject, decision, or workflow state is incompatible."""


class RetryNotReadyError(DomainError):
    """A retry was requested before its scheduled availability."""


class RetryExhaustedError(DomainError):
    """A step or message exhausted its retry budget."""


class AuthorizationError(DomainError):
    """The current identity is not permitted to perform the requested action."""


class AuthenticationError(AuthorizationError):
    """No valid trusted principal was supplied."""


class WorkspaceAccessError(AuthorizationError):
    """The principal is not a member of the requested workspace."""


class AdminRequiredError(AuthorizationError):
    """The principal is not an administrator of the requested workspace."""
