# oan-evals — proposed repo structure

**Status:** draft for discussion
**Date:** 2026-11-27
**Architecture:** [Eval Framework Design](https://claude.ai/artifact/3Cq3vGF8xmQcdDzy6qfRkz) (the diagram this document implements)
**Source repo being transformed:** `repos/oan-evaluation` (branch `common_evals_dev`)
**Goal:** one framework that marks any system the same way, so we can compare X (Bh‑Vistaar, "legacy") against Y (DSS), and one DSS variant against another.
**Related:** `docs/rough/dss-eval-framework-plan.md` (judge catalogue, deterministic checks), `docs/rough/dss-eval-implementation-plan.md` (smoke / e2e / production suites). Those two were DSS‑only; this document is the system‑neutral version the diagram asks for.

---

## 1. The one idea the diagram fixes

Everything inside the framework boundary reads and writes exactly one thing: **the Record** — one JSON object per turn holding the answer, the calls made, and the timings. Adapters produce it; metrics consume it; files keep it; Langfuse gets a copy.

The existing `oan-evaluation` repo is already *almost* this. `orchestrate_eval.py` scores a JSON row with plug‑in metrics and never calls the bot; `inference/` calls the bot and never scores. The two halves are just not joined by a declared contract, and the "row" is shaped by whatever CSV came out of the legacy bot. The transformation is therefore mostly **renaming, moving, and declaring the contract**, not rewriting. Section 4 lists what moves where, file by file.

Three rules from the diagram drive every choice below:

| Rule | Consequence for the repo |
|---|---|
| The framework never names a system | `framework/` has no "legacy", "DSS", "Bh‑Vistaar", tool names, or endpoint URLs. All of that lives in `adapters/`. |
| Metrics read the record only | Every metric takes a `Record`, not `row_json / transformed_json / variables`. Anything a metric needs, the adapter must put in the record. |
| Rules are config, not code | Which metrics run, which judge marks them, pass lines and margins are YAML under `rules/`. No more `pipeline_config_*.py`. |

---

## 2. Proposed layout

```
oan-evals/
├── README.md
├── pyproject.toml                 # uv; pydantic, pydantic-ai, httpx, jinja2, pyyaml, tqdm
│
├── rules/                         # "Rules — kept in config, not code"
│   ├── judges.yaml                #   judge profiles: model, base_url, temperature, pricing
│   ├── metrics.yaml               #   which metrics run, which judge profile each uses
│   ├── thresholds.yaml            #   per-metric pass line, direction, margin, release-blocking?
│   └── suites/                    #   named picks of questions from the pool
│       ├── smoke.yaml
│       ├── regression.yaml
│       └── release.yaml           #   held back (open item in the diagram)
│
├── pool/                          # "Pool — every question, once; personal details removed"
│   ├── pool.jsonl                 #   id, text per language, category, expected_status, tags
│   ├── answer_key.jsonl           #   "One data set — holds the right answers" (empty today, see §7)
│   └── README.md                  #   provenance + the PII rule
│
├── mockdata/                      # the one mock data set both doors read
│   ├── mandi/                     #   prices by commodity × market
│   ├── weather/                   #   forecasts by place
│   ├── schemes/                   #   PM-KISAN, PMFBY, MahaDBT… scheme info + status fixtures
│   ├── advisory/                  #   crop advisories
│   └── grievance/                 #   tickets, OTPs
│
├── framework/                     # knows no system
│   ├── record.py                  #   Record, Turn, Call, Timings  (pydantic, frozen)
│   ├── score.py                   #   Score, Verdict, Decision
│   ├── pool/
│   │   ├── loader.py              #   read + validate pool.jsonl
│   │   └── suites.py              #   apply a suite rule → list of questions
│   ├── runner/
│   │   ├── ask.py                 #   drive an adapter over a suite → runs/<id>/records.jsonl
│   │   ├── mark.py                #   run metrics over records     → runs/<id>/scores.jsonl
│   │   └── simulated_user.py      #   (later) LLM plays the farmer for multi-turn follow-ups
│   ├── metrics/
│   │   ├── base.py                #   BaseMetric.evaluate(record, rules) -> MetricResult
│   │   ├── registry.py            #   name → class; "a new metric is one class + one line in rules"
│   │   ├── judged/                #   one file per LLM-judged metric
│   │   └── rules/                 #   deterministic, free: status, language, latency, length…
│   ├── judge/
│   │   ├── client.py              #   one call to any OpenAI-compatible / Anthropic judge
│   │   ├── prompts.py             #   Jinja render; resolves prompts/<metric>/<version>.md
│   │   ├── run.py                 #   render → call → parse JSON → Score (+ cost, pinning)
│   │   ├── cost.py
│   │   └── prompts/
│   │       ├── <metric>/v1.md     #   versioned; the version + sha go into every Score
│   │       └── overlays/agri/     #   domain context prepended to a prompt (system-neutral!)
│   ├── report/
│   │   ├── collate.py             #   scores.jsonl → wide table
│   │   ├── report_card.py         #   two runs → per-metric delta, n, margin → verdict
│   │   ├── verdict.py             #   pass · fail · ask-a-person, from thresholds.yaml
│   │   └── decisions.py           #   record a person's call on "cannot tell" items
│   ├── sinks/
│   │   ├── base.py                #   Sink protocol: put_run / put_records / put_scores / put_report
│   │   └── files.py               #   the truth, kept by us
│   └── cli.py                     #   oan-evals ask | mark | report | decide | push
│
├── adapters/                      # everything that names a system, a provider API, or a tool
│   ├── base.py                    #   Adapter protocol: ask(question, session) -> Record
│   │                              #   Door protocol:   serve(mockdata) + calls() ledger
│   ├── legacy/                    #   X — Bh-Vistaar (bharat-oan-api / mh-oan-api family)
│   │   ├── adapter.py             #   GET /api/chat/ streaming, bearer token, session_id
│   │   ├── langfuse_calls.py      #   tool calls from Langfuse TOOL observations (fallback)
│   │   └── door/                  #   Beckn BPP mock: search proxy, grievance, mandi, SATHI
│   ├── dss/                       #   Y — decision-support-system
│   │   ├── adapter.py             #   POST /v1/turns (JSON or SSE), sessionId + UUID transactionId
│   │   └── door/                  #   /discover /select /init /support /status mock
│   └── langfuse/
│       └── sink.py                #   "Langfuse plug": traces via OTel, scores + report via Scores API
│
├── runs/                          # gitignored
│   └── <run_id>/
│       ├── manifest.json          #   variant {system, version, config}, suite, adapter sha, started_at
│       ├── records.jsonl          #   one Record per turn
│       ├── scores.jsonl           #   one Score per (turn, metric)
│       ├── decisions.jsonl        #   append-only human calls on "ask a person"
│       └── report.md / .json      #   produced by `report` when this run is A or B
│
├── calibration/                   # (later) human labels per judge, κ against the judge
└── tests/
    ├── unit/                      # framework + metrics, with fixture records — no network
    └── contract/                  # each adapter against its door — proves the Record contract
```

**Naming note.** "Legacy" is what the diagram calls X. If the team prefers the product name, call the folder `adapters/bharat_vistaar/`. Nothing under `framework/` cares.

---

## 3. The contracts

### 3.1 Record — written by adapters, read by metrics

```python
class Call(BaseModel):            # one provider / tool call the system made
    name: str                     # tool name (legacy) or capability @type (DSS)
    args: dict | None
    output: Any                   # what came back — this IS the retrieved context
    latency_ms: float | None
    error: str | None = None

class Turn(BaseModel):            # one prior exchange, for multi-turn context
    question: str
    answer: str

class Record(BaseModel):
    run_id: str
    question_id: str
    session_id: str
    turn_index: int               # 0-based within the session
    variant: Variant              # {system, version, config} — the only place a system is named, as data
    # input
    question: str
    language: str                 # target language code ("hi", "mr", "en"…)
    category: str | None          # from the pool, not hard-coded
    prior_turns: list[Turn]
    # output
    answer: str
    status: Literal["answered", "unavailable", "no_match", "refused", "clarify", "error"]
    sources: list[str]            # citations the answer carried, if any
    calls: list[Call]
    # measurement
    ttft_ms: float | None
    total_ms: float | None
    tokens_in: int | None
    tokens_out: int | None
    error: str | None
    trace_id: str | None          # Langfuse / OTel trace, if known
    raw: dict                     # adapter-specific response, kept for debugging only
    captured_at: datetime
```

Why these fields and not more: this is the exact set the existing judge prompts already consume. Today `build_context()` in `metrics/llm_metric_utils.py` hands the prompts `question, answer, category, output_language, retrieved_sources, tool_calls_raw, agent_turns`. Every one of those is derivable from `Record` with no information loss:

| Prompt variable today | From `Record` |
|---|---|
| `question` | `question`, with `prior_turns` rendered above it when `turn_index > 0` (replaces `_collapse_session`) |
| `answer` | `answer` |
| `category` | `category` |
| `output_language` | `language` |
| `retrieved_sources` | `"\n---\n".join(str(c.output) for c in calls)` |
| `tool_calls_raw` | `calls` |
| `agent_turns` | derived from `calls` (tool-call / tool-return pairs) — only `ToolSequencingMetric` ever used it; drop with that metric |

So the six prompts that run today keep working on day one.

### 3.2 Score — one per (turn, metric)

```python
class Judge(BaseModel):           # pinned, saved with every score
    profile: str                  # key in rules/judges.yaml
    model: str                    # resolved model id
    prompt_version: str           # "v1"
    prompt_sha256: str            # of the rendered template file
    temperature: float

class Score(BaseModel):
    run_id: str
    question_id: str
    turn_index: int
    metric: str
    value: float | None           # normalised to [0, 1], higher is better — report card uses this
    raw: float | int | str | None # what the rubric actually returned (1–4, 0/1, 1–10…)
    label: Literal["pass", "fail", "unsure"]   # from thresholds.yaml; "unsure" → ask a person
    reason: str
    judge: Judge | None           # None for rule-based metrics
    usage: dict
    cost_usd: float | None
    elapsed_s: float
    error: str | None
```

Normalising `value` to [0, 1] is the one change to the metric outputs that the existing code does not do. Without it the report card cannot put a 1–4 rubric next to a 0/1 one. `raw` keeps the original so nothing is lost.

### 3.3 Adapter and Door protocols

```python
class Adapter(Protocol):
    name: str                                            # "legacy", "dss"
    async def ask(self, q: PoolQuestion, session: Session, variant: Variant) -> Record: ...

class Door(Protocol):                                    # the mock a system's provider calls hit
    def start(self, mockdata: Path) -> None: ...
    def calls(self, since: datetime) -> list[Call]: ...  # the ledger — what was actually fetched
    def stop(self) -> None: ...
```

The `Door.calls()` ledger matters more than it looks. The DSS's retrieved evidence never reaches the wire (`TurnFinished` carries the answer and sources, not the provider payloads), and the legacy bot's tool outputs are only recoverable by reading Langfuse after the fact. If each door records what was asked of it and what it returned, **both adapters get `Record.calls` the same way, from the same place**, and the no‑fabrication / citation metrics compare like with like. The Langfuse read in the legacy adapter becomes a fallback, not the path.

---

## 4. Reuse map — what moves from `oan-evaluation`

Legend: **keep** = move with light edits · **reshape** = same logic, new contract · **drop** = not carried over.

| Today (`oan-evaluation`) | Tomorrow (`oan-evals`) | Action | Notes |
|---|---|---|---|
| `metrics/base.py` — `MetricResult`, `BaseMetric` | `framework/metrics/base.py` | reshape | `evaluate(record, rules)` instead of four dicts. `MetricResult` → `Score` (adds `value`, `raw`, `label`, `judge`). |
| `metrics/llm_metrics.py` — the 20 `_LlmMetricBase` subclasses | `framework/metrics/judged/<name>.py` | keep | Pure declarations (`metric_name` + `prompt_file`). Add a `normalise(raw) -> float` per rubric scale. |
| `metrics/llm_metrics.py` — `*CompareMetric` (A vs B in one prompt) | — | drop for v1 | The diagram compares two *runs* on their scores, not two answers in one prompt. Pairwise judging breaks "metrics read the record only". Revisit as a report‑card option if pass/fail deltas prove too coarse. |
| `metrics/llm_metrics.py` — `ElapsedSeconds`, `TTFB`, `WordCount`, `TokenUsage*`, `Error` | `framework/metrics/rules/` | reshape | Read `Record.total_ms`, `ttft_ms`, `answer`, `tokens_*`, `error`. These are the "four measured numbers" candidates (§6). |
| `metrics/llm_metrics.py` — `AgristackWorkflow`, `ToolSequencing`, `SearchQuality`, `OutputHygiene` | — | drop | Keyword checks on serialised JSON (`"tool-call" in text`). Not meaningful, and two of them name legacy tools. |
| `metrics/llm_metrics.py` — `LanguageMixingMetric` + `helpers/language_utils.py` | `framework/metrics/rules/language.py` | keep | Script‑based, free, system‑neutral. Becomes the "language compliance" check. |
| `metrics/llm_metric_utils.py` — `run_llm_metric`, `parse_json_result`, `estimate_cost`, `build_context` | `framework/judge/run.py`, `cost.py` | reshape | `build_context` becomes `Record → template context` (table in §3.1). Add judge pinning: model id + prompt sha into `Score.judge`. |
| `helpers/llm_helper.py` — `llm_call` via pydantic‑ai | `framework/judge/client.py` | keep | Already handles OpenAI and any OpenAI‑compatible `base_url` (vLLM Gemma). Add Anthropic via pydantic‑ai's provider; the diagram lists all three. |
| `helpers/prompt_helper.py` — Jinja `render_prompt`, `try_render_prompt` | `framework/judge/prompts.py` | keep | Change the lookup from `<prompt_dir>/<file>` to `prompts/<metric>/<version>.md`. |
| `assets/prompts/metrics/*.md` (base rubrics) | `framework/judge/prompts/<metric>/v1.md` | keep | Verified: base prompts contain no system or tool names. |
| `assets/prompts/metrics/bv_specific/*.md` | `framework/judge/prompts/overlays/agri/*.md` | reshape | **These name legacy tools and products** (`search_terms`, `search_documents`, `mahadbt`, `Agristack` in `tool_call_quality`, `no_fabrication`, `source_data_comprehensiveness`, `context_fit`). Each needs a rewrite in domain terms ("the scheme‑information lookup", "the farmer's land record") before it can sit inside the framework, or the DSS gets judged against the legacy bot's tool vocabulary. |
| `assets/prompts/metrics/comparison_metrics/` | — | drop for v1 | Goes with the compare metrics. |
| `assets/prompts/evaluation_system.md`, `assets/prompts/category/*.md`, `evaluation/evaluator*.py`, `metrics/agri_eval_metrics.py` | — | drop | The old single‑call 16‑sub‑dimension judge. Marathi‑specific, legacy‑tool‑specific, only reachable via `pipeline_config_legacy`. Archive in git history. |
| `orchestrate_eval.py` — concurrency semaphore, periodic checkpoint, `fail_open`, cost summary, progress bars | `framework/runner/mark.py` | reshape | The scheduling core is good; keep it. Input becomes `records.jsonl`, output `scores.jsonl`. `fail_open` failures must produce a `Score` with `error` set and `label="unsure"` so they are visible, not silently `None`. |
| `config/pipeline_config.py` — `normalize_llm_profiles`, metric↔profile binding | `rules/judges.yaml`, `rules/metrics.yaml` + a tiny loader | reshape | Same data, YAML. The `LLM_PROFILES` dict in `pipeline_config_scenario.py` is already the right shape for `judges.yaml`; fix the misleading keys (`"gpt-5.4-mini"` → `gpt-5.1`). |
| `config/pipeline_config_scenario.py` metric list | `rules/metrics.yaml` | reshape | The six enabled metrics become the v1 default. |
| `config/pipeline_config_goldenset_*`, `_legacy`, `_compare` | — | drop | Their inputs (`data/models/goldenset_*/data.json`) are not in the repo. |
| `inference/run_sequential_scenario.py` — `stream_chat_answer`, session grouping, `fetch_langfuse_tool_calls`, bounded concurrency, periodic CSV save | `adapters/legacy/adapter.py`, `adapters/legacy/langfuse_calls.py`; the concurrency/save loop → `framework/runner/ask.py` | reshape | This *is* the legacy adapter. Split it: the HTTP call and the Langfuse read are legacy‑specific; the loop over sessions and questions is not. |
| `inference/run_simulated_scenario.py` — Gemma plays the farmer | `framework/runner/simulated_user.py` | keep (later) | System‑neutral once it talks through an `Adapter`. Not needed for v1. |
| `inference/run_text_inference.py`, `run_audio_vs_text_comparison.py` | — | drop | Superseded by the sequential runner; audio fixture never migrated. |
| `inference/benchmark_questions.csv` (11 languages, categories), `mh_evaluation_questions.csv`, `per_lang_csvs/*` | `pool/pool.jsonl` | reshape | Seed the pool. `mh_evaluation_questions.csv` carries 11 distinct `farmer_id`s — check against the "six farmer ids" open item and strip before entry. |
| `data_transformation/transform_per_lang_csv.py` — session collapse, tool‑call expansion, hard‑coded `category="General"`, `agristack_required="Yes"`, language‑from‑filename | — | drop | Replaced by `Record` + `prior_turns`. The three hard‑codings disappear because the pool carries category and language. |
| `data_transformation/collate_all_models.py`, `flatten_evaluation_to_csv.py` | `framework/report/collate.py` | reshape | Same wide‑table idea over `scores.jsonl`; keyed by `run_id` not `model_name`. |
| `helpers/total_cost_calculator.py` | `framework/report/cost.py` | keep | |
| `mock_server/beckn_search_proxy/`, `vistaar_grievance_mock/`, `vistaar_mandi_mock/`, `vistaar_sathi_mock/` | `adapters/legacy/door/` | reshape | Already the legacy door. Change: read fixtures from `mockdata/` instead of their own `registry.json` / inline data, and add the `calls()` ledger. |
| `decision-support-system/tools/mock_network/` (`/discover /select /init /support /status`, catalogs in `responses/`) | `adapters/dss/door/` | reshape | The DSS door. Either vendor it here or depend on the DSS repo at a pinned sha; either way it must read `mockdata/`, not its own `responses/`. This is the only change we need inside the DSS repo and should be raised with that team (§8). |
| `scripts/run_batch_eval.sh` | `framework/cli.py` | reshape | `ask` / `mark` / `report` subcommands instead of a shell loop. |
| `notebooks/`, `temp/`, `src/` (`datautils`, `tasks` — Langfuse dataset upload), `assets/vllm.md`, `assets/docs/` | — | drop | Exploration and one‑offs. |
| `CLAUDE.md` / `AGENTS.md` pointers to `/mnt/raid/gautam/...` | new `README.md` | drop | Those paths exist only on Gautam's server. |

Roughly: **~70 % of the scoring side moves with light edits**, the inference side becomes one adapter, and the transform script and the old evaluator are the only real deletions.

---

## 5. How a run goes (the diagram's Ask → Mark → Decide)

```bash
# Ask — one run per variant. Both hit doors fed from mockdata/.
oan-evals ask --adapter legacy --variant "legacy@prod"         --suite smoke --out runs/2026-11-27_legacy
oan-evals ask --adapter dss    --variant "dss@0.4+config=base" --suite smoke --out runs/2026-11-27_dss

# Mark — offline, re-runnable; never touches the systems again.
oan-evals mark runs/2026-11-27_legacy --rules rules/metrics.yaml
oan-evals mark runs/2026-11-27_dss    --rules rules/metrics.yaml

# Decide — two runs in, one report card out. Only metrics both runs filled.
oan-evals report runs/2026-11-27_legacy runs/2026-11-27_dss --thresholds rules/thresholds.yaml
oan-evals decide runs/2026-11-27_dss         # walk the "unsure" items, save a person's call
oan-evals push   runs/2026-11-27_dss --sink langfuse
```

Separating `ask` from `mark` is the single biggest cost saver and the existing repo already does it: adding a metric or fixing a judge prompt re‑scores old records without a single new call to either system.

**Multi‑turn.** Both systems keep history server‑side (`session_id` on `/api/chat/`, `sessionId` on `/v1/turns`), so the runner sends one question per turn and records one `Record` per turn with `prior_turns` filled in. The old "collapse the session and score only the last answer" goes away; every turn is scored, and the report can still roll up by session.

---

## 6. Rules files — what goes in them

`rules/judges.yaml` (lifted from `LLM_PROFILES`):

```yaml
vllm-gemma:
  model: ${JUDGE_VLLM_MODEL}          # no default on purpose — shared GPU rule from the old README
  base_url: ${JUDGE_VLLM_BASE_URL}
  api_key: dummy
  temperature: 0.0
  timeout_s: 120
  retries: 3
  cost_per_1m: {input: 0, output: 0}
gpt-5-1:
  model: gpt-5.1
  temperature: 0.0
  cost_per_1m: {input: 1.25, output: 10}
```

`rules/metrics.yaml` — the v1 set is the six that run today plus the free checks:

```yaml
judged:                      # "the seven judges" — proposal; the diagram leaves them open
  - {metric: accuracy_completeness,        judge: vllm-gemma, prompt: v1}
  - {metric: source_data_comprehensiveness, judge: vllm-gemma, prompt: v1}
  - {metric: no_fabrication,               judge: vllm-gemma, prompt: v1}
  - {metric: citation_accuracy,            judge: vllm-gemma, prompt: v1}
  - {metric: translation_accuracy,         judge: vllm-gemma, prompt: v1}
  - {metric: tool_call_quality,            judge: vllm-gemma, prompt: v1}
  - {metric: safety_compliance,            judge: vllm-gemma, prompt: v1}   # exists, currently off
rules:                       # "the four measured numbers" — proposal
  - status_expected          # Record.status == pool.expected_status
  - language_compliance      # LanguageMixingMetric
  - ttft_ms
  - total_ms
```

`rules/thresholds.yaml` — one line per metric, read by `verdict.py` and `report_card.py`:

```yaml
no_fabrication:        {pass_at: 1.0,  unsure_below: null, blocks_release: true}
accuracy_completeness: {pass_at: 0.75, unsure_band: [0.5, 0.75], blocks_release: false}
total_ms:              {direction: lower, pass_at: null}     # open: "the speed number that would stop a release"
report_card:
  min_margin: 0.05           # a delta smaller than this is "no change"
  min_n: 100                 # open: pool is too small today to prove a 5-point margin
```

A new metric is one class in `framework/metrics/` plus one line here — exactly the diagram's rule.

---

## 7. Where this is honest about gaps

1. **There is no answer key yet.** `oan-evaluation` has no golden answers anywhere (checked: tracked CSVs are questions only; the goldenset configs point at files that are not in the repo). `pool/answer_key.jsonl` starts empty. Any metric that needs it (correctness vs expected, required‑fact recall) is "cannot fill" and simply drops out of the report card until the key exists. The six v1 judges do not need it — they judge the answer against the retrieved calls — which is why they can run on day one.
2. **`tool_call_quality` and `citation_accuracy` are shaped by what each system exposes.** The legacy bot exposes named tool calls; the DSS exposes sources plus provider calls. Defining both on `Record.calls` from the door ledger makes them comparable, but the first few report cards should be read with that in mind.
3. **The overlay prompts must be rewritten before the DSS is judged with them.** See the `bv_specific` row in §4. Until then, run the base prompts only (`overlays: none` in `metrics.yaml`).
4. **Judge bias.** If the DSS composer and the judge are the same model family, the judge will favour it. `judges.yaml` should carry a `not_for_variants:` guard or we agree a judge family up front.
5. **Rubric scales differ** (0/1, 1–4, 1–10). The `normalise()` per metric is small but must be written and agreed, because it decides what "a 5‑point margin" means.
6. **Shared `mockdata/` is new work**, not reuse. Today the legacy mock and the DSS mock each have their own fixtures with different facts. Reconciling them (same potato price at the same mandi, same forecast for Nashik) is a prerequisite for a fair comparison and is the one piece with no existing code to lift.

---

## 8. Dependencies on the OAN team (feeds ticket #260)

| Need | Why | Owner (proposed) |
|---|---|---|
| PII‑cleaned real questions from legacy traffic, with category and language | Fills the pool; the diagram requires PII removal before entry | OAN data / legacy team |
| An owner and refresh cadence for `answer_key.jsonl` | Without it the correctness metrics never switch on | OAN product |
| DSS `tools/mock_network` able to read fixtures from an external `mockdata/` path | Both doors must serve the same facts | DSS team (small change) |
| Confirmation that `POST /v1/turns` + `outcome.status` set is stable | The DSS adapter and `Record.status` map onto it | DSS team |
| Legacy API bearer token, Langfuse keys, and a judge endpoint we are allowed to use | Adapters and judge client | Infra |
| The speed threshold that blocks a release | `thresholds.yaml` has a hole where it goes | OAN product |
| Who adjudicates "ask a person" items, and where decisions are reviewed | `decisions.jsonl` needs a human behind it | OAN product |
| Whether Langfuse can hold the report‑card verdict as a score on a run | Decides if the Langfuse plug writes one score or a markdown comment | Us to check, DSS team to confirm their Langfuse version |

---

## 9. Build order

| Phase | What ships | Proves |
|---|---|---|
| 0 — skeleton (1–2 d) | Repo, `Record`/`Score` models, `rules/*.yaml`, metrics + judge code moved under the new contract, unit tests on fixture records | The scoring side still works after the move |
| 1 — legacy end‑to‑end (3–4 d) | `adapters/legacy/` (adapter + door with ledger), `ask` + `mark` CLI, the six judges on a 20‑question smoke suite | **Parity:** scores match what `oan-evaluation` produces today on the same questions — this is the reuse check |
| 2 — DSS end‑to‑end (3–5 d) | `adapters/dss/` (adapter + door), shared `mockdata/` for mandi + weather + one scheme, same smoke suite | First X‑vs‑Y report card, on metrics both fill |
| 3 — decide (2–3 d) | `thresholds.yaml`, `verdict`, `decisions`, Langfuse sink | The Decide step of the diagram |
| 4 — grow (ongoing) | Pool to ≥100 per category, answer key, overlay prompt rewrite, judge calibration, regression suite, release suite | The margins in `thresholds.yaml` become provable |

Phase 1's parity check is the point of reusing rather than rewriting: if the moved code scores the legacy bot the same way it did before, every later difference in a report card is about the systems, not about the framework.
