# CommerceVision Agent 执行进度

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
