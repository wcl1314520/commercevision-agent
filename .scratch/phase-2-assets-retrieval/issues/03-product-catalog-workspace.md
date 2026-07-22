# 03 — Product Catalog workspace

**What to build:** Deliver a workspace-scoped Product and SKU catalog through MySQL, versioned HTTP
contracts, and the Web workbench. The catalog is manually managed in Phase 2 but exposes stable source
and external identifier contracts for later ERP integration.

**Blocked by:** None — can start immediately.

**Status:** complete

- [x] Product and SKU aggregates enforce workspace ownership, external source identity, category, brand, attributes, expiry, and optimistic versions.
- [x] External identifiers are unique within workspace and source namespace.
- [x] Create, read, list, update, and SKU mutation HTTP operations use stable contracts and workspace filtering in repository queries.
- [x] Reusing an idempotency key with an identical request returns the same resource; a different request returns the public conflict error.
- [x] Cursor pagination is stable under concurrent inserts.
- [x] The Web workbench can create, inspect, update, and list products and SKUs with loading, empty, failure, and version-conflict states.
- [x] Frontend request and response types are checked against the committed OpenAPI contract.
- [x] Cross-workspace identifiers do not reveal resource existence.
- [x] Unit, MySQL HTTP integration, OpenAPI, frontend lint/typecheck/build, and responsive UI tests pass.
- [x] Product and SKU external identities share one transactional
  `(workspace_id, source_namespace, external_id)` registry; deletes release reservations atomically.
- [x] Catalog mutation idempotency is serialized in the MySQL transaction and replays the original
  response snapshot, including concurrent create, update, and delete retries.
- [x] SKU ownership is enforced by a composite MySQL foreign key over workspace and product identity.
- [x] The Web catalog proxy reads `CV_API_PROXY_URL` at request time and defaults to the Compose `api:8000`
  service without baking a loopback destination into the production build.

## Phase 2 expiry decision

Expired Product and SKU records remain readable and listable for audit and renewal. `expires_at` is
explicit metadata; rights and asset usability enforcement belong to the later rights and retrieval
Tickets, so the catalog does not silently hide expired records.
