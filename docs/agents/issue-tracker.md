# Issue tracker: Local Markdown

Issues and specs for this repo live as Markdown files in `.scratch/`.

## Conventions

- One feature per directory: `.scratch/<feature-slug>/`
- The spec is `.scratch/<feature-slug>/spec.md`
- Implementation issues are one file per ticket at `.scratch/<feature-slug>/issues/<NN>-<slug>.md`
- Ticket numbers start from `01`.
- Triage state is recorded with a `Status:` line near the top of each issue file.
- Comments and conversation history append to the bottom under a `## Comments` heading.

## When a skill says "publish to the issue tracker"

Create a new file under `.scratch/<feature-slug>/`, creating the directory if necessary.

## When a skill says "fetch the relevant ticket"

Read the referenced Markdown file directly.

## Blocking

Use a `Blocked by: NN, NN` line near the top of a ticket when it depends on other tickets.

A ticket is unblocked when every referenced blocking ticket is resolved.
