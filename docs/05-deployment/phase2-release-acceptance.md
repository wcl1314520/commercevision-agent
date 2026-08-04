# Phase 2 发布验收

| 属性 | 值 |
|---|---|
| 状态 | verified |
| 最后更新 | 2026-08-04 |
| 适用版本 | Phase 2 / 0.2.0 |

## 目标与权威入口

Phase 2 的发布判定由版本化清单
`evaluation/phase2/release-v1/manifest.json` 驱动，不依赖人工勾选。清单固定要求、故障组件、恢复不变量、
CI 门禁和 public-demo/private 边界；审计器只读取仓库内的普通 UTF-8 文件，拒绝重复 JSON key、路径穿越、
符号链接逃逸、缺失 anchor 和超限输入。

```powershell
uv run commercevision-phase2-acceptance `
  --manifest evaluation/phase2/release-v1/manifest.json `
  --repository-root . `
  --json-output .artifacts/acceptance/phase2-release.json `
  --markdown-output .artifacts/acceptance/phase2-release.md
```

退出码 `0` 表示通过，`2` 表示输入或证据被拒绝。JSON/Markdown 只保存聚合状态、清单 digest 和证据
digest；不会输出私有 workspace、credential scope、隐藏数据路径或候选 payload。

## 发布门禁

本地与 CI 使用相同锁文件和入口：

```powershell
uv sync --locked --all-packages
uv run ruff format --check .
uv run ruff check .
uv run mypy packages/evaluation scripts/audit_licenses.py scripts/check_mypy_baseline.py
uv run python scripts/check_mypy_baseline.py
uv run python scripts/audit_licenses.py
uv run pytest
pnpm web:lint
pnpm web:typecheck
pnpm web:unit-test
pnpm web:build
pnpm web:e2e
pnpm licenses list --prod --json | node scripts/audit-node-licenses.mjs
```

CI 另外验证 OpenAPI drift、MCP/Provider contracts、daily retrieval evaluation、Python/Node dependency audit、
Compose config/全服务镜像构建、Gitleaks 和 SPDX SBOM，并上传 `phase2-release-acceptance` 聚合 artifact。

## 恢复与迁移矩阵

| 边界 | 发布证明 |
|---|---|
| MinIO / ClamAV / 内容安全 | 上传验证在存储或扫描失败后可重试，terminal reject 不调用后续 Provider，保留期不延长 |
| Vision / ProductBrief | typed Provider 故障持久化；人工等待和确认可跨 Worker 重启恢复 |
| Embedding / Milvus | Provider timeout 失败关闭；增量 upsert、lease 恢复和 generation 主键防重复向量 |
| RabbitMQ / Worker | Outbox/Inbox、提交 marker、lease recovery 保证逻辑操作最终只生效一次 |
| reranker | 显式 degraded 响应保留确定性融合顺序，不伪装完整混合检索 |
| Collection rebuild | 删除/中断后从 MySQL cursor 恢复，Rights watermark 验证后原子切换 |
| Migration | 空库完整升级、Phase 1→2、非破坏降级/重升级、Alembic drift 与 `DATETIME(6)` contract |

清单中的每项都绑定具体自动测试函数 anchor；anchor 移除或改名会使审计失败，而不是静默降级。

## 公共 Demo 隔离

`infra/public-demo/phase2.env.example` 是独立部署 profile：只允许 `catalog-demo` workspace，不开放管理员
workspace；使用四个 `public-demo-*` bucket、独立对象前缀和 OIDC credential scope，禁用 Vision/Embedding/
Validation 数据外传，并只声明有明确许可的 `retrieval-daily-v1` 数据集。示例 ARN 必须在部署时由平台配置
替换；不得把 private profile 或静态密钥复制到该文件。

配置在构建前验证：

```powershell
docker compose --env-file infra/public-demo/phase2.env.example `
  -f infra/compose/docker-compose.yml config --quiet
```

## 已知类型债务

Phase 2 新增的 evaluation 和发布脚本必须 Mypy 零诊断。历史 Python 全域仍有类型诊断，因此当前采用
锁定 Mypy `2.3.0` 的精确诊断数量与 SHA-256 基线；新增、消失、位置/消息漂移都会失败，必须在独立变更中
显式更新 `.mypy-baseline.json`。该基线是防新增债务的过渡门禁，不等同于全仓零诊断，也不能用来豁免
发布关键代码。

## 发布审批

最终批准要求同一 Git SHA 的 GitHub Actions 全绿。审批记录至少保存 SHA、CI run、Phase 2 聚合 artifact、
retrieval evaluation artifact、SBOM、迁移 revision 和已知问题；hidden release 输入始终位于 Git 外的隔离存储。
