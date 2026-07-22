# Domain Docs

This repository uses a single-context documentation layout.

## Before exploring

Read:

- `CONTEXT.md` at the repository root when it exists.
- Relevant documents indexed by `docs/README.md`.
- Relevant ADRs under `docs/07-decisions/`.

The repository currently uses `docs/07-decisions/` for ADRs. Do not create a duplicate `docs/adr/` tree unless the project explicitly migrates its ADR layout.

## Existing domain documentation

- Product and workflow definitions: `docs/00-product/`
- Architecture and Agent Runtime: `docs/01-architecture/`
- Data and persistence: `docs/02-data/`
- AI, tools, prompts, and evaluation: `docs/03-ai/`
- Engineering and integration contracts: `docs/04-engineering/`
- Deployment and operations: `docs/05-deployment/`
- Roadmap and acceptance criteria: `docs/06-roadmap/`
- Architecture decisions: `docs/07-decisions/`
- Research and source boundaries: `docs/08-research/`

## Vocabulary

Use terms as defined in `CONTEXT.md` and the indexed project documentation. If a needed concept is not defined, record the gap for `/domain-modeling` rather than silently introducing a competing term.

## ADR conflicts

If a proposed change contradicts an existing ADR, identify the conflict explicitly and recommend reopening or superseding the ADR.
