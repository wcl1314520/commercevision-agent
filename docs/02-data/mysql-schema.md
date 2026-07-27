# MySQL 逻辑模型

| 属性 | 值 |
|---|---|
| 状态 | decision |
| 最后更新 | 2026-07-24 |
| 适用版本 | Schema v1 |

## 设计原则

- MySQL 8.4 LTS。
- InnoDB。
- UTC 时间统一保存为 MySQL `DATETIME(6)`，应用层拒绝 naive datetime，读取时恢复 UTC 时区。
- `utf8mb4`；所有 `workspace_id` 列使用 `utf8mb4_0900_bin`，对合法 ASCII Token
  执行精确比较并提供数据库纵深防御。
- 业务主键使用 UUIDv7/ULID 的二进制表示或有序字符表示。
- 金额、成本和 Token 不使用浮点数。
- 所有可变业务实体带 `version`。
- JSON 只用于品类扩展，不替代关系约束。

## 身份与工作区

Workspace ID 是匹配 `^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$` 的不透明 ASCII Token。
入口、签名 Claims、领域构造和持久化 Bind 均原样验证，禁止修剪、大小写折叠或 Unicode
规范化；非法值不会被转换为另一个租户。所有直接 `workspace_id` 列，以及嵌入 Workspace ID
的 `idempotency_keys.scope`，都使用 `utf8mb4_0900_bin` 作为纵深防御。升级在任何 DDL 前
拒绝已持久化的非法 Workspace；降级回旧排序规则前扫描全部 Workspace 列和幂等
`(scope, key_hash)`，若精确身份会在旧规则下折叠则拒绝破坏性 DDL。

### `users`

- `id`
- `email`
- `display_name`
- `role`: `ADMIN` / `USER`
- `status`
- `created_at`
- `updated_at`

### `workspaces`

公开版本仍保留工作区边界，以支持演示、个人账户和未来企业部署：

- `id`
- `name`
- `mode`: `DEMO` / `PRIVATE`
- `settings_json`
- `created_at`

### `workspace_members`

- `workspace_id`
- `user_id`
- `role`
- 唯一键 `(workspace_id, user_id)`

## 商品与资产

### `products`

- `id`
- `workspace_id`
- `source_namespace`
- `external_id`
- `category_code`
- `title`
- `brand_id`
- `attributes_json`
- `source_version`
- `expires_at`
- external identity is unique within the shared catalog namespace
  `(workspace_id, source_namespace, external_id)`

### `skus`

- `id`
- `workspace_id`
- `product_id`
- `source_namespace`
- `external_id`
- `attributes_json`
- `expires_at`
- `(workspace_id, product_id)` references the owning Product with a composite foreign key

### `catalog_external_identities`

Product and SKU external identities reserve one shared registry row keyed by
`(workspace_id, source_namespace, external_id)`. Reservations are created and released in the same
transaction as the owning catalog mutation. Expired catalog rows remain readable and listable for
audit and renewal; this metadata does not enforce asset-rights usability.

### `assets`

- `id`
- `workspace_id`
- `retention_class`: `TASK` / `FOUNDATION`
- `asset_kind`: Ticket 04 仅允许 `IMAGE`
- `workflow_id`: TASK 必填，FOUNDATION 必须为空
- `product_id`
- `sku_id`
- `status`: finalize 后初始为 `QUARANTINED`
- `current_version_id`
- `retention_deadline`
- `version`
- `created_at`
- `updated_at`

Asset 不保存对象地址或可变文件字段。`workflow_id`、`product_id`、`sku_id`、
`current_version_id` 都使用 Workspace 前置复合外键和 `RESTRICT` 历史语义。

### `upload_sessions`

Upload Session 是独立聚合，不是 Asset 的临时状态：

- `id`
- `workspace_id`
- `actor_id`
- `reserved_asset_id`
- `reserved_asset_version_id`
- `retention_class`
- `asset_kind`
- 文件名、声明 MIME、预期字节数和预期 SHA-256
- 可选 Workflow/Product/SKU 复合归属
- 上传策略版本和完整性策略版本
- Validation Data Transfer Policy version 和 canonical snapshot SHA-256
- 内部存储后端、隔离区逻辑位置、Bucket 和服务端生成 Key
- 按保留类型预留的 Task/Foundation 目标位置、Bucket 和服务端生成 Key
- `state`: `OPEN` / `FINALIZING` / `FINALIZED` / `EXPIRED` / `ABORTED`
- finalize Lease Owner、Token、到期时间和尝试次数
- `finalized_asset_version_id`
- `validation_operation_id`
- `cleanup_operation_id`
- `cleanup_reconcile_until`
- `expires_at`
- `version`
- `created_at`
- `updated_at`

Lease 字段只允许在 `FINALIZING` 同时存在；finalize 结果字段只允许在 `FINALIZED` 同时
存在，且结果必须等于预留的 Asset Version ID。`cleanup_operation_id` 只允许挂在
`FINALIZED`、`EXPIRED` 或 `ABORTED` 终态，并通过 Workspace 前置复合外键指向唯一的
`ASSET_DELETION` Durable Operation；它必须与 `cleanup_reconcile_until` 同时为空或同时
存在。`(state, expires_at, id)` 索引服务于无 API 流量时的全局到期扫描。所有时间列使用
`DATETIME(6)`，确保 Upload 到期、Finalize Lease 与重协调边界不会被秒级舍入改变。
隔离源与保留目标必须不同。公开 API 不返回内部 Bucket、Key 或后端凭证。

### `asset_versions`

Asset Version 创建后不可更新：

- `id`
- `workspace_id`
- `asset_id`
- `version_number`
- `upload_session_id`
- 文件名、SHA-256、字节数、声明/检测 MIME
- 图片格式、宽、高和帧数
- 品类、角色和完整性策略版本
- Asset validation policy version、Validation Data Transfer Policy version 和 snapshot SHA-256
- `created_at`

`UNIQUE(upload_session_id)` 是一个 Upload Session 最多产生一个 Asset Version 的数据库
保证；`UNIQUE(asset_id, version_number)` 保证版本序号唯一。

### `asset_objects`

对象事实独立于 Asset Version 的业务元数据：

- `id`
- `workspace_id`
- `asset_version_id`
- `role`
- 内部后端、逻辑位置、Bucket 和 Key
- Provider Version ID 和不透明 ETag
- 字节数、验证后的 SHA-256 和对象状态
- `version`
- `created_at`
- `updated_at`

ETag 只用于对象存储条件操作，不能替代 SHA-256。所有 Asset 历史外键均为
Workspace 前置复合外键和 `RESTRICT`。

### `asset_validation_results`

Validation Result 是 append-only evidence：

- Workspace、Durable Operation、Asset Version 和精确 source Asset Object 身份
- execution attempt、allowlisted stage、validator name/version 和 validation policy version
- verdict、bounded reason code、Provider Version ID、不透明 ETag 和 lowercase SHA-256
- stage-specific normalized `evidence_json`
- Task retention deadline 和 `created_at`，均使用 `DATETIME(6)`

唯一键限制同一 attempt/stage 只有一条事实，`UPDATE` trigger 阻止改写；Retention 清理仍可
按治理策略 `DELETE`，数据库没有全局 no-delete trigger。外部内容安全 evidence 只保存
Validation Data Transfer Policy version/snapshot、purpose、provider 和 region 等 allowlisted
身份，不保存签名 URL、对象 Key、Secret 或 Provider 原始响应。

### `rights_records`

Rights Record 是 Asset Aggregate 的不可变、append-only 使用权事实：

- `workspace_id`、`asset_id`、可选的精确 `asset_version_id`
- 每个 Asset 单调递增且唯一的 `version_number`
- `decision`（`GRANT` 或 `REVOKE`）、权利人、来源、许可证和证据引用
- 派生与公开演示开关、条款 SHA-256
- `valid_from` 和 exclusive `valid_until`；永久权利必须显式设置 `perpetual`
- `supersedes_record_id`、创建人、`DATETIME(6)` 创建时间和一次性
  `permissions_sealed_at`

`rights_record_uses` 和 `rights_record_providers` 分别保存规范化、可索引的用途和 Provider
许可；空集合按默认拒绝解释，不能从 JSON、缓存或 Milvus 推导授权。应用在一个事务内写入
父记录、全部许可行并封存，只有 `NULL -> permissions_sealed_at` 这一次且不改变其他字段的
父记录更新被允许。封存后的许可 `INSERT` 以及 Rights Record/许可行的 `UPDATE`、`DELETE`
均由数据库 trigger 拒绝，迁移降级在存在历史时关闭式拒绝。

`assets.current_rights_record_id` 是同 Workspace、同 Asset 的复合外键。登记、替换、撤销、
到期或管理员阻断会在同一事务内锁定 Asset、追加记录（适用时）、更新 current pointer 与
Asset 状态，并写入 Audit 和 Durable Outbox。并发替换由聚合行锁、Asset optimistic version
和 `(workspace_id, asset_id, version_number)` 唯一键共同保证不会产生重复版本。
把 Asset 推进为 `AVAILABLE` 的事务在 `COMMIT` 前用最后一条、带
`UTC_TIMESTAMP(6)` 条件的 DML 重新检查 retention、当前 Rights Record、有效期和非空许可
集合；边界已跨越时整笔事务回滚。

### `asset_embeddings`

MySQL 不保存向量本体，只保存索引事实：

- `asset_id`
- `embedding_model`
- `embedding_version`
- `milvus_collection`
- `milvus_primary_key`
- `status`
- `indexed_at`

## 配置

### `brand_profiles`

- `id`
- `workspace_id`
- `name`
- `rules_json`
- `version`
- `status`

### `prompt_templates`

- `id`
- `name`
- `semantic_version`
- `node_type`
- `model_family`
- `template_ref`
- `input_schema_ref`
- `output_schema_ref`
- `status`
- `evaluation_summary_json`

生产版本不可原地修改。

### `provider_configs`

- `id`
- `provider`
- `display_name`
- `secret_ref`
- `data_region`
- `policy_json`
- `enabled`

### `model_endpoints`

- `id`
- `provider_config_id`
- `model_id`
- `model_version`
- `capabilities_json`
- `limits_json`
- `routing_group`
- `status`

## Workflow

### `workflows`

- `id`
- `workspace_id`
- `created_by`
- `workflow_type`
- `status`
- `retention_status`
- `current_node`
- `version`
- `expires_at`
- `cancellation_requested_at`
- `created_at`
- `updated_at`

索引：

- `(workspace_id, created_at)`
- `(status, updated_at)`
- `(retention_status, expires_at)`

### `workflow_steps`

- `id`
- `workflow_id`
- `step_type`
- `status`
- `sequence`
- `expected_workflow_version`
- `lease_owner`
- `lease_expires_at`
- `attempt_count`
- `max_attempts`
- `input_ref`
- `output_ref`
- `error_class`
- `started_at`
- `completed_at`

### `creative_plans`

- `id`
- `workflow_id`
- `plan_version`
- `payload_ref`
- `payload_hash`
- `created_by_type`: `AGENT` / `USER`
- `created_at`
- 唯一键 `(workflow_id, plan_version)`

### `approvals`

- `id`
- `workflow_id`
- `approval_type`
- `subject_id`
- `subject_version`
- `decision`
- `reason_code`
- `comment_ref`
- `approved_by`
- `created_at`

审批不可更新，只能追加。

## LangGraph Checkpoint

### `agent_checkpoints`

- `thread_id`
- `checkpoint_namespace`
- `checkpoint_id`
- `parent_checkpoint_id`
- `workflow_id`
- `workflow_version`
- `checkpoint_blob`
- `metadata_blob`
- `created_at`
- `expires_at`
- 主键 `(thread_id, checkpoint_namespace, checkpoint_id)`

### `agent_checkpoint_writes`

- `thread_id`
- `checkpoint_namespace`
- `checkpoint_id`
- `task_id`
- `channel`
- `write_type`
- `value_blob`
- `sequence`
- 主键覆盖 LangGraph pending writes 唯一性。

序列化格式必须版本化并限制允许类型，禁止反序列化任意 Pickle。

## 生成与评测

### `generation_attempts`

- `id`
- `workflow_id`
- `step_id`
- `candidate_index`
- `idempotency_key`
- `provider_endpoint_id`
- `provider_request_id`
- `status`
- `request_ref`
- `result_asset_id`
- `error_class`
- `cost_amount`
- `currency`
- `started_at`
- `completed_at`
- 唯一键 `idempotency_key`

### `evaluation_runs`

- `id`
- `workflow_id`
- `candidate_asset_id`
- `evaluation_suite_version`
- `status`
- `aggregate_score`
- `report_ref`
- `created_at`

### `evaluation_scores`

- `evaluation_run_id`
- `evaluator_name`
- `evaluator_version`
- `score`
- `threshold`
- `passed`
- `evidence_ref`

### `repair_plans`

- `id`
- `workflow_id`
- `evaluation_run_id`
- `repair_version`
- `payload_ref`
- `approved_automatically`
- `created_at`

## 评测数据集

### `datasets`

- `id`
- `name`
- `version`
- `category_scope_json`
- `license_summary`
- `status`

### `dataset_items`

- `id`
- `dataset_id`
- `product_ref`
- `input_asset_refs_json`
- `expected_constraints_ref`
- `human_labels_ref`
- `split`: `TRAINING` / `DEV` / `TEST`

测试集对 Prompt 优化流程保持隐藏，避免过拟合。

## 可靠消息

### `outbox_events`

- `id`
- `workspace_id`
- `source_dead_letter_id`
- `aggregate_type`
- `aggregate_id`
- `event_type`
- `schema_version`
- `payload_ref`
- `available_at`
- `published_at`
- `publish_attempts`

### `inbox_messages`

- `consumer`
- `message_id`
- `status`
- `lease_expires_at`
- `processed_at`
- 主键 `(consumer, message_id)`

### `durable_operations`

- 逻辑唯一键：
  `(workspace_id, kind, target_type, target_id, target_version, input_hash)`。
- 状态、Lease、执行尝试预算、`next_attempt_at`、对账尝试预算、`next_reconciliation_at`、
  `execution_deadline_at`、对账开始/截止时间、对账需要与结果、标准化错误、死信/重放来源、
  Recovery 已发出/已消费代次和乐观 `version`。
- `recovery_pending` 是由已发出/已消费代次生成的 STORED 列；恢复扫描使用
  `(state, recovery_pending, updated_at, id)` 索引跳过已有待消费事件的 Ready 行。
- Operation 顶层 `provider_request_id` 保存执行成功、未知结果或 Provider 状态返回的外部任务身份；
  `error_provider_request_id` 只描述当前标准化错误，两者独立更新，状态查询错误不能覆盖外部任务身份。
- `lease_expires_at == now` 视为过期，`next_attempt_at == now` 视为可重试。
- 逻辑唯一性覆盖终态；普通消息重放返回同一终态操作。管理员显式重放 Operation 死信时，
  普通失败把 `max_attempts` 设为累计 `attempt_count + 1`；未知结果耗尽则保留累计
  `reconciliation_attempt_count` 并把对应最大值设为当前计数加一，只允许一次状态查询，
  不能直接重做外部调用。
- 每次终态失败与 Operation 更新在同一事务中写入原有 DLQ；`dead_letter_id` 使失败可检查和重放。
- 所有时间字段为 UTC `DATETIME(6)`。
- `(workspace_id, id)` 是复合父键；`dead_letter_id` 和
  `replay_source_dead_letter_id` 都通过 `(workspace_id, <id>)` 引用同一工作区死信。

### `dead_letter_replays`

- 每次重放追加一条记录；源死信、工作区、执行人、原因、重放时间、重放序号和新 Outbox Event
  等审计身份字段不可变，处理生命周期字段仅通过状态 CAS 前进。
- Operation 重放在同一记录中持久化 `RECORDED -> PREPARED -> CLAIMED -> COMPLETED` 生命周期，
  并保存 Operation ID、终态预算重放或 Transport 重放类型、执行或对账类型，以及准备、认领、
  完成各 CAS 的 Operation Version。`claim_token` 与 Operation Lease Token 相同，认领与
  Operation 进入 `RUNNING` 或取得对账 Lease 在同一事务完成。
- Worker 重启依据该显式生命周期恢复准备后未认领的重放；Operation 的 Recovery Generation
  消费或迟到 Provider 身份写入不会被误判为已认领。执行/对账结算或过期 Lease 转交 Recovery
  Generation 时，以匹配的 Operation Lease Token 在同一事务完成重放生命周期并清空活动
  `claim_token`；认领时间和版本继续保留用于审计。并发或过期 Token 不能重复执行 Provider 调用，
  Worker 在认领提交后崩溃也不会留下永久 `CLAIMED` 记录。结算 CAS 使用
  `(operation_id, lifecycle_state, claim_token)` 索引。
- 原 `dead_letter_messages` 行不因重放而更新或删除。
- `dead_letter_messages`、`outbox_events` 和 `durable_operations` 各自提供
  `(workspace_id, id)` 复合父键。死信自来源、Outbox 来源、Operation 的终态/重放来源，
  以及重放生命周期的源死信、重放 Event 和 Operation 均使用 Workspace 前置复合外键。
  Legacy `NULL` Workspace 只能保持无来源；一旦附加来源即由 Check Constraint 关闭式拒绝。
- 重放后的再次失败通过 `source_dead_letter_id` 和 `replay_attempt` 继续关联原始失败。
- Operator 详情只查询直接子死信并以 `(created_at, id)` 稳定分页；每个子项继续携带
  `source_dead_letter_id`，因此任意深度的历史都能逐层完整遍历且不会静默截断。
- 同一死信的不可变重放尝试以 `(replayed_at, id)` 独立分页；详情中的重放记录和子死信
  各自返回 Continuation Cursor，任一历史维度都不会无界读取或静默截断。
- 升级时从 Workflow 或事件 Payload 确定性回填工作区；Payload 仅接受原始值直接匹配
  Workspace 正则的 JSON String，并逐字符原样保存。带首尾空白、Tab/换行、Unicode、
  JSON Null、数值、对象、数组、空值、超长值或 malformed JSON 均保留 `NULL`，只能通过
  系统管理员只读 Legacy API 查看，禁止重放。

## 审计

### `audit_events`

- `id`
- `workspace_id`
- `actor_type`
- `actor_id`
- `action`
- `resource_type`
- `resource_id`
- `trace_id`
- `metadata_json`
- `created_at`
- `expires_at`

审计中不能保存 Secret、原图、完整 Prompt 或模型原始响应。
