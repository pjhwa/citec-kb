#!/usr/bin/env bash
# map_inventory.sh — confluence_map 주간 전신(전체 메타데이터 대조) 로테이션,
# cron용 래퍼.
#
# 왜 필요한가 (2026-10 백필에서 실제로 겪음): 매일 도는 map_sync.sh(증분,
# CQL lastmodified > cursor)는 "페이지가 삭제됐거나 추적 중인 루트 밖으로
# 옮겨졌다"를 구조적으로 절대 감지하지 못한다 — "무엇이 바뀌었나" 쿼리로는
# "무엇이 없어졌나"를 알 수 없기 때문. DevOps001 백필 때 바로 이 이유로
# 184건이 갱신도, 삭제처리(archive)도 안 된 채 남아있었다(archive 판단엔
# 전체 재대조가 필요한데 그게 한 번도 주기적으로 돈 적이 없었음).
#
# app.confluence.map_sync.run_map_inventory()가 그 전체 재대조 함수인데,
# 공간 하나당 처음 크롤할 때와 똑같은 비용(전체 재크롤)이 든다. 16개 공간
# 전부를 매주 돌리면 매주 이번 백필 전체(약 78,251페이지) 분량을 반복하게
# 되므로, 이 스크립트는 app.confluence.map_inventory_cli의 로테이션을 그대로
# 호출해 **한 번에 일부 공간만** 처리한다 — 기본 3개, 16개 공간이면 약
# 6주에 한 바퀴.
#
# 반드시 먼저 손으로 --dry-run 확인부터:
#   scripts/map_inventory.sh --dry-run --count 1
#   scripts/map_inventory.sh --count 1          # 실제 반영, 결과 JSON 확인
#
# 문제없으면 크론에 등록 (목표: 주 1회, 다른 크론과 안 겹치는 한가한 시간대 —
# daily 크론들(02/04/11/12시)과 안 겹치게, 예: 일요일 20시):
#   0 20 * * 0 /usr/bin/flock -n /tmp/citec-kb-map-inventory.lock \
#     /path/to/citec-kb/scripts/map_inventory.sh >> /path/to/citec-kb/logs/cron_map_inventory.log 2>&1
#
# map_sync.sh/map_backfill.sh와 같은 lock(_sync_run_lock, DB advisory lock)을
# 공유하므로 둘 중 뭔가 돌고 있으면 이 스크립트는 그 소스를 "already_running"
# 으로 건너뛴다(에러 아님) — 로테이션 인덱스는 건드리지 않고 다음 주 같은
# 자리에서 재시도한다.
#
# 레이트리밋 임시 상향: CONFLUENCE_RATE_LIMIT_RPS=1.0 scripts/map_inventory.sh ...
# (docker compose exec는 -e 없이는 호스트 환경변수를 안 넘기므로, 아래에서
# 명시적으로 forward한다 — scripts/map_backfill.sh에서 겪은 문제와 동일)
#
# Usage: scripts/map_inventory.sh [--dry-run] [--count N] [--source-ids id1,id2,...]
#                                  [--raw-dir DIR] [-v]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SERVICE="${MAP_INVENTORY_SERVICE:-api}"
LOG_DIR="${PROJECT_DIR}/logs"
mkdir -p "$LOG_DIR"

cd "$PROJECT_DIR"

# docker compose exec는 -e로 명시하지 않으면 호스트 쉘의 환경변수를
# 컨테이너로 넘기지 않는다 (scripts/map_backfill.sh에서 실제로 겪은 문제,
# 위 주석 참고).
EXEC_ENV_ARGS=()
if [[ -n "${CONFLUENCE_RATE_LIMIT_RPS:-}" ]]; then
  EXEC_ENV_ARGS+=(-e "CONFLUENCE_RATE_LIMIT_RPS=${CONFLUENCE_RATE_LIMIT_RPS}")
fi

STAMP="$(date +%Y%m%d_%H%M%S)"
LOG="${LOG_DIR}/map_inventory_${STAMP}.log"

echo "[map_inventory] 시작 $(date -Iseconds) args=$*"
if ! docker compose exec -T "${EXEC_ENV_ARGS[@]+"${EXEC_ENV_ARGS[@]}"}" "$SERVICE" \
    python3 -m app.confluence.map_inventory_cli -v "$@" | tee "$LOG"; then
  echo "❌ map inventory 실패 (로그: $LOG)" >&2
  exit 1
fi
echo "✅ 완료 $(date -Iseconds) — log=$LOG"
