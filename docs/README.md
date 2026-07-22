# CommerceVision Agent 文档索引

| 属性 | 值 |
|---|---|
| 状态 | decision |
| 最后更新 | 2026-07-21 |
| 适用版本 | Project Definition v1 |

## 00 产品

- [产品定义与边界](00-product/product-definition.md)
- [用户与 Agent 工作流](00-product/user-workflows.md)

## 01 架构

- [系统架构](01-architecture/system-architecture.md)
- [Agent Runtime](01-architecture/agent-runtime.md)
- [工作流状态机](01-architecture/workflow-state-machine.md)
- [目标仓库结构](01-architecture/repository-layout.md)

## 02 数据

- [数据架构](02-data/data-architecture.md)
- [MySQL 逻辑模型](02-data/mysql-schema.md)
- [记忆与多模态检索](02-data/memory-and-retrieval.md)

## 03 AI 与 Agent

- [工具、MCP 与模型路由](03-ai/tools-mcp-and-routing.md)
- [Prompt、Context 与 Guardrails](03-ai/prompt-context-and-guardrails.md)
- [评测、反思与回放](03-ai/evaluation-and-replay.md)

## 04 工程

- [API、事件与集成契约](04-engineering/api-events-and-integrations.md)
- [可靠性、安全与数据治理](04-engineering/reliability-security-and-governance.md)
- [测试策略](04-engineering/testing-strategy.md)

## 05 部署与运维

- [部署拓扑](05-deployment/deployment-topology.md)
- [本地开发与 Phase 0-1 Runbook](05-deployment/local-development.md)
- [可观测性与运行维护](05-deployment/observability-and-operations.md)
- [CI/CD 与发布](05-deployment/ci-cd-and-release.md)

## 06 路线图

- [实施路线](06-roadmap/implementation-roadmap.md)
- [上线验收标准](06-roadmap/acceptance-criteria.md)

## 07 决策记录

- [ADR 索引](07-decisions/README.md)
- [ADR-001：公开的垂直 Agent 项目](07-decisions/ADR-001-public-vertical-agent.md)
- [ADR-002：Python-first 技术栈](07-decisions/ADR-002-python-first-stack.md)
- [ADR-003：MySQL 与 Milvus 分离](07-decisions/ADR-003-mysql-and-milvus.md)
- [ADR-004：单一 Agent 与 LangGraph](07-decisions/ADR-004-single-agent-langgraph.md)
- [ADR-005：开源许可与来源代码边界](07-decisions/ADR-005-open-source-boundary.md)

## 08 研究

- [来源项目吸收策略](08-research/source-adoption.md)
- [Agent 求职价值映射](08-research/job-market-fit.md)
- [外部依据](08-research/external-references.md)

## 模板

- [ADR 模板](templates/adr-template.md)
- [评测实验模板](templates/evaluation-experiment.md)
- [事故复盘模板](templates/incident-report.md)

## 文档规则

- `fact`：源码或运行结果能够直接证明。
- `decision`：项目已经采用的设计。
- `planned`：已进入路线图但尚未实现。
- `verified`：已经通过测试、部署或外部证据验证。
- 关键架构变更必须新增或更新 ADR。
- 路线图完成状态必须以自动化测试和可运行产物为依据。
