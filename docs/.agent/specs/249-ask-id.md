# Spec: Refactoring: name an ask by its id, not its position

Story: OpenAgriNet/engineering-tracker#249
PRD: none

## Architecture / approach

An ask gets an `id` once, when intent builds it. Every step names the ask by
that id. A step hands on the ask together with what it found.

- `Ask.id`: a required whole number. It is the ask's first position (0, 1, 2)
  and never changes. `Intent` rejects two asks with the same id.
- `AskSettlement`: an ask with its settlement. `settle_asks` returns one list
  of these. `TurnResult` holds that list in place of two lists.
- Discovery and the planner still get an `Intent` of the settled asks only.
  The asks keep their ids, so nothing is numbered again.
- `ask_index` becomes `ask_id` everywhere, including the planner tool
  parameter the model fills in.
- `merge_unsettled` looks up each settled ask's evidence by id.

Considered and rejected:
- **The ask object as the key.** An ask is equal by value, so two identical
  asks would share one key. The planner model also cannot send an object.
- **Send all asks, plus a "skip these" set.** Every stage would have to
  know what "unsettled" means.
- **Pass `(number, ask)` pairs.** Changes every stage and port signature.
- **One object per ask after discovery.** Wanted, but it moves decision
  rules. It is the next story, stacked on this one.

## Data model

- `Ask` (`core/intent/models.py`): new required field `id: int`.
- `Intent`: validator, ids must be unique.
- `AskSettlement` (`core/settlement/models.py`): `ask`, `settlement`.
- `TurnResult` (`orchestration/turn.py`): `settlements` holds
  `AskSettlement`s. `settled_intent` filters them, no `zip`.
- `DiscoveryResult`: `answers`, `capabilities`, `failures`, `priorities`
  keyed by ask id. `serves(ask_id)`.
- `Result`, `Failure`, `AskPriority`, `AskDiscoveryFailed`, `AskUnservable`:
  `ask_index` → `ask_id`.
- `FORM_ASK_INDEX` → `FORM_ASK_ID`. The form path's one ask has id 0.

## API contracts

No change to `POST /v1/turns`.

Two inside names change:
- The planner tools `select` and `select_next` take `ask_id`. The prompt
  lists asks as `ask <id>`.
- Log events that carry `ask_index` now carry `ask_id`. No dashboard reads
  it today.

## Edge cases

- Ids with a gap (asks 0 and 2 settled, 1 not): the planner prompt shows
  "ask 0" and "ask 2", and `select(ask_id=2)` reads ask 2's data.
- Unsettled ask first, settled ask second: the result carries id 1 and the
  reply keeps the farmer's order.
- Every ask settled: no merge, same reply as today.
- No ask settled: discovery gets an empty intent, as today.
- The form path builds one ask with id 0.

## Failure modes

- Two asks with one id: `Intent` raises at build.
- A result, failure or form naming an id the turn does not have:
  `group_by_ask` raises `ValueError`, as it does today for a stray position.
- The model sends an id that is not listed: the tool returns the same
  "not one of this ask's candidates" text as today, and the model retries.
- A settled ask with no evidence at merge: `ValueError`.

## Implementation tasks

1. Add the `an_ask` test factory
   - [ ] Given a test needs an ask, when it calls `an_ask()`, then it gets a valid ask with defaults it can override.
   - [ ] Given the 10 local `_ask` helpers and direct `Ask(...)` calls in tests, when moved to the factory, then the suite passes with no change under `src/`.
2. Give `Ask` a required `id`
   - [ ] Given a message with three asks, when intent builds them, then their ids are 0, 1, 2 in the farmer's order.
   - [ ] Given two asks with the same id, when an `Intent` is built, then it is rejected.
   - [ ] Given a form submission, when its ask is built, then its id is 0.
3. Pair each ask with its settlement
   - [ ] Given an intent, when `settle_asks` runs, then it returns one `AskSettlement` per ask, in order.
   - [ ] Given a `TurnResult`, when `settled_intent` is read, then it holds only the settled asks, each with its first id.
4. Key discovery on the ask id
   - [ ] Given settled asks with ids 0 and 2, when discovery runs, then its answers, capabilities, failures and priorities are keyed 0 and 2.
   - [ ] Given a discovery failure or an unserved ask, when its event is made, then it carries that ask's id.
5. Key the planner, results and forms on the ask id
   - [ ] Given settled asks with ids 0 and 2, when the planner prompt is built, then it lists "ask 0" and "ask 2".
   - [ ] Given `select(ask_id=2)`, when it runs, then it reads ask 2's candidates and its result carries id 2.
   - [ ] Given a form found for the ask with id 2, when evidence is grouped, then that ask awaits the form.
   - [ ] Given a part naming an id no ask has, when evidence is grouped, then it raises `ValueError`.
6. Merge by lookup
   - [ ] Given asks [unsettled, settled], when evidence is merged, then the settled ask has its own results and the unsettled ask has its settlement, in the farmer's order.
   - [ ] Given a settled ask with no evidence, when merged, then it raises `ValueError`.
7. Docs
   - [ ] Given `TODO.md`, when this ships, then the three done items are removed ("Pass the ask itself", "`ask_index` is wrong after the merge", "An ask and its settlement are matched by position").
   - [ ] Given `docs/DSS_ARCHITECTURE.md` and any ADR text that says `ask_index`, when this ships, then accepted ADRs stay as written and the architecture doc uses the new name.

## Test strategy

- Tier 1 (most): `Intent` validation, `settle_asks`, discovery keying,
  `group_by_ask`, `merge_unsettled`, the planner prompt.
- Tier 3: one turn with a gap in the ids, through the real planner wiring
  with a scripted model. This is the seam test.
- Tier 5: run the existing structural tests on the planner tools once with
  a real model, to see that it fills `ask_id` as well as it filled
  `ask_index`. Not a merge gate.
- No new e2e test. The existing ones must pass unchanged.
