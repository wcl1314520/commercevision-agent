# 上线验收标准

| 属性 | 值 |
|---|---|
| 状态 | decision |
| 最后更新 | 2026-07-21 |
| 适用版本 | Release 1.0 |

## 产品

- [ ] 美妆、食品、汽车配件、服装均有合法固定测试集。
- [ ] Amazon 主图、场景图和卖点图可完成。
- [ ] Web、REST API、批量任务和 Webhook 可用。
- [ ] ProductBrief、Creative Plan 和最终结果均可人工审核。
- [ ] 单张候选图可以独立返工。
- [ ] Amazon US 导出含图片、manifest 和校验报告。
- [ ] 公开 Demo 能完成端到端流程。

## Agent

- [ ] 使用显式 LangGraph，不存在无约束自由循环。
- [ ] Agent State、Checkpoint 和 Workflow 状态职责分离。
- [ ] 所有核心输出通过 Pydantic Schema。
- [ ] Tool Gateway 执行权限、参数和预算校验。
- [ ] MCP Server 不暴露数据库、Secret、Shell 或任意文件。
- [ ] Human Interrupt 可以跨进程和发布恢复。
- [ ] Reflection 有次数、预算和重复失败限制。
- [ ] 每次决策可追溯到 Prompt、模型、工具和上下文版本。

## Durable Execution

- [ ] API 返回成功前 Workflow 和 Outbox 已提交 MySQL。
- [ ] Worker 崩溃后任务恢复。
- [ ] 重复消息不产生重复有效结果。
- [ ] 模型调用期间不持有 MySQL 长事务。
- [ ] Provider 未知结果先对账。
- [ ] RabbitMQ 中断后自动补发。
- [ ] Redis 清空不丢失业务状态。
- [ ] MySQL Checkpointer 通过同步和异步 Contract 测试。
- [ ] Checkpoint 不使用不安全 Pickle。

## 检索

- [ ] Milvus 只保存可重建索引。
- [ ] MySQL 权利过滤始终生效。
- [ ] 未授权/过期素材召回率为 0。
- [ ] Embedding 模型升级采用新版本索引。
- [ ] 固定数据集报告 Recall@K、nDCG 和 P95。
- [ ] 检索对下游计划或图片质量有量化提升。

## 模型与工具

- [ ] 至少两个图片供应商。
- [ ] Capability Registry 覆盖尺寸、参考图、地域和限制。
- [ ] 主 Endpoint 故障可切换兼容 Endpoint。
- [ ] 内容安全拒绝不能自动绕过。
- [ ] Provider 调用有 timeout、request ID、成本和错误分类。
- [ ] `NON_RECONCILABLE` Endpoint 不进行危险自动重试。

## Evaluation

- [ ] 四品类各至少 30 个样本。
- [ ] Development、Validation 和 Hidden Test 分离。
- [ ] 确定性、视觉/OCR、模型 Judge 和人工评测并存。
- [ ] Judge 经过人工校准。
- [ ] 无检索/有检索完成对比。
- [ ] 无 Reflection/有 Reflection 完成对比。
- [ ] Prompt/模型新版本通过 Release Gate。
- [ ] 任意历史任务可以生成新 Replay Run。

## 安全

- [ ] Secret 不在代码、镜像、MySQL 明文字段和日志。
- [ ] 管理员开启 MFA。
- [ ] 上传验证 MIME、魔数、解码、像素和大小。
- [ ] SSRF 私网、云元数据和重定向测试被阻止。
- [ ] Prompt Injection 不能扩展工具、权限或供应商。
- [ ] 越权读取 Workflow/Asset 被阻止。
- [ ] Webhook 防伪造和防重放。
- [ ] 公开 Demo 有身份、配额、内容安全和预算限制。
- [ ] 无未豁免 Critical 漏洞。

## 数据

- [ ] 任务数据和 Checkpoint 在 72 小时到期。
- [ ] 品牌/公开 Dataset 不被任务清理器删除。
- [ ] 删除资产后 MySQL、Milvus 和 OSS 一致收敛。
- [ ] MySQL PITR 恢复演练通过。
- [ ] Milvus 备份或全量重建演练通过。
- [ ] 审计不保存完整 Prompt、原图或 Secret。

## 性能与可用性

- [ ] 20 个并发 Workflow 可可靠受理。
- [ ] Workflow 创建 P95 <= 2 秒。
- [ ] 非生成 API P95 <= 500 ms。
- [ ] 300 Workflow/日 Soak 无积压失控。
- [ ] Queue Oldest Age 正常容量下 P95 <= 30 秒。
- [ ] 检索 P95 达到项目设定阈值。
- [ ] 公开 Demo 月可用性达到 99.9%。
- [ ] 企业部署控制面目标达到 99.95%。

## 部署与运维

- [x] 本地一条命令启动。
- [ ] staging 和 demo 使用不可变镜像。
- [ ] Helm/Terraform 可重复部署。
- [ ] API/Worker 多副本和跨节点分布。
- [ ] SLO、Queue、Provider、Agent、MySQL、Milvus Dashboard 可用。
- [ ] 每个关键告警关联 Runbook。
- [ ] 发布、回滚和 Secret 轮换演练通过。
- [ ] staging 连续 14 天无未解决 P1/P2。

## 公开仓库

- [x] Apache-2.0 License。
- [ ] PicsetAI 复用文件保留 MIT 声明。
- [x] 未复制 Fashion-AI 代码。
- [ ] 不提交无权素材。
- [ ] 中文/英文 README。
- [ ] Architecture、ADR、API、Eval 和 Deployment 文档完整。
- [ ] 在线 Demo、演示视频和评测报告可访问。
- [ ] 新开发者可按照文档运行测试和完整流程。

## 阻断项

以下任意存在时不能发布 1.0：

- 只完成模型 API 包装，没有 Agent Graph/评测/回放。
- 人工审批可绕过。
- 工作流依赖进程内内存。
- 没有 MySQL Checkpoint 恢复。
- 没有固定 Evaluation Dataset。
- 无权代码或素材进入仓库。
- Secret 泄露。
- 公开 Demo 可以造成无限模型费用。
- 架构文档与实际代码不一致。
