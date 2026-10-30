# 19. The calling sender's preferred providers

Date: 2026-10-14

## Status

Accepted

Amended by [ADR-0020](0020-provider-preferences-from-the-registry.md): the
caller now names itself in the request body (`senderId`, not an `X-Client-Id`
header) and the rankings live in the network registry, not a mounted file.

Amended by [ADR-0022](0022-next-provider-when-the-first-cannot-answer.md):
when the first provider's data cannot answer the ask, the planner switches to
the next ranked provider's data with `select_next`.

## Context

Several providers now serve the same capability in the same place — two
weather sources over one district — with different quality and coverage.
Until now the planner's model picked a candidate, so the provider behind an
answer could differ between two identical questions, and nobody could state a
preference. Adopters have vetted sources of their own and treat that choice
as part of what they promise their users. Only the chosen provider's answer
was recorded, so a ranking's order could never be judged afterwards.

## Decision

Each calling application gets a ranked provider list per capability (the
ask's subject category — `Weather`, `Market` — not a resolved `@type`), with
a default list underneath. The rules:

- **Exclusive.** A provider the ranking does not name is never called.
  Vetting is the point; ordering-only would never keep an unvetted provider
  out.
- **Fallback is per capability, not per sender.** A sender that ranked only
  `Weather` gets the default's `Market` list. An unknown or absent sender
  gets the default throughout — answered, never refused.
- **Applied once, after discovery.** Everything downstream — the no-match
  check, the planner's candidates, the catalog's Direct answers — reads an
  already-ranked set. Telling the model the order in its prompt would make
  the promise a tendency; ordering at each consumer would break at the first
  one that forgot.
- **One `select` consults every ranked provider** serving the chosen
  candidate's `@type`, concurrently, and answers from the best-ranked one
  that returned anything. "Returned anything" is the whole sufficiency test
  this release.
- **Unused answers are recorded** — ids and counts at INFO, bodies at DEBUG
  where the invocation adapter already logs them (ADR-0014) — so the order
  can be judged on real answers.

The rankings sit behind an async `ProviderPreferences` port; the merge rule
lives in `core/provider_preferences`, so no source of rankings can drift on
what "falls back to the default" means.

## Consequences

- Which provider answers is configuration: deterministic, per adopter,
  changed without a release.
- A provider outside a sender's ranking is never consulted, so a question
  only it could answer goes unanswered. Accepted; what was dropped is logged
  per ask.
- A thin first-choice answer beats a fuller one below it. The recorded
  unused answers are the evidence for revisiting that.
- A ranking of three costs three `/select` calls where one was made. They run
  concurrently and each is bounded by its timeout.
- The model's `resource_id` picks the kind of data, no longer the vendor.
- One ask is still answered by one provider; merging answers stays out of
  scope.
