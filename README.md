# CommerceVision Agent

评测驱动、可回放、可人工干预的多模态电商视觉创意 Agent。

## 项目定位

CommerceVision Agent 面向电商美工和视觉运营人员，将商品理解、历史素材检索、创意规划、图片生成、自动评测、反思修正、人工审核和平台导出组织成一条可恢复的 Agent 工作流。

这个项目的目标不是包装第三方生图 API，也不是复刻一个通用 Agent Builder，而是公开展示完整的 Agent 应用工程能力：

- 结构化 Planning 和受控 Tool Calling。
- 多模态 RAG、品牌记忆和历史经验检索。
- Human-in-the-loop interrupt/resume。
- Evaluator、Reflection、Repair 和任务回放。
- 模型路由、超时、幂等、重试和故障恢复。
- MySQL 业务状态、Milvus 向量检索、OSS 对象存储。
- 可观测、可测试、可部署、可公开演示。

## 核心工作流

```text
商品与品牌素材接入
  -> 多模态商品理解
  -> 历史优秀素材检索
  -> 结构化创意方案
  -> 人工方案审核
  -> 模型与工具执行
  -> 自动质量评测
  -> 反思与局部修正
  -> 人工结果终审
  -> Amazon 规范化导出
```

## 架构原则

1. 单一编排 Agent，不用多 Agent 数量制造复杂度。
2. Agent 决策与确定性业务规则分离。
3. MySQL 是业务和工作流状态的事实来源。
4. Milvus 只负责向量检索，不保存业务真相。
5. 所有长任务可恢复、可取消、可回放。
6. 每个模型、Prompt、工具和路由版本均可追溯。
7. 自动评测不能替代人工终审。
8. 每个阶段都形成最终系统的一部分，不建设一次性 MVP。

## 文档入口

- [项目文档总索引](docs/README.md)
- [产品定义与边界](docs/00-product/product-definition.md)
- [用户与 Agent 工作流](docs/00-product/user-workflows.md)
- [系统架构](docs/01-architecture/system-architecture.md)
- [Agent Runtime](docs/01-architecture/agent-runtime.md)
- [数据架构](docs/02-data/data-architecture.md)
- [评测与回放](docs/03-ai/evaluation-and-replay.md)
- [实施路线](docs/06-roadmap/implementation-roadmap.md)
- [上线验收标准](docs/06-roadmap/acceptance-criteria.md)

## Phase 0-1 快速启动

前提：

- Docker Desktop 或 Docker Engine + Compose v2。
- Python 3.13 和 `uv`。
- Docker 建议分配至少 8 GB 内存。

PowerShell：

```powershell
.\scripts\dev.ps1 up
```

Bash：

```bash
./scripts/dev.sh up
```

该命令会构建镜像、等待所有服务健康，并运行主机侧完整验收。

主要入口：

| 服务 | 地址 |
|---|---|
| Web 工程控制面 | `http://localhost:13000` |
| Control API | `http://localhost:18000` |
| API 文档 | `http://localhost:18000/api/v1/docs` |
| RabbitMQ 管理台 | `http://localhost:25672` |
| MinIO Console | `http://localhost:19001` |

完整端口、迁移、配置和故障排查见 [本地开发 Runbook](docs/05-deployment/local-development.md)。

## 使用 Obsidian 阅读

项目根目录已配置为 Obsidian Vault：

1. 在 Obsidian 中选择“打开本地仓库文件夹”。
2. 选择 `D:\个人项目\电商生图agent\mine`。
3. 从 `README.md` 或 `docs/README.md` 进入文档体系。

Vault 使用标准相对 Markdown 链接，GitHub 和 Obsidian 可以读取同一套文档。模板目录已设置为 `docs/templates`，个人窗口布局不会提交到 Git。

## 当前状态

`Phase 1 / 0.1.0` 已完成并通过本地验收：

- Python workspace、Next.js App 和共享 Contract 边界已建立。
- FastAPI、Celery Worker、Scheduler、MCP Server 均有独立入口和健康检查。
- MySQL、Valkey（Redis 协议兼容）、RabbitMQ、MinIO、Milvus、etcd、OpenTelemetry 可一条命令启动。
- Workflow/Step/Attempt 状态机、乐观锁和 72 小时保留边界已落库。
- 事务 Outbox、Inbox 去重、Lease、Retry、DLQ 和 Recovery Scheduler 已打通。
- 自定义 MySQL LangGraph Checkpointer 支持同步/异步、pending writes、Interrupt/Resume 和线程级删除。
- 11 张运行时表的 33 个 UTC 时间列统一使用 MySQL `DATETIME(6)`，并有 Alembic 精度漂移门禁。
- Fixture Agent 已通过两次人工审批、重复消息、Worker 重启和持久 Checkpoint 恢复验收。
- Ruff、pytest、ESLint、TypeScript、Next build、OpenAPI drift、Secret Scan 和 SBOM 已进入 CI。
- Python 与 Node 依赖漏洞审计已进入 CI，当前锁文件本地审计无已知漏洞。
- Python 应用镜像使用非 root 用户，服务 readiness 覆盖所有必要依赖。

当前不包含真实模型、生图 Provider、多模态资产检索和完整产品工作台；这些能力按 Phase 2-6 路线继续实现。

## 来源项目

- Open PicsetAI：仅在遵守 MIT License 和保留版权声明的前提下复用适合的交互与实现。
- Fashion-AI：因仓库未提供明确许可证，只借鉴混合检索和参考图分析思想，不复制代码。

详细边界见 [来源项目吸收策略](docs/08-research/source-adoption.md)。

## License

原创代码采用 [Apache License 2.0](LICENSE)。第三方边界见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
