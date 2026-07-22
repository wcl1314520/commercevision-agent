# 评测、反思与回放

| 属性 | 值 |
|---|---|
| 状态 | decision |
| 最后更新 | 2026-07-21 |
| 适用版本 | Evaluation v1 |

## 原则

评测系统不是上线后的附属 Dashboard，而是 Agent 开发主循环：

```text
Dataset
  -> Run
  -> Trace
  -> Evaluators
  -> Human Labels
  -> Bad Case Cluster
  -> Prompt/Model/Tool Change
  -> Replay
  -> Compare
  -> Release Gate
```

## 数据集

公开发布前建立至少四个品类的数据：

| 品类 | 最低数量 | 重点 |
|---|---:|---|
| 美妆 | 30 | 包装、Logo、材质、文字 |
| 食品 | 30 | 包装文字、食物真实性、场景 |
| 汽车配件 | 30 | 结构、接口、比例、方向 |
| 服装 | 30 | 材质、版型、图案和试穿 |

数据集分为：

- Development：用于 Prompt 和实现迭代。
- Validation：用于阶段验收。
- Hidden Test：发布前运行，避免过拟合。

## Evaluator 类型

### 确定性

- 文件解码。
- 尺寸、比例、颜色空间。
- 背景颜色。
- 水印/边框。
- Amazon 命名和角色。
- Workflow 状态和审批。

### 视觉与 OCR

- 商品图像相似度。
- Logo 区域相似度。
- OCR 文本差异。
- 颜色偏差。
- 关键结构检测。

### 模型 Judge

- Creative Plan 遵循度。
- 构图和视觉质量。
- 商品真实性。
- 品牌一致性。
- 失败根因分类。

模型 Judge 必须：

- 锁定模型和 Prompt 版本。
- 使用评分 Rubric。
- 通过人工样本校准。
- 报告置信度。
- 不能作为唯一安全判断。

### 人工

- 方案是否可执行。
- 首轮通过/拒绝。
- 返工原因。
- 最终可用性。
- Judge 错误标记。

## 指标

### Agent

- 计划 Schema 通过率。
- 有效工具调用率。
- 平均节点数。
- 无效循环率。
- Checkpoint 恢复率。
- Reflection 成功率。

### 检索

- Recall@K。
- nDCG。
- 无权资产召回率。
- 检索后计划/图片提升。

### 图片

- 商品一致性。
- Logo/文字正确率。
- 人工首轮通过率。
- 平均返工次数。
- Amazon 规则通过率。

### 工程

- 任务最终成功率。
- P50/P95 总时延。
- 队列等待时间。
- 单任务成本。
- Provider 错误和切换率。

## Reflection

`RepairPlan` 必须包含：

- 失败的 Evaluator。
- 证据引用。
- 根因分类。
- 是否可自动修正。
- 修改目标。
- 保持不变的字段。
- 建议模型/工具。
- 预期改善指标。

禁止使用“质量不好，请重试”这种无结构反思。

## Replay

Replay 输入：

- 原始 Workflow 输入引用。
- Agent Checkpoint。
- Prompt、模型、工具和检索策略版本。
- 固定供应商响应或真实沙箱。
- 人工审批快照。

Replay 模式：

| 模式 | 用途 |
|---|---|
| Deterministic Fixture | 单元和 CI |
| Provider Sandbox | 集成验证 |
| Offline Real Model | Prompt/模型实验 |
| Production Shadow | 新版本影子评测 |

Replay 产生新 Run，不覆盖旧 Trace。

## 实验设计

每次改变 Prompt、模型、检索或 Evaluator：

1. 写假设。
2. 选择固定 Dataset。
3. 记录 Baseline。
4. 只改变一个主要变量。
5. 运行质量、成本和时延对比。
6. 检查分品类退化。
7. 人工复核随机样本和最大差异样本。
8. 通过 Release Gate 才能发布。

## 首批必须完成的对比

- 无检索 vs 混合检索。
- 无 Reflection vs Reflection。
- 固定模型 vs 能力路由。
- Prompt v1 vs v2。
- 单一 Judge vs 规则 + Judge + 人工校准。

## 发布门

新版本必须满足：

- 关键安全指标不下降。
- Hidden Test 无显著退化。
- 至少一个核心业务指标改善，或在质量持平时显著降低成本/时延。
- 无新增无限循环和无效工具调用。
- Trace 和版本信息完整。

