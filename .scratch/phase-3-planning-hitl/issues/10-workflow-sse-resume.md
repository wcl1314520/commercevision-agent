# 10 — Persisted Workflow SSE with opaque resume cursor

**What to build:** Stream persisted Workflow/Planning events through SSE with workspace isolation,
stable ordering, bounded catch-up, heartbeat, and resumable opaque cursor semantics.

**Blocked by:** 05 — Creative Plan HTTP; 06 — Exact plan approval fence.

**Status:** ready-for-agent

- [ ] Events are read from persisted facts in stable order and never from process-local authorization state.
- [ ] Cursor is opaque, signed or otherwise tamper-evident, workspace/Workflow scoped, bounded, and expires with retained data.
- [ ] Reconnect resumes strictly after the last delivered event without gaps; duplicate delivery has no duplicate business effect.
- [ ] Invalid, foreign, future, expired, or oversized cursors fail without information leakage or unbounded scans.
- [ ] Slow/disconnected clients do not hold transactions, connections indefinitely, Worker leases, or background tasks.
- [ ] Heartbeat and retry hints are bounded and configurable; event bodies exclude raw plans/prompts/provider payloads.
- [ ] HTTP/stream tests cover reconnect, concurrent events, disconnect, cursor tamper, retention, and authorization.
- [ ] Load test covers reconnect storm and database/page budgets.
