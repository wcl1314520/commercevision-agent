# 07 — Durable Fixture Planner and version creation

**What to build:** Replace the opaque fixture plan reference with a deterministic Planner Node that
uses exact Planning Context and Prompt Revision facts to create one authoritative Creative Plan
Version through the existing durable Workflow lifecycle.

**Blocked by:** 02 — Prompt Registry; 03 — Planning Context; 04 — Creative Plan MySQL authority.

**Status:** ready-for-agent

- [ ] Planner input records exact context and Prompt revisions and output validates against the Creative Plan schema.
- [ ] The deterministic Fixture Planner produces reproducible beauty and automotive examples without an external model call.
- [ ] Create-plan Durable Operation/Step identity prevents duplicate versions across event replay and Worker interruption.
- [ ] Eligibility, Workflow current node/version, ProductBrief confirmation, retention, and current plan head are rechecked on claim and commit.
- [ ] Unknown outcomes are not possible for the Fixture Planner; Phase 4 owns real Provider reconciliation.
- [ ] Success enters AWAITING_PLAN_APPROVAL with the exact subject; validation or policy failures are classified and auditable.
- [ ] Raw context or full plan bodies do not enter Outbox, logs, metrics, or errors.
- [ ] Unit, event, and real MySQL tests cover replay, crash boundaries, stale continuation, and deterministic output.
