# 07 — Kuaipao synchronous Images adapter

**What to build:** Implement a bounded OpenAI Images-compatible adapter for verified Kuaipao
synchronous generation/editing/model discovery, with external mounted-secret injection.

**Blocked by:** 03, 06.

**Status:** pending

- [ ] HTTPS exact-host/region allowlist, deadlines, body limits, redirect policy and per-endpoint concurrency fail closed.
- [ ] Secret is reread from a bounded mounted regular file for each submit and never enters repo/config examples/MySQL/logs/traces/artifacts/errors.
- [ ] Async submit/query stay disabled; JSON and multipart editing are independent capabilities enabled only by authenticated contract evidence.
- [ ] Parser supports bounded `url` or `b64_json`, captures `X-Oneapi-Request-Id`, and redacts Provider bodies.
- [ ] Connection timeout/response loss is unknown and never auto-resubmitted; only proven pre-dispatch failures are retryable.
- [ ] Mock-HTTP contract, SSRF, redirect, oversized payload, malformed response, secret-rotation and secret-scan tests pass; live smoke is opt-in only.
