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

const uploadSession = {
  id: "019f8a00-0000-7000-8000-000000000010",
  workspace_id: "catalog-demo",
  reserved_asset_id: "019f8a00-0000-7000-8000-000000000011",
  retention_class: "FOUNDATION",
  asset_kind: "IMAGE",
  filename: "pixel.png",
  declared_mime: "image/png",
  expected_byte_length: 68,
  expected_sha256: "e69f0bc5c2cc7d75ef3b102a3208e6b5b8e23f4cb3c6b8f4f53d86f7f6322f84",
  workflow_id: null,
  product_id: product.id,
  sku_id: null,
  category: product.category_code,
  role: "product-primary",
  upload_policy_version: "direct-put-v1",
  integrity_policy_version: "image-integrity-v1",
  status: "OPEN",
  failure_code: null,
  asset_version_id: null,
  validation_operation_id: null,
  expires_at: "2026-07-24T13:00:00Z",
  version: 1,
  created_at: "2026-07-24T12:45:00Z",
  updated_at: "2026-07-24T12:45:00Z",
};

const assetVersion = {
  id: "019f8a00-0000-7000-8000-000000000012",
  workspace_id: "catalog-demo",
  asset_id: uploadSession.reserved_asset_id,
  version_number: 1,
  upload_session_id: uploadSession.id,
  filename: "pixel.png",
  sha256: uploadSession.expected_sha256,
  byte_size: 68,
  declared_mime: "image/png",
  detected_mime: "image/png",
  image_format: "PNG",
  width: 1,
  height: 1,
  frame_count: 1,
  category: product.category_code,
  role: "product-primary",
  integrity_policy_version: "image-integrity-v1",
  validation_policy_version: "asset-validation-v1",
  object_state: "QUARANTINED",
  created_at: "2026-07-24T12:45:02Z",
};

const quarantinedAsset = {
  id: uploadSession.reserved_asset_id,
  workspace_id: "catalog-demo",
  retention_class: "FOUNDATION",
  asset_kind: "IMAGE",
  workflow_id: null,
  product_id: product.id,
  sku_id: null,
  status: "QUARANTINED",
  current_version_id: assetVersion.id,
  retention_deadline: null,
  version: 1,
  created_at: "2026-07-24T12:45:02Z",
  updated_at: "2026-07-24T12:45:02Z",
  current_version: assetVersion,
};

const finalizeResponse = {
  upload_session: {
    ...uploadSession,
    status: "FINALIZED",
    asset_version_id: assetVersion.id,
    validation_operation_id: "019f8a00-0000-7000-8000-000000000013",
    version: 3,
    updated_at: "2026-07-24T12:45:02Z",
  },
  asset: quarantinedAsset,
  asset_version: assetVersion,
  validation_operation: {
    id: "019f8a00-0000-7000-8000-000000000013",
    state: "PENDING",
    target_id: assetVersion.id,
    target_version: 1,
    version: 1,
  },
};

const durableOperation = {
  id: finalizeResponse.validation_operation.id,
  workspace_id: "catalog-demo",
  kind: "ASSET_VALIDATION",
  target_type: "ASSET_VERSION",
  target_id: assetVersion.id,
  target_version: 1,
  input_hash: "a".repeat(64),
  input_ref: null,
  output_ref: null,
  provider_request_id: null,
  state: "PENDING",
  lease_owner: null,
  lease_expires_at: null,
  attempt_count: 0,
  max_attempts: 3,
  next_attempt_at: "2026-07-24T12:45:03Z",
  execution_deadline_at: "2026-07-24T13:45:02Z",
  reconciliation_attempt_count: 0,
  max_reconciliation_attempts: 2,
  next_reconciliation_at: null,
  reconciliation_started_at: null,
  reconciliation_deadline_at: null,
  reconciliation_required: false,
  reconciliation_outcome: "NOT_REQUIRED",
  dead_letter_id: null,
  replay_source_dead_letter_id: null,
  replay_attempt: 0,
  recovery_generation: 0,
  recovery_consumed_generation: 0,
  error: null,
  created_at: "2026-07-24T12:45:02Z",
  updated_at: "2026-07-24T12:45:02Z",
  last_attempt_at: null,
  started_at: null,
  completed_at: null,
  version: 1,
};

const pngBytes = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
  "base64",
);

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

async function registerFoundationFile(
  page: Page,
  {
    assetKind,
    bytes,
    declaredMime,
    fileLabel,
    filename,
    role,
  }: {
    assetKind: "LORA" | "PROMPT_TEMPLATE" | "MODEL_CONFIGURATION";
    bytes: Buffer;
    declaredMime: string;
    fileLabel: string;
    filename: string;
    role: string;
  },
) {
  await mockReadyCatalog(page);
  let createBody: Record<string, unknown> | undefined;
  let directBody: Buffer | null = null;
  let openSession: Record<string, unknown> | undefined;

  await page.route("**/api/v1/upload-sessions", async (route) => {
    createBody = route.request().postDataJSON() as Record<string, unknown>;
    openSession = {
      ...uploadSession,
      asset_kind: assetKind,
      filename,
      declared_mime: declaredMime,
      expected_byte_length: bytes.length,
      expected_sha256: createBody.sha256,
      role,
      upload: {
        method: "PUT",
        url: `https://object-storage.example/${assetKind.toLowerCase()}-upload`,
        required_headers: {
          "Content-Type": declaredMime,
          "Content-Length": String(bytes.length),
          "x-amz-meta-upload-session-id": uploadSession.id,
        },
        maximum_bytes: bytes.length,
        checksum_algorithm: "SHA-256",
        expires_at: uploadSession.expires_at,
      },
    };
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify(openSession),
    });
  });
  await page.route("https://object-storage.example/**", async (route) => {
    directBody = route.request().postDataBuffer();
    await route.fulfill({ status: 200, body: "" });
  });
  await page.route(
    `**/api/v1/upload-sessions/${uploadSession.id}:finalize`,
    async (route) => {
      if (!openSession || !createBody) {
        throw new Error("upload session must be created before finalize");
      }
      const registeredVersion = {
        ...assetVersion,
        filename,
        sha256: createBody.sha256,
        byte_size: bytes.length,
        declared_mime: declaredMime,
        detected_mime: null,
        image_format: null,
        width: null,
        height: null,
        frame_count: null,
        role,
      };
      const registeredAsset = {
        ...quarantinedAsset,
        asset_kind: assetKind,
        current_version: registeredVersion,
      };
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({
          upload_session: {
            ...openSession,
            upload: undefined,
            status: "FINALIZED",
            asset_version_id: assetVersion.id,
            validation_operation_id: durableOperation.id,
            version: 3,
          },
          asset: registeredAsset,
          asset_version: registeredVersion,
          validation_operation: finalizeResponse.validation_operation,
        }),
      });
    },
  );
  await page.route(
    `**/api/v1/assets/${quarantinedAsset.id}/validation`,
    async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          asset_id: quarantinedAsset.id,
          asset_version_id: assetVersion.id,
          asset_status: "PENDING_RIGHTS",
          validation_policy_version: "asset-validation-v1",
          operation: {
            id: durableOperation.id,
            state: "SUCCEEDED",
            attempt_count: 1,
            max_attempts: 3,
            next_attempt_at: null,
            retryable: false,
            failure_code: null,
            failure_category: null,
            completed_at: "2026-07-24T12:45:05Z",
          },
          stages: [],
        }),
      });
    },
  );

  await page.goto("/");
  await page.getByLabel("资产类型").selectOption(assetKind);
  await page.getByLabel(fileLabel, { exact: true }).setInputFiles({
    name: filename,
    mimeType: declaredMime,
    buffer: bytes,
  });
  await page.getByRole("button", { name: "上传并登记" }).click();
  await expect(page.locator(".asset-status")).toHaveText("隔离区");

  return {
    createBody: () => createBody,
    directBody: () => directBody,
  };
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

test("uploads image bytes only to the constrained object-storage request", async ({
  page,
}) => {
  await mockReadyCatalog(page);
  let createBody: Record<string, unknown> | undefined;
  let finalizeBody: Record<string, unknown> | undefined;
  let directBody: Buffer | null = null;
  let directHeaders: Record<string, string> = {};

  await page.route("**/api/v1/upload-sessions", async (route) => {
    createBody = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        ...uploadSession,
        upload: {
          method: "PUT",
          url: "https://object-storage.example/opaque-upload?signature=one-use",
          required_headers: {
            "Content-Type": "image/png",
            "Content-Length": "68",
            "x-amz-checksum-sha256": "5p8LxcLMfXXvOxAqMgjmtbjiP0yzxrj09T2G9/YyL4Q=",
            "If-None-Match": "*",
            "x-amz-meta-upload-session-id": uploadSession.id,
          },
          maximum_bytes: 68,
          checksum_algorithm: "SHA-256",
          expires_at: uploadSession.expires_at,
        },
      }),
    });
  });
  await page.route("https://object-storage.example/**", async (route) => {
    directBody = route.request().postDataBuffer();
    directHeaders = route.request().headers();
    await route.fulfill({ status: 200, body: "" });
  });
  await page.route(
    `**/api/v1/upload-sessions/${uploadSession.id}:finalize`,
    async (route) => {
      finalizeBody = route.request().postDataJSON() as Record<string, unknown>;
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify(finalizeResponse),
      });
    },
  );

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "商品素材" })).toBeVisible();
  await page.getByLabel("商品图片", { exact: true }).setInputFiles({
    name: "pixel.png",
    mimeType: "image/png",
    buffer: pngBytes,
  });
  await page.getByRole("button", { name: "上传并登记" }).click();

  await expect(page.locator(".asset-status")).toHaveText("隔离区");
  await expect(page.getByText("QUARANTINED").last()).toBeVisible();
  expect(directBody).toEqual(pngBytes);
  expect(directHeaders["x-amz-meta-upload-session-id"]).toBe(uploadSession.id);
  expect(createBody).toMatchObject({
    retention_class: "FOUNDATION",
    asset_kind: "IMAGE",
    filename: "pixel.png",
    declared_mime: "image/png",
    byte_length: 68,
    product_id: product.id,
  });
  expect(finalizeBody).toEqual({ expected_version: 1 });
});

test("registers a SafeTensors LoRA as a Foundation Asset", async ({ page }) => {
  const header = Buffer.from(
    JSON.stringify({
      weight: {
        dtype: "F32",
        shape: [1],
        data_offsets: [0, 4],
      },
    }),
    "utf8",
  );
  const headerLength = Buffer.alloc(8);
  headerLength.writeBigUInt64LE(BigInt(header.length));
  const bytes = Buffer.concat([headerLength, header, Buffer.alloc(4)]);
  const registration = await registerFoundationFile(page, {
    assetKind: "LORA",
    bytes,
    declaredMime: "application/octet-stream",
    fileLabel: "LoRA SafeTensors",
    filename: "studio-style.safetensors",
    role: "generation-lora",
  });

  expect(registration.directBody()).toEqual(bytes);
  expect(registration.createBody()).toMatchObject({
    retention_class: "FOUNDATION",
    asset_kind: "LORA",
    filename: "studio-style.safetensors",
    declared_mime: "application/octet-stream",
    byte_length: bytes.length,
    role: "generation-lora",
  });
  await expect(page.locator(".asset-preview")).toContainText(
    "studio-style.safetensors",
  );
  await expect(page.locator(".asset-preview img")).toHaveCount(0);
});

test("registers a prompt JSON template as a Foundation Asset", async ({
  page,
}) => {
  const bytes = Buffer.from(
    JSON.stringify({
      schema_version: "commercevision.prompt-template.v1",
      name: "catalog",
      template: "Create {{ product_name }}",
      variables: [{ name: "product_name", required: true }],
    }),
    "utf8",
  );
  const registration = await registerFoundationFile(page, {
    assetKind: "PROMPT_TEMPLATE",
    bytes,
    declaredMime: "application/json",
    fileLabel: "提示词模板",
    filename: "catalog.prompt.json",
    role: "generation-prompt-template",
  });

  expect(registration.directBody()).toEqual(bytes);
  expect(registration.createBody()).toMatchObject({
    retention_class: "FOUNDATION",
    asset_kind: "PROMPT_TEMPLATE",
    filename: "catalog.prompt.json",
    declared_mime: "application/json",
    byte_length: bytes.length,
    role: "generation-prompt-template",
  });
  await expect(page.locator(".asset-preview")).toContainText(
    "catalog.prompt.json",
  );
  await expect(page.locator(".asset-preview")).toContainText("提示词模板");
});

test("replays a lost create-session response with its persisted request identity", async ({
  page,
}) => {
  await mockReadyCatalog(page);
  const storageKey = `commercevision:upload:catalog-demo:${product.id}`;
  const createAttempts: Array<{
    body: Record<string, unknown>;
    idempotencyKey: string | undefined;
    persisted: string | null;
  }> = [];

  await page.route("**/api/v1/upload-sessions", async (route) => {
    const request = route.request();
    createAttempts.push({
      body: request.postDataJSON() as Record<string, unknown>,
      idempotencyKey: request.headers()["idempotency-key"],
      persisted: await page.evaluate(
        (key) => localStorage.getItem(key),
        storageKey,
      ),
    });
    if (createAttempts.length === 1) {
      await route.abort("failed");
      return;
    }
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        ...uploadSession,
        upload: {
          method: "PUT",
          url: "https://object-storage.example/opaque-upload?signature=one-use",
          required_headers: {
            "Content-Type": "image/png",
            "Content-Length": "68",
          },
          maximum_bytes: 68,
          checksum_algorithm: "SHA-256",
          expires_at: uploadSession.expires_at,
        },
      }),
    });
  });
  await page.goto("/");
  await page.getByLabel("商品图片", { exact: true }).setInputFiles({
    name: "pixel.png",
    mimeType: "image/png",
    buffer: pngBytes,
  });
  await page.getByRole("button", { name: "上传并登记" }).click();
  await expect(page.locator(".error-banner")).toBeVisible();
  await page.reload();
  await expect(page.locator(".asset-status")).toHaveText("等待上传");
  const attemptsAfterResponseRecovery = createAttempts.length;
  await page.reload();
  await expect(page.locator(".asset-status")).toHaveText("等待上传");

  expect(createAttempts.length).toBeGreaterThan(attemptsAfterResponseRecovery);
  expect(createAttempts[0].idempotencyKey).toBeTruthy();
  for (const replay of createAttempts.slice(1)) {
    expect(replay.idempotencyKey).toBe(createAttempts[0].idempotencyKey);
    expect(replay.body).toEqual(createAttempts[0].body);
  }
  expect(JSON.parse(createAttempts[0].persisted ?? "null")).toMatchObject({
    stage: "CREATING",
    createIdempotencyKey: createAttempts[0].idempotencyKey,
    createRequest: createAttempts[0].body,
  });
});

test("manually replays an OPEN create response with its persisted identity", async ({
  page,
}) => {
  await mockReadyCatalog(page);
  const createIdempotencyKey = "web-upload-create-open-retry-0001";
  const createRequest = {
    retention_class: "FOUNDATION",
    asset_kind: "IMAGE",
    filename: "pixel.png",
    declared_mime: "image/png",
    byte_length: 68,
    sha256: "431ced6916a2a21a156e38701afe55bbd7f88969fbbfc56d7fe099d47f265460",
    workflow_id: null,
    product_id: product.id,
    sku_id: null,
    category: product.category_code,
    role: "product-reference",
  };
  const createAttempts: Array<{
    body: Record<string, unknown>;
    idempotencyKey: string | undefined;
  }> = [];
  let responseAvailable = false;
  await page.addInitScript(
    ({ key, value }) => localStorage.setItem(key, JSON.stringify(value)),
    {
      key: `commercevision:upload:catalog-demo:${product.id}`,
      value: {
        sessionId: uploadSession.id,
        finalizeIdempotencyKey: "web-upload-finalize-open-retry-0001",
        finalizeExpectedVersion: 1,
        stage: "OPEN",
        createIdempotencyKey,
        createRequest,
      },
    },
  );
  await page.route("**/api/v1/upload-sessions", async (route) => {
    createAttempts.push({
      body: route.request().postDataJSON() as Record<string, unknown>,
      idempotencyKey: route.request().headers()["idempotency-key"],
    });
    if (!responseAvailable) {
      await route.abort("failed");
      return;
    }
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        ...uploadSession,
        expected_sha256: createRequest.sha256,
        upload: {
          method: "PUT",
          url: "https://object-storage.example/opaque-upload?signature=one-use",
          required_headers: {
            "Content-Type": "image/png",
            "Content-Length": "68",
          },
          maximum_bytes: 68,
          checksum_algorithm: "SHA-256",
          expires_at: uploadSession.expires_at,
        },
      }),
    });
  });
  await page.route("https://object-storage.example/**", async (route) => {
    await route.fulfill({ status: 200, body: "" });
  });
  await page.route(
    `**/api/v1/upload-sessions/${uploadSession.id}:finalize`,
    async (route) => {
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify(finalizeResponse),
      });
    },
  );

  await page.goto("/");
  await expect(page.locator(".error-banner")).toBeVisible();
  await expect(page.getByLabel("素材角色")).toHaveValue("product-reference");
  await page.getByLabel("商品图片", { exact: true }).setInputFiles({
    name: "pixel.png",
    mimeType: "image/png",
    buffer: pngBytes,
  });
  responseAvailable = true;
  await page.getByRole("button", { name: "上传并登记" }).click();
  await expect(page.locator(".asset-status")).toHaveText("隔离区");

  expect(createAttempts.length).toBeGreaterThanOrEqual(2);
  for (const attempt of createAttempts) {
    expect(attempt.idempotencyKey).toBe(createIdempotencyKey);
    expect(attempt.body).toEqual(createRequest);
  }
});

test("creates a new session identity only after explicitly abandoning a lost response", async ({
  page,
}) => {
  await mockReadyCatalog(page);
  const storageKey = `commercevision:upload:catalog-demo:${product.id}`;
  const idempotencyKeys: Array<string | undefined> = [];
  await page.route("**/api/v1/upload-sessions", async (route) => {
    idempotencyKeys.push(route.request().headers()["idempotency-key"]);
    await route.abort("failed");
  });

  await page.goto("/");
  const fileInput = page.getByLabel("商品图片", { exact: true });
  await fileInput.setInputFiles({
    name: "pixel.png",
    mimeType: "image/png",
    buffer: pngBytes,
  });
  await page.getByRole("button", { name: "上传并登记" }).click();
  await expect(page.locator(".error-banner")).toBeVisible();

  await page.getByRole("button", { name: "放弃本次上传" }).click();
  expect(
    await page.evaluate((key) => localStorage.getItem(key), storageKey),
  ).toBeNull();

  await fileInput.setInputFiles({
    name: "pixel.png",
    mimeType: "image/png",
    buffer: pngBytes,
  });
  await page.getByRole("button", { name: "上传并登记" }).click();
  await expect.poll(() => idempotencyKeys.length).toBe(2);
  expect(idempotencyKeys[1]).not.toBe(idempotencyKeys[0]);
});

test("recovers persisted finalize identity after a browser refresh", async ({
  page,
}) => {
  await mockReadyCatalog(page);
  const finalizeIdempotencyKey = "web-upload-finalize-persisted-0001";
  let finalizeBody: Record<string, unknown> | undefined;
  let finalizeHeader: string | undefined;

  await page.addInitScript(
    ({ key, value }) => localStorage.setItem(key, JSON.stringify(value)),
    {
      key: `commercevision:upload:catalog-demo:${product.id}`,
      value: {
        sessionId: uploadSession.id,
        finalizeIdempotencyKey,
        finalizeExpectedVersion: 1,
        stage: "FINALIZING",
      },
    },
  );
  await page.route(
    `**/api/v1/upload-sessions/${uploadSession.id}`,
    async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ ...uploadSession, version: 3 }),
      });
    },
  );
  await page.route(
    `**/api/v1/upload-sessions/${uploadSession.id}:finalize`,
    async (route) => {
      finalizeBody = route.request().postDataJSON() as Record<string, unknown>;
      finalizeHeader = route.request().headers()["idempotency-key"];
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify(finalizeResponse),
      });
    },
  );

  await page.goto("/");

  await expect(page.locator(".asset-status")).toHaveText("隔离区");
  expect(finalizeBody).toEqual({ expected_version: 1 });
  expect(finalizeHeader).toBe(finalizeIdempotencyKey);
  const persisted = await page.evaluate(
    (key) => localStorage.getItem(key),
    `commercevision:upload:catalog-demo:${product.id}`,
  );
  expect(persisted).not.toContain("signature");
  expect(persisted).not.toContain("object-storage");
});

test("drops physical storage fields while recovering persisted upload state", async ({
  page,
}) => {
  await mockReadyCatalog(page);
  const storageKey = `commercevision:upload:catalog-demo:${product.id}`;
  await page.addInitScript(
    ({ key, value }) => localStorage.setItem(key, JSON.stringify(value)),
    {
      key: storageKey,
      value: {
        sessionId: uploadSession.id,
        finalizeIdempotencyKey: "web-upload-finalize-sanitized-0001",
        finalizeExpectedVersion: 1,
        stage: "FINALIZING",
        upload: {
          url: "https://object-storage.example/private?signature=must-drop",
        },
        objectKey: "quarantine/private-object-key",
      },
    },
  );
  await page.route(
    `**/api/v1/upload-sessions/${uploadSession.id}`,
    async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(finalizeResponse.upload_session),
      });
    },
  );
  await page.route(`**/api/v1/assets/${quarantinedAsset.id}`, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(quarantinedAsset),
    });
  });
  await page.route(
    `**/api/v1/assets/${quarantinedAsset.id}/validation`,
    async (route) => {
      await route.fulfill({ status: 404, body: "" });
    },
  );

  await page.goto("/");
  await expect(page.locator(".asset-status")).toHaveText("隔离区");
  const persisted = await page.evaluate(
    (key) => localStorage.getItem(key),
    storageKey,
  );
  expect(persisted).not.toContain("signature");
  expect(persisted).not.toContain("object-storage");
  expect(persisted).not.toContain("objectKey");
  expect(persisted).not.toContain("quarantine/");
});

test("retries finalize with the persisted session version after VERSION_CONFLICT", async ({
  page,
}) => {
  await mockReadyCatalog(page);
  const storageKey = `commercevision:upload:catalog-demo:${product.id}`;
  const finalizeIdempotencyKey = "web-upload-finalize-version-retry-0001";
  const finalizeRequests: Array<{
    body: Record<string, unknown>;
    idempotencyKey: string | undefined;
  }> = [];
  let conflicted = false;

  await page.addInitScript(
    ({ key, value }) => localStorage.setItem(key, JSON.stringify(value)),
    {
      key: storageKey,
      value: {
        sessionId: uploadSession.id,
        finalizeIdempotencyKey,
        finalizeExpectedVersion: 1,
        stage: "FINALIZING",
      },
    },
  );
  await page.route(
    `**/api/v1/upload-sessions/${uploadSession.id}`,
    async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          ...uploadSession,
          version: conflicted ? 3 : 1,
        }),
      });
    },
  );
  await page.route(
    `**/api/v1/upload-sessions/${uploadSession.id}:finalize`,
    async (route) => {
      finalizeRequests.push({
        body: route.request().postDataJSON() as Record<string, unknown>,
        idempotencyKey: route.request().headers()["idempotency-key"],
      });
      if (!conflicted) {
        conflicted = true;
        await route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({
            ...errorEnvelope,
            message: "upload session version is stale",
          }),
        });
        return;
      }
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify(finalizeResponse),
      });
    },
  );

  await page.goto("/");
  await expect(page.locator(".error-banner")).toContainText("上传状态已更新");
  const reconciled = JSON.parse((await page.evaluate(
    (key) => localStorage.getItem(key),
    storageKey,
  )) ?? "null") as {
    finalizeAttempt: {
      idempotencyKey: string;
      request: { expected_version: number };
    };
  };
  expect(reconciled).toMatchObject({
    schemaVersion: 1,
    finalizeAttempt: {
      request: { expected_version: 3 },
    },
    stage: "FINALIZING",
  });
  expect(reconciled.finalizeAttempt.idempotencyKey).not.toBe(
    finalizeIdempotencyKey,
  );

  await page.getByRole("button", { name: "重试登记" }).click();
  await expect(page.locator(".asset-status")).toHaveText("隔离区");
  expect(finalizeRequests).toEqual([
    {
      body: { expected_version: 1 },
      idempotencyKey: finalizeIdempotencyKey,
    },
    {
      body: { expected_version: 3 },
      idempotencyKey: reconciled.finalizeAttempt.idempotencyKey,
    },
  ]);
});

test("recovers when refresh interrupts the direct-upload completion boundary", async ({
  page,
}) => {
  await mockReadyCatalog(page);
  const finalizeIdempotencyKey = "web-upload-finalize-interrupted-put-0001";
  let finalizeRequests = 0;

  await page.addInitScript(
    ({ key, value }) => localStorage.setItem(key, JSON.stringify(value)),
    {
      key: `commercevision:upload:catalog-demo:${product.id}`,
      value: {
        sessionId: uploadSession.id,
        finalizeIdempotencyKey,
        finalizeExpectedVersion: 1,
        stage: "UPLOADING",
      },
    },
  );
  await page.route(
    `**/api/v1/upload-sessions/${uploadSession.id}`,
    async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(uploadSession),
      });
    },
  );
  await page.route(
    `**/api/v1/upload-sessions/${uploadSession.id}:finalize`,
    async (route) => {
      finalizeRequests += 1;
      expect(route.request().headers()["idempotency-key"]).toBe(
        finalizeIdempotencyKey,
      );
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify(finalizeResponse),
      });
    },
  );

  await page.goto("/");

  await expect(page.locator(".asset-status")).toHaveText("隔离区");
  expect(finalizeRequests).toBe(1);
});

test("returns a missing interrupted upload to a resumable OPEN state", async ({
  page,
}) => {
  await mockReadyCatalog(page);
  const recoveredSha256 =
    "431ced6916a2a21a156e38701afe55bbd7f88969fbbfc56d7fe099d47f265460";
  const recoveredUploadSession = {
    ...uploadSession,
    expected_sha256: recoveredSha256,
    version: 2,
  };
  const storageKey = `commercevision:upload:catalog-demo:${product.id}`;
  const createIdempotencyKey = "web-upload-create-missing-put-0001";
  const finalizeIdempotencyKey = "web-upload-finalize-missing-put-0001";
  const createRequest = {
    retention_class: "FOUNDATION",
    asset_kind: "IMAGE",
    filename: "pixel.png",
    declared_mime: "image/png",
    byte_length: 68,
    sha256: recoveredSha256,
    workflow_id: null,
    product_id: product.id,
    sku_id: null,
    category: product.category_code,
    role: "product-primary",
  };
  const finalizeAttempts: Array<{
    expectedVersion: number;
    idempotencyKey: string | undefined;
  }> = [];
  let createReplays = 0;
  let directUploads = 0;

  await page.addInitScript(
    ({ key, value }) => localStorage.setItem(key, JSON.stringify(value)),
    {
      key: storageKey,
      value: {
        sessionId: uploadSession.id,
        createIdempotencyKey,
        createRequest,
        finalizeIdempotencyKey,
        finalizeExpectedVersion: 1,
        stage: "UPLOADING",
      },
    },
  );
  await page.route(
    `**/api/v1/upload-sessions/${uploadSession.id}`,
    async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(recoveredUploadSession),
      });
    },
  );
  await page.route("**/api/v1/upload-sessions", async (route) => {
    createReplays += 1;
    expect(route.request().headers()["idempotency-key"]).toBe(
      createIdempotencyKey,
    );
    expect(route.request().postDataJSON()).toEqual(createRequest);
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        ...recoveredUploadSession,
        upload: {
          method: "PUT",
          url: "https://object-storage.example/recovered-upload",
          required_headers: {
            "Content-Type": "image/png",
            "Content-Length": "68",
            "x-amz-checksum-sha256": "QxztaRaiohoVbjhwGv5Vu9f4iWn7v8Vtf+CZ1H8mVGA=",
            "If-None-Match": "*",
            "x-amz-meta-upload-session-id": uploadSession.id,
          },
          maximum_bytes: 68,
          checksum_algorithm: "SHA-256",
          expires_at: uploadSession.expires_at,
        },
      }),
    });
  });
  await page.route(
    "https://object-storage.example/recovered-upload",
    async (route) => {
      directUploads += 1;
      await route.fulfill({ status: 200, body: "" });
    },
  );
  await page.route(
    `**/api/v1/upload-sessions/${uploadSession.id}:finalize`,
    async (route) => {
      const body = route.request().postDataJSON() as {
        expected_version: number;
      };
      finalizeAttempts.push({
        expectedVersion: body.expected_version,
        idempotencyKey: route.request().headers()["idempotency-key"],
      });
      if (finalizeAttempts.length === 1) {
        expect(finalizeAttempts[0].idempotencyKey).toBe(
          finalizeIdempotencyKey,
        );
        await route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({
            ...errorEnvelope,
            code: "UPLOAD_OBJECT_MISSING",
            message: "uploaded object was not found",
            category: "transient",
            retryable: true,
          }),
        });
        return;
      }
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify(finalizeResponse),
      });
    },
  );

  await page.goto("/");
  await expect(page.locator(".error-banner")).toContainText(
    "未找到完整上传文件",
  );
  await expect(page.getByRole("button", { name: "放弃本次上传" })).toBeVisible();
  const reupload = JSON.parse(
    (await page.evaluate((key) => localStorage.getItem(key), storageKey)) ??
      "null",
  ) as {
    finalizeAttempt: {
      idempotencyKey: string;
      request: { expected_version: number };
    };
  };
  expect(reupload).toMatchObject({
    schemaVersion: 1,
    finalizeAttempt: {
      request: { expected_version: 2 },
    },
    stage: "OPEN",
  });
  expect(reupload.finalizeAttempt.idempotencyKey).not.toBe(
    finalizeIdempotencyKey,
  );

  await page.getByLabel("商品图片", { exact: true }).setInputFiles({
    name: "pixel.png",
    mimeType: "image/png",
    buffer: pngBytes,
  });
  await page.getByRole("button", { name: "上传并登记" }).click();

  await expect(page.locator(".asset-status")).toHaveText("隔离区");
  expect(createReplays).toBe(1);
  expect(directUploads).toBe(1);
  expect(finalizeAttempts).toEqual([
    {
      expectedVersion: 1,
      idempotencyKey: finalizeIdempotencyKey,
    },
    {
      expectedVersion: 2,
      idempotencyKey: reupload.finalizeAttempt.idempotencyKey,
    },
  ]);
});

test("durably aborts a known upload session before clearing local recovery state", async ({
  page,
}) => {
  await mockReadyCatalog(page);
  const storageKey = `commercevision:upload:catalog-demo:${product.id}`;
  const openSession = { ...uploadSession, version: 2 };
  let abortRequests = 0;
  let abortIdempotencyKey: string | undefined;

  await page.addInitScript(
    ({ key, value }) => localStorage.setItem(key, JSON.stringify(value)),
    {
      key: storageKey,
      value: {
        sessionId: uploadSession.id,
        finalizeIdempotencyKey: "web-upload-finalize-abandon-0001",
        finalizeExpectedVersion: 2,
        stage: "OPEN",
      },
    },
  );
  await page.route(
    `**/api/v1/upload-sessions/${uploadSession.id}`,
    async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(openSession),
      });
    },
  );
  await page.route(
    `**/api/v1/upload-sessions/${uploadSession.id}:abort`,
    async (route) => {
      abortRequests += 1;
      abortIdempotencyKey = route.request().headers()["idempotency-key"];
      expect(route.request().postDataJSON()).toEqual({ expected_version: 2 });
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          ...openSession,
          status: "ABORTED",
          failure_code: "CLIENT_ABORTED",
          cleanup_operation_id: "019f8a00-0000-7000-8000-000000000099",
          version: 4,
        }),
      });
    },
  );

  await page.goto("/");
  await expect(
    page.getByRole("button", { name: "放弃本次上传" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "放弃本次上传" }).click();

  await expect(page.locator(".asset-status")).toHaveText("未开始");
  expect(abortRequests).toBe(1);
  expect(abortIdempotencyKey).toMatch(/^web-upload-abort-/);
  expect(await page.evaluate((key) => localStorage.getItem(key), storageKey)).toBe(
    null,
  );
});

test("keeps recovery state when an in-flight finalize rejects abort", async ({
  page,
}) => {
  await mockReadyCatalog(page);
  const storageKey = `commercevision:upload:catalog-demo:${product.id}`;
  const finalizingSession = {
    ...uploadSession,
    status: "FINALIZING",
    version: 2,
  };
  let abortRequests = 0;

  await page.addInitScript(
    ({ key, value }) => localStorage.setItem(key, JSON.stringify(value)),
    {
      key: storageKey,
      value: {
        sessionId: uploadSession.id,
        finalizeIdempotencyKey: "web-upload-finalize-busy-abort-0001",
        finalizeExpectedVersion: 2,
        stage: "OPEN",
      },
    },
  );
  await page.route(
    `**/api/v1/upload-sessions/${uploadSession.id}`,
    async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(finalizingSession),
      });
    },
  );
  await page.route(
    `**/api/v1/upload-sessions/${uploadSession.id}:abort`,
    async (route) => {
      abortRequests += 1;
      await route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify({
          ...errorEnvelope,
          code: "UPLOAD_BUSY",
          message: "upload session is being finalized",
          category: "conflict",
          retryable: true,
        }),
      });
    },
  );

  await page.goto("/");
  await page.getByRole("button", { name: "放弃本次上传" }).click();

  await expect(page.locator(".error-banner")).toContainText("登记仍在处理中");
  expect(abortRequests).toBe(1);
  expect(
    JSON.parse(
      (await page.evaluate((key) => localStorage.getItem(key), storageKey)) ??
        "null",
    ),
  ).toMatchObject({
    abortIdempotencyKey: expect.stringMatching(/^web-upload-abort-/),
    sessionId: uploadSession.id,
    stage: "OPEN",
  });
});

test("does not retry a terminal upload session after refresh", async ({ page }) => {
  await mockReadyCatalog(page);
  let finalizeRequests = 0;
  await page.addInitScript(
    ({ key, value }) => localStorage.setItem(key, JSON.stringify(value)),
    {
      key: `commercevision:upload:catalog-demo:${product.id}`,
      value: {
        sessionId: uploadSession.id,
        finalizeIdempotencyKey: "web-upload-finalize-terminal-0001",
        finalizeExpectedVersion: 1,
        stage: "FINALIZING",
      },
    },
  );
  await page.route(
    `**/api/v1/upload-sessions/${uploadSession.id}`,
    async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          ...uploadSession,
          status: "ABORTED",
          failure_code: "OBJECT_MISMATCH",
          version: 3,
        }),
      });
    },
  );
  await page.route(
    `**/api/v1/upload-sessions/${uploadSession.id}:finalize`,
    async (route) => {
      finalizeRequests += 1;
      await route.fulfill({ status: 409, body: "" });
    },
  );

  await page.goto("/");

  await expect(page.locator(".asset-status")).toHaveText("已终止");
  await expect(page.locator(".error-banner")).toContainText("文件校验未通过");
  await expect(page.getByRole("button", { name: "重试登记" })).toHaveCount(0);
  expect(finalizeRequests).toBe(0);
});

test("restores a terminal validation operation from its durable resource", async ({
  page,
}) => {
  await mockReadyCatalog(page);
  let operationRequests = 0;
  let releaseOperation: (() => void) | undefined;
  const operationReleased = new Promise<void>((resolve) => {
    releaseOperation = resolve;
  });
  await page.addInitScript(
    ({ key, value }) => localStorage.setItem(key, JSON.stringify(value)),
    {
      key: `commercevision:upload:catalog-demo:${product.id}`,
      value: {
        sessionId: uploadSession.id,
        finalizeIdempotencyKey: "web-upload-finalize-terminal-operation-0001",
        finalizeExpectedVersion: 1,
        stage: "FINALIZED",
        assetId: quarantinedAsset.id,
      },
    },
  );
  await page.route(
    `**/api/v1/upload-sessions/${uploadSession.id}`,
    async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(finalizeResponse.upload_session),
      });
    },
  );
  await page.route(`**/api/v1/assets/${quarantinedAsset.id}`, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(quarantinedAsset),
    });
  });
  await page.route(
    `**/api/v1/assets/${quarantinedAsset.id}/validation`,
    async (route) => {
      await route.fulfill({ status: 404, body: "" });
    },
  );
  await page.route(
    `**/api/v1/operations/${durableOperation.id}`,
    async (route) => {
      operationRequests += 1;
      await operationReleased;
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          ...durableOperation,
          state: "SUCCEEDED",
          completed_at: "2026-07-24T12:45:05Z",
          version: 2,
        }),
      });
    },
  );

  await page.goto("/");
  const operationState = page
    .locator(".asset-facts > div")
    .filter({ hasText: "校验任务" })
    .locator("dd");
  await expect.poll(() => operationRequests).toBeGreaterThan(0);
  await expect(operationState).toHaveText("正在读取");
  releaseOperation?.();
  await expect(operationState).toHaveText("SUCCEEDED");
  const requestsAtTerminal = operationRequests;
  await page.waitForTimeout(1200);
  expect(operationRequests).toBe(requestsAtTerminal);
});

test("polls a restored retryable validation operation until it is terminal", async ({
  page,
}) => {
  await mockReadyCatalog(page);
  let operationRequests = 0;
  let terminal = false;
  await page.addInitScript(
    ({ key, value }) => localStorage.setItem(key, JSON.stringify(value)),
    {
      key: `commercevision:upload:catalog-demo:${product.id}`,
      value: {
        sessionId: uploadSession.id,
        finalizeIdempotencyKey: "web-upload-finalize-retry-operation-0001",
        finalizeExpectedVersion: 1,
        stage: "FINALIZED",
        assetId: quarantinedAsset.id,
      },
    },
  );
  await page.route(
    `**/api/v1/upload-sessions/${uploadSession.id}`,
    async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(finalizeResponse.upload_session),
      });
    },
  );
  await page.route(`**/api/v1/assets/${quarantinedAsset.id}`, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(quarantinedAsset),
    });
  });
  await page.route(
    `**/api/v1/assets/${quarantinedAsset.id}/validation`,
    async (route) => {
      await route.fulfill({ status: 404, body: "" });
    },
  );
  await page.route(
    `**/api/v1/operations/${durableOperation.id}`,
    async (route) => {
      operationRequests += 1;
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(
          terminal
            ? {
                ...durableOperation,
                state: "SUCCEEDED",
                completed_at: "2026-07-24T12:45:08Z",
                version: 4,
              }
            : {
                ...durableOperation,
                state: "RETRYABLE_FAILED",
                attempt_count: 1,
                next_attempt_at: "2026-07-24T12:45:07Z",
                error: {
                  code: "PROVIDER_TIMEOUT",
                  category: "transient",
                  message: "provider timed out",
                  retryable: true,
                  provider_request_id: null,
                },
                version: 3,
              },
        ),
      });
    },
  );

  await page.goto("/");
  const operationState = page
    .locator(".asset-facts > div")
    .filter({ hasText: "校验任务" })
    .locator("dd");
  await expect(operationState).toHaveText("RETRYABLE_FAILED");
  await expect(page.locator(".validation-banner-retryable")).toContainText(
    "系统将自动重试",
  );
  terminal = true;
  await expect(operationState).toHaveText("SUCCEEDED", { timeout: 5000 });
  expect(operationRequests).toBeGreaterThanOrEqual(2);
});

test("renders normalized validation stages and terminal rejection", async ({
  page,
}) => {
  await mockReadyCatalog(page);
  await page.addInitScript(
    ({ key, value }) => localStorage.setItem(key, JSON.stringify(value)),
    {
      key: `commercevision:upload:catalog-demo:${product.id}`,
      value: {
        sessionId: uploadSession.id,
        finalizeIdempotencyKey: "web-upload-finalize-rejected-validation-0001",
        finalizeExpectedVersion: 1,
        stage: "FINALIZED",
        assetId: quarantinedAsset.id,
      },
    },
  );
  await page.route(
    `**/api/v1/upload-sessions/${uploadSession.id}`,
    async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(finalizeResponse.upload_session),
      });
    },
  );
  await page.route(`**/api/v1/assets/${quarantinedAsset.id}`, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ ...quarantinedAsset, status: "BLOCKED" }),
    });
  });
  await page.route(
    `**/api/v1/assets/${quarantinedAsset.id}/validation`,
    async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          asset_id: quarantinedAsset.id,
          asset_version_id: assetVersion.id,
          asset_status: "BLOCKED",
          validation_policy_version: "asset-validation-v1",
          operation: {
            id: durableOperation.id,
            state: "FAILED",
            attempt_count: 1,
            max_attempts: 3,
            next_attempt_at: null,
            retryable: false,
            failure_code: "CONTENT_SAFETY_BLOCKED",
            failure_category: "policy",
            completed_at: "2026-07-24T12:45:05Z",
          },
          stages: [
            {
              id: "019f8a00-0000-7000-8000-000000000021",
              attempt_number: 1,
              stage: "LOCAL_FORMAT",
              verdict: "PASS",
              reason_code: null,
              validator_name: "local-image",
              validator_version: "1",
              policy_version: "asset-validation-v1",
              evidence: {
                asset_kind: "IMAGE",
                byte_size: 68,
                detected_mime: "image/png",
                facts: { width: 1, height: 1 },
                format_name: "PNG",
              },
              created_at: "2026-07-24T12:45:03Z",
            },
            {
              id: "019f8a00-0000-7000-8000-000000000022",
              attempt_number: 1,
              stage: "CONTENT_SAFETY",
              verdict: "BLOCK",
              reason_code: "CONTENT_SAFETY_BLOCKED",
              validator_name: "alibaba-green20220302",
              validator_version: "3.2.4",
              policy_version: "asset-validation-v1",
              evidence: {
                asset_kind: "IMAGE",
                outcome: "BLOCK",
                risk_level: "high",
                labels: [{ code: "prohibited_content", confidence: 99 }],
              },
              created_at: "2026-07-24T12:45:04Z",
            },
          ],
        }),
      });
    },
  );

  await page.goto("/");

  await expect(page.getByRole("heading", { name: "素材校验" })).toBeVisible();
  await expect(page.locator(".validation-stages")).toContainText("本地格式");
  await expect(page.locator(".validation-stages")).toContainText("PNG");
  await expect(page.locator(".validation-stages")).toContainText("内容安全");
  await expect(page.locator(".validation-stages")).toContainText(
    "prohibited_content",
  );
  await expect(page.locator(".validation-banner-rejected")).toContainText(
    "CONTENT_SAFETY_BLOCKED",
  );
  await expect(page.locator(".asset-upload-panel")).not.toContainText(
    "provider_request_id",
  );
});

test("renders exhausted validation infrastructure failure separately from rejection", async ({
  page,
}) => {
  await mockReadyCatalog(page);
  await page.addInitScript(
    ({ key, value }) => localStorage.setItem(key, JSON.stringify(value)),
    {
      key: `commercevision:upload:catalog-demo:${product.id}`,
      value: {
        sessionId: uploadSession.id,
        finalizeIdempotencyKey: "web-upload-finalize-system-failure-0001",
        finalizeExpectedVersion: 1,
        stage: "FINALIZED",
        assetId: quarantinedAsset.id,
      },
    },
  );
  await page.route(
    `**/api/v1/upload-sessions/${uploadSession.id}`,
    async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(finalizeResponse.upload_session),
      });
    },
  );
  await page.route(`**/api/v1/assets/${quarantinedAsset.id}`, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ ...quarantinedAsset, status: "VALIDATING" }),
    });
  });
  await page.route(
    `**/api/v1/assets/${quarantinedAsset.id}/validation`,
    async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          asset_id: quarantinedAsset.id,
          asset_version_id: assetVersion.id,
          asset_status: "VALIDATING",
          validation_policy_version: "asset-validation-v1",
          operation: {
            id: durableOperation.id,
            state: "FAILED",
            attempt_count: 3,
            max_attempts: 3,
            next_attempt_at: null,
            retryable: false,
            failure_code: "MALWARE_SCANNER_UNAVAILABLE",
            failure_category: "infrastructure",
            completed_at: "2026-07-24T12:45:05Z",
          },
          stages: [
            {
              id: "019f8a00-0000-7000-8000-000000000041",
              attempt_number: 3,
              stage: "MALWARE",
              verdict: "RETRYABLE_FAILURE",
              reason_code: "MALWARE_SCANNER_UNAVAILABLE",
              validator_name: "clamav",
              validator_version: "clamav-unavailable",
              policy_version: "asset-validation-v1",
              evidence: {
                asset_kind: "IMAGE",
                outcome: "UNAVAILABLE",
                latency_ms: 15000,
                scanner_version: null,
                signature: null,
              },
              created_at: "2026-07-24T12:45:04Z",
            },
          ],
        }),
      });
    },
  );

  await page.goto("/");

  await expect(page.locator(".validation-banner-failed")).toContainText(
    "校验无法完成",
  );
  await expect(page.locator(".validation-banner-failed")).toContainText(
    "MALWARE_SCANNER_UNAVAILABLE",
  );
  await expect(page.locator(".validation-banner-rejected")).toHaveCount(0);
  await expect(page.locator(".validation-banner-retryable")).toHaveCount(0);
  await expect(page.locator(".asset-upload-panel")).not.toContainText(
    "素材未通过校验",
  );
});

test("renders pending review as a recoverable human gate", async ({ page }) => {
  await mockReadyCatalog(page);
  await page.addInitScript(
    ({ key, value }) => localStorage.setItem(key, JSON.stringify(value)),
    {
      key: `commercevision:upload:catalog-demo:${product.id}`,
      value: {
        sessionId: uploadSession.id,
        finalizeIdempotencyKey: "web-upload-finalize-review-validation-0001",
        finalizeExpectedVersion: 1,
        stage: "FINALIZED",
        assetId: quarantinedAsset.id,
      },
    },
  );
  await page.route(
    `**/api/v1/upload-sessions/${uploadSession.id}`,
    async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(finalizeResponse.upload_session),
      });
    },
  );
  await page.route(`**/api/v1/assets/${quarantinedAsset.id}`, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        ...quarantinedAsset,
        status: "PENDING_REVIEW",
      }),
    });
  });
  await page.route(
    `**/api/v1/assets/${quarantinedAsset.id}/validation`,
    async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          asset_id: quarantinedAsset.id,
          asset_version_id: assetVersion.id,
          asset_status: "PENDING_REVIEW",
          validation_policy_version: "asset-validation-v1",
          operation: {
            id: durableOperation.id,
            state: "SUCCEEDED",
            attempt_count: 1,
            max_attempts: 3,
            next_attempt_at: null,
            retryable: false,
            failure_code: null,
            failure_category: null,
            completed_at: "2026-07-24T12:45:05Z",
          },
          stages: [
            {
              id: "019f8a00-0000-7000-8000-000000000031",
              attempt_number: 1,
              stage: "CONTENT_SAFETY",
              verdict: "REVIEW",
              reason_code: "CONTENT_SAFETY_REVIEW",
              validator_name: "alibaba-green20220302",
              validator_version: "3.2.4",
              policy_version: "asset-validation-v1",
              evidence: {
                asset_kind: "IMAGE",
                outcome: "REVIEW",
                risk_level: "medium",
                labels: [{ code: "manual_review", confidence: 71 }],
              },
              created_at: "2026-07-24T12:45:04Z",
            },
          ],
        }),
      });
    },
  );

  await page.goto("/");

  await expect(page.locator(".validation-banner-review")).toContainText(
    "等待人工复核",
  );
  await expect(page.locator(".validation-banner-review")).toContainText(
    "CONTENT_SAFETY_REVIEW",
  );
  await expect(page.locator(".validation-stage-REVIEW")).toContainText(
    "待复核",
  );
  await expect(page.locator(".validation-banner-rejected")).toHaveCount(0);
  await expect(page.locator(".asset-upload-panel")).not.toContainText(
    "素材未通过校验",
  );
});
