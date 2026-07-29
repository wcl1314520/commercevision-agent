"""Stable Asset Registry failures."""

from commercevision_domain.workflow.errors import DomainError


class UploadExpiredError(DomainError):
    pass


class UploadAbortedError(DomainError):
    pass


class UploadBusyError(DomainError):
    pass


class UploadObjectMissingError(DomainError):
    pass


class ObjectMismatchError(DomainError):
    pass


class UnsupportedAssetKindError(DomainError):
    pass


class StorageUnavailableError(DomainError):
    pass


class StorageWriteSafeToRetryError(StorageUnavailableError):
    """A conditional object write was proven not to have been attempted."""


class StorageWriteOutcomeUnknownError(StorageUnavailableError):
    """A conditional object write may have committed and needs reconciliation."""


class StoragePreconditionError(DomainError):
    pass
