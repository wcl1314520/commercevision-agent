# 15 — Cost, circuit, safety observability and runbooks

**What to build:** Add aggregate metrics, traces, audit views, unresolved-cost/circuit alerts and
operator runbooks for provider execution without leaking sensitive media or credentials.

**Blocked by:** 04, 09, 10, 11, 13.

**Status:** pending

- [ ] Metrics cover route decisions, endpoint/model, dispatch/query outcomes, latency, candidate convergence, safety, circuit/quota and estimated/actual/unresolved cost.
- [ ] Cardinality is bounded and tenant/provider request IDs, prompts, signed URLs, object keys and secrets are excluded.
- [ ] Operator views correlate hashed request identity, Operation, batch/slot, endpoint version and reconciliation history.
- [ ] Alerts distinguish provider outage, unknown outcomes, cost mismatch, safety outage, queue lag and retention cleanup failure.
- [ ] Runbooks cover secret rotation, Kuaipao unknown wait, Wan task expiry, circuit recovery, DLQ replay, cancellation and cost reconciliation.
- [ ] Telemetry/report fixtures and secret scans prove redaction under success and adversarial failures.
