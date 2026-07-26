import { describe, expect, it } from "vitest";

import { validationPresentation } from "../lib/validation-presentation";

function status({
  assetStatus = "VALIDATING",
  operationState = "RUNNING",
  retryable = false,
  failureCode = null,
  verdict = "PASS",
  reasonCode = null,
} = {}) {
  return {
    asset_status: assetStatus,
    operation: {
      state: operationState,
      retryable,
      failure_code: failureCode,
    },
    stages: [{ verdict, reason_code: reasonCode }],
  };
}

describe("validation presentation", () => {
  it("keeps retryable failure distinct from policy outcomes", () => {
    expect(
      validationPresentation(
        status({
          operationState: "RETRYABLE_FAILED",
          retryable: true,
          failureCode: "PROVIDER_TIMEOUT",
        }),
        null,
      ),
    ).toEqual({ kind: "retryable", reason: "PROVIDER_TIMEOUT" });
  });

  it("presents pending review as a recoverable human gate", () => {
    expect(
      validationPresentation(
        status({
          assetStatus: "PENDING_REVIEW",
          operationState: "SUCCEEDED",
          verdict: "REVIEW",
          reasonCode: "CONTENT_SAFETY_REVIEW",
        }),
        null,
      ),
    ).toEqual({ kind: "review", reason: "CONTENT_SAFETY_REVIEW" });
  });

  it("uses only block evidence for terminal rejection", () => {
    expect(
      validationPresentation(
        status({
          assetStatus: "BLOCKED",
          operationState: "FAILED",
          failureCode: "ASSET_VALIDATION_REJECTED",
          verdict: "BLOCK",
          reasonCode: "CONTENT_SAFETY_BLOCKED",
        }),
        null,
      ),
    ).toEqual({ kind: "rejected", reason: "CONTENT_SAFETY_BLOCKED" });
  });

  it("keeps terminal rejection visible while quarantine cleanup retries", () => {
    expect(
      validationPresentation(
        status({
          assetStatus: "BLOCKED",
          operationState: "RETRYABLE_FAILED",
          retryable: true,
          failureCode: "REJECTED_ASSET_CLEANUP_STORAGE_UNAVAILABLE",
          verdict: "BLOCK",
          reasonCode: "CONTENT_SAFETY_BLOCKED",
        }),
        null,
      ),
    ).toEqual({
      kind: "rejected",
      reason: "CONTENT_SAFETY_BLOCKED",
      cleanup_retrying: true,
      cleanup_reason: "REJECTED_ASSET_CLEANUP_STORAGE_UNAVAILABLE",
    });
  });

  it("presents exhausted infrastructure failure separately from rejection", () => {
    expect(
      validationPresentation(
        status({
          assetStatus: "VALIDATING",
          operationState: "FAILED",
          failureCode: "MALWARE_SCANNER_UNAVAILABLE",
          verdict: "RETRYABLE_FAILURE",
          reasonCode: "MALWARE_SCANNER_UNAVAILABLE",
        }),
        null,
      ),
    ).toEqual({ kind: "failed", reason: "MALWARE_SCANNER_UNAVAILABLE" });
  });

  it("treats block evidence as rejection even before the asset projection catches up", () => {
    expect(
      validationPresentation(
        status({
          assetStatus: "VALIDATING",
          operationState: "FAILED",
          failureCode: "CONTENT_SAFETY_BLOCKED",
          verdict: "BLOCK",
          reasonCode: "CONTENT_SAFETY_BLOCKED",
        }),
        null,
      ),
    ).toEqual({ kind: "rejected", reason: "CONTENT_SAFETY_BLOCKED" });
  });
});
