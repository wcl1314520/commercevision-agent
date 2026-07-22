# 05 — Multi-kind asset validation pipeline

**What to build:** Process quarantined image and Foundation Asset versions through local validation,
ClamAV, content safety, and provenance evidence. Valid objects are promoted to controlled storage and
move to pending rights; rejected content remains unusable and converges to quarantine cleanup.

**Blocked by:** 04 — Direct Upload Sessions and quarantine.

**Status:** ready-for-agent

- [ ] Image validation enforces declared/detected MIME, magic bytes, complete decode, 10 MB, 1280x1280, pixel, frame, metadata, and decompression limits.
- [ ] The image allowlist excludes SVG, PSD, archive, document, video, and executable formats.
- [ ] ClamAV scanning distinguishes clean, infected, timeout, and unavailable outcomes without unsafe clean fallback.
- [ ] The Alibaba content-safety Adapter and deterministic Adapter return normalized pass, review, block, and retryable failure results.
- [ ] Provenance evidence reports verified, unverified, conflicting, or not present without unsupported authenticity claims.
- [ ] LoRA registration accepts only configured safe tensor formats, never deserializes model data, and rejects pickle-based model formats.
- [ ] Prompt templates and model configurations use strict size and schema validation.
- [ ] Validation results are append-only by Asset Version and validator version.
- [ ] Promotion performs idempotent copy, destination verification, and source cleanup before making the object eligible for rights processing.
- [ ] The Web workbench displays validation stages, evidence, terminal rejection, and retryable failure without exposing raw provider payloads.
- [ ] Provider contract tests and real MinIO/MySQL Worker tests cover all acceptance and failure paths, including Worker interruption.

