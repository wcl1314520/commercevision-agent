# 07 — Vision ProductBrief and human confirmation

**What to build:** Generate evidence-backed ProductBrief versions from eligible product images and
catalog data using a production Alibaba Vision Adapter and deterministic tests. Low-confidence,
conflicting, mandatory, or sensitive fields must enter a real human confirmation workflow in the Web
workbench and resume the durable Workflow only after a specific version is confirmed.

**Blocked by:** 02 — Durable Operations and recovery control plane; 03 — Product Catalog workspace; 06 — Rights Records and current usability.

**Status:** complete

- [x] Vision requests accept only currently authorized internal Asset Versions for the configured provider.
- [x] Provider, endpoint, requested model, resolved model, prompt/config version, request ID, usage, latency, and response reference are recorded.
- [x] Structured output is independently validated and malformed output follows bounded repair or terminal failure policy.
- [x] Common ProductBrief fields and versioned beauty and automotive-accessory extension schemas are implemented.
- [x] Every field records value, confidence, evidence, source Asset Version, conflict, review requirement, and sensitive-claim flag.
- [x] Policy thresholds place uncertain or sensitive briefs into awaiting confirmation.
- [x] Human edits create an immutable new version with actor, reason, and evidence; they do not overwrite model history.
- [x] Confirmation targets one exact version, records an append-only approval, completes the ProductBrief human wait, and emits Workflow resume.
- [x] Stale expected versions return a stable conflict and the Web workbench reloads current state.
- [x] Raw provider request/response objects inherit the correct Task or Foundation retention and are not written to logs or Outbox payloads.
- [x] Provider contract, MySQL Worker, HTTP, and Web tests cover success, low confidence, conflict, sensitive claims, malformed output, provider timeout, human edit, confirmation, and restart during wait.
