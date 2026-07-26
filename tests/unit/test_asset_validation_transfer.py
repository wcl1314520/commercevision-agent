from __future__ import annotations

import pytest
from commercevision_application.asset_validation_transfer import (
    SECURITY_VALIDATION_PURPOSE,
    ValidationDataTransferDenied,
    ValidationDataTransferPolicy,
)
from commercevision_domain import AssetKind, RetentionClass, new_uuid7


def _enabled_policy() -> ValidationDataTransferPolicy:
    return ValidationDataTransferPolicy(
        enabled=True,
        version="enterprise-security-validation-v3",
        allowed_workspace_ids=frozenset({"transfer-workspace"}),
        allowed_asset_kinds=frozenset({AssetKind.IMAGE}),
        allowed_retention_classes=frozenset({RetentionClass.TASK, RetentionClass.FOUNDATION}),
        allowed_providers=frozenset({"alibaba-green"}),
        allowed_endpoint_regions=frozenset({"cn-shanghai"}),
        allowed_endpoint_hosts=frozenset({"green-cip.cn-shanghai.aliyuncs.com"}),
    )


def test_validation_transfer_workspace_identity_is_binary_exact() -> None:
    policy = ValidationDataTransferPolicy(
        enabled=True,
        version="enterprise-security-validation-v3",
        allowed_workspace_ids=frozenset({"Catalog-A"}),
        allowed_asset_kinds=frozenset({AssetKind.IMAGE}),
        allowed_retention_classes=frozenset({RetentionClass.FOUNDATION}),
        allowed_providers=frozenset({"alibaba-green"}),
        allowed_endpoint_regions=frozenset({"cn-shanghai"}),
        allowed_endpoint_hosts=frozenset({"green-cip.cn-shanghai.aliyuncs.com"}),
    )

    with pytest.raises(ValidationDataTransferDenied) as denied:
        policy.authorize(
            persisted_policy_version=policy.version,
            persisted_policy_snapshot_sha256=policy.snapshot_sha256,
            workspace_id="catalog-a",
            asset_version_id=new_uuid7(),
            asset_kind=AssetKind.IMAGE,
            retention_class=RetentionClass.FOUNDATION,
            provider="alibaba-green",
            endpoint_region="cn-shanghai",
            endpoint_host="green-cip.cn-shanghai.aliyuncs.com",
            purpose=SECURITY_VALIDATION_PURPOSE,
        )

    assert denied.value.code == "VALIDATION_TRANSFER_WORKSPACE_DENIED"


def test_validation_transfer_policy_is_deny_by_default() -> None:
    policy = ValidationDataTransferPolicy.deny_all()

    with pytest.raises(ValidationDataTransferDenied) as denied:
        policy.authorize(
            persisted_policy_version=policy.version,
            persisted_policy_snapshot_sha256=policy.snapshot_sha256,
            workspace_id="transfer-workspace",
            asset_version_id=new_uuid7(),
            asset_kind=AssetKind.IMAGE,
            retention_class=RetentionClass.FOUNDATION,
            provider="alibaba-green",
            endpoint_region="cn-shanghai",
            endpoint_host="green-cip.cn-shanghai.aliyuncs.com",
            purpose=SECURITY_VALIDATION_PURPOSE,
        )

    assert denied.value.code == "VALIDATION_TRANSFER_DISABLED"


def test_validation_transfer_authorization_binds_every_exact_dimension() -> None:
    policy = _enabled_policy()
    asset_version_id = new_uuid7()

    authorization = policy.authorize(
        persisted_policy_version=policy.version,
        persisted_policy_snapshot_sha256=policy.snapshot_sha256,
        workspace_id="transfer-workspace",
        asset_version_id=asset_version_id,
        asset_kind=AssetKind.IMAGE,
        retention_class=RetentionClass.TASK,
        provider="alibaba-green",
        endpoint_region="cn-shanghai",
        endpoint_host="green-cip.cn-shanghai.aliyuncs.com",
        purpose=SECURITY_VALIDATION_PURPOSE,
    )

    assert authorization.asset_version_id == asset_version_id
    assert authorization.policy_version == policy.version
    assert authorization.policy_snapshot_sha256 == policy.snapshot_sha256
    assert authorization.provider == "alibaba-green"
    assert authorization.endpoint_region == "cn-shanghai"
    assert authorization.endpoint_host == "green-cip.cn-shanghai.aliyuncs.com"
    assert authorization.purpose == SECURITY_VALIDATION_PURPOSE


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"workspace_id": "other-workspace"}, "VALIDATION_TRANSFER_WORKSPACE_DENIED"),
        ({"asset_kind": AssetKind.LORA}, "VALIDATION_TRANSFER_ASSET_KIND_DENIED"),
        (
            {"retention_class": RetentionClass.TASK, "provider": "other-provider"},
            "VALIDATION_TRANSFER_PROVIDER_DENIED",
        ),
        ({"endpoint_region": "cn-beijing"}, "VALIDATION_TRANSFER_REGION_DENIED"),
        (
            {"endpoint_host": "collector.example"},
            "VALIDATION_TRANSFER_ENDPOINT_DENIED",
        ),
        ({"purpose": "MODEL_INFERENCE"}, "VALIDATION_TRANSFER_PURPOSE_DENIED"),
        (
            {"persisted_policy_version": "revoked-v2"},
            "VALIDATION_TRANSFER_POLICY_MISMATCH",
        ),
        (
            {"persisted_policy_snapshot_sha256": "f" * 64},
            "VALIDATION_TRANSFER_POLICY_MISMATCH",
        ),
    ],
)
def test_validation_transfer_policy_denies_drift_and_non_allowlisted_scope(
    change: dict[str, object],
    code: str,
) -> None:
    policy = _enabled_policy()
    values: dict[str, object] = {
        "persisted_policy_version": policy.version,
        "persisted_policy_snapshot_sha256": policy.snapshot_sha256,
        "workspace_id": "transfer-workspace",
        "asset_version_id": new_uuid7(),
        "asset_kind": AssetKind.IMAGE,
        "retention_class": RetentionClass.FOUNDATION,
        "provider": "alibaba-green",
        "endpoint_region": "cn-shanghai",
        "endpoint_host": "green-cip.cn-shanghai.aliyuncs.com",
        "purpose": SECURITY_VALIDATION_PURPOSE,
    }
    values.update(change)

    with pytest.raises(ValidationDataTransferDenied) as denied:
        policy.authorize(**values)  # type: ignore[arg-type]

    assert denied.value.code == code


def test_validation_transfer_snapshot_binds_endpoint_host_allowlist() -> None:
    policy = _enabled_policy()
    changed = ValidationDataTransferPolicy(
        enabled=True,
        version=policy.version,
        allowed_workspace_ids=policy.allowed_workspace_ids,
        allowed_asset_kinds=policy.allowed_asset_kinds,
        allowed_retention_classes=policy.allowed_retention_classes,
        allowed_providers=policy.allowed_providers,
        allowed_endpoint_regions=policy.allowed_endpoint_regions,
        allowed_endpoint_hosts=frozenset({"green-cip.cn-beijing.aliyuncs.com"}),
    )

    assert changed.snapshot_sha256 != policy.snapshot_sha256
