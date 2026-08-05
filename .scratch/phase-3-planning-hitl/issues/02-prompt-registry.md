# 02 — Immutable Prompt Registry revisions

**What to build:** Publish and resolve versioned Planner Prompt Revisions with immutable content,
input/output contracts, lifecycle, hashes, and exact production resolution.

**Blocked by:** None — can start independently after the shared terminology is locked.

**Status:** complete

- [x] Prompt identity, semantic revision, node, category/model applicability, schema versions, policy version, content hash, actor, and timestamps are recorded.
- [x] DRAFT, REVIEW, STAGING, PRODUCTION, and DEPRECATED transitions are explicit and version-checked.
- [x] A published revision is immutable; changes create another semantic revision.
- [x] Production resolution returns one exact revision and hash rather than a mutable alias.
- [x] Invalid template variables, unknown schemas, oversized content, secrets, and unsafe control characters fail closed.
- [x] MySQL migration, repository, application Interface, HTTP administration contract, and audit/outbox facts are workspace-safe.
- [x] Concurrent publication and rollback/deprecation behavior are covered with real MySQL.
- [x] No external Planner Provider is called by this ticket.

## Comments

- First tracer bullet: create one frozen Planner Prompt Revision with exact template variables,
  applicability, schemas, policy, actor/time provenance, and an independently frozen content hash.
- Final release evidence: commits `91d0d7c`, `98139fb`, and `ac87d85`; exact GitHub Actions
  run `30898303008` passed Python, Web, Container, and Security/SBOM, including all four real
  MySQL Prompt Registry tests.
