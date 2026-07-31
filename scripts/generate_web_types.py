"""Generate Web workbench TypeScript types from the committed OpenAPI document."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

SCHEMAS = (
    "AssetKind",
    "AssetAdministratorBlockRequestV1",
    "AssetObjectState",
    "AssetResponseV1",
    "AssetState",
    "AssetValidationOperationResponseV1",
    "AssetValidationStageResponseV1",
    "AssetValidationStatusResponseV1",
    "AssetVersionResponseV1",
    "BrandColorV1",
    "BrandProfileCreateRequestV1",
    "BrandProfileDraftV1",
    "BrandProfileListResponseV1",
    "BrandProfileMemberRole",
    "BrandProfileMemberSelectionV1",
    "BrandProfilePublishRequestV1",
    "BrandProfilePublishedMemberV1",
    "BrandProfileResponseV1",
    "BrandProfileState",
    "BrandProfileUpdateDraftRequestV1",
    "BrandProfileValidateRequestV1",
    "BrandProfileValidationIssueV1",
    "BrandProfileValidationResponseV1",
    "BrandProfileVersionListResponseV1",
    "BrandProfileVersionResponseV1",
    "BrandRuleScope",
    "BrandRuleV1",
    "CatalogDeleteRequestV1",
    "ErrorResponse",
    "JsonValue",
    "OperationState",
    "PresignedUploadV1",
    "ProductBriefAnalysisAcceptedV1",
    "ProductBriefAnalysisRequestV1",
    "ProductBriefCategory",
    "ProductBriefConfirmationRequestV1",
    "ProductBriefConfirmationResponseV1",
    "ProductBriefEvidenceKind",
    "ProductBriefEvidenceResponseV1",
    "ProductBriefEvidenceRevisionV1",
    "ProductBriefFieldConflict",
    "ProductBriefIdentityValueV1",
    "ProductBriefCategoryValueV1",
    "ProductBriefTextValueV1",
    "ProductBriefTextListValueV1",
    "ProductBriefStatementListValueV1",
    "ProductBriefFlagListValueV1",
    "ProductBriefDimensionValueItemV1",
    "ProductBriefDimensionListValueV1",
    "ProductBriefFieldResponseV1",
    "ProductBriefFieldRevisionV1",
    "ProductBriefFieldSource",
    "ProductBriefOperationErrorResponseV1",
    "ProductBriefOperationStatusResponseV1",
    "ProductBriefProviderCallResponseV1",
    "ProductBriefResponseV1",
    "ProductBriefRevisionRequestV1",
    "ProductBriefState",
    "ProductBriefVersionListResponseV1",
    "ProductBriefVersionResponseV1",
    "ProductBriefVersionSummaryResponseV1",
    "ProductBriefVersionSource",
    "ProductBriefWorkflowContextResponseV1",
    "ProductCreateRequestV1",
    "ProductListResponseV1",
    "ProductResponseV1",
    "ProductSummaryResponseV1",
    "ProductUpdateRequestV1",
    "RetentionClass",
    "RightsDecisionCode",
    "RightsHistoryResponseV1",
    "RightsMutationResponseV1",
    "RightsRecordDecision",
    "RightsRecordMutationRequestV1",
    "RightsRecordResponseV1",
    "RightsRecordRevokeRequestV1",
    "RightsUsabilityRequestV1",
    "RightsUsabilityResponseV1",
    "SKUCreateRequestV1",
    "SKUResponseV1",
    "SKUUpdateRequestV1",
    "UploadFinalizeResponseV1",
    "UploadSessionCreateRequestV1",
    "UploadSessionCreateResponseV1",
    "UploadSessionMutationRequestV1",
    "UploadSessionResponseV1",
    "UploadSessionState",
    "ValidationOperationSummaryV1",
    "ValidationStage",
    "ValidationVerdict",
    "WorkflowStatus",
)

ASSOCIATED_PRODUCT_BRIEF_FIELD_SCHEMAS = (
    "ProductBriefFieldResponseV1",
    "ProductBriefFieldRevisionV1",
)


def schema_type(schema: dict[str, Any]) -> str:
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    if "const" in schema:
        return json.dumps(schema["const"])
    if "enum" in schema:
        return " | ".join(json.dumps(value) for value in schema["enum"])
    if "anyOf" in schema:
        return " | ".join(schema_type(item) for item in schema["anyOf"])
    if "oneOf" in schema:
        return " | ".join(schema_type(item) for item in schema["oneOf"])
    if schema.get("type") == "array":
        if "prefixItems" in schema:
            return "[" + ", ".join(schema_type(item) for item in schema["prefixItems"]) + "]"
        item_type = schema_type(schema["items"])
        minimum_items = schema.get("minItems", 0)
        if minimum_items > 0:
            required_items = ", ".join(item_type for _ in range(minimum_items))
            return f"[{required_items}, ...Array<{item_type}>]"
        return f"Array<{item_type}>"
    if schema.get("type") == "object":
        if "additionalProperties" in schema:
            return "Record<string, unknown>"
        return "Record<string, unknown>"
    return {
        "boolean": "boolean",
        "integer": "number",
        "number": "number",
        "null": "null",
        "string": "string",
    }.get(schema.get("type", ""), "unknown")


def append_interface(
    lines: list[str],
    *,
    name: str,
    schema: dict[str, Any],
    export: bool = True,
    omit: frozenset[str] = frozenset(),
) -> None:
    prefix = "export " if export else ""
    lines.append(f"{prefix}interface {name} {{")
    required = set(schema.get("required", []))
    for property_name, property_schema in schema.get("properties", {}).items():
        if property_name in omit:
            continue
        optional = "" if property_name in required else "?"
        lines.append(f"  {property_name}{optional}: {schema_type(property_schema)};")
    lines.append("}")
    lines.append("")


def generate(document: dict[str, Any]) -> str:
    schemas = document["components"]["schemas"]
    lines = [
        "// Generated by scripts/generate_web_types.py. Do not edit by hand.",
        "",
    ]
    for name in SCHEMAS:
        schema = schemas[name]
        if name in ASSOCIATED_PRODUCT_BRIEF_FIELD_SCHEMAS:
            continue
        if name == "JsonValue":
            lines.append(
                "export type JsonValue = null | boolean | number | string | "
                "Array<JsonValue> | { [key: string]: JsonValue };"
            )
            lines.append("")
            continue
        if "enum" in schema:
            lines.append(f"export type {name} = {schema_type(schema)};")
            lines.append("")
            continue
        append_interface(lines, name=name, schema=schema)
    field_value_kinds = schemas["ProductBriefFieldRevisionV1"]["x-commercevision-field-value-kinds"]
    revision_value_schema = schemas["ProductBriefFieldRevisionV1"]["properties"]["value"]
    value_kind_mapping = revision_value_schema["discriminator"]["mapping"]
    lines.extend(
        [
            "export const PRODUCT_BRIEF_FIELD_VALUE_KIND_BY_PATH = "
            + json.dumps(
                field_value_kinds,
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
            + " as const;",
            "",
            "export type ProductBriefFieldPath = "
            "keyof typeof PRODUCT_BRIEF_FIELD_VALUE_KIND_BY_PATH;",
            "",
            "export interface ProductBriefFieldValueByKindV1 {",
            *[
                f"  {kind}: {reference.rsplit('/', 1)[-1]};"
                for kind, reference in sorted(value_kind_mapping.items())
            ],
            "}",
            "",
            "export type ProductBriefFieldValueForPath<"
            "Path extends ProductBriefFieldPath"
            "> = ProductBriefFieldValueByKindV1["
            "(typeof PRODUCT_BRIEF_FIELD_VALUE_KIND_BY_PATH)[Path]"
            "];",
            f"export type ProductBriefFieldValueV1 = {schema_type(revision_value_schema)};",
            "",
        ]
    )
    for name in ASSOCIATED_PRODUCT_BRIEF_FIELD_SCHEMAS:
        schema = schemas[name]
        base_name = f"{name}Base"
        append_interface(
            lines,
            name=base_name,
            schema=schema,
            export=False,
            omit=frozenset({"path", "value"}),
        )
        lines.extend(
            [
                f"export type {name} = {{",
                f"  [Path in ProductBriefFieldPath]: {base_name} & {{",
                "    path: Path;",
                "    value: ProductBriefFieldValueForPath<Path>;",
                "  };",
                "}[ProductBriefFieldPath];",
                "",
            ]
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).parents[1]
    source = root / "docs" / "api" / "openapi.json"
    target = root / "apps" / "web" / "lib" / "generated" / "catalog-api.ts"
    content = generate(json.loads(source.read_text(encoding="utf-8")))
    if args.check:
        if target.read_text(encoding="utf-8") != content:
            raise SystemExit("generated Web API types are out of date")
        print(target)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    print(target)


if __name__ == "__main__":
    main()
