# 03 — Bounded Planning Context with provenance

**What to build:** Deterministically assemble a Planning Context from current confirmed ProductBrief,
published Brand Profile, eligible Retrieval Citations, and versioned policy without admitting hidden
or unauthorized context.

**Blocked by:** 01 — Creative Plan version contract.

**Status:** awaiting-ci

- [x] The input accepts exact authoritative references and a versioned context policy, never raw URL/path/SQL/object keys or chat history.
- [x] ProductBrief confirmation, Brand Profile publication/current usability, Retrieval Rights, workspace, retention, and purpose are revalidated.
- [x] Ordering, priority, deduplication, token/image budgets, citation numbering, redaction, and truncation are deterministic and bounded.
- [x] Output records all included/omitted sources, reasons, policy version, canonical SHA-256, and schema version.
- [x] Prompt Injection remains quoted source data and cannot add system policy, tools, providers, permissions, or budget.
- [x] A context can be reconstructed from retained authoritative facts for the Workflow lifetime.
- [x] Domain/application tests cover rights expiry, stale versions, conflict, budget clipping, duplicate citations, and malicious text.
- [x] The public Interface returns a value; callers do not inspect internal loaders or ranking helpers.

**Local verification:** domain/application/persistence/MySQL authority and migration tests pass; full
unit suite `1146 passed`; full Ruff format/check, strict touched-file Mypy, 432-diagnostic Mypy
baseline, dependency audit, migration contract, and diff checks pass. Exact GitHub Actions evidence
is required before this Ticket becomes `complete` or Ticket 04 starts.
