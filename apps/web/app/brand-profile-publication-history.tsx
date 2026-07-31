"use client";

import { useEffect, useRef } from "react";

import type {
  BrandProfileDraftV1,
  BrandProfileVersionResponseV1,
} from "../lib/generated/catalog-api";

function displayDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function unusableMemberCount(version: BrandProfileVersionResponseV1): number {
  return version.members.filter((member) => !member.currently_usable).length;
}

function TextList({
  label,
  values,
}: {
  label: string;
  values: readonly string[];
}) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{values.length > 0 ? values.join("；") : "—"}</dd>
    </div>
  );
}

function FrozenDraft({ draft }: { draft: BrandProfileDraftV1 }) {
  return (
    <section
      aria-label="冻结品牌规则"
      className="brand-profile-frozen-draft"
    >
      <h4>发布时冻结规则</h4>
      <dl>
        <div>
          <dt>用途 / Provider</dt>
          <dd>
            {draft.purpose} / {draft.provider}
          </dd>
        </div>
        <div>
          <dt>派生授权</dt>
          <dd>{draft.requires_derivative ? "需要" : "不需要"}</dd>
        </div>
        <TextList label="必需标记" values={draft.required_marks} />
        <TextList label="禁止元素" values={draft.prohibited_elements} />
        <TextList label="语气约束" values={draft.tone_constraints} />
        <TextList label="文案约束" values={draft.copy_constraints} />
      </dl>
      <div className="brand-profile-frozen-groups">
        <div>
          <h5>规则</h5>
          {draft.rules.length > 0 ? (
            <ul>
              {draft.rules.map((rule, index) => (
                <li key={`${index}:${rule.code}`}>
                  <strong>{rule.code}</strong>
                  <span>{rule.scope}</span>
                  <p>{rule.instruction}</p>
                </li>
              ))}
            </ul>
          ) : (
            <p>—</p>
          )}
        </div>
        <div>
          <h5>批准色板</h5>
          {draft.approved_colors.length > 0 ? (
            <ul>
              {draft.approved_colors.map((color, index) => (
                <li key={`${index}:${color.name}`}>
                  <strong>{color.name}</strong>
                  <code>{color.value}</code>
                </li>
              ))}
            </ul>
          ) : (
            <p>—</p>
          )}
        </div>
        <div>
          <h5>冻结素材选择</h5>
          {draft.selected_assets.length > 0 ? (
            <ul>
              {draft.selected_assets.map((selection, index) => (
                <li
                  key={`${selection.asset_version_id}:${selection.role}:${index}`}
                >
                  <strong>{selection.role}</strong>
                  <code>{selection.asset_version_id}</code>
                </li>
              ))}
            </ul>
          ) : (
            <p>—</p>
          )}
        </div>
      </div>
    </section>
  );
}

export function BrandProfilePublicationHistory({
  disabled,
  historyLoading,
  historyStatus,
  onLoadMore,
  onLoadVersion,
  profileId,
  selectedVersion,
  selectedVersionFocusNonce,
  versions,
  versionsNextCursor,
  versionLoading,
}: {
  disabled: boolean;
  historyLoading: "initial" | "more" | null;
  historyStatus: "unloaded" | "loading" | "ready" | "error";
  onLoadMore: () => void;
  onLoadVersion: (versionNumber: number) => void;
  profileId: string;
  selectedVersion: BrandProfileVersionResponseV1 | null;
  selectedVersionFocusNonce: number;
  versions: BrandProfileVersionResponseV1[];
  versionsNextCursor: string | null;
  versionLoading: number | null;
}) {
  const detailRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (selectedVersionFocusNonce > 0) detailRef.current?.focus();
  }, [selectedVersionFocusNonce]);

  return (
    <>
      <div
        aria-busy={historyLoading !== null}
        className="brand-profile-history-section"
      >
        <div className="brand-profile-section-heading">
          <div>
            <h3>不可变发布历史</h3>
            <p>
              冻结引用用于审计；“当前可用”每次读取都会依据最新 Asset/Rights
              重新计算。
            </p>
          </div>
          <span aria-live="polite" className="version-label" role="status">
            {historyStatus === "ready" || versions.length > 0
              ? `${versions.length} 个版本`
              : historyStatus === "loading"
                ? "版本数读取中…"
                : "版本数未知"}
          </span>
        </div>
        {historyStatus === "error" ? (
          <div className="brand-profile-error-banner" role="alert">
            <strong>发布历史暂不可用</strong>
            <span>未能确认权威发布历史；请稍后刷新。</span>
          </div>
        ) : null}
        {versions.length === 0 && historyStatus === "ready" ? (
          <div className="empty-state compact">
            <strong>尚未发布</strong>
            <span>发布成功后会在这里显示内容哈希和精确授权引用。</span>
          </div>
        ) : versions.length > 0 ? (
          <ul className="brand-profile-history">
            {versions.map((version) => {
              const unavailable = unusableMemberCount(version);
              const detailId = `brand-profile-version-${profileId}-${version.version_number}`;
              return (
                <li key={version.id}>
                  <div>
                    <strong>发布版本 {version.version_number}</strong>
                    <span>{displayDate(version.published_at)}</span>
                  </div>
                  <code>{version.content_sha256}</code>
                  <p>
                    {version.members.length} 个冻结成员 ·{" "}
                    {unavailable === 0
                      ? "当前全部可用"
                      : `当前 ${unavailable} 个不可用`}
                  </p>
                  <button
                    aria-controls={detailId}
                    aria-pressed={
                      selectedVersion?.version_number ===
                      version.version_number
                    }
                    className="button button-secondary"
                    disabled={disabled || versionLoading !== null}
                    onClick={() => onLoadVersion(version.version_number)}
                    type="button"
                  >
                    {versionLoading === version.version_number
                      ? "读取中…"
                      : "查看冻结内容与当前可用性"}
                  </button>
                </li>
              );
            })}
          </ul>
        ) : null}
        {historyLoading ? (
          <p aria-live="polite" className="form-hint" role="status">
            正在读取发布历史…
          </p>
        ) : null}
        {versionsNextCursor ? (
          <button
            className="button button-secondary"
            disabled={disabled || historyLoading !== null}
            onClick={onLoadMore}
            type="button"
          >
            加载更多发布版本
          </button>
        ) : null}
      </div>

      {selectedVersion ? (
        <div
          aria-labelledby={`brand-profile-version-heading-${selectedVersion.version_number}`}
          className="brand-profile-version-detail"
          id={`brand-profile-version-${profileId}-${selectedVersion.version_number}`}
          ref={detailRef}
          tabIndex={-1}
        >
          <div className="brand-profile-section-heading">
            <div>
              <p className="eyebrow">
                PUBLICATION {selectedVersion.version_number}
              </p>
              <h3
                id={`brand-profile-version-heading-${selectedVersion.version_number}`}
              >
                冻结事实与当前授权
              </h3>
            </div>
            <span className="version-label">
              决策刷新于{" "}
              {displayDate(selectedVersion.members[0]?.decided_at)}
            </span>
          </div>
          <FrozenDraft draft={selectedVersion.draft} />
          <ul aria-label="发布成员当前授权">
            {selectedVersion.members.map((member) => (
              <li
                className={
                  member.currently_usable
                    ? "member-usable"
                    : "member-unusable"
                }
                key={`${member.asset_version_id}:${member.ordinal}`}
              >
                <div>
                  <strong>{member.role}</strong>
                  <span>
                    {member.currently_usable ? "当前可用" : "当前不可用"}
                  </span>
                </div>
                <dl>
                  <div>
                    <dt>Asset Version</dt>
                    <dd>{member.asset_version_id}</dd>
                  </div>
                  <div>
                    <dt>发布时 Rights</dt>
                    <dd>
                      {member.published_rights_record_id} · v
                      {member.published_rights_record_version}
                    </dd>
                  </div>
                  <div>
                    <dt>当前判断</dt>
                    <dd>{member.current_reason_code}</dd>
                  </div>
                </dl>
                {!member.currently_usable ? (
                  <p>此历史成员仅保留审计价值，不能作为检索或生成授权。</p>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </>
  );
}
