# 11 — Rights-first hybrid retrieval and Retrieval Explorer

**What to build:** Execute a structured Retrieval Query through MySQL hard filtering, IMAGE and
PRODUCT_FUSED dense recall, FULLTEXT, Brand Profile and explicit-reference channels, versioned RRF,
optional reranking, deduplication, and final current-rights revalidation. Return explainable Retrieval
Citations and controlled temporary references in the API and Web explorer.

**Blocked by:** 08 — Brand Profile publication; 09 — Collection Registry and IMAGE incremental indexing; 10 — PRODUCT_FUSED indexing and CJK lexical documents.

**Status:** ready-for-agent

- [ ] Retrieval Query validates workspace, product/brief, purpose, provider, derivative requirement, roles, vector kinds, query text/image, explicit references, limits, and policy version.
- [ ] MySQL generates the eligible Asset Version set before any candidate recall.
- [ ] Dense search cannot weaken the eligible set when Milvus expression limits require chunking.
- [ ] FULLTEXT, Brand Profile, and explicit-reference candidates intersect the same current authorization decision.
- [ ] Versioned reciprocal-rank fusion is used; raw cosine and lexical scores are not directly added.
- [ ] Optional reranking can only reorder supplied eligible candidates and cannot add IDs.
- [ ] Deduplication handles repeated versions and identical hashes while preserving required brand members.
- [ ] Selected and replacement candidates pass one final MySQL current-rights query before return.
- [ ] Temporary reference issuance performs another current-rights check and uses a 30–60 second opaque token.
- [ ] Retrieval Citation includes Asset/Version, Rights Record version, policy version, channels, score breakdown, rank, reason, and decision time.
- [ ] Milvus or reranker failure returns an explicit degraded mode without claiming complete hybrid retrieval.
- [ ] The Web Retrieval Explorer displays filters, channels, scores, degradation, citations, and controlled previews.
- [ ] Real infrastructure tests include a rights revocation race between candidate recall and final return and assert zero unauthorized results.

