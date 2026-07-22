# 本地开发与 Phase 0-1 Runbook

| 属性 | 值 |
|---|---|
| 状态 | verified |
| 最后更新 | 2026-07-22 |
| 适用版本 | Phase 0-1 / 0.1.0 |

## 定位

本地 Docker Compose 用于开发、集成验证和演示工程边界。它不是生产高可用拓扑，不提供多可用区、托管数据库、备份恢复或 99.95% SLO 承诺。

生产目标见 [部署拓扑](deployment-topology.md) 和 [可靠性、安全与数据治理](../04-engineering/reliability-security-and-governance.md)。

## 前提

- Docker Desktop 或 Docker Engine + Compose v2。
- 建议 Docker 至少分配 8 GB 内存。
- Python 3.13 和 `uv`，用于主机侧验收。
- 首次构建需要访问 Docker Registry、Python 和 npm 包源。

本项目使用独立主机端口，避免与其他本地项目的 MySQL、Redis 兼容缓存和 Web 服务冲突。Compose 默认只绑定 `127.0.0.1`，需要跨主机访问时必须显式设置 `CV_BIND_HOST` 并自行配置防火墙。

Compose 项目名固定为 `commercevision`，避免因目录名 `compose` 与其他仓库共享容器、网络或卷命名空间。

## 一条命令启动

PowerShell：

```powershell
.\scripts\dev.ps1 up
```

Bash：

```bash
./scripts/dev.sh up
```

脚本执行：

1. 构建本地应用镜像。
2. 启动基础设施。
3. 等待 Docker healthcheck 全部通过。
4. 运行 `scripts/verify_phase0.py` 的主机侧 HTTP/TCP 验收。

## 服务入口

| 服务 | 地址 | 健康语义 |
|---|---|---|
| Web | `http://localhost:13000` | Next.js 可响应 |
| Control API | `http://localhost:18000` | `/health/ready` 检查全部必要依赖 |
| API Docs | `http://localhost:18000/api/v1/docs` | OpenAPI UI |
| MCP Server | `http://localhost:18001/health/live` | MCP HTTP 进程可响应 |
| Scheduler | `http://localhost:18002/health/live` | Scheduler event loop 可响应 |
| RabbitMQ UI | `http://localhost:25672` | 凭证来自环境配置 |
| MinIO Console | `http://localhost:19001` | 凭证来自环境配置 |
| Milvus Health | `http://localhost:19091/healthz` | Milvus Standalone ready |
| OTel Health | `http://localhost:14319` | Collector ready |

数据端口：

| 组件 | 主机端口 |
|---|---:|
| MySQL | 13316 |
| Valkey（Redis 协议兼容） | 16379 |
| RabbitMQ AMQP | 15673 |
| MinIO API | 19000 |
| Milvus | 19531 |
| OTLP gRPC | 14317 |
| OTLP HTTP | 14318 |

端口和本地凭证可通过 `.env` 覆盖，字段见 `.env.example`。示例凭证只允许本地开发，不能用于 Demo、staging 或 production。

大陆网络环境如果访问 PyPI 不稳定，可以仅在 `.env` 中将 `CV_PYPI_INDEX_URL` 改为企业批准的 PyPI 镜像；仓库默认值仍是官方 PyPI，CI 不依赖地域镜像。

## 常用命令

```powershell
.\scripts\dev.ps1 status
.\scripts\dev.ps1 logs
uv run python scripts\verify_phase0.py
.\scripts\dev.ps1 down
```

仅校验 Compose：

```powershell
docker compose -f infra\compose\docker-compose.yml config --quiet
```

查看指定服务：

```powershell
docker compose -f infra\compose\docker-compose.yml logs --tail 200 api worker
```

数据库迁移和漂移检查：

```powershell
$env:CV_MYSQL_DSN="mysql+pymysql://commercevision:commercevision@127.0.0.1:13316/commercevision"
uv run alembic upgrade head
uv run alembic current
uv run alembic check
```

Compose 启动时 `migrate` 服务会在 API、Worker 和 Scheduler 之前执行 `alembic upgrade head`。MySQL 类型变更可能需要表复制；生产环境必须按 [CI/CD 与发布](ci-cd-and-release.md) 的在线迁移或维护窗口要求执行。

## 配置优先级

从高到低：

1. 显式构造参数。
2. `CV_` 环境变量。
3. `.env` / `.env.local`。
4. Secret file source。
5. `config/base.yaml` 非敏感默认值。

启动时由 Pydantic 校验类型和枚举，未知环境值会拒绝启动。Secret file 默认从容器内 `/run/secrets` 或项目本地 `secrets` 目录读取，也可通过 `CV_SECRETS_DIR` 指定；文件名使用完整 `CV_` 前缀，例如 `CV_OBJECT_STORE_SECRET_KEY`。

## 故障排查

### Registry 超时

先单独拉取报错的基础镜像，再重新运行启动脚本。不要通过关闭 TLS 校验规避。

### Milvus 启动失败

检查日志是否包含 MinIO Access Key 错误。Milvus 与 MinIO 必须使用同一组 `CV_OBJECT_STORE_ACCESS_KEY` 和 `CV_OBJECT_STORE_SECRET_KEY`。

```powershell
docker compose -f infra\compose\docker-compose.yml logs --tail 200 milvus minio
```

### API readiness 降级

```powershell
Invoke-RestMethod http://localhost:18000/health/ready | ConvertTo-Json -Depth 4
```

返回结果会分别标识 MySQL、Redis 兼容缓存、RabbitMQ、MinIO 和 Milvus。

### 从 Redis 7.4 本地卷迁移

Valkey 8.1 不能读取 Redis 7.4 生成的 RDB v12。缓存不是事实数据，当前 Compose 使用新的 `cache_data` 卷；旧版开发环境的 `redis_data` 卷不会被自动删除。确认其中没有需要人工分析的本地调试数据后，再单独清理旧卷。

### 重置本地数据

以下命令会永久删除本项目 Compose Volume 中的本地数据：

```powershell
docker compose -f infra\compose\docker-compose.yml down --volumes
```

执行前确认 Compose project 为当前 `mine` 项目，不要对其他项目运行。

## Phase 0 验收证据

2026-07-21 已验证：

- 12 个 Compose 服务全部为 `healthy`。
- 主机侧 8 个 HTTP 和 3 个 TCP 检查通过。
- API readiness 的 5 个外部依赖全部为 `ok`。
- Python 应用容器以 UID/GID 10001 非 root 运行。
- Worker 以 2 个 prefork 进程启动并连接 RabbitMQ。
- API 容器可连接 OTel gRPC 4317 和 HTTP 4318。
- Python 12 项单元测试、pnpm audit 和 pip-audit 通过，当前锁文件无已知漏洞。
- Compose 项目名为 `commercevision`，全部发布端口默认只绑定 `127.0.0.1`。
- Web standalone 镜像为 311 MB，Python/Web/OTel 容器均以非 root 用户运行。

## Phase 1 验收证据

2026-07-22 已验证：

- 11 张 MySQL 运行时表和 33 个 `DATETIME(6)` 时间列与 ORM 元数据一致。
- Alembic 可识别 MySQL fractional-second precision 漂移，旧 head、新 head、downgrade/upgrade 和空库建库链路均通过。
- 35 项 pytest 全部通过，覆盖状态机、Checkpoint、Outbox、Inbox、Lease、Retry、DLQ、并发认领和人工恢复。
- 完整 HTTP 流程经过两次人工审批后达到 `COMPLETED`，仅产生一个有效 Tool Attempt。
- 两次在人工等待点停止 Worker，审批在 Worker 离线期间提交；新 Worker 启动后均从 MySQL Checkpoint 恢复并完成任务。
- 更新后的 `migrate`、API、Worker 和 Scheduler 镜像已部署，12 个 Compose 服务全部 healthy，主机侧 8 个 HTTP 和 3 个 TCP 验收通过。
