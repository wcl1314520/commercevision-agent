# 06 — Image Provider Adapter contract and deterministic adapter

**What to build:** Define the narrow production Adapter port for generation/edit submit/query/cancel
and a deterministic implementation that exercises identical typed outcomes in public CI.

**Blocked by:** 01, 02.

**Status:** complete

- [x] Adapter requests carry only normalized typed media requirements and controlled input handles.
- [x] Outcomes distinguish success, confirmed failure, content rejection, safe pre-dispatch retry and unknown possible dispatch.
- [x] Provider request/task identity, usage, result references and errors are bounded and typed.
- [x] Secrets, arbitrary URLs, business authorization, route choice and Candidate persistence remain outside the Adapter Interface.
- [x] Deterministic adapter supports reproducible success/failure/rejection/unknown/query/cancel fixtures.
- [x] Contract tests run unchanged against deterministic and bounded HTTP adapters.

## Comments

- 2026-08-06: Ticket 01/02 blockers are complete. Ticket 05 exact commit
  `0c87b93ead5b56f62153528d6e5ca1f77ba8915f` passed GitHub Actions
  `31095178065`; Ticket 06 entered TDD at the shared Image Provider Adapter contract seam.
- 2026-08-06: Shared immutable request/outcome/identity/result/usage/error contracts and the deterministic
  sync/async fixture Adapter are locally green. URL/credential rejection, repr redaction, five dispatch
  meanings, reconciliation-safe `NOT_FOUND`, terminal success/cancellation and submit replay are covered.
  The unchanged bounded-HTTP parity box remains open for the Ticket 07 Adapter implementation.
- 2026-08-06: Local release gates are green: unit+contract `1544 passed, 1 skipped`, full Ruff `501 files`,
  strict touched-code Mypy, full-workspace 431-diagnostic baseline, license, lock, vulnerability and diff checks.
- 2026-08-06: Commit `b7621e2277c5ecc52a04928d90941876a7cd3f9e` passed all four GitHub Actions
  jobs in run `31099374416`. The contract is released for Ticket 07; its final unchanged bounded-HTTP
  parity acceptance remains deliberately open and will co-close when that Adapter joins the same suite.
- 2026-08-06: Ticket 07 added the bounded Kuaipao HTTP Adapter to one unchanged parameterized success
  contract beside the deterministic Adapter. Both shared contract suites pass (`68 passed`), so the
  deferred parity acceptance is now closed and Ticket 06 is complete.
