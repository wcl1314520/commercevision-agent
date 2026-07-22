# Plan: Phase 2 资产、商品理解与多模态记忆

_Locked via grill-with-docs. Terms follow `CONTEXT.md`._

## Goal

建立一条可公开演示、可私有部署、可恢复且可审计的资产与商品理解链路：用户通过直传提交任务资产或注册基础资产，系统在权利、安全和真实性证据校验后生成可人工确认的商品简报，将有效资产增量写入版本化多模态索引，并通过 Product Catalog/Asset MCP 向 Agent 提供带检索引用的混合检索结果。MySQL 始终是资产、权利和索引状态的事实来源，Milvus 只保存可重建索引。

## Approach

1. **固化领域模型和状态机**
   - 建立 Asset、AssetVersion、UploadSession、RightsRecord、ProductBrief、BrandProfile、EmbeddingRecord、RetrievalPolicy 和 RetrievalCitation。
   - 任务资产与基础资产使用不同保留边界。
   - 资产状态覆盖上传、隔离、校验、待权利、待人工、可用、阻断、删除中、已删除和权利到期。
   - 权利记录版本不可变；新版本替代旧版本，但历史审计仍可追溯。

2. **建立对象存储直传模块**
   - API 创建有过期时间和内容约束的 UploadSession，返回 MinIO/OSS 预签名 PUT。
   - 客户端直传后调用 finalize；服务端通过 HEAD、长度、ETag 和 SHA-256 完成提交确认。
   - 上传对象先进入 quarantine 前缀，未完成校验不能被下载、分析、索引或发送给模型。
   - 同一幂等键只产生一个有效 AssetVersion，重复 finalize 返回同一结果。

3. **建立图片安全、质量与权利校验流水线**
   - 校验扩展名、MIME、文件魔数、完整解码、最大 10 MB、最大 1280x1280、像素炸弹、动画帧和元数据边界。
   - 使用 ClamAV Adapter 扫描恶意载荷；使用阿里云内容安全 Adapter 生成内容风险结论。
   - 提取哈希、EXIF 和可用的来源/内容凭证证据；缺少凭证时只标记“未验证”，不宣称真实或伪造。
   - RightsRecord 必须声明所有者、来源、用途、供应商、可派生条件和有效期；无权或过期资产先在 MySQL 阻断，再异步清理索引和对象。

4. **实现可人工确认的商品理解**
   - VisionAnalyzer 使用版本化 Prompt 和结构化输出生成 ProductBrief。
   - 第一阶段生产 Adapter 使用阿里云百炼视觉模型；模型、Prompt、原始响应引用和字段证据全部可追溯。
   - ProductBrief 包含跨品类公共字段，以及美妆和汽车配件扩展字段。
   - 每个字段保存置信度、证据来源和冲突状态；必填字段低置信度、相互冲突或涉及敏感声明时进入人工确认。
   - Web 和 REST API 均支持查看证据、修改字段、版本化保存和确认，旧版本不能覆盖新版本。

5. **实现 Brand Profile**
   - Brand Profile 聚合当前有效的品牌基础资产、品牌规则、标识、配色、禁用项和文案约束。
   - 每次发布形成不可变版本；Workflow 和 RetrievalCitation 引用具体版本。
   - 删除或权利到期的基础资产立即从下一版本排除，并触发索引修复。

6. **实现版本化增量 Embedding Indexer**
   - Phase 2 默认使用北京地域 `qwen3-vl-embedding`，以配置锁定模型 ID、维度和融合策略。
   - 为图片独立向量和商品图片+受控文本融合向量建立明确 vector kind。
   - MySQL 事务写入 `asset_index_requested` Outbox；Indexer 在事务外调用 Embedding Provider，再幂等 upsert Milvus。
   - Collection 按模型系列、版本和维度隔离；升级采用新 Collection、双写、回填、评测、切换和旧版本退役。
   - 索引任务使用 Lease、Retry、DLQ、对账和恢复调度，不做全量重建式增量更新。

7. **实现权利优先的混合检索模块**
   - MySQL 先按 workspace、品类、品牌、用途、供应商、派生权限、状态和有效期生成硬过滤集合。
   - Milvus 执行多模态 Dense 召回，MySQL FULLTEXT 执行词法召回，品牌固定资产和用户明确选择资产作为受控候选源。
   - 使用版本化 RRF/业务评分融合；可选 `qwen3-vl-rerank` 只重排已通过硬过滤的 Top-N。
   - 返回结果前再次以 MySQL 当前权利状态复核，避免 Milvus 最终一致窗口召回无权资产。
   - RetrievalCitation 包含资产版本、权利版本、检索策略版本、得分分解和使用理由。

8. **实现 Product Catalog/Asset MCP**
   - 提供读取商品、读取 ProductBrief、读取 Brand Profile、搜索资产和获取受控临时引用的工具。
   - MCP 不接受任意 URL、SQL、对象键或文件路径，不暴露 Secret 和原始存储凭据。
   - Tool Gateway 执行 workspace、用途、供应商、预算和权利校验，并复用现有幂等与审计链路。

9. **实现删除、到期和 Milvus 重建**
   - Retention Scanner 对任务资产执行 72 小时到期流程。
   - 管理员删除或权利到期先写 MySQL tombstone 和 Outbox，随后收敛 Milvus、对象存储和缓存。
   - Rebuild Runner 从 MySQL 有效资产和对象存储重建指定 Collection，支持断点、批次、校验和切换。
   - 删除、重建和索引修复均可重复执行且不会恢复已失效资产。

10. **建立运行观测和评测**
    - 为上传、校验、Vision、Embedding、索引、检索、删除和重建建立 OpenTelemetry spans、指标和结构化错误分类。
    - 指标覆盖 quarantine age、validation failures、provider latency/error、index lag、rebuild progress、retrieval P50/P95 和 unauthorized recall。
    - 固定美妆和汽车配件 Query Set，报告 Recall@K、Precision@K、MRR、nDCG、P95 和未授权召回率。
    - 使用故障注入验证 MinIO、Milvus、Provider、RabbitMQ 和 Worker 中断后的恢复行为。

11. **按独立上下文 Ticket 实现**
    - 每个 Ticket 是可演示、可验证的纵向 tracer bullet。
    - 每个 Ticket 在新子 Agent 上下文中执行 TDD、局部门禁、代码审查和独立提交。
    - blockers-first 推进；主上下文只负责规格一致性、提交审查、集成和最终退出验收。

## Key Decisions And Tradeoffs

- 任务资产保存 72 小时，基础资产保存至管理员删除或权利到期，见 [ADR-006](docs/07-decisions/ADR-006-asset-retention-boundary.md)。
- MySQL 是权利和资产状态事实来源；Milvus 的标量字段只能加速，不能成为授权依据。
- 对象先隔离后校验，牺牲少量处理延迟以阻止未验证内容进入模型和索引。
- 采用预签名 PUT 而非 API 中转上传，因为当前单图最大 1280x1280 且不超过 10 MB；UploadSession 接口保留未来 multipart Adapter 的扩展能力。
- Phase 2 使用百炼视觉、Embedding 和可选多模态 Rerank 作为首个生产 Adapter，但 Provider 接口不绑定厂商；跨供应商自动切换仍由 Phase 4 Capability Registry 和 Router 统一实现。
- ProductBrief 人工确认页面提前进入 Phase 2，因为 Phase 2 的退出标准要求低置信度可确认；Phase 3 在此基础上接入 Agent Planning，而不是重新实现确认能力。
- 不使用 Milvus 作为 FULLTEXT 事实源；词法检索保留在 MySQL，Dense 索引可独立重建。
- 不把“未发现生成证据”解释为“真实照片”；真实性只记录可验证证据和检测结论。

## Risks

- 阿里云模型主线别名会演进；每次结果必须记录实际返回模型和配置版本，发布前通过固定评测集。
- 内容安全和 Vision Provider 可能限流或超时；调用必须事务外执行，并使用有界重试、DLQ 和人工处理状态。
- MySQL 与 Milvus 最终一致可能产生短暂陈旧向量；返回前权利复核是不可绕过的安全门。
- 真实检索指标依赖有权且经过相关性标注的数据集；代码 Fixture 只能证明行为，不能替代正式评测集。
- Foundation Asset 版本控制可能增加存储；删除保护不能突破权利到期后的停止使用要求。

## Out Of Scope

- 图片生成、编辑、候选图选择和多供应商生成 Router。
- Creative Plan、执行审批和最终结果终审。
- 视频生成、视频资产处理和完整 PSD 智能分层。
- LoRA 训练；Phase 2 只建立 LoRA 基础资产的注册、权利和对象存储能力，实际模型调用在 Phase 4。
- 完整 ERP 同步和 Amazon 导出；Phase 2 只提供 Product Catalog/Asset MCP 与内部 REST 契约。
- 生产 ACK/RDS/Tair/OSS/Milvus Helm/Terraform 和 99.95% SLO 演练，仍属于 Phase 7。
