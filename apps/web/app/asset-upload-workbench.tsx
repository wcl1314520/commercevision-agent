"use client";

import {
  ChangeEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  AssetApi,
  AssetApiError,
  newUploadIdempotencyKey,
  sha256Hex,
} from "../lib/asset-api";
import type {
  AssetKind,
  AssetIndexStatusResponseV1,
  AssetResponseV1,
  AssetValidationStageResponseV1,
  AssetValidationStatusResponseV1,
  OperationState,
  UploadFinalizeResponseV1,
  UploadSessionCreateRequestV1,
  UploadSessionCreateResponseV1,
  UploadSessionResponseV1,
  ValidationStage,
  ValidationVerdict,
} from "../lib/generated/catalog-api";
import {
  ASSET_UPLOAD_POLICIES,
  declaredMimeForAsset,
} from "../lib/asset-upload-policy";
import {
  operationPollDelayMs,
  shouldContinueOperationPolling,
} from "../lib/operation-polling";
import {
  acceptIndexStatusResponse,
  indexStatusRetryDelayMs,
  indexStatusPresentation,
  shouldRefreshIndexStatus,
} from "../lib/index-status-state";
import type { ProductBriefSourceSelection } from "../lib/product-brief-workbench-state";
import { useUploadWorkflow } from "../lib/use-upload-workflow";
import type { PersistedSessionUpload } from "../lib/upload-workflow";
import { validationPresentation } from "../lib/validation-presentation";
import { AssetRightsWorkbench } from "./asset-rights-workbench";

const api = new AssetApi();
const INDEX_AUTHORITY_GRACE_ATTEMPTS = 15;
const TERMINAL_OPERATION_STATES = new Set<OperationState>([
  "SUCCEEDED",
  "FAILED",
  "CANCELLED",
]);
const ASSET_KIND_LABELS: Record<AssetKind, string> = {
  IMAGE: "商品图片",
  LORA: "LoRA",
  PROMPT_TEMPLATE: "提示词模板",
  MODEL_CONFIGURATION: "模型配置",
};
const INDEX_STATUS_LABELS: Record<AssetIndexStatusResponseV1["state"], string> = {
  NOT_REQUESTED: "尚未请求",
  PENDING: "等待索引",
  PROCESSING: "正在索引",
  INDEXED: "可检索",
  RETRYABLE_FAILED: "等待重试",
  PERMANENT_FAILED: "索引失败",
  STALE: "已失效",
  DELETE_PENDING: "正在移除",
  DELETED: "已移除",
};

function indexStatusFingerprint(status: AssetIndexStatusResponseV1 | null): string | null {
  if (!status) return null;
  return JSON.stringify([
    status.asset_id,
    status.asset_version_id,
    status.state,
    status.retryable,
    status.failure_reason,
    status.indexed_at,
    status.updated_at,
  ]);
}
const ROLE_OPTIONS: Record<AssetKind, Array<{ label: string; value: string }>> = {
  IMAGE: [
    { label: "商品主图", value: "product-primary" },
    { label: "商品参考图", value: "product-reference" },
  ],
  LORA: [{ label: "生成 LoRA", value: "generation-lora" }],
  PROMPT_TEMPLATE: [
    { label: "生成提示词", value: "generation-prompt-template" },
  ],
  MODEL_CONFIGURATION: [
    { label: "生成模型配置", value: "generation-model-configuration" },
  ],
};

function formatByteSize(byteSize: number): string {
  if (byteSize >= 1024 * 1024) {
    return `${(byteSize / (1024 * 1024)).toFixed(1)} MB`;
  }
  if (byteSize >= 1024) {
    return `${(byteSize / 1024).toFixed(1)} KB`;
  }
  return `${byteSize} B`;
}
const VALIDATION_STAGE_LABELS: Record<ValidationStage, string> = {
  LOCAL_FORMAT: "本地格式",
  MALWARE: "恶意软件",
  CONTENT_SAFETY: "内容安全",
  PROVENANCE: "来源凭证",
  PROMOTION: "受控存储",
};
const VALIDATION_VERDICT_LABELS: Record<ValidationVerdict, string> = {
  PASS: "通过",
  REVIEW: "待复核",
  BLOCK: "拒绝",
  RETRYABLE_FAILURE: "待重试",
  TERMINAL_FAILURE: "系统失败",
  NOT_APPLICABLE: "不适用",
};

function scalarEvidence(
  evidence: Record<string, unknown>,
  key: string,
): string | null {
  const value = evidence[key];
  return typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
    ? String(value)
    : null;
}

function stageEvidenceSummary(stage: AssetValidationStageResponseV1): string[] {
  const evidence = stage.evidence;
  const facts =
    typeof evidence.facts === "object" && evidence.facts !== null
      ? (evidence.facts as Record<string, unknown>)
      : {};
  switch (stage.stage) {
    case "LOCAL_FORMAT": {
      const dimensions =
        scalarEvidence(facts, "width") && scalarEvidence(facts, "height")
          ? `${scalarEvidence(facts, "width")} × ${scalarEvidence(facts, "height")}`
          : null;
      return [
        scalarEvidence(evidence, "format_name"),
        scalarEvidence(evidence, "detected_mime"),
        dimensions,
        scalarEvidence(facts, "tensor_count")
          ? `${scalarEvidence(facts, "tensor_count")} tensors`
          : null,
        scalarEvidence(facts, "schema_version"),
      ].filter((value): value is string => value !== null);
    }
    case "MALWARE":
      return [
        scalarEvidence(evidence, "outcome"),
        scalarEvidence(evidence, "scanner_version"),
        scalarEvidence(evidence, "signature"),
      ].filter((value): value is string => value !== null);
    case "CONTENT_SAFETY": {
      const labels = Array.isArray(evidence.labels)
        ? evidence.labels
            .map((label) =>
              typeof label === "object" &&
              label !== null &&
              typeof (label as Record<string, unknown>).code === "string"
                ? String((label as Record<string, unknown>).code)
                : null,
            )
            .filter((value): value is string => value !== null)
        : [];
      return [
        scalarEvidence(evidence, "outcome"),
        scalarEvidence(evidence, "risk_level"),
        ...labels,
      ].filter((value): value is string => value !== null);
    }
    case "PROVENANCE":
      return [
        scalarEvidence(evidence, "status"),
        scalarEvidence(evidence, "validation_state"),
        scalarEvidence(evidence, "manifest_count")
          ? `${scalarEvidence(evidence, "manifest_count")} manifests`
          : null,
      ].filter((value): value is string => value !== null);
    case "PROMOTION":
      return [
        scalarEvidence(evidence, "destination_verified") === "true"
          ? "目标已核验"
          : null,
        scalarEvidence(evidence, "source_deleted") === "true"
          ? "隔离副本已清理"
          : null,
      ].filter((value): value is string => value !== null);
  }
}

function uploadMessage(error: unknown): string {
  if (error instanceof AssetApiError) {
    switch (error.envelope?.code) {
      case "UPLOAD_BUSY":
        return "登记仍在处理中，请稍后重试。";
      case "OBJECT_MISMATCH":
        return "文件校验未通过，请重新选择原始文件。";
      case "UPLOAD_OBJECT_MISSING":
        return "未找到完整上传文件，请重新选择原始文件后继续。";
      case "UPLOAD_EXPIRED":
        return "上传会话已过期，请重新上传。";
      case "VERSION_CONFLICT":
        return "上传状态已更新，正在读取服务器状态。";
      default:
        return error.envelope?.message ?? "素材请求失败。";
    }
  }
  return error instanceof Error ? error.message : "素材请求失败。";
}

function statusLabel(session: UploadSessionResponseV1 | null): string {
  switch (session?.status) {
    case "OPEN":
      return "等待上传";
    case "FINALIZING":
      return "正在登记";
    case "FINALIZED":
      return "隔离区";
    case "EXPIRED":
      return "已过期";
    case "ABORTED":
      return "已终止";
    default:
      return "未开始";
  }
}

function terminalSessionMessage(session: UploadSessionResponseV1): string | null {
  if (session.status === "EXPIRED") {
    return "上传会话已过期，请重新上传。";
  }
  if (session.status === "ABORTED") {
    return session.failure_code === "OBJECT_MISMATCH"
      ? "文件校验未通过，请重新选择原始文件。"
      : "上传会话已终止，请重新上传。";
  }
  return null;
}

export function AssetUploadWorkbench({
  productId,
  categoryCode,
  onSourceReady,
}: {
  productId: string;
  categoryCode: string;
  onSourceReady?: (source: ProductBriefSourceSelection | null) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [assetKind, setAssetKind] = useState<AssetKind>("IMAGE");
  const [role, setRole] = useState("product-primary");
  const [session, setSession] = useState<UploadSessionResponseV1 | null>(null);
  const [asset, setAsset] = useState<AssetResponseV1 | null>(null);
  const [finalized, setFinalized] = useState<UploadFinalizeResponseV1 | null>(
    null,
  );
  const [createdSession, setCreatedSession] =
    useState<UploadSessionCreateResponseV1 | null>(null);
  const [validationStatus, setValidationStatus] =
    useState<AssetValidationStatusResponseV1 | null>(null);
  const [validationControlError, setValidationControlError] =
    useState<string | null>(null);
  const [indexStatus, setIndexStatus] =
    useState<AssetIndexStatusResponseV1 | null>(null);
  const [indexStatusError, setIndexStatusError] = useState(false);
  const [indexStatusRefreshEpoch, setIndexStatusRefreshEpoch] = useState(0);
  const indexStatusRequestEpoch = useRef(0);
  const indexStatusFailureCount = useRef(0);
  const indexStatusAuthority = useRef<{ assetId: string; version: number } | null>(
    null,
  );
  const indexStatusAuthorityBaseline = useRef<string | null>(null);
  const indexStatusAuthorityRefreshBudget = useRef(0);
  const indexStatusLastAccepted = useRef<AssetIndexStatusResponseV1 | null>(null);
  const [operationPollingPaused, setOperationPollingPaused] = useState(false);
  const [operationPollingEpoch, setOperationPollingEpoch] = useState(0);
  const [progress, setProgress] = useState(0);
  const [busy, setBusy] = useState<string | null>("recover");
  const [error, setError] = useState<string | null>(null);
  const {
    clear: clearPersisted,
    load: loadPersisted,
    persisted,
    transition,
  } = useUploadWorkflow(productId);

  const finishFinalize = useCallback(
    (result: UploadFinalizeResponseV1, current: PersistedSessionUpload) => {
      setFinalized(result);
      setSession(result.upload_session);
      setAsset(result.asset);
      setAssetKind(result.upload_session.asset_kind);
      transition(current, {
        type: "FINALIZED",
        sessionId: result.upload_session.id,
        assetId: result.asset.id,
      });
    },
    [transition],
  );

  const resumeFinalize = useCallback(
    async (
      currentSession: UploadSessionResponseV1,
      current: PersistedSessionUpload,
    ) => {
      setBusy("finalize");
      setError(null);
      const finalizing = transition(current, { type: "FINALIZE_STARTED" });
      if (finalizing.stage === "CREATING") {
        throw new Error("finalize transition did not retain the upload session");
      }
      try {
        const result = await api.finalizeUploadSession(
          currentSession.id,
          finalizing.finalizeAttempt.request.expected_version,
          finalizing.finalizeAttempt.idempotencyKey,
        );
        finishFinalize(result, finalizing);
      } catch (requestError) {
        setError(uploadMessage(requestError));
        const versionConflict =
          requestError instanceof AssetApiError &&
          requestError.envelope?.code === "VERSION_CONFLICT";
        const uploadObjectMissing =
          requestError instanceof AssetApiError &&
          requestError.envelope?.code === "UPLOAD_OBJECT_MISSING";
        try {
          const refreshed = await api.getUploadSession(currentSession.id);
          setSession(refreshed);
          if (refreshed.status === "FINALIZED") {
            const recoveredAsset = await api.getAsset(
              refreshed.reserved_asset_id,
            );
            setAsset(recoveredAsset);
            transition(finalizing, {
              type: "FINALIZED",
              sessionId: refreshed.id,
              assetId: recoveredAsset.id,
            });
          } else if (
            versionConflict &&
            (refreshed.status === "OPEN" ||
              refreshed.status === "FINALIZING")
          ) {
            transition(finalizing, {
              type: "FINALIZE_RECONCILED",
              idempotencyKey: newUploadIdempotencyKey("finalize"),
              expectedVersion: refreshed.version,
              nextStage: "FINALIZING",
            });
          } else if (uploadObjectMissing && refreshed.status === "OPEN") {
            transition(finalizing, {
              type: "FINALIZE_RECONCILED",
              idempotencyKey: newUploadIdempotencyKey("finalize"),
              expectedVersion: refreshed.version,
              nextStage: "OPEN",
            });
          }
        } catch {
          // Preserve the durable session identity for an explicit retry.
        }
      } finally {
        setBusy(null);
      }
    },
    [finishFinalize, transition],
  );

  useEffect(() => {
    let active = true;
    setFile(null);
    setPreviewUrl(null);
    setSession(null);
    setAsset(null);
    setFinalized(null);
    setCreatedSession(null);
    setValidationStatus(null);
    setValidationControlError(null);
    setProgress(0);
    setError(null);
    setBusy("recover");
    const current = loadPersisted();
    const recoveredKind = current?.createRequest?.asset_kind ?? "IMAGE";
    setAssetKind(recoveredKind);
    setRole(
      current?.createRequest?.role ?? ROLE_OPTIONS[recoveredKind][0].value,
    );
    if (!current) {
      setBusy(null);
      return () => {
        active = false;
      };
    }
    void (async () => {
      try {
        if (current.stage === "CREATING") {
          const recoveredCreate = await api.createUploadSession(
            current.createRequest,
            current.createIdempotencyKey,
          );
          if (!active) return;
          setCreatedSession(recoveredCreate);
          setSession(recoveredCreate);
          transition(current, {
            type: "SESSION_OPENED",
            sessionId: recoveredCreate.id,
            expectedVersion: recoveredCreate.version,
            finalizeIdempotencyKey: newUploadIdempotencyKey("finalize"),
          });
          return;
        }
        if (
          current.stage === "OPEN" &&
          current.createRequest &&
          current.createIdempotencyKey
        ) {
          const recoveredCreate = await api.createUploadSession(
            current.createRequest,
            current.createIdempotencyKey,
          );
          if (!active) return;
          setCreatedSession(recoveredCreate);
          setSession(recoveredCreate);
          transition(current, {
            type: "SESSION_OPENED",
            sessionId: recoveredCreate.id,
            expectedVersion: recoveredCreate.version,
          });
          return;
        }
        const recoveredSession = await api.getUploadSession(current.sessionId);
        if (!active) return;
        setSession(recoveredSession);
        setAssetKind(recoveredSession.asset_kind);
        setRole(recoveredSession.role);
        if (recoveredSession.status === "FINALIZED") {
          const recoveredAsset = await api.getAsset(
            recoveredSession.reserved_asset_id,
          );
          if (!active) return;
          setAsset(recoveredAsset);
          transition(current, {
            type: "FINALIZED",
            sessionId: recoveredSession.id,
            assetId: recoveredAsset.id,
          });
        } else if (
          (current.stage === "UPLOADING" ||
            current.stage === "UPLOADED" ||
            current.stage === "FINALIZING") &&
          (recoveredSession.status === "OPEN" ||
            recoveredSession.status === "FINALIZING")
        ) {
          await resumeFinalize(recoveredSession, current);
        } else {
          setError(terminalSessionMessage(recoveredSession));
        }
      } catch (requestError) {
        if (active) setError(uploadMessage(requestError));
      } finally {
        if (active) setBusy(null);
      }
    })();
    return () => {
      active = false;
    };
  }, [loadPersisted, productId, resumeFinalize, transition]);

  useEffect(
    () => () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    },
    [previewUrl],
  );

  const validationAssetId =
    asset?.id ??
    finalized?.asset.id ??
    (session?.status === "FINALIZED" ? session.reserved_asset_id : null);

  useEffect(() => {
    const operationId = session?.validation_operation_id;
    setValidationStatus(null);
    setValidationControlError(null);
    setOperationPollingPaused(false);
    if (!operationId || !validationAssetId) return;

    let active = true;
    let completedRequests = 0;
    let nextPoll: ReturnType<typeof setTimeout> | undefined;
    const scheduleNextPoll = () => {
      if (!shouldContinueOperationPolling(completedRequests)) {
        if (active) setOperationPollingPaused(true);
        return;
      }
      nextPoll = setTimeout(
        () => void poll(),
        operationPollDelayMs(completedRequests),
      );
    };
    const poll = async () => {
      try {
        const projection = await api.getAssetValidation(validationAssetId);
        if (!active) return;
        setValidationStatus(projection);
        setValidationControlError(null);
        const operationState: OperationState = projection.operation.state;
        completedRequests += 1;
        if (!TERMINAL_OPERATION_STATES.has(operationState)) {
          scheduleNextPoll();
        }
      } catch (requestError) {
        completedRequests += 1;
        if (active) {
          setValidationControlError(uploadMessage(requestError));
          scheduleNextPoll();
        }
      }
    };
    void poll();

    return () => {
      active = false;
      if (nextPoll) clearTimeout(nextPoll);
    };
  }, [
    operationPollingEpoch,
    session?.validation_operation_id,
    validationAssetId,
  ]);

  useEffect(() => {
    if (
      !validationAssetId ||
      !validationStatus ||
      !["PENDING_RIGHTS", "AVAILABLE", "BLOCKED", "RIGHTS_EXPIRED"].includes(
        validationStatus.asset_status,
      )
    ) {
      return;
    }
    let active = true;
    void api
      .getAsset(validationAssetId)
      .then((current) => {
        if (active) setAsset(current);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [validationAssetId, validationStatus]);

  const selectFile = (event: ChangeEvent<HTMLInputElement>) => {
    const selected = event.target.files?.[0] ?? null;
    setError(null);
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    if (!selected) {
      setFile(null);
      setPreviewUrl(null);
      return;
    }
    try {
      declaredMimeForAsset(assetKind, selected);
    } catch (selectionError) {
      setFile(null);
      setPreviewUrl(null);
      setError(uploadMessage(selectionError));
      return;
    }
    setFile(selected);
    setPreviewUrl(
      assetKind === "IMAGE" ? URL.createObjectURL(selected) : null,
    );
  };

  const changeAssetKind = (nextKind: AssetKind) => {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setAssetKind(nextKind);
    setRole(ROLE_OPTIONS[nextKind][0].value);
    setFile(null);
    setPreviewUrl(null);
    setError(null);
    setProgress(0);
  };

  const clearLocalUpload = () => {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    clearPersisted();
    setFile(null);
    setPreviewUrl(null);
    setSession(null);
    setCreatedSession(null);
    setAssetKind("IMAGE");
    setRole(ROLE_OPTIONS.IMAGE[0].value);
    setProgress(0);
    setError(null);
    setBusy(null);
  };

  const abandonUpload = async () => {
    const current = persisted;
    if (current && current.stage !== "CREATING") {
      const abortIdempotencyKey =
        current.abortIdempotencyKey ?? newUploadIdempotencyKey("abort");
      const abortable = transition(current, {
        type: "ABORT_KEY_ASSIGNED",
        idempotencyKey: abortIdempotencyKey,
      });
      if (abortable.stage === "CREATING") {
        throw new Error("abort transition did not retain the upload session");
      }
      const expectedVersion =
        session?.id === current.sessionId
          ? session.version
          : abortable.finalizeAttempt.request.expected_version;
      if (
        session === null ||
        session.id !== current.sessionId ||
        (session.status !== "FINALIZED" &&
          session.status !== "ABORTED" &&
          session.status !== "EXPIRED")
      ) {
        setBusy("abort");
        setError(null);
        try {
          await api.abortUploadSession(
            current.sessionId,
            expectedVersion,
            abortIdempotencyKey,
          );
        } catch (requestError) {
          setError(uploadMessage(requestError));
          setBusy(null);
          return;
        }
      }
    }
    clearLocalUpload();
  };

  const beginUpload = async () => {
    if (!file) return;
    setBusy("hash");
    setError(null);
    setProgress(0);
    try {
      const declaredMime = declaredMimeForAsset(assetKind, file);
      const checksum = await sha256Hex(file);
      let created: UploadSessionCreateResponseV1;
      let current: PersistedSessionUpload;
      if (persisted?.stage === "CREATING") {
        created = await api.createUploadSession(
          persisted.createRequest,
          persisted.createIdempotencyKey,
        );
        const opened = transition(persisted, {
          type: "SESSION_OPENED",
          sessionId: created.id,
          expectedVersion: created.version,
          finalizeIdempotencyKey: newUploadIdempotencyKey("finalize"),
        });
        if (opened.stage === "CREATING") {
          throw new Error("session transition did not establish the upload");
        }
        current = opened;
      } else if (persisted?.stage === "OPEN") {
        if (createdSession?.id === persisted.sessionId) {
          created = createdSession;
        } else if (
          persisted.createRequest &&
          persisted.createIdempotencyKey
        ) {
          created = await api.createUploadSession(
            persisted.createRequest,
            persisted.createIdempotencyKey,
          );
        } else {
          throw new Error(
            "无法恢复原上传请求，请放弃本次上传后重新开始。",
          );
        }
        const opened = transition(persisted, {
          type: "SESSION_OPENED",
          sessionId: created.id,
          expectedVersion: created.version,
        });
        if (opened.stage === "CREATING") {
          throw new Error("session transition did not retain the upload");
        }
        current = opened;
      } else {
        const createRequest: UploadSessionCreateRequestV1 = {
          retention_class: "FOUNDATION",
          asset_kind: assetKind,
          filename: file.name,
          declared_mime: declaredMime,
          byte_length: file.size,
          sha256: checksum,
          workflow_id: null,
          product_id: productId,
          sku_id: null,
          category: categoryCode,
          role,
        };
        const pendingCreate = transition(null, {
          type: "CREATE_STARTED",
          createIdempotencyKey: newUploadIdempotencyKey("create"),
          createRequest,
        });
        if (pendingCreate.stage !== "CREATING") {
          throw new Error("create transition did not start the upload");
        }
        created = await api.createUploadSession(
          pendingCreate.createRequest,
          pendingCreate.createIdempotencyKey,
        );
        const opened = transition(pendingCreate, {
          type: "SESSION_OPENED",
          sessionId: created.id,
          expectedVersion: created.version,
          finalizeIdempotencyKey: newUploadIdempotencyKey("finalize"),
        });
        if (opened.stage === "CREATING") {
          throw new Error("session transition did not establish the upload");
        }
        current = opened;
      }
      if (
        current.createRequest &&
        (current.createRequest.asset_kind !== assetKind ||
          current.createRequest.filename !== file.name ||
          current.createRequest.declared_mime !== declaredMime ||
          current.createRequest.byte_length !== file.size ||
          current.createRequest.sha256 !== checksum)
      ) {
        throw new Error("请选择与待恢复上传请求完全相同的原始文件。");
      }
      setCreatedSession(created);
      setSession(created);
      const uploading = transition(current, {
        type: "UPLOAD_STAGE_CHANGED",
        stage: "UPLOADING",
      });
      if (uploading.stage === "CREATING") {
        throw new Error("upload transition lost the established session");
      }
      setBusy("upload");
      await api.uploadDirect(created.upload, file, setProgress);
      const uploaded = transition(uploading, {
        type: "UPLOAD_STAGE_CHANGED",
        stage: "UPLOADED",
      });
      if (uploaded.stage === "CREATING") {
        throw new Error("upload transition lost the established session");
      }
      await resumeFinalize(created, uploaded);
    } catch (requestError) {
      setError(uploadMessage(requestError));
      setBusy(null);
    }
  };

  const operationState =
    validationStatus?.operation.state ??
    finalized?.validation_operation.state ??
    null;
  const displayAsset = asset ?? finalized?.asset ?? null;
  const indexAssetId =
    displayAsset?.asset_kind === "IMAGE" ? displayAsset.id : null;
  useEffect(() => {
    let active = true;
    let refreshTimer: ReturnType<typeof setTimeout> | null = null;
    setIndexStatus((current) =>
      current?.asset_id === indexAssetId ? current : null,
    );
    setIndexStatusError(false);
    if (!indexAssetId) {
      indexStatusAuthority.current = null;
      indexStatusAuthorityBaseline.current = null;
      indexStatusAuthorityRefreshBudget.current = 0;
      indexStatusLastAccepted.current = null;
      return () => {
        active = false;
      };
    }
    const authorityVersion = displayAsset?.version ?? 0;
    const previousAuthority = indexStatusAuthority.current;
    if (previousAuthority?.assetId === indexAssetId) {
      if (previousAuthority.version !== authorityVersion) {
        indexStatusAuthorityBaseline.current = indexStatusFingerprint(
          indexStatusLastAccepted.current,
        );
        indexStatusAuthorityRefreshBudget.current =
          INDEX_AUTHORITY_GRACE_ATTEMPTS;
      }
    } else {
      indexStatusAuthorityBaseline.current = null;
      indexStatusAuthorityRefreshBudget.current =
        INDEX_AUTHORITY_GRACE_ATTEMPTS;
      indexStatusLastAccepted.current = null;
    }
    indexStatusAuthority.current = {
      assetId: indexAssetId,
      version: authorityVersion,
    };
    const requestStatus = (): void => {
      const request = {
        assetId: indexAssetId,
        requestEpoch: ++indexStatusRequestEpoch.current,
      };
      void api
        .getAssetIndexStatus(indexAssetId)
        .then((status) => {
          if (!active) return;
          const accepted = acceptIndexStatusResponse(
            {
              assetId: indexAssetId,
              requestEpoch: indexStatusRequestEpoch.current,
            },
            request,
            status,
          );
          if (!accepted) return;
          indexStatusFailureCount.current = 0;
          setIndexStatusError(false);
          indexStatusLastAccepted.current = accepted;
          setIndexStatus(accepted);
          const shouldRefresh = shouldRefreshIndexStatus(accepted.state);
          if (shouldRefresh) {
            indexStatusAuthorityRefreshBudget.current = 0;
          } else if (indexStatusAuthorityRefreshBudget.current > 0) {
            const firstAuthorityGraceResponse =
              indexStatusAuthorityRefreshBudget.current ===
              INDEX_AUTHORITY_GRACE_ATTEMPTS;
            if (
              firstAuthorityGraceResponse &&
              indexStatusAuthorityBaseline.current === null
            ) {
              indexStatusAuthorityBaseline.current =
                indexStatusFingerprint(accepted);
            }
            const authorityProjectionChanged =
              !firstAuthorityGraceResponse &&
              indexStatusFingerprint(accepted) !==
                indexStatusAuthorityBaseline.current;
            if (authorityProjectionChanged) {
              indexStatusAuthorityRefreshBudget.current = 0;
            } else {
              indexStatusAuthorityRefreshBudget.current -= 1;
            }
          }
          if (shouldRefresh || indexStatusAuthorityRefreshBudget.current > 0) {
            refreshTimer = setTimeout(requestStatus, 2_000);
          }
        })
        .catch(() => {
          if (!active) return;
          setIndexStatus(null);
          setIndexStatusError(true);
          indexStatusFailureCount.current += 1;
          refreshTimer = setTimeout(
            requestStatus,
            indexStatusRetryDelayMs(indexStatusFailureCount.current),
          );
        });
    };
    requestStatus();
    return () => {
      active = false;
      if (refreshTimer !== null) clearTimeout(refreshTimer);
    };
  }, [displayAsset?.version, indexAssetId, indexStatusRefreshEpoch]);
  useEffect(() => {
    if (
      displayAsset?.asset_kind === "IMAGE" &&
      displayAsset.status === "AVAILABLE" &&
      displayAsset.product_id === productId &&
      displayAsset.workflow_id &&
      displayAsset.current_version_id
    ) {
      onSourceReady?.({
        productId,
        workflowId: displayAsset.workflow_id,
        assetVersionId: displayAsset.current_version_id,
      });
      return;
    }
    onSourceReady?.(null);
  }, [
    displayAsset?.asset_kind,
    displayAsset?.current_version_id,
    displayAsset?.product_id,
    displayAsset?.status,
    displayAsset?.workflow_id,
    onSourceReady,
    productId,
  ]);
  const validationView = validationPresentation(
    validationStatus,
    operationState,
  );
  const indexStatusView = indexStatus
    ? indexStatusPresentation(indexStatus)
    : null;
  const canRetryFinalize =
    persisted !== null &&
      persisted.stage !== "CREATING" &&
    session !== null &&
    (session.status === "OPEN" || session.status === "FINALIZING") &&
    (persisted.stage === "UPLOADING" ||
      persisted.stage === "UPLOADED" ||
      persisted.stage === "FINALIZING");
  const dimensions = useMemo(() => {
    const version = displayAsset?.current_version;
    return version?.width !== null &&
      version?.width !== undefined &&
      version.height !== null &&
      version.height !== undefined
      ? `${version.width} × ${version.height}`
      : null;
  }, [displayAsset]);
  const uploadPolicy = ASSET_UPLOAD_POLICIES[assetKind];

  return (
    <section className="panel asset-upload-panel" aria-labelledby="asset-upload-heading">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">PRODUCT ASSETS</p>
          <h2 id="asset-upload-heading">商品素材</h2>
        </div>
        <span className={`asset-status asset-status-${session?.status ?? "NONE"}`}>
          {statusLabel(session)}
        </span>
      </div>

      <div className="asset-upload-layout">
        <div className="asset-preview" aria-label="素材预览">
          {previewUrl ? (
            // The preview is a short-lived local object URL and never traverses the API.
            // eslint-disable-next-line @next/next/no-img-element
            <img alt={file?.name ?? "待上传图片"} src={previewUrl} />
          ) : file ? (
            <div className="asset-fact">
              <strong>{file.name}</strong>
              <span>{ASSET_KIND_LABELS[assetKind]}</span>
              <span>{formatByteSize(file.size)}</span>
            </div>
          ) : displayAsset?.current_version ? (
            <div className="asset-fact">
              <strong>{displayAsset.current_version.filename}</strong>
              <span>{ASSET_KIND_LABELS[displayAsset.asset_kind]}</span>
              <span>
                {dimensions ??
                  formatByteSize(displayAsset.current_version.byte_size)}
              </span>
              <span>
                {displayAsset.current_version.image_format ??
                  displayAsset.current_version.detected_mime ??
                  displayAsset.current_version.declared_mime}
              </span>
            </div>
          ) : (
            <span className="muted">未选择文件</span>
          )}
        </div>

        <div className="asset-upload-controls">
          <label>
            <span>资产类型</span>
            <select
              disabled={
                busy !== null ||
                persisted?.stage === "CREATING" ||
                persisted?.stage === "OPEN"
              }
              onChange={(event) =>
                changeAssetKind(event.target.value as AssetKind)
              }
              value={assetKind}
            >
              <option value="IMAGE">商品图片</option>
              <option value="LORA">LoRA</option>
              <option value="PROMPT_TEMPLATE">提示词模板</option>
              <option value="MODEL_CONFIGURATION">模型配置</option>
            </select>
          </label>
          <label>
            <span>{uploadPolicy.label}</span>
            <input
              accept={uploadPolicy.accept}
              disabled={busy !== null || persisted?.stage === "CREATING"}
              key={assetKind}
              onChange={selectFile}
              type="file"
            />
          </label>
          <label>
            <span>素材角色</span>
            <select
              disabled={
                busy !== null ||
                persisted?.stage === "CREATING" ||
                persisted?.stage === "OPEN"
              }
              onChange={(event) => setRole(event.target.value)}
              value={role}
            >
              {ROLE_OPTIONS[assetKind].map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          {busy === "upload" || progress > 0 ? (
            <div className="upload-progress" aria-live="polite">
              <progress max={100} value={progress} />
              <span>{progress}%</span>
            </div>
          ) : null}
          <div className="form-actions">
            <button
              className="button button-primary"
              disabled={!file || busy !== null}
              onClick={() => void beginUpload()}
              type="button"
            >
              {busy === "hash"
                ? "正在校验"
                : busy === "upload"
                  ? "正在上传"
                  : busy === "finalize"
                    ? "正在登记"
                    : "上传并登记"}
            </button>
            {canRetryFinalize ? (
              <button
                className="button button-secondary"
                disabled={busy !== null}
                onClick={() => void resumeFinalize(session, persisted)}
                type="button"
              >
                重试登记
              </button>
            ) : null}
            {persisted?.stage === "CREATING" ||
            persisted?.stage === "OPEN" ? (
              <button
                className="button button-secondary"
                disabled={busy !== null}
                onClick={() => void abandonUpload()}
                type="button"
              >
                放弃本次上传
              </button>
            ) : null}
            {operationPollingPaused ? (
              <button
                className="button button-secondary"
                disabled={busy !== null}
                onClick={() => setOperationPollingEpoch((value) => value + 1)}
                type="button"
              >
                刷新校验状态
              </button>
            ) : null}
          </div>
        </div>
      </div>

      {session ? (
        <dl className="asset-facts">
          <div>
            <dt>文件</dt>
            <dd>{session.filename}</dd>
          </div>
          <div>
            <dt>隔离状态</dt>
            <dd>{displayAsset?.status ?? session.status}</dd>
          </div>
          <div>
            <dt>校验任务</dt>
            <dd>
              {operationState ??
                (session.validation_operation_id ? "正在读取" : "尚未创建")}
            </dd>
          </div>
        </dl>
      ) : null}
      {validationStatus?.stages.length ? (
        <section
          aria-labelledby="asset-validation-heading"
          className="asset-validation"
        >
          <div className="asset-validation-heading">
            <h3 id="asset-validation-heading">素材校验</h3>
            <span>{validationStatus.validation_policy_version}</span>
          </div>
          <ol className="validation-stages">
            {validationStatus.stages.map((stage) => {
              const summary = stageEvidenceSummary(stage);
              return (
                <li
                  className={`validation-stage validation-stage-${stage.verdict}`}
                  key={stage.id}
                >
                  <div>
                    <strong>{VALIDATION_STAGE_LABELS[stage.stage]}</strong>
                    <span>{VALIDATION_VERDICT_LABELS[stage.verdict]}</span>
                  </div>
                  <p>
                    {summary.length
                      ? summary.join(" · ")
                      : stage.reason_code ?? stage.validator_name}
                  </p>
                </li>
              );
            })}
          </ol>
        </section>
      ) : null}
      {validationView.kind === "retryable" ? (
        <div className="validation-banner validation-banner-retryable" role="status">
          <strong>校验暂时中断</strong>
          <span>
            系统将自动重试
            {validationView.reason
              ? `（${validationView.reason}）`
              : ""}
          </span>
        </div>
      ) : null}
      {validationView.kind === "review" ? (
        <div className="validation-banner validation-banner-review" role="status">
          <strong>等待人工复核</strong>
          <span>{validationView.reason ?? "素材将在复核完成后继续流转。"}</span>
        </div>
      ) : null}
      {validationView.kind === "rejected" ? (
        <div className="validation-banner validation-banner-rejected" role="alert">
          <strong>素材未通过校验</strong>
          <span>
            {validationView.reason ??
              "该素材不能进入可用资产库。"}
            {validationView.cleanup_retrying
              ? `；隔离文件清理正在自动重试${
                  validationView.cleanup_reason
                    ? `（${validationView.cleanup_reason}）`
                    : ""
                }`
              : ""}
          </span>
        </div>
      ) : null}
      {validationView.kind === "failed" ? (
        <div className="validation-banner validation-banner-failed" role="alert">
          <strong>校验无法完成</strong>
          <span>
            系统未能完成该素材的校验
            {validationView.reason ? `（${validationView.reason}）` : "。"}
          </span>
        </div>
      ) : null}
      {validationControlError ? (
        <div className="validation-banner validation-banner-control" role="alert">
          <strong>无法读取校验状态</strong>
          <span>{validationControlError}</span>
        </div>
      ) : null}
      {displayAsset?.asset_kind === "IMAGE" ? (
        <section
          aria-labelledby="asset-index-heading"
          className="asset-index-status"
        >
          <div>
            <p className="eyebrow">RETRIEVAL INDEX</p>
            <h3 id="asset-index-heading">图片检索索引</h3>
          </div>
          {indexStatusError ? (
            <div className="asset-index-status-recovery" role="status" aria-live="polite">
              <span className="asset-index-status-error">状态暂不可用，系统将自动重试</span>
              <button
                className="asset-index-status-retry"
                onClick={() =>
                  setIndexStatusRefreshEpoch((value) => value + 1)
                }
                type="button"
              >
                立即重试
              </button>
            </div>
          ) : (
            <span
              aria-live="polite"
              role="status"
              className={`asset-index-status-badge asset-index-status-${indexStatus?.state ?? "LOADING"}`}
            >
              {indexStatus ? INDEX_STATUS_LABELS[indexStatus.state] : "正在读取"}
            </span>
          )}
          {indexStatusView?.detail ? (
            <p className="asset-index-status-reason">
              {indexStatusView.detail}
            </p>
          ) : null}
        </section>
      ) : null}
      {displayAsset ? (
        <AssetRightsWorkbench asset={displayAsset} onAssetChange={setAsset} />
      ) : null}
      {error ? (
        <div className="error-banner" role="alert">
          <strong>素材未完成</strong>
          <span>{error}</span>
        </div>
      ) : null}
    </section>
  );
}
