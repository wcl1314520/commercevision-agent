import type {
  BrandProfileCreateRequestV1,
  BrandProfileDraftV1,
  BrandProfilePublishRequestV1,
  BrandProfileUpdateDraftRequestV1,
} from "./generated/catalog-api";

const STORAGE_KEY = "commercevision:brand-profile:pending-commands:v1";
const SHA256_PATTERN = /^[0-9a-f]{64}$/;
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const TOKEN_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const IDEMPOTENCY_KEY_PATTERN = /^[\x21-\x7e]{8,256}$/;
const CONTROL_CHARACTER_PATTERN = /[\x00-\x1f\x7f]/;
const COMMAND_FIELDS = new Set([
  "schema_version",
  "action",
  "workspace_id",
  "brand",
  "profile_id",
  "profile_key",
  "expected_version",
  "expected_publication_version",
  "idempotency_key",
  "payload",
  "payload_sha256",
  "attempted_draft",
  "created_at",
  "command_sha256",
]);

export type BrandProfileCommandPayload =
  | BrandProfileCreateRequestV1
  | BrandProfilePublishRequestV1
  | BrandProfileUpdateDraftRequestV1;

export type BrandProfilePendingCommandAction =
  | "CREATE"
  | "UPDATE_DRAFT"
  | "PUBLISH";

export type PendingBrandProfileCommand = {
  schema_version: 1;
  action: BrandProfilePendingCommandAction;
  workspace_id: string;
  brand: string;
  profile_id: string | null;
  profile_key: string;
  expected_version: number;
  expected_publication_version: number;
  idempotency_key: string;
  payload: BrandProfileCommandPayload;
  payload_sha256: string;
  attempted_draft: BrandProfileDraftV1;
  created_at: string;
  command_sha256: string;
};

export type PendingBrandProfileCommandReadResult =
  | { kind: "absent" }
  | { kind: "valid"; command: PendingBrandProfileCommand }
  | {
      kind: "unverifiable";
      reason:
        | "AMBIGUOUS_IDENTITY"
        | "COMMAND_FINGERPRINT_MISMATCH"
        | "MALFORMED_STORAGE"
        | "PAYLOAD_FINGERPRINT_MISMATCH"
        | "UNSUPPORTED_OR_INVALID_COMMAND";
    };

type PendingCommandStorageReadResult =
  | { kind: "absent" }
  | { kind: "valid"; commands: PendingBrandProfileCommand[] }
  | Extract<PendingBrandProfileCommandReadResult, { kind: "unverifiable" }>;

type PendingCommandIdentity = {
  workspaceId: string;
  brand: string;
};

type PendingCommandStorage = Pick<
  Storage,
  "getItem" | "removeItem" | "setItem"
>;

type CreatePendingBrandProfileCommand = {
  action: BrandProfilePendingCommandAction;
  workspaceId: string;
  brand: string;
  profileId: string | null;
  profileKey: string;
  expectedVersion: number;
  expectedPublicationVersion: number;
  idempotencyKey: string;
  payload: BrandProfileCommandPayload;
  attemptedDraft: BrandProfileDraftV1;
  createdAt?: string;
};

function canonicalize(value: unknown): string {
  if (
    value === null ||
    typeof value === "boolean" ||
    typeof value === "string"
  ) {
    return JSON.stringify(value);
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      throw new TypeError("Brand Profile command payload must be finite JSON");
    }
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalize(item)).join(",")}]`;
  }
  if (!isPlainObject(value)) {
    throw new TypeError("Brand Profile command payload must be JSON");
  }
  const fields = Object.keys(value).sort();
  return `{${fields
    .map(
      (field) =>
        `${JSON.stringify(field)}:${canonicalize(value[field])}`,
    )
    .join(",")}}`;
}

function toHex(bytes: ArrayBuffer): string {
  return [...new Uint8Array(bytes)]
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
}

export async function canonicalizeBrandProfileCommandPayload(
  payload: BrandProfileCommandPayload,
): Promise<{
  canonicalPayload: string;
  payloadSha256: string;
}> {
  const canonicalPayload = canonicalize(payload);
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(canonicalPayload),
  );
  return { canonicalPayload, payloadSha256: toHex(digest) };
}

async function sha256(value: unknown): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(canonicalize(value)),
  );
  return toHex(digest);
}

function commandFingerprintSource(
  command: Omit<PendingBrandProfileCommand, "command_sha256">,
): Omit<PendingBrandProfileCommand, "command_sha256"> {
  return {
    schema_version: command.schema_version,
    action: command.action,
    workspace_id: command.workspace_id,
    brand: command.brand,
    profile_id: command.profile_id,
    profile_key: command.profile_key,
    expected_version: command.expected_version,
    expected_publication_version:
      command.expected_publication_version,
    idempotency_key: command.idempotency_key,
    payload: command.payload,
    payload_sha256: command.payload_sha256,
    attempted_draft: command.attempted_draft,
    created_at: command.created_at,
  };
}

function fingerprintPendingCommand(
  command: Omit<PendingBrandProfileCommand, "command_sha256">,
): Promise<string> {
  return sha256(commandFingerprintSource(command));
}

function isPlainObject(
  value: unknown,
): value is Record<string, unknown> {
  return (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value)
  );
}

function isSafeNonNegativeInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 0;
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isDraft(value: unknown): boolean {
  if (!isPlainObject(value)) return false;
  return (
    Array.isArray(value.rules) &&
    value.rules.every(
      (rule) =>
        isPlainObject(rule) &&
        typeof rule.code === "string" &&
        typeof rule.scope === "string" &&
        typeof rule.instruction === "string",
    ) &&
    Array.isArray(value.approved_colors) &&
    value.approved_colors.every(
      (color) =>
        isPlainObject(color) &&
        typeof color.name === "string" &&
        typeof color.value === "string",
    ) &&
    isStringArray(value.required_marks) &&
    isStringArray(value.prohibited_elements) &&
    isStringArray(value.tone_constraints) &&
    isStringArray(value.copy_constraints) &&
    typeof value.purpose === "string" &&
    typeof value.provider === "string" &&
    typeof value.requires_derivative === "boolean" &&
    Array.isArray(value.selected_assets) &&
    value.selected_assets.every(
      (selection) =>
        isPlainObject(selection) &&
        typeof selection.asset_version_id === "string" &&
        typeof selection.role === "string",
    )
  );
}

function payloadMatchesCommand(value: Record<string, unknown>): boolean {
  const payload = value.payload;
  if (!isPlainObject(payload) || !isDraft(value.attempted_draft)) {
    return false;
  }
  if (value.action === "CREATE") {
    return (
      value.profile_id === null &&
      value.expected_version === 0 &&
      payload.brand === value.brand &&
      payload.profile_key === value.profile_key &&
      isDraft(payload.draft) &&
      canonicalize(payload.draft) ===
        canonicalize(value.attempted_draft)
    );
  }
  if (
    value.profile_id === null ||
    payload.expected_version !== value.expected_version ||
    value.expected_version === 0
  ) {
    return false;
  }
  if (value.action === "UPDATE_DRAFT") {
    return (
      isDraft(payload.draft) &&
      canonicalize(payload.draft) ===
        canonicalize(value.attempted_draft)
    );
  }
  return (
    value.action === "PUBLISH" &&
    Object.keys(payload).every((field) => field === "expected_version")
  );
}

function isCanonicalTimestamp(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const parsed = Date.parse(value);
  return (
    Number.isFinite(parsed) &&
    new Date(parsed).toISOString() === value
  );
}

function isPendingCommand(
  value: unknown,
): value is PendingBrandProfileCommand {
  if (!isPlainObject(value)) return false;
  return (
    Object.keys(value).every((field) => COMMAND_FIELDS.has(field)) &&
    Object.keys(value).length === COMMAND_FIELDS.size &&
    value.schema_version === 1 &&
    (value.action === "CREATE" ||
      value.action === "UPDATE_DRAFT" ||
      value.action === "PUBLISH") &&
    typeof value.workspace_id === "string" &&
    TOKEN_PATTERN.test(value.workspace_id) &&
    typeof value.brand === "string" &&
    value.brand.length > 0 &&
    value.brand.length <= 128 &&
    value.brand === value.brand.trim() &&
    !CONTROL_CHARACTER_PATTERN.test(value.brand) &&
    (value.profile_id === null ||
      (typeof value.profile_id === "string" &&
        UUID_PATTERN.test(value.profile_id))) &&
    typeof value.profile_key === "string" &&
    TOKEN_PATTERN.test(value.profile_key) &&
    isSafeNonNegativeInteger(value.expected_version) &&
    isSafeNonNegativeInteger(value.expected_publication_version) &&
    typeof value.idempotency_key === "string" &&
    IDEMPOTENCY_KEY_PATTERN.test(value.idempotency_key) &&
    isPlainObject(value.payload) &&
    typeof value.payload_sha256 === "string" &&
    SHA256_PATTERN.test(value.payload_sha256) &&
    isDraft(value.attempted_draft) &&
    isCanonicalTimestamp(value.created_at) &&
    typeof value.command_sha256 === "string" &&
    SHA256_PATTERN.test(value.command_sha256) &&
    payloadMatchesCommand(value)
  );
}

async function readCommands(
  storage: PendingCommandStorage,
): Promise<PendingCommandStorageReadResult> {
  const raw = storage.getItem(STORAGE_KEY);
  if (raw === null) return { kind: "absent" };
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { kind: "unverifiable", reason: "MALFORMED_STORAGE" };
  }
  if (!Array.isArray(parsed)) {
    return { kind: "unverifiable", reason: "MALFORMED_STORAGE" };
  }
  if (!parsed.every(isPendingCommand)) {
    return {
      kind: "unverifiable",
      reason: "UNSUPPORTED_OR_INVALID_COMMAND",
    };
  }
  for (const command of parsed) {
    const fingerprint = await canonicalizeBrandProfileCommandPayload(
      command.payload,
    );
    if (fingerprint.payloadSha256 !== command.payload_sha256) {
      return {
        kind: "unverifiable",
        reason: "PAYLOAD_FINGERPRINT_MISMATCH",
      };
    }
    const commandFingerprint = await fingerprintPendingCommand(command);
    if (commandFingerprint !== command.command_sha256) {
      return {
        kind: "unverifiable",
        reason: "COMMAND_FINGERPRINT_MISMATCH",
      };
    }
  }
  return { kind: "valid", commands: parsed };
}

function writeCommands(
  storage: PendingCommandStorage,
  commands: readonly PendingBrandProfileCommand[],
): void {
  if (commands.length === 0) {
    storage.removeItem(STORAGE_KEY);
    return;
  }
  storage.setItem(STORAGE_KEY, JSON.stringify(commands));
}

function sameIdentity(
  command: PendingBrandProfileCommand,
  identity: PendingCommandIdentity,
): boolean {
  return (
    command.workspace_id === identity.workspaceId &&
    command.brand === identity.brand
  );
}

function sameCommand(
  candidate: PendingBrandProfileCommand,
  command: PendingBrandProfileCommand,
): boolean {
  return (
    candidate.schema_version === command.schema_version &&
    candidate.action === command.action &&
    candidate.workspace_id === command.workspace_id &&
    candidate.brand === command.brand &&
    candidate.profile_id === command.profile_id &&
    candidate.profile_key === command.profile_key &&
    candidate.expected_version === command.expected_version &&
    candidate.expected_publication_version ===
      command.expected_publication_version &&
    candidate.idempotency_key === command.idempotency_key &&
    candidate.payload_sha256 === command.payload_sha256 &&
    candidate.created_at === command.created_at &&
    candidate.command_sha256 === command.command_sha256
  );
}

export async function createPendingBrandProfileCommand(
  input: CreatePendingBrandProfileCommand,
): Promise<PendingBrandProfileCommand> {
  const { payloadSha256 } =
    await canonicalizeBrandProfileCommandPayload(input.payload);
  const command: Omit<
    PendingBrandProfileCommand,
    "command_sha256"
  > = {
    schema_version: 1,
    action: input.action,
    workspace_id: input.workspaceId,
    brand: input.brand,
    profile_id: input.profileId,
    profile_key: input.profileKey,
    expected_version: input.expectedVersion,
    expected_publication_version:
      input.expectedPublicationVersion,
    idempotency_key: input.idempotencyKey,
    payload: input.payload,
    payload_sha256: payloadSha256,
    attempted_draft: input.attemptedDraft,
    created_at: input.createdAt ?? new Date().toISOString(),
  };
  return {
    ...command,
    command_sha256: await fingerprintPendingCommand(command),
  };
}

export async function savePendingBrandProfileCommand(
  storage: PendingCommandStorage,
  command: PendingBrandProfileCommand,
): Promise<void> {
  if (!isPendingCommand(command)) {
    throw new Error("Brand Profile pending command is invalid.");
  }
  const fingerprint = await canonicalizeBrandProfileCommandPayload(
    command.payload,
  );
  if (fingerprint.payloadSha256 !== command.payload_sha256) {
    throw new Error("Brand Profile pending command fingerprint is invalid.");
  }
  if (
    (await fingerprintPendingCommand(command)) !== command.command_sha256
  ) {
    throw new Error(
      "Brand Profile pending command envelope fingerprint is invalid.",
    );
  }
  const stored = await readCommands(storage);
  if (stored.kind === "unverifiable") {
    throw new Error(
      `Brand Profile pending command storage is unverifiable: ${stored.reason}.`,
    );
  }
  const commands = stored.kind === "valid" ? stored.commands : [];
  if (
    commands.some((candidate) =>
      sameIdentity(candidate, {
        workspaceId: command.workspace_id,
        brand: command.brand,
      }),
    )
  ) {
    throw new Error(
      "A Brand Profile pending command already owns this workspace and brand.",
    );
  }
  writeCommands(storage, [...commands, command]);
}

export async function readPendingBrandProfileCommand(
  storage: PendingCommandStorage,
  identity: PendingCommandIdentity,
): Promise<PendingBrandProfileCommandReadResult> {
  const stored = await readCommands(storage);
  if (stored.kind !== "valid") return stored;
  const commands = stored.commands.filter((candidate) =>
    sameIdentity(candidate, identity),
  );
  if (commands.length === 0) return { kind: "absent" };
  if (commands.length > 1) {
    return { kind: "unverifiable", reason: "AMBIGUOUS_IDENTITY" };
  }
  return { kind: "valid", command: commands[0] };
}

export async function clearPendingBrandProfileCommand(
  storage: PendingCommandStorage,
  command: PendingBrandProfileCommand,
): Promise<boolean> {
  const stored = await readCommands(storage);
  if (stored.kind === "unverifiable") {
    throw new Error(
      `Brand Profile pending command storage is unverifiable: ${stored.reason}.`,
    );
  }
  if (stored.kind === "absent") return false;
  const retained = stored.commands.filter(
    (candidate) => !sameCommand(candidate, command),
  );
  if (retained.length === stored.commands.length) return false;
  writeCommands(storage, retained);
  return true;
}
