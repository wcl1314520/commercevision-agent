"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";

import {
  CollectionRebuildApi,
  CollectionRebuildApiError,
  CollectionRebuildRequest,
  CollectionRebuildResponse,
} from "../lib/collection-rebuild-api";

const TERMINAL = new Set(["FAILED", "RETIRED"]);

const initialRequest: CollectionRebuildRequest = {
  vector_kind: "IMAGE",
  model_family: "deterministic-image-embedding",
  model_id: "deterministic-image-embedding-v1",
  pinned_revision: "fixture-epoch-v1",
  dimension: 256,
  schema_version: 2,
  index_spec_version: "hnsw-cosine-v1",
  expected_active_collection_version: 1,
  expected_policy_pointer_version: 1,
};

function message(error: unknown): string {
  if (error instanceof CollectionRebuildApiError) {
    return error.envelope?.message ?? "Collection 重建请求失败。";
  }
  return error instanceof Error ? error.message : "Collection 重建请求失败。";
}

export function CollectionRebuildAdmin() {
  const api = useMemo(() => new CollectionRebuildApi(), []);
  const [form, setForm] = useState(initialRequest);
  const [lookupId, setLookupId] = useState("");
  const [rebuild, setRebuild] = useState<CollectionRebuildResponse | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!rebuild || TERMINAL.has(rebuild.state) || rebuild.state === "READY") return;
    const timer = window.setInterval(() => {
      void api
        .get(rebuild.id)
        .then((next) => {
          setRebuild(next);
          setLookupId(next.id);
        })
        .catch((failure) => setError(message(failure)));
    }, 3000);
    return () => window.clearInterval(timer);
  }, [api, rebuild]);

  const request = async (event: FormEvent) => {
    event.preventDefault();
    setBusy("request");
    setError(null);
    try {
      const next = await api.request(form);
      setRebuild(next);
      setLookupId(next.id);
    } catch (failure) {
      setError(message(failure));
    } finally {
      setBusy(null);
    }
  };

  const load = async (event: FormEvent) => {
    event.preventDefault();
    setBusy("load");
    setError(null);
    try {
      setRebuild(await api.get(lookupId.trim()));
    } catch (failure) {
      setError(message(failure));
    } finally {
      setBusy(null);
    }
  };

  const act = async (action: "validate" | "activate") => {
    if (!rebuild) return;
    setBusy(action);
    setError(null);
    try {
      setRebuild(
        action === "validate"
          ? await api.validate(rebuild.id, rebuild.version)
          : await api.activate(rebuild.id, rebuild.version),
      );
    } catch (failure) {
      setError(message(failure));
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="panel collection-rebuild-admin" aria-labelledby="rebuild-heading">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">ADMIN / MILVUS COLLECTION</p>
          <h2 id="rebuild-heading">Collection 安全重建</h2>
          <p className="muted">候选集合在验证通过前不会接管线上检索。</p>
        </div>
        <span className="version-label">{rebuild?.state ?? "未启动"}</span>
      </div>

      {error ? <div className="error-banner" role="alert">{error}</div> : null}

      <form className="rebuild-lookup" onSubmit={load}>
        <label>
          <span>重建 ID</span>
          <input
            onChange={(event) => setLookupId(event.target.value)}
            placeholder="输入已有重建 UUID"
            value={lookupId}
          />
        </label>
        <button className="button button-secondary" disabled={busy !== null} type="submit">
          {busy === "load" ? "读取中…" : "读取状态"}
        </button>
      </form>

      <details>
        <summary>发起新的候选集合重建</summary>
        <form className="catalog-form rebuild-request-form" onSubmit={request}>
          <div className="form-grid">
            <label>
              <span>向量类型</span>
              <select
                onChange={(event) =>
                  setForm({ ...form, vector_kind: event.target.value as CollectionRebuildRequest["vector_kind"] })
                }
                value={form.vector_kind}
              >
                <option value="IMAGE">IMAGE</option>
                <option value="PRODUCT_FUSED">PRODUCT_FUSED</option>
              </select>
            </label>
            <label><span>模型族</span><input value={form.model_family} onChange={(event) => setForm({ ...form, model_family: event.target.value })} /></label>
            <label><span>模型 ID</span><input value={form.model_id} onChange={(event) => setForm({ ...form, model_id: event.target.value })} /></label>
            <label><span>固定修订</span><input value={form.pinned_revision} onChange={(event) => setForm({ ...form, pinned_revision: event.target.value })} /></label>
            <label><span>维度</span><input min={1} type="number" value={form.dimension} onChange={(event) => setForm({ ...form, dimension: Number(event.target.value) })} /></label>
            <label><span>Schema 版本</span><input min={1} type="number" value={form.schema_version} onChange={(event) => setForm({ ...form, schema_version: Number(event.target.value) })} /></label>
            <label><span>索引规格</span><input value={form.index_spec_version} onChange={(event) => setForm({ ...form, index_spec_version: event.target.value })} /></label>
            <label><span>当前集合版本</span><input min={1} type="number" value={form.expected_active_collection_version} onChange={(event) => setForm({ ...form, expected_active_collection_version: Number(event.target.value) })} /></label>
            <label><span>策略指针版本</span><input min={1} type="number" value={form.expected_policy_pointer_version} onChange={(event) => setForm({ ...form, expected_policy_pointer_version: Number(event.target.value) })} /></label>
          </div>
          <button className="button button-primary" disabled={busy !== null} type="submit">
            {busy === "request" ? "提交中…" : "创建非活动候选集合"}
          </button>
        </form>
      </details>

      {rebuild ? (
        <div className="rebuild-status" aria-live="polite">
          <dl>
            <div><dt>状态</dt><dd>{rebuild.state}</dd></div>
            <div><dt>版本</dt><dd>{rebuild.version}</dd></div>
            <div><dt>已处理</dt><dd>{rebuild.processed_count}</dd></div>
            <div><dt>快照水位</dt><dd>{new Date(rebuild.snapshot_watermark).toLocaleString()}</dd></div>
            <div><dt>退休时间</dt><dd>{rebuild.retire_after ? new Date(rebuild.retire_after).toLocaleString() : "—"}</dd></div>
          </dl>
          {rebuild.failure_code ? <p className="error-banner">失败代码：{rebuild.failure_code}</p> : null}
          {rebuild.validation ? (
            <div className="rebuild-validation">
              <strong>{rebuild.validation.accepted ? "验证通过" : "验证拒绝"}</strong>
              <span>行数 {rebuild.validation.actual_row_count}/{rebuild.validation.expected_row_count}</span>
              <span>ANN Recall@10 {(rebuild.validation.ann_recall_at_10 * 100).toFixed(2)}%</span>
              <span>未授权结果 {rebuild.validation.unauthorized_result_count}</span>
            </div>
          ) : null}
          <div className="form-actions">
            {rebuild.state === "AWAITING_VALIDATION" ? (
              <button className="button button-secondary" disabled={busy !== null} onClick={() => void act("validate")} type="button">运行验证</button>
            ) : null}
            {rebuild.state === "READY" ? (
              <button className="button button-primary" disabled={busy !== null} onClick={() => void act("activate")} type="button">原子激活候选集合</button>
            ) : null}
          </div>
          <ol className="rebuild-progress">
            {rebuild.progress.map((item) => (
              <li key={item.sequence}>
                <strong>{item.message_code}</strong>
                <span>{item.state} · {item.processed_count}</span>
              </li>
            ))}
          </ol>
        </div>
      ) : null}
    </section>
  );
}
