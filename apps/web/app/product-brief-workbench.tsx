"use client";

import {
  FormEvent,
  useEffect,
  useMemo,
  useState,
} from "react";

import type {
  ProductBriefEvidenceRevisionV1,
  ProductBriefFieldConflict,
  ProductBriefFieldPath,
  ProductBriefFieldResponseV1,
  ProductBriefFieldRevisionV1,
  ProductBriefFieldValueV1,
  ProductBriefRevisionRequestV1,
  ProductBriefVersionResponseV1,
  ProductBriefVersionSummaryResponseV1,
} from "../lib/generated/catalog-api";
import { isProductBriefOperationPollTerminal } from "../lib/operation-polling";
import {
  isProductBriefFieldValueForPath,
  productBriefSourceFor,
  restoredProductBriefDrafts,
  structuredValueError,
} from "../lib/product-brief-workbench-state";
import type {
  PendingProductBriefCommand,
  ProductBriefSourceSelection,
} from "../lib/product-brief-workbench-state";
import { useProductBriefWorkbenchController } from "../lib/use-product-brief-workbench-controller";

export type { ProductBriefSourceSelection } from "../lib/product-brief-workbench-state";

type FieldDraft = {
  path: ProductBriefFieldPath;
  valueText: string;
  confidence: number;
  conflict: ProductBriefFieldConflict;
  reviewRequired: boolean;
  sensitive: boolean;
  evidence: ProductBriefEvidenceRevisionV1[];
};

type StaleDraft = {
  command: Extract<PendingProductBriefCommand, { kind: "revise" }>;
  fields: Record<string, FieldDraft>;
  reason: string;
  versionId: string;
};

const FIELD_LABELS: Record<string, string> = {
  "common.identity": "商品识别",
  "common.category": "商品分类",
  "common.brand": "品牌",
  "common.product_type": "商品类型",
  "common.package_or_part_form": "包装或零件形态",
  "common.material": "材质",
  "common.colors": "颜色",
  "common.visible_text_summary": "可见文字",
  "common.visual_features": "视觉特征",
  "common.usage_context": "使用场景",
  "common.prohibited_assumptions": "禁止推断",
  "common.sensitive_claims": "敏感声明",
  "common.source_conflicts": "来源冲突",
  "beauty.package_type": "包装类型",
  "beauty.cosmetic_form": "美妆剂型",
  "beauty.finish": "妆效",
  "beauty.texture": "质地",
  "beauty.shade_evidence": "色号证据",
  "beauty.ingredient_claim_evidence": "成分声明证据",
  "beauty.skin_hair_claim_flags": "皮肤或头发功效声明",
  "beauty.medical_like_claim_flags": "医疗类声明",
  "beauty.packaging_compliance_notes": "包装合规备注",
  "automotive.part_type": "配件类型",
  "automotive.placement": "车辆安装位置",
  "automotive.compatibility_evidence": "兼容性证据",
  "automotive.material": "配件材质",
  "automotive.finish": "表面处理",
  "automotive.dimensions_evidence": "尺寸证据",
  "automotive.installation_evidence": "安装证据",
  "automotive.safety_critical_claim_flags": "安全关键声明",
  "automotive.certification_marks": "可见认证标志",
};
const REVIEW_REASON_LABELS: Record<string, string> = {
  LOW_CONFIDENCE: "低置信度",
  MANDATORY_REVIEW: "强制人工复核",
  SOURCE_CONFLICT: "来源冲突",
  SENSITIVE_CLAIM: "敏感声明",
  PROVIDER_REVIEW: "模型要求复核",
};

function fieldLabel(path: string): string {
  return FIELD_LABELS[path] ?? path;
}

function valueText(value: ProductBriefFieldValueV1): string {
  return JSON.stringify(value, null, 2);
}

function draftsFor(
  version: ProductBriefVersionResponseV1 | null,
): Record<string, FieldDraft> {
  if (!version) return {};
  return Object.fromEntries(
    version.fields.map((field) => [
      field.path,
      {
        path: field.path,
        valueText: valueText(field.value),
        confidence: Number(field.confidence),
        conflict: field.conflict,
        reviewRequired: field.review_required,
        sensitive: field.sensitive,
        evidence: field.evidence.map(
          ({
            source_asset_version_id,
            kind,
            reference,
            region,
            excerpt_sha256,
          }) => ({
            source_asset_version_id,
            kind,
            reference,
            region,
            excerpt_sha256,
          }),
        ),
      } satisfies FieldDraft,
    ]),
  );
}

function parseAssetVersionIds(value: string): string[] {
  return [
    ...new Set(
      value
        .split(/[\s,]+/)
        .map((item) => item.trim())
        .filter(Boolean),
    ),
  ];
}

function parseDraftValue(draft: FieldDraft): ProductBriefFieldValueV1 {
  const value: unknown = JSON.parse(draft.valueText);
  if (!isProductBriefFieldValueForPath(draft.path, value)) {
    throw new Error(`字段值不符合契约：${fieldLabel(draft.path)}`);
  }
  return value;
}

function fieldValueControlId(path: string): string {
  return `brief-field-value-${path.replace(/[^A-Za-z0-9_-]/g, "-")}`;
}

function revisionFields(
  version: ProductBriefVersionResponseV1,
  drafts: Record<string, FieldDraft>,
): [ProductBriefFieldRevisionV1, ...ProductBriefFieldRevisionV1[]] {
  const fields = version.fields.map((field) => {
    const draft = drafts[field.path];
    if (!draft) throw new Error(`缺少字段：${fieldLabel(field.path)}`);
    const value = parseDraftValue(draft);
    return {
      path: draft.path,
      value,
      confidence: draft.confidence.toFixed(4),
      conflict: draft.conflict,
      review_required: draft.reviewRequired,
      sensitive: draft.sensitive,
      evidence: draft.evidence,
    } as ProductBriefFieldRevisionV1;
  });
  const firstField = fields[0];
  if (!firstField) throw new Error("商品简报没有可修订字段。");
  return [firstField, ...fields.slice(1)];
}

function staleDraftForRevisionCommand(
  command: Extract<PendingProductBriefCommand, { kind: "revise" }>,
): StaleDraft {
  return {
    command,
    fields: Object.fromEntries(
      command.payload.fields.map((field) => [
        field.path,
        {
          path: field.path,
          valueText: valueText(field.value),
          confidence: Number(field.confidence ?? 1),
          conflict: field.conflict ?? "NONE",
          reviewRequired: field.review_required ?? false,
          sensitive: field.sensitive,
          evidence: field.evidence,
        } satisfies FieldDraft,
      ]),
    ),
    reason: command.payload.reason,
    versionId: command.payload.base_version_id,
  };
}

function displayDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function stateLabel(state: string): string {
  const labels: Record<string, string> = {
    DRAFT: "等待分析",
    AWAITING_CONFIRMATION: "等待人工确认",
    CONFIRMED: "已确认",
    ARCHIVED: "已归档",
    PENDING: "等待执行",
    CLAIMED: "已领取",
    RUNNING: "分析中",
    RECONCILING: "结果核对中",
    WAITING_HUMAN: "等待人工确认",
    RETRYABLE_FAILED: "等待重试",
    SUCCEEDED: "已完成",
    FAILED: "失败",
    CANCELLED: "已取消",
  };
  return labels[state] ?? state;
}

function EvidenceList({
  path,
  evidence,
}: {
  path: ProductBriefFieldPath;
  evidence: ProductBriefEvidenceRevisionV1[];
}) {
  return (
    <ul
      className="brief-evidence-list"
      aria-label={`${fieldLabel(path)}证据`}
    >
      {evidence.map((item, index) => (
        <li key={`${item.kind}:${item.reference}:${index}`}>
          <div>
            <strong>{item.kind}</strong>
            <span>{item.source_asset_version_id}</span>
          </div>
          <p>{item.reference}</p>
          {item.region ? (
            <span>
              区域{" "}
              {item.region.map((value) => value.toFixed(2)).join(" / ")}
            </span>
          ) : null}
          {item.excerpt_sha256 ? (
            <span>摘要 {item.excerpt_sha256.slice(0, 12)}…</span>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

function FieldEditor({
  field,
  draft,
  editable,
  onChange,
}: {
  field: ProductBriefFieldResponseV1;
  draft: FieldDraft;
  editable: boolean;
  onChange: (next: FieldDraft) => void;
}) {
  const confidencePercent = Math.round(draft.confidence * 100);
  const validationError = structuredValueError(field.path, draft.valueText);
  const valueControlId = fieldValueControlId(field.path);
  const valueErrorId = `${valueControlId}-error`;
  return (
    <fieldset
      className={[
        "brief-field",
        field.review_reasons.length ? "brief-field-review" : "",
        draft.sensitive ? "brief-field-sensitive" : "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <legend>
        <span>{fieldLabel(field.path)}</span>
        <code>{field.path}</code>
      </legend>
      <div className="brief-field-flags">
        <span className={`brief-confidence brief-confidence-${confidencePercent < 80 ? "low" : "high"}`}>
          {confidencePercent}% 置信度
        </span>
        {draft.conflict === "CONFLICTING" ? (
          <span className="brief-warning">来源冲突</span>
        ) : null}
        {draft.sensitive ? <span className="brief-danger">敏感声明</span> : null}
        {field.review_reasons.map((reason) => (
          <span className="brief-warning" key={reason}>
            {REVIEW_REASON_LABELS[reason] ?? reason}
          </span>
        ))}
      </div>
      <label htmlFor={valueControlId}>
        <span>字段值</span>
        <textarea
          aria-describedby={validationError ? valueErrorId : undefined}
          aria-invalid={validationError !== null}
          aria-label={`${fieldLabel(field.path)}值`}
          disabled={!editable}
          id={valueControlId}
          onChange={(event) =>
            onChange({ ...draft, valueText: event.target.value })
          }
          rows={4}
          value={draft.valueText}
        />
      </label>
      {validationError ? (
        <p className="brief-field-error" id={valueErrorId} role="alert">
          {validationError}
        </p>
      ) : null}
      <div className="brief-field-controls">
        <label className="brief-confidence-control">
          <span>人工置信度 {confidencePercent}%</span>
          <input
            aria-label={`${fieldLabel(field.path)}置信度`}
            disabled={!editable}
            max="1"
            min="0"
            onChange={(event) =>
              onChange({ ...draft, confidence: Number(event.target.value) })
            }
            step="0.01"
            type="range"
            value={draft.confidence}
          />
        </label>
        <label>
          <span>冲突状态</span>
          <select
            aria-label={`${fieldLabel(field.path)}冲突状态`}
            disabled={!editable}
            onChange={(event) =>
              onChange({
                ...draft,
                conflict: event.target.value as ProductBriefFieldConflict,
              })
            }
            value={draft.conflict}
          >
            <option value="NONE">无冲突</option>
            <option value="CONFLICTING">存在冲突</option>
            <option value="RESOLVED">人工已解决</option>
          </select>
        </label>
      </div>
      <div className="brief-binary-controls">
        <label>
          <input
            checked={draft.reviewRequired}
            disabled={!editable}
            onChange={(event) =>
              onChange({ ...draft, reviewRequired: event.target.checked })
            }
            type="checkbox"
          />
          <span>仍需复核</span>
        </label>
        <label>
          <input
            checked={draft.sensitive}
            disabled={!editable}
            onChange={(event) =>
              onChange({ ...draft, sensitive: event.target.checked })
            }
            type="checkbox"
          />
          <span>敏感声明</span>
        </label>
      </div>
      <EvidenceList evidence={draft.evidence} path={field.path} />
    </fieldset>
  );
}

function VersionHistory({
  versions,
}: {
  versions: ProductBriefVersionSummaryResponseV1[];
}) {
  return (
    <ol className="brief-history">
      {versions.map((version) => (
        <li key={version.id}>
          <div className="brief-history-heading">
            <strong>版本 {version.version_number}</strong>
            <span className={`brief-state brief-state-${version.effective_state}`}>
              {stateLabel(version.effective_state)}
            </span>
          </div>
          <div className="brief-history-meta">
            <span>{version.source === "HUMAN" ? "人工修订" : "模型生成"}</span>
            <span>{version.actor_id}</span>
            <time dateTime={version.created_at}>{displayDate(version.created_at)}</time>
          </div>
          {version.revision_reason ? <p>{version.revision_reason}</p> : null}
          {version.source === "HUMAN" ? (
            <p>
              变更字段：
              {version.changed_field_paths.map(fieldLabel).join("、")}
            </p>
          ) : null}
          {version.provider_call ? (
            <p>
              {version.provider_call.provider} ·{" "}
              {version.provider_call.resolved_model ??
                version.provider_call.requested_model}{" "}
              · {version.provider_call.latency_ms} ms
            </p>
          ) : null}
          <code>{version.payload_sha256.slice(0, 20)}…</code>
        </li>
      ))}
    </ol>
  );
}

export function ProductBriefWorkbench({
  productId,
  source,
}: {
  productId: string;
  source: ProductBriefSourceSelection | null;
}) {
  const controller = useProductBriefWorkbenchController({ productId });
  const {
    brief,
    versions,
    versionsNextCursor,
    historyLoading,
    operation,
    pendingAnalysis,
    pendingCommand,
    commandStatus,
    polling,
  } = controller.snapshot;
  const {
    busy,
    error,
    notice,
    auxiliaryWarning,
    pollingWarning,
  } = controller.ui;
  const [lookupId, setLookupId] = useState("");
  const [workflowId, setWorkflowId] = useState("");
  const [assetVersionIdsText, setAssetVersionIdsText] = useState("");
  const [drafts, setDrafts] = useState<Record<string, FieldDraft>>({});
  const [revisionReason, setRevisionReason] = useState("");
  const [confirmationReason, setConfirmationReason] =
    useState("HUMAN_VERIFIED");
  const [commentRef, setCommentRef] = useState("");
  const [confirmationAcknowledged, setConfirmationAcknowledged] =
    useState(false);
  const conflictedRevision =
    commandStatus === "version-conflict" &&
    pendingCommand?.kind === "revise"
      ? pendingCommand
      : null;
  const staleDraft = conflictedRevision
    ? staleDraftForRevisionCommand(conflictedRevision)
    : null;
  const pollingPausedOperationId = polling.paused
    ? polling.operationId
    : null;
  const hasUnsettledAction =
    pendingAnalysis !== null || pendingCommand !== null;
  const refreshBlocked =
    pendingAnalysis !== null ||
    (pendingCommand !== null &&
      commandStatus !== "version-conflict");

  const currentVersion = brief?.current_version ?? null;
  const hasUnsavedChanges = useMemo(
    () =>
      currentVersion !== null &&
      JSON.stringify(drafts) !== JSON.stringify(draftsFor(currentVersion)),
    [currentVersion, drafts],
  );
  const reviewEditable =
    (brief?.state === "AWAITING_CONFIRMATION" ||
      brief?.state === "CONFIRMED") &&
    busy === null &&
    !hasUnsettledAction &&
    staleDraft === null;

  useEffect(() => {
    setLookupId(controller.formSeed.lookupId);
    setWorkflowId(controller.formSeed.workflowId);
    setAssetVersionIdsText(
      controller.formSeed.assetVersionIds.join("\n"),
    );
    setDrafts(draftsFor(brief?.current_version ?? null));
    setRevisionReason("");
    setConfirmationReason("HUMAN_VERIFIED");
    setCommentRef("");
    setConfirmationAcknowledged(false);
  }, [
    brief?.current_version,
    controller.formSeed.assetVersionIds,
    controller.formSeed.lookupId,
    controller.formSeed.revision,
    controller.formSeed.workflowId,
  ]);

  useEffect(() => {
    const matchingSource = productBriefSourceFor(productId, source);
    if (!matchingSource || brief) return;
    setWorkflowId(matchingSource.workflowId);
    setAssetVersionIdsText((current) => {
      const values = parseAssetVersionIds(current);
      return values.includes(matchingSource.assetVersionId)
        ? current
        : [...values, matchingSource.assetVersionId].join("\n");
    });
  }, [brief, productId, source]);

  const commonFields = useMemo(
    () => currentVersion?.fields.filter((field) => field.path.startsWith("common.")) ?? [],
    [currentVersion],
  );
  const categoryFields = useMemo(
    () => currentVersion?.fields.filter((field) => !field.path.startsWith("common.")) ?? [],
    [currentVersion],
  );

  const beginAnalysis = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (hasUnsettledAction) return;
    const assetVersionIds = parseAssetVersionIds(assetVersionIdsText);
    const firstAssetVersionId = assetVersionIds[0];
    if (
      !workflowId.trim() ||
      !firstAssetVersionId ||
      assetVersionIds.length > 8
    ) {
      controller.reportError(
        "工作流和 1 至 8 个素材版本为必填项。",
      );
      return;
    }
    await controller.analyze({
      workflowId: workflowId.trim(),
      assetVersionIds: [
        firstAssetVersionId,
        ...assetVersionIds.slice(1),
      ],
    });
  };

  const loadLookup = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!lookupId.trim() || hasUnsettledAction) return;
    await controller.load(lookupId.trim());
  };

  const saveRevision = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!brief || !currentVersion || hasUnsettledAction) return;
    const firstInvalidField = currentVersion.fields.find((field) => {
      const draft = drafts[field.path];
      return (
        !draft ||
        structuredValueError(field.path, draft.valueText) !== null
      );
    });
    if (firstInvalidField) {
      controller.reportError(
        "请先修正标记的结构化字段。",
      );
      document
        .getElementById(fieldValueControlId(firstInvalidField.path))
        ?.focus();
      return;
    }
    if (revisionReason.trim().length < 3) {
      controller.reportError("修订原因至少需要 3 个字符。");
      return;
    }
    const payload: ProductBriefRevisionRequestV1 = {
      expected_product_brief_version: brief.version,
      base_version_id: currentVersion.id,
      reason: revisionReason.trim(),
      fields: revisionFields(currentVersion, drafts),
    };
    await controller.revise(payload);
  };

  const confirmVersion = async () => {
    if (
      !brief ||
      !currentVersion ||
      !confirmationAcknowledged ||
      hasUnsavedChanges ||
      hasUnsettledAction
    ) {
      return;
    }
    await controller.confirm({
      reasonCode: confirmationReason.trim() || null,
      commentRef: commentRef.trim() || null,
    });
  };

  const restoreStaleDraft = () => {
    if (!currentVersion) {
      controller.reportError(
        "无法安全结算版本冲突草稿，请重新载入。",
      );
      return;
    }
    const command = controller.resolveRevisionConflict("restore");
    if (!command) return;
    const local = staleDraftForRevisionCommand(command);
    setDrafts(
      restoredProductBriefDrafts(
        draftsFor(currentVersion),
        local.fields,
      ),
    );
    setRevisionReason(local.reason);
  };

  const discardStaleDraft = () => {
    controller.resolveRevisionConflict("discard");
  };

  return (
    <section className="panel product-brief-panel" aria-labelledby="product-brief-heading">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">PRODUCT UNDERSTANDING</p>
          <h2 id="product-brief-heading">商品理解</h2>
        </div>
        <div className="brief-heading-status">
          {brief ? (
            <span className={`brief-state brief-state-${brief.state}`}>
              {stateLabel(brief.state)}
            </span>
          ) : null}
          <button
            className="button button-quiet"
            disabled={!brief || busy !== null || refreshBlocked}
            onClick={() => void controller.refresh()}
            type="button"
          >
            {busy === "refresh" ? "刷新中…" : "刷新"}
          </button>
        </div>
      </div>

      {error ? (
        <div className="error-banner" role="alert">
          <strong>商品理解未完成</strong>
          <span>{error}</span>
          {pendingAnalysis ? (
            <button
              className="button button-secondary"
              disabled={busy !== null}
              onClick={() => void controller.retryPending()}
              type="button"
            >
              安全重试
            </button>
          ) : null}
          {pendingCommand?.kind === "revise" ? (
            <button
              className="button button-secondary"
              disabled={busy !== null}
              onClick={() => void controller.retryPending()}
              type="button"
            >
              安全重试
            </button>
          ) : null}
          {pendingCommand?.kind === "confirm" ? (
            <button
              className="button button-secondary"
              disabled={busy !== null}
              onClick={() => void controller.retryPending()}
              type="button"
            >
              安全重试
            </button>
          ) : null}
        </div>
      ) : null}
      {notice ? (
        <div className="notice" aria-live="polite">
          {notice}
        </div>
      ) : null}
      {auxiliaryWarning ? (
        <div className="notice" role="status">
          {auxiliaryWarning}
        </div>
      ) : null}
      {pollingWarning ? (
        <div className="notice" role="status">
          {pollingWarning}
        </div>
      ) : null}
      {staleDraft ? (
        <div className="brief-stale-banner" role="alert">
          <div>
            <strong>服务器版本已更新</strong>
            <span>本地草稿基于版本 {staleDraft.versionId.slice(0, 8)}。</span>
          </div>
          <div className="brief-stale-actions">
            <button
              className="button button-secondary"
              disabled={busy !== null}
              onClick={restoreStaleDraft}
              type="button"
            >
              恢复本地草稿
            </button>
            <button
              className="button button-quiet"
              disabled={busy !== null}
              onClick={discardStaleDraft}
              type="button"
            >
              放弃本地草稿
            </button>
          </div>
        </div>
      ) : null}

      <form className="brief-lookup" onSubmit={loadLookup}>
        <label>
          <span>商品理解 ID</span>
          <input
            disabled={busy !== null || hasUnsettledAction}
            maxLength={36}
            onChange={(event) => setLookupId(event.target.value)}
            value={lookupId}
          />
        </label>
        <button
          className="button button-secondary"
          disabled={
            !lookupId.trim() || busy !== null || hasUnsettledAction
          }
          type="submit"
        >
          {busy === "load" ? "载入中…" : "载入"}
        </button>
      </form>

      {!brief || brief.state === "CONFIRMED" ? (
        <form className="brief-analysis-form" onSubmit={beginAnalysis}>
          <div className="form-grid">
            <label>
              <span>工作流 ID</span>
              <input
                disabled={busy !== null || hasUnsettledAction}
                maxLength={36}
                onChange={(event) => setWorkflowId(event.target.value)}
                required
                value={workflowId}
              />
            </label>
            <label>
              <span>素材版本 ID</span>
              <textarea
                disabled={busy !== null || hasUnsettledAction}
                onChange={(event) => setAssetVersionIdsText(event.target.value)}
                required
                rows={3}
                value={assetVersionIdsText}
              />
            </label>
          </div>
          <button
            className="button button-primary"
            disabled={busy !== null || hasUnsettledAction}
            type="submit"
          >
            {busy === "analyze"
              ? "提交中…"
              : brief
                ? "重新分析商品"
                : "开始商品理解"}
          </button>
        </form>
      ) : null}

      {brief && operation ? (
        <div
          aria-live="polite"
          className="brief-operation"
          data-testid="brief-operation"
          role="status"
        >
          <div>
            <span>分析任务</span>
            <strong>{stateLabel(operation.state)}</strong>
          </div>
          <div>
            <span>尝试</span>
            <strong>
              {operation.attempt_count} / {operation.max_attempts}
            </strong>
          </div>
          <div>
            <span>任务 ID</span>
            <code>{operation.id}</code>
          </div>
          {operation.error ? (
            <div className="brief-operation-error">
              <span>{operation.error.code}</span>
              <strong>{operation.error.message}</strong>
            </div>
          ) : null}
          {pollingPausedOperationId === operation.id &&
          !isProductBriefOperationPollTerminal(operation.state) ? (
            <button
              className="button button-secondary"
              onClick={controller.resumePolling}
              type="button"
            >
              继续刷新
            </button>
          ) : null}
        </div>
      ) : null}

      {brief && currentVersion ? (
        <>
          <div className="brief-version-summary">
            <div>
              <span>当前版本</span>
              <strong>{currentVersion.version_number}</strong>
            </div>
            <div>
              <span>分类</span>
              <strong>{currentVersion.category}</strong>
            </div>
            <div>
              <span>待复核字段</span>
              <strong>{currentVersion.unresolved_field_count}</strong>
            </div>
            <div>
              <span>策略</span>
              <strong>{currentVersion.review_policy_version}</strong>
            </div>
          </div>

          <form className="brief-review-form" onSubmit={saveRevision}>
            <section className="brief-field-group" aria-labelledby="brief-common-heading">
              <div className="brief-group-heading">
                <h3 id="brief-common-heading">通用字段</h3>
                <span>{commonFields.length} 项</span>
              </div>
              {commonFields.map((field) =>
                drafts[field.path] ? (
                  <FieldEditor
                    draft={drafts[field.path]}
                    editable={reviewEditable}
                    field={field}
                    key={field.id}
                    onChange={(next) =>
                      setDrafts((current) => ({
                        ...current,
                        [field.path]: next,
                      }))
                    }
                  />
                ) : null,
              )}
            </section>
            <section className="brief-field-group" aria-labelledby="brief-category-heading">
              <div className="brief-group-heading">
                <h3 id="brief-category-heading">
                  {currentVersion.category === "BEAUTY" ? "美妆字段" : "汽车配件字段"}
                </h3>
                <span>{categoryFields.length} 项</span>
              </div>
              {categoryFields.map((field) =>
                drafts[field.path] ? (
                  <FieldEditor
                    draft={drafts[field.path]}
                    editable={reviewEditable}
                    field={field}
                    key={field.id}
                    onChange={(next) =>
                      setDrafts((current) => ({
                        ...current,
                        [field.path]: next,
                      }))
                    }
                  />
                ) : null,
              )}
            </section>
            {brief.state === "AWAITING_CONFIRMATION" ||
            brief.state === "CONFIRMED" ? (
              <div className="brief-revision-actions">
                <label>
                  <span>修订原因</span>
                  <textarea
                    disabled={!reviewEditable}
                    maxLength={512}
                    minLength={3}
                    onChange={(event) => setRevisionReason(event.target.value)}
                    required
                    rows={2}
                    value={revisionReason}
                  />
                </label>
                <button
                  className="button button-secondary"
                  disabled={!reviewEditable}
                  type="submit"
                >
                  {busy === "revise" ? "保存中…" : "保存人工版本"}
                </button>
              </div>
            ) : null}
          </form>

          {brief.state === "AWAITING_CONFIRMATION" ? (
            <section className="brief-confirmation" aria-labelledby="brief-confirm-heading">
              <div className="brief-group-heading">
                <h3 id="brief-confirm-heading">确认版本 {currentVersion.version_number}</h3>
                <code>{currentVersion.id}</code>
              </div>
              <div className="form-grid">
                <label>
                  <span>确认原因编码</span>
                  <input
                    disabled={!reviewEditable}
                    maxLength={128}
                    onChange={(event) => setConfirmationReason(event.target.value)}
                    value={confirmationReason}
                  />
                </label>
                <label>
                  <span>评论引用</span>
                  <input
                    disabled={!reviewEditable}
                    maxLength={512}
                    onChange={(event) => setCommentRef(event.target.value)}
                    value={commentRef}
                  />
                </label>
              </div>
              <label className="brief-confirm-check">
                <input
                  checked={confirmationAcknowledged}
                  disabled={!reviewEditable || hasUnsavedChanges}
                  onChange={(event) =>
                    setConfirmationAcknowledged(event.target.checked)
                  }
                  type="checkbox"
                />
                <span>确认当前精确版本</span>
              </label>
              <button
                className="button button-primary"
                disabled={
                  !confirmationAcknowledged ||
                  hasUnsavedChanges ||
                  !reviewEditable
                }
                onClick={() => void confirmVersion()}
                type="button"
              >
                {busy === "confirm" ? "确认中…" : "确认并继续工作流"}
              </button>
              {hasUnsavedChanges ? (
                <p className="brief-warning" role="status">
                  请先保存当前字段修改，再确认精确版本。
                </p>
              ) : null}
            </section>
          ) : null}

          <section className="brief-history-section" aria-labelledby="brief-history-heading">
            <div className="brief-group-heading">
              <h3 id="brief-history-heading">版本历史</h3>
              <span>{versions.length} 个不可变版本</span>
            </div>
            <div id="brief-version-history">
              <VersionHistory versions={versions} />
            </div>
            {versionsNextCursor !== null ? (
              <button
                aria-controls="brief-version-history"
                className="button button-secondary"
                disabled={historyLoading !== null || busy !== null}
                onClick={() => void controller.loadMoreVersions()}
                type="button"
              >
                {historyLoading === "more"
                  ? "载入中…"
                  : "载入更多版本"}
              </button>
            ) : null}
          </section>
        </>
      ) : null}

      {busy === "recover" ? (
        <div
          aria-live="polite"
          className="loading-panel"
          role="status"
        >
          <span>正在恢复商品理解…</span>
          <span className="loading-bar wide" />
          <span className="loading-bar" />
        </div>
      ) : null}
    </section>
  );
}
