#!/usr/bin/env bash
# sync_manifest.sh — 6개 테이블의 (table, id, md5hash) 매니페스트를 gzip TSV로 생성.
# 개발/운영 양쪽에서 동일하게 실행 (incremental sync의 diff 계산 전 단계).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUT_FILE=""
TS="$(date '+%Y-%m-%d_%H%M%S')"

usage() {
  cat <<'EOF'
sync_manifest.sh — documents/document_sections/chunks/checkitems/issue_frames/failure_buckets
6개 테이블 + data/raw 첨부파일(raw_files)의 (table, id, hash) 매니페스트를 gzip TSV로 생성한다.

USAGE
  scripts/sync_manifest.sh [--out FILE] [--project DIR]

옵션:
  --out FILE       출력 경로 (기본: ~/tmp/citec-kb-manifest-<TS>.tsv.gz)
  --project DIR    레포 루트 (기본: 이 스크립트 상위)
EOF
}

while [[ $# -gt 0 ]]; do
  case $1 in
    --out) OUT_FILE="${2:-}"; shift 2 ;;
    --project) PROJECT_DIR="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "알 수 없는 옵션: $1" >&2; usage; exit 1 ;;
  esac
done

OUT_FILE="${OUT_FILE:-${HOME}/tmp/citec-kb-manifest-${TS}.tsv.gz}"
mkdir -p "$(dirname "$OUT_FILE")"

PG_USER="${POSTGRES_USER:-citec}"
PG_DB="${POSTGRES_DB:-citec_knowledge}"

TABLES=(documents document_sections chunks checkitems issue_frames failure_buckets)

echo "[sync_manifest] project=${PROJECT_DIR} out=${OUT_FILE}" >&2

TMP_RAW="$(mktemp)"
trap 'rm -f "$TMP_RAW"' EXIT

for tbl in "${TABLES[@]}"; do
  echo "[sync_manifest] ${tbl}" >&2
  docker compose -f "${PROJECT_DIR}/docker-compose.yml" exec -T postgres \
    psql -q -v ON_ERROR_STOP=1 -U "$PG_USER" -d "$PG_DB" -At -F $'\t' \
    -c "SELECT '${tbl}', id, md5(t::text) FROM ${tbl} t" >> "$TMP_RAW"
done

RAW_DIR="${PROJECT_DIR}/data/raw"
if [[ -d "$RAW_DIR" ]]; then
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
