# ADR-0005: Use anyio for Async Concurrency and Structured Concurrency

- **Status:** ACCEPTED
- **Date:** 2026-09-02
- **Deciders:** DSS implementation

---

## 1. Context and Problem Statement

Provider Discovery (issue #79) fans out one network call per distinct query
value and needs to run those calls concurrently. Plain `asyncio` does not
guarantee structured concurrency: `asyncio.gather()` does not reliably cancel
sibling tasks when one fails, and error handling is manual. `asyncio.TaskGroup`
(3.11+) closes this gap, but only for asyncio itself.

`pydantic-ai-slim` — the orchestration framework already adopted in ADR-0001 —
depends on `anyio` internally. `anyio` is already present in this project's
dependency tree, transitively, whether or not it is declared.

## 2. Decision Drivers

1. Structured concurrency: a failed task should cancel its siblings and
   surface its error, without hand-written bookkeeping.
2. Consistency with the orchestration framework's own async foundation —
   fewer concurrency models in one codebase.
3. Avoid depending on a transitive package implicitly; if the codebase relies
   on it, it belongs in `pyproject.toml`.

## 3. Considered Options

1. **Plain `asyncio`** (`TaskGroup` for structured concurrency where needed).
2. **`anyio`** — structured concurrency (`create_task_group`) on top of
   asyncio or trio, interchangeably.

## 4. Decision Outcome

Chosen option: **anyio**, declared as a direct dependency.

It is already the concurrency layer underneath Pydantic AI, so adopting it
explicitly adds no new runtime behaviour — it makes an existing, load-bearing
dependency visible and pins it deliberately instead of inheriting whatever
version `pydantic-ai-slim` happens to resolve. `anyio.create_task_group()` is
used for per-ask fan-out in Provider Discovery, and its pytest plugin runs
the project's async tests.

## 5. Consequences

- `anyio` is a main dependency, not a dev-only one — `adapters/` and
  `orchestration/` will use `create_task_group()` for concurrent network
  calls.
- Async tests use `@pytest.mark.anyio` with an `anyio_backend` fixture fixed
  to `"asyncio"` (trio is not otherwise used in this project).
- `asyncio.TaskGroup` remains available and is not banned; anyio is preferred
  for anything that should compose with the rest of the codebase's
  concurrency, so a future trio experiment would not require touching
  business logic.
