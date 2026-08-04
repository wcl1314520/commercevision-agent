# 03 — Bounded Planning Context with provenance

**What to build:** Deterministically assemble a Planning Context from current confirmed ProductBrief,
published Brand Profile, eligible Retrieval Citations, and versioned policy without admitting hidden
or unauthorized context.

**Blocked by:** 01 — Creative Plan version contract.

**Status:** ready-for-agent

- [ ] The input accepts exact authoritative references and a versioned context policy, never raw URL/path/SQL/object keys or chat history.
- [ ] ProductBrief confirmation, Brand Profile publication/current usability, Retrieval Rights, workspace, retention, and purpose are revalidated.
- [ ] Ordering, priority, deduplication, token/image budgets, citation numbering, redaction, and truncation are deterministic and bounded.
- [ ] Output records all included/omitted sources, reasons, policy version, canonical SHA-256, and schema version.
- [ ] Prompt Injection remains quoted source data and cannot add system policy, tools, providers, permissions, or budget.
- [ ] A context can be reconstructed from retained authoritative facts for the Workflow lifetime.
- [ ] Domain/application tests cover rights expiry, stale versions, conflict, budget clipping, duplicate citations, and malicious text.
- [ ] The public Interface returns a value; callers do not inspect internal loaders or ranking helpers.
