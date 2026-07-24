# CommerceVision Agent 立项、架构与实施计划

## 本轮目标

在 `D:\个人项目\电商生图agent\mine` 中建立面向公开 GitHub、在线 Demo 和 Agent 应用开发求职的完整可上线系统。当前建立远程 Git 与 CI 基线，并按 `grill-with-docs -> to-spec -> to-tickets -> 独立上下文 implement` 完成 Phase 2：资产、商品理解与多模态记忆。

## 输入

- `D:\个人项目\电商生图agent\open-picsetai-main`
- `D:\个人项目\电商生图agent\Fashion-AI-main`
- 用户确认：
  - 目标岗位为 Agent 应用开发实习。
  - Python 是主要技术栈。
  - GitHub 和在线 Demo 公开。
  - 多品类合法素材可获得。
  - 采用 MySQL，不采用 PostgreSQL。

## 阶段

### Phase 1：项目方向与技术边界
**Status:** complete
- 确定 CommerceVision Agent 产品定位
- 确定 Python-first、单一 Agent、LangGraph、MySQL + Milvus
- 确定公开仓库和许可边界

### Phase 2：删除旧文档体系
**Status:** complete
- 删除旧 `docs` 内容和旧验证脚本
- 保留必要研究结论并重新编写

### Phase 3：重建项目文档
**Status:** complete
- 产品、架构、数据、AI、工程、部署、路线图和 ADR
- 公开 Demo、评测和求职价值映射
- 模板和总索引

### Phase 4：一致性和完整性验证
**Status:** complete
- 检查相对链接、元数据和索引覆盖
- 检查 MySQL/PostgreSQL、单 Agent/多 Agent 等关键口径
- 检查阶段任务和验收标准覆盖

### Phase 5：Obsidian 文档浏览接入
**Status:** complete
- 将项目根目录配置为 Obsidian Vault
- 共享 Markdown、模板、图谱和核心插件配置
- 排除个人工作区状态和本地缓存

### Phase 6：实施路线 Phase 0 工程基线
**Status:** complete
- 建立 Python workspace、服务入口和共享 Contract
- 建立 Next.js Web 基线
- 建立 Docker Compose、本地依赖和配置校验
- 建立测试、CI、Secret Scan 和 SBOM 基线
- 通过本地命令和健康检查验收后进入下一阶段

### Phase 7：实施路线 Phase 1 Durable Agent Runtime
**Status:** complete
- 固化 Workflow/Step/Attempt 状态机和版本化 Contract
- 建立 MySQL ORM、Alembic、Repository 和 Unit of Work
- 建立 Outbox/Inbox、Lease、Retry、DLQ 和恢复调度
- 实现 MySQL LangGraph Checkpointer 的同步/异步 Contract
- 实现可 Interrupt/Resume 的 Fixture Agent Graph
- 打通 API、Celery Worker、Scheduler 和可靠消息链路
- 通过并发、重复消息、非法转换、Worker 恢复和人工等待验收

### Phase 8：GitHub 公开仓库与远程 CI 基线
**Status:** complete
- 安全完成 GitHub CLI OAuth 认证，不使用已暴露 PAT
- 创建公开仓库 `commercevision-agent`
- 配置 `origin` 并推送 `main`
- 验证全部 GitHub Actions 门禁
- 使用未暴露的 30 天 classic PAT 为 `gh` 提供 `repo`、`workflow`、`read:org` 权限
- 已暴露 PAT 的撤销等待用户在删除动作前再次确认

### Phase 9：Phase 2 领域澄清与活文档
**Status:** complete
- 建立根目录 `CONTEXT.md`
- 固化任务资产与基础资产保留边界
- 创建 ADR-006
- 完成 `PLAN.md` 并锁定 Phase 2 范围、深模块和 Provider 基线
- 已确认 `to-spec` 测试接缝：HTTP、Durable Worker/Event、真实基础设施检索、MCP、Provider Adapter

### Phase 10：Phase 2 规格与工单
**Status:** complete
- 已确认测试接缝
- 已生成 `.scratch/phase-2-assets-retrieval/spec.md`
- 已将规格拆为 17 个 blockers-first 独立 Ticket
- 每个 Ticket 已写入 `.scratch/phase-2-assets-retrieval/issues/`

### Phase 11：Phase 2 独立上下文实现
**Status:** in_progress
- 每个 Ticket 使用独立子 Agent 上下文和 TDD
- 逐 Ticket 审查、测试和提交
- 不跨 Ticket 复用隐式上下文
- Ticket 01 已完成独立上下文实现、主控双轴审查和全量门禁；当前提交为 `088203f`。
- Ticket 03 已完成独立上下文实现、主控双轴审查和全量门禁；当前提交为 `856f57b`。
- Ticket 02 已完成独立上下文实现、反复对抗审查和全量门禁；最终单一实现提交为 `9b88493`。
- Next.js 15.5.21 安全维护作为独立提交 `083004d` 落地，不混入 Ticket 02 的业务实现。
- 下一步进入已由 Ticket 02 与 Ticket 03 共同解除阻塞的 Ticket 04，仍保持单 Ticket、单独立上下文、单独提交。

### Phase 12：Phase 2 集成、可靠性与退出验收
**Status:** pending
- 运行完整静态、单元、集成、迁移、容器和安全门禁
- 验证未授权素材召回率为 0
- 验证增量索引和 Milvus 可重建
- 验证固定检索集指标与 ProductBrief 人工确认
- 更新路线图、Runbook、OpenAPI、评测与 GitHub CI 证据

## 成功标准

1. `README.md` 是公开项目入口。
2. `docs/README.md` 可索引所有正式文档。
3. 架构支持完整上线，不是一次性 Demo。
4. MySQL 是事务事实主库，Milvus 是可重建向量索引。
5. LangGraph Checkpoint 与 Workflow 业务状态明确分离。
6. 项目具备 Planning、Tool/MCP、Memory、HITL、Evaluation、Reflection 和 Replay。
7. 路线图每个阶段都有退出标准。
8. 没有无许可证代码复用或真实凭证。
9. Phase 0 可通过一条命令构建，全部服务健康且依赖漏洞审计无已知漏洞。
10. Phase 1 任务状态、Checkpoint 和消息交付均以 MySQL 为事实来源，可恢复、可幂等、可审计。

## 已知限制

- Phase 0 本地 Compose 不是生产多可用区拓扑，不承诺 99.95% SLO。
- Phase 1 使用确定性 Fixture Tool，不包含真实模型、生图 Provider 和多模态检索。
- MySQL `DATETIME(6)` 类型升级需要 `ALGORITHM=COPY`；生产大表必须使用维护窗口或受控在线 schema 迁移。
- Milvus 生产部署方式需要在目标阿里云地域完成容量和运维评估。
- GitHub Actions 初始远程运行 `29905132767` 已通过 Python、Web、容器构建、Secret Scan 和 SBOM。

## 错误记录

| 日期 | 错误 | 处理 |
|---|---|---|
| 2026-07-21 | 两次 PowerShell 删除命令被环境策略阻止 | 不再重复，改用 `apply_patch` 逐文件删除 |
| 2026-07-21 | 一次一致性检查的工作目录误写为不存在的路径 | 改为在已验证的 `mine` 绝对路径重新执行 |
| 2026-07-21 | 在尚未初始化 Git 的目录中执行 `git check-ignore` 无法验证忽略规则 | 改为直接检查 `.gitignore` 内容，并通过 Obsidian 生成的 `workspace.json` 验证匹配口径 |
| 2026-07-21 | 初次读取 CI/CD 文档时使用了错误目录 `docs/04-engineering` | 按仓库总索引改读 `docs/05-deployment/ci-cd-and-release.md` |
| 2026-07-21 | Phase 0 健康检查批量补丁因 hunk 边界格式错误被解析器拒绝 | 确认未写入文件，改为按服务代码、Compose、脚本分批应用 |
| 2026-07-21 | 完整 Compose 重建拉取 `alpine:3.21` 的 Docker Hub token 超时 | 不重复拉取，改用本机已有镜像层构建 OTel 健康镜像 |
| 2026-07-21 | 最后一版 Compose 重建下载 `wcwidth==0.8.2` 时 PyPI 连接超时 | 为 Python 镜像增加可覆盖的 `UV_INDEX_URL` build arg；本地使用镜像，CI 默认官方 PyPI |
| 2026-07-21 | RabbitMQ `check_port_connectivity` 健康检查超过 5 秒 timeout，容器被误判 unhealthy | 改用镜像内置 `nc -z 127.0.0.1 5672` 做低开销 AMQP 监听端口检查 |
| 2026-07-21 | standalone Web 启动正常但 `up --wait` 超时，`localhost:3000` 健康检查连接被拒绝 | Alpine 容器内 `localhost` 优先解析到 IPv6，而 Next 监听 IPv4；探针改用 `127.0.0.1:3000` |
| 2026-07-21 | `pnpm audit` 发现 Next 间接依赖 PostCSS 8.4.31 存在 Moderate XSS 公告 | 使用 pnpm override 升级到已修复且已通过供应链冷却期的 PostCSS 8.5.20，并将审计加入 CI |
| 2026-07-21 | 本地环境尚未安装 `pip-audit`，Python 漏洞审计命令不可用 | 将 `pip-audit` 纳入 dev dependency 和 CI 门禁 |
| 2026-07-21 | 两次 Docker Hub manifest 查询超时 | 使用成功拉取并验证的 Valkey 8.1.8 镜像及其 digest，不重复失败的 manifest 请求 |
| 2026-07-21 | 首次执行 `uv tree --locked --all-packages` 参数不受当前 uv 支持 | 改用 `uv tree --locked` 完成依赖树检查 |
| 2026-07-21 | pnpm 11 忽略根 `package.json` 中旧位置的 `pnpm.overrides` | 将 PostCSS override 移到 `pnpm-workspace.yaml` 后重新生成锁文件 |
| 2026-07-21 | Windows 中文区域设置下 `pip-audit` 通过 `pip-api` 读取 pip 版本时按 UTF-8 解码失败 | 新增跨平台包装脚本，为审计子进程显式启用 `PYTHONUTF8=1` |
| 2026-07-21 | Python 审计发现旧版 LangGraph、checkpoint、sdk 与 pytest 共 6 条已知漏洞 | 提升到官方修复版本范围并重新解析 `uv.lock`，不做漏洞豁免 |
| 2026-07-21 | PostCSS 8.5.21 发布不足供应链冷却期，pnpm 自动生成版本例外 | 改用同样包含安全修复且发布更早的 8.5.20，删除冷却期例外 |
| 2026-07-21 | Valkey 8.1 无法读取旧 Redis 7.4 卷中的 RDB v12，缓存容器持续重启 | Compose 改用新的 `cache_data` 卷，保留旧 `redis_data` 卷供核验后单独清理 |
| 2026-07-21 | Compose 默认将项目名解析为通用目录名 `compose`，可能与其他仓库冲突 | 在 Compose 文件顶层固定项目名 `commercevision` |
| 2026-07-21 | 最终镜像检查命令一次向 `docker images` 传入两个 repository 参数而失败 | 拆分镜像查询；该错误不影响已构建镜像或运行服务 |
| 2026-07-22 | LangGraph API 探测读取 `BaseCheckpointSaver.__abstractmethods__` 失败 | 当前类未暴露该属性；改为逐项检查方法签名和源码，不重复依赖 ABC 元数据 |
| 2026-07-22 | 依赖探测导入 `alembic` 失败 | Phase 0 尚未安装迁移框架；将在 Phase 1 persistence 包中加入 Alembic 并锁定依赖 |
| 2026-07-22 | 首批 Domain/Tool Runtime Ruff 检查发现 7 个格式、未使用导入和长行问题 | 按 Ruff 建议修正并执行统一格式化；缓存目录权限告警不影响检查结果，后续使用 `--no-cache` 验证 |
| 2026-07-22 | Alembic 基线补丁因迁移 README 的旧文本与预期不一致而未应用 | 读取真实文件内容后重新应用完整补丁，未保留半写入文件 |
| 2026-07-22 | Persistence 初次 Ruff 检查发现 5 个导入排序和长行问题 | 使用 Ruff 格式化/自动修复后重新通过检查 |
| 2026-07-22 | 首次 `alembic upgrade head` 因自动生成脚本缺少 `commercevision_persistence.models` 导入而失败 | 检查数据库仅存在空 `alembic_version` 表，无业务表半写入；补齐自定义 UTC 类型导入后重跑 |
| 2026-07-22 | 主机未安装 MySQL CLI，无法用 `mysql` 命令检查表 | 改用项目已锁定的 SQLAlchemy/PyMySQL Inspector 检查真实数据库状态 |
| 2026-07-22 | 首次迁移后 `alembic check` 将 `LargeBinary(16777215)` 与 MySQL 反射的 `MEDIUMBLOB` 判为类型漂移 | 模型改为显式 `MEDIUMBLOB`，不修改已正确创建的数据列 |
| 2026-07-22 | Application 首次 Ruff 检查发现嵌套条件和长行 2 项 | 合并重试时间条件并拆分错误消息，重新执行静态检查 |
| 2026-07-22 | Agent Core 首次 Ruff 检查发现 2 个未使用导入 | 删除无用类型导入，并将重试时长改为显式 `timedelta` 导入 |
| 2026-07-22 | 首次 Creative Plan Resume 因 `ResumePayload` 未声明事件中的 `workflow_id` 被 Pydantic 拒绝 | Contract 补充 `workflow_id` 并在 Graph 校验它与当前线程一致；审批事务已安全提交，继续从原 Checkpoint 恢复 |
| 2026-07-22 | Resume 重入时 Human Wait 返回审批后的当前 Workflow 版本，导致与 Interrupt 冻结版本不一致 | Human Step 重入改为返回持久化的 `expected_workflow_version`，保留严格乐观锁语义 |
| 2026-07-22 | 修复 Human Wait 的首个补丁因格式化后的代码行和错误记录上下文不匹配而未应用 | 使用 `rg` 读取真实行后按当前内容重新应用，未产生半写入 |
| 2026-07-22 | 首次服务导入检查发现 `ApiContainer` 前向类型名和 3 个 lambda 工厂导致导入/Ruff 失败 | 使用 postponed annotations 和显式 Unit of Work 工厂函数修正 |
| 2026-07-22 | 新增集成测试首次 Ruff 检查发现 9 个无用/乱序导入和 1 个 lambda 工厂 | 删除无用导入、统一排序并改为显式测试 UoW 工厂 |
| 2026-07-22 | 首轮 26 项测试有 3 项失败：pending writes 断言取错 checkpoint、Outbox 锁 Token 超长、审批测试取到人工 Step | pending writes/审批对象修正测试；Outbox 拆分 `lock_owner` 和 UUID `lock_token` 并新增迁移 |
| 2026-07-22 | Outbox 并发测试修复 Token 后仍无法认领任何刚写入事件 | SQL 诊断发现 MySQL `DATETIME(0)` 将微秒四舍五入到下一秒；所有运行时时间列改为 `DATETIME(6)` 并生成迁移 |
| 2026-07-22 | 宿主机执行 Alembic 时继承容器 DSN `mysql:3306`，DNS 解析失败 | 后续数据库命令显式使用测试夹具已验证的宿主机 DSN `127.0.0.1:13316` |
| 2026-07-22 | Compose 状态检查使用了不存在的 `infra/compose/compose.yaml` | 先枚举 `infra` 下真实文件，再使用仓库实际 Compose 路径 |
| 2026-07-22 | 临时 schema 审计脚本从包根导入未导出的 `load_settings` | 改从 `commercevision_contracts.config` 导入，或直接使用测试 DSN 构造 Settings |
| 2026-07-22 | 新增时间精度测试首次 Ruff 检查发现导入顺序和两个无用导入 | 删除无用导入、按 Ruff 规则排序，并改为直接用 `isinstance(..., UTCDateTime)` 枚举 schema contract |
| 2026-07-22 | 主库迁移后误请求 Scheduler 不存在的 `/health/ready` 得到 404 | 按服务已定义 Contract 使用 `/health/live`；Compose 健康状态和 Scheduler heartbeat 均正常 |
| 2026-07-22 | 最终文档一致性检查发现 Phase 0 路线图仍使用旧 Runbook 显示名称 | 更新为 `Phase 0-1 Runbook`，链接目标未变化 |
| 2026-07-22 | 新 PowerShell 会话未刷新 PATH，`gh` 命令不可见 | 使用已验证的绝对路径 `C:\Program Files\GitHub CLI\gh.exe`，后续再统一刷新 PATH |
| 2026-07-22 | 首次 GitHub CLI 设备授权码因浏览器控件未实际录入字符而无效 | 终止旧 OAuth 等待进程，创建新设备码并使用逐键输入验证页面显示 |
| 2026-07-22 | GitHub CLI OAuth 确认页的授权按钮持续 disabled | 保留当前有效 OAuth 会话并检查页面安全状态；不回退到已暴露 PAT |
| 2026-07-22 | GitHub OAuth Token 缺少 `workflow` scope，服务器拒绝推送 CI 文件 | 创建未暴露的 30 天 classic PAT，仅授予 `repo`、`workflow`、`read:org` 并安全写入 keyring |
| 2026-07-22 | Git 直连 GitHub 多次 connection reset | 验证本机 v2rayN HTTP 代理后，只在当前仓库配置 `127.0.0.1:10809` |
| 2026-07-22 | 首次 Git push 等待 Git Credential Manager 超时 | 配置 `gh auth setup-git`，终止本轮残留 Git 进程并使用非交互凭据重试 |
| 2026-07-22 | classic PAT 首次创建因 Note 重名失败 | 使用唯一 Note 重新创建，未产生额外有效 Token |
| 2026-07-22 | 恢复 Phase 2 时 `.scratch/phase-2-assets-retrieval` 不存在 | 以已提交的 `PLAN.md`、`CONTEXT.md`、ADR-006 和仓库现状为事实来源重建本地规格目录 |
| 2026-07-22 | 首次批量写入 Phase 2 spec 时因 `CONTEXT.md` 匹配文本与实际文件差异导致补丁整体拒绝 | 拆分补丁，先独立创建 spec，再按真实上下文更新领域术语和进度 |
| 2026-07-22 | Phase 2 spec 推送后首次 `gh run list` 直连 GitHub API 超时 | `gh` 不读取仓库级 Git HTTP 代理；改为仅对查询进程设置已验证的本地 HTTPS 代理 |
| 2026-07-23 | Next.js 15.5.21 锁文件更新后首次 `pnpm install --frozen-lockfile` 超过 120 秒 | 保留已验证锁文件，不重复短超时调用；改用更长超时完成确定性安装与审计 |
| 2026-07-23 | Ticket 02 修复 Worker 的 Codex 后端响应流在完成前断开 | 工作树改动完整保留；恢复同一个 Agent ID 和上下文，从现状继续测试、审查与 amend |
| 2026-07-23 | 本地主库已执行 Ticket 02 同 revision 的中间版迁移，最终代码下 `alembic check` 检出列与索引漂移 | 先确认 Ticket 02 新表均为 0 行，再兼容旧 downgrade 并执行受控 downgrade/upgrade；Phase 1 表数据由迁移重新回填 |
| 2026-07-23 | Ticket 02 第四轮修复 Worker 从多 Agent 注册表消失并返回 `not_found` | 保留全部共享工作树改动；创建新的无历史恢复 Worker，仅审计现有 diff、完成门禁并 amend 原 Ticket 提交 |
| 2026-07-24 | Ticket 02 最终提交后的本地主库仍停留在同 revision 中间版 schema，`alembic check` 检出复合 Workspace 索引与外键漂移 | 停止应用写入，完成非事务性 downgrade suffix 并 stamp 父 revision，再由正式 migration 升级；逐表数据量、drift、Phase 0/1 均通过 |
| 2026-07-24 | 原生 `INFORMATION_SCHEMA` 一行 Python 探针被 PowerShell 的嵌套双引号提前解析 | 不重复该引号结构；改用 PowerShell 单引号包裹 Python 程序、Python 双引号包裹 SQL |
| 2026-07-24 | 查询测试 DSN 时假定根目录存在 `tests/conftest.py`，`rg` 报路径不存在 | 改为在 `tests/` 全目录按配置键检索真实夹具位置 |
| 2026-07-24 | 给中间 schema 补兼容索引/唯一约束后 downgrade 在缺失的 `ck_outbox_source_workspace` 处失败；MySQL 已提交此前 DDL | 未原样重试；按 Inspector 结果手工完成剩余 suffix、stamp `9a7e3c1f5b20`，正式 upgrade 后无漂移 |
