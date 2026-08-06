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

## 13. Alibaba Wan 2.7（2026-08-06，第一方资料核验）

本节只依据阿里云百炼 / Model Studio 官方文档整理，未使用任何凭据，也未发起可能计费的调用。核心来源为 [Wan 2.7 图像生成与编辑 API 参考](https://help.aliyun.com/en/model-studio/wan-image-generation-and-editing-api-reference)、[图像模型总览](https://help.aliyun.com/en/model-studio/image-model/)、[文生图指南](https://help.aliyun.com/en/model-studio/text-to-image)、[限流](https://help.aliyun.com/en/model-studio/rate-limit)、[限流最佳实践](https://help.aliyun.com/en/model-studio/rate-limiting-best-practices) 和 [错误码](https://help.aliyun.com/en/model-studio/error-code)。

### 13.1 模型、地域与 HTTP 契约

当前 Wan 2.7 图像模型 ID 为：

- `wan2.7-image-pro`：文生图（非组图）最高 4K；编辑、参考图和组图最高 2K。
- `wan2.7-image`：同类生成/编辑能力，最高 2K，官方定位为更快版本。

官方 Wan 2.7 API 参考明确给出的业务空间专属 HTTP 地址如下；`{WorkspaceId}` 是实际百炼业务空间 ID：

| 操作 | 华北 2（北京） | 新加坡 |
| --- | --- | --- |
| 同步生成/编辑 | `POST https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation` | `POST https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation` |
| 异步提交 | `POST https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/image-generation/generation` | `POST https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/api/v1/services/aigc/image-generation/generation` |
| 异步查询 | `GET https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1/tasks/{task_id}` | `GET https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/api/v1/tasks/{task_id}` |

鉴权和 headers：

- 同步：`Content-Type: application/json`、`Authorization: Bearer <api-key>`。
- 异步提交：除以上两项外，必须传 `X-DashScope-Async: enable`。官方明确说明，缺少该 header 会返回 `current user api does not support synchronous calls`。
- 异步查询：只要求 `Authorization: Bearer <api-key>`；`task_id` 在 path 中。
- 北京和新加坡的 API Key、请求地址均按地域隔离，不可混用；跨地域混用会导致鉴权失败或服务错误。

同步和异步不是同一路径上的开关：同步使用 `multimodal-generation/generation`，一次请求直接返回结果且不产生供客户端轮询的 `task_id`；异步使用 `image-generation/generation`，先返回任务身份，再由 `/tasks/{task_id}` 查询。适配器必须将二者建成不同 transport flow，不能仅靠是否存在异步 header 猜测响应形态。官方 Python/Java SDK 同时提供同步与异步封装，但 HTTP 契约仍以上述两条独立路径为准。

### 13.2 请求 envelope 与字段约束

同步与异步提交使用相同的请求主体结构：

```json
{
  "model": "wan2.7-image-pro",
  "input": {
    "messages": [
      {
        "role": "user",
        "content": [
          {"image": "<public-url-or-data-url>"},
          {"text": "<prompt>"}
        ]
      }
    ]
  },
  "parameters": {
    "size": "2K",
    "n": 1,
    "watermark": false
  }
}
```

已确认的输入契约：

- `model` 必填，只接受上面两个 Wan 2.7 ID。
- `input.messages` 必填且只支持单轮；`role` 必须为 `user`；`content` 是由 `text` 和零到多个独立 `image` 对象组成的数组，多图顺序具有语义。
- `text` 支持中文和英文，最多 5,000 个字符；超长部分会被截断，而不是明确拒绝。
- 可输入 0–9 张图片。图片可为 HTTP(S) 公网 URL 或完整 Base64 data URL；格式为 JPEG/JPG、PNG（不支持 alpha）、BMP、WEBP；宽高各 240–8,000 px、宽高比 1:8–8:1、单张不超过 20 MB。
- `bbox_list` 用于交互式编辑，外层长度必须等于输入图片数；无框图片传 `[]`；坐标为原图绝对像素 `[x1,y1,x2,y2]`，每张图最多两个框。

已确认的 `parameters`：

| 字段 | 契约 |
| --- | --- |
| `enable_sequential` | 默认 `false`；`true` 启用组图。 |
| `size` | `wan2.7-image-pro` 支持 `1K`、默认 `2K`；只有无输入图且非组图的文生图可用 `4K`。自定义尺寸时文生图总像素范围为 768×768 到 4096×4096，其他场景到 2048×2048，宽高比 1:8–8:1。`wan2.7-image` 仅支持 `1K`、默认 `2K`，所有场景自定义尺寸上限 2048×2048。输出像素可能与指定值有轻微差异。 |
| `n` | 非组图范围 1–4，默认 1；组图范围 1–12，默认 12，模型决定实际数量且不超过 `n`。成功图片数直接影响费用。 |
| `thinking_mode` | 默认 `true`；仅在非组图且没有图片输入时有效，会增加耗时。 |
| `color_palette` | 仅非组图可用；3–10 个颜色（官方建议 8 个），每项为 HEX 与两位小数百分比，比例合计必须为 100.00%。 |
| `watermark` | 默认 `false`；`true` 时在右下角加入固定 `AI Generated` 标识。 |
| `seed` | 可选整数 `[0,2147483647]`；相同 seed 只保证相似，不保证完全一致。 |

Wan 2.7 的两个模型均**不支持** `negative_prompt` 和 `prompt_extend`；排除内容应写入正向 prompt，质量增强使用 `thinking_mode`。组图模式下 `thinking_mode` 和 `color_palette` 均不可用。

### 13.3 异步身份、查询状态与终态映射

异步提交成功响应返回两类身份：

```json
{
  "output": {
    "task_status": "PENDING",
    "task_id": "<provider-task-id>"
  },
  "request_id": "<provider-request-id>"
}
```

- `output.task_id` 是后续查询的 provider job identity，官方声明可查询 24 小时。
- `request_id` 是本次 HTTP 请求的唯一追踪/排障 ID。提交和每次查询都应分别保存自己的 `request_id`，不能把它当作 `task_id`。
- 创建失败没有 `task_id`，使用顶层 `code`、`message`、`request_id` error envelope。

查询响应的 `output` 可包含 `task_id`、`task_status`、`submit_time`、`scheduled_time`、`end_time`、`finished`、`choices`；顶层继续有本次查询的 `request_id`。官方状态枚举为 `PENDING`、`RUNNING`、`SUCCEEDED`、`FAILED`、`CANCELED`、`UNKNOWN`，并明确常规转换为 `PENDING → RUNNING → SUCCEEDED | FAILED`。

以下是面向 CommerceVision 的推荐映射；其中“内部语义”是基于官方状态定义的工程推论，不是阿里云字段：

| Provider 状态 | 推荐内部语义 | 处理原则 |
| --- | --- | --- |
| `PENDING` | 非终态 / queued | 继续有界轮询，不得重复提交。 |
| `RUNNING` | 非终态 / running | 继续有界轮询，不得重复提交。 |
| `SUCCEEDED` | 终态成功 | 解析并立即持久化所有 `choices[].message.content[]` 图片。 |
| `FAILED` | 终态失败 | 读取 `code/message` 后分类；只有明确瞬态 provider 错误才允许创建新任务，参数、安全、鉴权类错误不可自动改投。 |
| `CANCELED` | 终态取消 | 不自动重建任务。Wan 2.7 专页没有给出取消动作或该状态的完整转换路径。 |
| `UNKNOWN` | 对账不确定 | 官方定义为任务不存在或状态未知；可能与 ID 错误、24 小时过期或后端未知有关，不能直接等同于业务失败，更不能据此盲目重提。 |

官方还返回 `finished`（默认 `false`），但生产状态机应以 `task_status` 的枚举与上述终态集合为主，并把字段冲突记录为 provider protocol violation，而不是静默选择其一。

### 13.4 结果、时效与计费事实

成功查询的实际媒体位于 `output.choices[].message.content[]`：`message.role` 固定为 `assistant`，内容项可能为 `type=image` 或 `type=text`；图片字段为 `image`，格式为 PNG。`finish_reason=stop` 表示自然完成。

图片 URL 是带过期参数的阿里云对象存储 URL，官方只保证 24 小时有效；任务数据（状态与图片 URL）也只保留 24 小时，之后自动清理。因此成功终态处理必须先下载到 CommerceVision 自有持久存储，再提交业务成功；不能把 provider URL 当作长期资产 URL。

`usage` 可包含 `size`、`image_count`、`input_tokens`、`output_tokens`、`total_tokens`。官方说明 token 只作统计、不用于图片计费，费用按成功生成的图片数计算；失败调用和处理错误不收费，也不消耗新用户免费额度。多图时 `usage` 只统计成功结果，但 Wan 2.7 专页没有定义逐图部分失败的 error item 形态，见未知项。

### 13.5 限流、错误与重试边界

官方限流页当前给出的 Wan 2.7 额度如下：

| 模型 | 北京：提交 RPS / 处理中并发 | 新加坡：提交 RPS / 处理中并发 |
| --- | --- | --- |
| `wan2.7-image-pro` | 5 / 5 | 5 / 5 |
| `wan2.7-image` | 5 / 5 | 5 / 5 |

限流在阿里云主账号维度聚合该账号下所有 RAM 用户、业务空间和 API Key，并按模型分别计算。官方称通常一分钟内自动恢复，但还可能有每秒控制和动态突发保护：即使尚未达到表面总量，流量骤增也可能触发限制。生产客户端应以本地队列、每模型并发信号量、平滑限速和带抖动的指数退避共同约束，而不是把“5 RPS”当作可以瞬时突发 5 个请求的保证。

官方通用错误页确认的主要类别：

- `400 InvalidParameter`：请求字段/值不合法；修正请求，不重试原 payload。
- `401 InvalidApiKey`、`403 AccessDenied.*` / `Model.AccessDenied`、`404 WorkSpaceNotFound` / `NotFound`：鉴权、权限、工作空间、资源或路由问题；不做自动 provider failover，先修复配置或权限。
- `429 Throttling`、`Throttling.RateQuota`、`Throttling.BurstRate`、`Throttling.AllocationQuota`：分别覆盖一般限流、请求速率、突发增长和配额维度；可以有界退避，但不能假定响应必带 `Retry-After`。
- `500 InternalError`、`InternalError.Timeout`、`SystemError`、`ModelServiceFailed`、`RequestTimeOut`，以及 `503 ModelUnavailable` / `ModelServingError`：可能是瞬态服务或超时错误。官方说明异步任务超过 3 小时可能产生 `InternalError.Timeout`。
- 文生图官方指南明确 `DataInspectionFailed` 表示输入触发内容审核。该类是输入/安全终态：修订内容后由用户重新发起，不得自动跨模型或跨 provider 绕过审核。

HTTP 客户端收到明确未创建任务的 429/5xx 时可以按策略有限重试；一旦提交响应丢失而“不知道是否已经创建任务”，则属于 unknown outcome。公开 Wan 2.7 文档没有幂等键或重复提交去重保证，所以此时不得盲目重发 POST，否则可能重复出图与重复计费。

### 13.6 明确未知、冲突与不可推断项

1. Wan 2.7 API 专页只公布北京、新加坡的精确业务空间主机；新的模型详情页同时列出日本（东京）的模型价格/限流，但没有在 Wan 2.7 API 参考中给出东京专属 host/path/key 契约。东京地址必须视为 `unknown`，不得按地域命名规律自行拼接。
2. 官方未公布提交幂等键、去重窗口、unknown-outcome 对账接口或响应重放保证。
3. Wan 2.7 专页未给出推荐轮询间隔、最大轮询时长、退避曲线、`Retry-After` 或 rate-limit response headers。
4. 通用异步任务文档公开了非业务空间域名上的取消 API，且仅能取消 `PENDING`；Wan 2.7 专页未确认业务空间专属取消 URL，也未说明 `CANCELED` 的完整转换。因此不能从查询 URL 机械推导取消 URL。
5. 未发现 Wan 2.7 的 webhook/callback 完成通知契约；当前只能按异步查询流程设计。
6. Wan 2.7 专页未定义 `n > 1` 时部分图片失败的逐项 schema、整体 `task_status` 或计费边界；只能以实际 `choices` 和 `usage.image_count` 保存观察结果，不能预设与旧 Wan API 相同。
7. 图片 URL 的响应 `Content-Type`、`Content-Length`、checksum、下载限流、出口费用及续签方式未形成 Wan 2.7 契约；下载器需校验真实媒体并在 24 小时内持久化。
8. `UNKNOWN` 无法区分“不存在”“已过期”和 provider 暂时无法确认；`CANCELED` 也没有专页级转换规则。二者都应保留原始响应用于人工/运维对账。
9. Wan 2.7 专页没有模型专属完整错误矩阵；`DataInspectionFailed` 来自官方文生图指南，其他内容安全/IP 错误 code 不应从 Wan 2.6 或其他编辑模型类推。
10. 官方声明 prompt 超过 5,000 字符会截断，但未精确定义 Unicode 计数单位或是否返回截断标志。生产侧应在发送前自行限制并记录规范化后的 prompt 摘要。

### 13.7 Phase 4 适配结论

- 为 `wan2.7-image-pro` / `wan2.7-image` 建独立的 Model Studio 原生 adapter，不把它伪装成 OpenAI Images 协议。
- endpoint 配置应是“地域 + WorkspaceId”不可分割的 typed configuration；API Key 必须通过 secret store 注入，禁止进入仓库、日志、任务 payload 或研究文档。
- 异步提交成功后立即持久化 `task_id`、提交 `request_id`、地域、workspace、模型和请求摘要；轮询每次另存 query `request_id` 与原始状态。
- provider 成功只代表生成完成；只有媒体下载、内容校验并写入自有持久存储成功后，CommerceVision 才能提交资产成功。
- 重试/路由必须按“限流/瞬态服务、输入参数、鉴权权限、内容安全、unknown outcome”分类，不能仅按 HTTP 4xx/5xx 粗分。
