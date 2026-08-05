const SESSION_SCHEMA_VERSION = 1;
const MAXIMUM_SESSION_BYTES = 64 * 1024;
const WORKSPACE_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export type CreativePlanRecoverableDraft = {
  baseVersionId: string;
  baseVersionNumber: number;
  payloadText: string;
  revisionReason: string;
};

export type CreativePlanReviewSession = {
  workspaceId: string;
  workflowId: string;
  creativePlanId: string;
  selectedVersionNumber: number;
  streamCursor: string | null;
  draft: CreativePlanRecoverableDraft | null;
};

type SessionStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

function record(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError("Creative Plan review session is invalid");
  }
  return value as Record<string, unknown>;
}

function exactKeys(value: Record<string, unknown>, keys: readonly string[]): void {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  if (
    actual.length !== expected.length ||
    actual.some((key, index) => key !== expected[index])
  ) {
    throw new TypeError("Creative Plan review session has unknown fields");
  }
}

function positiveInteger(value: unknown): number {
  if (!Number.isSafeInteger(value) || (value as number) < 1) {
    throw new TypeError("Creative Plan review version is invalid");
  }
  return value as number;
}

function uuid(value: unknown): string {
  if (typeof value !== "string" || !UUID_PATTERN.test(value)) {
    throw new TypeError("Creative Plan review identity is invalid");
  }
  return value;
}

function decodeDraft(value: unknown): CreativePlanRecoverableDraft | null {
  if (value === null) return null;
  const draft = record(value);
  exactKeys(draft, [
    "baseVersionId",
    "baseVersionNumber",
    "payloadText",
    "revisionReason",
  ]);
  if (
    typeof draft.revisionReason !== "string" ||
    draft.revisionReason.length > 512
  ) {
    throw new TypeError("Creative Plan revision reason is invalid");
  }
  if (
    typeof draft.payloadText !== "string" ||
    draft.payloadText.length < 1 ||
    draft.payloadText.length > 60 * 1024
  ) {
    throw new TypeError("Creative Plan draft text is invalid");
  }
  return {
    baseVersionId: uuid(draft.baseVersionId),
    baseVersionNumber: positiveInteger(draft.baseVersionNumber),
    payloadText: draft.payloadText,
    revisionReason: draft.revisionReason,
  };
}

function decodeSession(value: unknown, workspaceId: string): CreativePlanReviewSession {
  const session = record(value);
  exactKeys(session, [
    "schemaVersion",
    "workspaceId",
    "workflowId",
    "creativePlanId",
    "selectedVersionNumber",
    "streamCursor",
    "draft",
  ]);
  if (session.schemaVersion !== SESSION_SCHEMA_VERSION) {
    throw new TypeError("Creative Plan review session schema is unsupported");
  }
  if (session.workspaceId !== workspaceId) {
    throw new TypeError("Creative Plan review session crosses workspaces");
  }
  const streamCursor = session.streamCursor;
  if (
    streamCursor !== null &&
    (typeof streamCursor !== "string" ||
      streamCursor.length < 1 ||
      streamCursor.length > 256 ||
      /[\r\n]/.test(streamCursor))
  ) {
    throw new TypeError("Workflow stream cursor is invalid");
  }
  return {
    workspaceId,
    workflowId: uuid(session.workflowId),
    creativePlanId: uuid(session.creativePlanId),
    selectedVersionNumber: positiveInteger(session.selectedVersionNumber),
    streamCursor,
    draft: decodeDraft(session.draft),
  };
}

export function creativePlanReviewSessionKey(workspaceId: string): string {
  if (!WORKSPACE_PATTERN.test(workspaceId)) {
    throw new TypeError("workspaceId is invalid");
  }
  return `commercevision:creative-plan-review:v1:${workspaceId}`;
}

export function readCreativePlanReviewSession(
  storage: SessionStorage,
  workspaceId: string,
): CreativePlanReviewSession | null {
  const key = creativePlanReviewSessionKey(workspaceId);
  try {
    const raw = storage.getItem(key);
    if (raw === null) return null;
    if (new TextEncoder().encode(raw).byteLength > MAXIMUM_SESSION_BYTES) {
      throw new TypeError("Creative Plan review session is too large");
    }
    return decodeSession(JSON.parse(raw), workspaceId);
  } catch {
    try {
      storage.removeItem(key);
    } catch {
      // Storage denial is an unavailable cache, never an application failure.
    }
    return null;
  }
}

export function writeCreativePlanReviewSession(
  storage: SessionStorage,
  session: CreativePlanReviewSession,
): void {
  const decoded = decodeSession(
    { schemaVersion: SESSION_SCHEMA_VERSION, ...session },
    session.workspaceId,
  );
  const serialized = JSON.stringify({
    schemaVersion: SESSION_SCHEMA_VERSION,
    ...decoded,
  });
  if (new TextEncoder().encode(serialized).byteLength > MAXIMUM_SESSION_BYTES) {
    throw new TypeError("Creative Plan review session is too large");
  }
  storage.setItem(creativePlanReviewSessionKey(session.workspaceId), serialized);
}

export function clearCreativePlanReviewSession(
  storage: SessionStorage,
  workspaceId: string,
): void {
  storage.removeItem(creativePlanReviewSessionKey(workspaceId));
}
