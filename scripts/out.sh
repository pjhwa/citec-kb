#!/usr/bin/env bash
# out.sh — 개발 시스템: citec-kb 배포 번들 생성
#
# wiki-qa out.sh 패턴을 따르되, multi-service + Postgres 구조에 맞춤.
#
# 번들 (독립 · 변경분만 재생성):
#   code           소스/설정 (수~수십 MB) — 일상 배포. 호스트 마운트 → restart 만
#   docker         core 5 이미지: api worker nginx redis pgvector
#   docker-mcp     citec-kb-mcp:latest
#   docker-keycloak  (선택) Keycloak 26
#   data           data/raw + seeds (+ 선택 --pg-dump / --pg-only)
#   model          HF 임베딩 캐시 (이 레포 models/)
#
# 출력 (기본 ~/tmp):
#   citec-kb-code-v<N>.tar.gz
#   citec-kb-docker-v<N>.tar.gz
#   citec-kb-docker-mcp-v<N>.tar.gz
#   citec-kb-docker-keycloak-v<N>.tar.gz
#   citec-kb-data-d<N>.tar.gz
#   citec-kb-model.tar.gz
#
# 주의: 검색 인덱스는 Postgres. 파일 data 만으로는 검색 불가.
#       DB 복제: --pg-dump 또는 --pg-only → 운용 in.sh --restore-pg (스키마 초기화 후 복원).
set -euo pipefail

usage() {
  cat <<'EOF'
out.sh — citec-kb 개발 시스템: 배포 번들 생성

USAGE
  scripts/out.sh [옵션]
  옵션 없음 = code+docker+docker-mcp+data+model (기존 파일이면 재사용)
  keycloak 은 기본 제외 → --docker-keycloak

포함/제외:
  --code / --no-code
  --docker / --no-docker          api worker nginx redis pgvector
  --docker-mcp / --no-docker-mcp
  --docker-keycloak               Keycloak (compose profile)
  --data / --no-data
  --model / --no-model

강제:
  --regen, -r                     전부 새로 생성
  --force-code / --force-docker / --force-docker-mcp
  --force-data / --force-model

버전:
  --code-ver VER                  vN  (code·docker·docker-mcp·keycloak 공유)
  --data-ver VER                  dN

경로:
  --source DIR                    레포 루트 (기본: 이 스크립트 상위)
  --out DIR                       출력 (기본: ~/tmp)

데이터:
  --pg-dump                       data 번들에 PG 논리 덤프 포함 (검색 인덱스 복제)
  --pg-only                       data 번들 = PG 덤프 위주 (raw 제외, data 자동 포함)
  --no-raw                        raw 제외 (seeds 만)

예시:
  scripts/out.sh --code                          # 일상 (마운트 코드만)
  scripts/out.sh --code --pg-only                # 코드 + DB 스냅샷 (운영 인덱스 재동기화)
  scripts/out.sh --code --docker --docker-mcp    # requirements/Dockerfile 변경
  scripts/out.sh --data --pg-dump                # raw + DB
  scripts/out.sh --model
  scripts/out.sh --regen                         # 최초 전체
EOF
}

# ── paths / tracking ─────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_PATH="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO="citec-kb"
OUT_DIR="${HOME}/tmp"
TPATH="${HOME}/tmp/citec-kb-pack-staging"
TS="$(date '+%Y-%m-%d_%H%M%S')"

CODE_VER_FILE="${HOME}/bin/.citec_kb_code_version"
DATA_VER_FILE="${HOME}/bin/.citec_kb_data_version"
MODEL_NAME_FILE="${HOME}/bin/.citec_kb_model_name"
mkdir -p "${HOME}/bin" "$OUT_DIR"

# Fixed image tags (must match docker-compose.yml image: lines)
IMG_API="citec-kb-api:latest"
IMG_WORKER="citec-kb-worker:latest"
IMG_MCP="citec-kb-mcp:latest"
IMG_NGINX="nginx:1.27-alpine"
IMG_REDIS="redis:7-alpine"
IMG_PG="pgvector/pgvector:pg16"
IMG_KC="quay.io/keycloak/keycloak:26.0"
CORE_IMAGES=("$IMG_API" "$IMG_WORKER" "$IMG_NGINX" "$IMG_REDIS" "$IMG_PG")

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

# ── args ─────────────────────────────────────────────────────────────────
SOURCE_STATE=""; DOCKER_STATE=""; DOCKER_MCP_STATE=""; DOCKER_KC_STATE=""
DATA_STATE=""; MODEL_STATE=""
CODE_VER_ARG=""; DATA_VER_ARG=""
REGEN=false
FORCE_CODE=false; FORCE_DOCKER=false; FORCE_DOCKER_MCP=false
FORCE_DATA=false; FORCE_MODEL=false
PG_DUMP=false; INCLUDE_RAW=true; PG_ONLY=false

while [[ $# -gt 0 ]]; do
  case $1 in
    --code) SOURCE_STATE=yes; shift ;;
    --docker) DOCKER_STATE=yes; shift ;;
    --docker-mcp) DOCKER_MCP_STATE=yes; shift ;;
    --docker-keycloak) DOCKER_KC_STATE=yes; shift ;;
    --data) DATA_STATE=yes; shift ;;
    --model) MODEL_STATE=yes; shift ;;
    --no-code) SOURCE_STATE=no; shift ;;
    --no-docker) DOCKER_STATE=no; shift ;;
    --no-docker-mcp) DOCKER_MCP_STATE=no; shift ;;
    --no-data) DATA_STATE=no; shift ;;
    --no-model) MODEL_STATE=no; shift ;;
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
    --pg-only) PG_ONLY=true; PG_DUMP=true; INCLUDE_RAW=false; DATA_STATE=yes; shift ;;
    --no-raw) INCLUDE_RAW=false; shift ;;
    -h|--help) usage; exit 0 ;;
    *) err "알 수 없는 옵션: $1"; usage; exit 1 ;;
  esac
done

SOURCE_PATH="$(cd "$SOURCE_PATH" && pwd)"
mkdir -p "$OUT_DIR"

# --pg-dump alone implies data bundle
if $PG_DUMP && [[ "$DATA_STATE" != "no" ]]; then
  DATA_STATE=yes
fi

HAS_YES=false
[[ "$SOURCE_STATE" == "yes" || "$DOCKER_STATE" == "yes" || "$DOCKER_MCP_STATE" == "yes" || \
   "$DOCKER_KC_STATE" == "yes" || "$DATA_STATE" == "yes" || "$MODEL_STATE" == "yes" ]] && HAS_YES=true
if ! $HAS_YES; then
  [[ "$SOURCE_STATE" != "no" ]] && SOURCE_STATE=yes
  [[ "$DOCKER_STATE" != "no" ]] && DOCKER_STATE=yes
  [[ "$DOCKER_MCP_STATE" != "no" ]] && DOCKER_MCP_STATE=yes
  # keycloak opt-in only
  [[ "$DATA_STATE" != "no" ]] && DATA_STATE=yes
  [[ "$MODEL_STATE" != "no" ]] && MODEL_STATE=yes
fi
DO_SOURCE=false; [[ "$SOURCE_STATE" == "yes" ]] && DO_SOURCE=true
DO_DOCKER=false; [[ "$DOCKER_STATE" == "yes" ]] && DO_DOCKER=true
DO_DOCKER_MCP=false; [[ "$DOCKER_MCP_STATE" == "yes" ]] && DO_DOCKER_MCP=true
DO_DOCKER_KC=false; [[ "$DOCKER_KC_STATE" == "yes" ]] && DO_DOCKER_KC=true
DO_DATA=false; [[ "$DATA_STATE" == "yes" ]] && DO_DATA=true
DO_MODEL=false; [[ "$MODEL_STATE" == "yes" ]] && DO_MODEL=true

if ! $DO_SOURCE && ! $DO_DOCKER && ! $DO_DOCKER_MCP && ! $DO_DOCKER_KC && ! $DO_DATA && ! $DO_MODEL; then
  err "생성할 번들이 없습니다."; exit 1
fi
$PG_ONLY && info "PG-only data 번들 (raw 제외, 검색 인덱스 덤프)"

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

# Models live ONLY under this repo: SOURCE_PATH/models
# Never reference other projects (e.g. citec-wiki-qa). Copy HF cache into ./models if needed.
resolve_models_dir() {
  local proj="${SOURCE_PATH}/models"
  local m=""
  if [[ -n "${MODELS_HOST_DIR:-}" ]]; then
    m="${MODELS_HOST_DIR}"
  elif [[ -f "${SOURCE_PATH}/.env" ]]; then
    m=$(grep -E '^MODELS_HOST_DIR=' "${SOURCE_PATH}/.env" 2>/dev/null \
      | head -1 | cut -d= -f2- | tr -d ' "' || true)
  fi
  if [[ -n "$m" ]]; then
    [[ "$m" != /* ]] && m="${SOURCE_PATH}/${m#./}"
    if [[ -d "$m" ]]; then
      m="$(cd "$m" && pwd)"
    fi
    if [[ "$m" == *citec-wiki-qa* ]]; then
      err "MODELS_HOST_DIR 가 citec-wiki-qa 를 가리킵니다: $m"
      err "복사: rsync -a <src>/ ${SOURCE_PATH}/models/   후  MODELS_HOST_DIR=./models"
      exit 1
    fi
    if [[ "$m" == "${SOURCE_PATH}/models" || "$m" == "${SOURCE_PATH}/models/"* ]]; then
      echo "$m"
      return
    fi
    warn "MODELS_HOST_DIR 이 이 레포 models/ 가 아닙니다 ($m) — ${proj} 사용"
  fi
  echo "$proj"
}

embedding_model_name() {
  local n=""
  if [[ -f "${SOURCE_PATH}/.env" ]]; then
    n=$(grep -E '^EMBEDDING_MODEL=' "${SOURCE_PATH}/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d ' "' || true)
  fi
  echo "${n:-intfloat/multilingual-e5-base}"
}

CURRENT_CODE_NUM=$(cat "$CODE_VER_FILE" 2>/dev/null || echo "0")
CURRENT_DATA_NUM=$(cat "$DATA_VER_FILE" 2>/dev/null || echo "0")

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
    last=$(cat "$MODEL_NAME_FILE" 2>/dev/null || echo "")
    now=$(embedding_model_name)
    [[ "$last" == "$now" ]] && MODEL_EXISTS=true
  fi
fi

# Bump shared code/docker version when any of those bundles needs fresh output
NEED_NEW_CODE=false
if $DO_SOURCE || $DO_DOCKER || $DO_DOCKER_MCP || $DO_DOCKER_KC; then
  if $REGEN || $FORCE_CODE || $FORCE_DOCKER || $FORCE_DOCKER_MCP || [[ "$CURRENT_CODE_NUM" -eq 0 ]]; then
    NEED_NEW_CODE=true
  fi
  $DO_SOURCE && ! $CODE_EXISTS && NEED_NEW_CODE=true
  $DO_DOCKER && ! $DOCKER_EXISTS && NEED_NEW_CODE=true
  $DO_DOCKER_MCP && ! $DOCKER_MCP_EXISTS && NEED_NEW_CODE=true
  # keycloak always rebuilds when requested; still needs a version number
  $DO_DOCKER_KC && NEED_NEW_CODE=true
fi
# if files exist and nothing forced and not regen → reuse version
if ! $REGEN && ! $FORCE_CODE && ! $FORCE_DOCKER && ! $FORCE_DOCKER_MCP; then
  if $DO_SOURCE && $CODE_EXISTS && $DO_DOCKER && $DOCKER_EXISTS && $DO_DOCKER_MCP && $DOCKER_MCP_EXISTS && ! $DO_DOCKER_KC; then
    NEED_NEW_CODE=false
  fi
  # partial selection: only bump if selected missing or forced
  if ! $DO_SOURCE && ! $DO_DOCKER && ! $DO_DOCKER_MCP && $DO_DOCKER_KC; then
    NEED_NEW_CODE=true
  fi
fi

NEED_NEW_DATA=false
if $DO_DATA; then
  if ! $DATA_EXISTS || $REGEN || $FORCE_DATA || $PG_DUMP; then NEED_NEW_DATA=true; fi
fi
NEED_NEW_MODEL=false
if $DO_MODEL; then
  if ! $MODEL_EXISTS || $REGEN || $FORCE_MODEL; then NEED_NEW_MODEL=true; fi
fi

# ── plan display ─────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}════════════════════════════════════════════════${RESET}"
echo -e "${BOLD}  citec-kb 배포 번들 생성  |  $(date '+%Y-%m-%d %H:%M:%S')${RESET}"
echo -e "  source: ${SOURCE_PATH}"
echo -e "  out:    ${OUT_DIR}"
$REGEN && echo -e "  ${YELLOW}강제 재생성 (--regen)${RESET}"
$PG_DUMP && echo -e "  ${YELLOW}PG 덤프 포함 (--pg-dump)${RESET}"
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

if $DO_SOURCE || $DO_DOCKER || $DO_DOCKER_MCP || $DO_DOCKER_KC; then
  if $NEED_NEW_CODE; then
    _cv="v$((CURRENT_CODE_NUM + 1))"
    [[ -n "$CODE_VER_ARG" ]] && _cv="v${CODE_VER_ARG#v}"
  else
    _cv="v${CURRENT_CODE_NUM}"
  fi
  if $DO_SOURCE; then
    _w=false; ($NEED_NEW_CODE || ! $CODE_EXISTS) && _w=true
    _show "code" "$_w" "citec-kb-code-${_cv}.tar.gz"
  fi
  if $DO_DOCKER; then
    _w=false; ($NEED_NEW_CODE || ! $DOCKER_EXISTS || $FORCE_DOCKER) && _w=true
    _show "docker (core5)" "$_w" "citec-kb-docker-${_cv}.tar.gz"
  fi
  if $DO_DOCKER_MCP; then
    _w=false; ($NEED_NEW_CODE || ! $DOCKER_MCP_EXISTS || $FORCE_DOCKER_MCP) && _w=true
    _show "docker-mcp" "$_w" "citec-kb-docker-mcp-${_cv}.tar.gz"
  fi
  if $DO_DOCKER_KC; then
    _show "docker-keycloak" "true" "citec-kb-docker-keycloak-${_cv}.tar.gz"
  fi
fi
if $DO_DATA; then
  if $NEED_NEW_DATA; then _dv="d$((CURRENT_DATA_NUM + 1))"
    [[ -n "$DATA_VER_ARG" ]] && _dv="d${DATA_VER_ARG#d}"
  else _dv="d${CURRENT_DATA_NUM}"; fi
  _show "data" "$NEED_NEW_DATA" "citec-kb-data-${_dv}.tar.gz"
fi
$DO_MODEL && _show "model" "$NEED_NEW_MODEL" "citec-kb-model.tar.gz"
echo -e "${BOLD}════════════════════════════════════════════════${RESET}"
echo ""

# ── version commit ───────────────────────────────────────────────────────
CODE_VERSION=""
if $DO_SOURCE || $DO_DOCKER || $DO_DOCKER_MCP || $DO_DOCKER_KC; then
  if $NEED_NEW_CODE; then
    CODE_VERSION=$(resolve_version "$CODE_VER_FILE" "v" "${CODE_VER_ARG:-}")
  else
    CODE_VERSION="v${CURRENT_CODE_NUM}"
    [[ -n "$CODE_VER_ARG" ]] && CODE_VERSION="v${CODE_VER_ARG#v}"
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

RESULT_SOURCE=""; RESULT_DOCKER=""; RESULT_DOCKER_MCP=""; RESULT_DOCKER_KC=""
RESULT_DATA=""; RESULT_MODEL=""

# ── builders ─────────────────────────────────────────────────────────────
build_source_bundle() {
  local version="$1"
  banner "code  ${version}"
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
  for f in DEPLOY.md EXTERNAL_API.md MCP.md OIDC_IDP_SETUP.md; do
    [[ -f "${SOURCE_PATH}/docs/${f}" ]] && cp -a "${SOURCE_PATH}/docs/${f}" "$dest/docs/"
  done

  for f in docker-compose.yml rebuild.sh README.md .env.example .gitignore .dockerignore; do
    [[ -e "${SOURCE_PATH}/${f}" ]] && cp -a "${SOURCE_PATH}/${f}" "$dest/"
  done

  # empty mount targets
  mkdir -p "$dest/data/raw" "$dest/data/seeds" "$dest/models" "$dest/logs"

  # strip junk
  find "$dest" -name '__pycache__' -type d -print0 | xargs -0r rm -rf
  find "$dest" \( -name '*.pyc' -o -name '*.pyo' -o -name '.DS_Store' \) -delete
  find "$dest" -type d -name 'tests' -path '*/apps/*' -print0 | xargs -0r rm -rf
  find "$dest" -type d -name '.pytest_cache' -print0 | xargs -0r rm -rf
  rm -f "$dest/.env" 2>/dev/null || true

  # prod run helper (never --build on air-gap)
  cat > "$dest/run_stack.sh" << 'PROD'
#!/usr/bin/env bash
# citec-kb 스택 재시작 (운용) — 이미지 빌드 없음
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -f .env ]] || { echo "ERROR: .env 없음 — .env.example 을 복사해 설정하세요"; exit 1; }
if [[ ! -d models/hub ]] && [[ -z "${MODELS_HOST_DIR:-}" ]]; then
  echo "WARN: models/hub 없음 — out.sh --model / in.sh --model 필요 (벡터 검색)"
fi
echo "=== docker compose down ==="
sudo docker compose down --remove-orphans 2>/dev/null || true
echo "=== docker compose up -d ==="
sudo docker compose up -d
sudo docker compose ps
echo "헬스: curl -s localhost:8573/v1/health"
PROD
  chmod +x "$dest/run_stack.sh"

  log "[2/3] 요약"
  du -sh "$dest"/apps "$dest"/config "$dest"/mcp-server "$dest"/scripts \
    "$dest"/docker-compose.yml 2>/dev/null | sed 's/^/  /' || true

  log "[3/3] tar"
  (cd "$TPATH" && tar czf "$output_tgz" "$REPO")
  rm -rf "$TPATH"
  log "완료: $(basename "$output_tgz") ($(du -sh "$output_tgz" | cut -f1))"
  RESULT_SOURCE="$output_tgz"
}

_IMAGES_BUILT=false
_ensure_built() {
  $_IMAGES_BUILT && return
  log "docker compose build api worker mcp"
  (
    cd "$SOURCE_PATH"
    docker compose build api worker mcp
  )
  # With explicit image: tags, compose tags correctly — verify
  local img
  for img in "$IMG_API" "$IMG_WORKER" "$IMG_MCP"; do
    if ! docker image inspect "$img" &>/dev/null; then
      err "빌드 후 이미지 없음: $img"
      err "docker-compose.yml 의 image: 태그를 확인하세요"
      exit 1
    fi
  done
  _IMAGES_BUILT=true
}

_require_or_pull() {
  local img="$1"
  if docker image inspect "$img" &>/dev/null; then return 0; fi
  log "pull $img"
  docker pull "$img"
}

_write_image_list() {
  local path="$1"; shift
  {
    echo "# citec-kb image list $(date -Iseconds)"
    printf '%s\n' "$@"
  } > "$path"
}

build_docker_bundle() {
  local version="$1"
  banner "docker (core5)  ${version}"
  info "api worker nginx redis pgvector  (mcp·keycloak 제외)"
  local output_tgz="${OUT_DIR}/citec-kb-docker-${version}.tar.gz"
  local list_file="${OUT_DIR}/citec-kb-docker-${version}.images.txt"
  _ensure_built

  local imgs=() i
  for i in "${CORE_IMAGES[@]}"; do
    case "$i" in
      citec-kb-*) docker image inspect "$i" &>/dev/null || { err "없음: $i"; exit 1; } ;;
      *) _require_or_pull "$i" ;;
    esac
    imgs+=("$i")
  done

  _write_image_list "$list_file" \
    "postgres  $IMG_PG" \
    "redis     $IMG_REDIS" \
    "web       $IMG_NGINX" \
    "api       $IMG_API" \
    "worker    $IMG_WORKER"

  log "docker save (${#imgs[@]}): ${imgs[*]}"
  docker save "${imgs[@]}" | gzip > "$output_tgz"
  log "완료: $(basename "$output_tgz") ($(du -sh "$output_tgz" | cut -f1))"
  RESULT_DOCKER="$output_tgz"
}

build_docker_mcp_bundle() {
  local version="$1"
  banner "docker-mcp  ${version}"
  local output_tgz="${OUT_DIR}/citec-kb-docker-mcp-${version}.tar.gz"
  _ensure_built
  docker image inspect "$IMG_MCP" &>/dev/null || { err "없음: $IMG_MCP"; exit 1; }
  docker save "$IMG_MCP" | gzip > "$output_tgz"
  echo "mcp  $IMG_MCP" > "${OUT_DIR}/citec-kb-docker-mcp-${version}.images.txt"
  log "완료: $(basename "$output_tgz") ($(du -sh "$output_tgz" | cut -f1))"
  RESULT_DOCKER_MCP="$output_tgz"
}

build_docker_keycloak_bundle() {
  local version="$1"
  banner "docker-keycloak  ${version}"
  local output_tgz="${OUT_DIR}/citec-kb-docker-keycloak-${version}.tar.gz"
  _require_or_pull "$IMG_KC"
  docker save "$IMG_KC" | gzip > "$output_tgz"
  echo "keycloak  $IMG_KC" > "${OUT_DIR}/citec-kb-docker-keycloak-${version}.images.txt"
  log "완료: $(basename "$output_tgz") ($(du -sh "$output_tgz" | cut -f1))"
  RESULT_DOCKER_KC="$output_tgz"
}

build_data_bundle() {
  local version="$1"
  banner "data  ${version}"
  local output_tgz="${OUT_DIR}/citec-kb-data-${version}.tar.gz"
  local staging="$TPATH/data-staging"
  rm -rf "$TPATH"
  mkdir -p "$staging/data"

  if $INCLUDE_RAW && [[ -d "${SOURCE_PATH}/data/raw" ]]; then
    log "raw 복사"
    cp -a "${SOURCE_PATH}/data/raw" "$staging/data/"
  else
    warn "raw 제외 또는 없음"
    mkdir -p "$staging/data/raw"
  fi

  if [[ -d "${SOURCE_PATH}/data/seeds" ]]; then
    cp -a "${SOURCE_PATH}/data/seeds" "$staging/data/"
  fi
  [[ -f "${SOURCE_PATH}/data/raw_manifest.json" ]] && \
    cp -a "${SOURCE_PATH}/data/raw_manifest.json" "$staging/data/"

  if $PG_DUMP; then
    log "Postgres 덤프 (인덱스·임베딩 포함 복제용, --no-owner --no-acl)"
    mkdir -p "$staging/data/backups"
    local dump_file="$staging/data/backups/citec_knowledge-${TS}.sql.gz"
    local dumped=false
    local pg_user="${POSTGRES_USER:-citec}"
    local pg_db="${POSTGRES_DB:-citec_knowledge}"
    # dump flags: portable restore after DROP SCHEMA on ops
    if docker compose -f "${SOURCE_PATH}/docker-compose.yml" ps --status running 2>/dev/null | grep -q postgres; then
      if (cd "$SOURCE_PATH" && docker compose exec -T postgres \
            pg_dump --no-owner --no-acl -U "$pg_user" "$pg_db") \
          | gzip > "$dump_file"; then
        dumped=true
      fi
    fi
    if ! $dumped; then
      local host_port="${POSTGRES_PORT:-8574}"
      if command -v pg_dump >/dev/null 2>&1; then
        PGPASSWORD="${POSTGRES_PASSWORD:-citec}" pg_dump \
          -h 127.0.0.1 -p "$host_port" \
          --no-owner --no-acl \
          -U "$pg_user" "$pg_db" \
          | gzip > "$dump_file" && dumped=true || true
      fi
    fi
    if $dumped && [[ -s "$dump_file" ]]; then
      log "덤프: $(basename "$dump_file") ($(du -sh "$dump_file" | cut -f1))"
      # side counts for ops verification
      local counts=""
      if (cd "$SOURCE_PATH" && docker compose exec -T postgres \
            psql -U "$pg_user" -d "$pg_db" -t -A -c \
            "SELECT 'documents='||count(*) FROM documents;
             SELECT 'chunks='||count(*) FROM chunks WHERE is_active;
             SELECT 'embeddings='||count(*) FROM embeddings;" 2>/dev/null); then
        counts=$(cd "$SOURCE_PATH" && docker compose exec -T postgres \
          psql -U "$pg_user" -d "$pg_db" -t -A -c \
          "SELECT 'documents='||(SELECT count(*) FROM documents)||
                  ' chunks='||(SELECT count(*) FROM chunks WHERE is_active)||
                  ' embeddings='||(SELECT count(*) FROM embeddings);" 2>/dev/null | tr -d '\r' | head -1)
      fi
      {
        echo "dump_file=$(basename "$dump_file")"
        echo "created=$(date -Iseconds)"
        echo "counts=${counts:-unknown}"
        echo "restore=in.sh --data --restore-pg   # drops public schema first"
      } > "$staging/data/backups/citec_knowledge-${TS}.meta.txt"
      [[ -n "$counts" ]] && log "DB 건수: $counts"
    else
      if $PG_ONLY || ! $INCLUDE_RAW; then
        err "PG 덤프 실패 — postgres 기동 여부 확인 후 재시도"
        exit 1
      fi
      warn "PG 덤프 실패 — raw/seeds 만 포함"
      rm -f "$dump_file"
    fi
  fi

  {
    echo "# citec-kb data ${version}"
    echo "time: $(date -Iseconds)"
    echo "host: $(hostname)"
    echo "raw_files: $(find "$staging/data/raw" -type f 2>/dev/null | wc -l)"
    echo "raw_size: $(du -sh "$staging/data/raw" 2>/dev/null | cut -f1)"
    echo "pg_dump: $PG_DUMP"
    echo "pg_only: $PG_ONLY"
    echo ""
    echo "NOTE: 파일 코퍼스만으로는 검색 불가. 벡터/FTS 는 Postgres."
    echo "  권장 복원: scripts/in.sh --data --restore-pg -y"
    echo "  (api 기동 전 schema drop → dump → 전체 up, 건수 검증)"
  } > "$staging/data/manifest.txt"

  (cd "$staging" && tar czf "$output_tgz" data/)
  rm -rf "$TPATH"
  log "완료: $(basename "$output_tgz") ($(du -sh "$output_tgz" | cut -f1))"
  RESULT_DATA="$output_tgz"
}

build_model_bundle() {
  banner "model"
  local models_dir
  models_dir=$(resolve_models_dir)
  info "MODELS_HOST_DIR → ${models_dir}"

  if [[ ! -d "${models_dir}/hub" ]]; then
    err "models/hub 없음: ${models_dir}"
    err "이 프로젝트 models/ 에 HF 캐시를 복사하세요 (다른 프로젝트 경로 참조 금지)."
    err "  예: rsync -a /path/to/hf-cache/hub/ ${SOURCE_PATH}/models/hub/"
    err "  또는 prepare_offline_model 등으로 ${SOURCE_PATH}/models 에 적재"
    exit 1
  fi

  local model_name
  model_name=$(embedding_model_name)
  info "EMBEDDING_MODEL: ${model_name}"

  local output_tgz="${OUT_DIR}/citec-kb-model.tar.gz"
  local staging="$TPATH/model-staging"
  rm -rf "$TPATH"
  mkdir -p "$staging/models"

  # IMPORTANT: always real copy — never tar a symlink (broken on air-gap extract)
  log "모델 복사 (실파일, symlink 따라감)"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --copy-links "${models_dir}/" "$staging/models/"
  else
    cp -aL "${models_dir}/." "$staging/models/" 2>/dev/null || cp -a "${models_dir}/." "$staging/models/"
  fi
  echo "$model_name" > "$staging/models/model.name"

  local sz
  sz=$(du -sh "$staging/models" | cut -f1)
  info "압축 전 크기: ${sz}"
  if [[ $(du -sm "$staging/models" | cut -f1) -lt 50 ]]; then
    warn "모델 디렉터리가 비정상적으로 작음 (<50MB) — hub 내용 확인"
  fi

  (cd "$staging" && tar czf "$output_tgz" models/)
  echo "$model_name" > "${OUT_DIR}/citec-kb-model.tar.gz.name"
  echo "$model_name" > "$MODEL_NAME_FILE"
  rm -rf "$TPATH"
  log "완료: citec-kb-model.tar.gz ($(du -sh "$output_tgz" | cut -f1))"
  RESULT_MODEL="$output_tgz"
}

# ── main ─────────────────────────────────────────────────────────────────
if $DO_SOURCE; then
  if ! $NEED_NEW_CODE && $CODE_EXISTS; then
    info "재사용: citec-kb-code-${CODE_VERSION}.tar.gz"
    RESULT_SOURCE="${OUT_DIR}/citec-kb-code-${CODE_VERSION}.tar.gz"
  else
    build_source_bundle "$CODE_VERSION"
  fi
fi

if $DO_DOCKER; then
  if ! $NEED_NEW_CODE && $DOCKER_EXISTS && ! $FORCE_DOCKER; then
    info "재사용: citec-kb-docker-${CODE_VERSION}.tar.gz"
    RESULT_DOCKER="${OUT_DIR}/citec-kb-docker-${CODE_VERSION}.tar.gz"
  else
    build_docker_bundle "$CODE_VERSION"
  fi
fi

if $DO_DOCKER_MCP; then
  if ! $NEED_NEW_CODE && $DOCKER_MCP_EXISTS && ! $FORCE_DOCKER_MCP; then
    info "재사용: citec-kb-docker-mcp-${CODE_VERSION}.tar.gz"
    RESULT_DOCKER_MCP="${OUT_DIR}/citec-kb-docker-mcp-${CODE_VERSION}.tar.gz"
  else
    build_docker_mcp_bundle "$CODE_VERSION"
  fi
fi

if $DO_DOCKER_KC; then
  build_docker_keycloak_bundle "$CODE_VERSION"
fi

if $DO_DATA; then
  if ! $NEED_NEW_DATA; then
    info "재사용: citec-kb-data-${DATA_VERSION}.tar.gz"
    RESULT_DATA="${OUT_DIR}/citec-kb-data-${DATA_VERSION}.tar.gz"
  else
    build_data_bundle "$DATA_VERSION"
  fi
fi

if $DO_MODEL; then
  if ! $NEED_NEW_MODEL; then
    info "재사용: citec-kb-model.tar.gz"
    RESULT_MODEL="${OUT_DIR}/citec-kb-model.tar.gz"
  else
    build_model_bundle
  fi
fi

echo ""
echo -e "${BOLD}════════════════════════════════════════════════${RESET}"
echo -e "${BOLD}  생성 완료${RESET}"
[[ -n "$RESULT_SOURCE" ]] && echo "  code:          $(basename "$RESULT_SOURCE") ($(du -sh "$RESULT_SOURCE" | cut -f1))"
[[ -n "$RESULT_DOCKER" ]] && echo "  docker:        $(basename "$RESULT_DOCKER") ($(du -sh "$RESULT_DOCKER" | cut -f1))"
[[ -n "$RESULT_DOCKER_MCP" ]] && echo "  docker-mcp:    $(basename "$RESULT_DOCKER_MCP") ($(du -sh "$RESULT_DOCKER_MCP" | cut -f1))"
[[ -n "$RESULT_DOCKER_KC" ]] && echo "  keycloak:      $(basename "$RESULT_DOCKER_KC") ($(du -sh "$RESULT_DOCKER_KC" | cut -f1))"
[[ -n "$RESULT_DATA" ]] && echo "  data:          $(basename "$RESULT_DATA") ($(du -sh "$RESULT_DATA" | cut -f1))"
[[ -n "$RESULT_MODEL" ]] && echo "  model:         $(basename "$RESULT_MODEL") ($(du -sh "$RESULT_MODEL" | cut -f1))"
echo ""
echo "  전송: scp ${OUT_DIR}/citec-kb-*.tar.gz user@prod:~/"
echo "  운용: ~/citec-kb/scripts/in.sh   또는  code 번들의 scripts/in.sh"
echo -e "${BOLD}════════════════════════════════════════════════${RESET}"
