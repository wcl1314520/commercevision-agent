# Agent 求职价值映射

| 属性 | 值 |
|---|---|
| 状态 | decision |
| 最后更新 | 2026-07-21 |
| 适用目标 | Agent 应用开发实习 |

## 作品要证明什么

招聘方需要看到的不只是“接入了大模型”，而是候选人能够：

- 把业务任务建模成可执行 Agent Graph。
- 设计 Planning、Tool Use、Memory 和 Reflection。
- 约束模型输出和工具权限。
- 处理异步任务、错误、恢复和人工介入。
- 建立 Dataset、Evaluator、Replay 和 bad case 闭环。
- 交付 Web、API、数据库、测试、部署和运维。

## 能力映射

| 招聘能力 | 项目证据 |
|---|---|
| Prompt/Context Engineering | Prompt Registry、上下文预算、结构化 Schema |
| Agent Framework | LangGraph 状态图、自定义 Checkpointer |
| Tool Calling | Tool Registry、MCP Server、服务端参数解析 |
| RAG | Milvus 多模态检索、MySQL 过滤、重排 |
| Memory | 工作记忆、品牌记忆、经验记忆 |
| Human-in-the-loop | 方案审核、结果终审、interrupt/resume |
| Evaluation | 固定数据集、规则 Evaluator、模型 Judge、人工标签 |
| Reflection | Repair Plan 和有限自动修正 |
| AgentOps | Trace、Replay、版本对比、成本和延迟指标 |
| 后端工程 | FastAPI、MySQL、队列、幂等和 Webhook |
| 全栈交付 | Next.js 工作台和公开演示 |
| 生产部署 | Docker、Kubernetes、CI/CD、可观测性和 Runbook |

## HR 可见产物

- 一句话明确项目价值。
- 公开 GitHub 仓库。
- 可访问的在线演示。
- 三到五分钟演示视频。
- README 中的 Agent 工作流图。
- 有数字的评测结果。
- 清晰的技术栈和个人职责。

## 技术面试可见产物

- Agent 状态图和状态转换测试。
- Checkpoint 恢复演示。
- Tool/MCP 权限边界。
- 一次失败任务的完整 Trace 和 Replay。
- 无检索/有检索、无反思/有反思的实验对比。
- Provider 超时和重复消息故障注入。
- 架构 ADR 和取舍说明。

## 不允许出现的作品问题

- 只有截图，没有在线系统或可运行说明。
- 大量框架代码无法解释。
- 没有测试数据和效果指标。
- 把模型调用次数当作 Agent 能力。
- 为追求“多 Agent”增加无意义角色。
- README 宣称高可用但没有故障实验。
- 仓库包含真实密钥、私有数据或无许可代码。

