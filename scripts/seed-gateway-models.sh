#!/usr/bin/env bash
# Set up a fresh gateway: one credential per vendor, then the four step labels
# that the assistant asks for (ADR-0013).
#
#     export LITELLM_MASTER_KEY=...
#     export AZURE_OPENAI_ENDPOINT=... AZURE_OPENAI_API_KEY=...
#     ./scripts/seed-gateway-models.sh http://localhost:4000
#
# Safe to run again: each label is removed and recreated.
#
# After this, models are changed in the gateway's admin screen, not here. This
# file is the starting point and the record of what the labels mean — it is not
# what is running. Re-run it and you overwrite anyone's changes.
#
# The gateway must have LITELLM_SALT_KEY set BEFORE this runs. It encrypts the
# vendor key in the database. Set later, or changed, and everything saved before
# becomes unreadable — the gateway then sends nonsense to the vendor and the
# vendor says the key is invalid. Nothing warns you.
set -euo pipefail

BASE="${1:-http://localhost:4000}"
: "${LITELLM_MASTER_KEY:?set LITELLM_MASTER_KEY}"
: "${AZURE_OPENAI_ENDPOINT:?set AZURE_OPENAI_ENDPOINT}"
: "${AZURE_OPENAI_API_KEY:?set AZURE_OPENAI_API_KEY}"

# The Azure deployment name, as it appears in Azure AI Foundry. Not a model
# family name: the endpoint only answers for deployments that exist.
DEPLOYMENT="${AZURE_DEPLOYMENT:-gpt-5.6-luna}"

# Price per token. Set it. A model the gateway cannot price is recorded as
# costing nothing, and a spending limit over nothing never stops anything.
IN_COST="${AZURE_INPUT_COST_PER_TOKEN:-0.00000015}"
OUT_COST="${AZURE_OUTPUT_COST_PER_TOKEN:-0.00000060}"

api() { curl -sS -H "Authorization: Bearer $LITELLM_MASTER_KEY" -H 'Content-Type: application/json' "$@"; }

# --- the vendor account, stored once, encrypted -----------------------------
# One credential covers every model on that account. A second is only needed
# when a second vendor arrives.
if api "$BASE/credentials" | grep -q '"azure-oan"'; then
  echo "credential azure-oan already there"
else
  api -X POST "$BASE/credentials" -d "{
    \"credential_name\": \"azure-oan\",
    \"credential_info\": {\"description\": \"OAN Azure OpenAI v1 endpoint\"},
    \"credential_values\": {
      \"api_key\": \"$AZURE_OPENAI_API_KEY\",
      \"api_base\": \"$AZURE_OPENAI_ENDPOINT\"
    }}" >/dev/null
  echo "credential azure-oan created"
fi

# --- one label per step -----------------------------------------------------
# The assistant asks for these names and nothing else. What they mean is this
# file's business today and the admin screen's business afterwards.
for step in intent moderation planner composer; do
  api -X POST "$BASE/model/delete" -d "{\"id\": \"dss-$step\"}" >/dev/null 2>&1 || true
  api -X POST "$BASE/model/new" -d "{
    \"model_name\": \"dss-$step\",
    \"litellm_params\": {
      \"model\": \"openai/$DEPLOYMENT\",
      \"litellm_credential_name\": \"azure-oan\",
      \"input_cost_per_token\": $IN_COST,
      \"output_cost_per_token\": $OUT_COST
    },
    \"model_info\": {\"id\": \"dss-$step\"}}" >/dev/null
  echo "dss-$step -> openai/$DEPLOYMENT (via azure-oan)"
done
