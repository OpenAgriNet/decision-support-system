#!/usr/bin/env bash
# Apply retention to a self-hosted Langfuse. Idempotent — safe after every
# `up -d`, and re-running only rewrites the same values.
#
# Two halves, both needed. ClickHouse holds the traces; MinIO holds a copy of
# every raw ingested event. Expiring only the first leaves the whole history in
# blob storage, which is the worse half to keep — see ADR-0007 §4.
#
#     ./scripts/langfuse-retention.sh            # 5 days, the default
#     RETENTION_DAYS=30 ./scripts/langfuse-retention.sh
#
set -euo pipefail

RETENTION_DAYS="${RETENTION_DAYS:-5}"
COMPOSE_PROJECT="${COMPOSE_PROJECT:-decision-support-system}"
CLICKHOUSE_USER="${CLICKHOUSE_USER:-clickhouse}"
CLICKHOUSE_PASSWORD="${CLICKHOUSE_PASSWORD:-clickhouse}"
MINIO_BUCKET="${MINIO_BUCKET:-langfuse}"
MINIO_ROOT_USER="${MINIO_ROOT_USER:-minio}"
MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-miniosecret}"

if command -v podman >/dev/null 2>&1; then R=podman; else R=docker; fi
ch="${COMPOSE_PROJECT}_clickhouse_1"
minio="${COMPOSE_PROJECT}_minio_1"

# `--enable_full_text_index=1`: these tables carry a text index, and MODIFY TTL
# re-validates the whole schema on the way through. Without the setting the
# ALTER fails with "Text index feature is not enabled", which says nothing about
# TTL and is easy to mistake for the TTL being unsupported.
clickhouse() {
  "$R" exec "$ch" clickhouse-client \
    --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" \
    --enable_full_text_index=1 -q "$1"
}

echo "retention: ${RETENTION_DAYS} days"
failed=0

# v4 writes events_core/events_full; the rest are pre-v4 shapes, still created
# and still worth bounding. Which time column exists varies by table.
for table in events_core events_full observations traces scores \
             analytics_traces analytics_observations analytics_scores; do
  [ "$(clickhouse "EXISTS TABLE ${table}")" = "1" ] || continue

  column=""
  for candidate in start_time timestamp created_at; do
    if [ "$(clickhouse "SELECT count() FROM system.columns WHERE table='${table}' AND name='${candidate}'")" = "1" ]; then
      column="$candidate"
      break
    fi
  done

  if [ -z "$column" ]; then
    echo "  ${table}: no time column found, skipped"
    continue
  fi

  # Errors are reported, not swallowed: a silent failure here means traces
  # accumulate until the disk fills, months after anyone read this output.
  if error="$(clickhouse "ALTER TABLE ${table} MODIFY TTL ${column} + INTERVAL ${RETENTION_DAYS} DAY" 2>&1)"; then
    echo "  ${table}: TTL on ${column}"
  else
    echo "  ${table}: FAILED — ${error}" >&2
    failed=1
  fi
done

# Replace rather than append: `mc ilm rule add` generates a fresh id each time,
# so running this twice would otherwise leave two rules on the bucket.
if "$R" exec "$minio" sh -c "
    mc alias set local http://localhost:9000 '${MINIO_ROOT_USER}' '${MINIO_ROOT_PASSWORD}' >/dev/null &&
    { mc ilm rule rm --all --force local/${MINIO_BUCKET} >/dev/null 2>&1 || true; } &&
    mc ilm rule add local/${MINIO_BUCKET} --expire-days ${RETENTION_DAYS} >/dev/null
  "; then
  echo "  minio/${MINIO_BUCKET}: expire after ${RETENTION_DAYS}d"
else
  echo "  minio/${MINIO_BUCKET}: FAILED — traces would expire but raw events would not" >&2
  failed=1
fi

[ "$failed" -eq 0 ] && echo "done" || { echo "one or more steps failed" >&2; exit 1; }
