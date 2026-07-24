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
