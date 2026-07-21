#!/usr/bin/env bash
# in.sh — 운용(폐쇄망) 시스템: citec-kb 배포 번들 적용
#
# 번들 파일은 홈 디렉터리(~)에 둔 뒤 실행:
#   citec-kb-code-vN.tar.gz
#   citec-kb-docker-vN.tar.gz
#   citec-kb-docker-mcp-vN.tar.gz
#   citec-kb-data-dN.tar.gz
#   citec-kb-model.tar.gz
#
# 동일 버전은 건너뜀 (--force 로 재적용). 변경된 번들만 배포.
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
  --data / --no-data
  --model / --no-model

동작:
  --force, -f           버전 동일해도 강제 재배포
  --no-restart          컨테이너 재시작 생략
  --yes, -y             확인 프롬프트 생략
  --dry-run, -n         계획만 출력

버전 직접 지정:
  --code-ver VER        예: v3
  --docker-ver VER
  --docker-mcp-ver VER
  --data-ver VER        예: d2

경로:
  --home DIR            번들 탐색·프로젝트 홈 (기본: 실제 사용자 홈)
  --project DIR         프로젝트 경로 (기본: $HOME/citec-kb)

버전 추적 (~/bin/):
  .citec_kb_code_deployed
  .citec_kb_docker_deployed
  .citec_kb_docker_mcp_deployed
  .citec_kb_data_deployed
  .citec_kb_model_deployed

예시:
  in.sh -y                          자동 감지 전체
  in.sh --code -y                   코드만 (일상)
  in.sh --docker-mcp -y             MCP 이미지만
  in.sh --code --docker -y          의존성 변경 후
  in.sh --data -y                   코퍼스 갱신
  in.sh --force --code -y           코드 강제 재적용
EOF
}

# sudo 시 호출 사용자 홈 유지
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

SOURCE_STATE=""; DOCKER_STATE=""; DOCKER_MCP_STATE=""; DATA_STATE=""; MODEL_STATE=""
CODE_VER_ARG=""; DOCKER_VER_ARG=""; DOCKER_MCP_VER_ARG=""; DATA_VER_ARG=""
FORCE=false; NO_RESTART=false; YES=false; DRY_RUN=false
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
    --force|-f) FORCE=true; shift ;;
    --no-restart) NO_RESTART=true; shift ;;
    --yes|-y) YES=true; shift ;;
    --dry-run|-n) DRY_RUN=true; shift ;;
    --code-ver) CODE_VER_ARG="${2:-}"; shift 2 ;;
    --docker-ver) DOCKER_VER_ARG="${2:-}"; shift 2 ;;
    --docker-mcp-ver) DOCKER_MCP_VER_ARG="${2:-}"; shift 2 ;;
    --data-ver) DATA_VER_ARG="${2:-}"; shift 2 ;;
    --home) REAL_HOME="${2:-}"; shift 2 ;;
    --project) PROJECT_DIR="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) err "알 수 없는 옵션: $1"; usage; exit 1 ;;
  esac
done

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

TEMP_DIR="$REAL_HOME/temp"
DEPLOY_TRACK_DIR="$REAL_HOME/bin"
mkdir -p "$TEMP_DIR" "$DEPLOY_TRACK_DIR"

find_latest() {
  # $1 glob pattern e.g. citec-kb-code-v*.tar.gz, $2 prefix letter v or d
  find "$REAL_HOME" -maxdepth 1 -name "$1" 2>/dev/null \
    | grep -Eo "${2}[0-9]+" | sort -t"${2}" -k2 -n | tail -1 || true
}
normalize_ver() {
  local prefix="$1" val="$2"
  val="${val#${prefix}}"
  echo "${prefix}${val}"
}

if [[ -n "$CODE_VER_ARG" ]]; then
  SOURCE_AVAIL=$(normalize_ver "v" "$CODE_VER_ARG")
else
  SOURCE_AVAIL=$(find_latest 'citec-kb-code-v*.tar.gz' 'v')
fi
if [[ -n "$DOCKER_VER_ARG" ]]; then
  DOCKER_AVAIL=$(normalize_ver "v" "$DOCKER_VER_ARG")
else
  DOCKER_AVAIL=$(find_latest 'citec-kb-docker-v*.tar.gz' 'v')
fi
if [[ -n "$DOCKER_MCP_VER_ARG" ]]; then
  DOCKER_MCP_AVAIL=$(normalize_ver "v" "$DOCKER_MCP_VER_ARG")
else
  DOCKER_MCP_AVAIL=$(find_latest 'citec-kb-docker-mcp-v*.tar.gz' 'v')
fi
if [[ -n "$DATA_VER_ARG" ]]; then
  DATA_AVAIL=$(normalize_ver "d" "$DATA_VER_ARG")
else
  DATA_AVAIL=$(find_latest 'citec-kb-data-d*.tar.gz' 'd')
fi
MODEL_AVAIL=""
if [[ -f "$REAL_HOME/citec-kb-model.tar.gz" ]]; then
  MODEL_AVAIL=$(cat "$REAL_HOME/citec-kb-model.tar.gz.name" 2>/dev/null | tr -d '\n\r' || echo "model")
fi

SOURCE_DEPLOYED=$(cat "$CODE_DEPLOYED_FILE" 2>/dev/null || echo "")
DOCKER_DEPLOYED=$(cat "$DOCKER_DEPLOYED_FILE" 2>/dev/null || echo "")
DOCKER_MCP_DEPLOYED=$(cat "$DOCKER_MCP_DEPLOYED_FILE" 2>/dev/null || echo "")
DATA_DEPLOYED=$(cat "$DATA_DEPLOYED_FILE" 2>/dev/null || echo "")
MODEL_DEPLOYED=$(cat "$MODEL_DEPLOYED_FILE" 2>/dev/null || echo "")

should_deploy() {
  # do_flag avail deployed
  [[ "$1" == "false" ]] && return 1
  [[ -z "$2" ]] && return 1
  $FORCE && return 0
  [[ "$2" != "$3" ]] && return 0
  return 1
}

DEPLOY_SOURCE=false; DEPLOY_DOCKER=false; DEPLOY_DOCKER_MCP=false
DEPLOY_DATA=false; DEPLOY_MODEL=false
should_deploy "$DO_SOURCE" "$SOURCE_AVAIL" "$SOURCE_DEPLOYED" && DEPLOY_SOURCE=true
should_deploy "$DO_DOCKER" "$DOCKER_AVAIL" "$DOCKER_DEPLOYED" && DEPLOY_DOCKER=true
should_deploy "$DO_DOCKER_MCP" "$DOCKER_MCP_AVAIL" "$DOCKER_MCP_DEPLOYED" && DEPLOY_DOCKER_MCP=true
should_deploy "$DO_DATA" "$DATA_AVAIL" "$DATA_DEPLOYED" && DEPLOY_DATA=true
should_deploy "$DO_MODEL" "$MODEL_AVAIL" "$MODEL_DEPLOYED" && DEPLOY_MODEL=true

$NO_MODEL_EXPLICIT && warn "--no-model: 모델 미배포 시 벡터 검색 불가 가능"

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
print_status "소스코드" "$SOURCE_AVAIL" "$SOURCE_DEPLOYED" "$DO_SOURCE" "$DEPLOY_SOURCE"
print_status "Docker" "$DOCKER_AVAIL" "$DOCKER_DEPLOYED" "$DO_DOCKER" "$DEPLOY_DOCKER"
print_status "Docker(mcp)" "$DOCKER_MCP_AVAIL" "$DOCKER_MCP_DEPLOYED" "$DO_DOCKER_MCP" "$DEPLOY_DOCKER_MCP"
print_status "데이터" "$DATA_AVAIL" "$DATA_DEPLOYED" "$DO_DATA" "$DEPLOY_DATA"
print_status "모델" "$MODEL_AVAIL" "$MODEL_DEPLOYED" "$DO_MODEL" "$DEPLOY_MODEL"
echo ""

if ! $DEPLOY_SOURCE && ! $DEPLOY_DOCKER && ! $DEPLOY_DOCKER_MCP && ! $DEPLOY_DATA && ! $DEPLOY_MODEL; then
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
    (cd "${PROJECT_DIR}" && sudo docker compose down --remove-orphans) || true
  fi
}
start_all_containers() {
  $NO_RESTART && { warn "--no-restart"; return; }
  if $CONTAINERS_WERE_RUNNING || $DEPLOY_SOURCE || $DEPLOY_DOCKER || $DEPLOY_DOCKER_MCP; then
    [[ -f "${PROJECT_DIR}/.env" ]] || {
      if [[ -f "${PROJECT_DIR}/.env.example" ]]; then
        warn ".env 없음 — .env.example 복사 (키 수동 설정 필요)"
        sudo cp "${PROJECT_DIR}/.env.example" "${PROJECT_DIR}/.env"
        sudo chown "$OWNER" "${PROJECT_DIR}/.env"
      else
        err ".env 필요: ${PROJECT_DIR}/.env"; exit 1
      fi
    }
    cd "${PROJECT_DIR}"
    # models path default on prod
    if ! grep -q '^MODELS_HOST_DIR=' .env 2>/dev/null; then
      echo "MODELS_HOST_DIR=${PROJECT_DIR}/models" | sudo tee -a .env >/dev/null || true
    fi
    sudo docker compose up -d
    log "컨테이너 시작"
    sudo docker compose ps 2>/dev/null || true
  fi
}

stop_all_containers

deploy_source() {
  banner "소스코드  ${SOURCE_AVAIL}"
  local tgz="$REAL_HOME/citec-kb-code-${SOURCE_AVAIL}.tar.gz"
  [[ -f "$tgz" ]] || { err "없음: $tgz"; exit 1; }

  local insh_self models_json
  insh_self=$(cat "${BASH_SOURCE[0]}" 2>/dev/null || true)
  models_json=$(sudo cat "${PROJECT_DIR}/config/models.json" 2>/dev/null || true)
  local env_bak=""
  [[ -f "${PROJECT_DIR}/.env" ]] && env_bak=$(sudo cat "${PROJECT_DIR}/.env" 2>/dev/null || true)

  log "[1/3] 추출"
  sudo tar xzf "$tgz" -C "$REAL_HOME" --no-same-owner

  # self-preserve in.sh / models.json / .env
  if [[ -n "$insh_self" ]]; then
    echo "$insh_self" | sudo tee "${PROJECT_DIR}/scripts/in.sh" >/dev/null
    sudo chmod +x "${PROJECT_DIR}/scripts/in.sh"
  fi
  if [[ -n "$models_json" ]]; then
    echo "$models_json" | sudo tee "${PROJECT_DIR}/config/models.json" >/dev/null
    log "config/models.json 보존"
  fi
  if [[ -n "$env_bak" ]]; then
    echo "$env_bak" | sudo tee "${PROJECT_DIR}/.env" >/dev/null
    log ".env 보존"
  fi

  sudo chown -R "$OWNER" "${PROJECT_DIR}" 2>/dev/null || true
  # api container uid 1000 read apps/
  for d in apps scripts config mcp-server; do
    [[ -d "${PROJECT_DIR}/$d" ]] && sudo chmod -R a+rX "${PROJECT_DIR}/$d" || true
  done
  mkdir -p "${PROJECT_DIR}/data/raw" "${PROJECT_DIR}/data/seeds" "${PROJECT_DIR}/logs" "${PROJECT_DIR}/models"

  log "[2/3] 번들 보관"
  mv "$tgz" "$TEMP_DIR/" 2>/dev/null || true
  echo "$SOURCE_AVAIL" > "$CODE_DEPLOYED_FILE"
  log "[3/3] ✅ 코드 ${SOURCE_AVAIL} (마운트 반영 — 재시작 후 적용)"
  info "Dockerfile/requirements 변경 시 docker 번들도 배포하세요"
}

deploy_docker() {
  banner "Docker(api/worker/base)  ${DOCKER_AVAIL}"
  local tgz="$REAL_HOME/citec-kb-docker-${DOCKER_AVAIL}.tar.gz"
  [[ -f "$tgz" ]] || { err "없음: $tgz"; exit 1; }
  log "docker load"
  gunzip -c "$tgz" | sudo docker load
  mv "$tgz" "$TEMP_DIR/" 2>/dev/null || true
  echo "$DOCKER_AVAIL" > "$DOCKER_DEPLOYED_FILE"
  log "✅ Docker ${DOCKER_AVAIL}"
}

deploy_docker_mcp() {
  banner "Docker(mcp)  ${DOCKER_MCP_AVAIL}"
  local tgz="$REAL_HOME/citec-kb-docker-mcp-${DOCKER_MCP_AVAIL}.tar.gz"
  [[ -f "$tgz" ]] || { err "없음: $tgz"; exit 1; }
  gunzip -c "$tgz" | sudo docker load
  mv "$tgz" "$TEMP_DIR/" 2>/dev/null || true
  echo "$DOCKER_MCP_AVAIL" > "$DOCKER_MCP_DEPLOYED_FILE"
  log "✅ Docker(mcp) ${DOCKER_MCP_AVAIL}"
}

deploy_data() {
  banner "데이터  ${DATA_AVAIL}"
  [[ -d "$PROJECT_DIR" ]] || { err "프로젝트 없음 — 먼저 --code"; exit 1; }
  local tgz="$REAL_HOME/citec-kb-data-${DATA_AVAIL}.tar.gz"
  [[ -f "$tgz" ]] || { err "없음: $tgz"; exit 1; }

  local snap_dir="${PROJECT_DIR}/data/backups"
  mkdir -p "$snap_dir"
  if [[ -d "${PROJECT_DIR}/data/raw" ]]; then
    log "롤백 스냅샷 (raw 메타)"
    sudo tar -czf "${snap_dir}/pre-data-$(date +%Y%m%d_%H%M%S).tar.gz" \
      -C "${PROJECT_DIR}" data/seeds data/raw_manifest.json 2>/dev/null || true
  fi

  log "data/ 병합 추출"
  local staging
  staging=$(mktemp -d)
  sudo tar xzf "$tgz" -C "$staging"
  if [[ -d "$staging/data" ]]; then
    sudo mkdir -p "${PROJECT_DIR}/data"
    # seeds always
    [[ -d "$staging/data/seeds" ]] && sudo rsync -a "$staging/data/seeds/" "${PROJECT_DIR}/data/seeds/"
    [[ -f "$staging/data/raw_manifest.json" ]] && sudo cp -a "$staging/data/raw_manifest.json" "${PROJECT_DIR}/data/"
    if [[ -d "$staging/data/raw" ]] && [[ -n "$(ls -A "$staging/data/raw" 2>/dev/null || true)" ]]; then
      log "raw 교체"
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

  # optional restore sql
  local latest_sql
  latest_sql=$(ls -1t "${PROJECT_DIR}/data/backups/"*.sql.gz 2>/dev/null | head -1 || true)
  if [[ -n "$latest_sql" ]] && [[ -f "${PROJECT_DIR}/docker-compose.yml" ]]; then
    info "PG 덤프 발견: $(basename "$latest_sql") — 수동 복원 예:"
    info "  gunzip -c data/backups/...sql.gz | docker compose exec -T postgres psql -U citec citec_knowledge"
  fi

  mv "$tgz" "$TEMP_DIR/" 2>/dev/null || true
  echo "$DATA_AVAIL" > "$DATA_DEPLOYED_FILE"
  log "✅ 데이터 ${DATA_AVAIL}"
}

deploy_model() {
  banner "모델  ${MODEL_AVAIL}"
  [[ -d "$PROJECT_DIR" ]] || { err "프로젝트 없음"; exit 1; }
  local tgz="$REAL_HOME/citec-kb-model.tar.gz"
  [[ -f "$tgz" ]] || { err "없음: $tgz"; exit 1; }
  cd "$PROJECT_DIR"
  sudo rm -rf models/
  sudo tar xzf "$tgz"
  sudo chown -R "$OWNER" models/ 2>/dev/null || true
  local model_name
  model_name=$(cat models/model.name 2>/dev/null || echo "$MODEL_AVAIL")
  # point compose volume
  if [[ -f .env ]]; then
    if grep -q '^MODELS_HOST_DIR=' .env; then
      sudo sed -i "s|^MODELS_HOST_DIR=.*|MODELS_HOST_DIR=${PROJECT_DIR}/models|" .env
    else
      echo "MODELS_HOST_DIR=${PROJECT_DIR}/models" | sudo tee -a .env >/dev/null
    fi
  fi
  mv "$tgz" "$TEMP_DIR/" 2>/dev/null || true
  rm -f "$REAL_HOME/citec-kb-model.tar.gz.name"
  echo "$model_name" > "$MODEL_DEPLOYED_FILE"
  log "✅ 모델 ${model_name}"
}

$DEPLOY_SOURCE && deploy_source
$DEPLOY_DOCKER && deploy_docker
$DEPLOY_DOCKER_MCP && deploy_docker_mcp
$DEPLOY_DATA && deploy_data
$DEPLOY_MODEL && deploy_model

start_all_containers

echo ""
echo "════════════════════════════════════════════════"
echo "  ✅ citec-kb 배포 완료"
$DEPLOY_SOURCE && echo "  code:       $SOURCE_AVAIL"
$DEPLOY_DOCKER && echo "  docker:     $DOCKER_AVAIL"
$DEPLOY_DOCKER_MCP && echo "  docker-mcp: $DOCKER_MCP_AVAIL"
$DEPLOY_DATA && echo "  data:       $DATA_AVAIL"
$DEPLOY_MODEL && echo "  model:      $MODEL_AVAIL"
echo "  이력: cat ~/bin/.citec_kb_*_deployed"
echo "  헬스: curl -s localhost:8573/v1/health | jq ."
echo "════════════════════════════════════════════════"
