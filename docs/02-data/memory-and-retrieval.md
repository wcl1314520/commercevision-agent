# 记忆与多模态检索

| 属性 | 值 |
|---|---|
| 状态 | decision |
| 最后更新 | 2026-07-21 |
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

建议按 Embedding 模型版本隔离 Collection：

```text
commerce_asset_embedding_{model_family}_{version}
```

字段：

- `vector_id`
- `asset_id`
- `workspace_id`
- `category_code`
- `brand_id`
- `asset_role`
- `embedding`
- `created_at`

权利、有效期和复杂业务过滤仍以 MySQL 为准。

## 索引流程

1. 资产通过安全和权利校验。
2. MySQL 写入资产和 Outbox。
3. Indexer 获取 OSS 临时签名 URL。
4. 调用版本锁定的 Embedding 模型。
5. 写 Milvus。
6. 更新 `asset_embeddings`。
7. 运行抽样检索验证。

更新 Embedding 模型时建立新 Collection，完成双写、回填、评测和切换，不覆盖旧向量。

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

### 3. 候选召回

- Milvus Dense 图像/多模态向量。
- MySQL FULLTEXT 标题、标签和人工备注。
- 品牌固定资产。
- 用户本次明确选择的参考图。

### 4. 融合与重排

初始策略：

```text
final_score =
  dense_similarity
  + lexical_rank
  + business_performance
  + human_quality
  + freshness
```

实际使用 RRF 或版本化加权策略。权重必须进入 `retrieval_policy_version`，不能散落在代码中。

### 5. 上下文裁剪

- 每类参考限制数量。
- 不发送无关 OCR 全文。
- 展示引用和使用理由。
- 相似素材去重。
- 低置信度结果不自动进入 Prompt。

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

