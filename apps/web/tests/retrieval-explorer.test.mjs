import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  buildRetrievalQuery,
  previewObjectUrl,
  RetrievalResults,
} from "../app/retrieval-explorer";

globalThis.React = React;

const PRODUCT_ID = "019f8a00-0000-7000-8000-000000000072";

describe("Retrieval Explorer", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("builds one structured query without weakening selected vector channels", () => {
    expect(
      buildRetrievalQuery(
        {
          purpose: "RETRIEVAL",
          provider: "fixture",
          requiresDerivative: false,
          rolesText: "HERO, LOGO",
          vectorKinds: ["PRODUCT_FUSED", "IMAGE"],
          queryText: "Calm skincare hero image",
          queryImageAssetVersionId:
            "019f8a00-0000-7000-8000-000000000076",
          explicitReferencesText:
            "019f8a00-0000-7000-8000-000000000077\n019f8a00-0000-7000-8000-000000000078",
          brandProfileId: "",
          brandProfileVersion: "",
          resultLimit: 8,
          candidateLimit: 40,
          retrievalPolicyVersion: "retrieval-policy-v1",
        },
        {
          workspaceId: "catalog-demo",
          requesterId: "catalog-workbench",
          productId: PRODUCT_ID,
          category: "beauty.skincare",
          brand: "Northstar Labs",
        },
      ),
    ).toMatchObject({
      roles: ["HERO", "LOGO"],
      vector_kinds: ["PRODUCT_FUSED", "IMAGE"],
      query_text: "Calm skincare hero image",
      explicit_reference_asset_version_ids: [
        "019f8a00-0000-7000-8000-000000000077",
        "019f8a00-0000-7000-8000-000000000078",
      ],
    });
  });

  it("renders degradation, channels, score evidence, rights decision, and preview action", () => {
    const markup = renderToStaticMarkup(
      React.createElement(RetrievalResults, {
        previewState: {},
        onPreview: () => undefined,
        response: {
          retrieval_run_id: "019f8a00-0000-7000-8000-000000000071",
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
              asset_id: "019f8a00-0000-7000-8000-000000000073",
              asset_version_id: "019f8a00-0000-7000-8000-000000000074",
              rights_record_id: "019f8a00-0000-7000-8000-000000000075",
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
              preview_reference_token: "A".repeat(43),
            },
          ],
        },
      }),
    );

    expect(markup).toContain("混合检索已降级");
    expect(markup).toContain("DENSE_RECALL_UNAVAILABLE");
    expect(markup).toContain("LEXICAL");
    expect(markup).toContain("EXPLICIT");
    expect(markup).toContain("authorized current asset version");
    expect(markup).toContain("rights v2");
    expect(markup).toContain("受控预览");
  });

  it("creates previews only for bounded validated image responses", async () => {
    const createObjectURL = vi
      .spyOn(URL, "createObjectURL")
      .mockReturnValue("blob:retrieval-preview");
    const fetchMock = vi.fn(async () =>
      new Response(new Blob(["png"], { type: "image/png" }), {
        headers: { "content-type": "image/png", "content-length": "3" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      previewObjectUrl(
        {
          method: "GET",
          url: "https://objects.example/preview",
          required_headers: {},
          expires_at: "2026-08-03T09:00:45Z",
        },
        new AbortController().signal,
      ),
    ).resolves.toBe("blob:retrieval-preview");
    expect(createObjectURL).toHaveBeenCalledOnce();

    fetchMock.mockResolvedValueOnce(
      new Response("", {
        headers: {
          "content-type": "image/png",
          "content-length": String(10 * 1024 * 1024 + 1),
        },
      }),
    );
    await expect(
      previewObjectUrl(
        {
          method: "GET",
          url: "https://objects.example/oversized",
          required_headers: {},
          expires_at: "2026-08-03T09:00:45Z",
        },
        new AbortController().signal,
      ),
    ).rejects.toThrow("大小限制");
  });
});
