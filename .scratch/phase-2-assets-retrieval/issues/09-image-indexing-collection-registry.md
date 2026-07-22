# 09 — Collection Registry and IMAGE incremental indexing

**What to build:** Create versioned Milvus Collection specifications and incrementally index authorized
image Asset Versions through an Embedding Provider and deterministic Milvus primary keys. MySQL owns
every index fact and can recover from duplicate delivery, provider failure, Milvus failure, and
Worker interruption.

**Blocked by:** 02 — Durable Operations and recovery control plane; 06 — Rights Records and current usability.

**Status:** ready-for-agent

- [ ] Embedding and Milvus runtime/admin interfaces use typed requests, normalized provider metadata, and normalized errors.
- [ ] Collection identity includes model family, pinned revision, dimension, vector kind, schema version, and index-spec version.
- [ ] Collection schema disables dynamic fields and stores only acceleration and audit scalars, never authorization truth.
- [ ] Embedding output count, finite values, and dimension are verified before Milvus upsert.
- [ ] IMAGE input hash includes bytes, preprocessing, model configuration, and vector kind.
- [ ] Embedding Record uniqueness and deterministic Milvus primary key make repeated requests idempotent.
- [ ] Eligibility is rechecked before provider submission and before MySQL commits indexed state.
- [ ] Rights invalidation after Milvus upsert schedules stale-vector deletion and never makes the asset retrievable.
- [ ] Index operations use leases, bounded retry, reconciliation, DLQ, and exact `DATETIME(6)` boundaries.
- [ ] Real MySQL, MinIO, and Milvus tests prove incremental upsert, duplicate delivery, dimension mismatch, provider timeout, Milvus outage, crash after upsert, and rights change.
- [ ] Index status is visible through HTTP and the Web asset view.

