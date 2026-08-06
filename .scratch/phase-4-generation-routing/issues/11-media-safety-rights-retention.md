# 11 — Media safety, Rights, and retention fences

**What to build:** Add mandatory input/output moderation, safety provenance, Rights/provider checks,
and retention/revocation convergence around every generated or edited media operation.

**Blocked by:** 05, 09.

**Status:** pending

- [ ] Input safety runs before dispatch and output safety before Candidate availability using exact versioned policies.
- [ ] Confirmed safety rejection is stable, auditable and cannot route to another Provider.
- [ ] Rights allowed-purpose/provider/derivation/expiry and Workflow retention are revalidated at both authority fences.
- [ ] Revocation or expiry stops use first and converges object/vector/checkpoint cleanup through existing tombstones/operations.
- [ ] Provider-unknown safety or retention semantics cannot weaken CommerceVision policy.
- [ ] Tests prove prompt injection, stale Rights, late results and moderation outages fail closed without retention extension.
