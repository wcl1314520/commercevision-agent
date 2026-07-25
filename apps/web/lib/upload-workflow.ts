import type { UploadSessionCreateRequestV1 } from "./generated/catalog-api";

export type UploadStage =
  | "OPEN"
  | "UPLOADING"
  | "UPLOADED"
  | "FINALIZING"
  | "FINALIZED";

export type FinalizeAttempt = {
  idempotencyKey: string;
  request: {
    expected_version: number;
  };
};

export type PersistedCreateUpload = {
  schemaVersion: 1;
  createIdempotencyKey: string;
  createRequest: UploadSessionCreateRequestV1;
  stage: "CREATING";
};

export type PersistedSessionUpload = {
  schemaVersion: 1;
  sessionId: string;
  finalizeAttempt: FinalizeAttempt;
  stage: UploadStage;
  abortIdempotencyKey?: string;
  createIdempotencyKey?: string;
  createRequest?: UploadSessionCreateRequestV1;
  assetId?: string;
};

export type PersistedUpload = PersistedCreateUpload | PersistedSessionUpload;

export type UploadWorkflowEvent =
  | {
      type: "CREATE_STARTED";
      createIdempotencyKey: string;
      createRequest: UploadSessionCreateRequestV1;
    }
  | {
      type: "SESSION_OPENED";
      sessionId: string;
      expectedVersion: number;
      finalizeIdempotencyKey?: string;
    }
  | {
      type: "UPLOAD_STAGE_CHANGED";
      stage: "UPLOADING" | "UPLOADED";
    }
  | {
      type: "ABORT_KEY_ASSIGNED";
      idempotencyKey: string;
    }
  | {
      type: "FINALIZE_STARTED";
    }
  | {
      type: "FINALIZE_RECONCILED";
      idempotencyKey: string;
      expectedVersion: number;
      nextStage: "OPEN" | "FINALIZING";
    }
  | {
      type: "FINALIZED";
      sessionId: string;
      assetId: string;
    };

const CREATE_REQUEST_KEYS = new Set([
  "retention_class",
  "asset_kind",
  "filename",
  "declared_mime",
  "byte_length",
  "sha256",
  "workflow_id",
  "product_id",
  "sku_id",
  "category",
  "role",
]);
const SESSION_STAGES = new Set<UploadStage>([
  "OPEN",
  "UPLOADING",
  "UPLOADED",
  "FINALIZING",
  "FINALIZED",
]);
const FINALIZE_ATTEMPT_KEYS = new Set(["idempotencyKey", "request"]);
const FINALIZE_REQUEST_KEYS = new Set(["expected_version"]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasOnlyKeys(
  value: Record<string, unknown>,
  allowed: ReadonlySet<string>,
): boolean {
  return Object.keys(value).every((key) => allowed.has(key));
}

function isOptionalString(value: unknown): boolean {
  return value === undefined || value === null || typeof value === "string";
}

function isIdentity(value: unknown): value is string {
  return typeof value === "string" && value.length >= 1 && value.length <= 256;
}

function isExpectedVersion(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 1;
}

function isCreateRequest(value: unknown): value is UploadSessionCreateRequestV1 {
  if (!isRecord(value) || !hasOnlyKeys(value, CREATE_REQUEST_KEYS)) {
    return false;
  }
  return (
    (value.retention_class === "TASK" ||
      value.retention_class === "FOUNDATION") &&
    (value.asset_kind === undefined || value.asset_kind === "IMAGE") &&
    typeof value.filename === "string" &&
    typeof value.declared_mime === "string" &&
    Number.isSafeInteger(value.byte_length) &&
    Number(value.byte_length) >= 1 &&
    typeof value.sha256 === "string" &&
    /^[a-f0-9]{64}$/.test(value.sha256) &&
    isOptionalString(value.workflow_id) &&
    isOptionalString(value.product_id) &&
    isOptionalString(value.sku_id) &&
    typeof value.category === "string" &&
    typeof value.role === "string"
  );
}

function isFinalizeAttempt(value: unknown): value is FinalizeAttempt {
  if (!isRecord(value) || !hasOnlyKeys(value, FINALIZE_ATTEMPT_KEYS)) {
    return false;
  }
  return (
    isIdentity(value.idempotencyKey) &&
    isRecord(value.request) &&
    hasOnlyKeys(value.request, FINALIZE_REQUEST_KEYS) &&
    isExpectedVersion(value.request.expected_version)
  );
}

function decodeCreateUpload(
  value: Record<string, unknown>,
): PersistedCreateUpload | null {
  if (
    (value.schemaVersion !== undefined && value.schemaVersion !== 1) ||
    value.stage !== "CREATING" ||
    !isIdentity(value.createIdempotencyKey) ||
    !isCreateRequest(value.createRequest)
  ) {
    return null;
  }
  return {
    schemaVersion: 1,
    createIdempotencyKey: value.createIdempotencyKey,
    createRequest: value.createRequest,
    stage: "CREATING",
  };
}

function optionalSessionFacts(
  value: Record<string, unknown>,
): Pick<
  PersistedSessionUpload,
  "abortIdempotencyKey" | "assetId" | "createIdempotencyKey" | "createRequest"
> | null {
  if (
    (value.abortIdempotencyKey !== undefined &&
      !isIdentity(value.abortIdempotencyKey)) ||
    (value.assetId !== undefined && !isIdentity(value.assetId)) ||
    !(
      (value.createIdempotencyKey === undefined &&
        value.createRequest === undefined) ||
      (isIdentity(value.createIdempotencyKey) &&
        isCreateRequest(value.createRequest))
    )
  ) {
    return null;
  }
  const facts: Pick<
    PersistedSessionUpload,
    "abortIdempotencyKey" | "assetId" | "createIdempotencyKey" | "createRequest"
  > = {};
  if (typeof value.abortIdempotencyKey === "string") {
    facts.abortIdempotencyKey = value.abortIdempotencyKey;
  }
  if (typeof value.assetId === "string") {
    facts.assetId = value.assetId;
  }
  if (
    typeof value.createIdempotencyKey === "string" &&
    isCreateRequest(value.createRequest)
  ) {
    facts.createIdempotencyKey = value.createIdempotencyKey;
    facts.createRequest = value.createRequest;
  }
  return facts;
}

function decodeSessionUpload(
  value: Record<string, unknown>,
): PersistedSessionUpload | null {
  const isCurrent = value.schemaVersion === 1;
  const stage = value.stage;
  const finalizeAttempt = isCurrent
    ? value.finalizeAttempt
    : {
        idempotencyKey: value.finalizeIdempotencyKey,
        request: { expected_version: value.finalizeExpectedVersion },
      };
  const optionalFacts = optionalSessionFacts(value);
  if (
    !isIdentity(value.sessionId) ||
    typeof stage !== "string" ||
    !SESSION_STAGES.has(stage as UploadStage) ||
    !isFinalizeAttempt(finalizeAttempt) ||
    optionalFacts === null
  ) {
    return null;
  }
  return {
    schemaVersion: 1,
    sessionId: value.sessionId,
    finalizeAttempt,
    stage: stage as UploadStage,
    ...optionalFacts,
  };
}

export function decodePersistedUpload(raw: string | null): PersistedUpload | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!isRecord(parsed)) return null;
    if (parsed.stage === "CREATING") {
      return decodeCreateUpload(parsed);
    }
    return decodeSessionUpload(parsed);
  } catch {
    return null;
  }
}

export function encodePersistedUpload(value: PersistedUpload): string {
  return JSON.stringify(value);
}

export function uploadStorageKey(productId: string): string {
  return `commercevision:upload:catalog-demo:${productId}`;
}

function requireSessionUpload(
  current: PersistedUpload | null,
  event: UploadWorkflowEvent["type"],
): PersistedSessionUpload {
  if (current === null || current.stage === "CREATING") {
    throw new Error(`${event} requires an established upload session`);
  }
  return current;
}

export function reduceUploadWorkflow(
  current: PersistedUpload | null,
  event: UploadWorkflowEvent,
): PersistedUpload {
  switch (event.type) {
    case "CREATE_STARTED":
      if (current !== null) {
        throw new Error("CREATE_STARTED requires an empty workflow");
      }
      return {
        schemaVersion: 1,
        createIdempotencyKey: event.createIdempotencyKey,
        createRequest: event.createRequest,
        stage: "CREATING",
      };
    case "SESSION_OPENED": {
      if (current === null) {
        throw new Error("SESSION_OPENED requires a persisted create attempt");
      }
      if (current.stage === "CREATING") {
        if (!event.finalizeIdempotencyKey) {
          throw new Error("SESSION_OPENED requires a finalize idempotency key");
        }
        return {
          schemaVersion: 1,
          sessionId: event.sessionId,
          finalizeAttempt: {
            idempotencyKey: event.finalizeIdempotencyKey,
            request: { expected_version: event.expectedVersion },
          },
          stage: "OPEN",
          createIdempotencyKey: current.createIdempotencyKey,
          createRequest: current.createRequest,
        };
      }
      return {
        ...current,
        sessionId: event.sessionId,
      };
    }
    case "UPLOAD_STAGE_CHANGED": {
      const session = requireSessionUpload(current, event.type);
      const allowed =
        (event.stage === "UPLOADING" && session.stage === "OPEN") ||
        (event.stage === "UPLOADED" && session.stage === "UPLOADING");
      if (!allowed) {
        throw new Error(
          `invalid upload transition ${session.stage} -> ${event.stage}`,
        );
      }
      return { ...session, stage: event.stage };
    }
    case "ABORT_KEY_ASSIGNED":
      return {
        ...requireSessionUpload(current, event.type),
        abortIdempotencyKey: event.idempotencyKey,
      };
    case "FINALIZE_STARTED": {
      const session = requireSessionUpload(current, event.type);
      if (
        session.stage !== "UPLOADING" &&
        session.stage !== "UPLOADED" &&
        session.stage !== "FINALIZING"
      ) {
        throw new Error(`cannot finalize an upload in ${session.stage}`);
      }
      return { ...session, stage: "FINALIZING" };
    }
    case "FINALIZE_RECONCILED": {
      const session = requireSessionUpload(current, event.type);
      if (session.stage !== "FINALIZING") {
        throw new Error("FINALIZE_RECONCILED requires a finalizing attempt");
      }
      return {
        ...session,
        finalizeAttempt: {
          idempotencyKey: event.idempotencyKey,
          request: { expected_version: event.expectedVersion },
        },
        stage: event.nextStage,
      };
    }
    case "FINALIZED":
      return {
        ...requireSessionUpload(current, event.type),
        sessionId: event.sessionId,
        stage: "FINALIZED",
        assetId: event.assetId,
      };
  }
}
