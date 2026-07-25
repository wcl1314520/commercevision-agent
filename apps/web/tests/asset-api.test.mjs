import {
  afterEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

import { AssetApi } from "../lib/asset-api";

describe("AssetApi durable operation reads", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("gets the real operation state from the exact operation resource", async () => {
    const operationId = "019f8a00-0000-7000-8000-000000000013";
    const fetchMock = vi.fn(async () =>
      Response.json({
        id: operationId,
        workspace_id: "catalog-demo",
        state: "RETRYABLE_FAILED",
        version: 4,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const operation = await new AssetApi({
      baseUrl: "https://web.example",
      workspaceId: "catalog-demo",
    }).getOperation(operationId);

    expect(operation).toMatchObject({
      id: operationId,
      state: "RETRYABLE_FAILED",
      version: 4,
    });
    expect(fetchMock).toHaveBeenCalledOnce();
    const [input, init] = fetchMock.mock.calls[0];
    expect(String(input)).toBe(
      `https://web.example/api/v1/operations/${operationId}`,
    );
    expect(init).toMatchObject({
      cache: "no-store",
    });
    expect(init?.method).toBeUndefined();
    expect(Object.fromEntries(new Headers(init?.headers).entries())).toEqual({
      accept: "application/json",
      "x-workspace-id": "catalog-demo",
    });
  });

  it("aborts a control-plane request at the configured deadline", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn(
      async (_input, init) =>
        await new Promise((_resolve, reject) => {
          init.signal.addEventListener(
            "abort",
            () => reject(init.signal.reason),
            { once: true },
          );
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const request = new AssetApi({
      baseUrl: "https://web.example",
      workspaceId: "catalog-demo",
      requestTimeoutMs: 50,
    }).getOperation("019f8a00-0000-7000-8000-000000000013");
    const rejection = expect(request).rejects.toMatchObject({
      name: "AssetApiError",
      status: 504,
    });
    await vi.advanceTimersByTimeAsync(50);

    await rejection;
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("aborts the exact upload session with a durable idempotency identity", async () => {
    const fetchMock = vi.fn(async () =>
      Response.json({
        id: "019f8a00-0000-7000-8000-000000000020",
        status: "ABORTED",
        version: 3,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const api = new AssetApi({
      baseUrl: "https://web.example",
      workspaceId: "catalog-demo",
      actorId: "catalog-workbench",
    });

    await api.abortUploadSession(
      "019f8a00-0000-7000-8000-000000000020",
      2,
      "web-upload-abort-0001",
    );

    const [input, init] = fetchMock.mock.calls[0];
    expect(String(input)).toBe(
      "https://web.example/api/v1/upload-sessions/" +
        "019f8a00-0000-7000-8000-000000000020:abort",
    );
    expect(init).toMatchObject({
      body: '{"expected_version":2}',
      method: "POST",
    });
    expect(Object.fromEntries(new Headers(init.headers).entries())).toEqual({
      accept: "application/json",
      "content-type": "application/json",
      "idempotency-key": "web-upload-abort-0001",
      "x-actor-id": "catalog-workbench",
      "x-workspace-id": "catalog-demo",
    });
  });
});
