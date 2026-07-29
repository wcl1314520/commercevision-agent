"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from "react";

import type {
  ProductBriefAnalysisAcceptedV1,
  ProductBriefConfirmationRequestV1,
  ProductBriefResponseV1,
  ProductBriefRevisionRequestV1,
  ProductBriefWorkflowContextResponseV1,
} from "./generated/catalog-api";
import {
  newProductBriefIdempotencyKey,
  ProductBriefApi,
  ProductBriefApiCancelledError,
  ProductBriefApiError,
} from "./product-brief-api";
import { isProductBriefOperationPollTerminal } from "./operation-polling";
import {
  PRODUCT_BRIEF_HISTORY_PAGE_SIZE,
  createProductBriefWorkbenchController,
} from "./product-brief-workbench-controller";
import type {
  ProductBriefControllerIdentity,
  ProductBriefControllerSnapshot,
  ProductBriefWorkbenchController,
} from "./product-brief-workbench-controller";
import {
  activateProductBriefPersistenceIdentity,
  clearProductBriefPersistenceNamespace,
  clearPersistedProductBrief,
  createProductBriefRetentionController,
  isActiveProductBriefRetentionDeadline,
  persistedCommandMatches,
  readPersistedProductBrief,
  sweepExpiredPersistedProductBriefs,
  writePersistedProductBrief,
} from "./product-brief-persistence";
import type {
  PendingProductBriefAnalysis,
  PersistedProductBrief,
  PersistedProductBriefV3,
  ProductBriefNamespaceStorage,
  ProductBriefRetentionController,
} from "./product-brief-persistence";
import { productBriefCommandsMatch } from "./product-brief-workbench-state";
import type { PendingProductBriefCommand } from "./product-brief-workbench-state";

type BusyState =
  | "recover"
  | "analyze"
  | "load"
  | "revise"
  | "confirm"
  | "refresh"
  | "history-more"
  | null;

type ProductBriefFormSeed = {
  revision: number;
  lookupId: string;
  workflowId: string;
  assetVersionIds: string[];
};

type ProductBriefControllerUi = {
  busy: BusyState;
  error: string | null;
  notice: string | null;
  auxiliaryWarning: string | null;
  pollingWarning: string | null;
};

type AnalysisInput = {
  workflowId: string;
  assetVersionIds: [string, ...string[]];
};

type ConfirmationInput = {
  reasonCode: string | null;
  commentRef: string | null;
};

const EMPTY_UI: ProductBriefControllerUi = {
  busy: "recover",
  error: null,
  notice: null,
  auxiliaryWarning: null,
  pollingWarning: null,
};

export function acquireProductBriefBrowserStorages(
  browser: Pick<Window, "sessionStorage" | "localStorage"> = window,
): {
  sessionStorage: ProductBriefNamespaceStorage | null;
  localStorage: ProductBriefNamespaceStorage | null;
} {
  let sessionStorage: ProductBriefNamespaceStorage | null = null;
  let localStorage: ProductBriefNamespaceStorage | null = null;
  try {
    sessionStorage = browser.sessionStorage;
  } catch {
    // A browser may deny one Storage area while leaving the other available.
  }
  try {
    localStorage = browser.localStorage;
  } catch {
    // Legacy cleanup is optional and must not block session recovery.
  }
  return { sessionStorage, localStorage };
}

function isAuthoritativeProductBriefGone(error: unknown): boolean {
  if (
    typeof error === "object" &&
    error !== null &&
    "status" in error &&
    error.status === 410
  ) {
    return true;
  }
  return (
    error instanceof ProductBriefApiError &&
    error.envelope?.code === "PRODUCT_BRIEF_RETENTION_EXPIRED"
  );
}

function isDefinitiveNonRetryableClientError(error: unknown): boolean {
  return (
    error instanceof ProductBriefApiError &&
    error.status >= 400 &&
    error.status < 500 &&
    error.envelope?.retryable === false
  );
}

function productBriefErrorMessage(error: unknown): string {
  if (error instanceof ProductBriefApiError) {
    const code = error.envelope?.code;
    if (code === "VERSION_CONFLICT") {
      return "服务器已有新的商品理解版本。";
    }
    if (code?.startsWith("VISION_TRANSFER_")) {
      return "当前工作区或服务端策略不允许模型传输。";
    }
    if (code?.startsWith("RIGHTS_")) {
      return "素材当前权利不允许商品理解分析。";
    }
    if (code === "PROVIDER_POLICY_DENIED") {
      return "当前模型策略拒绝此分析请求。";
    }
    return error.envelope?.message ?? "商品理解请求失败。";
  }
  return error instanceof Error ? error.message : "商品理解请求失败。";
}

function isCurrentRequest(
  generation: number,
  currentGeneration: number,
  signal: AbortSignal,
): boolean {
  return !signal.aborted && generation === currentGeneration;
}

export type AuthoritativeGoneTarget =
  | { productBriefId: string }
  | { workflowId: string }
  | { pendingAnalysis: PendingProductBriefAnalysis };

export function productBriefResponseMatchesIdentity(
  response: Pick<
    ProductBriefResponseV1,
    "id" | "workspace_id" | "workflow_id" | "product_id" | "operation_id"
  >,
  expected: {
    workspaceId: string;
    productId: string;
    productBriefId: string;
    workflowId?: string;
    operationId?: string;
  },
): boolean {
  return (
    response.workspace_id === expected.workspaceId &&
    response.product_id === expected.productId &&
    response.id === expected.productBriefId &&
    (expected.workflowId === undefined ||
      response.workflow_id === expected.workflowId) &&
    (expected.operationId === undefined ||
      response.operation_id === expected.operationId)
  );
}

export function productBriefRevisionResponseMatchesIdentity(
  response: Pick<
    ProductBriefResponseV1,
    | "id"
    | "workspace_id"
    | "workflow_id"
    | "product_id"
    | "operation_id"
    | "state"
    | "current_version_id"
    | "confirmed_version_id"
    | "version"
  >,
  expected: {
    workspaceId: string;
    productId: string;
    productBriefId: string;
    workflowId: string;
    priorOperationId: string;
    expectedProductBriefVersion: number;
    baseVersionId: string;
  },
): boolean {
  if (
    !productBriefResponseMatchesIdentity(response, {
      workspaceId: expected.workspaceId,
      productId: expected.productId,
      productBriefId: expected.productBriefId,
      workflowId: expected.workflowId,
    }) ||
    response.state !== "AWAITING_CONFIRMATION" ||
    response.version !== expected.expectedProductBriefVersion + 1 ||
    response.current_version_id === null ||
    response.current_version_id === expected.baseVersionId
  ) {
    return false;
  }
  return (
    response.operation_id === expected.priorOperationId ||
    response.confirmed_version_id === expected.baseVersionId
  );
}

export function exactPendingProductBriefCommandIsDurable(
  snapshot: ProductBriefControllerSnapshot,
  persisted: PersistedProductBrief | null,
  command: PendingProductBriefCommand,
  status: "pending" | "version-conflict",
): persisted is PersistedProductBriefV3 {
  if (!persistedCommandMatches(persisted, command)) return false;
  return (
    snapshot.identity.workspaceId === persisted.workspaceId &&
    snapshot.identity.productId === persisted.productId &&
    snapshot.commandStatus === status &&
    snapshot.pendingCommand !== null &&
    productBriefCommandsMatch(snapshot.pendingCommand, command) &&
    persisted.commandStatus === status
  );
}

export function productBriefWorkflowContextMatchesIdentity(
  response: Pick<ProductBriefWorkflowContextResponseV1, "id">,
  expected: {
    requestedWorkflowId: string;
    boundWorkflowId?: string;
  },
): boolean {
  return (
    response.id === expected.requestedWorkflowId &&
    (expected.boundWorkflowId === undefined ||
      response.id === expected.boundWorkflowId)
  );
}

function pendingAnalysesMatch(
  left: PendingProductBriefAnalysis,
  right: PendingProductBriefAnalysis,
): boolean {
  const priorProductBriefMatches =
    (left.priorProductBrief === null &&
      right.priorProductBrief === null) ||
    (left.priorProductBrief !== null &&
      right.priorProductBrief !== null &&
      left.priorProductBrief.productBriefId ===
        right.priorProductBrief.productBriefId &&
      left.priorProductBrief.operationId ===
        right.priorProductBrief.operationId);
  return (
    priorProductBriefMatches &&
    left.idempotencyKey === right.idempotencyKey &&
    left.payload.workflow_id === right.payload.workflow_id &&
    left.payload.product_id === right.payload.product_id &&
    left.payload.expected_workflow_version ===
      right.payload.expected_workflow_version &&
    left.payload.asset_version_ids.length ===
      right.payload.asset_version_ids.length &&
    left.payload.asset_version_ids.every(
      (assetVersionId, index) =>
        assetVersionId === right.payload.asset_version_ids[index],
    )
  );
}

export function productBriefAnalysisAcceptedMatchesPending(
  accepted: ProductBriefAnalysisAcceptedV1,
  pending: PendingProductBriefAnalysis,
  workspaceId: string,
): boolean {
  return (
    accepted.product_brief.workspace_id === workspaceId &&
    accepted.product_brief.product_id === pending.payload.product_id &&
    accepted.product_brief.workflow_id === pending.payload.workflow_id &&
    accepted.product_brief.operation_id === accepted.operation_id &&
    (pending.priorProductBrief === null ||
      (accepted.product_brief.id ===
        pending.priorProductBrief.productBriefId &&
        accepted.operation_id !==
          pending.priorProductBrief.operationId))
  );
}

export function productBriefReloadMatchesAcceptedAnalysis(
  current: ProductBriefResponseV1,
  accepted: ProductBriefAnalysisAcceptedV1,
): boolean {
  const acceptedBrief = accepted.product_brief;
  return (
    acceptedBrief.operation_id === accepted.operation_id &&
    current.id === acceptedBrief.id &&
    current.workspace_id === acceptedBrief.workspace_id &&
    current.product_id === acceptedBrief.product_id &&
    current.workflow_id === acceptedBrief.workflow_id &&
    current.version >= acceptedBrief.version &&
    (current.version > acceptedBrief.version ||
      current.operation_id === accepted.operation_id)
  );
}

export function earliestActiveProductBriefRetentionDeadline(
  ...deadlines: unknown[]
): string | null {
  const nowMs = Date.now();
  let earliest: { value: string; time: number } | null = null;
  for (const deadline of deadlines) {
    if (!isActiveProductBriefRetentionDeadline(deadline, nowMs)) {
      return null;
    }
    const time = Date.parse(deadline);
    if (earliest === null || time < earliest.time) {
      earliest = { value: deadline, time };
    }
  }
  return earliest?.value ?? null;
}

function persistedRetentionDeadlineForProductBrief(
  persisted: PersistedProductBrief | null,
  current: ProductBriefResponseV1,
): string | null {
  if (!persisted || persisted.workflowId !== current.workflow_id) {
    return null;
  }
  if (
    (persisted.schemaVersion === 1 ||
      persisted.schemaVersion === 3) &&
    persisted.productBriefId === current.id
  ) {
    return persisted.retentionDeadline;
  }
  if (
    persisted.schemaVersion === 2 &&
    persisted.pendingAnalysis.priorProductBrief?.productBriefId ===
      current.id
  ) {
    return persisted.retentionDeadline;
  }
  return null;
}

function persistedIdentityForProductBrief(
  persisted: PersistedProductBrief | null,
  productBriefId: string,
): { workflowId: string; operationId: string } | null {
  if (
    (persisted?.schemaVersion === 1 ||
      persisted?.schemaVersion === 3) &&
    persisted.productBriefId === productBriefId
  ) {
    return {
      workflowId: persisted.workflowId,
      operationId: persisted.operationId,
    };
  }
  if (
    persisted?.schemaVersion === 2 &&
    persisted.pendingAnalysis.priorProductBrief?.productBriefId ===
      productBriefId
  ) {
    return {
      workflowId: persisted.workflowId,
      operationId:
        persisted.pendingAnalysis.priorProductBrief.operationId,
    };
  }
  return null;
}

function productBriefRetentionDeadlineAtOrBefore(
  candidate: unknown,
  ceiling: string,
): string | null {
  const earliest = earliestActiveProductBriefRetentionDeadline(
    candidate,
    ceiling,
  );
  if (
    earliest === null ||
    !isActiveProductBriefRetentionDeadline(candidate) ||
    Date.parse(candidate) > Date.parse(ceiling)
  ) {
    return null;
  }
  return earliest;
}

function authoritativeGoneTargetIsCurrent(
  current: ProductBriefControllerSnapshot,
  persisted: PersistedProductBrief | null,
  target: AuthoritativeGoneTarget,
): boolean {
  if ("productBriefId" in target) {
    return (
      current.brief?.id === target.productBriefId ||
      current.pendingCommand?.productBriefId === target.productBriefId ||
      ((persisted?.schemaVersion === 1 ||
        persisted?.schemaVersion === 3) &&
        persisted.productBriefId === target.productBriefId)
    );
  }
  if ("workflowId" in target) {
    return (
      current.brief?.workflow_id === target.workflowId ||
      current.pendingAnalysis?.payload.workflow_id === target.workflowId ||
      persisted?.workflowId === target.workflowId
    );
  }
  return (
    (current.pendingAnalysis !== null &&
      pendingAnalysesMatch(
        current.pendingAnalysis,
        target.pendingAnalysis,
      )) ||
    (persisted?.schemaVersion === 2 &&
      pendingAnalysesMatch(
        persisted.pendingAnalysis,
        target.pendingAnalysis,
      ))
  );
}

export function authoritativeGoneAppliesToCurrentWorkbench({
  current,
  persisted,
  target,
  requestIdentity,
  requestGeneration,
  currentGeneration,
  requestAborted,
}: {
  current: ProductBriefControllerSnapshot;
  persisted: PersistedProductBrief | null;
  target: AuthoritativeGoneTarget;
  requestIdentity: ProductBriefControllerIdentity;
  requestGeneration: number;
  currentGeneration: number;
  requestAborted: boolean;
}): boolean {
  return (
    !requestAborted &&
    requestGeneration === currentGeneration &&
    current.identity.workspaceId === requestIdentity.workspaceId &&
    current.identity.productId === requestIdentity.productId &&
    authoritativeGoneTargetIsCurrent(current, persisted, target)
  );
}

export type ProductBriefWorkbenchControllerResult = {
  snapshot: ProductBriefControllerSnapshot;
  formSeed: ProductBriefFormSeed;
  ui: ProductBriefControllerUi;
  analyze: (input: AnalysisInput) => Promise<void>;
  load: (productBriefId: string) => Promise<void>;
  revise: (payload: ProductBriefRevisionRequestV1) => Promise<void>;
  confirm: (input: ConfirmationInput) => Promise<void>;
  refresh: () => Promise<void>;
  loadMoreVersions: () => Promise<void>;
  retryPending: () => Promise<void>;
  resolveRevisionConflict: (
    choice: "restore" | "discard",
  ) => Extract<PendingProductBriefCommand, { kind: "revise" }> | null;
  resumePolling: () => void;
  reportError: (message: string) => void;
};

export function useProductBriefWorkbenchController({
  productId,
  workspaceId = "catalog-demo",
}: {
  productId: string;
  workspaceId?: string;
}): ProductBriefWorkbenchControllerResult {
  const controllerRef = useRef<ProductBriefWorkbenchController | null>(
    null,
  );
  if (controllerRef.current === null) {
    controllerRef.current = createProductBriefWorkbenchController({
      productId,
      workspaceId,
    });
  }
  const controller = controllerRef.current;
  const [, forceRender] = useReducer((value: number) => value + 1, 0);
  const [ui, setUi] = useState<ProductBriefControllerUi>(EMPTY_UI);
  const [formSeed, setFormSeed] = useState<ProductBriefFormSeed>({
    revision: 0,
    lookupId: "",
    workflowId: "",
    assetVersionIds: [],
  });
  const api = useMemo(
    () => new ProductBriefApi({ workspaceId }),
    [workspaceId],
  );
  const persistenceIdentity = useMemo(
    () => ({ workspaceId, productId }),
    [productId, workspaceId],
  );
  const storageRef = useRef<ProductBriefNamespaceStorage | null>(null);
  const retentionRef =
    useRef<ProductBriefRetentionController | null>(null);
  const requestControllerRef = useRef<AbortController | null>(null);
  const requestGenerationRef = useRef(0);

  const patchUi = useCallback(
    (patch: Partial<ProductBriefControllerUi>) => {
      setUi((current) => ({ ...current, ...patch }));
    },
    [],
  );

  const publishController = useCallback(() => {
    forceRender();
  }, []);

  const resetForm = useCallback(
    ({
      lookupId = "",
      workflowId = "",
      assetVersionIds = [],
    }: Partial<Omit<ProductBriefFormSeed, "revision">> = {}) => {
      setFormSeed((current) => ({
        revision: current.revision + 1,
        lookupId,
        workflowId,
        assetVersionIds,
      }));
    },
    [],
  );

  const storage = useCallback((): ProductBriefNamespaceStorage => {
    if (!storageRef.current) {
      throw new Error("浏览器会话存储尚未就绪。");
    }
    return storageRef.current;
  }, []);

  const armRetention = useCallback(
    (retentionDeadline: string): boolean =>
      retentionRef.current?.activate(
        persistenceIdentity,
        retentionDeadline,
      ) ?? false,
    [persistenceIdentity],
  );

  const readPersisted = useCallback(
    (): PersistedProductBrief | null =>
      readPersistedProductBrief(storage(), persistenceIdentity),
    [persistenceIdentity, storage],
  );

  const clearPersisted = useCallback(() => {
    clearPersistedProductBrief(storage(), persistenceIdentity);
  }, [persistenceIdentity, storage]);

  const invalidateRetainedWorkbench = useCallback(
    ({
      clearPersistence = true,
      error =
        "商品理解任务已到保留期限，本地恢复数据已清除。",
    }: {
      clearPersistence?: boolean;
      error?: string;
    } = {}) => {
      requestGenerationRef.current += 1;
      requestControllerRef.current?.abort();
      requestControllerRef.current = new AbortController();
      retentionRef.current?.dispose();
      if (clearPersistence) clearPersisted();
      controller.expireRetention();
      publishController();
      resetForm();
      setUi({
        ...EMPTY_UI,
        busy: null,
        error,
      });
    },
    [clearPersisted, controller, publishController, resetForm],
  );

  const writePersisted = useCallback(
    (value: PersistedProductBrief): boolean => {
      const previous = readPersisted();
      let activated = false;
      try {
        activated = armRetention(value.retentionDeadline);
      } catch {
        activated = false;
      }
      if (!activated) {
        if (!controller.getSnapshot().retentionExpired) {
          invalidateRetainedWorkbench();
        }
        return false;
      }
      if (!writePersistedProductBrief(storage(), value)) {
        invalidateRetainedWorkbench({
          clearPersistence: false,
          error:
            "浏览器无法安全保存商品理解恢复状态，工作台已关闭。",
        });
        let priorTimerRestored = false;
        if (previous) {
          try {
            priorTimerRestored = armRetention(
              previous.retentionDeadline,
            );
          } catch {
            priorTimerRestored = false;
          }
        }
        if (!priorTimerRestored) {
          retentionRef.current?.dispose();
          clearPersisted();
        }
        return false;
      }
      return true;
    },
    [
      armRetention,
      clearPersisted,
      controller,
      invalidateRetainedWorkbench,
      readPersisted,
      storage,
    ],
  );

  const handleAuthoritativeGone = useCallback(
    (
      error: unknown,
      target: AuthoritativeGoneTarget,
      generation: number,
      signal: AbortSignal,
    ): boolean => {
      if (!isAuthoritativeProductBriefGone(error)) return false;
      const current = controller.getSnapshot();
      const persisted = readPersisted();
      if (
        !authoritativeGoneAppliesToCurrentWorkbench({
          current,
          persisted,
          target,
          requestIdentity: { workspaceId, productId },
          requestGeneration: generation,
          currentGeneration: requestGenerationRef.current,
          requestAborted: signal.aborted,
        })
      ) {
        return false;
      }
      invalidateRetainedWorkbench();
      return true;
    },
    [
      controller,
      invalidateRetainedWorkbench,
      productId,
      readPersisted,
      workspaceId,
    ],
  );

  const refreshAuxiliary = useCallback(
    async (
      current: ProductBriefResponseV1,
      options: { clearUnavailable: boolean },
      generation: number,
      signal: AbortSignal,
    ) => {
      const historyRead = controller.beginHistoryRead(
        "initial",
        current.id,
      );
      const operationRead = controller.beginOperationRead(
        current.operation_id,
      );
      publishController();
      const auxiliaryController = new AbortController();
      const abortAuxiliary = () =>
        auxiliaryController.abort(signal.reason);
      if (signal.aborted) {
        abortAuxiliary();
      } else {
        signal.addEventListener("abort", abortAuxiliary, {
          once: true,
        });
      }
      const observeAuthoritativeGone = <T,>(
        pending: Promise<T>,
      ): Promise<T> =>
        pending.catch((error: unknown) => {
          if (isAuthoritativeProductBriefGone(error)) {
            auxiliaryController.abort(error);
            handleAuthoritativeGone(
              error,
              { productBriefId: current.id },
              generation,
              signal,
            );
          }
          throw error;
        });
      const [history, operation] = await Promise.allSettled([
        observeAuthoritativeGone(
          api.listVersions(
            current.id,
            { limit: PRODUCT_BRIEF_HISTORY_PAGE_SIZE },
            auxiliaryController.signal,
          ),
        ),
        observeAuthoritativeGone(
          api.getOperation(
            current.id,
            current.operation_id,
            auxiliaryController.signal,
          ),
        ),
      ]);
      signal.removeEventListener("abort", abortAuxiliary);
      if (
        !isCurrentRequest(
          generation,
          requestGenerationRef.current,
          signal,
        ) ||
        controller.getSnapshot().brief?.id !== current.id
      ) {
        return;
      }
      const authoritativeGone = [history, operation].find(
        (result) =>
          result.status === "rejected" &&
          isAuthoritativeProductBriefGone(result.reason),
      );
      if (
        authoritativeGone?.status === "rejected" &&
        handleAuthoritativeGone(
          authoritativeGone.reason,
          { productBriefId: current.id },
          generation,
          signal,
        )
      ) {
        return;
      }
      const unavailable: string[] = [];
      if (history.status === "fulfilled") {
        if (
          !controller.publishHistory(historyRead, history.value) &&
          controller.failHistory(
            historyRead,
            options.clearUnavailable,
          )
        ) {
          unavailable.push("版本历史");
        }
      } else if (
        !(history.reason instanceof ProductBriefApiCancelledError)
      ) {
        if (controller.failHistory(historyRead, options.clearUnavailable)) {
          unavailable.push("版本历史");
        }
      }
      if (operation.status === "fulfilled") {
        controller.completeAuxiliaryOperationRead(
          operationRead,
          operation.value,
        );
      } else if (
        !(operation.reason instanceof ProductBriefApiCancelledError) &&
        operationRead.operationId ===
          controller.getSnapshot().brief?.operation_id
      ) {
        controller.failAuxiliaryOperationRead(operationRead);
        unavailable.push("任务状态");
      }
      publishController();
      patchUi({
        auxiliaryWarning:
          unavailable.length > 0
            ? `商品理解已更新，但${unavailable.join("和")}暂不可用。`
            : null,
      });
    },
    [
      api,
      controller,
      handleAuthoritativeGone,
      patchUi,
      publishController,
    ],
  );

  const loadCurrent = useCallback(
    async (
      productBriefId: string,
      {
        resetFormState = true,
        clearUnavailable = false,
        generation = requestGenerationRef.current,
        signal = requestControllerRef.current?.signal,
        retentionDeadlineCeiling,
        acceptedAnalysisAuthority,
      }: {
        resetFormState?: boolean;
        clearUnavailable?: boolean;
        generation?: number;
        signal?: AbortSignal;
        retentionDeadlineCeiling?: string;
        acceptedAnalysisAuthority?: ProductBriefAnalysisAcceptedV1;
      } = {},
    ): Promise<ProductBriefResponseV1> => {
      if (!signal) throw new ProductBriefApiCancelledError();
      const persistedAtStart = readPersisted();
      const read = controller.beginBriefRead();
      let current: ProductBriefResponseV1;
      try {
        current = await api.get(productBriefId, signal);
      } catch (requestError) {
        if (
          handleAuthoritativeGone(
            requestError,
            { productBriefId },
            generation,
            signal,
          )
        ) {
          throw new ProductBriefApiCancelledError();
        }
        throw requestError;
      }
      if (
        !isCurrentRequest(
          generation,
          requestGenerationRef.current,
          signal,
        )
      ) {
        throw new ProductBriefApiCancelledError();
      }
      const boundIdentity = acceptedAnalysisAuthority
        ? {
            workflowId:
              acceptedAnalysisAuthority.product_brief.workflow_id,
            operationId: acceptedAnalysisAuthority.operation_id,
          }
        : persistedIdentityForProductBrief(
            persistedAtStart,
            productBriefId,
          );
      if (
        !productBriefResponseMatchesIdentity(current, {
          workspaceId,
          productId,
          productBriefId,
          ...(boundIdentity ?? {}),
        })
      ) {
        throw new Error(
          "商品理解响应身份不匹配，原恢复身份已保留。",
        );
      }
      if (
        acceptedAnalysisAuthority &&
        !productBriefReloadMatchesAcceptedAnalysis(
          current,
          acceptedAnalysisAuthority,
        )
      ) {
        throw new Error(
          "商品理解读取结果早于已接受的分析响应，原请求标识已保留。",
        );
      }
      const persisted = readPersisted();
      const retainedDeadlineAtStart =
        persistedRetentionDeadlineForProductBrief(
          persistedAtStart,
          current,
        );
      const retainedDeadline =
        persistedRetentionDeadlineForProductBrief(persisted, current);
      const retentionDeadline =
        retentionDeadlineCeiling === undefined
          ? earliestActiveProductBriefRetentionDeadline(
              current.retention_deadline,
              ...(retainedDeadlineAtStart === null
                ? []
                : [retainedDeadlineAtStart]),
              ...(retainedDeadline === null
                ? []
                : [retainedDeadline]),
            )
          : productBriefRetentionDeadlineAtOrBefore(
              current.retention_deadline,
              retentionDeadlineCeiling,
            );
      if (retentionDeadline === null) {
        throw new Error(
          retentionDeadlineCeiling === undefined
            ? "商品理解记录缺少有效的任务保留期限。"
            : "商品理解响应保留期限发生非法漂移，原请求标识已保留。",
        );
      }
      current = {
        ...current,
        retention_deadline: retentionDeadline,
      };
      const replacingBrief =
        controller.getSnapshot().brief?.id !== current.id;
      if (!controller.publishBrief(read, current, resetFormState)) {
        throw new ProductBriefApiCancelledError();
      }
      publishController();
      const assetVersionIds = persisted?.assetVersionIds ?? [];
      const nextPersisted: PersistedProductBrief =
        persisted?.schemaVersion === 3 &&
        persisted.pendingCommand.productBriefId === current.id
          ? {
              ...persisted,
              operationId: current.operation_id,
              workflowId: current.workflow_id,
              retentionDeadline,
            }
          : {
              schemaVersion: 1,
              workspaceId,
              productId,
              productBriefId: current.id,
              operationId: current.operation_id,
              workflowId: current.workflow_id,
              assetVersionIds,
              retentionDeadline,
            };
      if (!writePersisted(nextPersisted)) {
        throw new Error("任务已过保留期限，无法继续读取商品理解。");
      }
      if (resetFormState) {
        resetForm({
          lookupId: current.id,
          workflowId: current.workflow_id,
          assetVersionIds,
        });
      }
      await refreshAuxiliary(
        current,
        { clearUnavailable: clearUnavailable || replacingBrief },
        generation,
        signal,
      );
      if (
        !isCurrentRequest(
          generation,
          requestGenerationRef.current,
          signal,
        )
      ) {
        throw new ProductBriefApiCancelledError();
      }
      return current;
    },
    [
      api,
      controller,
      handleAuthoritativeGone,
      productId,
      publishController,
      readPersisted,
      refreshAuxiliary,
      resetForm,
      writePersisted,
      workspaceId,
    ],
  );

  const settlePendingCommand = useCallback(
    (
      command: PendingProductBriefCommand,
      current?: ProductBriefResponseV1,
      options: {
        commandStatus?: "pending" | "version-conflict";
        revisionOperationTransition?: {
          expectedProductBriefVersion: number;
          baseVersionId: string;
        };
      } = {},
    ): string | null => {
      const persisted = readPersisted();
      if (
        !exactPendingProductBriefCommandIsDurable(
          controller.getSnapshot(),
          persisted,
          command,
          options.commandStatus ?? "pending",
        )
      ) {
        return null;
      }
      if (current) {
        const responseMatches = options.revisionOperationTransition
          ? productBriefRevisionResponseMatchesIdentity(current, {
              workspaceId,
              productId,
              productBriefId: command.productBriefId,
              workflowId: persisted.workflowId,
              priorOperationId: persisted.operationId,
              ...options.revisionOperationTransition,
            })
          : productBriefResponseMatchesIdentity(current, {
              workspaceId,
              productId,
              productBriefId: command.productBriefId,
              workflowId: persisted.workflowId,
              operationId: persisted.operationId,
            });
        if (!responseMatches) return null;
      }
      const retentionDeadline =
        earliestActiveProductBriefRetentionDeadline(
          persisted.retentionDeadline,
          ...(current ? [current.retention_deadline] : []),
        );
      if (
        !retentionDeadline ||
        !writePersisted({
          schemaVersion: 1,
          workspaceId,
          productId,
          productBriefId: current?.id ?? persisted.productBriefId,
          operationId:
            current?.operation_id ?? persisted.operationId,
          workflowId: current?.workflow_id ?? persisted.workflowId,
          assetVersionIds: persisted.assetVersionIds,
          retentionDeadline,
        })
      ) {
        return null;
      }
      const settled = controller.settleCommand(command);
      if (settled) publishController();
      return settled ? retentionDeadline : null;
    },
    [
      controller,
      productId,
      publishController,
      readPersisted,
      writePersisted,
      workspaceId,
    ],
  );

  const recoverAnalysis = useCallback(
    async (
      pending: PendingProductBriefAnalysis,
      generation = requestGenerationRef.current,
      signal = requestControllerRef.current?.signal,
    ) => {
      if (!signal) return;
      patchUi({ busy: "analyze", error: null });
      try {
        const persistedAtStart = readPersisted();
        if (
          persistedAtStart?.schemaVersion !== 2 ||
          !pendingAnalysesMatch(
            persistedAtStart.pendingAnalysis,
            pending,
          )
        ) {
          controller.recoverAnalysis(pending);
          publishController();
          patchUi({
            error:
              "无法确认原分析请求的持久化身份，已停止自动重试。",
          });
          return;
        }
        const accepted = await api.requestAnalysis(
          pending.payload,
          pending.idempotencyKey,
          signal,
        );
        if (
          !isCurrentRequest(
            generation,
            requestGenerationRef.current,
            signal,
          )
        ) {
          return;
        }
        if (
          !productBriefAnalysisAcceptedMatchesPending(
            accepted,
            pending,
            workspaceId,
          )
        ) {
          controller.recoverAnalysis(pending);
          publishController();
          patchUi({
            error:
              "分析响应身份不匹配。原请求标识已保留，可安全重试。",
          });
          return;
        }
        const retentionDeadline =
          productBriefRetentionDeadlineAtOrBefore(
            accepted.product_brief.retention_deadline,
            persistedAtStart.retentionDeadline,
          );
        if (retentionDeadline === null) {
          controller.recoverAnalysis(pending);
          publishController();
          patchUi({
            error:
              "分析响应保留期限发生非法漂移。原请求标识已保留，可安全重试。",
          });
          return;
        }
        await loadCurrent(accepted.product_brief.id, {
          resetFormState: true,
          generation,
          signal,
          retentionDeadlineCeiling: retentionDeadline,
          acceptedAnalysisAuthority: accepted,
        });
        controller.settleAnalysis();
        publishController();
        if (
          isCurrentRequest(
            generation,
            requestGenerationRef.current,
            signal,
          )
        ) {
          patchUi({ notice: "分析请求已提交。" });
        }
      } catch (requestError) {
        if (
          handleAuthoritativeGone(
            requestError,
            { pendingAnalysis: pending },
            generation,
            signal,
          )
        ) {
          return;
        }
        if (
          requestError instanceof ProductBriefApiCancelledError ||
          !isCurrentRequest(
            generation,
            requestGenerationRef.current,
            signal,
          )
        ) {
          return;
        }
        if (isDefinitiveNonRetryableClientError(requestError)) {
          const persisted = readPersisted();
          if (
            persisted?.schemaVersion === 2 &&
            pendingAnalysesMatch(
              persisted.pendingAnalysis,
              pending,
            ) &&
            pending.priorProductBrief !== null
          ) {
            if (
              !writePersisted({
                schemaVersion: 1,
                workspaceId,
                productId,
                productBriefId:
                  pending.priorProductBrief.productBriefId,
                operationId:
                  pending.priorProductBrief.operationId,
                workflowId: persisted.workflowId,
                assetVersionIds: persisted.assetVersionIds,
                retentionDeadline:
                  persisted.retentionDeadline,
              })
            ) {
              return;
            }
          } else {
            clearPersisted();
          }
          controller.settleAnalysis();
          publishController();
          patchUi({ error: productBriefErrorMessage(requestError) });
        } else {
          controller.recoverAnalysis(pending);
          publishController();
          patchUi({
            error:
              "分析请求结果未知。可使用原请求标识安全重试。",
          });
        }
      } finally {
        if (generation === requestGenerationRef.current) {
          patchUi({ busy: null });
        }
      }
    },
    [
      api,
      clearPersisted,
      controller,
      handleAuthoritativeGone,
      loadCurrent,
      patchUi,
      productId,
      publishController,
      readPersisted,
      writePersisted,
      workspaceId,
    ],
  );

  const recoverRevisionConflict = useCallback(
    async (
      command: Extract<PendingProductBriefCommand, { kind: "revise" }>,
      generation = requestGenerationRef.current,
      signal = requestControllerRef.current?.signal,
    ) => {
      if (!signal) return;
      patchUi({ busy: "revise", error: null });
      try {
        const latest = await loadCurrent(command.productBriefId, {
          resetFormState: true,
          generation,
          signal,
        });
        if (
          isCurrentRequest(
            generation,
            requestGenerationRef.current,
            signal,
          )
        ) {
          patchUi({ notice: `已载入服务器版本 ${latest.version}。` });
        }
      } catch (requestError) {
        if (
          handleAuthoritativeGone(
            requestError,
            { productBriefId: command.productBriefId },
            generation,
            signal,
          )
        ) {
          return;
        }
        if (
          !(requestError instanceof ProductBriefApiCancelledError) &&
          isCurrentRequest(
            generation,
            requestGenerationRef.current,
            signal,
          )
        ) {
          patchUi({ error: productBriefErrorMessage(requestError) });
        }
      } finally {
        if (generation === requestGenerationRef.current) {
          patchUi({ busy: null });
        }
      }
    },
    [handleAuthoritativeGone, loadCurrent, patchUi],
  );

  const recoverRevision = useCallback(
    async (
      command: Extract<PendingProductBriefCommand, { kind: "revise" }>,
      generation = requestGenerationRef.current,
      signal = requestControllerRef.current?.signal,
    ) => {
      if (!signal) return;
      const persistedAtStart = readPersisted();
      if (
        exactPendingProductBriefCommandIsDurable(
          controller.getSnapshot(),
          persistedAtStart,
          command,
          "version-conflict",
        )
      ) {
        await recoverRevisionConflict(command, generation, signal);
        return;
      }
      if (
        !exactPendingProductBriefCommandIsDurable(
          controller.getSnapshot(),
          persistedAtStart,
          command,
          "pending",
        )
      ) {
        patchUi({
          busy: null,
          error:
            "无法确认原修订请求的持久化身份，已停止自动重试。",
        });
        return;
      }
      patchUi({ busy: "revise", error: null });
      const read = controller.beginBriefRead();
      try {
        const updated = await api.revise(
          command.productBriefId,
          command.payload,
          command.idempotencyKey,
          signal,
        );
        if (
          !isCurrentRequest(
            generation,
            requestGenerationRef.current,
            signal,
          )
        ) {
          return;
        }
        const persistedAtResponse = readPersisted();
        const revisionOperationTransition = {
          expectedProductBriefVersion:
            command.payload.expected_product_brief_version,
          baseVersionId: command.payload.base_version_id,
        };
        if (
          !exactPendingProductBriefCommandIsDurable(
            controller.getSnapshot(),
            persistedAtResponse,
            command,
            "pending",
          ) ||
          !productBriefRevisionResponseMatchesIdentity(updated, {
            workspaceId,
            productId: command.productId,
            productBriefId: command.productBriefId,
            workflowId: persistedAtResponse.workflowId,
            priorOperationId: persistedAtResponse.operationId,
            ...revisionOperationTransition,
          })
        ) {
          patchUi({
            error:
              "修订响应身份或持久化命令不匹配，请使用原请求标识重试。",
          });
          return;
        }
        const retentionDeadline = settlePendingCommand(
          command,
          updated,
          { revisionOperationTransition },
        );
        if (!retentionDeadline) return;
        const retainedUpdate = {
          ...updated,
          retention_deadline: retentionDeadline,
        };
        if (!controller.publishBrief(read, retainedUpdate, true)) return;
        publishController();
        resetForm({
          lookupId: retainedUpdate.id,
          workflowId: retainedUpdate.workflow_id,
          assetVersionIds: readPersisted()?.assetVersionIds ?? [],
        });
        await refreshAuxiliary(
          retainedUpdate,
          { clearUnavailable: false },
          generation,
          signal,
        );
        if (
          isCurrentRequest(
            generation,
            requestGenerationRef.current,
            signal,
          )
        ) {
          patchUi({
            notice: `人工版本 ${retainedUpdate.current_version?.version_number ?? ""} 已保存。`,
          });
        }
      } catch (requestError) {
        if (
          handleAuthoritativeGone(
            requestError,
            { productBriefId: command.productBriefId },
            generation,
            signal,
          )
        ) {
          return;
        }
        if (
          requestError instanceof ProductBriefApiCancelledError ||
          !isCurrentRequest(
            generation,
            requestGenerationRef.current,
            signal,
          )
        ) {
          return;
        }
        if (isDefinitiveNonRetryableClientError(requestError)) {
          if (
            requestError instanceof ProductBriefApiError &&
            requestError.envelope?.code === "VERSION_CONFLICT"
          ) {
            const persisted = readPersisted();
            if (
              !exactPendingProductBriefCommandIsDurable(
                controller.getSnapshot(),
                persisted,
                command,
                "pending",
              ) ||
              !writePersisted({
                ...persisted,
                commandStatus: "version-conflict",
              }) ||
              !controller.markVersionConflict(command)
            ) {
              patchUi({
                error:
                  "无法安全保存版本冲突草稿，修订已停止。",
              });
              return;
            }
            publishController();
            await recoverRevisionConflict(
              command,
              generation,
              signal,
            );
          } else if (settlePendingCommand(command)) {
            patchUi({ error: productBriefErrorMessage(requestError) });
          }
        } else {
          controller.recoverCommand(command, "pending");
          publishController();
          patchUi({
            error:
              "修订请求结果未知。可使用原请求标识安全重试。",
          });
        }
      } finally {
        if (generation === requestGenerationRef.current) {
          patchUi({ busy: null });
        }
      }
    },
    [
      api,
      controller,
      handleAuthoritativeGone,
      patchUi,
      publishController,
      readPersisted,
      recoverRevisionConflict,
      refreshAuxiliary,
      resetForm,
      settlePendingCommand,
      writePersisted,
      workspaceId,
    ],
  );

  const recoverConfirmation = useCallback(
    async (
      command: Extract<PendingProductBriefCommand, { kind: "confirm" }>,
      generation = requestGenerationRef.current,
      signal = requestControllerRef.current?.signal,
    ) => {
      if (!signal) return;
      const persistedAtStart = readPersisted();
      if (
        !exactPendingProductBriefCommandIsDurable(
          controller.getSnapshot(),
          persistedAtStart,
          command,
          "pending",
        )
      ) {
        patchUi({
          busy: null,
          error:
            "无法确认原确认请求的持久化身份，已停止自动重试。",
        });
        return;
      }
      patchUi({ busy: "confirm", error: null });
      const read = controller.beginBriefRead();
      try {
        const confirmed = await api.confirm(
          command.productBriefId,
          command.payload,
          command.idempotencyKey,
          signal,
        );
        if (
          !isCurrentRequest(
            generation,
            requestGenerationRef.current,
            signal,
          )
        ) {
          return;
        }
        const current = confirmed.product_brief;
        const persistedAtResponse = readPersisted();
        if (
          !exactPendingProductBriefCommandIsDurable(
            controller.getSnapshot(),
            persistedAtResponse,
            command,
            "pending",
          ) ||
          !productBriefResponseMatchesIdentity(current, {
            workspaceId,
            productId: command.productId,
            productBriefId: command.productBriefId,
            workflowId: persistedAtResponse.workflowId,
            operationId: persistedAtResponse.operationId,
          }) ||
          confirmed.workflow_id !== current.workflow_id
        ) {
          patchUi({
            error:
              "确认响应身份或持久化命令不匹配，请使用原请求标识重试。",
          });
          return;
        }
        const retentionDeadline = settlePendingCommand(
          command,
          current,
        );
        if (!retentionDeadline) return;
        const retainedCurrent = {
          ...current,
          retention_deadline: retentionDeadline,
        };
        if (!controller.publishBrief(read, retainedCurrent, true)) return;
        publishController();
        resetForm({
          lookupId: retainedCurrent.id,
          workflowId: retainedCurrent.workflow_id,
          assetVersionIds: readPersisted()?.assetVersionIds ?? [],
        });
        await refreshAuxiliary(
          retainedCurrent,
          { clearUnavailable: false },
          generation,
          signal,
        );
        if (
          isCurrentRequest(
            generation,
            requestGenerationRef.current,
            signal,
          )
        ) {
          patchUi({
            notice: `版本 ${retainedCurrent.current_version?.version_number ?? ""} 已确认。`,
          });
        }
      } catch (requestError) {
        if (
          handleAuthoritativeGone(
            requestError,
            { productBriefId: command.productBriefId },
            generation,
            signal,
          )
        ) {
          return;
        }
        if (
          requestError instanceof ProductBriefApiCancelledError ||
          !isCurrentRequest(
            generation,
            requestGenerationRef.current,
            signal,
          )
        ) {
          return;
        }
        if (isDefinitiveNonRetryableClientError(requestError)) {
          if (!settlePendingCommand(command)) return;
          if (
            requestError instanceof ProductBriefApiError &&
            requestError.envelope?.code === "VERSION_CONFLICT"
          ) {
            try {
              const latest = await loadCurrent(command.productBriefId, {
                resetFormState: true,
                generation,
                signal,
              });
              patchUi({
                notice: `已载入服务器版本 ${latest.version}。`,
              });
            } catch (reloadError) {
              if (
                !(reloadError instanceof ProductBriefApiCancelledError)
              ) {
                patchUi({
                  error: productBriefErrorMessage(reloadError),
                });
              }
            }
          } else {
            patchUi({ error: productBriefErrorMessage(requestError) });
          }
        } else {
          controller.recoverCommand(command, "pending");
          publishController();
          patchUi({
            error:
              "确认请求结果未知。可使用原请求标识安全重试。",
          });
        }
      } finally {
        if (generation === requestGenerationRef.current) {
          patchUi({ busy: null });
        }
      }
    },
    [
      api,
      controller,
      handleAuthoritativeGone,
      loadCurrent,
      patchUi,
      publishController,
      readPersisted,
      refreshAuxiliary,
      resetForm,
      settlePendingCommand,
      workspaceId,
    ],
  );

  useEffect(() => {
    const browserStorages = acquireProductBriefBrowserStorages();
    if (browserStorages.localStorage) {
      clearProductBriefPersistenceNamespace(browserStorages.localStorage);
    }
    const identity = persistenceIdentity;
    if (!browserStorages.sessionStorage) {
      requestControllerRef.current?.abort();
      requestControllerRef.current = null;
      requestGenerationRef.current += 1;
      retentionRef.current?.dispose();
      retentionRef.current = null;
      storageRef.current = null;
      controller.restartIdentity(identity);
      publishController();
      resetForm();
      setUi({
        ...EMPTY_UI,
        busy: null,
        error:
          "浏览器拒绝访问会话存储，商品理解内存状态已清除并停止自动恢复。",
      });
      return () => {
        retentionRef.current?.dispose();
      };
    }
    storageRef.current = browserStorages.sessionStorage;
    const sessionStorage = browserStorages.sessionStorage;
    const identityActivated =
      activateProductBriefPersistenceIdentity(sessionStorage, {
        workspaceId,
        productId,
      });
    retentionRef.current?.dispose();
    retentionRef.current = createProductBriefRetentionController({
      storage: sessionStorage,
      onExpired: (expiredIdentity) => {
        const activeIdentity = controller.getSnapshot().identity;
        if (
          expiredIdentity.workspaceId !== activeIdentity.workspaceId ||
          expiredIdentity.productId !== activeIdentity.productId
        ) {
          return;
        }
        invalidateRetainedWorkbench();
      },
    });
    retentionRef.current.changeIdentity(identity);
    sweepExpiredPersistedProductBriefs(sessionStorage);
    requestControllerRef.current?.abort();
    const requestController = new AbortController();
    requestControllerRef.current = requestController;
    requestGenerationRef.current += 1;
    const generation = requestGenerationRef.current;
    const signal = requestController.signal;
    controller.restartIdentity(identity);
    publishController();
    resetForm();
    setUi(EMPTY_UI);

    if (!identityActivated) {
      patchUi({
        busy: null,
        error:
          "浏览器无法安全确认商品理解恢复身份，已停止自动恢复。",
      });
      return () => {
        requestController.abort();
        retentionRef.current?.dispose();
      };
    }

    const persisted = readPersistedProductBrief(
      sessionStorage,
      persistenceIdentity,
    );
    if (!persisted) {
      patchUi({ busy: null });
      return () => {
        requestController.abort();
        retentionRef.current?.dispose();
      };
    }
    let retentionActivated = false;
    try {
      retentionActivated = retentionRef.current.activate(
        identity,
        persisted.retentionDeadline,
      );
    } catch {
      clearPersistedProductBrief(sessionStorage, identity);
    }
    if (!retentionActivated) {
      requestController.abort();
      retentionRef.current.dispose();
      controller.expireRetention();
      publishController();
      patchUi({
        busy: null,
        error:
          "商品理解任务已到保留期限，本地恢复数据已清除。",
      });
      return () => {
        requestController.abort();
        retentionRef.current?.dispose();
      };
    }
    resetForm({
      lookupId:
        persisted.schemaVersion === 1 ||
        persisted.schemaVersion === 3
          ? persisted.productBriefId
          : "",
      workflowId: persisted.workflowId,
      assetVersionIds: persisted.assetVersionIds,
    });
    if (persisted.schemaVersion === 2) {
      controller.recoverAnalysis(persisted.pendingAnalysis);
      publishController();
      void recoverAnalysis(
        persisted.pendingAnalysis,
        generation,
        signal,
      );
    } else if (persisted.schemaVersion === 3) {
      controller.recoverCommand(
        persisted.pendingCommand,
        persisted.commandStatus,
      );
      publishController();
      if (persisted.pendingCommand.kind === "revise") {
        void recoverRevision(
          persisted.pendingCommand,
          generation,
          signal,
        );
      } else {
        void recoverConfirmation(
          persisted.pendingCommand,
          generation,
          signal,
        );
      }
    } else {
      void loadCurrent(persisted.productBriefId, {
        resetFormState: true,
        generation,
        signal,
      })
        .catch((requestError) => {
          if (
            !(requestError instanceof ProductBriefApiCancelledError) &&
            isCurrentRequest(
              generation,
              requestGenerationRef.current,
              signal,
            )
          ) {
            patchUi({
              error: productBriefErrorMessage(requestError),
            });
          }
        })
        .finally(() => {
          if (generation === requestGenerationRef.current) {
            patchUi({ busy: null });
          }
        });
    }
    return () => {
      requestController.abort();
      retentionRef.current?.dispose();
    };
  }, [
    controller,
    invalidateRetainedWorkbench,
    loadCurrent,
    patchUi,
    persistenceIdentity,
    productId,
    publishController,
    recoverAnalysis,
    recoverConfirmation,
    recoverRevision,
    resetForm,
    workspaceId,
  ]);

  const pollOperation = useCallback(
    async (
      identityGeneration: number,
      operationId: string,
      signal: AbortSignal,
      generation: number,
    ) => {
      if (
        identityGeneration !==
          controller.getSnapshot().identityGeneration ||
        !isCurrentRequest(
          generation,
          requestGenerationRef.current,
          signal,
        )
      ) {
        return;
      }
      const currentBrief = controller.getSnapshot().brief;
      if (
        !currentBrief ||
        currentBrief.operation_id !== operationId
      ) {
        return;
      }
      const read = controller.beginOperationRead(operationId);
      try {
        const operation = await api.getOperation(
          currentBrief.id,
          operationId,
          signal,
        );
        if (
          !isCurrentRequest(
            generation,
            requestGenerationRef.current,
            signal,
          ) ||
          !controller.completeOperationPoll(read, operation)
        ) {
          return;
        }
        publishController();
        patchUi({ pollingWarning: null });
        if (isProductBriefOperationPollTerminal(operation.state)) {
          await loadCurrent(currentBrief.id, {
            resetFormState: true,
            generation,
            signal,
          });
        }
      } catch (requestError) {
        if (
          handleAuthoritativeGone(
            requestError,
            { productBriefId: currentBrief.id },
            generation,
            signal,
          )
        ) {
          return;
        }
        if (
          requestError instanceof ProductBriefApiCancelledError ||
          !isCurrentRequest(
            generation,
            requestGenerationRef.current,
            signal,
          ) ||
          !controller.failOperationPoll(read)
        ) {
          return;
        }
        publishController();
        patchUi({
          pollingWarning: `任务状态刷新暂时失败：${productBriefErrorMessage(requestError)}`,
        });
      }
    },
    [
      api,
      controller,
      handleAuthoritativeGone,
      loadCurrent,
      patchUi,
      publishController,
    ],
  );

  const snapshot = controller.getSnapshot();
  useEffect(() => {
    const { polling, identityGeneration } =
      controller.getSnapshot();
    const signal = requestControllerRef.current?.signal;
    const generation = requestGenerationRef.current;
    if (
      !signal ||
      polling.operationId === null ||
      polling.nextDelayMs === null ||
      polling.paused
    ) {
      return;
    }
    const timer = setTimeout(
      () =>
        void pollOperation(
          identityGeneration,
          polling.operationId as string,
          signal,
          generation,
        ),
      polling.nextDelayMs,
    );
    return () => clearTimeout(timer);
  }, [
    controller,
    pollOperation,
    snapshot.identityGeneration,
    snapshot.polling.nextDelayMs,
    snapshot.polling.operationId,
    snapshot.polling.paused,
    snapshot.polling.requestToken,
  ]);

  const analyze = useCallback(
    async ({ workflowId, assetVersionIds }: AnalysisInput) => {
      const signal = requestControllerRef.current?.signal;
      const generation = requestGenerationRef.current;
      const currentSnapshot = controller.getSnapshot();
      if (
        !signal ||
        currentSnapshot.pendingAnalysis ||
        currentSnapshot.pendingCommand
      ) {
        return;
      }
      patchUi({
        busy: "analyze",
        error: null,
        notice: null,
      });
      try {
        const current = controller.getSnapshot().brief;
        const workflow = current
          ? await api.getWorkflowContext(
              workflowId,
              current.id,
              signal,
            )
          : await api.getAnalysisWorkflowContext(workflowId, signal);
        if (
          !isCurrentRequest(
            generation,
            requestGenerationRef.current,
            signal,
          )
        ) {
          return;
        }
        if (
          !productBriefWorkflowContextMatchesIdentity(workflow, {
            requestedWorkflowId: workflowId,
            ...(current
              ? { boundWorkflowId: current.workflow_id }
              : {}),
          })
        ) {
          patchUi({
            error:
              "工作流上下文身份不匹配，分析请求未提交。",
          });
          return;
        }
        const persisted = readPersisted();
        const retainedEstablishedDeadline =
          current !== null &&
          (persisted?.schemaVersion === 1 ||
            persisted?.schemaVersion === 3) &&
          persisted.productBriefId === current.id &&
          persisted.operationId === current.operation_id
            ? persisted.retentionDeadline
            : null;
        const retentionDeadline =
          earliestActiveProductBriefRetentionDeadline(
            workflow.retention_deadline,
            ...(current !== null
              ? [current.retention_deadline]
              : []),
            ...(retainedEstablishedDeadline !== null
              ? [retainedEstablishedDeadline]
              : []),
          );
        if (retentionDeadline === null) {
          patchUi({
            error:
              "无法确认商品理解任务的有效保留期限。",
          });
          return;
        }
        const pending: PendingProductBriefAnalysis = {
          payload: {
            workflow_id: workflow.id,
            product_id: productId,
            asset_version_ids: assetVersionIds,
            expected_workflow_version: workflow.version,
          },
          idempotencyKey: newProductBriefIdempotencyKey("analyze"),
          priorProductBrief:
            current === null
              ? null
              : {
                  productBriefId: current.id,
                  operationId: current.operation_id,
                },
        };
        if (
          !writePersisted({
            schemaVersion: 2,
            workspaceId,
            productId,
            workflowId: workflow.id,
            assetVersionIds,
            retentionDeadline,
            pendingAnalysis: pending,
          })
        ) {
          patchUi({
            error:
              "任务已过保留期限，或浏览器无法安全保存恢复状态。",
          });
          return;
        }
        controller.recoverAnalysis(pending);
        publishController();
        await recoverAnalysis(pending, generation, signal);
      } catch (requestError) {
        if (
          handleAuthoritativeGone(
            requestError,
            { workflowId },
            generation,
            signal,
          )
        ) {
          return;
        }
        if (
          !(requestError instanceof ProductBriefApiCancelledError) &&
          isCurrentRequest(
            generation,
            requestGenerationRef.current,
            signal,
          )
        ) {
          patchUi({ error: productBriefErrorMessage(requestError) });
        }
      } finally {
        if (generation === requestGenerationRef.current) {
          patchUi({ busy: null });
        }
      }
    },
    [
      api,
      controller,
      handleAuthoritativeGone,
      patchUi,
      productId,
      publishController,
      readPersisted,
      recoverAnalysis,
      writePersisted,
      workspaceId,
    ],
  );

  const load = useCallback(
    async (productBriefId: string) => {
      const signal = requestControllerRef.current?.signal;
      const generation = requestGenerationRef.current;
      const currentSnapshot = controller.getSnapshot();
      if (
        !signal ||
        currentSnapshot.pendingAnalysis ||
        currentSnapshot.pendingCommand
      ) {
        return;
      }
      patchUi({ busy: "load", error: null });
      try {
        await loadCurrent(productBriefId, {
          resetFormState: true,
          generation,
          signal,
        });
      } catch (requestError) {
        if (
          handleAuthoritativeGone(
            requestError,
            { productBriefId },
            generation,
            signal,
          )
        ) {
          return;
        }
        if (
          !(requestError instanceof ProductBriefApiCancelledError) &&
          isCurrentRequest(
            generation,
            requestGenerationRef.current,
            signal,
          )
        ) {
          patchUi({ error: productBriefErrorMessage(requestError) });
        }
      } finally {
        if (generation === requestGenerationRef.current) {
          patchUi({ busy: null });
        }
      }
    },
    [controller, handleAuthoritativeGone, loadCurrent, patchUi],
  );

  const revise = useCallback(
    async (payload: ProductBriefRevisionRequestV1) => {
      const currentSnapshot = controller.getSnapshot();
      const current = currentSnapshot.brief;
      if (
        !current ||
        currentSnapshot.pendingAnalysis ||
        currentSnapshot.pendingCommand
      ) {
        return;
      }
      const command: Extract<
        PendingProductBriefCommand,
        { kind: "revise" }
      > = {
        schemaVersion: 1,
        kind: "revise",
        productId,
        productBriefId: current.id,
        payload,
        idempotencyKey: newProductBriefIdempotencyKey("revise"),
      };
      const persisted = readPersisted();
      const retainedDeadline =
        persistedRetentionDeadlineForProductBrief(persisted, current);
      const retentionDeadline =
        earliestActiveProductBriefRetentionDeadline(
          current.retention_deadline,
          ...(retainedDeadline === null ? [] : [retainedDeadline]),
        );
      if (
        !retentionDeadline ||
        !writePersisted({
          schemaVersion: 3,
          workspaceId,
          productId,
          productBriefId: current.id,
          operationId: current.operation_id,
          workflowId: current.workflow_id,
          assetVersionIds: persisted?.assetVersionIds ?? [],
          retentionDeadline,
          pendingCommand: command,
          commandStatus: "pending",
        })
      ) {
        patchUi({
          error:
            "任务已过保留期限，或浏览器无法安全保存修订恢复状态。",
        });
        return;
      }
      controller.recoverCommand(command, "pending");
      publishController();
      patchUi({ notice: null });
      await recoverRevision(command);
    },
    [
      controller,
      patchUi,
      productId,
      publishController,
      readPersisted,
      recoverRevision,
      writePersisted,
      workspaceId,
    ],
  );

  const confirm = useCallback(
    async ({ reasonCode, commentRef }: ConfirmationInput) => {
      const currentSnapshot = controller.getSnapshot();
      const current = currentSnapshot.brief;
      const currentVersion = current?.current_version;
      const signal = requestControllerRef.current?.signal;
      const generation = requestGenerationRef.current;
      if (
        !current ||
        !currentVersion ||
        !signal ||
        currentSnapshot.pendingAnalysis ||
        currentSnapshot.pendingCommand
      ) {
        return;
      }
      patchUi({
        busy: "confirm",
        error: null,
        notice: null,
      });
      try {
        const workflow = await api.getWorkflowContext(
          current.workflow_id,
          current.id,
          signal,
        );
        if (
          !isCurrentRequest(
            generation,
            requestGenerationRef.current,
            signal,
          )
        ) {
          return;
        }
        if (
          !productBriefWorkflowContextMatchesIdentity(workflow, {
            requestedWorkflowId: current.workflow_id,
            boundWorkflowId: current.workflow_id,
          })
        ) {
          patchUi({
            error:
              "工作流上下文身份不匹配，确认请求未提交。",
          });
          return;
        }
        const persisted = readPersisted();
        const retainedDeadline =
          persistedRetentionDeadlineForProductBrief(
            persisted,
            current,
          );
        const retentionDeadline =
          earliestActiveProductBriefRetentionDeadline(
            workflow.retention_deadline,
            current.retention_deadline,
            ...(retainedDeadline === null
              ? []
              : [retainedDeadline]),
          );
        if (!retentionDeadline) {
          patchUi({
            error: "工作流与商品理解缺少共同有效的保留期限。",
          });
          return;
        }
        const payload: ProductBriefConfirmationRequestV1 = {
          expected_product_brief_version: current.version,
          product_brief_version_id: currentVersion.id,
          expected_workflow_version: workflow.version,
          reason_code: reasonCode,
          comment_ref: commentRef,
        };
        const command: Extract<
          PendingProductBriefCommand,
          { kind: "confirm" }
        > = {
          schemaVersion: 1,
          kind: "confirm",
          productId,
          productBriefId: current.id,
          payload,
          idempotencyKey:
            newProductBriefIdempotencyKey("confirm"),
        };
        if (
          !writePersisted({
            schemaVersion: 3,
            workspaceId,
            productId,
            productBriefId: current.id,
            operationId: current.operation_id,
            workflowId: current.workflow_id,
            assetVersionIds: persisted?.assetVersionIds ?? [],
            retentionDeadline,
            pendingCommand: command,
            commandStatus: "pending",
          })
        ) {
          patchUi({
            error:
              "任务已过保留期限，或浏览器无法安全保存确认恢复状态。",
          });
          return;
        }
        controller.recoverCommand(command, "pending");
        publishController();
        await recoverConfirmation(command, generation, signal);
      } catch (requestError) {
        if (
          handleAuthoritativeGone(
            requestError,
            { productBriefId: current.id },
            generation,
            signal,
          )
        ) {
          return;
        }
        if (
          !(requestError instanceof ProductBriefApiCancelledError) &&
          isCurrentRequest(
            generation,
            requestGenerationRef.current,
            signal,
          )
        ) {
          patchUi({ error: productBriefErrorMessage(requestError) });
        }
      } finally {
        if (generation === requestGenerationRef.current) {
          patchUi({ busy: null });
        }
      }
    },
    [
      api,
      controller,
      handleAuthoritativeGone,
      patchUi,
      productId,
      publishController,
      readPersisted,
      recoverConfirmation,
      writePersisted,
      workspaceId,
    ],
  );

  const refresh = useCallback(async () => {
    const currentSnapshot = controller.getSnapshot();
    const current = currentSnapshot.brief;
    const signal = requestControllerRef.current?.signal;
    const generation = requestGenerationRef.current;
    if (
      !current ||
      !signal ||
      currentSnapshot.pendingAnalysis ||
      (currentSnapshot.pendingCommand !== null &&
        currentSnapshot.commandStatus !== "version-conflict")
    ) {
      return;
    }
    patchUi({ busy: "refresh", error: null });
    try {
      await loadCurrent(current.id, {
        resetFormState: true,
        generation,
        signal,
      });
    } catch (requestError) {
      if (
        handleAuthoritativeGone(
          requestError,
          { productBriefId: current.id },
          generation,
          signal,
        )
      ) {
        return;
      }
      if (
        !(requestError instanceof ProductBriefApiCancelledError) &&
        isCurrentRequest(
          generation,
          requestGenerationRef.current,
          signal,
        )
      ) {
        patchUi({ error: productBriefErrorMessage(requestError) });
      }
    } finally {
      if (generation === requestGenerationRef.current) {
        patchUi({ busy: null });
      }
    }
  }, [controller, handleAuthoritativeGone, loadCurrent, patchUi]);

  const loadMoreVersions = useCallback(async () => {
    const current = controller.getSnapshot();
    const cursor = current.versionsNextCursor;
    const signal = requestControllerRef.current?.signal;
    const generation = requestGenerationRef.current;
    if (
      !current.brief ||
      cursor === null ||
      current.historyLoading !== null ||
      !signal
    ) {
      return;
    }
    const read = controller.beginHistoryRead(
      "more",
      current.brief.id,
    );
    publishController();
    patchUi({
      busy: "history-more",
      auxiliaryWarning: null,
    });
    try {
      const page = await api.listVersions(
        current.brief.id,
        {
          limit: PRODUCT_BRIEF_HISTORY_PAGE_SIZE,
          cursor,
        },
        signal,
      );
      if (
        isCurrentRequest(
          generation,
          requestGenerationRef.current,
          signal,
        )
      ) {
        if (controller.publishHistory(read, page)) {
          publishController();
        } else if (controller.failHistory(read, false)) {
          publishController();
          patchUi({
            auxiliaryWarning:
              "无法载入更多版本：服务器返回了无效的分页数据。",
          });
        }
      }
    } catch (requestError) {
      if (
        handleAuthoritativeGone(
          requestError,
          { productBriefId: current.brief.id },
          generation,
          signal,
        )
      ) {
        return;
      }
      if (
        !(requestError instanceof ProductBriefApiCancelledError) &&
        isCurrentRequest(
          generation,
          requestGenerationRef.current,
          signal,
        ) &&
        controller.failHistory(read, false)
      ) {
        publishController();
        patchUi({
          auxiliaryWarning: `无法载入更多版本：${productBriefErrorMessage(requestError)}`,
        });
      }
    } finally {
      if (generation === requestGenerationRef.current) {
        patchUi({ busy: null });
      }
    }
  }, [
    api,
    controller,
    handleAuthoritativeGone,
    patchUi,
    publishController,
  ]);

  const retryPending = useCallback(async () => {
    const current = controller.getSnapshot();
    if (current.pendingAnalysis) {
      await recoverAnalysis(current.pendingAnalysis);
    } else if (current.pendingCommand?.kind === "revise") {
      await recoverRevision(current.pendingCommand);
    } else if (current.pendingCommand?.kind === "confirm") {
      await recoverConfirmation(current.pendingCommand);
    }
  }, [controller, recoverAnalysis, recoverConfirmation, recoverRevision]);

  const resolveRevisionConflict = useCallback(
    (
      choice: "restore" | "discard",
    ): Extract<PendingProductBriefCommand, { kind: "revise" }> | null => {
      const current = controller.getSnapshot();
      const command =
        current.commandStatus === "version-conflict" &&
        current.pendingCommand?.kind === "revise"
          ? current.pendingCommand
          : null;
      if (
        !command ||
        !settlePendingCommand(command, current.brief ?? undefined, {
          commandStatus: "version-conflict",
        })
      ) {
        patchUi({
          error:
            "无法安全结算版本冲突草稿，请重新载入。",
        });
        return null;
      }
      patchUi({
        error: null,
        notice:
          choice === "restore"
            ? "本地草稿已恢复到服务器当前版本。"
            : "本地草稿已放弃。",
      });
      return choice === "restore" ? command : null;
    },
    [controller, patchUi, settlePendingCommand],
  );

  const resumePolling = useCallback(() => {
    if (controller.resumePolling()) {
      publishController();
      patchUi({ pollingWarning: null });
    }
  }, [controller, patchUi, publishController]);

  const reportError = useCallback(
    (message: string) => patchUi({ error: message }),
    [patchUi],
  );

  return {
    snapshot,
    formSeed,
    ui,
    analyze,
    load,
    revise,
    confirm,
    refresh,
    loadMoreVersions,
    retryPending,
    resolveRevisionConflict,
    resumePolling,
    reportError,
  };
}
