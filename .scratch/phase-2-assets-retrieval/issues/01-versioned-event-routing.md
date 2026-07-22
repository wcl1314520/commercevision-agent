# 01 — Versioned event routing and single retry authority

**What to build:** Replace the Worker event-type condition with a versioned handler registry and
route Outbox events to explicit workflow, asset, index, and maintenance queues. Preserve the Phase 1
Workflow path while making MySQL the single authority for business retry timing and retry budgets.
Unknown or unsupported event versions must become auditable permanent failures rather than silent
successes.

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] Versioned event handlers are registered through one public routing interface with duplicate-registration protection.
- [ ] Existing Workflow run and resume events retain their Phase 1 behavior.
- [ ] Scheduler publication selects a queue from the event contract rather than a hard-coded default.
- [ ] Celery handles transport redelivery only and does not create an independent business retry schedule.
- [ ] Unknown event types and unsupported schema versions are recorded as permanent failures and reach the DLQ.
- [ ] Duplicate delivery remains idempotent through the existing Inbox contract.
- [ ] Queue and consumer identities are configurable and suitable for independent scaling.
- [ ] Unit and MySQL integration tests cover routing, duplicate delivery, unsupported events, and Phase 1 compatibility.
- [ ] Ruff, pytest, OpenAPI drift, and the complete Phase 1 suite pass.

