# 04 — Deterministic model router and immutable decisions

**What to build:** Implement the application routing service that loads current server-owned
authority, applies hard filters, scores eligible capabilities and persists one immutable decision.

**Blocked by:** 01, 03.

**Status:** pending

- [ ] Caller supplies trusted requirements and identities only; provider, URL, credential, price and quota authority are resolved server-side.
- [ ] Stable policy inputs and observations produce the same ordered route with stable identity as final tie-breaker.
- [ ] No eligible route returns a stable fail-closed reason without external side effects.
- [ ] Fallback endpoints are fully compatible and stay within original Rights, safety, region and budget ceilings.
- [ ] Decision, audit and idempotency facts commit atomically and replays return the original decision.
- [ ] Real-MySQL tests cover stale pointers, circuit races, quota exhaustion, budget edges and tenant isolation.
