"""Domain errors with transport-independent semantics."""


class DomainError(Exception):
    """Base class for expected domain failures."""


class NotFoundError(DomainError):
    """Requested aggregate does not exist in the caller's scope."""


class ConcurrencyError(DomainError):
    """Optimistic version validation failed."""


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
