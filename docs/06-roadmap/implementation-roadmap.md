# 实施路线

| 属性 | 值 |
|---|---|
| 状态 | decision |
| 最后更新 | 2026-07-21 |
| 适用版本 | Roadmap v1 |

## 执行原则

- 不建设一次性 MVP。
- 每个阶段产物直接进入最终架构。
- 不以“先写死，后面再重构”作为常规方案。
- 阶段性减少的是启用范围，不降低安全、测试和可恢复要求。
- 每个阶段必须有自动测试、文档和可运行演示。
- Agent 质量和工程质量同等重要。

## Phase 0：项目与工程基线

**状态：verified（2026-07-21）**

### 目标

建立公开仓库、开发规范、基础设施和可重复构建。

### 交付

- Python Workspace 和 Next.js App。
- FastAPI、Worker、Scheduler、MCP Server 入口。
- Domain/Contract/Agent 包边界。
- Docker Compose。
- MySQL、Redis 协议兼容缓存（本地 Valkey）、RabbitMQ、Milvus、MinIO。
- CI、Secret Scan、SBOM 和依赖锁。
- OpenTelemetry 基线。
- OpenAPI 和配置校验。

### 退出标准

- [x] 新环境一条命令启动。
- [x] 所有服务有 health/readiness。
- [x] CI 工作流覆盖 Python、Web、容器构建、Secret Scan 和 SBOM。
- [x] Secret 不进入 Git、镜像和日志。
- [x] 本地和 CI 使用相同锁文件、OpenAPI 导出和 Compose 配置。

验收证据见 [本地开发与 Phase 0-1 Runbook](../05-deployment/local-development.md)。数据库迁移框架在 Phase 1 随首个领域表落地，Phase 0 不创建空迁移。

## Phase 1：领域状态与 Durable Agent Runtime

**状态：verified（2026-07-22）**

### 目标

先建立可靠的 Agent 执行内核，再接入真实模型。

### 交付

- Workflow/Step/Attempt 状态机。
- MySQL Repository 和 Alembic。
- 自定义 MySQL LangGraph Checkpointer。
- Outbox、Inbox、Lease、Retry 和 DLQ。
- Interrupt/Resume。
- Recovery Scheduler。
- Fixture Provider 和 Tool Runtime。

### 退出标准

- [x] 杀死 Worker 后从 Checkpoint 恢复。
- [x] 重复消息不产生重复有效结果。
- [x] 人工等待跨发布保持。
- [x] 所有非法状态转换被拒绝。
- [x] 长工具调用不持有 MySQL 事务。

验收证据见 [本地开发与 Phase 0-1 Runbook](../05-deployment/local-development.md)。

## Phase 2：资产、商品理解与多模态记忆

### 目标

建立安全可追溯的输入和检索系统。

### 交付

- OSS/MinIO 直传。
- 图片真实性、安全和权利校验。
- ProductBrief Schema。
- Vision Analyzer。
- Brand Profile。
- Embedding Indexer。
- Milvus 索引。
- MySQL FULLTEXT + Milvus + 业务重排。
- Product Catalog/Asset MCP Server。

### 范围

先启用美妆和汽车配件，Schema 和数据模型按多品类设计。

### 退出标准

- 未授权素材召回率为 0。
- 索引增量更新，不全量重建。
- 固定检索集达到约定 Recall@K/nDCG。
- ProductBrief 低置信度可以人工确认。
- Milvus 丢失后可重建。

## Phase 3：Planning 与 Human-in-the-loop

### 目标

实现真正可审核的 Agent 规划，而不是直接生成 Prompt。

### 交付

- ContextBuilder。
- Prompt Registry。
- CreativePlan Schema。
- Planner Node。
- ProductBrief 确认页面。
- Creative Plan 编辑、版本和审批。
- Tool Intent 与服务端 Policy。
- SSE 和恢复游标。

### 退出标准

- 未审批方案无法执行。
- Prompt Injection 不能增加工具或权限。
- 计划可追溯到检索引用和 Prompt 版本。
- 旧页面审批不能覆盖新版本。
- 计划 Fixture 和 Agent Eval 通过。

## Phase 4：图片生成、编辑与模型路由

### 目标

接入真实模型并形成可靠的多供应商执行。

### 交付

- 至少两个图片供应商 Adapter。
- 一个 Vision/Planning Provider。
- Capability Registry。
- Router、熔断、配额和降级。
- 图片生成和编辑 Worker。
- Provider task 对账。
- 候选图 UI。
- 成本和使用量记录。

### 退出标准

- 主端点故障时切换兼容端点。
- 内容拒绝不会被跨供应商规避。
- 同一幂等键只产生一个有效结果。
- 未知供应商结果不会盲目重发。
- 模型能力和费用可观测。

## Phase 5：Evaluator、Reflection 与 Replay

### 目标

建立项目最核心的 Agent 评测闭环。

### 交付

- 文件和 Amazon 规则 Evaluator。
- OCR/Logo/商品一致性 Evaluator。
- 校准后的模型 Judge。
- Evaluation Suite 和 Dataset。
- RepairPlan 和有限 Reflection。
- Trace Timeline。
- Replay Runner。
- Experiment Report。

### 范围

完成美妆和汽车配件的开发/验证/隐藏测试集，再扩展食品和服装。

### 退出标准

- 无 Reflection/有 Reflection 有正式对比。
- 无检索/有检索有正式对比。
- 人工与 Judge 一致率达到标定标准。
- Agent 无界循环为 0。
- 失败任务可以使用固定版本回放。

## Phase 6：完整产品体验与开放接口

### 目标

形成可被真实用户使用的完整产品。

### 交付

- 项目和任务工作台。
- 候选图对比、终审和局部返工。
- Batch API。
- Webhook。
- Amazon US 导出。
- 管理员模型、Prompt、品牌和 Dataset 页面。
- 公开 Demo 模式。
- API 文档和 SDK 示例。

### 退出标准

- Web、API 和批量任务完成同一完整流程。
- 未终审图片不能导出。
- 每个导出文件可追溯。
- E2E 覆盖主流程和异常流程。
- 公开 Demo 不能访问管理员或私有资源。

## Phase 7：生产硬化

### 目标

将系统提升到可长期运行和私有部署的质量。

### 交付

- ACK/RDS/Tair/RabbitMQ/OSS/Milvus 部署。
- 多副本、PDB、HPA 和滚动发布。
- SLO Dashboard 和 Runbook。
- 安全扫描和渗透测试。
- 备份恢复。
- Retention 清理。
- Load/Soak/Chaos。
- Prompt/模型 Release Gate。

### 退出标准

- staging 连续 14 天无未解决 P1/P2。
- 完成 Worker、Queue、MySQL、Milvus 和 Provider 故障演练。
- 任务数据 72 小时清理通过。
- 发布和回滚演练通过。
- 企业部署目标通过验收。

## Phase 8：公开发布与求职交付

### 目标

让 HR 和技术面试官能够快速验证项目价值。

### 交付

- 公开 GitHub。
- Apache-2.0 License 和第三方声明。
- 在线 Demo。
- 三到五分钟演示视频。
- 架构图、Trace 和 Replay 示例。
- 公开评测报告。
- 中文/英文 README。
- 部署指南和贡献指南。
- 三篇技术文章：
  - Durable Agent 与 MySQL Checkpointer。
  - 多模态 RAG 与 Agent Evaluation。
  - 从 Prompt Wrapper 到生产 Agent。

### 退出标准

- 新用户能按 README 本地启动。
- 在线 Demo 可完成完整任务。
- 公开数据和代码许可清晰。
- README 包含可复现指标。
- 面试演示脚本覆盖 Agent 决策、失败和恢复。

## 阶段依赖

```text
Phase 0
  -> Phase 1
  -> Phase 2
  -> Phase 3
  -> Phase 4
  -> Phase 5
  -> Phase 6
  -> Phase 7
  -> Phase 8
```

允许同一 Phase 内并行开发互不依赖的 UI、Fixture 和文档，但不能跳过退出标准进入下一阶段。
