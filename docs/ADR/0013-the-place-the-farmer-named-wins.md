# ADR-0013: The Place the Farmer Named Beats the Device Location

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
- **One precedence rule:** a place named this turn, then a place carried
  forward from this session, then the client's asserted `area`, then the
  device geometry. The client's `area` moves below the farmer's own words: one
  is a platform repeating what it captured earlier, the other is what the
  farmer just said.
- **Carry-forward is the classifier's job, not new state.** The last six turns
  are already rendered into the intent prompt. The prompt currently forbids
  taking a place from the conversation, which was written to stop the model
  inventing one; it is narrowed to "only ever copy a place someone actually
  said". No session store is added — the DSS does not own durable session
  state.
- **The LLM's schema is split from the domain model.** `Intent` is handed to
  `llm.structured`, so any field on it is a field the model is asked to fill.
  A classifier-only `Classification`/`ClassifiedAsk` carries `place_name`; the
  resolver builds the domain `Intent` with the geometry. Driver 3 becomes
  structural rather than prompt-enforced.
- **The resolved place is a fallback in the composer, not an override.**
  `render_evidence` renders a provider's `resourceAttributes` verbatim, so a
  mandi price already names the market it came from — more precise than the
  district centroid we searched around. The composer prefers the place the
  data names and uses ours only when the data names none.
- **Three clarification messages, not one.** Named nowhere, named a place the
  index lacks, and named an ambiguous one are different problems; today all
  three read "Which district are you in?".

## 5. Consequences

- `core/location/` is created for the resolver, alongside `core/enrichment/`
  and shaped like it: take an `Intent`, look names up, return a new one.
- **`coverage_for` is deleted.** Its precedence moves into the resolver, and
  the orchestrator's gate reads the outcome off `TurnResult` instead of
  recomputing it. The second resolution per turn disappears.
- **A resolved place reaches `/select` for the first time.**
  `resource_attributes._location_field` built the select body's location from
  `turn.location.geometry` alone, so a turn that resolved "Pune" from the
  question sent a weather select with no location at all. Per-ask resolution
  closes that gap as a side effect rather than as a separate fix.
- **Blocks enter the index and ambiguity rises sharply.** The generator's own
  header records why blocks were excluded: they take colliding names from 3 to
  226. That is accepted here because this change builds the ambiguous branch
  that makes collisions answerable. Villages are not included — the LGD
  snapshot does not carry them.
- **`NEEDS_DISTRICT_TEXT` stops being correct and changes.** Its comment ties
  it to an index that "holds districts only"; with blocks that reason expires,
  and demanding a district is needlessly narrow.
- **The ambiguity question's numbered format is a contract with #131.** That
  story resolves the farmer's reply by giving the LLM the candidate list and
  having it pick, so nothing can be invented. The list must therefore be
  legible in the conversation history that #131 reads back.
- **Carry-forward is bounded by `_HISTORY_WINDOW = 6`, not by the session.** A
  place named ten turns ago is outside the window and silently stops carrying.
  Raising it costs prompt tokens on every turn; the durable fix is session
  history, which the Experience API owns (architecture §1.2). Named here
  rather than fixed.
- **The prompt's "never invent a place" guard is weakened by design.** It was
  previously enforced by forbidding the model to read history at all; it is
  now a narrower rule about copying only spoken words. A tier-5 test that a
  query with no place anywhere returns none is what earns the relaxation. If
  it drifts, the fallback is validating that the name appears in the prompt
  text — declined for now because transliteration means the English "Pune"
  does not appear literally in "मी पुण्याहून".
