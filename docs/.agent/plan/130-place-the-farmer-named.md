# Spec — #130 Answer for the place the farmer named

## Context

A farmer in Anand asks "what is the weather in Pune?" and gets Anand's weather.
Nothing tells them the place was swapped.

The cause is in `core/provider_discovery/service.py:67`. `coverage_for` takes the
device coordinates first and never looks at the place the farmer named. The
comment says a name "could only contradict them". That is the bug.

Two more problems sit alongside it:

- Only 784 Indian districts resolve. A farmer naming their town is not understood.
- The resolved place is thrown away. `coverage_for` keeps the coordinates and
  discards `AreaMatch.name`, so nothing downstream can say which place answered.

This story fixes all three, and makes the place model work outside India.

---

## Decisions

Settled with the user. Do not re-open.

1. **Resolve once, at entry.** Today `coverage_for` runs twice per turn with the
   same arguments. After this, one resolution step runs after intent
   classification and everything downstream reads its result.
2. **Location per ask.** Each `Ask` carries its own resolved place. The turn
   keeps the device location. This reverses the current comment on
   `Intent.place_name`.
3. **Carry forward within a session.** If the current text names no place, use a
   place named earlier in the conversation. Turn 1 "how to grow potato in Pune"
   then turn 2 "when will it rain?" resolves to Pune, not the device location.
4. **The answer names its place.** The resolved name reaches the composer, which
   words it naturally. No template.
5. **The resolved place reaches `/select`,** not just `/discover`.
6. **Ship block-level data.** Regenerate the CSV to include blocks (~7k rows) as
   well as districts.
7. **Ask well now, pick later.** #130 asks a numbered clarification question and
   stops. Reading the farmer's reply is #131.

---

## The place model

Levels are named differently everywhere — India has State/District/Block, Kenya
has County/Sub-county/Ward, France has Région/Département/Commune. Naming them
in code makes the DSS India-only.

So a place carries an **ordered chain of ancestors, coarsest first, with no level
words**:

```
name:     "Baramati"
within:   ("India", "Maharashtra", "Pune")
geometry: point
```

The DSS never knows what a "state" is. An adopter in Kenya fills `within` with
counties and everything works unchanged. This is how GeoNames, Who's On First and
Photon model it.

Disambiguation compares chains and shows the shortest part that tells candidates
apart. Two Bilaspurs differing at position 1 show position 1. Two Baramatis in
one state differ at position 2 and show the district.

---

## Precedence

One rule, replacing the three scattered ones:

```
place named in this turn
  > place carried forward from this session
  > client-asserted area
  > device geometry
  > nothing
```

**"Client-asserted area"** is `turn.location.area` on the API request — a place
*name* the calling platform sends us, separate from the device coordinates in
`turn.location.geometry`. Typically the platform captured a district on an
earlier turn and now repeats it on every request. It is the platform asserting
where the farmer is, not something the farmer said this turn.

Today it **beats** the farmer's own words (`service.py:74-78`). After this
change it drops below them: what the farmer actually said wins, and the
platform's assertion is only used when nobody named anywhere.

Carry-forward beating the device location is decision 3.

---

## Changes

### Models — `core/intent/models.py`

`ResolvedPlace` — frozen, `extra="forbid"`: `name`, `within: tuple[str, ...]`,
`geometry`, `source` (DEVICE / NAMED / CARRIED).

`Ask` gains `place: ResolvedPlace | None`. `Intent.place_name` is removed.

**The LLM must never fill this.** `Intent` is the structured-output schema handed
to `llm.structured`. A nested geometry in that schema invites the invented lat/lon
the existing docstring warns about. So split the schemas:

- `ClassifiedAsk` / `Classification` — what the LLM returns. Carries
  `place_name: str | None` per ask, no geometry.
- `Ask` / `Intent` — the domain shape, with `place` filled by the resolver.

This makes "the model never writes coordinates" structural, not prompt-enforced.

### New package — `core/location/`

`resolve_places(intent, turn, *, lookup) -> PlaceResolution`

Returns a new `Intent` with each ask's `place` filled, plus a `PlaceOutcome`:

| Outcome | Means |
|---|---|
| `RESOLVED` | every ask that needs a place has one |
| `AMBIGUOUS` | a named place matched several — candidates carried |
| `UNRESOLVED` | a place was named, the index does not carry it |
| `NONE` | nobody named a place and no device location |

Per ask: resolve its own name; else reuse a place another ask in the turn
resolved; else client area; else device geometry.

**Shape it like `core/enrichment/`.** That package already does the same kind of
job: `resolve_scheme_subjects` takes an `Intent`, looks names up in a catalog,
and hands back a *new* `Intent` with those names rewritten — plus a record of
what it matched, so the rewrite can be logged. `Intent` is frozen, so nothing is
edited in place; a new one is built.

Location resolution is the same pattern with a different index: take an
`Intent`, look names up, return a new `Intent` with `place` filled, plus a
record of how it went (`PlaceOutcome`). Copying a shape the codebase already has
means one less thing for a reviewer to learn.

### Call site — `orchestration/turn.py::run_turn`

**Not inside `_enrich`.** That helper has one job — resolving scheme names
against the scheme catalog — and it returns early when no catalog is mounted
(`turn.py:82-83`). Location resolution must run whether or not a scheme catalog
exists, so putting it there would either be skipped on that early return or
force `_enrich` to stop being about schemes. Two unrelated lookups in one
function also makes the trace span meaningless: today each step gets its own
`trace_component`, and a combined one could not tell you which lookup was slow.

So it goes as its own step, in three parts:

**Where.** `classify_then_discover` (`turn.py:160-166`) currently runs two steps
in sequence: `_enrich`, then `discover_providers`. Location resolution slots
between them — it needs the enriched `Intent`, and discovery needs its result.

**Its own trace span.** Each step is wrapped in `trace_component(name, ...)`,
which is what makes a slow turn diagnosable in Langfuse — you can see which step
cost the time. A new step gets its own span, `"location"`.

**Passing the index in.** `resolve_places` needs the `AreaLookup`. `run_turn`
does not have it today, so it takes it as an argument; `Orchestrator` already
holds one (`orchestrator.py:154`) and passes it through.

**Returning the outcome.** `run_turn` returns a `TurnResult` holding what each
step produced — `intent`, `decision`, `discovery`. It gains a fourth field,
`location: PlaceOutcome`, so the orchestrator's gate can read the result instead
of recomputing it. When moderation refuses the turn, the existing code blanks
`intent` and `discovery` because they describe work that must not be acted on;
`location` is blanked with them for the same reason.

### Discovery — `core/provider_discovery/service.py`

Delete `coverage_for`. Its precedence is now the resolver's job and duplicating
it reintroduces the drift this story fixes.

Add `coverage_for_ask(ask, *, radius_m)` — reads `ask.place`, no lookup.

`_build_queries` computes coverage **per ask**. A turn asking about two places
issues two spatially different discover calls.

`build_discover_providers` and the composition root drop `area_lookup`.

### `/select` — `core/planner/resource_attributes.py`

`_location_field` takes a `ResolvedPlace` instead of reading
`turn.location.geometry`. `build_resource_attributes` gains `place`; `turn` is
then unused there and comes out. Caller `orchestration/planner.py:106` already
holds the ask.

This is the first time a resolved place reaches `/select`.

### Composer — `core/channel/prompt.py`

This is decision 4: the answer must say which place it is about, so a wrong
resolution is visible to the farmer instead of silent.

The composer is the component that writes the farmer's answer. It builds two
prompts for the model — a system prompt (standing instructions) and a user
prompt (this turn's material). Today the user prompt holds exactly two things:
the farmer's question, and the data the providers returned. **Neither mentions a
place**, so the model cannot name one.

Three changes:

**1. Pass the place in — as a fallback, not an override.**

The provider's answer often already names a place. `render_evidence`
(`prompt.py:75-94`) renders `result.data` — the provider's `resourceAttributes`
verbatim — so a mandi price comes back carrying the market's own name and
location. **That is better than ours**: it is the actual market, where we only
resolved a district centroid to search around.

So the resolved place is what the model falls back to when the evidence names no
place of its own — a weather reading, or a turn that returned nothing.

The system prompt states the order: prefer a place the retrieved data names;
use the place block when it names none. This is a prompt instruction rather than
code because only the model can tell whether a given `resourceAttributes` blob
names a place — the shapes differ per pack and `render_evidence` deliberately
does not interpret them.

`user_prompt` already composes its material from labelled blocks — the question
in one, the provider data in another. The place gets a third.

**2. Wrap it as data, not instruction.** The existing blocks go through
`wrap_as_data`, which marks text as material to read rather than orders to
follow. The place name comes partly from what the farmer typed, so it must be
wrapped the same way — otherwise a farmer who types a place name containing
instruction-like text could steer the model. This is a real safety property the
codebase already maintains, not ceremony.

**3. Tell the model to use it.** Added to `SYSTEM_PROMPT`: say which place the
answer is about, in your own words, in the natural place for it. Prefer a place
the retrieved data names — it is more precise than the one we searched around.
Use the place block only when the data names none. Never invent one.

Deliberately *not* a template. `"Weather in {place}: ..."` would read as a
machine stamp and would break across languages — the composer writes in the
farmer's language. The model weaves the place into its own sentence.

Getting the place there means the composer needs the `Intent` (which holds the
asks and their places). So `ComposeStream` and `stream_response` take it, and
the orchestrator passes it at the call site. The fakes in `tests/support/` move
with the signature.

### Clarification — `core/channel/service.py`

Today **one** message covers every failure: "Which district are you in?" The
farmer reads the same sentence whether they named nowhere, named a place we do
not carry, or named one that matches several. Those are different problems and
only the first is answered by that question.

Worse, its own comment (`service.py:32-34`) says it asks for a *district*
specifically because the index holds districts only. **Blocks break that
reason** — the farmer can now name a smaller place, so demanding a district is
both wrong and needlessly narrow. The text must change regardless.

| Outcome | What the farmer reads |
|---|---|
| `NONE` — named nowhere, no device location | "Where are you asking about?" — no longer district-specific |
| `UNRESOLVED` — named a place we do not carry | "I could not find Xyzzy." |
| `AMBIGUOUS` — the name matches several | the numbered list below |

Three messages because there are three problems. A farmer who typed a place we
do not carry is helped by hearing that, not by being asked a question they
already answered.

All deterministic f-strings, matching how `no_match_answer` and
`needs_district_answer` already work — asking a fixed question needs no model.

- `unknown_place_answer(name)` — "I could not find <name>."
- `ambiguous_place_answer(name, candidates)` — a **numbered list**, formatted so
  #131 can read it back from history:

  ```
  Which Bilaspur?
  1. Bilaspur, Himachal Pradesh
  2. Bilaspur, Chhattisgarh
  ```

  Each line is the candidate's shortest distinguishing ancestor. #131 will give
  this list to the LLM and have it pick — so "HP", "2", and "Himachal Pradesh
  Bilaspur" all resolve, and nothing can be invented. **This format is a
  contract with #131.**

`answer_for_place_outcome(outcome)` in `core/channel/service.py` maps an outcome
to its message. The orchestrator's gate then reads `result.location` and calls
it — no location logic in the orchestrator itself.

This keeps a rule the codebase already follows. `orchestrator.py` wires steps
together; it does not decide business rules. `_status_for` (line 320) states it:
it "reads facts off `Evidence` — it invents no rule, so no business `if` escapes
`core/`". Deciding *which* message a farmer reads is a business rule, so it
belongs in `core/`; the orchestrator only asks for the answer and yields it.

### Intent prompt — `core/intent/service.py:88`

Moves to per-ask. The line "do not guess one from the conversation's subject"
currently blocks carry-forward and must change to:

- fill from the latest query when it names a place
- else use the most recent place named earlier in this conversation
- **only ever copy a place someone actually said** — never infer from crop,
  language or subject

"Never invent" is now enforced by a narrower rule, so it needs the tier-5
negative test below to earn the relaxation.

Note `_HISTORY_WINDOW = 6` bounds carry-forward to 6 messages, not a whole
session. Name this limit in the PR; do not fix it here — history is the
Experience API's to own.

### Data — `scripts/generate_district_csv.py`

Include blocks alongside districts. Add a `within` column, semicolon-separated,
coarsest first. The generator already walks `parent_code` to a state name, so
the chain is one more level of the same walk.

Blocks take name collisions from 3 to 226. That is expected and is why the
ambiguous branch must work.

### Lookup — `adapters/area_lookup/csv_lookup.py`

`_qualified_by` (line 90) full-scans every key on a prefix fallback — O(n), and
the fallback runs on every **miss**, which is the "place we do not carry" path.

Replace the scan with `bisect` over a sorted key list. Sorting puts every key
sharing a prefix in one contiguous block: `bisect_left` finds where it starts in
O(log n), then walk forward while `startswith` holds.

Keep both views, built once at load:

- the dict for the exact hit — O(1), the common path, unchanged
- a sorted key list for the prefix fallback

Measured on this machine, worst case (a name not in the index):

| Rows | Full scan | Bisect |
|---|---|---|
| 784 (today) | 0.010 ms | 0.0001 ms |
| 7,000 (blocks) | 0.101 ms | 0.0001 ms |
| 600,000 (villages) | 22.4 ms | 0.0002 ms |

Blocks alone do not need this — 0.1 ms is below the noise of a network call. It
goes in now because it is ~5 lines, changes no behaviour, and removes a cliff an
adopter would otherwise hit with real village data, far from whoever wrote it.

Behaviour is identical, so the 10 existing `test_csv_lookup.py` tests stay green
unchanged — that is the check that this is a pure optimisation.

---

## Testing

Per the CLAUDE.md tier table. Tier 1 must outnumber the rest combined.

**Tier 1 — `tests/unit/core/location/test_resolve_places.py`** (new, ~14):
named place beats device geometry (**the headline test**); beats client area;
carried-forward place beats device; no place anywhere → device; nothing → NONE;
unresolvable → UNRESOLVED with the name; ambiguous → AMBIGUOUS with candidates;
region narrows; two asks two places; two asks one place; input `Intent`
unmutated; empty `Intent` never touches the lookup.

**Tier 1 — updated:** `test_coverage.py` retargets to `coverage_for_ask` (~3;
its 7 precedence tests move to the file above). `test_models.py`,
`test_service.py` (prompt assertions for carry-forward *and* never-invent),
`test_resource_attributes.py`, `test_service.py` (channel — the two new answers
and the numbered format). Composer prompt tests: the place block is present and
wrapped as data; absent when no place resolved; the system prompt carries both
the "say which place" line and the "prefer the place the data names" order.

**Tier 2:** `test_csv_lookup.py` — the 10 existing tests stay green unchanged
through the `bisect` rewrite. Add block-level and `within` cases.

**Tier 3:** ambiguous place short-circuits with the numbered question; a resolved
place reaches the composer; `/select` carries the resolved geo, not the device
point; `run_turn` fills `TurnResult.location`. Keep
`test_an_unlocated_turn_asks_for_a_district` — the `NONE` path still
short-circuits to `REQUIRES_INPUT` with the planner and composer never running,
but its assertion changes: the message no longer demands a *district*, because
blocks mean a smaller place is now answerable. Rename it to match.

**Tier 5 — `test_intent_place_name.py`:** carry-forward resolves Pune from
history; **never-invent returns None** with no place anywhere. The second earns
the prompt relaxation.

**Tier 4:** re-record any cassette pinning a composed answer.

---

## Verification

```
uv run pytest
uv run ruff check . && uv run ruff format .
```

PO test, end to end:

1. `POST /v1/turns` — "what is the weather in Pune?", device location Anand.
   Answer is about Pune and says so.
2. Same session — "when will it rain?". Still Pune.
3. "weather in Bilaspur" — numbered question naming both.
4. "weather in Xyzzy" — not recognised, no answer about somewhere else.
5. No place named — device location, unchanged from today.

---

## Docs

Required in the same change by CLAUDE.md:

- **ADR** — this reverses a recorded position (location turn-level → ask-level)
  and changes device-vs-spoken precedence.
- **`docs/DSS_ARCHITECTURE.md`** §5.2 — per-ask `place`; add `ResolvedPlace` and
  `PlaceOutcome` to the domain language table.

---

## Commit sequence

1. Models: `ResolvedPlace`, `PlaceOutcome`, per-ask `place`, the
   `Classification` split. Unwired.
2. `core/location/service.py` + tier 1. Unwired.
3. Wire into `run_turn`; delete `coverage_for`; per-ask coverage.
4. The gate: `TurnResult.location`, the new answers, the numbered format.
5. `/select`.
6. Composer.
7. Lookup: `bisect` prefix scan. Behaviour-neutral — the existing tests are the
   check.
8. Data: blocks + `within`.
9. Intent prompt + tier 5. **Last** — the only change needing a live model.
10. ADR + architecture doc.

---

## Deferred

- **Villages.** The LGD snapshot has no villages — it stops at blocks. Village
  coverage needs a different source (GeoNames) and is its own story.
- **Reading the farmer's reply.** #131.
- **Reverse-geocoding device coordinates to a name.** Needs `nearest()` on the
  port. The composer says "your area" instead.
- **Per-category gating** — whether a scheme ask needs a place at all. A new
  policy decision; today's all-or-nothing gate stays.
- **Validating `place_name` is a literal copy of prompt text.** Only if the
  tier-5 never-invent test fails. Transliteration makes it genuinely hard.
- **`_HISTORY_WINDOW` past 6.** Session state is not the DSS's to own.
