# 09 — Generation queue, Worker execution, and candidate convergence

**What to build:** Add an isolated generation queue and Durable Operation executor that dispatches
outside transactions, validates result bytes, persists assets/candidates/usage and resumes Workflow.

**Blocked by:** 02, 05, 06.

**Status:** complete

## Comments

- 2026-08-06: Blockers 02/05/06 are complete, and Ticket 08 implementation commit
  `16f18960c06db9abc19626fe7af70fb27ee70619` passed exact GitHub Actions run `31110859233`
  in all four jobs. Ticket 09 is unblocked. Its first RED will exercise the existing public
  Outbox/Inbox -> Worker -> Durable Operation executor seam; no parallel queue or job framework
  will be introduced.
- 2026-08-06: Ticket 08 release-state commit `595a4c825baa58a88bc0a12b7d57fa8838af3e4e`
  passed exact GitHub Actions run `31113124705` in all four jobs. RED 1 then failed at the intended
  public seam because `EventQueue.GENERATION` did not exist. Minimal GREEN adds the isolated queue,
  shared Scheduler/Worker route, strict workspace/batch identity fences, and one call into the
  existing Durable Operation Worker. Focused routing/settings/deployment regression is `192 passed`.
- 2026-08-06: RED/GREEN 2 adds the generation Operation executor seam. Current authority denial is
  mapped to a stable non-retryable error before any Provider dispatch. The authority snapshot and
  ports live in application so persistence does not reverse-depend on Worker; Provider success and
  reconciliation remain deliberately unimplemented until their own REDs.
- 2026-08-06: RED/GREEN 3 closes the immutable dispatch-input blocker. Model Route Decisions now
  retain a strict credential-free canonical request projection under migration `fb9e4c6a1205`;
  legacy missing projections remain nullable for fail-closed reads. Real MySQL migration and route
  persistence tests are green, with the original request hash unchanged.
- 2026-08-06: RED/GREEN 4 adds `MySqlGenerationDispatchAuthority`. It reuses the approved-plan
  authority under canonical lock order, validates the running Operation/lease, batch, slot, plan,
  route projection and endpoint hash, closes MySQL, then invokes the request builder. Real MySQL
  proves cancellation denies before both builder and Provider, while the authorized path proves
  builder/dispatch run with no active unit of work.
- 2026-08-06: RED/GREEN 5 adds the versioned `creative-plan-image.v1` structured request builder.
  It accepts no arbitrary prompt/model/URL/credential, derives the Provider idempotency key from
  the Durable Operation, and binds exact dimensions, format, reference count and earliest deadline.
- 2026-08-06: RED/GREEN 6 extends the existing Operation completion protocol for atomic target
  convergence. The active lease is internal and repr-redacted; an executor may report an already
  committed completion only when a MySQL reread proves the exact SUCCEEDED output and Provider
  identity. A false claim remains RUNNING and raises a concurrency error instead of being trusted.
- 2026-08-06: RED/GREEN 7–9 add the controlled TASK-object write and migration
  `ad4e6b8c1206`, then converge Provider Call, Asset/Asset Version, Candidate Image, Usage, audit,
  Candidate Ready event and Operation success in one MySQL transaction. A durable dispatch-attempt
  fence is written before submit; a crash after possible dispatch cannot grant another submit.
- 2026-08-06: RED/GREEN 10 closes public seam 5. Candidate Ready is only a notification: the
  Workflow Worker rereads the exact batch/slot/operation/candidate/asset/usage/current-plan facts,
  waits for every slot to succeed, and starts `evaluate_results` in a batch-specific LangGraph
  checkpoint generation. Restart after the evaluation-node claim reuses the same MySQL authority;
  duplicate events after Workflow advancement become safe no-ops. Fresh-schema generation,
  migration and restart/fault regression is `79 passed`.
- 2026-08-07: Release review keeps the production Compose subscription fail-closed. The isolated
  queue and injectable executor seam are complete, but Compose must not subscribe to generation or
  advertise `IMAGE_GENERATION` readiness until Tickets 10/11 provide reconciliation and mandatory
  result-admission composition. Enabling only the queue would consume paid work with an incomplete
  recovery/safety plane. Ticket 09 tests route through the same Worker/Inbox/Operation boundary with
  deterministic injected collaborators; the later deployment gate must enable queue, executor and
  readiness atomically.
- 2026-08-07: Five-axis release review closed two additional integrity gaps with RED/GREEN evidence:
  generated media writes now reject every non-TASK destination before object I/O, and an authorized
  dispatch derives its request hash from the exact structured Provider request instead of accepting
  caller-supplied duplicate state. Object-storage/dispatcher regression is `79 passed`; final full
  unit+contract is `1673 passed, 1 skipped`, and the fresh real-MySQL Ticket 09 matrix is `105 passed`.
- 2026-08-07: Final local gates are green: Ruff format/check over `512` files, strict touched-code
  Mypy, full-workspace Mypy baseline reduced to `426` with zero drift, OpenAPI/Web generated types,
  Web typecheck/lint and `224` unit tests, lock/diff, Python licenses and vulnerability audit. Added
  diff secret-pattern scan found zero credentials; no live Provider call or user credential was used.
- 2026-08-07: Release hardening fixed the full-suite migration table registry in
  `d5b49b4522c901936d705292a61b6aed15c629ce`, pinned the patched `js-yaml==4.3.1` workspace
  resolution in `9caed1bd3e7354d29cb56a791a2613a4c35b8be1`, and classified 13 historical deterministic
  idempotency fixtures with exact Gitleaks fingerprints in
  `e092732e7c2125711c7de7fb934c524392b35a2f`. GitHub Actions run `31128782236` then passed
  Python checks, Web checks, Container builds, and Security/SBOM on that exact final SHA. Ticket 09
  is complete; Ticket 10's dependency is unlocked, while its first production RED remains gated on
  the release-state evidence commit receiving the same exact-SHA CI proof.

- [x] Existing Worker/registry/lease/Outbox/Inbox/readiness framework owns the queue; no parallel job system or service framework is introduced.
- [x] Authority is rechecked before dispatch and before late result availability.
- [x] Provider calls and downloads occur outside MySQL transactions with bounded leases/deadlines.
- [x] Result transfer enforces exact-host SSRF, redirects, MIME, bytes, pixels, decompression and hash validation before controlled object persistence.
- [x] Asset Version, Candidate Image, Usage, audit, event and Operation success converge atomically or remain unavailable and recoverable.
- [x] Real-infrastructure fault tests cover crashes around dispatch/download/object/commit and prove one effective candidate/usage identity.
