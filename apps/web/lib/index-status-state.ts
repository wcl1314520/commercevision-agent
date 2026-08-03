import type { AssetIndexStatusResponseV1 } from "./generated/catalog-api";

const INDEX_STATES = new Set<AssetIndexStatusResponseV1["state"]>([
  "NOT_REQUESTED",
  "PENDING",
  "PROCESSING",
  "INDEXED",
  "RETRYABLE_FAILED",
  "PERMANENT_FAILED",
  "STALE",
  "DELETE_PENDING",
  "DELETED",
]);
const INDEX_STATUS_FIELDS = new Set([
  "asset_id",
  "asset_version_id",
  "state",
  "retryable",
  "failure_reason",
  "indexed_at",
  "updated_at",
]);
const UTC_TIMESTAMP_PATTERN =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(?:Z|\+00:00)$/;
const FAILURE_MESSAGES: Readonly<Record<string, string>> = {
  PROVIDER_THROTTLED: "服务繁忙，正在等待可用容量",
  EMBEDDING_THROTTLED: "服务繁忙，正在等待可用容量",
  EMBEDDING_TIMEOUT: "索引服务响应超时",
  EMBEDDING_UNAVAILABLE: "索引服务暂时不可用",
  VECTOR_NOT_FOUND_AFTER_UNKNOWN_UPSERT: "未确认索引写入，正在安全重试",
};

function optionalString(value: unknown, field: string): string | null {
  if (value === null) return null;
  if (typeof value !== "string" || value.length === 0) {
    throw new TypeError(`${field} must be a non-empty string or null`);
  }
  return value;
}

function optionalDateTime(value: unknown, field: string): string | null {
  const decoded = optionalString(value, field);
  if (decoded === null) return null;
  const match = UTC_TIMESTAMP_PATTERN.exec(decoded);
  const timestamp = Date.parse(decoded);
  if (!match || !Number.isFinite(timestamp)) {
    throw new TypeError(`${field} must be an ISO date-time or null`);
  }
  const parsed = new Date(timestamp);
  const [, year, month, day, hour, minute, second] = match;
  if (
    parsed.getUTCFullYear() !== Number(year) ||
    parsed.getUTCMonth() + 1 !== Number(month) ||
    parsed.getUTCDate() !== Number(day) ||
    parsed.getUTCHours() !== Number(hour) ||
    parsed.getUTCMinutes() !== Number(minute) ||
    parsed.getUTCSeconds() !== Number(second)
  ) {
    throw new TypeError(`${field} must be an ISO date-time or null`);
  }
  return decoded;
}

export function indexStatusRetryDelayMs(failedRequests: number): number {
  return Math.min(5_000 * 2 ** Math.max(0, failedRequests - 1), 30_000);
}

function friendlyFailureReason(reason: string | null): string {
  return reason ? (FAILURE_MESSAGES[reason] ?? "索引暂时无法完成") : "索引暂时无法完成";
}

export function decodeAssetIndexStatus(
  value: unknown,
): AssetIndexStatusResponseV1 {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError("IMAGE index status must be an object");
  }
  const record = value as Record<string, unknown>;
  for (const field of Object.keys(record)) {
    if (!INDEX_STATUS_FIELDS.has(field)) {
      throw new TypeError(`IMAGE index status has unexpected field ${field}`);
    }
  }
  if (typeof record.asset_id !== "string" || record.asset_id.length === 0) {
    throw new TypeError("asset_id must be a non-empty string");
  }
  if (
    typeof record.state !== "string" ||
    !INDEX_STATES.has(record.state as AssetIndexStatusResponseV1["state"])
  ) {
    throw new TypeError("IMAGE index status state is invalid");
  }
  if (typeof record.retryable !== "boolean") {
    throw new TypeError("IMAGE index status retryable must be boolean");
  }
  return {
    asset_id: record.asset_id,
    asset_version_id: optionalString(record.asset_version_id, "asset_version_id"),
    state: record.state as AssetIndexStatusResponseV1["state"],
    retryable: record.retryable,
    failure_reason: optionalString(record.failure_reason, "failure_reason"),
    indexed_at: optionalDateTime(record.indexed_at, "indexed_at"),
    updated_at: optionalDateTime(record.updated_at, "updated_at"),
  };
}

export function shouldRefreshIndexStatus(
  state: AssetIndexStatusResponseV1["state"],
): boolean {
  return (
    state === "PENDING" ||
    state === "PROCESSING" ||
    state === "RETRYABLE_FAILED" ||
    state === "DELETE_PENDING"
  );
}

export function indexStatusPresentation(status: AssetIndexStatusResponseV1): {
  tone: "neutral" | "progress" | "success" | "retry" | "error";
  detail: string | null;
} {
  if (status.state === "INDEXED") return { tone: "success", detail: null };
  if (status.retryable) {
    return {
      tone: "retry",
      detail: `系统将自动重试：${friendlyFailureReason(status.failure_reason ?? null)}`,
    };
  }
  if (
    status.state === "PERMANENT_FAILED" ||
    status.state === "STALE" ||
    status.state === "DELETED"
  ) {
    return {
      tone: "error",
      detail: `原因：${friendlyFailureReason(status.failure_reason ?? null)}`,
    };
  }
  if (shouldRefreshIndexStatus(status.state)) {
    return { tone: "progress", detail: null };
  }
  return { tone: "neutral", detail: null };
}

export function acceptIndexStatusResponse(
  current: { assetId: string; requestEpoch: number },
  request: { assetId: string; requestEpoch: number },
  status: AssetIndexStatusResponseV1,
): AssetIndexStatusResponseV1 | null {
  if (
    current.assetId !== request.assetId ||
    current.requestEpoch !== request.requestEpoch ||
    status.asset_id !== current.assetId
  ) {
    return null;
  }
  return status;
}
