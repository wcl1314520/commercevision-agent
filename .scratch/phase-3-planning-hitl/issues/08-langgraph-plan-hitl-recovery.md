# 08 — LangGraph Creative Plan interrupt, resume, and restart recovery

**What to build:** Bind Planner output and exact Plan Approval to a real LangGraph interrupt that
survives process restart and rejects stale or unrelated resume payloads.

**Blocked by:** 06 — Exact plan approval fence; 07 — Durable Fixture Planner.

**Status:** complete

- [x] Interrupt payload exposes exact Workflow and plan versions plus allowed actions, with no mutable authorization state.
- [x] Resume validates Approval ID/type/decision/subject/version and resulting Workflow version against MySQL facts.
- [x] Worker restart before or after interrupt/approval/commit resumes once without duplicate plan, approval, Step, or tool intent.
- [x] Reject loops to a new plan version while preserving immutable history and bounded iteration count.
- [x] Stale continuation and wrong checkpoint generation converge as auditable no-ops or stable conflicts.
- [x] MySQL Workflow state remains business truth; Checkpoint stores graph continuation only.
- [x] Retention expiry prevents resume and triggers existing cleanup semantics.
- [x] In-memory contract and real MySQL Checkpointer tests cover sync/async restart paths.
