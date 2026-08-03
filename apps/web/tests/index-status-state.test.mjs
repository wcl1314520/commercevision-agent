import { describe, expect, it } from "vitest";

import {
  acceptIndexStatusResponse,
  decodeAssetIndexStatus,
  indexStatusRetryDelayMs,
  indexStatusPresentation,
  shouldRefreshIndexStatus,
} from "../lib/index-status-state";

const status = {
  asset_id: "019f8a00-0000-7000-8000-000000000011",
  asset_version_id: "019f8a00-0000-7000-8000-000000000012",
  state: "RETRYABLE_FAILED",
  retryable: true,
  failure_reason: "PROVIDER_THROTTLED",
  indexed_at: null,
  updated_at: "2026-07-31T00:00:00Z",
};

describe("IMAGE index status runtime state", () => {
  it("rejects infrastructure leakage and malformed states at runtime", () => {
    expect(() =>
      decodeAssetIndexStatus({ ...status, collection_name: "internal" }),
    ).toThrow(/unexpected field/);
    expect(() =>
      decodeAssetIndexStatus({ ...status, state: "MILVUS_PARTITION" }),
    ).toThrow(/state/);
  });

  it("refreshes transient states and distinguishes terminal failures", () => {
    expect(shouldRefreshIndexStatus("PENDING")).toBe(true);
    expect(shouldRefreshIndexStatus("PROCESSING")).toBe(true);
    expect(shouldRefreshIndexStatus("RETRYABLE_FAILED")).toBe(true);
    expect(shouldRefreshIndexStatus("INDEXED")).toBe(false);
    expect(indexStatusPresentation(status)).toMatchObject({
      tone: "retry",
      detail: "系统将自动重试：服务繁忙，正在等待可用容量",
    });
    expect(
      indexStatusPresentation({
        ...status,
        state: "PERMANENT_FAILED",
        retryable: false,
      }),
    ).toMatchObject({
      tone: "error",
      detail: "原因：服务繁忙，正在等待可用容量",
    });
  });

  it("requires UTC timestamps and hides unknown backend reason codes", () => {
    for (const updated_at of [
      "July 31, 2026 12:00:00",
      "2026-07-31Z",
      "2026-07-31 08:00:00Z",
      "2026-07-31T08:00:00+08:00",
      "2026-02-30T08:00:00Z",
    ]) {
      expect(() =>
        decodeAssetIndexStatus({ ...status, updated_at }),
      ).toThrow(/ISO date-time/);
    }
    expect(
      indexStatusPresentation({
        ...status,
        failure_reason: "INTERNAL_BACKEND_TABLE_X",
      }).detail,
    ).toBe("系统将自动重试：索引暂时无法完成");
    expect(indexStatusRetryDelayMs(1)).toBe(5_000);
    expect(indexStatusRetryDelayMs(3)).toBe(20_000);
    expect(indexStatusRetryDelayMs(20)).toBe(30_000);
  });

  it("ignores late responses from an older asset or refresh epoch", () => {
    expect(
      acceptIndexStatusResponse(
        {
          assetId: status.asset_id,
          requestEpoch: 4,
        },
        {
          assetId: status.asset_id,
          requestEpoch: 3,
        },
        status,
      ),
    ).toBeNull();
    expect(
      acceptIndexStatusResponse(
        {
          assetId: "019f8a00-0000-7000-8000-000000000099",
          requestEpoch: 4,
        },
        {
          assetId: status.asset_id,
          requestEpoch: 4,
        },
        status,
      ),
    ).toBeNull();
  });
});
