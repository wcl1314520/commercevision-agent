# MySQL 逻辑模型

| 属性 | 值 |
|---|---|
| 状态 | decision |
| 最后更新 | 2026-07-22 |
| 适用版本 | Schema v1 |

## 设计原则

- MySQL 8.4 LTS。
- InnoDB。
- UTC 时间统一保存为 MySQL `DATETIME(6)`，应用层拒绝 naive datetime，读取时恢复 UTC 时区。
- `utf8mb4`。
- 业务主键使用 UUIDv7/ULID 的二进制表示或有序字符表示。
- 金额、成本和 Token 不使用浮点数。
- 所有可变业务实体带 `version`。
- JSON 只用于品类扩展，不替代关系约束。

## 身份与工作区

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
- `external_id`
- `category_code`
- `title`
- `brand_id`
- `attributes_json`
- `source_version`
- `expires_at`

### `skus`

- `id`
- `product_id`
- `external_sku`
- `attributes_json`
- `expires_at`

### `assets`

- `id`
- `workspace_id`
- `asset_type`
- `storage_bucket`
- `storage_key`
- `sha256`
- `mime_type`
- `width`
- `height`
- `foundation`
- `status`
- `expires_at`
- 唯一键 `(workspace_id, sha256, asset_type)`

### `asset_rights`

- `asset_id`
- `owner`
- `source`
- `license_type`
- `allowed_uses_json`
- `allowed_providers_json`
- `valid_from`
- `valid_until`

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
