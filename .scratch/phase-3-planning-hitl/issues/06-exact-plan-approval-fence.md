# 06 — Exact Creative Plan approval and execution fence

**What to build:** Deepen the existing Workflow approval command so Creative Plan approval is bound
to the authoritative current plan version and becomes the only authorization fact for generation.

**Blocked by:** 04 — Creative Plan MySQL authority; 05 — Creative Plan HTTP contract.

**Status:** in_progress

- [x] The shared application Interface loads Workflow and current Creative Plan under the same transaction before accepting a decision.
- [x] Workspace, Workflow, plan identity, plan version, expected Workflow version, and current head must all match exactly.
- [x] Approve transitions to GENERATING; reject returns to PLANNING and requires a later plan version.
- [x] Stale, foreign, superseded, fabricated, or retention-expired subjects fail without Approval, transition, audit, Outbox, or checkpoint side effects.
- [x] Append-only Approval, idempotency, Workflow transition, audit, and resume Outbox commit atomically.
- [x] Duplicate commands and duplicate resume delivery remain idempotent; mismatched key reuse fails.
- [x] The future execution claim revalidates the exact approval from MySQL rather than trusting Workflow state or Checkpoint alone.
- [x] Domain, application, HTTP, and real MySQL tests cover stale browser and concurrent revision/approval races.
