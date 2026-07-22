# 13 — Retention, deletion, and consistency reconciliation

**What to build:** Enforce Task Asset retention at the owning Workflow's 72-hour deadline and
Foundation Asset retention until administrator deletion or Rights Expiry. MySQL must tombstone use
first, then durable operations converge objects, vectors, search documents, checkpoints, temporary
references, and caches without resurrection.

**Blocked by:** 02 — Durable Operations and recovery control plane; 06 — Rights Records and current usability; 09 — Collection Registry and IMAGE incremental indexing.

**Status:** ready-for-agent

- [ ] Task Asset retention copies the Workflow expiry and is never extended by upload, retry, analysis, or reindex.
- [ ] Foundation Asset retention ends at administrator deletion or current Rights Record expiry, whichever occurs first.
- [ ] Rights expiry and deletion atomically mark MySQL unusable, increment deletion generation, record a tombstone, and emit cleanup work.
- [ ] Cleanup includes all Asset Versions, objects, vectors, search documents, temporary references, caches, task ProductBrief payloads, retrieval runs, and task-bearing checkpoints.
- [ ] Missing external objects or vectors are treated as converged success.
- [ ] Old delete events cannot delete a later Asset Version because target version and deletion generation are checked.
- [ ] Partial cleanup retries without restoring usability or duplicating logical work.
- [ ] Reconciliation detects orphaned quarantine objects, stale vectors, missing vectors, stuck operations, and incomplete deletion.
- [ ] Scheduler scans use bounded keyset batches, `SKIP LOCKED`, exact time boundaries, and independent scanner health.
- [ ] HTTP and Web operation views expose deletion and reconciliation progress without storage details.
- [ ] Time-frozen and fault-injection tests prove exact 72-hour expiry, Foundation expiry, administrator delete, storage/vector outage, Worker restart, and no resurrection.

