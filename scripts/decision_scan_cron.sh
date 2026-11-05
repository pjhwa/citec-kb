#!/usr/bin/env bash
# decision_scan_cron.sh — enqueue the periodic topic scan (사람이 확정할 주제를 도출해 확정 큐에 등록).
#
# The scan is rule-based (no LLM), idempotent (source_key) and capped, so running it daily is safe:
# it only ever adds `proposed` decisions (created_by_kind=server_job); a human decides in /decisions.html.
# It also marks decisions whose pinned evidence changed as needs_recheck.
#
# crontab (NOT installed automatically — add it yourself after review):
#   30 6 * * * /home/citec/dev/citec-kb/scripts/decision_scan_cron.sh >> /home/citec/dev/citec-kb/logs/cron_decision_scan.log 2>&1
#
# Env: API_BASE (default http://localhost:8573), API_TOKEN (Bearer, needed only when AUTH_MODE is on; admin role).
set -euo pipefail
API_BASE="${API_BASE:-http://localhost:8573}"
AUTH=()
[ -n "${API_TOKEN:-}" ] && AUTH=(-H "Authorization: Bearer ${API_TOKEN}")
echo "[$(date '+%F %T')] enqueue decision_staleness_scan + decision_topic_scan"
for t in decision_staleness_scan decision_topic_scan; do
  curl -fsS -X POST "${API_BASE}/v1/jobs" -H 'content-type: application/json' "${AUTH[@]}" \
    -d "{\"type\":\"${t}\",\"payload\":{}}"
  echo
done
