# 02 — Durable Operations and recovery control plane

**What to build:** Introduce one generic Durable Operation lifecycle for asset validation,
ProductBrief analysis, indexing, deletion, reconciliation, and rebuild work. Operators must be able
to inspect and replay dead letters, while Scheduler scanners recover expired leases and ready retries
without one scanner failure stopping unrelated maintenance work.

**Blocked by:** 01 — Versioned event routing and single retry authority.

**Status:** complete

- [x] Durable Operations have explicit state, target identity/version, input hash, lease, retry, reconciliation, error, and optimistic-version invariants.
- [x] A logical uniqueness contract prevents duplicate operations for the same kind, target version, and input.
- [x] Claim, start, retry, reconcile, succeed, fail, and cancel transitions are enforced by the domain model.
- [x] All operation and DLQ timestamps use UTC `DATETIME(6)`.
- [x] External work is impossible while the Unit of Work transaction is active.
- [x] Recovery uses `SKIP LOCKED`, exact lease boundaries, bounded batches, and no duplicate unpublished recovery event.
- [x] DLQ read and replay operations record actor, reason, replay time, and repeated failure history.
- [x] Scheduler scanners execute independently and expose per-scanner status and errors.
- [x] HTTP operator endpoints use stable error envelopes, workspace or admin scope, and idempotency where required.
- [x] Concurrent claims, exact retry time, expired lease, unknown outcome, replay, and scanner isolation are proven with MySQL integration tests.

## Comments

- Implemented one generic Durable Operation aggregate and separate typed application/MySQL UoW,
  preserving the existing Outbox, Inbox, Retry authority, Lease conventions, and DLQ.
- Added v1 recovery/replay contracts, `SKIP LOCKED` recovery, explicit unknown-outcome
  reconciliation with bounded backoff and elapsed budgets, atomic terminal-operation DLQ records,
  append-only replay ancestry, and fair recovery selection under published or unpublished backlog.
- Operator HTTP routes now fail closed behind a signed trusted-principal adapter with explicit
  current/previous keyed HMAC rotation, workspace membership, and workspace-admin policy. Seeded
  migration tests prove deterministic workspace backfill plus read-only system-admin handling for
  genuinely orphaned legacy records.
- Scheduler scanners have independent bounded execution and timeout health. Worker startup requires
  explicit executor registration for configured operation kinds, and the real-MySQL matrix exercises
  all six kinds, retry-through-success, reconciliation completion, and forced `SKIP LOCKED` overlap.
- Recovery Generation remains outstanding through failed Inbox handling, while expired execution
  leases can still advance to reconciliation without duplicate events. Replay attempts and direct
  child failures expose independent bounded continuation cursors.
- Recovery-event replay now distinguishes transport DLQs from the operation's current terminal
  DLQ. Transport replay atomically consumes only its outstanding recovery generation, while a
  current terminal replay grants exactly one additional execution or reconciliation attempt.
  Transport replay ancestry remains on the operation without changing either retry budget, and
  any later terminal event and child DLQ link back to that source transport dead letter. Durable
  replay records now advance explicitly through `RECORDED`, `PREPARED`, `CLAIMED`, and `COMPLETED`.
  Preparation and the operation budget/generation change commit atomically; claiming atomically
  records the exact Operation Lease Token and enters `RUNNING` or acquires the reconciliation Lease.
  Recovery-generation consumption and late Provider provenance can advance the Operation Version
  without changing replay claimability. Real-MySQL crash, concurrent-delivery, and stale-token
  probes prove one claimant and one Provider call without version-offset inference. Execution and
  reconciliation settlement plus expired-Lease recovery complete any matching replay claim
  atomically and clear its active token, so a crash after `CLAIMED` cannot strand the lifecycle.
- Replay idempotency uses a versioned fixed-length Scope containing the full Workspace SHA-256 and
  readable Dead Letter ID. Real-MySQL tests cover Workspace lengths 1 and 128, duplicate keys,
  alternate keys, and cross-Workspace isolation.
- Signed trusted principals reject blank or over-128-character Actor IDs before persistence. The
  bound counts Unicode characters consistently with contracts and MySQL `VARCHAR(128)`.
- Workspace IDs are exact opaque ASCII tokens matching
  `^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$`; case and spelling are identity-bearing and are never
  trimmed, Unicode-normalized, or case-folded. The shared validator is enforced at HTTP headers,
  trusted claims, configuration/application commands, contracts, domain construction, and SQL
  bind boundaries. Every Phase 1/2 Workspace column and embedded Idempotency Scope also uses
  `utf8mb4_0900_bin` as database defense in depth.
- Migration preflight rejects existing nonconforming Workspace identities instead of silently
  rewriting tenants. Legacy payload backfill guards malformed JSON with `JSON_VALID`, validates
  the original extracted string, and copies valid ASCII byte-for-character; surrounding
  whitespace, tabs/newlines, Unicode, non-strings, oversized values, and malformed JSON remain
  `NULL` legacy. The MySQL predicate also rejects the engine's end-of-string newline regex edge.
- Workspace-scoped parent keys and composite foreign keys bind every Ticket 02 ownership edge:
  Dead Letter self-source, Outbox source DLQ, Durable Operation terminal/replay source DLQs,
  replay source DLQ/event/operation, and replay lifecycle operation/source/event. Workspace-first
  indexes, nullable-provenance checks, orphan/mismatch preflight, and real-MySQL insert/update
  tests prove same-Workspace success and cross-Workspace rejection.
- Raw Uvicorn/ASGI wire tests reject CJK, NFC/NFD accents, Latin-1, whitespace, and controls before
  comparison or persistence, while 1- and 128-character valid ASCII identities succeed across
  Workflow, Catalog, and operator surfaces.
- Dead Letter detail and replay paths accept only exact hyphenated ASCII UUID text before any
  database lookup. Uppercase hexadecimal is normalized to lowercase at both HTTP and Application
  boundaries; repository lookups add binary comparison. Accent, NFC/NFD, fullwidth, zero-width,
  whitespace, malformed, and extra-character aliases return the same non-enumerating envelope as
  a missing or cross-Workspace record. Uppercase/lowercase replay requests share one durable
  identity and idempotency Scope.
- Revision `b1c8e4f2a703` owns a frozen copy of the Workspace regex and SQL validation behavior;
  it no longer imports mutable runtime identity policy. Source and behavior regressions prove a
  future runtime regex change cannot alter this historical migration.
- Regression coverage now exercises every same/unrelated replay owner by reconciliation
  success/failure combination, repeated settlement immutability, literal full Workspace SHA-256
  replay scopes, and persistence of 128-character ASCII and CJK Actor IDs.
- Shared database execution and flush boundaries classify immediate MySQL INSERT, UPDATE, and
  DELETE failures consistently across operation, operator, generic, and catalog repositories.
  Unique, reference, invalid-data, catalog duplicate, and optimistic-concurrency semantics remain
  distinct and map to stable API-safe errors.
- Final `code-review` (Standards and Spec) and five-axis `code-review-and-quality` verdicts are
  `APPROVED`, with no Critical or Required findings.
- Final verification: focused operator/MySQL/Uvicorn/migration matrix 78 passed; full Python suite
  302 passed; Ruff,
  Alembic drift, seeded upgrade/downgrade/upgrade, Python and pnpm audits, Phase 0/1 checks, OpenAPI
  and generated TypeScript contracts, Web lint/typecheck/build plus 9 Playwright tests, Compose
  config and health, seven container builds, gitleaks, and source SBOM generation all passed.
