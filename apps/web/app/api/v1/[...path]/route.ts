const DEFAULT_API_PROXY_URL = "http://api:8000";
const FORWARDED_HEADERS = [
  "accept",
  "content-type",
  "idempotency-key",
  "x-actor-id",
  "x-request-id",
  "x-trace-id",
  "x-workspace-id",
] as const;

type RouteContext = {
  params: Promise<{ path: string[] }>;
};

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

function catalogMethodAllowed(path: string, method: string): boolean {
  if (path === "/products") return method === "GET" || method === "POST";
  if (/^\/products\/[^/]+$/.test(path)) {
    return method === "GET" || method === "PUT" || method === "DELETE";
  }
  if (/^\/products\/[^/]+\/skus$/.test(path)) return method === "POST";
  if (/^\/products\/[^/]+\/skus\/[^/]+$/.test(path)) {
    return method === "PUT" || method === "DELETE";
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
  if (!catalogMethodAllowed(path, method)) {
    return errorResponse(
      request,
      404,
      "NOT_FOUND",
      "catalog route was not found",
      "not_found",
      false,
    );
  }
  const response = errorResponse(
    request,
    405,
    "METHOD_NOT_ALLOWED",
    "catalog method was not allowed",
    "validation",
    false,
  );
  response.headers.set("Allow", "GET, POST, PUT, DELETE");
  return response;
}

async function proxyCatalog(request: Request, context: RouteContext): Promise<Response> {
  const { path: segments } = await context.params;
  const path = `/${segments.map((segment) => encodeURIComponent(segment)).join("/")}`;
  if (!catalogMethodAllowed(path, request.method)) {
    return notAllowedResponse(request, path, request.method);
  }

  let target: URL;
  try {
    target = proxyOrigin();
  } catch {
    return errorResponse(
      request,
      500,
      "API_PROXY_MISCONFIGURED",
      "catalog API proxy is misconfigured",
      "configuration",
      false,
    );
  }
  target.pathname = `${target.pathname}/api/v1${path}`.replace(/\/{2,}/g, "/");
  target.search = new URL(request.url).search;

  const headers = new Headers();
  for (const header of FORWARDED_HEADERS) {
    const value = request.headers.get(header);
    if (value) headers.set(header, value);
  }

  try {
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body: request.method === "GET" ? undefined : await request.arrayBuffer(),
      cache: "no-store",
    });
    const responseHeaders = new Headers();
    for (const header of ["content-type", "x-request-id", "x-trace-id"]) {
      const value = upstream.headers.get(header);
      if (value) responseHeaders.set(header, value);
    }
    return new Response(upstream.body, {
      status: upstream.status,
      headers: responseHeaders,
    });
  } catch {
    return errorResponse(
      request,
      503,
      "SERVICE_UNAVAILABLE",
      "catalog API is unavailable",
      "transient",
      true,
    );
  }
}

export async function GET(request: Request, context: RouteContext) {
  return proxyCatalog(request, context);
}

export async function POST(request: Request, context: RouteContext) {
  return proxyCatalog(request, context);
}

export async function PUT(request: Request, context: RouteContext) {
  return proxyCatalog(request, context);
}

export async function DELETE(request: Request, context: RouteContext) {
  return proxyCatalog(request, context);
}
