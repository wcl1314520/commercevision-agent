import { describe, expect, it } from "vitest";

import {
  activateProductBriefPersistenceIdentity,
  clearProductBriefPersistenceNamespace,
  createProductBriefRetentionController,
  defaultProductBriefStorage,
  productBriefPersistenceKey,
  readPersistedProductBrief,
  sweepExpiredPersistedProductBriefs,
  writePersistedProductBrief,
} from "../lib/product-brief-persistence";

const PRODUCT_ID = "product-a";
const WORKSPACE_ID = "catalog-demo";
const ASSET_VERSION_ID = "asset-version-a";
const DEADLINE = "2026-07-31T08:00:00.000Z";
const BEFORE_DEADLINE = Date.parse("2026-07-31T07:59:59.999Z");
const AT_DEADLINE = Date.parse(DEADLINE);
const EVIDENCE_TOKEN = "a".repeat(64);

function identityFor(
  productId = PRODUCT_ID,
  workspaceId = WORKSPACE_ID,
) {
  return { workspaceId, productId };
}

function memoryStorage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return {
    get length() {
      return values.size;
    },
    key(index) {
      return [...values.keys()][index] ?? null;
    },
    getItem(key) {
      return values.get(key) ?? null;
    },
    setItem(key, value) {
      values.set(key, value);
    },
    removeItem(key) {
      values.delete(key);
    },
    value(key) {
      return values.get(key) ?? null;
    },
  };
}

function established(overrides = {}) {
  return {
    schemaVersion: 1,
    workspaceId: WORKSPACE_ID,
    productId: PRODUCT_ID,
    productBriefId: "brief-a",
    operationId: "operation-a",
    workflowId: "workflow-a",
    assetVersionIds: [ASSET_VERSION_ID],
    retentionDeadline: DEADLINE,
    ...overrides,
  };
}

function revisionCommand(overrides = {}) {
  return {
    schemaVersion: 1,
    kind: "revise",
    productId: PRODUCT_ID,
    productBriefId: "brief-a",
    idempotencyKey: "fixed-revision-key",
    payload: {
      expected_product_brief_version: 3,
      base_version_id: "version-a",
      reason: "Verified",
      fields: [
        {
          path: "common.brand",
          value: { kind: "TEXT", text: "Northstar" },
          confidence: "1.0000",
          conflict: "NONE",
          review_required: false,
          sensitive: false,
          evidence: [
            {
              source_asset_version_id: ASSET_VERSION_ID,
              kind: "IMAGE_REGION",
              reference: `asset-region://${EVIDENCE_TOKEN}`,
              region: [0.1, 0.2, 0.8, 0.9],
              excerpt_sha256: EVIDENCE_TOKEN,
            },
          ],
        },
      ],
    },
    ...overrides,
  };
}

function pendingRevision(overrides = {}) {
  return {
    ...established(),
    schemaVersion: 3,
    commandStatus: "pending",
    pendingCommand: revisionCommand(),
    ...overrides,
  };
}

describe("ProductBrief browser persistence", () => {
  it("binds both the storage key and payload to the workspace identity", () => {
    const identity = {
      workspaceId: WORKSPACE_ID,
      productId: PRODUCT_ID,
    };
    const storage = memoryStorage();
    const value = established({ workspaceId: WORKSPACE_ID });

    expect(productBriefPersistenceKey(identity)).toBe(
      "commercevision.product-brief.v2:catalog-demo:product-a",
    );
    expect(
      writePersistedProductBrief(storage, value, BEFORE_DEADLINE),
    ).toBe(true);
    expect(
      readPersistedProductBrief(storage, identity, BEFORE_DEADLINE),
    ).toEqual(value);
    const foreignIdentity = {
      workspaceId: "catalog-secondary",
      productId: PRODUCT_ID,
    };
    storage.setItem(
      productBriefPersistenceKey(foreignIdentity),
      JSON.stringify(value),
    );
    expect(
      readPersistedProductBrief(
        storage,
        foreignIdentity,
        BEFORE_DEADLINE,
      ),
    ).toBeNull();
    expect(
      storage.value(productBriefPersistenceKey(foreignIdentity)),
    ).toBeNull();
    expect(
      readPersistedProductBrief(storage, identity, BEFORE_DEADLINE),
    ).toEqual(value);
  });

  it("deletes a record whose workspace binding is damaged", () => {
    const identity = identityFor();
    const key = productBriefPersistenceKey(identity);
    const storage = memoryStorage({
      [key]: JSON.stringify(
        established({ workspaceId: { unexpected: true } }),
      ),
    });

    expect(
      readPersistedProductBrief(storage, identity, BEFORE_DEADLINE),
    ).toBeNull();
    expect(storage.value(key)).toBeNull();
  });

  it("does not activate or read an invalid current workspace identity", () => {
    const storage = memoryStorage();
    const invalidIdentity = {
      workspaceId: "",
      productId: PRODUCT_ID,
    };

    activateProductBriefPersistenceIdentity(storage, invalidIdentity);

    expect(
      storage.value("commercevision.product-brief.active.v2"),
    ).toBeNull();
    expect(
      readPersistedProductBrief(
        storage,
        invalidIdentity,
        BEFORE_DEADLINE,
      ),
    ).toBeNull();
  });

  it.each([
    ["missing", null],
    ["corrupt", "not-json"],
  ])(
    "fails closed when the active marker is %s",
    (_description, activeMarker) => {
      const currentIdentity = identityFor();
      const foreignIdentity = identityFor(
        PRODUCT_ID,
        "catalog-secondary",
      );
      const current = established();
      const foreign = established({
        workspaceId: foreignIdentity.workspaceId,
      });
      const legacy = { ...current };
      delete legacy.workspaceId;
      const legacyKey = `commercevision.product-brief.v1:${PRODUCT_ID}`;
      const storage = memoryStorage({
        [productBriefPersistenceKey(currentIdentity)]:
          JSON.stringify(current),
        [productBriefPersistenceKey(foreignIdentity)]:
          JSON.stringify(foreign),
        [legacyKey]: JSON.stringify(legacy),
        "unrelated:key": "keep",
      });
      if (activeMarker !== null) {
        storage.setItem(
          "commercevision.product-brief.active.v2",
          activeMarker,
        );
      }

      expect(
        activateProductBriefPersistenceIdentity(
          storage,
          currentIdentity,
        ),
      ).toBe(true);

      expect(
        readPersistedProductBrief(
          storage,
          currentIdentity,
          BEFORE_DEADLINE,
        ),
      ).toBeNull();
      expect(storage.value(legacyKey)).toBeNull();
      expect(
        storage.value(productBriefPersistenceKey(foreignIdentity)),
      ).toBeNull();
      expect(storage.value("unrelated:key")).toBe("keep");
    },
  );

  it("fails closed when the active marker cannot be written", () => {
    const identity = identityFor();
    const key = productBriefPersistenceKey(identity);
    const storage = memoryStorage({
      [key]: JSON.stringify(established()),
      "unrelated:key": "keep",
    });
    const setItem = storage.setItem;
    storage.setItem = (storageKey, value) => {
      if (storageKey === "commercevision.product-brief.active.v2") {
        throw new Error("marker write denied");
      }
      setItem(storageKey, value);
    };

    expect(
      activateProductBriefPersistenceIdentity(storage, identity),
    ).toBe(false);
    expect(storage.value(key)).toBeNull();
    expect(storage.value("unrelated:key")).toBe("keep");
  });

  it("fails closed and removes legacy records without a workspace binding", () => {
    const legacyKey = `commercevision.product-brief.v1:${PRODUCT_ID}`;
    const storage = memoryStorage({
      [legacyKey]: JSON.stringify(established()),
    });

    expect(
      readPersistedProductBrief(
        storage,
        { workspaceId: WORKSPACE_ID, productId: PRODUCT_ID },
        BEFORE_DEADLINE,
      ),
    ).toBeNull();
    expect(storage.value(legacyKey)).toBeNull();
  });

  it("uses session-scoped storage by default", () => {
    const sessionStorage = memoryStorage();
    const localStorage = memoryStorage();

    expect(
      defaultProductBriefStorage({ sessionStorage, localStorage }),
    ).toBe(sessionStorage);
  });

  it("purges legacy persistent ProductBrief payloads without touching other keys", () => {
    const storage = memoryStorage({
      [productBriefPersistenceKey(identityFor())]: JSON.stringify(
        pendingRevision(),
      ),
      [productBriefPersistenceKey(identityFor("product-b"))]: JSON.stringify(
        established({ productId: "product-b" }),
      ),
      "unrelated:key": "keep",
    });

    expect(clearProductBriefPersistenceNamespace(storage)).toBe(2);
    expect(
      storage.value(productBriefPersistenceKey(identityFor())),
    ).toBeNull();
    expect(
      storage.value(productBriefPersistenceKey(identityFor("product-b"))),
    ).toBeNull();
    expect(storage.value("unrelated:key")).toBe("keep");
  });

  it("sweeps every expired ProductBrief record in the storage namespace", () => {
    const activeProductId = "product-active";
    const expiredProductId = "product-expired";
    const storage = memoryStorage({
      [productBriefPersistenceKey(identityFor(activeProductId))]:
        JSON.stringify(
        established({ productId: activeProductId }),
      ),
      [productBriefPersistenceKey(identityFor(expiredProductId))]:
        JSON.stringify(
        established({
          productId: expiredProductId,
          retentionDeadline: "2026-07-31T07:00:00.000Z",
        }),
      ),
      "unrelated:key": "keep",
    });

    expect(
      sweepExpiredPersistedProductBriefs(storage, BEFORE_DEADLINE),
    ).toBe(1);
    expect(
      storage.value(productBriefPersistenceKey(identityFor(activeProductId))),
    ).not.toBeNull();
    expect(
      storage.value(productBriefPersistenceKey(identityFor(expiredProductId))),
    ).toBeNull();
    expect(storage.value("unrelated:key")).toBe("keep");
  });

  it.each([
    ["initial analysis", null],
    [
      "reanalysis",
      {
        productBriefId: "brief-a",
        operationId: "operation-a",
      },
    ],
  ])(
    "preserves an active pending %s during a namespace sweep",
    (_description, priorProductBrief) => {
    const storage = memoryStorage();
    const pendingAnalysis = {
      schemaVersion: 2,
      workspaceId: WORKSPACE_ID,
      productId: PRODUCT_ID,
      workflowId: "workflow-a",
      assetVersionIds: [ASSET_VERSION_ID],
      retentionDeadline: DEADLINE,
      pendingAnalysis: {
        idempotencyKey:
          "web-product-brief-analyze-019f8a00-0000-7000-8000-000000000199",
        priorProductBrief,
        payload: {
          workflow_id: "workflow-a",
          product_id: PRODUCT_ID,
          asset_version_ids: [ASSET_VERSION_ID],
          expected_workflow_version: 3,
        },
      },
    };

    expect(
      writePersistedProductBrief(
        storage,
        pendingAnalysis,
        BEFORE_DEADLINE,
      ),
    ).toBe(true);
    expect(
      sweepExpiredPersistedProductBriefs(storage, BEFORE_DEADLINE),
    ).toBe(0);
    expect(
      readPersistedProductBrief(
        storage,
        identityFor(),
        BEFORE_DEADLINE,
      ),
    ).toEqual(pendingAnalysis);
    },
  );

  it("expires retained task payloads in an idle tab without another read", () => {
    let nowMs = BEFORE_DEADLINE;
    let scheduled = null;
    const expiredIdentities = [];
    const storage = memoryStorage();
    const value = pendingRevision();
    expect(writePersistedProductBrief(storage, value, nowMs)).toBe(true);
    const retention = createProductBriefRetentionController({
      storage,
      now: () => nowMs,
      schedule(callback, delayMs) {
        scheduled = { callback, delayMs };
        return 1;
      },
      cancel() {
        scheduled = null;
      },
      onExpired(identity) {
        expiredIdentities.push(identity);
      },
    });

    expect(
      retention.activate(
        { workspaceId: "catalog-demo", productId: PRODUCT_ID },
        DEADLINE,
      ),
    ).toBe(true);
    expect(scheduled?.delayMs).toBe(1);

    nowMs = AT_DEADLINE;
    scheduled.callback();

    expect(
      storage.value(productBriefPersistenceKey(identityFor())),
    ).toBeNull();
    expect(expiredIdentities).toEqual([
      { workspaceId: "catalog-demo", productId: PRODUCT_ID },
    ]);
  });

  it("synchronously invalidates an identity whose deadline elapsed before activation", () => {
    const storage = memoryStorage();
    const expiredIdentities = [];
    expect(
      writePersistedProductBrief(storage, pendingRevision(), BEFORE_DEADLINE),
    ).toBe(true);
    const retention = createProductBriefRetentionController({
      storage,
      now: () => AT_DEADLINE,
      schedule() {
        throw new Error("an expired identity must not arm a timer");
      },
      cancel() {},
      onExpired(identity) {
        expiredIdentities.push(identity);
      },
    });

    expect(
      retention.activate(
        { workspaceId: "catalog-demo", productId: PRODUCT_ID },
        DEADLINE,
      ),
    ).toBe(false);
    expect(
      storage.value(productBriefPersistenceKey(identityFor())),
    ).toBeNull();
    expect(expiredIdentities).toEqual([
      { workspaceId: "catalog-demo", productId: PRODUCT_ID },
    ]);
  });

  it("fails closed when an active deadline timer cannot be scheduled", () => {
    const storage = memoryStorage();
    const expiredIdentities = [];
    expect(
      writePersistedProductBrief(storage, pendingRevision(), BEFORE_DEADLINE),
    ).toBe(true);
    const retention = createProductBriefRetentionController({
      storage,
      now: () => BEFORE_DEADLINE,
      schedule() {
        throw new Error("timer service unavailable");
      },
      cancel() {},
      onExpired(identity) {
        expiredIdentities.push(identity);
      },
    });

    expect(
      retention.activate(
        { workspaceId: "catalog-demo", productId: PRODUCT_ID },
        DEADLINE,
      ),
    ).toBe(false);
    expect(
      storage.value(productBriefPersistenceKey(identityFor())),
    ).toBeNull();
    expect(expiredIdentities).toEqual([
      { workspaceId: "catalog-demo", productId: PRODUCT_ID },
    ]);
  });

  it("cleans the previous task record and timer when identity changes", () => {
    const productB = "product-b";
    const storage = memoryStorage();
    expect(
      writePersistedProductBrief(storage, pendingRevision(), BEFORE_DEADLINE),
    ).toBe(true);
    expect(
      writePersistedProductBrief(
        storage,
        established({ productId: productB }),
        BEFORE_DEADLINE,
      ),
    ).toBe(true);
    let cancelledTimers = 0;
    const retention = createProductBriefRetentionController({
      storage,
      now: () => BEFORE_DEADLINE,
      schedule: () => 1,
      cancel: () => {
        cancelledTimers += 1;
      },
      onExpired() {},
    });
    expect(
      retention.activate(
        { workspaceId: "catalog-demo", productId: PRODUCT_ID },
        DEADLINE,
      ),
    ).toBe(true);

    expect(
      retention.activate(
        { workspaceId: "catalog-demo", productId: productB },
        DEADLINE,
      ),
    ).toBe(true);

    expect(
      storage.value(productBriefPersistenceKey(identityFor())),
    ).toBeNull();
    expect(
      storage.value(productBriefPersistenceKey(identityFor(productB))),
    ).not.toBeNull();
    expect(cancelledTimers).toBe(1);
  });

  it("cleans the previous task record when a new controller mount changes identity", () => {
    const productB = "product-b";
    const storage = memoryStorage();
    activateProductBriefPersistenceIdentity(storage, {
      workspaceId: "catalog-demo",
      productId: PRODUCT_ID,
    });
    expect(
      writePersistedProductBrief(storage, pendingRevision(), BEFORE_DEADLINE),
    ).toBe(true);
    expect(
      writePersistedProductBrief(
        storage,
        established({ productId: productB }),
        BEFORE_DEADLINE,
      ),
    ).toBe(true);

    activateProductBriefPersistenceIdentity(storage, {
      workspaceId: "catalog-demo",
      productId: productB,
    });

    expect(
      storage.value(productBriefPersistenceKey(identityFor())),
    ).toBeNull();
    expect(
      storage.value(productBriefPersistenceKey(identityFor(productB))),
    ).toBeNull();
  });

  it("cleans the previous task record when the workspace identity changes", () => {
    const storage = memoryStorage();
    expect(
      writePersistedProductBrief(storage, pendingRevision(), BEFORE_DEADLINE),
    ).toBe(true);
    activateProductBriefPersistenceIdentity(storage, {
      workspaceId: "catalog-demo",
      productId: PRODUCT_ID,
    });

    activateProductBriefPersistenceIdentity(storage, {
      workspaceId: "catalog-secondary",
      productId: PRODUCT_ID,
    });

    expect(
      storage.value(productBriefPersistenceKey(identityFor())),
    ).toBeNull();
  });

  it("keeps an identity only before the exact server deadline", () => {
    const storage = memoryStorage();
    const value = established();

    expect(
      writePersistedProductBrief(storage, value, BEFORE_DEADLINE),
    ).toBe(true);
    expect(
      readPersistedProductBrief(storage, identityFor(), BEFORE_DEADLINE),
    ).toEqual(value);
    expect(
      readPersistedProductBrief(storage, identityFor(), AT_DEADLINE),
    ).toBeNull();
    expect(
      storage.value(productBriefPersistenceKey(identityFor())),
    ).toBeNull();
  });

  it("removes legacy records that have no server retention deadline", () => {
    const key = productBriefPersistenceKey(identityFor());
    const storage = memoryStorage({
      [key]: JSON.stringify({
        ...established(),
        retentionDeadline: undefined,
      }),
    });

    expect(
      readPersistedProductBrief(storage, identityFor(), BEFORE_DEADLINE),
    ).toBeNull();
    expect(storage.value(key)).toBeNull();
  });

  it("rejects commands whose identity or typed payload was changed", () => {
    const storage = memoryStorage();
    const value = {
      ...established(),
      schemaVersion: 3,
      commandStatus: "pending",
      pendingCommand: {
        schemaVersion: 1,
        kind: "revise",
        productId: PRODUCT_ID,
        productBriefId: "brief-a",
        idempotencyKey: "fixed-revision-key",
        payload: {
          expected_product_brief_version: 3,
          base_version_id: "version-a",
          reason: "Verified",
          fields: [
            {
              path: "common.brand",
              value: { kind: "FLAG_LIST", flags: ["wrong kind"] },
              sensitive: false,
              evidence: [],
            },
          ],
        },
      },
    };

    expect(
      writePersistedProductBrief(storage, value, BEFORE_DEADLINE),
    ).toBe(false);
    expect(
      storage.value(productBriefPersistenceKey(identityFor())),
    ).toBeNull();
  });

  it.each([
    [
      "an extra persisted-record key",
      () => pendingRevision({ unexpected: "retained" }),
    ],
    [
      "duplicate retained Asset Version IDs",
      () =>
        pendingRevision({
          assetVersionIds: [ASSET_VERSION_ID, ASSET_VERSION_ID],
        }),
    ],
    [
      "an analysis payload with version zero",
      () => ({
        ...established(),
        schemaVersion: 2,
        pendingAnalysis: {
          idempotencyKey: "fixed-analysis-key",
          priorProductBrief: null,
          payload: {
            workflow_id: "workflow-a",
            product_id: PRODUCT_ID,
            asset_version_ids: [ASSET_VERSION_ID],
            expected_workflow_version: 0,
          },
        },
      }),
    ],
    [
      "an extra confirmation payload key",
      () => ({
        ...pendingRevision(),
        pendingCommand: {
          schemaVersion: 1,
          kind: "confirm",
          productId: PRODUCT_ID,
          productBriefId: "brief-a",
          idempotencyKey: "fixed-confirmation-key",
          payload: {
            expected_product_brief_version: 3,
            product_brief_version_id: "version-a",
            expected_workflow_version: 4,
            reason_code: "HUMAN_VERIFIED",
            comment_ref: null,
            unexpected: true,
          },
        },
      }),
    ],
    [
      "an overlong revision reason",
      () => ({
        ...pendingRevision(),
        pendingCommand: revisionCommand({
          payload: {
            ...revisionCommand().payload,
            reason: "x".repeat(513),
          },
        }),
      }),
    ],
    [
      "duplicate revision field paths",
      () => {
        const command = revisionCommand();
        return {
          ...pendingRevision(),
          pendingCommand: {
            ...command,
            payload: {
              ...command.payload,
              fields: [
                command.payload.fields[0],
                structuredClone(command.payload.fields[0]),
              ],
            },
          },
        };
      },
    ],
    [
      "a revision field without evidence",
      () => {
        const command = revisionCommand();
        command.payload.fields[0].evidence = [];
        return { ...pendingRevision(), pendingCommand: command };
      },
    ],
    [
      "a revision field with more than 32 evidence records",
      () => {
        const command = revisionCommand();
        command.payload.fields[0].evidence = Array.from(
          { length: 33 },
          () => structuredClone(command.payload.fields[0].evidence[0]),
        );
        return { ...pendingRevision(), pendingCommand: command };
      },
    ],
    [
      "an evidence object with an extra key",
      () => {
        const command = revisionCommand();
        command.payload.fields[0].evidence[0].unexpected = true;
        return { ...pendingRevision(), pendingCommand: command };
      },
    ],
    [
      "an uncontrolled evidence reference",
      () => {
        const command = revisionCommand();
        command.payload.fields[0].evidence[0].reference =
          "https://attacker.example/evidence";
        return { ...pendingRevision(), pendingCommand: command };
      },
    ],
    [
      "an inherited evidence kind",
      () => {
        const command = revisionCommand();
        command.payload.fields[0].evidence[0].kind = "__proto__";
        command.payload.fields[0].evidence[0].reference =
          `o${EVIDENCE_TOKEN}`;
        return { ...pendingRevision(), pendingCommand: command };
      },
    ],
    [
      "an invalid evidence hash",
      () => {
        const command = revisionCommand();
        command.payload.fields[0].evidence[0].excerpt_sha256 =
          "A".repeat(64);
        return { ...pendingRevision(), pendingCommand: command };
      },
    ],
    [
      "invalid evidence region bounds",
      () => {
        const command = revisionCommand();
        command.payload.fields[0].evidence[0].region = [0.8, 0.2, 0.1, 0.9];
        return { ...pendingRevision(), pendingCommand: command };
      },
    ],
  ])("deletes a record containing %s", (_description, corruptRecord) => {
    const key = productBriefPersistenceKey(identityFor());
    const storage = memoryStorage({
      [key]: JSON.stringify(corruptRecord()),
    });

    expect(
      readPersistedProductBrief(storage, identityFor(), BEFORE_DEADLINE),
    ).toBeNull();
    expect(storage.value(key)).toBeNull();
  });

  it("fails closed when browser storage cannot persist a command", () => {
    const storage = {
      getItem() {
        return null;
      },
      setItem() {
        throw new Error("quota denied");
      },
      removeItem() {},
    };

    expect(
      writePersistedProductBrief(storage, established(), BEFORE_DEADLINE),
    ).toBe(false);
  });
});
