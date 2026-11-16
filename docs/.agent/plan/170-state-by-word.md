# #170: a state is found by a word in its name

## Problem

"Tomato pests in Kashmir" asks "Which Kashmir?" and lists two villages, in
Rajasthan and Himachal Pradesh. Jammu and Kashmir is not a choice.

Three things cause it:

1. The area file has no place named "Kashmir". The state is known only by its
   full name.
2. Photon returns "Jammu and Kashmir", but the adapter keeps only results named
   exactly as typed.
3. Core drops a state whenever real places are also found.

The same causes hide other places. "Andhra" silently resolves to a village in
Himachal Pradesh. In Kenya, "Baringo" (a county) silently resolves to a village
of that name in another county.

## Decisions

- **Photon keeps a state when the typed name is a whole word in its name.**
  "Kashmir" fits "Jammu and Kashmir". "Kashmiri Bagh" is not a state, so it
  stays out.
- **Only when one state fits.** "Pradesh" fits five states. None is kept, so
  it stays not found, as today.
- **Core drops a state only when a found place sits inside it.** Chandigarh
  the district sits inside Chandigarh the territory, so the district wins as
  today. The village Kashmir in Rajasthan is not inside Jammu and Kashmir, so
  both are offered.
- **A state is never the place itself.** It is only a line in the question.
  When it is the only thing found, the existing state rule runs.
- **A user inside an offered state gets their own place.** A user in
  Srinagar who says "Kashmir" is not asked to pick against a village in
  Rajasthan.
- **A state gets its own line in the question,** with its full name.

Rejected:

- **An alias file ("Kashmir" means Jammu and Kashmir).** The file answers
  first and the chain stops, so the two villages could no longer be reached.
  It also needs a hand-kept list for every state.
- **A looser name match for all places.** Photon also returns "Chota Kashmir"
  in Mumbai and "Kasmar" in Jharkhand. A loose match lets confident wrong
  places in.
- **Telling a word match from an exact match by comparing names in core.** The
  "inside" test covers Chandigarh directly and also fixes Baringo, where the
  names are equal.

No ADR: no new technology and no change of direction. The port is unchanged.

## Changes

One test, then its code, one at a time.

### 1. Photon adapter — `adapters/area_lookup/photon.py`

Tier 2, `tests/integration/adapters/area_lookup/test_photon.py`.

- A state named with the typed word is kept, next to the exact matches.
- A place that only contains the word is still not a match.
- A word that fits two states keeps neither.

### 2. Core — `core/location/service.py`

Tier 1, `tests/unit/core/location/test_resolve_places.py`.

- A state is offered next to same-word places that are outside it.
- A state is dropped when a found place sits inside it.
- A state near the user is never picked as the place.
- A state left alone by the narrowing is not the place.
- A user inside an offered state gets their own place.
- A name with its state after a comma resolves without a question.
- A state found alone follows the state rule (already covered; must stay green).
- `_resolve_named` is split into named steps. No behaviour change beyond the
  rules above, except: a state now also has to fit the part after a comma
  ("Maharashtra, Kenya" is not found).

### 3. Question text — `core/channel/service.py`

Tier 1, the channel service tests.

- A state among the choices has its own line. Today the lines are grouped by
  the level where the places differ. A state has no such level and would fall
  out of the list.

### 4. Docs

- `docs/PLAYBOOK.md`, "Place names the file lacks": the "Exact names only" and
  "A whole state or region" lines.
- `docs/DSS_ARCHITECTURE.md`, if it states either rule.

## Out of scope

- Old names such as "Orissa". That needs aliases.
- A word that fits a state in two countries at once. None is kept, as today.

## Known side effects

- "Uttar" today offers places that start with it (Uttar Dinajpur). It will
  lead to Uttar Pradesh.
- A generic word matches too: "Islands" leads to Andaman and Nicobar Islands.

## How to verify

- The rules were run on 142 names across India, Kenya and Ghana against the
  public Photon server: every state name and each word in it. No name got
  worse. All 36 full Indian state names behave as today.
- `uv run pytest tests/unit/core/location tests/unit/core/channel tests/integration/adapters/area_lookup`
- By hand, with Photon on: "Kashmir" lists three choices. "Kashmir, Rajasthan",
  "Chandigarh" and "Punjab" behave as before.
