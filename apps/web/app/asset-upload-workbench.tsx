"use client";

import { ChangeEvent, useCallback, useEffect, useMemo, useState } from "react";

import {
  AssetApi,
  AssetApiError,
  newUploadIdempotencyKey,
  sha256Hex,
} from "../lib/asset-api";
import type { DurableOperationResponseV1 } from "../lib/asset-api";
import type {
  AssetResponseV1,
  OperationState,
  UploadFinalizeResponseV1,
  UploadSessionCreateRequestV1,
  UploadSessionCreateResponseV1,
  UploadSessionResponseV1,
} from "../lib/generated/catalog-api";
import {
  operationPollDelayMs,
  shouldContinueOperationPolling,
} from "../lib/operation-polling";
import { useUploadWorkflow } from "../lib/use-upload-workflow";
import type { PersistedSessionUpload } from "../lib/upload-workflow";

const api = new AssetApi();
const MAXIMUM_IMAGE_BYTES = 10 * 1024 * 1024;
const SUPPORTED_MIME_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);
const TERMINAL_OPERATION_STATES = new Set<OperationState>([
  "SUCCEEDED",
  "FAILED",
  "CANCELLED",
]);

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
}: {
  productId: string;
  categoryCode: string;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [role, setRole] = useState("product-primary");
  const [session, setSession] = useState<UploadSessionResponseV1 | null>(null);
  const [asset, setAsset] = useState<AssetResponseV1 | null>(null);
  const [finalized, setFinalized] = useState<UploadFinalizeResponseV1 | null>(
    null,
  );
  const [createdSession, setCreatedSession] =
    useState<UploadSessionCreateResponseV1 | null>(null);
  const [validationOperation, setValidationOperation] =
    useState<DurableOperationResponseV1 | null>(null);
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
    setValidationOperation(null);
    setProgress(0);
    setError(null);
    setBusy("recover");
    const current = loadPersisted();
    setRole(current?.createRequest?.role ?? "product-primary");
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

  useEffect(() => {
    const operationId = session?.validation_operation_id;
    setValidationOperation(null);
    setOperationPollingPaused(false);
    if (!operationId) return;

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
        const operation = await api.getOperation(operationId);
        completedRequests += 1;
        if (!active) return;
        setValidationOperation(operation);
        if (!TERMINAL_OPERATION_STATES.has(operation.state)) {
          scheduleNextPoll();
        }
      } catch {
        completedRequests += 1;
        if (active) scheduleNextPoll();
      }
    };
    void poll();

    return () => {
      active = false;
      if (nextPoll) clearTimeout(nextPoll);
    };
  }, [operationPollingEpoch, session?.validation_operation_id]);

  const selectFile = (event: ChangeEvent<HTMLInputElement>) => {
    const selected = event.target.files?.[0] ?? null;
    setError(null);
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    if (!selected) {
      setFile(null);
      setPreviewUrl(null);
      return;
    }
    if (!SUPPORTED_MIME_TYPES.has(selected.type)) {
      setFile(null);
      setPreviewUrl(null);
      setError("仅支持 JPEG、PNG 和 WebP 图片。");
      return;
    }
    if (selected.size < 1 || selected.size > MAXIMUM_IMAGE_BYTES) {
      setFile(null);
      setPreviewUrl(null);
      setError("图片大小必须在 1 字节到 10 MB 之间。");
      return;
    }
    setFile(selected);
    setPreviewUrl(URL.createObjectURL(selected));
  };

  const clearLocalUpload = () => {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    clearPersisted();
    setFile(null);
    setPreviewUrl(null);
    setSession(null);
    setCreatedSession(null);
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
          asset_kind: "IMAGE",
          filename: file.name,
          declared_mime: file.type,
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
        (current.createRequest.filename !== file.name ||
          current.createRequest.declared_mime !== file.type ||
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
    validationOperation?.state ?? finalized?.validation_operation.state ?? null;
  const displayAsset = asset ?? finalized?.asset ?? null;
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
    return version ? `${version.width} × ${version.height}` : null;
  }, [displayAsset]);

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
          ) : displayAsset?.current_version ? (
            <div className="asset-fact">
              <strong>{displayAsset.current_version.filename}</strong>
              <span>{dimensions}</span>
              <span>{displayAsset.current_version.image_format}</span>
            </div>
          ) : (
            <span className="muted">未选择图片</span>
          )}
        </div>

        <div className="asset-upload-controls">
          <label>
            <span>商品图片</span>
            <input
              accept="image/jpeg,image/png,image/webp"
              disabled={busy !== null || persisted?.stage === "CREATING"}
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
              <option value="product-primary">商品主图</option>
              <option value="product-reference">商品参考图</option>
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
      {error ? (
        <div className="error-banner" role="alert">
          <strong>素材未完成</strong>
          <span>{error}</span>
        </div>
      ) : null}
    </section>
  );
}
