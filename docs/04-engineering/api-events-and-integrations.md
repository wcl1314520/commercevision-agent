# API、事件与集成契约

| 属性 | 值 |
|---|---|
| 状态 | decision |
| 最后更新 | 2026-07-30 |
| 适用版本 | API v1 |

## API 原则

- REST + OpenAPI 3.1。
- `/api/v1` 显式版本。
- Pydantic Contract 是后端和前端 Client 的共同来源。
- 所有写接口支持 `Idempotency-Key`。
- 所有响应包含 `request_id` 和 `trace_id`。
- 列表使用游标分页。
- 文件使用 MinIO/OSS 预签名 PUT 直接上传，字节不经过 Control API 或 Web Proxy。

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
POST   /api/v1/upload-sessions
GET    /api/v1/upload-sessions/{uploadSessionId}
POST   /api/v1/upload-sessions/{uploadSessionId}:abort
POST   /api/v1/upload-sessions/{uploadSessionId}:finalize
GET    /api/v1/assets/{assetId}
```

创建接口返回仅绑定一个服务端 Key 的短期 PUT URL、允许的 Headers、精确最大字节数和
SHA-256 策略。文件名只保存为元数据；响应不返回凭证、Bucket、对象 Key 或无限制 URL。
Finalize 使用 `Idempotency-Key`、请求 Hash 和 `expected_version`，返回 `202` 及唯一的
Quarantined Asset、不可变 Asset Version 和 `ASSET_VALIDATION` Durable Operation。
Finalize 先在 MySQL 领取带 Token 的短 Lease，再在事务外完成 HEAD、受限流式 SHA-256、
完整图片解码。证明成功后，Asset、Asset Version、隔离区精确 Provider Version 对象事实、
Durable Operation、Outbox 和 Session 结果在一个事务中提交。Finalize 不复制、提升或删除
对象；已登记的隔离对象是后续校验的唯一输入，Ticket 05 在恶意文件、内容安全与政策检查
通过后才执行到 Task/Foundation 的条件提升。

由于已签发的 Presigned PUT 不能撤销，只有 `ABORTED` 或 `EXPIRED` Session 的隔离对象进入
耐久清理。清理事件在 URL 到期并经过配置的时钟偏差缓冲后才可发布。首次删除后同一个
Cleanup Operation 进入 `RECONCILING`，在持久化截止时间前周期性重复精确版本清理；首次
HEAD 后完成的迟到 PUT 仍由该 Operation 收敛，不创建第二套 Lease、重试或 Outbox 控制面。
Cleanup Operation 的累计执行预算从事件变为可消费时开始计算，等待 URL 失效不会提前耗尽
业务重试预算。成功 Finalize 的隔离对象不得被 Upload Session 清理器删除。
Session 到期阻止新的 `OPEN` finalize claim；到期前已领取的 finalize 由其 Lease 截止时间
约束。进程中断后仅能在 Lease 到期时 Session 本身仍有效的情况下重新认领；Session 与
Lease 都已到期时，API 与 Scheduler 使用同一个 `expire_abandoned` 状态转换，谁先取得行锁
都只能将其转为 `EXPIRED` 并进入清理。

Finalize 分为三个事务边界：MySQL 认领 Lease；事务外 HEAD、受限流式 SHA-256 和图片解码
证明；最后一次 MySQL 事务原子提交 Asset、Asset Version、对象事实、Operation、Upload
Session 和 Outbox。存储不可用或条件读取冲突会释放 Lease 并保留同一幂等请求供重试；对象
长度、Checksum、MIME、格式或解码证明不匹配会稳定终止该 Session，并在 Presigned URL
失效后进入同一个耐久清理与复核流程。

### Brand Profile

```text
POST   /api/v1/brand-profiles
GET    /api/v1/brand-profiles
GET    /api/v1/brand-profiles/{profileId}
PUT    /api/v1/brand-profiles/{profileId}/draft
POST   /api/v1/brand-profiles/{profileId}:validate
POST   /api/v1/brand-profiles/{profileId}:publish
GET    /api/v1/brand-profiles/{profileId}/versions
GET    /api/v1/brand-profiles/{profileId}/versions/{versionNumber}
```

创建、更新草稿、校验和发布要求 Workspace 管理员；列表、identity 与不可变历史读取只要求
Workspace 成员。`X-Workspace-Id` 仍只选择租户，`X-Actor-Id` 必须与签名
`X-Trusted-Principal` 中的 actor 完全一致。`profileId` 只接受 canonical lowercase UUID；
跨 Workspace ID 与不存在 ID 对已授权成员统一返回 `404 NOT_FOUND`。

创建、更新草稿和发布必须携带 `Idempotency-Key`。幂等 Scope 包含操作类型、完整
Workspace 哈希和 Profile identity；相同 key + 相同请求返回原结果或对账后的当前 Profile，
相同 key + 不同请求返回 `409 IDEMPOTENCY_CONFLICT`。创建响应为 `201`，发布响应为 `201`，
草稿更新为同步 `200`。`:validate` 是无持久副作用的当前授权评估，因此不领取幂等记录。

更新、校验和发布请求都携带 `expected_version`，以 `brand_profiles.version` 做乐观并发。
过期版本返回 `409 VERSION_CONFLICT`；调用方必须重新读取 Profile，不能自动把旧草稿套用到
新 head。发布在同一 MySQL 事务中重新锁定并校验每个选中 Asset Version 的 Foundation
retention、当前 Asset Version、用途、Provider、派生许可和有效期。任一 member 不合法时
返回 `422 BRAND_PROFILE_PUBLICATION_REJECTED`，`details.issues[]` 包含
`asset_version_id`、角色、稳定 reason code 与安全消息；不会创建部分 publication。

发布成功追加一个不可变 Brand Profile Version，记录规范化 `content_sha256`、完整草稿、
精确 Asset Version、发布时 Rights Record ID/version、publisher 与数据库时间，并原子推进
identity head。历史列表按 version number 稳定游标分页，Profile 列表按
`created_at + id` 稳定游标分页；两者默认 20、最大 100。

历史响应将发布时证据与当前权限明确分开：每个 member 同时返回
`published_rights_record_id/version`、`currently_usable`、`current_reason_code`、
可选 current Rights Record identity 和 `decided_at`。`decided_at` 与当前 Asset/Rights
快照来自同一个数据库一致性边界。该字段只是读取时快照；检索或 Provider 调用必须在实际使用
前再次查询 MySQL 当前可用性。`NEEDS_REPUBLISH` 和历史 `currently_usable=true` 都不能替代
最终授权检查。

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
- Web BFF 是公开 Demo 的受信入口 Adapter：它拒绝
  `CV_WEB_ALLOWED_WORKSPACE_IDS` 之外的 Workspace，覆盖浏览器传入的 Actor/Principal，
  并用服务端 Current Key 为每个请求签发短期成员 Principal。可选的
  `CV_WEB_ADMIN_WORKSPACE_IDS` 必须是 Allowed 集合的严格子集；未配置时不签发任何管理员
  权限。企业部署必须由真实会话或上游身份网关计算 Workspace 授权，不能把浏览器 Header
  直接转换成成员或管理员关系。
- Web 的 `/api/web-capabilities` 只从上述服务端 Allowed/Admin 集合返回当前 Workspace 的
  展示能力，用于隐藏未授权的管理员控件；它不签发新权限，也不替代 API 对签名 Principal
  的最终授权。
- Upload Session 与 Asset 路由和 Operation 路由使用同一 Trusted Principal 边界。业务
  `actor_id` 只能来自签名 Claims；浏览器提供的 `X-Actor-Id` 和
  `X-Trusted-Principal` 会被 BFF 覆盖。BFF 对控制面请求体限制为 1 MiB、响应体限制为
  2 MiB，并在 `CV_API_PROXY_TIMEOUT_MS` 截止时间内完成上游响应读取；商品图片只通过短期
  Presigned PUT 直传对象存储，不穿过这些 JSON 限制。
- Principal Token 格式为 `<key-id>.<base64url-claims>.<hex-signature>`，签名输入包含
  `key-id` 和 Claims。API 同时验证一个 Current Key 和一个 Previous Key，未知 Key ID
  即使签名格式正确也关闭式拒绝；滚动轮换完成后必须删除 Previous Key。`actor_id` 在签名
  身份解析阶段按 Unicode 字符计数并强制为 1–128 个字符，空值或超长值在写审计记录前返回
  `AUTHENTICATION_REQUIRED`。`workspace_ids` 和 `admin_workspace_ids` 中每个值也必须为
  匹配 `^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$` 的 ASCII Token；Header 和 Claims 中的
  空白、控制字符、非 ASCII 或超长值分别在路由校验或身份解析阶段稳定拒绝。系统不修剪、
  折叠大小写或规范化身份，授权、持久化和幂等范围使用完全相同的合法 Token。
- 这是当前 Phase 2 身份 Adapter seam，不是完整认证系统；生产入口负责先完成真实身份认证。
- `POST /api/v1/assets/{assetId}/usability:check` 在一个 MySQL 事务内共享锁定 Asset/current
  Rights 快照并读取 `UTC_TIMESTAMP(6)`。请求中的 `decision_time` 是保守评估上界，实际
  时间取 `max(decision_time, database_now)`；调用方可请求面向未来的关闭式判断，但不能
  通过回填旧时间延长当前授权。
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
- 客户端以 `Accept: text/event-stream` 协商 SSE；未声明时保留既有 JSON 读取兼容面。
- 每条事件的 `id` 是 HMAC 防篡改的不透明游标，绑定 Workspace、Workflow、保留截止时间与
  `(occurred_at, event_id)` 稳定排序边界；它不是数据库 Event ID。
- 客户端通过 `Last-Event-ID` 严格恢复到最后已交付事件之后。未知、篡改、跨 Workspace/Workflow、
  不存在、超长、过期或保留期变化的游标统一失败关闭。
- 事件仅从 MySQL Outbox 持久事实按 tenant-first keyset 索引读取；任何未来 Fan-out 层都不是恢复事实源。
- 每个 catch-up 页使用独立短事务，连接在网络传输前释放；页面、轮询、heartbeat、retry 和单次流会话
  均有配置上限，到期后客户端以最后的 `id` 重连。
- 轮询 `GET workflow` 作为降级。

事件在应用边界再次经过注册 Event Contract 投影，只包含状态和引用，不发送完整 Plan、Prompt、原图、
任意额外 Outbox 字段或供应商响应。Heartbeat 使用 SSE comment，不改变客户端恢复游标。

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

Ticket 04 发布 `asset.validation.requested` v1 到 Asset Queue。Payload 包含
`asset_id`、`asset_version_id`、`object_fact_id` 和 `operation_id`；Worker 先通过
Inbox 去重并验证 Envelope 的 Workspace/Aggregate，再由 Durable Operation 作为唯一业务
重试权威执行。重复投递不产生第二次校验执行。

同一个 Finalize 提交还发布 `asset.upload.finalized` v1 Observation，Payload 固定引用
Upload Session、Asset、Asset Version、对象事实和 Validation Operation。该事件以 Asset
作为 Aggregate，供审计和进度观察者消费；它不替代 `asset.validation.requested` Command，
也不创建第二个业务重试权威。

Ticket 04 创建并传递该 Durable Operation，后续 Ticket 已注册 `ASSET_VALIDATION`、
`ASSET_DELETION` 和 `PRODUCT_BRIEF_ANALYSIS` Executor。当前 Compose Worker 显式订阅
Workflow、Asset 和 Maintenance Queue，并在启动时把三个 Operation kind 作为必需能力；
任一 Executor 缺失即失败关闭。Index Queue 由后续索引 Ticket 接管。所有消费者仍通过
Inbox 去重，并以 Durable Operation 而不是 Celery retry 作为唯一业务重试权威。

Ticket 08 发布 `brand-profile.published` v1 到 Asset Queue。它是 strict typed
Observation，Payload 只包含 Workspace、Profile ID、不可变 Profile Version ID/number、
`content_sha256`、member count 和 publisher；对象位置、规则正文和 Rights 明细不进入消息。
Envelope 的 Aggregate 是 BrandProfile identity，Worker 校验 Workspace、Aggregate type/ID
后只记录观察事实，不创建第二套发布权威。Envelope `aggregate_version` 是 mutable Profile
head 的乐观锁版本；Payload `profile_version_number` 是 append-only publication 序号，二者
不得混为同一个计数器。

`asset.rights.changed` 与 `asset.rights.expired` v1 同时驱动 Brand Profile 失效收敛。
Worker 先验证 Workspace 与 `aggregate_type=Asset`，再用 MySQL live authority 重验引用该
Asset 的 current Profile heads。事件时间只作因果/审计信息；Profile locks、Asset/current
Rights lock 和锁后 `UTC_TIMESTAMP(6)` 决定是否写入 `NEEDS_REPUBLISH`。重复、乱序、已被
新 publication 取代或 live authority 仍满足的事件都是 Inbox 幂等 no-op，不能按旧 Payload
恢复授权。

`asset.delete.completed` v1 是 Maintenance Queue 上的 forward-compatible typed
Observation。Payload 固定要求 Workspace、Asset ID、精确 Asset Version ID、
`retention_class=FOUNDATION` 和正整数 `deletion_generation`；v1 消费者忽略未来新增字段。
Worker 验证 Workspace、Asset Aggregate identity、retention class，并要求 Envelope
`aggregate_version == deletion_generation` 后，复用同一个 live-authority 失效接口。重复
delivery 不重复推进 Profile version，旧删除代次也不能删除或失效后来创建的 Asset Version；
Ticket 13 的删除执行器负责产生实际完成事件，Ticket 08 只定义安全消费与 Brand Profile
收敛。

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
