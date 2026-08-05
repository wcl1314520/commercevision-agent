# 13 — Planning observability and operator runbooks

**What to build:** Instrument Planning Context, Prompt resolution, Planner, versioning, approval, Tool
Policy, LangGraph resume, and SSE while documenting concrete recovery procedures.

**Blocked by:** 07–12 — complete planning execution, policy, stream, Web, and evaluation paths.

**Status:** in_progress

- [ ] Spans propagate Workflow, plan/version, context hash, Prompt revision, approval, event, operation, trace, and policy identifiers.
- [ ] Metrics cover context clipping, planner validity/latency, revisions, stale approvals, policy denials, human wait/confirmation, SSE clients/reconnects, and resume failures.
- [ ] Raw plan/prompt/context/provider payloads, secrets, arbitrary user text, and sensitive citations are absent from logs, spans, metrics, events, and errors.
- [ ] Readiness includes only dependencies each process requires; optional SSE/client degradation does not take down control API writes.
- [ ] Runbooks cover stuck planning, invalid output, stale approvals, repeated rejection, resume mismatch, policy denial surge, SSE lag/reconnect storm, and retention expiry.
- [ ] Trace/metric/redaction tests observe public instrumentation Interfaces.
- [ ] Alert thresholds and bounded-cardinality labels are documented.
- [ ] Local Compose exposes useful telemetry without production secrets.
