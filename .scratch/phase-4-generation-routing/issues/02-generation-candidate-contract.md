# 02 — Generation batch, candidate slot, and usage contract

**What to build:** Add immutable Generation Batch, Candidate Slot, Candidate Image, Provider Call,
and Usage Record contracts plus deterministic logical identities and lifecycle invariants.

**Blocked by:** 01.

**Status:** complete

- [x] One batch binds exact Workflow, Plan, Approval, direction, authorized Tool Intent, policy versions, retention and actor facts.
- [x] Candidate indexes are contiguous and bounded; one slot derives one stable idempotency identity and owns one Durable Operation.
- [x] Generation and editing are distinct typed requests; editing binds exact source Asset Version and approved repair/mask scope.
- [x] Candidate Image is immutable and references a controlled Task Asset Version rather than a Provider URL.
- [x] Usage is append-only, decimal/currency-safe, call-identity deduplicated, and keeps estimated/configured/final evidence separate.
- [x] Unit tests freeze canonical identities, bounded collections, lifecycle transitions and forbidden authority fields.

## Comments

- Ticket 01 exact implementation CI is green, so this ticket is unblocked and started on
  2026-08-06.
- First public RED freezes one immutable generation batch with three candidates. The only observed
  behavior is `create_candidate_slots(batch, durable_operation_ids)` from the domain root: it must
  return exactly indexes `0..2`, bind one `IMAGE_GENERATION` Durable Operation per slot, and derive
  stable unique logical/idempotency identities from batch authority rather than call order or
  Provider data.
- Five RED/GREEN slices added the public domain contracts without persistence or transport coupling:
  immutable batches/slots, distinct generation/editing authority, converged Candidate Image facts,
  typed Provider Call outcomes, and append-only Usage Records.
- Batch canonical identity now includes exact prompt/context, authorized Asset Version order, edit
  source/mask/scope, policy versions, and Workflow/Rights deadlines. Request binding rejects kind,
  slot, prompt/context, reference, source/mask, or repair-scope divergence.
- Unknown Provider outcomes require possible dispatch and prohibit automatic resubmission; only a
  confirmed pre-dispatch failure is resubmission-safe. Candidate Slot intentionally has no second
  lifecycle state machine beyond its Durable Operation.
- Usage accepts only bounded `Decimal` values and uppercase currency, verifies configured estimate
  arithmetic, separates Provider/pricing/final evidence, remains unresolved when usage is missing,
  and deduplicates by exact Provider Call identity.
- Public tests: `23 passed`; related domain regression: `138 passed`; full unit/contract:
  `1513 passed, 1 skipped`. Full Ruff format/check, strict touched-code Mypy, workspace Mypy
  baseline and Python license policy pass. OpenAPI was regenerated idempotently; its only intended
  change is adding `IMAGE_GENERATION` and `IMAGE_EDITING` to `OperationKind`.
