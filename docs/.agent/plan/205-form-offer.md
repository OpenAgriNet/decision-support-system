# #205 task 4: the form step on TextPath

## Context

Spec `docs/.agent/specs/205-farmer-asks-form-offered.md`, task 4. Intent can now say "a grievance about PMFBY, to be filed" (#134), the orchestrator runs a text turn on `TextPath` (#132), and `core/forms` holds `FormDefinition`, `FormBlock` and the `FormLookup` seam (#130). This plan makes the DSS answer such an ask with an empty form.

Decided 27 Oct, reversing two spec lines:

- **The form is added to the answer, never instead of it.** "What's the weather, and file my PMFBY grievance" gets the weather answer, then the form. The spec's "first form ask only, others go through the planner, noted as a limit" becomes this rule.
- **A form is offered only for a served ask.** Discovery found a provider at that ask's position and the registry has a form for its context. Fail either and the ask is handled as today: unserved message, or a normal planner call.
- The spec's "the form arrives in `claim.completed`" stands, and now has a reason: the form rides the same answer stream as the composed text.

Needs #132 and #134 merged. Branch `feat/205-form-offer` off `main`.

## Design

### Shape of a turn with a form

1. Intent, enrichment, moderation, discovery: as today.
2. **Form step.** For each ask, in order: served at its position, `form_context_for(ask)` is not `None`, `lookup.find(context)` returns a definition. Each hit is composed by `core/forms/service.py::compose_form_offer` into the reply's blocks.
3. Form asks leave the planner's view: `without_asks(intent, discovery, positions)` drops those asks and renumbers `answers`, `capabilities`, `failures` and `priorities` so positions stay aligned. The planner and composer never learn a grievance was asked.
4. If asks remain: planner and composer run on the trimmed pair, streaming as today. Then the intro is appended as the last piece of the same text, the way the place question is today, and the text block is claimed.
5. If no asks remain: no planner, no composer. The intro is the only piece; it is claimed as one `TextBlock`.
6. Each form block is appended as its own whole claim. `TurnFinished` holds text then forms. Status `requires_input`, no cause. Without a form, today's `_status_for` applies.

Status is per turn: "weather answered, form offered" reports `requires_input`. A per-ask outcome is out of scope.

### `core/forms/service.py` (new): the form composer

- `compose_form_offer(definition: FormDefinition, *, intro: str) -> ComposedAnswer`: an intro `TextBlock` and a `FormBlock` with a fresh `uuid4().hex` as `form_id`. Forms owns how a form is offered; the step only decides when. No model call: the form is fixed data, and the intro is system text from the clarification file, localised on the way out like the rest.

### `src/dss/orchestration/form_offer.py` (new, the step module beside `redaction.py` and `discovery.py`)

- `form_context_for(ask: Ask) -> FormContext | None`. `None` without `ask.scheme` or for `ADVISE`. Otherwise `FormContext(scheme=ask.scheme.code, category=ask.subject_categories.value, action=_ACTION[ask.interaction_type])` with `ACT → "support"`, `OBSERVE → "status"`. Forms never imports intent, so the mapping lives here.
- `find_forms(intent, discovery, lookup) -> tuple[FormOffer, ...]` where `FormOffer(position: int, definition: FormDefinition, scheme_name: str)` is a frozen dataclass. Served means `discovery.capabilities[i]` or `discovery.answers[i]` is non-empty, the same test `_nobody_serves` makes per ask. The step hands each offer to `compose_form_offer`.
- `without_asks(intent, discovery, positions) -> tuple[Intent, DiscoveryResult]`? No: a frozen dataclass `PlannerView(intent, discovery)`, per the no-positional-tuples rule.

### `core/channel/models.py` and `src/dss/config/defaults/clarification-text.yaml`

- `ClarificationText.form_offer: str`, a `str.format` template with `{scheme}`: "Please fill in the form below for your {scheme} grievance." Rendered in the target language by the localizer like the other system text. The scheme's name comes from `ask.scheme.name`.

### `src/dss/orchestration/answer_stream.py`

- `add_block(block) -> Claim`: a block that was not streamed. Asserts no pieces are pending, appends the claim, returns it. `claim()` keeps its text-only contract.

### Readers that assumed every block has text

- `core/channel/localize.py::localize_answer`: localises `TextBlock` and `RefusalBlock`, passes other blocks through.
- `orchestration/translation.py::translate_out`: streams a translation for text blocks, `add_block` for others.
- `orchestration/orchestrator.py::_reveal_to_farmer`, the `Claim` branch: `claim()` for text blocks, `add_block` for others. `_reveal_finished` already passes other blocks through.
- `tests/support/turn_events.py::assert_well_formed`: a claim whose block has no text asserts no pending deltas instead.

### `TextPath`

- New keyword `form_lookup: FormLookup`. The four test builders and composition pass it.
- Composition wires `NoForms` from `core/forms/registry.py`, a lookup whose `find` returns `None`, with `# TODO(#205): the registry built from the packs replaces this.` Production behaviour is unchanged until the registry lands, which also keeps the wire's `TypeError` on a form block unreachable until task 5.
- `_run_core` gains the step between `_nobody_serves` and the planner, and the two append points above.

### Not in this plan

- The wire (`form` output item, SSE framing): task 5, next plan.
- A trace stage for the step. It is a lookup, no model call; stage names are a dashboard contract.
- Prompts skipping form references in history: task 6.

## Steps (TDD, one test at a time, user reviews before each commit)

1. `tests/integration/orchestration/test_form_offer.py` (new; orchestration tests live in the tier-3 folder even when pure): `form_context_for` maps `ACT` to `support`, `OBSERVE` to `status`, `ADVISE` and no scheme to `None`. **Commit 1**: `feat: the form context an ask names [#205]`.
2. `without_asks` renumbers all four discovery dicts and keeps the others' order. **Commit 2**: `feat: take form asks out of the planner's view [#205]`.
3. Tier 1, `tests/unit/core/forms/test_service.py`: `compose_form_offer` yields an intro text block then a form block with a fresh id. **Commit 3**: `feat: compose the form offer [#205]`.
4. `AnswerStream.add_block` unit test; `assert_well_formed` learns blocks without text. **Commit 4**: `feat: the answer stream carries a whole block [#205]`.
5. Tier 1: `localize_answer` leaves a form block alone. Tier 3 (`test_orchestrator_translation.py`): a translated turn carries a form block whole. **Commit 5**: `fix: readers of a reply skip blocks without text [#205]`.
6. Tier 3, same file (reusing `_build` from `test_orchestrator.py` with a `form_lookup` kwarg):
   - a served PMFBY grievance ask with a registry form ends `requires_input` with text then form, and the planner is not called;
   - weather plus grievance: the planner sees one ask, the composed text comes first, the form last;
   - the same ask with no provider is the unserved message, no form, lookup not asked;
   - a provider but no registry form runs the planner as today.
   **Commit 6**: `feat: offer the form for a served ask the registry knows [#205]`.
7. `ClarificationText.form_offer`, yaml, localizer test for the intro. Folded into step 6 if small.
8. Docs: spec edge cases, failure modes and task 4 boxes; `docs/DSS_ARCHITECTURE.md` §5.1 one paragraph on the form step. **Commit 7**: `docs: record the form step [#205]`.

## Verification

- `uv run pytest` green, `uv run ruff check src` clean, `uv run ruff format --check .`.
- `uv run pytest tests/unit/test_core_isolation.py`: `core/forms` and `core/channel` import no orchestration.
- Local run with the stub LLM: a text turn still answers as before. The form path is unreachable until the registry lands, by design.

Time: about 3 hours with review rounds.
