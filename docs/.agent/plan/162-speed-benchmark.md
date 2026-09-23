# #162 Speed Benchmark — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans. Steps use `- [ ]`.
> This plan is deleted when #162 merges (see `docs/.agent/plan/README.md`).

**Goal:** One command runs 30 fixed questions against a running DSS and reports how fast each turn was answered.

**Architecture:** A CLI in `evals/perf/` drives a real uvicorn DSS over SSE. It times the first `claim.delta` and `claim.completed` on the client. It reads stage times and tokens back from Langfuse. The mock network changes from "one JSON file per `@type`" to "a generator per `@type`", matched on key fields.

**Tech stack:** Python 3.13, httpx (already a dependency), stdlib `argparse` / `statistics` / `csv`. No new dependency.

**Spec:** [engineering-tracker#162](https://github.com/OpenAgriNet/engineering-tracker/issues/162) plus the Q1–Q28 answers in `.scratchpad/handoff-2026-09-23-speed-benchmark.md`.

**Branch:** `feat/162-speed-benchmark`. One PR: code, ADR-0013, the `CLAUDE.md` tier-table line, and the `DSS_ARCHITECTURE.md` / `RUNNING.md` updates.

## Decisions made in planning (2026-09-23)
1. A miss returns 400. One test checks that the mock refuses and counts it.
2. Load testing is part of #162.
3. Load uses the **real LLM** only.
4. Load runs **concurrency steps** (closed loop): N workers, each sends, waits, then sends again. Default N = 1, 2, 4, 8.
5. Load is **its own mode**: `uv run python -m evals.perf load`. The speed run stays one turn at a time.
6. Load mode pins the DSS to **1 CPU and 1 GiB** in Docker. The report shows the limits and the machine facts. The speed run is not pinned.
7. There is no GPU pin. The DSS uses no GPU, and the LLM's GPU belongs to the provider. A self-hosted model is a later story.
8. No requests-per-second figure for the speed run. The load run reports turns per minute for each step.

## Global constraints
- Strict TDD, **one test at a time**. Red, green, then the next test.
- `evals/` is never imported by `src/dss/`. It doesn't ship, because the Dockerfile copies only `src/`.
- No call leaves for a real provider. The mock serves every `/select`.
- Never read `.env*`. The user exports the Langfuse keys.
- Commit only when the user asks. Use the format `<type>: <summary> [#162]`.

## Findings that shape the plan (please check)
1. **The DSS reads only `commitments[0].resources[0]`** (`adapters/invocation/client.py:108-109`). A real mandi answer (seen 2026-09-23) is one resource with one price, about 1.5 KB, so mandi stays at one price and has no size knob. The knob grows lists inside the one resource:
   - advisory: `recommendations[]` and `actions[]`
   - weather: `parameters[]` (at most 8, from the pack enum)
   - mandi: one price, fixed, as in real answers
1a. **Mandi market comes from discover.** A real discover advertises one resource per market (`resource:mandi-price:market:1634`), with `market{marketCode, marketName, district (a code), state (a code), location.geo}`. The select request echoes it. The mock's mandi discover must do the same: one resource per benchmark market (Mumbai, Kochi, Kozhikode, Bangalore, Chandigarh).
2. **A miss returns 400, not 404.** A 404 is retried 3 times (TRANSIENT, `network_common.py:21-32`), wasting about 1.5 s per miss. That doesn't skew the figures, because miss turns are left out. It only saves run time.
3. **The client can't see a miss.** A miss turn just ends `unavailable`. So the mock counts its own misses at `GET /_bench/misses`, and the CLI reads the count before and after each turn. This works because turns run one at a time.
4. **The mock does not check packs at run time.** `RUNNING.md:122-125` says it does. The plan checks generator output against the packs *in tier-1 tests* and fixes the doc.
5. **The known values live in the discover files.** Matching checks the request against what the mock *advertises*, e.g. `supportedCommodities`. The mandi file must list the benchmark's commodities: potato, apple, tapioca, cotton.
6. **Models and trace:** the SSE `traceId` echoes our `transactionId`. The CLI sends a unique `sessionId` per turn and finds the trace in Langfuse by session. The four model names are read from the `dss.turn` span attributes, so they come from the server that actually ran.
7. **Tokens:** sum only the `chat {model}` spans (`gen_ai.usage.input_tokens` / `output_tokens`). Agent spans carry `gen_ai.aggregated_usage.*`, so summing those too would count tokens twice.

## Review focus (not covered by the happy path)
1. A turn that ends `requires_input` or `no_match` has no first delta. It is counted by status and left out of the timing figures.
2. A request with a missing or malformed key field. It must come back as a miss (400), never an uncaught error (500), which the DSS would retry. There is no name matching: the planner sends only advertised codes (`core/planner/resource_attributes.py:98-129`).
3. Langfuse hasn't finished ingesting the trace. Poll with a deadline, then mark the turn `trace_missing`, never crash.
4. A flat trace, where stage spans are not children of `dss.turn`. Warn and leave stage figures out.
5. `--max-turns` is smaller than warm-up plus timed turns. Stop at the cap, report what ran, and say so.

---

## File map
| Path | Job |
|---|---|
| `tools/mock_network/generators.py` | New. One pure function per `@type`: (request key fields, size) → resourceAttributes. The same input always gives the same output. |
| `tools/mock_network/matching.py` | New. `key_fields(type, request_attrs) -> dict \| None`. Normalises the values and checks them against the advertised values. |
| `tools/mock_network/app.py` | `/select` calls matching then the generator. A miss returns 400 and increments a counter. Adds `GET /_bench/misses`. Size flags come from `__main__`. |
| `tools/mock_network/responses/*_select.json` | Deleted; the generators replace them. |
| `tools/mock_network/responses/mandi_discover.json`, `advisory_discover.json` | Advertise the benchmark's commodities and crops. |
| `evals/__init__.py`, `evals/perf/__init__.py`, `__main__.py` | CLI entry: `uv run python -m evals.perf`. |
| `evals/perf/questions.toml` | 30 `[[question]]` entries: `id, category, question, region, area`, plus `lon/lat` (weather) and `match, answer` (advisory). TOML because it reads long text well, has strict types, and `tomllib` is in the standard library. The mock reads it through `--questions <path>`, not by importing `evals/`. |
| `evals/perf/questions.py` | Loads the entries for `--lang`. |
| `evals/perf/sse.py` | Parses SSE lines into `(event, data)`. Pure. |
| `evals/perf/turn.py` | Drives one turn and returns `TurnTiming`. |
| `evals/perf/traces.py` | Finds the trace and returns `TraceFacts` (stages, token calls, models, flat or not). Not `langfuse.py`: nothing imports the SDK, only the REST API. Langfuse v4 (`events_only`) has turned off `/api/public/traces`; the read API is `/api/public/v2/observations`. Only `dss.turn` carries the session, so it's two steps: find the root by `sessionId`, then fetch the whole trace by `traceId`. |
| `evals/perf/stats.py` | `summarise(values) -> Summary(p50, p95, max, n)`. |
| `evals/perf/runner.py` | Warm-up, repeats, turn cap, miss check, and joining client times to trace facts. |
| `evals/perf/load.py` | Load mode: steps through the N values with N workers each, and returns one `StepResult` per N. |
| `evals/perf/container.py` | Starts and stops the pinned DSS container (`docker run --cpus 1 --memory 1g`), and reads back the limits with `docker inspect`. |
| `evals/perf/report.py` | Text table to stdout, and the JSON to `var/evals/perf/<utc>-<commit>.json`, so a run is found by time or commit. The CLI reads the commit with `git rev-parse HEAD` (`-dirty` with changes, `unknown` with no git). In #164's PR job, pass the PR head SHA in, because a `pull_request` checkout is a merge commit. |
| `tests/unit/tools/test_mock_generators.py`, `test_mock_matching.py` | Tier 1. |
| `tests/integration/tools/test_mock_network.py` | Tier 2. Extend it for miss → 400 and the counter. |
| `tests/unit/evals/perf/test_*.py` | Tier 1, for sse, stats, questions, langfuse mapping, runner, report. |
| `tests/integration/evals/perf/test_turn.py`, `test_langfuse.py` | Tier 2. Local test server or a recorded fixture. |
| `docs/ADR/0013-speed-benchmark-harness.md`, `CLAUDE.md`, `docs/DSS_ARCHITECTURE.md`, `docs/RUNNING.md`, `pyproject.toml` | Docs, plus `known-first-party` gets `"evals"` and ruff `src` gets `"evals"`. |

---

### Task 1: Mock matching on key fields
**Interfaces:** `key_fields(capability: str, attrs: dict, advertised: dict) -> dict | None`
- Mandi: `{commodity, market}`. `commodity` is `supportedCommodities[].code`; a name is accepted and mapped to its advertised code. `market` is `market.marketCode`. Both must be advertised.
- Weather: `{lon, lat}` from `location.geo.coordinates`, rounded to 2 decimal places. It is required.
- Advisory: `{question_id}`. The DSS builds `topics` from the question as a short phrase, subject plus place (`src/dss/config/defaults/skills/provider-invocation.md:20-28`), e.g. `["Stem borer on wheat in Beed"]`. Each question row carries match words (`stem borer`, `wheat`). The mock picks the row whose words **all** appear in a topic, ignoring case. One file holds `question_id, question, match words, answer`, so the benchmark's questions and the mock's answers can't drift apart.
- Returns `None` if anything is missing or not advertised.

Tests, each written and passed before the next:
- [ ] Mandi code `"78"` → matches, commodity `"78"`.
- [ ] Mandi commodity not advertised → `None`.
- [ ] Mandi `marketCode` missing or not advertised → `None`.
- [ ] Weather coordinates rounded. A request with no location → `None`.
- [ ] Advisory topic containing all of a row's match words, in any case → that row's `question_id`. Missing a word → `None`. No `topics` → `None`.
- [ ] Run `uv run pytest tests/unit/tools/test_mock_matching.py`. Commit when asked.

### Task 2: Mock generators, deterministic, with size
**Interfaces:** `generate(capability: str, keys: dict, size: int, now: datetime) -> dict` (resourceAttributes). The seed is `sha256` of the sorted keys, never `hash()`, because Python salts `hash()` per process.

Tests:
- [ ] The same keys twice → equal output.
- [ ] Different keys → different figures (e.g. the mandi modal price).
- [ ] Advisory has **no generator and no size knob**. Each advisory question row carries a hand-written answer of 400–500 words, drafted by Claude and reviewed by the user. Nothing calls an LLM at run time. The mock puts the row's answer into `recommendations[0].message` word for word. It adds only the fields the pack requires (`@type`, `informationMode`, `subjectCategories`), so there are no dates and no source. The long text gives the composer plenty to stream from.
- [ ] Weather has **no size knob**. Each answer has the pack's required fields plus one entry for each of the pack's 8 `parameters[].parameter` values. 8 is the pack's full list, so it is the largest real answer. The values are drawn from a realistic range for each parameter by a `random.Random` seeded from the coordinates (`sha256`). So each location gets its own numbers, and the same numbers every run, which keeps token counts steady for #164.
- [ ] Every generator's output passes `check_against_pack` against the real `var/schema-packs` (reuse `tools/mock_network/validation.py`). Skip with a clear reason if packs aren't fetched.
- [ ] Dates use `now`, keeping the job of today's `fill_in_dates` (which is then no longer needed for select).

### Task 3: Wire the mock — 400 on miss, counter, size flags
- [ ] Tier-2 test: a known potato + Kochi-market `/select` returns a generated answer through the real `HttpCapabilityInvocation`. Shape it after the real answer: one resource, `arrivalDate`, `market{district, marketName, state}` as names, `prices{min,max,modal,currency,unit}`.
- [ ] The 400 gets a code comment saying why. A 404 fits "no answer" better, but the DSS treats a 404 as TRANSIENT and retries it. Turning retries off (`DSS_SELECT_ATTEMPTS=1`) was rejected, because the benchmark must time the DSS on its real settings.
- [ ] Tier-2 test: an unknown commodity → the mock answers 400, and `GET /_bench/misses` rises by 1. This is the only failure test. It backs "a miss is counted, never answered with wrong data". The DSS's own retry logic is not tested here.
- [ ] No size flags: advisory answers are hand-written, weather is fixed, and mandi is one price. Delete the `*_select.json` files, and update the mandi/advisory discover files.
- [ ] Run the whole suite: `uv run pytest`. Run `uv run ruff check .`.

### Task 4: Questions
- [ ] Copy 10 single-turn questions per category from `../oan-evaluation/inference/benchmark_questions.csv` into `evals/perf/questions.toml`, keeping `question_id`. Claude drafts the 10 advisory answers (400–500 words each) and their match words; the user reviews each one. Leave out livestock and scheme questions, which the mock doesn't serve. Add `region`/`area` so no turn stops at `requires_input`. Weather rows also get `lon`/`lat`, sent as `location.geometry`. The DSS doesn't pass a point it found by place name on to `/select` (`core/planner/resource_attributes.py:64-79`). Without the coordinates, every weather turn would miss. That bug is outside #162.
- [ ] A long input doesn't force a long answer: the composer may summarise. Pick some advisory questions that ask for detail ("explain step by step…"), so the stream is long. The report's composer output tokens and `claim.delta` count show whether it was.
- [ ] The advertised advisory topics must cover the 10 advisory questions (pests, nutrients, sowing, irrigation, weeds, …).
- [ ] Tier-1 test: loader returns 30 rows, 10 per category, unique ids.
- [ ] Tier-1 test: an unknown `--lang` fails with a clear message naming the known languages.

### Task 5: SSE parser and stats (pure)
- [ ] `parse_sse(lines) -> Iterator[tuple[str, dict]]`. Test: two frames split across lines give two events.
- [ ] `summarise([...]) -> Summary(p50, p95, max, n)`. Tests: known values; one value; an empty list gives `n=0` with no crash.

### Task 6: Drive one turn
**Interfaces:** `async run_turn(client, base_url, question, session_id) -> TurnTiming(status, first_delta_s: float | None, total_s: float | None)`. Uses `perf_counter` and `client.stream("POST", ..., Accept: text/event-stream)`.
- [ ] Tier-2 test against a tiny local ASGI app that emits frames with sleeps: `first_delta_s < total_s`, status `answered`.
- [ ] Tier-2 test: no `claim.delta` (`no_match`) → `first_delta_s is None`.
- [ ] Tier-2 test: `turn.failed` → status `unavailable`.

### Task 7: Read the trace from Langfuse
**Interfaces:** `async fetch_trace(client, session_id, deadline_s) -> TraceFacts | None`, plus a pure `to_trace_facts(observations) -> TraceFacts(stages: dict[str, float], calls: list[TokenCall(agent, input, output)], models: dict[str, str], flat: bool)`.
- [ ] Spike first: run one turn locally and save the real observations JSON for Langfuse v4 as `tests/fixtures/langfuse/observations.json`. The endpoint and field names are confirmed here, not guessed. Scrub the question text first.
- [ ] Tier-1 tests on `to_trace_facts`: stage times per `dss.stage.*`, `dss.discover`, `dss.select`; tokens only from `chat` spans; models from `dss.turn`; `flat=True` when a stage's parent isn't `dss.turn`.
- [ ] Tier-2 test with `pytest-httpserver` serving the fixture: polling returns facts. A server that never has the trace → `None` after the deadline.

### Task 8: Runner, report, CLI
**Interfaces:** `async run(questions, opts) -> RunResult`; `render_text(RunResult) -> str`; `write_json(RunResult, dir) -> Path`.
- [ ] Tier-1 (fakes for turn / trace / miss count): warm-up turns are not in the figures.
- [ ] A miss turn becomes `replay_miss`, is counted, and is left out.
- [ ] `--max-turns` stops the run and sets `truncated=True`.
- [ ] The report has p50 / p95 / max for first delta, total, each stage, and tokens per agent call, plus the four models and the commit (`git rev-parse HEAD`, and `dirty` when the tree has changes).
- [ ] The JSON holds the same figures plus the raw per-turn rows.
- [ ] The report and JSON record the machine: `platform.platform()`, the CPU model, `os.cpu_count()`, and the Python version. The text report says: "compare runs on the same machine, made close together."
- [ ] `__main__.py` flags: `--base-url`, `--mock-url`, `--langfuse-url`, `--lang en`, `--warmup 2`, `--repeats 3`, `--max-turns`. Langfuse keys come from the env vars `run-local.sh` already exports.

### Task 9: Load mode
**Interfaces:** `async run_load(questions, steps: list[int], opts) -> list[StepResult]`, where `StepResult(concurrency, turns, wall_s, turns_per_min, first_delta: Summary, total: Summary, by_status: dict[str, int])`. It reuses `run_turn`, `fetch_trace` and `summarise`.
- [ ] Tier 1 (fake `run_turn` with a set delay): at N=4, never more than 4 turns are in flight at once. Use an `anyio` capacity limiter or task group, and count the peak in the fake.
- [ ] Each step runs all 30 questions once, whatever N is. So the cost is known up front: 30 × number of steps.
- [ ] `turns_per_min` = completed turns ÷ step wall time.
- [ ] `unavailable` and `turn.failed` are counted per step, not hidden. A provider 429 under load shows up here.
- [ ] `--max-turns` also caps load mode.
- [ ] Tier 2, `container.py`: with a fake `docker` command on `PATH`, the start call passes `--cpus 1 --memory 1g`, and the limits read back go into the result. No real Docker in tests.
- [ ] The CLI subcommand `load` takes `--concurrency 1,2,4,8`, `--cpus 1`, `--memory 1g`. The container reaches the mock and Langfuse on the host through `host.docker.internal`. The LLM keys are passed through as env vars, never through a file.
- [ ] The report prints one row per step: N, turns/min, first-delta and total p50/p95/max, status counts. Under the table it prints the CPU and memory limits and the machine facts.

### Task 10: Dry run, sizes, docs
- [ ] Start the stack with `scripts/run-local.sh`, run `uv run python -m evals.perf --repeats 1 --warmup 0`, and read the result. Every turn should be `answered` with 0 misses. Fix the questions or matching until it is.
- [ ] Set the default advisory and weather sizes from real answers. Ask the user for one real advisory and one real weather `/select` answer; don't guess. Mandi is settled: one price.
- [ ] Dry run of load mode: `uv run python -m evals.perf load --concurrency 1,2`. Check that the container's limits show in the report.
- [ ] ADR-0013: `evals/` folder, generated fakes, client headline times plus Langfuse stages, a size knob limited to one resource, a load mode with real LLM and closed-loop steps, and a 1 CPU / 1 GiB pin.
- [ ] Propose new acceptance items on #162 for load mode (steps, turns/min, pinned container). Show the text and wait before editing the tracker.
- [ ] `CLAUDE.md` tier table: tier-6 speed runs live in `evals/`, not `tests/`.
- [ ] `DSS_ARCHITECTURE.md`: a short note on the benchmark. `RUNNING.md`: how to run it, and the fixed mock validation claim.
- [ ] Run `uv run pytest` and `uv run ruff check .`. Show the output. Wait to be asked before committing.

## Verification
- `uv run pytest` is green and `uv run ruff check .` is clean.
- The full run: `scripts/run-local.sh` in one shell, then `uv run python -m evals.perf` in another. Expect 90 timed turns, 0 `replay_miss`, a printed table, and a JSON file under `var/evals/perf/`.
- Open one trace in Langfuse and check the tree is not flat and the stage times look sane next to the client total.
- Load: `uv run python -m evals.perf load` gives 4 step rows (N = 1, 2, 4, 8) and shows 1 CPU / 1 GiB under the table. Expect turns/min to rise with N until the CPU or the provider limit is hit.

## Not in this plan
- The nightly CI job and its history (#163). CI will use one fixed runner type, so its runs compare with each other.
- The token gate (#164).
- Answer quality.
- Fixed-arrival-rate (open-loop) load. Its cost has no upper limit with a real LLM.
- A fake LLM for load testing.
- GPU pinning, which belongs with a self-hosted model later.
