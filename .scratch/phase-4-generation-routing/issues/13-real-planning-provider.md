# 13 — Routed real Planning Provider

**What to build:** Add an Alibaba Model Studio Qwen Chat Completions JSON-mode Planning Adapter and
route Phase 3 Planning through the same capability/control-plane discipline while retaining
deterministic fixture parity.

**Blocked by:** 03, 04, 06.

**Status:** pending

- [ ] Planning capability pins exact provider/model/region/adapter/configuration and mounted Secret Reference.
- [ ] OpenAI-compatible JSON mode is bounded and region-scoped; mutable model aliases are not silently resolved at execution time.
- [ ] Exact Prompt Revision and Planning Context provenance produce a bounded structured response validated by the existing Creative Plan contract.
- [ ] Provider text cannot add tools, permissions, providers, resources, URLs, credentials or budget.
- [ ] Durable Planner execution records request identity, latency, token/price evidence and redacted stable errors.
- [ ] Missing/invalid Provider output fails closed and deterministic fixture remains the public CI baseline.
- [ ] Contract, prompt-injection, timeout, malformed-output, secret and route-policy tests pass.
