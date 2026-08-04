# 01 — Structured Creative Plan and immutable version contract

**What to build:** Add the framework-independent Creative Plan payload, Tool Intent proposal,
provenance references, immutable Creative Plan Version, canonical hash, and revision invariants that
all later Phase 3 modules share.

**Blocked by:** None — can start immediately.

**Status:** complete

- [x] The payload covers image role, visual direction, product protection, selected citations, proposed tools, candidate count, quality targets, and bounded repair scope.
- [x] All text and collections are bounded, control characters and non-finite JSON are rejected, and duplicate stable keys or citation selections fail closed.
- [x] Tool Intents are typed proposals without credentials, arbitrary URLs, paths, SQL, object keys, or execution authority.
- [x] Every version records exact ProductBrief, optional Brand Profile, Retrieval, Planning Context, and Prompt provenance with hashes.
- [x] Versions are immutable, canonically hashed, and ordered persistence reconstruction does not change the hash.
- [x] Agent-created and human-created version invariants are explicit; human revision requires actor, reason, and superseded version.
- [x] Domain exports use existing package conventions and add no runtime dependency.
- [x] Unit tests exercise only the public domain Interface and Ruff/Mypy pass for the touched code.

## Comments

- First tracer bullet: create one valid version and prove immutable provenance plus a stable canonical payload hash.
- RED: public imports failed because the Creative Plan Interface did not exist.
- GREEN: the public domain Interface now creates one immutable, provenance-complete Agent version with the independently frozen canonical hash.
- Verification: targeted Ruff format/check and Mypy pass; the complete unit suite is `1074 passed` with only the existing upstream Starlette deprecation warning.
- Citation selections retain both the bounded citation ID and its bounded selection reason; the independently frozen payload hash is `04c9fe86d61276edf23b8f219e40e04df6bca0e809e0fb83652c430e2f46347a`.
- Direction, citation-selection, and Tool Intent stable keys are normalized before hashing, so persistence row order cannot change the payload identity.
- Tool Intent arguments are bounded canonical JSON and reject non-finite data, control characters, arbitrary URI/path values, credentials, storage/database coordinates, and execution authority in snake_case or camelCase fields.
- Human revision is a version method that preserves plan/workflow/workspace identity and exact prior provenance while creating the next immutable version and supersedes edge.
- Final verification: complete unit suite `1112 passed`; full-repository Ruff format/check, touched-module strict Mypy, and `git diff --check` pass. Five-axis review found one Required camelCase authority bypass and closed it with RED/GREEN; no blockers remain.
