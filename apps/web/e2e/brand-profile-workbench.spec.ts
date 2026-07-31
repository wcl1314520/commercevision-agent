import { expect, Page, test } from "@playwright/test";

import type {
  BrandProfileDraftV1,
  BrandProfileResponseV1,
  BrandProfileVersionResponseV1,
} from "../lib/generated/catalog-api";

const PRODUCT_ID = "019f8a00-0000-7000-8000-000000000101";
const SECOND_PRODUCT_ID =
  "019f8a00-0000-7000-8000-000000000102";
const PROFILE_ID = "019f8a00-0000-7000-8000-000000000141";
const SECOND_PROFILE_ID =
  "019f8a00-0000-7000-8000-000000000146";
const THIRD_PROFILE_ID =
  "019f8a00-0000-7000-8000-000000000147";
const PROFILE_VERSION_ID =
  "019f8a00-0000-7000-8000-000000000142";
const ASSET_ID = "019f8a00-0000-7000-8000-000000000143";
const ASSET_VERSION_ID =
  "019f8a00-0000-7000-8000-000000000144";
const RIGHTS_ID = "019f8a00-0000-7000-8000-000000000145";
const PROFILE_PAGE_2_CURSOR =
  "v1.e2e.cHJvZmlsZXMtcGFnZS0y.c2lnbmF0dXJl";

const product = {
  id: PRODUCT_ID,
  workspace_id: "catalog-demo",
  source_namespace: "MANUAL",
  external_id: "BRAND-PROFILE-001",
  source_version: "manual-v1",
  title: "Northstar Hydrating Serum",
  category_code: "beauty.skincare.serum",
  brand: "Northstar Labs",
  attributes: { volume_ml: 30 },
  expires_at: null,
  version: 1,
  created_at: "2026-07-30T01:00:00Z",
  updated_at: "2026-07-30T01:00:00Z",
  skus: [],
};

function draft(
  instruction = "Keep one mark-width of clear space.",
): BrandProfileDraftV1 {
  return {
    rules: [
      {
        code: "logo.clear-space",
        scope: "VISUAL",
        instruction,
      },
    ],
    approved_colors: [{ name: "Primary", value: "#1457FF" }],
    required_marks: ["Northstar wordmark"],
    prohibited_elements: ["Competitor marks"],
    tone_constraints: ["Calm"],
    copy_constraints: ["No unsupported claims"],
    purpose: "BRAND_PROFILE",
    provider: "qwen-vl",
    requires_derivative: true,
    selected_assets: [
      {
        asset_version_id: ASSET_VERSION_ID,
        role: "LOGO",
      },
    ],
  };
}

function profile({
  id = PROFILE_ID,
  profileKey = "primary",
  version,
  state,
  value,
}: {
  id?: string;
  profileKey?: string;
  version: number;
  state: BrandProfileResponseV1["state"];
  value: BrandProfileDraftV1;
}): BrandProfileResponseV1 {
  const published = state !== "DRAFT";
  return {
    id,
    workspace_id: "catalog-demo",
    brand: "Northstar Labs",
    profile_key: profileKey,
    state,
    draft: value,
    current_version_id: published ? PROFILE_VERSION_ID : null,
    current_version_number: published ? 1 : 0,
    version,
    stale_at:
      state === "NEEDS_REPUBLISH"
        ? "2026-07-30T09:00:00Z"
        : null,
    created_by: "brand-admin",
    created_at: "2026-07-30T07:00:00Z",
    updated_by: "brand-admin",
    updated_at: "2026-07-30T09:00:00Z",
  };
}

function publication(
  value: BrandProfileDraftV1,
  currentlyUsable: boolean,
): BrandProfileVersionResponseV1 {
  return {
    id: PROFILE_VERSION_ID,
    workspace_id: "catalog-demo",
    profile_id: PROFILE_ID,
    version_number: 1,
    draft: value,
    content_sha256: "a".repeat(64),
    published_by: "brand-admin",
    published_at: "2026-07-30T08:00:00Z",
    members: [
      {
        ordinal: 0,
        asset_id: ASSET_ID,
        asset_version_id: ASSET_VERSION_ID,
        role: "LOGO",
        published_rights_record_id: RIGHTS_ID,
        published_rights_record_version: 1,
        currently_usable: currentlyUsable,
        current_reason_code: currentlyUsable
          ? "AUTHORIZED"
          : "RIGHTS_REVOKED",
        current_rights_record_id: RIGHTS_ID,
        current_rights_record_version: currentlyUsable ? 1 : 2,
        decided_at: "2026-07-30T09:00:00Z",
      },
    ],
  };
}

async function routeCatalog(page: Page) {
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
  await page.route("**/api/web-capabilities", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ administrator: true }),
    });
  });
}

test("edits, validates, publishes, and audits exact Brand Profile members", async ({
  page,
}) => {
  await routeCatalog(page);
  let current = profile({ version: 1, state: "DRAFT", value: draft() });
  let published: BrandProfileVersionResponseV1 | null = null;
  const idempotencyKeys: string[] = [];

  await page.route("**/api/v1/brand-profiles**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path === "/api/v1/brand-profiles" && request.method() === "GET") {
      expect(url.searchParams.get("brand")).toBe("Northstar Labs");
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [current], next_cursor: null }),
      });
      return;
    }
    if (
      path === `/api/v1/brand-profiles/${PROFILE_ID}/draft` &&
      request.method() === "PUT"
    ) {
      const body = request.postDataJSON() as {
        expected_version: number;
        draft: BrandProfileDraftV1;
      };
      expect(body.expected_version).toBe(1);
      expect(body.draft.rules[0].instruction).toBe(
        "Use the compact mark below 320 px.",
      );
      idempotencyKeys.push(request.headers()["idempotency-key"]);
      current = profile({
        version: 2,
        state: "DRAFT",
        value: body.draft,
      });
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(current),
      });
      return;
    }
    if (
      path === `/api/v1/brand-profiles/${PROFILE_ID}:validate` &&
      request.method() === "POST"
    ) {
      expect(request.headers()["idempotency-key"]).toBeUndefined();
      expect(request.postDataJSON()).toEqual({ expected_version: 2 });
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          profile_id: PROFILE_ID,
          profile_version: 2,
          valid: true,
          decided_at: "2026-07-30T08:30:00Z",
          issues: [],
        }),
      });
      return;
    }
    if (
      path === `/api/v1/brand-profiles/${PROFILE_ID}:publish` &&
      request.method() === "POST"
    ) {
      expect(request.postDataJSON()).toEqual({ expected_version: 2 });
      idempotencyKeys.push(request.headers()["idempotency-key"]);
      current = profile({
        version: 3,
        state: "ACTIVE",
        value: current.draft,
      });
      published = publication(current.draft, true);
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify(current),
      });
      return;
    }
    if (
      path === `/api/v1/brand-profiles/${PROFILE_ID}/versions` &&
      request.method() === "GET"
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          items: published ? [published] : [],
          next_cursor: null,
        }),
      });
      return;
    }
    if (
      path === `/api/v1/brand-profiles/${PROFILE_ID}/versions/1` &&
      request.method() === "GET" &&
      published
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(published),
      });
      return;
    }
    if (
      path === `/api/v1/brand-profiles/${PROFILE_ID}` &&
      request.method() === "GET"
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(current),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "unexpected route" });
  });

  await page.goto("/");
  const panel = page.getByRole("region", { name: "品牌档案" });
  await expect(panel.getByText("草稿", { exact: true })).toBeVisible();

  await panel
    .getByLabel("规则说明")
    .fill("Use the compact mark below 320 px.");
  await panel.getByRole("button", { name: "保存草稿" }).click();
  await expect(
    panel.getByText("编辑版本 2", { exact: true }),
  ).toBeVisible();
  await expect(
    panel.getByRole("button", { name: "发布不可变版本" }),
  ).toBeDisabled();

  await panel.getByRole("button", { name: "校验当前草稿" }).click();
  await expect(
    panel.getByText("当前编辑版本的所有成员均通过实时授权校验。"),
  ).toBeVisible();
  await panel.getByRole("button", { name: "发布不可变版本" }).click();

  await expect(panel.getByText("已发布", { exact: true })).toBeVisible();
  await expect(panel.getByText("发布版本 1")).toBeVisible();
  await expect(panel.getByText("当前全部可用")).toBeVisible();
  await expect(panel.getByText("发布时 Rights")).toBeVisible();
  await expect(panel.getByText("发布时冻结规则")).toBeVisible();
  await expect(
    panel
      .getByLabel("冻结品牌规则")
      .getByText("Use the compact mark below 320 px."),
  ).toBeVisible();
  await expect(
    panel
      .getByLabel("冻结品牌规则")
      .getByText(ASSET_VERSION_ID),
  ).toBeVisible();
  expect(idempotencyKeys).toHaveLength(2);
  expect(idempotencyKeys.every(Boolean)).toBe(true);
  expect(new Set(idempotencyKeys).size).toBe(2);
});

test("preserves a conflicting local draft while showing NEEDS_REPUBLISH authority loss", async ({
  page,
}) => {
  await routeCatalog(page);
  const competingAdministratorKey = "admin-b-update-key";
  let attemptedAdministratorKey = "";
  const initialDraft = draft("Initial server rule.");
  let current = profile({
    version: 3,
    state: "ACTIVE",
    value: initialDraft,
  });
  const historical = publication(initialDraft, false);

  await page.route("**/api/v1/brand-profiles**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/v1/brand-profiles" && request.method() === "GET") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [current], next_cursor: null }),
      });
      return;
    }
    if (
      path === `/api/v1/brand-profiles/${PROFILE_ID}/draft` &&
      request.method() === "PUT"
    ) {
      attemptedAdministratorKey =
        request.headers()["idempotency-key"] ?? "";
      expect(attemptedAdministratorKey).not.toBe(
        competingAdministratorKey,
      );
      current = profile({
        version: 4,
        state: "NEEDS_REPUBLISH",
        value: draft("Authoritative server rule."),
      });
      await route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify({
          code: "VERSION_CONFLICT",
          category: "conflict",
          message: "Brand Profile version is stale",
          retryable: false,
          request_id: "request-conflict",
          trace_id: "trace-conflict",
        }),
      });
      return;
    }
    if (
      path === `/api/v1/brand-profiles/${PROFILE_ID}` &&
      request.method() === "GET"
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(current),
      });
      return;
    }
    if (
      path === `/api/v1/brand-profiles/${PROFILE_ID}/versions` &&
      request.method() === "GET"
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [historical], next_cursor: null }),
      });
      return;
    }
    if (
      path === `/api/v1/brand-profiles/${PROFILE_ID}/versions/1` &&
      request.method() === "GET"
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(historical),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "unexpected route" });
  });

  await page.goto("/");
  const panel = page.getByRole("region", { name: "品牌档案" });
  const instruction = panel.getByLabel("规则说明");
  await instruction.fill("Preserve this local conflict draft.");
  await panel.getByRole("button", { name: "保存草稿" }).click();

  await expect(panel.getByText("检测到版本冲突")).toBeVisible();
  await expect(
    panel.getByRole("button", { name: "确认放弃该待对账命令" }),
  ).toBeVisible();
  const persistedConflict = await page.evaluate(() =>
    sessionStorage.getItem(
      "commercevision:brand-profile:pending-commands:v1",
    ),
  );
  expect(persistedConflict).toContain(attemptedAdministratorKey);
  expect(persistedConflict).not.toContain(competingAdministratorKey);
  await expect(instruction).toBeDisabled();
  await expect(
    panel.getByText("当前发布已失去完整授权"),
  ).toBeVisible();
  await panel
    .getByRole("button", { name: "确认放弃该待对账命令" })
    .click();
  await panel.getByRole("button", { name: "恢复本地草稿" }).click();
  await expect(instruction).toHaveValue(
    "Preserve this local conflict draft.",
  );
  await expect(panel.getByText("当前 1 个不可用")).toBeVisible();
  await expect(
    panel.getByText("此历史成员仅保留审计价值，不能作为检索或生成授权。"),
  ).toBeVisible();
});

test("retains a first-attempt publish conflict until an administrator explicitly discards its exact key", async ({
  page,
}) => {
  await routeCatalog(page);
  const competingAdministratorKey = "admin-b-publish-key";
  let attemptedAdministratorKey = "";
  const initialDraft = draft("Publish this exact local draft.");
  let current = profile({
    version: 2,
    state: "DRAFT",
    value: initialDraft,
  });

  await page.route("**/api/v1/brand-profiles**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/v1/brand-profiles" && request.method() === "GET") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [current], next_cursor: null }),
      });
      return;
    }
    if (
      path === `/api/v1/brand-profiles/${PROFILE_ID}:validate` &&
      request.method() === "POST"
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          profile_id: PROFILE_ID,
          profile_version: 2,
          valid: true,
          decided_at: "2026-07-30T08:30:00Z",
          issues: [],
        }),
      });
      return;
    }
    if (
      path === `/api/v1/brand-profiles/${PROFILE_ID}:publish` &&
      request.method() === "POST"
    ) {
      attemptedAdministratorKey =
        request.headers()["idempotency-key"] ?? "";
      expect(attemptedAdministratorKey).not.toBe(
        competingAdministratorKey,
      );
      current = profile({
        version: 3,
        state: "ACTIVE",
        value: draft("Competing authoritative draft."),
      });
      await route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify({
          code: "VERSION_CONFLICT",
          category: "conflict",
          message: "Another administrator published first.",
          retryable: false,
          request_id: "request-publish-conflict",
          trace_id: "trace-publish-conflict",
        }),
      });
      return;
    }
    if (
      path === `/api/v1/brand-profiles/${PROFILE_ID}` &&
      request.method() === "GET"
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(current),
      });
      return;
    }
    if (path.endsWith("/versions") && request.method() === "GET") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [], next_cursor: null }),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "unexpected route" });
  });

  await page.goto("/");
  const panel = page.getByRole("region", { name: "品牌档案" });
  await panel.getByRole("button", { name: "校验当前草稿" }).click();
  await panel.getByRole("button", { name: "发布不可变版本" }).click();

  await expect(
    panel.getByRole("button", { name: "确认放弃该待对账命令" }),
  ).toBeVisible();
  const persistedConflict = await page.evaluate(() =>
    sessionStorage.getItem(
      "commercevision:brand-profile:pending-commands:v1",
    ),
  );
  expect(persistedConflict).toContain(attemptedAdministratorKey);
  expect(persistedConflict).not.toContain(competingAdministratorKey);
  await expect(panel.getByLabel("规则说明")).toBeDisabled();

  await panel
    .getByRole("button", { name: "确认放弃该待对账命令" })
    .click();
  await expect
    .poll(() =>
      page.evaluate(() =>
        sessionStorage.getItem(
          "commercevision:brand-profile:pending-commands:v1",
        ),
      ),
    )
    .toBeNull();
  await expect(panel.getByText("检测到版本冲突")).toBeVisible();
  await expect(panel.getByLabel("规则说明")).toHaveValue(
    "Competing authoritative draft.",
  );
  await panel.getByRole("button", { name: "恢复本地草稿" }).click();
  await expect(panel.getByLabel("规则说明")).toHaveValue(
    "Publish this exact local draft.",
  );
});

test("retains an accepted mutation when local version reconciliation rejects its 2xx response", async ({
  page,
}) => {
  await routeCatalog(page);
  const initial = profile({
    version: 3,
    state: "DRAFT",
    value: draft("Initial rule."),
  });
  let attemptedKey = "";

  await page.route("**/api/v1/brand-profiles**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/v1/brand-profiles" && request.method() === "GET") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [initial], next_cursor: null }),
      });
      return;
    }
    if (
      path === `/api/v1/brand-profiles/${PROFILE_ID}/draft` &&
      request.method() === "PUT"
    ) {
      attemptedKey = request.headers()["idempotency-key"] ?? "";
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(
          profile({
            version: 5,
            state: "DRAFT",
            value: draft("Accepted but non-adjacent response."),
          }),
        ),
      });
      return;
    }
    if (path.endsWith("/versions") && request.method() === "GET") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [], next_cursor: null }),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "unexpected route" });
  });

  await page.goto("/");
  const panel = page.getByRole("region", { name: "品牌档案" });
  await panel.getByLabel("规则说明").fill("Attempt this exact draft.");
  await panel.getByRole("button", { name: "保存草稿" }).click();

  await expect(
    panel.getByText(/本地状态无法完成权威对账/),
  ).toBeVisible();
  await expect(
    panel.getByRole("button", { name: "确认放弃该待对账命令" }),
  ).toHaveCount(0);
  const persisted = await page.evaluate(() =>
    sessionStorage.getItem(
      "commercevision:brand-profile:pending-commands:v1",
    ),
  );
  expect(persisted).toContain(attemptedKey);
  await expect(panel.getByLabel("规则说明")).toBeDisabled();
});

test("keeps multiline and keyed edits through focus refresh and guards profile switching", async ({
  page,
}) => {
  await routeCatalog(page);
  const primary = profile({
    version: 3,
    state: "ACTIVE",
    value: draft("Primary server rule."),
  });
  const secondary = profile({
    id: SECOND_PROFILE_ID,
    profileKey: "campaign",
    version: 1,
    state: "DRAFT",
    value: draft("Secondary server rule."),
  });
  const seasonal = profile({
    id: THIRD_PROFILE_ID,
    profileKey: "seasonal",
    version: 1,
    state: "DRAFT",
    value: draft("Third-page seasonal rule."),
  });
  let primaryReads = 0;
  let releaseMoreProfiles = () => {};
  const moreProfilesGate = new Promise<void>((resolve) => {
    releaseMoreProfiles = resolve;
  });

  await page.route("**/api/v1/brand-profiles**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path === "/api/v1/brand-profiles" && request.method() === "GET") {
      if (url.searchParams.get("cursor") === PROFILE_PAGE_2_CURSOR) {
        expect(url.searchParams.get("limit")).toBe("2");
        await moreProfilesGate;
        await route.fulfill({
          contentType: "application/json",
          body: JSON.stringify({
            items: [seasonal],
            next_cursor: null,
          }),
        });
        return;
      }
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          items: [primary, secondary],
          next_cursor: PROFILE_PAGE_2_CURSOR,
        }),
      });
      return;
    }
    if (
      path === `/api/v1/brand-profiles/${PROFILE_ID}` &&
      request.method() === "GET"
    ) {
      primaryReads += 1;
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(primary),
      });
      return;
    }
    if (
      path === `/api/v1/brand-profiles/${SECOND_PROFILE_ID}` &&
      request.method() === "GET"
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(secondary),
      });
      return;
    }
    if (
      path === `/api/v1/brand-profiles/${THIRD_PROFILE_ID}` &&
      request.method() === "GET"
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(seasonal),
      });
      return;
    }
    if (path.endsWith("/versions") && request.method() === "GET") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [], next_cursor: null }),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "unexpected route" });
  });

  await page.goto("/");
  const panel = page.getByRole("region", { name: "品牌档案" });
  const ruleCode = panel.getByLabel("规则代码");
  await ruleCode.fill("");
  await ruleCode.pressSequentially("campaign-rule");
  await expect(ruleCode).toHaveValue("campaign-rule");
  await panel.getByRole("button", { name: "添加规则" }).click();
  await panel.getByLabel("规则代码").nth(1).fill("secondary-rule");
  await panel
    .getByLabel("规则说明")
    .nth(1)
    .fill("Preserve this keyed rule after deleting its predecessor.");
  await panel.getByRole("button", { name: "删除规则" }).first().click();
  await expect(panel.getByLabel("规则代码")).toHaveValue(
    "secondary-rule",
  );
  await expect(panel.getByLabel("规则说明")).toHaveValue(
    "Preserve this keyed rule after deleting its predecessor.",
  );

  const requiredMarks = panel.getByRole("textbox", {
    name: "必需标记",
    exact: true,
  });
  await requiredMarks.fill("Northstar wordmark\nRegistration mark");
  await expect(requiredMarks).toHaveValue(
    "Northstar wordmark\nRegistration mark",
  );

  const loadMoreProfiles = panel.getByRole("button", {
    name: "加载更多档案",
  });
  await loadMoreProfiles.click();
  const loadingMoreProfiles = panel.getByRole("button", {
    name: "载入中…",
  });
  await expect(loadingMoreProfiles).toBeDisabled();
  await expect(ruleCode).toBeEnabled();
  releaseMoreProfiles();
  await expect(loadingMoreProfiles).toHaveCount(0);
  await expect(
    panel.getByLabel("当前档案").locator(
      `option[value="${THIRD_PROFILE_ID}"]`,
    ),
  ).toHaveText(/seasonal/);

  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect.poll(() => primaryReads).toBeGreaterThan(0);
  await expect(ruleCode).toHaveValue("secondary-rule");
  await expect(requiredMarks).toHaveValue(
    "Northstar wordmark\nRegistration mark",
  );

  await panel.getByLabel("当前档案").selectOption(THIRD_PROFILE_ID);
  await expect(panel.getByText("本地草稿尚未保存")).toBeVisible();
  await panel.getByRole("button", { name: "保留当前草稿" }).click();
  await expect(ruleCode).toHaveValue("secondary-rule");

  await panel.getByLabel("当前档案").selectOption(THIRD_PROFILE_ID);
  await panel.getByRole("button", { name: "丢弃草稿并切换" }).click();
  await expect(panel.getByLabel("规则说明")).toHaveValue(
    "Third-page seasonal rule.",
  );
  await expect(panel.getByLabel("当前档案")).toHaveValue(
    THIRD_PROFILE_ID,
  );
});

test("creates another profile without replacing the existing identity", async ({
  page,
}) => {
  await routeCatalog(page);
  const primary = profile({
    version: 2,
    state: "ACTIVE",
    value: draft(),
  });
  let createCalls = 0;
  let createdProfile: BrandProfileResponseV1 | null = null;

  await page.route("**/api/v1/brand-profiles**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/v1/brand-profiles" && request.method() === "GET") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          items: createdProfile ? [primary, createdProfile] : [primary],
          next_cursor: null,
        }),
      });
      return;
    }
    if (path === "/api/v1/brand-profiles" && request.method() === "POST") {
      createCalls += 1;
      const body = request.postDataJSON() as {
        brand: string;
        profile_key: string;
        draft: BrandProfileDraftV1;
      };
      expect(body.brand).toBe("Northstar Labs");
      expect(body.profile_key).toBe("campaign");
      expect(request.headers()["idempotency-key"]).toBeTruthy();
      createdProfile = profile({
        id: SECOND_PROFILE_ID,
        profileKey: "campaign",
        version: 1,
        state: "DRAFT",
        value: body.draft,
      });
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify(createdProfile),
      });
      return;
    }
    if (
      path === `/api/v1/brand-profiles/${SECOND_PROFILE_ID}` &&
      request.method() === "GET" &&
      createdProfile
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(createdProfile),
      });
      return;
    }
    if (path.endsWith("/versions") && request.method() === "GET") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [], next_cursor: null }),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "unexpected route" });
  });

  await page.goto("/");
  const panel = page.getByRole("region", { name: "品牌档案" });
  await panel.getByRole("button", { name: "新建档案" }).click();
  const createForm = panel.getByRole("form", {
    name: "创建另一份品牌档案",
  });
  await expect(createForm.getByLabel("档案键")).toHaveValue("profile-2");
  await createForm.getByLabel("档案键").fill("campaign");
  await createForm.getByRole("button", { name: "取消创建" }).click();
  await expect(panel.getByText("新建档案草稿尚未保存")).toBeVisible();
  await panel.getByRole("button", { name: "继续编辑新建档案" }).click();
  await expect(createForm.getByLabel("档案键")).toHaveValue("campaign");
  await createForm.getByRole("button", { name: "取消创建" }).click();
  await panel
    .getByRole("button", { name: "丢弃新建草稿并返回" })
    .click();
  await expect(createForm).toHaveCount(0);

  await panel.getByRole("button", { name: "新建档案" }).click();
  const confirmedCreateForm = panel.getByRole("form", {
    name: "创建另一份品牌档案",
  });
  await confirmedCreateForm.getByLabel("档案键").fill("campaign");
  await confirmedCreateForm
    .getByRole("button", { name: "创建品牌档案" })
    .click();

  await expect(panel.getByLabel("当前档案")).toHaveValue(
    SECOND_PROFILE_ID,
  );
  await expect(panel.getByLabel("当前档案").locator("option")).toHaveCount(
    2,
  );
  expect(createCalls).toBe(1);
});

test("invalidates a green validation after a deterministic publication policy rejection", async ({
  page,
}) => {
  await routeCatalog(page);
  const current = profile({
    version: 2,
    state: "DRAFT",
    value: draft(),
  });

  await page.route("**/api/v1/brand-profiles**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/v1/brand-profiles" && request.method() === "GET") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [current], next_cursor: null }),
      });
      return;
    }
    if (
      path === `/api/v1/brand-profiles/${PROFILE_ID}:validate` &&
      request.method() === "POST"
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          profile_id: PROFILE_ID,
          profile_version: 2,
          valid: true,
          decided_at: "2026-07-30T08:30:00Z",
          issues: [],
        }),
      });
      return;
    }
    if (
      path === `/api/v1/brand-profiles/${PROFILE_ID}:publish` &&
      request.method() === "POST"
    ) {
      await route.fulfill({
        status: 422,
        contentType: "application/json",
        body: JSON.stringify({
          code: "BRAND_PROFILE_PUBLICATION_REJECTED",
          category: "validation",
          message: "Foundation Asset rights were revoked.",
          retryable: false,
          request_id: "request-policy",
          trace_id: "trace-policy",
        }),
      });
      return;
    }
    if (path.endsWith("/versions") && request.method() === "GET") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [], next_cursor: null }),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "unexpected route" });
  });

  await page.goto("/");
  const panel = page.getByRole("region", { name: "品牌档案" });
  await panel.getByRole("button", { name: "校验当前草稿" }).click();
  await expect(
    panel.getByText("当前编辑版本的所有成员均通过实时授权校验。"),
  ).toBeVisible();
  await panel.getByRole("button", { name: "发布不可变版本" }).click();

  await expect(
    panel.getByText("Foundation Asset rights were revoked."),
  ).toBeVisible();
  await expect(
    panel.getByText("当前编辑版本的所有成员均通过实时授权校验。"),
  ).toHaveCount(0);
  await expect(
    panel.getByRole("button", { name: "发布不可变版本" }),
  ).toBeDisabled();
  await expect
    .poll(() =>
      page.evaluate(() =>
        sessionStorage.getItem(
          "commercevision:brand-profile:pending-commands:v1",
        ),
      ),
    )
    .toBeNull();
});

test("reconciles a response-loss publication after reload without changing its idempotency key", async ({
  page,
}) => {
  await routeCatalog(page);
  const initialDraft = draft();
  let current = profile({
    version: 2,
    state: "DRAFT",
    value: initialDraft,
  });
  const publishedVersion = publication(initialDraft, true);
  let publishCalls = 0;
  let originalKey = "";

  await page.route("**/api/v1/brand-profiles**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/v1/brand-profiles" && request.method() === "GET") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [current], next_cursor: null }),
      });
      return;
    }
    if (
      path === `/api/v1/brand-profiles/${PROFILE_ID}:validate` &&
      request.method() === "POST"
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          profile_id: PROFILE_ID,
          profile_version: 2,
          valid: true,
          decided_at: "2026-07-30T08:30:00Z",
          issues: [],
        }),
      });
      return;
    }
    if (
      path === `/api/v1/brand-profiles/${PROFILE_ID}:publish` &&
      request.method() === "POST"
    ) {
      publishCalls += 1;
      const replayKey = request.headers()["idempotency-key"];
      if (publishCalls === 1) {
        originalKey = replayKey;
        current = profile({
          version: 3,
          state: "ACTIVE",
          value: initialDraft,
        });
        await route.abort("failed");
        return;
      }
      expect(replayKey).toBe(originalKey);
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify(current),
      });
      return;
    }
    if (
      path === `/api/v1/brand-profiles/${PROFILE_ID}` &&
      request.method() === "GET"
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(current),
      });
      return;
    }
    if (
      path === `/api/v1/brand-profiles/${PROFILE_ID}/versions` &&
      request.method() === "GET"
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          items: current.state === "ACTIVE" ? [publishedVersion] : [],
          next_cursor: null,
        }),
      });
      return;
    }
    if (
      path === `/api/v1/brand-profiles/${PROFILE_ID}/versions/1` &&
      request.method() === "GET"
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(publishedVersion),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "unexpected route" });
  });

  await page.goto("/");
  const panel = page.getByRole("region", { name: "品牌档案" });
  await panel.getByRole("button", { name: "校验当前草稿" }).click();
  await panel.getByRole("button", { name: "发布不可变版本" }).click();
  await expect(
    panel.getByText(/发布结果尚未确认；原幂等键已保存/),
  ).toBeVisible();
  await expect(panel.getByLabel("规则说明")).toBeDisabled();
  await expect(
    panel.getByRole("button", { name: "校验当前草稿" }),
  ).toBeDisabled();

  const persisted = await page.evaluate(() =>
    sessionStorage.getItem(
      "commercevision:brand-profile:pending-commands:v1",
    ),
  );
  expect(persisted).toContain(originalKey);

  await page.reload();
  await expect(
    panel.getByText("已使用原幂等键恢复发布命令并确认完成。"),
  ).toBeVisible();
  await expect(panel.getByText("已发布", { exact: true })).toBeVisible();
  expect(publishCalls).toBe(2);
  await expect
    .poll(() =>
      page.evaluate(() =>
        sessionStorage.getItem(
          "commercevision:brand-profile:pending-commands:v1",
        ),
      ),
    )
    .toBeNull();
});

test("keeps the workbench fail-closed when a pending record is unverifiable", async ({
  page,
}) => {
  await routeCatalog(page);
  await page.addInitScript(() => {
    sessionStorage.setItem(
      "commercevision:brand-profile:pending-commands:v1",
      JSON.stringify([{ schema_version: 0 }]),
    );
  });
  const current = profile({
    version: 2,
    state: "DRAFT",
    value: draft(),
  });
  await page.route("**/api/v1/brand-profiles**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/v1/brand-profiles" && request.method() === "GET") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [current], next_cursor: null }),
      });
      return;
    }
    if (
      path === `/api/v1/brand-profiles/${PROFILE_ID}/versions` &&
      request.method() === "GET"
    ) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [], next_cursor: null }),
      });
      return;
    }
    await route.fulfill({ status: 500, body: "writes must stay frozen" });
  });

  await page.goto("/");
  const panel = page.getByRole("region", { name: "品牌档案" });
  await expect(
    panel.getByText(/浏览器待对账记录损坏、版本不受支持或指纹不匹配/),
  ).toBeVisible();
  await expect(panel.getByLabel("规则说明")).toBeDisabled();
  await expect(panel.getByLabel("当前档案")).toBeDisabled();
  await expect(
    panel.getByRole("button", { name: "刷新当前授权" }),
  ).toBeDisabled();
  await expect(
    panel.getByRole("button", { name: "新建档案" }),
  ).toBeDisabled();
  expect(
    await page.evaluate(() =>
      sessionStorage.getItem(
        "commercevision:brand-profile:pending-commands:v1",
      ),
    ),
  ).toBe(JSON.stringify([{ schema_version: 0 }]));
});

test("keeps reads available but disables validation when capability lookup degrades", async ({
  page,
}) => {
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
  await page.route("**/api/web-capabilities", async (route) => {
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({
        code: "GATEWAY_CONFIGURATION_UNAVAILABLE",
        message: "capability unavailable",
        retryable: true,
      }),
    });
  });
  const current = profile({
    version: 2,
    state: "DRAFT",
    value: draft(),
  });
  await page.route("**/api/v1/brand-profiles**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/v1/brand-profiles") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [current], next_cursor: null }),
      });
      return;
    }
    if (path.endsWith("/versions")) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [], next_cursor: null }),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "unexpected route" });
  });

  await page.goto("/");
  const panel = page.getByRole("region", { name: "品牌档案" });
  await expect(
    panel.getByText(/管理权限能力暂时无法确认/),
  ).toBeVisible();
  await expect(panel.getByLabel("规则说明")).toBeDisabled();
  await expect(
    panel.getByRole("button", { name: "校验当前草稿" }),
  ).toBeDisabled();
});

test("requires an explicit Brand Profile discard before switching products", async ({
  page,
}) => {
  const secondProduct = {
    ...product,
    id: SECOND_PRODUCT_ID,
    external_id: "BRAND-PROFILE-002",
    title: "Southstar Repair Cream",
    brand: "Southstar Labs",
  };
  let secondProductReads = 0;
  await page.route("**/api/v1/products**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === `/api/v1/products/${PRODUCT_ID}`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(product),
      });
      return;
    }
    if (path === `/api/v1/products/${SECOND_PRODUCT_ID}`) {
      secondProductReads += 1;
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(secondProduct),
      });
      return;
    }
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        items: [product, secondProduct],
        next_cursor: null,
      }),
    });
  });
  await page.route("**/api/web-capabilities", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ administrator: true }),
    });
  });
  const current = profile({
    version: 2,
    state: "DRAFT",
    value: draft("Original Northstar rule."),
  });
  await page.route("**/api/v1/brand-profiles**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path === "/api/v1/brand-profiles") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          items:
            url.searchParams.get("brand") === "Northstar Labs"
              ? [current]
              : [],
          next_cursor: null,
        }),
      });
      return;
    }
    if (path.endsWith("/versions")) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [], next_cursor: null }),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "unexpected route" });
  });

  await page.goto("/");
  const panel = page.getByRole("region", { name: "品牌档案" });
  await panel
    .getByLabel("规则说明")
    .fill("Unsaved Northstar product-switch draft.");
  await page
    .getByRole("button", { name: /Southstar Repair Cream/ })
    .click();

  await expect(
    page.getByText("品牌档案存在未保存的本地更改"),
  ).toBeVisible();
  expect(secondProductReads).toBe(0);
  await expect(panel.getByLabel("规则说明")).toHaveValue(
    "Unsaved Northstar product-switch draft.",
  );
  await page.getByRole("button", { name: "留在当前商品" }).click();
  await expect(
    page.getByRole("heading", {
      name: "Northstar Hydrating Serum",
      exact: true,
    }),
  ).toBeVisible();

  await page
    .getByRole("button", { name: /Southstar Repair Cream/ })
    .click();
  await page
    .getByRole("button", {
      name: "丢弃品牌档案更改并切换商品",
    })
    .click();
  await expect
    .poll(() => secondProductReads)
    .toBe(1);
  await expect(
    page.getByRole("heading", {
      name: "Southstar Repair Cream",
      exact: true,
    }),
  ).toBeVisible();
});

test("gates a product brand save before discarding local Brand Profile state", async ({
  page,
}) => {
  const renamedProduct = {
    ...product,
    brand: "Northstar Renamed",
    version: 2,
    updated_at: "2026-07-30T10:00:00Z",
  };
  let updateCalls = 0;
  await page.route("**/api/v1/products**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (
      path === `/api/v1/products/${PRODUCT_ID}` &&
      request.method() === "PUT"
    ) {
      updateCalls += 1;
      expect(request.postDataJSON()).toMatchObject({
        expected_version: 1,
        brand: "Northstar Renamed",
      });
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(renamedProduct),
      });
      return;
    }
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(
        path === `/api/v1/products/${PRODUCT_ID}`
          ? product
          : { items: [product], next_cursor: null },
      ),
    });
  });
  await page.route("**/api/web-capabilities", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ administrator: true }),
    });
  });
  const current = profile({
    version: 2,
    state: "DRAFT",
    value: draft("Original product-brand rule."),
  });
  await page.route("**/api/v1/brand-profiles**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/v1/brand-profiles") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          items:
            url.searchParams.get("brand") === "Northstar Labs"
              ? [current]
              : [],
          next_cursor: null,
        }),
      });
      return;
    }
    if (url.pathname.endsWith("/versions")) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [], next_cursor: null }),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "unexpected route" });
  });

  await page.goto("/");
  const panel = page.getByRole("region", { name: "品牌档案" });
  await panel
    .getByLabel("规则说明")
    .fill("Unsaved draft before product brand save.");
  await page.getByLabel("品牌", { exact: true }).fill("Northstar Renamed");
  await page.getByRole("button", { name: "保存商品" }).click();

  expect(updateCalls).toBe(0);
  await expect(
    page.getByText("品牌档案存在未保存的本地更改"),
  ).toBeVisible();
  await expect(panel.getByLabel("规则说明")).toHaveValue(
    "Unsaved draft before product brand save.",
  );
  await page
    .getByRole("button", {
      name: "保留品牌档案更改，暂不保存商品",
    })
    .click();
  expect(updateCalls).toBe(0);
  await expect(panel.getByLabel("规则说明")).toHaveValue(
    "Unsaved draft before product brand save.",
  );

  await page.getByRole("button", { name: "保存商品" }).click();
  await page
    .getByRole("button", {
      name: "丢弃品牌档案更改并保存商品品牌",
    })
    .click();
  await expect.poll(() => updateCalls).toBe(1);
  await expect(page.getByLabel("品牌", { exact: true })).toHaveValue(
    "Northstar Renamed",
  );
  await expect(
    page.getByText("品牌档案存在未保存的本地更改"),
  ).toHaveCount(0);
});

test("gates a concurrent product brand refresh after an SKU mutation", async ({
  page,
}) => {
  const createdSku = {
    id: "019f8a00-0000-7000-8000-000000000148",
    workspace_id: "catalog-demo",
    product_id: PRODUCT_ID,
    source_namespace: "MANUAL",
    external_id: "SKU-REFRESH-1",
    source_version: "manual-v1",
    title: "Refresh SKU",
    category_code: "beauty.skincare",
    brand: "Northstar Labs",
    attributes: {},
    expires_at: null,
    version: 1,
    created_at: "2026-07-30T10:00:00Z",
    updated_at: "2026-07-30T10:00:00Z",
  };
  const concurrentlyRenamedProduct = {
    ...product,
    brand: "Concurrent Northstar",
    version: 2,
    updated_at: "2026-07-30T10:00:00Z",
    skus: [createdSku],
  };
  let productReads = 0;
  await page.route("**/api/v1/products**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (
      path === `/api/v1/products/${PRODUCT_ID}/skus` &&
      request.method() === "POST"
    ) {
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify(createdSku),
      });
      return;
    }
    if (
      path === `/api/v1/products/${PRODUCT_ID}` &&
      request.method() === "GET"
    ) {
      productReads += 1;
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(
          productReads === 1 ? product : concurrentlyRenamedProduct,
        ),
      });
      return;
    }
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ items: [product], next_cursor: null }),
    });
  });
  await page.route("**/api/web-capabilities", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ administrator: true }),
    });
  });
  const current = profile({
    version: 2,
    state: "DRAFT",
    value: draft("Original concurrent-refresh rule."),
  });
  await page.route("**/api/v1/brand-profiles**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/v1/brand-profiles") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          items: [current],
          next_cursor: null,
        }),
      });
      return;
    }
    if (url.pathname.endsWith("/versions")) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [], next_cursor: null }),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "unexpected route" });
  });

  await page.goto("/");
  const panel = page.getByRole("region", { name: "品牌档案" });
  await panel
    .getByLabel("规则说明")
    .fill("Unsaved draft before concurrent product refresh.");
  await page.getByLabel("SKU 外部标识").fill("SKU-REFRESH-1");
  await page.getByLabel("SKU 名称", { exact: true }).fill("Refresh SKU");
  await page.getByLabel("SKU 品牌").fill("Northstar Labs");
  await page.getByRole("button", { name: "创建 SKU" }).click();

  await expect.poll(() => productReads).toBe(2);
  await expect(
    page.getByText("品牌档案存在未保存的本地更改"),
  ).toBeVisible();
  await expect(page.getByLabel("品牌", { exact: true })).toHaveValue(
    "Northstar Labs",
  );
  await expect(panel.getByLabel("规则说明")).toHaveValue(
    "Unsaved draft before concurrent product refresh.",
  );
  await page
    .getByRole("button", {
      name: "保留品牌档案更改，暂不接纳商品品牌更新",
    })
    .click();
  await expect(panel.getByLabel("规则说明")).toHaveValue(
    "Unsaved draft before concurrent product refresh.",
  );
});

test("revokes mutation controls after the first authoritative 403", async ({
  page,
}) => {
  await routeCatalog(page);
  const current = profile({
    version: 2,
    state: "DRAFT",
    value: draft(),
  });
  await page.route("**/api/v1/brand-profiles**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/v1/brand-profiles") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [current], next_cursor: null }),
      });
      return;
    }
    if (path.endsWith("/versions")) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [], next_cursor: null }),
      });
      return;
    }
    if (
      path === `/api/v1/brand-profiles/${PROFILE_ID}:validate` &&
      request.method() === "POST"
    ) {
      await route.fulfill({
        status: 403,
        contentType: "application/json",
        body: JSON.stringify({
          code: "WORKSPACE_ACCESS_DENIED",
          category: "authorization",
          message: "Membership was revoked.",
          retryable: false,
          request_id: "request-authority-loss",
          trace_id: "trace-authority-loss",
        }),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "unexpected route" });
  });

  await page.goto("/");
  const panel = page.getByRole("region", { name: "品牌档案" });
  await panel.getByRole("button", { name: "校验当前草稿" }).click();

  await expect(
    panel.getByText(/当前 Workspace 为只读模式/),
  ).toBeVisible();
  await expect(panel.getByLabel("规则说明")).toBeDisabled();
  await expect(
    panel.getByRole("button", { name: "校验当前草稿" }),
  ).toBeDisabled();
});

test("does not report a failed publication history read as an empty history", async ({
  page,
}) => {
  await routeCatalog(page);
  const current = profile({
    version: 2,
    state: "ACTIVE",
    value: draft(),
  });
  await page.route("**/api/v1/brand-profiles**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/v1/brand-profiles") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [current], next_cursor: null }),
      });
      return;
    }
    if (path.endsWith("/versions")) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          code: "SERVICE_UNAVAILABLE",
          category: "transient",
          message: "History temporarily unavailable.",
          retryable: true,
          request_id: "request-history-unavailable",
          trace_id: "trace-history-unavailable",
        }),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "unexpected route" });
  });

  await page.goto("/");
  const panel = page.getByRole("region", { name: "品牌档案" });
  await expect(panel.getByText("发布历史暂不可用")).toBeVisible();
  await expect(panel.getByText("尚未发布")).toHaveCount(0);
});

test("focuses immutable publication detail only after explicit user intent", async ({
  page,
}) => {
  await routeCatalog(page);
  const currentDraft = draft();
  const current = profile({
    version: 2,
    state: "ACTIVE",
    value: currentDraft,
  });
  const historical = publication(currentDraft, true);
  await page.route("**/api/v1/brand-profiles**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/v1/brand-profiles") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [current], next_cursor: null }),
      });
      return;
    }
    if (path.endsWith("/versions/1")) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(historical),
      });
      return;
    }
    if (path.endsWith("/versions")) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          items: [historical],
          next_cursor: null,
        }),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "unexpected route" });
  });

  await page.goto("/");
  const panel = page.getByRole("region", { name: "品牌档案" });
  const detail = panel.locator(".brand-profile-version-detail");
  await expect(detail).toBeVisible();
  await expect(detail).not.toBeFocused();

  await panel
    .getByRole("button", {
      name: "查看冻结内容与当前可用性",
    })
    .click();
  await expect(detail).toBeFocused();
});
