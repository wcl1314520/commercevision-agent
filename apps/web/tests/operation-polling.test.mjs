import { describe, expect, it } from "vitest";

import {
  isProductBriefOperationPollTerminal,
  operationPollDelayMs,
  shouldContinueOperationPolling,
} from "../lib/operation-polling";

describe("durable operation polling policy", () => {
  it("backs off to a bounded interval", () => {
    const maximumJitter = () => 1;
    expect(operationPollDelayMs(1, maximumJitter)).toBe(1000);
    expect(operationPollDelayMs(2, maximumJitter)).toBe(2000);
    expect(operationPollDelayMs(3, maximumJitter)).toBe(4000);
    expect(operationPollDelayMs(20, maximumJitter)).toBe(10000);
  });

  it("adds equal jitter without exceeding the interval cap", () => {
    const minimumJitter = () => 0;
    expect(operationPollDelayMs(1, minimumJitter)).toBe(500);
    expect(operationPollDelayMs(20, minimumJitter)).toBe(5000);
  });

  it("stops automatically after its request budget", () => {
    expect(shouldContinueOperationPolling(1)).toBe(true);
    expect(shouldContinueOperationPolling(23)).toBe(true);
    expect(shouldContinueOperationPolling(24)).toBe(false);
  });

  it("classifies every polling terminal state through one canonical policy", () => {
    expect(
      [
        "PENDING",
        "CLAIMED",
        "RUNNING",
        "RECONCILING",
        "RETRYABLE_FAILED",
      ].map(isProductBriefOperationPollTerminal),
    ).toEqual([false, false, false, false, false]);
    expect(
      [
        "WAITING_HUMAN",
        "SUCCEEDED",
        "FAILED",
        "CANCELLED",
      ].map(isProductBriefOperationPollTerminal),
    ).toEqual([true, true, true, true]);
  });
});
