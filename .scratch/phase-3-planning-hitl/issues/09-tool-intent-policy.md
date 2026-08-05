# 09 — Server-owned Tool Intent policy and authorization

**What to build:** Validate untrusted Planner Tool Intents against a server-owned registry and policy
so model output cannot expand tools, resources, providers, permissions, or budget.

**Blocked by:** 01 — Creative Plan contract; 06 — Exact plan approval fence.

**Status:** complete

- [x] Registered tools have stable names/schema versions, allowed nodes, typed arguments, resource resolvers, cost class, and audit level.
- [x] Policy derives workspace, actor, Workflow, exact approved plan, Rights, provider constraints, quota, and budget from trusted facts.
- [x] Unknown tools/versions, extra arguments, arbitrary URL/path/SQL/object keys, cross-workspace IDs, and excessive budget fail closed.
- [x] Prompt Injection in any source or plan field cannot alter the registry or policy decision.
- [x] Authorization returns a bounded immutable decision and deterministic future idempotency key, not an external side effect.
- [x] Reject/allow decisions are auditable without secret or raw prompt leakage.
- [x] Unit and real application tests cover allow, deny, narrowing, stale approval, revoked Rights, quota, and malicious arguments.
- [x] No Phase 4 image Provider is called.
