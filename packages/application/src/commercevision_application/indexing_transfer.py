"""Deny-by-default policy for IMAGE embedding data transfer."""

from __future__ import annotations

from dataclasses import dataclass

from commercevision_contracts import Settings, validate_canonical_endpoint_host
from commercevision_domain import RetentionClass


@dataclass(frozen=True, slots=True)
class ImageIndexDataTransferDenied(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class ImageIndexDataTransferPolicy:
    enabled: bool
    version: str
    allowed_workspace_ids: frozenset[str]
    allowed_retention_classes: frozenset[RetentionClass]
    allowed_providers: frozenset[str]
    allowed_endpoint_regions: frozenset[str]
    allowed_endpoint_hosts: frozenset[str]

    def __post_init__(self) -> None:
        if not self.version or len(self.version) > 64 or self.version != self.version.strip():
            raise ValueError("IMAGE index data transfer policy version is invalid")
        for values in (
            self.allowed_workspace_ids,
            self.allowed_providers,
            self.allowed_endpoint_regions,
        ):
            if any(not value or len(value) > 128 or value != value.strip() for value in values):
                raise ValueError("IMAGE index data transfer allowlist contains invalid values")
        for endpoint_host in self.allowed_endpoint_hosts:
            validate_canonical_endpoint_host(endpoint_host)
        if self.enabled and any(
            not values
            for values in (
                self.allowed_workspace_ids,
                self.allowed_retention_classes,
                self.allowed_providers,
                self.allowed_endpoint_regions,
                self.allowed_endpoint_hosts,
            )
        ):
            raise ValueError("enabled IMAGE index data transfer requires every allowlist")

    @classmethod
    def from_settings(cls, settings: Settings) -> ImageIndexDataTransferPolicy:
        return cls(
            enabled=settings.embedding_data_transfer_enabled,
            version=settings.embedding_data_transfer_policy_version,
            allowed_workspace_ids=frozenset(settings.embedding_data_transfer_allowed_workspace_ids),
            allowed_retention_classes=frozenset(
                settings.embedding_data_transfer_allowed_retention_classes
            ),
            allowed_providers=frozenset(settings.embedding_data_transfer_allowed_providers),
            allowed_endpoint_regions=frozenset(
                settings.embedding_data_transfer_allowed_endpoint_regions
            ),
            allowed_endpoint_hosts=frozenset(
                settings.embedding_data_transfer_allowed_endpoint_hosts
            ),
        )

    def authorize(
        self,
        *,
        workspace_id: str,
        retention_class: RetentionClass,
        provider: str,
        endpoint_region: str,
        endpoint_host: str,
    ) -> None:
        checks = (
            (self.enabled, "EMBEDDING_TRANSFER_DISABLED"),
            (
                workspace_id in self.allowed_workspace_ids,
                "EMBEDDING_TRANSFER_WORKSPACE_DENIED",
            ),
            (
                retention_class in self.allowed_retention_classes,
                "EMBEDDING_TRANSFER_RETENTION_DENIED",
            ),
            (provider in self.allowed_providers, "EMBEDDING_TRANSFER_PROVIDER_DENIED"),
            (
                endpoint_region in self.allowed_endpoint_regions,
                "EMBEDDING_TRANSFER_REGION_DENIED",
            ),
            (
                endpoint_host in self.allowed_endpoint_hosts,
                "EMBEDDING_TRANSFER_ENDPOINT_DENIED",
            ),
        )
        for permitted, code in checks:
            if not permitted:
                raise ImageIndexDataTransferDenied(
                    code=code,
                    message="IMAGE embedding data transfer is not authorized",
                )
