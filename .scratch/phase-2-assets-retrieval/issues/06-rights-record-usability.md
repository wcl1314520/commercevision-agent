# 06 — Rights Records and current usability

**What to build:** Add immutable, versioned Rights Records and one authoritative current-usability
decision. Users can register, replace, revoke, and inspect rights in the Web workbench, while every
provider or retrieval use is denied unless workspace, time, purpose, provider, and derivative
requirements pass in MySQL.

**Blocked by:** 05 — Multi-kind asset validation pipeline.

**Status:** ready-for-agent

- [ ] Rights Records are append-only, versioned, and linked through an atomic current pointer on the Asset aggregate.
- [ ] Allowed uses and allowed providers are normalized and indexed rather than hidden only in JSON.
- [ ] Validity uses the exclusive upper bound `valid_until`; perpetual rights require an explicit policy flag.
- [ ] Empty use or provider sets deny use.
- [ ] The current usability decision returns the exact Rights Record version and stable reason code.
- [ ] Asset becomes available only after mandatory validation passes and current rights grant the required use.
- [ ] Replacement, revocation, expiry, and administrator blocking stop use in MySQL in the same transaction that emits cleanup or repair events.
- [ ] Concurrent rights replacement uses aggregate locking and optimistic versions without duplicate version numbers.
- [ ] HTTP and Web flows support registration, replacement, revocation, history, evidence, and visible deny-by-default permissions.
- [ ] Cross-workspace reads and mutations are indistinguishable from not found.
- [ ] Exact validity boundaries, concurrent replacement, provider denial, derivative denial, and immediate retrieval blocking are proven with MySQL integration tests.

