# #205 task 4: the form step on TextPath

## Context

Spec `docs/.agent/specs/205-farmer-asks-form-offered.md`, task 4. Intent can say "a grievance about PMFBY, to be filed" (#134), a text turn runs on `TextPath` (#132), and `core/forms` holds `FormDefinition`, `FormBlock` and the `FormLookup` seam (#130). This plan makes the DSS answer such an ask with an empty form.

Decided 27 Oct, after review:

- **A form offer is a finding in `Evidence`, like a result or a failure.** The form step decides before the planner which asks await a form. The planner records them and calls no provider for them. The composer writes the referral line in its own prose. The orchestrator appends the form block after the composed text. Status comes from the evidence, like every other status. One flow for every turn, no second path for "only a form ask".
- **A form is offered only for a served ask**: discovery found a provider at its position, and the registry has a form for its context. Otherwise the ask is handled as today.
- **A form-only turn still pays the composer call.** The planner's agent run is skipped by a guard on an empty actionable set. The composer always runs, which is the "every reply through the composer" direction. Known latency cost, 1 to 2 seconds on that turn; a fixed-text version is a later, contained change if the benchmark says so.
- The spec's "first form ask only, noted as a limit" and "the LLM planner is skipped" change to the above. "The form arrives in `claim.completed`" stands: the form rides the answer stream after the composed text.

Branch `feat/205-form-offer` off `main`. Commit 1, `form_context_for`, is in.

## Design

### Shape of "what's the weather, and file my PMFBY grievance"

1. Intent, enrichment, moderation, discovery: as today. The grievance ask carries `scheme=PMFBY`.
2. **Form step**, `orchestration/form_offer.py::find_forms(intent, discovery, lookup) -> Mapping[int, FormDefinition]`. Per ask: served at its position (the per-ask test `_nobody_serves` makes), `form_context_for(ask)` not `None`, `lookup.find(context)` found. Here `{1: grievance form}`.
3. **Planner** gets `awaiting={1: ...}`. Its prompt marks ask 1 "awaits a form, make no call"; the `select` tool refuses position 1 the way it refuses an ask with no place; the agent runs for ask 0. Evidence: ask 0 served, ask 1 awaiting.
4. **Composer** renders ask 1 as one line, "awaits a form shown below this reply", and its prompt says: tell the farmer in one sentence to fill in the form below, never list or invent its fields.
5. The composed text is claimed. Then `offer_form(definition)` from `core/forms/service.py` mints a `FormBlock` with a fresh id, appended as its own whole claim. `TurnFinished` is text then form, status `requires_input`.

With the grievance alone: `find_forms` gives `{0: ...}`, no ask is actionable, the agent run is skipped, the composer writes the one referral sentence, the form follows.

### `core/planner`

- `AskEvidence.awaiting: FormDefinition | None = None`. `Evidence.awaiting_forms` lists the asks that have one. `unserved_asks` excludes them: not served, but not a gap either. `core/planner` imports `core/forms`; forms imports only shared.
- `assemble_evidence(..., awaiting: Mapping[int, FormDefinition] = {})` and `group_by_ask` carry it; `_reject_stray_ask_indexes` checks its keys like the others. `tests/support/evidence.py::build_evidence` gains the same keyword.

### `core/forms/service.py` (new)

- `offer_form(definition) -> FormBlock`: a fresh `uuid4().hex` as `form_id`. Forms owns how a form is offered.

### Planner (`orchestration/plan.py`, `orchestration/planner.py`, `core/planner/prompt.py`)

- `Plan.__call__` gains `awaiting: Mapping[int, FormDefinition] = {}`. `PlannerDeps.awaiting: frozenset[int]`.
- Guard: `actionable = [i for i in range(len(intent.asks)) if i not in awaiting]`; empty means no agent run, straight to `assemble_evidence` with the place failures and `awaiting`. Same kind of guard as "no asks" in the orchestrator.
- `_select` gate right after the verdict check: a position in `awaiting` returns "Ask N awaits the farmer's form. Do not call select for it." Same shape as the no-place return.
- `asks_section(asks, *, awaiting)` renders the marker on the line: `- ask 1: Grievance (act), awaits a form`. One instruction line in `prompts/planner/{en,hi}/prompt.j2`: an ask marked as awaiting a form needs the farmer's input first; make no call for it.

### Composer (`core/channel/prompt.py`, `prompts/composer/{en,hi}/prompt.j2`)

- `render_evidence` renders an awaiting ask as `Awaits a form{place label}: the farmer fills in the {scheme name} form shown below this reply.`
- Prompt rule in both languages: if a line says an ask awaits a form, tell the farmer in one sentence to fill in the form below; do not list, describe or invent its fields.

### Orchestrator (`orchestration/orchestrator.py`)

- `TextPath(form_lookup: FormLookup)`. Composition wires `NoForms` from `core/forms/registry.py`, a lookup whose `find` returns `None`, with `# TODO(#205): the registry built from the packs replaces this.` Production behaviour unchanged, and the wire's `TypeError` on a form block stays unreachable until task 5.
- `_run_core`: after `_nobody_serves`, `awaiting = find_forms(...)`, passed to `plan`. After the composed claim, one `add_block(offer_form(e.awaiting))` per awaiting ask, in ask order.
- `_status_for`: `awaiting_forms` first, `REQUIRES_INPUT` with no cause.

### Readers that assumed every block has text

- `AnswerStream.add_block(block) -> Claim`: a block that was not streamed; asserts no pieces pending.
- `translate_out`: text blocks stream a translation, others `add_block`.
- `_reveal_to_farmer`, the `Claim` branch: `claim()` for text blocks, `add_block` for others.
- `tests/support/turn_events.py::assert_well_formed`: a claim whose block has no text asserts no pending deltas.

### Not in this plan

- The wire (`form` output item, SSE framing): task 5.
- A trace stage for the form step; it is a lookup.
- Prompts skipping form references in history: task 6.

## Steps (TDD, one test at a time, user reviews before each commit)

1. Tier 1 `tests/unit/core/planner/test_evidence.py`: an awaiting ask sits on its `AskEvidence`, is not an unserved ask, and a stray position is refused. **Commit 2**: `feat: evidence records an ask that awaits a form [#205]`.
2. Tier 1 `tests/unit/core/forms/test_service.py`: `offer_form` mints a fresh id per call. **Commit 3**: `feat: offer a form as a block with its own id [#205]`.
3. `tests/integration/orchestration/test_answer_stream.py` (new, beside the stream tests): `add_block`; `assert_well_formed` learns blocks without text. Tier 3 translation test: a translated turn carries a form block whole. **Commit 4**: `feat: the answer stream carries a whole block [#205]`.
4. Tier 3 `test_orchestrator.py`: with a fake plan returning awaiting evidence, the turn ends `requires_input`, text then form; `_build` gains `form_lookup`. **Commit 5**: `feat: a turn with a form awaiting ends on the form [#205]`.
5. Tier 3 planner: `test_planner_select_tool.py` select refuses an awaiting position; `test_plan.py` no agent run when nothing is actionable, awaiting reaches the evidence; tier 1 `asks_section` marker. Prompt lines en and hi. **Commit 6**: `feat: the planner leaves an ask that awaits a form alone [#205]`.
6. Tier 1 `test_prompt.py`: the awaiting line; composer prompt rule en and hi. Tier 5 structural, skipped without a key, run once: the composer's reply for an awaiting ask mentions the form and names no field. **Commit 7**: `feat: the composer refers the farmer to the form [#205]`.
7. Tier 3 `test_form_offer.py`: `find_forms` for a served ask with a form, no provider, provider but no form, weather plus grievance gives `{1: form}`; `_run_core` wiring: the fake plan sees `awaiting`. **Commit 8**: `feat: offer the form for a served ask the registry knows [#205]`.
8. Docs: spec edge cases, failure modes, orchestration section and task 4 boxes; `docs/DSS_ARCHITECTURE.md` §5.1 one paragraph. **Commit 9**: `docs: record the form step [#205]`.

## Verification

- `uv run pytest` green, `uv run ruff check src` clean, `uv run ruff format --check .`.
- `uv run pytest tests/unit/test_core_isolation.py`: `core/planner` importing `core/forms` is core to core.
- Tier 5 composer test once, live.
- Local run with the stub LLM: a text turn still answers as before. The form path is unreachable until the registry lands, by design.

Time: about 5 hours with review rounds.
