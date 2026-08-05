import type {
  ApprovalRequest,
  CreativePlanCurrentResponseV1,
  CreativePlanRevisionRequestV1,
  CreativePlanVersionListResponseV1,
  CreativePlanVersionResponseV1,
  ErrorResponse,
  WorkflowResponse,
} from "./generated/catalog-api";
import {
  CreativePlanProtocolError,
  decodeCreativePlanCurrentResponse,
  decodeCreativePlanVersionListResponse,
  decodeCreativePlanVersionResponse,
  decodeErrorResponse,
  decodeWorkflowResponse,
} from "./creative-plan-api-decoders";

export type CreativePlanVersionPageOptions = {
  limit?: number;
  cursor?: string;
};

export function newCreativePlanIdempotencyKey(action: string): string {
  if (!/^[a-z][a-z0-9-]{0,31}$/.test(action)) {
    throw new TypeError("Creative Plan action is invalid");
  }
  if (typeof globalThis.crypto?.randomUUID !== "function") {
    throw new Error("Secure browser randomness is unavailable");
  }
  return `web-creative-plan-${action}-${globalThis.crypto.randomUUID()}`;
}

export class CreativePlanApiError extends Error {
  readonly status: number;
  readonly envelope?: ErrorResponse;

  constructor(status: number, envelope?: ErrorResponse) {
    super(envelope?.message ?? `Creative Plan request failed with ${status}`);
    this.name = "CreativePlanApiError";
    this.status = status;
    this.envelope = envelope;
  }
}

export class CreativePlanApiCancelledError extends Error {
  constructor() {
    super("Creative Plan request was cancelled");
    this.name = "CreativePlanApiCancelledError";
  }
}

async function readResponseJson(response: Response, signal: AbortSignal): Promise<unknown> {
  if (signal.aborted) {
    throw signal.reason ?? new DOMException("request was aborted", "AbortError");
  }
  let abort: (() => void) | undefined;
  const aborted = new Promise<never>((_resolve, reject) => {
    abort = () =>
      reject(signal.reason ?? new DOMException("request was aborted", "AbortError"));
    signal.addEventListener("abort", abort, { once: true });
  });
  try {
    return await Promise.race([response.json(), aborted]);
  } finally {
    if (abort) signal.removeEventListener("abort", abort);
  }
}

export class CreativePlanApi {
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
    {
      init = {},
      idempotencyKey,
      expectedStatus,
      decode,
      signal: externalSignal,
    }: {
      init?: RequestInit;
      idempotencyKey?: string;
      expectedStatus?: number;
      decode: (value: unknown) => T;
      signal?: AbortSignal;
    },
  ): Promise<T> {
    if (externalSignal?.aborted) throw new CreativePlanApiCancelledError();
    const controller = new AbortController();
    const cancel = () => controller.abort(externalSignal?.reason);
    externalSignal?.addEventListener("abort", cancel, { once: true });
    const timeout = setTimeout(
      () => controller.abort(new DOMException("request timed out", "TimeoutError")),
      this.requestTimeoutMs,
    );
    try {
      const headers = new Headers(init.headers);
      headers.set("Accept", "application/json");
      headers.set("X-Workspace-Id", this.workspaceId);
      if (init.body) headers.set("Content-Type", "application/json");
      if (idempotencyKey) {
        headers.set("Idempotency-Key", idempotencyKey);
        headers.set("X-Actor-Id", this.actorId);
      }
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
          if (externalSignal?.aborted) throw new CreativePlanApiCancelledError();
          if (controller.signal.aborted) throw new CreativePlanApiError(504);
        }
        throw new CreativePlanApiError(response.status, envelope);
      }
      if (expectedStatus !== undefined && response.status !== expectedStatus) {
        void response.body?.cancel().catch(() => undefined);
        throw new CreativePlanApiError(502);
      }
      let body: unknown;
      try {
        body = await readResponseJson(response, controller.signal);
      } catch {
        if (externalSignal?.aborted) throw new CreativePlanApiCancelledError();
        throw new CreativePlanApiError(controller.signal.aborted ? 504 : 502);
      }
      try {
        return decode(body);
      } catch (error) {
        if (error instanceof CreativePlanProtocolError) {
          throw new CreativePlanApiError(502);
        }
        throw error;
      }
    } catch (error) {
      if (
        error instanceof CreativePlanApiError ||
        error instanceof CreativePlanApiCancelledError
      ) {
        throw error;
      }
      if (externalSignal?.aborted) throw new CreativePlanApiCancelledError();
      throw new CreativePlanApiError(controller.signal.aborted ? 504 : 503);
    } finally {
      clearTimeout(timeout);
      externalSignal?.removeEventListener("abort", cancel);
    }
  }

  getCurrent(
    creativePlanId: string,
    workflowId: string,
    signal?: AbortSignal,
  ): Promise<CreativePlanCurrentResponseV1> {
    const query = new URLSearchParams({ workflow_id: workflowId });
    return this.request(
      `/api/v1/creative-plans/${encodeURIComponent(creativePlanId)}?${query.toString()}`,
      {
        decode: (value) =>
          decodeCreativePlanCurrentResponse(value, {
            workspaceId: this.workspaceId,
            workflowId,
            creativePlanId,
          }),
        expectedStatus: 200,
        signal,
      },
    );
  }

  listVersions(
    creativePlanId: string,
    workflowId: string,
    page: CreativePlanVersionPageOptions = {},
    signal?: AbortSignal,
  ): Promise<CreativePlanVersionListResponseV1> {
    if (
      page.limit !== undefined &&
      (!Number.isSafeInteger(page.limit) || page.limit < 1 || page.limit > 100)
    ) {
      throw new TypeError("Creative Plan page limit must be between 1 and 100");
    }
    if (
      page.cursor !== undefined &&
      (page.cursor.length < 1 || page.cursor.length > 256 || /[\u0000-\u001f\u007f]/.test(page.cursor))
    ) {
      throw new TypeError("Creative Plan page cursor is invalid");
    }
    const query = new URLSearchParams({ workflow_id: workflowId });
    if (page.limit !== undefined) query.set("limit", String(page.limit));
    if (page.cursor !== undefined) query.set("cursor", page.cursor);
    return this.request(
      `/api/v1/creative-plans/${encodeURIComponent(creativePlanId)}/versions?${query.toString()}`,
      {
        decode: (value) =>
          decodeCreativePlanVersionListResponse(value, {
            workspaceId: this.workspaceId,
            workflowId,
            creativePlanId,
          }),
        expectedStatus: 200,
        signal,
      },
    );
  }

  getVersion(
    creativePlanId: string,
    workflowId: string,
    versionNumber: number,
    signal?: AbortSignal,
  ): Promise<CreativePlanVersionResponseV1> {
    if (!Number.isSafeInteger(versionNumber) || versionNumber < 1) {
      throw new TypeError("Creative Plan version number is invalid");
    }
    const query = new URLSearchParams({ workflow_id: workflowId });
    return this.request(
      `/api/v1/creative-plans/${encodeURIComponent(creativePlanId)}/versions/${versionNumber}?${query.toString()}`,
      {
        decode: (value) =>
          decodeCreativePlanVersionResponse(value, {
            workspaceId: this.workspaceId,
            workflowId,
            creativePlanId,
          }),
        expectedStatus: 200,
        signal,
      },
    );
  }

  revise(
    creativePlanId: string,
    payload: CreativePlanRevisionRequestV1,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<CreativePlanCurrentResponseV1> {
    return this.request(
      `/api/v1/creative-plans/${encodeURIComponent(creativePlanId)}:revise`,
      {
        init: { method: "POST", body: JSON.stringify(payload) },
        idempotencyKey,
        expectedStatus: 201,
        decode: (value) =>
          decodeCreativePlanCurrentResponse(value, {
            workspaceId: this.workspaceId,
            workflowId: payload.workflow_id,
            creativePlanId,
          }),
        signal,
      },
    );
  }

  approve(
    workflowId: string,
    payload: ApprovalRequest,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<WorkflowResponse> {
    return this.decide(
      "approve",
      workflowId,
      payload,
      idempotencyKey,
      signal,
    );
  }

  reject(
    workflowId: string,
    payload: ApprovalRequest,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<WorkflowResponse> {
    return this.decide(
      "reject",
      workflowId,
      payload,
      idempotencyKey,
      signal,
    );
  }

  private decide(
    action: "approve" | "reject",
    workflowId: string,
    payload: ApprovalRequest,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<WorkflowResponse> {
    return this.request(
      `/api/v1/workflows/${encodeURIComponent(workflowId)}/creative-plan:${action}`,
      {
        init: { method: "POST", body: JSON.stringify(payload) },
        idempotencyKey,
        expectedStatus: 200,
        decode: (value) =>
          decodeWorkflowResponse(value, {
            workspaceId: this.workspaceId,
            workflowId,
          }),
        signal,
      },
    );
  }

  getWorkflow(workflowId: string, signal?: AbortSignal): Promise<WorkflowResponse> {
    return this.request(
      `/api/v1/workflows/${encodeURIComponent(workflowId)}`,
      {
        decode: (value) =>
          decodeWorkflowResponse(value, {
            workspaceId: this.workspaceId,
            workflowId,
          }),
        expectedStatus: 200,
        signal,
      },
    );
  }
}
