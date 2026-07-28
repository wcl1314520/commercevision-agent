# 06 — Rights Records and current usability

**What to build:** Add immutable, versioned Rights Records and one authoritative current-usability
decision. Users can register, replace, revoke, and inspect rights in the Web workbench, while every
provider or retrieval use is denied unless workspace, time, purpose, provider, and derivative
requirements pass in MySQL.

**Blocked by:** 05 — Multi-kind asset validation pipeline.

**Status:** complete

- [x] Rights Records are append-only, versioned, and linked through an atomic current pointer on the Asset aggregate.
- [x] Allowed uses and allowed providers are normalized and indexed rather than hidden only in JSON.
- [x] Validity uses the exclusive upper bound `valid_until`; perpetual rights require an explicit policy flag.
- [x] Empty use or provider sets deny use.
- [x] The current usability decision returns the exact Rights Record version and stable reason code.
- [x] Asset becomes available only after mandatory validation passes and current rights grant the required use.
- [x] Replacement, revocation, expiry, and administrator blocking stop use in MySQL in the same transaction that emits cleanup or repair events.
- [x] Concurrent rights replacement uses aggregate locking and optimistic versions without duplicate version numbers.
- [x] HTTP and Web flows support registration, replacement, revocation, history, evidence, and visible deny-by-default permissions.
- [x] Cross-workspace reads and mutations are indistinguishable from not found.
- [x] Exact validity boundaries, concurrent replacement, provider denial, derivative denial, and immediate retrieval blocking are proven with MySQL integration tests.
