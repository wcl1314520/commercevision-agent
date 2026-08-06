# 09 — Generation queue, Worker execution, and candidate convergence

**What to build:** Add an isolated generation queue and Durable Operation executor that dispatches
outside transactions, validates result bytes, persists assets/candidates/usage and resumes Workflow.

**Blocked by:** 02, 05, 06.

**Status:** pending

- [ ] Existing Worker/registry/lease/Outbox/Inbox/readiness framework owns the queue; no parallel job system or service framework is introduced.
- [ ] Authority is rechecked before dispatch and before late result availability.
- [ ] Provider calls and downloads occur outside MySQL transactions with bounded leases/deadlines.
- [ ] Result transfer enforces exact-host SSRF, redirects, MIME, bytes, pixels, decompression and hash validation before controlled object persistence.
- [ ] Asset Version, Candidate Image, Usage, audit, event and Operation success converge atomically or remain unavailable and recoverable.
- [ ] Real-infrastructure fault tests cover crashes around dispatch/download/object/commit and prove one effective candidate/usage identity.
