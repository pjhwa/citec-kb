#!/usr/bin/env bash
# graph_prereq_survey.sh — 운영에서 실행, 읽기 전용. knowledge-graph 백필
# (docs/superpowers/specs/2026-10-07-knowledge-graph-design.md)을 위해 운영→개발
# 데이터 반입(sync_manifest.sh/sync_export.sh/sync_apply.sh, USB 등 승인된 경로로
# 운반)을 하기 전에, 운영 쪽 스키마/데이터 상태가 그 스크립트들의 전제와 맞는지
# 먼저 확인한다. DB에 아무것도 쓰지 않는다 — SELECT만 실행.
#
# USAGE
#   scripts/graph_prereq_survey.sh [--out FILE]
#
# 출력: 사람이 읽을 텍스트 리포트 (기본 ~/tmp/citec-kb-graph-prereq-<TS>.txt).
# 이 출력을 그대로 공유하면 dev 쪽에서 sync 스크립트가 쓸 수 있는 상태인지 판단한다.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUT_FILE=""
TS="$(date '+%Y-%m-%d_%H%M%S')"

while [[ $# -gt 0 ]]; do
  case $1 in
    --out) OUT_FILE="${2:-}"; shift 2 ;;
    -h|--help)
      sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "알 수 없는 옵션: $1" >&2; exit 1 ;;
  esac
done

OUT_FILE="${OUT_FILE:-${HOME}/tmp/citec-kb-graph-prereq-${TS}.txt}"
mkdir -p "$(dirname "$OUT_FILE")"

PG_USER="${POSTGRES_USER:-citec}"
PG_DB="${POSTGRES_DB:-citec_knowledge}"

# knowledge-graph sync가 쓰는 테이블(entities/document_entities/lexicon_terms)과
# failure_buckets.fb_domain/evidence_ref는 각각 다음 리비전에서 추가됐다 —
# 이보다 낮으면 sync_apply.sh가 없는 컬럼을 참조해 실패한다.
REQUIRED_MIN_REVISION="20260807_0005"
DEV_HEAD_REVISION="20260930_0009"   # 이 레포 dev의 현재 alembic head (apps/api/alembic/versions)

if ! docker compose -f "${PROJECT_DIR}/docker-compose.yml" ps --status running --services 2>/dev/null \
      | grep -qx postgres; then
  echo "중단: postgres 컨테이너가 실행 중이 아닙니다." >&2
  exit 1
fi

psql_q() {
  docker compose -f "${PROJECT_DIR}/docker-compose.yml" exec -T postgres \
    psql -q -v ON_ERROR_STOP=1 -U "$PG_USER" -d "$PG_DB" -At -c "$1"
}

{
  echo "# citec-kb knowledge-graph 백필 사전조사 (운영)"
  echo "생성: $(date -Iseconds)  host=$(hostname)"
  echo

  echo "## 1. 스키마 버전"
  rev="$(psql_q "SELECT version_num FROM alembic_version" || echo "조회실패")"
  echo "alembic_version: ${rev}"
  if [[ "$rev" < "$REQUIRED_MIN_REVISION" ]]; then
    echo "  ⚠ ${REQUIRED_MIN_REVISION} 미만 — fb_domain/evidence_ref 컬럼이 없을 수 있음. sync_apply.sh 적용 전 운영 마이그레이션 먼저 필요."
  fi
  if [[ "$rev" < "$DEV_HEAD_REVISION" ]]; then
    echo "  참고: dev head(${DEV_HEAD_REVISION})보다 낮음 — dev가 운영보다 앞선 상태, sync로 받는 데이터는 이 리비전 기준."
  fi
  echo

  echo "## 2. 테이블별 건수 (sync 대상 9개 + 참고)"
  psql_q "
    SELECT 'documents='||count(*) FROM documents
    UNION ALL SELECT 'document_sections='||count(*) FROM document_sections
    UNION ALL SELECT 'chunks='||count(*) FROM chunks
    UNION ALL SELECT 'checkitems='||count(*) FROM checkitems
    UNION ALL SELECT 'issue_frames='||count(*) FROM issue_frames
    UNION ALL SELECT 'failure_buckets='||count(*) FROM failure_buckets
    UNION ALL SELECT 'entities='||count(*) FROM entities
    UNION ALL SELECT 'document_entities='||count(*) FROM document_entities
    UNION ALL SELECT 'lexicon_terms='||count(*) FROM lexicon_terms;
  "
  echo

  echo "## 3. documents: source_type별 건수 + 본문/ancestor_ids 충전율"
  psql_q "
    SELECT source_type || '  total=' || count(*)
        || '  empty_body=' || count(*) FILTER (WHERE body_md IS NULL OR body_md = '')
        || '  has_ancestor_ids=' || count(*) FILTER (WHERE metadata ? 'ancestor_ids')
        || '  has_space_key=' || count(*) FILTER (WHERE metadata ? 'space_key')
    FROM documents GROUP BY source_type ORDER BY count(*) DESC;
  "
  echo

  echo "## 4. confluence_map: space_key 분포 (LOOKIN/TechRepo가 1순위 CI-TEC 산출물, §0.1)"
  psql_q "
    SELECT coalesce(metadata->>'space_key','(없음)') || '  ' || count(*)
    FROM documents WHERE source_type='confluence_map'
    GROUP BY metadata->>'space_key' ORDER BY count(*) DESC;
  "
  echo

  echo "## 5. issue_frames: 필드별 충전율 (dev는 전체가 낮았음 — 운영도 같은지 확인)"
  psql_q "
    SELECT 'total='||count(*)
      ||'  symptom='||count(*) FILTER (WHERE symptom IS NOT NULL AND symptom<>'')
      ||'  root_cause='||count(*) FILTER (WHERE root_cause IS NOT NULL AND root_cause<>'')
      ||'  components='||count(*) FILTER (WHERE cardinality(components)>0)
      ||'  environment='||count(*) FILTER (WHERE environment IS NOT NULL)
      ||'  citec_domains='||count(*) FILTER (WHERE cardinality(citec_domains)>0)
      ||'  body_hash='||count(*) FILTER (WHERE body_hash IS NOT NULL)
      ||'  extractor_version='||count(*) FILTER (WHERE extractor_version IS NOT NULL)
    FROM issue_frames;
  "
  echo

  echo "## 6. failure_buckets: fb_domain/evidence_ref NULL 여부 (0이어야 정상 — NULL 있으면 마이그레이션 문제)"
  psql_q "
    SELECT 'total='||count(*)
      ||'  null_fb_domain='||count(*) FILTER (WHERE fb_domain IS NULL)
      ||'  null_evidence_ref='||count(*) FILTER (WHERE evidence_ref IS NULL)
      ||'  null_environment='||count(*) FILTER (WHERE environment IS NULL)
    FROM failure_buckets;
  "
  echo

  echo "## 7. lexicon_terms / entities 현황 (dev는 각각 10건/5건으로 극히 적었음)"
  psql_q "SELECT canonical FROM lexicon_terms ORDER BY canonical;"
  echo "---"
  psql_q "SELECT id||' ('||type||')' FROM entities ORDER BY id;"
  echo

  echo "## 8. 대략적 전송 용량 추정 (sync 대상 9테이블, pg_total_relation_size는 인덱스 포함이라 과대추정 — 참고용)"
  psql_q "
    SELECT relname || '  ' || pg_size_pretty(pg_total_relation_size(relid))
    FROM pg_catalog.pg_statio_user_tables
    WHERE relname IN ('documents','document_sections','chunks','checkitems',
                       'issue_frames','failure_buckets','entities',
                       'document_entities','lexicon_terms')
    ORDER BY pg_total_relation_size(relid) DESC;
  "
} | tee "$OUT_FILE"

echo
echo "[graph_prereq_survey] 완료 — 저장: ${OUT_FILE}" >&2
echo "[graph_prereq_survey] 이 파일 내용을 그대로 전달해 주세요 (USB 등 승인된 경로)." >&2
