"use client";

import type { FormEvent } from "react";

import type { BrandProfileDraftV1 } from "../lib/generated/catalog-api";
import { BrandProfileDraftEditor } from "./brand-profile-draft-editor";

export function BrandProfileCreateForm({
  busy,
  canAdminister,
  draft,
  isFirstProfile,
  onCancel,
  onDraftChange,
  onProfileKeyChange,
  onSubmit,
  profileKey,
}: {
  busy: boolean;
  canAdminister: boolean;
  draft: BrandProfileDraftV1;
  isFirstProfile: boolean;
  onCancel?: () => void;
  onDraftChange: (draft: BrandProfileDraftV1) => void;
  onProfileKeyChange: (profileKey: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  profileKey: string;
}) {
  const disabled = !canAdminister || busy;
  return (
    <form
      aria-label={isFirstProfile ? "创建第一份品牌档案" : "创建另一份品牌档案"}
      className="catalog-form brand-profile-create-form"
      onSubmit={onSubmit}
    >
      <div className="brand-profile-section-heading">
        <div>
          <h3>
            {isFirstProfile ? "创建第一份品牌档案" : "创建另一份品牌档案"}
          </h3>
          <p>档案键在同一 Workspace 与品牌内唯一，创建后不可静默改名。</p>
        </div>
        {!isFirstProfile && onCancel ? (
          <button
            className="button button-secondary"
            disabled={busy}
            onClick={onCancel}
            type="button"
          >
            取消创建
          </button>
        ) : null}
      </div>
      <label>
        <span>档案键</span>
        <input
          autoFocus={!isFirstProfile}
          disabled={disabled}
          maxLength={128}
          onChange={(event) => onProfileKeyChange(event.target.value)}
          pattern="[A-Za-z0-9][A-Za-z0-9._:-]{0,127}"
          required
          value={profileKey}
        />
      </label>
      <BrandProfileDraftEditor
        disabled={disabled}
        onChange={onDraftChange}
        value={draft}
      />
      <button
        className="button button-primary"
        disabled={disabled}
        type="submit"
      >
        {busy ? "创建中…" : "创建品牌档案"}
      </button>
    </form>
  );
}
