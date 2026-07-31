import type {
  BrandProfileDraftV1,
  BrandProfileResponseV1,
  BrandProfileValidationResponseV1,
  BrandProfileVersionListResponseV1,
  BrandProfileVersionResponseV1,
} from "./generated/catalog-api";
import { BRAND_PROFILE_SAFE_PAGE_SIZE } from "./brand-profile-transport-limits";

export const BRAND_PROFILE_HISTORY_PAGE_SIZE =
  BRAND_PROFILE_SAFE_PAGE_SIZE;
export const BRAND_PROFILE_VALIDATION_FRESHNESS_MS = 60_000;

export type BrandProfileControllerIdentity = {
  workspaceId: string;
  brand: string;
};

type ReadToken = {
  identityGeneration: number;
  readGeneration: number;
};

type ProfileReadToken = ReadToken & {
  profileId: string;
};

type MutationToken = ReadToken & {
  profileId: string | null;
  profileKey: string;
  baselineProfileId: string | null;
  baselineProfileVersion: number | null;
  expectedVersion: number;
  attemptedDraft: BrandProfileDraftV1;
};

type MutationCommand =
  | {
      profileId: null;
      profileKey: string;
      expectedVersion: 0;
      draft: BrandProfileDraftV1;
    }
  | {
      profileId: string;
      profileKey: string;
      expectedVersion: number;
      draft: BrandProfileDraftV1;
    };

type ValidationToken = ReadToken & {
  profileId: string;
  profileVersion: number;
};

type HistoryReadToken = ReadToken & {
  profileId: string;
  mode: "initial" | "more";
};

type VersionReadToken = ReadToken & {
  profileId: string;
  versionNumber: number;
  focusIntent: boolean;
};

export type BrandProfileConflict = {
  attemptedDraft: BrandProfileDraftV1;
  authoritativeProfile: BrandProfileResponseV1;
};

export type BrandProfileControllerSnapshot = {
  identity: BrandProfileControllerIdentity;
  identityGeneration: number;
  profile: BrandProfileResponseV1 | null;
  draft: BrandProfileDraftV1 | null;
  dirty: boolean;
  validation: BrandProfileValidationResponseV1 | null;
  validationExpiresAt: number | null;
  conflict: BrandProfileConflict | null;
  versions: BrandProfileVersionResponseV1[];
  versionsNextCursor: string | null;
  historyLoading: "initial" | "more" | null;
  historyStatus: "unloaded" | "loading" | "ready" | "error";
  selectedVersion: BrandProfileVersionResponseV1 | null;
  selectedVersionFocusNonce: number;
};

function cloneDraft(draft: BrandProfileDraftV1): BrandProfileDraftV1 {
  return {
    rules: draft.rules.map((rule) => ({ ...rule })),
    approved_colors: draft.approved_colors.map((color) => ({ ...color })),
    required_marks: [...draft.required_marks],
    prohibited_elements: [...draft.prohibited_elements],
    tone_constraints: [...draft.tone_constraints],
    copy_constraints: [...draft.copy_constraints],
    purpose: draft.purpose,
    provider: draft.provider,
    requires_derivative: draft.requires_derivative,
    selected_assets: draft.selected_assets.map((selection) => ({
      ...selection,
    })),
  };
}

function draftsEqual(
  left: BrandProfileDraftV1,
  right: BrandProfileDraftV1,
): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function initialSnapshot(
  identity: BrandProfileControllerIdentity,
  identityGeneration: number,
): BrandProfileControllerSnapshot {
  return {
    identity,
    identityGeneration,
    profile: null,
    draft: null,
    dirty: false,
    validation: null,
    validationExpiresAt: null,
    conflict: null,
    versions: [],
    versionsNextCursor: null,
    historyLoading: null,
    historyStatus: "unloaded",
    selectedVersion: null,
    selectedVersionFocusNonce: 0,
  };
}

function profileMatchesIdentity(
  profile: BrandProfileResponseV1,
  identity: BrandProfileControllerIdentity,
  profileId?: string | null,
): boolean {
  return (
    profile.workspace_id === identity.workspaceId &&
    profile.brand === identity.brand &&
    (profileId == null || profile.id === profileId)
  );
}

export class BrandProfileWorkbenchController {
  private snapshot: BrandProfileControllerSnapshot;
  private readonly listeners = new Set<() => void>();
  private profileReadGeneration = 0;
  private mutationGeneration = 0;
  private validationGeneration = 0;
  private historyReadGeneration = 0;
  private versionReadGeneration = 0;

  constructor(identity: BrandProfileControllerIdentity) {
    this.snapshot = initialSnapshot(identity, 1);
  }

  getSnapshot = (): BrandProfileControllerSnapshot => this.snapshot;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  private updateSnapshot(next: BrandProfileControllerSnapshot): void {
    if (next === this.snapshot) return;
    this.snapshot = next;
    for (const listener of this.listeners) listener();
  }

  changeIdentity(identity: BrandProfileControllerIdentity): boolean {
    if (
      identity.workspaceId === this.snapshot.identity.workspaceId &&
      identity.brand === this.snapshot.identity.brand
    ) {
      return false;
    }
    this.profileReadGeneration += 1;
    this.mutationGeneration += 1;
    this.validationGeneration += 1;
    this.historyReadGeneration += 1;
    this.versionReadGeneration += 1;
    this.updateSnapshot(
      initialSnapshot(
        identity,
        this.snapshot.identityGeneration + 1,
      ),
    );
    return true;
  }

  beginProfileRead(profileId: string): ProfileReadToken {
    this.profileReadGeneration += 1;
    return {
      identityGeneration: this.snapshot.identityGeneration,
      readGeneration: this.profileReadGeneration,
      profileId,
    };
  }

  publishProfile(
    token: ProfileReadToken,
    profile: BrandProfileResponseV1,
    resetDraft = true,
  ): boolean {
    const current = this.snapshot.profile;
    if (
      token.identityGeneration !== this.snapshot.identityGeneration ||
      token.readGeneration !== this.profileReadGeneration ||
      !profileMatchesIdentity(profile, this.snapshot.identity, token.profileId) ||
      (!resetDraft && current?.id !== profile.id) ||
      (current?.id === profile.id && profile.version < current.version)
    ) {
      return false;
    }
    if (
      current?.id === profile.id &&
      current.version === profile.version &&
      !draftsEqual(current.draft, profile.draft)
    ) {
      return false;
    }
    const profileChanged = current?.id !== profile.id;
    const preserveLocalDraft =
      !resetDraft &&
      !profileChanged &&
      (this.snapshot.dirty || this.snapshot.conflict !== null);
    const serverAdvancedWithLocalDraft =
      preserveLocalDraft &&
      current !== null &&
      profile.version > current.version &&
      this.snapshot.draft !== null;
    this.updateSnapshot({
      ...this.snapshot,
      profile,
      ...(!preserveLocalDraft
        ? { draft: cloneDraft(profile.draft), dirty: false, conflict: null }
        : serverAdvancedWithLocalDraft
          ? {
              conflict: {
                attemptedDraft: cloneDraft(this.snapshot.draft!),
                authoritativeProfile: profile,
              },
            }
          : {}),
      validation:
        current?.id === profile.id && current.version === profile.version
          ? this.snapshot.validation
          : null,
      validationExpiresAt:
        current?.id === profile.id && current.version === profile.version
          ? this.snapshot.validationExpiresAt
          : null,
      ...(profileChanged
        ? {
            versions: [],
            versionsNextCursor: null,
            historyLoading: null,
            historyStatus: "unloaded" as const,
            selectedVersion: null,
            selectedVersionFocusNonce: 0,
          }
        : {}),
    });
    return true;
  }

  editDraft(draft: BrandProfileDraftV1): void {
    this.updateSnapshot({
      ...this.snapshot,
      draft: cloneDraft(draft),
      dirty: true,
      validation: null,
      validationExpiresAt: null,
    });
  }

  beginMutation(command: MutationCommand): MutationToken {
    const { profileId, profileKey, expectedVersion, draft } = command;
    this.mutationGeneration += 1;
    this.validationGeneration += 1;
    this.updateSnapshot({
      ...this.snapshot,
      validation: null,
      validationExpiresAt: null,
    });
    return {
      identityGeneration: this.snapshot.identityGeneration,
      readGeneration: this.mutationGeneration,
      profileId,
      profileKey,
      baselineProfileId: this.snapshot.profile?.id ?? null,
      baselineProfileVersion: this.snapshot.profile?.version ?? null,
      expectedVersion,
      attemptedDraft: cloneDraft(draft),
    };
  }

  publishMutation(
    token: MutationToken,
    profile: BrandProfileResponseV1,
  ): boolean {
    const current = this.snapshot.profile;
    if (
      token.identityGeneration !== this.snapshot.identityGeneration ||
      token.readGeneration !== this.mutationGeneration ||
      !profileMatchesIdentity(profile, this.snapshot.identity, token.profileId) ||
      profile.profile_key !== token.profileKey ||
      profile.version !== token.expectedVersion + 1 ||
      !draftsEqual(profile.draft, token.attemptedDraft) ||
      (token.profileId === null
        ? (current?.id ?? null) !== token.baselineProfileId ||
          (current?.version ?? null) !== token.baselineProfileVersion ||
          token.expectedVersion !== 0
        : current?.id !== token.profileId ||
          current.version !== token.expectedVersion ||
          current.profile_key !== token.profileKey)
    ) {
      return false;
    }
    const profileChanged = current?.id !== profile.id;
    this.profileReadGeneration += 1;
    this.updateSnapshot({
      ...this.snapshot,
      profile,
      draft: cloneDraft(profile.draft),
      dirty: false,
      validation: null,
      validationExpiresAt: null,
      conflict: null,
      ...(profileChanged
        ? {
            versions: [],
            versionsNextCursor: null,
            historyLoading: null,
            historyStatus: "unloaded" as const,
            selectedVersion: null,
            selectedVersionFocusNonce: 0,
          }
        : {}),
    });
    return true;
  }

  recordVersionConflict(
    token: MutationToken,
    authoritativeProfile: BrandProfileResponseV1,
  ): boolean {
    if (
      token.identityGeneration !== this.snapshot.identityGeneration ||
      token.readGeneration !== this.mutationGeneration ||
      token.profileId === null ||
      !profileMatchesIdentity(
        authoritativeProfile,
        this.snapshot.identity,
        token.profileId,
      ) ||
      authoritativeProfile.profile_key !== token.profileKey ||
      this.snapshot.profile?.profile_key !== token.profileKey ||
      authoritativeProfile.version <= token.expectedVersion
    ) {
      return false;
    }
    this.profileReadGeneration += 1;
    this.validationGeneration += 1;
    this.updateSnapshot({
      ...this.snapshot,
      profile: authoritativeProfile,
      draft: cloneDraft(authoritativeProfile.draft),
      dirty: false,
      validation: null,
      validationExpiresAt: null,
      conflict: {
        attemptedDraft: cloneDraft(token.attemptedDraft),
        authoritativeProfile,
      },
    });
    return true;
  }

  restoreConflictDraft(): boolean {
    const conflict = this.snapshot.conflict;
    if (!conflict) return false;
    this.updateSnapshot({
      ...this.snapshot,
      draft: cloneDraft(conflict.attemptedDraft),
      dirty: true,
      validation: null,
      validationExpiresAt: null,
      conflict: null,
    });
    return true;
  }

  discardConflictDraft(): boolean {
    if (!this.snapshot.conflict || !this.snapshot.profile) return false;
    return this.discardLocalChanges();
  }

  discardLocalChanges(): boolean {
    if (!this.snapshot.profile) return false;
    this.updateSnapshot({
      ...this.snapshot,
      draft: cloneDraft(this.snapshot.profile.draft),
      dirty: false,
      validation: null,
      validationExpiresAt: null,
      conflict: null,
    });
    return true;
  }

  beginValidation(
    profileId: string,
    profileVersion: number,
  ): ValidationToken {
    this.validationGeneration += 1;
    this.updateSnapshot({
      ...this.snapshot,
      validation: null,
      validationExpiresAt: null,
    });
    return {
      identityGeneration: this.snapshot.identityGeneration,
      readGeneration: this.validationGeneration,
      profileId,
      profileVersion,
    };
  }

  publishValidation(
    token: ValidationToken,
    validation: BrandProfileValidationResponseV1,
    receivedAtMs = Date.now(),
  ): boolean {
    if (
      token.identityGeneration !== this.snapshot.identityGeneration ||
      token.readGeneration !== this.validationGeneration ||
      this.snapshot.profile?.id !== token.profileId ||
      this.snapshot.profile.version !== token.profileVersion ||
      validation.profile_id !== token.profileId ||
      validation.profile_version !== token.profileVersion ||
      !Number.isFinite(receivedAtMs)
    ) {
      return false;
    }
    this.updateSnapshot({
      ...this.snapshot,
      validation,
      validationExpiresAt:
        receivedAtMs + BRAND_PROFILE_VALIDATION_FRESHNESS_MS,
    });
    return true;
  }

  invalidateValidation(): void {
    this.validationGeneration += 1;
    if (
      this.snapshot.validation !== null ||
      this.snapshot.validationExpiresAt !== null
    ) {
      this.updateSnapshot({
        ...this.snapshot,
        validation: null,
        validationExpiresAt: null,
      });
    }
  }

  expireValidation(nowMs = Date.now()): boolean {
    if (
      this.snapshot.validation === null ||
      this.snapshot.validationExpiresAt === null ||
      nowMs < this.snapshot.validationExpiresAt
    ) {
      return false;
    }
    this.invalidateValidation();
    return true;
  }

  beginHistoryRead(
    mode: "initial" | "more",
    profileId: string,
  ): HistoryReadToken {
    this.historyReadGeneration += 1;
    this.updateSnapshot({
      ...this.snapshot,
      historyLoading: mode,
      historyStatus: "loading",
    });
    return {
      identityGeneration: this.snapshot.identityGeneration,
      readGeneration: this.historyReadGeneration,
      profileId,
      mode,
    };
  }

  publishHistory(
    token: HistoryReadToken,
    page: BrandProfileVersionListResponseV1,
  ): boolean {
    if (
      token.identityGeneration !== this.snapshot.identityGeneration ||
      token.readGeneration !== this.historyReadGeneration ||
      this.snapshot.profile?.id !== token.profileId
    ) {
      return false;
    }
    const existing = token.mode === "more" ? this.snapshot.versions : [];
    const existingNumbers = new Set(
      existing.map((version) => version.version_number),
    );
    const existingTail =
      existing.length === 0
        ? undefined
        : existing[existing.length - 1].version_number;
    if (
      page.items.length > BRAND_PROFILE_HISTORY_PAGE_SIZE ||
      page.items.some(
        (version) =>
          version.workspace_id !== this.snapshot.identity.workspaceId ||
          version.profile_id !== token.profileId,
      ) ||
      page.items.some(
        (version, index) =>
          index > 0 &&
          page.items[index - 1].version_number <= version.version_number,
      ) ||
      (existingTail !== undefined &&
        page.items.some(
          (version) =>
            !existingNumbers.has(version.version_number) &&
            version.version_number >= existingTail,
        ))
    ) {
      this.updateSnapshot({
        ...this.snapshot,
        historyLoading: null,
        historyStatus: "error",
      });
      return false;
    }
    const versions = [...existing];
    const versionNumbers = new Set(existingNumbers);
    for (const version of page.items) {
      if (versionNumbers.has(version.version_number)) continue;
      versionNumbers.add(version.version_number);
      versions.push(version);
    }
    this.updateSnapshot({
      ...this.snapshot,
      versions,
      versionsNextCursor: page.next_cursor ?? null,
      historyLoading: null,
      historyStatus: "ready",
    });
    return true;
  }

  failHistory(token: HistoryReadToken): boolean {
    if (
      token.identityGeneration !== this.snapshot.identityGeneration ||
      token.readGeneration !== this.historyReadGeneration ||
      this.snapshot.profile?.id !== token.profileId
    ) {
      return false;
    }
    this.updateSnapshot({
      ...this.snapshot,
      historyLoading: null,
      historyStatus: "error",
    });
    return true;
  }

  beginVersionRead(
    profileId: string,
    versionNumber: number,
    focusIntent = false,
  ): VersionReadToken {
    this.versionReadGeneration += 1;
    return {
      identityGeneration: this.snapshot.identityGeneration,
      readGeneration: this.versionReadGeneration,
      profileId,
      versionNumber,
      focusIntent,
    };
  }

  publishVersion(
    token: VersionReadToken,
    version: BrandProfileVersionResponseV1,
  ): boolean {
    if (
      token.identityGeneration !== this.snapshot.identityGeneration ||
      token.readGeneration !== this.versionReadGeneration ||
      this.snapshot.profile?.id !== token.profileId ||
      version.workspace_id !== this.snapshot.identity.workspaceId ||
      version.profile_id !== token.profileId ||
      version.version_number !== token.versionNumber
    ) {
      return false;
    }
    this.updateSnapshot({
      ...this.snapshot,
      selectedVersion: version,
      selectedVersionFocusNonce: token.focusIntent
        ? this.snapshot.selectedVersionFocusNonce + 1
        : this.snapshot.selectedVersionFocusNonce,
    });
    return true;
  }
}

export function createBrandProfileWorkbenchController(
  identity: BrandProfileControllerIdentity,
): BrandProfileWorkbenchController {
  return new BrandProfileWorkbenchController(identity);
}
