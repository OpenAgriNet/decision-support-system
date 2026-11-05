# Spec: Farmer with a scheme problem is guided to a grievance (#243)

Story: https://github.com/OpenAgriNet/engineering-tracker/issues/243
PRD: `transactions-file-a-complaint-or-check-a-scheme-status-through-the-assistant.md`

## Approach

**Infer the action, never ask it.** A problem with a scheme ("payment did
not come") is a wish to raise a grievance unless the farmer shows one is
filed. One generic prompt rule in the intent prompt; the intent types do
not change.

**Ask only for what the form needs.** After discovery, code checks each
form-backed ask for the pieces its form context needs and the ask lacks.
Today that is the scheme. The check walks the context's pieces; nothing
in code names Grievance or the scheme. The turn ends `requires_input`
with the composer's question.

**The composer writes the question.** This is the first system text to go
through the composer. The orchestrator hands it a line per ask, "this ask
needs the scheme", the same way it hands "this ask awaits a form". The
composer asks in the farmer's language. No choices are listed; the next
turn matches the farmer's answer against the catalog as it does today.
Later work moves the other fixed texts the same way, with the ADR-0018
revisit.

**Every interaction gets the form.** Advise joins act and observe in the
form lookup: "how do I file" gets the steps if a provider has them, and
the file form with them.

**The offer states the assumption.** When the file form is offered, the
composer adds one line: if you already filed, give the ticket number.
Next turn, a reply with a ticket or "already filed" is observe, so the
status form comes.

**Place.** The missing-piece stop runs before the place check, so a
form-backed ask never asks for a place.

**Rejected**
- Asking "file or check status?". Action names are ours; a farmer may not
  know which they need. Inferred instead, with a correction line.
- Listing schemes as choices. A catalog can hold a hundred.
- A history reference for the question. Needs a new wire item in the
  Experience API and the UI. The reply reads fine from the text.
- Fixed text through the localizer, like the place question. Chosen
  against so this story sets the composer pattern.

## Data model

- `FormRegistry`: a way to ask which contexts exist for a category, so
  code can tell a form-backed category without a scheme.
- Evidence per ask gains "missing pieces", beside `awaiting`. The ask line
  to the composer says which piece is missing.
- `form_offer._ACTION_BY_INTERACTION` maps advise to `support`. The
  reverse map is written out so `support` still reads back as act.

## API contracts

No wire change. The turn ends `requires_input` with one text block.

## Edge cases

- Scheme named, no form for that scheme and action in the registry: as
  today, the planner path; the ask may end unserved.
- Two asks, one missing a scheme, one answerable: both go to the composer
  in one reply, the question after the answer.
- Nobody serves the category: unserved reply, as today. The question is
  asked only when a provider serves the ask.
- Farmer answers the scheme question with a new question: intent treats it
  as new, as it does for the place question.
- Farmer names a scheme the catalog does not hold: scheme stays missing,
  the question is asked again.

## Failure modes

- Composer call fails on the question turn: the existing composer
  failure handling applies.
- Intent drops the grievance ask on the reply turn: the farmer's reply is
  classified fresh. One wasted turn. Covered by a prompt example and a
  tier-5 test.

## Implementation tasks

1. Intent prompt: problem description under a form-backed category is act;
   a reply to "which scheme" keeps the earlier ask. English and Hindi.
   - [ ] Given "payment did not come" with no filing sign, when classified, then the ask is Grievance/act.
   - [ ] Given our question "which scheme" in history and a reply naming one, when classified, then the ask is Grievance/act with that scheme.
   - [ ] Given a ticket number or "already filed" in the reply, when classified, then the ask is observe.
2. Registry: contexts by category.
   - [ ] Given forms for Grievance, when asked for the category, then its contexts are returned; an unknown category gives none.
3. Missing-piece check in `form_offer`, generic over the context's pieces.
   - [ ] Given a served form-backed ask with no scheme, when checked, then "scheme" is the missing piece.
   - [ ] Given the scheme is set, when checked, then nothing is missing.
4. Orchestrator stop before the place check; evidence with the missing
   piece; outcome `requires_input`.
   - [ ] Given a scheme problem without a scheme, when the turn runs, then no place question and the reply is a composed question.
5. Composer prompt: the missing-piece line and the "already filed" line
   on a file-form offer. English and Hindi.
   - [ ] Given a file form is offered, when composed, then the reply holds the correction line.
6. Advise in the form lookup.
   - [ ] Given "how do I file" for a scheme with a file form, when the turn runs, then the form is offered.
7. Architecture doc: the form step asks for missing pieces; first composer-written system text.

## Test strategy

- Tier 1: registry query, missing-piece check, advise mapping.
- Tier 3: orchestrator stop, evidence line, outcome, place skipped, advise
  offer.
- Tier 5: the three intent rules, structural only.
- Tier 7: one smoke, the three-turn flow with replayed responses.
