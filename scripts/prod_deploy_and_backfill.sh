#!/usr/bin/env bash
# prod_deploy_and_backfill.sh — 운영 서버에서 1회 실행: 백업 → 코드 배포 →
# lexicon 재시딩 → 전체 그래프 재백필까지 전부 한 번에.
#
# 실행 위치: 운영 서버 프로젝트 루트(citectools-vm-001, 보통
#            /DATA/jooksan.park/citec-kb) — docker-compose.yml이 있는 디렉토리.
#
# 사용법:
#   ./scripts/prod_deploy_and_backfill.sh              # 전체 실행(각 단계 확인 프롬프트 있음)
#   ./scripts/prod_deploy_and_backfill.sh -y            # 확인 프롬프트 생략(배포 스크립트에 그대로 전달)
#   ./scripts/prod_deploy_and_backfill.sh --dry-run      # 백업만 하고 이후 단계는 계획만 표시
#   ./scripts/prod_deploy_and_backfill.sh --skip-backup  # 백업을 이미 했으면 생략(위험 — 재확인 프롬프트)
#
# 이 스크립트가 하는 일(순서대로):
#   1. 사전 점검(디렉토리, docker compose 서비스 상태, 동시 실행 방지)
#   2. 작업 트리 dirty 확인 — .gitignore만 수정됐으면 자동 stash, 그 외는 중단
#   3. Postgres 백업(scripts/backup_postgres.sh)
#   4. Neo4j 백업(볼륨 mountpoint를 호스트 tar로 직접 — 이미지 pull 없음, air-gap 안전)
#   5. scripts/git_pull_deploy.sh 로 코드 배포 + 컨테이너 재시작 + 헬스체크(+자동 롤백)
#   6. .gitignore stash 복원(2번에서 stash했다면)
#   7. alembic 마이그레이션 버전 확인(엔트리포인트가 자동 실행 — 확인만)
#   8. cron에 graph_sync/sync_cli 관련 작업이 있으면 경고 후 사용자 확인 대기
#      (이 스크립트는 crontab을 직접 건드리지 않는다 — 사람이 직접 확인)
#   9. lexicon 재시딩: POST /v1/lexicon/seed (기본 replace=false, 비파괴)
#  10. 전체 그래프 재백필을 screen 세션에서 백그라운드로 시작(SSH 끊겨도 계속 진행)
#  11. 완료 판정에 필요한 명령어 안내 출력
#
# 안전장치:
#   - 백업 실패(빈 파일/0바이트)면 즉시 중단 — 배포로 넘어가지 않음
#   - git_pull_deploy.sh 자체의 안전장치(ff-only, dirty 체크, 자동 롤백)를 그대로 사용
#   - sync_cli는 자체 Postgres advisory lock이 있어(2026-10-13 수정) 혹시 cron이
#     안 멈췄어도 동시 실행끼리 레이스는 안 나지만, 그래도 cron은 멈추는 걸 권장
#     (멈추지 않으면 cron 쪽 실행이 lock_held_by_another_run으로 매번 실패 기록만 남음)
#   - 재백필 자체(삭제 없음, MERGE/치환만)는 되돌리기 쉬우므로 자동 진행하지만,
#     고립 유령 노드 삭제 등 파괴적 정리는 이 스크립트가 절대 하지 않는다
#     (scripts/verify/ghost_node_audit.py는 읽기 전용 별도 스크립트)
set -euo pipefail

PROJECT_DIR="$(pwd)"
YES=false
DRY_RUN=false
SKIP_BACKUP=false
BACKFILL_LOG="/tmp/citec_graph_backfill_$(date +%Y%m%d_%H%M%S).log"
SCREEN_SESSION="graph_resync"

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; BOLD=$'\033[1m'; RESET=$'\033[0m'
log()  { echo "${GREEN}[$(date '+%H:%M:%S')]${RESET} $*"; }
warn() { echo "${YELLOW}[$(date '+%H:%M:%S')] 경고:${RESET} $*"; }
err()  { echo "${RED}[$(date '+%H:%M:%S')] 오류:${RESET} $*" >&2; }
die()  { err "$*"; exit 1; }
banner() { echo ""; echo "${BOLD}════════════════════════════════════════════════${RESET}"; echo "${BOLD}  $*${RESET}"; echo "${BOLD}════════════════════════════════════════════════${RESET}"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    -y|--yes) YES=true; shift ;;
    -n|--dry-run) DRY_RUN=true; shift ;;
    --skip-backup) SKIP_BACKUP=true; shift ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "알 수 없는 옵션: $1 (--help 참고)" ;;
  esac
done

confirm() {
  $YES && return 0
  echo -e "${RED}${BOLD}$1 [y/N]${RESET} "
  read -r REPLY
  [[ "$REPLY" =~ ^[Yy]$ ]]
}

# ── 0. 사전 점검 ────────────────────────────────────────────────────────
banner "0. 사전 점검"
[[ -f "docker-compose.yml" ]] || die "docker-compose.yml이 없습니다 — 프로젝트 루트에서 실행하세요"
[[ -f "scripts/git_pull_deploy.sh" ]] || die "scripts/git_pull_deploy.sh가 없습니다"
command -v docker >/dev/null 2>&1 || die "docker 명령을 찾을 수 없습니다"
log "project: $PROJECT_DIR"
docker compose ps --status running --services || die "실행 중인 서비스 목록을 가져올 수 없습니다"

LOCK_FILE="/tmp/.citec_kb_prod_deploy_and_backfill.lock"
exec 201>"$LOCK_FILE"
flock -n 201 || die "다른 prod_deploy_and_backfill.sh가 이미 실행 중입니다 (lock: $LOCK_FILE)"

# ── 1. 작업 트리 dirty 확인(.gitignore만이면 자동 stash) ───────────────
banner "1. 작업 트리 상태 확인"
STASHED_GITIGNORE=false
DIRTY="$(git status --porcelain)"
if [[ -n "$DIRTY" ]]; then
  log "커밋되지 않은 변경 발견:"
  echo "$DIRTY" | sed 's/^/    /'
  if [[ "$DIRTY" == " M .gitignore" ]]; then
    log ".gitignore만 수정됨(알려진 운영 커스터마이징, 예: docker-compose.override.yml 무시 설정) — 자동 stash 후 배포 뒤 복원합니다"
    git stash push -m "prod-local gitignore (auto, prod_deploy_and_backfill.sh)" -- .gitignore
    STASHED_GITIGNORE=true
  else
    die ".gitignore 외의 변경이 섞여 있습니다 — 직접 확인 후(git status, git diff) 커밋/stash 하고 재실행하세요"
  fi
else
  log "작업 트리 clean"
fi

if $DRY_RUN; then
  warn "DRY-RUN — 백업/배포/재백필을 실제로 실행하지 않습니다. 계획만 보여주고 종료합니다."
  echo "  2. Postgres 백업 → data/backups/citec_knowledge_<timestamp>.sql.gz"
  echo "  3. Neo4j 볼륨 백업 → data/backups/neo4jdata_<timestamp>.tar.gz"
  echo "  4. scripts/git_pull_deploy.sh 실행(코드 배포 + 재시작 + 헬스체크)"
  echo "  5. POST /v1/lexicon/seed (replace=false)"
  echo "  6. screen -S ${SCREEN_SESSION} 에서 전체 그래프 재백필 시작"
  $STASHED_GITIGNORE && git stash pop
  exit 0
fi

# ── 2. Postgres 백업 ────────────────────────────────────────────────────
if $SKIP_BACKUP; then
  warn "--skip-backup 지정됨 — Postgres/Neo4j 백업을 생략합니다."
  confirm "정말로 백업 없이 계속할까요? 되돌릴 수 없는 작업은 아니지만 권장하지 않습니다." \
    || die "취소"
else
  banner "2. Postgres 백업"
  PG_BACKUP_OUTPUT="$(./scripts/backup_postgres.sh)"
  echo "$PG_BACKUP_OUTPUT"
  PG_BACKUP_FILE="$(echo "$PG_BACKUP_OUTPUT" | grep -oE 'path=\S+' | cut -d= -f2)"
  [[ -n "$PG_BACKUP_FILE" && -s "$PG_BACKUP_FILE" ]] || die "Postgres 백업 파일이 비어있거나 생성되지 않았습니다 — 중단"
  log "✅ Postgres 백업 확인: $PG_BACKUP_FILE ($(du -h "$PG_BACKUP_FILE" | cut -f1))"

  # ── 3. Neo4j 백업(볼륨 mountpoint 직접 tar — 이미지 pull 없음) ────────
  banner "3. Neo4j 백업"
  NEO4J_VOLUME="$(docker volume ls --format '{{.Name}}' | grep -i neo4jdata | head -1)"
  [[ -n "$NEO4J_VOLUME" ]] || die "neo4jdata 볼륨을 찾을 수 없습니다 (docker volume ls 확인)"
  NEO4J_MOUNTPOINT="$(docker volume inspect "$NEO4J_VOLUME" --format '{{ .Mountpoint }}')"
  log "volume: $NEO4J_VOLUME  mountpoint: $NEO4J_MOUNTPOINT"
  mkdir -p data/backups
  NEO4J_BACKUP_FILE="data/backups/neo4jdata_$(date +%Y%m%d_%H%M%S).tar.gz"
  if [[ -r "$NEO4J_MOUNTPOINT" ]]; then
    tar czf "$NEO4J_BACKUP_FILE" -C "$(dirname "$NEO4J_MOUNTPOINT")" "$(basename "$NEO4J_MOUNTPOINT")"
  else
    warn "mountpoint를 sudo 없이 못 읽습니다 — sudo로 재시도합니다"
    sudo tar czf "$NEO4J_BACKUP_FILE" -C "$(dirname "$NEO4J_MOUNTPOINT")" "$(basename "$NEO4J_MOUNTPOINT")"
    sudo chown "$(id -u):$(id -g)" "$NEO4J_BACKUP_FILE"
  fi
  [[ -s "$NEO4J_BACKUP_FILE" ]] || die "Neo4j 백업 파일이 비어있습니다 — 중단"
  log "✅ Neo4j 백업 확인: $NEO4J_BACKUP_FILE ($(du -h "$NEO4J_BACKUP_FILE" | cut -f1))"
fi

# ── 4. 코드 배포 ────────────────────────────────────────────────────────
banner "4. 코드 배포 (scripts/git_pull_deploy.sh)"
DEPLOY_ARGS=()
$YES && DEPLOY_ARGS+=(-y)
if ! ./scripts/git_pull_deploy.sh "${DEPLOY_ARGS[@]}"; then
  err "코드 배포 실패(자동 롤백이 이미 시도됐을 수 있음 — 위 출력 확인)"
  $STASHED_GITIGNORE && { warn ".gitignore stash는 그대로 남겨둡니다 — 문제 해결 후 'git stash pop'으로 직접 복원하세요"; }
  exit 1
fi

# ── 5. .gitignore stash 복원 ────────────────────────────────────────────
if $STASHED_GITIGNORE; then
  banner "5. .gitignore 복원"
  git stash pop
  log "✅ .gitignore 복원 완료"
  git status --short
fi

# ── 6. 마이그레이션 버전 확인 ────────────────────────────────────────────
# 실제 운영에서 재현된 사고: git_pull_deploy.sh가 재시작은 했지만 entrypoint의
# `alembic upgrade head`가 어떤 이유로든 반영되지 않아, graph_runs에 새 컬럼
# (scope 등)이 없는 채로 sync_cli가 돌다가 run 시작 INSERT에서 죽었다(문서/
# 관계 쓰기 전이라 데이터 손상은 없었음). 이제 head와 다르면 여기서 바로
# 중단하고 재백필을 시작하지 않는다.
banner "6. 마이그레이션 확인"
ALEMBIC_CURRENT="$(docker compose exec -T api alembic current 2>&1)"
echo "$ALEMBIC_CURRENT"
ALEMBIC_HEADS="$(docker compose exec -T api alembic heads 2>&1)"
# alembic이 실제 리비전 줄보다 먼저 "INFO [alembic...]" 로그 줄을 찍는다 —
# 단순히 맨 앞줄에서 토큰을 뽑으면 그 "INFO"를 리비전으로 잘못 읽는다(실제
# 운영에서 재현된 버그: current=INFO로 잘못 판정해 정상 상태를 실패로 봄).
# 이 저장소의 리비전 ID는 전부 YYYYMMDD_NNNN 형식이므로 그 패턴만 찾는다.
CURRENT_REV="$(echo "$ALEMBIC_CURRENT" | grep -oE '^[0-9]{8}_[0-9]{4}' | head -1)"
HEAD_REV="$(echo "$ALEMBIC_HEADS" | grep -oE '^[0-9]{8}_[0-9]{4}' | head -1)"
if [[ -z "$CURRENT_REV" || "$CURRENT_REV" != "$HEAD_REV" ]]; then
  err "마이그레이션이 head까지 안 올라갔습니다 (current=${CURRENT_REV:-없음}, head=$HEAD_REV)"
  err "원인 확인: docker compose logs api --tail 100 | grep -i alembic"
  err "수동 적용: docker compose exec -T api alembic upgrade head"
  die "마이그레이션 확인 실패 — 재백필을 시작하지 않습니다"
fi
log "✅ 마이그레이션 head 확인: $CURRENT_REV"

# ── 7. cron 확인(건드리지 않음 — 경고만) ────────────────────────────────
banner "7. cron 확인"
CRON_HITS="$(crontab -l 2>/dev/null | grep -E 'graph_sync|sync_cli' || true)"
if [[ -n "$CRON_HITS" ]]; then
  warn "graph_sync/sync_cli 관련 cron 작업이 있습니다 — 전체 재백필 동안 겹치면 비효율적입니다"
  echo "$CRON_HITS" | sed 's/^/    /'
  warn "(sync_cli 자체 advisory lock이 있어 레이스는 안 나지만, cron 쪽은 lock 충돌로 매번 실패 기록만 남깁니다)"
  warn "지금 crontab -e 로 직접 주석 처리하는 걸 권장합니다. 이 스크립트는 crontab을 자동으로 바꾸지 않습니다."
  confirm "cron을 확인/정리했고 계속 진행할까요?" || die "취소 — cron 정리 후 재실행하세요"
else
  log "cron에 graph_sync/sync_cli 관련 작업 없음"
fi

# ── 8. lexicon 재시딩 ────────────────────────────────────────────────────
banner "8. lexicon 재시딩 (POST /v1/lexicon/seed, replace=false)"
SEED_RESPONSE="$(curl -sf -X POST http://127.0.0.1:8573/v1/lexicon/seed)"
echo "$SEED_RESPONSE"
echo "$SEED_RESPONSE" | grep -q '"upserted"' || die "lexicon 재시딩 응답이 예상과 다릅니다 — 확인 필요"
log "✅ lexicon 재시딩 완료"

# ── 9. 전체 그래프 재백필(screen 백그라운드) ────────────────────────────
banner "9. 전체 그래프 재백필 시작"
command -v screen >/dev/null 2>&1 || die "screen이 설치되어 있지 않습니다 (sudo apt-get install screen 등)"
if screen -list 2>/dev/null | grep -q "\.${SCREEN_SESSION}\b"; then
  die "이미 screen 세션 '${SCREEN_SESSION}'이 실행 중입니다 — screen -r ${SCREEN_SESSION} 로 확인하세요"
fi
confirm "운영 전체 문서(수십만 건 규모)에 대한 전체 재백필을 지금 시작할까요? (dev 기준 약 2시간, 운영은 더 걸릴 수 있음)" \
  || die "취소 — 준비되면 다시 실행하세요"

# set -o pipefail + ${PIPESTATUS[0]} 필요 — "cmd | tee log; echo \$?"는
# 파이프의 마지막 명령(tee)의 종료코드를 가리켜 항상 0을 찍는다(실제 사고
# 재현: sync_cli가 마이그레이션 누락으로 즉시 죽었는데도 BACKFILL_DONE_EXIT=0
# 이 찍혀 "성공"으로 오인하게 만들었다). PIPESTATUS[0]이 진짜 sync_cli 종료코드.
screen -dmS "$SCREEN_SESSION" bash -c \
  "set -o pipefail; docker compose exec -T api python -m app.graph.sync_cli 2>&1 | tee '$BACKFILL_LOG'; echo BACKFILL_DONE_EXIT=\${PIPESTATUS[0]} >> '$BACKFILL_LOG'"

log "✅ screen 세션 '${SCREEN_SESSION}'에서 재백필 시작됨 (로그: $BACKFILL_LOG)"

banner "완료 — 재백필은 백그라운드에서 계속 진행 중"
cat <<EOF

다음 명령으로 진행 상황을 확인하세요:

  # 실시간 로그 보기(SSH 재접속 후에도 가능)
  screen -r ${SCREEN_SESSION}
  # (screen 안에서 나오되 죽이지 않기: Ctrl-A d)

  # 끝났는지만 빠르게 확인
  tail -5 $BACKFILL_LOG

  # 완료 후 상태 확인 (run_id, status, documents, evidence_reasons 등)
  curl -s http://127.0.0.1:8573/v1/graph/status | python3 -m json.tool

완료 판정 기준(docs/features/graph-correctness-prod-deploy-plan.md 참고):
  - latest_full_completed_run.status == "completed"(또는 hub 실패만 있는
    "completed_warn" — documents_failed/documents_state_write_failed는 0이어야 함)
  - latest_full_completed_run.generation == neo4j_generation (generation_matches: true)
  - cron을 멈췄다면 지금 다시 켜세요.
  - 실제 Claude Code 세션에서 kb_search → kb_graph_explore → kb_get_document
    체인을 한 번 실행해 trace를 남기세요(스크립트가 아니라 실제 MCP 호출).

EOF
