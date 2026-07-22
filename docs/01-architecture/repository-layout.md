# 目标仓库结构

| 属性 | 值 |
|---|---|
| 状态 | decision |
| 最后更新 | 2026-07-21 |
| 适用版本 | Repository v1 |

## 结构

```text
commercevision-agent/
├── apps/
│   └── web/                         # Next.js 工作台
├── services/
│   ├── api/                         # FastAPI Control API
│   ├── worker/                      # Agent/生成/评测 Worker 入口
│   ├── scheduler/                   # Outbox、Recovery、Retention
│   └── mcp-server/                  # 受控 MCP 工具
├── packages/
│   ├── agent-core/                  # LangGraph、State、Node
│   ├── domain/                      # Workflow、审批、资产领域模型
│   ├── contracts/                   # Pydantic/OpenAPI/Event Schema
│   ├── providers/                   # LLM/Vision/Image Adapter
│   ├── retrieval/                   # Embedding、Milvus、重排
│   ├── evaluation/                  # Evaluator、Dataset、Replay
│   ├── tool-runtime/                # Tool Registry、Policy、MCP Client
│   └── observability/               # Trace、Metrics、Logging
├── database/
│   ├── migrations/                  # Alembic
│   ├── seeds/
│   └── diagrams/
├── prompts/
│   ├── templates/
│   ├── schemas/
│   └── evaluation-fixtures/
├── datasets/
│   ├── manifests/                   # 不提交受限原图
│   ├── public/
│   └── expected/
├── infra/
│   ├── docker/
│   ├── compose/
│   ├── helm/
│   └── terraform/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   ├── agent-evals/
│   ├── e2e/
│   ├── load/
│   └── chaos/
├── docs/
├── scripts/
├── .github/workflows/
├── pyproject.toml
├── uv.lock
├── package.json
├── pnpm-lock.yaml
└── README.md
```

## 依赖方向

```text
apps/services
    -> packages/agent-core
    -> packages/domain
    -> packages/contracts

providers/retrieval/evaluation/tool-runtime
    -> contracts

domain
    -> contracts
    -X-> infrastructure SDK
```

领域层不能直接依赖：

- FastAPI。
- Celery。
- MySQL Driver。
- Milvus SDK。
- 模型供应商 SDK。

基础设施通过接口注入。

## Python 工作区

- 使用一个根 `pyproject.toml` 管理 Python Workspace。
- 使用 `uv.lock` 锁定依赖和 Python 版本。
- API、Worker、Scheduler 和 MCP Server 共享领域包，但拥有独立入口。
- 公共 Contract 使用 Pydantic，并生成 OpenAPI/JSON Schema。

## 前端工作区

- Next.js 和 TypeScript 独立锁定依赖。
- API Client 从 OpenAPI 生成。
- 前端不得复制后端枚举和状态字符串。
- UI 只通过 API/SSE 获取状态。

## 配置

- `config/base`：非敏感默认值。
- `.env.example`：只包含占位符。
- 本地 Secret：不提交。
- 生产 Secret：KMS/Secret Manager。
- 配置启动时严格校验，禁止静默降级为空配置。

## 代码所有权边界

| 目录 | 职责 |
|---|---|
| `agent-core` | Agent 状态、Graph 和节点编排 |
| `domain` | 不依赖框架的业务规则 |
| `providers` | 外部模型协议与错误标准化 |
| `retrieval` | 向量化、检索和重排 |
| `evaluation` | 评测、数据集和回放 |
| `tool-runtime` | 工具权限、参数和执行 |
| `services` | 传输、进程和依赖装配 |

## 禁止项

- 在 Controller 中写 Prompt。
- 在 React 组件中写业务状态机。
- 在 Agent Node 中直接执行 SQL。
- 在 Provider Adapter 中决定业务路由。
- 在数据库模型中保存永久公网图片 URL。
- 在同一个文件中混合 Prompt、SDK 调用和 UI 逻辑。

