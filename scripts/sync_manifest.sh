#!/usr/bin/env bash
# sync_manifest.sh — 9개 테이블의 (table, key, md5hash) 매니페스트를 gzip TSV로 생성.
# 개발/운영 양쪽에서 동일하게 실행 (incremental sync의 diff 계산 전 단계).
#
# knowledge-graph 백필(docs/superpowers/specs/2026-10-07-knowledge-graph-design.md)에
# 필요한 entities/document_entities/lexicon_terms 3개를 추가했다(2026-10-07).
# document_entities/lexicon_terms는 PK가 autoincrement 정수라 운영/개발 간 값이
# 안정적인 식별자가 아니다 — 환경 간 diff 키로는 자연키(natural key)를 쓴다
# (아래 key_expr_for 참고). 그 외 테이블은 전부 uuid 문자열 PK라 id를 그대로 쓴다.
set -euo pipefail

# 테이블별 diff 키 표현식. documents.id 류(uuid 문자열 PK)는 그대로 id를 쓰고,
# autoincrement 정수 PK 테이블만 자연키로 바꾼다 — id 값 자체는 환경마다 달라
# diff 키로 못 쓰기 때문(같은 행이라도 매번 "신규"로 잡혀 diff가 무의미해짐).
key_expr_for() {
  case "$1" in
    document_entities) echo "document_id || ':' || entity_id" ;;
    lexicon_terms) echo "canonical" ;;
    *) echo "id" ;;
  esac
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUT_FILE=""
PROFILE="full"
TS="$(date '+%Y-%m-%d_%H%M%S')"

usage() {
  cat <<'EOF'
sync_manifest.sh — 9개 테이블 + data/raw 첨부파일(raw_files)의 (table, id, hash)
매니페스트를 gzip TSV로 생성한다.

USAGE
  scripts/sync_manifest.sh [--out FILE] [--project DIR] [--profile full|graph]

옵션:
  --out FILE       출력 경로 (기본: ~/tmp/citec-kb-manifest-<TS>.tsv.gz)
  --project DIR    레포 루트 (기본: 이 스크립트 상위)
  --profile        full(기본): 9테이블+raw_files 전체 — dev 검색/벡터도 운영과 맞춤.
                    graph: chunks/document_sections/raw_files 제외, knowledge-graph
                    백필에 필요한 7테이블만 — 전송량이 약 1/4로 줄어든다
                    (2026-10-07 운영 조사 기준 전체 ~2.8GB → graph ~700MB).
                    sync_export.sh에도 --profile 로 동일하게 넘겨야 한다
                    (dev 매니페스트와 운영 export의 테이블 집합이 일치해야 diff가 맞음).
EOF
}

while [[ $# -gt 0 ]]; do
  case $1 in
    --out) OUT_FILE="${2:-}"; shift 2 ;;
    --project) PROJECT_DIR="${2:-}"; shift 2 ;;
    --profile) PROFILE="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "알 수 없는 옵션: $1" >&2; usage; exit 1 ;;
  esac
done

case "$PROFILE" in
  full|graph) ;;
  *) echo "ERROR: --profile 은 full 또는 graph" >&2; exit 1 ;;
esac

OUT_FILE="${OUT_FILE:-${HOME}/tmp/citec-kb-manifest-${TS}.tsv.gz}"
mkdir -p "$(dirname "$OUT_FILE")"

PG_USER="${POSTGRES_USER:-citec}"
PG_DB="${POSTGRES_DB:-citec_knowledge}"

if [[ "$PROFILE" == "graph" ]]; then
  TABLES=(documents checkitems issue_frames failure_buckets
          entities document_entities lexicon_terms)
else
  TABLES=(documents document_sections chunks checkitems issue_frames failure_buckets
          entities document_entities lexicon_terms)
fi

echo "[sync_manifest] project=${PROJECT_DIR} out=${OUT_FILE}" >&2

TMP_RAW="$(mktemp)"
trap 'rm -f "$TMP_RAW"' EXIT

for tbl in "${TABLES[@]}"; do
  echo "[sync_manifest] ${tbl}" >&2
  key_expr="$(key_expr_for "$tbl")"
  docker compose -f "${PROJECT_DIR}/docker-compose.yml" exec -T postgres \
    psql -q -v ON_ERROR_STOP=1 -U "$PG_USER" -d "$PG_DB" -At -F $'\t' \
    -c "SELECT '${tbl}', ${key_expr}, md5(t::text) FROM ${tbl} t" >> "$TMP_RAW"
done

RAW_DIR="${PROJECT_DIR}/data/raw"
if [[ "$PROFILE" == "graph" ]]; then
  echo "[sync_manifest] --profile graph — raw_files 건너뜀" >&2
elif [[ -d "$RAW_DIR" ]]; then
  echo "[sync_manifest] raw_files" >&2
  # 파일명에 공백이 있어도 안전하도록 NUL 구분 + 파일당 sha256sum 개별 호출
  # (sha256sum 배치 출력 "<hash>  <path>"를 공백 기준으로 재파싱하면 공백 포함 경로에서 깨짐)
  while IFS= read -r -d '' relpath; do
    hash="$(sha256sum "${RAW_DIR}/${relpath}" | cut -d' ' -f1)"
    printf 'raw_files\t%s\t%s\n' "$relpath" "$hash"
  done < <(cd "$RAW_DIR" && find . -type f -printf '%P\0') >> "$TMP_RAW"
else
  echo "[sync_manifest] data/raw 없음 — raw_files 건너뜀" >&2
fi

gzip -c "$TMP_RAW" > "$OUT_FILE"
n=$(wc -l < "$TMP_RAW")
echo "[sync_manifest] 완료: ${OUT_FILE} (${n} rows)" >&2
