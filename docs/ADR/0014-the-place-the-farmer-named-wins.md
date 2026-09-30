# ADR-0014: The Place the Farmer Named Beats the Device Location

- **Status:** ACCEPTED
- **Date:** 2026-09-24
- **Deciders:** DSS implementation

---

## 1. Context and Problem Statement

A farmer in Anand asks "what is the weather in Pune?" and reads Anand's
weather. Nothing in the answer says the place was swapped.

`coverage_for` (`core/provider_discovery/service.py`) takes the device
coordinates first and never consults the place the farmer named. Its comment
states the reasoning: coordinates "are already what the spatial filter needs,
and a name could only contradict them". That holds when the only place a turn
carries is where the farmer is standing. It fails the moment a farmer asks
about somewhere else — a market they will travel to, a relative's field, a
district they are deciding whether to plant for.

Three properties of the current code shape the answer:

- **The place name is extracted but outranked.** The classifier already fills
  `Intent.place_name`, and the lookup already resolves it. The name simply
  sits last in a three-way precedence behind the device coordinates and the
  client's asserted `area`.
- **Resolution happens twice and is thrown away twice.** `coverage_for` runs
  once inside `discover_providers` and again as an orchestrator gate, with
  identical arguments. Both calls keep `AreaMatch.geometry` and discard
  `AreaMatch.name`, so no component downstream can state which place answered.
- **The index holds 784 Indian districts.** A farmer who names their town is
  told nothing was found, and an adopter outside India cannot be served at
  all.

## 2. Decision Drivers

1. A wrong place must be visible to the farmer, not silent. This is what makes
   the current bug expensive: the answer looks correct.
2. A follow-up must not lose the place. "How do I grow potato in Pune?" then
   "when will it rain?" is one conversation about one place.
3. The model must not be asked to invent coordinates. The existing
   `place_name` docstring already warns that a model asked for lat/lon
   produces plausible ones.
4. Nothing in the domain may assume India's administrative levels. The DSS is
   a DPG; an adopter in Kenya has counties, not districts.
5. One rule for which place wins, in one place. Today the precedence is spread
   across three call sites and drifts.

## 3. Considered Options

1. **Resolve once at entry, per ask**, and carry the resolved place through
   the turn.
2. **Reorder `coverage_for`** so the named place wins, and change nothing
   else.
3. **Resolve per turn, not per ask** — one place for the whole question.
4. **Name the administrative levels** in the domain model — `state`,
   `district`, `block`.

## 4. Decision Outcome

Chosen option: **1, resolved once at entry and carried per ask.**

Option 2 is the minimum that fixes the reported bug, and it was rejected for
what it leaves behind: the resolved name still dies inside `coverage_for`, so
the farmer still cannot see which place answered, and the precedence still
lives in a function called twice. The bug would be fixed and its cause would
not.

Option 3 was the existing design, recorded on `Intent.place_name`: "a turn is
grounded in one location however many asks it holds". **This ADR reverses
that.** A turn can hold "onion price at Lasalgaon and will it rain here?" —
two asks, two places, and one resolved location cannot serve both. Per-ask
also makes the discovery call correct without special-casing: each ask
searches around its own point.

Option 4 was rejected on driver 4. India has State → District → Block →
Village, Kenya has County → Sub-county → Ward, France has Région →
Département → Commune. Naming any of them in the domain makes the DSS
single-country.

Supporting choices, each following from the above:

- **A place carries an ordered chain of ancestors, coarsest first, with no
  level words.** `("India", "Maharashtra", "Pune")`. Disambiguation compares
  chains and shows the shortest part that distinguishes two candidates; the
  code never asks what a "state" is. This is how GeoNames, Who's On First and
  Photon model the same problem.
- **Narrowing a name that matches several places**, in order: drop a match
  inside a same-name match (Nashik block inside Nashik district); then the
  envelope's `region`. Each step only narrows, and never empties the list: a
  place the farmer named is never hidden by where their phone is. Each
  remaining choice shows the part of its chain that sets it apart ("Ashti,
  Wardha" / "Ashti, Beed"). A state the farmer names ("Bilaspur, Himachal
  Pradesh") is not asked of the model on a first question: doing so made it
  merge separate questions into one ask. It is used only when the farmer
  replies to our question (ADR-0015).
- **One precedence rule, per ask:** a place the farmer named (this turn, or
  carried from earlier), then the device's own geometry, then the client's
  asserted `area`, then a place another ask in the same turn resolved.
  "Here" means the farmer, so the device outranks a sibling's place; the
  classifier repeats a place that covers several asks. Live device geometry — sent this turn, with the
  farmer's location consent — is a fresher signal of where they are than an
  `area` the platform is only repeating from an earlier turn, so it now
  outranks that repeat. The client's `area` still moves below the farmer's own
  words: one is a platform repeating what it captured earlier, the other is
  what the farmer just said.
- **Not resolved is not one shape.** A named place can fail two different
  ways — it matches several areas, or none — and each needs its own answer to
  the farmer. `Ask.place` is a union
  (`ResolvedPlace | AmbiguousPlace | UnresolvedPlace | None`), not a resolved
  value plus a side channel for failure. The type itself carries which case
  happened; nothing downstream needs a second field to check.
- **Carry-forward is the classifier's job, not new state.** The last six turns
  are already rendered into the intent prompt. The prompt currently forbids
  taking a place from the conversation, which was written to stop the model
  inventing one; it is narrowed to "only ever copy a place someone actually
  said". No session store is added — the DSS does not own durable session
  state. The model marks a place it took from history (`place_from_history`),
  and the resolver labels it `CARRIED`. The model sets the flag, not code: it
  read both the query and the history, in whatever language. A text check in
  code fails as soon as the farmer's language differs from the English
  `place_name`.
- **The LLM's schema is split from the domain model.** `Intent` is handed to
  `llm.structured`, so any field on it is a field the model is asked to fill.
  `classify_intent` now returns `IntentClassification` — words only, `place_name`
  per ask, no geometry — and never builds an `Ask` itself. `resolve_places`
  is the only place a domain `Ask` is built, turning each `place_name` into a
  `place`. Driver 3 becomes structural rather than prompt-enforced.
- **The model's place name is trimmed in code.** A live model returned
  `'Pune, '` and `"Pune', "`, which matched nothing. `ClassifiedAsk` strips
  quotes, commas and spaces off the ends, and a name that is only punctuation
  becomes no name. The prompt handles meaning; code handles format.
- **The resolved place is a fallback in the composer, not an override.**
  `render_evidence` renders a provider's `resourceAttributes` verbatim, so a
  mandi price already names the market it came from — more precise than the
  district centroid we searched around. The composer prefers the place the
  data names and uses ours only when the data names none. Each result and
  failure carries its own ask's place — `Failure` gained `ask_index` to match
  `Result` — so a turn naming two places labels each block correctly rather
  than leaving the model to guess which is which.
- **Three clarification messages, not one.** Named nowhere, named a place the
  index lacks, and named an ambiguous one are different problems; today all
  three read "Which district are you in?". All three failing asks in one
  turn are reported together, in one reply, rather than only the first found
  — a farmer fixing one does not need a second round-trip to hear about the
  other.

## 5. Consequences

- `core/location/` is created for the resolver, alongside `core/enrichment/`
  and shaped like it: take a `IntentClassification`, look names up, return an
  `Intent`.
- **`coverage_for` is deleted.** Its precedence moves into the resolver.
  `coverage_for_ask` reads `ask.place` directly, no lookup — resolution
  already happened. The second resolution per turn disappears.
- **A resolved place reaches `/select` for the first time.**
  `resource_attributes._location_field` built the select body's location from
  `turn.location.geometry` alone, so a turn that resolved "Pune" from the
  question sent a weather select with no location at all. Per-ask resolution
  closes that gap as a side effect rather than as a separate fix.
- **Blocks enter the index and ambiguity rises sharply.** The generator's own
  header records why blocks were originally excluded: they take colliding
  names from 3 to several hundred. That is accepted here because this change
  builds the ambiguous branch that makes collisions answerable. The area
  index — regenerated with blocks and the real `within` chain — is renamed
  `areas.csv`; it was `districts.csv` when it held districts alone. Villages
  are still not included — the LGD snapshot does not carry them.
- **A block's coordinate is its district's, not its own — a known gap.**
  Every Block row in the snapshot this ships against has an inherited
  district/state centroid rather than a real point of its own. A farmer
  naming their block now resolves by name; the geometry returned is the
  district's, same precision as before blocks existed. Fixing this needs the
  snapshot rebuilt with a different geometry source, a decision for whoever
  owns that pipeline.
- **`NEEDS_DISTRICT_TEXT` stops being correct and changes** to
  `ClarificationText.needs_place` (`"Which place are you asking about?"`). Its old comment
  tied it to an index that "holds districts only"; with blocks that reason
  expires, and demanding a district is needlessly narrow. Clarification text
  is now a config primitive (`ClarificationText`, mirroring `Identity`) —
  bundled defaults; the loader takes a path, but no setting points it at an
  adopter file yet.
- **A turn with some places found answers those and reports the rest.** The
  gate asks the farmer only when no ask resolved a place. Otherwise each
  ambiguous or unresolved ask becomes a `Failure` with no `capability`,
  because no provider was called. It is recorded before the planner runs, so
  it is reported even if the model skips that ask, and `select` refuses to
  call for it. The turn ends `partially_answered`. The ambiguous candidate
  list is added after the answer (ADR-0015).
- **The ambiguity question's numbered format is a contract with the reply
  turn (ADR-0015).** The model reads the list back from the conversation
  history and copies the farmer's pick, so nothing can be invented. The list
  must stay legible in that history.
- **Carry-forward reaches back six messages, not the whole session.**
  `_HISTORY_WINDOW = 6` bounds how far the prompt looks. If that proves too
  short, the durable fix is session history the Experience API owns
  (architecture §1.2), not something this story changes.
