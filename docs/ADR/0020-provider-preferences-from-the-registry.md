# 20. Provider preferences from the registry, per turn

Date: 2026-10-21

## Status

Accepted

Amends [ADR-0019](0019-client-preferred-providers.md): where the caller names
itself, and where the rankings live.

## Context

ADR-0019 planned the rankings' externalisation: a preference service behind
the same port as the mounted file. That service exists — the network registry
holds one `SenderPreference` row per caller, edited without a DSS deployment.
Separately, the API contract was revised to carry the caller's identity in
the request body as `context.senderId`; keeping ADR-0019's `X-Client-Id`
header as well would leave two sources that can disagree.

## Decision

- **The caller is the sender, named once.** `context.senderId` rides the
  request body; the header is gone. The concept is renamed sender-wide — the
  port answers `for_sender`, the core model is `SenderPreference`, the log
  lines say `sender_id`. Absent, blank, or unregistered senders are served
  the default ranking, never refused.
- **The registry is the only source.** The YAML adapter and its mounted file
  are removed; the DSS holds no rankings of its own. With
  `DSS_PROVIDER_PREFERENCES_BASE_URL` unset, no ranking applies anywhere,
  warned once at boot.
- **Asked on every turn, in one search.** The registry's `or` filter fetches
  the sender's row and the reserved `default` row together:

      POST {base_url}/api/v1/SenderPreference/search
      {"filters": {"senderId": {"or": ["mh-consumer", "default"]}}}

  No cache, so an operator's edit applies on the next turn. Reading the whole
  book at boot would mean a restart per edit — what externalising was meant
  to end.
- **A failed lookup costs the ranking, never the turn.** A search that
  errors, times out (`DSS_PROVIDER_PREFERENCES_TIMEOUT_SECONDS`, default 3s),
  or returns garbage counts as "no rows", logged at WARNING; the turn runs as
  if no preference existed. Rankings order providers; they are not the
  answer.
- **Parsing is lenient.** The registry is edited while the DSS runs: a
  malformed entry is skipped, a malformed or inactive row reads as absent — a
  turn has nowhere to refuse to boot. The entity and field names are two
  module constants in the adapter; renaming them in the registry is a
  two-line edit here.

## Consequences

- A ranking edit in the registry applies on the very next turn, no release
  and no restart.
- No preference state in the DSS: nothing to mount, nothing to drift from
  the registry. The per-capability merge stays in core (`PreferenceBook`).
- One registry call per turn, on the critical path ahead of discovery — the
  short timeout is deliberate.
- A registry outage silently drops rankings: the turn is answered on
  unranked candidates, visible as the WARNING log line and missing
  `ranking_applied` lines, not as an alert. Monitoring the warning is the
  operator's job.
- A local run needs a stub of the search endpoint to demo rankings
  (`scripts/test-sender-preferences.sh registry` is one).
- Breaking change: a caller still sending `X-Client-Id` is served the
  default ranking until it moves the id into `context.senderId`.
