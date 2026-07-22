# Prompt、Context 与 Guardrails

| 属性 | 值 |
|---|---|
| 状态 | decision |
| 最后更新 | 2026-07-21 |
| 适用版本 | Prompt System v1 |

## Prompt Registry

Prompt 作为正式配置资产管理：

- `prompt_id`。
- 语义版本。
- Agent 节点。
- 适用品类和模型家族。
- 输入/输出 Schema。
- 系统政策版本。
- 模板正文引用。
- 创建人、审核人和变更说明。
- 评测集结果。
- 发布状态。

状态：

```text
DRAFT -> REVIEW -> STAGING -> PRODUCTION -> DEPRECATED
```

生产 Prompt 不允许原地修改。Workflow 创建时保存所用版本快照。

## ContextBuilder

每个节点定义独立上下文配方：

```text
system policy
node objective
approved product constraints
approved brand constraints
retrieved evidence
necessary recent tool results
output schema
```

ContextBuilder 负责：

- Token/图片预算。
- 字段优先级。
- 摘要和去重。
- 引用编号。
- 数据来源标记。
- 敏感字段脱敏。
- 模型格式适配。

## 不可信输入

以下全部视为数据，不是指令：

- ERP 商品描述。
- 图片中的 OCR 文字。
- 参考素材说明。
- 用户上传文件名。
- 供应商响应。
- MCP Tool 返回文本。
- 网页和外部 API 内容。

模型不能根据这些内容：

- 增加工具。
- 修改权限。
- 更换供应商密钥。
- 访问任意 URL。
- 跳过人工审核。
- 执行代码。

## 结构化输出

核心输出：

- `ProductBrief`。
- `RetrievalQuery`。
- `CreativePlan`。
- `ToolIntent`。
- `EvaluationInterpretation`。
- `RepairPlan`。

处理顺序：

1. 使用供应商结构化输出能力。
2. Pydantic 验证。
3. 执行业务字段白名单。
4. 校验资源 ID 和枚举。
5. Schema 错误允许有限修复。
6. 仍失败则停止节点并记录原始响应引用。

禁止从任意混合文本中宽松提取 JSON 后直接执行工具。

## Guardrail 层次

### 输入 Guardrail

- 文件和 MIME 验证。
- 内容安全。
- 素材权利。
- PII/人脸标记。
- Prompt 长度和字符限制。

### 规划 Guardrail

- 工具白名单。
- 图片角色和尺寸。
- 商品保护约束。
- 最大候选数。
- 预算和重试策略。

### 执行 Guardrail

- 审批版本。
- 幂等键。
- Provider 能力。
- URL/路径解析。
- 超时和响应大小。

### 输出 Guardrail

- 文件可解码。
- 内容安全。
- OCR 和 Logo。
- 商品一致性。
- 平台规则。
- 人工终审。

## 模型输入记录

为了 Trace 和数据最小化同时成立：

- 在线日志保存 Prompt 哈希、版本和脱敏摘要。
- 完整任务 Prompt 加密保存于 72 小时 Task Bucket。
- 固定评测集 Prompt 可以长期版本化。
- Secret、签名 URL 和内部 Token 永不进入 Prompt。

## Prompt 测试

- Schema Snapshot。
- Prompt 注入用例。
- 极长 OCR 和恶意商品描述。
- 多语言商品。
- 空检索结果。
- 冲突品牌规则。
- 模型返回额外字段。
- 工具 ID/资产 ID 伪造。

## Context 评测

不仅比较模型输出，还比较：

- 输入 Token/图片数量。
- 关键约束召回率。
- 无关上下文比例。
- 引用正确率。
- Prompt 注入成功率。
- 计划质量和最终图片质量。

