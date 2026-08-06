# 04 — Deterministic model router and immutable decisions

**What to build:** Implement the application routing service that loads current server-owned
authority, applies hard filters, scores eligible capabilities and persists one immutable decision.

**Blocked by:** 01, 03.

**Status:** in_progress

- [x] Caller supplies trusted requirements and identities only; provider, URL, credential, price and quota authority are resolved server-side.
- [x] Stable policy inputs and observations produce the same ordered route with stable identity as final tie-breaker.
- [x] No eligible route returns a stable fail-closed reason without external side effects.
- [x] Fallback endpoints are fully compatible and stay within original Rights, safety, region and budget ceilings.
- [x] Decision, audit and idempotency facts commit atomically and replays return the original decision.
- [x] Real-MySQL tests cover stale pointers, circuit races, quota exhaustion, budget edges and tenant isolation.

## Comments

- Ticket 03 exact-SHA CI is green. The first TDD vertical slice observes only the public
  application command: trusted route requirements plus a policy key enter the Router, while the
  current capability versions, policy and observations come from a server-owned read authority.
- The first RED requires decision persistence, audit and idempotency completion in one unit of
  work; replay must return the original immutable decision without loading mutable current
  authority or appending a second decision/audit fact.
- The completed local slice uses one bounded locking query for latest observations, preserves
  `(observed_at, id)` ordering, and serializes an in-flight OPEN circuit transition before making a
  decision. Empty capability authority returns stable `NO_CURRENT_CAPABILITY` with no business
  side effects.
- The immutable tenant-first decision row binds exact Workflow/Plan/Approval, policy row, selected
  capability, ordered fallback/scores/rejections, scoped idempotency identity, six-place estimated
  cost/currency and database UTC time. Replay cross-checks the completed idempotency response
  against that immutable row and never returns a Secret Reference, endpoint host or credential.
- Local acceptance is complete, including `1521 passed, 1 skipped` unit/contract, strict touched
  Mypy, the 431-diagnostic no-drift baseline, and real-MySQL route/migration/concurrency gates.
  Status remains `in_progress` until the exact implementation SHA is green in GitHub Actions.
