import { describe, expect, it } from "vitest";

import {
  canonicalizeBrandProfileCommandPayload,
  clearPendingBrandProfileCommand,
  createPendingBrandProfileCommand,
  readPendingBrandProfileCommand,
  savePendingBrandProfileCommand,
} from "../lib/brand-profile-pending-command";
import {
  brandProfileIdentityChangeGuard,
  nextUniqueColorName,
  nextUniqueProfileKey,
  nextUniqueRuleCode,
  normalizeLineList,
  splitEditableLineList,
} from "../lib/brand-profile-editor-state";

const PENDING_STORAGE_KEY =
  "commercevision:brand-profile:pending-commands:v1";

class MemoryStorage {
  #values = new Map();

  getItem(key) {
    return this.#values.get(key) ?? null;
  }

  removeItem(key) {
    this.#values.delete(key);
  }

  setItem(key, value) {
    this.#values.set(key, value);
  }
}

function pendingDraft() {
  return {
    rules: [],
    approved_colors: [],
    required_marks: [],
    prohibited_elements: [],
    tone_constraints: [],
    copy_constraints: [],
    purpose: "BRAND_PROFILE",
    provider: "qwen-vl",
    requires_derivative: true,
    selected_assets: [],
  };
}

describe("Brand Profile editor state", () => {
  it("normalizes a completed multiline edit without destroying line order", () => {
    expect(
      normalizeLineList(
        "Northstar wordmark\n\n  Registration mark  \nNorthstar wordmark",
      ),
    ).toEqual(["Northstar wordmark", "Registration mark"]);
  });

  it("preserves an unfinished trailing line while the textarea is active", () => {
    expect(splitEditableLineList("Northstar wordmark\n")).toEqual([
      "Northstar wordmark",
      "",
    ]);
  });

  it("allocates defaults from the first unused stable suffix", () => {
    expect(
      nextUniqueRuleCode([
        { code: "rule-1" },
        { code: "custom" },
        { code: "rule-3" },
      ]),
    ).toBe("rule-2");
    expect(
      nextUniqueColorName([
        { name: "Color 1" },
        { name: "Primary" },
        { name: "Color 3" },
      ]),
    ).toBe("Color 2");
    expect(
      nextUniqueProfileKey([
        { profile_key: "primary" },
        { profile_key: "profile-2" },
        { profile_key: "campaign" },
      ]),
    ).toBe("profile-3");
  });

  it("uses one identity-change guard for drafts, conflicts, creation, and unsettled commands", () => {
    expect(
      brandProfileIdentityChangeGuard({
        dirty: false,
        hasConflict: false,
        creatingAnother: false,
        pendingCommand: false,
      }),
    ).toBe("clear");
    for (const protectedState of [
      { dirty: true },
      { hasConflict: true },
      { creatingAnother: true },
    ]) {
      expect(
        brandProfileIdentityChangeGuard({
          dirty: false,
          hasConflict: false,
          creatingAnother: false,
          pendingCommand: false,
          ...protectedState,
        }),
      ).toBe("discard-required");
    }
    expect(
      brandProfileIdentityChangeGuard({
        dirty: true,
        hasConflict: true,
        creatingAnother: true,
        pendingCommand: true,
      }),
    ).toBe("frozen");
  });
});

describe("Brand Profile pending command persistence", () => {
  it("hashes semantically identical JSON payloads identically", async () => {
    const first = await canonicalizeBrandProfileCommandPayload({
      expected_version: 7,
      draft: { provider: "qwen-vl", rules: [] },
    });
    const second = await canonicalizeBrandProfileCommandPayload({
      draft: { rules: [], provider: "qwen-vl" },
      expected_version: 7,
    });

    expect(first.canonicalPayload).toBe(second.canonicalPayload);
    expect(first.payloadSha256).toMatch(/^[0-9a-f]{64}$/);
    expect(first.payloadSha256).toBe(second.payloadSha256);
  });

  it("restores only the exact workspace and brand command with its original key", async () => {
    const storage = new MemoryStorage();
    const command = await createPendingBrandProfileCommand({
      action: "UPDATE_DRAFT",
      workspaceId: "brand-workspace",
      brand: "Northstar Labs",
      profileId: "019f8a00-0000-7000-8000-000000000041",
      profileKey: "primary",
      expectedVersion: 7,
      expectedPublicationVersion: 2,
      idempotencyKey: "web-brand-profile-update-original",
      payload: {
        expected_version: 7,
        draft: pendingDraft(),
      },
      attemptedDraft: pendingDraft(),
      createdAt: "2026-07-30T10:00:00.000Z",
    });

    await savePendingBrandProfileCommand(storage, command);

    await expect(
      readPendingBrandProfileCommand(storage, {
        workspaceId: "other-workspace",
        brand: "Northstar Labs",
      }),
    ).resolves.toEqual({ kind: "absent" });
    await expect(
      readPendingBrandProfileCommand(storage, {
        workspaceId: "brand-workspace",
        brand: "Other Brand",
      }),
    ).resolves.toEqual({ kind: "absent" });
    await expect(
      readPendingBrandProfileCommand(storage, {
        workspaceId: "brand-workspace",
        brand: "Northstar Labs",
      }),
    ).resolves.toEqual({ kind: "valid", command });
    expect(command.idempotency_key).toBe(
      "web-brand-profile-update-original",
    );
    expect(command.attempted_draft).toEqual(pendingDraft());
    expect(command.command_sha256).toMatch(/^[0-9a-f]{64}$/);
  });

  it("clears only the exact command fingerprint", async () => {
    const storage = new MemoryStorage();
    const command = await createPendingBrandProfileCommand({
      action: "PUBLISH",
      workspaceId: "brand-workspace",
      brand: "Northstar Labs",
      profileId: "019f8a00-0000-7000-8000-000000000041",
      profileKey: "primary",
      expectedVersion: 7,
      expectedPublicationVersion: 2,
      idempotencyKey: "web-brand-profile-publish-original",
      payload: { expected_version: 7 },
      attemptedDraft: pendingDraft(),
      createdAt: "2026-07-30T10:00:00.000Z",
    });
    await savePendingBrandProfileCommand(storage, command);

    await expect(
      clearPendingBrandProfileCommand(storage, {
        ...command,
        payload_sha256: "0".repeat(64),
      }),
    ).resolves.toBe(false);
    await expect(
      readPendingBrandProfileCommand(storage, {
        workspaceId: "brand-workspace",
        brand: "Northstar Labs",
      }),
    ).resolves.toEqual({ kind: "valid", command });
    await expect(
      clearPendingBrandProfileCommand(storage, command),
    ).resolves.toBe(true);
    await expect(
      readPendingBrandProfileCommand(storage, {
        workspaceId: "brand-workspace",
        brand: "Northstar Labs",
      }),
    ).resolves.toEqual({ kind: "absent" });
  });

  it("marks a stored payload with a broken SHA-256 binding as unverifiable", async () => {
    const storage = new MemoryStorage();
    const command = await createPendingBrandProfileCommand({
      action: "PUBLISH",
      workspaceId: "brand-workspace",
      brand: "Northstar Labs",
      profileId: "019f8a00-0000-7000-8000-000000000041",
      profileKey: "primary",
      expectedVersion: 7,
      expectedPublicationVersion: 2,
      idempotencyKey: "web-brand-profile-publish-original",
      payload: { expected_version: 7 },
      attemptedDraft: pendingDraft(),
      createdAt: "2026-07-30T10:00:00.000Z",
    });
    await savePendingBrandProfileCommand(storage, command);
    const stored = JSON.parse(storage.getItem(PENDING_STORAGE_KEY));
    stored[0].payload.expected_version = 8;
    stored[0].expected_version = 8;
    storage.setItem(PENDING_STORAGE_KEY, JSON.stringify(stored));

    await expect(
      readPendingBrandProfileCommand(storage, {
        workspaceId: "brand-workspace",
        brand: "Northstar Labs",
      }),
    ).resolves.toEqual({
      kind: "unverifiable",
      reason: "PAYLOAD_FINGERPRINT_MISMATCH",
    });
  });

  it("rejects non-canonical command identities before they can own recovery authority", async () => {
    const storage = new MemoryStorage();
    const command = await createPendingBrandProfileCommand({
      action: "UPDATE_DRAFT",
      workspaceId: "brand-workspace",
      brand: "Northstar Labs",
      profileId: "019F8A00-0000-7000-8000-000000000041",
      profileKey: "primary",
      expectedVersion: 7,
      expectedPublicationVersion: 2,
      idempotencyKey: "web-brand-profile-update-noncanonical",
      payload: {
        expected_version: 7,
        draft: pendingDraft(),
      },
      attemptedDraft: pendingDraft(),
      createdAt: "2026-07-30T10:00:00.000Z",
    });

    await expect(
      savePendingBrandProfileCommand(storage, command),
    ).rejects.toThrow(/invalid/i);
    expect(storage.getItem(PENDING_STORAGE_KEY)).toBeNull();
  });

  it("marks envelope identity tampering as unverifiable even when the API payload is unchanged", async () => {
    const storage = new MemoryStorage();
    const command = await createPendingBrandProfileCommand({
      action: "UPDATE_DRAFT",
      workspaceId: "brand-workspace",
      brand: "Northstar Labs",
      profileId: "019f8a00-0000-7000-8000-000000000041",
      profileKey: "primary",
      expectedVersion: 7,
      expectedPublicationVersion: 2,
      idempotencyKey: "web-brand-profile-update-bound",
      payload: {
        expected_version: 7,
        draft: pendingDraft(),
      },
      attemptedDraft: pendingDraft(),
      createdAt: "2026-07-30T10:00:00.000Z",
    });
    await savePendingBrandProfileCommand(storage, command);
    const stored = JSON.parse(storage.getItem(PENDING_STORAGE_KEY));
    stored[0].profile_id = "019f8a00-0000-7000-8000-000000000099";
    storage.setItem(PENDING_STORAGE_KEY, JSON.stringify(stored));

    await expect(
      readPendingBrandProfileCommand(storage, {
        workspaceId: "brand-workspace",
        brand: "Northstar Labs",
      }),
    ).resolves.toEqual({
      kind: "unverifiable",
      reason: "COMMAND_FINGERPRINT_MISMATCH",
    });
  });

  it.each([
    ["not JSON", "{", "MALFORMED_STORAGE"],
    [
      "an old schema",
      JSON.stringify([{ schema_version: 0 }]),
      "UNSUPPORTED_OR_INVALID_COMMAND",
    ],
  ])(
    "fails closed when session storage contains %s",
    async (_description, raw, reason) => {
      const storage = new MemoryStorage();
      storage.setItem(PENDING_STORAGE_KEY, raw);

      await expect(
        readPendingBrandProfileCommand(storage, {
          workspaceId: "brand-workspace",
          brand: "Northstar Labs",
        }),
      ).resolves.toEqual({ kind: "unverifiable", reason });
    },
  );

  it("does not overwrite an unverifiable record with a new command", async () => {
    const storage = new MemoryStorage();
    const original = JSON.stringify([{ schema_version: 0 }]);
    storage.setItem(PENDING_STORAGE_KEY, original);
    const command = await createPendingBrandProfileCommand({
      action: "CREATE",
      workspaceId: "brand-workspace",
      brand: "Northstar Labs",
      profileId: null,
      profileKey: "primary",
      expectedVersion: 0,
      expectedPublicationVersion: 0,
      idempotencyKey: "web-brand-profile-create-new",
      payload: {
        brand: "Northstar Labs",
        profile_key: "primary",
        draft: pendingDraft(),
      },
      attemptedDraft: pendingDraft(),
      createdAt: "2026-07-30T10:00:00.000Z",
    });

    await expect(
      savePendingBrandProfileCommand(storage, command),
    ).rejects.toThrow(/unverifiable/i);
    expect(storage.getItem(PENDING_STORAGE_KEY)).toBe(original);
  });
});
