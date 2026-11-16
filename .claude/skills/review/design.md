# Design pass

Reviews design and readability. The rules come from the `writing-code` skill;
this file is the checklist. Bugs belong to the correctness pass — skip them.

## Checks, in order

### Boundaries (highest severity)
- `core/` imports Pydantic AI, pydantic-graph, an SDK, or an adapter.
- `core/` opens a span or logs a provider body.
- A vendor type leaks out of `adapters/` into `core/` or a port.
- Logs contain raw PII or miss `request_id`.

### Design
- **Single responsibility** — a function or module that does two jobs. Signal: "and" in its description, or two unrelated groups of parameters.
- **Open/closed** — a new variant added by extending an `if/elif`/`match` on a type or name.
- **Liskov** — a caller checks which adapter it has, or an adapter raises `NotImplementedError` for part of its port.
- **Interface segregation** — a port gained a method only one caller needs.
- **Dependency inversion** — `core/` constructs its own dependency instead of taking a port.
- **YAGNI** — unused parameters, options with one value, abstractions with one implementation, code "for later".
- **DRY** — the same logic in three places that would change together. Two copies is not a finding.

### Smells
| Smell | Signal | Fix |
|---|---|---|
| Primitive obsession | Function returns a tuple of different things; `tuple[str, str]` as a dict key; `dict` with fixed keys passed around; a `str` that is really a language code or ID | Frozen `dataclass`; `NamedTuple` if unpacked; `TypedDict` at a JSON edge; `Enum`/`Literal` for fixed values |
| Unnamed domain type | A signature uses bare `str`, `list[float]`, `dict` for a domain value (`-> str` for a prompt); old-style `X = str` alias; a shared `types.py` | `type Prompt = str` in the owner's `models.py`; `NewType` for IDs and codes that must not mix |
| Shotgun surgery | One logical change edits many unrelated files | Find the missing seam; move the knowledge into one place |
| Divergent change | One file edited for unrelated reasons | Split by reason to change |
| Feature envy | A function mostly reads another object's fields | Move it next to that data |
| Data clumps | The same 3+ parameters travel together | Make them one type |
| Long parameter list | More than ~4 parameters | Group into a type; keyword-only args |
| Flag argument | `bool` parameter that switches behaviour | Two functions |
| Long function | Doesn't fit on a screen; deep nesting | Extract named steps; return early |
| Speculative generality | Hooks, base classes, or params with one user | Delete |
| Dead code | Unreachable branches, unused names | Delete |
| Message chains | `a.b.c.d` across modules | Ask the owner for what you need |

### Declarative style
- A high-level function mixes the flow with low-level detail (index loops, flag variables, string slicing). Fix: extract named steps so it reads as a list of what happens.
- A branch per case (`if crop == ...`) where a table or mapping would do. Fix: move the facts into data.
- A manual loop that builds a list or sets a flag where a comprehension, `any`/`all`, or `sorted(key=...)` says it directly.
- A function mutates its arguments or shared state instead of returning a value.
- Two levels of abstraction in one function.

### Naming (self-documenting)
- Can't tell what a function does from its name and signature alone → rename.
- Name hides an effect (writes, sends, mutates) → name it (`save_`, `send_`, `mark_`).
- Named by mechanics, not meaning (`filtered_list`, `diff`, `tmp`) → name the domain idea.
- A comment explains what a name means → rename and delete the comment.
- Function not a verb (`unserved_asks`, `provider_failed`) → `find_unserved_asks`, `mark_provider_failed`.
- Variable or class not a noun; boolean without `is_`/`has_`/`should_`.
- Vague (`data`, `result`, `info`, `handle`, `process`, `utils`, `manager`).
- Two words for one concept in the same area (`fetch` vs `get`).
- Name disagrees with what the code does.

### Comments
- Restates the code, narrates steps, or tells history → delete.
- Explains confusing code → rename or extract instead, then delete.
- Docstring longer than the function, or repeats the signature → cut to one line.
- Keep: a reason the code cannot show (external constraint, surprising choice).

### Tests
- Test is in the wrong tier for what it tests (see the table in `CLAUDE.md`).
- Asserts exact LLM text outside tier 4.
- Mocks Pydantic AI in tier 3.

