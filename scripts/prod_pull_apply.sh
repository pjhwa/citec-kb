#!/usr/bin/env bash
# prod_pull_apply.sh — 개발에서 실행. scripts/prod_pull_export.sh가 만든 전체
# DB+data/raw 번들을 적용해 개발을 운영과 완전히 동일하게 맞춘다.
#
# ⚠ 파괴적 작업: 기존 개발 DB는 DROP SCHEMA 후 통째로 재생성되고, data/raw는
#   rsync --delete로 완전 미러링된다 — 개발에만 있던 DB 행/파일은 전부 사라진다
#   (scripts/sync_apply.sh의 증분 적용과 달리 삭제를 무시하지 않는다).
#   기본적으로 적용 전 현재 개발 DB를 백업한다(--no-backup으로 생략 가능).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BUNDLE=""
NO_BACKUP=false
NO_RAW=false
NO_RESTART=false
YES=false
DRY_RUN=false

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BOLD='\033[1m'; RESET='\033[0m'
log()  { echo -e "${GREEN}[$(date '+%H:%M:%S')]${RESET} $*" >&2; }
warn() { echo -e "${YELLOW}[$(date '+%H:%M:%S')] ⚠${RESET}  $*" >&2; }
err()  { echo -e "${RED}[$(date '+%H:%M:%S')] ✗${RESET}  $*" >&2; }
die()  { err "$*"; exit 1; }
banner() {
  echo -e "\n${BOLD}════════════════════════════════════════════════${RESET}" >&2
  echo -e "${BOLD}  $*${RESET}" >&2
  echo -e "${BOLD}════════════════════════════════════════════════${RESET}" >&2
}

usage() {
  cat <<'EOF'
prod_pull_apply.sh — 개발: prod_pull_export.sh 번들을 적용해 운영과 완전 동일화

USAGE
  scripts/prod_pull_apply.sh BUNDLE.tar.gz [옵션]

⚠ 파괴적 작업입니다:
  - 개발 Postgres 는 DROP SCHEMA public CASCADE 후 번들 내용으로 통째로 복원됨
    (개발 전용 행 삭제 포함 — scripts/sync_apply.sh의 증분 적용과 다름)
  - data/raw 는 rsync --delete 로 번들과 완전히 동일하게 미러링됨
    (개발 전용 파일 삭제 포함)
  기본적으로 적용 직전 현재 개발 DB를 data/backups/ 에 백업한다.

옵션:
  --project DIR    레포 루트 (기본: 이 스크립트 상위)
  --no-backup      적용 전 백업 생략
  --no-raw         data/raw 미러링 생략 (DB만 적용, 번들에 raw.tar.gz 있어도 무시)
  --no-restart     적용 후 api/worker 재시작 생략
  --yes, -y        확인 프롬프트 생략 (백업은 --no-backup 없으면 그래도 실행됨)
  --dry-run, -n    번들 내용(manifest)만 표시, 아무것도 바꾸지 않음
  -h, --help       도움말

예시:
  scripts/prod_pull_apply.sh --dry-run ~/citec-kb-prod-full-2026-09-22_140000.tar.gz
  scripts/prod_pull_apply.sh -y ~/citec-kb-prod-full-2026-09-22_140000.tar.gz
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT_DIR="${2:?}"; shift 2 ;;
    --no-backup) NO_BACKUP=true; shift ;;
    --no-raw) NO_RAW=true; shift ;;
    --no-restart) NO_RESTART=true; shift ;;
    --yes|-y) YES=true; shift ;;
    --dry-run|-n) DRY_RUN=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) BUNDLE="$1"; shift ;;
  esac
done

[[ -n "$BUNDLE" && -f "$BUNDLE" ]] || { err "BUNDLE.tar.gz 인자 필요"; usage; exit 1; }
command -v docker >/dev/null 2>&1 || die "docker 명령을 찾을 수 없습니다."
[[ -f "${PROJECT_DIR}/docker-compose.yml" ]] || die "docker-compose.yml 없음: ${PROJECT_DIR}"

PG_USER="${POSTGRES_USER:-citec}"
PG_DB="${POSTGRES_DB:-citec_knowledge}"

compose() { docker compose -f "${PROJECT_DIR}/docker-compose.yml" "$@"; }

STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT

tar xzf "$BUNDLE" -C "$STAGING"
BUNDLE_DIR="${STAGING}/bundle"
[[ -d "$BUNDLE_DIR" ]] || die "번들 구조 이상 (bundle/ 없음): $BUNDLE"
[[ -f "${BUNDLE_DIR}/db.sql.gz" ]] || die "번들에 db.sql.gz 없음: $BUNDLE"

banner "prod_pull_apply — $(basename "$BUNDLE")"
if [[ -f "${BUNDLE_DIR}/manifest.txt" ]]; then
  cat "${BUNDLE_DIR}/manifest.txt" >&2
else
  warn "manifest.txt 없음 (오래된 번들?)"
fi
echo "" >&2

HAS_RAW=false
[[ -f "${BUNDLE_DIR}/raw.tar.gz" ]] && ! $NO_RAW && HAS_RAW=true

warn "이 작업은 개발 DB(db=${PG_DB})를 완전히 지우고 번들 내용으로 대체합니다."
$HAS_RAW && warn "data/raw 도 rsync --delete 로 완전히 미러링됩니다 (개발 전용 파일 삭제)."
warn "개발에만 있던 테스트 데이터는 모두 사라집니다."

if $DRY_RUN; then
  warn "DRY-RUN — 여기서 종료, 아무것도 바꾸지 않음"
  exit 0
fi

if ! $YES; then
  echo -e "${RED}${BOLD}계속할까요? [y/N]${RESET} " >&2
  read -r CONFIRM
  [[ "${CONFIRM}" =~ ^[Yy]$ ]] || { warn "취소"; exit 0; }
fi

if ! $NO_BACKUP; then
  log "적용 전 현재 개발 DB 백업"
  BACKUP_DIR="${PROJECT_DIR}/data/backups"
  mkdir -p "$BACKUP_DIR"
  BACKUP_FILE="${BACKUP_DIR}/pre-prod-pull-$(date +%Y%m%d_%H%M%S).sql.gz"
  if compose exec -T postgres pg_dump -U "$PG_USER" "$PG_DB" 2>/dev/null | gzip > "$BACKUP_FILE" \
      && [[ -s "$BACKUP_FILE" ]]; then
    log "백업 완료: $(basename "$BACKUP_FILE") ($(du -sh "$BACKUP_FILE" | cut -f1))"
  else
    rm -f "$BACKUP_FILE"
    warn "백업 실패(빈 DB이거나 postgres 미기동일 수 있음) — 백업 없이 계속 진행"
  fi
fi

log "[1/4] public 스키마 초기화"
compose exec -T postgres psql -U "$PG_USER" -d "$PG_DB" -v ON_ERROR_STOP=1 -q <<'SQL'
DROP SCHEMA IF EXISTS public CASCADE;
CREATE SCHEMA public;
GRANT ALL ON SCHEMA public TO citec;
GRANT ALL ON SCHEMA public TO public;
SET maintenance_work_mem = '512MB';
SQL

log "[2/4] dump 적용 (quiet · ON_ERROR_STOP) — 크기에 따라 수 분 걸릴 수 있음"
ERRF="${STAGING}/restore-errors.log"
: > "$ERRF"
set +e
gunzip -c "${BUNDLE_DIR}/db.sql.gz" | compose exec -T postgres \
  psql -U "$PG_USER" -d "$PG_DB" -v ON_ERROR_STOP=1 -q -o /dev/null 2>>"$ERRF"
RC=$?
set -e
ERR_N=0
if [[ -s "$ERRF" ]]; then
  ERR_N=$(grep -cE '^ERROR:|^FATAL:|ERROR:  ' "$ERRF" 2>/dev/null || true)
  ERR_N=${ERR_N//$'\n'/}
  ERR_N=${ERR_N:-0}
fi
if [[ "$RC" -ne 0 || "$ERR_N" -gt 0 ]]; then
  [[ -s "$ERRF" ]] && tail -50 "$ERRF" >&2
  die "복원 실패 (exit=$RC ERROR=${ERR_N})"
fi
log "복원 완료 (exit=0, ERROR=0)"

log "[3/4] 건수 확인"
# 스키마에 documents/chunks/embeddings 가 없는 등 예상 밖의 이유로 이 쿼리 자체가
# 실패해도(복원이 실제로 실패한 게 아니라 이 진단성 조회만 실패한 경우), 방금 성공한
# 복원 단계를 set -e 로 인해 통째로 실패 처리하지 않도록 항상 성공으로 처리한다 —
# 아래에서 COUNTS 가 비어 있으면 그 사실 자체를 로그로 남긴다.
COUNTS="$(compose exec -T postgres psql -U "$PG_USER" -d "$PG_DB" -t -A -c \
  "SELECT (SELECT count(*) FROM documents)||' '||
          (SELECT count(*) FROM chunks WHERE is_active)||' '||
          (SELECT count(*) FROM embeddings);" 2>/dev/null | tr -d '\r' | head -1 || true)"
if [[ -z "$COUNTS" ]]; then
  warn "건수 조회 실패 (documents/chunks/embeddings 테이블이 없는 스키마이거나 일시적 오류) — 복원 자체는 이미 완료됨"
  DOCS=""; CHUNKS=""; EMB=""
else
  DOCS=$(echo "$COUNTS" | awk '{print $1}')
  CHUNKS=$(echo "$COUNTS" | awk '{print $2}')
  EMB=$(echo "$COUNTS" | awk '{print $3}')
  log "documents=${DOCS:-0}  chunks=${CHUNKS:-0}  embeddings=${EMB:-0}"
fi

if $HAS_RAW; then
  log "[4/4] data/raw rsync --delete 미러링"
  RAW_STAGE="${STAGING}/raw_extract"
  mkdir -p "$RAW_STAGE"
  tar xzf "${BUNDLE_DIR}/raw.tar.gz" -C "$RAW_STAGE"
  [[ -d "${RAW_STAGE}/raw" ]] || die "raw.tar.gz 안에 raw/ 없음 — 번들 손상 의심"
  mkdir -p "${PROJECT_DIR}/data/raw"
  rsync -a --delete "${RAW_STAGE}/raw/" "${PROJECT_DIR}/data/raw/"
  N_RAW=$(find "${PROJECT_DIR}/data/raw" -type f | wc -l | tr -d ' ')
  log "data/raw 미러링 완료 (${N_RAW}개 파일)"
else
  log "[4/4] data/raw 미러링 생략 (--no-raw 이거나 번들에 raw 없음)"
fi

if ! $NO_RESTART; then
  log "api/worker 재시작 (alembic 은 api entrypoint 가 자동 실행)"
  compose restart api worker 2>/dev/null || warn "재시작 실패 — 수동으로 확인하세요 (docker compose ps)"
fi

banner "✅ prod_pull_apply 완료"
echo "  documents=${DOCS:-0}  chunks=${CHUNKS:-0}  embeddings=${EMB:-0}" >&2
$HAS_RAW && echo "  raw_files=${N_RAW:-0}" >&2
echo "  확인: curl -s localhost:8573/v1/health | jq ." >&2
