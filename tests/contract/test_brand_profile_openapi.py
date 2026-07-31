from __future__ import annotations

import re

from commercevision_api.main import create_app

CANONICAL_UUID_PATTERN = (
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}$"
)


def _parameters(operation: dict[str, object]) -> dict[str, dict[str, object]]:
    values = operation.get("parameters", [])
    assert isinstance(values, list)
    return {str(value["name"]): value for value in values if isinstance(value, dict)}


def test_brand_profile_openapi_exposes_strict_versioned_publication_contract() -> None:
    generated = create_app().openapi()
    paths = generated["paths"]
    expected = {
        ("/api/v1/brand-profiles", "post"),
        ("/api/v1/brand-profiles", "get"),
        ("/api/v1/brand-profiles/{profile_id}", "get"),
        ("/api/v1/brand-profiles/{profile_id}/draft", "put"),
        ("/api/v1/brand-profiles/{profile_id}:validate", "post"),
        ("/api/v1/brand-profiles/{profile_id}:publish", "post"),
        ("/api/v1/brand-profiles/{profile_id}/versions", "get"),
        (
            "/api/v1/brand-profiles/{profile_id}/versions/{version_number}",
            "get",
        ),
    }
    for path, method in expected:
        assert method in paths[path]
        responses = paths[path][method]["responses"]
        assert {"500", "503"}.issubset(responses)
        assert responses["500"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }
        assert responses["503"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }

    schemas = generated["components"]["schemas"]
    for name in (
        "BrandRuleV1",
        "BrandColorV1",
        "BrandProfileMemberSelectionV1",
        "BrandProfileDraftV1",
        "BrandProfileCreateRequestV1",
        "BrandProfileUpdateDraftRequestV1",
        "BrandProfileValidateRequestV1",
        "BrandProfilePublishRequestV1",
        "BrandProfileResponseV1",
        "BrandProfileValidationIssueV1",
        "BrandProfileValidationResponseV1",
        "BrandProfilePublishedMemberV1",
        "BrandProfileVersionResponseV1",
        "BrandProfileListResponseV1",
        "BrandProfileVersionListResponseV1",
    ):
        assert schemas[name]["additionalProperties"] is False

    create = paths["/api/v1/brand-profiles"]["post"]
    update = paths["/api/v1/brand-profiles/{profile_id}/draft"]["put"]
    validate = paths["/api/v1/brand-profiles/{profile_id}:validate"]["post"]
    publish = paths["/api/v1/brand-profiles/{profile_id}:publish"]["post"]
    assert "201" in publish["responses"]
    assert "200" not in publish["responses"]
    for mutation in (create, update, publish):
        parameters = _parameters(mutation)
        assert parameters["Idempotency-Key"]["required"] is True
        assert parameters["X-Actor-Id"]["required"] is True
        assert {"401", "403", "404", "409", "422"}.issubset(mutation["responses"])
    assert "Idempotency-Key" not in _parameters(validate)
    assert _parameters(validate)["X-Actor-Id"]["required"] is True

    profile_schema = schemas["BrandProfileResponseV1"]["properties"]
    assert "members" not in profile_schema
    assert "currently_usable" not in profile_schema
    member_schema = schemas["BrandProfilePublishedMemberV1"]["properties"]
    assert {
        "published_rights_record_id",
        "published_rights_record_version",
        "currently_usable",
        "current_reason_code",
        "current_rights_record_id",
        "current_rights_record_version",
        "decided_at",
    }.issubset(member_schema)

    profile_list = paths["/api/v1/brand-profiles"]["get"]
    version_list = paths["/api/v1/brand-profiles/{profile_id}/versions"]["get"]
    for operation in (profile_list, version_list):
        parameters = _parameters(operation)
        assert parameters["limit"]["schema"]["minimum"] == 1
        assert parameters["limit"]["schema"]["maximum"] == 100
        assert parameters["cursor"]["required"] is False

    for path, method in expected:
        if "{profile_id}" not in path:
            continue
        operation = paths[path][method]
        pattern = _parameters(operation)["profile_id"]["schema"]["pattern"]
        assert pattern == CANONICAL_UUID_PATTERN
        assert re.fullmatch(
            pattern,
            "019f8a00-0000-7000-8000-000000000041",
        )
        assert (
            re.fullmatch(
                pattern,
                "019F8A00-0000-7000-8000-000000000041",
            )
            is None
        )
        assert "422" in operation["responses"]
