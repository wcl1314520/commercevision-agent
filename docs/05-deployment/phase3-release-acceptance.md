# Phase 3 发布验收

| 属性 | 值 |
|---|---|
| 状态 | verified |
| 最后更新 | 2026-08-06 |
| 适用版本 | Phase 3 / 0.3.0 |

## 目标与权威入口

Phase 3 的发布判定由版本化清单 `evaluation/phase3/release-v1/manifest.json` 驱动，不依赖人工勾选。
清单冻结浏览器路径、故障注入、恢复不变量、迁移、授权安全、Planner 安全、CI 门禁和 public-demo/private
边界。审计器只读取仓库内普通 UTF-8 文件，拒绝重复 JSON key、路径穿越、符号链接逃逸、缺失 anchor、
超限输入和不完整的精确集合。

```powershell
uv run commercevision-phase3-acceptance `
  --manifest evaluation/phase3/release-v1/manifest.json `
  --repository-root . `
  --json-output .artifacts/acceptance/phase3-release.json `
  --markdown-output .artifacts/acceptance/phase3-release.md
```

退出码 `0` 表示通过，`2` 表示输入或证据被拒绝。报告只保留固定 gate identity、manifest/evidence digest
和 public-demo 边界数量；不会输出 Workspace、Prompt revision、cursor signing scope、credential、Plan、
Approval、Citation 或隐藏数据路径。

## 浏览器与控制面证明

Playwright 覆盖 Creative Plan provenance/history、不可变用户修订、exact-version approve、reject conflict、
刷新后草稿恢复、SSE 从最后已交付 cursor 重连、Policy denial 和 retention expiry。`409` 只刷新权威事实并
保留输入，不自动重放批准或驳回；`403/410` 会撤下授权操作面。

服务端真实 MySQL 测试证明 stale 页面不能批准或覆盖新 head，未批准/已驳回/过期/伪造/跨 Workspace Plan
不能形成 execution claim。Tool Policy 从服务端 Registry、Rights、resource、provider、quota 和 budget
派生 authority；Prompt Injection 只作为数据，不能增加工具、权限、Provider、资源或预算。

## 故障注入与恢复不变量

| 故障边界 | 发布证明 |
|---|---|
| Worker commit/replay | Plan/Approval 的提交事实来自 MySQL；重复 delivery 或崩溃恢复不重复版本和审批 |
| RabbitMQ | Outbox/Inbox、marker、lease recovery 让同一 logical command 最终收敛 |
| MySQL reconnect | production engine 使用 pre-ping/recycle；事务失败回滚，重投从持久 authority 继续 |
| Checkpointer restart | LangGraph 只恢复 execution state；resume 前重新验证 exact MySQL Plan/Approval |
| SSE disconnect | 每事件持久 keyset cursor，短事务重连，不创建业务命令或延长 retention |
| Evaluation interruption | fixture/observation hash 固定，报告原子替换且可重算验证，不接受半写 artifact |

所有恢复必须同时证明：Plan version 唯一、Approval 唯一、无 stale authorization、未授权 Tool Intent 为零、
retention 不延长、最终状态收敛。无法证明外部副作用结果时不得猜测成功。

## 迁移与数据边界

发布套件覆盖空库 base→head、完整历史链 downgrade/re-upgrade、Phase 2→3 表结构、Alembic drift、tenant-first
复合键、immutable Prompt/Planning Context/Creative Plan/Approval facts 和所有 runtime `DATETIME(6)`。
有不可表示历史数据的 destructive downgrade 必须在 DDL 前失败关闭。

## Public Demo 隔离

`infra/public-demo/phase3.env.example` 是独立部署 profile，只允许 `catalog-demo`，管理员集合为空，使用四个
`public-demo-*` bucket、独立对象前缀和 OIDC credential scope。Planner 使用公开清单中固定的
`public-demo-planner-r1`，Planner dataset 为许可明确的 `planner-ci-v1`；private Prompt revision、hidden
release dataset 和 cursor signing scope 均不得重叠。

Creative Plan 与 Workflow event cursor 最长 900 秒；Tool Intent 只允许 `low` cost class 且共享 quota 为 8。
这些 cursor 是恢复位置而不是授权事实。任何真实 Provider transfer 默认关闭；公开 Demo 使用 deterministic/
fixture adapter，不读取 private 配置或静态密钥。

Profile 固定独立的 `public-demo-phase3-current` key ID，但不提交 HMAC secret。部署平台必须通过受管 Secret
注入 `CV_TRUSTED_PRINCIPAL_CURRENT_HMAC_SECRET`；缺失或仍为 Compose 本地默认值时，API 在
`CV_ENVIRONMENT=production` 下启动失败，Web 也无法签发 Principal，不能降级到开发密钥。

```powershell
docker compose --env-file infra/public-demo/phase3.env.example `
  -f infra/compose/docker-compose.yml config --quiet
```

## 发布门禁与最终审批

同一最终 Git SHA 必须通过 Python、Web、OpenAPI、真实 MySQL、LangGraph、SSE、Playwright、Planner Eval、
安全/Secret、Python/Node dependency、全部服务容器、License 和 SPDX SBOM。CI 上传 aggregate-only
`phase3-release-acceptance`、Planner evaluation 和 SBOM artifact。

最终记录至少包含 SHA、GitHub Actions run、Phase 3 聚合报告 digest、Planner report digest、迁移 head 和已知
问题。hidden release 输入始终在 Git 外；Node action runtime deprecation 等非失败 annotation 需要记录但不能
替代门禁结论。
