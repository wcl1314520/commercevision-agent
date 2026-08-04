# Phase 3 — Auditable Planning and Human-in-the-loop

**Status:** locked

## Problem Statement

Phase 2 gives CommerceVision Agent durable workflows, confirmed ProductBriefs, published Brand
Profiles, rights-first Retrieval Citations, and recoverable human confirmation. The planning portion
of the current fixture graph still creates an opaque `fixture://creative-plan/...` reference. The
generic approval endpoint checks Workflow state and Workflow version, but no authoritative Creative
Plan record exists against which the submitted subject can be fenced.

The system therefore cannot yet prove what facts, Prompt revision, retrieval evidence, proposed
tools, or reviewer decision authorized a later generation. A stale browser can submit an approval
for an unrelated subject while the Workflow is awaiting plan approval. Planner output also has no
server-owned policy layer separating model intent from execution authority.

Phase 3 must turn planning into a versioned, reviewable control-plane capability. It must not call a
real image-generation provider; generation, editing, provider routing, and provider reconciliation
remain Phase 4 work.

## Solution

Build five deep modules around existing Phase 1–2 seams:

1. **Creative Planning** owns structured Creative Plan payloads, immutable versions, canonical
   hashes, revision lineage, and the current-version head. Its external Interface creates, revises,
   reads, lists, and resolves the exact current version.
2. **Planning Context** deterministically builds a bounded snapshot from one confirmed ProductBrief
   version, an optional published Brand Profile version, eligible Retrieval Citations, and a
   versioned context policy. It never accepts arbitrary chat history, URL, path, SQL, or object key.
3. **Prompt Registry** publishes immutable Prompt Revisions and resolves the exact production
   revision selected for the Planner node. A Workflow stores the resolved revision, not a mutable
   alias.
4. **Plan Approval** reuses the append-only Approval, Unit of Work, Workflow transition, Outbox,
   Inbox, idempotency, and LangGraph interrupt/resume infrastructure. Before committing approval it
   fences the submitted subject against the authoritative current Creative Plan version in the same
   MySQL transaction.
5. **Tool Policy** treats Planner Tool Intents as untrusted proposals. It applies a server-owned
   allowlist, node/state checks, exact plan approval, typed parameter validation, resource
   resolution, Rights, quota, and budget rules before any future execution is authorized.

The Planner first ships with a deterministic Fixture implementation so Creative Plan fixtures,
Agent Eval, recovery, and security gates are reproducible. The first real planning Provider Adapter
is delivered with Phase 4 capability routing; Phase 3 does not create a speculative provider port
or call an external model.

## Domain Contracts

### Creative Plan Version

Every version records:

- immutable `id`, `workspace_id`, `workflow_id`, stable `creative_plan_id`, and positive
  `version_number`;
- optional `supersedes_version_id` and source `AGENT` or `USER`;
- exact ProductBrief ID/version/hash;
- optional Brand Profile ID/version/hash;
- Retrieval Run ID and a bounded ordered set of Retrieval Citation IDs;
- Context policy version and Planning Context SHA-256;
- Prompt ID, semantic revision, and Prompt content SHA-256;
- structured plan payload, canonical payload SHA-256, actor, reason for human revision, and UTC
  creation time.

The structured payload contains a bounded set of image directions. Each direction records a stable
key, image role, scene, composition, camera, lighting, color direction, product-protection
constraints, required elements, prohibited elements, selected citation IDs with reasons, proposed
Tool Intents, candidate count, quality targets, and allowed repair scope. Free text is bounded and
control characters are rejected. Collections are immutable, bounded, duplicate-free, and
deterministically ordered for hashing.

Edits always create the next version. They cannot mutate an existing version or change the stable
Creative Plan identity. Agent versions require Prompt and Planning Context provenance. User versions
require actor and revision reason and retain the prior provenance they are editing.

### Plan Approval

- An approval targets exactly one `creative_plan_id`, plan version, and expected Workflow version.
- Approval is valid only while that plan version is the authoritative current version for the same
  workspace and Workflow and the Workflow is `AWAITING_PLAN_APPROVAL`.
- `APPROVE` transitions to `GENERATING`; `REJECT` returns to `PLANNING` and the next edit creates a new
  version.
- A stale or foreign subject returns a stable conflict without Approval, Workflow transition,
  Outbox, audit, checkpoint, or other side effect.
- Repeating the same idempotent command returns the original result; reusing its key for different
  content fails.
- Approval is authorization evidence, not mutable status on the Creative Plan row.

### Tool Intent and Policy

- Tool Intent contains a registered tool name, schema version, typed arguments, purpose, and budget
  estimate; it never contains credentials or arbitrary URL/path/SQL/object-key authority.
- A Planner may propose only. The server derives actor, workspace, Workflow, plan approval, available
  node, resource identities, Rights, provider constraints, quota, and budget from trusted facts.
- Prompt Injection content remains data. It cannot register a tool, change an allowlist, select a
  provider outside policy, grant Rights, increase budget, or bypass approval.
- Authorized intents become future Phase 4 execution commands with deterministic idempotency keys;
  rejected intents remain auditable and have no external side effects.

## Persistence

MySQL remains authoritative. Add versioned `creative_plans`, `creative_plan_versions`,
`prompt_revisions`, and the minimum Planning Context provenance tables or immutable JSON columns
needed to reproduce a version. All tenant-owned tables place `workspace_id` before caller-supplied
IDs in keys or indexes. All runtime times are timezone-aware UTC mapped to `DATETIME(6)`.

Creative Plan versions and Prompt Revisions are immutable after insert. The Creative Plan head uses
optimistic versioning. Approval, head advancement, Workflow transition, audit, idempotency, and
Outbox facts that belong to one command commit atomically. Large or sensitive bodies use controlled
object references plus hashes and inherit the Workflow retention deadline; logs and events carry
only aggregate metadata.

## Public Interfaces and Confirmed Test Seams

Tests observe behavior only through these confirmed seams:

1. Creative Planning domain/application commands.
2. REST create/read/list/revise/approve endpoints and stable conflict responses.
3. Durable Worker/Event handling with real MySQL and existing Outbox/Inbox lifecycle.
4. LangGraph Planner and approval Interrupt/Resume across Worker restart.
5. SSE Workflow event stream with opaque resumable cursor.
6. Web Creative Plan editor/approval workbench.
7. Deterministic Planner fixtures, Tool Policy fixtures, and Agent Eval release gate.

Repository helpers, mappers, validators, and internal collaborators are not independent test seams.

## HTTP and SSE

- HTTP reads are workspace-scoped and expose immutable versions plus current head metadata.
- Create/revise/approve commands require idempotency and expected versions.
- Conflicts distinguish stale Workflow, stale plan head, mismatched approval subject, invalid state,
  and idempotency reuse without exposing other-tenant existence.
- SSE emits only persisted events in stable order. The cursor is opaque, bounded, workspace-scoped,
  and resumes after the last delivered event without gaps or duplicate business effects.
- Slow/disconnected clients do not hold MySQL transactions, Worker leases, or model calls.

## Web

The Workbench shows exact plan version, provenance, selected Retrieval Citations, proposed tools,
policy decisions, and approval history. Edits create a new version. Approve/Reject always submits the
visible plan and Workflow versions. A `409` reloads current state and never silently reapplies stale
edits. Refresh and SSE reconnect preserve the current review position without local authorization
state.

## Security, Privacy, and Reliability

- Workspace identity is validated before any lookup; foreign resources are not distinguishable from
  missing resources.
- Planning Context rejects raw provider payloads, arbitrary URLs/paths/SQL, unapproved assets,
  expired Rights, secrets, and unbounded text or collections.
- Prompt and plan payloads are data, not instructions to infrastructure. Tool Policy is always
  server-owned and fail-closed.
- No approval or execution authorization is inferred from LangGraph Checkpoint alone. MySQL current
  facts are rechecked when consuming resume events and before every future execution claim.
- Duplicate delivery, Worker interruption before/after commit, stale browser approval, prompt
  injection, SSE reconnect, and retention expiry converge without duplicate plan versions,
  duplicate approvals, unauthorized intent, or retention extension.

## Evaluation and Exit Criteria

Phase 3 is complete only when:

1. An unapproved or rejected Creative Plan cannot authorize execution.
2. Prompt Injection cannot add tools, permissions, providers, resources, or budget.
3. Every plan version traces to exact ProductBrief, Brand Profile, Retrieval Citations, Planning
   Context policy/hash, and Prompt Revision/hash.
4. A stale page cannot approve or overwrite a newer plan version.
5. Planner fixtures and Agent Eval pass versioned quality, determinism, provenance, policy, and
   latency thresholds.
6. HTTP, MySQL, Event, LangGraph restart, SSE reconnect, Web E2E, migration, OpenAPI, security,
   dependency, license, container, and SBOM gates are green on one final commit.

## Non-goals

- Real image generation or editing.
- Multi-provider Capability Registry, routing, failover, cost reconciliation, or candidate images.
- Reflection, Repair Plan, visual/OCR Judge, or replay comparison.
- Amazon export, ERP synchronization, batch submission, or Webhooks.
- A general chat Agent, unconstrained tool loop, user-authored executable Prompt, arbitrary MCP
  access, or mutable production Prompt.
