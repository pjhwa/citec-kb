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
    if [[ -f "${BUNDLE_DIR}/chunks.csv" ]]; then
      cat <<'SQL'
-- 이 정리(cleanup)는 documents.csv와 chunks.csv가 둘 다 있을 때만 실행한다 — 즉
-- profile=full일 때만. 안전한 이유: 이 레포의 유일한 chunks/document_sections 쓰기
-- 경로는 apps/api/app/ingest/pipeline.py의 _upsert_document 뿐이고, 거기서는
-- documents.content_hash가 바뀔 때만 섹션/청크를 재생성한다. app.embed.cli(재임베딩
-- 트리거)는 "active 상태인데 임베딩 없는 chunk"만 임베딩할 뿐, 없어진 chunk를 다시
-- 만들어주지 않는다 — 그래서 이 블록으로 비활성화한 chunk는 같은 번들의 chunks.csv로
-- 바로 대체되는 게 전제다. profile=graph는 chunks.csv를 안 담으므로 여기서 건드리면
-- 대체될 데이터 없이 영구히 chunks가 사라진다(2026-10-07 운영 반입 1차 테스트에서
-- 실제로 겪은 사고 — dev의 active chunk가 178,310→0이 됐었다. embeddings 테이블의
-- chunk_id로 복구함). profile=graph일 때는 이 cleanup을 건너뛰고 기존 chunks/sections를
-- (body_md가 갱신돼도) 그대로 둔다 — 최신화는 못 되지만 최소한 검색은 계속 된다.
UPDATE chunks SET is_active = false
  WHERE document_id IN (SELECT id FROM stg_documents)
    AND document_id IN (SELECT id FROM documents);
DELETE FROM document_sections
  WHERE document_id IN (SELECT id FROM stg_documents)
    AND document_id IN (SELECT id FROM documents);
SQL
    fi
    cat <<'SQL'
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
  id, document_id, bucket_name, fb_domain, protocol, environment, evidence_ref, symptom,
  discriminating_signals, counter_signals, root_cause, recommended_action,
  confidence, support_count, counter_count, evidence_grade, created_by,
  created_at, updated_at
)
SELECT
  id, document_id, bucket_name, fb_domain, protocol, environment, evidence_ref, symptom,
  discriminating_signals, counter_signals, root_cause, recommended_action,
  confidence, support_count, counter_count, evidence_grade, created_by,
  created_at, updated_at
FROM stg_failure_buckets
ON CONFLICT (id) DO UPDATE SET
  document_id = EXCLUDED.document_id,
  bucket_name = EXCLUDED.bucket_name,
  fb_domain = EXCLUDED.fb_domain,
  protocol = EXCLUDED.protocol,
  environment = EXCLUDED.environment,
  evidence_ref = EXCLUDED.evidence_ref,
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

  # 2026-10-07 knowledge-graph 백필용 추가 3테이블. document_entities/lexicon_terms는
  # autoincrement 정수 PK라(§sync_manifest.sh 상단 주석) id를 그대로 들여오면 dev 자체
  # 시퀀스와 충돌할 수 있어, id는 들여오지 않고 자연키로 upsert한다 — dev가 새 id를
  # 스스로 채번한다.
  if [[ -f "${BUNDLE_DIR}/entities.csv" ]]; then
    cat <<'SQL'
CREATE TEMP TABLE stg_entities (LIKE entities INCLUDING DEFAULTS);
SQL
    echo '\copy stg_entities FROM STDIN WITH (FORMAT csv, HEADER)'
    cat "${BUNDLE_DIR}/entities.csv"
    echo '\.'
    cat <<'SQL'
INSERT INTO entities (id, canonical_name, type, customer, aliases, host_patterns, env_hints, metadata, created_at)
SELECT id, canonical_name, type, customer, aliases, host_patterns, env_hints, metadata, created_at
FROM stg_entities
ON CONFLICT (id) DO UPDATE SET
  canonical_name = EXCLUDED.canonical_name,
  type = EXCLUDED.type,
  customer = EXCLUDED.customer,
  aliases = EXCLUDED.aliases,
  host_patterns = EXCLUDED.host_patterns,
  env_hints = EXCLUDED.env_hints,
  metadata = EXCLUDED.metadata;
SQL
  fi

  if [[ -f "${BUNDLE_DIR}/document_entities.csv" ]]; then
    cat <<'SQL'
CREATE TEMP TABLE stg_document_entities (LIKE document_entities INCLUDING DEFAULTS);
SQL
    echo '\copy stg_document_entities FROM STDIN WITH (FORMAT csv, HEADER)'
    cat "${BUNDLE_DIR}/document_entities.csv"
    echo '\.'
    cat <<'SQL'
INSERT INTO document_entities (document_id, entity_id, confidence)
SELECT document_id, entity_id, confidence
FROM stg_document_entities
ON CONFLICT (document_id, entity_id) DO UPDATE SET
  confidence = EXCLUDED.confidence;
SQL
  fi

  if [[ -f "${BUNDLE_DIR}/lexicon_terms.csv" ]]; then
    cat <<'SQL'
CREATE TEMP TABLE stg_lexicon_terms (LIKE lexicon_terms INCLUDING DEFAULTS);
SQL
    echo '\copy stg_lexicon_terms FROM STDIN WITH (FORMAT csv, HEADER)'
    cat "${BUNDLE_DIR}/lexicon_terms.csv"
    echo '\.'
    cat <<'SQL'
INSERT INTO lexicon_terms (canonical, variants, priority, metadata)
SELECT canonical, variants, priority, metadata
FROM stg_lexicon_terms
ON CONFLICT (canonical) DO UPDATE SET
  variants = EXCLUDED.variants,
  priority = EXCLUDED.priority,
  metadata = EXCLUDED.metadata;
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
