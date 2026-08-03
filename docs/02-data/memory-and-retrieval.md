# 记忆与多模态检索

| 属性 | 值 |
|---|---|
| 状态 | decision |
| 最后更新 | 2026-08-03 |
| 适用版本 | Retrieval v1 |

## 记忆分层

### 工作记忆

当前 Workflow 的：

- ProductBrief。
- Creative Plan。
- 已批准约束。
- 工具结果摘要。
- Evaluation 和 Repair 结果。

保存于 LangGraph State，正文引用 MySQL/OSS。

### 语义长期记忆

- 品牌规范。
- 品牌历史素材。
- 同品类优秀素材。
- 商品视觉标签。
- 平台规范。

MySQL 保存业务和权利元数据，Milvus 保存向量。

### 经验记忆

- 人工拒绝原因。
- 常见失败类型。
- 有效 Repair 策略。
- 模型在不同品类的表现。

经验必须经过离线整理和审核后才能进入检索，不能把全部用户反馈直接写入线上记忆。

## Milvus Collection

按 Embedding model family + CommerceVision 内部 pinned epoch 隔离 Collection：

```text
commerce_asset_embedding_{model_family}_{version}
```

字段：

- `milvus_primary_key`（`<embedding_record_id>:g<N>`）
- `embedding_record_id`
- `asset_version_id`
- `workspace_id`
- `category_code`
- `brand_id`
- `asset_role`
- `embedding`
- `input_hash`、`embedding_spec_sha256`、`write_generation`
- `indexed_at_epoch_micros`

权利、有效期和复杂业务过滤仍以 MySQL 为准。

## 索引流程

1. 资产通过安全和权利校验。
2. MySQL 写入资产和 Outbox。
3. Indexer 获取 OSS 临时签名 URL。
4. 在 workspace/retention/provider/region/host 数据出境策略通过后调用 Embedding Provider。
5. 以 generation-specific 主键写 Milvus；unknown outcome 必须做 exact proof，禁止盲重投。
6. 更新 `embedding_records` 并写 completed Outbox。
7. 运行抽样检索验证。

Alibaba mainline model ID 不是不可变快照；内部 pinned epoch 是 collection 发布栅栏。Provider
alias 变化时先 write-disable 旧 collection，再提升 epoch 建新 collection，经 Ticket 16 评测后
切换读取/回填；不同 epoch 不得混写同一 collection。

## 查询流程

### 1. 意图构造

根据 ProductBrief 和 Creative Plan 生成结构化 `RetrievalQuery`：

- 品类。
- 品牌。
- 目标图片角色。
- 视觉属性。
- 禁用项。
- 时间范围。
- 需要的参考类型。

### 2. 硬过滤

MySQL 过滤：

- 工作区。
- 品类/品牌。
- 权利和有效期。
- 允许用途和供应商。
- 资产状态。
- 是否允许派生生成。

该查询先形成唯一的 eligible Asset Version 集合；所有 Dense、FULLTEXT、Brand Profile 与
显式引用通道都只能在该集合内召回。Milvus 过滤表达式超限时按完整 eligible Embedding Record
集合分块查询并全局归并，禁止退化为无过滤 ANN。`candidate_limit` 只限制融合候选池，不能用来
截断 eligible set；MySQL Dense catalog、FULLTEXT 与 Brand Profile 交集也按 1000 个 ID 分块并
全局归并，因此 eligible set 超过单次 `IN`/Milvus 表达式预算时仍保持完整。

### 3. 候选召回

- Milvus Dense 图像/多模态向量。
- MySQL FULLTEXT 标题、标签和人工备注。
- 品牌固定资产。
- 用户本次明确选择的参考图。

### 4. 融合与重排

融合只使用版本化通道排名：

```text
rrf_score(asset) = Σ channel_weight / (rrf_k + channel_rank)
final_score(asset) = rrf_score(asset) + bounded_business_adjustment
```

Cosine、FULLTEXT relevance 等原始分数只进入 Citation 解释，不跨分数空间直接相加。`rrf_k`、
通道权重与业务调整上限必须进入 `retrieval_policy_version`，不能散落在代码中。可选 reranker
只能返回已融合候选的完整排列，不能新增或删除 ID。融合去重后的总候选池必须受
`candidate_limit` 约束；截断时保持融合相对顺序，并优先保留全部必需 Brand Profile 成员。

### 5. 上下文裁剪

- 每类参考限制数量。
- 不发送无关 OCR 全文。
- 展示引用和使用理由。
- 相似素材去重。
- 低置信度结果不自动进入 Prompt。

去重后，将全部待选结果与替补候选一次性提交给 MySQL 做当前权利复核，再按当前 Rights Record
生成 Citation。Milvus 或可选 reranker 不可用时返回显式 degradation，并将
`complete_hybrid=false`；不得以空通道或静默 fallback 冒充完整混合检索。

## Retrieval Run 与受控预览

- `retrieval_runs` 短期保存结构化查询、规范哈希、策略版本、eligible/fused/final 候选计数、
  检索耗时、降级事实与过期时间。
- `retrieval_results` 保存顺序、Asset/Version、Rights Record 版本、通道、RRF 分解、原因与
  MySQL 决策时间；原始预览 token 只返回一次，数据库仅保存 SHA-256。
- 预览交换同时绑定 Workspace、原请求者、Run、Rank 与 30–60 秒 opaque token，并再次查询
  当前 MySQL 权利与精确受控对象。撤权、过期、跨请求者或对象身份变化统一返回不可用。
- 交换后对象引用仍只有 30–60 秒有效；浏览器只接受受支持且不超过 10 MiB 的图片响应，按
  required headers 获取对象并使用短期 Blob URL，不把签名 URL 留在页面标记或持久化状态；
  Blob URL 最迟在引用到期、结果替换或页面卸载时主动撤销。

## 检索评测

每个品类建立带人工相关性标签的 Query Set：

- Recall@K。
- Precision@K。
- MRR。
- nDCG。
- 无权资产召回率必须为 0。
- 检索时延 P50/P95。

同时进行 Agent 下游评测：检索提升是否真正改善 Creative Plan 和最终图片，而不只优化向量指标。

## 故障处理

| 故障 | 行为 |
|---|---|
| Milvus 不可用 | Workflow 进入可解释等待或使用已批准品牌固定资产，不伪装完整检索 |
| Embedding 失败 | 有界重试和 DLQ |
| MySQL/Milvus 不一致 | 以 MySQL 权利状态为准并触发修复 |
| 新模型回填中 | 继续使用旧版本，不能混合不可比较向量 |
| 无合适参考 | Planner 明确标记无参考方案 |

Milvus、Embedding metadata 和任何应用缓存都不是授权源。每次检索或下游 Provider 使用都
必须以 MySQL 当前 Asset、精确 Asset Version 和 `current_rights_record_id` 重新计算可用性；
索引中的历史许可只能帮助收敛或修复，不能把已撤销、到期或管理员阻断的素材恢复为可用。
