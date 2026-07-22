# CommerceVision Agent 立项、架构与实施计划

## 本轮目标

在 `D:\个人项目\电商生图agent\mine` 中建立面向公开 GitHub、在线 Demo 和 Agent 应用开发求职的完整项目架构，并逐阶段实现可上线系统。当前实施 Phase 1：领域状态与 Durable Agent Runtime，不进入真实模型、生图、资产检索和完整产品 UI。

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
- GitHub Actions 工作流已配置并完成对应本地门禁验证，远程 CI 需在仓库首次 push 后取得运行证据。

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
