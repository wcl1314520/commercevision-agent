# 可靠性、安全与数据治理

| 属性 | 值 |
|---|---|
| 状态 | decision |
| 最后更新 | 2026-07-22 |
| 适用版本 | Engineering v1 |

## SLO

### 公开 Demo

| SLI | 目标 |
|---|---:|
| Web/API 月可用性 | >= 99.9% |
| 非生成 API P95 | <= 500 ms |
| Workflow 可靠受理 P95 | <= 2 秒 |
| 已受理任务持久化成功率 | >= 99.9% |

### 企业部署目标

| SLI | 目标 |
|---|---:|
| 控制面月可用性 | >= 99.95% |
| 已受理任务持久化成功率 | >= 99.99% |
| 平台原因最终失败率 | <= 0.5% |
| Webhook 最终送达率 | >= 99.9% |
| 任务数据到期清理率 | >= 99.9%/日 |

供应商生成成功率单独统计，不通过排除供应商故障掩盖用户体验。

## 可靠执行

Worker 采用：

```text
短事务认领
  -> 提交
  -> 事务外执行模型/工具
  -> 短事务完成
```

禁止在模型网络调用期间持有 MySQL 事务。

### 幂等

- Workflow 创建：用户 Idempotency Key。
- 消息消费：Inbox 唯一键。
- Tool 执行：稳定 Tool Execution Key。
- 图片生成：Generation Attempt Idempotency Key。
- Webhook：Event ID。
- 导出：Workflow + approval version + ruleset version。

### 恢复

- Step Lease。
- Provider task ID 对账。
- Outbox 补发。
- LangGraph Checkpoint。
- DLQ 和可审计重放。
- 数据状态不一致检测。

### 消息重试权威

MySQL 是业务消息重试时间和预算的唯一权威：

1. Worker 从 Inbox 认领消息，`delivery_attempts` 是唯一尝试预算。
2. 普通 Handler 或数据库失败时，同一个短事务将 Inbox 标记为 `FAILED`，并把同一个
   Outbox Event 重置为未发布，写入按尝试次数计算的未来 `available_at`。
3. 退避使用可配置的初始值和上限；Scheduler 在精确到微秒的 `available_at` 到达前不会
   重新发布。
4. 持久化成功后 Worker 返回 `retry-scheduled`，Celery ACK 当前投递，不创建 countdown、
   autoretry 或第二套业务重试计划。
5. 若 Inbox/Outbox 重试事务无法提交，Worker 抛出异常。Celery 使用 late ACK 且
   `task_acks_on_failure_or_timeout=false`，只在该持久化失败场景执行 Transport Redelivery。
6. 下一次 MySQL 调度的投递再次增加 Inbox 尝试次数；预算耗尽后 Inbox 进入 `DEAD` 并写入
   DLQ，不再调用 Handler。

Outbox 重试会清除旧的发布锁。若 Worker 在 Publisher Confirm 写回前已经完成重试调度，
Scheduler 识别同一行的未来 `available_at`，不会用旧锁把该事件重新标记为已发布。

### 持久操作恢复

- 资产校验、ProductBrief 分析、索引、删除、对账和 Collection Rebuild 共用一个
  Durable Operation 生命周期；不建立第二套 Outbox、Inbox、Retry、Lease 或 DLQ。
- Worker 在短事务中认领并开始操作，提交后才可通过公开执行边界调用外部系统，再以短事务
  完成或失败。执行边界检测到活动 Unit of Work 时立即拒绝调用。
- 过期 `CLAIMED` Lease 可安全进入 MySQL 定时重试；过期 `RUNNING` Lease 表示外部结果未知，
  必须进入 `RECONCILING`，不能盲目重发。
- Operation Recovery 使用有界 `SKIP LOCKED` 批次；Operation 持久化已发出和已消费的
  Recovery Generation。生成列和复合索引使 Ready 重试/对账查询排除尚未消费的代次，
  而不只排除未发布 Outbox 行，避免已发布但积压的旧行被反复入队或阻塞新行。已有代次的
  过期执行 Lease 仍可被扫描器推进到 `RECONCILING`，但不会发出第二个事件；原事件重投后
  完成对账并消费同一代次。
- Operation 业务重试使用指数退避和 jitter，尊重 Provider `retry_at`，受最大延迟、持久化
  `execution_deadline_at` 和操作尝试预算约束；Provider `retry_at` 超出最大延迟时被截断到
  边界。认领在同一事务中按 `deadline == now` 已耗尽处理并原子写入死信，因此停机后迟到
  的消息不能调用外部系统。显式重放设置新的经过时间窗口，把 `max_attempts` 设为累计
  `attempt_count + 1`，即使原配置预算尚有未使用尝试也只允许一次额外执行。Provider 请求
  使用由 Operation ID 派生且跨重试稳定的幂等键。
- 未知结果对账拥有独立的尝试数、`next_reconciliation_at`、退避和总经过时间预算。
  `PENDING`、`NOT_FOUND` 和查询异常都保持 `RECONCILING`；只有明确
  `CONFIRMED_FAILURE` 才能回到外部执行重试。预算耗尽后原子写入 Operation 死信；显式
  重放保留累计对账计数并将最大值设为当前计数加一，每次重放只允许一次额外状态查询。
- 外部系统返回的 Provider Request ID 独立保存于 Operation；最新执行/查询错误拥有单独的
  Error Provider ID。首次执行成功或对账确认成功也可建立该身份；Worker 重启或多次状态
  查询失败后，对账请求仍携带原始 Request ID。
- 死信重放复制原事件契约并创建新 Event ID；原死信不可变，执行人、原因、时间和每次失败链路
  保存在追加历史中。
- Worker 从 `commercevision.operation_executors` Python Entry Point 发现显式 Executor
  Factory。Celery `WorkController` 构建时先验证生产配置声明的全部 Kind，每个 Worker
  Process 再在接收任务前构建自己的 Runtime；缺失或初始化失败会终止启动而不是延迟到首条
  消息。Runtime 就绪后原子写入 `worker_readiness_path`，容器健康检查读取同一标记。

## 身份与权限

只定义两类产品权限：

| 权限 | 能力 |
|---|---|
| 管理员 | 用户、模型、Prompt、工具、品牌、数据集和系统配置 |
| 使用者 | 创建任务、审核、返工、导出和查看授权任务 |

审批是 Workflow 能力，不额外创造大量角色。公开 Demo 使用受限账户和全局配额。

## Secret

- 生产 Secret 保存于 KMS/Secret Manager。
- 数据库只保存 Secret Reference。
- Pod 使用工作负载身份。
- 支持双 Key 轮换。
- Trusted Principal 双 Key 轮换使用显式 Current/Previous Key ID；Secret 可通过
  `CV_SECRETS_DIR` 下的 `CV_TRUSTED_PRINCIPAL_CURRENT_HMAC_SECRET` 和
  `CV_TRUSTED_PRINCIPAL_PREVIOUS_HMAC_SECRET` 文件注入。
- 日志和 Trace 执行脱敏。
- `.env.example` 只包含占位符。
- CI 执行 Secret Scan。

## 文件上传

- MIME、魔数和真实解码。
- 文件大小和总像素。
- 防止图像解压炸弹。
- 隔离前缀上传。
- 病毒/恶意文件扫描。
- 扫描前不能进入 Agent Context。
- 文件名不作为 OSS Key。

未来支持 LoRA 时：

- 仅 `.safetensors`。
- 不执行 Pickle、脚本或仓库代码。
- 限制 Header 和张量元数据大小。
- 必须记录许可证和基础模型。

## SSRF 与出站

- 不实现任意 URL 图片代理。
- 远程素材只允许登记过的域名或内部资产 ID。
- DNS 解析后阻止私网、环回、链路本地和云元数据地址。
- 每次重定向重新校验。
- 设置连接、首字节、总时限和响应体上限。
- 生产出站通过固定 NAT/EIP 和 NetworkPolicy。

## Prompt Injection

- OCR、商品描述、MCP 返回和供应商响应均是不可信数据。
- Tool List、权限和系统政策在服务端固定。
- 输出通过 Pydantic 和业务白名单。
- 模型不能构造 SQL、路径、URL 或 Secret Reference。
- 人工审批版本不能由模型修改。
- 建立专门注入测试集。

## 内容安全

- 输入和输出内容审核。
- 真人素材记录授权和处理范围。
- 内容拒绝不能换供应商绕过。
- 模型 Judge 不替代平台内容安全。
- 公开 Demo 设置提示词和图片滥用检测。

## 数据治理

- 任务数据 72 小时。
- 公开数据集和品牌资产按许可证保存。
- 用户可以提前删除任务。
- 删除资产后先禁止检索，再删除 OSS 和 Milvus。
- 审计保存脱敏元数据 180 天。
- 供应商登记数据地域、保留期和训练政策。

## RPO/RTO

| 场景 | RPO | RTO |
|---|---:|---:|
| API/Worker Pod | 0 | <= 5 分钟 |
| 单节点 | 0 | <= 10 分钟 |
| 单可用区 | 以实例复制模式和演练确认，目标 0 | <= 30 分钟 |
| MySQL 逻辑误操作 | <= 5 分钟 | <= 60 分钟 |
| 地域故障 | 公开 Demo 不承诺；企业版后续评估 | 不承诺 |

任何 RPO 0 声明都必须经过真实实例故障演练。

## 上线阻断

- Secret 出现在代码或日志。
- 人工审批可绕过。
- 任意 URL/SSRF 未处理。
- 没有 Outbox/Inbox 和恢复器。
- Agent 存在无界循环。
- 没有固定评测集。
- 无权素材进入检索或生成。
- Checkpoint 使用不安全反序列化。
- 任务数据不能按策略删除。
