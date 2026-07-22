# 02 — Durable Operations and recovery control plane

**What to build:** Introduce one generic Durable Operation lifecycle for asset validation,
ProductBrief analysis, indexing, deletion, reconciliation, and rebuild work. Operators must be able
to inspect and replay dead letters, while Scheduler scanners recover expired leases and ready retries
without one scanner failure stopping unrelated maintenance work.

**Blocked by:** 01 — Versioned event routing and single retry authority.

**Status:** ready-for-agent

- [ ] Durable Operations have explicit state, target identity/version, input hash, lease, retry, reconciliation, error, and optimistic-version invariants.
- [ ] A logical uniqueness contract prevents duplicate operations for the same kind, target version, and input.
- [ ] Claim, start, retry, reconcile, succeed, fail, and cancel transitions are enforced by the domain model.
- [ ] All operation and DLQ timestamps use UTC `DATETIME(6)`.
- [ ] External work is impossible while the Unit of Work transaction is active.
- [ ] Recovery uses `SKIP LOCKED`, exact lease boundaries, bounded batches, and no duplicate unpublished recovery event.
- [ ] DLQ read and replay operations record actor, reason, replay time, and repeated failure history.
- [ ] Scheduler scanners execute independently and expose per-scanner status and errors.
- [ ] HTTP operator endpoints use stable error envelopes, workspace or admin scope, and idempotency where required.
- [ ] Concurrent claims, exact retry time, expired lease, unknown outcome, replay, and scanner isolation are proven with MySQL integration tests.

