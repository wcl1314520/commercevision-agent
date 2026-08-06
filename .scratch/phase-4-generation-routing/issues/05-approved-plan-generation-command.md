# 05 — Exact approved-plan generation command

**What to build:** Convert authorized Phase 3 Tool Intent decisions into one atomic Generation Batch,
Candidate Slots, IMAGE_GENERATION Durable Operations, audit and Outbox messages.

**Blocked by:** 02, 04.

**Status:** pending

- [ ] Command revalidates exact Workflow `GENERATING`, current Creative Plan/Approval, Tool Policy decision, Rights, retention, budget and candidate count in one transaction.
- [ ] Duplicate logical requests return the original batch/slots/operations; conflicting reuse fails without partial facts.
- [ ] Batch, slots, durable operations, audit, idempotency and Outbox commit atomically.
- [ ] Unapproved, rejected, stale, expired, revoked or foreign inputs cannot create dispatchable work.
- [ ] Workspace-scoped REST command/read surfaces expose stable conflicts and aggregate provenance without private Provider details.
- [ ] Real-MySQL and HTTP tests cover concurrency, rollback boundaries and cross-tenant opacity.
