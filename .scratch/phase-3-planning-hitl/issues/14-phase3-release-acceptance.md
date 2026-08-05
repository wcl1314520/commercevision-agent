# 14 — Phase 3 chaos, E2E, and release acceptance

**What to build:** Prove Phase 3 as a deployable, auditable planning control plane through browser,
real-infrastructure recovery, migration, security, evaluation, Compose, and supply-chain evidence.

**Blocked by:** 01–13 — all preceding Phase 3 Tickets.

**Status:** in_progress

- [x] Playwright covers plan creation, provenance review, edit, version history, approve, reject/revise, stale conflict, refresh, SSE reconnect, policy denial, and retention expiry.
- [x] Fault injection covers Worker interruption around version/approval commits, RabbitMQ, MySQL reconnect, Checkpointer restart, SSE disconnect, and evaluation interruption.
- [x] Recovery proves no duplicate plan version/approval, no stale authorization, no unauthorized Tool Intent, no retention extension, and eventual convergence.
- [x] Empty upgrade, Phase 2-to-3 upgrade, non-destructive downgrade/re-upgrade, Alembic drift, and `DATETIME(6)` checks pass.
- [x] Unapproved/rejected plans cannot execute and stale pages cannot approve or overwrite a newer version.
- [x] Prompt Injection cannot add tools, permissions, providers, resources, or budget; Agent Eval gates pass.
- [x] Python/Web/OpenAPI/real MySQL/LangGraph/SSE/E2E/Eval/security/secret/dependency/container/license/SBOM gates pass.
- [x] Public-demo Planning data, Prompt revisions, quotas, cursors, and datasets are isolated from private configuration.
- [x] Architecture, schema, AI, testing, deployment, runbook, roadmap, README, and metadata match the implementation.
- [ ] GitHub Actions on the final implementation commit are green and exact evidence is recorded.
