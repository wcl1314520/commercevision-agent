# ADR-002：Python-first 技术栈

| 属性 | 值 |
|---|---|
| 状态 | accepted |
| 日期 | 2026-07-21 |

## 背景

项目主要目标岗位是 Agent 应用开发，当前最强语言是 Python。过多后端语言会分散深度和维护能力。

## 决策

- Agent、API、Worker、Scheduler 和 MCP Server 使用 Python。
- Web 使用 Next.js + TypeScript。
- 不在第一阶段引入 Java/Go 服务。

## 后果

- 可直接使用 LangGraph、FastAPI、Pydantic 和主流 AI 生态。
- 后端拥有统一领域模型和测试体系。
- TypeScript 只负责用户界面和生成的 API Client。

## 验证

- 核心 Agent、领域和可靠性逻辑可由项目负责人完整解释和调试。
- Python 类型检查、测试和性能满足目标。

