# Phase 2 Observability Incident Runbook

| 属性 | 值 |
|---|---|
| 状态 | verified |
| 最后更新 | 2026-08-04 |
| 适用版本 | Phase 2 / Ticket 15 |
| 所有者 | On-call / Control API / Asset、Index、Maintenance Worker |

## 使用原则

MySQL 是 Operation、Rights、Asset、Collection pointer 与删除进度的权威；Milvus、对象存储和
Provider 是受 fencing 约束的外部系统。先止损，再从 MySQL 和 Outbox/Inbox 事实恢复。不得手工
篡改终态、跳过 Rights、盲目重投未知 Provider 结果、清空活动 Collection，或扩大删除条件。

每个事件都记录 `trace_id`、`operation_id`、`workspace_id`、`target_id/version`、`event_id` 与
哈希后的 `provider_request_id`。调查材料不得复制 Secret、签名 URL、完整原图、原始 Prompt、
完整 OCR 或 Provider 原始 payload。

## Stuck quarantine

- **Signal**：`commercevision.phase2.quarantine.age` 持续增长，且 validation outcome 无新增。
- **Containment**：停止该 Workspace 新 finalize；保持对象在 Quarantine，不得人工 promotion。
- **Recovery**：检查 Asset Worker readiness、`commercevision.asset` queue、Operation lease/retry 与 Inbox；
  修复依赖后让原 Operation 恢复，不新建同目标 Operation。
- **Recovery proof**：原 Asset Version 达到 `PENDING_RIGHTS` 或安全终态；Quarantine 精确版本已删除，
  目标副本的 Version ID/ETag/长度/SHA-256 与 MySQL 一致。
- **Escalate**：P95 quarantine age 超过保留预算，或出现 source/target identity mismatch。

## ClamAV outage

- **Signal**：Worker readiness 的 `malware_scanner` 非 `ok`，MALWARE retryable failure 增长。
- **Containment**：将受影响 Asset Worker 摘流；禁止将 unavailable 降级为 clean。
- **Recovery**：核对 clamd `PING`、`VERSIONCOMMANDS`、病毒库更新时间、INSTREAM 与文件上限；恢复后由
  Operation Recovery 按原 input hash 重试。
- **Recovery proof**：readiness 恢复，真实 EICAR 测试被拦截，clean fixture 通过且无旁路 promotion。
- **Escalate**：病毒库无法更新、扫描能力漂移，或重试预计超过 Quarantine 保留期。

## Content safety outage

- **Signal**：CONTENT_SAFETY retryable failure、provider timeout/5xx 增长。
- **Containment**：保持 Asset 在 Quarantine；不得切到 deterministic adapter 作为生产降级。
- **Recovery**：验证启用的数据传输 policy 与 Workspace/retention/provider/region/host allowlist；再检查
  approved endpoint 与 deadline。仅在授权仍有效时重试。
- **Recovery proof**：新调用通过 transfer policy，结果仅保存 allowlisted evidence，旧签名 URL 已失效。
- **Escalate**：policy 漂移、地域错误、疑似数据外传，或 provider unknown outcome 无法 reconciliation。

## Provider throttling

- **Signal**：`commercevision.phase2.provider.rate_limits`、retry 与调用时延升高。
- **Containment**：降低对应 Worker 并发/流量；保留幂等键和 operation epoch，禁止无界即时重试。
- **Recovery**：遵循 provider retry-after 与有上限退避；通过 Operation 状态确认是 retryable 还是 unknown。
- **Recovery proof**：错误率回落、积压下降、相同 Operation 未产生重复业务结果。
- **Escalate**：预算或截止时间将耗尽，限流持续超过 15 分钟，或未知结果比例上升。

## Index lag

- **Signal**：`commercevision.phase2.index.lag` P95 超阈值，Index queue oldest age 同步升高。
- **Containment**：检索继续以 MySQL eligible set 与最终 Rights 为准；不得放宽授权过滤。
- **Recovery**：检查 Index Worker readiness、Milvus/对象存储/embedding provider、Operation retry 与 DLQ；
  水平扩容前确认 provider 配额。
- **Recovery proof**：lag 回落，当前 generation 为 `INDEXED`，固定查询召回且 unauthorized recall 为零。
- **Escalate**：活动 Collection 不可写、backlog 超过 SLO，或不同 generation 状态不一致。

## Stale vectors

- **Signal**：`commercevision.phase2.index.stale_vectors` 非零或 rebuild validation 出现 unexpected PK。
- **Containment**：MySQL 立即隐藏 `DELETE_PENDING`/无 Rights 记录；不要宽泛删除 Asset 或 Collection。
- **Recovery**：按 `<embedding_record_id>:g<N>` 精确验证并删除旧 generation；重放 typed delete event。
- **Recovery proof**：旧 PK 不存在，新 generation 不受迟到删除影响，最终 Rights 过滤仍为零越权。
- **Escalate**：无法证明 generation identity，或 stale vector 出现在多个 Collection。

## Milvus loss

- **Signal**：Milvus search/upsert/delete 错误，dense channel degradation 增长。
- **Containment**：保持 API/MCP 控制面可用；检索显式返回 degraded，仅保留可用 lexical channel。
- **Recovery**：从 MySQL 权威事实重建，不从 Milvus 反推 Rights；恢复 Collection 后执行 reconciliation 与
  固定查询验证。
- **Recovery proof**：dense 召回恢复、candidate/final counts 合理、unauthorized recall 为零。
- **Escalate**：活动 Collection 数据丢失、pointer identity 不明，或 lexical fallback 也不可用。

## Deletion backlog

- **Signal**：`commercevision.phase2.deletion.backlog` 持续非零或保留期限接近。
- **Containment**：MySQL 保持公开隐藏；不得因物理清理失败恢复可见性。
- **Recovery**：检查 Maintenance queue、Operation retry、对象版本枚举、Milvus generation delete 与 search
  document 清理；使用 generation-fenced coordinator 收敛。
- **Recovery proof**：MySQL deletion complete，所有受控对象版本、精确向量与 search document 均为零。
- **Escalate**：越过法规/策略截止时间、出现未授权可见性，或清理对象身份无法证明。

## DLQ replay

- **Signal**：`commercevision.phase2.operation.dlq` 任意新增或 Inbox 标记 permanent failure。
- **Containment**：按 error code 聚类，暂停同类自动 replay；禁止直接发布复制后的原 payload。
- **Recovery**：修复根因后使用受审计 replay 流程，绑定 source dead-letter ID、replay ID/attempt 与原 workspace；
  对 terminal Operation 走 recovery generation fencing。
- **Recovery proof**：原 DLQ 与 replay lineage 可追溯，Inbox 幂等，Operation/业务结果仅收敛一次。
- **Escalate**：schema/identity mismatch、重复副作用、replay 再次进入 DLQ。

## Rebuild failure

- **Signal**：rebuild progress 停滞、remaining 不下降、validation rejected 或 retirement 超时。
- **Containment**：旧活动 pointer 保持不变；禁止激活失败候选或手工删除活动 Collection。
- **Recovery**：按 rebuild ID 检查 cursor/watermark、Outbox replay、Rights rescan 与候选 validation；修复后从
  持久化状态继续。不可修复的候选创建新 rebuild，保留失败审计。
- **Recovery proof**：PK/row count/visibility/recall/unauthorized 检查全通过，pointer 原子切换且旧 Collection
  延迟退役。
- **Escalate**：pointer/version fencing 冲突、候选污染、活动 Collection 被误删。

## Readiness contract

- API 只要求 MySQL 与对象存储；它不声明未使用的 RabbitMQ/Redis，也不因 Milvus/可选 reranker 失败摘流。
- Worker 在 Consumer 前检查队列所需 MySQL、RabbitMQ、Executor 与进程拥有的 provider/storage/Milvus；
  master 和全部 prefork child 标记必须有效。
- Scheduler 只有所有配置 scanner 至少成功一次且当前无 error/timeout 才 ready。
- MCP 要求控制面 MySQL 与对象存储；retrieval channel 失败通过显式 degradation 表达。
- OpenTelemetry Collector 不是业务 readiness 的硬依赖；Exporter 失败不得使业务进程退出。

## Data safety

Telemetry 只允许稳定 ID、枚举型低基数维度、计数、时延和 normalized error code/category/class。
不记录异常 message；不把 ID 放入 metrics label；不记录 Secret、Authorization header、cookie、对象 key、
签名 URL、图片字节、Prompt、OCR 全文或 Provider payload。Provider request ID 始终 SHA-256；不满足
安全 ID 语法的 operation/target/event/policy ID 同样哈希；trace ID 无条件哈希。事故导出遵循同一规则。
