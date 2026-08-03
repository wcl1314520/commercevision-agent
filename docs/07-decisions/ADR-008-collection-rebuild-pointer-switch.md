# ADR-008：Collection 升级采用候选构建与原子策略指针切换

| 属性 | 值 |
|---|---|
| 状态 | accepted |
| 日期 | 2026-08-04 |

## 背景

Milvus 是可重建的检索派生层，MySQL 才保存资产、权利、索引记录和检索路由的权威事实。直接清空或
原地改造活动 Collection 会把长时间回填、进程中断、权益变化和索引参数升级暴露给在线检索，且无法
在失败时保持旧路径可用。仅在 Worker 内保存游标也不能跨进程恢复或证明验证后没有新的授权变化。

## 决策

- 每次升级创建绑定不可变 Collection Spec 与 rebuild identity 的非活动物理候选；活动 Collection
  在切换前只读写自身，重建流程不得清空、删除或覆盖它。
- 重建是独立的持久聚合，使用 MySQL 状态、代次、快照水位、稳定 keyset cursor、placement 和
  Outbox/Inbox 检查点恢复；它不复用通用 Durable Operation 状态机，避免两个生命周期互相驱动。
- 回填完成后，按 `(occurred_at, event_id)` 重放快照水位之后的相关事实，再按 MySQL 当前权利执行
  全量资格复扫。候选写入和删除均使用确定性向量身份与 generation fence。
- 验证开始时先持久化 validation watermark 并进入 `VALIDATING`，然后检查行数、主键集合、抽样可见性、
  exact-versus-ANN recall、固定查询通过率和未授权结果。验证开始后的相关事件会阻止直接激活并使流程
  回到 replay，关闭验证扫描期间的竞态窗口。
- 激活在一个 MySQL 事务中锁定 rebuild、源 Collection、候选 Collection 与 Retrieval Policy pointer；
  只有版本和验证证据仍匹配时才切换指针。旧 Collection 进入只读 `RETIRING`，配置延迟结束后才删除
  物理集合并标记 `RETIRED`。

## 后果

- 在线检索始终只通过 Retrieval Policy pointer 解析活动 Collection，不从多个 `ACTIVE` 状态行猜测路由。
- 升级需要临时双份向量容量，并增加一次当前权利复扫与固定质量验证，但中断和验证失败不会破坏在线路径。
- 候选被误删后，下一有界批次会按不可变规格重新创建并从持久游标恢复；旧活动集合继续服务。
- 延迟退役期间旧集合保持可回查，物理删除只接受 rebuild 记录的源集合名，不能指向当前候选。

## 验证

- 真实 MySQL migration 执行 upgrade、schema drift、downgrade/re-upgrade。
- 真实 Milvus 测试覆盖候选删除、跨多个进程边界恢复、回放、权利复扫、验证、原子切换和延迟退役。
- 向候选注入未知向量必须使验证失败，并报告 unexpected/unauthorized 计数；不得切换策略指针。
- API 与 Web 只向 Workspace 管理员开放请求、验证和激活，并展示持久化进度、失败与退役状态。
