#!/usr/bin/env bash
# sync_export.sh — 운영에서 실행. 개발 매니페스트 대비 신규/변경 행만 export.
set -euo pipefail

# sync_manifest.sh의 key_expr_for와 반드시 동일하게 유지 — diff .ids 파일의 값이
# 이 식으로 만든 키와 일치해야 join이 된다 (2026-10-07, knowledge-graph 백필용 추가).
key_expr_for() {
  case "$1" in
    document_entities) echo "document_id || ':' || entity_id" ;;
    lexicon_terms) echo "canonical" ;;
    *) echo "id" ;;
  esac
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEV_MANIFEST=""
OUT_DIR="${HOME}/tmp"
PROFILE="full"
TS="$(date '+%Y-%m-%d_%H%M%S')"

usage() {
  cat <<'EOF'
sync_export.sh — 운영: 개발 매니페스트 대비 신규/변경 행만 export

USAGE
  scripts/sync_export.sh --dev-manifest FILE [--project DIR] [--out DIR] [--profile full|graph]

옵션:
  --dev-manifest FILE   scripts/sync_manifest.sh 로 개발에서 생성해 반입한 파일 (필수)
  --project DIR         레포 루트 (기본: 이 스크립트 상위)
  --out DIR             출력 디렉터리 (기본: ~/tmp)
  --profile             --dev-manifest를 만들 때 쓴 것과 반드시 같아야 한다
                         (sync_manifest.sh --profile 참고). full(기본) 또는 graph.

DB 테이블은 CSV로, data/raw 변경 파일(profile=full일 때만)은 raw_files.tar.gz로 묶는다.
출력: citec-kb-incr-<TS>.tar.gz (변경분이 있을 때만 생성)
EOF
}

while [[ $# -gt 0 ]]; do
  case $1 in
    --dev-manifest) DEV_MANIFEST="${2:-}"; shift 2 ;;
    --project) PROJECT_DIR="${2:-}"; shift 2 ;;
    --out) OUT_DIR="${2:-}"; shift 2 ;;
    --profile) PROFILE="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "알 수 없는 옵션: $1" >&2; usage; exit 1 ;;
  esac
done

[[ -n "$DEV_MANIFEST" && -f "$DEV_MANIFEST" ]] || { echo "ERROR: --dev-manifest 파일 필요" >&2; exit 1; }
case "$PROFILE" in
  full|graph) ;;
  *) echo "ERROR: --profile 은 full 또는 graph" >&2; exit 1 ;;
esac

PG_USER="${POSTGRES_USER:-citec}"
PG_DB="${POSTGRES_DB:-citec_knowledge}"
if [[ "$PROFILE" == "graph" ]]; then
  TABLES=(documents checkitems issue_frames failure_buckets
          entities document_entities lexicon_terms)
else
  TABLES=(documents document_sections chunks checkitems issue_frames failure_buckets
          entities document_entities lexicon_terms)
fi

STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT

echo "[sync_export] 운영 매니페스트 생성 (profile=${PROFILE})" >&2
"${SCRIPT_DIR}/sync_manifest.sh" --project "$PROJECT_DIR" --profile "$PROFILE" --out "${STAGING}/ops-manifest.tsv.gz"

echo "[sync_export] diff 계산" >&2
python3 "${SCRIPT_DIR}/sync_diff.py" \
  --dev-manifest "$DEV_MANIFEST" \
  --ops-manifest "${STAGING}/ops-manifest.tsv.gz" \
  --out-dir "${STAGING}/diff" > "${STAGING}/diff_counts.json"
cat "${STAGING}/diff_counts.json" >&2

mkdir -p "${STAGING}/bundle"
HAS_ANY=false
for tbl in "${TABLES[@]}"; do
  ids_file="${STAGING}/diff/${tbl}.ids"
  [[ -f "$ids_file" ]] || continue
  HAS_ANY=true
  n=$(wc -l < "$ids_file")
  echo "[sync_export] ${tbl}: ${n}건 export" >&2
  key_expr="$(key_expr_for "$tbl")"
  {
    echo "CREATE TEMP TABLE _sync_ids (id text);"
    echo '\copy _sync_ids FROM STDIN WITH (FORMAT csv)'
    cat "$ids_file"
    echo '\.'
    echo "\\copy (SELECT tbl.* FROM ${tbl} tbl JOIN _sync_ids s ON (${key_expr}) = s.id) TO STDOUT WITH (FORMAT csv, HEADER)"
  } | docker compose -f "${PROJECT_DIR}/docker-compose.yml" exec -T postgres \
        psql -q -v ON_ERROR_STOP=1 -U "$PG_USER" -d "$PG_DB" \
        > "${STAGING}/bundle/${tbl}.csv"
done

RAW_IDS_FILE="${STAGING}/diff/raw_files.ids"
if [[ "$PROFILE" == "graph" ]]; then
  echo "[sync_export] --profile graph — raw_files 건너뜀" >&2
elif [[ -f "$RAW_IDS_FILE" ]]; then
  HAS_ANY=true
  n=$(wc -l < "$RAW_IDS_FILE")
  echo "[sync_export] raw_files: ${n}건 tar" >&2
  tar czf "${STAGING}/bundle/raw_files.tar.gz" -C "${PROJECT_DIR}/data/raw" -T "$RAW_IDS_FILE"
fi

if ! $HAS_ANY; then
  echo "[sync_export] 변경분 없음 — 번들 생성 안 함" >&2
  exit 0
fi

{
  echo "# citec-kb incremental sync export"
  echo "created=$(date -Iseconds)"
  echo "host=$(hostname)"
  echo "dev_manifest=$(basename "$DEV_MANIFEST")"
  cat "${STAGING}/diff_counts.json"
} > "${STAGING}/bundle/manifest.txt"

mkdir -p "$OUT_DIR"
OUT_TGZ="${OUT_DIR}/citec-kb-incr-${TS}.tar.gz"
(cd "$STAGING" && tar czf "$OUT_TGZ" bundle/)
echo "[sync_export] 완료: ${OUT_TGZ}" >&2
