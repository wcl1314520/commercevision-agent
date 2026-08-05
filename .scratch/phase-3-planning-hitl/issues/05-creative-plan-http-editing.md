# 05 — Creative Plan REST read and versioned editing

**What to build:** Expose workspace-scoped create, read, version-history, and revise commands for the
Creative Planning module with idempotency, optimistic conflicts, and generated Web types.

**Blocked by:** 04 — Creative Plan MySQL authority.

**Status:** in_progress

- [ ] Create/revise commands require idempotency keys and expected head/Workflow versions.
- [ ] Read/list responses expose immutable versions, current head, provenance, hashes, and no internal object location or secret.
- [ ] User edits create a new version with actor and reason; they never mutate Agent history.
- [ ] Stable errors distinguish invalid payload, stale head, invalid Workflow state, and idempotency conflict without leaking foreign resources.
- [ ] Request/response size, list limits, cursors, and text/collection bounds are enforced before expensive work.
- [ ] OpenAPI and generated Web types remain drift-free.
- [ ] HTTP tests use the public routes and application Interface, not repository internals.
- [ ] API audit events record aggregate metadata only.
