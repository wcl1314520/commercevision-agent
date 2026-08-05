import { CreativePlanApiError } from "./creative-plan-api";

export type CreativePlanCommandFailure =
  | { kind: "conflict"; status: 409 }
  | { kind: "policy-denied"; status: 401 | 403 }
  | { kind: "retention-expired"; status: 410 }
  | { kind: "retryable"; status: number }
  | { kind: "rejected"; status: number };

export type CreativePlanCommandAvailability = {
  revise: boolean;
  decide: boolean;
  reason: string | null;
};

type CurrentAuthority = {
  head: {
    current_version_number: number;
    retain_until: string;
  };
};

type WorkflowAuthority = {
  status: string;
  retention_status: string;
  expires_at: string;
};

export function classifyCreativePlanCommandFailure(
  error: unknown,
): CreativePlanCommandFailure {
  if (error instanceof CreativePlanApiError) {
    if (error.status === 409) return { kind: "conflict", status: 409 };
    if (error.status === 401 || error.status === 403) {
      return { kind: "policy-denied", status: error.status };
    }
    if (error.status === 410) {
      return { kind: "retention-expired", status: 410 };
    }
    if (error.envelope?.retryable || error.status >= 500) {
      return { kind: "retryable", status: error.status };
    }
    return { kind: "rejected", status: error.status };
  }
  return { kind: "retryable", status: 0 };
}

export function creativePlanCommandAvailability(
  current: CurrentAuthority,
  workflow: WorkflowAuthority,
  visibleVersionNumber: number,
  now = Date.now(),
): CreativePlanCommandAvailability {
  if (visibleVersionNumber !== current.head.current_version_number) {
    return {
      revise: false,
      decide: false,
      reason: "正在查看历史版本；返回当前版本后才能提交。",
    };
  }
  const retentionDeadline = Math.min(
    Date.parse(current.head.retain_until),
    Date.parse(workflow.expires_at),
  );
  if (
    workflow.retention_status !== "ACTIVE" ||
    !Number.isFinite(retentionDeadline) ||
    now >= retentionDeadline
  ) {
    return {
      revise: false,
      decide: false,
      reason: "Workflow 保留期已结束，命令已禁用。",
    };
  }
  if (workflow.status !== "AWAITING_PLAN_APPROVAL") {
    return {
      revise: false,
      decide: false,
      reason: "Workflow 已离开方案审批节点。",
    };
  }
  return { revise: true, decide: true, reason: null };
}
