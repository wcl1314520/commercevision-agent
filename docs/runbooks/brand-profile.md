# Brand Profile 发布与失效收敛 Runbook

| 属性 | 值 |
|---|---|
| 状态 | implementation complete；本地发布验证完成；远程 CI 为最终放行门禁 |
| 最后更新 | 2026-07-31 |
| 适用版本 | Phase 2 / Ticket 08 |

## 权威与不变量

MySQL 是 Brand Profile identity、可变草稿、current publication、不可变版本、不可变 member、
当前 Asset/Rights authority、Idempotency、Audit、Outbox 和 Inbox 的唯一权威。Web
`sessionStorage`、RabbitMQ delivery、缓存和后续向量索引都不能发布 Profile 或授权 Asset。

一个 Brand Profile 由三类事实组成：

- `brand_profiles` 是可变 identity/head；`workspace_id + brand + profile_key` 二进制精确唯一，
  `version` 是所有草稿和 head 变更共用的乐观锁。
- `brand_profile_versions` 是 append-only publication。每次发布冻结完整草稿、
  `content_sha256`、publisher 和 MySQL 发布时间，旧行不可更新或删除。
- `brand_profile_members` 按 ordinal 冻结精确 Asset、Asset Version、角色以及发布时使用的
  Rights Record ID/version。member 是审计证据，不是持续授权。

状态流为：

```text
create -> DRAFT
DRAFT|ACTIVE|NEEDS_REPUBLISH -- publish --> ACTIVE
ACTIVE -- live authority no longer matches --> NEEDS_REPUBLISH
NEEDS_REPUBLISH -- corrected draft + live validation + publish --> ACTIVE
any non-archived state -- archive domain transition --> ARCHIVED
```

当前 HTTP 管理面提供 create、draft update、validate、publish 和 read，不提供 archive
endpoint；不得用直接 SQL 模拟缺失的管理命令。普通运行时连接不能删除 identity、version 或
member。迁移 Trigger 和 `ON DELETE RESTRICT` 组合保护整个历史；不要为“修复”而关闭 Trigger。

草稿支持最多 64 条规则、32 个批准颜色、每类最多 64 个文本约束和最多 64 个 selected
assets。规则 code、颜色 name 和 Asset Version 必须各自唯一；颜色使用 canonical uppercase
hex。选中 member 必须是当前 Workspace 中的 Foundation Asset Version。用途、Provider 和
是否需要派生许可是发布授权判断的一部分，不能在发布后由调用方覆盖。

## 发布事务

所有 mutation 都要求签名 Trusted Principal 中的 Workspace 管理员。创建、更新草稿和发布在
发送前必须持久化一个唯一 `Idempotency-Key`；更新、校验和发布同时携带当前
`expected_version`。

发布使用一个短 MySQL 事务，固定执行：

1. `FOR UPDATE` 读取 Brand Profile head，并验证 `expected_version`。
2. 解析全部 selected Asset Version，按稳定 Asset ID 顺序锁定 Asset/current Rights
   authority；Rights 写路径也先锁 Asset，因此发布与替换、撤销、到期和删除线性化。
3. 取得锁后读取一次 `UTC_TIMESTAMP(6)`，以该数据库时间判断 Asset 是否
   `AVAILABLE`、是否仍以所选版本为 current、Retention Class 是否为 `FOUNDATION`、Rights
   是否为 current GRANT、用途/Provider 是否允许、派生许可与 validity window 是否满足。
4. 任一 member 失败则整个发布失败，不追加部分 version/member，也不推进 head。
5. 全部通过后追加 version/member，CAS 推进 current head，写 Audit、Idempotency 结果和
   `brand-profile.published` Outbox observation，再一次提交。

每次重新发布都产生新 version number 和新 content hash，即使规则文本相同也重新冻结当前
精确 Rights identity。发布后 `state=ACTIVE` 且 `stale_at=NULL`。不得通过复制旧 version、
重指 current pointer 或手工改 hash 来“快速恢复”。

`:validate` 执行同一套实时 authority 检查但不持久化 publication。校验结果绑定
`profile_id + profile_version + decided_at`；草稿、Profile version 或当前 authority 任一变化
后都必须重新校验。Web 中绿色结果只是一次数据库快照，不能跨修改或长时间缓存。

常见校验 reason code：

| reason | 含义与动作 |
|---|---|
| `ASSET_VERSION_NOT_FOUND` | 版本不存在或不属于当前 Workspace；移除错误引用 |
| `NOT_FOUNDATION_ASSET` | 选中了 Task Asset；改用合法 Foundation Asset |
| `ASSET_VERSION_NOT_CURRENT` | Asset 已有新 current version；重新选择并审阅 |
| `ASSET_NOT_AVAILABLE` / `ASSET_BLOCKED` | Asset 生命周期不允许使用；先处理 Asset 根因 |
| `NO_CURRENT_RIGHTS` / `RIGHTS_REVOKED` | 没有当前 GRANT；不得发布 |
| `RIGHTS_NOT_YET_VALID` / `RIGHTS_EXPIRED` | 当前数据库时间不在授权窗口内 |
| `RIGHTS_ASSET_VERSION_MISMATCH` | Rights 未覆盖所选版本 |
| `USE_NOT_ALLOWED` / `PROVIDER_NOT_ALLOWED` | 草稿用途或 Provider 不在精确许可集合 |
| `DERIVATIVE_NOT_ALLOWED` | 草稿要求派生，但当前 Rights 不允许 |

`422 BRAND_PROFILE_PUBLICATION_REJECTED` 的 `details.issues[]` 可直接定位 member；这是确定性
policy/validation 拒绝，不应自动重试。`409 VERSION_CONFLICT` 要求先读取新 head，并由操作人
决定如何处理本地草稿。

## 历史读取与 current usability

`GET .../versions` 和 `GET .../versions/{versionNumber}` 永远返回发布时的原始规则和精确
member facts。每次读取同时在一个数据库一致性边界内取得当前 Asset/current Rights 快照和
锁后 `UTC_TIMESTAMP(6)`，按 publication 的用途、Provider 和派生要求重新计算：

- `currently_usable`
- `current_reason_code`
- `current_rights_record_id/version`
- `decided_at`

发布时 `published_rights_record_id/version` 永不改变；current Rights identity 可以不同或
为空。即使 Profile state 尚因消息延迟保持 `ACTIVE`，当前不可用 member 也必须立即返回
`currently_usable=false`。反过来，历史 member 一次返回 true 也不能授权后续检索或 Provider
调用；实际使用前仍须在 MySQL 重新判断 current usability。

Profile version history 以 version number keyset cursor 分页；Profile identity 列表以
`created_at + id` 分页。客户端不得推导或修改 cursor，也不得把未加载的旧页当作不存在。
服务端只签发 `v1.<key-id>.<payload>.<signature>` cursor：Profile 列表 token 绑定精确
Workspace 与 Brand filter，Version token 绑定精确 Workspace 与 Profile，并同时绑定 cursor
kind、排序 schema、签发时间和 keyset boundary。签名与 scope key 由 trusted-principal
current/previous 根密钥经不同域派生，不直接复用认证签名 key；旧 unsigned token、篡改、
跨查询复用、过期和 future-skew token 全部统一返回无 token 细节的 `400 INVALID_ARGUMENT`，
且在打开 UoW 或查询 Repository 前失败关闭。

`CV_BRAND_PROFILE_CURSOR_MAX_AGE_SECONDS` 默认 86400（允许 60–604800），
`CV_BRAND_PROFILE_CURSOR_FUTURE_SKEW_SECONDS` 默认 30（允许 0–300）。Compose 会把
current/previous key ring、principal TTL/skew 与 cursor TTL/skew 完整传入 API；生产
Control API 缺少 current key ID 或 current secret 时会在组合根、创建数据库和对象存储资源
之前拒绝启动，因此实例不能先进入 ready 再在首个 cursor 请求失败。Worker、Scheduler 等不
接受 Trusted Principal 入站请求的进程不承担这个 API 专属启动门禁。生产环境不得使用
Compose 的本地默认 secret 作为 current 或 previous；任一仍在验证环中的 key 都具有完整
管理员认证权限，必须满足相同的生产安全校验。

### 密钥环无中断轮换

Trusted Principal 和 Brand Profile cursor 使用同一 current/previous 根密钥环，但 cursor
签名与 query scope 使用独立域派生密钥。不得直接把 current 从旧密钥替换为新密钥，也不得在
确认过期窗口前删除旧 secret。固定执行以下两阶段：

1. **分发阶段（旧 current + 新 previous）**：保持 Web gateway 继续用旧 current 签发，
   把全部 API 实例滚动部署为 `old=current, new=previous`。等待每个实例 ready，并以不记录
   secret 的受控新 key signed canary 验证新 key ID/secret 已正确分发到每个实例。此时 API
   仍签发旧 cursor，但已能验证新、旧两种签名。
2. **切换阶段（新 current + 旧 previous）**：把全部 API 实例滚动切换为
   `new=current, old=previous`，并在同一变更窗口把 Web gateway signer 切到新 current。
   混合版本窗口中的两类 API 都持有两把密钥，因此旧 principal、旧 cursor、新 principal 和
   新 cursor 均可验证。确认所有 API ready、Web 已只签发新 key，并记录最后一个仍可能签发
   旧 key 的实例退出时间。

从该“最后旧签名时间”起，至少等待：

```text
max(trusted-principal TTL, cursor TTL)
+ max(trusted-principal future skew, cursor future skew)
+ 本次发布的最长滚动部署/配置传播裕量
```

窗口结束且指标中不再出现旧 key ID 后，才可从所有 API 移除 old previous；随后按密钥管理
制度销毁或封存旧 secret。移除过程中继续逐实例检查 startup、readiness、认证请求和 cursor
翻页，任何实例都不得配置 previous 而缺少 current。

分发阶段回滚时，所有实例恢复为旧 current，并在确认没有组件曾以新 key 签发后移除新
previous。切换阶段或等待窗口内回滚时，先把 API 恢复为
`old=current, new=previous`，再把 Web signer 恢复为旧 current；必须继续保留 new previous
直至“最后新签名时间 + 上述完整等待窗口”，否则切换期间已签发的新 principal/cursor 会被
提前拒绝。旧 key 已移除或因泄露而吊销后禁止自动回滚到旧 key；此时执行向前轮换，泄露场景
按安全事件处理并接受主动失效，而不是为了可用性重新启用已吊销密钥。

## Rights、到期与删除收敛

Worker 消费以下 observation，并通过 Inbox 保证重复投递幂等：

- `asset.rights.changed` v1：登记、替换、撤销或管理员阻断后重验；
- `asset.rights.expired` v1：Scheduler 以数据库时间完成到期转换后重验；
- `asset.delete.completed` v1：Maintenance Queue 上的 forward-compatible typed
  observation，要求 Workspace、Asset ID、精确 Asset Version ID、
  `retention_class=FOUNDATION` 和正整数 `deletion_generation`，完成后重验。

消费者先校验 Outbox Workspace、`aggregate_type=Asset`、aggregate Asset ID 和 typed payload
identity；删除完成事件还必须满足 Envelope
`aggregate_version == deletion_generation`，且 Asset/Asset Version ID 必须是 canonical
lowercase UUID。删除失效事务不会凭 payload 自证：它在同一 UoW 内按既定顺序锁定 current
Profile heads 和保留的 Asset tombstone row，只有 row 仍为 `FOUNDATION/DELETED`、其
`current_version_id` 等于 payload 的精确 Asset Version、Asset aggregate `version` 等于
`deletion_generation` 时才强制失效该精确成员。同代次但 identity/status/retention 错配会
永久失败关闭；row 已进入更高 generation（包括后来建立的新 current Asset Version）则是
幂等 stale no-op，旧事件不能污染新 authority。事件的 `occurred_at` 是因果/审计证据，不是
授权时间，也不能决定是否跳过新 publication。失效事务按稳定顺序锁定引用目标的 current
Profile heads，再锁定 Asset/current Rights authority，最后读取一次 MySQL
`UTC_TIMESTAMP(6)`。只有以下条件同时成立才把 `ACTIVE` head CAS 为
`NEEDS_REPUBLISH`：

1. current publication 仍是所检查的精确 version；
2. current publication 仍引用该 Asset；
3. live Asset/Rights facts 不再满足 publication 冻结的精确 authority。

`stale_at`、Profile `updated_at` 和新的 optimistic version 使用锁后的数据库时间。Rights
replacement 即使权限文字等价，也改变了 publication 冻结的精确 Rights identity，因此要求
重新发布。旧事件、重复事件、已被新 publication 取代的事件、已归档 Profile，以及重验后
authority 仍满足的事件均为 no-op；不得因事件较晚送达而让新 publication 变 stale。

失效消息延迟不扩大授权：Asset/Rights 表会先关闭可用性，历史读取和后续检索都直接读取 live
authority。`NEEDS_REPUBLISH` 是运营收敛信号，不是最终安全闸。

## HTTP 与 Web 操作

```text
POST   /api/v1/brand-profiles
GET    /api/v1/brand-profiles?brand=&limit=&cursor=
GET    /api/v1/brand-profiles/{profile_id}
PUT    /api/v1/brand-profiles/{profile_id}/draft
POST   /api/v1/brand-profiles/{profile_id}:validate
POST   /api/v1/brand-profiles/{profile_id}:publish
GET    /api/v1/brand-profiles/{profile_id}/versions?limit=&cursor=
GET    /api/v1/brand-profiles/{profile_id}/versions/{version_number}
```

读取要求 Workspace member；所有草稿、校验与发布操作要求 Workspace administrator。
`X-Workspace-Id` 不承担认证，`X-Actor-Id` 必须与签名 principal 完全一致。企业入口必须
计算真实成员/管理员关系；浏览器自报 Header 不能转换成权限。

Web 写入在请求发出前把 action、Workspace、brand、Profile ID/key、expected profile/version
基线、完整 payload、payload SHA-256、创建时间和幂等键写入当前标签页
`sessionStorage`。存储不可用时写操作失败关闭。网络中断、取消、`408`、`429`、可重试错误或
`5xx` 不能证明结果，页面保留原命令，刷新后先 GET 对账；只有服务端仍在原基线时才以同一
payload/key 重放。不得生成新 key 盲目重试发布。

恢复记录以 `workspace_id + brand` 隔离，并继续验证 Profile ID/key、expected versions 和
payload hash。切换 Workspace/brand 会 abort 旧请求并更换 generation；迟到响应不得恢复旧
投影。本地草稿未保存时，刷新保持 dirty draft；切换 Profile 要求操作人显式确认丢弃。
Version conflict 保留本地草稿并展示 authoritative head，不能自动 merge。权限 capability
读取失败时，Profile/history 仍可读，但写入和校验全部关闭。

## 部署与迁移

Brand Profile 迁移 revision 为 `b8e1d4f7a203`，前置 revision 为 `a4c8e7f3b219`。升级会：

1. 为 Rights Record 增加精确 version 复合唯一键；
2. 创建三张 Brand Profile 表、约束与查询/失效索引；
3. 后置增加 current-head 循环外键；
4. 创建 identity delete 栅栏及 version/member update/delete 栅栏。

迁移 downgrade 先统计三表行数；任一行存在即抛错，不删除历史。计划回退前只能在从未写入
Brand Profile 的新环境验证 downgrade；已有生产数据的环境必须采用向前修复。

Worker 必须消费 Asset Queue 上的 Rights observations 和 Maintenance Queue 上的 Asset
delete completion，并保持 MySQL、RabbitMQ 与 Inbox/DLQ 健康。Outbox publisher 必须运行，
否则 publication 本身仍会原子成功，但 observation 不会离开 MySQL。发布部署后至少检查：

1. API 与 Scheduler readiness 均为 200，Worker readiness 新鲜且订阅所需队列；Compose
   部署必须用同一 `CV_RABBITMQ_PASSWORD` 派生 RabbitMQ、API、Worker 与 Scheduler 的连接，
   不能只覆盖一个进程的 `CV_RABBITMQ_URL`；
2. 创建/校验/发布一条受控 Profile 后，Outbox `brand-profile.published` 已投递；
3. 替换测试 Rights 后，旧 head 收敛为 `NEEDS_REPUBLISH`；
4. 同一 Rights event 重放不再次增加 Profile version；
5. 历史读取在替换后立即返回 `currently_usable=false`；
6. migration upgrade、empty downgrade/re-upgrade、`alembic check` 和运行时 Trigger 权限门禁
   全部通过。

## 诊断

先用公开 GET 接口确认 Profile head 与 version history，再使用只读 SQL：

```sql
SELECT id, workspace_id, brand, profile_key, state,
       current_version_id, current_version_number, version, stale_at,
       draft_sha256, created_by, created_at, updated_by, updated_at
FROM brand_profiles
WHERE workspace_id = :workspace_id AND id = :profile_id;

SELECT id, version_number, content_sha256, purpose, provider,
       requires_derivative, published_by, published_at
FROM brand_profile_versions
WHERE workspace_id = :workspace_id AND profile_id = :profile_id
ORDER BY version_number DESC;

SELECT profile_version_id, profile_version_number, ordinal, role,
       asset_id, asset_version_id, rights_record_id, rights_record_version
FROM brand_profile_members
WHERE workspace_id = :workspace_id AND profile_id = :profile_id
ORDER BY profile_version_number DESC, ordinal;

SELECT id, status, retention_class, current_version_id,
       current_rights_record_id, version, updated_at
FROM assets
WHERE workspace_id = :workspace_id AND id = :asset_id;

SELECT id, asset_id, asset_version_id, version_number, decision,
       derivative_allowed, valid_from, valid_until, perpetual,
       permissions_sealed_at, created_at
FROM rights_records
WHERE workspace_id = :workspace_id AND asset_id = :asset_id
ORDER BY version_number DESC;

SELECT id, event_type, schema_version, aggregate_type, aggregate_id,
       aggregate_version, occurred_at, available_at, published_at,
       publish_attempts, last_error
FROM outbox_events
WHERE workspace_id = :workspace_id
  AND aggregate_type IN ('BrandProfile', 'Asset')
  AND aggregate_id IN (:profile_id, :asset_id)
ORDER BY occurred_at DESC, id DESC;
```

### Profile 保持 `ACTIVE`，但 member 已不可用

1. 先调用不可变 version GET；若 `currently_usable=false`，安全边界已经关闭，不要手工改
   history。
2. 找到对应 Rights/delete Outbox event，检查 `published_at`、Worker queue 和 Inbox/DLQ。
3. 修复消息依赖后通过原 event/DLQ replay 收敛；消费者会重新读取 live authority。
4. 若事件已处理但 state 未变化，核对 current publication 是否仍引用该 Asset；新
   publication 或已恢复 authority 产生 no-op 是正确行为。

### Profile 意外进入 `NEEDS_REPUBLISH`

核对 current member 的 published Rights identity 与 Asset current Rights identity。
Rights replacement 是新法律事实，即使许可集合相同也必须重新校验并发布。不要把
`state`/`stale_at` 直接改回 `ACTIVE`；修正草稿或 Rights 后执行 validate，再创建新
publication。

### 发布请求超时或浏览器刷新

不要更换幂等键。确认当前标签页 `sessionStorage` 中仍有 pending command，让 Web 先按
Profile key/ID、expected versions 和 payload hash 对账。命令已成功时只清除本地记录；head
仍在原基线时才可用原 key 重放。若服务端版本已前进，转入人工 version-conflict 处理。

### `VERSION_CONFLICT`

读取最新 Profile 与 current publication，保留原始本地草稿供人工比较。禁止客户端自动提升
`expected_version`、服务器静默 merge，或直接改数据库 version。只有操作人确认新基线后，
才能以新 mutation 和新幂等键提交。

### 迁移或 Trigger 失败

若 downgrade 报三表非空，这是数据保护门禁，不是可忽略错误。不要 truncate 表或 drop
Trigger。保留数据库快照，停止相关部署，确认目标 revision 与迁移链；已有历史只能向前修复。

## 发布门禁

```powershell
$env:UV_CACHE_DIR = 'D:\个人项目\电商生图agent\.codex-uv-cache'
uv run pytest -p no:cacheprovider `
  tests/unit/test_brand_profile_domain.py `
  tests/unit/test_brand_profile_contracts.py `
  tests/unit/test_brand_profile_application.py `
  tests/unit/test_brand_profile_repository_ports.py `
  tests/unit/test_brand_profile_event_contracts.py `
  tests/unit/test_brand_profile_worker_invalidation.py -q
uv run pytest -p no:cacheprovider `
  tests/contract/test_brand_profile_openapi.py -q
uv run pytest -p no:cacheprovider `
  tests/integration/test_brand_profile_mysql.py `
  tests/integration/test_brand_profile_migration_mysql.py -q
pnpm --filter @commercevision/web test:unit
pnpm web:proxy-test
pnpm web:typecheck
pnpm web:lint
pnpm web:api-types:check
pnpm --filter @commercevision/web build
pnpm --filter @commercevision/web e2e e2e/brand-profile-workbench.spec.ts
uv run alembic upgrade head
uv run alembic check
docker compose -f infra/compose/docker-compose.yml config --quiet
```

Focused 通过后仍须执行 Ticket 08 的完整 Python unit/contract/integration 分段门禁、全量 Web
Playwright、安全审计、Compose rebuild/health/log 检查与独立终审；不能用本节命令替代完整
发布门禁。
