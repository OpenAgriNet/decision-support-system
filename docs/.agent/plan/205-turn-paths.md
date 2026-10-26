# #205 task 2: the orchestrator picks a path by input kind

## Context

Spec `docs/.agent/specs/205-farmer-asks-form-offered.md`, task 2. Today `Orchestrator.run` narrows the turn once in `_as_text_turn()` and raises `TypeError` for any other input. Task 3 adds `FormSubmission`; without a seam, that becomes an `if` chain. This task makes the pipeline `TextPath`, puts a path table in front of it, and refuses to boot when a `TurnInput` kind has no path.

Branch: `feat/205-turn-paths` off `main` (#129 and #130 merged 26 Oct).

## What exploration found

- Everything below the span is already typed `UserTurn[TextQuery]`: redaction (`texts_of`/`with_texts`), `turns.opened`, edge translation, `_run_core`, `_reveal_to_farmer`, `_record_finished`. Only `bind_turn_ids`, `turn_span` and `TurnStarted` are kind-agnostic (`src/dss/orchestration/orchestrator.py:219-275`).
- So the path owns the whole text pipeline, not just the core half the spec sketched. Redaction (`texts_of`/`with_texts`), translation and reveal all read the text turn, and the spec itself says each path owns its redaction. The path therefore yields `TurnEvent`, and the spec's separate `input` argument goes because `turn.input` is already typed. **Agreed with the user on 26 Oct; spec lines 63-64 are rewritten to this shape in commit 3.**
- Boot-check pattern to mirror: `src/dss/config/prompt_service.py:209-220` (list the unserved, raise `ValueError` with "refusing to boot").
- Protocol style to mirror: `Plan` in `src/dss/orchestration/plan.py:42`.
- Four places build an `Orchestrator`: `tests/integration/orchestration/test_orchestrator.py:_build` (reused by the translation test), `test_orchestrator_streaming.py:66,187`, `tests/unit/test_port_conformance.py:79`. Composition builds it at `src/dss/entrypoint/composition.py:199`.
- `__subclasses__()` is used nowhere in `src/`. It lists direct subclasses only, which is all we have.

## Design

In `src/dss/orchestration/orchestrator.py` (same file; a split into `text_path.py` can come with the form step if the file grows):

1. **Rename** today's `Orchestrator` to `TextPath`. Its `__init__` keeps the 14 deps. `run(self, turn: UserTurn[TextQuery], *, ctx, recorder) -> AsyncIterator[TurnEvent]` is today's body minus `_as_text_turn`, `bind_turn_ids`, `turn_span` and `TurnStarted`. Delete `_as_text_turn`.
2. **`TurnPath` protocol**: one method, `run(turn: UserTurn[Any], *, ctx: TurnContext, recorder: TurnRecorder) -> AsyncIterator[TurnEvent]`. `Any` so `TextPath`, which declares `UserTurn[TextQuery]`, satisfies it by shape.
3. **New `Orchestrator(*, paths: Mapping[type[TurnInput], TurnPath])`**. `run` binds ids, opens the span, yields `TurnStarted`, then `async for event in self._paths[type(turn.input)].run(turn, ctx=ctx, recorder=recorder): yield event`. Still satisfies `ports.turn.TurnRunner`.
4. **`unrouted_kinds(paths, kinds: Iterable[type[TurnInput]]) -> list[type[TurnInput]]`**, module-level, pure. Takes `kinds` explicitly so the test can pass a throwaway subclass without touching the real `__subclasses__()` list.

In `src/dss/entrypoint/composition.py`: build `TextPath(...)` with today's kwargs, `paths = {TextQuery: text_path}`, then the check like the prompt one:

```python
# Every input kind the wire can carry needs a path, or the miss would be a
# KeyError on a farmer's turn instead of a refusal here.
unrouted = unrouted_kinds(paths, TurnInput.__subclasses__())
if unrouted:
    raise ValueError(f"no path for {', '.join(k.__name__ for k in unrouted)}, refusing to boot")
runner = Orchestrator(paths=paths)
```

## Steps (TDD, one test at a time, user reviews before each commit)

1. Test `tests/integration/orchestration/test_orchestrator.py`: `test_a_text_turn_runs_on_its_path`. A fake `TurnPath` that yields one `TurnFinished`; `Orchestrator(paths={TextQuery: fake})`; assert events are `TurnStarted` then the fake's. Red: no `paths` kwarg.
2. Make it pass: steps 1 to 3 above. Update the four builders: `_build` returns `Orchestrator(paths={TextQuery: TextPath(...)})`; same two-liner in streaming and port_conformance. Composition wires the same way, no check yet. Suite green. **Commit 1** (`refactor: run the turn on a path picked by input kind [#205]`).
3. Test `test_unrouted_kinds_lists_the_kind_without_a_path`: a `class _Otp(TurnInput)` in the test, `unrouted_kinds({TextQuery: fake}, [TextQuery, _Otp]) == [_Otp]`. Red, then write the function.
4. Add the composition check. Existing `test_build_runner_returns_a_turn_runner` covers the wired case. **Commit 2** (`feat: refuse to boot when an input kind has no path [#205]`).
5. Docs, **commit 3**: spec lines 63-64 record the deviation (path yields `TurnEvent`, owns redaction and translation, no `input` arg) and tick task 2; `docs/DSS_ARCHITECTURE.md` §5.1 gets one sentence under the open-slots paragraph: the orchestrator picks a path by the input's kind. No ADR (user's call, recorded).

## Verification

- `uv run pytest` green (1759 today), `uv run ruff check src` clean, `uv run ruff format --check .`.
- `uv run pytest tests/unit/test_core_isolation.py tests/unit/test_port_conformance.py` still pass: `Orchestrator` still a `TurnRunner`.
- Local run: `uv run uvicorn --factory dss.entrypoint.app:create_app --port 8077`, one text turn via curl from `docs/RUNNING.md`, same answer as before.

## After this plan (user's ask, 26 Oct)

Next PR, in order: an integration test that a PMFBY grievance turn ends with a form block (spec tasks 4 and 5: form step on `TextPath`, `form` wire shape), then the submission path (task 3).

Time: about 2 hours including review rounds.
