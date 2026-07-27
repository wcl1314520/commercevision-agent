"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import {
  AssetApi,
  AssetApiError,
  newUploadIdempotencyKey,
} from "../lib/asset-api";
import type {
  AssetResponseV1,
  RightsHistoryResponseV1,
  RightsRecordMutationRequestV1,
  RightsUsabilityResponseV1,
} from "../lib/generated/catalog-api";

const api = new AssetApi();
const RIGHTS_HISTORY_PAGE_SIZE = 25;
const USE_OPTIONS = [
  ["RETRIEVAL", "素材检索"],
  ["VISION_ANALYSIS", "视觉分析"],
  ["GENERATION", "创意生成"],
  ["BRAND_PROFILE", "品牌档案"],
] as const;
const PROVIDER_OPTIONS = [
  ["milvus", "Milvus 检索"],
  ["qwen-vl", "通义视觉"],
  ["alibaba-green", "阿里内容安全"],
] as const;

type RightsForm = Omit<
  RightsRecordMutationRequestV1,
  "expected_asset_version" | "asset_version_id" | "valid_from" | "valid_until"
> & {
  valid_from: string;
  valid_until: string;
};

const EMPTY_FORM: RightsForm = {
  owner_reference: "",
  source: "",
  license_reference: "",
  allowed_uses: [],
  allowed_providers: [],
  derivative_allowed: false,
  public_demo_allowed: false,
  evidence_reference: "",
  terms_sha256: "",
  valid_from: "",
  valid_until: "",
  perpetual: false,
};

function localDateTime(value: string): string {
  if (!value) return "";
  const date = new Date(value);
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function formFromRightsRecord(
  record: RightsHistoryResponseV1["items"][number] | undefined,
): RightsForm {
  if (record?.decision !== "GRANT") return EMPTY_FORM;
  return {
    owner_reference: record.owner_reference,
    source: record.source,
    license_reference: record.license_reference,
    allowed_uses: record.allowed_uses,
    allowed_providers: record.allowed_providers,
    derivative_allowed: record.derivative_allowed,
    public_demo_allowed: record.public_demo_allowed,
    evidence_reference: record.evidence_reference,
    terms_sha256: record.terms_sha256,
    valid_from: localDateTime(record.valid_from),
    valid_until: localDateTime(record.valid_until ?? ""),
    perpetual: record.perpetual,
  };
}

function toggle(values: string[], value: string): string[] {
  return values.includes(value)
    ? values.filter((item) => item !== value)
    : [...values, value];
}

function rightsError(error: unknown): string {
  if (error instanceof AssetApiError) {
    if (error.envelope?.code === "VERSION_CONFLICT") {
      return "资产版本已变化，本地草稿未提交。请先载入最新权利，再重新编辑。";
    }
    if (error.envelope?.code === "INVALID_TRANSITION") {
      return "素材尚未通过强制校验，或当前状态不允许修改权利。";
    }
    return error.envelope?.message ?? "权利请求失败。";
  }
  return error instanceof Error ? error.message : "权利请求失败。";
}

export function AssetRightsWorkbench({
  asset,
  onAssetChange,
}: {
  asset: AssetResponseV1;
  onAssetChange: (asset: AssetResponseV1) => void;
}) {
  const [history, setHistory] = useState<RightsHistoryResponseV1 | null>(null);
  const [historyStatus, setHistoryStatus] = useState<
    "loading" | "ready" | "error"
  >("loading");
  const [canAdminister, setCanAdminister] = useState(false);
  const [form, setForm] = useState<RightsForm>(EMPTY_FORM);
  const [busy, setBusy] = useState<string | null>("history");
  const [error, setError] = useState<string | null>(null);
  const [revokeReason, setRevokeReason] = useState("");
  const [revokeEvidence, setRevokeEvidence] = useState("");
  const [blockReason, setBlockReason] = useState("");
  const [blockEvidence, setBlockEvidence] = useState("");
  const [versionConflict, setVersionConflict] = useState(false);
  const [decision, setDecision] = useState<RightsUsabilityResponseV1 | null>(
    null,
  );
  const [decisionPurpose, setDecisionPurpose] = useState("RETRIEVAL");
  const [decisionProvider, setDecisionProvider] = useState("milvus");
  const [decisionDerivative, setDecisionDerivative] = useState(false);
  const decisionObservedAt = useRef<number | null>(null);
  const decisionGeneration = useRef(0);

  const clearDecision = useCallback(() => {
    decisionGeneration.current += 1;
    decisionObservedAt.current = null;
    setDecision(null);
  }, []);

  const reload = useCallback(async (options?: { synchronizeForm?: boolean }) => {
    clearDecision();
    setHistoryStatus("loading");
    try {
      const [nextAsset, nextHistory] = await Promise.all([
        api.getAsset(asset.id),
        api.getRightsHistory(asset.id, {
          limit: RIGHTS_HISTORY_PAGE_SIZE,
        }),
      ]);
      onAssetChange(nextAsset);
      setHistory(nextHistory);
      if (options?.synchronizeForm) {
        setForm(formFromRightsRecord(nextHistory.items[0]));
        setVersionConflict(false);
      }
      setHistoryStatus("ready");
      return nextAsset;
    } catch (requestError) {
      setHistoryStatus("error");
      throw requestError;
    }
  }, [asset.id, clearDecision, onAssetChange]);

  useEffect(() => {
    let active = true;
    setHistory(null);
    setHistoryStatus("loading");
    setForm(EMPTY_FORM);
    setVersionConflict(false);
    clearDecision();
    setBusy("history");
    setError(null);
    void api
      .getRightsHistory(asset.id, { limit: RIGHTS_HISTORY_PAGE_SIZE })
      .then((result) => {
        if (!active) return;
        setHistory(result);
        setHistoryStatus("ready");
        setForm(formFromRightsRecord(result.items[0]));
      })
      .catch((requestError) => {
        if (active) {
          setHistoryStatus("error");
          setError(rightsError(requestError));
        }
      })
      .finally(() => {
        if (active) setBusy(null);
      });
    return () => {
      active = false;
    };
  }, [asset.id, clearDecision]);

  useEffect(() => {
    let active = true;
    void api
      .getWorkspaceCapabilities()
      .then((capabilities) => {
        if (active) setCanAdminister(capabilities.administrator);
      })
      .catch(() => {
        if (active) setCanAdminister(false);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    clearDecision();
  }, [
    asset.id,
    asset.version,
    asset.current_rights_record_id,
    asset.current_version_id,
    clearDecision,
  ]);

  useEffect(() => {
    const refreshOnFocus = () => {
      clearDecision();
      void reload().catch((requestError) => {
        setError(rightsError(requestError));
      });
    };
    window.addEventListener("focus", refreshOnFocus);
    return () => window.removeEventListener("focus", refreshOnFocus);
  }, [clearDecision, reload]);

  const requestUsability = useCallback(async () => {
    const generation = decisionGeneration.current + 1;
    decisionGeneration.current = generation;
    decisionObservedAt.current = null;
    setDecision(null);
    const observedAt = performance.now();
    const nextDecision = await api.checkUsability(asset.id, {
      asset_version_id: asset.current_version_id,
      purpose: decisionPurpose,
      provider: decisionProvider,
      requires_derivative: decisionDerivative,
      decision_time: new Date().toISOString(),
    });
    if (generation !== decisionGeneration.current) return null;
    decisionObservedAt.current = observedAt;
    setDecision(nextDecision);
    return nextDecision;
  }, [
    asset.current_version_id,
    asset.id,
    decisionDerivative,
    decisionProvider,
    decisionPurpose,
  ]);

  useEffect(() => {
    if (!decision?.authorized) return;
    if (decision.rights_record_id === null) {
      clearDecision();
      return;
    }
    const decidedRights = history?.items.find(
      (record) => record.id === decision.rights_record_id,
    );
    if (decidedRights === undefined) {
      clearDecision();
      return;
    }

    const boundaries: string[] = [];
    if (!decidedRights.perpetual) {
      if (decidedRights.valid_until === null) {
        clearDecision();
        return;
      }
      boundaries.push(decidedRights.valid_until);
    }
    if (asset.retention_deadline !== null) {
      boundaries.push(asset.retention_deadline);
    }
    if (boundaries.length === 0) {
      return;
    }

    const timestamps = boundaries.map((value) => new Date(value).getTime());
    const decidedAt = new Date(decision.decided_at).getTime();
    if (
      timestamps.some((value) => !Number.isFinite(value)) ||
      !Number.isFinite(decidedAt) ||
      decisionObservedAt.current === null
    ) {
      clearDecision();
      return;
    }
    const invalidationAt = Math.min(...timestamps);
    const serverLifetime = invalidationAt - decidedAt;

    let timer: number | undefined;
    const recheckAtBoundary = () => {
      const observedAt = decisionObservedAt.current;
      if (observedAt === null) {
        clearDecision();
        return;
      }
      const elapsed = Math.max(0, performance.now() - observedAt);
      const remaining = serverLifetime - elapsed;
      if (remaining <= 0) {
        void requestUsability().catch((requestError) => {
          clearDecision();
          setError(rightsError(requestError));
        });
        return;
      }
      timer = window.setTimeout(
        recheckAtBoundary,
        Math.min(remaining, 2_147_483_647),
      );
    };
    recheckAtBoundary();
    return () => {
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [
    asset.retention_deadline,
    clearDecision,
    decision,
    history,
    requestUsability,
  ]);

  const saveRights = async (event: FormEvent) => {
    event.preventDefault();
    clearDecision();
    setBusy("save");
    setError(null);
    const replacing = asset.current_rights_record_id !== null;
    try {
      const payload: RightsRecordMutationRequestV1 = {
        ...form,
        expected_asset_version: asset.version,
        asset_version_id: asset.current_version_id,
        valid_from: new Date(form.valid_from).toISOString(),
        valid_until: form.perpetual
          ? null
          : new Date(form.valid_until).toISOString(),
      };
      if (replacing) {
        await api.replaceRights(
          asset.id,
          payload,
          newUploadIdempotencyKey("rights-replace"),
        );
      } else {
        await api.registerRights(
          asset.id,
          payload,
          newUploadIdempotencyKey("rights-register"),
        );
      }
      await reload({ synchronizeForm: true });
    } catch (requestError) {
      setError(rightsError(requestError));
      if (
        requestError instanceof AssetApiError &&
        requestError.envelope?.code === "VERSION_CONFLICT"
      ) {
        setVersionConflict(true);
        await reload().catch(() => undefined);
      }
    } finally {
      setBusy(null);
    }
  };

  const revoke = async () => {
    clearDecision();
    setBusy("revoke");
    setError(null);
    try {
      await api.revokeRights(
        asset.id,
        {
          expected_asset_version: asset.version,
          reason: revokeReason,
          evidence_reference: revokeEvidence,
        },
        newUploadIdempotencyKey("rights-revoke"),
      );
      setRevokeReason("");
      setRevokeEvidence("");
      await reload({ synchronizeForm: true });
    } catch (requestError) {
      setError(rightsError(requestError));
      await reload().catch(() => undefined);
    } finally {
      setBusy(null);
    }
  };

  const administratorBlock = async () => {
    clearDecision();
    setBusy("block");
    setError(null);
    try {
      await api.administratorBlock(
        asset.id,
        {
          expected_asset_version: asset.version,
          reason: blockReason,
          evidence_reference: blockEvidence,
        },
        newUploadIdempotencyKey("administrator-block"),
      );
      setBlockReason("");
      setBlockEvidence("");
      await reload({ synchronizeForm: true });
    } catch (requestError) {
      setError(rightsError(requestError));
      await reload().catch(() => undefined);
    } finally {
      setBusy(null);
    }
  };

  const checkUsability = async () => {
    setBusy("decision");
    setError(null);
    try {
      await requestUsability();
    } catch (requestError) {
      setError(rightsError(requestError));
    } finally {
      setBusy(null);
    }
  };

  const loadMoreHistory = async () => {
    const beforeVersion = history?.next_cursor;
    if (beforeVersion === null || beforeVersion === undefined) return;
    setBusy("history-more");
    setError(null);
    try {
      const nextPage = await api.getRightsHistory(asset.id, {
        beforeVersion,
        limit: RIGHTS_HISTORY_PAGE_SIZE,
      });
      setHistory((currentHistory) => {
        if (currentHistory === null) return nextPage;
        const existingIds = new Set(
          currentHistory.items.map((record) => record.id),
        );
        return {
          items: [
            ...currentHistory.items,
            ...nextPage.items.filter((record) => !existingIds.has(record.id)),
          ],
          next_cursor: nextPage.next_cursor,
        };
      });
    } catch (requestError) {
      setError(rightsError(requestError));
    } finally {
      setBusy(null);
    }
  };

  const current = history?.items[0] ?? null;
  const permissionSetsEmpty =
    form.allowed_uses.length === 0 || form.allowed_providers.length === 0;
  const canSelectRights = [
    "PENDING_RIGHTS",
    "AVAILABLE",
    "BLOCKED",
    "RIGHTS_EXPIRED",
  ].includes(asset.status);
  const rightsEditingDisabled =
    !canSelectRights || busy !== null || versionConflict;
  const adoptLatestRights = () => {
    setForm(formFromRightsRecord(current ?? undefined));
    setVersionConflict(false);
    setError(null);
    clearDecision();
  };

  return (
    <section className="asset-rights" aria-labelledby="asset-rights-heading">
      <div className="asset-validation-heading">
        <h3 id="asset-rights-heading">使用权利</h3>
        <span>
          {current
            ? `记录 v${current.version_number} · 资产 v${asset.version}`
            : "默认拒绝"}
        </span>
      </div>

      {!canSelectRights ? (
        <div className="rights-denied" role="status">
          强制校验完成前不能登记或使用权利。
        </div>
      ) : null}
      {permissionSetsEmpty ? (
        <div className="rights-denied" role="status">
          用途或供应商为空时，素材保持不可用。
        </div>
      ) : null}
      {versionConflict ? (
        <div className="rights-denied" role="alert">
          <strong>检测到并发更新</strong>
          <span>本地草稿已冻结，载入最新记录后再重新编辑。</span>
          <button
            className="button button-secondary"
            disabled={busy !== null || historyStatus !== "ready"}
            onClick={adoptLatestRights}
            type="button"
          >
            载入最新权利并放弃本地草稿
          </button>
        </div>
      ) : null}

      <form className="rights-form" onSubmit={saveRights}>
        <div className="form-grid two-columns">
          <label>
            <span>权利人</span>
            <input
              disabled={rightsEditingDisabled}
              maxLength={256}
              onChange={(event) =>
                setForm({ ...form, owner_reference: event.target.value })
              }
              required
              value={form.owner_reference}
            />
          </label>
          <label>
            <span>许可来源</span>
            <input
              disabled={rightsEditingDisabled}
              maxLength={256}
              onChange={(event) =>
                setForm({ ...form, source: event.target.value })
              }
              required
              value={form.source}
            />
          </label>
          <label>
            <span>许可证引用</span>
            <input
              disabled={rightsEditingDisabled}
              maxLength={256}
              onChange={(event) =>
                setForm({ ...form, license_reference: event.target.value })
              }
              required
              value={form.license_reference}
            />
          </label>
          <label>
            <span>证据引用</span>
            <input
              disabled={rightsEditingDisabled}
              maxLength={512}
              onChange={(event) =>
                setForm({ ...form, evidence_reference: event.target.value })
              }
              required
              value={form.evidence_reference}
            />
          </label>
        </div>

        <fieldset>
          <legend>允许用途</legend>
          <div className="rights-options">
            {USE_OPTIONS.map(([value, label]) => (
              <label key={value}>
                <input
                  checked={form.allowed_uses.includes(value)}
                  disabled={rightsEditingDisabled}
                  onChange={() =>
                    setForm({
                      ...form,
                      allowed_uses: toggle(form.allowed_uses, value),
                    })
                  }
                  type="checkbox"
                />
                <span>{label}</span>
              </label>
            ))}
          </div>
        </fieldset>

        <fieldset>
          <legend>允许供应商</legend>
          <div className="rights-options">
            {PROVIDER_OPTIONS.map(([value, label]) => (
              <label key={value}>
                <input
                  checked={form.allowed_providers.includes(value)}
                  disabled={rightsEditingDisabled}
                  onChange={() =>
                    setForm({
                      ...form,
                      allowed_providers: toggle(
                        form.allowed_providers,
                        value,
                      ),
                    })
                  }
                  type="checkbox"
                />
                <span>{label}</span>
              </label>
            ))}
          </div>
        </fieldset>

        <div className="form-grid two-columns">
          <label>
            <span>生效时间</span>
            <input
              disabled={rightsEditingDisabled}
              onChange={(event) =>
                setForm({ ...form, valid_from: event.target.value })
              }
              required
              type="datetime-local"
              value={form.valid_from}
            />
          </label>
          <label>
            <span>失效时间（不含）</span>
            <input
              disabled={rightsEditingDisabled || form.perpetual}
              onChange={(event) =>
                setForm({ ...form, valid_until: event.target.value })
              }
              required={!form.perpetual}
              type="datetime-local"
              value={form.valid_until}
            />
          </label>
          <label>
            <span>条款 SHA-256</span>
            <input
              disabled={rightsEditingDisabled}
              maxLength={64}
              minLength={64}
              onChange={(event) =>
                setForm({ ...form, terms_sha256: event.target.value })
              }
              pattern="[0-9a-f]{64}"
              required
              value={form.terms_sha256}
            />
          </label>
        </div>

        <div className="rights-options">
          <label>
            <input
              checked={form.perpetual}
              disabled={rightsEditingDisabled}
              onChange={(event) =>
                setForm({
                  ...form,
                  perpetual: event.target.checked,
                  valid_until: event.target.checked ? "" : form.valid_until,
                })
              }
              type="checkbox"
            />
            <span>明确设为永久权利</span>
          </label>
          <label>
            <input
              checked={form.derivative_allowed}
              disabled={rightsEditingDisabled}
              onChange={(event) =>
                setForm({
                  ...form,
                  derivative_allowed: event.target.checked,
                })
              }
              type="checkbox"
            />
            <span>允许派生</span>
          </label>
          <label>
            <input
              checked={form.public_demo_allowed}
              disabled={rightsEditingDisabled}
              onChange={(event) =>
                setForm({
                  ...form,
                  public_demo_allowed: event.target.checked,
                })
              }
              type="checkbox"
            />
            <span>允许公开演示</span>
          </label>
        </div>

        <button
          className="button button-primary"
          disabled={rightsEditingDisabled}
          type="submit"
        >
          {busy === "save"
            ? "保存中…"
            : current
              ? "替换权利记录"
              : "登记权利记录"}
        </button>
      </form>

      {current?.decision === "GRANT" ? (
        <div className="rights-actions">
          <label>
            <span>撤销原因</span>
            <input
              disabled={busy !== null}
              onChange={(event) => setRevokeReason(event.target.value)}
              value={revokeReason}
            />
          </label>
          <label>
            <span>撤销证据</span>
            <input
              disabled={busy !== null}
              onChange={(event) => setRevokeEvidence(event.target.value)}
              value={revokeEvidence}
            />
          </label>
          <button
            className="button button-danger"
            disabled={!revokeReason || !revokeEvidence || busy !== null}
            onClick={() => void revoke()}
            type="button"
          >
            {busy === "revoke" ? "撤销中…" : "撤销当前权利"}
          </button>
        </div>
      ) : null}

      {canAdminister ? (
        <div className="rights-actions">
          <label>
            <span>管理员阻断原因</span>
            <input
              disabled={busy !== null}
              onChange={(event) => setBlockReason(event.target.value)}
              value={blockReason}
            />
          </label>
          <label>
            <span>阻断证据</span>
            <input
              disabled={busy !== null}
              onChange={(event) => setBlockEvidence(event.target.value)}
              value={blockEvidence}
            />
          </label>
          <button
            className="button button-danger"
            disabled={!blockReason || !blockEvidence || busy !== null}
            onClick={() => void administratorBlock()}
            type="button"
          >
            {busy === "block" ? "阻断中…" : "管理员阻断"}
          </button>
        </div>
      ) : null}

      {current ? (
        <div className="rights-decision">
          <div className="form-grid two-columns">
            <label>
              <span>决策用途</span>
              <select
                onChange={(event) => {
                  clearDecision();
                  setDecisionPurpose(event.target.value);
                }}
                value={decisionPurpose}
              >
                {USE_OPTIONS.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>决策供应商</span>
              <select
                onChange={(event) => {
                  clearDecision();
                  setDecisionProvider(event.target.value);
                }}
                value={decisionProvider}
              >
                {PROVIDER_OPTIONS.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <label className="rights-inline-option">
            <input
              checked={decisionDerivative}
              onChange={(event) => {
                clearDecision();
                setDecisionDerivative(event.target.checked);
              }}
              type="checkbox"
            />
            <span>本次使用需要派生</span>
          </label>
          <button
            className="button button-secondary"
            disabled={busy !== null}
            onClick={() => void checkUsability()}
            type="button"
          >
            检查当前可用性
          </button>
          {decision ? (
            <div
              className={
                decision.authorized ? "rights-authorized" : "rights-denied"
              }
              role="status"
            >
              <strong>
                {decision.authorized ? "允许使用" : "拒绝使用"}
              </strong>
              <span>
                {decision.reason_code} · 权利记录 v
                {decision.rights_record_version ?? "—"}
              </span>
              <span>
                决策时间：
                <time dateTime={decision.decided_at}>
                  {new Date(decision.decided_at).toLocaleString()}
                </time>
              </span>
            </div>
          ) : null}
        </div>
      ) : null}

      {historyStatus === "loading" ? (
        <div className="empty-state compact" role="status">
          <strong>正在载入权利历史</strong>
        </div>
      ) : historyStatus === "error" ? (
        <div className="empty-state compact">
          <strong>权利历史载入失败</strong>
          <button
            className="button button-secondary"
            disabled={busy !== null}
            onClick={() =>
              void reload().catch((requestError) =>
                setError(rightsError(requestError)),
              )
            }
            type="button"
          >
            重试
          </button>
        </div>
      ) : history?.items.length ? (
        <>
          <ol className="rights-history">
            {history.items.map((record) => (
              <li key={record.id}>
                <div>
                  <strong>
                    v{record.version_number} · {record.decision}
                  </strong>
                  <span>{record.created_by}</span>
                </div>
                <p>
                  用途：
                  {record.allowed_uses.length
                    ? record.allowed_uses.join("、")
                    : "无（拒绝）"}
                </p>
                <p>
                  供应商：
                  {record.allowed_providers.length
                    ? record.allowed_providers.join("、")
                    : "无（拒绝）"}
                </p>
                <p>
                  有效期：{record.valid_from} 至{" "}
                  {record.perpetual ? "永久" : `${record.valid_until}（不含）`}
                </p>
                <p className="rights-evidence">
                  证据：{record.evidence_reference}
                </p>
              </li>
            ))}
          </ol>
          {history.next_cursor !== null ? (
            <button
              className="button button-secondary"
              disabled={busy !== null}
              onClick={() => void loadMoreHistory()}
              type="button"
            >
              {busy === "history-more" ? "载入中…" : "载入更早记录"}
            </button>
          ) : null}
        </>
      ) : (
        <div className="empty-state compact">
          <strong>暂无权利记录</strong>
          <span>素材保持不可用，直到明确登记用途和供应商。</span>
        </div>
      )}

      {error ? (
        <div className="error-banner" role="alert">
          <strong>权利操作未完成</strong>
          <span>{error}</span>
        </div>
      ) : null}
    </section>
  );
}
