#!/usr/bin/env bash
# git_pull_deploy.sh — 운용 서버: 사내 GitHub(code.sdsdev.co.kr)에서 git pull 로
# "코드만" 안전하게 배포하고 컨테이너를 재시작한다.
#
# 전제 (docker-compose.yml 참고):
#   api/worker/web/mcp 는 apps/*/app, apps/web/public, mcp-server/server.py,
#   apps/api/alembic 을 호스트 bind-mount 하므로, 순수 코드 변경은
#   `docker compose restart` 만으로 반영된다 (이미지 재빌드 불필요).
#   alembic 마이그레이션은 api 컨테이너 entrypoint(scripts/api-entrypoint.sh)가
#   기동할 때마다 자동으로 `alembic upgrade head` 를 실행하므로 restart 로 충분하다.
#
# 이 스크립트가 다루지 않는 것:
#   Dockerfile / requirements*.txt / pyproject.toml / package*.json /
#   docker-compose.yml 변경(=이미지 재빌드가 필요한 변경)은 여전히
#   scripts/out.sh (개발) + scripts/in.sh (운용) 번들 배포를 사용한다.
#   이 스크립트는 그런 변경이 pull 대상에 섞여 있으면 경고만 하고,
#   기본적으로는 진행을 막는다 (--force-image-diff 로만 무시 가능).
#
# 안전장치:
#   - 작업 트리가 dirty 하면 중단 (로컬 수정 보존 — 임의로 버리지 않음)
#   - origin 이 사내 GitHub(code.sdsdev.co.kr)를 가리키지 않으면 중단
#   - git pull 은 --ff-only 만 사용 (예기치 않은 merge/rebase 없음)
#   - 재시작 후 헬스체크 실패 시 배포 직전 커밋으로 자동 롤백 후 재시작
#   - 동시 실행 방지 (flock)
#   - 최초 git 연결(--attach-git)은 현재 파일과 원격 브랜치의 diff 를 먼저
#     보여주고, "ATTACH" 를 정확히 입력해야만 진행 (재시작은 하지 않음)
set -euo pipefail

# ── defaults (in.sh 관습과 동일: $HOME/citec-kb, main) ─────────────────────
PROJECT_DIR="${HOME}/citec-kb"
REMOTE="origin"
BRANCH="main"
EXPECTED_REMOTE_HOST="code.sdsdev.co.kr"
SERVICES=(api worker web mcp)
WAIT_TIMEOUT=180
YES=false
DRY_RUN=false
NO_RESTART=false
FORCE_IMAGE_DIFF=false
NO_ROLLBACK=false
ATTACH_GIT=false
ATTACH_REMOTE_URL=""

# 이미지 재빌드가 필요한 변경으로 간주하는 파일 패턴 (grep -E, git diff 경로 기준)
IMAGE_SENSITIVE_PATTERN='(^|/)Dockerfile$|requirements.*\.txt$|pyproject\.toml$|package(-lock)?\.json$|^docker-compose\.ya?ml$'

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'
log()  { echo -e "${GREEN}[$(date '+%H:%M:%S')]${RESET} $*"; }
warn() { echo -e "${YELLOW}[$(date '+%H:%M:%S')] ⚠${RESET}  $*"; }
err()  { echo -e "${RED}[$(date '+%H:%M:%S')] ✗${RESET}  $*" >&2; }
info() { echo -e "${CYAN}[$(date '+%H:%M:%S')]${RESET} $*"; }
banner() {
  echo -e "\n${BOLD}════════════════════════════════════════════════${RESET}"
  echo -e "${BOLD}  $*${RESET}"
  echo -e "${BOLD}════════════════════════════════════════════════${RESET}"
}
die() { err "$*"; exit 1; }

usage() {
  cat <<'EOF'
git_pull_deploy.sh — 운용 서버: git pull 로 코드만 배포 + 컨테이너 재시작

USAGE
  scripts/git_pull_deploy.sh [옵션]

전제: Dockerfile/requirements/pyproject/package.json/docker-compose.yml
      변경이 없는 "코드만" 배포. 이미지 재빌드가 필요하면 out.sh/in.sh 사용.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
사전 준비 (최초 1회 — 이 스크립트를 처음 쓰기 전에 운용 서버에서)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1) 사내 GitHub(code.sdsdev.co.kr) 계정/접근 권한 신청
   - citec-kb 저장소(예: jooksan/citec-kb)에 대한 read 권한이 있는 계정 필요.
   - 운용 서버 전용이면 개인 계정보다 "배포용 서비스 계정" 또는 저장소
     Deploy Key(읽기 전용) 발급을 권장 — 담당자/IT 에 문의.

2) 인증 방식 하나 선택 — SSH(권장) 또는 HTTPS + PAT

   [SSH 방식]
     # 운용 서버에서 배포 계정으로 키 생성 (이미 있으면 생략)
     ssh-keygen -t ed25519 -C "citec-kb-ops@$(hostname)" -f ~/.ssh/id_ed25519_citec_kb

     # 공개키를 사내 GitHub 계정(또는 저장소 Deploy Key)에 등록
     cat ~/.ssh/id_ed25519_citec_kb.pub
     # → code.sdsdev.co.kr 웹 UI: Settings > SSH Keys 에 붙여넣기
     #   (Deploy Key 라면 저장소 Settings > Deploy keys, read-only 로)

     # code.sdsdev.co.kr 호스트용으로 이 키를 쓰도록 ~/.ssh/config 에 등록
     cat >> ~/.ssh/config <<CFG
     Host code.sdsdev.co.kr
       User git
       IdentityFile ~/.ssh/id_ed25519_citec_kb
       IdentitiesOnly yes
     CFG

     # 연결 테스트 (known_hosts 등록 겸)
     ssh -T git@code.sdsdev.co.kr

   [HTTPS + Personal Access Token 방식]
     # code.sdsdev.co.kr 웹 UI 에서 PAT 발급(repo read 권한만)
     # clone/remote 시 https://<user>:<PAT>@code.sdsdev.co.kr/... 형태로 쓰거나
     # git credential helper(store/cache) 로 한 번만 입력해 저장

3) 저장소 준비

   [경우 A] 아직 ~/citec-kb 가 없다면(최초 구축) — 그냥 클론:
       git clone git@code.sdsdev.co.kr:jooksan/citec-kb.git ~/citec-kb
     (URL 은 실제 경로로 교체. HTTPS 라면 https://code.sdsdev.co.kr/jooksan/citec-kb.git)

   [경우 B] 이미 out.sh/in.sh 번들로 만들어진 ~/citec-kb 가 있고(git 이력 없이
   코드만 있음, DB·컨테이너 운영 중) — 이 스크립트의 --attach-git 을 사용:

       scripts/git_pull_deploy.sh --attach-git \
         --remote-url git@code.sdsdev.co.kr:jooksan/citec-kb.git

     동작: git init → remote add → fetch → **현재 디렉토리 파일과
     origin/main 의 diff(변경/추가/삭제 파일 목록)를 화면에 표시** →
     정확히 "ATTACH" 를 입력해야만 checkout 진행. 그 외 입력이면 취소하고
     .git 을 다시 제거해 원상복구. -y 로도 이 확인은 건너뛸 수 없음
     (일회성·고위험 작업이라 항상 눈으로 diff 를 보고 진행).
     **컨테이너는 재시작하지 않음** — 반영하려면 완료 후 이 스크립트를
     --attach-git 없이 다시 실행 (또는 수동 재시작).

     diff 범위가 크면(운영 배포 시점과 사내 GitHub main 사이에 누적된 차이가
     클수록) 한 번에 반영되는 변경도 커집니다 — 진행 전에 가능하면
     `scripts/backup_postgres.sh` 로 DB 를 먼저 백업하고, 트래픽이 적은
     시간대에 하는 것을 권장합니다.

4) 원격/브랜치 확인 (이후 이 스크립트가 매번 검증하는 항목이기도 함)
     cd ~/citec-kb
     git remote -v                 # origin 이 code.sdsdev.co.kr 를 가리키는지
     git rev-parse --abbrev-ref HEAD   # main 브랜치인지
     git fetch origin main && git status   # 연결/인증이 실제로 되는지

5) 준비 확인 후 dry-run 으로 먼저 점검
     scripts/git_pull_deploy.sh --dry-run

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

옵션
  --project DIR        프로젝트 경로 (기본: $HOME/citec-kb)
  --remote NAME         git remote 이름 (기본: origin)
  --branch NAME          배포 브랜치 (기본: main)
  --services "a b c"    재시작할 서비스 (기본: "api worker web mcp")
  --wait-timeout SEC    헬스체크 대기 초 (기본: 180)
  --no-restart          pull 만 하고 재시작은 생략
  --no-rollback         헬스체크 실패해도 자동 롤백하지 않음 (수동 조치)
  --force-image-diff    Dockerfile 등 이미지 관련 파일 변경분이 섞여 있어도 진행
  --attach-git           최초 1회: 기존 디렉토리에 git 연결 (diff 확인 후 진행,
                        재시작 없음) — --remote-url 필수, 위 "사전 준비" 3) 참고
  --remote-url URL      --attach-git 과 함께 사용할 원격 저장소 URL
  --yes, -y             확인 프롬프트 생략 (--attach-git 의 ATTACH 확인은 예외)
  --dry-run, -n          fetch 후 계획만 표시, pull/restart 없음
  -h, --help            도움말

예시
  scripts/git_pull_deploy.sh --attach-git --remote-url git@code.sdsdev.co.kr:jooksan/citec-kb.git
  scripts/git_pull_deploy.sh --dry-run
  scripts/git_pull_deploy.sh -y
  scripts/git_pull_deploy.sh --services "api worker" -y
EOF
}

# attach_git_flow: 기존(비-git) 디렉토리에 최초로 git 을 연결한다. 현재 디렉토리
# 파일과 원격 브랜치의 diff 를 먼저 보여주고, 정확히 "ATTACH" 입력을 받아야만
# checkout 을 진행한다 (-y 로도 건너뛸 수 없음 — 일회성·고위험 작업).
# 성공/취소 어느 쪽이든 이 함수 안에서 exit 한다 (호출 지점으로 돌아오지 않음).
attach_git_flow() {
  [[ -n "$ATTACH_REMOTE_URL" ]] || die "--attach-git 에는 --remote-url <URL> 이 필요합니다 (--help 참고)"

  if [[ -d ".git" ]]; then
    die "$PROJECT_DIR 에 이미 .git 이 있습니다 — --attach-git 은 최초 1회만 사용하세요 (평소 배포는 --attach-git 없이 실행)"
  fi

  banner "최초 git 연결  |  $PROJECT_DIR"
  warn "이 디렉토리는 아직 git 저장소가 아닙니다."
  warn "사내 GitHub 에 연결하고, 현재 파일과 원격 브랜치의 차이를 보여준 뒤 확인을 받습니다."
  echo ""

  log "git init"
  git init --quiet

  log "git remote add $REMOTE $ATTACH_REMOTE_URL"
  if ! git remote add "$REMOTE" "$ATTACH_REMOTE_URL"; then
    rm -rf .git
    die "remote 추가 실패"
  fi

  local resolved_url
  resolved_url="$(git remote get-url "$REMOTE")"
  if [[ "$resolved_url" != *"$EXPECTED_REMOTE_HOST"* ]]; then
    rm -rf .git
    die "--remote-url 이 사내 GitHub($EXPECTED_REMOTE_HOST)를 가리키지 않습니다: $ATTACH_REMOTE_URL"
  fi

  log "git fetch $REMOTE $BRANCH"
  if ! git fetch "$REMOTE" "$BRANCH" --quiet; then
    rm -rf .git
    die "fetch 실패 — remote-url / 인증(SSH 키·PAT)을 확인하세요 (--help 의 '사전 준비' 1)-2) 참고)"
  fi
  git rev-parse --verify --quiet "${REMOTE}/${BRANCH}" >/dev/null \
    || { rm -rf .git; die "${REMOTE}/${BRANCH} 를 찾을 수 없습니다"; }

  echo ""
  info "현재 디렉토리 파일과 ${REMOTE}/${BRANCH} 의 차이:"
  echo ""
  local diffstat
  diffstat="$(git diff --stat "${REMOTE}/${BRANCH}" 2>&1 || true)"
  if [[ -z "$diffstat" ]]; then
    log "차이 없음 — 현재 파일이 ${REMOTE}/${BRANCH} 와 이미 동일합니다."
  else
    echo "$diffstat" | sed 's/^/    /'
  fi
  echo ""

  local changed_files changed_count
  changed_files="$(git diff --name-status "${REMOTE}/${BRANCH}")"
  changed_count=$(echo "$changed_files" | grep -c . || true)
  info "변경될 파일 수: ${changed_count}"

  if [[ "$changed_count" -gt 0 ]]; then
    warn "위 파일들은 확인 후 즉시 ${REMOTE}/${BRANCH} 상태로 대체됩니다."
    warn "(.env / data/ / models/ / logs/ 는 .gitignore 대상이라 영향 없음)"
    warn "컨테이너 재시작은 하지 않습니다 — 반영은 이후 별도 실행에서."
  fi

  echo ""
  echo -e "${RED}${BOLD}계속하려면 정확히 ATTACH 를 입력하세요 (그 외 입력 시 전부 취소):${RESET} "
  read -r CONFIRM_ATTACH
  if [[ "$CONFIRM_ATTACH" != "ATTACH" ]]; then
    warn "취소 — 방금 만든 .git 을 제거하고 원상복구합니다"
    rm -rf .git
    exit 0
  fi

  # -f 필요: git init 직후라 아무 것도 add 된 적이 없어 현재 파일들은 전부
  # "untracked" 상태다. 일반 checkout 은 대상 커밋과 경로가 겹치는 untracked
  # 파일을 보호하려고 거부하므로, 이미 위에서 diff 를 보여주고 ATTACH 확인까지
  # 받은 뒤에만 -f 로 실제 대체를 진행한다.
  log "git checkout -f -B $BRANCH --track ${REMOTE}/${BRANCH}"
  git checkout -f -B "$BRANCH" --track "${REMOTE}/${BRANCH}"

  DEPLOY_TRACK_DIR="${HOME}/bin"
  mkdir -p "$DEPLOY_TRACK_DIR"
  git rev-parse HEAD > "${DEPLOY_TRACK_DIR}/.citec_kb_git_deployed"

  echo ""
  banner "✅ git 연결 완료"
  echo "  commit: $(git rev-parse --short HEAD) ($BRANCH)"
  echo "  다음: 반영하려면 --attach-git 없이 이 스크립트를 다시 실행하세요"
  echo "        (재시작이 필요하면 ${SERVICES[*]} 가 docker compose restart 됩니다)"
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT_DIR="${2:?}"; shift 2 ;;
    --remote) REMOTE="${2:?}"; shift 2 ;;
    --branch) BRANCH="${2:?}"; shift 2 ;;
    --services) read -r -a SERVICES <<< "${2:?}"; shift 2 ;;
    --wait-timeout) WAIT_TIMEOUT="${2:?}"; shift 2 ;;
    --no-restart) NO_RESTART=true; shift ;;
    --no-rollback) NO_ROLLBACK=true; shift ;;
    --force-image-diff) FORCE_IMAGE_DIFF=true; shift ;;
    --attach-git) ATTACH_GIT=true; shift ;;
    --remote-url) ATTACH_REMOTE_URL="${2:?}"; shift 2 ;;
    --yes|-y) YES=true; shift ;;
    --dry-run|-n) DRY_RUN=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) err "알 수 없는 옵션: $1"; usage; exit 1 ;;
  esac
done

command -v git >/dev/null 2>&1 || die "git 명령을 찾을 수 없습니다."
command -v docker >/dev/null 2>&1 || die "docker 명령을 찾을 수 없습니다."
[[ -d "$PROJECT_DIR" ]] || die "디렉토리가 없습니다: $PROJECT_DIR (최초 구축은 --help 의 '사전 준비' 3) 참고)"

cd "$PROJECT_DIR"

# ── 동시 실행 방지 ───────────────────────────────────────────────────────
# 반드시 저장소 밖에 둔다 — 안에 두면 untracked 파일로 잡혀 아래
# "작업 트리 clean" 검사가 항상 실패한다.
LOCK_DIR="${HOME}/bin"
mkdir -p "$LOCK_DIR"
LOCK_FILE="${LOCK_DIR}/.citec_kb_git_pull_deploy.lock"
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
  die "다른 git_pull_deploy.sh 가 이미 실행 중입니다 (lock: $LOCK_FILE)"
fi

if $ATTACH_GIT; then
  attach_git_flow
fi

[[ -d ".git" ]] || die "git 저장소가 아닙니다: $PROJECT_DIR — 최초 연결은 --attach-git --remote-url <URL> (--help 참고)"

banner "citec-kb git pull 배포  |  $(date '+%Y-%m-%d %H:%M:%S')"
info "project: $PROJECT_DIR"
info "remote:  $REMOTE   branch: $BRANCH"

# ── origin 이 사내 GitHub 인지 확인 ─────────────────────────────────────
REMOTE_URL="$(git remote get-url "$REMOTE" 2>/dev/null || true)"
[[ -n "$REMOTE_URL" ]] || die "remote '$REMOTE' 를 찾을 수 없습니다 (git remote -v 확인)"
if [[ "$REMOTE_URL" != *"$EXPECTED_REMOTE_HOST"* ]]; then
  die "remote '$REMOTE' 가 사내 GitHub($EXPECTED_REMOTE_HOST)를 가리키지 않습니다: $REMOTE_URL"
fi
info "origin OK: $REMOTE_URL"

# ── 현재 브랜치 확인 ─────────────────────────────────────────────────────
CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$CURRENT_BRANCH" != "$BRANCH" ]]; then
  die "현재 브랜치($CURRENT_BRANCH)가 배포 브랜치($BRANCH)와 다릅니다 — 수동 확인 필요"
fi

# ── 작업 트리 clean 확인 (로컬 변경을 임의로 버리지 않음) ──────────────
if [[ -n "$(git status --porcelain)" ]]; then
  err "작업 트리에 커밋되지 않은 변경이 있습니다 — 중단합니다."
  git status --short
  die "먼저 커밋/stash 하거나 직접 확인하세요 (git status)"
fi
CURRENT_SHA="$(git rev-parse HEAD)"
info "현재 커밋: ${CURRENT_SHA:0:12}"

# ── fetch 및 diff 계획 ──────────────────────────────────────────────────
log "git fetch $REMOTE $BRANCH"
git fetch "$REMOTE" "$BRANCH" --quiet
NEW_SHA="$(git rev-parse "$REMOTE/$BRANCH")"

if [[ "$NEW_SHA" == "$CURRENT_SHA" ]]; then
  log "이미 최신입니다 (${CURRENT_SHA:0:12}) — 배포할 변경 없음"
  exit 0
fi

# fast-forward 가능한지 확인 (로컬이 원격 히스토리의 조상이어야 함)
if ! git merge-base --is-ancestor "$CURRENT_SHA" "$NEW_SHA"; then
  die "fast-forward 불가 — 로컬 브랜치가 $REMOTE/$BRANCH 와 갈라졌습니다. 수동 확인 필요 (git log, git diff)"
fi

echo ""
info "적용될 커밋 (${CURRENT_SHA:0:12} → ${NEW_SHA:0:12}):"
git log --oneline "${CURRENT_SHA}..${NEW_SHA}" | sed 's/^/    /'
echo ""

CHANGED_FILES="$(git diff --name-only "${CURRENT_SHA}" "${NEW_SHA}")"
IMAGE_SENSITIVE_HITS="$(echo "$CHANGED_FILES" | grep -E "$IMAGE_SENSITIVE_PATTERN" || true)"
if [[ -n "$IMAGE_SENSITIVE_HITS" ]]; then
  warn "이미지 재빌드가 필요할 수 있는 파일이 변경분에 포함되어 있습니다:"
  echo "$IMAGE_SENSITIVE_HITS" | sed 's/^/    /'
  warn "이 스크립트는 컨테이너 재시작만 하며 이미지는 다시 빌드하지 않습니다."
  warn "→ scripts/out.sh(개발) + scripts/in.sh(운용) 로 이미지도 함께 배포하세요."
  if ! $FORCE_IMAGE_DIFF; then
    die "중단합니다. 정말 코드만 반영해도 된다면 --force-image-diff 로 재실행하세요."
  fi
  warn "--force-image-diff 지정됨 — 이미지 변경 무시하고 코드만 진행"
fi

if $DRY_RUN; then
  warn "DRY-RUN — pull/restart 하지 않고 종료"
  exit 0
fi

if ! $YES; then
  echo -e "${RED}${BOLD}${#SERVICES[@]}개 서비스(${SERVICES[*]})를 재시작합니다. 계속할까요? [y/N]${RESET} "
  read -r CONFIRM
  [[ "${CONFIRM}" =~ ^[Yy]$ ]] || { warn "취소"; exit 0; }
fi

# ── pull ─────────────────────────────────────────────────────────────────
log "git pull --ff-only $REMOTE $BRANCH"
git pull --ff-only "$REMOTE" "$BRANCH"
APPLIED_SHA="$(git rev-parse HEAD)"
[[ "$APPLIED_SHA" == "$NEW_SHA" ]] || die "pull 후 커밋이 예상과 다릅니다 (예상 ${NEW_SHA:0:12}, 실제 ${APPLIED_SHA:0:12})"
log "✅ 코드 반영: ${CURRENT_SHA:0:12} → ${APPLIED_SHA:0:12}"

if $NO_RESTART; then
  warn "--no-restart — 컨테이너 재시작 생략 (수동으로 재시작하세요)"
  exit 0
fi

compose() { sudo docker compose "$@"; }

wait_http() {
  local url="$1" name="$2" deadline=$((SECONDS + WAIT_TIMEOUT))
  while (( SECONDS < deadline )); do
    if curl -sf --max-time 3 "$url" >/dev/null 2>&1; then
      log "  ✓ $name  ready  ($url)"
      return 0
    fi
    sleep 2
  done
  log "  ✗ $name  timeout after ${WAIT_TIMEOUT}s  ($url)"
  return 1
}

wait_health() {
  log "헬스 대기 (timeout=${WAIT_TIMEOUT}s)…"
  local failed=0 s
  for s in "${SERVICES[@]}"; do
    case "$s" in
      api)
        wait_http "http://127.0.0.1:8573/v1/health" "api" || failed=1
        ;;
      web)
        wait_http "http://127.0.0.1:8572/" "web" || failed=1
        ;;
      mcp)
        local deadline=$((SECONDS + WAIT_TIMEOUT)) ok=0
        while (( SECONDS < deadline )); do
          if (echo >/dev/tcp/127.0.0.1/8577) >/dev/null 2>&1; then
            log "  ✓ mcp   port 8577 open"; ok=1; break
          fi
          sleep 2
        done
        [[ "$ok" -eq 1 ]] || { log "  ✗ mcp   port 8577 timeout"; failed=1; }
        ;;
      worker)
        # worker has no HTTP endpoint of its own — confirm the container is
        # actually up (not restarting/crash-looping) instead.
        if compose ps worker 2>/dev/null | grep -qE "Up|running"; then
          log "  ✓ worker  container up"
        else
          log "  ✗ worker  not running"
          failed=1
        fi
        ;;
    esac
  done
  return "$failed"
}

restart_services() {
  log "docker compose restart ${SERVICES[*]}"
  compose restart "${SERVICES[@]}"
}

do_rollback() {
  err "헬스체크 실패 — 배포 직전 커밋(${CURRENT_SHA:0:12})으로 롤백합니다"
  git reset --hard "$CURRENT_SHA"
  restart_services
  if wait_health; then
    warn "롤백 완료 — 서비스는 ${CURRENT_SHA:0:12} 상태로 복구되었습니다. 실패한 배포(${APPLIED_SHA:0:12})를 조사하세요."
    return 0
  else
    err "롤백 후에도 헬스체크 실패 — 수동 개입이 필요합니다!"
    err "  docker compose logs --tail 100 ${SERVICES[*]}"
    return 1
  fi
}

restart_services

if wait_health; then
  DEPLOY_TRACK_DIR="${HOME}/bin"
  mkdir -p "$DEPLOY_TRACK_DIR"
  echo "$APPLIED_SHA" > "${DEPLOY_TRACK_DIR}/.citec_kb_git_deployed"
  echo ""
  banner "✅ 배포 완료"
  echo "  commit: ${APPLIED_SHA:0:12} ($BRANCH)"
  echo "  services: ${SERVICES[*]}"
  echo "  web  http://localhost:8572"
  echo "  api  http://localhost:8573/v1/health"
  echo "  mcp  http://localhost:8577"
  exit 0
else
  if $NO_ROLLBACK; then
    err "헬스체크 실패 — --no-rollback 지정됨, 자동 롤백하지 않습니다"
    err "  현재 코드: ${APPLIED_SHA:0:12}  (직전: ${CURRENT_SHA:0:12})"
    err "  로그 확인: docker compose logs --tail 100 ${SERVICES[*]}"
    exit 1
  fi
  do_rollback
  exit 1
fi
