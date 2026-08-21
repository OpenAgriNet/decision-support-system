# Per-task plans

Working specs for in-flight changes. Committed so they can be reviewed and
shared, then **deleted when the change lands**.

## Lifecycle

1. A plan lands here as `NNNN-short-slug.md`, on the branch that implements it.
2. Reviewers comment on it alongside the diff.
3. When the change merges, the plan is **deleted** and whatever is durable moves
   into the centralised docs — `docs/DSS_ARCHITECTURE.md` for design that holds,
   `docs/ADR/` for decisions with trade-offs.

## Why they get deleted

Centralised documentation is the single source of truth. A plan that survives
past its change becomes a second, staler account of the same design — and the
reader has no way to tell which one is current. These files describe *intent at a
point in time*; once the code exists, the code and the centralised docs are the
truth.

So the plan is scaffolding for review, not an archive. Git history keeps it if
anyone needs to look back.

## What belongs here

- The problem, and why it is worth solving now
- Decisions taken, with their reasoning — especially options rejected and why
- Scope boundaries: what this change deliberately leaves out
- How to verify the change works

## What does not

- Design that outlives the change → `docs/DSS_ARCHITECTURE.md`
- Decisions with real trade-offs → a new `docs/ADR/NNNN-*.md`
- Anything already true of the code → the code says it better
