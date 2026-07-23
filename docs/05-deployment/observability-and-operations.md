# 可观测性与运行维护

| 属性 | 值 |
|---|---|
| 状态 | decision |
| 最后更新 | 2026-07-21 |
| 适用版本 | Operations v1 |

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

### Evaluation

- 各 Evaluator 通过率。
- 人工与 Judge 一致率。
- 分品类首轮通过率。
- Regression。

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
- OSS 上传、下载和清理失败。
- Provider 限流、全故障和未知结果。
- Prompt/模型发布回滚。
- Secret 泄露和轮换。
- 公共 Demo 滥用和预算失控。
- 数据未按期删除。

Scheduler readiness 同时报告 `outbox_dispatch`、`workflow_recovery` 和
`operation_recovery` 的最近开始、最近成功、最近错误、耗时、最近处理数和累计处理数。
Scanner 同轮并发启动并受独立超时约束；单个 Scanner 异常或卡住只降低自己的状态，不阻止
其他 Scanner。状态另外报告 `in_progress`、`timed_out` 和累计超时数。

Celery Worker 在 `WorkController` 启动阶段验证 `worker_required_operation_kinds` 与
`commercevision.operation_executors` Entry Point。每个 Worker Process 完成 Runtime 和
Executor 初始化后写入 `CV_WORKER_READINESS_PATH`；容器健康检查要求该标记存在。缺失
Executor、Factory 加载失败或 Runtime 初始化失败均发生在接收任务前，不依赖首条消息触发。

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
