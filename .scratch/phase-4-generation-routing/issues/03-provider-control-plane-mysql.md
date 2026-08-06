# 03 — Provider control plane and MySQL authority

**What to build:** Persist Provider identities, immutable endpoint capability versions/current
pointers, Route Policies, circuits and quota observations with transactional publish/rollback.

**Blocked by:** 01.

**Status:** complete

- [x] Alembic migrations use workspace-first tenant identities where applicable, binary-exact IDs, `DATETIME(6)` and `DECIMAL(20,6)` money.
- [x] Publishing appends an immutable version and CAS-advances a current pointer; rollback moves only the pointer.
- [x] Runtime model discovery creates review candidates and never mutates live capability or traffic automatically.
- [x] Secret references are opaque administrator-only metadata; raw credentials are rejected and never returned.
- [x] Route policy and circuit/quota mutations are auditable, idempotent and concurrency-safe.
- [x] Empty/upgrade/downgrade/re-upgrade, drift, immutability and real-MySQL concurrency tests pass.

## Comments

- Implementation commit `b5bd24ae86730dc38d94646c41c3ae0ebb839a4a` is green in exact
  GitHub Actions run `31077517988`; Python, Web, Container builds, Gitleaks and SBOM all passed.
  Ticket 03 is complete and Ticket 04 is unblocked.

- Ticket 01 routing contracts and Ticket 02 generation/candidate/call/usage contracts are complete;
  the control-plane persistence seam is ready for its first migration-level RED.
- First RED targets the public control-plane domain command: publish must CAS-advance an endpoint
  current pointer, rollback must move only that pointer, and stale expected versions must fail
  without mutating either immutable capability version.
- First GREEN adds a narrow endpoint capability head aggregate with separate current/latest version
  counters and optimistic versioning. A follow-up RED exposed downgrade/re-upgrade semantics;
  rollback now selects any different already-published version without appending or rewriting it.
- The application seam now owns idempotent/audited Provider registration, capability and Route
  Policy publish/rollback, review-only discovery candidate ingestion, and immutable endpoint
  observation append. Responses and audit metadata exclude Secret References; raw credential-like
  values and discovery evidence fields are rejected at the domain boundary.
- Alembic adds seven control-plane tables and immutable UPDATE triggers for capability versions,
  Route Policy versions, and endpoint observations. SQLAlchemy mappings match the migration with
  no Alembic drift; empty upgrade, downgrade, re-upgrade and direct immutable-row rejection pass on
  real MySQL.
- Real-MySQL public-seam coverage passes idempotent replay/conflict, downgrade/re-upgrade, discovery
  non-activation, circuit/quota observation append and a two-thread CAS race with exactly one
  capability publisher. Targeted Ticket 03 suite: `9 passed`; Ruff, touched-code Mypy, 431-entry
  workspace Mypy baseline and `git diff --check` pass. Exact-SHA CI remains the completion gate.
