import type {
  ProductBriefEvidenceKind,
  ProductBriefEvidenceRevisionV1,
  ProductBriefFieldRevisionV1,
  ProductBriefFieldValueV1,
  ProductBriefConfirmationRequestV1,
  ProductBriefRevisionRequestV1,
} from "./generated/catalog-api";
import { PRODUCT_BRIEF_FIELD_VALUE_KIND_BY_PATH } from "./generated/catalog-api";

export type VersionedProductBriefIdentity = {
  id: string;
  version: number;
};

export type ProductBriefSourceSelection = {
  productId: string;
  workflowId: string;
  assetVersionId: string;
};

type PendingProductBriefCommandBase = {
  schemaVersion: 1;
  productId: string;
  productBriefId: string;
  idempotencyKey: string;
};

export type PendingProductBriefCommand =
  | (PendingProductBriefCommandBase & {
      kind: "revise";
      payload: ProductBriefRevisionRequestV1;
    })
  | (PendingProductBriefCommandBase & {
      kind: "confirm";
      payload: ProductBriefConfirmationRequestV1;
    });

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isVersion(value: unknown): value is number {
  return (
    typeof value === "number" &&
    Number.isSafeInteger(value) &&
    value >= 1
  );
}

function hasExactKeys(
  value: Record<string, unknown>,
  required: string[],
  optional: string[] = [],
): boolean {
  const keys = Object.keys(value);
  return (
    required.every((key) => key in value) &&
    keys.every((key) => required.includes(key) || optional.includes(key))
  );
}

function isText(value: unknown, allowEmpty: boolean): value is string {
  return (
    typeof value === "string" &&
    (allowEmpty || value.length > 0) &&
    new TextEncoder().encode(value).byteLength <= 2048
  );
}

function isOptionalText(value: unknown): value is string | null | undefined {
  return value === null || value === undefined || isText(value, true);
}

function isBoundedString(
  value: unknown,
  minimumLength: number,
  maximumLength: number,
): value is string {
  return (
    typeof value === "string" &&
    value.length >= minimumLength &&
    value.length <= maximumLength
  );
}

function isUniqueTextList(value: unknown, maximumItems = 32): value is string[] {
  return (
    Array.isArray(value) &&
    value.length <= maximumItems &&
    value.every((item) => isText(item, false)) &&
    new Set(value).size === value.length
  );
}

function productBriefFieldValueForPath(
  path: string,
  value: unknown,
): ProductBriefFieldValueV1 | null {
  if (!isRecord(value)) return null;
  const expectedKind = (
    PRODUCT_BRIEF_FIELD_VALUE_KIND_BY_PATH as Record<string, string>
  )[path];
  if (!expectedKind || value.kind !== expectedKind) return null;

  switch (expectedKind) {
    case "IDENTITY":
      if (
        !hasExactKeys(
          value,
          ["kind", "display_name"],
          ["model_number", "variant"],
        ) ||
        !isText(value.display_name, true) ||
        !isOptionalText(value.model_number) ||
        !isOptionalText(value.variant) ||
        !Boolean(value.display_name || value.model_number || value.variant)
      ) {
        return null;
      }
      return {
        kind: "IDENTITY",
        display_name: value.display_name,
        ...(value.model_number !== undefined
          ? { model_number: value.model_number }
          : {}),
        ...(value.variant !== undefined ? { variant: value.variant } : {}),
      };
    case "CATEGORY":
      return hasExactKeys(value, ["kind", "code", "label"]) &&
        isText(value.code, false) &&
        isText(value.label, false)
        ? { kind: "CATEGORY", code: value.code, label: value.label }
        : null;
    case "TEXT":
      return hasExactKeys(value, ["kind", "text"]) &&
        isText(value.text, true)
        ? { kind: "TEXT", text: value.text }
        : null;
    case "TEXT_LIST":
      return hasExactKeys(value, ["kind", "items"]) &&
        isUniqueTextList(value.items)
        ? { kind: "TEXT_LIST", items: [...value.items] }
        : null;
    case "STATEMENT_LIST":
      return hasExactKeys(value, ["kind", "statements"]) &&
        isUniqueTextList(value.statements)
        ? { kind: "STATEMENT_LIST", statements: [...value.statements] }
        : null;
    case "FLAG_LIST":
      return hasExactKeys(value, ["kind", "flags"]) &&
        isUniqueTextList(value.flags)
        ? { kind: "FLAG_LIST", flags: [...value.flags] }
        : null;
    case "DIMENSION_LIST": {
      if (
        !hasExactKeys(value, ["kind", "dimensions"]) ||
        !Array.isArray(value.dimensions) ||
        value.dimensions.length > 16
      ) {
        return null;
      }
      const names: string[] = [];
      const dimensions = [];
      for (const dimension of value.dimensions) {
        if (
          !isRecord(dimension) ||
          !hasExactKeys(
            dimension,
            ["name", "value"],
            ["unit", "raw_text"],
          ) ||
          !isText(dimension.name, false) ||
          !isText(dimension.value, false) ||
          !isOptionalText(dimension.unit) ||
          !isOptionalText(dimension.raw_text)
        ) {
          return null;
        }
        names.push(dimension.name);
        dimensions.push({
          name: dimension.name,
          value: dimension.value,
          ...(dimension.unit !== undefined ? { unit: dimension.unit } : {}),
          ...(dimension.raw_text !== undefined
            ? { raw_text: dimension.raw_text }
            : {}),
        });
      }
      return new Set(names).size === names.length
        ? { kind: "DIMENSION_LIST", dimensions }
        : null;
    }
    default:
      return null;
  }
}

export function isProductBriefFieldValueForPath(
  path: string,
  value: unknown,
): value is ProductBriefFieldValueV1 {
  return productBriefFieldValueForPath(path, value) !== null;
}

const EVIDENCE_REFERENCE_PREFIX: Record<ProductBriefEvidenceKind, string> = {
  IMAGE_REGION: "asset-region://",
  VISIBLE_TEXT: "asset-text://",
  PRODUCT_DATA: "product-data://",
  HUMAN_NOTE: "human-note://",
};

function evidenceRegionFor(
  value: unknown,
): [number, number, number, number] | null {
  if (
    !Array.isArray(value) ||
    value.length !== 4 ||
    !value.every(
      (coordinate) =>
        typeof coordinate === "number" &&
        Number.isFinite(coordinate) &&
        coordinate >= 0 &&
        coordinate <= 1,
    ) ||
    value[0] >= value[2] ||
    value[1] >= value[3]
  ) {
    return null;
  }
  return [value[0], value[1], value[2], value[3]];
}

function evidenceFor(
  value: unknown,
): ProductBriefEvidenceRevisionV1 | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(
      value,
      ["source_asset_version_id", "kind", "reference"],
      ["region", "excerpt_sha256"],
    ) ||
    !isBoundedString(value.source_asset_version_id, 1, 36) ||
    typeof value.kind !== "string" ||
    !Object.prototype.hasOwnProperty.call(
      EVIDENCE_REFERENCE_PREFIX,
      value.kind,
    ) ||
    !isBoundedString(value.reference, 1, 512)
  ) {
    return null;
  }
  const kind = value.kind as ProductBriefEvidenceKind;
  const expectedReference = new RegExp(
    `^${EVIDENCE_REFERENCE_PREFIX[kind]}[0-9a-f]{64}$`,
  );
  if (!expectedReference.test(value.reference)) return null;

  const region =
    value.region === undefined || value.region === null
      ? value.region
      : evidenceRegionFor(value.region);
  if (value.region !== undefined && value.region !== null && region === null) {
    return null;
  }
  if (
    value.excerpt_sha256 !== undefined &&
    value.excerpt_sha256 !== null &&
    (typeof value.excerpt_sha256 !== "string" ||
      !/^[0-9a-f]{64}$/.test(value.excerpt_sha256))
  ) {
    return null;
  }
  return {
    source_asset_version_id: value.source_asset_version_id,
    kind,
    reference: value.reference,
    ...(value.region !== undefined ? { region } : {}),
    ...(value.excerpt_sha256 !== undefined
      ? { excerpt_sha256: value.excerpt_sha256 }
      : {}),
  };
}

function isConfidence(value: unknown): value is number | string {
  if (typeof value === "number") {
    return Number.isFinite(value) && value >= 0 && value <= 1;
  }
  return (
    isBoundedString(value, 1, 64) &&
    /^(?:0(?:\.[0-9]+)?|1(?:\.0+)?)$/.test(value)
  );
}

function revisionFieldFor(
  value: unknown,
): ProductBriefFieldRevisionV1 | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(
      value,
      ["path", "value", "sensitive", "evidence"],
      ["confidence", "conflict", "review_required"],
    ) ||
    !isBoundedString(value.path, 1, 160) ||
    typeof value.sensitive !== "boolean" ||
    !Array.isArray(value.evidence) ||
    value.evidence.length < 1 ||
    value.evidence.length > 32 ||
    (value.confidence !== undefined && !isConfidence(value.confidence)) ||
    (value.conflict !== undefined &&
      value.conflict !== "NONE" &&
      value.conflict !== "CONFLICTING" &&
      value.conflict !== "RESOLVED") ||
    (value.review_required !== undefined &&
      typeof value.review_required !== "boolean")
  ) {
    return null;
  }
  const fieldValue = productBriefFieldValueForPath(value.path, value.value);
  const evidence = value.evidence.map(evidenceFor);
  if (fieldValue === null || evidence.some((item) => item === null)) {
    return null;
  }
  return {
    path: value.path,
    value: fieldValue,
    ...(value.confidence !== undefined
      ? { confidence: value.confidence }
      : {}),
    ...(value.conflict !== undefined ? { conflict: value.conflict } : {}),
    ...(value.review_required !== undefined
      ? { review_required: value.review_required }
      : {}),
    sensitive: value.sensitive,
    evidence: evidence as ProductBriefEvidenceRevisionV1[],
  } as ProductBriefFieldRevisionV1;
}

function revisionPayloadFor(
  value: unknown,
): ProductBriefRevisionRequestV1 | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      "expected_product_brief_version",
      "base_version_id",
      "reason",
      "fields",
    ]) ||
    !isVersion(value.expected_product_brief_version) ||
    !isBoundedString(value.base_version_id, 1, 36) ||
    !isBoundedString(value.reason, 3, 512) ||
    !Array.isArray(value.fields) ||
    value.fields.length < 1 ||
    value.fields.length > 64
  ) {
    return null;
  }
  const fields = value.fields.map(revisionFieldFor);
  if (
    fields.some((field) => field === null) ||
    new Set(fields.map((field) => field?.path)).size !== fields.length
  ) {
    return null;
  }
  const canonicalFields = fields as ProductBriefFieldRevisionV1[];
  return {
    expected_product_brief_version: value.expected_product_brief_version,
    base_version_id: value.base_version_id,
    reason: value.reason,
    fields: [canonicalFields[0], ...canonicalFields.slice(1)],
  };
}

function optionalBoundedString(
  value: unknown,
  maximumLength: number,
): value is string | null | undefined {
  return (
    value === undefined ||
    value === null ||
    isBoundedString(value, 1, maximumLength)
  );
}

function confirmationPayloadFor(
  value: unknown,
): ProductBriefConfirmationRequestV1 | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(
      value,
      [
        "expected_product_brief_version",
        "product_brief_version_id",
        "expected_workflow_version",
      ],
      ["reason_code", "comment_ref"],
    ) ||
    !isVersion(value.expected_product_brief_version) ||
    !isBoundedString(value.product_brief_version_id, 1, 36) ||
    !isVersion(value.expected_workflow_version) ||
    !optionalBoundedString(value.reason_code, 128) ||
    !optionalBoundedString(value.comment_ref, 512)
  ) {
    return null;
  }
  return {
    expected_product_brief_version: value.expected_product_brief_version,
    product_brief_version_id: value.product_brief_version_id,
    expected_workflow_version: value.expected_workflow_version,
    ...(value.reason_code !== undefined
      ? { reason_code: value.reason_code }
      : {}),
    ...(value.comment_ref !== undefined
      ? { comment_ref: value.comment_ref }
      : {}),
  };
}

export function pendingProductBriefCommandFor(
  productId: string,
  productBriefId: string,
  candidate: unknown,
): PendingProductBriefCommand | null {
  if (
    !isRecord(candidate) ||
    !hasExactKeys(candidate, [
      "schemaVersion",
      "kind",
      "productId",
      "productBriefId",
      "payload",
      "idempotencyKey",
    ]) ||
    candidate.schemaVersion !== 1 ||
    (candidate.kind !== "revise" && candidate.kind !== "confirm") ||
    candidate.productId !== productId ||
    candidate.productBriefId !== productBriefId ||
    !isBoundedString(candidate.productId, 1, 36) ||
    !isBoundedString(candidate.productBriefId, 1, 36) ||
    !isBoundedString(candidate.idempotencyKey, 8, 256)
  ) {
    return null;
  }
  if (candidate.kind === "revise") {
    const payload = revisionPayloadFor(candidate.payload);
    return payload
      ? {
          schemaVersion: 1,
          kind: "revise",
          productId: candidate.productId,
          productBriefId: candidate.productBriefId,
          payload,
          idempotencyKey: candidate.idempotencyKey,
        }
      : null;
  }
  const payload = confirmationPayloadFor(candidate.payload);
  return payload
    ? {
        schemaVersion: 1,
        kind: "confirm",
        productId: candidate.productId,
        productBriefId: candidate.productBriefId,
        payload,
        idempotencyKey: candidate.idempotencyKey,
      }
    : null;
}

export function productBriefCommandsMatch(
  dispatched: PendingProductBriefCommand,
  candidate: unknown,
): boolean {
  const matching = pendingProductBriefCommandFor(
    dispatched.productId,
    dispatched.productBriefId,
    candidate,
  );
  const canonicalDispatched = pendingProductBriefCommandFor(
    dispatched.productId,
    dispatched.productBriefId,
    dispatched,
  );
  return (
    matching !== null &&
    canonicalDispatched !== null &&
    JSON.stringify(matching) === JSON.stringify(canonicalDispatched)
  );
}

export function isMonotonicProductBriefVersion(
  published: VersionedProductBriefIdentity | null,
  candidate: VersionedProductBriefIdentity,
): boolean {
  return (
    published === null ||
    published.id !== candidate.id ||
    candidate.version >= published.version
  );
}

export function structuredValueError(
  path: string,
  valueText: string,
): string | null {
  try {
    const value: unknown = JSON.parse(valueText);
    return isProductBriefFieldValueForPath(path, value)
      ? null
      : "字段值不符合当前 ProductBrief 字段契约。";
  } catch {
    return "请输入有效的 JSON。";
  }
}

export function productBriefSourceFor(
  productId: string,
  source: ProductBriefSourceSelection | null,
): ProductBriefSourceSelection | null {
  return source?.productId === productId ? source : null;
}

export function restoredProductBriefDrafts<T extends object>(
  currentDrafts: Record<string, T>,
  staleDrafts: Record<string, T>,
): Record<string, T> {
  return Object.fromEntries(
    Object.entries(currentDrafts).map(([path, currentDraft]) => [
      path,
      staleDrafts[path]
        ? { ...currentDraft, ...staleDrafts[path] }
        : currentDraft,
    ]),
  );
}
