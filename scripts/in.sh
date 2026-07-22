#!/usr/bin/env bash
# in.sh — 운용(폐쇄망) 시스템: citec-kb 배포 번들 적용
#
# 번들을 홈(~)에 둔 뒤 실행:
#   citec-kb-code-vN.tar.gz
#   citec-kb-docker-vN.tar.gz            # core5: api worker nginx redis pgvector
#   citec-kb-docker-mcp-vN.tar.gz
#   citec-kb-docker-keycloak-vN.tar.gz   # optional
#   citec-kb-data-dN.tar.gz
#   citec-kb-model.tar.gz
#
# 동일 버전은 건너뜀 (--force 로 재적용).
set -euo pipefail

usage() {
  cat <<'EOF'
in.sh — citec-kb 운용 시스템: 배포 번들 적용

USAGE
  scripts/in.sh [옵션]
  옵션 없음: ~/ 에서 번들 자동 감지, 변경분만 배포

포함/제외:
  --code / --no-code
  --docker / --no-docker
  --docker-mcp / --no-docker-mcp
  --docker-keycloak
  --data / --no-data
  --model / --no-model

동작:
  --force, -f           버전 동일해도 강제
  --no-restart          컨테이너 재시작 생략
  --yes, -y             확인 생략
  --dry-run, -n         계획만
  --restore-pg          PG 덤프 깨끗이 복원 (public 스키마 DROP 후,
                        api 기동 전 복원 · 건수 검증). 최초/인덱스 동기화 시 필수

버전:
  --code-ver VER
  --docker-ver VER
  --docker-mcp-ver VER
  --docker-keycloak-ver VER
  --data-ver VER

경로:
  --home DIR
  --project DIR         기본: $HOME/citec-kb

예시:
  in.sh -y
  in.sh --code -y
  # 코드 + DB 인덱스 재동기화 (운영 깨진 restore 복구)
  in.sh --code --data --restore-pg -y
  in.sh --data --restore-pg -y
  in.sh --model -y
EOF
}

REAL_USER="${SUDO_USER:-$(id -un)}"
REAL_HOME=$(getent passwd "$REAL_USER" 2>/dev/null | cut -d: -f6)
REAL_HOME="${REAL_HOME:-$HOME}"
OWNER="${REAL_USER}:$(id -gn "$REAL_USER" 2>/dev/null || echo "$REAL_USER")"
REPO="citec-kb"
TEMP_DIR="$REAL_HOME/temp"
PROJECT_DIR="$REAL_HOME/$REPO"
DEPLOY_TRACK_DIR="$REAL_HOME/bin"

CODE_DEPLOYED_FILE="$DEPLOY_TRACK_DIR/.citec_kb_code_deployed"
DOCKER_DEPLOYED_FILE="$DEPLOY_TRACK_DIR/.citec_kb_docker_deployed"
DOCKER_MCP_DEPLOYED_FILE="$DEPLOY_TRACK_DIR/.citec_kb_docker_mcp_deployed"
DOCKER_KC_DEPLOYED_FILE="$DEPLOY_TRACK_DIR/.citec_kb_docker_keycloak_deployed"
DATA_DEPLOYED_FILE="$DEPLOY_TRACK_DIR/.citec_kb_data_deployed"
MODEL_DEPLOYED_FILE="$DEPLOY_TRACK_DIR/.citec_kb_model_deployed"

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

SOURCE_STATE=""; DOCKER_STATE=""; DOCKER_MCP_STATE=""; DOCKER_KC_STATE=""
DATA_STATE=""; MODEL_STATE=""
CODE_VER_ARG=""; DOCKER_VER_ARG=""; DOCKER_MCP_VER_ARG=""; DOCKER_KC_VER_ARG=""; DATA_VER_ARG=""
FORCE=false; NO_RESTART=false; YES=false; DRY_RUN=false; RESTORE_PG=false

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
    --force|-f) FORCE=true; shift ;;
    --no-restart) NO_RESTART=true; shift ;;
    --yes|-y) YES=true; shift ;;
    --dry-run|-n) DRY_RUN=true; shift ;;
    --restore-pg) RESTORE_PG=true; shift ;;
    --code-ver) CODE_VER_ARG="${2:-}"; shift 2 ;;
    --docker-ver) DOCKER_VER_ARG="${2:-}"; shift 2 ;;
    --docker-mcp-ver) DOCKER_MCP_VER_ARG="${2:-}"; shift 2 ;;
    --docker-keycloak-ver) DOCKER_KC_VER_ARG="${2:-}"; shift 2 ;;
    --data-ver) DATA_VER_ARG="${2:-}"; shift 2 ;;
    --home) REAL_HOME="${2:-}"; shift 2 ;;
    --project) PROJECT_DIR="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) err "알 수 없는 옵션: $1"; usage; exit 1 ;;
  esac
done

HAS_YES=false
[[ "$SOURCE_STATE" == "yes" || "$DOCKER_STATE" == "yes" || "$DOCKER_MCP_STATE" == "yes" || \
   "$DOCKER_KC_STATE" == "yes" || "$DATA_STATE" == "yes" || "$MODEL_STATE" == "yes" ]] && HAS_YES=true
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
DO_DOCKER_KC=false; [[ "$DOCKER_KC_STATE" == "yes" ]] && DO_DOCKER_KC=true
DO_DATA=false; [[ "$DATA_STATE" == "yes" ]] && DO_DATA=true
DO_MODEL=false; [[ "$MODEL_STATE" == "yes" ]] && DO_MODEL=true

TEMP_DIR="$REAL_HOME/temp"
DEPLOY_TRACK_DIR="$REAL_HOME/bin"
mkdir -p "$TEMP_DIR" "$DEPLOY_TRACK_DIR"

find_latest() {
  # Prefer exact prefix match; exclude sibling prefixes (docker vs docker-mcp)
  local pattern="$1" prefix="$2"
  local f best="" best_n=-1 n
  for f in "$REAL_HOME"/$pattern; do
    [[ -f "$f" ]] || continue
    n=$(basename "$f" | grep -Eo "${prefix}[0-9]+" | tail -1 | tr -d "$prefix" || true)
    [[ -n "$n" ]] || continue
    if [[ "$n" -gt "$best_n" ]]; then best_n=$n; best="${prefix}${n}"; fi
  done
  echo "$best"
}
normalize_ver() {
  local prefix="$1" val="$2"
  val="${val#${prefix}}"
  echo "${prefix}${val}"
}

if [[ -n "$CODE_VER_ARG" ]]; then SOURCE_AVAIL=$(normalize_ver "v" "$CODE_VER_ARG")
else SOURCE_AVAIL=$(find_latest 'citec-kb-code-v*.tar.gz' 'v'); fi

if [[ -n "$DOCKER_VER_ARG" ]]; then DOCKER_AVAIL=$(normalize_ver "v" "$DOCKER_VER_ARG")
else DOCKER_AVAIL=$(find_latest 'citec-kb-docker-v*.tar.gz' 'v'); fi

if [[ -n "$DOCKER_MCP_VER_ARG" ]]; then DOCKER_MCP_AVAIL=$(normalize_ver "v" "$DOCKER_MCP_VER_ARG")
else DOCKER_MCP_AVAIL=$(find_latest 'citec-kb-docker-mcp-v*.tar.gz' 'v'); fi

if [[ -n "$DOCKER_KC_VER_ARG" ]]; then DOCKER_KC_AVAIL=$(normalize_ver "v" "$DOCKER_KC_VER_ARG")
else DOCKER_KC_AVAIL=$(find_latest 'citec-kb-docker-keycloak-v*.tar.gz' 'v'); fi

if [[ -n "$DATA_VER_ARG" ]]; then DATA_AVAIL=$(normalize_ver "d" "$DATA_VER_ARG")
else DATA_AVAIL=$(find_latest 'citec-kb-data-d*.tar.gz' 'd'); fi

MODEL_AVAIL=""
if [[ -f "$REAL_HOME/citec-kb-model.tar.gz" ]]; then
  MODEL_AVAIL=$(cat "$REAL_HOME/citec-kb-model.tar.gz.name" 2>/dev/null | tr -d '\n\r' || echo "model")
fi

# auto-enable keycloak only when bundle present
if ! $HAS_YES && [[ -n "$DOCKER_KC_AVAIL" ]]; then DO_DOCKER_KC=true; fi

SOURCE_DEPLOYED=$(cat "$CODE_DEPLOYED_FILE" 2>/dev/null || echo "")
DOCKER_DEPLOYED=$(cat "$DOCKER_DEPLOYED_FILE" 2>/dev/null || echo "")
DOCKER_MCP_DEPLOYED=$(cat "$DOCKER_MCP_DEPLOYED_FILE" 2>/dev/null || echo "")
DOCKER_KC_DEPLOYED=$(cat "$DOCKER_KC_DEPLOYED_FILE" 2>/dev/null || echo "")
DATA_DEPLOYED=$(cat "$DATA_DEPLOYED_FILE" 2>/dev/null || echo "")
MODEL_DEPLOYED=$(cat "$MODEL_DEPLOYED_FILE" 2>/dev/null || echo "")

should_deploy() {
  [[ "$1" == "false" ]] && return 1
  [[ -z "$2" ]] && return 1
  $FORCE && return 0
  [[ "$2" != "$3" ]] && return 0
  return 1
}

DEPLOY_SOURCE=false; DEPLOY_DOCKER=false; DEPLOY_DOCKER_MCP=false; DEPLOY_DOCKER_KC=false
DEPLOY_DATA=false; DEPLOY_MODEL=false
should_deploy "$DO_SOURCE" "$SOURCE_AVAIL" "$SOURCE_DEPLOYED" && DEPLOY_SOURCE=true
should_deploy "$DO_DOCKER" "$DOCKER_AVAIL" "$DOCKER_DEPLOYED" && DEPLOY_DOCKER=true
should_deploy "$DO_DOCKER_MCP" "$DOCKER_MCP_AVAIL" "$DOCKER_MCP_DEPLOYED" && DEPLOY_DOCKER_MCP=true
should_deploy "$DO_DOCKER_KC" "$DOCKER_KC_AVAIL" "$DOCKER_KC_DEPLOYED" && DEPLOY_DOCKER_KC=true
should_deploy "$DO_DATA" "$DATA_AVAIL" "$DATA_DEPLOYED" && DEPLOY_DATA=true
should_deploy "$DO_MODEL" "$MODEL_AVAIL" "$MODEL_DEPLOYED" && DEPLOY_MODEL=true

echo ""
echo "════════════════════════════════════════════════"
echo "  citec-kb 배포  |  $(date '+%Y-%m-%d %H:%M:%S')"
echo "  home: $REAL_HOME"
echo "  project: $PROJECT_DIR"
echo "════════════════════════════════════════════════"
print_status() {
  local label="$1" avail="$2" deployed="$3" do_flag="$4" will="$5"
  printf "  %-14s " "$label"
  if [[ -z "$avail" ]]; then
    printf "파일 없음"
  else
    printf "%-8s" "$avail"
    if [[ "$will" == "true" ]]; then
      if [[ -z "$deployed" ]]; then printf "  ${GREEN}→ 신규${RESET}"
      else printf "  (현재 %-6s) ${GREEN}→ 업데이트${RESET}" "$deployed"; fi
    else
      if [[ "$do_flag" == "false" ]]; then printf "  ${CYAN}(제외)${RESET}"
      else printf "  (현재 %-6s) ${YELLOW}→ 동일·생략${RESET}" "$deployed"; fi
    fi
  fi
  echo ""
}
print_status "code" "$SOURCE_AVAIL" "$SOURCE_DEPLOYED" "$DO_SOURCE" "$DEPLOY_SOURCE"
print_status "docker" "$DOCKER_AVAIL" "$DOCKER_DEPLOYED" "$DO_DOCKER" "$DEPLOY_DOCKER"
print_status "docker-mcp" "$DOCKER_MCP_AVAIL" "$DOCKER_MCP_DEPLOYED" "$DO_DOCKER_MCP" "$DEPLOY_DOCKER_MCP"
print_status "keycloak" "$DOCKER_KC_AVAIL" "$DOCKER_KC_DEPLOYED" "$DO_DOCKER_KC" "$DEPLOY_DOCKER_KC"
print_status "data" "$DATA_AVAIL" "$DATA_DEPLOYED" "$DO_DATA" "$DEPLOY_DATA"
print_status "model" "$MODEL_AVAIL" "$MODEL_DEPLOYED" "$DO_MODEL" "$DEPLOY_MODEL"
echo ""

if ! $DEPLOY_SOURCE && ! $DEPLOY_DOCKER && ! $DEPLOY_DOCKER_MCP && ! $DEPLOY_DOCKER_KC && ! $DEPLOY_DATA && ! $DEPLOY_MODEL; then
  warn "배포할 항목 없음 (--force 가능)"
  exit 0
fi
$DRY_RUN && { warn "DRY-RUN — 종료"; exit 0; }

if ! $YES; then
  echo -e "${RED}계속할까요? [y/N]${RESET} "
  read -r CONFIRM
  [[ "${CONFIRM}" =~ ^[Yy]$ ]] || { warn "취소"; exit 0; }
fi

CONTAINERS_WERE_RUNNING=false
stop_all_containers() {
  if [[ ! -f "${PROJECT_DIR}/docker-compose.yml" ]]; then
    warn "프로젝트 없음 — 중지 생략"
    return
  fi
  if sudo docker compose -f "${PROJECT_DIR}/docker-compose.yml" ps 2>/dev/null | grep -qE "Up|running"; then
    CONTAINERS_WERE_RUNNING=true
    log "컨테이너 중지"
    # network Removing 단계가 가끔 무한 대기 → timeout + stop 폴백
    (
      cd "${PROJECT_DIR}"
      export COMPOSE_PROGRESS="${COMPOSE_PROGRESS:-plain}"
      if command -v timeout >/dev/null 2>&1; then
        timeout 90 sudo docker compose down --remove-orphans \
          || timeout 30 sudo docker compose stop \
          || true
      else
        sudo docker compose down --remove-orphans || sudo docker compose stop || true
      fi
    ) || true
  fi
}
ensure_env_file() {
  if [[ ! -f "${PROJECT_DIR}/.env" ]]; then
    if [[ -f "${PROJECT_DIR}/.env.example" ]]; then
      warn ".env 없음 — .env.example 복사 (키 값은 직접 설정 필요)"
      cp "${PROJECT_DIR}/.env.example" "${PROJECT_DIR}/.env"
      chown "$OWNER" "${PROJECT_DIR}/.env" 2>/dev/null || true
    else
      err ".env 없음: ${PROJECT_DIR}/.env"; exit 1
    fi
  fi
  if ! grep -q '^MODELS_HOST_DIR=' "${PROJECT_DIR}/.env" 2>/dev/null; then
    echo "MODELS_HOST_DIR=${PROJECT_DIR}/models" >> "${PROJECT_DIR}/.env"
  fi
  if ! grep -q '^TRANSFORMERS_OFFLINE=' "${PROJECT_DIR}/.env" 2>/dev/null; then
    echo "TRANSFORMERS_OFFLINE=1" >> "${PROJECT_DIR}/.env"
  fi
  if ! grep -q '^HF_HUB_OFFLINE=' "${PROJECT_DIR}/.env" 2>/dev/null; then
    echo "HF_HUB_OFFLINE=1" >> "${PROJECT_DIR}/.env"
  fi
}

start_all_containers() {
  $NO_RESTART && { warn "--no-restart — 시작 생략"; return; }
  local force_start="${1:-false}"
  if $CONTAINERS_WERE_RUNNING || $DEPLOY_SOURCE || $DEPLOY_DOCKER || $DEPLOY_DOCKER_MCP || \
     $DEPLOY_DOCKER_KC || $DEPLOY_MODEL || $DEPLOY_DATA || [[ "$force_start" == "true" ]]; then
    ensure_env_file
    cd "${PROJECT_DIR}"
    # Avoid empty-array mapfile → docker compose "" (unknown command "compose ")
    export COMPOSE_PROGRESS="${COMPOSE_PROGRESS:-plain}"
    if $DEPLOY_DOCKER_KC && [[ -f "${PROJECT_DIR}/deploy/keycloak/realm-citec.json" ]]; then
      log "docker compose --profile keycloak up -d"
      sudo docker compose --profile keycloak up -d
    else
      log "docker compose up -d"
      sudo docker compose up -d
    fi
    sudo docker compose ps 2>/dev/null || true
  else
    warn "원래 중지 상태 — 시작 생략"
  fi
}

start_postgres_only() {
  ensure_env_file
  cd "${PROJECT_DIR}"
  log "postgres (+ redis) 만 기동 — api 기동 전 DB 복원용"
  sudo docker compose up -d postgres redis
  local i
  for i in $(seq 1 60); do
    # Must use -d citec_knowledge: default DB name = user "citec" does not exist → FATAL spam
    if sudo docker compose exec -T postgres \
         pg_isready -U citec -d citec_knowledge &>/dev/null; then
      log "postgres ready"
      return 0
    fi
    sleep 2
  done
  err "postgres 기동 타임아웃"
  exit 1
}

stop_all_containers

deploy_source() {
  banner "code  ${SOURCE_AVAIL}"
  local tgz="$REAL_HOME/citec-kb-code-${SOURCE_AVAIL}.tar.gz"
  [[ -f "$tgz" ]] || { err "없음: $tgz"; exit 1; }

  # self-preserve this running in.sh (bundle may ship older copy)
  local insh_self
  insh_self=$(cat "${BASH_SOURCE[0]}" 2>/dev/null || true)

  local models_json env_bak
  models_json=$(sudo cat "${PROJECT_DIR}/config/models.json" 2>/dev/null || true)
  env_bak=$(sudo cat "${PROJECT_DIR}/.env" 2>/dev/null || true)

  log "추출 → $REAL_HOME/$REPO"
  sudo tar xzf "$tgz" -C "$REAL_HOME" --no-same-owner

  if [[ -n "$insh_self" ]]; then
    echo "$insh_self" | sudo tee "${PROJECT_DIR}/scripts/in.sh" >/dev/null
    sudo chmod +x "${PROJECT_DIR}/scripts/in.sh"
    log "in.sh 자기보존"
  fi
  if [[ -n "$models_json" ]]; then
    echo "$models_json" | sudo tee "${PROJECT_DIR}/config/models.json" >/dev/null
    log "config/models.json 보존"
  fi
  if [[ -n "$env_bak" ]]; then
    echo "$env_bak" | sudo tee "${PROJECT_DIR}/.env" >/dev/null
    log ".env 보존"
  elif [[ -f "${PROJECT_DIR}/.env.example" && ! -f "${PROJECT_DIR}/.env" ]]; then
    sudo cp "${PROJECT_DIR}/.env.example" "${PROJECT_DIR}/.env"
    warn ".env 생성 — LLM 키 등 설정 필요"
  fi

  sudo chown -R "$OWNER" "${PROJECT_DIR}" 2>/dev/null || true
  for d in apps scripts config mcp-server deploy; do
    [[ -d "${PROJECT_DIR}/$d" ]] && sudo chmod -R a+rX "${PROJECT_DIR}/$d" || true
  done
  mkdir -p "${PROJECT_DIR}/data/raw" "${PROJECT_DIR}/data/seeds" "${PROJECT_DIR}/logs" "${PROJECT_DIR}/models"

  mv "$tgz" "$TEMP_DIR/" 2>/dev/null || true
  echo "$SOURCE_AVAIL" > "$CODE_DEPLOYED_FILE"
  log "✅ code ${SOURCE_AVAIL} (마운트 반영 — 재시작 후 적용)"
  info "requirements/Dockerfile 변경 시 docker 번들도 배포"
}

deploy_docker() {
  banner "docker  ${DOCKER_AVAIL}"
  local tgz="$REAL_HOME/citec-kb-docker-${DOCKER_AVAIL}.tar.gz"
  [[ -f "$tgz" ]] || { err "없음: $tgz"; exit 1; }
  log "docker load"
  gunzip -c "$tgz" | sudo docker load
  mv "$tgz" "$TEMP_DIR/" 2>/dev/null || true
  echo "$DOCKER_AVAIL" > "$DOCKER_DEPLOYED_FILE"
  log "✅ docker ${DOCKER_AVAIL}"
}

deploy_docker_mcp() {
  banner "docker-mcp  ${DOCKER_MCP_AVAIL}"
  local tgz="$REAL_HOME/citec-kb-docker-mcp-${DOCKER_MCP_AVAIL}.tar.gz"
  [[ -f "$tgz" ]] || { err "없음: $tgz"; exit 1; }
  gunzip -c "$tgz" | sudo docker load
  mv "$tgz" "$TEMP_DIR/" 2>/dev/null || true
  echo "$DOCKER_MCP_AVAIL" > "$DOCKER_MCP_DEPLOYED_FILE"
  log "✅ docker-mcp ${DOCKER_MCP_AVAIL}"
}

deploy_docker_keycloak() {
  banner "docker-keycloak  ${DOCKER_KC_AVAIL}"
  local tgz="$REAL_HOME/citec-kb-docker-keycloak-${DOCKER_KC_AVAIL}.tar.gz"
  [[ -f "$tgz" ]] || { err "없음: $tgz"; exit 1; }
  gunzip -c "$tgz" | sudo docker load
  mv "$tgz" "$TEMP_DIR/" 2>/dev/null || true
  echo "$DOCKER_KC_AVAIL" > "$DOCKER_KC_DEPLOYED_FILE"
  log "✅ keycloak ${DOCKER_KC_AVAIL}"
}

deploy_data() {
  banner "data  ${DATA_AVAIL}"
  [[ -d "$PROJECT_DIR" ]] || { err "프로젝트 없음 — 먼저 --code"; exit 1; }
  local tgz="$REAL_HOME/citec-kb-data-${DATA_AVAIL}.tar.gz"
  [[ -f "$tgz" ]] || { err "없음: $tgz"; exit 1; }

  local snap_dir="${PROJECT_DIR}/data/backups"
  mkdir -p "$snap_dir"
  if [[ -d "${PROJECT_DIR}/data/raw" ]] || [[ -d "${PROJECT_DIR}/data/seeds" ]]; then
    log "롤백 스냅샷"
    sudo tar -czf "${snap_dir}/pre-data-$(date +%Y%m%d_%H%M%S).tar.gz" \
      -C "${PROJECT_DIR}" data/seeds data/raw_manifest.json 2>/dev/null || true
  fi

  log "data/ 병합 추출"
  local staging
  staging=$(mktemp -d)
  sudo tar xzf "$tgz" -C "$staging"
  if [[ -d "$staging/data" ]]; then
    sudo mkdir -p "${PROJECT_DIR}/data"
    [[ -d "$staging/data/seeds" ]] && sudo rsync -a "$staging/data/seeds/" "${PROJECT_DIR}/data/seeds/"
    [[ -f "$staging/data/raw_manifest.json" ]] && sudo cp -a "$staging/data/raw_manifest.json" "${PROJECT_DIR}/data/"
    [[ -f "$staging/data/manifest.txt" ]] && sudo cp -a "$staging/data/manifest.txt" "${PROJECT_DIR}/data/"
    if [[ -d "$staging/data/raw" ]] && [[ -n "$(ls -A "$staging/data/raw" 2>/dev/null || true)" ]]; then
      sudo mkdir -p "${PROJECT_DIR}/data/raw"
      sudo rsync -a --delete "$staging/data/raw/" "${PROJECT_DIR}/data/raw/"
    fi
    if [[ -d "$staging/data/backups" ]]; then
      sudo mkdir -p "${PROJECT_DIR}/data/backups"
      sudo rsync -a "$staging/data/backups/" "${PROJECT_DIR}/data/backups/"
    fi
  fi
  sudo rm -rf "$staging"
  sudo chown -R "$OWNER" "${PROJECT_DIR}/data" 2>/dev/null || true

  local latest_sql
  latest_sql=$(ls -1t "${PROJECT_DIR}/data/backups/"*.sql.gz 2>/dev/null | head -1 || true)
  if [[ -n "$latest_sql" ]]; then
    if $RESTORE_PG; then
      log "PG 복원 예약: $(basename "$latest_sql") (api 기동 전 · schema drop)"
      echo "$latest_sql" > "${PROJECT_DIR}/data/backups/.pending_restore"
    else
      info "PG 덤프 발견: $(basename "$latest_sql")"
      info "  깨끗이 복원: in.sh --data --restore-pg -y"
      info "  (alembic 스키마 위에 얹지 말고 --restore-pg 사용)"
    fi
  else
    info "파일 코퍼스만 배포됨 — 검색 인덱스는 --pg-only 덤프 또는 ingest/embed 필요"
  fi

  mv "$tgz" "$TEMP_DIR/" 2>/dev/null || true
  echo "$DATA_AVAIL" > "$DATA_DEPLOYED_FILE"
  log "✅ data ${DATA_AVAIL}"
}

deploy_model() {
  banner "model  ${MODEL_AVAIL}"
  [[ -d "$PROJECT_DIR" ]] || { err "프로젝트 없음"; exit 1; }
  local tgz="$REAL_HOME/citec-kb-model.tar.gz"
  [[ -f "$tgz" ]] || { err "없음: $tgz"; exit 1; }
  cd "$PROJECT_DIR"
  sudo rm -rf models/
  sudo tar xzf "$tgz"
  # refuse broken symlink-only extract
  if [[ -L models/hub ]] && [[ ! -d models/hub ]]; then
    err "models/hub 가 깨진 심볼릭 링크입니다 — out.sh 모델 패키징을 재실행하세요"
    exit 1
  fi
  if [[ ! -d models/hub ]]; then
    err "models/hub 없음 — 잘못된 model 번들"
    exit 1
  fi
  sudo chown -R "$OWNER" models/ 2>/dev/null || true
  local model_name
  model_name=$(cat models/model.name 2>/dev/null || echo "$MODEL_AVAIL")

  if [[ -f .env ]]; then
    if grep -q '^MODELS_HOST_DIR=' .env; then
      sudo sed -i "s|^MODELS_HOST_DIR=.*|MODELS_HOST_DIR=${PROJECT_DIR}/models|" .env
    else
      echo "MODELS_HOST_DIR=${PROJECT_DIR}/models" | sudo tee -a .env >/dev/null
    fi
    if ! grep -q '^TRANSFORMERS_OFFLINE=' .env; then
      echo "TRANSFORMERS_OFFLINE=1" | sudo tee -a .env >/dev/null
    fi
    if ! grep -q '^HF_HUB_OFFLINE=' .env; then
      echo "HF_HUB_OFFLINE=1" | sudo tee -a .env >/dev/null
    fi
  fi

  mv "$tgz" "$TEMP_DIR/" 2>/dev/null || true
  rm -f "$REAL_HOME/citec-kb-model.tar.gz.name"
  echo "$model_name" > "$MODEL_DEPLOYED_FILE"
  log "✅ model ${model_name}"
}

find_pg_dump() {
  # prefer pending, else newest sql.gz under data/backups
  local pending="${PROJECT_DIR}/data/backups/.pending_restore"
  local sql=""
  if [[ -f "$pending" ]]; then
    sql=$(cat "$pending" | tr -d '\r\n')
    [[ -f "$sql" ]] && { echo "$sql"; return; }
  fi
  ls -1t "${PROJECT_DIR}/data/backups/"*.sql.gz 2>/dev/null | head -1 || true
}

pg_cid() {
  (cd "${PROJECT_DIR}" && sudo docker compose ps -q postgres | head -1)
}

restore_pg_clean() {
  # Clean restore: postgres only → DROP SCHEMA → psql dump → verify counts.
  # Must run BEFORE api (alembic) starts, otherwise CREATE/FK errors.
  #
  # Large dumps (embeddings + HNSW, ~200MB+ gzip) take many minutes.
  # Quiet mode + docker exec -i avoids flooding the SSH TTY (which looks like a hang).
  local sql="$1"
  [[ -f "$sql" ]] || { err "덤프 없음: $sql"; exit 1; }

  local sz
  sz=$(du -sh "$sql" 2>/dev/null | cut -f1 || echo "?")
  banner "Postgres 깨끗이 복원  $(basename "$sql") (${sz})"
  info "순서: postgres only → DROP SCHEMA → quiet restore → 건수 검증 → 전체 up"
  warn "기존 DB 데이터는 삭제됩니다"
  warn "대용량 덤프는 10~40분 걸릴 수 있습니다. 출력을 최소화합니다 — 멈춰 있어도 정상일 수 있음."

  start_postgres_only
  cd "${PROJECT_DIR}"

  local cid
  cid=$(pg_cid)
  [[ -n "$cid" ]] || { err "postgres 컨테이너 ID 없음"; exit 1; }

  log "[1/4] public 스키마 초기화"
  sudo docker exec -i "$cid" psql -U citec -d citec_knowledge -v ON_ERROR_STOP=1 -q <<'SQL'
DROP SCHEMA IF EXISTS public CASCADE;
CREATE SCHEMA public;
GRANT ALL ON SCHEMA public TO citec;
GRANT ALL ON SCHEMA public TO public;
-- speed up bulk load / index build during restore
SET maintenance_work_mem = '512MB';
SQL

  log "[2/4] dump 적용 (quiet · ON_ERROR_STOP) — 수 분~수십 분 소요 가능"
  info "진행: 30초마다 heartbeat. 다른 터미널: sudo docker logs --tail 20 citec-kb-postgres-1"
  info "  (embeddings vacuum/checkpoint 메시지는 복원 중 정상)"

  local errf statusf
  errf="${PROJECT_DIR}/data/backups/restore-errors-$(date +%Y%m%d_%H%M%S).log"
  statusf="${PROJECT_DIR}/data/backups/.restore_status"
  mkdir -p "${PROJECT_DIR}/data/backups"
  : > "$errf"
  echo "running $(date -Iseconds) dump=$(basename "$sql")" > "$statusf"

  # Heartbeat: must query pg_stat_activity (bare "state" is invalid → ERROR spam)
  local hb_pid=""
  (
    n=0
    while true; do
      sleep 30
      n=$((n + 30))
      act=$(sudo docker exec "$cid" psql -U citec -d citec_knowledge -t -A -c \
        "SELECT coalesce(
           (SELECT left(coalesce(state,'?')||':'||coalesce(wait_event_type,''), 40)
            FROM pg_stat_activity
            WHERE datname = current_database()
              AND pid <> pg_backend_pid()
              AND state IS NOT NULL
            ORDER BY query_start NULLS LAST
            LIMIT 1),
           'idle');" 2>/dev/null | tr -d '\r' | head -c 48 || echo "ok")
      echo "[$(date '+%H:%M:%S')] restore 진행 중… ${n}s  pg=${act}"
    done
  ) &
  hb_pid=$!

  # -q + discard stdout: dump contains SELECT setval() which still prints tables without full quiet
  # stderr → errf only for real ERROR/FATAL
  set +e
  gunzip -c "$sql" | sudo docker exec -i "$cid" \
    psql -U citec -d citec_knowledge \
      -v ON_ERROR_STOP=1 \
      -q \
      -o /dev/null \
    2>>"$errf"
  local rc=$?
  set -e

  kill "$hb_pid" 2>/dev/null || true
  wait "$hb_pid" 2>/dev/null || true

  local err_n=0
  if [[ -s "$errf" ]]; then
    err_n=$(grep -cE '^ERROR:|^FATAL:|ERROR:  ' "$errf" 2>/dev/null || true)
    err_n=${err_n//$'\n'/}
    err_n=${err_n:-0}
  fi
  if [[ "$rc" -ne 0 ]] || [[ "$err_n" -gt 0 ]]; then
    [[ -s "$errf" ]] && tail -50 "$errf" || true
    echo "failed $(date -Iseconds) rc=$rc errors=$err_n" > "$statusf"
    err "PG 복원 실패 (exit=$rc ERROR=${err_n}). 로그: $errf"
    exit 1
  fi
  echo "ok $(date -Iseconds)" > "$statusf"
  log "psql 완료 (exit=0, ERROR=0)"

  log "[3/4] 건수 검증"
  local counts docs chunks emb
  counts=$(sudo docker exec "$cid" psql -U citec -d citec_knowledge -t -A -c \
    "SELECT (SELECT count(*) FROM documents)||' '||
            (SELECT count(*) FROM chunks WHERE is_active)||' '||
            (SELECT count(*) FROM embeddings);" 2>/dev/null | tr -d '\r' | head -1)
  docs=$(echo "$counts" | awk '{print $1}')
  chunks=$(echo "$counts" | awk '{print $2}')
  emb=$(echo "$counts" | awk '{print $3}')
  docs=${docs:-0}; chunks=${chunks:-0}; emb=${emb:-0}
  log "documents=$docs  chunks=$chunks  embeddings=$emb"
  if [[ "$docs" -lt 100 ]]; then
    err "documents=$docs — 복원이 비정상적으로 작습니다"
    exit 1
  fi
  if [[ "$chunks" -lt 100 ]] || [[ "$emb" -lt 100 ]]; then
    err "chunks/embeddings 부족 (chunks=$chunks emb=$emb) — 검색 불가 상태"
    exit 1
  fi
  log "✅ PG 복원·검증 완료"

  rm -f "${PROJECT_DIR}/data/backups/.pending_restore"
  echo "$docs $chunks $emb" > "${PROJECT_DIR}/data/backups/.last_restore_counts"

  log "[4/4] 전체 스택 기동"
  start_all_containers true
}

$DEPLOY_SOURCE && deploy_source
$DEPLOY_DOCKER && deploy_docker
$DEPLOY_DOCKER_MCP && deploy_docker_mcp
$DEPLOY_DOCKER_KC && deploy_docker_keycloak
$DEPLOY_DATA && deploy_data
$DEPLOY_MODEL && deploy_model

# PG restore: if --restore-pg, find dump (from this deploy or already on disk)
DO_CLEAN_RESTORE=false
RESTORE_SQL=""
if $RESTORE_PG; then
  RESTORE_SQL=$(find_pg_dump)
  if [[ -z "$RESTORE_SQL" || ! -f "$RESTORE_SQL" ]]; then
    err "--restore-pg 인데 덤프(*.sql.gz) 없음. out.sh --pg-only 후 data 번들을 먼저 두세요."
    exit 1
  fi
  DO_CLEAN_RESTORE=true
fi

if $DO_CLEAN_RESTORE; then
  restore_pg_clean "$RESTORE_SQL"
else
  start_all_containers
fi

echo ""
echo "════════════════════════════════════════════════"
echo "  ✅ citec-kb 배포 완료"
$DEPLOY_SOURCE && echo "  code:       $SOURCE_AVAIL"
$DEPLOY_DOCKER && echo "  docker:     $DOCKER_AVAIL"
$DEPLOY_DOCKER_MCP && echo "  docker-mcp: $DOCKER_MCP_AVAIL"
$DEPLOY_DOCKER_KC && echo "  keycloak:   $DOCKER_KC_AVAIL"
$DEPLOY_DATA && echo "  data:       $DATA_AVAIL"
$DEPLOY_MODEL && echo "  model:      $MODEL_AVAIL"
$DO_CLEAN_RESTORE && echo "  pg-restore: $(basename "$RESTORE_SQL") (clean)"
echo "  이력: cat ~/bin/.citec_kb_*_deployed"
echo "  웹:   http://localhost:8572"
echo "  API:  http://localhost:8573/v1/health"
echo "  MCP:  http://localhost:8577"
echo "════════════════════════════════════════════════"
