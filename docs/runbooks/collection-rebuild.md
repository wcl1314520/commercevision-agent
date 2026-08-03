# Collection 重建与升级 Runbook

| 属性 | 值 |
|---|---|
| 状态 | active |
| 最后更新 | 2026-08-04 |
| 所有者 | Index Worker / Workspace Administrator |

## 安全边界

- MySQL 的 Collection Registry、Retrieval Policy pointer、Embedding Record 和当前 Rights 是权威；
  Milvus 只保存可重建派生向量。
- 只能为同一 embedding identity 升级 schema/index spec。模型、pinned revision 或 dimension 变化必须
  走新的 embedding spec 与重新索引流程。
- 重建只写入绑定 rebuild ID 的非活动候选；不得清空、删除或原地修改活动 Collection。
- 只有 Workspace Administrator 可以请求、验证或激活。公开状态不暴露物理 Collection 名、凭据或向量。

## 标准流程

1. 读取当前 Collection 与 Retrieval Policy pointer 的版本，提交 `POST /api/v1/collections/rebuilds`；
   使用稳定 `Idempotency-Key`，网络结果未知时原键重放。
2. Worker 创建候选并按 MySQL snapshot watermark/keyset cursor 分批回填。管理页展示持久化状态与进度，
   Worker 重启后应从 cursor 继续，而不是重新清空候选。
3. 流程自动重放快照后的相关 Outbox 事实并执行当前 Rights 全量复扫。到达 `AWAITING_VALIDATION` 后请求验证。
4. 仅当行数、主键集合、抽样可见性、exact-versus-ANN recall、固定查询和 unauthorized 计数全部通过，
   状态才进入 `READY`。失败候选不得激活。
5. 以响应中的最新 rebuild version 激活。事务会再次检查源/指针版本和 validation watermark 后的事件；
   若出现迟到事实，状态退回 `REPLAYING`，必须等待再次验证，不能强制切换。
6. 成功切换后旧 Collection 为只读 `RETIRING`。达到 `CV_COLLECTION_REBUILD_RETIREMENT_DELAY_SECONDS`
   后由 Worker 删除记录的旧物理集合并进入 `RETIRED`。

## 故障处置

- `PROVISIONING/BACKFILLING/REPLAYING/RIGHTS_RESCAN` 停滞：先检查 `commercevision.index` queue、
  Worker readiness、Inbox/DLQ 和最近 progress code；修复依赖后重放原消息，不创建并行 rebuild。
- 候选被误删：不要操作活动 Collection。下一批 Worker 会按不可变 spec 重建候选并从 MySQL cursor 恢复。
- `VALIDATION_REJECTED`：检查 missing/unexpected PK、visibility、recall 和 unauthorized 指标；候选保持非活动，
  修复权威事实或实现后创建新的 rebuild，不手工改 validation JSON。
- 激活返回 `REPLAYING`：这是 validation watermark 后发现新事实的正常安全收敛；等待 replay、rights rescan
  和新验证完成，再使用新 version 激活。
- `RETIRING` 超期：确认 pointer 仍指向候选且候选 read-enabled，再检查延迟命令/DLQ。禁止手工按名称猜测删除；
  只允许处理 rebuild 记录的 source Collection。

## 发布验收

- Alembic upgrade、schema drift、downgrade/re-upgrade 通过。
- 真实 MySQL + Milvus 覆盖候选删除恢复、跨批次重启、同微秒水位事件、原子 pointer 切换和延迟退役。
- 未知/未授权候选向量使验证失败，在线 pointer 保持旧 Collection。
- Web proxy allowlist、管理员权限、OpenAPI、配置边界和依赖审计通过。
