# Phase 3 Planning Observability Runbook

| 属性 | 值 |
|---|---|
| 状态 | verified |
| 最后更新 | 2026-08-05 |
| 适用版本 | Phase 3 / Planning + HITL |
| 所有者 | On-call / Control API / Workflow Worker |

## 操作原则

MySQL 中的 Workflow、Creative Plan 精确版本、Approval、Outbox 和保留期是权威；LangGraph
Checkpoint 只承载可恢复执行状态。先停止新的副作用，再沿 `workflow_id`、`plan_id/version`、
`context_sha256`、Prompt revision、`approval_id`、`event_id`、`operation_id`、哈希后的 `trace_id`
和 `policy_id` 关联事实。不得手工伪造 Approval、跳过 Tool Policy、覆盖 Checkpoint、延长已过期
保留期或重放未经 MySQL 再验证的 resume payload。

事故材料不得复制 Raw Creative Plan、Prompt、Planning Context、Provider payload、Secret、任意用户
文本或 sensitive Citation。只保留稳定 ID、哈希、计数、时延和本文列出的固定原因码。

## Stuck planning

- **Signal**：Planner duration P95 升高，Workflow 在 `PLANNING/create_plan` 超过 5 分钟，且没有新
  version/event；15 分钟为 critical。
- **Containment**：暂停受影响 Workspace 的新规划消费；保留原 Operation、Inbox、Checkpoint 和
  幂等键，不创建替代 Workflow。
- **Recovery**：按 trace→Workflow→Operation→Checkpoint 检查 Worker readiness、队列 oldest age、
  Planning Context/Prompt 解析 span；修复依赖后让原消息或 lease recovery 继续。
- **Recovery proof**：原 Workflow 产生且只产生一个新 Plan version，Outbox/Inbox 收敛，进入
  `AWAITING_PLAN_APPROVAL`，无重复版本或 Provider 调用。
- **Escalate**：超过 15 分钟、多个 Workspace 同时受影响、出现幂等冲突或 Checkpoint/数据库身份不一致。

## Invalid Planner output

- **Signal**：`planner.validity{valid=false}` 5 分钟错误率超过 1%（最少 20 次），或评测/生产出现任一
  未授权 Tool Intent、缺失 Citation/Provenance。
- **Containment**：停止对应 Prompt revision/模型路由；禁止放宽 schema、重试上限或 Tool Policy。
- **Recovery**：核对固定 Prompt revision、Planning Context hash、输出 schema 与规范化错误码；回滚到最近
  通过 release gate 的 revision，再以原幂等键做有界重试。
- **Recovery proof**：固定夹具与隐藏 release suite 全绿，新 Plan 通过 schema/provenance/Citation 检查，
  恶意文本仍只作为数据且未进入 Tool Registry authority。
- **Escalate**：任何安全违规、连续两个 revision 失败，或无原始 payload 也无法定位 normalized error。

## Stale approval

- **Signal**：`approvals.stale` 5 分钟内任一 Workspace 超过 5 次，或同一 Plan 连续发生 stale conflict。
- **Containment**：阻止该 Approval 的 Tool execution/resume；不得把旧 Approval 迁移到新 Plan version。
- **Recovery**：从 MySQL 读取当前 Plan head/Workflow version，让操作员刷新并针对精确当前版本重新决策；
  原 Approval 保留审计但不再授权。
- **Recovery proof**：新的 Approval 同时匹配 Workflow version、Plan ID/version 与 retention，Tool Policy 再验证
  成功；旧 Approval 仍被稳定拒绝。
- **Escalate**：旧 Approval 能执行、跨 Workspace 命中，或冲突在刷新当前版本后仍重复。

## Repeated rejection

- **Signal**：同一 Plan 达到 8 次 rejection 预警；达到服务端上限 10 次，或 revision 数 5 分钟快速增长。
- **Containment**：停止自动 revise loop，保持 Workflow 在可解释的人工状态，不提高 rejection 上限。
- **Recovery**：比较固定原因码、Plan version lineage、Prompt revision 与 Context hash；由操作员决定修改输入、
  回滚 Prompt 或结束 Workflow，所有选择创建新精确版本/审计事件。
- **Recovery proof**：循环停止；若继续，下一次审批只指向新 head 且旧版本不能获批；若结束则进入终态。
- **Escalate**：接近 10 次仍无可操作原因、版本 lineage 断裂，或出现重复/跳号版本。

## Resume mismatch

- **Signal**：`resume.failures` 任意 `checkpoint_mismatch` 立即告警；5 分钟超过 3 次升级。
- **Containment**：停止相关 Workflow 的 side-effecting node；保留 resume Outbox、Approval 和全部 Checkpoint。
- **Recovery**：核对 resume event 的 Workflow/Approval/subject/resulting version 与 MySQL authority；确认正确的
  checkpoint generation/namespace。只重放原持久化事件，不手工拼 payload。
- **Recovery proof**：恢复从匹配 generation 继续，精确 Approval 被再次验证，副作用幂等且只发生一次。
- **Escalate**：找不到 durable checkpoint、多个 generation 同时匹配、或未知副作用结果无法 reconciliation。

## Tool Policy denial surge

- **Signal**：`policy.denials` 5 分钟超过 20 且高于同类基线 3 倍；`REGISTRY_DENIED` 或
  `RESOURCE_DENIED` 的突增立即安全审查。
- **Containment**：停止受影响 Prompt revision/Workspace 的执行消费，保持控制面读取与审批可用；不扩大
  scope、provider、resource、quota 或 budget。
- **Recovery**：按固定 reason label 分组，核对服务端 Registry、entitlements、Rights 与精确 Approval；若是
  Prompt 漂移则回滚，否则修复权威配置后重新授权当前 Plan。
- **Recovery proof**：允许项仅使用注册工具/资源/Provider/成本类，拒绝项无 idempotency key 且从未执行。
- **Escalate**：疑似 Prompt injection、越权成功、denial reason 不在白名单，或多个 Workspace 同时突增。

## SSE lag or reconnect storm

- **Signal**：`sse.lag` P95 连续 5 分钟超过 10 秒，或每客户端估算 reconnect 超过 3 次/分钟且总量超过 30；
  process-local client samples 接近连接预算 80%。
- **Containment**：保护短事务 JSON/command API；对新 SSE 连接限流并依赖 `Last-Event-ID` 恢复，不摘除控制 API。
- **Recovery**：检查 Outbox publish lag、MySQL event page latency、代理 buffering/idle timeout 和客户端退避；
  修复后从最后签名 cursor 重连。
- **Recovery proof**：事件按 persisted order 补齐、无重复业务命令，P95 lag 低于 10 秒且 reconnect 回落。
- **Escalate**：cursor 验签失败突增、事件缺口、连接耗尽影响 command writes，或 lag 超过 15 分钟。

## Retention expiry

- **Signal**：等待人工的 Workflow 距 expiry 少于 24 小时预警；任何过期 Context/Plan/Approval 仍可读取或执行
  立即告警。
- **Containment**：阻止新的规划、审批、resume 与 Tool execution；不得延长原事实或恢复已删除 payload。
- **Recovery**：确认 scheduler/cleanup 与 deletion evidence；需要继续业务时创建新 Workflow，重新确认输入、
  Context、Prompt 与 Approval，不复用过期身份。
- **Recovery proof**：过期精确版本稳定返回 retention/not-found，受控存储清理完成，新 Workflow lineage 独立。
- **Escalate**：删除截止时间越界、过期数据仍可见/执行、或无法证明所有 Provider/对象副本已清理。

## Readiness 与遥测降级

Control API readiness 只包含进程实际需要的 MySQL 与对象存储；Workflow Worker 包含 MySQL、RabbitMQ、
Checkpoint/Executor 和其拥有的执行依赖。OpenTelemetry Collector、Prometheus scrape、浏览器 SSE 连接和
可选客户端均不是业务 readiness 依赖。Exporter 或 SSE 失败必须显式记录/降级，但不得让审批、版本写入、
取消等控制面命令失效。
