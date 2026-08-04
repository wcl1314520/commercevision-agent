# 14 — Phase 3 chaos, E2E, and release acceptance

**What to build:** Prove Phase 3 as a deployable, auditable planning control plane through browser,
real-infrastructure recovery, migration, security, evaluation, Compose, and supply-chain evidence.

**Blocked by:** 01–13 — all preceding Phase 3 Tickets.

**Status:** ready-for-agent

- [ ] Playwright covers plan creation, provenance review, edit, version history, approve, reject/revise, stale conflict, refresh, SSE reconnect, policy denial, and retention expiry.
- [ ] Fault injection covers Worker interruption around version/approval commits, RabbitMQ, MySQL reconnect, Checkpointer restart, SSE disconnect, and evaluation interruption.
- [ ] Recovery proves no duplicate plan version/approval, no stale authorization, no unauthorized Tool Intent, no retention extension, and eventual convergence.
- [ ] Empty upgrade, Phase 2-to-3 upgrade, non-destructive downgrade/re-upgrade, Alembic drift, and `DATETIME(6)` checks pass.
- [ ] Unapproved/rejected plans cannot execute and stale pages cannot approve or overwrite a newer version.
- [ ] Prompt Injection cannot add tools, permissions, providers, resources, or budget; Agent Eval gates pass.
- [ ] Python/Web/OpenAPI/real MySQL/LangGraph/SSE/E2E/Eval/security/secret/dependency/container/license/SBOM gates pass.
- [ ] Public-demo Planning data, Prompt revisions, quotas, cursors, and datasets are isolated from private configuration.
- [ ] Architecture, schema, AI, testing, deployment, runbook, roadmap, README, and metadata match the implementation.
- [ ] GitHub Actions on the final implementation commit are green and exact evidence is recorded.
