# 快跑 API 图片生成 Provider 事实基线

> 调研时间：2026-08-06（Asia/Shanghai）
> 范围：`https://kuaipao.pro/v1` 的图片生成、编辑、模型发现及任务语义。
> 证据边界：仅使用快跑公开站点、公开 API 响应，以及站点 `/api/status` 明确配置的官方 Apifox 文档。未使用任何 API 凭证，未发起会产生模型费用的请求。

## 1. 结论摘要

| 问题 | 可证结论 | 置信度 |
|---|---|---|
| 是否 OpenAI-compatible | **是，但仅能逐端点确认。** 官方文档明确把同步图片入口描述为 OpenAI Images 兼容接口；当前部署的 `/v1/images/generations`、`/v1/images/edits`、`/v1/edits`、`/v1/models` 均能匹配路由并在无凭证时返回 `401`。同时还提供 Gemini 原生 `generateContent` 格式。 | 高 |
| 模型发现 | 面向具体令牌的发现入口为 `GET /v1/models`，需要 Bearer Token。公开 `GET /api/pricing` 可作为全站目录/价格元数据源，但不能证明某个令牌实际可调用。 | 高 |
| 同步图片生成 | `POST /v1/images/generations`；JSON 请求；成功响应为 `created + data[]`，图片可在 `url` 或 `b64_json` 中。 | 高 |
| 图片编辑 | 主入口 `POST /v1/images/edits`，兼容别名 `POST /v1/edits`。两条路由当前均存在。 | 高 |
| 异步图片生成/查询 | 官方文档写有 `POST /v1/images/generations/async` 与 `GET /v1/images/generations/async/{taskId}`；**但当前线上部署对这两个精确路径返回 `404 Invalid URL`，不是其他已注册接口的 `401 Invalid token`。因此当前集成必须视为“不支持/未部署”，不能据文档实现为可用能力。** | 高（当前部署） |
| 幂等 | 未找到 `Idempotency-Key`、请求幂等键、去重窗口或重放响应契约。 | `unknown` |
| 限流 | 官方文档定义 `429` 为超过速率限制，但未公开额度、窗口、并发上限、`Retry-After` 或 rate-limit headers。 | 部分已知 |
| 内容安全 | 未找到图片拒绝分类、不可重试的 safety code、审核等级、跨模型/跨渠道安全继承关系或申诉语义。 | `unknown` |
| 计费 | 公开目录能返回模型计费类型、基础价格和分组；站点公告称图片“失败不计费”。币种换算、预扣/退款时点、超时或未知结果是否计费仍未形成公开 API 契约。 | 部分已知 |

生产结论：Phase 4 可以把快跑作为一个 **OpenAI Images 兼容的同步 Provider** 接入；异步任务、幂等重放、精确限流和内容安全分类在获得可复现的已鉴权契约测试前不得声明支持。

## 2. 来源与证据等级

- **A（线上响应）**：对公开 URL 发起不带鉴权的只读请求；可证明当前路由匹配、状态码、错误 envelope 和响应头。
- **B（官方接口文档）**：快跑在 [`GET /api/status`](https://kuaipao.pro/api/status) 中公开配置 `docs_link=https://kuaipao.apifox.cn`，因此以下 Apifox 页面作为快跑一方接口文档使用。
- **C（站点声明）**：官网、公告或价格页面的营销/运营声明；只记录“平台声称”，不提升为可执行协议保证。

任何只存在于文档、但与当前线上响应冲突的内容，以线上响应为准。

## 3. 当前线上只读探测

所有请求均未带 `Authorization`，也未提交有效图片或提示词。

| 请求 | 线上结果 | 可证明事实 |
|---|---|---|
| `GET https://kuaipao.pro/v1/models` | `401 Unauthorized`，`error.type=new_api_error`，消息为无效 token | 路由存在且需要鉴权 |
| `POST https://kuaipao.pro/v1/images/generations` | `401 Unauthorized`，同上 | 同步生成路由存在且需要鉴权 |
| `POST https://kuaipao.pro/v1/images/edits` | `401 Unauthorized`，同上 | 主编辑路由存在且需要鉴权 |
| `POST https://kuaipao.pro/v1/edits` | `401 Unauthorized`，同上 | 文档所述兼容别名当前存在 |
| `POST https://kuaipao.pro/v1/images/generations/async` | `404 Not Found`，`error.type=invalid_request_error`，消息为 `Invalid URL` | 文档中的异步提交路由当前未注册/未暴露 |
| `GET https://kuaipao.pro/v1/images/generations/async/nonexistent-public-probe` | `404 Not Found`，同上 | 文档中的异步查询路由当前未注册/未暴露 |

已观察到的响应头包括 `X-Oneapi-Request-Id` 与 `X-New-Api-Version`。后者当前固定返回 `v0.0.0`，不能作为可用的版本协商信号；前者应作为故障诊断关联 ID 保存。未观察到公开限流响应头。

## 4. 鉴权与模型发现

### 4.1 鉴权

图片文档统一要求：

```http
Authorization: Bearer <token>
```

来源：[同步生成](https://kuaipao.apifox.cn/api-482738940)、[编辑](https://kuaipao.apifox.cn/api-482738942)、[模型列表](https://kuaipao.apifox.cn/api-443891971)。

### 4.2 模型发现

- `GET /v1/models` 是官方列出的模型发现入口，并要求 Bearer Token。[来源](https://kuaipao.apifox.cn/api-443891971)
- 该文档页面的 `200` 示例误放了 chat completion 对象，而不是 model list；因此响应字段不能从该示例锁定。
- 线上不带 token 请求返回 `401`，所以本次无法证明已鉴权响应 schema，也无法证明给定令牌所属分组与实际模型集合。
- `GET /api/pricing` 当前允许公开读取，响应包含 `data[]`、`group_ratio`、`usable_group`、`supported_endpoint`、`pricing_version`。它适合做目录提示和配置审计，不应替代令牌级 `/v1/models` 能力探测。[公开目录](https://kuaipao.pro/api/pricing)

调研时，公开目录中可识别的图片相关条目包括：

| 模型 | `quota_type` | `model_price` | 目录声明的 endpoint types |
|---|---:|---:|---|
| `gpt-image-1.5` | 1 | 0.2 | `image-generation`, `openai` |
| `gpt-image-2` | 1 | 0.25 | `openai` |
| `gpt-image-2-1k` | 1 | 0.25 | `openai` |
| `gpt-image-2-2k` | 1 | 0.4 | `openai` |
| `gpt-image-2-4k` | 1 | 1.5 | `openai` |
| `gpt-image-2-4k-超分` | 1 | 1.0 | `openai` |
| `grok-image` | 1 | 0.25 | `openai` |
| `qwen-image-2.0-pro` | 1 | 0.5 | `openai` |
| `wan2.7-image` | 1 | 0.2 | `openai` |
| `wan2.7-image-pro` | 1 | 0.5 | `openai` |
| `nano-banana-2-1k` | 1 | 0.5 | `gemini`, `openai` |
| `nano-banana-2` / `-2k` / `-4k` | 1 | 1.0 | `gemini`, `openai` |

另有若干 `quota_type=0` 的 Gemini image-token 条目。`model_price` 的币种、分组倍率换算和最终应付金额不应仅凭此 JSON 推导。

## 5. 同步图片生成契约

### 5.1 端点

```http
POST /v1/images/generations
Content-Type: application/json
Authorization: Bearer <token>
```

官方页面称其为 OpenAI Images 兼容入口，并以 `gpt-image-2-1k` 演示。[来源](https://kuaipao.apifox.cn/api-482738940)

### 5.2 文档公开的请求字段

`ImageGenerationRequest` 示例公开字段：[来源](https://kuaipao.apifox.cn/schema-291388934)

| 字段 | 文档可证信息 |
|---|---|
| `model` | 示例为 `gpt-image-2` / `gpt-image-2-1k` |
| `prompt` | 文本提示词 |
| `n` | 生成数量；示例为 `1`，未公开完整范围 |
| `size` | 示例有 `1024x1024`、`1536x1024`；未公开按模型的完整枚举 |
| `image` | 可传参考图；同步 schema 示例为字符串 |
| `response_format` | 示例为 `url`，页面还展示 Base64 返回选项 |
| `quality` | 示例为 `auto`；完整枚举未知 |
| `style` | 公开但无约束说明 |
| `background` | 公开但无约束说明 |
| `watermark` | 布尔示例为 `false` |

### 5.3 成功响应

```json
{
  "created": 1735689600,
  "data": [
    {
      "url": "https://example.com/images/img-abc123.png",
      "b64_json": "...",
      "revised_prompt": "..."
    }
  ]
}
```

`url` 与 `b64_json` 应按可选结果处理，不能假设两者总是同时存在。[来源](https://kuaipao.apifox.cn/schema-291388936)

## 6. 图片编辑契约

### 6.1 路径

- 推荐路径：`POST /v1/images/edits`
- 兼容别名：`POST /v1/edits`

两条路径当前无鉴权探测均返回 `401`，表明路由存在。[来源](https://kuaipao.apifox.cn/api-482738942)

### 6.2 文档矛盾

同一官方页面：

- 正文建议使用 `multipart/form-data` 提交 `image`、`prompt`、`mask`；
- 页面参数区却标为 `application/json` 必填，并给出 data-URL JSON 示例，字段含 `model`、`prompt`、`image`、`size`、`watermark`。

因此当前公开资料不能证明唯一、稳定的媒体类型，也不能证明 `mask`、多图、文件大小、MIME 和尺寸限制。生产适配器应把 multipart 与 JSON 视作两个独立 capability，并在受控已鉴权契约测试后才启用；不能仅凭文档猜测。

成功响应与同步生成相同：`created + data[].url/revised_prompt`；页面列出 `400/401/402/429` 错误。

## 7. Gemini 原生图片格式

快跑还公开了 Gemini 原生入口：[来源](https://kuaipao.apifox.cn/api-482738943)

```http
POST /v1beta/models/{model}:generateContent
```

文生图和图生图均使用 `contents[].parts[]`；参考图通过 `parts[].inlineData`；`generationConfig` 示例包含 `responseModalities`、`temperature`、`topP`、`maxOutputTokens`、`imageConfig.aspectRatio`、`imageConfig.imageSize`。成功响应示例包含 `candidates[].content.parts[].inlineData`、`finishReason`、`usageMetadata`、`modelVersion` 和 `createTime`。

这不是 OpenAI Images 响应 envelope，应该实现为独立协议适配器，不能在同一解析器中靠大量分支混用。

## 8. 异步任务：文档契约与线上现实

官方文档声明：

- 提交：`POST /v1/images/generations/async`；可能返回任务，也可能兼容直接返回图片。[来源](https://kuaipao.apifox.cn/api-482738944)
- 查询：`GET /v1/images/generations/async/{taskId}`；完成后从 `data[].url` 或 `data[].b64_json` 读取结果。[来源](https://kuaipao.apifox.cn/api-482738945)
- 任务 ID 可能出现在 `id`、`task_id` 或 `taskId`。
- 任务示例字段：`object`、`model`、`status`、`progress`、`created_at`、`completed_at`、`expires_at`、`size`、`data[]`、`error`。[任务 schema](https://kuaipao.apifox.cn/schema-291388938)
- 示例状态至少包含 `queued`、`processing`；文档 UI 还展示完成与失败示例，但未给出正式状态枚举和状态机。

但是，当前线上两个精确路径均返回 `404 Invalid URL`。此外异步页面响应示例写 `gpt-image-2-pro`，该模型在本次公开 `/api/pricing` 目录中不存在，而请求示例使用 `gpt-image-2-1k`。这是明确的文档/部署漂移。

**Phase 4 决策：快跑 capability registry 中 `async_submit=false`、`async_reconcile=false`，直到线上路由探测与已鉴权契约测试同时通过。**

## 9. 错误、限流、幂等、计费与安全

### 9.1 错误 envelope

官方 schema：[来源](https://kuaipao.apifox.cn/schema-291388948)

```json
{
  "error": {
    "message": "prompt is required",
    "type": "invalid_request_error",
    "param": "prompt",
    "code": "invalid_request"
  }
}
```

线上无效鉴权响应同样使用 `error` envelope，但 `type=new_api_error`、`code` 为空，且 request ID 可能嵌入 `message`。解析器必须允许 `code`、`param` 为空并优先采集 `X-Oneapi-Request-Id`。

官方状态码页说明：[来源](https://kuaipao.apifox.cn/doc-8542915)

- `400` 请求格式错误；`401` key 无效或过期；`403` 权限不足；`404` 资源/端点不存在；`413` 请求体过大；
- `429` 超过速率限制；`500` 服务内部错误；`503` 维护或过载；
- 图片生成/编辑页面还列出 `402`，但没有定义其精确含义或错误 code。

### 9.2 限流

已知只有 `429` 的一般含义。以下均为 `unknown`：每分钟/并发额度、是否按账号/令牌/模型/分组/IP 限制、`Retry-After`、重置时间头、429 是否可能来自上游以及是否计费。客户端应采用有上限的指数退避与抖动，但不能假设任何固定窗口。

### 9.3 幂等

公开快跑图片文档、数据模型和状态码页没有 `Idempotency-Key` 或请求级幂等字段，也没有重复提交去重窗口/响应重放保证。因此：

- 同步请求在连接超时或响应丢失后属于 **unknown outcome**；
- 当前又没有可用的异步查询路由可用于对账；
- 在生产中不得对 POST 自动盲重试，否则可能重复出图和重复计费。

### 9.4 计费

- `/api/pricing` 明确提供 `quota_type`、`model_price`、分组和目录版本；调研时多种图片模型为 `quota_type=1`（按次类元数据）。
- 官方文档首页称 `gpt-image-2` 为按张价格；站点 `/api/status` 的 2026-07-31 图片公告称生成失败不计费。[文档首页](https://kuaipao.apifox.cn/)、[站点状态](https://kuaipao.pro/api/status)
- `model_price` 的币种、分组倍率换算、税费、预扣、超时、客户端断连、上游成功但网关丢响应、内容安全拒绝等场景的最终扣费/退款语义未形成公开契约。

因此价格只能做运行时展示和预估；账务真值应来自平台实际用量/日志，并把 provider request ID、模型、分组、预计费用和最终费用分别保存。

### 9.5 内容安全

快跑图片接口文档未公开：

- safety rejection 的 HTTP 状态、`error.type/code`；
- 哪些模型/渠道执行何种审核；
- safety 拒绝是否计费；
- 拒绝是否允许改投其他模型或渠道；
- 上传参考图的数据留存、训练使用、区域和删除 SLA。

以上全部为 `unknown`。Phase 4 不应把普通 `400`/`403` 自动等同于安全拒绝，也不应在识别出安全拒绝后自动跨供应商绕过；需要 CommerceVision 自己的前置安全门和不可降级的 safety 终态。

## 10. 尚未由公开来源证明的清单

1. 给定令牌实际可调用的模型、分组、并发和额度。
2. 同步与编辑接口的真实已鉴权成功响应，以及不同模型的字段差异。
3. 编辑接口到底保证 multipart、JSON 还是两者；文件大小、格式、mask 和多图限制。
4. 各模型允许的 `size`、`quality`、`n`、`style`、`background`、`watermark` 枚举及默认值。
5. 异步图片路由何时/在哪个分组可用；当前线上明确不可用。
6. 任务取消、Webhook、轮询建议间隔、终态全集、结果 TTL 与 URL 下载时效。
7. 请求幂等、重复提交去重、unknown outcome 对账与退款保证。
8. 限流额度、并发上限、rate-limit headers 和 `Retry-After`。
9. safety 分类与不可重试错误码。
10. 精确计费单位、币种换算、预扣/退款时点及最终账单查询 API。
11. 图片/提示词/参考图的数据驻留、保留、训练使用和删除契约。

## 11. Phase 4 适配边界建议

- 将同步 OpenAI Images、Gemini 原生图片分别建成独立协议 Adapter；共享的只是传输、鉴权和可观测性基础设施。
- 启动时以 `GET /v1/models` 做令牌级 discovery，以 `/api/pricing` 做非权威目录/价格补充；对缺失能力 fail closed。
- 快跑当前 capability 默认仅开启同步生成与编辑；异步提交/查询关闭。
- 对 `401/402/403/413` 不自动重试；`429/500/503` 仅在请求确定未被接受时有限重试。连接超时、断连或 5xx unknown outcome 不盲重发。
- 保存 HTTP 状态、`error.type/code/param`、`X-Oneapi-Request-Id`、模型、请求摘要、耗时和成本对账字段；不得记录 Bearer token 或完整参考图。
- 任何内容安全拒绝一旦被本地或 provider 明确识别，必须成为不可跨 provider 绕过的终态。

## 12. 主要第一方来源

- [快跑官网](https://kuaipao.pro/)
- [公开站点状态与官方文档链接](https://kuaipao.pro/api/status)
- [公开模型/价格目录](https://kuaipao.pro/api/pricing)
- [快跑官方 Apifox 首页](https://kuaipao.apifox.cn/)
- [同步图片生成](https://kuaipao.apifox.cn/api-482738940)
- [图片编辑](https://kuaipao.apifox.cn/api-482738942)
- [Gemini 原生图片生成](https://kuaipao.apifox.cn/api-482738943)
- [异步图片提交（文档声明；当前线上 404）](https://kuaipao.apifox.cn/api-482738944)
- [异步图片查询（文档声明；当前线上 404）](https://kuaipao.apifox.cn/api-482738945)
- [模型列表](https://kuaipao.apifox.cn/api-443891971)
- [HTTP 状态码](https://kuaipao.apifox.cn/doc-8542915)
- [错误响应 schema](https://kuaipao.apifox.cn/schema-291388948)
