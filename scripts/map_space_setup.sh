#!/usr/bin/env bash
# map_space_setup.sh — confluence_map 공간 root 재구성 (운용 서버, api 컨테이너 안에서 실행).
#
# 부분 크롤이던 공간을 "최상위 페이지 root = 전체 공간 크롤"로 바꾸고, 신규 공간을 추가하고,
# 폐기 공간(genaibusiness)을 제거한다. 대상 목록은
# apps/api/app/confluence/map_space_setup.py 의 SPACE_HOMES / REMOVE_SOURCES.
# LOOKIN/TechRepo/ServiceExcellenceTeam/ICLOUDUT/SPC 는 건드리지 않는다.
#
#   scripts/map_space_setup.sh                    # 1) 계획만 출력 (Confluence 로 페이지/공간 검증, DB 변경 없음)
#   scripts/map_space_setup.sh --apply            # 2) 계획을 DB 에 반영 (크롤은 안 함)
#   scripts/map_space_setup.sh --apply --backfill # 2 + 3) 반영 후 해당 소스만 전체 백필을 백그라운드로 시작
#
# 3) 의 백필은 scripts/map_backfill.sh --from-scratch 와 같다. 수 시간 걸릴 수 있으니
# 부하가 적은 시간대에, 매일 13시 map_sync 크론과 겹치지 않게 실행한다. 진행: admin.html 의
# 맵 백필 줄 또는 data/raw/confluence_map/.backfill.log.
#
# 반드시 첫 실행은 인자 없이(계획 확인) 하고, ERROR 가 없고 WARNING 이 이해된 뒤에 --apply.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_DIR"

APPLY=false
BACKFILL=false
PY_ARGS=()
SKIP_ARGS=()
for a in "$@"; do
  case "$a" in
    --apply) APPLY=true; PY_ARGS+=("$a") ;;
    --backfill) BACKFILL=true ;;
    --skip-verify) PY_ARGS+=("$a"); SKIP_ARGS+=("$a") ;;
    -h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "알 수 없는 인자: $a" >&2; exit 64 ;;
  esac
done
if $BACKFILL && ! $APPLY; then
  echo "--backfill 은 --apply 와 함께만 쓸 수 있습니다." >&2
  exit 64
fi

# 백필 대상은 반영 전에 계산한다 (반영 후에는 set_roots 가 unchanged 로 바뀌어 목록에서 빠진다).
IDS=""
if $BACKFILL; then
  IDS="$(docker compose exec -T api python3 -m app.confluence.map_space_setup --print-source-ids ${SKIP_ARGS[@]+"${SKIP_ARGS[@]}"})"
fi

docker compose exec -T api python3 -m app.confluence.map_space_setup "${PY_ARGS[@]+"${PY_ARGS[@]}"}"

if $BACKFILL; then
  if [[ -z "$IDS" ]]; then
    echo "백필할 소스가 없습니다."
    exit 0
  fi
  echo "백필 시작: $IDS"
  exec "${SCRIPT_DIR}/map_backfill.sh" --from-scratch --source-ids "$IDS"
fi
