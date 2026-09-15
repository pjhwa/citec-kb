#!/usr/bin/env bash
# Bulk ingest (app.ingest.cli) followed by embed backfill (app.embed.cli), for cron.
#
# Why this script exists: /v1/ingest/run and app.ingest.cli only parse+store
# documents/chunks — they never call embed_pending_chunks (see app/ingest/pipeline.py).
# Only the single-file compat upload path (/api/upload, used by kb_upload_document)
# auto-embeds after ingest (app/routers/external_compat.py:_run_upload_ingest_job).
# A periodic bulk ingest (e.g. re-scanning raw_dir for externally-dropped files)
# therefore needs this script to close the gap and actually populate embeddings.
#
# Runs both steps inside the running `api` container (docker compose exec) so it
# shares the container's model cache (/models, HF_HUB_OFFLINE) and DB credentials —
# same pattern as scripts/backfill_2026-08-07_failure_bucket_environment.sh.
#
# Usage: scripts/ingest_and_embed.sh [--sources support_history,tech_repo,...] [--raw-dir DIR]
#
# Cron example (every night at 02:00, from the project's crontab):
#   0 2 * * * /usr/bin/flock -n /tmp/citec-kb-ingest-embed.lock \
#     /path/to/citec-kb/scripts/ingest_and_embed.sh >> /path/to/citec-kb/logs/cron_ingest_embed.log 2>&1
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${PROJECT_DIR}/logs"
mkdir -p "$LOG_DIR"

SOURCES=""
RAW_DIR=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --sources) SOURCES="${2:-}"; shift 2 ;;
    --raw-dir) RAW_DIR="${2:-}"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 [--sources support_history,tech_repo,confluence_docs,tuning_ai,checkitem] [--raw-dir DIR]"
      exit 0
      ;;
    *) echo "알 수 없는 옵션: $1" >&2; exit 1 ;;
  esac
done

cd "$PROJECT_DIR"

STAMP="$(date +%Y%m%d_%H%M%S)"
INGEST_LOG="${LOG_DIR}/ingest_${STAMP}.log"
EMBED_LOG="${LOG_DIR}/embed_${STAMP}.log"

INGEST_ARGS=(-m app.ingest.cli -v)
[[ -n "$SOURCES" ]] && INGEST_ARGS+=(--sources "$SOURCES")
[[ -n "$RAW_DIR" ]] && INGEST_ARGS+=(--raw-dir "$RAW_DIR")

echo "[1/2] ingest 시작 $(date -Iseconds) args=${INGEST_ARGS[*]}"
if ! docker compose exec -T api python3 "${INGEST_ARGS[@]}" | tee "$INGEST_LOG"; then
  echo "❌ ingest 실패 — embed 단계 건너뜀 (로그: $INGEST_LOG)" >&2
  exit 1
fi

echo "[2/2] embed 시작 $(date -Iseconds)"
if ! docker compose exec -T api python3 -m app.embed.cli -v | tee "$EMBED_LOG"; then
  echo "❌ embed 실패 (로그: $EMBED_LOG)" >&2
  exit 1
fi

echo "✅ 완료 $(date -Iseconds) — ingest_log=$INGEST_LOG embed_log=$EMBED_LOG"
