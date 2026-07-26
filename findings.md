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
