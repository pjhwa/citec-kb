#!/usr/bin/env bash
# Run migrations against compose Postgres (host port 8574).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://citec:citec@127.0.0.1:8574/citec_knowledge}"
cd "$ROOT/apps/api"
if [[ -d "$ROOT/.venv" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/.venv/bin/activate"
fi
alembic upgrade head
echo "OK: alembic upgrade head via $DATABASE_URL"
