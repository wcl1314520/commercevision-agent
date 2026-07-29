# ProductBrief Vision 与人工确认 Runbook

| 属性 | 值 |
|---|---|
| 状态 | verified |
| 最后更新 | 2026-07-29 |
| 适用版本 | Phase 2 / Ticket 07 |

## 权威与不变量

MySQL 是 ProductBrief、不可变版本、字段证据、Provider 调用事实、人工确认、Durable
Operation 和 Workflow 状态的唯一权威。RabbitMQ/Celery 只传递 Outbox 事件；浏览器轮询和
本地缓存不能代表执行状态。

Vision 只接受服务端解析出的内部 IMAGE Asset Version。请求创建、临时读引用签发以及
Provider 传输前都必须重新检查当前 Asset、精确版本、受控对象、Rights Record、用途
`VISION_ANALYSIS`、Provider 和保留期。Vision Data Transfer Policy 默认拒绝，并精确绑定
Workspace、Retention Class、Provider、Endpoint Region 和 Endpoint Host。Workspace ID
二进制精确匹配；大小写折叠、通配符和近似 endpoint 都不能授权。

每次模型分析或人工编辑都创建新版本。旧版本、字段和 evidence 不允许更新。确认只能指向
一个精确 ProductBrief Version；确认记录 append-only，并且只有该版本仍是 current version
时才完成 `WAITING_HUMAN` 和恢复 Workflow。过期 `expected_version` 返回稳定
`VERSION_CONFLICT`，操作人必须先审阅服务端最新版本。

数据库使用组合外键把 confirmation 的 `approval_id + workflow_id + subject_id +
subject_version` 绑定到同一个 Approval 精确主题，不能把其他 Workflow、ProductBrief 或版本
的批准记录拼接为有效确认。应用层校验是错误反馈，组合外键才是最终完整性边界。

Task-bearing ProductBrief、Provider request/response artifact、Audit 和 Idempotency 记录继承
Workflow 的同一个 72 小时截止时间。重新分析、重试或人工编辑不得延长该时间。原始
Provider request/response 只写入启用 Versioning 的 `PROVIDER_RESULT` 逻辑位置，每次写入
强制服务端加密；MySQL 保存 storage backend、location、bucket、key、精确 provider version、
ETag、SHA-256、byte size、Retention Class、deadline 和归一化 provenance。日志、trace、错误
和 Outbox 不得包含原始 prompt、商品字段正文、图片 URL、对象位置、Provider body 或 Secret。
ProductBrief Analysis 另外持久化最初 HTTP 请求的 Trace ID；Worker 产生的等待、策略确认和
Workflow continuation 事件必须沿用该 Trace，而不是用 Operation ID 伪造新的链路身份。

每个原始 Provider artifact 在写对象前必须先提交
`product_brief_provider_artifacts` 的 `INTENDED` 行，冻结 Workspace、ProductBrief、
Operation attempt、`call_index`、kind、确定性对象目标、期望 hash/size 和 retention。确认写入
后再把同一行推进为 `STORED`，补全精确 Version ID、ETag 和 `stored_at`；无法证明写入结果时
进入 `UNKNOWN`，只能按持久化精确 key 做有界版本枚举对账，禁止再次调用 Provider 或覆盖
目标。暂时性对象存储不可用必须保留原行并交给 Durable Operation 重试权威，不能伪造成
artifact 语义上的 `UNKNOWN`。

每条 completed Provider Call 必须引用一个同归属、同 `call_index`、kind=`REQUEST` 且状态为
`STORED` 的账本行。非成功 Call 可以没有 response artifact；`SUCCEEDED` Call 必须引用同归属、
同 `call_index`、kind=`RESPONSE` 且状态为 `STORED` 的账本行。数据库约束与 Trigger 是最终
边界，应用层匹配用于返回稳定错误。账本目标、内容、retention 和归属不可变，合法状态推进
也必须严格递增版本。

Ticket 07 负责上述 durable discoverability、deadline 后业务读取拒绝和删除所需精确证据；
对象存储中的 Durable 物理删除、失败重试、删除 tombstone 以及 MySQL/对象存储对账由
Ticket 13 实现。在 Ticket 13 上线前，不得把 deadline 存在误报为对象已经物理删除，也不得
用无 Version ID 的手工删除替代后续流程。

所有同时推进 Workflow 与 ProductBrief 的事务统一按 `Workflow -> ProductBrief -> Operation`
获取行锁；只写 Provider Call 的事务以 ProductBrief 行锁作为同一 Operation attempt 的串行化
点。一个 attempt 已存在调用记录时，重放必须逐字段匹配 Provider identity、request ID、
usage、状态、artifact reference、错误和 retention；任何不一致以
`VISION_PROVIDER_CALL_REPLAY_MISMATCH` 终止，不能覆盖首个胜者。

每次外部提交前先单独提交 append-only `product_brief_provider_attempts`，以
`Operation ID + operation attempt + call_index` 唯一标识初次调用或 bounded repair，记录
提交幂等键哈希、输入哈希和 Provider 配置快照哈希，不保存原始幂等键或输入。开始 repair
前必须先提交上一条 completed `product_brief_provider_calls`；最终成功 Call 仍与 Model
ProductBrief Version 同事务提交。逐调用 intent 与 completed Call 共同区分“尚未提交”、
“提交结果未知”和“已有确定结果”，避免 Worker 在任一次 Provider 提交后、结果落库前崩溃时
把未知结果误判为未执行。
Provider attempt intent 与 Workflow cancel 统一先锁 Workflow。取消先提交时，Worker 不得进入
Provider；intent 先提交且对应 Operation 仍为 `RUNNING`、尚无 Provider Call 时，取消必须返回
`409 WORKFLOW_CANCELLATION_REFUSED`。该区间是外部提交的不可逆临界点，不能返回虚假的
`CANCELLED`。

## 状态流

```text
POST /workflows (COMMERCE_IMAGE_GENERATION)
  -> Workflow UNDERSTANDING / understand_product
  -> 不发布初始 workflow.run.requested，不提前进入检索或生成

POST :analyze (workflow input product_id 必须精确匹配)
  -> ProductBrief DRAFT
  -> Durable Operation PENDING/RUNNING
  -> model version
     -> policy satisfied: ProductBrief CONFIRMED + Operation SUCCEEDED
                          + workflow.run.requested(exact ProductBrief Version)
     -> review required: ProductBrief AWAITING_CONFIRMATION
                         + Operation WAITING_HUMAN

POST :revise -> immutable HUMAN version -> AWAITING_CONFIRMATION
POST :confirm(exact current version)
  -> append-only confirmation
  -> ProductBrief CONFIRMED
  -> Operation SUCCEEDED
  -> workflow.resume.requested

CONFIRMED -> POST :revise
  -> preserve prior confirmed version as history
  -> new immutable HUMAN version
  -> new Durable Operation WAITING_HUMAN
  -> Workflow AWAITING_PRODUCT_CONFIRMATION
  -> exact confirmation replaces confirmed version

CONFIRMED -> POST :analyze
  -> preserve all prior versions and confirmations
  -> ProductBrief DRAFT + new analysis record + new Durable Operation
  -> Workflow UNDERSTANDING
  -> normal model/review flow above
```

Outbox 中的 continuation 只表示发布时有效的执行意图。Worker 消费
`workflow.run.requested` 或 `workflow.resume.requested` 时，以及后续每个节点 claim 时，
必须以 MySQL `UTC_TIMESTAMP(6)` 和当前锁定事实重验 Commerce Workflow 类型、冻结
`product_id`、Workflow/ProductBrief retention、精确 deadline 与 confirmed version。期限已到
或版本已被后续重分析/修订取代时，事件以可观测的 `expired`/`superseded` stale no-op 完成，
不创建 Step、Checkpoint、Provider Call 或新 Outbox，也不消耗 retry/DLQ 预算。只有依赖暂时
故障进入 Durable Retry；未知或无效 Contract 继续失败关闭。

低置信度、冲突、mandatory review 或 sensitive claim 任一命中都进入人工确认。Web 必须展示
common/category fields、confidence、evidence、source Asset Version、conflict、review reason
和 sensitive warning。修改值必须填写 revision reason；恢复本地旧草稿需要操作人显式选择，
不能在 409 后自动覆盖服务端版本。

每个字段值都是带 `kind` 判别器的版本化对象，不接受裸字符串、裸数组或任意 JSON。
`common.identity/category`、文本、文本列表、声明列表、风险标志和尺寸列表分别有独立严格
schema；字段 path 决定唯一允许的 kind。API、Provider 输出、人工修订、浏览器草稿恢复和
OpenAPI 生成类型都必须复用同一 31 路径映射，未知 path、错误 kind、额外属性、重复列表项或
非法尺寸结构均失败关闭。

## HTTP 操作

所有请求都必须由可信网关提供签名 principal。所有 mutation 另外使用 `X-Workspace-Id`、
`X-Actor-Id` 和唯一 `Idempotency-Key`，且 actor 必须与签名 principal 精确匹配。读取使用
签名 principal 和 Workspace scope；无 Workspace 权限返回 `403 WORKSPACE_ACCESS_DENIED`，
有权限但资源不在该 Workspace 时返回 `404 NOT_FOUND`。

```text
POST /api/v1/product-briefs:analyze
GET  /api/v1/product-briefs/{product_brief_id}
GET  /api/v1/product-briefs/{product_brief_id}/versions
POST /api/v1/product-briefs/{product_brief_id}:revise
POST /api/v1/product-briefs/{product_brief_id}:confirm
GET  /api/v1/product-briefs/analysis-workflow-context/{workflow_id}
GET  /api/v1/product-briefs/workflow-context/{workflow_id}
GET  /api/v1/product-briefs/{product_brief_id}/operations/{operation_id}
```

分析返回 `202` 和持久化的 `operation_id`。刷新页面后 Web 从持久化的 ProductBrief ID 恢复，
并重新读取 ProductBrief、version history 和 Operation；不能只恢复 spinner。新分析提交前
读取 analysis Workflow context，确认提交前读取绑定该 ProductBrief 的 Workflow context，
两类投影都不能由浏览器通用 Workflow 接口代替。
Policy/Rights 拒绝为非重试错误。Provider 429、明确 5xx 和可证明未提交的连接失败由
Durable Operation 分类重试；无法证明是否已执行的超时/中断进入人工对账，不使用第二套
Celery retry。

Web 只能读取 ProductBrief 专用的最小权限 Workflow/Operation 投影。Workflow 投影只包含
`id/version/retention_deadline` 和确认所需的规范化状态；Operation 必须同时匹配 Workspace、
`PRODUCT_BRIEF_ANALYSIS`、`product_brief` 目标类型和 URL 中的 ProductBrief ID，并且只返回
状态、尝试计数、脱敏错误和版本。BFF 不得代理完整 Workflow/Operation 读取接口，避免把输入
输出引用、Provider request ID、Lease owner、步骤与尝试内部事实暴露给浏览器。

所有 ProductBrief 路由在 OpenAPI 中显式声明稳定的 `500` 与 `503` 错误信封。未分类异常只
返回 `INTERNAL_ERROR`、固定安全文案和 request/trace ID；原始异常正文不得进入响应或结构化
日志字段。`422` 只返回校验错误的 `type/loc/msg`，不得回显非法输入值或校验器上下文。
`STORAGE_UNAVAILABLE` 等已知依赖故障继续按 `503` 和可重试属性分类。

Web 在发送分析请求前先持久化 Product、完整请求 payload 和幂等键。页面卸载、网络中断或
浏览器崩溃后，恢复流程只能用同一 payload 和同一幂等键重放，直到 API 返回原有
ProductBrief/Operation；不能生成新幂等键。该命令未结算期间，载入、刷新、新分析、人工
修订和确认全部关闭，只保留同 payload/key 的安全重试。`408/429` 等只有在稳定错误信封明确
`retryable: false` 时才能结算；`retryable: true` 或缺少结算证据时继续保留原命令。切换
Product 时必须取消旧请求并清除旧投影，
迟到响应通过 generation guard 丢弃。确认后的 ProductBrief 仍允许人工修订或显式启动新的
模型分析周期。

浏览器持久记录必须保存服务端精确 `retention_deadline`，在该时刻及之后删除记录并禁止重放；
旧 schema、缺失/非法 deadline 和无法写入浏览器存储都失败关闭。任何核心投影发布后的写入
或 retention timer 激活失败必须立即 abort 并清除内存投影；覆盖写失败时保留最后一份已经
持久化的 replay identity，不能把仅存的 payload/key 静默降级成无恢复身份的内存状态。修订收到
`409 VERSION_CONFLICT` 时，把原始 payload、幂等键和 `version-conflict` 状态作为一个完整
命令保留。随后刷新只能 GET 最新 ProductBrief，不能再次 POST 修订；即使该 GET 暂时返回
`503` 或再次刷新，命令也必须保留到操作人明确“恢复本地草稿”或“放弃本地草稿”，选择完成后
才结算为无 pending command 的当前身份。

ProductBrief 恢复命令、人工草稿和 evidence 只能写入当前标签页的 `sessionStorage`，不能写入
跨会话 `localStorage`。加载时先清除旧 ProductBrief localStorage namespace，再清扫全部过期
session 记录；活动任务必须设置 deadline timer，到期立即 abort 当前请求、清除持久记录和内存
投影。切换 Workspace 或 Product 同样 abort 旧代次并清除上一身份，不能让迟到响应恢复旧数据。

Web 把当前 ProductBrief 作为核心事实，version history 和 Operation 作为可独立失败的辅助
投影。核心 revise/confirm 成功后，辅助接口 `503` 只能显示降级告警，不能回滚已显示的核心
结果。自动轮询在状态变化时沿用同一请求预算；预算耗尽后停止请求并要求操作人显式继续。
Version history 每页最多 20 条并使用服务端 cursor；“载入更多”必须保持 ProductBrief 身份、
请求代次和 abort guard，跨页及页内按不可变 Version ID 去重。

## 部署配置

升级到 `f2a7c9d1e406` 时，迁移第一步会只读验证每条 legacy Provider Call 都能完整映射到
Ledger，并验证 request/response physical target 与逻辑 owner 均唯一；重复 target、孤儿归属
或不完整 legacy artifact 会在创建 Ledger 表或新增 Call 列之前失败。

从 `f2a7c9d1e406` 升级 Trace Lineage 时，迁移会先证明每条历史 Analysis 精确对应一条
`product-brief.requested` Outbox 事件；缺失或重复映射会在增加列前失败。合法升级会短暂移除
Analysis immutable trigger、回填原始 Trace、把列收紧为 `NOT NULL`，并在成功或异常路径恢复
trigger。部署前仍应备份并在同规模副本演练迁移。

降级必须在第一个 DDL 前通过可表示性预检。有任何 Analysis Trace 时不得移除 Trace Lineage；
Artifact Ledger 中存在 `INTENDED`、`UNKNOWN`、未被 completed Call 引用的 `STORED` 行，或
Ledger 与 legacy Call 引用不一致时，不得降到 `d9e4f7a2b610` 以下。预检拒绝后 revision、
列、行和 immutable/no-delete trigger 必须保持原样；操作人不得通过删行、改 trigger 或设置
session variable 绕过，应先保留备份并修复事实不一致。

执行 ProductBrief 的 Worker 必须订阅 `commercevision.asset`，并声明
`PRODUCT_BRIEF_ANALYSIS`：

```text
CV_WORKER_QUEUES=["commercevision.asset"]
CV_WORKER_REQUIRED_OPERATION_KINDS=["PRODUCT_BRIEF_ANALYSIS"]
```

API 和 Worker 必须共享以下非 Secret 身份与策略值：

```text
CV_VISION_ADAPTER=alibaba
CV_VISION_PROMPT_VERSION=<immutable-prompt-version>
CV_VISION_PRODUCT_FACTS_MAXIMUM_BYTES=65536
CV_VISION_PRODUCT_FACTS_MAXIMUM_DEPTH=8
CV_VISION_PRODUCT_FACTS_MAXIMUM_NODES=1024
CV_VISION_PRODUCT_FACTS_MAXIMUM_STRING_BYTES=4096
CV_PRODUCT_BRIEF_REVIEW_POLICY_VERSION=<published-review-policy-version>
CV_PRODUCT_BRIEF_CONFIDENCE_THRESHOLD=0.80
CV_PRODUCT_BRIEF_MANDATORY_REVIEW_PATHS=[]
CV_PRODUCT_BRIEF_SENSITIVE_CLAIM_PATHS=["<published-field-path>", "..."]
CV_PRODUCT_BRIEF_ANALYSIS_MAX_ATTEMPTS=5
CV_PRODUCT_BRIEF_ANALYSIS_MAX_RECONCILIATION_ATTEMPTS=8
CV_VISION_TEMPORARY_REFERENCE_LIFETIME_SECONDS=60
CV_ALIBABA_VISION_ENDPOINT=https://<approved-host>/<compatible-api-path>
CV_ALIBABA_VISION_ENDPOINT_REGION=<approved-region>
CV_ALIBABA_VISION_MODEL=<configured-model-id>
CV_ALIBABA_VISION_MODEL_SNAPSHOT=<pinned-model-snapshot>
CV_ALIBABA_VISION_ADAPTER_VERSION=<adapter-version>
CV_ALIBABA_VISION_CONNECT_TIMEOUT_SECONDS=3
CV_ALIBABA_VISION_READ_TIMEOUT_SECONDS=30
CV_ALIBABA_VISION_END_TO_END_TIMEOUT_SECONDS=45
CV_ALIBABA_VISION_MAXIMUM_CONCURRENCY=4
CV_ALIBABA_VISION_MAXIMUM_RESPONSE_BYTES=524288
CV_ALIBABA_VISION_MAXIMUM_OUTPUT_TOKENS=4096
CV_ALIBABA_VISION_MAXIMUM_REPAIR_ATTEMPTS=1
CV_VISION_DATA_TRANSFER_ENABLED=true
CV_VISION_DATA_TRANSFER_POLICY_VERSION=<published-transfer-policy-version>
CV_VISION_DATA_TRANSFER_ALLOWED_WORKSPACE_IDS=["<binary-exact-workspace-id>"]
CV_VISION_DATA_TRANSFER_ALLOWED_RETENTION_CLASSES=["TASK","FOUNDATION"]
CV_VISION_DATA_TRANSFER_ALLOWED_PROVIDERS=["alibaba-model-studio"]
CV_VISION_DATA_TRANSFER_ALLOWED_ENDPOINT_REGIONS=["<approved-region>"]
CV_VISION_DATA_TRANSFER_ALLOWED_ENDPOINT_HOSTS=["<approved-host>"]
```

只有 Worker 接收以下执行配置：

```text
CV_ALIBABA_VISION_API_KEY_HOST_PATH=/absolute/host/path/to/alibaba-vision-api-key
CV_ALIBABA_VISION_API_KEY_FILE=/run/secrets/alibaba-vision-api-key
CV_ALIBABA_VISION_API_KEY_FILE_MAX_BYTES=4096
CV_ALIBABA_VISION_ALLOWED_IMAGE_ORIGINS=["https://<controlled-read-origin>"]
CV_VISION_PREFLIGHT_BUDGET_SECONDS=10
CV_VISION_OPERATION_LEASE_MARGIN_SECONDS=15
CV_WORKER_STOP_GRACE_PERIOD_SECONDS=90
CV_PROVIDER_ARTIFACT_RECONCILIATION_TARGETS=[]
CV_WORKFLOW_STEP_LEASE_SECONDS=300
```

API 不得接收 API Key 文件路径、静态 `CV_ALIBABA_VISION_API_KEY` 或 controlled origin。
基础 Compose 的 deterministic Adapter 只读挂载仓库内受版本控制的空白非秘密 fixture，
因此干净 clone 不依赖工作区外的凭据文件；该 fixture 不是可用凭据。切换到 Alibaba
Adapter 时必须把 `CV_ALIBABA_VISION_API_KEY_HOST_PATH` 设置为宿主机上已经安全配置的真实
Secret 文件绝对路径。未覆盖、缺失、空白、目录、非普通文件、超限或多行文件都会在 Worker
接收任务前失败关闭，绝不降级使用 fixture。生产环境只允许只读挂载的绝对 API Key 文件；
Worker 每次 Provider 调用重新读取文件，因此 Secret 轮换不要求重启进程。静态 Key 仅允许
本地/测试。生产 ProductBrief Worker 只允许 Alibaba Adapter，并要求对象存储 Versioning、加密策略和
`CV_OBJECT_STORE_REQUIRE_ENCRYPTION=true`。Provider artifact 仍会逐写强制 SSE，不能依赖
bucket 默认值作为唯一控制。

生产 `CV_ALIBABA_VISION_MODEL_SNAPSHOT` 必须使用带日期的不可变标识
`<model-family>-YYYY-MM-DD`。`CV_WORKFLOW_STEP_LEASE_SECONDS` 必须不小于
`CV_VISION_PREFLIGHT_BUDGET_SECONDS +
CV_ALIBABA_VISION_END_TO_END_TIMEOUT_SECONDS +
CV_VISION_OPERATION_LEASE_MARGIN_SECONDS`，为授权复检、Provider deadline 和数据库提交保留
明确余量；配置不满足时 Worker 启动失败。

`CV_WORKER_STOP_GRACE_PERIOD_SECONDS` 同时驱动 Worker 的配置校验和 Compose
`stop_grace_period`，不得小于上述 preflight、Provider deadline 与 lease/cleanup margin
之和；默认 90 秒覆盖默认的 `10 + 45 + 15` 秒预算并保留进程清理余量。提高任一执行预算时
必须同步提高停机宽限，不能让滚动发布在 Celery warm shutdown 完成前发送 `SIGKILL`。

对象存储 backend 或 Provider Result bucket 变更前，必须通过
`CV_PROVIDER_ARTIFACT_RECONCILIATION_TARGETS` 显式注册仍可能被未结算 Ledger 引用的历史物理
目标。该值是严格 JSON 数组；每项至少包含 `object_store_backend` 和
`object_store_provider_result_bucket`。同 backend 可继承当前连接配置；跨 backend 必须显式
提供 endpoint、presign endpoint、region、credential mode 和 addressing mode，生产 OSS
仍只能使用可续期 workload identity。API 不得接收此 Worker-only 配置。

临时 URL 有效期必须覆盖完整 end-to-end deadline；deadline 必须大于 connect+read transport
budget。Image origin 仅允许 HTTPS exact origin。Endpoint 必须是无 credential、无 query 或
fragment 的 HTTPS URL；transfer allowlist 使用其 canonical host。完整 endpoint 路径进入
Provider 配置快照哈希，因此同 host 下的路径漂移也会在临时 URL 签发前被拒绝。

end-to-end deadline 从请求校验前开始，覆盖并发容量等待、请求 artifact 写入、HTTP 连接与
流读取、response artifact 写入、结构化解析和 bounded repair。对象存储 Adapter 自身仍必须
配置有限 connect/read timeout；请求发出前耗尽 deadline 才能归一为 retryable
`PROVIDER_TIMEOUT`。一旦可能已提交，迟到响应、流读取/关闭中断或 response artifact 写入
失败都归一为不可自动重试的 UNKNOWN，不得把迟到响应发布为成功。

API 与 Worker 使用同一共享函数计算 Provider 配置快照。完整 endpoint、模型快照、response/
output 上限、repair 上限、connect/read/end-to-end timeout、最大并发、Product facts
字节/深度/节点/字符串预算和 prompt version 任一漂移，都会在签发图片引用前以 identity
mismatch 失败关闭。配置身份 schema version 当前为 v2；确定性 Adapter 的 scenario 只是
测试/本地结果注入，不属于 Provider identity，其余预算和 prompt 仍必须精确一致。

本地 Compose 默认 deterministic Adapter 和 deny-all transfer policy。需要本地完整流程时，
显式允许测试 Workspace、`TASK`、`deterministic-vision`、`local` 和
`deterministic.invalid`；不要把该配置复制到 production。

## Provider 与恢复

Alibaba Adapter 使用严格 structured-output schema、确定性 decoding 参数（Provider 支持时）、
有限 response bytes、有限 output tokens、有限 Product facts 复杂度、有限并发和最多一次
repair。每个 call 记录 Provider、endpoint region/
host、requested model、resolved model、prompt/config snapshot、request ID、token usage、
latency、状态和 artifact reference。结构在 Adapter 外独立验证；repair 后仍 malformed 是
terminal failure。

Provider 返回的 evidence reference 只是不可信输入。字段结构验证通过后，应用层按 source
Asset Version、field path、kind、region 和 excerpt hash 生成新的 opaque reference；URL、
对象位置以及 URL 编码、包装或 base64 变体不得进入 ProductBrief、API、日志或 Outbox。

Worker Master 启动时以及运行期间每 5 秒执行真实 readiness，覆盖 RabbitMQ、MySQL、通用对象
存储、`PROVIDER_RESULT`、未结算 Artifact Ledger 实际引用的每个历史 exact target、ClamAV
和当前挂载的 Vision Credential。历史配置中没有未结算 Ledger 引用的 target 不参与远程
readiness，但其 client 仍由 Worker 明确持有并逐项 best-effort 关闭；当前 Object Storage
client 继续由 Worker Runtime 单独持有，不能重复关闭。未知 target 或任一必需历史 target
不可达时 readiness 失败关闭。必需历史 target 在配置上限内并行探测，Worker 会等待全部探针
结算并聚合错误，然后才逐项关闭 client，避免 target 数量线性放大 readiness 周期或留下后台
连接。只有全部依赖和必需
Executor 可用时才原子刷新带 `checked_at/fresh_until` 的 Master marker；连续两次失败必须先
撤销 marker，再让 Celery Master 以非零状态受控退出。容器 healthcheck 拒绝缺失、过期、
格式错误、含错误正文或依赖状态异常的 marker，并要求达到配置并发数的存活 ready 子进程。
Prefork 子进程只验证本地 Executor Registry，不创建重复探针线程或执行远程 readiness。
仅有通用对象存储 endpoint 可达不能作为 ProductBrief Worker 就绪证据。

`CV_WORKER_READINESS_MAX_AGE_SECONDS` 是 publisher 与 healthcheck 共用的唯一 Master marker
lease。Settings 在 Worker 启动前要求它不少于以下完整最坏周期，不能单独放宽 healthcheck：

```text
5s probe interval
+ 3s RabbitMQ connect                         # 失败或成功后均 abortive collect，不等待 CloseOk
+ CV_MYSQL_CONNECT_TIMEOUT_SECONDS
+ 5s MySQL SELECT 1 socket/server deadline
+ 5s unsettled Artifact target query deadline # 仅 Asset/ProductBrief Worker
+ current Object Storage readiness target budget
+ max(required historical target readiness budget)  # 历史 target 并行，不求和
+ CV_CLAMAV_TIMEOUT_SECONDS                          # 仅 ClamAV Asset Worker
+ 2s publication/scheduling margin
```

每个 Object Storage target 最多执行三次顺序元数据调用；每次调用的预算都是“可再生凭据
刷新 deadline（static 为 0）+ connect deadline + read deadline”。因此 target budget 为
`3 × (credential refresh + 2 × OBJECT_STORE_READINESS_TIMEOUT_SECONDS)`。MinIO readiness
client 的 SDK 总尝试数为 1，OSS readiness `requests` transport 的 retry 总数为 0，不能让
SDK 隐式重试突破该预算。当前 adapter 的不同 bucket 并行，历史 exact target 也并行，所以
两组分别取最慢者；当前组完成后才执行历史组，二者仍需相加。

readiness 专用 MySQL Engine 只有一个连接、禁用 `pool_pre_ping`，使用只读探针所需的
autocommit 并跳过归还连接时的额外 rollback，同时设置 connect/read/write socket timeout。
它先执行带 5 秒 server hint 的 `SELECT 1`；Asset/ProductBrief Worker 再在同一连接上执行
一次带相同 deadline、`DISTINCT + LIMIT` 的未结算 target 查询，不能按历史 target 数量增加
SQL round trip。RabbitMQ 探测最多连接一次，清理使用 `collect(0)` 直接关闭 transport，
不执行可能无限等待 `CloseOk` 的 graceful release。

默认 Compose 没有历史 target，因而 Asset Worker 预算为
`5 + 3 + 5 + 5 + 5 + 6 + 0 + 15 + 2 = 46s`，配置的 50s lease 另留 4s 余量。
若增加 MySQL、Object Storage、历史 target 或 ClamAV timeout，必须同步增大 lease，否则
Settings fail closed，Worker 不会启动。lease 只避免健康探针成功期间出现过期空窗；连续失败
达到阈值时仍立即撤销 marker，不等待 `fresh_until`。

Provider call 之外不持有 MySQL transaction。Durable Operation 使用既有 Lease、attempt、
retry-after、指数退避、reconciliation 和 DLQ：

| 结果 | Operation 行为 |
|---|---|
| success + review required | `WAITING_HUMAN`，不自动重投 Provider |
| success + policy confirmation | `SUCCEEDED` 并恢复 Workflow |
| 明确未提交的 connect/pool failure、429、已返回 5xx | `RETRYABLE_FAILED`，等待数据库 `next_attempt_at` |
| 已收到 2xx/4xx/429/5xx 后的 body read/close interruption、response artifact 失败 | 保留已确认 status/header/request ID/有限 body evidence，结果标为 UNKNOWN，进入 reconciliation，禁止自动重投 |
| 任一 `call_index` 已记录 submission intent、无同索引 call/result | terminal `VISION_SUBMISSION_OUTCOME_UNKNOWN`，进入 DLQ/人工对账 |
| artifact 对账期间对象存储暂时不可用 | 保持账本行不变，由 Durable Operation 按数据库重试计划重试 |
| artifact 同一精确 key 存在多版本、delete marker 或内容不匹配 | 账本进入 `UNKNOWN`，terminal `VISION_ARTIFACT_OUTCOME_UNKNOWN`，人工完整性对账 |
| malformed after bounded repair | terminal `FAILED` |
| artifact 精确版本丢失或内容冲突 | terminal `VISION_ARTIFACT_INTEGRITY_CONFLICT` |
| Rights / transfer policy denied | terminal `FAILED`，不得调用 Provider |
| 已持久化 awaiting result 的重复事件 | 收敛回同一 `WAITING_HUMAN` |

成功 Provider Call 与其 Model ProductBrief Version 在同一 MySQL 事务中提交；不能先留下
`SUCCEEDED` Call 再单独写 Version。若后续状态事件发布被拒绝，调用与版本证据保持原子，
通用 Durable Operation 再按既有收敛协议处理。

Worker 在等待期间重启不会丢失人工门禁。确认事务完成精确版本批准、Operation human wait 和
Workflow resume Outbox。不要通过直接 UPDATE Operation、ProductBrief 或 Workflow 来绕过
门禁。`workflow.resume.requested` 对 ProductBrief approval 不是 LangGraph interrupt 的
`Command(resume=...)`：Worker 校验 payload 后从 MySQL 持久化的 Workflow current node 继续；
Creative Plan/Result approval 才恢复对应 interrupt。Inbox 重放同一确认事件必须幂等，不能
重复执行 Provider 或重复创建 Step。

Alibaba OpenAI-compatible Chat Transport 不提供按客户端幂等键查询提交结果，也没有公开的
Provider 去重 Contract。未知结果必须先进入 Durable Operation reconciliation；仍无法证明时
以 `VISION_SUBMISSION_OUTCOME_UNKNOWN` 失败关闭到 DLQ/人工对账，不允许自动发出第二个
Provider POST。只有能证明未提交或已收到明确失败响应的情况才消耗自动重试预算，不能由
Celery 另起重试权威。Worker shutdown 会先关闭/取消 HTTP transport，再在有限时间内等待已
进入 analyzer 生命周期的调用和 artifact 写入结束；等待失败必须使 shutdown 明确失败，不能
在后台静默继续写入。Analyzer 关闭后拒绝新调用。

读取 Provider artifact 必须携带 MySQL 保存的精确 provider version。版本不存在、ETag/
SHA-256/byte size 不一致或同一逻辑 artifact 的重放内容变化均按完整性冲突处理，不能退回
读取“当前最新版”，也不能发布迟到结果。

## 诊断

先通过公开接口读取 ProductBrief 和 Operation，再使用只读 SQL 对照：

```sql
SELECT id, workspace_id, workflow_id, product_id, state,
       current_version_id, confirmed_version_id, version,
       retention_deadline, created_at, updated_at
FROM product_briefs
WHERE workspace_id = :workspace_id AND id = :product_brief_id;

SELECT id, state, attempt_count, max_attempts, reconciliation_attempt_count,
       next_attempt_at, lease_expires_at, error_code, error_category,
       provider_request_id
FROM durable_operations
WHERE workspace_id = :workspace_id
  AND kind = 'PRODUCT_BRIEF_ANALYSIS'
  AND target_id = :product_brief_id;

SELECT operation_attempt, call_index, status, provider, endpoint_region,
       endpoint_host, requested_model, resolved_model, prompt_version,
       request_id, latency_ms, error_code, error_retryable, created_at
FROM product_brief_provider_calls
WHERE workspace_id = :workspace_id
  AND product_brief_id = :product_brief_id
ORDER BY operation_attempt, call_index;

SELECT id, operation_id, trace_id, category, product_catalog_version,
       provider, endpoint_region, endpoint_host, requested_model,
       prompt_version, provider_configuration_snapshot_sha256,
       retention_deadline, created_at
FROM product_brief_analysis_requests
WHERE workspace_id = :workspace_id
  AND product_brief_id = :product_brief_id
ORDER BY created_at, id;

SELECT operation_id, operation_attempt, call_index, submission_key_sha256,
       input_sha256, provider, endpoint_region, endpoint_host, requested_model,
       prompt_version, config_snapshot_sha256, retention_deadline, created_at
FROM product_brief_provider_attempts
WHERE workspace_id = :workspace_id
  AND product_brief_id = :product_brief_id
ORDER BY created_at, id;

SELECT id, operation_id, operation_attempt, call_index, kind, state,
       storage_backend, location, bucket, object_key, provider_version_id,
       etag, expected_sha256, expected_byte_size, unknown_reason, version,
       retention_class, retention_deadline, stored_at, created_at, updated_at
FROM product_brief_provider_artifacts
WHERE workspace_id = :workspace_id
  AND product_brief_id = :product_brief_id
ORDER BY operation_attempt, call_index, kind;

SELECT version_number, source, confirmation_required,
       unresolved_field_count, changed_paths_json, payload_sha256,
       actor_id, created_at
FROM product_brief_versions
WHERE workspace_id = :workspace_id
  AND product_brief_id = :product_brief_id
ORDER BY version_number DESC;
```

Provider call 表只允许 provenance 和 opaque artifact reference。若日志、Outbox 或错误响应中
出现商品字段正文、raw JSON、签名 URL、API key、bucket/key 或完整 prompt，立即停止该
Worker 的新流量，保留最小化 ID evidence，并按 Secret/数据泄露流程处置。

### Provider throttling 或 timeout

1. 按 Operation normalized code、attempt、`next_attempt_at`、Provider request ID 和 latency
   区分明确未提交的 connect/pool failure、429/5xx 与结果未知的 read/write interruption。
2. 检查 endpoint region、配额、controlled origin、临时引用有效期和并发上限。
3. 仅对确认可重试的结果等待同一 Operation 的数据库重试；不要调用 Celery retry 或创建
   替代 Operation。
4. `VISION_SUBMISSION_OUTCOME_UNKNOWN` 必须由操作人先对账账单/Provider 审计，再决定是否
   通过既有 Operator DLQ replay 创建一次有 actor/reason 的显式新尝试。

### 长时间 `WAITING_HUMAN`

1. 确认 ProductBrief 为 `AWAITING_CONFIRMATION` 且 current version 存在。
2. 确认 Operation 为 `WAITING_HUMAN`，不是有效 Lease 或 delayed retry。
3. 检查 Web 是否加载 current version、history 和 review reasons；刷新不会改变服务端状态。
4. 操作人可先创建 HUMAN revision，再确认该精确 current version。

不得自动确认低置信度/冲突/sensitive 字段，也不得把旧 confirmation 复制到新版本。

### Rights 或 policy 拒绝

检查当前 Rights Record 是否精确允许 `VISION_ANALYSIS` 和 configured provider，以及 Asset、
版本、对象和 retention 是否仍可用。再检查 API 与 Worker 的 transfer policy
version/snapshot、Workspace 大小写、Retention Class、Region 和 Host 是否一致。修正政策
必须发布新 version；不要重用旧临时 URL、旧 Operation input hash 或旧 Provider artifact。

### Stale version conflict

Web 应保留本地草稿，重新加载服务端 current version，并让操作人显式恢复或放弃草稿。
只有在审阅新基线后才能用新的 `expected_version` 提交。不得由服务端静默 merge。

## 发布门禁

```powershell
uv run pytest tests/unit/test_product_brief_domain.py `
  tests/unit/test_product_brief_value_contracts.py `
  tests/unit/test_vision_provider_adapter.py `
  tests/unit/test_provider_artifact_storage.py `
  tests/unit/test_settings.py `
  tests/unit/test_worker_transport.py -q
uv run pytest tests/contract/test_product_brief_openapi.py `
  tests/contract/test_worker_deployment.py -q
uv run pytest tests/integration/test_product_brief_hitl_mysql.py `
  tests/integration/test_product_brief_migration_mysql.py `
  tests/integration/test_product_brief_provider_artifact_migration_mysql.py `
  tests/integration/test_product_brief_trace_migration_mysql.py `
  tests/integration/test_product_brief_provider_artifact_ledger_mysql.py `
  tests/integration/test_operation_migration_mysql.py -q
pnpm --filter @commercevision/web test:unit
pnpm web:proxy-test
pnpm web:typecheck
pnpm web:lint
pnpm web:api-types:check
pnpm --filter @commercevision/web build
pnpm --filter @commercevision/web e2e -- product-brief-workbench.spec.ts
pnpm --filter @commercevision/web e2e
uv run alembic upgrade head
uv run alembic check
docker compose -f infra/compose/docker-compose.yml config --quiet
```

CI 使用 deterministic Adapter 和 mocked Alibaba HTTP transport，验证成功、低置信度、冲突、
sensitive、malformed、timeout、bounded repair、response byte limit 和 Secret redaction。
真实 Alibaba 调用需要目标账号、Region、模型权限、受控读域名和企业 transfer policy，
因此是显式 production/staging 凭据门禁，不属于无凭据 CI。上线前必须在目标 Region 完成
一次受控图片 smoke test，并只记录 normalized result、request ID、resolved model 和 latency。
