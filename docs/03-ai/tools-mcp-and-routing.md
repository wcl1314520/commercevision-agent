# 工具、MCP 与模型路由

| 属性 | 值 |
|---|---|
| 状态 | decision |
| 最后更新 | 2026-07-21 |
| 适用版本 | Tool Runtime v1 |

## Tool Registry

所有工具由服务端静态注册：

| 工具 | 类型 | Agent 是否可直接选择 |
|---|---|---|
| `analyze_product` | 模型工具 | 是 |
| `search_reference_assets` | MCP/检索工具 | 是 |
| `get_brand_guidelines` | MCP 资源工具 | 是 |
| `generate_image` | 长任务工具 | 是，但需要已批准计划 |
| `edit_image` | 长任务工具 | 是，但限制编辑区域 |
| `run_ocr` | 确定性/模型工具 | 是 |
| `evaluate_candidate` | 评测套件 | 由 Graph 固定调用 |
| `create_export_manifest` | MCP/确定性工具 | 由 Graph 固定调用 |
| `publish_to_amazon` | 不存在 | 第一阶段禁止 |

每个 Tool Definition 包含：

- 工具名称和语义版本。
- 输入/输出 JSON Schema。
- 所需权限。
- 可用 Agent 节点。
- 超时和最大响应大小。
- 幂等策略。
- 数据地域和外部供应商。
- 成本分类。
- 审计级别。

## Tool Gateway

Agent 只能生成工具调用意图。Tool Gateway 负责：

1. 确认工具在当前节点可用。
2. 验证 Workflow、用户和审批状态。
3. 解析资源 ID，禁止模型生成任意 URL/路径。
4. 校验参数 Schema 和长度。
5. 检查预算、配额、数据权利和内容策略。
6. 生成幂等键。
7. 创建 Step/Attempt 和 Outbox。
8. 返回任务引用，而不是等待长任务完成。

## MCP 使用边界

### MCP Server 提供

- 商品和 SKU 查询。
- 品牌规范读取。
- 历史素材搜索。
- 资产元数据和权利信息。
- 导出 manifest 创建。

### 不通过 MCP 暴露

- 数据库原始查询。
- Secret 和模型配置。
- 任意 OSS 文件读写。
- Shell、Python 执行和动态代码。
- 管理员权限变更。
- 图片供应商原始 API。

原因是 MCP 是工具契约，不是绕过领域服务的后门。

## MCP 安全

- Server 与 Agent 使用服务身份认证。
- Tool 名称和 Schema 固定版本。
- 每次调用携带 `workflow_id`、`trace_id` 和最小作用域 Token。
- Tool Server 重新执行授权，不信任 Agent 传入的用户信息。
- 返回内容限制长度，并标记为不可信业务数据。
- URL 仅返回内部资产 ID 或短时签名地址。

## Provider Adapter

统一能力接口：

```text
VisionAnalysisProvider
TextPlanningProvider
ImageGenerationProvider
ImageEditingProvider
EmbeddingProvider
ModerationProvider
```

Adapter 负责：

- 供应商协议转换。
- 请求和结果规范化。
- 错误分类。
- timeout/cancel/poll。
- 供应商 request ID。
- 使用量和成本。

Adapter 不负责：

- 决定业务场景。
- 自动绕过内容安全。
- 读取用户权限。
- 修改 Creative Plan。

## Model Capability Registry

每个 Endpoint 登记：

- provider/model/version。
- 能力。
- 输入图片数量。
- 支持尺寸和格式。
- 是否支持 Seed、编辑、Mask、LoRA。
- 数据处理地域。
- 并发和限流。
- 价格。
- 质量基线。
- 允许品类和图片角色。
- 降级组。

## 路由

### 硬过滤

- 能力匹配。
- 图片尺寸和数量。
- 数据地域。
- 权利允许的供应商。
- 内容和品类政策。
- 用户锁定模型。
- LoRA/基础模型兼容。

### 评分

```text
score =
  quality_score
  + recent_availability
  + latency_score
  + remaining_quota
  + cost_score
```

权重按场景版本化。例如商品主图优先一致性，创意场景图可以提高审美质量权重。

## 熔断和降级

- 429、网络错误和 5xx 属于可重试分类。
- 内容拒绝、非法参数和权利风险不自动切换供应商规避。
- 低请求量采用连续失败阈值，高请求量采用滑动窗口错误率。
- 熔断状态按 Endpoint 隔离。
- 半开探测只能使用内部测试流量。
- 使用 LoRA 时只能切换到已验证的兼容绑定。
- 所有兼容端点不可用时进入等待，不无限重试。

## 幂等与未知结果

- 支持供应商幂等键时必须传递。
- 异步供应商保存 task ID 并查询。
- 同步且不可查询的供应商标记 `NON_RECONCILABLE`。
- `NON_RECONCILABLE` 调用发生超时后默认人工确认，不自动再次扣费。

