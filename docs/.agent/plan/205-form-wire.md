# #205 task 5: the form on the wire

## Context

Spec `docs/.agent/specs/205-farmer-asks-form-offered.md`, task 5. The text path can end a turn with a `FormBlock` (#135), but the HTTP mapping raises `TypeError` on any block that is not text or a refusal. This plan gives the form its wire shape, in JSON and in SSE, so a UI can render it.

Needs only `FormBlock` from `main` (#130). Branch `feat/205-form-wire` off `main`; independent of #133 and #135.

## Design

### The `form` content item

One new item type beside `text` and `refusal`, camelCase like the rest, `null` rules left out as every other frame does (`exclude_none`):

```json
{
  "type": "form",
  "formId": "8f3c…",
  "context": {"scheme": "PMFBY", "category": "Grievance", "action": "support"},
  "fields": [
    {"name": "applicantMobile", "label": "Applicant mobile", "kind": "text",
     "required": true, "pattern": "^[6-9][0-9]{9}$", "options": []},
    {"name": "season", "label": "Season", "kind": "select", "required": true,
     "options": [{"value": "Kharif", "label": "Kharif"}, {"value": "Rabi", "label": "Rabi"}]}
  ]
}
```

- `kind` is one of `text`, `textarea`, `select`, `secret`.
- Rules are flattened onto the field: `required`, `pattern`, `minLength`, `maxLength`, `options`. No `rules` object on the wire; the UI reads a field as one flat thing.
- Nothing about PII goes out. `FieldDefinition` has no such field yet, and when it gains one the wire model simply does not carry it.
- `context` is echoed back verbatim by a submission (task 3), so it goes out exactly as the registry keyed it.

### Where it appears

- **SSE.** The orchestrator claims the form whole, so `to_claim_frame` already emits one `claim.completed` with the form as its single item once `_block` knows the type. Never a `claim.delta`.
- **JSON.** One item in `message.content`, after the text, from `to_terminal_frame`.
- `outcome.status` is `requires_input`, `cause` is `null`, unchanged.

### `src/dss/adapters/http/v1/schema.py`

- `OutputFormOption(value, label)`, `OutputFormField(name, label, kind, required, pattern, min_length, max_length, options)`, `OutputFormContext(scheme, category, action)`, `OutputForm(type="form", form_id, context, fields)`, all `_WireOut`.
- `ResponseMessage.content` union gains `OutputForm`.

### `src/dss/adapters/http/v1/mapping.py`

- `_block` gains `case FormBlock(): return _form(content)`. The `TypeError` for an unknown kind stays; the TODO goes.
- `_form(block) -> schema.OutputForm` and `_form_field(definition) -> schema.OutputFormField`, flattening the rules.

### Not in this plan

- **Inbound history references** (`form` and `form_error` items in `message.input`, read as `formId`, `context`, `submissionId`). The spec's data-model section already defers `FormReference` and the message slot to task 3, because validation is the first reader. The task 5 bullets that mention them move there too. Decision to confirm.
- **The tier-7 turn over HTTP** with a live model: blocked until discovery indexes API packs and #133 is in. The route tests below drive the real app with a fake runner, which is this repo's pattern for the transport.
- Form labels in the farmer's language: open question in the spec, own story.

## Steps (TDD, one test at a time, user reviews before each commit)

1. Tier 2 `tests/unit/adapters/http/v1/test_response_mapping.py`: a form block maps to a `form` item with camelCase keys, flattened rules, options, and no `rules` or `pii` key; a field with no pattern has no `pattern` key once dumped. **Commit 1**: `feat: the form block has a wire shape [#205]`.
2. Tier 3 `tests/integration/entrypoint/test_turn_route.py`, with `FakeRunner` yielding text deltas, a text claim, a form claim and the finished turn: on SSE the form arrives whole in one `claim.completed` and in no delta; on JSON `message.content` is text then form; both modes describe the same turn. **Commit 2**: `test: a form reaches the client whole in both modes [#205]`.
3. Docs: `docs/RUNNING.md` gains the form reply beside the refusal example; `docs/DSS_ARCHITECTURE.md` interfaces section names the item; `CLAUDE.md` tech stack line lists `form` beside `claim.delta` and `claim.completed`; spec task 5 boxes for the outbound items ticked, the history bullets moved to task 3. ADR-0004, the envelope, gets a dated amendment line naming the new item, or the architecture doc alone: decision to confirm. **Commit 3**: `docs: the form content item [#205]`.

## Verification

- `uv run pytest` green, `uv run ruff check src` clean, `uv run ruff format --check .`.
- `uv run uvicorn --factory dss.entrypoint.app:create_app --port 8077` and a text turn still answers as before. The form path stays unreachable in production until #133 and the discovery indexing land.

Time: about 1.5 hours with review rounds.
