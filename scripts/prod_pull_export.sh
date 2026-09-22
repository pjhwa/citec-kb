#!/usr/bin/env bash
# prod_pull_export.sh — 운영에서 실행. Postgres 전체 + data/raw 전체를 번들로
# 묶는다 (증분 아님 — scripts/sync_export.sh와 달리 "개발을 운영과 완전히
# 동일하게 맞춘다"는 목적. 개발 쪽 반영은 scripts/prod_pull_apply.sh가 한다).
#
# scripts/out.sh --data --pg-dump 와 성격은 비슷하지만 그건 개발→운영 방향
# 배포 번들 체계(out.sh/in.sh)의 일부이고, 이 스크립트는 반대 방향(운영→개발)
# 전용 독립 스크립트다.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUT_DIR="${HOME}/tmp"
INCLUDE_RAW=true
TS="$(date '+%Y-%m-%d_%H%M%S')"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; RESET='\033[0m'
log()  { echo -e "${GREEN}[$(date '+%H:%M:%S')]${RESET} $*" >&2; }
warn() { echo -e "${YELLOW}[$(date '+%H:%M:%S')] ⚠${RESET}  $*" >&2; }
err()  { echo -e "${RED}[$(date '+%H:%M:%S')] ✗${RESET}  $*" >&2; }
die()  { err "$*"; exit 1; }

usage() {
  cat <<'EOF'
prod_pull_export.sh — 운영: 개발로 가져갈 전체 DB + data/raw 번들 생성

USAGE
  scripts/prod_pull_export.sh [옵션]

Postgres 전체(pg_dump, --no-owner --no-acl)와 data/raw 전체를 tar.gz 하나로
묶는다. scripts/sync_export.sh(증분, 삭제 무시)와 달리 이건 "개발을 운영과
완전히 동일하게" 맞추기 위한 것 — 개발 쪽에서 prod_pull_apply.sh가 적용할 때
기존 개발 DB/data/raw는 통째로 대체(개발 전용 데이터 삭제 포함)된다.

옵션:
  --project DIR    레포 루트 (기본: 이 스크립트 상위)
  --out DIR        출력 디렉터리 (기본: ~/tmp)
  --no-raw         data/raw 제외, DB만 (개발에 이미 동일 raw 코퍼스가 있을 때)
  -h, --help       도움말

출력: citec-kb-prod-full-<TS>.tar.gz (bundle/db.sql.gz [+ bundle/raw.tar.gz] + manifest.txt)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT_DIR="${2:?}"; shift 2 ;;
    --out) OUT_DIR="${2:?}"; shift 2 ;;
    --no-raw) INCLUDE_RAW=false; shift ;;
    -h|--help) usage; exit 0 ;;
    *) err "알 수 없는 옵션: $1"; usage; exit 1 ;;
  esac
done

PG_USER="${POSTGRES_USER:-citec}"
PG_DB="${POSTGRES_DB:-citec_knowledge}"

command -v docker >/dev/null 2>&1 || die "docker 명령을 찾을 수 없습니다."
[[ -f "${PROJECT_DIR}/docker-compose.yml" ]] || die "docker-compose.yml 없음: ${PROJECT_DIR}"

compose() { sudo docker compose -f "${PROJECT_DIR}/docker-compose.yml" "$@"; }

STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT
mkdir -p "${STAGING}/bundle"

log "Postgres 덤프 (db=${PG_DB}, --no-owner --no-acl)"
if ! compose exec -T postgres pg_dump --no-owner --no-acl -U "$PG_USER" "$PG_DB" \
    | gzip > "${STAGING}/bundle/db.sql.gz"; then
  die "pg_dump 실패 — postgres 컨테이너 기동 여부 확인"
fi
[[ -s "${STAGING}/bundle/db.sql.gz" ]] || die "덤프 결과가 비어 있습니다"
log "덤프 완료: $(du -sh "${STAGING}/bundle/db.sql.gz" | cut -f1)"

log "건수 확인 (참고용, 반영 전 육안 확인용)"
COUNTS="$(compose exec -T postgres psql -U "$PG_USER" -d "$PG_DB" -t -A -c \
  "SELECT 'documents='||(SELECT count(*) FROM documents)||
          ' chunks='||(SELECT count(*) FROM chunks WHERE is_active)||
          ' embeddings='||(SELECT count(*) FROM embeddings);" 2>/dev/null | tr -d '\r' | head -1 || true)"
[[ -n "$COUNTS" ]] && log "운영 건수: $COUNTS"

RAW_FILES=0
if $INCLUDE_RAW; then
  RAW_DIR="${PROJECT_DIR}/data/raw"
  if [[ -d "$RAW_DIR" ]]; then
    log "data/raw 전체 tar (증분 아님 — 개발 쪽에서 rsync --delete로 완전 미러링됨)"
    tar czf "${STAGING}/bundle/raw.tar.gz" -C "${PROJECT_DIR}/data" raw
    RAW_FILES=$(find "$RAW_DIR" -type f | wc -l | tr -d ' ')
    log "raw 파일 ${RAW_FILES}개, $(du -sh "${STAGING}/bundle/raw.tar.gz" | cut -f1)"
  else
    warn "data/raw 없음 — 건너뜀"
    INCLUDE_RAW=false
  fi
fi

{
  echo "# citec-kb prod-pull full export (증분 아님 — 개발과 완전 동일화용)"
  echo "created=$(date -Iseconds)"
  echo "host=$(hostname)"
  echo "db=${PG_DB}"
  echo "counts=${COUNTS:-unknown}"
  echo "include_raw=${INCLUDE_RAW}"
  echo "raw_files=${RAW_FILES}"
  echo "apply_on_dev=scripts/prod_pull_apply.sh <이 번들>"
  echo "경고: 개발 쪽 적용 시 기존 개발 DB/data/raw 는 이 번들 내용으로 완전히 대체됩니다"
  echo "      (개발 전용 데이터·파일 삭제 포함)."
} > "${STAGING}/bundle/manifest.txt"
cat "${STAGING}/bundle/manifest.txt" >&2

mkdir -p "$OUT_DIR"
OUT_TGZ="${OUT_DIR}/citec-kb-prod-full-${TS}.tar.gz"
(cd "$STAGING" && tar czf "$OUT_TGZ" bundle/)
log "완료: ${OUT_TGZ} ($(du -sh "$OUT_TGZ" | cut -f1))"
echo "$OUT_TGZ"
