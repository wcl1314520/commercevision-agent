import {
  issueWorkspacePrincipal,
  TrustedPrincipalConfigurationError,
  WorkspaceBoundaryError,
} from "../../../../lib/trusted-principal.ts";

const DEFAULT_API_PROXY_URL = "http://api:8000";
const DEFAULT_API_PROXY_TIMEOUT_MS = 15_000;
const MAXIMUM_API_PROXY_TIMEOUT_MS = 120_000;
const MAXIMUM_PROXY_REQUEST_BODY_BYTES = 1024 * 1024;
const MAXIMUM_PROXY_RESPONSE_BODY_BYTES = 2 * 1024 * 1024;
const FORWARDED_HEADERS = [
  "accept",
  "content-type",
  "idempotency-key",
  "x-request-id",
  "x-trace-id",
  "x-workspace-id",
] as const;

type RouteContext = {
  params: Promise<{ path: string[] }>;
};

class RequestBodyTooLargeError extends Error {}
class UpstreamResponseTooLargeError extends Error {}

function encodeApiPathSegment(segment: string): string {
  const action = /^(.*):(abort|block|check|finalize|replace|revoke)$/.exec(
    segment,
  );
  if (action) {
    return `${encodeURIComponent(action[1])}:${action[2]}`;
  }
  return encodeURIComponent(segment);
}

function proxyOrigin(): URL {
  const configured = process.env.CV_API_PROXY_URL ?? DEFAULT_API_PROXY_URL;
  const origin = new URL(configured);
  if (
    (origin.protocol !== "http:" && origin.protocol !== "https:") ||
    origin.username ||
    origin.password ||
    origin.search ||
    origin.hash
  ) {
    throw new Error("CV_API_PROXY_URL must be an HTTP(S) origin without credentials or query");
  }
  origin.pathname = origin.pathname.replace(/\/+$/, "");
  return origin;
}

function proxyTimeoutMs(): number {
  const configured = process.env.CV_API_PROXY_TIMEOUT_MS;
  if (configured === undefined) return DEFAULT_API_PROXY_TIMEOUT_MS;
  if (!/^[1-9][0-9]*$/.test(configured)) {
    throw new Error("CV_API_PROXY_TIMEOUT_MS must be a positive integer");
  }
  const timeout = Number(configured);
  if (!Number.isSafeInteger(timeout) || timeout > MAXIMUM_API_PROXY_TIMEOUT_MS) {
    throw new Error("CV_API_PROXY_TIMEOUT_MS is outside the supported range");
  }
  return timeout;
}

async function readBoundedStream(
  stream: ReadableStream<Uint8Array>,
  maximumBytes: number,
  tooLarge: () => Error,
): Promise<Uint8Array> {
  const reader = stream.getReader();
  const chunks: Uint8Array[] = [];
  let totalBytes = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    totalBytes += value.byteLength;
    if (totalBytes > maximumBytes) {
      await reader.cancel();
      throw tooLarge();
    }
    chunks.push(value);
  }
  const body = new Uint8Array(totalBytes);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return body;
}

async function readBoundedRequestBody(
  request: Request,
): Promise<Uint8Array | undefined> {
  if (request.body === null) return undefined;
  const declaredLength = request.headers.get("content-length");
  if (declaredLength !== null) {
    if (!/^(0|[1-9][0-9]*)$/.test(declaredLength)) {
      throw new RequestBodyTooLargeError("request Content-Length is invalid");
    }
    if (Number(declaredLength) > MAXIMUM_PROXY_REQUEST_BODY_BYTES) {
      throw new RequestBodyTooLargeError("request body exceeds the proxy limit");
    }
  }

  return readBoundedStream(
    request.body,
    MAXIMUM_PROXY_REQUEST_BODY_BYTES,
    () => new RequestBodyTooLargeError("request body exceeds the proxy limit"),
  );
}

async function readBoundedUpstreamBody(
  response: Response,
): Promise<Uint8Array | undefined> {
  if (response.body === null) return undefined;
  const declaredLength = response.headers.get("content-length");
  if (
    declaredLength !== null &&
    /^(0|[1-9][0-9]*)$/.test(declaredLength) &&
    Number(declaredLength) > MAXIMUM_PROXY_RESPONSE_BODY_BYTES
  ) {
    await response.body.cancel();
    throw new UpstreamResponseTooLargeError(
      "upstream response exceeds the proxy limit",
    );
  }
  return readBoundedStream(
    response.body,
    MAXIMUM_PROXY_RESPONSE_BODY_BYTES,
    () =>
      new UpstreamResponseTooLargeError(
        "upstream response exceeds the proxy limit",
      ),
  );
}

function apiMethodAllowed(path: string, method: string): boolean {
  if (path === "/products") return method === "GET" || method === "POST";
  if (/^\/products\/[^/]+$/.test(path)) {
    return method === "GET" || method === "PUT" || method === "DELETE";
  }
  if (/^\/products\/[^/]+\/skus$/.test(path)) return method === "POST";
  if (/^\/products\/[^/]+\/skus\/[^/]+$/.test(path)) {
    return method === "PUT" || method === "DELETE";
  }
  if (path === "/upload-sessions") return method === "POST";
  if (/^\/upload-sessions\/[^/:]+$/.test(path)) return method === "GET";
  if (/^\/upload-sessions\/[^/:]+:(abort|finalize)$/.test(path)) {
    return method === "POST";
  }
  if (/^\/assets\/[^/:]+$/.test(path)) return method === "GET";
  if (/^\/assets\/[^/:]+\/validation$/.test(path)) return method === "GET";
  if (/^\/assets\/[^/:]+\/rights$/.test(path)) {
    return method === "GET" || method === "POST";
  }
  if (/^\/assets\/[^/:]+\/rights:(replace|revoke)$/.test(path)) {
    return method === "POST";
  }
  if (/^\/assets\/[^/:]+\/usability:check$/.test(path)) {
    return method === "POST";
  }
  if (/^\/assets\/[^/:]+:block$/.test(path)) return method === "POST";
  if (
    /^\/operations\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
      path,
    )
  ) {
    return method === "GET";
  }
  return false;
}

function errorResponse(
  request: Request,
  status: number,
  code: string,
  message: string,
  category: string,
  retryable: boolean,
): Response {
  const requestId = request.headers.get("x-request-id") ?? crypto.randomUUID();
  const traceId = request.headers.get("x-trace-id") ?? requestId;
  return Response.json(
    {
      code,
      message,
      category,
      retryable,
      details: {},
      request_id: requestId,
      trace_id: traceId,
    },
    {
      status,
      headers: {
        "X-Request-Id": requestId,
        "X-Trace-Id": traceId,
      },
    },
  );
}

function notAllowedResponse(request: Request, path: string, method: string): Response {
  if (!apiMethodAllowed(path, method)) {
    return errorResponse(
      request,
      404,
      "NOT_FOUND",
      "API route was not found",
      "not_found",
      false,
    );
  }
  const response = errorResponse(
    request,
    405,
    "METHOD_NOT_ALLOWED",
    "API method was not allowed",
    "validation",
    false,
  );
  response.headers.set("Allow", "GET, POST, PUT, DELETE");
  return response;
}

async function proxyApi(request: Request, context: RouteContext): Promise<Response> {
  const { path: segments } = await context.params;
  const path = `/${segments.map(encodeApiPathSegment).join("/")}`;
  if (!apiMethodAllowed(path, request.method)) {
    return notAllowedResponse(request, path, request.method);
  }

  let target: URL;
  let timeoutMs: number;
  try {
    target = proxyOrigin();
    timeoutMs = proxyTimeoutMs();
  } catch {
    return errorResponse(
      request,
      500,
      "API_PROXY_MISCONFIGURED",
      "API proxy is misconfigured",
      "configuration",
      false,
    );
  }
  target.pathname = `${target.pathname}/api/v1${path}`.replace(/\/{2,}/g, "/");
  target.search = new URL(request.url).search;

  let principal: { actorId: string; token: string };
  try {
    principal = issueWorkspacePrincipal(request.headers.get("x-workspace-id"));
  } catch (error) {
    if (error instanceof WorkspaceBoundaryError) {
      return errorResponse(
        request,
        403,
        "WORKSPACE_ACCESS_DENIED",
        "workspace is outside the Web gateway boundary",
        "authorization",
        false,
      );
    }
    if (error instanceof TrustedPrincipalConfigurationError) {
      return errorResponse(
        request,
        500,
        "API_PROXY_MISCONFIGURED",
        "API proxy identity is misconfigured",
        "configuration",
        false,
      );
    }
    throw error;
  }

  const headers = new Headers();
  for (const header of FORWARDED_HEADERS) {
    const value = request.headers.get(header);
    if (value) headers.set(header, value);
  }
  headers.set("x-actor-id", principal.actorId);
  headers.set("x-trusted-principal", principal.token);

  let body: Uint8Array | undefined;
  try {
    body =
      request.method === "GET" ? undefined : await readBoundedRequestBody(request);
  } catch (error) {
    if (error instanceof RequestBodyTooLargeError) {
      return errorResponse(
        request,
        413,
        "REQUEST_BODY_TOO_LARGE",
        "request body exceeds the control API proxy limit",
        "validation",
        false,
      );
    }
    throw error;
  }

  const timeoutController = new AbortController();
  const timeout = setTimeout(
    () =>
      timeoutController.abort(
        new DOMException("upstream request timed out", "TimeoutError"),
      ),
    timeoutMs,
  );
  try {
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body,
      cache: "no-store",
      signal: timeoutController.signal,
    });
    const responseHeaders = new Headers();
    for (const header of ["content-type", "x-request-id", "x-trace-id"]) {
      const value = upstream.headers.get(header);
      if (value) responseHeaders.set(header, value);
    }
    const responseBody = await readBoundedUpstreamBody(upstream);
    const response = new Response(responseBody, {
      status: upstream.status,
      headers: responseHeaders,
    });
    clearTimeout(timeout);
    return response;
  } catch (error) {
    clearTimeout(timeout);
    if (timeoutController.signal.aborted) {
      return errorResponse(
        request,
        504,
        "UPSTREAM_TIMEOUT",
        "control API did not respond before the proxy deadline",
        "transient",
        true,
      );
    }
    if (error instanceof UpstreamResponseTooLargeError) {
      return errorResponse(
        request,
        502,
        "UPSTREAM_RESPONSE_TOO_LARGE",
        "control API response exceeds the proxy limit",
        "upstream",
        false,
      );
    }
    return errorResponse(
      request,
      503,
      "SERVICE_UNAVAILABLE",
      "control API is unavailable",
      "transient",
      true,
    );
  }
}

export async function GET(request: Request, context: RouteContext) {
  return proxyApi(request, context);
}

export async function POST(request: Request, context: RouteContext) {
  return proxyApi(request, context);
}

export async function PUT(request: Request, context: RouteContext) {
  return proxyApi(request, context);
}

export async function DELETE(request: Request, context: RouteContext) {
  return proxyApi(request, context);
}
