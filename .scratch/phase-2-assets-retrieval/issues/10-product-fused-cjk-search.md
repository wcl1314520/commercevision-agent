# 10 — PRODUCT_FUSED indexing and CJK lexical documents

**What to build:** Add controlled ProductBrief-derived multimodal embeddings and MySQL FULLTEXT search
documents for Chinese and mixed-language commerce queries. Incremental updates must occur only when
the confirmed ProductBrief, approved labels, notes, preprocessing, or model configuration changes.

**Blocked by:** 07 — Vision ProductBrief and human confirmation; 09 — Collection Registry and IMAGE incremental indexing.

**Status:** ready-for-agent

- [ ] PRODUCT_FUSED text is built only from confirmed ProductBrief fields, approved labels, and approved notes.
- [ ] Raw OCR, raw prompts, and unconfirmed model output are excluded by default.
- [ ] Canonical normalization and input hashing produce stable idempotency across equivalent content.
- [ ] A changed confirmed ProductBrief creates a new Embedding Record while unchanged input does not reindex.
- [ ] Search documents contain controlled title, labels, OCR summary, ProductBrief summary, and notes with retention metadata.
- [ ] MySQL FULLTEXT uses a verified CJK ngram parser and supports Chinese, English, and mixed-language queries.
- [ ] Search documents and fused vectors become stale or deleted when current rights stop allowing use.
- [ ] Exact and ANN search behavior is tested against literal multilingual fixtures.
- [ ] MySQL schema drift, FULLTEXT query plans, incremental Worker behavior, and Phase 1 compatibility tests pass.

