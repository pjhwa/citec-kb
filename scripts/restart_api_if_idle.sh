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
# BUT: `python -m app.ingest.cli` / `python -m app.embed.cli` batch jobs are
# sometimes run inside this same container via `docker compose exec` (see
# scripts/ingest_and_embed.sh) and can run for hours. Restarting mid-batch would
# kill that job (idempotent, so a rerun recovers, but there's no reason to cut
# it short). This script checks for a live ingest/embed CLI process inside the
# container first and skips the restart if one is running — the next cron tick
# picks it up once the batch is actually done.
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
    tr "\0" " " < "$p/cmdline" 2>/dev/null | grep -Eq "app\.(embed|ingest)\.cli" && basename "$p"
  done
' 2>/dev/null || true)"

if [[ -n "$BUSY_PIDS" ]]; then
  echo "[$(date -Iseconds)] ingest/embed 배치 진행 중 (pid: $(echo "$BUSY_PIDS" | tr '\n' ' ')) — 재시작 건너뜀, 다음 스케줄에 재확인"
  exit 0
fi

echo "[$(date -Iseconds)] 진행 중인 배치 없음 — api 재시작"
docker compose restart api
echo "[$(date -Iseconds)] 재시작 완료"
docker compose ps api
