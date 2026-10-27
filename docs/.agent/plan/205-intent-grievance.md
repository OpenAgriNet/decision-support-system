# #205 task 3: intent learns Grievance, the ask carries its scheme

## Context

Spec `docs/.agent/specs/205-farmer-asks-form-offered.md`, task 3. The form step (next plan) needs an ask that says "a grievance, about scheme PMFBY, to be filed". Today the classifier has no `Grievance` category, and scheme enrichment finds the catalog's `Scheme(code, name)` then drops the code and forces the category to `Scheme`. So a grievance ask cannot exist, and nothing carries the key the Grievance pack selects its form by, `scheme.code`.

Decided 27 Oct: the ask gets a typed `scheme` slot, like it already has a typed `place` slot. Words stay words. The code is the network's, verbatim, and the planner's provider filter reads the same slot later.

Branch: `feat/205-intent-grievance` off `main`. Independent of #132.

## Design

### `core/intent/models.py`

- `Scheme(code, name)` moves here from `core/enrichment/models.py`, unchanged. Four importers follow: `adapters/scheme_catalog/csv_file.py`, `ports/scheme_catalog.py`, `tests/support/fakes.py`, `tests/unit/core/enrichment/test_service.py`. Enrichment imports intent, so the type must sit on the intent side.
- `Ask.scheme: Scheme | None = None`, beside `place`. Docstring line: set by enrichment on an alias hit, the key a scheme pack selects by.
- `SubjectCategory.GRIEVANCE = "Grievance"`, with a comment: the Grievance API pack's type, filed or checked against a scheme.

### `core/enrichment/service.py`

On an alias hit, `resolve_scheme_subjects` sets `scheme=match.scheme` and the canonical name as today. The category becomes `Scheme` only when the classifier did not say `Grievance`. A grievance is about a scheme; the classifier already said what kind of ask it is. Docstring updated.

### Prompts `prompts/intent/en/prompt.j2` and `hi/prompt.j2`

One category line after `Facility`: "Grievance — a complaint about a scheme: an enrolment, claim or payment that went wrong, to be filed or followed up." One example: `'file a complaint about my PMFBY claim' -> Grievance / act / 'PMFBY'`. The Hindi file gets both in Hindi, with the category value and the example query kept in English, as its own header rule says.

### Catalog

- Fixture `tests/integration/adapters/scheme_catalog/fixtures/schemes.csv` gains `PMFBY,Pradhan Mantri Fasal Bima Yojana,PMFBY|fasal bima`.
- `csv_file.py` module docstring and `docs/DSS_ARCHITECTURE.md` §enrichment say: `scheme_code` is the network's code, verbatim and case-exact. It is the key a pack selects a form by.

### Not in this plan

- Discovery for a `Grievance` ask. The capability index learns the Grievance type when the pack is indexed, task 1. Until then a grievance ask is `no_match`, which is today's behaviour for any unserved ask.
- The form step and the wire. Next two plans.

## Steps (TDD, one test at a time, user reviews before each commit)

1. `tests/unit/core/intent/test_models.py::test_subject_category_values` adds `Grievance`. Red, add the enum value. Prompt line and example. `tests/unit/config/test_shipped_prompts.py` renders from the enum, so it stays green. **Commit 1**: `feat: intent learns Grievance [#205]`.
2. Move `Scheme`, fix four imports. Suite green. **Commit 2**: `refactor: move Scheme to the intent models [#205]`.
3. `tests/unit/core/enrichment/test_service.py::test_an_alias_hit_sets_the_scheme_on_the_ask`. Red, add the slot and set it. **Commit 3**: `feat: carry the matched scheme on the ask [#205]`.
4. `test_a_grievance_ask_keeps_its_category`. Red, the one-line rule. **Commit 4**: `feat: an alias hit keeps a grievance ask a grievance [#205]`.
5. Fixture row, docstring, architecture doc, spec task 3 ticked and data model section updated. **Commit 5**: `docs: the catalog code is the network's scheme code [#205]`.
6. Tier 5, `tests/llm/structural/test_intent_grievance.py`: "I want to file a PMFBY grievance" classifies as `Grievance` / `act`. Skips without a model key, same guard as `test_intent_place_name.py`. Run once live before the PR. **Commit 6**: `test: a grievance sentence classifies as Grievance [#205]`.

## Verification

- `uv run pytest` green, `uv run ruff check src` clean, `uv run ruff format --check .`.
- `uv run pytest tests/llm/structural/test_intent_grievance.py` with a key in the environment, once.
- `grep -rn "enrichment.models import Scheme"` finds nothing.

Time: about 1.5 hours with review rounds.
