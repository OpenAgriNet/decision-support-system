# Spec: Farmer submits the form, the DSS files it and reports the ticket (#205, task 3)

Story: https://github.com/OpenAgriNet/engineering-tracker/issues/205
PRD: `transactions-file-a-complaint-or-check-a-scheme-status-through-the-assistant.md`
Builds on: `docs/.agent/specs/205-farmer-asks-form-offered.md`. On `main` already: the registry from the API packs and `TextPath(forms=)` (#133), discovery indexing API packs under the `@type`'s last word and the pack's `interaction_type`, so a grievance ask finds the desks. In review: #135 (form step), #136 (form on the wire).

## Architecture / approach

**A submission is the second input kind.** `FormSubmission(TurnInput)` arrives as a `form` item in the current user message and runs on `FormPath`, wired in the path table beside `TextPath`. The boot check already refuses a kind without a path.

**The path, in order.**

1. **Validate raw values** against the registry's form for the submission's `context`: unknown context, unknown or repeated field, missing required, pattern, length, option. Pure, before any sink sees a value. Errors end the turn as `requires_input` with cause `invalid_input`, a text block and a `FormErrorBlock`: field names, labels and messages, never values. No model call.
2. **Record references only.** The audit record and the trace get `formId`, `context`, `submissionId` and the field names. Values live in the process for the turn and leave it only inside the provider call. Field tagging by the pack's `x-oan-pii` mark is its own later story; until then this rule keeps values out of every sink.
3. **Moderate the free text.** One `moderate` call over the pack's `freetext` fields (the complaint), through the same policy pack as a question. A rejection refuses the turn, nothing is filed. Fails closed, like a question. Moderation has no scope check; scope is intent's, which this path never runs.
4. **One ask, discovery, the scheme filter.** The path builds an `Intent` of one `Ask` from the context: category from `context.category`, interaction from the action (`support` is `ACT`, `status` is `OBSERVE`), scheme from the catalog by code. Discovery runs as on any turn; the DSS stays stateless, so nothing is cached between the offer and the submission. Discovery may return several desks under `openagrinet:Grievance`, one per scheme. Each capability carries the catalog's attributes in `advertised`, so the path keeps only the candidates whose advertised `scheme.code` equals the ask's, in code, before the model sees any. The provider ranking (ADR-0019) then orders what is left. Follow-up, not this slice: send the scheme code as a `/discover` filter too, the path is filterable.
5. **Planner and the `support` tool.** The planner runs with the submission and its `ActionSchema` on its deps. The ask line reads "files the farmer's form". `describe_capability` is reused: it learns to render an API-pack candidate from its `ActionSchema`, provider name and `resource_id` with "files the form's values", where today it skips any candidate without a `select` schema. A tool per pack action, `support` now, `status` with #206. The model's only decision is which desk, among those left; the tool builds the whole body in code from the `ActionSchema`: `FIXED` fields from the pack, `PROVIDER` fields from the chosen capability, `FARMER` fields from the submission, plus `@context` and `@type` from the pack's `x-jsonld`. The attributes become `message.support.channels[0]`, the container the pack names. The call carries the turn's `transactionId`. Real values are revealed in the request body only, as `select` does. **Retries like `select`**: transient failures, same attempts and backoff. Accepted risk, recorded: a slow desk can receive the same grievance twice until the pack gives a duplicate key.
6. **Evidence and reply.** What comes back (`case.ticketNo`, `case.status.code`, `case.filedOn`, the fields the pack lists under `comesBack`) is a `Result` on the ask, so the composer writes the confirmation: the ticket number verbatim, the filing date, keep the ticket and the phone you gave. The composer prompt says: never repeat a value the farmer typed. A failed call is a `Failure`; the composer says the complaint was not filed and can be tried again later, with no provider text.

**Three ways a submission turn ends.** One rule for every UI: a reply that carries a `form_error` item means "this form is still yours".

| | status | cause | content | error | the UI |
|---|---|---|---|---|---|
| bad values | `requires_input` | `invalid_input` | text, `form_error` with the bad fields | none | reopens the form with the errors; the farmer fixes and resubmits, new `submissionId` |
| desk failed | `unavailable` | `provider_unavailable` or `provider_rejected` | text, `form_error` with no fields | `retryable` true for transient, false for rejected; `retryAfterSeconds` only when the desk sent `Retry-After` | keeps the form, submit stays enabled; the farmer decides when |
| filed | `answered` | none | text | none | closes the form |

No invented retry delay: only the desk knows when it is back, and the DSS has already retried the quick cases within the turn.

**History carries references.** `ConversationMessage` gains an open slot, `reference: MessageReference | None`, with `FormReference(form_id, context, submission_id)` in `core/forms`. The wire reads `form` and `form_error` history items into it and ignores their `fields`. Intent, moderation and the composer render such a message as one fixed line, "a form was offered" or "a form was submitted", never field names or values.

**A question while a form is open** runs the text path as today; the form is dropped. "Try again" typed as text is a new grievance ask and offers a fresh, empty form: the DSS holds no values, so it cannot refill. A current message with both a `text` and a `form` item is a 422: one kind per turn.

**Why the planner, not a code-only step.** New packs must file with no code change. The `ActionSchema` gives that either way; the planner keeps one story, "the planner calls providers through tools the skill lists", and a tool per action means `status` and a future `init` are tools, not paths. Cost: one model round-trip to decide what the pack already decided.

**Rejected.**
- Validating after redaction. A tagged phone fails its own pattern.
- A DSS-side cache of the catalog between turns. The epic forbids DSS transaction state.
- A fixed `retryAfterSeconds`. Nobody but the desk knows it.
- Text only on a failed filing. Every UI would have to infer "keep the form" from an outcome that also occurs with no form at all.

## Data model

### `core/shared/models.py`
- `TurnInput` gains its second subclass; `FormSubmission` itself lives in `core/forms/models.py`: `form_id`, `context: FormContext`, `submission_id`, `values: tuple[FieldValue, ...]`. A tuple, not a dict, so a repeated field survives to be reported.
- `ConversationMessage.reference: MessageReference | None = None`. `MessageReference` is an open frozen base in shared, like `TurnInput` and `OutputBlock`.
- `Cause.INVALID_INPUT = "invalid_input"`, `Cause.PROVIDER_REJECTED = "provider_rejected"`.

### `core/forms/models.py`
- `FieldValue(name, value: str)`.
- `FormReference(MessageReference)`: `form_id`, `context`, `submission_id: str | None`.
- `FieldErrorCode`: `REQUIRED`, `PATTERN`, `MIN_LENGTH`, `MAX_LENGTH`, `NOT_AN_OPTION`, `DUPLICATE`, `UNKNOWN_FIELD`. `FieldError(name, code, message)`; the code is for metrics, the message for the farmer, English, localised on the way out.
- `FormErrors(form_id, submission_id, field_errors, unknown_form: bool)`, never empty. `ValidSubmission(definition, submission_id, values: Mapping[str, str])`.
- `FormErrorBlock(OutputBlock)`: `form_id`, `context`, `submission_id`, `fields: tuple[FieldErrorEntry(name, label, error), ...]`, empty when the desk failed.
- `ActionSchema` stays as `main` defines it. The registry keeps it: `FormRegistry.action_schema(context) -> ActionSchema | None`; today `build_form_registry` throws it away after cutting the form.

### `core/forms/service.py`
- `validate_submission(submission, forms: FormLookup) -> ValidSubmission | FormErrors`.
- `form_error_block(errors, definition) -> FormErrorBlock`.
- `build_action_attributes(schema, capability, values) -> dict`: the body the tool sends, from the three sources plus `@context`/`@type`.
- `candidates_for_scheme(candidates, scheme_code)`: the scheme filter over `advertised`.

### Planner
- `PlannerDeps.submission: ValidSubmission | None`, `PlannerDeps.action_schemas: Mapping[str, ActionSchema]` by `@type` for `describe_capability`.
- `Evidence` unchanged: the reply is a `Result`, the failure a `Failure` with `capability` set.

### Messages
- `clarification-text.yaml`: one message per `FieldErrorCode`, `str.format` templates with `{label}`, `{min}`, `{max}`; `not_filed` and `not_filed_rejected`.

## API contracts

### Wire in (`adapters/http/v1`)
- `InputMessage.content` discriminated union gains `FormContent(type="form", form_id, context, submission_id, fields: [{name, value}])` and `FormErrorContent(type="form_error", form_id, context, submission_id, fields)`.
- A `form` item in the current user message is a submission. `submissionId` is required there; missing is a 422. In history, `form` and `form_error` items map to `FormReference`; their `fields` are ignored.
- A current message with more than one item kind is a 422 from the mapping.

### Wire out
- `form_error` item: `formId`, `context`, `submissionId`, `fields: [{name, label, error}]`, `fields` empty on a failed filing. Statuses and causes as in the table above. `error.retryAfterSeconds` only from the desk's `Retry-After`.
- Filed: `text` only, status `answered`.

### Ports
- `CapabilityInvocation.support(capability, attributes, transaction_id, *, visibility) -> ActionReply(attributes: Mapping, retry_after_seconds: int | None)`: the `on_support` attributes, concealed, and the desk's `Retry-After` if any. `SupportFailedError(capability, status_code, failure_class, retry_after_seconds)`, no provider text. `_UnwiredInvocation` mirrors it. `NetworkAction.SUPPORT`, endpoint `/support` <!-- inferred — verify with network-specs: the README never names the path -->.

### Prompts
- Planner: a generic `provider-action` skill listing `support` and `describe_capability`, with the rule "an ask that files a form is answered by the `support` tool with the form's values; describe the candidates, pick one desk, call it once". Composer: the ticket number verbatim, never a value the farmer typed, "not filed, try again later" for a filing failure. Both languages.

## Edge cases

- Unknown `context`: text only, "I do not know that form", `requires_input`, `invalid_input`, no `form_error` block.
- Known `context`, unknown `formId`: accepted. Old history may be trimmed.
- Repeated or unknown field name: listed in the `form_error` block by name with an empty label.
- Several errors on one field: the first in rule order (required, pattern, length, option).
- Resubmission after errors or after a failed filing: a new `submissionId`, same `formId`.
- Several desks for the ask: only those advertising the ask's scheme code remain; none left is the unserved message, `no_match`, nothing filed.
- A farmer question while a form is open: text path, form dropped, no reminder.
- Both `text` and `form` in the current message: 422.
- `maySend` fields (`grievance.category.code`, catalog options): not sent in this slice.

## Failure modes

- Provider timeout or 5xx after the retries: `unavailable`, `provider_unavailable`, `retryable` true, "not filed, try again later", the form kept. The double filing a retry can cause is the accepted risk above.
- Provider 4xx: `unavailable`, `provider_rejected`, `retryable` false, same text, the form kept. The status code reaches the span, never the body.
- Moderation rejects the complaint: `rejected` with the refusal text, nothing filed.
- Moderation model down: fails closed, `rejected`, `moderation_unavailable`, as for a question.
- Network unwired: the unserved path; the tool is never reached.
- A `FormSubmission` kind with no path: refused at boot.

## Implementation tasks

1. **Models, validation, the registry keeps the ActionSchema**
   - [ ] Given a submission with a phone that is not ten digits starting 6 to 9, when validated, then the phone field carries `PATTERN` and nothing else is reported.
   - [ ] Given a repeated field name, when validated, then it is reported as `DUPLICATE` with no value.
   - [ ] Given an unknown context, when validated, then `FormErrors.unknown_form` is true.
   - [ ] Given the PMFBY pack, when the registry is built, then `action_schema(PMFBY support)` returns the leaf's `mustSend` with its sources.
2. **Wire in: the submission and the history references**
   - [ ] Given a `form` item in the current message, when mapped, then the turn's input is a `FormSubmission` with its values.
   - [ ] Given a `form` item in history carrying `fields`, when mapped, then the message's reference holds `formId`, `context`, `submissionId` only.
   - [ ] Given a current message with a `text` and a `form` item, when posted, then 422.
3. **FormPath: validation exit and references in the record**
   - [ ] Given invalid values, when the turn runs, then it ends `requires_input`, `invalid_input`, with text and a `form_error` block, and no model or provider call.
   - [ ] Given any submission, when recorded, then the audit record and the trace hold names and references, never a value.
4. **Moderation of the free text**
   - [ ] Given a complaint the policy pack rejects, when the turn runs, then it is `rejected` and nothing is filed.
5. **The ask, discovery and the scheme filter**
   - [ ] Given two grievance desks advertising different scheme codes, when the submission is for PMFBY, then only the PMFBY desk reaches the planner.
6. **The `support` tool, port and adapter**
   - [ ] Given a valid submission and a desk, when the planner runs, then `describe_capability` lists the desk and the `support` tool sends a body with every `mustSend` path filled from the pack, the desk and the form, and the real phone appears in the request body only.
   - [ ] Given a transient failure that persists, when the tool runs, then the attempts and backoff match `select` and a `Failure` lands on the ask with the desk's `Retry-After`, if any.
7. **Reply and status**
   - [ ] Given the desk returns a ticket, when the turn ends, then the composer's text names the ticket number and the filing date and never the phone, status `answered`, text only.
   - [ ] Given a failed filing, when the turn ends, then the text says not filed, try again later, status `unavailable` with the right cause, `retryable` set, a `form_error` with no fields, and `retryAfterSeconds` only if the desk sent one.
8. **Prompts skip references**
   - [ ] Given history with a form reference, when intent, moderation or the composer build a prompt, then the message shows as one fixed line.
9. **Wire out, docs**
   - [ ] Given a `form_error` block, when the reply is JSON or SSE, then it is a `form_error` item with names, labels and messages only.
   - [ ] Spec boxes, `DSS_ARCHITECTURE.md`, ADR-0004 amendment for `form_error`, `RUNNING.md`. #135 drops `NoForms` on rebase.

## Test strategy

- Tier 1: validation per rule, `build_action_attributes` against the PMFBY leaf, the scheme filter, `form_error_block`, history reference parsing, prompt rendering of a reference, the registry's `action_schema`.
- Tier 2: the invocation adapter's `support` request and response against the pack's example, status classes, retries, `Retry-After`; wire mapping in and out.
- Tier 3: `FormPath` with fakes: the validation exit, moderation reject, the scheme filter, the planner driving `describe_capability` and `support` through a `FunctionModel`, status from evidence; the boot check.
- Tier 5, once live: the composer names the ticket and not the phone.
- Tier 7: "file a PMFBY grievance" to a form, then a submission to a ticket, over HTTP against the mock network with a `/support` handler.
- Metrics: a `support` span like `dss.select`, counts of validation errors by code, no field value anywhere.

## Open questions

- A duplicate key for filings: the pack has no slot for `submissionId`. For network-specs; until then a retry can file twice.
- The `support` endpoint path, implied by Beckn convention only.
- Whether the ticket number is PII (#206 asks the same).
- Tagging form fields by the pack's PII mark: its own story.
- Sending the scheme code as a `/discover` filter: follow-up.
- The `pre_tool_call` checkpoint for tenant policies before a provider call: later.
