#!/usr/bin/env bash
# backfill_p0b_p1b_metadata.sh — 운용 서버: PR #1/#2에서 코드는 배포됐지만
# 기존 데이터에는 아직 반영 안 된 3가지 메타데이터를 채운다. 전부 **기존
# 도구를 그대로 재사용**한다 — 새 수집기/백필 로직을 새로 만들지 않는다
# (지침 §9 "기존 backfill/inventory 재사용" 원칙).
#
# 대상 (2026-10-01 scripts/collect_prod_diagnostics.sh 실측 기준):
#   docs   — confluence_docs(5,509건)+tech_repo(2,800건): ancestor_ids/
#            source_version 100% 결측. scripts/confluence_sync.sh는 증분이라
#            (lastmodified > since) 이미 있던 페이지는 내용이 안 바뀌면 영영
#            재크롤되지 않는다 — Source.last_sync_at을 NULL로 되돌려 "최초
#            크롤" 상태로 만든 뒤 같은 스크립트를 그대로 돌린다(코드 변경 없음,
#            app.confluence.client.build_incremental_cql이 since=None이면
#            날짜 필터 없이 전체 크롤하는 것을 그대로 이용).
#   map    — confluence_map 28,859/77,446건(37.3%) ancestor_ids 결측(OVE
#            두 페이지 포함). 이미 있는 scripts/map_backfill.sh --from-scratch
#            를 그대로 호출한다.
#   frames — issue_frames 17,632/17,632건(100%) body_hash/extractor_version
#            NULL — P0-C 마이그레이션 설계대로 "최초 1회 재생성 대상". LLM
#            호출 없는 규칙 기반 추출(app.frames.extract)이라 비용이 낮다.
#            app.frames.cli를 지원 두 source_type(support_history,
#            incident_reports) 각각에 대해 실행한다.
#
# 사용법 — 반드시 인자 없이(계획만 확인) 먼저:
#   scripts/backfill_p0b_p1b_metadata.sh                  # 현재 결측 수치만 다시 조회, 아무것도 안 함
#   scripts/backfill_p0b_p1b_metadata.sh --apply frames    # frames만 적용 (가장 싸고 빠름 — 먼저 권장)
#   scripts/backfill_p0b_p1b_metadata.sh --apply map-pilot # confluence_map 작은 공간 1개만 먼저(아래 "파일럿" 참고)
#   scripts/backfill_p0b_p1b_metadata.sh --apply docs      # confluence_docs/tech_repo 전체 재크롤
#   scripts/backfill_p0b_p1b_metadata.sh --apply map       # confluence_map 전체 재크롤(수 시간)
#   scripts/backfill_p0b_p1b_metadata.sh --apply all       # 위 3개 전부(순서: frames → docs → map, map-pilot 제외)
#
# 비용/주의 — 2026-09-28~30 실제 백필 로그(사용자 제공, /tmp/backfill.txt)로
# 실측 검증됨: 크롤은 소스 크기와 무관하게 **정확히 3.40초/페이지**
# (CONFLUENCE_RATE_LIMIT_RPS=0.3의 이론치 3.33초와 거의 일치 — 레이트리밋이
# 거의 전부를 설명하며, 재시도/오류로 인한 추가 지연은 미미했음: 48,586페이지
# 중 오류 4건). 임베딩은 48,584건에 6,987초(≈143.8ms/건, 그 때는 여전히 발췌
# 수준 콘텐츠 — 이번 전체본문 전환 후에는 청크 수가 늘어 더 걸릴 것, 그래서
# map-pilot으로 먼저 재는 것을 권장):
#   - docs: 8,309페이지 × 3.40초 ≈ 7.8시간(현재 0.3rps) / 1.0rps면 ≈ 2.3시간.
#   - map 전체: 77,446페이지 × 3.40초 ≈ 73시간(0.3rps) / 1.0rps면 ≈ 22시간.
#     이미 2026-09-28~30에 11개 공간(48,586페이지, 약 41시간)을 전체
#     재크롤한 이력이 있다 — 그 공간들은 ancestor_ids가 이미 있을 가능성이
#     높으므로, 전체 --from-scratch 전에 scripts/collect_prod_diagnostics.sh의
#     [2b] 쿼리(공간별 결측 분포)로 **정말 남은 범위**를 먼저 확인할 것.
#   - frames: DB 내부 규칙 기반 처리만, 외부 API 호출 없음 — 가장 안전/저비용.
#   - docs/map 둘 다 되돌릴 수 있다(ancestor_ids/source_version/
#     source_modified_at은 추가적 필드라 재크롤해도 본문 재청크 트리거 없음 —
#     app.ingest.adapters.DocumentDraft._HASH_EXCLUDED_METADATA_KEYS 참고).
#     docs의 cursor를 NULL로 되돌리는 것 자체만 유일한 "쓰기"이며, 재크롤이
#     끝나면 last_sync_at이 다시 정상적으로 전진한다.
#
# 레이트리밋 일시 상향(사용자 승인, 공유 계정이라 신중히):
#   CONFLUENCE_RATE_LIMIT_RPS=1.0 scripts/backfill_p0b_p1b_metadata.sh --apply map
#
# 파일럿(map-pilot): 전체본문+전체 공간 적용 전, 가장 작은 공간
# confluence_map_si(670페이지, 2026-09-28 기준 0.63시간 소요)로 먼저 크롤+
# 인제스트+임베딩을 끝까지 돌려 "전체본문 전환 후 실제 임베딩 ms/건"을
# 측정한다. 끝나면 app.ops.dashboard.coverage_gaps 또는 embed 로그의
# elapsed_sec/embedded로 새 ms/건을 계산해 전체 77,446건 규모를 재추정할 것.
#
# 이 스크립트는 각 단계 전에 반드시 한 번 더 y/N 확인을 받는다(-y로 생략 가능).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PG_CONTAINER="${PG_CONTAINER:-}"
PG_USER="${PG_USER:-citec}"
PG_DB="${PG_DB:-citec_knowledge}"
SERVICE="${BACKFILL_SERVICE:-api}"

APPLY=""
YES=false
TARGET="plan"

usage() { sed -n '2,38p' "$0" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) APPLY="apply"; TARGET="${2:?--apply 뒤에 docs|map|frames|all 중 하나를 지정하세요}"; shift 2 ;;
    -y|--yes) YES=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "알 수 없는 인자: $1" >&2; usage >&2; exit 64 ;;
  esac
done

cd "$PROJECT_DIR"

compose() { sudo docker compose "$@"; }

psql_exec() {
  # $1: SQL
  if [[ -n "$PG_CONTAINER" ]]; then
    docker exec -i "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -At -c "$1"
  else
    local cid
    cid="$(compose ps -q postgres)"
    [[ -n "$cid" ]] || { echo "오류: postgres 컨테이너를 찾을 수 없습니다 (PG_CONTAINER로 지정 가능)" >&2; exit 1; }
    docker exec -i "$cid" psql -U "$PG_USER" -d "$PG_DB" -At -c "$1"
  fi
}

confirm() {
  $YES && return 0
  echo -e "\033[1;31m$1 [y/N]\033[0m "
  read -r ans
  [[ "$ans" =~ ^[Yy]$ ]]
}

print_plan() {
  echo "=== 현재 결측 수치 (실시간 재조회) ==="
  echo ""
  echo "-- confluence_docs/tech_repo: ancestor_ids/source_version 결측 --"
  psql_exec "SELECT source_type, COUNT(*) total_active, COUNT(*) FILTER (WHERE NOT (metadata ? 'ancestor_ids')) missing_ancestor_ids, COUNT(*) FILTER (WHERE NOT (metadata ? 'source_version')) missing_source_version FROM documents WHERE status='active' AND source_type IN ('confluence_docs','tech_repo') GROUP BY source_type ORDER BY source_type;" | column -s'|' -t
  echo ""
  echo "-- confluence_map: ancestor_ids 결측 --"
  psql_exec "SELECT COUNT(*) total_active, COUNT(*) FILTER (WHERE NOT (metadata ? 'ancestor_ids')) missing_ancestor_ids FROM documents WHERE status='active' AND source_type='confluence_map';" | column -s'|' -t
  echo ""
  echo "-- confluence_map: 공간(path_l2)별 결측 분포 (상위 10개, 결측 많은 순) --"
  psql_exec "SELECT path_l2, COUNT(*) total_active, COUNT(*) FILTER (WHERE NOT (metadata ? 'ancestor_ids')) missing_ancestor_ids FROM documents WHERE status='active' AND source_type='confluence_map' GROUP BY path_l2 ORDER BY missing_ancestor_ids DESC NULLS LAST LIMIT 10;" | column -s'|' -t
  echo ""
  echo "-- issue_frames: body_hash/extractor_version 결측 --"
  psql_exec "SELECT COUNT(*) total_frames, COUNT(*) FILTER (WHERE body_hash IS NULL) missing_body_hash FROM issue_frames;" | column -s'|' -t
  echo ""
  echo "실행하려면: $0 --apply {docs|map|map-pilot|frames|all} [-y]"
}

do_frames() {
  echo "[frames] app.frames.cli로 support_history + incident_reports 재추출 (규칙 기반, 외부 API 없음)"
  confirm "issue_frames 전체(17,632건 수준)를 재추출합니다. 진행할까요?" || { echo "취소"; return 0; }
  for st in support_history incident_reports; do
    echo "[frames] source_type=$st"
    compose exec -T "$SERVICE" python -m app.frames.cli --source-type "$st" -v
  done
  echo "[frames] 완료. app.ops.dashboard.coverage_gaps 또는 /v1/ops/dashboard로 missing_frame/body_hash 재확인하세요."
}

do_docs() {
  echo "[docs] confluence_docs/tech_repo 전체 재크롤 (Source.last_sync_at 리셋 → 증분 동기화 스크립트 재사용)"
  echo "  대상 Source: confluence_lookin_docs, confluence_techrepo"
  confirm "두 Source의 last_sync_at을 NULL로 되돌리고 전체(8,309페이지) 재크롤을 시작합니다. 라이브 Confluence에 부하가 갑니다. 진행할까요?" || { echo "취소"; return 0; }
  echo "[docs] last_sync_at 리셋"
  psql_exec "UPDATE sources SET last_sync_at = NULL WHERE id IN ('confluence_lookin_docs','confluence_techrepo');"
  echo "[docs] scripts/confluence_sync.sh 실행 (전체 소요 시간은 레이트리밋에 따라 다름 — 먼저 --max-pages로 소규모 확인 권장)"
  "${SCRIPT_DIR}/confluence_sync.sh" --sources confluence_docs,tech_repo
  echo "[docs] 완료. --dry-run 없이 바로 돌렸으므로 last_sync_at이 다시 정상적으로 전진했을 것 — 위 psql_exec SELECT로 확인하세요."
}

do_map_pilot() {
  echo "[map-pilot] confluence_map_si(가장 작은 공간, 2026-09-28 기준 670페이지/0.63시간) 1개만 --from-scratch로 재크롤"
  echo "  목적: 전체본문 전환(PR #4) 후 실제 임베딩 ms/건을 측정해 전체 77,446건 규모를 재추정하기 위함."
  confirm "confluence_map_si 공간 하나만 재백필합니다(포그라운드로 끝까지 대기). 진행할까요?" || { echo "취소"; return 0; }
  "${SCRIPT_DIR}/map_backfill.sh" --from-scratch --source-ids confluence_map_si --foreground
  echo "[map-pilot] 완료. data/raw/confluence_map/.backfill.log의 embed.passes[].elapsed_sec / embedded로 ms/건을 계산하세요."
  echo "  예: (elapsed_sec / embedded) * 1000 = ms/청크. 2026-09-28~30 실측(발췌 콘텐츠 기준)은 143.8ms/건이었음 — 전체본문 전환 후 이 값과 비교."
}

do_map() {
  echo "[map] confluence_map 전체 재크롤 — 기존 scripts/map_backfill.sh --from-scratch 재사용 (수 시간~수십 시간, 백그라운드)"
  echo "  권장: 먼저 --apply map-pilot으로 측정 후, 위 [2b] 공간별 결측 분포로 정말 남은 범위를 좁혀서 --source-ids로 실행하는 것을 고려."
  confirm "confluence_map 전체(77,446페이지)를 --from-scratch로 재백필합니다. 현재 레이트리밋 기준 최대 ~73시간 걸릴 수 있습니다. 진행할까요?" || { echo "취소"; return 0; }
  "${SCRIPT_DIR}/map_backfill.sh" --from-scratch
  echo "[map] 백그라운드로 시작됨 — 진행: data/raw/confluence_map/.backfill_state.json, 로그: data/raw/confluence_map/.backfill.log, 화면: admin.html"
}

case "$APPLY" in
  "")
    print_plan
    ;;
  apply)
    case "$TARGET" in
      frames) do_frames ;;
      map-pilot) do_map_pilot ;;
      docs) do_docs ;;
      map) do_map ;;
      all)
        do_frames
        do_docs
        do_map
        ;;
      *) echo "알 수 없는 대상: $TARGET (docs|map|map-pilot|frames|all)" >&2; exit 64 ;;
    esac
    ;;
esac
