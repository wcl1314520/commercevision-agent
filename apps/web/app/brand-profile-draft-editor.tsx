"use client";

import { useId, useRef } from "react";

import type {
  BrandProfileDraftV1,
  BrandProfileMemberRole,
  BrandRuleScope,
} from "../lib/generated/catalog-api";
import {
  nextUniqueColorName,
  nextUniqueRuleCode,
  normalizeLineList,
  splitEditableLineList,
} from "../lib/brand-profile-editor-state";

const RULE_SCOPES: ReadonlyArray<{
  value: BrandRuleScope;
  label: string;
}> = [
  { value: "VISUAL", label: "视觉" },
  { value: "COPY", label: "文案" },
  { value: "COMPOSITION", label: "构图" },
  { value: "LEGAL", label: "法律与合规" },
  { value: "GENERAL", label: "通用" },
];

const MEMBER_ROLES: ReadonlyArray<{
  value: BrandProfileMemberRole;
  label: string;
}> = [
  { value: "LOGO", label: "Logo" },
  { value: "REQUIRED_MARK", label: "必需标记" },
  { value: "VISUAL_REFERENCE", label: "视觉参考" },
  { value: "PROMPT_TEMPLATE", label: "Prompt 模板" },
  { value: "MODEL_CONFIGURATION", label: "模型配置" },
  { value: "LORA", label: "LoRA" },
];

function textLines(values: string[]): string {
  return values.join("\n");
}

function useStableEditorKeys(prefix: string, length: number) {
  const instanceId = useId();
  const nextKey = useRef(0);
  const keys = useRef<string[]>([]);
  while (keys.current.length < length) {
    const sequence = nextKey.current;
    nextKey.current += 1;
    keys.current.push(`${instanceId}-${prefix}-${sequence}`);
  }
  if (keys.current.length > length) keys.current.length = length;
  return {
    keys: keys.current,
    removeAt(index: number) {
      keys.current.splice(index, 1);
    },
  };
}

export function emptyBrandProfileDraft(): BrandProfileDraftV1 {
  return {
    rules: [],
    approved_colors: [],
    required_marks: [],
    prohibited_elements: [],
    tone_constraints: [],
    copy_constraints: [],
    purpose: "BRAND_PROFILE",
    provider: "qwen-vl",
    requires_derivative: true,
    selected_assets: [],
  };
}

export function BrandProfileDraftEditor({
  value,
  onChange,
  disabled = false,
}: {
  value: BrandProfileDraftV1;
  onChange: (draft: BrandProfileDraftV1) => void;
  disabled?: boolean;
}) {
  const ruleEditorKeys = useStableEditorKeys("rule", value.rules.length);
  const colorEditorKeys = useStableEditorKeys(
    "color",
    value.approved_colors.length,
  );
  const assetEditorKeys = useStableEditorKeys(
    "asset",
    value.selected_assets.length,
  );
  const update = <Key extends keyof BrandProfileDraftV1>(
    key: Key,
    next: BrandProfileDraftV1[Key],
  ) => onChange({ ...value, [key]: next });

  return (
    <div className="brand-profile-draft">
      <div className="brand-profile-section-heading">
        <div>
          <h3>品牌规则</h3>
          <p>每条规则使用稳定代码，发布后会进入不可变内容哈希。</p>
        </div>
        <button
          className="button button-secondary"
          disabled={disabled || value.rules.length >= 64}
          onClick={() =>
            update("rules", [
              ...value.rules,
              {
                code: nextUniqueRuleCode(value.rules),
                scope: "GENERAL",
                instruction: "Describe the required brand behavior.",
              },
            ])
          }
          type="button"
        >
          添加规则
        </button>
      </div>
      {value.rules.length === 0 ? (
        <p className="form-hint">当前没有规则；可仅使用色板和受控素材发布。</p>
      ) : (
        <div className="brand-profile-repeat-list">
          {value.rules.map((rule, index) => (
            <fieldset
              className="brand-profile-repeat-item"
              disabled={disabled}
              key={ruleEditorKeys.keys[index]}
            >
              <legend>规则 {index + 1}</legend>
              <div className="form-grid">
                <label>
                  <span>规则代码</span>
                  <input
                    maxLength={128}
                    onChange={(event) =>
                      update(
                        "rules",
                        value.rules.map((item, itemIndex) =>
                          itemIndex === index
                            ? { ...item, code: event.target.value }
                            : item,
                        ),
                      )
                    }
                    required
                    value={rule.code}
                  />
                </label>
                <label>
                  <span>规则范围</span>
                  <select
                    onChange={(event) =>
                      update(
                        "rules",
                        value.rules.map((item, itemIndex) =>
                          itemIndex === index
                            ? {
                                ...item,
                                scope: event.target.value as BrandRuleScope,
                              }
                            : item,
                        ),
                      )
                    }
                    value={rule.scope}
                  >
                    {RULE_SCOPES.map((scope) => (
                      <option key={scope.value} value={scope.value}>
                        {scope.label}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <label>
                <span>规则说明</span>
                <textarea
                  maxLength={1024}
                  onChange={(event) =>
                    update(
                      "rules",
                      value.rules.map((item, itemIndex) =>
                        itemIndex === index
                          ? { ...item, instruction: event.target.value }
                          : item,
                      ),
                    )
                  }
                  required
                  rows={3}
                  value={rule.instruction}
                />
              </label>
              <button
                className="button button-danger"
                onClick={() => {
                  ruleEditorKeys.removeAt(index);
                  update(
                    "rules",
                    value.rules.filter(
                      (_item, itemIndex) => itemIndex !== index,
                    ),
                  );
                }}
                type="button"
              >
                删除规则
              </button>
            </fieldset>
          ))}
        </div>
      )}

      <div className="brand-profile-section-heading">
        <div>
          <h3>批准色板</h3>
          <p>颜色必须使用大写十六进制，例如 #1457FF。</p>
        </div>
        <button
          className="button button-secondary"
          disabled={disabled || value.approved_colors.length >= 32}
          onClick={() =>
            update("approved_colors", [
              ...value.approved_colors,
              {
                name: nextUniqueColorName(value.approved_colors),
                value: "#000000",
              },
            ])
          }
          type="button"
        >
          添加颜色
        </button>
      </div>
      <div className="brand-profile-color-grid">
        {value.approved_colors.map((color, index) => (
          <fieldset disabled={disabled} key={colorEditorKeys.keys[index]}>
            <legend>颜色 {index + 1}</legend>
            <label>
              <span>名称</span>
              <input
                maxLength={64}
                onChange={(event) =>
                  update(
                    "approved_colors",
                    value.approved_colors.map((item, itemIndex) =>
                      itemIndex === index
                        ? { ...item, name: event.target.value }
                        : item,
                    ),
                  )
                }
                value={color.name}
              />
            </label>
            <label>
              <span>色值</span>
              <input
                maxLength={9}
                onChange={(event) =>
                  update(
                    "approved_colors",
                    value.approved_colors.map((item, itemIndex) =>
                      itemIndex === index
                        ? {
                            ...item,
                            value: event.target.value.toUpperCase(),
                          }
                        : item,
                    ),
                  )
                }
                pattern="#[0-9A-F]{6}([0-9A-F]{2})?"
                value={color.value}
              />
            </label>
            <button
              className="button button-danger"
              onClick={() => {
                colorEditorKeys.removeAt(index);
                update(
                  "approved_colors",
                  value.approved_colors.filter(
                    (_item, itemIndex) => itemIndex !== index,
                  ),
                );
              }}
              type="button"
            >
              删除颜色
            </button>
          </fieldset>
        ))}
      </div>

      <div className="form-grid brand-profile-text-grid">
        {[
          ["required_marks", "必需标记", "每行一个必须出现的品牌标记"],
          [
            "prohibited_elements",
            "禁止元素",
            "每行一个禁止出现的视觉或文案元素",
          ],
          ["tone_constraints", "语气约束", "每行一个语气要求"],
          ["copy_constraints", "文案约束", "每行一个文案限制"],
        ].map(([key, label, hint]) => (
          <label key={key}>
            <span>{label}</span>
            <textarea
              disabled={disabled}
              onChange={(event) =>
                update(
                  key as
                    | "required_marks"
                    | "prohibited_elements"
                    | "tone_constraints"
                    | "copy_constraints",
                  splitEditableLineList(event.target.value),
                )
              }
              onBlur={(event) =>
                update(
                  key as
                    | "required_marks"
                    | "prohibited_elements"
                    | "tone_constraints"
                    | "copy_constraints",
                  normalizeLineList(event.currentTarget.value),
                )
              }
              placeholder={hint}
              rows={4}
              value={textLines(
                value[
                  key as
                    | "required_marks"
                    | "prohibited_elements"
                    | "tone_constraints"
                    | "copy_constraints"
                ],
              )}
            />
          </label>
        ))}
      </div>

      <div className="form-grid">
        <label>
          <span>授权用途</span>
          <input
            disabled={disabled}
            maxLength={128}
            onChange={(event) => update("purpose", event.target.value)}
            required
            value={value.purpose}
          />
        </label>
        <label>
          <span>执行 Provider</span>
          <input
            disabled={disabled}
            maxLength={128}
            onChange={(event) => update("provider", event.target.value)}
            required
            value={value.provider}
          />
        </label>
      </div>
      <label className="brand-profile-checkbox">
        <input
          checked={value.requires_derivative}
          disabled={disabled}
          onChange={(event) =>
            update("requires_derivative", event.target.checked)
          }
          type="checkbox"
        />
        <span>生成链路需要派生作品授权</span>
      </label>

      <div className="brand-profile-section-heading">
        <div>
          <h3>Foundation 素材版本</h3>
          <p>
            选择精确 Asset Version；发布时服务端会锁定并重新验证当前 Asset 与 Rights。
          </p>
        </div>
        <button
          className="button button-secondary"
          disabled={disabled || value.selected_assets.length >= 64}
          onClick={() =>
            update("selected_assets", [
              ...value.selected_assets,
              { asset_version_id: "", role: "VISUAL_REFERENCE" },
            ])
          }
          type="button"
        >
          添加素材版本
        </button>
      </div>
      <div className="brand-profile-repeat-list">
        {value.selected_assets.map((selection, index) => (
          <fieldset
            className="brand-profile-repeat-item"
            disabled={disabled}
            key={assetEditorKeys.keys[index]}
          >
            <legend>素材 {index + 1}</legend>
            <div className="form-grid">
              <label>
                <span>Asset Version ID</span>
                <input
                  onChange={(event) =>
                    update(
                      "selected_assets",
                      value.selected_assets.map((item, itemIndex) =>
                        itemIndex === index
                          ? {
                              ...item,
                              asset_version_id: event.target.value,
                            }
                          : item,
                      ),
                    )
                  }
                  placeholder="00000000-0000-0000-0000-000000000000"
                  required
                  value={selection.asset_version_id}
                />
              </label>
              <label>
                <span>成员角色</span>
                <select
                  onChange={(event) =>
                    update(
                      "selected_assets",
                      value.selected_assets.map((item, itemIndex) =>
                        itemIndex === index
                          ? {
                              ...item,
                              role: event.target.value as BrandProfileMemberRole,
                            }
                          : item,
                      ),
                    )
                  }
                  value={selection.role}
                >
                  {MEMBER_ROLES.map((role) => (
                    <option key={role.value} value={role.value}>
                      {role.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <button
              className="button button-danger"
              onClick={() => {
                assetEditorKeys.removeAt(index);
                update(
                  "selected_assets",
                  value.selected_assets.filter(
                    (_item, itemIndex) => itemIndex !== index,
                  ),
                );
              }}
              type="button"
            >
              删除素材
            </button>
          </fieldset>
        ))}
      </div>
    </div>
  );
}
