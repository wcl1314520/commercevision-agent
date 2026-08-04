# 09 — Collection Registry and IMAGE incremental indexing

**What to build:** Create versioned Milvus Collection specifications and incrementally index authorized
image Asset Versions through an Embedding Provider and deterministic Milvus primary keys. MySQL owns
every index fact and can recover from duplicate delivery, provider failure, Milvus failure, and
Worker interruption.

**Blocked by:** 02 — Durable Operations and recovery control plane; 06 — Rights Records and current usability.

**Status:** complete

- [x] Embedding and Milvus runtime/admin interfaces use typed requests, normalized provider metadata, and normalized errors.
- [x] Collection identity includes model family, pinned revision, dimension, vector kind, schema version, and index-spec version.
- [x] Collection schema disables dynamic fields and stores only acceleration and audit scalars, never authorization truth.
- [x] Embedding output count, finite values, and dimension are verified before Milvus upsert.
- [x] IMAGE input hash includes bytes, preprocessing, model configuration, and vector kind.
- [x] Embedding Record uniqueness and deterministic Milvus primary key make repeated requests idempotent.
- [x] Eligibility is rechecked before provider submission and before MySQL commits indexed state.
- [x] Rights invalidation after Milvus upsert schedules stale-vector deletion and never makes the asset retrievable.
- [x] Index operations use leases, bounded retry, reconciliation, DLQ, and exact `DATETIME(6)` boundaries.
- [x] Real MySQL, MinIO, and Milvus tests prove incremental upsert, duplicate delivery, dimension mismatch, provider timeout, Milvus outage, crash after upsert, and rights change.
- [x] Index status is visible through HTTP and the Web asset view.
