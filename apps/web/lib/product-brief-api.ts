import type {
  ErrorResponse,
  ProductBriefAnalysisAcceptedV1,
  ProductBriefAnalysisRequestV1,
  ProductBriefConfirmationRequestV1,
  ProductBriefConfirmationResponseV1,
  ProductBriefOperationStatusResponseV1,
  ProductBriefResponseV1,
  ProductBriefRevisionRequestV1,
  ProductBriefVersionListResponseV1,
  ProductBriefWorkflowContextResponseV1,
} from "./generated/catalog-api";

export class ProductBriefApiError extends Error {
  readonly status: number;
  readonly envelope?: ErrorResponse;

  constructor(status: number, envelope?: ErrorResponse) {
    super(envelope?.message ?? `ProductBrief request failed with ${status}`);
    this.name = "ProductBriefApiError";
    this.status = status;
    this.envelope = envelope;
  }
}

export class ProductBriefApiCancelledError extends Error {
  constructor() {
    super("ProductBrief request was cancelled");
    this.name = "ProductBriefApiCancelledError";
  }
}

export function newProductBriefIdempotencyKey(action: string): string {
  const random =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `web-product-brief-${action}-${random}`;
}

export type ProductBriefVersionPageOptions = {
  limit?: number;
  cursor?: number;
};

async function readResponseJson<T>(
  response: Response,
  signal: AbortSignal,
): Promise<T> {
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
    return await Promise.race([
      response.json() as Promise<T>,
      aborted,
    ]);
  } finally {
    if (abort) signal.removeEventListener("abort", abort);
  }
}

export class ProductBriefApi {
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
    idempotencyKey?: string,
    externalSignal?: AbortSignal,
    expectedStatus?: number,
  ): Promise<T> {
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    headers.set("X-Workspace-Id", this.workspaceId);
    if (init.body) headers.set("Content-Type", "application/json");
    if (idempotencyKey) {
      headers.set("X-Actor-Id", this.actorId);
      headers.set("Idempotency-Key", idempotencyKey);
    }
    const controller = new AbortController();
    const cancel = () => controller.abort(externalSignal?.reason);
    if (externalSignal?.aborted) {
      throw new ProductBriefApiCancelledError();
    }
    externalSignal?.addEventListener("abort", cancel, { once: true });
    const timeout = setTimeout(
      () => controller.abort(new DOMException("request timed out", "TimeoutError")),
      this.requestTimeoutMs,
    );
    try {
      const response = await fetch(`${this.baseUrl}${path}`, {
        ...init,
        cache: "no-store",
        headers,
        signal: controller.signal,
      });
      if (response.status === 410) {
        void response.body?.cancel().catch(() => undefined);
        throw new ProductBriefApiError(410);
      }
      if (!response.ok) {
        let envelope: ErrorResponse | undefined;
        try {
          envelope = await readResponseJson<ErrorResponse>(
            response,
            controller.signal,
          );
        } catch {
          if (externalSignal?.aborted) {
            throw new ProductBriefApiCancelledError();
          }
          if (controller.signal.aborted) {
            throw new ProductBriefApiError(504);
          }
          envelope = undefined;
        }
        throw new ProductBriefApiError(response.status, envelope);
      }
      if (
        expectedStatus !== undefined &&
        response.status !== expectedStatus
      ) {
        void response.body?.cancel().catch(() => undefined);
        throw new ProductBriefApiError(502);
      }
      return await readResponseJson<T>(response, controller.signal);
    } catch (error) {
      if (
        error instanceof ProductBriefApiError ||
        error instanceof ProductBriefApiCancelledError
      ) {
        throw error;
      }
      if (externalSignal?.aborted) {
        throw new ProductBriefApiCancelledError();
      }
      throw new ProductBriefApiError(controller.signal.aborted ? 504 : 503);
    } finally {
      clearTimeout(timeout);
      externalSignal?.removeEventListener("abort", cancel);
    }
  }

  requestAnalysis(
    payload: ProductBriefAnalysisRequestV1,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<ProductBriefAnalysisAcceptedV1> {
    return this.request<ProductBriefAnalysisAcceptedV1>(
      "/api/v1/product-briefs:analyze",
      { method: "POST", body: JSON.stringify(payload) },
      idempotencyKey,
      signal,
      202,
    );
  }

  get(
    productBriefId: string,
    signal?: AbortSignal,
  ): Promise<ProductBriefResponseV1> {
    return this.request<ProductBriefResponseV1>(
      `/api/v1/product-briefs/${encodeURIComponent(productBriefId)}`,
      { signal },
      undefined,
      signal,
    );
  }

  listVersions(
    productBriefId: string,
    page: ProductBriefVersionPageOptions = {},
    signal?: AbortSignal,
  ): Promise<ProductBriefVersionListResponseV1> {
    const query = new URLSearchParams();
    if (page.limit !== undefined) query.set("limit", String(page.limit));
    if (page.cursor !== undefined) query.set("cursor", String(page.cursor));
    const suffix = query.size > 0 ? `?${query.toString()}` : "";
    return this.request<ProductBriefVersionListResponseV1>(
      `/api/v1/product-briefs/${encodeURIComponent(productBriefId)}/versions${suffix}`,
      { signal },
      undefined,
      signal,
    );
  }

  revise(
    productBriefId: string,
    payload: ProductBriefRevisionRequestV1,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<ProductBriefResponseV1> {
    return this.request<ProductBriefResponseV1>(
      `/api/v1/product-briefs/${encodeURIComponent(productBriefId)}:revise`,
      { method: "POST", body: JSON.stringify(payload) },
      idempotencyKey,
      signal,
    );
  }

  confirm(
    productBriefId: string,
    payload: ProductBriefConfirmationRequestV1,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<ProductBriefConfirmationResponseV1> {
    return this.request<ProductBriefConfirmationResponseV1>(
      `/api/v1/product-briefs/${encodeURIComponent(productBriefId)}:confirm`,
      { method: "POST", body: JSON.stringify(payload) },
      idempotencyKey,
      signal,
    );
  }

  getWorkflowContext(
    workflowId: string,
    productBriefId: string,
    signal?: AbortSignal,
  ): Promise<ProductBriefWorkflowContextResponseV1> {
    const query = new URLSearchParams({
      product_brief_id: productBriefId,
    });
    return this.request<ProductBriefWorkflowContextResponseV1>(
      `/api/v1/product-briefs/workflow-context/${encodeURIComponent(workflowId)}?${query.toString()}`,
      { signal },
      undefined,
      signal,
    );
  }

  getAnalysisWorkflowContext(
    workflowId: string,
    signal?: AbortSignal,
  ): Promise<ProductBriefWorkflowContextResponseV1> {
    return this.request<ProductBriefWorkflowContextResponseV1>(
      `/api/v1/product-briefs/analysis-workflow-context/${encodeURIComponent(workflowId)}`,
      { signal },
      undefined,
      signal,
    );
  }

  getOperation(
    productBriefId: string,
    operationId: string,
    signal?: AbortSignal,
  ): Promise<ProductBriefOperationStatusResponseV1> {
    return this.request<ProductBriefOperationStatusResponseV1>(
      `/api/v1/product-briefs/${encodeURIComponent(productBriefId)}/operations/${encodeURIComponent(operationId)}`,
      { signal },
      undefined,
      signal,
    );
  }
}
