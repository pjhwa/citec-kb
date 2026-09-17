#!/usr/bin/env bash
# restart_api_if_idle.sh — periodic `api` container restart to reclaim creeping
# process RSS (PyTorch/glibc allocator rarely returns freed memory to the OS —
# see app/ops/dashboard.py:resource_snapshot(), which reads this same process's
# /proc/self/status VmRSS for the admin dashboard "RSS 메모리" figure).
#
# Safe to restart any time: `api` is stateless (documents/chunks/embeddings/jobs
# all live in Postgres), and docker-compose.yml already has restart:unless-stopped
# + a healthcheck with start_period:15s, so the container is back to healthy
# within seconds.
#
# BUT: `python -m app.ingest.cli` / `python -m app.embed.cli` / the Confluence
# sync CLIs (`app.confluence.sync_cli`, `app.confluence.map_sync_cli`) are
# sometimes run inside this same container via `docker compose exec` (see
# scripts/ingest_and_embed.sh, scripts/confluence_sync.sh, scripts/map_sync.sh)
# and can run for hours. Restarting mid-batch would kill that job (idempotent,
# so a rerun recovers, but there's no reason to cut it short — and a killed
# `docker compose exec` process leaves its piped log file empty, since stdout
# is block-buffered and never gets flushed). This script checks for any of
# those live processes inside the container first and skips the restart if
# one is running — the next cron tick picks it up once the batch is actually
# done. Keep this list in sync with any new long-running `docker compose exec`
# batch script added under scripts/.
#
# ALSO: confluence_map_sync can now be triggered from the admin page
# (app.routers.confluence_map's POST /v1/confluence-map/_run-sync, run via
# the Redis job worker) — that path runs sync_map() as a plain HTTP request
# handled inside the api container's own thread pool, not as a separate
# `docker compose exec` process, so the /proc cmdline grep above can't see
# it. sync_map() takes a Postgres advisory lock (key 861234501, see
# app.confluence.map_sync._SYNC_LOCK_KEY / is_sync_running()) for its whole
# run regardless of which path started it, so check that lock too.
#
# Usage: scripts/restart_api_if_idle.sh
#
# Cron example (nightly at 04:00, low-traffic hours):
#   0 4 * * * /home/citec/dev/citec-kb/scripts/restart_api_if_idle.sh >> /home/citec/dev/citec-kb/logs/cron_restart_api.log 2>&1
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_DIR"

echo "[$(date -Iseconds)] restart_api_if_idle 시작"

if [[ -z "$(docker compose ps -q --status running api 2>/dev/null)" ]]; then
  echo "[$(date -Iseconds)] api 컨테이너가 running 상태가 아님 — 건너뜀"
  exit 0
fi

BUSY_PIDS="$(docker compose exec -T api sh -c '
  for p in /proc/[0-9]*; do
    [ -r "$p/cmdline" ] || continue
    tr "\0" " " < "$p/cmdline" 2>/dev/null | grep -Eq "app\.(embed|ingest)\.cli|app\.confluence\.(map_sync_cli|sync_cli)" && basename "$p"
  done
' 2>/dev/null || true)"

if [[ -n "$BUSY_PIDS" ]]; then
  echo "[$(date -Iseconds)] ingest/embed 배치 진행 중 (pid: $(echo "$BUSY_PIDS" | tr '\n' ' ')) — 재시작 건너뜀, 다음 스케줄에 재확인"
  exit 0
fi

MAP_SYNC_RUNNING="$(docker compose exec -T api python3 -c '
from app.confluence.map_sync import is_sync_running
print("yes" if is_sync_running() else "no")
' 2>/dev/null || echo "no")"

if [[ "$MAP_SYNC_RUNNING" == "yes" ]]; then
  echo "[$(date -Iseconds)] confluence_map_sync 진행 중 (advisory lock held, 아마 admin 페이지에서 트리거됨) — 재시작 건너뜀, 다음 스케줄에 재확인"
  exit 0
fi

echo "[$(date -Iseconds)] 진행 중인 배치 없음 — api 재시작"
docker compose restart api
echo "[$(date -Iseconds)] 재시작 완료"
docker compose ps api
