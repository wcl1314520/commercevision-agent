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

- 任务数据和 Checkpoint 默认保存 72 小时。
- 品牌、Prompt 和公开评测集长期保存到删除/退役。
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
