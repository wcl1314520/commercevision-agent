# 10 — Unknown-outcome reconciliation, compatible failover, and cancellation

**What to build:** Complete the Durable Operation state machine for task query, bounded recovery,
safe compatible failover, non-reconcilable waits, cancellation and explicit DLQ replay.

**Blocked by:** 04, 07, 08, 09.

**Status:** pending

- [ ] First accepted Provider request/task identity is immutable and all queries target that identity.
- [ ] Pending/not-found/query failure/worker restart never authorizes a second submission.
- [ ] Non-reconcilable Kuaipao unknown outcomes enter operator wait and never blind retry or fail over.
- [ ] Confirmed retryable failure may select only the next endpoint from the original compatible route and remaining budget.
- [ ] Policy/content/Rights denial and unknown outcome are terminal to failover; cancellation prevents all late availability.
- [ ] Chaos tests cover leases, polling budget, circuit transitions, duplicate delivery, cancel races and multi-level DLQ replay.
