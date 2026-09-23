#!/usr/bin/env bash
# Start the whole local stack: Langfuse, the mock network, and the DSS.
#
#     ./scripts/run-local.sh
#
# Ctrl-C stops the DSS. Langfuse and the mock keep running — they are slow to
# start and you usually want them across several DSS restarts. Stop them with
# `./scripts/run-local.sh --down`.
#
# This script checks prerequisites and refuses, rather than fixing them. A dev
# script that silently creates networks and rewrites config files is the thing
# that bites months later, when nobody remembers what it wrote.
set -euo pipefail

cd "$(dirname "$0")/.."

DSS_PORT="${DSS_PORT:-8077}"
MOCK_PORT="${MOCK_PORT:-8078}"
LANGFUSE_PORT="${LANGFUSE_PORT:-3000}"
OTEL_PORT="${OTEL_PORT:-4318}"
COLLECTOR="${COLLECTOR:-dss-otel-collector}"
SCHEMA_PACK_REF="${SCHEMA_PACK_REF:-schema-packs-v0.1}"
COMPOSE_PROJECT="${COMPOSE_PROJECT:-decision-support-system}"
ENV_LOCAL="${ENV_LOCAL:-.env.local}"

die() { echo "error: $*" >&2; exit 1; }

# --- container runtime -----------------------------------------------------
#
# Installed is not the same as usable. A Homebrew `podman` with no machine
# started is on the PATH and answers every `command -v`, so picking on that
# alone sent anyone running Docker or Colima into a podman socket error about
# a VM they never meant to use. Each candidate has to actually answer before
# it is chosen.
#
# Podman stays first, so a machine that runs both keeps the behaviour it had.
# Set CONTAINER_RUNTIME to force one either way.

runtime_responds() {
  command -v "$1" >/dev/null 2>&1 && "$1" info >/dev/null 2>&1
}

R="${CONTAINER_RUNTIME:-}"
if [ -n "$R" ]; then
  runtime_responds "$R" || \
    die "CONTAINER_RUNTIME is $R, which is not installed or is not responding.
  Start it, or unset CONTAINER_RUNTIME to let this script choose."
else
  for candidate in podman docker; do
    if runtime_responds "$candidate"; then R="$candidate"; break; fi
  done
  [ -n "$R" ] || die "no container runtime is responding. Start one:

    colima start          # or Docker Desktop
    podman machine start

  Set CONTAINER_RUNTIME=docker|podman to choose explicitly."
fi

if [ "$R" = podman ]; then
  COMPOSE=podman-compose
  command -v podman-compose >/dev/null 2>&1 || \
    die "podman is the runtime but podman-compose is not installed:

    brew install podman-compose

  Or set CONTAINER_RUNTIME=docker to use Docker instead."
else
  COMPOSE="docker compose"
  docker compose version >/dev/null 2>&1 || \
    die "docker is the runtime but 'docker compose' is not available.
  Install the Compose plugin, or set CONTAINER_RUNTIME=podman."
fi

echo "==> container runtime: $R"

# --- stop ------------------------------------------------------------------

if [ "${1:-}" = "--down" ]; then
  $COMPOSE -f docker-compose.langfuse.yml --env-file .env.langfuse down
  # Started with `run`, not compose, so it is stopped by name. See where it
  # starts, below, for why it is not a compose service here.
  "$R" rm -f "$COLLECTOR" >/dev/null 2>&1 || true
  # The mock is not a container, so compose does not own it.
  pkill -f 'tools.mock_network' 2>/dev/null || true
  echo "stopped"
  exit 0
fi

# --- prerequisites ---------------------------------------------------------
#
# All checked before anything starts, so a missing piece is one message at the
# top rather than a failure three minutes in, after Langfuse has booted.

# `network inspect`, not podman's `network exists`: docker has no `exists`
# subcommand, so that check failed on every Docker host and reported a missing
# network rather than an unknown command. `inspect` is in both.
"$R" network inspect oan-edge >/dev/null 2>&1 || \
  die "the oan-edge network does not exist. Create it:

    $R network create oan-edge

  docker-compose.langfuse.yml declares it external, so compose will not."

[ -f .env.langfuse ] || \
  die "$PWD/.env.langfuse is missing. Copy the template and fill in the
  # CHANGEME lines:

    cp .env.langfuse.example .env.langfuse"

# Read shape only — never printed. These two are what actually fail, and both
# fail late and unhelpfully: the worker crash-loops on the key, and the web
# container 500s on the password while the worker looks fine.
key="$(grep -E '^ENCRYPTION_KEY=' .env.langfuse | head -1 | cut -d= -f2- | tr -d '"'"'"' ' || true)"
[ -n "$key" ] || die "ENCRYPTION_KEY is not set in .env.langfuse. Generate one:

    openssl rand -hex 32"
[[ "$key" =~ ^[0-9a-fA-F]{64}$ ]] || \
  die "ENCRYPTION_KEY in .env.langfuse is ${#key} characters, not 64 hex.
  Langfuse rejects it at boot. Generate one:

    openssl rand -hex 32"

# Optional: unset means sign up through the UI. Set-but-short is the failure —
# the web container rejects it and only says 'Password needs to be at least 8
# characters long', with nothing naming the variable.
pw="$(grep -E '^LANGFUSE_INIT_USER_PASSWORD=' .env.langfuse | head -1 | cut -d= -f2- | tr -d '"'"'"' ' || true)"
if [ -n "$pw" ] && [ "${#pw}" -lt 8 ]; then
  die "LANGFUSE_INIT_USER_PASSWORD in .env.langfuse is ${#pw} characters.
  Langfuse needs 8 or more, or clear it (with the other LANGFUSE_INIT_* lines)
  and sign up through the UI instead."
fi

# The four that cannot live in .env: pydantic-settings reads that file into the
# Settings object, and these are read from os.environ — by the OpenAI SDK, by
# the OTLP exporter, and by tracing.py. A .env entry never arrives.
[ -f "$ENV_LOCAL" ] || \
  die "$PWD/$ENV_LOCAL is missing. It holds the four values .env cannot carry:

    export AZURE_OPENAI_ENDPOINT=\"https://<res>.services.ai.azure.com/openai/v1\"
    export AZURE_OPENAI_API_KEY=\"<key>\"
    export LANGFUSE_PUBLIC_KEY=\"pk-lf-...\"
    export LANGFUSE_SECRET_KEY=\"sk-lf-...\"

  The Langfuse pair comes from Settings -> API Keys at
  http://localhost:$LANGFUSE_PORT. .env.* is gitignored."

# `set -a` so a line without `export` still reaches the DSS. Plain `source`
# makes a shell variable, which satisfied the check below and then never
# reached uvicorn — the whole point of this file is that these are read from
# `os.environ` by a child process. The failure was a raw
# `KeyError: 'AZURE_OPENAI_ENDPOINT'` from inside the app, with the script
# having just reported the variable as set.
set -a
# shellcheck disable=SC1090
source "$ENV_LOCAL"
set +a

for v in AZURE_OPENAI_ENDPOINT AZURE_OPENAI_API_KEY LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY; do
  [ -n "${!v:-}" ] || die "$v is not set in $ENV_LOCAL"
done

# --- schema packs ----------------------------------------------------------
#
# Fetched once. With the network wired and no packs loaded the DSS refuses to
# boot, so this is a prerequisite rather than an optimisation.

if [ -z "$(ls -A var/schema-packs 2>/dev/null || true)" ]; then
  echo "==> fetching schema packs ($SCHEMA_PACK_REF)"
  uv run python scripts/fetch_schema_packs.py --ref "$SCHEMA_PACK_REF"
else
  echo "==> schema packs present"
fi

# --- langfuse --------------------------------------------------------------
#
# Not waited on. A cold start spends minutes in ClickHouse migrations, and the
# DSS's exporter retries — early spans land late rather than being lost.

if [ -n "$("$R" ps --filter "name=${COMPOSE_PROJECT}.*langfuse-web" --format '{{.Names}}' 2>/dev/null || true)" ]; then
  echo "==> langfuse already up"
else
  echo "==> starting langfuse"
  $COMPOSE -f docker-compose.langfuse.yml --env-file .env.langfuse up -d
fi

# --- collector -------------------------------------------------------------
#
# The DSS talks to the collector, and the collector talks to Langfuse
# (ADR-0013). Locally it uses `collector.local.yaml`, which has no ClickHouse
# exporter: the collector validates exporters at startup, so one pointed at a
# ClickHouse that is not there stops the process rather than degrading. The
# stripped branch still runs and prints, so `docker logs` shows what a
# deployment would have sent to ClickHouse — with the farmer's words removed.
#
# `run`, not a compose service, because the compose one mounts the deployment
# config. One container started by name is less machinery than a second
# compose file whose only job is to swap one path.

if [ -n "$("$R" ps --filter "name=^${COLLECTOR}$" --format '{{.Names}}' 2>/dev/null || true)" ]; then
  echo "==> collector already up on :$OTEL_PORT"
else
  lsof -ti:"$OTEL_PORT" >/dev/null 2>&1 && \
    die "port $OTEL_PORT is in use, and the collector needs it. Another
  collector (HyperDX runs one) or a stale container — find it with
  \`lsof -i:$OTEL_PORT\`, or set OTEL_PORT to something else."

  echo "==> starting collector on :$OTEL_PORT"
  "$R" rm -f "$COLLECTOR" >/dev/null 2>&1 || true
  # A plain header value, not the URL-encoded OTEL_EXPORTER_OTLP_HEADERS list
  # the DSS used to build — so a literal space after "Basic" is correct here
  # and `%20` would be wrong. The hazard moved rather than disappeared.
  "$R" run -d --name "$COLLECTOR" \
    --network oan-edge \
    -p "127.0.0.1:${OTEL_PORT}:4318" \
    -e LANGFUSE_OTLP_ENDPOINT="http://langfuse-web:3000/api/public/otel" \
    -e LANGFUSE_AUTH_HEADER="Basic $(printf '%s:%s' "$LANGFUSE_PUBLIC_KEY" "$LANGFUSE_SECRET_KEY" | base64 | tr -d '\n')" \
    -v "$PWD/otel/collector.local.yaml:/etc/otel/collector.yaml:ro" \
    "otel/opentelemetry-collector-contrib:${OTEL_COLLECTOR_TAG:-latest}" \
    --config=/etc/otel/collector.yaml >/dev/null
fi

# --- mock network ----------------------------------------------------------
#
# --reload because the @type-to-response map is read at import: a mock started
# before a scenario existed answers an empty catalog for it, and the turn comes
# back no_match with nothing saying the mock is simply stale.

if lsof -ti:"$MOCK_PORT" >/dev/null 2>&1; then
  echo "==> mock network already on :$MOCK_PORT"
else
  echo "==> starting mock network on :$MOCK_PORT"
  mkdir -p var
  uv run python -m tools.mock_network --port "$MOCK_PORT" --reload \
    >var/mock-network.log 2>&1 &
fi

# --- dss -------------------------------------------------------------------

lsof -ti:"$DSS_PORT" >/dev/null 2>&1 && \
  die "port $DSS_PORT is already in use — another DSS is running."

# The collector, not Langfuse. It holds the Langfuse credential now, so there
# is no OTEL_EXPORTER_OTLP_HEADERS here at all — which also retires the `%20`
# trap that used to live on this line.
export OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:${OTEL_PORT}"
# On, because the collector forwards metrics. It was `none` while the endpoint
# was Langfuse, which discards them and filled the log with failed exports.
export OTEL_METRICS_EXPORTER=otlp
# Matches the deployment, so a dashboard query written against local data still
# works against real data.
export OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=delta
export OTEL_SERVICE_NAME=dss
export OTEL_RESOURCE_ATTRIBUTES="deployment.environment.name=local"
# A laptop, where seeing the prompt is the point. ADR-0007 §5 does not permit
# this in a deployment. It reaches Langfuse and is stripped before the branch
# that would go to ClickHouse — `docker logs` on the collector shows both.
export DSS_TRACE_INCLUDE_MESSAGE_CONTENT=true

export DSS_DISCOVERY_BASE_URL="http://127.0.0.1:${MOCK_PORT}"
export DSS_INVOCATION_BASE_URL="http://127.0.0.1:${MOCK_PORT}"

echo
echo "    langfuse   http://localhost:${LANGFUSE_PORT}"
echo "    dss        http://127.0.0.1:${DSS_PORT}/docs"
echo "    mock log   tail -f var/mock-network.log"
echo "    otel log   $R logs -f $COLLECTOR"
echo
echo "    curl -s -X POST http://127.0.0.1:${DSS_PORT}/v1/turns \\"
echo "      -H 'Content-Type: application/json' -H 'Accept: application/json' \\"
echo "      --data-binary @docs/api-contracts/examples/answered_streaming.json | jq"
echo
echo "==> starting dss on :$DSS_PORT (Ctrl-C to stop; langfuse and the mock stay up)"

# Tee'd, not redirected: the log stays on the terminal AND lands in a file. A
# load run's failures scroll off the terminal long before anyone reads them,
# and the interesting ones (upstream timeouts, exhausted retries) are the
# lines the turn itself never reports — `outcome.status: unavailable` says a
# dependency failed, not which one.
#
# `pipefail` is set, so the pipe still exits on uvicorn's status rather than
# tee's. exec is dropped because a pipeline cannot be exec'd; trapping INT
# keeps Ctrl-C behaving as it did.
mkdir -p var
echo "    dss log    tail -f var/dss.log"
uv run uvicorn --factory dss.entrypoint.app:create_app --port "$DSS_PORT" 2>&1 \
  | tee -a var/dss.log
