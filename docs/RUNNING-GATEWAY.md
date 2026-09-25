# Running the model gateway

Every model call the assistant makes goes through LiteLLM (ADR-0013). This is
the operator's side: how to start it, and how to change a model, a vendor, a key
or a spending limit without touching the assistant.

Everything here was run on a laptop against a real Azure account. Where
something did not work, it says so.

---

## 1. Where to change what

| You want to | Deployed | Locally |
|---|---|---|
| A step to use a different model | `models` in the chart values → PR → sync | §4 recipe |
| A step to use a different vendor | same, plus a credential for that vendor | §4 recipe |
| Add or replace a vendor key | a Kubernetes secret, named by the chart | §4 recipe |
| A step to use a different label | `DSS_<STEP>_MODEL` | same |
| Quieten the gateway's tracing | `otel/collector-gateway.yaml` | same |
| Anything above | **the assistant's code** — never | never |

Nothing restarts for the first three: a job applies the list through the
gateway's own interface after each deployment (ADR-0013 §5). Changing the label a
step asks for does restart the assistant, which is why the labels are meant to
stay put and their meaning to move.

The admin screen is off in deployed environments. The list lives in the chart, so
a screen that edits the running gateway would be a second source of truth that
nothing reviews. Turn it on locally if it helps.

The assistant knows four labels — `dss-intent`, `dss-moderation`, `dss-planner`,
`dss-composer` — one address and one key. It never learns which vendor answered.

## 2. Start it

```bash
docker network create oan-edge                       # once; both stacks share it
docker-compose -f docker-compose.langfuse.yml --env-file .env up -d
docker-compose -f docker-compose.litellm.yml  --env-file .env up -d
curl -s localhost:${LITELLM_HOST_PORT:-4000}/health/readiness   # {"status":"healthy","db":"connected"}
```

Then create the vendor credential and the four labels:

```bash
set -a && . ./.env && set +a
./scripts/seed-gateway-models.sh "http://localhost:${LITELLM_HOST_PORT:-4000}"
```

Four things that caught us out:

- **`LITELLM_SALT_KEY` must be set before anything is saved.** It encrypts
  vendor keys in the database. Set it late, or change it, and everything saved
  before becomes unreadable: the gateway sends nonsense to the vendor and the
  vendor says the key is invalid. Nothing warns you. The deployment file now
  refuses to start without it. Keep a copy of the key with your other secrets —
  lose it and every vendor key must be typed in again.
- **`docker compose` may not exist** even where `docker` does. On this machine
  the plugin is a broken link to a removed Rancher Desktop. The standalone
  `docker-compose` works; commands are otherwise identical.
- **Port 4000 is popular.** Grafana holds it here, and a container published on
  `127.0.0.1:4000` loses to it quietly — every call returns a redirect to a login
  page belonging to something else. Set `LITELLM_HOST_PORT` (we use 4100).
- **Langfuse checks the shape of `LANGFUSE_INIT_USER_EMAIL`.** `spike@local`
  fails and the web container restarts in a loop.

## 3. Point the assistant at it

**On compose, one secret.** The four names and the gateway's address are fixed
in `docker-compose.yml` — they never change, because a model change happens in
the gateway. All `.env` carries is the key:

```bash
DSS_GATEWAY_API_KEY=<a gateway key>
```

**Running the DSS on the host instead**, the address is the published port
rather than the service name, so pass all six:

```bash
DSS_INTENT_MODEL=dss-intent
DSS_MODERATION_MODEL=dss-moderation
DSS_PLANNER_MODEL=dss-planner
DSS_COMPOSER_MODEL=dss-composer
DSS_GATEWAY_URL=http://localhost:4100/v1
DSS_GATEWAY_API_KEY=<a gateway key>
```

A full turn answers this way with `AZURE_OPENAI_*` removed from the assistant
altogether.

**The four model settings are names the gateway resolves, not vendors' model
ids.** Which vendor is behind `dss-composer` is the gateway's business, and it
can change without this file changing. Set these once and leave them alone: a
model or vendor change happens in the gateway.

With `DSS_GATEWAY_URL` unset, the same strings fall back to what Pydantic AI
makes of them — `azure:<deployment>` goes straight to Azure. That is the
pre-gateway path, and it is the way back if the gateway has to come out.

**Not solved yet:** `OPENAI_API_KEY` is one setting, so all four steps present
the same gateway key. Giving each step its own — which is how spending is
attributed — needs a small change in `adapters/llm/`.

## 4. Recipes — copy, replace, run

**Where these belong.** In a deployed environment the model list lives in the
Helm chart and is applied by a job on deployment (ADR-0013 §5); the admin screen
is off, and a change is a pull request. Use what follows on a local gateway, and
to read what a deployed one is doing. Anything here that writes will be undone by
the next deployment, which is the point.

Set these once per shell:

```bash
set -a && . ./.env && set +a            # zsh needs the ./ — a bare `. .env` searches PATH
export G="http://localhost:${LITELLM_HOST_PORT:-4000}"
export M="$LITELLM_MASTER_KEY"
```

Both come from `.env`; `.env.example` says what each one is and how to generate
it. Nothing below contains a key.

Every recipe below is one command with `<PLACEHOLDERS>` to replace.

### Add a vendor account

Once per account, not per model. Every model on that account shares it.

```bash
curl -sS -X POST $G/credentials -H "Authorization: Bearer $M" -H 'Content-Type: application/json' -d '{
  "credential_name": "<ACCOUNT-NAME>",
  "credential_info": {"description": "<WHAT IT IS>"},
  "credential_values": {
    "api_key": "<VENDOR KEY>",
    "api_base": "<ENDPOINT, Azure and self-hosted only - omit for others>"
  }}'
```

### Add a model

```bash
curl -sS -X POST $G/model/new -H "Authorization: Bearer $M" -H 'Content-Type: application/json' -d '{
  "model_name": "<NAME THE ASSISTANT ASKS FOR, e.g. dss-composer>",
  "litellm_params": {
    "model": "<VENDOR>/<MODEL>",
    "litellm_credential_name": "<ACCOUNT-NAME>",
    "input_cost_per_token": <PRICE IN, e.g. 0.00000015>,
    "output_cost_per_token": <PRICE OUT, e.g. 0.00000060>
  },
  "model_info": {"id": "<A STABLE ID, e.g. dss-composer>"}}'
```

`<VENDOR>` is the prefix: `openai`, `anthropic`, `gemini`, `vertex_ai`,
`bedrock`, `hosted_vllm`. **On Azure `<MODEL>` is the deployment name**, not a
model family — the endpoint only answers for deployments that exist.

**Set both prices.** An unpriced model is recorded as costing nothing, and a
spending limit over nothing never stops anything.

### Change the model behind a step

Same vendor, different model. Send the whole `litellm_params` — it is replaced,
not merged, so leaving out the credential name loses the key.

```bash
curl -sS -X POST $G/model/update -H "Authorization: Bearer $M" -H 'Content-Type: application/json' -d '{
  "model_info": {"id": "<THE MODEL ID>"},
  "litellm_params": {
    "model": "<VENDOR>/<NEW MODEL>",
    "litellm_credential_name": "<ACCOUNT-NAME>",
    "input_cost_per_token": <NEW PRICE IN>,
    "output_cost_per_token": <NEW PRICE OUT>
  }}'
```

### Change the vendor behind a step

The same call, with a different prefix and a different account. Add that
account first, if it is new.

```bash
curl -sS -X POST $G/model/update -H "Authorization: Bearer $M" -H 'Content-Type: application/json' -d '{
  "model_info": {"id": "<THE MODEL ID>"},
  "litellm_params": {
    "model": "anthropic/<MODEL>",
    "litellm_credential_name": "<THE NEW ACCOUNT>",
    "input_cost_per_token": <PRICE IN>,
    "output_cost_per_token": <PRICE OUT>
  }}'
```

### Split traffic between two models

Two models, the same `model_name`, different `weight`. Measured at 200 calls: a
9/1 split came out 177/23.

```bash
curl -sS -X POST $G/model/new -H "Authorization: Bearer $M" -H 'Content-Type: application/json' -d '{
  "model_name": "<THE SHARED NAME>",
  "litellm_params": {
    "model": "<VENDOR>/<MODEL>",
    "litellm_credential_name": "<ACCOUNT-NAME>",
    "weight": <SHARE, e.g. 9>,
    "input_cost_per_token": <PRICE IN>,
    "output_cost_per_token": <PRICE OUT>
  },
  "model_info": {"id": "<A STABLE ID>"}}'
```

The split is per call, not per farmer: one conversation can be answered by both.

### Give a step its own key and spending limit

```bash
curl -sS -X POST $G/key/generate -H "Authorization: Bearer $M" -H 'Content-Type: application/json' -d '{
  "key_alias": "<STEP, e.g. dss-composer>",
  "models": ["<THE NAME THAT STEP USES>"],
  "max_budget": <HARD LIMIT, e.g. 5.00>,
  "soft_budget": <WARN AT, e.g. 4.00>,
  "budget_duration": "30d",
  "rpm_limit": 60}'
```

The key is the step, so its spending and limit are the step's. A key used against
another step's name is refused.

### Remove a model

```bash
curl -sS -X POST $G/model/delete -H "Authorization: Bearer $M" -H 'Content-Type: application/json' \
  -d '{"id": "<THE MODEL ID>"}'
```

### Look before you change

```bash
# what every name resolves to, and which account it uses
curl -sS $G/model/info    -H "Authorization: Bearer $M"

# the vendor accounts (values stay encrypted)
curl -sS $G/credentials   -H "Authorization: Bearer $M"

# every deployment, reachable or not - this is how a wrong key is found
curl -sS $G/health        -H "Authorization: Bearer $M"

# is a key within its limit
curl -sS "$G/key/info?key=<KEY>" -H "Authorization: Bearer $M"
```

### Try a model without involving the assistant

```bash
curl -sS $G/v1/chat/completions -H "Authorization: Bearer $M" -H 'Content-Type: application/json' -d '{
  "model": "<NAME>",
  "messages": [{"role": "user", "content": "reply with one word: ok"}],
  "max_tokens": 10}'
```

## 5. Reading the spending

Admin screen → **Logs** and **Usage**, or straight from the database:

```
postgresql://litellm:litellm@127.0.0.1:5433/litellm
```

```sql
SELECT model, round(sum(spend)::numeric, 6) AS spend,
       sum(total_tokens) AS tokens, count(*) AS calls
FROM "LiteLLM_SpendLogs" GROUP BY model ORDER BY spend DESC;
```

Rows reading `0.000000` with real tokens are models the gateway cannot price.
Each one is a spending limit that is not working.

A backup of this database contains vendor keys, encrypted. Treat it as a secret.

## 6. Tracing

The assistant sends the turn's tracking number with every call, so the gateway's
records join the turn instead of standing alone.

The gateway records its own internal steps as well — six to eight per model call,
plus one for every database write. A turn went from 22 recorded steps to 71, with
database writes appearing under the part that writes the farmer's answer. A
collector sits between the gateway and Langfuse and throws those away, keeping
two per model call. A turn is 34 steps.

To keep more or less of it, edit the rule in `otel/collector-gateway.yaml` and
restart that one container.

**Cost still does not reach Langfuse.** It works cost out from its own price
list, which does not contain our labels, and ignores the figure the gateway
sends. Cost lives in the gateway; the story of the turn lives in Langfuse.

## 7. Stop it

```bash
docker-compose -f docker-compose.litellm.yml  --env-file .env down
docker-compose -f docker-compose.langfuse.yml --env-file .env down      # -v also drops the data
```
