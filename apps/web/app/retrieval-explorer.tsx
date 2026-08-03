"use client";

import {
  FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import type {
  RetrievalQueryV1,
  VectorKind,
} from "../lib/generated/catalog-api";
import {
  RetrievalApi,
  RetrievalApiError,
} from "../lib/retrieval-api";
import type {
  RetrievalResponse,
  RetrievalTemporaryReference,
} from "../lib/retrieval-api";

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const MAXIMUM_PREVIEW_BYTES = 10 * 1024 * 1024;
const PREVIEW_IMAGE_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);

const CHANNEL_LABELS = {
  IMAGE_DENSE: "图片向量",
  PRODUCT_FUSED_DENSE: "商品融合向量",
  LEXICAL: "全文检索",
  BRAND_PROFILE: "品牌档案",
  EXPLICIT: "显式引用",
} as const;

export type RetrievalExplorerForm = {
  purpose: string;
  provider: string;
  requiresDerivative: boolean;
  rolesText: string;
  vectorKinds: VectorKind[];
  queryText: string;
  queryImageAssetVersionId: string;
  explicitReferencesText: string;
  brandProfileId: string;
  brandProfileVersion: string;
  resultLimit: number;
  candidateLimit: number;
  retrievalPolicyVersion: string;
};

type RetrievalIdentity = {
  workspaceId: string;
  requesterId: string;
  productId: string;
  category: string;
  brand: string;
};

export type RetrievalPreviewState = Record<
  number,
  {
    loading: boolean;
    objectUrl?: string;
    expiresAt?: string;
    error?: string;
  }
>;

function optionalUuid(value: string, field: string): string | null {
  const normalized = value.trim();
  if (!normalized) return null;
  if (!UUID_PATTERN.test(normalized)) {
    throw new TypeError(`${field}必须是小写 UUID。`);
  }
  return normalized;
}

function uniqueTokens(value: string): string[] {
  return Array.from(
    new Set(
      value
        .split(/[\s,，]+/)
        .map((item) => item.trim())
        .filter(Boolean),
    ),
  );
}

function explicitReferences(value: string): string[] {
  return uniqueTokens(value).map((item) => {
    if (!UUID_PATTERN.test(item)) {
      throw new TypeError("显式引用必须全部是小写 Asset Version UUID。");
    }
    return item;
  });
}

export function buildRetrievalQuery(
  form: RetrievalExplorerForm,
  identity: RetrievalIdentity,
): RetrievalQueryV1 {
  if (form.vectorKinds.length < 1) {
    throw new TypeError("至少选择一个向量检索通道。");
  }
  const imageId = optionalUuid(
    form.queryImageAssetVersionId,
    "查询图片 Asset Version",
  );
  if (form.vectorKinds.includes("IMAGE") && imageId === null) {
    throw new TypeError("图片向量检索需要查询图片 Asset Version。 ");
  }
  const profileId = optionalUuid(form.brandProfileId, "品牌档案");
  const profileVersion = form.brandProfileVersion.trim()
    ? Number(form.brandProfileVersion)
    : null;
  if ((profileId === null) !== (profileVersion === null)) {
    throw new TypeError("品牌档案 ID 与不可变版本必须同时填写。");
  }
  if (
    profileVersion !== null &&
    (!Number.isSafeInteger(profileVersion) || profileVersion < 1)
  ) {
    throw new TypeError("品牌档案版本必须是正整数。");
  }
  const references = explicitReferences(form.explicitReferencesText);
  const queryText = form.queryText.trim() || null;
  if (!queryText && !imageId && references.length === 0 && !profileId) {
    throw new TypeError("至少提供文本、图片、显式引用或品牌档案之一。");
  }
  if (
    !Number.isSafeInteger(form.resultLimit) ||
    form.resultLimit < 1 ||
    form.resultLimit > 50
  ) {
    throw new TypeError("结果数必须在 1–50 之间。");
  }
  if (
    !Number.isSafeInteger(form.candidateLimit) ||
    form.candidateLimit < form.resultLimit ||
    form.candidateLimit > 1_000
  ) {
    throw new TypeError("候选数必须不少于结果数，且不超过 1000。");
  }
  return {
    workspace_id: identity.workspaceId,
    requester_id: identity.requesterId,
    product_id: identity.productId,
    product_brief_id: null,
    category: identity.category,
    brand: identity.brand,
    purpose: form.purpose.trim(),
    provider: form.provider.trim(),
    requires_derivative: form.requiresDerivative,
    roles: uniqueTokens(form.rolesText),
    vector_kinds: form.vectorKinds as [VectorKind, ...VectorKind[]],
    query_text: queryText,
    query_image_asset_version_id: imageId,
    explicit_reference_asset_version_ids: references,
    brand_profile_id: profileId,
    brand_profile_version: profileVersion,
    result_limit: form.resultLimit,
    candidate_limit: form.candidateLimit,
    retrieval_policy_version: form.retrievalPolicyVersion.trim(),
  };
}

export async function previewObjectUrl(
  reference: RetrievalTemporaryReference,
  signal: AbortSignal,
): Promise<string> {
  const response = await fetch(reference.url, {
    method: reference.method,
    headers: reference.required_headers,
    cache: "no-store",
    credentials: "omit",
    referrerPolicy: "no-referrer",
    signal,
  });
  if (!response.ok) throw new Error("受控对象读取失败");
  const contentType = response.headers
    .get("content-type")
    ?.split(";", 1)[0]
    .trim()
    .toLowerCase();
  if (!contentType || !PREVIEW_IMAGE_TYPES.has(contentType)) {
    throw new Error("受控对象不是支持的图片格式");
  }
  const contentLength = response.headers.get("content-length");
  if (contentLength !== null) {
    const declaredBytes = Number(contentLength);
    if (
      !Number.isSafeInteger(declaredBytes) ||
      declaredBytes < 0 ||
      declaredBytes > MAXIMUM_PREVIEW_BYTES
    ) {
      throw new Error("受控图片超过预览大小限制");
    }
  }
  const blob = await response.blob();
  if (blob.size > MAXIMUM_PREVIEW_BYTES) {
    throw new Error("受控图片超过预览大小限制");
  }
  return URL.createObjectURL(blob);
}

function retrievalMessage(error: unknown): string {
  if (error instanceof RetrievalApiError) {
    if (error.status === 404) return "检索证据或预览授权已失效，请重新执行检索。";
    if (error.status === 504) return "检索请求超时，请重试。";
    return error.envelope?.message ?? "检索服务暂时不可用，请稍后重试。";
  }
  if (error instanceof Error) return error.message;
  return "检索未完成，请检查查询后重试。";
}

function formatScore(value: number): string {
  return new Intl.NumberFormat("zh-CN", {
    minimumFractionDigits: 4,
    maximumFractionDigits: 6,
  }).format(value);
}

export function RetrievalResults({
  response,
  previewState,
  onPreview,
}: {
  response: RetrievalResponse;
  previewState: RetrievalPreviewState;
  onPreview: (rank: number, token: string) => void;
}) {
  return (
    <div className="retrieval-results" aria-live="polite">
      <div className="retrieval-run-summary">
        <div>
          <span>检索运行</span>
          <code>{response.retrieval_run_id ?? "未保留"}</code>
        </div>
        <div>
          <span>策略版本</span>
          <strong>{response.retrieval_policy_version}</strong>
        </div>
        <div>
          <span>混合通道</span>
          <strong>{response.complete_hybrid ? "完整" : "已降级"}</strong>
        </div>
        <div>
          <span>候选收敛</span>
          <strong>
            {response.eligible_asset_version_count} → {response.fused_candidate_count} →{" "}
            {response.final_authorized_candidate_count}
          </strong>
        </div>
        <div>
          <span>结果 / 耗时</span>
          <strong>
            {response.citations.length} 条 · {response.latency_ms} ms
          </strong>
        </div>
      </div>

      {!response.complete_hybrid ? (
        <section className="retrieval-degradation" role="status">
          <strong>混合检索已降级</strong>
          <p>以下依赖不可用；当前结果仍通过最终 MySQL 权利复核。</p>
          <ul>
            {response.degradations.map((item) => (
              <li key={`${item.component}-${item.code}`}>
                <code>{item.code}</code>
                <span>{item.component}</span>
                <p>{item.message}</p>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {response.citations.length === 0 ? (
        <div className="empty-state compact">
          <strong>没有可授权结果</strong>
          <span>当前过滤器、权利窗口与检索信号没有产生可返回资产。</span>
        </div>
      ) : (
        <ol className="retrieval-result-list">
          {response.citations.map((citation) => {
            const preview = previewState[citation.rank];
            return (
              <li key={citation.asset_version_id}>
                <article className="retrieval-result-card">
                  <header>
                    <div>
                      <span className="retrieval-rank">#{citation.rank}</span>
                      <strong>Asset Version</strong>
                      <code>{citation.asset_version_id}</code>
                    </div>
                    <strong className="retrieval-score">
                      {formatScore(citation.score.final_score)}
                    </strong>
                  </header>

                  <div className="retrieval-channel-list" aria-label="召回通道">
                    {citation.channels.map((channel) => (
                      <span key={channel} title={CHANNEL_LABELS[channel]}>
                        {channel}
                      </span>
                    ))}
                  </div>

                  <dl className="retrieval-evidence-grid">
                    <div>
                      <dt>RRF</dt>
                      <dd>{formatScore(citation.score.reciprocal_rank_fusion)}</dd>
                    </div>
                    <div>
                      <dt>业务调整</dt>
                      <dd>{formatScore(citation.score.business_adjustment)}</dd>
                    </div>
                    <div>
                      <dt>重排位置</dt>
                      <dd>{citation.score.rerank_position ?? "未启用"}</dd>
                    </div>
                    <div>
                      <dt>权利证据</dt>
                      <dd>
                        rights v{citation.rights_record_version} ·{" "}
                        <code>{citation.rights_record_id}</code>
                      </dd>
                    </div>
                    <div>
                      <dt>决策时间</dt>
                      <dd>{new Date(citation.decided_at).toLocaleString("zh-CN")}</dd>
                    </div>
                    <div>
                      <dt>品牌档案</dt>
                      <dd>
                        {citation.brand_profile_version
                          ? `v${citation.brand_profile_version}`
                          : "未绑定"}
                      </dd>
                    </div>
                  </dl>

                  <details className="retrieval-score-details">
                    <summary>查看通道排名与原始分数</summary>
                    <ul>
                      {citation.channels.map((channel) => (
                        <li key={channel}>
                          <strong>{channel}</strong>
                          <span>排名 {citation.score.channel_ranks[channel] ?? "—"}</span>
                          <span>
                            原始分数{" "}
                            {citation.score.channel_raw_scores[channel] === undefined
                              ? "—"
                              : formatScore(citation.score.channel_raw_scores[channel])}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </details>

                  <p className="retrieval-reason">
                    <strong>返回原因</strong>
                    <span>{citation.reason}</span>
                  </p>

                  {citation.preview_reference_token ? (
                    <div className="retrieval-preview">
                      <button
                        className="button button-secondary"
                        disabled={preview?.loading}
                        onClick={() =>
                          onPreview(
                            citation.rank,
                            citation.preview_reference_token as string,
                          )
                        }
                        type="button"
                      >
                        {preview?.loading ? "授权中…" : "受控预览"}
                      </button>
                      {preview?.error ? <span role="alert">{preview.error}</span> : null}
                      {preview?.objectUrl ? (
                        <figure>
                          {/* A short-lived blob URL avoids retaining the signed object URL in markup. */}
                          {/* eslint-disable-next-line @next/next/no-img-element */}
                          <img
                            alt={`检索结果 ${citation.rank} 的受控资产预览`}
                            height="320"
                            src={preview.objectUrl}
                            width="480"
                          />
                          <figcaption>
                            临时授权至{" "}
                            {preview.expiresAt
                              ? new Date(preview.expiresAt).toLocaleTimeString("zh-CN")
                              : "当前会话"}
                          </figcaption>
                        </figure>
                      ) : null}
                    </div>
                  ) : null}
                </article>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}

export function RetrievalExplorer({
  productId,
  category,
  brand,
  workspaceId = "catalog-demo",
  requesterId = "catalog-workbench",
}: {
  productId: string;
  category: string;
  brand: string;
  workspaceId?: string;
  requesterId?: string;
}) {
  const api = useMemo(
    () => new RetrievalApi({ workspaceId, requesterId }),
    [requesterId, workspaceId],
  );
  const [form, setForm] = useState<RetrievalExplorerForm>({
    purpose: "RETRIEVAL",
    provider: "fixture",
    requiresDerivative: false,
    rolesText: "",
    vectorKinds: ["PRODUCT_FUSED"],
    queryText: `${brand} ${category}`,
    queryImageAssetVersionId: "",
    explicitReferencesText: "",
    brandProfileId: "",
    brandProfileVersion: "",
    resultLimit: 8,
    candidateLimit: 40,
    retrievalPolicyVersion: "retrieval-policy-v1",
  });
  const [response, setResponse] = useState<RetrievalResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [previewState, setPreviewState] = useState<RetrievalPreviewState>({});
  const requestRef = useRef<AbortController | null>(null);
  const previewRequestsRef = useRef(new Map<number, AbortController>());
  const previewUrlsRef = useRef(new Map<number, string>());
  const previewExpiryTimersRef = useRef(
    new Map<number, ReturnType<typeof setTimeout>>(),
  );

  const clearPreviewResources = useCallback(() => {
    for (const request of previewRequestsRef.current.values()) request.abort();
    previewRequestsRef.current.clear();
    for (const timer of previewExpiryTimersRef.current.values()) clearTimeout(timer);
    previewExpiryTimersRef.current.clear();
    for (const url of previewUrlsRef.current.values()) URL.revokeObjectURL(url);
    previewUrlsRef.current.clear();
  }, []);

  useEffect(() => {
    return () => {
      requestRef.current?.abort();
      clearPreviewResources();
    };
  }, [clearPreviewResources]);

  const toggleVectorKind = (kind: VectorKind) => {
    setForm((current) => ({
      ...current,
      vectorKinds: current.vectorKinds.includes(kind)
        ? current.vectorKinds.filter((candidate) => candidate !== kind)
        : [...current.vectorKinds, kind],
    }));
  };

  const execute = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    requestRef.current?.abort();
    clearPreviewResources();
    setPreviewState({});
    const controller = new AbortController();
    requestRef.current = controller;
    setBusy(true);
    setError(null);
    try {
      const payload = buildRetrievalQuery(form, {
        workspaceId,
        requesterId,
        productId,
        category,
        brand,
      });
      const next = await api.execute(payload, controller.signal);
      setResponse(next);
    } catch (failure) {
      if (!controller.signal.aborted) setError(retrievalMessage(failure));
    } finally {
      if (requestRef.current === controller) {
        requestRef.current = null;
        setBusy(false);
      }
    }
  };

  const preview = async (rank: number, token: string) => {
    if (!response?.retrieval_run_id) return;
    previewRequestsRef.current.get(rank)?.abort();
    const previousTimer = previewExpiryTimersRef.current.get(rank);
    if (previousTimer !== undefined) {
      clearTimeout(previousTimer);
      previewExpiryTimersRef.current.delete(rank);
    }
    const previousUrl = previewUrlsRef.current.get(rank);
    if (previousUrl) {
      URL.revokeObjectURL(previousUrl);
      previewUrlsRef.current.delete(rank);
    }
    const controller = new AbortController();
    previewRequestsRef.current.set(rank, controller);
    setPreviewState((current) => ({
      ...current,
      [rank]: { loading: true },
    }));
    try {
      const reference = await api.preview(
        response.retrieval_run_id,
        rank,
        token,
        controller.signal,
      );
      const objectUrl = await previewObjectUrl(reference, controller.signal);
      if (controller.signal.aborted) {
        URL.revokeObjectURL(objectUrl);
        return;
      }
      previewUrlsRef.current.set(rank, objectUrl);
      setPreviewState((current) => ({
        ...current,
        [rank]: {
          loading: false,
          objectUrl,
          expiresAt: reference.expires_at,
        },
      }));
      const expiresInMs = Math.max(
        0,
        Math.min(60_000, Date.parse(reference.expires_at) - Date.now()),
      );
      previewExpiryTimersRef.current.set(
        rank,
        setTimeout(() => {
          if (previewUrlsRef.current.get(rank) !== objectUrl) return;
          URL.revokeObjectURL(objectUrl);
          previewUrlsRef.current.delete(rank);
          previewExpiryTimersRef.current.delete(rank);
          setPreviewState((current) => ({
            ...current,
            [rank]: {
              loading: false,
              error: "预览授权已失效，请重新授权。",
            },
          }));
        }, expiresInMs),
      );
    } catch (failure) {
      if (!controller.signal.aborted) {
        setPreviewState((current) => ({
          ...current,
          [rank]: { loading: false, error: retrievalMessage(failure) },
        }));
      }
    } finally {
      if (previewRequestsRef.current.get(rank) === controller) {
        previewRequestsRef.current.delete(rank);
      }
    }
  };

  return (
    <section className="panel retrieval-explorer" aria-labelledby="retrieval-heading">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">RIGHTS-FIRST HYBRID RETRIEVAL</p>
          <h2 id="retrieval-heading">检索探索器</h2>
          <p className="muted">
            先由 MySQL 建立当前可授权集合，再执行多通道召回、RRF 融合与最终权利复核。
          </p>
        </div>
        <span className="version-label">{form.retrievalPolicyVersion}</span>
      </div>

      <form className="retrieval-query-form" onSubmit={execute}>
        <div className="retrieval-context" aria-label="当前检索过滤器">
          <div>
            <span>商品</span>
            <code>{productId}</code>
          </div>
          <div>
            <span>品牌</span>
            <strong>{brand}</strong>
          </div>
          <div>
            <span>分类</span>
            <strong>{category}</strong>
          </div>
        </div>

        <label>
          <span>查询文本</span>
          <textarea
            maxLength={4096}
            onChange={(event) =>
              setForm((current) => ({ ...current, queryText: event.target.value }))
            }
            rows={3}
            value={form.queryText}
          />
          <small>用于全文与商品融合向量召回；服务端会规范化受控文本。</small>
        </label>

        <fieldset className="retrieval-channel-selector">
          <legend>向量通道</legend>
          <label>
            <input
              checked={form.vectorKinds.includes("PRODUCT_FUSED")}
              onChange={() => toggleVectorKind("PRODUCT_FUSED")}
              type="checkbox"
            />
            <span>商品融合向量</span>
          </label>
          <label>
            <input
              checked={form.vectorKinds.includes("IMAGE")}
              onChange={() => toggleVectorKind("IMAGE")}
              type="checkbox"
            />
            <span>图片向量</span>
          </label>
        </fieldset>

        {form.vectorKinds.includes("IMAGE") ? (
          <label>
            <span>查询图片 Asset Version UUID</span>
            <input
              onChange={(event) =>
                setForm((current) => ({
                  ...current,
                  queryImageAssetVersionId: event.target.value,
                }))
              }
              required
              value={form.queryImageAssetVersionId}
            />
            <small>生成查询向量前会再次检查当前权利与外传策略。</small>
          </label>
        ) : null}

        <div className="form-grid">
          <label>
            <span>用途</span>
            <input
              onChange={(event) =>
                setForm((current) => ({ ...current, purpose: event.target.value }))
              }
              required
              value={form.purpose}
            />
          </label>
          <label>
            <span>提供方</span>
            <input
              onChange={(event) =>
                setForm((current) => ({ ...current, provider: event.target.value }))
              }
              required
              value={form.provider}
            />
          </label>
        </div>

        <label className="retrieval-inline-check">
          <input
            checked={form.requiresDerivative}
            onChange={(event) =>
              setForm((current) => ({
                ...current,
                requiresDerivative: event.target.checked,
              }))
            }
            type="checkbox"
          />
          <span>本次用途需要衍生权</span>
        </label>

        <details className="retrieval-advanced">
          <summary>高级过滤与不可变引用</summary>
          <div className="retrieval-advanced-fields">
            <label>
              <span>角色过滤（逗号或空格分隔）</span>
              <input
                onChange={(event) =>
                  setForm((current) => ({ ...current, rolesText: event.target.value }))
                }
                value={form.rolesText}
              />
            </label>
            <label>
              <span>显式 Asset Version 引用（每行一个）</span>
              <textarea
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    explicitReferencesText: event.target.value,
                  }))
                }
                rows={3}
                value={form.explicitReferencesText}
              />
            </label>
            <div className="form-grid">
              <label>
                <span>品牌档案 UUID</span>
                <input
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      brandProfileId: event.target.value,
                    }))
                  }
                  value={form.brandProfileId}
                />
              </label>
              <label>
                <span>品牌档案不可变版本</span>
                <input
                  min={1}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      brandProfileVersion: event.target.value,
                    }))
                  }
                  type="number"
                  value={form.brandProfileVersion}
                />
              </label>
            </div>
            <div className="form-grid">
              <label>
                <span>最终结果数</span>
                <input
                  max={50}
                  min={1}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      resultLimit: Number(event.target.value),
                    }))
                  }
                  type="number"
                  value={form.resultLimit}
                />
              </label>
              <label>
                <span>候选池上限</span>
                <input
                  max={1000}
                  min={form.resultLimit}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      candidateLimit: Number(event.target.value),
                    }))
                  }
                  type="number"
                  value={form.candidateLimit}
                />
              </label>
            </div>
            <label>
              <span>检索策略版本</span>
              <input
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    retrievalPolicyVersion: event.target.value,
                  }))
                }
                required
                value={form.retrievalPolicyVersion}
              />
            </label>
          </div>
        </details>

        {error ? (
          <div className="error-banner" role="alert">
            <strong>检索未完成</strong>
            <span>{error}</span>
          </div>
        ) : null}

        <div className="form-actions">
          <button className="button button-primary" disabled={busy} type="submit">
            {busy ? "检索与复核中…" : "执行权利优先检索"}
          </button>
          <span className="form-hint">结果会保留审计证据；预览授权仅短时有效。</span>
        </div>
      </form>

      {response ? (
        <RetrievalResults
          onPreview={(rank, token) => void preview(rank, token)}
          previewState={previewState}
          response={response}
        />
      ) : null}
    </section>
  );
}
