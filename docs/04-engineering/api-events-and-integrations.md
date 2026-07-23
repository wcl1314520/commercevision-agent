# API、事件与集成契约

| 属性 | 值 |
|---|---|
| 状态 | decision |
| 最后更新 | 2026-07-22 |
| 适用版本 | API v1 |

## API 原则

- REST + OpenAPI 3.1。
- `/api/v1` 显式版本。
- Pydantic Contract 是后端和前端 Client 的共同来源。
- 所有写接口支持 `Idempotency-Key`。
- 所有响应包含 `request_id` 和 `trace_id`。
- 列表使用游标分页。
- 大文件使用 OSS 预签名上传。

## 核心 API

### Workflow

```text
POST   /api/v1/workflows
POST   /api/v1/workflows:batchCreate
GET    /api/v1/workflows/{workflowId}
GET    /api/v1/workflows
POST   /api/v1/workflows/{workflowId}:cancel
GET    /api/v1/workflows/{workflowId}/events
GET    /api/v1/workflows/{workflowId}/trace
POST   /api/v1/workflows/{workflowId}:replay
```

### Human-in-the-loop

```text
POST /api/v1/workflows/{id}/product-brief:confirm
POST /api/v1/workflows/{id}/creative-plan:approve
POST /api/v1/workflows/{id}/creative-plan:reject
POST /api/v1/workflows/{id}/results:approve
POST /api/v1/workflows/{id}/results:regenerate
```

请求必须包含 `expected_workflow_version` 和目标对象版本。

### Asset

```text
POST   /api/v1/assets/uploads
POST   /api/v1/assets/uploads/{uploadId}:complete
GET    /api/v1/assets/{assetId}
DELETE /api/v1/assets/{assetId}
GET    /api/v1/reference-assets:search
```

### Configuration

```text
/api/v1/brands
/api/v1/prompts
/api/v1/providers
/api/v1/models
/api/v1/tools
/api/v1/evaluation-suites
/api/v1/datasets
```

配置发布和删除需要管理员权限与审计。

### Operations

```text
GET  /api/v1/operations
GET  /api/v1/operations/{operationId}
GET  /api/v1/operator/dead-letters
GET  /api/v1/operator/dead-letters/{deadLetterId}
POST /api/v1/operator/dead-letters/{deadLetterId}:replay
GET  /api/v1/operator/legacy-dead-letters
GET  /api/v1/operator/legacy-dead-letters/{deadLetterId}
```

- `X-Workspace-Id` 只选择工作区，不承担认证。入口网关必须移除调用方同名 Header，并生成
  HMAC-SHA256 签名的短期 `X-Trusted-Principal`，包含 Actor、工作区成员关系、工作区管理员
  授权和系统管理员声明。签名 Secret 缺失、签名无效、过期或授权缺失时 API 关闭式拒绝。
- Principal Token 格式为 `<key-id>.<base64url-claims>.<hex-signature>`，签名输入包含
  `key-id` 和 Claims。API 同时验证一个 Current Key 和一个 Previous Key，未知 Key ID
  即使签名格式正确也关闭式拒绝；滚动轮换完成后必须删除 Previous Key。`actor_id` 在签名
  身份解析阶段按 Unicode 字符计数并强制为 1–128 个字符，空值或超长值在写审计记录前返回
  `AUTHENTICATION_REQUIRED`。`workspace_ids` 和 `admin_workspace_ids` 中每个值也必须为
  匹配 `^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$` 的 ASCII Token；Header 和 Claims 中的
  空白、控制字符、非 ASCII 或超长值分别在路由校验或身份解析阶段稳定拒绝。系统不修剪、
  折叠大小写或规范化身份，授权、持久化和幂等范围使用完全相同的合法 Token。
- 这是当前 Phase 2 身份 Adapter seam，不是完整认证系统；生产入口负责先完成真实身份认证。
- Operation 和死信查询在授权后按工作区限定；已授权工作区中的跨工作区 ID 与不存在 ID 均
  返回 `NOT_FOUND`，未加入请求工作区则返回 `WORKSPACE_ACCESS_DENIED`。
- 重放要求 `Idempotency-Key`，返回 `202`，相同请求返回同一不可变重放记录。持久化 Scope
  使用版本化命名空间、完整 Workspace SHA-256 和可读 Dead Letter ID，固定保持在
  `idempotency_keys.scope` 的 160 字符上限内。Dead Letter 路径只接受带连字符的 ASCII
  UUID；大写十六进制输入在 HTTP 和 Application 边界规范化为小写后才允许进入数据库查询，
  查询还使用二进制比较。重音、NFC/NFD、全角、零宽、空白或额外字符别名与跨工作区查询均
  返回相同的 `NOT_FOUND` 语义。同一 UUID 的大小写变体因此命中同一重放和幂等 Scope。
- 死信详情通过 `child_limit`/`child_cursor` 返回直接子死信和
  `child_dead_letters_next_cursor`。调用方逐层读取即可完整遍历任意深度和宽度的重放失败
  链，服务端不会用隐式深度或总行数上限截断。同一详情通过独立的
  `replay_limit`/`replay_cursor` 和 `replays_next_cursor` 分页读取不可变重放尝试。
  无法回填工作区的历史死信只允许系统管理员通过 Legacy API 读取；Legacy API 不提供重放。
- 列表使用最大 100 条的稳定游标分页。

### Export

```text
POST /api/v1/workflows/{id}/exports
GET  /api/v1/exports/{exportId}
GET  /api/v1/exports/{exportId}/download
```

## SSE

SSE 用于 UI 任务进度：

- 需要认证。
- 事件带单调递增 `event_cursor`。
- 客户端通过 `Last-Event-ID` 恢复。
- Redis 可以用于实时 Fan-out。
- Redis 丢失时从 MySQL 事件表补齐。
- 轮询 `GET workflow` 作为降级。

事件只包含状态和引用，不发送完整 Prompt、原图或供应商响应。

## 领域事件

```text
workflow.created
workflow.product_brief_ready
workflow.awaiting_product_confirmation
workflow.references_retrieved
workflow.creative_plan_ready
workflow.awaiting_plan_approval
workflow.generation_started
workflow.candidates_ready
workflow.evaluation_completed
workflow.repair_started
workflow.awaiting_result_approval
workflow.completed
workflow.failed
workflow.expiring
export.ready
```

消息 Envelope：

```json
{
  "eventId": "uuid",
  "eventType": "workflow.created",
  "schemaVersion": 1,
  "aggregateId": "workflow-id",
  "aggregateVersion": 1,
  "occurredAt": "UTC timestamp",
  "traceId": "trace-id",
  "payloadRef": "object-reference"
}
```

### Durable Worker 事件

Durable Worker 使用 `packages/contracts` 中的版本化 Pydantic 契约。每个契约同时声明
`event_type`、`schema_version`、逻辑队列和 Payload Model；Scheduler 和 Worker 都在边界执行
Payload 校验。兼容性新增字段会被忽略，缺失必填字段或字段类型错误属于永久失败。

四个逻辑队列分别为：

| 逻辑队列 | 默认 Queue | 用途 |
|---|---|---|
| workflow | `commercevision.workflow` | Workflow 命令、进度通知和审计事件 |
| asset | `commercevision.asset` | 资产校验、权利、ProductBrief 和 Brand Profile |
| index | `commercevision.index` | Embedding、索引删除和 Collection Rebuild |
| maintenance | `commercevision.maintenance` | 删除、对账，以及无法按契约路由的消息 |

Phase 1 已发布的 v1 契约全部路由至 Workflow Queue：

- `workflow.run.requested`、`workflow.resume.requested` 执行 Graph。
- `workflow.node.started`、`workflow.node.completed`、
  `workflow.human_input.required`、`workflow.human_input.received`、
  `workflow.failed`、`workflow.cancelled` 是显式注册的通知/审计事件。Worker 通过 Inbox
  记录已观察状态，不重复执行 Graph，也不会将它们误判为未知事件。

未知事件类型、已知事件的不支持版本、未绑定处理器和格式错误的 Payload 都先发布至
Maintenance Queue，再由 Worker 记录为永久失败并写入 DLQ；不会静默成功。

Operation Recovery 和 Dead-letter Replay 使用版本化 v1 Payload。Recovery Payload 包含
Operation、Workspace、Kind、恢复原因和单调递增的 `recovery_generation`；该代次从事件
创建持续占用至 Worker 成功消费，发布完成本身不释放。Replay Payload 包含源死信、重放
记录、Workspace 和重放序号。新建 Outbox 事件携带内部 Workspace 归属元数据，使永久失败
可被工作区隔离的 Operator API 查询；该元数据不改变版本化事件 Envelope。

## Webhook

- 事件由 Outbox 产生。
- 独立 Worker 投递。
- HMAC 签名和时间戳。
- 唯一 event ID。
- 2xx 才视为成功。
- 指数退避和 jitter。
- 最长尝试 24 小时。
- 支持查询和人工重放。
- Webhook Secret 只在创建时显示一次。

## ERP/PIM 集成

正式企业部署支持 REST/Webhook 或 MCP Adapter：

- 外部系统是商品主数据权威来源。
- Agent 保存带版本和过期时间的任务快照。
- Agent 不直接连接 ERP 数据库。
- Agent 回写 Workflow、审批、图片和导出状态。

公开 Demo 使用独立 Product Catalog MCP Server，不连接真实企业 ERP。

## 错误模型

统一错误字段：

- `code`。
- `message`。
- `category`。
- `retryable`。
- `details`。
- `request_id`。
- `trace_id`。

外部供应商原始错误必须脱敏后映射，不能原样暴露 Secret、URL 或内部栈。

## 兼容策略

- API 删除字段需要主版本。
- 增加可选字段属于兼容变更。
- Event Consumer 忽略未知字段。
- Event Schema 在 Registry 中版本化。
- 至少保留一个旧客户端发布周期。
