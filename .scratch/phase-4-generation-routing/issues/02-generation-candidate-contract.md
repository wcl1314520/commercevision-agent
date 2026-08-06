# 02 — Generation batch, candidate slot, and usage contract

**What to build:** Add immutable Generation Batch, Candidate Slot, Candidate Image, Provider Call,
and Usage Record contracts plus deterministic logical identities and lifecycle invariants.

**Blocked by:** 01.

**Status:** pending

- [ ] One batch binds exact Workflow, Plan, Approval, direction, authorized Tool Intent, policy versions, retention and actor facts.
- [ ] Candidate indexes are contiguous and bounded; one slot derives one stable idempotency identity and owns one Durable Operation.
- [ ] Generation and editing are distinct typed requests; editing binds exact source Asset Version and approved repair/mask scope.
- [ ] Candidate Image is immutable and references a controlled Task Asset Version rather than a Provider URL.
- [ ] Usage is append-only, decimal/currency-safe, call-identity deduplicated, and keeps estimated/configured/final evidence separate.
- [ ] Unit tests freeze canonical identities, bounded collections, lifecycle transitions and forbidden authority fields.
