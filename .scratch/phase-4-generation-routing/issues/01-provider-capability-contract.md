# 01 — Provider endpoint capability and route contract

**What to build:** Add framework-independent immutable Provider Endpoint Capability Version,
Route Policy, Route Request, Route Decision, circuit/quota observations, canonical hashes, and
fail-closed invariants shared by every Phase 4 module.

**Blocked by:** None — Phase 4 public test seams confirmed on 2026-08-06.

**Status:** complete

- [x] Capability versions pin provider/endpoint/model/region/protocol/adapter/configuration identity and never contain credentials.
- [x] Generation, editing, planning, sync, async, query, cancel, multipart, JSON, input/output and pricing capabilities are explicit positive facts; unknown means disabled.
- [x] Hard Rights, region, safety, category, budget, quota, format, size and circuit filters run before deterministic scoring.
- [x] Route Decisions are immutable, canonically hashed, explain aggregate reasons, and contain a bounded compatible fallback sequence without secrets.
- [x] Content rejection, policy denial, invalid input and unknown outcome can never authorize failover.
- [x] Unit tests exercise only public domain Interfaces; Ruff and Mypy pass for touched code.

## Comments

- First tracer bullet: publish two capabilities and prove a denied Rights/region request cannot produce a route.
- Public test seams were confirmed and the Phase 4 spec locked on 2026-08-06.
- Public domain Interface under test: `select_model_route(request, capabilities, policy, observations)`
  imported from the `commercevision_domain` root. The Interface returns one immutable
  `ModelRouteDecision` or raises one stable no-eligible-route domain error; it does not expose
  validators, score helpers, filters, repositories or transports.
- First RED fixture: two enabled `IMAGE_GENERATION` endpoint capability versions. The cheaper
  endpoint is outside the trusted Rights/provider or transfer-region set; the eligible endpoint
  must be selected even when its score is lower. A separately frozen expected endpoint identity
  and decision hash keep the assertion independent of the implementation.
- First GREEN ownership is intentionally narrow: one `provider_routing.py` domain module, explicit
  root exports, and one public domain test. No MySQL model, migration, HTTP contract, settings,
  Provider Adapter, Durable Operation kind, queue or dependency is part of this slice.
- Subsequent Ticket 01 RED/GREEN slices add positive capability facts, bounded media constraints,
  budget/quota/circuit filters, deterministic scoring/fallback, canonical decision provenance and
  adversarial validation one behavior at a time.
- Production review added three required contract fixes through RED/GREEN: policy-bounded freshness
  for circuit/quota observations, generic MIME facts so `PLAN` can require `application/json`, and
  exact authorized Asset Version identities in the Route Request canonical hash. Future and stale
  observations fail closed under one stable aggregate rejection code.
- Failover attempt history must be an exact prefix of the immutable route, preventing a caller from
  skipping or reordering compatible endpoints during replay. Selection helpers were extracted into
  one private module; the confirmed root Interface remains unchanged and internal helpers remain
  outside the public test seam.
- Local pre-push evidence: focused domain regression `95 passed`; unit/contract `1490 passed,
  1 skipped`; all 469 Python files formatted and lint-clean; strict touched-code Mypy and the full
  Mypy baseline pass; Python vulnerability/license policy, credential-like scan and
  `git diff --check` pass.
  Ticket status stays `in_progress` until the exact implementation SHA is green in all GitHub
  Actions jobs.
- Implementation commit `83d74e54327edd2f4dc48edb9622d02ba02e190f` is green in exact GitHub
  Actions run `31068995538`: Python `2074 passed, 3 skipped`, Web unit `224 passed`, Web E2E
  `94 passed`, Container builds, Gitleaks, SBOM, dependency audits, migrations, schema drift,
  OpenAPI and prior release/evaluation gates all succeeded. Ticket 01 is complete.
