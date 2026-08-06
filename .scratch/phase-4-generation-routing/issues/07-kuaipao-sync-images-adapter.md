# 07 — Kuaipao synchronous Images adapter

**What to build:** Implement a bounded OpenAI Images-compatible adapter for verified Kuaipao
synchronous generation/editing/model discovery, with external mounted-secret injection.

**Blocked by:** 03, 06.

**Status:** complete

- [x] HTTPS exact-host/region allowlist, deadlines, body limits, redirect policy and per-endpoint concurrency fail closed.
- [x] Secret is reread from a bounded mounted regular file for each submit and never enters repo/config examples/MySQL/logs/traces/artifacts/errors.
- [x] Async submit/query stay disabled; JSON and multipart editing are independent capabilities enabled only by authenticated contract evidence.
- [x] Parser supports bounded `url` or `b64_json`, captures `X-Oneapi-Request-Id`, and redacts Provider bodies.
- [x] Connection timeout/response loss is unknown and never auto-resubmitted; only proven pre-dispatch failures are retryable.
- [x] Mock-HTTP contract, SSRF, redirect, oversized payload, malformed response, secret-rotation and secret-scan tests pass; live smoke is opt-in only.

## Comments

- 2026-08-06: Ticket 06's public contract and deterministic Adapter were released by exact commit
  `b7621e2277c5ecc52a04928d90941876a7cd3f9e` and GitHub Actions run `31099374416`. Ticket 07 now
  enters TDD at the synchronous `b64_json` generation seam. Proving the bounded HTTP Adapter against
  the unchanged shared contract is the final Ticket 06 acceptance item, so the two tickets co-close
  that parity without weakening the blockers-first dependency.
- 2026-08-06: Synchronous generation, typed failure/unknown semantics, mounted-secret rotation,
  exact Endpoint/result-host policies, bounded Base64/URL parsing, internal result download and full
  image decode are locally green. The deterministic/bounded-HTTP shared contract parity closes
  Ticket 06. Ticket 07 remains open for release gates, secret scan and opt-in-live boundary evidence.
- 2026-08-06: Local release evidence is green: unit+contract `1595 passed, 1 skipped`, shared
  Image/Vision regression `139 passed`, full Ruff `503 files`, strict touched-code Mypy, tightened
  full-workspace Mypy baseline `427`, lock, license, vulnerability and diff checks. Staged Gitleaks
  scanned about 83.43 KB with no leaks. All Kuaipao tests are mock HTTP; no default live call or
  credential-consuming smoke was added. Status remains `in_progress` until exact-commit CI passes.
- 2026-08-06: Implementation commit `1158f88dd1de28877e8bc7a66f5b10ee38e1c80b` passed all four
  GitHub Actions jobs in run `31103788945` (Python, Web, Container builds, Security and SBOM).
  Ticket 07 is complete; Ticket 08 is the next blockers-first implementation target.
