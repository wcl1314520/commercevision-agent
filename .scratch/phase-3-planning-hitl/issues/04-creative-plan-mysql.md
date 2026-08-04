# 04 — Creative Plan MySQL authority and optimistic head

**What to build:** Persist Creative Plan identities, immutable versions, provenance, and current-version
heads in MySQL with atomic create/revise behavior and exact retention.

**Blocked by:** 01 — Creative Plan version contract.

**Status:** ready-for-agent

- [ ] Tenant-owned keys and indexes lead with binary-exact Workspace ID and prevent cross-workspace references.
- [ ] `(workspace_id, workflow_id, creative_plan_id, version_number)` and immutable IDs prevent duplicate logical versions.
- [ ] Current head advancement is optimistic and atomic; stale revisions fail without partial inserts.
- [ ] Immutable rows cannot be updated through repository/application Interfaces.
- [ ] Provenance hashes and bounded structured payloads round-trip without semantic drift.
- [ ] Task retention is clamped to the Workflow deadline and never extended by editing or approval.
- [ ] Empty upgrade, downgrade where non-destructive, re-upgrade, drift, and all UTC `DATETIME(6)` checks pass.
- [ ] Real MySQL tests cover duplicate delivery, concurrency, foreign workspace, missing provenance, and reconstruction order.
