import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { BrandProfilePublicationHistory } from "../app/brand-profile-publication-history";
import {
  BrandProfileApiCancelledError,
  BrandProfileApiError,
} from "../lib/brand-profile-api";
import {
  BrandProfileCommandCoordinator,
  BrandProfileLocalReconciliationError,
  classifyBrandProfileCommandFailure,
  isBrandProfileAuthorityLoss,
} from "../lib/brand-profile-command-coordinator";
import {
  createPendingBrandProfileCommand,
  readPendingBrandProfileCommand,
  savePendingBrandProfileCommand,
} from "../lib/brand-profile-pending-command";
import {
  BRAND_PROFILE_HISTORY_PAGE_SIZE,
  BRAND_PROFILE_VALIDATION_FRESHNESS_MS,
  createBrandProfileWorkbenchController,
} from "../lib/brand-profile-workbench-controller";

const PROFILE_ID = "019f8a00-0000-7000-8000-000000000041";
const PROFILE_VERSION_ID =
  "019f8a00-0000-7000-8000-000000000043";
const ASSET_ID = "019f8a00-0000-7000-8000-000000000044";
const ASSET_VERSION_ID =
  "019f8a00-0000-7000-8000-000000000045";
const RIGHTS_ID = "019f8a00-0000-7000-8000-000000000046";

globalThis.React = React;

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

function draft(instruction = "Keep one mark-width of clear space.") {
  return {
    rules: [
      {
        code: "logo.clear-space",
        scope: "VISUAL",
        instruction,
      },
    ],
    approved_colors: [{ name: "Primary", value: "#1457FF" }],
    required_marks: ["Northstar wordmark"],
    prohibited_elements: ["Competitor marks"],
    tone_constraints: ["Calm"],
    copy_constraints: ["No unsupported claims"],
    purpose: "BRAND_CONTEXT",
    provider: "alibaba",
    requires_derivative: true,
    selected_assets: [
      {
        asset_version_id: ASSET_VERSION_ID,
        role: "LOGO",
      },
    ],
  };
}

function profile({
  version = 3,
  profileDraft = draft(),
  state = "ACTIVE",
} = {}) {
  return {
    id: PROFILE_ID,
    workspace_id: "brand-workspace",
    brand: "Northstar Labs",
    profile_key: "primary",
    state,
    draft: profileDraft,
    current_version_id: PROFILE_VERSION_ID,
    current_version_number: 1,
    version,
    stale_at: state === "NEEDS_REPUBLISH"
      ? "2026-07-30T08:00:00Z"
      : null,
    created_by: "brand-admin",
    created_at: "2026-07-30T07:00:00Z",
    updated_by: "brand-admin",
    updated_at: "2026-07-30T08:00:00Z",
  };
}

function publishedVersion(
  versionNumber,
  {
    profileId = PROFILE_ID,
    currentlyUsable = true,
    decidedAt = "2026-07-30T08:00:00Z",
  } = {},
) {
  return {
    id:
      versionNumber === 1
        ? PROFILE_VERSION_ID
        : `019f8a00-0000-7000-8000-00000000005${versionNumber}`,
    workspace_id: "brand-workspace",
    profile_id: profileId,
    version_number: versionNumber,
    draft: draft(`Published instruction ${versionNumber}`),
    content_sha256: "a".repeat(64),
    published_by: "brand-admin",
    published_at: `2026-07-30T0${versionNumber}:00:00Z`,
    members: [
      {
        ordinal: 0,
        asset_id: ASSET_ID,
        asset_version_id: ASSET_VERSION_ID,
        role: "LOGO",
        published_rights_record_id: RIGHTS_ID,
        published_rights_record_version: 1,
        currently_usable: currentlyUsable,
        current_reason_code: currentlyUsable
          ? "AUTHORIZED"
          : "RIGHTS_REVOKED",
        current_rights_record_id: RIGHTS_ID,
        current_rights_record_version: currentlyUsable ? 1 : 2,
        decided_at: decidedAt,
      },
    ],
  };
}

describe("Brand Profile workbench controller", () => {
  it("publishes snapshot changes through one subscribable authority", () => {
    const controller = createBrandProfileWorkbenchController({
      workspaceId: "brand-workspace",
      brand: "Northstar Labs",
    });
    const notifications = [];
    const unsubscribe = controller.subscribe(() => {
      notifications.push(controller.getSnapshot());
    });

    const staleRead = controller.beginProfileRead(PROFILE_ID);
    const currentRead = controller.beginProfileRead(PROFILE_ID);
    expect(controller.publishProfile(currentRead, profile())).toBe(true);
    expect(controller.publishProfile(staleRead, profile())).toBe(false);
    controller.editDraft(draft("Use the compact wordmark."));
    unsubscribe();
    controller.editDraft(draft("Use the horizontal wordmark."));

    expect(notifications).toHaveLength(2);
    expect(notifications[0].profile.id).toBe(PROFILE_ID);
    expect(notifications[1].dirty).toBe(true);
  });

  it("rejects a late profile response after the workspace or brand changes", () => {
    const controller = createBrandProfileWorkbenchController({
      workspaceId: "brand-workspace",
      brand: "Northstar Labs",
    });
    const token = controller.beginProfileRead(PROFILE_ID);

    controller.changeIdentity({
      workspaceId: "other-workspace",
      brand: "Other Brand",
    });

    expect(controller.publishProfile(token, profile())).toBe(false);
    expect(controller.getSnapshot().profile).toBeNull();
    expect(controller.getSnapshot().identity).toEqual({
      workspaceId: "other-workspace",
      brand: "Other Brand",
    });
  });

  it("publishes only an exact optimistic mutation response", () => {
    const controller = createBrandProfileWorkbenchController({
      workspaceId: "brand-workspace",
      brand: "Northstar Labs",
    });
    const read = controller.beginProfileRead(PROFILE_ID);
    expect(controller.publishProfile(read, profile())).toBe(true);
    const attemptedDraft = draft("Use the compact mark below 320 px.");
    const mutation = controller.beginMutation({
      profileId: PROFILE_ID,
      profileKey: "primary",
      expectedVersion: 3,
      draft: attemptedDraft,
    });

    expect(
      controller.publishMutation(
        mutation,
        profile({ version: 5, profileDraft: attemptedDraft }),
      ),
    ).toBe(false);
    expect(
      controller.publishMutation(
        mutation,
        profile({ version: 4, profileDraft: attemptedDraft }),
      ),
    ).toBe(true);
    expect(controller.getSnapshot()).toMatchObject({
      dirty: false,
      conflict: null,
      profile: { version: 4 },
      draft: attemptedDraft,
    });
  });

  it("binds an existing-profile mutation response to its immutable profile key", () => {
    const controller = createBrandProfileWorkbenchController({
      workspaceId: "brand-workspace",
      brand: "Northstar Labs",
    });
    const read = controller.beginProfileRead(PROFILE_ID);
    expect(controller.publishProfile(read, profile())).toBe(true);
    const attemptedDraft = draft("Keep the immutable key bound.");
    const mutation = controller.beginMutation({
      profileId: PROFILE_ID,
      profileKey: "primary",
      expectedVersion: 3,
      draft: attemptedDraft,
    });

    expect(
      controller.publishMutation(mutation, {
        ...profile({ version: 4, profileDraft: attemptedDraft }),
        profile_key: "secondary",
      }),
    ).toBe(false);
    expect(
      controller.recordVersionConflict(mutation, {
        ...profile({
          version: 4,
          profileDraft: draft("Authoritative but wrong identity."),
        }),
        profile_key: "secondary",
      }),
    ).toBe(false);
    expect(controller.getSnapshot().profile?.profile_key).toBe("primary");
  });

  it("binds a create response to the requested profile key", () => {
    const controller = createBrandProfileWorkbenchController({
      workspaceId: "brand-workspace",
      brand: "Northstar Labs",
    });
    const existing = controller.beginProfileRead(PROFILE_ID);
    controller.publishProfile(existing, profile());
    const secondaryProfileId =
      "019f8a00-0000-7000-8000-000000000099";
    const mutation = controller.beginMutation({
      profileId: null,
      profileKey: "secondary",
      expectedVersion: 0,
      draft: draft(),
    });

    expect(
      controller.publishMutation(
        mutation,
        {
          ...profile({ version: 1 }),
          id: secondaryProfileId,
          profile_key: "primary",
        },
      ),
    ).toBe(false);
    expect(
      controller.publishMutation(
        mutation,
        {
          ...profile({ version: 1 }),
          id: secondaryProfileId,
          profile_key: "secondary",
        },
      ),
    ).toBe(true);
    expect(controller.getSnapshot().profile?.id).toBe(secondaryProfileId);
  });

  it("keeps the exact local draft across a 409 until restore or discard", () => {
    const controller = createBrandProfileWorkbenchController({
      workspaceId: "brand-workspace",
      brand: "Northstar Labs",
    });
    const read = controller.beginProfileRead(PROFILE_ID);
    controller.publishProfile(read, profile());
    const attemptedDraft = draft("Preserve this unsaved local rule.");
    const mutation = controller.beginMutation({
      profileId: PROFILE_ID,
      profileKey: "primary",
      expectedVersion: 3,
      draft: attemptedDraft,
    });
    attemptedDraft.rules[0].instruction = "Mutated after submission";

    expect(
      controller.recordVersionConflict(
        mutation,
        profile({
          version: 4,
          profileDraft: draft("Authoritative server rule."),
        }),
      ),
    ).toBe(true);
    expect(
      controller.getSnapshot().conflict?.attemptedDraft.rules[0].instruction,
    ).toBe("Preserve this unsaved local rule.");

    expect(controller.restoreConflictDraft()).toBe(true);
    expect(controller.getSnapshot().draft.rules[0].instruction).toBe(
      "Preserve this unsaved local rule.",
    );
    expect(controller.getSnapshot().dirty).toBe(true);

    const retry = controller.beginMutation({
      profileId: PROFILE_ID,
      profileKey: "primary",
      expectedVersion: 4,
      draft: controller.getSnapshot().draft,
    });
    controller.recordVersionConflict(
      retry,
      profile({
        version: 5,
        profileDraft: draft("Newest authoritative rule."),
      }),
    );
    expect(controller.discardConflictDraft()).toBe(true);
    expect(controller.getSnapshot().draft.rules[0].instruction).toBe(
      "Newest authoritative rule.",
    );
    expect(controller.getSnapshot().dirty).toBe(false);
  });

  it("accepts validation only for the still-current optimistic version", () => {
    const controller = createBrandProfileWorkbenchController({
      workspaceId: "brand-workspace",
      brand: "Northstar Labs",
    });
    const read = controller.beginProfileRead(PROFILE_ID);
    controller.publishProfile(read, profile());
    const token = controller.beginValidation(PROFILE_ID, 3);

    expect(
      controller.publishValidation(token, {
        profile_id: PROFILE_ID,
        profile_version: 2,
        valid: false,
        decided_at: "2026-07-30T08:00:00Z",
        issues: [],
      }),
    ).toBe(false);
    expect(
      controller.publishValidation(token, {
        profile_id: PROFILE_ID,
        profile_version: 3,
        valid: false,
        decided_at: "2026-07-30T08:00:00Z",
        issues: [
          {
            asset_version_id: ASSET_VERSION_ID,
            role: "LOGO",
            reason_code: "RIGHTS_REVOKED",
            message: "The current Rights Record is revoked.",
          },
        ],
      }),
    ).toBe(true);
    expect(controller.getSnapshot().validation?.valid).toBe(false);

    controller.beginValidation(PROFILE_ID, 3);
    expect(controller.getSnapshot().validation).toBeNull();

    controller.editDraft(draft("Editing invalidates prior validation."));
    expect(controller.getSnapshot().validation).toBeNull();
  });

  it("invalidates an accepted validation as soon as a mutation starts", () => {
    const controller = createBrandProfileWorkbenchController({
      workspaceId: "brand-workspace",
      brand: "Northstar Labs",
    });
    const read = controller.beginProfileRead(PROFILE_ID);
    controller.publishProfile(read, profile());
    const validation = controller.beginValidation(PROFILE_ID, 3);
    controller.publishValidation(validation, {
      profile_id: PROFILE_ID,
      profile_version: 3,
      valid: true,
      decided_at: "2026-07-30T08:00:00Z",
      issues: [],
    });

    controller.beginMutation({
      profileId: PROFILE_ID,
      profileKey: "primary",
      expectedVersion: 3,
      draft: draft(),
    });

    expect(controller.getSnapshot().validation).toBeNull();
  });

  it("expires an accepted validation at a bounded client freshness deadline", () => {
    const controller = createBrandProfileWorkbenchController({
      workspaceId: "brand-workspace",
      brand: "Northstar Labs",
    });
    const read = controller.beginProfileRead(PROFILE_ID);
    controller.publishProfile(read, profile());
    const validation = controller.beginValidation(PROFILE_ID, 3);
    controller.publishValidation(
      validation,
      {
        profile_id: PROFILE_ID,
        profile_version: 3,
        valid: true,
        decided_at: "2026-07-30T08:00:00Z",
        issues: [],
      },
      1_000,
    );

    expect(controller.getSnapshot().validationExpiresAt).toBe(
      1_000 + BRAND_PROFILE_VALIDATION_FRESHNESS_MS,
    );
    expect(
      controller.expireValidation(
        1_000 + BRAND_PROFILE_VALIDATION_FRESHNESS_MS - 1,
      ),
    ).toBe(false);
    expect(controller.getSnapshot().validation?.valid).toBe(true);
    expect(
      controller.expireValidation(
        1_000 + BRAND_PROFILE_VALIDATION_FRESHNESS_MS,
      ),
    ).toBe(true);
    expect(controller.getSnapshot().validation).toBeNull();
    expect(controller.getSnapshot().validationExpiresAt).toBeNull();
  });

  it("preserves a dirty draft during a same-profile refresh and exposes server advancement as a conflict", () => {
    const controller = createBrandProfileWorkbenchController({
      workspaceId: "brand-workspace",
      brand: "Northstar Labs",
    });
    const initialRead = controller.beginProfileRead(PROFILE_ID);
    controller.publishProfile(initialRead, profile());
    const localDraft = draft("Unsaved local instruction.");
    controller.editDraft(localDraft);

    const refresh = controller.beginProfileRead(PROFILE_ID);
    expect(
      controller.publishProfile(
        refresh,
        profile({
          version: 4,
          profileDraft: draft("Server-side instruction."),
        }),
        false,
      ),
    ).toBe(true);

    expect(controller.getSnapshot()).toMatchObject({
      dirty: true,
      draft: localDraft,
      profile: { version: 4 },
      conflict: {
        attemptedDraft: localDraft,
        authoritativeProfile: { version: 4 },
      },
    });
  });

  it("rejects a non-destructive refresh that tries to switch profiles", () => {
    const controller = createBrandProfileWorkbenchController({
      workspaceId: "brand-workspace",
      brand: "Northstar Labs",
    });
    const initialRead = controller.beginProfileRead(PROFILE_ID);
    controller.publishProfile(initialRead, profile());
    controller.editDraft(draft("Do not discard this."));
    const otherProfileId = "019f8a00-0000-7000-8000-000000000099";
    const refresh = controller.beginProfileRead(otherProfileId);

    expect(
      controller.publishProfile(
        refresh,
        { ...profile(), id: otherProfileId },
        false,
      ),
    ).toBe(false);
    expect(controller.getSnapshot().profile?.id).toBe(PROFILE_ID);
    expect(controller.getSnapshot().draft?.rules[0].instruction).toBe(
      "Do not discard this.",
    );
  });

  it("guards and deduplicates bounded cursor history pages", () => {
    const controller = createBrandProfileWorkbenchController({
      workspaceId: "brand-workspace",
      brand: "Northstar Labs",
    });
    const read = controller.beginProfileRead(PROFILE_ID);
    controller.publishProfile(read, profile());
    const first = controller.beginHistoryRead("initial", PROFILE_ID);

    expect(
      controller.publishHistory(first, {
        items: [publishedVersion(3), publishedVersion(2)],
        next_cursor: "cursor-2",
      }),
    ).toBe(true);
    const more = controller.beginHistoryRead("more", PROFILE_ID);
    expect(
      controller.publishHistory(more, {
        items: [publishedVersion(2), publishedVersion(1)],
        next_cursor: null,
      }),
    ).toBe(true);
    expect(
      controller.getSnapshot().versions.map((item) => item.version_number),
    ).toEqual([3, 2, 1]);
    expect(controller.getSnapshot().historyLoading).toBeNull();
    expect(controller.getSnapshot().historyStatus).toBe("ready");

    const invalidMore = controller.beginHistoryRead("more", PROFILE_ID);
    expect(
      controller.publishHistory(invalidMore, {
        items: [publishedVersion(4)],
        next_cursor: null,
      }),
    ).toBe(false);
    expect(
      controller.getSnapshot().versions.map((item) => item.version_number),
    ).toEqual([3, 2, 1]);

    const oversized = controller.beginHistoryRead("initial", PROFILE_ID);
    expect(
      controller.publishHistory(oversized, {
        items: Array.from(
          { length: BRAND_PROFILE_HISTORY_PAGE_SIZE + 1 },
          (_, index) => publishedVersion(index + 1),
        ),
        next_cursor: null,
      }),
    ).toBe(false);
    expect(controller.getSnapshot().historyStatus).toBe("error");
  });

  it("distinguishes a failed history read from an authoritative empty history", () => {
    const controller = createBrandProfileWorkbenchController({
      workspaceId: "brand-workspace",
      brand: "Northstar Labs",
    });
    const read = controller.beginProfileRead(PROFILE_ID);
    controller.publishProfile(read, profile());
    const failed = controller.beginHistoryRead("initial", PROFILE_ID);

    expect(controller.getSnapshot().historyStatus).toBe("loading");
    expect(controller.failHistory(failed)).toBe(true);
    expect(controller.getSnapshot()).toMatchObject({
      historyStatus: "error",
      historyLoading: null,
      versions: [],
    });

    const empty = controller.beginHistoryRead("initial", PROFILE_ID);
    expect(
      controller.publishHistory(empty, {
        items: [],
        next_cursor: null,
      }),
    ).toBe(true);
    expect(controller.getSnapshot().historyStatus).toBe("ready");
  });

  it("shows an unknown or loading publication count until an authoritative empty page arrives", () => {
    const controller = createBrandProfileWorkbenchController({
      workspaceId: "brand-workspace",
      brand: "Northstar Labs",
    });
    const renderHistory = () => {
      const snapshot = controller.getSnapshot();
      return renderToStaticMarkup(
        React.createElement(BrandProfilePublicationHistory, {
          disabled: false,
          historyLoading: snapshot.historyLoading,
          historyStatus: snapshot.historyStatus,
          onLoadMore: () => undefined,
          onLoadVersion: () => undefined,
          profileId: PROFILE_ID,
          selectedVersion: null,
          selectedVersionFocusNonce: 0,
          versions: snapshot.versions,
          versionsNextCursor: snapshot.versionsNextCursor,
          versionLoading: null,
        }),
      );
    };

    expect(renderHistory()).toContain("版本数未知");
    expect(renderHistory()).not.toContain("0 个版本");

    const profileRead = controller.beginProfileRead(PROFILE_ID);
    controller.publishProfile(profileRead, profile());
    const failedRead = controller.beginHistoryRead("initial", PROFILE_ID);
    expect(renderHistory()).toContain("版本数读取中…");
    expect(renderHistory()).not.toContain("0 个版本");

    controller.failHistory(failedRead);
    expect(renderHistory()).toContain("版本数未知");
    expect(renderHistory()).not.toContain("0 个版本");

    const emptyRead = controller.beginHistoryRead("initial", PROFILE_ID);
    controller.publishHistory(emptyRead, {
      items: [],
      next_cursor: null,
    });
    expect(renderHistory()).toContain("0 个版本");
  });

  it("emits a focus intent only for an explicit history selection", () => {
    const controller = createBrandProfileWorkbenchController({
      workspaceId: "brand-workspace",
      brand: "Northstar Labs",
    });
    const read = controller.beginProfileRead(PROFILE_ID);
    controller.publishProfile(read, profile());

    const automatic = controller.beginVersionRead(PROFILE_ID, 1);
    expect(
      controller.publishVersion(automatic, publishedVersion(1)),
    ).toBe(true);
    expect(controller.getSnapshot().selectedVersionFocusNonce).toBe(0);

    const explicit = controller.beginVersionRead(PROFILE_ID, 1, true);
    expect(
      controller.publishVersion(explicit, publishedVersion(1)),
    ).toBe(true);
    expect(controller.getSnapshot().selectedVersionFocusNonce).toBe(1);

    const refresh = controller.beginVersionRead(PROFILE_ID, 1);
    expect(
      controller.publishVersion(refresh, publishedVersion(1)),
    ).toBe(true);
    expect(controller.getSnapshot().selectedVersionFocusNonce).toBe(1);
  });

  it("refreshes current usability without changing immutable publication facts", () => {
    const controller = createBrandProfileWorkbenchController({
      workspaceId: "brand-workspace",
      brand: "Northstar Labs",
    });
    const read = controller.beginProfileRead(PROFILE_ID);
    controller.publishProfile(read, profile());
    const firstRead = controller.beginVersionRead(PROFILE_ID, 1);
    expect(
      controller.publishVersion(firstRead, publishedVersion(1)),
    ).toBe(true);
    const frozenRights =
      controller.getSnapshot().selectedVersion?.members[0]
        .published_rights_record_id;

    const refresh = controller.beginVersionRead(PROFILE_ID, 1);
    expect(
      controller.publishVersion(
        refresh,
        publishedVersion(1, {
          currentlyUsable: false,
          decidedAt: "2026-07-30T09:00:00Z",
        }),
      ),
    ).toBe(true);
    const member = controller.getSnapshot().selectedVersion?.members[0];
    expect(member?.published_rights_record_id).toBe(frozenRights);
    expect(member?.currently_usable).toBe(false);
    expect(member?.current_reason_code).toBe("RIGHTS_REVOKED");
  });
});

describe("Brand Profile pending command authority", () => {
  it("classifies command failures without treating local reconciliation as a server rejection", () => {
    expect(
      classifyBrandProfileCommandFailure(
        new BrandProfileApiCancelledError(),
      ),
    ).toEqual({ kind: "uncertain" });
    expect(
      classifyBrandProfileCommandFailure(
        new BrandProfileApiError(422, {
          code: "BRAND_PROFILE_PUBLICATION_REJECTED",
          category: "validation",
          message: "Rights are no longer valid.",
          retryable: false,
          request_id: "request-rejected",
          trace_id: "trace-rejected",
        }),
      ),
    ).toEqual({
      kind: "deterministic-rejected",
      requiresExplicitDiscard: false,
    });
    expect(
      classifyBrandProfileCommandFailure(
        new BrandProfileApiError(409, {
          code: "VERSION_CONFLICT",
          category: "conflict",
          message: "Another administrator won the write.",
          retryable: false,
          request_id: "request-conflict",
          trace_id: "trace-conflict",
        }),
      ),
    ).toEqual({
      kind: "deterministic-rejected",
      requiresExplicitDiscard: true,
    });
    expect(
      classifyBrandProfileCommandFailure(
        new BrandProfileLocalReconciliationError(
          "The accepted response could not be reconciled locally.",
        ),
      ),
    ).toEqual({ kind: "local-reconciliation-failure" });
    expect(
      classifyBrandProfileCommandFailure(
        new Error("An unexpected local validation failure."),
      ),
    ).toEqual({ kind: "local-reconciliation-failure" });
  });

  it("recognizes direct and reconciliation-wrapped authority loss", () => {
    const forbidden = new BrandProfileApiError(403, {
      code: "WORKSPACE_ACCESS_DENIED",
      category: "authorization",
      message: "Membership was revoked.",
      retryable: false,
      request_id: "request-forbidden",
      trace_id: "trace-forbidden",
    });

    expect(isBrandProfileAuthorityLoss(forbidden)).toBe(true);
    expect(
      isBrandProfileAuthorityLoss(
        new BrandProfileLocalReconciliationError(
          "Recovery preflight could not read the profile.",
          forbidden,
        ),
      ),
    ).toBe(true);
    expect(
      isBrandProfileAuthorityLoss(
        new BrandProfileApiError(409, {
          code: "VERSION_CONFLICT",
          category: "conflict",
          message: "Another administrator won.",
          retryable: false,
          request_id: "request-conflict",
          trace_id: "trace-conflict",
        }),
      ),
    ).toBe(false);
  });

  it("rejects a first-run mutation confirmation whose current head has another profile key", async () => {
    const storage = new MemoryStorage();
    const accepted = profile({
      version: 4,
      profileDraft: draft("Accepted mutation response."),
    });
    const controller = createBrandProfileWorkbenchController({
      workspaceId: "brand-workspace",
      brand: "Northstar Labs",
    });
    const baselineRead = controller.beginProfileRead(PROFILE_ID);
    expect(
      controller.publishProfile(
        baselineRead,
        profile({ version: 3 }),
      ),
    ).toBe(true);
    const coordinator = new BrandProfileCommandCoordinator({
      api: {
        get: async () => ({
          ...accepted,
          profile_key: "secondary",
        }),
      },
      brand: "Northstar Labs",
      controller,
      storage: () => storage,
      workspaceId: "brand-workspace",
    });

    await expect(
      coordinator.confirmAcceptedMutation(
        accepted,
        new AbortController().signal,
        "primary",
      ),
    ).rejects.toBeInstanceOf(BrandProfileLocalReconciliationError);
    expect(controller.getSnapshot().profile?.profile_key).toBe("primary");
  });

  it("retains the original update command when a 2xx replay cannot be confirmed by the current head", async () => {
    const storage = new MemoryStorage();
    const attemptedDraft = draft("The accepted replay draft.");
    const command = await createPendingBrandProfileCommand({
      action: "UPDATE_DRAFT",
      workspaceId: "brand-workspace",
      brand: "Northstar Labs",
      profileId: PROFILE_ID,
      profileKey: "primary",
      expectedVersion: 3,
      expectedPublicationVersion: 1,
      idempotencyKey: "accepted-update-key",
      payload: {
        expected_version: 3,
        draft: attemptedDraft,
      },
      attemptedDraft,
    });
    await savePendingBrandProfileCommand(storage, command);
    const baseline = profile({ version: 3 });
    const accepted = profile({
      version: 4,
      profileDraft: attemptedDraft,
    });
    let reads = 0;
    const api = {
      get: async () => {
        reads += 1;
        return baseline;
      },
      updateDraft: async () => accepted,
    };
    const controller = createBrandProfileWorkbenchController({
      workspaceId: "brand-workspace",
      brand: "Northstar Labs",
    });
    const coordinator = new BrandProfileCommandCoordinator({
      api,
      brand: "Northstar Labs",
      controller,
      storage: () => storage,
      workspaceId: "brand-workspace",
    });

    await expect(
      coordinator.recover(command, {
        administrator: true,
        listedProfiles: [baseline],
        signal: new AbortController().signal,
      }),
    ).rejects.toBeInstanceOf(BrandProfileLocalReconciliationError);
    expect(reads).toBe(2);
    await expect(
      readPendingBrandProfileCommand(storage, {
        workspaceId: "brand-workspace",
        brand: "Northstar Labs",
      }),
    ).resolves.toEqual({ kind: "valid", command });
  });

  it("does not attribute an identical draft written by another administrator to the pending update key", async () => {
    const storage = new MemoryStorage();
    const attemptedDraft = draft("The shared draft content.");
    const command = await createPendingBrandProfileCommand({
      action: "UPDATE_DRAFT",
      workspaceId: "brand-workspace",
      brand: "Northstar Labs",
      profileId: PROFILE_ID,
      profileKey: "primary",
      expectedVersion: 3,
      expectedPublicationVersion: 1,
      idempotencyKey: "admin-a-update-key",
      payload: {
        expected_version: 3,
        draft: attemptedDraft,
      },
      attemptedDraft,
    });
    await savePendingBrandProfileCommand(storage, command);
    const authoritative = profile({
      version: 4,
      profileDraft: attemptedDraft,
    });
    const updateKeys = [];
    const api = {
      get: async () => authoritative,
      updateDraft: async (_profileId, _payload, idempotencyKey) => {
        updateKeys.push(idempotencyKey);
        throw new BrandProfileApiError(409, {
          code: "VERSION_CONFLICT",
          category: "conflict",
          message: "Another administrator won the version race.",
          retryable: false,
          request_id: "request-admin-a-update",
          trace_id: "trace-admin-a-update",
        });
      },
    };
    const controller = createBrandProfileWorkbenchController({
      workspaceId: "brand-workspace",
      brand: "Northstar Labs",
    });
    const coordinator = new BrandProfileCommandCoordinator({
      api,
      brand: "Northstar Labs",
      controller,
      storage: () => storage,
      workspaceId: "brand-workspace",
    });

    await expect(
      coordinator.recover(command, {
        administrator: true,
        listedProfiles: [authoritative],
        signal: new AbortController().signal,
      }),
    ).resolves.toMatchObject({
      kind: "blocked",
      pendingRetained: true,
      discardAllowed: true,
    });
    expect(updateKeys).toEqual(["admin-a-update-key"]);
    await expect(
      readPendingBrandProfileCommand(storage, {
        workspaceId: "brand-workspace",
        brand: "Northstar Labs",
      }),
    ).resolves.toEqual({ kind: "valid", command });
  });

  it("does not attribute an adjacent publication written by another administrator to the pending publish key", async () => {
    const storage = new MemoryStorage();
    const attemptedDraft = draft(
      "Preserve the exact draft that the administrator tried to publish.",
    );
    const command = await createPendingBrandProfileCommand({
      action: "PUBLISH",
      workspaceId: "brand-workspace",
      brand: "Northstar Labs",
      profileId: PROFILE_ID,
      profileKey: "primary",
      expectedVersion: 3,
      expectedPublicationVersion: 1,
      idempotencyKey: "admin-a-publish-key",
      payload: { expected_version: 3 },
      attemptedDraft,
    });
    await savePendingBrandProfileCommand(storage, command);
    const authoritative = {
      ...profile({ version: 4 }),
      current_version_number: 2,
    };
    const publishKeys = [];
    const api = {
      get: async () => authoritative,
      publish: async (_profileId, _payload, idempotencyKey) => {
        publishKeys.push(idempotencyKey);
        throw new BrandProfileApiError(409, {
          code: "VERSION_CONFLICT",
          category: "conflict",
          message: "Another administrator published version 2.",
          retryable: false,
          request_id: "request-admin-a-publish",
          trace_id: "trace-admin-a-publish",
        });
      },
    };
    const controller = createBrandProfileWorkbenchController({
      workspaceId: "brand-workspace",
      brand: "Northstar Labs",
    });
    const coordinator = new BrandProfileCommandCoordinator({
      api,
      brand: "Northstar Labs",
      controller,
      storage: () => storage,
      workspaceId: "brand-workspace",
    });

    await expect(
      coordinator.recover(command, {
        administrator: true,
        listedProfiles: [authoritative],
        signal: new AbortController().signal,
      }),
    ).resolves.toMatchObject({
      kind: "blocked",
      pendingRetained: true,
      discardAllowed: true,
    });
    expect(publishKeys).toEqual(["admin-a-publish-key"]);
    expect(controller.getSnapshot()).toMatchObject({
      dirty: true,
      draft: attemptedDraft,
      profile: authoritative,
    });
    await expect(
      readPendingBrandProfileCommand(storage, {
        workspaceId: "brand-workspace",
        brand: "Northstar Labs",
      }),
    ).resolves.toEqual({ kind: "valid", command });
  });

  it("retains a pending command when the authoritative recovery preflight loses access", async () => {
    const storage = new MemoryStorage();
    const attemptedDraft = draft("Do not erase this recovery evidence.");
    const command = await createPendingBrandProfileCommand({
      action: "UPDATE_DRAFT",
      workspaceId: "brand-workspace",
      brand: "Northstar Labs",
      profileId: PROFILE_ID,
      profileKey: "primary",
      expectedVersion: 3,
      expectedPublicationVersion: 1,
      idempotencyKey: "authority-loss-update-key",
      payload: {
        expected_version: 3,
        draft: attemptedDraft,
      },
      attemptedDraft,
    });
    await savePendingBrandProfileCommand(storage, command);
    const api = {
      get: async () => {
        throw new BrandProfileApiError(403, {
          code: "WORKSPACE_ACCESS_DENIED",
          category: "authorization",
          message: "Membership was revoked.",
          retryable: false,
          request_id: "request-authority-loss",
          trace_id: "trace-authority-loss",
        });
      },
    };
    const controller = createBrandProfileWorkbenchController({
      workspaceId: "brand-workspace",
      brand: "Northstar Labs",
    });
    const coordinator = new BrandProfileCommandCoordinator({
      api,
      brand: "Northstar Labs",
      controller,
      storage: () => storage,
      workspaceId: "brand-workspace",
    });

    await expect(
      coordinator.recover(command, {
        administrator: true,
        listedProfiles: [],
        signal: new AbortController().signal,
      }),
    ).rejects.toBeInstanceOf(BrandProfileLocalReconciliationError);
    await expect(
      readPendingBrandProfileCommand(storage, {
        workspaceId: "brand-workspace",
        brand: "Northstar Labs",
      }),
    ).resolves.toEqual({ kind: "valid", command });
  });

  it("refuses to replay a command when the authoritative profile key no longer matches its identity", async () => {
    const storage = new MemoryStorage();
    const attemptedDraft = draft("Keep the command bound to primary.");
    const command = await createPendingBrandProfileCommand({
      action: "UPDATE_DRAFT",
      workspaceId: "brand-workspace",
      brand: "Northstar Labs",
      profileId: PROFILE_ID,
      profileKey: "primary",
      expectedVersion: 3,
      expectedPublicationVersion: 1,
      idempotencyKey: "identity-bound-update-key",
      payload: {
        expected_version: 3,
        draft: attemptedDraft,
      },
      attemptedDraft,
    });
    await savePendingBrandProfileCommand(storage, command);
    let replayed = false;
    const api = {
      get: async () => ({ ...profile(), profile_key: "secondary" }),
      updateDraft: async () => {
        replayed = true;
        return profile({ version: 4, profileDraft: attemptedDraft });
      },
    };
    const controller = createBrandProfileWorkbenchController({
      workspaceId: "brand-workspace",
      brand: "Northstar Labs",
    });
    const coordinator = new BrandProfileCommandCoordinator({
      api,
      brand: "Northstar Labs",
      controller,
      storage: () => storage,
      workspaceId: "brand-workspace",
    });

    await expect(
      coordinator.recover(command, {
        administrator: true,
        listedProfiles: [],
        signal: new AbortController().signal,
      }),
    ).rejects.toBeInstanceOf(BrandProfileLocalReconciliationError);
    expect(replayed).toBe(false);
    await expect(
      readPendingBrandProfileCommand(storage, {
        workspaceId: "brand-workspace",
        brand: "Northstar Labs",
      }),
    ).resolves.toEqual({ kind: "valid", command });
  });
});
