# 03 — Product Catalog workspace

**What to build:** Deliver a workspace-scoped Product and SKU catalog through MySQL, versioned HTTP
contracts, and the Web workbench. The catalog is manually managed in Phase 2 but exposes stable source
and external identifier contracts for later ERP integration.

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] Product and SKU aggregates enforce workspace ownership, external source identity, category, brand, attributes, expiry, and optimistic versions.
- [ ] External identifiers are unique within workspace and source namespace.
- [ ] Create, read, list, update, and SKU mutation HTTP operations use stable contracts and workspace filtering in repository queries.
- [ ] Reusing an idempotency key with an identical request returns the same resource; a different request returns the public conflict error.
- [ ] Cursor pagination is stable under concurrent inserts.
- [ ] The Web workbench can create, inspect, update, and list products and SKUs with loading, empty, failure, and version-conflict states.
- [ ] Frontend request and response types are checked against the committed OpenAPI contract.
- [ ] Cross-workspace identifiers do not reveal resource existence.
- [ ] Unit, MySQL HTTP integration, OpenAPI, frontend lint/typecheck/build, and responsive UI tests pass.

