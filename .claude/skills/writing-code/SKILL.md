---
name: writing-code
description: Code-quality rules for writing or changing Python under src/ or tests/ in this repo — declarative style, self-documenting names, return types, SOLID, YAGNI, DRY, comments. Load before any code edit.
---

# Writing code in the DSS

Short rules. Apply them to every line you write or touch. The full catalogue
with examples is in the `review` skill (`design.md`).

## Declarative first
High-level code says **what** happens, not **how**. Low-level steps live in small named helpers.
- A top-level function reads like a list of named steps. A reader gets the flow without opening the helpers.
  ```python
  # Bad — the how is mixed into the flow
  def plan_turn(turn):
      tools = []
      for skill in skills:
          if skill.domain == turn.domain and skill.enabled:
              for name in skill.tools:
                  if name not in tools:
                      tools.append(name)
      ...

  # Good — the flow reads as steps
  def plan_turn(turn):
      skills = select_skills_for(turn.domain)
      tools = collect_tool_names(skills)
      return build_plan(turn, tools)
  ```
- Put varying facts in data (tables, mappings, config), not in branches. Code walks the data.
  ```python
  # Bad
  if crop == "paddy": ...
  elif crop == "wheat": ...

  # Good
  SEASONS: Mapping[Crop, SeasonWindow] = {...}
  window = SEASONS.get(crop)
  ```
- Prefer comprehensions, `any`/`all`, `sorted(key=...)` and `Mapping` lookups over manual loops that build lists and set flags.
- Return values. Avoid mutating arguments or building results through shared state.
- One level of abstraction per function. Don't mix a business step with string slicing.

## Naming — names are the documentation
A reader should understand a function from its name and signature alone, without its body or a comment.
- Variables, attributes, classes: **nouns**. `capability_index`, `skipped_packs`, `Evidence`.
- Functions and methods: **verbs** that say what they do. `build_capability_index`, `resolve_location`.
  - `unserved_asks()` → `find_unserved_asks()`. `provider_failed` as a function → `mark_provider_failed()`.
  - Predicates read as questions: `is_expired`, `has_location`.
- Booleans: `is_`/`has_`/`should_` prefix. Never `flag`, `status`, `ok`.
- No vague names: `data`, `info`, `result`, `obj`, `tmp`, `handle`, `process`, `manager`, `helper`, `utils`.
- One word per concept across the repo. If `fetch` is used, don't add `get`/`retrieve`/`load` for the same idea.
- No abbreviations unless they are domain terms (`bpp`, `pii`). No type in the name (`items_list`).
- Name says the full effect. If it also writes, sends, or mutates, the name says so (`save_`, `send_`, `mark_`).
- Name by meaning, not mechanics: `eligible_providers`, not `filtered_list`; `days_until_harvest`, not `diff`.
- Units and scope in the name when ambiguous: `timeout_seconds`, `max_retry_count`.
- If you need a comment to explain a name, rename it.

## Return types — no positional tuples
- Never return a tuple of **different things**. Callers unpack by position and break silently when it changes.
  ```python
  # Bad
  def build_capability_index(...) -> tuple[dict[...], tuple[SchemaPackSkipped, ...]]:
      return index, skipped

  # Good
  @dataclass(frozen=True)
  class CapabilityIndex:
      types_by_capability: Mapping[Capability, tuple[str, ...]]
      skipped_packs: tuple[SchemaPackSkipped, ...]
  ```
- Use a frozen `dataclass` by default. `NamedTuple` only if callers already unpack it. `TypedDict` only for a dict that crosses a JSON boundary.
- A tuple of **one thing** (`tuple[str, ...]`) is fine — it is an immutable list.

## Primitive obsession
- A domain value gets a type: `LanguageCode`, `RequestId`, a `Capability` dataclass. Not bare `str`, `int`, or `tuple[str, str]` dict keys.
- No `dict` crossing a function boundary when the keys are known — make it a model.
- No magic strings repeated in two places — use an `Enum` or `Literal`.
- Name domain values with a type alias, so the signature says what it is: `-> Prompt`, not `-> str`.
  ```python
  type Prompt = str
  type Vector = list[float]
  ```
  Use the `type` statement, not `X = str`.
- Use `NewType` when mixing two values up would be a bug, such as IDs or language codes. Callers must wrap: `RequestId(raw)`.
  ```python
  RequestId = NewType("RequestId", str)
  ```
- Put the alias in the owning function's `models.py`, or in `core/shared/` if several functions use it. No global `types.py`.

## SOLID, applied here
- **S** — one reason to change per function and per module. If you write "and" to describe it, split it.
- **O** — add a new provider, reviewer, or skill by adding a file, not by growing an `if/elif` chain.
- **L** — every adapter honours its port's full contract. No adapter-specific `isinstance` checks in callers.
- **I** — ports stay small. A caller depends only on the methods it uses.
- **D** — `core/` depends on `ports/` Protocols. Never construct an adapter inside `core/`.

## YAGNI and DRY
- Build only what the current issue needs. No unused parameters, flags, hooks, or "for later" abstractions.
- Copy once is fine. Extract on the **third** copy, and only if the copies change for the same reason.
- No class where a function will do. No base class with one subclass.

## Comments
Code says **what**. A comment says **why**, and only when the code can't.
- Before writing a comment, try a better name or a smaller function instead.
- Keep: non-obvious reasons, constraints from outside (a spec, a bug, a vendor quirk), surprising decisions. One or two lines.
- Delete: comments that restate the code, narrate steps, describe history ("used to…", "now we…"), or repeat the docstring.
- Docstrings are for the caller, and only when the name and signature can't say it: an ordering guarantee, a unit, what is raised, a surprise. One line. A second paragraph only for a contract.
  - No docstring when the name says it. `class TextQuery: text: str` needs none. "Build a turn" above `build_turn` is noise.
  - Don't add one because the file's other classes have one. Matching style is not a reason.
  - Tests: none, unless the reasoning isn't in the name.
  - Modules: say why the module exists, not what is in it.
  ```python
  # Bad — the name already says it
  class TextQuery(TurnInput):
      """What the farmer typed or said."""
      text: str

  # Good — a contract a caller depends on
  def texts_of(turn: UserTurn[TextQuery]) -> list[str]:
      """The question, then every history message, then the phone. `with_texts` relies on this order."""

  # Bad — restates the code
  # loop over packs and skip bad ones
  for pack in packs: ...

  # Good — a reason the code can't show
  # sorted: set order varies with PYTHONHASHSEED and reaches the jsonpath filter.
  categories = sorted(...)
  ```

## Functions
- Short — fits on a screen. Few parameters; use keyword-only (`*`) after the first one or two.
- No boolean flag parameters that switch behaviour — write two functions.
- Return early. Avoid nesting deeper than two levels.
