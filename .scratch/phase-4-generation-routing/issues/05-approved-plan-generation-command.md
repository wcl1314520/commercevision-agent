# 05 — Exact approved-plan generation command

**What to build:** Convert authorized Phase 3 Tool Intent decisions into one atomic Generation Batch,
Candidate Slots, IMAGE_GENERATION Durable Operations, audit and Outbox messages.

**Blocked by:** 02, 04.

**Status:** complete

- [x] Command revalidates exact Workflow `GENERATING`, current Creative Plan/Approval, Tool Policy decision, Rights, retention, budget and candidate count in one transaction.
- [x] Duplicate logical requests return the original batch/slots/operations; conflicting reuse fails without partial facts.
- [x] Batch, slots, durable operations, audit, idempotency and Outbox commit atomically.
- [x] Unapproved, rejected, stale, expired, revoked or foreign inputs cannot create dispatchable work.
- [x] Workspace-scoped REST command/read surfaces expose stable conflicts and aggregate provenance without private Provider details.
- [x] Real-MySQL and HTTP tests cover concurrency, rollback boundaries and cross-tenant opacity.

## Comments

- Ticket 04 exact-SHA CI is green. The first TDD vertical slice observes only the public
  application command: an exact Plan/Approval/direction/Tool Intent identity enters the service,
  while current Workflow, Tool Policy, Rights, retention, budget and route authority are loaded
  and locked behind one server-owned Generation authority port.
- The first RED requires one successful command to atomically produce a Generation Batch, its
  deterministic Candidate Slots, one pending IMAGE_GENERATION Durable Operation and dispatch
  Outbox event per slot, a bounded audit record and completed scoped idempotency fact.
- The first three RED/GREEN slices now cover the public command, immutable aggregate replay and
  conflict behavior, typed dispatch events, tenant-first MySQL Batch/Slot tables, exact Route
  Decision binding, logical uniqueness, immutable triggers, and one real-MySQL atomic write/read
  round trip. Focused gates are `33 passed`; Ruff and strict touched-code Mypy are green.
- Acceptance remains open. The next RED is two different idempotency keys concurrently claiming
  the same logical Plan Intent: the unique-key loser must reload and return the winning aggregate,
  never leak a database constraint or create duplicate dispatch work.
- The concurrency RED failed against real MySQL at the logical unique-key flush boundary; GREEN now
  rolls back the loser, reloads the exact winner in one fresh bounded transaction, completes the
  second idempotency fact and returns the same Batch/Slots/Operations. Exact concurrency is green,
  with one Batch/Slot/Operation/dispatch/audit and two completed idempotency records.
- The next RED replaces the injected authority with the production same-transaction MySQL authority:
  Workflow `GENERATING`, current Creative Plan/Approval, Tool Policy authorization, current Rights,
  retention, budget, candidate count and exact Route Decision must all be locked and revalidated.
- Production authority, route-safe asset/candidate projections, current Rights, Tool budget, endpoint
  circuit/quota/freshness, deterministic concurrency convergence, atomic rollback, workspace REST
  command/read, safe provenance and cross-tenant opacity are implemented. Focused release proof is
  `29` unit/API/schema tests plus `17` real-MySQL generation cases; the complete unit/contract suite is
  `1526 passed, 1 skipped` (opt-in live OSS only). Ruff, strict touched-code Mypy, the exact full-workspace
  Mypy baseline, Alembic drift/reversibility, OpenAPI/type drift, security/license and Phase 2/3 release
  acceptance gates are green.
