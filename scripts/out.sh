#!/usr/bin/env bash
# out.sh — 개발 시스템: citec-kb 배포 번들 생성
#
# 번들 종류 (독립 버전 · 변경분만 재생성):
#   code        소스/설정 (수~수십 MB) — 일상 배포
#   docker      api+worker+베이스 이미지 — Dockerfile/requirements 변경 시
#   docker-mcp  mcp 이미지만 — mcp-server 변경 시
#   data        raw 코퍼스 + seeds (+ 선택: PG dump)
#   model       임베딩 모델 (HF cache)
#
# 출력 (기본 ~/tmp):
#   citec-kb-code-v<N>.tar.gz
#   citec-kb-docker-v<N>.tar.gz
#   citec-kb-docker-mcp-v<N>.tar.gz
#   citec-kb-data-d<N>.tar.gz
#   citec-kb-model.tar.gz
set -euo pipefail

usage() {
  cat <<'EOF'
out.sh — citec-kb 개발 시스템: 배포 번들 생성

USAGE
  scripts/out.sh [옵션]
  옵션 없음 = 다섯 종류 모두 (기존 번들 파일이 있으면 재사용)

포함/제외 (혼용):
  --code / --no-code
  --docker / --no-docker          api+worker+nginx+redis+pgvector
  --docker-mcp / --no-docker-mcp  citec-kb-mcp 만
  --data / --no-data              data/raw + data/seeds (+ --pg-dump)
  --model / --no-model            임베딩 모델 (MODELS_HOST_DIR)

재생성:
  --regen, -r                     기존 파일 무시하고 전부 새로 생성
  --force-code                    코드 번들만 강제
  --force-docker / --force-docker-mcp / --force-data / --force-model

버전 지정 (생략 시 자동 +1):
  --code-ver VER                  예: v3 또는 3  (code·docker·docker-mcp 공유)
  --data-ver VER                  예: d2 또는 2

경로:
  --source DIR                    레포 루트 (기본: 이 스크립트의 상위)
  --out DIR                       번들 출력 디렉터리 (기본: ~/tmp)

데이터 옵션:
  --pg-dump                       data 번들에 postgres 덤프 포함 (pg_dump 필요)
  --no-raw                        raw 코퍼스 제외 (seeds 만)

기타:
  -h, --help

일상 패턴:
  # 코드만 (웹·API 소스 수정 — 이미지 재빌드 불필요, 호스트 마운트)
  scripts/out.sh --code

  # MCP 서버만
  scripts/out.sh --docker-mcp

  # requirements / Dockerfile 변경
  scripts/out.sh --code --docker --docker-mcp

  # 지식 코퍼스 갱신
  scripts/out.sh --data

  # 폐쇄망 최초 구축
  scripts/out.sh --regen

전송 후 운용 서버:
  scp ~/tmp/citec-kb-*.tar.gz user@prod:~/
  # 운용: scripts/in.sh --code -y
EOF
}

# ── 경로 · 버전 추적 ─────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_SOURCE="$(cd "$SCRIPT_DIR/.." && pwd)"
SOURCE_PATH="$DEFAULT_SOURCE"
OUT_DIR="${HOME}/tmp"
TPATH="${HOME}/tmp/citec-kb-pack-tmp"
TS="$(date '+%Y-%m-%d_%H%M%S')"
REPO="citec-kb"

TRACK_DIR="${HOME}/bin"
mkdir -p "$TRACK_DIR" "$OUT_DIR" 2>/dev/null || true
CODE_VER_FILE="$TRACK_DIR/.citec_kb_code_version"
DATA_VER_FILE="$TRACK_DIR/.citec_kb_data_version"
MODEL_NAME_FILE="$TRACK_DIR/.citec_kb_model_name"
CODE_FINGERPRINT_FILE="$TRACK_DIR/.citec_kb_code_fingerprint"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'; DIM='\033[2m'
log()  { echo -e "${GREEN}[$(date '+%H:%M:%S')]${RESET} $*"; }
warn() { echo -e "${YELLOW}[$(date '+%H:%M:%S')] ⚠${RESET}  $*"; }
err()  { echo -e "${RED}[$(date '+%H:%M:%S')] ✗${RESET}  $*" >&2; }
info() { echo -e "${CYAN}[$(date '+%H:%M:%S')]${RESET} $*"; }
banner() {
  echo -e "\n${BOLD}════════════════════════════════════════════════${RESET}"
  echo -e "${BOLD}  $*${RESET}"
  echo -e "${BOLD}════════════════════════════════════════════════${RESET}"
}

SOURCE_STATE=""; DOCKER_STATE=""; DOCKER_MCP_STATE=""; DATA_STATE=""; MODEL_STATE=""
CODE_VER_ARG=""; DATA_VER_ARG=""
REGEN=false
FORCE_CODE=false; FORCE_DOCKER=false; FORCE_DOCKER_MCP=false
FORCE_DATA=false; FORCE_MODEL=false
PG_DUMP=false; INCLUDE_RAW=true
NO_MODEL_EXPLICIT=false

while [[ $# -gt 0 ]]; do
  case $1 in
    --code) SOURCE_STATE=yes; shift ;;
    --docker) DOCKER_STATE=yes; shift ;;
    --docker-mcp) DOCKER_MCP_STATE=yes; shift ;;
    --data) DATA_STATE=yes; shift ;;
    --model) MODEL_STATE=yes; shift ;;
    --no-code) SOURCE_STATE=no; shift ;;
    --no-docker) DOCKER_STATE=no; shift ;;
    --no-docker-mcp) DOCKER_MCP_STATE=no; shift ;;
    --no-data) DATA_STATE=no; shift ;;
    --no-model) MODEL_STATE=no; NO_MODEL_EXPLICIT=true; shift ;;
    --regen|-r) REGEN=true; shift ;;
    --force-code) FORCE_CODE=true; shift ;;
    --force-docker) FORCE_DOCKER=true; shift ;;
    --force-docker-mcp) FORCE_DOCKER_MCP=true; shift ;;
    --force-data) FORCE_DATA=true; shift ;;
    --force-model) FORCE_MODEL=true; shift ;;
    --code-ver) CODE_VER_ARG="${2:-}"; shift 2 ;;
    --data-ver) DATA_VER_ARG="${2:-}"; shift 2 ;;
    --source) SOURCE_PATH="${2:-}"; shift 2 ;;
    --out) OUT_DIR="${2:-}"; shift 2 ;;
    --pg-dump) PG_DUMP=true; shift ;;
    --no-raw) INCLUDE_RAW=false; shift ;;
    -h|--help) usage; exit 0 ;;
    *) err "알 수 없는 옵션: $1"; usage; exit 1 ;;
  esac
done

SOURCE_PATH="$(cd "$SOURCE_PATH" && pwd)"
mkdir -p "$OUT_DIR"

HAS_YES=false
[[ "$SOURCE_STATE" == "yes" || "$DOCKER_STATE" == "yes" || "$DOCKER_MCP_STATE" == "yes" || \
   "$DATA_STATE" == "yes" || "$MODEL_STATE" == "yes" ]] && HAS_YES=true
if ! $HAS_YES; then
  [[ "$SOURCE_STATE" != "no" ]] && SOURCE_STATE=yes
  [[ "$DOCKER_STATE" != "no" ]] && DOCKER_STATE=yes
  [[ "$DOCKER_MCP_STATE" != "no" ]] && DOCKER_MCP_STATE=yes
  [[ "$DATA_STATE" != "no" ]] && DATA_STATE=yes
  [[ "$MODEL_STATE" != "no" ]] && MODEL_STATE=yes
fi
DO_SOURCE=false; [[ "$SOURCE_STATE" == "yes" ]] && DO_SOURCE=true
DO_DOCKER=false; [[ "$DOCKER_STATE" == "yes" ]] && DO_DOCKER=true
DO_DOCKER_MCP=false; [[ "$DOCKER_MCP_STATE" == "yes" ]] && DO_DOCKER_MCP=true
DO_DATA=false; [[ "$DATA_STATE" == "yes" ]] && DO_DATA=true
DO_MODEL=false; [[ "$MODEL_STATE" == "yes" ]] && DO_MODEL=true

if ! $DO_SOURCE && ! $DO_DOCKER && ! $DO_DOCKER_MCP && ! $DO_DATA && ! $DO_MODEL; then
  err "생성할 번들이 없습니다."; exit 1
fi
$NO_MODEL_EXPLICIT && warn "--no-model: 운용 서버에 모델이 없으면 벡터 검색 불가"

resolve_version() {
  local ver_file="$1" prefix="$2" ver_arg="${3:-}"
  local num version last
  if [[ -n "$ver_arg" ]]; then
    num="${ver_arg#${prefix}}"
    [[ "$num" =~ ^[0-9]+$ ]] || { err "버전 형식: ${prefix}N 또는 N"; exit 1; }
    version="${prefix}${num}"
  else
    last=$(cat "$ver_file" 2>/dev/null || echo "0")
    num=$((last + 1))
    version="${prefix}${num}"
  fi
  echo "$num" > "$ver_file"
  echo "$version"
}

# 코드 fingerprint (변경 없으면 재패키지 생략 가능)
code_fingerprint() {
  (
    cd "$SOURCE_PATH"
    # git 있으면 커밋+diff, 없으면 mtime 해시
    if command -v git >/dev/null && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
      echo "git:$(git rev-parse HEAD 2>/dev/null)-$(git status -s -- apps config mcp-server scripts docker-compose.yml 2>/dev/null | md5sum | awk '{print $1}')"
    else
      find apps config mcp-server scripts deploy packages docker-compose.yml rebuild.sh README.md \
        -type f \( -name '*.py' -o -name '*.html' -o -name '*.js' -o -name '*.css' -o -name '*.yml' -o -name '*.md' -o -name 'Dockerfile' -o -name 'requirements*.txt' -o -name '*.json' -o -name '*.sh' \) \
        2>/dev/null | sort | xargs md5sum 2>/dev/null | md5sum | awk '{print $1}'
    fi
  )
}

CURRENT_CODE_NUM=$(cat "$CODE_VER_FILE" 2>/dev/null || echo "0")
CURRENT_DATA_NUM=$(cat "$DATA_VER_FILE" 2>/dev/null || echo "0")
FP_NOW=$(code_fingerprint)
FP_LAST=$(cat "$CODE_FINGERPRINT_FILE" 2>/dev/null || echo "")

CODE_EXISTS=false; DOCKER_EXISTS=false; DOCKER_MCP_EXISTS=false
DATA_EXISTS=false; MODEL_EXISTS=false
if ! $REGEN; then
  if [[ "$CURRENT_CODE_NUM" -gt 0 ]]; then
    [[ -f "${OUT_DIR}/citec-kb-code-v${CURRENT_CODE_NUM}.tar.gz" ]] && CODE_EXISTS=true
    [[ -f "${OUT_DIR}/citec-kb-docker-v${CURRENT_CODE_NUM}.tar.gz" ]] && DOCKER_EXISTS=true
    [[ -f "${OUT_DIR}/citec-kb-docker-mcp-v${CURRENT_CODE_NUM}.tar.gz" ]] && DOCKER_MCP_EXISTS=true
  fi
  [[ "$CURRENT_DATA_NUM" -gt 0 && -f "${OUT_DIR}/citec-kb-data-d${CURRENT_DATA_NUM}.tar.gz" ]] && DATA_EXISTS=true
  if [[ -f "${OUT_DIR}/citec-kb-model.tar.gz" ]]; then
    local_model=$(grep -E "^EMBEDDING_MODEL=" "${SOURCE_PATH}/.env" 2>/dev/null | cut -d= -f2 | tr -d ' "' | head -1 || true)
    local_model="${local_model:-intfloat/multilingual-e5-base}"
    last_model=$(cat "$MODEL_NAME_FILE" 2>/dev/null || echo "")
    [[ "$last_model" == "$local_model" ]] && MODEL_EXISTS=true
  fi
fi

# fingerprint 동일 + 파일 존재 → 코드 재생성 불필요 (docker 는 별도 force)
CODE_UNCHANGED=false
[[ -n "$FP_LAST" && "$FP_NOW" == "$FP_LAST" && "$CODE_EXISTS" == "true" ]] && CODE_UNCHANGED=true

NEED_NEW_CODE=false
if $DO_SOURCE || $DO_DOCKER || $DO_DOCKER_MCP; then
  if $REGEN || $FORCE_CODE || [[ "$CURRENT_CODE_NUM" -eq 0 ]] || ! $CODE_UNCHANGED; then
    # docker-only force without code change: still allow reuse of code tarball
    if $FORCE_DOCKER || $FORCE_DOCKER_MCP; then
      NEED_NEW_CODE=true  # bump version shared
    elif ! $CODE_UNCHANGED || $FORCE_CODE || $REGEN || [[ "$CURRENT_CODE_NUM" -eq 0 ]]; then
      NEED_NEW_CODE=true
    fi
  fi
fi
# refine: if only docker-mcp forced and code unchanged, still need version for mcp file name
if $FORCE_DOCKER || $FORCE_DOCKER_MCP; then NEED_NEW_CODE=true; fi
if $FORCE_CODE || $REGEN; then NEED_NEW_CODE=true; fi
if $DO_SOURCE && ! $CODE_EXISTS; then NEED_NEW_CODE=true; fi
if $DO_DOCKER && ! $DOCKER_EXISTS; then NEED_NEW_CODE=true; fi
if $DO_DOCKER_MCP && ! $DOCKER_MCP_EXISTS; then NEED_NEW_CODE=true; fi
# if code unchanged and exists, don't bump unless docker missing or forced
if $CODE_UNCHANGED && ! $FORCE_CODE && ! $FORCE_DOCKER && ! $FORCE_DOCKER_MCP && ! $REGEN; then
  NEED_NEW_CODE=false
fi

NEED_NEW_DATA=false
if $DO_DATA; then
  if ! $DATA_EXISTS || $REGEN || $FORCE_DATA; then NEED_NEW_DATA=true; fi
fi
NEED_NEW_MODEL=false
if $DO_MODEL; then
  if ! $MODEL_EXISTS || $REGEN || $FORCE_MODEL; then NEED_NEW_MODEL=true; fi
fi

echo ""
echo -e "${BOLD}════════════════════════════════════════════════${RESET}"
echo -e "${BOLD}  citec-kb 배포 번들 생성  |  $(date '+%Y-%m-%d %H:%M:%S')${RESET}"
echo -e "  source: ${SOURCE_PATH}"
echo -e "  out:    ${OUT_DIR}"
$REGEN && echo -e "  ${YELLOW}강제 재생성 (--regen)${RESET}"
echo -e "  code fingerprint: ${DIM}${FP_NOW:0:16}…${RESET} (prev: ${FP_LAST:0:12}…)"
echo -e "${BOLD}════════════════════════════════════════════════${RESET}"

_show() {
  local label="$1" will="$2" name="$3"
  if [[ "$will" == "true" ]]; then
    echo -e "  $(printf '%-18s' "$label") ${GREEN}[생성]${RESET}  ${name}"
  else
    local sz=""
    [[ -f "${OUT_DIR}/${name}" ]] && sz=" $(du -sh "${OUT_DIR}/${name}" | cut -f1)"
    echo -e "  $(printf '%-18s' "$label") ${DIM}[재사용]${RESET} ${name}${DIM}${sz}${RESET}"
  fi
}

if $DO_SOURCE || $DO_DOCKER || $DO_DOCKER_MCP; then
  if $NEED_NEW_CODE; then _cv="v$((CURRENT_CODE_NUM + 1))"
    [[ -n "$CODE_VER_ARG" ]] && _cv="v${CODE_VER_ARG#v}"
  else _cv="v${CURRENT_CODE_NUM}"; fi
  if $DO_SOURCE; then
    _w=false; ($NEED_NEW_CODE || ! $CODE_EXISTS) && _w=true
    _show "소스코드" "$_w" "citec-kb-code-${_cv}.tar.gz"
  fi
  if $DO_DOCKER; then
    _w=false; ($NEED_NEW_CODE || ! $DOCKER_EXISTS || $FORCE_DOCKER) && _w=true
    _show "Docker(api/worker)" "$_w" "citec-kb-docker-${_cv}.tar.gz"
  fi
  if $DO_DOCKER_MCP; then
    _w=false; ($NEED_NEW_CODE || ! $DOCKER_MCP_EXISTS || $FORCE_DOCKER_MCP) && _w=true
    _show "Docker(mcp)" "$_w" "citec-kb-docker-mcp-${_cv}.tar.gz"
  fi
fi
if $DO_DATA; then
  if $NEED_NEW_DATA; then _dv="d$((CURRENT_DATA_NUM + 1))"
    [[ -n "$DATA_VER_ARG" ]] && _dv="d${DATA_VER_ARG#d}"
  else _dv="d${CURRENT_DATA_NUM}"; fi
  _show "데이터" "$NEED_NEW_DATA" "citec-kb-data-${_dv}.tar.gz"
fi
$DO_MODEL && _show "임베딩모델" "$NEED_NEW_MODEL" "citec-kb-model.tar.gz"
echo -e "${BOLD}════════════════════════════════════════════════${RESET}"
echo ""

# 버전 확정
CODE_VERSION=""
if $DO_SOURCE || $DO_DOCKER || $DO_DOCKER_MCP; then
  if $NEED_NEW_CODE; then
    CODE_VERSION=$(resolve_version "$CODE_VER_FILE" "v" "${CODE_VER_ARG:-}")
  else
    CODE_VERSION="v${CURRENT_CODE_NUM}"
  fi
fi
DATA_VERSION=""
if $DO_DATA; then
  if $NEED_NEW_DATA; then
    DATA_VERSION=$(resolve_version "$DATA_VER_FILE" "d" "${DATA_VER_ARG:-}")
  else
    DATA_VERSION="d${CURRENT_DATA_NUM}"
  fi
fi

RESULT_SOURCE=""; RESULT_DOCKER=""; RESULT_DOCKER_MCP=""; RESULT_DATA=""; RESULT_MODEL=""

# ── code bundle ──────────────────────────────────────────────────────────
build_source_bundle() {
  local version="$1"
  banner "소스코드 번들  ${version}"
  local output_tgz="${OUT_DIR}/citec-kb-code-${version}.tar.gz"
  local dest="$TPATH/$REPO"
  rm -rf "$TPATH"
  mkdir -p "$dest"

  log "[1/3] 화이트리스트 복사"
  local d
  for d in apps config mcp-server scripts deploy packages; do
    if [[ -d "${SOURCE_PATH}/${d}" ]]; then
      cp -a "${SOURCE_PATH}/${d}" "$dest/"
    else
      warn "없음 (건너뜀): ${d}"
    fi
  done
  mkdir -p "$dest/docs"
  local f
  for f in docker-compose.yml rebuild.sh README.md .env.example .gitignore \
           docs/EXTERNAL_API.md docs/MCP.md docs/IMPLEMENTATION_PLAN.md \
           docs/OIDC_IDP_SETUP.md docs/PHASE2_PILOT_CHECKLIST.md; do
    [[ -e "${SOURCE_PATH}/${f}" ]] && { mkdir -p "$dest/$(dirname "$f")"; cp -a "${SOURCE_PATH}/${f}" "$dest/$f"; }
  done

  # strip caches / secrets / heavy data / tests pyc
  find "$dest" -name '__pycache__' -type d -print0 | xargs -0r rm -rf
  find "$dest" \( -name '*.pyc' -o -name '*.pyo' -o -name '.DS_Store' \) -delete
  rm -rf "$dest/apps/api/tests" "$dest/apps/api/app/**/__pycache__" 2>/dev/null || true
  find "$dest" -type d -name '__pycache__' -print0 | xargs -0r rm -rf
  rm -f "$dest/.env" 2>/dev/null || true
  # empty skeleton for volumes
  mkdir -p "$dest/data/raw" "$dest/data/seeds" "$dest/data/backups" "$dest/logs"

  # production helper
  cat > "$dest/run_stack.sh" << 'PROD'
#!/usr/bin/env bash
# 운용: 이미지 재빌드 없이 스택 재시작 (코드는 호스트 마운트)
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -f .env ]]; then
  echo "ERROR: .env 없음 — cp .env.example .env 후 키 설정"
  exit 1
fi
docker compose down --remove-orphans 2>/dev/null || true
docker compose up -d
docker compose ps
PROD
  chmod +x "$dest/run_stack.sh"

  log "[2/3] 메타"
  {
    echo "repo: citec-kb"
    echo "bundle: code"
    echo "version: ${version}"
    echo "created: $(date -Iseconds)"
    echo "hostname: $(hostname)"
    echo "fingerprint: ${FP_NOW}"
  } > "$dest/BUNDLE_META.txt"

  log "[3/3] tar"
  (cd "$TPATH" && tar czf "$output_tgz" "$REPO")
  log "완료: $(basename "$output_tgz") ($(du -sh "$output_tgz" | cut -f1))"
  echo "$FP_NOW" > "$CODE_FINGERPRINT_FILE"
  rm -rf "$TPATH"
  RESULT_SOURCE="$output_tgz"
}

# ── docker images ────────────────────────────────────────────────────────
_IMAGES_BUILT=false
_ensure_built() {
  $_IMAGES_BUILT && return
  log "docker compose build (api worker mcp)"
  (cd "$SOURCE_PATH" && docker compose build api worker mcp)
  _IMAGES_BUILT=true
}

compose_image_name() {
  # project directory basename → citec-kb-api
  local svc="$1"
  local proj
  proj=$(basename "$SOURCE_PATH" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]//g')
  # docker compose default: {dir}_{service}
  # but we set image: for mcp; api/worker use project build tags
  case "$svc" in
    mcp) echo "citec-kb-mcp:latest" ;;
    api) echo "$(cd "$SOURCE_PATH" && docker compose images -q api 2>/dev/null | head -1)" ;;
    *) echo "" ;;
  esac
}

list_runtime_images() {
  # images needed for closed network
  local imgs=()
  imgs+=(pgvector/pgvector:pg16)
  imgs+=(redis:7-alpine)
  imgs+=(nginx:1.27-alpine)
  # built
  local api_img worker_img
  api_img=$(docker images --format '{{.Repository}}:{{.Tag}}' | grep -E 'citec-kb-api|citec_kb-api|citeckb-api' | head -1 || true)
  worker_img=$(docker images --format '{{.Repository}}:{{.Tag}}' | grep -E 'citec-kb-worker|citec_kb-worker' | head -1 || true)
  # compose project images often named after folder
  if [[ -z "$api_img" ]]; then
    api_img=$(cd "$SOURCE_PATH" && docker compose images api --format json 2>/dev/null | head -1 | sed -n 's/.*"Repository":"\([^"]*\)".*"Tag":"\([^"]*\)".*/\1:\2/p' || true)
  fi
  # fallback: any local build matching
  [[ -z "$api_img" ]] && api_img=$(docker images --format '{{.Repository}}:{{.Tag}}' | grep -i 'api' | grep -i 'citec' | head -1 || true)
  [[ -z "$worker_img" ]] && worker_img=$(docker images --format '{{.Repository}}:{{.Tag}}' | grep -i 'worker' | grep -i 'citec' | head -1 || true)

  # docker compose images
  while IFS= read -r line; do
    [[ -n "$line" ]] && imgs+=("$line")
  done < <(cd "$SOURCE_PATH" && docker compose images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | grep -v '^$' || true)

  # unique
  printf '%s\n' "${imgs[@]}" | awk 'NF && !seen[$0]++'
}

build_docker_bundle() {
  local version="$1"
  banner "Docker 번들 (api/worker/base)  ${version}"
  local output_tgz="${OUT_DIR}/citec-kb-docker-${version}.tar.gz"
  _ensure_built
  local imgs=()
  local i
  for i in pgvector/pgvector:pg16 redis:7-alpine nginx:1.27-alpine; do
    if ! docker image inspect "$i" &>/dev/null; then
      log "pull $i"
      docker pull "$i"
    fi
    imgs+=("$i")
  done
  # all compose-built except mcp (separate bundle)
  while IFS= read -r line; do
    [[ -z "$line" || "$line" == *mcp* ]] && continue
    imgs+=("$line")
  done < <(cd "$SOURCE_PATH" && docker compose images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null || true)

  # de-dupe
  local uniq=()
  while IFS= read -r line; do
    [[ -n "$line" && "$line" != ":<none>" ]] && uniq+=("$line")
  done < <(printf '%s\n' "${imgs[@]}" | awk 'NF && !seen[$0]++')

  log "저장 이미지: ${uniq[*]}"
  docker save "${uniq[@]}" | gzip > "$output_tgz"
  log "완료: $(basename "$output_tgz") ($(du -sh "$output_tgz" | cut -f1))"
  RESULT_DOCKER="$output_tgz"
}

build_docker_mcp_bundle() {
  local version="$1"
  banner "Docker 번들 (mcp)  ${version}"
  local output_tgz="${OUT_DIR}/citec-kb-docker-mcp-${version}.tar.gz"
  _ensure_built
  local mcp_img="citec-kb-mcp:latest"
  if ! docker image inspect "$mcp_img" &>/dev/null; then
    mcp_img=$(docker images --format '{{.Repository}}:{{.Tag}}' | grep -i 'mcp' | grep -i 'citec' | head -1 || true)
  fi
  [[ -n "$mcp_img" ]] || { err "mcp 이미지 없음 — docker compose build mcp"; exit 1; }
  docker save "$mcp_img" | gzip > "$output_tgz"
  log "완료: $(basename "$output_tgz") ($(du -sh "$output_tgz" | cut -f1))"
  RESULT_DOCKER_MCP="$output_tgz"
}

# ── data ─────────────────────────────────────────────────────────────────
build_data_bundle() {
  local version="$1"
  banner "데이터 번들  ${version}"
  local output_tgz="${OUT_DIR}/citec-kb-data-${version}.tar.gz"
  local staging="$TPATH/data-staging"
  rm -rf "$TPATH"
  mkdir -p "$staging/data"

  log "[1/3] seeds / config-ish data"
  [[ -d "${SOURCE_PATH}/data/seeds" ]] && cp -a "${SOURCE_PATH}/data/seeds" "$staging/data/"
  [[ -f "${SOURCE_PATH}/data/raw_manifest.json" ]] && cp -a "${SOURCE_PATH}/data/raw_manifest.json" "$staging/data/"

  if $INCLUDE_RAW; then
    log "[2/3] raw 코퍼스"
    if [[ -d "${SOURCE_PATH}/data/raw" ]]; then
      # exclude huge backups/xls optional
      rsync -a --exclude='*.xls' --exclude='.git' \
        "${SOURCE_PATH}/data/raw/" "$staging/data/raw/" 2>/dev/null \
        || cp -a "${SOURCE_PATH}/data/raw" "$staging/data/"
    else
      warn "data/raw 없음"
    fi
  else
    log "[2/3] raw 제외 (--no-raw)"
    mkdir -p "$staging/data/raw"
  fi

  if $PG_DUMP; then
    log "postgres 덤프"
    mkdir -p "$staging/data/backups"
    local dumpf="$staging/data/backups/citec_knowledge_${TS}.sql.gz"
    if command -v pg_dump >/dev/null 2>&1; then
      PGPASSWORD="${POSTGRES_PASSWORD:-citec}" pg_dump \
        -h "${POSTGRES_HOST:-127.0.0.1}" -p "${POSTGRES_PORT:-8574}" \
        -U "${POSTGRES_USER:-citec}" "${POSTGRES_DB:-citec_knowledge}" \
        | gzip > "$dumpf" || warn "pg_dump 실패"
    else
      warn "pg_dump 없음 — 컨테이너로 시도"
      (cd "$SOURCE_PATH" && docker compose exec -T postgres \
        pg_dump -U citec citec_knowledge 2>/dev/null | gzip > "$dumpf") || warn "pg_dump 실패"
    fi
  fi

  {
    echo "repo: citec-kb"
    echo "bundle: data"
    echo "version: ${version}"
    echo "created: $(date -Iseconds)"
    echo "include_raw: ${INCLUDE_RAW}"
    echo "pg_dump: ${PG_DUMP}"
    if [[ -d "$staging/data/raw" ]]; then
      echo "raw_files: $(find "$staging/data/raw" -type f 2>/dev/null | wc -l)"
      echo "raw_size: $(du -sh "$staging/data/raw" 2>/dev/null | cut -f1)"
    fi
  } > "$staging/manifest.txt"

  log "[3/3] tar"
  (cd "$staging" && tar czf "$output_tgz" data manifest.txt)
  log "완료: $(basename "$output_tgz") ($(du -sh "$output_tgz" | cut -f1))"
  rm -rf "$TPATH"
  RESULT_DATA="$output_tgz"
}

# ── model ────────────────────────────────────────────────────────────────
build_model_bundle() {
  banner "임베딩 모델 번들"
  local models_dir="${MODELS_HOST_DIR:-}"
  if [[ -z "$models_dir" ]]; then
    models_dir=$(grep -E 'MODELS_HOST_DIR' "${SOURCE_PATH}/.env" 2>/dev/null | cut -d= -f2 | tr -d ' "' || true)
  fi
  models_dir="${models_dir:-/home/citec/tmp/citec-wiki-qa/models}"
  if [[ ! -d "$models_dir" ]]; then
    err "모델 디렉터리 없음: $models_dir"
    err "MODELS_HOST_DIR 또는 out.sh 전에 모델 준비"
    exit 1
  fi
  local model_name
  model_name=$(grep -E "^EMBEDDING_MODEL=" "${SOURCE_PATH}/.env" 2>/dev/null | cut -d= -f2 | tr -d ' "' | head -1 || true)
  model_name="${model_name:-intfloat/multilingual-e5-base}"
  info "모델: ${model_name}  dir=${models_dir}"

  local output_tgz="${OUT_DIR}/citec-kb-model.tar.gz"
  local staging="$TPATH/models-staging"
  rm -rf "$TPATH"
  mkdir -p "$staging"
  # pack as models/ relative for extract to project root
  ln -sfn "$models_dir" "$staging/models" 2>/dev/null || cp -a "$models_dir" "$staging/models"
  echo "$model_name" > "$staging/models/model.name" 2>/dev/null \
    || { mkdir -p "$staging/models"; echo "$model_name" > "$staging/models/model.name"; cp -a "$models_dir/." "$staging/models/" 2>/dev/null || true; }

  (cd "$staging" && tar czf "$output_tgz" models/)
  echo "$model_name" > "${OUT_DIR}/citec-kb-model.tar.gz.name"
  echo "$model_name" > "$MODEL_NAME_FILE"
  log "완료: citec-kb-model.tar.gz ($(du -sh "$output_tgz" | cut -f1))"
  rm -rf "$TPATH"
  RESULT_MODEL="$output_tgz"
}

# ── main ─────────────────────────────────────────────────────────────────
if $DO_SOURCE; then
  if ! $NEED_NEW_CODE && $CODE_EXISTS; then
    info "소스코드 재사용: citec-kb-code-${CODE_VERSION}.tar.gz"
    RESULT_SOURCE="${OUT_DIR}/citec-kb-code-${CODE_VERSION}.tar.gz"
  else
    build_source_bundle "$CODE_VERSION"
  fi
fi

if $DO_DOCKER; then
  if ! $NEED_NEW_CODE && $DOCKER_EXISTS && ! $FORCE_DOCKER; then
    info "Docker 재사용: citec-kb-docker-${CODE_VERSION}.tar.gz"
    RESULT_DOCKER="${OUT_DIR}/citec-kb-docker-${CODE_VERSION}.tar.gz"
  else
    build_docker_bundle "$CODE_VERSION"
  fi
fi

if $DO_DOCKER_MCP; then
  if ! $NEED_NEW_CODE && $DOCKER_MCP_EXISTS && ! $FORCE_DOCKER_MCP; then
    info "Docker(mcp) 재사용: citec-kb-docker-mcp-${CODE_VERSION}.tar.gz"
    RESULT_DOCKER_MCP="${OUT_DIR}/citec-kb-docker-mcp-${CODE_VERSION}.tar.gz"
  else
    build_docker_mcp_bundle "$CODE_VERSION"
  fi
fi

if $DO_DATA; then
  if ! $NEED_NEW_DATA; then
    info "데이터 재사용: citec-kb-data-${DATA_VERSION}.tar.gz"
    RESULT_DATA="${OUT_DIR}/citec-kb-data-${DATA_VERSION}.tar.gz"
  else
    build_data_bundle "$DATA_VERSION"
  fi
fi

if $DO_MODEL; then
  if ! $NEED_NEW_MODEL; then
    info "모델 재사용: citec-kb-model.tar.gz"
    RESULT_MODEL="${OUT_DIR}/citec-kb-model.tar.gz"
  else
    build_model_bundle
  fi
fi

echo ""
echo -e "${BOLD}════════════════════════════════════════════════${RESET}"
echo -e "${BOLD}  생성 완료${RESET}"
[[ -n "$RESULT_SOURCE" ]] && echo "  code:       $(basename "$RESULT_SOURCE") ($(du -sh "$RESULT_SOURCE" | cut -f1))"
[[ -n "$RESULT_DOCKER" ]] && echo "  docker:     $(basename "$RESULT_DOCKER") ($(du -sh "$RESULT_DOCKER" | cut -f1))"
[[ -n "$RESULT_DOCKER_MCP" ]] && echo "  docker-mcp: $(basename "$RESULT_DOCKER_MCP") ($(du -sh "$RESULT_DOCKER_MCP" | cut -f1))"
[[ -n "$RESULT_DATA" ]] && echo "  data:       $(basename "$RESULT_DATA") ($(du -sh "$RESULT_DATA" | cut -f1))"
[[ -n "$RESULT_MODEL" ]] && echo "  model:      $(basename "$RESULT_MODEL") ($(du -sh "$RESULT_MODEL" | cut -f1))"
echo ""
echo "  전송: scp ${OUT_DIR}/citec-kb-*.tar.gz user@prod:~/"
echo "  운용: ~/citec-kb/scripts/in.sh  또는  번들과 함께 배포된 scripts/in.sh"
echo -e "${BOLD}════════════════════════════════════════════════${RESET}"
