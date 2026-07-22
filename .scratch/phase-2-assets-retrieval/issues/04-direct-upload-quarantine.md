# 04 — Direct Upload Sessions and quarantine

**What to build:** Allow the browser and API clients to create constrained Upload Sessions, upload
directly to MinIO or OSS, and finalize one verified object into a quarantined Asset and immutable
Asset Version. Finalize must use a lease-based three-phase protocol and remain correct under retries
and Worker or API interruption.

**Blocked by:** 02 — Durable Operations and recovery control plane; 03 — Product Catalog workspace.

**Status:** ready-for-agent

- [ ] Upload Session is an independent aggregate with open, finalizing, finalized, expired, and aborted states.
- [ ] MinIO and OSS Adapters satisfy one typed object-storage interface for presign, stat, bounded read, conditional copy, conditional delete, and temporary read.
- [ ] Logical quarantine, task, foundation, and provider-result storage locations are configured without exposing credentials.
- [ ] Presigned PUT responses constrain object key, method, expiry, content metadata, checksum policy, and maximum bytes.
- [ ] Object keys are server-generated and filenames are metadata only.
- [ ] Finalize claims a MySQL lease, verifies object metadata and SHA-256 outside the transaction, then atomically persists Asset, Asset Version, object fact, operation, and Outbox event.
- [ ] ETag is treated as opaque and is never accepted as SHA-256.
- [ ] One Upload Session can produce at most one Asset Version, including concurrent or repeated finalize calls.
- [ ] The Web workbench uploads bytes directly to object storage and shows persisted session and quarantine state after refresh.
- [ ] Real MySQL and MinIO tests cover success, checksum mismatch, length mismatch, expiry, abort, duplicate finalize, concurrent finalize, storage outage, and copy-after-crash recovery.

