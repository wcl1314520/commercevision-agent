# ADR-007：ProductBrief continuation 由消费时权威事实授权

| 属性 | 值 |
|---|---|
| 状态 | accepted |
| 日期 | 2026-07-29 |

## 背景

ProductBrief 确认与 Workflow continuation 通过 MySQL Outbox 和 RabbitMQ 解耦。事件可能在队列
中等待较长时间；等待期间 Workflow 可能跨过 72 小时保留期限，操作人也可能已经启动新的
ProductBrief 分析或人工修订。若 Worker 把“事件曾经发布”当作执行授权，迟到事件会在过期后
继续读取任务数据，或用旧版本恢复已被替代的 Agent 路径。若把这些业务失配作为普通异常重试，
合法重分析又会制造无效重试和 DLQ 告警。

## 决策

- `workflow.run.requested` 和 `workflow.resume.requested` 只承载 continuation 意图，不承载
  永久授权。
- continuation 必须携带精确 ProductBrief Version 身份。Worker 在消费时以及每个 Agent
  节点 claim 时，以 MySQL 当前时间和锁定事实重验：
  - Workflow 类型严格为 `COMMERCE_IMAGE_GENERATION`。
  - Workflow 冻结输入中的 Product ID 与 ProductBrief Product 完全相同。
  - Workflow 和 ProductBrief 的 Retention Status 仍允许执行，且 deadline 尚未到达。
  - 事件中的 ProductBrief Version 仍是当前精确 confirmed authority。
- 过期事件和已被后续分析、修订或确认取代的事件，以可观测的 `expired` 或 `superseded`
  stale no-op 完成 Inbox 消费；不得启动 Graph、创建 Step/Checkpoint、调用 Provider、发布
  新 Outbox、消耗业务重试或进入 DLQ。
- 临时 MySQL、RabbitMQ 或对象存储故障仍按 Durable Retry 分类。未知事件、Contract 无效和
  不支持版本仍失败关闭并进入 DLQ；它们不属于 stale no-op。
- 显式重新分析允许 Workflow 从 `RETRIEVING` 回到 `UNDERSTANDING`；基于已确认版本的人工
  修订允许进入 `AWAITING_PRODUCT_CONFIRMATION`。旧 continuation 不得反向覆盖这些新事实。

## 后果

- 队列和 Checkpoint 都不能单独授权业务执行，MySQL 保持唯一权威。
- Worker 每次 continuation 和节点 claim 增加一次窄的权威校验，但关闭了保留期越界和
  乱序副作用。
- 运维指标必须区分正常的 stale no-op、可重试基础设施故障和真正的永久 Contract 失败，
  避免把合法并发流程误报成死信事故。

## 验证

- 人工确认和 policy confirmation 都覆盖“deadline 前发布、deadline 后消费”，并证明没有
  Step、Checkpoint、Provider 或后续 Outbox 副作用。
- 两种 confirmation 都覆盖“V1 continuation 未消费、V2 重分析/确认先完成”的乱序场景，
  并证明 V1 收敛为 superseded no-op。
- 非 Commerce Workflow、冻结 Product 不匹配和精确 confirmed version 不匹配均失败关闭。
- 当前、未过期且精确匹配的 continuation 仍能从 ProductBrief gate 恢复到检索和规划。
