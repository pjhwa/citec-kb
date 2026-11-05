#!/usr/bin/env bash
# api_busy_check.sh — is a long-running batch alive inside the api container?
#
# Exit 0 = BUSY (prints what), exit 1 = idle, exit 2 = could not check (treated
# as idle by callers that only warn, never silently as "safe").
#
# Single source of truth for "restarting api now would kill a batch". Used by
# scripts/restart_api_if_idle.sh (nightly) and scripts/git_pull_deploy.sh
# (deploy restart — a deploy at 14:32 killed an 18h map inventory on 2026-11-02).
# Keep the pattern list in sync with any new long `docker compose exec` batch CLI.
#
#   COMPOSE_CMD="sudo docker compose" scripts/api_busy_check.sh   # when docker needs sudo
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$(cd "${SCRIPT_DIR}/.." && pwd)"
read -r -a DC <<< "${COMPOSE_CMD:-docker compose}"

if [[ -z "$("${DC[@]}" ps -q --status running api 2>/dev/null)" ]]; then
  echo "api 컨테이너가 running 상태가 아님"
  exit 1
fi

BUSY_PIDS="$("${DC[@]}" exec -T api sh -c '
  for p in /proc/[0-9]*; do
    [ -r "$p/cmdline" ] || continue
    tr "\0" " " < "$p/cmdline" 2>/dev/null | grep -Eq "app\.(embed|ingest)\.cli|app\.synonyms\.cli|app\.confluence\.(map_sync_cli|sync_cli|map_inventory_cli|docs_inventory_cli|map_backfill_cli)" && basename "$p"
  done
' 2>/dev/null || true)"

if [[ -n "$BUSY_PIDS" ]]; then
  echo "ingest/embed/confluence 배치 진행 중 (pid: $(echo "$BUSY_PIDS" | tr '\n' ' '))"
  exit 0
fi

# Admin-triggered map sync runs inside the api process (no separate cmdline);
# it holds the same advisory lock as sync_map()/run_map_inventory().
MAP_SYNC_RUNNING="$("${DC[@]}" exec -T api python3 -c '
from app.confluence.map_sync import is_sync_running
print("yes" if is_sync_running() else "no")
' 2>/dev/null || echo "unknown")"

if [[ "$MAP_SYNC_RUNNING" == "yes" ]]; then
  echo "confluence_map sync/inventory 진행 중 (advisory lock held)"
  exit 0
fi
if [[ "$MAP_SYNC_RUNNING" == "unknown" ]]; then
  echo "advisory lock 확인 실패 (api 응답 없음?)"
  exit 2
fi
exit 1
