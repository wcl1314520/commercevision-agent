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
- Ticket 04 已完成独立上下文实现、主控 TDD 修复、两路独立终审和全量本地/远程门禁；实现提交为 `ca1b1d5`。
- Ticket 04 的 monorepo CI 修复作为独立工程提交 `8c15291` 落地，不改动业务行为。
- Ticket 05 已完成独立实现、对抗审查、迁移/运行时数据库身份修复和全部本地/远程门禁；业务提交为 `77e5214`，工程修复为 `dbc8161`，验收 CI 为 `30225320445`。
- Ticket 06 已完成独立实现、对抗审查和全部本地/远程门禁；业务提交为 `2975fcf`，验收 CI 为 `30319058792`。
- Ticket 07 已完成：Vision ProductBrief、原始 Provider Artifact Ledger、Durable HITL、
  Workflow continuation、Web 工作台、运行态 readiness、迁移与生产 Runbook 均通过红绿 TDD、
  多轮三路独立终审和全部本地发布门禁。
- Ticket 07 单一实现提交为 `26245f9`；GitHub Actions 运行 `30482611560` 的 Python、Web、
  Container builds、Gitleaks 与 SBOM 全部通过。
- Ticket 08 本地实现与发布门禁已完成：Brand Profile 草稿、校验、不可变发布版本、历史
  current usability、Rights/Asset 失效收敛、HTTP/Web、MySQL/Alembic、Worker、Runbook 和
  生产配置均已闭合。
- Ticket 08 完整 Python/Web/迁移/容器/安全门禁和 Backend/Ops/Web 三路独立终审均通过，
  无剩余 P0/P1/P2/Required；单一实现提交为 `2b79080`。
- Ticket 08 对应 GitHub Actions 运行 `30596872198` 已全部绿色，正式放行。
- Ticket 09 本地实现与发布门禁已完成：IMAGE 增量索引、Rights/generation fencing、Durable
  recovery/DLQ replay、MySQL 权威状态、Milvus 可重建索引、Embedding Provider、HTTP/Web、
  Alembic、生产配置与 Runbook 均已闭合；Standards、Spec 与 Quality 终审全部批准，无剩余阻断。
- Ticket 09 单一实现提交及对应 GitHub Actions 全绿是进入 Ticket 10 的最终放行门槛；在该门槛
  完成前不启动下一 Ticket。
- Ticket 09 最终单一实现提交为 `73c2194`；GitHub Actions `30786845917` 的 Python、Web、
  Container builds、Gitleaks 与 SBOM 全部通过，Ticket 10 已正式解锁并进入独立上下文实现。
- Ticket 10 的 PRODUCT_FUSED 原子增量索引、受控文本、CJK ngram Search Document、Rights
  撤销/重授权收敛、Worker 事件接线与受 MySQL eligibility 围栏约束的 Milvus ANN primitive 已
  完整落地并通过本地发布门禁：unit/contract `1120 passed, 1 skipped`、PRODUCT_FUSED/Milvus/
  migration `28 passed`、完整 Phase 1 IMAGE MySQL 兼容 `36 passed`。当前只允许形成单一提交、
  推送并等待精确 GitHub Actions 全绿，未启动 Ticket 11。
- Ticket 10 单一提交 `f1d8bf0` 对应 GitHub Actions `30800574595` 已全绿，Ticket 11 正式解锁。
- Ticket 11 已闭合 structured Retrieval Query、MySQL eligible/final rights fence、完整 eligible
  分块 dense、lexical/brand/explicit recall、versioned RRF、受界 rerank/去重、Retained Run、
  30–60 秒 controlled preview、API 与 Web Retrieval Explorer；实现提交 `32d3aff` 与迁移契约
  修复 `dccacec` 已由 GitHub Actions `30813127324` 全绿验证并正式放行。
- Ticket 12 单一提交 `ff9c4b3` 已推送；精确 GitHub Actions `30832780556` 的 Python、Web、
  Container builds、Gitleaks 与 SBOM 全部通过，Ticket 13 已正式解锁。
- Ticket 13 已按冻结工单完成本地实现与发布门禁：资产级 tombstone/deletion generation、复用既有
  Durable Operation 的全版本外部收敛、独立 Scheduler 到期扫描、Provider Artifact 精确版本/未知
  结果穷尽对账，以及不暴露存储细节的 HTTP/Web 进度视图均已闭合；当前只允许形成单一提交、推送并
  等待精确 GitHub Actions 全绿，在此之前不启动 Ticket 14。

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
| 2026-08-03 | Ticket 09 修复前全量 `tests/integration` 运行期间用户中断，进程随后不存在且未产生 pytest 汇总 | 不把无失败输出冒充通过；保留已完成的 28 项 Ticket09 integration 与其它门禁证据，双审修复后重新运行有明确汇总的完整 integration 门禁 |
| 2026-08-03 | Ticket 09 Standards/Spec 双审均 Request Changes，现有绿测未覆盖两个持久化提交边界、DLQ replay、执行中 regrant、delete identity conflict 等生产恢复路径 | 暂停发布与 Ticket10；逐项先在公开 seam 添加 RED，再做最小深模块修复并要求双审复核 |
| 2026-07-31 | Web unit 181 项全绿后 TypeScript 门禁发现 generated `failure_reason` 仍为 optional，presentation helper 只接受 `string | null` | 精确反馈 `index-status-state.ts` 两处调用以 `?? null` 收敛或修正生成契约；ESLint 同次已通过，修复后需重跑 typecheck |
| 2026-07-31 | Ticket 09 主链 195 项 unit 通过后，聚焦 Ruff 检出三个并行编辑产生的 import-order I001 | 不在主实现仍编辑同文件时抢占 auto-fix；已精确反馈三个文件，待语义稳定后执行机械排序并重跑同一门禁 |
| 2026-07-31 | 查找 integration fixture 时先假定根 `tests/conftest.py`，实际 fixture 位于 `tests/integration/conftest.py` | 只读命令未写文件；用 `rg --files -g conftest.py` 定位真实路径后读取，后续复用该 session MySQL fixture |
| 2026-07-31 | Compose 配置复验首次误把 env 文件放在 `infra/compose/.env.example`，实际文件位于仓库根目录 | 命令未写文件；先用 `rg --files` 确认真实路径，再以根 `.env.example` 执行同一 `docker compose config --quiet`，结果通过 |
| 2026-07-31 | UI 无障碍检索首次用 PowerShell 双引号包裹含 `\"` 的复合 `rg` 正则，转义后形成未闭合分组 | 命令未写文件；改用单引号字面正则并拆开 CSS 检索，成功读取状态/重试/aria 接缝 |
| 2026-07-31 | 记录 Milvus 审查结论的首个补丁假定了不存在的 `findings.md` Ticket 标题，context 校验失败且未写入 | 先读取文件真实开头，以现有 Milvus 限制条目为锚点重新应用，审查结论已持久化 |
| 2026-07-31 | 主控检查 Compose 接线时先假定根目录 `docker-compose.yml`，并再次把 `compose*.yml` 作为 Windows 字面路径传给 `rg` | 未改文件；用 `rg --files -g "*compose*"` 定位真实 `infra/compose/docker-compose.yml`，后续只对已验证路径检索 |
| 2026-07-31 | 主控静态复核再次把 `indexing*.py` 作为 Windows `rg` 字面路径，命中已记录的非法 glob 用法 | 未据此作结论；立即改为真实目录加 `-g "indexing*.py"`，后续只使用过滤参数而不向 Windows 传通配路径 |
| 2026-07-31 | Ticket 09 Worker 接线首次聚焦收集失败：`image_indexing.py` 导入了 object-storage 包并未公开的 `ObjectStorage` 名称 | 不通过新增跨层导出掩盖；按 object-storage 现有公开 Port/Protocol 修正类型依赖，再重跑 Worker/settings 聚焦套件 |
| 2026-07-31 | Embedding Provider 首次从根默认 package 运行聚焦测试时无法导入 provider workspace 包 | 改用 `uv run --package commercevision-providers` 的真实 workspace 环境；该次只算测试装置 RED，不虚报为行为 RED |
| 2026-07-31 | Milvus unit 与 integration 测试初版使用相同 basename，在 tests 非 package 布局下全量 pytest 会产生 import-file-mismatch | 不改全仓 pytest import mode；将 integration 文件改为唯一 basename，并在同一 pytest 进程联合收集两者验证 |
| 2026-07-31 | Ticket 09 HTTP route test 首次从根默认 package 运行，collection 阶段找不到 `commercevision_api`；route 与测试又在同一批落下，无法诚实提供 behavior RED | 改用 `uv run --package commercevision-api`，记录本切片无 behavioral RED；保留测试并由独立 Spec review 与真实 MySQL/HTTP 回归补足证据，不虚报 TDD |
| 2026-07-31 | 修正 Rights seed 后真实 MySQL indexing 为 5 pass / 1 fail：Provider facts 已持久化但返回 target 未刷新；随后 stale 断言把 raw SQL JSON 字符串误当对象 | Production mapper 改为同步 generation/provider request/actual model；测试显式解析 raw JSON，聚焦 stale typed-Outbox 已通过并重跑完整范围 |
| 2026-07-31 | Ticket 09 首轮真实 MySQL indexing 测试 6 项全部在 seed 阶段失败：fixture 先封存 Rights Record 再插 permissions，被不可变 trigger 正确拒绝 | 不修改生产栅栏；修正测试 seed 为父记录未封存 → 插 use/provider 子表 → 一次性 seal，再重跑真实 MySQL seam |
| 2026-07-31 | 一次 `rg` 静态门禁搜索向 Windows 传入 `packages/*/pyproject.toml`，该 glob 被解释为非法路径并返回 1 | 已从有效输出确认根配置与 CI 只有 Python Ruff/pytest；后续如需枚举 package 配置使用 `rg ... packages -g pyproject.toml`，不重复非法 glob |
| 2026-07-31 | Ticket 09 实现上下文尝试运行未安装/未配置的 `pyright`，程序不存在 | 不重复不存在的工具，也不为单 Ticket 临时扩依赖；使用仓库与 CI 已配置的 Ruff、测试和现有类型门禁，终审记录实际覆盖 |
| 2026-07-31 | 隔离 Python 3.13 探针导入 `pymilvus==2.4.15` 失败：SDK 使用但未声明 `pkg_resources`，裸环境缺少 setuptools | 不直接写入项目锁文件；用显式 setuptools 做第二个隔离探针，再决定生产 Adapter 的最小兼容依赖并验证 Worker 镜像 |
| 2026-07-31 | Ticket 09 初版 application/persistence 将 Operation target version 绑到可变 Embedding CAS version、reconcile 未提交 MySQL indexed state、eligibility 错用 `milvus` 代替真实 Embedding Provider，且未比较 Rights Record ID | 暂停扩展；分别添加 retry/reconcile、crash-after-upsert、Provider 授权与 Rights identity 的公开 seam RED tests，修复后再进入 Worker/Milvus 纵切 |
| 2026-07-31 | Ticket 09 初版 migration 的 positive-version Check Constraint 引用了尚未创建的 `write_generation`，ORM-only schema 测试未发现，真实 MySQL 会报 3820 | 在继续后续纵切前补齐 generation 的 ORM/DDL/约束，并新增/运行真实 Alembic MySQL roundtrip RED→GREEN |
| 2026-07-31 | `code-review-and-quality` 引用的 `references/security-checklist.md` 与 `references/performance-checklist.md` 未随本地技能安装包提供 | 不重复读取不存在路径；终审使用技能正文已完整定义的 Correctness/Readability/Architecture/Security/Performance 五轴清单 |
| 2026-07-31 | Phase 2 长任务恢复脚本在默认 10 秒工具窗口内超时，未返回恢复报告 | 不重复相同短窗口调用；改用 60 秒有界窗口，并以 Git 状态与现有计划文件交叉确认恢复结果 |
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
| 2026-07-28 | 分段审查 Provider 源码时 PowerShell 将变量后的冒号误解析为变量名组成部分 | 命令未改动文件；后续使用格式化字符串输出标题，不重复该命令形式 |
| 2026-07-28 | 尝试用 `pnpm exec prettier --check` 检查 Ticket 07 Web 文件，但仓库未安装 Prettier | 未改动文件；改用仓库已配置的 ESLint、TypeScript、Vitest 和 Playwright 门禁，不重复不存在的工具 |
| 2026-07-28 | 清理 ProductBrief Workbench 未使用导入的首个补丁因实际导入顺序与预期上下文不一致而拒绝 | 补丁未写入；读取文件真实开头后按当前内容拆分修改 |
| 2026-07-28 | Ticket 07 Python 格式门禁发现 3 个文件未满足 Ruff formatter | 只对 `scripts/generate_web_types.py`、`services/api/.../errors.py` 和 `tests/contract/test_product_brief_openapi.py` 执行 Ruff format，再重跑全仓门禁 |
| 2026-07-28 | Ruff lint 在新增 ProductBrief route 单元测试发现标准库导入顺序错误 | 按 `datetime` 后 `types` 的规范顺序手工修正并重跑 lint |
| 2026-07-29 | 首次全仓 pytest 在 15 分钟工具上限被终止，期间无测试失败且 Python 持续消耗 CPU | 不以超时冒充通过；确认 Docker 依赖全部 healthy，改用 `pytest -q --durations=20` 和 30 分钟上限完成同一全仓范围并输出慢测证据 |
| 2026-07-29 | 超时后的并行进程探针因 `Get-Process python` 已无匹配进程而返回非零 | 这证明超时进程已退出；后续用容错的进程查询或直接启动新的单一全量 pytest，不重复该探针 |
| 2026-07-29 | 30 分钟全仓 pytest 得到 `983 passed / 3 skipped / 1 failed`；旧商品 API 测试仍读取 422 detail 的 `ctx` | 422 安全契约已统一移除 `input` 与 `ctx`，生产实现正确；更新旧公开 HTTP 测试改为断言二者不泄露，再执行聚焦和全量回归 |
| 2026-07-29 | Compose 重建后手工请求 MCP `/health/ready` 得到 404 | 容器本身为 healthy；读取 Compose/服务实际健康 Contract 后使用正确路径复查，不重复不存在的端点 |
| 2026-07-29 | 清理已验证 index 快照的 `Remove-Item -Recurse` 被环境策略拦截 | 命令未执行；保留绝对路径等值与 workspace 边界校验，改用 PowerShell 内的 .NET `Directory.Delete`，不跨 shell 传递路径 |
| 2026-07-29 | .NET 删除 index 快照时遇到 pnpm 依赖中的只读 `eslintrc` 文件，目录只被部分清理 | 仅在已验证快照根内枚举文件并清除只读属性，再由同一 .NET API 完成删除；不触碰仓库或其他 workspace 路径 |
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
| 2026-07-24 | Ticket 04 审查准备时假定前端源码位于 `apps/web/src`，`rg` 返回路径不存在 | 使用 `rg --files apps/web` 枚举实际 App Router 目录，不复用错误路径 |
| 2026-07-25 | PowerShell 将 `foreach` 语句结果直接接管道时报告 empty pipe element | 使用 `$rows = foreach (...) { ... }` 收集对象后再排序，不重复无效语法 |
| 2026-07-25 | Ticket 04 原 Worker 恢复后连续 8 分钟无文件/测试进程进展，强制状态请求仍超时 | 先关闭失联 Agent，保留共享工作树全部实现；启动新的无历史恢复 Worker，仅审计、修复、门禁和提交现有 diff |
| 2026-07-25 | 首次创建 Ticket 04 恢复 Worker 的 JS items 数组漏写末项对象右花括号 | 调用在解析期失败、未创建 Agent；补齐对象边界后重新提交 |
| 2026-07-25 | Ticket 04 恢复 Worker 在 focused/full pytest 与两轮 Playwright 通过后静止 13 分钟，强制状态请求仍无响应 | 关闭失联上下文并保留全部修复；改派窄范围无历史 Finisher，仅做固定 diff 审查、必要修复、最终门禁和显式暂存提交 |
| 2026-07-25 | Ticket 04 窄范围 Finisher 启动后 10 分钟无文件/进程活动，强制状态请求仍无响应 | 关闭失联 Agent；主控接管现有已验证 diff，使用新的只读审查上下文并直接完成修复、门禁和提交 |
| 2026-07-26 | Ticket 05 原 Worker 在恢复后的终态切片连续无响应，关闭并恢复同一 Agent ID 后仍停滞 | 保留共享工作树并关闭失联上下文；主控基于既有红测试完成窄范围审查修复，不新建第二个 Ticket 05 实现上下文 |
| 2026-07-26 | 首轮终态回放测试发现 completed event 的 Trace ID 超过 Outbox `VARCHAR(64)`，MySQL 归一为 `InvalidDataError` | 缩短为 `validation:<operation_uuid>:<attempt>`，回放从 FAILED 成功收敛到 PENDING_RIGHTS |
| 2026-07-26 | 临时数据库探针先后因 PowerShell 引号和错误本地密码失败 | 改用受限 here-string 脚本并读取测试 Fixture 中已定义的宿主机 DSN；不记录或引入新凭证 |
| 2026-07-25 | 本地主库已执行 Ticket 04 同 revision 的中间迁移，最终模型下 `alembic check` 检出 Upload Session 目标对象列与唯一约束漂移 | 以 drift 为反馈环；先审计 Ticket 04 表数据与 downgrade 契约，只在新表为空时受控重建该 revision并复验 |
| 2026-07-25 | 审计中间版 `upload_sessions` 时按最终模型查询 `status` 列，MySQL 报 Unknown column | 不重复最终模型假设；先用 Inspector 枚举实际列，再按中间 schema 查询保留记录 |
| 2026-07-25 | 最初计划从 `asset_objects` 回填 Finalize 目标位置，但现有演示对象仍位于 QUARANTINE 且与源相同 | 放弃不满足最终 CHECK/领域不变量的直接回填；暂停写入面后先复制到 FOUNDATION，再更新对象事实并补列/约束 |
| 2026-07-25 | Ticket 04 的 Standards、Spec、Security 三路只读审查同时因 Codex 后端 response stream 断开失败 | 不重复同一宽任务；保留固定工作树和已通过门禁，由主控先完成五轴审查，再用更窄只读任务重试独立审查 |
| 2026-07-25 | 尝试通过通用 `exec` 嵌套关闭失联审查 Agent 时工具注册表没有对应方法 | 改用已暴露的直接 Agent 关闭工具完成清理；不再通过通用执行器猜测 Agent 工具名 |
| 2026-07-25 | OSS readiness 回归测试首次运行在局部 Fake 类的返回类型注解处触发 `NameError` | 将局部类前向引用改为字符串注解，先排除测试装置错误再验证生产缺陷 |
| 2026-07-25 | 恢复后文件规模审查再次把 PowerShell `foreach` 结果直接接入管道，重复触发 empty pipe element | 执行层改为固定使用 `$rows = foreach (...) { ... }; $rows | ...`；后续批量对象输出不再临时拼接该语法 |
| 2026-07-25 | 领域审查按常见拆分名猜测 `upload_session.py`、`asset.py` 和 `types.py`，但实际模块为 `entities.py/enums.py` | 采用同次目录枚举返回的真实文件名继续审查；后续先用 `rg --files` 再读取新增包 |
| 2026-07-25 | 架构审查读取了不存在的 `packages/providers/README.md` | 以实际存在的 `pyproject.toml` 包描述和 `rg --files` 为边界事实，不再假设每个 workspace package 都有 README |
| 2026-07-25 | 前端代理审查用普通 `Get-Content` 读取含 `[...path]` 的 App Router 路径并猜错测试文件名 | 对方括号路径固定使用 `-LiteralPath`，测试文件先由 `rg --files apps/web/tests` 枚举后读取 |
| 2026-07-25 | 新增 Adapter 测试后用于定位的 `rg` 组合正则转义不完整 | 改用不含嵌套转义的简单函数名模式检查文件边界，不重复该正则 |
| 2026-07-25 | 新增 OSS 缺 Bucket 测试时插入点位于原合约测试尾部之前，第二次幂等删除断言被带入新函数并触发 `NameError` | 将原断言恢复到 OSS 主合约函数，缺 Bucket 用例保持独立；后续补丁后以函数边界和 focused test 双重检查 |
| 2026-07-25 | UUID 边界测试的组合补丁因集成测试断言上下文与实际变量名不一致而整体拒绝 | 确认新文件未创建后拆分补丁，按当前 `body` 变量和真实 `upload-workspace` 夹具插入 |
| 2026-07-25 | 对象存储包迁移首次使用了无内容的 `apply_patch` move hunk，解析器拒绝整个补丁 | 将每个移动与模块 docstring 的实际变更绑定并分批执行，确认无半迁移后继续 |
| 2026-07-25 | 新 object-storage workspace 加入锁文件后直接运行测试，旧 `.venv` 尚未安装其 editable 包，收集期报 `ModuleNotFoundError` | 先执行 `uv sync --locked --all-packages` 同步完整 workspace，再复验迁移后的导入与测试 |
| 2026-07-25 | Ticket 04 三项回归测试转绿后的局部 Ruff 检查发现集成测试导入顺序不符合规则 | 仅对该测试执行 Ruff 导入排序修复，再运行完整格式与静态检查门禁 |
| 2026-07-25 | Ticket 04 的 91 项聚焦测试仅 OpenAPI 提交快照失败 | 严格 UUID Pattern 已改变公开契约；按仓库生成流程重新导出 OpenAPI 和 Web 类型后复验，不手工修改生成物 |
| 2026-07-25 | Ticket 04 全仓 Ruff 门禁发现 `config.py` 格式和 `assets.py` 导入排序两项机械问题 | 使用 Ruff 对这两个文件执行确定性格式/导入修复，并重新导出受影响的 OpenAPI 契约 |
| 2026-07-25 | 宿主机直接执行 Ticket 04 `alembic check` 时再次继承容器 DSN `mysql:3306`，DNS 解析失败 | 按已记录的本地开发契约为该进程显式设置 `127.0.0.1:13316` DSN 后复验，不修改应用默认配置 |
| 2026-07-25 | Windows 上用 `rg apps/web/playwright.config.*` 检查截图配置时通配路径被判为无效文件名 | 先用 `rg --files apps/web` 枚举真实配置文件 `playwright.config.ts`，后续对该字面路径读取 |
| 2026-07-25 | 内置浏览器的当前后端不支持文档中列出的 `networkidle` 等待状态 | 页面导航已成功；改用 DOM 快照和就绪元素作为确定性加载信号，不重复该等待参数 |
| 2026-07-25 | 仓库内未找到可用于真实上传冒烟的 PNG/JPEG/WebP 文件，`rg` 以无匹配状态退出 | 从已测试的 1×1 PNG 固定字节生成系统临时文件，仅用于本地部署上传验证，不写入仓库 |
| 2026-07-25 | 从非文档入口 `http://127.0.0.1:13000` 执行上传时，等待“已进入隔离区”超时且 UI 返回对象存储不可达 | Preflight 证明 MinIO 按安全配置只允许规范 Origin `http://localhost:13000`；切换文档入口后真实 PUT、Finalize、刷新恢复均成功，不扩大默认 CORS 信任域 |
| 2026-07-25 | 首个部署数据诊断脚本调用 `load_settings()` 时遗漏必需的 `service_name` | 使用明确诊断服务名重跑，成功定位最新 Upload Session |
| 2026-07-25 | 诊断脚本猜测幂等表名为 `idempotency_records`，MySQL 返回表不存在 | 使用 SQLAlchemy Inspector 枚举真实 schema，确认表名为 `idempotency_keys` |
| 2026-07-25 | 改用真实幂等表后又猜测响应列为 `response_data`，MySQL 返回列不存在 | 同一 Inspector 输出确认列名 `response_json`，按真实列完成查询；未改动数据库 |
| 2026-07-25 | 部署证据查询沿用领域属性名 `Asset.state`，但持久化列实际为 `assets.status` | 读取 SQLAlchemy 模型后改用真实列名，不再把领域命名直接猜成 schema 命名 |
| 2026-07-25 | 用包含转义引号的组合 `rg` 正则查找 Asset 模型时产生未闭合分组错误 | 改用简单字面模式先定位类和表，再读取精确行段 |
| 2026-07-25 | 部署证据查询猜测 Outbox 存在 `status` 列 | 模型确认 Outbox 以 `published_at` 表示发布状态；查询改为该事实并成功验证 Finalized/Quarantined/Pending/Published 原子链路 |
| 2026-07-25 | 同时追加 `findings.md` 与 `progress.md` 的补丁使用了只存在于进度文件的共同上下文，整体校验失败 | 确认两文件均未写入后，改为分别使用各自真实尾行作为上下文追加 |
| 2026-07-25 | Windows 下把 `packages/application/.../asset_*.py` 作为 `rg` 路径参数导致文件名语法错误 | 改为对真实目录检索并用模式筛选，不再把 shell 通配符当作 Windows 字面路径 |
| 2026-07-25 | 一次组合 `rg` 表达式的引号被 PowerShell 截断，产生未闭合分组错误 | 拆成简单字面模式定位模型类，再读取精确行段 |
| 2026-07-25 | 审查 API 路由时顺带读取了不存在的 `dependencies.py` | 保留已成功读取的真实 `asset_routes.py`，后续先用 `rg --files` 枚举模块，不猜测依赖文件名 |
| 2026-07-25 | 首次持久化清理组合补丁按错误的 Contract 字段名匹配而整体拒绝 | 确认所有目标文件均未发生半写入，读取真实 `asset_version_id` 字段后拆分并重新应用 |
| 2026-07-25 | 首次运行迁移测试时误用无 `mysql` 管理库权限的业务账号，四项测试被跳过 | 改用 Compose 已定义的本地 root 测试 DSN，得到 `cleanup_operation_id` 缺失的有效红测试 |
| 2026-07-25 | Finalize 清理调度首次接线遗漏把 `trace_id` 传入 `_claim`，聚焦集成测试触发 `NameError` | 将请求 Trace 显式加入私有方法签名并沿调用链传递，保持 Outbox 可追踪性 |
| 2026-07-25 | 清理深模块组合补丁按错误的 application 包 docstring 匹配而整体拒绝 | 确认新文件和哈希函数均未写入，读取真实 `__init__.py` 后按当前上下文重新应用 |
| 2026-07-25 | 清理重试集成测试用未来逻辑时钟生成 Recovery Event 后立即按真实时钟消费，被 Inbox 正确判为 `retry-not-ready` | 测试等待 Durable Operation 的已持久化 `next_attempt_at` 边界，再由新 Worker 消费，不篡改生产时钟或重试状态 |
| 2026-07-25 | 对象存储 readiness 首个多文件测试补丁因插入锚点与格式化后的测试尾部不一致而整体拒绝 | 确认未产生半写入后，按 `_S3Client`、`_OssReadResult` 和现有测试函数边界分段加入红测试 |
| 2026-07-25 | 检查对象存储 Adapter 时沿用了不存在的 `minio.py`/`oss.py` 文件名 | 先用 `rg --files packages/object-storage` 枚举真实模块，改读 `object_storage.py` 与 `object_storage_oss.py` |
| 2026-07-25 | 检索集成夹具时顺带传入不存在的根级 `tests/conftest.py` | 使用检索结果确认真实共享夹具位于 `tests/integration/conftest.py`，后续不猜测根级夹具路径 |
| 2026-07-25 | 新增 Durable Cleanup Contract 后聚焦测试检出 committed OpenAPI 漂移 | 使用仓库正式生成脚本重导 `docs/api/openapi.json` 与 Web 类型，不手工修改生成物 |
| 2026-07-25 | 全仓 Ruff 门禁发现 3 个待格式化文件和 3 组导入顺序漂移 | 对精确文件执行 Ruff format/import fix，随后全仓 161 个文件 format/check 通过 |
| 2026-07-25 | 首次把 Vitest 固定为仓库依赖时 pnpm 阻止 `esbuild` 安装脚本并以 1 退出 | 将编译器构建权限显式写入 `allowBuilds.esbuild=true`，冻结锁文件重装后 postinstall 和测试均通过 |
| 2026-07-25 | 全量 pytest 的旧 Worker readiness 断言未包含新内建 `ASSET_DELETION` Executor | 保留运行时正确的内建清理能力，更新集成断言同时证明内建与显式注入 Executor 均已注册 |
| 2026-07-25 | Web 审计发现固定的 Vitest 3.2.4 存在 1 项 Critical 公告 | 不做豁免，升级到同维护线修复版 3.2.6；冻结安装、仓库单测与 `pnpm audit --audit-level=moderate` 均通过 |
| 2026-07-25 | 内置浏览器在刷新后持续显示 Operation “正在读取”，API 日志证明 BFF 转发的 Operation GET 返回 401 | 根因是 Phase 1 Operation API 要求签名 `X-Trusted-Principal`，而 Web BFF 只转发可伪造 Workspace Header；新增服务端 HMAC Principal 签发、Workspace allowlist、浏览器身份覆盖及失败关闭测试 |
| 2026-07-25 | BFF 的直接 Node 测试无法解析 extensionless TypeScript 模块导入 | 服务端 Route 改用显式 `.ts` 扩展，保持 Node `--experimental-strip-types` 测试可直接加载真实模块 |
| 2026-07-25 | 显式 `.ts` 导入首次触发 TypeScript `TS5097` | 在 Web `tsconfig.json` 启用 `allowImportingTsExtensions`，随后 TypeScript 与代理测试均通过 |
| 2026-07-25 | 内置浏览器不支持 `waitForLoadState("networkidle")` | 保留已成功的页面导航，改用短等待、DOM 状态和 API 日志作为真实部署验收信号 |
| 2026-07-25 | Ticket 04 Compose 为暂不消费 Asset Validation 而把 Worker 限制为仅 Workflow 队列，同时误排除 Maintenance 队列 | 新增部署 Contract 红测试，将生产 Worker 边界修正为 `workflow + maintenance`；RabbitMQ 实测两队列各有 1 个 Consumer，Asset 队列保持 0 Consumer 等待 Ticket 05 |
| 2026-07-25 | 本机没有安装 `gitleaks` CLI，无法执行同款本地 Secret Scan | 不临时引入未锁定工具；保留 staged diff 人工凭证检查，并由推送后的既有 GitHub Actions Gitleaks 门禁给出最终证据 |
| 2026-07-25 | Compose 对象存储 Environment Anchor 的首个组合补丁因精确上下文不匹配被拒绝 | 确认未产生半写入，读取实际 API/Worker 段后拆分应用并通过 `docker compose config --quiet` |
| 2026-07-25 | Task 72 小时保留测试的局部补丁造成一次 `IndentationError` | 用完整测试函数替换损坏块，先通过 `py_compile` 再执行红绿测试 |
| 2026-07-25 | 中断 PUT 恢复 E2E 首次使用旧 Session SHA，实际 PNG 与声明校验和不一致 | 以固定 PNG 字节重新计算 SHA-256 和 Base64 Checksum，独立恢复夹具后定向与全量 E2E 均通过 |
| 2026-07-25 | Finalized durable cleanup 首次持久化被旧 MySQL CHECK 拒绝为 `INVALID_DATA` | 保留历史迁移不变，新增向前迁移 `f7a8c2d1e903` 扩展终态约束；主库、测试库 upgrade/current/check 通过 |
| 2026-07-25 | `tests/*.test.mjs` 在 Windows pnpm 脚本中未展开，Vitest 报无测试文件 | 将两个单元测试入口显式列入脚本，避免依赖 Shell glob 行为 |
| 2026-07-25 | 超时单测先推进 fake timer 后才注册 rejection matcher，Vitest 报异步未处理拒绝 | 在推进时钟前创建 rejection 断言，消除测试装置竞态 |
| 2026-07-25 | Node 测试中的 `AbortSignal.timeout()` 使用非保活计时器，测试进程提前结束 | BFF 改用显式 `AbortController + setTimeout`，并将截止时间覆盖到上游响应体读取完成 |
| 2026-07-25 | 文档/环境配置的首个组合补丁因 `.env.example` Actor 默认值猜错而整体拒绝 | 读取真实行后分离环境配置与文档补丁，确认无半写入并通过 Compose 校验 |
| 2026-07-25 | RabbitMQ 管理 API 首次沿用 `guest/guest` 返回 401 | 读取 Compose 的本地开发账号配置后重新查询，确认 Workflow/Maintenance 消费者与 Asset 暂存边界 |
| 2026-07-25 | 终态清理安全修复首次组合文档补丁因实际段落换行不同而整体拒绝 | 确认未产生半写入，分离配置与文档补丁后按真实行段应用 |
| 2026-07-25 | 清理延迟参数首次被补丁锚点误加到 Validation Operation 构造，上传全套出现 20 个同根失败 | 根据首个堆栈定位单一错误调用，移除错误参数并只传给 Cleanup 调度；随后完整上传套件恢复 |
| 2026-07-25 | 清理延迟生效后 3 个旧集成测试仍假定 Outbox 可立即消费，Inbox 正确返回 `retry-not-ready` | 到期测试将逻辑时间推进到 grace 之后；Finalize 测试先断言提前消费被拒绝，再推进事件可用时间验证最终删除 |
| 2026-07-25 | `pip-audit` 在中文 Windows 路径下用 UTF-8 解码本地编码的 `pip --version` 输出而失败 | 为审计进程设置 `PYTHONUTF8=1` 后重新执行，扫描完成且无已知漏洞；不把工具编码异常误报为安全结果 |
| 2026-07-25 | 全仓 Ruff 最终检查发现新增时间边界测试需要机械格式化 | 只格式化该测试文件并重新执行全仓 format/check；业务逻辑与断言未改变 |
| 2026-07-25 | Ticket 04 两路最终复审首次均因 Codex response stream 断开失败 | 关闭失效任务并重新发起只读窄范围复审；替代审查最终均返回 APPROVED |
| 2026-07-25 | 首次读取 Gitleaks Job 日志时 GitHub API TLS handshake 超时 | 保留失败运行不重试相同行为，增加 `GH_HTTP_TIMEOUT=120` 后成功获取日志并定位误报 |
| 2026-07-25 | Ticket 04 首次远程 CI 中 Gitleaks 误报测试幂等键，根目录 Playwright 安装找不到 workspace 二进制 | 使用低熵测试夹具并显式在 `@commercevision/web` workspace 执行 Playwright；新运行 `30177137257` 四个 Job 全绿 |
| 2026-07-26 | Ticket 05 迁移复验误把配置路径写成 `database/alembic.ini`，Alembic 报缺少 `script_location` | 先枚举仓库实际配置，改用根目录 `alembic.ini`；主库与测试库随后均确认在 `e5f8b2d6c914 (head)` 且 `alembic check` 无 drift |
| 2026-07-26 | 查询启动告警的首个 Python 单行命令被 PowerShell 嵌套引号截断并触发 `SyntaxError` | 改用 PowerShell here-string 通过标准输入执行只读 SQLAlchemy 查询，确认两条遗留事件均以终态 Operation + Dead Letter 收敛 |
| 2026-07-26 | 部署冒烟的首次对象核验误把 `ORIGINAL` 隔离源行当作 Promotion 目标，按源 Version ID 查询 Foundation 得到 404 | 枚举同一 Asset Version 的对象角色后确认双对象事实；改按 `CONTROLLED_ORIGINAL` 精确 Version 验证目标，并独立验证 `ORIGINAL` 源已删除 |
| 2026-07-26 | 初次凭证前缀扫描把测试幂等键中的普通子串 `sk-rejected...` 误判为 OpenAI Key 前缀 | 为 `sk-` 增加前置词边界并只输出候选文件；复扫确认无 GitHub、OpenAI、AWS 或阿里云凭证前缀 |
| 2026-07-26 | Ticket 05 静态审查的一组并行只读命令因单个子命令非零而丢失整组结果 | 不重复失败的 fail-fast 组合；后续使用 `Promise.allSettled` 保留各子命令输出并单独诊断失败项 |
| 2026-07-26 | 检查对象条件删除语义时假定了不存在的 MinIO 文件名，并把 PowerShell 通配符直接传给 `rg` | 不重复错误路径；先用 `rg --files packages/object-storage` 枚举真实模块，再按实际文件执行只读检索 |
| 2026-07-26 | 外传策略状态检查再次把可能“无匹配即返回 1”的 `rg` 放入 fail-fast 并行组，重复造成整组输出丢失 | 将本轮后续可空检索强制改为 `Promise.allSettled` 或显式正常化空结果，不再使用 fail-fast `Promise.all` |
| 2026-07-26 | Ticket 05 安全终审发现 Recovery Scanner 直接耗尽 reconciliation 时只提交 Operation/DLQ，Asset 可永久停在 `VALIDATING` | 新增 `TERMINAL_FAILURE` Recovery Generation；真实 Provider 中断、持续不可用、Scanner 到期路径证明 Worker 幂等收敛 Asset 与 typed terminal event |
| 2026-07-26 | 全量真实集成发现测试执行器与生产重启的 Content Safety/C2PA configured identity 不一致 | 测试 Executor 统一从同一 Settings 构造 policy/mapping/trust identity，继续对真实配置轮换失败关闭 |
| 2026-07-26 | MinIO Versioning 并发 conditional copy 可短暂产生两个同内容版本，后到执行器把合法竞态误判为篡改 | MySQL `CONTROLLED_ORIGINAL` 版本作为胜者；事务外复验胜者并按 Version ID + ETag 精确删除重复版本，数据库与存储均只保留一个版本 |
| 2026-07-27 | Windows 再次把 `*.py` 通配路径直接传给 `rg`，产生文件名语法错误 | 改为传入真实目录并由 `rg` 过滤；本轮后续检索均使用字面目录 |
| 2026-07-27 | 损坏版本游标红测试首次遗漏必填 `page_size`，先触发 Pydantic 测试装置错误 | 补齐合法请求其余字段后重跑，确认损坏 Base64 在 MinIO/OSS 两端均映射为 `StoragePreconditionError` |
| 2026-07-27 | 新版分页清理使旧并发测试仍在 `stat` 上等待扫描信号并超时 | 将竞争探针迁到真实 `list_versions` 边界，继续证明首次空扫描后并发 copy 会被后续稳定扫描捕获并删除 |
| 2026-07-27 | 首次全量 pytest 使用过短工具超时被终止 | 使用相同真实 MySQL DSN 和足够窗口完整重跑，最终 `712 passed, 1 skipped` |
| 2026-07-27 | 宿主机 `alembic check` 默认解析 Compose 内部主机名 `mysql` 失败 | 显式使用文档化宿主机 DSN `127.0.0.1:13316`，`upgrade head` 与 `alembic check` 均通过 |
| 2026-07-27 | 最新 Compose 重建后的首个预签名 MinIO PUT 瞬时返回 503 | 确认 MinIO readiness、Bucket 初始化与日志正常；独立 PUT 随后返回 200，并用全新 Session 完成容器级 Validation 冒烟 |
| 2026-07-27 | endpoint 授权升级后的首次全量 pytest 有 1 个外部 URL 时窗测试仍使用 `endpoint=local` fixture | 业务策略按设计在预签名前拒绝；将该外部 Adapter fixture 绑定 allowlisted Alibaba host，先单测再完整重跑 |
| 2026-07-27 | 修复后容器冒烟首次 MinIO PUT 返回 503，而 MinIO readiness 与日志均正常 | 宿主 `httpx` 显式禁用环境代理后同一预签名契约 PUT 返回 200，并完成 Scheduler/RabbitMQ/Celery/ClamAV/Promotion 全链路；不把宿主代理行为归因于业务配置 |
| 2026-07-27 | Ticket 05 远程 CI `30221101083` 在 MySQL 8.4 创建不可变结果 Trigger 时返回 1419，业务账号无 `SUPER`/Trigger 权限 | 不提升运行时账号权限；新增独立迁移 DSN，使用管理员身份执行 Alembic，并在启动/CI 中把 API、Worker、Scheduler 的账号收敛为精确 DML-only 权限 |
| 2026-07-27 | 最终全仓 Ruff 检查发现迁移身份单测的两个第三方导入顺序不符合规则 | 只调整测试导入顺序；随后 Ruff format/check 与完整 `760 passed, 3 skipped` 全部通过 |
| 2026-07-28 | 使用异步 `mysql+aiomysql` DSN 创建同步 SQLAlchemy Engine 触发 `MissingGreenlet` | 只读 schema 探针显式将 driver 改为 `mysql+pymysql`，不修改运行时配置或凭证 |
| 2026-07-28 | Ticket 07 集成测试库停留在同 revision 的中间 schema，17 项用例统一缺少 Provider 配置快照列 | 确认 8 张 ProductBrief 表全部为 0 行后，对明确的 `commercevision_test` 执行受控 downgrade/upgrade；复跑 18 项全绿 |
| 2026-07-28 | Provider 提交意图并发取消红灯按预期失败后，测试线程等待 10 秒释放超时 | 这是首轮 TDD 红灯的预期副作用；实现取消围栏后同一测试快速通过，不重复旧行为 |
| 2026-07-28 | planning-with-files 恢复脚本首次引用了本机不存在的系统 Python 绝对路径 | 未改动工作区；改用仓库锁定的 `uv run python` 成功执行恢复脚本，不重复错误路径 |
| 2026-07-28 | ProductBrief E2E 检索命令使用 PowerShell 双引号包裹含双引号和 `|` 的正则，Shell 将片段误解析为模块名 | 命令未改动文件；后续使用单引号包裹正则或拆分字面检索，不重复该引号结构 |
| 2026-07-28 | ProductBrief 专项 Playwright 首轮 15/16，通过浏览器 DOM 发现运行的是旧 `rows="3"` 工作台而非当前 `rows={4}` 源码 | 按既定发布门禁补执行 production build，再运行隔离 E2E；当前源码下 ProductBrief Playwright `16 passed` |
| 2026-07-28 | OpenAPI 新门禁首次假定内部 Provider `ProductBriefFieldOutput` 会出现在 HTTP components，触发 `KeyError` | 保留 HTTP Response/Revision 组件断言；内部 Provider Output 改由其独立 `model_json_schema()` Contract 验证 |
| 2026-07-28 | 新增 OpenAPI Contract test 通过后，Ruff 先后检出机械格式与本地包导入顺序 | 只对目标测试执行 `ruff format` 和受控 import auto-fix，再分别运行 format/check，业务断言不变 |
| 2026-07-28 | 90 项迁移/Operation 回归中 1 项失败：仅要求 Asset Validation 的 Worker 因注册内置 ProductBrief Executor 而强制探测容器内 Provider Result bucket | 按进程实际 required Operation kinds 收窄 ProductBrief 专属 readiness；新增/保留公开 readiness 回归后重跑完整 90 项 |
| 2026-07-28 | Asset-only readiness 红测试首次在构造 Alibaba Settings 时被缺失出境策略 allowlist 拒绝 | 补齐合法非密策略但继续不配置 API key，使红灯精确命中不应发生的 ProductBrief credential/storage probe |
| 2026-07-28 | 按 required kind 收窄 readiness 后，既有安全回归证明 Alibaba Asset queue 即使将 ProductBrief 标为 optional 也必须验证挂载凭证 | 撤回收窄方向；保留 queue/capability 依赖保证，改造纯注册测试以注入显式 ReadyStorage，隔离宿主机 DNS |
| 2026-07-28 | Ticket 07 全仓 Ruff 门禁检出 9 个并行修复文件需机械格式化，并检出 ProductBrief Contract import 顺序 | 仅对列出文件执行 formatter/受控 import auto-fix，再运行全仓 format/check；不改变业务语义 |
| 2026-07-28 | 全工作树凭证前缀扫描命中 Ticket 07 未修改的既有上传测试假值 | 不输出候选内容；改为扫描固定基线后的新增行和全部 untracked 文件，由远程 Gitleaks 再做最终全仓门禁 |
| 2026-07-28 | 容器 smoke 首次尝试读取生产 API `/openapi.json`，部署配置按安全策略返回 404，PowerShell 仍继续输出空枚举 | 不把禁用 OpenAPI 误报为路由缺失；改用未认证 ProductBrief 请求的稳定 401 验证路由装载 |
| 2026-07-28 | 首个未认证 ProductBrief smoke 未提供必填 Workspace header，先命中请求校验并返回 422 | 补齐合法 Workspace scope 但不提供签名 principal，验证认证边界返回稳定 401 |
| 2026-07-28 | 检查 OpenAPI schema 的 PowerShell `foreach` 结果直接接管道产生空管道解析错误 | 命令未改动文件；先将循环结果赋给变量再输出，成功读取目标 schema |
| 2026-07-28 | 检索不存在的 `packages/contracts/.../workflows.py` 使组合 `rg` 返回非零 | 已确认实际文件为 `workflow.py`；后续使用仓库 `rg --files`/真实路径，不重复错误文件名 |
| 2026-07-28 | 第三轮审查修复的 Provider、原子性、Event、422 和 Retention 测试按 TDD 首轮预期失败 | 每项均在公开接缝复现审查故障后实施定向修复，当前对应聚焦套件全部转绿 |
| 2026-07-29 | 第四轮修复后首次 Web TypeScript 门禁找不到新增 Workflow 状态类型 `WorkflowStatus` | 将该 schema 加入正式生成器导出集合，重新生成 OpenAPI 客户端并重跑 lint、typecheck、unit、proxy、build 与 Playwright，全部通过 |
| 2026-07-29 | 第四轮修复后的首次全仓 pytest 使用 1 秒工具超时窗口，宿主在约 5 秒后终止命令 | 确认无残留 pytest 进程；改用隐藏后台进程与日志轮询，在不牺牲完整运行窗口的情况下持续报告进度 |
| 2026-07-29 | Windows 工具宿主在启动命令结束时回收 `Start-Process` 子进程，后台 pytest 只运行到约 7% | 不再依赖脱离式子进程；改用可 yield 的长运行工具单元保持进程所有权，并通过 wait 快照报告进度 |
| 2026-07-29 | 后台 pytest 的 `uv` 父进程不可见但 Python 子进程继续到 06:30，和 06:10 启动的全量套件并发 `TRUNCATE` 同一测试库，造成两边分别 82/97 个集中集成失败 | 通过持续增长的独立日志时间戳、两组互补失败和无单测/Contract 扩散确认并发污染；等待两进程完全退出、确认测试库无事务后，只保留一个可追踪前台全量进程 |
| 2026-07-29 | 首失败诊断使用 15 分钟窗口，在未复现任何失败时被工具超时终止 | 不再用短窗口诊断完整套件；最终干净全量使用 33 分钟窗口，超过此前约 23 分钟最慢运行 |
| 2026-07-29 | 首次高置信凭证扫描把 tracked diff 与 untracked 输出包装成两个嵌套数组，只统计到 2 个元素 | 不采用该结果；改用 PowerShell 数组语句逐行收集并去重，再对实际全部变更文件扫描 |
| 2026-07-29 | 并行 worker 进度探针中一个 `rg` 暂无匹配并返回 1，导致 `Promise.all` 丢弃其他只读结果 | 未修改文件；改用 `Promise.allSettled` 分别保留 ledger 与 pre-analysis 探针结果 |

## Ticket 07 第十轮后端修复计划

### B10-1：Recovery 权威时间
**Status:** complete
- 公开 seam：真实 MySQL `RecoveryService.recover_once`
- RED：证明 Worker 时钟漂移不能提前回收 MySQL 仍有效的 Lease，也不能错误判定 ProductBrief retention
- GREEN：同一 UoW 的 `database_now()` 驱动 lease query、retention、stale threshold 与 recovery event 时间

### B10-2：Recovery 调度去重
**Status:** complete
- 公开 seam：真实 MySQL Recovery Outbox
- RED：已发布未消费的 recovery event 不得因 Workflow 仍 stale 而重复生成
- GREEN：调度 event 时持久化不提升业务版本的 recovery observation/fence

### B10-3：Commerce no-generation 恢复
**Status:** complete
- 公开 seam：真实 Worker/Event + MySQL
- RED：pre-ProductBrief 与 confirmed-before-first-claim 两条路径均不得 retry/DLQ
- GREEN：恢复事件携带足够的当前权威事实，且 Graph 仍拒绝任意 unsafe initial entry

### B10-4：命令与 Provider 提交绑定
**Status:** complete
- 公开 seam：ProductBrief HTTP/Service 与 Durable Provider Executor
- RED：revise、confirm 和 Provider submission 在 Workflow type/product/retention 漂移后失败关闭
- GREEN：锁内使用 MySQL 时间统一验证 Workflow/Product/ProductBrief binding 与 retention

### B10-5：Checkpoint 不持久化 Lease Token
**Status:** complete
- 公开 seam：Agent Runtime + MySQL/InMemory Checkpointer
- RED：START、pending writes 和历史 checkpoint 中均不可解码出 live lease token
- GREEN：preclaim authority 通过运行时私有、非 checkpoint 通道交付；DB-ahead/checkpoint-behind 与 lease-expiry crash recovery 保持可恢复

### B10-6：完整门禁
**Status:** complete
- ProductBrief HITL、Reliability MySQL、Agent Graph/Runtime、Contracts
- Ruff format/check 与基线 diff-check
