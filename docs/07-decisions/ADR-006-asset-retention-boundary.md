# ADR-006：任务资产与基础资产采用不同保留边界

| 属性 | 值 |
|---|---|
| 状态 | accepted |
| 日期 | 2026-07-22 |

## 背景

任务内容包含商品图、真人图、Prompt、分析正文、候选图和生成记录，企业只要求短暂保存；品牌素材、Prompt 模板、注册 LoRA、模型配置、授权参考素材和公开评测集则需要跨任务复用。若所有数据统一保存 72 小时，基础资产无法支持稳定生产；若所有数据长期保存，又会扩大任务隐私、权利和清理风险。

## 决策

- 任务资产从 Workflow 创建起保存 72 小时，到期后停止使用并清理对象、正文和 Checkpoint 节点数据。
- 基础资产保存至管理员删除或权利到期，以两者先发生者为准。
- 资产必须先有权利记录才能进入可检索、可派生、创意生成或通用模型处理的状态。
- 唯一的 Rights 前例外是 Quarantine 内的安全校验：企业管理员必须显式发布
  deny-by-default 的 Validation Data Transfer Policy，且目的只能是
  `SECURITY_VALIDATION`。策略精确限制 Workspace、Asset Version、Asset Kind、
  Retention Class、Provider 和 Endpoint Region；它不授予检索、派生、创意使用或任何
  Rights Record 权利。
- 外部安全校验的策略 version 和 canonical snapshot hash 在 Upload Session、
  Asset Version、Durable Operation input hash 与 append-only evidence 中保持一致。
  Worker 在每次签发临时引用前按当前服务端配置重验；撤销或配置漂移立即关闭式阻断。
  Workspace ID 使用二进制精确匹配，不能 casefold。
- 基础资产被删除或权利到期时，MySQL 先将其标记为不可使用，再异步收敛 Milvus 索引和对象存储。
- 脱敏业务 tombstone、审计事件和运行指标不属于任务资产，其保留期由独立治理策略定义。

## 后果

- 清理器必须区分任务资产与基础资产，不能仅按 Bucket 或创建时间批量删除。
- MySQL 是资产状态和权利状态的事实来源；Milvus 中存在向量不能证明资产仍可使用。
- 备份、重放、导出和公开 Demo 都不得绕过资产保留边界。
- Validation Data Transfer Policy 只是安全校验外传同意，不得被解释为 Rights Record。

## 验证

- 72 小时任务资产清理测试覆盖 MySQL、对象存储和 Checkpoint。
- 管理员删除与权利到期测试覆盖检索阻断和 MySQL/Milvus/对象存储最终收敛。
- 基础资产不会被任务清理器误删。
- 默认拒绝、策略撤销、Workspace 大小写不匹配和 Endpoint Region 漂移测试证明不会签发
  临时 URL 或调用外部 Provider。
