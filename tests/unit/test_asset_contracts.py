import pytest
from commercevision_contracts import UploadSessionCreateRequestV1
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
