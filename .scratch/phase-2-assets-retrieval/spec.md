# Phase 2 — Assets, Product Understanding, and Multimodal Memory

**Status:** ready-for-agent

## Problem Statement

CommerceVision Agent currently has a durable Workflow runtime, but it cannot safely accept real
commerce assets, establish whether they may be used, understand a product, reuse brand material, or
retrieve multimodal references for later Agent planning. The Phase 1 fixture proves durable execution
only. It does not provide a production asset lifecycle, direct object upload, image validation,
rights enforcement, ProductBrief confirmation, versioned brand context, incremental embedding,
hybrid retrieval, MCP tools, retention cleanup, or rebuild capability.

The target user is an ecommerce art team operating a privately deployed enterprise system. The team
needs to work with beauty, automotive accessory, food, apparel, and future product categories. Users
must be able to upload Task Assets and register reusable Foundation Assets without allowing
unvalidated, unauthorized, expired, malicious, or stale content to reach a model or retrieval result.
Human review must remain authoritative for low-confidence product facts.

The system must preserve the Phase 1 durability guarantees. MySQL must remain the transactional
source of truth. MinIO or OSS may store binary objects, and Milvus may store rebuildable vectors, but
neither may independently authorize use. All processing must be idempotent, recoverable, auditable,
and observable. Task Assets must be removed after 72 hours from Workflow creation. Foundation Assets
must remain until an administrator deletes them or their rights expire, whichever occurs first.

## Solution

Build a production-grade asset and retrieval subsystem around a small set of deep modules:

1. An Asset Registry owns Asset, Asset Version, Upload Session, Rights Record, retention, and
   lifecycle invariants.
2. An Object Storage module issues constrained presigned uploads, verifies finalized objects, keeps
   new objects in quarantine, and promotes only validated objects.
3. A Durable Asset Processing module reuses the existing Unit of Work, Outbox, Inbox, Lease, Retry,
   DLQ, and recovery patterns for validation, Vision analysis, indexing, deletion, and rebuild work.
4. A Product Understanding module produces evidence-backed, versioned ProductBriefs and requires
   human confirmation when confidence or policy requires it.
5. A Brand Profile module publishes immutable versions assembled only from currently usable
   Foundation Assets.
6. An Indexing module writes versioned image and multimodal vectors to Milvus incrementally while
   recording every index fact in MySQL.
7. A Retrieval module applies MySQL rights filtering before recall, combines dense and lexical
   candidates under a versioned Retrieval Policy, rechecks rights before return, and emits Retrieval
   Citations.
8. Product Catalog and Asset MCP tools expose only controlled domain operations and temporary
   references, never arbitrary URLs, storage keys, SQL, or credentials.
9. Retention, rights-expiry, reconciliation, and rebuild runners converge MySQL, object storage,
   Milvus, and caches after deletion or failure.
10. OpenTelemetry, fixed retrieval datasets, and fault-injection tests provide production evidence
    for correctness, recovery, quality, and unauthorized-recall guarantees.

The first production provider family is Alibaba Cloud Model Studio for Vision, multimodal embedding,
optional reranking, and content safety. Provider-facing interfaces remain vendor-neutral. Model IDs,
dimensions, prompt versions, endpoint regions, and policy versions are configuration data and are
recorded on every result. MinIO is the local object-storage Adapter; an OSS Adapter satisfies the
same interface for Alibaba Cloud deployment.

## User Stories

1. As an ecommerce artist, I want to create an upload session for a product image, so that I can
   upload directly to object storage without routing image bytes through the Control API.
2. As an ecommerce artist, I want an upload session to state the accepted MIME type, byte limit,
   image dimension limit, checksum requirement, and expiry, so that invalid uploads fail early.
3. As an ecommerce artist, I want a repeated create-upload request with the same idempotency key to
   return the same session, so that client retries do not create duplicate assets.
4. As an ecommerce artist, I want to finalize an upload after the object is present, so that the
   platform can verify the exact object before processing it.
5. As an ecommerce artist, I want repeated finalize calls to return the same Asset Version, so that
   network retries do not create duplicate versions.
6. As a security administrator, I want every new object to remain in quarantine until validation
   succeeds, so that unsafe content cannot be downloaded, analyzed, indexed, or sent to a model.
7. As a security administrator, I want extension, declared MIME, detected MIME, file magic, complete
   decode, byte size, dimensions, frame count, pixel count, and metadata limits checked, so that
   malformed and deceptive image uploads are rejected.
8. As a security administrator, I want an antivirus scan and provider content-safety result attached
   to each image Asset Version, so that unsafe content is blocked with auditable evidence.
9. As a compliance reviewer, I want authenticity and provenance evidence recorded without unsupported
   claims, so that absence of evidence is not reported as proof that an image is authentic.
10. As an ecommerce artist, I want to upload registered LoRA files as Foundation Assets, so that they
    can be governed now and invoked by a later generation phase.
11. As a security administrator, I want LoRA registration restricted to non-executable safe tensor
    formats and malware scanning, so that registration cannot introduce unsafe pickle payloads.
12. As an ecommerce artist, I want to register versioned Prompt templates and model configurations as
    Foundation Assets, so that later Agent runs can reference governed configuration.
13. As a compliance reviewer, I want to register a Rights Record containing owner, source, allowed
    uses, allowed providers, derivative permission, validity period, and evidence reference, so that
    the platform can decide whether an asset may be used.
14. As a compliance reviewer, I want Rights Records to be immutable and versioned, so that historical
    authorization decisions remain auditable.
15. As a compliance reviewer, I want only one current Rights Record selected for an Asset at a time,
    so that authorization is deterministic.
16. As an administrator, I want to revoke or replace a Rights Record, so that the asset becomes
    unusable immediately in MySQL before asynchronous cleanup completes.
17. As an administrator, I want expired rights detected automatically, so that expired assets stop
    appearing in retrieval and cannot be sent to providers.
18. As an ecommerce artist, I want to register a Foundation Asset independently of a Workflow, so
    that approved brand and reference material can be reused.
19. As an ecommerce artist, I want to attach a Task Asset to a Workflow, product, and optional SKU,
    so that its 72-hour retention boundary is deterministic.
20. As an administrator, I want Foundation Assets retained until deletion or rights expiry, whichever
    occurs first, so that reusable material is available only while lawful.
21. As an ecommerce artist, I want product records and SKU records to preserve external identifiers,
    categories, brands, and structured attributes, so that later ERP integration has a stable domain
    contract.
22. As an ecommerce artist, I want the Vision analyzer to generate a ProductBrief from eligible
    product images and product metadata, so that I do not need to rewrite basic product facts.
23. As an ecommerce artist, I want each ProductBrief field to show confidence, evidence, source Asset
    Version, and conflict state, so that I can judge the model's reasoning.
24. As an ecommerce artist, I want beauty-specific fields such as form, package, finish, and sensitive
    claim flags, so that the ProductBrief is useful for beauty creative work.
25. As an ecommerce artist, I want automotive-accessory fields such as part type, compatibility,
    material, fitment evidence, and safety-claim flags, so that the ProductBrief is useful for
    automotive creative work.
26. As an ecommerce artist, I want common fields to work for future categories without schema
    replacement, so that the platform can expand beyond the first two evaluation categories.
27. As an ecommerce artist, I want mandatory low-confidence, conflicting, or sensitive fields to
    require human confirmation, so that uncertain model output does not become accepted product fact.
28. As an ecommerce artist, I want to edit a ProductBrief and preserve my reason and evidence, so
    that human corrections are auditable.
29. As an ecommerce artist, I want optimistic version checks while editing a ProductBrief, so that my
    changes do not silently overwrite another reviewer.
30. As an ecommerce artist, I want to confirm a specific ProductBrief version, so that later Agent
    planning can reference an immutable approved snapshot.
31. As an administrator, I want to define brand rules, colors, required marks, prohibited elements,
    tone constraints, and copy constraints, so that creative work follows brand requirements.
32. As an administrator, I want a Brand Profile draft to include only usable Foundation Assets, so
    that invalid or expired assets cannot be published into brand context.
33. As an administrator, I want each Brand Profile publication to create an immutable version, so
    that a Workflow can reproduce the exact brand constraints it used.
34. As an administrator, I want deleted or expired Foundation Assets excluded from the next Brand
    Profile version, so that stale rights do not remain active.
35. As an operator, I want every usable image Asset Version to request embedding through an Outbox
    event, so that indexing is durable and recoverable.
36. As an operator, I want separate vector kinds for image-only and controlled image-plus-text
    embeddings, so that retrieval behavior is explicit and evaluable.
37. As an operator, I want embedding model ID, actual model version, dimension, vector kind, input
    hash, and collection recorded in MySQL, so that index state is reproducible.
38. As an operator, I want repeated index requests to upsert the same logical vector, so that worker
    retries do not create duplicates.
39. As an operator, I want a failed embedding or Milvus write to retry with a lease and bounded retry
    budget, so that temporary provider failures recover without duplicate work.
40. As an operator, I want exhausted processing work in a DLQ with a reason and replay path, so that
    failures can be investigated and resumed.
41. As an operator, I want collection identity to include model family, pinned version, vector kind,
    and dimension, so that incompatible vectors never mix.
42. As an operator, I want model upgrades to use a new collection, optional dual write, backfill,
    evaluation, atomic policy switch, and retirement, so that index upgrades do not corrupt live
    retrieval.
43. As an Agent caller, I want to submit a structured Retrieval Query, so that retrieval intent is
    explicit and not hidden in free-form SQL or filters.
44. As an Agent caller, I want MySQL to filter by workspace, category, brand, use, provider,
    derivative permission, asset state, and rights time before recall, so that ineligible assets do
    not enter the candidate pool.
45. As an Agent caller, I want dense image or multimodal recall combined with MySQL FULLTEXT recall,
    so that both semantic and exact product terms are represented.
46. As an Agent caller, I want explicitly selected references and published brand assets considered
    as controlled candidate sources, so that user intent is not lost.
47. As an Agent caller, I want a versioned RRF and business-scoring policy, so that ranking changes
    are reproducible and measurable.
48. As an Agent caller, I want optional provider reranking limited to already authorized candidates,
    so that reranking cannot introduce an unauthorized asset.
49. As an Agent caller, I want MySQL rights rechecked immediately before results are returned, so
    that stale Milvus entries cannot bypass revocation or expiry.
50. As an Agent caller, I want each result to include a Retrieval Citation with Asset Version, Rights
    Record version, Retrieval Policy version, score breakdown, and use reason, so that context is
    explainable.
51. As an ecommerce artist, I want a temporary controlled preview reference for an authorized asset,
    so that I can inspect it without receiving storage credentials or permanent URLs.
52. As an Agent developer, I want MCP tools to read product, ProductBrief, Brand Profile, and asset
    search results, so that the Agent uses typed domain tools instead of database access.
53. As a security administrator, I want MCP to reject arbitrary URLs, object keys, file paths, SQL,
    cross-workspace identifiers, and unapproved provider use, so that tools do not become an SSRF or
    data-exfiltration path.
54. As an operator, I want Task Assets and task-bearing ProductBrief drafts, retrieval traces, and
    temporary references to expire at the Workflow retention deadline, so that task data is not kept
    beyond 72 hours.
55. As an operator, I want deletion to write a MySQL tombstone before deleting vectors and objects,
    so that use stops immediately even when external cleanup is delayed.
56. As an operator, I want deletion steps to be idempotent and resumable, so that partial cleanup
    converges after worker or dependency failure.
57. As an operator, I want a reconciliation scan to detect missing vectors, stale vectors, orphaned
    quarantine objects, and stuck operations, so that silent drift is repaired.
58. As an operator, I want to rebuild a Milvus collection from current MySQL and object-storage facts
    with checkpoints and validation, so that vector storage remains disposable.
59. As an operator, I want a rebuild never to restore a revoked, expired, blocked, or deleted asset,
    so that disaster recovery preserves rights enforcement.
60. As an operator, I want spans, metrics, structured error classes, and trace IDs across upload,
    validation, Vision, embedding, indexing, retrieval, deletion, and rebuild, so that failures are
    diagnosable.
61. As a project evaluator, I want fixed beauty and automotive-accessory query sets with relevance
    judgments, so that retrieval quality can be compared over time.
62. As a project evaluator, I want Recall@K, Precision@K, MRR, nDCG, P50, P95, and unauthorized
    recall reported by Retrieval Policy version, so that quality and safety regressions block release.
63. As a project evaluator, I want unauthorized recall to be exactly zero, so that release evidence
    demonstrates the rights boundary.
64. As a project evaluator, I want fault injection for object storage, Milvus, providers, RabbitMQ,
    and workers, so that durability claims are supported by recovery evidence.
65. As a public-demo operator, I want demo assets, buckets, provider credentials, quotas, and datasets
    isolated from private deployment data, so that the public demo cannot expose enterprise content.
66. As an ecommerce artist, I want to upload assets and see their quarantine, validation, rights, and
    index status in the Web workbench, so that I can operate the lifecycle without API tooling.
67. As an ecommerce artist, I want to register or replace a Rights Record in the Web workbench, so
    that lawful use is part of the normal creative workflow.
68. As an ecommerce artist, I want to review ProductBrief evidence, edit uncertain fields, and confirm
    a version in the Web workbench, so that human-in-the-loop is visible and usable.
69. As a brand administrator, I want to draft and publish a Brand Profile in the Web workbench, so
    that reusable brand constraints can be managed without database access.
70. As an operator or evaluator, I want a retrieval explorer showing filters, degraded state, score
    breakdowns, and Retrieval Citations, so that search behavior can be demonstrated and debugged.

## Implementation Decisions

### Module and Seam Design

- The Asset Registry is the public interface for creating upload sessions, finalizing uploads,
  registering or replacing Rights Records, reading Asset state, requesting deletion, and computing
  current usability. Callers do not manipulate Asset state directly.
- The Product Understanding module is the public interface for requesting analysis, reading evidence,
  revising fields, and confirming a ProductBrief version. Provider prompts and response parsing remain
  hidden inside the module.
- The Brand Profile module is the public interface for drafting, validating, publishing, and reading
  immutable Brand Profile versions.
- The Indexing module is the public interface for requesting an index operation, reconciling an Asset
  Version, rebuilding a collection, and reporting index state. Milvus details do not leak to callers.
- The Retrieval module is the public interface for executing a structured Retrieval Query and
  returning Retrieval Citations. Rights filtering, dense recall, lexical recall, fusion, optional
  reranking, deduplication, and final rights verification remain inside the module.
- Provider interfaces live at explicit seams for object storage, malware scanning, content safety,
  Vision analysis, embedding, optional reranking, and vector indexing. Each has at least a deterministic
  test Adapter and a production Adapter.
- Durable processing is represented by one generic operation lifecycle used by asset validation,
  ProductBrief analysis, embedding/indexing, deletion, reconciliation, and collection rebuild. It
  reuses the existing reliable-message infrastructure and does not create a second Outbox, Inbox,
  Retry, Lease, or DLQ system.
- The Worker routes versioned event types to registered handlers. Unknown event types are permanent
  failures routed to the DLQ; they are never silently acknowledged.
- Celery transport retries are limited to transport redelivery. The MySQL Durable Operation is the
  single business retry authority and owns retry timing, budget, and failure classification.
- The Scheduler runs independent scanners behind small interfaces for retention, rights expiry,
  operation recovery, index reconciliation, quarantine cleanup, and rebuild progress. A scanner
  failure is isolated and observable instead of stopping every scheduler function.
- Asset application interfaces, reliable-message interfaces, and read-query interfaces remain
  separate. The Phase 1 catch-all Unit of Work protocol is not expanded with additional untyped
  `Any` or `**kwargs` methods.

### Domain Model

- An Upload Session is an independent aggregate and owns the pre-finalize upload lifecycle. Uploading
  is not an Asset state. An intended Asset ID may be reserved, but the Asset is created only after
  finalize proves the object.
- An Asset is the stable identity and policy aggregate. It belongs to one workspace, has one retention
  class, optional Workflow/product/SKU associations, a current Asset Version, a current Rights Record,
  and a lifecycle state.
- An Asset Version is an immutable representation of uploaded bytes and derived metadata. Replacing
  content creates a new version; it never mutates the prior byte identity.
- A Rights Record is immutable and versioned. An Asset points to the selected current record. Replacing,
  revoking, or expiring a record updates the Asset usability state in the same MySQL transaction and
  emits cleanup or reindex events.
- A ProductBrief has a mutable identity row and immutable versions. A version contains common fields,
  category extension fields, per-field confidence, evidence, conflict state, model provenance, human
  edits, and status.
- A Brand Profile has a mutable identity row and immutable versions. A published version references
  exact Foundation Asset Versions and exact Rights Record versions.
- An Embedding Record is the MySQL fact for one Asset Version, vector kind, model configuration,
  dimension, and collection. It is not the vector itself.
- A Retrieval Policy is immutable and versioned. It defines eligibility rules, candidate limits,
  vector kinds, lexical fields, RRF parameters, business weights, rerank settings, deduplication, and
  context limits.
- A Retrieval Citation is the returned and optionally persisted explanation of one selected Asset
  Version under one Rights Record and Retrieval Policy version.
- A Durable Operation identifies one operation kind, target aggregate and target version. It holds
  status, lease, attempt budget, next retry, input/output references, error classification, and
  optimistic version.

### Lifecycle States

- Upload Session states are `OPEN`, `FINALIZING`, `FINALIZED`, `EXPIRED`, and `ABORTED`.
- Asset states are `QUARANTINED`, `VALIDATING`, `PENDING_RIGHTS`, `PENDING_REVIEW`, `AVAILABLE`,
  `BLOCKED`, `RIGHTS_EXPIRED`, `DELETING`, `DELETED`, and `FAILED`.
- Asset transitions are explicit. `AVAILABLE` requires a finalized Asset Version, passing mandatory
  validation results, and an active Rights Record. `BLOCKED`, `RIGHTS_EXPIRED`, `DELETING`, and
  `DELETED` are never retrievable.
- A rejected upload remains auditable in MySQL while its quarantine object is scheduled for deletion.
  It does not become a usable Asset Version.
- `DELETED` is terminal. If rights have already expired and cleanup completed, later rights require a
  new upload, Asset Version, and Rights Record rather than resurrection.
- ProductBrief version states are `DRAFT`, `AWAITING_CONFIRMATION`, `CONFIRMED`, and `ARCHIVED`.
  Only a `CONFIRMED` version may be selected by later Agent planning.
- Brand Profile states are `DRAFT`, `ACTIVE`, `NEEDS_REPUBLISH`, and `ARCHIVED`. Publication is
  append-only, and historical versions never override current rights.
- Embedding Record states are `PENDING`, `PROCESSING`, `INDEXED`, `RETRYABLE_FAILED`,
  `PERMANENT_FAILED`, `STALE`, `DELETE_PENDING`, and `DELETED`.
- Durable Operation states are `PENDING`, `CLAIMED`, `RUNNING`, `RECONCILING`, `WAITING_HUMAN`,
  `RETRYABLE_FAILED`, `SUCCEEDED`, `FAILED`, and `CANCELLED`, using the same lease and retry semantics
  as Phase 1.
- Collection states are `PLANNED`, `CREATING`, `BACKFILLING`, `VERIFYING`, `READY`, `ACTIVE`,
  `RETIRING`, `RETIRED`, and `FAILED`.

### Retention and Deletion

- Task Asset expiry is copied from its owning Workflow and therefore remains anchored to Workflow
  creation time. Re-uploading or reprocessing does not extend the deadline.
- Foundation Asset expiry is the earlier of administrator deletion and the current Rights Record's
  `valid_until`. A missing `valid_until` is allowed only when policy permits perpetual rights; the
  administrator can still delete the asset.
- Task-bearing ProductBrief versions, retrieval runs, temporary access references, raw Vision
  responses, and task-derived search documents inherit the Workflow expiry.
- Foundation metadata and published Brand Profile versions remain while their source rights permit
  retention, but a published version cannot authorize a now-invalid Asset.
- Deletion first marks the MySQL Asset and current versions unusable and records a tombstone plus
  Outbox event. Vector, object, search-document, and cache cleanup happen asynchronously.
- Cleanup is idempotent. Missing vectors or objects count as successful convergence. A failed external
  deletion remains retryable without restoring usability.
- Delete events include exact Asset Version and deletion generation identifiers so an old event cannot
  delete a later version.
- Audit metadata must not contain original bytes, secrets, full raw prompts, full OCR, or full provider
  responses. Audit retention is configured independently but cannot retain task payloads beyond their
  permitted lifetime.

### MySQL Schema

- Existing tables and all new temporal columns continue to use UTC `DATETIME(6)`.
- All mutable aggregates carry an integer optimistic `version`.
- IDs use ordered UUIDv7 strings under the existing project convention.
- New product tables store products, SKUs, workspace ownership, external identifiers, categories,
  brands, structured attributes, source versions, expiry, and timestamps. External identifiers are
  unique within a workspace and source namespace.
- `upload_sessions` stores workspace, actor, intended retention class and associations, generated
  quarantine location, upload policy version, expected SHA-256/MIME/length, status, finalize lease,
  attempts, expiry, finalize result, optimistic version, and timestamps. Storage location and
  idempotency constraints ensure one effective object and one effective finalize result.
- `assets` stores workspace, retention class, asset kind, Workflow/product/SKU/brand associations,
  status, current Asset Version and Rights Record pointers, version, retention deadline, block reason,
  deletion generation, and timestamps.
- `asset_versions` stores immutable content identity and metadata: Asset ID, version number, Upload
  Session, SHA-256, byte size, declared and detected MIME, dimensions or format-specific metadata,
  category, role, validation policy version, validation summary, and timestamps.
- `asset_objects` stores logical object role, backend, bucket, generated key, object version, opaque
  ETag, state, optimistic version, and deletion timestamps. Object location is not stored directly on
  the Asset aggregate.
- Rights Records contain Asset and optional Asset Version, monotonically increasing version, decision,
  owner or holder reference, source, license, derivative permission, public-demo permission, evidence
  reference, terms hash, valid-from, exclusive valid-until, perpetual flag, superseded-record
  reference, actor, and timestamps.
- Allowed uses and allowed providers use normalized child rows so hard authorization queries do not
  depend on unindexed JSON scans. Empty sets mean deny, not allow all.
- Asset validation results are append-only per Asset Version and validator version. They record result
  kind, verdict, structured evidence reference, provider/model version where applicable, and
  timestamps.
- Durable Operations use a generic table with a unique logical key consisting of operation kind,
  target type, target ID, target version, and input hash. Lease, retry, reconciliation, error, and
  optimistic version columns follow the existing runtime semantics.
- ProductBrief identity rows store workspace, Workflow, product, current version, confirmed version,
  state, optimistic version, retention deadline, and timestamps.
- ProductBrief versions store schema and category version, validated payload, payload hash, human
  confirmation requirement, unresolved count, source kind, prompt version, requested/actual model,
  raw response reference, actor, and timestamps.
- ProductBrief field and evidence rows store field path, value, decimal confidence, source kind,
  conflict state, review requirement, sensitive-claim flag, source Asset Version, evidence kind and
  reference, optional region metadata, and excerpt hash.
- Brand Profile identity rows store workspace, brand, profile key, state, current version, optimistic
  version, stale timestamp, and timestamps.
- Brand Profile versions and member rows store immutable payloads, content hashes, exact Asset Version
  references, roles, and Rights Record references used at publication.
- Embedding Records are unique by Asset Version and fixed embedding-spec hash. They record Rights
  Record used for audit, vector kind, model family/ID/revision, configuration version, dimension,
  input hash, collection, deterministic Milvus primary key, execution state, lease, attempts, retry,
  provider request ID, optimistic version, and timestamps.
- Search documents provide MySQL FULLTEXT columns for title, labels, OCR summary, confirmed
  ProductBrief summary, and approved notes. The MySQL CJK ngram parser is used and tested for Chinese
  and mixed-language queries.
- Retrieval Policy versions store normalized immutable configuration and a content hash.
- Retrieval Runs and Retrieval Results record structured query hash, policy version, timings,
  candidate counts, score components, selected citations, and expiry. They must not store long-lived
  raw task prompts.
- Collection Registry rows record logical and physical collection, model family, pinned revision,
  vector kind, dimension, schema/index specification version, lifecycle state, active/read/write
  flags, snapshot watermark, backfill cursor, validation summary, and timestamps.
- Historical Asset, Asset Version, Rights Record, ProductBrief, Brand Profile, and Embedding references
  use `RESTRICT`, not cascade delete. Mutable head pointers are added after version tables to avoid
  migration-order cycles.
- Canonical fixed-length hashes are used for large logical uniqueness constraints to remain within
  MySQL `utf8mb4` index limits.
- Query indexes cover workspace and state, current rights and validity, retention deadlines,
  operation readiness and lease expiry, Asset Version hashes, ProductBrief current version, Brand
  Profile publication, Embedding Record readiness, and retrieval-run expiry.
- Persistence error mapping distinguishes unique conflicts, foreign-key violations, invalid data, and
  optimistic-concurrency conflicts rather than mapping every integrity error to concurrency.

### Object Storage and Upload Contract

- Local MinIO and production OSS satisfy one Object Storage interface.
- Object storage is divided into quarantine, task, foundation, and provider-result location classes.
  Production may map these to separate buckets; callers use logical locations rather than bucket names.
- Creating an Upload Session returns the session ID, reserved Asset ID, object upload URL, required
  headers, method, maximum bytes, expected checksum algorithm, and expiry. Storage credentials are
  never returned.
- Presigned uploads permit one object key, one method, bounded expiry, exact or bounded content
  length where supported, and expected content type. Server-side encryption headers are required in
  production.
- Finalize uses three phases: claim a finalize lease in MySQL, perform storage verification outside the
  transaction, then commit the Asset, Asset Version, object fact, finalized session, and validation
  Outbox event using the lease token.
- Finalize performs HEAD verification and streams the object through checksum and decode validation
  when storage metadata cannot prove the required checksum. Client-supplied ETag is never treated as
  a portable SHA-256 value.
- Finalize uses an idempotency key and request hash. Reuse with different parameters returns the
  existing public idempotency-conflict error.
- `asset_versions.upload_session_id` uniqueness is the database-level guarantee that one session
  creates at most one version.
- Temporary reads use short-lived controlled references generated only after current rights and
  workspace checks. They are never accepted as input identifiers by MCP or public HTTP mutation
  operations.
- Promotion from quarantine to final storage is an idempotent server-side copy followed by verified
  destination HEAD and best-effort source deletion. MySQL state changes only after destination
  verification.
- Asset-kind validation policies differ. Images use the full image pipeline; LoRA accepts only
  configured safe tensor formats and does not deserialize model data; Prompt templates and model
  configurations use strict text/JSON schema and size limits. LoRA, Prompt, and model configuration
  assets are not embedded or invoked in Phase 2.

### Image Validation and Rights Contract

- Mandatory local validation checks bytes, declared and detected MIME, extension consistency, file
  magic, complete image decode, width and height not greater than 1280, total bytes not greater than
  10 MB, total decoded pixels, animation frame limit, unsupported formats, ICC/EXIF size limits, and
  decompression-bomb conditions.
- Supported Phase 2 image formats are an explicit raster allowlist. SVG, PSD, archive, document,
  video, and executable formats are rejected by the image policy.
- Malware scanning uses a ClamAV Adapter that can scan streamed bytes without exposing arbitrary
  filesystem paths. Scanner timeout or unavailability never degrades to a clean verdict.
- Content safety uses a provider Adapter and records provider, actual model or policy version,
  request ID, verdict, categories, and evidence reference. A safety rejection cannot be bypassed by
  changing providers.
- Provenance extraction records verifiable EXIF, C2PA, content-credential, and source metadata when
  present. It reports `VERIFIED`, `UNVERIFIED`, `CONFLICTING`, or `NOT_PRESENT`; it never reports
  authenticity solely because no generated-content marker was found.
- Validation error classes distinguish terminal input rejection from transient scanner, storage, or
  provider failure. Only transient failures consume retry attempts.
- Registering or replacing rights requires explicit owner, source, use set, provider set, derivative
  permission, validity window, and evidence reference.
- Current rights are valid only when the selected record grants use, `valid_from <= now`, and
  `valid_until IS NULL OR now < valid_until`, with purpose, provider, and derivative requirements all
  satisfied.
- Every use decision accepts workspace, Asset Version, purpose, provider, derivative requirement, and
  decision time. It returns an authorization result containing the exact Rights Record version and
  reason code.

### Product Understanding Contract

- Vision analysis accepts only internal image Asset Version references that are currently usable for
  the configured Vision provider.
- Provider requests use a versioned prompt, a strict structured output schema, deterministic decoding
  settings where supported, and bounded image count and response size.
- Raw provider requests and responses are stored only as encrypted object references with the
  appropriate Task Asset or Foundation Asset retention. MySQL stores provenance and normalized output.
- ProductBrief common fields include identity, category, brand, product type, package or part form,
  material, colors, visible text summary, visual features, usage context, prohibited assumptions,
  sensitive claims, and source conflicts.
- Beauty extensions cover package type, cosmetic form, finish, texture, shade evidence, ingredient
  claim evidence, skin or hair claim flags, medical-like claim flags, and packaging compliance notes.
- Automotive-accessory extensions cover part type, vehicle placement, compatibility evidence,
  material, finish, dimensions evidence, installation evidence, safety-critical claim flags, and
  visible certification marks.
- Category extension payloads are validated against versioned schemas selected by category code.
- Confidence thresholds and sensitive-field rules are configuration under a ProductBrief policy
  version. Mandatory low-confidence, conflicting, or sensitive fields force
  `AWAITING_CONFIRMATION`.
- Human revision creates a new immutable ProductBrief version under optimistic concurrency. It records
  actor, reason, changed fields, and evidence references.
- Confirmation references a specific ProductBrief version and is append-only. It completes the
  existing ProductBrief human wait and emits `workflow.resume.requested`.
- A later analysis or edit archives but never mutates the previously confirmed version.

### Brand Profile Contract

- Brand Profile drafts include structured rules, approved colors, required logos or marks, prohibited
  elements, tone, copy constraints, and selected Foundation Assets.
- Draft validation resolves every selected Asset Version and current Rights Record in MySQL and
  verifies workspace, Foundation retention class, use, provider, derivative permission, and expiry.
- Publication creates an immutable version with a content hash and exact Asset Version and Rights
  Record references.
- Reading a published version returns its historical content but separately reports whether each
  source asset remains currently usable. Retrieval cannot use an invalid source even if it appears in
  a historical version.
- Rights replacement, expiry, revocation, or Asset deletion emits an event that marks affected current
  Brand Profiles `NEEDS_REPUBLISH` and requests index repair where appropriate.

### Durable Processing and Events

- Event envelopes continue to include event ID, type, schema version, aggregate type, aggregate ID,
  aggregate version, occurred time, trace ID, and payload.
- Phase 2 event families include upload finalized, validation requested/completed/failed, rights
  changed/expired, ProductBrief requested/awaiting-confirmation/confirmed, Brand Profile published,
  index requested/completed/delete-requested, asset delete requested/completed, collection rebuild
  requested/progressed/completed, and reconciliation requested.
- The Scheduler publishes events to workflow, asset, index, and maintenance queues by event type.
  The initial deployment may run one Worker process subscribed to all queues, while queue separation
  permits independent scaling without changing event contracts.
- Each event handler claims the Inbox message, claims or creates the logical Durable Operation,
  performs short transactional state changes, executes external work outside the transaction, and
  completes through a short transaction.
- Network calls, object streams, image decode, provider calls, Milvus operations, and rebuild batches
  never hold a MySQL transaction or row lock.
- Retry classification is explicit: throttling, timeout, temporary provider unavailability, storage
  unavailability, Milvus unavailability, and worker interruption are retryable; invalid media,
  denied rights, unsupported format, schema-invalid provider output after bounded repair, and policy
  rejection are terminal.
- Backoff is exponential with jitter, provider-specific retry-after support, bounded attempts, and
  maximum elapsed time. Idempotency keys remain stable across retries.
- Recovery scans expired operation leases, ready retries, stale events, and partially completed
  external operations. Recovery emits missing events only when no equivalent unpublished event exists.
- Provider calls record an attempt before submission. When a timeout leaves an unknown external
  outcome, the operation enters `RECONCILING` instead of blindly resubmitting when provider request
  lookup is available.
- DLQ supports query, replay, replay actor/reason, `replayed_at`, and repeated-failure history.

### Provider Adapter Contract

- Object Storage, Malware Scanner, Content Safety, Vision Analyzer, Embedding Provider, Reranker, and
  Vector Index interfaces use typed request and result contracts from the shared contracts package.
- Provider Adapters return normalized error categories, retryability, provider request ID, actual
  model or policy version, latency, and usage metadata.
- Adapters never receive unrestricted caller URLs. They receive internal object references and obtain
  a bounded temporary read reference from the Object Storage module.
- Alibaba Cloud configuration includes endpoint region, credential secret reference, model ID,
  optional snapshot or version, timeout, concurrency limit, retry budget, and data-transfer policy.
- The Vision Adapter validates structured output independently of provider success status.
- The Embedding Adapter verifies returned vector count, finite numeric values, and dimension against
  the configured collection contract. A mismatch is terminal for that collection and opens an alert.
- The optional Reranker Adapter can only reorder the supplied eligible candidate IDs and cannot add a
  new candidate.
- Deterministic Fixture Adapters cover success, rejection, timeout, throttling, malformed response,
  dimension mismatch, and unknown-outcome behavior without real credentials.

### Incremental Indexing and Milvus Contract

- Two initial vector kinds are `IMAGE` and `PRODUCT_FUSED`. Controlled text is built from confirmed
  ProductBrief fields, approved labels, and approved notes; raw OCR or free-form prompt text is not
  automatically embedded.
- The embedding input hash covers Asset Version byte hash, vector kind, normalized controlled text,
  model configuration version, and preprocessing version.
- The logical Milvus primary key is deterministic from the Embedding Record identity. Retries use
  upsert semantics.
- Collection schema includes Embedding Record ID, Asset Version ID, workspace ID, Rights Record
  version used for audit, category, brand, asset role, vector kind, model configuration version,
  indexed time, and vector. Dynamic fields are disabled.
- Physical collection identity includes model family, pinned revision, dimension, vector kind, and
  schema version. Collection names are generated and validated by the Indexing module, not callers.
- The initial ANN policy uses cosine similarity and a versioned HNSW specification. Exact FLAT search
  over the evaluation corpus is the baseline for measuring ANN recall; index parameters are not
  unversioned constants in route code.
- Indexing starts from an Outbox event written with the MySQL eligibility state. The worker rechecks
  eligibility before embedding and again before committing `INDEXED`.
- If rights become invalid after vector upsert but before MySQL completion, the operation records the
  stale write and schedules vector deletion. The Asset never becomes retrievable because final rights
  verification remains authoritative.
- Incremental update never requires a full collection rebuild. Only changed Asset Versions or
  controlled text inputs receive new Embedding Records.
- Collection rebuild records a MySQL snapshot watermark, reads eligible records in ordered batches,
  checkpoints its cursor, replays changes after the watermark, rechecks current rights, validates
  counts/primary keys/sampled queries/evaluation metrics, atomically switches the active collection in
  MySQL, and retires the old collection after a delay.
- Rebuild never clears or mutates the active collection in place.

### Hybrid Retrieval Contract

- A Retrieval Query contains workspace, requesting actor or Agent identity, product or ProductBrief
  reference, category, brand, intended use, intended provider, derivative requirement, desired asset
  roles, vector kinds, controlled query text, optional query image Asset Version, explicit reference
  IDs, limits, and Retrieval Policy version.
- The Retrieval module first creates an eligible Asset Version set in MySQL using current Asset state,
  current Rights Record, validity time, allowed use, allowed provider, derivative permission,
  workspace, and requested filters.
- Dense recall searches only the eligible set or an equivalently restrictive server-generated filter.
  Search is chunked when a vector-store expression limit would otherwise weaken the filter.
- Lexical recall uses MySQL FULLTEXT over approved search documents and intersects results with the
  same eligible set.
- Published Brand Profile assets and explicit user references are separate controlled candidate
  channels. They still pass the same current rights decision.
- Fusion uses versioned reciprocal-rank fusion followed by bounded business-score adjustments.
  Raw cosine and FULLTEXT scores are never added directly.
- Optional reranking receives only the top eligible candidates and returns an ordering over those
  candidate IDs.
- Deduplication removes repeated Asset Versions, identical hashes, and policy-defined similarity
  groups while preserving required brand assets.
- Immediately before return, one MySQL query re-resolves every selected Asset Version and current
  Rights Record. Any changed, expired, revoked, blocked, or deleted candidate is removed. Replacement
  candidates pass the same check.
- Issuing a temporary reference performs one additional current-rights check to close the
  search-to-read time-of-check/time-of-use window.
- Retrieval returns an explicit degraded result when Milvus or reranking is unavailable. It never
  labels lexical-only or fixed-brand fallback as a complete hybrid result.
- A Retrieval Citation contains Asset ID, Asset Version ID, Rights Record ID and version, Brand
  Profile version when relevant, Retrieval Policy version, candidate channels, score breakdown,
  rank, use reason, current authorization decision time, and a controlled preview-reference token.

### HTTP Contract

- All Phase 2 mutation endpoints require `X-Workspace-Id`, `X-Actor-Id`, and `Idempotency-Key`.
  Versioned mutations also require an expected version in the body.
- Read endpoints require `X-Workspace-Id` and never reveal whether a cross-workspace identifier exists.
- Product endpoints create, read, list, and update product and SKU metadata with optimistic versions.
- `POST /api/v1/upload-sessions` creates a constrained upload session and returns the presigned PUT
  contract.
- `POST /api/v1/upload-sessions/{id}:finalize` returns `202` with the quarantined Asset, Asset Version,
  and validation operation.
- Upload Session read and abort endpoints expose current state without object credentials.
- Asset endpoints read Asset and Asset Version state, register or replace Rights Records, list rights
  history, request deletion, and request a controlled preview reference.
- ProductBrief endpoints request analysis, read versions and evidence, revise a version, and confirm a
  version.
- Brand Profile endpoints create or update a draft, validate it, publish a version, and read published
  versions.
- Retrieval endpoints execute a structured query and read a retained retrieval run for debugging or
  evaluation.
- Collection administration endpoints request rebuild, inspect progress, validate a candidate
  collection, and activate an accepted collection. They require an administrator actor policy.
- Accepted asynchronous mutations return `202` with current resource and operation identifiers.
  Synchronous idempotent metadata mutations return `200` or `201`.
- Error responses reuse the existing stable envelope and add domain codes for rights denied, upload
  expired, object mismatch, asset blocked, validation rejected, ProductBrief confirmation required,
  index unavailable, retrieval degraded, provider policy denied, duplicate key, invalid reference,
  and unsupported asset kind.
- Every route explicitly documents stable error responses. Request-validation errors are normalized
  into the same public envelope rather than exposing FastAPI's default body.
- OpenAPI is regenerated and drift-checked in CI. The web client consumes only the versioned public
  contract.

### MCP Contract

- `catalog.get_product.v1` returns one workspace-scoped product and its SKUs.
- `catalog.get_product_brief.v1` returns the requested or current confirmed ProductBrief version with
  evidence summaries and confirmation status.
- `brand.get_profile.v1` returns a published Brand Profile version and current usability flags for its
  referenced assets.
- `assets.search.v1` accepts the structured Retrieval Query subset permitted to the Agent and returns
  Retrieval Citations, degraded state, and policy version.
- `assets.get_temporary_reference.v1` exchanges an authorized Asset Version or Retrieval Citation for
  a 30–60 second opaque controlled read reference.
- Workspace, actor, provider, purpose, and scopes come from a server-validated short-lived identity
  context, never model-supplied tool arguments.
- MCP tool schemas reject additional properties and enforce length, enum, array, and result-size
  limits. Returned asset metadata is explicitly untrusted business data.
- MCP calls the Product Catalog, Product Understanding, Brand Profile, and Retrieval application
  interfaces. It does not directly call SQL, MinIO, or Milvus.
- MCP does not expose upload, rights mutation, deletion, arbitrary storage access, provider credential
  access, raw SQL, arbitrary filter expressions, caller-selected model IDs, or unrestricted network
  fetch.
- Tool errors use normalized domain categories and indicate retryability. Authorization denials are
  non-retryable unless the caller obtains a new approved Rights Record.

### Web Workbench Contract

- The existing Next.js application becomes an authenticated-context workbench shell with product,
  assets, ProductBrief, Brand Profile, retrieval, and operation-status views. Authentication remains
  an Adapter seam; Phase 2 uses the current workspace and actor context without claiming production
  identity is complete.
- The product workspace supports manual product and SKU creation for Phase 2, image and Foundation
  Asset upload, upload progress, finalize state, validation evidence, current rights, and index state.
- Upload uses the presigned PUT contract directly from the browser. Image bytes do not transit through
  Next.js or the Control API.
- Rights forms expose owner, source, license, uses, providers, derivative permission, validity, and
  evidence reference. Empty permission sets are visibly denied rather than silently defaulted.
- ProductBrief review renders common and category fields with confidence, evidence, conflicts,
  sensitive-claim warnings, version history, stale-version conflict handling, and explicit confirmation.
- Brand Profile management supports draft rules, Foundation Asset selection, rights validation
  failures, publication, immutable version history, and `NEEDS_REPUBLISH` state.
- The retrieval explorer sends a structured Retrieval Query and displays candidate channels, RRF and
  business score breakdowns, rerank state, final rights decision, degraded mode, and controlled
  previews from Retrieval Citations.
- Long-running operations are shown from persisted status and survive refresh. The browser never
  treats a local spinner as execution truth.
- Every command has loading, retryable failure, non-retryable policy rejection, version conflict,
  empty, and completed states. Version conflicts reload current server state before a user retries.
- The workbench is responsive and keyboard accessible, uses the existing visual system, and does not
  expose secrets, bucket names, object keys, provider credentials, raw model payloads, or unrestricted
  URLs.
- Frontend types are generated from or checked against the committed OpenAPI contract. Ad hoc duplicate
  request and response types are not maintained separately.

### Security and Governance

- Every mutation and read enforces workspace scope in the repository query, not only at route level.
- Object keys are generated by the server from workspace, retention class, Asset ID, Asset Version,
  and random nonce. Caller-provided paths are rejected.
- Filenames are metadata only and are sanitized, length-limited, and never used as object keys or
  filesystem paths.
- Image decoding runs with strict resource limits. Production deployment may isolate validation in a
  constrained worker pool.
- Provider dispatch checks Rights Record provider permission and deployment data-transfer policy
  immediately before issuing a temporary object reference.
- Logs, traces, metrics, events, and errors contain IDs and classifications, not bytes, secrets, full
  prompts, full OCR, or full provider payloads.
- Secret values come from configured secret files or a production secret manager and never from
  committed configuration.
- Public-demo workspaces use separate buckets or prefixes, provider configurations, rate limits, and
  Retrieval Policies.
- No Fashion-AI code or unlicensed asset source is copied. Evaluation and demo assets carry explicit
  Rights Records.
- Request headers remain a Phase 2 identity context seam, not a claim that authentication is complete.
  Production authentication and authorization may replace the Adapter without changing application
  interfaces.

### Observability and Operations

- Spans cover upload-session creation, finalize verification, promotion, validation stages, rights
  decision, Vision request, ProductBrief persistence and confirmation, embedding request, Milvus
  upsert/delete/search, lexical search, fusion, reranking, final authorization, deletion, retention,
  reconciliation, and rebuild batches.
- Metrics include open and expired upload sessions, quarantine age, validation verdicts, rights
  denials and expiries, provider latency/error/rate-limit counts, operation lease age, retries and DLQ,
  ProductBrief confirmation rate, index lag, stale vector count, retrieval latency, candidate counts,
  degraded retrieval count, rebuild progress, and unauthorized recall.
- Structured errors use stable classes and include operation ID, target ID/version, event ID, trace ID,
  provider request ID where available, and retry classification.
- Readiness for API, Worker, Scheduler, and MCP includes only dependencies required for that process.
  A temporarily unavailable optional reranker degrades retrieval and does not mark the entire control
  plane unavailable.
- Runbooks cover stuck quarantine, scanner outage, rights-expiry backlog, provider throttling, index
  lag, Milvus loss, collection rebuild, stale vectors, deletion backlog, and DLQ replay.

### Configuration and Compatibility

- Configuration adds logical bucket or prefix mappings, upload expiry, image and non-image limits,
  validation versions, ClamAV endpoint, provider endpoint and model configurations, operation retry
  policies, collection registry defaults, Retrieval Policy defaults, controlled-reference expiry,
  scanner intervals, and evaluation thresholds.
- Compose adds bucket initialization, ClamAV, a deterministic Provider test Adapter where required,
  and all Worker/MCP dependency configuration.
- Existing Phase 1 Workflow APIs and fixture runtime remain behaviorally compatible.
- The metadata endpoint reports Phase 2 only after migrations, APIs, workers, MCP, retrieval, and
  acceptance gates are complete.
- Database migrations are forward-only for normal deployment, include a tested downgrade where data
  loss is not implied, and pass an empty-database upgrade plus existing-Phase-1 upgrade path.
- Production table growth, FULLTEXT indexing, and collection rebuild operations are designed for
  online or controlled maintenance. Destructive schema changes are not introduced into this phase.

## Testing Decisions

- Tests verify behavior through the five confirmed seams: HTTP, Durable Worker/Event, real
  MySQL/MinIO/Milvus retrieval, MCP, and Provider Adapter contracts.
- Tests do not call private helpers, assert internal call order, or query implementation tables when a
  public seam can prove the behavior. Direct schema inspection is reserved for database contracts such
  as constraints, indexes, and `DATETIME(6)`.
- Existing Phase 1 HTTP idempotency, MySQL integration, Outbox concurrency, Inbox lease, retry,
  recovery, and MCP health tests are the prior art for Phase 2.
- Domain tests cover allowed and rejected Asset transitions, Rights Record replacement and expiry,
  retention calculation, current-usability decisions, ProductBrief confirmation policy, Brand Profile
  publication, Embedding Record transitions, and Durable Operation lease/retry invariants.
- HTTP integration tests use real MySQL and MinIO to prove constrained upload, finalize idempotency,
  object mismatch rejection, quarantine isolation, workspace isolation, Rights Record versioning,
  ProductBrief optimistic concurrency, confirmation, Brand Profile publication, deletion request, and
  stable errors.
- Upload validation tests use known literal fixtures for valid JPEG/PNG/WebP, MIME mismatch, truncated
  data, oversized bytes, oversized dimensions, decompression bomb, excessive animation frames,
  malformed metadata, antivirus rejection, content-safety rejection, safe LoRA registration, unsafe
  model formats, and invalid Prompt/model configuration schemas.
- Provider contract tests run every Adapter against a common behavior suite covering success,
  normalized errors, retry-after, timeout, malformed response, request ID propagation, actual model
  recording, and secret redaction.
- Durable Worker/Event integration tests prove duplicate delivery, two concurrent consumers, worker
  death after external success but before commit, lease recovery, retry readiness at exact
  microseconds, DLQ exhaustion and replay, unknown-event handling, and idempotent external convergence.
- Indexing integration tests use real MySQL, MinIO, and Milvus plus a deterministic Embedding Adapter
  to prove incremental upsert, no duplicate logical vectors, input-hash change, rights change,
  deletion, stale-write repair, and collection isolation by model/version/kind/dimension.
- Retrieval integration tests use a fixed literal corpus and vectors to prove hard eligibility,
  dense recall, CJK/mixed-language FULLTEXT recall, RRF fusion, explicit references, Brand Profile
  candidates, deduplication, optional reranking, final rights recheck, degraded modes, and Retrieval
  Citation completeness.
- A dedicated race test revokes or expires rights between dense recall and final return and asserts
  that the Asset Version is absent.
- MCP contract tests enumerate tool schemas, call every tool through the MCP transport, verify
  workspace and rights enforcement, and reject arbitrary URLs, keys, paths, SQL, model IDs, secrets,
  and cross-workspace identifiers.
- Playwright covers manual product creation, direct upload, validation status, Rights Record
  registration, ProductBrief review and confirmation, Brand Profile publication, retrieval
  explanation, controlled preview, refresh recovery, version conflict, policy rejection, and degraded
  retrieval. Provider and safety calls use deterministic Adapters.
- Retention tests freeze time at boundaries and prove Task Asset expiry exactly at the Workflow
  deadline, Foundation Asset deletion or rights expiry, tombstone-first behavior, partial external
  cleanup recovery, and no resurrection during rebuild.
- Rebuild tests delete or replace a Milvus collection, rebuild from MySQL and object storage, interrupt
  between batches, resume from checkpoint, replay changes after the snapshot watermark, validate
  counts and sample queries, activate the candidate, and prove invalid assets are never restored.
- Retrieval evaluation freezes query, category, candidate-universe version, 0–3 relevance judgments,
  rights snapshot, purpose/provider, and split. Reports include K=5/10/20 macro, per-category, and
  per-vector-kind Recall@K, Precision@K, MRR, nDCG, P50, P95, unauthorized recall, unauthorized return
  count, and queries with unauthorized results.
- Evaluation thresholds live in a versioned dataset manifest. Validation reports bootstrap 95%
  confidence intervals; hidden release data is not used for daily tuning.
- Unauthorized recall, unauthorized return count, and queries with unauthorized results must all
  equal zero. Any nonzero value fails CI or release acceptance.
- Fault-injection tests stop or make unavailable MinIO, Milvus, RabbitMQ, a Provider Adapter, ClamAV,
  and a Worker at defined boundaries, then prove eventual convergence and absence of duplicate logical
  work.
- Static gates include Ruff format/check, Python type checking, OpenAPI drift, dependency audit,
  secret scan, container build, migration drift, license/SBOM generation, and retrieval-evaluation
  regression.
- The full Phase 1 suite remains green throughout every Ticket and after Phase 2 integration.

## Out of Scope

- Image generation, image editing, candidate selection, multi-provider generation routing, and final
  generated-image review.
- Creative Plan generation or approval changes beyond preserving existing Phase 1 compatibility.
- Video upload, analysis, generation, retrieval, or export.
- PSD parsing or intelligent layer separation.
- LoRA training or inference. Phase 2 registers and governs LoRA Foundation Assets only.
- Complete ERP synchronization, Amazon listing synchronization, or Amazon image export.
- Historical bestseller scraping or any unlicensed material acquisition.
- Production Kubernetes, ACK, RDS, Tair, OSS lifecycle policy deployment, Milvus Distributed, Helm,
  Terraform, multi-zone failover, or the final 99.95% control-plane SLO exercise.
- Provider automatic failover and multi-model degradation routing. Phase 2 provides vendor-neutral
  interfaces and normalized failures; capability routing belongs to Phase 4.
- Experience memory learned automatically from user feedback.

## Further Notes

- Phase 2 is a prerequisite for later Agent planning and generation. It intentionally builds the
  trustworthy multimodal context layer before a real image-generation Provider is introduced.
- MySQL authorization checks are mandatory both before candidate recall and immediately before return.
  Milvus scalar filters are performance hints, never a rights authority.
- An approved ProductBrief and a published Brand Profile are versioned facts for later Agent context;
  mutable drafts are not.
- Provider aliases may change. Every stored result records the configured model ID, actual returned
  model or policy version, prompt/configuration version, and request ID.
- Collection dimension is verified from configuration and provider output. It is not inferred from a
  model-name string.
- The initial workload is hundreds of tasks per day with at most five SKUs, five historical images,
  five to ten requested outputs, and 1280x1280 input images. The design remains scalable, but defaults
  and tests optimize for a single-operator private deployment rather than premature fleet complexity.
