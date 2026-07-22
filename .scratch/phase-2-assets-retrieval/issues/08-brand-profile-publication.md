# 08 — Brand Profile publication

**What to build:** Let brand administrators draft rules and select Foundation Asset versions, validate
their current rights, and publish immutable Brand Profile versions. Rights changes must mark affected
profiles as needing republication without allowing historical versions to authorize stale assets.

**Blocked by:** 03 — Product Catalog workspace; 06 — Rights Records and current usability.

**Status:** ready-for-agent

- [ ] Brand Profile identity and immutable version aggregates enforce workspace, brand, profile key, and optimistic versions.
- [ ] Drafts support rules, approved colors, required marks, prohibited elements, tone, copy constraints, and selected Foundation Assets.
- [ ] Draft validation rechecks current Asset state and Rights Record purpose, provider, derivative permission, and validity.
- [ ] Publication records a content hash and exact Asset Version and Rights Record references.
- [ ] Historical versions remain readable for audit but report current usability separately.
- [ ] Rights replacement, revocation, expiry, or deletion marks affected active profiles `NEEDS_REPUBLISH`.
- [ ] Retrieval cannot use an invalid member merely because an older Brand Profile version contains it.
- [ ] HTTP and Web flows support draft editing, validation errors, publication, immutable version history, and version conflicts.
- [ ] MySQL integration tests cover concurrent publish, invalid member, rights change after publication, and cross-workspace isolation.
- [ ] OpenAPI and frontend contract gates pass.

