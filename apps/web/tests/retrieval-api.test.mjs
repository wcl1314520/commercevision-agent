import { afterEach, describe, expect, it, vi } from "vitest";

import {
  RetrievalApi,
  RetrievalApiError,
} from "../lib/retrieval-api";

const RUN_ID = "019f8a00-0000-7000-8000-000000000071";
const PRODUCT_ID = "019f8a00-0000-7000-8000-000000000072";
const ASSET_ID = "019f8a00-0000-7000-8000-000000000073";
const ASSET_VERSION_ID = "019f8a00-0000-7000-8000-000000000074";
const RIGHTS_ID = "019f8a00-0000-7000-8000-000000000075";

function query() {
  return {
    workspace_id: "catalog-demo",
    requester_id: "catalog-workbench",
    product_id: PRODUCT_ID,
    product_brief_id: null,
    category: "beauty.skincare",
    brand: "Northstar Labs",
    purpose: "RETRIEVAL",
    provider: "fixture",
    requires_derivative: false,
    roles: [],
    vector_kinds: ["PRODUCT_FUSED"],
    query_text: "calm skincare hero image",
    query_image_asset_version_id: null,
    explicit_reference_asset_version_ids: [],
    brand_profile_id: null,
    brand_profile_version: null,
    result_limit: 8,
    candidate_limit: 40,
    retrieval_policy_version: "retrieval-policy-v1",
  };
}

function response() {
  return {
    retrieval_run_id: RUN_ID,
    retrieval_policy_version: "retrieval-policy-v1",
    complete_hybrid: false,
    eligible_asset_version_count: 4_000,
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
        asset_id: ASSET_ID,
        asset_version_id: ASSET_VERSION_ID,
        rights_record_id: RIGHTS_ID,
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
  };
}

describe("RetrievalApi", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("executes a workspace-bound retrieval and validates the retained run", async () => {
    const payload = query();
    const body = response();
    const fetchMock = vi.fn(async () => Response.json(body, { status: 201 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      new RetrievalApi({
        baseUrl: "https://web.example",
        workspaceId: "catalog-demo",
        requesterId: "catalog-workbench",
      }).execute(payload),
    ).resolves.toEqual(body);

    const [input, init] = fetchMock.mock.calls[0];
    expect(String(input)).toBe("https://web.example/api/v1/retrieval-runs");
    expect(init).toMatchObject({
      method: "POST",
      body: JSON.stringify(payload),
      cache: "no-store",
    });
    expect(Object.fromEntries(new Headers(init.headers).entries())).toEqual({
      accept: "application/json",
      "content-type": "application/json",
      "x-actor-id": "catalog-workbench",
      "x-workspace-id": "catalog-demo",
    });
  });

  it("exchanges only the exact run/rank/token preview capability", async () => {
    const temporaryReference = {
      method: "GET",
      url: "https://objects.example/preview?signature=opaque",
      required_headers: { "x-preview": "required" },
      expires_at: "2026-08-03T09:00:45Z",
    };
    const fetchMock = vi.fn(async () => Response.json(temporaryReference));
    vi.stubGlobal("fetch", fetchMock);
    const token = "B".repeat(43);

    await expect(
      new RetrievalApi().preview(RUN_ID, 1, token),
    ).resolves.toEqual(temporaryReference);

    const [input, init] = fetchMock.mock.calls[0];
    expect(String(input)).toBe(
      `/api/v1/retrieval-runs/${RUN_ID}/results/1:preview`,
    );
    expect(init).toMatchObject({
      method: "POST",
      body: JSON.stringify({ preview_reference_token: token }),
    });
  });

  it("fails closed on malformed or cross-policy retrieval evidence", async () => {
    const malformed = response();
    malformed.retrieval_policy_version = "retrieval-policy-v2";
    malformed.citations[0].score.final_score = 99;
    vi.stubGlobal("fetch", vi.fn(async () => Response.json(malformed)));

    await expect(new RetrievalApi().execute(query())).rejects.toMatchObject({
      status: 502,
    });
  });

  it("fails closed on contradictory completeness evidence", async () => {
    const malformed = response();
    malformed.complete_hybrid = true;
    vi.stubGlobal("fetch", vi.fn(async () => Response.json(malformed)));

    await expect(new RetrievalApi().execute(query())).rejects.toMatchObject({
      status: 502,
    });
  });

  it("preserves API error envelopes", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json(
          {
            code: "RETRIEVAL_RUN_NOT_FOUND",
            category: "not_found",
            message: "Retrieval run was not found",
            retryable: false,
            request_id: "request-retrieval-not-found",
            trace_id: "trace-retrieval-not-found",
          },
          { status: 404 },
        ),
      ),
    );

    await expect(new RetrievalApi().get(RUN_ID)).rejects.toBeInstanceOf(
      RetrievalApiError,
    );
  });
});
