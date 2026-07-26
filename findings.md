# CommerceVision Agent 研究结论

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
