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
