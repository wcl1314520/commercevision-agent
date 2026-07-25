import { describe, expect, it } from "vitest";

import {
  decodePersistedUpload,
  encodePersistedUpload,
  reduceUploadWorkflow,
  uploadStorageKey,
} from "../lib/upload-workflow";

const createRequest = {
  retention_class: "FOUNDATION",
  asset_kind: "IMAGE",
  filename: "product.png",
  declared_mime: "image/png",
  byte_length: 67,
  sha256: "a".repeat(64),
  workflow_id: null,
  product_id: "product-1",
  sku_id: null,
  category: "beauty",
  role: "product-primary",
};

const openUpload = {
  schemaVersion: 1,
  sessionId: "019f8a00-0000-7000-8000-000000000001",
  finalizeAttempt: {
    idempotencyKey: "web-upload-finalize-original-0001",
    request: { expected_version: 1 },
  },
  stage: "OPEN",
  createIdempotencyKey: "web-upload-create-original-0001",
  createRequest,
};

describe("persisted upload workflow", () => {
  it("names storage by workspace and product", () => {
    expect(uploadStorageKey("product-1")).toBe(
      "commercevision:upload:catalog-demo:product-1",
    );
  });

  it("round-trips the versioned persisted schema", () => {
    expect(decodePersistedUpload(encodePersistedUpload(openUpload))).toEqual(
      openUpload,
    );
  });

  it("migrates the pre-schema finalize fields into one atomic attempt", () => {
    const legacy = JSON.stringify({
      sessionId: openUpload.sessionId,
      finalizeIdempotencyKey: "legacy-finalize-key-0001",
      finalizeExpectedVersion: 3,
      stage: "FINALIZING",
      createIdempotencyKey: openUpload.createIdempotencyKey,
      createRequest,
    });

    expect(decodePersistedUpload(legacy)).toEqual({
      ...openUpload,
      finalizeAttempt: {
        idempotencyKey: "legacy-finalize-key-0001",
        request: { expected_version: 3 },
      },
      stage: "FINALIZING",
    });
  });

  it("migrates a pre-schema create attempt without rotating its identity", () => {
    const legacy = JSON.stringify({
      createIdempotencyKey: "legacy-create-key-0001",
      createRequest,
      stage: "CREATING",
    });

    expect(decodePersistedUpload(legacy)).toEqual({
      schemaVersion: 1,
      createIdempotencyKey: "legacy-create-key-0001",
      createRequest,
      stage: "CREATING",
    });
  });

  it.each([
    null,
    "",
    "not-json",
    JSON.stringify({ schemaVersion: 1, stage: "OPEN" }),
    JSON.stringify({
      ...openUpload,
      finalizeAttempt: {
        idempotencyKey: "key",
        request: { expected_version: 1.5 },
      },
    }),
  ])("fails closed for malformed persisted state", (raw) => {
    expect(decodePersistedUpload(raw)).toBeNull();
  });

  it("keeps the exact attempt when finalize delivery is uncertain", () => {
    const uploaded = { ...openUpload, stage: "UPLOADED" };
    const finalizing = reduceUploadWorkflow(uploaded, {
      type: "FINALIZE_STARTED",
    });

    expect(finalizing).toEqual({
      ...uploaded,
      stage: "FINALIZING",
    });
    expect(finalizing.finalizeAttempt).toBe(openUpload.finalizeAttempt);
  });

  it("rotates key and request atomically after a known version conflict", () => {
    const reconciled = reduceUploadWorkflow(
      { ...openUpload, stage: "FINALIZING" },
      {
        type: "FINALIZE_RECONCILED",
        idempotencyKey: "web-upload-finalize-reconciled-0002",
        expectedVersion: 4,
        nextStage: "FINALIZING",
      },
    );

    expect(reconciled).toEqual({
      ...openUpload,
      finalizeAttempt: {
        idempotencyKey: "web-upload-finalize-reconciled-0002",
        request: { expected_version: 4 },
      },
      stage: "FINALIZING",
    });
    expect(reconciled.finalizeAttempt).not.toBe(openUpload.finalizeAttempt);
  });

  it("opens a new atomic finalize attempt after a known missing upload", () => {
    const reconciled = reduceUploadWorkflow(
      { ...openUpload, stage: "FINALIZING" },
      {
        type: "FINALIZE_RECONCILED",
        idempotencyKey: "web-upload-finalize-reupload-0002",
        expectedVersion: 2,
        nextStage: "OPEN",
      },
    );

    expect(reconciled.stage).toBe("OPEN");
    expect(reconciled.finalizeAttempt).toEqual({
      idempotencyKey: "web-upload-finalize-reupload-0002",
      request: { expected_version: 2 },
    });
  });

  it("records terminal asset identity without changing the completed attempt", () => {
    const finalized = reduceUploadWorkflow(
      { ...openUpload, stage: "FINALIZING" },
      {
        type: "FINALIZED",
        assetId: "019f8a00-0000-7000-8000-000000000099",
        sessionId: openUpload.sessionId,
      },
    );

    expect(finalized).toMatchObject({
      stage: "FINALIZED",
      assetId: "019f8a00-0000-7000-8000-000000000099",
      finalizeAttempt: openUpload.finalizeAttempt,
    });
  });
});
