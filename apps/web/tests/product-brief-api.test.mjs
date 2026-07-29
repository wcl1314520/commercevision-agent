import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ProductBriefApi,
  ProductBriefApiCancelledError,
  ProductBriefApiError,
} from "../lib/product-brief-api";

const BRIEF_ID = "019f8a00-0000-7000-8000-000000000081";
const WORKFLOW_ID = "019f8a00-0000-7000-8000-000000000082";
const OPERATION_ID = "019f8a00-0000-7000-8000-000000000083";
const VERSION_ID = "019f8a00-0000-7000-8000-000000000084";

function stalledJsonResponse(status = 200) {
  let bodyController;
  const response = new Response(
    new ReadableStream({
      start(controller) {
        bodyController = controller;
        controller.enqueue(new TextEncoder().encode("{"));
      },
    }),
    { status },
  );
  return {
    response,
    close() {
      try {
        bodyController.close();
      } catch {
        // An aborted fetch may already have cancelled the synthetic body.
      }
    },
  };
}

describe("ProductBriefApi", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("submits analysis with exact workspace, actor, and idempotency identity", async () => {
    const responseBody = {
      product_brief: { id: BRIEF_ID },
      operation_id: OPERATION_ID,
      operation_state: "PENDING",
    };
    const fetchMock = vi.fn(async () =>
      Response.json(responseBody, { status: 202 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const payload = {
      workflow_id: WORKFLOW_ID,
      product_id: "019f8a00-0000-7000-8000-000000000085",
      asset_version_ids: ["019f8a00-0000-7000-8000-000000000086"],
      expected_workflow_version: 3,
    };

    await expect(
      new ProductBriefApi({
        baseUrl: "https://web.example",
        workspaceId: "catalog-demo",
        actorId: "catalog-workbench",
      }).requestAnalysis(payload, "web-brief-analysis-0001"),
    ).resolves.toEqual(responseBody);

    const [input, init] = fetchMock.mock.calls[0];
    expect(String(input)).toBe(
      "https://web.example/api/v1/product-briefs:analyze",
    );
    expect(init).toMatchObject({
      body: JSON.stringify(payload),
      cache: "no-store",
      method: "POST",
    });
    expect(Object.fromEntries(new Headers(init.headers).entries())).toEqual({
      accept: "application/json",
      "content-type": "application/json",
      "idempotency-key": "web-brief-analysis-0001",
      "x-actor-id": "catalog-workbench",
      "x-workspace-id": "catalog-demo",
    });
  });

  it("rejects a success-shaped analysis response unless status is 202", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json({
          product_brief: { id: BRIEF_ID },
          operation_id: OPERATION_ID,
          operation_state: "PENDING",
        }),
      ),
    );

    const rejection = new ProductBriefApi()
      .requestAnalysis(
        {
          workflow_id: WORKFLOW_ID,
          product_id: "019f8a00-0000-7000-8000-000000000085",
          asset_version_ids: [
            "019f8a00-0000-7000-8000-000000000086",
          ],
          expected_workflow_version: 3,
        },
        "web-brief-analysis-non-authoritative",
      )
      .catch((error) => error);

    await expect(rejection).resolves.toBeInstanceOf(
      ProductBriefApiError,
    );
    await expect(rejection).resolves.toMatchObject({ status: 502 });
  });

  it("uses exact immutable version endpoints for revision and confirmation", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(Response.json({ id: BRIEF_ID, version: 4 }))
      .mockResolvedValueOnce(
        Response.json({
          confirmation_id: "019f8a00-0000-7000-8000-000000000087",
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const api = new ProductBriefApi();
    const revision = {
      expected_product_brief_version: 3,
      base_version_id: VERSION_ID,
      reason: "Verified against the controlled product image",
      fields: [],
    };
    const confirmation = {
      expected_product_brief_version: 4,
      product_brief_version_id: VERSION_ID,
      expected_workflow_version: 6,
      reason_code: "HUMAN_VERIFIED",
      comment_ref: "comment://product-brief/review-1",
    };

    await api.revise(BRIEF_ID, revision, "web-brief-revise-0001");
    await api.confirm(BRIEF_ID, confirmation, "web-brief-confirm-0001");

    expect(String(fetchMock.mock.calls[0][0])).toBe(
      `/api/v1/product-briefs/${BRIEF_ID}:revise`,
    );
    expect(fetchMock.mock.calls[0][1]).toMatchObject({
      body: JSON.stringify(revision),
      method: "POST",
    });
    expect(String(fetchMock.mock.calls[1][0])).toBe(
      `/api/v1/product-briefs/${BRIEF_ID}:confirm`,
    );
    expect(fetchMock.mock.calls[1][1]).toMatchObject({
      body: JSON.stringify(confirmation),
      method: "POST",
    });
  });

  it("uses distinct pre-analysis and ProductBrief-bound workflow reads without caches", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(Response.json({ id: BRIEF_ID }))
      .mockResolvedValueOnce(Response.json({ items: [], next_cursor: null }))
      .mockResolvedValueOnce(Response.json({ id: WORKFLOW_ID, version: 7 }))
      .mockResolvedValueOnce(Response.json({ id: WORKFLOW_ID, version: 8 }))
      .mockResolvedValueOnce(
        Response.json({ id: OPERATION_ID, state: "WAITING_HUMAN" }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const api = new ProductBriefApi();

    await api.get(BRIEF_ID);
    await api.listVersions(BRIEF_ID);
    await api.getAnalysisWorkflowContext(WORKFLOW_ID);
    await api.getWorkflowContext(WORKFLOW_ID, BRIEF_ID);
    await api.getOperation(BRIEF_ID, OPERATION_ID);

    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      `/api/v1/product-briefs/${BRIEF_ID}`,
      `/api/v1/product-briefs/${BRIEF_ID}/versions`,
      `/api/v1/product-briefs/analysis-workflow-context/${WORKFLOW_ID}`,
      `/api/v1/product-briefs/workflow-context/${WORKFLOW_ID}?product_brief_id=${BRIEF_ID}`,
      `/api/v1/product-briefs/${BRIEF_ID}/operations/${OPERATION_ID}`,
    ]);
    expect(
      fetchMock.mock.calls.every(([, init]) => init.cache === "no-store"),
    ).toBe(true);
  });

  it("requests an explicit ProductBrief version keyset page", async () => {
    const responseBody = { items: [], next_cursor: 17 };
    const fetchMock = vi.fn(async () => Response.json(responseBody));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      new ProductBriefApi().listVersions(BRIEF_ID, {
        limit: 7,
        cursor: 23,
      }),
    ).resolves.toEqual(responseBody);

    expect(String(fetchMock.mock.calls[0][0])).toBe(
      `/api/v1/product-briefs/${BRIEF_ID}/versions?limit=7&cursor=23`,
    );
  });

  it("canonically encodes the bound ProductBrief query identity", async () => {
    const fetchMock = vi.fn(async () =>
      Response.json({ id: WORKFLOW_ID, version: 7 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await new ProductBriefApi().getWorkflowContext(
      "workflow/context",
      "brief identity/&",
    );

    expect(String(fetchMock.mock.calls[0][0])).toBe(
      "/api/v1/product-briefs/workflow-context/workflow%2Fcontext?product_brief_id=brief+identity%2F%26",
    );
  });

  it.each([
    ["ProductBrief", (api, signal) => api.get(BRIEF_ID, signal)],
    [
      "pre-analysis workflow",
      (api, signal) => api.getAnalysisWorkflowContext(WORKFLOW_ID, signal),
    ],
    [
      "bound workflow",
      (api, signal) => api.getWorkflowContext(WORKFLOW_ID, BRIEF_ID, signal),
    ],
  ])("lets callers cancel the %s read without reporting an API failure", async (
    _readKind,
    startRead,
  ) => {
    const controller = new AbortController();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_input, init) =>
          new Promise((_resolve, reject) => {
            init.signal.addEventListener("abort", () =>
              reject(init.signal.reason),
            );
          }),
      ),
    );

    const read = startRead(new ProductBriefApi(), controller.signal);
    controller.abort();

    await expect(read).rejects.toBeInstanceOf(ProductBriefApiCancelledError);
    await expect(read).rejects.not.toBeInstanceOf(ProductBriefApiError);
  });

  it.each([
    [
      "revision",
      (api, signal) =>
        api.revise(
          BRIEF_ID,
          {
            expected_product_brief_version: 3,
            base_version_id: VERSION_ID,
            reason: "Verified",
            fields: [],
          },
          "web-brief-revise-abort",
          signal,
        ),
    ],
    [
      "confirmation",
      (api, signal) =>
        api.confirm(
          BRIEF_ID,
          {
            expected_product_brief_version: 4,
            product_brief_version_id: VERSION_ID,
            expected_workflow_version: 6,
          },
          "web-brief-confirm-abort",
          signal,
        ),
    ],
  ])("propagates caller cancellation into the %s fetch", async (
    _operation,
    startMutation,
  ) => {
    let fetchSignal;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_input, init) =>
          new Promise((resolve, reject) => {
            fetchSignal = init.signal;
            const completion = setTimeout(
              () => resolve(Response.json({ ok: true })),
              25,
            );
            init.signal.addEventListener(
              "abort",
              () => {
                clearTimeout(completion);
                reject(init.signal.reason);
              },
              { once: true },
            );
          }),
      ),
    );
    const controller = new AbortController();

    const mutation = startMutation(
      new ProductBriefApi(),
      controller.signal,
    );
    controller.abort(new DOMException("product changed", "AbortError"));

    await expect(mutation).rejects.toBeInstanceOf(
      ProductBriefApiCancelledError,
    );
    expect(fetchSignal.aborted).toBe(true);
  });

  it("treats a 410 as authoritative before reading its stalled body", async () => {
    const stalled = stalledJsonResponse(410);
    let fetchSignal;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input, init) => {
        fetchSignal = init.signal;
        return stalled.response;
      }),
    );
    let outcome = "pending";
    const read = new ProductBriefApi({ requestTimeoutMs: 30_000 })
      .get(BRIEF_ID)
      .then(
        () => {
          outcome = "fulfilled";
        },
        (error) => {
          outcome = error;
        },
      );

    try {
      await vi.waitFor(
        () =>
          expect(outcome).toMatchObject({
            status: 410,
          }),
        { timeout: 100, interval: 5 },
      );
      expect(fetchSignal.aborted).toBe(false);
    } finally {
      stalled.close();
      await read;
    }
  });

  it.each([200, 409])(
    "keeps its deadline active while reading a stalled %i response body",
    async (status) => {
      vi.useFakeTimers();
      const stalled = stalledJsonResponse(status);
      let fetchSignal;
      vi.stubGlobal(
        "fetch",
        vi.fn(async (_input, init) => {
          fetchSignal = init.signal;
          return stalled.response;
        }),
      );
      let outcome;
      const read = new ProductBriefApi({ requestTimeoutMs: 20 })
        .get(BRIEF_ID)
        .then(
          () => {
            outcome = "fulfilled";
          },
          (error) => {
            outcome = error;
          },
        );

      try {
        await vi.advanceTimersByTimeAsync(25);
        expect(outcome).toBeInstanceOf(ProductBriefApiError);
        expect(outcome).toMatchObject({ status: 504 });
        expect(fetchSignal.aborted).toBe(true);
      } finally {
        stalled.close();
        await read;
      }
    },
  );

  it("keeps caller cancellation active while reading a success body", async () => {
    const stalled = stalledJsonResponse();
    const controller = new AbortController();
    vi.stubGlobal("fetch", vi.fn(async () => stalled.response));
    let outcome;
    const read = new ProductBriefApi({ requestTimeoutMs: 30_000 })
      .get(BRIEF_ID, controller.signal)
      .then(
        () => {
          outcome = "fulfilled";
        },
        (error) => {
          outcome = error;
        },
      );

    try {
      await Promise.resolve();
      controller.abort(new DOMException("product changed", "AbortError"));
      await vi.waitFor(
        () =>
          expect(outcome).toBeInstanceOf(
            ProductBriefApiCancelledError,
          ),
        { timeout: 200, interval: 5 },
      );
      expect(outcome).not.toBeInstanceOf(ProductBriefApiError);
    } finally {
      stalled.close();
      await read;
    }
  });

  it("preserves stable stale-version errors for workbench reconciliation", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json(
          {
            code: "VERSION_CONFLICT",
            category: "conflict",
            message: "ProductBrief version is stale",
            retryable: false,
            trace_id: "trace-brief-conflict",
          },
          { status: 409 },
        ),
      ),
    );

    const rejection = new ProductBriefApi()
      .get(BRIEF_ID)
      .catch((error) => error);

    await expect(rejection).resolves.toBeInstanceOf(ProductBriefApiError);
    await expect(rejection).resolves.toMatchObject({
      status: 409,
      envelope: { code: "VERSION_CONFLICT" },
    });
  });
});
