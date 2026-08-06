# 14 — Candidate REST, SSE, and Web Workbench

**What to build:** Expose workspace-scoped generation status, candidate review, cost provenance,
cancel/retry/edit commands and resumable events through the Phase 3 REST/SSE/Web seams.

**Blocked by:** 05, 09, 10, 11, 12.

**Status:** pending

- [ ] Reads show exact Plan/direction/batch/slot/candidate versions, safety state, aggregate cost and stable failure classes.
- [ ] Browser never receives/renders Provider URLs, raw payloads, Prompt, keys, Secret References or private endpoint details.
- [ ] Preview/download use short-lived controlled Asset URLs; retention/cancellation removes actions and previews immediately.
- [ ] Retry/cancel/regenerate/edit require idempotency, expected versions and current server authority.
- [ ] Persisted SSE resumes with opaque workspace/Workflow cursors and does not hold transactions or Provider calls.
- [ ] Playwright covers refresh/reconnect, partial candidates, conflict reload, cancellation, edit and retention expiry.
