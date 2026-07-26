# 本地开发与 Phase 0-2 Runbook

| 属性 | 值 |
|---|---|
| 状态 | verified |
| 最后更新 | 2026-07-24 |
| 适用版本 | Phase 0-2 / 0.1.0 |

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
$env:CV_MIGRATION_MYSQL_DSN="mysql+pymysql://root:root-change-me@127.0.0.1:13316/commercevision"
uv run alembic upgrade head
uv run alembic current
uv run alembic check
```

Compose 启动时 `migrate` 服务会在 API、Worker 和 Scheduler 之前执行 `alembic upgrade head`。
`mysql-permissions` 一次性服务会先把官方镜像默认授予的数据库级 `ALL` 收敛为
`SELECT/INSERT/UPDATE/DELETE`，并以真实 `CREATE TABLE` 拒绝探针验证运行时账号。
`migrate` 读取独立的 `CV_MIGRATION_MYSQL_DSN`；API、Worker 和 Scheduler 继续使用
`CV_MYSQL_DSN` 对应的 DML-only 账号。默认管理员 DSN 只适用于本地 Compose，生产环境必须
注入专用迁移身份，不能把 DDL 或 `SUPER` 权限授予运行时账号。MySQL 类型变更可能需要
表复制；生产环境必须按 [CI/CD 与发布](ci-cd-and-release.md) 的在线迁移或维护窗口要求执行。

## Direct Upload 对象存储

应用只使用 `QUARANTINE`、`TASK`、`FOUNDATION` 和 `PROVIDER_RESULT` 四个逻辑位置。Compose
的 `object-storage-init` 在 API 和 Worker 启动前幂等创建对应 Bucket 并启用 MinIO Versioning；
调用方不能传入 Bucket 或对象 Key。

API 通过 `CV_OBJECT_STORE_ENDPOINT=http://minio:9000` 执行服务端 I/O，但必须通过
`CV_OBJECT_STORE_PRESIGN_ENDPOINT=http://localhost:19000` 生成浏览器可访问的 URL。
生产部署应将后者设置为受 TLS 保护的对象存储上传域名。`CV_OBJECT_STORE_CORS_ORIGINS`
控制 MinIO PUT 来源，本地默认为 `http://localhost:13000`；生产环境必须列出实际 Web
Origin，不能依赖通配符。

关键配置：

```text
CV_OBJECT_STORE_BACKEND=minio|oss
CV_OBJECT_STORE_CREDENTIAL_MODE=static|ecs_ram_role|oidc_role_arn
CV_OBJECT_STORE_RAM_ROLE_NAME=commercevision-assets
CV_OBJECT_STORE_OIDC_ROLE_ARN=acs:ram::1234567890123456:role/commercevision-assets
CV_OBJECT_STORE_OIDC_PROVIDER_ARN=acs:ram::1234567890123456:oidc-provider/commercevision
CV_OBJECT_STORE_OIDC_TOKEN_FILE_PATH=/var/run/secrets/aliyun/oidc-token
CV_OBJECT_STORE_STS_ENDPOINT=sts-vpc.cn-hangzhou.aliyuncs.com
CV_OBJECT_STORE_QUARANTINE_BUCKET=quarantine-assets
CV_OBJECT_STORE_TASK_BUCKET=task-assets
CV_OBJECT_STORE_FOUNDATION_BUCKET=foundation-assets
CV_OBJECT_STORE_PROVIDER_RESULT_BUCKET=provider-results
CV_OBJECT_STORE_FORCE_PATH_STYLE=true|false
CV_OBJECT_STORE_REQUIRE_ENCRYPTION=true|false
CV_OBJECT_STORE_CONNECT_TIMEOUT_SECONDS=3
CV_OBJECT_STORE_READ_TIMEOUT_SECONDS=30
CV_OBJECT_STORE_READINESS_TIMEOUT_SECONDS=1
CV_OBJECT_STORE_CREDENTIAL_REFRESH_TIMEOUT_SECONDS=5
CV_UPLOAD_SESSION_EXPIRY_SECONDS=900
CV_UPLOAD_CLEANUP_PRESIGN_GRACE_SECONDS=30
CV_UPLOAD_CLEANUP_MAX_ATTEMPTS=600
CV_UPLOAD_CLEANUP_RECONCILE_INTERVAL_SECONDS=3600
CV_UPLOAD_CLEANUP_RECONCILE_HORIZON_SECONDS=259200
CV_UPLOAD_CLEANUP_RECONCILE_MAX_ATTEMPTS=80
CV_UPLOAD_FINALIZE_LEASE_SECONDS=120
CV_ASSET_VALIDATION_MAX_ATTEMPTS=5
CV_API_PROXY_TIMEOUT_MS=15000
```

旧部署只设置 `CV_OBJECT_STORE_BUCKET` 时，该值继续作为 Task Bucket；同时设置
`CV_OBJECT_STORE_TASK_BUCKET` 时以新配置为准。

生产环境要求对象存储 TLS、服务端加密、四个互不相同的物理 Bucket，以及可续期的工作负载
身份；静态 Access Key/Secret 会在配置加载时被拒绝。ECS RAM Role 只允许 IMDSv2，不回退
IMDSv1。ACK OIDC 必须以只读方式挂载投射 Token，并显式配置同 Region 的 STS 私网 DNS
Endpoint；STS Endpoint 只接受无 Scheme、路径、端口和凭证的 DNS Hostname。
日志、HTTP 响应和事件不得记录凭证、预签名 URL、Bucket、原始 Key 或文件字节。
生产配置还要求内部 SDK Endpoint 与浏览器 Presign Origin 使用不同 Origin，避免内部服务
地址被签入浏览器请求。MinIO 本地部署使用 path-style；阿里云 OSS 生产部署必须设置
`CV_OBJECT_STORE_FORCE_PATH_STYLE=false`，使用 virtual-hosted addressing。

API 和 Worker 就绪探针使用经过认证的对象存储 Adapter 检查每个唯一 Bucket 的可访问性和
Versioning；生产启用加密要求时还会通过专用 Versioning/Encryption API 检查服务端加密，
不要求宽权限的 `GetBucketInfo`。API 探针失败时进程保持存活，但
`/health/ready` 返回 503，编排器不得向该实例发送流量；Worker 主进程在创建消费者前执行
真实 `SELECT 1` 和对象存储探测，任一失败都不会启动消费者或写入 readiness 文件。Pool
子进程只构造本地 Runtime，不在 Celery 的 `worker_process_init` 四秒限制内执行网络探测。
Finalize 的最坏路径包含初始 HEAD、精确版本 HEAD 和受限 GET 三次存储请求，因此
`3 * (CREDENTIAL REFRESH + CONNECT + READ)` 超时预算必须严格小于
`CV_UPLOAD_FINALIZE_LEASE_SECONDS`，避免凭据 SDK 或串行 OSS 请求耗尽租约。静态身份不计
Credential Refresh。凭据 Provider 在进程内复用、串行刷新，卡死刷新由平台外层截止时间
终止调用方等待；超时后的并发调用复用同一个未完成请求，不能形成 STS 请求风暴。刷新使用
守护线程，进程关闭不会等待无法取消的 SDK 调用。就绪探针使用独立、较短的
`CV_OBJECT_STORE_READINESS_TIMEOUT_SECONDS`。
终止 Upload Session 后，已签发的 PUT URL 无法撤销。清理事件只会在 URL 到期并经过
`CV_UPLOAD_CLEANUP_PRESIGN_GRACE_SECONDS` 时钟偏差缓冲后变为可发布，避免迟到 PUT
与首次清理竞争。Scheduler 在 API 无流量时也会过期 OPEN 或遗留 FINALIZING 会话；首次
Cleanup Operation 首次删除后进入 `RECONCILING`，由 Operation Recovery 按
`CV_UPLOAD_CLEANUP_RECONCILE_INTERVAL_SECONDS` 驱动同一个 Operation 复核对象，最长持续
`CV_UPLOAD_CLEANUP_RECONCILE_HORIZON_SECONDS`。若发现首次 HEAD 后才完成的 PUT，Worker
仍使用原 Operation 精确删除；Scheduler 不访问对象存储，也不创建第二套业务重试权威。
`CV_UPLOAD_CLEANUP_MAX_ATTEMPTS` 按最短 jitter 延迟计算也必须覆盖完整执行预算；
`CV_UPLOAD_CLEANUP_RECONCILE_MAX_ATTEMPTS` 必须覆盖完整复核窗口，且全局
`CV_OPERATION_RECONCILIATION_MAX_ELAPSED_SECONDS` 必须大于该窗口。Cleanup Operation 的
执行截止时间从可发布时间继续计算完整的 `CV_OPERATION_RETRY_MAX_ELAPSED_SECONDS`，等待
URL 失效不会提前耗尽执行重试预算。

API 与 Worker 通过同一个 Compose Environment Anchor 获取对象存储后端、Endpoint、身份、
Bucket、TLS、加密和超时配置，并依赖 `object-storage-init` 成功完成后启动。API、Worker 与
Scheduler 通过另一共享 Anchor 获取 Cleanup 重协调参数，避免调度端与执行端边界不一致。
这保证 Maintenance Queue 上的到期和终止对象清理不会因 Worker 使用回退凭证或不同截止
时间而持续失败。Finalize 成功的对象保持在 Quarantine，由 Ticket 05 校验通过后提升，不属于
Upload Session Cleanup。Web BFF 的控制面 JSON 请求/响应分别硬限制为 1 MiB/2 MiB，并使用
`CV_API_PROXY_TIMEOUT_MS` 覆盖连接、响应头和响应体读取；图片字节不经过 BFF。

## Asset Validation

Compose Worker 消费 `commercevision.asset` 并注册 `ASSET_VALIDATION` built-in Executor。
四种支持的上传类型都在 Quarantine 内完成 local format、ClamAV 和适用的内容安全/C2PA
stage；全部通过后才执行受控目标复核与源清理。Compose 使用真实 ClamAV，本地 Alibaba 与
C2PA 使用 deterministic Adapter；production 配置会拒绝 deterministic Adapter。

```powershell
docker compose -f infra\compose\docker-compose.yml `
  -f infra\compose\docker-compose.clamav-test.yml up -d --wait clamav
docker compose -f infra\compose\docker-compose.yml `
  -f infra\compose\docker-compose.clamav-test.yml ps clamav
uv run pytest tests\integration\test_clamav_real.py -q
```

默认 Compose 不发布 ClamAV TCP 端口。显式测试 override 才固定绑定
`127.0.0.1:13310`，且不读取 `CV_BIND_HOST`。完整配置、readiness、stuck quarantine、
Provider throttling、Worker death、promotion recovery、指标和 DLQ 处置见
[Asset Validation Runbook](../runbooks/asset-validation.md)。

CI 中的 OSS Adapter Contract 使用确定性的 OSS SDK 协议替身，覆盖签名参数、版本 ID、
不透明 ETag、受限读取、条件复制/删除、临时读取和错误归一化，不需要或保存云凭证。由于
仓库没有可用的阿里云账号，Ticket 04 未执行真实 OSS 服务测试；生产上线前仍须在目标
Region、Bucket 版本化、CORS、SSE 与 RAM Policy 配置下运行同一 Contract，并把结果作为
部署证据保留。真实 MySQL + MinIO 是本地和 CI 的端到端对象存储门禁。

真实 OSS 门禁默认跳过，只有显式设置 `CV_TEST_OSS_LIVE=1` 才会访问云资源。它要求
`CV_OBJECT_STORE_BACKEND=oss`、相互独立的 HTTPS 内部/浏览器 Endpoint、四个不同 Bucket
及 `ecs_ram_role` 或 `oidc_role_arn` 可续期身份。OIDC 模式还必须提供 Role ARN、Provider
ARN、只读 Token 文件和显式 STS Endpoint。门禁拒绝静态凭据，并只在随机
`ticket04-live/` 前缀下创建和清理对象：

```text
uv run pytest tests/contract/test_object_storage_oss_live.py -m live_oss -q
```

该门禁验证真实签名 PUT、Provider Version ID、不透明 ETag、受限精确版本读取、匹配重放、
冲突目标拒绝、临时读取、条件删除和缺失对象错误归一化。没有该门禁的成功记录，不能把
离线 OSS Contract 当作目标账号的生产验证。

## 配置优先级

从高到低：

1. 显式构造参数。
2. `CV_` 环境变量。
3. `.env` / `.env.local`。
4. Secret file source。
5. `config/base.yaml` 非敏感默认值。

启动时由 Pydantic 校验类型和枚举，未知环境值会拒绝启动。Secret file 默认从容器内 `/run/secrets` 或项目本地 `secrets` 目录读取，也可通过 `CV_SECRETS_DIR` 指定；文件名使用完整 `CV_` 前缀，例如 `CV_OBJECT_STORE_SECRET_KEY`。Trusted Principal 轮换的 Current/Previous HMAC Secret 分别使用 `CV_TRUSTED_PRINCIPAL_CURRENT_HMAC_SECRET` 和 `CV_TRUSTED_PRINCIPAL_PREVIOUS_HMAC_SECRET`，Key ID 通过同名环境配置显式绑定；两个配置必须成对且 ID 不得相同。Web BFF 使用 Current Key 为 `CV_WEB_ALLOWED_WORKSPACE_IDS` 内的请求签发短期 Principal，并将 Actor 固定为 `CV_WEB_PRINCIPAL_ACTOR_ID`；本地 Compose 默认值只用于回环地址开发，生产必须由 Secret Manager 注入随机 Secret，并由真实身份会话决定 Workspace 成员关系。

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

### 浏览器 PUT 失败

确认上传 URL 使用浏览器可达的 `CV_OBJECT_STORE_PRESIGN_ENDPOINT`，而不是容器内
`minio:9000`；再检查 MinIO 的 `MINIO_API_CORS_ALLOW_ORIGIN` 是否包含当前 Web Origin。
不要让 Next.js Route 或 Control API 代理对象字节。

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
