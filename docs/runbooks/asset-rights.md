# Asset Rights Runbook

| 属性 | 值 |
|---|---|
| 状态 | authorization verified；external convergence pending Ticket 09/13 |
| 最后更新 | 2026-07-27 |
| 适用版本 | Phase 2 / Ticket 06 |

## 权威与不变量

MySQL 的 Asset current pointer 和不可变 Rights Record 历史是当前可用性的唯一权威。
校验通过只把 Asset 推进到 `PENDING_RIGHTS`；只有当前 Asset Version、用途、Provider、
派生要求和时间窗口全部满足时才能进入并保持 `AVAILABLE`。用途或 Provider 集合为空即
拒绝。`valid_until` 是 exclusive 边界，永久权利必须显式设置 `perpetual=true`。

Milvus、对象存储 metadata、Worker delivery state 和缓存不得授权。每个决定返回稳定
reason code、精确 Rights Record ID/version 和 decision time，供审计与下游日志关联。
当前可用性读取在一个 MySQL 事务中对 Asset/current Rights 快照加共享锁，并以
`UTC_TIMESTAMP(6)` 为最低决策时间。调用方提交的 `decision_time` 只允许把评估推向未来
并产生更保守的拒绝，回填旧时间不能绕过 Rights 或 retention 到期；并发撤销在该读取之后
线性化，下一次读取必须立即看到撤销。

## 变更与收敛

登记、替换、撤销、到期和管理员阻断在同一 MySQL 事务内完成 Asset 状态/current pointer、
Audit 和 Durable Outbox 写入。Ticket 06 的 Worker 只关闭式验证并持久确认这些 observation；
Ticket 09 和 Ticket 13 将接入实际向量重建与删除，并在完成后把本 Runbook 状态提升为
`verified`。最终消费者必须幂等处理：

| `required_convergence` | 必须动作 |
|---|---|
| `REINDEX` | 用 MySQL 当前决定重新建立或移除检索事实 |
| `REMOVE_EXTERNAL_DERIVATIVES` | 先禁止检索，再清理 Milvus、缓存和已物化派生物 |

在 Ticket 09/13 完成前，不得把 observation 已处理等同于外部收敛完成。最终实现中任何
cleanup/repair 失败都必须保留 Durable Operation 重试或进入 DLQ，不能回滚 MySQL 已生效的
阻断。消费者处理旧事件时必须重新读取当前 Asset/Rights version，禁止按旧 payload 恢复授权。

## 到期扫描

Scheduler 使用 `CV_RIGHTS_EXPIRY_SCAN_INTERVAL_SECONDS` 分别运行 activation 和 expiry
扫描。activation 以数据库锁认领 `valid_from <= now` 且尚未过期的当前 GRANT，原子推进
为 `AVAILABLE`（许可集合为空时仍拒绝）并发布 repair 事件；expiry 认领
`valid_until <= now` 的当前 GRANT，即使短有效期窗口从未进入 `AVAILABLE`，也会原子推进到
`RIGHTS_EXPIRED` 并发布 removal 事件。领取条件、状态时间、Outbox 时间和 Audit 时间全部
来自同一条 MySQL 查询采样的 `UTC_TIMESTAMP(6)`，不能使用 Scheduler 节点时钟。扫描逐条
事务提交，避免一条失败回滚整个批次；activation 在提交边界再用新的
`UTC_TIMESTAMP(6)` 检查 Rights 与 retention，防止领取后跨过失效边界。数据库时间列全部
是 `DATETIME(6)`，边界微秒不可截断或四舍五入。

部署后确认：

1. Scheduler readiness 中 `activated_rights_total` 和 `expired_rights_total` 持续可读。
2. 到期 Asset 在边界时立即变为 `RIGHTS_EXPIRED`。
3. 对应 Outbox 事件最终进入 processed 状态，重复投递结果为 duplicate。
4. Milvus/缓存中不存在该 Asset 的可召回事实。

## 故障处置

并发登记或替换返回 `VERSION_CONFLICT` 时重新读取 Asset 和完整 Rights history，再由操作人
决定是否基于新版本提交。跨 Workspace ID 与不存在 ID 都返回相同 404，不能通过运维查询
向未授权调用者确认资源存在。

若发现 Asset current pointer 与 Rights history 不一致，立即管理员阻断 Asset，保留数据库
和 Audit evidence，发布 removal convergence，然后从 append-only history 与 Outbox 重建。
不得直接 UPDATE/DELETE Rights 表、禁用 immutable trigger，或从 Milvus 反向写回授权。
不得向已设置 `permissions_sealed_at` 的 Rights Record 追加 use/provider 行；数据库触发器
会拒绝该操作。发现未封存记录表示事务或手工写入违规，应先阻断 Asset，再从 Audit/Outbox
确认来源，不得通过手工补行恢复授权。

Web BFF 默认不签发管理员权限。私有部署只有在真实身份入口完成管理员认证后，才可将
对应 Workspace 加入 `CV_WEB_ADMIN_WORKSPACE_IDS`；该集合必须是
`CV_WEB_ALLOWED_WORKSPACE_IDS` 的子集。公开 Demo 保持空集合，管理员阻断通过受保护的
运维入口执行。Web 从 `/api/web-capabilities` 读取同一份服务端配置以决定是否展示管理员
控件，但 API 的签名 Principal 校验仍是唯一授权边界。

Web 展示的允许/拒绝结果是带 `decided_at` 的授权快照，不是持续授权。允许结果在 Rights
`valid_until` 或 Task Asset retention deadline 到达时自动清除；窗口重新获得焦点时先清除
旧结果，再刷新 Asset 与 Rights history。任何实际 Provider 调用仍必须重新请求 MySQL
当前可用性决定，不能复用界面中的绿色状态。

边界倒计时使用服务端 `decided_at` 到服务端 Rights/retention 边界的差值，并扣除浏览器
从请求发起起算的单调时钟耗时；不得用浏览器 `Date.now()` 判断数据库授权是否到期。倒计时
到达时先撤销旧快照并重新请求服务端决定，页面休眠后重新获得焦点同样执行失效与刷新。

若写入返回 `VERSION_CONFLICT`，Web 必须冻结本地 Rights 草稿并刷新最新 Asset/history，
不能把旧用途或 Provider 集合直接套用到新 Asset version。操作人只能显式选择“载入最新
权利并放弃本地草稿”，然后基于新记录重新编辑提交；刷新失败时该操作保持禁用。
