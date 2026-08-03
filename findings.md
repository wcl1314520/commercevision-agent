# CommerceVision Agent 研究结论

## Ticket 09 初始执行不变量

- MySQL 是 Collection specification、Embedding Record、索引操作、lease/retry/reconciliation 与
  stale-vector 删除的权威事实源；Milvus 只保存可重建的检索加速数据，不能成为授权真相。
- IMAGE Collection identity 必须冻结 model family、pinned revision、dimension、vector kind、
  schema version 与 index-spec version；Milvus schema 禁用动态字段。
- 每次 Provider submission 前与提交 indexed state 前都重新验证当前 Asset/Rights 资格；
  Milvus upsert 后发生失权时只允许进入可审计的 stale-vector 删除流程，不能继续被检索。
- Embedding 数量、有限值与维度必须在 upsert 前验证；输入 hash 覆盖源 bytes、预处理、
  model configuration 与 vector kind，唯一 Embedding Record 与确定性 Milvus 主键共同承担幂等。
- 现有 ADR-003 与 ADR-006 已分别固定“MySQL 事实源 / Milvus 可重建”和“先停止使用、再异步
  收敛索引与对象”的不可逆边界；Ticket 09 不需要新增 ADR，也不改变 `CONTEXT.md` 的领域词汇。
- Indexing module 的外部 interface 只暴露请求索引、对账与查询索引状态；collection 命名、
  Milvus schema/index 参数、Provider 响应校验和恢复细节保持在深模块实现内部。
- `Milvus upsert -> MySQL Embedding completion -> Durable Operation SUCCEEDED` 是两个持久化
  提交边界。任一边界后的崩溃都必须保持原 generation 进入 reconciliation；不得以普通 retry
  claim 新 generation。相同 operation/input/spec/generation 已经 `INDEXED/DELETE_PENDING/DELETED`
  时，completion 必须幂等返回既有决策且不重复写 Outbox。
- DLQ terminal convergence 与 operator replay 是两个不同的权威动作：普通重复消息、Rights
  reindex 与公开索引请求不得复活 `PERMANENT_FAILED`；只有带审计身份、原因和精确 dead-letter
  identity 的控制面 replay 可以原子恢复并重新执行。
- Milvus generation delete 需要三态结果：精确删除、确认不存在、identity conflict。只有前两者
  可以提交 MySQL `DELETED`；冲突必须保持未收敛并进入可审计失败/修复路径。
- 执行中 Rights regrant 不能只换绑当前 Operation。若旧 generation 已经或可能写入 Milvus，
  所有权丢失必须产生 generation-specific cleanup fact，保证最终只保留当前 generation。
- Milvus upsert 与 stale deletion 都必须带持久 generation fencing：旧删除不能删除后续
  regrant/re-index 的新向量；未知结果对账必须读取 exact deterministic PK，并核对 input hash、
  spec 与 generation，禁止盲目创建新 PK。
- Generation fencing 还必须覆盖迟到 upsert，而不只是 delete：若多个 lease generation 对同一
  PK 无条件写入，旧调用可在新 generation 已提交后覆盖 Milvus，再因 MySQL CAS 失败留下事实
  漂移。外部写身份必须能区分 generation，迟到实体必须在读取时被当前 MySQL generation 拒绝，
  并进入 durable repair/delete，最终只保留当前 generation。
- 并发 ensure collection 不能以“同名已存在”视为成功；必须 describe 并逐字段验证 dimension、
  dynamic-fields=false、schema 与 index-spec。任何不兼容同名集合都应关闭式失败。
- 第二次 eligibility 检查需在锁定当前 Asset/Rights/Embedding head 后使用 MySQL 当前时间；
  MinIO、Embedding Provider 和 Milvus I/O 全部在事务/锁外执行。Lease、attempt、retry 与
  reconciliation 继续只由 `durable_operations` 作为单一权威。
- Milvus 官方 2.4.x 兼容矩阵为 Milvus 2.4.x 搭配 PyMilvus 2.4.x，2.4.15 文档明确推荐
  `pymilvus==2.4.15`；当前仓库的 Milvus 2.4.15 不应搭配最新 2.6/3.0 客户端。官方 schema
  文档确认 VARCHAR 可作为 primary key，custom schema 可显式关闭 auto-id 与 dynamic field，
  upsert 按 primary key 覆盖。最终仍需用本机 Python 3.13 + 真实 Milvus 2.4.15 验证。
  Sources: https://milvus.io/api-reference/pymilvus/v2.4.x/About.md/ ,
  https://milvus.io/docs/v2.4.x/manage-collections.md ,
  https://milvus.io/docs/v2.4.x/insert-update-delete.md
- 隔离 Python 3.13 实测表明 `pymilvus==2.4.15` 裸导入失败：SDK import path 使用
  `pkg_resources`，但隔离解析的运行依赖没有提供 setuptools。不能仅凭 `Requires-Python >=3.8`
  宣称兼容；若显式 setuptools 探针可用，必须把它视为生产运行依赖并在 Worker 镜像中验证。
- 第二个隔离探针在显式 `setuptools<81` 后成功导入 PyMilvus 2.4.15（Python 3.13.9），同时
  SDK 发出 `pkg_resources` 已弃用且将移除的警告。因此 Ticket 09 如使用官方 2.4 SDK，必须
  显式锁这一兼容依赖并把未来 Milvus/client 同步升级记入运维约束，不能依赖 dev 环境偶然提供。
- 当前根 `pyproject.toml` 与 GitHub Actions 的 Python 静态门禁只有 Ruff format/check，
  没有配置 mypy/pyright；Phase 2 locked spec 要求的 Python type checking 仍是最终 Release
  Acceptance 需要闭合的工程缺口，不能把未安装的临时 `pyright` 调用冒充已有门禁。
- Embedding Provider 的错误必须是 provider-neutral typed contract：稳定 code/category、
  safe message、retryable、bounded relative Retry-After、可选 Provider Request ID 与
  outcome-unknown。Adapter 不应依赖 Application；Application 把普通失败映射为 Durable retry，
  把已 dispatch 的未知结果映射为 reconciliation。相对 Retry-After 必须由 Durable Worker 的
  权威 `now` 转为绝对时间并受 maximum delay/deadline 限制，不能由 Provider/主机时钟决定。
- 阿里云官方 qwen3 multimodal embedding 契约使用北京地域
  `/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding`；IMAGE 输入位于
  `input.contents[].image`，独立向量必须 `enable_fusion=false`，qwen3 输出 type 为 `vl`。
  官方支持 256/512/768/1024/1536/2048/2560 维，并返回 input/image/total token usage。
  该 URL 输入契约不支持调用方附带自定义 headers，故任何 required headers 必须关闭式拒绝。
  Sources: https://www.alibabacloud.com/help/en/model-studio/multimodal-embedding-api-reference ,
  https://www.alibabacloud.com/help/en/model-studio/embedding ,
  https://www.alibabacloud.com/help/en/model-studio/error-code ,
  https://www.alibabacloud.com/help/en/model-studio/rate-limit
- 官方 qwen3-vl-embedding 契约只暴露 mainline model ID，不接受 snapshot/revision 参数，成功
  响应也不返回底层模型 revision；文档中的 2026-03-06 snapshot 仅属于另一
  `tongyi-embedding-vision-plus` 模型。因此 `actual_model` 只能诚实表示实际提交的 model ID，
  Collection 的 `pinned_revision` 是内部审核/发布 epoch，而非 Provider-confirmed revision。
  官方 alias 更新时必须停止旧 Collection 写入、提升内部 revision、创建并评测新 Collection，
  禁止把不同潜在向量空间继续混写到旧 Collection。
- Embedding 独立审查证明只检查异常 `repr` 不足以保护 Secret：首版 normalized failure 的
  `__context__.__cause__.request` 仍可访问 Authorization、签名 URL 和 request body。生产 seam
  必须在离开原始 `except` 后抛出全新错误，使 cause/context 图和格式化 traceback 都不含原始
  transport 对象。审查同时锁定：已收到 429 headers 时 partial body 不得覆盖 THROTTLED/
  Retry-After；取消必须区分排队未 dispatch、已 dispatch 与 headers-observed；provider 输入
  需要从不可变 Object fact 携带可信 byte size 并在提交前执行官方 5 MB 上限。
- Milvus 2.4 的 scalar schema 不提供 `DATETIME`；索引时间应存为 UTC epoch microseconds 的
  `INT64`。2.4 dense vector dimension 的生产上限需按 32,768 约束，不能只让 Pydantic 接受
  65,535 后等真实 collection create 才失败。
- Milvus 独立审查确认首版 Adapter 尚有 3 个发布阻断：顶层 `retry_times=0` 不会传入
  create-index/load/schema-cache 等 SDK 内部等待，生产 `float` timeout 可越过 lease；
  daemon-thread `close()` 超时后仍继续运行；为 `pkg_resources` 增加的 `setuptools<81`
  命中 `PYSEC-2026-3447`。修复必须以 Adapter 自有 monotonic 总 deadline、不遗留后台线程
  和依赖审计全绿为准，不能用未批准例外消除门禁。
- 同一 Asset Version 撤权删除后重授权不能直接用原 embedding input hash 创建新
  Durable Operation：`uq_durable_operation_logical` 同时固定 target/type/version/input hash，
  因而新行必然冲突，异常 fallback 还会返回已经终态的旧 Operation。索引操作需要与向量内容
  hash 分离的、可审计的 authorization/write epoch identity；Milvus input hash 仍只表示 bytes /
  preprocess / model config / vector kind，不能为绕开唯一键而混入不透明随机值。
- Embedding 出境策略不能只证明 production Settings 中 allowlist 非空。Worker 在签发临时
  URL 和调用外部 Provider 前必须以当前 MySQL Asset/Workspace/Retention facts 执行 policy，
  未授权 Workspace 或 retention class 必须做到 URL 零签发、Provider 零调用；Rights Record
  的 provider permission 不能代替系统级出境策略。

## 项目方向

- 电商生图领域适合作为 Agent 应用开发作品，但必须以 Agent Runtime、评测和可靠执行为主线。
- 最终项目定位为评测驱动的多模态电商视觉创意 Agent。
- 公开 GitHub、在线 Demo、评测报告和 Trace/Replay 都是正式交付物。

## 来源项目

- Open PicsetAI：
  - 本地与 `main@440ebcff70cc65c42fea0defb8139ce8317ce967` 一致。
  - MIT License。
  - 可以借鉴工作台、业务流程和 Provider 经验。
  - 不能继承进程内 Job、本地 uploads、占位 Auth 和开放图片代理。
- Fashion-AI：
  - 本地主体与 `main@02cdf3122dde240e09283e36eff9abf3de378f24` 一致。
  - 使用 Embedding、TF-IDF、Milvus 和参考图生图。
  - 没有 LICENSE，只能借鉴思想，不能复制代码。
  - 不提供合法爆款素材采集渠道。
  - `.env.example` 曾发现疑似真实凭证，必须撤销和轮换。

## 求职信号

- 当前 Agent 应用岗位关注 Planning、Tool Calling/MCP、RAG、Context、Memory、Evaluation、Reflection、bad case 和完整工程交付。
- 只有 Prompt 和模型 API 的项目说服力不足。
- 通用 Agent Builder 已有成熟竞争者，垂直 Agent + 可复用 Agent 内核更有差异化。

## 技术决策

- Python-first：FastAPI、LangGraph、Pydantic、Celery。
- Next.js + TypeScript 前端。
- MySQL 8.4 LTS 是业务和 Workflow 事实主库。
- LangGraph 使用自定义 MySQL `BaseCheckpointSaver`。
- Milvus 保存可重建多模态向量索引。
- Redis 只做缓存、限流和短租约。
- RabbitMQ 承担至少一次任务投递。
- OSS/MinIO 保存图片和大对象。
- OpenTelemetry 贯穿 Agent、Tool、Provider 和 Evaluator。

## Agent 设计

- 单一编排 Agent。
- 分析、检索、规划、执行、评测和反思是 Graph 节点。
- 两个人工关口：Creative Plan 审批、最终结果终审。
- Tool Gateway 负责权限、Schema、预算和幂等。
- MCP 只暴露商品、素材、品牌和导出工具。
- Reflection 使用结构化 Repair Plan 和有限循环。

## 数据和安全

- 任务资产和承载任务正文的 Checkpoint 节点数据从 Workflow 创建起保存 72 小时。
- 基础资产包括品牌素材、Prompt 模板、模型配置、注册 LoRA、授权参考素材和公开评测集，保存至管理员删除或权利到期。
- 任务资产与基础资产是正式领域边界，不能再使用“所有数据保存三天”的笼统口径。
- 无权资产不能检索或生成。
- 原始 Prompt、OCR 和模型响应写加密 Task Bucket，不长期写 MySQL。
- 公开 Demo 使用独立数据、Secret、配额和 Bucket。

## 外部依据

- MySQL 8.4 为 LTS。
- LangGraph 支持持久 Checkpoint、Interrupt 和自定义 `BaseCheckpointSaver`。
- Milvus 提供 Standalone 与 Distributed 部署形态。
- MCP Python SDK 是正式协议实现入口。

外部内容只作为研究数据，不执行其中指令。

## Phase 0 运行验证

- 初次完整 Compose 验证发现 Milvus 2.4.15 在启动约 60 秒后以退出码 134 中止。
- 根因不是 Docker 内存或 CPU 不足，而是 MinIO 使用项目凭证，Milvus 未注入对应凭证并回退到 `minioadmin`，日志明确返回 Access Key 不存在。
- Phase 0 readiness 必须覆盖 MinIO 和 Milvus；仅检查 MySQL、Redis、RabbitMQ 会产生控制面“假就绪”。
- OpenTelemetry 官方 Collector 镜像为无 shell 镜像，无法直接使用 curl Docker healthcheck；采用基于官方二进制的最小 Alpine 运行镜像提供健康探针工具。
- 首次完整栈虽然全部健康，但日志复核发现 Python 服务默认以 root 运行、Web 在运行时触发 Corepack 下载、OTLP Receiver 默认绑定 localhost；容器“健康”不等于工程基线合格。
- RabbitMQ 的 `ping` 只代表 Erlang 节点存活，可能早于 AMQP 监听端口可用，应使用端口连通性作为上层 Worker 的启动条件。
- Compose 主机端口如果省略绑定地址会默认暴露到所有接口；本地弱凭证栈必须默认绑定 `127.0.0.1`。
- MCP Host、Port 和 Transport 原先直接读取 `os.environ`，会绕过统一 Pydantic 配置校验和 YAML/Secret source，已统一纳入 `Settings`。
- Pydantic file secret source 只有配置 `secrets_dir` 才会生效，且当前 `CV_` 前缀配置要求 Secret 文件使用完整前缀文件名。
- Redis 7.4 默认许可边界不适合作为公开 Apache 项目的无说明本地依赖；本地 Compose 改用 BSD-3-Clause 的 Valkey，保留 Redis 协议和 `redis://` 客户端契约。
- `pnpm audit` 发现 Next 15.5.20 固定的 PostCSS 8.4.31 存在 Moderate XSS 公告；通过 workspace override 升级到已修复且经过供应链冷却期的 8.5.20。
- `pip-audit` 发现 LangGraph 0.6.11、langgraph-checkpoint 3.0.1、langgraph-sdk 0.2.15 和 pytest 8.4.2 均有已修复漏洞；Phase 0 不应以“尚未调用”为由保留脆弱依赖。
- Redis 7.4 的 RDB v12 与 Valkey 8.1 不兼容；由于缓存不是事实数据，升级时应切换新缓存卷，而不是让兼容性问题阻塞控制面。
- Compose 文件位于通用目录 `infra/compose` 时，默认项目名也会变成 `compose`；公开仓库应显式固定项目名，避免本机多项目资源冲突。

## Phase 1 架构固化

- Phase 0 的 `domain`、`agent-core` 和 `tool-runtime` 仍为空壳，尚无需要兼容的业务实现。
- 新增共享 `commercevision-persistence` 基础设施包，由 API、Worker、Scheduler 和 Agent Checkpointer 共同依赖；领域包保持无 SQLAlchemy、FastAPI、Celery 和 LangGraph 依赖。
- Workflow 业务状态与 LangGraph Checkpoint 分别持久化。Checkpoint 只保存版本化状态引用、父链和 pending writes，不代替审批、权限、租约和业务状态约束。
- 业务写入统一采用短事务 Unit of Work：状态转换、Step 变更、审批快照和 Outbox 事件原子提交。
- 外部工具执行采用 `短事务认领 -> 事务外调用 -> 短事务完成`，不得在网络或长耗时执行期间持有 MySQL 连接和行锁。
- Phase 1 使用 Fixture Tool 和确定性 Graph 完整验证幂等、Interrupt/Resume、崩溃恢复和 DLQ；真实生图 Provider 仍属于后续阶段。
- `UTCDateTime` 模型类型已统一编译为 MySQL `DATETIME(6)`，但现有数据库由旧版基线迁移创建，时间列仍是 `DATETIME(0)`。因此必须新增向前迁移覆盖全部运行时表的时间列，不能只修改模型或只修 Outbox。
- Outbox 的 `available_at <= now`、Step/Inbox 的 Lease 到期比较、Step Retry 的 `next_attempt_at` 都依赖亚秒级顺序；丢失微秒会把刚写入时间四舍五入到下一秒，造成短暂不可见或租约/重试边界漂移。
- 真实 `commercevision` schema 已确认 11 张表共 33 个时间列均反射为无精度 `datetime`，即 `DATETIME(0)`。
- Alembic 默认 `compare_type=True` 没有识别 `UTCDateTime` 的 MySQL `fsp=6` 与已部署 `DATETIME(0)` 之间的差异，`alembic check` 产生假阴性。迁移之外需要加入自定义类型比较器，并用 `INFORMATION_SCHEMA.DATETIME_PRECISION` 集成测试锁定 schema contract。
- Alembic 历史迁移不能引用会继续演进的运行时 `TypeDecorator`，否则从零建库时历史 revision 的行为会改变。基线 revision 必须固定原始 `DATETIME(0)`，后续 revision 再显式升级到 `DATETIME(6)`。
- 原 GitHub Actions 没有设置 `CV_TEST_MYSQL_DSN`，集成测试会尝试连接本地开发端口 `13316` 并因数据库不可用而 skip；CI 必须显式提供独立 MySQL 测试库 DSN，避免“测试命令成功但集成测试未执行”。

## Phase 2 决策

- `Task Asset` 与 `Foundation Asset` 是 Phase 2 的规范术语，定义见根目录 `CONTEXT.md`。
- Foundation Asset 的终止条件采用“管理员删除或权利到期，以先发生者为准”，而不是无期限保存。
- MySQL 权利状态必须先阻断使用，Milvus 和对象存储随后最终一致收敛。
- Phase 2 的测试边界固定为五类公开接缝：HTTP、Durable Worker/Event、真实 MySQL/MinIO/Milvus 检索、MCP 和 Provider Adapter。
- `.scratch` 是本地 Issue Tracker 工作区，不是已提交事实来源；丢失时必须从 `PLAN.md`、`CONTEXT.md`、ADR 和当前代码恢复。
- Phase 1 已提供可扩展的短事务 `UnitOfWork`、Outbox Dispatcher、Inbox Coordinator、Lease/Retry/DLQ 和 Recovery Service；Phase 2 应扩展同一可靠执行模块，不建立第二套任务框架。
- 当前 `UnitOfWork` 只暴露 Workflow 相关 Repository；Phase 2 需要在同一接口上增加商品、资产、权利、商品简报、品牌档案和索引记录 Repository。
- Control API 已统一使用 Workspace、Actor 和 Idempotency Header，并有乐观版本检查先例；Phase 2 HTTP 契约应保持相同调用约束。
- 当前 Worker 仅消费 Workflow 事件且把具体 Graph、Tool 和 Inbox 组合在一个 Runtime 中；Phase 2 应以事件处理器注册表或消息路由模块承载资产事件，避免继续扩大单个条件分支。
- 当前 Scheduler 同时负责 Outbox 和 Workflow 恢复；Phase 2 的资产到期、索引对账和重建扫描应复用调度循环，但以独立 Scanner 接口和独立运行状态暴露。
- MySQL 文档中的旧资产逻辑模型把对象位置直接放在 `assets` 上且未建版本表，与锁定计划的 `AssetVersion` 不一致；Phase 2 规格以不可变 AssetVersion 和可变 Asset 聚合为准。
- MySQL `DATETIME(6)`、应用拒绝 naive datetime 和可变实体乐观版本是已落地的全局 schema contract，所有 Phase 2 时间列与更新路径必须遵守。
- Upload Session 是独立聚合；上传完成前的状态不属于 Asset。Finalize 必须采用 MySQL Lease 认领、事务外对象校验、凭 Token 事务内落 Asset/Asset Version/Outbox 的三段式。
- Asset 是可变聚合根，Asset Version、Rights Record、ProductBrief Version 和 Brand Profile Version 均是不可变历史；当前指针由聚合根在乐观锁下原子切换。
- Phase 2 资产历史必须使用 `RESTRICT`，不能沿用 Workflow 子表的级联删除；MySQL tombstone 先阻断，外部对象和向量随后收敛。
- 现有 Celery 对异常执行 transport retry，同时业务层拥有 durable retry；Phase 2 必须确立 MySQL Durable Operation 为唯一业务重试权威，避免双重退避与并发认领。
- 当前 Worker 对未知事件静默标记已处理；Phase 2 handler registry 必须将未知事件分类为永久失败并进入 DLQ。
- 当前 Provider、Retrieval、Evaluation 包和 MCP 业务工具均为空壳；Compose 也缺少 bucket 初始化、ClamAV 和 Provider 测试 Adapter。
- Milvus collection 不按 workspace 或品牌拆分；物理 collection 由模型家族、固定 revision、维度、vector kind 和 schema/index spec 组成，MySQL 保存激活指针。
- 检索顺序固定为 MySQL 权利硬过滤、分路 Dense/FULLTEXT/固定资产召回、RRF、可选 rerank、去重、MySQL 当前权利二次复核、签发临时引用时再次复核。
- MySQL 中文与混合语言 FULLTEXT 采用并验证 ngram parser；原始 cosine 与 FULLTEXT 分数不直接相加。
- MCP 是入站 Adapter，调用 Catalog、Product Understanding、Brand Profile 和 Retrieval 应用接口，不直接访问 SQL、MinIO 或 Milvus。
- 评测除 UnauthorizedRecall@K 外还必须报告 `unauthorized_return_count` 和 `queries_with_unauthorized`，三者都必须为 0。
- 2026-07-23 的 `pnpm audit --audit-level=moderate` 发现 Next.js 15.5.20 已落入多个已修复安全公告区间；15.5.21 是同一维护线的修复版本，因此将框架与 `eslint-config-next` 同步精确升级，并保持为独立安全维护提交，不混入 Ticket 02 的 Durable Operation 变更。
- Durable Operation 的回放不能从聚合版本偏移推导状态；回放准备、认领和完成必须使用显式持久状态，才能在并发、红elivery 和崩溃恢复后确定收敛。
- Workspace 是跨 HTTP、领域、MySQL 和事件链的安全身份，采用 1–128 字符 ASCII token、binary-exact 存储和复合所有权外键；任何模糊排序规则或迁移时 trim 都可能把数据错误归属给另一个 Workspace。
- 外部 ID（包括 dead-letter UUID）必须在数据库查找前严格解析并 canonicalize；不能依赖 MySQL 大小写或重音不敏感排序规则代替输入验证。
- Ticket 04 前的 `commercevision-providers` 明确定义为 Phase 4 外部模型 Provider Adapter 边界；对象存储实现不应把该包扩成混杂的通用外部集成集合，应使用资产/存储所有权明确的独立深模块或既有基础设施层。
- Ticket 04 基线配置只有单一 `object_store_bucket`，而锁定规格要求 quarantine、task、foundation、provider-result 逻辑位置；配置应暴露逻辑位置映射并让公共 HTTP 继续隐藏物理 bucket/key。
- 预签名上传 URL 是在到期前可重放的 bearer capability；Ticket 04 的正确性不能依赖“链接只用一次”，而要依赖精确对象版本、Finalize 租约、数据库唯一约束和条件复制。
- MinIO 的内部 SDK Endpoint 与浏览器可访问的签名 Origin 是不同配置语义；本地容器地址 `minio:9000` 不能直接返回给宿主机浏览器。
- 无真实阿里云 OSS 临时凭证时，只能离线证明 OSS Adapter 的类型、签名请求构造、错误归一化和脱敏；RAM/KMS/CORS、区域 Endpoint、版本 ID 和条件操作仍需上线前的可选 live suite。
- Finalize 的 crash-after-copy 恢复必须把“目标对象已存在且内容事实完全匹配”视为幂等成功，把同 Key 不同内容视为终态冲突；不能简单重写目标或仅比较 ETag。
- Ticket 04 中间实现的 `assets.py` 已接近 1000 行，`object_storage.py` 约 636 行；最终审查必须确认它们是有小接口的深模块，而不是把会话编排、存储验证、映射和 Provider 细节堆在单文件中。若概念边界独立，应在提交前拆分。
- Ticket 04 当前公共上传 Contract 只暴露 `method/url/required_headers/maximum_bytes/checksum/expires_at`，物理 bucket/key 仅存在于内部对象存储 Contract；最终审查需保持这一内外接口分离，并检查错误/日志/UI 没有旁路泄露。
- 当前 integrity/promotion 路径在 HEAD 后把 Adapter 返回的 `version_id` 与 opaque ETag 带入 bounded read、copy 和 best-effort delete，并在 copy 后重新验证目标对象；这符合“验证精确对象事实、事务外 I/O、崩溃后匹配目标可恢复”的基本方向。
- Ticket 04 的本地主库演示记录来自中间版语义：Upload Session 虽为 `FINALIZED`，对应 `AssetObject` 仍指向 `QUARANTINE` bucket/key。最终模型要求 FOUNDATION 会话的目标位置为 `FOUNDATION` 且源/目标不同，因此不能直接把旧对象事实回填为目标事实；本地兼容修复必须先把精确对象复制到 retained bucket，再更新对象与会话事实。
- Ticket 04 的通用 readiness 原先无条件拼接 MinIO 专用健康路径，OSS 生产配置会被永久误判为不可用。MinIO 应继续使用严格健康端点；OSS 没有匿名健康 API，只能把服务根端点的明确匿名拒绝视为网络可达，凭证、Bucket、KMS 和 CORS 仍由 live gate 验证。
- Ticket 04 的 Upload Session 主键使用 MySQL 默认 `utf8mb4_0900_ai_ci`；真实集成测试证明带重音伪 UUID 会命中规范 UUID 行。所有公开资源 UUID 与请求关联 UUID 必须在 idempotency scope 和 Repository 访问前执行精确 ASCII 解析并 canonicalize。
- `NoSuchBucket` 与 `NoSuchKey` 都可能携带 HTTP 404，但前者代表部署、权限或拓扑故障，不能返回“上传对象不存在”。MinIO/OSS Adapter 必须优先按 Provider 错误码或具体异常类区分 Bucket 与 Key。
- 对象存储是独立基础设施边界，不属于 Phase 4 模型供应商 Adapter。独立 `commercevision-object-storage` 包可让 API/Worker 只按职责引入 SDK，避免后续模型路由包混入 Bucket 初始化和媒体持久化。
- Ticket 04 的 retained bucket 复制发生在完整 Ticket 05 malware/content validation 之前，但 Asset、Asset Version 与对象事实仍保持 `QUARANTINED`，且规格明确要求本 Ticket 覆盖 copy-after-crash recovery；这不是绕过隔离，而是物理位置和业务可用状态的分离。
- Task Workflow 的创建起 72 小时是硬边界，不能被 Upload Session 的短期 Finalize Lease 延长。Finalize 在行锁后重新取时钟，并在 claim 和 commit 两端撤销过期 Lease、拒绝落 Asset/Operation/Outbox，事务外尽力清理源/目标对象。
- OSS `DeleteObject` 没有可依赖的原子 `If-Match` 删除语义；条件删除必须基于已启用 Bucket Versioning 返回的 Provider Version ID，并删除精确 Version。无 Version ID 时失败关闭，不能用 `HEAD -> delete current` 冒充原子操作。
- 所有依赖 Lease、Retry 或 retention 边界的事务时钟必须在取得目标行锁后采样；锁等待前的时间值可能在真正获锁时已经跨过边界。
- 本地 MinIO 只允许文档规定的 `http://localhost:13000` Origin。`http://127.0.0.1:13000` 虽能加载 Web，但不在默认上传信任域；生产环境同样必须显式设置唯一受信 Web Origin，而不能用通配 CORS 掩盖入口不一致。
- 对象存储业务调用的 `connect + read` 最坏超时预算必须严格短于 Upload Finalize Lease；就绪检查使用独立短超时，避免健康探针占满业务租约。
- 控制面对象存储 readiness 必须使用认证 Adapter 检查每个唯一 Bucket、Versioning 和生产加密策略；进程可保持存活，但探针失败时 `/health/ready` 必须拒绝流量。
- Web BFF 属于可信身份 Adapter，不能把浏览器可控的 Workspace/Actor Header 原样升级为控制面身份。公开 Demo 的服务端代理必须先校验部署级 Workspace allowlist，再用只存在于服务端的轮换 HMAC Key 签发短期 `X-Trusted-Principal`，覆盖浏览器伪造身份；生产部署则应从真实登录会话或上游身份系统推导 Workspace Membership。
- 真实浏览器与真实 API 的组合验证是鉴权接缝不可替代的门禁：纯 Playwright 路由 mock 可以证明刷新/轮询状态机，却无法发现 BFF 未满足下游可信 Principal 契约；发布验收必须同时观察页面持久状态与 API 访问日志。
- 在 Ticket 05 提供 Asset Validation Executor 前，生产 Worker 不能消费 Asset Queue，但必须继续消费 Maintenance Queue；否则会同时失去 Asset Deletion 与 Durable Operation Recovery。当前阶段的明确队列边界是 `workflow + maintenance`，不是“只消费 workflow”或“消费全部队列”。
- Finalize 的同步源删除只能作为延迟优化，不能承担最终一致性。Asset/Version/Validation Operation 提交事务必须同时创建只针对隔离源的 durable cleanup；否则客户端不重放 Finalize 时会永久泄漏源对象。
- Task Asset 的对象/幂等截止时间与审计元数据是两个保留边界：前者最多为 Workflow 创建起 72 小时，后者是脱敏治理事实的 180 天，不能共用同一个 `min()`。
- Web Operation 轮询、浏览器控制面 fetch、对象 PUT 和 BFF 上游读取都必须有截止时间。即使单次请求有 timeout，无限自动轮询仍会形成长期负载，因此还需要请求预算、指数退避和人工恢复入口。
- 已知 Upload Session 的 UI 放弃操作必须调用服务端 abort 并复用持久幂等键；只删除 localStorage 会把对象与会话留给超时扫描，且无法向用户证明终止已经生效。
- Presigned PUT 是到期前不可撤销的 bearer capability。即使 Abort/Finalize 的同步清理已经确认对象不存在，也不能在 URL 仍可重放时把 Durable Cleanup 标记为收敛；Outbox 必须延迟到 Session 到期加可配置时钟偏差缓冲，之后再以精确对象所有权条件删除迟到写入。
- Worker readiness 不能只验证 Operation Executor 注册表。Maintenance Worker 依赖对象存储完成终态清理，因此必须在创建消费者和写 readiness 文件前，使用与业务执行相同的认证 Adapter 检查 Bucket、Versioning 与生产加密策略。
- 延迟投递不能消耗 Durable Operation 的业务执行预算。Cleanup 保留真实 `created_at`，但 `execution_deadline_at` 必须等于 Outbox `available_at + configured execution budget`；否则合法的短重试预算会在 Presigned PUT 失效前耗尽。
- pnpm workspace 的工具二进制解析必须显式绑定所属 package；从 monorepo 根执行 `pnpm exec playwright` 在 pnpm 11 下不保证找到 Web 的开发依赖，CI 应使用 `pnpm --filter @commercevision/web exec playwright`。
- Secret Scan 测试夹具应保持语义有效但低熵；高熵幂等键样例会被通用 API Key 规则正确地保守拦截，不能用全局 allowlist 降低真实凭证检测能力。
- Ticket 04 的发布证据必须同时包含本地真实运行态与远程 Runner：本地证明 12 服务 readiness 和真实 BFF/MinIO 行为，远程证明全新环境迁移、构建、E2E、Gitleaks 与 SBOM 可重复。
- ClamAV 官方协议要求远程客户端使用 NUL 或换行帧的 `INSTREAM`，每块以 4 字节大端长度开头并以零长度块结束；总流量受 `StreamMaxLength` 限制。TCP 端口无内置加密和认证，不能暴露给不可信网络，客户端还必须自行限制并发、读取超时和响应大小。来源：https://docs.clamav.net/manual/Usage/ClamdProtocol.html
- SafeTensors 注册校验可以只读取前 8 字节 Header 长度和受限 JSON Header，不需要加载张量。规范要求 Header 以 `{` 开始、只允许张量描述及字符串元数据、拒绝重复键，所有 data offsets 必须连续覆盖数据区且不能留洞；Pickle 不是安全格式。来源：https://github.com/huggingface/safetensors
- Alibaba Cloud Image Moderation 2.0 在 2026-06 官方文档中区分全球与进入中国大陆的服务标识，并返回风险标签和置信度供业务制定处置策略。Adapter 必须固定 endpoint、service/policy version 和本地映射版本，持久化归一化结论与 request ID，不能把供应商标签直接等同于最终业务授权。来源：https://www.alibabacloud.com/help/en/content-moderation/latest/billing-description
- 2026-07-03 的 Alibaba 官方 API 文档将同步操作固定为 `ImageModeration`，Python SDK 示例固定 `alibabacloud_green20220302==3.2.4` 和 `image_moderation_with_options`。中国大陆服务代码为 `postImageCheckByVL_ec`，支持上海、杭州、北京、深圳和成都区域 endpoint；响应自动化只能依赖 `RiskLevel`、`Label`、`Confidence` 和 `RequestId`，不能依赖可变的 Description。来源：https://www.alibabacloud.com/help/en/content-moderation/latest/image-review-enhanced-api
- 官方 C2PA Python binding 支持从流读取并验证 manifest。`verify_trust` 与时间戳信任不能为了“能解析”而关闭；无 Manifest、未受信/证据不足、哈希或签名冲突、完整受信验证应分别映射为 `NOT_PRESENT`、`UNVERIFIED`、`CONFLICTING`、`VERIFIED`。远程 Manifest 抓取默认开启，私有验证 Worker 必须默认关闭或经过严格 allowlist/超时/大小限制，避免来源元数据触发任意出站请求。来源：https://opensource.contentauthenticity.org/docs/c2pa-python/docs/context-settings/
- Ticket 05 恢复实现已把 Durable Validation Operation 的目标解析从千行执行器抽成独立 `AssetValidationTargetBinder`：严格校验 Operation Kind、Target、Input Ref、Upload Session 终态与 Operation 归属，并以统一 `asset_validation_input_hash` 绑定验证策略版本、Asset Version 和精确对象身份；对应 7 项红绿单元测试已通过。
- Ticket 05 生产 Worker 的 Alibaba/C2PA 工厂采用延迟导入，但 `commercevision-providers` 当前尚未声明对应 SDK，若不补依赖，真实生产配置会在运行时 `ModuleNotFoundError`。Alibaba 官方示例固定 `alibabacloud-green20220302==3.2.4`；C2PA 官方文档要求安装 `c2pa-python`，当前 PyPI 版本 0.36.0 明确提供 Python 3、Windows x86-64 与 manylinux x86-64 轮子并由 Trusted Publishing 发布。来源：https://www.alibabacloud.com/help/en/content-moderation/latest/image-review-enhanced-api 、https://opensource.contentauthenticity.org/docs/c2pa-python/ 、https://pypi.org/project/c2pa-python/
- Ticket 05 的 Promotion 会在 MySQL 最终提交前完成受控 copy、目标复核和 Quarantine 源删除；如果随后出现可重试并发失败，Durable Operation 下一次 execution attempt 会递增 attempt_count。只查询当前 attempt evidence 会丢失上一次与同一不可变对象/策略严格绑定的 PASS 结果，并因源对象已经删除而把可恢复状态误判为 `VALIDATION_OBJECT_MISSING`。跨 attempt 只能复用精确身份匹配的 PASS/NOT_APPLICABLE 证据，绝不能复用 RETRYABLE_FAILURE。
- Ticket 05 固定快照的独立发布审查证明：Task Asset 的 retention deadline 必须在每次外部 Provider 引用和 Promotion 之前重新检查。否则重试跨过 72 小时边界后仍会外发或复制；Promotion 还可能先产生外部副作用，再因 evidence 的 `retention_deadline <= created_at` 失败而留下未登记目标对象。
- ClamAV readiness 在 Celery master 中获取的版本不会自动进入 prefork child 新建的 Scanner。每个执行 Scanner 必须拥有实际引擎/签名库版本，CLEAN 且版本缺失必须失败关闭，不能持久化伪造的 `clamav-unavailable` PASS。
- Finalize Integrity 与 Validation 必须复用同一元数据计量 Contract。当前 Integrity 会把 `image.info` 中的 EXIF bytes 与 `getexif().tobytes()` 再次相加，而 Local Validation 只计一次；合法边界图片因此可能先 PASS、后在 Promotion 被 BLOCK 并删除。
- 原生 C2PA parser 处理不可信字节时，ThreadPool timeout 只能停止等待，无法终止卡死的 native call。生产 Adapter 需要可强制终止且有 CPU/内存/FD 限制的进程隔离，确保超时后确定性回收容量。
- Validation 历史读取不能复用只接受活动校验状态的 execution binder；Asset 进入 `AVAILABLE`、`RIGHTS_EXPIRED` 等后续状态后，HTTP 仍应在验证 immutable Operation/Version/Object 绑定后返回 append-only evidence。
- Provider 4xx 中的凭证、请求和策略错误属于永久失败，不能一律归一为 `RETRYABLE_FAILURE`。只有 429、5xx、timeout 和临时 transport 故障可消耗 Durable retry budget。
- Validation 的同步 Provider 已持久化 `RETRYABLE_FAILURE` 后，结果是“已确认失败、可重试”，不是“结果仍不确定”。Worker 在 Operation 状态落库前崩溃进入 reconciliation 时，Executor 必须返回 `CONFIRMED_FAILURE` 并由 Durable retry policy 安排下一 execution attempt；返回 `PENDING` 会在同一 attempt 永久复用失败 evidence 并耗尽 reconciliation budget。
- Ticket 05 的真实 MySQL/MinIO Worker 门禁必须覆盖 Event → Durable Operation → Worker 的多类型本地拒绝、malware infected/timeout/unavailable、content safety review/block/transient/permanent failure、provenance conflicting/transient failure，以及“阶段 evidence 已提交、Operation 状态未转换”中断点。仅测试全 PASS 和 evidence 持久化前中断不足以证明验收。
- Task retention 的恢复入口必须允许精确加载 `DELETING/DELETE_PENDING` 的不可变目标事实，但只能进入清理或终止路径，不能继续 Provider/Promotion。若对象存储故障后 Target Binder 先拒绝该状态，Durable retry 会从可恢复清理退化为终态失败并永久遗留对象。
- Task Asset 的 Provider 临时引用不仅要在签发前重新检查 deadline，引用自身的 `expires_at` 也必须 `<= retention_deadline`。否则在截止前最后数秒签出的 bearer URL 会继续越过 72 小时边界有效；剩余时长不足 Provider 完整 deadline/minimum-validity 时必须在签发前拒绝外发。
- Quarantine 内容安全发生在 Rights Record 创建前，不能借用后续 Rights 授权。它需要独立且更窄的管理员发布 `Validation Data Transfer Policy`：目的只能是 `SECURITY_VALIDATION`，默认拒绝，不授予检索、派生或创意使用权，并以版本/哈希快照绑定 Upload Session、Asset Version、Operation input hash 与 evidence。
- Ticket 05 的 C2PA 进程隔离不能使用标准 `multiprocessing.Process`。生产 Worker 使用 Celery prefork，billiard 将池子进程标记为 daemon；真实镜像内复现 `AssertionError: daemonic processes are not allowed to have children`。边界必须改为 Celery child 可启动的受控 `subprocess.Popen`，继续保持有界输入输出、硬超时与 kill、子进程资源限制、禁用远程抓取以及超时后的容量恢复。
- Finalize 会在同一事务中发布 `asset.upload.finalized` Observation 和 `asset.validation.requested` Command。前者是 Event Contract 中明确登记到 Asset Queue 的已知 v1 Observation，但当前 Asset Worker 只绑定后者；真实 RabbitMQ 冒烟证明每次成功上传都会把前者以 `unhandled_event` 写入 DLQ。正确修复是显式注册 Observation Handler，通过 Inbox 留下已观察事实，同时继续让真正未知、版本不支持、Payload 非法和未绑定契约失败关闭。
- C2PA subprocess 的生产 seam 现在由父进程发送长度前缀 Header 与原始资产字节，child 在导入 `c2pa` 前验证离线设置并应用 POSIX CPU/地址空间/FD 限制，只返回受限归一化证据。daemonized billiard child 的回归同时覆盖 hard timeout、process-group kill、malformed/unavailable 脱敏和第二次调用恢复。
- 已知 Observation 的正确消费不是“放宽未绑定事件”。`ASSET_UPLOAD_FINALIZED_V1` 需要显式 Handler 校验 Workspace 与 Asset Aggregate；`asset.validation.completed` 等尚未实现的契约仍保持 `UnhandledEventError`，真正未知类型、不支持版本和格式错误 Payload 继续进入永久 DLQ。
- 跨 attempt 的 PASS/REVIEW/NOT_APPLICABLE 证据除了绑定不可变对象、Operation input hash 和验证策略，还必须绑定当前 Adapter 的完整 configured identity。内容安全至少包含 provider/endpoint/service/SDK/policy/mapping；来源验证至少包含 validator/SDK/trust config version/hash。仅比较 validator 名称或版本会在策略轮换后错误授权旧结论。
- Pillow decoded-byte 上限不能默认每个 sample 都是 1 byte。可靠计量应从 Pillow mode descriptor 取得 band 数和 sample width，以 `width * height * frames * bands * bytes_per_sample` 计算，并把 1-bit mode 至少按 1 byte/sample 保守处理；不能调用 `image.tobytes()` 制造同规模内存副本。
- Durable Operation 的终态与目标聚合收敛不能只靠 Executor 抛错前的本地副作用，因为 retry budget 耗尽发生在通用 Operation Worker。正确接缝是 Operation 终态提交后调用可选、事务外且可幂等的目标收敛回调；若回调崩溃，原消息重投看到已终态 Operation 时必须再次执行回调。
- `state=FAILED` 与 `error.retryable=true` 是冲突事实。Retry Policy 在当前 attempt 已达到 `max_attempts` 时必须把错误归一为不可重试并清空 `retry_at`，然后再由 Operation 聚合创建 terminal dead letter。
- Outbox `trace_id` 的持久化上限为 64 字符。包含完整 UUID、attempt 与长 outcome 的拼接在 PENDING_RIGHTS 场景会触发 MySQL Data Too Long；终态事件使用固定短前缀、Operation UUID 与 attempt 即可保持可追踪且满足 schema。
- Recovery Scanner 自身触发 Operation `FAILED` 时，不能依赖执行消息再次出现。它必须原子保留一个未消费的 terminal convergence generation，由相同 Worker 路由调用可选 Executor 回调；目标事务失败时 generation 保持未消费并由 transport redelivery 重试。
- Versioned MinIO/S3/OSS 的 destination conditional copy 仍需处理“两个请求都观察 absent”的竞态。只有数据库胜者和观察版本在 Workspace、Asset Version、位置、Key、长度、SHA 与上传身份完全一致且 Version ID 均存在时，才允许复验胜者并精确删除重复版本；不能把同内容等同于同对象。
- Ticket 05 固定快照的新一轮独立故障注入发现五个发布阻断：普通 Operation DLQ replay 在目标终态回调前错误完成 Replay Lifecycle；execution deadline 可产生 `attempt_count=0` 而终态事件 schema 仅接受正数；终态回调在行锁等待后缺少 retention 二次栅栏；Promotion 在实际数据库提交边界仍存在 retention TOCTOU；Task 清理固定执行两次无法收敛三个及以上对象版本。
- 普通 Operation DLQ replay 的耐久边界必须与 terminal Recovery Event 一致：Operation 已进入 `FAILED` 但目标聚合回调尚未成功时，Replay Lifecycle 必须保留可重投的 claimed/pending 状态；只有幂等目标回调成功后才能原子标记 Replay 完成。执行截止时间分支同样不能旁路该屏障。
- “尚未开始执行即因 deadline 终止”是合法且准确的零次尝试事实，不能用 `max(1)` 伪造执行。终态 Validation Event Contract 应明确接受 `attempt_number=0`，而阶段 Evidence 仍保持从 1 开始。
- Task retention 清理不能依赖固定次数的 latest HEAD。版本化存储需要有界分页枚举精确 Key 的所有 owned versions，逐版本复验 Workspace、Asset Version、位置、长度、SHA 与上传身份后精确删除，并在稳定空集合后才允许数据库进入 `DELETED`；超过单次上限必须失败关闭并由 Durable Operation 重试。
- 当前普通 Replay 的失败缺口有两条确定路径：`fail/resolve/defer/exhaust` 在 Operation 事务内按 lease token 调用 `complete_claimed_replays`；重投时 `claim_recovery_replay` 看到 `CLAIMED` 但 Operation 已无活动 lease 又会直接标记 `COMPLETED`。因此目标终态回调必须拥有显式的 pending/complete 协议，不能只在 Worker 内追加一次 best-effort 调用。
- `claim_recovery_replay` 的 execution/reconciliation deadline 分支会先把 Operation 终态化，再以 `PREPARED -> COMPLETED` 结束 replay，Worker 得不到需要执行目标终态回调的工作项；deadline 终止也必须返回 terminal-convergence work，而不能视为“无工作”。
- 对于未取得 Provider lease 就在 replay claim 阶段终态化的 deadline 分支，Replay Lifecycle 仍需要独立、持久化的 claim token；可复用领域 `new_uuid7()` 生成 replay callback token，而不能伪造 Operation lease。重投从 Lifecycle 读取该 token，目标回调成功后再以该 token 完成。
- `AssetValidationCompletedPayload` 与 `AssetValidationFailedPayload` 当前都声明 `attempt_number: int = Field(ge=1)`，但 `build_validation_failed_event` 原样使用 `OperationExecutionRequest.attempt_count`。Operation 在第一次 claim 前因 execution deadline 终止时该值准确为 0，因此终态 Observation Contract 应调整为 `ge=0`；阶段 Evidence 的正数约束不变。
- `AssetValidationLifecycleCoordinator.record_terminal_failure` 在事务外调用 `expire_if_due` 后才获取 Asset/Object 行锁，锁内仅在写入前重新采样时钟，没有调用已有的 `retention.assert_commit_active`。同模块的 `mark_pending_review` 已展示正确的锁内栅栏模式；终态失败应复用该模式，并在事务回滚后调用 `expire(target)`，避免存储 I/O 发生在事务内。
- Ticket 05 集成套件已有可注入 Unit of Work 与可变时钟模式。终态 retention TOCTOU 可通过包装 Asset UoW，在首次 `assets.get(..., for_update=True)` 返回后把时钟推进到 deadline，模拟行锁等待完成时跨界；公开调用仍是 `record_terminal_failure`，验收观察真实 MySQL 与 MinIO。
- `AssetValidationTargetBinder.load_historical` 只做非锁定读取；因此测试仓储包装器可只在 `get(..., for_update=True)` 后推进时钟，精确区分事务外预检与锁内提交边界，不会误把 Binder 阶段当作锁等待。
- Promotion 当前在进入 UoW、获取 Asset/Object 行锁之前采样第一次 `now`，随后虽在 `uow.commit()` 前再次调用 app clock 栅栏，但实际 commit 入口仍可推进时钟并越过 deadline。既有 `ExpireAtPromotionCommitUnitOfWork` 只在添加 PROMOTION evidence 时推进时钟，尚未覆盖真实 commit-entry 边界。
- Promotion 修复至少需要同时处理两层：锁获取后重新采样/校验；提交接缝提供 retention-aware fence，而不是让协调器在调用 `commit()` 前独立采样。若 fence 拒绝提交，数据库事务必须回滚，已复制对象再由现有精确补偿清理收敛。
- `SqlAlchemyAssetUnitOfWork.commit()` 目前只有无条件 `Session.commit()`；`AssetRepository.save_asset()` 已通过直接 optimistic UPDATE 持有 Asset 行锁，而新增 controlled object/evidence/outbox 仍可能待 flush。生产提交栅栏应在同一 UoW 内先 flush，复验注入时钟，再以 MySQL `UTC_TIMESTAMP(6)` 对锁定 Asset 的持久 retention deadline 做权威检查，最后立即 commit；任一检查失败均 rollback。
- 为避免 persistence 反向依赖 Validation 协调器，可在 `asset_ports` 定义窄的 `AssetRetentionCommitExpiredError` 和 retention-aware UoW 方法；Persistence 实现提交原语，Promotion 将该基础设施事实映射为现有 `AssetValidationRetentionError/AssetValidationPromotionError` 并执行精确对象补偿。
- Validation Operation input hash包含 Task Asset retention deadline，因此修改持久 deadline 会正确触发目标身份漂移。数据库时钟栅栏应另以真实 `SqlAlchemyAssetUnitOfWork` 接缝验证：让 DB deadline 已过、注入 app clock 仍在 deadline 前，断言 commit 被 MySQL 时间拒绝且事务写入回滚。
- `ObjectStorage` 当前只暴露单对象 `stat/delete_if_match`，没有版本枚举；`UploadPromoter.discard_for_retention` 对 destination 和 source 各执行一次 unversioned HEAD + exact delete。Retention Coordinator 固定调用两轮只能偶然清除两个 latest versions，无法证明任意 3+ crash copies 已收敛。
- 新能力必须留在对象存储深模块：以精确 location/key 有界分页列出 provider versions，返回 opaque continuation token 与精确 `ObjectReference.version_id`；应用层逐个 `stat` 并校验 bucket、sha256、length、upload-session metadata 后调用已有 exact delete。不能把 provider marker 或 SDK 对象泄漏到应用层。
- MinIO/S3 与 OSS Adapter 已分文件实现；版本枚举必须同时扩展 `object_storage.py` 与 `object_storage_oss.py` 的 Contract 测试，不能只让本地 MinIO 通过而使阿里云生产 Adapter 在 retention 时缺方法。
- 已锁定的 `oss2` SDK 原生提供 `Bucket.list_object_versions(prefix, delimiter, key_marker, max_keys, versionid_marker)`，可用返回的 next key/version markers 做有界分页；因此 OSS 无需失败关闭或自造 REST 请求。
- Retention 在清理前先把 Asset/Object 置为 `DELETING/DELETE_PENDING`，合法 Promotion 随后会在数据库栅栏失败并补偿自己的观察版本；对象存储全版本扫描负责收敛此前 crash-after-copy 留下、未进入 MySQL 的未知版本。达到扫描预算时必须保持 `DELETE_PENDING` 并返回 retryable，不能提前写 `DELETED`。
- 现有并发 retention 测试需要第三次 Executor 调用才能收敛，证明 Durable retry 可以承载有界多轮清理；新的单次 storage sweep 仍应在配置预算内删除 3+ 已存在版本并完成稳定空扫描。
- `UploadPromoter._delete_owned_object` 已集中校验 location/key、expected bucket、content length、upload-session-id 与 SHA-256 后按 exact VersionId + ETag 删除，适合作为每个列举 object version 的所有权验证接缝；只需为 delete marker 增加 exact marker 删除，不应复制校验逻辑。
- S3/MinIO 的现有 `delete_if_match` 已把 exact VersionId 和 If-Match 同时发送；新增 `delete_marker` 只允许 exact VersionId 且不带 If-Match。`list_versions` 必须过滤 `Prefix` 返回中的非精确 key，并在 `IsTruncated` 时要求完整 next key/version markers。
- 有界清理预算应进入 Settings 并由生产 `build_asset_validation_executor` 传给 `UploadPromoter`：page size、单次最大扫描页数、单次最大删除版本数、稳定空扫描次数。达到任一预算后抛 retryable storage failure，依赖 Durable Operation 下一 attempt 继续，而不是无限循环。
- Retention 的数据库完成事实必须晚于对象存储稳定空集合。版本化 Bucket 中一次空页不是完成证明，因为并发 promotion 可能在该扫描后写入；至少两次从头开始的完整空扫描与 MySQL 提交围栏共同关闭该窗口。
- Task Asset 的最终提交围栏不能只依赖应用层在 `commit()` 前采样时钟。事务内 flush、锁定持久 deadline、MySQL `UTC_TIMESTAMP(6)` 与立即 commit 必须属于同一个窄 UoW 原语，查询异常也必须先 rollback。
- 普通 DLQ replay 和 Recovery Scanner terminal generation 应共享同一个不变量：目标聚合及类型化终态事件未成功收敛前，持久 replay/generation 不得标记完成；重投只能补偿回调，不能再次调用 Provider。
- 对象版本分页游标属于 Adapter 私有协议，应用层只接收 provider-bound opaque token。Adapter 必须拒绝跨 Provider token、损坏 token、游标循环、截断页缺失 marker、非精确 Prefix sibling 和缺失 Version ID。
- Ticket 05 最新固定差异终审又确认四个发布阻断：Recovery Scanner 会在 expired claim 终态化后直接完成普通 Replay Lifecycle；Validation Transfer Policy 只绑定 Provider 与 Region，没有绑定 Alibaba Adapter 实际 endpoint；Workspace allowlist 会静默修剪输入并把非法身份变成另一个合法身份；三类受支持 JSON 资产对深层嵌套可泄漏 `RecursionError`。
- Recovery Scanner 的 expired claim 处理必须服从与 Worker replay 相同的回调屏障：若扫描把 Operation 终态化为 `FAILED`，原普通 replay 的 claim token 必须继续保持 `CLAIMED`，直到 terminal-convergence callback 成功；不得由扫描器直接完成。
- 数据出境授权必须绑定发送方 Adapter 的规范化精确 endpoint host，并把该事实纳入不可变 Policy Snapshot；只允许 Provider/Region 会让错误配置或恶意 endpoint 在生成临时读 URL 后把商品图发送到未授权主机。
- Workspace ID 是二进制精确身份。Settings allowlist 不得修剪、折叠大小写或接收不符合 `^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$` 的条目；生产配置错误必须在启动时失败关闭。
- JSON 字节上限不能替代结构复杂度上限。SafeTensors Header、Prompt Template 与 Model Configuration 必须统一捕获解析器递归失败，并在解析后以迭代遍历强制最大嵌套深度和节点数，归一为各自的 `MALFORMED_*` 结果。
- Scanner 修复后的不变量是：只有非终态 Operation 才能由 expired-claim scanner 完成普通 Replay Lifecycle；一旦 Scanner 把 Operation 收敛为 `FAILED`，原 claim token 必须保留到 terminal callback 成功，真实 MySQL 重投证明 Provider 不会再次执行。
- 出境策略的授权身份必须来自实际 Adapter `configured_identity.endpoint`，不能由请求工厂或 Settings 旁路复制。Policy Snapshot v2 同时绑定 Provider、Region、Endpoint Host、Workspace、Asset Kind 与 Retention Class，任何身份漂移均在生成临时 URL 之前失败关闭。
- Workspace allowlist 使用统一规范 ID 校验但不做规范化转换：前后空白、非法分隔符、Unicode 与精确重复均拒绝，大小写不同的两个合法 ID 保持不同授权主体。
- 三类 JSON 资产的复杂度防护采用迭代遍历，并同时限制深度与节点数；待处理栈也在扩展前受节点预算约束，避免仅把 Python recursion 转换为无界内存消耗。
- 完整容器冒烟证明 Ticket 05 的部署链路无需测试进程直接调用 WorkerRuntime：Scheduler 从 MySQL Outbox 投递 RabbitMQ，Celery Worker 运行真实 ClamAV 与确定性内容安全/来源 Adapter，最终 API 投影与对象晋升事实一致。
- MySQL 迁移身份与应用运行时身份必须是两个独立部署 Contract。业务账号只保留 DML，Trigger、DDL 和 Alembic 版本写入由短生命周期迁移账号承担；不能为通过迁移向常驻 API/Worker/Scheduler 授予 `CREATE/TRIGGER/ALTER`。
- 官方 MySQL 容器会在首次初始化时给 `MYSQL_USER` 授予 schema `ALL`，仅在环境变量中声明“运行时账号”并不等于最小权限。已有数据卷和新数据卷都必须在每次迁移前执行幂等 REVOKE/GRANT 收敛，并用实际 DDL 拒绝探针验证，而不是只解析配置。
- 部署环境的 fail-closed 判断必须读取与服务相同的 validated Settings 来源。只检查 `os.environ` 会让 YAML、dotenv 或 Secret Provider 中的 `production` 环境绕过独立迁移身份要求。
- `mysqladmin ping` 证明的是 mysqld 进程响应，不证明凭证有效、目标 schema 已初始化或 SQL 可执行。数据库 readiness 应使用目标网络路径和认证身份执行有确定结果的 TCP 查询。
- 基础设施集成门禁只有在“目标被显式配置或处于 CI”时失败关闭才有意义；连接/认证错误若被归类为 skip，会把生产权限回归伪装成环境缺失。
- ProductBrief 的 Provider 授权身份必须在任何临时 URL 签发前由实际 Adapter 暴露，并与请求时冻结的 provider、region、endpoint host、requested model、prompt version 和 configuration snapshot 全量相等；仅根据 Settings 复制一份预期配置不能证明执行时 Adapter 未漂移。
- Human-in-the-loop 判定属于不可变业务事实。置信阈值、强制复核字段、敏感字段规则和 policy version 必须共同生成 snapshot hash 并随分析请求持久化；重试或配置热更新只能使用该冻结策略，不能改变同一 Operation 是否需要人工确认。
- Provider 的 `review_required` 与 `sensitive` 是不可信输入，只能作为风险加法。服务端策略必须对强制字段直接要求复核，并从配置敏感字段的非空声明值推导敏感状态，避免模型通过返回 `false` 绕过 HITL。
- ProductBrief 幂等记录需要原子 `INSERT ... ON DUPLICATE KEY` claim、行锁读取和同事务 complete；COMPLETED 响应必须保存完整首次响应，否则并发同键可能暴露 409，后续重放也会因聚合继续演进而返回不同事实。
- ProductBrief 的 Provider Call 复合身份不仅是 Workspace；Version 的 `provider_call_id` 必须同时绑定同一 ProductBrief，数据库外键与应用投影检查共同阻止跨聚合来源拼接。
- 同步 Worker 接口不要求同步网络 I/O。可取消的异步 HTTP 运行时可以在不改变 Operation Executor seam 的前提下，让绝对 deadline 覆盖并发容量、连接、流读取和 bounded repair；超时必须等待取消清理完成，进程关闭必须先取消活动任务再关闭客户端。
- Web 冲突恢复的核心事实是最新 ProductBrief，版本历史和 Operation 是可独立失败的辅助投影。核心 GET 成功后应立即更新当前版本和草稿基线，辅助接口用部分成功语义更新；把三者放在 `Promise.all` 会让一次 503 破坏 409 草稿恢复。
- 原始 Vision request/response 的最终删除与对账属于 Ticket 13，但 Ticket 07 必须为每个对象持久化 storage backend/location/key/provider version/etag/hash/size、Retention Class 和精确 deadline；否则后续 Durable Deletion 无法安全条件删除。
- ProductBrief 自动确认事务与通用 Durable Operation 成功事务之间存在必然的 crash window。恢复不能通过 ProductBrief 的可变“当前 Operation”指针判断旧 Operation，而必须沿不可变 Analysis、Provider Call 和输出 Version 事实收敛；否则确认后立即重新分析会让旧成功 Operation 被错误终态化。
- Alibaba Model Studio 公开的 OpenAI-compatible Chat Completions Contract 只定义普通 `POST /chat/completions` 和返回 response ID，没有客户端幂等键或按提交身份查询接口。对于已记录 submission intent、但没有持久 response/call 的结果，系统不能声称可安全自动重投；确认的 429/5xx 可重试，read/write interruption 与 post-response artifact failure 必须进入失败关闭的人工/DLQ 对账。来源：https://www.alibabacloud.com/help/en/model-studio/text-generation
- 视觉 Provider 只消费 `image_url`，不能携带对象存储签名所要求的自定义 `If-Match` Header。生产路径必须先对精确 Version ID 做 `stat` 并复验 ETag/长度/身份，再签发不要求额外 Header 的精确版本 URL；版本不可变性关闭检查后的替换窗口。
- ProductBrief confirmation 的数据库完整性必须同时证明 Version ID、Version Number、ProductBrief、Workspace、Workflow、Approval Type 与 APPROVE Decision 属于同一个精确事实。只把 approval subject 与 confirmation 自身的重复字段相连，无法阻止二者共同写入一个错误版本号。
- ProductBrief Web 的异步事实必须按 Product、ProductBrief、Operation 和请求代次共同分区。仅保存最后一个 operation state 会把旧终态/暂停状态泄漏到新 Operation；页面级商品详情与并发 Operation GET 同样需要 abort/generation/sequence guard。
- Next production build 与 Playwright `next dev` 不能共享同一个 `.next` 产物目录。连续执行时两种编译模式会产生 manifest/module 缓存竞争；发布门禁需要独立 dist directory，并显式验证 `build -> e2e` 顺序。
- ProductBrief Web 工作台不能再通过 `typeof value === "string"` 推断编辑器或接受任意合法 JSON。字段 `path` 是值 schema 的判别依据；草稿恢复、提交前校验和丢失响应重放都必须复用由 OpenAPI 生成的 path-to-kind 映射，并始终持久化带 `kind` 判别器的版本化值对象。
- ProductBrief 的 OpenAPI 契约由两层共同表达：`value.oneOf + discriminator(kind)` 固定七种对象形状，Field schema 上的 `x-commercevision-field-value-kinds` 固定 31 个 path 对应哪一种形状。Contract test 必须同时锁定两层及每个对象的 `additionalProperties: false`，否则客户端生成仍可能接受错误 path/kind 组合。
- ProductBrief Provider 组合现集中在 Worker `build_product_brief_executor`：同一 builder 负责 Credential Provider、Adapter、Artifact Sink、Policy、Transfer Policy 和 lease reserve。挂载凭证在每次提交前以 bounded/stable file read 读取，readiness 复用同一来源；原始 artifact 写入后还会独立复验服务端加密和 Version ID，避免仅信任 write request 的加密意图。
- Worker 的“required Operation kinds”只定义启动时必须具备的 Executor，不会限制共享队列中实际可能到达的事件。只要进程消费 Asset queue 且内置 ProductBrief 能力可执行，Alibaba credential 与 Provider Result storage 就必须在 readiness 阶段验证；纯 Executor 注册测试应注入 ReadyStorage，而不能通过关闭生产依赖探针来适配宿主机 DNS。
- Ticket 07 的 retention 完成定义是持久化精确 deadline/对象身份、过期后拒绝业务访问并为后续删除提供条件删除证据；对象的 Durable 物理删除、重试与对账属于已批准的 Ticket 13。Runbook 必须明确该边界，不能把“有 deadline”描述成“物理删除已上线”。
- Playwright 的隔离 production artifact 位于 `apps/web/test-results/next`，本地约 140 MiB；若 `.dockerignore` 只排除 `.next` 而不排除 `test-results/playwright-report`，源码镜像构建会把测试产物发送到 BuildKit context，造成数百 MiB 无效传输。发布构建上下文必须排除两类测试输出。
- Ticket 07 形成了几个大型深模块：Application ProductBrief 约 3004 行（主要为 Application Service 与 Analysis Executor 两个类），Web Workbench 约 1868 行，Vision Provider 约 1350 行。它们的公共接口集中，但文件体量超过常规审查阈值；终审需判断是否存在真实跨职责耦合再决定拆分，不能仅按行数做高风险机械搬迁。
- 成功 Vision 结果只有在 Provider Call 与对应 Model Version 同一事务提交时才是可恢复事实。任何 authority drift、取消、lease 失效或 evidence 校验失败都不得通过异常补偿单独写入 `SUCCEEDED` Call；否则恢复端无法区分可发布结果与永久丢失结果。
- Provider 已收到 HTTP 状态后，如果响应读取/关闭或 response artifact 持久化不完整，是否拥有可对账证据已经不确定。该事实优先于 429/5xx 的通常重试分类，必须统一进入非自动重试的 `UNKNOWN`；只有完整持久化响应证据后，明确的 429/5xx 才可自动重试。
- 人工修订版本始终需要用户显式确认，即使修订已把所有风险字段解决为零；因此“是否需要确认”和“未解决字段数”是两个独立事实，awaiting-confirmation 事件不能把后者错误约束为至少一。
- ProductBrief 专用 Workflow/Operation 浏览器投影虽然来自通用持久对象，但仍属于 72 小时业务读取面；deadline 到期后必须与 current/version 投影一样返回 `410`，物理删除继续由 Ticket 13 负责。
- OpenAPI 的 `const` 若被生成器降级为 `string`，运行时 validator 再严格也无法为 TypeScript 调用方提供判别联合。生成层必须保留 literal `kind/path`，并用 path-to-value 关联联合表达 31 个字段的合法组合。
- 409 版本冲突不是命令已安全结算：在用户明确恢复或丢弃前，完整 revise 草稿必须继续持久化；同时浏览器存储的商品字段、evidence 与 revision reason 必须携带服务端 retention deadline，并在读、写、恢复三处到期失败关闭。
- Provider 输出中的 evidence reference 不能作为受信任的内部 URI。服务端应只接受可由本次授权 source 和受控 evidence 结构导出的 opaque 引用，拒绝 URL 包裹、编码后的对象位置和其他可被 Web 原样投影的外部位置。
- FastAPI 422 错误的稳定公共详情只应包含 `loc/type/msg` 等结构信息；Pydantic `input`、`ctx` 和原始请求值可能包含商品正文、对象位置或证据引用，不应进入响应或外围错误采集。
- Vision repair 不是首个 Provider 调用的内部细节；每次实际网络提交都必须先持久化独立 `call_index` 意图。恢复时只要存在没有对应完成 Call 的任一索引，就必须进入结果不确定态，不能因为较早调用已有失败证据而自动重投较晚调用。
- ProductBrief 浏览器读取面需要专用投影，而不是复用通用 Workflow/Operation 读取后在路由层过滤。Workspace、目标聚合、Operation kind、retention 和允许字段必须在 SQL 查询中共同收窄，才能同时实现最小权限、二进制租户隔离和过期拒绝。
- Provider 取消必须从 React mutation 贯穿 API client、BFF 和上游 fetch；仅忽略迟到响应仍会让服务端继续产生昂贵或不可逆副作用。商品上下文切换应无条件 abort 当前代次，即使目标商品没有本地持久命令。
- Provider Attempt 与 Provider Call 的取消围栏必须按 `(operation_id, operation_attempt, call_index)` 精确相关。只按 attempt 相关会让已完成的 malformed call 0 错误覆盖正在提交的 repair call 1，从而允许虚假取消。
- 原始 Provider artifact 的物理删除可以由 Ticket 13 实现，但写入前的 durable discoverability 不能延期。每个 request/response 写入必须先有 MySQL artifact intent，至少冻结确定性 key、期望 hash/size、retention 和所属 call；exact Version ID 在写入后立即补全，未知写入由后续按精确 key 枚举对账。
- ProductBrief 版本历史必须采用有硬上限的 keyset 分页，且一页版本的字段、evidence 与公开 Provider 摘要要批量加载。无界历史加每版本多次查询既是 DoS 面，也是长期人工修订后的确定性性能退化。
- 浏览器只需要 Provider、请求/解析模型和 latency 等公开摘要。Operation ID/attempt/call index、endpoint host、配置 hash、Provider request ID 和内部 error metadata 属于受控 provenance，不应因版本读取而暴露。
- Provider request artifact 必须先完成自身的 MySQL intent 和对象写入，再记录紧邻外部调用的
  submission intent，才能让提交前存储故障保持可安全重试。与此同时，Executor 必须在签发新
  临时 URL 或构造新 artifact 前检查同一 Operation Attempt 是否已有 Provider Attempt；否则
  重放会先因短期签名内容漂移被误报为 artifact integrity conflict，而不是稳定的 submission
  fence。
- 时间冻结测试必须在被测事实创建后推进控制时钟，不能在不可控的 Worker/SDK 初始化之前
  假设固定 1 秒裕量。进程退出测试也必须从目标边界开始计时，不能把模块导入和操作系统调度
  时间混入泄漏断言；外部 subprocess 的短暂启动失败应保持 retryable，但容量回收测试仍需在
  同一 Adapter 的有界重试内证明最终成功。
- ProductBrief continuation 的授权不是“事件曾经有效”，而是消费和每个节点 claim 时仍有效。
  Worker 必须以 MySQL 当前时间、Workflow retention 状态、Workflow deadline、ProductBrief
  deadline 和精确 confirmed version 共同判定；过期或已被重分析取代的事件是可审计 stale
  no-op，不能启动 Graph、产生副作用、消耗 retry 或进入 DLQ。
- ProductBrief 与 Commerce Workflow 的绑定必须在命令服务、只读投影和异步 Worker 三层保持
  同一不变量：Workflow 类型严格为 `COMMERCE_IMAGE_GENERATION`，冻结输入中的
  `product_id` 与目标 Product 完全相等。仅对 Commerce 类型执行条件校验等同于允许其他类型
  绕过绑定。
- 浏览器本地持久化属于租户数据边界。ProductBrief 恢复记录必须同时绑定 Workspace 和
  Product，旧 schema、缺失或损坏的 active identity marker 都必须 fail closed 清理；服务端
  返回权威 `410 PRODUCT_BRIEF_RETENTION_EXPIRED` 时，即使客户端时钟落后也要立即 abort、
  清内存和 sessionStorage，并停止恢复。
- MySQL 的 append-only 事实需要同时禁止 UPDATE 和普通 DELETE。迁移中临时移除 immutable
  trigger 时必须把可行预检前置，并在 MySQL DDL 隐式提交语义下以 `try/finally` 恢复；
  downgrade 不能删除尚未映射的 Artifact intent/unknown ledger。到期物理删除只能经 Ticket
  13 的受控 durable 清理能力，不得通过可由普通连接伪造的 session variable 绕过。
- Vision Transport 的取消只有在后台读/关任务被有界回收后才算完成；若底层客户端抑制取消，
  必须淘汰客户端并失败关闭 Worker readiness/进程，且在清理完成前不能返还并发容量。聚合
  shutdown 应逐项 best-effort 释放并汇总异常，不能让首个 close 失败阻断其他资源回收。
- Provider-neutral 输出 schema、错误和字段目录属于 Contracts 或 Application 注入 seam，
  Provider Adapter 不应直接导入 Domain 实现。ProductBrief 持久化也应按
  Brief/Version、Analysis/Call、Artifact Ledger、Confirmation 拆成窄端口，再由同一 UoW
  组合以保留事务一致性；一个二十余方法的 Repository Port 会把所有子域变化耦合到同一接口。

## Ticket 07 第十轮后端修复不变量

- Recovery 的单次扫描必须只采样一次同事务 MySQL 当前时间；Host clock 不能参与 Lease、
  retention、stale threshold 或 recovery event 时间的业务判定。
- 发布 recovery event 与推进 scanner freshness 必须属于同一事务；Outbox 已发布但尚未消费时，
  Scheduler 不能为同一 stale observation 持续制造新消息。
- Commerce Workflow 的 `none` 不能等同于通用 Graph recovery：pre-ProductBrief 没有可恢复
  Graph entry；confirmed-before-first-claim 则必须由当前 confirmed ProductBrief 重建 continuation
  intent，两者都不能制造 retry/DLQ。
- revise、confirm 和 Vision submission 的最后授权点都必须锁定 Workflow，并验证
  `COMMERCE_IMAGE_GENERATION`、冻结 Product、Workflow ACTIVE/deadline 与 ProductBrief deadline。
- Lease token 只能存在于权威 Workflow Step 行和当前进程内存；LangGraph state/checkpoint、
  event、step generation metadata 和历史 checkpoint 都不得保存可逆 token。
- Commerce `none` 需要分成两个公开结果：没有 confirmed ProductBrief 时只推进 scanner
  observation，不启动 Graph；存在当前 confirmed ProductBrief 但尚无 retrieval step 时，Recovery
  必须从 Brief/Version/Confirmation 重建精确 event identity，并允许 Worker 首次创建 generation。
- `recover_product_brief_continuation` 的 retrieval 分支本身已能通过 `_begin_node_locked` 创建 Step；
  当前提前的 `retrieval_step is None` 拒绝位于该分支之前，是 confirmed-before-first-claim
  无法恢复的直接阻断。
- ProductBrief command 与 executor 可共用同一纯验证函数：输入已锁定 Workflow、ProductBrief
  和 `uow.database_now()`；它必须同时校验 Commerce 类型、冻结 Product、Workflow ACTIVE、
  两个 deadline 未到且 ProductBrief deadline 与 Workflow deadline 相同。命令映射为
  `ConcurrencyError`/retention error，Executor 映射为稳定 `PRODUCT_BRIEF_WORKFLOW_NOT_EXECUTABLE`。
- 现有公开 Provider seam 已有 `CountingAnalyzer` 与 cancellation-before-consumption 测试模式；
  binding drift 可在 `product-brief.requested` 入 Outbox 后、Worker 消费前通过同样路径验证
  Analyzer 调用数保持 0、Operation 收敛为稳定失败。

## Ticket 07 最终验收不变量

- Task-scoped 事实不能直接信任可配置的 Workflow `expires_at`。唯一权威截止时间是
  `min(expires_at, created_at + 72h)`；公开 Commerce 创建、legacy Workflow、ProductBrief、
  Analysis、Provider artifact/call、continuation 和 pre-analysis 投影必须共用这一 domain seam。
  显式更短的 Workflow deadline 必须原样保留，不能被 72 小时上限反向延长。
- Retention deadline 与 Retention status 是两个独立栅栏。即使 deadline 尚未来临，
  `EXPIRING`、`DELETING` 或 `EXPIRED` 的 pre-analysis Workflow 投影也必须返回 410，不能让
  Web 建立新的持久命令。
- 浏览器中的“安全重试”只有在 exact schema-3 durable command 仍存在、未过期且与内存命令
  完全一致时才可发出 revise/confirm POST。响应结算必须再次验证同一命令，并同时绑定
  Workspace、Product、ProductBrief、Workflow 与 Operation；人工修订切换到新 Operation 只能
  经显式、可证明的 confirmed-base reopen 转换。
- `window.localStorage/sessionStorage` getter 本身也可能同步抛出 `SecurityError`。localStorage
  不可用只允许跳过 legacy purge；sessionStorage 不可用必须 abort 当前代次、清除内存恢复态并
  显示 fail-closed 错误，不能继续发送没有 durable replay identity 的 mutation。
- 默认 deterministic Compose 不能依赖 Git 外的 Secret 文件。仓库内空白 fixture 只保证
  clean clone 的 bind source 存在，不是凭据；切换 Alibaba 时必须显式挂载真实只读 Secret，
  缺失或空白在 Worker 接收任务前失败关闭。

## Ticket 08 初始不变量

- Brand Profile 的历史内容与当前授权是两个不同事实：历史版本保留发布时的精确
  Asset Version/Rights Record 引用供审计，但任何读取或后续检索都必须依据 MySQL 当前
  Asset/Rights 状态重新计算成员可用性，历史版本本身永远不能授权已失效成员。
- 发布是一个带乐观版本的短事务：锁定 Brand Profile 身份与所有选中成员，使用同一事务的
  MySQL 当前时间重新验证 Workspace、Foundation retention class、Asset 状态、当前
  Rights Record、用途、Provider、派生权限和有效期，再原子追加不可变版本并切换当前指针。
- Rights replacement、revocation、expiry 或 Asset deletion 不能改写历史版本；它们需要让
  受影响的当前 Brand Profile 进入 `NEEDS_REPUBLISH`。同一变化重放必须幂等，且不得把已经
  `ARCHIVED` 或已由新版本修复的档案错误降级。
- Brand Profile 持久化应按 Identity/Draft、Published Version/Member 和当前可用性查询拆为
  窄端口，由同一 UoW 组合以保留发布事务一致性；不可把资产授权判断埋进 Web、路由或
  Repository 的宽泛 JSON 查询。
- Ticket 08 的首批 RED tests 必须从公开 Domain、HTTP 和真实 MySQL 接缝证明：并发发布只有
  一个胜者、无效成员发布失败、发布后 Rights 变化立即改变当前可用性并标记重发、跨
  Workspace 标识始终返回不可枚举错误。

## Windows 沙箱 1312 根因与稳定修复

- Codex 沙箱令牌本身可正常启动 `cmd.exe`、Windows PowerShell 5.1 和 `whoami.exe`；
  失败只发生在 Microsoft Store/MSIX 的 App Execution Alias
  `C:\Users\23163\AppData\Local\Microsoft\WindowsApps\pwsh.exe`，因此重启 runner、
  放宽仓库权限或降低沙箱级别都不能解决根因。
- 官方 MSI 把 Win32 PowerShell 安装到 `C:\Program Files\PowerShell\7\pwsh.exe` 并写入
  machine PATH。Codex 必须完整重启才能继承新的 PATH；仅重启 command runner 仍会继承
  Desktop 主进程缓存的旧路径。
- 修复验收必须同时证明：实际 `pwsh` 路径、PowerShell 版本、受限沙箱身份，以及多次
  独立 SpawnChild 均成功。单次 `pwsh --version` 不能证明 1312 已稳定消失。

## Ticket 08 Web 接缝恢复

- Ticket 08 的浏览器测试接缝已经由验收条件固定为 Brand Profile HTTP client、Workbench
  controller 和公开页面；这些测试只观察版本冲突保留本地草稿、权威读取替换、历史 cursor
  去重、迟到响应隔离以及历史内容与当前成员可用性的分离，不探测 React 或 Repository 内部实现。
- 现有 Web BFF 采用精确方法/路径 allowlist，并由服务端注入受信 Principal；Brand Profile
  只能扩展这套窄入口，不得让浏览器构造可信身份，也不得用宽前缀放行未知 action。
- 本轮恢复时一次组合状态命令在递归 `Get-ChildItem` 阶段超过 10 秒，但前置 Git 基线读取已经
  完成；后续文件枚举改用 `rg --files`。一次 `wait_agent` 使用了低于工具下限的 1 秒参数，
  后续固定使用至少 10 秒，不重复无效调用。
- 现有 BFF 把控制面请求体限制为 1 MiB、响应限制为 2 MiB，并以 UUID/action 正则逐路径放行；
  Brand Profile 历史必须继续 cursor 分页并保持在同一有界读取面，不能借新工作台放宽限制。
- Domain 已将 Brand Profile 身份明确为 `workspace_id + brand + profile_key`，其中 `brand` 是
  严格的展示名称、`profile_key` 是规范 token；Web 只能使用服务端返回的乐观 `version` 发命令，
  不能把当前发布版本号当作身份版本，也不能在客户端规范化这些键后静默重试。
- Application seam 已落为同一 UoW 下的三组窄端口：Identity/Draft、不可变 Publication、
  Asset Authority。mutation 响应只返回身份/当前 head；只有 Version GET 重新计算并返回
  `published_rights_*` 与 `current_*`，从接口层防止幂等缓存把旧 usability 当作当前授权。
- Web 草稿需要覆盖 Domain 的完整受控字段：规则、色板、必需标记、禁止元素、语气、文案约束、
  purpose、provider、派生权限和最多 64 个精确 Asset Version 选择；UI 可以提供逐项编辑器，
  但发送时必须是严格结构化契约，不能用任意 JSON 作为逃逸面。
- 首版 HTTP route 的 publish 未显式返回 201，且 `profile_id` 仍是裸 `str`；这会分别削弱
  “新不可变版本已创建”的响应契约和 OpenAPI 的 canonical UUID 约束，已要求 Application
  Worker 用 route/contract RED tests 收紧。Web client 对 publish 状态保持 fail-closed 201。
- Pydantic 带默认值的 `next_cursor` 在生成 TypeScript 中是可选的 `string | null | undefined`；
  Workbench controller 必须在接口进入点归一为内部严格的 `string | null`，不能让第三种状态向
  分页状态机扩散。
- Persistence focused 测试发现 publication/validation 若在取得 Asset locks 之前采样数据库时间，
  等锁期间自然到期的 Rights 可能被旧时钟误授权；权威 `database_now()` 必须在全部成员锁定后、
  最终授权判断前采样。
- TypeScript 类型不能替代不可信 HTTP 响应的运行时契约。Brand Profile Web decoder 必须在数据
  进入 Controller 前校验租户/品牌/档案身份、canonical UUID、状态与 Rights 枚举、数组上限、
  opaque cursor、冻结 Draft/member 映射及 `currently_usable ↔ AUTHORIZED`，协议违例统一映射
  为 fail-closed 502。
- 同一品牌创建第二个 `profile_key` 时，create command 不能假定当前没有已选档案。安全 token
  需要同时绑定请求的 `profile_key` 与命令开始时的当前 profile id/version；只有 baseline
  未漂移且响应 key 精确匹配时，Controller 才能切换到新档案。
- mutation 请求一旦发出就可能跨越 Rights/版本变化，因此旧的绿色 validation 不再具有发布
  授权意义；在命令开始时递增 validation generation 并清空结果，比只在特定 422 分支清理更
  能覆盖超时、响应丢失和迟到回包。
- 历史 Brand Profile 的内容快照与“此刻是否仍可使用”必须来自一个一致的数据库观察边界。
  在 `READ COMMITTED` 下先读 Asset/Rights、再单独采样 `database_now()` 会把旧授权事实与
  新时间拼接成虚假 `currently_usable=true`；持久化端口应在稳定成员锁内返回快照与同一次
  MySQL `UTC_TIMESTAMP(6)`，Application 不得再次采样。
- Brand Profile 失效不能以 API/Worker 主机生成的 `event.occurred_at` 作为数据库顺序或
  `published_at` 上界。安全算法是在同一事务中稳定锁定当前 Profile head 与 Asset/current
  Rights，随后采样 MySQL 当前时间并以“当前 head 仍精确引用已经失效的 authority”执行 CAS；
  事件时间只用于审计，慢主机时钟不得造成永久漏失效。
- Asset 物理删除由后续 Retention Ticket 执行，但 Ticket 08 必须先定义并消费类型化、
  可演进的 `asset.delete.completed` observation。Payload 要绑定 Workspace、Asset 和删除
  generation；重复投递必须幂等，错误 Workspace/Aggregate 必须失败关闭，当前引用该 Asset
  的 Profile 才能进入 `NEEDS_REPUBLISH`。
- Windows 上 C2PA Contract 的进程隔离预算必须区分 native/interpreter 冷启动与真正泄漏。
  本地 1.5 秒在全套并发负载下可重复耗尽，而 3 秒预算连续五轮通过；Linux 仍保持 1.5 秒，
  生产 Adapter 的 10 秒截止时间与硬终止/容量回收语义未放宽。
- Pydantic 的默认整数/布尔解析会接受 `true -> 1`、`3.0 -> 3`、`"3" -> 3` 和
  `"false" -> false`。Optimistic version 与授权派生开关是控制面语义，必须在字段上显式
  `strict=True`，并由 Domain 构造器再次拒绝 Python `bool`（它是 `int` 子类）、float 和 string。
- Keyset 历史分页的有序性不能只在单页内验证。客户端追加页应允许服务端边界重叠并去重，
  但所有新的 version number 必须严格低于既有尾部；否则乱序响应会破坏审计历史和后续 cursor
  推理。当前 token 下的协议违例还必须结束 loading，不能把工作台永久留在加载态。
- Canonical UUID 必须在 OpenAPI、API route 和 Web BFF 三层表达相同。BFF 的 `[0-9a-f]`
  正则若带 `i` flag，会让大写 alias 穿过 allowlist 后才在 API 失败，产生 403/422/404 顺序差异
  和不必要的上游流量；Brand Profile 路径应在代理边界直接按 lowercase pattern 拒绝。
- Opaque keyset cursor 不是授权边界，但可伪造或跨查询复用的 cursor 仍会静默跳过审计结果。
  Brand Profile cursor 因此必须绑定完整查询 identity（Profile 列表为 Workspace + 可选 Brand，
  Version 列表为 Workspace + Profile）、cursor kind 与排序 schema，并用域分隔派生的
  HMAC-SHA256 current/previous key 验证；旧 unsigned JSON cursor 不能在未发布的 Ticket 08
  中保留兼容逃逸面。解码失败对外统一为无细节的 invalid cursor，且必须在 Repository 查询前发生。
- Alembic 在 MySQL 上执行非事务 DDL。某一旧 migration 的“失败前无任何变化”测试若从未来
  `head` 开始 downgrade，会先合法删除所有后续 revision 的对象，再到达被测 fail-closed guard，
  因而无法证明目标 migration 自身的原子前置检查。此类测试必须从被测 revision 本身开始并
  downgrade 到其直接 parent；head roundtrip 应由独立链路测试负责。

## Ticket 08 最终生产不变量

- Brand Profile 历史内容和当前授权必须保持两个独立事实。历史 member 永久保留发布时的
  Asset Version/Rights Record identity；每次读取和后续使用都在稳定锁内重新读取当前
  Asset/Rights authority，并在取得全部锁后用独立语句采样 MySQL 当前时间。先采样时间再等待
  锁会把等待期间已经到期的 Rights 错误授权。
- 发布、校验和失效共享同一 authority seam：Profile head、Asset/current Rights 按稳定顺序
  锁定，最终判断使用一次数据库时间。Rights identity replacement 即使许可文本等价，也会使
  当前 publication 进入 `NEEDS_REPUBLISH`；迟到、重复或旧 generation 事件不能污染新 head。
- Brand Profile keyset cursor 必须绑定 Workspace、查询种类、Brand/Profile identity、排序
  schema、签发时间和 keyset boundary，并使用 current/previous 根密钥经独立域派生的
  HMAC-SHA256。生产 API 对 current 和 previous trust key 执行同等安全校验，禁止空值和公开
  本地默认 secret；配置不安全时必须在构造数据库、对象存储或 readiness 资源前失败关闭。
- 浏览器 pending command 是 mutation 的 durable authority，而不是 UI 缓存。记录必须绑定
  Workspace、Brand、Profile ID、不可变 profile key、action、expected versions、完整 payload、
  command/payload digest、幂等键和 attempted draft；preflight 读取失败、409、408/429/5xx、
  取消或响应丢失都保留未确认命令证据，只有精确对账或操作人显式清理后才解锁。
- Web 的 2 MiB BFF 响应硬上限与 Brand Profile 历史分页上限共同构成读取安全边界；一页最多
  两个不可变 publication，历史状态必须显式区分 unloaded/loading/error/ready。权限 403、
  retention 410、租户身份漂移和持久恢复协议违例均失败关闭管理员能力，不把错误伪装为空历史。
- Trusted actor、Domain 和事件中的 Actor ID 统一拒绝 Unicode `Cc` 控制字符；malformed Asset
  UUID 事件属于永久路由失败。Worker 关闭保持逐项 best-effort 并聚合错误，Scheduler/API/Worker
  使用同一个 RabbitMQ 密码派生连接，避免单个进程在表面健康的异构凭证下脱离可靠消息链。

## Ticket 10 PRODUCT_FUSED 索引边界

- Ticket 10 的公共测试 seam 已由锁定工单确认：确认态 ProductBrief 原子请求模块、通用 Durable
  索引执行接口，以及 MySQL CJK 词法查询接口；不在本 Ticket 实现 Ticket 11 的 Rights-first
  hybrid fusion。
- 当前 checkpoint 已完成受控文本、PRODUCT_FUSED identity/Provider/Event 契约与
  `product_search_documents` ngram migration。剩余纵切必须复用 Ticket 09 的 Collection、
  Durable Operation、Outbox 和 authority 收敛，不创建平行执行框架。
- `ProductBrief` 是领域术语；`PRODUCT_FUSED` 与 Search Document 是索引实现身份，不写入
  `CONTEXT.md` 领域词汇表。确认版本是文本来源的批准边界，但 raw OCR/raw prompt/未确认模型输出
  仍必须默认排除。
- Ticket 09 的 `MySqlImageIndexRequestService` 已封装 Collection + Embedding Record + Durable
  Operation + typed Outbox 的原子提交和并发 winner reload；Ticket 10 应加深该模块或复用其内部
  原语，不能复制一套浅层索引生命周期。
- Application `ImageIndexingExecutor` 的外部执行流程本身与 vector kind 无关，当前只有 Target、
  Provider request 和 Milvus row 三处硬编码 IMAGE。最小深模块方向是把 target 提升为通用索引
  target，并保留 IMAGE 公共名称兼容别名；PRODUCT_FUSED 只增加受控文本/provenance 数据。
- PRODUCT_FUSED 的稳定输入身份必须绑定 ProductBrief 本体而非确认版本：版本号是可推进的 provenance，
  不是受控内容。等价新版本只原子更新 Embedding/Search Document 的版本溯源与 retention；受控文本
  变化产生新 input hash 和 Record，不同 ProductBrief 即使内容相同也必须隔离。
