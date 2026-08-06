# 06 — Image Provider Adapter contract and deterministic adapter

**What to build:** Define the narrow production Adapter port for generation/edit submit/query/cancel
and a deterministic implementation that exercises identical typed outcomes in public CI.

**Blocked by:** 01, 02.

**Status:** pending

- [ ] Adapter requests carry only normalized typed media requirements and controlled input handles.
- [ ] Outcomes distinguish success, confirmed failure, content rejection, safe pre-dispatch retry and unknown possible dispatch.
- [ ] Provider request/task identity, usage, result references and errors are bounded and typed.
- [ ] Secrets, arbitrary URLs, business authorization, route choice and Candidate persistence remain outside the Adapter Interface.
- [ ] Deterministic adapter supports reproducible success/failure/rejection/unknown/query/cancel fixtures.
- [ ] Contract tests run unchanged against deterministic and bounded HTTP adapters.
