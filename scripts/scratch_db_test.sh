#!/usr/bin/env bash
# scratch_db_test.sh — DB-integration 테스트를 disposable scratch Postgres에서
# 실행한다 (운영 citec_knowledge DB는 절대 건드리지 않음).
#
# 매 실행마다 citec-kb-postgres-1 컨테이너 안에 citec_kb_test DB를 새로
# 만들고, alembic upgrade head를 적용한 뒤, pytest -k _db (또는 넘긴 인자)를
# CONFLUENCE_SYNC_TEST_DATABASE_URL을 지정해 실행하고, 끝나면 DB를 삭제한다.
# tests/test_confluence_sync_db.py 등 여러 tests/test_*_db.py 파일의 모듈
# docstring에 적힌 수동 3줄 설정을 대체한다.
#
#   scripts/scratch_db_test.sh                      # pytest tests/ -k _db 전체
#   scripts/scratch_db_test.sh tests/test_confluence_sync_db.py
#   scripts/scratch_db_test.sh tests/test_confluence_sync_db.py::test_foo -v
#   scripts/scratch_db_test.sh --keep tests/test_confluence_sync_db.py  # 끝나도 DB 삭제 안 함 (디버깅용)
#
# 전제: docker로 citec-kb-postgres-1(pgvector/pgvector:pg16, 8574:5432)이
# 떠 있어야 한다. 다른 이름/포트를 쓰면 아래 두 변수를 env로 덮어쓴다:
#   PG_CONTAINER=my-postgres PG_PORT=5432 scripts/scratch_db_test.sh ...
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

PG_CONTAINER="${PG_CONTAINER:-citec-kb-postgres-1}"
PG_PORT="${PG_PORT:-8574}"
PG_USER="${PG_USER:-citec}"
PG_PASSWORD="${PG_PASSWORD:-citec}"
PG_DB="${PG_DB:-citec_kb_test}"

KEEP=false
ARGS=()
for a in "$@"; do
  case "$a" in
    --keep) KEEP=true ;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) ARGS+=("$a") ;;
  esac
done
if [ "${#ARGS[@]}" -eq 0 ]; then
  ARGS=(tests/ -k _db)
fi

TEST_DSN="postgresql+psycopg://${PG_USER}:${PG_PASSWORD}@127.0.0.1:${PG_PORT}/${PG_DB}"

echo "[scratch_db_test] creating ${PG_DB} in ${PG_CONTAINER}..." >&2
docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d postgres -c "CREATE DATABASE ${PG_DB};" \
  || echo "[scratch_db_test] ${PG_DB} already exists — reusing" >&2
docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -c "CREATE EXTENSION IF NOT EXISTS vector;"

cleanup() {
  if [ "$KEEP" = false ]; then
    echo "[scratch_db_test] dropping ${PG_DB}..." >&2
    docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d postgres -c "DROP DATABASE IF EXISTS ${PG_DB};" || true
  else
    echo "[scratch_db_test] --keep given — leaving ${PG_DB} in place" >&2
  fi
}
trap cleanup EXIT

cd "${PROJECT_DIR}/apps/api"
source "${PROJECT_DIR}/.venv/bin/activate"
echo "[scratch_db_test] alembic upgrade head..." >&2
DATABASE_URL="$TEST_DSN" alembic upgrade head

echo "[scratch_db_test] pytest ${ARGS[*]}" >&2
CONFLUENCE_SYNC_TEST_DATABASE_URL="$TEST_DSN" python3 -m pytest "${ARGS[@]}"
