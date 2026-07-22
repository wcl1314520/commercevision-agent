# Contributing

## 开发前提

- Python 3.13。
- `uv` 0.11.7 或兼容版本。
- Node.js 22。
- pnpm 11.9.0。
- Docker Desktop 或 Docker Engine + Compose v2，建议至少分配 8 GB 内存。

## 本地启动

PowerShell：

```powershell
.\scripts\dev.ps1 up
```

Bash：

```bash
./scripts/dev.sh up
```

完整说明见 [本地开发与 Phase 0 Runbook](docs/05-deployment/local-development.md)。

## 变更规则

1. 从短生命周期分支提交 Pull Request。
2. 业务行为变更必须包含测试。
3. API Contract 变更必须重新导出 `docs/api/openapi.json`。
4. 架构或边界变更必须更新文档；重大决策新增 ADR。
5. 不提交真实 Secret、无权素材、生成大文件或个人 Obsidian 工作区。
6. 不复制 Fashion-AI 源码；复用 Open PicsetAI 文件时更新 `THIRD_PARTY_NOTICES.md`。

## 本地质量门禁

```powershell
uv sync --locked --all-packages
uv run ruff format --check .
uv run ruff check .
uv run pytest
uv run python scripts\audit_python.py
uv run python scripts\export_openapi.py
pnpm install --frozen-lockfile
pnpm web:lint
pnpm web:typecheck
pnpm web:build
pnpm audit --audit-level=moderate
docker compose -f infra\compose\docker-compose.yml config --quiet
```

提交前还应运行：

```powershell
uv run python scripts\verify_phase0.py
```

## 代码边界

- `domain` 不依赖 FastAPI、Celery、数据库或供应商 SDK。
- Agent Node 不直接执行 SQL。
- Provider Adapter 不决定业务路由。
- React 组件不实现工作流状态机。
- Prompt、模型和 Tool Contract 必须版本化。
