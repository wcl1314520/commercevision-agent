import { describe, expect, it } from "vitest";

import {
  clearCreativePlanReviewSession,
  creativePlanReviewSessionKey,
  readCreativePlanReviewSession,
  writeCreativePlanReviewSession,
} from "../lib/creative-plan-review-session";

const WORKFLOW_ID = "019f8a00-0000-7000-8000-000000000122";
const PLAN_ID = "019f8a00-0000-7000-8000-000000000121";
const VERSION_ID = "019f8a00-0000-7000-8000-000000000123";

function memoryStorage() {
  const values = new Map();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
}

function payload() {
  return {
    schema_version: "creative-plan.v1",
    directions: [
      {
        key: "hero",
        image_role: "HERO",
        scene: "Studio",
        composition: "Centered",
        camera: "85mm",
        lighting: "Softbox",
        color_direction: "Brand blue",
        product_constraints: ["Preserve shape"],
        required_elements: ["Label"],
        prohibited_elements: [],
        citation_selections: [],
        candidate_count: 2,
        quality_targets: ["Sharp label"],
        repair_scope: [],
        tool_intents: [],
      },
    ],
  };
}

describe("Creative Plan review session", () => {
  it("restores lookup, review position, stream cursor, and recoverable draft only", () => {
    const storage = memoryStorage();
    const session = {
      workspaceId: "catalog-demo",
      workflowId: WORKFLOW_ID,
      creativePlanId: PLAN_ID,
      selectedVersionNumber: 2,
      streamCursor: "v1.current.cGF5bG9hZA.c2lnbmF0dXJl",
      draft: {
        baseVersionId: VERSION_ID,
        baseVersionNumber: 2,
        payloadText: JSON.stringify(payload()),
        revisionReason: "Improve hero balance",
      },
    };

    writeCreativePlanReviewSession(storage, session);

    expect(readCreativePlanReviewSession(storage, "catalog-demo")).toEqual(session);
    const serialized = storage.getItem(
      creativePlanReviewSessionKey("catalog-demo"),
    );
    expect(serialized).not.toContain("APPROVE");
    expect(serialized).not.toContain("authorization");
  });

  it("fails closed and deletes malformed, cross-workspace, or oversized state", () => {
    for (const raw of [
      "{broken",
      JSON.stringify({ schemaVersion: 1, workspaceId: "other-workspace" }),
      "x".repeat(70_000),
    ]) {
      const storage = memoryStorage();
      const key = creativePlanReviewSessionKey("catalog-demo");
      storage.setItem(key, raw);

      expect(readCreativePlanReviewSession(storage, "catalog-demo")).toBeNull();
      expect(storage.getItem(key)).toBeNull();
    }
  });

  it("clears the exact workspace session without touching another workspace", () => {
    const storage = memoryStorage();
    storage.setItem(creativePlanReviewSessionKey("catalog-demo"), "first");
    storage.setItem(creativePlanReviewSessionKey("other-workspace"), "second");

    clearCreativePlanReviewSession(storage, "catalog-demo");

    expect(storage.getItem(creativePlanReviewSessionKey("catalog-demo"))).toBeNull();
    expect(storage.getItem(creativePlanReviewSessionKey("other-workspace"))).toBe(
      "second",
    );
  });

  it("fails closed when browser storage rejects both reads and cleanup", () => {
    const deniedStorage = {
      getItem() {
        throw new DOMException("denied", "SecurityError");
      },
      setItem() {
        throw new DOMException("denied", "SecurityError");
      },
      removeItem() {
        throw new DOMException("denied", "SecurityError");
      },
    };

    expect(() =>
      readCreativePlanReviewSession(deniedStorage, "catalog-demo"),
    ).not.toThrow();
    expect(readCreativePlanReviewSession(deniedStorage, "catalog-demo")).toBeNull();
  });
});
