import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import test from "node:test";

import { GET as GET_WORKSPACE_CAPABILITIES } from "../app/api/web-capabilities/route.ts";
import { GET, POST } from "../app/api/v1/[...path]/route.ts";

const TRUSTED_KEY_ID = "web-gateway-test";
const TRUSTED_SECRET = "web-gateway-test-secret-at-least-32-characters";
const TRUSTED_ACTOR_ID = "catalog-web-test";

process.env.CV_TRUSTED_PRINCIPAL_CURRENT_KEY_ID = TRUSTED_KEY_ID;
process.env.CV_TRUSTED_PRINCIPAL_CURRENT_HMAC_SECRET = TRUSTED_SECRET;
process.env.CV_WEB_ALLOWED_WORKSPACE_IDS = "workspace-1";
process.env.CV_WEB_PRINCIPAL_ACTOR_ID = TRUSTED_ACTOR_ID;

function verifyTrustedPrincipal(
  token,
  workspaceId,
  issuedAfter,
  expectedAdminWorkspaceIds = [],
) {
  const [keyId, encoded, signature] = token.split(".");
  assert.equal(keyId, TRUSTED_KEY_ID);
  assert.equal(
    signature,
    createHmac("sha256", TRUSTED_SECRET)
      .update(`${keyId}.${encoded}`)
      .digest("hex"),
  );
  const claims = JSON.parse(Buffer.from(encoded, "base64url").toString("utf8"));
  assert.deepEqual(
    {
      actor_id: claims.actor_id,
      admin_workspace_ids: claims.admin_workspace_ids,
      system_admin: claims.system_admin,
      workspace_ids: claims.workspace_ids,
    },
    {
      actor_id: TRUSTED_ACTOR_ID,
      admin_workspace_ids: expectedAdminWorkspaceIds,
      system_admin: false,
      workspace_ids: [workspaceId],
    },
  );
  assert.ok(Number.isSafeInteger(claims.issued_at));
  assert.ok(claims.issued_at >= issuedAfter);
  assert.ok(claims.issued_at <= Math.floor(Date.now() / 1000));
}

test("reports administrator UI capability from the same gateway authority boundary", async (context) => {
  const originalAdminWorkspaces = process.env.CV_WEB_ADMIN_WORKSPACE_IDS;
  context.after(() => {
    if (originalAdminWorkspaces === undefined) {
      delete process.env.CV_WEB_ADMIN_WORKSPACE_IDS;
    } else {
      process.env.CV_WEB_ADMIN_WORKSPACE_IDS = originalAdminWorkspaces;
    }
  });

  process.env.CV_WEB_ADMIN_WORKSPACE_IDS = "workspace-1";
  const allowed = await GET_WORKSPACE_CAPABILITIES(
    new Request("http://web.local/api/web-capabilities", {
      headers: { "x-workspace-id": "workspace-1" },
    }),
  );
  delete process.env.CV_WEB_ADMIN_WORKSPACE_IDS;
  const denied = await GET_WORKSPACE_CAPABILITIES(
    new Request("http://web.local/api/web-capabilities", {
      headers: { "x-workspace-id": "workspace-1" },
    }),
  );
  const hidden = await GET_WORKSPACE_CAPABILITIES(
    new Request("http://web.local/api/web-capabilities", {
      headers: { "x-workspace-id": "outside-boundary" },
    }),
  );

  assert.equal(allowed.status, 200);
  assert.deepEqual(await allowed.json(), { administrator: true });
  assert.equal(denied.status, 200);
  assert.deepEqual(await denied.json(), { administrator: false });
  assert.equal(hidden.status, 404);
});

test("signs administrator authority only for an explicitly configured workspace", async (context) => {
  const originalFetch = globalThis.fetch;
  const originalAdminWorkspaces = process.env.CV_WEB_ADMIN_WORKSPACE_IDS;
  let upstreamRequest;
  process.env.CV_WEB_ADMIN_WORKSPACE_IDS = "workspace-1";
  globalThis.fetch = async (input, init) => {
    upstreamRequest = {
      headers: Object.fromEntries(init.headers.entries()),
      url: String(input),
    };
    return Response.json({ accepted: true });
  };
  context.after(() => {
    globalThis.fetch = originalFetch;
    if (originalAdminWorkspaces === undefined) {
      delete process.env.CV_WEB_ADMIN_WORKSPACE_IDS;
    } else {
      process.env.CV_WEB_ADMIN_WORKSPACE_IDS = originalAdminWorkspaces;
    }
  });

  const issuedAfter = Math.floor(Date.now() / 1000);
  const response = await POST(
    new Request(
      "http://web.local/api/v1/assets/019f8a00-0000-7000-8000-000000000099:block",
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "idempotency-key": "proxy-admin-block-idempotency",
          "x-workspace-id": "workspace-1",
        },
        body: JSON.stringify({
          expected_asset_version: 4,
          reason: "legal hold",
          evidence_reference: "evidence://admin/42",
        }),
      },
    ),
    {
      params: Promise.resolve({
        path: [
          "assets",
          "019f8a00-0000-7000-8000-000000000099:block",
        ],
      }),
    },
  );

  assert.equal(response.status, 200);
  verifyTrustedPrincipal(
    upstreamRequest.headers["x-trusted-principal"],
    "workspace-1",
    issuedAfter,
    ["workspace-1"],
  );
});

test("finalize keeps its route delimiter across the HTTP proxy seam", async (context) => {
  const originalFetch = globalThis.fetch;
  let upstreamRequest;
  globalThis.fetch = async (input, init) => {
    upstreamRequest = {
      body: Buffer.from(init.body).toString("utf8"),
      headers: Object.fromEntries(init.headers.entries()),
      method: init.method,
      url: String(input),
    };
    return Response.json({ accepted: true }, { status: 202 });
  };
  context.after(() => {
    globalThis.fetch = originalFetch;
  });

  const issuedAfter = Math.floor(Date.now() / 1000);
  const request = new Request(
    "http://web.local/api/v1/upload-sessions/session-1:finalize",
    {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "idempotency-key": "proxy-finalize-idempotency",
        "x-actor-id": "browser-spoofed-actor",
        "x-trusted-principal": "browser.spoofed.principal",
        "x-workspace-id": "workspace-1",
      },
      body: JSON.stringify({ expected_version: 1 }),
    },
  );
  const response = await POST(request, {
    params: Promise.resolve({
      path: ["upload-sessions", "session-1:finalize"],
    }),
  });

  assert.equal(response.status, 202);
  const trustedPrincipal = upstreamRequest.headers["x-trusted-principal"];
  assert.equal(typeof trustedPrincipal, "string");
  verifyTrustedPrincipal(trustedPrincipal, "workspace-1", issuedAfter);
  assert.deepEqual(upstreamRequest, {
    body: '{"expected_version":1}',
    headers: {
      "content-type": "application/json",
      "idempotency-key": "proxy-finalize-idempotency",
      "x-actor-id": TRUSTED_ACTOR_ID,
      "x-trusted-principal": trustedPrincipal,
      "x-workspace-id": "workspace-1",
    },
    method: "POST",
    url: "http://api:8000/api/v1/upload-sessions/session-1:finalize",
  });
});

test("rights workbench routes cross only their exact signed proxy seams", async (context) => {
  const originalFetch = globalThis.fetch;
  const upstreamRequests = [];
  globalThis.fetch = async (input, init) => {
    upstreamRequests.push({
      method: init.method,
      url: String(input),
    });
    return Response.json({ accepted: true });
  };
  context.after(() => {
    globalThis.fetch = originalFetch;
  });

  const assetId = "019f8a00-0000-7000-8000-000000000061";
  const cases = [
    {
      handler: GET,
      method: "GET",
      path: ["assets", assetId, "rights"],
      suffix: `/assets/${assetId}/rights`,
    },
    {
      handler: POST,
      method: "POST",
      path: ["assets", assetId, "rights"],
      suffix: `/assets/${assetId}/rights`,
    },
    {
      handler: POST,
      method: "POST",
      path: ["assets", assetId, "rights:replace"],
      suffix: `/assets/${assetId}/rights:replace`,
    },
    {
      handler: POST,
      method: "POST",
      path: ["assets", assetId, "rights:revoke"],
      suffix: `/assets/${assetId}/rights:revoke`,
    },
    {
      handler: POST,
      method: "POST",
      path: ["assets", assetId, "usability:check"],
      suffix: `/assets/${assetId}/usability:check`,
    },
    {
      handler: POST,
      method: "POST",
      path: ["assets", `${assetId}:block`],
      suffix: `/assets/${assetId}:block`,
    },
  ];

  for (const route of cases) {
    const response = await route.handler(
      new Request(`http://web.local/api/v1${route.suffix}`, {
        method: route.method,
        headers: {
          "content-type": "application/json",
          "idempotency-key": "rights-proxy-idempotency",
          "x-workspace-id": "workspace-1",
        },
        body: route.method === "POST" ? "{}" : undefined,
      }),
      { params: Promise.resolve({ path: route.path }) },
    );
    assert.equal(response.status, 200);
  }

  assert.deepEqual(
    upstreamRequests,
    cases.map((route) => ({
      method: route.method,
      url: `http://api:8000/api/v1${route.suffix}`,
    })),
  );

  const deniedSuffix = await GET(
    new Request(`http://web.local/api/v1/assets/${assetId}/rights/export`),
    {
      params: Promise.resolve({
        path: ["assets", assetId, "rights", "export"],
      }),
    },
  );
  const deniedAction = await POST(
    new Request(`http://web.local/api/v1/assets/${assetId}:delete`, {
      method: "POST",
    }),
    {
      params: Promise.resolve({ path: ["assets", `${assetId}:delete`] }),
    },
  );
  assert.equal(deniedSuffix.status, 404);
  assert.equal(deniedAction.status, 404);
  assert.equal(upstreamRequests.length, cases.length);
});

test("only the exact operation GET path crosses the HTTP proxy seam", async (context) => {
  const originalFetch = globalThis.fetch;
  const upstreamRequests = [];
  globalThis.fetch = async (input, init) => {
    upstreamRequests.push({
      headers: Object.fromEntries(init.headers.entries()),
      method: init.method,
      url: String(input),
    });
    return Response.json({ state: "SUCCEEDED" });
  };
  context.after(() => {
    globalThis.fetch = originalFetch;
  });

  const operationId = "019f8a00-0000-7000-8000-000000000013";
  const issuedAfter = Math.floor(Date.now() / 1000);
  const accepted = await GET(
    new Request(`http://web.local/api/v1/operations/${operationId}`, {
      headers: { "x-workspace-id": "workspace-1" },
    }),
    {
      params: Promise.resolve({ path: ["operations", operationId] }),
    },
  );
  assert.equal(accepted.status, 200);
  const trustedPrincipal =
    upstreamRequests[0].headers["x-trusted-principal"];
  assert.equal(typeof trustedPrincipal, "string");
  verifyTrustedPrincipal(trustedPrincipal, "workspace-1", issuedAfter);
  assert.deepEqual(upstreamRequests, [
    {
      headers: {
        "x-actor-id": TRUSTED_ACTOR_ID,
        "x-trusted-principal": trustedPrincipal,
        "x-workspace-id": "workspace-1",
      },
      method: "GET",
      url: `http://api:8000/api/v1/operations/${operationId}`,
    },
  ]);

  const deniedList = await GET(
    new Request("http://web.local/api/v1/operations"),
    {
      params: Promise.resolve({ path: ["operations"] }),
    },
  );
  const deniedIdentifier = await GET(
    new Request("http://web.local/api/v1/operations/not-a-uuid"),
    {
      params: Promise.resolve({ path: ["operations", "not-a-uuid"] }),
    },
  );
  const deniedMethod = await POST(
    new Request(`http://web.local/api/v1/operations/${operationId}`, {
      method: "POST",
    }),
    {
      params: Promise.resolve({ path: ["operations", operationId] }),
    },
  );
  assert.equal(deniedList.status, 404);
  assert.equal(deniedIdentifier.status, 404);
  assert.equal(deniedMethod.status, 404);
  assert.equal(upstreamRequests.length, 1);
});

test("only the exact asset validation GET path crosses the HTTP proxy seam", async (context) => {
  const originalFetch = globalThis.fetch;
  const upstreamRequests = [];
  globalThis.fetch = async (input, init) => {
    upstreamRequests.push({ method: init.method, url: String(input) });
    return Response.json({ stages: [] });
  };
  context.after(() => {
    globalThis.fetch = originalFetch;
  });

  const assetId = "019f8a00-0000-7000-8000-000000000011";
  const accepted = await GET(
    new Request(`http://web.local/api/v1/assets/${assetId}/validation`, {
      headers: { "x-workspace-id": "workspace-1" },
    }),
    {
      params: Promise.resolve({ path: ["assets", assetId, "validation"] }),
    },
  );
  const deniedSuffix = await GET(
    new Request(`http://web.local/api/v1/assets/${assetId}/validation/raw`),
    {
      params: Promise.resolve({
        path: ["assets", assetId, "validation", "raw"],
      }),
    },
  );

  assert.equal(accepted.status, 200);
  assert.equal(deniedSuffix.status, 404);
  assert.deepEqual(upstreamRequests, [
    {
      method: "GET",
      url: `http://api:8000/api/v1/assets/${assetId}/validation`,
    },
  ]);
});

test("does not sign or proxy a workspace outside the configured boundary", async (context) => {
  const originalFetch = globalThis.fetch;
  let upstreamRequests = 0;
  globalThis.fetch = async () => {
    upstreamRequests += 1;
    return Response.json({ accepted: true });
  };
  context.after(() => {
    globalThis.fetch = originalFetch;
  });

  const response = await GET(
    new Request(
      "http://web.local/api/v1/operations/019f8a00-0000-7000-8000-000000000013",
      { headers: { "x-workspace-id": "workspace-other" } },
    ),
    {
      params: Promise.resolve({
        path: ["operations", "019f8a00-0000-7000-8000-000000000013"],
      }),
    },
  );

  assert.equal(response.status, 403);
  assert.equal((await response.json()).code, "WORKSPACE_ACCESS_DENIED");
  assert.equal(upstreamRequests, 0);
});

test("fails closed when the trusted gateway secret is unavailable", async (context) => {
  const originalFetch = globalThis.fetch;
  const originalSecret =
    process.env.CV_TRUSTED_PRINCIPAL_CURRENT_HMAC_SECRET;
  let upstreamRequests = 0;
  delete process.env.CV_TRUSTED_PRINCIPAL_CURRENT_HMAC_SECRET;
  globalThis.fetch = async () => {
    upstreamRequests += 1;
    return Response.json({ accepted: true });
  };
  context.after(() => {
    globalThis.fetch = originalFetch;
    process.env.CV_TRUSTED_PRINCIPAL_CURRENT_HMAC_SECRET = originalSecret;
  });

  const response = await GET(
    new Request(
      "http://web.local/api/v1/operations/019f8a00-0000-7000-8000-000000000013",
      { headers: { "x-workspace-id": "workspace-1" } },
    ),
    {
      params: Promise.resolve({
        path: ["operations", "019f8a00-0000-7000-8000-000000000013"],
      }),
    },
  );

  assert.equal(response.status, 500);
  assert.equal((await response.json()).code, "API_PROXY_MISCONFIGURED");
  assert.equal(upstreamRequests, 0);
});

test("fails closed instead of trimming a configured workspace identity", async (context) => {
  const originalFetch = globalThis.fetch;
  const originalWorkspaces = process.env.CV_WEB_ALLOWED_WORKSPACE_IDS;
  let upstreamRequests = 0;
  process.env.CV_WEB_ALLOWED_WORKSPACE_IDS = " workspace-1";
  globalThis.fetch = async () => {
    upstreamRequests += 1;
    return Response.json({ accepted: true });
  };
  context.after(() => {
    globalThis.fetch = originalFetch;
    process.env.CV_WEB_ALLOWED_WORKSPACE_IDS = originalWorkspaces;
  });

  const response = await GET(
    new Request(
      "http://web.local/api/v1/operations/019f8a00-0000-7000-8000-000000000013",
      { headers: { "x-workspace-id": "workspace-1" } },
    ),
    {
      params: Promise.resolve({
        path: ["operations", "019f8a00-0000-7000-8000-000000000013"],
      }),
    },
  );

  assert.equal(response.status, 500);
  assert.equal((await response.json()).code, "API_PROXY_MISCONFIGURED");
  assert.equal(upstreamRequests, 0);
});

test("fails closed when an administrator workspace is not an allowed workspace", async (context) => {
  const originalFetch = globalThis.fetch;
  const originalAdminWorkspaces = process.env.CV_WEB_ADMIN_WORKSPACE_IDS;
  let upstreamRequests = 0;
  process.env.CV_WEB_ADMIN_WORKSPACE_IDS = "workspace-2";
  globalThis.fetch = async () => {
    upstreamRequests += 1;
    return Response.json({ accepted: true });
  };
  context.after(() => {
    globalThis.fetch = originalFetch;
    if (originalAdminWorkspaces === undefined) {
      delete process.env.CV_WEB_ADMIN_WORKSPACE_IDS;
    } else {
      process.env.CV_WEB_ADMIN_WORKSPACE_IDS = originalAdminWorkspaces;
    }
  });

  const response = await GET(
    new Request(
      "http://web.local/api/v1/operations/019f8a00-0000-7000-8000-000000000013",
      { headers: { "x-workspace-id": "workspace-1" } },
    ),
    {
      params: Promise.resolve({
        path: ["operations", "019f8a00-0000-7000-8000-000000000013"],
      }),
    },
  );

  assert.equal(response.status, 500);
  assert.equal((await response.json()).code, "API_PROXY_MISCONFIGURED");
  assert.equal(upstreamRequests, 0);
});

test("fails closed when the signing key id cannot form a three-part token", async (context) => {
  const originalFetch = globalThis.fetch;
  const originalKeyId = process.env.CV_TRUSTED_PRINCIPAL_CURRENT_KEY_ID;
  let upstreamRequests = 0;
  process.env.CV_TRUSTED_PRINCIPAL_CURRENT_KEY_ID = "web.gateway";
  globalThis.fetch = async () => {
    upstreamRequests += 1;
    return Response.json({ accepted: true });
  };
  context.after(() => {
    globalThis.fetch = originalFetch;
    process.env.CV_TRUSTED_PRINCIPAL_CURRENT_KEY_ID = originalKeyId;
  });

  const response = await GET(
    new Request(
      "http://web.local/api/v1/operations/019f8a00-0000-7000-8000-000000000013",
      { headers: { "x-workspace-id": "workspace-1" } },
    ),
    {
      params: Promise.resolve({
        path: ["operations", "019f8a00-0000-7000-8000-000000000013"],
      }),
    },
  );

  assert.equal(response.status, 500);
  assert.equal((await response.json()).code, "API_PROXY_MISCONFIGURED");
  assert.equal(upstreamRequests, 0);
});

test("rejects an oversized control-plane request before buffering or proxying it", async (context) => {
  const originalFetch = globalThis.fetch;
  let upstreamRequests = 0;
  globalThis.fetch = async () => {
    upstreamRequests += 1;
    return Response.json({ accepted: true });
  };
  context.after(() => {
    globalThis.fetch = originalFetch;
  });

  const response = await POST(
    new Request("http://web.local/api/v1/upload-sessions", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-workspace-id": "workspace-1",
      },
      body: "x".repeat(1024 * 1024 + 1),
    }),
    {
      params: Promise.resolve({ path: ["upload-sessions"] }),
    },
  );

  assert.equal(response.status, 413);
  assert.equal((await response.json()).code, "REQUEST_BODY_TOO_LARGE");
  assert.equal(upstreamRequests, 0);
});

test("terminates an unavailable upstream at the proxy deadline", async (context) => {
  const originalFetch = globalThis.fetch;
  const originalTimeout = process.env.CV_API_PROXY_TIMEOUT_MS;
  process.env.CV_API_PROXY_TIMEOUT_MS = "20";
  globalThis.fetch = async (_input, init) =>
    await new Promise((_resolve, reject) => {
      init.signal.addEventListener(
        "abort",
        () => reject(init.signal.reason),
        { once: true },
      );
    });
  context.after(() => {
    globalThis.fetch = originalFetch;
    if (originalTimeout === undefined) {
      delete process.env.CV_API_PROXY_TIMEOUT_MS;
    } else {
      process.env.CV_API_PROXY_TIMEOUT_MS = originalTimeout;
    }
  });

  const response = await GET(
    new Request(
      "http://web.local/api/v1/operations/019f8a00-0000-7000-8000-000000000013",
      { headers: { "x-workspace-id": "workspace-1" } },
    ),
    {
      params: Promise.resolve({
        path: ["operations", "019f8a00-0000-7000-8000-000000000013"],
      }),
    },
  );

  assert.equal(response.status, 504);
  assert.equal((await response.json()).code, "UPSTREAM_TIMEOUT");
});

test("keeps the proxy deadline active while reading the upstream body", async (context) => {
  const originalFetch = globalThis.fetch;
  const originalTimeout = process.env.CV_API_PROXY_TIMEOUT_MS;
  process.env.CV_API_PROXY_TIMEOUT_MS = "20";
  globalThis.fetch = async (_input, init) =>
    new Response(
      new ReadableStream({
        start(controller) {
          init.signal.addEventListener(
            "abort",
            () => controller.error(init.signal.reason),
            { once: true },
          );
        },
      }),
      {
        status: 200,
        headers: { "content-type": "application/json" },
      },
    );
  context.after(() => {
    globalThis.fetch = originalFetch;
    if (originalTimeout === undefined) {
      delete process.env.CV_API_PROXY_TIMEOUT_MS;
    } else {
      process.env.CV_API_PROXY_TIMEOUT_MS = originalTimeout;
    }
  });

  const response = await GET(
    new Request(
      "http://web.local/api/v1/operations/019f8a00-0000-7000-8000-000000000013",
      { headers: { "x-workspace-id": "workspace-1" } },
    ),
    {
      params: Promise.resolve({
        path: ["operations", "019f8a00-0000-7000-8000-000000000013"],
      }),
    },
  );

  assert.equal(response.status, 504);
  assert.equal((await response.json()).code, "UPSTREAM_TIMEOUT");
});
