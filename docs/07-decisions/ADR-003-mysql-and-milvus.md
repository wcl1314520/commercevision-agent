# ADR-003：MySQL 与 Milvus 分离

| 属性 | 值 |
|---|---|
| 状态 | accepted |
| 日期 | 2026-07-21 |

## 背景

项目明确选择 MySQL，同时需要专业的图片向量检索。强行让单一数据库承担所有能力会降低检索和演进质量。

## 决策

- MySQL 8.4 LTS 保存业务状态、Agent Checkpoint、审批、Outbox/Inbox 和审计。
- Milvus 保存图片和多模态向量索引。
- MySQL 是事实来源，Milvus 可以重建。

## 后果

- 需要实现索引 Outbox 和一致性修复。
- 需要运维 Milvus 或采用经过评估的托管服务。
- 不依赖 MySQL 原生向量功能。
- 检索权利过滤最终由 MySQL 强制执行。

## 验证

- Milvus 清空后可以重建。
- 未授权资产不能因向量索引残留被召回。
- MySQL/Milvus 不一致有监控和修复。

