import type {
  AssetResponseV1,
  AssetValidationStatusResponseV1,
  ErrorResponse,
  OperationResponseV1,
  PresignedUploadV1,
  UploadFinalizeResponseV1,
  UploadSessionCreateRequestV1,
  UploadSessionCreateResponseV1,
  UploadSessionResponseV1,
} from "./generated/catalog-api";

export type DurableOperationResponseV1 = OperationResponseV1;

export class AssetApiError extends Error {
  readonly status: number;
  readonly envelope?: ErrorResponse;

  constructor(status: number, envelope?: ErrorResponse) {
    super(envelope?.message ?? `Asset request failed with ${status}`);
    this.name = "AssetApiError";
    this.status = status;
    this.envelope = envelope;
  }
}

export function newUploadIdempotencyKey(action: string): string {
  const random =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `web-upload-${action}-${random}`;
}

export async function sha256Hex(file: File): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return Array.from(new Uint8Array(digest), (value) =>
    value.toString(16).padStart(2, "0"),
  ).join("");
}

export class AssetApi {
  private readonly baseUrl: string;
  private readonly workspaceId: string;
  private readonly actorId: string;
  private readonly requestTimeoutMs: number;
  private readonly uploadTimeoutMs: number;

  constructor({
    baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "",
    workspaceId = "catalog-demo",
    actorId = "catalog-workbench",
    requestTimeoutMs = 15_000,
    uploadTimeoutMs = 120_000,
  }: {
    baseUrl?: string;
    workspaceId?: string;
    actorId?: string;
    requestTimeoutMs?: number;
    uploadTimeoutMs?: number;
  } = {}) {
    if (!Number.isSafeInteger(requestTimeoutMs) || requestTimeoutMs < 1) {
      throw new TypeError("requestTimeoutMs must be a positive safe integer");
    }
    if (!Number.isSafeInteger(uploadTimeoutMs) || uploadTimeoutMs < 1) {
      throw new TypeError("uploadTimeoutMs must be a positive safe integer");
    }
    this.baseUrl = baseUrl;
    this.workspaceId = workspaceId;
    this.actorId = actorId;
    this.requestTimeoutMs = requestTimeoutMs;
    this.uploadTimeoutMs = uploadTimeoutMs;
  }

  private async request<T>(
    path: string,
    init: RequestInit = {},
    idempotencyKey?: string,
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
    const timeout = setTimeout(
      () => controller.abort(new DOMException("request timed out", "TimeoutError")),
      this.requestTimeoutMs,
    );
    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}${path}`, {
        ...init,
        headers,
        cache: "no-store",
        signal: controller.signal,
      });
    } catch {
      if (controller.signal.aborted) {
        throw new AssetApiError(504);
      }
      throw new AssetApiError(503);
    } finally {
      clearTimeout(timeout);
    }
    if (!response.ok) {
      let envelope: ErrorResponse | undefined;
      try {
        envelope = (await response.json()) as ErrorResponse;
      } catch {
        envelope = undefined;
      }
      throw new AssetApiError(response.status, envelope);
    }
    return (await response.json()) as T;
  }

  createUploadSession(
    payload: UploadSessionCreateRequestV1,
    idempotencyKey: string,
  ): Promise<UploadSessionCreateResponseV1> {
    return this.request<UploadSessionCreateResponseV1>(
      "/api/v1/upload-sessions",
      { method: "POST", body: JSON.stringify(payload) },
      idempotencyKey,
    );
  }

  getUploadSession(uploadSessionId: string): Promise<UploadSessionResponseV1> {
    return this.request<UploadSessionResponseV1>(
      `/api/v1/upload-sessions/${encodeURIComponent(uploadSessionId)}`,
    );
  }

  abortUploadSession(
    uploadSessionId: string,
    expectedVersion: number,
    idempotencyKey: string,
  ): Promise<UploadSessionResponseV1> {
    return this.request<UploadSessionResponseV1>(
      `/api/v1/upload-sessions/${encodeURIComponent(uploadSessionId)}:abort`,
      {
        method: "POST",
        body: JSON.stringify({ expected_version: expectedVersion }),
      },
      idempotencyKey,
    );
  }

  finalizeUploadSession(
    uploadSessionId: string,
    expectedVersion: number,
    idempotencyKey: string,
  ): Promise<UploadFinalizeResponseV1> {
    return this.request<UploadFinalizeResponseV1>(
      `/api/v1/upload-sessions/${encodeURIComponent(uploadSessionId)}:finalize`,
      {
        method: "POST",
        body: JSON.stringify({ expected_version: expectedVersion }),
      },
      idempotencyKey,
    );
  }

  getAsset(assetId: string): Promise<AssetResponseV1> {
    return this.request<AssetResponseV1>(
      `/api/v1/assets/${encodeURIComponent(assetId)}`,
    );
  }

  getAssetValidation(assetId: string): Promise<AssetValidationStatusResponseV1> {
    return this.request<AssetValidationStatusResponseV1>(
      `/api/v1/assets/${encodeURIComponent(assetId)}/validation`,
    );
  }

  getOperation(operationId: string): Promise<DurableOperationResponseV1> {
    return this.request<DurableOperationResponseV1>(
      `/api/v1/operations/${encodeURIComponent(operationId)}`,
    );
  }

  uploadDirect(
    upload: PresignedUploadV1,
    file: File,
    onProgress: (percentage: number) => void,
  ): Promise<void> {
    if (file.size > upload.maximum_bytes) {
      return Promise.reject(new Error("文件大小超过上传会话限制。"));
    }
    return new Promise((resolve, reject) => {
      const request = new XMLHttpRequest();
      request.open("PUT", upload.url, true);
      request.timeout = this.uploadTimeoutMs;
      for (const [name, rawValue] of Object.entries(upload.required_headers)) {
        if (name.toLowerCase() === "content-length") continue;
        if (typeof rawValue !== "string") {
          reject(new Error("上传会话包含无效请求头。"));
          return;
        }
        request.setRequestHeader(name, rawValue);
      }
      request.upload.addEventListener("progress", (event) => {
        const total = event.lengthComputable ? event.total : upload.maximum_bytes;
        onProgress(Math.min(100, Math.round((event.loaded / total) * 100)));
      });
      request.addEventListener("load", () => {
        if (request.status >= 200 && request.status < 300) {
          onProgress(100);
          resolve();
          return;
        }
        reject(new Error(`对象存储上传失败（${request.status}）。`));
      });
      request.addEventListener("error", () => {
        reject(new Error("无法连接对象存储。"));
      });
      request.addEventListener("abort", () => {
        reject(new Error("对象存储上传已取消。"));
      });
      request.addEventListener("timeout", () => {
        reject(new Error("对象存储上传超时。"));
      });
      request.send(file);
    });
  }
}
