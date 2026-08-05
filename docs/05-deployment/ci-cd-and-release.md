# CI/CD 与发布

| 属性 | 值 |
|---|---|
| 状态 | decision |
| 最后更新 | 2026-08-04 |
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
- 发布关键模块 Mypy 零诊断；其余全域 Mypy 诊断使用锁定版本的精确哈希基线，任何漂移均失败。
- Python License policy 与依赖漏洞检查。
- 单元测试。
- Contract 测试。
- 小型确定性 Agent Eval。

### Web

- ESLint。
- TypeScript。
- Component 测试。
- Node production dependency License policy 与漏洞检查。
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
- Phase 2 aggregate-only release acceptance JSON/Markdown。

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
- 迁移身份与运行时身份必须分离。Alembic 在部署环境中要求一次性 Migration Job 直接注入 `CV_MIGRATION_MYSQL_DSN`；API、Worker 和 Scheduler 的 `CV_MYSQL_DSN` 只能拥有 `SELECT/INSERT/UPDATE/DELETE`，不得拥有 DDL、`TRIGGER` 或 `GRANT OPTION`。
- 每次部署必须先幂等收敛运行时 Grants，并通过 `SHOW GRANTS` 与真实 DDL 拒绝探针；不能依赖 MySQL 镜像只在空数据卷执行的首次初始化逻辑。
- 发布前必须验证迁移身份拥有本次 DDL 所需权限。MySQL 8.4 开启 Binary Log 时，`CREATE TRIGGER` 除 `TRIGGER` 外还可能要求管理权限；由 DBA 配置专用迁移身份或托管实例的受控参数，禁止为运行时账号授予 `SUPER`。
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

### Retrieval Evaluation Report

- PR/main CI 运行 `retrieval-daily-v1`，并以 `retrieval-evaluation-daily` artifact 留存 JSON 与 Markdown。
- 正式发布从隔离存储挂载 hidden release manifest/observations，使用 `--profile release` 与
  `confidence-bound` 阈值运行，产物命名为 `retrieval-evaluation-release`。
- 报告必须记录 suite、candidate universe、Rights snapshot、Retrieval Policy、Embedding Model 和
  Collection version；三个未授权指标必须全部为零。
- 发布审批保留聚合报告，不复制隐藏 Query、候选 ID、Rights payload 或任何未授权内容。

### Phase 2 Release Acceptance

- 版本化入口为 `evaluation/phase2/release-v1/manifest.json`，由
  `commercevision-phase2-acceptance` 校验证据路径、anchor、故障矩阵、恢复不变量和 CI 门禁全集。
- `phase2-release-acceptance` artifact 只包含聚合 JSON/Markdown、manifest digest 和证据 digest；
  不包含私有 workspace、credential scope、隐藏数据路径或候选 payload。
- 公共 Demo 必须使用 `infra/public-demo/phase2.env.example` 对应的独立 workspace、四个 bucket、
  OIDC credential scope、对象前缀、配额和已授权 daily dataset；不得与私有部署 profile 混用。
- 完整运行顺序、故障映射和类型基线策略见 [Phase 2 发布验收](phase2-release-acceptance.md)。

### Planner Evaluation Report

- PR/main CI 运行 provider-free `planner-ci-v1`，并以 `planner-evaluation-ci` artifact 独立留存带摘要的
  JSON 与聚合 Markdown。
- 正式发布从隔离存储只读挂载 `evaluation/planner/hidden-release/`，使用 `--profile release` 运行，
  产物命名为 `planner-evaluation-release`。
- 发布审批必须先验证 JSON `report_sha256`；任何 policy violation、unauthorized tool/provider/resource、
  budget expansion 或 missing approval evidence 非零都拒绝发布。

### Phase 3 Release Acceptance

- 版本化入口为 `evaluation/phase3/release-v1/manifest.json`，由
  `commercevision-phase3-acceptance` 失败关闭地校验证据、故障矩阵、恢复/授权不变量和 14 项 CI gate。
- `phase3-release-acceptance` artifact 只保留固定 gate、manifest/evidence digest 与 public-demo 边界数量，
  不保留 Workspace、Prompt revision、cursor signing scope、Plan/Approval 或隐藏数据。
- 公共 Demo 使用 `infra/public-demo/phase3.env.example` 的独立 deployment profile；Prompt、cursor、quota、
  Planner dataset 和 credential scope 均不得与 private profile 重叠。
- 完整运行顺序与同一最终 SHA 规则见 [Phase 3 发布验收](phase3-release-acceptance.md)。
- artifact 不得包含 case ID、ProductBrief/Brand/Retrieval 身份、Prompt Injection 文本或方案 payload。
