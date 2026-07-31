import { afterEach, describe, expect, it, vi } from "vitest";

import {
  BrandProfileApi,
  BrandProfileApiCancelledError,
  BrandProfileApiError,
} from "../lib/brand-profile-api";
import {
  BrandProfileProtocolError,
  decodeBrandProfileDraft,
} from "../lib/brand-profile-api-decoders";

const PROFILE_ID = "019f8a00-0000-7000-8000-000000000041";

function draft() {
  return {
    rules: [
      {
        code: "logo.clear-space",
        scope: "VISUAL",
        instruction: "Keep one mark-width of clear space.",
      },
    ],
    approved_colors: [{ name: "Primary", value: "#1457FF" }],
    required_marks: ["Northstar wordmark"],
    prohibited_elements: ["Competitor marks"],
    tone_constraints: ["Calm"],
    copy_constraints: ["No unsupported claims"],
    purpose: "BRAND_CONTEXT",
    provider: "alibaba",
    requires_derivative: true,
    selected_assets: [
      {
        asset_version_id: "019f8a00-0000-7000-8000-000000000042",
        role: "LOGO",
      },
    ],
  };
}

function profileResponse(overrides = {}) {
  return {
    id: PROFILE_ID,
    workspace_id: "catalog-demo",
    brand: "Northstar Labs",
    profile_key: "primary",
    state: "DRAFT",
    draft: draft(),
    current_version_id: null,
    current_version_number: 0,
    version: 1,
    stale_at: null,
    created_by: "brand-admin",
    created_at: "2026-07-30T07:00:00Z",
    updated_by: "brand-admin",
    updated_at: "2026-07-30T07:00:00Z",
    ...overrides,
  };
}

function publishedVersion(versionNumber = 2) {
  return {
    id: "019f8a00-0000-7000-8000-000000000043",
    workspace_id: "catalog-demo",
    profile_id: PROFILE_ID,
    version_number: versionNumber,
    draft: draft(),
    content_sha256: "a".repeat(64),
    published_by: "brand-admin",
    published_at: "2026-07-30T08:00:00Z",
    members: [
      {
        ordinal: 0,
        asset_id: "019f8a00-0000-7000-8000-000000000044",
        asset_version_id: "019f8a00-0000-7000-8000-000000000042",
        role: "LOGO",
        published_rights_record_id:
          "019f8a00-0000-7000-8000-000000000046",
        published_rights_record_version: 1,
        currently_usable: true,
        current_reason_code: "AUTHORIZED",
        current_rights_record_id:
          "019f8a00-0000-7000-8000-000000000046",
        current_rights_record_version: 1,
        decided_at: "2026-07-30T08:00:00Z",
      },
    ],
  };
}

describe("BrandProfileApi", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("applies decoder character limits to Unicode code points", () => {
    const boundedDraft = draft();
    boundedDraft.rules[0].instruction = "😀".repeat(1024);

    expect(() => decodeBrandProfileDraft(boundedDraft)).not.toThrow();

    boundedDraft.rules[0].instruction = "😀".repeat(1025);
    expect(() => decodeBrandProfileDraft(boundedDraft)).toThrow(
      BrandProfileProtocolError,
    );
  });

  it("accepts an API response at the Unicode code-point boundary", async () => {
    const responseBody = profileResponse({ brand: "😀".repeat(128) });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(responseBody)),
    );

    await expect(new BrandProfileApi().get(PROFILE_ID)).resolves.toEqual(
      responseBody,
    );
  });

  it("creates a profile through the exact idempotent workspace seam", async () => {
    const responseBody = profileResponse({
      workspace_id: "brand-workspace",
    });
    const fetchMock = vi.fn(async () =>
      Response.json(responseBody, { status: 201 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const payload = {
      brand: "Northstar Labs",
      profile_key: "primary",
      draft: draft(),
    };

    await expect(
      new BrandProfileApi({
        baseUrl: "https://web.example",
        workspaceId: "brand-workspace",
        actorId: "brand-workbench",
      }).create(payload, "web-brand-profile-create-0001"),
    ).resolves.toEqual(responseBody);

    const [input, init] = fetchMock.mock.calls[0];
    expect(String(input)).toBe(
      "https://web.example/api/v1/brand-profiles",
    );
    expect(init).toMatchObject({
      body: JSON.stringify(payload),
      cache: "no-store",
      method: "POST",
    });
    expect(Object.fromEntries(new Headers(init.headers).entries())).toEqual({
      accept: "application/json",
      "content-type": "application/json",
      "idempotency-key": "web-brand-profile-create-0001",
      "x-actor-id": "brand-workbench",
      "x-workspace-id": "brand-workspace",
    });
  });

  it("updates, validates, and publishes using optimistic version commands", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        Response.json(profileResponse({ version: 4 })),
      )
      .mockResolvedValueOnce(
        Response.json({
          profile_id: PROFILE_ID,
          profile_version: 4,
          valid: true,
          decided_at: "2026-07-30T08:00:00Z",
          issues: [],
        }),
      )
      .mockResolvedValueOnce(
        Response.json(
          profileResponse({
            version: 5,
            state: "ACTIVE",
            current_version_id:
              "019f8a00-0000-7000-8000-000000000043",
            current_version_number: 1,
          }),
          { status: 201 },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);
    const api = new BrandProfileApi();
    const update = { expected_version: 3, draft: draft() };

    await api.updateDraft(
      PROFILE_ID,
      update,
      "web-brand-profile-update-0001",
    );
    await api.validate(PROFILE_ID, { expected_version: 4 });
    await api.publish(
      PROFILE_ID,
      { expected_version: 4 },
      "web-brand-profile-publish-0001",
    );

    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      `/api/v1/brand-profiles/${PROFILE_ID}/draft`,
      `/api/v1/brand-profiles/${PROFILE_ID}:validate`,
      `/api/v1/brand-profiles/${PROFILE_ID}:publish`,
    ]);
    expect(fetchMock.mock.calls.map(([, init]) => init.method)).toEqual([
      "PUT",
      "POST",
      "POST",
    ]);
    expect(
      new Headers(fetchMock.mock.calls[1][1].headers).has("idempotency-key"),
    ).toBe(false);
    expect(
      new Headers(fetchMock.mock.calls[1][1].headers).get("x-actor-id"),
    ).toBe("catalog-workbench");
  });

  it("fails closed when a profile list crosses the requested workspace or brand", async () => {
    const crossWorkspace = {
      id: PROFILE_ID,
      workspace_id: "other-workspace",
      brand: "Northstar Labs",
      profile_key: "primary",
      state: "DRAFT",
      draft: draft(),
      current_version_id: null,
      current_version_number: 0,
      version: 1,
      stale_at: null,
      created_by: "brand-admin",
      created_at: "2026-07-30T07:00:00Z",
      updated_by: "brand-admin",
      updated_at: "2026-07-30T07:00:00Z",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json({ items: [crossWorkspace], next_cursor: null }),
      ),
    );

    await expect(
      new BrandProfileApi({
        workspaceId: "brand-workspace",
      }).list({ brand: "Northstar Labs" }),
    ).rejects.toMatchObject({ status: 502 });
  });

  it("accepts only bounded signed v1 response cursors", async () => {
    const signedCursor = "v1.current.cGF5bG9hZA.c2lnbmF0dXJl";
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        Response.json({
          items: [profileResponse()],
          next_cursor: signedCursor,
        }),
      )
      .mockResolvedValueOnce(
        Response.json({
          items: [profileResponse()],
          next_cursor: "eyJraW5kIjoicHJvZmlsZSJ9",
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const api = new BrandProfileApi();

    await expect(
      api.list({ brand: "Northstar Labs" }),
    ).resolves.toMatchObject({ next_cursor: signedCursor });
    await expect(
      api.list({ brand: "Northstar Labs" }),
    ).rejects.toMatchObject({ status: 502 });
  });

  it("fails closed on malformed UUIDs, enums, oversized pages, and cursors", async () => {
    const validProfile = {
      id: PROFILE_ID,
      workspace_id: "brand-workspace",
      brand: "Northstar Labs",
      profile_key: "primary",
      state: "DRAFT",
      draft: draft(),
      current_version_id: null,
      current_version_number: 0,
      version: 1,
      stale_at: null,
      created_by: "brand-admin",
      created_at: "2026-07-30T07:00:00Z",
      updated_by: "brand-admin",
      updated_at: "2026-07-30T07:00:00Z",
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        Response.json({
          ...validProfile,
          id: "NOT-A-UUID",
          state: "UNKNOWN",
        }),
      )
      .mockResolvedValueOnce(
        Response.json({
          items: [
            validProfile,
            {
              ...validProfile,
              id: "019f8a00-0000-7000-8000-000000000044",
            },
          ],
          next_cursor: "cursor with spaces",
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const api = new BrandProfileApi({ workspaceId: "brand-workspace" });

    await expect(api.get(PROFILE_ID)).rejects.toMatchObject({ status: 502 });
    await expect(
      api.list({ brand: "Northstar Labs", limit: 1 }),
    ).rejects.toMatchObject({ status: 502 });
  });

  it("fails closed on malformed capability and validation envelopes", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        Response.json({ administrator: "yes" }),
      )
      .mockResolvedValueOnce(
        Response.json({
          profile_id: PROFILE_ID,
          profile_version: 4,
          valid: true,
          decided_at: "2026-07-30T08:00:00Z",
          issues: [
            {
              asset_version_id:
                "019f8a00-0000-7000-8000-000000000042",
              role: "LOGO",
              reason_code: "RIGHTS_REVOKED",
              message: "Revoked",
            },
          ],
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const api = new BrandProfileApi({ workspaceId: "brand-workspace" });

    await expect(api.getWorkspaceCapabilities()).rejects.toMatchObject({
      status: 502,
    });
    await expect(
      api.validate(PROFILE_ID, { expected_version: 4 }),
    ).rejects.toMatchObject({ status: 502 });
  });

  it("uses encoded list cursors and immutable publication numbers", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(Response.json({ items: [], next_cursor: null }))
      .mockResolvedValueOnce(
        Response.json(profileResponse({ version: 3 })),
      )
      .mockResolvedValueOnce(Response.json({ items: [], next_cursor: null }))
      .mockResolvedValueOnce(Response.json(publishedVersion()));
    vi.stubGlobal("fetch", fetchMock);
    const api = new BrandProfileApi();

    await api.list({
      brand: "Northstar Labs/中国",
      cursor: "opaque_cursor-1",
      limit: 2,
    });
    await api.get(PROFILE_ID);
    await api.listVersions(PROFILE_ID, {
      cursor: "history_cursor-2",
      limit: 1,
    });
    await api.getVersion(PROFILE_ID, 2);

    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      "/api/v1/brand-profiles?brand=Northstar+Labs%2F%E4%B8%AD%E5%9B%BD&cursor=opaque_cursor-1&limit=2",
      `/api/v1/brand-profiles/${PROFILE_ID}`,
      `/api/v1/brand-profiles/${PROFILE_ID}/versions?cursor=history_cursor-2&limit=1`,
      `/api/v1/brand-profiles/${PROFILE_ID}/versions/2`,
    ]);
    expect(
      fetchMock.mock.calls.every(([, init]) => init.cache === "no-store"),
    ).toBe(true);
    expect(() =>
      api.list({ brand: "Northstar Labs", limit: 3 }),
    ).toThrow(/between 1 and 2/);
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it("reads administrator capability through the workspace-bound gateway", async () => {
    const fetchMock = vi.fn(async () =>
      Response.json({ administrator: true }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      new BrandProfileApi({
        baseUrl: "https://web.example",
        workspaceId: "brand-workspace",
      }).getWorkspaceCapabilities(),
    ).resolves.toEqual({ administrator: true });

    expect(String(fetchMock.mock.calls[0][0])).toBe(
      "https://web.example/api/web-capabilities",
    );
    expect(
      new Headers(fetchMock.mock.calls[0][1].headers).get("x-workspace-id"),
    ).toBe("brand-workspace");
  });

  it("propagates caller cancellation without reporting an API outage", async () => {
    const controller = new AbortController();
    let fetchSignal;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_input, init) =>
          new Promise((_resolve, reject) => {
            fetchSignal = init.signal;
            init.signal.addEventListener(
              "abort",
              () => reject(init.signal.reason),
              { once: true },
            );
          }),
      ),
    );

    const request = new BrandProfileApi().get(
      PROFILE_ID,
      controller.signal,
    );
    controller.abort(new DOMException("profile changed", "AbortError"));

    await expect(request).rejects.toBeInstanceOf(
      BrandProfileApiCancelledError,
    );
    await expect(request).rejects.not.toBeInstanceOf(BrandProfileApiError);
    expect(fetchSignal.aborted).toBe(true);
  });

  it("preserves stable version-conflict details for reconciliation", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json(
          {
            code: "VERSION_CONFLICT",
            category: "conflict",
            message: "Brand Profile version is stale",
            retryable: false,
            request_id: "request-brand-profile-conflict",
            trace_id: "trace-brand-profile-conflict",
          },
          { status: 409 },
        ),
      ),
    );

    const rejection = new BrandProfileApi()
      .updateDraft(
        PROFILE_ID,
        { expected_version: 3, draft: draft() },
        "web-brand-profile-conflict",
      )
      .catch((error) => error);

    await expect(rejection).resolves.toBeInstanceOf(BrandProfileApiError);
    await expect(rejection).resolves.toMatchObject({
      status: 409,
      envelope: { code: "VERSION_CONFLICT" },
    });
  });
});
