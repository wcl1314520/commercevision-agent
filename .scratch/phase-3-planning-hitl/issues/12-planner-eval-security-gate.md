# 12 — Planner fixtures, Agent Eval, and prompt-injection release gate

**What to build:** Add versioned beauty and automotive planning fixtures, deterministic evaluation,
and release gates for plan validity, provenance, policy safety, determinism, and bounded latency.

**Blocked by:** 02 — Prompt Registry; 03 — Planning Context; 07 — Fixture Planner; 09 — Tool Policy.

**Status:** ready-for-agent

- [ ] Datasets freeze ProductBrief, Brand Profile, Retrieval Citations, context policy, Prompt Revision, expected plan facts, and malicious variants.
- [ ] Metrics cover schema validity, required constraints, citation precision, provenance completeness, policy violations, determinism, and latency.
- [ ] Unauthorized tool/provider/resource/budget expansion and missing approval evidence must equal zero.
- [ ] Prompt Injection cases cover source text, OCR-like evidence, brand rules, retrieval reasons, and user edits.
- [ ] Development, validation, and hidden release data are separated; thresholds live in a versioned manifest.
- [ ] Reports are machine/human readable, aggregate-only where required, reproducible by all relevant versions, and retain no sensitive payload.
- [ ] A deterministic small profile runs in CI and a full release profile is available.
- [ ] Fixture drift, threshold bypass, malformed observations, unbounded input, and report tamper fail closed.
