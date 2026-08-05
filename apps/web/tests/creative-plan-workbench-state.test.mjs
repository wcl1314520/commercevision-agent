import { describe, expect, it } from "vitest";

import { CreativePlanApiError } from "../lib/creative-plan-api";
import {
  classifyCreativePlanCommandFailure,
  creativePlanCommandAvailability,
} from "../lib/creative-plan-workbench-state";

describe("Creative Plan Workbench command state", () => {
  it.each([
    [409, "conflict"],
    [403, "policy-denied"],
    [410, "retention-expired"],
    [503, "retryable"],
    [422, "rejected"],
  ])("classifies HTTP %s without converting it into approval truth", (status, kind) => {
    expect(classifyCreativePlanCommandFailure(new CreativePlanApiError(status))).toEqual({
      kind,
      status,
    });
  });

  it("allows commands only for the exact current visible version at the approval fence", () => {
    const current = {
      head: { current_version_number: 3, retain_until: "2099-01-01T00:00:00Z" },
    };
    const workflow = {
      status: "AWAITING_PLAN_APPROVAL",
      retention_status: "ACTIVE",
      expires_at: "2099-01-01T00:00:00Z",
    };

    expect(
      creativePlanCommandAvailability(current, workflow, 3, Date.parse("2026-01-01")),
    ).toEqual({ revise: true, decide: true, reason: null });
    expect(
      creativePlanCommandAvailability(current, workflow, 2, Date.parse("2026-01-01")),
    ).toEqual({
      revise: false,
      decide: false,
      reason: "正在查看历史版本；返回当前版本后才能提交。",
    });
  });

  it.each([
    [
      { status: "GENERATING", retention_status: "ACTIVE", expires_at: "2099-01-01T00:00:00Z" },
      "Workflow 已离开方案审批节点。",
    ],
    [
      { status: "AWAITING_PLAN_APPROVAL", retention_status: "EXPIRED", expires_at: "2099-01-01T00:00:00Z" },
      "Workflow 保留期已结束，命令已禁用。",
    ],
    [
      { status: "AWAITING_PLAN_APPROVAL", retention_status: "ACTIVE", expires_at: "2025-01-01T00:00:00Z" },
      "Workflow 保留期已结束，命令已禁用。",
    ],
  ])("fails closed when authority is not actionable", (workflow, reason) => {
    expect(
      creativePlanCommandAvailability(
        { head: { current_version_number: 3, retain_until: "2099-01-01T00:00:00Z" } },
        workflow,
        3,
        Date.parse("2026-01-01"),
      ),
    ).toEqual({ revise: false, decide: false, reason });
  });
});
