# Asset Validation Runbook

| 属性 | 值 |
|---|---|
| 状态 | verified |
| 最后更新 | 2026-07-27 |
| 适用版本 | Phase 2 / Ticket 05 |

## 目标与不变量

直接上传的 IMAGE、LORA、PROMPT_TEMPLATE 和 MODEL_CONFIGURATION 字节只写入
Quarantine。MySQL Durable Operation 是校验重试、Lease、恢复和 DLQ 的唯一业务权威；
RabbitMQ/Celery 只负责传输。对象存储和 Provider I/O 不在 MySQL 事务内执行。

任何对象只有在全部适用 stage 通过、受控目标副本经 Version ID、ETag、长度和 SHA-256
复核、Quarantine 源精确版本已清理后，才能进入 `PENDING_RIGHTS`。校验通过不等于拥有使用
权，Rights 流程仍必须通过。`PENDING_REVIEW` 是可恢复人工门禁，不是 terminal rejection。

Append-only 表示 Validation Result 只允许 INSERT，不允许 UPDATE；保留期清理仍允许 DELETE。
数据库、API、Web、日志、traces 和 metrics 都不能保存或返回 Provider 原始响应、Secret、
签名 URL、对象 Key、文件字节或不受控 evidence。

外部内容安全校验是 ADR-006 的窄化 Rights 前例外。它只允许企业管理员发布的
Validation Data Transfer Policy 授权 `SECURITY_VALIDATION`，不授予检索、派生、创意
生成或其他模型使用权。策略默认拒绝，并精确绑定 Workspace、Asset Version、Asset Kind、
Retention Class、Provider 和 Endpoint Region。Workspace ID 大小写敏感，只 trim；
Provider 和 Region 按其 canonical contract 归一。Upload Session、Asset Version、
Operation input hash 和 evidence 都绑定策略 version+snapshot hash，Worker 每次签 URL
前用当前配置重验，因此撤销或漂移不会复用历史 PASS。deterministic/no-transfer Adapter
不要求外传授权。

## Stage 矩阵

| Stage | IMAGE | LORA | PROMPT_TEMPLATE | MODEL_CONFIGURATION |
|---|---|---|---|---|
| LOCAL_FORMAT | raster magic/MIME/完整解码/资源边界 | SafeTensors header-only | 严格 JSON schema | 严格 JSON schema |
| MALWARE | ClamAV INSTREAM | ClamAV INSTREAM | ClamAV INSTREAM | ClamAV INSTREAM |
| CONTENT_SAFETY | Alibaba 或 deterministic | NOT_APPLICABLE | NOT_APPLICABLE | NOT_APPLICABLE |
| PROVENANCE | C2PA 或 deterministic | NOT_APPLICABLE | NOT_APPLICABLE | NOT_APPLICABLE |
| PROMOTION | 受控目标复核和源清理 | 同左 | 同左 | 同左 |

LoRA 校验从不反序列化 tensor 数据。pickle、PyTorch checkpoint、archive、document、
executable、SVG、PSD 和未允许的媒体格式必须关闭式阻断。非图片 finalize 前
`detected_mime` 保持空值；真实检测格式只写入 allowlisted `LOCAL_FORMAT` evidence。

## 部署

Asset Worker 必须订阅 `commercevision.asset`，并在
`CV_WORKER_REQUIRED_OPERATION_KINDS` 中包含 `ASSET_VALIDATION`。Compose 同时订阅
Workflow、Asset 和 Maintenance；生产可拆成独立 Worker deployment 以单独扩缩容。

本地 Compose 使用按 digest 固定的 `clamav/clamav:1.5.3_base`，默认只在 Compose
内部网络提供 clamd，不发布主机端口；病毒库保存在 `clamav_data`。仅运行真实扫描集成测试
时叠加 `docker-compose.clamav-test.yml`，它把 clamd 固定绑定到
`127.0.0.1:13310`，不读取 `CV_BIND_HOST`。Alibaba 和 C2PA 在本地使用 deterministic
Adapter；这两个 Adapter 不能作为 production 配置。

Production Asset Worker 启动配置必须同时满足：

```text
CV_WORKER_QUEUES=["commercevision.asset"]
CV_WORKER_REQUIRED_OPERATION_KINDS=["ASSET_VALIDATION"]
CV_ASSET_RETENTION_CLEANUP_VERSION_PAGE_SIZE=100
CV_ASSET_RETENTION_CLEANUP_MAX_VERSION_PAGES=50
CV_ASSET_RETENTION_CLEANUP_MAX_VERSIONS=1000
CV_ASSET_RETENTION_CLEANUP_STABLE_EMPTY_PASSES=2
CV_ASSET_VALIDATION_JSON_MAXIMUM_DEPTH=32
CV_ASSET_VALIDATION_JSON_MAXIMUM_NODES=10000
CV_ASSET_MALWARE_ADAPTER=clamav
CV_CLAMAV_HOST=<internal-clamd-dns>
CV_CLAMAV_PORT=3310
CV_ASSET_CONTENT_SAFETY_ADAPTER=alibaba
CV_ALIBABA_CONTENT_SAFETY_ENDPOINT=<approved-region-endpoint>
CV_ALIBABA_CONTENT_SAFETY_ENDPOINT_REGION=<approved-region>
CV_ALIBABA_CONTENT_SAFETY_ALLOWED_URL_ORIGINS=["https://<controlled-read-origin>"]
CV_VALIDATION_DATA_TRANSFER_ENABLED=true
CV_VALIDATION_DATA_TRANSFER_POLICY_VERSION=<published-enterprise-policy-version>
CV_VALIDATION_DATA_TRANSFER_ALLOWED_WORKSPACE_IDS=["<binary-exact-workspace-id>"]
CV_VALIDATION_DATA_TRANSFER_ALLOWED_ASSET_KINDS=["IMAGE"]
CV_VALIDATION_DATA_TRANSFER_ALLOWED_RETENTION_CLASSES=["TASK","FOUNDATION"]
CV_VALIDATION_DATA_TRANSFER_ALLOWED_PROVIDERS=["alibaba-green"]
CV_VALIDATION_DATA_TRANSFER_ALLOWED_ENDPOINT_REGIONS=["<approved-region>"]
CV_VALIDATION_DATA_TRANSFER_ALLOWED_ENDPOINT_HOSTS=["<approved-region-endpoint>"]
CV_ASSET_PROVENANCE_ADAPTER=c2pa
CV_C2PA_TRUST_CONFIG_VERSION=<versioned-trust-config-id>
```

Alibaba Access Key ID/Secret、C2PA trust anchors PEM 和 EKU OID policy 必须来自 Secret
Manager 或只读 Secret 文件，不能写入 Compose、镜像或 Git。Alibaba 只允许受控 HTTPS
Origin，且 end-to-end deadline 必须大于 connect+read transport budget；Adapter 在 Future
完成后再次检查总截止时间。C2PA 禁止 remote manifest 和 OCSP fetch，必须启用 trust 与
timestamp verification，并把实际 trust-config SHA-256 写入 allowlisted evidence。
生产 Alibaba 缺少 enabled policy 或任一 allowlist 时 Worker 启动失败。变更策略必须发布
新 version；撤销时先更新服务端策略，随后观察
`asset_validation_stage_results_total{stage="CONTENT_SAFETY",verdict="TERMINAL_FAILURE"}`
和对应 normalized reason，不得手工重放旧 URL。

Workspace allowlist 使用二进制精确身份，不会修剪空格或折叠大小写。Alibaba endpoint 和
endpoint-host allowlist 只接受小写 ASCII 规范 DNS hostname，不接受 scheme、port、path、
通配符、IP literal 或尾随点；实际 `ContentSafetyAdapter` endpoint 必须精确命中 allowlist。
endpoint host 属于不可变 transfer policy snapshot 和 append-only evidence，配置漂移会在生成
临时读 URL 前以 `VALIDATION_TRANSFER_ENDPOINT_DENIED` 或 policy mismatch 关闭式失败。

SafeTensors Header、Prompt Template 和 Model Configuration 共享 JSON 结构复杂度上限。
解析递归失败、超过最大嵌套深度或节点数均归一为对应 `MALFORMED_*`，不得作为未分类 Worker
异常进入通用重试。上调限制前必须同时评估 Worker 内存、CPU 和上传字节上限。

ClamAV `StreamMaxLength`、`MaxFileSize` 和 `MaxScanSize` 必须覆盖所有允许的最大上传类型。
Worker 配置的 `CV_CLAMAV_STREAM_MAX_BYTES` 不能超过 daemon 限制，也不能小于最大的上传
上限。不要公开 clamd TCP 端口；协议本身不提供认证或 TLS。

## Readiness

Asset Worker 在创建 Consumer 前检查：

1. MySQL `SELECT 1`。
2. 所需对象存储 Bucket 的认证、Versioning 和加密策略。
3. ClamAV `PING`、`VERSIONCOMMANDS` 和 `INSTREAM` capability。
4. required Operation Kind 已注册 built-in Executor。

Readiness 文件中的 `malware_scanner` 必须为 `ok`，Consumer 和所有 prefork child 标记也必须
有效。ClamAV unavailable 时不得把结果降级为 clean，也不得通过移除 readiness 断言切流。
Alibaba 网络限流/超时在 stage 内归一化为 retryable failure；C2PA runtime/config 失败同样
关闭式失败。

本地检查：

```powershell
docker compose -f infra\compose\docker-compose.yml `
  -f infra\compose\docker-compose.clamav-test.yml up -d --wait clamav
docker compose -f infra\compose\docker-compose.yml `
  -f infra\compose\docker-compose.clamav-test.yml ps clamav
uv run pytest tests/integration/test_clamav_real.py -q
docker compose -f infra\compose\docker-compose.yml `
  -f infra\compose\docker-compose.clamav-test.yml stop clamav
```

## 控制面诊断

用户态只读取：

```text
GET /api/v1/assets/{asset_id}/validation
GET /api/v1/operations/{operation_id}
```

Validation endpoint 按 workspace 隔离；跨 workspace、畸形 ID 和不存在记录统一 404。它会
重新验证 Upload Session、Operation kind/target/input ref/input hash 和当前 source identity。
返回值只包含 normalized stage、verdict、reason、validator identity、allowlisted evidence
以及 Operation retry 分类，不含 provider payload 和存储身份字段。

必要时使用只读 SQL 确认控制面事实：

```sql
SELECT id, workspace_id, state, attempt_count, max_attempts,
       next_attempt_at, lease_expires_at, error_code, error_category
FROM durable_operations
WHERE kind = 'ASSET_VALIDATION' AND target_id = :asset_version_id;

SELECT attempt_number, stage, verdict, reason_code,
       validator_name, validator_version, policy_version, created_at
FROM asset_validation_results
WHERE workspace_id = :workspace_id AND asset_version_id = :asset_version_id
ORDER BY created_at, id;
```

不要直接修改 Asset、Operation、Object 或 Validation Result 来“修复”状态。先确认同一
Operation 是否仍有有效 Lease、`next_attempt_at` 是否到期、Outbox 是否待发布、Inbox 是否
已处理以及是否进入 DLQ。

## 故障处置

### ClamAV unavailable 或 timeout

1. 检查 clamd 容器/Pod health、病毒库更新时间、内存、连接数和 StreamMaxLength。
2. 运行真实 PING/clean/EICAR 门禁；EICAR 必须为 `INFECTED`。
3. 修复 scanner 后等待 Durable Operation 的 `next_attempt_at` 和 Operation Recovery。
4. 监控 retryable `MALWARE_SCANNER_UNAVAILABLE` / `MALWARE_SCAN_TIMEOUT` 是否下降。

不得临时切换 deterministic scanner、插入 PASS evidence 或直接提升对象。

### Alibaba throttling 或 outage

1. 按 normalized failure code、retryable 数和 stage latency 判断 429、timeout 或 5xx。
2. 检查 Region endpoint、配额、允许的 controlled read Origin 和临时 URL 有效期。
3. Durable Operation 接受 Provider `retry_after_seconds`；不要另建 Celery retry。
4. 只有在原 Operation terminal/DLQ 且根因已修复后，才通过 Operator API 重放 DLQ。

日志中只使用 normalized code、Provider request ID 和 latency，禁止输出 SDK response body。

### Stuck quarantine

1. 查询 Validation endpoint 与 Durable Operation，区分有效 Lease、scheduled retry、
   `PENDING_REVIEW`、terminal failure 和 DLQ。
2. 检查 `commercevision.asset` queue depth/oldest age、Consumer 数、Operation Recovery 和
   Outbox oldest unpublished age。
3. Lease 过期后由 Recovery 重新发布同一 Operation；不要创建替代 Operation。
4. 若 stage evidence 已存在，Worker 会先验证其 exact object/policy identity，再复用。

Recovery Scanner 自身把过期 `CLAIMED` 或耗尽的 `RECONCILING` Operation 转成 `FAILED`
时，会在同一事务写 Operation DLQ 和未发布的 `TERMINAL_FAILURE` Recovery Generation。
Asset Worker 消费该事件后，以幂等回调把仍处于 `QUARANTINED/VALIDATING` 的 Asset 转成
`FAILED`，保留精确 Quarantine 版本供管理员修复后 DLQ replay，并原子发布
`asset.validation.failed`。若回调事务失败，Recovery Generation 不会被消费，原事件必须
重投；不得手工把 Asset 状态改成 FAILED。

普通 Operation DLQ replay 也以目标终态回调为完成屏障：目标聚合和类型化事件未收敛前，
Replay Lifecycle 保持 `CLAIMED`，重投只重试回调，不再次调用 Provider。Operation 在首次
执行认领前已经超过执行截止时间时，失败事件的 `attempt_number` 为 `0`；它表示没有发生
Provider 执行，不得伪造 stage evidence 或改写为第一次尝试。

`PENDING_REVIEW` 必须显示“等待人工复核”，不能标成 BLOCKED。人工门禁未完成前不能手工
写入 PASS 或调用 promotion。

### Promotion 中断或 Worker death

重复投递会重新验证受控目标对象的 backend/location/bucket/key/version/ETag/size/SHA 和
source 状态。已存在的 `CONTROLLED_ORIGINAL` 或 PROMOTION result 只有全字段匹配才收敛为
成功；unique race 尚未完整可见时返回 retryable concurrency failure。

启用 Versioning 的 MinIO/S3/OSS 在并发 conditional copy 边界仍可能短暂可见两个相同内容
版本。MySQL 中先提交的 `CONTROLLED_ORIGINAL.provider_version_id` 是唯一胜者；后到执行器
必须在事务外重新读取并完整验证该精确版本，再按 Version ID + ETag 删除自己观察到的重复
版本，最后用数据库胜者重试短事务。仅当 Workspace、Asset Version、位置、Key、长度和
SHA-256 全部一致且两个 Version ID 均非空时允许此收敛；其他差异继续按篡改失败关闭。

让原 Operation 通过 Lease expiry/Recovery 继续。不要删除未知受控目标、覆盖目标 Key 或把
Quarantine source 标为 DELETED。目标或源 identity mismatch 是 terminal integrity failure，
Asset 会 BLOCK，并使用精确版本条件清理 Quarantine。

Task Asset 的 promotion 事务在最终提交前同时检查应用 UTC 时钟和 MySQL
`UTC_TIMESTAMP(6)`。任一时钟到达 `retention_deadline`，事务必须回滚，随后在事务外按精确
Version ID 补偿已复制对象并进入到期清理；不能把已过期对象提交为 `PENDING_RIGHTS`。

### Terminal rejection 与 cleanup

LOCAL_FORMAT、malware infected 或安全策略 BLOCK 会令 Asset 进入 `BLOCKED`。MySQL 保留
normalized reason 与 append-only evidence；Quarantine 精确版本进入 DELETE_PENDING 并
幂等清理。存储暂时 unavailable 时 Operation retry；条件不匹配时 terminal，不能无条件删除
latest object。

到期清理必须分页枚举 source 和 destination 的精确 Key，删除全部对象版本及 delete marker，
并在连续两个完整空扫描后才能把 MySQL Object/Asset 标记为 `DELETED`。分页游标、
页数和删除数量都有界；游标循环、缺失 Version ID、预算耗尽或存储不可用都保持
`DELETING/DELETE_PENDING` 并进入业务重试，不能提前宣告清理完成。以上四个
`CV_ASSET_RETENTION_CLEANUP_*` 参数按 Bucket 历史版本量调优；页预算必须不小于稳定空扫描
次数。

## Signals 与告警

OTel instruments：

- `commercevision.asset_validation.operations`
- `commercevision.asset_validation.completions`
- `commercevision.asset_validation.stage_runs`
- `commercevision.asset_validation.stage_results`
- `commercevision.asset_validation.operation.duration`
- `commercevision.asset_validation.stage.duration`
- `commercevision.asset_validation.quarantine.age`

Structured events：

- `asset_validation_started`
- `asset_validation_target_bound`
- `asset_validation_stage_result`
- `asset_validation_operation_completed`
- `asset_validation_lifecycle_completed`
- `asset_validation_failed`

Spans 使用 `commercevision.asset.validation` 及按 stage 命名的 child span。IDs、attempt、
stage、verdict、reason、adapter identity 和 retry classification 可以记录；原始异常消息、
evidence dict、hash、对象位置、URL、字节和 Secret 不得记录。

初始告警：

| 告警 | 条件 |
|---|---|
| ClamAV readiness | 任一 Asset Worker `malware_scanner != ok` |
| Queue lag | Asset oldest message age > 5 分钟 |
| Retry surge | 任一 retryable reason 5 分钟持续增长 |
| Quarantine age | P95 超过 Operation 最大 elapsed budget |
| Terminal block surge | 同 stage/reason 相对基线显著上升 |
| DLQ | 任一新增 ASSET_VALIDATION dead letter |
| Promotion | PROMOTION retry/terminal failure 任一持续出现 |
| Retention cleanup | 版本枚举游标异常、预算耗尽或 `DELETE_PENDING` 年龄超过清理 SLO |

## 发布门禁

```powershell
uv run pytest tests/contract/test_malware_scanner_adapters.py `
  tests/contract/test_content_safety_adapters.py `
  tests/contract/test_provenance_adapters.py -q
uv run pytest tests/integration/test_clamav_real.py `
  tests/integration/test_upload_sessions_mysql_minio.py -q
uv run pytest tests/integration/test_asset_validation_migration_mysql.py `
  tests/integration/test_asset_validation_results_mysql.py -q
docker compose -f infra/compose/docker-compose.yml config --quiet
```

真实 Alibaba OSS 门禁需要目标账号凭证并默认跳过；详见
[本地开发与 Phase 0-2 Runbook](../05-deployment/local-development.md)。Production 发布还
必须验证目标 Region 的 Alibaba moderation、真实 C2PA trust bundle 和 timestamp chain。
迁移先执行 `alembic upgrade head` 和 `alembic check`，再部署 Worker，最后切入 Asset queue。
已有 Validation Result 的生产库不得通过 downgrade 回滚；应用回滚必须保持 schema forward
compatible。
