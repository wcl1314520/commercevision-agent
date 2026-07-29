import { describe, expect, it } from "vitest";

import {
  createProductBriefWorkbenchController,
} from "../lib/product-brief-workbench-controller";
import {
  acquireProductBriefBrowserStorages,
  authoritativeGoneAppliesToCurrentWorkbench,
  earliestActiveProductBriefRetentionDeadline,
  exactPendingProductBriefCommandIsDurable,
  productBriefAnalysisAcceptedMatchesPending,
  productBriefResponseMatchesIdentity,
  productBriefRevisionResponseMatchesIdentity,
  productBriefReloadMatchesAcceptedAnalysis,
  productBriefWorkflowContextMatchesIdentity,
} from "../lib/use-product-brief-workbench-controller";
import {
  isMonotonicProductBriefVersion,
  pendingProductBriefCommandFor,
  productBriefCommandsMatch,
  productBriefSourceFor,
  restoredProductBriefDrafts,
  structuredValueError,
} from "../lib/product-brief-workbench-state";

const EVIDENCE_HASH = "a".repeat(64);

function revisionEvidence(hash = EVIDENCE_HASH) {
  return {
    source_asset_version_id: "asset-version-a",
    kind: "IMAGE_REGION",
    reference: `asset-region://${hash}`,
    region: [0.1, 0.2, 0.8, 0.9],
    excerpt_sha256: hash,
  };
}

describe("ProductBrief workbench state", () => {
  it("acquires session and local browser storage independently", () => {
    const sessionStorage = { marker: "session" };
    const localStorage = { marker: "local" };
    const deniedLocal = { sessionStorage };
    Object.defineProperty(deniedLocal, "localStorage", {
      get() {
        throw new DOMException("denied", "SecurityError");
      },
    });
    expect(acquireProductBriefBrowserStorages(deniedLocal)).toEqual({
      sessionStorage,
      localStorage: null,
    });

    const deniedSession = { localStorage };
    Object.defineProperty(deniedSession, "sessionStorage", {
      get() {
        throw new DOMException("denied", "SecurityError");
      },
    });
    expect(acquireProductBriefBrowserStorages(deniedSession)).toEqual({
      sessionStorage: null,
      localStorage,
    });
  });

  it("keeps the earliest active deadline for one durable identity", () => {
    expect(
      earliestActiveProductBriefRetentionDeadline(
        "2099-01-02T00:00:00.000Z",
        "2099-01-01T00:00:00.000Z",
      ),
    ).toBe("2099-01-01T00:00:00.000Z");
  });

  it("rejects reanalysis when 202 reuses the prior operation identity", () => {
    const pending = {
      payload: {
        workflow_id: "workflow-a",
        product_id: "product-a",
        expected_workflow_version: 3,
        asset_version_ids: ["asset-version-a"],
      },
      idempotencyKey: "web-product-brief-analysis-fixed",
      priorProductBrief: {
        productBriefId: "brief-a",
        operationId: "operation-a",
      },
    };
    const accepted = {
      operation_id: "operation-a",
      product_brief: {
        id: "brief-a",
        workspace_id: "catalog-demo",
        workflow_id: "workflow-a",
        product_id: "product-a",
        operation_id: "operation-a",
      },
    };

    expect(
      productBriefAnalysisAcceptedMatchesPending(
        accepted,
        pending,
        "catalog-demo",
      ),
    ).toBe(false);
  });

  it("rejects a stale ProductBrief reload after authoritative 202", () => {
    const accepted = {
      operation_id: "operation-new",
      product_brief: {
        id: "brief-a",
        workspace_id: "catalog-demo",
        workflow_id: "workflow-a",
        product_id: "product-a",
        operation_id: "operation-new",
        version: 5,
      },
    };
    const staleReload = {
      ...accepted.product_brief,
      operation_id: "operation-prior",
      version: 4,
    };

    expect(
      productBriefReloadMatchesAcceptedAnalysis(
        staleReload,
        accepted,
      ),
    ).toBe(false);
  });

  it.each([
    ["workspace", { workspace_id: "catalog-other" }],
    ["product", { product_id: "product-other" }],
    ["ProductBrief", { id: "brief-other" }],
    ["Workflow", { workflow_id: "workflow-other" }],
    ["Operation", { operation_id: "operation-other" }],
  ])("rejects a %s identity mismatch before publishing a core response", (
    _description,
    patch,
  ) => {
    const response = {
      id: "brief-a",
      workspace_id: "catalog-demo",
      workflow_id: "workflow-a",
      product_id: "product-a",
      operation_id: "operation-a",
      ...patch,
    };

    expect(
      productBriefResponseMatchesIdentity(response, {
        workspaceId: "catalog-demo",
        productId: "product-a",
        productBriefId: "brief-a",
        workflowId: "workflow-a",
        operationId: "operation-a",
      }),
    ).toBe(false);
  });

  it("accepts a core response only when every requested identity matches", () => {
    expect(
      productBriefResponseMatchesIdentity(
        {
          id: "brief-a",
          workspace_id: "catalog-demo",
          workflow_id: "workflow-a",
          product_id: "product-a",
          operation_id: "operation-a",
        },
        {
          workspaceId: "catalog-demo",
          productId: "product-a",
          productBriefId: "brief-a",
          workflowId: "workflow-a",
          operationId: "operation-a",
        },
      ),
    ).toBe(true);
  });

  it("allows a revision to rotate Operation identity only when reopening the exact confirmed base", () => {
    const response = {
      id: "brief-a",
      workspace_id: "catalog-demo",
      workflow_id: "workflow-a",
      product_id: "product-a",
      operation_id: "operation-reopened",
      state: "AWAITING_CONFIRMATION",
      current_version_id: "version-new",
      confirmed_version_id: "version-base",
      version: 8,
    };
    const expected = {
      workspaceId: "catalog-demo",
      productId: "product-a",
      productBriefId: "brief-a",
      workflowId: "workflow-a",
      priorOperationId: "operation-a",
      expectedProductBriefVersion: 7,
      baseVersionId: "version-base",
    };

    expect(
      productBriefRevisionResponseMatchesIdentity(response, expected),
    ).toBe(true);
    expect(
      productBriefRevisionResponseMatchesIdentity(
        { ...response, confirmed_version_id: null },
        expected,
      ),
    ).toBe(false);
    expect(
      productBriefRevisionResponseMatchesIdentity(
        { ...response, version: 9 },
        expected,
      ),
    ).toBe(false);
    expect(
      productBriefRevisionResponseMatchesIdentity(
        { ...response, current_version_id: "version-base" },
        expected,
      ),
    ).toBe(false);
  });

  it("keeps the existing Operation identity for an awaiting-confirmation revision", () => {
    expect(
      productBriefRevisionResponseMatchesIdentity(
        {
          id: "brief-a",
          workspace_id: "catalog-demo",
          workflow_id: "workflow-a",
          product_id: "product-a",
          operation_id: "operation-a",
          state: "AWAITING_CONFIRMATION",
          current_version_id: "version-new",
          confirmed_version_id: null,
          version: 8,
        },
        {
          workspaceId: "catalog-demo",
          productId: "product-a",
          productBriefId: "brief-a",
          workflowId: "workflow-a",
          priorOperationId: "operation-a",
          expectedProductBriefVersion: 7,
          baseVersionId: "version-base",
        },
      ),
    ).toBe(true);
  });

  it("binds Workflow context to both the requested and existing Workflow identities", () => {
    expect(
      productBriefWorkflowContextMatchesIdentity(
        { id: "workflow-a" },
        {
          requestedWorkflowId: "workflow-a",
          boundWorkflowId: "workflow-a",
        },
      ),
    ).toBe(true);
    expect(
      productBriefWorkflowContextMatchesIdentity(
        { id: "workflow-other" },
        { requestedWorkflowId: "workflow-a" },
      ),
    ).toBe(false);
    expect(
      productBriefWorkflowContextMatchesIdentity(
        { id: "workflow-a" },
        {
          requestedWorkflowId: "workflow-a",
          boundWorkflowId: "workflow-other",
        },
      ),
    ).toBe(false);
  });

  it("publishes ProductBrief reads monotonically through the controller seam", () => {
    const controller = createProductBriefWorkbenchController({
      productId: "product-a",
      workspaceId: "catalog-demo",
    });
    const newer = {
      id: "brief-a",
      product_id: "product-a",
      operation_id: "operation-a",
      version: 8,
    };
    const older = { ...newer, version: 7 };

    const firstRead = controller.beginBriefRead();
    expect(controller.publishBrief(firstRead, newer)).toBe(true);
    const lateRead = controller.beginBriefRead();
    expect(controller.publishBrief(lateRead, older)).toBe(false);
    expect(controller.getSnapshot().brief).toBe(newer);
  });

  it("moves one exact durable revision through pending, conflict, and settlement", () => {
    const controller = createProductBriefWorkbenchController({
      productId: "product-a",
      workspaceId: "catalog-demo",
    });
    const command = {
      schemaVersion: 1,
      kind: "revise",
      productId: "product-a",
      productBriefId: "brief-a",
      payload: {
        expected_product_brief_version: 7,
        base_version_id: "version-a",
        reason: "Verified against packaging",
        fields: [
          {
            path: "common.brand",
            value: { kind: "TEXT", text: "Northstar" },
            sensitive: false,
            evidence: [revisionEvidence()],
          },
        ],
      },
      idempotencyKey: "web-product-brief-revise-fixed",
    };

    expect(controller.recoverCommand(command, "pending")).toBe(true);
    expect(controller.markVersionConflict(command)).toBe(true);
    expect(controller.getSnapshot()).toMatchObject({
      pendingCommand: command,
      commandStatus: "version-conflict",
    });
    expect(
      controller.settleCommand({
        ...command,
        idempotencyKey: "web-product-brief-revise-unrelated",
      }),
    ).toBe(false);
    expect(controller.settleCommand(command)).toBe(true);
    expect(controller.getSnapshot()).toMatchObject({
      pendingCommand: null,
      commandStatus: null,
    });
  });

  it("allows command dispatch only while persistence and memory retain the exact pending identity", () => {
    const command = {
      schemaVersion: 1,
      kind: "confirm",
      productId: "product-a",
      productBriefId: "brief-a",
      payload: {
        expected_product_brief_version: 7,
        product_brief_version_id: "version-a",
        expected_workflow_version: 3,
        reason_code: "HUMAN_VERIFIED",
        comment_ref: null,
      },
      idempotencyKey: "web-product-brief-confirm-fixed",
    };
    const persisted = {
      schemaVersion: 3,
      workspaceId: "catalog-demo",
      productId: "product-a",
      productBriefId: "brief-a",
      operationId: "operation-a",
      workflowId: "workflow-a",
      assetVersionIds: ["asset-version-a"],
      retentionDeadline: "2099-01-01T00:00:00Z",
      pendingCommand: command,
      commandStatus: "pending",
    };
    const controller = createProductBriefWorkbenchController({
      productId: "product-a",
      workspaceId: "catalog-demo",
    });
    expect(controller.recoverCommand(command, "pending")).toBe(true);

    expect(
      exactPendingProductBriefCommandIsDurable(
        controller.getSnapshot(),
        persisted,
        command,
        "pending",
      ),
    ).toBe(true);
    expect(
      exactPendingProductBriefCommandIsDurable(
        controller.getSnapshot(),
        null,
        command,
        "pending",
      ),
    ).toBe(false);
    expect(
      exactPendingProductBriefCommandIsDurable(
        controller.getSnapshot(),
        { ...persisted, commandStatus: "version-conflict" },
        command,
        "pending",
      ),
    ).toBe(false);

    const mismatchedMemory = createProductBriefWorkbenchController({
      productId: "product-a",
      workspaceId: "catalog-demo",
    });
    expect(
      exactPendingProductBriefCommandIsDurable(
        mismatchedMemory.getSnapshot(),
        persisted,
        command,
        "pending",
      ),
    ).toBe(false);
  });

  it("appends every immutable version across cursor pages without duplicates", () => {
    const controller = createProductBriefWorkbenchController({
      productId: "product-a",
      workspaceId: "catalog-demo",
    });
    const currentBrief = {
      id: "brief-a",
      product_id: "product-a",
      operation_id: "operation-a",
      version: 25,
    };
    expect(
      controller.publishBrief(controller.beginBriefRead(), currentBrief),
    ).toBe(true);
    const allVersions = Array.from({ length: 25 }, (_, index) => ({
      id: `version-${25 - index}`,
      product_brief_id: "brief-a",
      version_number: 25 - index,
    }));

    const firstPage = controller.beginHistoryRead("initial", "brief-a");
    expect(
      controller.publishHistory(firstPage, {
        items: allVersions.slice(0, 20),
        next_cursor: 5,
      }),
    ).toBe(true);
    const nextPage = controller.beginHistoryRead("more", "brief-a");
    expect(
      controller.publishHistory(nextPage, {
        items: [
          allVersions[19],
          allVersions[20],
          allVersions[20],
          ...allVersions.slice(21),
        ],
        next_cursor: null,
      }),
    ).toBe(true);

    expect(controller.getSnapshot().versions).toHaveLength(25);
    expect(
      new Set(controller.getSnapshot().versions.map((item) => item.id)).size,
    ).toBe(25);
    expect(controller.getSnapshot().versionsNextCursor).toBeNull();
  });

  it("rejects a late history page after the product identity changes", () => {
    const controller = createProductBriefWorkbenchController({
      productId: "product-a",
      workspaceId: "catalog-demo",
    });
    expect(
      controller.publishBrief(controller.beginBriefRead(), {
        id: "brief-a",
        product_id: "product-a",
        operation_id: "operation-a",
        version: 1,
      }),
    ).toBe(true);
    const stalePage = controller.beginHistoryRead("more", "brief-a");

    controller.changeIdentity({
      productId: "product-b",
      workspaceId: "catalog-demo",
    });

    expect(
      controller.publishHistory(stalePage, {
        items: [
          {
            id: "version-a",
            product_brief_id: "brief-a",
            version_number: 1,
          },
        ],
        next_cursor: null,
      }),
    ).toBe(false);
    expect(controller.getSnapshot()).toMatchObject({
      identity: {
        productId: "product-b",
        workspaceId: "catalog-demo",
      },
      brief: null,
      versions: [],
    });
  });

  it.each([
    {
      description: "accepts the current ProductBrief target",
      requestIdentity: {
        workspaceId: "catalog-demo",
        productId: "product-a",
      },
      requestGeneration: 7,
      currentGeneration: 7,
      requestAborted: false,
      target: { productBriefId: "brief-a" },
      expected: true,
    },
    {
      description: "accepts the current Workflow target",
      requestIdentity: {
        workspaceId: "catalog-demo",
        productId: "product-a",
      },
      requestGeneration: 7,
      currentGeneration: 7,
      requestAborted: false,
      target: { workflowId: "workflow-a" },
      expected: true,
    },
    {
      description: "accepts the exact pending analysis target",
      requestIdentity: {
        workspaceId: "catalog-demo",
        productId: "product-a",
      },
      requestGeneration: 7,
      currentGeneration: 7,
      requestAborted: false,
      targetKind: "pending",
      expected: true,
    },
    {
      description: "rejects a request from a superseded workspace",
      requestIdentity: {
        workspaceId: "catalog-previous",
        productId: "product-a",
      },
      requestGeneration: 7,
      currentGeneration: 7,
      requestAborted: false,
      target: { productBriefId: "brief-a" },
      expected: false,
    },
    {
      description: "rejects a request from a superseded product",
      requestIdentity: {
        workspaceId: "catalog-demo",
        productId: "product-previous",
      },
      requestGeneration: 7,
      currentGeneration: 7,
      requestAborted: false,
      target: { productBriefId: "brief-a" },
      expected: false,
    },
    {
      description: "rejects a superseded request generation",
      requestIdentity: {
        workspaceId: "catalog-demo",
        productId: "product-a",
      },
      requestGeneration: 6,
      currentGeneration: 7,
      requestAborted: false,
      target: { productBriefId: "brief-a" },
      expected: false,
    },
    {
      description: "rejects an aborted request",
      requestIdentity: {
        workspaceId: "catalog-demo",
        productId: "product-a",
      },
      requestGeneration: 7,
      currentGeneration: 7,
      requestAborted: true,
      target: { productBriefId: "brief-a" },
      expected: false,
    },
    {
      description: "rejects a superseded ProductBrief target",
      requestIdentity: {
        workspaceId: "catalog-demo",
        productId: "product-a",
      },
      requestGeneration: 7,
      currentGeneration: 7,
      requestAborted: false,
      target: { productBriefId: "brief-previous" },
      expected: false,
    },
    {
      description: "rejects a superseded Workflow target",
      requestIdentity: {
        workspaceId: "catalog-demo",
        productId: "product-a",
      },
      requestGeneration: 7,
      currentGeneration: 7,
      requestAborted: false,
      target: { workflowId: "workflow-previous" },
      expected: false,
    },
    {
      description: "rejects a superseded pending analysis target",
      requestIdentity: {
        workspaceId: "catalog-demo",
        productId: "product-a",
      },
      requestGeneration: 7,
      currentGeneration: 7,
      requestAborted: false,
      targetKind: "stale-pending",
      expected: false,
    },
  ])(
    "$description before applying an authoritative 410",
    ({
      requestIdentity,
      requestGeneration,
      currentGeneration,
      requestAborted,
      target,
      targetKind,
      expected,
    }) => {
      const pendingAnalysis = {
        payload: {
          workflow_id: "workflow-a",
          product_id: "product-a",
          expected_workflow_version: 3,
          asset_version_ids: ["asset-version-a"],
        },
        idempotencyKey: "web-product-brief-analysis-fixed",
        priorProductBrief: null,
      };
      const controller = createProductBriefWorkbenchController({
        productId: "product-a",
        workspaceId: "catalog-demo",
      });
      expect(
        controller.publishBrief(controller.beginBriefRead(), {
          id: "brief-a",
          product_id: "product-a",
          workflow_id: "workflow-a",
          operation_id: "operation-a",
          version: 1,
        }),
      ).toBe(true);
      controller.recoverAnalysis(pendingAnalysis);
      const pendingTarget =
        targetKind === "stale-pending"
          ? {
              ...pendingAnalysis,
              idempotencyKey:
                "web-product-brief-analysis-superseded",
            }
          : pendingAnalysis;

      expect(
        authoritativeGoneAppliesToCurrentWorkbench({
          current: controller.getSnapshot(),
          persisted: {
            schemaVersion: 2,
            workspaceId: "catalog-demo",
            productId: "product-a",
            workflowId: "workflow-a",
            assetVersionIds: ["asset-version-a"],
            retentionDeadline: "2099-01-01T00:00:00Z",
            pendingAnalysis,
          },
          target:
            targetKind === "pending" ||
            targetKind === "stale-pending"
              ? { pendingAnalysis: pendingTarget }
              : target,
          requestIdentity,
          requestGeneration,
          currentGeneration,
          requestAborted,
        }),
      ).toBe(expected);
    },
  );

  it("keeps one bounded polling lifecycle across alternating operation states", () => {
    const controller = createProductBriefWorkbenchController({
      productId: "product-a",
      workspaceId: "catalog-demo",
    });
    expect(
      controller.publishBrief(controller.beginBriefRead(), {
        id: "brief-a",
        product_id: "product-a",
        operation_id: "operation-a",
        version: 1,
      }),
    ).toBe(true);

    for (let request = 1; request <= 24; request += 1) {
      const read = controller.beginOperationRead("operation-a");
      expect(
        controller.completeOperationPoll(
          read,
          {
            id: "operation-a",
            version: request,
            state: request % 2 === 0 ? "RECONCILING" : "RUNNING",
            attempt_count: 1,
            max_attempts: 5,
            error: null,
          },
          () => 1,
        ),
      ).toBe(true);
    }

    expect(controller.getSnapshot().polling).toMatchObject({
      operationId: "operation-a",
      completedRequests: 24,
      nextDelayMs: null,
      paused: true,
    });
    const staleRead = controller.beginOperationRead("operation-a");
    expect(
      controller.publishOperation(staleRead, {
        id: "operation-a",
        version: 23,
        state: "RUNNING",
        attempt_count: 1,
        max_attempts: 5,
        error: null,
      }),
    ).toBe(false);
    expect(controller.getSnapshot().operation.version).toBe(24);
  });

  it("re-arms polling when an auxiliary read supersedes an in-flight poll", () => {
    const controller = createProductBriefWorkbenchController({
      productId: "product-a",
      workspaceId: "catalog-demo",
    });
    expect(
      controller.publishBrief(controller.beginBriefRead(), {
        id: "brief-a",
        product_id: "product-a",
        operation_id: "operation-a",
        version: 1,
      }),
    ).toBe(true);
    const inFlightPoll = controller.beginOperationRead("operation-a");
    const auxiliaryRead = controller.beginOperationRead("operation-a");
    const previousRequestToken =
      controller.getSnapshot().polling.requestToken;

    expect(
      controller.completeAuxiliaryOperationRead(
        auxiliaryRead,
        {
          id: "operation-a",
          version: 2,
          state: "RUNNING",
          attempt_count: 1,
          max_attempts: 5,
          error: null,
        },
        () => 0,
      ),
    ).toBe(true);
    expect(controller.getSnapshot().polling).toMatchObject({
      operationId: "operation-a",
      completedRequests: 0,
      paused: false,
    });
    expect(controller.getSnapshot().polling.nextDelayMs).not.toBeNull();
    expect(controller.getSnapshot().polling.requestToken).toBeGreaterThan(
      previousRequestToken,
    );
    expect(
      controller.completeOperationPoll(inFlightPoll, {
        id: "operation-a",
        version: 1,
        state: "RUNNING",
        attempt_count: 1,
        max_attempts: 5,
        error: null,
      }),
    ).toBe(false);
  });

  it("rejects a lower version of the currently displayed ProductBrief", () => {
    expect(
      isMonotonicProductBriefVersion(
        { id: "brief-1", version: 8 },
        { id: "brief-1", version: 7 },
      ),
    ).toBe(false);
    expect(
      isMonotonicProductBriefVersion(
        { id: "brief-1", version: 8 },
        { id: "brief-1", version: 8 },
      ),
    ).toBe(true);
    expect(
      isMonotonicProductBriefVersion(
        { id: "brief-1", version: 8 },
        { id: "brief-1", version: 9 },
      ),
    ).toBe(true);
  });

  it("validates the versioned value schema selected by each field path", () => {
    expect(
      structuredValueError(
        "common.brand",
        '{"kind":"TEXT","text":"Northstar"}',
      ),
    ).toBeNull();
    expect(
      structuredValueError(
        "common.colors",
        '{"kind":"TEXT_LIST","items":["blue","white"]}',
      ),
    ).toBeNull();
    expect(structuredValueError("common.colors", '["blue",')).toBe(
      "请输入有效的 JSON。",
    );
    expect(
      structuredValueError(
        "common.brand",
        '{"kind":"TEXT_LIST","items":["Northstar"]}',
      ),
    ).toBe("字段值不符合当前 ProductBrief 字段契约。");
    expect(
      structuredValueError(
        "common.brand",
        '{"kind":"TEXT","text":"Northstar","claim":true}',
      ),
    ).toBe("字段值不符合当前 ProductBrief 字段契约。");
    expect(
      structuredValueError(
        "automotive.dimensions_evidence",
        '{"kind":"DIMENSION_LIST","dimensions":[{"name":"length","value":true}]}',
      ),
    ).toBe("字段值不符合当前 ProductBrief 字段契约。");
  });

  it("returns a source only for the product that produced it", () => {
    const source = {
      productId: "product-a",
      workflowId: "workflow-a",
      assetVersionId: "asset-version-a",
    };

    expect(productBriefSourceFor("product-a", source)).toEqual(source);
    expect(productBriefSourceFor("product-b", source)).toBeNull();
    expect(productBriefSourceFor("product-a", null)).toBeNull();
  });

  it("restores the exact draft evidence when reanalysis changed the current evidence", () => {
    const staleEvidence = [revisionEvidence("b".repeat(64))];
    const currentEvidence = [revisionEvidence("c".repeat(64))];
    const staleBrand = {
      valueText: "stale local brand",
      evidence: staleEvidence,
    };
    const currentMaterial = {
      valueText: "current material",
      evidence: currentEvidence,
    };

    const restored = restoredProductBriefDrafts(
      {
        "common.brand": {
          valueText: "reanalyzed brand",
          evidence: currentEvidence,
        },
        "common.material": currentMaterial,
      },
      {
        "common.brand": staleBrand,
        "common.removed": {
          valueText: "removed field",
          evidence: staleEvidence,
        },
      },
    );

    expect(restored).toEqual({
      "common.brand": staleBrand,
      "common.material": currentMaterial,
    });
    expect(restored["common.brand"].evidence).toEqual(staleEvidence);
    expect(restored["common.brand"].evidence).not.toEqual(currentEvidence);
  });

  it("replays the exact revision command after a committed response is lost", () => {
    const command = {
      schemaVersion: 1,
      kind: "revise",
      productId: "product-a",
      productBriefId: "brief-a",
      payload: {
        expected_product_brief_version: 7,
        base_version_id: "version-a",
        reason: "Verified against packaging",
        fields: [
          {
            path: "common.brand",
            value: {
              kind: "TEXT",
              text: "Northstar Verified",
            },
            confidence: 1,
            conflict: "NONE",
            review_required: false,
            sensitive: false,
            evidence: [revisionEvidence()],
          },
        ],
      },
      idempotencyKey: "web-product-brief-revise-fixed",
    };

    const restored = pendingProductBriefCommandFor(
      "product-a",
      "brief-a",
      JSON.parse(JSON.stringify(command)),
    );

    expect(restored).toEqual(command);
    expect(
      pendingProductBriefCommandFor("product-a", "brief-b", command),
    ).toBeNull();
    expect(
      pendingProductBriefCommandFor("product-a", "brief-a", {
        ...command,
        payload: {
          ...command.payload,
          fields: [
            {
              ...command.payload.fields[0],
              value: {
                kind: "TEXT_LIST",
                items: ["Northstar Verified"],
              },
            },
          ],
        },
      }),
    ).toBeNull();
  });

  it.each([
    ["without evidence", []],
    [
      "with more than 32 evidence records",
      Array.from({ length: 33 }, revisionEvidence),
    ],
  ])("rejects a persisted revision field %s", (_description, evidence) => {
    const command = {
      schemaVersion: 1,
      kind: "revise",
      productId: "product-a",
      productBriefId: "brief-a",
      payload: {
        expected_product_brief_version: 7,
        base_version_id: "version-a",
        reason: "Verified against packaging",
        fields: [
          {
            path: "common.brand",
            value: { kind: "TEXT", text: "Northstar Verified" },
            sensitive: false,
            evidence,
          },
        ],
      },
      idempotencyKey: "web-product-brief-revise-fixed",
    };

    expect(
      pendingProductBriefCommandFor("product-a", "brief-a", command),
    ).toBeNull();
  });

  it("replays the exact confirmation command after a committed response is lost", () => {
    const command = {
      schemaVersion: 1,
      kind: "confirm",
      productId: "product-a",
      productBriefId: "brief-a",
      payload: {
        expected_product_brief_version: 8,
        product_brief_version_id: "version-b",
        expected_workflow_version: 12,
        reason_code: "HUMAN_VERIFIED",
        comment_ref: "comment://verification",
      },
      idempotencyKey: "web-product-brief-confirm-fixed",
    };

    const restored = pendingProductBriefCommandFor(
      "product-a",
      "brief-a",
      JSON.parse(JSON.stringify(command)),
    );

    expect(restored).toEqual(command);
    expect(
      pendingProductBriefCommandFor("product-b", "brief-a", command),
    ).toBeNull();
  });

  it("settles only the matching command and preserves stale unrelated state", () => {
    const dispatched = {
      schemaVersion: 1,
      kind: "revise",
      productId: "product-a",
      productBriefId: "brief-a",
      payload: {
        expected_product_brief_version: 7,
        base_version_id: "version-a",
        reason: "Verified against packaging",
        fields: [
          {
            path: "common.brand",
            value: { kind: "TEXT", text: "Northstar" },
            sensitive: false,
            evidence: [revisionEvidence()],
          },
        ],
      },
      idempotencyKey: "web-product-brief-revise-fixed",
    };

    expect(
      productBriefCommandsMatch(
        dispatched,
        JSON.parse(JSON.stringify(dispatched)),
      ),
    ).toBe(true);
    expect(
      productBriefCommandsMatch(dispatched, {
        ...dispatched,
        productBriefId: "stale-brief",
      }),
    ).toBe(false);
    expect(
      productBriefCommandsMatch(dispatched, {
        ...dispatched,
        idempotencyKey: "web-product-brief-revise-newer",
      }),
    ).toBe(false);
  });

  it("rejects stale commands whose kind and payload schema do not match", () => {
    const stale = {
      schemaVersion: 1,
      kind: "confirm",
      productId: "product-a",
      productBriefId: "brief-a",
      payload: {
        expected_product_brief_version: 7,
        base_version_id: "version-a",
        reason: "This is a revision payload",
        fields: [],
      },
      idempotencyKey: "web-product-brief-confirm-stale",
    };

    expect(
      pendingProductBriefCommandFor("product-a", "brief-a", stale),
    ).toBeNull();
    expect(
      pendingProductBriefCommandFor("product-a", "brief-a", {
        ...stale,
        schemaVersion: 2,
      }),
    ).toBeNull();
  });
});
