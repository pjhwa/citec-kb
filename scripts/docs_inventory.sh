#!/usr/bin/env bash
# docs_inventory.sh — confluence_docs / tech_repo 주간 전체 대조 (cron용 래퍼).
#
# 일일 증분 sync는 "위치만 바뀐" 페이지와 "추적 루트 밖으로 나간/삭제된" 페이지를
# 잡지 못한다. 이 작업은 모든 루트를 전부 나열(조상 정보 포함)해 DB와 비교하고,
# 이동·이름변경·신규 페이지만 다시 가져온 뒤 ingest+embed 한다. 목록에서 사라진
# 문서는 보관(archived) 처리하고 원본 파일은 <raw>/<source_type>/.archived/ 로 옮긴다.
# (docs/CONFLUENCE_SYNC.md "주간 전체 대조" 참고)
#
# 반드시 먼저 --dry-run 으로 would_archive 와 fetch_by_reason 을 눈으로 확인:
#   scripts/docs_inventory.sh --dry-run
#   scripts/docs_inventory.sh               # 실제 반영
#
# cron: 일일 sync 와 같은 flock 파일을 써서 서로 겹치지 않게 한다 (주 1회, 토요일 18시 — 20시에 시작하는
# map_inventory 와 Confluence 호출이 겹치지 않게, 이 작업은 보통 수십 분이면 끝난다).
# 운영 crontab 의 일일 sync/map_sync 가 쓰는 공용 lock 이름은 citec-kb-confluence-shared.lock:
#   0 18 * * 6 /usr/bin/flock -n /tmp/citec-kb-confluence-shared.lock \
#     /path/to/citec-kb/scripts/docs_inventory.sh >> /path/to/citec-kb/logs/cron_docs_inventory.log 2>&1
#
# Usage: scripts/docs_inventory.sh [--dry-run] [--sources confluence_docs,tech_repo]
#                                  [--no-archive] [--max-archive-ratio R] [--allow-full-fetch] [--raw-dir DIR]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${PROJECT_DIR}/logs"
mkdir -p "$LOG_DIR"

cd "$PROJECT_DIR"

STAMP="$(date +%Y%m%d_%H%M%S)"
LOG="${LOG_DIR}/docs_inventory_${STAMP}.log"

echo "[docs_inventory] 시작 $(date -Iseconds) args=$*"
if ! docker compose exec -T api python3 -m app.confluence.docs_inventory_cli -v "$@" | tee "$LOG"; then
  echo "❌ docs inventory 실패 (로그: $LOG)" >&2
  exit 1
fi
echo "✅ 완료 $(date -Iseconds) — log=$LOG"
