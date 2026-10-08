#!/usr/bin/env bash
# graph_sync.sh — Postgres -> Neo4j 지식그래프 백필 (app.graph.sync_cli).
#
#   scripts/graph_sync.sh --dry-run
#   scripts/graph_sync.sh
#   scripts/graph_sync.sh --source-ids doc-id-1,doc-id-2
set -euo pipefail

PROJECT_DIR="${GRAPH_SYNC_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SERVICE="${GRAPH_SYNC_SERVICE:-api}"

cd "$PROJECT_DIR"

if ! docker compose ps --status running --services 2>/dev/null | grep -qx "$SERVICE"; then
  echo "중단: ${SERVICE} 컨테이너가 실행 중이 아닙니다." >&2
  exit 1
fi

docker compose exec -T "$SERVICE" python -m app.graph.sync_cli "$@"
