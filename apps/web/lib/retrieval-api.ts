import type {
  ErrorResponse,
  RetrievalChannel,
  RetrievalCitationV1,
  RetrievalDegradationV1,
  RetrievalQueryV1,
  RetrievalResponseV1,
  RetrievalScoreBreakdownV1,
  RetrievalTemporaryReferenceV1,
} from "./generated/catalog-api";

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const TOKEN_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const PREVIEW_TOKEN_PATTERN = /^[A-Za-z0-9_-]{32,128}$/;
const CHANNELS = new Set<RetrievalChannel>([
  "IMAGE_DENSE",
  "PRODUCT_FUSED_DENSE",
  "LEXICAL",
  "BRAND_PROFILE",
  "EXPLICIT",
]);

type JsonObject = Record<string, unknown>;

export type RetrievalScoreEvidence = RetrievalScoreBreakdownV1 & {
  channel_ranks: Partial<Record<RetrievalChannel, number>>;
  channel_raw_scores: Partial<Record<RetrievalChannel, number>>;
};

export type RetrievalCitation = Omit<RetrievalCitationV1, "score"> & {
  score: RetrievalScoreEvidence;
};

export type RetrievalResponse = Omit<RetrievalResponseV1, "citations"> & {
  citations: RetrievalCitation[];
};

export type RetrievalTemporaryReference = Omit<
  RetrievalTemporaryReferenceV1,
  "required_headers"
> & {
  required_headers: Record<string, string>;
};

export class RetrievalApiError extends Error {
  readonly status: number;
  readonly envelope?: ErrorResponse;

  constructor(status: number, envelope?: ErrorResponse) {
    super(envelope?.message ?? `Retrieval request failed with ${status}`);
    this.name = "RetrievalApiError";
    this.status = status;
    this.envelope = envelope;
  }
}

function object(value: unknown, field: string): JsonObject {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError(`${field} must be an object`);
  }
  return value as JsonObject;
}

function text(value: unknown, field: string, maximum = 8192): string {
  if (typeof value !== "string" || value.length < 1 || value.length > maximum) {
    throw new TypeError(`${field} must be bounded text`);
  }
  return value;
}

function token(value: unknown, field: string): string {
  const result = text(value, field, 128);
  if (!TOKEN_PATTERN.test(result)) throw new TypeError(`${field} is invalid`);
  return result;
}

function uuid(value: unknown, field: string): string {
  const result = text(value, field, 36);
  if (!UUID_PATTERN.test(result)) throw new TypeError(`${field} is invalid`);
  return result;
}

function integer(
  value: unknown,
  field: string,
  minimum: number,
  maximum = Number.MAX_SAFE_INTEGER,
): number {
  if (
    typeof value !== "number" ||
    !Number.isSafeInteger(value) ||
    value < minimum ||
    value > maximum
  ) {
    throw new TypeError(`${field} must be a bounded integer`);
  }
  return value;
}

function finite(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new TypeError(`${field} must be finite`);
  }
  return value;
}

function utcTimestamp(value: unknown, field: string): string {
  const result = text(value, field, 64);
  if (!/(?:Z|[+-][0-9]{2}:[0-9]{2})$/.test(result)) {
    throw new TypeError(`${field} must be timezone-aware`);
  }
  const timestamp = Date.parse(result);
  if (!Number.isFinite(timestamp)) throw new TypeError(`${field} is invalid`);
  return result;
}

function decodeChannel(value: unknown, field: string): RetrievalChannel {
  if (typeof value !== "string" || !CHANNELS.has(value as RetrievalChannel)) {
    throw new TypeError(`${field} is invalid`);
  }
  return value as RetrievalChannel;
}

function decodeChannelNumbers(
  value: unknown,
  field: string,
  positiveIntegers: boolean,
): Partial<Record<RetrievalChannel, number>> {
  const source = object(value, field);
  const result: Partial<Record<RetrievalChannel, number>> = {};
  for (const [key, raw] of Object.entries(source)) {
    const channel = decodeChannel(key, `${field} channel`);
    result[channel] = positiveIntegers
      ? integer(raw, `${field}.${key}`, 1)
      : finite(raw, `${field}.${key}`);
  }
  return result;
}

function decodeScore(value: unknown, field: string): RetrievalScoreEvidence {
  const source = object(value, field);
  const channelRanks = decodeChannelNumbers(
    source.channel_ranks,
    `${field}.channel_ranks`,
    true,
  );
  if (Object.keys(channelRanks).length < 1) {
    throw new TypeError(`${field}.channel_ranks must not be empty`);
  }
  const rawScores = decodeChannelNumbers(
    source.channel_raw_scores ?? {},
    `${field}.channel_raw_scores`,
    false,
  );
  if (Object.keys(rawScores).some((channel) => !(channel in channelRanks))) {
    throw new TypeError(`${field}.channel_raw_scores introduced a new channel`);
  }
  const reciprocalRankFusion = finite(
    source.reciprocal_rank_fusion,
    `${field}.reciprocal_rank_fusion`,
  );
  const businessAdjustment = finite(
    source.business_adjustment,
    `${field}.business_adjustment`,
  );
  const finalScore = finite(source.final_score, `${field}.final_score`);
  if (
    reciprocalRankFusion < 0 ||
    businessAdjustment < -1 ||
    businessAdjustment > 1 ||
    Math.abs(finalScore - (reciprocalRankFusion + businessAdjustment)) > 1e-9
  ) {
    throw new TypeError(`${field} is internally inconsistent`);
  }
  const rerankPosition =
    source.rerank_position === null || source.rerank_position === undefined
      ? null
      : integer(source.rerank_position, `${field}.rerank_position`, 1);
  return {
    channel_ranks: channelRanks,
    channel_raw_scores: rawScores,
    reciprocal_rank_fusion: reciprocalRankFusion,
    business_adjustment: businessAdjustment,
    final_score: finalScore,
    rerank_position: rerankPosition,
  };
}

function decodeCitation(
  value: unknown,
  index: number,
  policyVersion: string,
): RetrievalCitation {
  const field = `citations[${index}]`;
  const source = object(value, field);
  const channelsValue = source.channels;
  if (!Array.isArray(channelsValue) || channelsValue.length < 1 || channelsValue.length > 5) {
    throw new TypeError(`${field}.channels must be a non-empty bounded list`);
  }
  const channels = channelsValue.map((item, channelIndex) =>
    decodeChannel(item, `${field}.channels[${channelIndex}]`),
  );
  if (new Set(channels).size !== channels.length) {
    throw new TypeError(`${field}.channels must be unique`);
  }
  const citationPolicy = token(
    source.retrieval_policy_version,
    `${field}.retrieval_policy_version`,
  );
  if (citationPolicy !== policyVersion) {
    throw new TypeError(`${field} crosses retrieval policy versions`);
  }
  const score = decodeScore(source.score, `${field}.score`);
  if (
    Object.keys(score.channel_ranks).length !== channels.length ||
    channels.some((channel) => !(channel in score.channel_ranks))
  ) {
    throw new TypeError(`${field}.channels and score ranks are inconsistent`);
  }
  const brandProfileVersion =
    source.brand_profile_version === null || source.brand_profile_version === undefined
      ? null
      : integer(source.brand_profile_version, `${field}.brand_profile_version`, 1);
  if (channels.includes("BRAND_PROFILE") !== (brandProfileVersion !== null)) {
    throw new TypeError(`${field} has inconsistent Brand Profile evidence`);
  }
  const previewToken = source.preview_reference_token;
  if (
    previewToken !== null &&
    previewToken !== undefined &&
    (typeof previewToken !== "string" || !PREVIEW_TOKEN_PATTERN.test(previewToken))
  ) {
    throw new TypeError(`${field}.preview_reference_token is invalid`);
  }
  return {
    asset_id: uuid(source.asset_id, `${field}.asset_id`),
    asset_version_id: uuid(source.asset_version_id, `${field}.asset_version_id`),
    rights_record_id: uuid(source.rights_record_id, `${field}.rights_record_id`),
    rights_record_version: integer(
      source.rights_record_version,
      `${field}.rights_record_version`,
      1,
    ),
    retrieval_policy_version: citationPolicy,
    brand_profile_version: brandProfileVersion,
    channels: channels as [RetrievalChannel, ...RetrievalChannel[]],
    score,
    rank: integer(source.rank, `${field}.rank`, 1, 50),
    reason: text(source.reason, `${field}.reason`, 512),
    decided_at: utcTimestamp(source.decided_at, `${field}.decided_at`),
    preview_reference_token: previewToken ?? null,
  };
}

function decodeDegradation(value: unknown, index: number): RetrievalDegradationV1 {
  const field = `degradations[${index}]`;
  const source = object(value, field);
  return {
    component: token(source.component, `${field}.component`),
    code: token(source.code, `${field}.code`),
    message: text(source.message, `${field}.message`, 512),
  };
}

export function decodeRetrievalResponse(
  value: unknown,
  { expectedPolicyVersion, expectedRunId }: {
    expectedPolicyVersion?: string;
    expectedRunId?: string;
  } = {},
): RetrievalResponse {
  const source = object(value, "retrieval response");
  const policyVersion = token(
    source.retrieval_policy_version,
    "retrieval_policy_version",
  );
  if (expectedPolicyVersion !== undefined && policyVersion !== expectedPolicyVersion) {
    throw new TypeError("retrieval response policy version mismatch");
  }
  const runId =
    source.retrieval_run_id === null || source.retrieval_run_id === undefined
      ? null
      : uuid(source.retrieval_run_id, "retrieval_run_id");
  if (expectedRunId !== undefined && runId !== expectedRunId) {
    throw new TypeError("retrieval response run identity mismatch");
  }
  if (typeof source.complete_hybrid !== "boolean") {
    throw new TypeError("complete_hybrid must be boolean");
  }
  if (!Array.isArray(source.degradations) || source.degradations.length > 16) {
    throw new TypeError("degradations must be a bounded list");
  }
  if (!Array.isArray(source.citations) || source.citations.length > 50) {
    throw new TypeError("citations must be a bounded list");
  }
  const eligibleCount = integer(
    source.eligible_asset_version_count,
    "eligible_asset_version_count",
    0,
    2_147_483_647,
  );
  const fusedCount = integer(
    source.fused_candidate_count,
    "fused_candidate_count",
    0,
    1_000,
  );
  const finalAuthorizedCount = integer(
    source.final_authorized_candidate_count,
    "final_authorized_candidate_count",
    0,
    1_000,
  );
  const latencyMs = integer(source.latency_ms, "latency_ms", 0, 3_600_000);
  const citations = source.citations.map((citation, index) =>
    decodeCitation(citation, index, policyVersion),
  );
  if (citations.some((citation, index) => citation.rank !== index + 1)) {
    throw new TypeError("citation ranks must be contiguous and ordered");
  }
  if (
    new Set(citations.map((citation) => citation.asset_version_id)).size !==
    citations.length
  ) {
    throw new TypeError("citations must contain unique Asset Versions");
  }
  if (
    !(
      citations.length <= finalAuthorizedCount &&
      finalAuthorizedCount <= fusedCount &&
      fusedCount <= eligibleCount
    )
  ) {
    throw new TypeError("retrieval candidate counts are inconsistent");
  }
  if (source.complete_hybrid && source.degradations.length > 0) {
    throw new TypeError("complete hybrid retrieval cannot contain degradations");
  }
  return {
    retrieval_run_id: runId,
    retrieval_policy_version: policyVersion,
    complete_hybrid: source.complete_hybrid,
    degradations: source.degradations.map(decodeDegradation),
    eligible_asset_version_count: eligibleCount,
    fused_candidate_count: fusedCount,
    final_authorized_candidate_count: finalAuthorizedCount,
    latency_ms: latencyMs,
    citations,
  };
}

export function decodeTemporaryReference(value: unknown): RetrievalTemporaryReference {
  const source = object(value, "temporary reference");
  if (source.method !== "GET") throw new TypeError("temporary reference method is invalid");
  const rawHeaders = object(source.required_headers ?? {}, "required_headers");
  if (Object.keys(rawHeaders).length > 32) {
    throw new TypeError("required_headers must be bounded");
  }
  const requiredHeaders = Object.fromEntries(
    Object.entries(rawHeaders).map(([key, value]) => [
      text(key, "required header name", 128),
      text(value, `required_headers.${key}`, 1024),
    ]),
  );
  const urlValue = text(source.url, "temporary reference URL", 8192);
  const parsedUrl = new URL(urlValue);
  if (
    !["http:", "https:"].includes(parsedUrl.protocol) ||
    parsedUrl.username !== "" ||
    parsedUrl.password !== ""
  ) {
    throw new TypeError("temporary reference URL is invalid");
  }
  return {
    method: "GET",
    url: urlValue,
    required_headers: requiredHeaders,
    expires_at: utcTimestamp(source.expires_at, "temporary reference expiry"),
  };
}

function decodeError(value: unknown): ErrorResponse | undefined {
  try {
    const source = object(value, "error response");
    if (typeof source.retryable !== "boolean") return undefined;
    return {
      code: text(source.code, "error code", 128),
      message: text(source.message, "error message", 2048),
      category: text(source.category, "error category", 128),
      retryable: source.retryable,
      details: object(source.details ?? {}, "error details"),
      request_id: text(source.request_id, "request id", 128),
      trace_id: text(source.trace_id, "trace id", 128),
    };
  } catch {
    return undefined;
  }
}

export class RetrievalApi {
  private readonly baseUrl: string;
  private readonly workspaceId: string;
  private readonly requesterId: string;
  private readonly requestTimeoutMs: number;

  constructor({
    baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "",
    workspaceId = "catalog-demo",
    requesterId = "catalog-workbench",
    requestTimeoutMs = 15_000,
  }: {
    baseUrl?: string;
    workspaceId?: string;
    requesterId?: string;
    requestTimeoutMs?: number;
  } = {}) {
    if (!Number.isSafeInteger(requestTimeoutMs) || requestTimeoutMs < 1) {
      throw new TypeError("requestTimeoutMs must be a positive safe integer");
    }
    this.baseUrl = baseUrl;
    this.workspaceId = workspaceId;
    this.requesterId = requesterId;
    this.requestTimeoutMs = requestTimeoutMs;
  }

  private async request<T>(
    path: string,
    init: RequestInit,
    decode: (value: unknown) => T,
    signal?: AbortSignal,
  ): Promise<T> {
    if (signal?.aborted) throw signal.reason;
    const controller = new AbortController();
    const relayAbort = () => controller.abort(signal?.reason);
    signal?.addEventListener("abort", relayAbort, { once: true });
    const timeout = setTimeout(
      () => controller.abort(new DOMException("request timed out", "TimeoutError")),
      this.requestTimeoutMs,
    );
    try {
      const headers = new Headers(init.headers);
      headers.set("Accept", "application/json");
      headers.set("X-Workspace-Id", this.workspaceId);
      headers.set("X-Actor-Id", this.requesterId);
      if (init.body) headers.set("Content-Type", "application/json");
      const response = await fetch(`${this.baseUrl}${path}`, {
        ...init,
        headers,
        cache: "no-store",
        signal: controller.signal,
      });
      const body = await response.json().catch(() => undefined);
      if (!response.ok) throw new RetrievalApiError(response.status, decodeError(body));
      try {
        return decode(body);
      } catch {
        throw new RetrievalApiError(502);
      }
    } catch (error) {
      if (error instanceof RetrievalApiError) throw error;
      if (signal?.aborted) throw signal.reason;
      throw new RetrievalApiError(controller.signal.aborted ? 504 : 503);
    } finally {
      clearTimeout(timeout);
      signal?.removeEventListener("abort", relayAbort);
    }
  }

  execute(payload: RetrievalQueryV1, signal?: AbortSignal): Promise<RetrievalResponse> {
    if (
      payload.workspace_id !== this.workspaceId ||
      payload.requester_id !== this.requesterId
    ) {
      throw new TypeError("retrieval query identity does not match the API boundary");
    }
    return this.request(
      "/api/v1/retrieval-runs",
      { method: "POST", body: JSON.stringify(payload) },
      (value) =>
        decodeRetrievalResponse(value, {
          expectedPolicyVersion: payload.retrieval_policy_version,
        }),
      signal,
    );
  }

  get(runId: string, signal?: AbortSignal): Promise<RetrievalResponse> {
    uuid(runId, "run ID");
    return this.request(
      `/api/v1/retrieval-runs/${encodeURIComponent(runId)}`,
      { method: "GET" },
      (value) => decodeRetrievalResponse(value, { expectedRunId: runId }),
      signal,
    );
  }

  preview(
    runId: string,
    rank: number,
    previewReferenceToken: string,
    signal?: AbortSignal,
  ): Promise<RetrievalTemporaryReference> {
    uuid(runId, "run ID");
    integer(rank, "rank", 1, 50);
    if (!PREVIEW_TOKEN_PATTERN.test(previewReferenceToken)) {
      throw new TypeError("preview reference token is invalid");
    }
    return this.request(
      `/api/v1/retrieval-runs/${encodeURIComponent(runId)}/results/${rank}:preview`,
      {
        method: "POST",
        body: JSON.stringify({
          preview_reference_token: previewReferenceToken,
        }),
      },
      decodeTemporaryReference,
      signal,
    );
  }
}
