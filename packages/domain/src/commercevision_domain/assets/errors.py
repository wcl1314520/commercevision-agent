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


class StoragePreconditionError(DomainError):
    pass
