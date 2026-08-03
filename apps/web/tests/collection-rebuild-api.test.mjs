import { afterEach, describe, expect, it, vi } from "vitest";

import {
  CollectionRebuildApi,
  decodeCollectionRebuild,
} from "../lib/collection-rebuild-api";

const ID = "019f8a00-0000-7000-8000-000000000140";

function response(overrides = {}) {
  return {
    id: ID,
    operation_id: ID,
    vector_kind: "IMAGE",
    state: "AWAITING_VALIDATION",
    version: 7,
    snapshot_watermark: "2026-08-04T08:00:00Z",
    replay_watermark: "2026-08-04T08:05:00Z",
    backfill_cursor: null,
    replay_cursor: null,
    processed_count: 42,
    validation: null,
    failure_code: null,
    retire_after: null,
    created_at: "2026-08-04T08:00:00Z",
    updated_at: "2026-08-04T08:06:00Z",
    progress: [],
    ...overrides,
  };
}

describe("CollectionRebuildApi", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("decodes the durable rebuild checkpoint and rejects unknown states", () => {
    expect(decodeCollectionRebuild(response()).processed_count).toBe(42);
    expect(() => decodeCollectionRebuild(response({ state: "UNSAFE" }))).toThrow(
      /state is invalid/,
    );
  });

  it("sends version-fenced validation and activation actions", async () => {
    const fetchMock = vi.fn(async () => Response.json(response(), { status: 202 }));
    vi.stubGlobal("fetch", fetchMock);
    const api = new CollectionRebuildApi("catalog-demo", "https://web.example");

    await api.validate(ID, 7);
    await api.activate(ID, 8);

    expect(String(fetchMock.mock.calls[0][0])).toBe(
      `https://web.example/api/v1/collections/rebuilds/${ID}:validate`,
    );
    expect(fetchMock.mock.calls[0][1].body).toBe(JSON.stringify({ expected_version: 7 }));
    expect(String(fetchMock.mock.calls[1][0])).toBe(
      `https://web.example/api/v1/collections/rebuilds/${ID}:activate`,
    );
  });

  it("never accepts a non-canonical rebuild identity", () => {
    const api = new CollectionRebuildApi();
    expect(() => api.get("NOT-A-UUID")).toThrow(/id is invalid/);
  });
});
