# ADR-0019: The Calling Client's Preferred Providers

- **Status:** ACCEPTED
- **Date:** 2026-10-14
- **Deciders:** DSS implementation

---

## 1. Context and Problem Statement

Several providers now serve the same topic in the same place — two weather
sources over one district, a national and a state mandi feed over one market.
They differ in quality, coverage and freshness, and nobody could say which one
a farmer should get.

Until now the planner's model chose. It is handed every candidate discovery
found and picks one by `resource_id`, so the provider behind an answer could
differ between two identical questions, and nobody could state a preference at
all.

Adopters running an experience layer on top of the DSS have vetted sources of
their own, and treat that choice as part of what they promise their users. One
adopter wants the state's own weather provider asked first; another wants the
national one. They need that honoured per topic, without a code change and
without affecting each other.

A second problem rides along: with two providers answering the same question
differently, nobody could tell whether a ranking was ordered the right way
round, because only the chosen provider's answer was ever recorded.

## 2. Decision Drivers

1. Which provider answers is the adopter's decision, stated in configuration —
   not the model's, and not a different one each turn.
2. An adopter changes their own ranking without affecting another adopter, and
   without a release.
3. A caller that sends no client id, or one nobody configured, is still
   answered. Rolling the header out must be a configuration step for an
   experience layer, never a breaking change for its farmers.
4. The rankings are a mounted file today and a preference service later. The
   move must be a change in the composition root, not in every caller.
5. The answers we did *not* use have to be recorded, or the ranking cannot be
   judged later.
6. `core/` reads no files and opens no spans (ADR-0012, the isolation rule).

## 3. Considered Options

### Where the client is named

1. **An `X-Client-Id` request header.** It identifies the caller, not the turn.
2. **A field on the request envelope.** The envelope is `additionalProperties:
   false` and describes what the farmer asked, not who relayed it; adding a
   field means a contract version bump for every caller, including the ones
   that will never set it.

### How a ranking is applied

1. **Filter and order discovery's findings, once, where discovery is
   composed.** Everything downstream reads the ranked set and knows nothing
   about clients.
2. **Tell the model the order in its prompt.** Cheap, and not a decision — a
   model that is *asked* to go in order sometimes does not, and "the
   first-choice provider answers whenever it returns anything" would be a
   tendency rather than a promise.
3. **Order at each consumer.** The no-match check, the planner's candidates,
   `describe_capability` and the catalog's Direct answers would each have to
   apply the same rule, and the first one that forgot would serve an answer
   from a provider the adopter ruled out.

### What a ranking means for a provider it does not name

1. **Exclusive — the list is the whole answer for its topic.** A provider the
   ranking does not name is never called.
2. **Ordering only — rank the named ones, keep the rest underneath.** A
   ranking could then only change *which* provider answered first, never
   whether an unvetted one could answer at all. Vetting is the point.

### How several ranked providers are consulted

1. **Call them all at once, use the first that returned anything.**
2. **Call them one at a time until one answers.** Fewer calls, but the farmer
   waits for each failure in turn, and the answers not used — the reason for
   doing this at all — are never collected.

## 4. Decision Outcome

**Chosen: 1 in each case.**

The calling client arrives as `X-Client-Id` and rides on `UserTurn.client_id`.
A `ProviderPreferences` port answers *for one client*, already resolved
against a default ranking. The YAML adapter reads a mounted file once at boot;
the merge rule itself lives in `core/provider_preferences`, so the preference
service that replaces the file later cannot drift on what "falls back to the
default" means. The port is async over a file read for that reason alone.

The ranking is applied in `orchestration/discovery.py`, on the way out of the
discovery stage: the turn already carries the client id and the intent already
carries each ask's topic, so it is the first point where both are in hand.
Both halves of a finding are governed — the capabilities the planner calls and
the Direct answers the catalog already holds — because being served by a
provider the adopter ruled out is the same wrong answer whether or not we had
to ask for it.

The planner's `select` tool then makes one call per *ask* rather than per
candidate: every ranked provider serving the chosen capability's `@type` is
consulted concurrently with the model's fields, and the answer is built from
the first ranked provider that returned anything. The rest are logged and
dropped.

**Topics are subject categories** (`Weather`, `Market`), not resolved `@type`
values: an adopter picks providers for weather, not for
`openagrinet:WeatherObservation`, and one category can resolve to several
`@type`s. The set is closed, so a typo'd topic refuses to boot rather than
loading cleanly and silently never matching.

**The fallback is per topic, not per client.** A client that ranks only
`Weather` is served the default's `Market` list. An unknown client and a
caller that sent no header both get the default throughout, and neither is
ever refused.

**Nothing is mounted by default.** A ranking is exclusive, so a shipped file
naming providers a deployment does not talk to would drop every candidate and
read to everyone as "nobody serves this". Unset means no ranking applies
anywhere and every ask keeps what discovery found — the behaviour that
existed before this — with one warning at boot.

**"Returned anything" is the whole sufficiency test** in this release. A
non-empty response from the first choice wins; its richness is not compared
with anything below it.

## 5. Consequences

### Good

- Which provider answers is configuration. Two adopters asking the identical
  question in the same place are each served their own first choice, and
  changing the order is an edit to a file.
- Deterministic. The same client, question and place reach the same provider
  every time, which is also what makes a regression visible.
- Every consulted provider's answer is recorded against the turn's
  `request_id` — the summary at INFO, the bodies at DEBUG where the
  invocation adapter already puts them (ADR-0014) — so the ranking can be
  judged on real answers rather than on intuition.
- The swap to a preference service touches the composition root only.

### Bad / accepted risks

- **A provider outside a client's ranking is never consulted**, so a question
  only that provider could have answered goes unanswered. This is the point of
  an exclusive list, and it is the first thing to check when one client's
  answers come back thinner than another's — which is why what was dropped is
  logged per ask.
- **A thin first-choice answer beats a fuller one below it.** "Returned
  anything" is a crude test. The logged unused answers are the evidence for
  revisiting it.
- **More provider calls per ask.** A ranking of three costs three `/select`
  calls where one was made before. They run concurrently and each is bounded
  by `DSS_SELECT_TIMEOUT_SECONDS`, so the farmer waits for the slowest rather
  than the sum — but the providers see the extra load, and a long ranking is
  an expensive one.
- **The model's `resource_id` no longer decides the vendor.** It still names
  the capability whose fields the model authored, and the tool's docstring
  says so, but a reader of the tool signature alone would not guess that one
  call can reach three providers.
- **One ask is still answered by one provider.** Merging or comparing several
  providers' answers into a richer one is out of scope; the DSS has no
  aggregation rule, and a composer handed two contradictory prices would have
  no basis to choose.

### Follow-ups

- The preference service, replacing the mounted file behind the same port.
- A real sufficiency judgement per provider, replacing "returned anything".
- State- and country-level default layers under the client, if adopters ask
  for them — the merge rule is the one place that would change.
