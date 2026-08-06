# 03 — Provider control plane and MySQL authority

**What to build:** Persist Provider identities, immutable endpoint capability versions/current
pointers, Route Policies, circuits and quota observations with transactional publish/rollback.

**Blocked by:** 01.

**Status:** ready-for-agent

- [ ] Alembic migrations use workspace-first tenant identities where applicable, binary-exact IDs, `DATETIME(6)` and `DECIMAL(20,6)` money.
- [ ] Publishing appends an immutable version and CAS-advances a current pointer; rollback moves only the pointer.
- [ ] Runtime model discovery creates review candidates and never mutates live capability or traffic automatically.
- [ ] Secret references are opaque administrator-only metadata; raw credentials are rejected and never returned.
- [ ] Route policy and circuit/quota mutations are auditable, idempotent and concurrency-safe.
- [ ] Empty/upgrade/downgrade/re-upgrade, drift, immutability and real-MySQL concurrency tests pass.

## Comments

- Ticket 01 routing contracts and Ticket 02 generation/candidate/call/usage contracts are complete;
  the control-plane persistence seam is ready for its first migration-level RED.
