# Phase 4 — Reliable Image Generation, Editing, and Model Routing

**Status:** locked

## Problem Statement

Phase 3 gives CommerceVision Agent authoritative Creative Plan versions, exact Plan Approval,
server-owned Tool Policy, resumable LangGraph execution, and a deterministic fixture Planner. The
approved graph still executes `fixture.generate_image` synchronously and returns opaque
`fixture://` references. No authoritative Provider capability catalog, route decision, generation
batch, candidate image, model usage, or image-provider reconciliation exists.

Calling a real image endpoint directly from the graph would bypass the reliability and authority
already established in Phases 1–3. It would also make a timeout ambiguous: the Provider may have
accepted and billed the request even when the Worker did not receive a response. Blind retry could
therefore create duplicate paid images; switching Provider after a content rejection could bypass
safety policy; accepting a model-returned URL could create an SSRF path; and storing an API key in
configuration or logs would violate the public-repository boundary.

Phase 4 must turn the exact approved Tool Intent into a durable, routed media execution. It must
connect at least two image Provider Adapters, one real Vision/Planning Provider path, image
generation and editing, Provider task reconciliation, candidate review, and cost/usage evidence.
It must reuse the existing Durable Operation, Workflow, Outbox/Inbox, Tool Policy, Rights,
retention, object-storage, audit, and observability seams rather than create a parallel job system.

## Solution

Build five deep modules around confirmed existing seams:

1. **Provider Control Plane** owns Provider identities, immutable Endpoint Capability Versions,
   current endpoint pointers, data-region/training/retention policies, prices, quotas, routing
   groups, health/circuit observations, and versioned Route Policies. It never stores a credential;
   it stores only an opaque Secret Reference.
2. **Model Router** accepts a trusted, typed capability request and current server-owned authority
   facts. It applies hard capability/Rights/region/safety/budget filters before deterministic
   scoring, returning one immutable Route Decision plus a bounded compatible fallback sequence.
   Planner or Provider responses cannot choose a base URL, credential, model, price, quota, or
   fallback group.
3. **Media Execution** converts allowed Tool Authorization Decisions for one exact approved
   Creative Plan into one Generation Batch and deterministic Candidate Slots. Every slot owns one
   existing Durable Operation, so duplicate messages, partial completion, cancellation, unknown
   outcomes, reconciliation, and explicit DLQ replay use the established lifecycle.
4. **Provider Adapters** sit only at the true-external seam. Each Adapter normalizes one Provider
   protocol into submit/query/cancel outcomes, bounded result transfer, request identity, usage,
   price evidence, and typed errors. Deterministic Adapters support CI; at least two production
   image Adapters and one production Vision/Planning path satisfy the same public contract.
5. **Candidate Delivery** persists validated output bytes as Task Asset Versions, records immutable
   Candidate Image and Usage Records, emits aggregate-only events, and exposes workspace-scoped
   REST/SSE/Web review. A remote Provider URL is never a Candidate Image identity or durable asset.

The user-supplied `https://kuaipao.pro/v1` base URL is deployment configuration, not repository
data. Its model identifiers and protocol features remain untrusted until an authenticated contract
probe confirms them. The API key is supplied only through a mounted Secret file, re-read for each
submission, and never appears in source, `.env` examples, MySQL, tests, logs, traces, reports,
artifacts, or Git history.

## Domain Contracts

### Provider Endpoint Capability Version

Every version records:

- immutable provider identity, endpoint identity, positive version, optional superseded version,
  configured endpoint host/region, model family, model ID, pinned model revision, adapter version,
  and canonical configuration hash;
- a bounded capability set drawn from `PLAN`, `VISION`, `IMAGE_GENERATION`, and `IMAGE_EDITING`;
- supported dimensions/aspect ratios, formats, reference-image count, mask/edit/seed/LoRA support,
  maximum candidates, request/result byte budgets, and synchronous/asynchronous execution mode;
- Provider idempotency and reconciliation modes, task query/cancel capability, rate/concurrency
  limits, pricing unit/currency, data region, retention/training policy, allowed categories/roles,
  safety policy version, routing group, and enabled state;
- actor, review reason, UTC creation time, and immutable canonical SHA-256.

Publishing creates a new immutable version and advances an optimistic current pointer. Rollback
changes only the pointer. Runtime model discovery may produce an administrator review candidate,
but cannot mutate production capability or route live traffic by itself.

### Model Route Decision

- A request contains only trusted business facts: Workspace/Workflow/Plan/Approval identities,
  operation kind, image role, required capability, exact authorized Asset Version identities,
  Rights/provider constraints, size/format/count, cost ceiling, route policy version, and deadline.
- Hard filters run before scoring: enabled exact version, capability, input/output limits, region,
  Rights, category/role policy, safety policy, budget/quota, model compatibility, and circuit state.
- Scoring uses a versioned policy over quality baseline, availability, latency, remaining quota, and
  price. Stable identity is the final tie-breaker; the same inputs and observations yield the same
  ordered result.
- The immutable decision records exact endpoint capability version, route policy version, input
  hash, fallback sequence, aggregate scores/reasons, and UTC decision time. It contains no Secret.
- Failover is allowed only before dispatch or after confirmed retryable failure, and only to a
  compatible endpoint selected in the original decision. Content rejection, Rights/policy denial,
  invalid input, and unknown outcome never trigger cross-Provider failover.

### Generation Batch and Candidate Slot

- One batch binds an exact Workflow version, Creative Plan version, Plan Approval, direction key,
  allowed Tool Intent, route request hash, candidate count, retention deadline, actor, and policy
  versions. The batch cannot outlive the Workflow or source Rights.
- Candidate indexes are contiguous, bounded, and unique. Each slot derives a deterministic logical
  identity and idempotency key from the batch, slot index, authorized input, and policy versions.
- Each slot owns exactly one `IMAGE_GENERATION` or `IMAGE_EDITING` Durable Operation. Ordinary
  replay returns the same slot/operation; a different request with the same key fails.
- Generation and editing are distinct typed requests. Editing requires one exact source Asset
  Version and a mask/allowed repair scope already authorized by the approved Plan; it cannot expand
  the repair region, replace protected product facts, or introduce an arbitrary object reference.
- A slot becomes available only after result transfer, bounded image validation, output moderation,
  object persistence, Asset Version creation, Candidate Image creation, Usage Record append, audit,
  Workflow event, and Operation success converge. Partial facts remain unavailable and recoverable.

### Provider Execution and Reconciliation

- Before every dispatch, the Worker revalidates Workspace, Workflow `GENERATING`, exact current
  Plan Approval, Candidate Slot, source Asset/Rights/retention, Route Decision, endpoint version,
  quota/budget, and content policy from MySQL.
- Provider calls occur outside MySQL transactions through the existing Operation execution
  boundary. The stable Provider idempotency key is derived from the Durable Operation identity.
- The Adapter returns only typed outcomes: confirmed success, confirmed failure, content rejection,
  safe-to-retry pre-dispatch failure, or unknown after possible dispatch. Provider bodies remain
  bounded untrusted data and are retained only in the controlled artifact ledger when required.
- First accepted Provider Request/Task ID is immutable. An unknown outcome enters `RECONCILING` and
  queries that exact identity. `PENDING`, `NOT_FOUND`, query failure, or Worker restart cannot
  authorize another submission.
- A non-reconcilable endpoint that becomes unknown enters a human/operator wait after its bounded
  query budget; it is never automatically resubmitted or failed over. Confirmed failure may use the
  next compatible endpoint only within the original route/budget/safety decision.
- Cancellation is best-effort externally but authoritative internally: no late result becomes
  available after Workflow cancellation, retention expiry, Rights revocation, or slot supersession.

### Candidate Image and Usage Record

- A Candidate Image binds Workspace, Workflow, batch, slot, exact Task Asset Version, content hash,
  dimensions/format, source inputs, Plan/Prompt/Context/Retrieval provenance, endpoint capability
  version, Provider Request ID hash, moderation decision, creation time, and retention deadline.
- Candidate Image is immutable. Regeneration or editing creates a new batch/slot/candidate lineage;
  it never overwrites a prior result.
- Usage Records are append-only and deduplicated by exact Provider call identity. They record
  provider/model/endpoint version, operation/attempt, priced units, quantity, currency, unit-price
  version, estimated and actual decimal amounts, latency, reconciliation source, and UTC time.
- Provider-reported usage and configured price are separate evidence. Missing or inconsistent usage
  never becomes zero cost; it is marked unresolved and blocks budget release/reconciliation closure.

## Persistence

MySQL remains authoritative. Add the minimum versioned tables for Provider identities, endpoint
capability versions/current pointers, route policies/decisions, Generation Batches/Candidate Slots,
Candidate Images, Usage Records, and endpoint circuit/quota state. Reuse `durable_operations`,
Workflow Step/Attempt, Outbox/Inbox, idempotency, audit, assets, rights, approvals, and dead letters.

All tenant-owned tables place `workspace_id` before caller-supplied IDs in primary/unique/index
identities. Runtime times are timezone-aware UTC mapped to `DATETIME(6)`. Money uses exact
`DECIMAL(20,6)` plus an explicit ISO currency; floats are forbidden. Large request/response bodies,
image bytes, masks, and Provider artifacts use controlled object references plus SHA-256 and inherit
the Workflow retention ceiling.

The command that creates a Generation Batch, Candidate Slots, Durable Operations, audit, and Outbox
commits atomically. Result convergence commits the Candidate Image, Asset Version, Usage Record,
events, and Operation terminal fact atomically or remains unavailable for idempotent recovery.

## Public Interfaces and Confirmed Test Seams

Tests observe behavior only through these proposed seams:

1. Provider Control Plane publish/rollback and Model Router domain/application commands.
2. Image Generation, Image Editing, and Planning Provider Adapter contracts, exercised by
   deterministic and bounded HTTP Adapters.
3. Exact approved-plan generation command and workspace-scoped REST reads/conflicts.
4. Durable Worker/Event execution and reconciliation with real MySQL, object storage, and fault
   injection around dispatch/result commits.
5. LangGraph `GENERATING` wait/resume using MySQL Candidate/Operation authority across restart.
6. Persisted Workflow SSE and Web Candidate Workbench review/retry/cancel behavior.
7. Cost/usage, circuit/quota, content-safety, security, chaos, and release-acceptance gates.

Repository helpers, mappers, transport parsers, scoring helpers, and internal collaborators are not
independent test seams.

## HTTP and SSE

- Administrator endpoints publish/read/rollback endpoint capability versions and route policies;
  no endpoint returns Secret References to ordinary users or accepts raw credentials.
- Generation commands are normally internal consequences of exact Plan Approval. Any public retry,
  cancel, or regenerate command requires idempotency, expected Workflow/batch/slot versions, and
  current authorization.
- Workspace-scoped reads expose batches, slots, candidate provenance, aggregate usage, route reason,
  safety state, and stable error classes without raw Prompt, Provider payload, signed URL, key, or
  private endpoint details.
- Persisted SSE events cover batch/slot accepted, routed, dispatched, reconciling, candidate ready,
  rejected, failed, cancelled, and usage resolved. Resumable cursor semantics remain Phase 3's
  opaque workspace/Workflow-scoped contract.

## Web

The Candidate Workbench displays exact Plan/direction/batch versions, candidate status and
provenance, model capability label, safety state, aggregate cost, and failures. It never renders a
Provider URL directly. Preview/download use short-lived controlled Asset URLs. Refresh and SSE
reconnect recover from MySQL; browser state cannot authorize retry, failover, edit, or selection.

Editing requires selecting an exact Candidate/Task Asset Version and the Plan-approved repair scope.
Conflicts reload current authority and preserve only non-authorizing draft UI state. Cancellation or
retention expiry removes every action and preview surface immediately.

## Security, Privacy, and Reliability

- Credentials are resolved from mounted bounded regular files for every submission; production
  fails closed without the file. `repr`, logs, exceptions, telemetry, reports, MySQL, and artifacts
  contain no secret or Authorization header.
- Endpoint base URL/model/region and response-download hosts come only from reviewed server-owned
  capability versions. HTTPS, exact host allowlists, DNS/private-network blocking, redirect
  revalidation, response byte/pixel/type bounds, and deadlines apply to every transfer.
- Input and output moderation are mandatory. A content rejection or policy denial is terminal for
  that authorized request and cannot be retried against another Provider. Safety versions are part
  of route, attempt, and candidate provenance.
- Rights, retention, Plan Approval, quota, and budget are rechecked before dispatch and before a
  late result becomes available. Revocation stops new use first; object/vector/checkpoint cleanup
  converges through existing deletion tombstones and Durable Operations.
- Provider response text, OCR, URLs, task IDs, headers, prices, and usage are untrusted. They cannot
  modify Tool Registry, route policy, permissions, budget, Secret References, or infrastructure.
- Duplicate delivery, Worker crash before/after dispatch or result commit, transport timeout,
  Provider 429/5xx, partial response, malformed image, task-query failure, circuit transition,
  cancellation, Rights expiry, and storage failure converge without duplicate paid submission,
  duplicate Candidate/Usage facts, stale availability, or retention extension.

## Provider Baseline

- Adapter A: `kuaipao.pro` OpenAI Images-compatible synchronous generation and editing. The current
  deployment exposes generation, editing, alias editing, and token-scoped model-discovery routes,
  but its documented async routes return `404 Invalid URL`; async submit/query therefore remain
  disabled. Successful response variants, edit media type, models, limits, and prices are enabled
  only by a bounded authenticated contract probe. Unknown synchronous outcomes are not resubmitted.
- Adapter B: Alibaba Cloud Model Studio Wan 2.7 using its independently owned, region-bound protocol.
  The first production path uses async submit/query so Durable Operation reconciliation is backed by
  an exact Provider task identity. Sync and async forms are separate endpoint capabilities and
  parsers. Deterministic Adapter does not count as the second production Provider.
- Planning/Vision: existing Alibaba Vision remains valid for Vision analysis. Alibaba Model Studio
  Qwen Chat Completions JSON mode is the first production structured Planning Adapter, with a
  reviewed region/workspace endpoint and pinned model capability version. Its JSON is still
  untrusted and must pass the existing Creative Plan contract; deterministic Planner remains CI.
- CI and public Demo use deterministic Adapters and mock HTTP transports. Live Provider smoke is an
  explicit secret-enabled deployment gate, not a public pull-request requirement.

## Evaluation and Exit Criteria

Phase 4 is complete only when:

1. An unapproved/rejected/stale Plan, denied Tool Intent, revoked Right, expired Workflow, exhausted
   quota, or exceeded budget cannot dispatch or make a candidate available.
2. At least two production image Adapters and one real Vision/Planning path satisfy the frozen
   contract; deterministic parity covers public CI without credentials.
3. A compatible endpoint can take over only after safe pre-dispatch or confirmed failure; content
   rejection, policy denial, and unknown outcome never trigger bypass or duplicate submission.
4. The same logical candidate idempotency key produces at most one effective Candidate Image and one
   charged Usage identity across message replay, Worker restart, failover, and reconciliation.
5. Provider task ID, exact model/capability/policy, inputs, result asset, safety, latency, units,
   price, cost, and reconciliation history are observable without exposing secrets or raw payloads.
6. Generation/editing REST, MySQL, Event, LangGraph restart, SSE reconnect, Candidate Web E2E,
   migrations, provider contract/fault injection, content safety, OpenAPI, dependencies, licenses,
   containers, secret scan, SBOM, and Phase 4 acceptance are green on one final Git SHA.

## Non-goals

- Visual/OCR/LLM Judge, Reflection, Repair Plan generation, automated best-candidate selection, or
  replay comparison; these are Phase 5.
- Amazon export, ERP synchronization, batch submission, or Webhooks; these are Phase 6.
- Arbitrary user-supplied base URLs, models, executable Prompt, MCP Provider access, dynamic code,
  or a general unconstrained tool loop.
- Training/fine-tuning/LoRA upload, GPU hosting, model weight management, or a general model-market
  abstraction.
