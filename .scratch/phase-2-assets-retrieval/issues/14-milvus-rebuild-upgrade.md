# 14 — Milvus rebuild and Collection upgrade

**What to build:** Rebuild a candidate Milvus Collection from current MySQL and object-storage facts,
resume from durable checkpoints, replay changes after a snapshot watermark, validate safety and
quality, atomically switch the active Collection in MySQL, and retire the old Collection later.

**Blocked by:** 09 — Collection Registry and IMAGE incremental indexing; 11 — Rights-first hybrid retrieval and Retrieval Explorer; 13 — Retention, deletion, and consistency reconciliation.

**Status:** ready-for-agent

- [ ] Rebuild creates a non-active physical Collection from an immutable Collection specification.
- [ ] A MySQL snapshot watermark and ordered keyset cursor are persisted before and during backfill.
- [ ] Restart resumes from the durable cursor without duplicate logical vectors.
- [ ] Events after the snapshot watermark are replayed before validation.
- [ ] Current rights are rescanned before candidate activation so revoked, expired, blocked, deleting, or deleted assets are absent.
- [ ] Validation checks row count, primary-key set, sampled vector visibility, exact-versus-ANN recall, fixed retrieval metrics, and unauthorized results.
- [ ] Active Collection switching is one MySQL transaction over the Collection Registry and Retrieval Policy pointer.
- [ ] The old active Collection remains available during switch and retires only after a configured delay.
- [ ] Rebuild never clears or mutates the active Collection in place.
- [ ] Admin HTTP and Web views support request, progress, validation, activation, failure, and retirement.
- [ ] Real Milvus tests delete the current Collection, interrupt multiple backfill boundaries, resume, activate, and prove invalid assets are not restored.

