import {
  afterEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

import { AssetApi } from "../lib/asset-api";

describe("AssetApi validation status reads", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("gets sanitized stage evidence from the exact asset validation resource", async () => {
    const assetId = "019f8a00-0000-7000-8000-000000000011";
    const fetchMock = vi.fn(async () =>
      Response.json({
        asset_id: assetId,
        operation: { state: "RETRYABLE_FAILED", retryable: true },
        stages: [{ stage: "MALWARE", verdict: "PASS", evidence: { outcome: "CLEAN" } }],
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const validation = await new AssetApi({
      baseUrl: "https://web.example",
      workspaceId: "catalog-demo",
    }).getAssetValidation(assetId);

    expect(validation).toMatchObject({
      asset_id: assetId,
      operation: { state: "RETRYABLE_FAILED", retryable: true },
    });
    expect(String(fetchMock.mock.calls[0][0])).toBe(
      `https://web.example/api/v1/assets/${assetId}/validation`,
    );
  });

  it("gets the sanitized IMAGE index status without vector infrastructure facts", async () => {
    const assetId = "019f8a00-0000-7000-8000-000000000011";
    const fetchMock = vi.fn(async () =>
      Response.json({
        asset_id: assetId,
        asset_version_id: "019f8a00-0000-7000-8000-000000000012",
        state: "INDEXED",
        retryable: false,
        failure_reason: null,
        indexed_at: "2026-07-31T00:00:00Z",
        updated_at: "2026-07-31T00:00:00Z",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const status = await new AssetApi({
      baseUrl: "https://web.example",
      workspaceId: "catalog-demo",
    }).getAssetIndexStatus(assetId);

    expect(status.state).toBe("INDEXED");
    expect(String(fetchMock.mock.calls[0][0])).toBe(
      `https://web.example/api/v1/assets/${assetId}/index-status`,
    );
    expect(status).not.toHaveProperty("collection_name");
    expect(status).not.toHaveProperty("milvus_primary_key");
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
    }).getAssetValidation("019f8a00-0000-7000-8000-000000000011");
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

  it("registers explicit rights and reads immutable history", async () => {
    const assetId = "019f8a00-0000-7000-8000-000000000030";
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        Response.json({
          asset_id: assetId,
          asset_version: 4,
          asset_state: "AVAILABLE",
          current_rights_record: { id: "rights-1", version_number: 1 },
        }),
      )
      .mockResolvedValueOnce(
        Response.json({
          items: [{ id: "rights-1", version_number: 1 }],
          next_cursor: null,
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const api = new AssetApi({
      baseUrl: "https://web.example",
      workspaceId: "catalog-demo",
      actorId: "catalog-workbench",
    });
    const payload = {
      expected_asset_version: 3,
      asset_version_id: "019f8a00-0000-7000-8000-000000000031",
      owner_reference: "owner",
      source: "dam",
      license_reference: "license",
      allowed_uses: [],
      allowed_providers: [],
      derivative_allowed: false,
      public_demo_allowed: false,
      evidence_reference: "evidence://1",
      terms_sha256: "a".repeat(64),
      valid_from: "2026-07-27T00:00:00Z",
      valid_until: null,
      perpetual: true,
    };

    await api.registerRights(assetId, payload, "web-rights-register-0001");
    const history = await api.getRightsHistory(assetId, {
      beforeVersion: 7,
      limit: 25,
    });

    expect(history.items).toHaveLength(1);
    const [registerInput, registerInit] = fetchMock.mock.calls[0];
    expect(String(registerInput)).toBe(
      `https://web.example/api/v1/assets/${assetId}/rights`,
    );
    expect(registerInit).toMatchObject({
      method: "POST",
      body: JSON.stringify(payload),
    });
    expect(
      Object.fromEntries(new Headers(registerInit.headers).entries()),
    ).toMatchObject({
      "idempotency-key": "web-rights-register-0001",
      "x-actor-id": "catalog-workbench",
    });
    expect(String(fetchMock.mock.calls[1][0])).toBe(
      `https://web.example/api/v1/assets/${assetId}/rights?before_version=7&limit=25`,
    );
  });

  it("reads workspace capabilities from the server-side gateway boundary", async () => {
    const fetchMock = vi.fn(async () =>
      Response.json({ administrator: true }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const api = new AssetApi({
      baseUrl: "https://web.example",
      workspaceId: "catalog-demo",
    });

    await expect(api.getWorkspaceCapabilities()).resolves.toEqual({
      administrator: true,
    });
    const [input, init] = fetchMock.mock.calls[0];
    expect(String(input)).toBe("https://web.example/api/web-capabilities");
    expect(Object.fromEntries(new Headers(init.headers).entries())).toEqual({
      accept: "application/json",
      "x-workspace-id": "catalog-demo",
    });
  });
});
