#!/usr/bin/env bash
# sync_apply.sh — 개발에서 실행. incr 번들을 단일 트랜잭션으로 적용 후 재임베딩 트리거.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BUNDLE=""
NO_REEMBED=false

usage() {
  cat <<'EOF'
sync_apply.sh — 개발: incr 번들 적용 (DB 단일 트랜잭션 + raw 파일 반영) + 재임베딩

USAGE
  scripts/sync_apply.sh BUNDLE.tar.gz [--project DIR] [--no-reembed]

옵션:
  --project DIR    레포 루트 (기본: 이 스크립트 상위)
  --no-reembed     적용 후 app.embed.cli 자동 실행 생략

DB 6테이블은 단일 트랜잭션으로, data/raw 변경 파일(raw_files.tar.gz가 있으면)은 별도로
data/raw 밑에 풀어 반영한다 (파일 복사는 멱등이라 트랜잭션 롤백 대상이 아님).
EOF
}

while [[ $# -gt 0 ]]; do
  case $1 in
    --project) PROJECT_DIR="${2:-}"; shift 2 ;;
    --no-reembed) NO_REEMBED=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) BUNDLE="$1"; shift ;;
  esac
done

[[ -n "$BUNDLE" && -f "$BUNDLE" ]] || { echo "ERROR: BUNDLE.tar.gz 인자 필요" >&2; usage; exit 1; }

PG_USER="${POSTGRES_USER:-citec}"
PG_DB="${POSTGRES_DB:-citec_knowledge}"

STAGING="$(mktemp -d)"
SQL_SCRIPT="$(mktemp)"
trap 'rm -rf "$STAGING"; rm -f "$SQL_SCRIPT"' EXIT

tar xzf "$BUNDLE" -C "$STAGING"
BUNDLE_DIR="${STAGING}/bundle"
[[ -d "$BUNDLE_DIR" ]] || { echo "ERROR: 번들 구조 이상 (bundle/ 없음)" >&2; exit 1; }

count_query="SELECT 'documents='||count(*) FROM documents
   UNION ALL SELECT 'chunks='||count(*) FROM chunks WHERE is_active
   UNION ALL SELECT 'checkitems='||count(*) FROM checkitems
   UNION ALL SELECT 'issue_frames='||count(*) FROM issue_frames
   UNION ALL SELECT 'failure_buckets='||count(*) FROM failure_buckets;"

echo "[sync_apply] 적용 전 건수" >&2
docker compose -f "${PROJECT_DIR}/docker-compose.yml" exec -T postgres \
  psql -U "$PG_USER" -d "$PG_DB" -t -A -c "$count_query" >&2

{
  if [[ -f "${BUNDLE_DIR}/documents.csv" ]]; then
    cat <<'SQL'
CREATE TEMP TABLE stg_documents (LIKE documents INCLUDING DEFAULTS);
SQL
    echo '\copy stg_documents FROM STDIN WITH (FORMAT csv, HEADER)'
    cat "${BUNDLE_DIR}/documents.csv"
    echo '\.'
    cat <<'SQL'
-- 이 정리(cleanup)는 documents.csv가 있을 때만 실행된다. 안전한 이유: 이 레포의 유일한
-- chunks/document_sections 쓰기 경로는 apps/api/app/ingest/pipeline.py의
-- _upsert_document 뿐이고, 거기서는 documents.content_hash가 바뀔 때만 섹션/청크를
-- 재생성한다 — 즉 documents 행이 안 바뀌었는데 sections/chunks만 바뀌는 경우는 현재
-- 코드베이스에 존재하지 않는다. 이 가정이 깨지면(예: 문서 내용과 무관하게 재청킹하는 새
-- 경로가 생기면) 아래 정리도 document_sections.csv/chunks.csv 단독 존재 시로 넓혀야 한다.
UPDATE chunks SET is_active = false
  WHERE document_id IN (SELECT id FROM stg_documents)
    AND document_id IN (SELECT id FROM documents);
DELETE FROM document_sections
  WHERE document_id IN (SELECT id FROM stg_documents)
    AND document_id IN (SELECT id FROM documents);
INSERT INTO documents (
  id, source_id, source_type, external_id, title, body_md, metadata,
  content_hash, version, status, source_uri, lang, evidence_grade,
  environment, domain, work_type, path_l2, path_l3, ingested_at,
  created_at, updated_at
)
SELECT
  id, source_id, source_type, external_id, title, body_md, metadata,
  content_hash, version, status, source_uri, lang, evidence_grade,
  environment, domain, work_type, path_l2, path_l3, ingested_at,
  created_at, updated_at
FROM stg_documents
ON CONFLICT (id) DO UPDATE SET
  source_id = EXCLUDED.source_id,
  source_type = EXCLUDED.source_type,
  external_id = EXCLUDED.external_id,
  title = EXCLUDED.title,
  body_md = EXCLUDED.body_md,
  metadata = EXCLUDED.metadata,
  content_hash = EXCLUDED.content_hash,
  version = EXCLUDED.version,
  status = EXCLUDED.status,
  source_uri = EXCLUDED.source_uri,
  lang = EXCLUDED.lang,
  evidence_grade = EXCLUDED.evidence_grade,
  environment = EXCLUDED.environment,
  domain = EXCLUDED.domain,
  work_type = EXCLUDED.work_type,
  path_l2 = EXCLUDED.path_l2,
  path_l3 = EXCLUDED.path_l3,
  ingested_at = EXCLUDED.ingested_at,
  updated_at = EXCLUDED.updated_at;
SQL
  fi

  if [[ -f "${BUNDLE_DIR}/document_sections.csv" ]]; then
    cat <<'SQL'
CREATE TEMP TABLE stg_document_sections (LIKE document_sections INCLUDING DEFAULTS);
SQL
    echo '\copy stg_document_sections FROM STDIN WITH (FORMAT csv, HEADER)'
    cat "${BUNDLE_DIR}/document_sections.csv"
    echo '\.'
    cat <<'SQL'
INSERT INTO document_sections (id, document_id, heading_path, level, body_md, token_count, ordinal)
SELECT id, document_id, heading_path, level, body_md, token_count, ordinal
FROM stg_document_sections
ON CONFLICT (id) DO UPDATE SET
  document_id = EXCLUDED.document_id,
  heading_path = EXCLUDED.heading_path,
  level = EXCLUDED.level,
  body_md = EXCLUDED.body_md,
  token_count = EXCLUDED.token_count,
  ordinal = EXCLUDED.ordinal;
SQL
  fi

  if [[ -f "${BUNDLE_DIR}/chunks.csv" ]]; then
    cat <<'SQL'
CREATE TEMP TABLE stg_chunks (LIKE chunks INCLUDING DEFAULTS);
SQL
    echo '\copy stg_chunks FROM STDIN WITH (FORMAT csv, HEADER)'
    cat "${BUNDLE_DIR}/chunks.csv"
    echo '\.'
    cat <<'SQL'
INSERT INTO chunks (id, document_id, section_id, ordinal, text, header_context, token_count, tsv, is_active, created_at)
SELECT id, document_id, section_id, ordinal, text, header_context, token_count, tsv, is_active, created_at
FROM stg_chunks
ON CONFLICT (id) DO UPDATE SET
  document_id = EXCLUDED.document_id,
  section_id = EXCLUDED.section_id,
  ordinal = EXCLUDED.ordinal,
  text = EXCLUDED.text,
  header_context = EXCLUDED.header_context,
  token_count = EXCLUDED.token_count,
  tsv = EXCLUDED.tsv,
  is_active = EXCLUDED.is_active,
  created_at = EXCLUDED.created_at;
SQL
  fi

  if [[ -f "${BUNDLE_DIR}/checkitems.csv" ]]; then
    cat <<'SQL'
CREATE TEMP TABLE stg_checkitems (LIKE checkitems INCLUDING DEFAULTS);
SQL
    echo '\copy stg_checkitems FROM STDIN WITH (FORMAT csv, HEADER)'
    cat "${BUNDLE_DIR}/checkitems.csv"
    echo '\.'
    cat <<'SQL'
INSERT INTO checkitems (
  id, code, lang, area, category, category_1, subcategory, subject,
  check_method, check_criteria, check_result, risk_if_vulnerable,
  remediation, raw, tsv, document_id, created_at
)
SELECT
  id, code, lang, area, category, category_1, subcategory, subject,
  check_method, check_criteria, check_result, risk_if_vulnerable,
  remediation, raw, tsv, document_id, created_at
FROM stg_checkitems
ON CONFLICT (id) DO UPDATE SET
  code = EXCLUDED.code,
  lang = EXCLUDED.lang,
  area = EXCLUDED.area,
  category = EXCLUDED.category,
  category_1 = EXCLUDED.category_1,
  subcategory = EXCLUDED.subcategory,
  subject = EXCLUDED.subject,
  check_method = EXCLUDED.check_method,
  check_criteria = EXCLUDED.check_criteria,
  check_result = EXCLUDED.check_result,
  risk_if_vulnerable = EXCLUDED.risk_if_vulnerable,
  remediation = EXCLUDED.remediation,
  raw = EXCLUDED.raw,
  tsv = EXCLUDED.tsv,
  document_id = EXCLUDED.document_id;
SQL
  fi

  if [[ -f "${BUNDLE_DIR}/issue_frames.csv" ]]; then
    cat <<'SQL'
CREATE TEMP TABLE stg_issue_frames (LIKE issue_frames INCLUDING DEFAULTS);
SQL
    echo '\copy stg_issue_frames FROM STDIN WITH (FORMAT csv, HEADER)'
    cat "${BUNDLE_DIR}/issue_frames.csv"
    echo '\.'
    cat <<'SQL'
INSERT INTO issue_frames (
  id, document_id, symptom, root_cause, resolution, workaround,
  components, environment, commands, quality, raw_extract,
  created_at, updated_at
)
SELECT
  id, document_id, symptom, root_cause, resolution, workaround,
  components, environment, commands, quality, raw_extract,
  created_at, updated_at
FROM stg_issue_frames
ON CONFLICT (id) DO UPDATE SET
  document_id = EXCLUDED.document_id,
  symptom = EXCLUDED.symptom,
  root_cause = EXCLUDED.root_cause,
  resolution = EXCLUDED.resolution,
  workaround = EXCLUDED.workaround,
  components = EXCLUDED.components,
  environment = EXCLUDED.environment,
  commands = EXCLUDED.commands,
  quality = EXCLUDED.quality,
  raw_extract = EXCLUDED.raw_extract,
  updated_at = EXCLUDED.updated_at;
SQL
  fi

  if [[ -f "${BUNDLE_DIR}/failure_buckets.csv" ]]; then
    cat <<'SQL'
CREATE TEMP TABLE stg_failure_buckets (LIKE failure_buckets INCLUDING DEFAULTS);
SQL
    echo '\copy stg_failure_buckets FROM STDIN WITH (FORMAT csv, HEADER)'
    cat "${BUNDLE_DIR}/failure_buckets.csv"
    echo '\.'
    cat <<'SQL'
INSERT INTO failure_buckets (
  id, document_id, bucket_name, protocol, symptom,
  discriminating_signals, counter_signals, root_cause, recommended_action,
  confidence, support_count, counter_count, evidence_grade, created_by,
  created_at, updated_at
)
SELECT
  id, document_id, bucket_name, protocol, symptom,
  discriminating_signals, counter_signals, root_cause, recommended_action,
  confidence, support_count, counter_count, evidence_grade, created_by,
  created_at, updated_at
FROM stg_failure_buckets
ON CONFLICT (id) DO UPDATE SET
  document_id = EXCLUDED.document_id,
  bucket_name = EXCLUDED.bucket_name,
  protocol = EXCLUDED.protocol,
  symptom = EXCLUDED.symptom,
  discriminating_signals = EXCLUDED.discriminating_signals,
  counter_signals = EXCLUDED.counter_signals,
  root_cause = EXCLUDED.root_cause,
  recommended_action = EXCLUDED.recommended_action,
  confidence = EXCLUDED.confidence,
  support_count = EXCLUDED.support_count,
  counter_count = EXCLUDED.counter_count,
  evidence_grade = EXCLUDED.evidence_grade,
  created_by = EXCLUDED.created_by,
  updated_at = EXCLUDED.updated_at;
SQL
  fi
} > "$SQL_SCRIPT"

echo "[sync_apply] 트랜잭션 적용 시작 (단일 트랜잭션, 실패 시 전체 롤백)" >&2
docker compose -f "${PROJECT_DIR}/docker-compose.yml" exec -T postgres \
  psql -q -1 -v ON_ERROR_STOP=1 -U "$PG_USER" -d "$PG_DB" < "$SQL_SCRIPT"
echo "[sync_apply] 트랜잭션 적용 완료" >&2

echo "[sync_apply] 적용 후 건수" >&2
docker compose -f "${PROJECT_DIR}/docker-compose.yml" exec -T postgres \
  psql -U "$PG_USER" -d "$PG_DB" -t -A -c "$count_query" >&2

if [[ -f "${BUNDLE_DIR}/raw_files.tar.gz" ]]; then
  echo "[sync_apply] raw_files 반영" >&2
  mkdir -p "${PROJECT_DIR}/data/raw"
  tar xzf "${BUNDLE_DIR}/raw_files.tar.gz" -C "${PROJECT_DIR}/data/raw"
  n=$(tar tzf "${BUNDLE_DIR}/raw_files.tar.gz" | grep -c -v '/$' || true)
  echo "[sync_apply] raw_files 반영 완료 (${n}건)" >&2
fi

if ! $NO_REEMBED; then
  echo "[sync_apply] 재임베딩 트리거 (app.embed.cli)" >&2
  docker compose -f "${PROJECT_DIR}/docker-compose.yml" exec -T api \
    python -m app.embed.cli
fi

echo "[sync_apply] 완료" >&2
