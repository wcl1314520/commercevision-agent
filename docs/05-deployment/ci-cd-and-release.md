# CI/CD 与发布

| 属性 | 值 |
|---|---|
| 状态 | decision |
| 最后更新 | 2026-07-22 |
| 适用版本 | Delivery v1 |

## 分支与变更

- 主分支始终可发布。
- 功能使用短生命周期分支。
- PR 必须关联需求、ADR 或 Issue。
- 数据库迁移、Prompt 变更和模型路由变更单独标记。
- 禁止将大量自动生成代码无审查合并。

## Pull Request Pipeline

### 通用

- Secret Scan。
- License 检查。
- 依赖漏洞。
- 文档链接和 Schema 检查。

### Python

- Ruff format/check。
- 类型检查。
- 单元测试。
- Contract 测试。
- 小型确定性 Agent Eval。

### Web

- ESLint。
- TypeScript。
- Component 测试。
- Production Build。

### Infrastructure

- Docker Build。
- Dockerfile lint。
- Terraform fmt/validate/tflint。
- Helm lint/template。
- Kubernetes Policy。

### 集成

- 启动 MySQL、Redis、RabbitMQ、Milvus 和 MinIO。
- 运行迁移。
- 运行 Outbox、Checkpoint、检索和 Provider Mock 测试。

## 构建产物

- 不可变 OCI 镜像。
- Git SHA 和版本标签。
- SBOM。
- 镜像漏洞报告。
- 签名。
- 数据库迁移包。
- Prompt/Schema Bundle。
- Helm Chart。
- Evaluation Report。

## 发布流程

1. 合并主分支。
2. 构建并签名镜像。
3. 自动部署 staging。
4. 运行迁移 dry-run。
5. Contract、E2E 和真实模型冒烟。
6. 运行完整 Validation Eval。
7. 人工批准。
8. Canary 发布 API/Worker。
9. 观察 SLO、Queue、Provider 和质量指标。
10. 全量发布。

## 数据库迁移

- Alembic。
- Expand/Contract。
- 先兼容旧代码，再删除旧字段。
- 大表迁移评估锁和执行时间。
- MySQL 类型变更必须验证实际 DDL 算法；需要 `ALGORITHM=COPY` 的迁移只能在上线前执行，或在生产维护窗口通过 `gh-ost`、`pt-online-schema-change` 等受控在线迁移流程执行。
- CI 必须运行 `alembic check` 和 `INFORMATION_SCHEMA` schema contract 测试；自定义类型不能依赖 Alembic 默认比较器推断精度、字符集等方言属性。
- 迁移前备份和恢复验证。
- 失败采用前向修复，避免危险降级脚本。

## Prompt/模型发布

Prompt 和模型不随意跟代码一起上线：

- 新版本进入 staging。
- 运行固定 Dataset。
- 记录质量、成本和时延。
- 通过 Gate 后发布。
- 生产 Workflow 固定使用创建时版本。
- 回滚只切换新 Workflow 默认版本，不修改历史任务。

## Demo 发布

- 与 production 使用不同 Secret、Bucket 和配额。
- 公开环境只使用授权数据。
- 每次发布执行滥用和成本冒烟。
- 首页展示版本、状态和限制。
- Demo 不提供管理员配置入口给匿名用户。

## 回滚

- 应用：Helm 回滚到前一镜像。
- Prompt/模型：切换默认版本。
- 数据库：前向修复。
- Event Schema：保留兼容 Consumer。
- 错误 Worker：停止消费，消息留在 Queue。

## Release 证据

每个版本保存：

- Changelog。
- 镜像 Digest。
- SBOM。
- 迁移版本。
- Prompt/模型/工具版本。
- Eval 报告。
- 已知问题。
- 发布和回滚结果。
