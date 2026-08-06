# 08 — Alibaba Model Studio Wan production adapter

**What to build:** Implement the independent, region-bound Wan 2.7 image generation/editing Adapter
with async task submission/query and bounded result handling.

**Blocked by:** 03, 06.

**Status:** in_progress

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

- [ ] Endpoint capability pins workspace-specific host, region, model, protocol mode and adapter/configuration hash.
- [ ] Async submit persists the first task/request identity; query maps pending/success/failure/rejection without resubmission.
- [ ] Sync and async protocols have separate capabilities/parsers; neither silently falls back to the other.
- [ ] Temporary result URLs are untrusted handles and never become Candidate identity or browser URLs.
- [ ] Mounted-secret, exact egress, byte/pixel/type/deadline and redaction controls match existing Alibaba Provider standards.
- [ ] Official-contract mock tests cover task expiry, malformed status, partial results, throttling, region mismatch and secret rotation.
