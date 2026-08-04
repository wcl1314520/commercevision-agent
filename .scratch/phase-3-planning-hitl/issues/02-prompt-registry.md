# 02 — Immutable Prompt Registry revisions

**What to build:** Publish and resolve versioned Planner Prompt Revisions with immutable content,
input/output contracts, lifecycle, hashes, and exact production resolution.

**Blocked by:** None — can start independently after the shared terminology is locked.

**Status:** ready-for-agent

- [ ] Prompt identity, semantic revision, node, category/model applicability, schema versions, policy version, content hash, actor, and timestamps are recorded.
- [ ] DRAFT, REVIEW, STAGING, PRODUCTION, and DEPRECATED transitions are explicit and version-checked.
- [ ] A published revision is immutable; changes create another semantic revision.
- [ ] Production resolution returns one exact revision and hash rather than a mutable alias.
- [ ] Invalid template variables, unknown schemas, oversized content, secrets, and unsafe control characters fail closed.
- [ ] MySQL migration, repository, application Interface, HTTP administration contract, and audit/outbox facts are workspace-safe.
- [ ] Concurrent publication and rollback/deprecation behavior are covered with real MySQL.
- [ ] No external Planner Provider is called by this ticket.
