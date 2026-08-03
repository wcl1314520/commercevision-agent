import { expect, Page, test } from "@playwright/test";

import type { ProductBriefFieldValueV1 } from "../lib/generated/catalog-api";

const PRODUCT_ID = "019f8a00-0000-7000-8000-000000000101";
const WORKFLOW_ID = "019f8a00-0000-7000-8000-000000000102";
const ASSET_VERSION_ID = "019f8a00-0000-7000-8000-000000000103";
const BRIEF_ID = "019f8a00-0000-7000-8000-000000000104";
const OPERATION_ID = "019f8a00-0000-7000-8000-000000000105";
const MODEL_VERSION_ID = "019f8a00-0000-7000-8000-000000000106";
const EXTERNAL_VERSION_ID = "019f8a00-0000-7000-8000-000000000107";
const HUMAN_VERSION_ID = "019f8a00-0000-7000-8000-000000000108";
const PRODUCT_B_ID = "019f8a00-0000-7000-8000-000000000110";
const REOPENED_OPERATION_ID = "019f8a00-0000-7000-8000-000000000111";
const REOPENED_VERSION_ID = "019f8a00-0000-7000-8000-000000000112";
const NEXT_BRIEF_ID = "019f8a00-0000-7000-8000-000000000113";
const NEXT_OPERATION_ID = "019f8a00-0000-7000-8000-000000000114";
const NEXT_VERSION_ID = "019f8a00-0000-7000-8000-000000000115";
const RETENTION_DEADLINE = "2099-01-01T00:00:00Z";

function textValue(text: string): ProductBriefFieldValueV1 {
  return { kind: "TEXT", text };
}

function textListValue(items: string[]): ProductBriefFieldValueV1 {
  return { kind: "TEXT_LIST", items };
}

function flagListValue(flags: string[]): ProductBriefFieldValueV1 {
  return { kind: "FLAG_LIST", flags };
}

function editorValue(value: ProductBriefFieldValueV1): string {
  return JSON.stringify(value, null, 2);
}

const product = {
  id: PRODUCT_ID,
  workspace_id: "catalog-demo",
  source_namespace: "MANUAL",
  external_id: "SERUM-HITL-001",
  source_version: "manual-v1",
  title: "Hydrating Serum",
  category_code: "beauty.skincare.serum",
  brand: "Northstar Labs",
  attributes: { volume_ml: 30 },
  expires_at: null,
  version: 1,
  created_at: "2026-07-28T01:00:00Z",
  updated_at: "2026-07-28T01:00:00Z",
  skus: [],
};

function evidence(id: string, referenceHash = "b".repeat(64)) {
  return {
    id,
    source_asset_version_id: ASSET_VERSION_ID,
    kind: "IMAGE_REGION",
    reference: `asset-region://${referenceHash}`,
    region: [0.1, 0.1, 0.9, 0.9],
    excerpt_sha256: "a".repeat(64),
  };
}

function field({
  id,
  path,
  value,
  confidence = "0.9600",
  conflict = "NONE",
  sensitive = false,
  reviewReasons = [],
  source = "MODEL",
  evidenceHash = "b".repeat(64),
}: {
  id: string;
  path: string;
  value: ProductBriefFieldValueV1;
  confidence?: string;
  conflict?: string;
  sensitive?: boolean;
  reviewReasons?: string[];
  source?: string;
  evidenceHash?: string;
}) {
  return {
    id,
    path,
    value,
    confidence,
    source,
    conflict,
    review_required: false,
    sensitive,
    review_reasons: reviewReasons,
    evidence: [evidence(`${id}-evidence`, evidenceHash)],
  };
}

function version({
  id,
  number,
  source = "MODEL",
  actor = "system:vision-analyzer",
  reason = null,
  fields,
}: {
  id: string;
  number: number;
  source?: string;
  actor?: string;
  reason?: string | null;
  fields: ReturnType<typeof field>[];
}) {
  return {
    id,
    product_brief_id: BRIEF_ID,
    version_number: number,
    supersedes_version_id:
      number === 1
        ? null
        : number === 2
          ? MODEL_VERSION_ID
          : EXTERNAL_VERSION_ID,
    effective_state: "AWAITING_CONFIRMATION",
    category: "BEAUTY",
    common_schema_version: "product-brief-common-v1",
    category_schema_version: "product-brief-beauty-v1",
    payload_sha256: String(number).repeat(64),
    changed_field_paths:
      source === "MODEL"
        ? fields.map((item) => item.path)
        : ["common.brand"],
    confirmation_required: true,
    unresolved_field_count: fields.filter(
      (item) =>
        Number(item.confidence) < 0.8 ||
        item.conflict === "CONFLICTING" ||
        item.sensitive,
    ).length,
    review_policy_version: "product-brief-review-v1",
    source,
    prompt_version: source === "MODEL" ? "product-brief-prompt-v1" : null,
    provider_call:
      source === "MODEL"
        ? {
            provider: "deterministic-vision",
            requested_model: "deterministic-vision-v1",
            resolved_model: "deterministic-vision-v1",
            latency_ms: 4,
          }
        : null,
    actor_id: actor,
    revision_reason: reason,
    retention_class: "TASK",
    retention_deadline: RETENTION_DEADLINE,
    created_at: `2026-07-28T01:0${number}:00Z`,
    fields,
  };
}

type BrowserVersion = ReturnType<typeof version>;
type BrowserBrief = {
  id: string;
  workspace_id: string;
  workflow_id: string;
  product_id: string;
  operation_id: string;
  state: string;
  current_version_id: string;
  confirmed_version_id: string | null;
  version: number;
  retention_class: string;
  retention_deadline: string;
  created_at: string;
  updated_at: string;
  current_version: BrowserVersion;
  confirmed_version: BrowserVersion | null;
};

function brief(
  currentVersion: BrowserVersion,
  aggregateVersion: number,
): BrowserBrief {
  return {
    id: BRIEF_ID,
    workspace_id: "catalog-demo",
    workflow_id: WORKFLOW_ID,
    product_id: PRODUCT_ID,
    operation_id: OPERATION_ID,
    state: "AWAITING_CONFIRMATION",
    current_version_id: currentVersion.id,
    confirmed_version_id: null,
    version: aggregateVersion,
    retention_class: "TASK",
    retention_deadline: RETENTION_DEADLINE,
    created_at: "2026-07-28T01:00:00Z",
    updated_at: "2026-07-28T01:02:00Z",
    current_version: currentVersion,
    confirmed_version: null,
  };
}

function confirmedBrief(
  currentVersion: BrowserVersion,
  aggregateVersion: number,
): BrowserBrief {
  const confirmedVersion = {
    ...currentVersion,
    effective_state: "CONFIRMED",
  };
  return {
    ...brief(confirmedVersion, aggregateVersion),
    state: "CONFIRMED",
    confirmed_version_id: confirmedVersion.id,
    current_version: confirmedVersion,
    confirmed_version: confirmedVersion,
  };
}

async function routeCatalog(page: Page) {
  await page.addInitScript((productId) => {
    sessionStorage.setItem(
      "commercevision.product-brief.active.v2",
      JSON.stringify({
        workspaceId: "catalog-demo",
        productId,
      }),
    );
  }, PRODUCT_ID);
  await page.route("**/api/v1/products**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(
        path === `/api/v1/products/${PRODUCT_ID}`
          ? product
          : { items: [product], next_cursor: null },
      ),
    });
  });
}

async function exhaustAutomaticPolling(
  page: Page,
  requestCount: () => number,
) {
  const continueButton = page.getByRole("button", { name: "继续刷新" });
  for (let step = 0; step < 30; step += 1) {
    if (await continueButton.isVisible()) return;
    const requestsBeforeAdvance = requestCount();
    await page.clock.runFor(10_000);
    await expect
      .poll(requestCount)
      .toBeGreaterThan(requestsBeforeAdvance);
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  await expect(continueButton).toBeVisible();
}

test("replays uncertain and retryable analysis with one durable identity", async ({
  page,
}) => {
  await routeCatalog(page);
  const modelVersion = version({
    id: MODEL_VERSION_ID,
    number: 1,
    fields: [
      field({
        id: "field-replay-brand",
        path: "common.brand",
        value: textValue("Northstar Labs"),
      }),
    ],
  });
  const currentBrief = brief(modelVersion, 2);
  const analyzeRequests: Array<{ body: unknown; key: string | undefined }> = [];

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path.startsWith("/api/v1/products")) {
      await route.fallback();
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/analysis-workflow-context/${WORKFLOW_ID}`
    ) {
      expect(url.search).toBe("");
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: WORKFLOW_ID,
          status: "UNDERSTANDING",
          version: 3,
          retention_deadline: RETENTION_DEADLINE,
        }),
      });
      return;
    }
    if (path === "/api/v1/product-briefs:analyze") {
      analyzeRequests.push({
        body: request.postDataJSON(),
        key: request.headers()["idempotency-key"],
      });
      if (analyzeRequests.length === 1) {
        await route.abort("connectionreset");
        return;
      }
      if (analyzeRequests.length === 2) {
        await route.fulfill({
          status: 429,
          contentType: "application/json",
          body: JSON.stringify({
            code: "PROVIDER_RATE_LIMITED",
            category: "upstream",
            message: "Provider asked the caller to retry",
            retryable: true,
            trace_id: "trace-analysis-retryable",
          }),
        });
        return;
      }
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({
          product_brief: currentBrief,
          operation_id: OPERATION_ID,
          operation_state: "PENDING",
        }),
      });
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(currentBrief),
      });
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}/versions`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [modelVersion], next_cursor: null }),
      });
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/${BRIEF_ID}/operations/${OPERATION_ID}`
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: OPERATION_ID,
          state: "WAITING_HUMAN",
          attempt_count: 1,
          max_attempts: 5,
          error: null,
        }),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "not mocked" });
  });

  await page.goto("/");
  await page.getByLabel("工作流 ID").fill(WORKFLOW_ID);
  await page.getByLabel("素材版本 ID").fill(ASSET_VERSION_ID);
  await page.getByRole("button", { name: "开始商品理解" }).click();
  await expect(page.getByRole("button", { name: "安全重试" })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "开始商品理解" }),
  ).toBeDisabled();
  await expect(page.getByLabel("商品理解 ID")).toBeDisabled();
  const pending = await page.evaluate((productId) => {
    const raw = sessionStorage.getItem(
      `commercevision.product-brief.v2:catalog-demo:${productId}`,
    );
    return raw ? JSON.parse(raw) : null;
  }, PRODUCT_ID);
  expect(pending).toMatchObject({
    schemaVersion: 2,
    pendingAnalysis: {
      payload: analyzeRequests[0].body,
      idempotencyKey: analyzeRequests[0].key,
    },
  });
  expect(
    await page.evaluate((productId) =>
      localStorage.getItem(
        `commercevision.product-brief.v2:catalog-demo:${productId}`,
      ),
    PRODUCT_ID),
  ).toBeNull();

  await page.reload();
  await expect(page.getByRole("button", { name: "安全重试" })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "开始商品理解" }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "安全重试" }).click();
  await expect(page.locator("[data-testid='brief-operation']")).toBeVisible();
  expect(analyzeRequests.length).toBeGreaterThanOrEqual(3);
  expect(analyzeRequests.every((request) => request.key === analyzeRequests[0].key)).toBe(
    true,
  );
  expect(analyzeRequests.every((request) => JSON.stringify(request.body) === JSON.stringify(analyzeRequests[0].body))).toBe(
    true,
  );
  await expect(page.locator("[data-testid='brief-operation']")).toHaveAttribute(
    "role",
    "status",
  );
  await expect(page.locator("[data-testid='brief-operation']")).toHaveAttribute(
    "aria-live",
    "polite",
  );
});

test("replays an exact revision after a server commit and lost response", async ({
  page,
}) => {
  await routeCatalog(page);
  const modelVersion = version({
    id: MODEL_VERSION_ID,
    number: 1,
    fields: [
      field({
        id: "field-revision-replay-brand",
        path: "common.brand",
        value: textValue("Northstar Labs"),
      }),
    ],
  });
  let currentBrief = brief(modelVersion, 2);
  let history = [modelVersion];
  const revisionRequests: Array<{
    body: Record<string, unknown>;
    key: string | undefined;
  }> = [];
  let signalFirstRevision!: () => void;
  let releaseFirstRevision!: () => void;
  const firstRevisionReceived = new Promise<void>((resolve) => {
    signalFirstRevision = resolve;
  });
  const firstRevisionResponseGate = new Promise<void>((resolve) => {
    releaseFirstRevision = resolve;
  });
  const unrelatedPersistedState = {
    schemaVersion: 3,
    workspaceId: "catalog-demo",
    productId: PRODUCT_B_ID,
    productBriefId: NEXT_BRIEF_ID,
    operationId: NEXT_OPERATION_ID,
    workflowId: WORKFLOW_ID,
    assetVersionIds: [ASSET_VERSION_ID],
    retentionDeadline: RETENTION_DEADLINE,
    pendingCommand: {
      schemaVersion: 1,
      kind: "revise",
      productId: PRODUCT_B_ID,
      productBriefId: NEXT_BRIEF_ID,
      payload: {
        expected_product_brief_version: 9,
        base_version_id: NEXT_VERSION_ID,
        reason: "Unrelated persisted revision",
        fields: [
          {
            path: "common.brand",
            value: textValue("Unrelated preserved brand"),
            confidence: 1,
            conflict: "NONE",
            review_required: false,
            sensitive: false,
            evidence: [
              {
                source_asset_version_id: ASSET_VERSION_ID,
                kind: "IMAGE_REGION",
                reference: `asset-region://${"d".repeat(64)}`,
                region: [0.1, 0.1, 0.9, 0.9],
                excerpt_sha256: "e".repeat(64),
              },
            ],
          },
        ],
      },
      idempotencyKey: "web-product-brief-revise-unrelated",
    },
    commandStatus: "pending",
  };

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.startsWith("/api/v1/products")) {
      await route.fallback();
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(currentBrief),
      });
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}/versions`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: history, next_cursor: null }),
      });
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/${BRIEF_ID}/operations/${OPERATION_ID}`
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: OPERATION_ID,
          version: 1,
          state: "WAITING_HUMAN",
          attempt_count: 1,
          max_attempts: 5,
          error: null,
        }),
      });
      return;
    }
    if (
      path === `/api/v1/product-briefs/${BRIEF_ID}:revise` &&
      request.method() === "POST"
    ) {
      const body = request.postDataJSON() as Record<string, unknown>;
      revisionRequests.push({
        body,
        key: request.headers()["idempotency-key"],
      });
      if (revisionRequests.length === 1) {
        const requestField = (
          body.fields as Array<Record<string, unknown>>
        )[0];
        const humanVersion = version({
          id: HUMAN_VERSION_ID,
          number: 2,
          source: "HUMAN",
          actor: "catalog-workbench",
          reason: String(body.reason),
          fields: [
            field({
              id: "field-revision-replay-human-brand",
              path: "common.brand",
              value: requestField.value as ProductBriefFieldValueV1,
              source: "HUMAN",
            }),
          ],
        });
        currentBrief = brief(humanVersion, 3);
        history = [humanVersion, modelVersion];
        signalFirstRevision();
        await firstRevisionResponseGate;
        await route.abort("connectionreset");
        return;
      }
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(currentBrief),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "not mocked" });
  });

  await page.addInitScript(
    ({
      productId,
      productBId,
      briefId,
      operationId,
      workflowId,
      assetVersionId,
      unrelatedState,
    }) => {
      const productKey = `commercevision.product-brief.v2:catalog-demo:${productId}`;
      const productBKey = `commercevision.product-brief.v2:catalog-demo:${productBId}`;
      if (!sessionStorage.getItem(productKey)) {
        sessionStorage.setItem(
          productKey,
          JSON.stringify({
            schemaVersion: 1,
            workspaceId: "catalog-demo",
            productId,
      productBriefId: briefId,
      operationId,
      workflowId,
      assetVersionIds: [assetVersionId],
      retentionDeadline: "2099-01-01T00:00:00Z",
          }),
        );
      }
      if (!sessionStorage.getItem(productBKey)) {
        sessionStorage.setItem(
          productBKey,
          JSON.stringify(unrelatedState),
        );
      }
    },
    {
      productId: PRODUCT_ID,
      productBId: PRODUCT_B_ID,
      briefId: BRIEF_ID,
      operationId: OPERATION_ID,
      workflowId: WORKFLOW_ID,
      assetVersionId: ASSET_VERSION_ID,
      unrelatedState: unrelatedPersistedState,
    },
  );

  await page.goto("/");
  await expect(page.getByLabel("品牌值")).toHaveValue(
    editorValue(textValue("Northstar Labs")),
  );
  await page
    .getByLabel("品牌值")
    .fill(editorValue(textValue("Northstar Replay")));
  await page.getByLabel("修订原因").fill("Verified after response loss");
  await page.getByRole("button", { name: "保存人工版本" }).click();
  await firstRevisionReceived;

  try {
    const persistedBeforeResponse = await page.evaluate((productId) => {
      const raw = sessionStorage.getItem(
        `commercevision.product-brief.v2:catalog-demo:${productId}`,
      );
      return raw ? JSON.parse(raw) : null;
    }, PRODUCT_ID);
    expect(persistedBeforeResponse).toMatchObject({
      schemaVersion: 3,
      workspaceId: "catalog-demo",
      productId: PRODUCT_ID,
      productBriefId: BRIEF_ID,
      pendingCommand: {
        schemaVersion: 1,
        kind: "revise",
        productId: PRODUCT_ID,
        productBriefId: BRIEF_ID,
        payload: revisionRequests[0].body,
        idempotencyKey: revisionRequests[0].key,
      },
    });
  } finally {
    releaseFirstRevision();
  }
  await expect(page.getByRole("button", { name: "安全重试" })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "保存人工版本" }),
  ).toBeDisabled();
  await expect(
    page
      .locator(".product-brief-panel")
      .getByRole("button", { name: "刷新" }),
  ).toBeDisabled();

  await page.reload();
  await expect.poll(() => revisionRequests).toHaveLength(2);
  expect(revisionRequests[1]).toEqual(revisionRequests[0]);
  await expect(page.getByLabel("品牌值")).toHaveValue(
    editorValue(textValue("Northstar Replay")),
  );

  const settledState = await page.evaluate((productId) => {
    const raw = sessionStorage.getItem(
      `commercevision.product-brief.v2:catalog-demo:${productId}`,
    );
    return raw ? JSON.parse(raw) : null;
  }, PRODUCT_ID);
  expect(settledState).toMatchObject({
    schemaVersion: 1,
    workspaceId: "catalog-demo",
    productId: PRODUCT_ID,
    productBriefId: BRIEF_ID,
  });
  expect(settledState).not.toHaveProperty("pendingCommand");
  expect(
    await page.evaluate(
      (productBId) =>
        JSON.parse(
          sessionStorage.getItem(
            `commercevision.product-brief.v2:catalog-demo:${productBId}`,
          ) ?? "null",
        ),
      PRODUCT_B_ID,
    ),
  ).toEqual(unrelatedPersistedState);
});

test("replays an exact confirmation after a server commit and lost response", async ({
  page,
}) => {
  await routeCatalog(page);
  const modelVersion = version({
    id: MODEL_VERSION_ID,
    number: 1,
    fields: [
      field({
        id: "field-confirm-replay-brand",
        path: "common.brand",
        value: textValue("Northstar Labs"),
      }),
    ],
  });
  let currentBrief = brief(modelVersion, 2);
  let history = [modelVersion];
  let workflowReads = 0;
  let confirmationResponse: Record<string, unknown> | null = null;
  const confirmationRequests: Array<{
    body: Record<string, unknown>;
    key: string | undefined;
  }> = [];
  let signalFirstConfirmation!: () => void;
  let releaseFirstConfirmation!: () => void;
  const firstConfirmationReceived = new Promise<void>((resolve) => {
    signalFirstConfirmation = resolve;
  });
  const firstConfirmationResponseGate = new Promise<void>((resolve) => {
    releaseFirstConfirmation = resolve;
  });
  const unrelatedPersistedState = {
    schemaVersion: 3,
    workspaceId: "catalog-demo",
    productId: PRODUCT_B_ID,
    productBriefId: NEXT_BRIEF_ID,
    operationId: NEXT_OPERATION_ID,
    workflowId: WORKFLOW_ID,
    assetVersionIds: [ASSET_VERSION_ID],
    retentionDeadline: RETENTION_DEADLINE,
    pendingCommand: {
      schemaVersion: 1,
      kind: "confirm",
      productId: PRODUCT_B_ID,
      productBriefId: NEXT_BRIEF_ID,
      payload: {
        expected_product_brief_version: 10,
        product_brief_version_id: NEXT_VERSION_ID,
        expected_workflow_version: 14,
        reason_code: "UNRELATED",
        comment_ref: null,
      },
      idempotencyKey: "web-product-brief-confirm-unrelated",
    },
    commandStatus: "pending",
  };

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path.startsWith("/api/v1/products")) {
      await route.fallback();
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/workflow-context/${WORKFLOW_ID}`
    ) {
      expect(url.search).toBe(`?product_brief_id=${BRIEF_ID}`);
      workflowReads += 1;
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: WORKFLOW_ID,
          version: 6,
          retention_deadline: RETENTION_DEADLINE,
          status: "AWAITING_PRODUCT_CONFIRMATION",
        }),
      });
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(currentBrief),
      });
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}/versions`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: history, next_cursor: null }),
      });
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/${BRIEF_ID}/operations/${OPERATION_ID}`
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: OPERATION_ID,
          version: currentBrief.state === "CONFIRMED" ? 2 : 1,
          state:
            currentBrief.state === "CONFIRMED"
              ? "SUCCEEDED"
              : "WAITING_HUMAN",
          attempt_count: 1,
          max_attempts: 5,
          error: null,
        }),
      });
      return;
    }
    if (
      path === `/api/v1/product-briefs/${BRIEF_ID}:confirm` &&
      request.method() === "POST"
    ) {
      confirmationRequests.push({
        body: request.postDataJSON() as Record<string, unknown>,
        key: request.headers()["idempotency-key"],
      });
      if (confirmationRequests.length === 1) {
        currentBrief = confirmedBrief(modelVersion, 3);
        history = [currentBrief.current_version];
        confirmationResponse = {
          product_brief: currentBrief,
          workflow_id: WORKFLOW_ID,
          workflow_status: "RETRIEVING",
          workflow_version: 7,
          confirmation_id: "019f8a00-0000-7000-8000-000000000109",
        };
        signalFirstConfirmation();
        await firstConfirmationResponseGate;
        await route.abort("connectionreset");
        return;
      }
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(confirmationResponse),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "not mocked" });
  });

  await page.addInitScript(
    ({
      productId,
      productBId,
      briefId,
      operationId,
      workflowId,
      assetVersionId,
      unrelatedState,
    }) => {
      const productKey = `commercevision.product-brief.v2:catalog-demo:${productId}`;
      const productBKey = `commercevision.product-brief.v2:catalog-demo:${productBId}`;
      if (!sessionStorage.getItem(productKey)) {
        sessionStorage.setItem(
          productKey,
          JSON.stringify({
            schemaVersion: 1,
            workspaceId: "catalog-demo",
            productId,
            productBriefId: briefId,
            operationId,
            workflowId,
            assetVersionIds: [assetVersionId],
            retentionDeadline: "2099-01-01T00:00:00Z",
          }),
        );
      }
      if (!sessionStorage.getItem(productBKey)) {
        sessionStorage.setItem(
          productBKey,
          JSON.stringify(unrelatedState),
        );
      }
    },
    {
      productId: PRODUCT_ID,
      productBId: PRODUCT_B_ID,
      briefId: BRIEF_ID,
      operationId: OPERATION_ID,
      workflowId: WORKFLOW_ID,
      assetVersionId: ASSET_VERSION_ID,
      unrelatedState: unrelatedPersistedState,
    },
  );

  await page.goto("/");
  await expect(page.getByLabel("确认当前精确版本")).toBeEnabled();
  await page.getByLabel("确认原因编码").fill("PACKAGE_VERIFIED");
  await page.getByLabel("评论引用").fill("comment://response-loss");
  await page.getByLabel("确认当前精确版本").check();
  await page.getByRole("button", { name: "确认并继续工作流" }).click();
  await firstConfirmationReceived;

  try {
    const persistedBeforeResponse = await page.evaluate((productId) => {
      const raw = sessionStorage.getItem(
        `commercevision.product-brief.v2:catalog-demo:${productId}`,
      );
      return raw ? JSON.parse(raw) : null;
    }, PRODUCT_ID);
    expect(persistedBeforeResponse).toMatchObject({
      schemaVersion: 3,
      workspaceId: "catalog-demo",
      productId: PRODUCT_ID,
      productBriefId: BRIEF_ID,
      pendingCommand: {
        schemaVersion: 1,
        kind: "confirm",
        productId: PRODUCT_ID,
        productBriefId: BRIEF_ID,
        payload: confirmationRequests[0].body,
        idempotencyKey: confirmationRequests[0].key,
      },
    });
  } finally {
    releaseFirstConfirmation();
  }
  await expect(page.getByRole("button", { name: "安全重试" })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "确认并继续工作流" }),
  ).toBeDisabled();

  await page.reload();
  await expect.poll(() => confirmationRequests).toHaveLength(2);
  expect(confirmationRequests[1]).toEqual(confirmationRequests[0]);
  expect(workflowReads).toBe(1);
  await expect(page.locator(".brief-state-CONFIRMED").first()).toContainText(
    "已确认",
  );

  const settledState = await page.evaluate((productId) => {
    const raw = sessionStorage.getItem(
      `commercevision.product-brief.v2:catalog-demo:${productId}`,
    );
    return raw ? JSON.parse(raw) : null;
  }, PRODUCT_ID);
  expect(settledState).toMatchObject({
    schemaVersion: 1,
    workspaceId: "catalog-demo",
    productId: PRODUCT_ID,
    productBriefId: BRIEF_ID,
  });
  expect(settledState).not.toHaveProperty("pendingCommand");
  expect(
    await page.evaluate(
      (productBId) =>
        JSON.parse(
          sessionStorage.getItem(
            `commercevision.product-brief.v2:catalog-demo:${productBId}`,
          ) ?? "null",
        ),
      PRODUCT_B_ID,
    ),
  ).toEqual(unrelatedPersistedState);
});

test("keeps a conflicted revision durable across a failed reload until the reviewer chooses", async ({
  page,
}) => {
  await routeCatalog(page);
  const staleEvidenceHash = "c".repeat(64);
  const currentEvidenceHash = "d".repeat(64);
  const staleEvidenceReference = `asset-region://${staleEvidenceHash}`;
  const currentEvidenceReference = `asset-region://${currentEvidenceHash}`;
  const modelVersion = version({
    id: MODEL_VERSION_ID,
    number: 1,
    fields: [
      field({
        id: "field-conflict-durable-brand",
        path: "common.brand",
        value: textValue("Northstar Labs"),
        evidenceHash: staleEvidenceHash,
      }),
    ],
  });
  const externalVersion = version({
    id: EXTERNAL_VERSION_ID,
    number: 2,
    source: "HUMAN",
    actor: "external-reviewer",
    reason: "External review",
    fields: [
      field({
        id: "field-conflict-durable-external-brand",
        path: "common.brand",
        value: textValue("External brand"),
        source: "HUMAN",
        evidenceHash: currentEvidenceHash,
      }),
    ],
  });
  let currentBrief = brief(modelVersion, 2);
  let history = [modelVersion];
  let failNextCurrentRead = false;
  const revisionRequests: Array<{
    body: Record<string, unknown>;
    key: string | undefined;
  }> = [];

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.startsWith("/api/v1/products")) {
      await route.fallback();
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}`) {
      if (failNextCurrentRead) {
        failNextCurrentRead = false;
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({
            code: "DEPENDENCY_UNAVAILABLE",
            category: "dependency",
            message: "Current ProductBrief is temporarily unavailable",
            retryable: true,
            trace_id: "trace-current-unavailable",
          }),
        });
        return;
      }
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(currentBrief),
      });
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}/versions`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: history, next_cursor: null }),
      });
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/${BRIEF_ID}/operations/${OPERATION_ID}`
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: OPERATION_ID,
          state: "WAITING_HUMAN",
          attempt_count: 1,
          max_attempts: 5,
          error: null,
        }),
      });
      return;
    }
    if (
      path === `/api/v1/product-briefs/${BRIEF_ID}:revise` &&
      request.method() === "POST"
    ) {
      revisionRequests.push({
        body: request.postDataJSON() as Record<string, unknown>,
        key: request.headers()["idempotency-key"],
      });
      if (revisionRequests.length === 1) {
        currentBrief = brief(externalVersion, 3);
        history = [externalVersion, modelVersion];
        failNextCurrentRead = true;
        await route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({
            code: "VERSION_CONFLICT",
            category: "conflict",
            message: "ProductBrief version is stale",
            retryable: false,
            trace_id: "trace-conflict-durable",
          }),
        });
      } else {
        await route.fulfill({
          contentType: "application/json",
          body: JSON.stringify(brief(externalVersion, 4)),
        });
      }
      return;
    }
    await route.fulfill({ status: 404, body: "not mocked" });
  });

  await page.addInitScript(
    ({
      productId,
      briefId,
      operationId,
      workflowId,
      assetVersionId,
    }) => {
      const key = `commercevision.product-brief.v2:catalog-demo:${productId}`;
      if (!sessionStorage.getItem(key)) {
        sessionStorage.setItem(
          key,
          JSON.stringify({
            schemaVersion: 1,
            workspaceId: "catalog-demo",
            productId,
            productBriefId: briefId,
            operationId,
            workflowId,
            assetVersionIds: [assetVersionId],
            retentionDeadline: "2099-01-01T00:00:00Z",
          }),
        );
      }
    },
    {
      productId: PRODUCT_ID,
      briefId: BRIEF_ID,
      operationId: OPERATION_ID,
      workflowId: WORKFLOW_ID,
      assetVersionId: ASSET_VERSION_ID,
    },
  );

  await page.goto("/");
  await expect(page.getByLabel("品牌值")).toHaveValue(
    editorValue(textValue("Northstar Labs")),
  );
  await page
    .getByLabel("品牌值")
    .fill(editorValue(textValue("Conflict-safe local draft")));
  await page
    .getByLabel("修订原因")
    .fill("Keep this exact draft until I choose");
  await page.getByRole("button", { name: "保存人工版本" }).click();

  await expect.poll(() => revisionRequests).toHaveLength(1);
  await expect(page.getByRole("button", { name: "安全重试" })).toBeVisible();
  const conflictedState = await page.evaluate((productId) => {
    const raw = sessionStorage.getItem(
      `commercevision.product-brief.v2:catalog-demo:${productId}`,
    );
    return raw ? JSON.parse(raw) : null;
  }, PRODUCT_ID);
  expect(conflictedState).toMatchObject({
    schemaVersion: 3,
    workspaceId: "catalog-demo",
    productId: PRODUCT_ID,
    productBriefId: BRIEF_ID,
    retentionDeadline: RETENTION_DEADLINE,
    commandStatus: "version-conflict",
    pendingCommand: {
      schemaVersion: 1,
      kind: "revise",
      productId: PRODUCT_ID,
      productBriefId: BRIEF_ID,
      payload: revisionRequests[0].body,
      idempotencyKey: revisionRequests[0].key,
    },
  });

  await page.reload();
  await expect(page.locator(".brief-stale-banner")).toBeVisible();
  expect(revisionRequests).toHaveLength(1);
  expect(
    await page.evaluate(
      (productId) =>
        JSON.parse(
          sessionStorage.getItem(
            `commercevision.product-brief.v2:catalog-demo:${productId}`,
          ) ?? "null",
        ),
      PRODUCT_ID,
    ),
  ).toEqual(conflictedState);

  await page.reload();
  await expect(page.locator(".brief-stale-banner")).toBeVisible();
  expect(revisionRequests).toHaveLength(1);
  const brandEvidence = page.getByLabel("品牌证据");
  await expect(brandEvidence).toContainText(currentEvidenceReference);
  await page.getByRole("button", { name: "恢复本地草稿" }).click();
  await expect(page.getByLabel("品牌值")).toHaveValue(
    editorValue(textValue("Conflict-safe local draft")),
  );
  await expect(page.getByLabel("修订原因")).toHaveValue(
    "Keep this exact draft until I choose",
  );
  await expect(brandEvidence).toContainText(staleEvidenceReference);
  await expect(brandEvidence).not.toContainText(currentEvidenceReference);
  expect(
    await page.evaluate(
      (productId) =>
        JSON.parse(
          sessionStorage.getItem(
            `commercevision.product-brief.v2:catalog-demo:${productId}`,
          ) ?? "null",
        ),
      PRODUCT_ID,
    ),
  ).toMatchObject({
    schemaVersion: 1,
    productBriefId: BRIEF_ID,
    retentionDeadline: RETENTION_DEADLINE,
  });

  await page.getByRole("button", { name: "保存人工版本" }).click();
  await expect.poll(() => revisionRequests).toHaveLength(2);
  expect(revisionRequests[1].body).toMatchObject({
    base_version_id: EXTERNAL_VERSION_ID,
    fields: [
      {
        path: "common.brand",
        evidence: [{ reference: staleEvidenceReference }],
      },
    ],
  });
});

test("clears an expired browser command without replaying retained task data", async ({
  page,
}) => {
  await routeCatalog(page);
  let productBriefRequests = 0;
  await page.route("**/api/v1/product-briefs**", async (route) => {
    productBriefRequests += 1;
    await route.fulfill({ status: 500, body: "expired command replayed" });
  });
  await page.addInitScript(
    ({
      productId,
      briefId,
      operationId,
      workflowId,
      assetVersionId,
    }) => {
      localStorage.setItem(
        `commercevision.product-brief.v2:catalog-demo:${productId}`,
        JSON.stringify({
          schemaVersion: 3,
          workspaceId: "catalog-demo",
          productId,
          productBriefId: briefId,
          operationId,
          workflowId,
          assetVersionIds: [assetVersionId],
          retentionDeadline: "2000-01-01T00:00:00Z",
          commandStatus: "pending",
          pendingCommand: {
            schemaVersion: 1,
            kind: "revise",
            productId,
            productBriefId: briefId,
            payload: {
              expected_product_brief_version: 2,
              base_version_id: "expired-version",
              reason: "expired-secret-draft-marker",
              fields: [
                {
                  path: "common.brand",
                  value: {
                    kind: "TEXT",
                    text: "expired-private-brand",
                  },
                  sensitive: false,
                  evidence: [],
                },
              ],
            },
            idempotencyKey: "expired-revision-command",
          },
        }),
      );
    },
    {
      productId: PRODUCT_ID,
      briefId: BRIEF_ID,
      operationId: OPERATION_ID,
      workflowId: WORKFLOW_ID,
      assetVersionId: ASSET_VERSION_ID,
    },
  );

  await page.goto("/");
  await expect
    .poll(() =>
      page.evaluate(
        (productId) =>
          localStorage.getItem(
            `commercevision.product-brief.v2:catalog-demo:${productId}`,
          ),
        PRODUCT_ID,
      ),
    )
    .toBeNull();
  expect(productBriefRequests).toBe(0);
  await expect(
    page.getByText("expired-secret-draft-marker"),
  ).toHaveCount(0);
  await expect(page.getByText("expired-private-brand")).toHaveCount(0);
});

test("expires an active ProductBrief in an idle tab at its exact deadline", async ({
  page,
}) => {
  const now = "2026-07-28T01:00:00.000Z";
  const deadline = "2026-07-28T01:00:01.000Z";
  await page.clock.install({ time: new Date(now) });
  await routeCatalog(page);
  const modelVersion = version({
    id: MODEL_VERSION_ID,
    number: 1,
    fields: [
      field({
        id: "field-idle-expiry-brand",
        path: "common.brand",
        value: textValue("Northstar Labs"),
      }),
    ],
  });
  const currentBrief = {
    ...brief(modelVersion, 2),
    retention_deadline: deadline,
  };
  let productBriefRequests = 0;
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.startsWith("/api/v1/products")) {
      await route.fallback();
      return;
    }
    productBriefRequests += 1;
    if (path === `/api/v1/product-briefs/${BRIEF_ID}`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(currentBrief),
      });
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}/versions`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          items: [modelVersion],
          next_cursor: null,
        }),
      });
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/${BRIEF_ID}/operations/${OPERATION_ID}`
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: OPERATION_ID,
          version: 1,
          state: "WAITING_HUMAN",
          attempt_count: 1,
          max_attempts: 5,
          error: null,
        }),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "not mocked" });
  });
  await page.addInitScript(
    ({ productId, briefId, operationId, workflowId, assetVersionId, deadline }) => {
      sessionStorage.setItem(
        `commercevision.product-brief.v2:catalog-demo:${productId}`,
        JSON.stringify({
          schemaVersion: 1,
          workspaceId: "catalog-demo",
          productId,
          productBriefId: briefId,
          operationId,
          workflowId,
          assetVersionIds: [assetVersionId],
          retentionDeadline: deadline,
        }),
      );
    },
    {
      productId: PRODUCT_ID,
      briefId: BRIEF_ID,
      operationId: OPERATION_ID,
      workflowId: WORKFLOW_ID,
      assetVersionId: ASSET_VERSION_ID,
      deadline,
    },
  );

  await page.goto("/");
  await expect(page.getByLabel("品牌值")).toHaveValue(
    editorValue(textValue("Northstar Labs")),
  );

  await page.clock.runFor(1_000);

  await expect(page.getByLabel("品牌值")).toHaveCount(0);
  await expect(
    page.getByText(
      "商品理解任务已到保留期限，本地恢复数据已清除。",
    ),
  ).toBeVisible();
  expect(
    await page.evaluate((productId) =>
      sessionStorage.getItem(
        `commercevision.product-brief.v2:catalog-demo:${productId}`,
      ),
    PRODUCT_ID),
  ).toBeNull();
  const requestsAtExpiry = productBriefRequests;
  await page.clock.runFor(10_000);
  expect(productBriefRequests).toBe(requestsAtExpiry);
});

for (const evidenceCount of [0, 33]) {
  test(`deletes a persisted revise command with ${evidenceCount} evidence items before fetching`, async ({
    page,
  }) => {
    await routeCatalog(page);
    let productBriefRequests = 0;
    await page.route("**/api/v1/product-briefs**", async (route) => {
      productBriefRequests += 1;
      await route.fulfill({
        status: 500,
        body: "invalid evidence command replayed",
      });
    });
    await page.addInitScript(
      ({
        productId,
        briefId,
        operationId,
        workflowId,
        assetVersionId,
        invalidEvidenceCount,
      }) => {
        const evidenceHash = "a".repeat(64);
        sessionStorage.setItem(
          `commercevision.product-brief.v2:catalog-demo:${productId}`,
          JSON.stringify({
            schemaVersion: 3,
            workspaceId: "catalog-demo",
            productId,
            productBriefId: briefId,
            operationId,
            workflowId,
            assetVersionIds: [assetVersionId],
            retentionDeadline: "2099-01-01T00:00:00Z",
            commandStatus: "pending",
            pendingCommand: {
              schemaVersion: 1,
              kind: "revise",
              productId,
              productBriefId: briefId,
              payload: {
                expected_product_brief_version: 2,
                base_version_id: "invalid-evidence-version",
                reason: "must not reach the network",
                fields: [
                  {
                    path: "common.brand",
                    value: { kind: "TEXT", text: "private draft" },
                    sensitive: false,
                    evidence: Array.from(
                      { length: invalidEvidenceCount },
                      () => ({
                        source_asset_version_id: assetVersionId,
                        kind: "IMAGE_REGION",
                        reference: `asset-region://${evidenceHash}`,
                        region: [0.1, 0.2, 0.8, 0.9],
                        excerpt_sha256: evidenceHash,
                      }),
                    ),
                  },
                ],
              },
              idempotencyKey: "invalid-evidence-revision",
            },
          }),
        );
      },
      {
        productId: PRODUCT_ID,
        briefId: BRIEF_ID,
        operationId: OPERATION_ID,
        workflowId: WORKFLOW_ID,
        assetVersionId: ASSET_VERSION_ID,
        invalidEvidenceCount: evidenceCount,
      },
    );

    await page.goto("/");
    await expect
      .poll(() =>
        page.evaluate(
          (productId) =>
            sessionStorage.getItem(
              `commercevision.product-brief.v2:catalog-demo:${productId}`,
            ),
          PRODUCT_ID,
        ),
      )
      .toBeNull();
    expect(productBriefRequests).toBe(0);
  });
}

test("deletes a corrupt confirmation conflict without replaying its POST", async ({
  page,
}) => {
  await routeCatalog(page);
  let productBriefRequests = 0;
  let productBriefPosts = 0;
  await page.route("**/api/v1/product-briefs**", async (route) => {
    productBriefRequests += 1;
    if (route.request().method() === "POST") productBriefPosts += 1;
    await route.fulfill({ status: 500, body: "corrupt command replayed" });
  });
  await page.addInitScript(
    ({
      productId,
      briefId,
      operationId,
      workflowId,
      assetVersionId,
      modelVersionId,
    }) => {
      sessionStorage.setItem(
        `commercevision.product-brief.v2:catalog-demo:${productId}`,
        JSON.stringify({
          schemaVersion: 3,
          workspaceId: "catalog-demo",
          productId,
          productBriefId: briefId,
          operationId,
          workflowId,
          assetVersionIds: [assetVersionId],
          retentionDeadline: "2099-01-01T00:00:00Z",
          commandStatus: "version-conflict",
          pendingCommand: {
            schemaVersion: 1,
            kind: "confirm",
            productId,
            productBriefId: briefId,
            payload: {
              expected_product_brief_version: 2,
              product_brief_version_id: modelVersionId,
              expected_workflow_version: 3,
              reason_code: "HUMAN_VERIFIED",
              comment_ref: "comment://must-not-replay",
            },
            idempotencyKey: "corrupt-confirmation-conflict",
          },
        }),
      );
    },
    {
      productId: PRODUCT_ID,
      briefId: BRIEF_ID,
      operationId: OPERATION_ID,
      workflowId: WORKFLOW_ID,
      assetVersionId: ASSET_VERSION_ID,
      modelVersionId: MODEL_VERSION_ID,
    },
  );

  await page.goto("/");
  await expect
    .poll(() =>
      page.evaluate(
        (productId) =>
          sessionStorage.getItem(
            `commercevision.product-brief.v2:catalog-demo:${productId}`,
          ),
        PRODUCT_ID,
      ),
    )
    .toBeNull();
  expect(productBriefRequests).toBe(0);
  expect(productBriefPosts).toBe(0);
});

test("reviews evidence, restores a stale draft, revises, confirms, and recovers after refresh", async ({
  page,
}) => {
  await routeCatalog(page);
  const modelFields = [
    field({
      id: "field-brand",
      path: "common.brand",
      value: textValue("Northstar Labs"),
      confidence: "0.4500",
      reviewReasons: ["LOW_CONFIDENCE", "MANDATORY_REVIEW"],
    }),
    field({
      id: "field-colors",
      path: "common.colors",
      value: textListValue(["blue", "white"]),
      conflict: "CONFLICTING",
      reviewReasons: ["SOURCE_CONFLICT"],
    }),
    field({
      id: "field-sensitive",
      path: "beauty.medical_like_claim_flags",
      value: flagListValue(["healing"]),
      sensitive: true,
      reviewReasons: ["SENSITIVE_CLAIM"],
    }),
  ];
  const modelVersion = version({
    id: MODEL_VERSION_ID,
    number: 1,
    fields: modelFields,
  });
  let currentBrief = brief(modelVersion, 2);
  let history = [modelVersion];
  let operationState = "WAITING_HUMAN";
  let workflowVersion = 4;
  let revisionAttempts = 0;
  let failHistoryAfterConflict = false;
  let failOperationAfterConfirm = false;
  let signalFirstRevision!: () => void;
  let releaseFirstRevision!: () => void;
  const firstRevisionReceived = new Promise<void>((resolve) => {
    signalFirstRevision = resolve;
  });
  const firstRevisionGate = new Promise<void>((resolve) => {
    releaseFirstRevision = resolve;
  });
  const revisionBodies: Array<Record<string, unknown>> = [];
  const confirmationBodies: Array<Record<string, unknown>> = [];

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path.startsWith("/api/v1/products")) {
      await route.fallback();
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/analysis-workflow-context/${WORKFLOW_ID}`
    ) {
      expect(url.search).toBe("");
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: WORKFLOW_ID,
          version: workflowVersion,
          retention_deadline: RETENTION_DEADLINE,
          status: "UNDERSTANDING",
        }),
      });
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/workflow-context/${WORKFLOW_ID}`
    ) {
      expect(url.search).toBe(`?product_brief_id=${BRIEF_ID}`);
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: WORKFLOW_ID,
          version: workflowVersion,
          retention_deadline: RETENTION_DEADLINE,
          status:
            currentBrief.state === "CONFIRMED"
              ? "RETRIEVING"
              : "AWAITING_PRODUCT_CONFIRMATION",
        }),
      });
      return;
    }
    if (path === "/api/v1/product-briefs:analyze" && request.method() === "POST") {
      expect(request.headers()["idempotency-key"]).toContain(
        "web-product-brief-analyze-",
      );
      expect(request.postDataJSON()).toMatchObject({
        workflow_id: WORKFLOW_ID,
        product_id: PRODUCT_ID,
        asset_version_ids: [ASSET_VERSION_ID],
        expected_workflow_version: workflowVersion,
      });
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({
          product_brief: currentBrief,
          operation_id: OPERATION_ID,
          operation_state: "PENDING",
        }),
      });
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(currentBrief),
      });
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}/versions`) {
      if (failHistoryAfterConflict) {
        failHistoryAfterConflict = false;
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({
            code: "DEPENDENCY_UNAVAILABLE",
            category: "dependency",
            message: "Version history is temporarily unavailable",
            retryable: true,
            trace_id: "trace-history-unavailable",
          }),
        });
        return;
      }
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: history, next_cursor: null }),
      });
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/${BRIEF_ID}/operations/${OPERATION_ID}`
    ) {
      if (failOperationAfterConfirm) {
        failOperationAfterConfirm = false;
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({
            code: "DEPENDENCY_UNAVAILABLE",
            category: "dependency",
            message: "Operation projection is temporarily unavailable",
            retryable: true,
            trace_id: "trace-operation-unavailable",
          }),
        });
        return;
      }
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: OPERATION_ID,
          state: operationState,
          attempt_count: 1,
          max_attempts: 5,
          error: null,
        }),
      });
      return;
    }
    if (
      path === `/api/v1/product-briefs/${BRIEF_ID}:revise` &&
      request.method() === "POST"
    ) {
      const body = request.postDataJSON() as Record<string, unknown>;
      revisionBodies.push(body);
      revisionAttempts += 1;
      if (revisionAttempts === 1) {
        const externalFields = modelFields.map((item) =>
          item.path === "common.brand"
            ? {
                ...item,
                id: "field-brand-external",
                value: textValue("External brand"),
              }
            : { ...item, id: `${item.id}-external` },
        );
        const externalVersion = version({
          id: EXTERNAL_VERSION_ID,
          number: 2,
          source: "HUMAN",
          actor: "external-reviewer",
          reason: "External review",
          fields: externalFields,
        });
        currentBrief = brief(externalVersion, 3);
        history = [externalVersion, modelVersion];
        failHistoryAfterConflict = true;
        signalFirstRevision();
        await firstRevisionGate;
        await route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({
            code: "VERSION_CONFLICT",
            category: "conflict",
            message: "ProductBrief version is stale",
            retryable: false,
            trace_id: "trace-stale",
          }),
        });
        return;
      }
      const requestFields = body.fields as Array<Record<string, unknown>>;
      const humanFields = requestFields.map((item, index) => ({
        id: `field-human-${index}`,
        path: String(item.path),
        value: item.value as ProductBriefFieldValueV1,
        confidence: String(item.confidence),
        source: "HUMAN",
        conflict: String(item.conflict),
        review_required: Boolean(item.review_required),
        sensitive: Boolean(item.sensitive),
        review_reasons: [],
        evidence: (item.evidence as Array<Record<string, unknown>>).map(
          (itemEvidence, evidenceIndex) => ({
            id: `evidence-human-${index}-${evidenceIndex}`,
            source_asset_version_id: String(
              itemEvidence.source_asset_version_id,
            ),
            kind: String(itemEvidence.kind),
            reference: String(itemEvidence.reference),
            region: itemEvidence.region as number[],
            excerpt_sha256: String(itemEvidence.excerpt_sha256),
          }),
        ),
      }));
      const humanVersion = version({
        id: HUMAN_VERSION_ID,
        number: 3,
        source: "HUMAN",
        actor: "catalog-workbench",
        reason: String(body.reason),
        fields: humanFields,
      });
      currentBrief = brief(humanVersion, 4);
      history = [humanVersion, ...history];
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(currentBrief),
      });
      return;
    }
    if (
      path === `/api/v1/product-briefs/${BRIEF_ID}:confirm` &&
      request.method() === "POST"
    ) {
      const body = request.postDataJSON() as Record<string, unknown>;
      confirmationBodies.push(body);
      workflowVersion += 1;
      operationState = "SUCCEEDED";
      failOperationAfterConfirm = true;
      currentBrief = {
        ...currentBrief,
        state: "CONFIRMED",
        confirmed_version_id: HUMAN_VERSION_ID,
        confirmed_version: {
          ...currentBrief.current_version,
          effective_state: "CONFIRMED",
        },
        current_version: {
          ...currentBrief.current_version,
          effective_state: "CONFIRMED",
        },
        version: currentBrief.version + 1,
      };
      history = [
        currentBrief.current_version as ReturnType<typeof version>,
        ...history.slice(1),
      ];
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          product_brief: currentBrief,
          workflow_id: WORKFLOW_ID,
          workflow_status: "RETRIEVING",
          workflow_version: workflowVersion,
          confirmation_id: "019f8a00-0000-7000-8000-000000000109",
        }),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "not mocked" });
  });

  await page.goto("/");
  await page.getByLabel("工作流 ID").fill(WORKFLOW_ID);
  await page.getByLabel("素材版本 ID").fill(ASSET_VERSION_ID);
  await page.getByRole("button", { name: "开始商品理解" }).click();

  await expect(page.getByText("低置信度", { exact: true })).toBeVisible();
  await expect(page.getByText("来源冲突", { exact: true }).first()).toBeVisible();
  await expect(
    page.getByText("强制人工复核", { exact: true }).first(),
  ).toBeVisible();
  await expect(page.getByText("敏感声明", { exact: true }).first()).toBeVisible();
  await expect(page.locator(".brief-evidence-list").first()).toContainText(
    ASSET_VERSION_ID,
  );
  await expect(page.getByRole("heading", { name: "美妆字段" })).toBeVisible();

  await page.reload();
  await expect(page.locator("[data-testid='brief-operation']")).toContainText(
    "等待人工确认",
  );
  await expect(page.getByLabel("品牌值")).toHaveValue(
    editorValue(textValue("Northstar Labs")),
  );

  await page
    .getByLabel("品牌值")
    .fill(editorValue(textValue("Northstar Verified")));
  await page.getByLabel("修订原因").fill("Verified against the package image");
  await expect(page.getByLabel("确认当前精确版本")).toBeDisabled();
  await page.getByRole("button", { name: "保存人工版本" }).click();
  await firstRevisionReceived;
  await expect(page.getByLabel("品牌值")).toBeDisabled();
  releaseFirstRevision();

  await expect(page.locator(".brief-stale-banner")).toBeVisible();
  await expect(page.locator(".brief-version-summary")).toContainText("2");
  await expect(page.getByLabel("品牌值")).toHaveValue(
    editorValue(textValue("External brand")),
  );
  await page.getByRole("button", { name: "恢复本地草稿" }).click();
  await expect(page.getByLabel("品牌值")).toHaveValue(
    editorValue(textValue("Northstar Verified")),
  );
  await page.getByRole("button", { name: "保存人工版本" }).click();

  await expect(page.locator(".brief-version-summary")).toContainText("3");
  expect(revisionBodies).toHaveLength(2);
  expect(revisionBodies[1]).toMatchObject({
    expected_product_brief_version: 3,
    base_version_id: EXTERNAL_VERSION_ID,
    reason: "Verified against the package image",
  });

  await page.getByLabel("确认当前精确版本").check();
  await page.getByRole("button", { name: "确认并继续工作流" }).click();

  await expect(page.locator(".brief-state-CONFIRMED").first()).toContainText(
    "已确认",
  );
  await expect(
    page.getByText("商品理解已更新，但任务状态暂不可用。"),
  ).toBeVisible();
  await page
    .locator(".product-brief-panel")
    .getByRole("button", { name: "刷新" })
    .click();
  await expect(page.locator("[data-testid='brief-operation']")).toContainText(
    "已完成",
  );
  expect(confirmationBodies).toEqual([
    expect.objectContaining({
      expected_product_brief_version: 4,
      product_brief_version_id: HUMAN_VERSION_ID,
      expected_workflow_version: 4,
    }),
  ]);
  await expect(page.locator(".brief-history li")).toHaveCount(3);

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator(".product-brief-panel")).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});

test("reopens a confirmed ProductBrief for an immutable human correction", async ({
  page,
}) => {
  await routeCatalog(page);
  const confirmedVersion = version({
    id: HUMAN_VERSION_ID,
    number: 3,
    source: "HUMAN",
    actor: "reviewer-1",
    reason: "Initial verification",
    fields: [
      field({
        id: "field-confirmed-brand",
        path: "common.brand",
        value: textValue("Northstar Verified"),
        source: "HUMAN",
      }),
    ],
  });
  const initial = confirmedBrief(confirmedVersion, 4);
  const reopenedVersion = {
    ...version({
      id: REOPENED_VERSION_ID,
      number: 4,
      source: "HUMAN",
      actor: "reviewer-2",
      reason: "Corrected after confirmation",
      fields: [
        field({
          id: "field-reopened-brand",
          path: "common.brand",
          value: textValue("Northstar Final"),
          source: "HUMAN",
        }),
      ],
    }),
    supersedes_version_id: HUMAN_VERSION_ID,
  };
  const reopened: BrowserBrief = {
    ...initial,
    operation_id: REOPENED_OPERATION_ID,
    state: "AWAITING_CONFIRMATION",
    current_version_id: REOPENED_VERSION_ID,
    current_version: reopenedVersion,
    version: 5,
  };
  let current: BrowserBrief = initial;
  let revisionBody: Record<string, unknown> | null = null;

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.startsWith("/api/v1/products")) {
      await route.fallback();
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(current),
      });
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}/versions`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          items: [current.current_version, confirmedVersion],
          next_cursor: null,
        }),
      });
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/${current.id}/operations/${current.operation_id}`
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: current.operation_id,
          state:
            current.operation_id === REOPENED_OPERATION_ID
              ? "WAITING_HUMAN"
              : "SUCCEEDED",
          attempt_count: 1,
          max_attempts: 1,
          error: null,
        }),
      });
      return;
    }
    if (
      path === `/api/v1/product-briefs/${BRIEF_ID}:revise` &&
      request.method() === "POST"
    ) {
      revisionBody = request.postDataJSON() as Record<string, unknown>;
      current = reopened;
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(reopened),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "not mocked" });
  });
  await page.addInitScript(
    ({
      productId,
      briefId,
      operationId,
      workflowId,
      assetVersionId,
    }) => {
      sessionStorage.setItem(
        `commercevision.product-brief.v2:catalog-demo:${productId}`,
        JSON.stringify({
          schemaVersion: 1,
          workspaceId: "catalog-demo",
          productId,
          productBriefId: briefId,
          operationId,
          workflowId,
          assetVersionIds: [assetVersionId],
          retentionDeadline: "2099-01-01T00:00:00Z",
        }),
      );
    },
    {
      productId: PRODUCT_ID,
      briefId: BRIEF_ID,
      operationId: OPERATION_ID,
      workflowId: WORKFLOW_ID,
      assetVersionId: ASSET_VERSION_ID,
    },
  );

  await page.goto("/");
  await expect(page.getByLabel("品牌值")).toBeEnabled();
  await expect(page.getByRole("button", { name: "重新分析商品" })).toBeVisible();
  await page
    .getByLabel("品牌值")
    .fill(editorValue(textValue("Northstar Final")));
  await page.getByLabel("修订原因").fill("Corrected after confirmation");
  await page.getByRole("button", { name: "保存人工版本" }).click();

  await expect(
    page.locator(".brief-state-AWAITING_CONFIRMATION").first(),
  ).toContainText("等待人工确认");
  expect(revisionBody).toMatchObject({
    expected_product_brief_version: 4,
    base_version_id: HUMAN_VERSION_ID,
    reason: "Corrected after confirmation",
  });
});

test("starts a new model analysis cycle from a confirmed ProductBrief", async ({
  page,
}) => {
  await routeCatalog(page);
  const confirmedVersion = version({
    id: HUMAN_VERSION_ID,
    number: 3,
    source: "HUMAN",
    actor: "reviewer-1",
    reason: "Initial verification",
    fields: [
      field({
        id: "field-reanalysis-brand",
        path: "common.brand",
        value: textValue("Northstar Verified"),
        source: "HUMAN",
      }),
    ],
  });
  const initial = confirmedBrief(confirmedVersion, 4);
  const draft: BrowserBrief = {
    ...initial,
    operation_id: REOPENED_OPERATION_ID,
    state: "DRAFT",
    version: 5,
  };
  let current: BrowserBrief = initial;
  let analysisBody: Record<string, unknown> | null = null;
  let signalAnalysisRequest!: () => void;
  let releaseAnalysisResponse!: () => void;
  const analysisRequestReceived = new Promise<void>((resolve) => {
    signalAnalysisRequest = resolve;
  });
  const analysisResponseGate = new Promise<void>((resolve) => {
    releaseAnalysisResponse = resolve;
  });

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path.startsWith("/api/v1/products")) {
      await route.fallback();
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/workflow-context/${WORKFLOW_ID}`
    ) {
      expect(url.search).toBe(`?product_brief_id=${BRIEF_ID}`);
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: WORKFLOW_ID,
          version: 7,
          retention_deadline: RETENTION_DEADLINE,
          status: "RETRIEVING",
        }),
      });
      return;
    }
    if (path === "/api/v1/product-briefs:analyze" && request.method() === "POST") {
      analysisBody = request.postDataJSON() as Record<string, unknown>;
      signalAnalysisRequest();
      await analysisResponseGate;
      current = draft;
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({
          product_brief: draft,
          operation_id: REOPENED_OPERATION_ID,
          operation_state: "PENDING",
        }),
      });
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(current),
      });
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}/versions`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [confirmedVersion], next_cursor: null }),
      });
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/${current.id}/operations/${current.operation_id}`
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: current.operation_id,
          state: current.operation_id === OPERATION_ID ? "SUCCEEDED" : "PENDING",
          attempt_count: current.operation_id === OPERATION_ID ? 1 : 0,
          max_attempts: 5,
          error: null,
        }),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "not mocked" });
  });
  await page.addInitScript(
    ({ productId, briefId, operationId, workflowId, assetVersionId }) => {
      sessionStorage.setItem(
        `commercevision.product-brief.v2:catalog-demo:${productId}`,
        JSON.stringify({
          schemaVersion: 1,
          workspaceId: "catalog-demo",
          productId,
          productBriefId: briefId,
          operationId,
          workflowId,
          assetVersionIds: [assetVersionId],
          retentionDeadline: "2099-01-01T00:00:00Z",
        }),
      );
    },
    {
      productId: PRODUCT_ID,
      briefId: BRIEF_ID,
      operationId: OPERATION_ID,
      workflowId: WORKFLOW_ID,
      assetVersionId: ASSET_VERSION_ID,
    },
  );

  await page.goto("/");
  await page.getByRole("button", { name: "重新分析商品" }).click();
  await analysisRequestReceived;
  try {
    const pending = await page.evaluate((productId) => {
      const raw = sessionStorage.getItem(
        `commercevision.product-brief.v2:catalog-demo:${productId}`,
      );
      return raw ? JSON.parse(raw) : null;
    }, PRODUCT_ID);
    expect(pending).toMatchObject({
      schemaVersion: 2,
      productId: PRODUCT_ID,
      workflowId: WORKFLOW_ID,
      pendingAnalysis: {
        priorProductBrief: {
          productBriefId: BRIEF_ID,
          operationId: OPERATION_ID,
        },
      },
    });
  } finally {
    releaseAnalysisResponse();
  }

  await expect(page.locator("[data-testid='brief-operation']")).toContainText(
    "等待执行",
  );
  expect(analysisBody).toEqual({
    workflow_id: WORKFLOW_ID,
    product_id: PRODUCT_ID,
    asset_version_ids: [ASSET_VERSION_ID],
    expected_workflow_version: 7,
  });
});

test("restores the established ProductBrief after a stable reanalysis rejection", async ({
  page,
}) => {
  await routeCatalog(page);
  const priorDeadline = "2098-12-31T23:00:00Z";
  const workflowDeadline = RETENTION_DEADLINE;
  const confirmedVersion = version({
    id: HUMAN_VERSION_ID,
    number: 3,
    source: "HUMAN",
    actor: "reviewer-1",
    reason: "Initial verification",
    fields: [
      field({
        id: "field-reanalysis-rejected-brand",
        path: "common.brand",
        value: textValue("Northstar Verified"),
        source: "HUMAN",
      }),
    ],
  });
  const current = {
    ...confirmedBrief(confirmedVersion, 4),
    retention_deadline: priorDeadline,
  };
  let analysisRequests = 0;

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path.startsWith("/api/v1/products")) {
      await route.fallback();
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/workflow-context/${WORKFLOW_ID}`
    ) {
      expect(url.search).toBe(`?product_brief_id=${BRIEF_ID}`);
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: WORKFLOW_ID,
          version: 7,
          retention_deadline: workflowDeadline,
          status: "RETRIEVING",
        }),
      });
      return;
    }
    if (
      path === "/api/v1/product-briefs:analyze" &&
      request.method() === "POST"
    ) {
      analysisRequests += 1;
      await route.fulfill({
        status: 403,
        contentType: "application/json",
        body: JSON.stringify({
          code: "PROVIDER_POLICY_DENIED",
          category: "permission",
          message: "Provider policy denied reanalysis",
          retryable: false,
          trace_id: "trace-reanalysis-rejected",
        }),
      });
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(current),
      });
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}/versions`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          items: [confirmedVersion],
          next_cursor: null,
        }),
      });
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/${BRIEF_ID}/operations/${OPERATION_ID}`
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: OPERATION_ID,
          state: "SUCCEEDED",
          attempt_count: 1,
          max_attempts: 5,
          error: null,
        }),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "not mocked" });
  });
  await page.addInitScript(
    ({
      productId,
      briefId,
      operationId,
      workflowId,
      assetVersionId,
      retentionDeadline,
    }) => {
      sessionStorage.setItem(
        `commercevision.product-brief.v2:catalog-demo:${productId}`,
        JSON.stringify({
          schemaVersion: 1,
          workspaceId: "catalog-demo",
          productId,
          productBriefId: briefId,
          operationId,
          workflowId,
          assetVersionIds: [assetVersionId],
          retentionDeadline,
        }),
      );
    },
    {
      productId: PRODUCT_ID,
      briefId: BRIEF_ID,
      operationId: OPERATION_ID,
      workflowId: WORKFLOW_ID,
      assetVersionId: ASSET_VERSION_ID,
      retentionDeadline: priorDeadline,
    },
  );

  await page.goto("/");
  await expect(page.getByRole("button", { name: "重新分析商品" })).toBeVisible();
  await page.getByRole("button", { name: "重新分析商品" }).click();
  await expect(
    page.locator(".product-brief-panel .error-banner"),
  ).toContainText("当前模型策略拒绝");

  const restored = await page.evaluate((productId) => {
    const raw = sessionStorage.getItem(
      `commercevision.product-brief.v2:catalog-demo:${productId}`,
    );
    return raw ? JSON.parse(raw) : null;
  }, PRODUCT_ID);
  expect(restored).toMatchObject({
    schemaVersion: 1,
    workspaceId: "catalog-demo",
    productId: PRODUCT_ID,
    productBriefId: BRIEF_ID,
    operationId: OPERATION_ID,
    retentionDeadline: priorDeadline,
  });
  expect(restored).not.toHaveProperty("pendingAnalysis");

  await page.reload();
  await expect(page.getByLabel("品牌值")).toHaveValue(
    editorValue(textValue("Northstar Verified")),
  );
  expect(analysisRequests).toBe(1);
});

test("keeps the exact pending reanalysis when 202 changes its brief or extends its deadline", async ({
  page,
}) => {
  await routeCatalog(page);
  const originalDeadline = "2099-01-01T00:00:00Z";
  const extendedDeadline = "2099-02-01T00:00:00Z";
  const confirmedVersion = version({
    id: HUMAN_VERSION_ID,
    number: 3,
    source: "HUMAN",
    actor: "reviewer-1",
    reason: "Initial verification",
    fields: [
      field({
        id: "field-reanalysis-drift-brand",
        path: "common.brand",
        value: textValue("Northstar Verified"),
        source: "HUMAN",
      }),
    ],
  });
  const current = {
    ...confirmedBrief(confirmedVersion, 4),
    retention_deadline: originalDeadline,
  };
  const draft: BrowserBrief = {
    ...current,
    operation_id: REOPENED_OPERATION_ID,
    state: "DRAFT",
    version: 5,
  };
  let analysisRequests = 0;

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path.startsWith("/api/v1/products")) {
      await route.fallback();
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/workflow-context/${WORKFLOW_ID}`
    ) {
      expect(url.search).toBe(`?product_brief_id=${BRIEF_ID}`);
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: WORKFLOW_ID,
          version: 7,
          retention_deadline: originalDeadline,
          status: "RETRIEVING",
        }),
      });
      return;
    }
    if (
      path === "/api/v1/product-briefs:analyze" &&
      request.method() === "POST"
    ) {
      analysisRequests += 1;
      const responseBrief =
        analysisRequests === 1
          ? { ...draft, id: NEXT_BRIEF_ID }
          : { ...draft, retention_deadline: extendedDeadline };
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({
          product_brief: responseBrief,
          operation_id: REOPENED_OPERATION_ID,
          operation_state: "PENDING",
        }),
      });
      return;
    }
    if (
      path === `/api/v1/product-briefs/${BRIEF_ID}` ||
      path === `/api/v1/product-briefs/${NEXT_BRIEF_ID}`
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(
          path.endsWith(NEXT_BRIEF_ID)
            ? { ...draft, id: NEXT_BRIEF_ID }
            : current,
        ),
      });
      return;
    }
    if (
      path === `/api/v1/product-briefs/${BRIEF_ID}/versions` ||
      path === `/api/v1/product-briefs/${NEXT_BRIEF_ID}/versions`
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          items: [confirmedVersion],
          next_cursor: null,
        }),
      });
      return;
    }
    if (path.includes("/operations/")) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: path.endsWith(REOPENED_OPERATION_ID)
            ? REOPENED_OPERATION_ID
            : OPERATION_ID,
          state: "PENDING",
          attempt_count: 0,
          max_attempts: 5,
          error: null,
        }),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "not mocked" });
  });
  await page.addInitScript(
    ({
      productId,
      briefId,
      operationId,
      workflowId,
      assetVersionId,
      retentionDeadline,
    }) => {
      sessionStorage.setItem(
        `commercevision.product-brief.v2:catalog-demo:${productId}`,
        JSON.stringify({
          schemaVersion: 1,
          workspaceId: "catalog-demo",
          productId,
          productBriefId: briefId,
          operationId,
          workflowId,
          assetVersionIds: [assetVersionId],
          retentionDeadline,
        }),
      );
    },
    {
      productId: PRODUCT_ID,
      briefId: BRIEF_ID,
      operationId: OPERATION_ID,
      workflowId: WORKFLOW_ID,
      assetVersionId: ASSET_VERSION_ID,
      retentionDeadline: originalDeadline,
    },
  );

  await page.goto("/");
  await expect(page.getByRole("button", { name: "重新分析商品" })).toBeVisible();
  await page.getByRole("button", { name: "重新分析商品" }).click();
  await expect(
    page.locator(".product-brief-panel .error-banner"),
  ).toContainText("分析响应身份不匹配");
  await expect(page.getByRole("button", { name: "安全重试" })).toBeVisible();

  const readPending = () =>
    page.evaluate((productId) => {
      const raw = sessionStorage.getItem(
        `commercevision.product-brief.v2:catalog-demo:${productId}`,
      );
      return raw ? JSON.parse(raw) : null;
    }, PRODUCT_ID);
  const identityMismatchPending = await readPending();
  expect(identityMismatchPending).toMatchObject({
    schemaVersion: 2,
    retentionDeadline: originalDeadline,
    pendingAnalysis: {
      priorProductBrief: {
        productBriefId: BRIEF_ID,
        operationId: OPERATION_ID,
      },
    },
  });

  await page.getByRole("button", { name: "安全重试" }).click();
  await expect
    .poll(() => analysisRequests)
    .toBe(2);
  await expect(
    page.locator(".product-brief-panel .error-banner"),
  ).toContainText("保留期限");
  await expect(page.getByRole("button", { name: "安全重试" })).toBeVisible();
  expect(await readPending()).toEqual(identityMismatchPending);
});

test("keeps one bounded polling budget while operation states alternate", async ({
  page,
}) => {
  await page.clock.install({ time: new Date("2026-07-28T01:00:00Z") });
  await routeCatalog(page);
  const modelVersion = version({
    id: MODEL_VERSION_ID,
    number: 1,
    fields: [
      field({
        id: "field-polling-brand",
        path: "common.brand",
        value: textValue("Northstar Labs"),
      }),
    ],
  });
  const currentBrief = brief(modelVersion, 2);
  let operationRequests = 0;

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.startsWith("/api/v1/products")) {
      await route.fallback();
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/analysis-workflow-context/${WORKFLOW_ID}`
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: WORKFLOW_ID,
          status: "UNDERSTANDING",
          version: 3,
          retention_deadline: RETENTION_DEADLINE,
        }),
      });
      return;
    }
    if (path === "/api/v1/product-briefs:analyze" && request.method() === "POST") {
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({
          product_brief: currentBrief,
          operation_id: OPERATION_ID,
          operation_state: "PENDING",
        }),
      });
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(currentBrief),
      });
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}/versions`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [modelVersion], next_cursor: null }),
      });
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/${BRIEF_ID}/operations/${OPERATION_ID}`
    ) {
      operationRequests += 1;
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: OPERATION_ID,
          state: operationRequests % 2 === 0 ? "RUNNING" : "PENDING",
          attempt_count: 1,
          max_attempts: 5,
          error: null,
        }),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "not mocked" });
  });

  await page.goto("/");
  await page.getByLabel("工作流 ID").fill(WORKFLOW_ID);
  await page.getByLabel("素材版本 ID").fill(ASSET_VERSION_ID);
  await page.getByRole("button", { name: "开始商品理解" }).click();
  await expect(page.locator("[data-testid='brief-operation']")).toBeVisible();
  await expect.poll(() => operationRequests).toBeGreaterThanOrEqual(2);
  await new Promise((resolve) => setTimeout(resolve, 100));

  await exhaustAutomaticPolling(page, () => operationRequests);
  await expect(page.getByRole("button", { name: "继续刷新" })).toBeVisible();
  expect(operationRequests).toBeGreaterThanOrEqual(24);
  expect(operationRequests).toBeLessThanOrEqual(26);

  const requestsAtPause = operationRequests;
  await page.clock.runFor(60_000);
  expect(operationRequests).toBe(requestsAtPause);
});

test("starts polling with a fresh budget when a loaded brief has a new operation", async ({
  page,
}) => {
  await page.clock.install({ time: new Date("2026-07-28T01:00:00Z") });
  await routeCatalog(page);
  const modelVersion = version({
    id: MODEL_VERSION_ID,
    number: 1,
    fields: [
      field({
        id: "field-operation-scope-brand",
        path: "common.brand",
        value: textValue("Northstar Labs"),
      }),
    ],
  });
  const initialBrief = brief(modelVersion, 2);
  const nextVersion = {
    ...modelVersion,
    id: NEXT_VERSION_ID,
    product_brief_id: NEXT_BRIEF_ID,
    version_number: 2,
  };
  const nextBrief = {
    ...brief(nextVersion, 3),
    id: NEXT_BRIEF_ID,
    operation_id: NEXT_OPERATION_ID,
    current_version_id: NEXT_VERSION_ID,
    current_version: nextVersion,
  };
  let initialOperationRequests = 0;
  let nextOperationRequests = 0;

  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.startsWith("/api/v1/products")) {
      await route.fallback();
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(initialBrief),
      });
      return;
    }
    if (path === `/api/v1/product-briefs/${NEXT_BRIEF_ID}`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(nextBrief),
      });
      return;
    }
    if (
      path === `/api/v1/product-briefs/${BRIEF_ID}/versions` ||
      path === `/api/v1/product-briefs/${NEXT_BRIEF_ID}/versions`
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          items: [
            path.includes(NEXT_BRIEF_ID) ? nextVersion : modelVersion,
          ],
        }),
      });
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/${BRIEF_ID}/operations/${OPERATION_ID}`
    ) {
      initialOperationRequests += 1;
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: OPERATION_ID,
          version: initialOperationRequests,
          state:
            initialOperationRequests % 2 === 0 ? "RUNNING" : "PENDING",
          attempt_count: 1,
          max_attempts: 5,
          error: null,
        }),
      });
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/${NEXT_BRIEF_ID}/operations/${NEXT_OPERATION_ID}`
    ) {
      nextOperationRequests += 1;
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: NEXT_OPERATION_ID,
          version: nextOperationRequests,
          state: nextOperationRequests === 1 ? "RUNNING" : "SUCCEEDED",
          attempt_count: 1,
          max_attempts: 5,
          error: null,
        }),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "not mocked" });
  });

  await page.addInitScript(
    ({ productId, briefId, operationId, workflowId, assetVersionId }) => {
      sessionStorage.setItem(
        `commercevision.product-brief.v2:catalog-demo:${productId}`,
        JSON.stringify({
          schemaVersion: 1,
          workspaceId: "catalog-demo",
          productId,
          productBriefId: briefId,
          operationId,
          workflowId,
          assetVersionIds: [assetVersionId],
          retentionDeadline: "2099-01-01T00:00:00Z",
        }),
      );
    },
    {
      productId: PRODUCT_ID,
      briefId: BRIEF_ID,
      operationId: OPERATION_ID,
      workflowId: WORKFLOW_ID,
      assetVersionId: ASSET_VERSION_ID,
    },
  );

  await page.goto("/");
  await expect(page.locator("[data-testid='brief-operation']")).toBeVisible();
  await expect
    .poll(() => initialOperationRequests)
    .toBeGreaterThanOrEqual(2);
  await new Promise((resolve) => setTimeout(resolve, 100));
  await exhaustAutomaticPolling(page, () => initialOperationRequests);
  await expect(page.getByRole("button", { name: "继续刷新" })).toBeVisible();

  await page.getByLabel("商品理解 ID").fill(NEXT_BRIEF_ID);
  await page.getByRole("button", { name: "载入", exact: true }).click();

  await expect
    .poll(() => nextOperationRequests)
    .toBeGreaterThanOrEqual(2);
  await expect(page.locator("[data-testid='brief-operation']")).toContainText(
    "已完成",
  );
});

test("keeps the authoritative operation version across concurrent reads", async ({
  page,
}) => {
  await page.clock.install({ time: new Date("2026-07-28T01:00:00Z") });
  await routeCatalog(page);
  const modelVersion = version({
    id: MODEL_VERSION_ID,
    number: 1,
    fields: [
      field({
        id: "field-operation-order-brand",
        path: "common.brand",
        value: textValue("Northstar Labs"),
      }),
    ],
  });
  const currentBrief = brief(modelVersion, 2);
  let operationRequests = 0;
  let serveStaleVersion = false;
  let signalOlderReadStarted!: () => void;
  let signalOlderReadCompleted!: () => void;
  let releaseOlderRead!: () => void;
  const olderReadStarted = new Promise<void>((resolve) => {
    signalOlderReadStarted = resolve;
  });
  const olderReadCompleted = new Promise<void>((resolve) => {
    signalOlderReadCompleted = resolve;
  });
  const olderReadGate = new Promise<void>((resolve) => {
    releaseOlderRead = resolve;
  });

  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.startsWith("/api/v1/products")) {
      await route.fallback();
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(currentBrief),
      });
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}/versions`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [modelVersion], next_cursor: null }),
      });
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/${BRIEF_ID}/operations/${OPERATION_ID}`
    ) {
      const requestNumber = ++operationRequests;
      if (requestNumber === 1) {
        signalOlderReadStarted();
        await olderReadGate;
      }
      const operationResponse =
        requestNumber === 1
          ? {
              id: OPERATION_ID,
              version: 1,
              state: "PENDING",
              attempt_count: 0,
              max_attempts: 5,
              error: null,
            }
          : serveStaleVersion
            ? {
                id: OPERATION_ID,
                version: 2,
                state: "PENDING",
                attempt_count: 1,
                max_attempts: 5,
                error: null,
              }
            : requestNumber === 2
              ? {
                  id: OPERATION_ID,
                  version: 2,
                  state: "RUNNING",
                  attempt_count: 2,
                  max_attempts: 5,
                  error: null,
                }
              : {
                  id: OPERATION_ID,
                  version: 3,
                  state: "SUCCEEDED",
                  attempt_count: 3,
                  max_attempts: 5,
                  error: null,
                };
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(operationResponse),
      });
      if (requestNumber === 1) signalOlderReadCompleted();
      return;
    }
    await route.fulfill({ status: 404, body: "not mocked" });
  });

  await page.addInitScript(
    ({ productId, briefId, operationId, workflowId, assetVersionId }) => {
      sessionStorage.setItem(
        `commercevision.product-brief.v2:catalog-demo:${productId}`,
        JSON.stringify({
          schemaVersion: 1,
          workspaceId: "catalog-demo",
          productId,
          productBriefId: briefId,
          operationId,
          workflowId,
          assetVersionIds: [assetVersionId],
          retentionDeadline: "2099-01-01T00:00:00Z",
        }),
      );
    },
    {
      productId: PRODUCT_ID,
      briefId: BRIEF_ID,
      operationId: OPERATION_ID,
      workflowId: WORKFLOW_ID,
      assetVersionId: ASSET_VERSION_ID,
    },
  );

  await page.goto("/");
  await olderReadStarted;
  await expect.poll(() => operationRequests).toBeGreaterThanOrEqual(2);
  await expect(page.locator("[data-testid='brief-operation']")).toContainText(
    "分析中",
  );
  await expect(page.locator("[data-testid='brief-operation']")).toContainText(
    "2 / 5",
  );
  const currentTime = await page.evaluate(() => Date.now());
  await page.clock.pauseAt(currentTime + 100);

  releaseOlderRead();
  await olderReadCompleted;
  await new Promise((resolve) => setTimeout(resolve, 100));

  await expect(page.locator("[data-testid='brief-operation']")).toContainText(
    "分析中",
  );
  await expect(page.locator("[data-testid='brief-operation']")).toContainText(
    "2 / 5",
  );

  await page.clock.runFor(2_000);
  await expect(page.locator("[data-testid='brief-operation']")).toContainText(
    "已完成",
  );
  await expect(page.locator("[data-testid='brief-operation']")).toContainText(
    "3 / 5",
  );
  await expect.poll(() => operationRequests).toBeGreaterThanOrEqual(4);

  serveStaleVersion = true;
  const requestsBeforeStaleRefresh = operationRequests;
  await page
    .locator(".product-brief-panel")
    .getByRole("button", { name: "刷新" })
    .click();
  await expect
    .poll(() => operationRequests)
    .toBeGreaterThan(requestsBeforeStaleRefresh);
  await new Promise((resolve) => setTimeout(resolve, 100));

  await expect(page.locator("[data-testid='brief-operation']")).toContainText(
    "已完成",
  );
  await expect(page.locator("[data-testid='brief-operation']")).toContainText(
    "3 / 5",
  );
});

test("does not let a late ProductBrief N response replace N+1", async ({
  page,
}) => {
  await routeCatalog(page);
  const versionN = version({
    id: MODEL_VERSION_ID,
    number: 1,
    fields: [
      field({
        id: "field-brief-n-brand",
        path: "common.brand",
        value: textValue("Version N"),
      }),
    ],
  });
  const versionNPlusOne = version({
    id: EXTERNAL_VERSION_ID,
    number: 2,
    source: "HUMAN",
    fields: [
      field({
        id: "field-brief-n-plus-one-brand",
        path: "common.brand",
        value: textValue("Version N+1"),
        source: "HUMAN",
      }),
    ],
  });
  const briefN = brief(versionN, 2);
  const briefNPlusOne = brief(versionNPlusOne, 3);
  let briefReads = 0;
  let operationReads = 0;
  let signalOlderRefreshStarted!: () => void;
  let releaseOlderRefresh!: () => void;
  const olderRefreshStarted = new Promise<void>((resolve) => {
    signalOlderRefreshStarted = resolve;
  });
  const olderRefreshGate = new Promise<void>((resolve) => {
    releaseOlderRefresh = resolve;
  });

  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.startsWith("/api/v1/products")) {
      await route.fallback();
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}`) {
      briefReads += 1;
      if (briefReads === 2) {
        signalOlderRefreshStarted();
        await olderRefreshGate;
        await route.fulfill({
          contentType: "application/json",
          body: JSON.stringify(briefN),
        });
        return;
      }
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(briefReads === 1 ? briefN : briefNPlusOne),
      });
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}/versions`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          items:
            briefReads >= 3 ? [versionNPlusOne, versionN] : [versionN],
        }),
      });
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/${BRIEF_ID}/operations/${OPERATION_ID}`
    ) {
      operationReads += 1;
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: OPERATION_ID,
          version: operationReads,
          state: operationReads === 1 ? "RUNNING" : "SUCCEEDED",
          attempt_count: operationReads,
          max_attempts: 5,
          error: null,
        }),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "not mocked" });
  });

  await page.addInitScript(
    ({ productId, briefId, operationId, workflowId, assetVersionId }) => {
      sessionStorage.setItem(
        `commercevision.product-brief.v2:catalog-demo:${productId}`,
        JSON.stringify({
          schemaVersion: 1,
          workspaceId: "catalog-demo",
          productId,
          productBriefId: briefId,
          operationId,
          workflowId,
          assetVersionIds: [assetVersionId],
          retentionDeadline: "2099-01-01T00:00:00Z",
        }),
      );
    },
    {
      productId: PRODUCT_ID,
      briefId: BRIEF_ID,
      operationId: OPERATION_ID,
      workflowId: WORKFLOW_ID,
      assetVersionId: ASSET_VERSION_ID,
    },
  );

  await page.goto("/");
  await olderRefreshStarted;
  await page
    .locator(".product-brief-panel")
    .getByRole("button", { name: "刷新" })
    .click();
  await expect(page.getByLabel("品牌值")).toHaveValue(
    editorValue(textValue("Version N+1")),
  );
  await expect(page.locator(".brief-version-summary")).toContainText("2");

  releaseOlderRefresh();
  await new Promise((resolve) => setTimeout(resolve, 100));

  await expect(page.getByLabel("品牌值")).toHaveValue(
    editorValue(textValue("Version N+1")),
  );
  await expect(page.locator(".brief-version-summary")).toContainText("2");
  await expect(page.locator(".brief-history li")).toHaveCount(2);
});

test("does not let an older auxiliary failure clear fresh history or warnings", async ({
  page,
}) => {
  await routeCatalog(page);
  const versionN = version({
    id: MODEL_VERSION_ID,
    number: 1,
    fields: [
      field({
        id: "field-aux-n-brand",
        path: "common.brand",
        value: textValue("Version N"),
      }),
    ],
  });
  const versionNPlusOne = version({
    id: EXTERNAL_VERSION_ID,
    number: 2,
    source: "HUMAN",
    fields: [
      field({
        id: "field-aux-n-plus-one-brand",
        path: "common.brand",
        value: textValue("Version N+1"),
        source: "HUMAN",
      }),
    ],
  });
  const currentBrief = brief(versionNPlusOne, 3);
  let historyReads = 0;
  let signalOldHistoryStarted!: () => void;
  let releaseOldHistory!: () => void;
  const oldHistoryStarted = new Promise<void>((resolve) => {
    signalOldHistoryStarted = resolve;
  });
  const oldHistoryGate = new Promise<void>((resolve) => {
    releaseOldHistory = resolve;
  });

  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.startsWith("/api/v1/products")) {
      await route.fallback();
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(currentBrief),
      });
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}/versions`) {
      historyReads += 1;
      if (historyReads === 1) {
        signalOldHistoryStarted();
        await oldHistoryGate;
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({
            code: "DEPENDENCY_UNAVAILABLE",
            category: "dependency",
            message: "old history read failed",
            retryable: true,
            trace_id: "trace-old-history",
          }),
        });
        return;
      }
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          items: [versionNPlusOne, versionN],
          next_cursor: null,
        }),
      });
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/${BRIEF_ID}/operations/${OPERATION_ID}`
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: OPERATION_ID,
          version: 2,
          state: "SUCCEEDED",
          attempt_count: 1,
          max_attempts: 5,
          error: null,
        }),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "not mocked" });
  });

  await page.addInitScript(
    ({ productId, briefId, operationId, workflowId, assetVersionId }) => {
      sessionStorage.setItem(
        `commercevision.product-brief.v2:catalog-demo:${productId}`,
        JSON.stringify({
          schemaVersion: 1,
          workspaceId: "catalog-demo",
          productId,
          productBriefId: briefId,
          operationId,
          workflowId,
          assetVersionIds: [assetVersionId],
          retentionDeadline: "2099-01-01T00:00:00Z",
        }),
      );
    },
    {
      productId: PRODUCT_ID,
      briefId: BRIEF_ID,
      operationId: OPERATION_ID,
      workflowId: WORKFLOW_ID,
      assetVersionId: ASSET_VERSION_ID,
    },
  );

  await page.goto("/");
  await oldHistoryStarted;
  await expect.poll(() => historyReads).toBeGreaterThanOrEqual(2);
  await expect(page.locator(".brief-history li")).toHaveCount(2);
  await expect(
    page.getByText("商品理解已更新，但版本历史暂不可用。"),
  ).toHaveCount(0);

  releaseOldHistory();
  await new Promise((resolve) => setTimeout(resolve, 100));

  await expect(page.locator(".brief-history li")).toHaveCount(2);
  await expect(
    page.getByText("商品理解已更新，但版本历史暂不可用。"),
  ).toHaveCount(0);
});

test("loads more than twenty immutable ProductBrief versions by cursor", async ({
  page,
}) => {
  await routeCatalog(page);
  const history = Array.from({ length: 25 }, (_, index) => {
    const number = 25 - index;
    return {
      ...version({
        id: `history-version-${number}`,
        number,
        fields: [
          field({
            id: `history-field-${number}`,
            path: "common.brand",
            value: textValue(`Northstar ${number}`),
          }),
        ],
      }),
      created_at: new Date(
        Date.UTC(2026, 6, 28, 1, number),
      ).toISOString(),
      payload_sha256: String(number % 10).repeat(64),
    };
  });
  const currentBrief = brief(history[0], 26);
  const historyCursors: Array<string | null> = [];
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path.startsWith("/api/v1/products")) {
      await route.fallback();
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(currentBrief),
      });
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}/versions`) {
      expect(url.searchParams.get("limit")).toBe("20");
      const cursor = url.searchParams.get("cursor");
      historyCursors.push(cursor);
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(
          cursor === null
            ? { items: history.slice(0, 20), next_cursor: 5 }
            : { items: history.slice(20), next_cursor: null },
        ),
      });
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/${BRIEF_ID}/operations/${OPERATION_ID}`
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: OPERATION_ID,
          version: 1,
          state: "WAITING_HUMAN",
          attempt_count: 1,
          max_attempts: 5,
          error: null,
        }),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "not mocked" });
  });
  await page.addInitScript(
    ({ productId, briefId, operationId, workflowId, assetVersionId }) => {
      sessionStorage.setItem(
        `commercevision.product-brief.v2:catalog-demo:${productId}`,
        JSON.stringify({
          schemaVersion: 1,
          workspaceId: "catalog-demo",
          productId,
          productBriefId: briefId,
          operationId,
          workflowId,
          assetVersionIds: [assetVersionId],
          retentionDeadline: "2099-01-01T00:00:00Z",
        }),
      );
    },
    {
      productId: PRODUCT_ID,
      briefId: BRIEF_ID,
      operationId: OPERATION_ID,
      workflowId: WORKFLOW_ID,
      assetVersionId: ASSET_VERSION_ID,
    },
  );

  await page.goto("/");
  const historyItems = page.locator(".brief-history > li");
  await expect(historyItems).toHaveCount(20);
  const loadMore = page.getByRole("button", {
    name: "载入更多版本",
  });
  await expect(loadMore).toHaveAttribute(
    "aria-controls",
    "brief-version-history",
  );

  await loadMore.click();

  await expect(historyItems).toHaveCount(25);
  await expect(loadMore).toHaveCount(0);
  expect(historyCursors.at(-1)).toBe("5");
  expect(historyCursors.filter((cursor) => cursor === "5")).toHaveLength(
    1,
  );
  expect(
    historyCursors
      .slice(0, -1)
      .every((cursor) => cursor === null),
  ).toBe(true);
});

test("validates structured fields per field and focuses the first invalid field", async ({
  page,
}) => {
  await routeCatalog(page);
  const structuredVersion = version({
    id: MODEL_VERSION_ID,
    number: 1,
    fields: [
      field({
        id: "field-a11y-colors",
        path: "common.colors",
        value: textListValue(["blue", "white"]),
      }),
      field({
        id: "field-a11y-claims",
        path: "beauty.medical_like_claim_flags",
        value: flagListValue(["healing"]),
      }),
    ],
  });
  const currentBrief = brief(structuredVersion, 2);
  let revisionRequests = 0;

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.startsWith("/api/v1/products")) {
      await route.fallback();
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(currentBrief),
      });
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}/versions`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [structuredVersion], next_cursor: null }),
      });
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/${BRIEF_ID}/operations/${OPERATION_ID}`
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: OPERATION_ID,
          version: 1,
          state: "WAITING_HUMAN",
          attempt_count: 1,
          max_attempts: 5,
          error: null,
        }),
      });
      return;
    }
    if (
      path === `/api/v1/product-briefs/${BRIEF_ID}:revise` &&
      request.method() === "POST"
    ) {
      revisionRequests += 1;
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(currentBrief),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "not mocked" });
  });

  await page.addInitScript(
    ({ productId, briefId, operationId, workflowId, assetVersionId }) => {
      sessionStorage.setItem(
        `commercevision.product-brief.v2:catalog-demo:${productId}`,
        JSON.stringify({
          schemaVersion: 1,
          workspaceId: "catalog-demo",
          productId,
          productBriefId: briefId,
          operationId,
          workflowId,
          assetVersionIds: [assetVersionId],
          retentionDeadline: "2099-01-01T00:00:00Z",
        }),
      );
    },
    {
      productId: PRODUCT_ID,
      briefId: BRIEF_ID,
      operationId: OPERATION_ID,
      workflowId: WORKFLOW_ID,
      assetVersionId: ASSET_VERSION_ID,
    },
  );

  await page.goto("/");
  const colors = page.getByLabel("颜色值");
  const claims = page.getByLabel("医疗类声明值");
  await expect(colors).toBeEditable();
  await colors.fill('{"kind":"TEXT_LIST","items":["blue",');
  await claims.fill('{"kind":"FLAG_LIST","flags":[true]}');

  await expect(colors).toHaveAttribute("aria-invalid", "true");
  await expect(claims).toHaveAttribute("aria-invalid", "true");
  const colorsErrorId = await colors.getAttribute("aria-describedby");
  const claimsErrorId = await claims.getAttribute("aria-describedby");
  expect(colorsErrorId).toBeTruthy();
  expect(claimsErrorId).toBeTruthy();
  expect(colorsErrorId).not.toBe(claimsErrorId);
  await expect(page.locator(`#${colorsErrorId}`)).toHaveAttribute(
    "role",
    "alert",
  );
  await expect(page.locator(`#${claimsErrorId}`)).toHaveText(
    "字段值不符合当前 ProductBrief 字段契约。",
  );

  await page.getByLabel("修订原因").fill("Keyboard accessibility review");
  const save = page.getByRole("button", { name: "保存人工版本" });
  await save.focus();
  await page.keyboard.press("Enter");
  await expect(colors).toBeFocused();
  expect(revisionRequests).toBe(0);

  await colors.fill(editorValue(textListValue(["blue", "white"])));
  await expect(colors).toHaveAttribute("aria-invalid", "false");
  await save.focus();
  await page.keyboard.press("Enter");
  await expect(claims).toBeFocused();
  expect(revisionRequests).toBe(0);

  await claims.fill(editorValue(flagListValue(["healing"])));
  await expect(claims).toHaveAttribute("aria-invalid", "false");
  await save.focus();
  await page.keyboard.press("Enter");
  await expect.poll(() => revisionRequests).toBe(1);
});

test("announces persisted ProductBrief recovery as a visible status", async ({
  page,
}) => {
  await routeCatalog(page);
  const modelVersion = version({
    id: MODEL_VERSION_ID,
    number: 1,
    fields: [
      field({
        id: "field-recovery-status-brand",
        path: "common.brand",
        value: textValue("Northstar Labs"),
      }),
    ],
  });
  const currentBrief = brief(modelVersion, 2);
  let signalBriefReadStarted!: () => void;
  let releaseBriefRead!: () => void;
  const briefReadStarted = new Promise<void>((resolve) => {
    signalBriefReadStarted = resolve;
  });
  const briefReadGate = new Promise<void>((resolve) => {
    releaseBriefRead = resolve;
  });

  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.startsWith("/api/v1/products")) {
      await route.fallback();
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}`) {
      signalBriefReadStarted();
      await briefReadGate;
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(currentBrief),
      });
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}/versions`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [modelVersion], next_cursor: null }),
      });
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/${BRIEF_ID}/operations/${OPERATION_ID}`
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: OPERATION_ID,
          version: 3,
          state: "WAITING_HUMAN",
          attempt_count: 1,
          max_attempts: 5,
          error: null,
        }),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "not mocked" });
  });

  await page.addInitScript(
    ({ productId, briefId, operationId, workflowId, assetVersionId }) => {
      sessionStorage.setItem(
        `commercevision.product-brief.v2:catalog-demo:${productId}`,
        JSON.stringify({
          schemaVersion: 1,
          workspaceId: "catalog-demo",
          productId,
          productBriefId: briefId,
          operationId,
          workflowId,
          assetVersionIds: [assetVersionId],
          retentionDeadline: "2099-01-01T00:00:00Z",
        }),
      );
    },
    {
      productId: PRODUCT_ID,
      briefId: BRIEF_ID,
      operationId: OPERATION_ID,
      workflowId: WORKFLOW_ID,
      assetVersionId: ASSET_VERSION_ID,
    },
  );

  await page.goto("/");
  await briefReadStarted;
  try {
    await expect(
      page.getByRole("status").filter({ hasText: "正在恢复商品理解" }),
    ).toBeVisible({ timeout: 1_000 });
  } finally {
    releaseBriefRead();
  }
});

test("aborts an in-flight revision when the selected product changes", async ({
  page,
}) => {
  await page.addInitScript((productId) => {
    sessionStorage.setItem(
      "commercevision.product-brief.active.v2",
      JSON.stringify({
        workspaceId: "catalog-demo",
        productId,
      }),
    );
  }, PRODUCT_ID);
  const productB = {
    ...product,
    id: PRODUCT_B_ID,
    external_id: "SERUM-HITL-002",
    title: "Second Product",
  };
  const modelVersion = version({
    id: MODEL_VERSION_ID,
    number: 1,
    fields: [
      field({
        id: "field-aborted-revision-brand",
        path: "common.brand",
        value: textValue("Northstar Labs"),
      }),
    ],
  });
  const currentBrief = brief(modelVersion, 2);
  let signalRevisionStarted!: () => void;
  let releaseRevision!: () => void;
  const revisionStarted = new Promise<void>((resolve) => {
    signalRevisionStarted = resolve;
  });
  const revisionGate = new Promise<void>((resolve) => {
    releaseRevision = resolve;
  });
  let revisionRequestFailed = false;
  page.on("requestfailed", (request) => {
    if (
      new URL(request.url()).pathname ===
      `/api/v1/product-briefs/${BRIEF_ID}:revise`
    ) {
      revisionRequestFailed = true;
    }
  });

  await page.route("**/api/v1/products**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const body =
      path === `/api/v1/products/${PRODUCT_ID}`
        ? product
        : path === `/api/v1/products/${PRODUCT_B_ID}`
          ? productB
          : { items: [product, productB], next_cursor: null };
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.startsWith("/api/v1/products")) {
      await route.fallback();
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(currentBrief),
      });
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}/versions`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [modelVersion], next_cursor: null }),
      });
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/${BRIEF_ID}/operations/${OPERATION_ID}`
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: OPERATION_ID,
          version: 1,
          state: "WAITING_HUMAN",
          attempt_count: 1,
          max_attempts: 5,
          error: null,
        }),
      });
      return;
    }
    if (
      path === `/api/v1/product-briefs/${BRIEF_ID}:revise` &&
      request.method() === "POST"
    ) {
      signalRevisionStarted();
      await revisionGate;
      try {
        await route.fulfill({
          contentType: "application/json",
          body: JSON.stringify(currentBrief),
        });
      } catch {
        // A cancelled route cannot be fulfilled after product switching.
      }
      return;
    }
    await route.fulfill({ status: 404, body: "not mocked" });
  });
  await page.addInitScript(
    ({ productId, briefId, operationId, workflowId, assetVersionId }) => {
      sessionStorage.setItem(
        `commercevision.product-brief.v2:catalog-demo:${productId}`,
        JSON.stringify({
          schemaVersion: 1,
          workspaceId: "catalog-demo",
          productId,
          productBriefId: briefId,
          operationId,
          workflowId,
          assetVersionIds: [assetVersionId],
          retentionDeadline: "2099-01-01T00:00:00Z",
        }),
      );
    },
    {
      productId: PRODUCT_ID,
      briefId: BRIEF_ID,
      operationId: OPERATION_ID,
      workflowId: WORKFLOW_ID,
      assetVersionId: ASSET_VERSION_ID,
    },
  );

  await page.goto("/");
  await expect(page.getByLabel("品牌值")).toHaveValue(
    editorValue(textValue("Northstar Labs")),
  );
  await page
    .getByLabel("品牌值")
    .fill(editorValue(textValue("Cancelled local brand")));
  await page.getByLabel("修订原因").fill("Cancel on product switch");
  await page.getByRole("button", { name: "保存人工版本" }).click();
  await revisionStarted;

  try {
    await page.getByRole("button", { name: /Second Product/ }).click();
    await expect.poll(() => revisionRequestFailed).toBe(true);
  } finally {
    releaseRevision();
  }
  await expect(page.getByLabel("工作流 ID")).toHaveValue("");
  await expect(page.getByLabel("品牌值")).toHaveCount(0);
});

test("resets ProductBrief state and rejects late responses when products change", async ({
  page,
}) => {
  await page.addInitScript((productId) => {
    sessionStorage.setItem(
      "commercevision.product-brief.active.v2",
      JSON.stringify({
        workspaceId: "catalog-demo",
        productId,
      }),
    );
  }, PRODUCT_ID);
  const productB = {
    ...product,
    id: PRODUCT_B_ID,
    external_id: "SERUM-HITL-002",
    title: "Second Product",
  };
  const modelVersion = version({
    id: MODEL_VERSION_ID,
    number: 1,
    fields: [
      field({
        id: "field-switch-brand",
        path: "common.brand",
        value: textValue("Northstar Labs"),
      }),
    ],
  });
  const currentBrief = brief(modelVersion, 2);
  let briefReads = 0;
  let signalDelayedRead!: () => void;
  let releaseDelayedRead!: () => void;
  const delayedReadStarted = new Promise<void>((resolve) => {
    signalDelayedRead = resolve;
  });
  const delayedReadGate = new Promise<void>((resolve) => {
    releaseDelayedRead = resolve;
  });

  await page.route("**/api/v1/products**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const body =
      path === `/api/v1/products/${PRODUCT_ID}`
        ? product
        : path === `/api/v1/products/${PRODUCT_B_ID}`
          ? productB
          : { items: [product, productB], next_cursor: null };
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.startsWith("/api/v1/products")) {
      await route.fallback();
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}`) {
      briefReads += 1;
      if (briefReads === 3) {
        signalDelayedRead();
        await delayedReadGate;
      }
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(currentBrief),
      });
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}/versions`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [modelVersion], next_cursor: null }),
      });
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/${BRIEF_ID}/operations/${OPERATION_ID}`
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: OPERATION_ID,
          state: "WAITING_HUMAN",
          attempt_count: 1,
          max_attempts: 5,
          error: null,
        }),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "not mocked" });
  });

  await page.addInitScript(
    ({ productId, briefId, operationId, workflowId, assetVersionId }) => {
      sessionStorage.setItem(
        `commercevision.product-brief.v2:catalog-demo:${productId}`,
        JSON.stringify({
          schemaVersion: 1,
          workspaceId: "catalog-demo",
          productId,
          productBriefId: briefId,
          operationId,
          workflowId,
          assetVersionIds: [assetVersionId],
          retentionDeadline: "2099-01-01T00:00:00Z",
        }),
      );
    },
    {
      productId: PRODUCT_ID,
      briefId: BRIEF_ID,
      operationId: OPERATION_ID,
      workflowId: WORKFLOW_ID,
      assetVersionId: ASSET_VERSION_ID,
    },
  );
  await page.goto("/");
  await expect(page.getByLabel("品牌值")).toHaveValue(
    editorValue(textValue("Northstar Labs")),
  );
  await page
    .getByLabel("品牌值")
    .fill(editorValue(textValue("Unsaved A brand")));
  await page.getByLabel("修订原因").fill("Unsaved A reason");
  await page.getByLabel("确认原因编码").fill("CUSTOM_A");
  await page.getByLabel("评论引用").fill("comment://a");

  await page
    .locator(".product-brief-panel")
    .getByRole("button", { name: "刷新" })
    .click();
  await delayedReadStarted;
  await page.getByRole("button", { name: /Second Product/ }).click();
  releaseDelayedRead();

  await expect(page.getByLabel("工作流 ID")).toHaveValue("");
  await expect(page.getByLabel("素材版本 ID")).toHaveValue("");
  await expect(page.locator("[data-testid='brief-operation']")).toHaveCount(0);
  await expect(page.getByLabel("品牌值")).toHaveCount(0);

  await page.getByRole("button", { name: /Hydrating Serum/ }).click();
  await expect(page.getByLabel("品牌值")).toHaveCount(0);
  await expect(page.getByLabel("工作流 ID")).toHaveValue("");
  expect(
    await page.evaluate((productId) =>
      sessionStorage.getItem(
        `commercevision.product-brief.v2:catalog-demo:${productId}`,
      ),
    PRODUCT_ID),
  ).toBeNull();
});

test("clears a transient polling warning after polling recovers", async ({
  page,
}) => {
  await page.clock.install({ time: new Date("2026-07-28T01:00:00Z") });
  await routeCatalog(page);
  const modelVersion = version({
    id: MODEL_VERSION_ID,
    number: 1,
    fields: [
      field({
        id: "field-poll-recovery",
        path: "common.brand",
        value: textValue("Northstar Labs"),
      }),
    ],
  });
  const currentBrief = brief(modelVersion, 2);
  let operationRequests = 0;
  let pollingCanRecover = false;

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.startsWith("/api/v1/products")) {
      await route.fallback();
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/analysis-workflow-context/${WORKFLOW_ID}`
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: WORKFLOW_ID,
          status: "UNDERSTANDING",
          version: 3,
          retention_deadline: RETENTION_DEADLINE,
        }),
      });
      return;
    }
    if (path === "/api/v1/product-briefs:analyze") {
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({
          product_brief: currentBrief,
          operation_id: OPERATION_ID,
          operation_state: "PENDING",
        }),
      });
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(currentBrief),
      });
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}/versions`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [modelVersion], next_cursor: null }),
      });
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/${BRIEF_ID}/operations/${OPERATION_ID}`
    ) {
      operationRequests += 1;
      if (!pollingCanRecover) {
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({
            code: "DEPENDENCY_UNAVAILABLE",
            category: "dependency",
            message: "Operation projection is temporarily unavailable",
            retryable: true,
            trace_id: "trace-poll-recovery",
          }),
        });
        return;
      }
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: OPERATION_ID,
          version: operationRequests,
          state: "RUNNING",
          attempt_count: 1,
          max_attempts: 5,
          error: null,
        }),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "not mocked" });
  });

  await page.goto("/");
  const currentTime = await page.evaluate(() => Date.now());
  await page.clock.pauseAt(currentTime + 100);
  await page.getByLabel("工作流 ID").fill(WORKFLOW_ID);
  await page.getByLabel("素材版本 ID").fill(ASSET_VERSION_ID);
  await page.getByRole("button", { name: "开始商品理解" }).click();
  await expect(
    page.getByText("商品理解已更新，但任务状态暂不可用。"),
  ).toBeVisible();
  await page.clock.runFor(2_000);
  await expect(
    page.getByText("任务状态刷新暂时失败", { exact: false }),
  ).toBeVisible();
  await expect(
    page.locator(".product-brief-panel .error-banner"),
  ).toHaveCount(0);
  pollingCanRecover = true;
  await page.clock.runFor(2_000);
  await expect(
    page.getByText("任务状态刷新暂时失败", { exact: false }),
  ).toHaveCount(0, { timeout: 10_000 });
});

test("shows deny-by-default provider policy rejection", async ({ page }) => {
  await routeCatalog(page);
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.startsWith("/api/v1/products")) {
      await route.fallback();
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/analysis-workflow-context/${WORKFLOW_ID}`
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: WORKFLOW_ID,
          status: "UNDERSTANDING",
          version: 3,
          retention_deadline: RETENTION_DEADLINE,
        }),
      });
      return;
    }
    if (path === "/api/v1/product-briefs:analyze") {
      await route.fulfill({
        status: 403,
        contentType: "application/json",
        body: JSON.stringify({
          code: "VISION_TRANSFER_DISABLED",
          category: "authorization",
          message: "Vision data transfer is disabled",
          retryable: false,
          trace_id: "trace-policy-denied",
        }),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "not mocked" });
  });

  await page.goto("/");
  await page.getByLabel("工作流 ID").fill(WORKFLOW_ID);
  await page.getByLabel("素材版本 ID").fill(ASSET_VERSION_ID);
  await page.getByRole("button", { name: "开始商品理解" }).click();

  await expect(page.locator(".product-brief-panel .error-banner")).toContainText(
    "当前工作区或服务端策略不允许模型传输",
  );
  await expect(page.locator("[data-testid='brief-operation']")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "安全重试" })).toHaveCount(0);
  expect(
    await page.evaluate((productId) =>
      sessionStorage.getItem(
        `commercevision.product-brief.v2:catalog-demo:${productId}`,
      ),
    PRODUCT_ID),
  ).toBeNull();
});

test("authoritative 410 aborts the workbench and clears all retained state immediately", async ({
  page,
}) => {
  await routeCatalog(page);
  const modelVersion = version({
    id: MODEL_VERSION_ID,
    number: 1,
    fields: [
      field({
        id: "field-authoritative-gone-brand",
        path: "common.brand",
        value: textValue("Northstar Labs"),
      }),
    ],
  });
  const currentBrief = brief(modelVersion, 2);
  let operationReads = 0;

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.startsWith("/api/v1/products")) {
      await route.fallback();
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/analysis-workflow-context/${WORKFLOW_ID}`
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: WORKFLOW_ID,
          status: "UNDERSTANDING",
          version: 3,
          retention_deadline: RETENTION_DEADLINE,
        }),
      });
      return;
    }
    if (path === "/api/v1/product-briefs:analyze") {
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({
          product_brief: currentBrief,
          operation_id: OPERATION_ID,
          operation_state: "PENDING",
        }),
      });
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(currentBrief),
      });
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}/versions`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [modelVersion], next_cursor: null }),
      });
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/${BRIEF_ID}/operations/${OPERATION_ID}`
    ) {
      operationReads += 1;
      if (operationReads < 2) {
        await route.fulfill({
          contentType: "application/json",
          body: JSON.stringify({
            id: OPERATION_ID,
            version: operationReads,
            state: "RUNNING",
            attempt_count: 1,
            max_attempts: 5,
            error: null,
          }),
        });
        return;
      }
      await route.fulfill({
        status: 410,
        contentType: "application/json",
        body: JSON.stringify({
          code: "PRODUCT_BRIEF_RETENTION_EXPIRED",
          category: "conflict",
          message: "ProductBrief retention has expired",
          retryable: false,
          trace_id: "trace-authoritative-gone",
        }),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "not mocked" });
  });

  await page.goto("/");
  const currentTime = await page.evaluate(() => Date.now());
  await page.clock.pauseAt(currentTime + 100);
  await page.getByLabel("工作流 ID").fill(WORKFLOW_ID);
  await page.getByLabel("素材版本 ID").fill(ASSET_VERSION_ID);
  await page.getByRole("button", { name: "开始商品理解" }).click();

  await expect(page.locator("[data-testid='brief-operation']")).toBeVisible();
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          Object.keys(sessionStorage).filter((key) =>
            key.startsWith("commercevision.product-brief.v2:"),
          ).length,
      ),
    )
    .toBeGreaterThan(0);

  await page.clock.runFor(5_000);
  await expect.poll(() => operationReads).toBeGreaterThanOrEqual(2);
  await expect(page.locator("[data-testid='brief-operation']")).toHaveCount(0);
  await expect(
    page.locator(".product-brief-panel .error-banner"),
  ).toContainText("商品理解任务已到保留期限");
  expect(
    await page.evaluate(
      () =>
        Object.keys(sessionStorage).filter((key) =>
          key.startsWith("commercevision.product-brief.v2:"),
        ).length,
    ),
  ).toBe(0);
});

test("auxiliary 410 clears retained state without waiting for a hung sibling read", async ({
  page,
}) => {
  await routeCatalog(page);
  const modelVersion = version({
    id: MODEL_VERSION_ID,
    number: 1,
    fields: [
      field({
        id: "field-auxiliary-gone-brand",
        path: "common.brand",
        value: textValue("Northstar Labs"),
      }),
    ],
  });
  const currentBrief = brief(modelVersion, 2);
  let historyReads = 0;
  let operationReads = 0;
  let releaseBlockedOperation!: () => void;
  const blockedOperation = new Promise<void>((resolve) => {
    releaseBlockedOperation = resolve;
  });

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.startsWith("/api/v1/products")) {
      await route.fallback();
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/analysis-workflow-context/${WORKFLOW_ID}`
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: WORKFLOW_ID,
          status: "UNDERSTANDING",
          version: 3,
          retention_deadline: RETENTION_DEADLINE,
        }),
      });
      return;
    }
    if (path === "/api/v1/product-briefs:analyze") {
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({
          product_brief: currentBrief,
          operation_id: OPERATION_ID,
          operation_state: "PENDING",
        }),
      });
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(currentBrief),
      });
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}/versions`) {
      historyReads += 1;
      if (historyReads === 1) {
        await route.fulfill({
          contentType: "application/json",
          body: JSON.stringify({
            items: [modelVersion],
            next_cursor: null,
          }),
        });
        return;
      }
      await route.fulfill({
        status: 410,
        contentType: "application/json",
        body: JSON.stringify({
          code: "PRODUCT_BRIEF_RETENTION_EXPIRED",
          category: "conflict",
          message: "ProductBrief retention has expired",
          retryable: false,
          trace_id: "trace-auxiliary-authoritative-gone",
        }),
      });
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/${BRIEF_ID}/operations/${OPERATION_ID}`
    ) {
      operationReads += 1;
      if (operationReads > 1) {
        await blockedOperation;
      }
      try {
        await route.fulfill({
          contentType: "application/json",
          body: JSON.stringify({
            id: OPERATION_ID,
            version: operationReads,
            state: "RUNNING",
            attempt_count: 1,
            max_attempts: 5,
            error: null,
          }),
        });
      } catch {
        // The authoritative 410 must abort this intentionally blocked sibling.
      }
      return;
    }
    await route.fulfill({ status: 404, body: "not mocked" });
  });

  await page.goto("/");
  const currentTime = await page.evaluate(() => Date.now());
  await page.clock.pauseAt(currentTime + 100);
  await page.getByLabel("工作流 ID").fill(WORKFLOW_ID);
  await page.getByLabel("素材版本 ID").fill(ASSET_VERSION_ID);
  await page.getByRole("button", { name: "开始商品理解" }).click();

  await expect(page.locator("[data-testid='brief-operation']")).toBeVisible();
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          Object.keys(sessionStorage).filter((key) =>
            key.startsWith("commercevision.product-brief.v2:"),
          ).length,
      ),
    )
    .toBeGreaterThan(0);

  await page
    .locator(".product-brief-panel")
    .getByRole("button", { name: "刷新" })
    .click();
  await expect.poll(() => historyReads).toBe(2);
  await expect.poll(() => operationReads).toBe(2);

  try {
    await expect(
      page.locator(".product-brief-panel .error-banner"),
    ).toContainText("商品理解任务已到保留期限", { timeout: 1_500 });
    await expect(page.locator("[data-testid='brief-operation']")).toHaveCount(
      0,
    );
    expect(
      await page.evaluate(
        () =>
          Object.keys(sessionStorage).filter((key) =>
            key.startsWith("commercevision.product-brief.v2:"),
          ).length,
      ),
    ).toBe(0);
  } finally {
    releaseBlockedOperation();
  }
});

test("manual refresh re-arms polling after superseding an in-flight poll", async ({
  page,
}) => {
  await page.clock.install({ time: new Date("2026-07-29T10:00:00Z") });
  await routeCatalog(page);
  const modelVersion = version({
    id: MODEL_VERSION_ID,
    number: 1,
    fields: [
      field({
        id: "field-poll-refresh-race-brand",
        path: "common.brand",
        value: textValue("Northstar Labs"),
      }),
    ],
  });
  const currentBrief = brief(modelVersion, 2);
  let operationReads = 0;
  let blockNextOperation = false;
  let blockedOperationRead = 0;
  let releaseInFlightPoll!: () => void;
  const inFlightPoll = new Promise<void>((resolve) => {
    releaseInFlightPoll = resolve;
  });

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.startsWith("/api/v1/products")) {
      await route.fallback();
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/analysis-workflow-context/${WORKFLOW_ID}`
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: WORKFLOW_ID,
          status: "UNDERSTANDING",
          version: 3,
          retention_deadline: RETENTION_DEADLINE,
        }),
      });
      return;
    }
    if (path === "/api/v1/product-briefs:analyze") {
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({
          product_brief: currentBrief,
          operation_id: OPERATION_ID,
          operation_state: "PENDING",
        }),
      });
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(currentBrief),
      });
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}/versions`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          items: [modelVersion],
          next_cursor: null,
        }),
      });
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/${BRIEF_ID}/operations/${OPERATION_ID}`
    ) {
      operationReads += 1;
      if (blockNextOperation) {
        blockNextOperation = false;
        blockedOperationRead = operationReads;
        await inFlightPoll;
      }
      try {
        await route.fulfill({
          contentType: "application/json",
          body: JSON.stringify({
            id: OPERATION_ID,
            version: operationReads,
            state: "RUNNING",
            attempt_count: 1,
            max_attempts: 5,
            error: null,
          }),
        });
      } catch {
        // A later manual read may make the blocked poll response stale.
      }
      return;
    }
    await route.fulfill({ status: 404, body: "not mocked" });
  });

  await page.goto("/");
  await page.getByLabel("工作流 ID").fill(WORKFLOW_ID);
  await page.getByLabel("素材版本 ID").fill(ASSET_VERSION_ID);
  await page.getByRole("button", { name: "开始商品理解" }).click();
  await expect(page.locator("[data-testid='brief-operation']")).toBeVisible();
  blockNextOperation = true;
  await page.clock.runFor(1);
  await expect.poll(() => blockedOperationRead).toBeGreaterThan(0);

  await page
    .locator(".product-brief-panel")
    .getByRole("button", { name: "刷新" })
    .click();
  await expect
    .poll(() => operationReads)
    .toBeGreaterThan(blockedOperationRead);

  try {
    const readsBeforeResume = operationReads;
    releaseInFlightPoll();
    await page.clock.runFor(10_000);
    await expect
      .poll(() => operationReads)
      .toBeGreaterThan(readsBeforeResume);
    await expect(
      page.locator("[data-testid='brief-operation']"),
    ).toContainText("分析中");
    await expect(
      page.getByRole("button", { name: "继续刷新" }),
    ).toHaveCount(0);
  } finally {
    releaseInFlightPoll();
  }
});

test("storage denial fails closed without discarding the last durable replay identity", async ({
  page,
}) => {
  const now = "2026-07-29T10:00:00.000Z";
  const priorDeadline = "2026-07-29T10:00:01.000Z";
  await page.clock.install({ time: new Date(now) });
  await routeCatalog(page);
  const modelVersion = version({
    id: MODEL_VERSION_ID,
    number: 1,
    fields: [
      field({
        id: "field-storage-denied-brand",
        path: "common.brand",
        value: textValue("Northstar Labs"),
      }),
    ],
  });
  const currentBrief = brief(modelVersion, 2);

  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.startsWith("/api/v1/products")) {
      await route.fallback();
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(currentBrief),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "not mocked" });
  });
  await page.addInitScript(
    ({
      productId,
      briefId,
      operationId,
      workflowId,
      assetVersionId,
      priorDeadline,
    }) => {
      const key =
        `commercevision.product-brief.v2:catalog-demo:${productId}`;
      const originalSetItem = Storage.prototype.setItem;
      originalSetItem.call(
        sessionStorage,
        key,
        JSON.stringify({
          schemaVersion: 1,
          workspaceId: "catalog-demo",
          productId,
          productBriefId: briefId,
          operationId,
          workflowId,
          assetVersionIds: [assetVersionId],
          retentionDeadline: priorDeadline,
        }),
      );
      Storage.prototype.setItem = function setItem(
        storageKey,
        value,
      ) {
        if (this === sessionStorage && storageKey === key) {
          throw new DOMException("storage denied", "QuotaExceededError");
        }
        originalSetItem.call(this, storageKey, value);
      };
    },
    {
      productId: PRODUCT_ID,
      briefId: BRIEF_ID,
      operationId: OPERATION_ID,
      workflowId: WORKFLOW_ID,
      assetVersionId: ASSET_VERSION_ID,
      priorDeadline,
    },
  );

  await page.goto("/");

  await expect(
    page.locator(".product-brief-panel .error-banner"),
  ).toContainText("浏览器无法安全保存商品理解恢复状态");
  await expect(page.locator("[data-testid='brief-operation']")).toHaveCount(0);
  await expect(page.getByLabel("品牌值")).toHaveCount(0);
  expect(
    await page.evaluate((productId) =>
      sessionStorage.getItem(
        `commercevision.product-brief.v2:catalog-demo:${productId}`,
      ),
    PRODUCT_ID),
  ).not.toBeNull();

  await page.clock.runFor(1_000);
  expect(
    await page.evaluate((productId) =>
      sessionStorage.getItem(
        `commercevision.product-brief.v2:catalog-demo:${productId}`,
      ),
    PRODUCT_ID),
  ).toBeNull();
});

test("ignores an authoritative 410 from a superseded ProductBrief poll", async ({
  page,
}) => {
  await routeCatalog(page);
  const versionA = version({
    id: MODEL_VERSION_ID,
    number: 1,
    fields: [
      field({
        id: "field-stale-410-brand-a",
        path: "common.brand",
        value: textValue("Original Brand"),
      }),
    ],
  });
  const briefA = brief(versionA, 2);
  const versionB = {
    ...version({
      id: NEXT_VERSION_ID,
      number: 1,
      fields: [
        field({
          id: "field-stale-410-brand-b",
          path: "common.brand",
          value: textValue("Replacement Brand"),
        }),
      ],
    }),
    product_brief_id: NEXT_BRIEF_ID,
  };
  const briefB = {
    ...brief(versionB, 3),
    id: NEXT_BRIEF_ID,
    operation_id: NEXT_OPERATION_ID,
    current_version_id: NEXT_VERSION_ID,
  };
  let operationAReads = 0;
  let releaseStalePoll!: () => void;
  const stalePoll = new Promise<void>((resolve) => {
    releaseStalePoll = resolve;
  });

  await page.addInitScript(
    ({ productId, briefId, operationId, workflowId, assetVersionId }) => {
      sessionStorage.setItem(
        `commercevision.product-brief.v2:catalog-demo:${productId}`,
        JSON.stringify({
          schemaVersion: 1,
          workspaceId: "catalog-demo",
          productId,
          productBriefId: briefId,
          operationId,
          workflowId,
          assetVersionIds: [assetVersionId],
          retentionDeadline: "2099-01-01T00:00:00Z",
        }),
      );
    },
    {
      productId: PRODUCT_ID,
      briefId: BRIEF_ID,
      operationId: OPERATION_ID,
      workflowId: WORKFLOW_ID,
      assetVersionId: ASSET_VERSION_ID,
    },
  );
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.startsWith("/api/v1/products")) {
      await route.fallback();
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(briefA),
      });
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}/versions`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [versionA], next_cursor: null }),
      });
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/${BRIEF_ID}/operations/${OPERATION_ID}`
    ) {
      operationAReads += 1;
      if (operationAReads === 1) {
        await route.fulfill({
          contentType: "application/json",
          body: JSON.stringify({
            id: OPERATION_ID,
            version: 1,
            state: "RUNNING",
            attempt_count: 1,
            max_attempts: 5,
            error: null,
          }),
        });
        return;
      }
      await stalePoll;
      try {
        await route.fulfill({
          status: 410,
          contentType: "application/json",
          body: JSON.stringify({
            code: "PRODUCT_BRIEF_RETENTION_EXPIRED",
            category: "conflict",
            message: "ProductBrief retention has expired",
            retryable: false,
            trace_id: "trace-superseded-brief-gone",
          }),
        });
      } catch {
        // The stale request can be cancelled once the replacement is retained.
      }
      return;
    }
    if (path === `/api/v1/product-briefs/${NEXT_BRIEF_ID}`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(briefB),
      });
      return;
    }
    if (path === `/api/v1/product-briefs/${NEXT_BRIEF_ID}/versions`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [versionB], next_cursor: null }),
      });
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/${NEXT_BRIEF_ID}/operations/${NEXT_OPERATION_ID}`
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: NEXT_OPERATION_ID,
          version: 1,
          state: "WAITING_HUMAN",
          attempt_count: 1,
          max_attempts: 5,
          error: null,
        }),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "not mocked" });
  });

  await page.goto("/");
  await expect(page.getByLabel("品牌值")).toHaveValue(
    editorValue(textValue("Original Brand")),
  );
  await expect.poll(() => operationAReads).toBeGreaterThanOrEqual(2);

  await page.getByLabel("商品理解 ID").fill(NEXT_BRIEF_ID);
  await page.getByRole("button", { name: "载入", exact: true }).click();
  await expect(page.getByLabel("品牌值")).toHaveValue(
    editorValue(textValue("Replacement Brand")),
  );
  await expect
    .poll(() =>
      page.evaluate((productId) => {
        const raw = sessionStorage.getItem(
          `commercevision.product-brief.v2:catalog-demo:${productId}`,
        );
        return raw ? JSON.parse(raw).productBriefId : null;
      }, PRODUCT_ID),
    )
    .toBe(NEXT_BRIEF_ID);

  releaseStalePoll();
  await expect(page.getByLabel("品牌值")).toHaveValue(
    editorValue(textValue("Replacement Brand")),
  );
  await expect(
    page.locator(".product-brief-panel .error-banner"),
  ).toHaveCount(0);
  expect(
    await page.evaluate((productId) => {
      const raw = sessionStorage.getItem(
        `commercevision.product-brief.v2:catalog-demo:${productId}`,
      );
      return raw ? JSON.parse(raw).productBriefId : null;
    }, PRODUCT_ID),
  ).toBe(NEXT_BRIEF_ID);
});

test("ignores an authoritative 410 after the selected product changes", async ({
  page,
}) => {
  await page.addInitScript((productId) => {
    sessionStorage.setItem(
      "commercevision.product-brief.active.v2",
      JSON.stringify({
        workspaceId: "catalog-demo",
        productId,
      }),
    );
  }, PRODUCT_ID);
  const productB = {
    ...product,
    id: PRODUCT_B_ID,
    external_id: "SERUM-HITL-002",
    title: "Second Product",
  };
  const versionA = version({
    id: MODEL_VERSION_ID,
    number: 1,
    fields: [
      field({
        id: "field-cross-product-410-brand-a",
        path: "common.brand",
        value: textValue("Original Product Brand"),
      }),
    ],
  });
  const briefA = brief(versionA, 2);
  const versionB = {
    ...version({
      id: NEXT_VERSION_ID,
      number: 1,
      fields: [
        field({
          id: "field-cross-product-410-brand-b",
          path: "common.brand",
          value: textValue("Current Product Brand"),
        }),
      ],
    }),
    product_brief_id: NEXT_BRIEF_ID,
  };
  const briefB = {
    ...brief(versionB, 3),
    id: NEXT_BRIEF_ID,
    product_id: PRODUCT_B_ID,
    operation_id: NEXT_OPERATION_ID,
    current_version_id: NEXT_VERSION_ID,
  };
  let operationAReads = 0;
  let releaseStalePoll!: () => void;
  let signalStalePollHandled!: () => void;
  const stalePoll = new Promise<void>((resolve) => {
    releaseStalePoll = resolve;
  });
  const stalePollHandled = new Promise<void>((resolve) => {
    signalStalePollHandled = resolve;
  });

  await page.route("**/api/v1/products**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const body =
      path === `/api/v1/products/${PRODUCT_ID}`
        ? product
        : path === `/api/v1/products/${PRODUCT_B_ID}`
          ? productB
          : { items: [product, productB], next_cursor: null };
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });
  await page.addInitScript(
    ({
      productId,
      productBId,
      briefId,
      briefBId,
      operationId,
      operationBId,
      workflowId,
      assetVersionId,
    }) => {
      for (const record of [
        {
          productId,
          productBriefId: briefId,
          operationId,
        },
        {
          productId: productBId,
          productBriefId: briefBId,
          operationId: operationBId,
        },
      ]) {
        sessionStorage.setItem(
          `commercevision.product-brief.v2:catalog-demo:${record.productId}`,
          JSON.stringify({
            schemaVersion: 1,
            workspaceId: "catalog-demo",
            productId: record.productId,
            productBriefId: record.productBriefId,
            operationId: record.operationId,
            workflowId,
            assetVersionIds: [assetVersionId],
            retentionDeadline: "2099-01-01T00:00:00Z",
          }),
        );
      }
    },
    {
      productId: PRODUCT_ID,
      productBId: PRODUCT_B_ID,
      briefId: BRIEF_ID,
      briefBId: NEXT_BRIEF_ID,
      operationId: OPERATION_ID,
      operationBId: NEXT_OPERATION_ID,
      workflowId: WORKFLOW_ID,
      assetVersionId: ASSET_VERSION_ID,
    },
  );
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.startsWith("/api/v1/products")) {
      await route.fallback();
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(briefA),
      });
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}/versions`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [versionA], next_cursor: null }),
      });
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/${BRIEF_ID}/operations/${OPERATION_ID}`
    ) {
      operationAReads += 1;
      if (operationAReads === 1) {
        await route.fulfill({
          contentType: "application/json",
          body: JSON.stringify({
            id: OPERATION_ID,
            version: 1,
            state: "RUNNING",
            attempt_count: 1,
            max_attempts: 5,
            error: null,
          }),
        });
        return;
      }
      await stalePoll;
      try {
        await route.fulfill({
          status: 410,
          contentType: "application/json",
          body: JSON.stringify({
            code: "PRODUCT_BRIEF_RETENTION_EXPIRED",
            category: "conflict",
            message: "ProductBrief retention has expired",
            retryable: false,
            trace_id: "trace-cross-product-gone",
          }),
        });
      } catch {
        // Changing product may cancel the superseded transport first.
      } finally {
        signalStalePollHandled();
      }
      return;
    }
    if (path === `/api/v1/product-briefs/${NEXT_BRIEF_ID}`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(briefB),
      });
      return;
    }
    if (path === `/api/v1/product-briefs/${NEXT_BRIEF_ID}/versions`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [versionB], next_cursor: null }),
      });
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/${NEXT_BRIEF_ID}/operations/${NEXT_OPERATION_ID}`
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: NEXT_OPERATION_ID,
          version: 1,
          state: "WAITING_HUMAN",
          attempt_count: 1,
          max_attempts: 5,
          error: null,
        }),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "not mocked" });
  });

  await page.goto("/");
  await expect(page.getByLabel("品牌值")).toHaveValue(
    editorValue(textValue("Original Product Brand")),
  );
  await expect.poll(() => operationAReads).toBeGreaterThanOrEqual(2);

  await page.getByRole("button", { name: /Second Product/ }).click();
  await expect(page.getByLabel("品牌值")).toHaveCount(0);
  await expect
    .poll(() =>
      page.evaluate((productId) =>
        sessionStorage.getItem(
          `commercevision.product-brief.v2:catalog-demo:${productId}`,
        ),
      PRODUCT_B_ID),
    )
    .toBeNull();
  await page.getByLabel("商品理解 ID").fill(NEXT_BRIEF_ID);
  await page.getByRole("button", { name: "载入", exact: true }).click();
  await expect(page.getByLabel("品牌值")).toHaveValue(
    editorValue(textValue("Current Product Brand")),
  );
  await expect
    .poll(() =>
      page.evaluate((productId) => {
        const raw = sessionStorage.getItem(
          `commercevision.product-brief.v2:catalog-demo:${productId}`,
        );
        return raw ? JSON.parse(raw).productBriefId : null;
      }, PRODUCT_B_ID),
    )
    .toBe(NEXT_BRIEF_ID);

  releaseStalePoll();
  await stalePollHandled;
  await expect(page.getByLabel("品牌值")).toHaveValue(
    editorValue(textValue("Current Product Brand")),
  );
  await expect(
    page.locator(".product-brief-panel .error-banner"),
  ).toHaveCount(0);
  expect(
    await page.evaluate((productId) => {
      const raw = sessionStorage.getItem(
        `commercevision.product-brief.v2:catalog-demo:${productId}`,
      );
      return raw ? JSON.parse(raw).productBriefId : null;
    }, PRODUCT_B_ID),
  ).toBe(NEXT_BRIEF_ID);
});

const analysisResponseIdentityMismatchCases: ReadonlyArray<{
  identityField: string;
  productBriefPatch: Partial<BrowserBrief>;
}> = [
  {
    identityField: "workspace_id",
    productBriefPatch: { workspace_id: "catalog-other" },
  },
  {
    identityField: "product_id",
    productBriefPatch: { product_id: PRODUCT_B_ID },
  },
  {
    identityField: "workflow_id",
    productBriefPatch: {
      workflow_id: "019f8a00-0000-7000-8000-000000000116",
    },
  },
  {
    identityField: "operation_id",
    productBriefPatch: { operation_id: NEXT_OPERATION_ID },
  },
];

for (const mismatch of analysisResponseIdentityMismatchCases) {
  test(`keeps the original analysis replay identity when 202 ${mismatch.identityField} mismatches`, async ({
    page,
  }) => {
  await routeCatalog(page);
  const modelVersion = version({
    id: MODEL_VERSION_ID,
    number: 1,
    fields: [
      field({
        id: "field-analysis-response-mismatch-brand",
        path: "common.brand",
        value: textValue("Mismatched analysis result"),
      }),
    ],
  });
  const mismatchedBrief = {
    ...brief(modelVersion, 2),
    ...mismatch.productBriefPatch,
  };
  const analysisRequests: Array<{
    body: unknown;
    idempotencyKey: string | null;
  }> = [];
  let productBriefReads = 0;

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.startsWith("/api/v1/products")) {
      await route.fallback();
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/analysis-workflow-context/${WORKFLOW_ID}`
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: WORKFLOW_ID,
          status: "UNDERSTANDING",
          version: 3,
          retention_deadline: RETENTION_DEADLINE,
        }),
      });
      return;
    }
    if (path === "/api/v1/product-briefs:analyze") {
      analysisRequests.push({
        body: request.postDataJSON(),
        idempotencyKey: request.headers()["idempotency-key"] ?? null,
      });
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({
          product_brief: mismatchedBrief,
          operation_id: OPERATION_ID,
          operation_state: "PENDING",
        }),
      });
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}`) {
      productBriefReads += 1;
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(mismatchedBrief),
      });
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}/versions`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [modelVersion], next_cursor: null }),
      });
      return;
    }
    if (
      path ===
      `/api/v1/product-briefs/${BRIEF_ID}/operations/${NEXT_OPERATION_ID}`
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: NEXT_OPERATION_ID,
          version: 1,
          state: "WAITING_HUMAN",
          attempt_count: 1,
          max_attempts: 5,
          error: null,
        }),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "not mocked" });
  });

  await page.goto("/");
  await page.getByLabel("工作流 ID").fill(WORKFLOW_ID);
  await page.getByLabel("素材版本 ID").fill(ASSET_VERSION_ID);
  await page.getByRole("button", { name: "开始商品理解" }).click();

  await expect.poll(() => analysisRequests.length).toBe(1);
  const persisted = await page.evaluate((productId) => {
    const raw = sessionStorage.getItem(
      `commercevision.product-brief.v2:catalog-demo:${productId}`,
    );
    return raw ? JSON.parse(raw) : null;
  }, PRODUCT_ID);
  expect(persisted?.schemaVersion).toBe(2);
  expect(persisted?.pendingAnalysis).toEqual({
    payload: analysisRequests[0]?.body,
    idempotencyKey: analysisRequests[0]?.idempotencyKey,
    priorProductBrief: null,
  });
  expect(productBriefReads).toBe(0);

  await page.getByRole("button", { name: "安全重试" }).click();
  await expect.poll(() => analysisRequests.length).toBe(2);
  expect(analysisRequests[1]).toEqual(analysisRequests[0]);
  });
}

test("keeps the bound recovery identity when a ProductBrief load changes Operation", async ({
  page,
}) => {
  await routeCatalog(page);
  const modelVersion = version({
    id: MODEL_VERSION_ID,
    number: 1,
    fields: [
      field({
        id: "field-load-operation-mismatch-brand",
        path: "common.brand",
        value: textValue("Wrong operation result"),
      }),
    ],
  });
  const mismatchedBrief = {
    ...brief(modelVersion, 2),
    operation_id: NEXT_OPERATION_ID,
  };
  let auxiliaryRequests = 0;

  await page.addInitScript(
    ({
      productId,
      briefId,
      operationId,
      workflowId,
      assetVersionId,
      retentionDeadline,
    }) => {
      sessionStorage.setItem(
        `commercevision.product-brief.v2:catalog-demo:${productId}`,
        JSON.stringify({
          schemaVersion: 1,
          workspaceId: "catalog-demo",
          productId,
          productBriefId: briefId,
          operationId,
          workflowId,
          assetVersionIds: [assetVersionId],
          retentionDeadline,
        }),
      );
    },
    {
      productId: PRODUCT_ID,
      briefId: BRIEF_ID,
      operationId: OPERATION_ID,
      workflowId: WORKFLOW_ID,
      assetVersionId: ASSET_VERSION_ID,
      retentionDeadline: RETENTION_DEADLINE,
    },
  );
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.startsWith("/api/v1/products")) {
      await route.fallback();
      return;
    }
    if (path === `/api/v1/product-briefs/${BRIEF_ID}`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(mismatchedBrief),
      });
      return;
    }
    if (path === "/api/v1/brand-profiles") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [], next_cursor: null }),
      });
      return;
    }
    auxiliaryRequests += 1;
    await route.fulfill({ status: 500, body: "auxiliary read must not run" });
  });

  await page.goto("/");

  await expect(
    page.locator(".product-brief-panel .error-banner"),
  ).toContainText("身份不匹配");
  await expect(page.getByLabel("品牌值")).toHaveCount(0);
  expect(auxiliaryRequests).toBe(0);
  expect(
    await page.evaluate((productId) => {
      const raw = sessionStorage.getItem(
        `commercevision.product-brief.v2:catalog-demo:${productId}`,
      );
      return raw ? JSON.parse(raw).operationId : null;
    }, PRODUCT_ID),
  ).toBe(OPERATION_ID);
});

const commandPersistenceLossCases = [
  {
    kind: "revision",
    endpoint: `/api/v1/product-briefs/${BRIEF_ID}:revise`,
    command: {
      schemaVersion: 1,
      kind: "revise",
      productId: PRODUCT_ID,
      productBriefId: BRIEF_ID,
      payload: {
        expected_product_brief_version: 7,
        base_version_id: MODEL_VERSION_ID,
        reason: "Verified against retained packaging evidence",
        fields: [
          {
            path: "common.brand",
            value: textValue("Northstar Labs"),
            confidence: 1,
            conflict: "NONE",
            review_required: false,
            sensitive: false,
            evidence: [
              {
                source_asset_version_id: ASSET_VERSION_ID,
                kind: "IMAGE_REGION",
                reference: `asset-region://${"c".repeat(64)}`,
                region: [0.1, 0.1, 0.9, 0.9],
                excerpt_sha256: "d".repeat(64),
              },
            ],
          },
        ],
      },
      idempotencyKey: "web-product-brief-revise-persistence-loss",
    },
  },
  {
    kind: "confirmation",
    endpoint: `/api/v1/product-briefs/${BRIEF_ID}:confirm`,
    command: {
      schemaVersion: 1,
      kind: "confirm",
      productId: PRODUCT_ID,
      productBriefId: BRIEF_ID,
      payload: {
        expected_product_brief_version: 7,
        product_brief_version_id: MODEL_VERSION_ID,
        expected_workflow_version: 3,
        reason_code: "HUMAN_VERIFIED",
        comment_ref: null,
      },
      idempotencyKey: "web-product-brief-confirm-persistence-loss",
    },
  },
] as const;

for (const scenario of commandPersistenceLossCases) {
  test(`does not replay a ${scenario.kind} after its durable command disappears`, async ({
    page,
  }) => {
    await routeCatalog(page);
    await page.addInitScript(
      ({
        productId,
        briefId,
        operationId,
        workflowId,
        assetVersionId,
        retentionDeadline,
        command,
      }) => {
        sessionStorage.setItem(
          `commercevision.product-brief.v2:catalog-demo:${productId}`,
          JSON.stringify({
            schemaVersion: 3,
            workspaceId: "catalog-demo",
            productId,
            productBriefId: briefId,
            operationId,
            workflowId,
            assetVersionIds: [assetVersionId],
            retentionDeadline,
            pendingCommand: command,
            commandStatus: "pending",
          }),
        );
      },
      {
        productId: PRODUCT_ID,
        briefId: BRIEF_ID,
        operationId: OPERATION_ID,
        workflowId: WORKFLOW_ID,
        assetVersionId: ASSET_VERSION_ID,
        retentionDeadline: RETENTION_DEADLINE,
        command: scenario.command,
      },
    );
    let mutationRequests = 0;
    await page.route("**/api/v1/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path.startsWith("/api/v1/products")) {
        await route.fallback();
        return;
      }
      if (
        path === scenario.endpoint &&
        route.request().method() === "POST"
      ) {
        mutationRequests += 1;
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({
            code: "PROVIDER_UNAVAILABLE",
            category: "upstream",
            message: "The result is unknown",
            retryable: true,
            trace_id: `trace-${scenario.kind}-persistence-loss`,
          }),
        });
        return;
      }
      await route.fulfill({ status: 404, body: "not mocked" });
    });

    await page.goto("/");
    await expect(page.getByRole("button", { name: "安全重试" })).toBeVisible();
    expect(mutationRequests).toBe(1);

    await page.evaluate((productId) => {
      sessionStorage.removeItem(
        `commercevision.product-brief.v2:catalog-demo:${productId}`,
      );
    }, PRODUCT_ID);
    await page.getByRole("button", { name: "安全重试" }).click();

    await expect(
      page.locator(".product-brief-panel .error-banner"),
    ).toContainText("持久化身份");
    await expect(page.getByRole("button", { name: "安全重试" })).toBeVisible();
    await page.waitForTimeout(250);
    expect(mutationRequests).toBe(1);
  });
}

test("fails closed without crashing when session storage access is denied", async ({
  page,
}) => {
  await routeCatalog(page);
  await page.addInitScript(() => {
    Object.defineProperty(window, "sessionStorage", {
      configurable: true,
      get() {
        throw new DOMException("Session storage denied", "SecurityError");
      },
    });
  });
  let productBriefRequests = 0;
  await page.route("**/api/v1/product-briefs**", async (route) => {
    productBriefRequests += 1;
    await route.fulfill({ status: 500, body: "recovery must stay stopped" });
  });
  const pageErrors: Error[] = [];
  page.on("pageerror", (error) => pageErrors.push(error));

  await page.goto("/");

  await expect(
    page.locator(".product-brief-panel .error-banner"),
  ).toContainText("会话存储");
  await expect(page.getByLabel("品牌值")).toHaveCount(0);
  expect(productBriefRequests).toBe(0);
  expect(pageErrors).toEqual([]);
});
