import type {
  BrandProfileCreateRequestV1,
  BrandProfileListResponseV1,
  BrandProfilePublishRequestV1,
  BrandProfileResponseV1,
  BrandProfileUpdateDraftRequestV1,
  BrandProfileValidateRequestV1,
  BrandProfileValidationResponseV1,
  BrandProfileVersionListResponseV1,
  BrandProfileVersionResponseV1,
  ErrorResponse,
} from "./generated/catalog-api";
import { BRAND_PROFILE_SAFE_PAGE_SIZE } from "./brand-profile-transport-limits";
import {
  BrandProfileProtocolError,
  decodeBrandProfileListResponse,
  decodeBrandProfileResponse,
  decodeBrandProfileValidationResponse,
  decodeBrandProfileVersionListResponse,
  decodeBrandProfileVersionResponse,
  decodeErrorResponse,
  decodeWorkspaceCapabilities,
} from "./brand-profile-api-decoders";

export class BrandProfileApiError extends Error {
  readonly status: number;
  readonly envelope?: ErrorResponse;

  constructor(status: number, envelope?: ErrorResponse) {
    super(envelope?.message ?? `Brand Profile request failed with ${status}`);
    this.name = "BrandProfileApiError";
    this.status = status;
    this.envelope = envelope;
  }
}

export class BrandProfileApiCancelledError extends Error {
  constructor() {
    super("Brand Profile request was cancelled");
    this.name = "BrandProfileApiCancelledError";
  }
}

export type BrandProfilePageOptions = {
  cursor?: string;
  limit?: number;
};

export type BrandProfileListOptions = BrandProfilePageOptions & {
  brand?: string;
};

export type WorkspaceCapabilities = {
  administrator: boolean;
};

export function newBrandProfileIdempotencyKey(action: string): string {
  const random =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `web-brand-profile-${action}-${random}`;
}

async function readResponseJson(
  response: Response,
  signal: AbortSignal,
): Promise<unknown> {
  if (signal.aborted) {
    throw (
      signal.reason ??
      new DOMException("request was aborted", "AbortError")
    );
  }
  let abort: (() => void) | undefined;
  const aborted = new Promise<never>((_resolve, reject) => {
    abort = () =>
      reject(
        signal.reason ??
          new DOMException("request was aborted", "AbortError"),
      );
    signal.addEventListener("abort", abort, { once: true });
  });
  try {
    return await Promise.race([response.json(), aborted]);
  } finally {
    if (abort) signal.removeEventListener("abort", abort);
  }
}

function appendPageOptions(
  query: URLSearchParams,
  page: BrandProfilePageOptions,
): void {
  if (page.cursor !== undefined) query.set("cursor", page.cursor);
  if (page.limit !== undefined) {
    if (
      !Number.isSafeInteger(page.limit) ||
      page.limit < 1 ||
      page.limit > BRAND_PROFILE_SAFE_PAGE_SIZE
    ) {
      throw new TypeError(
        `limit must be an integer between 1 and ${BRAND_PROFILE_SAFE_PAGE_SIZE}`,
      );
    }
    query.set("limit", String(page.limit));
  }
}

export class BrandProfileApi {
  private readonly baseUrl: string;
  private readonly workspaceId: string;
  private readonly actorId: string;
  private readonly requestTimeoutMs: number;

  constructor({
    baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "",
    workspaceId = "catalog-demo",
    actorId = "catalog-workbench",
    requestTimeoutMs = 15_000,
  }: {
    baseUrl?: string;
    workspaceId?: string;
    actorId?: string;
    requestTimeoutMs?: number;
  } = {}) {
    if (!Number.isSafeInteger(requestTimeoutMs) || requestTimeoutMs < 1) {
      throw new TypeError("requestTimeoutMs must be a positive safe integer");
    }
    this.baseUrl = baseUrl;
    this.workspaceId = workspaceId;
    this.actorId = actorId;
    this.requestTimeoutMs = requestTimeoutMs;
  }

  private async request<T>(
    path: string,
    init: RequestInit = {},
    {
      idempotencyKey,
      signal: externalSignal,
      expectedStatus,
      includeActor = false,
      decode,
    }: {
      idempotencyKey?: string;
      signal?: AbortSignal;
      expectedStatus?: number;
      includeActor?: boolean;
      decode: (value: unknown) => T;
    },
  ): Promise<T> {
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    headers.set("X-Workspace-Id", this.workspaceId);
    if (init.body) headers.set("Content-Type", "application/json");
    if (idempotencyKey || includeActor) {
      headers.set("X-Actor-Id", this.actorId);
    }
    if (idempotencyKey) {
      headers.set("Idempotency-Key", idempotencyKey);
    }

    const controller = new AbortController();
    const cancel = () => controller.abort(externalSignal?.reason);
    if (externalSignal?.aborted) {
      throw new BrandProfileApiCancelledError();
    }
    externalSignal?.addEventListener("abort", cancel, { once: true });
    const timeout = setTimeout(
      () =>
        controller.abort(
          new DOMException("request timed out", "TimeoutError"),
        ),
      this.requestTimeoutMs,
    );
    try {
      const response = await fetch(`${this.baseUrl}${path}`, {
        ...init,
        cache: "no-store",
        headers,
        signal: controller.signal,
      });
      if (!response.ok) {
        let envelope: ErrorResponse | undefined;
        try {
          envelope = decodeErrorResponse(
            await readResponseJson(response, controller.signal),
          );
        } catch {
          if (externalSignal?.aborted) {
            throw new BrandProfileApiCancelledError();
          }
          if (controller.signal.aborted) {
            throw new BrandProfileApiError(504);
          }
        }
        throw new BrandProfileApiError(response.status, envelope);
      }
      if (
        expectedStatus !== undefined &&
        response.status !== expectedStatus
      ) {
        void response.body?.cancel().catch(() => undefined);
        throw new BrandProfileApiError(502);
      }
      let body: unknown;
      try {
        body = await readResponseJson(response, controller.signal);
      } catch {
        if (externalSignal?.aborted) {
          throw new BrandProfileApiCancelledError();
        }
        throw new BrandProfileApiError(
          controller.signal.aborted ? 504 : 502,
        );
      }
      try {
        return decode(body);
      } catch (error) {
        if (error instanceof BrandProfileProtocolError) {
          throw new BrandProfileApiError(502);
        }
        throw error;
      }
    } catch (error) {
      if (
        error instanceof BrandProfileApiError ||
        error instanceof BrandProfileApiCancelledError
      ) {
        throw error;
      }
      if (externalSignal?.aborted) {
        throw new BrandProfileApiCancelledError();
      }
      throw new BrandProfileApiError(
        controller.signal.aborted ? 504 : 503,
      );
    } finally {
      clearTimeout(timeout);
      externalSignal?.removeEventListener("abort", cancel);
    }
  }

  list(
    options: BrandProfileListOptions = {},
    signal?: AbortSignal,
  ): Promise<BrandProfileListResponseV1> {
    const query = new URLSearchParams();
    if (options.brand !== undefined) query.set("brand", options.brand);
    appendPageOptions(query, options);
    const suffix = query.size > 0 ? `?${query.toString()}` : "";
    return this.request<BrandProfileListResponseV1>(
      `/api/v1/brand-profiles${suffix}`,
      {},
      {
        signal,
        decode: (value) =>
          decodeBrandProfileListResponse(value, {
            workspaceId: this.workspaceId,
            brand: options.brand,
            limit: options.limit ?? BRAND_PROFILE_SAFE_PAGE_SIZE,
          }),
      },
    );
  }

  getWorkspaceCapabilities(
    signal?: AbortSignal,
  ): Promise<WorkspaceCapabilities> {
    return this.request<WorkspaceCapabilities>(
      "/api/web-capabilities",
      {},
      { signal, decode: decodeWorkspaceCapabilities },
    );
  }

  create(
    payload: BrandProfileCreateRequestV1,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<BrandProfileResponseV1> {
    return this.request<BrandProfileResponseV1>(
      "/api/v1/brand-profiles",
      { method: "POST", body: JSON.stringify(payload) },
      {
        idempotencyKey,
        signal,
        expectedStatus: 201,
        decode: (value) =>
          decodeBrandProfileResponse(value, {
            workspaceId: this.workspaceId,
            brand: payload.brand,
            profileKey: payload.profile_key,
          }),
      },
    );
  }

  get(
    profileId: string,
    signal?: AbortSignal,
  ): Promise<BrandProfileResponseV1> {
    return this.request<BrandProfileResponseV1>(
      `/api/v1/brand-profiles/${encodeURIComponent(profileId)}`,
      {},
      {
        signal,
        decode: (value) =>
          decodeBrandProfileResponse(value, {
            workspaceId: this.workspaceId,
            profileId,
          }),
      },
    );
  }

  updateDraft(
    profileId: string,
    payload: BrandProfileUpdateDraftRequestV1,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<BrandProfileResponseV1> {
    return this.request<BrandProfileResponseV1>(
      `/api/v1/brand-profiles/${encodeURIComponent(profileId)}/draft`,
      { method: "PUT", body: JSON.stringify(payload) },
      {
        idempotencyKey,
        signal,
        decode: (value) =>
          decodeBrandProfileResponse(value, {
            workspaceId: this.workspaceId,
            profileId,
          }),
      },
    );
  }

  validate(
    profileId: string,
    payload: BrandProfileValidateRequestV1,
    signal?: AbortSignal,
  ): Promise<BrandProfileValidationResponseV1> {
    return this.request<BrandProfileValidationResponseV1>(
      `/api/v1/brand-profiles/${encodeURIComponent(profileId)}:validate`,
      { method: "POST", body: JSON.stringify(payload) },
      {
        signal,
        includeActor: true,
        decode: (value) =>
          decodeBrandProfileValidationResponse(value, {
            profileId,
            profileVersion: payload.expected_version,
          }),
      },
    );
  }

  publish(
    profileId: string,
    payload: BrandProfilePublishRequestV1,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<BrandProfileResponseV1> {
    return this.request<BrandProfileResponseV1>(
      `/api/v1/brand-profiles/${encodeURIComponent(profileId)}:publish`,
      { method: "POST", body: JSON.stringify(payload) },
      {
        idempotencyKey,
        signal,
        expectedStatus: 201,
        decode: (value) =>
          decodeBrandProfileResponse(value, {
            workspaceId: this.workspaceId,
            profileId,
          }),
      },
    );
  }

  listVersions(
    profileId: string,
    page: BrandProfilePageOptions = {},
    signal?: AbortSignal,
  ): Promise<BrandProfileVersionListResponseV1> {
    const query = new URLSearchParams();
    appendPageOptions(query, page);
    const suffix = query.size > 0 ? `?${query.toString()}` : "";
    return this.request<BrandProfileVersionListResponseV1>(
      `/api/v1/brand-profiles/${encodeURIComponent(profileId)}/versions${suffix}`,
      {},
      {
        signal,
        decode: (value) =>
          decodeBrandProfileVersionListResponse(value, {
            workspaceId: this.workspaceId,
            profileId,
            limit: page.limit ?? BRAND_PROFILE_SAFE_PAGE_SIZE,
          }),
      },
    );
  }

  getVersion(
    profileId: string,
    versionNumber: number,
    signal?: AbortSignal,
  ): Promise<BrandProfileVersionResponseV1> {
    if (!Number.isSafeInteger(versionNumber) || versionNumber < 1) {
      throw new TypeError("versionNumber must be a positive safe integer");
    }
    return this.request<BrandProfileVersionResponseV1>(
      `/api/v1/brand-profiles/${encodeURIComponent(profileId)}/versions/${versionNumber}`,
      {},
      {
        signal,
        decode: (value) =>
          decodeBrandProfileVersionResponse(value, {
            workspaceId: this.workspaceId,
            profileId,
            versionNumber,
          }),
      },
    );
  }
}
