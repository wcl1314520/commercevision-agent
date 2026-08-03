import pytest
from commercevision_contracts import AssetDeleteRequestV1, UploadSessionCreateRequestV1
from pydantic import ValidationError


def _foundation_request(**overrides: object) -> UploadSessionCreateRequestV1:
    values: dict[str, object] = {
        "retention_class": "FOUNDATION",
        "asset_kind": "IMAGE",
        "filename": "product.png",
        "declared_mime": "image/png",
        "byte_length": 68,
        "sha256": "a" * 64,
        "workflow_id": None,
        "product_id": None,
        "sku_id": None,
        "category": "beauty.skincare",
        "role": "product-primary",
    }
    values.update(overrides)
    return UploadSessionCreateRequestV1.model_validate(values)


def test_upload_association_ids_are_canonicalized() -> None:
    request = _foundation_request(
        product_id="019F8A00-0000-7000-8000-000000000001",
        sku_id="019F8A00-0000-7000-8000-000000000002",
    )

    assert request.product_id == "019f8a00-0000-7000-8000-000000000001"
    assert request.sku_id == "019f8a00-0000-7000-8000-000000000002"


@pytest.mark.parametrize(
    "product_id",
    [
        "019f8á00-0000-7000-8000-000000000001",
        "019f8a00000070008000000000000001",
        "{019f8a00-0000-7000-8000-000000000001}",
    ],
)
def test_upload_association_ids_reject_noncanonical_uuid_text(product_id: str) -> None:
    with pytest.raises(ValidationError):
        _foundation_request(product_id=product_id)


@pytest.mark.parametrize(
    ("asset_kind", "filename", "declared_mime", "byte_length"),
    [
        (
            "LORA",
            "style.safetensors",
            "application/x-safetensors",
            100 * 1024 * 1024,
        ),
        (
            "PROMPT_TEMPLATE",
            "listing.prompt.json",
            "application/json",
            256 * 1024,
        ),
        (
            "MODEL_CONFIGURATION",
            "generator.model.json",
            "application/json",
            64 * 1024,
        ),
    ],
)
def test_foundation_registration_contract_accepts_kind_specific_maximum(
    asset_kind: str,
    filename: str,
    declared_mime: str,
    byte_length: int,
) -> None:
    request = _foundation_request(
        asset_kind=asset_kind,
        filename=filename,
        declared_mime=declared_mime,
        byte_length=byte_length,
    )

    assert request.asset_kind.value == asset_kind
    assert request.byte_length == byte_length


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "asset_kind": "LORA",
            "filename": "style.safetensors",
            "declared_mime": "application/x-safetensors",
            "byte_length": 100 * 1024 * 1024 + 1,
        },
        {
            "asset_kind": "PROMPT_TEMPLATE",
            "filename": "listing.prompt.json",
            "declared_mime": "application/json",
            "byte_length": 256 * 1024 + 1,
        },
        {
            "asset_kind": "MODEL_CONFIGURATION",
            "filename": "generator.model.json",
            "declared_mime": "application/json",
            "byte_length": 64 * 1024 + 1,
        },
        {
            "retention_class": "TASK",
            "asset_kind": "LORA",
            "filename": "style.safetensors",
            "declared_mime": "application/x-safetensors",
            "workflow_id": "019f8a00-0000-7000-8000-000000000010",
        },
        {
            "asset_kind": "PROMPT_TEMPLATE",
            "filename": "listing.prompt.json",
            "declared_mime": "text/plain",
        },
    ],
)
def test_registration_contract_rejects_unsafe_kind_policy(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _foundation_request(**overrides)


@pytest.mark.parametrize("expected_version", [True, 3.0, "3"])
def test_administrator_deletion_requires_a_strict_integer_version(
    expected_version: object,
) -> None:
    with pytest.raises(ValidationError):
        AssetDeleteRequestV1.model_validate({"expected_version": expected_version})
