# 测试策略

| 属性 | 值 |
|---|---|
| 状态 | decision |
| 最后更新 | 2026-07-22 |
| 适用版本 | Quality v1 |

## 测试层次

```text
静态检查
  -> 单元测试
  -> Contract 测试
  -> 集成测试
  -> Agent Eval
  -> E2E
  -> Load/Soak
  -> Chaos/Recovery
  -> Security
```

## 静态检查

Python：

- Ruff。
- Pyright 或 Mypy。
- Bandit/Semgrep。
- 依赖漏洞扫描。

TypeScript：

- ESLint。
- `tsc --noEmit`。
- 依赖漏洞和 License 检查。

基础设施：

- Dockerfile lint。
- Helm lint。
- Terraform fmt/validate/tflint。
- Kubernetes Policy 检查。

## 单元测试

重点测试：

- 状态转换。
- Pydantic Schema。
- ContextBuilder。
- 路由评分和硬过滤。
- Tool Policy。
- Retention 计算。
- 错误分类。
- Amazon 规则。
- Evaluator 聚合。

单元测试不能调用真实模型。

## Contract 测试

- OpenAPI 与前端 Client。
- MCP Tool Schema。
- RabbitMQ Event Schema。
- Provider Adapter Fixture。
- LangGraph Checkpointer Contract。
- Webhook 签名。

自定义 MySQL Checkpointer 必须复用 LangGraph Checkpoint Contract 行为，并测试同步/异步 API。

## 集成测试

使用容器启动：

- MySQL。
- Redis。
- RabbitMQ。
- Milvus。
- MinIO。

覆盖：

- 事务 Outbox。
- Inbox 去重。
- MySQL 到 Milvus 索引。
- 预签名上传。
- Checkpoint 恢复。
- Provider Mock。
- Retention 清理。

## Agent Eval

Agent Eval 与普通单元测试分开：

- 固定 Dataset。
- 固定 Prompt/模型/工具版本。
- 质量、成本和时延。
- 允许统计波动，但有阈值和置信区间。
- Hidden Test 不用于日常 Prompt 调参。

PR 默认运行小型确定性 Eval；夜间或手动发布运行完整真实模型 Eval。

## E2E

Playwright 覆盖：

1. 登录。
2. 创建 Workflow。
3. 上传商品。
4. 确认 ProductBrief。
5. 审批 Creative Plan。
6. 查看候选图和 Evaluator。
7. 触发局部重做。
8. 终审。
9. 导出。
10. 查看 Trace。

E2E 使用 Provider Mock，发布候选环境额外运行真实模型冒烟。

## 性能

- 20 个并发 Workflow 受理。
- 100 个并发外部调用受全局配额控制。
- 300 Workflow/日 Soak。
- Milvus 检索 P95。
- MySQL 连接池和慢查询。
- SSE 重连风暴。
- 大文件直传。

## Chaos/Recovery

必须演练：

- 杀死 API Pod。
- 杀死 Agent Worker。
- 工具调用后、完成事务前杀死 Worker。
- RabbitMQ 暂停和恢复。
- Redis 清空。
- Milvus 暂时不可用。
- MySQL 主备切换。
- OSS 超时。
- Provider 返回未知结果。

验证重点是任务状态、重复计费和恢复，不只是服务重新启动。

Phase 1 已将 Worker 停止、人工等待期间审批、RabbitMQ 暂存和 MySQL Checkpoint 恢复纳入在线验收；后续 Phase 4-7 再扩展到长耗时 Provider 调用中断、未知结果对账和基础设施主备切换。

## 安全测试

- Prompt Injection。
- SSRF。
- 上传伪造和解压炸弹。
- 越权读取 Workflow/Asset。
- 审批重放。
- Webhook 伪造。
- Secret 泄露。
- 依赖与镜像漏洞。
- 不安全反序列化。
- 公共 Demo 配额绕过。

## 覆盖率

覆盖率不是唯一质量指标，但最低要求：

- Domain/Agent 状态转换：90%。
- Tool Policy、权限和安全逻辑：90%。
- 普通服务层：80%。
- 前端关键 Workflow：E2E 全覆盖。

模型质量由 Agent Eval 衡量，不使用代码覆盖率替代。
