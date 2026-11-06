# ADR-0022: Settle Each Ask Before Serving It

- **Status:** PROPOSED
- **Date:** 2026-11-05
- **Deciders:** DSS implementation

---

## 1. Context and Problem Statement

Some asks need facts the farmer did not give. A grievance needs the
scheme and whether to raise or check a complaint. Today only the place
is asked for, by its own code path. So "my payment did not come" is
asked for a place it does not need, then fails.

## 2. Decision Drivers

1. A new pack or form works with no code change.
2. Never ask for what the ask does not need.
3. No stored state.
4. No extra model call.

## 3. Considered Options

1. **Slot filling.** Code knows which facts an ask needs. The model
   only reads them from the farmer's words.
2. **The agent asks**, through a tool. The model decides.
3. **Intent writes the question.**
4. **A fixed flow per use case.**

## 4. Decision Outcome

Chosen option: **1.**

- Each fact is a slot: action, scheme, place. Which ones an ask needs
  comes from its pack (place) and the form registry (scheme, action).
- Intent returns each value and whether it is sure.
- Right after intent, each ask is settled: every slot filled, or we
  know we cannot serve it.
- If not settled, the turn ends with one question, for the first
  missing slot: action, then scheme, then place.
- The scheme is never listed. An unsure match is confirmed. No match
  is asked again.
- The action is asked when unclear, never guessed. A guess risks a
  duplicate complaint or a status check with no ticket.
- The history holds our question, as in ADR-0017.
- Intent examples per category live in `configs/intent-examples.yaml`,
  at most 5 each (`DSS_INTENT_EXAMPLES_MAX`), checked at boot.

Option 2 cannot promise it never asks for a place, and calls a model
every turn. Option 3 cannot see what a pack needs. Option 4 needs
code for every use case.

## 5. Consequences

- One rule replaces the place question path.
- A vague grievance takes three turns: action, scheme, form.
- If nobody serves the category, the farmer may answer a question for
  nothing. Rare.
- A new kind of slot still needs code once. A new pack using existing
  slots does not.
