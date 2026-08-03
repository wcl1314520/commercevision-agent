# IMAGE 索引 Runbook

| 属性 | 值 |
|---|---|
| 状态 | active |
| 最后更新 | 2026-08-03 |
| 所有者 | Index Worker |

## 边界

- MySQL 是索引资格、Rights、operation epoch、写入代际与公开状态的权威。
- MinIO/OSS 只通过短期受控 URL 提供精确 `AssetVersion` 原图，单图上限 5 MiB。
- Milvus 只存 generation-specific 向量；物理主键为 `<embedding_record_id>:g<N>`。
- API readiness 不依赖 Milvus。只有消费 `commercevision.index` 的 Worker 探测 MySQL、对象存储、Milvus、Provider 凭据。
- Alibaba 提交前必须通过 workspace、retention class、provider、region、host 五项数据出境 allowlist；拒绝发生在 URL 签发前。

## 状态与恢复

- `PENDING/RETRYABLE_FAILED`：Durable Operation 继续调度；每次 provider submission 单调增加 `write_generation`。
- `PROCESSING`：外部调用中。Milvus upsert 结果未知时进入强 identity reconciliation，禁止盲目重投。
- `INDEXED`：仅该状态可作为候选；Ticket 10 retrieval 仍必须按 MySQL 当前 generation 与 Rights 再过滤，Milvus 状态本身不是授权。
- `PERMANENT_FAILED`：DLQ/人工恢复边界；Rights REINDEX 不自动复活。
- `DELETE_PENDING`：立即从公开检索隐藏，typed delete event 删除精确旧 generation。
- 重授权复用同一 `EmbeddingRecord`，创建新的 operation epoch。旧 generation delete 即使迟到，也不能修改新 operation/generation 的 MySQL 状态。
- Durable Operation 达到 attempt/reconciliation 上限时，终态回调把当前 `PROCESSING` 或 `RETRYABLE_FAILED` 幂等收敛为 `PERMANENT_FAILED`。

## Collection 与模型版本迁移

Alibaba `qwen3-vl-embedding` 是 Provider 提交的 mainline model ID；`embedding_pinned_revision` 是 CommerceVision 内部发布/collection epoch，不是 Provider 确认的不可变快照。

Schema 或索引参数升级必须走候选 Collection 重建，活动 Collection 在回填和验证期间继续读写；
不得提前关闭写入或原地清空。Embedding identity（model family、model ID、pinned revision、dimension）
变化属于新的 embedding spec/re-index，不伪装成兼容 Collection upgrade。完整操作流程见
[Collection 重建与升级 Runbook](collection-rebuild.md)。

## 诊断

1. 查询公开 `GET /api/v1/assets/{asset_id}/index-status`，只暴露有限状态与安全 reason code。
2. 按 `operation_id` 检查 Durable Operation、attempt/reconciliation 计数与 DLQ。
3. 按 `embedding_record_id` 检查当前 operation epoch、`write_generation`、Rights identity。
4. 对 upsert unknown 使用精确 collection/PK/input/spec/generation proof；不得按相似度判断。
5. 删除只允许 generation-specific identity；不执行按 asset 或 collection 的宽泛删除。

生产 Index Worker 必须使用 mounted Alibaba API key 与 Milvus token。任何日志、错误、DLQ 或状态 API 都不得包含临时 URL、required headers、API key 或 Milvus token。
