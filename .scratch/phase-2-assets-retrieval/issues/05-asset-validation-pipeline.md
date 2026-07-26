# 05 — Multi-kind asset validation pipeline

**What to build:** Process quarantined image and Foundation Asset versions through local validation,
ClamAV, content safety, and provenance evidence. Valid objects are promoted to controlled storage and
move to pending rights; rejected content remains unusable and converges to quarantine cleanup.

**Blocked by:** 04 — Direct Upload Sessions and quarantine.

**Status:** complete

- [x] Image validation enforces declared/detected MIME, magic bytes, complete decode, 10 MB, 1280x1280, pixel, frame, metadata, and decompression limits.
- [x] The image allowlist excludes SVG, PSD, archive, document, video, and executable formats.
- [x] ClamAV scanning distinguishes clean, infected, timeout, and unavailable outcomes without unsafe clean fallback.
- [x] The Alibaba content-safety Adapter and deterministic Adapter return normalized pass, review, block, and retryable failure results.
- [x] Provenance evidence reports verified, unverified, conflicting, or not present without unsupported authenticity claims.
- [x] LoRA registration accepts only configured safe tensor formats, never deserializes model data, and rejects pickle-based model formats.
- [x] Prompt templates and model configurations use strict size and schema validation.
- [x] Validation results are append-only by Asset Version and validator version.
- [x] Promotion performs idempotent copy, destination verification, and source cleanup before making the object eligible for rights processing.
- [x] The Web workbench displays validation stages, evidence, terminal rejection, and retryable failure without exposing raw provider payloads.
- [x] Provider contract tests and real MinIO/MySQL Worker tests cover all acceptance and failure paths, including Worker interruption.

**Implementation:** `77e5214` (`Implement multi-kind asset validation pipeline`)

**CI correction:** `dbc8161` (`Separate migration and runtime database identities`)

**CI:** GitHub Actions run `30225320445` passed Python, Web, container build,
Gitleaks, and SBOM gates.
