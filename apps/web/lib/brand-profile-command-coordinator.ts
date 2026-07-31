import {
  BrandProfileApi,
  BrandProfileApiCancelledError,
  BrandProfileApiError,
} from "./brand-profile-api";
import {
  canonicalizeBrandProfileCommandPayload,
  clearPendingBrandProfileCommand,
  createPendingBrandProfileCommand,
  type PendingBrandProfileCommand,
  type PendingBrandProfileCommandReadResult,
  readPendingBrandProfileCommand,
  savePendingBrandProfileCommand,
} from "./brand-profile-pending-command";
import type { BrandProfileWorkbenchController } from "./brand-profile-workbench-controller";
import type {
  BrandProfileCreateRequestV1,
  BrandProfileDraftV1,
  BrandProfilePublishRequestV1,
  BrandProfileResponseV1,
  BrandProfileUpdateDraftRequestV1,
} from "./generated/catalog-api";

type StorageProvider = () => Storage;

export type BrandProfileRecoveryResult = {
  kind: "resolved" | "blocked" | "pending";
  message: string;
  profile: BrandProfileResponseV1 | null;
  empty: boolean;
  pendingRetained: boolean;
  discardAllowed?: boolean;
  creationConflict?: {
    profileKey: string;
    draft: BrandProfileDraftV1;
    message: string;
  };
};

export class BrandProfileLocalReconciliationError extends Error {
  readonly reconciliationCause: unknown;

  constructor(message: string, reconciliationCause?: unknown) {
    super(message);
    this.name = "BrandProfileLocalReconciliationError";
    this.reconciliationCause = reconciliationCause;
  }
}

export type BrandProfileCommandFailure =
  | { kind: "uncertain" }
  | {
      kind: "deterministic-rejected";
      requiresExplicitDiscard: boolean;
    }
  | { kind: "local-reconciliation-failure" };

export function isUncertainBrandProfileCommandOutcome(
  error: unknown,
): boolean {
  return (
    error instanceof BrandProfileApiCancelledError ||
    (error instanceof BrandProfileApiError &&
      (error.envelope?.retryable === true ||
        error.status === 408 ||
        error.status === 429 ||
        error.status >= 500))
  );
}

export function classifyBrandProfileCommandFailure(
  error: unknown,
): BrandProfileCommandFailure {
  if (error instanceof BrandProfileLocalReconciliationError) {
    return { kind: "local-reconciliation-failure" };
  }
  if (isUncertainBrandProfileCommandOutcome(error)) {
    return { kind: "uncertain" };
  }
  if (error instanceof BrandProfileApiError) {
    return {
      kind: "deterministic-rejected",
      requiresExplicitDiscard: error.status === 409,
    };
  }
  return { kind: "local-reconciliation-failure" };
}

export function isBrandProfileAuthorityLoss(error: unknown): boolean {
  if (
    error instanceof BrandProfileApiError &&
    (error.status === 401 || error.status === 403)
  ) {
    return true;
  }
  return (
    error instanceof BrandProfileLocalReconciliationError &&
    error.reconciliationCause !== error &&
    isBrandProfileAuthorityLoss(error.reconciliationCause)
  );
}

async function payloadMatches(
  command: PendingBrandProfileCommand,
  payload:
    | BrandProfileCreateRequestV1
    | BrandProfilePublishRequestV1
    | BrandProfileUpdateDraftRequestV1,
): Promise<boolean> {
  const fingerprint =
    await canonicalizeBrandProfileCommandPayload(payload);
  return fingerprint.payloadSha256 === command.payload_sha256;
}

async function draftsMatch(
  expectedVersion: number,
  first: BrandProfileDraftV1,
  second: BrandProfileDraftV1,
): Promise<boolean> {
  const [firstFingerprint, secondFingerprint] = await Promise.all([
    canonicalizeBrandProfileCommandPayload({
      expected_version: expectedVersion,
      draft: first,
    }),
    canonicalizeBrandProfileCommandPayload({
      expected_version: expectedVersion,
      draft: second,
    }),
  ]);
  return (
    firstFingerprint.payloadSha256 === secondFingerprint.payloadSha256
  );
}

export class BrandProfileCommandCoordinator {
  private readonly api: BrandProfileApi;
  private readonly brand: string;
  private readonly controller: BrandProfileWorkbenchController;
  private readonly storage: StorageProvider;
  private readonly workspaceId: string;

  constructor({
    api,
    brand,
    controller,
    storage,
    workspaceId,
  }: {
    api: BrandProfileApi;
    brand: string;
    controller: BrandProfileWorkbenchController;
    storage: StorageProvider;
    workspaceId: string;
  }) {
    this.api = api;
    this.brand = brand;
    this.controller = controller;
    this.storage = storage;
    this.workspaceId = workspaceId;
  }

  readPending(): Promise<PendingBrandProfileCommandReadResult> {
    return readPendingBrandProfileCommand(this.storage(), {
      workspaceId: this.workspaceId,
      brand: this.brand,
    });
  }

  async persistPending(
    input: Parameters<typeof createPendingBrandProfileCommand>[0],
  ): Promise<PendingBrandProfileCommand> {
    const command = await createPendingBrandProfileCommand(input);
    await savePendingBrandProfileCommand(this.storage(), command);
    return command;
  }

  clearPending(command: PendingBrandProfileCommand): Promise<boolean> {
    return clearPendingBrandProfileCommand(this.storage(), command);
  }

  private async requirePendingClear(
    command: PendingBrandProfileCommand,
  ): Promise<void> {
    if (!(await this.clearPending(command))) {
      throw new BrandProfileLocalReconciliationError(
        "Recovered Brand Profile command no longer matches its pending record.",
      );
    }
  }

  private publishRead(profile: BrandProfileResponseV1): void {
    const token = this.controller.beginProfileRead(profile.id);
    if (!this.controller.publishProfile(token, profile)) {
      throw new BrandProfileLocalReconciliationError(
        "Recovered profile does not match the current Brand Profile context.",
      );
    }
  }

  private async resolved(
    command: PendingBrandProfileCommand,
    message: string,
    profile: BrandProfileResponseV1,
  ): Promise<BrandProfileRecoveryResult> {
    await this.requirePendingClear(command);
    return {
      kind: "resolved",
      message,
      profile,
      empty: false,
      pendingRetained: false,
    };
  }

  private retained(
    profile: BrandProfileResponseV1 | null,
  ): BrandProfileRecoveryResult {
    return {
      kind: "blocked",
      message:
        "检测到待对账写命令，但当前会话没有管理员能力；命令已保留，管理员会话可继续恢复。",
      profile,
      empty: profile === null,
      pendingRetained: true,
    };
  }

  private conflict(
    profile: BrandProfileResponseV1 | null,
    message: string,
    creationConflict?: BrandProfileRecoveryResult["creationConflict"],
  ): BrandProfileRecoveryResult {
    return {
      kind: "blocked",
      message,
      profile,
      empty: profile === null,
      pendingRetained: true,
      discardAllowed: true,
      ...(creationConflict ? { creationConflict } : {}),
    };
  }

  private isReplayConflict(error: unknown): boolean {
    return error instanceof BrandProfileApiError && error.status === 409;
  }

  private isAuthorityOrIdentityFailure(error: unknown): boolean {
    return (
      error instanceof BrandProfileApiError &&
      (error.status === 401 ||
        error.status === 403 ||
        error.status === 404)
    );
  }

  private preserveUnsettledAuthority(
    error: unknown,
    message: string,
  ): never {
    throw new BrandProfileLocalReconciliationError(message, error);
  }

  async confirmAcceptedMutation(
    replayed: BrandProfileResponseV1,
    signal: AbortSignal,
    expectedProfileKey: string,
  ): Promise<BrandProfileResponseV1> {
    if (
      replayed.workspace_id !== this.workspaceId ||
      replayed.brand !== this.brand ||
      replayed.profile_key !== expectedProfileKey
    ) {
      throw new BrandProfileLocalReconciliationError(
        "The accepted Brand Profile command result does not match its immutable profile identity.",
      );
    }
    let current: BrandProfileResponseV1;
    try {
      current = await this.api.get(replayed.id, signal);
    } catch (error) {
      throw new BrandProfileLocalReconciliationError(
        "The accepted Brand Profile command could not be confirmed against the current authoritative head.",
        error,
      );
    }
    if (
      current.id !== replayed.id ||
      current.workspace_id !== this.workspaceId ||
      current.brand !== this.brand ||
      current.profile_key !== expectedProfileKey ||
      current.version < replayed.version
    ) {
      throw new BrandProfileLocalReconciliationError(
        "The current authoritative Brand Profile head does not match the accepted command identity or version.",
      );
    }
    this.publishRead(current);
    return current;
  }

  async recover(
    command: PendingBrandProfileCommand,
    {
      administrator,
      listedProfiles,
      signal,
    }: {
      administrator: boolean;
      listedProfiles: BrandProfileResponseV1[];
      signal: AbortSignal;
    },
  ): Promise<BrandProfileRecoveryResult> {
    if (command.action === "CREATE") {
      return this.recoverCreate(
        command,
        listedProfiles,
        administrator,
        signal,
      );
    }
    return this.recoverExisting(
      command,
      administrator,
      signal,
    );
  }

  private async recoverCreate(
    command: PendingBrandProfileCommand,
    listedProfiles: BrandProfileResponseV1[],
    administrator: boolean,
    signal: AbortSignal,
  ): Promise<BrandProfileRecoveryResult> {
    const payload = command.payload as BrandProfileCreateRequestV1;
    const existing = listedProfiles.find(
      (profile) => profile.profile_key === command.profile_key,
    );
    const fallback = existing ?? listedProfiles[0] ?? null;
    if (fallback) this.publishRead(fallback);
    if (!administrator) return this.retained(fallback);

    let created: BrandProfileResponseV1;
    try {
      created = await this.api.create(
        payload,
        command.idempotency_key,
        signal,
      );
    } catch (error) {
      if (this.isAuthorityOrIdentityFailure(error)) {
        this.preserveUnsettledAuthority(
          error,
          "The pending Brand Profile create command could not establish current mutation authority.",
        );
      }
      if (!this.isReplayConflict(error)) throw error;
      const message =
        "原创建命令以原幂等键重放后发生确定性冲突；现有档案不能证明属于该命令。记录继续保留，需人工核对后明确放弃。";
      return this.conflict(fallback, message, {
        profileKey: command.profile_key,
        draft: command.attempted_draft,
        message,
      });
    }
    if (
      created.version !== 1 ||
      !(await payloadMatches(command, {
        brand: created.brand,
        profile_key: created.profile_key,
        draft: created.draft,
      }))
    ) {
      return this.conflict(
        fallback,
        "原创建幂等键返回了不符合待恢复命令的结果；记录继续保留，禁止自动归属。",
      );
    }
    const current = await this.confirmAcceptedMutation(
      created,
      signal,
      command.profile_key,
    );
    return this.resolved(
      command,
      "已使用原幂等键恢复创建命令并确认完成。",
      current,
    );
  }

  private async recoverExisting(
    command: PendingBrandProfileCommand,
    administrator: boolean,
    signal: AbortSignal,
  ): Promise<BrandProfileRecoveryResult> {
    const profileId = command.profile_id;
    if (!profileId) {
      throw new Error("待恢复命令缺少品牌档案标识。");
    }
    let authoritative: BrandProfileResponseV1;
    try {
      authoritative = await this.api.get(profileId, signal);
    } catch (error) {
      this.preserveUnsettledAuthority(
        error,
        "The pending Brand Profile command could not read its authoritative profile during recovery preflight.",
      );
    }
    if (
      authoritative.id !== profileId ||
      authoritative.workspace_id !== this.workspaceId ||
      authoritative.brand !== this.brand ||
      authoritative.profile_key !== command.profile_key
    ) {
      throw new BrandProfileLocalReconciliationError(
        "The pending Brand Profile command identity does not match the authoritative recovery profile.",
      );
    }
    this.publishRead(authoritative);
    if (!administrator) return this.retained(authoritative);

    if (command.action === "UPDATE_DRAFT") {
      return this.recoverUpdate(
        command,
        authoritative,
        signal,
      );
    }
    return this.recoverPublish(
      command,
      authoritative,
      signal,
    );
  }

  private async recoverUpdate(
    command: PendingBrandProfileCommand,
    authoritative: BrandProfileResponseV1,
    signal: AbortSignal,
  ): Promise<BrandProfileRecoveryResult> {
    const payload =
      command.payload as BrandProfileUpdateDraftRequestV1;
    let updated: BrandProfileResponseV1;
    try {
      updated = await this.api.updateDraft(
        authoritative.id,
        payload,
        command.idempotency_key,
        signal,
      );
    } catch (error) {
      if (this.isAuthorityOrIdentityFailure(error)) {
        this.preserveUnsettledAuthority(
          error,
          "The pending Brand Profile draft command could not establish current mutation authority.",
        );
      }
      if (!this.isReplayConflict(error)) throw error;
      this.controller.editDraft(command.attempted_draft);
      return this.conflict(
        authoritative,
        "原草稿命令以原幂等键重放后发生版本冲突；相同服务端内容不能证明命令归属。原始草稿与记录均已保留，请人工核对后明确放弃或恢复。",
      );
    }
    if (
      updated.version !== command.expected_version + 1 ||
      updated.workspace_id !== this.workspaceId ||
      updated.brand !== this.brand ||
      updated.profile_key !== command.profile_key ||
      !(await payloadMatches(command, {
        expected_version: command.expected_version,
        draft: updated.draft,
      }))
    ) {
      return this.conflict(
        authoritative,
        "原草稿幂等键返回了不符合待恢复命令的结果；记录继续保留，禁止自动归属。",
      );
    }
    const current = await this.confirmAcceptedMutation(
      updated,
      signal,
      command.profile_key,
    );
    return this.resolved(
      command,
      "已使用原幂等键恢复草稿保存并确认完成。",
      current,
    );
  }

  private async recoverPublish(
    command: PendingBrandProfileCommand,
    authoritative: BrandProfileResponseV1,
    signal: AbortSignal,
  ): Promise<BrandProfileRecoveryResult> {
    const payload = command.payload as BrandProfilePublishRequestV1;
    let published: BrandProfileResponseV1;
    try {
      published = await this.api.publish(
        authoritative.id,
        payload,
        command.idempotency_key,
        signal,
      );
    } catch (error) {
      if (this.isAuthorityOrIdentityFailure(error)) {
        this.preserveUnsettledAuthority(
          error,
          "The pending Brand Profile publish command could not establish current mutation authority.",
        );
      }
      if (!this.isReplayConflict(error)) throw error;
      this.controller.editDraft(command.attempted_draft);
      return this.conflict(
        authoritative,
        "原发布命令以原幂等键重放后发生版本冲突；相邻发布序号不能证明命令归属。记录继续保留，需人工核对后明确放弃。",
      );
    }
    if (
      published.version !== command.expected_version + 1 ||
      published.workspace_id !== this.workspaceId ||
      published.brand !== this.brand ||
      published.profile_key !== command.profile_key ||
      published.current_version_number !==
        command.expected_publication_version + 1 ||
      !(await draftsMatch(
        command.expected_version,
        published.draft,
        command.attempted_draft,
      ))
    ) {
      return this.conflict(
        authoritative,
        "原发布幂等键返回了不符合待恢复命令的结果；记录继续保留，禁止自动归属。",
      );
    }
    const current = await this.confirmAcceptedMutation(
      published,
      signal,
      command.profile_key,
    );
    return this.resolved(
      command,
      "已使用原幂等键恢复发布命令并确认完成。",
      current,
    );
  }
}
