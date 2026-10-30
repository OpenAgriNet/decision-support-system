# 19. The calling sender's preferred providers

Date: 2026-10-14

## Status

Accepted

Amended by [ADR-0020](0020-provider-preferences-from-the-registry.md): the
caller now names itself in the request body (`senderId`, not an `X-Client-Id`
header) and the rankings live in the network registry, not a mounted file.

Amended 2026-10-30: when the first provider's data cannot answer the ask, the
planner switches to the next ranked provider's data with `select_next` (see
"Falling back when the first cannot answer").

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
  that returned anything.
- **The planner can fall back down the ranking.** When the data it was given
  cannot answer the ask, it calls `select_next`. See below.
- **Unused answers are recorded** — ids and counts at INFO, bodies at DEBUG
  where the invocation adapter already logs them (ADR-0014) — so the order
  can be judged on real answers.

The rankings sit behind an async `ProviderPreferences` port; the merge rule
lives in `core/provider_preferences`, so no source of rankings can drift on
what "falls back to the default" means.

### Falling back when the first cannot answer

Added 2026-10-30. "Returned anything" alone let one unrelated passage win. A
Maharashtra farmer asked how to grow Red Banana in a hill area. The
Maharashtra provider came first with a passage on custard apple; Bharat
Vistaar came second with three on bananas in hill areas. The farmer was told
there was no answer.

- When a lower-ranked provider also returned data, `select` tells the
  planner it may call `select_next`.
- `select_next` replaces the ask's data with the next provider's. It calls
  nobody: `select` already fetched it. One ask is still answered by one
  provider.
- The planner can go on down the list until the data answers the ask or no
  provider is left. An unranked ask has no next provider.
- Each switch is logged as `next_provider_used`.

Other options weighed:

- **Swap the ranking.** Loses the first provider's answers when it has them.
- **Providers drop weak matches.** Right in principle, but every provider
  must do it. Still worth doing; this rule works either way.
- **A separate relevance check per provider.** One more LLM call and about a
  second per provider. The planner already reads the data, so it judges.

## Consequences

- Which provider answers is configuration: deterministic, per adopter,
  changed without a release.
- A provider outside a sender's ranking is never consulted, so a question
  only it could answer goes unanswered. Accepted; what was dropped is logged
  per ask.
- A first-choice answer that cannot answer the ask gives way to the next
  one. The planner model judges that, and can be wrong either way: keep a
  thin answer, or skip a good one. `next_provider_used` lets the team check.
- Answers the catalog already holds (Direct answers) never go through
  `select`, so they still come from the best-ranked provider only.
- A ranking of three costs three `/select` calls where one was made. They run
  concurrently and each is bounded by its timeout.
- The model's `resource_id` picks the kind of data, no longer the vendor.
- One ask is still answered by one provider; merging answers stays out of
  scope.
