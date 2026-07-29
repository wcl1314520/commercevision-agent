import type {
  ProductBriefEvidenceRevisionV1,
  ProductBriefFieldPath,
  ProductBriefFieldResponseV1,
  ProductBriefFieldRevisionV1,
  ProductBriefTextValueV1,
  ProductBriefVersionListResponseV1,
  ProductBriefVersionSummaryResponseV1,
} from "../lib/generated/catalog-api";

const textValue: ProductBriefTextValueV1 = {
  kind: "TEXT",
  text: "Northstar Labs",
};
const textKind: "TEXT" = textValue.kind;

const revisionEvidence: ProductBriefEvidenceRevisionV1 = {
  source_asset_version_id: "019f8a00-0000-7000-8000-000000000103",
  kind: "IMAGE_REGION",
  reference: `asset-region://${"a".repeat(64)}`,
  region: [0.1, 0.2, 0.8, 0.9],
  excerpt_sha256: "b".repeat(64),
};

const validRevision: ProductBriefFieldRevisionV1 = {
  path: "common.brand",
  value: textValue,
  sensitive: false,
  evidence: [revisionEvidence],
};

const emptyEvidenceRevision: ProductBriefFieldRevisionV1 = {
  path: "common.brand",
  value: textValue,
  sensitive: false,
  // @ts-expect-error ProductBrief revisions require at least one evidence item.
  evidence: [],
};

const wrongKindForPath: ProductBriefFieldRevisionV1 = {
  path: "common.brand",
  // @ts-expect-error common.brand only accepts the TEXT value contract.
  value: { kind: "FLAG_LIST", flags: [] },
  sensitive: false,
  evidence: [revisionEvidence],
};

const unknownPath: ProductBriefFieldRevisionV1 = {
  // @ts-expect-error unknown ProductBrief paths cannot reach the HTTP client.
  path: "common.unknown",
  value: textValue,
  sensitive: false,
  evidence: [revisionEvidence],
};

declare const responseField: ProductBriefFieldResponseV1;
const responsePath: ProductBriefFieldPath = responseField.path;
declare const historySummary: ProductBriefVersionSummaryResponseV1;
const historyPage: ProductBriefVersionListResponseV1 = {
  items: [historySummary],
  next_cursor: null,
};
// @ts-expect-error History summaries intentionally exclude full field/evidence payloads.
const historyFields = historySummary.fields;

void [
  textKind,
  validRevision,
  emptyEvidenceRevision,
  wrongKindForPath,
  unknownPath,
  responsePath,
  historyPage,
  historyFields,
];
