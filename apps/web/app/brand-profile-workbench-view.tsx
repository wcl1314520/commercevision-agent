"use client";

import type { FormEvent } from "react";

import type {
  BrandProfileControllerSnapshot,
} from "../lib/brand-profile-workbench-controller";
import type {
  BrandProfileDraftV1,
  BrandProfileResponseV1,
} from "../lib/generated/catalog-api";
import { BrandProfileCreateForm } from "./brand-profile-create-form";
import { BrandProfileDraftEditor } from "./brand-profile-draft-editor";
import { BrandProfilePublicationHistory } from "./brand-profile-publication-history";
import { BrandProfilePublicationPanel } from "./brand-profile-publication-panel";

export type BrandProfileWorkbenchStatus =
  | "loading"
  | "ready"
  | "empty"
  | "error";

export type BrandProfileReconciliationNotice = {
  kind: "pending" | "resolved" | "blocked";
  message: string;
};

function stateLabel(state: string): string {
  return (
    {
      DRAFT: "草稿",
      ACTIVE: "已发布",
      NEEDS_REPUBLISH: "需要重新发布",
      ARCHIVED: "已归档",
    }[state] ?? state
  );
}

function displayDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

export function BrandProfileWorkbenchView({
  brand,
  busy,
  canDiscardPendingCommand,
  canAdminister,
  capabilityDegraded,
  creatingAnother,
  creationDraft,
  error,
  notice,
  onCancelCreate,
  onCancelIdentityChange,
  onConfirmIdentityChange,
  onCreate,
  onCreationDraftChange,
  onDiscardConflict,
  onDiscardPendingCommand,
  onDraftChange,
  onLoadHistoryMore,
  onLoadMoreProfiles,
  onLoadVersion,
  onProfileKeyChange,
  onPublish,
  onRefresh,
  onRestoreConflict,
  onStartCreate,
  onSwitchProfile,
  onUpdateDraft,
  onValidate,
  pendingCommand,
  pendingIdentityChange,
  profileKey,
  profiles,
  profilesLoading,
  profilesNextCursor,
  snapshot,
  status,
  versionLoading,
}: {
  brand: string;
  busy: string | null;
  canDiscardPendingCommand: boolean;
  canAdminister: boolean;
  capabilityDegraded: boolean;
  creatingAnother: boolean;
  creationDraft: BrandProfileDraftV1;
  error: string | null;
  notice: BrandProfileReconciliationNotice | null;
  onCancelCreate: () => void;
  onCancelIdentityChange: () => void;
  onConfirmIdentityChange: () => void;
  onCreate: (event: FormEvent<HTMLFormElement>) => void;
  onCreationDraftChange: (draft: BrandProfileDraftV1) => void;
  onDiscardConflict: () => void;
  onDiscardPendingCommand: () => void;
  onDraftChange: (draft: BrandProfileDraftV1) => void;
  onLoadHistoryMore: () => void;
  onLoadMoreProfiles: () => void;
  onLoadVersion: (versionNumber: number) => void;
  onProfileKeyChange: (profileKey: string) => void;
  onPublish: () => void;
  onRefresh: () => void;
  onRestoreConflict: () => void;
  onStartCreate: () => void;
  onSwitchProfile: (profileId: string) => void;
  onUpdateDraft: (event: FormEvent<HTMLFormElement>) => void;
  onValidate: () => void;
  pendingCommand: boolean;
  pendingIdentityChange:
    | "switch-profile"
    | "start-create"
    | "cancel-create"
    | null;
  profileKey: string;
  profiles: BrandProfileResponseV1[];
  profilesLoading: boolean;
  profilesNextCursor: string | null;
  snapshot: BrandProfileControllerSnapshot;
  status: BrandProfileWorkbenchStatus;
  versionLoading: number | null;
}) {
  const profile = snapshot.profile;
  const draft = snapshot.draft;
  const validation =
    snapshot.validation?.profile_id === profile?.id &&
    snapshot.validation?.profile_version === profile?.version
      ? snapshot.validation
      : null;
  const archived = profile?.state === "ARCHIVED";
  const canMutate = canAdminister && !pendingCommand;
  const publishReady =
    canMutate &&
    !archived &&
    !snapshot.dirty &&
    validation?.valid === true &&
    busy === null;

  return (
    <section
      aria-labelledby="brand-profile-heading"
      className="panel brand-profile-panel"
    >
      <div className="panel-heading">
        <div>
          <p className="eyebrow">BRAND GOVERNANCE</p>
          <h2 id="brand-profile-heading">品牌档案</h2>
          <p className="panel-subtitle">
            为 {brand} 管理可审计规则、Foundation 素材和不可变发布历史。
          </p>
        </div>
        <div className="brand-profile-heading-status">
          {profile ? (
            <>
              <span
                className={`brand-profile-state state-${profile.state.toLowerCase()}`}
              >
                {stateLabel(profile.state)}
              </span>
              <span className="version-label">
                编辑版本 {profile.version}
              </span>
            </>
          ) : null}
        </div>
      </div>

      {error ? (
        <div className="brand-profile-error-banner" role="alert">
          <strong>品牌档案请求未完成</strong>
          <span>{error}</span>
        </div>
      ) : null}

      {notice ? (
        <div
          aria-live="polite"
          className={`brand-profile-reconciliation is-${notice.kind}`}
          role={notice.kind === "blocked" ? "alert" : "status"}
        >
          <span>{notice.message}</span>
          {canDiscardPendingCommand ? (
            <button
              className="button button-danger"
              onClick={onDiscardPendingCommand}
              type="button"
            >
              确认放弃该待对账命令
            </button>
          ) : null}
        </div>
      ) : null}

      {capabilityDegraded ? (
        <div className="brand-profile-readonly" role="alert">
          管理权限能力暂时无法确认；档案仍可读取，但所有写入与校验均已安全禁用。
        </div>
      ) : !canAdminister && status !== "loading" ? (
        <div className="brand-profile-readonly" role="status">
          当前 Workspace 为只读模式；发布、校验与草稿修改只对品牌管理员开放。
        </div>
      ) : null}

      {status === "loading" ? (
        <div className="empty-state compact">
          <strong>正在读取品牌档案</strong>
          <span>同时核对 Workspace 管理权限和最新发布状态。</span>
        </div>
      ) : null}

      {status === "error" ? (
        <div className="empty-state compact">
          <strong>品牌档案暂不可用</strong>
          <button
            className="button button-secondary"
            onClick={() => window.location.reload()}
            type="button"
          >
            重新载入
          </button>
        </div>
      ) : null}

      {status === "empty" ? (
        <BrandProfileCreateForm
          busy={busy !== null || pendingCommand}
          canAdminister={canMutate}
          draft={creationDraft}
          isFirstProfile
          onDraftChange={onCreationDraftChange}
          onProfileKeyChange={onProfileKeyChange}
          onSubmit={onCreate}
          profileKey={profileKey}
        />
      ) : null}

      {status === "ready" && profile && draft ? (
        <>
          <div className="brand-profile-toolbar">
            <label>
              <span>当前档案</span>
              <select
                disabled={
                  busy !== null || creatingAnother || pendingCommand
                }
                onChange={(event) =>
                  onSwitchProfile(event.target.value)
                }
                value={profile.id}
              >
                {profiles.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.profile_key} · {stateLabel(item.state)}
                  </option>
                ))}
              </select>
            </label>
            <div className="brand-profile-toolbar-actions">
              {profilesNextCursor ? (
                <button
                  className="button button-secondary"
                  disabled={profilesLoading || pendingCommand}
                  onClick={onLoadMoreProfiles}
                  type="button"
                >
                  {profilesLoading ? "载入中…" : "加载更多档案"}
                </button>
              ) : null}
              <button
                className="button button-secondary"
                disabled={
                  !canAdminister ||
                  pendingCommand ||
                  busy !== null ||
                  creatingAnother
                }
                onClick={onStartCreate}
                type="button"
              >
                新建档案
              </button>
              <button
                className="button button-secondary"
                disabled={
                  busy !== null || creatingAnother || pendingCommand
                }
                onClick={onRefresh}
                type="button"
              >
                刷新当前授权
              </button>
            </div>
          </div>

          {pendingIdentityChange ? (
            <div className="brief-stale-banner" role="alert">
              <div>
                <strong>
                  {pendingIdentityChange === "cancel-create"
                    ? "新建档案草稿尚未保存"
                    : "本地草稿尚未保存"}
                </strong>
                <span>
                  {pendingIdentityChange === "start-create"
                    ? "新建档案会丢弃当前本地修改；请选择继续新建或留在当前档案。"
                    : pendingIdentityChange === "cancel-create"
                      ? "返回当前档案会丢弃这份新建草稿；请选择明确丢弃或继续编辑。"
                      : "切换档案会丢弃当前本地修改；请选择继续切换或留在当前档案。"}
                </span>
              </div>
              <div className="brand-profile-toolbar-actions">
                <button
                  className="button button-danger"
                  disabled={pendingCommand}
                  onClick={onConfirmIdentityChange}
                  type="button"
                >
                  {pendingIdentityChange === "start-create"
                    ? "丢弃草稿并新建"
                    : pendingIdentityChange === "cancel-create"
                      ? "丢弃新建草稿并返回"
                      : "丢弃草稿并切换"}
                </button>
                <button
                  className="button button-secondary"
                  disabled={pendingCommand}
                  onClick={onCancelIdentityChange}
                  type="button"
                >
                  {pendingIdentityChange === "cancel-create"
                    ? "继续编辑新建档案"
                    : "保留当前草稿"}
                </button>
              </div>
            </div>
          ) : null}

          {creatingAnother ? (
            <BrandProfileCreateForm
              busy={busy !== null || pendingCommand}
              canAdminister={canMutate}
              draft={creationDraft}
              isFirstProfile={false}
              onCancel={onCancelCreate}
              onDraftChange={onCreationDraftChange}
              onProfileKeyChange={onProfileKeyChange}
              onSubmit={onCreate}
              profileKey={profileKey}
            />
          ) : (
            <>
              {profile.state === "NEEDS_REPUBLISH" ? (
                <div
                  className="brand-profile-stale-banner"
                  role="alert"
                >
                  <strong>当前发布已失去完整授权</strong>
                  <span>
                    Rights、有效期或 Asset 状态已变化（
                    {displayDate(profile.stale_at)}
                    ）。历史引用仍可审计，但不能继续授权检索或生成。
                  </span>
                </div>
              ) : null}

              {snapshot.conflict ? (
                <div className="brief-stale-banner" role="alert">
                  <div>
                    <strong>检测到版本冲突</strong>
                    <span>
                      已载入服务器版本{" "}
                      {snapshot.conflict.authoritativeProfile.version}
                      ，提交或刷新前的本地草稿仍完整保留。
                    </span>
                  </div>
                  <div className="brand-profile-toolbar-actions">
                    <button
                      className="button button-primary"
                      disabled={pendingCommand}
                      onClick={onRestoreConflict}
                      type="button"
                    >
                      恢复本地草稿
                    </button>
                    <button
                      className="button button-secondary"
                      disabled={pendingCommand}
                      onClick={onDiscardConflict}
                      type="button"
                    >
                      丢弃本地草稿
                    </button>
                  </div>
                </div>
              ) : null}

              <form className="catalog-form" onSubmit={onUpdateDraft}>
                <BrandProfileDraftEditor
                  disabled={
                    !canMutate || archived || busy !== null
                  }
                  onChange={onDraftChange}
                  value={draft}
                />
                <div className="form-actions">
                  <button
                    className="button button-primary"
                    disabled={
                      !canMutate ||
                      archived ||
                      !snapshot.dirty ||
                      busy !== null
                    }
                    type="submit"
                  >
                    {busy === "update" ? "保存中…" : "保存草稿"}
                  </button>
                  <span className="form-hint">
                    保存使用编辑版本 {profile.version}
                    ；冲突不会覆盖本地草稿。
                  </span>
                </div>
              </form>

              <BrandProfilePublicationPanel
                archived={archived}
                busy={busy}
                canAdminister={canMutate}
                dirty={snapshot.dirty}
                onPublish={onPublish}
                onValidate={onValidate}
                publishReady={publishReady}
                validation={validation}
              />

              <BrandProfilePublicationHistory
                disabled={pendingCommand}
                historyLoading={snapshot.historyLoading}
                historyStatus={snapshot.historyStatus}
                onLoadMore={onLoadHistoryMore}
                onLoadVersion={onLoadVersion}
                profileId={profile.id}
                selectedVersion={snapshot.selectedVersion}
                selectedVersionFocusNonce={
                  snapshot.selectedVersionFocusNonce
                }
                versions={snapshot.versions}
                versionsNextCursor={snapshot.versionsNextCursor}
                versionLoading={versionLoading}
              />
            </>
          )}
        </>
      ) : null}
    </section>
  );
}
