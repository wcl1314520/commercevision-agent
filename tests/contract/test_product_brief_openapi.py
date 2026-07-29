import json
from pathlib import Path

from commercevision_api.main import create_app
from commercevision_contracts.product_briefs import ProductBriefFieldOutput

EXPECTED_FIELD_VALUE_KINDS = {
    "automotive.certification_marks": "STATEMENT_LIST",
    "automotive.compatibility_evidence": "STATEMENT_LIST",
    "automotive.dimensions_evidence": "DIMENSION_LIST",
    "automotive.finish": "TEXT",
    "automotive.installation_evidence": "STATEMENT_LIST",
    "automotive.material": "TEXT",
    "automotive.part_type": "TEXT",
    "automotive.placement": "TEXT",
    "automotive.safety_critical_claim_flags": "FLAG_LIST",
    "beauty.cosmetic_form": "TEXT",
    "beauty.finish": "TEXT",
    "beauty.ingredient_claim_evidence": "STATEMENT_LIST",
    "beauty.medical_like_claim_flags": "FLAG_LIST",
    "beauty.package_type": "TEXT",
    "beauty.packaging_compliance_notes": "TEXT",
    "beauty.shade_evidence": "STATEMENT_LIST",
    "beauty.skin_hair_claim_flags": "FLAG_LIST",
    "beauty.texture": "TEXT",
    "common.brand": "TEXT",
    "common.category": "CATEGORY",
    "common.colors": "TEXT_LIST",
    "common.identity": "IDENTITY",
    "common.material": "TEXT",
    "common.package_or_part_form": "TEXT",
    "common.product_type": "TEXT",
    "common.prohibited_assumptions": "TEXT_LIST",
    "common.sensitive_claims": "STATEMENT_LIST",
    "common.source_conflicts": "STATEMENT_LIST",
    "common.usage_context": "TEXT",
    "common.visible_text_summary": "TEXT",
    "common.visual_features": "TEXT_LIST",
}
FIELD_VALUE_SCHEMA_NAMES = {
    "ProductBriefIdentityValueV1",
    "ProductBriefCategoryValueV1",
    "ProductBriefTextValueV1",
    "ProductBriefTextListValueV1",
    "ProductBriefStatementListValueV1",
    "ProductBriefFlagListValueV1",
    "ProductBriefDimensionListValueV1",
}


def _parameters_by_name(operation: dict[str, object]) -> dict[str, dict[str, object]]:
    parameters = operation.get("parameters", [])
    assert isinstance(parameters, list)
    return {
        str(parameter["name"]): parameter for parameter in parameters if isinstance(parameter, dict)
    }


def test_product_brief_openapi_is_versioned_and_committed() -> None:
    root = Path(__file__).parents[2]
    committed = json.loads((root / "docs/api/openapi.json").read_text("utf-8"))
    generated = create_app().openapi()

    paths = generated["paths"]
    assert paths["/api/v1/product-briefs:analyze"]["post"]["responses"]["202"]
    assert paths["/api/v1/product-briefs/{product_brief_id}"]["get"]
    assert paths["/api/v1/product-briefs/{product_brief_id}/versions"]["get"]
    assert paths["/api/v1/product-briefs/{product_brief_id}:revise"]["post"]
    assert paths["/api/v1/product-briefs/{product_brief_id}:confirm"]["post"]
    assert paths["/api/v1/product-briefs/analysis-workflow-context/{workflow_id}"]["get"]
    assert paths["/api/v1/product-briefs/workflow-context/{workflow_id}"]["get"]
    assert paths["/api/v1/product-briefs/{product_brief_id}/operations/{operation_id}"]["get"]

    schemas = generated["components"]["schemas"]
    revision = schemas["ProductBriefRevisionRequestV1"]
    confirmation = schemas["ProductBriefConfirmationRequestV1"]
    provider_call = schemas["ProductBriefProviderCallResponseV1"]
    version_list = schemas["ProductBriefVersionListResponseV1"]
    version_summary = schemas["ProductBriefVersionSummaryResponseV1"]
    version_detail = schemas["ProductBriefVersionResponseV1"]
    workflow_context = schemas["ProductBriefWorkflowContextResponseV1"]
    operation_status = schemas["ProductBriefOperationStatusResponseV1"]
    operation_error = schemas["ProductBriefOperationErrorResponseV1"]
    assert revision["additionalProperties"] is False
    assert confirmation["additionalProperties"] is False
    assert workflow_context["additionalProperties"] is False
    assert operation_status["additionalProperties"] is False
    assert operation_error["additionalProperties"] is False
    assert revision["properties"]["expected_product_brief_version"]["minimum"] == 1
    assert confirmation["properties"]["expected_workflow_version"]["minimum"] == 1
    assert set(version_list["properties"]) == {"items", "next_cursor"}
    assert version_list["properties"]["items"]["items"]["$ref"].endswith(
        "/ProductBriefVersionSummaryResponseV1"
    )
    assert version_list["properties"]["items"]["maxItems"] == 100
    assert "fields" not in version_summary["properties"]
    assert "fields" in version_detail["properties"]
    assert version_summary["additionalProperties"] is False
    assert version_summary["properties"]["id"]["maxLength"] == 36
    assert version_summary["properties"]["version_number"]["maximum"] == 2_147_483_647
    assert version_summary["properties"]["changed_field_paths"]["maxItems"] == 64
    assert version_summary["properties"]["changed_field_paths"]["items"]["maxLength"] == 160
    assert version_detail["properties"]["fields"]["maxItems"] == 64
    assert set(provider_call["properties"]) == {
        "provider",
        "requested_model",
        "resolved_model",
        "latency_ms",
    }
    assert set(provider_call["required"]) == {
        "provider",
        "requested_model",
        "resolved_model",
        "latency_ms",
    }
    assert set(workflow_context["properties"]) == {
        "id",
        "status",
        "version",
        "retention_deadline",
    }
    assert set(operation_status["properties"]) == {
        "id",
        "state",
        "attempt_count",
        "max_attempts",
        "error",
        "version",
    }
    assert set(operation_error["properties"]) == {
        "code",
        "category",
        "message",
        "retryable",
    }
    analysis_workflow_parameters = _parameters_by_name(
        paths["/api/v1/product-briefs/analysis-workflow-context/{workflow_id}"]["get"]
    )
    bound_workflow_parameters = _parameters_by_name(
        paths["/api/v1/product-briefs/workflow-context/{workflow_id}"]["get"]
    )
    version_parameters = _parameters_by_name(
        paths["/api/v1/product-briefs/{product_brief_id}/versions"]["get"]
    )
    assert "product_brief_id" not in analysis_workflow_parameters
    assert bound_workflow_parameters["product_brief_id"]["in"] == "query"
    assert bound_workflow_parameters["product_brief_id"]["required"] is True
    assert version_parameters["limit"]["schema"] == {
        "default": 20,
        "maximum": 100,
        "minimum": 1,
        "title": "Limit",
        "type": "integer",
    }
    assert version_parameters["cursor"]["required"] is False
    assert version_parameters["cursor"]["schema"]["anyOf"] == [
        {"maximum": 2_147_483_647, "minimum": 1, "type": "integer"},
        {"type": "null"},
    ]
    for field_schema_name in (
        "ProductBriefFieldResponseV1",
        "ProductBriefFieldRevisionV1",
    ):
        field_schema = schemas[field_schema_name]
        assert field_schema["x-commercevision-field-value-kinds"] == EXPECTED_FIELD_VALUE_KINDS
        value_schema = field_schema["properties"]["value"]
        assert value_schema["discriminator"]["propertyName"] == "kind"
        assert set(value_schema["discriminator"]["mapping"]) == {
            value_kind for value_kind in EXPECTED_FIELD_VALUE_KINDS.values()
        }
        assert {
            item["$ref"].rsplit("/", maxsplit=1)[-1] for item in value_schema["oneOf"]
        } == FIELD_VALUE_SCHEMA_NAMES

    provider_field_schema = ProductBriefFieldOutput.model_json_schema()
    assert provider_field_schema["x-commercevision-field-value-kinds"] == EXPECTED_FIELD_VALUE_KINDS
    provider_value_schema = provider_field_schema["properties"]["value"]
    assert provider_value_schema["discriminator"]["propertyName"] == "kind"
    assert {
        item["$ref"].rsplit("/", maxsplit=1)[-1] for item in provider_value_schema["oneOf"]
    } == FIELD_VALUE_SCHEMA_NAMES

    for value_schema_name in (
        *FIELD_VALUE_SCHEMA_NAMES,
        "ProductBriefDimensionValueItemV1",
    ):
        assert schemas[value_schema_name]["additionalProperties"] is False

    operations = (
        paths["/api/v1/product-briefs:analyze"]["post"],
        paths["/api/v1/product-briefs/{product_brief_id}"]["get"],
        paths["/api/v1/product-briefs/{product_brief_id}/versions"]["get"],
        paths["/api/v1/product-briefs/{product_brief_id}:revise"]["post"],
        paths["/api/v1/product-briefs/{product_brief_id}:confirm"]["post"],
        paths["/api/v1/product-briefs/analysis-workflow-context/{workflow_id}"]["get"],
        paths["/api/v1/product-briefs/workflow-context/{workflow_id}"]["get"],
        paths["/api/v1/product-briefs/{product_brief_id}/operations/{operation_id}"]["get"],
    )
    for operation in operations:
        parameters = _parameters_by_name(operation)
        assert parameters["X-Trusted-Principal"]["required"] is False
        assert "401" in operation["responses"]
        assert "500" in operation["responses"]
        assert "503" in operation["responses"]

    generated_types = (root / "apps/web/lib/generated/catalog-api.ts").read_text("utf-8")
    mapping_source = generated_types.split(
        "export const PRODUCT_BRIEF_FIELD_VALUE_KIND_BY_PATH = ",
        maxsplit=1,
    )[1].split(" as const;", maxsplit=1)[0]
    assert json.loads(mapping_source) == EXPECTED_FIELD_VALUE_KINDS
    for kind in EXPECTED_FIELD_VALUE_KINDS.values():
        assert f'kind: "{kind}";' in generated_types
    assert "export type ProductBriefFieldRevisionV1 = {" in generated_types
    assert "export type ProductBriefFieldResponseV1 = {" in generated_types
    assert "value: ProductBriefFieldValueForPath<Path>;" in generated_types
    provider_type = generated_types.split(
        "export interface ProductBriefProviderCallResponseV1",
        maxsplit=1,
    )[1].split("}", maxsplit=1)[0]
    for public_field in ("provider", "requested_model", "resolved_model", "latency_ms"):
        assert f"{public_field}:" in provider_type
    for internal_field in (
        "operation_id",
        "operation_attempt",
        "call_index",
        "endpoint_region",
        "endpoint_host",
        "submitted_model_snapshot",
        "config_snapshot_sha256",
        "request_id",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "error_code",
        "error_category",
        "error_retryable",
    ):
        assert f"{internal_field}:" not in provider_type
    assert (
        "path: string;"
        not in generated_types.split(
            "interface ProductBriefFieldRevisionV1Base",
            maxsplit=1,
        )[1]
    )
    assert committed == generated
