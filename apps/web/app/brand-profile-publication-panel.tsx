"use client";

import type { BrandProfileValidationResponseV1 } from "../lib/generated/catalog-api";

export function BrandProfilePublicationPanel({
  archived,
  busy,
  canAdminister,
  dirty,
  onPublish,
  onValidate,
  publishReady,
  validation,
}: {
  archived: boolean;
  busy: string | null;
  canAdminister: boolean;
  dirty: boolean;
  onPublish: () => void;
  onValidate: () => void;
  publishReady: boolean;
  validation: BrandProfileValidationResponseV1 | null;
}) {
  return (
    <div className="brand-profile-publication">
      <div className="brand-profile-section-heading">
        <div>
          <h3>发布前校验</h3>
          <p>
            服务端会在同一短事务中锁定所选成员，并以数据库当前时间重检 Foundation
            状态与当前 Rights。
          </p>
        </div>
        <div className="brand-profile-toolbar-actions">
          <button
            className="button button-secondary"
            disabled={
              !canAdminister || archived || dirty || busy !== null
            }
            onClick={onValidate}
            type="button"
          >
            {busy === "validate" ? "校验中…" : "校验当前草稿"}
          </button>
          <button
            className="button button-primary"
            disabled={!publishReady}
            onClick={onPublish}
            type="button"
          >
            {busy === "publish" ? "发布中…" : "发布不可变版本"}
          </button>
        </div>
      </div>
      {dirty ? (
        <p className="brand-profile-validation pending">
          请先保存草稿，再校验并发布。
        </p>
      ) : null}
      {validation ? (
        validation.valid ? (
          <p
            aria-live="polite"
            className="brand-profile-validation valid"
            role="status"
          >
            当前编辑版本的所有成员均通过实时授权校验。
          </p>
        ) : (
          <div className="brand-profile-validation invalid" role="alert">
            <strong>当前草稿不能发布</strong>
            <ul>
              {validation.issues.map((issue) => (
                <li
                  key={`${issue.asset_version_id}:${issue.role}:${issue.reason_code}`}
                >
                  <span>{issue.role}</span>
                  <code>{issue.asset_version_id}</code>
                  <strong>{issue.reason_code}</strong>
                  <p>{issue.message}</p>
                </li>
              ))}
            </ul>
          </div>
        )
      ) : null}
    </div>
  );
}
