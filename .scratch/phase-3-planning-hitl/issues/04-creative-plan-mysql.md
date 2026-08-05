# 04 — Creative Plan MySQL authority and optimistic head

**What to build:** Persist Creative Plan identities, immutable versions, provenance, and current-version
heads in MySQL with atomic create/revise behavior and exact retention.

**Blocked by:** 01 — Creative Plan version contract.

**Status:** complete

- [x] Tenant-owned keys and indexes lead with binary-exact Workspace ID and prevent cross-workspace references.
- [x] `(workspace_id, workflow_id, creative_plan_id, version_number)` and immutable IDs prevent duplicate logical versions.
- [x] Current head advancement is optimistic and atomic; stale revisions fail without partial inserts.
- [x] Immutable rows cannot be updated through repository/application Interfaces.
- [x] Provenance hashes and bounded structured payloads round-trip without semantic drift.
- [x] Task retention is clamped to the Workflow deadline and never extended by editing or approval.
- [x] Empty upgrade, downgrade where non-destructive, re-upgrade, drift, and all UTC `DATETIME(6)` checks pass.
- [x] Real MySQL tests cover duplicate delivery, concurrency, foreign workspace, missing provenance, and reconstruction order.

**Verification:** Creative Plan domain/application/MySQL/migration suite passed, including
real concurrent revisions and migration trigger enforcement. Full unit/contract, Ruff, touched-file
Mypy, baseline drift, license, dependency, Alembic drift, and historical migration gates passed.
Implementation commit `d213f5908645f3b2b14d1977af97562b62033705` is verified by exact
GitHub Actions run `30976515426`: Python, Web, Container builds, and Security/SBOM all passed.
