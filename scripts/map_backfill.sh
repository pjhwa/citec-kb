#!/usr/bin/env bash
# map_backfill.sh — 운용 서버에서 confluence_map 전체 백필을 실행한다.
#
# api 컨테이너 안에서 python -m app.confluence.map_backfill_cli 를 돌린다.
# 크롤(발췌) → 실패 요청 최대 3회 재시도 → 인제스트 1회 → 임베딩.
# 끝난 소스는 상태 파일에서 건너뛴다. API 컨테이너를 재시작하면 이 프로세스도 죽는다.
#
#   scripts/map_backfill.sh --dry-run          # 소스 목록과 예상 시간만
#   scripts/map_backfill.sh                    # 백그라운드로 전체 실행
#   scripts/map_backfill.sh --foreground       # 끝날 때까지 붙어서 실행 (종료 코드 유지)
#   scripts/map_backfill.sh --from-scratch     # 완료된 소스도 다시 크롤
#   scripts/map_backfill.sh --source-ids confluence_map_spc,confluence_map_guid
#
# 로그: data/raw/confluence_map/.backfill.log
# 상태: data/raw/confluence_map/.backfill_state.json
# 진행/완료: 관리 화면 admin.html 의 맵 백필 줄 (10초마다 갱신)
set -euo pipefail

PROJECT_DIR="${MAP_BACKFILL_DIR:-${HOME}/citec-kb}"
SERVICE="${MAP_BACKFILL_SERVICE:-api}"
LOG_REL="data/raw/confluence_map/.backfill.log"
FOREGROUND=false
PY_ARGS=()

usage() {
  sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --foreground) FOREGROUND=true; shift ;;
    --dry-run|--from-scratch|-v|--verbose)
      PY_ARGS+=("$1"); shift ;;
    --source-ids)
      PY_ARGS+=("$1" "${2:?--source-ids 값이 필요합니다}")
      shift 2 ;;
    --raw-dir)
      PY_ARGS+=("$1" "${2:?--raw-dir 값이 필요합니다}")
      shift 2 ;;
    *) echo "알 수 없는 인자: $1" >&2; usage >&2; exit 2 ;;
  esac
done

cd "$PROJECT_DIR"

if ! docker compose ps --status running --services 2>/dev/null | grep -qx "$SERVICE"; then
  echo "중단: ${SERVICE} 컨테이너가 실행 중이 아닙니다. (cd ${PROJECT_DIR})" >&2
  exit 1
fi

if docker compose exec -T "$SERVICE" python -c 'import os,sys
needle="app.confluence.map_backfill_cli"
for pid in os.listdir("/proc"):
    if not pid.isdigit() or int(pid)==os.getpid():
        continue
    try:
        cmd=open(f"/proc/{pid}/cmdline","rb").read().replace(b"\x00",b" ").decode()
    except OSError:
        continue
    if needle in cmd:
        sys.exit(0)
sys.exit(1)' >/dev/null 2>&1; then
  echo "중단: 백필이 이미 실행 중입니다. 로그: ${PROJECT_DIR}/${LOG_REL}" >&2
  exit 2
fi

mkdir -p "${PROJECT_DIR}/data/raw/confluence_map"

dry=false
for a in "${PY_ARGS[@]+"${PY_ARGS[@]}"}"; do
  if [[ "$a" == "--dry-run" ]]; then dry=true; fi
done

if $dry || $FOREGROUND; then
  echo "백필을 이 터미널에서 실행합니다. 컨테이너를 재시작하지 마세요."
  docker compose exec -T "$SERVICE" python -m app.confluence.map_backfill_cli "${PY_ARGS[@]+"${PY_ARGS[@]}"}"
  exit $?
fi

LOG="${PROJECT_DIR}/${LOG_REL}"
echo "$(date -Is) host launching map_backfill" >> "$LOG"
# -T: no tty. Without it, detaching sends SIGHUP and the process dies
# before it can write a log line. trap keeps that disposition across exec.
echo "백필을 백그라운드로 시작합니다."
docker compose exec -d -T "$SERVICE" \
  sh -c 'trap "" HUP; cd /app; exec python -u -m app.confluence.map_backfill_cli "$@" >> /data/raw/confluence_map/.backfill.log 2>&1' \
  sh "${PY_ARGS[@]+"${PY_ARGS[@]}"}"
sleep 2
if ! docker compose exec -T "$SERVICE" python -c 'import os,sys
needle="app.confluence.map_backfill_cli"
for pid in os.listdir("/proc"):
    if not pid.isdigit() or int(pid)==os.getpid():
        continue
    try:
        cmd=open(f"/proc/{pid}/cmdline","rb").read().replace(b"\x00",b" ").decode()
    except OSError:
        continue
    if needle in cmd:
        sys.exit(0)
sys.exit(1)' >/dev/null 2>&1; then
  echo "프로세스가 바로 종료됐습니다. 로그 끝:" >&2
  tail -n 40 "$LOG" >&2 || true
  exit 1
fi
echo "로그:  tail -f ${LOG}"
echo "상태:  ${PROJECT_DIR}/data/raw/confluence_map/.backfill_state.json"
echo "화면:  admin.html 의 맵 백필 줄. 임베딩이 끝나면 '백필 완료'가 남습니다."
echo "컨테이너(api)를 재시작하면 이 작업도 같이 종료됩니다. 다시 실행하면 끝난 소스부터 이어갑니다."
