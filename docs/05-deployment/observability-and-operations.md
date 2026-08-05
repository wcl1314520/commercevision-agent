# 可观测性与运行维护

| 属性 | 值 |
|---|---|
| 状态 | decision |
| 最后更新 | 2026-08-05 |
| 适用版本 | Operations v1 / Phase 2–3 |

## OpenTelemetry

统一 Trace：

```text
Browser
  -> API
  -> MySQL Transaction
  -> Outbox
  -> RabbitMQ
  -> Agent Node
  -> MCP/Tool
  -> Provider
  -> Evaluator
  -> OSS/MySQL
```

核心属性：

- `trace_id`
- `request_id`
- `workflow_id`
- `workflow_version`
- `step_id`
- `checkpoint_id`
- `tool_execution_id`
- `generation_attempt_id`
- `provider`
- `model`
- `prompt_version`
- `evaluation_suite_version`

禁止记录 Secret、完整原图、完整 Prompt 和签名 URL。

### Phase 2 lifecycle

API、Outbox/Inbox、Worker、Provider、Milvus、MCP 与 Scheduler 使用同一 `commercevision.phase2.*`
span 命名空间。链路覆盖 upload/finalize/promotion、validation、Rights、Vision/ProductBrief、
embedding、Milvus、lexical/fusion/rerank/final Rights、临时引用、删除/reconciliation 与 rebuild。

允许跨边界传播 `trace_id`、`operation_id`、`workspace_id`、`target_id/version`、`event_id`、
`policy_id` 和 `provider_request_id`；这些 ID 只进入 span/log，不作为 metrics label。
`trace_id` 与 `provider_request_id` 始终哈希，其他含 URL、空格、`@`、`/` 或不安全字符的 ID
同样哈希。
错误只记录 normalized code/category/retryable/class，不记录异常 message。严禁 Secret、签名 URL、
原图、完整 Prompt、完整 OCR 和原始 provider payload。

## 日志

- JSON 结构化日志。
- 统一错误分类。
- User-facing message 与内部错误分离。
- 日志采样不能丢失安全和审计事件。
- 相同异常聚合，避免告警风暴。
- Prompt/模型输入只记录哈希、长度和脱敏摘要。

## 指标

### API

- 请求率、4xx/5xx。
- P50/P95/P99。
- 活跃连接和 SSE。
- 认证失败和限流。

### Workflow

- 各状态数量和停留时间。
- 人工等待时间。
- 恢复次数。
- 无限循环保护触发。
- 最终成功/失败分类。

### Queue

- Queue Depth。
- Oldest Message Age。
- Consumer 数。
- Retry 和 DLQ。
- Prefetch/处理时长。

### Agent

- 节点调用次数和时延。
- Schema 失败。
- Tool Call 成功率。
- Checkpoint 写入和恢复。
- Reflection 次数和改善率。
- Context Token/图片预算。

### Provider

- 成功率。
- 429、5xx、timeout。
- P50/P95 时延。
- 熔断状态。
- 成本。
- 未知结果数量。

### Phase 2 operations

- `commercevision.phase2.operation.events`、`commercevision.phase2.operation.lease_age`、
  `commercevision.phase2.operation.retries`、`commercevision.phase2.operation.dlq`。
- `commercevision.phase2.provider.calls|duration|errors|rate_limits`。
- `commercevision.phase2.rights.decisions` 与 `confirmations`。
- `commercevision.phase2.index.lag|stale_vectors`。
- `commercevision.phase2.retrieval.duration|candidates|degraded|unauthorized_recall`。
- `commercevision.phase2.deletion.backlog`。
- `commercevision.phase2.rebuild.processed|remaining`。

本地 Compose 的服务通过 OTLP/HTTP 发往 Collector。Collector 使用 memory limiter + batch，trace
输出到 debug exporter，metrics 同时输出到 Prometheus exporter：
`http://127.0.0.1:19464/metrics`。端口可通过 `CV_OTEL_METRICS_HOST_PORT` 调整，不需要生产 Secret。

### Asset Validation

- `commercevision.asset_validation.operations`：execute/reconcile 与 retryable/terminal 分类。
- `commercevision.asset_validation.completions`：`PENDING_REVIEW` 和 `PENDING_RIGHTS`。
- `commercevision.asset_validation.stage_runs` 与 `stage_results`：stage、复用、verdict、reason。
- `commercevision.asset_validation.operation.duration` 与 `stage.duration`。
- `commercevision.asset_validation.quarantine.age`。

Spans 使用 `commercevision.asset.validation` 和按 stage 命名的 child span。结构化日志只记录
Operation/Asset IDs、attempt、stage、verdict、reason、validator identity 和 retry 分类；
不记录 evidence dict、对象身份、签名 URL、文件字节或原始异常消息。告警和处置见
[Asset Validation Runbook](../runbooks/asset-validation.md)。

### Evaluation

- 各 Evaluator 通过率。
- 人工与 Judge 一致率。
- 分品类首轮通过率。
- Regression。

### Phase 3 planning and HITL

Planning Context、Prompt resolution、Planner、Plan versioning、Approval、Tool Policy、LangGraph
resume 与 SSE 使用 `commercevision.phase3.planning.*` span。允许关联的身份仅为 Workflow、Plan
ID/version、Context SHA-256、Prompt revision ID、Approval、Event、Operation、无条件哈希的 request
trace 与 Policy ID；身份不进入 metrics label。

指标如下：

- `commercevision.phase3.planning.context.clipped_sources`。
- `commercevision.phase3.planning.planner.validity` 与
  `commercevision.phase3.planning.planner.duration`。
- `commercevision.phase3.planning.revisions` 与
  `commercevision.phase3.planning.approvals.stale`。
- `commercevision.phase3.planning.policy.denials`。
- `commercevision.phase3.planning.human.wait` 与
  `commercevision.phase3.planning.human.confirmations`。
- `commercevision.phase3.planning.sse.clients`、
  `commercevision.phase3.planning.sse.reconnects` 与
  `commercevision.phase3.planning.sse.lag`。
- `commercevision.phase3.planning.resume.failures`。

这是 bounded-cardinality 契约。labels 只允许以下固定集合：Context `complete|clipped`；Planner
`valid|invalid`；revision `created|revised`；Approval/HITL `approve|reject|regenerate|stale|confirmed`；
Policy outcome `allowed|denied`，reason 只允许 Tool Registry 发布的固定枚举；SSE
`connected|emitted|disconnected`；resume
`succeeded|checkpoint_mismatch|contract_mismatch|execution_failed`。Workspace、Workflow、Plan、Approval、
Event、Operation、Prompt revision、Context hash、trace 和 Citation ID 均不得作为 label。

Raw Creative Plan、Prompt、Planning Context、Provider payload、Secret、签名 URL、arbitrary user text、
sensitive Citation、异常消息以及 SSE payload 不得进入 log/span/metric/event/error。错误只输出固定 code、
category、retryability 与 class。Collector/exporter 是 best-effort；SSE 不是 readiness，SSE 或浏览器降级
不得让控制面写命令摘流。

初始告警：Planner invalid 5 分钟 >1%（最少 20）；Planner stuck >5 分钟预警、>15 分钟 critical；
stale Approval 5 分钟 >5；resume `checkpoint_mismatch` 任意新增；Tool Policy denial 5 分钟 >20 且高于
基线 3 倍；SSE lag P95 连续 5 分钟 >10 秒；reconnect >3 次/客户端/分钟且总量 >30；等待人工任务距
retention expiry <24 小时。全部处置见
[Phase 3 Planning Observability Runbook](../runbooks/planning-observability.md)。

### 数据

- MySQL 连接、锁、慢查询和复制。
- Milvus Query/Insert 延迟、索引和容量。
- OSS 存储、下载和清理。
- Redis 命中率和内存。

## Dashboard

最小 Dashboard：

1. Control Plane SLO。
2. Workflow Funnel。
3. Queue 和 Worker。
4. Provider Health/Cost。
5. Agent Node 和 Tool。
6. Evaluation Quality。
7. MySQL/Milvus/OSS。
8. Retention 和 Security。

## 告警

| 告警 | 初始条件 |
|---|---|
| API 5xx | 5 分钟 > 2% |
| Workflow 受理失败 | 5 分钟 > 1% |
| Outbox | 最老未发布 > 2 分钟 |
| Queue | 最老消息 > 5 分钟 |
| DLQ | 任意新增 |
| Checkpoint | 写入失败或恢复失败 |
| Provider | 5 分钟错误率 > 30%，满足最小样本 |
| MySQL | 连接 > 80%、锁等待、切换事件 |
| Milvus | Query P95 超阈值、索引失败 |
| Retention | 72 小时后仍存在任务原始资产 |
| Budget | 日/月预算达到 70%、90%、100% |

告警必须链接 Runbook。

## Runbook

- API 大面积 5xx。
- MySQL 切换、连接耗尽和慢查询。
- RabbitMQ backlog/DLQ。
- Worker 卡死和 Lease 恢复。
- LangGraph Checkpoint 不一致。
- Milvus 不可用或索引错乱。
- Collection 重建停滞、验证失败或延迟退役失败（见 [Collection 重建与升级 Runbook](../runbooks/collection-rebuild.md)）。
- OSS 上传、下载和清理失败。
- Provider 限流、全故障和未知结果。
- Prompt/模型发布回滚。
- Secret 泄露和轮换。
- 公共 Demo 滥用和预算失控。
- 数据未按期删除。

上述 Phase 2 故障的统一信号、止损、恢复证明和升级条件见
[Phase 2 可观测性事故 Runbook](../runbooks/phase2-observability.md)。

Scheduler readiness 同时报告 `outbox_dispatch`、`workflow_recovery`、
`operation_recovery` 和 `upload_session_expiry` 的最近开始、最近成功、最近错误、耗时、
最近处理数和累计处理数，并单独累计自动过期的 Upload Session。Scanner 同轮并发启动并受
独立超时约束；单个 Scanner 异常或卡住只降低自己的状态，不阻止其他 Scanner。状态另外
报告 `in_progress`、`timed_out` 和累计超时数。Scheduler 只认领 MySQL 到期记录并原子创建
Cleanup Operation/Outbox，不访问对象存储；对象复核由 Operation Recovery 和 Worker 推进。

Celery Worker 在 `WorkController` 启动阶段验证 `worker_required_operation_kinds` 与
`commercevision.operation_executors` Entry Point，并在 fork 前探测 MySQL 及所选 Queue
所需依赖。Maintenance Worker 必须探测对象存储；Workflow-only Worker 将其标记为
`not_required`，不因无关存储故障停止消费。
Master 只在 Consumer 已就绪后写入 `CV_WORKER_READINESS_PATH`；每个 Prefork 子进程完成
Runtime/Executor 初始化后写入独立 PID 标记。容器健康检查要求共享标记有效、远端依赖结果
为 `ok`、当前存活子进程数量达到配置并且 RabbitMQ 可连接。缺失 Executor、Factory 加载
失败、远端依赖失败或任一 Runtime 初始化失败均发生在消费任务前，不依赖首条消息触发。

## 成本治理

- 每个 Workflow 记录模型和存储成本。
- 公共 Demo 使用日配额和单用户配额。
- Provider Router 考虑成本但不以牺牲质量为唯一目标。
- 预算 70% 告警，90% 降低公开配额，100% 停止付费生成并保留控制面。
- Evaluation 区分真实模型和 Fixture，CI 默认不产生大额调用。

## 事故复盘

P1/P2 事故需要：

- 时间线。
- 用户影响。
- 触发条件。
- 为什么监控没有提前发现。
- 数据和费用影响。
- 恢复步骤。
- 根因和促成因素。
- 可验证修复。
- 后续评测/测试用例。
