# Spec: Farmer asks, the DSS offers the form (#205, task 2)

Story: https://github.com/OpenAgriNet/engineering-tracker/issues/205
PRD: `transactions-file-a-complaint-or-check-a-scheme-status-through-the-assistant.md`
Design notes: `docs/exploration/form-flow.md`
Scope: task 2 of three. Task 1 (form registry) and task 3 (submission) build on the models this spec lands first.

## Architecture / approach

**A turn is an envelope with one input inside.** The envelope (`UserTurn`) is the same every turn. The input is one of several kinds: a sentence today, a form submission next, an OTP reply later.

**The orchestrator picks the path by the input's kind.** Today's pipeline becomes `TextPath`. Task 3 adds `FormPath`. A new kind is a new path, wired in the composition root. The orchestrator itself does not change.

**Each path owns its own redaction.** Text redaction runs detectors over sentences. Form redaction tags fields by name from the pack. They differ, so they sit inside their paths, not above the branch.

**History carries references, not forms.** A history item names a form by `formId` and `context`, plus `submissionId` for a submission or a rejection. No fields, no values. The DSS finds the definition in the registry by `context`. The UI may send the full block, and the DSS reads only those keys. If old history is trimmed, validation still works from `context`.

**What this task covers.** The farmer asks in words and the DSS replies with an empty form. Intent and discovery run as today. If a PMFBY grievance provider exists and the registry has a form for the ask, the DSS fetches the form and ends the turn `requires_input` with a text block and the form block. The LLM planner is skipped: there is no provider call to make yet. What happens when the farmer submits is task 3.

**Rejected:**
- `query: TextQuery | FormQuery` as a field-level union. Every reader must narrow it. A form reaching intent fails at runtime, not in types.
- A nullable `form` field on `UserTurn`. Allows neither and both.
- `Form` (offers plus submissions plus a rejected set) rebuilt from history. Nothing reads submissions or the rejected set. Dropped. The reference carries `submissionId`, so it can come back if a reader appears.
- `forms` map on `UserTurn`. Only the form path reads it, so it is built there, from history.

## Data model

### `core/shared/models.py`

- `TurnInput`: frozen base class. One subclass per kind.
- `TextQuery(TurnInput)`: one field, `text`. Replaces `original_query` and `enriched_query`. Enrichment has not landed, so `enriched_query` always mirrored the original; it comes back when enrichment ships.
- `UserTurn.input: SerializeAsAny[TurnInput]`. `original_query` and `enriched_query` are removed. The 9 readers move to `turn.input.text`. Tests build turns through the `a_turn` fixture in `tests/conftest.py`; the few that build a `UserTurn` by hand move onto it, so the next shape change is one edit.
- `FormContext`: `scheme`, `category`, `action`. The form's type. Sent out, copied back.
- `FormReference`: `form_id`, `context`, `submission_id: str | None`. What history carries. `None` means an offer.
- `ConversationMessage` gains `form: FormReference | None = None`. `text` stays required.
- `FieldKind`, `Option`, `FieldRules`, `FieldDefinition` (`name`, `label`, `kind`, `rules`, `pii`). They cross the wire inside `FormBlock`, so they are shared.
- `FormBlock`: `form_id`, `context`, `fields`.
- `OutputContent = TextBlock | RefusalBlock | FormBlock`.

### `core/forms/models.py`

- `FormDefinition`: `scheme`, `category`, `action`, `fields`. `context` property. `field(name)` lookup.

Dependency direction: `core/forms` imports `core/shared`. Never the reverse.

### Deferred to task 3

Everything a submission produces or consumes: `FormSubmission` (the second `TurnInput` kind), `FieldValue`, `FieldErrorCode`, `FieldError`, `FormErrors`, `ValidSubmission`, `FormErrorBlock` and `Cause.INVALID_INPUT`. `docs/exploration/form-flow.md` holds their draft shapes.

### `core/intent/models.py`

- `SubjectCategory.GRIEVANCE = "Grievance"`. Lands with the intent work, not the models PR, because it widens what the classifier may emit.

## API contracts

### Depends on (task 1)

`FormRegistry.find_for_ask(scheme, category, interaction) -> FormDefinition | None`. Proposed rule for picking the action: `ACT` maps to `support`, `OBSERVE` maps to `status`. Story #206 confirms it.

### Orchestration

- `TurnPath` protocol: `run(input, turn, *, ctx, recorder) -> AsyncGenerator[CoreEvent]`.
- `Orchestrator(*, paths: Mapping[type[TurnInput], TurnPath], ...)`. `run` looks up `type(turn.input)`.
- Boot check in the composition root: every `TurnInput` subclass has a path, or the app refuses to start.
- `TextPath`: today's pipeline, plus the form step after discovery.
- `offer_form(definition) -> ComposedAnswer` in `core/forms/service.py`: an intro `TextBlock` and a `FormBlock` with a new UUID `form_id`.

### Wire (`adapters/http/v1`)

- `InputMessage.content` accepts `form` and `form_error` items beside `text`, in history. The mapping reads `formId`, `context`, `submissionId` into `FormReference` and ignores `fields`. A `form` item in the current user message is a submission, which is task 3.
- New output block `form`: `formId`, `context`, `fields` with `required`, `pattern`, `minLength`, `maxLength`, `options` flattened onto each field. `pii` is never on the wire.
- On SSE the form block is one whole `claim.completed` piece. On JSON it is one item in `message.content`.
- `outcome.status` is `requires_input`, with no cause.

## Edge cases

- Intent gives `(PMFBY, Grievance, ACT)` and a provider exists, but the registry has no form: the turn carries on down today's path.
- Several asks in one turn, one of them a form ask: the form step runs for the first form ask only. The others go through the planner as today. Noted as a limit, not solved.
- A form is open in history and the farmer asks a plain question: today's path answers. The DSS does not resend the form or remind the farmer. The history reference is passed to prompts as one fixed line, "a form was offered", never as fields.
- The UI sends the full form block in history: the DSS reads only the three reference keys.
- Two offers of the same form type in one session: each has its own `form_id`.

## Failure modes

- No provider for `scheme.code = PMFBY`: `no_match`, text only, no form.
- A `TurnInput` subclass with no path: refused at boot, not on the first turn.
- Localizer fails on the intro text: English intro, form still sent, same as other system text.

## Implementation tasks

1. **Models PR: the shared contracts**
   - [ ] Given `UserTurn`, when built, then `input` is a `TurnInput` and `original_query` and `enriched_query` no longer exist.
   - [ ] Given a `TextQuery` on a turn, when the turn is serialised, then `text` appears, not an empty base.
   - [ ] Given `OutputContent`, when a `FormBlock` is used in a `Claim` or `TurnFinished`, then it validates.
   - [ ] Given `ConversationMessage`, when built with a `FormReference`, then `text` is still required and `form` holds the reference.
   - [ ] Given `FormDefinition`, when `field(name)` is called, then it returns the field or `None`.
   - [ ] Given the 9 readers and the tests, when `uv run pytest` runs, then it is green with `turn.input.text` and no behaviour change.
2. **Orchestrator branches by table**
   - [ ] Given a `TextQuery` turn, when `Orchestrator.run` runs, then `TextPath` handles it and output matches today's.
   - [ ] Given a `TurnInput` subclass with no path, when the app boots, then it refuses to start with a clear message.
3. **Intent learns Grievance**
   - [ ] Given "I want to file a PMFBY grievance", when intent runs, then the ask is `PMFBY`, `Grievance`, `ACT`.
4. **The form step on `TextPath`**
   - [ ] Given a PMFBY grievance ask with a provider and a registry form, when the turn runs, then it ends `requires_input` with a text block then a form block, and the planner LLM is not called.
   - [ ] Given the same ask with no provider, when the turn runs, then it is `no_match` with text only.
   - [ ] Given the same ask with a provider but no registry form, when the turn runs, then the planner runs as today.
5. **Wire: emit the form, accept references in history**
   - [ ] Given a form block, when the reply is JSON, then `message.content` holds `text` then `form` with camelCase fields and no `pii` key.
   - [ ] Given a form block, when the reply is SSE, then it arrives whole in `claim.completed`, never in a delta.
   - [ ] Given history with a `form` item carrying `fields`, when mapped, then `ConversationMessage.form` holds `formId`, `context`, `submissionId` only.
   - [ ] Given history with a `form_error` item, when mapped, then it is a `FormReference` with its `submissionId`.
6. **Prompts skip form references**
   - [ ] Given history with a form reference, when intent, moderation or the composer build a prompt, then the message shows as one fixed line, never field names.
7. **Docs**
   - [ ] `docs/DSS_ARCHITECTURE.md` describes the input kinds, how the path is picked, reference-only history, and the form block.
   - [ ] `CLAUDE.md` tech stack lists the new content type.

## Test strategy

- Tier 1: models, `offer_form`, reference parsing, prompt rendering of references.
- Tier 2: wire mapping in and out, SSE framing of the form block.
- Tier 3: the path table, the boot check, the form step on `TextPath` with mocked discovery and registry.
- Tier 7: one smoke turn, "file a PMFBY grievance" to a form block, recorded LLM.
- No eval change in this task.

## Open questions

- Who mints `submissionId`, and can the provider use it as a duplicate key.
- Whether `ApiDomainSchema` and today's `DomainSchema` merge (task 1).
- Whether the free-text complaint goes through moderation (task 3).
- Whether a failed filing is a `FormErrorBlock` or text (task 3).
- Whether `PROVIDER_REJECTED` is added to `Cause` (task 3).
