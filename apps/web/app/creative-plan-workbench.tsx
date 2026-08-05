"use client";

import { FormEvent, useState } from "react";

import type {
  CreativePlanCurrentResponseV1,
  CreativePlanVersionResponseV1,
  WorkflowResponse,
} from "../lib/generated/catalog-api";
import { creativePlanCommandAvailability } from "../lib/creative-plan-workbench-state";
import { useCreativePlanWorkbench } from "../lib/use-creative-plan-workbench";

const UUID_PATTERN =
  "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";

function StringList({ items }: { items: string[] }) {
  if (items.length === 0) return <span className="muted">无</span>;
  return (
    <ul className="creative-plan-string-list">
      {items.map((item) => (
        <li key={item}>{item}</li>
      ))}
    </ul>
  );
}

export function CreativePlanReview({
  current,
  workflow,
  visibleVersion = current.version,
}: {
  current: CreativePlanCurrentResponseV1;
  workflow: WorkflowResponse;
  visibleVersion?: CreativePlanVersionResponseV1;
}) {
  const { head } = current;
  const version = visibleVersion;
  const historical = version.version_number !== head.current_version_number;
  const planApprovals = (workflow.approvals ?? []).filter(
    (approval) =>
      approval.approval_type === "CREATIVE_PLAN" &&
      approval.subject_id === head.creative_plan_id,
  );
  const provenance = version.provenance;

  return (
    <section
      aria-labelledby="creative-plan-review-heading"
      className="creative-plan-review"
    >
      <div className="creative-plan-section-heading">
        <div>
          <p className="eyebrow">{historical ? "IMMUTABLE HISTORY" : "AUTHORITATIVE REVIEW"}</p>
          <h3 id="creative-plan-review-heading">
            {historical ? "历史不可变版本" : "当前权威审查快照"}
          </h3>
        </div>
        <div className="creative-plan-version-pair" aria-label="当前版本">
          <strong>方案版本 {version.version_number}</strong>
          <span>Workflow 版本 {workflow.version}</span>
        </div>
      </div>

      <dl className="creative-plan-authority-grid">
        <div>
          <dt>Workflow 状态</dt>
          <dd>{workflow.status}</dd>
        </div>
        <div>
          <dt>保留状态</dt>
          <dd>{workflow.retention_status}</dd>
        </div>
        <div>
          <dt>当前节点</dt>
          <dd>{workflow.current_node ?? "无"}</dd>
        </div>
        <div>
          <dt>方案来源</dt>
          <dd>{version.source}</dd>
        </div>
        <div className="creative-plan-wide-fact">
          <dt>Creative Plan</dt>
          <dd><code>{head.creative_plan_id}</code></dd>
        </div>
        <div className="creative-plan-wide-fact">
          <dt>Workflow</dt>
          <dd><code>{head.workflow_id}</code></dd>
        </div>
      </dl>

      <section aria-labelledby="creative-plan-provenance-heading">
        <h4 id="creative-plan-provenance-heading">来源与完整性</h4>
        <dl className="creative-plan-provenance-grid">
          <div>
            <dt>商品简报 · 版本 {provenance.product_brief_version}</dt>
            <dd><code>{provenance.product_brief_id}</code></dd>
            <dd><code>{provenance.product_brief_sha256}</code></dd>
          </div>
          <div>
            <dt>
              品牌档案
              {provenance.brand_profile_version
                ? ` · 版本 ${provenance.brand_profile_version}`
                : " · 未使用"}
            </dt>
            <dd><code>{provenance.brand_profile_id ?? "—"}</code></dd>
            <dd><code>{provenance.brand_profile_sha256 ?? "—"}</code></dd>
          </div>
          <div>
            <dt>规划上下文 · {provenance.context_policy_version}</dt>
            <dd><code>{provenance.context_sha256}</code></dd>
          </div>
          <div>
            <dt>Prompt · {provenance.prompt_revision}</dt>
            <dd><code>{provenance.prompt_id}</code></dd>
            <dd><code>{provenance.prompt_sha256}</code></dd>
          </div>
          <div className="creative-plan-wide-fact">
            <dt>检索运行</dt>
            <dd><code>{provenance.retrieval_run_id}</code></dd>
          </div>
          <div className="creative-plan-wide-fact">
            <dt>方案内容哈希</dt>
            <dd><code>{version.payload_sha256}</code></dd>
          </div>
        </dl>
      </section>

      <section aria-labelledby="creative-plan-directions-heading">
        <h4 id="creative-plan-directions-heading">创意方向</h4>
        <div className="creative-plan-directions">
          {version.payload.directions.map((direction) => (
            <article className="creative-plan-direction" key={direction.key}>
              <header>
                <div>
                  <span className="creative-plan-role">{direction.image_role}</span>
                  <h5>{direction.key}</h5>
                </div>
                <span>{direction.candidate_count} 个候选</span>
              </header>
              <dl className="creative-plan-direction-grid">
                <div><dt>场景</dt><dd>{direction.scene}</dd></div>
                <div><dt>构图</dt><dd>{direction.composition}</dd></div>
                <div><dt>镜头</dt><dd>{direction.camera}</dd></div>
                <div><dt>光线</dt><dd>{direction.lighting}</dd></div>
                <div><dt>色彩</dt><dd>{direction.color_direction}</dd></div>
              </dl>
              <div className="creative-plan-constraint-grid">
                <section>
                  <h6>商品约束</h6>
                  <StringList items={direction.product_constraints} />
                </section>
                <section>
                  <h6>必须元素</h6>
                  <StringList items={direction.required_elements} />
                </section>
                <section>
                  <h6>禁止元素</h6>
                  <StringList items={direction.prohibited_elements} />
                </section>
                <section>
                  <h6>质量目标</h6>
                  <StringList items={direction.quality_targets} />
                </section>
              </div>
              <section aria-labelledby={`citations-${direction.key}`}>
                <h6 id={`citations-${direction.key}`}>Retrieval Citations</h6>
                {direction.citation_selections.length > 0 ? (
                  <ul className="creative-plan-evidence-list">
                    {direction.citation_selections.map((citation) => (
                      <li key={citation.citation_id}>
                        <code>{citation.citation_id}</code>
                        <span>{citation.reason}</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="muted">此方向未选择检索引用。</p>
                )}
              </section>
              <section aria-labelledby={`intents-${direction.key}`}>
                <h6 id={`intents-${direction.key}`}>Tool Intents</h6>
                {direction.tool_intents.length > 0 ? (
                  <ul className="creative-plan-intent-list">
                    {direction.tool_intents.map((intent) => (
                      <li key={intent.intent_key}>
                        <div>
                          <strong>{intent.tool_name}</strong>
                          <span>{intent.purpose}</span>
                          <small>
                            {intent.schema_version} · 成本单位 {intent.estimated_cost_units}
                          </small>
                        </div>
                        <details>
                          <summary>查看结构化参数</summary>
                          <pre>{JSON.stringify(intent.arguments, null, 2)}</pre>
                        </details>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="muted">此方向未提出工具意图。</p>
                )}
              </section>
            </article>
          ))}
        </div>
      </section>

      <section aria-labelledby="creative-plan-approvals-heading">
        <h4 id="creative-plan-approvals-heading">方案审批历史</h4>
        {planApprovals.length > 0 ? (
          <ol className="creative-plan-approval-list">
            {planApprovals.map((approval) => (
              <li key={approval.id}>
                <strong>{approval.decision}</strong>
                <span>
                  方案 v{approval.subject_version} · Workflow v
                  {approval.expected_workflow_version}
                </span>
                <span>{approval.approved_by}</span>
                <time dateTime={approval.created_at}>{approval.created_at}</time>
              </li>
            ))}
          </ol>
        ) : (
          <div className="empty-state compact">
            <strong>尚无此方案的审批记录</strong>
            <span>当前页面不代表已授权执行；审批必须针对当前精确版本提交。</span>
          </div>
        )}
      </section>
    </section>
  );
}

export function CreativePlanWorkbench() {
  const workbench = useCreativePlanWorkbench();
  const [reasonCode, setReasonCode] = useState("");
  const [commentRef, setCommentRef] = useState("");

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void workbench.loadAuthority();
  };

  const data = workbench.readState.kind === "ready" ? workbench.readState.data : null;
  const visibleVersion = data?.versions.find(
    (version) => version.version_number === data.visibleVersionNumber,
  );
  const availability =
    data && visibleVersion
      ? creativePlanCommandAvailability(
          data.current,
          data.workflow,
          visibleVersion.version_number,
        )
      : null;
  const commandBusy = workbench.commandState.kind === "submitting";

  const streamLabel = {
    offline: "未连接",
    connecting: "正在连接事件流",
    live: "实时同步",
    reconnecting: "正在恢复事件流",
    degraded: "事件流已降级",
    "retention-expired": "事件保留期已结束",
    "policy-denied": "事件流访问被拒绝",
  }[workbench.streamState];

  return (
    <section className="panel creative-plan-panel" aria-labelledby="creative-plan-heading">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">PHASE 3 · PLAN REVIEW</p>
          <h2 id="creative-plan-heading">创意方案审查</h2>
          <p className="muted">
            页面只展示 API 返回的当前权威版本；本地状态不构成执行授权。
          </p>
        </div>
        <div className="creative-plan-heading-actions">
          {data ? (
            <span
              aria-live="polite"
              className={`creative-plan-stream-state is-${workbench.streamState}`}
            >
              {streamLabel}
            </span>
          ) : null}
          {data ? (
          <button className="button button-secondary" onClick={() => void workbench.loadAuthority()} type="button">
            刷新权威事实
          </button>
          ) : null}
        </div>
      </div>

      <form className="creative-plan-lookup" onSubmit={submit}>
        <label>
          <span>Workflow ID</span>
          <input
            disabled={commandBusy}
            maxLength={36}
            aria-describedby="creative-plan-lookup-help"
            onChange={(event) =>
              workbench.changeIdentity(event.target.value, workbench.creativePlanId)
            }
            pattern={UUID_PATTERN}
            required
            value={workbench.workflowId}
          />
        </label>
        <label>
          <span>Creative Plan ID</span>
          <input
            disabled={commandBusy}
            maxLength={36}
            aria-describedby="creative-plan-lookup-help"
            onChange={(event) =>
              workbench.changeIdentity(workbench.workflowId, event.target.value)
            }
            pattern={UUID_PATTERN}
            required
            value={workbench.creativePlanId}
          />
        </label>
        <button className="button button-primary" disabled={workbench.readState.kind === "loading"} type="submit">
          {workbench.readState.kind === "loading" ? "读取中…" : "读取审查事实"}
        </button>
        <p className="muted creative-plan-lookup-help" id="creative-plan-lookup-help">
          两个标识必须属于同一工作区；浏览器只保存恢复位置和未提交文本。
        </p>
      </form>

      {workbench.readState.kind === "idle" ? (
        <div className="empty-state compact">
          <strong>输入精确 Workflow 与 Creative Plan 标识</strong>
          <span>读取后可核对版本、来源、引用、工具意图和审批历史。</span>
        </div>
      ) : null}
      {workbench.readState.kind === "loading" ? (
        <div aria-label="创意方案审查事实加载中" className="creative-plan-loading">
          <span className="loading-bar wide" />
          <span className="loading-bar" />
          <span className="loading-bar" />
        </div>
      ) : null}
      {workbench.readState.kind === "error" ||
      workbench.readState.kind === "policy-denied" ||
      workbench.readState.kind === "retention-expired" ? (
        <div className="error-banner" role="alert">
          <strong>
            {workbench.readState.kind === "policy-denied"
              ? "策略拒绝访问"
              : workbench.readState.kind === "retention-expired"
                ? "保留期已结束"
                : "审查事实未加载"}
          </strong>
          <span>{workbench.readState.message}</span>
          <button className="button button-secondary" onClick={() => void workbench.loadAuthority()} type="button">
            重试
          </button>
        </div>
      ) : null}
      {data && visibleVersion ? (
        <div className="creative-plan-workspace">
          <aside aria-labelledby="creative-plan-history-heading" className="creative-plan-history">
            <div className="creative-plan-section-heading">
              <div>
                <p className="eyebrow">IMMUTABLE VERSIONS</p>
                <h3 id="creative-plan-history-heading">版本历史</h3>
              </div>
              {data.visibleVersionNumber !== data.current.head.current_version_number ? (
                <button
                  className="button button-secondary"
                  onClick={() =>
                    workbench.selectVersion(data.current.head.current_version_number)
                  }
                  type="button"
                >
                  返回当前版本
                </button>
              ) : null}
            </div>
            <ol className="creative-plan-version-list">
              {data.versions.map((version) => (
                <li key={version.id}>
                  <button
                    aria-current={
                      version.version_number === data.visibleVersionNumber
                        ? "true"
                        : undefined
                    }
                    onClick={() => workbench.selectVersion(version.version_number)}
                    type="button"
                  >
                    <strong>方案 v{version.version_number}</strong>
                    <span>{version.source} · {version.actor_id}</span>
                    <span>{version.revision_reason ?? "初始方案"}</span>
                    <time dateTime={version.created_at}>{version.created_at}</time>
                  </button>
                </li>
              ))}
            </ol>
            {data.nextCursor ? (
              <button
                className="button button-secondary creative-plan-load-older"
                onClick={() => void workbench.loadOlderVersions()}
                type="button"
              >
                加载更早版本
              </button>
            ) : (
              <p className="muted">已加载全部可用版本。</p>
            )}
          </aside>

          <div className="creative-plan-main-column">
            {workbench.streamState === "degraded" ||
            workbench.streamState === "retention-expired" ||
            workbench.streamState === "policy-denied" ? (
              <div className="warning-banner" role="status">
                <strong>{streamLabel}</strong>
                <span>
                  实时通知不可用；页面不会猜测状态，请手动刷新权威事实。
                </span>
              </div>
            ) : null}

            {workbench.commandState.kind === "failure" ? (
              <div className="error-banner" role="alert">
                <strong>
                  {workbench.commandState.failure.kind === "conflict"
                    ? "版本冲突，输入已保全"
                    : "命令未完成"}
                </strong>
                <span>{workbench.commandState.message}</span>
                {workbench.commandState.retryable ? (
                  <button
                    className="button button-secondary"
                    onClick={() => void workbench.retryPendingCommand()}
                    type="button"
                  >
                    使用同一幂等键重试
                  </button>
                ) : null}
              </div>
            ) : null}
            {workbench.commandState.kind === "success" ? (
              <div className="success-banner" role="status">
                <strong>权威状态已更新</strong>
                <span>{workbench.commandState.message}</span>
              </div>
            ) : null}

            <CreativePlanReview
              current={data.current}
              visibleVersion={visibleVersion}
              workflow={data.workflow}
            />

            <section aria-labelledby="creative-plan-command-heading" className="creative-plan-command-panel">
              <div className="creative-plan-section-heading">
                <div>
                  <p className="eyebrow">EXACT VERSION COMMANDS</p>
                  <h3 id="creative-plan-command-heading">修订与审批</h3>
                </div>
                <span className="creative-plan-command-subject">
                  方案 v{visibleVersion.version_number} · Workflow v{data.workflow.version}
                </span>
              </div>

              {availability?.reason ? (
                <p className="warning-banner" role="status">{availability.reason}</p>
              ) : null}

              {workbench.draft ? (
                <form
                  className="creative-plan-revision-form"
                  onSubmit={(event) => {
                    event.preventDefault();
                    workbench.submitRevision();
                  }}
                >
                  <label>
                    <span>方案 JSON（Creative Plan v1）</span>
                    <textarea
                      aria-describedby="creative-plan-json-help"
                      maxLength={60 * 1024}
                      onChange={(event) =>
                        workbench.updateRevision(
                          event.target.value,
                          workbench.draft?.revisionReason ?? "",
                        )
                      }
                      rows={18}
                      spellCheck={false}
                      value={workbench.draft.payloadText}
                    />
                  </label>
                  <p className="muted" id="creative-plan-json-help">
                    提交会创建新版本；当前版本与历史版本均不会被覆盖。
                  </p>
                  <label>
                    <span>修订原因</span>
                    <textarea
                      maxLength={512}
                      onChange={(event) =>
                        workbench.updateRevision(
                          workbench.draft?.payloadText ?? "",
                          event.target.value,
                        )
                      }
                      required
                      rows={3}
                      value={workbench.draft.revisionReason}
                    />
                  </label>
                  <div className="creative-plan-command-actions">
                    <button
                      className="button button-primary"
                      disabled={!availability?.revise || commandBusy}
                      type="submit"
                    >
                      {commandBusy ? "提交中…" : "创建新版本"}
                    </button>
                    <button
                      className="button button-secondary"
                      disabled={commandBusy}
                      onClick={workbench.cancelRevision}
                      type="button"
                    >
                      取消编辑
                    </button>
                  </div>
                </form>
              ) : (
                <button
                  className="button button-secondary"
                  disabled={!availability?.revise || commandBusy}
                  onClick={workbench.beginRevision}
                  type="button"
                >
                  基于当前版本创建修订
                </button>
              )}

              <form
                className="creative-plan-decision-form"
                onSubmit={(event) => event.preventDefault()}
              >
                <label>
                  <span>原因代码（可选）</span>
                  <input
                    maxLength={128}
                    onChange={(event) => setReasonCode(event.target.value)}
                    placeholder="例如 HUMAN_VERIFIED 或 NEEDS_REVISION"
                    value={reasonCode}
                  />
                </label>
                <label>
                  <span>备注引用（可选）</span>
                  <textarea
                    maxLength={512}
                    onChange={(event) => setCommentRef(event.target.value)}
                    rows={3}
                    value={commentRef}
                  />
                </label>
                <p className="muted">
                  决定只针对页面所示精确方案版本；冲突后不会自动重放。
                </p>
                <div className="creative-plan-command-actions">
                  <button
                    className="button button-primary"
                    disabled={!availability?.decide || commandBusy}
                    onClick={() => workbench.submitDecision("APPROVE", reasonCode, commentRef)}
                    type="button"
                  >
                    批准方案 v{visibleVersion.version_number}
                  </button>
                  <button
                    className="button button-danger"
                    disabled={!availability?.decide || commandBusy}
                    onClick={() => workbench.submitDecision("REJECT", reasonCode, commentRef)}
                    type="button"
                  >
                    驳回方案 v{visibleVersion.version_number}
                  </button>
                </div>
              </form>
            </section>
          </div>
        </div>
      ) : null}
    </section>
  );
}
