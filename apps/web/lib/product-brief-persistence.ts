import type { ProductBriefAnalysisRequestV1 } from "./generated/catalog-api";
import {
  pendingProductBriefCommandFor,
  productBriefCommandsMatch,
} from "./product-brief-workbench-state";
import type { PendingProductBriefCommand } from "./product-brief-workbench-state";

export type PendingProductBriefAnalysis = {
  payload: ProductBriefAnalysisRequestV1;
  idempotencyKey: string;
  priorProductBrief: {
    productBriefId: string;
    operationId: string;
  } | null;
};

type PersistedProductBriefBase = {
  workspaceId: string;
  productId: string;
  workflowId: string;
  assetVersionIds: string[];
  retentionDeadline: string;
};

export type PersistedProductBriefV1 = PersistedProductBriefBase & {
  schemaVersion: 1;
  productBriefId: string;
  operationId: string;
};

export type PersistedProductBriefV2 = PersistedProductBriefBase & {
  schemaVersion: 2;
  pendingAnalysis: PendingProductBriefAnalysis;
};

export type PersistedProductBriefV3 = PersistedProductBriefBase & {
  schemaVersion: 3;
  productBriefId: string;
  operationId: string;
  pendingCommand: PendingProductBriefCommand;
  commandStatus: "pending" | "version-conflict";
};

export type PersistedProductBrief =
  | PersistedProductBriefV1
  | PersistedProductBriefV2
  | PersistedProductBriefV3;

export type ProductBriefStorage = Pick<
  Storage,
  "getItem" | "setItem" | "removeItem"
>;

export type ProductBriefNamespaceStorage = ProductBriefStorage &
  Pick<Storage, "key" | "length">;

export type ProductBriefPersistenceIdentity = {
  workspaceId: string;
  productId: string;
};

type ProductBriefRetentionControllerOptions = {
  storage: ProductBriefNamespaceStorage;
  now?: () => number;
  schedule?: (callback: () => void, delayMs: number) => unknown;
  cancel?: (handle: unknown) => void;
  onExpired: (identity: ProductBriefPersistenceIdentity) => void;
};

export function defaultProductBriefStorage(
  browser: Pick<Window, "sessionStorage" | "localStorage"> = window,
): ProductBriefNamespaceStorage {
  return browser.sessionStorage;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
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

function hasExactKeys(
  value: Record<string, unknown>,
  required: string[],
): boolean {
  const keys = Object.keys(value);
  return (
    keys.length === required.length &&
    required.every((key) => key in value)
  );
}

function assetVersionIdsFor(
  value: unknown,
  minimumItems: number,
): string[] | null {
  if (
    !Array.isArray(value) ||
    value.length < minimumItems ||
    value.length > 8 ||
    !value.every((item) => isBoundedString(item, 1, 36)) ||
    new Set(value).size !== value.length
  ) {
    return null;
  }
  return [...value];
}

const PRODUCT_BRIEF_PERSISTENCE_PREFIX =
  "commercevision.product-brief.v2:";
const LEGACY_PRODUCT_BRIEF_PERSISTENCE_PREFIX =
  "commercevision.product-brief.v1:";
const PRODUCT_BRIEF_ACTIVE_IDENTITY_KEY =
  "commercevision.product-brief.active.v2";
const LEGACY_PRODUCT_BRIEF_ACTIVE_IDENTITY_KEY =
  "commercevision.product-brief.active.v1";

function isProductBriefPersistenceIdentity(
  value: unknown,
): value is ProductBriefPersistenceIdentity {
  return (
    isRecord(value) &&
    hasExactKeys(value, ["workspaceId", "productId"]) &&
    isBoundedString(value.workspaceId, 1, 128) &&
    isBoundedString(value.productId, 1, 36)
  );
}

export function productBriefPersistenceKey(
  identity: ProductBriefPersistenceIdentity,
): string {
  if (!isProductBriefPersistenceIdentity(identity)) {
    throw new TypeError(
      "ProductBrief persistence identity must include a bounded workspaceId and productId",
    );
  }
  return `${PRODUCT_BRIEF_PERSISTENCE_PREFIX}${encodeURIComponent(identity.workspaceId)}:${encodeURIComponent(identity.productId)}`;
}

function legacyProductBriefPersistenceKey(productId: string): string {
  return `${LEGACY_PRODUCT_BRIEF_PERSISTENCE_PREFIX}${productId}`;
}

function identityFromPersistenceKey(
  key: string,
): ProductBriefPersistenceIdentity | null {
  if (!key.startsWith(PRODUCT_BRIEF_PERSISTENCE_PREFIX)) return null;
  const encoded = key.slice(PRODUCT_BRIEF_PERSISTENCE_PREFIX.length);
  const separator = encoded.indexOf(":");
  if (separator < 1 || separator !== encoded.lastIndexOf(":")) return null;
  try {
    const identity = {
      workspaceId: decodeURIComponent(encoded.slice(0, separator)),
      productId: decodeURIComponent(encoded.slice(separator + 1)),
    };
    return isProductBriefPersistenceIdentity(identity) &&
      productBriefPersistenceKey(identity) === key
      ? identity
      : null;
  } catch {
    return null;
  }
}

export function activateProductBriefPersistenceIdentity(
  storage: ProductBriefNamespaceStorage,
  identity: ProductBriefPersistenceIdentity,
): boolean {
  const removeActiveMarkers = () => {
    try {
      storage.removeItem(PRODUCT_BRIEF_ACTIVE_IDENTITY_KEY);
      storage.removeItem(LEGACY_PRODUCT_BRIEF_ACTIVE_IDENTITY_KEY);
    } catch {
      // Identity cleanup is best effort when storage is unavailable.
    }
  };
  const failClosed = () => {
    clearProductBriefPersistenceNamespace(storage);
    removeActiveMarkers();
    return false;
  };

  if (!isProductBriefPersistenceIdentity(identity)) {
    return failClosed();
  }

  let previous: ProductBriefPersistenceIdentity | null = null;
  try {
    storage.removeItem(LEGACY_PRODUCT_BRIEF_ACTIVE_IDENTITY_KEY);
    const raw = storage.getItem(PRODUCT_BRIEF_ACTIVE_IDENTITY_KEY);
    let markerIsTrusted = raw !== null;
    if (raw !== null) {
      try {
        const candidate = JSON.parse(raw) as unknown;
        if (isProductBriefPersistenceIdentity(candidate)) {
          previous = candidate;
        } else {
          markerIsTrusted = false;
        }
      } catch {
        markerIsTrusted = false;
      }
    }

    const identityChanged =
      previous !== null &&
      (previous.workspaceId !== identity.workspaceId ||
        previous.productId !== identity.productId);
    if (!markerIsTrusted || identityChanged) {
      clearProductBriefPersistenceNamespace(storage);
    }

    const serializedIdentity = JSON.stringify(identity);
    storage.setItem(
      PRODUCT_BRIEF_ACTIVE_IDENTITY_KEY,
      serializedIdentity,
    );
    if (
      storage.getItem(PRODUCT_BRIEF_ACTIVE_IDENTITY_KEY) !==
      serializedIdentity
    ) {
      return failClosed();
    }
    return true;
  } catch {
    return failClosed();
  }
}

export function isActiveProductBriefRetentionDeadline(
  value: unknown,
  nowMs = Date.now(),
): value is string {
  if (
    typeof value !== "string" ||
    value.length > 64 ||
    !/(?:Z|[+-]\d{2}:\d{2})$/.test(value)
  ) {
    return false;
  }
  const deadlineMs = Date.parse(value);
  return Number.isFinite(deadlineMs) && deadlineMs > nowMs;
}

function pendingAnalysisFor(
  productId: string,
  workflowId: string,
  assetVersionIds: string[],
  candidate: unknown,
): PendingProductBriefAnalysis | null {
  if (
    !isRecord(candidate) ||
    !hasExactKeys(candidate, [
      "payload",
      "idempotencyKey",
      "priorProductBrief",
    ]) ||
    !isRecord(candidate.payload) ||
    !hasExactKeys(candidate.payload, [
      "workflow_id",
      "product_id",
      "asset_version_ids",
      "expected_workflow_version",
    ]) ||
    !isBoundedString(candidate.idempotencyKey, 8, 256) ||
    candidate.payload.product_id !== productId ||
    candidate.payload.workflow_id !== workflowId ||
    !isBoundedString(candidate.payload.product_id, 1, 36) ||
    !isBoundedString(candidate.payload.workflow_id, 1, 36) ||
    typeof candidate.payload.expected_workflow_version !== "number" ||
    !Number.isSafeInteger(candidate.payload.expected_workflow_version) ||
    candidate.payload.expected_workflow_version < 1
  ) {
    return null;
  }
  const payloadAssetVersionIds = assetVersionIdsFor(
    candidate.payload.asset_version_ids,
    1,
  );
  if (
    payloadAssetVersionIds === null ||
    JSON.stringify(payloadAssetVersionIds) !== JSON.stringify(assetVersionIds)
  ) {
    return null;
  }
  const priorProductBrief =
    candidate.priorProductBrief === null
      ? null
      : isRecord(candidate.priorProductBrief) &&
          hasExactKeys(candidate.priorProductBrief, [
            "productBriefId",
            "operationId",
          ]) &&
          isBoundedString(
            candidate.priorProductBrief.productBriefId,
            1,
            36,
          ) &&
          isBoundedString(
            candidate.priorProductBrief.operationId,
            1,
            36,
          )
        ? {
            productBriefId:
              candidate.priorProductBrief.productBriefId,
            operationId: candidate.priorProductBrief.operationId,
          }
        : undefined;
  if (priorProductBrief === undefined) return null;
  return {
    payload: {
      workflow_id: candidate.payload.workflow_id,
      product_id: candidate.payload.product_id,
      asset_version_ids: [
        payloadAssetVersionIds[0],
        ...payloadAssetVersionIds.slice(1),
      ],
      expected_workflow_version:
        candidate.payload.expected_workflow_version,
    },
    idempotencyKey: candidate.idempotencyKey,
    priorProductBrief,
  };
}

function parsePersistedProductBrief(
  identity: ProductBriefPersistenceIdentity,
  candidate: unknown,
  nowMs: number,
): PersistedProductBrief | null {
  if (
    !isRecord(candidate) ||
    candidate.workspaceId !== identity.workspaceId ||
    candidate.productId !== identity.productId ||
    !isBoundedString(candidate.workspaceId, 1, 128) ||
    !isBoundedString(candidate.productId, 1, 36) ||
    !isBoundedString(candidate.workflowId, 1, 36) ||
    !isActiveProductBriefRetentionDeadline(
      candidate.retentionDeadline,
      nowMs,
    )
  ) {
    return null;
  }
  const assetVersionIds = assetVersionIdsFor(
    candidate.assetVersionIds,
    candidate.schemaVersion === 2 ? 1 : 0,
  );
  if (assetVersionIds === null) return null;
  const base = {
    workspaceId: candidate.workspaceId,
    productId: candidate.productId,
    workflowId: candidate.workflowId,
    assetVersionIds,
    retentionDeadline: candidate.retentionDeadline,
  };
  if (candidate.schemaVersion === 1) {
    return hasExactKeys(candidate, [
      "schemaVersion",
      "workspaceId",
      "productId",
      "productBriefId",
      "operationId",
      "workflowId",
      "assetVersionIds",
      "retentionDeadline",
    ]) &&
      isBoundedString(candidate.productBriefId, 1, 36) &&
      isBoundedString(candidate.operationId, 1, 36)
      ? {
          schemaVersion: 1,
          ...base,
          productBriefId: candidate.productBriefId,
          operationId: candidate.operationId,
        }
      : null;
  }
  if (candidate.schemaVersion === 2) {
    if (
      !hasExactKeys(candidate, [
        "schemaVersion",
        "workspaceId",
        "productId",
        "workflowId",
        "assetVersionIds",
        "retentionDeadline",
        "pendingAnalysis",
      ])
    ) {
      return null;
    }
    const pendingAnalysis = pendingAnalysisFor(
      identity.productId,
      candidate.workflowId,
      assetVersionIds,
      candidate.pendingAnalysis,
    );
    return pendingAnalysis
      ? { schemaVersion: 2, ...base, pendingAnalysis }
      : null;
  }
  if (candidate.schemaVersion === 3) {
    if (
      !hasExactKeys(candidate, [
        "schemaVersion",
        "workspaceId",
        "productId",
        "productBriefId",
        "operationId",
        "workflowId",
        "assetVersionIds",
        "retentionDeadline",
        "pendingCommand",
        "commandStatus",
      ]) ||
      !isBoundedString(candidate.productBriefId, 1, 36) ||
      !isBoundedString(candidate.operationId, 1, 36) ||
      (candidate.commandStatus !== "pending" &&
        candidate.commandStatus !== "version-conflict")
    ) {
      return null;
    }
    const pendingCommand = pendingProductBriefCommandFor(
      identity.productId,
      candidate.productBriefId,
      candidate.pendingCommand,
    );
    if (
      !pendingCommand ||
      (candidate.commandStatus === "version-conflict" &&
        pendingCommand.kind !== "revise")
    ) {
      return null;
    }
    return {
      schemaVersion: 3,
      ...base,
      productBriefId: candidate.productBriefId,
      operationId: candidate.operationId,
      pendingCommand,
      commandStatus: candidate.commandStatus,
    };
  }
  return null;
}

export function readPersistedProductBrief(
  storage: ProductBriefStorage,
  identity: ProductBriefPersistenceIdentity,
  nowMs = Date.now(),
): PersistedProductBrief | null {
  if (!isProductBriefPersistenceIdentity(identity)) return null;
  const key = productBriefPersistenceKey(identity);
  try {
    storage.removeItem(legacyProductBriefPersistenceKey(identity.productId));
    const raw = storage.getItem(key);
    if (raw === null) return null;
    const parsed = parsePersistedProductBrief(
      identity,
      JSON.parse(raw) as unknown,
      nowMs,
    );
    if (parsed === null) storage.removeItem(key);
    return parsed;
  } catch {
    try {
      storage.removeItem(key);
    } catch {
      // Storage can be unavailable under browser privacy policies.
    }
    return null;
  }
}

function productBriefPersistenceKeys(
  storage: ProductBriefNamespaceStorage,
): string[] | null {
  const keys: string[] = [];
  try {
    for (let index = 0; index < storage.length; index += 1) {
      const key = storage.key(index);
      if (
        key?.startsWith(PRODUCT_BRIEF_PERSISTENCE_PREFIX) ||
        key?.startsWith(LEGACY_PRODUCT_BRIEF_PERSISTENCE_PREFIX)
      ) {
        keys.push(key);
      }
    }
    return keys;
  } catch {
    return null;
  }
}

export function clearProductBriefPersistenceNamespace(
  storage: ProductBriefNamespaceStorage,
): number {
  const keys = productBriefPersistenceKeys(storage);
  if (keys === null) return 0;
  let removed = 0;
  for (const key of keys) {
    try {
      storage.removeItem(key);
      if (storage.getItem(key) === null) removed += 1;
    } catch {
      // Continue clearing other task payloads if one key is inaccessible.
    }
  }
  return removed;
}

export function sweepExpiredPersistedProductBriefs(
  storage: ProductBriefNamespaceStorage,
  nowMs = Date.now(),
): number {
  const keys = productBriefPersistenceKeys(storage);
  if (keys === null) return 0;

  let removed = 0;
  for (const key of keys) {
    try {
      const raw = storage.getItem(key);
      if (raw === null) continue;
      const identity = identityFromPersistenceKey(key);
      const candidate =
        identity === null
          ? null
          : parsePersistedProductBrief(
              identity,
              JSON.parse(raw) as unknown,
              nowMs,
            );
      if (candidate !== null) {
        continue;
      }
      storage.removeItem(key);
      if (storage.getItem(key) === null) removed += 1;
    } catch {
      try {
        storage.removeItem(key);
        if (storage.getItem(key) === null) removed += 1;
      } catch {
        // Continue sweeping other task records when one key is inaccessible.
      }
    }
  }
  return removed;
}

export function writePersistedProductBrief(
  storage: ProductBriefStorage,
  value: PersistedProductBrief,
  nowMs = Date.now(),
): boolean {
  if (
    !isProductBriefPersistenceIdentity({
      workspaceId: value.workspaceId,
      productId: value.productId,
    })
  ) {
    return false;
  }
  const identity = {
    workspaceId: value.workspaceId,
    productId: value.productId,
  };
  const key = productBriefPersistenceKey(identity);
  const canonical = parsePersistedProductBrief(
    identity,
    value,
    nowMs,
  );
  if (canonical === null) {
    try {
      storage.removeItem(key);
    } catch {
      // A failed remove still means the mutation must not be dispatched.
    }
    return false;
  }
  try {
    storage.setItem(key, JSON.stringify(canonical));
    return true;
  } catch {
    return false;
  }
}

export function clearPersistedProductBrief(
  storage: ProductBriefStorage,
  identity: ProductBriefPersistenceIdentity,
): void {
  if (!isProductBriefPersistenceIdentity(identity)) return;
  try {
    storage.removeItem(productBriefPersistenceKey(identity));
  } catch {
    // Clearing is best effort when the browser denies storage access.
  }
  try {
    storage.removeItem(legacyProductBriefPersistenceKey(identity.productId));
  } catch {
    // Attempt every identity-bound key even when one removal is denied.
  }
}

const MAXIMUM_RETENTION_TIMER_DELAY_MS = 2_147_483_647;

export class ProductBriefRetentionController {
  private readonly storage: ProductBriefNamespaceStorage;
  private readonly now: () => number;
  private readonly schedule: (
    callback: () => void,
    delayMs: number,
  ) => unknown;
  private readonly cancel: (handle: unknown) => void;
  private readonly onExpired: (
    identity: ProductBriefPersistenceIdentity,
  ) => void;
  private active:
    | {
        identity: ProductBriefPersistenceIdentity;
        deadlineMs: number;
        activation: number;
      }
    | undefined;
  private timer: unknown;
  private activation = 0;
  private identity: ProductBriefPersistenceIdentity | undefined;

  constructor({
    storage,
    now = Date.now,
    schedule = (callback, delayMs) => setTimeout(callback, delayMs),
    cancel = (handle) =>
      clearTimeout(handle as ReturnType<typeof setTimeout>),
    onExpired,
  }: ProductBriefRetentionControllerOptions) {
    this.storage = storage;
    this.now = now;
    this.schedule = schedule;
    this.cancel = cancel;
    this.onExpired = onExpired;
  }

  activate(
    identity: ProductBriefPersistenceIdentity,
    retentionDeadline: string,
  ): boolean {
    if (!isProductBriefPersistenceIdentity(identity)) return false;
    sweepExpiredPersistedProductBriefs(this.storage, this.now());
    this.changeIdentity(identity);
    this.cancelTimer();
    this.activation += 1;
    const deadlineMs = Date.parse(retentionDeadline);
    if (
      !isActiveProductBriefRetentionDeadline(
        retentionDeadline,
        this.now(),
      ) ||
      !Number.isFinite(deadlineMs)
    ) {
      clearPersistedProductBrief(this.storage, identity);
      this.active = undefined;
      this.onExpired({ ...identity });
      return false;
    }
    this.active = {
      identity: { ...identity },
      deadlineMs,
      activation: this.activation,
    };
    return this.scheduleDeadline();
  }

  changeIdentity(identity: ProductBriefPersistenceIdentity): boolean {
    const previousIdentity = this.identity;
    if (
      previousIdentity?.workspaceId === identity.workspaceId &&
      previousIdentity.productId === identity.productId
    ) {
      return false;
    }
    this.cancelTimer();
    this.active = undefined;
    this.activation += 1;
    if (previousIdentity) {
      clearPersistedProductBrief(
        this.storage,
        previousIdentity,
      );
    }
    this.identity = { ...identity };
    return true;
  }

  dispose(): void {
    this.cancelTimer();
    this.active = undefined;
    this.activation += 1;
  }

  private scheduleDeadline(): boolean {
    const active = this.active;
    if (!active) return false;
    const remainingMs = active.deadlineMs - this.now();
    if (remainingMs <= 0) {
      this.expire(active);
      return false;
    }
    const delayMs = Math.min(
      remainingMs,
      MAXIMUM_RETENTION_TIMER_DELAY_MS,
    );
    try {
      this.timer = this.schedule(() => {
        this.timer = undefined;
        if (
          this.active?.activation !== active.activation ||
          this.active.identity.workspaceId !== active.identity.workspaceId ||
          this.active.identity.productId !== active.identity.productId
        ) {
          return;
        }
        if (this.now() < active.deadlineMs) {
          this.scheduleDeadline();
          return;
        }
        this.expire(active);
      }, delayMs);
      return true;
    } catch {
      this.timer = undefined;
      this.expire(active);
      return false;
    }
  }

  private expire(active: NonNullable<ProductBriefRetentionController["active"]>): void {
    clearPersistedProductBrief(this.storage, active.identity);
    sweepExpiredPersistedProductBriefs(this.storage, this.now());
    this.active = undefined;
    this.onExpired({ ...active.identity });
  }

  private cancelTimer(): void {
    if (this.timer !== undefined) {
      this.cancel(this.timer);
      this.timer = undefined;
    }
  }
}

export function createProductBriefRetentionController(
  options: ProductBriefRetentionControllerOptions,
): ProductBriefRetentionController {
  return new ProductBriefRetentionController(options);
}

export function persistedCommandMatches(
  persisted: PersistedProductBrief | null,
  command: PendingProductBriefCommand,
): persisted is PersistedProductBriefV3 {
  return (
    persisted?.schemaVersion === 3 &&
    productBriefCommandsMatch(command, persisted.pendingCommand)
  );
}
