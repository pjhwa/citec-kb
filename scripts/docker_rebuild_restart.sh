#!/usr/bin/env bash
# docker_rebuild_restart.sh — citec-kb 스택 종료 → (재)빌드 → 재기동
#
# 기본 동작: docker compose down → build → up -d → 헬스/포트 요약
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

COMPOSE=(docker compose)
PROFILE_ARGS=()
SERVICES=()
NO_CACHE=0
PULL=0
REMOVE_ORPHANS=1
WITH_KEYCLOAK=0
DOWN_VOLUMES=0
BUILD_ONLY=0
UP_ONLY=0
DOWN_ONLY=0
SKIP_DOWN=0
WAIT_HEALTH=1
WAIT_TIMEOUT=180
QUIET=0

usage() {
  cat <<'EOF'
citec-kb Docker 스택 일괄 종료 · 재빌드 · 재기동

USAGE
  scripts/docker_rebuild_restart.sh [options] [service ...]

DESCRIPTION
  레포 루트에서 docker compose 로 스택을 정리한 뒤 이미지를 다시 빌드하고
  기동합니다. 인자를 주지 않으면 compose 기본 서비스 전체
  (postgres, redis, api, worker, web, mcp) 대상입니다.
  keycloak 은 기본 제외(profile) — --with-keycloak 으로 포함.

OPTIONS
  -h, --help              이 도움말
  --with-keycloak         keycloak profile 포함 (호스트 8576)
  --no-cache              docker compose build --no-cache
  --pull                  베이스 이미지 pull 후 빌드 (build --pull)
  --volumes               down 시 named volume 도 삭제 (⚠ pgdata 데이터 삭제)
  --no-remove-orphans     down 시 orphan 컨테이너 제거 안 함
  --skip-down             down 생략 (이미 일부만 재빌드할 때)
  --build-only            down + build 만 (up 안 함)
  --up-only               build 없이 up -d 만
  --down-only             down 만
  --no-wait               기동 후 health 대기 생략
  --wait-timeout SEC      health 대기 초 (기본 180)
  -q, --quiet             진행 메시지 축소

SERVICES (선택, 하나 이상)
  postgres  redis  api  worker  web  mcp  keycloak
  예:  api mcp 만 재빌드·재기동
       scripts/docker_rebuild_restart.sh api mcp

EXAMPLES
  # 전체 스택 재시작 (가장 흔함)
  scripts/docker_rebuild_restart.sh

  # 캐시 없이 클린 재빌드
  scripts/docker_rebuild_restart.sh --no-cache

  # API + MCP 만
  scripts/docker_rebuild_restart.sh api mcp

  # Keycloak 포함 전체
  scripts/docker_rebuild_restart.sh --with-keycloak

  # 종료만 / 기동만
  scripts/docker_rebuild_restart.sh --down-only
  scripts/docker_rebuild_restart.sh --up-only

PORTS (기본 할당)
  8572 web · 8573 api · 8574 postgres · 8575 redis
  8576 keycloak(optional) · 8577 mcp

ENV
  COMPOSE_FILE   docker compose -f 추가 파일 (선택)
  RAW_HOST_DIR / MODELS_HOST_DIR  등 compose 와 동일

EXIT
  0 성공 · 비0 실패 (build/up/health 타임아웃)
EOF
}

log() {
  if [[ "$QUIET" -eq 0 ]]; then
    echo "[$(date '+%H:%M:%S')] $*"
  fi
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "'$1' 명령을 찾을 수 없습니다."
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -h|--help)
        usage
        exit 0
        ;;
      --with-keycloak)
        WITH_KEYCLOAK=1
        shift
        ;;
      --no-cache)
        NO_CACHE=1
        shift
        ;;
      --pull)
        PULL=1
        shift
        ;;
      --volumes)
        DOWN_VOLUMES=1
        shift
        ;;
      --no-remove-orphans)
        REMOVE_ORPHANS=0
        shift
        ;;
      --skip-down)
        SKIP_DOWN=1
        shift
        ;;
      --build-only)
        BUILD_ONLY=1
        shift
        ;;
      --up-only)
        UP_ONLY=1
        shift
        ;;
      --down-only)
        DOWN_ONLY=1
        shift
        ;;
      --no-wait)
        WAIT_HEALTH=0
        shift
        ;;
      --wait-timeout)
        WAIT_TIMEOUT="${2:-}"
        [[ -n "$WAIT_TIMEOUT" ]] || die "--wait-timeout 값 필요"
        shift 2
        ;;
      -q|--quiet)
        QUIET=1
        shift
        ;;
      --)
        shift
        SERVICES+=("$@")
        break
        ;;
      -*)
        die "알 수 없는 옵션: $1  ( --help 참고 )"
        ;;
      *)
        SERVICES+=("$1")
        shift
        ;;
    esac
  done

  local modes=0
  [[ "$BUILD_ONLY" -eq 1 ]] && modes=$((modes + 1))
  [[ "$UP_ONLY" -eq 1 ]] && modes=$((modes + 1))
  [[ "$DOWN_ONLY" -eq 1 ]] && modes=$((modes + 1))
  if [[ "$modes" -gt 1 ]]; then
    die "--build-only / --up-only / --down-only 는 하나만 지정"
  fi
}

compose() {
  "${COMPOSE[@]}" "${PROFILE_ARGS[@]}" "$@"
}

do_down() {
  local args=(down)
  if [[ "$REMOVE_ORPHANS" -eq 1 ]]; then
    args+=(--remove-orphans)
  fi
  if [[ "$DOWN_VOLUMES" -eq 1 ]]; then
    log "⚠ named volume 포함 삭제 (--volumes)"
    args+=(--volumes)
  fi
  # service 지정 시에도 compose down 은 프로젝트 전체 종료가 일반적.
  # 부분 재시작은 skip-down + up 조합을 권장.
  if [[ ${#SERVICES[@]} -gt 0 && "$SKIP_DOWN" -eq 0 ]]; then
    log "서비스 지정됨 → 해당 컨테이너 stop/rm 후 재생성 (전체 down 아님)"
    compose stop "${SERVICES[@]}" 2>/dev/null || true
    compose rm -f "${SERVICES[@]}" 2>/dev/null || true
    return
  fi
  log "compose down ${args[*]}"
  compose "${args[@]}"
}

do_build() {
  local args=(build)
  if [[ "$NO_CACHE" -eq 1 ]]; then
    args+=(--no-cache)
  fi
  if [[ "$PULL" -eq 1 ]]; then
    args+=(--pull)
  fi
  if [[ ${#SERVICES[@]} -gt 0 ]]; then
    log "compose build ${args[*]} ${SERVICES[*]}"
    compose "${args[@]}" "${SERVICES[@]}"
  else
    log "compose build ${args[*]}"
    compose "${args[@]}"
  fi
}

do_up() {
  local args=(up -d --remove-orphans)
  if [[ ${#SERVICES[@]} -gt 0 ]]; then
    log "compose up ${args[*]} ${SERVICES[*]}"
    compose "${args[@]}" "${SERVICES[@]}"
  else
    log "compose up ${args[*]}"
    compose "${args[@]}"
  fi
}

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
  [[ "$WAIT_HEALTH" -eq 1 ]] || return 0
  log "헬스 대기 (timeout=${WAIT_TIMEOUT}s)…"
  local failed=0

  # always try core endpoints if those services are in scope
  local want_api=1 want_web=1 want_mcp=1
  if [[ ${#SERVICES[@]} -gt 0 ]]; then
    want_api=0 want_web=0 want_mcp=0
    local s
    for s in "${SERVICES[@]}"; do
      case "$s" in
        api) want_api=1 ;;
        web) want_web=1 ;;
        mcp) want_mcp=1 ;;
      esac
    done
  fi

  if [[ "$want_api" -eq 1 ]]; then
    wait_http "http://127.0.0.1:8573/v1/health" "api" || failed=1
    wait_http "http://127.0.0.1:8573/api/health" "api/compat" || true
  fi
  if [[ "$want_web" -eq 1 ]]; then
    wait_http "http://127.0.0.1:8572/" "web" || failed=1
  fi
  if [[ "$want_mcp" -eq 1 ]]; then
    # MCP may only open TCP until first client; port listen is enough
    local deadline=$((SECONDS + WAIT_TIMEOUT))
    local ok=0
    while (( SECONDS < deadline )); do
      if (echo >/dev/tcp/127.0.0.1/8577) >/dev/null 2>&1; then
        log "  ✓ mcp   port 8577 open"
        ok=1
        break
      fi
      sleep 2
    done
    [[ "$ok" -eq 1 ]] || { log "  ✗ mcp   port 8577 timeout"; failed=1; }
  fi

  if [[ "$WITH_KEYCLOAK" -eq 1 ]]; then
    wait_http "http://127.0.0.1:8576/" "keycloak" || failed=1
  fi

  return "$failed"
}

print_summary() {
  log "---- compose ps ----"
  compose ps || true
  log "---- endpoints ----"
  cat <<EOF
  web UI     http://localhost:8572
  api        http://localhost:8573/v1/health
  api docs   http://localhost:8573/docs
  external   http://localhost:8573/v1/external/catalog
  mcp        http://localhost:8577  (Claude streamable-http)
  postgres   localhost:8574
  redis      localhost:8575
EOF
  if [[ "$WITH_KEYCLOAK" -eq 1 ]]; then
    echo "  keycloak  http://localhost:8576"
  fi
}

main() {
  parse_args "$@"
  need_cmd docker
  docker compose version >/dev/null 2>&1 || die "docker compose 플러그인이 필요합니다."

  if [[ "$WITH_KEYCLOAK" -eq 1 ]]; then
    PROFILE_ARGS+=(--profile keycloak)
  fi

  # keycloak only makes sense with profile
  if [[ ${#SERVICES[@]} -gt 0 ]]; then
    local s
    for s in "${SERVICES[@]}"; do
      if [[ "$s" == "keycloak" && "$WITH_KEYCLOAK" -eq 0 ]]; then
        PROFILE_ARGS+=(--profile keycloak)
        WITH_KEYCLOAK=1
      fi
    done
  fi

  log "ROOT=$ROOT"
  log "services=${SERVICES[*]:-(all default)}"

  if [[ "$UP_ONLY" -eq 1 ]]; then
    do_up
    wait_health || die "health check 실패"
    print_summary
    log "완료 (up-only)"
    return 0
  fi

  if [[ "$DOWN_ONLY" -eq 1 ]]; then
    do_down
    log "완료 (down-only)"
    return 0
  fi

  if [[ "$SKIP_DOWN" -eq 0 ]]; then
    do_down
  else
    log "down 생략 (--skip-down)"
  fi

  if [[ "$UP_ONLY" -eq 0 ]]; then
    do_build
  fi

  if [[ "$BUILD_ONLY" -eq 1 ]]; then
    log "완료 (build-only)"
    return 0
  fi

  do_up
  wait_health || die "health check 실패 — logs: docker compose logs --tail 80"
  print_summary
  log "완료"
}

main "$@"
