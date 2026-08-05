import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import test from "node:test";

import { GET as GET_WORKSPACE_CAPABILITIES } from "../app/api/web-capabilities/route.ts";
import { GET, POST, PUT } from "../app/api/v1/[...path]/route.ts";

const TRUSTED_KEY_ID = "web-gateway-test";
const TRUSTED_SECRET = "web-gateway-test-secret-at-least-32-characters";
const TRUSTED_ACTOR_ID = "catalog-web-test";
const SAFE_READ_PRODUCT_BRIEF_ID =
  "019f8a00-0000-7000-8000-000000000021";
const SAFE_READ_URL =
  `http://web.local/api/v1/product-briefs/${SAFE_READ_PRODUCT_BRIEF_ID}`;
const SAFE_READ_PATH = ["product-briefs", SAFE_READ_PRODUCT_BRIEF_ID];
const BRAND_PROFILE_ID =
  "019f8a00-0000-7000-8000-000000000041";

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

function maximumBrandProfileDraft() {
  const suffix = (index) => index.toString(36).padStart(2, "0");
  return {
    rules: Array.from({ length: 64 }, (_, index) => ({
      code: `r${"x".repeat(125)}${suffix(index)}`,
      scope: "VISUAL",
      instruction: `${"😀".repeat(1022)}${suffix(index)}`,
    })),
    approved_colors: Array.from({ length: 32 }, (_, index) => ({
      name: `${"😀".repeat(62)}${suffix(index)}`,
      value: "#FFFFFFFF",
    })),
    required_marks: Array.from(
      { length: 64 },
      (_, index) => `${"😀".repeat(510)}${suffix(index)}`,
    ),
    prohibited_elements: Array.from(
      { length: 64 },
      (_, index) => `${"😀".repeat(510)}${suffix(index)}`,
    ),
    tone_constraints: Array.from(
      { length: 64 },
      (_, index) => `${"😀".repeat(510)}${suffix(index)}`,
    ),
    copy_constraints: Array.from(
      { length: 64 },
      (_, index) => `${"😀".repeat(510)}${suffix(index)}`,
    ),
    purpose: `p${"x".repeat(127)}`,
    provider: `p${"x".repeat(127)}`,
    requires_derivative: true,
    selected_assets: Array.from({ length: 64 }, (_, index) => ({
      asset_version_id:
        `019f8a00-0000-7000-8000-${index
          .toString(16)
          .padStart(12, "0")}`,
      role: "REFERENCE",
    })),
  };
}

function maximumBrandProfile(index) {
  const suffix = index.toString(36).padStart(2, "0");
  return {
    id:
      `019f8a00-0000-7000-8000-${index
        .toString(16)
        .padStart(12, "0")}`,
    workspace_id: `w${"x".repeat(127)}`,
    brand: "😀".repeat(128),
    profile_key: `p${"x".repeat(125)}${suffix}`,
    state: "DRAFT",
    draft: maximumBrandProfileDraft(),
    current_version_id: null,
    current_version_number: 0,
    version: 1,
    stale_at: null,
    created_by: "😀".repeat(128),
    created_at: "2026-07-30T08:00:00Z",
    updated_by: "😀".repeat(128),
    updated_at: "2026-07-30T08:00:00Z",
  };
}

function maximumBrandProfileVersion(index) {
  const draft = maximumBrandProfileDraft();
  return {
    id:
      `019f8a00-0000-7000-8001-${index
        .toString(16)
        .padStart(12, "0")}`,
    workspace_id: `w${"x".repeat(127)}`,
    profile_id: BRAND_PROFILE_ID,
    version_number: index,
    draft,
    content_sha256: "a".repeat(64),
    published_by: "😀".repeat(128),
    published_at: "2026-07-30T08:00:00Z",
    members: draft.selected_assets.map((selection, ordinal) => ({
      ordinal,
      asset_id:
        `019f8a00-0000-7000-8002-${ordinal
          .toString(16)
          .padStart(12, "0")}`,
      asset_version_id: selection.asset_version_id,
      role: selection.role,
      published_rights_record_id:
        `019f8a00-0000-7000-8003-${ordinal
          .toString(16)
          .padStart(12, "0")}`,
      published_rights_record_version: Number.MAX_SAFE_INTEGER,
      currently_usable: true,
      current_reason_code: "AUTHORIZED",
      current_rights_record_id:
        `019f8a00-0000-7000-8003-${ordinal
          .toString(16)
          .padStart(12, "0")}`,
      current_rights_record_version: Number.MAX_SAFE_INTEGER,
      decided_at: "2026-07-30T08:00:00Z",
    })),
  };
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

test("never proxies Operation or non-Workbench Workflow contracts", async (context) => {
  const originalFetch = globalThis.fetch;
  let upstreamRequests = 0;
  globalThis.fetch = async () => {
    upstreamRequests += 1;
    return Response.json({ state: "SUCCEEDED" });
  };
  context.after(() => {
    globalThis.fetch = originalFetch;
  });

  const operationId = "019f8a00-0000-7000-8000-000000000013";
  const workflowId = "019f8a00-0000-7000-8000-000000000014";
  const deniedOperation = await GET(
    new Request(`http://web.local/api/v1/operations/${operationId}`, {
      headers: { "x-workspace-id": "workspace-1" },
    }),
    {
      params: Promise.resolve({ path: ["operations", operationId] }),
    },
  );
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
  const deniedWorkflow = await GET(
    new Request(`http://web.local/api/v1/workflows/${workflowId}/checkpoint`, {
      headers: { "x-workspace-id": "workspace-1" },
    }),
    {
      params: Promise.resolve({
        path: ["workflows", workflowId, "checkpoint"],
      }),
    },
  );
  assert.equal(deniedOperation.status, 404);
  assert.equal(deniedList.status, 404);
  assert.equal(deniedIdentifier.status, 404);
  assert.equal(deniedMethod.status, 404);
  assert.equal(deniedWorkflow.status, 404);
  assert.equal(upstreamRequests, 0);
});

test("only exact ProductBrief and safe status paths cross the HTTP proxy seam", async (context) => {
  const originalFetch = globalThis.fetch;
  const upstreamRequests = [];
  globalThis.fetch = async (input, init) => {
    upstreamRequests.push({
      method: init.method,
      url: String(input),
    });
    return Response.json({ ok: true });
  };
  context.after(() => {
    globalThis.fetch = originalFetch;
  });

  const productBriefId = "019f8a00-0000-7000-8000-000000000021";
  const workflowId = "019f8a00-0000-7000-8000-000000000022";
  const operationId = "019f8a00-0000-7000-8000-000000000023";
  const headers = {
    "content-type": "application/json",
    "idempotency-key": "product-brief-proxy-test",
    "x-workspace-id": "workspace-1",
  };
  const cases = [
    {
      handler: GET,
      method: "GET",
      path: ["brand-profiles"],
      url: "http://web.local/api/v1/brand-profiles?brand=Northstar+Labs&limit=2",
      upstream:
        "http://api:8000/api/v1/brand-profiles?brand=Northstar+Labs&limit=2",
    },
    {
      handler: POST,
      method: "POST",
      path: ["product-briefs:analyze"],
      url: "http://web.local/api/v1/product-briefs:analyze",
      upstream: "http://api:8000/api/v1/product-briefs:analyze",
    },
    {
      handler: GET,
      method: "GET",
      path: ["product-briefs", productBriefId],
      url: `http://web.local/api/v1/product-briefs/${productBriefId}`,
      upstream: `http://api:8000/api/v1/product-briefs/${productBriefId}`,
    },
    {
      handler: GET,
      method: "GET",
      path: ["product-briefs", productBriefId, "versions"],
      url: `http://web.local/api/v1/product-briefs/${productBriefId}/versions`,
      upstream: `http://api:8000/api/v1/product-briefs/${productBriefId}/versions`,
    },
    {
      handler: POST,
      method: "POST",
      path: ["product-briefs", `${productBriefId}:revise`],
      url: `http://web.local/api/v1/product-briefs/${productBriefId}:revise`,
      upstream: `http://api:8000/api/v1/product-briefs/${productBriefId}:revise`,
    },
    {
      handler: POST,
      method: "POST",
      path: ["product-briefs", `${productBriefId}:confirm`],
      url: `http://web.local/api/v1/product-briefs/${productBriefId}:confirm`,
      upstream: `http://api:8000/api/v1/product-briefs/${productBriefId}:confirm`,
    },
    {
      handler: GET,
      method: "GET",
      path: ["product-briefs", "analysis-workflow-context", workflowId],
      url: `http://web.local/api/v1/product-briefs/analysis-workflow-context/${workflowId}`,
      upstream: `http://api:8000/api/v1/product-briefs/analysis-workflow-context/${workflowId}`,
    },
    {
      handler: GET,
      method: "GET",
      path: ["product-briefs", "workflow-context", workflowId],
      url: `http://web.local/api/v1/product-briefs/workflow-context/${workflowId}?product_brief_id=${productBriefId}`,
      upstream: `http://api:8000/api/v1/product-briefs/workflow-context/${workflowId}?product_brief_id=${productBriefId}`,
    },
    {
      handler: GET,
      method: "GET",
      path: [
        "product-briefs",
        productBriefId,
        "operations",
        operationId,
      ],
      url: `http://web.local/api/v1/product-briefs/${productBriefId}/operations/${operationId}`,
      upstream: `http://api:8000/api/v1/product-briefs/${productBriefId}/operations/${operationId}`,
    },
  ];

  for (const item of cases) {
    const response = await item.handler(
      new Request(item.url, {
        method: item.method,
        headers,
        body: item.method === "POST" ? "{}" : undefined,
      }),
      { params: Promise.resolve({ path: item.path }) },
    );
    assert.equal(response.status, 200);
  }
  assert.deepEqual(
    upstreamRequests.map(({ method, url }) => ({ method, url })),
    cases.map(({ method, upstream }) => ({ method, url: upstream })),
  );

  const deniedList = await GET(
    new Request("http://web.local/api/v1/product-briefs"),
    { params: Promise.resolve({ path: ["product-briefs"] }) },
  );
  const deniedIdentifier = await POST(
    new Request("http://web.local/api/v1/product-briefs/not-a-uuid:confirm", {
      method: "POST",
    }),
    {
      params: Promise.resolve({
        path: ["product-briefs", "not-a-uuid:confirm"],
      }),
    },
  );
  const deniedInternalWorkflow = await GET(
    new Request(`http://web.local/api/v1/workflows/${workflowId}/checkpoint`),
    {
      params: Promise.resolve({
        path: ["workflows", workflowId, "checkpoint"],
      }),
    },
  );
  const deniedInternalOperation = await GET(
    new Request(`http://web.local/api/v1/operations/${operationId}`),
    { params: Promise.resolve({ path: ["operations", operationId] }) },
  );
  assert.equal(deniedList.status, 404);
  assert.equal(deniedIdentifier.status, 404);
  assert.equal(deniedInternalWorkflow.status, 404);
  assert.equal(deniedInternalOperation.status, 404);
  assert.equal(upstreamRequests.length, cases.length);
});

test("only exact Brand Profile command and immutable history paths cross the HTTP proxy seam", async (context) => {
  const originalFetch = globalThis.fetch;
  const upstreamRequests = [];
  globalThis.fetch = async (input, init) => {
    upstreamRequests.push({
      method: init.method,
      url: String(input),
    });
    return Response.json({ ok: true });
  };
  context.after(() => {
    globalThis.fetch = originalFetch;
  });

  const profileId = "019f8a00-0000-7000-8000-000000000041";
  const headers = {
    "content-type": "application/json",
    "idempotency-key": "brand-profile-proxy-test",
    "x-workspace-id": "workspace-1",
  };
  const cases = [
    {
      handler: POST,
      method: "POST",
      path: ["brand-profiles"],
      url: "http://web.local/api/v1/brand-profiles",
      upstream: "http://api:8000/api/v1/brand-profiles",
    },
    {
      handler: GET,
      method: "GET",
      path: ["brand-profiles", profileId],
      url: `http://web.local/api/v1/brand-profiles/${profileId}`,
      upstream: `http://api:8000/api/v1/brand-profiles/${profileId}`,
    },
    {
      handler: PUT,
      method: "PUT",
      path: ["brand-profiles", profileId, "draft"],
      url: `http://web.local/api/v1/brand-profiles/${profileId}/draft`,
      upstream: `http://api:8000/api/v1/brand-profiles/${profileId}/draft`,
    },
    {
      handler: POST,
      method: "POST",
      path: ["brand-profiles", `${profileId}:validate`],
      url: `http://web.local/api/v1/brand-profiles/${profileId}:validate`,
      upstream: `http://api:8000/api/v1/brand-profiles/${profileId}:validate`,
    },
    {
      handler: POST,
      method: "POST",
      path: ["brand-profiles", `${profileId}:publish`],
      url: `http://web.local/api/v1/brand-profiles/${profileId}:publish`,
      upstream: `http://api:8000/api/v1/brand-profiles/${profileId}:publish`,
    },
    {
      handler: GET,
      method: "GET",
      path: ["brand-profiles", profileId, "versions"],
      url: `http://web.local/api/v1/brand-profiles/${profileId}/versions?limit=2`,
      upstream: `http://api:8000/api/v1/brand-profiles/${profileId}/versions?limit=2`,
    },
    {
      handler: GET,
      method: "GET",
      path: ["brand-profiles", profileId, "versions", "2"],
      url: `http://web.local/api/v1/brand-profiles/${profileId}/versions/2`,
      upstream: `http://api:8000/api/v1/brand-profiles/${profileId}/versions/2`,
    },
  ];

  for (const item of cases) {
    const response = await item.handler(
      new Request(item.url, {
        method: item.method,
        headers,
        body: item.method === "POST" ? "{}" : undefined,
      }),
      { params: Promise.resolve({ path: item.path }) },
    );
    assert.equal(response.status, 200);
  }
  assert.deepEqual(
    upstreamRequests.map(({ method, url }) => ({ method, url })),
    cases.map(({ method, upstream }) => ({ method, url: upstream })),
  );

  const deniedMalformedId = await GET(
    new Request("http://web.local/api/v1/brand-profiles/not-a-uuid"),
    { params: Promise.resolve({ path: ["brand-profiles", "not-a-uuid"] }) },
  );
  const uppercaseProfileId = profileId.toUpperCase();
  const deniedNonCanonicalId = await GET(
    new Request(
      `http://web.local/api/v1/brand-profiles/${uppercaseProfileId}`,
    ),
    {
      params: Promise.resolve({
        path: ["brand-profiles", uppercaseProfileId],
      }),
    },
  );
  const deniedUnknownAction = await POST(
    new Request(
      `http://web.local/api/v1/brand-profiles/${profileId}:force-publish`,
      { method: "POST" },
    ),
    {
      params: Promise.resolve({
        path: ["brand-profiles", `${profileId}:force-publish`],
      }),
    },
  );
  const deniedNestedHistory = await GET(
    new Request(
      `http://web.local/api/v1/brand-profiles/${profileId}/versions/2/members`,
    ),
    {
      params: Promise.resolve({
        path: ["brand-profiles", profileId, "versions", "2", "members"],
      }),
    },
  );
  assert.equal(deniedMalformedId.status, 404);
  assert.equal(deniedNonCanonicalId.status, 404);
  assert.equal(deniedUnknownAction.status, 404);
  assert.equal(deniedNestedHistory.status, 404);
  assert.equal(upstreamRequests.length, cases.length);
});

test("only exact retrieval run and bounded preview paths cross the signed proxy seam", async (context) => {
  const originalFetch = globalThis.fetch;
  const upstreamRequests = [];
  globalThis.fetch = async (input, init) => {
    upstreamRequests.push({ method: init.method, url: String(input) });
    return Response.json({ ok: true });
  };
  context.after(() => {
    globalThis.fetch = originalFetch;
  });

  const runId = "019f8a00-0000-7000-8000-000000000071";
  const headers = {
    "content-type": "application/json",
    "x-workspace-id": "workspace-1",
  };
  const cases = [
    {
      handler: POST,
      method: "POST",
      path: ["retrieval-runs"],
      url: "http://web.local/api/v1/retrieval-runs",
      upstream: "http://api:8000/api/v1/retrieval-runs",
    },
    {
      handler: GET,
      method: "GET",
      path: ["retrieval-runs", runId],
      url: `http://web.local/api/v1/retrieval-runs/${runId}`,
      upstream: `http://api:8000/api/v1/retrieval-runs/${runId}`,
    },
    {
      handler: POST,
      method: "POST",
      path: ["retrieval-runs", runId, "results", "1:preview"],
      url: `http://web.local/api/v1/retrieval-runs/${runId}/results/1:preview`,
      upstream: `http://api:8000/api/v1/retrieval-runs/${runId}/results/1:preview`,
    },
  ];

  for (const item of cases) {
    const response = await item.handler(
      new Request(item.url, {
        method: item.method,
        headers,
        body: item.method === "POST" ? "{}" : undefined,
      }),
      { params: Promise.resolve({ path: item.path }) },
    );
    assert.equal(response.status, 200);
  }
  assert.deepEqual(
    upstreamRequests.map(({ method, url }) => ({ method, url })),
    cases.map(({ method, upstream }) => ({ method, url: upstream })),
  );

  for (const deniedPath of [
    ["retrieval-runs", "not-a-uuid"],
    ["retrieval-runs", runId.toUpperCase()],
    ["retrieval-runs", runId, "results", "0:preview"],
    ["retrieval-runs", runId, "results", "51:preview"],
    ["retrieval-runs", runId, "results", "1:download"],
  ]) {
    const response = await POST(
      new Request(`http://web.local/api/v1/${deniedPath.join("/")}`, {
        method: "POST",
        headers,
        body: "{}",
      }),
      { params: Promise.resolve({ path: deniedPath }) },
    );
    assert.equal(response.status, 404);
  }
  assert.equal(upstreamRequests.length, cases.length);
});

test("only exact Collection rebuild admin paths cross the signed proxy seam", async (context) => {
  const originalFetch = globalThis.fetch;
  const upstreamRequests = [];
  globalThis.fetch = async (input, init) => {
    upstreamRequests.push({ method: init.method, url: String(input) });
    return Response.json({ ok: true });
  };
  context.after(() => {
    globalThis.fetch = originalFetch;
  });

  const rebuildId = "019f8a00-0000-7000-8000-000000000140";
  const headers = {
    "content-type": "application/json",
    "idempotency-key": "collection-rebuild-proxy-test",
    "x-workspace-id": "workspace-1",
  };
  const cases = [
    [POST, "POST", ["collections", "rebuilds"], "/collections/rebuilds"],
    [GET, "GET", ["collections", "rebuilds", rebuildId], `/collections/rebuilds/${rebuildId}`],
    [POST, "POST", ["collections", "rebuilds", `${rebuildId}:validate`], `/collections/rebuilds/${rebuildId}:validate`],
    [POST, "POST", ["collections", "rebuilds", `${rebuildId}:activate`], `/collections/rebuilds/${rebuildId}:activate`],
  ];
  for (const [handler, method, path, suffix] of cases) {
    const response = await handler(
      new Request(`http://web.local/api/v1${suffix}`, {
        method,
        headers,
        body: method === "POST" ? "{}" : undefined,
      }),
      { params: Promise.resolve({ path }) },
    );
    assert.equal(response.status, 200);
  }
  assert.deepEqual(
    upstreamRequests.map(({ method, url }) => ({ method, url })),
    cases.map(([, method, , suffix]) => ({
      method,
      url: `http://api:8000/api/v1${suffix}`,
    })),
  );

  const denied = await POST(
    new Request("http://web.local/api/v1/collections/rebuilds/not-a-uuid:activate", {
      method: "POST",
    }),
    { params: Promise.resolve({ path: ["collections", "rebuilds", "not-a-uuid:activate"] }) },
  );
  assert.equal(denied.status, 404);
});

test("enforces a two-item Brand Profile page before crossing the bounded response seam", async (context) => {
  const originalFetch = globalThis.fetch;
  const upstreamUrls = [];
  const boundedPage = {
    items: [maximumBrandProfile(1), maximumBrandProfile(2)],
    next_cursor: null,
  };
  const boundedBytes = Buffer.byteLength(JSON.stringify(boundedPage));
  const boundedHistoryBytes = Buffer.byteLength(
    JSON.stringify({
      items: [
        maximumBrandProfileVersion(1),
        maximumBrandProfileVersion(2),
      ],
      next_cursor: null,
    }),
  );
  assert.ok(boundedBytes > 1024 * 1024);
  assert.ok(boundedBytes < 2 * 1024 * 1024);
  assert.ok(boundedHistoryBytes > boundedBytes);
  assert.ok(boundedHistoryBytes < 2 * 1024 * 1024);
  globalThis.fetch = async (input) => {
    upstreamUrls.push(String(input));
    return Response.json(boundedPage);
  };
  context.after(() => {
    globalThis.fetch = originalFetch;
  });

  const defaulted = await GET(
    new Request(
      "http://web.local/api/v1/brand-profiles?brand=Northstar+Labs",
      { headers: { "x-workspace-id": "workspace-1" } },
    ),
    { params: Promise.resolve({ path: ["brand-profiles"] }) },
  );
  const unsafe = await GET(
    new Request(
      "http://web.local/api/v1/brand-profiles?brand=Northstar+Labs&limit=3",
      { headers: { "x-workspace-id": "workspace-1" } },
    ),
    { params: Promise.resolve({ path: ["brand-profiles"] }) },
  );

  assert.equal(defaulted.status, 200);
  assert.equal(Buffer.byteLength(await defaulted.text()), boundedBytes);
  assert.deepEqual(upstreamUrls, [
    "http://api:8000/api/v1/brand-profiles?brand=Northstar+Labs&limit=2",
  ]);
  assert.equal(unsafe.status, 400);
  assert.equal(
    (await unsafe.json()).code,
    "BRAND_PROFILE_PAGE_LIMIT_UNSAFE",
  );
});

test("keeps the hard response cap when a malformed upstream exceeds even the safe Brand Profile page", async (context) => {
  const originalFetch = globalThis.fetch;
  const oversizedPage = {
    items: [
      maximumBrandProfile(1),
      maximumBrandProfile(2),
      maximumBrandProfile(3),
    ],
    next_cursor: null,
  };
  assert.ok(
    Buffer.byteLength(JSON.stringify(oversizedPage)) >
      2 * 1024 * 1024,
  );
  globalThis.fetch = async () => Response.json(oversizedPage);
  context.after(() => {
    globalThis.fetch = originalFetch;
  });

  const response = await GET(
    new Request(
      `http://web.local/api/v1/brand-profiles/${BRAND_PROFILE_ID}/versions?limit=2`,
      { headers: { "x-workspace-id": "workspace-1" } },
    ),
    {
      params: Promise.resolve({
        path: ["brand-profiles", BRAND_PROFILE_ID, "versions"],
      }),
    },
  );

  assert.equal(response.status, 502);
  assert.equal(
    (await response.json()).code,
    "UPSTREAM_RESPONSE_TOO_LARGE",
  );
});

test("proxies bounded ProductBrief history summaries without full field payloads", async (context) => {
  const originalFetch = globalThis.fetch;
  const productBriefId = "019f8a00-0000-7000-8000-000000000021";
  const history = {
    items: Array.from({ length: 3 }, (_, index) => ({
      id: `019f8a00-0000-7000-8000-00000000003${index}`,
      product_brief_id: productBriefId,
      version_number: 3 - index,
      supersedes_version_id:
        index === 2 ? null : `019f8a00-0000-7000-8000-00000000003${index + 1}`,
      effective_state: index === 0 ? "AWAITING_CONFIRMATION" : "ARCHIVED",
      category: "BEAUTY",
      common_schema_version: "product-brief-common-v1",
      category_schema_version: "product-brief-beauty-v1",
      payload_sha256: "a".repeat(64),
      changed_field_paths: ["common.brand"],
      confirmation_required: true,
      unresolved_field_count: 1,
      review_policy_version: "review-v1",
      source: index === 2 ? "MODEL" : "HUMAN",
      prompt_version: index === 2 ? "prompt-v1" : null,
      provider_call:
        index === 2
          ? {
              provider: "deterministic-vision",
              requested_model: "vision-v1",
              resolved_model: "vision-v1",
              latency_ms: 125,
            }
          : null,
      actor_id: index === 2 ? "vision-provider" : "reviewer",
      revision_reason: index === 2 ? null : "Verified against source evidence",
      retention_class: "TASK",
      retention_deadline: "2026-07-31T00:00:00Z",
      created_at: `2026-07-29T00:00:0${index}Z`,
    })),
    next_cursor: null,
  };
  globalThis.fetch = async () => Response.json(history);
  context.after(() => {
    globalThis.fetch = originalFetch;
  });

  const response = await GET(
    new Request(
      `http://web.local/api/v1/product-briefs/${productBriefId}/versions`,
      { headers: { "x-workspace-id": "workspace-1" } },
    ),
    {
      params: Promise.resolve({
        path: ["product-briefs", productBriefId, "versions"],
      }),
    },
  );
  const body = await response.text();

  assert.equal(response.status, 200);
  assert.ok(Buffer.byteLength(body) < 2 * 1024 * 1024);
  const parsed = JSON.parse(body);
  assert.equal(parsed.items.length, 3);
  assert.ok(parsed.items.every((item) => !Object.hasOwn(item, "fields")));
});

test("only exact asset status GET paths cross the HTTP proxy seam", async (context) => {
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
  const deletion = await GET(
    new Request(`http://web.local/api/v1/assets/${assetId}/deletion`, {
      headers: { "x-workspace-id": "workspace-1" },
    }),
    {
      params: Promise.resolve({ path: ["assets", assetId, "deletion"] }),
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
  assert.equal(deletion.status, 200);
  assert.equal(deniedSuffix.status, 404);
  assert.deepEqual(upstreamRequests, [
    {
      method: "GET",
      url: `http://api:8000/api/v1/assets/${assetId}/validation`,
    },
    {
      method: "GET",
      url: `http://api:8000/api/v1/assets/${assetId}/deletion`,
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
    new Request(SAFE_READ_URL, {
      headers: { "x-workspace-id": "workspace-other" },
    }),
    {
      params: Promise.resolve({
        path: SAFE_READ_PATH,
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
    new Request(SAFE_READ_URL, {
      headers: { "x-workspace-id": "workspace-1" },
    }),
    {
      params: Promise.resolve({
        path: SAFE_READ_PATH,
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
    new Request(SAFE_READ_URL, {
      headers: { "x-workspace-id": "workspace-1" },
    }),
    {
      params: Promise.resolve({
        path: SAFE_READ_PATH,
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
    new Request(SAFE_READ_URL, {
      headers: { "x-workspace-id": "workspace-1" },
    }),
    {
      params: Promise.resolve({
        path: SAFE_READ_PATH,
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
    new Request(SAFE_READ_URL, {
      headers: { "x-workspace-id": "workspace-1" },
    }),
    {
      params: Promise.resolve({
        path: SAFE_READ_PATH,
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

test("aborts the upstream fetch when the incoming request is cancelled", async (context) => {
  const originalFetch = globalThis.fetch;
  const originalTimeout = process.env.CV_API_PROXY_TIMEOUT_MS;
  const clientController = new AbortController();
  const clientReason = new DOMException("client disconnected", "AbortError");
  let upstreamSignal;
  let signalUpstreamStarted;
  const upstreamStarted = new Promise((resolve) => {
    signalUpstreamStarted = resolve;
  });
  process.env.CV_API_PROXY_TIMEOUT_MS = "50";
  globalThis.fetch = async (_input, init) => {
    upstreamSignal = init.signal;
    signalUpstreamStarted();
    return await new Promise((_resolve, reject) => {
      init.signal.addEventListener(
        "abort",
        () => reject(init.signal.reason),
        { once: true },
      );
    });
  };
  context.after(() => {
    globalThis.fetch = originalFetch;
    if (originalTimeout === undefined) {
      delete process.env.CV_API_PROXY_TIMEOUT_MS;
    } else {
      process.env.CV_API_PROXY_TIMEOUT_MS = originalTimeout;
    }
  });

  const responsePromise = GET(
    new Request(SAFE_READ_URL, {
      headers: { "x-workspace-id": "workspace-1" },
      signal: clientController.signal,
    }),
    {
      params: Promise.resolve({
        path: SAFE_READ_PATH,
      }),
    },
  );
  await upstreamStarted;
  clientController.abort(clientReason);
  const abortedByClient = await Promise.race([
    new Promise((resolve) => {
      if (upstreamSignal.aborted) {
        resolve(upstreamSignal.reason === clientReason);
        return;
      }
      upstreamSignal.addEventListener(
        "abort",
        () => resolve(upstreamSignal.reason === clientReason),
        { once: true },
      );
    }),
    new Promise((resolve) => setTimeout(() => resolve(false), 10)),
  ]);
  const response = await responsePromise;

  assert.equal(abortedByClient, true);
  assert.equal(upstreamSignal.aborted, true);
  assert.equal(response.status, 503);
  assert.equal((await response.json()).code, "SERVICE_UNAVAILABLE");
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
    new Request(SAFE_READ_URL, {
      headers: { "x-workspace-id": "workspace-1" },
    }),
    {
      params: Promise.resolve({
        path: SAFE_READ_PATH,
      }),
    },
  );

  assert.equal(response.status, 504);
  assert.equal((await response.json()).code, "UPSTREAM_TIMEOUT");
});

test("propagates an upstream 410 before reading its stalled body", async (context) => {
  const originalFetch = globalThis.fetch;
  const originalTimeout = process.env.CV_API_PROXY_TIMEOUT_MS;
  let bodyCancelled = false;
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
        cancel() {
          bodyCancelled = true;
        },
      }),
      {
        status: 410,
        headers: {
          "content-type": "application/json",
          "x-request-id": "request-authoritative-gone",
        },
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
    new Request(SAFE_READ_URL, {
      headers: { "x-workspace-id": "workspace-1" },
    }),
    {
      params: Promise.resolve({
        path: SAFE_READ_PATH,
      }),
    },
  );

  assert.equal(response.status, 410);
  assert.equal(response.headers.get("x-request-id"), "request-authoritative-gone");
  assert.equal(await response.text(), "");
  assert.equal(bodyCancelled, true);
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
    new Request(SAFE_READ_URL, {
      headers: { "x-workspace-id": "workspace-1" },
    }),
    {
      params: Promise.resolve({
        path: SAFE_READ_PATH,
      }),
    },
  );

  assert.equal(response.status, 504);
  assert.equal((await response.json()).code, "UPSTREAM_TIMEOUT");
});

test("only exact Creative Plan Workbench routes cross the HTTP proxy seam", async (context) => {
  const originalFetch = globalThis.fetch;
  const upstreamRequests = [];
  globalThis.fetch = async (input, init) => {
    upstreamRequests.push({
      method: init.method,
      url: String(input),
    });
    return Response.json({ ok: true });
  };
  context.after(() => {
    globalThis.fetch = originalFetch;
  });

  const planId = "019f8a00-0000-7000-8000-000000000121";
  const workflowId = "019f8a00-0000-7000-8000-000000000122";
  const headers = {
    "content-type": "application/json",
    "idempotency-key": "creative-plan-proxy-test",
    "x-workspace-id": "workspace-1",
  };
  const cases = [
    {
      handler: GET,
      method: "GET",
      path: ["creative-plans", planId],
      url: `http://web.local/api/v1/creative-plans/${planId}?workflow_id=${workflowId}`,
      upstream: `http://api:8000/api/v1/creative-plans/${planId}?workflow_id=${workflowId}`,
    },
    {
      handler: GET,
      method: "GET",
      path: ["creative-plans", planId, "versions"],
      url: `http://web.local/api/v1/creative-plans/${planId}/versions?workflow_id=${workflowId}`,
      upstream: `http://api:8000/api/v1/creative-plans/${planId}/versions?workflow_id=${workflowId}`,
    },
    {
      handler: GET,
      method: "GET",
      path: ["creative-plans", planId, "versions", "2"],
      url: `http://web.local/api/v1/creative-plans/${planId}/versions/2?workflow_id=${workflowId}`,
      upstream: `http://api:8000/api/v1/creative-plans/${planId}/versions/2?workflow_id=${workflowId}`,
    },
    {
      handler: POST,
      method: "POST",
      path: ["creative-plans", `${planId}:revise`],
      url: `http://web.local/api/v1/creative-plans/${planId}:revise`,
      upstream: `http://api:8000/api/v1/creative-plans/${planId}:revise`,
    },
    {
      handler: GET,
      method: "GET",
      path: ["workflows", workflowId],
      url: `http://web.local/api/v1/workflows/${workflowId}`,
      upstream: `http://api:8000/api/v1/workflows/${workflowId}`,
    },
    {
      handler: GET,
      method: "GET",
      path: ["workflows", workflowId, "events"],
      url: `http://web.local/api/v1/workflows/${workflowId}/events`,
      upstream: `http://api:8000/api/v1/workflows/${workflowId}/events`,
    },
    ...["approve", "reject"].map((action) => ({
      handler: POST,
      method: "POST",
      path: ["workflows", workflowId, `creative-plan:${action}`],
      url: `http://web.local/api/v1/workflows/${workflowId}/creative-plan:${action}`,
      upstream: `http://api:8000/api/v1/workflows/${workflowId}/creative-plan:${action}`,
    })),
  ];

  for (const item of cases) {
    const response = await item.handler(
      new Request(item.url, {
        method: item.method,
        headers,
        body: item.method === "POST" ? "{}" : undefined,
      }),
      { params: Promise.resolve({ path: item.path }) },
    );
    assert.equal(response.status, 200);
  }
  assert.deepEqual(
    upstreamRequests.map(({ method, url }) => ({ method, url })),
    cases.map(({ method, upstream }) => ({ method, url: upstream })),
  );

  const denied = [
    [GET, "http://web.local/api/v1/creative-plans", ["creative-plans"]],
    [POST, "http://web.local/api/v1/creative-plans", ["creative-plans"]],
    [GET, "http://web.local/api/v1/workflows", ["workflows"]],
    [
      POST,
      `http://web.local/api/v1/workflows/${workflowId}:cancel`,
      ["workflows", `${workflowId}:cancel`],
    ],
    [
      GET,
      "http://web.local/api/v1/creative-plans/not-a-uuid",
      ["creative-plans", "not-a-uuid"],
    ],
  ];
  for (const [handler, url, path] of denied) {
    const response = await handler(
      new Request(url, { method: handler === POST ? "POST" : "GET" }),
      { params: Promise.resolve({ path }) },
    );
    assert.equal(response.status, 404);
  }
  assert.equal(upstreamRequests.length, cases.length);
});

test("streams Workflow SSE without buffering and forwards its opaque resume cursor", async (context) => {
  const originalFetch = globalThis.fetch;
  const originalTimeout = process.env.CV_API_PROXY_TIMEOUT_MS;
  const workflowId = "019f8a00-0000-7000-8000-000000000122";
  const cursor = "v1.current.cGF5bG9hZA.c2lnbmF0dXJl";
  const frame = `id: ${cursor}\nevent: workflow.created\ndata: {}\n\n`;
  let upstreamHeaders;
  process.env.CV_API_PROXY_TIMEOUT_MS = "25";
  globalThis.fetch = async (_input, init) => {
    upstreamHeaders = new Headers(init.headers);
    return new Response(
      new ReadableStream({
        start(controller) {
          controller.enqueue(new TextEncoder().encode(frame));
          init.signal.addEventListener(
            "abort",
            () => controller.error(init.signal.reason),
            { once: true },
          );
        },
      }),
      {
        status: 200,
        headers: {
          "cache-control": "no-cache, no-transform",
          "content-type": "text/event-stream; charset=utf-8",
          "x-accel-buffering": "no",
        },
      },
    );
  };
  context.after(() => {
    globalThis.fetch = originalFetch;
    if (originalTimeout === undefined) {
      delete process.env.CV_API_PROXY_TIMEOUT_MS;
    } else {
      process.env.CV_API_PROXY_TIMEOUT_MS = originalTimeout;
    }
  });

  const response = await GET(
    new Request(`http://web.local/api/v1/workflows/${workflowId}/events`, {
      headers: {
        accept: "text/event-stream",
        "last-event-id": cursor,
        "x-workspace-id": "workspace-1",
      },
    }),
    {
      params: Promise.resolve({
        path: ["workflows", workflowId, "events"],
      }),
    },
  );

  assert.equal(response.status, 200);
  assert.equal(response.headers.get("content-type"), "text/event-stream; charset=utf-8");
  assert.equal(response.headers.get("cache-control"), "no-cache, no-transform");
  assert.equal(response.headers.get("x-accel-buffering"), "no");
  assert.equal(upstreamHeaders.get("accept"), "text/event-stream");
  assert.equal(upstreamHeaders.get("last-event-id"), cursor);
  assert.ok(upstreamHeaders.get("x-trusted-principal"));
  const reader = response.body.getReader();
  const first = await reader.read();
  assert.equal(new TextDecoder().decode(first.value), frame);
  await reader.cancel();
});

test("rejects an unsafe Workflow resume cursor before contacting upstream", async (context) => {
  const originalFetch = globalThis.fetch;
  let upstreamCalls = 0;
  globalThis.fetch = async () => {
    upstreamCalls += 1;
    return Response.json([]);
  };
  context.after(() => {
    globalThis.fetch = originalFetch;
  });
  const workflowId = "019f8a00-0000-7000-8000-000000000122";

  const response = await GET(
    new Request(`http://web.local/api/v1/workflows/${workflowId}/events`, {
      headers: {
        accept: "text/event-stream",
        "last-event-id": "x".repeat(257),
        "x-workspace-id": "workspace-1",
      },
    }),
    { params: Promise.resolve({ path: ["workflows", workflowId, "events"] }) },
  );

  assert.equal(response.status, 400);
  assert.equal((await response.json()).code, "WORKFLOW_EVENT_CURSOR_INVALID");
  assert.equal(upstreamCalls, 0);
});
