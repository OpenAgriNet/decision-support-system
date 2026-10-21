# ADR-0020: Provider Preferences from the Registry, per Turn

- **Status:** ACCEPTED
- **Date:** 2026-10-21
- **Deciders:** DSS implementation
- **Amends:** ADR-0019 (where the caller names itself, and where the rankings live)

---

## 1. Context and Problem Statement

ADR-0019 gave each calling application a per-topic provider ranking and
planned its externalisation: "the preference service, replacing the mounted
file behind the same port". That service exists now — the network registry
holds one row per caller, maintained there like the participants and the
capability bindings, and edited without a DSS deployment.

Two things had to be settled to use it:

1. **Where the caller names itself.** ADR-0019 chose an `X-Client-Id` header
   over a body field, to spare the envelope a version bump. The API contract
   has since been revised, and the revision carries the caller's identity in
   the request body as `context.senderId` — the network's own word for a
   calling application. A header duplicate of a body field would leave two
   sources that can disagree.
2. **When the rankings are read.** The file is read once at boot; a registry
   can be read at boot too, or on every turn.

## 2. Decision Drivers

1. One name and one place for the caller's identity, end to end: the contract
   says `senderId`, so the DSS says sender everywhere — a reader must never
   wonder whether a client and a sender differ.
2. An operator's edit to a ranking applies without a DSS restart. Live
   rankings are the reason to externalise them at all.
3. A registry outage must not become a farmer outage. Rankings order
   providers; they are not the answer.
4. The DSS holds no preference machinery of its own. Rankings are the
   registry's data; a deployment without a registry simply has no rankings,
   which is the behaviour that predates them.

## 3. Considered Options

### Where the caller names itself

1. **`context.senderId` in the request body.** The revised contract's choice;
   one source of truth, validated with the rest of the envelope.
2. **Keep the `X-Client-Id` header.** Two sources that can disagree, and a
   name (`client`) the network does not use.

### When the registry is read

1. **On every turn, two searches in parallel — the turn's `senderId` and the
   reserved id `default`.** An edit applies on the next turn. The default
   ladder is fetched alongside rather than after, so a sender nobody
   registered costs no second round trip; the DSS holds no cache, so there is
   no staleness rule to document and no invalidation to build.
2. **At boot, the whole book.** A restart per ranking edit — exactly what
   externalising was meant to end — and a "give me everything" call the
   registry should not have to serve.
3. **Per turn with a cache.** Fewer calls, but a staleness window to explain
   to an operator mid-incident, for two small searches that run concurrently
   with nothing else the turn needs.

### What a failed lookup does to the turn

1. **Costs the ranking, never the turn.** A search that errors, times out, or
   returns garbage counts as "no row found", logged at WARNING. Both searches
   lost → the turn runs as if no preference existed.
2. **Fails the turn.** Treats routing data as load-bearing; a registry blip
   would read to a farmer as the DSS being down.

## 4. Decision Outcome

**Chosen: 1 in each case.**

The caller arrives as `context.senderId` and rides `UserTurn.sender_id`; the
`X-Client-Id` header is gone. The concept is renamed sender-wide: the port
answers `for_sender`, the core model is `SenderPreference`, and the log lines
say `sender_id`.

`RegistryProviderPreferences` (`adapters/provider_preferences/registry.py`)
implements the `ProviderPreferences` port over the registry's search API:

    POST {DSS_PROVIDER_PREFERENCES_BASE_URL}/api/v1/SenderPreference/search
    {"filters": {"senderId": {"eq": "<senderId or 'default'>"}}}

Both searches go out in parallel inside one `for_sender` call, bounded by
`DSS_PROVIDER_PREFERENCES_TIMEOUT_SECONDS` (default 3s). The rows feed the
`PreferenceBook` merge in core, so "a sender's list replaces the default's
per topic" stays defined in exactly one place. The entity and field names are
two module constants — if the registry publishes them differently, that is
the whole edit.

**Parsing is lenient.** The registry is edited while the DSS runs, so a
malformed entry is skipped with a warning and a malformed row reads as
absent — a turn has nowhere to refuse to boot. An inactive row or entry is
honoured as retired.

**The registry is the only source.** The ADR-0019 YAML adapter, its
`DSS_PROVIDER_PREFERENCES_PATH` setting and the example file are removed —
the DSS maintains no rankings of its own. `DSS_PROVIDER_PREFERENCES_BASE_URL`
unset means no ranking applies anywhere, warned once at boot; the composition
root's stand-in answers "no preference" for every sender.

## 5. Consequences

### Good

- A ranking edit in the registry applies on the very next turn, no release
  and no restart.
- The experience layer sends one envelope; its identity is contract data,
  versioned and validated like everything else it sends.
- The swap landed where ADR-0019 said it would: the composition root picks
  the adapter; orchestration and core did not change for it. The per-topic
  merge (`PreferenceBook`) stays in core, exercised by the adapter and the
  test fakes alike.
- No preference state in the DSS at all: nothing to mount, nothing to drift
  from the registry, one place to edit a ranking.

### Bad / accepted risks

- **Two registry calls per turn.** Small, parallel, and bounded, but they are
  on the turn's critical path ahead of discovery. The timeout is deliberately
  short for that reason.
- **A registry outage silently drops rankings.** The turn is answered on
  unranked candidates — by design — so a long outage shows up as "the wrong
  provider answered", found via the WARNING log and the absent
  `ranking_applied` lines, not as an alert. Monitoring the warning is the
  operator's job.
- **A malformed registry row is skipped, not refused.** Boot-time
  strictness has no turn-time equivalent; the typo surfaces as a log line
  instead.
- **A local run cannot demo rankings from a file any more.** It needs a
  registry, or a stub of its search endpoint — the e2e test's stub is the
  reference.
- **Breaking change for header users.** Any caller already sending
  `X-Client-Id` must move it into `context.senderId`; the header is ignored.

### Follow-ups

- A short-lived cache if the per-turn calls ever show up in the latency
  budget — the port's shape already allows it.
