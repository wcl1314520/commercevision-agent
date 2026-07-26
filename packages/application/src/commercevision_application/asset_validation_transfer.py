"""Narrow, deny-by-default authorization for validation-only data transfer."""

from __future__ import annotations

from dataclasses import dataclass, field

from commercevision_contracts import Settings, validate_canonical_endpoint_host
from commercevision_domain import AssetKind, RetentionClass

from .asset_idempotency import canonical_hash

SECURITY_VALIDATION_PURPOSE = "SECURITY_VALIDATION"
_POLICY_SCHEMA_VERSION = "commercevision.validation-data-transfer-policy.v2"


@dataclass(frozen=True, slots=True)
class ValidationDataTransferDenied(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class ValidationDataTransferAuthorization:
    asset_version_id: str
    policy_version: str
    policy_snapshot_sha256: str
    provider: str
    endpoint_region: str
    endpoint_host: str
    purpose: str


@dataclass(frozen=True, slots=True)
class ValidationDataTransferPolicy:
    """Authorize only an exact security-validation transfer scope."""

    enabled: bool
    version: str
    allowed_workspace_ids: frozenset[str]
    allowed_asset_kinds: frozenset[AssetKind]
    allowed_retention_classes: frozenset[RetentionClass]
    allowed_providers: frozenset[str]
    allowed_endpoint_regions: frozenset[str]
    allowed_endpoint_hosts: frozenset[str]
    snapshot_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        if not self.version or len(self.version) > 64:
            raise ValueError("validation data transfer policy version is invalid")
        values = (
            self.allowed_workspace_ids,
            self.allowed_providers,
            self.allowed_endpoint_regions,
        )
        if any(
            not value or len(value) > 128 or value != value.strip()
            for allowed in values
            for value in allowed
        ):
            raise ValueError("validation data transfer allowlists contain invalid values")
        for endpoint_host in self.allowed_endpoint_hosts:
            validate_canonical_endpoint_host(endpoint_host)
        if self.enabled and any(
            not allowed
            for allowed in (
                self.allowed_workspace_ids,
                self.allowed_asset_kinds,
                self.allowed_retention_classes,
                self.allowed_providers,
                self.allowed_endpoint_regions,
                self.allowed_endpoint_hosts,
            )
        ):
            raise ValueError("enabled validation data transfer policy requires every allowlist")
        object.__setattr__(
            self,
            "snapshot_sha256",
            canonical_hash(
                {
                    "allowed_asset_kinds": sorted(kind.value for kind in self.allowed_asset_kinds),
                    "allowed_endpoint_hosts": sorted(self.allowed_endpoint_hosts),
                    "allowed_endpoint_regions": sorted(self.allowed_endpoint_regions),
                    "allowed_providers": sorted(self.allowed_providers),
                    "allowed_retention_classes": sorted(
                        retention.value for retention in self.allowed_retention_classes
                    ),
                    "allowed_workspace_ids": sorted(self.allowed_workspace_ids),
                    "enabled": self.enabled,
                    "purpose": SECURITY_VALIDATION_PURPOSE,
                    "schema_version": _POLICY_SCHEMA_VERSION,
                    "version": self.version,
                }
            ),
        )

    @classmethod
    def deny_all(cls) -> ValidationDataTransferPolicy:
        return cls(
            enabled=False,
            version="validation-transfer-deny-v1",
            allowed_workspace_ids=frozenset(),
            allowed_asset_kinds=frozenset(),
            allowed_retention_classes=frozenset(),
            allowed_providers=frozenset(),
            allowed_endpoint_regions=frozenset(),
            allowed_endpoint_hosts=frozenset(),
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> ValidationDataTransferPolicy:
        return cls(
            enabled=settings.validation_data_transfer_enabled,
            version=settings.validation_data_transfer_policy_version,
            allowed_workspace_ids=frozenset(
                settings.validation_data_transfer_allowed_workspace_ids
            ),
            allowed_asset_kinds=frozenset(settings.validation_data_transfer_allowed_asset_kinds),
            allowed_retention_classes=frozenset(
                settings.validation_data_transfer_allowed_retention_classes
            ),
            allowed_providers=frozenset(settings.validation_data_transfer_allowed_providers),
            allowed_endpoint_regions=frozenset(
                settings.validation_data_transfer_allowed_endpoint_regions
            ),
            allowed_endpoint_hosts=frozenset(
                settings.validation_data_transfer_allowed_endpoint_hosts
            ),
        )

    def authorize(
        self,
        *,
        persisted_policy_version: str,
        persisted_policy_snapshot_sha256: str,
        workspace_id: str,
        asset_version_id: str,
        asset_kind: AssetKind,
        retention_class: RetentionClass,
        provider: str,
        endpoint_region: str,
        endpoint_host: str,
        purpose: str,
    ) -> ValidationDataTransferAuthorization:
        if (
            persisted_policy_version != self.version
            or persisted_policy_snapshot_sha256 != self.snapshot_sha256
        ):
            self._deny(
                "VALIDATION_TRANSFER_POLICY_MISMATCH",
                "validation data transfer policy was revoked or changed",
            )
        if not self.enabled:
            self._deny(
                "VALIDATION_TRANSFER_DISABLED",
                "validation data transfer is disabled",
            )
        if purpose != SECURITY_VALIDATION_PURPOSE:
            self._deny(
                "VALIDATION_TRANSFER_PURPOSE_DENIED",
                "validation data transfer purpose is not authorized",
            )
        if workspace_id not in self.allowed_workspace_ids:
            self._deny(
                "VALIDATION_TRANSFER_WORKSPACE_DENIED",
                "workspace is not authorized for validation data transfer",
            )
        if asset_kind not in self.allowed_asset_kinds:
            self._deny(
                "VALIDATION_TRANSFER_ASSET_KIND_DENIED",
                "Asset kind is not authorized for validation data transfer",
            )
        if retention_class not in self.allowed_retention_classes:
            self._deny(
                "VALIDATION_TRANSFER_RETENTION_DENIED",
                "Asset retention class is not authorized for validation data transfer",
            )
        if provider not in self.allowed_providers:
            self._deny(
                "VALIDATION_TRANSFER_PROVIDER_DENIED",
                "provider is not authorized for validation data transfer",
            )
        if endpoint_region not in self.allowed_endpoint_regions:
            self._deny(
                "VALIDATION_TRANSFER_REGION_DENIED",
                "provider region is not authorized for validation data transfer",
            )
        if endpoint_host not in self.allowed_endpoint_hosts:
            self._deny(
                "VALIDATION_TRANSFER_ENDPOINT_DENIED",
                "provider endpoint is not authorized for validation data transfer",
            )
        if not asset_version_id:
            self._deny(
                "VALIDATION_TRANSFER_ASSET_VERSION_DENIED",
                "Asset Version identity is required for validation data transfer",
            )
        return ValidationDataTransferAuthorization(
            asset_version_id=asset_version_id,
            policy_version=self.version,
            policy_snapshot_sha256=self.snapshot_sha256,
            provider=provider,
            endpoint_region=endpoint_region,
            endpoint_host=endpoint_host,
            purpose=purpose,
        )

    @staticmethod
    def _deny(code: str, message: str) -> None:
        raise ValidationDataTransferDenied(code=code, message=message)
