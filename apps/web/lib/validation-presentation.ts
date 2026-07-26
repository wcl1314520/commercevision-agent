import type {
  AssetState,
  OperationState,
  ValidationVerdict,
} from "./generated/catalog-api";

type ValidationPresentationInput = {
  asset_status: AssetState;
  operation: {
    state: OperationState;
    retryable: boolean;
    failure_code: string | null;
  };
  stages: Array<{
    verdict: ValidationVerdict;
    reason_code: string | null;
  }>;
};

export type ValidationPresentation =
  | { kind: "none"; reason: null }
  | { kind: "retryable"; reason: string | null }
  | { kind: "review"; reason: string | null }
  | {
      kind: "rejected";
      reason: string | null;
      cleanup_retrying?: true;
      cleanup_reason?: string | null;
    }
  | { kind: "failed"; reason: string | null };

export function validationPresentation(
  status: ValidationPresentationInput | null,
  fallbackOperationState: OperationState | null,
): ValidationPresentation {
  const operationState = status?.operation.state ?? fallbackOperationState;
  const blockStage = status?.stages.find(
    (stage) => stage.verdict === "BLOCK",
  );
  if (status?.asset_status === "BLOCKED" || blockStage !== undefined) {
    const rejected = {
      kind: "rejected",
      reason:
        blockStage?.reason_code ?? status?.operation.failure_code ?? null,
    } as const;
    if (
      status?.operation.retryable ||
      operationState === "RETRYABLE_FAILED"
    ) {
      return {
        ...rejected,
        cleanup_retrying: true,
        cleanup_reason: status?.operation.failure_code ?? null,
      };
    }
    return rejected;
  }
  if (status?.operation.retryable || operationState === "RETRYABLE_FAILED") {
    return {
      kind: "retryable",
      reason: status?.operation.failure_code ?? null,
    };
  }
  if (
    status?.asset_status === "PENDING_REVIEW" ||
    status?.stages.some((stage) => stage.verdict === "REVIEW")
  ) {
    return {
      kind: "review",
      reason:
        status.stages.find((stage) => stage.verdict === "REVIEW")
          ?.reason_code ?? null,
    };
  }
  if (operationState === "FAILED") {
    return {
      kind: "failed",
      reason: status?.operation.failure_code ?? null,
    };
  }
  return { kind: "none", reason: null };
}
