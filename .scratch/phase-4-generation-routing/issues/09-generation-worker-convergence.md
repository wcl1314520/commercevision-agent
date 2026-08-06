# 09 — Generation queue, Worker execution, and candidate convergence

**What to build:** Add an isolated generation queue and Durable Operation executor that dispatches
outside transactions, validates result bytes, persists assets/candidates/usage and resumes Workflow.

**Blocked by:** 02, 05, 06.

**Status:** in_progress

## Comments

- 2026-08-06: Blockers 02/05/06 are complete, and Ticket 08 implementation commit
  `16f18960c06db9abc19626fe7af70fb27ee70619` passed exact GitHub Actions run `31110859233`
  in all four jobs. Ticket 09 is unblocked. Its first RED will exercise the existing public
  Outbox/Inbox -> Worker -> Durable Operation executor seam; no parallel queue or job framework
  will be introduced.

- [ ] Existing Worker/registry/lease/Outbox/Inbox/readiness framework owns the queue; no parallel job system or service framework is introduced.
- [ ] Authority is rechecked before dispatch and before late result availability.
- [ ] Provider calls and downloads occur outside MySQL transactions with bounded leases/deadlines.
- [ ] Result transfer enforces exact-host SSRF, redirects, MIME, bytes, pixels, decompression and hash validation before controlled object persistence.
- [ ] Asset Version, Candidate Image, Usage, audit, event and Operation success converge atomically or remain unavailable and recoverable.
- [ ] Real-infrastructure fault tests cover crashes around dispatch/download/object/commit and prove one effective candidate/usage identity.
