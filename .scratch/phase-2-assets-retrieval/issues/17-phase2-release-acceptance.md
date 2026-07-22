# 17 — Phase 2 chaos, E2E, and release acceptance

**What to build:** Prove the complete Phase 2 capability as a deployable system. Run browser E2E,
real-infrastructure recovery scenarios, migration paths, full security and supply-chain gates,
Compose verification, public-demo isolation checks, and a requirement-by-requirement exit audit.

**Blocked by:** 01–16 — all preceding Phase 2 Tickets.

**Status:** ready-for-agent

- [ ] Playwright covers product creation, direct upload, validation, Rights Record registration, ProductBrief review/confirmation, Brand Profile publication, retrieval explanation, controlled preview, refresh recovery, version conflict, policy rejection, and degraded retrieval.
- [ ] Fault injection covers MinIO, Milvus, RabbitMQ, ClamAV, content safety, Vision, Embedding, reranker, Worker interruption, and rebuild interruption at defined transaction boundaries.
- [ ] Recovery proves no duplicate logical operation, no duplicate vector, no unauthorized return, no retention extension, and eventual convergence.
- [ ] Empty-database upgrade, Phase 1-to-Phase 2 upgrade, downgrade where non-destructive, re-upgrade, and Alembic drift checks pass with all time columns at `DATETIME(6)`.
- [ ] Local Compose initializes every required bucket and dependency and all long-running services become healthy.
- [ ] The full Python, frontend, OpenAPI, MCP, Provider contract, real infrastructure, E2E, evaluation, security, secret-scan, dependency-audit, container-build, license, and SBOM gates pass.
- [ ] Public-demo workspace, buckets/prefixes, credentials, quotas, and datasets are isolated from private deployment configuration.
- [ ] The metadata endpoint reports Phase 2 only after all exit gates are satisfied.
- [ ] Architecture, schema, API, runbook, evaluation, deployment, roadmap, and README documentation match the implemented system.
- [ ] GitHub Actions on the final `main` commit are green and the evidence is recorded.
- [ ] The final audit proves unauthorized recall is zero, indexing is incremental, Milvus rebuild succeeds, ProductBrief HITL works after Worker restart, and retention boundaries are exact.

