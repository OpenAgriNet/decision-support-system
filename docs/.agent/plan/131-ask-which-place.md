# #131 — Ask the farmer which place they meant

## Context
A named place can match several areas (Rampur, Bilaspur, Ashti). #130 detects
this (`AmbiguousPlace`) and replies with a fixed numbered question
(`core/channel/service.py::answer_for_unplaced_asks` + `_candidate_lines`).
But the next turn is treated as new: "Himachal" or "2" means nothing. #131
makes that reply finish the original question, statelessly, with no extra LLM
call. Branch: `feat/131-ask-which-place` (on top of #130). Story:
OpenAgriNet/engineering-tracker#131.

## Settled decisions
1. **Stateless.** The pending question lives in the conversation history the
   Experience API already sends (`adapters/http/v1/mapping.py:62` builds
   `turn.history` with both roles). No store, no tag, no contract change.
2. **One intent call per turn, as today.** On the reply turn the model reads
   history, returns the *original* ask, and copies the picked line into
   `place_name` ("Rampur, Himachal Pradesh"). Code splits at the last comma
   (no area name contains one — checked), looks up the name, and keeps the
   candidate whose `within` chain holds the part. A new, unrelated question is
   classified normally; the old one is simply ignored.
3. **Prompt exception:** when the user picks one of the options the assistant
   listed, copy that option — it is the user's choice, not "a place only the
   assistant named". Vashi/Lasalgaon must still stay null.
4. **At most 5 choices listed; a long list is narrowed first, then grouped.**
   - *First, the farmer's region narrows it.* This already happens today:
     `_resolve_named` keeps only matches in the envelope's `region` when that
     leaves at least one. So a farmer in Himachal asking about Rampur never
     sees the Uttar Pradesh ones. (Nothing new; a tier-1 test pins it for a
     long list.) Device *geometry* is not used to pick — the story puts
     "guessing from where they are" out of scope.
   - *If still more than 5: ask one level up, with no level words.* Group the
     matches by the first part of their chains where they differ, and list
     those groups: "Rampur is in several places. Which one: 1. Uttar Pradesh
     2. Himachal Pradesh 3. Odisha?". The groups come from the data, so the
     question never says "state" or "district" — in Kenya it would list
     counties. If there are more than 5 groups too, list the first 5 and add
     "or name a larger place nearby".
   - The reply works the same way: "Rampur, Uttar Pradesh" narrows the
     matches; if several are left (two Rampurs in UP) the farmer is asked
     again, one level further down.
5. **Still not one place → the same question again.** No "still" wording
   (would need detecting our earlier question). Never a silent guess.
6. **No expiry logic.** Nothing is stuck: a new question is new. A late bare
   "2" still answers the old question; how much history is sent is the
   Experience API's policy. Stated in the ADR.
7. **The answer says which place it used, not just the name.** Today the
   composer is told "this data is about Rampur" — the same bare name the
   farmer found confusing. Instead it is told "about Rampur, Himachal
   Pradesh": the name plus the place directly above it in the chain (for a
   district that is its state; for a block, its district — "Ashti, Wardha").
   This applies to every resolved place, not only after a question.
8. **Partial turns show the choices.** After the composed answer the
   orchestrator appends the same fixed "Which X? 1… 2…" block, so a reply
   works exactly as in the all-failed case. Closes the #130 TODO gap.
9. **Place-only.** No generic clarification layer; the ADR records the
   pattern (fixed list → model copies the pick → code checks it) for reuse.

## Implementation (strict TDD, one test at a time)
1. **Narrow by a "Name, Part" place name** — `core/location/service.py::_resolve_named`:
   if `place_name` has a comma, split at the last one; narrow the (nested-
   dropped) matches to those whose `within` contains the part (casefold);
   empty → keep all. Tier 1 in `tests/unit/core/location/test_resolve_places.py`
   (Rampur HP/UP → HP; unknown part → still ambiguous). Reuses the narrowing
   shape already there for `region`.
2. **Region narrows a long list** — tier-1 test only, pinning that the
   existing region step in `_resolve_named` runs before any listing.
3. **Group a long list** — `core/channel/service.py`: over 5 candidates → a
   helper that groups them by the first differing chain part and lists up to
   5 groups (reuse `_standout_part`'s walk). New `ClarificationText` field for
   the header ("{name} is in several places. Which one:"), text in
   `config/defaults/clarification-text.yaml`. Tier 1 in
   `tests/unit/core/channel/test_service.py`.
4. **Place label** — `core/channel/prompt.py::_place_label` says
   "about Rampur, Himachal Pradesh" (name + the last part of `within`) instead
   of "about Rampur". Tier 1 in `tests/unit/core/channel/test_prompt.py`.
5. **Partial-turn question** — `orchestration/orchestrator.py`: after the
   composed claims, if any ask is `AmbiguousPlace`, yield one more `Claim`
   with the fixed block and include it in `TurnFinished.content`. Needs a core
   helper returning that block for the ambiguous asks only (reuse
   `_candidate_lines`). Tier 1 for the helper; tier 3 in
   `tests/integration/orchestration/test_orchestrator.py` ("Pune and
   Aurangabad" → answer + question block, status `partially_answered`).
6. **Prompt** — `core/intent/service.py::build_intent_prompt`: one worked
   example of the reply turn (use a name other than the live test's), plus the
   exception sentence from decision 3. Tier 1 in
   `tests/unit/core/intent/test_service.py`.
7. **Docs** — new `docs/ADR/0015-ask-which-place-statelessly.md` (the
   dependency the story names); `docs/DSS_ARCHITECTURE.md` place section;
   ADR-0014 pointer; `TODO.md`: remove the #131 gap entry.

## Verification
- `uv run ruff check . && uv run ruff format --check . && uv run pytest`
  (offline, tiers 1–3).
- **Tier 5, new tests in `tests/llm/structural/test_intent_place_name.py`:**
  reply by state ("Himachal"), by number ("2"), by "the one in X", by a short
  or loose form ("Rampur himachal", "HP" — the model must copy the full listed
  line, since code only matches the part exactly); a pick from the
  grouped long list ("Uttar Pradesh"); a reply in another language or script;
  a new question after our list is classified as new; a follow-up after the
  pick ("and tomorrow?") carries the picked place (likely the hardest — the
  history then holds a bare "Bilaspur"); Vashi/Lasalgaon still stay null
  (the new exception must not leak). Run the **whole file on Gemma
  and luna**; all must pass (today's lesson: any prompt line can break
  splitting).
- **Manual (acceptance: "a product owner can…"):** local run per
  `docs/RUNNING.md`; POST "weather in Rampur", then POST "Himachal" with the
  first exchange as `history`; the answer names "Rampur, Himachal Pradesh".

## Risks
- The model mis-copies the line → caught by decision 5 (asked again) and
  measured by tier 5.
- The Experience layer must send our assistant message back verbatim in
  history (the story lists this as a dependency to confirm with that team).
- A "Name, Part" whose part fits no match but is itself a place is split into
  two places, to undo a model joining "Pune and Mumbai" into "Pune, Mumbai".
  Side effect: "Ashti, Nagpur" (meaning the Ashti near Nagpur, none inside
  it) also answers Nagpur. Same for a part like "Bihar", which is also a
  block name. Not a silent wrong place: the answer names each place.
