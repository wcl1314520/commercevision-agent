# 12 — Product Catalog and Asset MCP

**What to build:** Expose five versioned MCP tools for product, ProductBrief, Brand Profile, asset
search, and controlled temporary references. MCP is an inbound Adapter over application interfaces
and Tool Gateway policy; it must never become direct database, object-storage, vector-store, or
network access.

**Blocked by:** 11 — Rights-first hybrid retrieval and Retrieval Explorer.

**Status:** complete

- [x] `catalog.get_product.v1` returns a workspace-scoped Product and SKU snapshot.
- [x] `catalog.get_product_brief.v1` returns an exact or current confirmed ProductBrief with evidence summaries.
- [x] `brand.get_profile.v1` returns an exact published Brand Profile and current member usability.
- [x] `assets.search.v1` returns policy version, degradation, and complete Retrieval Citations.
- [x] `assets.get_temporary_reference.v1` returns an opaque, short-lived authorized reference.
- [x] Workspace, actor, scopes, purpose, provider, and budget come from a server-validated identity context, not model arguments.
- [x] Tool schemas reject additional properties and enforce enum, string, array, top-k, argument-byte, and output-byte limits.
- [x] Tool Gateway validates input and output schemas and includes tool and policy versions in idempotency.
- [x] Arbitrary URL, SQL, bucket, object key, file path, model ID, Secret reference, and cross-workspace identifier attempts fail with stable errors.
- [x] MCP readiness reflects required dependencies without exposing credentials.
- [x] Contract tests enumerate schemas and execute every tool through the configured MCP transport.
