#!/usr/bin/env bash
# Confluence 맵(구조 전용, confluence_map) 증분 동기화, cron용 래퍼.
#
# scripts/confluence_sync.sh와 동일한 패턴: 실행 중인 api 컨테이너 안에서
# docker compose exec으로 실행 — 컨테이너의 모델 캐시(/models)와 DB/Confluence
# 자격증명(CONFLUENCE_BASE_URL/USERNAME/PASSWORD)을 공유한다.
#
# 반드시 먼저 손으로 --dry-run 확인부터 (docs/CONFLUENCE_MAP.md 참고):
#   scripts/map_sync.sh --dry-run --root-id 601879661 --max-pages 5
#   scripts/map_sync.sh --dry-run
#   scripts/map_sync.sh              # 실제 반영, DB에서 cursor_advanced 확인
#
# 위 3단계가 문제없이 끝난 뒤에만 크론에 등록할 것 — 아래 라인을 그대로
# crontab -e 에 추가 (목표: 매일 1회. confluence_sync.sh와 같은 시각에 겹치게
# 돌리지 말 것 — 두 스크립트가 같은 Confluence 계정/같은
# CONFLUENCE_RATE_LIMIT_RPS 예산을 공유한다. 13시처럼 confluence_sync.sh의
# 12시 실행이 보통 끝나 있을 시각을 권장):
#   0 13 * * * /usr/bin/flock -n /tmp/citec-kb-map-sync.lock \
#     /path/to/citec-kb/scripts/map_sync.sh >> /path/to/citec-kb/logs/cron_map_sync.log 2>&1
#
# 두 크론이 실제로 겹쳐 도는 경우(예: confluence_sync.sh가 그날따라 오래
# 걸림)가 걱정되면, 두 flock 모두 같은 lock 파일 경로
# (예: /tmp/citec-kb-confluence-shared.lock)를 쓰도록 바꿔서 완전 직렬화할
# 수도 있다 — 기본은 별도 lock(각자 독립 실행, 시각으로만 회피)으로 둔다.
#
# Usage: scripts/map_sync.sh [--dry-run] [--source-ids id1,id2,...]
#                             [--max-pages N] [--root-id ID] [--raw-dir DIR]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${PROJECT_DIR}/logs"
mkdir -p "$LOG_DIR"

cd "$PROJECT_DIR"

STAMP="$(date +%Y%m%d_%H%M%S)"
LOG="${LOG_DIR}/map_sync_${STAMP}.log"

echo "[map_sync] 시작 $(date -Iseconds) args=$*"
if ! docker compose exec -T api python3 -m app.confluence.map_sync_cli -v "$@" | tee "$LOG"; then
  echo "❌ map sync 실패 (로그: $LOG)" >&2
  exit 1
fi
echo "✅ 완료 $(date -Iseconds) — log=$LOG"
