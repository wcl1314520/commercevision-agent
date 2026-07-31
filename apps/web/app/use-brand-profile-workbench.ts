"use client";

import {
  type FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";

import {
  BrandProfileApi,
  BrandProfileApiCancelledError,
  BrandProfileApiError,
  newBrandProfileIdempotencyKey,
} from "../lib/brand-profile-api";
import {
  BrandProfileCommandCoordinator,
  BrandProfileLocalReconciliationError,
  classifyBrandProfileCommandFailure,
  isBrandProfileAuthorityLoss,
} from "../lib/brand-profile-command-coordinator";
import type { PendingBrandProfileCommand } from "../lib/brand-profile-pending-command";
import {
  BRAND_PROFILE_HISTORY_PAGE_SIZE,
  BrandProfileWorkbenchController,
  createBrandProfileWorkbenchController,
} from "../lib/brand-profile-workbench-controller";
import { BRAND_PROFILE_SAFE_PAGE_SIZE } from "../lib/brand-profile-transport-limits";
import {
  brandProfileIdentityChangeGuard,
  nextUniqueProfileKey,
} from "../lib/brand-profile-editor-state";
import type {
  BrandProfileCreateRequestV1,
  BrandProfileDraftV1,
  BrandProfilePublishRequestV1,
  BrandProfileResponseV1,
  BrandProfileUpdateDraftRequestV1,
} from "../lib/generated/catalog-api";
import { emptyBrandProfileDraft } from "./brand-profile-draft-editor";
import {
  type BrandProfileReconciliationNotice,
  type BrandProfileWorkbenchStatus,
} from "./brand-profile-workbench-view";

const PROFILE_LIST_PAGE_SIZE = BRAND_PROFILE_SAFE_PAGE_SIZE;

type BrandProfileMutationToken = Parameters<
  BrandProfileWorkbenchController["recordVersionConflict"]
>[0];

type PendingCommandAuthority =
  | { kind: "none" }
  | { kind: "unverifiable" }
  | {
      kind: "valid";
      command: PendingBrandProfileCommand;
      discardAllowed: boolean;
    };

type PendingIdentityChange =
  | { kind: "switch-profile"; profileId: string }
  | { kind: "start-create" }
  | { kind: "cancel-create" };

function profileError(error: unknown): string {
  if (error instanceof BrandProfileApiError) {
    if (error.envelope?.code === "VERSION_CONFLICT") {
      return "服务器上的品牌档案已更新；本地草稿仍保留，请选择恢复或丢弃。";
    }
    if (error.envelope?.code === "IDEMPOTENCY_CONFLICT") {
      return "该重复请求键已用于另一份命令，请刷新后重试。";
    }
    if (error.status === 403) {
      return "当前 Workspace 没有品牌管理员权限。";
    }
    return error.envelope?.message ?? "品牌档案请求失败。";
  }
  return error instanceof Error ? error.message : "品牌档案请求失败。";
}

function replaceProfile(
  profiles: BrandProfileResponseV1[],
  profile: BrandProfileResponseV1,
): BrandProfileResponseV1[] {
  const next = profiles.filter((item) => item.id !== profile.id);
  next.push(profile);
  return next.sort((left, right) =>
    left.profile_key.localeCompare(right.profile_key),
  );
}

function sessionCommandStorage(): Storage {
  try {
    return window.sessionStorage;
  } catch {
    throw new Error(
      "浏览器会话存储不可用；为避免失去命令对账依据，本次写操作没有发送。",
    );
  }
}

export function useBrandProfileWorkbench({
  brand,
  workspaceId = "catalog-demo",
}: {
  brand: string;
  workspaceId?: string;
}) {
  const api = useMemo(
    () => new BrandProfileApi({ workspaceId }),
    [workspaceId],
  );
  const controllerRef = useRef<BrandProfileWorkbenchController | null>(
    null,
  );
  if (controllerRef.current === null) {
    controllerRef.current = createBrandProfileWorkbenchController({
      workspaceId,
      brand,
    });
  }
  const controller = controllerRef.current;
  const commandCoordinator = useMemo(
    () =>
      new BrandProfileCommandCoordinator({
        api,
        brand,
        controller,
        storage: sessionCommandStorage,
        workspaceId,
      }),
    [api, brand, controller, workspaceId],
  );
  const requestControllers = useRef(new Set<AbortController>());
  const snapshot = useSyncExternalStore(
    controller.subscribe,
    controller.getSnapshot,
    controller.getSnapshot,
  );
  const [profiles, setProfiles] = useState<BrandProfileResponseV1[]>([]);
  const [profilesNextCursor, setProfilesNextCursor] = useState<string | null>(
    null,
  );
  const [canAdminister, setCanAdminister] = useState(false);
  const [capabilityDegraded, setCapabilityDegraded] = useState(false);
  const [status, setStatus] =
    useState<BrandProfileWorkbenchStatus>("loading");
  const [busy, setBusy] = useState<string | null>("loading");
  const [profilesLoading, setProfilesLoading] = useState(false);
  const [versionLoading, setVersionLoading] = useState<number | null>(
    null,
  );
  const [pendingAuthority, setPendingAuthority] =
    useState<PendingCommandAuthority>({ kind: "none" });
  const pendingCommand = pendingAuthority.kind !== "none";
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] =
    useState<BrandProfileReconciliationNotice | null>(null);
  const [profileKey, setProfileKey] = useState("primary");
  const [creationDraft, setCreationDraft] = useState<BrandProfileDraftV1>(
    emptyBrandProfileDraft,
  );
  const [creatingAnother, setCreatingAnother] = useState(false);
  const [pendingIdentityChange, setPendingIdentityChange] =
    useState<PendingIdentityChange | null>(null);

  const beginRequest = useCallback(() => {
    const request = new AbortController();
    requestControllers.current.add(request);
    return request;
  }, []);

  const finishRequest = useCallback((request: AbortController) => {
    requestControllers.current.delete(request);
  }, []);

  const abortRequests = useCallback(() => {
    for (const request of requestControllers.current) request.abort();
    requestControllers.current.clear();
  }, []);

  const failClosedOnAuthorityLoss = useCallback(
    (requestError: unknown): boolean => {
      if (!isBrandProfileAuthorityLoss(requestError)) return false;
      setCanAdminister(false);
      setCapabilityDegraded(false);
      controller.invalidateValidation();
      return true;
    },
    [controller],
  );

  const clearPending = useCallback(
    async (command: PendingBrandProfileCommand): Promise<boolean> => {
      try {
        if (await commandCoordinator.clearPending(command)) {
          setPendingAuthority({ kind: "none" });
          return true;
        }
        setPendingAuthority({
          kind: "valid",
          command,
          discardAllowed: false,
        });
        setNotice({
          kind: "blocked",
          message:
            "命令结果已确定，但本地对账记录与当前命令不一致；写操作保持冻结，请重新载入后核对。",
        });
        return false;
      } catch {
        setPendingAuthority({
          kind: "valid",
          command,
          discardAllowed: false,
        });
        setNotice({
          kind: "blocked",
          message:
            "命令已完成，但浏览器未能清除本地对账记录；下次载入会再次安全核对服务端状态。",
        });
        return false;
      }
    },
    [commandCoordinator],
  );

  const persistPending = useCallback(
    async (
      input: Parameters<
        BrandProfileCommandCoordinator["persistPending"]
      >[0],
    ): Promise<PendingBrandProfileCommand> => {
      const command = await commandCoordinator.persistPending(input);
      setPendingAuthority({
        kind: "valid",
        command,
        discardAllowed: false,
      });
      setNotice({
        kind: "pending",
        message: "命令已持久化；若连接中断，将使用同一幂等键恢复并对账。",
      });
      return command;
    },
    [commandCoordinator],
  );

  const reconcileExplicitDiscardConflict = useCallback(
    async ({
      baseline,
      command,
      signal,
      token,
    }: {
      baseline: BrandProfileResponseV1;
      command: PendingBrandProfileCommand;
      signal: AbortSignal;
      token: BrandProfileMutationToken;
    }): Promise<void> => {
      setPendingAuthority({
        kind: "valid",
        command,
        discardAllowed: false,
      });
      let authoritative: BrandProfileResponseV1;
      try {
        authoritative = await api.get(baseline.id, signal);
      } catch (reconciliationError) {
        throw new BrandProfileLocalReconciliationError(
          "The rejected command could not be reconciled with the current authoritative Brand Profile head.",
          reconciliationError,
        );
      }

      let reconciled = false;
      if (authoritative.version > baseline.version) {
        reconciled = controller.recordVersionConflict(
          token,
          authoritative,
        );
      } else if (authoritative.version === baseline.version) {
        const read = controller.beginProfileRead(authoritative.id);
        reconciled = controller.publishProfile(
          read,
          authoritative,
          false,
        );
      }
      if (!reconciled) {
        throw new BrandProfileLocalReconciliationError(
          "The rejected command response does not match the active Brand Profile identity or version.",
        );
      }

      setProfiles((current) => replaceProfile(current, authoritative));
      setPendingAuthority({
        kind: "valid",
        command,
        discardAllowed: true,
      });
      setNotice({
        kind: "blocked",
        message:
          "该命令已被服务端确定拒绝；权威档案已加载，本地草稿、原始 payload 与幂等键均已保留。请核对后由管理员明确放弃该待对账命令。",
      });
    },
    [api, controller],
  );

  const loadVersion = useCallback(
    async (
      profileId: string,
      versionNumber: number,
      focusIntent = false,
    ) => {
      const request = beginRequest();
      const token = controller.beginVersionRead(
        profileId,
        versionNumber,
        focusIntent,
      );
      setVersionLoading(versionNumber);
      setError(null);
      try {
        const version = await api.getVersion(
          profileId,
          versionNumber,
          request.signal,
        );
        controller.publishVersion(token, version);
      } catch (requestError) {
        failClosedOnAuthorityLoss(requestError);
        if (!(requestError instanceof BrandProfileApiCancelledError)) {
          setError(profileError(requestError));
        }
      } finally {
        finishRequest(request);
        setVersionLoading((current) =>
          current === versionNumber ? null : current,
        );
      }
    },
    [
      api,
      beginRequest,
      controller,
      failClosedOnAuthorityLoss,
      finishRequest,
    ],
  );

  const loadHistory = useCallback(
    async (profileId: string, mode: "initial" | "more") => {
      const request = beginRequest();
      const token = controller.beginHistoryRead(mode, profileId);
      try {
        const page = await api.listVersions(
          profileId,
          {
            cursor:
              mode === "more"
                ? controller.getSnapshot().versionsNextCursor ?? undefined
                : undefined,
            limit: BRAND_PROFILE_HISTORY_PAGE_SIZE,
          },
          request.signal,
        );
        if (!controller.publishHistory(token, page)) return;
        const currentNumber =
          controller.getSnapshot().profile?.current_version_number ?? 0;
        if (mode === "initial" && currentNumber > 0) {
          void loadVersion(profileId, currentNumber);
        }
      } catch (requestError) {
        controller.failHistory(token);
        failClosedOnAuthorityLoss(requestError);
        if (!(requestError instanceof BrandProfileApiCancelledError)) {
          setError(profileError(requestError));
        }
      } finally {
        finishRequest(request);
      }
    },
    [
      api,
      beginRequest,
      controller,
      failClosedOnAuthorityLoss,
      finishRequest,
      loadVersion,
    ],
  );

  const activateProfile = useCallback(
    (
      profile: BrandProfileResponseV1,
      resetDraft = true,
    ): boolean => {
      const token = controller.beginProfileRead(profile.id);
      if (!controller.publishProfile(token, profile, resetDraft)) {
        return false;
      }
      setProfiles((current) => replaceProfile(current, profile));
      setStatus("ready");
      void loadHistory(profile.id, "initial");
      return true;
    },
    [controller, loadHistory],
  );

  const openProfile = useCallback(
    async (
      profileId: string,
      { preserveLocalDraft = false } = {},
    ) => {
      const request = beginRequest();
      const token = controller.beginProfileRead(profileId);
      setBusy("profile");
      setError(null);
      try {
        const profile = await api.get(profileId, request.signal);
        if (
          !controller.publishProfile(
            token,
            profile,
            !preserveLocalDraft,
          )
        ) {
          throw new Error(
            "刷新响应与当前品牌档案上下文不一致，已忽略该响应。",
          );
        }
        setProfiles((current) => replaceProfile(current, profile));
        void loadHistory(profile.id, "initial");
      } catch (requestError) {
        failClosedOnAuthorityLoss(requestError);
        if (!(requestError instanceof BrandProfileApiCancelledError)) {
          setError(profileError(requestError));
        }
      } finally {
        finishRequest(request);
        setBusy((current) => (current === "profile" ? null : current));
      }
    },
    [
      api,
      beginRequest,
      controller,
      failClosedOnAuthorityLoss,
      finishRequest,
      loadHistory,
    ],
  );

  const recoverPendingCommand = useCallback(
    async (
      command: PendingBrandProfileCommand,
      listedProfiles: BrandProfileResponseV1[],
      administrator: boolean,
      signal: AbortSignal,
    ): Promise<void> => {
      setNotice({
        kind: "pending",
        message: "检测到未确认结果的命令，正在按原幂等键核对服务端状态。",
      });

      try {
        const result = await commandCoordinator.recover(command, {
          administrator,
          listedProfiles,
          signal,
        });
        if (result.profile) {
          setProfiles((current) =>
            replaceProfile(current, result.profile!),
          );
          setStatus("ready");
          void loadHistory(result.profile.id, "initial");
        } else if (result.empty) {
          setStatus("empty");
        }
        setPendingAuthority(
          result.pendingRetained
            ? {
                kind: "valid",
                command,
                discardAllowed: result.discardAllowed === true,
              }
            : { kind: "none" },
        );
        if (result.creationConflict) {
          setProfileKey(result.creationConflict.profileKey);
          setCreationDraft(result.creationConflict.draft);
          setCreatingAnother(true);
          setError(result.creationConflict.message);
        }
        setNotice({ kind: result.kind, message: result.message });

      } catch (recoveryError) {
        failClosedOnAuthorityLoss(recoveryError);
        const failure =
          classifyBrandProfileCommandFailure(recoveryError);
        if (failure.kind === "uncertain") {
          setPendingAuthority({
            kind: "valid",
            command,
            discardAllowed: false,
          });
          setNotice({
            kind: "pending",
            message:
              "服务端结果仍无法确认；原命令及幂等键已保留，稍后载入会继续对账。",
          });
          return;
        }
        if (failure.kind === "local-reconciliation-failure") {
          setPendingAuthority({
            kind: "valid",
            command,
            discardAllowed: false,
          });
          setNotice({
            kind: "blocked",
            message:
              "服务端可能已接受命令，但本地状态无法完成权威对账；原始 payload 与幂等键已保留，工作台保持冻结，刷新后将继续恢复。",
          });
          setError(profileError(recoveryError));
          return;
        }
        if (failure.requiresExplicitDiscard) {
          setPendingAuthority({
            kind: "valid",
            command,
            discardAllowed: true,
          });
          setNotice({
            kind: "blocked",
            message:
              "待恢复命令已被服务端确定拒绝；原始 payload 与幂等键继续保留，需由管理员核对后明确放弃。",
          });
          setError(profileError(recoveryError));
          return;
        }
        await clearPending(command);
        setNotice({
          kind: "blocked",
          message:
            "待恢复命令已得到确定性拒绝；已停止自动重试，需要人工修正后重新提交。",
        });
        setError(profileError(recoveryError));
      }
    },
    [
      clearPending,
      commandCoordinator,
      failClosedOnAuthorityLoss,
      loadHistory,
    ],
  );

  useEffect(() => {
    abortRequests();
    controller.changeIdentity({ workspaceId, brand });
    setProfiles([]);
    setProfilesNextCursor(null);
    setCanAdminister(false);
    setCapabilityDegraded(false);
    setStatus("loading");
    setBusy("loading");
    setProfilesLoading(false);
    setVersionLoading(null);
    setPendingAuthority({ kind: "none" });
    setError(null);
    setNotice(null);
    setProfileKey("primary");
    setCreationDraft(emptyBrandProfileDraft());
    setCreatingAnother(false);
    setPendingIdentityChange(null);

    const request = beginRequest();
    const identityGeneration = controller.getSnapshot().identityGeneration;
    void Promise.allSettled([
      api.getWorkspaceCapabilities(request.signal),
      api.list(
        { brand, limit: PROFILE_LIST_PAGE_SIZE },
        request.signal,
      ),
    ])
      .then(async ([capabilitiesResult, pageResult]) => {
        if (
          request.signal.aborted ||
          controller.getSnapshot().identityGeneration !== identityGeneration
        ) {
          return;
        }
        if (pageResult.status === "rejected") {
          throw pageResult.reason;
        }
        const administrator =
          capabilitiesResult.status === "fulfilled" &&
          capabilitiesResult.value.administrator;
        setCanAdminister(administrator);
        setCapabilityDegraded(capabilitiesResult.status === "rejected");
        const page = pageResult.value;
        setProfiles(page.items);
        setProfilesNextCursor(page.next_cursor ?? null);

        let pendingRead:
          | Awaited<ReturnType<typeof commandCoordinator.readPending>>
          | undefined;
        try {
          pendingRead = await commandCoordinator.readPending();
        } catch {
          setPendingAuthority({ kind: "unverifiable" });
          setNotice({
            kind: "blocked",
            message:
              "浏览器会话存储不可用；无法验证待对账命令。所有写入、档案切换与刷新保持冻结，系统不会自动删除或覆盖记录。",
          });
        }
        if (pendingRead?.kind === "unverifiable") {
          setPendingAuthority({ kind: "unverifiable" });
          setNotice({
            kind: "blocked",
            message:
              "浏览器待对账记录损坏、版本不受支持或指纹不匹配；系统无法安全识别其精确命令。所有写入、档案切换与刷新保持冻结，请导出并人工核对该浏览器会话记录后再精确清理。",
          });
          const first = page.items[0];
          if (!first) {
            setStatus("empty");
          } else {
            activateProfile(first);
          }
          return;
        }
        if (pendingRead?.kind === "valid") {
          const pending = pendingRead.command;
          setPendingAuthority({
            kind: "valid",
            command: pending,
            discardAllowed: false,
          });
          await recoverPendingCommand(
            pending,
            page.items,
            administrator,
            request.signal,
          );
          return;
        }
        if (pendingRead === undefined) {
          const first = page.items[0];
          if (!first) {
            setStatus("empty");
          } else {
            activateProfile(first);
          }
          return;
        }
        const first = page.items[0];
        if (!first) {
          setStatus("empty");
          return;
        }
        activateProfile(first);
      })
      .catch((requestError) => {
        if (requestError instanceof BrandProfileApiCancelledError) return;
        failClosedOnAuthorityLoss(requestError);
        setStatus("error");
        setError(profileError(requestError));
      })
      .finally(() => {
        finishRequest(request);
        setBusy((current) => (current === "loading" ? null : current));
      });

    return abortRequests;
  }, [
    abortRequests,
    activateProfile,
    api,
    beginRequest,
    brand,
    commandCoordinator,
    controller,
    finishRequest,
    failClosedOnAuthorityLoss,
    recoverPendingCommand,
    workspaceId,
  ]);

  useEffect(() => {
    const expiresAt = snapshot.validationExpiresAt;
    if (expiresAt === null) return;
    const remaining = expiresAt - Date.now();
    if (remaining <= 0) {
      controller.expireValidation();
      return;
    }
    const timeout = window.setTimeout(
      () => controller.expireValidation(),
      remaining,
    );
    return () => window.clearTimeout(timeout);
  }, [controller, snapshot.validationExpiresAt]);

  useEffect(() => {
    const refresh = () => {
      if (pendingCommand || requestControllers.current.size > 0) return;
      const current = controller.getSnapshot();
      const profileId = current.profile?.id;
      if (profileId) {
        controller.invalidateValidation();
        void openProfile(profileId, {
          preserveLocalDraft: current.dirty || current.conflict !== null,
        });
      }
    };
    window.addEventListener("focus", refresh);
    return () => window.removeEventListener("focus", refresh);
  }, [controller, openProfile, pendingCommand]);

  const loadMoreProfiles = async () => {
    if (pendingCommand || !profilesNextCursor) return;
    const request = beginRequest();
    const identityGeneration = controller.getSnapshot().identityGeneration;
    setProfilesLoading(true);
    setError(null);
    try {
      const page = await api.list(
        {
          brand,
          cursor: profilesNextCursor,
          limit: PROFILE_LIST_PAGE_SIZE,
        },
        request.signal,
      );
      if (
        identityGeneration !== controller.getSnapshot().identityGeneration
      ) {
        return;
      }
      setProfiles((current) => {
        const ids = new Set(current.map((item) => item.id));
        return [
          ...current,
          ...page.items.filter((item) => !ids.has(item.id)),
        ];
      });
      setProfilesNextCursor(page.next_cursor ?? null);
    } catch (requestError) {
      failClosedOnAuthorityLoss(requestError);
      if (!(requestError instanceof BrandProfileApiCancelledError)) {
        setError(profileError(requestError));
      }
    } finally {
      finishRequest(request);
      setProfilesLoading(false);
    }
  };

  const createProfile = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canAdminister || pendingCommand) return;
    const payload: BrandProfileCreateRequestV1 = {
      brand,
      profile_key: profileKey,
      draft: creationDraft,
    };
    const request = beginRequest();
    const token = controller.beginMutation({
      profileId: null,
      profileKey,
      expectedVersion: 0,
      draft: creationDraft,
    });
    setBusy("create");
    setError(null);
    let command: PendingBrandProfileCommand | null = null;
    try {
      const idempotencyKey = newBrandProfileIdempotencyKey("create");
      command = await persistPending({
        action: "CREATE",
        workspaceId,
        brand,
        profileId: null,
        profileKey,
        expectedVersion: 0,
        expectedPublicationVersion: 0,
        idempotencyKey,
        payload,
        attemptedDraft: creationDraft,
      });
      const profile = await api.create(
        payload,
        idempotencyKey,
        request.signal,
      );
      if (!controller.publishMutation(token, profile)) {
        throw new BrandProfileLocalReconciliationError(
          "创建响应与当前品牌档案上下文不一致。",
        );
      }
      const current = await commandCoordinator.confirmAcceptedMutation(
        profile,
        request.signal,
        profileKey,
      );
      if (await clearPending(command)) {
        setNotice({ kind: "resolved", message: "品牌档案已创建。" });
      }
      setProfiles((profiles) => replaceProfile(profiles, current));
      setStatus("ready");
      setCreatingAnother(false);
      setProfileKey("primary");
      setCreationDraft(emptyBrandProfileDraft());
      void loadHistory(current.id, "initial");
    } catch (requestError) {
      failClosedOnAuthorityLoss(requestError);
      if (command) {
        const failure =
          classifyBrandProfileCommandFailure(requestError);
        if (
          failure.kind === "deterministic-rejected" &&
          !failure.requiresExplicitDiscard
        ) {
          await clearPending(command);
        } else {
          const discardAllowed =
            failure.kind === "deterministic-rejected" &&
            failure.requiresExplicitDiscard;
          setPendingAuthority({
            kind: "valid",
            command,
            discardAllowed,
          });
          setNotice({
            kind: discardAllowed ? "blocked" : "pending",
            message: discardAllowed
              ? "创建命令已被服务端确定拒绝；原始 payload 与幂等键已保留，需由管理员核对后明确放弃。"
              : failure.kind === "local-reconciliation-failure"
                ? "服务端可能已接受创建命令，但本地状态无法完成权威对账；原始 payload 与幂等键已保留，工作台保持冻结。"
                : "创建结果尚未确认；原幂等键已保存，刷新后会先对账再决定是否重试。",
          });
        }
      }
      if (!(requestError instanceof BrandProfileApiCancelledError)) {
        setError(profileError(requestError));
      }
    } finally {
      finishRequest(request);
      setBusy((current) => (current === "create" ? null : current));
    }
  };

  const updateDraft = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const profile = controller.getSnapshot().profile;
    const draft = controller.getSnapshot().draft;
    if (!canAdminister || pendingCommand || !profile || !draft) return;
    const payload: BrandProfileUpdateDraftRequestV1 = {
      expected_version: profile.version,
      draft,
    };
    const request = beginRequest();
    const token = controller.beginMutation({
      profileId: profile.id,
      profileKey: profile.profile_key,
      expectedVersion: profile.version,
      draft,
    });
    setBusy("update");
    setError(null);
    let command: PendingBrandProfileCommand | null = null;
    try {
      const idempotencyKey = newBrandProfileIdempotencyKey("update");
      command = await persistPending({
        action: "UPDATE_DRAFT",
        workspaceId,
        brand,
        profileId: profile.id,
        profileKey: profile.profile_key,
        expectedVersion: profile.version,
        expectedPublicationVersion: profile.current_version_number,
        idempotencyKey,
        payload,
        attemptedDraft: draft,
      });
      const updated = await api.updateDraft(
        profile.id,
        payload,
        idempotencyKey,
        request.signal,
      );
      if (!controller.publishMutation(token, updated)) {
        throw new BrandProfileLocalReconciliationError(
          "草稿响应与当前品牌档案版本不一致。",
        );
      }
      const current = await commandCoordinator.confirmAcceptedMutation(
        updated,
        request.signal,
        profile.profile_key,
      );
      if (await clearPending(command)) {
        setNotice({
          kind: "resolved",
          message: "品牌档案草稿已保存。",
        });
      }
      setProfiles((profiles) => replaceProfile(profiles, current));
    } catch (requestError) {
      failClosedOnAuthorityLoss(requestError);
      const failure =
        classifyBrandProfileCommandFailure(requestError);
      if (
        command &&
        failure.kind === "deterministic-rejected" &&
        failure.requiresExplicitDiscard
      ) {
        try {
          await reconcileExplicitDiscardConflict({
            baseline: profile,
            command,
            signal: request.signal,
            token,
          });
        } catch (reconciliationError) {
          failClosedOnAuthorityLoss(reconciliationError);
          setPendingAuthority({
            kind: "valid",
            command,
            discardAllowed: false,
          });
          setNotice({
            kind: "blocked",
            message:
              "服务端已拒绝命令，但本地状态无法完成权威对账；原始 payload 与幂等键已保留，工作台保持冻结。",
          });
          setError(profileError(reconciliationError));
          return;
        }
      } else if (
        command &&
        failure.kind === "deterministic-rejected"
      ) {
        await clearPending(command);
      } else if (command) {
        setPendingAuthority({
          kind: "valid",
          command,
          discardAllowed: false,
        });
        setNotice({
          kind:
            failure.kind === "local-reconciliation-failure"
              ? "blocked"
              : "pending",
          message:
            failure.kind === "local-reconciliation-failure"
              ? "服务端可能已接受草稿命令，但本地状态无法完成权威对账；原始 payload 与幂等键已保留，工作台保持冻结。"
              : "草稿保存结果尚未确认；原幂等键已保存，刷新后会先对账再决定是否重试。",
        });
      }
      if (!(requestError instanceof BrandProfileApiCancelledError)) {
        setError(profileError(requestError));
      }
    } finally {
      finishRequest(request);
      setBusy((current) => (current === "update" ? null : current));
    }
  };

  const validateDraft = async () => {
    const profile = controller.getSnapshot().profile;
    if (
      !canAdminister ||
      pendingCommand ||
      !profile ||
      controller.getSnapshot().dirty
    ) {
      return;
    }
    const request = beginRequest();
    const token = controller.beginValidation(profile.id, profile.version);
    setBusy("validate");
    setError(null);
    try {
      const validation = await api.validate(
        profile.id,
        { expected_version: profile.version },
        request.signal,
      );
      if (!controller.publishValidation(token, validation)) {
        throw new Error("校验结果已过期，请重新校验当前草稿。");
      }
    } catch (requestError) {
      failClosedOnAuthorityLoss(requestError);
      if (!(requestError instanceof BrandProfileApiCancelledError)) {
        setError(profileError(requestError));
      }
    } finally {
      finishRequest(request);
      setBusy((current) => (current === "validate" ? null : current));
    }
  };

  const publishProfile = async () => {
    controller.expireValidation();
    const currentSnapshot = controller.getSnapshot();
    const profile = currentSnapshot.profile;
    const draft = currentSnapshot.draft;
    const validation = currentSnapshot.validation;
    if (
      !canAdminister ||
      pendingCommand ||
      !profile ||
      !draft ||
      currentSnapshot.dirty ||
      validation?.valid !== true ||
      validation.profile_id !== profile.id ||
      validation.profile_version !== profile.version
    ) {
      return;
    }
    const payload: BrandProfilePublishRequestV1 = {
      expected_version: profile.version,
    };
    const request = beginRequest();
    const token = controller.beginMutation({
      profileId: profile.id,
      profileKey: profile.profile_key,
      expectedVersion: profile.version,
      draft,
    });
    setBusy("publish");
    setError(null);
    let command: PendingBrandProfileCommand | null = null;
    try {
      const idempotencyKey = newBrandProfileIdempotencyKey("publish");
      command = await persistPending({
        action: "PUBLISH",
        workspaceId,
        brand,
        profileId: profile.id,
        profileKey: profile.profile_key,
        expectedVersion: profile.version,
        expectedPublicationVersion: profile.current_version_number,
        idempotencyKey,
        payload,
        attemptedDraft: draft,
      });
      const published = await api.publish(
        profile.id,
        payload,
        idempotencyKey,
        request.signal,
      );
      if (!controller.publishMutation(token, published)) {
        throw new BrandProfileLocalReconciliationError(
          "发布响应与当前品牌档案版本不一致。",
        );
      }
      const current = await commandCoordinator.confirmAcceptedMutation(
        published,
        request.signal,
        profile.profile_key,
      );
      if (await clearPending(command)) {
        setNotice({
          kind: "resolved",
          message: "不可变品牌档案版本已发布。",
        });
      }
      setProfiles((profiles) => replaceProfile(profiles, current));
      void loadHistory(current.id, "initial");
    } catch (requestError) {
      failClosedOnAuthorityLoss(requestError);
      const failure =
        classifyBrandProfileCommandFailure(requestError);
      if (
        command &&
        failure.kind === "deterministic-rejected" &&
        failure.requiresExplicitDiscard
      ) {
        try {
          await reconcileExplicitDiscardConflict({
            baseline: profile,
            command,
            signal: request.signal,
            token,
          });
        } catch (reconciliationError) {
          failClosedOnAuthorityLoss(reconciliationError);
          setPendingAuthority({
            kind: "valid",
            command,
            discardAllowed: false,
          });
          setNotice({
            kind: "blocked",
            message:
              "服务端已拒绝命令，但本地状态无法完成权威对账；原始 payload 与幂等键已保留，工作台保持冻结。",
          });
          setError(profileError(reconciliationError));
          return;
        }
      } else if (
        command &&
        failure.kind === "deterministic-rejected"
      ) {
        await clearPending(command);
      } else if (command) {
        setPendingAuthority({
          kind: "valid",
          command,
          discardAllowed: false,
        });
        setNotice({
          kind:
            failure.kind === "local-reconciliation-failure"
              ? "blocked"
              : "pending",
          message:
            failure.kind === "local-reconciliation-failure"
              ? "服务端可能已接受发布命令，但本地状态无法完成权威对账；原始 payload 与幂等键已保留，工作台保持冻结。"
              : "发布结果尚未确认；原幂等键已保存，刷新后会先对账，绝不会盲目创建重复版本。",
        });
      }
      if (!(requestError instanceof BrandProfileApiCancelledError)) {
        setError(profileError(requestError));
      }
    } finally {
      finishRequest(request);
      setBusy((current) => (current === "publish" ? null : current));
    }
  };

  const requestProfileSwitch = (profileId: string) => {
    const current = controller.getSnapshot();
    if (profileId === current.profile?.id) return;
    const guard = brandProfileIdentityChangeGuard({
      creatingAnother,
      dirty: current.dirty,
      hasConflict: current.conflict !== null,
      pendingCommand,
    });
    if (guard === "frozen") return;
    if (guard === "discard-required") {
      setPendingIdentityChange({
        kind: "switch-profile",
        profileId,
      });
      return;
    }
    void openProfile(profileId);
  };

  const startCreate = () => {
    setProfileKey(nextUniqueProfileKey(profiles));
    setCreationDraft(emptyBrandProfileDraft());
    setCreatingAnother(true);
  };

  const discardPendingAuthority = async () => {
    if (
      pendingAuthority.kind !== "valid" ||
      !pendingAuthority.discardAllowed
    ) {
      return;
    }
    const command = pendingAuthority.command;
    if (await clearPending(command)) {
      setNotice({
        kind: "resolved",
        message:
          "已按人工确认精确清除该幂等键的待对账记录；未删除其他 Workspace 或品牌的记录。",
      });
    }
  };

  return {
      brand,
      busy,
      canAdminister,
      capabilityDegraded,
      creatingAnother,
      creationDraft,
      error,
      notice,
      canDiscardPendingCommand:
        pendingAuthority.kind === "valid" &&
        pendingAuthority.discardAllowed,
      pendingCommand,
      onCancelCreate: () => {
        if (pendingCommand) return;
        setPendingIdentityChange({ kind: "cancel-create" });
      },
      onCancelIdentityChange: () => setPendingIdentityChange(null),
      onConfirmIdentityChange: () => {
        if (pendingCommand) return;
        const intent = pendingIdentityChange;
        if (!intent) return;
        setPendingIdentityChange(null);
        if (intent.kind === "start-create") {
          controller.discardLocalChanges();
          startCreate();
          return;
        }
        if (intent.kind === "cancel-create") {
          setCreatingAnother(false);
          setCreationDraft(emptyBrandProfileDraft());
          return;
        }
        void openProfile(intent.profileId);
      },
      onCreate: (event: FormEvent<HTMLFormElement>) =>
        void createProfile(event),
      onCreationDraftChange: (nextDraft: BrandProfileDraftV1) => {
        if (pendingCommand) return;
        setCreationDraft(nextDraft);
      },
      onDiscardConflict: () => {
        if (pendingCommand) return;
        controller.discardConflictDraft();
        setError(null);
      },
      onDiscardPendingCommand: () => void discardPendingAuthority(),
      onDraftChange: (next: BrandProfileDraftV1) => {
        if (pendingCommand) return;
        controller.editDraft(next);
      },
      onLoadHistoryMore: () => {
        if (pendingCommand) return;
        const profileId = controller.getSnapshot().profile?.id;
        if (profileId) void loadHistory(profileId, "more");
      },
      onLoadMoreProfiles: () => void loadMoreProfiles(),
      onLoadVersion: (versionNumber: number) => {
        if (pendingCommand) return;
        const profileId = controller.getSnapshot().profile?.id;
        if (profileId) void loadVersion(profileId, versionNumber, true);
      },
      onProfileKeyChange: (nextProfileKey: string) => {
        if (pendingCommand) return;
        setProfileKey(nextProfileKey);
      },
      onPublish: () => void publishProfile(),
      onRefresh: () => {
        if (pendingCommand) return;
        const current = controller.getSnapshot();
        if (!current.profile) return;
        void openProfile(current.profile.id, {
          preserveLocalDraft:
            current.dirty || current.conflict !== null,
        });
      },
      onRestoreConflict: () => {
        if (pendingCommand) return;
        controller.restoreConflictDraft();
        setError(null);
      },
      onStartCreate: () => {
        const current = controller.getSnapshot();
        const guard = brandProfileIdentityChangeGuard({
          creatingAnother,
          dirty: current.dirty,
          hasConflict: current.conflict !== null,
          pendingCommand,
        });
        if (guard === "frozen") return;
        if (guard === "discard-required") {
          setPendingIdentityChange({ kind: "start-create" });
          return;
        }
        startCreate();
      },
      onSwitchProfile: requestProfileSwitch,
      onUpdateDraft: (event: FormEvent<HTMLFormElement>) =>
        void updateDraft(event),
      onValidate: () => void validateDraft(),
      pendingIdentityChange: pendingIdentityChange?.kind ?? null,
      profileKey,
      profiles,
      profilesLoading,
      profilesNextCursor,
      snapshot,
      status,
      versionLoading,
      identityChangeGuard: brandProfileIdentityChangeGuard({
        creatingAnother,
        dirty: snapshot.dirty,
        hasConflict: snapshot.conflict !== null,
        pendingCommand,
      }),
  };
}
