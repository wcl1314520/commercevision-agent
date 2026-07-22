# 可靠性、安全与数据治理

| 属性 | 值 |
|---|---|
| 状态 | decision |
| 最后更新 | 2026-07-21 |
| 适用版本 | Engineering v1 |

## SLO

### 公开 Demo

| SLI | 目标 |
|---|---:|
| Web/API 月可用性 | >= 99.9% |
| 非生成 API P95 | <= 500 ms |
| Workflow 可靠受理 P95 | <= 2 秒 |
| 已受理任务持久化成功率 | >= 99.9% |

### 企业部署目标

| SLI | 目标 |
|---|---:|
| 控制面月可用性 | >= 99.95% |
| 已受理任务持久化成功率 | >= 99.99% |
| 平台原因最终失败率 | <= 0.5% |
| Webhook 最终送达率 | >= 99.9% |
| 任务数据到期清理率 | >= 99.9%/日 |

供应商生成成功率单独统计，不通过排除供应商故障掩盖用户体验。

## 可靠执行

Worker 采用：

```text
短事务认领
  -> 提交
  -> 事务外执行模型/工具
  -> 短事务完成
```

禁止在模型网络调用期间持有 MySQL 事务。

### 幂等

- Workflow 创建：用户 Idempotency Key。
- 消息消费：Inbox 唯一键。
- Tool 执行：稳定 Tool Execution Key。
- 图片生成：Generation Attempt Idempotency Key。
- Webhook：Event ID。
- 导出：Workflow + approval version + ruleset version。

### 恢复

- Step Lease。
- Provider task ID 对账。
- Outbox 补发。
- LangGraph Checkpoint。
- DLQ 和可审计重放。
- 数据状态不一致检测。

## 身份与权限

只定义两类产品权限：

| 权限 | 能力 |
|---|---|
| 管理员 | 用户、模型、Prompt、工具、品牌、数据集和系统配置 |
| 使用者 | 创建任务、审核、返工、导出和查看授权任务 |

审批是 Workflow 能力，不额外创造大量角色。公开 Demo 使用受限账户和全局配额。

## Secret

- 生产 Secret 保存于 KMS/Secret Manager。
- 数据库只保存 Secret Reference。
- Pod 使用工作负载身份。
- 支持双 Key 轮换。
- 日志和 Trace 执行脱敏。
- `.env.example` 只包含占位符。
- CI 执行 Secret Scan。

## 文件上传

- MIME、魔数和真实解码。
- 文件大小和总像素。
- 防止图像解压炸弹。
- 隔离前缀上传。
- 病毒/恶意文件扫描。
- 扫描前不能进入 Agent Context。
- 文件名不作为 OSS Key。

未来支持 LoRA 时：

- 仅 `.safetensors`。
- 不执行 Pickle、脚本或仓库代码。
- 限制 Header 和张量元数据大小。
- 必须记录许可证和基础模型。

## SSRF 与出站

- 不实现任意 URL 图片代理。
- 远程素材只允许登记过的域名或内部资产 ID。
- DNS 解析后阻止私网、环回、链路本地和云元数据地址。
- 每次重定向重新校验。
- 设置连接、首字节、总时限和响应体上限。
- 生产出站通过固定 NAT/EIP 和 NetworkPolicy。

## Prompt Injection

- OCR、商品描述、MCP 返回和供应商响应均是不可信数据。
- Tool List、权限和系统政策在服务端固定。
- 输出通过 Pydantic 和业务白名单。
- 模型不能构造 SQL、路径、URL 或 Secret Reference。
- 人工审批版本不能由模型修改。
- 建立专门注入测试集。

## 内容安全

- 输入和输出内容审核。
- 真人素材记录授权和处理范围。
- 内容拒绝不能换供应商绕过。
- 模型 Judge 不替代平台内容安全。
- 公开 Demo 设置提示词和图片滥用检测。

## 数据治理

- 任务数据 72 小时。
- 公开数据集和品牌资产按许可证保存。
- 用户可以提前删除任务。
- 删除资产后先禁止检索，再删除 OSS 和 Milvus。
- 审计保存脱敏元数据 180 天。
- 供应商登记数据地域、保留期和训练政策。

## RPO/RTO

| 场景 | RPO | RTO |
|---|---:|---:|
| API/Worker Pod | 0 | <= 5 分钟 |
| 单节点 | 0 | <= 10 分钟 |
| 单可用区 | 以实例复制模式和演练确认，目标 0 | <= 30 分钟 |
| MySQL 逻辑误操作 | <= 5 分钟 | <= 60 分钟 |
| 地域故障 | 公开 Demo 不承诺；企业版后续评估 | 不承诺 |

任何 RPO 0 声明都必须经过真实实例故障演练。

## 上线阻断

- Secret 出现在代码或日志。
- 人工审批可绕过。
- 任意 URL/SSRF 未处理。
- 没有 Outbox/Inbox 和恢复器。
- Agent 存在无界循环。
- 没有固定评测集。
- 无权素材进入检索或生成。
- Checkpoint 使用不安全反序列化。
- 任务数据不能按策略删除。

