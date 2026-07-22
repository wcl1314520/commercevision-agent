import { expect, Page, test } from "@playwright/test";

const product = {
  id: "019f8a00-0000-7000-8000-000000000001",
  workspace_id: "catalog-demo",
  source_namespace: "MANUAL",
  external_id: "SERUM-001",
  source_version: "manual-v1",
  title: "Hydrating Serum",
  category_code: "beauty.skincare.serum",
  brand: "Northstar Labs",
  attributes: { volume_ml: 30 },
  expires_at: null,
  version: 1,
  created_at: "2026-07-22T12:00:00Z",
  updated_at: "2026-07-22T12:00:00Z",
  skus: [],
};

type BrowserSku = {
  id: string;
  external_id: string;
  title: string;
  expires_at: string | null;
  version: number;
  [key: string]: unknown;
};

type BrowserProduct = Omit<typeof product, "skus"> & {
  skus: BrowserSku[];
};

const productWithSku: BrowserProduct = {
  ...product,
  skus: [
    {
      id: "019f8a00-0000-7000-8000-000000000002",
      workspace_id: "catalog-demo",
      product_id: product.id,
      source_namespace: "MANUAL",
      external_id: "SERUM-001-30ML",
      source_version: "manual-v1",
      title: "30 ml",
      category_code: "beauty.skincare.serum",
      brand: "Northstar Labs",
      attributes: { volume_ml: 30 },
      expires_at: "2026-07-21T12:00:00Z",
      version: 1,
      created_at: "2026-07-22T12:00:00Z",
      updated_at: "2026-07-22T12:00:00Z",
    },
  ],
};

const errorEnvelope = {
  code: "VERSION_CONFLICT",
  message: "product version is stale",
  category: "conflict",
  retryable: false,
  details: {},
  request_id: "browser-request",
  trace_id: "browser-trace",
};

async function mockReadyCatalog(page: Page) {
  await page.route("**/api/v1/products**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "GET" && url.pathname === "/api/v1/products") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          items: [product],
          next_cursor: null,
        }),
      });
      return;
    }
    if (request.method() === "GET" && url.pathname.endsWith(product.id)) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(product),
      });
      return;
    }
    await route.fallback();
  });
}

test("shows deterministic loading and empty states", async ({ page }) => {
  let releaseList: (() => void) | undefined;
  const listReady = new Promise<void>((resolve) => {
    releaseList = resolve;
  });
  await page.route("**/api/v1/products**", async (route) => {
    await listReady;
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ items: [], next_cursor: null }),
    });
  });

  await page.goto("/");
  await expect(page.getByLabel("商品加载中")).toBeVisible();
  releaseList?.();
  await expect(page.getByText("还没有商品")).toBeVisible();
});

test("shows a retryable list failure and recovers", async ({ page }) => {
  let attempts = 0;
  await page.route("**/api/v1/products**", async (route) => {
    attempts += 1;
    if (attempts === 1) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          ...errorEnvelope,
          code: "SERVICE_UNAVAILABLE",
          message: "catalog unavailable",
        }),
      });
      return;
    }
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ items: [product], next_cursor: null }),
    });
  });

  await page.goto("/");
  await expect(page.locator(".error-banner")).toContainText("catalog unavailable");
  await page.getByRole("button", { name: "重试" }).click();
  await expect(page.getByText("Hydrating Serum")).toBeVisible();
});

test("reloads current product after a version conflict", async ({ page }) => {
  await mockReadyCatalog(page);
  let updateCalls = 0;
  await page.route("**/api/v1/products/**", async (route) => {
    if (route.request().method() !== "PUT") {
      await route.fallback();
      return;
    }
    updateCalls += 1;
    await route.fulfill({
      status: 409,
      contentType: "application/json",
      body: JSON.stringify(errorEnvelope),
    });
  });

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Hydrating Serum" })).toBeVisible();
  const titleInput = page.getByLabel("商品名称");
  await titleInput.fill("Hydrating Serum edited");
  await page.getByRole("button", { name: "保存商品" }).click();

  await expect(page.getByText("服务器上的版本已更新，当前表单已刷新，请重新提交。")).toBeVisible();
  expect(updateCalls).toBe(1);
});

test("creates a product with an exact request body", async ({ page }) => {
  let products: typeof product[] = [];
  let createBody: Record<string, unknown> | undefined;
  await page.route("**/api/v1/products**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "GET" && url.pathname === "/api/v1/products") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: products, next_cursor: null }),
      });
      return;
    }
    if (request.method() === "POST" && url.pathname === "/api/v1/products") {
      createBody = request.postDataJSON() as Record<string, unknown>;
      products = [product];
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify(product),
      });
      return;
    }
    if (request.method() === "GET" && url.pathname.endsWith(product.id)) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(product),
      });
      return;
    }
    await route.fallback();
  });

  await page.goto("/");
  await page.getByLabel("来源空间").fill("MANUAL");
  await page.getByLabel("外部标识").fill("SERUM-001");
  await page.getByLabel("商品名称").fill("Hydrating Serum");
  await page.getByLabel("品牌").fill("Northstar Labs");
  await page.getByRole("button", { name: "创建商品" }).click();

  await expect(page.getByText("商品已创建")).toBeVisible();
  expect(Object.keys(createBody ?? {}).sort()).toEqual(
    [
      "attributes",
      "brand",
      "category_code",
      "expires_at",
      "external_id",
      "source_namespace",
      "source_version",
      "title",
    ].sort(),
  );
});

test("updates a product with only ProductUpdateRequestV1 fields", async ({ page }) => {
  await mockReadyCatalog(page);
  let updateBody: Record<string, unknown> | undefined;
  await page.route("**/api/v1/products/**", async (route) => {
    if (route.request().method() !== "PUT") {
      await route.fallback();
      return;
    }
    updateBody = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ ...product, title: "Hydrating Serum edited", version: 2 }),
    });
  });

  await page.goto("/");
  await page.getByLabel("商品名称").fill("Hydrating Serum edited");
  await page.getByRole("button", { name: "保存商品" }).click();

  await expect(page.getByText("商品已保存")).toBeVisible();
  expect(Object.keys(updateBody ?? {}).sort()).toEqual(
    [
      "attributes",
      "brand",
      "category_code",
      "expected_version",
      "expires_at",
      "source_version",
      "title",
    ].sort(),
  );
  expect(updateBody).not.toHaveProperty("source_namespace");
  expect(updateBody).not.toHaveProperty("external_id");
});

test("creates, updates, and deletes SKU with exact request bodies", async ({ page }) => {
  let currentProduct = { ...productWithSku };
  let createBody: Record<string, unknown> | undefined;
  let updateBody: Record<string, unknown> | undefined;
  let deleteBody: Record<string, unknown> | undefined;
  await page.route("**/api/v1/products**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "GET" && url.pathname === "/api/v1/products") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [currentProduct], next_cursor: null }),
      });
      return;
    }
    if (request.method() === "GET" && url.pathname.endsWith(product.id)) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(currentProduct),
      });
      return;
    }
    if (request.method() === "POST" && url.pathname.endsWith("/skus")) {
      createBody = request.postDataJSON() as Record<string, unknown>;
      const createdSku = {
        ...productWithSku.skus[0],
        id: "019f8a00-0000-7000-8000-000000000003",
        external_id: "SERUM-001-50ML",
        title: "50 ml",
        expires_at: null,
      };
      currentProduct = { ...currentProduct, skus: [...(currentProduct.skus ?? []), createdSku] };
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify(createdSku),
      });
      return;
    }
    await route.fallback();
  });
  await page.route("**/api/v1/products/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "PUT" && url.pathname.endsWith(productWithSku.skus[0].id)) {
      updateBody = request.postDataJSON() as Record<string, unknown>;
      currentProduct = {
        ...currentProduct,
        skus: currentProduct.skus.map((sku) =>
          sku.id === productWithSku.skus[0].id ? { ...sku, title: "30 ml refill", version: 2 } : sku,
        ),
      };
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(currentProduct.skus[0]),
      });
      return;
    }
    if (request.method() === "DELETE" && url.pathname.endsWith(productWithSku.skus[0].id)) {
      deleteBody = request.postDataJSON() as Record<string, unknown>;
      currentProduct = {
        ...currentProduct,
        skus: currentProduct.skus.filter((sku) => sku.id !== productWithSku.skus[0].id),
      };
      await route.fulfill({ status: 204 });
      return;
    }
    await route.fallback();
  });

  await page.goto("/");
  await expect(page.getByText("已过期")).toBeVisible();
  await page.getByLabel("SKU 名称").first().fill("30 ml refill");
  await page.getByLabel("SKU 过期时间（可选）").first().fill("2026-07-30T12:00");
  await page.getByRole("button", { name: "保存 SKU" }).click();
  await expect(page.getByText("SKU 已保存")).toBeVisible();
  expect(Object.keys(updateBody ?? {}).sort()).toEqual(
    [
      "attributes",
      "brand",
      "category_code",
      "expected_version",
      "expires_at",
      "source_version",
      "title",
    ].sort(),
  );
  expect(updateBody).not.toHaveProperty("source_namespace");
  expect(updateBody).not.toHaveProperty("external_id");
  expect(updateBody?.expires_at).not.toBe("2026-07-21T12:00:00Z");

  await page.getByRole("button", { name: "删除 SKU" }).click();
  await expect(page.getByText("SKU 已删除")).toBeVisible();
  expect(deleteBody).toEqual({ expected_version: 2 });

  await page.getByLabel("SKU 外部标识").fill("SERUM-001-50ML");
  await page.getByLabel("SKU 品牌").fill("Northstar Labs");
  await page.getByLabel("SKU 名称").last().fill("50 ml");
  await page.getByRole("button", { name: "创建 SKU" }).click();
  await expect(page.getByText("SKU 已创建")).toBeVisible();
  expect(Object.keys(createBody ?? {}).sort()).toEqual(
    [
      "attributes",
      "brand",
      "category_code",
      "expires_at",
      "external_id",
      "source_namespace",
      "source_version",
      "title",
    ].sort(),
  );
});

test("shows and recovers from a product detail-load failure", async ({ page }) => {
  let detailAttempts = 0;
  await page.route("**/api/v1/products**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "GET" && url.pathname === "/api/v1/products") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [product], next_cursor: null }),
      });
      return;
    }
    if (request.method() === "GET" && url.pathname.endsWith(product.id)) {
      detailAttempts += 1;
      if (detailAttempts === 1) {
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({
            ...errorEnvelope,
            code: "SERVICE_UNAVAILABLE",
            message: "detail unavailable",
          }),
        });
        return;
      }
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(product),
      });
      return;
    }
    await route.fallback();
  });

  await page.goto("/");
  await expect(page.locator(".error-banner")).toContainText("detail unavailable");
  await page.getByRole("button", { name: "重试" }).click();
  await expect(page.getByRole("heading", { name: "Hydrating Serum" })).toBeVisible();
});

test("keeps the usable workbench within a desktop viewport", async ({ page }) => {
  await mockReadyCatalog(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "商品目录工作台" })).toBeVisible();
  await expect(page.getByRole("button", { name: "创建 SKU" })).toBeVisible();
  const dimensions = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);
});

test("keeps the usable workbench within a mobile viewport", async ({ page }) => {
  await mockReadyCatalog(page);
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "商品目录工作台" })).toBeVisible();
  await expect(page.getByLabel("商品名称")).toBeVisible();
  const dimensions = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);
});
