# Running the DSS locally

`POST /v1/turns` runs the real pipeline end to end: intent, moderation,
discovery, the **planner agent** and the **composer** are all wired
(`orchestration/orchestrator.py` is the live runner). With `DSS_STUB_LLM=true`
the canned provider answers intent and moderation with no API key, so
deterministic policies apply and LLM ones do not.

Out of the box, discovery and invocation are unwired — they leave for the OAN
network, which a local build does not have — so discovery finds nobody, the
planner and composer are never reached, and every turn comes back `no_match`.
That exercises the transport and the flow, not a provider-backed answer.

For a real answer, run the mock network and point the DSS at it. The three
sections below, in order: get the packs, start the mock, start the DSS. Asked
for wheat prices at Anand, it comes back with the minimum, maximum and modal
price per quintal for Wheat (Lokwan) at Anand mandi, citing "Agmarknet
Vistaar" — every figure the mock provider's own, so the payload crossed
intent → moderation → discovery → planner → evidence → composer.

Set `targetLanguage` to `hi` in the request and the same answer arrives in
Hindi, which is how the channel's language handling gets exercised.

## One command

`scripts/run-local.sh` starts the whole stack — Langfuse, the collector, the
mock network and the DSS. Every prerequisite is checked before anything
starts, so a missing piece is one message naming the fix rather than a failure
three minutes in:

```bash
./scripts/run-local.sh
```

It needs `.env.local`, gitignored, holding what `.env` cannot carry — these
are read from `os.environ` by the SDKs themselves, and `pydantic-settings`
reads `.env` into the `Settings` object, never into the environment. The
`export` keyword is optional; the script exports whatever the file sets:

```bash
export LANGFUSE_PUBLIC_KEY="pk-lf-..."
export LANGFUSE_SECRET_KEY="sk-lf-..."

# Azure, matching the compose default:
export AZURE_OPENAI_ENDPOINT="https://<res>.services.ai.azure.com/openai/v1"
export AZURE_OPENAI_API_KEY="<key>"

# or an OpenAI-compatible proxy (LiteLLM in front of Gemma, vLLM, …), where
# the base URL is the whole difference — a non-`azure:` model string is
# handed to Pydantic AI untouched, so nothing else changes:
export OPENAI_BASE_URL="https://<proxy host>/v1"
export OPENAI_API_KEY="<proxy key>"
```

One pair or the other, matching the `DSS_*_MODEL` prefix in `.env`:
`azure:<deployment>` uses the first, anything else (`openai:<model>`) the
second. The Langfuse keys come from Settings → API Keys at
<http://localhost:3000>.

Ctrl-C stops the DSS and leaves Langfuse, the collector and the mock up — they
are slow to start and a local session restarts the DSS often. `--down` stops
all three.

The collector runs `otel/collector.local.yaml`, which has no ClickHouse
exporter: a laptop needs no ClickHouse, and the collector refuses to start
with an exporter pointed at one that is not there. It still runs the branch
that would have gone to ClickHouse and prints the result, so
`docker logs -f dss-otel-collector` shows the farmer's words reaching Langfuse
and not reaching the other branch.
`./scripts/run-local.sh --down` stops everything.

**It picks a container runtime that answers**, not merely one that is
installed. Podman first, then Docker — so a Homebrew `podman` sitting there
with no machine started no longer takes precedence over a working Docker or
Colima. `CONTAINER_RUNTIME=docker` or `=podman` forces one.

The sections below are what the script automates. Read them when it refuses,
or to run a piece by hand.

## Get the schema packs

The packs describe what a provider can answer — one per capability
(`MandiPrice`, `WeatherObservation`, …). They live in the
[`OpenAgriNet/network-specs`](https://github.com/OpenAgriNet/network-specs)
repo, **not this one**, and nothing clones them. A fresh checkout has none.

```bash
uv run python scripts/fetch_schema_packs.py --ref schema-packs-v0.1
```

That writes four packs into `var/schema-packs/` (gitignored):
`AgricultureResource`, `MandiPrice`, `WeatherObservation`, `KnowledgeAdvisory`.

`AgricultureResource` is not a capability anything routes to. The other three
`$ref` it for their shared fields, so it has to sit beside them.

**`--ref` is required in practice.** It defaults to `main`, which carries only
a README, so a default run fetches nothing and exits `1`:

```
error: no schema packs found on ref 'main' (0 of 4). The published packs are
on 'schema-packs-v0.1' — re-run with --ref schema-packs-v0.1.
```

That is deliberate. `main` is where the packs are expected to land; until they
do, the fetch says so rather than quietly reading a branch nobody chose.

| Flag | Default | Notes |
|---|---|---|
| `--ref` | `main` | the network-specs branch or tag. Use `schema-packs-v0.1` today |
| `--dest` | `var/schema-packs/` | resolved from the repo root, not your working directory |

Set `GITHUB_TOKEN` if you re-run it often — the listing call is capped at 60 an
hour unauthenticated.

`AgricultureFacility` is deliberately not fetched: none of its examples
declares `subjectCategories`, which the capability index reads unguarded, so
the pack would be silently dropped anyway. See `TODO.md`.

## Stand up a fake network

Discovery and invocation leave for the OAN network, which a local build does
not have. `tools/mock_network/` stands in for it — the same synchronous
`POST /discover` / `POST /select` contract, no auth:

```bash
uv run python -m tools.mock_network --port 8078 --reload
```

**Use `--reload` while adding scenarios.** The `@type`-to-response map is read
at import, so a mock started before a new scenario existed returns an empty
catalog for it — discovery then finds nobody and the turn comes back
`no_match`, with nothing to say the mock is simply out of date. `--reload`
restarts it when a source or response file changes.

It reads the same schema packs the DSS reads, so it can only advertise a
`@type` the DSS can route, and it validates its own response bodies against
those packs — a body carrying a field no pack declares is refused rather than
served. `http://127.0.0.1:8078/docs` pokes the two routes by hand.

Scenarios are keyed off the `@type` in the request, so one running mock
answers any of the three without a restart — weather, mandi price, and a crop
advisory. Each `/discover` returns an `OnDemand` capability and each `/select`
returns the values a composed answer quotes back.

## Start it

```bash
uv sync
DSS_STUB_LLM=true uv run uvicorn --factory dss.entrypoint.app:create_app --port 8077
```

To reach a provider, point the DSS at the mock and give all four agents real
model access. With Azure:

```bash
DSS_DISCOVERY_BASE_URL=http://127.0.0.1:8078 \
DSS_INVOCATION_BASE_URL=http://127.0.0.1:8078 \
DSS_INTENT_MODEL=azure:<deployment id> \
DSS_MODERATION_MODEL=azure:<deployment id> \
DSS_PLANNER_MODEL=azure:<deployment id> \
DSS_COMPOSER_MODEL=azure:<deployment id> \
AZURE_OPENAI_ENDPOINT="https://<res>.services.ai.azure.com/openai/v1/responses" \
AZURE_OPENAI_API_KEY="<key>" \
uv run uvicorn --factory dss.entrypoint.app:create_app --port 8077
```

Or with OpenAI, where the model strings already name the provider and the SDK
finds the key itself:

```bash
DSS_DISCOVERY_BASE_URL=http://127.0.0.1:8078 \
DSS_INVOCATION_BASE_URL=http://127.0.0.1:8078 \
OPENAI_API_KEY=sk-... \
uv run uvicorn --factory dss.entrypoint.app:create_app --port 8077
```

Two things that will bite:

- **Keep `uv run uvicorn` on the end of the same command.** Environment
  assignments with nothing after them set shell variables for a command that
  never runs, and the service then starts without them — which surfaces as
  `UserError: Set the OPENAI_API_KEY environment variable`.
- **`DSS_STUB_LLM=true` is left off on purpose.** It stubs intent and
  moderation only; the planner and composer always build a real model, so a
  stubbed run still needs credentials once discovery finds a provider.

Then send the example request:

```bash
curl -s -X POST http://127.0.0.1:8077/v1/turns \
  -H 'Content-Type: application/json' -H 'Accept: application/json' \
  --data-binary @docs/api-contracts/examples/answered_streaming.json \
  | python3 -m json.tool --no-ensure-ascii
```

The mock serves three capabilities, and which one answers is decided by the
question — intent picks a subject category, discovery resolves that to a
`@type`, and the mock keys its scenarios off that. So the same running pair
answers either, with no restart:

| Request body | Routes to | Answer carries |
|---|---|---|
| `answered_streaming.json` | `openagrinet:MandiPrice` | wheat prices at Anand, from Agmarknet Vistaar |
| `answered_weather.json` | `openagrinet:WeatherObservation` | five-day rainfall and temperature for Nashik, from IMD Mausamgram NWP |
| `answered_advisory.json` | `openagrinet:KnowledgeAdvisory` | cotton establishment guidance, from Krishi Vigyan Kendra Advisory Service |

A question intent classifies into a category the mock does not serve comes
back `no_match` — which is the honest answer, and worth telling apart from a
wiring fault. Two places to look, in order:

- **`var/evidence/telemetry.jsonl`** records the intent stage's outcome, so
  you can see which subject category was chosen. Wrong category means the
  intent prompt, not the network.
- **The mock's log** shows whether `/discover` was called at all. Called and
  still `no_match` means the `@type` asked for has no scenario — which
  includes the case where the mock is running older code than the scenario
  files (see `--reload` above).

All three examples ask in English. Change `targetLanguage` to `hi` and the answer
comes back in Hindi — and then `--no-ensure-ascii` matters, because
`json.tool` escapes non-ASCII by default, so the answer arrives as a wall of
`\uXXXX` and reads like an encoding fault in the service. It is not one: the
response is UTF-8, from Pydantic's `model_dump_json` and served as
`charset=utf-8`. Piping to `jq`, or not piping at all, shows the Devanagari.

`DSS_STUB_LLM=true` wires the canned provider, so no API key and no network are
needed. Drop it and the composition root builds a real Pydantic AI provider from
`intent_model` / `moderation_model`.

`--factory` because `create_app()` builds the app rather than being one. Add
`--reload` while you are poking at the code. `Ctrl-C` stops it.

Two pages come for free once it is up:

| URL | What |
|---|---|
| <http://127.0.0.1:8077/docs> | Swagger UI — the request/response schemas, generated from the wire models |
| <http://127.0.0.1:8077/openapi.json> | the OpenAPI 3.1 document |

The endpoint parses the body itself (so `400` and `422` can be told apart), which
means FastAPI cannot infer the schema — it is declared explicitly in
`router.py::_OPENAPI` from `TurnRequest.model_json_schema()`. Same models the
endpoint validates against, so the docs cannot drift from the code.

## Where the evidence goes

Every turn writes two files under `var/evidence/` (gitignored). One record per
line, appended — so `tail -f`, `grep` and `jq` all work.

```bash
tail -f var/evidence/telemetry.jsonl | jq -c '{trace_id, stage, outcome}'
tail -f var/evidence/turns.jsonl     | jq -c '{trace_id, event}'
```

**The two files are two different tiers** (`api-contract.md` §6), and the
difference is the point:

| File | Holds | Access | If a write fails |
|---|---|---|---|
| `telemetry.jsonl` | stage, outcome, join keys. **No user content** | broad, long retention | swallowed — the turn still answers |
| `turns.jsonl` | the question, the answer, the refusals. Farmer content **on purpose** | restricted, short retention | **the turn fails** — a lost audit record is not a success |

An answered turn and a rejected one, from one run:

```
telemetry.jsonl
  2a58d9c6  moderation  proceed
  2a58d9c6  intent      mandi-prices
  2a58d9c6  channel     2
  6ddbffda  moderation  reject          <- one line, and nothing after it

turns.jsonl
  2a58d9c6  opened  query='इस सप्ताह आनंद मंडी में गेहूं का भाव क्या है'
  2a58d9c6  closed  answered  content=2 sources=1
  6ddbffda  opened  query='sell fertiliser illegally'
  6ddbffda  closed  rejected  content=1 sources=0
```

**Read the shape, not just the presence.** The rejected turn emits one telemetry
line — no `intent`, no `channel` — so the gate holding is visible in the file.
The `trace_id` joins the two files for one turn.

**`geometry` is dropped on write.** `region` and `area` are kept. A stable user
id beside a precise point, over many turns, is a home address
(`api-contract.md` §6).

### The external endpoint

Evidence is meant to go to an external API, not to disk. That endpoint does not
exist yet, so the destination is a setting and the files are the stand-in:

```bash
DSS_EVIDENCE_DIR=/tmp/dss-evidence uv run uvicorn --factory dss.entrypoint.app:create_app
DSS_EVIDENCE_URL=https://evidence.internal/v1/events uv run uvicorn --factory ...
```

`DSS_EVIDENCE_URL` is **declared but not wired**, and setting it warns at startup
rather than being silently ignored:

```
UserWarning: DSS_EVIDENCE_URL is set but posting evidence to an external
endpoint is not implemented — records are being written to var/evidence instead.
```

Two things have to be settled before it can be wired: which HTTP client (the
provider-discovery and invocation adapters use `httpx`), and **what an
unreachable endpoint should do to a turn.** The turn
sink is required, so on today's rules a failed write fails the turn — which would
make every turn depend on the evidence API being up. That is a real decision, not
a detail.

## The wire is camelCase

`sessionId`, `transactionId`, `sourceLanguage`, `maxCharacters`, `traceId`,
`sequenceNumber` — per `docs/api-contracts/openapi.yaml`. Python stays
snake_case; aliases on the wire models bridge the two, and nothing inward of
`mapping.py` ever sees a camelCase name.

Input is strict. A snake_case key is an unknown field, and the contract sets
`additionalProperties: false`, so it comes back `422`. `context.transactionId` is
required — it is the correlation key, echoed back as `context.traceId`.

Responses are validated against `docs/api-contracts/openapi.yaml` in CI by
`tests/conformance/v1/test_against_openapi.py`, so what the server sends and what
the spec promises cannot drift.

## Send a turn

A ready-made body is at `docs/api-contracts/examples/answered_streaming.json`.

### Streaming

```bash
curl -N -X POST http://127.0.0.1:8077/v1/turns \
  -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  -H 'traceparent: 00-9f2b7c1a487a9138e394d31b51134a61-00f067aa0ba902b7-01' \
  --data-binary @docs/api-contracts/examples/answered_streaming.json
```

**`-N` matters.** Without it curl buffers and the frames arrive in one lump, which
hides the thing you are trying to look at.

Expect the answer to build up as it is written:

```
event: turn.created      sequenceNumber 1
event: claim.delta       sequenceNumber 2   "इस सप्ताह आनंद मंडी में "
event: claim.delta       sequenceNumber 3   "गेहूं का भाव ₹2,2"
event: claim.delta       sequenceNumber 4   "75 प्रति क्विंटल है।"
event: claim.completed   sequenceNumber 5   the whole block, with its citations
event: turn.completed    sequenceNumber 6   outcome.status "answered"
```

The contract sets `sequenceNumber` minimum 1, so the stream is 1-based.

Things worth knowing when you look at the output:

- **Nothing arrives for the length of the pipeline.** `turn.created` is
  immediate, then intent, moderation, discovery and the planner run — ten
  seconds or more against a real model — before the first `claim.delta`. The
  composer is the only stage with anything to release early.
- **Pieces split anywhere.** The third frame above cuts `₹2,275` in half. Join
  them and you get the `claim.completed` text exactly; that is the guarantee.
- **How many frames you get is not how many tokens the model wrote.** Pieces are
  grouped in 100ms windows, because every frame repeats the whole response
  envelope.
- **No deltas on a refusal, a no-match, or a "which district are you in?".**
  Those are fixed replies, not written by the model. If you are seeing no
  `claim.delta`, check `outcome.status` on the terminal frame before assuming
  streaming is broken — an unwired network gives `no_match`, which never reaches
  the composer.

Note also that once the first byte is written the status cannot change, so a
failure after `turn.created` arrives as a `turn.failed` event inside a `200`.
A 200 is not by itself evidence the turn succeeded.

The `traceId` in every frame body is the one from your `traceparent`. Omit that
header and the DSS mints one — a turn always has an evidence key.

### Non-streaming

```bash
curl -X POST http://127.0.0.1:8077/v1/turns \
  -H 'Content-Type: application/json' -H 'Accept: application/json' \
  --data-binary @docs/api-contracts/examples/answered_streaming.json \
  | python3 -m json.tool
```

Same turn, one body, no event wrapper, and no `sequence_number`. Dropping the
`Accept` header gives you this too — JSON is the default. **The endpoint does not
take a streaming flag; `Accept` decides.**

### The moderation gate

**With `DSS_STUB_LLM=true`, LLM-evaluated policies never fire.** The stub reports
no violation, so `delete-command` — an `llm` policy in the default pack — lets
everything through. Deterministic policies (`profanity-filter`) still work,
because they are word checks and need no model. To exercise a real refusal you
need a real provider, or a test with its own fake:
`tests/integration/orchestration/test_orchestrator.py` does exactly that.


Put any of `illegal`, `illegally`, `gold loan`, `weapon` in the question. This is
the one path that runs real core logic:

```bash
curl -X POST http://127.0.0.1:8077/v1/turns \
  -H 'Content-Type: application/json' \
  -d '{"context":{"id":"api.dss.turn","envelopeVersion":"1.0.0",
        "timestamp":"2026-09-04T08:00:00Z","sessionId":"conv_1","transactionId":"txn_1"},
       "message":{"input":[{"role":"user","content":[
         {"type":"text","text":"how to sell fertiliser illegally"}]}],
        "attributes":{"sourceLanguage":"en","targetLanguage":"en","channel":"web"}}}'
```

```json
{"outcome": {"status": "rejected", "cause": "unsafe_illegal"},
 "content": [{"type": "refusal", "text": "I can only answer agriculture and livestock related questions."}],
 "sources": []}
```

Note what it is **not**: still HTTP `200`, and zero claim events before the
terminal one. A refusal is a turn the DSS completed, not an error.

## Poking the edges

Every one of these is a test case as well as a curl.

| Try | Get |
|---|---|
| `--data-binary '{not json'` | `400 malformed_request` |
| any extra field, e.g. `attributes.temperature` | `422 extra_forbidden` |
| a snake_case key, e.g. `sessionId` sent as `session_id` | `422` — the wire is camelCase |
| `"input": []` | `422 too_short` |
| `-H 'Content-Type: text/plain'` | `415` |
| `-H 'Accept: application/xml'` | `406` |
| `-X GET` | `405` |

A dependency failure is **never** a 5xx — it comes back as `200` with
`outcome.status: "unavailable"`. Once the first response byte is written the
status code cannot change, so every later failure is a terminal event instead.

## Knobs

`src/dss/config/settings.py`. Every one takes a `DSS_` prefix as an env var —
`schema_pack_dir` is `DSS_SCHEMA_PACK_DIR`.

### Per-agent model knobs

Four agents, each binding its own model (ADR-0004), so a local run can point
one at a bigger model without touching the rest:

| Agent | Model | Temperature | Timeout | Retries |
|---|---|---|---|---|
| intent | `DSS_INTENT_MODEL` | `DSS_INTENT_TEMPERATURE` | `DSS_INTENT_TIMEOUT_SECONDS` | `DSS_INTENT_RETRIES` |
| moderation | `DSS_MODERATION_MODEL` | `DSS_MODERATION_TEMPERATURE` | `DSS_MODERATION_TIMEOUT_SECONDS` | `DSS_MODERATION_RETRIES` |
| planner | `DSS_PLANNER_MODEL` | `DSS_PLANNER_TEMPERATURE` | `DSS_PLANNER_TIMEOUT_SECONDS` | `DSS_PLANNER_RETRIES` |
| composer | `DSS_COMPOSER_MODEL` | `DSS_COMPOSER_TEMPERATURE` | `DSS_COMPOSER_TIMEOUT_SECONDS` | `DSS_COMPOSER_RETRIES` |

All models default to `openai:gpt-4o-mini`. Temperatures default to `0.0`
except the composer's `0.3` — it writes the farmer's answer, where a little
variation reads better than a fixed phrasing. The planner gets 30s and 3
retries because it is a loop, and its design leans on `ModelRetry` in three
places.

Set any of them on the command line, or in a `.env` file:

```bash
DSS_PLANNER_MODEL=openai:gpt-4o \
DSS_PLANNER_TEMPERATURE=0.2 \
uv run uvicorn --factory dss.entrypoint.app:create_app --port 8077
```

The provider is the model string's prefix — Pydantic AI's own syntax — and the
API key is read by that SDK from its own environment variable
(`OPENAI_API_KEY`), not by `Settings`. Note only `pydantic-ai-slim[openai]` is
installed, so an `anthropic:` or `google:` model needs its extra added to
`pyproject.toml` first: the setting will accept the string, and the SDK will
not be there.

### Azure OpenAI

An agent goes to Azure when its model string starts `azure:` — the rest is
the **deployment id**, not a model name:

```bash
DSS_PLANNER_MODEL=azure:gpt-4o-mini
```

Two environment variables supply the rest, read directly rather than through
`Settings` because the SDK reads them under the same names:

| Variable | Notes |
|---|---|
| `AZURE_OPENAI_ENDPOINT` | the v1 base or full responses URL; a trailing `/responses` is trimmed |
| `AZURE_OPENAI_API_KEY` | the deployment key |

An `azure:` model with either missing raises at startup, naming both. A model
string with any other prefix is handed to Pydantic AI untouched — so one agent
can be on Azure while another is on OpenAI.

**These two cannot live in `.env`.** `pydantic-settings` reads that file into
the `Settings` object, not into `os.environ`, and these are read from
`os.environ` — so a `.env` entry never arrives. Every `DSS_*` setting can go
in `.env`; these two must be exported:

```bash
export AZURE_OPENAI_ENDPOINT="https://<res>.services.ai.azure.com/openai/v1"
export AZURE_OPENAI_API_KEY="<key>"
```

Also: **do not set `OPENAI_API_VERSION`.** The v1 GA endpoint rejects it.

This bypasses Pydantic AI's own `AzureProvider`, which uses the classic
`?api-version=` API that the v1 GA endpoint rejects.

Two symptoms worth recognising, since neither error says what is actually
wrong:

- **`401 invalid_api_key`** — an Azure key sent to `api.openai.com`, i.e. the
  model string has no `azure:` prefix. An Azure key has no `sk-` prefix, so
  this reads as a bad key rather than a key sent to the wrong service.
- **`404 DeploymentNotFound`** — the name after `azure:` is not a deployment
  on that resource. The error quotes the name it tried.

### Everything else

| Setting | Default | Set it to see |
|---|---|---|
| `max_body_bytes` | `1000000` | something small → `413` (checked *after* gunzip, so a small gzip can still trip it) |
| `ready` | `True` | `False` → `503` |
| `dss_release` | `"v1.0.0"` | anything — it is echoed as `context.dss_release` |
| `discovery_base_url` | unset | the OAN discovery endpoint |
| `invocation_base_url` | unset | the provider `/select` endpoint |
| `schema_pack_dir` | `var/schema-packs/` | another pack checkout, or a mounted path in a container |
| `discovery_radius_m` | `25000` | how far around the turn's location to look |
| `district_csv_path` | `src/dss/config/districts.csv` | another district index — a different area set, or extra aliases |

The two base URLs are all-or-nothing (`Settings.network_enabled`): set both and
discovery + the planner call real providers; leave either unset and the turn
stays `no_match`. `schema_pack_dir` has a working default, so it is no longer
part of that gate — but with the network on and no packs loaded, the DSS
refuses to boot rather than answer every turn `no_match`. Run the fetch above. The planner and composer bind their own
models (`DSS_PLANNER_MODEL`, `DSS_COMPOSER_MODEL`) — `DSS_STUB_LLM` only stubs
intent and moderation, so a real provider-backed answer needs both the network
settings and real model access.

### The district index

`src/dss/config/districts.csv` turns a place the farmer names into the point the
`/discover` spatial filter needs — "I am from Pune" becomes a coordinate. It is
read once at startup and held in memory; a missing or unreadable file **refuses
the boot**, naming the path, rather than quietly serving turns that have lost
every spatial filter.

| Column | Notes |
|---|---|
| `area_code` | LGD district code |
| `area_name` | official name — the string a follow-up question shows the farmer |
| `region` | ISO 3166-2 (`IN-MH`); disambiguates the three district names that repeat |
| `latitude` / `longitude` | district centroid |
| `aliases` | `;`-separated other names for the same district (`Bangalore;Bangalore City`) |

A name resolves in two steps. Exact match on `area_name` or any alias first;
failing that, districts that *qualify* it as a whole word — "Bengaluru" finds
Bengaluru Urban, Rural and South. Exact wins on its own, so "Mumbai" never drags
in "Mumbai Suburban", and a partial word ("Pun") matches nothing rather than
guessing at a typo.

Resolving to several districts is not an answer: the turn goes without a spatial
filter and the farmer is asked which district they are in.

**Aliases are hand-maintained in this file.** The LGD snapshot carries none, so
`scripts/generate_district_csv.py` reads the existing `districts.csv` and carries
the column across when regenerating against a newer snapshot. An adopter who
needs a different area set or different aliases points `DSS_DISTRICT_CSV_PATH`
at their own file.

## Where telemetry goes

Everything the DSS emits — spans, metrics, log lines — leaves over OTLP to one
endpoint, and a **collector** splits it from there (ADR-0013):

```
DSS ──OTLP──> collector ──┬── traces, with the words ──> Langfuse
                          ├── traces, words removed  ──> ClickHouse ──> Grafana
                          ├── metrics                ──> ClickHouse ──> Grafana
                          └── logs                   ──> ClickHouse ──> Grafana
```

Two destinations because they answer different questions. Langfuse answers
"why did the model say that", which needs the prompt and the completion.
Grafana answers "how often, how slow, how much", which needs none of the
words — so they are deleted on the way.

The DSS knows about none of this. It sets one endpoint, and where things go
after that is `otel/collector.yaml`.

## Tracing

Agent runs become OpenTelemetry spans — which agent ran, how long, how many
tokens, which tool it called, where it failed — plus a `dss.turn` root, one
span per stage, and one per outbound provider call (ADR-0012).

In `docker compose`, the endpoint already points at the collector and there is
nothing to set. Running uvicorn by hand:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 \
OTEL_SERVICE_NAME=dss \
OTEL_RESOURCE_ATTRIBUTES=deployment.environment.name=local \
uv run uvicorn --factory dss.entrypoint.app:create_app --port 8077
```

Unset the endpoint and nothing is instrumented and nothing is exported — the
quiet default for a test run.

**Name the service.** Without `OTEL_SERVICE_NAME` every row says
`unknown_service`. The ClickHouse is shared with other services, and
`service.name` is the only thing telling the rows apart;
`deployment.environment.name` is what keeps a laptop's turns out of a
production panel. Every dashboard panel filters on both.

**Something has to be listening.** With nothing on the other end, one agent run
produces half a dozen `Transient error … Connection refused … retrying`
warnings from the OTLP exporter — the exporter being honest rather than
tracing being broken, but it buries the rest of the log. `./scripts/run-local.sh`
starts a collector for you.

**Message content is suppressed by default.** A span carries roles, part types,
token counts and latency, but not the farmer's query or the composed answer.
`DSS_TRACE_INCLUDE_MESSAGE_CONTENT=true` turns them on and logs a warning
saying not to do that in a deployment. Only a literal `true` counts — `1` and
`yes` read as off, so a typo cannot enable it.

**Even with it on, ClickHouse never sees the words.** The collector deletes the
six attributes that carry message text before the ClickHouse branch:
`gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.system_instructions`,
`pydantic_ai.all_messages`, `gen_ai.tool.call.arguments`,
`gen_ai.tool.call.result`. Locally you can watch this happen — the collector
prints the stripped branch:

```bash
docker logs -f dss-otel-collector
```

Langfuse shows the prompt; the same turn in that log does not. If you upgrade
`pydantic-ai` and one of those attribute names changes, content starts flowing
to ClickHouse and nothing fails — re-check the list in `otel/collector.yaml`
against the installed version.

To see spans with no container at all, the tests read them back in memory:

```bash
uv run pytest tests/integration/adapters/observability/ -v --no-cov
```

## Metrics

Spans answer "why was *this* turn slow". They cannot answer "is this deployment
slower than last week" — that needs numbers already added up.

**They are on by default now.** They used to ship as `OTEL_METRICS_EXPORTER=none`
because the endpoint was Langfuse and Langfuse throws metrics away. The
endpoint is a collector that forwards them, so the reason is gone.

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 \
OTEL_METRICS_EXPORTER=otlp \
OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=delta \
DSS_MODEL_PROFILE=local \
uv run uvicorn --factory dss.entrypoint.app:create_app --port 8077
```

What gets published:

| Name | What it says |
|---|---|
| `dss.turn.duration` | how long a whole turn took, by how it ended |
| `dss.turn.first_delta.duration` | how long the farmer waited for the first word |
| `dss.turn.composed.duration` | when the answer finished writing, with its sources |
| `dss.turn.count` | how many turns, by how they ended |
| `dss.turn.cost` | what a turn cost, where the model has a published price |
| `dss.stage.duration` | how long one stage took, and which model ran it |
| `dss.stage.tokens` | tokens per stage, split into input and output |
| `http.server.request.duration` | every request at the edge, by route and status |

Durations are in **seconds**, which is what the HTTP convention uses. The DSS
opts into the stable OpenTelemetry HTTP names at startup; without that the
instrumentor still publishes the superseded `http.server.duration`, in
milliseconds, and a panel built on the name above would stay empty.

**Temporality is delta, not cumulative.** ClickHouse stores what it is given.
A cumulative counter would make every rate panel compute a windowed difference
in SQL; delta lets a panel sum rows. The checked-in dashboard is written for
delta, so a deployment that leaves this unset gets wrong-looking graphs rather
than empty ones.

Worth knowing:

- **`DSS_MODEL_PROFILE` names the whole model configuration.** Nothing works it
  out for you. Two deployments running the same four models are only comparable
  if each says which one it is. Unset reads as `default`.
- **A turn that crashes still counts.** It is recorded with `status=error`, so a
  breakdown by status accounts for every turn rather than only the happy ones.
- **HTTP duration is not time to first word.** A turn streams, so the request
  is not over until the last word. `dss.turn.first_delta.duration` is the wait
  the farmer actually feels, and the two differ by a lot.
- **Composed is the end of composition, not the first word.** Sources are
  attached from the whole text, so it lands after the last word. The gap
  between first delta and composed is how long the writing took.
- **HTTP requests get metrics, not spans.** The instrumentor's spans would sit
  above `dss.turn` as the root and carry the query string and exception
  messages. `dss.turn` is the trace root.
- **A caller's `baggage` header never reaches a span.** Otherwise a caller
  could set the Langfuse user, session or trace name.
- **Cost is zero on a self-hosted model.** There is no published price for one.
  That is expected; read the token counts instead.

**No label carries a farmer's words, a provider, or a place.** Every distinct
combination of label values becomes its own stored series, so an unbounded one
would grow without limit — and §6.1 keeps personal data out of telemetry
anyway. A test pins exactly which labels are allowed.

To see the numbers with no container at all:

```bash
uv run pytest tests/integration/adapters/observability/test_metrics.py -v --no-cov
```

## Logs

`dss.trace` writes one line per stage and one per outbound call. Those lines go
to stderr as they always have, and now also over OTLP, so a dashboard can sit
them next to the span they came from instead of leaving them in a container's
stdout.

They carry `trace_id` and `span_id` as fields, so Grafana joins a log line to
its trace without parsing the text.

**Only INFO and above is exported, and that is a PII control, not a volume
one.** `/discover` and `/select` bodies are logged at DEBUG — clipped at 8000
characters, but otherwise the farmer's words and the provider's reply. The
bridge's handler sits at INFO, so `DSS_LOG_LEVEL=DEBUG` still prints those
bodies to your terminal and still exports none of them.

That is the whole of it. If you move the level onto the logger instead of the
handler, or raise the handler to DEBUG, provider bodies start landing in
ClickHouse. A test pins the behaviour:

```bash
uv run pytest tests/integration/adapters/observability/test_logs.py -v --no-cov
```

## Dashboards

`grafana/dashboards/dss.json` is the dashboard, checked in and provisioned from
disk with UI edits disabled. It is in the repo because the `dss.*` metric names
and the stage names in `observability/stages.py` are a contract, and a contract
is only enforced if breaking it breaks something visible.

Four rows: turn health, stage breakdown, cost and tokens, HTTP and errors.

Two variables to set when you open it: **Database** (`otel` unless a schema
clash forced another) and **Environment** (`deployment.environment.name`).
The shared ClickHouse holds every environment, so every panel filters on the
second.

Editing it means editing the JSON and committing it. A dashboard changed in the
UI drifts from the file, and then the file is wrong and nobody knows which one
is real.

## What is fake, and where to swap it

Every stub is marked `STUB(#nn)` in the source. Grep for it.

| Stub | File | Real version |
|---|---|---|
| the canned answer | `adapters/llm/stub.py` | a real `LLM` adapter |
| the deny word list | `core/moderation/service.py` | #82 policy evaluator |
| the fixed prompt | `core/intent/service.py` | #83 real prompt |
| the in-memory turn record | `adapters/sinks/memory.py` | #85 durable store |

The composer is no longer fixed sentences: `orchestration/compose.py` writes
the answer from the planner's `Evidence`. It is only reached once the network
is wired (otherwise the turn is `no_match`). The old stub `compose` in
`core/channel/service.py` and `CoreRunner` have been removed — the orchestrator
is the only runner, and `core/channel/service.py` now just shapes the answer
(`answer_from_evidence`, `no_match_answer`).

Swapping any of them is a one-line change in
`src/dss/entrypoint/composition.py` — the only file that names concrete classes.

## Tests

```bash
uv run pytest                 # everything; tier 6 (eval) excluded
uv run pytest tests/unit      # pure — mapping, framing, core rules, boundaries
uv run pytest tests/integration/entrypoint     # the HTTP layer against a fake runner
uv run pytest tests/integration/orchestration  # the runner against fake ports
uv run ruff check . && uv run ruff format --check .
```

## Related

- `docs/api-contracts/api-contract.md` — the contract (still describes the older
  three-endpoint shape; being rewritten)
- `docs/.agent/plan/0002-v1/turn_API_contract.md` — the build plan and the
  reconciled contract rules
- `docs/HEXAGONAL_ARCHITECTURE.md` — why the layers are shaped this way
