export type CollectionRebuildState =
  | "REQUESTED"
  | "PROVISIONING"
  | "BACKFILLING"
  | "REPLAYING"
  | "RIGHTS_RESCAN"
  | "AWAITING_VALIDATION"
  | "VALIDATING"
  | "READY"
  | "ACTIVATING"
  | "ACTIVE"
  | "FAILED"
  | "RETIRING"
  | "RETIRED";

export type CollectionRebuildRequest = {
  vector_kind: "IMAGE" | "PRODUCT_FUSED";
  model_family: string;
  model_id: string;
  pinned_revision: string;
  dimension: number;
  schema_version: number;
  index_spec_version: string;
  expected_active_collection_version: number;
  expected_policy_pointer_version: number;
};

export type CollectionRebuildValidation = {
  expected_row_count: number;
  actual_row_count: number;
  missing_primary_key_count: number;
  unexpected_primary_key_count: number;
  sampled_visibility_count: number;
  sampled_visibility_failures: number;
  ann_recall_at_10: number;
  minimum_ann_recall_at_10: number;
  fixed_query_pass_count: number;
  fixed_query_total_count: number;
  unauthorized_result_count: number;
  queries_with_unauthorized_results: number;
  accepted: boolean;
};

export type CollectionRebuildResponse = {
  id: string;
  operation_id: string;
  vector_kind: "IMAGE" | "PRODUCT_FUSED";
  state: CollectionRebuildState;
  version: number;
  snapshot_watermark: string;
  replay_watermark: string | null;
  backfill_cursor: string | null;
  replay_cursor: string | null;
  processed_count: number;
  validation: CollectionRebuildValidation | null;
  failure_code: string | null;
  retire_after: string | null;
  created_at: string;
  updated_at: string;
  progress: Array<{
    sequence: number;
    state: CollectionRebuildState;
    processed_count: number;
    message_code: string;
    observed_at: string;
  }>;
};

type ErrorEnvelope = { code?: string; message?: string };

export class CollectionRebuildApiError extends Error {
  constructor(
    readonly status: number,
    readonly envelope?: ErrorEnvelope,
  ) {
    super(envelope?.message ?? `Collection rebuild request failed with ${status}`);
    this.name = "CollectionRebuildApiError";
  }
}

const STATES = new Set<CollectionRebuildState>([
  "REQUESTED",
  "PROVISIONING",
  "BACKFILLING",
  "REPLAYING",
  "RIGHTS_RESCAN",
  "AWAITING_VALIDATION",
  "VALIDATING",
  "READY",
  "ACTIVATING",
  "ACTIVE",
  "FAILED",
  "RETIRING",
  "RETIRED",
]);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

function record(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError(`${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

function integer(value: unknown, label: string, minimum = 0): number {
  if (!Number.isSafeInteger(value) || Number(value) < minimum) {
    throw new TypeError(`${label} must be an integer >= ${minimum}`);
  }
  return Number(value);
}

function date(value: unknown, label: string): string {
  if (typeof value !== "string" || Number.isNaN(Date.parse(value))) {
    throw new TypeError(`${label} must be an ISO timestamp`);
  }
  return value;
}

function nullableString(value: unknown, label: string): string | null {
  if (value === null) return null;
  if (typeof value !== "string") throw new TypeError(`${label} must be a string or null`);
  return value;
}

function validation(value: unknown): CollectionRebuildValidation | null {
  if (value === null) return null;
  const item = record(value, "validation");
  const result = {
    expected_row_count: integer(item.expected_row_count, "expected_row_count"),
    actual_row_count: integer(item.actual_row_count, "actual_row_count"),
    missing_primary_key_count: integer(item.missing_primary_key_count, "missing_primary_key_count"),
    unexpected_primary_key_count: integer(item.unexpected_primary_key_count, "unexpected_primary_key_count"),
    sampled_visibility_count: integer(item.sampled_visibility_count, "sampled_visibility_count"),
    sampled_visibility_failures: integer(item.sampled_visibility_failures, "sampled_visibility_failures"),
    ann_recall_at_10: Number(item.ann_recall_at_10),
    minimum_ann_recall_at_10: Number(item.minimum_ann_recall_at_10),
    fixed_query_pass_count: integer(item.fixed_query_pass_count, "fixed_query_pass_count"),
    fixed_query_total_count: integer(item.fixed_query_total_count, "fixed_query_total_count"),
    unauthorized_result_count: integer(item.unauthorized_result_count, "unauthorized_result_count"),
    queries_with_unauthorized_results: integer(item.queries_with_unauthorized_results, "queries_with_unauthorized_results"),
    accepted: item.accepted,
  };
  if (
    typeof result.accepted !== "boolean" ||
    !Number.isFinite(result.ann_recall_at_10) ||
    !Number.isFinite(result.minimum_ann_recall_at_10)
  ) {
    throw new TypeError("validation metrics are invalid");
  }
  return result as CollectionRebuildValidation;
}

export function decodeCollectionRebuild(value: unknown): CollectionRebuildResponse {
  const source = record(value, "collection rebuild");
  if (typeof source.id !== "string" || !UUID.test(source.id)) {
    throw new TypeError("collection rebuild id is invalid");
  }
  if (typeof source.operation_id !== "string" || !UUID.test(source.operation_id)) {
    throw new TypeError("collection rebuild operation id is invalid");
  }
  if (typeof source.state !== "string" || !STATES.has(source.state as CollectionRebuildState)) {
    throw new TypeError("collection rebuild state is invalid");
  }
  if (source.vector_kind !== "IMAGE" && source.vector_kind !== "PRODUCT_FUSED") {
    throw new TypeError("collection rebuild vector kind is invalid");
  }
  if (!Array.isArray(source.progress) || source.progress.length > 200) {
    throw new TypeError("collection rebuild progress is invalid");
  }
  const progress = source.progress.map((value, index) => {
    const item = record(value, `progress[${index}]`);
    if (typeof item.state !== "string" || !STATES.has(item.state as CollectionRebuildState)) {
      throw new TypeError(`progress[${index}].state is invalid`);
    }
    if (typeof item.message_code !== "string") {
      throw new TypeError(`progress[${index}].message_code is invalid`);
    }
    return {
      sequence: integer(item.sequence, `progress[${index}].sequence`, 1),
      state: item.state as CollectionRebuildState,
      processed_count: integer(item.processed_count, `progress[${index}].processed_count`),
      message_code: item.message_code,
      observed_at: date(item.observed_at, `progress[${index}].observed_at`),
    };
  });
  return {
    id: source.id,
    operation_id: source.operation_id,
    vector_kind: source.vector_kind,
    state: source.state as CollectionRebuildState,
    version: integer(source.version, "version", 1),
    snapshot_watermark: date(source.snapshot_watermark, "snapshot_watermark"),
    replay_watermark: source.replay_watermark === null ? null : date(source.replay_watermark, "replay_watermark"),
    backfill_cursor: nullableString(source.backfill_cursor, "backfill_cursor"),
    replay_cursor: nullableString(source.replay_cursor, "replay_cursor"),
    processed_count: integer(source.processed_count, "processed_count"),
    validation: validation(source.validation),
    failure_code: nullableString(source.failure_code, "failure_code"),
    retire_after: source.retire_after === null ? null : date(source.retire_after, "retire_after"),
    created_at: date(source.created_at, "created_at"),
    updated_at: date(source.updated_at, "updated_at"),
    progress,
  };
}

export class CollectionRebuildApi {
  constructor(
    private readonly workspaceId = "catalog-demo",
    private readonly baseUrl = "",
  ) {}

  private async send(path: string, init?: RequestInit): Promise<CollectionRebuildResponse> {
    const response = await fetch(`${this.baseUrl}/api/v1${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        "X-Workspace-Id": this.workspaceId,
        ...init?.headers,
      },
      cache: "no-store",
    });
    const body: unknown = await response.json();
    if (!response.ok) throw new CollectionRebuildApiError(response.status, record(body, "error"));
    return decodeCollectionRebuild(body);
  }

  request(payload: CollectionRebuildRequest): Promise<CollectionRebuildResponse> {
    return this.send("/collections/rebuilds", {
      method: "POST",
      headers: { "Idempotency-Key": `web-rebuild-${crypto.randomUUID()}` },
      body: JSON.stringify(payload),
    });
  }

  get(id: string): Promise<CollectionRebuildResponse> {
    if (!UUID.test(id)) throw new TypeError("rebuild id is invalid");
    return this.send(`/collections/rebuilds/${encodeURIComponent(id)}`);
  }

  validate(id: string, expectedVersion: number): Promise<CollectionRebuildResponse> {
    return this.action(id, "validate", expectedVersion);
  }

  activate(id: string, expectedVersion: number): Promise<CollectionRebuildResponse> {
    return this.action(id, "activate", expectedVersion);
  }

  private action(id: string, action: "validate" | "activate", version: number) {
    if (!UUID.test(id) || !Number.isSafeInteger(version) || version < 1) {
      throw new TypeError("rebuild action identity is invalid");
    }
    return this.send(`/collections/rebuilds/${encodeURIComponent(id)}:${action}`, {
      method: "POST",
      body: JSON.stringify({ expected_version: version }),
    });
  }
}
