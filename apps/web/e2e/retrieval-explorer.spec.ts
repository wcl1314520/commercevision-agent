import { expect, test } from "@playwright/test";
import path from "node:path";

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

const runId = "019f8a00-0000-7000-8000-000000000071";
const assetId = "019f8a00-0000-7000-8000-000000000073";
const assetVersionId = "019f8a00-0000-7000-8000-000000000074";
const rightsId = "019f8a00-0000-7000-8000-000000000075";
const previewToken = "A".repeat(43);
const pngBytes = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
  "base64",
);

test("executes rights-first retrieval and exchanges a short-lived controlled preview", async ({
  page,
}) => {
  let retrievalBody: Record<string, unknown> | undefined;
  let previewBody: Record<string, unknown> | undefined;
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });

  await page.route("**/api/web-capabilities", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ administrator: false }),
    });
  });
  await page.route("**/api/v1/brand-profiles**", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ items: [], next_cursor: null }),
    });
  });
  await page.route("**/api/v1/products**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(
        path === "/api/v1/products"
          ? { items: [product], next_cursor: null }
          : product,
      ),
    });
  });
  await page.route("**/api/v1/retrieval-runs", async (route) => {
    retrievalBody = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        retrieval_run_id: runId,
        retrieval_policy_version: "retrieval-policy-v1",
        complete_hybrid: false,
        eligible_asset_version_count: 4,
        fused_candidate_count: 2,
        final_authorized_candidate_count: 1,
        latency_ms: 37,
        degradations: [
          {
            component: "milvus",
            code: "DENSE_RECALL_UNAVAILABLE",
            message: "dense recall unavailable",
          },
        ],
        citations: [
          {
            asset_id: assetId,
            asset_version_id: assetVersionId,
            rights_record_id: rightsId,
            rights_record_version: 2,
            retrieval_policy_version: "retrieval-policy-v1",
            brand_profile_version: null,
            channels: ["LEXICAL", "EXPLICIT"],
            score: {
              channel_ranks: { LEXICAL: 1, EXPLICIT: 1 },
              channel_raw_scores: { LEXICAL: 0.92 },
              reciprocal_rank_fusion: 0.03278688524590164,
              business_adjustment: 0,
              final_score: 0.03278688524590164,
              rerank_position: null,
            },
            rank: 1,
            reason: "authorized current asset version",
            decided_at: "2026-08-03T09:00:00Z",
            preview_reference_token: previewToken,
          },
        ],
      }),
    });
  });
  await page.route(
    `**/api/v1/retrieval-runs/${runId}/results/1:preview`,
    async (route) => {
      previewBody = route.request().postDataJSON() as Record<string, unknown>;
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          method: "GET",
          url: "https://objects.example/retrieval-preview",
          required_headers: { "x-preview-capability": "opaque" },
          expires_at: new Date(Date.now() + 45_000).toISOString(),
        }),
      });
    },
  );
  await page.route("https://objects.example/retrieval-preview", async (route) => {
    expect(route.request().headers()["x-preview-capability"]).toBe("opaque");
    await route.fulfill({ contentType: "image/png", body: pngBytes });
  });

  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "检索探索器" })).toBeVisible();
  await page.getByLabel("查询文本").fill("Calm skincare hero image");
  await page.getByRole("button", { name: "执行权利优先检索" }).click();

  await expect(page.getByText("混合检索已降级")).toBeVisible();
  await expect(page.getByText("DENSE_RECALL_UNAVAILABLE")).toBeVisible();
  await expect(page.getByText("authorized current asset version")).toBeVisible();
  expect(retrievalBody).toMatchObject({
    workspace_id: "catalog-demo",
    requester_id: "catalog-workbench",
    product_id: product.id,
    category: product.category_code,
    brand: product.brand,
    vector_kinds: ["PRODUCT_FUSED"],
    query_text: "Calm skincare hero image",
    retrieval_policy_version: "retrieval-policy-v1",
  });

  const previewButton = page.getByRole("button", { name: "受控预览" });
  expect((await previewButton.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44);
  await previewButton.click();
  await expect(
    page.getByAltText("检索结果 1 的受控资产预览"),
  ).toBeVisible();
  expect(previewBody).toEqual({ preview_reference_token: previewToken });
  await expect(page.locator("body")).not.toHaveCSS("overflow-x", "scroll");
  await page.locator(".retrieval-explorer").screenshot({
    path: path.resolve("../../.scratch/retrieval-explorer-mobile.png"),
  });
  expect(consoleErrors).toEqual([]);
});
