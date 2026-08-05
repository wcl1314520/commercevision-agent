import { expect, Page, test } from "@playwright/test";

const PRODUCT_ID = "019f8a00-0000-7000-8000-000000000101";
const PLAN_ID = "019f8a00-0000-7000-8000-000000000121";
const WORKFLOW_ID = "019f8a00-0000-7000-8000-000000000122";

const product = {
  id: PRODUCT_ID,
  workspace_id: "catalog-demo",
  source_namespace: "MANUAL",
  external_id: "PLAN-HITL-001",
  source_version: "manual-v1",
  title: "Phase 3 Hero Product",
  category_code: "beauty.skincare.serum",
  brand: "Northstar Labs",
  attributes: {},
  expires_at: null,
  version: 1,
  created_at: "2026-08-05T07:00:00Z",
  updated_at: "2026-08-05T07:00:00Z",
  skus: [],
};

function payload(scene = "Clean studio surface") {
  return {
    schema_version: "creative-plan.v1",
    directions: [
      {
        key: "hero-main",
        image_role: "HERO",
        scene,
        composition: "Centered product with negative space",
        camera: "85mm eye-level",
        lighting: "Soft key with controlled rim",
        color_direction: "Brand blue with neutral support",
        product_constraints: ["Preserve packaging geometry"],
        required_elements: ["Product label"],
        prohibited_elements: ["Unsupported claims"],
        citation_selections: [
          {
            citation_id: "citation.hero-lighting",
            reason: "Approved lighting reference",
          },
        ],
        candidate_count: 2,
        quality_targets: ["Legible label"],
        repair_scope: ["background"],
        tool_intents: [
          {
            intent_key: "generate-hero",
            tool_name: "fixture.generate_image",
            schema_version: "1",
            purpose: "Generate the approved hero direction",
            arguments: { count: 2 },
            estimated_cost_units: 2,
          },
        ],
      },
    ],
  };
}

function version(number: number, scene?: string) {
  return {
    id: `019f8a00-0000-7000-8000-${String(120 + number).padStart(12, "0")}`,
    workspace_id: "catalog-demo",
    workflow_id: WORKFLOW_ID,
    creative_plan_id: PLAN_ID,
    version_number: number,
    supersedes_version_id:
      number === 1
        ? null
        : `019f8a00-0000-7000-8000-${String(119 + number).padStart(12, "0")}`,
    source: number >= 3 ? "USER" : "AGENT",
    payload: payload(scene),
    provenance: {
      product_brief_id: "019f8a00-0000-7000-8000-000000000126",
      product_brief_version: 3,
      product_brief_sha256: "a".repeat(64),
      brand_profile_id: "019f8a00-0000-7000-8000-000000000127",
      brand_profile_version: 4,
      brand_profile_sha256: "b".repeat(64),
      retrieval_run_id: "019f8a00-0000-7000-8000-000000000128",
      retrieval_citation_ids: ["citation.hero-lighting"],
      context_policy_version: "planning-context-v1",
      context_sha256: "c".repeat(64),
      prompt_id: "creative-planner",
      prompt_revision: "fixture-v1",
      prompt_sha256: "d".repeat(64),
    },
    payload_sha256: String(number).repeat(64),
    actor_id: number >= 3 ? "catalog-workbench" : "fixture-planner",
    revision_reason: number >= 3 ? "Improve hero balance" : null,
    created_at: `2026-08-05T0${number + 6}:00:00Z`,
  };
}

function current(number: number, scene?: string) {
  const currentVersion = version(number, scene);
  return {
    head: {
      workspace_id: "catalog-demo",
      workflow_id: WORKFLOW_ID,
      creative_plan_id: PLAN_ID,
      current_version_id: currentVersion.id,
      current_version_number: number,
      version: number + 1,
      retain_until: "2099-01-01T00:00:00Z",
      created_at: "2026-08-05T07:59:00Z",
      updated_at: "2026-08-05T08:00:00Z",
    },
    version: currentVersion,
  };
}

function workflow({
  version: workflowVersion = 7,
  status = "AWAITING_PLAN_APPROVAL",
  approvals = [],
}: {
  version?: number;
  status?: string;
  approvals?: Record<string, unknown>[];
} = {}) {
  return {
    id: WORKFLOW_ID,
    workspace_id: "catalog-demo",
    created_by: "catalog-workbench",
    workflow_type: "COMMERCE_IMAGE_GENERATION",
    status,
    retention_status: "ACTIVE",
    current_node: status === "AWAITING_PLAN_APPROVAL" ? "approve_plan" : "generate",
    version: workflowVersion,
    input_data: {},
    result_data: null,
    expires_at: "2099-01-01T00:00:00Z",
    cancellation_requested_at: null,
    created_at: "2026-08-05T07:00:00Z",
    updated_at: "2026-08-05T08:00:00Z",
    steps: [],
    attempts: [],
    approvals,
  };
}

function errorEnvelope(status: number) {
  return {
    code: status === 409 ? "VERSION_CONFLICT" : "NOT_FOUND",
    message: status === 409 ? "version conflict" : "not found",
    category: status === 409 ? "conflict" : "not_found",
    retryable: false,
    details: {},
    request_id: "request-e2e",
    trace_id: "trace-e2e",
  };
}

type MockState = {
  currentVersion: number;
  scene: string;
  workflowVersion: number;
  workflowStatus: string;
  approvals: Record<string, unknown>[];
  conflictNextDecision: boolean;
  revisionRequests: { body: Record<string, unknown>; idempotencyKey: string }[];
  decisionRequests: { body: Record<string, unknown>; idempotencyKey: string }[];
  streamLastEventIds: (string | undefined)[];
};

type MockOptions = {
  readFailureStatus?: 403 | 410;
  reconnectFailureStatus?: 403 | 410;
};

async function installApi(page: Page, options: MockOptions = {}): Promise<MockState> {
  const state: MockState = {
    currentVersion: 2,
    scene: "Clean studio surface",
    workflowVersion: 7,
    workflowStatus: "AWAITING_PLAN_APPROVAL",
    approvals: [],
    conflictNextDecision: false,
    revisionRequests: [],
    decisionRequests: [],
    streamLastEventIds: [],
  };
  await page.route("**/api/web-capabilities", async (route) => {
    await route.fulfill({ json: { administrator: false } });
  });
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (request.method() === "GET" && path === "/api/v1/products") {
      await route.fulfill({ json: { items: [product], next_cursor: null } });
      return;
    }
    if (
      request.method() === "GET" &&
      path === `/api/v1/products/${PRODUCT_ID}`
    ) {
      await route.fulfill({ json: product });
      return;
    }
    if (request.method() === "GET" && path === "/api/v1/brand-profiles") {
      await route.fulfill({ json: { items: [], next_cursor: null } });
      return;
    }
    if (path === `/api/v1/workflows/${WORKFLOW_ID}/events`) {
      expect(request.headers()["x-workspace-id"]).toBe("catalog-demo");
      state.streamLastEventIds.push(request.headers()["last-event-id"]);
      if (options.reconnectFailureStatus && state.streamLastEventIds.length > 1) {
        await route.fulfill({
          status: options.reconnectFailureStatus,
          json: errorEnvelope(options.reconnectFailureStatus),
        });
        return;
      }
      if (options.reconnectFailureStatus) {
        const event = {
          event_id: "019f8a00-0000-7000-8000-000000000130",
          event_type: "workflow.human_input_received",
          schema_version: 1,
          aggregate_type: "workflow",
          aggregate_id: WORKFLOW_ID,
          aggregate_version: 7,
          occurred_at: "2026-08-05T08:00:00Z",
          trace_id: "trace-creative-plan",
          payload: { approval_type: "CREATIVE_PLAN" },
        };
        await route.fulfill({
          contentType: "text/event-stream; charset=utf-8",
          body: `retry: 100\nid: cursor-phase3-e2e\nevent: workflow.event\ndata: ${JSON.stringify(event)}\n\n`,
        });
        return;
      }
      await route.fulfill({
        contentType: "text/event-stream; charset=utf-8",
        body: "retry: 30000\n\n",
      });
      return;
    }
    if (request.method() === "GET" && path === `/api/v1/workflows/${WORKFLOW_ID}`) {
      if (options.readFailureStatus) {
        await route.fulfill({
          status: options.readFailureStatus,
          json: errorEnvelope(options.readFailureStatus),
        });
        return;
      }
      await route.fulfill({
        json: workflow({
          version: state.workflowVersion,
          status: state.workflowStatus,
          approvals: state.approvals,
        }),
      });
      return;
    }
    if (
      request.method() === "GET" &&
      path === `/api/v1/creative-plans/${PLAN_ID}`
    ) {
      await route.fulfill({ json: current(state.currentVersion, state.scene) });
      return;
    }
    if (
      request.method() === "GET" &&
      path === `/api/v1/creative-plans/${PLAN_ID}/versions`
    ) {
      const versions = Array.from(
        { length: state.currentVersion },
        (_, index) => version(state.currentVersion - index, index === 0 ? state.scene : undefined),
      );
      await route.fulfill({ json: { items: versions, next_cursor: null } });
      return;
    }
    const exactVersion = new RegExp(
      `^/api/v1/creative-plans/${PLAN_ID}/versions/([1-9][0-9]*)$`,
    ).exec(path);
    if (request.method() === "GET" && exactVersion) {
      await route.fulfill({ json: version(Number(exactVersion[1])) });
      return;
    }
    if (
      request.method() === "POST" &&
      path === `/api/v1/creative-plans/${PLAN_ID}:revise`
    ) {
      state.revisionRequests.push({
        body: request.postDataJSON() as Record<string, unknown>,
        idempotencyKey: request.headers()["idempotency-key"],
      });
      state.currentVersion = 3;
      state.scene = "Balanced marble pedestal";
      await route.fulfill({
        status: 201,
        json: current(state.currentVersion, state.scene),
      });
      return;
    }
    if (
      request.method() === "POST" &&
      path.startsWith(`/api/v1/workflows/${WORKFLOW_ID}/creative-plan:`)
    ) {
      state.decisionRequests.push({
        body: request.postDataJSON() as Record<string, unknown>,
        idempotencyKey: request.headers()["idempotency-key"],
      });
      if (state.conflictNextDecision) {
        state.conflictNextDecision = false;
        state.currentVersion = 3;
        state.workflowVersion = 8;
        await route.fulfill({ status: 409, json: errorEnvelope(409) });
        return;
      }
      const body = request.postDataJSON() as { decision: string; subject_version: number };
      state.workflowVersion += 1;
      state.workflowStatus = body.decision === "APPROVE" ? "GENERATING" : "AWAITING_PLAN_APPROVAL";
      state.approvals.push({
        id: "019f8a00-0000-7000-8000-000000000140",
        approval_type: "CREATIVE_PLAN",
        subject_id: PLAN_ID,
        subject_version: body.subject_version,
        decision: body.decision,
        approved_by: "catalog-workbench",
        expected_workflow_version: state.workflowVersion - 1,
        created_at: "2026-08-05T10:00:00Z",
      });
      await route.fulfill({
        json: workflow({
          version: state.workflowVersion,
          status: state.workflowStatus,
          approvals: state.approvals,
        }),
      });
      return;
    }
    await route.fulfill({ status: 404, json: errorEnvelope(404) });
  });
  return state;
}

async function openPlan(page: Page) {
  await page.goto("/");
  await page.getByLabel("Workflow ID").fill(WORKFLOW_ID);
  await page.getByLabel("Creative Plan ID").fill(PLAN_ID);
  await page.getByRole("button", { name: "读取审查事实" }).click();
  await expect(page.getByRole("heading", { name: "当前权威审查快照" })).toBeVisible();
}

test("creates an immutable revision and approves the exact visible version", async ({ page }) => {
  const state = await installApi(page);
  await openPlan(page);

  await expect(page.getByText("商品简报 · 版本 3")).toBeVisible();
  await expect(page.getByText("fixture.generate_image")).toBeVisible();
  await page.getByRole("button", { name: "基于当前版本创建修订" }).click();
  const edited = payload("Balanced marble pedestal");
  await page.getByLabel("方案 JSON（Creative Plan v1）").fill(JSON.stringify(edited, null, 2));
  await page.getByLabel("修订原因").fill("Improve hero balance");
  await page.getByRole("button", { name: "创建新版本" }).click();

  await expect(page.getByText("已创建新的不可变方案版本。")).toBeVisible();
  await expect(page.getByText("方案版本 3")).toBeVisible();
  expect(state.revisionRequests).toHaveLength(1);
  expect(state.revisionRequests[0].body).toMatchObject({
    workflow_id: WORKFLOW_ID,
    expected_workflow_version: 7,
    expected_head_version: 3,
    revision_reason: "Improve hero balance",
    payload: edited,
  });
  expect(state.revisionRequests[0].idempotencyKey).toMatch(
    /^web-creative-plan-revise-/,
  );

  await page.getByLabel("原因代码（可选）").fill("HUMAN_VERIFIED");
  await page.getByLabel("备注引用（可选）").fill("review://phase-3/e2e");
  await page.getByRole("button", { name: "批准方案 v3" }).click();

  await expect(page.getByText("已提交方案 v3 的批准决定。")).toBeVisible();
  await expect(page.getByText("GENERATING", { exact: true })).toBeVisible();
  expect(state.decisionRequests).toHaveLength(1);
  expect(state.decisionRequests[0].body).toEqual({
    expected_workflow_version: 7,
    subject_id: PLAN_ID,
    subject_version: 3,
    decision: "APPROVE",
    reason_code: "HUMAN_VERIFIED",
    comment_ref: "review://phase-3/e2e",
  });
  expect(state.decisionRequests[0].idempotencyKey).toMatch(
    /^web-creative-plan-approve-/,
  );
});

test("preserves draft text across refresh and never replays a conflicted decision", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  const state = await installApi(page);
  await openPlan(page);

  await page.getByRole("button", { name: "基于当前版本创建修订" }).click();
  const recoverableText = '{\n  "unfinished": true\n';
  await page.getByLabel("方案 JSON（Creative Plan v1）").fill(recoverableText);
  await page.getByLabel("修订原因").fill("Draft survives refresh");
  await page.reload();
  await expect(page.getByLabel("方案 JSON（Creative Plan v1）")).toHaveValue(
    recoverableText,
  );
  await expect(page.getByLabel("修订原因")).toHaveValue("Draft survives refresh");
  await page.getByRole("button", { name: "取消编辑" }).click();

  state.conflictNextDecision = true;
  await page.getByLabel("原因代码（可选）").fill("NEEDS_REVISION");
  await page.getByLabel("备注引用（可选）").fill("Keep this reviewer note");
  await page.getByRole("button", { name: "驳回方案 v2" }).click();

  await expect(page.getByText("版本冲突，输入已保全")).toBeVisible();
  await expect(page.getByLabel("原因代码（可选）")).toHaveValue("NEEDS_REVISION");
  await expect(page.getByLabel("备注引用（可选）")).toHaveValue(
    "Keep this reviewer note",
  );
  expect(state.decisionRequests).toHaveLength(1);
  await page.waitForTimeout(250);
  expect(state.decisionRequests).toHaveLength(1);

  const dimensions = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);
  const refreshButton = page.getByRole("button", { name: "刷新权威事实" });
  const box = await refreshButton.boundingBox();
  expect(box?.height).toBeGreaterThanOrEqual(44);
  await refreshButton.focus();
  await expect(refreshButton).toBeFocused();

  await page
    .getByLabel("Creative Plan ID")
    .fill("019f8a00-0000-7000-8000-000000000199");
  await expect(
    page.getByRole("heading", { name: "当前权威审查快照" }),
  ).toBeHidden();
  await expect(page.getByText("输入精确 Workflow 与 Creative Plan 标识")).toBeVisible();
});

test("resumes SSE from the last delivered cursor and fails closed on policy denial", async ({
  page,
}) => {
  const state = await installApi(page, { reconnectFailureStatus: 403 });
  await openPlan(page);

  await expect(
    page.locator(".warning-banner").getByText("事件流访问被拒绝", { exact: true }),
  ).toBeVisible();
  expect(state.streamLastEventIds).toEqual([undefined, "cursor-phase3-e2e"]);
  await expect(page.getByText("实时通知不可用；页面不会猜测状态，请手动刷新权威事实。")).toBeVisible();
});

test("renders retention expiry without exposing an approval surface", async ({ page }) => {
  await installApi(page, { readFailureStatus: 410 });
  await page.goto("/");
  await page.getByLabel("Workflow ID").fill(WORKFLOW_ID);
  await page.getByLabel("Creative Plan ID").fill(PLAN_ID);
  await page.getByRole("button", { name: "读取审查事实" }).click();

  await expect(page.locator(".error-banner[role='alert']")).toContainText("保留期已结束");
  await expect(page.getByRole("button", { name: /批准方案/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /驳回方案/ })).toHaveCount(0);
});
