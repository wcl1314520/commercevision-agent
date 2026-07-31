import type {
  BrandColorV1,
  BrandProfileDraftV1,
  BrandProfileListResponseV1,
  BrandProfileMemberRole,
  BrandProfilePublishedMemberV1,
  BrandProfileResponseV1,
  BrandProfileState,
  BrandProfileValidationIssueV1,
  BrandProfileValidationResponseV1,
  BrandProfileVersionListResponseV1,
  BrandProfileVersionResponseV1,
  BrandRuleScope,
  BrandRuleV1,
  ErrorResponse,
  RightsDecisionCode,
} from "./generated/catalog-api";

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const TOKEN_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const WORKSPACE_PATTERN = TOKEN_PATTERN;
const CURSOR_PATTERN =
  /^v1\.[A-Za-z0-9_-]{1,64}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/;
const COLOR_PATTERN = /^#[0-9A-F]{6}(?:[0-9A-F]{2})?$/;
const SHA256_PATTERN = /^[0-9a-f]{64}$/;
const UTC_TIMESTAMP_PATTERN = /(?:Z|\+00:00)$/;
const CONTROL_PATTERN = /[\u0000-\u001f\u007f]/;

const PROFILE_STATES = new Set<BrandProfileState>([
  "DRAFT",
  "ACTIVE",
  "NEEDS_REPUBLISH",
  "ARCHIVED",
]);
const RULE_SCOPES = new Set<BrandRuleScope>([
  "VISUAL",
  "COPY",
  "COMPOSITION",
  "LEGAL",
  "GENERAL",
]);
const MEMBER_ROLES = new Set<BrandProfileMemberRole>([
  "LOGO",
  "REQUIRED_MARK",
  "VISUAL_REFERENCE",
  "PROMPT_TEMPLATE",
  "MODEL_CONFIGURATION",
  "LORA",
]);
const RIGHTS_DECISION_CODES = new Set<RightsDecisionCode>([
  "AUTHORIZED",
  "NO_CURRENT_RIGHTS",
  "RIGHTS_REVOKED",
  "RIGHTS_NOT_YET_VALID",
  "RIGHTS_EXPIRED",
  "RIGHTS_ASSET_VERSION_MISMATCH",
  "ASSET_VERSION_NOT_CURRENT",
  "ASSET_NOT_AVAILABLE",
  "ASSET_RETENTION_EXPIRED",
  "ASSET_BLOCKED",
  "ADMINISTRATIVELY_BLOCKED",
  "USE_NOT_ALLOWED",
  "PROVIDER_NOT_ALLOWED",
  "DERIVATIVE_NOT_ALLOWED",
]);

type JsonObject = Record<string, unknown>;

export class BrandProfileProtocolError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "BrandProfileProtocolError";
  }
}

function reject(field: string): never {
  throw new BrandProfileProtocolError(
    `Brand Profile response field is invalid: ${field}`,
  );
}

function object(value: unknown, field: string): JsonObject {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    reject(field);
  }
  return value as JsonObject;
}

function string(
  value: unknown,
  field: string,
  {
    min = 1,
    max,
    pattern,
  }: {
    min?: number;
    max: number;
    pattern?: RegExp;
  },
): string {
  if (typeof value !== "string") {
    reject(field);
  }
  const characterLength = Array.from(value).length;
  if (
    characterLength < min ||
    characterLength > max ||
    value.trim() !== value ||
    CONTROL_PATTERN.test(value) ||
    (pattern !== undefined && !pattern.test(value))
  ) {
    reject(field);
  }
  return value;
}

function integer(
  value: unknown,
  field: string,
  minimum: number,
): number {
  if (
    typeof value !== "number" ||
    !Number.isSafeInteger(value) ||
    value < minimum
  ) {
    reject(field);
  }
  return value;
}

function nullableInteger(
  value: unknown,
  field: string,
  minimum: number,
): number | null {
  return value === null ? null : integer(value, field, minimum);
}

function boolean(value: unknown, field: string): boolean {
  if (typeof value !== "boolean") reject(field);
  return value;
}

function boundedArray(
  value: unknown,
  field: string,
  maximum: number,
): unknown[] {
  if (!Array.isArray(value) || value.length > maximum) reject(field);
  return value;
}

function uuid(value: unknown, field: string): string {
  return string(value, field, {
    max: 36,
    min: 36,
    pattern: UUID_PATTERN,
  });
}

function nullableUuid(value: unknown, field: string): string | null {
  return value === null ? null : uuid(value, field);
}

function timestamp(value: unknown, field: string): string {
  const result = string(value, field, { max: 64 });
  if (
    !UTC_TIMESTAMP_PATTERN.test(result) ||
    !Number.isFinite(Date.parse(result))
  ) {
    reject(field);
  }
  return result;
}

function nullableTimestamp(value: unknown, field: string): string | null {
  return value === null ? null : timestamp(value, field);
}

function enumeration<T extends string>(
  value: unknown,
  field: string,
  allowed: ReadonlySet<T>,
): T {
  if (typeof value !== "string" || !allowed.has(value as T)) reject(field);
  return value as T;
}

function textList(
  value: unknown,
  field: string,
): string[] {
  const items = boundedArray(value, field, 64).map((item, index) =>
    string(item, `${field}[${index}]`, { max: 512 }),
  );
  if (new Set(items).size !== items.length) reject(field);
  return items;
}

function decodeRule(value: unknown, index: number): BrandRuleV1 {
  const item = object(value, `draft.rules[${index}]`);
  return {
    code: string(item.code, `draft.rules[${index}].code`, {
      max: 128,
      pattern: TOKEN_PATTERN,
    }),
    scope: enumeration(
      item.scope,
      `draft.rules[${index}].scope`,
      RULE_SCOPES,
    ),
    instruction: string(
      item.instruction,
      `draft.rules[${index}].instruction`,
      { max: 1024 },
    ),
  };
}

function decodeColor(value: unknown, index: number): BrandColorV1 {
  const item = object(value, `draft.approved_colors[${index}]`);
  return {
    name: string(item.name, `draft.approved_colors[${index}].name`, {
      max: 64,
    }),
    value: string(item.value, `draft.approved_colors[${index}].value`, {
      max: 9,
      min: 7,
      pattern: COLOR_PATTERN,
    }),
  };
}

export function decodeBrandProfileDraft(
  value: unknown,
): BrandProfileDraftV1 {
  const draft = object(value, "draft");
  const rules = boundedArray(draft.rules, "draft.rules", 64).map(decodeRule);
  if (new Set(rules.map((rule) => rule.code)).size !== rules.length) {
    reject("draft.rules");
  }
  const approvedColors = boundedArray(
    draft.approved_colors,
    "draft.approved_colors",
    32,
  ).map(decodeColor);
  if (
    new Set(approvedColors.map((color) => color.name)).size !==
    approvedColors.length
  ) {
    reject("draft.approved_colors");
  }
  const selectedAssets = boundedArray(
    draft.selected_assets,
    "draft.selected_assets",
    64,
  ).map((value, index) => {
    const selection = object(value, `draft.selected_assets[${index}]`);
    return {
      asset_version_id: uuid(
        selection.asset_version_id,
        `draft.selected_assets[${index}].asset_version_id`,
      ),
      role: enumeration(
        selection.role,
        `draft.selected_assets[${index}].role`,
        MEMBER_ROLES,
      ),
    };
  });
  if (
    new Set(selectedAssets.map((item) => item.asset_version_id)).size !==
    selectedAssets.length
  ) {
    reject("draft.selected_assets");
  }
  return {
    rules,
    approved_colors: approvedColors,
    required_marks: textList(draft.required_marks, "draft.required_marks"),
    prohibited_elements: textList(
      draft.prohibited_elements,
      "draft.prohibited_elements",
    ),
    tone_constraints: textList(
      draft.tone_constraints,
      "draft.tone_constraints",
    ),
    copy_constraints: textList(
      draft.copy_constraints,
      "draft.copy_constraints",
    ),
    purpose: string(draft.purpose, "draft.purpose", {
      max: 128,
      pattern: TOKEN_PATTERN,
    }),
    provider: string(draft.provider, "draft.provider", {
      max: 128,
      pattern: TOKEN_PATTERN,
    }),
    requires_derivative: boolean(
      draft.requires_derivative,
      "draft.requires_derivative",
    ),
    selected_assets: selectedAssets,
  };
}

export type BrandProfileDecodeContext = {
  workspaceId: string;
  brand?: string;
  profileId?: string;
  profileKey?: string;
};

export function decodeBrandProfileResponse(
  value: unknown,
  context: BrandProfileDecodeContext,
): BrandProfileResponseV1 {
  const profile = object(value, "profile");
  const id = uuid(profile.id, "profile.id");
  const workspaceId = string(profile.workspace_id, "profile.workspace_id", {
    max: 128,
    pattern: WORKSPACE_PATTERN,
  });
  const brand = string(profile.brand, "profile.brand", { max: 128 });
  const profileKey = string(profile.profile_key, "profile.profile_key", {
    max: 128,
    pattern: TOKEN_PATTERN,
  });
  if (
    workspaceId !== context.workspaceId ||
    (context.brand !== undefined && brand !== context.brand) ||
    (context.profileId !== undefined && id !== context.profileId) ||
    (context.profileKey !== undefined && profileKey !== context.profileKey)
  ) {
    reject("profile.identity");
  }
  const state = enumeration(profile.state, "profile.state", PROFILE_STATES);
  const currentVersionId = nullableUuid(
    profile.current_version_id,
    "profile.current_version_id",
  );
  const currentVersionNumber = integer(
    profile.current_version_number,
    "profile.current_version_number",
    0,
  );
  const staleAt = nullableTimestamp(profile.stale_at, "profile.stale_at");
  if (
    (currentVersionId === null) !== (currentVersionNumber === 0) ||
    (state === "DRAFT" && currentVersionId !== null) ||
    ((state === "ACTIVE" || state === "NEEDS_REPUBLISH") &&
      currentVersionId === null) ||
    (state === "NEEDS_REPUBLISH") !== (staleAt !== null)
  ) {
    reject("profile.state");
  }
  return {
    id,
    workspace_id: workspaceId,
    brand,
    profile_key: profileKey,
    state,
    draft: decodeBrandProfileDraft(profile.draft),
    current_version_id: currentVersionId,
    current_version_number: currentVersionNumber,
    version: integer(profile.version, "profile.version", 1),
    stale_at: staleAt,
    created_by: string(profile.created_by, "profile.created_by", {
      max: 128,
    }),
    created_at: timestamp(profile.created_at, "profile.created_at"),
    updated_by: string(profile.updated_by, "profile.updated_by", {
      max: 128,
    }),
    updated_at: timestamp(profile.updated_at, "profile.updated_at"),
  };
}

function cursor(value: unknown, field: string): string | null {
  return value === null || value === undefined
    ? null
    : string(value, field, { max: 256, pattern: CURSOR_PATTERN });
}

export function decodeBrandProfileListResponse(
  value: unknown,
  context: BrandProfileDecodeContext & { limit: number },
): BrandProfileListResponseV1 {
  const page = object(value, "profile_page");
  const items = boundedArray(
    page.items,
    "profile_page.items",
    context.limit,
  ).map((profile) => decodeBrandProfileResponse(profile, context));
  if (new Set(items.map((item) => item.id)).size !== items.length) {
    reject("profile_page.items");
  }
  return {
    items,
    next_cursor: cursor(page.next_cursor, "profile_page.next_cursor"),
  };
}

function decodeValidationIssue(
  value: unknown,
  index: number,
): BrandProfileValidationIssueV1 {
  const issue = object(value, `validation.issues[${index}]`);
  return {
    asset_version_id: uuid(
      issue.asset_version_id,
      `validation.issues[${index}].asset_version_id`,
    ),
    role: enumeration(
      issue.role,
      `validation.issues[${index}].role`,
      MEMBER_ROLES,
    ),
    reason_code: string(
      issue.reason_code,
      `validation.issues[${index}].reason_code`,
      { max: 128, pattern: TOKEN_PATTERN },
    ),
    message: string(
      issue.message,
      `validation.issues[${index}].message`,
      { max: 512 },
    ),
  };
}

export function decodeBrandProfileValidationResponse(
  value: unknown,
  {
    profileId,
    profileVersion,
  }: {
    profileId: string;
    profileVersion: number;
  },
): BrandProfileValidationResponseV1 {
  const validation = object(value, "validation");
  const decoded = {
    profile_id: uuid(validation.profile_id, "validation.profile_id"),
    profile_version: integer(
      validation.profile_version,
      "validation.profile_version",
      1,
    ),
    valid: boolean(validation.valid, "validation.valid"),
    decided_at: timestamp(validation.decided_at, "validation.decided_at"),
    issues: boundedArray(
      validation.issues,
      "validation.issues",
      64,
    ).map(decodeValidationIssue),
  };
  if (
    decoded.profile_id !== profileId ||
    decoded.profile_version !== profileVersion ||
    decoded.valid === (decoded.issues.length > 0)
  ) {
    reject("validation.summary");
  }
  return decoded;
}

function decodePublishedMember(
  value: unknown,
  index: number,
): BrandProfilePublishedMemberV1 {
  const member = object(value, `version.members[${index}]`);
  const currentRightsRecordId = nullableUuid(
    member.current_rights_record_id ?? null,
    `version.members[${index}].current_rights_record_id`,
  );
  const currentRightsRecordVersion = nullableInteger(
    member.current_rights_record_version ?? null,
    `version.members[${index}].current_rights_record_version`,
    1,
  );
  if (
    (currentRightsRecordId === null) !==
    (currentRightsRecordVersion === null)
  ) {
    reject(`version.members[${index}].current_rights_record`);
  }
  const currentlyUsable = boolean(
    member.currently_usable,
    `version.members[${index}].currently_usable`,
  );
  const currentReasonCode = enumeration(
    member.current_reason_code,
    `version.members[${index}].current_reason_code`,
    RIGHTS_DECISION_CODES,
  );
  if (currentlyUsable !== (currentReasonCode === "AUTHORIZED")) {
    reject(`version.members[${index}].current_reason_code`);
  }
  return {
    ordinal: integer(
      member.ordinal,
      `version.members[${index}].ordinal`,
      0,
    ),
    asset_id: uuid(
      member.asset_id,
      `version.members[${index}].asset_id`,
    ),
    asset_version_id: uuid(
      member.asset_version_id,
      `version.members[${index}].asset_version_id`,
    ),
    role: enumeration(
      member.role,
      `version.members[${index}].role`,
      MEMBER_ROLES,
    ),
    published_rights_record_id: uuid(
      member.published_rights_record_id,
      `version.members[${index}].published_rights_record_id`,
    ),
    published_rights_record_version: integer(
      member.published_rights_record_version,
      `version.members[${index}].published_rights_record_version`,
      1,
    ),
    currently_usable: currentlyUsable,
    current_reason_code: currentReasonCode,
    current_rights_record_id: currentRightsRecordId,
    current_rights_record_version: currentRightsRecordVersion,
    decided_at: timestamp(
      member.decided_at,
      `version.members[${index}].decided_at`,
    ),
  };
}

export function decodeBrandProfileVersionResponse(
  value: unknown,
  context: {
    workspaceId: string;
    profileId: string;
    versionNumber?: number;
  },
): BrandProfileVersionResponseV1 {
  const version = object(value, "version");
  const workspaceId = string(version.workspace_id, "version.workspace_id", {
    max: 128,
    pattern: WORKSPACE_PATTERN,
  });
  const profileId = uuid(version.profile_id, "version.profile_id");
  const versionNumber = integer(
    version.version_number,
    "version.version_number",
    1,
  );
  if (
    workspaceId !== context.workspaceId ||
    profileId !== context.profileId ||
    (context.versionNumber !== undefined &&
      versionNumber !== context.versionNumber)
  ) {
    reject("version.identity");
  }
  const draft = decodeBrandProfileDraft(version.draft);
  const members = boundedArray(version.members, "version.members", 64).map(
    decodePublishedMember,
  );
  if (
    members.length !== draft.selected_assets.length ||
    members.some(
      (member, index) =>
        member.ordinal !== index ||
        member.asset_version_id !==
          draft.selected_assets[index].asset_version_id ||
        member.role !== draft.selected_assets[index].role,
    )
  ) {
    reject("version.members");
  }
  return {
    id: uuid(version.id, "version.id"),
    workspace_id: workspaceId,
    profile_id: profileId,
    version_number: versionNumber,
    draft,
    content_sha256: string(
      version.content_sha256,
      "version.content_sha256",
      { min: 64, max: 64, pattern: SHA256_PATTERN },
    ),
    published_by: string(version.published_by, "version.published_by", {
      max: 128,
    }),
    published_at: timestamp(version.published_at, "version.published_at"),
    members,
  };
}

export function decodeBrandProfileVersionListResponse(
  value: unknown,
  context: {
    workspaceId: string;
    profileId: string;
    limit: number;
  },
): BrandProfileVersionListResponseV1 {
  const page = object(value, "version_page");
  const items = boundedArray(
    page.items,
    "version_page.items",
    context.limit,
  ).map((version) => decodeBrandProfileVersionResponse(version, context));
  if (
    new Set(items.map((item) => item.version_number)).size !== items.length ||
    items.some(
      (item, index) =>
        index > 0 &&
        items[index - 1].version_number <= item.version_number,
    )
  ) {
    reject("version_page.items");
  }
  return {
    items,
    next_cursor: cursor(page.next_cursor, "version_page.next_cursor"),
  };
}

export function decodeWorkspaceCapabilities(
  value: unknown,
): { administrator: boolean } {
  const capabilities = object(value, "capabilities");
  return {
    administrator: boolean(
      capabilities.administrator,
      "capabilities.administrator",
    ),
  };
}

export function decodeErrorResponse(value: unknown): ErrorResponse {
  const envelope = object(value, "error");
  const details =
    envelope.details === undefined
      ? undefined
      : object(envelope.details, "error.details");
  return {
    code: string(envelope.code, "error.code", {
      max: 128,
      pattern: TOKEN_PATTERN,
    }),
    category: string(envelope.category, "error.category", {
      max: 128,
      pattern: TOKEN_PATTERN,
    }),
    message: string(envelope.message, "error.message", { max: 1024 }),
    retryable: boolean(envelope.retryable, "error.retryable"),
    ...(details === undefined ? {} : { details }),
    request_id: string(envelope.request_id, "error.request_id", {
      max: 128,
      pattern: TOKEN_PATTERN,
    }),
    trace_id: string(envelope.trace_id, "error.trace_id", {
      max: 128,
      pattern: TOKEN_PATTERN,
    }),
  };
}
