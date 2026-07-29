"""Deny-by-default policy for authorized ProductBrief Vision transfer."""

from __future__ import annotations

from dataclasses import dataclass, field

from commercevision_contracts import Settings, validate_canonical_endpoint_host
from commercevision_domain import RetentionClass

from .asset_idempotency import canonical_hash

VISION_ANALYSIS_PURPOSE = "VISION_ANALYSIS"
_POLICY_SCHEMA_VERSION = "commercevision.vision-data-transfer-policy.v1"


@dataclass(frozen=True, slots=True)
class VisionDataTransferDenied(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class VisionDataTransferAuthorization:
    asset_version_id: str
    policy_version: str
    policy_snapshot_sha256: str
    provider: str
    endpoint_region: str
    endpoint_host: str
    purpose: str


@dataclass(frozen=True, slots=True)
class VisionDataTransferPolicy:
    enabled: bool
    version: str
    allowed_workspace_ids: frozenset[str]
    allowed_retention_classes: frozenset[RetentionClass]
    allowed_providers: frozenset[str]
    allowed_endpoint_regions: frozenset[str]
    allowed_endpoint_hosts: frozenset[str]
    snapshot_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        if not self.version or len(self.version) > 64:
            raise ValueError("Vision data transfer policy version is invalid")
        for values in (
            self.allowed_workspace_ids,
            self.allowed_providers,
            self.allowed_endpoint_regions,
        ):
            if any(not value or len(value) > 128 or value != value.strip() for value in values):
                raise ValueError("Vision data transfer allowlist contains invalid values")
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
            raise ValueError("enabled Vision data transfer policy requires every allowlist")
        object.__setattr__(
            self,
            "snapshot_sha256",
            canonical_hash(
                {
                    "allowed_endpoint_hosts": sorted(self.allowed_endpoint_hosts),
                    "allowed_endpoint_regions": sorted(self.allowed_endpoint_regions),
                    "allowed_providers": sorted(self.allowed_providers),
                    "allowed_retention_classes": sorted(
                        value.value for value in self.allowed_retention_classes
                    ),
                    "allowed_workspace_ids": sorted(self.allowed_workspace_ids),
                    "enabled": self.enabled,
                    "purpose": VISION_ANALYSIS_PURPOSE,
                    "schema_version": _POLICY_SCHEMA_VERSION,
                    "version": self.version,
                }
            ),
        )

    @classmethod
    def deny_all(cls) -> VisionDataTransferPolicy:
        return cls(
            enabled=False,
            version="vision-transfer-deny-v1",
            allowed_workspace_ids=frozenset(),
            allowed_retention_classes=frozenset(),
            allowed_providers=frozenset(),
            allowed_endpoint_regions=frozenset(),
            allowed_endpoint_hosts=frozenset(),
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> VisionDataTransferPolicy:
        return cls(
            enabled=settings.vision_data_transfer_enabled,
            version=settings.vision_data_transfer_policy_version,
            allowed_workspace_ids=frozenset(settings.vision_data_transfer_allowed_workspace_ids),
            allowed_retention_classes=frozenset(
                settings.vision_data_transfer_allowed_retention_classes
            ),
            allowed_providers=frozenset(settings.vision_data_transfer_allowed_providers),
            allowed_endpoint_regions=frozenset(
                settings.vision_data_transfer_allowed_endpoint_regions
            ),
            allowed_endpoint_hosts=frozenset(settings.vision_data_transfer_allowed_endpoint_hosts),
        )

    def authorize(
        self,
        *,
        persisted_policy_version: str,
        persisted_policy_snapshot_sha256: str,
        workspace_id: str,
        asset_version_id: str,
        retention_class: RetentionClass,
        provider: str,
        endpoint_region: str,
        endpoint_host: str,
        purpose: str,
    ) -> VisionDataTransferAuthorization:
        checks = (
            (
                persisted_policy_version == self.version
                and persisted_policy_snapshot_sha256 == self.snapshot_sha256,
                "VISION_TRANSFER_POLICY_MISMATCH",
                "Vision data transfer policy was revoked or changed",
            ),
            (
                self.enabled,
                "VISION_TRANSFER_DISABLED",
                "Vision data transfer is disabled",
            ),
            (
                purpose == VISION_ANALYSIS_PURPOSE,
                "VISION_TRANSFER_PURPOSE_DENIED",
                "Vision data transfer purpose is not authorized",
            ),
            (
                workspace_id in self.allowed_workspace_ids,
                "VISION_TRANSFER_WORKSPACE_DENIED",
                "workspace is not authorized for Vision data transfer",
            ),
            (
                retention_class in self.allowed_retention_classes,
                "VISION_TRANSFER_RETENTION_DENIED",
                "retention class is not authorized for Vision data transfer",
            ),
            (
                provider in self.allowed_providers,
                "VISION_TRANSFER_PROVIDER_DENIED",
                "provider is not authorized for Vision data transfer",
            ),
            (
                endpoint_region in self.allowed_endpoint_regions,
                "VISION_TRANSFER_REGION_DENIED",
                "provider region is not authorized for Vision data transfer",
            ),
            (
                endpoint_host in self.allowed_endpoint_hosts,
                "VISION_TRANSFER_ENDPOINT_DENIED",
                "provider endpoint is not authorized for Vision data transfer",
            ),
            (
                bool(asset_version_id),
                "VISION_TRANSFER_ASSET_VERSION_DENIED",
                "Asset Version identity is required for Vision data transfer",
            ),
        )
        for permitted, code, message in checks:
            if not permitted:
                raise VisionDataTransferDenied(code=code, message=message)
        return VisionDataTransferAuthorization(
            asset_version_id=asset_version_id,
            policy_version=self.version,
            policy_snapshot_sha256=self.snapshot_sha256,
            provider=provider,
            endpoint_region=endpoint_region,
            endpoint_host=endpoint_host,
            purpose=purpose,
        )
