# CommerceVision Agent 执行进度

## 2026-07-31 — Phase 2 连续交付恢复

- Ticket 08 提交 `2b79080` 与 GitHub Actions `30596872198` 已确认全绿，Ticket 09 正式解锁。
- 已恢复 `task_plan.md`、`progress.md`、`findings.md`；session catchup 在 60 秒有界窗口内完成，
  Git 基线为 `main == origin/main == 2b79080`，仅计划文件存在本轮记录改动。
- Ticket 09 沿用已确认的 HTTP、Durable Worker/Event、真实 MySQL/MinIO/Milvus、Provider Adapter
  和 Web 测试接缝，执行独立上下文 TDD，不重做 Ticket 08。
- Ticket 09 只读架构侦察确认仓库尚无平行索引实现：Retrieval 包为空壳，但 Domain 已预留
  `ASSET_INDEXING` / `RECONCILIATION` / `COLLECTION_REBUILD` Durable Operation，Contracts
  已预留 INDEX queue/event，Compose 已有 Milvus。实现将替换 pending event payload、扩展现有
  Operation Executor/Worker queue，并复用 Asset current-usability seam。
- 首个 RED 纵切锁定公开 Domain/Contract：完整 Collection identity、`dynamic_fields=false`、
  embedding-spec hash、确定性 Milvus primary key，以及严格 typed INDEX event。
- Ticket 09 首批 TDD 证据：Collection Domain RED 因 `CollectionSpec` 缺失而失败，GREEN 为
  `6 passed`；Provider/Admin Contract RED 因 typed request 缺失而失败，GREEN 为 `11 passed`；
  typed INDEX event RED 因 `AssetIndexRequestedPayload` 缺失而失败。Domain、Contract 与既有
  event suite 聚焦回归为 `24 passed in 0.40s`，对应 Ruff 全绿。
- 持久化早审在真实 MySQL seam 前发现初版 migration 的 Check Constraint 引用了缺失的
  `write_generation` 列；ORM-only schema tests 对此假绿。该问题已作为 P0 退回，要求先完成
  ORM/DDL/约束与真实 Alembic roundtrip RED→GREEN。
- 上述 migration P0 已修复：实现 Agent 先核对测试库 revision 与唯一 partial table，再仅清除
  该次失败留下的测试表；`tests/integration/test_indexing_migration_mysql.py` 现为
  `1 passed in 12.84s`，并在真实 MySQL 证明 generation 归属正确及四个时间列均为 `DATETIME(6)`。
- Application/Persistence 早审发现并退回：Operation identity 错绑可变 Embedding CAS version、
  crash-after-upsert reconcile 未提交 MySQL `INDEXED`、授权校验错用 `milvus` 而非真实
  Embedding Provider 三项 P0，以及未同时比较 Rights Record ID/Version 的 P1。每项必须先有
  公开 seam RED，再修复并给出聚焦 GREEN。
- 增量早审确认 Operation target identity 已在进行中修正为不可变 Asset Version number；同时
  新增必须覆盖的门禁：Milvus upsert 边界 timeout 进入 unknown-outcome reconciliation，claim/
  final commit 对 Embedding state、Collection write authority、Operation 与 generation 做 CAS，
  并在发送 Provider 前重算 input hash、验证 Record 与 Collection 冗余身份完全一致。
- 依赖兼容探针：Python 3.13 下 `pymilvus==2.4.15` 裸导入因缺少 `pkg_resources` 失败；显式
  `setuptools<81` 后成功导入 2.4.15，但存在官方弃用警告。该运行依赖与未来同步升级约束已反馈
  实现 Agent，最终还需生产 Worker 镜像 import 与真实 Milvus CRUD 证明。
- 早审修复 checkpoint：不可变 Operation identity、实际 Provider 授权、Rights ID+Version、
  provider facts CAS、reconcile finalization、unknown upsert、generation-specific PK、合法状态/
  Collection write authority、权威 hash/spec、durable DELETE_PENDING、Milvus 2.4 schema 上限与
  Secret temporary input 已进入实现；公开 Domain/Contract/Executor 聚焦回归 `21 passed` 且 Ruff
  全绿。下一门禁是这些不变量的真实 MySQL/public-seam 证据与原子 create/unique-winner reload。
- 首轮 `tests/integration/test_indexing_mysql.py` 为 `6 failed / 107.17s`，失败均发生在测试
  seed：fixture 先设置 `permissions_sealed_at` 再插 Rights permissions，数据库不可变 trigger
  正确拒绝。生产栅栏保持不变，测试按“未封存父记录 → 子权限 → 一次性 seal”修正后重跑。
- 修正 seed 后真实 MySQL 范围进至 `5 passed / 1 failed`；唯一生产缺陷是 Provider facts
  已持久化但返回 target 未同步，已改为显式刷新 generation/provider request/actual model。
  stale + typed delete Outbox 聚焦回归为 `1 passed in 13.26s`，完整 6 项正在复跑。
- `uv run pytest -q tests/integration/test_indexing_mysql.py` 完整 GREEN：
  `6 passed in 71.54s`。真实 MySQL 已证明并发/重复请求 unique-winner reload 后 Collection、
  Embedding Record、Operation、requested Outbox 各一；crash-after-upsert reconcile 收敛为
  `INDEXED` 并保留 Provider facts；Rights race 收敛为 `DELETE_PENDING` + typed delete Outbox；
  非法状态、write-disabled Collection 与冗余模型身份漂移均关闭式失败。
- Milvus Adapter 首批 RED 覆盖缺实现、并发 create race、缺 upsert、SDK secret timeout 与 lazy
  lifecycle；当前 unit GREEN 为 `9 passed in 0.47s`。Retrieval 锁定 PyMilvus 2.4.15 与
  setuptools 80.10.2，正在进入真实 Milvus CRUD。
- Provider Adapter 侦察发现上层会丢失 stable error、Retry-After 与 unknown outcome；已锁定
  provider-neutral typed failure + Application 映射，并要求相对 Retry-After 由 Durable Policy
  的权威时钟计算。
- HTTP index-status route 聚焦测试以正确 package context 运行后 `1 passed in 22.48s`；它锁定
  Workspace membership forwarding 和严格有界响应字段，明确不暴露 Collection ID/name、
  Milvus PK 或 Provider Request ID。该切片实现与测试同批落下，无可验证 behavioral RED，
  已如实记录；真实 MySQL status projection 已覆盖 `NOT_REQUESTED → PENDING` 与相同禁泄漏面。
- Typed Provider error 与相对 Retry-After 完成 RED→GREEN：RED 因
  `EmbeddingProviderErrorV1` 缺失产生 2 个 collection error；新增 strict provider-neutral
  failure、Executor durable 映射、relative delay 互斥与 RetryPolicy worker-now/
  max-delay/deadline clamp 后，聚焦命令为 `4 passed, 37 deselected in 0.82s`。Ruff 自动修复
  2 项并手工拆分 1 条长行，待后续全量门禁。
- 真实 Milvus 2.4.15 Adapter 完成两轮 RED→GREEN：首轮发现 2.4.15 `get_load_state/drop`
  不接受统一 retry kwargs，改为只在受支持 RPC 上禁用 SDK retry；第二轮发现逐次 flush 被真实
  0.1/s RateLimiter 拒绝，删除 per-row flush，exact-PK proof 使用 Strong consistency，dirty
  collections 仅在 bounded close 聚合 flush。最终
  `tests/integration/test_milvus_index_adapter.py` 为 `3 passed, 1 warning in 12.07s`，覆盖并发
  ensure、重复 generation PK upsert/prove、旧 generation delete 不伤新 generation；无 sleep/
  rowcount，teardown 仅 drop 自有 `cv_ticket09_*` collection。
- Milvus unit/integration 初版同 basename 会触发 pytest import-file-mismatch；未改全仓 import
  mode，改为唯一 integration 文件名后在同一进程联合收集：
  中间门禁 `14 passed, 1 warning in 11.96s`，无 mismatch。
- Milvus owned scope 终版门禁：unit + 真实 2.4.15 integration 为
  `21 passed, 1 warning in 12.19s`；Ruff format/check、`uv lock --check`、dependency/import
  smoke 与 `git diff --check` 全绿。唯一警告是已显式锁 `setuptools<81` 的 PyMilvus
  `pkg_resources` 上游弃用，后续必须随 client/server 同步升级。
- Alibaba/Fixture Embedding Provider package 当前 `13 passed in 0.90s`；额外 HTTP 408
  contract RED→GREEN 后 contract 子集 `5 passed`。Adapter 依据阿里云官方 multimodal embedding、
  model、error-code 与 rate-limit 文档实现北京地域 IMAGE 请求、`enable_fusion=false`、受控
  dimension/usage、限流/超时/错误归一化和 Secret 脱敏；官方 URL 不支持自定义 headers，因此
  非空 required headers 关闭式拒绝。
- Web index-status 完成严格 RED→GREEN：RED 为缺少 `index-status-state` module；实现 exact
  runtime decoder、字段/state/date fail-closed、transient refresh policy、retry/nonretry
  presentation、asset+request epoch late-response fence 与 2 秒轮询后，相关 2 files / 9 tests、
  typecheck、lint 全绿；此前 Web 全量为 177 unit + 21 proxy 全绿。
- Embedding Provider owned scope 已完成：Fixture/Alibaba 单元与契约最终 `18 passed`，连同
  Vision、Content Safety、Malware、Provenance、依赖边界和 Worker deployment 的 Provider
  回归为 `184 passed in 29.84s`；Ruff 与隔离 mypy 探针全绿。官方不返回 resolved revision，
  因此 `actual_model` 只记录已提交模型 ID，`pinned_revision` 保持内部 Collection release epoch。
- IMAGE Worker 首个公开接缝 RED→GREEN 已闭合：index queue 的内置 Operation Kind 与 typed
  `asset.index.requested.v1` handler 初始 `2 failed`，接线后 `2 passed`；Embedding Settings
  启动/生产边界初始 `3 failed`，配置收敛后 `3 passed`。主控复跑同一 focused 范围全绿。
- Milvus owned scope 独立复审为 3 个 P1、3 个 P2、无 P0，当前明确不批准合并。P1 分别是
  PyMilvus 内部等待未受顶层 retry/float timeout 约束、`close()` 超时遗留 daemon thread、
  `setuptools 80.10.2` 命中 `PYSEC-2026-3447`；原 Milvus owner 已按同一范围恢复修复，业务
  主实现不并发触碰 retrieval/lock 文件。
- Embedding owned scope 独立复审为 4 个 P1、3 个 P2、无 P0，同样 Request changes。P1
  覆盖原始 httpx request 经异常图泄漏 Secret、429 partial-body 丢失已知 Retry-After、取消
  阶段无法区分 queued/dispatched/headers-observed，以及 qwen3 mainline alias 无 provider
  resolved revision。原 owner 已恢复修复；主实现只配合可信 byte-size seam 与内部 release
  epoch 的配置/运行手册栅栏。
- 业务 P0 中间门禁：Worker/Application/Domain/Settings/Readiness 聚焦组合为 `180 passed`；
  真实 MySQL 并发唯一 winner 为 `1 passed`，Provider retry、final-rights race、INDEXED 后撤权
  和非 IMAGE 四项为 `4 passed`。当前已实现 `PROCESSING→RETRYABLE_FAILED`、Strong absence
  confirmed-retryable、INDEXED 撤权原子 `DELETE_PENDING` + typed delete event；下一步仍需
  terminal reconciliation convergence、regrant 新 Operation、迟到旧代删除与真实
  Durable Worker/Event + MySQL/MinIO/Milvus 七场景。
- Milvus 独立复审的 3 个 P1 / 3 个 P2 已全部关闭：Adapter 采用单一 monotonic deadline、
  禁用 SDK 内部重试、移除后台 close/flush 线程，并以 setuptools 83 + 最小兼容层消除
  `pkg_resources` 漏洞依赖。主控复验 unit + 真实 Milvus 为 `23 passed`，Ruff 与
  `uv lock --check` 全绿；owner 的 `pip-audit` 为无已知漏洞。
- Embedding Provider 独立复审的 Secret 异常图、429 partial body、取消阶段、float32/
  HTTP-date Retry-After、preprocess 与可信 byte-size/5 MiB 上限均已关闭；主控复验 focused
  `30 passed` 与 Ruff 全绿。Provider 更广回归由 owner 得到 `195 passed`，唯一共享工作树
  失败是 index queue 已加入 Compose 而旧 deployment contract 尚未同步。
- 主控继续审查发现两个未闭合的发布阻断：regrant 新 Operation 会撞
  `uq_durable_operation_logical` 并被错误 reload 为旧 Operation；Embedding 数据出境
  Workspace/Retention allowlist 当前只有启动配置校验、Provider submission 前尚未执行。
  两项均已退回主实现，要求真实并发/零 Provider 调用与 Secret URL 零签发证据。
- 上述阻断已进入实现：Operation epoch/hash 与 embedding input identity 分离，typed request
  event 显式携带三套 identity 并由 EventRouter + MySQL authority 分层验证；外部传输策略在
  URL 签发前执行。主控复跑主链 unit 为 `195 passed`，复跑真实 MySQL indexing 为
  `11 passed in 159.14s`，覆盖 regrant 新 Operation、永久失败不自动复活、旧代 delete fencing
  与既有并发/重试/撤权场景。当前仍需 Durable max-attempt terminal、INDEXED 直接 regrant
  superseded delete，以及明文要求的真实 MySQL + MinIO + Milvus 七场景。
- Durable `max_attempts` 耗尽把当前 `RETRYABLE_FAILED` 收敛为 `PERMANENT_FAILED`，以及
  `INDEXED` 直接 rights reindex 原子发旧 gN `SUPERSEDED` delete 两项已落地；主控定向复验
  `2 passed, 11 deselected in 24.08s`。旧 Operation terminal callback 受 operation identity
  fence，不得覆盖 regrant 后的新当前 Operation。
- HTTP 状态已由真实 MySQL projection + ASGI error handler 证明：authorized current status
  返回有界 200，unknown 与 cross-workspace 返回完全一致的 404 envelope；主控定向复验
  `1 passed, 13 deselected`。API route/health unit 为 `15 passed`，仅保留仓库既有
  Starlette TestClient deprecation warning。
- Ticket 09 明文七场景已由主控独立复验：真实 MySQL + MinIO + Milvus 的 incremental
  upsert、duplicate delivery、dimension mismatch、Provider timeout、Milvus outage、
  crash-after-upsert unknown outcome 与 Rights regrant/generation fence 为
  `7 passed, 17 deselected in 122.29s`。Ticket 09 聚焦 unit/contract 为 `317 passed`，
  迁移 + MySQL 主链 + Milvus integration 为 `28 passed in 383.60s`。
- 完整 Web 门禁为 unit `181 passed`、BFF proxy `21 passed`、Playwright `89 passed`，
  TypeScript、ESLint、generated types、production build、pnpm audit 均通过；Python 全量
  unit + contract 为 `1082 passed, 1 skipped`，唯一 skip 为显式 opt-in Alibaba OSS。
- Standards/Spec 双审均为 Request Changes。阻断项覆盖：post-upsert MySQL completion
  必须进入同 generation reconciliation；authority completion 要在 Embedding 已提交但
  Operation 未成功时幂等；operator DLQ replay 必须能受审计恢复；执行中 regrant 必须清理
  已写旧代；Milvus delete identity conflict 不能误报 DELETED；SDK malformed response、
  Provider close 异常图、生产 Milvus TLS/default token 与 Web Rights 后状态刷新必须闭合。
- 2026-07-31 启动的全量 `tests/integration` 在用户中断时仍无失败输出，但进程随后不存在，
  未取得 pytest 汇总，因此不计作通过证据。上述审查修复完成后将以分组或有界单进程重新运行。
- 审查整改已全部闭合：post-upsert 迟到旧代写入会在同一事务记录 superseded completion marker
  并发出精确 generation delete；提交回包丢失后可由旧 request event 依据静态身份与 completion
  marker 幂等恢复；正常过期事件仍关闭式拒绝。真实故障注入覆盖“旧 g1 晚写、早期 delete 已
  absence、MySQL superseded commit 成功但 transport timeout、重入恢复成功、g2 保留”。
- Web 权威投影已在 Rights identity 不一致时立即返回 `STALE`，并以有界 authority grace polling
  覆盖异步事务可见性；首个 `NOT_REQUESTED` 建立基线、连续未请求继续等待，503 后自动恢复会清除
  错误，商品切换后的旧响应与旧 timer 均被 epoch fence 隔离。
- Milvus 生产边界现拒绝非 HTTPS、默认 token、URI userinfo/path/query/fragment 与非法 host/port；
  builder、consistency/proof malformed response、Provider close 等异常统一为固定安全错误，不保留
  SDK 原始异常图或 Secret。真实集成 fixture 在所有 teardown 分支尽力清理 collection、版本化对象、
  bucket、存储与向量客户端，并在全部清理后聚合报告失败。
- Ticket 09 最终本地证据：owned integration `40 passed, 1 warning in 623.56s`；全量 unit +
  contract `1099 passed, 1 skipped`（唯一 skip 为显式 live OSS opt-in）；Web unit `181 passed`、
  proxy `21 passed`、Playwright `89 passed`；TypeScript、ESLint、production build、Ruff format/check、
  Python/pnpm audit、OpenAPI 与 generated TypeScript、Compose config 均通过。两次无时间上限的完整
  integration 尝试分别运行 20/30 分钟且没有失败输出，但桌面执行窗口未取得最终汇总，因此不冒充
  通过；GitHub Actions 的完整 `uv run pytest` 是最终集成事实来源。
- Ticket 09 Standards、Spec 与 Quality 最终复审全部 `APPROVE`，明确复核 superseded marker
  recovery、旧事件静态身份、Web null baseline/自动恢复、Milvus URI/builders 与 fixture cleanup；
  当前不启动 Ticket 10，只允许形成单一实现提交、推送并等待对应 GitHub Actions 全绿。
- 首次发布候选 `af0e923` 的 CI `30784813677` 中 Web、Container、Security/SBOM 全绿，Python
  完成 1604 项后报告 9 败：CI Milvus 暴露在 19531 但未注入 Worker Settings，级联 8 个既有
  readiness/runtime 用例；Operation migration 的 workspace collation 期望漏列新
  `embedding_records`。修复以 CI 部署契约先 RED 后 GREEN，注入精确 URI 与 10 秒冷启动 readiness
  预算，并更新两个既有公开 readiness 形状和 migration 身份集合。
- 修复后原 8 个 Worker/Runtime/Upload 失败全部定向通过，扩大后的四文件真实集成为
  `170 passed, 1 skipped in 839.22s`；唯一 skip 为显式未启动的真实 ClamAV。Provider close
  全量门禁另发现测试用 50ms read timeout 与 50ms sleep 竞争；生产状态机正确把已发出的第二请求
  标为 unknown。测试改为等待两个 active lifecycle 的确定性排队事实，连续 `10/10` 通过，随后
  全量 unit + contract 恢复为 `1099 passed, 1 skipped`。
- Ticket 09 最终单一提交 `73c2194` 已与 `origin/main` 精确一致且工作树为空；GitHub Actions
  `30786845917` 全绿：Python 13m02s、Web 2m36s、Container 1m23s、Security/SBOM 10s。
  Ticket 10 正式解锁，按既有《PRODUCT_FUSED indexing and CJK lexical documents》工单继续，
  不提前实现 Ticket 11 的 rights-first hybrid fusion。
- Ticket 10 首个 Domain RED 因 `build_controlled_product_text` 缺失而在 collection 阶段失败；
  GREEN 深模块统一 PRODUCT_FUSED 与 FULLTEXT 的受控文本来源，执行 Unicode NFKC、控制字符
  清理、空白折叠、casefold、集合去重/排序、大小预算和 canonical SHA-256。白名单只接受确认版
  ProductBrief 的安全字段与显式 approved labels/notes，raw prompt、敏感 claims、兼容性 claims
  等未批准字段不进入输出；等价中英混排内容产生相同文本与 hash。
- Embedding identity 已保持 IMAGE 兼容：IMAGE 仍按 AssetVersion + spec 生成原确定性 ID；只有
  PRODUCT_FUSED 将 input hash 纳入 ID，因此确认简报受控内容变化会产生新 Record，等价内容不会。
  Provider request/event 契约现在严格区分 IMAGE 无文本与 PRODUCT_FUSED 单图 + controlled text，
  typed event 同时绑定 ProductBrief Version 与 controlled-text hash；Deterministic/Alibaba 复用
  既有 Adapter seam，相关 Domain/Contract/Provider 聚焦门禁 `56 passed`。
- Ticket 10 migration `f5a1c3e7b902` 新增 fused provenance 与 `product_search_documents`，保存
  Product/Brief/AssetVersion/Rights/Embedding 精确身份、title/labels/OCR summary/confirmed brief
  summary/approved notes、retention 与状态；真实 MySQL `SHOW CREATE TABLE` 已证明
  `FULLTEXT ... WITH PARSER ngram` 且 Alembic schema drift 为零，migration integration
  `1 passed`。downgrade 在存在
  PRODUCT_FUSED 历史时拒绝，避免静默删除索引事实。
- Ticket 10 当前 checkpoint 尚未完成：下一切片必须实现 confirmed ProductBrief 原子 request
  service、Search Document/Embedding/Operation/Outbox unique-winner、Worker 对 controlled text 的
  authority load/commit、Rights stale/delete 收敛，以及中文/英文/混合 FULLTEXT 查询计划与真实
  incremental MySQL+Milvus 测试；在这些门禁和独立终审前不得提交或进入 Ticket 11。
- 本 checkpoint 的首轮全量 unit/contract 捕获 Provider 为比较 VectorKind 而反向导入 Domain；
  既有 dependency-boundary test 正确 RED。Adapter 改为只读取 Contracts enum value 后删除反向
  依赖，全量恢复为 `1104 passed, 1 skipped`；这条低耦合边界保持不变。
- Ticket 10 原子请求首个真实 MySQL RED 已准确命中缺失的
  `MySqlProductFusedIndexRequestService`。实现将 Ticket 09 请求模块加深为共享 IMAGE / PRODUCT_FUSED
  原子边界，IMAGE operation hash 保持原域兼容，PRODUCT_FUSED 使用独立域并绑定确认版与受控文本。
- 首次 GREEN 运行在测试 seed 的 `product_brief_fields.sensitive` 未转义处收到 MySQL 1064；这是
  测试装置错误而非业务行为结果。已仅转义该列名，下一次运行继续验证同一公开 seam。
- 原子请求、authority controlled-text load/commit、Rights DELETE_PENDING→DELETED、确认版变更
  增量替换与 CJK FULLTEXT 已逐条完成 RED→GREEN。确认版不变保持单一 Record/Document/Operation/
  Outbox；受控内容变化创建新 Record 并把旧 generation0 事实同步置为 STALE。
- MySQL literal 查询覆盖中文 `鎏金口红`、英文 `summer lipstick` 与混合 `鎏金 summer`；三者均由
  ngram FULLTEXT 返回唯一受控文档。首次 EXPLAIN 在空表选择 workspace 唯一索引，生产查询与
  计划门禁随后显式 `FORCE INDEX (ft_product_search_cjk)`，查询计划已转绿。
- Worker confirmed ProductBrief observation 已先 RED 证明缺少 fused request composition，随后新增
  独立 PRODUCT_FUSED collection spec/request service，但继续复用同一个 Durable executor、MySQL
  authority、Provider、Milvus adapter 和 delete handler；对应 unit seam 已转绿。

## 2026-07-21

### 已完成

- 重新确认项目目标为 Agent 应用开发实习作品。
- 确认公开 GitHub 和在线 Demo。
- 确认合法多品类素材可获得。
- 确认数据库使用 MySQL。
- 确定项目名称和产品定位。
- 确定 Python-first、Next.js Web、LangGraph 单 Agent。
- 确定 MySQL + Milvus + Redis + RabbitMQ + OSS 数据与运行边界。
- 删除原先 64 份学习型/旧目标架构 Markdown 和旧验证脚本。
- 重建产品、架构、数据、AI、工程、部署、路线图、ADR 和研究文档。
- 明确 Open PicsetAI MIT 复用边界。
- 明确 Fashion-AI 无许可证，仅借鉴思想。
- 建立八阶段实施路线和 Release 1.0 验收标准。
- 建立 ADR、评测实验和事故复盘模板。

### 文档阶段完成状态（历史）

- 文档架构重建和最终检查已完成。
- 当前共有 37 份 Markdown，全部相对链接、索引和状态元数据检查通过。
- 5 份 ADR 状态均为 `accepted`。
- 已确认旧目录和旧 `scripts` 目录已清理。
- 已修正 MCP Server 被误画为 RabbitMQ Consumer 的部署图问题。
- 最终自动验收：3168 行 Markdown、29 份正式领域文档、0 个 H1/链接/索引错误。
- 旧方案残留检查通过：正式文档中不存在 `pgvector`、NestJS、RocketMQ、旧 `backend/`/`frontend/` 目录口径或非预期 PostgreSQL 表述。
- 该节点尚未创建业务代码、容器、IaC 或云资源。
- 已将项目根目录接入 Obsidian，配置相对 Markdown 链接、自动更新链接、文档模板和知识图谱。
- 已通过 `.gitignore` 排除 Obsidian 个人工作区、缓存和回收站内容。
- 已在 Obsidian 本机 Vault 注册表中保留原有 Vault，并新增当前项目 Vault。
- 已实际打开 `docs/README.md`，窗口标题确认显示为 `README - mine - Obsidian 1.12.7`。
- Obsidian 启动后生成的 `.obsidian/workspace.json` 已按规则排除，不影响公开仓库。

### 当时的下一步（历史）

- 文档架构确认后进入实施路线 Phase 0。
- 在编写代码前先固定仓库初始化、版本和 Phase 0 任务清单。

### Phase 0 实施进展

- 已建立 Python workspace、FastAPI、Celery Worker、Scheduler、MCP Server 和 Next.js 服务入口。
- 已建立 MySQL、Redis、RabbitMQ、MinIO、Milvus、etcd 和 OpenTelemetry 本地 Compose。
- 已建立 Ruff、pytest、ESLint、TypeScript、Next build、Gitleaks、SBOM 和 OpenAPI drift CI。
- 已定位并修复 Milvus 与 MinIO 凭证不一致问题。
- 已为全部应用服务和基础设施补充健康检查、依赖条件和重启策略。
- 已将 MinIO 与 Milvus 纳入 Control API readiness。
- 已新增 `scripts/verify_phase0.py` 作为主机侧完整栈验收入口。
- 完整 Compose 已重建成功，12 个服务均为 healthy，主机侧 11 项 HTTP/TCP 验收全部通过。
- Control API readiness 已实测返回 MySQL、Redis、RabbitMQ、MinIO、Milvus 全部 `ok`。
- 日志审查已识别非 root、Web 离线启动、OTLP 监听地址和 RabbitMQ 启动竞态四项待硬化问题。
- Web standalone 镜像已构建为 311 MB，较原约 991 MB 镜像明显缩小。
- standalone Web 容器启动成功，但健康检查因 `localhost` 的 IPv6/IPv4 解析差异误判；已将探针固定为 `127.0.0.1`。
- 修复后 Web 容器已切换到 standalone 镜像并达到 healthy，`scripts/verify_phase0.py` 的 8 个 HTTP 与 3 个 TCP 入口全部通过。
- 最终门禁第一组通过：Ruff format/check、9 项 pytest、OpenAPI 导出、ESLint 和 TypeScript typecheck。
- pytest 仅有 FastAPI TestClient 间接触发的上游 `StarletteDeprecationWarning`，不影响 Phase 0 验收。

### Phase 0 最终验收

- 完成五轴审查：correctness、readability、architecture、security、performance。
- MCP Host、Port 和 Transport 已纳入统一 Pydantic 配置；Secret file source 已接通并有优先级测试。
- 本地缓存改为 BSD-3-Clause 的 Valkey 8.1.8，保持 Redis 协议和客户端契约。
- Compose 项目名固定为 `commercevision`，全部主机端口默认只绑定 `127.0.0.1`。
- Python、Web 和 OTel 容器均以非 root 用户运行；Web 用户组已验证为 `nodejs`。
- Python 镜像不再持久化 `UV_INDEX_URL`，Web 运行镜像不包含 Corepack/pnpm 构建变量。
- LangGraph 升级到 1.2.9，pytest 升级到 9.1.1；Python 和 pnpm 漏洞审计均为 0。
- PostCSS 固定为已修复且通过供应链冷却期的 8.5.20。
- 最终测试为 12 passed；Ruff、ESLint、TypeScript、Next build、OpenAPI 和 Compose 配置全部通过。
- 最终完整栈使用 `commercevision-*` 容器和卷启动，12 个服务全部 healthy，8 个 HTTP 与 3 个 TCP 验收全部通过。
- Web standalone 镜像最终为 311 MB。

### 后续

- Phase 0 已完成；下一步只能在明确启动 Phase 1 后实现领域状态与 Durable Agent Runtime。
- 旧 `compose_*` Docker 卷为本轮迁移前的本地数据保留，不属于当前运行栈，未自动删除。

## 2026-07-22

### Phase 1 启动

- 用户明确启动 Phase 1，并要求按完整高可用实现，不接受最小实现或演示替代。
- Phase 1 范围固定为领域状态、MySQL 持久化、可靠消息、MySQL Checkpointer、Interrupt/Resume、恢复调度和 Fixture 执行链路。
- 真实模型、生图 Provider、多模态检索和产品级工作台仍按路线图留在后续阶段。
- 已复核 Phase 0 代码、依赖、Compose 和 Phase 1 架构文档。
- 已确定新增共享 `commercevision-persistence` 包，保持纯领域层与 SQLAlchemy 基础设施解耦。
- 已固化业务状态与 Checkpoint 分离、事务 Outbox、Inbox 去重、Step Lease 和事务外工具执行边界。
- 已建立纯领域 Workflow/Step/Attempt 状态机、版本和租约实体及公开 Pydantic Contract。
- 已建立 `commercevision-application` 与 `commercevision-persistence` 包，完成 MySQL ORM、Repository、Unit of Work、Idempotency、Outbox/Inbox、DLQ、Audit 和 Recovery 协调。
- 已建立 Alembic 基线迁移，11 张 Phase 1 业务/运行时表已在本地 MySQL 8.4 落库，`alembic check` 无漂移。
- 已实现禁用 Pickle 的 MySQL LangGraph Checkpointer，同步/异步接口、pending writes、父链、复制和线程级安全删除均具备实现。
- 已实现可拒绝重规划、重新生成和两次人工审批的 Fixture LangGraph。
- 已在真实 MySQL 上完成 `INGESTING -> AWAITING_PLAN_APPROVAL -> AWAITING_RESULT_APPROVAL -> COMPLETED` 全流程冒烟，最终仅产生 1 个有效 Tool Attempt。
- 已恢复并确认当前阻塞根因：模型 `UTCDateTime` 已声明 MySQL `DATETIME(6)`，现有迁移数据库仍是无小数秒 `DATETIME`，需要新增全表时间列精度迁移并补齐 Outbox、Lease、Retry 回归测试。
- 已从 ORM 元数据枚举出 11 张表共 33 个 `UTCDateTime` 列，MySQL 方言编译结果全部为 `DATETIME(6)`；下一步对真实 schema 生成并核验对应迁移。
- 已对真实 `commercevision` schema 完成列级审计：33 个时间列全部仍为 `DATETIME(0)`；同时发现 Alembic 默认类型比较无法识别 `fsp` 漂移，已将自定义 drift 门禁纳入本次修复范围。
- 已新增 `7f4a2b9c1d6e` 迁移，按 11 张表聚合修改全部 33 个时间列为 `DATETIME(6)`，使用 MySQL 8.4 实测可执行的 `ALGORITHM=COPY, LOCK=SHARED`。
- 已新增 Alembic MySQL `fsp` 自定义比较器；在测试库伪造“版本在 head、schema 仍为 `DATETIME(0)`”后，`alembic check` 成功逐列识别全部 33 个漂移。
- 已补充 UTC 归一化、naive datetime 拒绝、schema 精度、微秒 round-trip、Outbox 即时可见、Inbox Lease 精确到期和 Step Retry 精确就绪测试。
- 测试库迁移已完成 upgrade、downgrade、upgrade 往返，最终 33 个时间列精度均为 6，`alembic check` 无漂移。
- 当前完整 Python 门禁通过：35 项 pytest、73 个 Ruff format 文件和全仓 Ruff check 均通过；仅保留既有 Starlette TestClient 上游弃用警告。
- 本地 `commercevision` 主库已升级到 `7f4a2b9c1d6e`，33 个时间列全部为 `DATETIME(6)`，Alembic 无 schema 漂移。
- 迁移后 12 个 Compose 服务保持 healthy；Control API readiness 的 MySQL、Redis、RabbitMQ、MinIO 和 Milvus 全部为 `ok`，Scheduler heartbeat 正常。
- 已固定基线迁移为显式 `DATETIME(0)`，避免历史迁移随运行时 `UTCDateTime` 实现变化；独立空数据库验证旧 head 为 33 个 `DATETIME(0)`、新 head 为 33 个 `DATETIME(6)`。
- 已修复 GitHub Actions 集成测试 DSN，使 MySQL 集成测试连接 CI 的 `3306` 独立测试库而不是默认本地 `13316`；CI 迁移后新增显式 `alembic check`。

### Phase 1 最终验收

- 更新后的 `migrate`、API、Worker 和 Scheduler 镜像已构建并部署到本地 Compose。
- 完整 HTTP Agent 流程已实测通过两个人工关口并达到 `COMPLETED`，最终仅有 1 个有效 Tool Attempt。
- 已在 Creative Plan 和 Results 两个人工等待点分别停止 Worker，在 Worker 离线期间提交审批，再启动新 Worker；两次均从持久 Checkpoint 恢复并完成。
- 迁移后 Outbox 无 ready unpublished、future unpublished 或 active lock 残留。
- 12 个 Compose 服务全部 healthy，主机侧 8 个 HTTP 和 3 个 TCP 检查通过。
- OpenAPI 已重新导出，包含 11 条健康、元数据和 Workflow 路径。
- Phase 1 已完成；Phase 2 尚未启动。
- 最终五轴代码审查通过：correctness、readability、architecture、security、performance 均无阻断问题。
- 最终门禁：35 passed、Ruff 全通过、Alembic head/漂移检查通过、33 个时间列均为 `DATETIME(6)`、Markdown 链接与 OpenAPI 稳定性检查通过、Outbox 无未发布或活动锁残留。

### GitHub 与 Phase 2 启动

- 已确认 GitHub CLI 2.96.0 安装于 `C:\Program Files\GitHub CLI\gh.exe`，当前 shell 仅未刷新 PATH。
- 已拒绝使用聊天中暴露的 fine-grained PAT，并改走 GitHub CLI 官方 OAuth 设备授权。
- 已创建公开仓库 `wcl1314520/commercevision-agent`，配置 `origin` 并推送 `main`。
- GitHub OAuth 基础权限缺少 `workflow` scope；改为生成未暴露的 30 天 classic PAT，只授予 `repo`、`workflow`、`read:org`，通过浏览器剪贴板直接写入 Windows keyring并清空剪贴板。
- 当前仓库单独配置 v2rayN HTTP 代理 `127.0.0.1:10809`，解决 Git HTTPS connection reset，不修改系统或全局 Git 代理。
- GitHub Actions 首次运行 `29905132767` 全绿：Python、Web、容器构建、Security/SBOM 均成功。
- 已创建根目录 `CONTEXT.md`，定义 Task Asset、Foundation Asset、Rights Record、ProductBrief、Brand Profile 和 Retrieval Citation 等统一领域术语。
- 已创建 ADR-006，正式确定任务资产保存 72 小时；基础资产保存至管理员删除或权利到期。
- 已同步更新数据架构、ADR 索引、文档索引和研究结论。
- 已生成并锁定 `PLAN.md`，覆盖资产直传隔离、权利、安全校验、商品理解、Brand Profile、增量 Embedding、混合检索、MCP、删除/重建、观测与评测。
- 已将 GitHub 远程基线、Phase 2 规格、工单、独立上下文实现和最终验收加入持久执行计划。

### Phase 2 规格恢复

- 用户再次确认任务资产保存 72 小时，基础资产保存至管理员删除或权利到期。
- 不使用或回显聊天中暴露的 GitHub Token；继续使用 Windows keyring 中现有 `gh` 登录态。
- 已确认五个测试接缝：HTTP、Durable Worker/Event、真实基础设施检索、MCP、Provider Adapter。
- 恢复时 `.scratch/phase-2-assets-retrieval` 不存在；将从已提交的锁定计划和领域文档重建。
- 当前进入 `/to-spec`，随后按 `/to-tickets` 和逐 Ticket 独立上下文 `/implement` 推进。
- 已读取 `to-spec`、`to-tickets`、`implement`、`tdd`、`codebase-design` 和 `domain-modeling` 技能约束。
- 已确认现有可靠执行、UoW、HTTP Header、Worker 和 Scheduler 模式可作为 Phase 2 的主要实现接缝。
- 已启动三个只读独立上下文审计：领域/Schema、API/Worker/Scheduler、MinIO/Milvus/Provider/MCP/评测。
- 三个只读独立上下文审计已完成，并已吸收其领域、迁移、可靠执行、Milvus、MCP 和评测结论。
- 已生成 `.scratch/phase-2-assets-retrieval/spec.md`，覆盖 70 条用户故事，以及状态机、Schema、HTTP/Event/MCP/Web/Provider 契约、检索、删除、重建、观测和测试门禁。
- 已在 `CONTEXT.md` 增加 `Asset Version` 与 `Upload Session` 两个已确定的领域术语。
- Phase 2 spec 结构校验通过：70 条用户故事、完整标准章节、领域术语检查和 `git diff --check` 均通过。
- Phase 2 spec 已提交为 `31a555f` 并推送到 `origin/main`；正在验证对应远程 CI。
- Phase 2 spec 的 GitHub Actions 运行 `29908574921` 已全部通过。
- `/to-tickets` 已向用户展示 17 个 blockers-first 纵向 Ticket；按流程等待粒度与依赖关系确认后才发布 Issue 文件。

### Phase 2 Ticket 发布

- 用户已明确批准 17 个 Ticket 的粒度与依赖关系。
- 已按 blockers-first 顺序发布 17 个 Local Markdown Ticket。
- Ticket 基线已提交为 `f0dffb0` 并推送到 `origin/main`。
- Ticket 基线 GitHub Actions 运行 `29910220853` 已全部通过。
- Phase 10 已完成，Phase 11 独立上下文实现已开始。
- Ticket 01 已在不继承会话历史的独立 Worker 上下文中启动，执行 `/implement`、TDD、完整测试与代码审查。
- Ticket 01 初版提交 `3bc4a4d` 的主控双轴审查未通过：发现 Phase 1 通知事件误入 DLQ、普通 Worker 异常可能被 ACK 后搁置、队列配置与事件 Contract 边界不完整。
- 已将全部 Critical/Required 审查意见退回原 Ticket 01 独立上下文，要求补齐 MySQL 主导重试闭环、全量 Phase 1 事件契约、严格配置、真实 Durable Worker/Event 接缝测试与架构文档，并 amend 原提交。
- Ticket 01 修复已 amend 为 `088203f`，保留为该 Ticket 的单一实现提交；独立 Standards、Spec 和五轴质量审查均批准。
- 主控复验通过：83 项 pytest、Ruff format/check、Python 依赖审计、Compose 配置和工单验收；仅有既有 Starlette 弃用警告，Pyright 仍未安装。
- Ticket 01 与状态日志已推送；最新 GitHub Actions 运行 `29921552040` 已全部通过，前一运行因并发取消策略被后续提交正常取代。
- Ticket 01 已解除 Phase 2 后续实现阻塞。
- Ticket 03 已在新的独立 Worker 上下文中启动，范围固定为 Product/SKU Catalog、Workspace 隔离、MySQL/HTTP Contract、OpenAPI 和 Web 工作台。
- Ticket 03 已完成并 amend 为 `856f57b`，包含 Product/SKU 共享外部身份注册表、复合 Workspace 外键、并发幂等快照、运行时 Web Proxy、过期元数据和 9 项 Playwright 测试；独立 Standards、Spec 和五轴质量审查均批准。
- 主控复验通过：94 项 pytest、9 项 Playwright、Web lint/typecheck/build、Ruff、Python 依赖审计、迁移 upgrade/`alembic check`、OpenAPI/前端类型漂移和 Compose 配置；仅有既有 Starlette/httpx 弃用警告。
- Ticket 03 与状态日志已推送；GitHub Actions 运行 `29952486669` 的 Python、Web、容器构建、Secret Scan 和 SBOM 全部通过。
- Ticket 03 已解除 Ticket 04、05、06、07、08、09 之外的直接依赖，并正式解锁 Ticket 02 的实现。
- Ticket 02 已经只读依赖审计确认无隐藏阻塞，并在新的独立 Worker 上下文中启动，范围固定为 Durable Operation、恢复控制面、DLQ Replay、独立 Scanner 与 Operator HTTP。
- 一次 `wait_agent` 空目标调用因参数校验失败；没有启动、终止或修改任何 Agent/文件，后续只使用非空 Agent ID。
- Ticket 02 独立 Worker 已产出提交 `f62ec5f`；主控正在等待固定比较点 `a6d597c...f62ec5f` 的 Standards 与 Spec 双轴审查。
- 独立安全门禁发现 Next.js 15.5.20 存在 3 个 High、5 个 Moderate 公告；已将 Next.js 与 `eslint-config-next` 精确升级到 15.5.21 并成功重建锁文件，修复将作为 Ticket 之外的独立安全维护提交。
- 首次 `pnpm install --frozen-lockfile` 在本地依赖拉取阶段超过 120 秒而被终止；没有修改业务代码，下一次使用更长超时继续确定性安装。
- 使用更长超时后 `pnpm install --frozen-lockfile` 成功；Next.js 15.5.21 安全升级已通过 `pnpm audit --audit-level=moderate`、Web lint、TypeScript、生产构建和 9 项 Playwright 回归。
- Ticket 02 主控双轴审查发现身份可信边界、未知结果对账、终态 DLQ、恢复公平性、旧数据迁移与 Scanner 隔离等阻断问题；全部意见已退回原独立 Worker，要求红绿修复并 amend 原提交。
- Ticket 02 修复过程中独立 Worker 的 Codex 响应流连接中断；约 45 个文件的实现与测试改动仍保留在工作树，已恢复同一个 Agent ID 和上下文继续验证与 amend，没有重建上下文或丢弃工作。
- Ticket 02 第一轮修复已 amend 为 `3134501`；主控复验通过 151 项 pytest、Ruff、Python 审计、Alembic upgrade/check、OpenAPI 稳定性、Phase 0/1 验证和 Compose 配置。
- 第二轮独立 Standards/Spec 审查确认第一轮大部分阻断已关闭，但仍发现生产 Worker 未在启动时装配 executor、Provider task identity 不可持续对账、累计时限在 claim 时未强制、已发布恢复事件仍可能队头饥饿、回放族谱静默截断和可信网关缺少双钥轮换；已全部退回原 Worker 继续 TDD 并 amend。
- 第二轮修复已 amend 为 `91c428e`；主控最终提交复验通过 165 项 pytest 和 Ruff。
- 主控 MySQL 因此前执行过同 revision 的中间版迁移而出现“版本号在 head、schema 内容仍旧”的本地漂移；确认 `durable_operations` 与 `dead_letter_replays` 均为 0 行，将通过受控 downgrade/upgrade 重建未使用的 Ticket 02 表并保留 Phase 1 数据。
- 本地主库已通过兼容索引完成受控 `downgrade 9a7e3c1f5b20 -> upgrade head`，最终 `alembic check` 无漂移，Phase 1 端到端验证继续通过。
- 第三轮独立审查将 Ticket 02 剩余问题收敛为四项：成功结果未持久化 Provider request ID、execution replay 保留了旧未耗预算、reconciliation replay 同时清零计数并扩张上限、迁移会误接收非字符串或超长 workspace JSON；已退回同一 Worker 补真实 MySQL 回归并 amend。
- 第三轮修复已 amend 为 `859d958`，主控复验通过 172 项 pytest、Ruff、MySQL drift、迁移/replay 定向测试、Python/pnpm 审计和 Web 构建。
- 第四轮独立审查继续发现六个边界缺口：非成功 Provider 结果 provenance、IntegrityError 分类、真实 MySQL Scanner 隔离、租约刚过期的 late result、对账 `retry_at <= now`、迁移中的制表符/换行 workspace 规范化；已全部退回同一 Worker 继续 TDD。
- 第四轮修复 Worker 在完成大部分实现和测试后从多 Agent 注册表消失，原 Agent ID 返回 `not_found`；约 1200 行修改及新增 Integrity/Scanner 测试完整保留在共享工作树。
- 已启动新的无历史独立恢复 Worker，只允许审计和完成现有 Ticket 02 diff、运行门禁并 amend `859d958`，禁止重做、清理或提交五个主控文件。
- 恢复 Worker 已将第四轮修复 amend 为 `18cce7f`；主控复验通过 204 项 pytest、59 项定向真实 MySQL、迁移往返、Ruff、Phase 0/1 和漏洞审计。
- 最终独立复审仍复现 replay event 红elivery 重复授权、未知查询异常提前终态、late reconciliation provenance、通用/Catalog UoW Integrity 分类未统一、损坏 JSON 迁移未防护五项阻断；已退回同一恢复 Worker 继续 amend。
- 第六轮修复已 amend 为 `4b04485`，主控复验通过 232 项 pytest、Ruff 和 Phase 0/1；Standards release gate 已批准。
- Spec release gate 仍复现 Transport DLQ replay 被误走终态 operation replay、Repository 在 `save()` 立即执行 SQL 时绕过 Integrity 分类两项阻断；已退回同一恢复 Worker 完成最终 TDD。
- 第七轮修复已 amend 为 `bd77392`，全量 238 项与 focused MySQL/recovery 134 项通过；Spec release gate 已批准。
- Standards release gate 通过 deterministic interleaving 发现 Transport replay 终态失败未继承 source DLQ 祖先、marker winner 输掉 provider claim 时仍抛异常两项并发缺口；已退回同一恢复 Worker 修复。
- 第八轮修复已 amend 为 `7260509`，242 项全量与 138 项 focused MySQL/recovery 通过；Spec release gate 已批准。
- 最终 Standards gate 发现 replay claim 仍依赖 `source_aggregate_version + 2` 推导，合法的终态后 generation/provenance 写入会让 prepared-but-unclaimed replay 被误判并永久搁置；已要求同一 Worker 改为显式持久 replay preparation/claim 状态。
- 第九轮修复已 amend 为 `27c521f`，显式 replay lifecycle 取代版本偏移，244 项全量与 147 项 focused MySQL/recovery 通过。
- 最终结构审查发现 `CLAIMED` 后崩溃仍可能未收敛到 `COMPLETED`、128 字符 workspace 生成的 replay 幂等 scope 超列长、签名 actor ID 未限制为 1–128 字符三项边界；已退回同一 Worker 修复。
- 第十轮修复已 amend 为 `4da0fb5`，248 项全量与 151 项 focused MySQL/recovery 通过。
- Security release gate 发现 API 的大小写敏感 workspace 授权与 MySQL 默认 `utf8mb4_0900_ai_ci` 过滤不一致，可造成跨 workspace 读取；同时大写 UUID 路径会破坏 replay 幂等。已要求对所有 workspace 查询建立统一精确比较契约并规范化 UUID。
- 第十一轮安全修复已 amend 为 `acb4417`，255 项全量与 193 项 focused recovery/MySQL 通过，9 个 workspace 列改为 binary-exact，并完成 canonical UUID replay。
- 安全复审发现迁移仍会 trim 后改派 workspace 身份、Unicode workspace 无可靠 HTTP wire 表示、Replay/Operation/Outbox 关系缺少 workspace 复合 FK。已确定 workspace ID 为 1–128 字符 ASCII 无空白 token，并要求迁移保留原值或 legacy、补齐所有复合所有权 FK。
- 第十二轮安全修复已 amend 为 `1f499dc`，290 项全量与 186 项 focused 安全/MySQL 通过，workspace ASCII contract 与复合所有权 FK 已全链路落地。
- 最终 Spec gate 发现 dead-letter UUID 在查库后才 canonicalize，重音伪 UUID 可被 `ai_ci` 命中；同时 Standards 建议历史 migration 内固化 workspace regex。已退回同一 Worker 做严格 pre-lookup UUID 校验和 migration 去运行时依赖。
- Ticket 02 最终修复已 amend 为 `9b88493`：dead-letter UUID 在数据库查询前完成严格解析与 canonicalize，历史迁移固定 workspace 正则，不再依赖可演进的运行时代码。
- 独立 Standards 与 Spec 复审均批准；最终实现覆盖显式 replay 生命周期、持久 Provider provenance、累计执行/对账预算、Scanner 隔离、可信双钥操作员身份、严格 workspace 所有权和可恢复生产 Worker 装配。
- Ticket 02 最终全量 Python 门禁通过 302 项 pytest；Ruff format/check、真实 MySQL 迁移与 drift、Phase 0/1 回归、Python 安全审计及相关 Web 门禁均通过，仅保留既有 Starlette 弃用警告。
- Next.js 与 `eslint-config-next` 的 15.5.21 安全升级已作为独立提交 `083004d` 落地；`pnpm audit --audit-level=moderate` 返回 0 个已知漏洞，Web lint、TypeScript、生产构建和 9 项 Playwright 回归通过。
- Ticket 02 与 Ticket 03 的依赖现已满足，下一项实现为 Ticket 04：Direct Upload、Quarantine 与三段式 Finalize。
- 组合 HEAD 复验已通过 Ruff format/check 与 302 项 pytest；本地主库的 `alembic check` 随后确定性复现同 revision schema 漂移。
- 漂移修复前审计确认 Alembic revision 为 `b1c8e4f2a703`，`durable_operations`、`dead_letter_replays` 和 `dead_letter_messages` 均为 0 行；`outbox_events` 641 行、`workflows` 31 行，需要在保留 Phase 1 数据的前提下受控重建 Ticket 02 revision。
- Schema Inspector 与原生 `INFORMATION_SCHEMA` 均确认本地主库保留的是 Ticket 02 中间版：相关关系仍为单列外键，缺少最终复合 Workspace 唯一约束、外键及配套索引；这与 `alembic check` 的完整漂移清单一致。
- `tests/integration/test_operation_migration_mysql.py` 在独立空库中 4 项全部通过，已排除最终迁移代码、模型元数据或 MySQL 复合约束反射本身存在漂移。
- 首次本地 downgrade 已在删除两个空 Ticket 02 表、来源外键和兼容索引后，于缺失 CHECK 约束处停止；剩余 suffix 必须删除临时唯一约束与 Ticket 02 列，并把五张既有 Workspace 表及 Idempotency Scope 恢复到父 revision 的默认排序规则。
- 本地 API、Worker、Scheduler、MCP 与 Web 在 schema 维护开始时仍处于运行状态；完成手工 suffix 前将停止应用层服务，仅保留 MySQL 等基础设施，避免迁移窗口出现并发写入。
- 应用层服务停止后已手工完成非事务性 downgrade suffix，Alembic stamp 到 `9a7e3c1f5b20`，再由正式 migration 升级到 `b1c8e4f2a703`；`alembic check` 已恢复为无漂移。
- 升级后 17 张业务表的行数与维护前逐表一致，包括 31 个 Workflow、641 个 Outbox/Inbox、336 个 Checkpoint 和 1804 个 Pending Write；两个 Ticket 02 表继续为 0 行，没有业务数据丢失。
- 12 个 Compose 服务已恢复 healthy；Phase 1 公共 HTTP 全流程通过两个人工审批并完成，Phase 0 的 8 个 HTTP 与 3 个 TCP 健康验收全部通过。
- Ticket 02 组合 HEAD 的最终本地门禁全部通过：302 项 pytest、Ruff、Alembic、Python/pnpm 漏洞审计、OpenAPI drift、Web lint/typecheck/build、9 项 Playwright、Compose config 与完整健康检查。
- Ticket 02、Next.js 安全补丁和状态记录已推送至 `origin/main`；GitHub Actions 运行 `30071635068` 的 Python、Web、容器构建、Secret Scan 与 SBOM 全部通过。
- GitHub Actions 提示 `actions/checkout@v4`、`actions/setup-node@v4` 等仍以 Node 20 为目标并被 runner 强制到 Node 24；当前不阻断，记录为后续工程基线维护项，不混入 Ticket 04。
- Ticket 04 已在不继承前序会话历史的全新 Worker 上下文中启动，固定比较点为 `1601320`；范围限定为 Direct Upload Session、对象存储 Adapter、Quarantine、三段式 Finalize、Durable 恢复和 Web 直传。
- 用户中断后 Ticket 04 Worker 结束但未返回总结；共享工作树保留完整未提交实现。已恢复同一个独立 Agent ID 和上下文，要求基于现状继续门禁、对抗审查、修复并形成单一 Ticket 提交。
- 两个只读审查已形成 finalize 并发/崩溃清单与对象存储安全清单，并已作为恢复 Worker 的强制审查目标，不改动共享工作树。
- 恢复后的原 Ticket 04 Worker 连续多次 `wait_agent` 超时，且无测试进程或实现文件变化；强制状态请求仍无响应。已关闭该 Agent，工作树中的约 65 个文件完整保留，准备由新的无历史恢复 Worker 接管。
- 新的无历史 Ticket 04 恢复 Worker 已启动，只允许审计和完成现有 diff、执行门禁与独立审查并创建单一提交；三份主控计划文件继续排除在其所有权和暂存范围外。
- 恢复 Worker 已拆分近千行资产编排与对象存储模块，补充 OSS live gate，并通过 focused/full pytest 与两轮 Playwright；随后再次失联。现有实现与绿色缓存均保留，下一上下文只负责审查、收尾和提交。
- 窄范围 Ticket 04 Finisher 已在新的无历史上下文启动；职责仅为固定比较点双轴/五轴审查、阻断修复、最终门禁、Ticket 状态和显式路径单一提交。
- 窄范围 Finisher 同样在无本地活动时失联；已关闭。主控正式接管当前 Ticket 04 diff，后续不再改派实现上下文，只使用只读审查 Agent 辅助发现问题。
- 主控复验通过 42 项 focused Domain/Adapter/HTTP/真实 MySQL+MinIO/迁移测试及 349 项全量 pytest；真实 OSS live contract 因未配置临时凭证按设计跳过，只有两项上游弃用警告。
- 本地主库随后复现 Ticket 04 同 revision schema 漂移：`d4e7a1c9b205` 已登记但缺少最终 Upload Session 目标对象列/唯一约束；开始按空表审计和受控 revision 重建处理。
- Ticket 04 表并非空：存在 1 条 `catalog-demo` 已 Finalized 会话及对应 Asset/Version/Object。不能通过 downgrade/drop 重建；其预留版本可由 `finalized_asset_version_id` 恢复，目标位置事实可由对应 `asset_objects` 行回填，改用保数据列级修复。
- 深入审计确认旧 `asset_objects` 行仍在 quarantine，而不是最终 retained 位置；已暂停 API、Worker、Scheduler 和 MCP 写入面，改为先做对象存储条件复制，再补齐 Upload Session 列/约束并同步对象事实，避免把中间版不变量固化进最终 schema。
- 本地兼容修复已完成：目标对象按最终 FOUNDATION key 复制并持久化精确 Version ID/ETag，旧 quarantine 对象按 ETag 条件删除；四张资产表行数均保持 1，11 个 Upload Session CHECK、目标列与唯一约束完整，`alembic check` 无漂移。
- Ticket 04 当前门禁通过：Ruff format/check、349 项全量 pytest（OSS live 因无临时凭证按设计跳过）、Python/pnpm 漏洞审计、OpenAPI/TypeScript 生成、Web lint/typecheck/proxy/build、13 项 Playwright 和 Compose config。
- 三个并行只读 Standards/Spec/Security 审查上下文均因 Codex 后端 response stream 断开而未返回结论；该外部故障不影响共享工作树，主控继续五轴审查并将在固定 diff 上缩窄重试。
- 主控在五轴审查中确认 OSS readiness 会错误请求 MinIO 专用路径；已按公共 `probe_dependencies` 接缝完成红绿 TDD，MinIO 保持严格健康探针，OSS 根端点的 `400/401/403` 作为匿名网络可达证据，超时、连接错误与其他失败仍降级。
- Adapter 合约新增 MinIO/OSS 缺 Bucket 分类测试并完成红绿修复：`NoSuchBucket` 统一为 `StorageUnavailableError`，不再被 404 误归类成用户对象缺失。
- 真实 MySQL 回归复现带重音伪 Upload Session UUID 命中规范行；现已建立精确连字符 ASCII UUID 领域边界，请求关联 ID 与 Upload Session/Asset 查找在进入 idempotency 和 Repository 前统一规范化，focused 16 项通过。
- MinIO/OSS 实现、存储工厂和初始化 CLI 已从模型 `commercevision-providers` 拆入独立 `commercevision-object-storage` workspace；API 依赖更新，Worker 移除当前无用 SDK 依赖，锁文件与 editable 环境同步后 focused 门禁通过。
- 独立审查指出 Task hard-retention、OSS 非原子删除和锁前陈旧时钟三项阻断；均已按 red-green TDD 修复。Task 过期的 copy-after-crash 现在返回 410，不落 Asset/Version/Operation，并在事务外清理源/目标对象；OSS 无 Version ID 时条件删除失败关闭。
- Ticket 04 最终 Python 门禁已扩展为 362 项：361 通过，真实 Alibaba OSS live contract 因无临时凭证按设计跳过；Ruff format/check、Alembic drift、Python 漏洞审计、OpenAPI 与 Web 类型漂移均通过。
- Web 最终门禁通过 lint、TypeScript、Proxy 路由、生产构建、依赖审计和 13 项 Playwright；桌面 1265px 与移动 375px 视口均无横向溢出，视觉检查未发现控件重叠。
- Compose 已用新增 object-storage workspace 完整重建；API、Worker、Scheduler、MCP、Web、MySQL、MinIO、Milvus、RabbitMQ、Valkey、OTel 全部 healthy，Bucket 初始化与 Alembic 迁移均以 0 退出，Phase 0/1 验证继续通过。
- 在规范入口 `http://localhost:13000` 完成真实浏览器 1×1 PNG 冒烟：直接 PUT、Finalize、刷新恢复成功；MySQL 事实为 Upload Session `FINALIZED`、Asset/Object `QUARANTINED`、Durable Operation `PENDING`、Outbox `PUBLISHED`。非规范 `127.0.0.1` Origin 被精确 CORS 白名单拒绝，失败会话已显式 Abort。
- 固定比较点 `1601320` 的最终独立 Standards/Spec/Quality 复审已启动；复审通过后才更新 Ticket 状态并提交。
- 针对复审阻断补齐 Durable Upload Cleanup：过期/中止会话在同一事务创建 `ASSET_DELETION` Operation、关联会话、写 Outbox，Worker 通过版本化 maintenance 事件执行精确源/目标删除并支持 outage/recovery 收敛。
- 对象存储 readiness 已改为认证控制调用：MinIO/OSS 均去重检查 Bucket、Versioning 和生产加密策略，空拓扑与缺失 Bucket 失败关闭；真实 MinIO 集成门禁通过，OSS live gate 在有临时凭证时先执行同一 readiness。
- 设置层红绿 TDD 固化 `connect timeout + read timeout < finalize lease`，OSS SDK 使用独立连接/读取 tuple，就绪探针使用独立短超时；39 项 Settings 测试、6 项 readiness Contract 和真实 MinIO readiness 均通过。
- Asset GET 新增跨 Workspace 404 集成断言并通过；对象存储测试夹具不再把测试体异常误吞为服务不可用 skip。
- Web 已补齐 Upload Session 创建幂等重放、版本冲突重载、刷新后真实 Operation 查询和持久化白名单；20 项 Playwright 与独立 Vitest 单元门禁此前均已通过。
- 真实浏览器刷新暴露 Web BFF 未提供 Phase 1 可信 Principal，Operation GET 被 API 以 401 拒绝；已按红绿 TDD 增加服务端 HMAC Principal 签发、部署级 Workspace allowlist、浏览器身份覆盖与 Secret 缺失失败关闭，准备重建真实栈复验。
- 最新 Web 门禁通过代理测试 4/4、Vitest 1/1、Playwright 20/20、lint、TypeScript、生成类型漂移、生产构建与 pnpm 漏洞审计。
- 完整 Compose 已按可信 BFF 配置重建并全部健康；真实浏览器刷新后恢复 `QUARANTINED` 与真实 `PENDING`，控制台无错误，API 日志确认 Operation GET 从原 401 修复为持续 200。
- Ticket 04 最终全量 Python 回归为 375 passed、1 个显式 OSS live skip、2 个上游弃用警告；Ruff 对 161 个文件的格式和静态检查通过。
- Alembic 代码 Head 与本地主库均为 `e2f6a9c4d801`，`alembic check` 无 schema drift；Python 与 pnpm 漏洞审计均为 0。
- Phase 0 的 11 项 HTTP/TCP/依赖验收、API 认证对象存储 readiness 和 Phase 1 完整人工审批 Workflow 均在最新 Compose 上通过。
- 主控审查发现 Compose Worker 仅消费 Workflow 队列会使 Durable Cleanup/Recovery 的 Maintenance 事件无人处理；新增部署 Contract 先红后绿，并改为显式消费 `workflow + maintenance`。
- 最新 RabbitMQ 运行证据：`commercevision.workflow` 与 `commercevision.maintenance` 各有 1 个 Consumer 且无积压；`commercevision.asset` 保持 0 Consumer、2 个待 Ticket 05 处理的 Validation 事件。
- 最终化后的隔离源对象删除已升级为原子调度的 `ASSET_DELETION` Durable Operation；`UPLOAD_PROMOTED` 执行只删除源对象。注入首次删除故障后由 Worker 收敛为 `SUCCEEDED`，保留目标对象仍可 HEAD。
- Task Asset 截止时间固定为 `min(Workflow.expires_at, Workflow.created_at + 72h)`；上传审计元数据独立保持 180 天，不再被 Task 对象截止时间截短。
- Upload Session/Asset HTTP 路由现在与 Operation 路由共用签名 Trusted Principal，业务 Actor 只取签名 Claims；浏览器伪造 Actor/Principal、缺失/伪造/跨 Workspace Principal 均有集成拒绝证据。
- Web 控制面请求和对象 PUT 都有明确超时；BFF 对 JSON 请求/响应限制为 1 MiB/2 MiB，并把截止时间覆盖到响应体读取。Operation 轮询采用有界指数退避，预算耗尽后提供显式刷新。
- Web 对已知 Session 的“放弃”持久化 abort 幂等键并调用服务端；定向 E2E 证明服务端终止成功后才清除本地恢复状态。中断 PUT 缺失对象会回到可重传的 `OPEN` 状态。
- Alembic Head 更新为 `f7a8c2d1e903`；主库和测试库 `upgrade/current/check` 均通过，无 schema drift。
- 最终本地门禁：Ruff 162 文件通过；`379 passed, 1 skipped`（仅真实 OSS live 凭证门禁）；Web Proxy 9/9、Vitest 5/5、Playwright 22/22、lint/typecheck/generated-types/build 通过；Python 与 pnpm 漏洞审计均为 0。
- 最新应用镜像已完整重建，Phase 0/1 通过。通过真实 Web BFF 创建并 Abort Upload Session 后，Maintenance Worker 使用共享对象存储身份把 Cleanup Operation 收敛为 `SUCCEEDED`；应用日志无 Error/Traceback。
- Ticket 04 最终 Standards 与 Security 独立审查通过；Architecture 审查发现终态清理早于 Presigned PUT 失效会留下迟到写入孤儿，以及 Worker readiness 未探测对象存储两项阻断。
- 两项阻断均建立确定性红灯：真实 MinIO 证明 Abort 后原 PUT URL 仍可写入且 Cleanup 事件过早可用；Celery 启动测试证明对象存储失败仍会进入 ready。修复后两项回归转绿。
- Durable Cleanup 现在以 `max(now, UploadSession.expires_at + 30s configurable grace)` 设置 Outbox `available_at`，Inbox 同时拒绝提前消费；过期、Finalize 源删除故障和 outage/recovery 测试均按该时间边界继续证明最终收敛。
- WorkerRuntime 保留与 Cleanup Executor 共用的对象存储 Adapter，启动时执行认证 readiness；探针失败会关闭已构建 Runtime、拒绝消费者启动且不写 readiness 文件。相关 Upload/Settings/Worker 聚焦套件 82 项全部通过。
- Architecture 复审继续发现 Cleanup 的 Operation deadline 从创建时计时，合法短预算可能在延迟事件可消费前耗尽。真实 MySQL 回归以 60 秒业务预算复现红灯；现在保持真实创建时间，同时把 deadline 定义为 `available_at + full execution budget`，82 项聚焦套件再次通过，原审查上下文复核为 PASS。
- 最新完整门禁通过：Ruff 162 文件、`384 passed, 1 skipped`、主库/测试库 Alembic Head 与 drift、Web Proxy 9/9、Vitest 6/6、Playwright 23/23、lint/typecheck/generated-types/build、Python 与 pnpm 漏洞审计、Compose config 和 staged secret prefix 扫描。
- API/Worker 已从当前源码重建并恢复 healthy；Worker readiness 文件明确记录 `object_storage=ok`、`ready=true`。Phase 0 的 11 项依赖验收与 Phase 1 完整 Workflow 再次通过，最近 API/Worker 日志无 Error/Traceback。
- Ticket 04 最终全量 Python 门禁为 `444 passed, 1 skipped`；唯一跳过项是需要真实阿里云临时凭证的 OSS live suite。Ruff、Web 19 项 unit、9 项 proxy、23 项 Playwright、类型检查、生产构建、Python/pnpm 漏洞审计和 Alembic drift 均通过。
- 六个最新应用镜像完成滚动替换，12 个 Compose 服务全部 healthy；Control API readiness 对配置、MySQL、Valkey、RabbitMQ、对象存储和 Milvus 均返回 `ok`，启动日志无 Error/Traceback。
- 两路独立最终复审均批准当前 Ticket 04 staged snapshot，未发现 P0–P2 阻断项。
- Ticket 04 独立实现提交为 `ca1b1d5`，已推送到 `origin/main`。
- 首次远程 CI `30176833610` 的 Python 与容器 Job 通过，但 Gitleaks 将测试幂等键误判为 API Key，Playwright 从 monorepo 根无法解析 Web workspace 二进制。
- CI 根因修复提交 `8c15291` 使用低熵测试夹具并显式从 `@commercevision/web` workspace 安装 Chromium；本地 19 项 Web unit 与精确 Playwright 安装命令通过。
- GitHub Actions 运行 `30177137257` 的 Python、Web、容器构建、Gitleaks 和 SBOM 全部通过；Ticket 04 正式完成并解锁 Ticket 05。
- Ticket 05 将在新的无历史独立 Worker 上下文中启动，范围固定为多类型资产验证、ClamAV、内容安全、来源证据、LoRA/Prompt/模型配置校验、受控 Promotion 与 Web 状态。
- Ticket 04 验收记录提交 `0436c40` 已推送；GitHub Actions 运行 `30183608967` 的 Python、Web、容器、Gitleaks 和 SBOM 再次全部通过。
- Ticket 05 已从干净基线 `0436c40` 在全新无历史 Worker `019f9c21-627c-7a71-b09c-b30f7c92a951` 中启动；实现上下文不拥有三份主控计划文件或 Issue 状态。
- 主控补充核对 ClamAV INSTREAM、SafeTensors 文件格式和 Alibaba Image Moderation 2.0 官方契约，并将 fail-closed、协议限流、Header-only 校验及大陆服务策略版本约束同步给 Ticket 05 Worker。
- Ticket 05 原执行上下文停滞后已保留共享工作树并切换到恢复 Worker `019f9c8a-a97c-73a3-b9f3-38aaa9a5fdd6`；恢复上下文正在按同一 Ticket 边界继续 TDD、审查和单独提交。
- 主控审查指出 Validation Executor 必须严格绑定 Operation Kind/Input Ref、Upload Session 的 Operation 所有权、验证策略版本与精确源对象身份。恢复 Worker 已新增独立 Target Binder 和规范输入哈希，`tests/unit/test_asset_validation_target.py` 当前 7 项通过。
- Validation Target/Evidence/Promotion 的漂移反例扩展后，Target 套件为 25 passed；本地格式、ClamAV、内容安全与 C2PA 聚焦套件合计 119 passed。
- 主控独立复验 Promotion 两类唯一键竞争、两个 Validation 执行器并发重放、租约过期后的 Worker 接管与迟到 Worker 返回，真实 MySQL/MinIO 聚焦门禁为 4 passed；每个阶段证据和受控对象均收敛为唯一事实。
- Asset Worker 工厂首轮聚焦回归为 56 passed / 10 failed；失败全部来自旧 production 测试默认订阅全部队列并以 `ASSET_VALIDATION` 代指任意 Operation，因而触发新增的真实 ClamAV/Alibaba/C2PA 失败关闭约束。修复策略是让非资产测试显式选择 workflow/maintenance，让资产 Worker fixture 提供完整验证依赖，不降低生产约束。
- 旧 production fixtures 已按队列职责修正，资产 Worker 使用完整失败关闭配置，非资产测试不再伪装为 Asset Worker；主控复跑 Worker Transport、Readiness 和 Settings 为 66 passed。
- Provider 包已声明并锁定 `alibabacloud-green20220302==3.2.4` 与 `c2pa-python==0.36.0`。主控实际导入两套 SDK，C2PA 原生 SDK 报告 0.89.0；Alibaba/C2PA Contract 套件为 45 passed，生产工厂不再依赖未安装的延迟导入。
- 多类型 Web 上传与 Validation 状态呈现完成后，主控 Web 门禁为 unit 26/26、proxy 10/10、TypeScript 通过。首次 lint 扫描了 Playwright `playwright-report/trace/assets` 生成代码并报告 182 errors；修复方向是显式忽略测试报告目录并清理生成物，不调整源码规则或豁免。
- ESLint 已显式忽略 Playwright 生成报告目录，源码规则未放宽；复跑 lint 通过。
- 修正多类型上传的精确表单定位器后，主控 Playwright 全量门禁为 27/27，通过图片、SafeTensors LoRA、Prompt JSON、恢复、验证拒绝、可重试故障和 `PENDING_REVIEW` 人工复核场景。
- Compose 已启动固定版本 `clamav/clamav:1.5.3_base` 并达到 healthy；真实 ClamAV 集成测试通过 readiness、clean scan 与 EICAR infected detection，结果为 1/1。
- ClamAV、asset queue、必需 Operation Executor 和 Worker readiness 的配置/部署聚焦门禁为 70/70；`docker compose config --quiet` 通过。
- Ticket 05 第一次全量 Python 回归为 531 passed、50 failed、21 setup errors、1 skipped；首个根因是可丢弃的 `commercevision_test` 已登记当前中间 revision 但缺少 `asset_validation_results`，Fixture 清理随后级联失败。先重建独立测试库再复跑，不把级联项误判为业务缺陷。
- 仅删除并由 Fixture 重建独立 `commercevision_test` 后，全量 Python 回归为 602 passed、1 skipped；唯一 skip 是需要真实阿里云临时凭证的 OSS live contract，两项 warning 均为既有上游弃用提示。
- 主 `commercevision` 库核对时停在 `b1c8e4f2a703` 且尚无资产表；停止 API/Worker/Scheduler/MCP/Web 写入面后，使用正式 migration 向前升级到 `e5f8b2d6c914`。38 个 Workflow、799 个 Outbox、795 个 Inbox、413 个 Checkpoint、2217 个 Pending Write 等既有行数保持不变；`alembic check` 无 drift，新资产表为空。
- 当前 Web 全门禁通过：Vitest 26/26、Proxy 10/10、Playwright 27/27、ESLint、TypeScript、OpenAPI 生成类型漂移检查和 Next.js 15.5.21 production build。
- Python 与 pnpm 依赖漏洞审计均返回 0 个已知漏洞；新增 Alibaba SDK 为 Apache-2.0，`c2pa-python` 为 MIT OR Apache-2.0，和公开仓库许可边界兼容。工作树扫描未发现 GitHub/云厂商凭证前缀。
- 新增真实 Durable Worker 红测试复现 Promotion 外部成功、DB 提交前并发失败、Operation 进入第二 execution attempt 后被误终态为 `VALIDATION_OBJECT_MISSING`；当前红灯为 1 failed，要求通过精确不可变身份检查安全复用上一 attempt 的 PASS/NOT_APPLICABLE evidence 后再转绿。
- Cross-attempt recovery 已转绿：Evidence Store 只复用同 Operation、同 Asset Version/Object/Policy 且严格身份匹配的旧 `PASS/NOT_APPLICABLE`，不复用 `RETRYABLE_FAILURE`；真实 Worker 第二 attempt 仅新增 PROMOTION evidence 并收敛为 `SUCCEEDED`。
- 主控随后把 21 项 unit/contract 与该 MySQL 用例组合复验时，恢复 Worker 恰好同时启动全量 pytest 并清理同一 `commercevision_test`，导致 Finalize 404；确认是两个测试进程共享数据库的装置竞争，不是实现回归。等待 Worker 全量结束后再串行复验。
- 恢复 Worker 的全量 pytest 进程退出后，主控改为单进程串行复验：Evidence/Observability/Malware/Content Safety/Provenance 共 66 项通过；真实 MySQL+MinIO 的跨 attempt PASS evidence 恢复、Validation HTTP 跨 Workspace 404 和篡改 Operation 绑定拒绝共 3 项通过。此前失败正式归因为共享测试库并发清理竞态。
- Ticket 05 恢复 Worker 最终全量 Python 门禁为 `615 passed, 1 skipped`；唯一跳过项为需真实阿里云临时凭证的 OSS live contract，两项 warning 为既有上游弃用提示。
- Validation UI 先以 27/28 红灯证明终态基础设施失败被误显示为内容拒绝，再增加独立 `failed` 呈现转绿。最终 Web 门禁为 unit 28/28、Proxy 10/10、Playwright 28/28，ESLint、TypeScript 和 Next.js production build 均通过。
- Ticket 05 工作树现已冻结，准备针对固定基线 `0436c40` 启动 Standards/Architecture、Spec/Acceptance、Security/Reliability 三路独立只读发布审查。
- 固定快照静态门禁通过：Ruff 对 191 个 Python 文件的 format/check 全绿，`git diff --check` 无空白错误，`docker compose config --quiet` 通过。
- Standards/Architecture、Spec/Acceptance、Security/Reliability 三路独立只读发布审查已并行启动；审查期间不修改业务代码。
- Alembic 首次复验误用了不存在的 `database/alembic.ini` 配置语义；枚举真实配置后改用根目录 `alembic.ini`。主 `commercevision` 与独立 `commercevision_test` 均在 `e5f8b2d6c914 (head)`，两库 `alembic check` 均无 schema drift。
- 以运行时 FastAPI schema 在内存中对比已提交快照，`docs/api/openapi.json` 无 drift；`pnpm web:api-types:check` 证明生成的 Web API 类型同步。
- Python 锁文件经 `pip-audit` 检查无已知漏洞；pnpm 以 Moderate 门槛审计同样返回 0 个已知漏洞。
- 最新源码已成功构建 `migrate/object-storage-init/scheduler/api/mcp-server/otel-collector/web/worker` 八个镜像；完整 Compose 重新部署后 13 个长期服务全部 healthy，Migration 与 Bucket 初始化均以 0 退出。
- Control API readiness 对 configuration、MySQL、Valkey、RabbitMQ、对象存储和 Milvus 全部返回 `ok`。Worker readiness 为 `ready=true`、`consumer_ready=true`、MySQL/对象存储/ClamAV 均 `ok`，注册与必需 Kind 同为 `ASSET_DELETION`、`ASSET_VALIDATION`。
- RabbitMQ 运行态确认 Workflow、Maintenance、Asset 三类队列各有 1 个消费者且无积压，Index 队列按后续 Ticket 边界保持 0 个消费者。
- Worker 启动后消费两条 Ticket 04 时期遗留、目标资产已不存在的 Validation Operation；两者均失败关闭为 `FAILED/VALIDATION_TARGET_NOT_FOUND`，并各自关联 `operation_terminal_failure` Dead Letter，没有被静默 ACK 或无限重试。
- 最新部署通过 Web BFF 完成真实 Foundation PNG 冒烟：创建 Upload Session、MinIO 直传、Finalize、Asset Queue 消费和 Validation 全链路成功。Operation `019f9d5f-1e56-749f-a83d-3e239411f2a7` 收敛为 `SUCCEEDED`，Asset `019f9d5f-1c81-7297-9bdb-b221dd9f4c00` 到达 `PENDING_RIGHTS`，LOCAL_FORMAT/MALWARE/CONTENT_SAFETY/PROVENANCE/PROMOTION 五阶段均为 `PASS`。
- 对象层复核确认隔离 `ORIGINAL` 事实为 `DELETED` 且当前源对象物理不可读；`CONTROLLED_ORIGINAL` 为 `FOUNDATION/CONTROLLED`，以 MySQL 持久化的精确 Provider Version ID 可成功 HEAD。
- 最新真实冒烟后的 API/Worker/Scheduler/MCP/Web/ClamAV 日志无 Traceback、ERROR、CRITICAL 或 panic；边界修正后的仓库凭证前缀扫描无命中。
- Security/Reliability 独立审查返回 3 个 P1、3 个 P2；Standards/Architecture 独立审查返回 1 个 P1、3 个 P2。两路共同复现 ClamAV prefork child 丢失实际 Scanner Version；其余阻断覆盖 Provider 外发授权、Task retention 竞态、C2PA native parser 隔离、默认 clamd 暴露、ClamAV digest、元数据双计、永久 4xx 重试和 Validation 历史读取。
- Spec/Acceptance 独立审查返回 3 个 P1：已持久化 retryable evidence 后崩溃会被错误地视为 reconciliation pending 并循环；实际 ClamAV Version 缺失；真实 MySQL/MinIO Worker 未覆盖 Ticket 要求的全失败矩阵和 evidence-commit 后中断点。三路发布审查均未批准当前快照。
- Ticket 05 审查修复在原独立恢复上下文继续：已观察到 retryable evidence 崩溃恢复、永久 Provider 失败、共享图片元数据计量和历史 Validation 读取的红绿用例；主控尚未把这些局部结果视为发布通过。
- 主控静态复核发现 Task retention 首次对象清理与 MySQL 锁之间仍存在并发 Promotion 重建目标副本的窗口，已要求同一 Worker 以 I/O 前检查、锁内截止时间检查、复制后补偿清理和真实 MySQL/MinIO 竞态测试证明最终无遗留对象。
- 审查修复期间父级不并发执行共享 `commercevision_test` 套件；当前等待 Worker 完成外发授权、C2PA 可终止进程隔离、ClamAV digest/默认无宿主端口与完整失败矩阵。
- 主控在当前 Worker 镜像的 Celery daemonized prefork child 中调用 `KillableC2paReaderBoundary.read()`，确定性复现标准 multiprocessing 无法创建子进程；Ticket 05 仍有生产执行上下文 P1，必须改为受限 subprocess 边界。
- 最新真实成功上传链路中，Validation Operation 正常 `SUCCEEDED` 且 Asset 到达 `PENDING_RIGHTS`，但同次 Finalize 的 `asset.upload.finalized` 已知 Observation 被 Asset Worker 以 `unhandled_event` 写入 DLQ。事件路由修复与“成功上传不新增 DLQ”的真实传输回归已加入 Ticket 05 发布门禁。
- Ticket 05 原独立上下文以两条红绿纵向切片修复发布阻断：C2PA 改为 package-owned、framed/bounded、可强制终止的 subprocess child，并在 daemonized billiard prefork child 中证明挂起超时后容量恢复；Asset Worker 显式观察 `asset.upload.finalized`，保持未知/不支持/格式错误/未绑定事件失败关闭。
- 主控复验 C2PA、Event Routing 与 Worker Transport 为 `37 passed`；真实 MySQL/MinIO Finalize 双事件用例为 `1 passed`，首投均 `processed`、重投均 `duplicate`、Inbox 均 `PROCESSED` 且精确 message IDs 的 DLQ 为 0。
- 第一组固定差异审查上下文长时间无结论后已关闭；修复后的 staged snapshot 重新启动三路窄范围 Standards/Architecture、Spec/Acceptance 与 Security/Reliability 终审，明确限制为高置信 P0-P2。
- 后续审查确认跨 execution attempt 复用的 Content Safety 与 Provenance 证据缺少当前 Provider/策略/映射/信任配置身份校验；同一 Ticket 05 实现上下文已按红绿 TDD 增加 side-effect-free typed configured identity，并对旧证据失败关闭。
- 图片解码字节上限现在按 Pillow mode 的 band 数与 1/8/16/32-bit sample width 保守计量，不物化额外 decoded copy；损坏 EXIF 在统一 metadata validator seam 归一为 `MALFORMED_IMAGE`，ICC 继续执行既定大小上限。
- 主控独立复验 Provider identity、图片边界、本地校验与 Alibaba/C2PA Contract 共 `100 passed`（启用 `-W error`）；相关 12 个文件 Ruff check/format 全部通过。
- 同一 Ticket 05 Worker 在终态切片恢复后持续停在运行态且无响应；安全关闭后由主控接管既有独立 Ticket 工作树，没有创建第二套实现或丢弃改动。
- 终态 TDD 红灯证明 Operation 与 `operation_terminal_failure` Dead Letter 已正确提交，但 Asset 仍停在 `VALIDATING`。新增通用可选 terminal-failure callback 后，非资产 Executor 无需实现新接口，Asset Validation 可在独立事务中原子写入 `FAILED` 与 typed Outbox Observation。
- Validation replay 现在只允许精确 `FAILED + QUARANTINED` 事实恢复到 `VALIDATING`；Provider 修复后同一 Operation attempt 2 可复用兼容 PASS 证据并完成 Promotion。
- Operation Retry Policy 在 execution attempt budget 已耗尽时把原始 retryable error 归一为 terminal，避免出现 `state=FAILED` 但 `error.retryable=true` 的矛盾事实。
- 终态 Contract/Domain/Worker 单元接缝为 `41 passed`；永久失败、预算耗尽和 DLQ replay 真实 Worker 门禁为 `5 passed`；本地、malware、内容安全、provenance 四组真实矩阵为 `13 passed`。
- 三路阶段终审中的有效阻断已修复：Recovery Scanner 终态现在发布独立 `TERMINAL_FAILURE` Generation，Worker 回调失败时不消费代次并可重投；Claim-before-start 与 Reconciliation 到期均有真实 MySQL 证据。
- LoRA、Prompt Template、Model Configuration 与 Image 的真实 Worker 成功矩阵已补齐，四类均完成本地校验、malware、适用/NOT_APPLICABLE Provider evidence、Promotion 和 typed completed event。
- Asset lifecycle 已从主执行器抽成独立协调器，集中管理人工复核、拒绝清理、Operation 终态收敛和 Outbox 原子发布；验证 stage 编排继续留在 Executor。
- 全量 Asset/Upload/ClamAV 真实集成门禁为 `87 passed`；Operation/Event/Domain 组合为 `148 passed`；当前 Ticket 05 Unit/Contract 聚焦为 `272 passed`。
- 并发 Promotion 回归现在同时断言 MySQL 只有一个 `CONTROLLED_ORIGINAL`，MinIO 也只保留该 Provider Version；重复同内容版本经精确条件删除收敛，未知差异仍失败关闭。
- Ticket 05 最新固定差异的两路独立故障注入新增 5 个 P1：普通 DLQ replay 会在目标终态回调前完成生命周期；零次执行的 deadline 终止无法生成终态事件；FAILED 回调在锁等待后可越过 Task retention；Promotion 在实际数据库提交边界仍有 TOCTOU；固定两次清理无法删除 3 个及以上对象版本。当前快照未获批准。
- 主控将按公开 Worker/Event、真实 MySQL/MinIO、Object Storage Adapter 三个既定接缝逐项建立红灯并修复；完成前不提交 Ticket 05，也不启动 Ticket 06。
- 普通 Recovery Event 的 Worker 当前仅在 `claim.provider_claimed=true` 时执行 Provider 工作并随后完成 replay；deadline 分支与已终态的 claimed redelivery 没有返回 terminal-convergence work。修复将让 Worker 在该显式工作种类下先执行幂等目标回调，再完成 Replay Lifecycle。
- 一次 `rg` 调用向 Windows 传入未展开的 `*.py` 路径并返回文件名语法错误；后续改为传目录或由 `rg --glob` 过滤，不重复该命令形式。
- 已确认现有公开测试接缝可直接扩展：`test_operation_dead_letter_replay_*` 通过真实 MySQL 创建原 Operation Dead Letter、管理员 Replay Event 并由 `DurableOperationWorker.handle_recovery_event` 驱动，能够在不测试私有方法的前提下注入终态回调首次失败和消息重投。
- 第一条 TDD 红灯已确认：`test_operation_dead_letter_replay_retries_terminal_callback_before_completion` 在首次目标回调提交前抛错后读到 Replay Lifecycle=`COMPLETED`，而契约要求 `CLAIMED`；失败发生在预期断言，Provider 只执行一次。
- 普通 Replay 终态回调屏障已转绿：Operation 进入 `FAILED` 时不再提前完成 claimed replay；重投返回显式 `TERMINAL_CONVERGENCE` 工作，回调成功后再按持久 claim token 完成。真实 MySQL 用例通过，并证明 Provider 仍只执行一次。
- 新增 replay deadline 回归时首个批量补丁因 `MutableClock` 后续类名与假定上下文不一致被拒绝，未产生半写入；拆为类与测试两个精确补丁后成功。
- Replay 终态两条真实 MySQL 门禁均通过：Provider 终态失败后的回调重投，以及认领前跨 execution deadline 的零新增执行回调重投；后者使用独立 Replay claim token，生命周期均在回调成功后才到 `COMPLETED`。
- 零次尝试 Contract 红灯按预期由 Pydantic `ge=1` 拒绝；仅将 `AssetValidationFailedPayload.attempt_number` 放宽为 `ge=0` 并写明零值语义后，事件 Contract 9/9 通过，负数仍被拒绝。
- 真实资产接缝将复用现有 Finalize → `asset.validation.requested` → WorkerRuntime 流程，在首次消费前把 Durable Operation execution deadline 置于过去，断言 Operation/Asset/typed Outbox 原子收敛且无任何阶段 Evidence。
- 零次尝试真实 MySQL/MinIO 门禁通过：首次 Validation Event 在 execution deadline 后消费，Operation=`FAILED`/attempt_count=0、Asset=`FAILED`、源仍为 `QUARANTINED`、阶段 Evidence=0，并原子写入唯一 `asset.validation.failed` Observation（attempt_number=0）。
- 终态 retention 锁等待测试可直接通过 `_validation_executor(..., uow_factory=..., clock=...)` 注入仓储包装器；既有 Task terminal test 已证明过期清理后的预期事实为 Asset/Object=`DELETED`、物理源 404、无 `asset.validation.failed`。
- 终态锁等待回归首次运行因测试误用 `AssetObject.version_id` 失败；领域字段实际为 `provider_version_id`，修正测试装置后不重复该错误。
- 修正装置后的真实红灯准确复现：时钟在 Asset 行锁返回后跨过 retention deadline，当前实现仍提交 Asset=`FAILED`、Object=`QUARANTINED` 和 1 条 failed event，而预期是删除收敛。
- 终态回调锁内栅栏已转绿：专用 retention 错误先回滚 MySQL 事务，再执行精确对象清理；锁等待跨界与原 delayed terminal 两项真实用例均通过。
- Promotion commit 修复设计固定为 `asset_ports` 的专用 retention-aware UoW 原语：同一事务 flush 后同时校验提交接缝时钟与 MySQL `UTC_TIMESTAMP(6)`，失败统一 rollback；协调器负责映射错误和存储补偿。
- 首次把测试时钟推进移到通用 `commit()` 后用例仍为绿，根因是同一包装 UoW 也服务前置阶段 evidence，第一次普通提交就提前推进了时钟，未命中 Promotion 实际提交。下一版用仓储 marker 只标记包含 PROMOTION result 的事务，再在该事务 commit 入口推进。
- marker 修正后真实红灯命中：Promotion commit 入口跨界时旧代码把 Operation 提交为 `SUCCEEDED`，证明此前最后一次应用时钟检查仍有 TOCTOU。
- 新增 retention-aware Asset UoW 提交原语：flush 后在提交接缝复验注入时钟，并以 MySQL `UTC_TIMESTAMP(6)` 对锁定 Asset 的持久 deadline 做权威校验；专用过期错误使事务回滚，Promotion 再精确补偿复制对象。commit-entry 用例已转绿。
- MySQL 权威时钟独立门禁通过：DB deadline 已过而 app clock 仍在 deadline 前时，UoW 拒绝提交并回滚已执行的 Asset 状态更新。
- 全版本 retention 测试首次假定 Finalize 已在 destination 保留一个版本，实际该 key 初始为空；把前置条件修正为显式创建 3 个 owned versions 后，真实红灯稳定留下 1 个版本，准确证明固定两轮清理不充分。
- 对象存储 Contract 已增加有界版本页、OBJECT/DELETE_MARKER 条目和 exact delete-marker 请求；MinIO 与 OSS Adapter 已实现 opaque 双 marker 游标、精确 key 过滤和版本 ID 失败关闭。下一步补齐双 Adapter contract fake，再接入 UploadPromoter 的有界稳定扫描。
- MinIO Contract fake 已支持分页响应、marker 请求断言和 exact marker 删除记录；OSS fake 同样扩展版本页与 marker 删除，现正把分页往返断言加入两套既有 Adapter Contract。
- MinIO/OSS 分页与 marker 删除 Contract 各通过；`UploadPromoter` 已改为有界分页、逐版本所有权复验、精确删除与两次完整空扫描，Retention Coordinator 不再依赖固定双调用。真实 MinIO 的 3-version 红灯已转绿。
- 真实 MinIO 进一步证明 bounded retry：删除预算设为 2 时数据库保持 Asset=`DELETING`/Object=`DELETE_PENDING` 并返回可重试错误；下一次默认预算调用删除剩余版本后才写 `DELETED`。
- 普通 Operation DLQ replay 现在把目标终态回调作为完成屏障；回调首次失败时 Lifecycle 保持 `CLAIMED`，同一事件重投只补偿回调且 Provider 调用次数保持 1。Provider 终态和认领前 deadline 两条真实 MySQL 用例均通过。
- `asset.validation.failed` 明确接受 `attempt_number=0` 表示首次 Provider claim 前到期，Completed Event 和阶段 Evidence 仍要求正数；真实 Worker 证明 Operation/Asset/typed Outbox 收敛且 Evidence 为 0。
- FAILED 回调在取得 Asset 行锁后重新采样时钟并执行 retention commit guard；Promotion UoW 在 flush 后同时使用应用 UTC 与 MySQL `UTC_TIMESTAMP(6)` 校验持久 deadline，过期事务回滚并精确补偿已复制版本。
- 对象存储深模块现提供 provider-bound opaque 分页游标、OBJECT/DELETE_MARKER 类型和 exact marker 删除；MinIO 与 OSS 合约覆盖分页往返、精确 Key 过滤、损坏游标和 marker 删除。
- 有界 retention 清理枚举 source/destination 的全部精确版本，逐对象复验所有权，并要求连续两次完整空扫描后才提交 `DELETED`。三版本、删除预算耗尽重试和首次扫空后并发 copy 三类真实 MinIO 用例均通过。
- Ticket 05 最终 Python 门禁为 `712 passed, 1 skipped`；唯一跳过项是需要真实阿里云凭证的 OSS live contract。Ruff 204 文件、依赖审计、OpenAPI/Web 类型与 Alembic drift 均通过。
- Web 最终门禁为 Proxy 10/10、Vitest 29/29、Playwright 28/28，并通过 ESLint、TypeScript、Next.js production build、生成类型检查和 pnpm Moderate 漏洞审计。
- CI 要求的 API、Worker、Scheduler、MCP、Web 与 OTel 镜像全部构建成功；默认 Compose 强制重建后全部长期服务 healthy，ClamAV 仅内部暴露 3310/7357，主机端口映射为 null。
- 最新容器级 Foundation PNG 冒烟完成 API 创建、MinIO 直传、Finalize、RabbitMQ/Celery Worker、真实 ClamAV、Promotion 和状态查询；Operation=`SUCCEEDED`、Asset=`PENDING_RIGHTS`，五阶段全部 PASS。
- 两路无历史固定差异终审已启动：一条审查 Standards/Spec/Architecture，另一条审查 Security/Reliability；审查期间业务差异保持冻结。
- 两路固定差异终审未批准当前快照，并新增四项必须修复的问题：Scanner replay 回调屏障、实际 Provider endpoint 授权、Workspace allowlist 精确身份，以及受限 JSON 结构复杂度。
- 当前保持全部业务文件 staged、规划文件 unstaged，不会在修复、完整回归和独立复审前提交。
- 已锁定四项 TDD 接缝：真实 MySQL Recovery Scanner + Replay Lifecycle；Validation Transfer Policy/Worker 请求工厂；Settings 启动校验；三类 `AssetLocalValidator.validate()`。
- Scanner replay 屏障红灯准确复现：最后一次 claimed replay lease 到期后 Operation=`FAILED`，旧代码把 Lifecycle 写成 `COMPLETED`。Scanner 现仅在非终态恢复时完成 expired claim；真实 MySQL 重投证明回调首次失败仍为 `CLAIMED`、第二次成功后才完成，且 Provider 调用数为 0。
- Validation Transfer Policy 已升级到 v2 snapshot schema并绑定 canonical endpoint-host allowlist；运行时授权直接读取 `ContentSafetyAdapter.configured_identity.endpoint`。真实 MySQL/MinIO 用例证明同 Provider/Region 但 endpoint=`collector.example` 时，在临时 URL 和 Provider 调用前以 `VALIDATION_TRANSFER_ENDPOINT_DENIED` 终止。
- Settings 现在拒绝非规范 endpoint host、IP、wildcard、scheme/port/path，以及不符合统一 Workspace ID 正则的 allowlist 条目；`Catalog-A` 与 `catalog-a` 保持两个二进制精确身份。
- `AssetLocalValidator` 统一捕获三类 JSON 的 `RecursionError`，并在解析成功后以迭代遍历执行深度和节点预算。SafeTensors、Prompt Template、Model Configuration 的超深、超深度和超节点 9 个公开接缝用例全部转绿。
- 四项终审修复聚焦回归通过：受影响 Unit `164 passed`、Operation MySQL `4 passed`、外部 transfer MySQL/MinIO `3 passed`；15 个变更 Python 文件 Ruff check 通过。
- 首次全量 Python 回归为 `743 passed, 3 skipped, 1 failed`。唯一失败是既有外部临时 URL 时窗测试仍让 fixture Adapter 报告 `endpoint=local`，新 endpoint policy 在请求工厂调用前按设计拒绝；测试装置改为 allowlisted Alibaba host 后单独复验并重跑全量。
- 修正旧 fixture 后，全量 Python 门禁为 `744 passed, 3 skipped`；Ruff format/check、依赖审计、OpenAPI/Web 类型、Alembic upgrade/check 均通过。三项默认 skip 中的两项真实 ClamAV 用 overlay 单独执行为 `2 passed`，仅阿里云 OSS live contract 因无临时凭证不可执行。
- Web 全门禁为 Proxy 10/10、Vitest 29/29、Playwright 28/28，并通过 frozen install、ESLint、TypeScript、API 类型检查、Moderate 漏洞审计与 production build。
- CI 所需 API、Worker、Scheduler、MCP、Web 与 OTel 镜像全部构建成功；完整 Compose 重建后长期服务均 healthy，Worker readiness 注册并要求 `ASSET_DELETION`、`ASSET_VALIDATION`，ClamAV 宿主端口映射保持 null。
- 修复后真实容器链路再次通过 Web BFF、Control API、MinIO、Scheduler、RabbitMQ/Celery、真实 ClamAV 与 Promotion：Operation 经 `PENDING -> RUNNING -> SUCCEEDED`，Asset 到达 `PENDING_RIGHTS`，五阶段均为 PASS，投影不暴露原始 Provider/Object 完整性载荷。
- 冒烟后应用与依赖日志无 Traceback、CRITICAL、Unhandled、ERROR、Exception 或 Dead Letter 特征；仓库凭据前缀扫描无命中。第一次 PUT 因宿主 Python 读取环境代理返回 503，改用 `trust_env=False` 后同一健康 MinIO 直传为 200，业务与容器配置未变。
- Ticket 05 业务实现已提交为 `77e5214` 并推送；远程 CI `30221101083` 的 Web、容器和安全/SBOM 均通过，Python Job 仅在 `alembic upgrade head` 创建不可变结果 Trigger 时因业务账号权限不足失败。
- CI 根因修复不向业务账号增加 DDL 权限：Alembic 直接消费独立 `CV_MIGRATION_MYSQL_DSN`，API/Worker/Scheduler 继续使用运行时 DSN；Compose 和 CI 在迁移前把运行时账号幂等收敛为 `SELECT/INSERT/UPDATE/DELETE`，并执行真实 `CREATE TABLE` 拒绝探针。
- 独立复审首轮指出三项遗漏：部署环境判断只读 `os.environ`、显式运行时 DSN 连接失败仍可 skip、`mysqladmin ping` 可在认证失败时报告服务存活。三项均已修复为 validated Settings、CI/显式 DSN 强制失败和认证 TCP `SELECT 1` 健康检查。
- 最终数据库身份专项测试 `21 passed`；全仓 Ruff format/check 通过；完整 Python 为 `760 passed, 3 skipped`；Alembic upgrade/check、Python 依赖审计和 OpenAPI drift 均通过。
- 最终源码重建 `migrate` 镜像后，Compose `mysql-permissions` 与 `migrate` 均重新以 0 退出，MySQL 和全部长期服务持续 healthy；真实运行时账号只保留 DML grant 且 DDL 被拒绝。
- 当前等待原独立审查上下文只复核上述三项整改；通过后提交并推送数据库身份工程修复，再以新的 GitHub Actions 运行作为 Ticket 05 验收门槛。
- 原独立审查上下文已复核配置源、强制权限门禁和认证 TCP readiness 三项整改，返回 `No P0-P2 findings / VERDICT: APPROVED`。
- 数据库身份工程修复已提交为 `dbc8161`（`Separate migration and runtime database identities`）并推送到 `origin/main`；GitHub Actions 运行 `30225320445` 已启动，等待全部 Job 结束。
- GitHub Actions `30225320445` 已完成并全绿：Python checks、Web checks、Container builds、Security and SBOM 均为 `success`；新增授权收敛、独立 Alembic 身份和运行时 DML-only 验证步骤均在真实 MySQL 8.4 服务上通过。
- Ticket 05 已更新为 `complete`，11 项验收标准全部勾选，并记录业务提交 `77e5214`、CI 修复 `dbc8161` 与远程运行 `30225320445`；下一步提交独立验收记录。
- Ticket 06 的 Rights Record、当前可用性决策、权限替换/撤销/到期/管理员阻断、HTTP/Web 工作台、Scheduler/Worker 收敛和 MySQL `DATETIME(6)` 迁移已作为单一业务提交 `2975fcf` 落地。
- Ticket 06 最终聚焦门禁包括 Rights 公开接缝 `41 passed`、Operation migration `5 passed`、Web Proxy `14 passed`、Vitest `31 passed`、Playwright `29 passed`；Ruff、Alembic upgrade/check、OpenAPI 生成、Web 类型与 production build 均通过。
- Ticket 06 最终全量 Python 本地运行的两个失败均为 Windows 高负载下的子进程时序波动，相关 Object Storage 与 C2PA 用例在隔离复跑中通过；远程 Linux CI `30319058792` 随后完整执行并确认 Python、Web/E2E、容器、安全/SBOM 全部 `success`。
- 独立终审确认数据库权威时钟、浏览器授权时间快照、版本冲突草稿保护、错误优先级与 OpenAPI 错误集合均已关闭，无剩余阻断问题。
- Ticket 06 已更新为 `complete`，11 项验收标准全部勾选；下一执行项为 Ticket 07 ProductBrief HITL。
- Ticket 07 已打通 ProductBrief 分析请求、Durable Operation/Event、确定性与 Alibaba Vision Adapter、不可变模型/人工版本、精确版本确认、Workflow Resume、HTTP/Web 工作台、审计与可观测性。
- 首轮审查发现 Provider 实际身份、审核策略、并发幂等、过期读取、Provider Call 聚合归属、Provider 风险标志、同步超时线程和 Web 409 辅助投影八类问题；当前均已建立公开接缝回归并修复。
- ProductBrief 分析请求现冻结 Provider 配置哈希、置信阈值、强制复核字段、敏感字段规则和审核策略哈希；Worker 在签发临时 URL 前校验实际 Adapter identity，并验证 Outcome 及每个 Provider Call 的完整来源身份。
- Provider 返回的 `review_required/sensitive` 只能提高风险，不能降低服务端审核策略；医学类、美妆功效、汽车适配/安全/认证等配置字段由服务端从非空声明值派生敏感风险。
- 分析、修订和确认统一使用 MySQL 原子 claim/complete 幂等协议；并发相同请求串行化，COMPLETED 重放返回首次保存的完整响应快照，不按当前聚合重新投影。
- Task ProductBrief 的读取、版本列表、修订和确认均在 MySQL 权威时钟下拒绝过期数据；Provider Call 到 ProductBrief Version 的复合外键阻止跨聚合来源引用。
- 本地主库和独立测试库均确认 8 张 ProductBrief 表为 0 行后，对同 revision 中间 schema 执行受控 downgrade/upgrade；主库 `alembic check` 无 drift，迁移测试通过。
- Vision HTTP Adapter 已从不可取消的同步线程池改为独立异步运行时；绝对 deadline 覆盖容量等待、流式响应和 repair，取消时先关闭响应流并归还并发容量，关闭进程时先取消活动请求再关闭客户端。
- Provider 与领域 JSON 采用迭代深度/节点/字符串/字节预算；超深结构统一归一为受控 malformed/domain error，不再泄漏 `RecursionError`。
- Web 409 恢复采用核心 ProductBrief 与辅助版本历史/Operation 分离的部分成功语义；辅助接口 503 不再阻止载入最新版本和恢复本地草稿，并明确显示非阻断告警。
- Ticket 07 聚焦门禁当前为 Python ProductBrief/Provider/MySQL `134 passed`，Web Unit `35 passed`、Proxy `15 passed`、Playwright `31 passed`，并通过 Ruff、ESLint、TypeScript、OpenAPI/Web 类型和 Next.js production build。
- 固定差异 `51c8462..工作树` 已启动规格/正确性、架构/可维护性、安全/可靠性三路独立只读终审；审查期间业务代码冻结。
- Ticket 07 第一轮固定差异后的顺序全量 Python 为 `888 passed, 3 skipped`；Web 为 Proxy `15 passed`、Unit `36 passed`、Playwright `37 passed`，并通过 lint、typecheck、API 类型、干净 production build 与双依赖漏洞审计。
- 三路终审未批准第一轮快照，确认发布阻断包括：旧 Operation 恢复依赖可变 ProductBrief 头指针、Alibaba 未知结果被盲目重投、OSS 签名 Header 与 image_url 不兼容、取消后仍可外发、非有限 JSON、确认组合外键不足、租约未覆盖 preflight，以及 Web 商品/Operation 异步状态串扰。
- Alibaba 官方 OpenAI-compatible Chat 文档未提供请求幂等键或按提交身份查询；Ticket 07 的生产策略固定为：确认 429/5xx 可重试，无法证明是否已执行的 read/write interruption、post-response artifact failure 和 intent-without-result 失败关闭到人工/DLQ，不伪装为安全自动重投。
- 当前在两个新的独立实现上下文中按红绿 TDD 修复 Python/Provider/MySQL 与 Web/Playwright，写集互不重叠；修复、第二轮固定差异复审和完整门禁完成前不提交。
- Ticket 07 第二轮修复已由三个互斥写集上下文完成：Web 隔离商品/Operation 异步状态并保护生产 `.next`；Provider 对未知提交结果失败关闭、按次轮换挂载凭证并签发无附加 Header 的精确对象 URL；后端以不可变 Analysis/Provider Attempt/Version 恢复旧 Operation，并加强租约、确认和迁移约束。
- `ProductBriefProviderCallResponseV1` 的实际提交模型快照先通过 OpenAPI 合约红灯暴露遗漏；补齐 `submitted_model_snapshot` Contract 与投影后重新生成 OpenAPI/Web 类型，合约测试与 TypeScript 检查均通过。
- 第二轮主控聚焦门禁：ProductBrief 真实 MySQL `27 passed`；ProductBrief/Operation migration、acceptance 和 runtime `90 passed`；Provider、配置、Worker、Object Storage、OpenAPI Unit/Contract `212 passed, 1 skipped`，唯一跳过项为无真实凭证的 OSS live contract。
- Web 第二轮完整门禁通过 ESLint、TypeScript、API 类型漂移、production build、Proxy `15 passed`、Unit `36 passed` 和 Playwright `41 passed`；E2E 使用隔离的 production artifact 副本，不重建或修改生产 `.next`。
- 全仓 Ruff 初检仅发现 4 个文件需要机械格式化；统一格式化后 `ruff format --check .` 与 `ruff check .` 均通过。
- 固定基线 `51c8462..工作树` 的第二轮三路全新只读复审已启动，分别覆盖后端状态/迁移、Provider 安全/可靠性、Web 契约/竞态/可访问性；复审完成前业务代码继续冻结。
- 第二轮三路复审均返回 `REQUEST_CHANGES`，无 P0；P1/P2 集中在旧 Worker 越权发布、取消与 Provider submission 竞态、UNKNOWN 自动重试、响应体中断分类、Provider 输入/输出预算、Provider Artifact readiness、同 ProductBrief 迟到响应、跨商品 source 污染、浏览器过宽 Workflow/Operation 契约和结构化字段可访问性。
- 三个全新互斥写集上下文已按 TDD 分别修复后端 fencing、Provider/配置/readiness 与 Web 状态隔离；主控独立负责 ProductBrief 专用最小权限浏览器投影和稳定 5xx 错误信封。
- 浏览器投影红灯证明现有 BFF 只能读取完整 Workflow/Operation；新增 Contract/API 后，Workflow 仅返回 `id/version`，Operation 必须精确绑定 ProductBrief 且只返回状态、尝试计数、脱敏错误与版本。跨类型或跨 ProductBrief Operation 统一隐藏为 404。
- 未捕获异常现在返回固定 `INTERNAL_ERROR` 信封和关联 ID Header，响应与日志结构字段均不包含原始异常文本；API 投影与错误边界 Unit 当前 `6 passed`。
- 第二轮修复后的浏览器聚焦门禁通过：ProductBrief API/OpenAPI `7 passed`、BFF Proxy `15 passed`、Web Unit `38 passed`、ProductBrief Playwright `14 passed`，以及 Asset validation 安全投影恢复回归 `2 passed`；ESLint、TypeScript 与 production build 同步通过。
- 后端聚焦回归首次暴露跨上下文集成缺陷：API 冻结策略与 Adapter 分别维护 Provider 配置哈希，新增输出/输入预算后字段集合漂移，导致 ProductBrief MySQL 套件 `23 failed, 8 passed`，统一失败为 `VISION_PROVIDER_IDENTITY_MISMATCH`。
- 以公开配置身份接缝新增两个红绿测试，并建立 `commercevision_contracts.vision_configuration` 共享快照函数；确定性 scenario 明确为测试结果注入，Alibaba 完整 endpoint、模型、response/output/repair 与 Product facts 预算统一冻结。Worker 同时传递全部预算。
- Compose Contract 进一步检出 API/Worker 未共享 mandatory/sensitive review paths 及 response/repair 上限；四项现已进入共同 policy anchor，避免生产覆盖默认值后才在执行期发生 identity mismatch。
- 修复后 Provider/Settings/Worker/Object Storage 聚焦套件 `236 passed`，ProductBrief 真实 MySQL HITL/恢复套件 `31 passed`；共享身份与 Worker 组合的三个新增回归测试均通过。
- 第三轮后端审查的取消竞态红灯准确复现：Provider 提交意图已经持久化且 Adapter 已进入调用边界时，旧取消接口仍返回 `200 CANCELLED`。
- Workflow 行锁现同时串行化取消与 ProductBrief Provider 提交意图；对应 Operation 仍为 `RUNNING` 且尚无持久 Provider Call 时，取消以稳定 `409 WORKFLOW_CANCELLATION_REFUSED` 失败关闭。提交先发生、取消先发生及消费前取消三项真实 MySQL/HTTP 回归全部通过。
- Ticket 07 上下文恢复确认：版本化 ProductBrief 字段值的后端 Contract、OpenAPI 扩展与 Web 状态层补丁已落盘；工作台组件、Vitest 和 Playwright 仍使用旧通用 JSON 值，当前按既定 HTTP/Web 公开接缝完成迁移。
- Web 工作台旧实现的具体漂移已定位：`FieldDraft.valueKind`、字符串专用 `<input>`、无 path 的 `structuredValueError`、`JsonValue` 返回类型及 stale draft 恢复共同绕过了新 schema；本轮统一改为 path 判别的 `ProductBriefFieldValueV1` JSON 对象。
- Web 测试审计确认 Vitest 的结构化值用例仍只验证“可解析 JSON”，Playwright 的 ProductBrief 夹具仍大量返回裸字符串/数组；这些夹具将改为 `TEXT`、`TEXT_LIST`、`FLAG_LIST` 判别联合，并增加错误 kind、额外属性和非法 dimension 类型拒绝用例。
- ProductBrief Playwright 结构化字段场景将继续验证逐字段 `aria-invalid`、独立错误关联和首个非法字段聚焦，同时把第二个错误改为“JSON 可解析但违反 FLAG_LIST schema”，覆盖解析错误与 Contract 错误两类用户反馈。
- 版本化 ProductBrief 字段值完成 Web 第一轮绿色验证：Vitest `42 passed`（含 path/kind、额外属性、dimension 类型和持久命令拒绝），TypeScript `tsc --noEmit` 通过。
- Web production build 通过；按 `build -> e2e` 顺序运行 ProductBrief Playwright `16 passed`。结构化字段的 JSON 解析错误、schema 错误、ARIA 关联、焦点顺序和最终修订提交均在当前构建产物上验证通过。
- OpenAPI 门禁的目标 schema 已核对为 Provider `ProductBriefFieldOutput`、读取 `ProductBriefFieldResponseV1` 和修订 `ProductBriefFieldRevisionV1`；三者都必须携带相同 31 路径映射和七分支 `kind` discriminator。
- ProductBrief OpenAPI Contract 门禁现锁定七分支 discriminator、31 条字面 path/kind 映射、所有值对象禁止额外属性，并反解析生成 TypeScript 常量验证一致性；专项 pytest 通过，目标文件 Ruff format/check 通过。
- Ticket 07 固定基线差异当前覆盖 87 个文件、约 2.98 万新增行；工单 11 项仍保持未勾选，必须在 Provider/storage/readiness、完整测试、独立终审及文档边界全部通过后一次性更新为 complete。
- S3/MinIO artifact 写入路径已人工核对：重放会对长度、SHA、Content-Type、全部 metadata 和实际加密状态做完整匹配；竞态胜者同样复验；新写入必须返回精确 Version ID 并在 HEAD 后验证实际加密，否则失败关闭。
- OSS artifact 路径采用同一完整匹配/加密/Version ID 不变量；Worker 启动、Celery readiness marker 与容器 healthcheck 都读取同一 mounted credential source，API 环境不接收 key/path，Compose Contract 明确验证 secret 仅挂载给 Worker。
- Provider/storage/readiness 完整专项组合 `242 passed, 1 skipped`，唯一跳过项为未提供真实阿里云凭证的 OSS live contract；ProductBrief 真实 MySQL 集成文件 `35 passed`，覆盖取消线性化、跨 Workspace 二进制隔离、Outbox 驱动的 Workflow 继续、HITL 幂等和恢复。
- Durable Operation 90 项组合首轮为 `89 passed, 1 failed`；失败定位为 ProductBrief 专属 readiness 误用 Asset queue 判定。修复将保留 readiness 稳定字段（非相关进程返回 `not_required`），同时按 required Operation kind 决定是否探测凭证和 Provider Result bucket。
- 后续完整 Worker 回归纠正了上述初判：共享 Asset queue 上的 optional ProductBrief 仍是可执行能力，必须探测凭证和 Provider Result storage。将恢复 queue-based readiness，并让纯注册测试通过 monkeypatch 注入 ReadyStorage，以隔离 `minio` 容器 DNS。
- Queue-based ProductBrief readiness 已恢复；纯 Executor 注册测试注入 ReadyStorage，既有 optional-Alibaba credential 安全回归与完整 Worker 套件 `35 passed`。ProductBrief/Operation migration、acceptance、recovery 组合重跑 `90 passed`。
- ProductBrief Runbook 已补齐版本化字段值、取消不可逆临界点、配置身份 v2、Provider Call/Model Version 原子提交、MySQL current-node Workflow continuation，以及 Ticket 13 负责物理删除/对账的明确边界；固定基线 `git diff --check` 通过。
- 全仓 Ruff format/check 通过（245 files）；OpenAPI 与 Web 类型已从当前源码重新生成，ProductBrief OpenAPI Contract `1 passed`，生成后 TypeScript `tsc --noEmit` 通过。
- 完整 Web 门禁通过：BFF Proxy `15 passed`、Vitest `42 passed`、ESLint、TypeScript、production build，以及 build 后全量 Playwright `47 passed`（Catalog/Asset/ProductBrief）。
- 完整 Python 测试套件通过：`979 passed, 3 skipped`，耗时约 18 分 55 秒；跳过项为无真实 OSS 凭证和当前宿主未暴露真实 ClamAV 两类显式 live contract。
- 本地主库与独立 `commercevision_test` 均通过 Alembic `upgrade head/current/check`：revision 为 `d9e4f7a2b610 (head)`，无 schema drift。
- Compose config 通过；Python `pip-audit` 与 `pnpm audit --audit-level=moderate` 均无已知漏洞。固定基线新增行和全部 untracked 文件的高置信凭证前缀扫描无候选，远程 Gitleaks 留作最终全仓证据。
- Docker 基础依赖当前 healthy，但 app services 未运行；固定 Compose project name 仍关联此前从 `mine` 路径创建的基础容器。下一步从当前源码重建 migrate/API/Worker/Scheduler/MCP/Web，并由 Compose 受控重建相同项目服务。
- 当前源码应用镜像全部构建成功；Web build context 传输了约 578 MiB，诊断发现 `.dockerignore` 未排除 Playwright `test-results`（其中隔离 Next artifact 约 140 MiB）和 report。新增部署 Contract 后排除这些生成物，再重建验证。
- `.dockerignore` 部署 Contract 红绿通过并排除两类浏览器生成物；Web BuildKit context 从约 578 MiB 降至 3.21 MiB，当前源码镜像重建成功。
- 当前源码 Compose 栈 `up -d --wait` 成功：API/Worker/Scheduler/MCP/Web 及全部依赖 healthy，mysql-permissions/migrate/object-storage-init 均 `Exited (0)`；`scripts/verify_phase0.py` 全项通过，近 10 分钟应用日志无 ERROR/Traceback/CRITICAL。
- 固定基线 `51c8462..工作树` 的第三轮三路全新只读终审已启动：后端状态/迁移、Provider/存储安全、Web/API Contract；只接收 P0-P2，并显式复核全部既有 P1 与 Ticket 13 物理删除延期边界。
- 容器 HTTP smoke 确认 Web 首页 200；生产 API 正确隐藏 OpenAPI；补齐 Workspace scope 但不提供签名 principal 的 ProductBrief GET 返回稳定 `401 AUTHENTICATION_REQUIRED`，路由和认证边界均已装载。
- API/Worker/Scheduler/MCP 均以非 root `commercevision` 用户运行，Web 以 `nextjs` 运行；五个应用容器均 healthy 且 restart policy 为 `unless-stopped`。
- Ticket 07 第三轮三路固定基线终审均返回 `REQUEST_CHANGES`，无 P0；去重后确认八项实现阻断：成功 Provider Call 与 Model Version 原子性、HTTP 响应证据中断的不确定态、零未解决字段的人工版本确认、ProductBrief 专用投影 retention 拒绝、生成 TS 字面量关联、409 草稿持久恢复、浏览器 72 小时保留边界、外部 evidence 引用信任边界，以及一项 422 输入回显 P2。
- “普通 diff 未包含未跟踪生产依赖”不是运行时实现缺陷，但属于发布完整性阻断；最终提交前必须 `git add -A` 后从 staged snapshot 验证所有导入目标、生成文件、构建和凭证扫描，不能以当前脏工作树测试代替 clean snapshot 证据。
- 当前修复策略固定为三个既有公开接缝的纵向红绿循环：真实 MySQL/HTTP 验证状态与 retention；Provider Adapter 验证 UNKNOWN 且不自动重投；Web/生成器验证 path-kind 编译约束、冲突草稿和 retention 到期清除。任何实现修改后重新运行受影响全量套件并启动全新固定基线终审。
- Provider 不确定态红绿完成：400/429/503 在 body read、close 或 response artifact 写入不完整时均返回非重试 `UNKNOWN`；完整 Adapter 文件 `63 passed`，不再让 HTTP 状态覆盖证据完整性事实。
- 成功结果原子性红绿完成：lease recovery、current-operation 漂移、Workflow 取消和注入事务失败均断言 Provider Attempt 保留、`SUCCEEDED` Call 与 Model Version 同时不存在；成功路径仍断言两者同事务存在，聚焦 MySQL `4 passed`。
- 人工版本的 `confirmation_required` 与 `unresolved_field_count` 已解耦，awaiting-confirmation Event 允许精确计数 0；对应 Domain/Event 测试通过。
- 422 公共错误信封仅投影 `type/loc/msg`，不再回显 Pydantic `input/ctx`；完整 API Error Unit `4 passed`。
- Evidence reference 现只允许固定内部 scheme 加 64 位小写 hex opaque token；应用忽略 Provider 自报 token，并根据授权 Asset Version、字段路径、类型、区域和摘要服务端重建。Domain/Contract/Provider Unit `90 passed`，真实 MySQL HITL 场景确认 Provider token 未进入 Web 投影。
- ProductBrief current、versions、Workflow context 和专用 Operation 四个读取面现共同在 72 小时 deadline 后返回 `410 PRODUCT_BRIEF_RETENTION_EXPIRED`；真实 MySQL retention 回归与路由 Unit 均通过。Workflow 最小投影增加精确 `retention_deadline`，供浏览器持久状态执行同一边界。
# 2026-07-28 Ticket 07 第三轮审查修复（Web 持久命令）

- 所有 ProductBrief Playwright 夹具已继承服务端 `retention_deadline`；V3 命令记录显式区分 `pending` 与 `version-conflict`。
- 新增浏览器公开接缝回归：修订返回 409 后，首次读取服务器当前版本返回 503，浏览器仍完整保存原命令；连续刷新只执行 GET 恢复、不重放 POST，并在人工“恢复/放弃”前持续显示冲突草稿。
- 新增过期清理回归：达到任务保留期限的本地命令在加载时删除，不发出 ProductBrief 请求，不恢复字段或修订原因。
- 清理 ProductBrief Workbench 已失效导入，并修正持久化结算与草稿恢复代码排版。
- 验证：
  - `pnpm --dir apps/web lint`：通过，0 warning。
  - `pnpm --dir apps/web typecheck`：通过。
  - `pnpm --dir apps/web test:unit`：46 passed。
  - `pnpm --dir apps/web build`：通过。
  - `pnpm --dir apps/web e2e e2e/product-brief-workbench.spec.ts`：18 passed。

# 2026-07-29 Ticket 07 第三轮审查修复（全量回归）

- OpenAPI 重导出前后 SHA-256 一致，生成 Web 类型 `--check` 通过。
- Ruff format：245 files already formatted；Ruff lint：通过。
- ProductBrief 单元/合约：104 passed。
- ProductBrief 真实 MySQL HITL：35 passed。
- ProductBrief/Operation 迁移往返：7 passed。
- Web 完整 Playwright：49 passed；BFF 代理合约：15 passed。
- 首轮全仓 pytest 暴露旧商品 API 测试仍依赖 422 `ctx`；已改为验证响应只含
  `type/loc/msg` 且不泄露 `ctx/input`，聚焦回归通过。
- 最终全仓 pytest：984 passed，3 skipped，2 个第三方 deprecation warnings；耗时
  18 分 25 秒。跳过项为显式 opt-in 的真实 Alibaba OSS 和 ClamAV 接缝。
- 主库 Alembic 位于 `d9e4f7a2b610 (head)`，`alembic check` 报告无新升级操作。
- 使用当前 Ticket 07 源码完成 `docker compose up -d --build --wait`；API、Worker、
  Scheduler、Web、MCP 与全部基础设施容器健康。
- API `/health/ready`、Web `/`、Scheduler `/health/live`、MCP `/health/live` 均返回 200。
- 从 Git index 导出独立快照，确认 12 个原未跟踪文件均进入提交树；在快照内重新执行
  `uv sync --frozen --all-packages`、`pnpm install --frozen-lockfile`、Python 包导入、
  Ruff、生成类型检查、Web TypeScript 与生产构建，全部通过。快照验证后已清理。

# 2026-07-29 Ticket 07 第四轮终审

- 三路全新独立终审均返回 `CHANGES REQUIRED`，无 Critical，共 12 个 Required：
  - repair 请求发出前 deadline 分类、每个 repair call 的独立 durable intent、MySQL 锁顺序、
    重分析期间 confirmed version 投影。
  - Transport cancellation/close deadline、压缩响应上限、API 进程 Secret 隔离、SSE 验证失败
    的精确版本清理、Provider request ID 遥测脱敏。
  - 浏览器恢复命令严格校验、专用最小权限 Workflow/Operation 投影与状态、mutation/BFF
    真实取消传播。
- 两个 Optional：version history N+1 与 1,900 行 Workbench 组件拆分；不替代上述正确性修复。
- 当前开始第四轮 TDD 修复，完成后重新执行全量门禁和全新独立终审。

# 2026-07-29 Ticket 07 第四轮审查修复

- 后端四项阻断已完成：deadline 分类只依据当前调用事实；每个 Vision repair call 拥有独立
  `(Operation ID, operation attempt, call_index)` 提交意图；行锁顺序固定为
  `Workflow -> ProductBrief -> Operation`；重分析期间已确认版本继续投影为 `CONFIRMED`。
- Provider/安全五项阻断已完成：Transport 的取消、响应读取/关闭和 shutdown 均受绝对截止
  时间约束；拒绝压缩响应；API 进程拒绝 Worker Vision Secret；对象写入后加密复验失败会按
  精确 Version ID 清理并证明不存在；不可信 Provider request ID 只以安全 token 进入遥测。
- Web/API 三项阻断已完成：持久化命令执行严格 canonical validation；Workflow/Operation
  读取改为 ProductBrief 专用最小权限投影并携带 Workflow 状态；组件、客户端和 BFF 全链路
  传播 AbortSignal，商品切换会真实取消在途 mutation 和上游请求。
- ProductBrief、Provider、Settings 和对象存储聚焦 Python 组合 `271 passed`；真实 MySQL
  ProductBrief 与迁移 `41 passed`；Vision Adapter/Transport `72 passed`。
- Web 门禁通过：ESLint、TypeScript、生成类型 `--check`、Vitest `59 passed`、BFF Proxy
  `16 passed`、production build 和完整 Playwright `51 passed`。
- 全仓 Ruff format/check 通过；主库与测试库均位于 `d9e4f7a2b610 (head)`，Alembic
  `check` 均无新升级操作。下一步执行全仓 pytest、当前源码 Compose 和第五轮终审。

# 2026-07-29 Ticket 07 全量测试环境诊断

- 首次全仓运行得到 `929 passed, 82 failed, 8 errors`，失败集中在共享 MySQL 集成夹具的
  `TRUNCATE`、并发状态与后续级联，不符合本轮业务修改的局部回归形态。
- 根因已由独立日志证实：此前用于轮询的隐藏 pytest 子进程没有随 `Start-Process` 宿主退出，
  从 06:09 持续运行至 06:30；新的全量套件在 06:10 启动。两者并发清理
  `commercevision_test`，后台套件也得到 `863 passed, 97 failed, 59 errors`。
- 两个进程退出后，测试库无活动事务；按相同排序执行 `--maxfail=1` 持续 15 分钟未复现失败，
  但被诊断命令自身的短工具窗口终止。下一次仅保留一个前台进程，并使用 33 分钟上限。
- 唯一干净全量进程最终通过：`1019 passed, 3 skipped, 2 warnings`，耗时 18 分 53 秒。
  三个 skip 仍为显式 opt-in 的真实 OSS/ClamAV 接缝；两条 warning 来自第三方
  Starlette TestClient 与 oss2 的弃用提示。相较此前 984 项，新增 35 项回归全部纳入。
- 后续发布门禁通过：Ruff format `248 files`、Ruff lint、生成 Web 类型 `--check`、
  Compose config 和 `git diff --check` 均绿色；Python 与 pnpm 漏洞审计均报告
  `No known vulnerabilities found`。生成文件仅有现存 CRLF/LF 工作区提示。
- 当前工作树完成 `docker compose up -d --build --wait`，API、Worker、Scheduler、MCP、
  Web 及全部基础设施服务 healthy；权限收敛、迁移和对象存储初始化任务均 `Exited (0)`。
- `scripts/verify_phase0.py` 全项通过；API `/health/ready`、Web `/`、Scheduler 与 MCP
  `/health/live` 均返回 200；最近 15 分钟应用日志无 `ERROR/Traceback/CRITICAL`。
- 提交边界共扫描 104 个 tracked diff/untracked 文件，高置信 GitHub/OpenAI/AWS/
  Private Key 前缀候选为 0；最终推送后仍由远程 Gitleaks 执行全仓门禁。

# 2026-07-29 Ticket 07 第五轮终审

- 三路全新只读审查均返回 `CHANGES_REQUIRED`，去重后 11 项：
  - repair 取消查询未按 `call_index` 关联；Workflow 投影未绑定 ProductBrief/SQL retention；
    版本历史无界且 N+1。
  - post-write 复验异常可能遗留精确对象版本；外部 request ID 的凭证形状仍可进入遥测；
    exact source object 完整性异常未归一为稳定终态。
  - raw provider artifact 在 durable ledger 之前写入；ProductBrief 读取暴露内部 Provider 拓扑；
    外部 UUID 非规范形式破坏幂等 scope。
  - Web 持久 revision 接受空 evidence；冲突恢复显示的 evidence 与最终提交值不同。
- 当前先在三个互不重叠写入域执行 TDD；artifact ledger 单独做跨层设计核对，避免把
  Ticket 13 的物理删除延期误用为 Ticket 07 可发现性/可对账缺口的豁免。
- Provider/存储域完成：S3/MinIO 与 OSS 的 post-PUT 复验异常均执行精确版本清理和不存在
  证明；外部 Provider request ID 在日志/指标/span 前统一 token 化。聚焦组合 `136 passed`。
- Web 恢复域完成：持久 revision evidence 强制 `1..32` 并在网络前失败关闭；生成器保留
  `minItems` 非空 tuple；冲突恢复展示与提交相同 draft evidence。Web unit `64 passed`、
  proxy `16 passed`、build 与定向 Playwright 通过。
- 取消/投影域完成：Provider Attempt/Call 按 `call_index` 相关；repair call 1 在途时取消
  返回 409；绑定投影在 SQL 中约束 Workspace/ProductBrief/Workflow/Operation kind/target/
  retention，过期仅做字面探针。真实 MySQL `40 passed`，路由 Unit `3 passed`。
- 投影变更同时揭示首次分析尚无 ProductBrief 可绑定；新增独立 worker 将实现仅允许活动
  `UNDERSTANDING` Workflow 的 pre-analysis 最小投影，已存在 brief 的确认/重分析仍走精确绑定。
- artifact ledger 设计确认必须在 Ticket 07 落地：独立 child aggregate 在写前冻结物理目标，
  写后立即结算 exact Version ID，歧义按持久 key 对账且绝不调用 Provider。实现 worker 已启动。
- 构建上下文审计发现本地 `.mypy_cache` 约 124 MiB 未被 Git/Docker 忽略；已增加两层忽略
  规则和部署 Contract，`test_worker_deployment.py` `15 passed`，Compose config 通过。
- Artifact Ledger 合并后的首轮 ProductBrief HITL 回归发现 4 个旧接缝不一致：两个测试仍用
  “进入 Analyzer 函数”代替“越过 durable submission intent”，成功场景仍断言零字节 artifact，
  同一 attempt 重放则在 submission fence 前先碰到临时 URL 导致的 artifact 内容漂移。
- Executor 已在任何新 source URL 或 artifact 写入前拒绝已有 Provider Attempt 的同 attempt
  重放；测试改为观察 durable submission 边界和真实字节证据。高风险子集 `11 passed`，
  完整 `test_product_brief_hitl_mysql.py` `43 passed`。
- 首轮全仓回归为 `1080 passed / 3 skipped / 1 failed`：迁移总账的
  `WORKSPACE_ID_TABLES` 未登记新表 `product_brief_provider_artifacts`，数据库实际 collation
  已正确。补齐总账后精确迁移测试通过。
- 第二轮全仓回归暴露 Phase 1 冻结时钟测试的 1 秒竞态：Worker 初始化慢于 1 秒时，新建
  Outbox 相对冻结 Dispatcher 时钟落在未来。冻结时间改为在 Workflow 创建后推进；参数化
  场景连续 6 轮、共 12 项通过。
- 高系统负载下，两个既有进程测试把 Python import/subprocess 启动延迟误判为线程或容量
  泄漏。凭证测试现独立测量刷新后的退出时长；C2PA 测试允许明确的 bounded transient，
  但同一 Adapter 最多 3 次内必须恢复 EVIDENCE。两项连续 5 轮通过。
- 资源审计发现本项目旧 Worker 容器约占 31% CPU；停止不参与 pytest 的应用容器、保留
  MySQL/MinIO/RabbitMQ/ClamAV 等基础设施后，唯一最终全仓进程通过：
  `1081 passed, 3 skipped, 2 warnings`，耗时 21 分 46 秒。两条 warning 仍来自第三方
  Starlette TestClient 与 oss2。

# 2026-07-29 Ticket 07 第六轮终审与分段全量证据

- 分段 integration 首轮为 `368 passed, 2 skipped, 1 failed`；唯一失败来自旧上传夹具在宿主机
  使用容器 DNS `rabbitmq:5672`。夹具改用可配置宿主机 AMQP 地址，远程 CI 增加真实 RabbitMQ
  service 后，失败用例通过，完整上传集成文件重跑为 `90 passed, 1 skipped`。
- 第六轮三路全新独立审查均返回阻断合入，无 P0；去重后共 13 个 P1/P2：
  - continuation 未以 MySQL 权威时间和 retention 状态拒绝过期 Workflow/ProductBrief；
    已被重分析取代的迟到人工或 policy continuation 会进入重试/DLQ。
  - ProductBrief 未在服务、投影和 Worker 三层严格限定
    `COMMERCE_IMAGE_GENERATION` 及冻结 `product_id`。
  - Web 未在权威 410 后立即清理内存/sessionStorage；恢复记录未把 Workspace 纳入 schema、
    key 和解析；Operation 终态分类在组件与 Controller 重复分叉。
  - Provider Artifact Ledger 迁移在 MySQL DDL 隐式提交下缺少 trigger 恢复栅栏，降级会丢失
    `INTENDED/UNKNOWN` 等未结算事实；ProductBrief 历史只禁 UPDATE、仍可被 DELETE 擦除。
  - Vision Adapter 反向依赖 Domain；Transport 取消后未有界等待任务/连接清理；Worker 聚合
    close 不是逐项 best-effort。
  - ProductBrief Repository Port 聚合二十余方法，未按 Brief/Version、Analysis/Call、
    Artifact Ledger、Confirmation 形成窄 seam。
  - 正式 Workflow 状态机文档未记录 `RETRIEVING -> UNDERSTANDING` 和
    `RETRIEVING -> AWAITING_PRODUCT_CONFIRMATION`。
- 四个新鲜独立上下文已按后端 continuation、Web、MySQL 迁移、Provider/持久化 seam
  分配互斥写入范围，全部要求先红测、后实现、聚焦回归和自审；主控负责文档、集成、
  Compose 验收、最终复审与 Ticket 07 单一提交。

# 2026-07-29 Ticket 07 第十轮后端红绿修复

- 已确认测试 seam：真实 MySQL Recovery/Worker Event、ProductBrief HTTP/Service、Durable
  Provider Executor、Agent Runtime/Checkpointer 与 Contracts。
- 已固定六项成功标准：MySQL 权威时间、recovery fence、Commerce no-generation 可执行收敛、
  三层 Workflow/Product binding、Checkpoint 零 raw lease token、完整聚焦门禁。
- 当前开始 B10-1；尚未修改生产实现。
- B10-1 RED：`test_recovery_uses_mysql_time_instead_of_the_scheduler_clock` 在真实 MySQL 下按预期
  失败，Worker clock 比 DB 快 2 小时时错误回收了 DB 仍有效的 Step，实际 `(1, 0)`、期望
  `(0, 0)`。
- B10-1 GREEN：`RecoveryService.recover_once` 进入同一 UoW 后只采样一次
  `uow.database_now()`；目标真实 MySQL 用例 `1 passed`，lease query、retention、stale threshold
  与 recovery event 共用该时间。
- B10-2 RED：真实 MySQL 中首次 recovery event 标记 published、但暂不消费后，再次
  `recover_once()` 仍返回 `(0, 1)` 并生成重复事件。
- B10-2 GREEN：recovery event 与 `record_recovery_observation`/Workflow save 同事务提交；
  `test_published_recovery_event_advances_scanner_freshness` 为 `1 passed`，第二次扫描稳定 `(0, 0)`。
- B10-3a RED：pre-ProductBrief Commerce Workflow 的 scanner 仍写出无 ProductBrief identity 的
  generic Graph event，目标用例捕获到 1 条 `stale_workflow`。
- B10-3a GREEN：Commerce `none` 只推进 recovery observation，不创建 Graph event；
  `test_stale_commerce_workflow_before_product_brief_is_observed_without_graph_recovery` 为 `1 passed`。
- B10-3b RED：policy-confirmed ProductBrief 在首个 retrieval Step claim 前丢失原 continuation 后，
  scanner 虽返回 `(0, 1)`，但没有生成任何带当前 Version identity 的 `stale_workflow` recovery
  event；真实 Worker/Event 用例在 `len(recovery_events) == 1` 处按预期失败。
- B10-3b GREEN：Generation resolver 会从当前 confirmed Brief/Version/Confirmation 重建 recovery
  identity；retrieval gate 在无旧 Step 时允许首次创建 generation。真实 Scanner → Outbox →
  Worker → Graph 用例 `1 passed`，Workflow 到达 `AWAITING_PLAN_APPROVAL/approve_plan`。
- B10-4a RED：Workflow type 在 revise/confirm 加锁前无版本漂移篡改后，两条 HTTP 命令都错误
  返回 `200`；真实 MySQL 用例分别捕获确认和修订跨越了 Commerce Workflow 绑定。
- B10-4a GREEN：revise/confirm 在同一锁内用 `uow.database_now()` 验证 Workflow type、冻结
  `product_id`、Workflow retention 状态/deadline 与 ProductBrief deadline；type/product/retention
  参数化回归 `6 passed`。
- B10-4b RED：`product-brief.requested` 已持久化但 Worker 尚未消费时篡改 Workflow type，
  `CountingAnalyzer.call_count` 错误成为 `1`。
- B10-4b GREEN：Executor 初始门禁及 artifact/submission lifecycle 的每次锁内复验均验证同一
  authority；消费前零调用参数化回归 `3 passed`，analyzer 已进入但 provider 尚未提交的并发
  漂移回归 `3 passed`，均稳定报 `PRODUCT_BRIEF_WORKFLOW_NOT_EXECUTABLE`。
- B10-5 RED：InMemory Saver 的 START/loop 历史可从 `channel_values` 直接解码出 live
  `initial_step_lease_token`；新增公开历史断言按预期失败并显示原始 token。
- B10-5 GREEN：Agent State 删除 raw token 字段，checkpoint namespace 只接受已持久的不可逆
  generation hash；`FixtureAgentRuntime.run` 用 `ContextVar` 在调用栈内交付 Step/Lease，
  `finally` 重置。真实 MySQL history 同时检查 config、metadata、parent config、checkpoint 与
  pending writes，lease-expiry crash recovery、DB-ahead/checkpoint-behind 与首次 claim 前恢复
  均保持可执行。
- B10-5 metadata RED/GREEN：`ProductBriefGenerationAuthority.from_step()` 原先会接受
  `initial_step_lease_token` 或任意未知额外键；参数化用例 `2 failed` 后改为 exact-key schema，
  `approval_id` 必须存在但允许 `null`，目标 Unit `5 passed`、三条真实 crash/recovery
  MySQL 回归 `3 passed`。
- B10-6 完整聚焦门禁：ProductBrief HITL + Reliability MySQL `89 passed`；Runtime/API
  Workflows/Contracts/Graph `51 passed`；最终 Agent/Completion Unit `17 passed`（其中完整
  Agent + Completion 组合在 metadata 收紧前为 `16 passed`，新增 nullable approval 用例单独
  转绿）。目标文件 Ruff format/check 全绿，`git diff --check` 仅报告两个既有 Web/OpenAPI
  CRLF 提示、无 whitespace error。

# 2026-07-29 Ticket 07 最终生产验收

- 第六轮 13 个 P1/P2 已全部按独立写入域完成红绿修复：过期/stale continuation、
  Commerce Workflow/Product 绑定、Web 410/Workspace 恢复、Provider Artifact 迁移与
  append-only、Provider seam、Transport cancellation、best-effort close 和窄 Repository ports。
- 三路全新终审及其第二次独立复核继续发现并关闭了提交前边界：
  - Commerce Task 的公开入口、legacy 168 小时 Workflow、ProductBrief/Analysis/Provider
    artifact/call/continuation 与 pre-analysis 投影统一使用
    `min(workflow.expires_at, workflow.created_at + 72h)`；非 ACTIVE retention 立即 410。
  - Web 在任何 revise/confirm POST 前要求 exact durable command，核心回包同时绑定
    Workspace、Product、ProductBrief、Workflow 和 Operation；Storage getter 拒绝也会
    fail-closed，而不会绕过恢复 UI。
  - 默认 deterministic Compose 使用仓库内受控空白 credential fixture，干净 clone 可启动；
    Alibaba 未显式挂载真实 Secret 时在消费任务前失败关闭。
- 最终 Python 门禁：unit `668 passed`；contract `165 passed, 1 skipped`；28 个 integration
  文件按四个互斥组得到 `27 + 111 + 124 + 178 = 440 passed, 2 skipped`。显式 skip 仅为需要
  外部真实 OSS/ClamAV host 的 opt-in 接缝；Compose 内 ClamAV readiness 实测为 `ok`。
- 最终 Web 门禁：unit `122 passed`、BFF proxy `18 passed`、Playwright `71 passed`；
  TypeScript、ESLint、生成 API 类型和 Next production build 全部通过。
- Alembic 主库位于 `a4c8e7f3b219 (head)` 且 `check` 无漂移；隔离库完整
  upgrade/downgrade/re-upgrade 和 runtime DML-only 权限测试通过。
- Ruff format/check、OpenAPI 重导出哈希、Compose config、`git diff --check`、Python/pnpm
  漏洞审计全部通过，均无已知漏洞。
- 最新源码执行 `docker compose up -d --build --wait` 后全部服务 healthy；API、Web、
  Scheduler、MCP 均 HTTP 200，Worker marker 新鲜且所有依赖为 `ok`，本次启动日志
  `ERROR/CRITICAL/Traceback/RuntimeWarning` 为 0，`scripts/verify_phase0.py` 全项通过。
- 最终只读闭环结论：Ops 与 Web 为 `APPROVE`，后端无 Required/P0/P1；最后一个短于 72 小时
  deadline 的 P2 测试覆盖已补齐。Ticket 07 本地验收完成，Ticket 08 未启动。

# 2026-07-30 Ticket 07 远程验收与 Ticket 08 启动

- Ticket 07 单一实现提交 `26245f9` 已推送到 `origin/main`；本地 `HEAD` 与 `origin/main`
  完全一致，工作树干净。
- GitHub Actions 运行 `30482611560` 已全部通过：Python checks、Web checks、
  Container builds、Security/Gitleaks 与 SBOM 均为 `success`。
- Ticket 08 已按独立上下文启动；当前只恢复锁定规格、领域语言、Rights/Asset 现有能力和
  HTTP/Web 接缝，不复用 Ticket 07 的隐式实现假设。
- Ticket 08 发布门禁固定为：不可变 Brand Profile 版本、发布时当前 Rights 重检、历史内容与
  当前可用性分离、Rights/Asset 变化驱动 `NEEDS_REPUBLISH`、跨 Workspace 隔离、并发发布
  乐观锁，以及 OpenAPI/Web 完整纵向流程。
- 三个只读独立审计已并行启动，分别覆盖领域/迁移、Rights/Event 传播和 HTTP/Web 契约；
  审计结果用于确定首批公开接缝 RED tests，尚未修改生产实现。
- Ticket 08 第一条 Domain TDD 红灯已观察：`tests/unit/test_brand_profile_domain.py` 在收集时
  因 `BrandColor` 等 Brand Profile 领域类型尚不存在而失败；红灯覆盖不可变发布、精确
  Asset/Rights 内容哈希、乐观草稿更新、迟到失效事件围栏和二进制精确身份。

# 2026-07-30 Codex Windows 沙箱恢复

- `CreateProcessAsUserW failed: 1312` 已定位为 Microsoft Store/MSIX PowerShell 的
  `WindowsApps\pwsh.exe` 无法由 `CodexSandboxOffline` 令牌启动；不是仓库权限或项目代码问题。
- 已用官方 MSI 安装并校验 PowerShell `7.6.4`，`pwsh.exe` Authenticode 状态为 `Valid`；
  Codex 完整重启后命令路径为 `C:\Program Files\PowerShell\7\pwsh.exe`。
- 修复后以受限身份 `laptop-5jnpnl1v\codexsandboxoffline` 连续执行 20 次独立命令，
  结果为 `20/20` 成功，未降低 `[windows] sandbox = "elevated"` 的安全边界。
- `uv` 后续统一使用授权项目根下的隔离缓存
  `D:\个人项目\电商生图agent\.codex-uv-cache`；pytest 禁用不可写的旧 `.pytest_cache`
  provider，避免把正常的用户级缓存拒绝误判成沙箱启动故障。
- Ticket 08 Domain focused gate 在恢复后通过：`9 passed`。

# 2026-07-30 Ticket 08 纵向实现继续

- 三个共享工作区 Worker 已恢复：Application/HTTP、MySQL/Alembic、Rights Event/Worker
  各自保留独立文件所有权；主控负责 Web/BFF、接口收拢和最终门禁。
- Contracts 与 Application persistence seam 已先行落盘：mutation 回包不缓存动态
  usability，Version GET 才按当前 Asset/Rights 事实重新决策；UoW 组合三组窄端口。
- Web BFF 的 Brand Profile 精确 allowlist 完成一轮 RED→GREEN：新增集合 GET/POST、
  identity GET、draft PUT、validate/publish POST、分页版本列表与按发布序号读取；未知 action、
  非 UUID 和嵌套历史路径仍返回 404。Focused proxy 从 `18 passed, 1 failed` 收敛为
  `19 passed`。
- Web API client 从缺失模块 RED 收敛为 GREEN，覆盖创建/草稿更新/校验/发布、opaque cursor、
  不可变发布序号、调用方取消和稳定 409 envelope；全量 Web unit 由原 `122 passed` 增至
  `127 passed`。
- Workbench controller 从缺失模块 RED 收敛为 GREEN，公开接缝覆盖租户/品牌切换后的迟到响应、
  optimistic mutation 精确回包、409 本地草稿保留与显式恢复/丢弃、validation 版本绑定、
  bounded cursor 去重和同一历史版本的动态 usability 刷新；全量 Web unit 现为 `133 passed`。
- Windows 下 `pnpm --filter ... exec vitest` 未解析 workspace 本地 binary；未重复该调用，
  改用仓库锁定的 `test:unit` script 执行同一真实测试集。
- Ticket 08 Application/API worker 已完成锁 profile → 稳定顺序锁 Asset → 单次 MySQL
  `database_now()` → 最终 Rights 复核；幂等 publish replay 重新水合当前 identity/head，不缓存
  `ACTIVE` 动态可用性。其 focused unit/contract 为 `40 passed`，MySQL 为 `8 passed`，
  API 回归为 `20 passed`。
- Ticket 08 Persistence worker 已完成三表模型、exact composite FK、binary identity、CAS、
  append-only/identity-delete triggers、单调分页、live-head invalidation fence 和拒绝有历史数据的
  downgrade；真实 MySQL/migration/runtime privilege 为 `11 passed`，Application + MySQL +
  migration roundtrip 为 `18 passed`，并显式声明 `commercevision-application` 依赖。
- Web 第六轮审查的首批 8 个公开接缝 RED 均按预期失败，覆盖 validate 缺少 Actor、
  跨 Workspace/错误 UUID/枚举/超限分页未 fail-closed、create 未绑定 profile key、
  mutation 未撤销旧 validation、dirty refresh 丢失冲突语义和非破坏刷新切换 profile。
- Web API 边界新增有界运行时 decoder，校验 Workspace/Brand/Profile identity、canonical UUID、
  枚举、UTC 时间、完整 Draft、列表上限/cursor、历史成员 exact mapping 和当前 Rights 结果；
  validate 的 `X-Actor-Id` 与 Idempotency-Key 已解耦。Controller 绑定 create profile key +
  baseline selection，并在 mutation 开始即撤销 validation、保留 dirty refresh。
- 上述 RED 转 GREEN 后，全量 Web unit 为 `147 passed`。TypeScript 当前仅等待并行 UI worker
  合入 `profileKey` 新契约及将测试夹具 `ALLOWED` 改为 `AUTHORIZED`；ESLint 无 error，
  decoder 的单个未使用 helper warning 已删除。
- Python 分段基线已重新建立：完整 unit 在把 `TEMP/TMP` 指向工作区外层可写隔离目录后为
  `707 passed`；首次 28 个错误全部来自 Windows pytest `tmp_path` 无法写入用户 Temp，
  与业务代码无关。完整 contract 最终为 `166 passed, 1 skipped`。
- Contract 门禁曾稳定复现 Windows 高负载下 C2PA 隔离子进程的 1.5 秒启动预算耗尽；
  保持 Linux 1.5 秒严格预算和生产 10 秒默认不变，只把 Windows 测试装置预算设为 3 秒并
  输出相对预算的耗时诊断。目标循环 `5/5` 通过后完整 contract 转绿。
- Brand Profile 路径参数已用 RED test 证明 OpenAPI 原先错误宣称大写 UUID 合法；公开
  schema 改为 lowercase canonical UUID pattern，route/contract `6 passed`，OpenAPI 与
  TypeScript 类型已重新生成且生成器 `--check` 通过。
- 第六轮后端独立审查没有 P0，但发现历史版本当前可用性的数据库时点竞态、使用 Host
  event time 造成永久漏失效、Asset 删除事件缺少 Brand Profile 纵向消费路径，以及
  Rights 子表查询缺少显式 Workspace/Asset 谓词。Persistence/Worker 修复正在以真实 MySQL
  并发、慢时钟和重放测试闭合；完整 integration 门禁在这些修复落地前暂不启动。
- 主控追加 Contract/Domain RED 证明 Pydantic 会把 `true`、`3.0` 和 `"3"` 静默转换为
  optimistic version，且 draft 会把数字/字符串转换为 derivative flag；13 个目标断言按预期
  失败。所有 Brand Profile 命令与响应整数/布尔字段改为 strict，Domain 聚合同时拒绝 bool、
  float 和 string 版本，目标套件转为 `26 passed`。
- Web Controller RED 证明重新校验期间旧 validation 仍可见，且后续历史页 `[4]` 可被追加到
  已有 `[3,2,1]`。`beginValidation` 现在原子撤销旧结论；history 接缝允许重叠去重但拒绝任何
  不低于既有尾部的新版本，并在当前响应协议违例时清除 loading，目标套件 `10 passed`。
- BFF RED 证明 Brand Profile 正则的大小写不敏感标志仍会把大写 UUID 上送至已要求
  lowercase canonical UUID 的 API，缺少身份头时甚至先返回 403 而不是本地 404。五条 Brand
  Profile path allowlist 已改为大小写敏感，BFF 全套 `19 passed`；既有其他资源策略未改变。
- Ticket 08 第六轮后端修复现已完成：历史 usability 在同一 `FOR SHARE` 批次返回
  `snapshots + decided_at`；失效统一按 Profile head → Asset/current Rights → 单次数据库时间
  加锁，并把慢时钟事件时间降为审计事实；typed `asset.delete.completed` 已由 Worker
  fail-closed 校验 Workspace/Aggregate/generation 后走同一 CAS 失效路径。门禁为完整 unit
  `731 passed`、contract `166 passed, 1 skipped`、Brand Profile focused `67 passed`、
  migration/真实 MySQL/runtime privilege `14 passed`。
- Ticket 08 Web 已把 Controller 改为 `subscribe + useSyncExternalStore` 单一快照源，并将
  未确认命令提升为显式 authority：durable pending record 存在期间冻结编辑、校验、切换和刷新，
  只有精确恢复/清理后解锁，避免下一条写命令覆盖唯一恢复记录。最终 Web unit `154 passed`、
  proxy `19 passed`、typecheck/lint/build 通过、Brand Profile Playwright `7 passed`。
- 补充独立后端审查无 P0/P1，提出 3 个 P2 与 1 个 Required。事件 identity/publisher 严格化
  与 create 幂等 scope 已按 `9 failed, 18 passed` RED → `27 passed` GREEN 修复；Ruff
  import-order Required 同步关闭。游标修复不是扩充 JSON 字段，而是新增查询绑定、HMAC
  防篡改、current/previous 轮换和统一错误的深模块，核心 `50 passed`，正在接入组合根。
- 分组 integration 定位确认单体门禁先前的失败来自两处跨 Ticket 迁移测试硬编码：
  Operation migration 的 workspace collation 集合未包含三张 Brand Profile 表；Provider
  Artifact “失败前无 DDL”测试从 `head` 跨越后续非事务 DDL 再检查旧迁移。两项均先稳定复现
  RED，再分别以扩充 head 集合及固定到被测 revision 边界修复，focused 为 `1 passed` 与
  `4 passed`；已完成的其它分组累计未再出现业务失败。

# 2026-07-31 Ticket 08 最终本地发布门禁

- 按交接顺序完整恢复 `AGENTS.md`、三份 planning 文件、`CONTEXT.md`、`PLAN.md`、Phase 2
  spec、Ticket 08、Brand Profile Runbook 与 ADR-006/007；未重新规划或重复已完成实现。
- Git 基线核验通过：`HEAD` 与 `origin/main` 均为
  `26245f9b604255befc8ab9a0a9fdc429bd676591`，分支为 `main`；恢复时为 42 个 tracked
  modified、48 个 untracked、0 staged，和 Ticket 08 交接快照完全一致。
- 最终 Python 证据：unit `812 passed`；contract `167 passed, 1 skipped`；integration
  四组 `151 + 68 + 152 + 90 = 461 passed, 2 skipped`；Brand Profile MySQL/Trusted Actor
  focused `15 passed`；Asset Rights/runtime MySQL `30 passed`。显式 skip 仅为真实 Alibaba
  OSS 与 live ClamAV 接缝。Ruff format/check 与 Python 依赖审计均通过，无已知漏洞。
- 最终 Web 证据：unit `176 passed`、BFF proxy `21 passed`、全量 Playwright `87 passed`
  （Brand Profile `16` 条）；TypeScript、ESLint、生成 API 类型、production build 和 pnpm
  audit 全部通过，无已知漏洞。
- 数据库与静态门禁通过：Alembic 单一 head 为 `b8e1d4f7a203`，parent 为
  `a4c8e7f3b219`；`alembic check` 无 drift，upgrade/downgrade/re-upgrade、运行时 DML-only、
  OpenAPI 重导出、生成 TypeScript、Compose 自定义 URL-safe RabbitMQ 凭证和
  `git diff --check` 均通过。
- Backend、Ops、Web 三路最终独立审查均无剩余 P0/P1/P2/Required；本轮没有修改生产代码，
  因此不重复整轮语义审查。
- 最新 Compose 应用实例已证明不是旧容器：API、Worker、Scheduler、MCP、Web、Migrate 和
  Object Storage Init 的容器 `.Image`、Compose image label、当前本地 tag ID 与 repo digest
  全部精确一致，且本地不存在更新的同服务 tagged/untagged image。五个长期应用服务 healthy，
  两个一次性任务 `Exited (0)`。
- HTTP 验收全部返回 200：API `/health/ready` 的 configuration/MySQL/Valkey/RabbitMQ/
  Object Storage/Milvus 均为 `ok`；Web `/` 返回 UTF-8 `zh-CN` 页面；Scheduler
  `/health/ready` 为 `ok` 且 `last_error=null`；MCP `/health/live` 为 `ok`。
- `uv run python scripts/verify_phase0.py` 完整通过 Web、API live/ready、Scheduler、MCP、
  MinIO、Milvus、OTel、MySQL、Valkey 和 RabbitMQ 共 11 项检查。
- 本轮轻量复核通过：Ruff `289 files already formatted`、Ruff lint、生成 Web 类型
  `--check`、`docker compose config --quiet` 和 `git diff --check`。运行态日志交接证据为
  348 行、`ERROR_SIGNATURES=0`。
- 范围审计确认 `apps/web/debug.log` 的 17 行全部是 Chromium GPU SharedImage runtime
  diagnostics，不含应用业务日志；已用仓库本地 `.git/info/exclude` 安全排除，未删除或覆盖。
  其余 47 个 untracked 文件均属于 Brand Profile 或共享 E2E 基础设施。
- Ticket 08 已达到本地完成条件并更新为 `complete`；下一步只允许形成单一实现提交、推送并
  等待对应 GitHub Actions 全绿。Ticket 09 继续保持 `pending`。

# 2026-08-03 Ticket 10 PRODUCT_FUSED/CJK 收口

- confirmed ProductBrief 的受控文本、稳定 hash、PRODUCT_FUSED Embedding/Search Document、
  typed Outbox 与 Durable Operation 已在单一 MySQL 事务中落地；并发请求只保留一个胜者，
  不变输入幂等，变更后的确认版本生成新记录并使旧记录/文档收敛为 STALE 或精确删除。
- IMAGE 与 PRODUCT_FUSED 共用同一索引请求、authority、executor、Provider 和 Milvus adapter；
  Provider 仅额外允许显式配置的融合预处理 identity，没有建立第二套并行框架。
- rights 撤销覆盖 PENDING 与已写入两种边界：未外写记录原子 STALE，已写入记录进入
  DELETE_PENDING 并发送 generation-fenced 删除；重授权从 Asset rights event 找回依赖该
  Asset 的当前 confirmed ProductBrief，复用稳定 record/document 并创建新 operation epoch。
- MySQL CJK ngram FULLTEXT 已用中文、英文和混合字面 fixture 验证，并用 `FORCE INDEX` +
  `EXPLAIN` 锁定 `ft_product_search_cjk`；非 INDEXED、撤权或永久失败文档不会参与 lexical 查询。
- Milvus ANN primitive 仅接收规范 lowercase UUID 的 MySQL-eligible record 集合，强制 Workspace、
  Vector Kind、eligible IDs、Strong consistency、结果数量/排序/主键 generation 围栏；真实
  Milvus 中文、英文、混合三组 fixture 均只返回 eligible PRODUCT_FUSED 候选。
- 新增模块与迁移组合门禁通过：Ticket 10 单元模块 `109 passed`；PRODUCT_FUSED + 两组迁移
  MySQL `18 passed`；PRODUCT_FUSED 独立 MySQL 套件 `12 passed`（新增 terminal 同步后待下一轮
  完整重跑确认最终计数）；真实 Milvus ANN `3 passed`。Ticket 11 仍保持 pending。
- PRODUCT_FUSED + 完整 IMAGE MySQL + 真实 Milvus 的首个组合回归在 7 分钟工具窗口到期后被宿主
  终止，终止前无失败输出但不计作通过；后续拆为短套件与完整 IMAGE 独立长窗口，避免重复该组合。
- 质量修复后的纵向复验 `26/27` 通过，唯一失败为旧测试仍期待旁路 `products.title`；实现已按
  确认态 `common.identity.display_name` 正确输出 title，approved `summer` label 仍存在。同步断言后
  重跑，未将该过期断言误报为业务回归。
- 合并前五轴自审关闭 4 个 Required：旁路 Product title、阿里 Qwen3 融合请求/响应 shape、
  superseded Brief generation 删除被 current-version 检查阻断、稳定 UUIDv5 Embedding ID 被 ANN
  契约误拒；并补齐 TASK lexical MySQL-time retention fence 与 title 列长边界。
- 质量修复后最终 Python 证据：完整 unit/contract `1120 passed, 1 skipped`；PRODUCT_FUSED、真实
  Milvus 与迁移组合 `28 passed`；完整 Ticket 09 IMAGE MySQL 兼容 `36 passed`。唯一 skip 仍为
  需要显式真实凭证的 Alibaba OSS live contract。
- 最终身份审查发现确认版 UUID 不应进入稳定 fused input hash，否则规范化后内容完全等价的新版本
  会撞到同一确定性 Record ID 却因 provenance 不同而失败。稳定 hash 现在绑定 ProductBrief 本体、
  Asset 内容、受控文本与 Provider 配置；等价新版本在同一事务中只推进 Embedding/Document provenance
  与 retention，不发送新索引事件。不同 ProductBrief 即使复用同一图片和文本也生成隔离记录。
- 旧版本事件回放只在其 ProductBrief Version 确属同一 Brief 且 controlled-text/input/operation 身份
  全部一致时通过；随机或跨 Brief provenance 继续失败关闭。等价版本、旧事件回放、篡改拒绝和
  跨 Brief hash 隔离均已进入公开测试，PRODUCT_FUSED 独立单元/MySQL 套件为 `20 passed`。
- 最终静态与发布审查通过：`uv lock --check`、Ruff format/check（`322 files`）、Python 依赖审计
  （无已知漏洞）、Compose config、OpenAPI 重导出、Web generated API types、`git diff --check`
  全部通过；Alembic 唯一 head 为 `f5a1c3e7b902` 且 `alembic check` 无 drift。变更文件凭证前缀
  扫描无命中，安全/正确性/性能/可维护性/简化五轴终审无剩余阻断项。
- Ticket 10 单一提交 `f1d8bf0a71ffb181b6aa59c9b0f66d4dc9d2c169` 已推送并由精确 GitHub
  Actions `30800574595` 验证全绿：Python、Web、Container builds、Gitleaks/SBOM 均成功。
  Ticket 11 的 blockers 已全部清除，现按冻结工单进入 Rights-first hybrid retrieval 实现。

# 2026-08-03 Ticket 11 Rights-first hybrid retrieval 收口

- 结构化 Retrieval Query、MySQL 当前权利 eligible/final fence、IMAGE/PRODUCT_FUSED Dense、CJK
  FULLTEXT、Brand Profile、显式引用、versioned RRF、受界 rerank、按 Version/Asset/hash 去重与
  Citation 证据已落地；selected 与全部 replacement 候选只做一次最终 MySQL 当前权利复核。
- eligible set 与 `candidate_limit` 已彻底解耦：Dense catalog、FULLTEXT、Brand Profile 和 Milvus
  均按 1000 ID 分块并确定性全局归并；真实 MySQL 用 1001 个 distractor 跨块证明合法候选不会在
  进入 ANN 前被截断。融合后的总候选池再受 `candidate_limit` 约束，并保留必需品牌成员。
- Retained Retrieval Run 保存规范 Query hash、策略、候选收敛计数、耗时、降级和精确 Citation；
  预览 capability 只保存 SHA-256，交换绑定 Workspace/Requester/Run/Rank、使用数据库时间并再次
  复核当前权利。URL、Header、MIME 与 10 MiB 边界失败关闭，浏览器 Blob URL 在 60 秒内、替换、
  新检索或卸载时撤销。
- Retrieval Explorer 已接入商品工作台，展示过滤器、通道、RRF/原始分数、Rights 版本、决策时间、
  eligible→fused→final 收敛、耗时与 degradation；375px 浏览器验收无横向溢出，交互目标不小于
  44px，受控预览不把签名 URL 写入 DOM/持久化状态。
- 本地发布证据：Python unit `977 passed`；contract `178 passed, 1 skipped`（仅既有真实 Alibaba
  OSS live contract）；PRODUCT_FUSED + Retrieval + Alembic roundtrip 真实 MySQL `22 passed`；
  Web unit `189 passed`、BFF `22 passed`、Playwright `90 passed`，TypeScript、ESLint、生成类型与
  production build 全部通过。Alembic head `a3f8c2d9e714`、schema drift 和 upgrade/downgrade/
  re-upgrade 均通过。
- 依赖审计（Python/pnpm）无已知漏洞，Compose config、Ruff、OpenAPI/TypeScript 生成物和
  `git diff --check` 已通过。代码简化与 Standards/Spec/安全/正确性/性能/可维护性多轴终审未发现
  剩余阻断项；Ticket 12 MCP 未启动。
- Ticket 11 实现提交 `32d3affe74bf69f9eef71325a4f7ef4e81faaa12` 推送后的首轮 CI
  `30811542093` 仅有旧 Operation migration 契约清单漏列 `retrieval_runs/retrieval_results`；
  生产迁移本身及两表 `workspace_id` collation 均正确。失败在本地精确复现后以两行测试契约修复，
  原测试转绿，Operation migration + 完整 migration roundtrip 为 `6 passed`。
- 修复提交 `dccacecc91f8f5d3869d15142c0b728150fab21b` 对应 GitHub Actions
  `30813127324` 已完成且全绿：Python、Web、Container builds、Security/Gitleaks 与 SBOM 均成功；
  本地与远端 `main` 精确一致，Ticket 11 正式完成并解锁 Ticket 12。

# 2026-08-03 Ticket 12 Product Catalog 与 Asset MCP 本地收口

- 五个固定版本化 MCP 工具已作为现有 Application Service 的只读入站 Adapter 落地；公开参数
  不包含 Workspace、actor、scope、purpose、provider、budget、URL、SQL、Bucket、对象 key、
  文件路径、模型 ID 或 Secret reference，全部身份与预算来自服务端验签上下文。
- HMAC trusted principal 支持 current/previous key 轮换、常量时间签名验证、签发时间与 32 KiB
  token 上限；Tool Gateway 对 input/output、scope、参数/结果字节、tool/policy version 和稳定
  idempotency key 统一失败关闭。跨 Workspace、额外字段、越权 scope、超预算和内部依赖错误均
  映射为稳定公开错误，不泄露内部异常或凭证。
- MCP composition 已从 API 反向依赖中拆出共享 `commercevision-bootstrap` 深模块；API 与 MCP
  共同依赖该组合根，MCP 仅持有窄 Application Ports。Streamable HTTP 使用进程级 lifecycle，
  MySQL/对象存储 readiness 独立探测，Milvus 通道故障继续由 Retrieval degradation 契约表达。
- 最终 Python unit/contract 为 `1179 passed, 1 skipped`；MCP/Tool Runtime 聚焦套件 `54 passed`；
  ProductBrief 真实 MySQL `152 passed`；上传/MinIO/迁移往返/ClamAV 组 `91 passed, 2 skipped`；
  Worker/Milvus 原失败节点在 CI 等价环境下 `7 passed`，最终官方 2.4 适配组合回归 `4 passed`。
  跳过项仅为显式 opt-in 的 Alibaba OSS live 与本机未配置的真实 ClamAV 接缝。
- 依赖锁检查、Ruff format/check、Python 审计、OpenAPI 重导出与 drift、Web generated API types、
  Compose config、`git diff --check` 均通过。最终 MCP 镜像成功构建，Compose 中 MySQL、迁移、
  对象存储和 Milvus 依赖收敛后服务 healthy，`/health/live` 与 `/health/ready` 均返回 200，
  readiness 为 `mysql=ok`、`object_storage=ok`。
- 排查中确认 PyMilvus 2.4 的 `pkg_resources` 兼容由既有受控 Adapter 提供；独立 SDK 探针曾绕过
  该边界并导致错误归因。最终保持官方匹配的 PyMilvus/Milvus `2.4.15` 与安全版
  `setuptools 83`，Python 审计无已知漏洞；短暂尝试的 2.5 组合已完全撤销且不在最终 diff。
- 安全、正确性、性能、可维护性与简化五轴终审未发现剩余阻断项。Ticket 12 只待形成单一提交、
  推送并等待精确 GitHub Actions 全绿；在此之前 Ticket 13 保持未启动。

# 2026-08-04 Ticket 12 发布与 Ticket 13 启动

- Ticket 12 单一提交 `ff9c4b3714ef4d3d65c27637775dde4eddfb0fa8` 已推送，精确 GitHub
  Actions `30832780556` 全绿：Python checks、Web checks、Container builds、Security/Gitleaks
  与 SBOM 均成功；本地 `main` 与 `origin/main` 一致且工作树干净。
- Ticket 13 已按既定工单和 TDD 接缝启动，不重开 Phase 2 规划。已重读 `CONTEXT.md`、
  `ADR-006`、Ticket 13 与规格中的 Retention/Deletion、Durable Events、Observability 和测试决策。
- 初始代码勘探确认可复用边界：上传隔离区已有 Durable cleanup，任务资产验证期已有精确对象清理，
  Rights expiry 已有 `SKIP LOCKED` 扫描，IMAGE/PRODUCT_FUSED 已有 generation-fenced vector delete；
  Ticket 13 的主要缺口是把它们收敛为资产级 tombstone/deletion generation 与完整 payload cleanup。
- 路径探测曾误用不存在的 `docs/adr` 和 `docs/phase-2-implementation-spec.md`；仓库权威路径分别是
  `docs/07-decisions` 与 `.scratch/phase-2-assets-retrieval/spec.md`。两次失败均无文件变更，后续命令
  已固定使用真实路径。

# 2026-08-04 Ticket 13 Retention、删除与一致性对账本地收口

- Asset 聚合新增不可逆 deletion generation、精确 Asset Version fence、删除原因与完成时间；管理员
  删除、Foundation Rights expiry、Task exact deadline 均在一个 MySQL UoW 内先停止可用性，再写入
  Durable Operation、不可变 tombstone 与 typed Outbox command。重复请求复用同一 Operation，旧代次
  或旧 Asset Version 无法完成后续删除。
- 清理协调器以 MySQL 为权威，覆盖全部 Asset Versions/object versions、IMAGE/PRODUCT_FUSED vectors、
  Search Documents 文本、Task ProductBrief fields/evidence、Retained Retrieval Runs、短期 preview token、
  Agent checkpoints、quarantine 与 cache 进度；完成前再次查询晚到事实，发现任何未收敛数据即回滚并
  进入 Durable retry，MySQL 全程保持不可用且不会复活。
- ProductBrief Provider Artifact ledger 保持不可变。`STORED` 行只按冻结的 Version ID + ETag 条件删除；
  `INTENDED/UNKNOWN` 行按精确 key 有界分页，删除每个 object version/delete marker，并要求两次真实稳定
  空扫描。列举后对象已被并发重试删除被视为收敛成功；每行收敛证据与组件进度均为 append-only。
- 深模块审查将 Provider 版本收敛与 Task payload 清理从 MySQL 协调器拆出，协调器只保留代际校验、事务
  栅栏和步骤编排；到期扫描增加 `(retention_class, deletion_operation_id, retention_deadline, id)` 索引，
  使用 MySQL `UTC_TIMESTAMP(6)`、`LIMIT`、确定性顺序与 `SKIP LOCKED`，并暴露独立 scanner health。
- HTTP 新增仅管理员可调用的 Foundation 删除入口和 Workspace 授权的删除状态投影；Web BFF 只放行精确
  路径，工作台轮询持久化组件进度且不显示 bucket/key/version/ETag。管理员并发版本字段已固定为 strict
  integer，拒绝 bool、float 与 string coercion。
- 本地证据：Python unit `1015 passed`（随后新增严格版本/Provider 竞态/schema tests 均独立通过）；
  Foundation 管理删除、Task 全版本清理、Rights expiry 与新 migration 的真实 MySQL/MinIO 聚焦矩阵
  `4 passed`，迁移/schema 复验 `3 passed`。全仓 `pytest` 运行 15 分钟无失败输出后受工具上限终止，
  不计为通过，最终完整矩阵由本票推送后的 GitHub Actions 提供。
- Ruff format/check、Python 依赖审计、OpenAPI 幂等导出、Web generated types、ESLint、TypeScript、BFF
  `22 passed`、Web unit `191 passed`、production build、Phase 0 verification、Compose config 与 Alembic
  schema drift 全部通过。审计新发现的 `brace-expansion <5.0.9` 与 `postcss <=8.5.22` 发布阻断已用最小
  workspace override 升至安全版本，锁文件重建后 `pnpm audit --audit-level=moderate` 无已知漏洞。
- 扫描索引加入同一未提交 revision 后，本地测试库仍是旧版 `e1b7c4d9a263` 物理 schema；MySQL 非事务
  downgrade 在缺失新索引处停止并留下部分 DDL。确认无测试进程且只读核验数据库名精确为
  `commercevision_test` 后，重建该可再生测试库并从首个 migration 完整升级；fresh head、schema drift、
  正式 downgrade/re-upgrade roundtrip 与 Ticket 13 migration contract 均通过（`2 passed`）。
- Standards/Spec 与安全、正确性、性能、可维护性、简化五轴本地终审已完成；Provider 并发缺失竞态、
  稳定空扫描误计数、管理员删除后的二次数据库读取、协调器职责过宽、到期扫描索引和严格版本解析等
  审查发现均已按 TDD 修复，当前无剩余 P0/P1/P2/Required。只待单一提交、推送与远端 CI 全绿。
- Ticket 13 主提交 `2b58bb1bb4a15faee28b7af0467734aca49fdfd1` 的 CI `30844827539` 中 Web、
  Container、Gitleaks/SBOM 全绿，Python 完整矩阵仅有 2 个向后兼容契约失败：旧 migration 全表
  collation 清单漏列 3 张新表；共享 Upload cleanup event 因扩展 Payload 多序列化两个 null 字段。
  最小修复补齐清单并对旧事件 `exclude_none=True`，两个失败节点本地精确复现后 `2 passed`。
