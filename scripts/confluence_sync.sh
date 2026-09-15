#!/usr/bin/env bash
# Confluence 증분 동기화 (app.confluence.sync_cli), cron용 래퍼.
#
# scripts/ingest_and_embed.sh와 동일한 패턴: 실행 중인 api 컨테이너 안에서
# docker compose exec으로 실행 — 컨테이너의 모델 캐시(/models)와 DB/Confluence
# 자격증명(CONFLUENCE_BASE_URL/USERNAME/PASSWORD)을 공유한다.
#
# 반드시 먼저 손으로 --dry-run 확인부터 (docs/CONFLUENCE_SYNC.md 참고):
#   scripts/confluence_sync.sh --dry-run --root-id 222532692 --max-pages 5
#   scripts/confluence_sync.sh --dry-run
#   scripts/confluence_sync.sh              # 실제 반영, DB에서 last_sync_at 갱신 여부 확인
#
# 위 3단계가 문제없이 끝난 뒤에만 크론에 등록할 것 — 아래 라인을 그대로
# crontab -e 에 추가 (박재화 확정: 하루 1회, 점심시간 12시 — 이 Confluence
# 계정은 본인 브라우징/MY-OS 등 다른 도구와 공유되므로 급할 것 없이 느리게):
#   0 12 * * * /usr/bin/flock -n /tmp/citec-kb-confluence-sync.lock \
#     /path/to/citec-kb/scripts/confluence_sync.sh >> /path/to/citec-kb/logs/cron_confluence_sync.log 2>&1
#
# Usage: scripts/confluence_sync.sh [--dry-run] [--sources confluence_docs,tech_repo]
#                                   [--max-pages N] [--root-id ID] [--raw-dir DIR]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${PROJECT_DIR}/logs"
mkdir -p "$LOG_DIR"

cd "$PROJECT_DIR"

STAMP="$(date +%Y%m%d_%H%M%S)"
LOG="${LOG_DIR}/confluence_sync_${STAMP}.log"

echo "[confluence_sync] 시작 $(date -Iseconds) args=$*"
if ! docker compose exec -T api python3 -m app.confluence.sync_cli -v "$@" | tee "$LOG"; then
  echo "❌ confluence sync 실패 (로그: $LOG)" >&2
  exit 1
fi
echo "✅ 완료 $(date -Iseconds) — log=$LOG"
