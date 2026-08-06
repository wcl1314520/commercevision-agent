# 12 — Exact-source image editing vertical slice

**What to build:** Extend approved execution to IMAGE_EDITING batches bound to exact source Asset
Versions, masks and Plan-authorized repair scope.

**Blocked by:** 05, 06, 09, 11.

**Status:** pending

- [ ] Edit command requires an exact current Candidate/Task Asset Version and current usable Rights.
- [ ] Mask bytes are validated, controlled and hashed; requested repair cannot expand beyond the approved scope.
- [ ] Protected product facts and prohibited elements remain server-owned hard constraints.
- [ ] Provider media type/protocol is selected only from positive endpoint capability.
- [ ] Editing creates new immutable batch/slot/candidate lineage and never overwrites the source.
- [ ] Domain, MySQL, Adapter, API and fault tests cover stale source, mask mismatch, scope expansion and provider drift.
