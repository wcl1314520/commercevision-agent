# 08 — Alibaba Model Studio Wan production adapter

**What to build:** Implement the independent, region-bound Wan 2.7 image generation/editing Adapter
with async task submission/query and bounded result handling.

**Blocked by:** 03, 06.

**Status:** complete

## Comments

- 2026-08-06: Ticket 03/06 blockers and Ticket 07 release status are complete. Ticket 07 state commit
  `2d79d97af3ee5612b4643df2761eb734b0125ae4` passed GitHub Actions run `31105556687` in all four
  jobs. Ticket 08 entered bounded primary-source contract research before its first async-submit RED.
- 2026-08-06: Official Alibaba evidence now freezes the workspace-specific Beijing/Singapore
  async submit/query protocols. The local implementation and adversarial mock suite are complete;
  exact mask-to-bbox editing and cancellation remain fail-closed because those semantics belong to
  Ticket 12 or are not published for the workspace-specific API. Local release gates are green at
  `1653 passed, 1 skipped`, Ruff `505 files`, Mypy baseline `427` with zero drift, plus lock,
  license and vulnerability audits. Exact implementation-SHA CI remains the completion gate.
- 2026-08-06: Implementation commit `16f18960c06db9abc19626fe7af70fb27ee70619` passed exact
  GitHub Actions run `31110859233` in all four jobs: Python checks, Web checks, Container builds,
  and Security/SBOM. All acceptance items are closed without live credentials or paid calls.

- [x] Endpoint capability pins workspace-specific host, region, model, protocol mode and adapter/configuration hash.
- [x] Async submit persists the first task/request identity; query maps pending/success/failure/rejection without resubmission.
- [x] Sync and async protocols have separate capabilities/parsers; neither silently falls back to the other.
- [x] Temporary result URLs are untrusted handles and never become Candidate identity or browser URLs.
- [x] Mounted-secret, exact egress, byte/pixel/type/deadline and redaction controls match existing Alibaba Provider standards.
- [x] Official-contract mock tests cover task expiry, malformed status, partial results, throttling, region mismatch and secret rotation.
