# 05 — Creative Plan REST read and versioned editing

**What to build:** Expose workspace-scoped create, read, version-history, and revise commands for the
Creative Planning module with idempotency, optimistic conflicts, and generated Web types.

**Blocked by:** 04 — Creative Plan MySQL authority.

**Status:** complete

- [x] Create/revise commands require idempotency keys and expected head/Workflow versions.
- [x] Read/list responses expose immutable versions, current head, provenance, hashes, and no internal object location or secret.
- [x] User edits create a new version with actor and reason; they never mutate Agent history.
- [x] Stable errors distinguish invalid payload, stale head, invalid Workflow state, and idempotency conflict without leaking foreign resources.
- [x] Request/response size, list limits, cursors, and text/collection bounds are enforced before expensive work.
- [x] OpenAPI and generated Web types remain drift-free.
- [x] HTTP tests use the public routes and application Interface, not repository internals.
- [x] API audit events record aggregate metadata only.

Implementation commit `c45a8b2b4dc7cd21bd5f7c91b60bdd55d42ec4b7` is verified by exact
GitHub Actions run `30981372490`: Python, Web, container, and Security/SBOM jobs all passed.
