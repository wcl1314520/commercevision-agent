# 08 — Alibaba Model Studio Wan production adapter

**What to build:** Implement the independent, region-bound Wan 2.7 image generation/editing Adapter
with async task submission/query and bounded result handling.

**Blocked by:** 03, 06.

**Status:** pending

- [ ] Endpoint capability pins workspace-specific host, region, model, protocol mode and adapter/configuration hash.
- [ ] Async submit persists the first task/request identity; query maps pending/success/failure/rejection without resubmission.
- [ ] Sync and async protocols have separate capabilities/parsers; neither silently falls back to the other.
- [ ] Temporary result URLs are untrusted handles and never become Candidate identity or browser URLs.
- [ ] Mounted-secret, exact egress, byte/pixel/type/deadline and redaction controls match existing Alibaba Provider standards.
- [ ] Official-contract mock tests cover task expiry, malformed status, partial results, throttling, region mismatch and secret rotation.
