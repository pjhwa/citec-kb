#!/usr/bin/env bash
# Postgres logical dump for citec-kb (dev).
# Usage: ./scripts/backup_postgres.sh [outdir]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUTDIR="${1:-$ROOT/data/backups}"
mkdir -p "$OUTDIR"
STAMP="$(date +%Y%m%d_%H%M%S)"
FILE="$OUTDIR/citec_knowledge_${STAMP}.sql.gz"
cd "$ROOT"
docker compose exec -T postgres pg_dump -U "${POSTGRES_USER:-citec}" "${POSTGRES_DB:-citec_knowledge}" \
  | gzip -c > "$FILE"
ls -lh "$FILE"
echo "backup_ok path=$FILE"
