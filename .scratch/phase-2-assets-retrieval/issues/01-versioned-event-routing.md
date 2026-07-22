# 01 — Versioned event routing and single retry authority

**What to build:** Replace the Worker event-type condition with a versioned handler registry and
route Outbox events to explicit workflow, asset, index, and maintenance queues. Preserve the Phase 1
Workflow path while making MySQL the single authority for business retry timing and retry budgets.
Unknown or unsupported event versions must become auditable permanent failures rather than silent
successes.

**Blocked by:** None — can start immediately.

**Status:** ready-for-human

- [x] Versioned event handlers are registered through one public routing interface with duplicate-registration protection.
- [x] Existing Workflow run and resume events retain their Phase 1 behavior.
- [x] Scheduler publication selects a queue from the event contract rather than a hard-coded default.
- [x] Celery handles transport redelivery only and does not create an independent business retry schedule.
- [x] Unknown event types and unsupported schema versions are recorded as permanent failures and reach the DLQ.
- [x] Duplicate delivery remains idempotent through the existing Inbox contract.
- [x] Queue and consumer identities are configurable and suitable for independent scaling.
- [x] Unit and MySQL integration tests cover routing, duplicate delivery, unsupported events, and Phase 1 compatibility.
- [x] Ruff, pytest, OpenAPI drift, and the complete Phase 1 suite pass.

## Comments

- Implemented the shared versioned event routing registry, explicit workflow/asset/index/maintenance queue selection, configurable worker queue consumption, and Inbox-backed permanent DLQ handling.
- Corrective implementation: all eight Phase 1 event contracts route to the workflow queue; run/resume dispatch remains intact; six notification/audit events are explicit observed no-ops; MySQL owns bounded business retries with transport-only Celery redelivery fallback; queue identities and worker selections are strict and configurable; event payload contracts live in `packages/contracts`; malformed, unknown, unsupported, and unhandled events are auditable permanent DLQ failures.
- Verification: `uv sync --locked --all-packages`; `83 passed`; Ruff format/check; Python dependency audit (no known vulnerabilities); OpenAPI drift; Phase 0 verification; Compose config; web lint, typecheck, and production build.
- Independent Standards, Spec, and code-review-and-quality reviews: `VERDICT: APPROVE`; no Critical or Required findings. Optional observations remain documented in the final delivery report.
