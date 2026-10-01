#!/usr/bin/env bash
# collect_prod_diagnostics.sh — 운용 서버에서 실행: 이번 리뷰/구현 세션이
# BLOCKED_EXTERNAL로 남긴 항목(운영 DB 조회가 필요해 개발 환경에서 확인하지
# 못한 것)을 한 번에 수집해 파일로 저장한다. 전부 READ-ONLY — DB에 쓰기를
# 하는 명령은 하나도 없다 (SELECT/EXPLAIN/\copy TO STDOUT만, 전부
# scripts/collect_prod_diagnostics.sql 참고).
#
#   scripts/collect_prod_diagnostics.sh
#   scripts/collect_prod_diagnostics.sh --out /tmp/citec_kb_diag  # 출력 디렉토리 지정
#
# 결과물: <out>/diagnostics_<timestamp>.txt (SQL 조회 결과),
#         <out>/diagnostics_<timestamp>_meta.txt (배포 SHA/컨테이너 상태/헬스체크)
# 두 파일을 그대로 개발 환경으로 가져오면 된다(scp, 또는 내용 복사·붙여넣기).
#
# 전제: 이 스크립트를 citec-kb가 실제로 떠 있는 디렉토리(docker compose가
# 있는 곳, 보통 $HOME/citec-kb)에서 실행한다. DATABASE_URL은 .env에서
# 읽거나, 이미 떠 있는 postgres 컨테이너에 docker exec로 접속한다.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUT_DIR="."
PG_CONTAINER="${PG_CONTAINER:-}"
PG_USER="${PG_USER:-citec}"
PG_DB="${PG_DB:-citec_knowledge}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out) OUT_DIR="${2:?--out 에는 디렉토리 경로가 필요합니다}"; shift 2 ;;
    -h|--help)
      sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "알 수 없는 인자: $1" >&2; exit 64 ;;
  esac
done
mkdir -p "$OUT_DIR"

TS="$(date +%Y%m%d_%H%M%S)"
SQL_OUT="${OUT_DIR}/diagnostics_${TS}.txt"
META_OUT="${OUT_DIR}/diagnostics_${TS}_meta.txt"

cd "$PROJECT_DIR"

echo "[collect] 배포 SHA/컨테이너 상태 수집 → ${META_OUT}" >&2
{
  echo "=== collected_at ==="
  date -u +%Y-%m-%dT%H:%M:%SZ
  echo ""
  echo "=== git HEAD (이 디렉토리) ==="
  git rev-parse HEAD 2>&1 || echo "(git 저장소 아님 또는 조회 실패)"
  git log -1 --format='%H %ci %s' 2>&1 || true
  echo ""
  echo "=== scripts/git_pull_deploy.sh 가 기록한 마지막 배포 SHA (있다면) ==="
  cat "${HOME}/bin/.citec_kb_git_deployed" 2>/dev/null || echo "(파일 없음 — git_pull_deploy.sh로 배포한 적 없거나 기록 전)"
  echo ""
  echo "=== docker compose ps ==="
  sudo docker compose ps 2>&1 || docker compose ps 2>&1 || echo "(docker compose ps 실패)"
  echo ""
  echo "=== GET /v1/health ==="
  curl -sf http://127.0.0.1:8573/v1/health 2>&1 || echo "(health 조회 실패)"
  echo ""
} > "$META_OUT"

echo "[collect] SQL 진단 수집 → ${SQL_OUT}" >&2
if [[ -n "$PG_CONTAINER" ]]; then
  docker exec -i "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" \
    < "${SCRIPT_DIR}/collect_prod_diagnostics.sql" > "$SQL_OUT" 2>&1
elif [[ -n "${DATABASE_URL:-}" ]]; then
  psql "$DATABASE_URL" -f "${SCRIPT_DIR}/collect_prod_diagnostics.sql" > "$SQL_OUT" 2>&1
else
  # docker-compose 서비스 이름이 "postgres"라고 가정 (이 저장소의 기존
  # docker-compose.yml 관례) — 다르면 PG_CONTAINER=실제이름 으로 지정.
  CID="$(sudo docker compose ps -q postgres 2>/dev/null || docker compose ps -q postgres 2>/dev/null || true)"
  if [[ -z "$CID" ]]; then
    echo "오류: postgres 컨테이너를 찾을 수 없습니다. PG_CONTAINER=<컨테이너이름> 또는 DATABASE_URL을 지정해 재실행하세요." >&2
    exit 1
  fi
  docker exec -i "$CID" psql -U "$PG_USER" -d "$PG_DB" \
    < "${SCRIPT_DIR}/collect_prod_diagnostics.sql" > "$SQL_OUT" 2>&1
fi

echo "" >&2
echo "[collect] 완료. 아래 두 파일을 개발 환경으로 가져오세요:" >&2
echo "  ${META_OUT}" >&2
echo "  ${SQL_OUT}" >&2
