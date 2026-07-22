# 15 — End-to-end OpenTelemetry and operations runbooks

**What to build:** Instrument the complete Phase 2 lifecycle with traceable spans, metrics, and stable
error attributes, and provide operator runbooks for the concrete failure modes introduced by asset
storage, providers, indexing, retrieval, deletion, DLQ, and rebuild.

**Blocked by:** 04 — Direct Upload Sessions and quarantine; 07 — Vision ProductBrief and human confirmation; 09 — Collection Registry and IMAGE incremental indexing; 11 — Rights-first hybrid retrieval and Retrieval Explorer; 13 — Retention, deletion, and consistency reconciliation; 14 — Milvus rebuild and Collection upgrade.

**Status:** ready-for-agent

- [ ] Spans cover upload, finalize, validation stages, rights decisions, Vision, ProductBrief, embedding, Milvus, lexical search, fusion, rerank, final rights, temporary references, deletion, reconciliation, and rebuild batches.
- [ ] Metrics cover quarantine age, validation outcomes, rights denials/expiry, provider latency/errors/rate limits, operation leases/retries/DLQ, confirmation rate, index lag, stale vectors, retrieval latency/degradation, deletion backlog, and rebuild progress.
- [ ] Trace, operation, target/version, event, provider request, and policy identifiers propagate across HTTP, Outbox, Worker, Provider, Milvus, MCP, and Scheduler.
- [ ] Images, secrets, raw prompts, full OCR, and raw provider payloads are absent from logs, spans, metrics, events, and errors.
- [ ] API, Worker, Scheduler, and MCP readiness checks include only process-required dependencies.
- [ ] Optional reranker outage degrades retrieval without taking down the control plane.
- [ ] Runbooks cover stuck quarantine, ClamAV outage, content-safety outage, provider throttling, index lag, stale vectors, Milvus loss, deletion backlog, DLQ replay, and rebuild failure.
- [ ] Observability tests verify attribute propagation, redaction, metric increments, and readiness degradation.
- [ ] Collector configuration and local Compose expose usable telemetry without production secrets.

