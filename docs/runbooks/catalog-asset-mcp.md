# Product Catalog 与 Asset MCP Runbook

| 属性 | 值 |
|---|---|
| 状态 | implementation complete；远程 CI 为最终放行门禁 |
| 最后更新 | 2026-08-03 |
| 适用版本 | Phase 2 / Ticket 12 |

## 边界与工具面

MCP Server 是现有 Application Service 的只读入站 Adapter。公开面固定为五个版本化工具：

- `catalog.get_product.v1`
- `catalog.get_product_brief.v1`
- `brand.get_profile.v1`
- `assets.search.v1`
- `assets.get_temporary_reference.v1`

工具层不持有 SQL、对象 key、Bucket、Milvus filter、Provider credential 或任意 URL
能力。数据库、对象存储、Embedding 和向量索引只在共享 composition helper 中装配，并通过
窄 Application Port 调用。禁止为 Agent 新增上传、Rights mutation、删除、任意网络读取或
调用方选择模型的工具。

## 身份与授权

生产传输固定为 Streamable HTTP。每次工具调用都必须携带 `X-Trusted-Principal`，格式为
`key_id.base64url(claims).hmac_sha256`。服务端验证 current/previous key、签名、签发时间与
完整闭合 claims 后，才取得 Workspace、actor、workflow、invocation、scopes、purpose、
provider、derivative requirement 和预算。以上字段不得出现在模型可写的工具参数中。

生产环境必须配置非公开的 `CV_TRUSTED_PRINCIPAL_CURRENT_KEY_ID` 与
`CV_TRUSTED_PRINCIPAL_CURRENT_HMAC_SECRET`；轮换时先发布 previous/current 双 key，再让
签发方切换，等待最大 token age 后移除 previous。公开 local secret 会使生产启动失败。

Scope 映射：

| 工具 | 必需 scope |
|---|---|
| Product / ProductBrief | `catalog.read` |
| Brand Profile | `brand.read` |
| Asset search | `assets.search` |
| Temporary reference | `assets.read` |

## 限额与错误

输入、输出均使用 `extra=forbid` 的严格 Pydantic/JSON Schema。默认请求参数上限 64 KiB，
工具输出上限 256 KiB；签名身份还可施加更小的 result、candidate 和 output budget。请求
`top_k` 超过签名预算会被拒绝，不会静默扩容或截断。

公开错误只返回稳定 `code` 与 `retryable`，不返回内部异常、凭据或连接地址。常见分类：

| code | retryable | 动作 |
|---|---:|---|
| `AUTHENTICATION_REQUIRED` | false | 获取新的合法签名身份 |
| `TOOL_POLICY_DENIED` / `ACCESS_DENIED` | false | 修正 scope 或取得新的 Rights approval |
| `TOOL_EXECUTION_REJECTED` | false | 修正闭合参数或预算 |
| `NOT_FOUND` | false | 刷新 Workspace 内的资源身份 |
| `DEPENDENCY_UNAVAILABLE` | true | 按退避策略重试并观察 readiness |
| `INTERNAL_ERROR` | false | 停止自动重试并升级处理 |

## 就绪性与处置

- `/health/live` 只证明进程和路由存活。
- `/health/ready` 独立探测 MySQL 与 Task/Foundation 对象存储，任一失败返回 503；响应只含
  `ok/failed`，不含异常详情。
- Milvus 或单个召回通道不可用时，`assets.search.v1` 依照 Retrieval policy 返回带
  degradation 的可用结果；因此不作为 MCP 进程硬 readiness 依赖。

排障顺序：先看 readiness 的 `mysql` / `object_storage`，再按错误 code 定位身份、scope、
预算或资源。不得通过放宽 Schema、扩大签名预算、绕过 Tool Gateway、直接生成对象 URL 或
将公开 local secret 带入生产来恢复服务。

## 发布验证

发布前至少执行：MCP unit/contract tests、全量 Python unit/contract/integration 门禁、Ruff、
Compose config、容器构建、`/health/ready`，并用配置的 Streamable HTTP transport 枚举和调用
全部五个工具。Ticket 13 只能在 Ticket 12 精确提交对应的远程 CI 全绿后开始。
