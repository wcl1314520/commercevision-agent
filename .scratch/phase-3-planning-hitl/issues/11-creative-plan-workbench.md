# 11 — Creative Plan editor and approval Workbench

**What to build:** Add a production Web Workbench for reviewing provenance and Tool Intents, editing
to a new plan version, approving/rejecting the visible version, and recovering through refresh/SSE.

**Blocked by:** 05 — Creative Plan HTTP; 06 — Exact plan approval fence; 10 — Workflow SSE.

**Status:** complete

- [x] The page shows exact plan/Workflow versions, ProductBrief/Brand/Prompt/Context provenance, citations, Tool Intents, and approval history.
- [x] Editing creates a new immutable version with reason and never mutates or hides prior versions.
- [x] Approve/reject sends the visible exact subject and versions with idempotency.
- [x] A stale `409` reloads current state, preserves recoverable user text separately, and never silently reapplies approval.
- [x] Refresh and SSE reconnect restore the current review position without local authorization truth.
- [x] Loading, empty, policy-denied, retention-expired, degraded stream, conflict, and retry states are accessible and actionable.
- [x] Keyboard, focus, labels, contrast, reduced motion, 375px layout, and minimum target sizes pass.
- [x] Unit, proxy, generated-type, persistence, and Playwright tests cover the complete review path.
